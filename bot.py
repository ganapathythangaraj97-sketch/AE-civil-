import logging
import os
import json
import time
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import db
from weightage import stars_for_unit

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

# in-memory: session_id -> asyncio.Event, used to let the Skip button
# cut a question short without affecting the overall exam clock
SKIP_EVENTS = {}


def is_owner(update: Update) -> bool:
    return update.effective_user.id == OWNER_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [
        "TNPSC Prep Bot ready.",
        "",
        "/units - list units",
        "/topics <unit> - list topics in a unit",
        "/quiz <unit or topic|all> <count> - quick quiz, no repeats",
        "/exam <count> <minutes> - full timed exam (auto-paced, force-ends on time)",
        "/grandexam <count> - weighted mock exam using official syllabus ratio",
        "/myweak - your accuracy by topic",
        "/pdfs - list stored PDFs",
    ]
    if is_owner(update):
        lines += [
            "",
            "Owner only:",
            "/addpdf - send a PDF with 'Unit X: Chapter' as caption",
            "/fixanswer <question_id> <A|B|C|D> - correct an answer key",
            "/addimage <question_id> - reply to a photo with this caption to attach a diagram",
        ]
    await update.message.reply_text("\n".join(lines))


async def units_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = db.get_units()
    if not u:
        await update.message.reply_text("No units loaded yet.")
        return
    lines = [f"{stars_for_unit(x)} {x}" for x in u]
    await update.message.reply_text("Units loaded:\n" + "\n".join(lines))


async def topics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /topics <unit name>\nSee /units for exact names.")
        return
    unit_query = " ".join(context.args)
    matched = next((u for u in db.get_units() if unit_query.lower() in u.lower()), None)
    if not matched:
        await update.message.reply_text("No matching unit. Check /units for exact names.")
        return
    chapters = db.get_chapters(unit=matched)
    if not chapters:
        await update.message.reply_text(f"No topics loaded yet for {matched}.")
        return
    await update.message.reply_text(f"Topics in {matched}:\n" + "\n".join(f"- {c}" for c in chapters))


async def addpdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    doc = update.message.document
    if not doc:
        return
    caption = (update.message.caption or "Uncategorized: Uncategorized").strip()
    if ":" in caption:
        unit, chapter = [x.strip() for x in caption.split(":", 1)]
    else:
        unit, chapter = caption, "General"
    db.add_pdf(unit=unit, chapter=chapter, file_name=doc.file_name, file_id=doc.file_id)
    await update.message.reply_text(f"Stored '{doc.file_name}' under {unit} / {chapter}.")


async def pdfs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_pdfs()
    if not rows:
        await update.message.reply_text("No PDFs stored yet.")
        return
    for r in rows:
        await update.message.reply_document(r["file_id"], caption=f"{r['unit']} / {r['chapter']} - {r['file_name']}")


async def fixanswer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /fixanswer <question_id> <A|B|C|D>")
        return
    qid, letter = context.args
    letter_map = {"A": 0, "B": 1, "C": 2, "D": 3}
    idx = letter_map.get(letter.upper())
    if idx is None:
        await update.message.reply_text("Answer letter must be A, B, C, or D.")
        return
    ok = db.fix_answer(qid, idx)
    await update.message.reply_text("Updated." if ok else f"No question found with id '{qid}'.")


async def addimage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not update.message.photo:
        await update.message.reply_text("Send this command as the CAPTION of a photo.")
        return
    if not context.args:
        await update.message.reply_text("Usage: caption a photo with /addimage <question_id>")
        return
    qid = context.args[0]
    file_id = update.message.photo[-1].file_id
    ok = db.set_question_image(qid, file_id)
    await update.message.reply_text("Image attached." if ok else f"No question found with id '{qid}'.")


def _parse_options(row):
    return row["options"] if isinstance(row["options"], list) else json.loads(row["options"])


async def _send_one_question(context, chat_id, q, exam_session_id=None):
    if q.get("image_file_id"):
        await context.bot.send_photo(chat_id=chat_id, photo=q["image_file_id"], caption="Refer to this figure ⬆")
        await asyncio.sleep(0.3)
    options = _parse_options(q)
    explanation = (q.get("explanation") or "")[:200]
    prefix = f"{stars_for_unit(q['unit'])} "
    message = await context.bot.send_poll(
        chat_id=chat_id,
        question=(prefix + q["question"])[:300],
        options=[o[:100] for o in options],
        type="quiz",
        correct_option_id=q["answer_index"],
        explanation=explanation,
        is_anonymous=False,
    )
    db.save_active_poll(message.poll.id, q["id"], q["unit"], q["chapter"], chat_id, exam_session_id)
    return message


async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /quiz <unit|topic|all> <count>\nExample: /quiz Bricks 10")
        return
    count = 5
    if args[-1].isdigit():
        count = int(args[-1])
        target = " ".join(args[:-1])
    else:
        target = " ".join(args)

    user_id = update.effective_user.id
    if not target or target.lower() == "all":
        questions = db.pick_questions(user_id, limit=count)
    else:
        matched_unit = next((u for u in db.get_units() if target.lower() in u.lower()), None)
        if matched_unit:
            questions = db.pick_questions(user_id, unit=matched_unit, limit=count)
        else:
            # try as a chapter/topic name across all units
            questions = db.pick_questions(user_id, chapter=target, limit=count)
            if not questions:
                all_chapters = db.get_chapters()
                close = [c for c in all_chapters if target.lower() in c["chapter"].lower()]
                if close:
                    questions = db.pick_questions(user_id, unit=close[0]["unit"], chapter=close[0]["chapter"], limit=count)

    if not questions:
        await update.message.reply_text(f"No questions found matching '{target}'. Check /units or /topics.")
        return

    await update.message.reply_text(f"Starting quiz: {len(questions)} question(s).")
    for q in questions:
        await _send_one_question(context, update.effective_chat.id, q)
        await asyncio.sleep(0.5)


