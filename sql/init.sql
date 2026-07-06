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
      ai_insight TEXT,
      created_at TIMESTAMP DEFAULT NOW(),
      applied_at TIMESTAMP
  );
  
  -- Note: savings_realized table is defined in sql/migrations/remediation_tables.sql
  -- (Complete version with all tracking columns)

  -- Photographie quotidienne du gaspillage actif par type de ressource
  -- (historique stable pour les tendances, voir sql/migrations/waste_snapshots.sql)
  CREATE TABLE IF NOT EXISTS waste_snapshots (
      snapshot_date DATE NOT NULL,
      resource_type VARCHAR(50) NOT NULL,
      total_eur DECIMAL(12, 4) NOT NULL DEFAULT 0,
      resource_count INTEGER NOT NULL DEFAULT 0,
      created_at TIMESTAMP DEFAULT NOW(),
      PRIMARY KEY (snapshot_date, resource_type)
  );

  -- Consommation LLM : une ligne par appel (insights, narratives)
  -- (coût calculé par litellm, voir sql/migrations/llm_usage.sql)
  CREATE TABLE IF NOT EXISTS llm_usage (
      id SERIAL PRIMARY KEY,
      called_at TIMESTAMP NOT NULL DEFAULT NOW(),
      feature VARCHAR(30) NOT NULL,
      model VARCHAR(100),
      prompt_tokens INTEGER,
      completion_tokens INTEGER,
      cost_usd DECIMAL(10, 6)
  );

  -- Briefing quotidien CTO généré par le LLM, caché une ligne par jour
  -- (voir sql/migrations/daily_briefings.sql)
  CREATE TABLE IF NOT EXISTS daily_briefings (
      briefing_date DATE PRIMARY KEY,
      content TEXT NOT NULL,
      model VARCHAR(100),
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
  );

  -- Contraintes d'unicité (nécessaires pour ON CONFLICT upserts)
  DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_waste_resource') THEN
      ALTER TABLE waste_detected ADD CONSTRAINT uq_waste_resource UNIQUE (resource_id, resource_type);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_rec_waste') THEN
      ALTER TABLE recommendations ADD CONSTRAINT uq_rec_waste UNIQUE (waste_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_cloud_costs') THEN
      ALTER TABLE cloud_costs_raw ADD CONSTRAINT uq_cloud_costs UNIQUE (provider, account_id, service, usage_date);
    END IF;
  END $$;

  -- Index pour performance
  CREATE INDEX IF NOT EXISTS idx_costs_raw_date ON cloud_costs_raw(usage_date);
  CREATE INDEX IF NOT EXISTS idx_costs_raw_provider ON cloud_costs_raw(provider);
  CREATE INDEX IF NOT EXISTS idx_waste_date ON waste_detected(detection_date);
  CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations(status);
  CREATE INDEX IF NOT EXISTS idx_llm_usage_called_at ON llm_usage(called_at);

  -- Gaspillage actif : ressource encore existante et non traitée.
  -- Exclut obsolete (ressource disparue), applied/approved (action faite)
  -- et dismissed (l'utilisateur a explicitement choisi de ne plus compter
  -- cet item) ; garde pending et rejected (la ressource coûte toujours,
  -- l'utilisateur peut revenir dessus).
  -- Tous les agrégats Home/Dashboard lisent cette vue.
  CREATE OR REPLACE VIEW active_waste AS
  SELECT w.*
  FROM waste_detected w
  LEFT JOIN recommendations r ON r.waste_id = w.id
  WHERE COALESCE(r.status, 'pending') NOT IN ('obsolete', 'applied', 'approved', 'dismissed');


  -- Base de données séparée pour Metabase
  CREATE DATABASE metabase;