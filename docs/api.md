# API

Everything the web app does goes through this REST API, so CI pipelines and scripts can do the same: start runs,
wait for them, fetch reports. The hub also serves interactive docs at **`/docs`** (Swagger UI) and the OpenAPI
schema at `/openapi.json`.

- [Signing in from a script](#signing-in-from-a-script)
- [Example: run a test from CI and fail the build if it fails](#example-run-a-test-from-ci-and-fail-the-build-if-it-fails)
- [Endpoints](#endpoints)
- [Live events](#live-events)

## Signing in from a script

With accounts on (the default), sign in once and keep the cookie:

```bash
curl -c jar -H "X-Requested-With: autopilot" -H "Content-Type: application/json" \
     -d '{"username":"ci-bot","password":"..."}' http://localhost:8000/api/auth/login
curl -b jar http://localhost:8000/api/runs
```

- Every request that changes something (`POST`, `PUT`, `DELETE`) needs the header **`X-Requested-With: autopilot`**.
- To work in a project other than Default, send **`X-Project: <key>`** (or `?project=<key>`).
- Give CI its own account with the **tester** role (Admin → Users).
- With `AIAUTO_AUTH=off` no sign-in is needed (the header is still required on writes).

## Example: run a test from CI and fail the build if it fails

```python
import sys, time, httpx

HUB, USER, PASSWORD = "https://autopilot.example.com", "ci-bot", "..."
c = httpx.Client(base_url=HUB, timeout=60, headers={"X-Requested-With": "autopilot", "X-Project": "shop"})
c.post("/api/auth/login", json={"username": USER, "password": PASSWORD}).raise_for_status()

# a library script, on both engines, on any free machine
ids = c.post("/api/runs", json={"library": "shop-cart", "engines": ["playwright", "selenium"], "agent": "any",
                                "config": {"headless": True}}).json()["run_ids"]
while True:
    runs = [c.get(f"/api/runs/{i}").json() for i in ids]
    if not any(r["live"] for r in runs):
        break
    time.sleep(5)
for r in runs:
    print(r["status"], r["test_name"], f"{HUB}/artifacts/{r['run_id']}/report.html")
sys.exit(0 if all(r["status"] == "passed" for r in runs) else 1)
```

Or skip the hub in CI: export the project once (run details → Export project), commit it, and run
`python run.py` in the pipeline. It writes JUnit XML to `results/`.

## Endpoints

### Runs

| Method & path | What it does |
|---|---|
| `POST /api/runs` | Start runs. Body: one of `text` (plain English), `steps` (edited steps), `library` (a library script's name) or `replay_run_id`; plus `name`, `data` (object, JSON string or `k=v;k2=v2`), `sensitive` (keys), `iterations` (`[{data, browser, engine, label}]`), `config` (run options, see `RunConfig`), `agent` (`"local"`, `"any"`, a machine name or a list), `spread` (`each` or `pool`), `engines` (`["playwright", "selenium"]`), `reuse_secrets`. Returns `{run_ids, batch_id, ...}` |
| `POST /api/parse` | English → steps without running: `{text, data, sensitive, use_llm}`. Returns `test` (steps, and `data` / `sensitive` including values lifted out of the text, such as a masked `password`), `parser` (`rules` or `llm:…`), `unparsed` (sentences nothing understood), `ignored` (background sentences left out), `notes` (things to check) and `data_added` |
| `POST /api/ext/bridge` · `WS /ws/ext` · `POST /api/ext/session` · `WS /cdp/{token}` | The Chrome extension as a browser: it asks for a link, connects its WebSocket, and a session gives a DevTools endpoint for one tab. `POST /api/runs` with `ext_session` and `start_url` then runs a scenario in that tab with the server's engine |
| `GET /api/ext/replay/run/{id}` · `GET /api/ext/replay/library/{name}` | A past run's or a saved script's steps for the extension to replay; `needs` lists secrets it must be given (a run's owner gets them back from the vault) |
| `POST /api/runs/{id}/control` | Pause a running test before its next step, resume it, or let one step through: `{action: "pause" \| "resume" \| "step"}`. `config.start_paused` starts a run held |
| `POST /api/record/step-from-element` | A step plus the element the person picked on the page → the step with that element's locators (the panel's "Pick the right element") |
| `POST /api/record/compile` | Recorder events from the Chrome extension → steps (stored locators, secrets lifted): `{events, name, data, sensitive}` → `{test, notes}` |
| `POST /api/record/import-chrome` | A Chrome DevTools Recorder JSON export → the same steps: `{recording, name}` → `{test, notes}` |
| `POST /api/library` | Save a test to the library: `{name, test}` (secret values are not stored) |
| `GET /api/runs` | Run list, newest first. Query: `limit`, `status`, `q` (search), `mine` |
| `GET /api/runs/{id}` | One run: steps, data (secrets masked), config, batch, `live`, `has_secrets` |
| `GET /api/stats` | Overview numbers: totals, pass/fail, in progress, average time, 14-day trend |
| `POST /api/runs/{id}/cancel` | Stop one run |
| `POST /api/runs/cancel` | Stop several: `{run_ids: [...]}` or `{batch_id}` |
| `DELETE /api/runs/{id}` | Delete a run and its folder (owner or admin) |
| `GET /api/runs/{id}/files` | The run folder's file tree |
| `GET /artifacts/{id}/{path}` | Any file of a run: `report.html`, `video.webm`, `screenshots/…`, `test_playwright.py`, `summary.json` |
| `GET /api/runs/{id}/script` | The run's `script.json` |
| `GET /api/runs/{id}/export` | Runnable project zip (the run, or every iteration of its batch) |
| `GET /api/runs/{id}/download` | The raw run folder as a zip |

### Schedules, CI hooks, notifications, flaky tests

| | |
|---|---|
| `GET/PUT /api/schedules`, `DELETE /api/schedules/{id}` | Run a library script on a cron schedule. Body: `{library, cron, name, enabled, data, config, agent, engines}`. `cron` is five fields (`30 2 * * *`, `*/15 9-17 * * mon-fri`) or `@daily`/`@hourly`/`@weekly`; the hub's clock decides |
| `POST /api/schedules/{id}/run` | Run a schedule now |
| `GET /api/schedules/preview?cron=...&count=5` | The next times a cron expression fires |
| `GET/POST /api/hooks`, `DELETE /api/hooks/{id}` | CI hooks. `POST /api/hooks` `{library, data, config}` returns a `url` once; **`POST /api/hooks/run/{token}`** (no sign-in, the token is the credential) starts that script. A JSON body `{"data": {...}}` overrides data for that run |
| `GET/PUT /api/notifications`, `DELETE /api/notifications/{id}`, `POST /api/notifications/{id}/test` | Channels told when a run finishes (admin). `{kind: slack\|teams\|webhook\|email, url\|to, on: failed\|always\|recovered, project}`. Email uses `AIAUTO_SMTP_HOST/PORT/USER/PASSWORD/FROM`; `AIAUTO_PUBLIC_URL` puts a link to the run in the message |
| `GET /api/flaky?window=20&only=true` | Tests ranked by how unstable their recent results are (`flaky`, `pass_rate`, `flips`, `history`) |

### Library and saved logins

| Method & path | What it does |
|---|---|
| `GET /api/library` · `GET /api/library/{name}` · `DELETE /api/library/{name}` | List, read, delete scripts |
| `POST /api/library/from-run` | Save a run's script: `{run_id, name}` |
| `POST /api/library/upload` | Upload a `script.json` or generated `.py` (multipart: `file`, `name`) |
| `GET /api/sessions` · `POST /api/sessions/{name}` · `DELETE /api/sessions/{name}` | Saved logins; upload a `storage_state.json` (multipart `file`) |

### Test design and integrations

| Method & path | What it does |
|---|---|
| `GET /api/integrations/catalog` | Every supported tool and its settings |
| `GET /api/connections` · `PUT/DELETE /api/connections/{kind}` · `POST /api/connections/{kind}/test` | The project's tool connections (`{settings, secrets}`; secrets come back masked) |
| `GET /api/requirements?q=` · `GET /api/requirements/{key}` | Search the tracker; one story's context pack |
| `POST /api/designs` | Design test cases: `{issue_key}` from the tracker, or `{story: {text, title, key, type, notes}}` pasted; `use_ai` |
| `GET /api/designs` · `GET/PUT/DELETE /api/designs/{id}` | List, read, edit cases, delete |
| `POST /api/designs/{id}/status` | Approve / reject / draft cases: `{case_ids, status}` |
| `POST /api/designs/{id}/run` | Run the approved cases: `{base_url, case_ids, data, sensitive, config, agent, engines}` |
| `POST /api/designs/{id}/push` · `/publish` · `/link` · `/refresh` · `PUT /settings` | Push cases to the test tool, publish results, link to a tracker key, re-check the story, auto-send results |
| `GET /api/designs/{id}/runs` · `/log` · `GET /api/sync-log` | Runs per case; what was sent to the tools |

### Accounts, projects, machines, AI (admin unless noted)

| Method & path | What it does |
|---|---|
| `POST /api/auth/login` · `/logout` · `GET /api/auth/me` | Sign in and out (anyone); who am I |
| `POST /api/auth/setup` | Create the first admin with the setup code |
| `POST /api/me/password` · `GET/PUT /api/me/ai` · `POST /api/me/ai/test` | Your password and **My AI** (anyone signed in) |
| `GET/POST /api/users` · `PUT/DELETE /api/users/{id}` | Users: `{username, name, email, role, password, disabled}` |
| `GET/PUT /api/auth/sso-config` · `GET /api/auth/sso/start` · `/callback` | SSO settings and the OpenID Connect flow |
| `GET /api/projects` · `PUT/DELETE /api/projects/{key}` | Projects: `{name, description, members: [usernames]}` |
| `GET /api/agents` · `DELETE /api/agents/{name}` | Machines; forget one |
| `GET/POST /api/agent-tokens` · `DELETE /api/agent-tokens/{id}` | Per-machine agent tokens (shown once) |
| `GET /api/grids` · `PUT/DELETE /api/grids/{name}` | Remote browser grids: `{url, engine, browser, caps, slots}` |
| `GET/PUT /api/ai` · `POST /api/ai/test` | Organisation AI |
| `GET /api/health` | Up? (anyone) |

Agents use `WS /ws/agent`, `POST /api/agents/upload/{run_id}` and `POST /api/ai/call` with their token; see
[Architecture → Machines](architecture.md#machines-the-hub-agents-and-grids).

## Live events

`WS /ws/ui` streams what the web app shows live, filtered to the projects you can see. Each message is JSON with a
`type`:

| Type | When | Main fields |
|---|---|---|
| `run_started` | A run opened its browser | `run_id`, `total`, `agent`, `engine` |
| `step_started` | A step begins | `run_id`, `index`, `raw`, `action`, `target`, `value` (masked) |
| `step_finished` | A step ends | `run_id`, `index`, `status`, `locator`, `error`, `screenshot`, `duration_ms`, `healed`, `picked_by` |
| `step_shot` | The step's screenshot (streamed, not stored) | `run_id`, `index`, image data |
| `live_frame` | A frame of the browser (streamed, not stored) | `run_id`, image data |
| `healing` | A stored locator broke and is being repaired | `run_id`, `index` |
| `paused` | A manual pause (e.g. SSO login) | `run_id`, seconds |
| `run_finished` | Done | `run_id`, `status`, `error` |
| `runs_changed`, `agents_changed`, `session_saved`, `ai_changed` | Lists to refresh | |

```python
import asyncio, json, websockets

async def watch():
    async with websockets.connect("ws://localhost:8000/ws/ui") as ws:     # add the sign-in cookie with accounts on
        async for msg in ws:
            ev = json.loads(msg)
            if ev["type"] in ("step_finished", "run_finished"):
                print(ev["type"], ev.get("run_id"), ev.get("index"), ev.get("status"))

asyncio.run(watch())
```
