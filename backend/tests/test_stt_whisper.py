"""Whisper on the segmented service: the output filter, and make_stt wiring."""

import asyncio

import pytest

from config import Config
from pipecat.frames.frames import InputAudioRawFrame, TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.tests.utils import SleepFrame, run_test
from services import ParakeetSTTService, checks_voice, make_stt
from whisper_text import clean


def test_a_tag_only_segment_pushes_nothing():
    assert clean("[MUSIC PLAYING]") == ""
    assert clean("(crickets chirping)") == ""
    assert clean(" [BLANK_AUDIO] ") == ""


def test_brackets_inside_a_sentence_are_kept():
    assert clean("I said [laughs] hi") == "I said [laughs] hi"


def test_only_runs_longer_than_two_collapse():
    assert clean("Nova Nova Nova Nova Nova") == "Nova"
    assert clean("Nova? Nova? Nova? Nova?") == "Nova?"
    assert clean("no no, the other one") == "no no, the other one"


def test_whisper_s_invented_phrases_on_silence_are_dropped():
    assert clean("Thank you.") == ""
    assert clean("Thanks for watching!") == ""
    assert clean("You") == ""
    assert clean("Thank you, Nova, that's all") == "Thank you, Nova, that's all"


class Fake:
    def __init__(self, text):
        self.text = text

    def create_stream(self):
        text = self.text

        class S:
            result = type("R", (), {"text": text})()

            def accept_waveform(self, sample_rate, samples):
                pass
        return S()

    def decode_stream(self, s):
        pass


def heard(stt):
    speech = [InputAudioRawFrame(audio=b"\x01\x00" * 1600, sample_rate=16000, num_channels=1) for _ in range(5)]
    stt.PAUSE_SECS = 0.1
    down, _ = asyncio.run(run_test(stt, frames_to_send=[VADUserStartedSpeakingFrame(), *speech,
                                                        VADUserStoppedSpeakingFrame(), SleepFrame(sleep=0.5)]))
    return [f.text for f in down if isinstance(f, TranscriptionFrame)]


def test_whisper_output_is_cleaned_before_it_is_pushed():
    assert heard(ParakeetSTTService(recognizer=Fake("[MUSIC PLAYING]"), clean=clean)) == []
    assert heard(ParakeetSTTService(recognizer=Fake("Nova Nova Nova"), clean=clean)) == ["Nova"]


def test_make_stt_gives_whisper_the_segmented_service_with_the_filter():
    stt = make_stt(Config(stt_engine="whisper-base"), recognizer=Fake("[MUSIC PLAYING]"))
    assert isinstance(stt, ParakeetSTTService)
    assert heard(stt) == []
    stt = make_stt(Config(stt_engine="parakeet"), recognizer=Fake("[MUSIC PLAYING]"))
    assert heard(stt) == ["[MUSIC PLAYING]"]


def test_only_segmented_engines_check_the_voice():
    assert checks_voice("parakeet") and checks_voice("whisper-small")
    assert not checks_voice("nemotron")


def test_a_missing_whisper_model_says_to_run_the_installer(tmp_path):
    with pytest.raises(RuntimeError, match="whisper-base speech model is not installed"):
        make_stt(Config(stt_engine="whisper-base", models_dir=str(tmp_path)))
