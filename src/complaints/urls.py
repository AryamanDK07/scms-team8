
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
    path(
        "<ref:reference_id>/assign/",
        views.complaint_assign,
        name="complaint_assign",
    ),
    path(
        "<ref:reference_id>/priority/",
        views.complaint_priority,
        name="complaint_priority",
    ),
    path(
        "<ref:reference_id>/status/",
        views.complaint_status,
        name="complaint_status",
    ),
    path(
        "<ref:reference_id>/comment/",
        views.complaint_comment_add,
        name="complaint_comment_add",
    ),
    path(
        "<ref:reference_id>/attach/",
        views.complaint_attachment_add,
        name="complaint_attachment_add",
    ),
    path(
        "<ref:reference_id>/attachments/<int:pk>/",
        views.complaint_attachment_download,
        name="complaint_attachment_download",
    ),
]
