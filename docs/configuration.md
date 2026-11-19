# Configuration

Everything is set with environment variables (and the web app's Admin pages). Nothing needs a config file. Set a
variable before starting the hub:

```bash
AIAUTO_LOCAL_WORKERS=4 ./start.sh                          # macOS / Linux, one start
export AIAUTO_PUBLIC_URL=https://autopilot.example.com     # macOS / Linux, this terminal
set AIAUTO_LOCAL_WORKERS=4                                 # Windows Command Prompt, this window
$env:AIAUTO_LOCAL_WORKERS = "4"                            # Windows PowerShell, this window
```

In Docker, put them under `environment:` in `docker-compose.yml` or pass `-e NAME=value`.

## Hub

| Variable | Default | Meaning |
|---|---|---|
| `AIAUTO_DB` | `aiauto.db` | SQLite database file ([Database](database.md)) |
| `AIAUTO_ARTIFACTS` | `artifacts` | Folder for run folders (reports, videos, screenshots, scripts) |
| `AIAUTO_LOCAL_WORKERS` | `2` | Tests the hub runs at the same time itself |
| `AIAUTO_LOCAL_NAME` | `local` | Name of the hub's own runner in the machine list |
| `AIAUTO_DEMO_SITE` | `on` | Serve the practice pages (`examples/`) at `/demo/`. `off` removes them |
| `AIAUTO_LIBRARY`, `AIAUTO_SESSIONS` | `library`, `sessions` | Folders of an older version's file-based library and logins, imported once on start |

The port and address are command-line options: `python -m aiauto server --host 0.0.0.0 --port 8000`
(`start.sh` / `start.bat` read `PORT`).

## Accounts and security

| Variable | Default | Meaning |
|---|---|---|
| `AIAUTO_AUTH` | `on` | `off` = single user, no sign-in (only on a machine nobody else can reach) |
| `AIAUTO_ADMIN_USER`, `AIAUTO_ADMIN_PASSWORD` | – | Create this admin on first start instead of using the setup code (password ≥ 10 characters) |
| `AIAUTO_SESSION_HOURS` | `168` | How long a sign-in lasts (7 days) |
| `AIAUTO_PUBLIC_URL` | – | The hub's external URL, e.g. `https://autopilot.example.com`: used for SSO redirects and links in Jira comments and bugs |
| `AIAUTO_SECRET_KEY` | key file | Key that encrypts stored secrets. Without it, a key is generated in `aiauto.key` next to the database. Changing it makes stored tokens unreadable |
| `AIAUTO_AGENT_TOKEN` | – | A shared token every agent may present (per-machine tokens from Machines → Agent tokens are better) |

SSO (OpenID Connect issuer, client id and secret, allowed domains, default role) is set in **Admin → Sign-in**.

## AI

All optional: without AI the built-in rules and heuristics do the work. These can also be set in **Admin →
Organisation AI** (stored in the database, keys encrypted); each person can set their own in **My AI**.

| Variable | Default | Meaning |
|---|---|---|
| `AIAUTO_AI_PROVIDER` | `auto` | `auto` (try each below in order), `claude-code`, `cursor`, `copilot`, `anthropic`, `litellm` |
| `AIAUTO_MODEL` | `claude-opus-5-5` | Model for the chosen provider |
| `AIAUTO_EFFORT` | `low` | Reasoning effort for parsing and tie-breaks (`low` is fast and enough) |
| `AIAUTO_AI_TIMEOUT` | `180` | Seconds to wait for one AI answer |
| `ANTHROPIC_API_KEY` | – | Anthropic API key |
| `AIAUTO_LITELLM_MODEL`, `AIAUTO_LITELLM_BASE`, `AIAUTO_LITELLM_KEY` | – | Any model through LiteLLM (`pip install litellm`): model (`openai/gpt-4o`, `gemini/gemini-2.5-pro`, …), optional base URL (a gateway or proxy), key |
| `AIAUTO_CLAUDE_CLI`, `AIAUTO_CURSOR_CLI`, `AIAUTO_COPILOT_CLI` | on `PATH` | Path to the subscription CLI if it isn't on `PATH` |
| `AIAUTO_DISABLE_LLM` | – | `1` = never use AI, whatever is configured (reproducible CI) |
| `CLAUDE_CODE_OAUTH_TOKEN`, `COPILOT_GITHUB_TOKEN`, `CURSOR_API_KEY` | – | Log the subscription CLIs in without a browser (Docker; see [Machines](machines.md#your-ai-subscription-inside-docker)) |

## Running tests

| Variable | Default | Meaning |
|---|---|---|
| `AIAUTO_NO_TEXT_SETTLE` | `2.0` | Seconds a "does not contain" check waits for a late message |
| `AIAUTO_SECRET_<KEY>` | – | A secret's value on the machine that runs it, e.g. `AIAUTO_SECRET_USER_PASSWORD` for `${user.password}` |
| `AIAUTO_CHROME_BINARY` | Playwright's | A specific Chromium / Chrome to use |
| `AIAUTO_CHROMEDRIVER` | Selenium Manager | A specific chromedriver |
| `AIAUTO_GOOGLE_CHROME`, `AIAUTO_GOOGLE_CHROMEDRIVER` | found | Google Chrome (as opposed to Chromium) and its driver |
| `AIAUTO_EDGE_BINARY`, `AIAUTO_EDGEDRIVER` | found | Microsoft Edge and its driver |
| `AIAUTO_FIREFOX_BINARY`, `AIAUTO_GECKODRIVER` | found | Firefox and geckodriver |

Per-run options (engine, browser, visible/headless, step timeout, video, screenshots, highlight, self-heal, dialogs,
viewport, saved login, Chrome profile, …) are chosen in Studio or sent with the run (`config` in the API). They are
the fields of `RunConfig` in `aiauto/models.py`.

## Agents

| Variable / option | Default | Meaning |
|---|---|---|
| `--server` / `AIAUTO_SERVER` | `http://localhost:8000` | The hub's URL |
| `--token` / `AIAUTO_AGENT_TOKEN` | – | Agent token (Machines → Agent tokens) |
| `--name` | the host name | Name in the machine list |
| `--slots` / `AIAUTO_SLOTS` | `1` | Tests this machine runs at the same time |
| `--out` / `AIAUTO_AGENT_ARTIFACTS` | `agent_artifacts` | Local copies of run folders |

An agent uses its own logged-in AI CLI or `ANTHROPIC_API_KEY` if it has one, otherwise the hub answers its AI calls.

## Generated scripts and exported projects

Read by `test_playwright.py`, `test_selenium.py` and `run.py`, not by the hub:

| Variable | Meaning |
|---|---|
| `BROWSER` | `chromium`, `chrome`, `msedge`, `firefox`, `webkit`, `safari` |
| `HEADLESS` | `1` or `0` |
| `RESULTS_DIR` | Where the report, video, screenshots, `summary.json` and `junit.xml` go |
| `STEP_TIMEOUT` | Per-step timeout in ms |
| `NO_VIDEO`, `HIGHLIGHT` | `NO_VIDEO=1` skips the video; `HIGHLIGHT=0` hides the boxes |
| `REMOTE_URL`, `REMOTE_CAPS` | Run on a grid: Selenium hub URL or Playwright `ws://` endpoint; capabilities as JSON |
| `STORAGE_STATE`, `CHROME_PROFILE_DIR`, `CHROME_PROFILE_NAME` | Start signed in |
| `AIAUTO_SECRET_<KEY>` | Secret values (an exported project reads them from `.env`) |
| `AIAUTO_CHROME_BINARY`, `AIAUTO_CHROMEDRIVER` | A specific browser or driver |
