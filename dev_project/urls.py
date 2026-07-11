from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("tracker/", include("dev_project.optiontracker.urls")),
]
