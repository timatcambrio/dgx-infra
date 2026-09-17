#!/bin/sh
# Postgres init script (runs once, on first container start, against a fresh data
# directory): creates the `kb_index` (owner) and `kb_read` (SELECT-only) roles. The
# database itself is `kb`, already created by POSTGRES_DB. This script is the ONLY place
# roles are created: `kb index --init` (retrieval/db.py:grant_read_role) grants SELECT to
# the read role but never creates it, and stops with the CREATE ROLE statement to run if
# the role is missing. One place creates roles; one place applies the schema.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	DO \$\$
	BEGIN
	  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kb_index') THEN
	    CREATE ROLE kb_index LOGIN PASSWORD '${KB_INDEX_PASSWORD}';
	  END IF;
	  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kb_read') THEN
	    CREATE ROLE kb_read LOGIN PASSWORD '${KB_READ_PASSWORD}';
	  END IF;
	END
	\$\$;

	ALTER DATABASE kb OWNER TO kb_index;
	GRANT ALL PRIVILEGES ON DATABASE kb TO kb_index;
	GRANT CONNECT ON DATABASE kb TO kb_read;
SQL

# A second database for tests/retrieval/ (DATABASE_URL_TEST default points here) so the test
# suite never touches the `kb` database a developer might also be indexing into by hand.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
	-c "SELECT 1 FROM pg_database WHERE datname = 'kb_test'" | grep -q 1 || \
	psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
	-c "CREATE DATABASE kb_test OWNER kb_index"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
	-c "GRANT CONNECT ON DATABASE kb_test TO kb_read"

# pgvector's CREATE EXTENSION needs superuser; do it here so `kb_index` (an ordinary role)
# never has to. `retrieval/schema.sql`'s own `CREATE EXTENSION IF NOT EXISTS vector` then
# no-ops for kb_index -- Postgres's existence check for IF NOT EXISTS runs before the
# privilege check.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "kb" -c "CREATE EXTENSION IF NOT EXISTS vector"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "kb_test" -c "CREATE EXTENSION IF NOT EXISTS vector"
