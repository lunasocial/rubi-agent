"""Minimal Linq client , send a text and parse an inbound webhook. Mirrors the Linq partner API
shape used elsewhere; scoped to this business's own line via LINQ_FROM.
"""
import logging
import os

import httpx

logger = logging.getLogger("rubi.linq")

_API_BASE = os.getenv("LINQ_API_BASE", "https://api.linqapp.com/api/partner/v3")
_SEND_URL = os.getenv("LINQ_SEND_URL", f"{_API_BASE}/chats")
_KEY = os.getenv("LINQ_API_KEY", "")
_FROM = os.getenv("LINQ_FROM", "")   # this business's Linq number


async def send(recipient: str, text: str) -> bool:
    if not _KEY:
        logger.error("LINQ_API_KEY not set")
        return False
    body = {"to": [recipient], "message": {"parts": [{"type": "text", "value": text}]}}
    if _FROM:
        body["from"] = _FROM
    headers = {"Authorization": f"Bearer {_KEY}", "Content-Type": "application/json",
               "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(_SEND_URL, headers=headers, json=body)
        if r.status_code in (200, 201, 202):
            return True
        logger.warning("linq send -> %s %s", r.status_code, r.text[:200])
    except Exception:
        logger.exception("linq send failed")
    return False


def parse_inbound(payload: dict):
    """Return (sender_phone, text) or None (our own echo / non-text / malformed)."""
    d = payload.get("data") or payload.get("message") or payload
    if not isinstance(d, dict):
        return None
    sh = d.get("sender_handle")
    if isinstance(sh, dict) and sh.get("is_me") is True:
        return None
    text = ""
    parts = d.get("parts")
    if isinstance(parts, list):
        text = " ".join(str(p.get("value")) for p in parts
                        if isinstance(p, dict) and str(p.get("type", "")).lower() == "text" and p.get("value"))
    text = text or d.get("text") or d.get("content") or d.get("body") or ""
    sender = ""
    if isinstance(sh, dict):
        sender = sh.get("handle") or sh.get("phone") or ""
    sender = sender or d.get("from") or d.get("sender") or ""
    if not (sender and text.strip()):
        return None
    return str(sender), text.strip()
