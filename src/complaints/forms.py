from django import forms

from .models import Category, Complaint


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
