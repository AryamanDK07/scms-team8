
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from .services import calculate_sla_deadline

from . import exports, ratelimit
from .attachments import (
    MAX_PER_COMPLAINT,
    canonical_content_type,
)
from .forms import (
    AssignComplaintForm,
    AttachmentForm,
    CommentForm,
    ComplaintForm,
    ComplaintSearchForm,
    PriorityOverrideForm,
    RatingForm,
    ReopenForm,
    ResolutionForm,
    StatusChangeForm,
)
from .models import (
    Complaint,
    ComplaintDraft,
    ComplaintAttachment,
    ComplaintComment,
    ComplaintStatusHistory,
    DuplicateFlag,
)
from .permissions import (
    can_work_complaint,
    require_complaint_access,
    role_required,
)
from .roles import Role, STAFF_ROLES, get_role
from .services import (
    add_comment,
    allowed_transitions,
    apply_complaint_filters,
    assign_complaint,
    can_rate,
    comment_thread,
    dashboard_figures,
    DRAFT_RETENTION_DAYS,
    flag_duplicates,
    get_draft,
    offerable_transitions,
    override_priority,
    may_reopen,
    priority_for,
    rate_complaint,
    resolve_duplicate_flag,
    reopen_complaint,
    save_draft,
    resolve_complaint,
    transition_status,
    visible_complaints,
)


@login_required
def complaint_list(request):
    complaints = (
        Complaint.objects
        .filter(submitted_by=request.user)
        .select_related("assigned_department")
        .order_by("-created_at")
    )

    return render(
        request,
        "complaints/complaint_list.html",
        {"complaints": complaints},
    )


@role_required(Role.COMPLAINANT)
def complaint_create(request):
    if request.method == "POST":
        form = ComplaintForm(request.POST)

        # SCMS-F-010. Saving a draft deliberately skips validation:
        # a draft is unfinished by definition, and refusing to keep
        # it because a field is missing defeats the point.
        if "save_draft" in request.POST:
            save_draft(
                request.user,
                title=request.POST.get("title", "")[:200],
                description=request.POST.get("description", "")[:2000],
                location=request.POST.get("location", "")[:200],
                category_id=request.POST.get("category") or None,
                sub_category_id=request.POST.get("sub_category") or None,
            )

            messages.success(
                request,
                f"Draft saved. It will be kept for "
                f"{DRAFT_RETENTION_DAYS} days.",
            )

            return redirect("complaints:complaint_create")

        # SCMS-SR-007. Checked before the form is saved, so a
        # throttled submission creates nothing.
        if ratelimit.too_many_submissions(request.user):
            messages.error(
                request,
                f"You have submitted "
                f"{ratelimit.SUBMISSION_LIMIT} complaints in the last "
                f"hour, which is the limit. Please try again later.",
            )

            return render(
                request,
                "complaints/complaint_form.html",
                {
                    "form": form,
                    "draft": get_draft(request.user),
                    "draft_days": DRAFT_RETENTION_DAYS,
                },
            )

        if form.is_valid():
            complaint = form.save(commit=False)
            complaint.submitted_by = request.user

            category = complaint.category
            department = (
                category.department
                if category.department.is_active
                else None
            )

            complaint.assigned_department = department

            # SCMS-F-013: priority comes from the category's matrix,
            # not from the complainant.
            complaint.priority = priority_for(category)
            complaint.sla_due_at = calculate_sla_deadline(category)
            complaint.save()

            ComplaintStatusHistory.objects.create(
                complaint=complaint,
                old_status="",
                new_status=complaint.status,
                field_name="status",
                old_value="",
                new_value=complaint.get_status_display(),
                changed_by=request.user,
                # SCMS-F-027: lodging is a state change too, and was
                # the one path that recorded no source IP.
                source_ip=request.META.get("REMOTE_ADDR"),
                comment=(
                    f"Complaint submitted and assigned to "
                    f"{department.name}."
                    if department
                    else "Complaint submitted; department assignment "
                         "requires administrator attention."
                ),
            )

            if department:
                messages.success(
                    request,
                    f"Complaint submitted successfully. "
                    f"Reference ID: {complaint.reference_id}. "
                    f"Assigned department: {department.name}.",
                )
            else:
                messages.warning(
                    request,
                    "Complaint submitted, but no active matching "
                    "department was found. An administrator must "
                    "assign it.",
                )

            # SCMS-F-015. Flagged after the complaint exists, so a
            # suspected duplicate is recorded against a real
            # reference rather than guessed at before submission.
            flagged = flag_duplicates(complaint)

            if flagged:
                messages.warning(
                    request,
                    f"This looks similar to {len(flagged)} complaint(s) "
                    f"you lodged recently. An agent will check whether "
                    f"it is a duplicate.",
                )

            # The draft has become a complaint, so it is no longer a
            # draft. Leaving it would offer the complainant their own
            # submitted text back as unfinished work.
            ComplaintDraft.objects.filter(user=request.user).delete()

            return redirect(
                "complaints:complaint_detail",
                reference_id=complaint.reference_id,
            )
    else:
        draft = get_draft(request.user)

        form = ComplaintForm(initial={
            "title": draft.title,
            "description": draft.description,
            "location": draft.location,
            "category": draft.category_id,
            "sub_category": draft.sub_category_id,
        }) if draft else ComplaintForm()

    return render(
        request,
        "complaints/complaint_form.html",
        {
            "form": form,
            "draft": get_draft(request.user),
            "draft_days": DRAFT_RETENTION_DAYS,
        },
    )


