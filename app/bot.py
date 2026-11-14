"""Glue between Telegram updates and the handlers. Used by the web app and by local polling."""
import logging
import random
import time

from sqlalchemy import func, select

from datetime import timedelta

from . import clock, config, telegram, whatsapp
from .db import SessionLocal
from .handlers import Reply, handle
from .models import ProcessedMessage, Review, User

log = logging.getLogger("stick.bot")

NUDGES = [
    "{n} topic{s} about to slip from memory. Two minutes now saves a crash-read later.",
    "Quick one: {n} topic{s} due today. Your future self says thanks.",
    "Memory check time. {n} topic{s} to review today.",
    "{n} topic{s} waiting for you. Ready when you are.",
]


def process_update(update: dict) -> None:
    cb = update.get("callback_query")
    msg = update.get("message")
    if cb and cb.get("message"):
        chat, sender, text = cb["message"]["chat"], cb["from"], cb.get("data", "")
        telegram.answer_callback(cb["id"])
    elif msg and msg.get("text"):
        chat, sender, text = msg["chat"], msg["from"], msg["text"]
    else:
        return
    if chat.get("type") != "private":
        return  # Stick is a one-to-one study coach
    try:
        with SessionLocal() as db:
            replies = handle(db, sender["id"], sender.get("first_name", ""), text)
        for reply in replies:
            telegram.send_reply(chat["id"], reply)
            if reply.broadcast:
                sent, failed = broadcast(reply.broadcast, exclude_tg_id=sender["id"])
                note = f"Done. Delivered to {sent}" + (f", {failed} couldn't be reached (they may have blocked the bot)." if failed else ".")
                telegram.send_reply(chat["id"], Reply(note))
    except Exception:
        # Always answer Telegram with 200, or it re-sends the same update and the command runs twice.
        log.exception("Failed to handle update")
        try:
            telegram.send_reply(chat["id"], Reply("Something went wrong on my side. Try again in a moment."))
        except Exception:
            log.exception("Could not send the error message either")


def broadcast(text: str, exclude_tg_id=None) -> tuple[int, int]:
    """Send one message to every Telegram user. Returns (delivered, failed). Slightly paced to stay under Telegram's limits."""
    with SessionLocal() as db:
        ids = [u.tg_id for u in db.scalars(select(User).where(User.tg_id.is_not(None))).all() if u.tg_id != exclude_tg_id]
    delivered = failed = 0
    for tg_id in ids:
        try:
            ok = telegram.send_reply(tg_id, Reply(text))
        except Exception:
            ok = False
        delivered, failed = (delivered + 1, failed) if ok is not False else (delivered, failed + 1)
        time.sleep(0.05)
    return delivered, failed


def _remind(user: User, count: int) -> None:
    text = random.choice(NUDGES).format(n=count, s="" if count == 1 else "s")
    reply = Reply(text, buttons=[[("Start quiz", "cb:quiz")]])
    if user.tg_id:
        telegram.send_reply(user.tg_id, reply)
    elif user.wa_id and whatsapp.enabled():
        if whatsapp.window_open(user.last_inbound_at):
            whatsapp.send_reply(user.wa_id, reply)  # they messaged recently: a normal message is allowed
        else:
            whatsapp.send_reminder_template(user.wa_id, user.name, count)  # otherwise WhatsApp requires a template


def send_due_reminders() -> dict:
    """Called by /tick. One nudge per user per day, between 08:00 and 21:00 Lagos time."""
    now = clock.now_local()
    if now.hour < 8 or now.hour >= 21:
        return {"sent": 0, "note": "quiet hours"}
    today, sent = now.date(), 0
    with SessionLocal() as db:
        db.query(ProcessedMessage).filter(ProcessedMessage.at < clock.now_utc() - timedelta(days=2)).delete()
        users = db.scalars(
            select(User).where((User.last_reminded_on.is_(None)) | (User.last_reminded_on != today))
        ).all()
        for user in users:
            n = db.scalar(select(func.count(Review.id)).where(Review.user_id == user.id, Review.due_on <= today))
            if not n:
                continue
            try:
                _remind(user, n)
                sent += 1
            except Exception:
                log.exception("Reminder failed for user %s", user.id)
            user.last_reminded_on = today  # even on failure, so a blocked user isn't retried all day
        db.commit()
    return {"sent": sent}


