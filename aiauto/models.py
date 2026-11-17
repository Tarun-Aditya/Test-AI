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
    SELECT = "select"        # <select> option
    CHECK = "check"
    UNCHECK = "uncheck"
    SCROLL = "scroll"
    WAIT = "wait"            # seconds, or until target visible
    ASSERT_TEXT = "assert_text"        # page / element contains text
    ASSERT_VISIBLE = "assert_visible"
    ASSERT_URL = "assert_url"
    ASSERT_TITLE = "assert_title"
    SCREENSHOT = "screenshot"
    BACK = "back"
    REFRESH = "refresh"
    UPLOAD = "upload"


# Actions that operate on an element and therefore need a locator.
ELEMENT_ACTIONS = {
    Action.CLICK, Action.DOUBLE_CLICK, Action.RIGHT_CLICK, Action.TYPE, Action.APPEND,
    Action.HOVER, Action.SELECT, Action.CHECK, Action.UNCHECK, Action.ASSERT_VISIBLE,
    Action.UPLOAD,
}


class Locator(BaseModel):
    """A concrete, replayable way to find an element."""
    strategy: str            # css | xpath | id | name | text | label | placeholder | role | testid
    value: str
    score: float = 0.0
    description: str = ""    # human summary of the matched element


class Step(BaseModel):
    """One browser action derived from plain English."""
    action: Action
    target: Optional[str] = Field(None, description="Natural-language description of the element, e.g. 'Login button'")
    value: Optional[str] = Field(None, description="Text to type, URL, key, option, expected text, seconds ...")
    raw: str = Field("", description="The original English sentence this step came from")
    locator: Optional[Locator] = None             # filled in after resolution (replayable)
    fallbacks: list[Locator] = Field(default_factory=list)
    optional: bool = False


class TestCase(BaseModel):
    name: str = "Untitled test"
    base_url: Optional[str] = None
    steps: list[Step]
    data: dict[str, Any] = Field(default_factory=dict)
    source_text: str = ""


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


class RunConfig(BaseModel):
    engine: str = "playwright"            # playwright | selenium
    headless: bool = True
    browser: str = "chromium"             # chromium | firefox | webkit (playwright); chrome | firefox | edge (selenium)
    record_video: bool = True
    screenshot_each_step: bool = True
    timeout_ms: int = 10000
    viewport_width: int = 1280
    viewport_height: int = 800
    use_llm: bool = True                  # use Claude for parsing / locator disambiguation if a key is available
    self_heal: bool = True                # when replaying, re-resolve broken locators


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
