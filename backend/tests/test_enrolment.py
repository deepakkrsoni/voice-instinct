import json

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import Config
from enrolment import LINES, Enrolment, make_enrol_router
from voiceprint import Voiceprint, learned_path, voiceprint_path

MU = np.array([0.0, 0.0, 1.0])


def speech(secs=1.0):
    return (np.full(int(secs * 16000), 0.3) * 32767).astype(np.int16).tobytes()


def make(tmp_path, clock=None):
    learned = []
    vp = Voiceprint(None, None, 0.6, lambda x: np.array([1.0, 0.0, 1.0]) / 2 ** 0.5)
    e = Enrolment(Config(data_dir=str(tmp_path), name="Milo"), vp, embed=vp._embed, cohort=lambda: MU,
                  on_learned=learned.append, **({"clock": clock} if clock else {}))
    return e, vp, learned


def test_five_segments_make_a_live_voiceprint_and_learned_spellings(tmp_path):
    e, vp, learned = make(tmp_path)
    e.start()
    for text in ["Hey Milo, a.", "Hey Mailo, b.", "Hey my luck, c.", "Hey Milo, d.", "Hey Milo, e."]:
        e.offer(speech(), text)
    assert not e.active and e.status() == {"active": False, "heard": 5, "done": True, "learned": ["hey mailo"],
                                         "error": None, "rejected": 0}
    assert vp.enrolled and voiceprint_path(e.cfg).exists()
    assert json.loads(learned_path(e.cfg).read_text()) == ["hey mailo"] and learned == [["hey mailo"]]


def test_too_little_speech_is_not_counted(tmp_path):
    e, _, _ = make(tmp_path)
    e.start()
    e.offer(speech(0.1), "uh")
    assert e.status()["heard"] == 0


def test_it_expires_after_a_minute_without_segments(tmp_path):
    now = [0.0]
    e, _, _ = make(tmp_path, clock=lambda: now[0])
    e.start()
    now[0] = 61
    assert not e.active  # Milo hears normally again


def test_routes_need_the_token_and_delete_turns_the_check_off(tmp_path):
    e, vp, _ = make(tmp_path)
    app = FastAPI()
    app.include_router(make_enrol_router(e, "tok"))
    c = TestClient(app)
    assert c.post("/enrol/start").status_code == 403
    assert c.post("/enrol/start", headers={"X-Voice-Token": "nope"}).status_code == 403
    h = {"X-Voice-Token": "tok"}
    assert c.post("/enrol/start", headers=h).json()["active"] is True
    for _ in LINES:
        e.offer(speech(), "Hey Milo")
    assert c.get("/enrol/status", headers=h).json()["done"] is True and vp.enrolled
    assert c.post("/enrol/delete", headers=h).status_code == 200
    assert not vp.enrolled and not voiceprint_path(e.cfg).exists()


def test_cancel_stops_listening_for_takes(tmp_path):
    e, _, _ = make(tmp_path)
    e.start()
    e.cancel()
    assert not e.active


def test_a_failure_building_the_voiceprint_is_logged_not_raised(tmp_path):
    e, vp, _ = make(tmp_path)
    e._cohort = lambda: (_ for _ in ()).throw(RuntimeError("kokoro broke"))
    e.start()
    for _ in LINES:
        e.offer(speech(), "Hey Milo")  # must not raise into the STT
    assert not vp.enrolled and e.status()["done"] is False and e.status()["error"]


def test_every_line_is_the_wake_phrase_with_a_short_request():
    # as the owner really wakes it: "Hey Nova, what time is it?" is heard right 9 times in 10, a bare one 3 in 8
    assert len(LINES) == 5 and all(line.startswith("Hey {name}, ") for line in LINES)


def test_start_without_the_speaker_model_says_run_install(tmp_path):
    e = Enrolment(Config(data_dir=str(tmp_path)), Voiceprint(None, None, 0.6, None), embed=None, cohort=lambda: MU)
    app = FastAPI()
    app.include_router(make_enrol_router(e, "tok"))
    r = TestClient(app).post("/enrol/start", headers={"X-Voice-Token": "tok"})
    assert r.status_code == 503 and "install.sh" in r.json()["detail"] and not e.active


def test_delete_tells_the_bot_which_phrases_to_drop(tmp_path):
    dropped = []
    vp = Voiceprint(None, None, 0.6, lambda x: np.array([1.0, 0.0, 1.0]) / 2 ** 0.5)
    e = Enrolment(Config(data_dir=str(tmp_path), name="Milo"), vp, embed=vp.embed, cohort=lambda: MU,
                  on_deleted=dropped.append)
    learned_path(e.cfg).write_text('["hey mailo"]')
    e.delete()
    assert dropped == [["hey mailo"]]  # read before the file is removed



def test_a_take_must_be_heard_as_the_wake_phrase(tmp_path):
    # a cough, another voice or a relay note must not go into the voiceprint
    e, _, _ = make(tmp_path)
    e.start()
    for text in ["", "A little bit", "You know what?"]:
        e.offer(speech(), text)
    assert e.status()["heard"] == 0 and e.status()["rejected"] == 3
    for text in ["Hey Mailo, what time is it?", "Okay Milo, hi", "Milo, what's on today?"]:
        e.offer(speech(), text)
    assert e.status()["heard"] == 3
