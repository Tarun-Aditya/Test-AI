# Automation: schedules, CI hooks, alerts, flaky tests, and more kinds of steps

Everything here works on the hub, on the same saved scripts and runs you already have. In the web app it is the **Automation** page in the sidebar,
with four tabs (Schedules, CI hooks, Alerts, Flaky tests); a library script's card has a **Schedule** button, and Overview shows a **Flaky tests** panel when there are any.
Each piece also has an API ([endpoints](api.md#schedules-ci-hooks-notifications-flaky-tests)). In Studio, the step editor offers the new step kinds, and
**Settings → Accept the new look in visual checks** accepts a changed baseline for that run.

## Run on a schedule

Save a script to the library (from a run, a recording, or `POST /api/library`), then:

```bash
curl -X PUT $HUB/api/schedules -H 'Content-Type: application/json' -H 'X-Requested-With: autopilot' \
  -d '{"name": "Nightly checkout", "library": "checkout", "cron": "30 2 * * *", "agent": "local"}'
```

* `cron`: minute hour day-of-month month day-of-week, with lists (`1,15`), ranges (`mon-fri`), steps (`*/15`) and `@hourly`, `@daily`, `@weekly`. Times are the hub's clock.
* The hub checks every 20 seconds and starts each schedule once in its minute, even across a restart. A schedule that cannot start (script deleted, no such machine) records why in `last_error`.
* The run is made as the person who created the schedule, in the schedule's project, with that person's saved sessions and AI settings.
* `POST /api/schedules/{id}/run` runs it now. `enabled: false` pauses it.

## Run from CI

`POST /api/hooks` `{"library": "checkout"}` returns a URL once. A pipeline calls it (no sign-in; the secret is in the URL, so keep it in the CI's secret store):

```bash
curl -X POST "$HUB/api/hooks/run/$TOKEN" -d '{"data": {"base_url": "https://staging.example.com"}}' -H 'Content-Type: application/json'
```

It answers `{run_ids}`; poll `GET /api/runs/{id}` for the result, as in the [CI example](api.md#example-run-a-test-from-ci-and-fail-the-build-if-it-fails).

## Alerts

Channels (admin): Slack incoming webhook, Microsoft Teams webhook, any webhook (JSON of the run), or email (set `AIAUTO_SMTP_HOST`, `AIAUTO_SMTP_PORT`, `AIAUTO_SMTP_USER`, `AIAUTO_SMTP_PASSWORD`, `AIAUTO_SMTP_FROM`).
`on` is `failed` (default), `always`, or `recovered` (a pass after a failure). `project` limits a channel to one project. Set `AIAUTO_PUBLIC_URL`
(e.g. `https://autopilot.example.com`) so the message links to the run. `POST /api/notifications/{id}/test` sends a clearly-marked test message. A channel that is
down never affects a run.

## Flaky tests

`GET /api/flaky` reads the last runs of each test (per data row). A test is **flaky** when it both passes and fails over its recent runs and the
result keeps flipping (at least two flips in at least four runs). A test that broke once and stayed broken is *failing*, not flaky. Each row has
`pass_rate`, `flips`, `history` (oldest first) and how often replays had to heal.

## More kinds of steps

All of these work from English, in the editor, from the API, and in the Playwright and Selenium scripts they generate (the scripts stay standalone).

| Step | In English | Notes |
|---|---|---|
| **API call** | `Call GET /api/health expecting status 200 containing "ok"` | `POST /api/orders expecting 201`. In JSON: `{"headers": {}, "body": {...}, "expect_status": 200, "expect_contains": "ok", "expect_json": {"data.id": 5}, "save": {"token": "data.token"}}`. `save` makes `${token}` available to later steps. Relative addresses use the page's origin |
| **Mock or block the network** | `Mock /api/cart to return 500` · `Mock "/api/items" to return 200 with body '{"items": []}'` · `Block requests to analytics` · `Stop mocking` | Answers the page's `fetch` and XHR calls whose URL contains the text (`*` wildcards; `re:` + a regular expression). The newest rule for a URL wins. Chrome and Edge keep it across page loads; in Firefox and Safari (Selenium) set it again after a navigation. Images, scripts and page loads are not intercepted |
| **Accessibility scan** | `Verify the page has no accessibility issues` · `Check there are no serious accessibility issues` | Built-in rules (not axe): image alt, button and link names, form labels, `lang`, page title, duplicate ids, positive tabindex, empty headings, focusable `aria-hidden`. Impacts: minor < moderate < serious < critical; the step fails at the level you name (default serious). Looks into open shadow roots; not iframes; no colour-contrast check |
| **Visual comparison** | `Verify the page looks the same as home` · `... as baseline named home within 0.5%` | The first run saves the screenshot as the baseline (the step passes and says so). Later runs compare pixels; a changed pixel is a colour channel moving by more than 24; `within N%` is the share of pixels allowed to differ (default 0.1%). A failure writes `screenshots/visual_<name>_diff.png`. To accept a change on purpose run with `config.update_baselines: true` (scripts: `UPDATE_BASELINES=1`). Baselines live next to the artifacts (`baselines/<test>/`, or `config.baselines_dir` / `AIAUTO_BASELINES`) **on the machine that runs the test**; scripts use `./baselines` (`BASELINES_DIR`). Same browser, size and fonts give the same pixels, so compare on the same machine |
| **One-time passwords** | type `${totp:seed}` | `seed` is the authenticator secret (the base32 text behind the QR code), kept as a secret data value. The code is worked out at the step (RFC 6238, 6 digits, 30 s) and masked everywhere |
| **Codes from an inbox** | type `${code:inbox}` | `inbox` is a URL that shows the latest message: a mail catcher (Mailpit `http://localhost:8025/api/v1/message/latest`, MailHog), a webhook inbox or an SMS gateway page. The step waits up to 30 s for a 4-8 digit code, preferring one after "code", "otp" or "verification" |
| **Saved logins** | *(already there)* | Log in once, save the session, reuse it: `config.session` / `save_session` ([user guide](user-guide.md)) |

Needs `Pillow` (in `requirements.txt`) for the pixel comparison; without it a visual step only tells you whether the screenshots are byte-identical.
