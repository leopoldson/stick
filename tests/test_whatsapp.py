import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from app import bot, clock, config, db as dbmod, handlers, main, models, whatsapp
from app.handlers import Reply


def incoming(text=None, button_id=None, wa="2348012345678", mid="wamid.1", name="Ada", kind=None):
    msg = {"from": wa, "id": mid}
    if button_id:
        msg.update(type="interactive", interactive={"type": "button_reply", "button_reply": {"id": button_id, "title": "x"}})
    elif kind:
        msg["type"] = kind
    else:
        msg.update(type="text", text={"body": text})
    return {"entry": [{"changes": [{"value": {"contacts": [{"wa_id": wa, "profile": {"name": name}}], "messages": [msg]}}]}]}


@pytest.fixture
def wa(monkeypatch, factory):
    sent = []
    monkeypatch.setattr(bot, "SessionLocal", factory)
    monkeypatch.setattr(whatsapp, "send_reply", lambda to, reply: sent.append((to, reply)))
    monkeypatch.setattr(whatsapp, "send_reminder_template", lambda to, name, n: sent.append((to, ("template", name, n))))
    for key, value in dict(WHATSAPP_TOKEN="tok", WHATSAPP_PHONE_ID="123", WHATSAPP_VERIFY_TOKEN="verify-me",
                           WHATSAPP_APP_SECRET="appsecret", TICK_KEY="k").items():
        monkeypatch.setattr(config, key, value)
    return sent, TestClient(main.app), factory


def signed(payload, secret="appsecret"):
    raw = json.dumps(payload).encode()
    return raw, {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}


# ---------- webhook plumbing ----------

def test_verification_handshake(wa):
    _, client, _ = wa
    ok = client.get("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=987654")
    assert ok.status_code == 200 and ok.text == "987654"
    assert client.get("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=1").status_code == 403
    assert client.get("/whatsapp/webhook").status_code == 403


