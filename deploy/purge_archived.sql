-- Purge archived complaints after the retention window
-- (SCMS-NF-007), run by a database administrator.
--
-- READ THIS BEFORE RUNNING IT. The operation is irreversible and
-- it temporarily removes the protection that SCMS-SR-006 relies
-- on.
--
-- WHY A PERSON RUNS THIS AND NOT THE APPLICATION
--
-- SCMS-NF-007 says records are purged after three years.
-- SCMS-SR-006 says the audit log is append-only and the
-- application holds no DELETE on it, enforced by the trigger from
-- migration 0011 and by the privileges in audit_privileges.sql.
-- Complaint rows are additionally PROTECTed by their audit rows.
--
-- Those two requirements cannot both be satisfied by code inside
-- the application. An audit trail the application can erase on a
-- timer is not an audit trail. So the purge lives here: a
-- deliberate, privileged act by a named person, not a scheduled
-- job running quietly at 3am.
--
-- BEFORE YOU RUN IT
--
--   1. python manage.py archive_expired_complaints <path>
--   2. Check the archive: it should hold the same count the
--      command reported, and you should be able to open it.
--   3. Store the archive somewhere durable and off this host.
--      After step 5 below it is the only copy.
--
-- Then run this file inside a single transaction, as the schema
-- owner (scms_migrate, not scms_app).

\set retention_years 3

BEGIN;

-- 1. What is about to go. Read this list before continuing.
SELECT reference_id, created_at, status
FROM complaints_complaint
WHERE created_at <= now() - (:retention_years || ' years')::interval
ORDER BY created_at;

-- 2. Lift the append-only guard. This is the dangerous moment:
--    between here and step 4 the audit trail is deletable. Do not
--    leave the transaction open, and do not run anything else
--    against this database meanwhile.
DROP TRIGGER IF EXISTS complaints_complaintstatushistory_append_only
    ON complaints_complaintstatushistory;

-- 3. Delete children first, then the complaints themselves. The
--    order matters: every one of these is a PROTECT relation, so
--    a complaint cannot go before its rows do.
CREATE TEMP TABLE expiring AS
SELECT id FROM complaints_complaint
WHERE created_at <= now() - (:retention_years || ' years')::interval;

DELETE FROM complaints_complaintstatushistory
    WHERE complaint_id IN (SELECT id FROM expiring);
DELETE FROM complaints_complaintcomment
    WHERE complaint_id IN (SELECT id FROM expiring);
DELETE FROM complaints_complaintattachment
    WHERE complaint_id IN (SELECT id FROM expiring);
DELETE FROM complaints_complaintrating
    WHERE complaint_id IN (SELECT id FROM expiring);
DELETE FROM complaints_duplicateflag
    WHERE complaint_id IN (SELECT id FROM expiring)
       OR similar_to_id IN (SELECT id FROM expiring);
DELETE FROM complaints_complaint
    WHERE id IN (SELECT id FROM expiring);

DROP TABLE expiring;

-- 4. Put the guard back. If anything above failed, ROLLBACK
--    instead: that restores the trigger along with the rows.
CREATE TRIGGER complaints_complaintstatushistory_append_only
BEFORE UPDATE OR DELETE ON complaints_complaintstatushistory
FOR EACH ROW EXECUTE FUNCTION scms_audit_append_only();

-- 5. Confirm the trigger is back BEFORE committing. One row
--    expected. If this returns nothing, ROLLBACK.
SELECT tgname FROM pg_trigger
WHERE tgname = 'complaints_complaintstatushistory_append_only';

COMMIT;

-- Uploaded evidence files are not touched by this script. The
-- rows that named them are gone, so the files under MEDIA_ROOT
-- are now orphaned and must be removed separately, using the
-- stored_as paths recorded in the archive.
