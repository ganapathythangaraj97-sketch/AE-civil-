import sqlite3
import json
import os

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
        topic TEXT NOT NULL,
        question TEXT NOT NULL,
        options TEXT NOT NULL,      -- JSON list
        answer_index INTEGER,       -- can be NULL if disputed/unknown
        explanation TEXT
    );

    CREATE TABLE IF NOT EXISTS pdfs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT NOT NULL,
        file_name TEXT,
        file_id TEXT NOT NULL,      -- Telegram file_id, valid indefinitely
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS active_polls (
        poll_id TEXT PRIMARY KEY,
        question_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        chat_id INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS answer_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        question_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        correct INTEGER NOT NULL,   -- 1 or 0
        answered_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()


def add_questions(question_list):
    conn = get_conn()
    cur = conn.cursor()
    for q in question_list:
        cur.execute(
            """INSERT OR REPLACE INTO questions (id, topic, question, options, answer_index, explanation)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (q["id"], q["topic"], q["question"], json.dumps(q["options"]),
             q.get("answer_index"), q.get("explanation", "")),
        )
    conn.commit()
    conn.close()


def get_topics():
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT topic FROM questions ORDER BY topic").fetchall()
    conn.close()
    return [r["topic"] for r in rows]


def get_questions_by_topic(topic, limit=None, only_answered=True):
    conn = get_conn()
    q = "SELECT * FROM questions WHERE topic = ?"
    params = [topic]
    if only_answered:
        q += " AND answer_index IS NOT NULL"
    q += " ORDER BY RANDOM()"
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_questions(limit=None, only_answered=True):
    conn = get_conn()
    q = "SELECT * FROM questions"
    if only_answered:
        q += " WHERE answer_index IS NOT NULL"
    q += " ORDER BY RANDOM()"
    params = []
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_active_poll(poll_id, question_id, topic, chat_id):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO active_polls (poll_id, question_id, topic, chat_id) VALUES (?, ?, ?, ?)",
        (poll_id, question_id, topic, chat_id),
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


def log_answer(user_id, question_id, topic, correct):
    conn = get_conn()
    conn.execute(
        "INSERT INTO answer_log (user_id, question_id, topic, correct) VALUES (?, ?, ?, ?)",
        (user_id, question_id, topic, int(correct)),
    )
    conn.commit()
    conn.close()


def get_weakness_report(user_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT topic,
                  COUNT(*) as total,
                  SUM(correct) as correct_count
           FROM answer_log
           WHERE user_id = ?
           GROUP BY topic
           ORDER BY (CAST(SUM(correct) AS FLOAT) / COUNT(*)) ASC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_pdf(topic, file_name, file_id):
    conn = get_conn()
    conn.execute(
        "INSERT INTO pdfs (topic, file_name, file_id) VALUES (?, ?, ?)",
        (topic, file_name, file_id),
    )
    conn.commit()
    conn.close()


def get_pdfs(topic=None):
    conn = get_conn()
    if topic:
        rows = conn.execute("SELECT * FROM pdfs WHERE topic = ? ORDER BY added_at DESC", (topic,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM pdfs ORDER BY topic, added_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]
