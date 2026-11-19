# Development

For people changing Autopilot itself. How it fits together is in [Architecture](architecture.md).

## Set up

```bash
./setup.sh                                   # Windows: setup.bat
source .venv/bin/activate                    # Windows: .venv\Scripts\activate
pip install pytest
```

Run the hub from the checkout with `python -m aiauto server` (auto-reload: `uvicorn aiauto.server.app:app --reload`).

## The web app

The React source is in `web/src`; the built app is committed in `aiauto/server/static/app`, so Python users never
need Node.

```bash
cd web
npm install
npm run dev          # http://localhost:5173, proxies /api and /ws to a hub on :8000
npm run build        # writes aiauto/server/static/app; commit the result
```

| File | What it is |
|---|---|
| `App.jsx` | Shell: sidebar, top bar, routing (`#/runs/<id>`), project switcher, live-event feed |
| `views/*.jsx` | One file per page: Dashboard (Overview), Requirements (Test design), Studio, Runs, Library, Sessions (Saved logins), Machines, Settings (Admin), MyAI, Login |
| `components/` | RunDrawer (run page), LiveViewer, CodeExplorer, DataEditor, MachinePicker |
| `api.js` | `fetch` wrapper: adds `X-Requested-With`, `X-Project`, handles 401 |
| `styles.css` | All styles; light and dark themes as CSS variables |

## Tests

```bash
pytest -q                                    # everything (~10 minutes; needs Chromium and Chrome or Edge)
pytest -q tests/test_parsing.py tests/test_elements.py    # parser + element finding, a minute or two
pytest -q --live-ai tests/test_llm.py        # also the tests that call a real AI provider
```

| File | Covers |
|---|---|
| `test_parsing.py` | English → steps (rules and prose), data, iterations, masking |
| `test_elements.py` | Element finding on the playground on both engines: shadow roots, iframes, dialogs, drag and drop, highlight |
| `test_shop.py`, `test_e2e.py` | Multi-page flows, generated scripts, replay, self-healing, export |
| `test_browsers.py` | Every engine × browser the machine has, headed mode, generated scripts |
| `test_server.py` | Hub API, live events, library, sessions, cancel, persistence across restarts |
| `test_auth.py` | Accounts, roles, projects, SSO, agent tokens, CSRF header |
| `test_agent.py` | Remote agents, slots, pools, grids, cancel |
| `test_integrations.py` | Context packs, test design, write-back, against `tests/fake_tools.py` |
| `test_llm.py` | AI providers and fallbacks (real calls only with `--live-ai`) |

Tests that start browsers use Playwright's Chromium; set `AIAUTO_CHROME_BINARY` to use another. Tests that need a
browser the machine doesn't have are skipped.

## The real-website suite

`examples/real_sites/` holds eight scenarios on public practice sites built for automation (SauceDemo, the-internet,
UI Testing Playground, DemoQA, Selenium's shadow-root page, Books to Scrape, TodoMVC): iframes, nested frames, shadow
DOM, alerts, dynamic loading, AJAX waits, hovers, drag and drop, tables, custom-skinned selects, unlabelled checkboxes,
shuffled look-alike buttons, non-breaking spaces and data-driven iterations.

```bash
python tools/real_sites.py --out real_out                       # headless, AI as configured
python tools/real_sites.py --headed --no-ai --workers 3 --out real_out
python tools/real_sites.py --matrix playwright:firefox,selenium:msedge --strict
```

It runs each scenario on each engine through a hub, replays every generated script, runs every exported project on
its own, and writes `REPORT.md` and `results.json`: pass/fail, timings, element-finding time, video size,
screenshots and live-view frames per run. On Linux without a display, wrap it in
`xvfb-run -a -s "-screen 0 1920x1080x24"` for `--headed`.

## CI

| Workflow | Runs on | What |
|---|---|---|
| `cross-platform.yml` | Windows, macOS, Linux | Browser matrix (incl. Edge, Safari), agents and parallel slots, unit tests; the one-step setup scripts and demo data |
| `real-sites.yml` | Windows (Chrome, Edge), macOS (Chrome, WebKit; Safari separately), Linux (Chrome, Firefox) | The real-website suite, visible browsers, AI off; `REPORT.md` on the run's summary page |

## Conventions

- Keep the hub, generated scripts and exported projects working without AI. AI may improve a result, never be
  required for one.
- Generated scripts depend only on Playwright / Selenium and `autopilot_runtime.py`; never import `aiauto` from them.
- A new database column goes in `SCHEMA` and as an `ALTER TABLE` migration in `DB.__init__` (old databases upgrade
  on start); if reports should see it, add it to the `report_*` views in `VIEWS` and to [Database](database.md).
- Secrets: never log them, never return them to the browser, mask them in anything written to disk.
- New sentence patterns go in `aiauto/nlp/rules.py` with a test in `test_parsing.py`; document them in the user
  guide's table.
