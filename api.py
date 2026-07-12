"""Authed JSON API for the business dashboard (web team's subdomain builds against this).

Auth: `Authorization: Bearer <token>` where token is either the static RUBI_API_TOKEN (service-level,
for the web backend) or a Firebase ID token (per-user, once dashboard logins exist). The public demo
dashboards at /<slug>/ stay unauthenticated and untouched.
"""
import asyncio
import hmac
import logging
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import businesses
import governance
import store

logger = logging.getLogger("rubi.api")

router = APIRouter(prefix="/api/v1")


def _ident(request: Request):
    """Caller identity: {'admin': True} for the service token (sees all tenants), {'uid', 'email'}
    for a Firebase-authenticated dashboard user (sees only tenants listing them in `owners`),
    None when unauthenticated."""
    auth = request.headers.get("authorization", "")
    token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return None
    static = os.getenv("RUBI_API_TOKEN", "")
    if static and hmac.compare_digest(token, static):
        return {"admin": True}
    try:
        import firebase_admin
        from firebase_admin import auth as fb_auth
        if firebase_admin._apps:
            claims = fb_auth.verify_id_token(token)
            return {"admin": False, "uid": claims.get("uid", ""),
                    "email": (claims.get("email") or "").lower()}
    except Exception:
        pass
    return None


def _authed(request: Request) -> bool:
    return _ident(request) is not None


def _can_access(ident, slug: str) -> bool:
    if not ident:
        return False
    if ident.get("admin"):
        return True
    owners = [str(o).lower() for o in businesses.get(slug).get("owners", [])]
    return bool(owners) and (ident.get("uid", "").lower() in owners
                             or (ident.get("email") and ident["email"] in owners))


def _deny():
    return JSONResponse({"error": "unauthorized"}, status_code=401)


@router.get("/tenants")
async def tenants(request: Request):
    ident = _ident(request)
    if not ident:
        return _deny()
    out = []
    for slug in businesses.all_slugs():
        if not _can_access(ident, slug):
            continue
        cfg = businesses.get(slug)
        out.append({"id": slug, "name": cfg.get("name"), "type": cfg.get("type", "restaurant"),
                    "neighborhood": cfg.get("neighborhood"), "line": cfg.get("line", "")})
    return {"tenants": out}


@router.get("/tenants/{slug}/data")
async def tenant_data(slug: str, request: Request):
    ident = _ident(request)
    if not ident:
        return _deny()
    if not businesses.known(slug):
        return JSONResponse({"error": "unknown tenant"}, status_code=404)
    if not _can_access(ident, slug):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    d = await asyncio.to_thread(store.dashboard_data, slug)
    cfg = businesses.get(slug)
    d["business"] = cfg.get("name")
    d["type"] = cfg.get("type", "restaurant")
    return d


@router.get("/tenants/{slug}/customers")
async def tenant_customers(slug: str, request: Request):
    ident = _ident(request)
    if not ident:
        return _deny()
    if not businesses.known(slug):
        return JSONResponse({"error": "unknown tenant"}, status_code=404)
    if not _can_access(ident, slug):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return {"customers": await asyncio.to_thread(store.customers_for, slug)}


@router.get("/tenants/{slug}/stats")
async def tenant_stats(slug: str, request: Request):
    ident = _ident(request)
    if not ident:
        return _deny()
    if not businesses.known(slug):
        return JSONResponse({"error": "unknown tenant"}, status_code=404)
    if not _can_access(ident, slug):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    d = await asyncio.to_thread(store.dashboard_data, slug)
    msgs = d.get("messages", [])
    inbound = [m for m in msgs if m.get("role") == "user"]
    active_res = [r for r in d.get("reservations", []) if r.get("status") != "cancelled"]
    customers = {m.get("customer_phone") for m in inbound}
    converted = customers & {r.get("customer_phone") for r in active_res}
    now = time.time()
    day = lambda ts: int((now - ts) // 86400) if ts else 999
    return {
        "conversations": len({m.get("customer_phone") for m in msgs}),
        "messages": len(msgs),
        "inbound": len(inbound),
        "bookings": len(active_res),
        "inquiries": len(d.get("inquiries", [])),
        # conversion: customers who texted AND ended up with a live booking
        "converted_customers": len(converted),
        "conversion_rate": round(len(converted) / len(customers), 3) if customers else None,
        "messages_7d": len([m for m in msgs if day(m.get("created_at")) < 7]),
        "customers_7d": len({m.get("customer_phone") for m in inbound
                             if day(m.get("created_at")) < 7}),
        "latency": await governance.latency_stats(slug),
        "today": await governance.day_counts(slug),
    }
