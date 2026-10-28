
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import (
    Complaint,
    ComplaintComment,
    ComplaintStatusHistory,
    ReferenceSequence,
)


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


def priority_for(category):
    """The category's default priority (SCMS-F-013).

    The priority matrix lives on the Category row as master data
    (SCMS-F-023), so changing how a category is triaged needs no
    code change. A category with nothing set falls back to Medium
    rather than leaving the complaint without a priority.
    """
    return (
        getattr(category, "default_priority", None)
        or Complaint.Priority.MEDIUM
    )


def override_priority(
    complaint, priority, actor, reason, source_ip=None,
):
    """Depart from the matrix priority, with the change recorded.

    SCMS-F-013 requires the old and the new value to be logged. The
    reason is always mandatory: every override is a deliberate
    departure from the configured matrix and needs to say why.
    """
    if not reason.strip():
        raise ValueError(
            "A reason is required when overriding priority "
            "(SCMS-F-013)."
        )

    if priority == complaint.priority:
        raise ValueError(
            "The complaint already holds that priority."
        )

    labels = dict(Complaint.Priority.choices)
    old_priority = complaint.priority

    with transaction.atomic():
        complaint.priority = priority
        complaint.save(update_fields=["priority", "updated_at"])

        # The audit table is shaped around status transitions, so a
        # priority change carries the same status on both sides and
        # states the before/after values in the comment. Generalising
        # the audit row to arbitrary field changes belongs to T8-34.
        ComplaintStatusHistory.objects.create(
            complaint=complaint,
            old_status=complaint.status,
            new_status=complaint.status,
            changed_by=actor,
            source_ip=source_ip,
            comment=(
                f"Priority changed from "
                f"{labels.get(old_priority, old_priority)} to "
                f"{labels.get(priority, priority)}. "
                f"Reason: {reason.strip()}"
            ),
        )

    return complaint


#: The legal status transitions (SCMS-F-016).
#:
#: Closed is terminal. Resolved can only go on to Closed or
#: Reopened, which is what gives the 7-day reopen window in
#: SCMS-F-019 something to apply to.
STATUS_TRANSITIONS = {
    Complaint.Status.OPEN: (
        Complaint.Status.ASSIGNED,
    ),
    Complaint.Status.ASSIGNED: (
        Complaint.Status.IN_PROGRESS,
    ),
    Complaint.Status.IN_PROGRESS: (
        Complaint.Status.PENDING_INFORMATION,
        Complaint.Status.RESOLVED,
    ),
    Complaint.Status.PENDING_INFORMATION: (
        Complaint.Status.IN_PROGRESS,
    ),
    Complaint.Status.RESOLVED: (
        Complaint.Status.CLOSED,
        Complaint.Status.REOPENED,
    ),
    Complaint.Status.REOPENED: (
        Complaint.Status.IN_PROGRESS,
    ),
    Complaint.Status.CLOSED: (),
}

#: Statuses that another requirement owns the entry conditions for.
#:
#: Resolved needs remarks and proof (SCMS-F-018) and Closed follows
#: from it, so neither may be reached through the generic
#: transition. Letting them through here would be a way to mark a
#: complaint resolved with no evidence at all.
GUARDED_STATUSES = (
    Complaint.Status.RESOLVED,
    Complaint.Status.CLOSED,
)


def allowed_transitions(complaint):
    """Statuses this complaint may move to next (SCMS-F-016)."""
    return STATUS_TRANSITIONS.get(complaint.status, ())


def offerable_transitions(complaint):
    """Transitions the generic status change may offer.

    The guarded statuses are excluded: they have their own flows
    with their own preconditions.
    """
    return tuple(
        status
        for status in allowed_transitions(complaint)
        if status not in GUARDED_STATUSES
    )


def transition_status(
    complaint, new_status, actor, comment="", source_ip=None,
):
    """Move a complaint to a new status, recording the transition.

    SCMS-F-016. An illegal move raises ValueError rather than being
    silently written, so the status column cannot drift out of the
    state machine the SRS describes.
    """
    if new_status == complaint.status:
        raise ValueError(
            f"The complaint is already "
            f"{complaint.get_status_display()}."
        )

    if new_status in GUARDED_STATUSES:
        raise ValueError(
            f"{new_status} has its own flow with preconditions and "
            f"cannot be set through a generic status change."
        )

    if new_status not in allowed_transitions(complaint):
        labels = dict(Complaint.Status.choices)
        raise ValueError(
            f"{complaint.get_status_display()} cannot move to "
            f"{labels.get(new_status, new_status)}."
        )

    old_status = complaint.status
    labels = dict(Complaint.Status.choices)

    detail = (
        f"Status changed from {labels.get(old_status, old_status)} "
        f"to {labels.get(new_status, new_status)}."
    )
    if comment.strip():
        detail = f"{detail} {comment.strip()}"

    with transaction.atomic():
        complaint.status = new_status
        complaint.save(update_fields=["status", "updated_at"])

        ComplaintStatusHistory.objects.create(
            complaint=complaint,
            old_status=old_status,
            new_status=new_status,
            changed_by=actor,
            source_ip=source_ip,
            comment=detail,
        )

    return complaint


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


#: Minimum length of resolution remarks (SCMS-F-018).
RESOLUTION_REMARKS_MIN = 50


