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
# AI (optional): uses your logged-in Claude Code / Cursor / Copilot CLI by default; or ANTHROPIC_API_KEY, or LiteLLM

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

Negative checks: `Verify page does not contain "Account created"`, `I should not see "Error"`, `Verify "Delete" is not visible`,
or for one element `Verify the Username field does not contain "…"`. They wait a moment (`AIAUTO_NO_TEXT_SETTLE`, 2 s) so a late
message still fails the step.

## Where AI is used

| Stage | With AI (any provider below) | Without it |
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

The AI status chip at the bottom of the sidebar shows which provider is in use; click it for **Settings → AI**.

### AI providers: your subscription by default, a key, or any model

| Provider | What it uses | Setup |
|---|---|---|
| **Claude subscription** (default when found) | Claude Code CLI in headless mode (`claude -p --json-schema`): the plan you are logged into | install Claude Code, run `claude` once to log in |
| **Cursor subscription** | Cursor CLI (`cursor-agent -p --output-format json`) | install the Cursor CLI, `cursor-agent login` |
| **GitHub Copilot subscription** | Copilot CLI (`copilot -p`) | `npm i -g @github/copilot`, log in |
| **Anthropic API key** | Anthropic SDK, structured outputs, server-side refusal fallback | key in Settings, or `ANTHROPIC_API_KEY` |
| **Any model via LiteLLM** | `litellm.completion` (OpenAI, Gemini, Azure, Bedrock, Mistral, Ollama, a LiteLLM proxy …) | model (`openai/gpt-4o`, `gemini/gemini-2.5-pro` …), key, optional base URL |

**Auto** (the default) tries them in that order. One that is not installed, not logged in, out of credits or
rejects the key is skipped for the rest of the session and the next one answers; with none, tests still run on
the rule parser and heuristics. **Model** can be anything the provider offers (default `claude-opus-5-5`).
Settings → AI has **Test connection**, which makes one small real call. Keys live only on the hub and are never
sent back to the browser. Remote machines use their own CLI login or env key, unless you allow sending keys with
each run. Environment variables: `AIAUTO_AI_PROVIDER`, `AIAUTO_MODEL`, `AIAUTO_LITELLM_MODEL`, `AIAUTO_LITELLM_BASE`,
`AIAUTO_LITELLM_KEY`, `ANTHROPIC_API_KEY`.

Subscription mode drives the official CLI that you installed and logged into on your own machine. If you build
a product on this for other people, use API keys (or LiteLLM) instead: Anthropic doesn't let third-party products
offer Claude.ai login without approval.

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
| **Canvas / pixels only** | The AI locates it on a screenshot and the engine acts at that position (any AI provider; replays look again). Without AI the error says it's a canvas | both |

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

## Accounts: a shared platform for a team

Host the hub once (a server or VM, or Docker) and everyone signs in from their browser. It works the same
on `localhost` for one person.

* **Sign-in**: built-in accounts (username + password, scrypt-hashed, with a lockout after repeated failures)
  and/or **company SSO** over OpenID Connect: Okta, Microsoft Entra ID (Azure AD), Google Workspace, Keycloak,
  Auth0 and so on (Admin → Sign-in). SSO tokens are signature-checked against the provider's keys. You can limit
  sign-in to e-mail domains, and accounts are created on first sign-in (or only for people an admin added, matched
  by e-mail).
* **First start**: the log prints a one-time **setup code**; the first visitor enters it and becomes admin. Or set
  `AIAUTO_ADMIN_USER` / `AIAUTO_ADMIN_PASSWORD` (Docker).
* **Roles**: **admin** (users, sign-in, organisation AI, machines, grids, agent tokens) · **tester** (create,
  run, replay, library, their own saved logins) · **viewer** (runs, reports, videos; read-only). Changing a
  role or password, or disabling someone, signs them out at once.
* **Ownership**: every run, library script and saved login records who made it, and the dashboard has an
  *Everyone / Mine* filter. The team sees all runs, but only the owner (or an admin) can delete or stop them,
  and a replay reuses the stored secrets only for the owner. **Saved logins (sessions) are private**: they
  hold cookies.
* **Projects**: each team's runs, reports, videos, files, library scripts, saved logins and live events stay in
  its project. Members see only their projects (plus **Default**, which is open to everyone), admins see all, and
  links to another project's run or report return "not found". Switch projects at the top of the sidebar; admins
  manage projects and members in Admin → Projects. Deleting a project moves its work to Default. Machines, grids
  and the organisation AI are shared.
