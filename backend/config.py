import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # API Keys
    DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    # Twilio
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
    TWILIO_API_KEY_SID = os.getenv("TWILIO_API_KEY_SID", "")
    TWILIO_API_KEY_SECRET = os.getenv("TWILIO_API_KEY_SECRET", "")
    TWILIO_TWIML_APP_SID = os.getenv("TWILIO_TWIML_APP_SID", "")
    NGROK_URL = os.getenv("NGROK_URL", "")
    
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
        if not cls.TWILIO_ACCOUNT_SID or not cls.TWILIO_API_KEY_SID:
            print("⚠️ Twilio credentials not fully set - browser test dialer will fail")
        else:
            print("✅ Twilio configured")

config = Config()
