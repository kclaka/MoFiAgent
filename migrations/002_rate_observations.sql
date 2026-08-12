CREATE TABLE IF NOT EXISTS rate_observations (
    observed_at DATE NOT NULL,
    series TEXT NOT NULL,
    tenor_months NUMERIC(5, 1) NOT NULL,
    rate_percent NUMERIC(8, 4) NOT NULL,
    source_url TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (observed_at, series, tenor_months),
    CONSTRAINT rate_observations_series_check
        CHECK (series IN ('treasury_nominal')),
    CONSTRAINT rate_observations_tenor_check
        CHECK (tenor_months IN (1, 1.5, 2, 3, 4, 6, 12, 24, 36, 60, 84, 120, 240, 360)),
    CONSTRAINT rate_observations_value_check
        CHECK (rate_percent BETWEEN -5 AND 100)
);

SELECT create_hypertable(
    'rate_observations',
    by_range('observed_at'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS rate_observations_lookup_idx
    ON rate_observations (series, tenor_months, observed_at DESC);
