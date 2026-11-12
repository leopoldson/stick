"""Glue between Telegram updates and the handlers. Used by the web app and by local polling."""
import logging
import random

from sqlalchemy import func, select

from . import clock, telegram
from .db import SessionLocal
from .handlers import Reply, handle
from .models import Review, User

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
    except Exception:
        # Always answer Telegram with 200, or it re-sends the same update and the command runs twice.
        log.exception("Failed to handle update")
        try:
            telegram.send_reply(chat["id"], Reply("Something went wrong on my side. Try again in a moment."))
        except Exception:
            log.exception("Could not send the error message either")


def send_due_reminders() -> dict:
    """Called by /tick. One nudge per user per day, between 08:00 and 21:00 Lagos time."""
    now = clock.now_local()
    if now.hour < 8 or now.hour >= 21:
        return {"sent": 0, "note": "quiet hours"}
    today, sent = now.date(), 0
    with SessionLocal() as db:
        users = db.scalars(
            select(User).where((User.last_reminded_on.is_(None)) | (User.last_reminded_on != today))
        ).all()
        for user in users:
            n = db.scalar(select(func.count(Review.id)).where(Review.user_id == user.id, Review.due_on <= today))
            if not n:
                continue
            text = random.choice(NUDGES).format(n=n, s="" if n == 1 else "s")
            try:
                telegram.send_reply(user.tg_id, Reply(text, buttons=[[("Start quiz", "cb:quiz")]]))
                sent += 1
            except Exception:
                log.exception("Reminder failed for user %s", user.id)
            user.last_reminded_on = today  # even on failure, so a blocked user isn't retried all day
        db.commit()
    return {"sent": sent}
