"""DRF serializers (spec §9, ADR-0017): serializers only — no viewsets,
no urls, no auth assumptions.

DRF is the optional `django-assets[drf]` extra; this module is the only
one that imports rest_framework, and it fails with an actionable message
when DRF is absent [D-8]. Core never imports this module.
"""

from collections.abc import Callable
from typing import Any

try:
    from rest_framework import serializers
except ImportError as exc:  # pragma: no cover — exercised via subprocess test
    raise ImportError(
        "django_assets.serializers requires djangorestframework. "
        'Install the extra: pip install "django-assets[drf]".'
    ) from exc

from django_assets.brokerage.models import (
    AccountProfile,
    DisclosureEvent,
    ImportBatch,
    ImportLine,
    ImportLineProposal,
    TransactionImport,
)
from django_assets.core.builder import TransactionBuilder
from django_assets.core.measure import Measure
from django_assets.core.models import (
    Account,
    Exchange,
    Identifier,
    Instrument,
    Transaction,
    TransactionLeg,
)
from django_assets.instruments.crypto.models import CryptoMeta
from django_assets.instruments.currencies.models import CurrencyMeta
from django_assets.instruments.equities.models import EquityMeta
from django_assets.instruments.models import CorporateAction
from django_assets.instruments.options.models import Deliverable, OptionMeta
from django_assets.lots.models import Lot, LotMatch
from django_assets.trades.models import (
    Tag,
    TagCategory,
    Trade,
    TradeAllocation,
    VirtualEntry,
    VirtualTransfer,
)

try:
    from drf_spectacular.utils import extend_schema_field
except ImportError:  # drf-spectacular is optional on top of the drf extra

    def extend_schema_field(field: Any) -> Callable[[Any], Any]:
        def decorator(cls: Any) -> Any:
            return cls

        return decorator


class ExchangeSerializer(serializers.ModelSerializer[Exchange]):
    class Meta:
        model = Exchange
        fields = ["id", "code", "name", "timezone"]


class InstrumentSerializer(serializers.ModelSerializer[Instrument]):
    class Meta:
        model = Instrument
        fields = [
            "id",
            "code",
            "quantity_decimals",
            "price_decimals",
            "multiplier",
            "price_currency",
            "is_active",
            "metadata",
        ]


class IdentifierSerializer(serializers.ModelSerializer[Identifier]):
    class Meta:
        model = Identifier
        fields = [
            "id",
            "instrument",
            "type",
            "value",
            "exchange",
            "is_active",
            "effective_from",
            "effective_to",
        ]


class AccountSerializer(serializers.ModelSerializer[Account]):
    class Meta:
        model = Account
        fields = ["id", "owner", "name", "created_at", "metadata"]
        read_only_fields = ["created_at"]


