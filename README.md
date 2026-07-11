# TNPSC Prep Bot — Setup Guide (no prior experience needed)

## What you're deploying
A Telegram bot that stores your PDFs, runs quiz polls with native
correct-answer + explanation reveal, and tracks your weak topics.

## Step 1 — Create the bot on Telegram
1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, give it a name and a username (must end in `bot`).
3. BotFather replies with a **token** like `123456:ABC-DEF...`. Copy it — you'll need it in Step 4.

## Step 2 — Find your own Telegram user ID
1. Search for **@userinfobot** on Telegram, start a chat, it replies with your numeric ID.
2. Save this number — it makes YOU the only person allowed to upload PDFs to the bot.

## Step 3 — Put this code on GitHub
1. Create a free GitHub account if you don't have one: github.com
2. Create a new repository (e.g. `tnpsc-bot`), and upload all the files
   in this folder (`bot.py`, `db.py`, `load_seed.py`, `requirements.txt`,
   `Procfile`, `questions_seed.json`) — GitHub's website lets you drag-and-drop
   files, no command line needed.

## Step 4 — Deploy on Railway (free)
1. Go to railway.app, sign up with your GitHub account.
2. Click **New Project** → **Deploy from GitHub repo** → select `tnpsc-bot`.
3. Once it's created, go to the project's **Variables** tab and add:
   - `BOT_TOKEN` = the token from Step 1
   - `OWNER_ID` = your numeric ID from Step 2
4. Go to the **Settings** tab → under "Deploy" make sure the start command
   matches the Procfile (`python bot.py`). Railway usually detects this automatically.
5. **Add a volume** (Settings → Volumes → "New Volume", mount path `/data`)
   so your database file survives restarts. Then add one more variable:
   - `DB_PATH` = `/data/tnpsc_bot.db`
6. Click Deploy. Check the **Logs** tab — you should see `Bot starting...`.

## Step 5 — Load the sample questions
Railway doesn't easily run one-off scripts on the free tier, so instead:
- Open Railway's **Shell** (if available on your plan) and run `python load_seed.py`, OR
- Temporarily change the Procfile's start line to run `python load_seed.py && python bot.py`
  once, then change it back to just `python bot.py` after the first successful deploy.

## Step 6 — Try it
Open Telegram, find your bot by its username, and send:
- `/start`
- `/topics`
- `/quiz Total Station 10`
- Answer a few polls — you'll see the correct answer highlighted and the
  explanation appear automatically (Telegram's native quiz-poll behavior).
- `/myweak` to see your accuracy by topic.

## Adding more PDFs later
Just send any PDF directly to the bot in your DM with it, with the **topic
name typed as the caption** (e.g. caption: "Indian Polity"). Only your
OWNER_ID account can do this — it'll reply confirming storage.
Note: the PDF itself is stored, but turning its questions into quiz polls
still requires converting it into the same JSON format as
`questions_seed.json` — send me the PDF text and I'll generate that file
for you, the same way I did for the Total Station sample.

## What's NOT built yet (next phases)
- Automatic PYQ weightage analysis across many PDFs
- Auto-generated "grand exam" weighted by that analysis
- Voice conversation mode
These need more infrastructure (topic-tagging at scale, and speech
APIs) — good candidates for phase 2 once the core is working for you.
