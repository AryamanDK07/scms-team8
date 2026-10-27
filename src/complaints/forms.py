from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import Category, Complaint, SubCategory


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
        fields = [
            "title",
            "category",
            "sub_category",
            "location",
            "description",
            "priority",
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
