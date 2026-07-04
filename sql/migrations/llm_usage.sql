-- Consommation LLM : une ligne par appel (insights, narratives).
-- Le coût est calculé par litellm (table de prix par modèle) et stocké en USD ;
-- l'affichage convertit en EUR à taux fixe (USD_TO_EUR, défaut 0.92) comme
-- les prix AWS des détecteurs. cost_usd est NULL quand litellm ne connaît
-- pas le modèle (modèle local, custom) ; les tokens restent loggés.
--
-- Application :
--   docker exec -i wasteless-postgres psql -U wasteless -d wasteless < sql/migrations/llm_usage.sql

CREATE TABLE IF NOT EXISTS llm_usage (
    id SERIAL PRIMARY KEY,
    called_at TIMESTAMP NOT NULL DEFAULT NOW(),
    feature VARCHAR(30) NOT NULL,
    model VARCHAR(100),
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd DECIMAL(10, 6)
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_called_at ON llm_usage(called_at);
