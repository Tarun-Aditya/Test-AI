# Deployment: every way to run it

Pick the mode that fits, then follow its steps. Every mode uses the same hub, the same web app and the same
database; you can start with one and grow into another without losing anything.

- [Choose a mode](#choose-a-mode)
- [Mode 1: one computer](#mode-1-one-computer)
- [Mode 2: one computer in Docker](#mode-2-one-computer-in-docker)
- [Mode 3: a hub for the team (server or VM)](#mode-3-a-hub-for-the-team-server-or-vm)
- [Mode 4: a hub plus agents on other machines](#mode-4-a-hub-plus-agents-on-other-machines)
- [Installing an agent](#installing-an-agent) · [Windows](#windows-agent) · [macOS](#macos-agent) · [Linux](#linux-agent) · [Docker](#docker-agent)
- [Mode 5: cloud and Selenium grids](#mode-5-cloud-and-selenium-grids)
- [Network and ports](#network-and-ports)
- [HTTPS and reverse proxies](#https-and-reverse-proxies)
- [Configuration checklist](#configuration-checklist)
- [Upgrading, backing up, moving](#upgrading-backing-up-moving)

## Choose a mode

```mermaid
flowchart TD
  Q1{"Who uses it?"} -->|"just me"| Q2{"Python or Docker?"}
  Q1 -->|"a team"| Q3{"Where do tests need to run?"}
  Q2 -->|Python| M1["Mode 1<br/>one computer"]
  Q2 -->|Docker| M2["Mode 2<br/>one computer in Docker"]
  Q3 -->|"on the server is enough<br/>(Linux browsers)"| M3["Mode 3<br/>team hub on a server"]
  Q3 -->|"also Windows / macOS / Safari,<br/>more parallel runs,<br/>inside our network"| M4["Mode 4<br/>hub + agents"]
  Q3 -->|"many OS / browser versions<br/>without our own machines"| M5["Mode 5<br/>hub + cloud grid"]
  M4 -.-> M5
```

| Mode | Runs tests on | Parallel runs | Sign-in | Effort |
|---|---|---|---|---|
| **1** One computer | that computer | 2 (`AIAUTO_LOCAL_WORKERS`) | optional | `./setup.sh`, `./start.sh` |
| **2** Docker, one computer | the container: Chromium, Chrome, Edge, Firefox, WebKit | 2 + agent slots | optional | `docker compose up` |
| **3** Team hub | the server | 2–8 depending on the server | accounts / SSO | a VM, HTTPS, backups |
| **4** Hub + agents | the hub and every agent: Windows, macOS (incl. Safari), Linux, Docker | sum of all slots | accounts / SSO | Mode 3 + one command per machine |
| **5** Hub + grids | BrowserStack, Sauce Labs, LambdaTest, Selenium Grid, Playwright server | grid's slots | accounts / SSO | Mode 3 + a grid URL |

## Mode 1: one computer

```mermaid
flowchart LR
  U["You<br/>browser"] -->|"http://localhost:8000"| H["Hub<br/>python -m aiauto server"]
  H --> W["2 workers"] --> B["Chromium / Chrome /<br/>Edge / Firefox / Safari"] --> APP["Site under test"]
  H --- D[("aiauto.db<br/>artifacts/")]
```

| macOS / Linux | Windows |
|---|---|
| `./setup.sh` (add `--demo` for demo data) | `setup.bat` (add `--demo`) |
| `./start.sh` | `start.bat` |

Open http://localhost:8000. The first start prints a setup code in the window to create the admin; with demo data,
sign in as `admin` / `autopilot-demo`. For no sign-in at all: `AIAUTO_AUTH=off ./start.sh`.

Visible ("headed") runs open real browser windows on your desktop. Stop the hub with Ctrl+C; history stays in
`aiauto.db` and `artifacts/` in the folder.

## Mode 2: one computer in Docker

```mermaid
flowchart LR
  U["You"] -->|":8000"| H
  subgraph compose["docker compose"]
    H["hub container"] <-->|"/ws/agent"| A["agent container<br/>2 slots, every Linux browser,<br/>virtual screen"]
  end
  H --- V[("autopilot-data volume<br/>/data: db, key, artifacts")]
```

```bash
docker compose up -d --build                     # hub on http://localhost:8000 + one agent
docker compose logs hub | grep "setup code"       # first start: the code to create the admin
docker compose up -d --scale agent=3              # more agents = more parallel runs (2 each)
```

Demo data instead of an empty start (before the first `up`, on a fresh volume):

```bash
docker compose run --rm hub python -m aiauto demo --db /data/aiauto.db --artifacts /data/artifacts
docker compose up -d
```

Settings go in `docker-compose.yml` under `environment:` or in a `.env` file next to it (`AIAUTO_ADMIN_USER`,
`AIAUTO_ADMIN_PASSWORD`, `AIAUTO_AGENT_TOKEN`, `ANTHROPIC_API_KEY`, …).

## Mode 3: a hub for the team (server or VM)

```mermaid
flowchart LR
  T["Team<br/>browsers"] -->|"https://autopilot.example.com"| P["HTTPS proxy<br/>Caddy / nginx / your LB"]
  P -->|":8000"| H["Hub"]
  H --- D[("database, key,<br/>artifacts")]
  H -->|"OIDC"| IDP["Okta / Entra ID /<br/>Google / Keycloak"]
  H -->|tokens| T2["Jira · GitHub ·<br/>Zephyr · Xray"]
```

1. **A machine**: Linux VM with 2+ CPUs and 4 GB RAM (add ~1 GB per extra parallel test), 20 GB disk for runs.
   Docker is simplest; Python works the same way as Mode 1.
2. **Start it with an admin and its public address**, e.g. in `.env` next to `docker-compose.yml`:

   ```bash
   AIAUTO_ADMIN_USER=admin
   AIAUTO_ADMIN_PASSWORD=<a long password>
   AIAUTO_PUBLIC_URL=https://autopilot.example.com
   DOMAIN=autopilot.example.com
   ```
   then `docker compose --profile https up -d --build`. Caddy gets a Let's Encrypt certificate (DNS must point at the
   server; ports 80 and 443 open). Using your own proxy instead: see [HTTPS](#https-and-reverse-proxies).
3. **People**: Admin → Users (username + password), or Admin → Sign-in for SSO (register
   `https://autopilot.example.com/api/auth/sso/callback` as the redirect URI with your identity provider).
4. **Projects** per team (Admin → Projects), **AI** (Admin → Organisation AI, or each person's My AI),
   **integrations** per project (Admin → Integrations).
5. **Backups** of `/data` (see [Upgrading, backing up, moving](#upgrading-backing-up-moving)).
6. Optional: `AIAUTO_DEMO_SITE=off` to remove the practice pages.

## Mode 4: a hub plus agents on other machines

```mermaid
flowchart LR
  subgraph net["Your network"]
    H["Hub<br/>(Mode 3)"]
    W["Windows PC<br/>agent.bat<br/>Chrome · Edge · Firefox"]
    M["Mac<br/>agent.sh<br/>Safari · Chrome · WebKit"]
    L["Linux box<br/>agent.sh / systemd"]
    D["Docker host<br/>docker-compose.agent.yml<br/>× N agents"]
  end
  W -->|"outbound WebSocket<br/>/ws/agent + token"| H
  M --> H
  L --> H
  D --> H
  W & M & L & D --> APP["Site under test<br/>(staging, localhost, VPN-only)"]
```

Agents **connect out** to the hub; the hub never connects to them. So an agent can sit behind NAT, on a laptop, in a
different office or inside a VPN that only it can reach, as long as it can open `http(s)://HUB/ws/agent`. Each agent
uses its own browsers, so tests run where the site under test is reachable. That includes `localhost` apps on a
developer's machine.

What the hub does with them: one queue; a run goes to the least-busy machine it may use with a free slot (see
[the picker](machines.md#parallel-runs)); live frames, screenshots and results stream back while it runs; the run
folder uploads when it ends. A machine that drops off marks its running tests *interrupted* and comes back by itself.

## Installing an agent

The same three steps on every OS:

```mermaid
sequenceDiagram
  autonumber
  participant Admin as Admin (web app)
  participant Hub
  participant PC as New machine
  Admin->>Hub: Machines → Agent tokens → New token
  Hub-->>Admin: token (shown once)
  PC->>PC: setup.sh / setup.bat (Python, packages, browser)
  PC->>PC: agent.env: hub URL, token, name, slots
  PC->>Hub: agent.sh / agent.bat → hello (name, token, OS, engines, browsers, slots)
  Hub-->>PC: welcome · machine shows Online
  Hub->>PC: job (test, data, config) when a slot is free
  PC-->>Hub: live events, then the run folder
```

1. **Token**: on the hub, **Machines → Agent tokens → New token**, named after the machine. Copy it now: it is shown
   once and stored only as a hash. Revoke it there to disconnect that machine.
2. **Install** on the machine: copy or clone the repository, then `./setup.sh` (macOS / Linux) or `setup.bat`
   (Windows). No demo data. Add `--all-browsers` for Playwright's Firefox and WebKit.
3. **Configure and start**: copy `agent.env.example` to `agent.env` and fill it in:

   ```bash
   AIAUTO_SERVER=https://autopilot.example.com     # the hub, as this machine reaches it
   AIAUTO_AGENT_TOKEN=<the token>
   AGENT_NAME=win-qa-01                            # how it shows in the machine list
   AIAUTO_SLOTS=2                                  # tests at once (≈1 GB RAM each)
   ```
   then `./agent.sh` or `agent.bat`. It prints `connected to ... as 'win-qa-01' with 2 slot(s)` and appears under
   **Machines** as *Online*. Options on the command line work too:
   `agent.bat --server https://autopilot.example.com --token <token> --name win-qa-01 --slots 2`.

![Machines page with the hub and an agent online, and its token](media/screens/machines.png)

To start it automatically, use the section for its OS below.

### Windows agent

Run it **in the signed-in user's session** so visible browsers can open on the desktop: a scheduled task at logon.

```bat
schtasks /Create /TN "Autopilot agent" /TR "\"C:\autopilot\agent.bat\"" /SC ONLOGON /RL LIMITED /F
schtasks /Run /TN "Autopilot agent"
```

- Set the PC to sign in automatically (or keep a session open) if it's a dedicated test machine.
- Headless-only machines can run it as a Windows service instead (e.g. with NSSM:
  `nssm install AutopilotAgent C:\autopilot\agent.bat`), but services can't show browser windows.
- Edge and Chrome are found where they're installed; Firefox needs to be installed for Selenium.
- Allow outbound HTTPS to the hub in the firewall; nothing inbound is needed.

### macOS agent

Run it as a **LaunchAgent** (in your login session, so Safari and visible browsers work). Save as
`~/Library/LaunchAgents/com.autopilot.agent.plist`, with the path of your copy:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.autopilot.agent</string>
  <key>ProgramArguments</key><array><string>/Users/qa/autopilot/agent.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/qa/autopilot/agent.log</string>
  <key>StandardErrorPath</key><string>/Users/qa/autopilot/agent.log</string>
</dict>
</plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.autopilot.agent.plist   # start now and at login
launchctl bootout gui/$(id -u)/com.autopilot.agent                                  # stop and remove
```

- **Safari**: once, `sudo safaridriver --enable`, and in Safari: Settings → Advanced → *Show features for web
  developers*, then Develop → *Allow Remote Automation*. Safari runs one test at a time: use `AIAUTO_SLOTS=1` on a
  Mac that runs Safari.
- Keep the Mac awake (System Settings → Energy → prevent sleep) if it's a dedicated test machine.

### Linux agent

A **systemd** service. Headed runs on a server need a virtual screen, so it starts through `xvfb-run`
(`sudo apt install xvfb`); on a desktop with a screen, drop `xvfb-run -a`. Save as
`/etc/systemd/system/autopilot-agent.service`:

```ini
[Unit]
Description=Autopilot agent
After=network-online.target
Wants=network-online.target

[Service]
User=autopilot
WorkingDirectory=/opt/autopilot
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1920x1080x24" /opt/autopilot/agent.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd --system --create-home autopilot                  # a user for it (once)
sudo chown -R autopilot /opt/autopilot
sudo -u autopilot /opt/autopilot/setup.sh                       # as that user
sudo /opt/autopilot/.venv/bin/python -m playwright install-deps # browser libraries (once)
sudo systemctl daemon-reload && sudo systemctl enable --now autopilot-agent
journalctl -u autopilot-agent -f                                # its log
```

### Docker agent

On any machine with Docker. Build the image there (`docker build -t autopilot .` in the repository) or pull it from
your registry, then:

```bash
HUB_URL=https://autopilot.example.com AGENT_TOKEN=<token> docker compose -f docker-compose.agent.yml up -d
HUB_URL=... AGENT_TOKEN=... docker compose -f docker-compose.agent.yml up -d --scale agent=4   # 4 agents × 2 slots
```

or without compose:

```bash
docker run -d --name autopilot-agent --restart unless-stopped --shm-size=2g \
  autopilot agent --server https://autopilot.example.com --token <token> --name docker-1 --slots 2
```

Each container has Chromium, Chrome, Edge, Firefox and WebKit for both engines and a virtual screen, and restarts
with the machine. A hub on the same machine is reachable as `HUB_URL=http://host.docker.internal:8000`. Scaled agents
name themselves after their container; give one agent a fixed name with `--name`.

Moving the image to a machine without the repository: `docker save autopilot | ssh host docker load`, or push it to
your registry and set `AUTOPILOT_IMAGE=registry.example.com/autopilot:latest`.

### Agent settings

| Setting | `agent.env` / environment | Command line | Default |
|---|---|---|---|
| Hub address | `AIAUTO_SERVER` | `--server` | `http://localhost:8000` |
| Token | `AIAUTO_AGENT_TOKEN` | `--token` | – |
| Name | `AGENT_NAME` | `--name` | the host name |
| Parallel tests | `AIAUTO_SLOTS` | `--slots` | 1 |
| Local copies of run folders | `AIAUTO_AGENT_ARTIFACTS` | `--out` | `agent_artifacts` |
| Its own AI (optional) | `ANTHROPIC_API_KEY`, or a logged-in Claude Code / Cursor / Copilot CLI | | the hub answers its AI calls |
| A specific browser or driver | `AIAUTO_CHROME_BINARY`, `AIAUTO_CHROMEDRIVER`, `AIAUTO_EDGEDRIVER`, `AIAUTO_GECKODRIVER`, … | | found automatically |

Using it: pick the machine in Studio's machine picker (or several: **Run on each** / **Share as pool**), choose
**Run tests here** on the Machines page, or send `"agent": "win-qa-01"` in the API.

## Mode 5: cloud and Selenium grids

```mermaid
flowchart LR
  H["Hub"] -->|"WebDriver over HTTPS<br/>(the hub drives the browser)"| G["BrowserStack · Sauce Labs ·<br/>LambdaTest · Selenium Grid"]
  H -->|"ws:// CDP / browser server"| PW["Playwright server"]
  G --> APP["Site under test"]
  PW --> APP
```

**Machines → Add a grid…**: name, URL, engine, browser, capabilities (JSON) and slots (sessions at once). It then
appears in the machine picker like a machine.

| Grid | URL | Capabilities example |
|---|---|---|
| Selenium Grid | `http://grid.internal:4444/wd/hub` | `{"platformName": "Windows 11"}` |
| BrowserStack | `https://USER:KEY@hub-cloud.browserstack.com/wd/hub` | `{"bstack:options": {"os": "Windows", "osVersion": "11"}, "browserName": "Edge"}` |
| Sauce Labs | `https://USER:KEY@ondemand.eu-central-1.saucelabs.com/wd/hub` | `{"platformName": "macOS 14", "browserName": "safari"}` |
| LambdaTest | `https://USER:KEY@hub.lambdatest.com/wd/hub` | `{"LT:Options": {"platformName": "Windows 11"}}` |
| Playwright server | `ws://host:3000/` | – |
| Chrome DevTools | `http://host:9222` | – |

Credentials in the URL stay on the hub and are shown masked. The cloud's browser must be able to reach the site under
test (public staging, or the provider's tunnel). Generated scripts run on the same grids with
`REMOTE_URL=... REMOTE_CAPS='{...}' python test_selenium.py`.

## Network and ports

```mermaid
flowchart LR
  B["People's browsers"] -->|"443 (or 8000)<br/>HTTPS + WebSocket /ws/ui"| H["Hub :8000"]
  A["Agents"] -->|"443 (or 8000)<br/>WebSocket /ws/agent, HTTPS uploads"| H
  H -->|"443"| X["Jira · Confluence · GitHub · Zephyr ·<br/>Xray · Azure DevOps · AI providers · grids"]
  H -->|"443"| IDP["SSO provider"]
  A --> S["Site under test"]
  H --> S
```

| From | To | Port | Why |
|---|---|---|---|
| People | hub | 443 via the proxy (or 8000 directly) | web app, API, live view (`/ws/ui` WebSocket) |
| Agents | hub | the same | jobs and events (`/ws/agent`), run uploads (`/api/agents/upload/…`), AI calls |
| Hub | tools, AI, grids, SSO | 443 outbound | integrations, AI, remote browsers, sign-in |
| Hub / agents | the site under test | whatever it uses | the tests themselves |
| Selenium (first run per browser) | driver downloads | 443 outbound | Selenium Manager fetches drivers; preset `AIAUTO_*DRIVER` paths if blocked |

Nothing ever connects *to* an agent.

## HTTPS and reverse proxies

The bundled option: `DOMAIN=autopilot.example.com docker compose --profile https up -d` (Caddy, automatic
certificates). With your own proxy, forward everything to the hub and let WebSockets through. nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name autopilot.example.com;
    ssl_certificate     /etc/ssl/autopilot.crt;
    ssl_certificate_key /etc/ssl/autopilot.key;
    client_max_body_size 500m;                    # agents upload run folders (videos)

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;   # /ws/ui and /ws/agent
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;                 # long-lived WebSockets
    }
}
```

Then set `AIAUTO_PUBLIC_URL=https://autopilot.example.com`. Cookies are marked `Secure` when the request arrived over
HTTPS.

## Configuration checklist

| | Where | Mode |
|---|---|---|
| Admin account | setup code, or `AIAUTO_ADMIN_USER` / `AIAUTO_ADMIN_PASSWORD` | all |
| Public address | `AIAUTO_PUBLIC_URL` | 3–5 |
| HTTPS | Caddy profile or your proxy | 3–5 |
| Encryption key backed up | `aiauto.key` next to the DB, or `AIAUTO_SECRET_KEY` | all |
| People and roles | Admin → Users, or Admin → Sign-in (SSO) | 3–5 |
| Projects and members | Admin → Projects | 3–5 |
| AI (optional) | Admin → Organisation AI; each person's My AI | all |
| Tool connections | Admin → Integrations (per project) | as needed |
| Agent tokens, one per machine | Machines → Agent tokens | 4 |
| Grids | Machines → Add a grid | 5 |
| Parallel runs on the hub | `AIAUTO_LOCAL_WORKERS` | all |
| Backups | `/data` or `aiauto.db` + key + `artifacts/` | all |

Every variable: [Configuration](configuration.md).

## Upgrading, backing up, moving

- **Upgrade**: `git pull`, then `./setup.sh` / `setup.bat` (Python), or `docker compose up -d --build` (Docker). The
  database upgrades itself on start. Upgrade agents the same way; an agent a version behind keeps working, but upgrade
  them together when you can.
- **Back up**: `sqlite3 aiauto.db ".backup backup.db"` while running (or copy with the hub stopped), plus `aiauto.key`
  and `artifacts/`. In Docker: `docker run --rm -v autopilot-data:/data -v "$PWD":/b alpine tar czf /b/autopilot.tgz /data`.
- **Move** a hub: stop it, copy the database, key and `artifacts/` to the new machine, start it with the same
  `AIAUTO_DB` / `AIAUTO_ARTIFACTS` (and `AIAUTO_SECRET_KEY` if set). Point agents' `AIAUTO_SERVER` at the new address.

More in [Database](database.md#back-up-move-reset) and [Troubleshooting](troubleshooting.md).
