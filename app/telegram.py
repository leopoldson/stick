"""Thin Telegram Bot API client (plain HTTPS, no framework)."""
import logging

import httpx

from . import config

log = logging.getLogger("stick.telegram")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"


def _markup(buttons) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}


def send_reply(chat_id: int, reply) -> bool:
    with httpx.Client(timeout=20) as client:
        if reply.document:
            name, data = reply.document
            r = client.post(
                _api("sendDocument"),
                data={"chat_id": chat_id, "caption": reply.text},
                files={"document": (name, data, XLSX)},
            )
        else:
            payload = {"chat_id": chat_id, "text": reply.text}
            if reply.buttons:
                payload["reply_markup"] = _markup(reply.buttons)
            r = client.post(_api("sendMessage"), json=payload)
        if r.status_code != 200:
            log.warning("Telegram send failed (%s): %s", r.status_code, r.text[:200])
        return r.status_code == 200


def answer_callback(callback_id: str) -> None:
    try:
        httpx.post(_api("answerCallbackQuery"), json={"callback_query_id": callback_id}, timeout=10)
    except httpx.HTTPError:
        pass


def set_webhook(url: str, secret: str, drop_pending: bool = False) -> dict:
    r = httpx.post(
        _api("setWebhook"),
        json={
            "url": url,
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": drop_pending,
        },
        timeout=20,
    )
    return r.json()


def delete_webhook() -> dict:
    return httpx.post(_api("deleteWebhook"), json={"drop_pending_updates": False}, timeout=20).json()


def get_me() -> dict:
    return httpx.get(_api("getMe"), timeout=10).json()


def get_webhook_info() -> dict:
    return httpx.get(_api("getWebhookInfo"), timeout=10).json()
