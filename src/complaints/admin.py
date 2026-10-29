
from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils import timezone

from .models import (
    Department,
    Complaint,
    Category,
    ComplaintStatusHistory,
    SubCategory,
    UserProfile,
)
from .services import calculate_sla_deadline


class DepartmentAdminForm(forms.ModelForm):
    """Guards deactivation of a department (SCMS-F-023).

    Routing reads the department off the category. Deactivating a
    department that categories still point at does not fail
    loudly: complaints in those categories are lodged with no
    department at all, and no queue shows an unassigned complaint,
    so they simply stop being seen.
    """

    class Meta:
        model = Department
        fields = "__all__"

    def clean_is_active(self):
        is_active = self.cleaned_data["is_active"]

        if is_active or not self.instance.pk:
            return is_active

        routed = list(
            self.instance.categories.filter(is_active=True)[:6]
        )

        if routed:
            names = ", ".join(c.name for c in routed[:5])
            more = " and others" if len(routed) > 5 else ""

            raise forms.ValidationError(
                f"{self.instance.name} is still the department for "
                f"{names}{more}. Repoint those categories to another "
                f"department, or deactivate them first. Otherwise new "
                f"complaints in them would be lodged with no "
                f"department and would not appear in any queue."
            )

        return is_active


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    form = DepartmentAdminForm
    list_display = (
        "name",
        "code",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("name",)


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = (
        "reference_id",
        "title",
        "category",
        "sub_category",
        "location",
        "priority",
        "status",
        "assigned_department",
        "assigned_to",
        "sla_due_at",
        "submitted_by",
        "created_at",
    )

    list_filter = (
        "category",
        "sub_category",
        "priority",
        "status",
        "assigned_department",
        "created_at",
    )

    search_fields = (
        "=reference_id",
        "title",
        "description",
        "location",
        "submitted_by__username",
        "submitted_by__email",
        "assigned_to__username",
        "assigned_department__name",
    )

    readonly_fields = (
        "reference_id",
        "created_at",
        "updated_at",
        "resolved_at",
    )

    date_hierarchy = "created_at"
    list_per_page = 25
    ordering = ("-created_at",)

    def save_model(self, request, obj, form, change):
        # Retrieve the previous values before saving an existing complaint.
        if change:
            previous = Complaint.objects.get(pk=obj.pk)
            old_status = previous.status
            old_priority = previous.priority
        else:
            old_status = None
            old_priority = None

        is_new = not change

        # Calculate the SLA deadline for new complaints,
        # changed priorities, or complaints without a deadline.
        if (
            is_new
            or old_priority != obj.priority
            or obj.sla_due_at is None
        ):
            # The SLA comes from the category, not the priority
            # (SCMS-F-014, ADR-06). This previously passed the
            # priority string, which has no sla_hours attribute, so
            # every admin-created complaint silently fell back to the
            # default 48 hours regardless of its category.
            obj.sla_due_at = calculate_sla_deadline(obj.category)

        # Record the resolution timestamp when a complaint
        # first reaches the Resolved status.
        if obj.status == Complaint.Status.RESOLVED:
            if (
                is_new
                or old_status != Complaint.Status.RESOLVED
                or obj.resolved_at is None
            ):
                obj.resolved_at = timezone.now()
        else:
            # Clear the current resolution timestamp if reopened
            # or changed to another non-resolved status.
            obj.resolved_at = None

        # Save the complaint.
        super().save_model(request, obj, form, change)

        # Create an audit entry for new complaints and
        # actual status changes only.
        if is_new or old_status != obj.status:
            status_labels = dict(Complaint.Status.choices)

            old_label = (
                status_labels.get(old_status, old_status)
                if old_status
                else "New"
            )

            new_label = status_labels.get(
                obj.status,
                obj.status,
            )

            if is_new:
                comment = (
                    f"Complaint created by administrator. "
                    f"Initial status: {new_label}."
                )
            else:
                comment = (
                    f"Status changed from {old_label} "
                    f"to {new_label} by administrator."
                )

            ComplaintStatusHistory.objects.create(
                complaint=obj,
                old_status=old_status or "",
                new_status=obj.status,
                field_name="status",
                old_value=old_label if old_status else "",
                new_value=new_label,
                changed_by=request.user,
                source_ip=request.META.get("REMOTE_ADDR"),
                comment=comment,
            )


@admin.register(ComplaintStatusHistory)
class ComplaintStatusHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "complaint",
        "old_status",
        "new_status",
        "changed_by",
        "created_at",
    )

    list_filter = (
        "new_status",
        "created_at",
    )

    search_fields = (
        "complaint__title",
        "complaint__reference_id",
        "comment",
        "changed_by__username",
    )

    readonly_fields = (
        "complaint",
        "old_status",
        "new_status",
        "changed_by",
        "comment",
        "created_at",
    )

    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 25

    # Status history is an audit trail, so it cannot be
    # manually added or deleted through Django Admin.
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    """Master data for routing, SLA and default priority (SCMS-F-023)."""

    list_display = (
        "name",
        "code",
        "department",
        "sla_hours",
        "default_priority",
        "is_active",
    )
    list_filter = ("department", "is_active")
    search_fields = ("name", "code")


@admin.register(SubCategory)
class SubCategoryAdmin(admin.ModelAdmin):
    """Master data for complaint sub-categories (SCMS-F-006)."""

    list_display = ("name", "code", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "code", "category__name")
    ordering = ("category__name", "name")


class UserProfileInline(admin.StackedInline):
    """Role and department, shown on the user's own admin page.

    SCMS-F-003 gives every user a role, but until now nothing
    exposed it: a profile was created by signal and could only be
    changed from a shell. That left the application unusable as
    anything but a Complainant.

    An inline keeps role and account together, so granting someone
    the Support Agent role is one edit rather than two objects to
    find and match up.
    """

    model = UserProfile
    can_delete = False
    verbose_name_plural = "SCMS role"
    fields = ("role", "department")


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = BaseUserAdmin.list_display + ("scms_role", "department")
    list_filter = BaseUserAdmin.list_filter + ("profile__role",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "profile__department",
        )

    @admin.display(description="SCMS role", ordering="profile__role")
    def scms_role(self, obj):
        profile = getattr(obj, "profile", None)

        return profile.get_role_display() if profile else "-"

    @admin.display(description="Department", ordering="profile__department")
    def department(self, obj):
        profile = getattr(obj, "profile", None)

        return profile.department if profile and profile.department else "-"


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """The roles on their own, for reviewing who holds what."""

    list_display = ("user", "role", "department", "created_at")
    list_filter = ("role", "department")
    search_fields = ("user__username", "user__email")
    ordering = ("user__username",)
