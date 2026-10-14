"""NemotronSTTService in a real Pipecat pipeline (pipecat.tests.utils.run_test); only the sherpa-onnx
recognizer is faked. Each audio frame advances the fake's script by one step: (partial text, endpoint)."""

import asyncio

from config import Config
from pipecat.frames.frames import (
    ErrorFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test
from services import STT_MODELS, NemotronSTTService, make_stt

AUDIO = b"\x00\x00" * 320  # 20 ms of 16 kHz silence


def audio():
    return InputAudioRawFrame(audio=AUDIO, sample_rate=16000, num_channels=1)


class FakeStream:
    def __init__(self, rec):
        self.rec = rec

    def accept_waveform(self, sample_rate, samples):
        assert sample_rate == 16000 and len(samples) == 320
        self.rec.calls += 1
        if self.rec.calls == self.rec.fail_on:
            raise RuntimeError("decode failed")
        self.rec.step += 1


class FakeRecognizer:
    def __init__(self, script, fail_on=None):
        self.script, self.step, self.resets, self.calls, self.fail_on = list(script), 0, 0, 0, fail_on

    def create_stream(self):
        return FakeStream(self)

    def is_ready(self, s):
        return False

    def decode_stream(self, s):
        pass

    def _now(self):
        return self.script[min(self.step, len(self.script)) - 1] if self.step else ("", False)

    def get_result(self, s):
        return self._now()[0]

    def is_endpoint(self, s):
        return self._now()[1]

    def reset(self, s):
        self.resets += 1


def run(script, frames, fail_on=None):
    rec = FakeRecognizer(script, fail_on)
    stt = NemotronSTTService(recognizer=rec)
    down, up = asyncio.run(run_test(stt, frames_to_send=frames))
    texts = [(type(f).__name__, f.text) for f in down if isinstance(f, (InterimTranscriptionFrame, TranscriptionFrame))]
    return texts, down, up, rec


def test_partial_words_go_out_as_interim_transcripts_only_when_they_change():
    texts, *_ = run([("", False), ("hey", False), ("hey", False), ("hey nova", False)], [audio() for _ in range(4)])
    assert texts == [("InterimTranscriptionFrame", "hey"), ("InterimTranscriptionFrame", "hey nova")]


def test_an_endpoint_gives_one_finalized_sentence_then_the_stream_resets():
    texts, down, _, rec = run([("hey nova find me a laptop", False), ("hey nova find me a laptop", True)],
                              [audio(), audio()])
    assert texts == [("InterimTranscriptionFrame", "hey nova find me a laptop"),
                     ("TranscriptionFrame", "hey nova find me a laptop")]
    final = next(f for f in down if isinstance(f, TranscriptionFrame))
    assert final.finalized is True and rec.resets == 1


def test_silence_before_the_wake_gives_no_frames_and_keeps_the_stream_small():
    texts, _, _, rec = run([("", False), ("", True), ("", False), ("", True)], [audio() for _ in range(4)])
    assert texts == [] and rec.resets == 2


def test_the_end_of_the_users_turn_forces_the_final():
    texts, *_ = run([("find me a laptop", False)], [audio(), UserStoppedSpeakingFrame(), SleepFrame(sleep=0.05)])
    assert texts[-1] == ("TranscriptionFrame", "find me a laptop")


def test_a_short_vad_pause_does_not_cut_the_sentence():
    texts, *_ = run([("hey", False), ("hey", False), ("hey nova", False)],
                    [audio(), VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame(), audio(), audio()])
    assert ("TranscriptionFrame", "hey") not in texts
    assert texts[-1] == ("InterimTranscriptionFrame", "hey nova")


def test_a_decode_error_is_reported_and_later_audio_is_still_transcribed():
    texts, _, up, rec = run([("hey", False), ("hey nova", False), ("hey nova", True)],
                            [audio(), audio(), audio(), audio()], fail_on=1)  # the first chunk fails
    assert any(isinstance(f, ErrorFrame) for f in up)
    assert texts[-1] == ("TranscriptionFrame", "hey nova")


def test_make_stt_builds_nemotron_for_both_streaming_engines(tmp_path):
    for engine in ("nemotron", "nemotron-multilingual"):
        (tmp_path / STT_MODELS[engine]).mkdir()
        stt = make_stt(Config(models_dir=str(tmp_path), stt_engine=engine), recognizer=FakeRecognizer([]))
        assert isinstance(stt, NemotronSTTService)
