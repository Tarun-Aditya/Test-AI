# Autopilot: AI test automation

Write a browser test in plain English. Autopilot works out the **intent** (click, type, hover,
select, drag…) and the **value** of each step, finds the element on the live page and runs it with
**Playwright or Selenium**, using each engine's *native* locators. A successful run produces a
**generated script**: plain Playwright / Selenium code with no AI involved. From then on you **replay
the script**, on any engine, browser or machine, with the same data or new data.

```
 plain English + test data ──► AI mode run ──► generated script ──► Script mode runs (replay) ──► …
   (web / CLI / API)            intent → native locators            native locators, data-driven
                                video, screenshots, report          engine self-heals if the page changed
                                                                    and every run regenerates the script
```

## What you get from every run

| Artifact | What it is |
|---|---|
| `report.html` | Self-contained report: each step with its screenshot, native locator, fallbacks, dialogs, errors, plus the embedded video |
| `video.webm` | Recording of the run (Playwright natively; Selenium via a frame recorder at about 4 fps) |
| `test_playwright.py` / `test_selenium.py` | Standalone scripts with native locators (`get_by_role`, `get_by_label`, `frame_locator`… / `By.ID`, `By.LINK_TEXT`, `switch_to.frame`, `shadow_root`…). No AI, no self-healing |
| `script.json` | The portable test spec the scripts were generated from (also embedded in each `.py`) |
| `data.json`, `summary.json`, `screenshots/` | Data used (secrets masked), machine-readable results, per-step screenshots |
| **Export project** | A self-runnable Python project: `run.py`, `requirements.txt`, `config.json`, every iteration's data file, `.env.example` for secrets, run scripts and a README |

## Quick start

```bash
pip install -r requirements.txt
python -m playwright install chromium        # Selenium finds or downloads its own driver
export ANTHROPIC_API_KEY=sk-ant-...          # optional: Claude for intent extraction + element disambiguation

python -m aiauto server --port 8000          # web app + machine hub → http://localhost:8000
```

The React UI is pre-built into `aiauto/server/static/app`. To work on it:
`cd web && npm install && npm run dev` (proxies to a hub on :8000), then `npm run build`.

## The web app

* **Dashboard**: pass rate, runs in progress (live progress bars, current step), a 14-day trend, and recent
  runs with thumbnails. Each run shows **AI** or **Script** mode. Click a run for its details;
  every card and row has **Replay**.
* **Studio**: write the test (with a line-numbered editor and examples) or pick a library script. Fill in **test data**
  as a single run, **iterations** (one row per run, each with its own **browser** dropdown) or JSON. Set options,
  login/SSO and profile, then watch the run **live**: browser frames stream from whichever machine runs it,
  with a timeline of steps, their native locators and screenshots.
* **Run details** (drawer): overview with step screenshots · **Code & files** (file tree, go-to-file,
  search in files, syntax-highlighted viewer) · video · report · data & config · **Replay** (same data
  incl. secrets, or new data; pick machine, engine, browser, headless, self-heal) · **Export project**.
* **Runs** (filterable table), **Library** (saved/uploaded scripts), **Machines**, **Sessions**.
* Engine, machine and headed/headless are always in the top bar.

Everything is stored in a local **SQLite** database (`AIAUTO_DB`, default `./aiauto.db`): runs, steps, library,
sessions, registered machines. Restarting the hub keeps all history. Runs that were in flight are marked
*interrupted*, artifact folders without a DB row are imported, and machines show as offline until they
reconnect. Large files stay on disk under `artifacts/`.

## Writing tests: describe them in plain English

Write the test the way you'd describe it to a colleague. No step-by-step format is needed:

```
Go to ${base_url}/index.html, search for a ${product} and open the first ${brand} result.
Add ${qty} of them to the cart, then go to the cart and make sure it shows ${brand}.
```

**Break down into steps** (Studio) shows how it was split into steps. Each step shows the phrase it came from
and the value it will use with your data, and you can edit the steps before running:

| # | step | from your text | with your data |
|---|------|----------------|----------------|
| 1 | navigate `${base_url}/index.html` | | http://shop… |
| 2 | type "search box" ← `${product}` | "search for a ${product}" | wireless mouse |
| 3 | press Enter | "search for a ${product}" | |
| 4 | click the **first** result containing `${brand}` | "open the first ${brand} result" | Logitech |
| 5 | select Quantity ← `${qty}` | "Add ${qty} of them to the cart" | 2 |
| 6 | click Add to Cart button | "Add ${qty} of them to the cart" | |
| 7 | click Cart | "go to the cart" | |
| 8 | verify page contains `${brand}` | "make sure it shows ${brand}" | Logitech |

