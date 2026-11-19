# Machines, Windows and macOS, grids and Docker

Where tests run, and how to host the hub for a team.

- [Three ways to run tests](#three-ways-to-run-tests)
- [Add a machine (agent)](#add-a-machine-agent)
- [Parallel runs](#parallel-runs)
- [Windows and macOS](#windows-and-macos)
- [Remote browser grids](#remote-browser-grids)
- [Docker](#docker)
- [Hosting the hub for a team](#hosting-the-hub-for-a-team)

## Three ways to run tests

| | Set up | Use it for |
|---|---|---|
| **The hub itself** (`local`) | Nothing: runs `AIAUTO_LOCAL_WORKERS` (2) tests at once | Getting started, one machine |
| **Agents** | `python -m aiauto agent ...` on any computer | Windows / macOS / Linux machines, a visible browser on your desk, Docker boxes, your own Chrome profile |
| **Grids** | A URL under Machines → Remote browser grids | Selenium Grid, BrowserStack, Sauce Labs, LambdaTest, a Playwright browser server |

Live view, highlight boxes, screenshots, video, report and generated scripts are the same wherever a test runs.

## Add a machine (agent)

1. On the hub: **Machines → Agent tokens → New token**. Copy it (it is shown once).
2. On the other machine: install as in [Getting started](getting-started.md#1-install) (`./setup.sh` or
   `setup.bat`; no demo data needed), then:

   ```bash
   .venv/bin/python -m aiauto agent --server http://HUB:8000 --name my-laptop --token <token> --slots 2
   # Windows: .venv\Scripts\python -m aiauto agent --server http://HUB:8000 --name my-laptop --token <token> --slots 2
   ```
3. It appears in the machine picker and on the Machines page with its platform, engines, browsers and slots.

The agent opens an **outbound** WebSocket to the hub, so it works behind NAT and firewalls: only the hub needs to be
reachable. It reconnects by itself, streams live frames and step results, and uploads each run's folder when the run
ends. Revoking its token disconnects it.

## Parallel runs

- **Slots**: `--slots 3` lets a machine run 3 tests at once, each in its own browser (any mix of Playwright and
  Selenium). The hub never sends a machine more runs than it has free slots; the rest wait as *queued* and start as
  soon as a slot frees up. The picker shows `running/slots`.
- **Machine picker** (top bar): tick several machines, then **Run on each** (every run on every machine: the same
  test on Windows, Mac and Linux at once) or **Share as pool** (iterations spread over them, first free slot wins).
  **Any free machine** pools over every connected machine.
- **Engine: Both** runs everything once per engine.
- API: `"agent": ["win-1", "mac-1"], "spread": "each" | "pool", "engines": ["playwright", "selenium"]`.
- A machine that disconnects marks its running tests *interrupted*; queued runs for "any" or a pool move to machines
  still online, and runs pinned to it wait until it returns.
- Each parallel browser needs roughly 0.5–1 GB of memory.

## Windows and macOS

Everything is cross-platform. On a Windows or Mac computer, run an agent (above) and pick it in the machine picker.

| Browser | Playwright | Selenium |
|---|---|---|
| Chromium | ✓ (downloaded by setup) | ✓ |
| Google Chrome | ✓ (installed Chrome) | ✓ |
| Microsoft Edge | ✓ (`msedge`) | ✓ |
| Firefox | ✓ (`setup --all-browsers`) | ✓ (installed Firefox) |
| WebKit (Safari's engine) | ✓ on every OS (`setup --all-browsers`) | – |
| Safari | – | ✓ on a Mac, after `sudo safaridriver --enable` once |

Safari notes: safaridriver allows one session at a time (use `--slots 1` on that Mac), and its page dialogs are
answered in the page (Safari's WebDriver stalls while a native alert is open), still following the run's
accept/dismiss setting and still recorded.

Visible ("headed") runs open a real window on that machine. For SSO or MFA, run visible on your own machine with
`Pause 90 seconds for SSO login` and save the login for later runs.

The project's CI runs the whole real-website suite, visible and with AI off, on real Windows (Chrome, Edge), macOS
(Chrome, WebKit, Safari) and Linux (Chrome, Firefox): `.github/workflows/real-sites.yml`.

## Remote browser grids

**Machines → Remote browser grids → Add**: a name, the URL, the engine, the browser, capabilities and slots.

| Grid | URL | Engine |
|---|---|---|
| Your Selenium Grid | `http://grid:4444/wd/hub` | Selenium |
| BrowserStack / Sauce Labs / LambdaTest | their hub URL with your user and key | Selenium (capabilities e.g. `{"platformName": "Windows 11", "bstack:options": {...}}`) |
| Playwright browser server | `ws://host:3000/...` | Playwright |
| Chrome DevTools endpoint | `http://host:9222` | Playwright |

A grid shows up in the machine picker like a machine, with its slots. The hub drives the remote browser. Credentials
in the URL are stored on the hub and shown masked. Generated scripts run on a grid too:
`REMOTE_URL=... REMOTE_CAPS='{"platformName": "Windows 11"}' python test_selenium.py`.

## Docker

One image runs either role. It is based on Microsoft's Playwright image and includes every browser both engines can
drive on Linux, with matching drivers (Playwright: Chromium, Chrome, Edge, Firefox, WebKit; Selenium: Chromium,
Chrome, Edge, Firefox) and a virtual screen for visible runs.

```bash
docker compose up --build                    # hub on :8000 + one agent with 2 slots
docker compose up --build --scale agent=3    # hub + 3 agents = 6 tests at once
```

Or by hand:

```bash
docker build -t autopilot .
docker run -d -p 8000:8000 -v autopilot-data:/data -e AIAUTO_AGENT_TOKEN=change-me autopilot hub
docker run -d --shm-size=2g autopilot agent --server http://HUB:8000 --name docker-1 --token change-me --slots 2
```

- **Data**: the hub keeps its database and runs in `/data` (the `autopilot-data` volume).
- **Demo data**: on a fresh volume, before the first start:

  ```bash
  docker compose run --rm hub python -m aiauto demo --db /data/aiauto.db --artifacts /data/artifacts
  docker compose up -d            # then sign in as admin / autopilot-demo
  ```
- **First admin**: set `AIAUTO_ADMIN_USER` / `AIAUTO_ADMIN_PASSWORD`, or read the setup code with
  `docker compose logs hub`.
- **`--shm-size`** matters: Chromium crashes with Docker's default 64 MB `/dev/shm` (about 1 GB per slot).
- **Visible runs** in a container are drawn on a virtual screen; you watch them in the live view and video. To see a
  window on your own monitor, run an agent on your computer.
- **Your Chrome profile** isn't available inside Docker: use saved logins.
- **Windows or macOS in Docker** is not possible here (Windows containers need a Windows host; macOS may only run on
  Apple hardware). Use agents on real Windows / Mac machines, or a grid.
- **Behind a TLS-inspecting proxy**: `docker build --secret id=ca,src=/path/to/ca-bundle.crt -t autopilot .` (used
  during the build only).

### Your AI subscription inside Docker

1. **Agents use the hub's AI**, no setup: an agent without AI of its own sends its AI calls to the hub, which answers
   with its provider.
2. **The CLIs in the image** (hub in Docker): build with `--build-arg AI_CLIS=1` (adds Node, Claude Code and the
   Copilot CLI) and log in with a token instead of a browser:
   - Claude: run `claude setup-token` once on your computer and pass `-e CLAUDE_CODE_OAUTH_TOKEN=...`.
   - Copilot: a GitHub token allowed to use Copilot as `-e COPILOT_GITHUB_TOKEN=...`.
   - Cursor: install its CLI in your own image layer and pass `-e CURSOR_API_KEY=...`.

   Or mount an existing Linux login: `-v ~/.claude:/root/.claude`.

An API key or LiteLLM also works (Admin → Organisation AI, or `-e ANTHROPIC_API_KEY=...`).

## Hosting the hub for a team

1. A server or VM (Linux is simplest) with Docker, or Python as in Getting started.
2. **HTTPS**: `DOMAIN=autopilot.example.com docker compose --profile https up -d` puts Caddy in front of the hub with
   an automatic Let's Encrypt certificate (the domain's DNS must point at the server; ports 80 and 443 open). Behind
   your own proxy, forward to port 8000 and pass the WebSocket upgrade for `/ws/`.
3. Set `AIAUTO_PUBLIC_URL=https://autopilot.example.com` (SSO redirects, links in Jira).
4. Create the admin, then add people (Admin → Users) or connect SSO (Admin → Sign-in). Make projects per team.
5. Give each agent its own token. Keep `AIAUTO_AUTH` on.
6. Back up `/data` (database, `aiauto.key`, artifacts) regularly: see [Database](database.md#back-up-move-reset).
7. Turn the practice pages off if you don't want them: `AIAUTO_DEMO_SITE=off`.
