-- Monthly cloud budget for the Reports page (CFO lens: spend vs budget).
-- One active budget amount in USD, editable from /reports. History is kept
-- (one row per change, newest wins) so a report of a past month can, later,
-- still reason against the budget that was in force -- but the reporting
-- code currently just reads the latest row.
--
-- All amounts are USD, no conversion (see CLAUDE.md). The column name uses
-- _usd on purpose (unlike the legacy *_eur columns) since this table is new.
--
-- Application:
--   docker exec -i wasteless-postgres psql -U wasteless -d wasteless < sql/migrations/budget.sql

CREATE TABLE IF NOT EXISTS budget_settings (
    id             SERIAL PRIMARY KEY,
    monthly_usd    DECIMAL(14, 2) NOT NULL CHECK (monthly_usd >= 0),
    updated_by     VARCHAR(80) DEFAULT 'settings_ui',
    updated_at     TIMESTAMP NOT NULL DEFAULT NOW()
);
