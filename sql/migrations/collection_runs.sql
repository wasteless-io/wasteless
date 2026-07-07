-- Un run "wasteless.sh collect" par ligne : quand steampipe est absent, les
-- steps 7-10 (elb_unused, nat_gateway_unused, vpc_unused, ebs_gp2_migration)
-- sont sautés avec un warning écrit uniquement dans ~/.wasteless.log --
-- invisible depuis l'UI (/logs ne capture que le process uvicorn). Cette
-- table permet d'afficher un bandeau "dernière collecte partielle" au lieu
-- de laisser croire que la collecte a couvert les 10 détecteurs.
CREATE TABLE IF NOT EXISTS collection_runs (
    id SERIAL PRIMARY KEY,
    ran_at TIMESTAMP NOT NULL DEFAULT NOW(),
    full_run BOOLEAN NOT NULL,
    skipped_steps TEXT[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_collection_runs_ran_at ON collection_runs(ran_at DESC);
