import json
import base64
from fastapi import WebSocket
from backend.stt import stt
from backend.llm import llm
from backend.database import db
from backend.models import TicketCreate
from backend.faq import get_faq_answer, get_all_topics

class WebSocketHandler:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.audio_chunks = []
    
    async def handle_audio(self, message: dict):
        """Process audio chunks"""
        if message.get('type') == 'audio':
            audio_data = base64.b64decode(message['data'])
            self.audio_chunks.append(audio_data)
        
        elif message.get('type') == 'end':
            if self.audio_chunks:
                full_audio = b''.join(self.audio_chunks)
                print(f"📥 Received {len(full_audio)} bytes of audio")
                
                # Transcribe
                transcript = await stt.transcribe(full_audio)
                
                # Send transcript back
                await self.websocket.send_text(json.dumps({
                    'type': 'transcript',
                    'text': transcript
                }))
                
                # Process with LLM
                await self.process_intent(transcript)
                
                self.audio_chunks = []
    
    async def process_intent(self, transcript: str):
        """Process intent and execute tools"""
        result = await llm.process_intent(transcript)
        
        # Handle tool calls
        for tool_call in result.get("tool_calls", []):
            await self.execute_tool(tool_call["name"], tool_call["arguments"])
        
        # If LLM gave a direct response
        if result.get("response"):
            await self.websocket.send_text(json.dumps({
                'type': 'response',
                'text': result["response"]
            }))
            print(f"💬 Response: {result['response']}")
    
    async def execute_tool(self, name: str, args: dict):
        """Execute a tool and send results"""
        if name == "create_ticket":
            # Create ticket in database
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
            
            await self.websocket.send_text(json.dumps({
                'type': 'ticket_created',
                'details': args
            }))
            
            response = f"Ticket created: {args.get('quantity', '')} {args['item']} for room {args['room']}"
            await self.websocket.send_text(json.dumps({
                'type': 'response',
                'text': response
            }))
            print(f"💬 Response: {response}")
            
        elif name == "answer_faq":
            answer = get_faq_answer(args.get("topic", ""))
            await self.websocket.send_text(json.dumps({
                'type': 'response',
                'text': answer
            }))
            print(f"💬 Response: {answer}")
            
        elif name == "set_wakeup_call":
            # Save wake-up call as ticket
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
            
            response = f"Wake-up call set for {args['time']} in room {args['room']}"
            await self.websocket.send_text(json.dumps({
                'type': 'response',
                'text': response
            }))
            print(f"💬 Response: {response}")
            
        else:
            await self.websocket.send_text(json.dumps({
                'type': 'error',
                'text': f"Unknown tool: {name}"
            }))
