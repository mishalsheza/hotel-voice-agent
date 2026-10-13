import httpx
from backend.config import config

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
STT_TIMEOUT_SECONDS = 10.0  # a caller won't wait longer than this for a reply anyway


class STTError(Exception):
    """Transcription failed (network/API error) — as opposed to the caller simply being silent."""


class STTService:
    def __init__(self):
        self.api_key = config.DEEPGRAM_API_KEY
        self.use_mock = not self.api_key
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        # One shared client so connections to Deepgram are reused across utterances
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=STT_TIMEOUT_SECONDS)
        return self._client

    async def aclose(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def transcribe(self, audio_bytes: bytes, encoding: str = "mulaw", sample_rate: int = 8000) -> str:
        """Transcribe audio using Deepgram REST API.

        Defaults match Twilio Media Streams (8 kHz µ-law). The browser demo sends
        16 kHz linear16. Returns "" when nothing was said; raises STTError on failure.
        """
        if self.use_mock:
            print("⚠️ Using mock STT (no API key)")
            return ""

        try:
            response = await self._get_client().post(
                DEEPGRAM_URL,
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "audio/raw",
                },
                params={
                    "model": "nova-2",
                    "language": "en-US",
                    "smart_format": "true",
                    "encoding": encoding,
                    "sample_rate": str(sample_rate),
                    "channels": "1",
                },
                content=audio_bytes,
            )
        except Exception as e:
            print(f"❌ Deepgram request failed: {e}")
            raise STTError(str(e)) from e

        if response.status_code != 200:
            print(f"❌ Deepgram API error: {response.status_code} — {response.text}")
            raise STTError(f"Deepgram returned {response.status_code}")

        try:
            result = response.json()
            transcript = (
                result.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
                .get("transcript", "")
            ) or ""
        except (ValueError, IndexError, AttributeError) as e:
            print(f"❌ Unexpected Deepgram response: {e}")
            raise STTError("Malformed Deepgram response") from e
        if transcript:
            print(f"🎤 STT Result: {transcript}")
        return transcript

stt = STTService()
