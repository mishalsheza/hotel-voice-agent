"""
Text-to-speech module using Cartesia's streaming TTS API.
Outputs audio in 8kHz µ-law format (pcm_mulaw) - exactly what Twilio expects.
"""

import os
import sys
import asyncio
from dotenv import load_dotenv
from cartesia import AsyncCartesia

load_dotenv()

CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
DEFAULT_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "694f9389-aac1-45b6-b726-9d9369183238")

# Twilio Media Streams expects exactly this format
TWILIO_OUTPUT_FORMAT = {
    "container": "raw",
    "encoding": "pcm_mulaw",
    "sample_rate": 8000,
}


class TTSNotConfigured(Exception):
    """Raised when CARTESIA_API_KEY is missing"""
    pass


async def synthesize_stream(text: str, voice_id: str = DEFAULT_VOICE_ID):
    """
    Stream synthesized speech as it's generated.
    Yields raw audio bytes (8kHz µ-law) ready for Twilio.
    """
    if not CARTESIA_API_KEY:
        raise TTSNotConfigured("CARTESIA_API_KEY is not set in .env")

    client = AsyncCartesia(api_key=CARTESIA_API_KEY)
    try:
        async with client.tts.websocket_connect() as ws:
            ctx = ws.context(
                model_id="sonic-2",
                voice={"mode": "id", "id": voice_id},
                output_format=TWILIO_OUTPUT_FORMAT,
                language="en",
            )
            await ctx.push(text)
            await ctx.no_more_inputs()

            async for response in ctx.receive():
                if response.type == "chunk" and response.audio:
                    yield response.audio
                elif response.type == "error":
                    print(f"Cartesia TTS error: {response.message or response.title}")
    finally:
        await client.close()


async def synthesize_full(text: str, voice_id: str = DEFAULT_VOICE_ID) -> bytes:
    """Synthesize full response as bytes (non-streaming convenience wrapper)"""
    chunks = []
    async for chunk in synthesize_stream(text, voice_id):
        chunks.append(chunk)
    return b"".join(chunks)


async def _cli_test(text: str):
    """Test TTS from command line"""
    print(f"Synthesizing: {text!r}")
    try:
        audio = await synthesize_full(text)
        print(f"Received {len(audio)} bytes of audio")
        
        with open("output.raw", "wb") as f:
            f.write(audio)
            
        print("Saved to output.raw")
        print("Play with:")
        print("  ffplay -f mulaw -ar 8000 output.raw")
    except TTSNotConfigured:
        print("CARTESIA_API_KEY is not set - add it to your .env file first.")
        print("Get a free key at: https://play.cartesia.ai/keys")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python tts.py "Text to speak"')
        sys.exit(1)
    
    text_arg = " ".join(sys.argv[1:])
    asyncio.run(_cli_test(text_arg))
