"""Settings, read from the environment (or a local .env file)."""
import os
import re
from pathlib import Path
from urllib.parse import urlparse


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def clean_token(value: str) -> str:
    """Bot tokens only contain digits, letters, ':', '_' and '-'. Drop stray spaces, quotes, newlines, '<>' and a pasted 'bot' prefix."""
    v = re.sub(r"[^0-9A-Za-z:_\-]", "", value or "")
    return v[3:] if v.lower().startswith("bot") else v


def clean_secret(value: str) -> str:
    """Telegram only allows A-Z, a-z, 0-9, '_' and '-' in the webhook secret."""
    return re.sub(r"[^0-9A-Za-z_\-]", "", value or "")


def clean_plain(value: str) -> str:
    return (value or "").strip().strip("\"'").strip()


def clean_url(value: str) -> str:
    """Keep only scheme://host, so a pasted '/tick?key=...' or trailing slash can't break the webhook address."""
    parts = urlparse(clean_plain(value))
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _database_url() -> str:
    url = clean_plain(os.getenv("DATABASE_URL", "")) or "sqlite:///./stick.db"
    # Render hands out postgres:// or postgresql:// URLs; SQLAlchemy needs a driver name.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = _database_url()
BOT_TOKEN_RAW = os.getenv("TELEGRAM_BOT_TOKEN", "")
BOT_TOKEN = clean_token(BOT_TOKEN_RAW)
WEBHOOK_SECRET = clean_secret(os.getenv("WEBHOOK_SECRET", ""))
TICK_KEY = clean_plain(os.getenv("TICK_KEY", ""))
# On Render, RENDER_EXTERNAL_URL is the service address, so PUBLIC_URL is optional there.
PUBLIC_URL = clean_url(os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "")
