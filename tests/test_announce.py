import pytest
from sqlalchemy import select

from app import bot, config, handlers, models, telegram

OWNER = 111


@pytest.fixture
def setup(monkeypatch, factory):
    sent = []
    monkeypatch.setattr(config, "OWNER_TG_ID", OWNER)
    monkeypatch.setattr(bot, "SessionLocal", factory)
    monkeypatch.setattr(bot, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(telegram, "answer_callback", lambda _id: None)
    monkeypatch.setattr(telegram, "send_reply", lambda chat_id, reply: sent.append((chat_id, reply)) or True)
    with factory() as db:
        for tg_id in (OWNER, 222, 333, 444):
            handlers.handle(db, tg_id, f"user{tg_id}", "help")
        handlers.handle(db, ("wa", "2348000000001"), "Wa", "help")  # WhatsApp people are not part of a Telegram announcement
    sent.clear()
    return sent, factory


def update(text=None, data=None, uid=OWNER):
    chat = {"id": uid, "type": "private"}
    if data:
        return {"callback_query": {"id": "c", "data": data, "from": {"id": uid, "first_name": "x"}, "message": {"chat": chat}}}
    return {"message": {"text": text, "chat": chat, "from": {"id": uid, "first_name": "x"}}}


def texts(sent, chat_id):
    return [r.text for c, r in sent if c == chat_id]


def test_owner_gets_a_preview_with_the_audience_size(setup):
    sent, _ = setup
    bot.process_update(update("/announce Hello everyone\nSecond line"))
    preview = texts(sent, OWNER)[0]
    assert "Hello everyone\nSecond line" in preview and "This will go to 3 people" in preview
    assert [b[0] for b in sent[0][1].buttons[0]] == ["Send to everyone", "Cancel"]
    assert [c for c, _ in sent] == [OWNER]  # nothing has gone to anyone else yet


def test_confirming_sends_to_every_other_telegram_user_once(setup):
    sent, _ = setup
    bot.process_update(update("/announce New things in Stick"))
    sent.clear()
    bot.process_update(update(data="cb:announce:send"))
    assert sorted(c for c, r in sent if r.text == "New things in Stick") == [222, 333, 444]
    assert any("Delivered to 3" in t for t in texts(sent, OWNER))
    assert OWNER not in [c for c, r in sent if r.text == "New things in Stick"]

    sent.clear()
    bot.process_update(update(data="cb:announce:send"))  # a second tap does nothing
    assert not [r for c, r in sent if r.text == "New things in Stick"]
    assert "already" in texts(sent, OWNER)[0]


def test_cancel_sends_nothing(setup):
    sent, _ = setup
    bot.process_update(update("/announce Hi"))
    sent.clear()
    bot.process_update(update(data="cb:announce:cancel"))
    assert texts(sent, OWNER) == ["Cancelled. Nothing was sent."] and len(sent) == 1
    bot.process_update(update(data="cb:announce:send"))
    assert not [r for c, r in sent if r.text == "Hi"]


def test_nobody_else_can_announce(setup):
    sent, _ = setup
    bot.process_update(update("/announce Free airtime for all", uid=222))
    assert "didn't catch" in texts(sent, 222)[0]
    sent.clear()
    bot.process_update(update(data="cb:announce:send", uid=222))
    assert [c for c, _ in sent] == [222] and "expired" in texts(sent, 222)[0]


def test_announce_is_off_when_no_owner_is_set(setup, monkeypatch):
    sent, _ = setup
    monkeypatch.setattr(config, "OWNER_TG_ID", None)
    bot.process_update(update("/announce Hello"))
    assert "didn't catch" in texts(sent, OWNER)[0]


def test_blocked_users_are_counted_not_fatal(setup, monkeypatch):
    sent, _ = setup
    bot.process_update(update("/announce Update"))
    sent.clear()
    monkeypatch.setattr(telegram, "send_reply", lambda chat_id, reply: sent.append((chat_id, reply)) or (chat_id != 333))
    bot.process_update(update(data="cb:announce:send"))
    assert any("Delivered to 2, 1 couldn't be reached" in t for t in texts(sent, OWNER))


def test_a_blank_announce_explains_itself(setup):
    sent, _ = setup
    bot.process_update(update("/announce"))
    assert "Send it as: announce" in texts(sent, OWNER)[0]


def test_myid_tells_you_your_telegram_id(setup):
    sent, _ = setup
    bot.process_update(update("/myid", uid=222))
    assert "222" in texts(sent, 222)[0]


def test_a_different_command_drops_a_pending_announcement(setup):
    sent, factory = setup
    bot.process_update(update("/announce Hello"))
    bot.process_update(update("/stats"))
    sent.clear()
    bot.process_update(update(data="cb:announce:send"))
    assert not [r for c, r in sent if r.text == "Hello"]
