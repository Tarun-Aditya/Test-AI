"""Command line:

  python -m aiauto run  "Open https://... then click Login" [--data data.json] [--engine selenium]
  python -m aiauto run  --file test.txt --sensitive user.password --headed --save-session mysso
  python -m aiauto replay artifacts/<run>/script.json   (or a generated test_*.py)  [--data other.json]
  python -m aiauto parse "..."                      # show the steps only
  python -m aiauto server [--port 8000]             # web app + agent hub
  python -m aiauto agent --server http://host:8000  # run jobs from the hub on this machine
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import sessions
from .data import iterations, load_data, normalize
from .models import RunConfig


def _config(a) -> RunConfig:
    cfg = RunConfig(engine=a.engine, headless=not a.headed, browser=a.browser, record_video=not a.no_video,
                    timeout_ms=a.timeout, use_llm=not a.no_llm, dialogs=a.dialogs, profile_dir=a.profile_dir,
                    profile_name=a.profile_name, session=a.session, save_session=a.save_session,
                    full_page_screenshots=a.full_page, live_view=False)
    if a.session:
        cfg.session_state = sessions.load(a.session)
        if cfg.session_state is None:
            sys.exit(f"No saved session named '{a.session}' in {sessions.ROOT}/")
    return cfg


def _add_run_opts(p):
    p.add_argument("--data", help="JSON file, inline JSON or k=v;k2=v2")
    p.add_argument("--sensitive", action="append", default=[], help="data key to mask (repeatable), e.g. user.password")
    p.add_argument("--engine", choices=["playwright", "selenium"], default="playwright")
    p.add_argument("--browser", default="chromium", help="chromium | chrome | msedge | firefox | webkit")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--full-page", action="store_true", help="full-page step screenshots")
    p.add_argument("--no-llm", action="store_true", help="use the rule-based parser / heuristics only")
    p.add_argument("--timeout", type=int, default=10000, help="per-step timeout in ms")
    p.add_argument("--dialogs", choices=["accept", "dismiss"], default="accept")
    p.add_argument("--profile-dir", help="Chrome user-data-dir to reuse (saved passwords / SSO cookies)")
    p.add_argument("--profile-name", help='Chrome profile inside the user-data-dir, e.g. "Default"')
    p.add_argument("--session", help="load a saved login session by name")
    p.add_argument("--save-session", help="save cookies + localStorage at the end of a passing run under this name")
    p.add_argument("--out", default=os.getenv("AIAUTO_ARTIFACTS", "artifacts"))


def _print_summary(s, out):
    icon = {"passed": "✔", "failed": "✘", "skipped": "-"}
    for r in s.results:
        heal = " (self-healed)" if r.healed else ""
        print(f"  {icon[r.status.value]} {r.index + 1:>2}. {r.step.raw or r.step.action.value}{heal}"
              + (f"\n       {r.error}" if r.error else ""))
    print(f"\n{s.status.value.upper()} in {s.duration_ms / 1000:.1f}s -> {Path(out) / s.run_id / 'report.html'}")


def _run(test, cfg, a, mode):
    from .runner import run_test
    rc = 0
    for row in iterations(test.data):
        t = test.model_copy(update={"data": row})
        print(f"Running '{t.name}' ({len(t.steps)} steps, engine={cfg.engine}, mode={mode})")
        holder: list = []
        s = run_test(t, cfg, a.out, mode=mode, runner_out=holder)
        if cfg.save_session and holder and holder[0].saved_session:
            print(f"Session saved as '{cfg.save_session}' -> {sessions.save(cfg.save_session, holder[0].saved_session)}")
        _print_summary(s, a.out)
        rc |= s.status.value != "passed"
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aiauto", description="AI-driven browser test automation")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="run plain-English instructions")
    p.add_argument("text", nargs="?")
    p.add_argument("--file", "-f")
    p.add_argument("--name")
    _add_run_opts(p)

    p = sub.add_parser("replay", help="re-run a generated script.json / test_*.py (no AI unless a locator broke)")
    p.add_argument("script")
    p.add_argument("--no-heal", action="store_true")
    _add_run_opts(p)

    p = sub.add_parser("parse", help="show parsed steps")
    p.add_argument("text", nargs="?")
    p.add_argument("--file", "-f")
    p.add_argument("--no-llm", action="store_true")

    p = sub.add_parser("server", help="start the web app / agent hub")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("agent", help="connect this machine to a hub as an execution agent")
    p.add_argument("--server", default=os.getenv("AIAUTO_SERVER", "http://localhost:8000"))
    p.add_argument("--name", default=None)
    p.add_argument("--token", default=os.getenv("AIAUTO_AGENT_TOKEN"))
    p.add_argument("--out", default=os.getenv("AIAUTO_AGENT_ARTIFACTS", "agent_artifacts"))
    p.add_argument("--slots", type=int, default=int(os.getenv("AIAUTO_SLOTS", "1")),
                   help="how many tests this machine runs at the same time (default 1)")

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if a.cmd in ("run", "parse"):
        from .nlp.parser import parse_text
        text = Path(a.file).read_text(encoding="utf-8") if a.file else (a.text or sys.stdin.read())
        data = load_data(getattr(a, "data", None))
        test, info = parse_text(text, data, name=getattr(a, "name", None), use_llm=not a.no_llm,
                                sensitive=getattr(a, "sensitive", []))
        if a.cmd == "parse":
            print(json.dumps({"parser": info["parser"], "unparsed": info["unparsed"],
                              "steps": [s.model_dump(exclude_none=True, exclude={"fallbacks", "optional"}) for s in test.steps]}, indent=2))
            return 0
        if info["unparsed"]:
            print("Could not understand:", *info["unparsed"], sep="\n  - ")
            return 2
        return _run(test, _config(a), a, "ai")

    if a.cmd == "replay":
        from .scriptgen import extract_spec
        test = extract_spec(Path(a.script).read_text(encoding="utf-8"))
        extra, sens = normalize(load_data(a.data), a.sensitive)
        test = test.model_copy(update={"data": {**test.data, **extra}, "sensitive": sorted(set(test.sensitive) | set(sens))})
        cfg = _config(a)
        cfg.self_heal = not a.no_heal
        return _run(test, cfg, a, "replay")

    if a.cmd == "server":
        import uvicorn
        uvicorn.run("aiauto.server.app:app", host=a.host, port=a.port)
        return 0

    if a.cmd == "agent":
        from .agent import run_agent
        run_agent(a.server, a.name, a.token, a.out, a.slots)
        return 0


if __name__ == "__main__":
    sys.exit(main())
