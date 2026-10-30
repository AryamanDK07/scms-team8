"""Enforce the append-only audit trail in the database (SCMS-SR-006).

T8-34 made the audit tables append-only in the application: the
model refuses save() and delete() on an existing row, and the
queryset refuses bulk update() and delete(). That is worth having,
but it only binds code that goes through Django. Anything holding
the database credentials -- a psql session, a script, a compromised
service -- could still rewrite the trail.

These triggers refuse UPDATE and DELETE on the audit tables for
every connection, whatever role it uses and whatever client it
comes from. A trigger is stronger than the privilege grant the
requirement describes, because a superuser is not exempt from it
while it bypasses privileges entirely. The privilege half is still
configured at deployment time; deploy/audit_privileges.sql holds
that script and explains why the application role must not own the
tables.

PostgreSQL only. SQLite is the local development convenience
described in config.settings_local, not the deployment target
named in SCMS-NF-006, and is left to the application-level guard:
a SQLite trigger would also fire during Django's flush between
TransactionTestCase cases, which deletes rather than truncates,
and would break the suite rather than protect anything real.
"""

from django.db import migrations

AUDIT_TABLES = (
    "complaints_complaintstatushistory",
    "complaints_securityevent",
)

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION scms_audit_append_only()
RETURNS trigger AS $scms$
BEGIN
    RAISE EXCEPTION
        'Audit rows are append-only and cannot be % (SCMS-SR-006).',
        CASE TG_OP WHEN 'UPDATE' THEN 'modified' ELSE 'deleted' END
        USING ERRCODE = 'restrict_violation';
END;
$scms$ LANGUAGE plpgsql;
"""

CREATE_TRIGGER = """
CREATE TRIGGER {table}_append_only
BEFORE UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION scms_audit_append_only();
"""

DROP_TRIGGER = "DROP TRIGGER IF EXISTS {table}_append_only ON {table};"

DROP_FUNCTION = "DROP FUNCTION IF EXISTS scms_audit_append_only();"


def apply_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    # params=None matters. schema_editor.execute() defaults to
    # params=(), which hands the statement to the driver for
    # placeholder substitution, and the RAISE EXCEPTION format string
    # below contains a '%'. With the default this migration fails on
    # an unsupported format character instead of creating anything.
    schema_editor.execute(CREATE_FUNCTION, params=None)

    for table in AUDIT_TABLES:
        schema_editor.execute(
            CREATE_TRIGGER.format(table=table), params=None,
        )


def remove_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    for table in AUDIT_TABLES:
        schema_editor.execute(
            DROP_TRIGGER.format(table=table), params=None,
        )

    schema_editor.execute(DROP_FUNCTION, params=None)


class Migration(migrations.Migration):

    dependencies = [
        ("complaints", "0010_account_lockout"),
    ]

    operations = [
        migrations.RunPython(apply_guard, remove_guard),
    ]
