import httpx
import pytest

from backend.stt import STTService, STTError


def make_service(handler):
    service = STTService()
    service.api_key = "test"
    service.use_mock = False
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return service


def deepgram_reply(transcript):
    return {"results": {"channels": [{"alternatives": [{"transcript": transcript}]}]}}


async def test_transcribe_passes_encoding_and_returns_text():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=deepgram_reply("hello"))

    service = make_service(handler)
    assert await service.transcribe(b"\x00" * 10, encoding="linear16", sample_rate=16000) == "hello"
    assert seen["encoding"] == "linear16" and seen["sample_rate"] == "16000"

    assert await service.transcribe(b"\x00" * 10) == "hello"
    assert seen["encoding"] == "mulaw" and seen["sample_rate"] == "8000"


async def test_silence_returns_empty_string():
    service = make_service(lambda r: httpx.Response(200, json=deepgram_reply("")))
    assert await service.transcribe(b"\x00") == ""


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"results": {"channels": []}}),
])
async def test_failures_raise_stt_error(response):
    service = make_service(lambda r: response)
    with pytest.raises(STTError):
        await service.transcribe(b"\x00")


async def test_network_error_raises_stt_error():
    def handler(request):
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(STTError):
        await make_service(handler).transcribe(b"\x00")
