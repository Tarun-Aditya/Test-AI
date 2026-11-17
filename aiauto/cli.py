"""Command line:

  python -m aiauto run  "Open https://... then click Login" [--data data.json] [--engine selenium]
  python -m aiauto run  --file test.txt
  python -m aiauto replay artifacts/<run>/script.json [--data other.json]
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

from .data import iterations, load_data
from .models import RunConfig


def _config(a) -> RunConfig:
    return RunConfig(engine=a.engine, headless=not a.headed, browser=a.browser, record_video=not a.no_video,
                     timeout_ms=a.timeout, use_llm=not a.no_llm)


def _add_run_opts(p):
    p.add_argument("--data", help="JSON file, inline JSON or k=v;k2=v2")
    p.add_argument("--engine", choices=["playwright", "selenium"], default="playwright")
    p.add_argument("--browser", default="chromium")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--no-llm", action="store_true", help="use the rule-based parser only")
    p.add_argument("--timeout", type=int, default=10000, help="per-step timeout in ms")
    p.add_argument("--out", default=os.getenv("AIAUTO_ARTIFACTS", "artifacts"))


def _print_summary(s, out):
    icon = {"passed": "✔", "failed": "✘", "skipped": "-"}
    for r in s.results:
        print(f"  {icon[r.status.value]} {r.index + 1:>2}. {r.step.raw or r.step.action.value}"
              + (f"\n       {r.error}" if r.error else ""))
    print(f"\n{s.status.value.upper()} in {s.duration_ms / 1000:.1f}s -> {Path(out) / s.run_id / 'report.html'}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aiauto", description="AI-driven browser test automation")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="run plain-English instructions")
    p.add_argument("text", nargs="?")
    p.add_argument("--file", "-f")
    p.add_argument("--name")
    _add_run_opts(p)

    p = sub.add_parser("replay", help="re-run a generated script.json (no AI unless a locator broke)")
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

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if a.cmd in ("run", "parse"):
        from .nlp.parser import parse_text
        text = Path(a.file).read_text() if a.file else (a.text or sys.stdin.read())
        data = load_data(getattr(a, "data", None))
        test, info = parse_text(text, data, name=getattr(a, "name", None), use_llm=not a.no_llm)
        if a.cmd == "parse":
            print(json.dumps({"parser": info["parser"], "unparsed": info["unparsed"],
                              "steps": [s.model_dump(exclude_none=True, exclude={"fallbacks", "optional"}) for s in test.steps]}, indent=2))
            return 0
        if info["unparsed"]:
            print("Could not understand:", *info["unparsed"], sep="\n  - ")
            return 2
        from .runner import run_test
        rc = 0
        for row in iterations(data):
            t = test.model_copy(update={"data": row})
            print(f"Running '{t.name}' ({len(t.steps)} steps, parser={info['parser']}, engine={a.engine})")
            s = run_test(t, _config(a), a.out, mode="ai")
            _print_summary(s, a.out)
            rc |= s.status.value != "passed"
        return rc

    if a.cmd == "replay":
        from .runner import load_script, run_test
        test = load_script(a.script)
        data = {**test.data, **load_data(a.data)}
        cfg = _config(a)
        cfg.self_heal = not a.no_heal
        rc = 0
        for row in iterations(data):
            s = run_test(test.model_copy(update={"data": row}), cfg, a.out, mode="replay")
            _print_summary(s, a.out)
            rc |= s.status.value != "passed"
        return rc

    if a.cmd == "server":
        import uvicorn
        uvicorn.run("aiauto.server.app:app", host=a.host, port=a.port)
        return 0

    if a.cmd == "agent":
        from .agent import run_agent
        run_agent(a.server, a.name, a.token, a.out)
        return 0


if __name__ == "__main__":
    sys.exit(main())
