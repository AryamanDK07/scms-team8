"""Archive complaints past the retention window (SCMS-NF-007).

Archives only. The purge is deploy/purge_archived.sql, run by a
database administrator, because the application holds no DELETE
on the audit tables (SCMS-SR-006) and must not.
"""

from django.core.management.base import BaseCommand, CommandError

from complaints.retention import (
    RETENTION_YEARS,
    archive_expired,
    expired_complaints,
)


class Command(BaseCommand):
    help = (
        f"Write complaints older than {RETENTION_YEARS} years to a "
        f"JSON archive. Deletes nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "destination",
            help="Path of the JSON archive to write.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count what would be archived, without writing.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            count = expired_complaints().count()
            self.stdout.write(f"{count} complaint(s) are past retention")

            return

        try:
            count, path = archive_expired(options["destination"])
        except RuntimeError as problem:
            raise CommandError(str(problem))

        self.stdout.write(
            self.style.SUCCESS(f"{count} complaint(s) archived to {path}")
        )
        self.stdout.write(
            "Nothing was deleted. The purge is a database "
            "administration step: see deploy/purge_archived.sql, and "
            "do not run it until this archive has been checked and "
            "stored somewhere durable."
        )
