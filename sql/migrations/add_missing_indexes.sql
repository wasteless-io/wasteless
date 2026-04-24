-- ============================================
-- Migration: Add Missing Performance Indexes
-- Date: 2026-01-11
-- Description: Add indexes identified during backend audit
-- ============================================

-- Add index on waste_detected.resource_id for faster lookups
-- Used by: UI queries, remediation lookups, recommendations joins
CREATE INDEX IF NOT EXISTS idx_waste_detected_resource
    ON waste_detected(resource_id);

-- Success message
DO $$
BEGIN
    RAISE NOTICE '✅ Missing indexes migration completed';
    RAISE NOTICE '   - Index idx_waste_detected_resource created';
    RAISE NOTICE '   - Performance improvement expected for resource_id lookups';
END $$;