**`${name}` is a placeholder.** It is replaced by the value called *name* under **Test data**, so the
same test runs with different data (e.g. one iteration with Logitech, another with HP). Leave the test name
empty and it's named after what it does, with the run's real values (`Search "wireless mouse" → first
Logitech result → Cart → check "Logitech" · #1 Logitech, qty 2`).

Step-by-step text works too ("Click the Login button", "Type "x" into Email", …), as do drag, hover,
dialogs, tabs, optional steps ("Click Accept cookies if shown") and pauses for manual SSO login.

## Where AI is used

| Stage | With Claude (`ANTHROPIC_API_KEY` set on the hub) | Without it |
|---|---|---|
| **1. English → steps** | Claude reads the whole description and returns structured steps (action, element description, value, ordinal, optional) | Built-in parser: splits prose into clauses and expands common intents (search, log in, add to cart, go to cart, make sure / should see, set/choose …) |
| **2. Which element on the page** | Candidates are scored by heuristics. **When the top matches are close, Claude picks** | Heuristic scoring only |
| **3. Running the step** | No AI: native Playwright / Selenium calls, each locator verified by the engine | same |
| **4. Self-healing on replay** | Stored locator → fallbacks → re-identify from the description (stage 2, so Claude breaks ties) | same, heuristics only |
| **5. Generated scripts / exported project** | **No AI at all**: plain native code | same |

The UI shows this for every run and step:
* Run badge: **AI** (Claude split the English) · **Rules** (built-in parser, AI off) · **Edited** (you changed the
  steps) · **Script** (replay of a generated script, no parsing).
* Step chips: `step: AI/rules/edited` (who made the step) and `element: AI/heuristic/script locator/fallback`
  (how the element was found). The same chips appear in `report.html`.

The AI status chip at the bottom of the sidebar shows whether Claude is configured on this hub.

### Test data and iterations

* Single run: key/value table (dotted keys for nesting) or JSON. Reference values as `${key}`.
* **Iterations**: a grid where each enabled row is its own run with its own data and **browser**. Shared
  values apply to all rows. In JSON, use `{"rows": [{…, "__browser": "firefox"}, …]}`.
* **Sensitive values** (lock icon, `{"value": "…", "sensitive": true}`, or `--sensitive key`) are masked
  in live events, reports, videos (the field is rendered as dots), `summary.json`, `data.json`, `script.json`
  and generated scripts. Scripts read them from `AIAUTO_SECRET_<KEY>`. The hub keeps them in its local DB
  (file mode 600) so **Replay with the same data** works. Secrets are never stored in the browser.

## How elements are found

1. A DOM snapshot walks the page, **open shadow roots at any depth** and **same-origin iframes at any depth**,
   and lists candidates with their text, ARIA role and accessible name, label, placeholder, test-id and so on.
   Auto-generated ids and classes (`css-8f3k2`, `el-x7p1…`) are ignored.
2. Candidates are scored against the step's words and the action (you type into inputs, select from
   dropdowns…). Ordinals pick the nth match in document order. Claude breaks close ties.
3. Each candidate locator is **verified natively by the engine** (it must match exactly one element, and the
   right one). The first verified one becomes the step's locator, and up to 3 more are kept as fallbacks.

Order of preference: test-id › stable id › role + name › label › placeholder › name › text › alt/title ›
attribute CSS › structural CSS › XPath. Selenium can't evaluate role, label or text inside shadow roots,
so it uses CSS there.

### Self-healing (engine only)

When a Script mode run finds that a stored locator no longer matches, the engine tries the fallbacks, then
re-identifies the element from the step's description, marks the step *self-healed*, and the run writes a
**new script with the repaired locators**. The generated scripts never self-heal: they are plain native code
that either passes or fails.

## Machines (run anywhere)

The hub runs tests itself by default (*local*). Any other computer can register:

```bash
# hub
AIAUTO_AGENT_TOKEN=change-me python -m aiauto server --host 0.0.0.0 --port 8000
# any machine with browsers
pip install -r requirements.txt && python -m playwright install chromium
python -m aiauto agent --server http://HUB:8000 --name my-laptop --token change-me
```

