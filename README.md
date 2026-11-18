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
  * **Columns from test** adds one column per `${…}` placeholder in the test (Single run has **Keys from test**).
  * **Template (CSV)** downloads the grid (or a blank template built from the placeholders) and
    **Upload CSV / JSON** loads it back. A CSV header row lists the keys. `password [secret]` (or `password*`)
    marks a secret column, and the optional `run` (yes/no) and `browser` columns set each row. JSON is a list of rows
    or `{"rows": [...]}`. Secret values are never written into downloaded templates.
* **Replay** (run details) runs the generated script in Script mode with: the **same data** (secrets from the
  hub's vault), **edited / new JSON** data, or **Studio's test data** (the Single-run values or every
  Iterations row, each with its own browser). Secrets left empty keep the original run's secret.
* **Sensitive values** (lock icon, `{"value": "…", "sensitive": true}`, or `--sensitive key`) are masked
  in live events, reports, videos (the field is rendered as dots), `summary.json`, `data.json`, `script.json`
  and generated scripts. Scripts read them from `AIAUTO_SECRET_<KEY>`. The hub keeps them in its local DB
  (file mode 600) so **Replay with the same data** works. Secrets are never stored in the browser.

## How elements are found

For every step that targets an element, at the moment the step runs (the page changes after each action):

| | Playwright | Selenium |
|---|---|---|
| **Candidates** | Playwright's own accessibility snapshot, `page.aria_snapshot(mode="ai")`: roles and names per the ARIA spec, shadow DOM, iframes, each element with a ref and its surrounding context (row, list, section…). A DOM walk is the fallback for elements without an accessible role | DOM walk (`SNAPSHOT_JS` via `execute_script`): shadow roots and same-origin iframes at any depth, dynamic ids and classes ignored |
| **Ranking** | Scores each candidate against the step's words, the action (you type into textboxes…), exact/prefix name matches and context words ("the View button for order 1002"). Ordinals ("the second …") pick by document order | same, then **Chrome's real role and accessible name** (CDP `Accessibility.getPartialAXTree`) for the top 12, and re-rank |
| **Tie** | Two different elements score within 0.12: Claude decides if configured. Otherwise the step **fails and lists both** instead of guessing (`on_ambiguous`, default `fail`) | same |
| **Locator** | `get_by_ref(ref).normalize()`: Playwright's own locator generator (as in codegen), unique by construction, e.g. `get_by_role("button", name="Pay now")` inside `frame_locator` chains. Plus verified engine-neutral fallbacks | Most robust of test-id › id › role+name › label › placeholder › name › text › CSS › XPath, mapped to `By.*` |
| **Verification** | The engine must find exactly one element and it must be the chosen one (primary + 2 fallbacks kept) | same |

### Highlighting

Each element a step acts on gets an overlay box (`config.highlight`, on by default; *Highlight elements* in Studio → Options):
dashed green "Step N · click" when located (held `highlight_ms`, 300 ms, so it shows in the video and live view), solid
green "✓ Step N · clicked" after the action (this is what the step screenshot shows), red "✗ Step N · … failed" when
the action fails. The box is an overlay above the page (not an outline, so it can't be clipped), its label is CSS
content (never part of page text or locators), it works in shadow DOM and iframes, and it's removed after the step.

### Hard cases covered

| Case | How it's handled | Engines |
|---|---|---|
| Open shadow roots, any depth | walked by the snapshot; Playwright locators pierce them, Selenium uses `shadow_root` + CSS | both |
| **Closed** shadow roots | a script injected before page scripts creates shadow roots open (`open_closed_shadow_roots`); generated scripts inject it too | Playwright; Selenium on Chrome/Edge (CDP) |
| Same-origin iframes, nested | walked by the snapshot; `frame_locator` / `switch_to.frame` chains | both |
| **Cross-origin** iframes (payment widgets…) | Playwright's snapshot covers them; Selenium switches into each one and scans it from inside | both |
| Late elements | re-snapshot every 0.5 s until the step timeout | both |
| **Not rendered yet**: virtualised lists, lazy sections | scroll the page, then the largest scrollable container; ordered lists are **binary-searched** by the numbers they show | both |
| Re-rendered elements (stale references) | re-find and retry | Selenium |
| Ties between different elements | Claude decides, otherwise the step **fails and lists them** (never a silent guess) | both |
| Numbers in the step ("order 750") | must match exactly; "order 5" is not a candidate | both |
| Replay fallbacks | a fallback (or structural CSS/XPath) must still match the step's description, so "the second row's button" can't click a different row after re-rendering | both |
| **Canvas / pixels only** | Claude locates it on a screenshot and the engine acts at that position (needs `ANTHROPIC_API_KEY`; replays look again). Without AI the error says it's a canvas | both |

Still out of reach: content inside browser plugins / native OS dialogs, and canvas content when AI is off.

### Time to locate (measured)

Per element step; non-element steps (open, press, wait, page-text checks) don't locate anything.

| Page | Engine | First run (AI mode) | Replay (Script mode) |
|---|---|---|---|
| Small form/app page (~100 DOM nodes) | Playwright | ~0.1 s | ~0.01 s |
|  | Selenium | ~0.09 s | ~0.03 s |
| Very large page (Wikipedia article, ~9,000 DOM nodes) | Playwright | ~0.9 s | ~0.15 s |
|  | Selenium (Chrome) | ~0.35 s | ~0.3 s |

Why Selenium was slow, and what changed: every WebDriver command is an HTTP round trip to the driver, so locating
used to cost ~10 round trips per step (one per locator check, plus frame/shadow switching) and shipped every
candidate on the page as JSON. Now each locator check for the chosen element runs in one in-page call (then a single
native `find_elements` confirms the winner), big pages send back only elements sharing a word with the step, and
video frames are JPEG via CDP. Playwright's per-step cost on huge pages is mostly its accessibility snapshot (~0.5 s).

A 600-step test with ~420 element steps adds roughly **40 s – 6 min** of locating on the first run, depending on
page size, and **~5 s – 2 min** when replaying the generated script. Page loads and the actions come on top.
Claude calls (tie-breaks, canvas) add the API's response time each. Reports and `summary.json` record `locate_ms`
per step and snapshot / rank / verify / scroll-search timings per run.

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

### Parallel runs, several machines, both engines

* **Slots**: `python -m aiauto agent ... --slots 3` lets a machine run 3 tests at the same time (each in its own
  browser; any mix of Playwright and Selenium). The hub itself runs `AIAUTO_LOCAL_WORKERS` (default 2) at once.
  The hub keeps one queue and only sends a machine as many runs as it has free slots; the rest wait (status
  *queued*) and start as soon as a slot frees up. The machine picker and Machines page show `running/slots`.
* **Machine picker** (top bar): tick any number of machines.
  * **Run on each**: every run goes to every selected machine (same test on Windows, Mac and a Docker box at once).
  * **Share as pool**: iterations are spread over the selected machines, first free slot wins.
  * **Any free machine**: the same, over every connected machine.
* **Engine: Both** runs every run once with Playwright and once with Selenium, side by side.
* Combined, one click can fan out: 3 iterations × 2 engines × 2 machines = 12 runs, executed in parallel as far
  as the slots allow. Each run's name says which iteration, engine and machine it is.
* API: `"agent": ["docker-a", "docker-b"], "spread": "each" | "pool", "engines": ["playwright", "selenium"]`.
* A machine that disconnects marks its running tests *interrupted*; queued runs for "any"/pool move to the
  machines still online, and runs pinned to it wait until it reconnects.

### Docker

One image (based on Microsoft's Playwright image: Chromium, Firefox, WebKit, a matching chromedriver for
Selenium, and Xvfb) runs either role:

```bash
docker build -t autopilot .
docker run -d -p 8000:8000 -v autopilot-data:/data -e AIAUTO_AGENT_TOKEN=change-me autopilot hub
docker run -d --shm-size=2g autopilot agent --server http://HUB:8000 --name docker-1 --token change-me --slots 2
docker compose up --build --scale agent=3      # hub + 3 agents with 2 slots each = 6 tests at once
```

* **Headed runs inside the container** go to a virtual screen (Xvfb, started by the entrypoint), so the
  Headless toggle works in both positions. The live view, screenshots and video show that screen.
* The hub keeps its SQLite DB and artifacts in the `/data` volume, so history survives container restarts.
* `--shm-size` matters: Chromium crashes with Docker's default 64 MB `/dev/shm` (about 1 GB per slot).
* Inside Docker the agent has no access to your desktop Chrome profile. Use **saved sessions** for SSO
  (captured once from a headed run on a desktop agent, then usable on every machine).
* Building behind a TLS-inspecting proxy: `docker build --secret id=ca,src=/path/to/ca-bundle.crt -t autopilot .`
  (the CA is used only during the build and is not stored in the image).

## Logins, SSO and Chrome profiles

* **Saved sessions** (cookies + localStorage, Playwright `storage_state` format) work in **both engines** on
  any machine. Capture one by running headed with `Pause 90 seconds for SSO login` and *Save session after
  run as*, or import a `storage_state.json`. Start later runs with *Start with saved session*.
* **Chrome profile**: point at a user-data-dir (+ profile name) on the run machine to reuse saved passwords
  and logged-in sites. Chrome must not already be open on that profile, and recent Chrome versions refuse
  automation of the *default* profile directory, so use a dedicated automation profile.

## Generated scripts are self-sustained

Every run writes `test_playwright.py`, `test_selenium.py` and `autopilot_runtime.py` (a small, standalone
framework: no AI, no self-healing, no dependency on this tool or its server). Running a script produces the
same kind of output as the original run:

* a green **highlight box** on each located element (Playwright and Selenium), red on failure
* a **screenshot per step**, a **video** (Playwright native, or frame capture for Selenium; `imageio-ffmpeg`)
* **report.html** with the screenshots and the video embedded, plus `summary.json` and `junit.xml` for CI
* masking of sensitive values (typed fields are rendered as dots, values hidden in the report and logs)

```bash
python test_selenium.py new_data.json          # new data; secrets from AIAUTO_SECRET_<KEY>
RESULTS_DIR=out BROWSER=chrome HEADLESS=0 python test_playwright.py
```
`HIGHLIGHT=0`, `NO_VIDEO=1` and `STEP_TIMEOUT` are also honoured. Locators whose names carry live counts
("Cart, 0 items") are recorded on the stable part ("Cart"), so scripts keep working when the data changes.

## Exported project

**Export project** (run details) downloads a self-runnable project for the run, or for all iterations of its batch.
It contains everything the first run used: the scripts and their runtime, `config.json`, each iteration's data file,
`.env.example` for the secrets, and the original instructions. `run.py` writes `results/iteration_N/` (report, video,
screenshots, summary, junit) and a `results/index.html` that links them all.

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
