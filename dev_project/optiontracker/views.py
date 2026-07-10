"""Thin views: fetch library reports, hand them to templates. No domain
logic — every number comes from django_assets (see GAPS.md)."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def option_positions(request: HttpRequest) -> HttpResponse:
    return render(request, "optiontracker/positions.html", {})


def wheel(request: HttpRequest) -> HttpResponse:
    return render(request, "optiontracker/wheel.html", {})


def equities(request: HttpRequest) -> HttpResponse:
    return render(request, "optiontracker/equities.html", {})


def analytics(request: HttpRequest) -> HttpResponse:
    return render(request, "optiontracker/analytics.html", {})


def pnl_flow_view(request: HttpRequest) -> HttpResponse:
    return render(request, "optiontracker/pnl_flow.html", {})


def calendar_view(request: HttpRequest) -> HttpResponse:
    return render(request, "optiontracker/calendar.html", {})


def history(request: HttpRequest) -> HttpResponse:
    return render(request, "optiontracker/history.html", {})


def broker(request: HttpRequest) -> HttpResponse:
    return render(request, "optiontracker/broker.html", {})
