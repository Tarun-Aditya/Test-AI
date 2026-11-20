# Feature tour

Screenshots and recordings of every part of Autopilot. For the whole story in one narrated video, see the
**[end-to-end demo](demo.md)**. All of them come from a hub loaded with
[demo data](database.md#demo-data) (`./setup.sh --demo`), with AI off, so you'll see exactly this after setup.
The animated previews play here; each links to a full-quality MP4.

- [Write a test and watch it run](#write-a-test-and-watch-it-run)
- [A business paragraph instead of steps](#a-business-paragraph-instead-of-steps)
- [Data-driven runs, both engines, several machines](#data-driven-runs-both-engines-several-machines)
- [Every run: steps, video, scripts, report, replay](#every-run-steps-video-scripts-report-replay)
- [What a run records](#what-a-run-records)
- [When a test fails](#when-a-test-fails)
- [Overview and run history](#overview-and-run-history)
- [Test design from a story](#test-design-from-a-story)
- [Library and saved logins](#library-and-saved-logins)
- [Machines and agents](#machines-and-agents)
- [Admin: AI, integrations, sign-in, users, projects](#admin-ai-integrations-sign-in-users-projects)
- [My AI](#my-ai)
- [Dark theme and phones](#dark-theme-and-phones)

## Write a test and watch it run

Type the test in plain English, press **Break down into steps** to see how it was understood, then **Run test**. The
browser streams into the page with each element boxed in green, and each step appears with its screenshot, its
native locator and how the element was found.

[![Writing a test in Studio and watching it run live](media/videos/studio-run.gif)](media/videos/studio-run.mp4)

*[Full video (MP4, 33 s)](media/videos/studio-run.mp4)*

| The steps, editable before running | Running live |
|---|---|
| ![Studio with the test broken into steps](media/screens/studio-finished.png) | ![Live view while the test runs](media/screens/studio-live.png) |

## A business paragraph instead of steps

Paste the scenario as an analyst writes it: third person, no quotes, several fields in one sentence, expected results in
plain words. It is split into steps with or without AI, secrets are lifted into masked Test data, and anything it can't
know (the page address, paraphrased messages, expectations with no concrete text) is listed under *Check before you
run*. Details: [User guide](user-guide.md#a-business-paragraph-instead-of-steps).

![A business paragraph split into steps](media/screens/paragraph-breakdown.png)

## Data-driven runs, both engines, several machines

`${placeholders}` in the test, one row per iteration under **Test data** (each row can pick its own browser), **Both**
engines, and two machines sharing the work: one click starts 2 iterations × 2 engines = 4 runs, spread over the hub
and an agent.

[![Iterations on both engines across two machines](media/videos/iterations.gif)](media/videos/iterations.mp4)

*[Full video (MP4, 36 s)](media/videos/iterations.mp4)*

![The iterations grid in Studio](media/screens/studio-iterations.png)

## Every run: steps, video, scripts, report, replay

Each run has its own page: every step with its screenshot, the recording, the generated Playwright and Selenium
scripts and every file, the single-file report, the data and options used, and **Replay** (the generated script, on
any machine, engine or browser, with the same or new data).

[![Touring a run's page and replaying it](media/videos/run-page.gif)](media/videos/run-page.mp4)

*[Full video (MP4, 56 s)](media/videos/run-page.mp4)*

| Steps | Scripts & files |
|---|---|
| ![A run's steps with screenshots](media/screens/run-overview.png) | ![The generated scripts and run folder](media/screens/run-code.png) |
| **Video** | **Report** |
| ![The run's video](media/screens/run-video.png) | ![The report inside the app](media/screens/run-report.png) |
| **Data & settings** | **Replay** |
| ![Data and settings of the run](media/screens/run-data.png) | ![Replay options](media/screens/run-replay.png) |

## What a run records

Every run records its browser, highlight boxes included: this is the `video.webm` the demo's cart test wrote
(shown here as a GIF). The same recording is embedded in `report.html`, a single file you can send to anyone.

[![The recording a run makes](media/videos/run-recording.gif)](media/videos/run-recording.mp4)

![report.html opened on its own](media/screens/report-html.png)

## When a test fails

The step that failed, what it expected and what the page showed, with the screenshot at that moment. Here the
demo's test design caught the sign-up form's planted bug: age 100 is accepted.

![A failed run showing the failing step and its screenshot](media/screens/run-failed.png)

## Overview and run history

| Overview: pass rate, what needs attention, what's running, story coverage | Runs: grouped by launch, filters, replay |
|---|---|
| ![Overview of the Default project with one failure](media/screens/overview-default.png) | ![Run history](media/screens/runs.png) |

## Test design from a story

Paste a story (or pick one from Jira): Autopilot reads its acceptance criteria and the code that implements it (here,
a commit naming `DEMO-1` in a Local Git repository), and writes positive, negative and boundary cases with rules.
Approve, run, and send the results to your tools.

[![Designing test cases from a pasted story](media/videos/test-design.gif)](media/videos/test-design.mp4)

*[Full video (MP4, 12 s)](media/videos/test-design.mp4)*

![The test design with its 13 cases, coverage and results](media/screens/test-design-full.png)

## Library and saved logins

| Library: saved scripts, run on any engine or machine | Saved logins: start runs already signed in |
|---|---|
| ![Library](media/screens/library.png) | ![Saved logins](media/screens/saved-logins.png) |

## Machines and agents

The hub, every agent and every grid, online or not, with its engines, browsers and slots, and one token per machine.
**Add a machine** shows the exact commands. Setup for each OS: [Deployment](deployment.md#installing-an-agent).

[![Machines page and the add-a-machine instructions](media/videos/machines.gif)](media/videos/machines.mp4)

![Machines with an agent online](media/screens/machines.png)

## Admin: AI, integrations, sign-in, users, projects

| Organisation AI | Integrations (tokens only) |
|---|---|
| ![Organisation AI settings](media/screens/admin-ai.png) | ![Integrations](media/screens/admin-integrations.png) |
| **Sign-in (SSO)** | **Users and roles** |
| ![SSO settings](media/screens/admin-sso.png) | ![Users](media/screens/admin-users.png) |
| **Projects** | **Sign-in page** |
| ![Projects](media/screens/admin-projects.png) | ![Sign-in page](media/screens/sign-in.png) |

## My AI

Each person can use their own Claude, Copilot or Cursor subscription, or a key, for their own runs.

![My AI](media/screens/my-ai.png)

## Dark theme and phones

| Dark theme | Phone: overview | Phone: a run |
|---|---|---|
| ![A run in the dark theme](media/screens/run-overview-dark.png) | ![Overview on a phone](media/screens/phone-overview.png) | ![A run on a phone](media/screens/phone-run.png) |

---

How these were made: a script signs in to a demo hub with Playwright, opens each page and records Chrome's screen
while it writes a test, runs it, opens a run, designs cases and so on. Re-make them after UI changes the same way.
