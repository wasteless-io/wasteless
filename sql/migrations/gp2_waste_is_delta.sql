-- Correction des détections gp2 historiques : l'ancienne version du détecteur
-- stockait le coût mensuel total du volume comme monthly_waste_eur, alors que
-- remédier (migrer gp2 → gp3) ne fait économiser que le delta de tarif
-- (~20 %, soit 0.0184 €/GiB/mois — voir src/detectors/ebs_gp2_migration.py).
-- Règle produit : waste = ce que la remédiation fait économiser, pas le coût
-- de la ressource. Les lignes récentes portent savings_eur_per_gib dans leur
-- metadata ; celles qui ne l'ont pas sont recalculées ici.
UPDATE waste_detected
SET monthly_waste_eur = ROUND((metadata->>'size_gb')::numeric * 0.0184, 2),
    metadata = metadata || jsonb_build_object(
        'savings_eur_per_gib', 0.0184,
        'monthly_cost_eur', ROUND((metadata->>'size_gb')::numeric * 0.0184, 2)
    ),
    updated_at = NOW()
WHERE waste_type = 'gp2_volume'
  AND metadata ? 'size_gb'
  AND NOT metadata ? 'savings_eur_per_gib';

-- Réaligne les recommandations liées sur le waste corrigé.
UPDATE recommendations r
SET estimated_monthly_savings_eur = w.monthly_waste_eur
FROM waste_detected w
WHERE r.waste_id = w.id
  AND w.waste_type = 'gp2_volume'
  AND r.estimated_monthly_savings_eur IS DISTINCT FROM w.monthly_waste_eur;
