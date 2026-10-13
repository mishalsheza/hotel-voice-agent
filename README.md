#  Hotel Voice Agent

An AI voice concierge that answers real phone calls for a hotel. Guests can call in and speak naturally — "I need towels in room 402" or "what's the Wi-Fi password?" — and the agent transcribes the request, understands intent, takes real action (creates a ticket, answers an FAQ, sets a wake-up call), and speaks a reply back, in real time.

A browser-based demo mode is also included for testing the pipeline without a phone call.

## How it works

```
Caller (phone)
   │
   ▼  Twilio answers the call, streams live audio to our server
FastAPI + WebSocket (/twilio-stream)
   │
   ▼  Voice activity detection buffers speech until the caller pauses
Deepgram (speech-to-text)
   │
   ▼  Transcript is sent to the LLM along with a set of callable "tools"
Groq LLM — openai/gpt-oss-120b (tool calling)
   │
   ├─ create_ticket        → new housekeeping/maintenance request
   ├─ update_last_ticket   → correct/amend the ticket just created
   ├─ answer_faq           → grounded lookup (wifi, checkout, gym, etc.)
   ├─ set_wakeup_call      → record a wake-up call
   └─ end_call             → hang up when the guest is done
   │
   ▼
Supabase (Postgres) — tickets persisted here, or in-memory if unconfigured
   │
   ▼  Reply text is synthesized to speech
Cartesia (text-to-speech, streamed)
   │
   ▼
Back to the caller over the same call
```

Every external service (STT, LLM, TTS, DB) has a fallback — if a key is missing or a call fails, the app degrades gracefully (keyword matching instead of the LLM, in-memory storage instead of Postgres) rather than crashing.

## Tech stack

| Purpose | Tool |
|---|---|
| Web server / WebSockets | FastAPI + Uvicorn |
| Telephony | Twilio Voice + Media Streams |
| Speech-to-text | Deepgram (`nova-2`) |
| LLM + tool calling | Groq (`openai/gpt-oss-120b`), via the OpenAI SDK |
| Text-to-speech | Cartesia (`sonic-2`, streaming) |
| Database | Supabase (Postgres) |
| Data models | Pydantic |
| Config | python-dotenv |

## Project structure

```
.
├── main.py                    # App entry point: routes, Twilio webhooks, WebSocket endpoints
├── requirements.txt            # requirements-dev.txt adds pytest
├── test_dialer.html            # Browser-based outgoing call dialer (open as /dialer?key=ADMIN_API_KEY)
├── tests/                      # pytest suite (no API keys or network needed)
└── backend/
    ├── config.py               # Loads .env, central config
    ├── database.py             # Supabase client + in-memory fallback
    ├── models.py                # Pydantic models (TicketCreate, Worker, WorkerCreate)
    ├── security.py              # Twilio signature check, admin API key, dial allow-list
    ├── ticket_actions.py        # Tool actions shared by the phone and browser handlers
    ├── stt.py                   # Deepgram speech-to-text
    ├── tts.py                   # Cartesia text-to-speech (streaming)
    ├── llm.py                   # Groq LLM + tool-calling + fallback keyword matching
    ├── tools.py                 # Tool schemas + system prompt
    ├── faq.py                   # Grounded FAQ knowledge base
    ├── twilio_handler.py        # Per-call state machine for real phone calls (VAD, turns)
    ├── websocket_handler.py     # Browser demo audio handler
    └── frontend.py               # HTML for the browser demo page
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```env
# Speech-to-text
DEEPGRAM_API_KEY=

# LLM
GROQ_API_KEY=

# Text-to-speech
CARTESIA_API_KEY=
CARTESIA_VOICE_ID=          # optional, defaults to a preset voice

# Database
SUPABASE_URL=
SUPABASE_KEY=                # SUPABASE_PUBLISHABLE_KEY also accepted

# Twilio (for real phone calls)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
TWILIO_API_KEY_SID=
TWILIO_API_KEY_SECRET=
TWILIO_TWIML_APP_SID=
NGROK_URL=                   # your public tunnel URL, without https://
TWILIO_VALIDATE_SIGNATURE=true   # set to false only for local testing
STREAM_TOKEN=                # random secret; /voice passes it to /twilio-stream, calls without it are dropped

# Protects /tickets, /workers, /dialer/token, /routes (X-API-Key header or ?key=)
ADMIN_API_KEY=

PORT=8080
```

Service keys are optional — missing keys just drop that service into a fallback/mock mode instead of failing. Three exceptions fail closed: without `ADMIN_API_KEY` the admin routes return 503, without `TWILIO_AUTH_TOKEN` the Twilio webhooks are rejected (unless `TWILIO_VALIDATE_SIGNATURE=false`), and without `STREAM_TOKEN` phone calls are dropped at `/twilio-stream`.

### 3. Run the server

```bash
python main.py
```

- Browser demo UI: `http://localhost:8080`
- Browser dialer (place outgoing test calls): `http://localhost:8080/dialer?key=<ADMIN_API_KEY>`
- View created tickets: `http://localhost:8080/tickets?key=<ADMIN_API_KEY>` (JSON) or `/tickets/text?key=...` (readable)

### Run the tests

```bash
pip install -r requirements-dev.txt
pytest
```

### 4. Enable real phone calls

Twilio needs a public URL to reach your local server:

```bash
ngrok http 8080
```

Then set your Twilio phone number's voice webhook to:

```
https://<your-ngrok-url>/voice
```

Call the number — the agent will answer and start listening.

## Example interaction

> **Guest:** "I need towels in room 402, and my AC isn't working."
> **Agent:** *(creates two tickets — housekeeping + high-priority maintenance)* "Ticket created for 2 towels in room 402. Is there anything else I can help you with?"
> **Guest:** "No that's all, thanks."
> **Agent:** "Thank you for calling. Have a great stay!" *(call ends)*

## Known limitations

- No true barge-in — the agent stops listening while it's speaking rather than letting the caller interrupt mid-sentence.
- Voice activity detection is a simple RMS-threshold check, not a trained VAD model.
- The admin routes use a single shared API key, not per-user auth. The browser demo (`/`, `/ws`) is unauthenticated.
- **Single process only for worker assignment.** Auto-assigning tickets to workers is protected by an in-process lock, so it's only race-free with one server process. Running several uvicorn workers or instances could assign two workers to one ticket, or one worker to two tickets. A multi-worker deployment needs the assignment done as an atomic conditional update in Postgres, e.g. `UPDATE tickets SET status='in_progress', assigned_worker_id=$1 WHERE id=$2 AND status='open' AND assigned_worker_id IS NULL` (and the same for claiming the worker: `... WHERE id=$1 AND status='available'`), treating "0 rows updated" as "someone else got there first".

## License

MIT (or update as appropriate).
