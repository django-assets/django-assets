"""Instruments admin surface (instruments spec §5)."""

from django.contrib import admin

from django_assets.instruments.crypto.models import CryptoMeta
from django_assets.instruments.currencies.models import CurrencyMeta
from django_assets.instruments.equities.models import EquityMeta
from django_assets.instruments.models import CorporateAction
from django_assets.instruments.options.models import Deliverable, OptionMeta


@admin.register(CorporateAction)
class CorporateActionAdmin(admin.ModelAdmin):
    list_display = ("effective_date", "action_type", "primary_instrument", "source_reference")
    list_filter = ("action_type",)
    date_hierarchy = "effective_date"
    search_fields = ("description", "source_reference")


@admin.register(CurrencyMeta)
class CurrencyMetaAdmin(admin.ModelAdmin):
    list_display = ("iso_code", "instrument", "symbol", "is_fiat", "central_bank")
    list_filter = ("is_fiat",)
    search_fields = ("iso_code",)


@admin.register(CryptoMeta)
class CryptoMetaAdmin(admin.ModelAdmin):
    list_display = ("symbol", "instrument", "network", "is_stablecoin", "pegged_to")
    list_filter = ("is_stablecoin", "network")
    search_fields = ("symbol",)


@admin.register(EquityMeta)
class EquityMetaAdmin(admin.ModelAdmin):
    list_display = ("instrument", "primary_exchange")
    list_filter = ("primary_exchange",)
    search_fields = ("instrument__code",)


class DeliverableInline(admin.TabularInline):
    model = Deliverable
    extra = 0


@admin.register(OptionMeta)
class OptionMetaAdmin(admin.ModelAdmin):
    list_display = ("instrument", "underlying", "expiry", "strike", "right")
    list_filter = ("right", "settlement_type", "exercise_style")
    date_hierarchy = "expiry"
    search_fields = ("instrument__code", "underlying__code")
    inlines = [DeliverableInline]
