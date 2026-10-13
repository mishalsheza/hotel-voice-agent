from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import main
from backend.config import config
from backend.database import db
from backend.models import TicketCreate

ADMIN_KEY = "test-admin-key"
PUBLIC_BASE = "https://example.ngrok-free.app"


@pytest.fixture
def client():
    return TestClient(main.app)


def twilio_post(client, path, params, signature=None, headers=None):
    if signature is None:
        signature = RequestValidator(config.TWILIO_AUTH_TOKEN).compute_signature(PUBLIC_BASE + path, params)
    return client.post(path, data=params, headers={"X-Twilio-Signature": signature, **(headers or {})})


# --- Twilio signature validation ---

CALL_PARAMS = {"CallSid": "CA123", "From": "+15551234567", "To": "+15550001111"}


def test_voice_accepts_valid_signature(client):
    res = twilio_post(client, "/voice", CALL_PARAMS)
    assert res.status_code == 200
    assert "wss://example.ngrok-free.app/twilio-stream" in res.text


def test_voice_rejects_bad_signature(client):
    res = twilio_post(client, "/voice", CALL_PARAMS, signature="not-a-real-signature")
    assert res.status_code == 403


def test_voice_rejects_missing_signature(client):
    res = client.post("/voice", data=CALL_PARAMS)
    assert res.status_code == 403


def test_voice_rejects_signature_for_tampered_params(client):
    signature = RequestValidator(config.TWILIO_AUTH_TOKEN).compute_signature(PUBLIC_BASE + "/voice", CALL_PARAMS)
    res = twilio_post(client, "/voice", {**CALL_PARAMS, "From": "+19999999999"}, signature=signature)
    assert res.status_code == 403


def test_voice_validation_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_VALIDATE_SIGNATURE", False)
    res = client.post("/voice", data=CALL_PARAMS)
    assert res.status_code == 200


def test_voice_fails_closed_without_auth_token(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "")
    res = client.post("/voice", data=CALL_PARAMS, headers={"X-Twilio-Signature": "x"})
    assert res.status_code == 403


# --- Dialer allow-list ---

def test_dialer_voice_dials_hotel_number(client):
    res = twilio_post(client, "/dialer/voice", {"To": "+1 (555) 000-1111"})
    assert res.status_code == 200
    assert "<Dial" in res.text and "+1 (555) 000-1111" in res.text


def test_dialer_voice_refuses_other_numbers(client):
    res = twilio_post(client, "/dialer/voice", {"To": "+447700900123"})
    assert res.status_code == 200
    assert "<Dial" not in res.text
    assert "<Hangup" in res.text


def test_dialer_voice_requires_signature(client):
    res = client.post("/dialer/voice", data={"To": "+15550001111"})
    assert res.status_code == 403


# --- Admin API key ---

