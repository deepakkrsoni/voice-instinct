"""Make Milo answer only your voice (#32): read five lines to the orb, about a minute. Milo records them
itself, so the voiceprint matches what it hears; it is used straight away and never leaves this computer.
Run: uv run enroll.py   Remove it: uv run enroll.py --delete   (Milo must be running.)"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import load
from enrolment import LINES

WAIT_SECS = 20  # per line, before asking whether the mic is muted
PORT = 7860


def http_call(token):
    def call(method, path):
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                     headers={"X-Voice-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:  # the bot is up but refuses, with a reason
            try:
                return {"error": json.loads(e.read()).get("detail") or str(e)}
            except ValueError:
                return {"error": str(e)}
        except OSError:
            return None
    return call


def run(cfg, call, say=print, sleep=time.sleep, delete=False) -> int:
    if cfg.stt_engine != "parakeet":
        say('The voice check needs [stt] engine = "parakeet" in config.toml.')
        return 1
    if delete:
        r = call("POST", "/enrol/delete")
        if r is None:
            say("Start Milo first (systemctl --user start voice-instinct.service).")
            return 1
        if r.get("error"):
            say(f"Can't delete: {r['error']}")
            return 1
        say(f"Voiceprint deleted: {cfg.name} hears every voice again.")
        return 0
    started = call("POST", "/enrol/start")
    if started is None:
        say("Start Milo first (systemctl --user start voice-instinct.service), with the orb open.")
        return 1
    if started.get("error"):
        say(f"Can't enrol: {started['error']}")
        return 1
    say(f"Read each line to the orb, where you normally use {cfg.name}. It stays quiet while you do.")
    heard = 0
    for i, line in enumerate(LINES, 1):
        say(f'\n[{i}/{len(LINES)}]  "{line.format(name=cfg.name)}"')
        for _ in range(WAIT_SECS * 4):
            st = call("GET", "/enrol/status") or {}
            heard = st.get("heard", heard)
            if heard >= i or st.get("done"):
                break
            sleep(0.25)
        else:
            call("POST", "/enrol/cancel")
            say("\nNothing heard for 20 s: is the orb's mic muted? Nothing was saved; run this again.")
            return 1
        say("  ✓ got it")
        if st.get("done"):
            break
    for _ in range(120):  # the voiceprint is built after the last line (the cohort takes a few seconds)
        st = call("GET", "/enrol/status") or {}
        if st.get("done") or st.get("error"):
            break
        sleep(0.25)
    else:
        st = {"error": "it took too long"}
    if st.get("error"):
        say(f"\nThe voiceprint wasn't saved ({st['error']}); run this again.")
        return 1
    say(f"\nDone: only your voice can wake {cfg.name} now (once awake, it listens to the conversation, as Siri does).")
    if st.get("learned"):
        say(f"It also learned how your wake phrase is heard: {', '.join(st['learned'])}.")
    say("If it misses you, lower [voice] threshold in config.toml; `uv run enroll.py --delete` undoes this.")
    return 0


def main():
    cfg = load()
    token = (Path(cfg.data_dir).expanduser() / "hook-token").read_text().strip()
    sys.exit(run(cfg, http_call(token), delete="--delete" in sys.argv))


if __name__ == "__main__":
    main()
