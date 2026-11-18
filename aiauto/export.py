"""Export a run (or a whole batch of iterations) as a self-runnable Python test project.

    my-test/
      README.md            how to install and run
      requirements.txt     playwright + selenium
      config.json          engine, browser, headless, timeouts, video ... used for the recorded run
      iterations.json      one entry per iteration: data file + browser (+ engine)
      data/iteration_N.json  the data each iteration used (sensitive values masked)
      .env.example         the AIAUTO_SECRET_* variables to fill for masked values
      tests/test_playwright.py, tests/test_selenium.py   plain native scripts (no AI, no self-healing)
      tests/autopilot_runtime.py   the standalone framework the scripts use: browser setup, green highlight,
                                   screenshot per step, video, masking, report.html, summary.json, junit.xml
      results/iteration_N/     created by run.py: report.html, video.webm, shots/, summary.json, junit.xml
      results/index.html       one page linking every iteration's report
      instructions.txt     the original plain-English test
      run.py, run.sh, run.bat

The scripts are exactly what the successful run generated: fixed native locators, no self-healing.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from .data import env_secret_name

RUN_PY = r'''#!/usr/bin/env python3
"""Run this exported test for every iteration.

  python run.py                       # all iterations, engine/browser from config.json
  python run.py --engine selenium     # switch engine
  python run.py --browser firefox     # force a browser for every iteration
  python run.py --headed              # watch the browser
  python run.py --only 2              # just iteration 2
"""
import argparse
import html
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    cfg = json.loads((ROOT / "config.json").read_text())
    its = json.loads((ROOT / "iterations.json").read_text())
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["playwright", "selenium"])
    ap.add_argument("--browser")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--only", type=int, help="run only iteration N (1-based)")
    a = ap.parse_args()
    load_env(ROOT / ".env")

    results = []
    for n, it in enumerate(its, 1):
        if a.only and a.only != n:
            continue
        engine = a.engine or it.get("engine") or cfg["engine"]
        browser = a.browser or it.get("browser") or cfg.get("browser", "chromium")
        env = {**os.environ, "BROWSER": browser,
               "HEADLESS": "0" if a.headed else ("1" if cfg.get("headless", True) else "0"),
               "STEP_TIMEOUT": str(cfg.get("timeout_ms", 15000))}
        if a.no_video or not cfg.get("record_video", True):
            env["NO_VIDEO"] = "1"
        out = ROOT / "results" / f"iteration_{n}"
        env["RESULTS_DIR"] = str(out)
        print(f"\n=== Iteration {n}/{len(its)}: {engine} / {browser} / {it['data']} ===", flush=True)
        t0 = time.time()
        rc = subprocess.call([sys.executable, str(ROOT / "tests" / f"test_{engine}.py"), str(ROOT / it["data"])],
                             cwd=ROOT, env=env)
        results.append((n, engine, browser, rc == 0, time.time() - t0, out))

    print("\nSummary")
    for n, engine, browser, ok, dur, out in results:
        print(f"  #{n:<3} {engine:<10} {browser:<9} {'PASSED' if ok else 'FAILED'}  {dur:5.1f}s  {out / 'report.html'}")
    failed = sum(1 for r in results if not r[3])
    index = write_index(results)
    print(f"\n{len(results) - failed} passed, {failed} failed  ->  {index}")
    sys.exit(1 if failed else 0)


def write_index(results):
    """results/index.html: one row per iteration with links to its report, video and junit."""
    rows = []
    for n, engine, browser, ok, dur, out in results:
        rel = out.relative_to(ROOT / "results").as_posix()
        steps = ""
        try:
            s = json.loads((out / "summary.json").read_text(encoding="utf-8"))["steps"]
            steps = f"{sum(x['status'] == 'passed' for x in s)}/{len(s)}"
        except Exception:
            pass
        links = [f'<a href="{rel}/report.html">report</a>']
        if (out / "video.webm").exists():
            links.append(f'<a href="{rel}/video.webm">video</a>')
        if (out / "junit.xml").exists():
            links.append(f'<a href="{rel}/junit.xml">junit</a>')
        rows.append(f"<tr><td>#{n}</td><td class={'ok' if ok else 'bad'}>{'PASSED' if ok else 'FAILED'}</td>"
                    f"<td>{html.escape(engine)}</td><td>{html.escape(browser)}</td><td>{steps}</td>"
                    f"<td>{dur:.1f}s</td><td>{' · '.join(links)}</td></tr>")
    page = ("<!doctype html><meta charset=utf-8><title>Test results</title><style>body{font:14px system-ui;margin:24px}"
            "table{border-collapse:collapse}td,th{padding:6px 12px;border-bottom:1px solid #ddd;text-align:left}"
            ".ok{color:#15803d;font-weight:600}.bad{color:#b91c1c;font-weight:600}</style>"
            f"<h2>{html.escape(ROOT.name)}</h2><table><tr><th>Iteration</th><th>Result</th><th>Engine</th><th>Browser</th>"
            "<th>Steps</th><th>Time</th><th>Artifacts</th></tr>" + "".join(rows) + "</table>")
    path = ROOT / "results" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


if __name__ == "__main__":
    main()
'''

README = """# {name}

Exported from AI Test Automation (Autopilot). The test was written in plain English,
executed with AI-identified locators, and this project contains the **generated scripts**
(native Playwright / Selenium locators, no AI and no self-healing at run time).

