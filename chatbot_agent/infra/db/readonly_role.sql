-- Least-privilege PostgreSQL role for the chatbot's query gateway (DB_URL).
--
-- The gateway already validates every generated query (single SELECT, allow-listed tables and
-- columns, tenant filter on every table). This role is the second wall: even if that validation
-- were bypassed, the database itself refuses writes and refuses to show secrets.
--
--   * read-only: SELECT only, default_transaction_read_only = on, statement timeout
--   * column-level grants: sensitive columns (password hashes, tokens, client secrets, storage
--     paths, lockout counters) are never granted, so they cannot be read even by raw SQL
--   * denied tables (audit_logs, password_reset_tokens, refresh_tokens) get no grant at all
--   * tenants: only tenants.id (the gateway checks that the tenant exists)
--
-- Run as a superuser/owner against the CloseLoop database. Safe to re-run: it resets the grants
-- first, so run it again after a migration adds tables or columns the chatbot should see.
--
--   docker exec -i closeloop-prod-db-1 sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--       -v ro_password="<password>" -f -' < infra/db/readonly_role.sql
--
-- Then set DB_URL=postgresql://chatbot_ro:<password>@db:5432/<database> in the gateway env.

SELECT 'CREATE ROLE chatbot_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 20'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chatbot_ro') \gexec

ALTER ROLE chatbot_ro WITH PASSWORD :'ro_password';
ALTER ROLE chatbot_ro SET default_transaction_read_only = on;
ALTER ROLE chatbot_ro SET statement_timeout = '10s';
ALTER ROLE chatbot_ro SET idle_in_transaction_session_timeout = '15s';

SELECT format('GRANT CONNECT ON DATABASE %I TO chatbot_ro', current_database()) \gexec
GRANT USAGE ON SCHEMA public TO chatbot_ro;

-- Start from nothing so a column that later becomes sensitive is also revoked on re-run.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM chatbot_ro;

-- Tenant-owned tables: every column except sensitive ones. Mirrors SENSITIVE_NAME / DENIED_TABLES
-- in apps/query_gateway/src/db/schema_catalog.py.
SELECT format(
         'GRANT SELECT (%s) ON public.%I TO chatbot_ro',
         string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum),
         c.relname
       )
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace AND n.nspname = 'public'
JOIN pg_attribute AS a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE c.relkind IN ('r', 'p')
  AND c.relname NOT IN ('audit_logs', 'password_reset_tokens', 'refresh_tokens', 'alembic_version', 'tenants')
  AND c.relname !~* '(password|secret|token|hash|credential|storage_path|failed_login|locked_until|client_id)'
  AND a.attname !~* '(password|secret|token|hash|credential|storage_path|failed_login|locked_until|client_id)'
  AND EXISTS (
        SELECT 1 FROM pg_attribute AS t
        WHERE t.attrelid = c.oid AND t.attname = 'tenant_id' AND NOT t.attisdropped
      )
GROUP BY c.relname \gexec

GRANT SELECT (id) ON public.tenants TO chatbot_ro;