def test_signature_is_enforced(wa):
    sent, client, _ = wa
    raw, headers = signed(incoming("study"))
    assert client.post("/whatsapp/webhook", content=raw).status_code == 403
    assert client.post("/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 403
    assert client.post("/whatsapp/webhook", content=raw, headers=headers).status_code == 200
    assert sent and sent[0][0] == "2348012345678"


def test_garbage_body_still_gets_200(wa):
    _, client, _ = wa
    raw = b"not json"
    headers = {"X-Hub-Signature-256": "sha256=" + hmac.new(b"appsecret", raw, hashlib.sha256).hexdigest()}
    assert client.post("/whatsapp/webhook", content=raw, headers=headers).status_code == 200


# ---------- conversation over WhatsApp ----------

def test_whatsapp_user_can_run_the_whole_loop(wa):
    sent, client, factory = wa
    n = iter(range(1, 100))

    def say(text=None, button_id=None):
        sent.clear()
        raw, headers = signed(incoming(text, button_id, mid=f"wamid.{next(n)}"))
        assert client.post("/whatsapp/webhook", content=raw, headers=headers).status_code == 200
        return [r for _, r in sent]

    say("study")
    assert "Which course" in say(button_id="cb:begin:now")[0].text
    assert "What are you studying" in say("csc301")[0].text
    assert "Timer started: CSC301: Trees" in say("trees")[0].text
    assert "Logged" in say("done")[0].text
    assert "Saved" in say(button_id="cb:rate:3")[0].text
    with factory() as db:
        user = db.scalars(select(models.User)).one()
        assert user.wa_id == "2348012345678" and user.tg_id is None and user.name == "Ada"
        assert user.last_inbound_at is not None


def test_first_message_of_any_kind_gets_a_welcome(wa):
    sent, client, _ = wa
    raw, headers = signed(incoming("hello there"))
    client.post("/whatsapp/webhook", content=raw, headers=headers)
    assert "Hi Ada, I'm Stick" in sent[0][1].text
    sent.clear()
    raw, headers = signed(incoming("qwerty zxcv", mid="wamid.2"))
    client.post("/whatsapp/webhook", content=raw, headers=headers)
    assert "didn't catch" in sent[0][1].text  # only the very first message gets the greeting


def test_duplicate_deliveries_are_answered_once(wa):
    sent, client, _ = wa
    raw, headers = signed(incoming("study", mid="wamid.same"))
    client.post("/whatsapp/webhook", content=raw, headers=headers)
    client.post("/whatsapp/webhook", content=raw, headers=headers)
    assert len(sent) == 1


def test_non_text_messages_get_a_polite_reply(wa):
    sent, client, _ = wa
    raw, headers = signed(incoming(kind="image"))
    client.post("/whatsapp/webhook", content=raw, headers=headers)
    assert "only read text" in sent[0][1].text


def test_status_updates_are_ignored(wa):
    sent, client, _ = wa
    raw, headers = signed({"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]})
    assert client.post("/whatsapp/webhook", content=raw, headers=headers).status_code == 200
    assert sent == []


def test_telegram_and_whatsapp_people_are_separate(factory):
    with factory() as db:
        handlers.handle(db, 5, "Tg", "help")
        handlers.handle(db, ("wa", "2348000000001"), "Wa", "help")
        a, b = db.scalars(select(models.User).order_by(models.User.id)).all()
        assert (a.tg_id, a.wa_id) == (5, None) and (b.tg_id, b.wa_id) == (None, "2348000000001")


# ---------- how replies are shaped for WhatsApp ----------

def test_message_shapes():
    plain = whatsapp.message_payload("234", Reply("hello"))
    assert plain["type"] == "text" and plain["text"]["body"] == "hello"

    three = whatsapp.message_payload("234", Reply("Pick", buttons=[[("1 Shaky", "cb:rate:1"), ("2 Okay", "cb:rate:2")], [("3 Solid", "cb:rate:3")]]))
    assert three["interactive"]["type"] == "button"
    assert [b["reply"]["id"] for b in three["interactive"]["action"]["buttons"]] == ["cb:rate:1", "cb:rate:2", "cb:rate:3"]

    four = whatsapp.message_payload("234", Reply("Pick", buttons=[[(f"Course {i}", f"cb:course:{i}")] for i in range(4)]))
    assert four["interactive"]["type"] == "list"
    assert len(four["interactive"]["action"]["sections"][0]["rows"]) == 4

    many = whatsapp.message_payload("234", Reply("Pick", buttons=[[(f"C{i}", f"cb:course:{i}")] for i in range(15)]))
    assert len(many["interactive"]["action"]["sections"][0]["rows"]) == 10  # WhatsApp lists hold 10 rows

    long_title = whatsapp.message_payload("234", Reply("Pick", buttons=[[("A" * 40, "cb:topic:1")]]))
    assert len(long_title["interactive"]["action"]["buttons"][0]["reply"]["title"]) == 20

    too_long = whatsapp.message_payload("234", Reply("x" * 1500, buttons=[[("Go", "cb:run:help")]]))
    assert too_long["type"] == "text"  # a button message's body is capped at 1024 characters


def test_every_button_label_in_the_bot_fits_whatsapp():
    """Labels are written for Telegram too, but WhatsApp truncates anything over 20 characters."""
    import re
    src = open("app/handlers.py").read()
    labels = re.findall(r'\(\s*"([^"]+)",\s*(?:f?"cb:|"cb:)', src)
    assert labels and all(len(label) <= 20 for label in labels), [l for l in labels if len(l) > 20]


def test_replies_with_files_go_out_as_documents(monkeypatch):
    calls = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"id": "media-1"}

    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "tok")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_ID", "123")
    monkeypatch.setattr(whatsapp.httpx, "post", lambda url, **kw: calls.append((url, kw)) or Resp())
    whatsapp.send_reply("234", Reply("Here you go", document=("log.xlsx", b"PK...")))
    assert calls[0][0].endswith("/123/media") and "file" in calls[0][1]["files"]
    sent = calls[1][1]["json"]
    assert sent["type"] == "document" and sent["document"] == {"id": "media-1", "filename": "log.xlsx", "caption": "Here you go"}


# ---------- reminders ----------

def due_whatsapp_user(factory, hours_since_message):
    with factory() as db:
        for text in ("study", "cb:begin:now", "csc301", "trees", "done 2"):
            handlers.handle(db, ("wa", "2348012345678"), "Ada", text)
        for r in db.scalars(select(models.Review)).all():
            r.due_on = datetime(2026, 11, 20).date()
        db.scalars(select(models.User)).one().last_inbound_at = clock.now_utc() - timedelta(hours=hours_since_message)
        db.commit()


def test_reminder_is_a_normal_message_inside_the_24_hour_window(wa, monkeypatch):
    sent, client, factory = wa
    due_whatsapp_user(factory, hours_since_message=3)
    monkeypatch.setattr(clock, "now_local", lambda: datetime(2026, 11, 20, 10, 0, tzinfo=clock.LAGOS))
    assert client.get("/tick?key=k").json()["sent"] == 1
    to, reply = sent[-1]
    assert isinstance(reply, Reply) and "1 topic" in reply.text


def test_reminder_uses_the_template_after_the_window_closes(wa, monkeypatch):
    sent, client, factory = wa
    due_whatsapp_user(factory, hours_since_message=30)
    monkeypatch.setattr(clock, "now_local", lambda: datetime(2026, 11, 20, 10, 0, tzinfo=clock.LAGOS))
    assert client.get("/tick?key=k").json()["sent"] == 1
    assert sent[-1] == ("2348012345678", ("template", "Ada", 1))


def test_template_payload_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "tok")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_ID", "123")
    class Resp:
        status_code = 200
        text = ""

    monkeypatch.setattr(whatsapp.httpx, "post", lambda url, **kw: calls.append(kw["json"]) or Resp())
    whatsapp.send_reminder_template("234", "Ada", 3)
    body = calls[0]
    assert body["type"] == "template" and body["template"]["name"] == "study_review_due"
    assert [p["text"] for p in body["template"]["components"][0]["parameters"]] == ["Ada", "3"]


