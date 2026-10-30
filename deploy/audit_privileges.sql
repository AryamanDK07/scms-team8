-- Database privileges for the SCMS application role (SCMS-SR-006).
--
-- Run once, as a superuser, after the first `manage.py migrate`.
-- Re-running it is harmless.
--
-- WHY THIS IS NOT ENOUGH ON ITS OWN, AND WHY IT IS STILL HERE
--
-- Migration 0011 puts a trigger on each audit table that refuses
-- UPDATE and DELETE from any connection. That is the enforcement the
-- test suite proves, and it is strictly stronger than a privilege
-- grant, because a superuser is exempt from privileges but not from
-- a trigger.
--
-- SCMS-SR-006 nonetheless states the privilege requirement
-- explicitly: "the application database account shall hold no UPDATE
-- or DELETE privilege on the audit table". This script is that half.
-- Defence in depth is the honest reason to keep both: a trigger can
-- be dropped by whoever owns the table, and the privileges are what
-- stop the application's own credentials from doing so.
--
-- THE OWNERSHIP TRAP
--
-- Revoking a privilege from the role that OWNS a table achieves
-- nothing: the owner can grant it straight back, and so can anyone
-- who gains the owner's credentials. If the application role runs
-- migrations, it owns every table it created, and this script is
-- theatre.
--
-- So two roles are needed. scms_migrate owns the schema and runs
-- migrations during deployment. scms_app is what the running
-- application connects as, and owns nothing.
--
--     DB_USER=scms_app        in the application's .env
--     DB_USER=scms_migrate    only while running migrate
--
-- If your deployment runs migrations as the application role --
-- which is common, and is what a single-role setup does -- then say
-- so plainly in the deployment notes rather than claiming
-- SCMS-SR-006 is met by this file. The trigger still holds; the
-- privilege half does not.

\set audit_history complaints_complaintstatushistory
\set audit_security complaints_securityevent

-- 1. The roles. Replace the passwords before running this.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scms_migrate') THEN
        CREATE ROLE scms_migrate LOGIN PASSWORD 'change-me-before-running';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scms_app') THEN
        CREATE ROLE scms_app LOGIN PASSWORD 'change-me-before-running';
    END IF;
END
$$;

-- 2. scms_migrate owns the schema and everything already in it.
ALTER SCHEMA public OWNER TO scms_migrate;

DO $$
DECLARE
    item text;
BEGIN
    FOR item IN
        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    LOOP
        EXECUTE format(
            'ALTER TABLE public.%I OWNER TO scms_migrate', item
        );
    END LOOP;
END
$$;

-- 3. scms_app may read and write ordinary tables.
GRANT USAGE ON SCHEMA public TO scms_app;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public TO scms_app;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO scms_app;

-- 4. The audit tables are the exception: append and read only.
--    This is the line SCMS-SR-006 asks for.
REVOKE UPDATE, DELETE ON complaints_complaintstatushistory FROM scms_app;
REVOKE UPDATE, DELETE ON complaints_securityevent FROM scms_app;

-- 5. The same rules for tables added by future migrations, so a new
--    table does not quietly arrive with no grant at all.
ALTER DEFAULT PRIVILEGES FOR ROLE scms_migrate IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO scms_app;

ALTER DEFAULT PRIVILEGES FOR ROLE scms_migrate IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO scms_app;

-- A new audit table would need its own REVOKE added above. There is
-- no way to express "except audit tables" in a default privilege, so
-- this is a step for whoever adds one, not something the database
-- will remember on their behalf.

-- 6. Check the result. Both queries should return no rows.
SELECT table_name, privilege_type
FROM information_schema.table_privileges
WHERE grantee = 'scms_app'
  AND privilege_type IN ('UPDATE', 'DELETE')
  AND table_name IN (
      'complaints_complaintstatushistory',
      'complaints_securityevent'
  );