async def grandexam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = 50
    if context.args and context.args[0].isdigit():
        count = int(context.args[0])
    user_id = update.effective_user.id
    questions = db.pick_weighted_grand_exam(user_id, count)
    if not questions:
        await update.message.reply_text("No questions loaded yet.")
        return
    await update.message.reply_text(
        f"Starting weighted grand exam: {len(questions)} question(s), "
        f"proportioned to official TNPSC unit weightage."
    )
    for q in questions:
        await _send_one_question(context, update.effective_chat.id, q)
        await asyncio.sleep(0.5)


async def exam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /exam <count> <minutes>\nExample: /exam 200 180")
        return
    try:
        count = int(context.args[0])
        minutes = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Both count and minutes must be numbers.")
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    questions = db.pick_weighted_grand_exam(user_id, count)
    if not questions:
        await update.message.reply_text("No questions loaded yet.")
        return

    duration_seconds = minutes * 60
    question_ids = [q["id"] for q in questions]
    session_id, per_q = db.create_exam_session(user_id, chat_id, question_ids, duration_seconds)
    per_q_capped = min(per_q, 600)  # Telegram poll auto-close cap is 10 minutes

    await update.message.reply_text(
        f"Exam started: {len(questions)} questions in {minutes} minutes "
        f"(~{per_q} sec/question). It will end automatically at the time limit "
        f"even if unfinished. Use the Skip button under a question to move on early."
    )

    context.application.create_task(
        _run_exam(context, chat_id, session_id, questions, per_q_capped, duration_seconds)
    )


async def _run_exam(context, chat_id, session_id, questions, per_q_capped, duration_seconds):
    start_time = time.time()
    deadline = start_time + duration_seconds

    for idx, q in enumerate(questions):
        if time.time() >= deadline:
            break

        message = await _send_one_question(context, chat_id, q, exam_session_id=session_id)
        skip_event = asyncio.Event()
        SKIP_EVENTS[session_id] = skip_event

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Skip \u2192", callback_data=f"skip:{session_id}")]])
        await context.bot.send_message(chat_id=chat_id, text=f"Question {idx + 1}/{len(questions)}", reply_markup=keyboard)

        remaining = deadline - time.time()
        wait_time = min(per_q_capped, max(1, remaining))
        try:
            await asyncio.wait_for(skip_event.wait(), timeout=wait_time)
        except asyncio.TimeoutError:
            pass
        SKIP_EVENTS.pop(session_id, None)
        db.advance_exam_session(session_id)

        if time.time() >= deadline:
            break

    db.finish_exam_session(session_id, status="finished")
    score = db.get_exam_score(session_id)
    total = len(questions)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"Exam ended. Score: {score['correct_count'] or 0}/{total} "
            f"(attempted {score['attempted'] or 0}/{total})."
        ),
    )


async def skip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, session_id_str = query.data.split(":")
    session_id = int(session_id_str)
    ev = SKIP_EVENTS.get(session_id)
    if ev:
        ev.set()


async def poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = update.poll_answer
    poll_record = db.pop_active_poll(ans.poll_id)
    if not poll_record:
        return

    conn = db.get_conn()
    row = conn.execute("SELECT answer_index FROM questions WHERE id = ?", (poll_record["question_id"],)).fetchone()
    conn.close()
    if row is None or row["answer_index"] is None:
        return

    correct = (len(ans.option_ids) > 0 and ans.option_ids[0] == row["answer_index"])
    db.log_answer(
        ans.user.id, poll_record["question_id"], poll_record["unit"], poll_record["chapter"],
        correct, poll_record.get("exam_session_id"),
    )


async def myweak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    report = db.get_weakness_report(update.effective_user.id)
    if not report:
        await update.message.reply_text("No quiz history yet. Take a /quiz first.")
        return
    lines = ["Your accuracy by topic (weakest first):"]
    for r in report:
        pct = 100 * (r["correct_count"] or 0) / r["total"]
        lines.append(f"- {r['chapter']} ({r['unit']}): {r['correct_count']}/{r['total']} ({pct:.0f}%)")
    await update.message.reply_text("\n".join(lines))


def main():
    if not BOT_TOKEN:
        raise SystemExit("Set the BOT_TOKEN environment variable.")

    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("units", units_cmd))
    app.add_handler(CommandHandler("topics", topics_cmd))
    app.add_handler(CommandHandler("quiz", quiz_cmd))
    app.add_handler(CommandHandler("exam", exam_cmd))
    app.add_handler(CommandHandler("grandexam", grandexam_cmd))
    app.add_handler(CommandHandler("myweak", myweak_cmd))
    app.add_handler(CommandHandler("pdfs", pdfs_cmd))
    app.add_handler(CommandHandler("fixanswer", fixanswer_cmd))
    app.add_handler(MessageHandler(filters.Document.PDF, addpdf))
    app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/addimage"), addimage_cmd))
    app.add_handler(PollAnswerHandler(poll_answer))
    app.add_handler(CallbackQueryHandler(skip_callback, pattern=r"^skip:"))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
