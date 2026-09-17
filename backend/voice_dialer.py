
"""
Twilio Voice Dialer - Browser-based phone calling
"""
import os
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from twilio.twiml.voice_response import VoiceResponse, Dial
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from backend.config import config

router = APIRouter(prefix="/dialer", tags=["dialer"])

@router.get("/token")
async def get_token():
    """Generate an Access Token for the browser to make outgoing calls."""
    account_sid = config.TWILIO_ACCOUNT_SID
    api_key_sid = config.TWILIO_API_KEY_SID
    api_key_secret = config.TWILIO_API_KEY_SECRET
    twiml_app_sid = config.TWILIO_TWIML_APP_SID
    
    if not all([account_sid, api_key_sid, api_key_secret, twiml_app_sid]):
        return JSONResponse({
            "error": "Twilio credentials not configured"
        }, status_code=500)
    
    # Create access token
    token = AccessToken(
        account_sid,
        api_key_sid,
        api_key_secret,
        identity="hotel_guest"
    )
    
    # Grant voice access
    voice_grant = VoiceGrant(
        outgoing_application_sid=twiml_app_sid,
        incoming_allow=False,
    )
    token.add_grant(voice_grant)
    
    return JSONResponse({
        "token": token.to_jwt()
    })

@router.post("/voice")
async def handle_dialer_voice(request: Request):
    """
    Twilio webhook for outgoing calls from the browser dialer.
    Dials out to the requested number.
    """
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
