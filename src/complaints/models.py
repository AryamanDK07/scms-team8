
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from .roles import Role


class ComplaintPriority(models.TextChoices):
    """The four priorities in the SRS priority matrix (SCMS-F-013).

    Defined at module level because both Category (which holds the
    default for its complaints) and Complaint (which holds the
    actual value) need it, and Category is declared first.
    Complaint.Priority aliases this, so existing references keep
    working.
    """

    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


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
        choices=ComplaintPriority.choices,
        default=ComplaintPriority.MEDIUM,
        help_text="Default priority from the priority matrix (SCMS-F-013).",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class SubCategory(models.Model):
    """Master data: a narrower classification within a Category.

    SCMS-F-006 captures a sub-category alongside the category.
    Like Category, these are editable rows rather than constants in
    code (ADR-06), so an administrator can add one without a
    redeployment.

    Routing and SLA stay on the parent Category: a sub-category
    refines what the complaint is about, not who handles it.
    """

    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=30)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="sub_categories",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category__name", "name"]
        verbose_name_plural = "sub-categories"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "code"],
                name="unique_subcategory_code_per_category",
            ),
            models.UniqueConstraint(
                fields=["category", "name"],
                name="unique_subcategory_name_per_category",
            ),
        ]

    def __str__(self):
        return f"{self.category.name} / {self.name}"


class ReferenceSequence(models.Model):
    """Per-year counter backing the SCMS-YYYY-NNNNNN reference ID."""

    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-year"]

    def __str__(self):
        return f"{self.year}: {self.last_number}"


class Complaint(models.Model):

    #: Alias so Complaint.Priority.* keeps resolving.
    Priority = ComplaintPriority

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

    # SCMS-F-006 requires a sub-category and a location on the
    # lodging form. Both are nullable/blank at the database level so
    # that complaints recorded before this field existed remain
    # valid; ComplaintForm makes them mandatory on the form, which
    # is where the requirement applies.
    sub_category = models.ForeignKey(
        SubCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="complaints",
    )
    location = models.CharField(
        max_length=200,
        blank=True,
        help_text="Where the issue is, e.g. Block C, Room 302.",
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
    resolution_remarks = models.TextField(
        blank=True,
        help_text=(
            "What was done, in at least 50 characters (SCMS-F-018)."
        ),
    )
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


class AppendOnlyQuerySet(models.QuerySet):
    """Refuses the bulk operations that bypass Model.save/delete.

    Overriding save() and delete() on the model alone is not enough
    for SCMS-SR-006: QuerySet.delete() and QuerySet.update() go
    straight to SQL without calling either, so a single
    .all().delete() would erase the audit trail while the model
    looked protected. These refuse at the queryset level too.

    This is application-level enforcement. The database-level half
    of SCMS-SR-006 -- withholding UPDATE and DELETE from the
    application's database role -- is deployment configuration and
    is tracked separately in T8-48.
    """

    APPEND_ONLY = (
        "Audit rows are append-only and cannot be {} "
        "(SCMS-SR-006)."
    )

    def delete(self):
        raise ValueError(self.APPEND_ONLY.format("deleted"))

    def update(self, **kwargs):
        raise ValueError(self.APPEND_ONLY.format("modified"))


class ComplaintStatusHistory(models.Model):
    """Append-only audit trail of complaint state changes.

    SCMS-F-027 and SCMS-SR-006: rows are written once and never
    updated or deleted through the application.
    """

    objects = AppendOnlyQuerySet.as_manager()

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
    # SCMS-F-027 asks for before and after values on every change,
    # not only on status moves. old_status/new_status stay as they
    # are, since the tracking page reads them; these three carry the
    # same information for a change to any other field, so a
    # priority or ownership change is a first-class audit row rather
    # than a sentence in the comment.
    field_name = models.CharField(
        max_length=40,
        default="status",
        help_text="The field this row records a change to.",
    )
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)

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


class ComplaintComment(models.Model):
    """A message on a complaint (SCMS-F-017).

    Threading is one level deep: a comment may reply to a top-level
    comment, but not to another reply. Unbounded nesting makes a
    conversation harder to follow, not easier, and nothing in the
    requirement asks for it.

    Who may read or write a comment follows the complaint itself,
    through the existing object-level check, so a comment is visible
    to the complainant and the assigned agent and to no other
    complainant.
    """

    BODY_LIMIT = 2000

    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.PROTECT,
        related_name="comments",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replies",
        help_text="The comment this one replies to, if any.",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="complaint_comments",
    )
    body = models.TextField(max_length=BODY_LIMIT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.complaint.reference_id}: {self.author}"


def attachment_path(instance, filename):
    """Store under a random name, keyed by complaint reference.

    The name a complainant uploaded is kept in original_name for
    display; the file on disk gets a random one so that the stored
    path reveals nothing and two uploads of "photo.jpg" cannot
    collide.
    """
    suffix = Path(filename).suffix.lower()

    return (
        f"complaints/{instance.complaint.reference_id}/"
        f"{uuid4().hex}{suffix}"
    )


class AttachmentKind(models.TextChoices):
    """What an attachment is for.

    SCMS-F-008 caps a complainant's evidence at three files.
    Resolution proof under SCMS-F-018 is counted separately: if the
    two shared one allowance, a complainant who attached three files
    would make their own complaint impossible to resolve.
    """

    EVIDENCE = "EVIDENCE", "Complainant evidence"
    PROOF = "PROOF", "Resolution proof"


class ComplaintAttachment(models.Model):
    """Evidence supporting a complaint (SCMS-F-008).

    Files live under MEDIA_ROOT, which is outside the web root and
    not served directly; they are read back through a view that
    checks access first, so one complainant cannot fetch another's
    evidence by guessing a URL.
    """

    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.PROTECT,
        related_name="attachments",
    )
    kind = models.CharField(
        max_length=10,
        choices=AttachmentKind.choices,
        default=AttachmentKind.EVIDENCE,
    )
    file = models.FileField(upload_to=attachment_path)
    original_name = models.CharField(
        max_length=255,
        help_text="The filename as uploaded, for display.",
    )
    content_type = models.CharField(
        max_length=100,
        help_text="Determined by reading the file, not from the client.",
    )
    size_bytes = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaint_attachments",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return f"{self.complaint.reference_id}: {self.original_name}"


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
