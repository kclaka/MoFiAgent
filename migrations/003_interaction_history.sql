CREATE TABLE IF NOT EXISTS interaction_history (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    question TEXT NOT NULL,
    answer TEXT,
    status TEXT NOT NULL,
    tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
    data_as_of DATE,
    source_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_name TEXT,
    latency_ms INTEGER NOT NULL,
    error_code TEXT,
    CONSTRAINT interaction_history_question_check
        CHECK (char_length(question) BETWEEN 3 AND 500),
    CONSTRAINT interaction_history_status_check
        CHECK (status IN ('answered', 'unsupported', 'failed')),
    CONSTRAINT interaction_history_latency_check
        CHECK (latency_ms >= 0)
);

CREATE INDEX IF NOT EXISTS interaction_history_created_idx
    ON interaction_history (created_at DESC, id DESC);

