ALTER TABLE archive_batches
    ADD COLUMN replica_id uuid;

ALTER TABLE archive_events
    ADD COLUMN replica_id uuid;

UPDATE archive_batches
SET replica_id = '00000000-0000-0000-0000-000000000000'
WHERE replica_id IS NULL;

UPDATE archive_events
SET replica_id = '00000000-0000-0000-0000-000000000000'
WHERE replica_id IS NULL;

ALTER TABLE archive_batches
    ALTER COLUMN replica_id SET NOT NULL;

ALTER TABLE archive_events
    ALTER COLUMN replica_id SET NOT NULL;

CREATE INDEX archive_batches_replica_sequence_idx
    ON archive_batches (replica_id, first_sequence, last_sequence);

CREATE INDEX archive_events_replica_sequence_idx
    ON archive_events (replica_id, local_sequence);
