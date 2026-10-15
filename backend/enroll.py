"""Make Milo answer only your voice (#32): say six lines once (about a minute) and a voiceprint is saved in
~/.voice-instinct. It never leaves this computer. Run: uv run enroll.py   Remove it: uv run enroll.py --delete"""

import json
import subprocess
import sys
import time

import numpy as np

from config import load
from voiceprint import embed_with, learn_spellings, learned_path, save_voiceprint, speaker_model_path, voiceprint_path

LINES = [
    "Hey {name}",
    "Hey {name}",
    "Hey {name}",
    "Book a table for two at eight tonight.",
    "Find me a cheap flight to Bangalore next Friday.",
    "What am I working on right now?",
]
SECS = 4
RESTART = "systemctl --user restart voice-instinct.service"


def record(secs: float) -> bytes:
    """16 kHz 16-bit mono from the default mic, via PipeWire (raw to stdout, so no file is written)."""
    # --raw: without it, "-" gets a WAV header
    p = subprocess.Popen(["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "--raw", "-"],
                         stdout=subprocess.PIPE)
    time.sleep(secs)
    p.terminate()
    return p.communicate()[0]


def enroll(cfg, record, embed, transcribe, say=print, pause=0.0) -> list[float]:
    """Record LINES, save the voiceprint and learned wake spellings; return each take's score."""
    takes = []
    for i, line in enumerate(LINES, 1):
        say(f'\n[{i}/{len(LINES)}]  "{line.format(name=cfg.name)}"')
        time.sleep(pause)  # time to read the line before recording starts
        say("  >>> SPEAK NOW <<<")
        takes.append((line, record(SECS)))
    vectors = [embed(pcm) for _, pcm in takes]
    save_voiceprint(cfg, vectors)
    heard = [transcribe(pcm) for line, pcm in takes if line == "Hey {name}"]  # never kept, only the spellings
    learned_path(cfg).write_text(json.dumps(learn_spellings(heard, cfg.name)))
    vp = np.load(voiceprint_path(cfg))
    return [float(v @ vp) for v in vectors]


def delete(cfg) -> None:
    for p in (voiceprint_path(cfg), learned_path(cfg)):
        p.unlink(missing_ok=True)


def main():
    cfg = load()
    if "--delete" in sys.argv:
        delete(cfg)
        print(f"Voiceprint deleted: {cfg.name} hears every voice again. Restart it: {RESTART}")
        return
    model = speaker_model_path(cfg)
    if not model.exists():
        sys.exit(f"The speaker model is not installed ({model}). Run ./install.sh first.")
    from services import _load_recognizer, parakeet_dir
    rec = _load_recognizer(parakeet_dir(cfg))

    def transcribe(pcm):
        s = rec.create_stream()
        s.accept_waveform(16000, np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0)
        rec.decode_stream(s)
        return s.result.text

    print(f"Read each line out loud when it says SPEAK NOW, where you normally use {cfg.name}. About a minute.")
    input("Press Enter to start...")
    scores = enroll(cfg, record, embed_with(model), transcribe, pause=1.5)
    learned = json.loads(learned_path(cfg).read_text())
    print(f"\nSaved your voiceprint. Scores: {', '.join(f'{s:.2f}' for s in scores)} (cut-off {cfg.voice_threshold}).")
    if min(scores) < cfg.voice_threshold + 0.2:
        print("One take scored low: run this again in a quieter room for a better voiceprint.")
    if learned:
        print(f"Also learned how your wake phrase is heard: {', '.join(learned)} (edit {learned_path(cfg)} to change).")
    print(f"Restart {cfg.name} to use it: {RESTART}")


if __name__ == "__main__":
    main()
