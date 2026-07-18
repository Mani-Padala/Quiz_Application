"""
main.py — Entry point for the Quiz Application (CLI version)

This never triggers document processing itself — run
`python process_documents.py` first (once, or after changing documents/).
This file only ever reads the caches that produces, same as app.py — so
neither entry point can accidentally trigger a long OCR run inline.

Current phase: fake/local "login" only (just asks for a username, creates
or looks up a matching user_id). Real Auth0-based login is what app.py
uses instead — everything else (session resume, quiz loop, scoring) is
identical either way, since it all operates on a plain user_id string.

Flow:
1. Get/create user (fake login for now)
2. Check for an incomplete session to resume
3. If none: show detected sections, let the user pick which to quiz on
4. Run the quiz loop (question -> answer, repeat)
5. Score the session, show per-topic breakdown + improvement suggestions
"""

import os
import uuid
from datetime import datetime

from database import create_tables, get_db_connection
from ingestion import get_document_sections, SECTIONS_CACHE_PATH
from checkpoint import load_checkpoint
import quiz
import scorer


DOCUMENTS_FOLDER = "documents"


# ---------------------------------------------------------------------------
# Fake login (placeholder — app.py uses real Auth0 login instead)
# ---------------------------------------------------------------------------

def get_or_create_user(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE username = ?', (username,))
    row = cursor.fetchone()

    if row:
        user_id = row[0]
    else:
        user_id = str(uuid.uuid4())
        cursor.execute(
            'INSERT INTO users (user_id, username, created_at) VALUES (?, ?, ?)',
            (user_id, username, datetime.now().isoformat())
        )
        conn.commit()

    conn.close()
    return user_id


# ---------------------------------------------------------------------------
# Session resume check
# ---------------------------------------------------------------------------

def get_incomplete_session(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT session_id FROM exam_sessions
        WHERE user_id = ? AND completed = 0
        ORDER BY created_at DESC LIMIT 1
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Quiz loop
# ---------------------------------------------------------------------------

def run_quiz_loop(session_id, user_id):
    while not quiz.is_quiz_complete(session_id, user_id):
        question = quiz.get_current_question(session_id, user_id)
        if question is None:
            break

        print(f"\n[{question['topic_name']} | {question['difficulty']}]")
        print(question["question_text"])
        for key in ["A", "B", "C", "D"]:
            print(f"  {key}. {question['options'][key]}")

        answer = input("Your answer (A/B/C/D): ").strip().upper()
        result = quiz.submit_answer(session_id, user_id, answer)

        if result["correct"]:
            print("Correct!")
        else:
            print(f"Incorrect. Correct answer: {result['correct_answer']}")
        print(f"Explanation: {result['explanation']}")

    finalize_session(session_id, user_id)


def finalize_session(session_id, user_id):
    checkpoint = load_checkpoint(session_id, user_id)
    if checkpoint is None:
        return

    breakdown = scorer.save_topic_scores(session_id, user_id, checkpoint["answered_questions"])
    summary = quiz.finish_quiz(session_id, user_id)

    print("\n=== Quiz complete ===")
    print(f"Score: {summary['total_correct']}/{summary['total_questions']} ({summary['score_percent']:.1f}%)")
    print("\nPer-topic breakdown:")
    for topic, stats in breakdown.items():
        print(f"  {topic}: {stats['correct']}/{stats['total']} ({stats['score']:.1f}%)")


def show_overall_summary(user_id):
    summary = scorer.get_overall_summary(user_id)
    if summary["topics_attempted"] == 0:
        return

    print("\n=== Your overall performance ===")
    print(f"Overall average across all topics: {summary['overall_average']:.1f}%")

    if summary["improvement_suggestions"]:
        print("\nSuggested focus areas:")
        for s in summary["improvement_suggestions"]:
            print(f"  {s['topic_name']}: {s['average_score']:.1f}% avg over {s['attempts']} attempt(s)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    create_tables()  # safe to call every run — CREATE TABLE IF NOT EXISTS

    if not os.path.exists(SECTIONS_CACHE_PATH):
        print("Documents haven't been processed yet.")
        print("Run this first: python process_documents.py")
        return

    username = input("Enter your username: ").strip()
    user_id = get_or_create_user(username)
    print(f"Welcome, {username}!")

    existing_session = get_incomplete_session(user_id)
    if existing_session:
        choice = input("You have an incomplete quiz. Resume it? (y/n): ").strip().lower()
        if choice == "y":
            run_quiz_loop(existing_session, user_id)
            show_overall_summary(user_id)
            return

    all_sections = get_document_sections(DOCUMENTS_FOLDER)
    section_names = list(all_sections.keys())

    print("\nAvailable sections:")
    for i, name in enumerate(section_names, start=1):
        print(f"  {i}. {name}")

    selection = input("\nEnter section numbers to quiz on, comma-separated (e.g. 1,3): ").strip()
    try:
        selected_indices = [int(x.strip()) - 1 for x in selection.split(",") if x.strip()]
        selected_names = [section_names[i] for i in selected_indices if 0 <= i < len(section_names)]
    except ValueError:
        selected_names = []

    if not selected_names:
        print("No valid sections selected. Exiting.")
        return

    topic_sections = {name: all_sections[name] for name in selected_names}

    difficulty = input("Difficulty (easy/medium/hard) [medium]: ").strip().lower() or "medium"
    questions_per_topic_input = input("Questions per section [10]: ").strip()
    questions_per_topic = int(questions_per_topic_input) if questions_per_topic_input else 10

    print("\nGenerating quiz...")
    session_id = quiz.start_quiz(user_id, topic_sections, difficulty, questions_per_topic)

    run_quiz_loop(session_id, user_id)
    show_overall_summary(user_id)


if __name__ == "__main__":
    main()