-- Users table
CREATE TABLE IF NOT EXISTS users(
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- Exam sessions table
CREATE TABLE IF NOT EXISTS exam_sessions(
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    total_score REAL NOT NULL,
    total_questions INTEGER NOT NULL,
    completed INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- Topic scores table — flexible, one row per topic per session
CREATE TABLE IF NOT EXISTS topic_scores(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES exam_sessions(session_id),
    user_id TEXT NOT NULL REFERENCES users(user_id),
    topic_name TEXT NOT NULL,
    score REAL NOT NULL,
    total_questions INTEGER NOT NULL
);

-- Checkpoint table — temporary progress data
CREATE TABLE IF NOT EXISTS checkpoint(
    session_id TEXT PRIMARY KEY REFERENCES exam_sessions(session_id),
    user_id TEXT NOT NULL REFERENCES users(user_id),
    current_question_index INTEGER NOT NULL,
    answered_questions TEXT NOT NULL,
    last_updated TEXT NOT NULL
);

-- Questions table — the persistent pool of generated questions per topic.
-- Generated once by generator.py, reused across all users and all future sessions.
CREATE TABLE IF NOT EXISTS questions(
    question_id TEXT PRIMARY KEY,
    topic_name TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    question_text TEXT NOT NULL,
    options_json TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    explanation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- User question history — tracks which questions a specific user has already
-- been shown, across all their sessions, so they're never repeated for that user.
-- order_index preserves each session's question sequence, so a resumed quiz
-- (via checkpoint.py) can reconstruct exactly which question comes next.
CREATE TABLE IF NOT EXISTS user_question_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    question_id TEXT NOT NULL REFERENCES questions(question_id),
    session_id TEXT NOT NULL REFERENCES exam_sessions(session_id),
    order_index INTEGER NOT NULL,
    shown_at TEXT NOT NULL
);