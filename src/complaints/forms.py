from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from .attachments import AttachmentRejected, validate_upload
from .models import Category, Complaint, SubCategory
from .services import eligible_agents, offerable_transitions


class EmailLoginForm(AuthenticationForm):
    """Login form that asks for an e-mail address (SCMS-F-002).

    AuthenticationForm calls its first field ``username``; the field
    is relabelled and validated as an e-mail address here, and
    EmailBackend resolves it to an account.
    """

    username = forms.EmailField(
        label="E-mail address",
        widget=forms.EmailInput(attrs={
            "autofocus": True,
            "autocomplete": "email",
            "placeholder": "you@example.com",
        }),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": (
            "The e-mail address or password is incorrect. "
            "Please try again."
        ),
    }


class PriorityOverrideForm(forms.Form):
    """Override a complaint's matrix priority (SCMS-F-013).

    The reason is always mandatory here. Unlike a first assignment,
    there is no "first" override: every one of these departs from
    the matrix and needs to say why.
    """

    priority = forms.ChoiceField(
        choices=Complaint.Priority.choices,
        label="New priority",
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "rows": 3,
            "placeholder": "Why does this need a different priority?",
        }),
        help_text="Recorded in the audit trail with the old and new value.",
    )

    def __init__(self, *args, complaint=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.complaint = complaint

    def clean_priority(self):
        priority = self.cleaned_data["priority"]

        if self.complaint is not None and priority == self.complaint.priority:
            raise forms.ValidationError(
                f"This complaint is already "
                f"{self.complaint.get_priority_display()}. "
                f"Choose a different priority."
            )

        return priority

    def clean_reason(self):
        reason = self.cleaned_data.get("reason", "")

        if not reason.strip():
            raise forms.ValidationError(
                "Give a reason for departing from the category's "
                "default priority."
            )

        return reason


class AttachmentForm(forms.Form):
    """Upload one piece of evidence (SCMS-F-008)."""

    file = forms.FileField(
        label="Evidence file",
        help_text=(
            "JPG, PNG or PDF, up to 5 MB. Three attachments maximum."
        ),
    )

    def __init__(self, *args, complaint=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.complaint = complaint

    def clean_file(self):
        upload = self.cleaned_data["file"]

        existing = (
            self.complaint.attachments.count()
            if self.complaint is not None
            else 0
        )

        try:
            self.detected_format = validate_upload(upload, existing)
        except AttachmentRejected as rejection:
            # The message names the rule that was broken, which is
            # what the acceptance criterion asks for.
            raise forms.ValidationError(str(rejection))

        return upload


class StatusChangeForm(forms.Form):
    """Move a complaint along the state machine (SCMS-F-016).

    The choices are built from the complaint's current status, so
    an illegal transition is never offered in the first place.
    """

    status = forms.ChoiceField(choices=(), label="Move to")
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "placeholder": "Anything the complainant should know",
        }),
        help_text="Shown to the complainant on their tracking page.",
    )

    def __init__(self, *args, complaint=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.complaint = complaint
        labels = dict(Complaint.Status.choices)

        if complaint is not None:
            self.fields["status"].choices = [
                (status, labels.get(status, status))
                for status in offerable_transitions(complaint)
            ]


class AssignComplaintForm(forms.Form):
    """Assign or reassign a complaint to an agent (SCMS-F-012).

    The reason is mandatory on a reassignment and optional on a
    first assignment, which is the distinction the requirement
    draws: there is no prior owner to account for the first time.
    """

    agent = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        empty_label="Select an agent",
        label="Support Agent",
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "placeholder": "Why is this being reassigned?",
        }),
        help_text="Required when reassigning.",
    )

    def __init__(self, *args, complaint=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.complaint = complaint
        self.is_reassignment = (
            complaint is not None and complaint.assigned_to_id is not None
        )

        if complaint is not None:
            self.fields["agent"].queryset = eligible_agents(complaint)

        if self.is_reassignment:
            self.fields["reason"].required = True
            self.fields["reason"].help_text = (
                "Recorded in the audit trail against this complaint."
            )

    def clean_agent(self):
        """Reassigning to the current owner is a no-op, not a change."""
        agent = self.cleaned_data["agent"]

        if (
            self.complaint is not None
            and self.complaint.assigned_to_id == agent.pk
        ):
            raise forms.ValidationError(
                f"{agent.get_username()} already has this complaint. "
                f"Choose a different agent."
            )

        return agent

    def clean_reason(self):
        reason = self.cleaned_data.get("reason", "")

        if self.is_reassignment and not reason.strip():
            raise forms.ValidationError(
                "Give a reason for the reassignment."
            )

        return reason


class ComplaintForm(forms.ModelForm):
    """Lodging form for SCMS-F-006.

    Captures category, sub-category, subject, description (capped at
    2000 characters) and location. Every one of them is mandatory
    here, even where the column itself is nullable, because the
    requirement applies to what a complainant submits rather than to
    what the table can hold.
    """

    DESCRIPTION_LIMIT = 2000

    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(is_active=True),
        empty_label="Select a category",
    )
    sub_category = forms.ModelChoiceField(
        queryset=SubCategory.objects.filter(is_active=True),
        empty_label="Select a sub-category",
        required=True,
        label="Sub-category",
    )
    location = forms.CharField(
        max_length=200,
        required=True,
        widget=forms.TextInput(attrs={
            "placeholder": "e.g. Block C, Room 302",
        }),
        help_text="Where the issue is.",
    )

    class Meta:
        model = Complaint
        # Priority is deliberately absent. SCMS-F-013 takes it from
        # the category's priority matrix, and only a Department Admin
        # may override it. Letting a complainant choose their own
        # priority would make the field meaningless for triage.
        fields = [
            "title",
            "category",
            "sub_category",
            "location",
            "description",
        ]

        labels = {
            "title": "Subject",
        }

        widgets = {
            "title": forms.TextInput(attrs={
                "placeholder": "Enter a short complaint title",
            }),
            "description": forms.Textarea(attrs={
                "rows": 5,
                "placeholder": "Describe your complaint clearly",
                "maxlength": 2000,
            }),
        }

    def clean_description(self):
        """Enforce the 2000-character cap server-side.

        The maxlength attribute on the textarea is a convenience for
        the browser and is trivially bypassed, so the limit is
        checked here as well.
        """
        description = self.cleaned_data["description"]

        if len(description) > self.DESCRIPTION_LIMIT:
            raise forms.ValidationError(
                f"Keep the description to "
                f"{self.DESCRIPTION_LIMIT} characters or fewer. "
                f"It is currently {len(description)}."
            )

        return description

    def clean(self):
        """Reject a sub-category that belongs to another category.

        The two dropdowns are populated independently, so nothing
        stops a submitted pair from being inconsistent.
        """
        cleaned = super().clean()

        category = cleaned.get("category")
        sub_category = cleaned.get("sub_category")

        if (
            category
            and sub_category
            and sub_category.category_id != category.pk
        ):
            self.add_error(
                "sub_category",
                f"\"{sub_category.name}\" does not belong to "
                f"{category.name}. Choose a sub-category of the "
                f"selected category.",
            )

        return cleaned
