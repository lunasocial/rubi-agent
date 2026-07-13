"""Voice layer: missed-call detection via call forwarding.

Flow: a call to the tenant's Linq number is forwarded (Linq-side setting) to our Twilio number.
Twilio hits /voice/incoming -> we return TwiML that rings the tenant's REAL phone (forward_phone).
If nobody answers, Twilio hits /voice/after with DialCallStatus -> we text the caller back from the
tenant's own line (the number they just called). Consent + pause are honored (governance).

No Twilio SDK needed , TwiML is XML we render, callbacks are form posts we parse. Signature check
runs in shadow mode until RUBI_VERIFY_TWILIO=1 (same pattern as the Linq webhook).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from urllib.parse import parse_qs
from xml.sax.saxutils import escape

import businesses
import governance
import linq
import store

logger = logging.getLogger("rubi.voice")

_MISSED = ("no-answer", "busy", "failed")
_DIAL_TIMEOUT = int(os.getenv("RUBI_VOICE_RING_SECONDS", "20"))


def parse_form(raw: bytes) -> dict:
    try:
        return {k: v[0] for k, v in parse_qs((raw or b"").decode()).items()}
    except Exception:
        return {}


def verify(raw: bytes, url: str, headers) -> bool:
    """Twilio X-Twilio-Signature: HMAC-SHA1(auth_token, url + sorted(k+v)). Shadow mode unless
    RUBI_VERIFY_TWILIO=1."""
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    enforce = os.getenv("RUBI_VERIFY_TWILIO", "0") == "1"
    if not token:
        return not enforce
    try:
        h = {k.lower(): v for k, v in dict(headers).items()}
        sig = h.get("x-twilio-signature", "")
        params = parse_form(raw)
        signed = url + "".join(k + params[k] for k in sorted(params))
        expected = base64.b64encode(
            hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()).decode()
        ok = hmac.compare_digest(sig, expected)
    except Exception:
        ok = False
    logger.info("twilio sig verified=%s enforce=%s", ok, enforce)
    return ok if enforce else True


def tenant_for_call(form: dict) -> str:
    """Which tenant does this call belong to? The Twilio DID it arrived on (To) is matched against
    tenant voice_number configs; the shared demo setup falls back to the active tenant."""
    to = (form.get("To") or "").strip()
    if to:
        for slug in businesses.all_slugs():
            if businesses.get(slug).get("voice_number", "").strip() == to:
                return slug
    return businesses.active_slug()


def _xml(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def incoming_twiml(slug: str, base_url: str) -> str:
    """Ring the tenant's real phone; when the dial ends Twilio posts the outcome to /voice/after."""
    cfg = businesses.get(slug)
    target = (cfg.get("forward_phone") or cfg.get("owner_phone") or cfg.get("phone") or "").strip()
    action = f"{base_url.rstrip('/')}/voice/after?tenant={slug}"
    if not target:
        # nowhere to forward: treat every call as missed -> text-back handles it
        return _xml(f'<Redirect method="POST">{escape(action)}</Redirect>')
    return _xml(
        f'<Dial timeout="{_DIAL_TIMEOUT}" action="{escape(action)}" method="POST">'
        f'<Number>{escape(target)}</Number></Dial>')


async def after_dial(slug: str, form: dict, base_url: str = "") -> str:
    """Dial finished. Answered -> hang up quietly. Missed -> tell the caller a text is coming and
    send it from the tenant's line."""
    status = (form.get("DialCallStatus") or "no-answer").lower()
    caller = (form.get("From") or "").strip()
    if status not in _MISSED or not caller.startswith("+"):
        return _xml("<Hangup/>")
    name = businesses.get(slug).get("name", "us")
    sent = False
    if os.getenv("RUBI_MISSED_CALL_ENABLED", "0") == "1" and governance.can_text(slug, caller):
        msg = (f"Hi, sorry we missed your call at {name}. I'm the assistant , I can help with "
               "hours, menu, or a booking right here. Reply STOP to opt out.")
        sent = await linq.send(caller, msg)
        if sent:
            store.log_message(slug, caller, "assistant", msg)
            await governance.count(slug, "missed_call")
    logger.info("missed call [%s] from %s -> text_sent=%s", slug, caller, sent)
    if sent:
        return _xml(f"<Say>Sorry, we couldn't get to the phone at {escape(name)}. "
                    "We just sent you a text , you can book or ask anything right there.</Say>")
    return _xml(f"<Say>Sorry, we couldn't get to the phone at {escape(name)}. "
                "Please try again shortly.</Say>")
