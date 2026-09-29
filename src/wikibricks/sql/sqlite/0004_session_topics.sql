CREATE TABLE IF NOT EXISTS session_topics (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
    page_path TEXT,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL
);
