"""WhatsApp Cloud API client: parse incoming webhooks, send replies, send template reminders."""
import hashlib
import hmac
import logging
from datetime import timedelta
from typing import Optional

import httpx

from . import clock, config

log = logging.getLogger("stick.whatsapp")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WINDOW = timedelta(hours=23)  # free-form replies are allowed for 24h after the person's last message
MAX_BUTTONS, MAX_ROWS, BODY_LIMIT = 3, 10, 1024


def enabled() -> bool:
    return bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_ID)


def _base() -> str:
    return f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}


def valid_signature(raw_body: bytes, header: Optional[str]) -> bool:
    """Meta signs every webhook with the app secret: 'sha256=<hmac of the raw body>'."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(config.WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def parse_webhook(payload: dict) -> list[dict]:
    """Flatten Meta's nested payload into [{'id', 'from', 'name', 'text'}]; 'text' is None for unsupported types."""
    found = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                kind, text = m.get("type"), None
                if kind == "text":
                    text = (m.get("text") or {}).get("body")
                elif kind == "interactive":
                    inter = m.get("interactive") or {}
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    text = reply.get("id")  # our callback data, e.g. "cb:rate:3"
                elif kind == "button":  # a quick-reply button on a template message
                    text = (m.get("button") or {}).get("text")
                found.append({"id": m.get("id", ""), "from": m.get("from", ""), "name": names.get(m.get("from"), ""), "text": text})
    return found


def window_open(last_inbound_at) -> bool:
    return bool(last_inbound_at) and clock.now_utc() - last_inbound_at <= WINDOW


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def message_payload(to: str, reply) -> dict:
    """Plain text, up to 3 buttons, or (for more choices) a tap-to-open list."""
    base = {"messaging_product": "whatsapp", "to": to}
    options = [(t, d) for row in (reply.buttons or []) for t, d in row]
    if not options or len(reply.text) > BODY_LIMIT:
        return {**base, "type": "text", "text": {"body": reply.text[:4096]}}
    if len(options) <= MAX_BUTTONS:
        buttons = [{"type": "reply", "reply": {"id": d, "title": _clip(t, 20)}} for t, d in options]
        return {**base, "type": "interactive",
                "interactive": {"type": "button", "body": {"text": reply.text}, "action": {"buttons": buttons}}}
    rows = [{"id": d, "title": _clip(t, 24)} for t, d in options[:MAX_ROWS]]
    return {**base, "type": "interactive",
            "interactive": {"type": "list", "body": {"text": reply.text},
                            "action": {"button": "Choose", "sections": [{"title": "Options", "rows": rows}]}}}


def _post(path: str, **kwargs) -> httpx.Response:
    r = httpx.post(f"{_base()}/{config.WHATSAPP_PHONE_ID}/{path}", headers=_headers(), timeout=25, **kwargs)
    if r.status_code >= 300:
        log.warning("WhatsApp %s failed (%s): %s", path, r.status_code, r.text[:300])
    return r


def send_reply(to: str, reply) -> None:
    if reply.document:
        name, data = reply.document
        up = _post("media", data={"messaging_product": "whatsapp", "type": XLSX}, files={"file": (name, data, XLSX)})
        media_id = up.json().get("id") if up.status_code < 300 else None
        if not media_id:
            _post("messages", json={"messaging_product": "whatsapp", "to": to, "type": "text",
                                    "text": {"body": "I couldn't attach your file just now. Please try export again."}})
            return
        _post("messages", json={"messaging_product": "whatsapp", "to": to, "type": "document",
                                "document": {"id": media_id, "filename": name, "caption": reply.text[:1024]}})
        return
    _post("messages", json=message_payload(to, reply))


def send_reminder_template(to: str, name: str, count: int) -> None:
    """Reminders usually arrive after the 24-hour window closes, so WhatsApp requires an approved template."""
    _post("messages", json={
        "messaging_product": "whatsapp", "to": to, "type": "template",
        "template": {
            "name": config.WHATSAPP_REMINDER_TEMPLATE, "language": {"code": "en"},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": name or "there"}, {"type": "text", "text": str(count)}]}],
        },
    })


def phone_status() -> dict:
    r = httpx.get(
        f"{_base()}/{config.WHATSAPP_PHONE_ID}",
        params={"fields": "display_phone_number,verified_name,status,quality_rating,name_status"},
        headers=_headers(), timeout=10,
    )
    return r.json()