def process_whatsapp(payload: dict) -> None:
    """Handle one webhook delivery from Meta. Never raises: Meta must always get a 200."""
    for msg in whatsapp.parse_webhook(payload):
        if not msg["from"] or not msg["id"]:
            continue
        try:
            with SessionLocal() as db:
                if db.get(ProcessedMessage, msg["id"]):
                    continue  # Meta delivered this one twice
                db.add(ProcessedMessage(id=msg["id"]))
                db.commit()
                if msg["text"] is None:
                    replies = [Reply("I can only read text messages for now. Send help to see what I can do.")]
                else:
                    replies = handle(db, ("wa", msg["from"]), msg["name"], msg["text"])
            for reply in replies:
                whatsapp.send_reply(msg["from"], reply)
        except Exception:
            log.exception("Failed to handle WhatsApp message")
            try:
                whatsapp.send_reply(msg["from"], Reply("Something went wrong on my side. Try again in a moment."))
            except Exception:
                log.exception("Could not send the error message either")


def ensure_webhook() -> str:
    """Point Telegram at this server. Safe to run on every start-up; queued messages are kept."""
    if not (config.BOT_TOKEN and config.PUBLIC_URL and config.WEBHOOK_SECRET):
        return "skipped: set TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET and (off Render) PUBLIC_URL"
    try:
        result = telegram.set_webhook(f"{config.PUBLIC_URL}/telegram/webhook", config.WEBHOOK_SECRET)
        return "ok" if result.get("ok") else f"Telegram refused: {result.get('description')}"
    except Exception as exc:  # never stop the server from starting
        return f"failed: {type(exc).__name__}"


def diagnostics() -> dict:
    """Safe-to-read status: no tokens, secrets or passwords appear here."""
    info: dict = {
        "token_set": bool(config.BOT_TOKEN),
        "token_length": len(config.BOT_TOKEN),
        "token_had_stray_characters": config.BOT_TOKEN_RAW != config.BOT_TOKEN,
        "webhook_secret_set": bool(config.WEBHOOK_SECRET),
        "tick_key_set": bool(config.TICK_KEY),
        "public_url": config.PUBLIC_URL or None,
        "database_is_postgres": config.DATABASE_URL.startswith("postgresql"),
    }
    try:
        me = telegram.get_me()
        info["bot"] = ("@" + me["result"]["username"]) if me.get("ok") else f"token rejected: {me.get('description')}"
    except Exception as exc:
        info["bot"] = f"could not reach Telegram: {type(exc).__name__}"
    try:
        wh = telegram.get_webhook_info().get("result", {})
        info["webhook"] = {
            "url": wh.get("url"),
            "expected_url": f"{config.PUBLIC_URL}/telegram/webhook" if config.PUBLIC_URL else None,
            "pending_update_count": wh.get("pending_update_count"),
            "last_error_message": wh.get("last_error_message"),
            "last_error_date": wh.get("last_error_date"),
        }
    except Exception as exc:
        info["webhook"] = f"could not reach Telegram: {type(exc).__name__}"
    info["whatsapp"] = {
        "configured": whatsapp.enabled(),
        "verify_token_set": bool(config.WHATSAPP_VERIFY_TOKEN),
        "app_secret_set": bool(config.WHATSAPP_APP_SECRET),
        "api_version": config.WHATSAPP_API_VERSION,
        "reminder_template": config.WHATSAPP_REMINDER_TEMPLATE,
    }
    if whatsapp.enabled():
        try:
            status = whatsapp.phone_status()
            info["whatsapp"]["number"] = status.get("display_phone_number")
            info["whatsapp"]["status"] = status.get("status") or (status.get("error") or {}).get("message", "no status")
            info["whatsapp"]["name_status"] = status.get("name_status")
        except Exception as exc:
            info["whatsapp"]["status"] = f"could not reach Meta: {type(exc).__name__}"
    try:
        with SessionLocal() as db:
            info["users"] = db.scalar(select(func.count(User.id)))
        info["database"] = "ok"
    except Exception as exc:
        info["database"] = f"error: {type(exc).__name__}"
    return info
