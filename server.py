"""FastAPI service: receives Linq inbound for the business line, runs the agent, replies, and serves
the owner dashboard data. Run: uvicorn server:app --host 0.0.0.0 --port 8090
"""
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import agent
import linq
import store
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rubi.server")

app = FastAPI(title="Rubirosa Receptionist")


@app.get("/health")
async def health():
    return {"ok": True, "business": config.BUSINESS["name"]}


@app.post("/webhook")
async def webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    parsed = linq.parse_inbound(payload)
    if not parsed:
        return JSONResponse({"ok": True})
    phone, text = parsed
    logger.info("inbound %s: %s", phone, text[:120])

    async def _work():
        try:
            reply = await agent.handle(phone, text)
            await linq.send(phone, reply)
        except Exception:
            logger.exception("handle failed for %s", phone)

    asyncio.create_task(_work())   # ack the webhook fast; process in the background
    return JSONResponse({"ok": True})


@app.get("/api/data")
async def data():
    try:
        d = await asyncio.to_thread(store.dashboard_data)
    except Exception:
        logger.exception("dashboard data failed")
        d = {"reservations": [], "inquiries": [], "messages": []}
    d["business"] = config.BUSINESS["name"]
    return JSONResponse(d)


@app.get("/")
async def index():
    return FileResponse("dashboard/index.html")