@extend_schema_field(
    {"type": "object", "properties": {"amount": {"type": "string"}, "unit": {"type": "string"}}}
)
class MeasureField(serializers.Field[Measure, Any, dict[str, str], Any]):
    """{"amount": "12.3456", "unit": "USD"} — read-only computed value."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("read_only", True)
        super().__init__(**kwargs)

    def to_representation(self, value: Measure) -> dict[str, str]:
        return {"amount": str(value.amount), "unit": value.unit.code}


class TransactionLegSerializer(serializers.ModelSerializer[TransactionLeg]):
    class Meta:
        model = TransactionLeg
        fields = ["id", "account", "instrument", "amount", "description", "metadata"]


class TransactionSerializer(serializers.ModelSerializer[Transaction]):
    """Whole-transaction read/write; the write path goes through
    TransactionBuilder so every guard (intake, quantization, ownership,
    balance) applies."""

    legs = TransactionLegSerializer(many=True)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "account",
            "timestamp",
            "trade_timestamp",
            "description",
            "metadata",
            "origin",
            "legs",
        ]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        sums: dict[Instrument, Any] = {}
        for leg in attrs.get("legs", []):
            inst = leg["instrument"]
            sums[inst] = sums.get(inst, 0) + leg["amount"]
        off = {inst.code: str(total) for inst, total in sums.items() if total != 0}
        if off:
            raise serializers.ValidationError(
                f"transaction legs are not balanced per instrument: {off}"
            )
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Transaction:
        legs = validated_data.pop("legs")
        builder = TransactionBuilder(
            account=validated_data["account"],
            timestamp=validated_data["timestamp"],
            trade_timestamp=validated_data.get("trade_timestamp"),
            description=validated_data.get("description", ""),
            metadata=validated_data.get("metadata"),
            origin=validated_data.get("origin", "manual"),
        )
        with builder as b:
            for leg in legs:
                b.add_leg(
                    account=leg["account"],
                    instrument=leg["instrument"],
                    amount=leg["amount"],
                    description=leg.get("description", ""),
                    metadata=leg.get("metadata"),
                )
        assert b.transaction is not None
        return b.transaction


class HoldingSerializer(serializers.Serializer[Any]):
    """Computed position: {"account": pk, "instrument": code, "quantity": "…"}."""

    account: "serializers.PrimaryKeyRelatedField[Account]" = serializers.PrimaryKeyRelatedField(
        read_only=True
    )
    instrument: "serializers.SlugRelatedField[Instrument]" = serializers.SlugRelatedField(
        slug_field="code", read_only=True
    )
    quantity = serializers.CharField(read_only=True)


class PortfolioSerializer(serializers.Serializer[Any]):
    """Computed snapshot: positions keyed by instrument code, decimals as
    strings (Portfolio.at output)."""

    account: "serializers.PrimaryKeyRelatedField[Account]" = serializers.PrimaryKeyRelatedField(
        read_only=True
    )
    positions = serializers.SerializerMethodField()

    def get_positions(self, obj: dict[str, Any]) -> dict[str, str]:
        return {inst.code: str(qty) for inst, qty in obj["positions"].items()}


class CorporateActionSerializer(serializers.ModelSerializer[CorporateAction]):
    class Meta:
        model = CorporateAction
        fields = [
            "id",
            "effective_date",
            "action_type",
            "source_reference",
            "description",
            "metadata",
            "primary_instrument",
        ]


class CurrencyMetaSerializer(serializers.ModelSerializer[CurrencyMeta]):
    class Meta:
        model = CurrencyMeta
        fields = [
            "id",
            "instrument",
            "iso_code",
            "iso_numeric",
            "symbol",
            "is_fiat",
            "central_bank",
        ]


class CryptoMetaSerializer(serializers.ModelSerializer[CryptoMeta]):
    class Meta:
        model = CryptoMeta
        fields = [
            "id",
            "instrument",
            "symbol",
            "network",
            "contract_address",
            "is_stablecoin",
            "pegged_to",
        ]


class EquityMetaSerializer(serializers.ModelSerializer[EquityMeta]):
    class Meta:
        model = EquityMeta
        fields = ["id", "instrument", "primary_exchange", "metadata"]


class DeliverableSerializer(serializers.ModelSerializer[Deliverable]):
    class Meta:
        model = Deliverable
        fields = [
            "id",
            "option_meta",
            "sequence",
            "instrument",
            "quantity",
            "cash_currency",
            "cash_amount",
            "effective_from",
            "effective_to",
            "corporate_action",
        ]


class OptionMetaSerializer(serializers.ModelSerializer[OptionMeta]):
    deliverables = DeliverableSerializer(many=True, read_only=True)

    class Meta:
        model = OptionMeta
        fields = [
            "id",
            "instrument",
            "underlying",
            "expiry",
            "strike",
            "right",
            "settlement_type",
            "exercise_style",
            "deliverables",
        ]


class AccountProfileSerializer(serializers.ModelSerializer[AccountProfile]):
    class Meta:
        model = AccountProfile
        fields = [
            "id",
            "account",
            "subtype",
            "allows_short",
            "allows_margin",
            "is_tax_advantaged",
            "allows_reconciliation",
            "tax_treatment",
            "metadata",
        ]


class ImportBatchSerializer(serializers.ModelSerializer[ImportBatch]):
    class Meta:
        model = ImportBatch
        fields = [
            "id",
            "account",
            "schema_broker",
            "schema_document_kind",
            "schema_format_kind",
            "schema_version",
            "period_start",
            "period_end",
            "file_name",
            "file_hash",
            "imported_at",
            "transaction_count",
            "notes",
            "metadata",
        ]


class TransactionImportSerializer(serializers.ModelSerializer[TransactionImport]):
    class Meta:
        model = TransactionImport
        fields = ["id", "transaction", "batch", "external_id", "content_hash", "source_data"]


class ImportLineSerializer(serializers.ModelSerializer[ImportLine]):
    class Meta:
        model = ImportLine
        fields = [
            "id",
            "batch",
            "line_number",
            "raw_data",
            "kind",
            "source_reference",
            "note",
            "matched_legs",
            "metadata",
        ]


class ImportLineProposalSerializer(serializers.ModelSerializer[ImportLineProposal]):
    class Meta:
        model = ImportLineProposal
        fields = [
            "id",
            "line",
            "candidate_transaction",
            "score_total",
            "score_breakdown",
            "rank",
            "proposal_group",
            "compound_kind",
            "created_at",
            "resolved_at",
            "resolution",
        ]


class DisclosureEventSerializer(serializers.ModelSerializer[DisclosureEvent]):
    class Meta:
        model = DisclosureEvent
        fields = [
            "id",
            "transaction",
            "source",
            "source_reference",
            "disclosed_at",
            "effective_date",
            "note",
        ]


class TagSerializer(serializers.ModelSerializer[Tag]):
    class Meta:
        model = Tag
        fields = ["id", "category", "name", "description"]


class TagCategorySerializer(serializers.ModelSerializer[TagCategory]):
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = TagCategory
        fields = ["id", "user", "code", "name", "description", "tags"]


class TradeAllocationSerializer(serializers.ModelSerializer[TradeAllocation]):
    class Meta:
        model = TradeAllocation
        fields = ["id", "trade", "leg", "amount", "category", "metadata"]


class TradeSerializer(serializers.ModelSerializer[Trade]):
    """Derived fields are read-only; P&L is ONE unified number
    (ADR-0031 — no separate virtual component is exposed)."""

    tags = serializers.SerializerMethodField()
    children: "serializers.PrimaryKeyRelatedField[Any]" = serializers.PrimaryKeyRelatedField(
        many=True, read_only=True
    )
    status = serializers.SerializerMethodField()
    realized_pnl = serializers.SerializerMethodField()

    class Meta:
        model = Trade
        fields = [
            "id",
            "user",
            "name",
            "parent",
            "children",
            "description",
            "metadata",
            "created_at",
            "tags",
            "status",
            "realized_pnl",
        ]

    def get_tags(self, obj: Trade) -> dict[str, list[str]]:
        return obj.get_tags_by_category()

    def get_status(self, obj: Trade) -> str:
        return obj.status

    def get_realized_pnl(self, obj: Trade) -> str:
        return str(obj.calculate_pnl()["realized_pnl"])


class VirtualEntrySerializer(serializers.ModelSerializer[VirtualEntry]):
    class Meta:
        model = VirtualEntry
        fields = ["id", "transfer", "trade", "instrument", "amount", "category", "metadata"]
        read_only_fields = ["transfer"]


class VirtualTransferSerializer(serializers.ModelSerializer[VirtualTransfer]):
    entries = VirtualEntrySerializer(many=True, read_only=True)

    class Meta:
        model = VirtualTransfer
        fields = ["id", "user", "timestamp", "description", "metadata", "entries"]


class LotSerializer(serializers.ModelSerializer[Lot]):
    instrument_code = serializers.CharField(source="instrument.code", read_only=True)

    class Meta:
        model = Lot
        fields = [
            "id",
            "account",
            "instrument",
            "instrument_code",
            "acquired_at",
            "quantity",
            "quantity_remaining",
            "cost_basis",
            "cost_basis_remaining",
            "direction",
            "rollover_linked",
            "metadata",
        ]


class LotMatchSerializer(serializers.ModelSerializer[LotMatch]):
    class Meta:
        model = LotMatch
        fields = [
            "id",
            "lot",
            "closing_leg",
            "quantity",
            "proceeds",
            "basis_recovered",
            "realized_gain",
            "term",
            "metadata",
        ]
