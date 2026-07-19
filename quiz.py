"""
quiz.py — Quiz session management for the Quiz Application

Responsibilities:
- Build (or reuse) a persistent pool of questions per topic, generated once
  via generator.py and stored in the `questions` table.
- Track which questions each user has already seen (`user_question_history`),
  so a user is never shown the same question twice, across any number of
  quiz attempts.
- If a user has already seen every question currently in a topic's pool,
  generate additional new ones automatically — the pool grows per topic,
  it never repeats for that user.
- Present questions one at a time, using checkpoint.py (already complete)
  to persist progress after every answer.

Depends on: database.py, checkpoint.py, generator.py (all already built).
"""

import uuid
import json
import random
from datetime import datetime

from database import get_db_connection
from checkpoint import save_checkpoint, load_checkpoint, delete_checkpoint
from generator import generate_questions

# Max number of context chunks ever sent to the generator in one call. A
# section like "Full Document" (no headings detected, so everything landed
# in one bucket) can contain thousands of chunks — joining all of them into
# one prompt exceeds Groq's context limit and causes a 500 error. Random
# sampling still gives good topic coverage without blowing the prompt size.
MAX_CONTEXT_CHUNKS_PER_GENERATION = 15


# ---------------------------------------------------------------------------
# Question pool management
# ---------------------------------------------------------------------------

