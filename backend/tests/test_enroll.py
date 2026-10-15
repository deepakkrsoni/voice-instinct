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
    assert any('"Hey Milo"' in s for s in said) and any("hey mailo" in s for s in said)


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
