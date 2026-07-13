import sqlite3
import json
import os
import time

DB_PATH = os.environ.get("DB_PATH", "tnpsc_bot.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS questions (
        id TEXT PRIMARY KEY,
        unit TEXT NOT NULL,
        chapter TEXT NOT NULL,       -- topic within the unit, e.g. "Bricks", "Cement"
        question TEXT NOT NULL,
        options TEXT NOT NULL,       -- JSON list
        answer_index INTEGER,        -- NULL if disputed/unknown; owner can fix later
        explanation TEXT,
        qtype TEXT DEFAULT 'mcq',    -- 'mcq' or 'ar' (assertion-reason)
        source TEXT DEFAULT 'mcq',   -- 'mcq' or 'pyq'
        year TEXT,                   -- e.g. "AE'13", "2024 TNPSC AE"
        image_file_id TEXT           -- optional Telegram file_id for diagram questions
    );

    CREATE TABLE IF NOT EXISTS pdfs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        unit TEXT,
        chapter TEXT,
        file_name TEXT,
        file_id TEXT NOT NULL,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS active_polls (
        poll_id TEXT PRIMARY KEY,
        question_id TEXT NOT NULL,
        unit TEXT,
        chapter TEXT,
        chat_id INTEGER NOT NULL,
        exam_session_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS answer_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        question_id TEXT NOT NULL,
        unit TEXT,
        chapter TEXT,
        correct INTEGER NOT NULL,
        exam_session_id INTEGER,
        answered_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    -- tracks recently-served questions per user so the quiz picker can avoid repeats
    CREATE TABLE IF NOT EXISTS recent_served (
        user_id INTEGER NOT NULL,
        question_id TEXT NOT NULL,
        served_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, question_id)
    );

    CREATE TABLE IF NOT EXISTS exam_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        total_questions INTEGER NOT NULL,
        duration_seconds INTEGER NOT NULL,
        per_question_seconds INTEGER NOT NULL,
        start_time INTEGER NOT NULL,
        status TEXT DEFAULT 'running',   -- running | finished | timed_out
        question_ids TEXT,               -- JSON ordered list
        current_index INTEGER DEFAULT 0
    );
    """)
    conn.commit()
    conn.close()


# ---------- Questions ----------

def add_questions(question_list):
    conn = get_conn()
    cur = conn.cursor()
    for q in question_list:
        cur.execute(
            """INSERT OR REPLACE INTO questions
               (id, unit, chapter, question, options, answer_index, explanation, qtype, source, year, image_file_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (q["id"], q["unit"], q["chapter"], q["question"], json.dumps(q["options"]),
             q.get("answer_index"), q.get("explanation", ""), q.get("qtype", "mcq"),
             q.get("source", "mcq"), q.get("year", ""), q.get("image_file_id")),
        )
    conn.commit()
    conn.close()


def fix_answer(question_id, new_answer_index):
    conn = get_conn()
    cur = conn.execute("SELECT id FROM questions WHERE id = ?", (question_id,))
    exists = cur.fetchone()
    if not exists:
        conn.close()
        return False
    conn.execute("UPDATE questions SET answer_index = ? WHERE id = ?", (new_answer_index, question_id))
    conn.commit()
    conn.close()
    return True


def set_question_image(question_id, file_id):
    conn = get_conn()
    cur = conn.execute("SELECT id FROM questions WHERE id = ?", (question_id,))
    exists = cur.fetchone()
    if not exists:
        conn.close()
        return False
    conn.execute("UPDATE questions SET image_file_id = ? WHERE id = ?", (file_id, question_id))
    conn.commit()
    conn.close()
    return True


def get_units():
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT unit FROM questions ORDER BY unit").fetchall()
    conn.close()
    return [r["unit"] for r in rows]


