import json
import base64
import re
from fastapi import WebSocket
from backend import ticket_actions
from backend.stt import stt, STTError
from backend.llm import llm

# The browser demo (backend/frontend.py) records 16 kHz mono 16-bit PCM
BROWSER_ENCODING = "linear16"
BROWSER_SAMPLE_RATE = 16000
MAX_HISTORY_MESSAGES = 20


class WebSocketHandler:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.audio_chunks = []
        self.conversation_history = []
        self.last_ticket_id = None  # id of the most recently created ticket, for corrections

    async def handle_audio(self, message: dict):
        """Process audio chunks"""
        if message.get('type') == 'audio':
            audio_data = base64.b64decode(message['data'])
            self.audio_chunks.append(audio_data)

        elif message.get('type') == 'end':
            if self.audio_chunks:
                full_audio = b''.join(self.audio_chunks)
                self.audio_chunks = []
                print(f"📥 Received {len(full_audio)} bytes of audio")

                try:
                    transcript = await stt.transcribe(
                        full_audio, encoding=BROWSER_ENCODING, sample_rate=BROWSER_SAMPLE_RATE
                    )
                except STTError:
                    await self.send_error("Sorry, I couldn't transcribe that. Please try again.")
                    return

                # Send transcript back
                await self.websocket.send_text(json.dumps({
                    'type': 'transcript',
                    'text': transcript
                }))

                if not transcript or not re.search(r"\w", transcript):
                    await self.send_response("I didn't hear anything. Please try again.")
                    return

                try:
                    await self.process_intent(transcript)
                except Exception as e:
                    print(f"❌ Browser processing error: {e}")
                    await self.send_error("Sorry, something went wrong. Please try again.")

    async def process_intent(self, transcript: str):
        """Process intent and execute tools"""
        result = await llm.process_intent(transcript, history=self.conversation_history)
        self.conversation_history.append({"role": "user", "content": transcript})

        if result.get("end_call"):
            await self.send_response(result.get("farewell") or "Thank you for calling. Have a great stay!")
            # The browser session stays open, so the next utterance starts a fresh conversation
            self.conversation_history = []
            self.last_ticket_id = None
            return

        # Handle tool calls
        for tool_call in result.get("tool_calls", []):
            await self.execute_tool(tool_call["name"], tool_call.get("arguments") or {})

        # If LLM gave a direct response
        if result.get("response"):
            await self.send_response(result["response"])

        if len(self.conversation_history) > MAX_HISTORY_MESSAGES:
            self.conversation_history = self.conversation_history[-MAX_HISTORY_MESSAGES:]

    async def execute_tool(self, name: str, args: dict):
        """Execute a tool and send results"""
        if name == "create_ticket":
            saved, response = await ticket_actions.create_ticket(args)
            if saved:
                self.last_ticket_id = saved.get("id")
                await self.websocket.send_text(json.dumps({
                    'type': 'ticket_created',
                    'details': args
                }))
            await self.send_response(response)

        elif name == "update_last_ticket":
            if not self.last_ticket_id:
                print("⚠️ update_last_ticket called with no prior ticket; creating a new one instead")
                await self.execute_tool("create_ticket", args)
                return
            _, response = await ticket_actions.update_ticket(self.last_ticket_id, args)
            await self.send_response(response)

        elif name == "answer_faq":
            await self.send_response(ticket_actions.answer_faq(args))

        elif name == "set_wakeup_call":
            saved, response = await ticket_actions.set_wakeup_call(args)
            if saved:
                self.last_ticket_id = saved.get("id")
            await self.send_response(response)

        else:
            await self.send_error(f"Unknown tool: {name}")

    async def send_response(self, text: str):
        await self.websocket.send_text(json.dumps({
            'type': 'response',
            'text': text
        }))
        self.conversation_history.append({"role": "assistant", "content": text})
        print(f"💬 Response: {text}")

    async def send_error(self, text: str):
        await self.websocket.send_text(json.dumps({
            'type': 'error',
            'text': text
        }))
