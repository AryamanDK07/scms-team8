
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Complaint, ComplaintStatusHistory, ReferenceSequence


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


def eligible_agents(complaint):
    """Support Agents who may be given this complaint (SCMS-F-012).

    An agent works their own department's queue, so the candidates
    are the agents of the department the complaint is routed to. An
    unrouted complaint has no candidates: it needs a department
    before it can have an owner.
    """
    from django.contrib.auth import get_user_model

    from .roles import Role

    if complaint.assigned_department_id is None:
        return get_user_model().objects.none()

    return get_user_model().objects.filter(
        is_active=True,
        profile__role=Role.SUPPORT_AGENT,
        profile__department_id=complaint.assigned_department_id,
    ).order_by("username")


def assign_complaint(complaint, agent, actor, reason="", source_ip=None):
    """Give a complaint to an agent and record why (SCMS-F-012).

    A reassignment must carry a reason: taking work off one person
    and giving it to another is the kind of decision an audit trail
    exists to explain. A first assignment may omit it, since there
    is no prior owner to account for.

    The status move and the audit row are written together, so a
    complaint cannot end up reassigned with no record of it.
    """
    is_reassignment = complaint.assigned_to_id is not None

    if is_reassignment and not reason.strip():
        raise ValueError(
            "A reason is required when reassigning a complaint "
            "(SCMS-F-012)."
        )

    previous = complaint.assigned_to
    old_status = complaint.status

    if is_reassignment:
        detail = (
            f"Reassigned from {previous.get_username()} to "
            f"{agent.get_username()}. Reason: {reason.strip()}"
        )
    else:
        detail = f"Assigned to {agent.get_username()}."
        if reason.strip():
            detail = f"{detail} Reason: {reason.strip()}"

    with transaction.atomic():
        complaint.assigned_to = agent

        # Only an untouched complaint advances to Assigned; one
        # already being worked on keeps the status it is in, so a
        # reassignment does not rewind progress.
        if complaint.status == Complaint.Status.OPEN:
            complaint.status = Complaint.Status.ASSIGNED

        complaint.save(update_fields=["assigned_to", "status", "updated_at"])

        ComplaintStatusHistory.objects.create(
            complaint=complaint,
            old_status=old_status,
            new_status=complaint.status,
            changed_by=actor,
            source_ip=source_ip,
            comment=detail,
        )

    return complaint


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
