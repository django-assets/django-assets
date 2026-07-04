"""Trades admin surface (spec §6, ADR-0017). Core registrations untouched."""

from django.contrib import admin

from django_assets.trades.models import (
    Tag,
    TagCategory,
    Trade,
    TradeAllocation,
    VirtualEntry,
    VirtualTransfer,
)


class TradeAllocationInline(admin.TabularInline):
    model = TradeAllocation
    extra = 0
    autocomplete_fields = ("leg",)
    fields = ("leg", "amount", "category")


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "parent", "derived_status", "positions")
    list_filter = ("user", "tags__category", ("parent", admin.EmptyFieldListFilter))
    search_fields = ("name",)
    filter_horizontal = ("tags",)
    inlines = [TradeAllocationInline]
    readonly_fields = ("derived_status", "positions", "consistency")

    @admin.display(description="status (derived, slow)")
    def derived_status(self, obj: Trade) -> str:
        return obj.status

    @admin.display(description="net positions (derived, slow)")
    def positions(self, obj: Trade) -> str:
        parts = [f"{obj.net_position(inst)} {inst.code}" for inst in obj.tracked_instruments()]
        return ", ".join(parts) or "flat"

    @admin.display(description="consistency")
    def consistency(self, obj: Trade) -> str:
        report = obj.check_consistency()
        issues = report["errors"] + report["warnings"]
        return "; ".join(issues) or "ok"


class TagInline(admin.TabularInline):
    model = Tag
    extra = 0


@admin.register(TagCategory)
class TagCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "user")
    list_filter = ("user",)
    inlines = [TagInline]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "category")
    list_filter = ("category",)


@admin.register(TradeAllocation)
class TradeAllocationAdmin(admin.ModelAdmin):
    list_display = ("trade", "leg", "amount", "category")
    list_filter = ("category", "trade", "leg__instrument")


class VirtualEntryInline(admin.TabularInline):
    model = VirtualEntry
    extra = 0


@admin.register(VirtualTransfer)
class VirtualTransferAdmin(admin.ModelAdmin):
    """Saves re-validate the zero-sum rule — the deferred trigger fires
    at the change-view's COMMIT, so an unbalanced inline edit rolls the
    whole save back with an IntegrityError message."""

    list_display = ("user", "timestamp", "description")
    list_filter = ("user",)
    date_hierarchy = "timestamp"
    inlines = [VirtualEntryInline]
