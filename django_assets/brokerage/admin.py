"""Brokerage admin surface, incl. the review queue (spec §7)."""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path

from django_assets.brokerage.models import (
    AccountProfile,
    ImportBatch,
    ImportLine,
    ImportLineProposal,
    TransactionImport,
)
from django_assets.brokerage.schemas import registry
from django_assets.core.admin import TransactionLegAdmin
from django_assets.core.models import TransactionLeg


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


class MatchedFilter(admin.SimpleListFilter):
    """Matched/unmatched queue filter (spec §7)."""

    title = "matched"
    parameter_name = "matched"

    def lookups(self, request, model_admin):
        return [("matched", "Matched"), ("unmatched", "Unmatched")]

    def queryset(self, request, queryset):
        if self.value() == "matched":
            return queryset.filter(matched_legs__isnull=False).distinct()
        if self.value() == "unmatched":
            return queryset.filter(kind__startswith="broker_", matched_legs__isnull=True)
        return queryset


def schema_registry_view(request):
    """Read-only registry listing: four-part key, definition, class path."""
    rows = "".join(
        f"<tr><td>{broker}</td><td>{doc}</td><td>{fmt}</td><td>{version}</td>"
        f"<td>{type(schema).__module__}.{type(schema).__name__}</td>"
        f"<td><code>{schema.definition}</code></td></tr>"
        for (broker, doc, fmt, version), schema in sorted(registry._schemas.items())
    )
    return HttpResponse(
        "<h1>Import schema registry (read-only)</h1>"
        "<table><tr><th>broker</th><th>document</th><th>format</th>"
        f"<th>version</th><th>class</th><th>definition</th></tr>{rows}</table>"
    )


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

    def get_urls(self):
        custom = [
            path(
                "schema-registry/",
                self.admin_site.admin_view(schema_registry_view),
                name="django_assets_schema_registry",
            ),
        ]
        return custom + super().get_urls()


@admin.register(ImportLine)
class ImportLineAdmin(admin.ModelAdmin):
    list_display = ("batch", "line_number", "kind", "source_reference", "match_count")
    list_filter = (MatchedFilter, "kind")
    filter_horizontal = ("matched_legs",)  # the manual-match surface

    @admin.display(description="matched legs")
    def match_count(self, obj):
        return obj.matched_legs.count()


@admin.register(TransactionImport)
class TransactionImportAdmin(admin.ModelAdmin):
    list_display = ("transaction", "batch", "external_id")
    search_fields = ("external_id",)


class LockAwareTransactionLegAdmin(TransactionLegAdmin):
    """Core's leg admin + the visual locked-leg indication (ADR-0024).

    Registered here so core keeps zero reconciliation awareness."""

    list_display = (*TransactionLegAdmin.list_display, "reconciled")

    @admin.display(boolean=True)
    def reconciled(self, obj):
        return obj.reconciliation_lines.exists()


admin.site.unregister(TransactionLeg)
admin.site.register(TransactionLeg, LockAwareTransactionLegAdmin)


@admin.register(ImportLineProposal)
class ImportLineProposalAdmin(admin.ModelAdmin):
    """One-at-a-time review happens through current_proposal ordering;
    this changelist is the audit surface."""

    list_display = (
        "line",
        "candidate_transaction",
        "rank",
        "score_total",
        "compound_kind",
        "resolution",
    )
    list_filter = ("resolution", "compound_kind")
    readonly_fields = ("score_breakdown", "proposal_group", "created_at", "resolved_at")
