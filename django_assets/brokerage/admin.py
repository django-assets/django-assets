"""Brokerage admin surface."""

from django.contrib import admin

from django_assets.brokerage.models import AccountProfile


@admin.register(AccountProfile)
class AccountProfileAdmin(admin.ModelAdmin):
    list_display = (
        "account",
        "subtype",
        "allows_short",
        "allows_margin",
        "is_tax_advantaged",
        "allows_reconciliation",
    )
    list_filter = ("subtype", "allows_reconciliation", "is_tax_advantaged")
    search_fields = ("account__name",)
