"""Engine interface shared by Playwright and Selenium."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..models import Locator, RunConfig


class Engine(ABC):
    name = "base"

    def __init__(self, config: RunConfig, run_dir: Path):
        self.config = config
        self.run_dir = run_dir

    # lifecycle -------------------------------------------------------------
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> str | None:
        """Close the browser; return path to the recorded video (relative to run_dir) if any."""

    # frame-based video (used by Selenium, and by Playwright when its recorder is unavailable)
    def init_frames(self) -> None:
        self._frames: list[tuple[float, Path]] = []
        self._frame_dir = self.run_dir / "_frames"
        self._frame_mode = bool(self.config.record_video)
        if self._frame_mode:
            self._frame_dir.mkdir(parents=True, exist_ok=True)

    def _grab(self, path: Path) -> None:
        self.screenshot(path)

    def frame(self) -> None:
        if not getattr(self, "_frame_mode", False):
            return
        try:
            p = self._frame_dir / f"{len(self._frames):05d}.png"
            self._grab(p)
            self._frames.append((time.time(), p))
        except Exception:
            pass

    def _render_video(self) -> str | None:
        if len(self._frames) < 1:
            return None
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        lst = self._frame_dir / "frames.txt"
        lines = []
        for i, (t, p) in enumerate(self._frames):
            nxt = self._frames[i + 1][0] if i + 1 < len(self._frames) else t + 1.5
            lines.append(f"file '{p.name}'\nduration {max(0.2, min(nxt - t, 5)):.2f}")
        lines.append(f"file '{self._frames[-1][1].name}'")
        lst.write_text("\n".join(lines))
        out = self.run_dir / "video.webm"
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
               "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=10", "-c:v", "libvpx-vp9", "-b:v", "1M", "-deadline", "realtime",
               "-cpu-used", "8", str(out)]
        try:
            subprocess.run(cmd, check=True, cwd=self._frame_dir, timeout=300)
            return out.name
        except Exception:
            out = self.run_dir / "video.mp4"
            cmd2 = cmd[:-1]
            cmd2[cmd2.index("libvpx-vp9")] = "libx264"
            for flag in ("-b:v", "1M", "-deadline", "realtime", "-cpu-used", "8"):
                cmd2.remove(flag)
            try:
                subprocess.run(cmd2 + ["-pix_fmt", "yuv420p", str(out)], check=True, cwd=self._frame_dir, timeout=300)
                return out.name
            except Exception:
                return None

    def finish_frames(self) -> str | None:
        video = self._render_video() if getattr(self, "_frame_mode", False) else None
        shutil.rmtree(self.run_dir / "_frames", ignore_errors=True)
        return video

    # page-level ------------------------------------------------------------
    @abstractmethod
    def goto(self, url: str) -> None: ...
    @abstractmethod
    def back(self) -> None: ...
    @abstractmethod
    def refresh(self) -> None: ...
    @abstractmethod
    def url(self) -> str: ...
    @abstractmethod
    def title(self) -> str: ...
    @abstractmethod
    def page_text(self) -> str: ...
    @abstractmethod
    def snapshot(self) -> list[dict]:
        """Run locators.SNAPSHOT_JS and return candidate elements."""
    @abstractmethod
    def screenshot(self, path: Path, full_page: bool = False) -> None: ...
    @abstractmethod
    def press(self, key: str, locator: Locator | None = None) -> None: ...
    @abstractmethod
    def scroll(self, direction: str, amount: int = 600) -> None: ...
    @abstractmethod
    def wait_for_load(self) -> None: ...

    # element-level ---------------------------------------------------------
    @abstractmethod
    def exists(self, locator: Locator, timeout_ms: int) -> bool: ...
    @abstractmethod
    def click(self, locator: Locator, button: str = "left", double: bool = False) -> None: ...
    @abstractmethod
    def fill(self, locator: Locator, text: str, clear: bool = True) -> None: ...
    @abstractmethod
    def hover(self, locator: Locator) -> None: ...
    @abstractmethod
    def select(self, locator: Locator, option: str) -> None: ...
    @abstractmethod
    def set_checked(self, locator: Locator, checked: bool) -> None: ...
    @abstractmethod
    def text_of(self, locator: Locator) -> str: ...
    @abstractmethod
    def scroll_into_view(self, locator: Locator) -> None: ...
    @abstractmethod
    def upload(self, locator: Locator, path: str) -> None: ...
    @abstractmethod
    def highlight(self, locator: Locator) -> None:
        """Outline the element so it shows in the step screenshot and the video."""


def chrome_binary() -> str | None:
    """Optional override for the browser binary (AIAUTO_CHROME_BINARY)."""
    p = os.getenv("AIAUTO_CHROME_BINARY")
    return p if p and os.path.exists(p) else None


HIGHLIGHT_JS = """
(el) => { if (!el) return; const prev = el.style.outline;
  el.style.outline = '3px solid #ff3b6b'; el.style.outlineOffset = '2px';
  setTimeout(() => { el.style.outline = prev; }, 900); }
"""


def make_engine(config: RunConfig, run_dir: Path) -> Engine:
    if config.engine == "selenium":
        from .selenium_engine import SeleniumEngine
        return SeleniumEngine(config, run_dir)
    from .playwright_engine import PlaywrightEngine
    return PlaywrightEngine(config, run_dir)
