import pytest

from backend.llm import LLMService


@pytest.fixture
def fallback():
    return LLMService()._fallback_processing


def ticket_items(result):
    return [c["arguments"]["item"] for c in result["tool_calls"] if c["name"] == "create_ticket"]


@pytest.mark.parametrize("text", [
    "I'd like to place an order",
    "Can you call me back?",
    "Do you have any snacks?",
    "I need a taxi to the airport",
])
def test_words_containing_ac_do_not_create_ac_ticket(fallback, text):
    result = fallback(text)
    assert "air conditioning" not in ticket_items(result)
    assert result["tool_calls"] == []
    assert result["response"]


@pytest.mark.parametrize("text", [
    "My AC is broken",
    "the A/C isn't working",
    "The air conditioning is too loud",
    "the air conditioner won't turn on",
])
def test_ac_requests_create_maintenance_ticket(fallback, text):
    result = fallback(text)
    assert ticket_items(result) == ["air conditioning"]
    args = result["tool_calls"][0]["arguments"]
    assert args["request_type"] == "maintenance"
    assert args["priority"] == "high"


def test_towels_create_housekeeping_ticket(fallback):
    result = fallback("Could I get some more towels please?")
    assert ticket_items(result) == ["towels"]
    assert result["tool_calls"][0]["arguments"]["request_type"] == "housekeeping"


def test_wifi_question_is_answered(fallback):
    result = fallback("What's the wifi password?")
    assert "HotelGuest2024" in result["response"]
    assert result["tool_calls"] == []


@pytest.mark.parametrize("text", ["No thanks, that's all.", "Bye!", "I'm all set"])
def test_end_phrases_end_the_call(fallback, text):
    result = fallback(text)
    assert result["end_call"] is True
    assert result["farewell"]


def test_end_phrase_must_be_whole_words(fallback):
    # "bye" inside "byelaw" shouldn't hang up on the guest
    assert fallback("What are the hotel byelaws?")["end_call"] is False


async def test_process_intent_uses_fallback_without_api_key():
    service = LLMService()
    assert service.use_fallback
    result = await service.process_intent("I need towels")
    assert ticket_items(result) == ["towels"]
