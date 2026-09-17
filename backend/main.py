import os
import json
import base64
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

app = FastAPI()

# --- Configuration ---
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

if not DEEPGRAM_API_KEY:
    print("WARNING: DEEPGRAM_API_KEY not set in .env file")
else:
    print(f"Using Deepgram API Key: {DEEPGRAM_API_KEY[:8]}...")

if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY not set in .env file")
else:
    print(f"Using Groq API Key: {GROQ_API_KEY[:8]}...")

# --- Initialize Groq Client (only if a key is actually present, so a missing
# key falls back to keyword matching instead of crashing the whole server) ---
openai_client = None
if GROQ_API_KEY:
    openai_client = OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )

# --- Grounded FAQ Answers ---
FAQ_KB = {
    "wifi": "The Wi-Fi password is 'HotelGuest2024'.",
    "checkout": "Checkout is at 11 AM. Late checkout is available until 2 PM.",
    "gym": "The gym is open 24/7 on the second floor.",
    "breakfast": "Breakfast is served from 7-10 AM in the lobby restaurant.",
    "wakeup": "I can set a wake-up call for you - what time would you like it?",
}

# --- LLM Tools ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": "Create a housekeeping or maintenance ticket.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string"},
                    "request_type": {"type": "string", "enum": ["housekeeping", "maintenance"]},
                    "item": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                    "description": {"type": "string"},
                },
                "required": ["room", "request_type", "item", "priority", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_faq",
            "description": "Look up a common hotel question (wifi, checkout, gym, breakfast). Do NOT use this for wake-up calls - use set_wakeup_call instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "enum": [k for k in FAQ_KB.keys() if k != "wakeup"]},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_wakeup_call",
            "description": "Record an actual wake-up call request for the guest's room. Only call this once you have a specific time - if the guest hasn't given one yet, ask them instead of calling this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string"},
                    "time": {"type": "string", "description": "The requested wake-up time, e.g. '7:00 AM'"},
                },
                "required": ["room", "time"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are a friendly hotel voice concierge. The guest is in room 402.

Tools:
- create_ticket: for item requests (towels, pillows, coffee) or reported issues (AC, TV, etc).
- answer_faq: for questions about wifi, checkout, gym, or breakfast.
- set_wakeup_call: for wake-up call requests, once you have a specific time.

Rules:
- One message can contain multiple requests - call multiple tools if needed.
- Every tool call requires all of its fields. If the guest hasn't given you a
  required value (e.g. how many coffees, what time for a wake-up call), do NOT
  guess or default it - respond with a short plain-text clarifying question
  instead of calling the tool, and wait for their answer.
- Only confirm something has been done after the corresponding tool call has
  actually succeeded. Never claim an action was taken if you didn't call a tool for it.
- Keep spoken replies short and natural.
"""

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
    <title>Hotel Voice Agent</title>
    <style>
        body { font-family: Arial, sans-serif; padding: 40px; background: #f5f5f5; }
        .container { max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; }
        button {
            padding: 15px 30px;
            font-size: 18px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            margin: 5px;
        }
        button:disabled { background: #ccc; cursor: not-allowed; }
        button#stopBtn { background: #dc3545; }
        .status { margin-top: 20px; padding: 15px; background: #e9ecef; border-radius: 8px; }
        .transcript { margin-top: 20px; padding: 15px; background: #fff3cd; border-radius: 8px; min-height: 40px; }
        .response { margin-top: 20px; padding: 15px; background: #d4edda; border-radius: 8px; min-height: 40px; }
        .ticket { margin-top: 10px; padding: 10px; background: #cce5ff; border-radius: 8px; display: none; white-space: pre-wrap; font-family: monospace; }
        .debug { margin-top: 20px; padding: 10px; background: #f8f9fa; border-radius: 8px; font-size: 12px; color: #666; word-break: break-all; max-height: 100px; overflow-y: auto; }
        .error-box { margin-top: 10px; padding: 15px; background: #f8d7da; border-radius: 8px; display: none; color: #721c24; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Hotel Voice Agent</h1>
        <p>Click the button and speak naturally.</p>
        <ul>
            <li>"I need towels in room 402"</li>
            <li>"What's the Wi-Fi password?"</li>
            <li>"Towels and my AC is broken"</li>
        </ul>
        <button id="startBtn">Start Speaking</button>
        <button id="stopBtn" disabled>Stop</button>
        <div class="status" id="status">Connecting...</div>
        <div class="transcript" id="transcript">Transcript: </div>
        <div class="response" id="response">Response: </div>
        <div class="ticket" id="ticket"></div>
        <div class="debug" id="debug">Ready</div>
        <div class="error-box" id="errorBox"></div>
    </div>
    <script>
        var ws = null;
        var audioContext = null;
        var mediaStream = null;
        var source = null;
        var processor = null;
        var isRecording = false;
        var reconnectAttempts = 0;
        var maxReconnectAttempts = 5;

        var startBtn = document.getElementById("startBtn");
        var stopBtn = document.getElementById("stopBtn");
        var statusDiv = document.getElementById("status");
        var transcriptDiv = document.getElementById("transcript");
        var responseDiv = document.getElementById("response");
        var ticketDiv = document.getElementById("ticket");
        var debugDiv = document.getElementById("debug");
        var errorBox = document.getElementById("errorBox");

        function updateStatus(msg) {
            statusDiv.textContent = msg;
            console.log("Status:", msg);
        }
        function setTranscript(text) {
            transcriptDiv.textContent = "Transcript: " + text;
        }
        function setResponse(text) {
            responseDiv.textContent = "Response: " + text;
        }
        function showTicket(details) {
            ticketDiv.style.display = "block";
            ticketDiv.textContent = "Ticket: " + JSON.stringify(details, null, 2);
        }
        function setDebug(msg) {
            debugDiv.textContent = msg;
            console.log("Debug:", msg);
        }
        function showError(msg) {
            errorBox.style.display = "block";
            errorBox.textContent = "Error: " + msg;
            updateStatus("Error");
        }
        function hideError() {
            errorBox.style.display = "none";
        }

        function connectWebSocket() {
            var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            var wsUrl = protocol + "//" + window.location.host + "/ws";
            setDebug("Connecting to: " + wsUrl);

            ws = new WebSocket(wsUrl);

            ws.onopen = function () {
                setDebug("WebSocket connected");
                updateStatus('Connected! Click "Start Speaking"');
                startBtn.disabled = false;
                hideError();
                reconnectAttempts = 0;
            };

            ws.onmessage = function (event) {
                try {
                    var data = JSON.parse(event.data);
                    console.log("Received:", data);

                    if (data.type === "transcript") {
                        setTranscript(data.text);
                        setDebug('STT: "' + data.text + '"');
                    } else if (data.type === "response") {
                        setResponse(data.text);
                    } else if (data.type === "ticket_created") {
                        showTicket(data.details);
                        setResponse("Ticket created!");
                    } else if (data.type === "error") {
                        showError(data.text);
                        setResponse("Error: " + data.text);
                    }
                } catch (e) {
                    console.error("Parse error:", e);
                    setDebug("Parse error: " + e.message);
                }
            };

            ws.onclose = function () {
                setDebug("WebSocket closed");
                updateStatus("Disconnected. Reconnecting...");
                startBtn.disabled = true;
                stopBtn.disabled = true;

                if (reconnectAttempts < maxReconnectAttempts) {
                    reconnectAttempts++;
                    setDebug("Reconnecting attempt " + reconnectAttempts + "/" + maxReconnectAttempts);
                    setTimeout(connectWebSocket, 2000);
                } else {
                    showError("Failed to connect to server. Please refresh the page.");
                    updateStatus("Connection failed");
                }
            };

            ws.onerror = function (error) {
                console.error("WebSocket error:", error);
                setDebug("WebSocket error");
            };
        }

        function startRecording() {
            hideError();
            ticketDiv.style.display = "none";
            setDebug("Requesting microphone...");

            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                var url = window.location.href;
                if (url.indexOf("localhost") === -1 && url.indexOf("127.0.0.1") === -1) {
                    showError("Microphone access requires localhost. Please use http://localhost:8080");
                } else {
                    showError("Your browser does not support microphone access. Please use Chrome, Firefox, or Edge.");
                }
                updateStatus("Microphone error");
                return;
            }

            navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, sampleRate: 16000 } })
                .then(function (stream) {
                    mediaStream = stream;
                    setDebug("Microphone access granted");

                    audioContext = new AudioContext({ sampleRate: 16000 });
                    source = audioContext.createMediaStreamSource(mediaStream);

                    processor = audioContext.createScriptProcessor(4096, 1, 1);
                    processor.onaudioprocess = function (e) {
                        if (ws && ws.readyState === WebSocket.OPEN && isRecording) {
                            var inputData = e.inputBuffer.getChannelData(0);
                            var pcmData = new Int16Array(inputData.length);
                            for (var i = 0; i < inputData.length; i++) {
                                pcmData[i] = Math.round(Math.max(-1, Math.min(1, inputData[i])) * 32767);
                            }
                            var bytes = new Uint8Array(pcmData.buffer);
                            var binary = "";
                            for (var j = 0; j < bytes.length; j++) {
                                binary += String.fromCharCode(bytes[j]);
                            }
                            ws.send(JSON.stringify({ type: "audio", data: window.btoa(binary) }));
                        }
                    };

                    source.connect(processor);
                    processor.connect(audioContext.destination);

                    isRecording = true;
                    updateStatus("Recording... Speak now!");
                    startBtn.disabled = true;
                    stopBtn.disabled = false;
                    setDebug("Recording started");
                })
                .catch(function (err) {
                    console.error("Recording error:", err);
                    showError(err.message);
                    updateStatus("Microphone error");
                    startBtn.disabled = false;
                    stopBtn.disabled = true;
                    setDebug("Error: " + err.message);
                });
        }

        function stopRecording() {
            isRecording = false;

            if (processor) {
                try { processor.disconnect(); } catch (e) {}
                processor = null;
            }
            if (source) {
                try { source.disconnect(); } catch (e) {}
                source = null;
            }
            if (mediaStream) {
                mediaStream.getTracks().forEach(function (track) { track.stop(); });
                mediaStream = null;
            }
            if (audioContext && audioContext.state !== "closed") {
                try { audioContext.close(); } catch (e) {}
                audioContext = null;
            }

            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "end" }));
            }

            updateStatus("Processing...");
            startBtn.disabled = false;
            stopBtn.disabled = true;
            setDebug("Processing audio...");
        }

        startBtn.addEventListener("click", startRecording);
        stopBtn.addEventListener("click", stopRecording);

        connectWebSocket();
        setDebug("Ready! Click Start Speaking");

        setTimeout(function () {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                var url = window.location.href;
                if (url.indexOf("localhost") === -1 && url.indexOf("127.0.0.1") === -1) {
                    showError("Please use http://localhost:8080 (not an IP address) for microphone access.");
                } else {
                    showError("Your browser does not support microphone access. Please use Chrome, Firefox, or Edge.");
                }
                startBtn.disabled = true;
            }
        }, 1000);
    </script>
</body>
</html>
"""

# --- HTML Frontend ---
@app.get("/")
async def get():
    return HTMLResponse(HTML_PAGE)

# --- WebSocket Endpoint ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Client connected")

    audio_chunks = []
    # One conversation history per guest session (this websocket connection),
    # so a follow-up answer like "Three." can be understood in the context of
    # a clarifying question asked moments earlier, instead of being processed
    # as an isolated, context-free message.
    conversation = [{"role": "system", "content": SYSTEM_PROMPT}]

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            if message.get('type') == 'audio':
                audio_data = base64.b64decode(message['data'])
                audio_chunks.append(audio_data)

            elif message.get('type') == 'end':
                if audio_chunks:
                    await process_audio(websocket, audio_chunks, conversation)
                    audio_chunks = []

    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

# --- Process Audio ---
async def process_audio(websocket, audio_chunks, conversation):
    try:
        full_audio = b''.join(audio_chunks)
        print(f"Received {len(full_audio)} bytes of audio")

        if not DEEPGRAM_API_KEY:
            transcript = "I need towels in room 402"
            print("Using mock STT (no API key)")
        else:
            print("Calling Deepgram REST API...")

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={
                        "Authorization": f"Token {DEEPGRAM_API_KEY}",
                        "Content-Type": "audio/raw",
                    },
                    params={
                        "model": "nova-2",
                        "language": "en-US",
                        "smart_format": "true",
                        "encoding": "linear16",
                        "sample_rate": "16000",
                        "channels": "1",
                    },
                    content=full_audio,
                )

                if response.status_code == 200:
                    result = response.json()
                    transcript = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
                    if not transcript:
                        transcript = "I need towels in room 402"
                    print(f"STT Result: {transcript}")
                else:
                    print(f"Deepgram API error: {response.status_code}")
                    transcript = "I need towels in room 402"

        await websocket.send_text(json.dumps({
            'type': 'transcript',
            'text': transcript
        }))

        await process_intent_with_llm(websocket, transcript, conversation)

    except Exception as e:
        print(f"Processing error: {e}")
        await websocket.send_text(json.dumps({
            'type': 'error',
            'text': f"Error: {str(e)}"
        }))

