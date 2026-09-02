CREATE EXTENSION IF NOT EXISTS lakebase_vector CASCADE;
CREATE EXTENSION IF NOT EXISTS lakebase_text;

CREATE TABLE IF NOT EXISTS remote_search_documents (
    document_id uuid PRIMARY KEY,
    replica_id uuid NOT NULL,
    archive_event_id uuid NOT NULL,
    local_sequence bigint NOT NULL CHECK (local_sequence >= 0),
    entity_kind text NOT NULL,
    entity_id uuid NOT NULL,
    version_id uuid NOT NULL,
    page_path text,
    title text,
    document_kind text NOT NULL CHECK (
        document_kind IN ('page', 'user', 'assistant')
    ),
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    content_text text NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', content_text)
    ) STORED,
    embedding_model text,
    embedding vector(__EMBEDDING_DIMENSION__),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (archive_event_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS remote_search_documents_replica_sequence_idx
    ON remote_search_documents (replica_id, local_sequence DESC);

CREATE INDEX IF NOT EXISTS remote_search_documents_page_version_idx
    ON remote_search_documents (replica_id, page_path, version_id)
    WHERE document_kind = 'page';

CREATE INDEX IF NOT EXISTS remote_search_documents_embedding_ann
    ON remote_search_documents
    USING lakebase_ann (embedding vector_cosine_ops);
