CREATE TABLE archive_batches (
    batch_id uuid PRIMARY KEY,
    schema_version integer NOT NULL,
    event_count integer NOT NULL,
    first_sequence bigint NOT NULL,
    last_sequence bigint NOT NULL,
    digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    committed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE archive_events (
    event_id uuid PRIMARY KEY,
    local_sequence bigint NOT NULL,
    entity_kind text NOT NULL,
    entity_id uuid NOT NULL,
    version_id uuid NOT NULL,
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE archive_batch_events (
    batch_id uuid NOT NULL REFERENCES archive_batches(batch_id) ON DELETE CASCADE,
    event_id uuid NOT NULL REFERENCES archive_events(event_id),
    PRIMARY KEY (batch_id, event_id)
);

CREATE TABLE curated_pages (
    path text NOT NULL,
    title text NOT NULL,
    content jsonb NOT NULL,
    content_text text NOT NULL,
    tags text[] NOT NULL DEFAULT '{}',
    snapshot_version bigint NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_version, path)
);
