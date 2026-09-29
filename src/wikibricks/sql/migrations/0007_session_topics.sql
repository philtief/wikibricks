CREATE TABLE session_topics (
    session_id uuid PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
    page_path text,
    origin text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
