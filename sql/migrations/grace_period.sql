-- Grace period for approvals: an approved recommendation can be scheduled
-- (status = 'scheduled') and executed by the UI background job once
-- execute_after is reached, unless cancelled meanwhile.

ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS execute_after TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_recommendations_execute_after
    ON recommendations (execute_after)
    WHERE status = 'scheduled';
