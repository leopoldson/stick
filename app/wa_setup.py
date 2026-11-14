"""One-off WhatsApp setup calls (works on Windows without curl).

    python -m app.wa_setup status
    python -m app.wa_setup register 123456          # 123456 = a 6-digit PIN you choose and keep
    python -m app.wa_setup subscribe <WABA_ID>      # makes Meta send incoming messages to your webhook

Needs WHATSAPP_TOKEN and WHATSAPP_PHONE_ID in .env (or the environment).
"""
import sys

import httpx

from . import config, whatsapp


def main(argv: list[str]) -> None:
    if not whatsapp.enabled():
        raise SystemExit("Set WHATSAPP_TOKEN and WHATSAPP_PHONE_ID in .env first.")
    if argv[:1] == ["status"]:
        print(whatsapp.phone_status())
    elif argv[:1] == ["register"] and len(argv) == 2 and argv[1].isdigit() and len(argv[1]) == 6:
        r = httpx.post(f"{whatsapp._base()}/{config.WHATSAPP_PHONE_ID}/register", headers=whatsapp._headers(),
                       json={"messaging_product": "whatsapp", "pin": argv[1]}, timeout=30)
        print(r.status_code, r.text)
    elif argv[:1] == ["subscribe"] and len(argv) == 2 and argv[1].isdigit():
        r = httpx.post(f"{whatsapp._base()}/{argv[1]}/subscribed_apps", headers=whatsapp._headers(), timeout=30)
        print(r.status_code, r.text)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