def get_chapters(unit=None):
    conn = get_conn()
    if unit:
        rows = conn.execute(
            "SELECT DISTINCT chapter FROM questions WHERE unit = ? ORDER BY chapter", (unit,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT unit, chapter FROM questions ORDER BY unit, chapter").fetchall()
    conn.close()
    return [dict(r) if not unit else r["chapter"] for r in rows]


def _recent_ids_for_user(conn, user_id, within_seconds=7 * 24 * 3600):
    cutoff = int(time.time()) - within_seconds
    rows = conn.execute(
        "SELECT question_id FROM recent_served WHERE user_id = ? AND served_at >= ?",
        (user_id, cutoff),
    ).fetchall()
    return {r["question_id"] for r in rows}


def pick_questions(user_id, unit=None, chapter=None, limit=10, only_answered=True):
    """Pick random questions, avoiding ones recently served to this user when possible."""
    conn = get_conn()
    q = "SELECT * FROM questions WHERE 1=1"
    params = []
    if unit:
        q += " AND unit = ?"
        params.append(unit)
    if chapter:
        q += " AND chapter = ?"
        params.append(chapter)
    if only_answered:
        q += " AND answer_index IS NOT NULL"
    all_rows = conn.execute(q, params).fetchall()
    all_rows = [dict(r) for r in all_rows]

    recent_ids = _recent_ids_for_user(conn, user_id)
    fresh = [r for r in all_rows if r["id"] not in recent_ids]

    import random
    random.shuffle(fresh)
    random.shuffle(all_rows)

    chosen = fresh[:limit]
    if len(chosen) < limit:
        # pool of unseen questions too small - top up with previously-seen ones
        remaining_needed = limit - len(chosen)
        chosen_ids = {c["id"] for c in chosen}
        backfill = [r for r in all_rows if r["id"] not in chosen_ids][:remaining_needed]
        chosen += backfill

    now = int(time.time())
    for c in chosen:
        conn.execute(
            "INSERT OR REPLACE INTO recent_served (user_id, question_id, served_at) VALUES (?, ?, ?)",
            (user_id, c["id"], now),
        )
    conn.commit()
    conn.close()
    return chosen


def pick_weighted_grand_exam(user_id, total_count, only_answered=True):
    """Pick questions across all units proportionally to official syllabus weightage."""
    from weightage import UNIT_WEIGHTAGE, TOTAL_QUESTIONS
    chosen = []
    for unit, weight in UNIT_WEIGHTAGE.items():
        share = max(1, round(total_count * weight / TOTAL_QUESTIONS))
        chosen += pick_questions(user_id, unit=unit, limit=share, only_answered=only_answered)
    return chosen[:total_count]


# ---------- Polls / answers ----------

def save_active_poll(poll_id, question_id, unit, chapter, chat_id, exam_session_id=None):
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO active_polls (poll_id, question_id, unit, chapter, chat_id, exam_session_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (poll_id, question_id, unit, chapter, chat_id, exam_session_id),
    )
    conn.commit()
    conn.close()


def pop_active_poll(poll_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM active_polls WHERE poll_id = ?", (poll_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM active_polls WHERE poll_id = ?", (poll_id,))
        conn.commit()
    conn.close()
    return dict(row) if row else None


def log_answer(user_id, question_id, unit, chapter, correct, exam_session_id=None):
    conn = get_conn()
    conn.execute(
        """INSERT INTO answer_log (user_id, question_id, unit, chapter, correct, exam_session_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, question_id, unit, chapter, int(correct), exam_session_id),
    )
    conn.commit()
    conn.close()


def get_weakness_report(user_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT chapter, unit,
                  COUNT(*) as total,
                  SUM(correct) as correct_count
           FROM answer_log
           WHERE user_id = ?
           GROUP BY unit, chapter
           ORDER BY (CAST(SUM(correct) AS FLOAT) / COUNT(*)) ASC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- PDFs ----------

def add_pdf(unit, chapter, file_name, file_id):
    conn = get_conn()
    conn.execute(
        "INSERT INTO pdfs (unit, chapter, file_name, file_id) VALUES (?, ?, ?, ?)",
        (unit, chapter, file_name, file_id),
    )
    conn.commit()
    conn.close()


def get_pdfs():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM pdfs ORDER BY unit, added_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Exam sessions ----------

def create_exam_session(user_id, chat_id, question_ids, duration_seconds):
    per_q = max(1, duration_seconds // max(1, len(question_ids)))
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO exam_sessions
           (user_id, chat_id, total_questions, duration_seconds, per_question_seconds, start_time, question_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, chat_id, len(question_ids), duration_seconds, per_q, int(time.time()), json.dumps(question_ids)),
    )
    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    return session_id, per_q


def get_exam_session(session_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM exam_sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def advance_exam_session(session_id):
    conn = get_conn()
    conn.execute("UPDATE exam_sessions SET current_index = current_index + 1 WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()


def finish_exam_session(session_id, status="finished"):
    conn = get_conn()
    conn.execute("UPDATE exam_sessions SET status = ? WHERE id = ?", (status, session_id))
    conn.commit()
    conn.close()


def get_exam_score(session_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as attempted, SUM(correct) as correct_count FROM answer_log WHERE exam_session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else {"attempted": 0, "correct_count": 0}
