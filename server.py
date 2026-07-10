"""FastAPI service: one deployment, many businesses. The shared phone line (LINQ_FROM) serves whichever
business is ACTIVE (businesses.active_slug()); each business also has its own always-on dashboard at
/<slug>/ reading its own isolated data. Run: uvicorn server:app --host 0.0.0.0 --port 8090
"""
import asyncio
import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse

import agent
import businesses
import linq
import store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rubi.server")

app = FastAPI(title="Rubi Receptionist")

_DASH = os.path.join(os.path.dirname(__file__), "dashboard", "index.html")


@app.get("/health")
async def health():
    return {"ok": True, "active": businesses.active_slug(),
            "businesses": list(businesses.BUSINESSES.keys())}


@app.post("/webhook")
async def webhook(request: Request):
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
    slug = businesses.active_slug()
    logger.info("inbound [%s] %s: %s", slug, phone, text[:120])

    async def _work():
        try:
            reply = await agent.handle(slug, phone, text)
            await linq.send(phone, reply)
        except Exception:
            logger.exception("handle failed for %s", phone)

    asyncio.create_task(_work())   # ack the webhook fast; process in the background
    return JSONResponse({"ok": True})


@app.get("/{slug}/api/data")
async def data(slug: str):
    if slug not in businesses.BUSINESSES:
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
    if slug not in businesses.BUSINESSES:
        return JSONResponse({"error": "unknown business"}, status_code=404)
    return FileResponse(_DASH)


@app.get("/{slug}")
async def dashboard_noslash(slug: str):
    if slug not in businesses.BUSINESSES:
        return JSONResponse({"error": "unknown business"}, status_code=404)
    return RedirectResponse(url=f"{slug}/")


@app.get("/")
async def index():
    return RedirectResponse(url=f"{businesses.active_slug()}/")
