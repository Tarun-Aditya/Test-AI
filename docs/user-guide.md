# User guide

How to use Autopilot day to day. New here? Start with [Getting started](getting-started.md).

- [The pages](#the-pages)
- [Writing tests](#writing-tests)
- [Test data, iterations and secrets](#test-data-iterations-and-secrets)
- [Running: engines, browsers, machines, options](#running-engines-browsers-machines-options)
- [Reading a run](#reading-a-run)
- [Replay, self-healing and the library](#replay-self-healing-and-the-library)
- [Generated scripts and exported projects](#generated-scripts-and-exported-projects)
- [Logins, SSO and browser profiles](#logins-sso-and-browser-profiles)
- [Accounts, roles and projects](#accounts-roles-and-projects)
- [How elements are found](#how-elements-are-found)
- [The command line](#the-command-line)

## The pages

| Page | What it's for |
|---|---|
| **Overview** | Pass rate, runs in progress with live progress, a 14-day trend, recent runs, coverage of each story. *Everyone / Mine* filter |
| **Test design** | Stories and bugs → test cases → runs → results back to your tools ([Integrations](integrations.md)) |
| **Studio** | Write a test (or pick one from the library), set its data and options, run it and watch it live |
| **Runs** | Every run, grouped by launch, with filters. Open one for its steps, video, report, scripts, replay and export |
| **Library** | Saved and uploaded scripts. Run them on any engine, browser or machine |
| **Saved logins** | Cookies and local storage a run can start with (skip the login, or SSO) |
| **Machines** | Computers and browser grids that run tests, their slots, and agent tokens |
| **Admin** | Organisation AI, integrations, sign-in (SSO), users, projects (admins only) |
| **My AI** (user menu) | Your own AI subscription or key, used for your runs |

The top bar always shows the project, engine, machine and visible/headless browser. Each run carries a badge:
**AI** (the AI wrote the steps), **Rules** (the built-in parser did), **Edited** (you changed the steps) or **Script**
(a replay).

## Writing tests

Write the test the way you'd describe it to a colleague. Either free-form:

```
Go to ${base_url}/index.html, search for a ${product} and open the first ${brand} result.
Add ${qty} of them to the cart, then go to the cart and make sure it shows ${brand}.
```

or one step per line:

```
Open https://www.saucedemo.com/
Type "standard_user" into Username
Type ${password} into Password
Click Login
Click the first "Add to cart" button
Click the shopping cart link
Verify page contains "Your Cart"
```

**Break down into steps** shows how the text was understood: one row per step with the phrase it came from and the
value it will use with your data. You can edit steps before running.

What you can say:

| Kind | Examples |
|---|---|
| Navigate | `Open <url>`, `Go back`, `Refresh`, `Switch to the new tab` |
| Click | `Click Login`, `Click the "Pay now" button`, `Double-click the row`, `Right-click the file` |
| Type | `Type "x" into Email`, `Enter ${password} in password`, `Append " more" to Notes`, `Press Enter` |
| Choose | `Select "India" from Country`, `Check Remember me`, `Uncheck checkbox 2`, `Upload "resume.txt" to Resume` |
| Pointer | `Hover over Products`, `Drag box A to box B`, `Scroll to the footer` |
| Which one | `the first / second / last ...`, `checkbox 1`, `the View button for order 1002`, `the first result containing "Logitech"` |
| Check | `Verify page contains "..."`, `I should see "..."`, `Verify "Delete" is visible`, `Verify the URL contains "/cart"`, `Verify the title is "..."` |
| Negative check | `Verify page does not contain "Error"`, `I should not see "Account created"`, `Verify the Username field does not contain "..."` |
| Dialogs | `Accept the next dialog`, `Dismiss the next dialog`, `Verify the dialog says "Are you sure?"` |
| Waiting | `Wait for "Hello World!" to appear`, `Wait 2 seconds`, `Pause 90 seconds for SSO login` |
| Optional | `Click Accept cookies if shown`: skipped when it isn't there |

Negative checks wait a moment (2 s, `AIAUTO_NO_TEXT_SETTLE`) so a late message still fails the step.
Put values (text to type, choose, upload or check for) in quotes; element names need none (`Click Add to Cart`).
`${name}` placeholders work with or without quotes. Apostrophes inside a word (`"Let's go"`, `O'Brien`) are fine.
With AI on, looser wording works too; the table above is what the built-in rules understand with AI off.

## Test data, iterations and secrets

- **`${name}` is a placeholder**, replaced by the value called *name* under **Test data**. Nested keys work:
  `${user.email}`.
- **Single run**: a key/value table (dotted keys for nesting) or JSON. **Keys from test** adds a row per placeholder.
- **Iterations**: a grid where each enabled row is its own run with its own data and **browser**. Shared values apply
  to every row. **Columns from test** adds a column per placeholder. **Template (CSV)** downloads the grid; **Upload CSV
  / JSON** loads it back. In a CSV header, `password [secret]` (or `password*`) marks a secret column, and optional
  `run` (yes/no) and `browser` columns set each row. In JSON: `{"rows": [{..., "__browser": "firefox"}, ...]}`.
- **Secrets** (lock icon, `{"value": "...", "sensitive": true}`, or `--sensitive key` on the CLI) are masked
  everywhere: live view, screenshots and video (the field shows dots), reports, `data.json`, `script.json`, generated
  scripts. The hub keeps the real values encrypted so **Replay with the same data** works. Scripts read them from
  `AIAUTO_SECRET_<KEY>` (e.g. `AIAUTO_SECRET_USER_PASSWORD`). Secrets are never stored in your browser.
- Leave the test name empty and it's named from what it does, with the run's values
  (`Search "wireless mouse" → first Logitech result → Cart · #1 Logitech, qty 2`).

## Running: engines, browsers, machines, options

| Choice | Options |
|---|---|
| **Engine** | Playwright, Selenium, or **Both** (every run once per engine, side by side) |
| **Browser** | Chromium, Chrome, Edge, Firefox, WebKit (Playwright), Safari (Selenium on a Mac) |
| **Visible or headless** | Visible opens a real window on the machine that runs it (on a server, a virtual screen) |
| **Machine** | The hub (`local`), any agent or grid, several at once: **Run on each** or **Share as pool**, or **Any free machine** |
| **Options** (Studio → Settings) | Step timeout, video, full-page screenshots, highlight, self-heal, answer to dialogs, viewport, ambiguity policy |

One click can fan out: 3 iterations × 2 engines × 2 machines = 12 runs, in parallel as far as the machines' slots
allow. The rest wait as *queued*.

**Stop** is on every queued or running run (and **Stop all**). A queued run leaves the queue; a running one stops
after its current step, closes its browser, keeps its screenshots, video and report, and is marked *cancelled*.

## Reading a run

Open a run from **Runs** or **Overview**:

- **Overview**: counts, then every step with its screenshot, the native locator, how it was found
  (`element: heuristic / AI / script locator / fallback`), who wrote the step (`step: AI / rules / edited`), any dialog
  it answered, and the error for a failed step.
- **Code & files**: the run folder: file tree, search, syntax-highlighted scripts.
- **Video**, **Report** (a single HTML file with the screenshots and video embedded; share it as is).
- **Data & config**: the data used (secrets masked) and every option.
- **Replay**, **Export project**, **Save to library**, **Delete**.

The highlight boxes: dashed green *"Step N · click"* when the element is found, solid green *"✓ Step N · clicked"*
after the action (this is what the step screenshot shows), red when it fails. They are overlays: they never change the
page, its text or its locators.

## Replay, self-healing and the library

**Replay** runs the run's generated script in **Script mode**: no parsing, the stored native locators, so it is fast
and needs no AI. Choose the same data (secrets included), edited or new JSON data, or Studio's data (every Iterations
row, each with its browser), and any machine, engine, browser and visible/headless.

If a stored locator no longer matches, **self-healing** tries the fallbacks, then finds the element again from the
step's description, marks the step *self-healed* and writes a new script with the repaired locator. Turn it off in
Replay to make broken locators fail instead.

**Library**: **Save to library** keeps a run's script under a name; you can also upload a `script.json` or generated
`.py`. Library scripts run in Studio or from the API (`"library": "name"`).

## Generated scripts and exported projects

Every run writes `test_playwright.py`, `test_selenium.py` and `autopilot_runtime.py`: plain Playwright / Selenium code
with native locators (`get_by_role`, `get_by_label`, `frame_locator`, … / `By.ID`, `By.LINK_TEXT`, `switch_to.frame`,
`shadow_root`, …) and a small standalone runtime. No AI, no self-healing, no dependency on the hub. Running one gives
the same output as a run in the app: highlight boxes, a screenshot per step, a video, `report.html`, `summary.json` and
`junit.xml`.

```bash
python test_selenium.py new_data.json                   # other data; secrets from AIAUTO_SECRET_<KEY>
RESULTS_DIR=out BROWSER=chrome HEADLESS=0 python test_playwright.py
REMOTE_URL=http://grid:4444 REMOTE_CAPS='{"platformName": "Windows 11"}' python test_selenium.py
```

Also honoured: `HIGHLIGHT=0`, `NO_VIDEO=1`, `STEP_TIMEOUT` (ms), `STORAGE_STATE`, `CHROME_PROFILE_DIR`.

**Export project** (run details) downloads a runnable project for the run, or for every iteration of its batch:
the scripts and runtime, `config.json`, each iteration's data file, `.env.example` for the secrets, the original
instructions, `run.sh` / `run.bat` and a README. `run.py` writes `results/iteration_N/` and a `results/index.html`
linking them all.

```bash
cd my-test && pip install -r requirements.txt && python -m playwright install chromium
cp .env.example .env            # fill in the masked secrets
python run.py                   # every iteration with its recorded engine and browser
python run.py --engine selenium --browser firefox --headed --only 2
```

Exported projects drop into any CI: they exit non-zero on failure and write JUnit XML.

## Logins, SSO and browser profiles

- **Saved logins** (cookies + local storage, Playwright `storage_state` format) work in both engines on any machine.
  Capture one by running visible with `Pause 90 seconds for SSO login` and *Save session after run as*, or import a
  `storage_state.json`. Later runs use *Start with saved session*. Saved logins are private to their owner.
- **Chrome profile**: point at a user-data-dir (and profile name) on the machine that runs the test to reuse its saved
  passwords and signed-in sites. Chrome must not already be open on that profile, and recent Chrome refuses to automate
  its *default* profile folder, so make a dedicated automation profile.

## Accounts, roles and projects

- **Sign-in**: built-in accounts and/or company SSO over OpenID Connect (Okta, Microsoft Entra ID, Google Workspace,
  Keycloak, Auth0, …) under Admin → Sign-in. You can restrict SSO to e-mail domains and create accounts on first
  sign-in, or only for people an admin added.
- **Roles**: **admin** (users, sign-in, AI, integrations, machines, projects) · **tester** (create, run, replay, library,
  their own saved logins) · **viewer** (runs, reports, videos; read-only). Changing someone's role or password, or
  disabling them, signs them out at once.
- **Ownership**: runs, library scripts and saved logins record who made them. Everyone in a project sees its runs; only
  the owner or an admin can delete or stop them, and a replay reuses stored secrets only for the owner.
- **Projects** keep each team's runs, reports, files, library, saved logins, designs and connections apart. Members
  see their projects plus **Default**; admins see all. Switch at the top of the sidebar. Deleting a project moves its
  work to Default. Machines, grids and the organisation AI are shared.
- **My AI**: anyone can attach their own Claude, Copilot or Cursor subscription, or an API key; it's used for their
  runs only, on whatever machine runs them.

## How elements are found

For each step that targets an element, at the moment it runs (see [Architecture](architecture.md#finding-the-element-for-a-step)
for the full picture):

1. **Candidates** come from the live page: Playwright's accessibility snapshot (real roles and names, through shadow
   DOM and iframes) with a DOM walk for what has no role; for Selenium a DOM walk in one script call, refined with
   Chrome's accessibility tree.
2. **Ranking** scores each on the step's words, the kind of element the step names, whether it fits the action,
   exact names, context ("for order 1002") and ordinals ("the second").
3. A **tie** goes to the next source, then to the AI if configured. Otherwise the step fails and lists the tied
   elements: it never guesses silently (`on_ambiguous`, default `fail`).
4. **Native locators** are built and each is verified to find exactly that element; the best one plus two fallbacks are
   kept, at least one of which Selenium can run.

Handled: open and closed shadow roots at any depth, nested and cross-origin iframes, late and re-rendered elements,
lazy and virtualised lists (scrolled and searched), custom-skinned selects and checkboxes, look-alike buttons with
shuffled classes, labels laid out beside a field, non-breaking spaces, hovers that must stay held while a later check
reads what they revealed, and native alerts (also on Safari). Canvas-only controls need AI (it finds them on a
screenshot). Out of reach: browser plugins and native OS dialogs.

Typical time to find an element: about 0.1 s on a normal page on the first run and 0.01–0.03 s on replay; about
0.4–0.9 s on a very large page (9,000 DOM nodes).

## The command line

```bash
python -m aiauto run -f examples/shop_cart.txt --data examples/shop_data.json --engine selenium
python -m aiauto run -f test.txt --sensitive user.password --headed --save-session corp-sso
python -m aiauto replay artifacts/<run>/test_playwright.py --engine selenium --data new.json
python -m aiauto parse "Open example.com then click More information"
python -m aiauto server --port 8000
python -m aiauto agent --server http://HUB:8000 --name my-laptop --token <token> --slots 2
python -m aiauto demo
```

`run` options: `--data` (file, inline JSON or `k=v;k2=v2`), `--sensitive KEY` (repeatable), `--engine`, `--browser`,
`--headed`, `--no-video`, `--full-page`, `--no-llm`, `--timeout` (ms), `--dialogs accept|dismiss`, `--profile-dir`,
`--profile-name`, `--session`, `--save-session`, `--out`. `replay` adds `--no-heal`. Run folders go to `./artifacts`.
