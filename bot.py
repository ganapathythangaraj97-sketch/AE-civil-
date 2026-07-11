import logging
import os
import asyncio

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)

import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
# Only this Telegram user ID is allowed to upload PDFs / manage content.
# Get your own numeric ID by messaging @userinfobot on Telegram.
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "TNPSC Prep Bot ready.\n\n"
        "Commands:\n"
        "/topics - list available topics\n"
        "/quiz <topic|all> <count> - start a quiz, e.g. /quiz Total Station 10\n"
        "/myweak - see your weak topics\n"
        "/pdfs - list stored PDFs\n"
        + ("/addpdf - (owner only) send a PDF with topic name as caption" if update.effective_user.id == OWNER_ID else "")
    )


async def topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = db.get_topics()
    if not t:
        await update.message.reply_text("No topics loaded yet.")
        return
    await update.message.reply_text("Available topics:\n" + "\n".join(f"- {x}" for x in t))


async def addpdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when a document is sent to the bot. Caption = topic name."""
    if update.effective_user.id != OWNER_ID:
        return  # silently ignore uploads from anyone else
    doc = update.message.document
    if not doc:
        return
    topic = (update.message.caption or "Uncategorized").strip()
    db.add_pdf(topic=topic, file_name=doc.file_name, file_id=doc.file_id)
    await update.message.reply_text(f"Stored '{doc.file_name}' under topic '{topic}'. It stays available indefinitely.")


async def pdfs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_pdfs()
    if not rows:
        await update.message.reply_text("No PDFs stored yet. Send one with the topic name as the caption.")
        return
    for r in rows:
        await update.message.reply_document(r["file_id"], caption=f"{r['topic']} - {r['file_name']}")


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /quiz <topic|all> <count>\nExample: /quiz Total Station 10")
        return

    count = 5
    if args[-1].isdigit():
        count = int(args[-1])
        topic_arg = " ".join(args[:-1])
    else:
        topic_arg = " ".join(args)

    if not topic_arg or topic_arg.lower() == "all":
        questions = db.get_all_questions(limit=count)
    else:
        # case-insensitive topic match
        matched = None
        for t in db.get_topics():
            if t.lower() == topic_arg.lower():
                matched = t
                break
        if not matched:
            await update.message.reply_text(f"No topic found matching '{topic_arg}'. Use /topics to see options.")
            return
        questions = db.get_questions_by_topic(matched, limit=count)

    if not questions:
        await update.message.reply_text("No questions available for that topic yet.")
        return

    await update.message.reply_text(f"Starting quiz: {len(questions)} question(s). Answer each poll as it arrives.")

    for q in questions:
        options = q["options"] if isinstance(q["options"], list) else __import__("json").loads(q["options"])
        explanation = (q.get("explanation") or "")[:200]  # Telegram limit: 200 chars
        message = await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=q["question"][:300],
            options=[o[:100] for o in options],
            type="quiz",
            correct_option_id=q["answer_index"],
            explanation=explanation,
            is_anonymous=False,
        )
        db.save_active_poll(message.poll.id, q["id"], q["topic"], update.effective_chat.id)
        await asyncio.sleep(0.5)  # avoid hitting rate limits when sending many polls


async def poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = update.poll_answer
    poll_record = db.pop_active_poll(ans.poll_id)
    if not poll_record:
        return  # not one of our tracked quiz polls

    conn_q = db.get_conn()
    row = conn_q.execute("SELECT answer_index FROM questions WHERE id = ?", (poll_record["question_id"],)).fetchone()
    conn_q.close()
    if row is None or row["answer_index"] is None:
        return

    correct = (len(ans.option_ids) > 0 and ans.option_ids[0] == row["answer_index"])
    db.log_answer(ans.user.id, poll_record["question_id"], poll_record["topic"], correct)


async def myweak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    report = db.get_weakness_report(update.effective_user.id)
    if not report:
        await update.message.reply_text("No quiz history yet. Take a /quiz first.")
        return
    lines = ["Your accuracy by topic (weakest first):"]
    for r in report:
        pct = 100 * r["correct_count"] / r["total"]
        lines.append(f"- {r['topic']}: {r['correct_count']}/{r['total']} ({pct:.0f}%)")
    await update.message.reply_text("\n".join(lines))


def main():
    if not BOT_TOKEN:
        raise SystemExit("Set the BOT_TOKEN environment variable (get one from @BotFather).")

    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("topics", topics))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("myweak", myweak))
    app.add_handler(CommandHandler("pdfs", pdfs))
    app.add_handler(MessageHandler(filters.Document.PDF, addpdf))
    app.add_handler(PollAnswerHandler(poll_answer))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
