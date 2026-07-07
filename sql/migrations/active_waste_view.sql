-- Active waste: detected waste whose resource still exists and has not
-- been remediated. Excludes rows whose recommendation is:
--   obsolete            -> the resource no longer exists in AWS
--   applied / approved  -> an action was actually taken against AWS
--   dismissed           -> the user explicitly chose to stop counting it
-- Rows with a pending or rejected recommendation (or none yet) stay
-- active: a rejected resource still costs money, the user just chose to
-- postpone the decision. approved_manual also stays active: it means a
-- human confirmed a manual-review recommendation (release_ip,
-- delete_snapshot, ...), but wasteless never touches AWS for those —
-- the resource keeps costing money until the human deletes it themselves
-- and a sync confirms it's gone (-> obsolete). All Home/Dashboard
-- aggregates read from this view so every page shows the same number.
CREATE OR REPLACE VIEW active_waste AS
SELECT w.*
FROM waste_detected w
LEFT JOIN recommendations r ON r.waste_id = w.id
WHERE COALESCE(r.status, 'pending') NOT IN ('obsolete', 'applied', 'approved', 'dismissed');
