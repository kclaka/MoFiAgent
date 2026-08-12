REVOKE CREATE ON SCHEMA public FROM PUBLIC;

DO $migration$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'mofi_api') THEN
        GRANT SELECT ON rate_observations TO mofi_api;
        GRANT INSERT ON interaction_history TO mofi_api;
    END IF;

    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'mofi_ingest') THEN
        GRANT SELECT, INSERT, UPDATE ON rate_observations TO mofi_ingest;
        GRANT SELECT, INSERT, UPDATE ON ingestion_runs TO mofi_ingest;
    END IF;
END
$migration$;
