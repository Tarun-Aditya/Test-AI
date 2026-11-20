"""Step 2 of 3. Record the end-to-end demo: drives the web app scene by scene in real time, holding each scene for its narration.
Writes frames/ (Chrome screencast) and timeline.json (scene start times)."""
import base64, json, os, shutil, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from narration import SCENES

BASE = os.getenv("DEMO_HUB", "http://localhost:8000")
HERE = Path(__file__).resolve().parent
WORK = Path(os.getenv("DEMO_WORK", HERE / "_work")).resolve()
FR = WORK / "frames"; shutil.rmtree(FR, ignore_errors=True); FR.mkdir(parents=True)
DUR = json.load(open(WORK / "audio/durations.json"))
W, H = 1440, 900
TEST = """Open http://localhost:8000/demo/shop/index.html
Type "wireless mouse" into the search box and press Enter
Click the first result containing "Logitech"
Select "2" from Quantity
Click Add to Cart
Go to the cart and make sure it shows "Subtotal (2 items)\""""
ITER_TEST = TEST.replace('"wireless mouse"', '"${product}"').replace('"Logitech"', '"${brand}"') \
    .replace('"2" from', '"${qty}" from').replace('"Subtotal (2 items)"', '"${brand}"')
STORY = """# Sign up form

As a visitor I want to create an account on the sign up page at /signup.html.

## Acceptance criteria
- Given I am on the sign up page, when I fill valid details and click Create account then I see "Account created"
- Email is required and must be a valid email address; shows "Enter a valid email"
- Age must be between 18 and 99; shows "Age must be 18-99"
"""
CURSOR = """(() => { const add = () => { if (document.getElementById('__demo_cursor') || !document.documentElement) return;
  const c = document.createElement('div'); c.id = '__demo_cursor';
  c.style.cssText = 'position:fixed;left:-50px;top:-50px;width:24px;height:24px;margin:-12px 0 0 -12px;border-radius:50%;' +
    'background:rgba(79,70,229,.28);border:2px solid #4f46e5;box-shadow:0 0 0 4px rgba(79,70,229,.12);z-index:2147483647;' +
    'pointer-events:none;transition:transform .12s ease';
  document.documentElement.appendChild(c);
  const p = JSON.parse(sessionStorage.getItem('__demo_xy') || 'null'); if (p) { c.style.left = p[0] + 'px'; c.style.top = p[1] + 'px'; }
  addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px';
    sessionStorage.setItem('__demo_xy', JSON.stringify([e.clientX, e.clientY])); }, true);
  addEventListener('mousedown', () => c.style.transform = 'scale(.6)', true);
  addEventListener('mouseup', () => c.style.transform = 'scale(1)', true); };
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', add); else add(); })();"""


def card(title, sub, lines=()):
    li = "".join(f"<div class='l'>{x}</div>" for x in lines)
    return f"""<!doctype html><html><head><meta charset=utf-8><style>
    body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#eef2ff,#f8fafc 60%);
    font-family:Inter,system-ui,sans-serif;color:#1c2230}} .w{{max-width:1000px;padding:0 40px}}
    .logo{{width:64px;height:64px;border-radius:16px;background:#4f46e5;color:#fff;display:flex;align-items:center;justify-content:center;font-size:34px;margin-bottom:28px}}
    h1{{font-size:56px;margin:0 0 14px;letter-spacing:-1px}} p{{font-size:24px;color:#4b5565;margin:0 0 30px;line-height:1.45}}
    .l{{font:20px/1.9 'DejaVu Sans Mono',monospace;color:#312e81}}</style></head>
    <body><div class=w><div class=logo>✦</div><h1>{title}</h1><p>{sub}</p>{li}</div></body></html>"""


class Rec:
    def __init__(self, c, p):
        self.frames, self.marks = [], []
        self.s = c.new_cdp_session(p)
        self.s.on("Page.screencastFrame", self.on)
        self.s.send("Page.startScreencast", {"format": "jpeg", "quality": 88, "maxWidth": W, "maxHeight": H})
        self.t0 = time.time()

    def snap(self, page):
        """A frame on demand: the screencast sends nothing for a page that never repaints (the title cards)."""
        path = FR / f"{len(self.frames):05d}c.jpg"
        page.screenshot(path=str(path), type="jpeg", quality=90)
        self.frames.append([round(time.time() - self.t0, 3), path.name])

    def on(self, ev):
        path = FR / f"{len(self.frames):05d}s.jpg"
        path.write_bytes(base64.b64decode(ev["data"]))
        self.frames.append([round(time.time() - self.t0, 3), path.name])
        try:
            self.s.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:
            pass