@role_required(Role.COMPLAINANT)
def draft_discard(request):
    """Throw away the saved draft (SCMS-F-010)."""
    if request.method == "POST":
        ComplaintDraft.objects.filter(user=request.user).delete()
        messages.success(request, "Draft discarded.")

    return redirect("complaints:complaint_create")


@role_required(*STAFF_ROLES)
def duplicate_flag_resolve(request, pk):
    """Confirm or dismiss a suspected duplicate (SCMS-F-015)."""
    flag = get_object_or_404(DuplicateFlag, pk=pk)

    require_complaint_access(flag.complaint, request.user, write=True)

    if request.method == "POST":
        try:
            resolve_duplicate_flag(
                flag,
                actor=request.user,
                confirmed=request.POST.get("verdict") == "confirm",
            )
        except ValueError as problem:
            messages.error(request, str(problem))
        else:
            messages.success(
                request, f"Recorded: {flag.get_verdict_display()}.",
            )

    return redirect(
        "complaints:complaint_detail",
        reference_id=flag.complaint.reference_id,
    )


@login_required
def complaint_detail(request, reference_id):
    complaint = get_object_or_404(
        Complaint.objects.select_related(
            "assigned_department",
            "assigned_to",
            "category",
        ),
        reference_id=reference_id,
    )

    # SCMS-SR-003: changing the reference ID in the URL must not
    # expose someone else's complaint.
    require_complaint_access(complaint, request.user)

    history = complaint.status_history.select_related(
        "changed_by"
    ).all()

    return render(
        request,
        "complaints/complaint_detail.html",
        {
            "complaint": complaint,
            "history": history,
            "thread": comment_thread(complaint),
            "comment_form": CommentForm(),
            "rating_form": RatingForm(),
            "can_rate": can_rate(complaint, request.user),
            "reopen_form": ReopenForm(),
            "can_reopen": may_reopen(complaint, request.user),
            "duplicate_flags": complaint.duplicate_flags.select_related(
                "similar_to", "reviewed_by",
            ).all(),
            "can_work": can_work_complaint(request.user, complaint),
        },
    )


@login_required
def complaint_comment_add(request, reference_id):
    """Post a comment or a reply on a complaint (SCMS-F-017).

    Access follows the complaint, so the complainant and the
    assigned agent can both post, and another complainant cannot
    reach it at all.
    """
    complaint = get_object_or_404(Complaint, reference_id=reference_id)

    require_complaint_access(complaint, request.user)

    if request.method != "POST":
        return redirect(
            "complaints:complaint_detail",
            reference_id=complaint.reference_id,
        )

    form = CommentForm(request.POST)

    if form.is_valid():
        parent = None
        parent_id = form.cleaned_data.get("parent")

        if parent_id:
            # Scoped to this complaint: a parent id from elsewhere
            # must not resolve, or a reply could be attached to a
            # conversation the author cannot see.
            parent = get_object_or_404(
                ComplaintComment, pk=parent_id, complaint=complaint,
            )

        try:
            add_comment(
                complaint,
                author=request.user,
                body=form.cleaned_data["body"],
                parent=parent,
            )
        except ValueError as error:
            form.add_error(None, str(error))
        else:
            return redirect(
                "complaints:complaint_detail",
                reference_id=complaint.reference_id,
            )

    history = complaint.status_history.select_related("changed_by").all()

    return render(
        request,
        "complaints/complaint_detail.html",
        {
            "complaint": complaint,
            "history": history,
            "thread": comment_thread(complaint),
            "comment_form": form,
            "rating_form": RatingForm(),
            "can_rate": can_rate(complaint, request.user),
            "reopen_form": ReopenForm(),
            "can_reopen": may_reopen(complaint, request.user),
            "duplicate_flags": complaint.duplicate_flags.select_related(
                "similar_to", "reviewed_by",
            ).all(),
            "can_work": can_work_complaint(request.user, complaint),
        },
    )


