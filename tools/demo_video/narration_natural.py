"""The same walkthrough as narration.py, written to be spoken: contractions, short sentences, one idea at a time.
Same scene names, so record.py's timeline and build.py work with either script."""
SCENES = [
    ("intro", "Hi! This is Autopilot. It's a test automation tool where you just describe a test in plain English, "
              "and it runs it for real. Let's go from start to finish: write a test, run it, replay it, "
              "design tests from a user story, and spread the work across machines."),
    ("overview", "Everything lives in one web app. Once you're signed in, the overview shows your pass rate, "
                 "what needs attention, what's running right now, and your latest runs."),
    ("write", "Over in Studio, I just describe the test, the way I'd explain it to a colleague. No code, no selectors. "
              "Open the shop, search for a wireless mouse, open the first Logitech result, add two to the cart, "
              "and check the subtotal."),
    ("breakdown", "If I want to see how it understood me, I click Break down into steps. "
                  "One action per step, the element in words, and its value. "
                  "I can edit any of it before I run."),
    ("run", "Now let's run it. The browser streams right into the page. Watch the green box: that's the element it found, "
            "and it's clicking and typing with real Playwright locators. Every step gets a screenshot and its locator."),
    ("runpage", "Every run gets its own page. Here are the steps with their screenshots, the video of the run, "
                "the generated Playwright and Selenium scripts, a single-file HTML report, "
                "and the data and settings that were used."),
    ("replay", "Replay runs the generated script. No parsing, no AI, just the saved locators, so it's fast. "
               "If the page changes and a locator breaks, it heals itself and saves a new script. "
               "Here, I'm replaying it on Selenium instead."),
    ("iterations", "Tests can be data driven. Placeholders take their values from a grid, one row per run, "
                   "and every row can use its own browser. I'll pick both engines and share the work "
                   "between this machine and a remote agent. One click, four runs, in parallel."),
    ("design", "Autopilot can write the tests, too. Paste a user story, and it reads the acceptance criteria "
               "and the code behind them. Here that's a Git commit for the sign up form. "
               "Then it designs positive, negative and boundary cases, using rules alone. No AI needed."),
    ("failure", "All thirteen cases ran, and one failed. The form accepts an age of one hundred, "
                "but the story says eighteen to ninety nine. The run shows the failing step, what it expected, "
                "and the page at that moment. Results can flow back to Zephyr, Xray and Jira, "
                "with a bug and the screenshot attached."),
    ("machines", "Tests can run anywhere. The hub runs them itself, and any Windows, Mac, Linux or Docker machine "
                 "joins as an agent with one command. Agents connect out to the hub, so they work behind firewalls. "
                 "Cloud grids like BrowserStack plug in the same way."),
    ("admin", "For teams, there are accounts and roles, single sign on, and projects that keep each team's work apart. "
              "You connect Jira, GitHub and your test tools with tokens, "
              "and you choose the AI: your own Claude, Copilot or Cursor subscription, an API key, or none at all."),
    ("outro", "And it's all yours to keep. Plain Playwright and Selenium scripts. A project you can run in any CI. "
              "And every result in a SQLite database. Setup is one command, and the full guide is in the docs folder. "
              "Thanks for watching!"),
]
