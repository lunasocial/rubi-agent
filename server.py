"""FastAPI service: one deployment, many businesses. The shared phone line (LINQ_FROM) serves whichever
business is ACTIVE (businesses.active_slug()); each business also has its own always-on dashboard at
/<slug>/ reading its own isolated data. Run: uvicorn server:app --host 0.0.0.0 --port 8090
"""
import asyncio
import json
import logging
import os
import time

try:                                     # load .env before importing modules that read env at import
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse

import agent
import api
import businesses
import governance
import linq
import store
import voice

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rubi.server")

app = FastAPI(title="Rubi Receptionist")
app.include_router(api.router)

# Browser calls from the web dashboard's subdomain (unset = same-origin/proxy only).
_cors = [o.strip() for o in os.getenv("RUBI_CORS_ORIGINS", "").split(",") if o.strip()]
if _cors:
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=_cors, allow_methods=["*"],
                       allow_headers=["*"])

_DASH = os.path.join(os.path.dirname(__file__), "dashboard", "index.html")


@app.get("/health")
async def health():
    return {"ok": True, "active": businesses.active_slug(), "businesses": businesses.all_slugs()}


async def _process(slug: str, phone: str, text: str) -> None:
    try:
        await governance.count(slug, "inbound")
        proceed, direct = await governance.gate(slug, phone, text)
        if not proceed:
            if direct:
                await linq.send(phone, direct)
            return
        t0 = time.time()
        reply = await agent.handle(slug, phone, text)
        await governance.record_latency(slug, int((time.time() - t0) * 1000))
        await linq.send(phone, reply)
        await governance.count(slug, "reply")
    except Exception:
        logger.exception("handle failed for %s", phone)


async def _inbound(request: Request, slug: str):
    raw = await request.body()
    if not linq.verify(raw, request.headers):
        return JSONResponse({"ok": False}, status_code=401)
    try:
        payload = json.loads(raw or b"{}")
    except Exception:
        return JSONResponse({"ok": True})
    parsed = linq.parse_inbound(payload)
    if not parsed:
        return JSONResponse({"ok": True})
    phone, text = parsed
    logger.info("inbound [%s] %s: %s", slug, phone, text[:120])
    asyncio.create_task(_process(slug, phone, text))   # ack fast; process in the background
    return JSONResponse({"ok": True})


@app.post("/webhook")
async def webhook(request: Request):
    """Shared demo line: serves whichever tenant is active (switch.py)."""
    return await _inbound(request, businesses.active_slug())


@app.post("/webhook/{slug}")
async def webhook_tenant(slug: str, request: Request):
    """Dedicated per-tenant line: that tenant's Linq number posts here , no switching."""
    if not businesses.known(slug):
        return JSONResponse({"ok": False, "error": "unknown tenant"}, status_code=404)
    return await _inbound(request, slug)


_PUBLIC_BASE = os.getenv("RUBI_PUBLIC_BASE", "https://agent.contextualintelligence.co/rubi")


@app.post("/voice/incoming")
async def voice_incoming(request: Request):
    """Twilio hits this when a forwarded call arrives; we ring the tenant's real phone."""
    raw = await request.body()
    if not voice.verify(raw, str(request.url), request.headers):
        return JSONResponse({"ok": False}, status_code=401)
    form = voice.parse_form(raw)
    slug = voice.tenant_for_call(form)
    logger.info("voice incoming [%s] from %s", slug, form.get("From", "?"))
    return Response(content=voice.incoming_twiml(slug, _PUBLIC_BASE), media_type="text/xml")


@app.post("/voice/after")
async def voice_after(request: Request, tenant: str = ""):
    """Twilio posts the dial outcome here; a miss becomes a text-back from the tenant's line."""
    raw = await request.body()
    if not voice.verify(raw, str(request.url), request.headers):
        return JSONResponse({"ok": False}, status_code=401)
    form = voice.parse_form(raw)
    slug = tenant if tenant and businesses.known(tenant) else voice.tenant_for_call(form)
    return Response(content=await voice.after_dial(slug, form), media_type="text/xml")


@app.post("/event/missed_call")
async def missed_call(request: Request):
    """Telephony hook (future): a call to a tenant's line wasn't answered -> text the caller back.
    Body: {"tenant": slug, "caller": "+1..."}. Flag-gated; honors consent + pause."""
    if not api._authed(request):
        return JSONResponse({"ok": False}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    slug, caller = (body.get("tenant") or "").strip(), (body.get("caller") or "").strip()
    if not (slug and caller and businesses.known(slug)):
        return JSONResponse({"ok": False, "error": "bad tenant/caller"}, status_code=400)
    if os.getenv("RUBI_MISSED_CALL_ENABLED", "0") != "1":
        return JSONResponse({"ok": True, "sent": False, "reason": "disabled"})
    if not governance.can_text(slug, caller):
        return JSONResponse({"ok": True, "sent": False, "reason": "suppressed"})
    name = businesses.get(slug).get("name", "us")
    msg = (f"Hi, sorry we missed your call at {name}. I'm the assistant , I can help with hours, "
           "menu, or a booking right here. Reply STOP to opt out.")
    sent = await linq.send(caller, msg)
    if sent:
        store.log_message(slug, caller, "assistant", msg)
        await governance.count(slug, "missed_call")
    return JSONResponse({"ok": True, "sent": bool(sent)})


@app.get("/{slug}/api/data")
async def data(slug: str):
    if not businesses.known(slug):
        return JSONResponse({"error": "unknown business"}, status_code=404)
    try:
        d = await asyncio.to_thread(store.dashboard_data, slug)
    except Exception:
        logger.exception("dashboard data failed")
        d = {"reservations": [], "inquiries": [], "messages": []}
    cfg = businesses.get(slug)
    d["business"] = cfg["name"]
    d["active"] = (slug == businesses.active_slug())
    return JSONResponse(d)


@app.get("/{slug}/")
async def dashboard(slug: str):
    if not businesses.known(slug):
        return JSONResponse({"error": "unknown business"}, status_code=404)
    return FileResponse(_DASH)


@app.get("/{slug}")
async def dashboard_noslash(slug: str):
    if not businesses.known(slug):
        return JSONResponse({"error": "unknown business"}, status_code=404)
    return RedirectResponse(url=f"{slug}/")


@app.get("/")
async def index():
    return RedirectResponse(url=f"{businesses.active_slug()}/")
