from unittest.mock import AsyncMock

import pytest

from backend import ticket_actions, twilio_handler
from backend.database import db
from backend.stt import STTError
from backend.twilio_handler import TwilioCallHandler


@pytest.fixture
def handler(fake_ws):
    h = TwilioCallHandler(fake_ws)
    h.spoken = []

    async def fake_send_audio_response(text):
        h.spoken.append(text)

    h.send_audio_response = fake_send_audio_response
    return h


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # process_intent pauses briefly to let audio play; skip that in tests
    monkeypatch.setattr(twilio_handler.asyncio, "sleep", AsyncMock())


# --- create_ticket ---

async def test_create_ticket_confirms_after_successful_save(handler):
    await handler.execute_tool("create_ticket", {
        "room": "405", "request_type": "housekeeping", "item": "towels",
        "quantity": 2, "priority": "medium", "description": "more towels",
    })

    assert handler.spoken == [
        "Ticket created for 2 towels in room 405. Is there anything else I can help you with?"
    ]
    tickets = await db.get_tickets()
    assert len(tickets) == 1
    assert handler.last_ticket_id == tickets[0]["id"]
    assert handler.conversation_history[-1] == {"role": "assistant", "content": handler.spoken[0]}


async def test_create_ticket_save_failed_does_not_claim_success(handler, monkeypatch):
    monkeypatch.setattr(db, "create_ticket", AsyncMock(return_value=None))

    await handler.execute_tool("create_ticket", {"room": "405", "item": "towels"})

    assert handler.spoken == [ticket_actions.SAVE_FAILED_REPLY]
    assert "Ticket created" not in handler.spoken[0]
    assert handler.last_ticket_id is None


async def test_create_ticket_db_exception_is_reported_not_raised(handler, monkeypatch):
    monkeypatch.setattr(db, "create_ticket", AsyncMock(side_effect=RuntimeError("db down")))

    await handler.execute_tool("create_ticket", {"room": "405", "item": "towels"})

    assert handler.spoken == [ticket_actions.SAVE_FAILED_REPLY]


async def test_create_ticket_with_missing_fields_does_not_raise(handler):
    await handler.execute_tool("create_ticket", {})

    assert len(handler.spoken) == 1
    assert handler.spoken[0].startswith("Ticket created for your request in room 402")


async def test_create_ticket_with_explicit_nulls_uses_defaults(handler):
    await handler.execute_tool("create_ticket", {"room": None, "item": "pillows", "quantity": None})

    assert handler.spoken[0].startswith("Ticket created for 1 pillows in room 402")


# --- update_last_ticket ---

async def test_update_last_ticket_updates_only_known_changed_fields(handler):
    await handler.execute_tool("create_ticket", {"room": "402", "item": "towels", "quantity": 2})
    ticket_id = handler.last_ticket_id
    handler.spoken.clear()

    await handler.execute_tool("update_last_ticket", {"quantity": 5, "item": None, "bogus": "x"})

    assert handler.spoken == ["Got it, I've updated the quantity to 5. Anything else?"]
    ticket = await db.get_ticket(ticket_id)
    assert ticket["quantity"] == 5
    assert ticket["item"] == "towels"
    assert "bogus" not in ticket


async def test_update_last_ticket_failure_is_reported(handler):
    handler.last_ticket_id = "does-not-exist"

    await handler.execute_tool("update_last_ticket", {"room": "405"})

    assert handler.spoken == [ticket_actions.UPDATE_FAILED_REPLY]


async def test_update_last_ticket_with_nothing_to_change_asks(handler, monkeypatch):
    handler.last_ticket_id = "abc"
    update = AsyncMock()
    monkeypatch.setattr(db, "update_ticket", update)

    await handler.execute_tool("update_last_ticket", {})

    update.assert_not_called()
    assert handler.spoken == ["What would you like me to change on that request?"]


