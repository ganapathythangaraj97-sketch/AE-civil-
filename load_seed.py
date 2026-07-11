"""Run once to load questions_seed.json into the database.
Usage: python load_seed.py
"""
import json
import db

db.init_db()
with open("questions_seed.json", "r", encoding="utf-8") as f:
    questions = json.load(f)

db.add_questions(questions)
print(f"Loaded {len(questions)} questions covering topics: {set(q['topic'] for q in questions)}")
