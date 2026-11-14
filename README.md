# Stick

A study coach on Telegram (`@stickstudy_bot`). Text `study`, choose to start now or log a past session, pick or type a course, then the topic, then `done`, and Stick logs the session
and brings the topic back at spaced intervals (1, 3, 7, 14, 30 days). Missed it? It returns tomorrow and goes on a revise list.
Everything exports to Excel.

All the logic lives in `app/handlers.py` and knows nothing about Telegram or WhatsApp. `app/bot.py` (Telegram) and `app/whatsapp.py` (WhatsApp) are thin adapters on top. WhatsApp is optional: see `WHATSAPP_SETUP.md`.

## 1. Run it on your laptop (no hosting needed)
```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your token after TELEGRAM_BOT_TOKEN=
python -m app.poll
```
Open `t.me/stickstudy_bot`, send `/start`, then `study`, tap **Start a session now**, and type a course and a topic. Data goes to a local `stick.db`.
Run the tests with `pip install -r requirements-dev.txt && pytest`.

## 2. Deploy on Render
1. Push this folder to a GitHub repo (`.env` and `*.db` are already git-ignored).
2. Render dashboard: **New > PostgreSQL** (free plan). Copy its **Internal Database URL**.
3. **New > Blueprint**, pick the repo (it reads `render.yaml`). Fill in the four variables:
   - `TELEGRAM_BOT_TOKEN`: from BotFather
   - `DATABASE_URL`: the Internal Database URL from step 2
   - `WEBHOOK_SECRET` and `TICK_KEY`: any long random strings you make up
4. **The webhook sets itself.** On every start-up the app registers its own address with Telegram, using its own
   token and `WEBHOOK_SECRET` (Render supplies the address). You don't need to run anything locally.
   To check, open `https://<your-service>.onrender.com/diag?key=<TICK_KEY>`. It shows whether the token is accepted,
   what address Telegram is calling, any delivery error, and whether the database is reachable. It never prints secrets.
   (`python -m app.set_webhook` still exists as a manual fallback.)
5. **Reminders and keep-awake:** the free web service sleeps after about 15 minutes of silence, so reminders need an outside timer.
   On cron-job.org (free), create a job that calls `https://<your-service>.onrender.com/tick?key=<TICK_KEY>` every 10 minutes.
   `/tick` sends at most one nudge per user per day, only between 08:00 and 21:00 Lagos time.
6. Check Render's current free-tier limits (Postgres expiry in particular) before you rely on it past the competition.

## 3. BotFather polish (2 minutes)
`/setcommands`, choose your bot, paste:
```
study - Start a study session
log - Log a session you already finished
done - Stop and log it
due - What to review today
quiz - Test yourself
stats - Your week and streak
export - Download your Excel log
undo - Cancel or delete your last session
help - Show commands
```
Also `/setdescription` and `/setuserpic`. If the token ever leaks, revoke it with `/revoke` in BotFather.

## Behaviour worth knowing
- Course names, topics and course titles are saved in title case (`discrete structures` becomes `Discrete Structures`, `csc301` becomes `CSC301`). Names saved earlier are tidied automatically when the app starts.
- `done` on a timer from an earlier day, or one running more than 5 hours, asks when you really stopped (`11:30pm`, `yesterday 11:30pm`, or tap Just now). `done 5pm` gives the stop time in one message.
- Courses you type are added to your list automatically; next time `study` shows them as buttons. `study Physics` skips the course question.
- Rating after a session sets the first review: Shaky 1 day, Okay 2 days, Solid 3 days. (How solid it *felt* right after studying is a weak signal, so even Solid comes back within days. Change `FIRST_REVIEW` in `app/srs.py` to tune it.) Got it moves one rung up; Missed drops to 1 day.
- `/myid` shows your Telegram ID. Set it as `OWNER_TG_ID` and you can send one message to every Telegram user with `announce <message>`: Stick shows a preview and the audience size, and only sends after you tap **Send to everyone**.
- `done 3` logs and rates in one message. Forgot the timer? `log` (or `study`, then **Log a past session**) asks for the day, the start time and the stop time (`9pm`, `19:30`, or a length like `45 minutes`), then course, topic and how solid it felt. `done 45` is a shortcut for 45 minutes ending just now, and Stick says so.
- A Shaky rating asks what felt shaky. Send topics or questions and they join that topic's quizzes. Every saved session then shows command buttons (Quiz me, Study again, My stats, All commands).
- Milestone messages appear on your first, fifth, tenth, 25th, 50th and 100th session (and every 100 after).
- A course typed with its title (`csc301 data structures`) is split into code and title. The Excel Courses & Topics sheet only shows a Title column if at least one course has one.
- `stats` shows the week at a glance (time per course as bars, streak and best streak, recall score, reviews due now). The Excel Dashboard adds charts: minutes per day, time by course, and recall score by week.
- After a quiz, a button lets you add what tripped you up. Those questions join that topic's next quiz. (`addq <question> | <answer>` still works as a shortcut.)
- Mistype a command (`stata`) and Stick offers the right one as a button.
- If you never rate a session, it is logged but no review is scheduled for it.
- `undo` cancels a running timer, or deletes the last session (its review schedule stays as it was).
- Days (due dates, streaks) use Lagos time. Timestamps are stored in UTC.
- Telegram bots can't message first: a student has to open the bot and send `/start` once.
- After updating from an older version, the app adds the new WhatsApp columns to the `users` table on start-up. If an old *local* `stick.db` complains, delete it (dev data only).
- Tested on SQLite. The same SQLAlchemy code targets Postgres, but your first Render deploy is the real check.

## Layout
```
app/handlers.py   commands and conversation flow (channel-independent)
app/srs.py        the review ladder
app/stats.py      weekly totals, streak, recall score
app/export.py     Excel workbook (columns size themselves to the content)
app/textfmt.py    title-casing for course and topic names
app/timeparse.py  reads typed dates and times (9pm, 2130, yesterday, 12 nov)
app/bot.py        Telegram updates in, reminders out
app/main.py       FastAPI: /telegram/webhook, /tick, /health
app/whatsapp.py   WhatsApp Cloud API client (webhook parsing, replies, template reminders)
app/wa_setup.py   one-off WhatsApp setup calls (register, subscribe, status)
app/poll.py       local runner (Telegram only)
tests/            pytest suite
```
