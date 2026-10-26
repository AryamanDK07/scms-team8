
from django.urls import path
from . import views

app_name = "complaints"

urlpatterns = [
    path("", views.complaint_list, name="my_complaints"),
    path("new/", views.complaint_create, name="complaint_create"),
    path(
        "<uuid:reference_id>/",
        views.complaint_detail,
        name="complaint_detail",
    ),
]
