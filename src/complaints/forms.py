from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import Category, Complaint


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
    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(is_active=True),
        empty_label="Select a category",
    )

    class Meta:
        model = Complaint
        fields = ["title", "description", "category", "priority"]

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
