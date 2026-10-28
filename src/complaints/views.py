
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from .services import calculate_sla_deadline

from .attachments import MAX_PER_COMPLAINT, canonical_content_type
from .forms import (
    AssignComplaintForm,
    AttachmentForm,
    ComplaintForm,
    PriorityOverrideForm,
)
from .models import (
    Complaint,
    ComplaintAttachment,
    ComplaintStatusHistory,
)
from .permissions import require_complaint_access, role_required
from .roles import Role, STAFF_ROLES, get_department, get_role
from .services import assign_complaint, override_priority, priority_for


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
                changed_by=request.user,
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

            return redirect(
                "complaints:complaint_detail",
                reference_id=complaint.reference_id,
            )
    else:
        form = ComplaintForm()

    return render(
        request,
        "complaints/complaint_form.html",
        {"form": form},
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

    return FileResponse(
        attachment.file.open("rb"),
        as_attachment=True,
        filename=attachment.original_name,
        content_type=attachment.content_type,
    )


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
def department_queue(request):
    """Staff queue, scoped by role (SCMS-F-003, SCMS-SR-003).

    An agent sees only complaints assigned to them; a Department
    Admin sees their department; a System Admin sees everything.
    """
    complaints = Complaint.objects.select_related(
        "assigned_department",
        "assigned_to",
        "category",
    )

    role = get_role(request.user)

    if role == Role.SUPPORT_AGENT:
        complaints = complaints.filter(assigned_to=request.user)
    elif role == Role.DEPARTMENT_ADMIN:
        complaints = complaints.filter(
            assigned_department=get_department(request.user),
        )

    return render(
        request,
        "complaints/department_queue.html",
        {
            "complaints": complaints.order_by("sla_due_at"),
            "role": role,
            "can_assign": role in (
                Role.DEPARTMENT_ADMIN, Role.SYSTEM_ADMIN,
            ),
        },
    )
