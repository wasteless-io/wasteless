-- Contrainte d'unicité sur cloud_costs_raw.
--
-- Les deux writers (src/aws_collector.py : une ligne par service et par
-- jour ; scripts/store_aws_real_monthly_cost.py : une ligne par compte et
-- par mois avec service='ALL') utilisent ON CONFLICT, mais sans contrainte
-- unique la clause était inopérante : chaque re-run dupliquait les lignes
-- et gonflait le dénominateur du Waste Rate.

-- Dédoublonnage préalable (garde la ligne la plus récente de chaque clé)
DELETE FROM cloud_costs_raw a
USING cloud_costs_raw b
WHERE a.id < b.id
  AND a.provider IS NOT DISTINCT FROM b.provider
  AND a.account_id IS NOT DISTINCT FROM b.account_id
  AND a.service IS NOT DISTINCT FROM b.service
  AND a.usage_date = b.usage_date;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_cloud_costs') THEN
    ALTER TABLE cloud_costs_raw
      ADD CONSTRAINT uq_cloud_costs UNIQUE (provider, account_id, service, usage_date);
  END IF;
END $$;
