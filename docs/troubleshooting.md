# Troubleshooting

## Installing and starting

| Problem | Fix |
|---|---|
| `Python 3.11 or newer is needed` | Install Python from [python.org](https://www.python.org/downloads/) (Windows: tick *Add python.exe to PATH*), open a new terminal, run setup again. Or point at one: `PYTHON=/path/to/python3.12 ./setup.sh` |
| `./setup.sh: Permission denied` | `chmod +x setup.sh start.sh`, or `bash setup.sh` |
| Windows: `running scripts is disabled` in PowerShell | Use `setup.bat` / `start.bat` (they are not PowerShell scripts), or run them from Command Prompt |
| Linux: the browser won't start, `missing dependencies` or `libnss3` | `sudo .venv/bin/python -m playwright install-deps` |
| `Address already in use` / port 8000 busy | Another hub is running, or another program uses the port: `PORT=9000 ./start.sh` (Windows: `set PORT=9000` then `start.bat`) |
| I lost the setup code | It is printed every time the hub starts while there are no users: restart and look again. Or start with `AIAUTO_ADMIN_USER=admin AIAUTO_ADMIN_PASSWORD=<10+ chars>` |
| I forgot the admin password | Stop the hub, then `python -c "from aiauto.db import DB; from aiauto.server.auth import hash_password; d=DB('aiauto.db'); u=d.user_get(username='admin'); d.user_update(u['id'], pw_hash=hash_password('new-password-here'))"` |
| `python -m aiauto demo` says the database has accounts | It won't mix the demo into your data. Use another file (`--db demo.db --artifacts demo_artifacts`, then start with `AIAUTO_DB=demo.db AIAUTO_ARTIFACTS=demo_artifacts`) or `--force` |
| Demo replays fail with "connection refused" | The demo runs point at port 8000. Run the hub on 8000, or rebuild the demo with `--port <yours>` |

## Running tests

| Problem | Fix |
|---|---|
| "Some sentences were not understood" for a paragraph | Rewrite just that sentence with one action ("The user enters ...", "Click ..."), or turn AI on. Background sentences are left out on their own; only a sentence that asks for an action nothing recognised is reported |
| The breakdown has `${base_url}` and "no value for base_url" | The text says "the sign up page" but not where it is: set `base_url` under Test data to that page's address |
| A check from my paragraph fails although the page looks right | The paragraph's wording differs from the page's ("age must be between 18 and 99" vs "Age must be 18-99"). Edit the check to the exact text; the *Check the wording* note marks these |
| `Could not identify element for '...'` | The error lists the closest candidates. Name the element as it appears on the page (its label, button text or placeholder), add its kind (`the Search button`), or an ordinal (`the second ...`). Check the step's screenshot: is the element on screen yet? Add `Wait for "..." to appear` before it |
| `Ambiguous '...': N different elements match equally well` | Be more specific: an ordinal (`the first "Add to cart" button`), context (`the View button for order 1002`), or the element's own text. With AI configured, ties are settled by the AI |
| A check fails but the text is visible in the screenshot | Text that appears late: add `Wait for "..." to appear`, or raise the step timeout (Studio → Settings). For text in a hover menu, the hover is held for the checks that follow it, so check right after the hover |
| Passes on one browser, fails on another | Compare the two runs' step screenshots. Firefox and WebKit lay out some pages differently; Safari can't run more than one session at a time (`--slots 1`) |
| Selenium can't find a driver | Selenium Manager downloads drivers on first use: allow network access to its download hosts, or set `AIAUTO_CHROMEDRIVER` / `AIAUTO_EDGEDRIVER` / `AIAUTO_GECKODRIVER` |
| Safari: `Allow remote automation` / session not created | Once on that Mac: `sudo safaridriver --enable` (and in Safari: Develop → Allow Remote Automation) |
| Visible run on Linux server: `cannot open display` | There is no screen: run headless, run inside Docker (virtual screen included), or `xvfb-run -a python -m aiauto agent ...` |
| A site shows a bot check / CAPTCHA | Many sites block cloud and data-centre browsers. Run the test from your own machine as an agent, visible, with your Chrome profile or a saved login. Autopilot does not try to get around bot detection |
| Video missing | Videos need `imageio-ffmpeg` (in `requirements.txt`); check `pip show imageio-ffmpeg`. Turned off in Studio → Settings? |
| Runs stay *queued* | No machine with a free slot can take them: check Machines (is the agent online? does it support that engine?), or raise `--slots` / `AIAUTO_LOCAL_WORKERS` |
| Runs show *interrupted* | The hub or the agent stopped while they ran. Run them again |

## AI

| Problem | Fix |
|---|---|
| The AI chip says *off* | Nothing is configured, or nothing answered. Admin → Organisation AI → Test connection shows why |
| Claude Code / Cursor / Copilot is installed but not used | It must be logged in for the user the hub runs as: run `claude` (or `cursor-agent login`, `copilot`) once in that account. Or set the path: `AIAUTO_CLAUDE_CLI=/path/to/claude` |
| Out of credits / rate limited | Auto skips that provider for the session and tries the next. Tests still run on rules when none answers |
| I want reproducible runs | `AIAUTO_DISABLE_LLM=1`, or untick AI in Studio |

## Accounts and integrations

| Problem | Fix |
|---|---|
| `Missing X-Requested-With header` from a script | Add `-H "X-Requested-With: autopilot"` to every POST / PUT / DELETE |
| `You are not a member of project '...'` | An admin adds you in Admin → Projects |
| SSO redirects to the wrong address | Set `AIAUTO_PUBLIC_URL` to the address people use, and register `<that>/api/auth/sso/callback` with the identity provider |
| `could not decrypt a stored secret` in the log | `AIAUTO_SECRET_KEY` changed or `aiauto.key` was replaced. Restore the old key, or enter the tokens again in Admin |
| Jira / GitHub `401` on Test connection | Jira Cloud wants your e-mail + an API token; Data Center wants a personal access token. GitHub's fine-grained token needs read access to Contents and Pull requests on that repository |

## Getting help

Collect the hub's console output, the run's `report.html` (Runs → the run → Report → download), and the step
screenshot of the failing step. Run the hub with `python -m aiauto -v server` for more detail.
