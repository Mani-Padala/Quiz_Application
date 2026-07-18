"""
scorer.py — Scoring and improvement suggestions for the Quiz Application

Responsibilities:
- Break down a completed session's answers by topic (using the existing
  `topic_scores` table), so we know not just "80% overall" but "80% on
  Topic A, 40% on Topic B."
- Save that breakdown once a session finishes.
- Compare a user's performance across ALL their past sessions to surface
  which topics they're consistently weak on — the "improvement suggestions"
  feature described in the project overview.

Integration note: call save_topic_scores() BEFORE quiz.finish_quiz(), since
finish_quiz() deletes the checkpoint that holds the answered_questions data
this module needs. See main.py for the intended call order.
"""

from database import get_db_connection


# ---------------------------------------------------------------------------
# Per-session topic breakdown
# ---------------------------------------------------------------------------

def _get_topic_for_question(question_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT topic_name FROM questions WHERE question_id = ?', (question_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def compute_topic_breakdown(answered_questions):
    """
    Takes the answered_questions dict from a checkpoint (question_id -> 
    {"selected": ..., "correct": bool}) and groups results by topic.

    Returns: dict of topic_name -> {"correct": int, "total": int, "score": float}
    """
    breakdown = {}

    for question_id, result in answered_questions.items():
        topic = _get_topic_for_question(question_id)
        if topic is None:
            continue  # question was deleted from pool since being answered — skip

        if topic not in breakdown:
            breakdown[topic] = {"correct": 0, "total": 0}

        breakdown[topic]["total"] += 1
        if result["correct"]:
            breakdown[topic]["correct"] += 1

    for topic, stats in breakdown.items():
        stats["score"] = (stats["correct"] / stats["total"] * 100) if stats["total"] > 0 else 0.0

    return breakdown


def save_topic_scores(session_id, user_id, answered_questions):
    """
    Computes the per-topic breakdown for a session and saves it into the
    topic_scores table. Call this BEFORE quiz.finish_quiz(), since that
    function deletes the checkpoint holding answered_questions.

    Returns the computed breakdown dict (useful for immediately showing
    the user their per-topic results on the results screen).
    """
    breakdown = compute_topic_breakdown(answered_questions)

    conn = get_db_connection()
    cursor = conn.cursor()
    for topic_name, stats in breakdown.items():
        cursor.execute('''
            INSERT INTO topic_scores
            (session_id, user_id, topic_name, score, total_questions)
            VALUES (?, ?, ?, ?, ?)
        ''', (session_id, user_id, topic_name, stats["score"], stats["total"]))
    conn.commit()
    conn.close()

    return breakdown


# ---------------------------------------------------------------------------
# Cross-session historical performance
# ---------------------------------------------------------------------------

def get_historical_topic_performance(user_id):
    """
    Returns average score per topic for this user, across ALL their past
    sessions — the basis for improvement suggestions.

    Returns: dict of topic_name -> {"average_score": float, "attempts": int}
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT topic_name, AVG(score) as avg_score, COUNT(*) as attempts
        FROM topic_scores
        WHERE user_id = ?
        GROUP BY topic_name
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()

    return {
        row[0]: {"average_score": row[1], "attempts": row[2]}
        for row in rows
    }


def get_topic_trend(user_id, topic_name):
    """
    Returns this user's score history for one specific topic, in
    chronological order — useful for showing "you're improving" or
    "you're stuck" over time on a given topic.

    Returns: list of dicts, each {"session_id": ..., "score": ..., "date": ...}
    ordered oldest to newest.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ts.session_id, ts.score, es.created_at
        FROM topic_scores ts
        JOIN exam_sessions es ON ts.session_id = es.session_id
        WHERE ts.user_id = ? AND ts.topic_name = ?
        ORDER BY es.created_at ASC
    ''', (user_id, topic_name))
    rows = cursor.fetchall()
    conn.close()

    return [
        {"session_id": row[0], "score": row[1], "date": row[2]}
        for row in rows
    ]


def get_improvement_suggestions(user_id, threshold=70.0, min_attempts=1):
    """
    Returns topics this user should focus on — those with an average score
    below `threshold` across their history, sorted weakest first.

    min_attempts avoids flagging a topic based on a single unlucky attempt
    (set higher, e.g. 2 or 3, once there's enough historical data).

    Returns: list of dicts, each:
        {"topic_name": ..., "average_score": ..., "attempts": ...}
    sorted from weakest to strongest.
    """
    performance = get_historical_topic_performance(user_id)

    weak_topics = [
        {"topic_name": topic, **stats}
        for topic, stats in performance.items()
        if stats["average_score"] < threshold and stats["attempts"] >= min_attempts
    ]

    weak_topics.sort(key=lambda t: t["average_score"])
    return weak_topics


def get_overall_summary(user_id):
    """
    A convenience function for a results/dashboard screen: overall stats
    plus the current improvement suggestions in one call.
    """
    performance = get_historical_topic_performance(user_id)
    suggestions = get_improvement_suggestions(user_id)

    if not performance:
        return {
            "topics_attempted": 0,
            "overall_average": 0.0,
            "improvement_suggestions": [],
        }

    overall_average = sum(s["average_score"] for s in performance.values()) / len(performance)

    return {
        "topics_attempted": len(performance),
        "overall_average": overall_average,
        "per_topic_performance": performance,
        "improvement_suggestions": suggestions,
    }