@login_required
def complaint_attachment_add(request, reference_id):
    """Attach evidence to a complaint (SCMS-F-008).

    Only the complainant who lodged it may add evidence; staff
    record their own proof through the resolution flow.
    """
    complaint = get_object_or_404(Complaint, reference_id=reference_id)

    require_complaint_access(complaint, request.user)

    if complaint.submitted_by_id != request.user.id:
        raise PermissionDenied(
            "Only the complainant may attach evidence to this "
            "complaint."
        )

    if request.method == "POST":
        form = AttachmentForm(
            request.POST, request.FILES, complaint=complaint,
        )

        if form.is_valid():
            upload = form.cleaned_data["file"]

            ComplaintAttachment.objects.create(
                complaint=complaint,
                file=upload,
                original_name=upload.name[:255],
                content_type=canonical_content_type(
                    form.detected_format,
                ),
                size_bytes=upload.size,
                uploaded_by=request.user,
            )

            messages.success(
                request, f"\"{upload.name}\" attached.",
            )

            return redirect(
                "complaints:complaint_detail",
                reference_id=complaint.reference_id,
            )
    else:
        form = AttachmentForm(complaint=complaint)

    return render(
        request,
        "complaints/complaint_attachment_form.html",
        {
            "complaint": complaint,
            "form": form,
            "remaining": MAX_PER_COMPLAINT - complaint.attachments.count(),
        },
    )


@login_required
def complaint_attachment_download(request, reference_id, pk):
    """Serve an attachment after checking access (SCMS-SR-005).

    Files are not reachable by URL: they are stored outside the web
    root and only handed over here, once the requester has been
    shown to have access to the complaint.
    """
    complaint = get_object_or_404(Complaint, reference_id=reference_id)

    require_complaint_access(complaint, request.user)

    attachment = get_object_or_404(
        complaint.attachments, pk=pk,
    )

    response = FileResponse(
        attachment.file.open("rb"),
        as_attachment=True,
        filename=attachment.original_name,
        content_type=attachment.content_type,
    )

    # as_attachment already sets Content-Disposition: attachment, so
    # the browser offers the file rather than rendering it. nosniff
    # covers the other half: without it a browser may ignore the
    # declared type and act on what the bytes look like, which is
    # precisely the guess SCMS-SR-005 exists to prevent.
    response["X-Content-Type-Options"] = "nosniff"

    return response


@role_required(Role.DEPARTMENT_ADMIN, Role.SYSTEM_ADMIN)
def complaint_assign(request, reference_id):
    """Assign or reassign a complaint to an agent (SCMS-F-012).

    Agents cannot reach this: deciding who owns work belongs to the
    Department Admin for that department, or a System Admin.
    """
    complaint = get_object_or_404(
        Complaint.objects.select_related(
            "assigned_department", "assigned_to", "category",
        ),
        reference_id=reference_id,
    )

    require_complaint_access(complaint, request.user, write=True)

    if request.method == "POST":
        form = AssignComplaintForm(request.POST, complaint=complaint)

        if form.is_valid():
            assign_complaint(
                complaint,
                agent=form.cleaned_data["agent"],
                actor=request.user,
                reason=form.cleaned_data["reason"],
                source_ip=request.META.get("REMOTE_ADDR"),
            )

            messages.success(
                request,
                f"{complaint.reference_id} assigned to "
                f"{form.cleaned_data['agent'].get_username()}.",
            )

            return redirect(
                "complaints:complaint_detail",
                reference_id=complaint.reference_id,
            )
    else:
        form = AssignComplaintForm(complaint=complaint)

    return render(
        request,
        "complaints/complaint_assign.html",
        {"complaint": complaint, "form": form},
    )


