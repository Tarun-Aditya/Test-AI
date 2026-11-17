# AI Test Automation

Write a browser test in plain English. The AI works out the **intent** (click, type, hover, select…) and the **value**, finds the right element on the live page, and runs the step with **Playwright** or **Selenium**. Every run produces artifacts you can keep:

| Artifact | What it is |
|---|---|
| `report.html` | Self-contained report: pass/fail per step, a screenshot for each step (embedded), the locator used, errors, and the run video |
| `video.webm` | Recording of the whole run |
| `script.json` | The resolved test: steps plus the locators that worked. **Replay this on the next run**, with no AI needed |
| `test_playwright.py` / `test_selenium.py` | Standalone, readable scripts you can commit and run anywhere |
| `screenshots/`, `summary.json`, `data.json` | Raw per-step evidence and machine-readable results |

You can run it as a **web app**, from the **CLI**, or through **agents** on any machine (your laptop, a VM, a CI box) that connect to a central hub.

```
 plain English + data (JSON / inline)
          │
          ▼
 ┌──────────────────┐   Claude (structured output), or the built-in rule parser as a fallback
 │ Intent & value   │──▶ steps: [{action: type, target: "username field", value: "${user.name}"}, …]
 └──────────────────┘
          │
          ▼
 ┌──────────────────┐   DOM snapshot → score candidates by text/label/placeholder/aria/role + action fit
 │ Locator finder   │──▶ unique CSS/XPath (testid > id > name > aria > placeholder > label > text > path)
 └──────────────────┘   + fallbacks; Claude breaks ties when two elements score close
          │
          ▼
 ┌──────────────────┐
 │ Playwright /     │──▶ screenshot per step, element highlight, video
 │ Selenium engine  │
 └──────────────────┘
          │
          ▼
 report.html · video · script.json · test_playwright.py · test_selenium.py
          │
          └──▶ next run: replay script.json (stored locators → fallbacks → AI self-heal)
```

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium          # Selenium finds or downloads its driver by itself

# optional: turn on Claude for intent extraction and element disambiguation
export ANTHROPIC_API_KEY=sk-ant-...

# web app (also the agent hub)
python -m aiauto server --port 8000   # open http://localhost:8000
```

### CLI

```bash
# run plain English
python -m aiauto run "Open https://example.com then click More information" --engine playwright

# from a file, with data
python -m aiauto run -f examples/login_test.txt --data examples/login_data.json --engine selenium --headed

# only look at the parsed steps
python -m aiauto parse -f examples/login_test.txt

# re-run a generated script with different data (no AI unless a locator broke)
python -m aiauto replay artifacts/<run-id>/script.json --data '{"user":{"name":"bob","password":"pw"}}'

# the generated scripts run by themselves
python artifacts/<run-id>/test_playwright.py other_data.json
```

`examples/login_test.txt` contains the placeholder `EXAMPLES_DIR`. Replace it with the absolute path to `examples/`, or point the test at your own site.

## Writing tests

One action per line. You can also chain actions with `then`, `and then` or `;`.

```
Open https://shop.example.com
Type "${user.email}" into the email field
Enter ${user.password} in password
Click on the "Sign in" button
Verify page contains "Welcome, ${user.name}"
Hover over Products menu
Click Laptops
Select "16 GB" from memory dropdown
Check Include accessories
Press Enter
Scroll down
Wait 2 seconds
Verify the url contains "/cart"
Take a screenshot named cart
```

Supported actions: navigate, click, double click, right click, type, append, press (keys and combos such as `ctrl+a`), hover, select, check, uncheck, scroll (direction or "scroll to X"), wait (seconds or for an element), upload, back, refresh, screenshot, and the checks assert text, assert visible, assert URL and assert title.

With Claude enabled you can write more freely, for example "log in as ${user.email} with ${user.password} and make sure the dashboard greets me". The model breaks that into the individual steps. Without a key, the rule parser handles the phrasings above and lists any sentence it didn't understand.

### Test data

- **Inline in the web UI**, or upload a `.json` file.
- **CLI:** `--data file.json`, `--data '{"k":"v"}'` or `--data "k=v;k2=v2"`.
- Reference values as `${path.to.value}` or `{{name}}`. List indexes work too (`${users[0].email}`).
- **Data-driven runs:** `{"env": "qa", "rows": [{"user": "a"}, {"user": "b"}]}` runs once per row, and each row gets its own report.
- Generated scripts keep the placeholders, so the same script works with new data.
- Values typed into password, secret or token fields are masked in live events and in the report.

## Remote agents: run on your machine or any machine

The hub (`python -m aiauto server`) holds the UI, the parser and all artifacts. An agent is a small process that opens an **outbound** WebSocket to the hub, so it works behind NAT and firewalls. It runs jobs with its own local browsers, streams step results live and uploads the artifacts when each run finishes.

```bash
# on the hub
AIAUTO_AGENT_TOKEN=change-me python -m aiauto server --host 0.0.0.0 --port 8000