# --- LLM Intent Processing ---
async def process_intent_with_llm(websocket, transcript, conversation):
    try:
        print(f"Sending to Groq: {transcript}")

        if not openai_client:
            await process_intent_fallback(websocket, transcript, conversation)
            return

        conversation.append({"role": "user", "content": transcript})

        # Loop the tool-calling exchange (a message can contain several distinct
        # requests, e.g. "towels AND my AC is broken", which the model may
        # resolve across more than one round of tool calls) until it responds
        # with plain text instead of another tool call. Every round - including
        # follow-ups - must still pass tools/tool_choice, or the API rejects
        # any further tool call the model tries to make.
        max_rounds = 5
        reply = "Done!"

        for _ in range(max_rounds):
            response = openai_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=conversation,
                tools=TOOLS,
                tool_choice="auto",
                timeout=30.0,
            )

            msg = response.choices[0].message

            if not msg.tool_calls:
                reply = msg.content or "Could you tell me a bit more about what you need?"
                conversation.append({"role": "assistant", "content": reply})
                break

            conversation.append(msg.model_dump(exclude_none=True))

            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                print(f"Tool called: {name} with {args}")

                result = await run_tool(websocket, name, args)
                conversation.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
        else:
            print("Hit max tool-call rounds without a final text response")

        await websocket.send_text(json.dumps({'type': 'response', 'text': reply}))
        print(f"Response: {reply}")

    except Exception as e:
        print(f"LLM processing error: {e}")
        print("Falling back to keyword matching")
        await process_intent_fallback(websocket, transcript, conversation)