def resolve_complaint(
    complaint, actor, remarks, proof_uploads=(), source_ip=None,
):
    """Mark a complaint resolved, with remarks and proof.

    SCMS-F-018 requires both, which is why T8-23 guards Resolved
    against the generic status change: this is the only route to it,
    and it cannot be taken without the evidence.

    Proof files are stored as attachments of kind PROOF, counted
    separately from the complainant's evidence allowance under
    SCMS-F-008. Sharing one allowance would let a complainant who
    attached three files block their own complaint from ever being
    resolved.
    """
    from .attachments import AttachmentRejected, validate_upload
    from .models import AttachmentKind, ComplaintAttachment

    if Complaint.Status.RESOLVED not in allowed_transitions(complaint):
        raise ValueError(
            f"A complaint that is "
            f"{complaint.get_status_display()} cannot be resolved."
        )

    text = (remarks or "").strip()

    if len(text) < RESOLUTION_REMARKS_MIN:
        raise ValueError(
            f"Resolution remarks must be at least "
            f"{RESOLUTION_REMARKS_MIN} characters. "
            f"They are currently {len(text)}."
        )

    uploads = list(proof_uploads)
    already_held = complaint.attachments.filter(
        kind=AttachmentKind.PROOF,
    ).count()

    if not uploads and already_held == 0:
        raise ValueError(
            "At least one proof file is required before a complaint "
            "can be resolved."
        )

    # Validate every upload before writing anything, so a rejected
    # second file does not leave the first one stored against a
    # complaint that was never resolved.
    checked = []
    for index, upload in enumerate(uploads):
        try:
            label = validate_upload(upload, already_held + index)
        except AttachmentRejected as rejection:
            raise ValueError(str(rejection))

        checked.append((upload, label))

    from .attachments import canonical_content_type

    old_status = complaint.status

    with transaction.atomic():
        for upload, label in checked:
            ComplaintAttachment.objects.create(
                complaint=complaint,
                kind=AttachmentKind.PROOF,
                file=upload,
                original_name=upload.name[:255],
                content_type=canonical_content_type(label),
                size_bytes=upload.size,
                uploaded_by=actor,
            )

        complaint.status = Complaint.Status.RESOLVED
        complaint.resolution_remarks = text
        complaint.resolved_at = timezone.now()
        complaint.save(update_fields=[
            "status", "resolution_remarks", "resolved_at", "updated_at",
        ])

        ComplaintStatusHistory.objects.create(
            complaint=complaint,
            old_status=old_status,
            new_status=Complaint.Status.RESOLVED,
            changed_by=actor,
            source_ip=source_ip,
            comment=f"Resolved. {text}",
        )

    return complaint


def apply_complaint_filters(queryset, filters):
    """Narrow a complaint queryset by the search criteria.

    SCMS-F-028. This takes a queryset rather than building its own,
    so it can only ever narrow what the caller already scoped by
    role. Searching must not be a way around SCMS-SR-003: an agent
    filtering by department cannot reach another department's work,
    because the role scoping was applied before this runs.
    """
    reference_id = (filters.get("reference_id") or "").strip()
    if reference_id:
        queryset = queryset.filter(reference_id__icontains=reference_id)

    status = filters.get("status")
    if status:
        queryset = queryset.filter(status=status)

    category = filters.get("category")
    if category:
        queryset = queryset.filter(category=category)

    department = filters.get("department")
    if department:
        queryset = queryset.filter(assigned_department=department)

    lodged_from = filters.get("lodged_from")
    if lodged_from:
        queryset = queryset.filter(created_at__date__gte=lodged_from)

    lodged_to = filters.get("lodged_to")
    if lodged_to:
        queryset = queryset.filter(created_at__date__lte=lodged_to)

    keyword = (filters.get("q") or "").strip()
    if keyword:
        queryset = queryset.filter(
            Q(title__icontains=keyword)
            | Q(description__icontains=keyword)
            | Q(location__icontains=keyword)
            | Q(reference_id__icontains=keyword)
        )

    return queryset


def add_comment(complaint, author, body, parent=None):
    """Post a comment or a reply on a complaint (SCMS-F-017).

    The parent checks matter: a reply carries its parent's id from
    the form, so without them a client could attach a reply to a
    comment on someone else's complaint and have it surface there.
    """
    text = (body or "").strip()

    if not text:
        raise ValueError("A comment cannot be empty.")

    if len(text) > ComplaintComment.BODY_LIMIT:
        raise ValueError(
            f"Keep a comment to {ComplaintComment.BODY_LIMIT} "
            f"characters or fewer."
        )

    if parent is not None:
        if parent.complaint_id != complaint.pk:
            raise ValueError(
                "That comment belongs to a different complaint."
            )

        if parent.parent_id is not None:
            raise ValueError(
                "Replies go on the original comment, not on another "
                "reply."
            )

    return ComplaintComment.objects.create(
        complaint=complaint,
        parent=parent,
        author=author,
        body=text,
    )


def comment_thread(complaint):
    """Top-level comments with their replies, oldest first."""
    return (
        complaint.comments
        .filter(parent__isnull=True)
        .select_related("author")
        .prefetch_related("replies__author")
        .order_by("created_at")
    )


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
