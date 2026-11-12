# Stick

A study coach on Telegram (`@stickstudy_bot`). Text `study CSC301 trees`, then `done`, and Stick logs the session
and brings the topic back at spaced intervals (1, 3, 7, 14, 30 days). Missed it? It returns tomorrow and goes on a revise list.
Everything exports to Excel.

All the logic lives in `app/handlers.py` and knows nothing about Telegram, so a WhatsApp adapter can reuse it later.

## 1. Run it on your laptop (no hosting needed)
```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your token after TELEGRAM_BOT_TOKEN=
python -m app.poll
```
Open `t.me/stickstudy_bot`, send `/start`, then `study csc301 binary trees`. Data goes to a local `stick.db`.
Run the tests with `pip install -r requirements-dev.txt && pytest`.

## 2. Deploy on Render
1. Push this folder to a GitHub repo (`.env` and `*.db` are already git-ignored).
2. Render dashboard: **New > PostgreSQL** (free plan). Copy its **Internal Database URL**.
3. **New > Blueprint**, pick the repo (it reads `render.yaml`). Fill in the four variables:
   - `TELEGRAM_BOT_TOKEN`: from BotFather
   - `DATABASE_URL`: the Internal Database URL from step 2
   - `WEBHOOK_SECRET` and `TICK_KEY`: any long random strings you make up
4. When it's live, put the same token and secret in your local `.env`, add
   `PUBLIC_URL=https://<your-service>.onrender.com`, and run `python -m app.set_webhook` once.
5. **Reminders and keep-awake:** the free web service sleeps after about 15 minutes of silence, so reminders need an outside timer.
   On cron-job.org (free), create a job that calls `https://<your-service>.onrender.com/tick?key=<TICK_KEY>` every 10 minutes.
   `/tick` sends at most one nudge per user per day, only between 08:00 and 21:00 Lagos time.
6. Check Render's current free-tier limits (Postgres expiry in particular) before you rely on it past the competition.

## 3. BotFather polish (2 minutes)
`/setcommands`, choose your bot, paste:
```
study - Start a study session
done - Stop and log it
due - What to review today
quiz - Test yourself
addq - Add a quiz question
stats - Your week and streak
export - Download your Excel log
undo - Cancel or delete your last session
help - Show commands
```
Also `/setdescription` and `/setuserpic`. If the token ever leaks, revoke it with `/revoke` in BotFather.

## Behaviour worth knowing
- Rating after a session sets the first review: Shaky 1 day, Okay 3 days, Solid 7 days. Got it moves one rung up; Missed drops to 1 day.
- `done 3` logs and rates in one message.
- If you never rate a session, it is logged but no review is scheduled for it.
- `undo` cancels a running timer, or deletes the last session (its review schedule stays as it was).
- Days (due dates, streaks) use Lagos time. Timestamps are stored in UTC.
- Telegram bots can't message first: a student has to open the bot and send `/start` once.
- Tested on SQLite. The same SQLAlchemy code targets Postgres, but your first Render deploy is the real check.

## Layout
```
app/handlers.py   commands and conversation flow (channel-independent)
app/srs.py        the review ladder
app/stats.py      weekly totals, streak, recall score
app/export.py     Excel workbook
app/bot.py        Telegram updates in, reminders out
app/main.py       FastAPI: /telegram/webhook, /tick, /health
app/poll.py       local runner
tests/            pytest suite
```
