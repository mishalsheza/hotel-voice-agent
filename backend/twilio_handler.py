"""
Twilio Media Streams handler for the Hotel Voice Agent.
Receives audio from phone calls, transcribes with Deepgram,
processes with Groq LLM, and responds with Cartesia TTS.
"""

import json
import base64
import re 
import asyncio
import math
import secrets
from fastapi import WebSocket
from backend import ticket_actions
from backend.stt import stt, STTError
from backend.llm import llm
from backend.tts import synthesize_stream, TTSNotConfigured
from backend.config import config

# --- Voice Activity Detection (VAD) tuning ---
# These may need adjusting based on real call audio quality / background noise.
SILENCE_RMS_THRESHOLD = 400      # RMS (on 16-bit PCM) below this = silence. Raise if picking up noise as speech.
SILENCE_DURATION_MS = 700        # ms of continuous silence that ends an utterance
MIN_SPEECH_MS = 300              # ignore blips shorter than this (coughs, clicks)
MAX_UTTERANCE_MS = 15000         # safety cap so a stuck-open mic doesn't buffer forever
BYTES_PER_MS_MULAW_8KHZ = 8      # 8000 samples/sec, 1 byte/sample (mu-law) = 8 bytes per ms

STT_FAILED_REPLY = "Sorry, I didn't catch that. Could you say it again?"
PROCESSING_FAILED_REPLY = "Sorry, I'm having trouble right now. Let me have the front desk follow up."


# --- µ-law decoding (pure Python; the stdlib audioop module was removed in Python 3.13) ---
def _ulaw_byte_to_linear(byte: int) -> int:
    """Decode one G.711 µ-law byte to a signed 16-bit sample (same values as audioop.ulaw2lin)."""
    byte = ~byte & 0xFF
    exponent = (byte >> 4) & 0x07
    mantissa = byte & 0x0F
    sample = (((mantissa << 3) + 0x84) << exponent) - 0x84
    return -sample if byte & 0x80 else sample


_ULAW_TO_LINEAR = tuple(_ulaw_byte_to_linear(b) for b in range(256))


def mulaw_rms(mulaw_bytes: bytes) -> int:
    """RMS of µ-law audio measured on the decoded 16-bit samples (equivalent to
    audioop.rms(audioop.ulaw2lin(data, 2), 2))."""
    if not mulaw_bytes:
        return 0
    total = sum(_ULAW_TO_LINEAR[b] ** 2 for b in mulaw_bytes)
    return int(math.sqrt(total / len(mulaw_bytes)))


