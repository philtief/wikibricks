CREATE TABLE remote_maintenance_runs (
    run_id uuid PRIMARY KEY,
    replica_id uuid NOT NULL,
    input_watermark bigint NOT NULL CHECK (input_watermark >= 0),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('no_changes', 'published')),
    proposal_count integer NOT NULL CHECK (proposal_count >= 0),
    manifest_hash text CHECK (
        manifest_hash IS NULL OR manifest_hash ~ '^[0-9a-f]{64}$'
    ),
    report jsonb NOT NULL DEFAULT '{}',
    completed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (replica_id, input_watermark),
    CHECK (
        (status = 'no_changes' AND proposal_count = 0 AND manifest_hash IS NULL)
        OR (status = 'published' AND proposal_count > 0 AND manifest_hash IS NOT NULL)
    )
);

CREATE INDEX remote_maintenance_runs_replica_idx
    ON remote_maintenance_runs (replica_id, input_watermark DESC);
