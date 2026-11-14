import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def migrate(bind=None) -> None:
    """Bring an older database up to date. create_all only creates missing tables, never missing columns."""
    bind = bind or engine
    log = logging.getLogger("stick.db")
    columns = {c["name"] for c in inspect(bind).get_columns("users")}
    steps = []
    if "wa_id" not in columns:
        steps.append("ALTER TABLE users ADD COLUMN wa_id VARCHAR(32)")
    if "last_inbound_at" not in columns:
        steps.append("ALTER TABLE users ADD COLUMN last_inbound_at TIMESTAMP")
    steps.append("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_wa_id ON users (wa_id)")
    if bind.dialect.name == "postgresql":
        steps.append("ALTER TABLE users ALTER COLUMN tg_id DROP NOT NULL")  # WhatsApp users have no Telegram id
    for step in steps:
        try:
            with bind.begin() as conn:
                conn.execute(text(step))
        except Exception:  # one failed step must not stop the app from starting
            log.exception("Migration step failed: %s", step)


def init_db() -> None:
    from . import models  # noqa: F401  (registers the tables)

    Base.metadata.create_all(engine)
    migrate()
