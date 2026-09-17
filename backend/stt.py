import httpx
from backend.config import config

class STTService:
    def __init__(self):
        self.api_key = config.DEEPGRAM_API_KEY
        self.use_mock = not self.api_key
    
    async def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe audio using Deepgram REST API"""
        if self.use_mock:
            print("⚠️ Using mock STT (no API key)")
            return ""
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={
                        "Authorization": f"Token {self.api_key}",
                        "Content-Type": "audio/raw",
                    },
                    params={
                        "model": "nova-2",
                        "language": "en-US",
                        "smart_format": "true",
                        "encoding": "mulaw",
                        "sample_rate": "8000",
                        "channels": "1",
                    },
                    content=audio_bytes,
                )
                
                if response.status_code == 200:
                    result = response.json()
                    transcript = (
                        result.get("results", {})
                        .get("channels", [{}])[0]
                        .get("alternatives", [{}])[0]
                        .get("transcript", "")
                    )
                    if transcript:
                        print(f"🎤 STT Result: {transcript}")
                    return transcript
                else:
                    body = response.text
                    print(f"❌ Deepgram API error: {response.status_code} — {body}")
                    return ""
        except Exception as e:
            print(f"❌ Deepgram request failed: {e}")
            return ""

stt = STTService()