import os
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi import Request
from backend.frontend import HTML_PAGE
from backend.database import db
from backend.models import WorkerCreate
from pydantic import BaseModel
from backend.websocket_handler import WebSocketHandler
from backend.twilio_handler import TwilioCallHandler
from backend.config import config
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

# Get the directory where this file is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIALER_HTML_PATH = os.path.join(BASE_DIR, "test_dialer.html")

app = FastAPI(title="Hotel Voice Agent")

# --- Simple Dialer Page (Direct Route) ---
@app.get("/dialer")
async def dialer_page():
    """Serve the browser dialer page"""
    if not os.path.exists(DIALER_HTML_PATH):
        return JSONResponse(
            {"error": f"test_dialer.html not found at {DIALER_HTML_PATH}"},
            status_code=404
        )
    return FileResponse(DIALER_HTML_PATH)

# --- Dialer Token Endpoint ---
@app.get("/dialer/token")
async def get_token():
    account_sid = config.TWILIO_ACCOUNT_SID
    api_key_sid = config.TWILIO_API_KEY_SID
    api_key_secret = config.TWILIO_API_KEY_SECRET
    twiml_app_sid = config.TWILIO_TWIML_APP_SID
    
    if not all([account_sid, api_key_sid, api_key_secret, twiml_app_sid]):
        return JSONResponse({
            "error": "Twilio credentials not configured"
        }, status_code=500)
    
    token = AccessToken(
        account_sid,
        api_key_sid,
        api_key_secret,
        identity="hotel_guest"
    )
    
    voice_grant = VoiceGrant(
        outgoing_application_sid=twiml_app_sid,
        incoming_allow=False,
    )
    token.add_grant(voice_grant)
    
    return JSONResponse({
        "token": token.to_jwt()
    })

# --- Dialer Voice Webhook ---
@app.post("/dialer/voice")
async def handle_dialer_voice(request: Request):
    from twilio.twiml.voice_response import VoiceResponse, Dial
    
    form = await request.form()
    to_number = form.get('To', config.TWILIO_PHONE_NUMBER)
    
    print(f"📞 Dialer: Outgoing call to {to_number}")
    
    response = VoiceResponse()
    dial = Dial(
        caller_id=config.TWILIO_PHONE_NUMBER,
        timeout=30,
        record=False,
    )
    dial.number(to_number)
    response.append(dial)
    
    return Response(content=str(response), media_type="application/xml")

# --- Routes ---
@app.get("/")
async def get():
    return HTMLResponse(HTML_PAGE)

@app.get("/tickets")
async def get_tickets():
    tickets = db.get_tickets()
    return JSONResponse({
        "total": len(tickets),
        "tickets": tickets
    })

class TicketStatusUpdate(BaseModel):
    status: str  # "open" | "assigned" | "in_progress" | "completed"

@app.patch("/tickets/{ticket_id}/status")
async def update_ticket_status(ticket_id: str, body: TicketStatusUpdate):
    """Update a ticket's status (used by the tickets drawer in the dialer page)"""
    valid_statuses = {"open", "assigned", "in_progress", "completed"}
    if body.status not in valid_statuses:
        return JSONResponse(
            {"error": f"status must be one of {sorted(valid_statuses)}"},
            status_code=400
        )
    if body.status == "completed":
        # complete_ticket also frees the assigned worker and hands them
        # the next open ticket, if any exist.
        updated = db.complete_ticket(ticket_id)
    else:
        updated = db.update_ticket(ticket_id, {"status": body.status})

    if updated is None:
        return JSONResponse({"error": "ticket not found"}, status_code=404)
    return JSONResponse({"ticket": updated})

# --- Workers ---
class WorkerCreateBody(BaseModel):
    name: str

@app.get("/workers")
async def get_workers():
    workers = db.get_workers()
    return JSONResponse({"total": len(workers), "workers": workers})

@app.post("/workers")
async def create_worker(body: WorkerCreateBody):
    if not body.name.strip():
        return JSONResponse({"error": "name is required"}, status_code=400)
    worker = db.create_worker(WorkerCreate(name=body.name.strip()))
    if not worker:
        return JSONResponse({"error": "failed to create worker"}, status_code=500)
    return JSONResponse({"worker": worker})

@app.get("/tickets/text")
async def get_tickets_text():
    tickets = db.get_tickets()
    
    lines = ["=" * 60, "HOTEL TICKETS", "=" * 60]
    for i, t in enumerate(tickets, 1):
        lines.append(f"\n{i}. Room: {t.get('room', 'N/A')}")
        lines.append(f"   Type: {t.get('request_type', 'N/A')}")
        lines.append(f"   Item: {t.get('item', 'N/A')}")
        lines.append(f"   Quantity: {t.get('quantity', 1)}")
        lines.append(f"   Priority: {t.get('priority', 'medium')}")
        lines.append(f"   Status: {t.get('status', 'open')}")
        lines.append(f"   Created: {t.get('created_at', 'N/A')}")
        lines.append(f"   Description: {t.get('description', '')[:50]}...")
    lines.append("\n" + "=" * 60)
    lines.append(f"Total: {len(tickets)} tickets")
    
    return HTMLResponse("<pre>" + "\n".join(lines) + "</pre>")

# --- Twilio Voice Webhook (Incoming calls) ---
@app.post("/voice")
async def handle_voice(request: Request):
    ngrok_url = config.NGROK_URL or request.base_url.hostname
    ws_url = f"wss://{ngrok_url}/twilio-stream" if "ngrok" in ngrok_url else f"wss://{ngrok_url}/twilio-stream"
    
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=ws_url)
    response.append(connect)
    
    print(f"📞 Incoming call → Stream to: {ws_url}")
    
    return Response(content=str(response), media_type="application/xml")

# --- Twilio WebSocket Stream ---
@app.websocket("/twilio-stream")
async def twilio_stream_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("📞 Twilio stream connected")

    handler = TwilioCallHandler(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            await handler.handle_message(message)

            if handler.should_end_call:
                break

    except WebSocketDisconnect:
        print("📞 Twilio stream disconnected")
    except Exception as e:
        print(f"❌ Twilio stream error: {e}")
        import traceback
        traceback.print_exc()
# --- Browser WebSocket ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("✅ Browser client connected")
    
    handler = WebSocketHandler(websocket)
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            await handler.handle_audio(message)
            
    except WebSocketDisconnect:
        print("❌ Browser client disconnected")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("🚀 Hotel Voice Agent")
    print("=" * 60)
    print(f"📍 Browser UI: http://localhost:{config.PORT}")
    print(f"📞 Dialer: http://localhost:{config.PORT}/dialer")
    print(f"📋 Tickets: http://localhost:{config.PORT}/tickets")
    print(f"📁 HTML path: {DIALER_HTML_PATH}")
    print("=" * 60)
    print("\nTo enable phone calls:")
    print("1. Run: ngrok http 8080")
    print("2. Update Twilio phone number webhook to: https://your-ngrok-url.ngrok-free.app/voice")
    print("3. Use the dialer at: http://localhost:8080/dialer")
    print("=" * 60)
    
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)

# --- Debug: List all routes ---
@app.get("/routes")
async def list_routes():
    routes = []
    for route in app.routes:
        routes.append({
            "path": route.path,
            "methods": list(route.methods) if hasattr(route, 'methods') else []
        })
    return JSONResponse(routes)