"""Brokerage admin surface."""

from django.contrib import admin

from django_assets.brokerage.models import (
    AccountProfile,
    ImportBatch,
    ImportLine,
    TransactionImport,
)


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


class ImportLineInline(admin.TabularInline):
    model = ImportLine
    extra = 0
    fields = ("line_number", "kind", "source_reference", "note")
    readonly_fields = fields
    can_delete = False


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "account",
        "schema_broker",
        "schema_document_kind",
        "period_start",
        "period_end",
        "transaction_count",
        "imported_at",
    )
    list_filter = ("schema_broker", "schema_document_kind")
    date_hierarchy = "imported_at"
    search_fields = ("file_name",)
    inlines = [ImportLineInline]


@admin.register(ImportLine)
class ImportLineAdmin(admin.ModelAdmin):
    list_display = ("batch", "line_number", "kind", "source_reference")
    list_filter = ("kind",)


@admin.register(TransactionImport)
class TransactionImportAdmin(admin.ModelAdmin):
    list_display = ("transaction", "batch", "external_id")
    search_fields = ("external_id",)
