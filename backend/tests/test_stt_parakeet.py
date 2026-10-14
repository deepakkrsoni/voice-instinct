"""ParakeetSTTService in a real Pipecat pipeline (run_test): a VAD stop ends a segment only after
PAUSE_SECS of silence, so short pauses between words no longer split a sentence (#19)."""

import asyncio

from pipecat.frames.frames import InputAudioRawFrame, TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.tests.utils import SleepFrame, run_test
from services import ParakeetSTTService

SPEECH = b"\x01\x00" * 1600  # 100 ms at 16 kHz


class FakeOffline:
    """sherpa-onnx OfflineRecognizer as ParakeetSTTService uses it; records each segment's length."""

    def __init__(self):
        self.segments = []

    def create_stream(self):
        rec = self

        class S:
            result = type("R", (), {"text": "heard"})()

            def accept_waveform(self, sample_rate, samples):
                rec.segments.append(len(samples) / sample_rate)
        return S()

    def decode_stream(self, s):
        pass


def speech(n=5):
    return [InputAudioRawFrame(audio=SPEECH, sample_rate=16000, num_channels=1) for _ in range(n)]


def run(frames, pause=0.2):
    rec = FakeOffline()
    stt = ParakeetSTTService(recognizer=rec)
    stt.PAUSE_SECS = pause
    down, _ = asyncio.run(run_test(stt, frames_to_send=frames))
    return rec.segments, [f for f in down if isinstance(f, TranscriptionFrame)]


def test_a_short_pause_between_words_keeps_one_segment():
    segments, finals = run([VADUserStartedSpeakingFrame(), *speech(), VADUserStoppedSpeakingFrame(),
                            SleepFrame(sleep=0.05),  # shorter than the pause: the same sentence
                            VADUserStartedSpeakingFrame(), *speech(), VADUserStoppedSpeakingFrame(),
                            SleepFrame(sleep=0.4)])
    assert len(segments) == 1 and segments[0] >= 1.0  # both halves, in one segment
    assert len(finals) == 1


def test_audio_heard_during_the_pause_does_not_trim_the_start_of_the_sentence():
    # The live mic keeps sending (silent) audio during a pause. Pipecat's base STTService clears
    # _user_speaking at every VAD stop, and SegmentedSTTService then trims its buffer to 1 s, which
    # dropped "Hey Nova" from the front of the sentence (found by test_stt_real).
    silence = [InputAudioRawFrame(audio=b"\x00\x00" * 1600, sample_rate=16000, num_channels=1) for _ in range(6)]
    segments, _ = run([VADUserStartedSpeakingFrame(), *speech(), VADUserStoppedSpeakingFrame(), *silence,
                       VADUserStartedSpeakingFrame(), *speech(), VADUserStoppedSpeakingFrame(),
                       SleepFrame(sleep=0.4)])
    assert len(segments) == 1
    assert segments[0] >= 2.05  # 0.5 s speech + 0.6 s pause + 0.5 s speech + 0.5 s padding, nothing trimmed


def test_a_real_silence_ends_the_segment_once():
    segments, finals = run([VADUserStartedSpeakingFrame(), *speech(), VADUserStoppedSpeakingFrame(),
                            SleepFrame(sleep=0.4),
                            VADUserStartedSpeakingFrame(), *speech(), VADUserStoppedSpeakingFrame(),
                            SleepFrame(sleep=0.4)])
    assert len(segments) == 2 and len(finals) == 2


def test_a_session_ending_mid_pause_pushes_nothing_after_the_end():
    segments, finals = run([VADUserStartedSpeakingFrame(), *speech(), VADUserStoppedSpeakingFrame()], pause=5)
    assert finals == []  # the pending cut was cancelled with the pipeline, not run after it


def test_the_default_pause_is_long_enough_for_speech():
    assert ParakeetSTTService.PAUSE_SECS == 0.8