* **Each person's own AI (My AI)**: everyone can tie their own subscription or key to their account. Claude
  (`claude setup-token`), GitHub Copilot (a GitHub token with Copilot access), Cursor (API key), an Anthropic
  key, or any model via LiteLLM. It is used for **their** runs only, on whatever machine the run lands on:
  agents send AI calls back to the hub, which answers with the run owner's settings. The subscription CLI runs
  with that person's token in a private profile, so nobody's login is used for anyone else, and the server's
  own keys and logins are never used for them. People who haven't set anything use the **organisation AI** the
  admin configured (a company key or gateway), unless the admin turns that off.
* **Secrets at rest**: AI tokens and keys, the SSO client secret and replay secrets are encrypted (Fernet) with
  `AIAUTO_SECRET_KEY` or a generated key file next to the database. Keep that key safe and back it up with the
  database.
* **Agent tokens**: per machine (Machines → Agent tokens), shown once, stored as a hash, revocable. Revoking a
  token disconnects that machine. With accounts on, agents must present a token (a per-machine one, or the
  shared `AIAUTO_AGENT_TOKEN`).
* **HTTPS**: `DOMAIN=autopilot.example.com docker compose --profile https up -d` puts Caddy in front of the hub
  with an automatic Let's Encrypt certificate. Behind any proxy, cookies are marked `Secure` when the request
  arrived over HTTPS. Set `AIAUTO_PUBLIC_URL=https://autopilot.example.com` so SSO redirects use it.
* **Single user**: `AIAUTO_AUTH=off` turns accounts off (no sign-in), as before.
* Writes need the `X-Requested-With: autopilot` header (the web app sends it), so another website can't make
  your browser change things.

## Requirements → test cases → runs → results back (Jira, Confluence, GitHub, Zephyr Scale)

Before a test exists, Autopilot can write it from the story. **Requirements** (left menu) searches your tracker; pick a story or bug and
Autopilot gathers its *context pack*:

- **Jira**: summary, description, acceptance criteria (a custom field, a field named like "Acceptance criteria", the description's
  section, or Given/When/Then lines), steps to reproduce / expected / actual for bugs, links, the last comments.
- **Confluence**: pages linked from the story, then pages that mention its key or title in the configured spaces.
- **GitHub**: pull requests that mention the key; their diff and the changed UI files, read for form fields (label, `required`,
  `type`, `maxlength`/`minlength`, `min`/`max`, `pattern`, zod/yup rules), buttons and routes.

**Design test cases** then writes positive, negative, boundary, role and (for bugs) regression cases **with rules only — no AI needed**:
required fields left empty, invalid formats, lengths and numbers just inside and outside each limit, the error message the criteria
quote, and "the form is not accepted" checks. Every case is plain English in the same format Studio runs, traces back to its acceptance
criterion, and the coverage table shows criteria that still lack a positive or a negative case. Tick **AI ideas** to also ask your AI for
cases the rules can't see (business rules, edge cases); those are marked `ai`.

Review → edit → **Approve** → **Push to test tool** (or just **Run**, which pushes first) → the runs use the machine, engine and options of
the top bar. When they finish:

| Where | What is written |
|---|---|
| Zephyr Scale | test cases (with steps, folder, linked to the story), one test cycle per launch, one execution per run with step results |
| Xray *(under development)* | one Test Execution per launch; each case's Xray test is created on its first import (steps, linked to the story, label `autopilot-case-<id>`) and reused afterwards |
| Azure DevOps *(under development)* | an automated test run per launch with one result per case; comment, state change and de-duplicated bugs (screenshot attached) on the work item |
| Custom tool *(under development)* | JSON events to your results URL: `test_case`, `result`, `comment` |
| Jira | one comment on the story with every result and report link; the pass / fail transition you configured; for each new failure a bug (linked, screenshot attached) — the same failure again adds a comment to that bug instead of a new one |

Everything sent is listed in the design's **sync log**. **Re-check story** says when the story, its pages or its code changed after the
cases were written.

### No tracker yet? Paste the story, work locally, Publish later

- **Paste a story** (Requirements): Markdown, Jira wiki markup or plain text — criteria under an "Acceptance criteria" heading, as
  bullets, or as Given/When/Then; bugs with "Steps to reproduce" / "Expected result". Add its key if it has one, and any spec text.
- **Local Git repository** (Admin → Integrations) as the code source instead of the GitHub API: the folder of a clone **on the hub
  machine** (in Docker, mount it: `-v /home/me/src/shop:/repos/shop`). No token is stored: `git` uses the credentials that clone
  already has (credential helper, SSH key), and only when *git fetch before reading* is on. Autopilot reads the commits on any branch
  that mention the key, their diffs and the UI files they touch — or, without a key, the UI files that best match the story's words.
