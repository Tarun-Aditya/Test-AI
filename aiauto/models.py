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
    raw: str = Field("", description="The original English sentence this step came from")
    locator: Optional[Locator] = None             # filled in after resolution (replayable)
    fallbacks: list[Locator] = Field(default_factory=list)
    target_locator: Optional[Locator] = None      # drag destination
    optional: bool = False


class TestCase(BaseModel):
    name: str = "Untitled test"
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


class StepResult(BaseModel):
    index: int
    step: Step
    status: StepStatus
    duration_ms: int = 0
    screenshot: Optional[str] = None      # relative path inside run dir
    error: Optional[str] = None
    healed: bool = False                  # locator from script failed and was re-resolved
    url: str = ""
    shown_value: Optional[str] = None     # value after data substitution, masked if sensitive
    dialog: Optional[str] = None          # text of a dialog that appeared during this step


class RunConfig(BaseModel):
    engine: str = "playwright"            # playwright | selenium
    headless: bool = True
    browser: str = "chromium"             # chromium | chrome | msedge | firefox | webkit
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
    live_view: bool = True                # stream live frames + per-step screenshots to the web UI
    live_fps: float = 1.5
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
    session_saved: Optional[str] = None