async def process_intent_fallback(websocket, transcript, conversation):
    """Fallback keyword-based intent processing"""
    lower = transcript.lower()
    response_text = ""
    ticket_details = None

    if "towel" in lower:
        ticket_details = {
            'room': '402',
            'request_type': 'housekeeping',
            'item': 'towels',
            'quantity': 2,
            'priority': 'medium',
            'description': transcript
        }
        response_text = "Ticket created for towel request"
        await websocket.send_text(json.dumps({
            'type': 'ticket_created',
            'details': ticket_details
        }))
    elif "wifi" in lower or "password" in lower:
        response_text = "The Wi-Fi password is 'HotelGuest2024'"
    elif "checkout" in lower:
        response_text = "Checkout is at 11 AM. Late checkout available until 2 PM."
    elif "ac" in lower or "air conditioning" in lower:
        ticket_details = {
            'room': '402',
            'request_type': 'maintenance',
            'item': 'air conditioning',
            'priority': 'high',
            'description': transcript
        }
        response_text = "Maintenance ticket created for AC issue"
        await websocket.send_text(json.dumps({
            'type': 'ticket_created',
            'details': ticket_details
        }))
    else:
        response_text = "I'm not sure I understood. You can ask about towels, Wi-Fi, checkout, or AC."

    # Keep the fallback path's exchange in the shared history too, so if the
    # LLM path is used again on a later turn it isn't missing context.
    conversation.append({"role": "user", "content": transcript})
    conversation.append({"role": "assistant", "content": response_text})

    await websocket.send_text(json.dumps({'type': 'response', 'text': response_text}))
    print(f"Response (fallback): {response_text}")

async def run_tool(websocket, name, args):
    if name == "create_ticket":
        await websocket.send_text(json.dumps({'type': 'ticket_created', 'details': args}))
        print(f"Ticket created: {args}")
        return f"Ticket created: {args.get('quantity', '')} {args['item']} for room {args['room']} ({args['priority']} priority)."

    if name == "answer_faq":
        answer = FAQ_KB.get(args.get("topic"), "I don't have information on that.")
        print(f"FAQ: {args['topic']} -> {answer}")
        return answer

    if name == "set_wakeup_call":
        details = {'room': args.get('room', '402'), 'request_type': 'wakeup_call', 'time': args.get('time')}
        await websocket.send_text(json.dumps({'type': 'ticket_created', 'details': details}))
        print(f"Wake-up call recorded: {details}")
        return f"Wake-up call recorded for room {details['room']} at {details['time']}."

    return "Unknown tool."

if __name__ == "__main__":
    import uvicorn
    print("Starting Hotel Voice Agent with Groq LLM!")
    print("Open http://localhost:8080 in your browser")
    print("Using Groq for intelligent intent understanding")
    print("If you don't have API keys set, fallback modes will be used")
    uvicorn.run(app, host="0.0.0.0", port=8080)