- Design, approve, run: a pasted story's results **stay on the hub** (the design shows *local · N results not published*).
- When the tools are connected: **Link** the design to the story's Jira key (optional; needed for the Jira comment, status and bugs),
  then **Publish** — the approved cases go to Zephyr Scale, the latest result of each becomes one test cycle, and the story gets its
  comment, transition and bugs. Publish can be pressed again later to send newer results.
- **Send results after each run** (switch on the design): on for stories pulled from the tracker, off for pasted ones; turn it on
  once a pasted story is linked and its results go out as each run finishes. The sync log shows who did each step.

### Connecting (tokens only)

Admin → **Integrations**, per project. Every tool is connected with a token; there is no OAuth app to register.

| Tool | Credential |
|---|---|
| Jira / Confluence **Cloud** | your Atlassian e-mail + an **API token** (id.atlassian.com → Security → API tokens), sent as Basic auth |
| Jira / Confluence **Data Center** | a **personal access token** (profile → Personal access tokens), sent as Bearer |
| GitHub (or Enterprise) | a fine-grained **personal access token**: read access to Contents and Pull requests |
| Local Git repository | none stored — the clone's own git credentials, used only for an optional fetch |
| Zephyr Scale (Cloud) | a Zephyr **API access token** (Jira → Apps → Zephyr → API Access Tokens), sent as Bearer |
| Xray Cloud | an **API key** (client id + client secret, Xray → Global Settings → API Keys); exchanged for a 24-hour token automatically |
| Azure DevOps *(under development)* | a **personal access token** (Work Items read/write, Test Management read/write) |
| Custom *(under development)* | optional token header for your own JSON endpoints |

Tokens are encrypted on the hub, never returned to the browser (shown as `••••••••`), and only admins can change them. Give each
the least access it needs. Planned: GitHub Issues, Zephyr Squad, TestRail, OpenText ALM / Octane, GitLab, Bitbucket.
Set `AIAUTO_PUBLIC_URL` so links in Jira comments and bugs point at your hub.

Try it without real tools: `python -m tests.fake_tools 8765` serves fake Jira/Confluence/GitHub/Zephyr/Xray (it prints the URLs and
tokens) and `examples/signup/signup.html` is the app under test — it has one deliberate bug (age 100 is accepted) for the flow to find.

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

### Stopping runs

Every queued or running run has a **Stop** button (dashboard, Runs table, live view, run details; **Stop all**
for everything running). A queued run is removed from the queue. A running one stops after its current step (a
`Wait` or manual `Pause` is cut short), closes its browser, keeps its screenshots, video and report, and is
marked **cancelled**. Its machine slot frees up for the next run. This works on the hub, on agents and on grids.
API: `POST /api/runs/{id}/cancel` or `POST /api/runs/cancel {"run_ids": [...]} | {"batch_id": "..."}`.

### Windows, macOS and cloud browsers (grids)

Three ways to run on Windows / macOS:

1. **An agent on the machine itself**: `python -m aiauto agent --server http://HUB:8000 --slots 2` on any
   Windows or Mac computer (everything is cross-platform; Safari works through Selenium on a Mac after
   `sudo safaridriver --enable`).
2. **A remote browser grid** (Machines → *Remote browser grids*): your own Selenium Grid with Windows/Mac nodes,
   BrowserStack, Sauce Labs, LambdaTest, or a Playwright browser server (`ws://`) or DevTools endpoint
   (`http://`). A grid shows up in the machine picker like a machine, with **slots** = how many sessions to open
   at once. The hub drives the remote browser, so live view, highlight, screenshots, video, report and generated
   scripts are the same as for a local run. Credentials in the URL are stored on the hub and shown masked.
   Exported scripts run on a grid with `REMOTE_URL=… REMOTE_CAPS='{"platformName": "Windows 11"}' python tests/test_selenium.py`.
3. **CI on real Windows / macOS**: `.github/workflows/cross-platform.yml` runs the browser matrix (including
   Safari on macOS and Edge on Windows), the agent, parallel and cancel tests on `windows-latest`, `macos-latest`
   and `ubuntu-latest`.

Docker cannot run Windows or macOS here. Windows containers need a Windows host. Projects that boot Windows or
macOS as a VM inside a container need KVM and a Windows licence, and Apple's licence allows macOS only on Apple
hardware. For Windows/Mac coverage, use options 1–3.

### Docker

One image runs either role. It is based on Microsoft's Playwright image and contains **every browser both
engines can drive on Linux**, with matching drivers: Playwright: Chromium, Google Chrome, Microsoft Edge, Firefox,
WebKit. Selenium: Chromium, Google Chrome, Microsoft Edge, Firefox. It also has Xvfb for headed runs. All nine
combinations, plus headed mode, are tested inside the image by `tests/test_browsers.py`:

