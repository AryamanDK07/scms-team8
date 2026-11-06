"""Delete complaint drafts untouched for the retention window.

SCMS-F-010. Intended to run on a schedule, like
close_expired_complaints.
"""

from django.core.management.base import BaseCommand

from complaints.services import DRAFT_RETENTION_DAYS, purge_expired_drafts


class Command(BaseCommand):
    help = (
        f"Delete drafts untouched for {DRAFT_RETENTION_DAYS} days."
    )

    def handle(self, *args, **options):
        deleted = purge_expired_drafts()

        self.stdout.write(
            self.style.SUCCESS(f"{deleted} draft(s) purged")
        )
