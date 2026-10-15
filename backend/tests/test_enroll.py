from config import Config
from enroll import run
from enrolment import LINES


class FakeBot:
    def __init__(self, hears=True):
        self.hears, self.heard, self.calls = hears, 0, []

    def __call__(self, method, path):
        self.calls.append((method, path))
        if path == "/enrol/status" and self.hears:
            self.heard += 1
        return {"active": self.heard < len(LINES), "heard": self.heard, "done": self.heard >= len(LINES),
                "learned": ["hey mailo"]}


def cfg(engine="parakeet"):
    return Config(name="Milo", stt_engine=engine)


def test_walks_the_lines_and_reports_what_it_learned():
    said = []
    bot = FakeBot()
    assert run(cfg(), bot, say=said.append, sleep=lambda s: None) == 0
    assert ("POST", "/enrol/start") in bot.calls
    assert any('"Milo, what time is it?"' in s for s in said) and any("hey mailo" in s for s in said)


def test_bot_not_running_says_start_milo_first():
    said = []
    assert run(cfg(), lambda m, p: None, say=said.append, sleep=lambda s: None) == 1
    assert any("start" in s.lower() for s in said)


def test_nothing_heard_cancels_and_says_check_the_mic():
    said = []
    bot = FakeBot(hears=False)
    assert run(cfg(), bot, say=said.append, sleep=lambda s: None) == 1
    assert ("POST", "/enrol/cancel") in bot.calls and any("mic" in s for s in said)


def test_nemotron_is_refused_before_calling_the_bot():
    said = []
    bot = FakeBot()
    assert run(cfg("nemotron"), bot, say=said.append, sleep=lambda s: None) == 1
    assert bot.calls == [] and any("parakeet" in s for s in said)


def test_a_failure_in_the_bot_is_reported_at_once():
    said = []
    bot = FakeBot()
    real = bot.__call__

    def call(method, path):
        st = real(method, path)
        return {**st, "done": False, "error": "kokoro broke"} if st and st["heard"] >= len(LINES) else st
    assert run(cfg(), call, say=said.append, sleep=lambda s: None) == 1
    assert any("kokoro broke" in s for s in said)


def test_the_bots_reason_is_shown_when_it_cannot_enrol():
    said = []
    assert run(cfg(), lambda m, p: {"error": "run ./install.sh"}, say=said.append, sleep=lambda s: None) == 1
    assert any("install.sh" in s for s in said)


def test_http_call_turns_a_503_into_its_reason(monkeypatch):
    import io
    import urllib.error
    import enroll

    def fail(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "x", {}, io.BytesIO(b'{"detail": "run ./install.sh"}'))
    monkeypatch.setattr(enroll.urllib.request, "urlopen", fail)
    assert enroll.http_call("t")("POST", "/enrol/start") == {"error": "run ./install.sh"}


def test_delete_reports_a_refusal_instead_of_success():
    said = []
    assert run(cfg(), lambda m, p: {"error": "Forbidden"}, say=said.append, sleep=lambda s: None, delete=True) == 1
    assert not any("deleted" in s for s in said)


def _real_bot(tmp_path, cohort):
    """enroll.run against the real Enrolment: each status poll, Milo 'hears' the next line."""
    import numpy as np
    from enrolment import Enrolment
    from voiceprint import Voiceprint
    vp = Voiceprint(None, None, 0.6, lambda x: np.array([1.0, 0.0, 1.0]) / 2 ** 0.5)
    e = Enrolment(Config(data_dir=str(tmp_path), name="Milo"), vp, embed=vp.embed, cohort=cohort)
    take = (np.full(16000, 0.3) * 32767).astype(np.int16).tobytes()

    def call(method, path):
        if path == "/enrol/start":
            e.start()
        elif path == "/enrol/cancel":
            e.cancel()
        elif path == "/enrol/status" and e.active:
            e.offer(take, LINES[min(e.status()["heard"], len(LINES) - 1)].format(name="Milo"))
        return e.status()
    return call, vp


def test_with_the_real_enrolment_a_build_failure_is_reported_not_blamed_on_the_mic(tmp_path):
    import numpy as np
    said = []
    call, vp = _real_bot(tmp_path, cohort=lambda: (_ for _ in ()).throw(RuntimeError("kokoro broke")))
    assert run(cfg(), call, say=said.append, sleep=lambda s: None) == 1
    assert any("kokoro broke" in s for s in said) and not any("mic" in s for s in said)


def test_with_the_real_enrolment_it_completes(tmp_path):
    import numpy as np
    said = []
    call, vp = _real_bot(tmp_path, cohort=lambda: np.array([0.0, 0.0, 2 ** -0.5]))
    assert run(cfg(), call, say=said.append, sleep=lambda s: None) == 0 and vp.enrolled



def test_a_take_not_heard_as_the_wake_phrase_asks_to_say_it_again():
    said, polls = [], []

    def call(method, path):
        if path == "/enrol/status":
            polls.append(1)
            n = len(polls)
            return {"active": True, "heard": 0 if n < 3 else 5, "done": n >= 3, "rejected": 1 if n >= 2 else 0,
                    "learned": []}
        return {"active": True, "heard": 0, "done": False, "rejected": 0}
    assert run(cfg(), call, say=said.append, sleep=lambda s: None) == 0
    assert any("say it again" in s.lower() for s in said)



def test_a_line_heard_but_never_matched_does_not_blame_the_mic():
    said = []
    call = lambda m, p: {"active": True, "heard": 0, "done": False, "rejected": 2}
    assert run(cfg(), call, say=said.append, sleep=lambda s: None) == 1
    assert not any("muted" in s for s in said) and any("heard you" in s.lower() for s in said)
