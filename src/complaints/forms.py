from django import forms
from .models import Complaint


class ComplaintForm(forms.ModelForm):
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
            }),
        }