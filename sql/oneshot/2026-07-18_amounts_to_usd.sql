-- One-shot data migration for the currency change of 2026-07-18.
--
-- Before that date every writer converted USD amounts to EUR at the fixed
-- 0.92 rate before storing them; the code no longer converts anything and
-- new rows are raw USD. This script divides the historical converted rows
-- back by 0.92 so old and new data share the same currency. The *_eur
-- column names are kept (legacy naming, amounts are USD).
--
-- Idempotence warning: running this twice inflates amounts twice. Run once,
-- manually. It lives in sql/oneshot/ (NOT sql/migrations/) because
-- install.sh and CI auto-apply every file in sql/migrations/ on each run,
-- which would re-divide amounts on every install and break on fresh
-- databases where savings_realized does not exist yet.
-- Rollback: multiply the same columns by 0.92.
--
-- Apply with:
--   docker exec -i wasteless-postgres psql -U wasteless -d wasteless \
--     < sql/oneshot/2026-07-18_amounts_to_usd.sql

BEGIN;

UPDATE waste_detected
   SET monthly_waste_eur = ROUND(monthly_waste_eur / 0.92, 4);

-- The per-resource cost stored in metadata is read by the "wasted so far"
-- computation and the resource-details modal until the next detector
-- upsert overwrites it with fresh USD values.
UPDATE waste_detected
   SET metadata = jsonb_set(
        metadata, '{monthly_cost_eur}',
        to_jsonb(ROUND((metadata->>'monthly_cost_eur')::numeric / 0.92, 2)))
 WHERE metadata ? 'monthly_cost_eur';

UPDATE recommendations
   SET estimated_monthly_savings_eur = ROUND(estimated_monthly_savings_eur / 0.92, 4);

UPDATE waste_snapshots
   SET total_eur = ROUND(total_eur / 0.92, 4);

UPDATE savings_realized
   SET cost_before_eur       = ROUND(cost_before_eur / 0.92, 4),
       cost_after_eur        = ROUND(cost_after_eur / 0.92, 4),
       actual_savings_eur    = ROUND(actual_savings_eur / 0.92, 4),
       estimated_savings_eur = ROUND(estimated_savings_eur / 0.92, 4);

COMMIT;
