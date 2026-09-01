CREATE TABLE sync_replicas (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    replica_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE curation_runs (
    run_id uuid PRIMARY KEY,
    replica_id uuid NOT NULL,
    input_watermark bigint NOT NULL CHECK (input_watermark >= 0),
    schema_version integer NOT NULL,
    manifest_hash text NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    manifest jsonb NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    received_at timestamptz,
    applied_at timestamptz
);

CREATE INDEX curation_runs_replica_idx
    ON curation_runs (replica_id, input_watermark, published_at);

CREATE TABLE curation_patches (
    patch_id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES curation_runs(run_id) ON DELETE CASCADE,
    group_id uuid NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    operation text NOT NULL CHECK (
        operation IN ('create_page', 'update_page', 'retarget_links', 'add_alias', 'supersede_page')
    ),
    path text NOT NULL,
    base_version_id uuid,
    base_content_hash text CHECK (
        base_content_hash IS NULL OR base_content_hash ~ '^[0-9a-f]{64}$'
    ),
    proposed_hash text NOT NULL CHECK (proposed_hash ~ '^[0-9a-f]{64}$'),
    proposal jsonb NOT NULL,
    evidence_ids text[] NOT NULL,
    reason text NOT NULL,
    risk_class text NOT NULL CHECK (risk_class IN ('low', 'medium', 'high')),
    UNIQUE (run_id, group_id, position)
);

ALTER TABLE page_versions
    ADD COLUMN curation_patch_id uuid REFERENCES curation_patches(patch_id);

ALTER TABLE pages
    ADD COLUMN status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded')),
    ADD COLUMN superseded_by_page_id uuid REFERENCES pages(page_id),
    ADD CONSTRAINT pages_supersede_state_check CHECK (
        (status = 'active' AND superseded_by_page_id IS NULL)
        OR (status = 'superseded' AND superseded_by_page_id IS NOT NULL)
    );

CREATE INDEX pages_active_path_idx ON pages (path) WHERE status = 'active';

CREATE TABLE page_aliases (
    alias_path text PRIMARY KEY,
    target_page_id uuid NOT NULL REFERENCES pages(page_id),
    curation_patch_id uuid REFERENCES curation_patches(patch_id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE curation_receipts (
    patch_id uuid PRIMARY KEY REFERENCES curation_patches(patch_id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES curation_runs(run_id) ON DELETE CASCADE,
    status text NOT NULL CHECK (
        status IN ('applied', 'already_applied', 'kept_local', 'merged')
    ),
    result_version_id uuid REFERENCES page_versions(version_id),
    local_content_hash text CHECK (
        local_content_hash IS NULL OR local_content_hash ~ '^[0-9a-f]{64}$'
    ),
    details jsonb NOT NULL DEFAULT '{}',
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE curation_conflicts (
    conflict_id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES curation_runs(run_id) ON DELETE CASCADE,
    group_id uuid NOT NULL,
    details jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved')),
    resolution text CHECK (
        resolution IS NULL OR resolution IN ('keep_local', 'accept_remote', 'merged')
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    UNIQUE (run_id, group_id)
);

CREATE INDEX curation_conflicts_pending_idx
    ON curation_conflicts (created_at) WHERE status = 'pending';
