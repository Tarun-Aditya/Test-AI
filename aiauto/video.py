"""Turning a tab's screencast (jpeg frames with timestamps) into a video."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("aiauto.video")
CANVAS = (1280, 720)


def ffmpeg_exe() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def build_video(frames: list[tuple[int, Path]], end_ms: int, out: Path) -> bool:
    """Screencast frames (time in ms, jpeg) -> one video. Frames arrive only when the page changes, so each is shown until the next."""
    exe = ffmpeg_exe()
    if not exe or not frames:
        return False
    frames = sorted(frames)
    lst = out.parent / "frames.txt"
    lines = []
    for i, (t, p) in enumerate(frames):
        nxt = frames[i + 1][0] if i + 1 < len(frames) else max(end_ms, t + 500)
        lines.append(f"file '{p.as_posix()}'\nduration {max(nxt - t, 40) / 1000:.3f}")
    lines.append(f"file '{frames[-1][1].as_posix()}'")           # the concat demuxer ignores the last duration
    lst.write_text("\n".join(lines), encoding="utf-8")
    w, h = CANVAS
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white,fps=10,format=yuv420p"
    try:
        r = subprocess.run([exe, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf,
                            "-c:v", "libvpx", "-b:v", "900k", "-deadline", "realtime", "-cpu-used", "5", str(out)],
                           capture_output=True, text=True, timeout=180)
        ok = r.returncode == 0 and out.exists() and out.stat().st_size > 0
        if not ok:
            log.warning("video build failed: %s", r.stderr[-300:])
        return ok
    except Exception as e:
        log.warning("video build failed: %s", e)
        return False
    finally:
        lst.unlink(missing_ok=True)
