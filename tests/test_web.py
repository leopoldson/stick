from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import bot, clock, config, handlers, main, models, telegram


def update(text=None, data=None, uid=42, chat_type="private"):
    chat = {"id": uid, "type": chat_type}
    if data:
        return {"callback_query": {"id": "cb1", "data": data, "from": {"id": uid, "first_name": "Fav"},
                                   "message": {"chat": chat}}}
    return {"message": {"text": text, "chat": chat, "from": {"id": uid, "first_name": "Fav"}}}


def setup(monkeypatch, factory):
    sent = []
    monkeypatch.setattr(bot, "SessionLocal", factory)
    monkeypatch.setattr(telegram, "send_reply", lambda chat_id, reply: sent.append((chat_id, reply)))
    monkeypatch.setattr(telegram, "answer_callback", lambda _id: None)
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(config, "TICK_KEY", "k")
    return sent, TestClient(main.app)  # no `with`: skips startup so no real DB file is created


def test_webhook_checks_secret_and_replies(monkeypatch, factory):
    sent, client = setup(monkeypatch, factory)
    assert client.post("/telegram/webhook", json=update("study")).status_code == 403
    r = client.post("/telegram/webhook", json=update("study"),
                    headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert r.status_code == 200
    assert sent and "start a session now" in sent[0][1].text and sent[0][0] == 42


def test_webhook_handles_buttons_and_ignores_groups_and_junk(monkeypatch, factory):
    sent, client = setup(monkeypatch, factory)
    h = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}
    client.post("/telegram/webhook", json=update("hello", chat_type="group"), headers=h)
    client.post("/telegram/webhook", json={"edited_message": {}}, headers=h)
    assert sent == []
    client.post("/telegram/webhook", json=update(data="cb:quiz"), headers=h)
    assert "Nothing due" in sent[0][1].text


def test_tick_sends_one_reminder_in_daytime_only(monkeypatch, factory):
    sent, client = setup(monkeypatch, factory)
    with factory() as db:
        for text in ("study", "cb:begin:now", "csc301", "trees", "done 1"):
            handlers.handle(db, 7, "Fav", text)
        for r in db.scalars(select(models.Review)).all():
            r.due_on = clock.today_local()
        db.commit()
    sent.clear()

    monkeypatch.setattr(clock, "now_local", lambda: datetime(2026, 11, 20, 23, 0, tzinfo=clock.LAGOS))
    assert client.get("/tick?key=k").json()["sent"] == 0  # quiet hours

    monkeypatch.setattr(clock, "now_local", lambda: datetime(2026, 11, 20, 10, 0, tzinfo=clock.LAGOS))
    with factory() as db:
        for r in db.scalars(select(models.Review)).all():
            r.due_on = datetime(2026, 11, 20).date()
        db.commit()
    assert client.get("/tick", headers={"X-Tick-Key": "k"}).json()["sent"] == 1
    assert "1 topic" in sent[0][1].text and sent[0][1].buttons
    assert client.get("/tick?key=k").json()["sent"] == 0  # already nudged today
    assert client.get("/tick?key=wrong").status_code == 403


def test_webhook_returns_200_even_if_sending_fails(monkeypatch, factory):
    sent, client = setup(monkeypatch, factory)

    def boom(chat_id, reply):
        raise RuntimeError("network down")

    monkeypatch.setattr(telegram, "send_reply", boom)
    r = client.post("/telegram/webhook", json=update("help"), headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert r.status_code == 200


def test_ensure_webhook_registers_with_own_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram, "set_webhook", lambda url, secret, drop_pending=False: calls.append((url, secret)) or {"ok": True})
    monkeypatch.setattr(config, "BOT_TOKEN", "123:abc")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://stick.onrender.com")
    assert bot.ensure_webhook() == "ok"
    assert calls == [("https://stick.onrender.com/telegram/webhook", "s3cret")]


def test_ensure_webhook_skips_when_unconfigured_and_never_raises(monkeypatch):
    monkeypatch.setattr(config, "BOT_TOKEN", "")
    assert bot.ensure_webhook().startswith("skipped")
    monkeypatch.setattr(config, "BOT_TOKEN", "123:abc")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://stick.onrender.com")

    def boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(telegram, "set_webhook", boom)
    assert bot.ensure_webhook() == "failed: RuntimeError"


def test_diag_needs_key_and_reveals_no_secrets(monkeypatch, factory):
    sent, client = setup(monkeypatch, factory)
    monkeypatch.setattr(config, "BOT_TOKEN", "123:SUPERSECRETTOKEN")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://stick.onrender.com")
    monkeypatch.setattr(telegram, "get_me", lambda: {"ok": True, "result": {"username": "stickstudy_bot"}})
    monkeypatch.setattr(telegram, "get_webhook_info", lambda: {"result": {
        "url": "https://stick.onrender.com/telegram/webhook", "pending_update_count": 0}})
    assert client.get("/diag").status_code == 403
    assert client.get("/diag?key=wrong").status_code == 403
    r = client.get("/diag?key=k")
    assert r.status_code == 200
    body = r.json()
    assert body["bot"] == "@stickstudy_bot" and body["database"] == "ok"
    assert body["webhook"]["url"] == body["webhook"]["expected_url"]
    assert "SUPERSECRETTOKEN" not in r.text and "s3cret" not in r.text
