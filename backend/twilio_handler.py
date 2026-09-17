"""
Twilio Media Streams handler for the Hotel Voice Agent.
Receives audio from phone calls, transcribes with Deepgram,
processes with Groq LLM, and responds with Cartesia TTS.
"""

import json
import base64
import re 
import asyncio
import audioop
from collections import deque
from fastapi import WebSocket
from backend.stt import stt
from backend.llm import llm
from backend.database import db
from backend.models import TicketCreate
from backend.faq import get_faq_answer
from backend.tts import synthesize_stream, TTSNotConfigured
from backend.config import config

# --- Voice Activity Detection (VAD) tuning ---
# These may need adjusting based on real call audio quality / background noise.
SILENCE_RMS_THRESHOLD = 400      # RMS (on 16-bit PCM) below this = silence. Raise if picking up noise as speech.
SILENCE_DURATION_MS = 700        # ms of continuous silence that ends an utterance
MIN_SPEECH_MS = 300              # ignore blips shorter than this (coughs, clicks)
MAX_UTTERANCE_MS = 15000         # safety cap so a stuck-open mic doesn't buffer forever
BYTES_PER_MS_MULAW_8KHZ = 8      # 8000 samples/sec, 1 byte/sample (mu-law) = 8 bytes per ms


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
        self.last_transcript = ""
        self.conversation_history = []
        self.should_end_call = False
        self.last_ticket_id = None  # id of the most recently created ticket, for corrections

    async def handle_message(self, message: dict):
        """Process incoming WebSocket messages from Twilio"""
        event = message.get('event')

        if event == 'start':
            self.stream_sid = message['start']['streamSid']
            self.call_sid = message['start'].get('callSid', 'unknown')
            print(f"📞 Call started: {self.call_sid}")
            print(f"📡 Stream: {self.stream_sid}")

            welcome = "Hello! This is the hotel concierge. How can I help you today?"
            await self.send_audio_response(welcome)

        elif event == 'media':
            audio_payload = message['media']
            audio_data = base64.b64decode(audio_payload['payload'])
            await self._handle_audio_frame(audio_data)

            

        elif event == 'stop':
            print(f"📞 Call ended: {self.call_sid}")
            # Flush whatever was being said when the call ended
            if self.has_speech and self.speech_ms >= MIN_SPEECH_MS:
                await self.process_audio_chunk()

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
        try:
            pcm16 = audioop.ulaw2lin(mulaw_bytes, 2)
            rms = audioop.rms(pcm16, 2)
            return rms > SILENCE_RMS_THRESHOLD
        except Exception as e:
            print(f"⚠️ VAD decode error: {e}")
            return False

    async def process_audio_chunk(self):
        """Transcribe the accumulated utterance and process it."""
        if len(self.audio_buffer) < 800:
            self.audio_buffer.clear()
            return

        audio_data = bytes(self.audio_buffer)
        self.audio_buffer.clear()

        try:
            transcript = await stt.transcribe(audio_data)

            if transcript and len(transcript.strip()) > 2:
                if transcript != self.last_transcript:
                    self.last_transcript = transcript
                    print(f"🎤 Caller: {transcript}")
                    await self.process_intent(transcript)

        except Exception as e:
            print(f"❌ Error processing audio: {e}")

    async def process_intent(self, transcript: str):
        """Process transcript with LLM and respond"""
        try:
            self.is_speaking = True

            result = await llm.process_intent(transcript, history=self.conversation_history)

            self.conversation_history.append({"role": "user", "content": transcript})

            if result.get("end_call"):
                farewell = result.get("farewell") or "Thank you for calling. Have a great stay!"
                await self.send_audio_response(farewell)
                self.conversation_history.append({"role": "assistant", "content": farewell})
                self.should_end_call = True
                self.is_speaking = False
                await asyncio.sleep(1.0)  # let the audio finish playing before closing
                await self._safe_close()
                return

            tool_executed = False
            for tool_call in result.get("tool_calls", []):
                tool_executed = True
                await self.execute_tool(tool_call["name"], tool_call["arguments"])

            if result.get("response") or not tool_executed:
                response_text = result.get("response", "I'm not sure I understood. Could you please repeat?")
                await self.send_audio_response(response_text)
                self.conversation_history.append({"role": "assistant", "content": response_text})

            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]

        except Exception as e:
            print(f"❌ LLM processing error: {e}")
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

    async def execute_tool(self, name: str, args: dict):
        """Execute a tool and respond"""
        if name == "create_ticket":
            ticket_data = TicketCreate(
                room=args.get("room", "402"),
                request_type=args.get("request_type", "housekeeping"),
                item=args.get("item", ""),
                quantity=args.get("quantity", 1),
                priority=args.get("priority", "medium"),
                description=args.get("description", ""),
            )
            saved = db.create_ticket(ticket_data)
            if saved:
                print(f"💾 Ticket saved: {saved}")
                self.last_ticket_id = saved.get("id")

            response = f"Ticket created for {args.get('quantity', '')} {args['item']} in room {args['room']}. Is there anything else I can help you with?"
            await self.send_audio_response(response)
            self.conversation_history.append({"role": "assistant", "content": response})

        elif name == "update_last_ticket":
            if not self.last_ticket_id:
                print("⚠️ update_last_ticket called with no prior ticket; creating a new one instead")
                await self.execute_tool("create_ticket", {
                    "room": args.get("room", "402"),
                    "request_type": args.get("request_type", "housekeeping"),
                    "item": args.get("item", ""),
                    "quantity": args.get("quantity", 1),
                    "priority": args.get("priority", "medium"),
                    "description": args.get("description", ""),
                })
                return

            updates = {k: v for k, v in args.items() if v is not None}
            updated = db.update_ticket(self.last_ticket_id, updates)

            if updated:
                print(f"💾 Ticket updated: {updated}")
                bits = ", ".join(f"{k} to {v}" for k, v in updates.items())
                response = f"Got it, I've updated the {bits}. Anything else?"
            else:
                print(f"⚠️ Could not find ticket {self.last_ticket_id} to update")
                response = "Sorry, I had trouble updating that. Could you repeat the request?"

            await self.send_audio_response(response)
            self.conversation_history.append({"role": "assistant", "content": response})

        elif name == "answer_faq":
            answer = get_faq_answer(args.get("topic", ""))
            if not answer or answer == "I don't have information on that.":
                answer = "I don't have that information. Would you like me to connect you with the front desk?"
            answer += " Anything else?"
            await self.send_audio_response(answer)
            self.conversation_history.append({"role": "assistant", "content": answer})

        elif name == "set_wakeup_call":
            ticket_data = TicketCreate(
                room=args.get("room", "402"),
                request_type="wakeup_call",
                item="wake-up call",
                quantity=1,
                priority="medium",
                description=f"Wake-up call at {args.get('time', '')}",
            )
            saved = db.create_ticket(ticket_data)
            if saved:
                print(f"💾 Wake-up call saved: {saved}")
                self.last_ticket_id = saved.get("id")

            response = f"Wake-up call set for {args['time']} in room {args['room']}. Anything else?"
            await self.send_audio_response(response)
            self.conversation_history.append({"role": "assistant", "content": response})

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

    async def send_silence(self, duration_ms: int = 500):
        """Send silence to allow Twilio's VAD to reset"""
        silence_bytes = b'\x7F' * int(duration_ms * 8)
        audio_base64 = base64.b64encode(silence_bytes).decode('utf-8')

        message = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {
                "payload": audio_base64
            }
        }
        await self.websocket.send_text(json.dumps(message))