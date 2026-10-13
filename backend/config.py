import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # API Keys
    DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")

    # Code should only use config.SUPABASE_KEY. The env var SUPABASE_KEY wins;
    # SUPABASE_PUBLISHABLE_KEY is accepted as a fallback for older .env files.
    SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

    CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")

    # Twilio
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
    TWILIO_API_KEY_SID = os.getenv("TWILIO_API_KEY_SID", "")
    TWILIO_API_KEY_SECRET = os.getenv("TWILIO_API_KEY_SECRET", "")
    TWILIO_TWIML_APP_SID = os.getenv("TWILIO_TWIML_APP_SID", "")
    NGROK_URL = os.getenv("NGROK_URL", "")
    # Reject Twilio webhooks without a valid X-Twilio-Signature. Only turn off for local testing.
    TWILIO_VALIDATE_SIGNATURE = os.getenv("TWILIO_VALIDATE_SIGNATURE", "true").strip().lower() not in ("0", "false", "no", "off")

    # Shared secret passed to /twilio-stream as a <Stream> <Parameter>; calls without it are dropped
    STREAM_TOKEN = os.getenv("STREAM_TOKEN", "")

    # Shared key protecting the admin/dashboard routes (/tickets, /workers, /dialer/token, /routes)
    ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

    # Server
    PORT = int(os.getenv("PORT", 8080))

    @classmethod
    def validate(cls):
        """Check if required config is present"""
        if not cls.DEEPGRAM_API_KEY:
            print("⚠️ DEEPGRAM_API_KEY not set - STT will use mock mode")
        if not cls.GROQ_API_KEY:
            print("⚠️ GROQ_API_KEY not set - LLM will use fallback mode")
        if not cls.SUPABASE_URL or not cls.SUPABASE_KEY:
            print("⚠️ Supabase credentials not set - tickets will be in-memory only")
        else:
            print("✅ Supabase configured")
        if not cls.CARTESIA_API_KEY:
            print("⚠️ CARTESIA_API_KEY not set - TTS will be unavailable")
        if not cls.TWILIO_ACCOUNT_SID or not cls.TWILIO_API_KEY_SID:
            print("⚠️ Twilio credentials not fully set - browser test dialer will fail")
        else:
            print("✅ Twilio configured")
        if cls.TWILIO_VALIDATE_SIGNATURE and not cls.TWILIO_AUTH_TOKEN:
            print("⚠️ TWILIO_AUTH_TOKEN not set - Twilio webhooks (/voice, /dialer/voice) will be rejected")
        if not cls.TWILIO_VALIDATE_SIGNATURE:
            print("⚠️ TWILIO_VALIDATE_SIGNATURE is off - Twilio webhooks are NOT authenticated")
        if not cls.STREAM_TOKEN:
            print("⚠️ STREAM_TOKEN not set - phone calls will be dropped at /twilio-stream")
        if not cls.ADMIN_API_KEY:
            print("⚠️ ADMIN_API_KEY not set - admin routes (/tickets, /workers, /dialer/token, /routes) will be rejected")

config = Config()