"""
Test setup: make sure no real API keys from .env are picked up.

python-dotenv never overrides variables that are already set, so blanking them
here (before any backend module is imported) forces every service into its
offline/fallback mode: mock STT, keyword LLM, in-memory database.
"""

import os

for _key in (
    "DEEPGRAM_API_KEY", "GROQ_API_KEY", "CARTESIA_API_KEY",
    "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_PUBLISHABLE_KEY",
    "TWILIO_ACCOUNT_SID", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET", "TWILIO_TWIML_APP_SID",
):
    os.environ[_key] = ""

os.environ["TWILIO_AUTH_TOKEN"] = "test-auth-token"
os.environ["TWILIO_PHONE_NUMBER"] = "+15550001111"
os.environ["TWILIO_VALIDATE_SIGNATURE"] = "true"
os.environ["NGROK_URL"] = "example.ngrok-free.app"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["STREAM_TOKEN"] = "test-stream-token"

import pytest  # noqa: E402
from backend.database import db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_in_memory_db():
    assert not db.is_connected(), "tests must never talk to the real Supabase"
    db._tickets = []
    db._workers = []
    yield
    db._tickets = []
    db._workers = []


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_ws():
    return FakeWebSocket()
