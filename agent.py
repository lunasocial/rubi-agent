"""The receptionist agent , Gemini with function calling, scoped to whichever business the message
is for. The same code serves every business; the slug picks the config, prompt, and data namespace.
"""
import asyncio
import datetime
import logging
import os

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:
    _NY = None

from google import genai
from google.genai import types

import availability
import businesses
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


def _today_ny() -> str:
    now = datetime.datetime.now(_NY) if _NY else datetime.datetime.now()
    return now.strftime("%A, %B %d, %Y")


def _has_availability(cfg: dict) -> bool:
    return bool((cfg.get("resy") or {}).get("venue_id") or (cfg.get("opentable") or {}).get("rid"))


def _system(cfg: dict, today: str) -> str:
    avail = ""
    if _has_availability(cfg):
        avail = """
- Before telling a guest whether a specific date/time is open, call check_availability with the date
  (YYYY-MM-DD), party size, and time. It returns our real live online openings. Offer the times it
  returns. If it comes back with nothing, don't invent times , say we don't show online tables for
  that day/size and offer to note it for the team (log_inquiry)."""
    return f"""You are the receptionist for {cfg['name']}, a {cfg['cuisine']} restaurant in {cfg['neighborhood']}.
You speak as part of the team , say "we" and "our", never "they". You're texting a customer, so keep every
reply to 1-2 short sentences. Warm, natural, never robotic. No emojis.

Today is {today} (New York). Use this to turn "tonight", "tomorrow", "this Friday" into an exact date.

WHAT WE ARE: {cfg['known_for']}.
ADDRESS: {cfg['address']}
RESERVATIONS: {cfg['reservation_policy']}
TAKEOUT/DELIVERY: {cfg['takeout']}

HOURS:
{businesses.hours_text(cfg)}

MENU (the ONLY items you may name , never invent a dish or a price we don't list):
{businesses.menu_text(cfg)}

HOW TO BEHAVE:
- Answer questions about hours, location, menu, and policy directly from the info above. If it's not
  listed (an exact price, whether a specific dish is available today), say you're not certain and offer
  to have the team confirm , never make it up.{avail}
- To BOOK a reservation, first collect the guest's name, party size, date, and time. Then read it back
  ("So that's [name], party of [n], on [date] at [time] , all good?") and only call make_reservation
  after they confirm. The reservation lands on our team's dashboard.
- To cancel, get their name and call cancel_reservation.
- For anything you can't handle, or a special request/complaint, call log_inquiry so the team sees it.
  If it genuinely needs a person right now, call escalate_to_owner.
- Never claim a table is held or confirmed beyond what the tool returned. Never promise something we
  don't offer. If unsure, it's always better to log it for the team than to guess."""


def _to_hhmm(natural: str) -> str:
    mins = availability._target_minutes(natural)
    return f"{mins // 60:02d}:{mins % 60:02d}" if mins is not None else "19:00"


def _tools(slug: str, phone: str, cfg: dict):
    def make_reservation(name: str, party_size: int, date: str, time: str, notes: str = "") -> str:
        """Book a reservation after the guest has confirmed name, party size, date, and time."""
        rid = store.add_reservation(slug, phone, name, party_size, date, time, notes)
        return f"Reservation {rid} saved to the team dashboard for {name}, party of {party_size}, {date} {time}."

    def cancel_reservation(name: str = "") -> str:
        """Cancel this guest's upcoming reservation."""
        rid = store.cancel_reservation(slug, phone, name)
        return f"Cancelled reservation {rid}." if rid else "No active reservation found under that name."

    def log_inquiry(question: str, name: str = "") -> str:
        """Log a question, request, or feedback for the team to follow up on."""
        store.add_inquiry(slug, phone, name, question)
        return "Logged for the team."

    def escalate_to_owner(reason: str) -> str:
        """Flag something that needs a human at the restaurant right now."""
        store.add_inquiry(slug, phone, "", f"[ESCALATION] {reason}")
        return "Flagged for the team to handle."

    tools = [make_reservation, cancel_reservation, log_inquiry, escalate_to_owner]

    if _has_availability(cfg):
        def check_availability(date: str, party_size: int = 2, time: str = "") -> str:
            """Look up our real live online reservation openings for a date (YYYY-MM-DD), party size,
            and optional time (e.g. '7pm'). Returns actual bookable times , never guess availability."""
            try:
                res = asyncio.run(availability.check(cfg, date, int(party_size) or 2, _to_hhmm(time)))
            except Exception:
                logger.exception("availability check failed")
                res = None
            if not res:
                return "No live online availability returned , offer to log the request for the team."
            if not res["slots"]:
                return f"No online tables showing for {date}, party of {party_size}."
            return (f"Open on {date} for {party_size} ({res['platform']}): "
                    + ", ".join(res["slots"]))

        tools.append(check_availability)

    return tools


async def handle(slug: str, phone: str, text: str) -> str:
    cfg = businesses.get(slug)
    store.log_message(slug, phone, "user", text)
    history = store.get_history(slug, phone, 12)
    contents = [
        types.Content(role=("user" if m.get("role") == "user" else "model"),
                      parts=[types.Part(text=m.get("text", ""))])
        for m in history if m.get("text")
    ]

    today = _today_ny()

    def _run() -> str:
        resp = _c().models.generate_content(
            model=_MODEL, contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=_system(cfg, today), tools=_tools(slug, phone, cfg),
                temperature=0.5))
        return (resp.text or "").strip()

    try:
        reply = await asyncio.to_thread(_run)
    except Exception:
        logger.exception("agent generate failed")
        reply = ""
    reply = reply or "Sorry, I didn't catch that , could you say it once more?"
    store.log_message(slug, phone, "assistant", reply)
    return reply
