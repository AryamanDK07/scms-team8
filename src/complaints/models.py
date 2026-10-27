
from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from .roles import Role


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Category(models.Model):
    """Master data: complaint categories.

    SCMS-F-023 and ADR-06 require categories, their department
    mapping, their SLA and their default priority to be editable
    rows rather than constants in code, so that an administrator
    can change routing behaviour without a redeployment.
    """

    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(max_length=30, unique=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="categories",
        help_text="Department this category is routed to (SCMS-F-011).",
    )
    sla_hours = models.PositiveIntegerField(
        default=48,
        help_text="Resolution SLA in hours for this category (SCMS-F-014).",
    )
    default_priority = models.CharField(
        max_length=10,
        default="MEDIUM",
        help_text="Default priority from the priority matrix (SCMS-F-013).",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class ReferenceSequence(models.Model):
    """Per-year counter backing the SCMS-YYYY-NNNNNN reference ID."""

    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-year"]

    def __str__(self):
        return f"{self.year}: {self.last_number}"


class Complaint(models.Model):

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ASSIGNED = "ASSIGNED", "Assigned"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        PENDING_INFORMATION = "PENDING_INFORMATION", "Pending Information"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"
        REOPENED = "REOPENED", "Reopened"

    reference_id = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        help_text="Format SCMS-YYYY-NNNNNN (SCMS-F-007).",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(max_length=2000)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="complaints",
    )
    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.OPEN,
    )

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_complaints",
    )
    assigned_department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_complaints",
    )

    sla_due_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopen_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="A complaint may be reopened once (SCMS-F-019).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["assigned_department"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.reference_id} - {self.title}"

    def save(self, *args, **kwargs):
        if not self.reference_id:
            from .services import generate_reference_id

            self.reference_id = generate_reference_id()
        super().save(*args, **kwargs)


class ComplaintStatusHistory(models.Model):
    """Append-only audit trail of complaint state changes.

    SCMS-F-027 and SCMS-SR-006: rows are written once and never
    updated or deleted through the application.
    """

    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.PROTECT,
        related_name="status_history",
    )
    old_status = models.CharField(
        max_length=30,
        choices=Complaint.Status.choices,
        blank=True,
    )
    new_status = models.CharField(
        max_length=30,
        choices=Complaint.Status.choices,
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaint_status_changes",
    )
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "complaint status history"

    def __str__(self):
        return (
            f"{self.complaint.reference_id}: "
            f"{self.old_status or 'NEW'} -> {self.new_status}"
        )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError(
                "Audit rows are append-only and cannot be modified "
                "(SCMS-SR-006)."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "Audit rows are append-only and cannot be deleted "
            "(SCMS-SR-006)."
        )


class UserProfile(models.Model):
    """Role membership for a user (SCMS-F-003).

    Every user has exactly one profile. A user without a profile
    holds no role and is refused everything, which is the
    deny-by-default stance required by SCMS-SR-003.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.COMPLAINANT,
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="staff",
        help_text="Required for Support Agent and Department Admin.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user} ({self.get_role_display()})"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    """Give every new user a Complainant profile by default."""
    if created:
        UserProfile.objects.get_or_create(user=instance)