@role_required(Role.DEPARTMENT_ADMIN, Role.SYSTEM_ADMIN)
def complaint_priority(request, reference_id):
    """Override the matrix priority, with a reason (SCMS-F-013)."""
    complaint = get_object_or_404(
        Complaint.objects.select_related(
            "assigned_department", "category",
        ),
        reference_id=reference_id,
    )

    require_complaint_access(complaint, request.user, write=True)

    if request.method == "POST":
        form = PriorityOverrideForm(request.POST, complaint=complaint)

        if form.is_valid():
            previous = complaint.get_priority_display()

            override_priority(
                complaint,
                priority=form.cleaned_data["priority"],
                actor=request.user,
                reason=form.cleaned_data["reason"],
                source_ip=request.META.get("REMOTE_ADDR"),
            )

            messages.success(
                request,
                f"{complaint.reference_id} priority changed from "
                f"{previous} to {complaint.get_priority_display()}.",
            )

            return redirect(
                "complaints:complaint_detail",
                reference_id=complaint.reference_id,
            )
    else:
        form = PriorityOverrideForm(complaint=complaint)

    return render(
        request,
        "complaints/complaint_priority.html",
        {"complaint": complaint, "form": form},
    )


@role_required(*STAFF_ROLES)
def complaint_resolve(request, reference_id):
    """Resolve a complaint with remarks and proof (SCMS-F-018).

    This is the only route to Resolved: the generic status change
    guards against it precisely so that a complaint cannot be
    closed off without the evidence this view requires.
    """
    complaint = get_object_or_404(
        Complaint.objects.select_related(
            "assigned_department", "assigned_to", "category",
        ),
        reference_id=reference_id,
    )

    require_complaint_access(complaint, request.user, write=True)

    resolvable = (
        Complaint.Status.RESOLVED in allowed_transitions(complaint)
    )

    if request.method == "POST" and resolvable:
        form = ResolutionForm(
            request.POST, request.FILES, complaint=complaint,
        )

        if form.is_valid():
            try:
                resolve_complaint(
                    complaint,
                    actor=request.user,
                    remarks=form.cleaned_data["remarks"],
                    proof_uploads=[form.cleaned_data["proof"]],
                    source_ip=request.META.get("REMOTE_ADDR"),
                )
            except ValueError as error:
                form.add_error(None, str(error))
            else:
                messages.success(
                    request,
                    f"{complaint.reference_id} marked resolved.",
                )

                return redirect(
                    "complaints:complaint_detail",
                    reference_id=complaint.reference_id,
                )
    else:
        form = ResolutionForm(complaint=complaint)

    return render(
        request,
        "complaints/complaint_resolve.html",
        {
            "complaint": complaint,
            "form": form,
            "resolvable": resolvable,
        },
    )


@role_required(*STAFF_ROLES)
def complaint_status(request, reference_id):
    """Move a complaint along the state machine (SCMS-F-016)."""
    complaint = get_object_or_404(
        Complaint.objects.select_related(
            "assigned_department", "assigned_to", "category",
        ),
        reference_id=reference_id,
    )

    require_complaint_access(complaint, request.user, write=True)

    if request.method == "POST":
        form = StatusChangeForm(request.POST, complaint=complaint)

        if form.is_valid():
            try:
                transition_status(
                    complaint,
                    new_status=form.cleaned_data["status"],
                    actor=request.user,
                    comment=form.cleaned_data["comment"],
                    source_ip=request.META.get("REMOTE_ADDR"),
                )
            except ValueError as error:
                # The form only offers legal moves, so this is the
                # race where someone else moved the complaint first.
                form.add_error(None, str(error))
            else:
                messages.success(
                    request,
                    f"{complaint.reference_id} is now "
                    f"{complaint.get_status_display()}.",
                )

                return redirect(
                    "complaints:complaint_detail",
                    reference_id=complaint.reference_id,
                )
    else:
        form = StatusChangeForm(complaint=complaint)

    return render(
        request,
        "complaints/complaint_status.html",
        {
            "complaint": complaint,
            "form": form,
            "has_moves": bool(offerable_transitions(complaint)),
        },
    )


@role_required(*STAFF_ROLES)
def department_queue(request):
    """Staff queue, scoped by role (SCMS-F-003, SCMS-SR-003).

    An agent sees only complaints assigned to them; a Department
    Admin sees their department; a System Admin sees everything.
    """
    role = get_role(request.user)
    complaints, search = scoped_and_filtered(request)

    return render(
        request,
        "complaints/department_queue.html",
        {
            "complaints": complaints.order_by("sla_due_at"),
            "search": search,
            "role": role,
            "can_assign": role in (
                Role.DEPARTMENT_ADMIN, Role.SYSTEM_ADMIN,
            ),
        },
    )


