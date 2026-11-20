"""Step 1 of 3. The narration as speech: one WAV per scene, with an offline neural voice (Piper).

    pip install piper-tts
    curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx
    curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx.json
    python voice.py en_US-lessac-high.onnx
"""
import json, os, shutil, subprocess, sys, wave
from pathlib import Path
from narration import SCENES

WORK = Path(os.getenv("DEMO_WORK", Path(__file__).resolve().parent / "_work")).resolve()
model = sys.argv[1] if len(sys.argv) > 1 else "en_US-lessac-high.onnx"
piper = shutil.which("piper") or str(Path(sys.executable).parent / "piper")
(WORK / "audio").mkdir(parents=True, exist_ok=True)
dur = {}
for name, text in SCENES:
    out = WORK / "audio" / f"{name}.wav"
    subprocess.run([piper, "-m", model, "-f", str(out), "--length-scale", "1.05", "--sentence-silence", "0.35"],
                   input=text.encode(), check=True, capture_output=True)
    w = wave.open(str(out)); dur[name] = round(w.getnframes() / w.getframerate(), 2)
json.dump(dur, open(WORK / "audio" / "durations.json", "w"), indent=1)
print(f"{len(dur)} scenes, {sum(dur.values()):.0f} s of narration")
