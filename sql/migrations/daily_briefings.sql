-- Briefing quotidien CTO : une ligne par jour, générée par le LLM
-- (src/reports/daily_briefing.py). Le contenu est du commentaire IA sur
-- des chiffres calculés en SQL ; il est caché ici pour n'appeler le LLM
-- qu'une fois par jour, quel que soit le nombre de chargements de page.
--
-- Application :
--   docker exec -i wasteless-postgres psql -U wasteless -d wasteless < sql/migrations/daily_briefings.sql

CREATE TABLE IF NOT EXISTS daily_briefings (
    briefing_date DATE PRIMARY KEY,
    content TEXT NOT NULL,
    model VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
