"""Core data model: a test is a list of Steps; a run produces StepResults."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Action(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE = "type"            # clear + fill
    APPEND = "append"        # type without clearing
    PRESS = "press"          # keyboard key, e.g. Enter
    HOVER = "hover"
    SELECT = "select"        # <select> option (value may be "A|B" for multi-select)
    CHECK = "check"
    UNCHECK = "uncheck"
    DRAG = "drag"            # target = source element, value = destination element description
    SCROLL = "scroll"
    WAIT = "wait"            # seconds, or until target visible
    PAUSE = "pause"          # headed runs: give a human N seconds (e.g. SSO / MFA login)
    ASSERT_TEXT = "assert_text"        # page / element contains text
    ASSERT_NO_TEXT = "assert_no_text"  # page / element does NOT contain text (stays absent while the page settles)
    ASSERT_VISIBLE = "assert_visible"
    ASSERT_URL = "assert_url"
    ASSERT_TITLE = "assert_title"
    ASSERT_DIALOG = "assert_dialog"    # last alert/confirm/prompt text contains value
    DIALOG = "dialog"                  # value accept|dismiss  -> policy for the next dialogs
    SCREENSHOT = "screenshot"
    BACK = "back"
    REFRESH = "refresh"
    UPLOAD = "upload"
    SWITCH_TAB = "switch_tab"          # value: index (0 = first) or "last"


# Actions that operate on an element and therefore need a locator.
ELEMENT_ACTIONS = {
    Action.CLICK, Action.DOUBLE_CLICK, Action.RIGHT_CLICK, Action.TYPE, Action.APPEND,
    Action.HOVER, Action.SELECT, Action.CHECK, Action.UNCHECK, Action.ASSERT_VISIBLE,
    Action.UPLOAD, Action.DRAG,
}


class Locator(BaseModel):
    """An engine-native, replayable way to find an element.

    strategy (native mapping):
      testid       Playwright get_by_test_id      / Selenium By.CSS_SELECTOR [data-testid=..]
      id           Playwright locator('#id')      / Selenium By.ID
      role         Playwright get_by_role(role, name=value)  / Selenium By.LINK_TEXT or By.XPATH
      label        Playwright get_by_label        / Selenium By.XPATH via <label>
      placeholder  Playwright get_by_placeholder  / Selenium By.CSS_SELECTOR [placeholder=..]
      text         Playwright get_by_text         / Selenium By.XPATH normalize-space()
      alt, title   Playwright get_by_alt_text / get_by_title / Selenium CSS attribute
      name         [name=..]                      / Selenium By.NAME
      css, xpath   raw selectors
    frames: CSS selectors of the iframes (outermost first) that contain the element.
    shadow: CSS selectors of the shadow hosts (outermost first), each relative to the previous root.
    """
    strategy: str
    value: str
    role: Optional[str] = None
    frames: list[str] = Field(default_factory=list)
    shadow: list[str] = Field(default_factory=list)
    score: float = 0.0
    description: str = ""
    picked_by: Optional[str] = None   # how the element was identified: ai | heuristic
    code: Optional[str] = None        # strategy "playwright": the Python locator code Playwright generated

    def label(self) -> str:
        where = "".join(f"iframe[{f}] » " for f in self.frames) + "".join(f"shadow[{s}] » " for s in self.shadow)
        r = f"{self.role}:" if self.role else ""
        return f"{where}{self.strategy}={r}{self.value}"


class Step(BaseModel):
    """One browser action derived from plain English."""
    action: Action
    target: Optional[str] = Field(None, description="Natural-language description of the element, e.g. 'Login button'")
    value: Optional[str] = Field(None, description="Text to type, URL, key, option, expected text, seconds ...")
    nth: Optional[int] = Field(None, description="0-based ordinal when the user said 'first/second/3rd ...'")
    raw: str = Field("", description="The step as a sentence")
    origin: Optional[str] = Field(None, description="The user's original wording this step was derived from")
    source: Optional[str] = Field(None, description="Who turned the English into this step: ai | rules | user")
    locator: Optional[Locator] = None             # filled in after resolution (replayable)
    fallbacks: list[Locator] = Field(default_factory=list)
    target_locator: Optional[Locator] = None      # drag destination
    optional: bool = False


class TestCase(BaseModel):
    name: str = "Untitled test"                   # may keep ${placeholders}: the reusable name scripts are saved under
    title: Optional[str] = None                   # this run's display name (placeholders filled, iteration label)
    base_url: Optional[str] = None
    steps: list[Step]
    data: dict[str, Any] = Field(default_factory=dict)
    sensitive: list[str] = Field(default_factory=list, description="dotted data keys whose values are masked")
    source_text: str = ""
    engine: Optional[str] = None                  # engine the script was recorded with (informational)


class StepStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"          # run-level only: stopped by the user


class StepResult(BaseModel):
    index: int
    step: Step
    status: StepStatus
    duration_ms: int = 0
    screenshot: Optional[str] = None      # relative path inside run dir
    error: Optional[str] = None
    healed: bool = False                  # locator from script failed and was re-resolved
    picked_by: Optional[str] = None       # ai | heuristic | script (stored locator) | fallback
    url: str = ""
    shown_value: Optional[str] = None     # value after data substitution, masked if sensitive
    dialog: Optional[str] = None          # text of a dialog that appeared during this step
    locate_ms: Optional[int] = None       # time spent finding the element (snapshot + ranking + verification)


class RunConfig(BaseModel):
    engine: str = "playwright"            # playwright | selenium
    headless: bool = True
    browser: str = "chromium"             # chromium | chrome | msedge | firefox | webkit
    # remote browser (Selenium Grid / BrowserStack / Sauce Labs / LambdaTest, or a Playwright browser server):
    # Selenium: the hub URL; Playwright: ws(s):// (browser server) or http(s):// (Chrome DevTools endpoint)
    remote_url: Optional[str] = None
    remote_caps: dict[str, Any] = Field(default_factory=dict)   # Selenium capabilities (platformName, bstack:options, ...)
    remote_attach: bool = False           # remote_url is the Chrome extension's DevTools relay: drive the person's own tab, don't open a browser
    ext_token: Optional[str] = None       # that relay's session (set by the hub, never by a client)
    record_video: bool = True
    screenshot_each_step: bool = True
    full_page_screenshots: bool = False
    timeout_ms: int = 10000
    viewport_width: int = 1366
    viewport_height: int = 900
    use_llm: bool = True                  # use Claude for parsing / locator disambiguation if a key is available
    self_heal: bool = True                # when replaying, re-resolve broken locators
    dialogs: str = "accept"               # default answer to alert/confirm/prompt: accept | dismiss
    slow_mo_ms: int = 0
    live_view: bool = True
    highlight: bool = True                # green box on the element: dashed when located, solid once the action is done
    highlight_ms: int = 300               # how long the "located" box is shown before acting (visible in video / live view)                # stream live frames + per-step screenshots to the web UI
    live_fps: float = 1.5
    use_ax_tree: bool = True
    use_playwright_snapshot: bool = True  # Playwright: candidates from page.aria_snapshot + Playwright's locator generator
    open_closed_shadow_roots: bool = True  # inject a script that creates shadow roots open (closed ones are otherwise invisible)
    visual_fallback: bool = True          # element not in DOM/accessibility tree (canvas…): ask Claude on a screenshot (needs AI)
    on_ambiguous: str = "fail"            # two different elements tie and nothing settles it: "fail" (say which) | "best"              # Selenium on Chrome/Edge: take roles + accessible names from the CDP accessibility tree
    # --- logged-in sessions ---------------------------------------------------
    profile_dir: Optional[str] = None     # Chrome user-data-dir to reuse (saved passwords, SSO cookies)
    profile_name: Optional[str] = None    # e.g. "Default", "Profile 1"
    session: Optional[str] = None         # name of a saved session (cookies + localStorage) to load
    save_session: Optional[str] = None    # save the session at the end of the run under this name
    session_state: Optional[dict] = Field(None, exclude=True)   # injected by the hub / CLI, never persisted
    user_agent: Optional[str] = None
    locale: Optional[str] = None


class RunSummary(BaseModel):
    run_id: str
    test_name: str
    engine: str
    status: StepStatus
    started_at: str
    finished_at: str = ""
    duration_ms: int = 0
    results: list[StepResult] = Field(default_factory=list)
    video: Optional[str] = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    agent: str = "local"
    mode: str = "ai"                      # ai (from English) | replay (from script.json)
    healed_steps: int = 0
    timings: dict = Field(default_factory=dict)   # snapshot / rank / verify / locate_step: count, avg_ms, max_ms
    session_saved: Optional[str] = None