def main():
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=os.getenv("AIAUTO_CHROME_BINARY") or None)
        c = b.new_context(viewport={"width": W, "height": H})
        c.add_init_script("try { localStorage.setItem('aiauto.project', JSON.stringify('shop')); "
                          "localStorage.setItem('aiauto.theme', JSON.stringify('light')); } catch (e) {}")
        c.add_init_script(CURSOR)
        p = c.new_page()
        p.set_content(card("Autopilot", "AI test automation")); time.sleep(1)
        r = Rec(c, p)
        r.frames.append([0.0, "00000.jpg"]); p.screenshot(path=str(FR / "00000.jpg"), type="jpeg", quality=90)
        state = {"xy": (W / 2, H / 2)}

        def move(x, y, steps=22):
            p.mouse.move(x, y, steps=steps); state["xy"] = (x, y)

        def click(loc, pause=0.35):
            loc = loc.first
            loc.scroll_into_view_if_needed()
            bb = loc.bounding_box()
            move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            time.sleep(pause)
            p.mouse.click(*state["xy"])

        def nav(label):
            click(p.locator(".navgroup").get_by_text(label, exact=True)); time.sleep(1.2)

        def scene(name, body):
            start = time.time() - r.t0
            r.marks.append({"name": name, "t": round(start, 3)})
            print(f"{start:6.1f}s  {name}", flush=True)
            body()
            left = start + DUR[name] + 0.9 - (time.time() - r.t0)
            if left > 0:
                time.sleep(left)

        def api(path, project="shop"):
            return p.evaluate(f"async () => await (await fetch({json.dumps(path)}, {{headers: {{'X-Project': {json.dumps(project)}}}}})).json()")

        def wait_runs(project="shop", limit=90):
            end = time.time() + limit
            while time.time() < end and any(x["live"] for x in api("/api/runs?limit=10", project)):
                time.sleep(1)

        # ---------------------------------------------------------------- scenes
        def intro():
            p.set_content(card("Autopilot", "Plain English in. Playwright and Selenium runs, reports, videos and real scripts out.",
                               ["write  →  run live  →  replay the script", "design cases from a story  →  run anywhere"]))
            time.sleep(0.5); r.snap(p)

        def overview():
            p.goto(BASE + "/"); move(W / 2, H / 2, 1)
            click(p.get_by_label("Username")); p.keyboard.type("admin", delay=60)
            click(p.get_by_label("Password")); p.keyboard.type("autopilot-demo", delay=40)
            click(p.get_by_role("button", name="Sign in"))
            p.wait_for_selector(".navgroup", timeout=15000)
            p.wait_for_function("() => !document.body.innerText.includes('No runs yet') && document.querySelectorAll('table tbody tr').length > 2", timeout=20000)
            time.sleep(1.5)
            move(700, 220); time.sleep(1.2); move(900, 650); time.sleep(1)

        def write():
            nav("Studio")
            box = p.get_by_label("Test steps")
            click(box)
            box.press_sequentially(TEST, delay=22)

        def breakdown():
            click(p.get_by_role("button", name="Break down into steps")); time.sleep(2.5)
            move(690, 700, 12)
            for _ in range(4):
                p.mouse.wheel(0, 260); time.sleep(0.7)

        def run():
            click(p.get_by_role("button", name="Run test")); time.sleep(3)
            move(1500 * W / 2000, 450, 30)
            wait_runs()
            time.sleep(1.5)

        def runpage():
            rid = api("/api/runs?limit=1")[0]["run_id"]
            state["rid"] = rid
            p.goto(f"{BASE}/#/runs/{rid}"); time.sleep(2)
            click(p.locator("button.srow").nth(1)); time.sleep(0.6)
            click(p.locator("button.srow").nth(4)); time.sleep(0.6)
            for tab, hold in (("Video", 1.6), ("Scripts & files", 3.2), ("Report", 2.0), ("Data & settings", 2.5)):
                click(p.locator(".main").get_by_role("button", name=tab, exact=True)); time.sleep(hold)

        def replay():
            click(p.locator(".main").get_by_role("button", name="Replay", exact=True).last); time.sleep(1.5)
            click(p.locator(".main .seg button", has_text="Selenium").first); time.sleep(0.8)
            click(p.get_by_role("button", name="Replay script")); time.sleep(3)
            wait_runs(); time.sleep(1)

        def iterations():
            form = {"text": ITER_TEST, "name": "", "steps": None, "stepsText": None, "srcMode": "text", "dataMode": "iter",
                    "rows": [{"k": "", "v": "", "s": False}], "json": "", "engine": "both", "agent": "local",
                    "agents": ["local", "lab-linux"], "spread": "pool", "headless": True, "browser": "chromium", "timeout": 10000,
                    "video": True, "shots": True, "live": True, "highlight": True, "heal": True, "useLlm": False,
                    "iterCols": [{"key": "product", "sensitive": False}, {"key": "brand", "sensitive": False}, {"key": "qty", "sensitive": False}],
                    "iterRows": [{"on": True, "browser": "", "values": {"product": "wireless mouse", "brand": "Logitech", "qty": "2"}},
                                 {"on": True, "browser": "chrome", "values": {"product": "wireless mouse", "brand": "HP", "qty": "1"}}]}
            p.evaluate(f"localStorage.setItem('aiauto.form2', {json.dumps(json.dumps(form))})")
            p.goto(BASE + "/#/studio"); p.reload(); time.sleep(2)
            click(p.locator(".tabs button", has_text="Test data")); time.sleep(3)
            move(1430 * W / 2000 * 1.0, 50, 25); time.sleep(1.5)
            click(p.get_by_role("button", name="Run test")); time.sleep(3)
            nav("Runs"); time.sleep(1)
            click(p.locator("button.caretbtn")); time.sleep(1)

        def design():
            sel = p.locator(".sidebar select, aside select").first
            bb = sel.bounding_box(); move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2); time.sleep(0.4)
            sel.select_option("default"); time.sleep(2)
            nav("Test design")
            click(p.get_by_role("button", name="Paste a story")); time.sleep(0.8)
            box = p.locator("textarea").first
            click(box); box.fill(STORY); time.sleep(1.5)
            click(p.get_by_role("button", name="Design test cases"))
            p.wait_for_selector("text=Test cases", timeout=60000); time.sleep(2.5)
            move(1100, 800, 15)
            for _ in range(5):
                p.mouse.wheel(0, 300); time.sleep(0.8)

        def failure():
            bad = next(x for x in api("/api/runs", "default") if x["status"] == "failed")
            p.goto(f"{BASE}/#/runs/{bad['run_id']}"); time.sleep(2.5)
            failed = p.locator("button.srow.failed")
            if failed.count():
                click(failed); time.sleep(1.5)
            move(1500, 600, 20); time.sleep(2)

        def machines():
            nav("Machines"); time.sleep(1.5)
            move(800, 370, 20); time.sleep(1.5)
            click(p.get_by_role("button", name="Add a machine")); time.sleep(2)
            move(900, 700, 15)
            for _ in range(3):
                p.mouse.wheel(0, 250); time.sleep(0.9)

        def admin():
            nav("Admin"); time.sleep(1.2)
            for label in ("Integrations", "Sign-in (SSO)", "Users", "Projects", "Organisation AI"):
                click(p.get_by_role("button", name=label, exact=True)); time.sleep(2.6)

        def outro():
            p.set_content(card("Yours to keep", "Native scripts · exported projects for any CI · results in SQLite",
                               ["./setup.sh --demo   &&   ./start.sh", "setup.bat --demo   &&   start.bat", "docs/README.md"]))
            time.sleep(0.5); r.snap(p)

        fns = {k: v for k, v in locals().items() if callable(v)}
        for name, _ in SCENES:
            scene(name, fns[name])
        time.sleep(1.5)
        r.s.send("Page.stopScreencast")
        end = round(time.time() - r.t0, 3)
        json.dump({"marks": r.marks, "frames": r.frames, "end": end}, open(WORK / "timeline.json", "w"))
        print("done", end, "s", len(r.frames), "frames")
        b.close()


main()