The agent opens an **outbound** WebSocket (so it works behind NAT and firewalls), appears in the machine dropdown,
streams live frames and step results, uploads artifacts when each run ends, and reconnects by itself.

## Logins, SSO and Chrome profiles

* **Saved sessions** (cookies + localStorage, Playwright `storage_state` format) work in **both engines** on
  any machine. Capture one by running headed with `Pause 90 seconds for SSO login` and *Save session after
  run as*, or import a `storage_state.json`. Start later runs with *Start with saved session*.
* **Chrome profile**: point at a user-data-dir (+ profile name) on the run machine to reuse saved passwords
  and logged-in sites. Chrome must not already be open on that profile, and recent Chrome versions refuse
  automation of the *default* profile directory, so use a dedicated automation profile.

## Exported project

**Export project** (run details) downloads a self-runnable project for the run, or for all iterations of its batch:

```bash
cd my-test && pip install -r requirements.txt && python -m playwright install chromium
cp .env.example .env            # fill in masked secrets
python run.py                   # every iteration with its recorded engine / browser
python run.py --engine selenium --browser firefox --headed --only 2
```

## CLI

```bash
python -m aiauto run -f examples/shop_cart.txt --data examples/shop_data.json --engine selenium
python -m aiauto run -f test.txt --sensitive user.password --headed --save-session corp-sso
python -m aiauto replay artifacts/<run>/test_playwright.py --engine selenium --data new.json
python -m aiauto parse "Open example.com then click More information"
```

## REST API (selection)

| Method & path | Purpose |
|---|---|
| `POST /api/runs` | `{text \| library \| replay_run_id, name, data, sensitive, iterations:[{data, browser, engine}], config, agent, reuse_secrets}` |
| `GET /api/runs`, `GET /api/stats` | History (from SQLite), dashboard numbers |
| `GET /api/runs/{id}` | Details: steps, data (masked), config, batch, `has_secrets` |
| `GET /api/runs/{id}/files` · `/export` · `/download` | File tree · runnable project zip · raw artifacts zip |
| `POST /api/parse` | English → steps without running |
| `GET/POST/DELETE /api/library…`, `/api/sessions…`, `GET /api/agents` | Library, sessions, machines |
| `WS /ws/ui` | Live events: `run_started`, `step_started`, `step_finished`, `step_shot`, `live_frame`, `healing`, `paused`, `run_finished` |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Turns on Claude (`ant auth login` profiles work too) |
| `AIAUTO_MODEL` / `AIAUTO_EFFORT` | `claude-opus-5-5` / `low` | Model and effort for parsing and disambiguation |
| `AIAUTO_DB` | `aiauto.db` | SQLite database of the hub |
| `AIAUTO_ARTIFACTS` | `artifacts` | Run folders |
| `AIAUTO_AGENT_TOKEN` | – | Shared secret machines must present |
| `AIAUTO_LOCAL_NAME` | `local` | Display name of the hub's own runner |
| `AIAUTO_LOCAL_WORKERS` | `2` | Parallel runs on the hub |
| `AIAUTO_CHROME_BINARY` / `AIAUTO_CHROMEDRIVER` | – | Specific browser binary / driver |
| `AIAUTO_SECRET_<KEY>` | – | Value for a sensitive data key on the executing machine |

## Examples and tests

* `examples/playground.html`: every element type (nested shadow roots, nested iframes, random ids,
  dialogs, drag and drop, custom dropdown, date/range/color, contenteditable, multi-select, upload, hover menu, late elements).
  `examples/playground_test.txt` exercises all of it in 52 steps.
* `examples/shop/`: a multi-page shop (search → results → product → cart) for `examples/shop_cart.txt`.
* `examples/amazon_cart.txt`: the same flow against amazon.in. Amazon shows an automated-traffic check to
  cloud and data-centre browsers. Run it from your own machine (as an agent, headed, with your profile).

```bash
pip install pytest && pytest -q
```

## Known limits

* Cross-origin iframes and closed shadow roots are not traversed.
* The hub has no user login. Protect agents with `AIAUTO_AGENT_TOKEN`, and put the hub behind your SSO or a reverse
  proxy before exposing it. The SQLite file holds replay secrets: keep it on a trusted disk.