def test_old_processed_message_ids_are_cleaned_up(wa, monkeypatch):
    sent, client, factory = wa
    with factory() as db:
        db.add(models.ProcessedMessage(id="old", at=clock.now_utc() - timedelta(days=5)))
        db.add(models.ProcessedMessage(id="new", at=clock.now_utc()))
        db.commit()
    monkeypatch.setattr(clock, "now_local", lambda: datetime(2026, 11, 20, 10, 0, tzinfo=clock.LAGOS))
    client.get("/tick?key=k")
    with factory() as db:
        assert [m.id for m in db.scalars(select(models.ProcessedMessage)).all()] == ["new"]


# ---------- diagnostics, privacy page, migration ----------

def test_diag_reports_whatsapp_without_secrets(wa, monkeypatch):
    _, client, _ = wa
    monkeypatch.setattr(whatsapp, "phone_status", lambda: {"display_phone_number": "+234 801 234 5678", "status": "CONNECTED", "name_status": "APPROVED"})
    monkeypatch.setattr(bot.telegram, "get_me", lambda: {"ok": True, "result": {"username": "stickstudy_bot"}})
    monkeypatch.setattr(bot.telegram, "get_webhook_info", lambda: {"result": {}})
    body = client.get("/diag?key=k").json()
    assert body["whatsapp"]["configured"] and body["whatsapp"]["status"] == "CONNECTED"
    assert body["whatsapp"]["verify_token_set"] and body["whatsapp"]["app_secret_set"]
    text = json.dumps(body)
    assert "appsecret" not in text and "verify-me" not in text and '"tok"' not in text


def test_privacy_page_is_public(wa):
    _, client, _ = wa
    r = client.get("/privacy")
    assert r.status_code == 200 and "Stick privacy policy" in r.text and "Deleting your data" in r.text


def test_migration_upgrades_an_old_users_table():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, tg_id BIGINT NOT NULL, name VARCHAR(100), state VARCHAR(30), "
                          "state_data TEXT, last_reminded_on DATE, created_at DATETIME)"))
        conn.execute(text("INSERT INTO users (id, tg_id, name) VALUES (1, 42, 'Old user')"))
    dbmod.migrate(engine)
    dbmod.migrate(engine)  # running it again is harmless
    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    assert {"wa_id", "last_inbound_at"} <= cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT tg_id FROM users")).scalar() == 42
