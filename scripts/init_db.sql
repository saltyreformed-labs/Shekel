-- Shekel Budget App -- Database Schema Initialization
--
-- Schema-only bootstrap.  Idempotent (every CREATE uses IF NOT
-- EXISTS) so it is safe to run multiple times -- once by Postgres'
-- /docker-entrypoint-initdb.d on the first volume creation in the
-- dev compose, and again by the deploy (scripts/deploy.sh) as the
-- database owner, shekel_owner, on every deploy.
--
-- The web login's grants (the least-privilege ``shekel_app`` role from
-- audit finding F-081 / Commit C-13) live in init_db_role.sql, which
-- the deploy runs next; both logins are made in the database container
-- (deploy/postgres/provision_roles.sh).

CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS budget;
CREATE SCHEMA IF NOT EXISTS salary;
CREATE SCHEMA IF NOT EXISTS system;

-- Alembic migration tracking table.
-- Pre-created to avoid transactional DDL issues with include_schemas.
CREATE TABLE IF NOT EXISTS public.alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
