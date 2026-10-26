
from datetime import timedelta

from django.utils import timezone

from .models import Complaint


# Prototype SLA rules, measured in elapsed hours.
# Replace these with the values specified in your SRS.
SLA_HOURS_BY_PRIORITY = {
    Complaint.Priority.URGENT: 4,
    Complaint.Priority.HIGH: 24,
    Complaint.Priority.MEDIUM: 48,
    Complaint.Priority.LOW: 72,
}


def calculate_sla_deadline(priority, start_time=None):
    """Return the SLA deadline for a complaint's priority."""
    start = start_time or timezone.now()
    hours = SLA_HOURS_BY_PRIORITY.get(priority, 48)

    return start + timedelta(hours=hours)
