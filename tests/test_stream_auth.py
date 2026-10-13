from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

import main
from backend import twilio_handler
from backend.config import config
from backend.twilio_handler import TwilioCallHandler

TOKEN = "test-stream-token"


def start_event(token=None, **extra):
    start = {"streamSid": "MZ1", "callSid": "CA1", **extra}
    if token is not None:
        start["customParameters"] = {"token": token}
    return {"event": "start", "start": start}


MEDIA_EVENT = {"event": "media", "media": {"payload": "/////w=="}}


@pytest.fixture
def handler(fake_ws, monkeypatch):
    h = TwilioCallHandler(fake_ws)
    h.send_audio_response = AsyncMock()
    h._handle_audio_frame = AsyncMock()
    # Any of these being called means work happened before authentication
    monkeypatch.setattr(twilio_handler.stt, "transcribe", AsyncMock())
    monkeypatch.setattr(twilio_handler.llm, "process_intent", AsyncMock())
    return h


def assert_rejected(h):
    assert h.websocket.closed
    assert h.should_end_call
    assert not h.authenticated
    h.send_audio_response.assert_not_called()
    twilio_handler.stt.transcribe.assert_not_called()
    twilio_handler.llm.process_intent.assert_not_called()


async def test_valid_token_starts_the_call(handler):
    await handler.handle_message(start_event(TOKEN))

    assert handler.authenticated
    assert not handler.websocket.closed
    handler.send_audio_response.assert_awaited_once()  # the welcome line

    await handler.handle_message(MEDIA_EVENT)
    handler._handle_audio_frame.assert_awaited_once()


@pytest.mark.parametrize("token", [None, "", "wrong-token", TOKEN + "x"])
async def test_missing_or_wrong_token_closes_immediately(handler, token):
    await handler.handle_message(start_event(token))
    assert_rejected(handler)


async def test_fails_closed_when_stream_token_not_configured(handler, monkeypatch):
    monkeypatch.setattr(config, "STREAM_TOKEN", "")
    await handler.handle_message(start_event(""))
    assert_rejected(handler)


async def test_media_and_stop_before_start_are_ignored(handler):
    handler.has_speech = True
    handler.speech_ms = 1000
    await handler.handle_message(MEDIA_EVENT)
    await handler.handle_message({"event": "stop"})

    handler._handle_audio_frame.assert_not_called()
    twilio_handler.stt.transcribe.assert_not_called()


async def test_media_after_rejected_start_is_ignored(handler):
    await handler.handle_message(start_event("wrong"))
    await handler.handle_message(MEDIA_EVENT)
    handler._handle_audio_frame.assert_not_called()


# --- Through the real endpoints ---

def test_voice_twiml_passes_stream_token():
    params = {"CallSid": "CA1"}
    signature = RequestValidator(config.TWILIO_AUTH_TOKEN).compute_signature(
        "https://example.ngrok-free.app/voice", params)
    res = TestClient(main.app).post("/voice", data=params, headers={"X-Twilio-Signature": signature})

    assert res.status_code == 200
    assert f'<Parameter name="token" value="{TOKEN}" />' in res.text


def test_stream_endpoint_closes_on_bad_token():
    client = TestClient(main.app)
    with client.websocket_connect("/twilio-stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(start_event("wrong"))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_stream_endpoint_accepts_good_token():
    # No TTS key in tests, so the welcome line is skipped rather than sent
    client = TestClient(main.app)
    with client.websocket_connect("/twilio-stream") as ws:
        ws.send_json(start_event(TOKEN))
        ws.send_json({"event": "stop"})
