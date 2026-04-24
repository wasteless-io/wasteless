-- Table des coûts cloud bruts
  CREATE TABLE IF NOT EXISTS cloud_costs_raw (
      id SERIAL PRIMARY KEY,
      provider VARCHAR(20) NOT NULL,
      account_id VARCHAR(100),
      service VARCHAR(100),
      resource_id VARCHAR(200),
      usage_date DATE NOT NULL,
      cost DECIMAL(12, 4),
      currency VARCHAR(3) DEFAULT 'EUR',
      region VARCHAR(50),
      raw_data JSONB,
      created_at TIMESTAMP DEFAULT NOW()
  );
  
  -- Table du waste détecté
  CREATE TABLE IF NOT EXISTS waste_detected (
      id SERIAL PRIMARY KEY,
      detection_date DATE NOT NULL,
      provider VARCHAR(20),
      account_id VARCHAR(100),
      resource_id VARCHAR(200),
      resource_type VARCHAR(50),
      waste_type VARCHAR(50),
      monthly_waste_eur DECIMAL(12, 4),
      confidence_score DECIMAL(3, 2),
      metadata JSONB,
      created_at TIMESTAMP DEFAULT NOW(),
      updated_at TIMESTAMP DEFAULT NOW()
  );
  
  -- Table des recommandations
  CREATE TABLE IF NOT EXISTS recommendations (
      id SERIAL PRIMARY KEY,
      waste_id INTEGER REFERENCES waste_detected(id),
      recommendation_type VARCHAR(50),
      action_required TEXT,
      estimated_monthly_savings_eur DECIMAL(12, 4),
      status VARCHAR(20) DEFAULT 'pending',
      created_at TIMESTAMP DEFAULT NOW(),
      applied_at TIMESTAMP
  );
  
  -- Note: savings_realized table is defined in sql/migrations/remediation_tables.sql
  -- (Complete version with all tracking columns)

  -- Contraintes d'unicité (nécessaires pour ON CONFLICT upserts)
  DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_waste_resource') THEN
      ALTER TABLE waste_detected ADD CONSTRAINT uq_waste_resource UNIQUE (resource_id, resource_type);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_rec_waste') THEN
      ALTER TABLE recommendations ADD CONSTRAINT uq_rec_waste UNIQUE (waste_id);
    END IF;
  END $$;

  -- Index pour performance
  CREATE INDEX IF NOT EXISTS idx_costs_raw_date ON cloud_costs_raw(usage_date);
  CREATE INDEX IF NOT EXISTS idx_costs_raw_provider ON cloud_costs_raw(provider);
  CREATE INDEX IF NOT EXISTS idx_waste_date ON waste_detected(detection_date);
  CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations(status);
  
  -- Base de données séparée pour Metabase
  CREATE DATABASE metabase;