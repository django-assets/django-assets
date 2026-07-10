from django.urls import path

from dev_project.optiontracker import views

app_name = "optiontracker"

urlpatterns = [
    path("", views.option_positions, name="positions"),
    path("wheel/", views.wheel, name="wheel"),
    path("equities/", views.equities, name="equities"),
    path("analytics/", views.analytics, name="analytics"),
    path("analytics/flow/", views.pnl_flow_view, name="pnl-flow"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("history/", views.history, name="history"),
    path("broker/", views.broker, name="broker"),
]
