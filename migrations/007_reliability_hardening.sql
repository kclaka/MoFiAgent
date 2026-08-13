DO $migration$
BEGIN
    BEGIN
        CREATE ROLE mofi_api NOLOGIN;
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END;

    BEGIN
        CREATE ROLE mofi_ingest NOLOGIN;
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END;
END
$migration$;

ALTER TABLE interaction_history
    DROP CONSTRAINT interaction_history_status_check;

ALTER TABLE interaction_history
    ADD CONSTRAINT interaction_history_status_check
        CHECK (status IN ('answered', 'unsupported', 'unavailable', 'failed'));

GRANT USAGE ON SCHEMA public TO mofi_api, mofi_ingest;
GRANT SELECT ON rate_observations TO mofi_api;
GRANT INSERT ON interaction_history TO mofi_api;
GRANT SELECT, INSERT, UPDATE ON conversations TO mofi_api;
GRANT SELECT, INSERT, UPDATE ON rate_observations TO mofi_ingest;
GRANT SELECT, INSERT, UPDATE ON ingestion_runs TO mofi_ingest;

DROP INDEX IF EXISTS interaction_history_session_history_idx;
