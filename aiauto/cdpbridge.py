"""The Chrome extension as a browser for the server's own engine.

The extension attaches Chrome's debugger to the tab the user is on and relays DevTools-protocol messages over a WebSocket to the
hub. The hub presents that as a DevTools endpoint (`/cdp/<token>`), so Playwright (the server's Playwright engine, with the
whole Runner on top of it) drives the user's real tab exactly as it drives a browser it launched itself: same element finding,
verification, self-healing, AI paths, uploads, assertions, reports. The extension carries no engine of its own.

Browser-level commands Playwright sends (Browser.*, Target.*) have no equivalent for a single tab, so they are answered here;
everything addressed to a page (or to a frame / worker session under it) is forwarded to the extension unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any

log = logging.getLogger("aiauto.cdpbridge")
PRODUCT = "Chrome/153.0.0.0"
import os
DEBUG = bool(os.getenv("AIAUTO_CDP_DEBUG"))
LOCAL_ACK = {"Runtime.runIfWaitingForDebugger", "Inspector.enable"}


class Link:
    """One connected extension: RPC to it, and the DevTools events it sends."""
    def __init__(self, owner: str | None, project: str):
        self.id = secrets.token_urlsafe(12)
        self.owner, self.project = owner, project
        self.ws = None
        self.created = time.time()
        self.pending: dict[int, asyncio.Future] = {}
        self.forwards: dict[int, Any] = {}        # id -> callback run in the receive loop, so replies and events keep Chrome's order
        self.n = 0
        self.sessions: set["CdpSession"] = set()

    async def rpc(self, method: str, timeout: float = 60, **params) -> Any:
        if not self.ws:
            raise RuntimeError("The extension is not connected")
        self.n += 1
        n = self.n                                  # this call's own id: calls run concurrently
        fut = asyncio.get_running_loop().create_future()
        self.pending[n] = fut
        if DEBUG:
            print("HUB->EXT", n, method, params.get("cdpMethod", ""), flush=True)
        await self.ws.send_text(json.dumps({"id": n, "method": method, "params": params}))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(n, None)

    async def forward(self, method: str, cb, **params) -> None:
        """Send a command whose answer is handed to `cb(result, error)` in arrival order (a DevTools client depends on that order)."""
        if not self.ws:
            return await cb(None, "The extension is not connected")
        self.n += 1
        self.forwards[self.n] = cb
        await self.ws.send_text(json.dumps({"id": self.n, "method": method, "params": params}))

    async def serve(self, ws) -> None:
        self.ws = ws
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if "id" in msg and msg["id"] in self.forwards:
                    err = msg.get("error")
                    await self.forwards.pop(msg["id"])(msg.get("result"), (err.get("message", "error") if isinstance(err, dict) else str(err)) if err else None)
                elif "id" in msg:
                    fut = self.pending.get(msg["id"])
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(msg["error"].get("message", "error") if isinstance(msg["error"], dict) else str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result"))
                else:
                    for s in list(self.sessions):
                        await s.on_extension_event(msg)
        finally:
            self.ws = None
            for f in self.pending.values():
                if not f.done():
                    f.set_exception(RuntimeError("The extension disconnected"))
                    f.exception()                      # retrieved: whoever awaits it still gets it, nobody warns about it
            for cb in list(self.forwards.values()):
                try:
                    await cb(None, "The extension disconnected")
                except Exception:
                    pass
            self.forwards.clear()
            for s in list(self.sessions):
                await s.close()


class CdpSession:
    """One DevTools client (Playwright) talking to one extension link, scoped to the tabs given."""
    def __init__(self, link: Link, tab_id: int, token: str | None = None):
        self.link, self.tab_id = link, tab_id
        self.tabs: dict[int, dict] = {}          # the tabs of THIS session: the one it was given, and any it opens
        self.token = token or secrets.token_urlsafe(16)
        self.client = None
        self.child_tab: dict[str, int] = {}       # child (frame / worker) session id -> tab
        self.auto = False                          # Target.setAutoAttach was asked for
        self.announced: set[int] = set()
        self.used = False                           # a run has claimed it
        self.video = False                          # stream the tab's screencast (the run's video)
        self.frames: list[tuple[int, Any]] = []     # (ms, jpeg path)
        self.frames_dir = None
        self.opener: dict[int, int] = {}            # tab -> the tab that opened it
        self.target_of: dict[int, str] = {}        # tab -> target id (Chrome's: the main frame's id; Playwright relies on that)
        self.tab_of_target: dict[str, int] = {}
        self.lock = asyncio.Lock()
        self.log_unknown: list[str] = []

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def sid(tab_id: int) -> str:
        return f"tab{tab_id}"

    async def ensure_target(self, tab_id: int) -> str:
        if tab_id not in self.target_of:
            tid = None
            for _ in range(40):                      # a tab that has just opened may not answer for a moment
                try:
                    tree = await self.link.rpc("cdp", timeout=5, tabId=tab_id, cdpMethod="Page.getFrameTree", cdpParams={})
                    tid = tree["frameTree"]["frame"]["id"]
                    break
                except Exception:
                    await asyncio.sleep(0.1)
            tid = tid or f"tab{tab_id}"
            self.target_of[tab_id] = tid
            self.tab_of_target[tid] = tab_id
        return self.target_of[tab_id]

    def tab_for_target(self, target_id: str) -> int | None:
        if target_id in self.tab_of_target:
            return self.tab_of_target[target_id]
        return int(target_id[3:]) if str(target_id).startswith("tab") and str(target_id)[3:].isdigit() else None

    def info(self, tab_id: int) -> dict:
        t = self.tabs.get(tab_id, {})
        info = {"targetId": self.target_of.get(tab_id, f"tab{tab_id}"), "type": "page", "title": t.get("title", ""), "url": t.get("url", ""), "attached": True, "canAccessOpener": False, "browserContextId": "extension-default"}
        op = self.opener.get(tab_id)
        if op is not None and op in self.target_of:
            info["openerId"], info["canAccessOpener"] = self.target_of[op], True
        return info

    async def send(self, obj: dict) -> None:
        if DEBUG:
            print("->PW", obj.get("sessionId"), obj.get("method") or ("reply %s" % obj.get("id")), json.dumps(obj.get("params") or obj.get("result") or obj.get("error"))[:160], flush=True)
        if self.client:
            try:
                await self.client.send_text(json.dumps(obj))
            except Exception:
                pass

    async def announce(self, tab_id: int) -> None:
        if tab_id in self.announced:
            return
        self.announced.add(tab_id)
        await self.ensure_target(tab_id)
        await self.send({"method": "Target.targetCreated", "params": {"targetInfo": self.info(tab_id)}})
        await self.send({"method": "Target.attachedToTarget", "params": {"sessionId": self.sid(tab_id), "targetInfo": self.info(tab_id), "waitingForDebugger": False}})

    # ------------------------------------------------------------------ extension -> client
    async def on_extension_event(self, msg: dict) -> None:
        ev, tab = msg.get("event"), msg.get("tabId")
        if ev == "cdp":
            sid = msg.get("sessionId") or self.sid(tab)
            m, p = msg["method"], msg.get("params") or {}
            if m == "Target.attachedToTarget" and p.get("sessionId"):
                self.child_tab[p["sessionId"]] = tab
            elif m == "Target.detachedFromTarget":
                self.child_tab.pop(p.get("sessionId"), None)
            if tab in self.tabs or msg.get("sessionId"):
                await self.send({"sessionId": sid, "method": m, "params": p})
        elif ev == "frame":
            if self.video and msg.get("data"):
                import base64
                from pathlib import Path
                import tempfile
                if self.frames_dir is None:
                    self.frames_dir = Path(tempfile.mkdtemp(prefix="aiauto-screencast-"))
                p = self.frames_dir / f"{len(self.frames):06d}.jpg"
                p.write_bytes(base64.b64decode(msg["data"]))
                self.frames.append((int(msg.get("t") or time.time() * 1000), p))
        elif ev == "tab":
            kind = msg.get("kind")
            if kind == "created":
                if msg.get("openerTabId") not in self.tabs:
                    return
                self.tabs[tab] = {"url": msg.get("url", ""), "title": msg.get("title", "")}
                if msg.get("openerTabId") is not None:
                    self.opener[tab] = msg["openerTabId"]
                if self.auto:
                    # a new tab is announced once it has committed its first page: DevTools clients treat an empty first document
                    # specially (they wait for the navigation away from it), and ours would have happened before they listen
                    asyncio.ensure_future(self._announce_when_loaded(tab))
            elif kind == "removed":
                self.tabs.pop(tab, None)
                if tab in self.announced:
                    self.announced.discard(tab)
                    await self.send({"method": "Target.detachedFromTarget", "params": {"sessionId": self.sid(tab), "targetId": self.target_of.get(tab, f"tab{tab}")}})
                    await self.send({"method": "Target.targetDestroyed", "params": {"targetId": self.target_of.get(tab, f"tab{tab}")}})
            elif kind == "info":
                self.tabs.setdefault(tab, {}).update({k: msg[k] for k in ("url", "title") if k in msg})
                if tab in self.announced:
                    await self.send({"method": "Target.targetInfoChanged", "params": {"targetInfo": self.info(tab)}})

    async def _announce_when_loaded(self, tab: int) -> None:
        for _ in range(60):
            try:
                i = await self.link.rpc("tabInfo", timeout=5, tabId=tab)
            except Exception:
                return
            if i.get("url") and not i["url"].startswith("about:") and i.get("status") != "loading":
                self.tabs[tab] = {"url": i["url"], "title": i.get("title", "")}
                break
            await asyncio.sleep(0.1)
        if tab in self.tabs:
            await self.announce(tab)

    # ------------------------------------------------------------------ client -> extension
    async def handle(self, msg: dict) -> None:
        mid, method, params, sid = msg.get("id"), msg.get("method", ""), msg.get("params") or {}, msg.get("sessionId")
        if DEBUG:
            print("PW->", sid, method, json.dumps(params)[:160], flush=True)

        async def ok(result=None):
            await self.send({"id": mid, "result": result or {}, **({"sessionId": sid} if sid else {})})

        async def fail(text):
            await self.send({"id": mid, "error": {"code": -32000, "message": text}, **({"sessionId": sid} if sid else {})})

        try:
            if not sid:
                return await self._browser(method, params, ok, fail)
            if method in LOCAL_ACK and (method != "Runtime.runIfWaitingForDebugger" or sid not in self.child_tab):
                return await ok()                   # Chrome's debugger API never answers these for a tab; a tab is never paused for us
            tab = self.child_tab.get(sid) if sid in self.child_tab else (int(sid[3:]) if sid.startswith("tab") else None)
            if tab is None:
                return await fail(f"No such session {sid}")
            async def relay(result, error):
                await (fail(error) if error else ok(result))
            await self.link.forward("cdp", relay, tabId=tab, sessionId=sid if sid in self.child_tab else None, cdpMethod=method, cdpParams=params)
        except Exception as e:
            await fail(str(e))

    async def _browser(self, method, params, ok, fail):
        link = self.link
        if method == "Browser.getVersion":
            return await ok({"protocolVersion": "1.3", "product": PRODUCT, "revision": "0", "userAgent": f"Mozilla/5.0 {PRODUCT}", "jsVersion": "0"})
        if method == "Target.setAutoAttach":
            self.auto = bool(params.get("autoAttach"))
            if self.auto:                           # as Chrome does: the targets are announced before the command is answered
                for t in list(self.tabs):
                    await self.announce(t)
            return await ok()
        if method == "Target.setDiscoverTargets":
            for t in list(self.tabs):
                await self.ensure_target(t)
                await self.send({"method": "Target.targetCreated", "params": {"targetInfo": self.info(t)}})
            return await ok()
        if method == "Target.getTargetInfo":
            tid = params.get("targetId")
            if tid and self.tab_for_target(tid) is not None:
                return await ok({"targetInfo": self.info(self.tab_for_target(tid))})
            return await ok({"targetInfo": {"targetId": "browser", "type": "browser", "title": "", "url": "", "attached": True, "canAccessOpener": False}})
        if method == "Target.getTargets":
            for t in list(self.tabs):
                await self.ensure_target(t)
            return await ok({"targetInfos": [self.info(t) for t in self.tabs]})
        if method == "Target.getBrowserContexts":
            return await ok({"browserContextIds": []})
        if method == "Target.attachToTarget":
            t = self.tab_for_target(params.get("targetId", ""))
            if t not in self.tabs:
                return await fail("No such target")
            self.announced.add(t)
            return await ok({"sessionId": self.sid(t)})
        if method == "Target.createTarget":
            r = await link.rpc("createTab", url=params.get("url") or "about:blank")
            self.tabs[r["tabId"]] = {"url": r.get("url", ""), "title": ""}
            await ok({"targetId": await self.ensure_target(r["tabId"])})
            if self.auto:
                await self.announce(r["tabId"])
            return
        if method == "Target.closeTarget":
            t = self.tab_for_target(params.get("targetId", "")) or 0
            await link.rpc("closeTab", tabId=t)
            return await ok({"success": True})
        if method == "Target.activateTarget":
            await link.rpc("activateTab", tabId=self.tab_for_target(params.get("targetId", "")) or 0)
            return await ok()
        if method in ("Target.createBrowserContext", "Target.disposeBrowserContext"):
            return await fail("The extension drives your own browser window: there is no separate browser context to create")
        if method == "Browser.close":
            return await ok()                       # never close the person's browser
        if method.startswith(("Browser.", "Target.")):
            self.log_unknown.append(method)
            log.debug("browser-level command answered with {}: %s", method)
            return await ok()
        await fail(f"Unknown browser-level command {method}")

    async def serve_client(self, ws) -> None:
        self.client = ws
        self.link.sessions.add(self)
        try:
            await self.link.rpc("attach", tabId=self.tab_id, screencast=self.video)
        except Exception as e:
            await ws.close(code=1011, reason=str(e)[:100])
            return
        try:
            info = await self.link.rpc("tabInfo", tabId=self.tab_id)
            self.tabs[self.tab_id] = {"url": info.get("url", ""), "title": info.get("title", "")}
            while True:
                text = await ws.receive_text()
                asyncio.ensure_future(self.handle(json.loads(text)))
        except Exception:
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        self.link.sessions.discard(self)
        try:
            await self.link.rpc("release", timeout=5)
        except Exception:
            pass
        if self.client:
            try:
                await self.client.close()
            except Exception:
                pass
            self.client = None


def finish_video(token: str | None, run_dir) -> str | None:
    """The screencast of a finished session -> run_dir/video.webm (None if there was none)."""
    import shutil
    from .video import build_video
    sess = SESSIONS.get(token or "")
    if not sess or not sess.frames:
        return None
    frames = sorted(sess.frames)
    base = frames[0][0]
    ok = build_video([(t - base, p) for t, p in frames], frames[-1][0] - base + 700, run_dir / "video.webm")
    if sess.frames_dir:
        shutil.rmtree(sess.frames_dir, ignore_errors=True)
    sess.frames = []
    return "video.webm" if ok else None


RUN_LINKS: dict[str, Link] = {}      # run id -> the extension that is watching it (progress events are pushed to it)
LINKS: dict[str, Link] = {}
SESSIONS: dict[str, CdpSession] = {}
