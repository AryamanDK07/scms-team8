"""Close resolved complaints whose reopen window has passed.

SCMS-F-019. Intended to run on a schedule -- cron, or Celery Beat
once SCMS-F-021 brings Celery in. Running it more often than once
a day costs nothing: a complaint already closed is no longer
Resolved, so it is not picked up twice.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from complaints.models import Complaint
from complaints.services import (
    REOPEN_WINDOW_DAYS,
    close_expired_complaints,
)


class Command(BaseCommand):
    help = (
        "Close resolved complaints whose "
        f"{REOPEN_WINDOW_DAYS}-day reopen window has passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be closed without closing it.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            cutoff = timezone.now() - timedelta(days=REOPEN_WINDOW_DAYS)
            pending = Complaint.objects.filter(
                status=Complaint.Status.RESOLVED,
                resolved_at__lte=cutoff,
            )

            for complaint in pending:
                self.stdout.write(
                    f"would close {complaint.reference_id} "
                    f"(resolved {complaint.resolved_at:%Y-%m-%d})"
                )

            self.stdout.write(
                self.style.WARNING(f"{pending.count()} would be closed")
            )

            return

        closed = close_expired_complaints()

        self.stdout.write(
            self.style.SUCCESS(f"{closed} complaint(s) closed")
        )
