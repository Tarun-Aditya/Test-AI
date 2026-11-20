"""Step 3 of 3. frames + timeline + narration audio -> e2e-demo.mp4 (voice-over, burned captions) and captions .vtt / .srt."""
import json, os, re, subprocess, sys
from pathlib import Path
import imageio_ffmpeg
from narration import SCENES

HERE = Path(__file__).resolve().parent
WORK = Path(os.getenv("DEMO_WORK", HERE / "_work")).resolve()
FF = imageio_ffmpeg.get_ffmpeg_exe()
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else WORK / "out").resolve(); OUT.mkdir(parents=True, exist_ok=True)
t = json.load(open(WORK / "timeline.json"))
marks = {m["name"]: m["t"] for m in t["marks"]}
dur = json.load(open(WORK / "audio/durations.json"))
LEAD = 0.4                                    # voice starts just after the scene does

# ------------------------------------------------------------------ video track
fr = t["frames"]
lines = []
for i, (ts, name) in enumerate(fr):
    nxt = fr[i + 1][0] if i + 1 < len(fr) else t["end"]
    lines.append(f"file 'frames/{name}'\nduration {max(0.02, nxt - ts):.3f}")
lines.append(f"file 'frames/{fr[-1][1]}'")
(WORK / "frames.txt").write_text("\n".join(lines))

# ------------------------------------------------------------------ captions (sentence chunks, timed by length)
def chunks(text, limit=84):
    out = []
    for sent in re.split(r"(?<=[.!?:])\s+", text):
        words, cur = sent.split(), ""
        for w in words:
            if len(cur) + len(w) + 1 > limit and cur:
                out.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            out.append(cur)
    return out

cues = []
for name, text in SCENES:
    start, total = marks[name] + LEAD, dur[name]
    parts = chunks(text)
    weights = [len(x) + 12 for x in parts]           # +12: the pause between phrases
    t0 = start
    for part, wgt in zip(parts, weights):
        d = total * wgt / sum(weights)
        cues.append((t0, t0 + d, part)); t0 += d

def ts(x, sep):
    h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", sep)

(OUT / "e2e-demo.srt").write_text("".join(f"{i}\n{ts(a, ',')} --> {ts(b, ',')}\n{txt}\n\n" for i, (a, b, txt) in enumerate(cues, 1)))
(OUT / "e2e-demo.vtt").write_text("WEBVTT\n\n" + "".join(f"{ts(a, '.')} --> {ts(b, '.')}\n{txt}\n\n" for a, b, txt in cues))

# ------------------------------------------------------------------ audio: each scene's voice at its scene start
inputs, filt = [], []
for i, (name, _) in enumerate(SCENES):
    inputs += ["-i", str(WORK / "audio" / f"{name}.wav")]
    ms = int((marks[name] + LEAD) * 1000)
    filt.append(f"[{i + 1}:a]aresample=48000,adelay={ms}|{ms}[a{i}]")
mix = "".join(f"[a{i}]" for i in range(len(SCENES)))
filt.append(f"{mix}amix=inputs={len(SCENES)}:normalize=0,apad,atrim=0:{t['end']:.3f},loudnorm=I=-16:TP=-1.5[aout]")

style = ("FontName=Inter,FontSize=14,PrimaryColour=&H00FFFFFF,BackColour=&H96000000,BorderStyle=4,Outline=0,Shadow=0,"
         "MarginV=22,Alignment=2")
srt = str(OUT / "e2e-demo.srt").replace(":", r"\:")
filt.append(f"[0:v]fps=25,scale=1440:900:flags=lanczos:in_range=pc:out_range=tv,subtitles='{srt}':force_style='{style}',format=yuv420p[vout]")
cmd = [FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "frames.txt", *inputs,
       "-filter_complex", ";".join(filt), "-map", "[vout]", "-map", "[aout]",
       "-c:v", "libx264", "-preset", "slow", "-crf", "27", "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-movflags", "+faststart",
       str(OUT / "e2e-demo.mp4")]
subprocess.run(cmd, check=True, cwd=WORK)
# the same video without captions burned in (players show the .vtt instead)
cmd2 = [x if x != ";".join(filt) else ";".join(filt).replace(f",subtitles='{srt}':force_style='{style}'", "") for x in cmd]
cmd2[-1] = str(OUT / "e2e-demo-nocaptions.mp4")
subprocess.run(cmd2, check=True, cwd=WORK)
for f in ("e2e-demo.mp4", "e2e-demo-nocaptions.mp4"):
    print(f, (OUT / f).stat().st_size // 1024, "KB")
print("cues", len(cues), "length", t["end"])
