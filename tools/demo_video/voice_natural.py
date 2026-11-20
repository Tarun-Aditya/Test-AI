"""Step 1 (natural variant). The narration spoken by Kokoro, a neural voice that runs offline, one WAV per scene.

    pip install kokoro-onnx espeakng-loader soundfile
    # model + voices (Kokoro-82M, Apache-2.0), from Hugging Face onnx-community/Kokoro-82M-v1.0-ONNX:
    #   onnx/model.onnx and voices/af_heart.bin (or am_michael, bf_emma, ...)
    python voice_natural.py model.onnx af_heart af_heart.bin [slots.json]

Each scene's audio must fit the time its screen action already takes (record.py holds a scene for its narration); slots.json
({scene: seconds}) is that limit. A scene that comes out longer is sped up a little (never past 1.12x), and fails loudly
if it still doesn't fit, so the script can be shortened instead of the voice being rushed.
"""
import json, os, sys
from pathlib import Path
import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro
from narration_natural import SCENES

WORK = Path(os.getenv("DEMO_WORK", Path(__file__).resolve().parent / "_work")).resolve()
model, voice = sys.argv[1], sys.argv[2]
voices = Path(sys.argv[3])
slots = json.load(open(sys.argv[4])) if len(sys.argv) > 4 else {}
npz = WORK / "voices.npz"
WORK.mkdir(parents=True, exist_ok=True)
np.savez(npz, **{voice: np.fromfile(voices, dtype=np.float32).reshape(-1, 1, 256)})
k = Kokoro(model, str(npz) if not str(npz).endswith(".npz") else str(npz))
(WORK / "audio").mkdir(exist_ok=True)
dur, tooLong = {}, []
for name, text in SCENES:
    speed = 1.0
    while True:
        s, sr = k.create(text, voice=voice, speed=speed, lang="en-us")
        d = len(s) / sr
        if not slots.get(name) or d <= slots[name] or speed >= 1.12:
            break
        speed = round(speed + 0.03, 2)
    pad = np.zeros(int(0.12 * sr), dtype=s.dtype)                       # a breath at the end
    sf.write(WORK / "audio" / f"{name}.wav", np.concatenate([s, pad]), sr)
    dur[name] = round(d + 0.12, 2)
    if slots.get(name) and dur[name] > slots[name]:
        tooLong.append((name, dur[name], slots[name]))
    print(f"{name:11s} {dur[name]:5.1f}s  (slot {slots.get(name, '-')}, speed {speed})")
json.dump(dur, open(WORK / "audio" / "durations.json", "w"), indent=1)
if tooLong:
    sys.exit(f"too long for their scene even at 1.12x: {tooLong}")
print(f"{len(dur)} scenes, {sum(dur.values()):.0f} s")
