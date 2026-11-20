"""The voice-over, one entry per scene (the recorder drives the browser for each)."""
SCENES = [
    ("intro", "This is Autopilot, a test automation platform. In the next few minutes we'll go end to end: write a test in "
              "plain English, run it on Playwright and Selenium, replay the script it generates, design test cases from a "
              "user story, and run everything across machines."),
    ("overview", "Everything happens in the web app. After signing in, the overview shows the pass rate, what needs "
                 "attention, what is running right now, and recent runs."),
    ("write", "In Studio, we describe a test the way we would explain it to a colleague. No code and no selectors. Open the "
              "shop, search for a wireless mouse, open the first Logitech result, add two to the cart, and check the subtotal."),
    ("breakdown", "Break down into steps shows exactly how it was understood: one action per step, the element described in "
                  "words, and the value it will use. You can edit the steps before running."),
    ("run", "Now we run it. The browser streams into the page live. Each element is found on the live page, outlined in "
            "green, and acted on with a native Playwright locator. Every step gets a screenshot, and the locator that "
            "found it."),
    ("runpage", "Every run has its own page. The steps with their screenshots. The video recording. The generated "
                "Playwright and Selenium scripts, with the whole run folder. A single file HTML report. And the data and "
                "settings that were used."),
    ("replay", "Replay runs the generated script in script mode. There is no parsing and no AI, just the stored native "
               "locators, so it is fast. If the page has changed and a locator breaks, the engine heals it and writes a "
               "new script. Here we replay on Selenium instead."),
    ("iterations", "Tests are data driven. Placeholders in the test take their values from a grid, one row per run, and "
                   "each row can use its own browser. We choose both engines, and share the work between the hub and a "
                   "remote agent. One click, four runs in parallel."),
    ("design", "Autopilot can also write the tests. Paste a user story, or pick one from Jira. It reads the acceptance "
               "criteria and the code behind them, here a Git commit for the sign up form, and designs positive, negative "
               "and boundary cases, with rules alone. No AI needed."),
    ("failure", "All thirteen cases were approved and run. One failed. The form accepts an age of one hundred, but the story "
                "says eighteen to ninety nine. The run shows the failing step, what was expected, and the page at that "
                "moment. Results can go back to Zephyr, Xray and Jira, including a bug with the screenshot."),
    ("machines", "Tests run wherever you need them. The hub runs tests itself, and any Windows, Mac, Linux or Docker "
                 "machine joins as an agent with one command and its own token. Agents connect out to the hub, so they "
                 "work behind firewalls. Cloud grids such as BrowserStack plug in the same way."),
    ("admin", "For teams, there are accounts and roles, company single sign on, projects that keep each team's work apart, "
              "tokens for Jira, GitHub and test tools, and the AI of your choice: your Claude, Copilot or Cursor "
              "subscription, an API key, or none at all."),
    ("outro", "Everything Autopilot produces is yours. Plain Playwright and Selenium scripts. An exported project that runs "
              "in any CI without the hub. And every result in a SQLite database, with reporting views. Set it up with one "
              "command, and find the full guide in the docs folder."),
]
