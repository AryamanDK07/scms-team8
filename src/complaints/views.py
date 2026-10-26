
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from .services import calculate_sla_deadline

from .forms import ComplaintForm
from .models import (
    Complaint,
    ComplaintStatusHistory,
    Department,
)


CATEGORY_TO_DEPARTMENT = {
    Complaint.Category.ACADEMIC: "academics",
    Complaint.Category.FACILITIES: "facilities",
    Complaint.Category.IT: "it-support",
    Complaint.Category.HOSTEL: "hostel",
    Complaint.Category.TRANSPORT: "transport",
    Complaint.Category.OTHER: "general",
}


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


@login_required
def complaint_create(request):
    if request.method == "POST":
        form = ComplaintForm(request.POST)

        if form.is_valid():
            complaint = form.save(commit=False)
            complaint.submitted_by = request.user

            department_code = CATEGORY_TO_DEPARTMENT.get(
                complaint.category,
                "general",
            )

            department = Department.objects.filter(
                code=department_code,
                is_active=True,
            ).first()

            complaint.assigned_department = department
            complaint.sla_due_at = calculate_sla_deadline(
            complaint.priority
)
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
        ),
        reference_id=reference_id,
        submitted_by=request.user,
    )

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
