"""Run the bot on your laptop without a public URL:  python -m app.poll"""
import json
import logging
import time

import httpx

from . import config, telegram
from .bot import process_update
from .db import init_db

logging.basicConfig(level=logging.INFO)


def run() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env first.")
    init_db()
    telegram.delete_webhook()  # polling and webhooks can't run together
    print("Stick is listening. Message your bot on Telegram. Ctrl+C to stop.")
    offset = None
    while True:
        try:
            r = httpx.get(
                telegram._api("getUpdates"),
                params={"timeout": 30, "offset": offset, "allowed_updates": json.dumps(["message", "callback_query"])},
                timeout=40,
            )
            for update in r.json().get("result", []):
                offset = update["update_id"] + 1
                process_update(update)
        except KeyboardInterrupt:
            break
        except httpx.HTTPError:
            time.sleep(3)


if __name__ == "__main__":
    run()
