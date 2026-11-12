"""Run once after deploying:  python -m app.set_webhook"""
from . import config, telegram

if __name__ == "__main__":
    if not (config.BOT_TOKEN and config.PUBLIC_URL and config.WEBHOOK_SECRET):
        raise SystemExit("Set TELEGRAM_BOT_TOKEN, PUBLIC_URL and WEBHOOK_SECRET first.")
    print(telegram.set_webhook(f"{config.PUBLIC_URL}/telegram/webhook", config.WEBHOOK_SECRET))
