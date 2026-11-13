"""Twilio WhatsApp (sandbox) and voice client. Everything degrades to 'not configured' so the web demo keeps working."""

import json
import os
import re

from . import config

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "+14155238886").strip()
VOICE_NUMBER = os.getenv("TWILIO_VOICE_NUMBER", "").strip()
USER_WHATSAPP = os.getenv("USER_WHATSAPP", "").strip()
SUPPLIER_PHONE = os.getenv("SUPPLIER_PHONE", "").strip()
WHATSAPP_LIMIT = 1500
TEMPLATE_SID = os.getenv("TWILIO_WHATSAPP_TEMPLATE_SID", "").strip()
TEMPLATE_VAR = os.getenv("TWILIO_WHATSAPP_TEMPLATE_VAR", "1").strip()
TEMPLATE_LIMIT = 900
OUTBOUND = os.getenv("TWILIO_WHATSAPP_OUTBOUND", "1").strip() != "0"

_client = None


def client():
    global _client
    if _client is None and ACCOUNT_SID and AUTH_TOKEN:
        from twilio.rest import Client

        _client = Client(ACCOUNT_SID, AUTH_TOKEN)
    return _client


def whatsapp_ready() -> bool:
    return bool(client() and USER_WHATSAPP)


def voice_ready() -> bool:
    return bool(client() and VOICE_NUMBER and SUPPLIER_PHONE and config.PUBLIC_URL)


def send_whatsapp(text: str, to: str | None = None) -> str:
    to = to or USER_WHATSAPP
    if not (client() and to):
        return "not configured"
    if not OUTBOUND:
        return "on screen only"
    try:
        if TEMPLATE_SID:
            flat = re.sub(r"\s*\n\s*", " | ", text)
            for start in range(0, len(flat), TEMPLATE_LIMIT):
                variables = {TEMPLATE_VAR: flat[start : start + TEMPLATE_LIMIT]}
                client().messages.create(from_=f"whatsapp:{WHATSAPP_FROM}", to=f"whatsapp:{to}", content_sid=TEMPLATE_SID, content_variables=json.dumps(variables))
            return "sent"
        for start in range(0, len(text), WHATSAPP_LIMIT):
            client().messages.create(from_=f"whatsapp:{WHATSAPP_FROM}", to=f"whatsapp:{to}", body=text[start : start + WHATSAPP_LIMIT])
        return "sent"
    except Exception as e:
        return f"failed: {e}"


def place_call(call_id: str) -> str:
    call = client().calls.create(
        to=SUPPLIER_PHONE,
        from_=VOICE_NUMBER,
        url=f"{config.PUBLIC_URL}/voice/turn?call_id={call_id}",
        status_callback=f"{config.PUBLIC_URL}/voice/status?call_id={call_id}",
        status_callback_event=["completed"],
    )
    return call.sid
