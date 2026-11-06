
from datetime import timedelta
from difflib import SequenceMatcher

from django.db import transaction
from django.db.models import Avg, Count, F, Q
from django.utils import timezone

from .models import (
    Complaint,
    ComplaintDraft,
    ComplaintPriority,
    ComplaintComment,
    ComplaintRating,
    ComplaintStatusHistory,
    DuplicateFlag,
    DuplicateVerdict,
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

        ComplaintStatusHistory.objects.create(
            complaint=complaint,
            # The status is unchanged by a priority override; the
            # change itself is carried by the field_name/old_value/
            # new_value triple (SCMS-F-027).
            old_status=complaint.status,
            new_status=complaint.status,
            field_name="priority",
            old_value=labels.get(old_priority, old_priority),
            new_value=labels.get(priority, priority),
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
            field_name="status",
            old_value=labels.get(old_status, old_status),
            new_value=labels.get(new_status, new_status),
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
            field_name="assigned_to",
            old_value=(
                previous.get_username() if previous else ""
            ),
            new_value=agent.get_username(),
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

        status_labels = dict(Complaint.Status.choices)

        ComplaintStatusHistory.objects.create(
            complaint=complaint,
            old_status=old_status,
            new_status=Complaint.Status.RESOLVED,
            field_name="status",
            old_value=status_labels.get(old_status, old_status),
            new_value=status_labels[Complaint.Status.RESOLVED],
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


def can_rate(complaint, user):
    """Whether this user may rate this complaint now (SCMS-F-020).

    The complainant who lodged it, once it is closed, and only if
    nobody has rated it yet. Staff do not rate their own work.
    """
    if complaint.status != Complaint.Status.CLOSED:
        return False

    if complaint.submitted_by_id != getattr(user, "id", None):
        return False

    return not ComplaintRating.objects.filter(complaint=complaint).exists()


def rate_complaint(complaint, actor, score, comment=""):
    """Record the complainant's rating of a closed complaint.

    SCMS-F-020. Raises ValueError with a reason the caller can show,
    rather than returning None, so a refusal cannot be mistaken for
    a successful rating of zero.
    """
    if complaint.status != Complaint.Status.CLOSED:
        raise ValueError(
            "A complaint can only be rated once it is closed."
        )

    if complaint.submitted_by_id != getattr(actor, "id", None):
        raise ValueError(
            "Only the person who lodged a complaint can rate it."
        )

    if not isinstance(score, int) or not (
        ComplaintRating.MINIMUM <= score <= ComplaintRating.MAXIMUM
    ):
        raise ValueError(
            f"The rating must be a whole number from "
            f"{ComplaintRating.MINIMUM} to {ComplaintRating.MAXIMUM}."
        )

    # get_or_create rather than create, so two submissions racing --
    # a double-clicked button is the usual cause -- leave one rating
    # and a clear refusal, not an IntegrityError page.
    with transaction.atomic():
        rating, created = ComplaintRating.objects.get_or_create(
            complaint=complaint,
            defaults={
                "score": score,
                "comment": (comment or "").strip(),
                "rated_by": actor,
            },
        )

    if not created:
        raise ValueError(
            "This complaint has already been rated."
        )

    return rating


def department_average_rating(department):
    """Mean satisfaction score for a department, or None.

    SCMS-F-020 asks for this to appear on the department dashboard,
    which is T8-32. The aggregate lives here so the dashboard reads
    one definition rather than inventing its own, and so this
    requirement can be tested before that page exists.

    None, not zero, when a department has no ratings yet: zero is a
    score somebody could have given, and showing it would report
    the worst possible satisfaction for a department nobody has
    rated.
    """
    result = ComplaintRating.objects.filter(
        complaint__assigned_department=department,
    ).aggregate(average=Avg("score"))

    return result["average"]


def may_reopen(complaint, user):
    """Whether this user may reopen this complaint right now.

    can_reopen() answers the question about the complaint -- is it
    resolved, unreopened, still inside the window. This adds the
    one about the person, so a template asking "show the button?"
    and reopen_complaint deciding "allow it?" cannot disagree.
    """
    if complaint.submitted_by_id != getattr(user, "id", None):
        return False

    return can_reopen(complaint)


def reopen_complaint(complaint, actor, reason="", source_ip=None):
    """Reopen a resolved complaint within its window (SCMS-F-019).

    Once only, within REOPEN_WINDOW_DAYS of resolution, and only by
    the person who lodged it: reopening is the complainant saying
    the fix did not work, so it is not an action staff take on
    their behalf.

    resolved_at is deliberately left as it was. It records when the
    complaint was resolved, which is what the reopen window was
    measured from, and clearing it would erase the evidence that
    the window was honoured.
    """
    if complaint.submitted_by_id != getattr(actor, "id", None):
        raise ValueError(
            "Only the person who lodged a complaint can reopen it."
        )

    if complaint.status != Complaint.Status.RESOLVED:
        raise ValueError(
            "Only a resolved complaint can be reopened."
        )

    if complaint.reopen_count >= 1:
        raise ValueError(
            "This complaint has already been reopened once."
        )

    if not can_reopen(complaint):
        raise ValueError(
            f"The {REOPEN_WINDOW_DAYS}-day window for reopening this "
            f"complaint has passed."
        )

    labels = dict(Complaint.Status.choices)
    detail = "Reopened by the complainant."

    if reason.strip():
        detail = f"{detail} {reason.strip()}"

    with transaction.atomic():
        complaint.status = Complaint.Status.REOPENED
        complaint.reopened_at = timezone.now()
        complaint.reopen_count += 1
        complaint.save(update_fields=[
            "status", "reopened_at", "reopen_count", "updated_at",
        ])

        ComplaintStatusHistory.objects.create(
            complaint=complaint,
            old_status=Complaint.Status.RESOLVED,
            new_status=Complaint.Status.REOPENED,
            field_name="status",
            old_value=labels[Complaint.Status.RESOLVED],
            new_value=labels[Complaint.Status.REOPENED],
            changed_by=actor,
            source_ip=source_ip,
            comment=detail,
        )

    return complaint


def close_expired_complaints(now=None):
    """Close resolved complaints whose reopen window has passed.

    SCMS-F-019: after the window, the complaint closes itself. The
    acceptance criterion puts this at day 8, which is the first
    moment a complaint resolved on day 0 is past a 7-day window.

    Returns the number closed.

    The actor on these audit rows is None, which the column allows.
    That is the honest record: no person closed these, a scheduled
    job did, and naming a user would put someone's name against an
    action they did not take.

    WHAT THIS DOES NOT DO: run itself. It is invoked by the
    close_expired_complaints management command, and scheduling
    that command -- cron, or Celery Beat once SCMS-F-021 brings
    Celery in -- is deployment configuration. Until something runs
    it, auto-closure happens only when someone runs it by hand.
    """
    moment = now or timezone.now()
    cutoff = moment - timedelta(days=REOPEN_WINDOW_DAYS)
    labels = dict(Complaint.Status.choices)

    expired = Complaint.objects.filter(
        status=Complaint.Status.RESOLVED,
        resolved_at__lte=cutoff,
    )

    closed = 0

    for complaint in expired:
        with transaction.atomic():
            complaint.status = Complaint.Status.CLOSED
            complaint.save(update_fields=["status", "updated_at"])

            ComplaintStatusHistory.objects.create(
                complaint=complaint,
                old_status=Complaint.Status.RESOLVED,
                new_status=Complaint.Status.CLOSED,
                field_name="status",
                old_value=labels[Complaint.Status.RESOLVED],
                new_value=labels[Complaint.Status.CLOSED],
                changed_by=None,
                source_ip=None,
                comment=(
                    f"Closed automatically: the "
                    f"{REOPEN_WINDOW_DAYS}-day window for reopening "
                    f"passed without the complainant reopening it."
                ),
            )

        closed += 1

    return closed


def visible_complaints(user):
    """The complaints this staff user may see (SCMS-SR-003).

    One definition, used by the queue, the dashboard and the
    exports. Before this existed the queue scoped inline, and any
    new page that reported on complaints would have had to repeat
    the rule -- which is how a reporting page ends up showing a
    department figures it should not see.

    Returns an unordered queryset so callers can order it; every
    caller does.
    """
    from .roles import Role, get_department, get_role

    complaints = Complaint.objects.select_related(
        "assigned_department",
        "assigned_to",
        "category",
    )

    role = get_role(user)

    if role == Role.SUPPORT_AGENT:
        return complaints.filter(assigned_to=user)

    if role == Role.DEPARTMENT_ADMIN:
        return complaints.filter(
            assigned_department=get_department(user),
        )

    if role == Role.SYSTEM_ADMIN:
        return complaints

    # Deny by default: a role with no reporting rights sees nothing,
    # rather than everything.
    return complaints.none()


def dashboard_figures(user, now=None):
    """Counts and averages for the dashboard (SCMS-F-025).

    Everything is derived from visible_complaints(), so the numbers
    a user sees cover exactly the complaints they could open. A
    dashboard that aggregates more widely than its viewer's access
    leaks through the totals even when every page is locked.
    """
    moment = now or timezone.now()
    scoped = visible_complaints(user)

    open_statuses = [
        status for status in Complaint.Status.values
        if status != Complaint.Status.CLOSED
    ]

    by_status = {
        row["status"]: row["count"]
        for row in scoped.values("status").annotate(count=Count("id"))
    }
    by_priority = {
        row["priority"]: row["count"]
        for row in scoped.values("priority").annotate(count=Count("id"))
    }
    by_department = {
        row["assigned_department__name"]: row["count"]
        for row in (
            scoped.values("assigned_department__name")
            .annotate(count=Count("id"))
            .order_by("assigned_department__name")
        )
    }

    # A breach is a deadline that has passed on something still
    # open. A closed complaint that was late is history, not a
    # problem anyone can still act on.
    breached = scoped.filter(
        sla_due_at__lt=moment,
        status__in=open_statuses,
    ).count()

    average = ComplaintRating.objects.filter(
        complaint__in=scoped,
    ).aggregate(average=Avg("score"))["average"]

    labels = dict(Complaint.Status.choices)
    priority_labels = dict(ComplaintPriority.choices)

    return {
        "total": scoped.count(),
        "open_total": scoped.exclude(
            status=Complaint.Status.CLOSED,
        ).count(),
        "by_status": {
            labels.get(key, key): value
            for key, value in sorted(by_status.items())
        },
        "by_priority": {
            priority_labels.get(key, key): value
            for key, value in sorted(by_priority.items())
        },
        "by_department": {
            (name or "Unassigned"): value
            for name, value in by_department.items()
        },
        "sla_breached": breached,
        "average_rating": average,
        "rating_count": ComplaintRating.objects.filter(
            complaint__in=scoped,
        ).count(),
    }


# How long an unfinished draft is kept (SCMS-F-010).
DRAFT_RETENTION_DAYS = 7

# A complaint is a suspected duplicate of another by the same
# complainant, in the same category, within this many days
# (SCMS-F-015).
DUPLICATE_WINDOW_DAYS = 30

# Description similarity at or above which the pair is flagged.
DUPLICATE_THRESHOLD = 0.70


def save_draft(user, **fields):
    """Create or update this user's draft (SCMS-F-010).

    The retention window runs from the last save, not the first:
    updated_at is what auto_now keeps, and a draft someone edited
    yesterday should not vanish because they began it eight days
    ago.
    """
    draft, _ = ComplaintDraft.objects.update_or_create(
        user=user, defaults=fields,
    )

    return draft


def get_draft(user):
    """This user's draft, or None."""
    return ComplaintDraft.objects.filter(user=user).first()


def purge_expired_drafts(now=None):
    """Delete drafts untouched for DRAFT_RETENTION_DAYS.

    Returns the number deleted. Like close_expired_complaints,
    this is invoked by a management command; scheduling it is
    deployment configuration.
    """
    moment = now or timezone.now()
    cutoff = moment - timedelta(days=DRAFT_RETENTION_DAYS)

    deleted, _ = ComplaintDraft.objects.filter(
        updated_at__lte=cutoff,
    ).delete()

    return deleted


def description_similarity(first, second):
    """How alike two descriptions are, from 0.0 to 1.0.

    difflib rather than anything cleverer. SCMS-F-015 asks for a
    similarity figure and a threshold, not for semantic matching,
    and a ratio a reader can reproduce by hand is easier to defend
    than a score out of a model nobody can inspect.

    Case and surrounding whitespace are ignored, so the same text
    retyped in a different case still matches.
    """
    return SequenceMatcher(
        None,
        (first or "").strip().lower(),
        (second or "").strip().lower(),
    ).ratio()


def find_duplicates(complaint, now=None):
    """Earlier complaints this one may duplicate (SCMS-F-015).

    Same complainant, same category, within the window, ordered
    most similar first. Returns (complaint, similarity) pairs.
    """
    moment = now or timezone.now()
    cutoff = moment - timedelta(days=DUPLICATE_WINDOW_DAYS)

    candidates = (
        Complaint.objects
        .filter(
            submitted_by_id=complaint.submitted_by_id,
            category_id=complaint.category_id,
            created_at__gte=cutoff,
        )
        .exclude(pk=complaint.pk)
    )

    matches = [
        (other, description_similarity(
            complaint.description, other.description,
        ))
        for other in candidates
    ]

    return sorted(
        [pair for pair in matches if pair[1] >= DUPLICATE_THRESHOLD],
        key=lambda pair: pair[1],
        reverse=True,
    )


def flag_duplicates(complaint, now=None):
    """Record a flag for each suspected duplicate. Returns them.

    Nothing is merged, closed or hidden: the flag asks a question
    and an agent answers it. Acting automatically on a 70% text
    match would close real complaints that happen to be worded
    alike.
    """
    flags = []

    for other, similarity in find_duplicates(complaint, now=now):
        flag, created = DuplicateFlag.objects.get_or_create(
            complaint=complaint,
            similar_to=other,
            defaults={"similarity": similarity},
        )

        if created:
            flags.append(flag)

    return flags


def resolve_duplicate_flag(flag, actor, confirmed):
    """Record an agent's verdict on a suspected duplicate."""
    if flag.verdict != DuplicateVerdict.PENDING:
        raise ValueError("This flag has already been reviewed.")

    flag.verdict = (
        DuplicateVerdict.CONFIRMED if confirmed
        else DuplicateVerdict.DISMISSED
    )
    flag.reviewed_by = actor
    flag.reviewed_at = timezone.now()
    flag.save(update_fields=["verdict", "reviewed_by", "reviewed_at"])

    return flag
