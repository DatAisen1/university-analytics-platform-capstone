-- warehouse/ddl/001_create_schemas.sql
--
-- Schema separation per docs/06_Data_Warehouse.md Section 2: Bronze,
-- Silver, Gold, marts, and meta each get their own Postgres schema
-- (not just a naming convention) specifically so permissions can be
-- granted per-layer, not just documented as a convention someone could
-- accidentally violate.

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS meta;
