-- Photographie quotidienne du gaspillage actif par type de ressource.
-- Alimentée par chaque run de détecteur (src/core/snapshots.py) : contrairement
-- à waste_detected.updated_at qui bouge à chaque re-scan, cette table conserve
-- un historique stable pour les graphiques de tendance.
CREATE TABLE IF NOT EXISTS waste_snapshots (
    snapshot_date DATE NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    total_eur DECIMAL(12, 4) NOT NULL DEFAULT 0,
    resource_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, resource_type)
);

CREATE INDEX IF NOT EXISTS idx_waste_snapshots_date ON waste_snapshots(snapshot_date);
