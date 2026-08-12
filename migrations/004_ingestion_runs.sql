CREATE TABLE IF NOT EXISTS ingestion_runs (
    id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    source_url TEXT NOT NULL,
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    rows_upserted INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    CONSTRAINT ingestion_runs_status_check
        CHECK (status IN ('running', 'succeeded', 'failed')),
    CONSTRAINT ingestion_runs_rows_fetched_check CHECK (rows_fetched >= 0),
    CONSTRAINT ingestion_runs_rows_upserted_check CHECK (rows_upserted >= 0)
);

CREATE INDEX IF NOT EXISTS ingestion_runs_started_idx
    ON ingestion_runs (started_at DESC);