class TwilioCallHandler:
    """Handles a single Twilio phone call via Media Streams"""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.stream_sid = None
        self.call_sid = None

        # VAD / utterance buffering state
        self.audio_buffer = bytearray()
        self.has_speech = False
        self.speech_ms = 0
        self.silence_ms = 0

        self.is_speaking = False  # True while the bot itself is talking (TTS in flight)
        self.conversation_history = []
        self.should_end_call = False
        self.authenticated = False  # set once the "start" event carries the right STREAM_TOKEN
        self.last_ticket_id = None  # id of the most recently created ticket, for corrections

    async def handle_message(self, message: dict):
        """Process incoming WebSocket messages from Twilio"""
        event = message.get('event')

        if event == 'start':
            start = message.get('start') or {}
            if not self._token_is_valid(start):
                # Drop the connection before any STT/LLM/TTS work happens
                self.should_end_call = True
                await self._safe_close()
                return

            self.authenticated = True
            self.stream_sid = start.get('streamSid')
            self.call_sid = start.get('callSid', 'unknown')
            print(f"📞 Call started: {self.call_sid}")
            print(f"📡 Stream: {self.stream_sid}")

            welcome = "Hello! This is the hotel concierge. How can I help you today?"
            await self.send_audio_response(welcome)

        elif not self.authenticated:
            # Nothing but "connected"/"start" is acted on until the stream is authenticated,
            # so a client can't skip "start" and feed audio straight in
            return

        elif event == 'media':
            audio_payload = message['media']
            audio_data = base64.b64decode(audio_payload['payload'])
            await self._handle_audio_frame(audio_data)

        elif event == 'stop':
            print(f"📞 Call ended: {self.call_sid}")
            # Flush whatever was being said when the call ended
            if self.has_speech and self.speech_ms >= MIN_SPEECH_MS:
                await self.process_audio_chunk()

    def _token_is_valid(self, start: dict) -> bool:
        expected = config.STREAM_TOKEN
        if not expected:
            print("❌ Rejecting Twilio stream: STREAM_TOKEN is not set")
            return False
        provided = (start.get('customParameters') or {}).get('token') or ""
        if not secrets.compare_digest(str(provided).encode(), expected.encode()):
            print(f"❌ Rejecting Twilio stream with missing/invalid token (call {start.get('callSid', 'unknown')})")
            return False
        return True

    async def _handle_audio_frame(self, audio_data: bytes):
        """Feed one inbound audio frame into the VAD / utterance buffer.

        Twilio sends small frames (typically ~20ms of mu-law audio) continuously.
        We accumulate frames while the caller is actively speaking, and only
        hand the accumulated utterance off to STT once we detect a real pause,
        so we transcribe whole thoughts instead of arbitrary fixed-size chunks.
        """
        # While the bot itself is speaking, don't try to interpret the caller's
        # audio as a new utterance (avoids reacting to timing artifacts during
        # our own TTS playback window). We still don't echo-cancel; this is a
        # simple "don't listen while talking" guard, not true barge-in support.
        if self.is_speaking:
            self.audio_buffer.clear()
            self.has_speech = False
            self.speech_ms = 0
            self.silence_ms = 0
            return

        frame_ms = max(1, len(audio_data) // BYTES_PER_MS_MULAW_8KHZ)
        is_loud = self._is_speech_frame(audio_data)

        if is_loud:
            self.audio_buffer.extend(audio_data)
            self.has_speech = True
            self.speech_ms += frame_ms
            self.silence_ms = 0
        elif self.has_speech:
            # Keep buffering briefly through short pauses (natural speech has gaps),
            # but track how long the silence has run so we know when to stop.
            self.audio_buffer.extend(audio_data)
            self.silence_ms += frame_ms

        buffer_ms = len(self.audio_buffer) // BYTES_PER_MS_MULAW_8KHZ

        utterance_finished = self.has_speech and (
            self.silence_ms >= SILENCE_DURATION_MS or buffer_ms >= MAX_UTTERANCE_MS
        )

        if utterance_finished:
            if self.speech_ms >= MIN_SPEECH_MS:
                await self.process_audio_chunk()
            else:
                # Too short to be real speech (e.g. a click or breath) — discard.
                self.audio_buffer.clear()

            self.has_speech = False
            self.speech_ms = 0
            self.silence_ms = 0

    def _is_speech_frame(self, mulaw_bytes: bytes) -> bool:
        """Return True if this audio frame looks like speech rather than silence/noise floor."""
        return mulaw_rms(mulaw_bytes) > SILENCE_RMS_THRESHOLD

    async def process_audio_chunk(self):
        """Transcribe the accumulated utterance and process it."""
        if len(self.audio_buffer) < 800:
            self.audio_buffer.clear()
            return

        audio_data = bytes(self.audio_buffer)
        self.audio_buffer.clear()

        try:
            transcript = await stt.transcribe(audio_data)
        except Exception as e:
            # Transcription failed (not silence) — don't leave the caller hanging,
            # and don't let the error escape and tear down the whole call stream
            if not isinstance(e, STTError):
                print(f"❌ Unexpected STT error: {e}")
            await self._say(STT_FAILED_REPLY)
            return

        # Any word counts, so short answers like "no", "ok" or a repeated "yes" get through
        if transcript and re.search(r"\w", transcript):
            print(f"🎤 Caller: {transcript}")
            await self.process_intent(transcript)

    async def process_intent(self, transcript: str):
        """Process transcript with LLM and respond"""
        try:
            self.is_speaking = True

            result = await llm.process_intent(transcript, history=self.conversation_history)

            self.conversation_history.append({"role": "user", "content": transcript})

            if result.get("end_call"):
                farewell = result.get("farewell") or "Thank you for calling. Have a great stay!"
                await self._say(farewell)
                self.should_end_call = True
                self.is_speaking = False
                await asyncio.sleep(1.0)  # let the audio finish playing before closing
                await self._safe_close()
                return

            tool_executed = False
            for tool_call in result.get("tool_calls", []):
                tool_executed = True
                await self.execute_tool(tool_call["name"], tool_call.get("arguments") or {})

            if result.get("response") or not tool_executed:
                # The key is always present (often as None), so `or` rather than a .get() default
                response_text = result.get("response") or "I'm not sure I understood. Could you please repeat?"
                await self._say(response_text)

            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]

        except Exception as e:
            print(f"❌ LLM processing error: {e}")
            await self._say(PROCESSING_FAILED_REPLY)
        finally:
            await asyncio.sleep(0.5)
            self.is_speaking = False

    async def _safe_close(self):
        """Close the websocket exactly once, even if something else tries to close it too."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            await self.websocket.close()
        except Exception as e:
            print(f"⚠️ Error closing websocket (likely already closed): {e}")

    async def _say(self, text: str):
        """Speak a line to the caller and record it in the conversation history."""
        await self.send_audio_response(text)
        self.conversation_history.append({"role": "assistant", "content": text})

    async def execute_tool(self, name: str, args: dict):
        """Execute a tool and respond"""
        if name == "create_ticket":
            saved, response = await ticket_actions.create_ticket(args)
            if saved:
                self.last_ticket_id = saved.get("id")
            await self._say(response)

        elif name == "update_last_ticket":
            if not self.last_ticket_id:
                print("⚠️ update_last_ticket called with no prior ticket; creating a new one instead")
                await self.execute_tool("create_ticket", args)
                return

            _, response = await ticket_actions.update_ticket(self.last_ticket_id, args)
            await self._say(response)

        elif name == "answer_faq":
            await self._say(ticket_actions.answer_faq(args))

        elif name == "set_wakeup_call":
            saved, response = await ticket_actions.set_wakeup_call(args)
            if saved:
                self.last_ticket_id = saved.get("id")
            await self._say(response)

        else:
            print(f"⚠️ Unknown tool: {name}")

    async def send_audio_response(self, text: str):
        """Send TTS audio response through Twilio"""
        print(f"📞 Agent: {text}")

        try:
            audio_chunks = []
            async for chunk in synthesize_stream(text):
                audio_chunks.append(chunk)

            if audio_chunks:
                full_audio = b''.join(audio_chunks)
                audio_base64 = base64.b64encode(full_audio).decode('utf-8')

                message = {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {
                        "payload": audio_base64
                    }
                }
                await self.websocket.send_text(json.dumps(message))
                print(f"🎤 Sent {len(full_audio)} bytes of audio")

                if len(full_audio) > 64000:  # ~8 seconds
                    await asyncio.sleep(0.1)
                    marker = {
                        "event": "mark",
                        "streamSid": self.stream_sid,
                        "mark": {
                            "name": "response_complete"
                        }
                    }
                    await self.websocket.send_text(json.dumps(marker))

        except TTSNotConfigured:
            print(f"⚠️ TTS not configured. Would have said: {text}")
        except Exception as e:
            print(f"❌ TTS error: {e}")
