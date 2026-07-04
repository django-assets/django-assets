"""Brokerage admin surface, incl. the review queue (spec §7)."""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path
from django.utils.html import format_html, format_html_join

from django_assets.brokerage.models import (
    AccountProfile,
    DisclosureEvent,
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
    """Read-only registry listing: four-part key, definition, class path.
    Escaped via format_html — third-party schema classes are still
    untrusted text here."""
    rows = format_html_join(
        "",
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
        "<td><code>{}</code></td></tr>",
        (
            (
                broker,
                doc,
                fmt,
                version,
                f"{type(schema).__module__}.{type(schema).__name__}",
                str(schema.definition),
            )
            for (broker, doc, fmt, version), schema in sorted(registry._schemas.items())
        ),
    )
    return HttpResponse(
        format_html(
            "<h1>Import schema registry (read-only)</h1>"
            "<table><tr><th>broker</th><th>document</th><th>format</th>"
            "<th>version</th><th>class</th><th>definition</th></tr>{}</table>",
            rows,
        )
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


def _render_snapshot(title: str, snapshot: dict) -> HttpResponse:
    """Structured reconstruction view — a record, never a JSON dump.
    Descriptions and names are user text: everything interpolates through
    format_html so it autoescapes."""
    rows = format_html_join(
        "",
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
        (
            (leg["account_name"], leg["instrument_code"], leg["amount"], leg["description"])
            for leg in snapshot["legs"]
        ),
    )
    return HttpResponse(
        format_html(
            "<h1>{}</h1><p>{} — {} (origin: {})</p>"
            "<table><tr><th>account</th><th>instrument</th><th>amount</th>"
            "<th>description</th></tr>{}</table>",
            title,
            snapshot["description"],
            snapshot["timestamp"],
            snapshot["origin"],
            rows,
        )
    )


def disclosure_before_view(request, event_id):
    from django_assets.brokerage.disclosure import reconstruct_before

    event = DisclosureEvent.objects.get(pk=event_id)
    return _render_snapshot(
        f"Before {event.source} ({event.disclosed_at:%Y-%m-%d})",
        reconstruct_before(event),
    )


def transaction_original_view(request, transaction_id):
    from django_assets.brokerage.disclosure import reconstruct_original
    from django_assets.core.models import Transaction

    tx = Transaction.objects.get(pk=transaction_id)
    return _render_snapshot("As imported", reconstruct_original(tx))


@admin.register(DisclosureEvent)
class DisclosureEventAdmin(admin.ModelAdmin):
    list_display = ("transaction", "source", "source_reference", "disclosed_at", "effective_date")
    list_filter = ("source",)
    date_hierarchy = "disclosed_at"
    readonly_fields = ("snapshot_before", "disclosed_at")

    def get_urls(self):
        custom = [
            path(
                "<int:event_id>/before/",
                self.admin_site.admin_view(disclosure_before_view),
                name="django_assets_disclosure_before",
            ),
            path(
                "transaction/<int:transaction_id>/original/",
                self.admin_site.admin_view(transaction_original_view),
                name="django_assets_transaction_original",
            ),
        ]
        return custom + super().get_urls()
