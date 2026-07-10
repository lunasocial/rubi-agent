"""The receptionist agent , Gemini with function calling, scoped to one business's config."""
import asyncio
import logging
import os

from google import genai
from google.genai import types

import config
import store

logger = logging.getLogger("rubi.agent")

_MODEL = os.getenv("RUBI_MODEL", "gemini-3-flash-preview")
_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
_client = None


def _c():
    global _client
    if _client is None:
        _client = genai.Client(api_key=_KEY)
    return _client


def _system() -> str:
    b = config.BUSINESS
    return f"""You are the receptionist for {b['name']}, a {b['cuisine']} restaurant in {b['neighborhood']}.
You speak as part of the team , say "we" and "our", never "they". You're texting a customer, so keep every
reply to 1-2 short sentences. Warm, natural, never robotic. No emojis.

WHAT WE ARE: {b['known_for']}.
ADDRESS: {b['address']}
RESERVATIONS: {b['reservation_policy']}
TAKEOUT/DELIVERY: {b['takeout']}

HOURS:
{config.hours_text()}

MENU (the ONLY items you may name , never invent a dish or a price we don't list):
{config.menu_text()}

HOW TO BEHAVE:
- Answer questions about hours, location, menu, and policy directly from the info above. If it's not
  listed (an exact price, whether a specific dish is available today), say you're not certain and offer
  to have the team confirm , never make it up.
- To BOOK a reservation, first collect the guest's name, party size, date, and time. Then read it back
  ("So that's [name], party of [n], on [date] at [time] , all good?") and only call make_reservation
  after they confirm. The reservation lands on our team's dashboard.
- To cancel, get their name and call cancel_reservation.
- For anything you can't handle, or a special request/complaint, call log_inquiry so the team sees it.
  If it genuinely needs a person right now, call escalate_to_owner.
- Never claim a table is held or confirmed beyond what the tool returned. Never promise something we
  don't offer. If unsure, it's always better to log it for the team than to guess."""


def _tools(phone: str):
    def make_reservation(name: str, party_size: int, date: str, time: str, notes: str = "") -> str:
        """Book a reservation after the guest has confirmed name, party size, date, and time."""
        rid = store.add_reservation(phone, name, party_size, date, time, notes)
        return f"Reservation {rid} saved to the team dashboard for {name}, party of {party_size}, {date} {time}."

    def cancel_reservation(name: str = "") -> str:
        """Cancel this guest's upcoming reservation."""
        rid = store.cancel_reservation(phone, name)
        return f"Cancelled reservation {rid}." if rid else "No active reservation found under that name."

    def log_inquiry(question: str, name: str = "") -> str:
        """Log a question, request, or feedback for the team to follow up on."""
        store.add_inquiry(phone, name, question)
        return "Logged for the team."

    def escalate_to_owner(reason: str) -> str:
        """Flag something that needs a human at the restaurant right now."""
        store.add_inquiry(phone, "", f"[ESCALATION] {reason}")
        return "Flagged for the team to handle."

    return [make_reservation, cancel_reservation, log_inquiry, escalate_to_owner]


async def handle(phone: str, text: str) -> str:
    store.log_message(phone, "user", text)
    history = store.get_history(phone, 12)
    contents = [
        types.Content(role=("user" if m.get("role") == "user" else "model"),
                      parts=[types.Part(text=m.get("text", ""))])
        for m in history if m.get("text")
    ]

    def _run() -> str:
        resp = _c().models.generate_content(
            model=_MODEL, contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=_system(), tools=_tools(phone), temperature=0.5))
        return (resp.text or "").strip()

    try:
        reply = await asyncio.to_thread(_run)
    except Exception:
        logger.exception("agent generate failed")
        reply = ""
    reply = reply or "Sorry, I didn't catch that , could you say it once more?"
    store.log_message(phone, "assistant", reply)
    return reply
