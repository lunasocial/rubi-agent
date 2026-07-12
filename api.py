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
import store

logger = logging.getLogger("rubi.api")

router = APIRouter(prefix="/api/v1")


def _authed(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return False
    static = os.getenv("RUBI_API_TOKEN", "")
    if static and hmac.compare_digest(token, static):
        return True
    try:
        import firebase_admin
        from firebase_admin import auth as fb_auth
        if firebase_admin._apps:
            fb_auth.verify_id_token(token)
            return True
    except Exception:
        pass
    return False


def _deny():
    return JSONResponse({"error": "unauthorized"}, status_code=401)


@router.get("/tenants")
async def tenants(request: Request):
    if not _authed(request):
        return _deny()
    out = []
    for slug in businesses.all_slugs():
        cfg = businesses.get(slug)
        out.append({"id": slug, "name": cfg.get("name"), "type": cfg.get("type", "restaurant"),
                    "neighborhood": cfg.get("neighborhood"), "line": cfg.get("line", "")})
    return {"tenants": out}


@router.get("/tenants/{slug}/data")
async def tenant_data(slug: str, request: Request):
    if not _authed(request):
        return _deny()
    if not businesses.known(slug):
        return JSONResponse({"error": "unknown tenant"}, status_code=404)
    d = await asyncio.to_thread(store.dashboard_data, slug)
    cfg = businesses.get(slug)
    d["business"] = cfg.get("name")
    d["type"] = cfg.get("type", "restaurant")
    return d


@router.get("/tenants/{slug}/customers")
async def tenant_customers(slug: str, request: Request):
    if not _authed(request):
        return _deny()
    if not businesses.known(slug):
        return JSONResponse({"error": "unknown tenant"}, status_code=404)
    return {"customers": await asyncio.to_thread(store.customers_for, slug)}


@router.get("/tenants/{slug}/stats")
async def tenant_stats(slug: str, request: Request):
    if not _authed(request):
        return _deny()
    if not businesses.known(slug):
        return JSONResponse({"error": "unknown tenant"}, status_code=404)
    d = await asyncio.to_thread(store.dashboard_data, slug)
    msgs = d.get("messages", [])
    inbound = [m for m in msgs if m.get("role") == "user"]
    now = time.time()
    day = lambda ts: int((now - ts) // 86400) if ts else 999
    return {
        "conversations": len({m.get("customer_phone") for m in msgs}),
        "messages": len(msgs),
        "inbound": len(inbound),
        "bookings": len([r for r in d.get("reservations", []) if r.get("status") != "cancelled"]),
        "inquiries": len(d.get("inquiries", [])),
        "messages_7d": len([m for m in msgs if day(m.get("created_at")) < 7]),
        "customers_7d": len({m.get("customer_phone") for m in inbound
                             if day(m.get("created_at")) < 7}),
    }
