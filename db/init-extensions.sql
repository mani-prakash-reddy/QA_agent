-- Runs once on first init of the data volume (against POSTGRES_DB).
-- JSONB is a built-in Postgres type, nothing to enable.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;

-- AGE needs ag_catalog on the search_path (last, so app tables stay in public) to use cypher() without qualification.
ALTER DATABASE app SET search_path = "$user", public, ag_catalog;