It is self-contained: it does not need the Autopilot tool, its server or an API key. The scripts use
`tests/autopilot_runtime.py`, a small framework that gives every run the same output as the original run:
a green highlight box on each located element, a screenshot per step, a video, sensitive values masked,
and `report.html` (screenshots + embedded video), `summary.json` and `junit.xml` (for CI).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python -m playwright install chromium                  # for the Playwright engine
```
{secrets_section}
## Run

```bash
python run.py                      # every iteration with the recorded engine / browsers
python run.py --engine selenium    # same test on the other engine
python run.py --browser firefox --headed
python run.py --only 2             # a single iteration
```
Or run one script directly: `python tests/test_playwright.py data/iteration_1.json`.

Each iteration writes `results/iteration_N/` (report.html, video.webm, shots/, summary.json, junit.xml);
`results/index.html` links them all. New data: edit `data/iteration_N.json` (or add files and rows to
`iterations.json`). `run.py` exits non-zero if any iteration failed.

Environment variables the scripts read: `BROWSER` (chromium | chrome | msedge | firefox | webkit),
`HEADLESS=1`, `NO_VIDEO=1`, `HIGHLIGHT=0`, `STEP_TIMEOUT` (ms), `RESULTS_DIR`, `STORAGE_STATE` (saved session file),
`CHROME_PROFILE_DIR` / `CHROME_PROFILE_NAME` (reuse a Chrome profile).

## Iterations

| # | Data | Browser | Engine |
|---|------|---------|--------|
{iter_rows}

## Recorded configuration

```json
{config}
```

## Original instructions

```
{instructions}
```
"""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "test").lower()).strip("-")[:50] or "test"


def build_project(artifacts: Path, run: dict, members: list[dict]) -> tuple[str, bytes]:
    # use scripts from a passing member if there is one (the "successful run" scripts)
    members = [m for m in members if (artifacts / m["run_id"] / "script.json").exists()] or [run]
    primary = next((m for m in members if m.get("status") == "passed"), None) or next(
        (m for m in members if m["run_id"] == run["run_id"]), members[0])
    pdir = artifacts / primary["run_id"]
    if not (pdir / "test_playwright.py").exists():
        raise FileNotFoundError("This run has no generated scripts yet (still running, or its machine never uploaded artifacts)")
    script = json.loads((pdir / "script.json").read_text())
    base_name = re.sub(r"\s*\[#\d+.*?\]$", "", script.get("name") or run.get("test_name") or "test")
    name = _slug(base_name)
    cfg = json.loads(primary.get("config_json") or "{}")
    config = {k: cfg.get(k) for k in ("engine", "browser", "headless", "timeout_ms", "record_video", "screenshot_each_step",
                                     "viewport_width", "viewport_height", "dialogs") if k in cfg}
    config.setdefault("engine", primary.get("engine") or "playwright")
    config.setdefault("browser", primary.get("browser") or "chromium")

    sensitive: set[str] = set(script.get("sensitive", []))
    iters, files = [], {}
    for n, m in enumerate(members, 1):
        mdir = artifacts / m["run_id"]
        data = json.loads(m["data_json"]) if m.get("data_json") else (
            json.loads((mdir / "data.json").read_text()) if (mdir / "data.json").exists() else {})
        sensitive |= set(json.loads(m.get("sensitive_json") or "[]"))
        mcfg = json.loads(m.get("config_json") or "{}")
        path = f"data/iteration_{n}.json"
        files[path] = json.dumps(data, indent=2)
        iters.append({"data": path, "browser": mcfg.get("browser") or m.get("browser") or config["browser"],
                      "engine": mcfg.get("engine") or m.get("engine"), "source_run": m["run_id"], "status": m.get("status")})

    env_lines = ["# Values for data marked sensitive (masked in data/*.json). Copy to .env and fill in."]
    env_lines += [f"{env_secret_name(k)}=" for k in sorted(sensitive)]
    secrets_section = ""
    if sensitive:
        secrets_section = ("\n## Secrets\n\nSensitive values are masked in `data/`. Copy `.env.example` to `.env` and fill in:\n\n"
                           + "\n".join(f"- `{env_secret_name(k)}` ({k})" for k in sorted(sensitive)) + "\n")
    iter_rows = "\n".join(f"| {i + 1} | `{it['data']}` | {it['browser']} | {it['engine'] or config['engine']} |" for i, it in enumerate(iters))

    files.update({
        "README.md": README.format(name=base_name, secrets_section=secrets_section, iter_rows=iter_rows,
                                   config=json.dumps(config, indent=2), instructions=script.get("source_text", "").strip() or "(none)"),
        "requirements.txt": "playwright>=1.45\nselenium>=4.20\nimageio-ffmpeg>=0.5\n",
        "config.json": json.dumps(config, indent=2),
        "iterations.json": json.dumps(iters, indent=2),
        ".env.example": "\n".join(env_lines) + "\n",
        "instructions.txt": script.get("source_text", ""),
        "run.py": RUN_PY,
        "run.sh": "#!/usr/bin/env bash\nset -e\ncd \"$(dirname \"$0\")\"\npython3 run.py \"$@\"\n",
        "run.bat": "@echo off\ncd /d %~dp0\npython run.py %*\n",
        ".gitignore": ".env\nresults/\nvideos/\n__pycache__/\n.venv/\n",
        "tests/test_playwright.py": (pdir / "test_playwright.py").read_text(),
        "tests/test_selenium.py": (pdir / "test_selenium.py").read_text(),
        "tests/script.json": json.dumps(script, indent=2),
        "tests/autopilot_runtime.py": Path(__file__).with_name("script_runtime.py").read_text(),
    })
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            info = zipfile.ZipInfo(f"{name}/{path}")
            info.external_attr = (0o755 if path in ("run.sh", "run.py") else 0o644) << 16
            z.writestr(info, content)
    return name, buf.getvalue()
