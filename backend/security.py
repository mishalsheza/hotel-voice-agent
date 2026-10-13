"""
Request authentication helpers: Twilio webhook signatures, the admin API key,
and the outbound-dial allow-list.
"""

import re
import secrets
from fastapi import HTTPException, Request
from twilio.request_validator import RequestValidator
from backend.config import config


def _configured_host() -> str:
    """NGROK_URL as a bare host (accepts it with or without a scheme / trailing slash)."""
    return re.sub(r"^\w+://", "", config.NGROK_URL.strip()).rstrip("/")


def public_host(request: Request) -> str:
    """Host the outside world (Twilio) uses to reach us, e.g. abcd.ngrok-free.app.

    Uses NGROK_URL when set; otherwise the Host header, which ngrok forwards
    unchanged by default.
    """
    if config.NGROK_URL:
        return _configured_host()
    return request.headers.get("host") or request.url.netloc


def warn_if_host_mismatch(request: Request):
    """Log loudly when a webhook arrives on a host other than NGROK_URL.

    That almost always means ngrok restarted with a new URL: signature checks
    will fail and the stream URL we hand Twilio will be wrong.
    """
    if not config.NGROK_URL:
        return
    host = (request.headers.get("host") or "").split(":")[0].lower()
    expected = _configured_host().split(":")[0].lower()
    if host and host != expected:
        print(
            f"⚠️ Webhook arrived on host '{host}' but NGROK_URL is '{expected}'. "
            "If ngrok restarted with a new URL, update NGROK_URL in .env and restart the server."
        )


def public_url(request: Request) -> str:
    """The exact URL Twilio requested (and signed), even when running behind ngrok."""
    url = f"https://{public_host(request)}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    return url


async def verify_twilio_signature(request: Request):
    """FastAPI dependency: reject Twilio webhooks without a valid X-Twilio-Signature."""
    warn_if_host_mismatch(request)

    if not config.TWILIO_VALIDATE_SIGNATURE:
        return

    if not config.TWILIO_AUTH_TOKEN:
        print("❌ Rejecting Twilio webhook: TWILIO_AUTH_TOKEN is not set (or set TWILIO_VALIDATE_SIGNATURE=false for local testing)")
        raise HTTPException(status_code=403, detail="Twilio signature validation is not configured")

    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()  # Starlette caches this, so the route can read it again
    url = public_url(request)
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    # Pass the FormData itself (not dict(form)) so repeated params keep all their values
    if not signature or not validator.validate(url, form, signature):
        print(f"❌ Rejecting webhook with invalid Twilio signature (validated against {url})")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


async def require_admin_key(request: Request):
    """FastAPI dependency: require ADMIN_API_KEY via the X-API-Key header or ?key= query param."""
    expected = config.ADMIN_API_KEY
    if not expected:
        print("❌ Rejecting admin request: ADMIN_API_KEY is not set")
        raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured on the server")

    provided = request.headers.get("X-API-Key") or request.query_params.get("key") or ""
    if not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def _normalize_number(number: str) -> str:
    return re.sub(r"[^\d+]", "", number or "")


def is_allowed_dial_target(number: str) -> bool:
    """The browser dialer may only call the hotel's own Twilio number (prevents toll fraud)."""
    allowed = _normalize_number(config.TWILIO_PHONE_NUMBER)
    return bool(allowed) and _normalize_number(number) == allowed