# on any machine with browsers
pip install -r requirements.txt && playwright install chromium
python -m aiauto agent --server http://HUB_HOST:8000 --name my-laptop --token change-me
```

In the UI, pick the target under **Run on**: *This server*, *Any agent* (least busy) or a named agent. Over the API, pass `"agent": "my-laptop"` (or `"any"` / `"local"`). Agents can run headed (untick *Headless*), so you can watch the browser on that machine. The agent reconnects by itself if the hub restarts.

## Replay and self-healing

Every run saves `script.json` with the locator chosen for each step, plus up to 3 fallback locators. A replay:

1. tries the stored locator, then each fallback in turn;
2. if all of them fail (the page changed), re-identifies the element from the step's English description (Claude helps when the match is ambiguous), and marks the step **self-healed** in the report;
3. writes a new `script.json` with the repaired locators.

Pass `--no-heal` (CLI) or `"config": {"self_heal": false}` (API) to fail strictly instead.

## REST API

| Method & path | Purpose |
|---|---|
| `POST /api/parse` `{text, data?, use_llm?}` | English → steps, without running |
| `POST /api/runs` `{text \| steps \| replay_run_id, data?, config?, agent?}` | Start a run. Returns `run_ids` (one per data row) |
| `GET /api/runs`, `GET /api/runs/{id}` | List runs, or get one summary |
| `GET /api/runs/{id}/script` | The replayable `script.json` |
| `GET /api/runs/{id}/download` | All artifacts as a zip |
| `GET /artifacts/{id}/{file}` | One artifact (report, video, scripts…) |
| `DELETE /api/runs/{id}` | Delete a run |
| `GET /api/agents` | Connected agents |
| `WS /ws/ui` | Live events: `run_started`, `step_started`, `healing`, `step_finished`, `run_finished`, `agents_changed` |
| `WS /ws/agent` | Agent connection |

`config` fields: `engine` (`playwright`/`selenium`), `browser` (`chromium`, `firefox`, `webkit`, `chrome`, `edge`), `headless`, `record_video`, `screenshot_each_step`, `timeout_ms`, `viewport_width`/`viewport_height`, `use_llm`, `self_heal`.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Turns on Claude. `ant auth login` profiles work too |
| `AIAUTO_MODEL` | `claude-opus-5-5` | Model used for parsing and disambiguation |
| `AIAUTO_EFFORT` | `low` | Reasoning effort for those calls |
| `AIAUTO_DISABLE_LLM` | – | Force rules-only mode |
| `AIAUTO_ARTIFACTS` | `artifacts` | Where the hub and CLI store runs |
| `AIAUTO_AGENT_TOKEN` | – | Shared secret agents must present |
| `AIAUTO_LOCAL_WORKERS` | `2` | Parallel runs on the hub itself |
| `AIAUTO_CHROME_BINARY` | – | Use a specific Chrome/Chromium binary |
| `AIAUTO_CHROMEDRIVER` | – | Use a specific chromedriver (Selenium) |

Claude calls use structured outputs, so the step list always matches the schema. They also opt in to server-side refusal fallbacks (`fallbacks: "default"`). Any API error, refusal or missing key falls back to the rule parser and heuristic matching, so a run never depends on the model being reachable.

## Project layout

```
aiauto/
  nlp/rules.py         rule-based English → steps
  nlp/llm.py           Claude: intent/value extraction + element disambiguation
  nlp/parser.py        picks LLM or rules
  locators.py          DOM snapshot JS, candidate scoring, locator ranking
  engines/             Playwright and Selenium behind one interface (+ frame-based video fallback)
  runner.py            executes steps, self-heals, writes artifacts
  scriptgen.py         Playwright / Selenium script generation
  report.py            HTML report with embedded screenshots + video
  server/app.py        FastAPI web app, REST API, agent hub
  server/static/       web UI
  agent.py             remote agent
  cli.py               CLI entry (python -m aiauto …)
examples/              demo page, sample test and data
tests/                 parser, LLM (mocked), end-to-end on both engines, generated scripts, self-healing
```

## Tests

```bash
pip install pytest
pytest -q
```

## Known limits

- Elements inside iframes and closed shadow roots are not identified yet.
- Selenium video is built from frames captured after each action, not a continuous recording. Playwright records natively, and falls back to the same frame method when its ffmpeg build is missing.
- The hub keeps queued and running jobs in memory. Finished runs live on disk.
- The hub has no user authentication. Agents are protected by `AIAUTO_AGENT_TOKEN`, but put the hub behind your SSO or a reverse proxy before you expose it beyond a trusted network.
