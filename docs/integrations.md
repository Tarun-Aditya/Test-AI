# Integrations: from story to test cases to results

Autopilot can write test cases from a story or bug, run them, and send the results back to your tracker and test
tool. Every tool connects with a **token**: there is no OAuth app to register.

- [The flow](#the-flow)
- [Connecting your tools](#connecting-your-tools)
- [What is read](#what-is-read)
- [How cases are designed](#how-cases-are-designed)
- [What is written back](#what-is-written-back)
- [No tracker yet: paste a story, publish later](#no-tracker-yet-paste-a-story-publish-later)
- [Try it with fake tools](#try-it-with-fake-tools)

## The flow

1. **Test design** (left menu) → search your tracker, or **Paste a story**.
2. Autopilot builds the story's **context pack**: acceptance criteria, linked documents, and the code that implements
   it (form fields, limits, buttons, routes).
3. **Design test cases**: positive, negative, boundary, role and (for bugs) regression cases, written with rules (no
   AI needed). Tick **AI ideas** to also ask your AI for cases the rules can't see; those are marked `ai`.
4. Review and edit the cases (plain English, the same format Studio runs), then **Approve**.
5. **Run** (or **Push to test tool**, then run). Runs use the machine, engine and options in the top bar.
6. Results go back as each run finishes: test executions, a comment on the story, a status transition, a bug for each
   new failure. Everything sent is listed in the design's **sync log**.

The coverage table shows each acceptance criterion with its cases, and which criteria still lack a positive or
negative case. **Re-check story** tells you when the story, its pages or its code changed after the cases were
written.

## Connecting your tools

**Admin → Integrations**, per project. Tokens are encrypted on the hub, never sent back to the browser (shown as
`••••••••`), and only admins can change them. Give each token the least access it needs.

| Tool | Role | Credential |
|---|---|---|
| **Jira Cloud** | stories and bugs, write-back | Atlassian e-mail + **API token** (id.atlassian.com → Security → API tokens), sent as Basic auth |
| **Jira Data Center** | stories and bugs, write-back | **Personal access token** (profile → Personal access tokens), sent as Bearer |
| **Confluence** Cloud / Data Center | documents | the same as Jira |
| **GitHub** / GitHub Enterprise | code and pull requests | Fine-grained **personal access token** with read access to Contents and Pull requests |
| **Local Git repository** | code | None stored: the path of a clone **on the hub machine**; `git` uses that clone's own credentials, and only if *git fetch before reading* is on. In Docker, mount it: `-v /home/me/src/shop:/repos/shop` |
| **Zephyr Scale** (Cloud) | test cases and results | Zephyr **API access token** (Jira → Apps → Zephyr → API Access Tokens), Bearer |
| **Xray Cloud** *(beta)* | test cases and results | **API key**: client id + client secret (Xray → Global Settings → API Keys); exchanged for a 24-hour token automatically |
| **Azure DevOps** *(beta)* | work items, test results | **Personal access token** (Work Items read/write, Test Management read/write) |
| **Custom tool** *(beta)* | anything with JSON | A results URL (and optionally a stories URL) with an optional token header |

**Test connection** makes one real, read-only call. Set `AIAUTO_PUBLIC_URL` so links in comments and bugs point at
your hub. Planned: GitHub Issues, Zephyr Squad, TestRail, OpenText ALM / Octane, GitLab, Bitbucket.

## What is read

- **Jira / Azure DevOps**: summary, description, acceptance criteria (a custom field, a field named like
  "Acceptance criteria", the description's section, or Given/When/Then lines), for bugs the steps to reproduce,
  expected and actual result, links, and the last comments.
- **Confluence**: pages linked from the story, then pages that mention its key or title in the configured spaces.
- **GitHub / local Git**: commits and pull requests that mention the key; their diffs and the UI files they change,
  read for form fields (label, `required`, `type`, `maxlength` / `minlength`, `min` / `max`, `pattern`, zod / yup rules),
  buttons and routes. Without a key, the UI files that best match the story's words.

Everything is size-limited and secrets in the text (tokens, passwords) are scrubbed before it is stored or sent to an
AI.

## How cases are designed

From the context pack, with rules:

| Case type | Example from a sign-up form |
|---|---|
| positive | Valid details → "Account created" |
| negative | Each required field left empty; an invalid e-mail |
| boundary | Age 18 and 99 accepted, 17 and 100 rejected; username at 20 characters accepted, 21 cut or rejected; password at 8 accepted, 7 rejected |
| role | The same flow as each role named in the story |
| regression (bugs) | The steps to reproduce, expecting the expected result |

Each case traces back to its acceptance criterion and quotes the error message the criteria give. Cases that need a
value nobody gave (a real test account, say) say so in their notes and can't be approved until you fill it in.

## What is written back

| Where | What |
|---|---|
| **Zephyr Scale** | Test cases (with steps, folder, linked to the story), one test cycle per launch, one execution per run with step results |
| **Xray** *(beta)* | One Test Execution per launch; each case's Xray test is created on first import (steps, linked to the story, label `autopilot-case-<id>`) and reused afterwards |
| **Azure DevOps** *(beta)* | An automated test run per launch with one result per case; a comment, a state change and de-duplicated bugs (screenshot attached) on the work item |
| **Custom** *(beta)* | JSON events to your results URL: `test_case`, `result`, `comment` |
| **Jira** | One comment on the story with every result and report link; the pass / fail transition you configured; a bug for each new failure (linked, screenshot attached). The same failure again adds a comment to that bug instead of opening a new one |

## No tracker yet: paste a story, publish later

- **Paste a story** in Test design: Markdown, Jira wiki markup or plain text. Put criteria under an "Acceptance
  criteria" heading, as bullets, or as Given/When/Then; for bugs, "Steps to reproduce" and "Expected result". Add its
  key if it has one, and any spec text.
- Connect a **Local Git repository** (or GitHub) so the design can read the form's fields from the code.
- Design, approve, run: a pasted story's results **stay on the hub** (the design shows *local · N results not
  published*).
- Later, when the tools are connected: **Link** the design to the story's Jira key (needed for the comment, status and
  bugs), then **Publish**: the approved cases go to the test tool, the latest result of each becomes one test cycle,
  and the story gets its comment, transition and bugs. Publish again later to send newer results.
- **Send results after each run** (a switch on the design) is on for stories from the tracker and off for pasted ones.

The [demo data](database.md#demo-data) contains exactly this: a pasted story (`DEMO-1`), a Local Git repository with
the sign-up page, 13 designed cases and their runs, one of which catches the form's planted bug.

## Try it with fake tools

```bash
python -m tests.fake_tools 8765     # fake Jira, Confluence, GitHub, Zephyr Scale and Xray; prints URLs and tokens
```

Connect them in Admin → Integrations with the printed URLs and tokens, design cases for `SHOP-1`, and run them with
the base URL `http://localhost:8000/demo/signup` (the cases open `${base_url}/signup.html`). The form has one
deliberate bug (age 100 is accepted) for the flow to find.
