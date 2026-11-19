# All diagrams

Every block diagram of Autopilot in one place. GitHub draws them; the prose around each lives in the linked guide.

| | | |
|---|---|---|
| [1. System context](#1-system-context) | [8. Scheduling: which machine runs it](#8-scheduling-which-machine-runs-it) | [15. Secrets: where they go](#15-secrets-where-they-go) |
| [2. Inside the hub](#2-inside-the-hub) | [9. One click, many runs](#9-one-click-many-runs) | [16. Test design and write-back](#16-test-design-and-write-back) |
| [3. Deployment modes](#3-deployment-modes) | [10. Finding the element for a step](#10-finding-the-element-for-a-step) | [17. Data model](#17-data-model) |
| [4. Network and ports](#4-network-and-ports) | [11. Replay and self-healing](#11-replay-and-self-healing) | [18. From description to portable script](#18-from-description-to-portable-script) |
| [5. Agent: install, connect, run](#5-agent-install-connect-run) | [12. Which AI answers](#12-which-ai-answers) | [19. Live view: how frames reach you](#19-live-view-how-frames-reach-you) |
| [6. Life of a run](#6-life-of-a-run) | [13. Sign-in and SSO](#13-sign-in-and-sso) | [20. CI pipelines](#20-ci-pipelines) |
| [7. Run states](#7-run-states) | [14. Who sees what: roles and projects](#14-who-sees-what-roles-and-projects) | |

## 1. System context

Who and what Autopilot talks to. ([Architecture](architecture.md))

```mermaid
flowchart TB
  QA["Testers, devs, managers<br/>(web app)"]
  CI["CI pipelines<br/>(REST API, exported projects)"]
  AP(("Autopilot"))
  APP["Web apps under test"]
  TR["Trackers & docs<br/>Jira · Azure DevOps · Confluence"]
  CODE["Code<br/>GitHub · local Git"]
  TM["Test management<br/>Zephyr Scale · Xray"]
  AI["AI providers (optional)<br/>Claude · Copilot · Cursor · API keys · LiteLLM"]
  IDP["Company sign-in<br/>Okta · Entra ID · Google · Keycloak"]
  GRID["Browser grids<br/>BrowserStack · Sauce · LambdaTest · Selenium Grid"]
  QA --> AP
  CI --> AP
  AP -->|"Playwright / Selenium"| APP
  AP <-->|tokens| TR
  AP -->|tokens| CODE
  AP <-->|tokens| TM
  AP --> AI
  AP --> IDP
  AP --> GRID --> APP
```

## 2. Inside the hub

The parts of the hub process and what each holds. ([Architecture → The parts](architecture.md#the-parts))

```mermaid
flowchart LR
  subgraph Hub["Hub process: python -m aiauto server"]
    WEB["Web app<br/>(React, static)"]
    API["REST API<br/>FastAPI"]
    AUTH["Auth<br/>accounts · SSO · roles · projects"]
    PARSE["Parser<br/>AI or rules"]
    Q["Queue + dispatcher"]
    WK["Local workers"]
    RUN["Runner<br/>locate · act · record"]
    ENG["Engines<br/>Playwright · Selenium"]
    GEN["Script generator<br/>report · export"]
    BUS["Live events<br/>/ws/ui fan-out"]
    DBW["DB writer<br/>(one thread)"]
    INT["Integrations<br/>context · design · sync"]
  end
  DB[("SQLite<br/>+ reporting views")]
  FS[("artifacts/")]
  WEB --> API --> AUTH
  API --> PARSE --> Q --> WK --> RUN --> ENG
  RUN --> GEN --> FS
  RUN --> BUS
  BUS --> DBW --> DB
  API --> INT --> DB
  Q -->|"/ws/agent"| AG["Agents"]
  AG --> BUS
```

## 3. Deployment modes

From one laptop to a hub with agents and grids. ([Deployment](deployment.md))

```mermaid
flowchart TB
  subgraph M1["Mode 1 · one computer"]
    h1["hub + 2 workers"] --- b1["local browsers"]
  end
  subgraph M2["Mode 2 · Docker"]
    h2["hub container"] --- a2["agent containers<br/>(every Linux browser)"]
  end
  subgraph M3["Mode 3 · team hub"]
    p3["HTTPS proxy"] --> h3["hub"] --- s3["SSO · projects · backups"]
  end
  subgraph M4["Mode 4 · hub + agents"]
    h4["hub"] --- w4["Windows agent"]
    h4 --- m4["Mac agent (Safari)"]
    h4 --- l4["Linux / Docker agents"]
  end
  subgraph M5["Mode 5 · hub + grids"]
    h5["hub"] --- g5["BrowserStack · Sauce ·<br/>LambdaTest · Selenium Grid"]
  end
  M1 -->|"share it"| M3
  M2 -->|"share it"| M3
  M3 -->|"more OSes, more parallel"| M4
  M3 -->|"no own machines"| M5
```

## 4. Network and ports

Everything connects to the hub; nothing connects to an agent. ([Deployment → Network](deployment.md#network-and-ports))

```mermaid
flowchart LR
  B["Browsers of people"] -->|"443 / 8000<br/>HTTPS · /ws/ui"| H["Hub :8000"]
  A["Agents (any network)"] -->|"443 / 8000<br/>/ws/agent · uploads · AI calls"| H
  H -->|443| T["Tools · AI · SSO · grids"]
  H --> S["Sites under test"]
  A --> S
```

## 5. Agent: install, connect, run

([Deployment → Installing an agent](deployment.md#installing-an-agent))

```mermaid
sequenceDiagram
  autonumber
  participant Ad as Admin
  participant H as Hub
  participant A as Agent machine
  Ad->>H: Machines → Agent tokens → New token
  H-->>Ad: token, shown once (stored as a hash)
  A->>A: setup.sh / setup.bat, then agent.env
  A->>H: WebSocket /ws/agent: hello {name, token, OS, engines, browsers, slots}
  H-->>A: welcome (machine is Online)
  loop while free slots
    H->>A: job {test, data, config, saved login, owner's AI}
    A-->>H: event (step_started, step_finished, live_frame, ...)
    A->>H: POST /api/agents/upload/{run_id} (run folder zip)
  end
  H->>A: cancel {run_id} (Stop button)
  Note over A,H: connection lost → its running tests become interrupted, the agent reconnects by itself
```

## 6. Life of a run

([Architecture → Life of a run](architecture.md#life-of-a-run))

```mermaid
sequenceDiagram
  autonumber
  participant U as Web app
  participant H as Hub
  participant D as SQLite
  participant M as Worker / agent / grid
  participant B as Browser
  U->>H: POST /api/runs
  H->>H: English → steps (AI or rules)
  H->>D: rows, status queued
  H->>M: dispatch on a free slot
  M->>B: open browser
  loop each step
    M->>B: candidates → rank → verify locator → highlight → act → screenshot
    M-->>H: events
    H->>D: update (single writer, in order)
    H-->>U: live events
  end
  M->>M: report.html · video · scripts · summary.json
  M-->>H: upload (agents)
  H->>D: finalize from summary.json
  H->>H: write-back to tools (designed cases)
  H-->>U: run_finished
```

## 7. Run states

```mermaid
stateDiagram-v2
  [*] --> queued: submitted
  queued --> dispatched: a machine has a free slot
  queued --> cancelled: Stop
  dispatched --> running: browser opened
  running --> passed: every step passed (or optional ones skipped)
  running --> failed: a step failed
  running --> cancelled: Stop (after the current step)
  running --> interrupted: hub or agent stopped
  dispatched --> interrupted: agent disconnected
  passed --> [*]
  failed --> [*]
  cancelled --> [*]
  interrupted --> [*]
```

## 8. Scheduling: which machine runs it

([Machines → Parallel runs](machines.md#parallel-runs))

```mermaid
flowchart TD
  J["Queued run"] --> T{"Target"}
  T -->|"a machine"| C1["that machine"]
  T -->|"several, Run on each"| C2["one copy per machine"]
  T -->|"several, Share as pool"| C3["any of them"]
  T -->|"Any free machine"| C4["every connected agent<br/>(the hub if none)"]
  C1 & C2 & C3 & C4 --> F{"Online, supports the engine,<br/>free slot?"}
  F -->|"yes"| P["Least loaded first<br/>(used / slots)"] --> S["Start: worker thread · grid session · job to agent"]
  F -->|"no"| W["Wait as queued<br/>re-checked when a run ends or a machine connects"]
  W --> F
```

## 9. One click, many runs

```mermaid
flowchart LR
  T["Test + data"] --> I1["Iteration 1<br/>Logitech"]
  T --> I2["Iteration 2<br/>HP, Firefox"]
  I1 --> E1["Playwright"] & E2["Selenium"]
  I2 --> E3["Playwright"] & E4["Selenium"]
  E1 & E2 & E3 & E4 --> Q["Queue: 4 runs, one batch_id"]
  Q --> M1["hub (local)"]
  Q --> M2["agent lab-linux"]
```

## 10. Finding the element for a step

([Architecture → Finding the element](architecture.md#finding-the-element-for-a-step))

```mermaid
flowchart TD
  S["Step: action + description"] --> C["Candidates<br/>Playwright: accessibility snapshot + DOM walk<br/>Selenium: DOM walk + Chrome accessibility tree"]
  C --> R["Rank: words · element kind · action fit ·<br/>exact names · context · ordinals · numbers"]
  R --> T{"Clear winner?"}
  T -->|yes| V["Verify native locators<br/>(exactly this element)"]
  T -->|"tie"| N["Next source settles it?"]
  N -->|yes| V
  N -->|no| AI{"AI on?"}
  AI -->|yes| PICK["AI picks"] --> V
  AI -->|no| FAIL["Fail, listing the tied elements"]
  V --> ACT["Highlight · act · screenshot · keep primary + 2 fallbacks"]
  C -.->|"not there yet"| WAIT["Retry · scroll · search virtual lists<br/>until the step timeout"] -.-> C
```

## 11. Replay and self-healing

([User guide → Replay](user-guide.md#replay-self-healing-and-the-library))

```mermaid
flowchart TD
  ST["Stored step<br/>primary locator + 2 fallbacks"] --> P{"Primary finds exactly one<br/>and it still fits the description?"}
  P -->|yes| ACT["Act"]
  P -->|no| FB{"A fallback does?"}
  FB -->|yes| H1["Act · mark self-healed"]
  FB -->|no| RE["Find it again from the description<br/>(diagram 10)"]
  RE -->|found| H2["Act · mark self-healed"]
  RE -->|"not found"| F["Step fails"]
  H1 & H2 --> NS["New script written with the repaired locator"]
```

## 12. Which AI answers

([Architecture → AI](architecture.md#ai-where-it-is-used-and-where-it-is-not))

```mermaid
flowchart TD
  CALL["AI needed: parse English · break a tie · find on canvas"] --> OWN{"Run owner set My AI?"}
  OWN -->|yes| MINE["Their subscription / key"]
  OWN -->|no| ORG{"Organisation AI allowed?"}
  ORG -->|yes| AUTO["Auto: Claude Code CLI → Cursor CLI → Copilot CLI →<br/>Anthropic key → LiteLLM"]
  ORG -->|no| NONE
  AUTO -->|"none answers"| NONE["No AI: rules and heuristics"]
  AG["On an agent"] -->|"own CLI or key?"| LOC["use it"]
  AG -->|"no"| HUB["send the call to the hub (same rules)"]
```

## 13. Sign-in and SSO

([User guide → Accounts](user-guide.md#accounts-roles-and-projects))

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant H as Hub
  participant I as Identity provider (OIDC)
  alt username + password
    B->>H: POST /api/auth/login
    H->>H: scrypt check · lockout after repeated failures
  else company SSO
    B->>H: GET /api/auth/sso/start
    H-->>B: redirect (state, nonce, PKCE)
    B->>I: sign in
    I-->>B: redirect to /api/auth/sso/callback?code
    B->>H: callback
    H->>I: exchange code, check token signature
    H->>H: match or create the account (allowed domains)
  end
  H-->>B: HttpOnly session cookie (only its hash is stored)
  B->>H: API calls with the cookie + X-Requested-With on writes
```

## 14. Who sees what: roles and projects

```mermaid
flowchart LR
  subgraph Roles
    AD["admin<br/>everything"]
    TE["tester<br/>create · run · replay · library · own logins"]
    VI["viewer<br/>read runs, reports, videos"]
  end
  subgraph Projects
    DF["Default<br/>(everyone)"]
    P1["Demo shop<br/>(members)"]
    P2["Payments<br/>(members)"]
  end
  AD --> DF & P1 & P2
  TE -->|member| P1
  TE --> DF
  VI --> DF
  SH["Shared by all projects:<br/>machines · grids · organisation AI"]
```

## 15. Secrets: where they go

```mermaid
flowchart LR
  IN["Secret test data<br/>(lock icon)"] --> R["Run"]
  R -->|"masked ••••••"| LIVE["Live view · screenshots · video (dots)"]
  R -->|"masked"| FILES["report · data.json · script.json · scripts"]
  R -->|"encrypted (Fernet)"| VAULT[("run_secrets")]
  VAULT -->|"Replay, owner only"| R
  FILES -->|"exported project"| ENV[".env: AIAUTO_SECRET_name per secret"]
  TOK["Tool tokens · AI keys · SSO secret"] -->|encrypted| DB[("connections · user_ai · settings")]
  KEY["aiauto.key or AIAUTO_SECRET_KEY"] -.-> VAULT & DB
```

## 16. Test design and write-back

([Integrations](integrations.md))

```mermaid
flowchart LR
  ST["Story / bug<br/>Jira · Azure · pasted"] --> CP["Context pack<br/>criteria · docs · code fields & limits"]
  CP --> TD["Rules (+ optional AI ideas)<br/>positive · negative · boundary · role · regression"]
  TD --> RV["Review · edit · approve"]
  RV --> RUN["Runs"]
  RUN --> WB["Zephyr / Xray cycles & executions ·<br/>Jira comment · transition · bugs"]
  RUN --> LOC["Pasted story: kept on the hub until Publish"]
```

## 17. Data model

([Database](database.md))

```mermaid
erDiagram
  projects ||--o{ project_members : has
  users ||--o{ project_members : "member of"
  users ||--o{ auth_sessions : "signed in"
  users ||--o| user_ai : "My AI"
  projects ||--o{ runs : contains
  runs ||--o{ steps : has
  runs ||--o| run_secrets : "replay secrets"
  projects ||--o{ library : contains
  projects ||--o{ sessions : contains
  projects ||--o{ connections : tools
  projects ||--o{ designs : contains
  designs ||--o{ design_runs : "case runs"
  runs ||--o| design_runs : "ran a case"
  designs ||--o{ sync_log : log
```

## 18. From description to portable script

([Architecture → First run, replay, script, project](architecture.md#first-run-replay-generated-script-exported-project))

```mermaid
flowchart LR
  E["Plain English + data"] -->|"first run"| R1["Run · report · video"]
  R1 --> S["script.json · test_playwright.py ·<br/>test_selenium.py · autopilot_runtime.py"]
  S -->|"Replay (self-heals)"| R2["Run"] -->|"repaired"| S
  S -->|"Export project"| X["run.py · config · data · .env"]
  X -->|"python run.py — no hub, no AI"| CI["CI: JUnit XML · report · video"]
```

## 19. Live view: how frames reach you

```mermaid
flowchart LR
  BR["Browser being tested"] -->|"screencast / screenshots"| RN["Runner<br/>(hub or agent)"]
  RN -->|"live_frame, step_shot<br/>(not stored)"| HB["Hub"]
  RN -->|"step events"| HB
  HB -->|"stored"| DB[("SQLite")]
  HB -->|"/ws/ui, filtered by project"| UI["Web app live view"]
```

## 20. CI pipelines

([Development → CI](development.md#ci))

```mermaid
flowchart LR
  PUSH["git push"] --> XP["cross-platform.yml"]
  PUSH --> RS["real-sites.yml"]
  XP --> X1["Windows · macOS · Linux<br/>browser matrix · agents · unit tests"]
  XP --> X2["setup.sh / setup.bat · demo data · start scripts"]
  RS --> R1["Windows: Chrome, Edge"]
  RS --> R2["macOS: Chrome, WebKit · Safari"]
  RS --> R3["Linux: Chrome, Firefox"]
  R1 & R2 & R3 --> REP["visible browsers, AI off:<br/>first run · replay · exported project<br/>REPORT.md on the summary page"]
```
