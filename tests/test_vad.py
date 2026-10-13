import math
import pytest

from backend import twilio_handler
from backend.twilio_handler import (
    TwilioCallHandler,
    _ULAW_TO_LINEAR,
    mulaw_rms,
    SILENCE_RMS_THRESHOLD,
    SILENCE_DURATION_MS,
    MIN_SPEECH_MS,
    MAX_UTTERANCE_MS,
)

FRAME_MS = 20
FRAME_BYTES = 160  # 20 ms of 8 kHz µ-law
SILENT_FRAME = bytes([0xFF]) * FRAME_BYTES  # µ-law 0xFF decodes to 0


def _encode_mulaw(sample: int) -> int:
    # Nearest µ-law code for a 16-bit sample (fine for test signals)
    return min(range(256), key=lambda b: abs(_ULAW_TO_LINEAR[b] - sample))


# A 440 Hz tone at roughly -12 dBFS
TONE_FRAME = bytes(
    _encode_mulaw(int(8000 * math.sin(2 * math.pi * 440 * n / 8000))) for n in range(FRAME_BYTES)
)


def test_test_frames_are_on_the_right_side_of_the_threshold():
    assert mulaw_rms(SILENT_FRAME) == 0
    assert mulaw_rms(TONE_FRAME) > SILENCE_RMS_THRESHOLD * 5


def test_mulaw_decoding_matches_audioop_when_available():
    audioop = pytest.importorskip("audioop")  # removed in Python 3.13
    import array

    expected = array.array("h", audioop.ulaw2lin(bytes(range(256)), 2))
    assert list(expected) == list(_ULAW_TO_LINEAR)
    assert mulaw_rms(TONE_FRAME) == audioop.rms(audioop.ulaw2lin(TONE_FRAME, 2), 2)


@pytest.fixture
def handler(fake_ws):
    h = TwilioCallHandler(fake_ws)
    h.utterances = []

    async def fake_process_audio_chunk():
        # Record how much audio was handed off, then clear it like the real method
        h.utterances.append(len(h.audio_buffer) // 8)
        h.audio_buffer.clear()

    h.process_audio_chunk = fake_process_audio_chunk
    return h


async def feed(handler, frame, ms):
    for _ in range(ms // FRAME_MS):
        await handler._handle_audio_frame(frame)


async def test_utterance_fires_after_700ms_of_silence(handler):
    await feed(handler, TONE_FRAME, 500)
    await feed(handler, SILENT_FRAME, SILENCE_DURATION_MS - FRAME_MS)
    assert handler.utterances == []  # 680 ms of silence: still waiting

    await feed(handler, SILENT_FRAME, FRAME_MS)
    assert handler.utterances == [500 + SILENCE_DURATION_MS]
    assert handler.has_speech is False


async def test_short_pause_mid_sentence_does_not_end_utterance(handler):
    await feed(handler, TONE_FRAME, 400)
    await feed(handler, SILENT_FRAME, 400)
    await feed(handler, TONE_FRAME, 400)
    assert handler.utterances == []

    await feed(handler, SILENT_FRAME, SILENCE_DURATION_MS)
    assert len(handler.utterances) == 1


async def test_speech_shorter_than_300ms_is_discarded(handler):
    await feed(handler, TONE_FRAME, MIN_SPEECH_MS - FRAME_MS)
    await feed(handler, SILENT_FRAME, 2000)
    assert handler.utterances == []
    assert len(handler.audio_buffer) == 0


async def test_silence_alone_never_fires(handler):
    await feed(handler, SILENT_FRAME, 5000)
    assert handler.utterances == []
    assert len(handler.audio_buffer) == 0


async def test_continuous_speech_is_cut_at_15_seconds(handler):
    await feed(handler, TONE_FRAME, MAX_UTTERANCE_MS - FRAME_MS)
    assert handler.utterances == []

    await feed(handler, TONE_FRAME, FRAME_MS)
    assert handler.utterances == [MAX_UTTERANCE_MS]


async def test_audio_is_ignored_while_the_bot_is_speaking(handler):
    handler.is_speaking = True
    await feed(handler, TONE_FRAME, 1000)
    await feed(handler, SILENT_FRAME, 1000)
    assert handler.utterances == []
    assert len(handler.audio_buffer) == 0


def test_no_audioop_import():
    # audioop is gone in Python 3.13; the handler must not depend on it
    assert "audioop" not in vars(twilio_handler)