async def test_update_last_ticket_without_prior_ticket_creates_one(handler):
    await handler.execute_tool("update_last_ticket", {"room": "405", "item": "pillows", "quantity": 3})

    assert handler.spoken[0].startswith("Ticket created for 3 pillows in room 405")
    assert len(await db.get_tickets()) == 1


# --- set_wakeup_call ---

async def test_set_wakeup_call_confirms_after_successful_save(handler):
    await handler.execute_tool("set_wakeup_call", {"room": "310", "time": "7:00 AM"})

    assert handler.spoken == ["Wake-up call set for 7:00 AM in room 310. Anything else?"]
    [ticket] = await db.get_tickets()
    assert ticket["request_type"] == "wakeup_call"
    assert ticket["description"] == "Wake-up call at 7:00 AM"


async def test_set_wakeup_call_save_failed(handler, monkeypatch):
    monkeypatch.setattr(db, "create_ticket", AsyncMock(return_value=None))

    await handler.execute_tool("set_wakeup_call", {"room": "310", "time": "7:00 AM"})

    assert handler.spoken == [ticket_actions.SAVE_FAILED_REPLY]


async def test_set_wakeup_call_without_time_asks_instead_of_raising(handler):
    await handler.execute_tool("set_wakeup_call", {"room": "310"})

    assert handler.spoken == ["What time would you like your wake-up call?"]
    assert await db.get_tickets() == []


# --- fallbacks in the turn pipeline ---

async def test_llm_failure_speaks_fallback(handler, monkeypatch):
    monkeypatch.setattr(twilio_handler.llm, "process_intent", AsyncMock(side_effect=RuntimeError("boom")))

    await handler.process_intent("I need towels")

    assert handler.spoken == [twilio_handler.PROCESSING_FAILED_REPLY]
    assert handler.is_speaking is False


async def test_none_response_uses_default_line(handler, monkeypatch):
    monkeypatch.setattr(twilio_handler.llm, "process_intent", AsyncMock(return_value={
        "tool_calls": [], "response": None, "end_call": False, "farewell": None,
    }))

    await handler.process_intent("hmm")

    assert handler.spoken == ["I'm not sure I understood. Could you please repeat?"]


async def test_stt_error_speaks_fallback(handler, monkeypatch):
    monkeypatch.setattr(twilio_handler.stt, "transcribe", AsyncMock(side_effect=STTError("timeout")))
    handler.audio_buffer.extend(b"\x00" * 1600)

    await handler.process_audio_chunk()

    assert handler.spoken == [twilio_handler.STT_FAILED_REPLY]


async def test_silence_from_stt_says_nothing(handler, monkeypatch):
    monkeypatch.setattr(twilio_handler.stt, "transcribe", AsyncMock(return_value=""))
    handler.audio_buffer.extend(b"\x00" * 1600)

    await handler.process_audio_chunk()

    assert handler.spoken == []


async def test_repeated_and_short_answers_are_processed(handler, monkeypatch):
    transcripts = iter(["Yes.", "Yes.", "no", "ok"])
    monkeypatch.setattr(twilio_handler.stt, "transcribe", AsyncMock(side_effect=lambda *_: next(transcripts)))
    seen = []

    async def record(transcript):
        seen.append(transcript)

    handler.process_intent = record
    for _ in range(4):
        handler.audio_buffer.extend(b"\x00" * 1600)
        await handler.process_audio_chunk()

    assert seen == ["Yes.", "Yes.", "no", "ok"]


async def test_unexpected_stt_exception_speaks_fallback_instead_of_ending_call(handler, monkeypatch):
    monkeypatch.setattr(twilio_handler.stt, "transcribe", AsyncMock(side_effect=ValueError("bad json")))
    handler.audio_buffer.extend(b"\x00" * 1600)

    await handler.process_audio_chunk()  # must not raise

    assert handler.spoken == [twilio_handler.STT_FAILED_REPLY]
