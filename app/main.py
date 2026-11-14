import logging
from contextlib import asynccontextmanager
import json
from typing import Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from . import bot, config, maintenance, whatsapp
from .db import init_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    try:
        logging.getLogger("stick").info("Tidied %s saved names", maintenance.tidy_existing_names())
    except Exception:
        logging.getLogger("stick").exception("Name tidy-up skipped")
    logging.getLogger("stick").info("Webhook setup: %s", bot.ensure_webhook())
    yield


app = FastAPI(title="Stick", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/telegram/webhook")
def telegram_webhook(
    update: dict = Body(...),
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    if config.WEBHOOK_SECRET and x_telegram_bot_api_secret_token != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=403)
    bot.process_update(update)
    return {"ok": True}


@app.api_route("/tick", methods=["GET", "POST"])
def tick(key: Optional[str] = Query(default=None), x_tick_key: Optional[str] = Header(default=None)):
    """Hit this every ~10 minutes from an outside pinger. It keeps the free host awake and sends reminders."""
    if config.TICK_KEY and (key or x_tick_key) != config.TICK_KEY:
        raise HTTPException(status_code=403)
    return bot.send_due_reminders()


@app.get("/diag")
def diag(key: Optional[str] = Query(default=None), x_tick_key: Optional[str] = Header(default=None)):
    """Open /diag?key=<TICK_KEY> in a browser to see what is (not) configured."""
    if not config.TICK_KEY or (key or x_tick_key) != config.TICK_KEY:
        raise HTTPException(status_code=403)
    return bot.diagnostics()


@app.get("/whatsapp/webhook")
def whatsapp_verify(
    mode: Optional[str] = Query(default=None, alias="hub.mode"),
    challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
    token: Optional[str] = Query(default=None, alias="hub.verify_token"),
):
    """Meta calls this once when you save the webhook in the developer dashboard."""
    if mode == "subscribe" and config.WHATSAPP_VERIFY_TOKEN and token == config.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403)


@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    raw = await request.body()
    if config.WHATSAPP_APP_SECRET and not whatsapp.valid_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403)
    try:
        payload = json.loads(raw)
    except ValueError:
        return {"ok": True}
    await run_in_threadpool(bot.process_whatsapp, payload)
    return {"ok": True}


PRIVACY = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stick privacy policy</title>
<style>body{{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#1f2a44}}h1{{margin-bottom:0}}</style></head>
<body><h1>Stick privacy policy</h1><p><em>Stick is a study coach that works through Telegram and WhatsApp.</em></p>
<h2>What Stick stores</h2>
<p>Your Telegram or WhatsApp ID (your phone number on WhatsApp), your first or profile name, the courses and topics you type,
your study sessions (times, durations and how solid they felt), quiz questions and answers you add, your recall results,
and when you were last reminded.</p>
<h2>Why</h2>
<p>Only to log your study, schedule your reviews, send reminders and build the Excel file you ask for.
Your data is not sold and not used for advertising.</p>
<h2>Who can see it</h2>
<p>The person who runs Stick can access the database. Messages pass through Telegram and Meta (WhatsApp), which handle them under their own policies.
The service is hosted on Render.</p>
<h2>Deleting your data</h2>
<p>To have your data deleted, contact {contact}. Stopping the chat stops all reminders.</p>
</body></html>"""


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return PRIVACY.format(contact=config.PRIVACY_CONTACT)
