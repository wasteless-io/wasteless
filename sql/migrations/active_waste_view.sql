-- Active waste: detected waste whose resource still exists and has not
-- been remediated. Excludes rows whose recommendation is:
--   obsolete            -> the resource no longer exists in AWS
--   applied / approved  -> an action was taken
-- Rows with a pending or rejected recommendation (or none yet) stay
-- active: a rejected resource still costs money, the user just chose to
-- keep it. All Home/Dashboard aggregates read from this view so every
-- page shows the same number.
CREATE OR REPLACE VIEW active_waste AS
SELECT w.*
FROM waste_detected w
LEFT JOIN recommendations r ON r.waste_id = w.id
WHERE COALESCE(r.status, 'pending') NOT IN ('obsolete', 'applied', 'approved');