@pytest.mark.parametrize("path", ["/tickets", "/tickets/text", "/workers", "/routes", "/dialer/token"])
def test_admin_routes_require_key(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401


def test_tickets_accepts_key_in_header_or_query(client):
    assert client.get("/tickets", headers={"X-API-Key": ADMIN_KEY}).status_code == 200
    res = client.get(f"/tickets?key={ADMIN_KEY}")
    assert res.status_code == 200
    assert res.json() == {"total": 0, "tickets": []}


def test_ticket_status_update_requires_key(client):
    res = client.patch("/tickets/abc/status", json={"status": "completed"})
    assert res.status_code == 401


def test_admin_routes_fail_closed_when_key_not_configured(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_API_KEY", "")
    assert client.get("/tickets", headers={"X-API-Key": ""}).status_code == 503


def test_routes_endpoint_is_registered(client):
    res = client.get("/routes", headers={"X-API-Key": ADMIN_KEY})
    assert res.status_code == 200
    assert "/voice" in {r["path"] for r in res.json()}


async def test_tickets_text_escapes_html(client):
    await db.create_ticket(TicketCreate(
        room="<b>402</b>", request_type="housekeeping", item="<script>alert(1)</script>",
        quantity=1, priority="medium", description="<img src=x onerror=alert(1)>",
    ))

    res = client.get("/tickets/text", headers={"X-API-Key": ADMIN_KEY})

    assert res.status_code == 200
    assert "<script>" not in res.text and "<img" not in res.text and "<b>" not in res.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in res.text


def test_voice_signature_covers_repeated_params(client):
    params = [("CallSid", "CA123"), ("Tag", "a"), ("Tag", "b")]
    multi = {"CallSid": ["CA123"], "Tag": ["a", "b"]}

    class Multi(dict):
        def getlist(self, key):
            return self[key]

    signature = RequestValidator(config.TWILIO_AUTH_TOKEN).compute_signature(PUBLIC_BASE + "/voice", Multi(multi))
    res = client.post("/voice", content=urlencode(params), headers={
        "X-Twilio-Signature": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    assert res.status_code == 200


def test_voice_signature_includes_query_string(client):
    params = {"CallSid": "CA123"}
    signature = RequestValidator(config.TWILIO_AUTH_TOKEN).compute_signature(PUBLIC_BASE + "/voice?src=main", params)
    assert client.post("/voice?src=main", data=params, headers={"X-Twilio-Signature": signature}).status_code == 200
    assert client.post("/voice", data=params, headers={"X-Twilio-Signature": signature}).status_code == 403


@pytest.mark.parametrize("ngrok_url", [
    "example.ngrok-free.app", "https://example.ngrok-free.app", "https://example.ngrok-free.app/",
])
def test_ngrok_url_formats_all_validate(client, monkeypatch, ngrok_url):
    monkeypatch.setattr(config, "NGROK_URL", ngrok_url)
    assert twilio_post(client, "/voice", CALL_PARAMS).status_code == 200


# --- Database outages surface as 503, not as empty lists ---

class BrokenSupabase:
    def table(self, name):
        raise ConnectionError("nodename nor servname provided")


@pytest.mark.parametrize("path", ["/tickets", "/tickets/text", "/workers"])
def test_database_outage_returns_503(client, monkeypatch, path):
    monkeypatch.setattr(db, "_client", BrokenSupabase())
    res = client.get(path, headers={"X-API-Key": ADMIN_KEY})
    assert res.status_code == 503
    assert res.json() == {"error": "database unavailable"}


def test_status_patch_during_outage_returns_503(client, monkeypatch):
    monkeypatch.setattr(db, "_client", BrokenSupabase())
    res = client.patch("/tickets/abc/status", json={"status": "completed"}, headers={"X-API-Key": ADMIN_KEY})
    assert res.status_code == 503


# --- PATCH /tickets/{id}/status keeps workers consistent ---

async def make_assigned_ticket(name="Sam"):
    from backend.models import WorkerCreate

    worker = await db.create_worker(WorkerCreate(name=name))
    ticket = await db.create_ticket(TicketCreate(
        room="402", request_type="housekeeping", item="towels", quantity=1, priority="medium", description="",
    ))
    assert ticket["assigned_worker_id"] == worker["id"]
    return worker, ticket


def patch_status(client, ticket_id, status):
    return client.patch(f"/tickets/{ticket_id}/status", json={"status": status}, headers={"X-API-Key": ADMIN_KEY})


async def worker_status(worker_id):
    return {w["id"]: w["status"] for w in await db.get_workers()}[worker_id]


async def test_reopening_ticket_frees_its_worker(client):
    worker, ticket = await make_assigned_ticket()

    res = patch_status(client, ticket["id"], "open")

    assert res.status_code == 200
    body = res.json()["ticket"]
    assert body["status"] == "open"
    assert body["assigned_worker_id"] is None and body["assigned_worker_name"] is None
    assert await worker_status(worker["id"]) == "available"


async def test_completing_ticket_frees_worker(client):
    worker, ticket = await make_assigned_ticket()

    assert patch_status(client, ticket["id"], "completed").status_code == 200
    assert await worker_status(worker["id"]) == "available"


async def test_old_ticket_status_change_does_not_free_worker_busy_elsewhere(client):
    worker, first = await make_assigned_ticket()
    patch_status(client, first["id"], "completed")
    second = await db.create_ticket(TicketCreate(
        room="402", request_type="housekeeping", item="pillows", quantity=1, priority="medium", description="",
    ))
    assert second["assigned_worker_id"] == worker["id"]

    # Re-completing or reopening the old ticket must not free Sam from the new one
    patch_status(client, first["id"], "completed")
    patch_status(client, first["id"], "open")
    assert await worker_status(worker["id"]) == "busy"


def test_status_patch_unknown_ticket_is_404(client):
    assert patch_status(client, "missing", "open").status_code == 404


# --- NGROK_URL vs Host header ---

def test_warns_when_host_does_not_match_ngrok_url(client, capsys):
    twilio_post(client, "/voice", CALL_PARAMS, headers={"host": "new-host.ngrok-free.app"})
    out = capsys.readouterr().out
    assert "new-host.ngrok-free.app" in out and "NGROK_URL is 'example.ngrok-free.app'" in out


def test_no_warning_when_host_matches_ngrok_url(client, capsys):
    res = twilio_post(client, "/voice", CALL_PARAMS, headers={"host": "example.ngrok-free.app"})
    assert res.status_code == 200
    assert "NGROK_URL is" not in capsys.readouterr().out
