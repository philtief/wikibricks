CREATE TABLE IF NOT EXISTS sync_replicas (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    replica_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS curation_runs (
    run_id TEXT PRIMARY KEY,
    replica_id TEXT NOT NULL,
    input_watermark INTEGER NOT NULL CHECK (input_watermark >= 0),
    schema_version INTEGER NOT NULL,
    manifest_hash TEXT NOT NULL,
    manifest TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    received_at TEXT,
    applied_at TEXT
);

CREATE INDEX IF NOT EXISTS curation_runs_replica_idx
ON curation_runs(replica_id, input_watermark, published_at);

CREATE TABLE IF NOT EXISTS curation_patches (
    patch_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES curation_runs(run_id) ON DELETE CASCADE,
    group_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    operation TEXT NOT NULL,
    path TEXT NOT NULL,
    base_version_id TEXT,
    base_content_hash TEXT,
    proposed_hash TEXT NOT NULL,
    proposal TEXT NOT NULL,
    evidence_ids TEXT NOT NULL,
    reason TEXT NOT NULL,
    risk_class TEXT NOT NULL,
    UNIQUE(run_id, group_id, position)
);

CREATE TABLE IF NOT EXISTS page_aliases (
    alias_path TEXT PRIMARY KEY,
    target_page_id TEXT NOT NULL REFERENCES pages(page_id),
    curation_patch_id TEXT REFERENCES curation_patches(patch_id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS curation_receipts (
    patch_id TEXT PRIMARY KEY REFERENCES curation_patches(patch_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES curation_runs(run_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    result_version_id TEXT REFERENCES page_versions(version_id),
    local_content_hash TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS curation_conflicts (
    conflict_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES curation_runs(run_id) ON DELETE CASCADE,
    group_id TEXT NOT NULL,
    details TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    resolution TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    UNIQUE(run_id, group_id)
);

CREATE INDEX IF NOT EXISTS curation_conflicts_pending_idx
ON curation_conflicts(created_at) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS archive_pages (
    path TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    content_text TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    snapshot_version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
