
from django.urls import path, register_converter

from . import views


class ReferenceIdConverter:
    """Matches a complaint reference ID: SCMS-YYYY-NNNNNN."""

    regex = r"SCMS-\d{4}-\d{6}"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return str(value)


register_converter(ReferenceIdConverter, "ref")

app_name = "complaints"

urlpatterns = [
    path("", views.complaint_list, name="my_complaints"),
    path("new/", views.complaint_create, name="complaint_create"),
    path("queue/", views.department_queue, name="department_queue"),
    path(
        "<ref:reference_id>/",
        views.complaint_detail,
        name="complaint_detail",
    ),
]
