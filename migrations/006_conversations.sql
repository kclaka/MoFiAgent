CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    completed_turns SMALLINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    closed_at TIMESTAMPTZ,
    next_session_id UUID REFERENCES conversations(id),
    pending_interaction_id UUID,
    pending_started_at TIMESTAMPTZ,
    CONSTRAINT conversations_turn_count_check
        CHECK (completed_turns BETWEEN 0 AND 5),
    CONSTRAINT conversations_status_check
        CHECK (status IN ('active', 'closed')),
    CONSTRAINT conversations_pending_check
        CHECK ((pending_interaction_id IS NULL) = (pending_started_at IS NULL)),
    CONSTRAINT conversations_lifecycle_check
        CHECK (
            (
                status = 'active'
                AND completed_turns < 5
                AND closed_at IS NULL
                AND next_session_id IS NULL
            )
            OR
            (
                status = 'closed'
                AND completed_turns = 5
                AND closed_at IS NOT NULL
                AND next_session_id IS NOT NULL
                AND pending_interaction_id IS NULL
            )
        )
);

ALTER TABLE interaction_history
    ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES conversations(id),
    ADD COLUMN IF NOT EXISTS turn_number SMALLINT;

ALTER TABLE interaction_history
    DROP CONSTRAINT IF EXISTS interaction_history_turn_number_check;

ALTER TABLE interaction_history
    ADD CONSTRAINT interaction_history_turn_number_check
        CHECK (turn_number IS NULL OR turn_number BETWEEN 1 AND 5);

CREATE UNIQUE INDEX IF NOT EXISTS interaction_history_session_turn_idx
    ON interaction_history (session_id, turn_number)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS interaction_history_session_history_idx
    ON interaction_history (session_id, turn_number)
    WHERE session_id IS NOT NULL;

DO $migration$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'mofi_api') THEN
        GRANT SELECT, INSERT, UPDATE ON conversations TO mofi_api;
        GRANT SELECT, INSERT ON interaction_history TO mofi_api;
    END IF;
END
$migration$;
