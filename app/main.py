import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query

from . import bot, config
from .db import init_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
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
