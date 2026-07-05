"""Lots admin (spec L5): derived rows are read-mostly — rebuilt, never
hand-edited; the linkage rows ARE editable (the manual API's surface)."""

from django.contrib import admin

from django_assets.lots.models import (
    ConversionLink,
    ExerciseLink,
    Lot,
    LotEvent,
    LotMatch,
    WashSaleAdjustment,
)


class ReadMostlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Lot)
class LotAdmin(ReadMostlyAdmin):
    list_display = (
        "account",
        "instrument",
        "direction",
        "acquired_at",
        "quantity_remaining",
        "cost_basis_remaining",
        "rollover_linked",
    )
    list_filter = ("direction", "instrument", "rollover_linked")


@admin.register(LotMatch)
class LotMatchAdmin(ReadMostlyAdmin):
    list_display = ("lot", "quantity", "proceeds", "basis_recovered", "realized_gain", "term")
    list_filter = ("term",)


@admin.register(LotEvent)
class LotEventAdmin(ReadMostlyAdmin):
    list_display = ("lot", "event_type", "ratio", "quantity_before", "quantity_after")
    list_filter = ("event_type",)


@admin.register(WashSaleAdjustment)
class WashSaleAdjustmentAdmin(ReadMostlyAdmin):
    list_display = ("loss_match", "replacement_lot", "disallowed_loss")


@admin.register(ExerciseLink)
class ExerciseLinkAdmin(admin.ModelAdmin):
    list_display = ("transaction", "delivered_leg", "option_instrument", "source")
    list_filter = ("source",)


@admin.register(ConversionLink)
class ConversionLinkAdmin(admin.ModelAdmin):
    list_display = ("transaction", "from_instrument", "to_instrument", "source")
    list_filter = ("source",)
