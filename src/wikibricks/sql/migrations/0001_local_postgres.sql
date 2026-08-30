CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE pages (
    page_id uuid PRIMARY KEY,
    path text NOT NULL UNIQUE,
    current_version_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX pages_path_trgm_idx ON pages USING gin (path gin_trgm_ops);

CREATE TABLE page_versions (
    version_id uuid PRIMARY KEY,
    page_id uuid NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    title text NOT NULL,
    page_type text NOT NULL,
    content jsonb NOT NULL,
    content_text text NOT NULL,
    tags text[] NOT NULL DEFAULT '{}',
    source_ids text[],
    parent_id uuid,
    chunk_index integer,
    created_by text NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (page_id, version)
);

ALTER TABLE pages
    ADD CONSTRAINT pages_current_version_fk
    FOREIGN KEY (current_version_id) REFERENCES page_versions(version_id);

CREATE INDEX page_versions_title_trgm_idx ON page_versions USING gin (title gin_trgm_ops);

CREATE TABLE page_search_chunks (
    chunk_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version_id uuid NOT NULL REFERENCES page_versions(version_id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    start_offset integer NOT NULL,
    end_offset integer NOT NULL,
    search_vector tsvector NOT NULL,
    UNIQUE (version_id, chunk_index),
    CHECK (start_offset >= 0 AND end_offset >= start_offset)
);

CREATE INDEX page_search_chunks_vector_idx ON page_search_chunks USING gin (search_vector);

CREATE TABLE links (
    link_id uuid PRIMARY KEY,
    source_page_id uuid NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    target_page_id uuid NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    link_type text NOT NULL,
    origin text NOT NULL DEFAULT 'manual',
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_page_id, target_page_id, link_type)
);

CREATE TABLE sources (
    source_id uuid PRIMARY KEY,
    source_type text NOT NULL,
    uri text,
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE operations (
    operation_id uuid PRIMARY KEY,
    op_type text NOT NULL,
    path text,
    query text,
    details jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    session_id uuid PRIMARY KEY,
    harness text NOT NULL,
    external_id text NOT NULL,
    user_id text NOT NULL,
    agent text,
    workspace text,
    started_at timestamptz,
    source_updated_at timestamptz,
    page_path text NOT NULL UNIQUE,
    title text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    current_hash text NOT NULL CHECK (current_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (harness, external_id)
);

CREATE INDEX sessions_path_trgm_idx ON sessions USING gin (page_path gin_trgm_ops);
CREATE INDEX sessions_title_trgm_idx ON sessions USING gin (title gin_trgm_ops);

CREATE TABLE session_events (
    event_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    external_id text NOT NULL,
    position integer NOT NULL,
    current_version_id uuid,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, external_id)
);

CREATE TABLE session_event_versions (
    version_id uuid PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES session_events(event_id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    kind text NOT NULL,
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    source_created_at timestamptz,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (event_id, version)
);

ALTER TABLE session_events
    ADD CONSTRAINT session_events_current_version_fk
    FOREIGN KEY (current_version_id) REFERENCES session_event_versions(version_id);

CREATE TABLE session_search_chunks (
    chunk_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version_id uuid NOT NULL REFERENCES session_event_versions(version_id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    start_offset integer NOT NULL,
    end_offset integer NOT NULL,
    search_vector tsvector NOT NULL,
    UNIQUE (version_id, chunk_index),
    CHECK (start_offset >= 0 AND end_offset >= start_offset)
);

CREATE INDEX session_search_chunks_vector_idx ON session_search_chunks USING gin (search_vector);

CREATE TABLE sync_outbox (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE,
    entity_kind text NOT NULL,
    entity_id uuid NOT NULL,
    version_id uuid NOT NULL,
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    batch_id uuid,
    acknowledged_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX sync_outbox_pending_idx
    ON sync_outbox (sequence) WHERE acknowledged_at IS NULL;

CREATE TABLE sync_state (
    target text PRIMARY KEY,
    cursor jsonb NOT NULL DEFAULT '{}',
    last_batch_id uuid,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE archive_pages (
    path text PRIMARY KEY,
    title text NOT NULL,
    content jsonb NOT NULL,
    content_text text NOT NULL,
    tags text[] NOT NULL DEFAULT '{}',
    snapshot_version bigint NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    imported_at timestamptz NOT NULL DEFAULT now()
);