```bash
docker run --rm --shm-size=2g -v "$PWD:/src" -w /src autopilot sh -c "pip install pytest && pytest tests/test_browsers.py"
```


```bash
docker build -t autopilot .
docker run -d -p 8000:8000 -v autopilot-data:/data -e AIAUTO_AGENT_TOKEN=change-me autopilot hub
docker run -d --shm-size=2g autopilot agent --server http://HUB:8000 --name docker-1 --token change-me --slots 2
docker compose up --build --scale agent=3      # hub + 3 agents with 2 slots each = 6 tests at once
```

* **Headed runs inside the container** open a real, full browser window, not the headless build. A container
  has no monitor, so the window is drawn on a virtual screen (Xvfb, started by the entrypoint). You watch it
  through the live view, screenshots and video. To watch a headed browser on your own monitor, run an agent on
  your own computer instead.
* **Your subscription inside Docker.** Two ways:
  1. **Agents use the hub's AI (no setup).** An agent without AI of its own, such as any Docker agent, sends its
     AI calls to the hub. The hub answers them with its own provider, for example the Claude / Cursor / Copilot
     CLI you are logged into on the computer the hub runs on. Tie-breaks between elements and canvas
     screenshots go over the agent's existing connection and token, and English → steps already runs on the hub.
     A machine that has its own logged-in CLI or key uses that first.
  2. **The CLIs in the image (hub in Docker).** Build with `--build-arg AI_CLIS=1` (adds Node, Claude Code and the
     Copilot CLI), then log in with a token instead of a browser:
     * Claude: run `claude setup-token` once on your computer (needs a Claude subscription) and pass the token
       as `-e CLAUDE_CODE_OAUTH_TOKEN=…`.
     * Copilot: pass a GitHub token that is allowed to use Copilot as `-e COPILOT_GITHUB_TOKEN=…` (or `GH_TOKEN`).
     * Cursor: install its CLI in your own image layer (`curl https://cursor.com/install -fsS | bash`) and pass a
       Cursor API key as `-e CURSOR_API_KEY=…`.
     Or mount an existing Linux login: `-v ~/.claude:/root/.claude` (on macOS the Claude login is in the Keychain,
     so use the token).
  An API key or LiteLLM also works (Settings → AI, or `-e ANTHROPIC_API_KEY=…`). Keys are sent to agents with
  jobs only if you allow it.
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
| `AIAUTO_AUTH` | `on` | `off` = single user, no sign-in |
| `AIAUTO_ADMIN_USER` / `AIAUTO_ADMIN_PASSWORD` | – | Create the first admin at startup |
| `AIAUTO_SECRET_KEY` | generated file | Key for secrets at rest |
| `AIAUTO_PUBLIC_URL` | – | External URL (SSO redirects) |
| `AIAUTO_SESSION_HOURS` | `168` | How long a sign-in lasts |
| `AIAUTO_AI_PROVIDER` | `auto` | `auto`, `claude-code`, `cursor`, `copilot`, `anthropic`, `litellm` (also in Settings → AI) |
| `AIAUTO_MODEL` | `claude-opus-5-5` | Model for the chosen provider |
| `ANTHROPIC_API_KEY` | – | Anthropic API key (`ant auth login` profiles work too) |
| `AIAUTO_MODEL` / `AIAUTO_EFFORT` | `claude-opus-5-5` / `low` | Model and effort for parsing and disambiguation |
| `AIAUTO_DB` | `aiauto.db` | SQLite database of the hub |
| `AIAUTO_ARTIFACTS` | `artifacts` | Run folders |
| `AIAUTO_AGENT_TOKEN` | – | Shared secret machines must present |
| `AIAUTO_LOCAL_NAME` | `local` | Display name of the hub's own runner |
| `AIAUTO_LOCAL_WORKERS` | `2` | Parallel runs on the hub |
| `AIAUTO_CHROME_BINARY` / `AIAUTO_CHROMEDRIVER` | – | Specific browser binary / driver |
| `AIAUTO_SECRET_<KEY>` | – | Value for a sensitive data key on the executing machine |

### Real-site check

`examples/real_sites/` holds eight scenarios on public practice sites built for automation (SauceDemo,
the-internet, UI Testing Playground, DemoQA, Selenium's shadow-root page, Books to Scrape, TodoMVC): iframes,
nested frames, shadow DOM, alerts and confirms, dynamic loading, AJAX waits, hovers, drag and drop, tables,
custom-skinned selects, unlabelled checkboxes, shuffled look-alike buttons, non-breaking spaces and data-driven
iterations. `python tools/real_sites.py` runs each on Playwright and Selenium through a hub, replays every
generated script, runs every exported project on its own, and writes `REPORT.md` with the timings, element-finding
times, video sizes, screenshots and live-view frames per run.

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
