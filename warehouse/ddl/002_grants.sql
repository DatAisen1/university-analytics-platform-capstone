-- warehouse/ddl/002_grants.sql
--
-- Scoped, per-layer permissions for the four service roles this project
-- uses (see docs/06_Data_Warehouse.md Section 5). Roles themselves are
-- created separately (pipelines/common/postgres.py's bootstrap_roles),
-- with passwords injected from environment variables -- never hardcoded
-- in this file, since this file IS meant to be version-controlled and
-- passwords are not.
--
-- Run this AFTER 001_create_schemas.sql and AFTER the four roles exist.
--
-- The single guarantee this file exists to enforce: dashboard_reader and
-- analyst_readonly get NO grant of any kind on bronze/silver/meta --
-- not even USAGE. Without USAGE on a schema, Postgres denies a role
-- visibility into that schema's objects at all, before SELECT
-- permissions even become a question. That's what makes "the dashboard
-- cannot read Silver or Bronze" enforced by the database itself, not
-- just a convention documented in a doc nobody re-reads.

-- ---------------------------------------------------------------------
-- pipeline_writer: full read/write on bronze, silver, gold
-- ---------------------------------------------------------------------
GRANT USAGE, CREATE ON SCHEMA bronze, silver, gold TO pipeline_writer;
GRANT ALL ON ALL TABLES IN SCHEMA bronze, silver, gold TO pipeline_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA bronze GRANT ALL ON TABLES TO pipeline_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver GRANT ALL ON TABLES TO pipeline_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT ALL ON TABLES TO pipeline_writer;

-- ---------------------------------------------------------------------
-- dbt_role: read Gold, read/write marts (dbt builds marts FROM Gold)
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA gold TO dbt_role;
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO dbt_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO dbt_role;

GRANT USAGE, CREATE ON SCHEMA marts TO dbt_role;
GRANT ALL ON ALL TABLES IN SCHEMA marts TO dbt_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT ALL ON TABLES TO dbt_role;

-- ---------------------------------------------------------------------
-- dashboard_reader: read-only on gold + marts. NOTHING on bronze/silver/meta.
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA gold, marts TO dashboard_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA gold, marts TO dashboard_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO dashboard_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO dashboard_reader;

-- ---------------------------------------------------------------------
-- analyst_readonly: read-only on marts ONLY (narrower than dashboard_reader --
-- an ad hoc analyst gets the curated marts, not raw Gold facts/dimensions)
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA marts TO analyst_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA marts TO analyst_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO analyst_readonly;

-- ---------------------------------------------------------------------
-- Default privileges scoped to the roles that actually CREATE the tables
-- in production, not just whichever admin ran this DDL. Without these,
-- a Gold table created by pipeline_writer (the real pipeline's role, once
-- migrated to Postgres) would NOT automatically grant dbt_role/
-- dashboard_reader read access -- only tables created by the role that
-- issued the plain ALTER DEFAULT PRIVILEGES above would. Setting default
-- privileges FOR a specific role requires either that role itself or a
-- superuser to run it -- done here once, by the admin bootstrap, so it
-- never has to be re-run as new tables get created.
-- ---------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES FOR ROLE pipeline_writer IN SCHEMA gold
    GRANT SELECT ON TABLES TO dbt_role, dashboard_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE dbt_role IN SCHEMA marts
    GRANT SELECT ON TABLES TO dashboard_reader, analyst_readonly;
