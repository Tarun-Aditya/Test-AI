# Chrome extension

Open the website you want to test, paste your scenario, press **Run on this tab**. The scenario runs on the page you have open, with real clicks and typing in your own browser. When the page opens a new tab (a `target="_blank"` link, `window.open`), the run follows it and carries on there.

![Side panel after a run that followed a new tab](media/extension-panel-newtab.png)

## Install

1. Start the hub (`./start.sh` or `start.bat`, see [Getting started](getting-started.md)). The extension is the hands; the hub is the brain (it understands the text and ranks elements).
2. Open `chrome://extensions`, turn on **Developer mode**, choose **Load unpacked** and pick the `extension/` folder. (Or build a zip with `python tools/build_extension.py --zip` → `dist/autopilot-extension.zip`.)
3. Pin the extension and click its icon: the side panel opens.
4. If your hub is not at `http://localhost:8000`, or it needs a sign-in, open the gear (**Settings**), enter the address and sign in.

![Settings](media/extension-options.png)

Works in Chrome, Edge and other Chromium browsers (Windows, macOS, Linux). It is tested in real Chromium in CI; try it in your own Chrome and tell us what you find.

## Use

* **The tab is picked for you**: the tab you were last on that is a normal website. Open a different site and the panel switches to it.
* Paste the scenario as a paragraph of business language: no quotes or commas needed. **Preview steps** shows how it was split, what was left out as background and what to double-check. **Run** runs it (a previewed plan is run as shown).
* Passwords and other secrets found in the text are lifted out, masked on the page, in screenshots and in the panel, and never echoed back by the hub. The optional **Data** box takes `key=value` lines (`password*=...` marks a secret).
* **Stop** ends the run at the next step. Closing the panel does not stop it; reopening shows where it is.

![Business paragraph, split into steps, run on the page](media/extension-panel-signup.png)

## Record a flow, replay it exactly

Switch the panel to **Record a flow**, press **Record on this tab** and use the site like a person would: click, type, choose, tick, press Enter, open links in new tabs. Every action becomes a step as you go, in the same format as a typed scenario.

![Recorded steps](media/extension-recorder.png)

* **Nothing is guessed from pixels.** The recorder reads the page structure: for each action it stores the element's role, name, label, text, test id and a list of locators (the same fingerprint the Playwright and Selenium engines use), and describes it in English ("Place order button").
* **Replay is the same engine** as a typed scenario. Each step tries the locators recorded for it first. If the page changed and none matches exactly one visible element, it falls back to finding the element by its description (the hub ranks the candidates), like the app's self-healing replay.
* **Passwords** are lifted into data (`${password}`), masked on screen and never stored in the steps. Saving to the hub leaves the secret value out.
* **Edit**: remove a step with ✕ after stopping. **Add a check** while recording ("the page should show …", or highlight text on the page and use **Use selected text**), because a click stream can't tell what the page was supposed to show.
* **New tabs** opened by a recorded tab join the recording and are followed on replay; going back to an earlier tab becomes a "switch to tab" step.
* **Copy as English** gives the steps as plain sentences; **Save to hub** puts them in the library, from where the app runs them (and exports Playwright / Selenium code) like any script; **Replay on this tab** runs them right here. For a repetitive task, record once and run it with data rows in the app.
* **Import a Chrome Recorder file** (DevTools → Recorder → Export → JSON) and it becomes the same steps, with the selectors Chrome recorded as the stored locators and fallbacks.

What it can't do: a video file (webm / mp4) has no page structure in it, so it can't be turned into steps without a vision model; record the flow instead. Scrolling, file uploads and mouse paths aren't recorded.

## How it follows tabs

The extension watches `tabs.onCreated` (with the opener), `onUpdated` and `onActivated`. When a step opens a tab from the one under test, the run attaches to the new tab, notes it as **Tab 2** in the panel, and later steps run there. Each step shows which tab it ran in.

## What runs where

| Hub (your Autopilot server) | Extension (your browser) |
|---|---|
| Understands the paragraph (`/api/parse`) | Reads the page (every frame) into candidates |
| Ranks elements, optional AI tie-break (`/api/locate`) | Real mouse and keyboard through the Chrome debugger |
| Holds secrets and masking rules | Waits, handles dialogs, takes screenshots, checks page text |

The element finder is generated from the same Python source the Playwright and Selenium engines use (`python tools/build_extension.py`; `--check` fails if the generated files are stale), so the extension finds, boxes and masks the same way.

## Limits

* Chrome shows a "… is debugging this browser" bar while a run is active. That is how real input is sent.
* `chrome://` pages and the Chrome Web Store cannot be automated; `file://` pages need **Allow access to file URLs** on the extension's page.
* Cross-origin iframes (payment widgets): clicks and typing use in-page events there, not trusted browser input.
* Closed shadow roots are only visible if you tick **Open closed shadow roots** in Settings (reload the page after).
* File upload is not supported yet; drag and drop only works where the page listens to mouse events.
* The hub must be reachable from the browser.
* With AI off the paragraph parser is rule based; wording it cannot place is reported as *not understood* instead of guessed.

## Tests

`python -m pytest tests/test_extension.py tests/test_recorder.py` loads the extension into real Chromium and covers: a business paragraph on an open page, auto-picking the tab, new tab and popup following, hover, native dialogs, multi-page flows, secrets, cross-origin frames, closed shadow roots, hub sign-in, Stop and the error messages.