def _get_unseen_question_ids(user_id, topic, difficulty, limit):
    """
    Returns up to `limit` question_ids for this topic/difficulty that this
    specific user has never been shown before, in random order.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT q.question_id
        FROM questions q
        WHERE q.topic_name = ? AND q.difficulty = ?
        AND q.question_id NOT IN (
            SELECT question_id FROM user_question_history WHERE user_id = ?
        )
        ORDER BY RANDOM()
        LIMIT ?
    ''', (topic, difficulty, user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


def _insert_generated_questions(topic, difficulty, questions):
    """
    Inserts freshly generated questions (from generator.py) into the
    persistent `questions` pool. Returns the list of new question_ids.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    new_ids = []
    timestamp = datetime.now().isoformat()
    for q in questions:
        question_id = str(uuid.uuid4())
        cursor.execute('''
            INSERT INTO questions
            (question_id, topic_name, difficulty, question_text, options_json,
             correct_answer, explanation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            question_id, topic, difficulty, q["question"],
            json.dumps(q["options"]), q["correct_answer"], q["explanation"], timestamp
        ))
        new_ids.append(question_id)
    conn.commit()
    conn.close()
    return new_ids


def ensure_enough_unseen(user_id, topic, difficulty, context_chunks, needed, api_key=None):
    """
    Guarantees at least `needed` unseen (for this user) questions exist for
    this topic/difficulty, generating more via generator.py if the current
    pool doesn't have enough the user hasn't already seen.

    api_key: optional per-user Groq API key, passed straight through to
    generator.py so each user consumes their own quota instead of everyone
    sharing one server-side key.

    Returns exactly `needed` question_ids.
    """
    unseen_ids = _get_unseen_question_ids(user_id, topic, difficulty, needed)

    if len(unseen_ids) < needed:
        shortfall = needed - len(unseen_ids)

        if len(context_chunks) > MAX_CONTEXT_CHUNKS_PER_GENERATION:
            sampled_chunks = random.sample(context_chunks, MAX_CONTEXT_CHUNKS_PER_GENERATION)
        else:
            sampled_chunks = context_chunks

        new_questions = generate_questions(
            context_chunks=sampled_chunks,
            topic=topic,
            difficulty=difficulty,
            num_questions=shortfall,
            api_key=api_key,
        )
        new_ids = _insert_generated_questions(topic, difficulty, new_questions)
        unseen_ids.extend(new_ids)

    return unseen_ids[:needed]


# ---------------------------------------------------------------------------
# Question lookup / formatting
# ---------------------------------------------------------------------------

def _get_question_by_id(question_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT question_id, topic_name, difficulty, question_text,
               options_json, correct_answer, explanation
        FROM questions WHERE question_id = ?
    ''', (question_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "question_id": row[0],
        "topic_name": row[1],
        "difficulty": row[2],
        "question_text": row[3],
        "options": json.loads(row[4]),
        "correct_answer": row[5],
        "explanation": row[6],
    }


def get_question_sequence(session_id):
    """
    Returns the full ordered list of questions for a session, reconstructed
    from user_question_history's order_index — this is what makes resume
    (via checkpoint.py) work correctly after an interruption.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT question_id FROM user_question_history
        WHERE session_id = ?
        ORDER BY order_index
    ''', (session_id,))
    rows = cursor.fetchall()
    conn.close()
    return [_get_question_by_id(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def start_quiz(user_id, topic_sections, difficulty="medium", questions_per_topic=10, api_key=None):
    """
    Starts a new quiz session covering multiple topics/sections.

    topic_sections: dict mapping topic_name -> list of context chunk strings
        (typically the output of retriever.py's retrieve_context() for that
        section of the source document).
    difficulty: "easy" | "medium" | "hard" for this whole session.
    questions_per_topic: how many questions to include per topic/section.
    api_key: optional per-user Groq API key — see ensure_enough_unseen.

    Returns the new session_id.
    """
    session_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()

    all_question_ids = []
    for topic, context_chunks in topic_sections.items():
        question_ids = ensure_enough_unseen(
            user_id, topic, difficulty, context_chunks, questions_per_topic, api_key=api_key
        )
        all_question_ids.extend(question_ids)

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO exam_sessions
        (session_id, user_id, total_score, total_questions, completed, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (session_id, user_id, 0.0, len(all_question_ids), 0, timestamp))

    for order_index, question_id in enumerate(all_question_ids):
        cursor.execute('''
            INSERT INTO user_question_history
            (user_id, question_id, session_id, order_index, shown_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, question_id, session_id, order_index, timestamp))

    conn.commit()
    conn.close()

    save_checkpoint(session_id, user_id, current_question_index=0, answered_questions={})
    return session_id


def get_current_question(session_id, user_id):
    """
    Returns the question the user should see next (resuming correctly via
    checkpoint.py if the session was interrupted), or None if the quiz is
    already complete.
    """
    checkpoint = load_checkpoint(session_id, user_id)
    if checkpoint is None:
        raise ValueError(f"No checkpoint found for session {session_id}. Did you call start_quiz()?")

    sequence = get_question_sequence(session_id)
    index = checkpoint["current_question_index"]

    if index >= len(sequence):
        return None  # quiz complete

    return sequence[index]


def submit_answer(session_id, user_id, selected_option):
    """
    Records the user's answer to the current question, advances the
    checkpoint, and returns whether it was correct plus the explanation.
    """
    checkpoint = load_checkpoint(session_id, user_id)
    if checkpoint is None:
        raise ValueError(f"No checkpoint found for session {session_id}.")

    sequence = get_question_sequence(session_id)
    index = checkpoint["current_question_index"]

    if index >= len(sequence):
        raise ValueError("No current question — quiz is already complete.")

    question = sequence[index]
    is_correct = selected_option == question["correct_answer"]

    answered = checkpoint["answered_questions"]
    answered[question["question_id"]] = {
        "selected": selected_option,
        "correct": is_correct,
    }

    save_checkpoint(session_id, user_id, index + 1, answered)

    return {
        "correct": is_correct,
        "correct_answer": question["correct_answer"],
        "explanation": question["explanation"],
    }


def is_quiz_complete(session_id, user_id):
    checkpoint = load_checkpoint(session_id, user_id)
    if checkpoint is None:
        return False
    sequence = get_question_sequence(session_id)
    return checkpoint["current_question_index"] >= len(sequence)


def finish_quiz(session_id, user_id):
    """
    Finalizes a completed quiz: tallies the score, updates exam_sessions,
    and clears the checkpoint (it's no longer needed once complete).

    Returns a summary dict — per-topic breakdown is handled separately by
    scorer.py, this just finalizes the session record.
    """
    checkpoint = load_checkpoint(session_id, user_id)
    if checkpoint is None:
        raise ValueError(f"No checkpoint found for session {session_id}.")

    answered = checkpoint["answered_questions"]
    total_questions = len(answered)
    total_correct = sum(1 for a in answered.values() if a["correct"])
    score = (total_correct / total_questions * 100) if total_questions > 0 else 0.0

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE exam_sessions
        SET total_score = ?, completed = 1
        WHERE session_id = ?
    ''', (score, session_id))
    conn.commit()
    conn.close()

    delete_checkpoint(session_id)

    return {
        "session_id": session_id,
        "total_questions": total_questions,
        "total_correct": total_correct,
        "score_percent": score,
    }