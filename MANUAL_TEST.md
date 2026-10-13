# Manual end-to-end test: real phone call

A checklist for testing a real call through Twilio → ngrok → this server, using the `/dialer` page.

## 0. One-time setup

- [ ] `pip install -r requirements.txt` in your `.venv`.
- [ ] `.env` has all of: `DEEPGRAM_API_KEY`, `GROQ_API_KEY`, `CARTESIA_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY` (or `SUPABASE_PUBLISHABLE_KEY`), `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_TWIML_APP_SID`, `NGROK_URL`, `ADMIN_API_KEY`, `STREAM_TOKEN`.
  - Generate `ADMIN_API_KEY` and `STREAM_TOKEN` (use two different values) with: `python -c "import secrets; print(secrets.token_urlsafe(24))"`
  - `TWILIO_AUTH_TOKEN` must be the **Auth Token** of the account that owns the phone number, not the API key secret.
- [ ] Your Supabase project is active (not paused) and has the `tickets` and `workers` tables.
- [ ] **Recommended: reserve a fixed ngrok domain** so the URL never changes between runs. Every ngrok account includes one free static domain: ngrok dashboard → **Domains**, then claim it. With a fixed domain you set `NGROK_URL` and the two Twilio URLs once, and the "new ngrok URL → 403" problem below goes away.

## 1. Start the tunnel and the server

1. [ ] Start ngrok:
   - With your fixed domain (recommended): `ngrok http 8080 --url=your-name.ngrok-free.app` (older ngrok agents use `--domain=` instead of `--url=`).
   - Without one: `ngrok http 8080`, then copy the host from the `Forwarding` line, e.g. `abcd-1234.ngrok-free.app`. This changes every time ngrok restarts.
2. [ ] Put that host into `.env` as `NGROK_URL=your-name.ngrok-free.app` (no `https://`, no trailing `/`). Keep only one `NGROK_URL` line.
3. [ ] `python main.py`. The startup log should show no ⚠️ lines for the keys above.

> `NGROK_URL` is read once at startup. **Restart the server every time you change it.**

## 2. Point Twilio at the tunnel

In the Twilio Console:

- [ ] **Phone Numbers → your number → Voice → "A call comes in"**: Webhook, `https://<NGROK_URL>/voice`, **HTTP POST**.
- [ ] **TwiML Apps → the app in `TWILIO_TWIML_APP_SID` → Voice Request URL**: `https://<NGROK_URL>/dialer/voice`, **HTTP POST**.

Use these exact URLs: `https`, no trailing slash, no extra query string.

Quick reachability check (expect **403**, which proves the request reached the server and signature checking is on):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<NGROK_URL>/voice
```

## 3. Place the call

1. [ ] Open `http://localhost:8080/dialer?key=<ADMIN_API_KEY>` (use `localhost` so the browser allows the mic).
2. [ ] The status lamp should read **Ready to call**. The number field defaults to the hotel number, and that is the only number the dialer is allowed to call.
3. [ ] Click **Call** and allow microphone access. You should hear: *"Hello! This is the hotel concierge…"*
4. [ ] Run this script, pausing about 1 second after each line:

| You say | Expect to hear | Check |
|---|---|---|
| "I need two towels in room 405." | "Ticket created for 2 towels in room 405…" | New ticket in the Tickets drawer |
| "Actually, make that three." | "Got it, I've updated the quantity to 3…" | Same ticket now has quantity 3, not a new ticket |
| "What's the Wi-Fi password?" | The Wi-Fi password | No ticket |
| "Can I get a wake-up call at 7 AM?" | "Wake-up call set for 7 AM in room …" | `wakeup_call` ticket |
| "Yes." then "Yes." again | A reply to **both** | Repeated answers aren't dropped |
| "No, that's all, thanks." | A goodbye, then the call hangs up | |

5. [ ] Open the **Tickets** drawer (or `http://localhost:8080/tickets/text?key=<ADMIN_API_KEY>`) and the Supabase `tickets` table. The tickets should appear in both.
6. [ ] If you have workers added, a new ticket should go to `in_progress` with a worker assigned. **Mark done** should hand that worker the next open ticket.
7. [ ] Optional: call the Twilio number from your own phone and repeat a couple of lines.

## Troubleshooting

### 403 on `/voice` or `/dialer/voice` (usually after ngrok restarted with a new URL)

Symptoms: the call fails with "an application error has occurred". The Twilio Debugger (Monitor → Logs → Errors) shows **11200** with HTTP 403. The server log shows:

```
⚠️ Webhook arrived on host '<new-host>' but NGROK_URL is '<old-host>'. ...
❌ Rejecting webhook with invalid Twilio signature (validated against https://<old-host>/voice)
```

The `⚠️` line names both hosts. If it appears, `NGROK_URL` is out of date.

Fix:
1. [ ] Compare the `<host>` in that log line with the current ngrok `Forwarding` host and the URLs in the Twilio Console. All three must match exactly.
2. [ ] Update `NGROK_URL` in `.env` to the new host and **restart the server**.
3. [ ] Update **both** Twilio URLs from step 2: the phone number webhook and the TwiML App.
4. [ ] Still 403? Check that `TWILIO_AUTH_TOKEN` is the current Auth Token for the account. Rotating it in the Console invalidates the old one.
5. [ ] Last resort, local only: `TWILIO_VALIDATE_SIGNATURE=false` and restart. **Turn it back on afterwards.**

Long-term fix: use a fixed ngrok domain (see step 0).

A stale `NGROK_URL` also breaks the audio stream, because `/voice` tells Twilio to connect to `wss://<NGROK_URL>/twilio-stream`. So "call connects, then immediate silence or hang-up" usually has the same fix.

### Other problems

| Symptom | Likely cause |
|---|---|
| Call connects then drops straight away, log shows `❌ Rejecting Twilio stream` | `STREAM_TOKEN` not set, or changed without restarting the server |
| Dialer lamp shows **Not authorized** | Missing or wrong `?key=` in the page URL, or `ADMIN_API_KEY` not set on the server |
| You hear "Sorry, this dialer can only call the hotel." | The number field isn't `TWILIO_PHONE_NUMBER` |
| You hear "Sorry, I couldn't log that…" | The database save failed. Check the server log for `❌ Supabase error` and that the Supabase project is active |
| You hear "Sorry, I didn't catch that…" | Deepgram request failed. Check `DEEPGRAM_API_KEY` and the log for `❌ Deepgram` |
| Call connects but the agent never speaks | `CARTESIA_API_KEY` missing or invalid (log: `TTS not configured` / `TTS error`) |
| Tickets drawer says "Could not load tickets. database unavailable" | Supabase is unreachable (paused project, wrong `SUPABASE_URL`). Check the log for `❌ Supabase error` |
