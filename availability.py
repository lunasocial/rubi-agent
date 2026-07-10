"""Live reservation availability, self-contained for rubi. Read-only: returns open slots + a booking
link, never books. Ported from the Luna availability checker.

Resy: direct /4/find. Resy's Incapsula edge 500s datacenter IPs, so route through a residential proxy
(RUBI_AVAIL_PROXY / AVAIL_PROXY_URL / RESY_PROXY) when set. OpenTable: Akamai-walled, needs Scrapfly
(SCRAPFLY_API_KEY) , activates only when the key is present, otherwise returns unknown gracefully.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

logger = logging.getLogger("rubi.availability")

_RESY_KEY = os.getenv("RESY_API_KEY", "VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_PROXY = (os.getenv("RUBI_AVAIL_PROXY") or os.getenv("AVAIL_PROXY_URL")
          or os.getenv("RESY_PROXY") or "")
_SCRAPFLY = os.getenv("SCRAPFLY_API_KEY", "")


# ---- time helpers ----------------------------------------------------------
def _fmt_time(start: str) -> str:
    """'2026-07-17 19:30:00' -> '7:30 PM'."""
    m = re.search(r"\b(\d{1,2}):(\d{2})", start or "")
    if not m:
        return ""
    h, mi = int(m.group(1)), m.group(2)
    ap = "AM" if h < 12 else "PM"
    return f"{h % 12 or 12}:{mi} {ap}"


def _slot_key(t: str) -> int:
    m = re.match(r"(\d{1,2}):(\d{2}) (AM|PM)", t)
    if not m:
        return 9999
    h = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
    return h * 60 + int(m.group(2))


def _target_minutes(prefer_time: str):
    """'7pm'/'8:00 PM'/'dinner' -> minutes from midnight, or None."""
    s = (prefer_time or "").lower()
    if not s:
        return None
    if "dinner" in s or "tonight" in s:
        return 19 * 60
    if "lunch" in s:
        return 12 * 60 + 30
    if "brunch" in s or "breakfast" in s:
        return 10 * 60
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and h <= 11:      # bare "8" for a meal -> assume evening
        h += 12
    return h * 60 + mi


def _near(slots: list[str], prefer_time: str) -> list[str]:
    """Surface slots closest to the requested time (within ~2.5h), so a 7pm ask shows ~7pm tables."""
    target = _target_minutes(prefer_time)
    if target is None:
        return slots[:10]
    near = [t for t in slots if abs(_slot_key(t) - target) <= 150]
    if not near:
        return slots[:10]
    near.sort(key=lambda t: abs(_slot_key(t) - target))
    return sorted(near[:10], key=_slot_key)


# ---- Resy ------------------------------------------------------------------
async def _resy_slots(venue_id: str, day: str, party_size: int) -> list[str] | None:
    H = {"Authorization": f'ResyAPI api_key="{_RESY_KEY}"', "Origin": "https://resy.com",
         "Referer": "https://resy.com/", "X-Origin": "https://resy.com", "User-Agent": _UA,
         "Accept": "application/json, text/plain, */*"}
    url = (f"https://api.resy.com/4/find?lat=40.74&long=-73.99&day={day}"
           f"&party_size={int(party_size) or 2}&venue_id={venue_id}")

    def _curl():
        from curl_cffi import requests as cr
        kw = {"headers": H, "impersonate": "chrome", "timeout": 12}
        if _PROXY:
            kw["proxies"] = {"https": _PROXY, "http": _PROXY}
        r = cr.request("GET", url, **kw)
        return r.status_code, r.text

    try:
        code, text = await asyncio.to_thread(_curl)
    except ImportError:                     # no curl_cffi -> plain httpx (works only with a proxy/IP)
        import httpx
        proxy = _PROXY or None
        async with httpx.AsyncClient(timeout=12.0, proxy=proxy) as c:
            r = await c.get(url, headers=H)
            code, text = r.status_code, r.text
    except Exception:
        logger.warning("resy find failed", exc_info=True)
        return None
    if code != 200:
        logger.info("resy find non-200 status=%s (proxy=%s)", code, bool(_PROXY))
        return None
    try:
        venues = (json.loads(text).get("results", {}) or {}).get("venues") or []
    except Exception:
        return None
    slots = []
    for s in (venues[0].get("slots", []) if venues else []) or []:
        t = _fmt_time((s.get("date", {}) or {}).get("start", ""))
        if t:
            slots.append(t)
    return sorted(set(slots), key=_slot_key)


# ---- OpenTable (Scrapfly, Akamai-walled) -----------------------------------
_OT_HASH = "cbcf4838a9b399f742e3741785df64560a826d8d3cc2828aa01ab09a8455e29e"
_OT_GQL = "https://www.opentable.com/dapi/fe/gql?optype=query&opname=RestaurantsAvailability"
_OT_CSRF = "00000000-0000-4000-8000-000000000000"


def _to_hhmm(total: int) -> str:
    total %= 1440
    return f"{total // 60:02d}:{total % 60:02d}"


async def _opentable_slots(rid: str, day: str, party_size: int, near_time: str) -> list[str] | None:
    if not _SCRAPFLY or not str(rid).isdigit():
        return None
    body = json.dumps({
        "operationName": "RestaurantsAvailability",
        "variables": {"onlyPop": False, "forwardDays": 0, "requireTimes": False,
                      "requireTypes": ["Standard", "Experience", "PrivateDining"], "useCBR": True,
                      "privilegedAccess": [], "restaurantIds": [int(rid)], "date": day,
                      "time": near_time, "partySize": party_size, "databaseRegion": "NA",
                      "restaurantAvailabilityTokens": [], "loyaltyRedemptionTiers": [],
                      "forwardMinutes": 210, "backwardMinutes": 210},
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": _OT_HASH}}})
    params = {"key": _SCRAPFLY, "url": _OT_GQL, "asp": "true", "country": "us", "method": "POST",
              "headers[content-type]": "application/json", "headers[x-csrf-token]": _OT_CSRF,
              "headers[ot-page-group]": "rest-profile"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=45.0) as c:
            resp = await c.post("https://api.scrapfly.io/scrape", params=params, content=body)
        result = resp.json().get("result") or {}
        if result.get("status_code") != 200:
            return None
        data = json.loads(result.get("content") or "{}")
        avail = (((data.get("data") or {}).get("availability") or [{}])[0]) or {}
        base = int(near_time.split(":")[0]) * 60 + int(near_time.split(":")[1])
        slots = []
        for d in avail.get("availabilityDays") or []:
            for s in d.get("slots", []):
                if s.get("isAvailable"):
                    slots.append(_fmt_time("x " + _to_hhmm(base + int(s.get("timeOffsetMinutes", 0))) + ":00"))
        return sorted(set(t for t in slots if t), key=_slot_key)
    except Exception:
        logger.warning("opentable find failed", exc_info=True)
        return None


# ---- public ----------------------------------------------------------------
async def check(cfg: dict, day: str, party_size: int, near_time: str = "19:00") -> dict | None:
    """Live availability for a business config, or None if it has no supported reservation platform
    or the lookup couldn't complete. near_time is 'HH:MM' (24h) for OpenTable's halo + Resy sorting."""
    resy = cfg.get("resy")
    ot = cfg.get("opentable")
    slots = None
    platform = booking_url = None
    if resy and resy.get("venue_id"):
        slots = await _resy_slots(resy["venue_id"], day, int(party_size) or 2)
        platform = "Resy"
        booking_url = (f"https://resy.com/cities/{resy.get('city','ny')}/{resy.get('url_slug','')}"
                       f"?date={day}&seats={int(party_size) or 2}") if resy.get("url_slug") else None
    if slots is None and ot and ot.get("rid"):
        slots = await _opentable_slots(ot["rid"], day, int(party_size) or 2, near_time)
        platform = "OpenTable"
        booking_url = ot.get("url")
    if slots is None:
        return None
    return {"platform": platform, "slots": _near(slots, near_time), "all_slots": slots,
            "booking_url": booking_url, "day": day, "party_size": int(party_size) or 2,
            "status": "available" if slots else "sold_out"}
