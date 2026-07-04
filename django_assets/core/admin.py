"""Core admin surface (spec §8, ADR-0017/0022).

Fully editable: the deferred trigger is the integrity gate, and the leg
inline adds a formset pre-check with the same per-instrument zero-sum
rule so unbalanced edits fail as form errors instead of COMMIT-time 500s.
The reversal pattern is documented, not enforced.
"""

from collections import defaultdict
from decimal import Decimal

from django import forms
from django.contrib import admin

from django_assets.core.models import (
    Account,
    Exchange,
    Identifier,
    Instrument,
    Transaction,
    TransactionLeg,
)


@admin.register(Exchange)
class ExchangeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "timezone")
    search_fields = ("code", "name")


@admin.register(Instrument)
class InstrumentAdmin(admin.ModelAdmin):
    list_display = ("code", "quantity_decimals", "price_decimals", "multiplier", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "identifiers__value")


@admin.register(Identifier)
class IdentifierAdmin(admin.ModelAdmin):
    list_display = ("type", "value", "exchange", "instrument", "is_active")
    list_filter = ("type", "exchange", "is_active")
    search_fields = ("value", "instrument__code")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    list_filter = ("owner",)
    search_fields = ("name",)


class TransactionLegInlineFormSet(forms.BaseInlineFormSet):
    """Friendly pre-check mirroring the trigger's rule: per-instrument
    sums of the edited legs must be zero. The trigger still backstops
    every other write path at COMMIT."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        sums = defaultdict(Decimal)
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            instrument = form.cleaned_data["instrument"]
            sums[instrument] += form.cleaned_data["amount"]
        off = {inst.code: str(total) for inst, total in sums.items() if total != 0}
        if off:
            raise forms.ValidationError(
                f"Transaction legs are not balanced per instrument: {off}. "
                f"Every instrument's legs must sum to zero."
            )


class TransactionLegInline(admin.TabularInline):
    model = TransactionLeg
    formset = TransactionLegInlineFormSet
    extra = 0


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "account", "description", "origin")
    list_filter = ("origin", "account")
    date_hierarchy = "timestamp"
    search_fields = ("description",)
    inlines = [TransactionLegInline]

    def get_readonly_fields(self, request, obj=None):
        # origin records provenance; it is set at create and then frozen.
        return ("origin",) if obj is not None else ()


@admin.register(TransactionLeg)
class TransactionLegAdmin(admin.ModelAdmin):
    list_display = ("transaction", "account", "instrument", "amount")
    list_filter = ("instrument",)
