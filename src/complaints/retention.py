"""Three-year retention, archive and purge (SCMS-NF-007).

A CONFLICT BETWEEN TWO REQUIREMENTS, which this module resolves
rather than hides.

SCMS-NF-007 says records older than three years are archived and
removed from the live table. SCMS-SR-006 says the audit log is
append-only and the application holds no DELETE on it -- enforced
in the model, in the queryset, and by a database trigger added in
T8-48. Complaint rows are additionally PROTECTed by their own
audit rows.

So the application cannot purge. Not "does not": cannot, and that
is the correct behaviour. An audit trail the application can
erase on a timer is not an audit trail, and a purge path inside
the application would be exactly the capability SCMS-SR-006
exists to deny.

The resolution is to split the two halves by privilege:

* The application ARCHIVES. archive_expired() writes every
  expired complaint and all of its related rows to a JSON file,
  verifies the file, and deletes nothing.
* A DATABASE ADMINISTRATOR PURGES, using deploy/purge_archived.sql,
  which drops the trigger, deletes the archived rows, and puts the
  trigger back. That is a deliberate, privileged, auditable act by
  a person, not something a scheduled job does quietly.

The team should record this in the SRS: NF-007's "purge" is a
database-administration procedure, not an application feature.
Until that is written down, the two requirements read as
contradictory and whoever implements the purge next will either
weaken SR-006 or give up on NF-007.
"""

import json
from datetime import timedelta
from pathlib import Path

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from .models import (
    Complaint,
    ComplaintAttachment,
    ComplaintComment,
    ComplaintRating,
    ComplaintStatusHistory,
)

#: How long records are kept before they may be archived.
RETENTION_YEARS = 3
RETENTION_DAYS = RETENTION_YEARS * 365


def expired_complaints(now=None):
    """Complaints past the retention window, oldest first.

    Measured from created_at: the requirement is about how long a
    record is kept, which runs from when it came into existence,
    not from when it was last touched.
    """
    moment = now or timezone.now()
    cutoff = moment - timedelta(days=RETENTION_DAYS)

    return Complaint.objects.filter(
        created_at__lte=cutoff,
    ).order_by("created_at")


def _serialise(complaint):
    """One complaint and everything that hangs off it."""
    return {
        "reference_id": complaint.reference_id,
        "title": complaint.title,
        "description": complaint.description,
        "location": complaint.location,
        "status": complaint.status,
        "priority": complaint.priority,
        "category": complaint.category.name if complaint.category else None,
        "department": (
            complaint.assigned_department.name
            if complaint.assigned_department else None
        ),
        "submitted_by": (
            complaint.submitted_by.get_username()
            if complaint.submitted_by else None
        ),
        "assigned_to": (
            complaint.assigned_to.get_username()
            if complaint.assigned_to else None
        ),
        "created_at": complaint.created_at,
        "resolved_at": complaint.resolved_at,
        "resolution_remarks": complaint.resolution_remarks,
        "audit_trail": [
            {
                "field": entry.field_name,
                "old_value": entry.old_value,
                "new_value": entry.new_value,
                "changed_by": (
                    entry.changed_by.get_username()
                    if entry.changed_by else None
                ),
                "source_ip": entry.source_ip,
                "comment": entry.comment,
                "created_at": entry.created_at,
            }
            for entry in ComplaintStatusHistory.objects.filter(
                complaint=complaint,
            ).order_by("created_at")
        ],
        "comments": [
            {
                "author": comment.author.get_username(),
                "body": comment.body,
                "created_at": comment.created_at,
            }
            for comment in ComplaintComment.objects.filter(
                complaint=complaint,
            ).order_by("created_at")
        ],
        "attachments": [
            {
                "original_name": attachment.original_name,
                "kind": attachment.kind,
                "size_bytes": attachment.size_bytes,
                "stored_as": attachment.file.name,
                "uploaded_at": attachment.uploaded_at,
            }
            for attachment in ComplaintAttachment.objects.filter(
                complaint=complaint,
            )
        ],
        "rating": (
            {
                "score": complaint.rating.score,
                "comment": complaint.rating.comment,
                "created_at": complaint.rating.created_at,
            }
            if ComplaintRating.objects.filter(complaint=complaint).exists()
            else None
        ),
    }


def archive_expired(destination, now=None):
    """Write expired records to a JSON file. Deletes nothing.

    Returns (count, path). The file is written whole and then read
    back and parsed before the count is returned, so a caller is
    never told records are archived when the archive is truncated
    or unreadable -- which is the failure that would matter, since
    the purge that follows is irreversible.
    """
    complaints = list(expired_complaints(now=now))
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "archived_at": timezone.now(),
        "retention_years": RETENTION_YEARS,
        "count": len(complaints),
        "complaints": [_serialise(c) for c in complaints],
    }

    path.write_text(
        json.dumps(payload, cls=DjangoJSONEncoder, indent=2),
        encoding="utf-8",
    )

    verified = json.loads(path.read_text(encoding="utf-8"))

    if verified["count"] != len(complaints):
        raise RuntimeError(
            f"Archive at {path} holds {verified['count']} records but "
            f"{len(complaints)} were expected. Nothing has been "
            f"deleted; do not run the purge."
        )

    return len(complaints), path


def purge_is_available_to_the_application():
    """Whether the application could carry out the purge itself.

    Always False, and deliberately so. Kept as a function rather
    than a comment because a test asserts it, so the day someone
    gives the application a delete path for audit rows, that test
    fails and asks them to justify it against SCMS-SR-006.
    """
    return False