@login_required
def complaint_rate(request, reference_id):
    """Record the complainant's rating of a closed complaint.

    SCMS-F-020. Access follows the complaint, and rate_complaint
    then decides whether this particular user may rate it now --
    the view does not duplicate that rule, so the view and any
    other caller cannot drift apart on who may rate what.
    """
    complaint = get_object_or_404(Complaint, reference_id=reference_id)

    require_complaint_access(complaint, request.user)

    if request.method != "POST":
        return redirect(
            "complaints:complaint_detail",
            reference_id=complaint.reference_id,
        )

    form = RatingForm(request.POST)

    if form.is_valid():
        try:
            rate_complaint(
                complaint,
                actor=request.user,
                score=form.cleaned_data["score"],
                comment=form.cleaned_data["comment"],
            )
        except ValueError as error:
            form.add_error(None, str(error))
        else:
            return redirect(
                "complaints:complaint_detail",
                reference_id=complaint.reference_id,
            )

    history = complaint.status_history.select_related("changed_by").all()

    return render(
        request,
        "complaints/complaint_detail.html",
        {
            "complaint": complaint,
            "history": history,
            "thread": comment_thread(complaint),
            "comment_form": CommentForm(),
            "rating_form": form,
            "can_rate": can_rate(complaint, request.user),
            "reopen_form": ReopenForm(),
            "can_reopen": may_reopen(complaint, request.user),
            "duplicate_flags": complaint.duplicate_flags.select_related(
                "similar_to", "reviewed_by",
            ).all(),
            "can_work": can_work_complaint(request.user, complaint),
        },
    )


@login_required
def complaint_reopen(request, reference_id):
    """Reopen a resolved complaint within its window (SCMS-F-019).

    Access follows the complaint; reopen_complaint then decides
    whether this user may reopen it now, so the rule lives in one
    place rather than being restated here.
    """
    complaint = get_object_or_404(Complaint, reference_id=reference_id)

    require_complaint_access(complaint, request.user)

    if request.method != "POST":
        return redirect(
            "complaints:complaint_detail",
            reference_id=complaint.reference_id,
        )

    form = ReopenForm(request.POST)
    error = None

    if form.is_valid():
        try:
            reopen_complaint(
                complaint,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                source_ip=request.META.get("REMOTE_ADDR"),
            )
        except ValueError as problem:
            error = str(problem)
        else:
            return redirect(
                "complaints:complaint_detail",
                reference_id=complaint.reference_id,
            )

    if error:
        form.add_error(None, error)

    history = complaint.status_history.select_related("changed_by").all()

    return render(
        request,
        "complaints/complaint_detail.html",
        {
            "complaint": complaint,
            "history": history,
            "thread": comment_thread(complaint),
            "comment_form": CommentForm(),
            "rating_form": RatingForm(),
            "can_rate": can_rate(complaint, request.user),
            "reopen_form": form,
            "can_reopen": False,
        },
    )


def scoped_and_filtered(request):
    """The complaints this user may see, narrowed by the search.

    Shared by the queue, the dashboard links and both exports, so
    an export cannot return a different set of rows from the page
    it was launched from -- which is exactly what SCMS-F-026's
    acceptance criterion asks.

    SCMS-F-028: the search runs on the queryset the role scoping
    has already narrowed, so filtering can only ever subtract.
    Building a fresh queryset here would make search a way around
    SCMS-SR-003.
    """
    complaints = visible_complaints(request.user)
    search = ComplaintSearchForm(request.GET or None)

    if search.is_valid():
        complaints = apply_complaint_filters(
            complaints, search.cleaned_data,
        )

    return complaints, search


@role_required(*STAFF_ROLES)
def dashboard(request):
    """Role-scoped workload and SLA dashboard (SCMS-F-025)."""
    return render(
        request,
        "complaints/dashboard.html",
        {
            "figures": dashboard_figures(request.user),
            "role": get_role(request.user),
        },
    )


@role_required(*STAFF_ROLES)
def complaint_export(request, extension):
    """Export the filtered list as CSV or PDF (SCMS-F-026)."""
    complaints, _ = scoped_and_filtered(request)
    complaints = complaints.order_by("sla_due_at")

    if extension == "csv":
        return exports.as_csv(complaints)

    return exports.as_pdf(complaints)
