"""Per-tenant governance: consent (TCPA STOP/HELP/START), pause switch, daily caps, event counters.

Ported thin from Luna's consent/killswitch/obs. Consent is DURABLE (rubi_customers.consent in
Firestore), never LLM-decided; caps/counters live in the shared cache. Everything fail-safe: a
governance error must never take down the reply path.
"""
from __future__ import annotations

import logging
import os
import time

import businesses
import context
import store

logger = logging.getLogger("rubi.governance")

_STOP = ("stop", "unsubscribe", "stop all", "end", "quit", "cancel")
_STOP_PHRASES = ("stop texting", "stop messaging", "opt out", "opt me out", "don't text",
                 "dont text", "remove me", "take me off")
_START = ("start", "unstop", "resume", "subscribe")
_HELP = ("help", "info")


def _norm(text: str) -> str:
    return (text or "").strip().lower().rstrip(".!")


def stop_intent(text: str) -> bool:
    t = _norm(text)
    return t in _STOP or any(p in t for p in _STOP_PHRASES)


def start_intent(text: str) -> bool:
    return _norm(text) in _START


def help_intent(text: str) -> bool:
    return _norm(text) in _HELP


def _set_consent(tenant: str, phone: str, value: str) -> None:
    did = f"{tenant}__{phone}".replace("/", "_")
    doc = {"tenant": tenant, "phone": phone, "consent": value, "consent_at": time.time()}
    try:
        db = store.db()
        if db is not None:
            db.collection("rubi_customers").document(did).set(doc, merge=True)
        else:
            store.mem_doc("rubi_customers", did).update(doc)
    except Exception:
        logger.exception("set_consent failed")


def _get_consent(tenant: str, phone: str) -> str:
    did = f"{tenant}__{phone}".replace("/", "_")
    try:
        db = store.db()
        if db is not None:
            d = db.collection("rubi_customers").document(did).get()
            return (d.to_dict() or {}).get("consent", "ok") if d.exists else "ok"
        return store.mem_doc("rubi_customers", did).get("consent", "ok")
    except Exception:
        return "ok"


def paused() -> bool:
    return os.getenv("RUBI_PAUSED", "0") == "1"


async def over_cap(tenant: str) -> bool:
    """Per-tenant daily message cap , a runaway-cost backstop, not a product limit."""
    cap = int(os.getenv("RUBI_TENANT_DAILY_CAP", "500"))
    if cap <= 0:
        return False
    day = time.strftime("%Y-%m-%d")
    n = await context._cache_incr(f"quota:{tenant}:{day}", ttl=2 * 86400)
    return n > cap


async def count(tenant: str, kind: str) -> None:
    try:
        day = time.strftime("%Y-%m-%d")
        await context._cache_incr(f"obs:{tenant}:{day}:{kind}", ttl=30 * 86400)
    except Exception:
        pass


async def day_counts(tenant: str, kinds=("inbound", "reply", "block", "missed_call")) -> dict:
    day = time.strftime("%Y-%m-%d")
    out = {}
    for k in kinds:
        try:
            out[k] = int(await context._cache_get(f"obs:{tenant}:{day}:{k}") or 0)
        except Exception:
            out[k] = 0
    return out


async def gate(tenant: str, phone: str, text: str):
    """Pre-agent gate. Returns (proceed: bool, direct_reply: str). Deterministic, never LLM.
    proceed=False + reply='' means drop silently (suppressed or paused/capped)."""
    try:
        name = businesses.get(tenant).get("name", "the business")
        if stop_intent(text):
            _set_consent(tenant, phone, "stopped")
            await count(tenant, "block")
            return False, (f"You've been opted out of texts from {name}. "
                           "Reply START anytime to opt back in.")
        consent = _get_consent(tenant, phone)
        if consent == "stopped":
            if start_intent(text):
                _set_consent(tenant, phone, "ok")
                return False, f"You're opted back in with {name}. How can we help?"
            await count(tenant, "block")
            return False, ""   # opted out: stay silent
        if help_intent(text):
            return False, (f"This is the {name} assistant , ask about hours, menu, or bookings. "
                           "Reply STOP to opt out anytime.")
        if paused() or await over_cap(tenant):
            await count(tenant, "block")
            logger.warning("gate blocked [%s]: paused=%s", tenant, paused())
            return False, ""
        return True, ""
    except Exception:
        logger.exception("gate failed , allowing through")
        return True, ""


def can_text(tenant: str, phone: str) -> bool:
    """May we INITIATE an outbound text (missed-call text-back etc.)? Consent + pause checked."""
    return not paused() and _get_consent(tenant, phone) != "stopped"
