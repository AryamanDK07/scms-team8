
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Complaint, ReferenceSequence


# How long a complainant may reopen a resolved complaint (SCMS-F-019).
REOPEN_WINDOW_DAYS = 7


def generate_reference_id(now=None):
    """Return the next reference ID in the format SCMS-YYYY-NNNNNN.

    SCMS-F-007. The per-year counter is incremented inside a
    transaction with a row lock, so concurrent submissions cannot
    be issued the same number.
    """
    moment = now or timezone.now()
    year = moment.year

    with transaction.atomic():
        sequence, _ = ReferenceSequence.objects.select_for_update(
        ).get_or_create(year=year)
        ReferenceSequence.objects.filter(pk=sequence.pk).update(
            last_number=F("last_number") + 1,
        )
        sequence.refresh_from_db(fields=["last_number"])

    return f"SCMS-{year}-{sequence.last_number:06d}"


def calculate_sla_deadline(category, start_time=None):
    """Return the SLA deadline for a complaint's category.

    SCMS-F-014: the SLA is a property of the category, held as
    master data (SCMS-F-023), not of the priority.
    """
    start = start_time or timezone.now()
    hours = getattr(category, "sla_hours", None) or 48

    return start + timedelta(hours=hours)


def can_reopen(complaint, now=None):
    """Whether a resolved complaint is still within its reopen window.

    SCMS-F-019: a complaint may be reopened once, within 7 days of
    being resolved.
    """
    if complaint.status != Complaint.Status.RESOLVED:
        return False
    if complaint.reopen_count >= 1:
        return False
    if complaint.resolved_at is None:
        return False

    moment = now or timezone.now()
    deadline = complaint.resolved_at + timedelta(days=REOPEN_WINDOW_DAYS)

    return moment <= deadline
