-- ============================================
-- Migration: ec2_metrics.region
-- Date: 2026-07-13
-- Description: le collecteur CloudWatch devient multi-régions ; chaque
--   relevé enregistre la région où l'instance vit. Le détecteur ec2_idle
--   recopie cette région dans le metadata de la détection, que le
--   remédiateur lit (metadata->>'region') avant d'exécuter un stop.
--   Les lignes historiques restent NULL : elles datent de l'époque
--   mono-région où tout venait d'AWS_REGION, le détecteur retombe sur
--   cette variable quand la colonne est vide.
-- ============================================

ALTER TABLE ec2_metrics ADD COLUMN IF NOT EXISTS region VARCHAR(50);

COMMENT ON COLUMN ec2_metrics.region IS
    'AWS region the instance lives in (NULL = collected before multi-region support, assume AWS_REGION)';

DO $$
BEGIN
    RAISE NOTICE '✅ ec2_metrics.region migration completed';
END $$;
