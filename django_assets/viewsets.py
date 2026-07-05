"""Read-only DRF viewsets (instruments spec §5, ADR-0017 posture).

Host-mounted: the package ships no urls and sets no authentication or
permission classes — hosts wire routers and auth themselves. Import is
DRF-guarded the same way as django_assets.serializers [D-8].
"""

try:
    from rest_framework import status, viewsets
    from rest_framework.decorators import action
    from rest_framework.response import Response
except ImportError as exc:  # pragma: no cover — same posture as serializers
    raise ImportError(
        "django_assets.viewsets requires djangorestframework. "
        'Install the extra: pip install "django-assets[drf]".'
    ) from exc

from typing import Any

from django_assets.brokerage.models import DisclosureEvent, ImportLine
from django_assets.brokerage.reconciliation import match_line, unmatch_line
from django_assets.brokerage.schemas import registry
from django_assets.core.models import (
    Account,
    Exchange,
    Identifier,
    Instrument,
    TransactionLeg,
)
from django_assets.instruments.models import CorporateAction
from django_assets.instruments.options.models import OptionMeta
from django_assets.lots.models import Lot, LotMatch
from django_assets.serializers import (
    AccountSerializer,
    CorporateActionSerializer,
    DisclosureEventSerializer,
    ExchangeSerializer,
    IdentifierSerializer,
    ImportLineProposalSerializer,
    ImportLineSerializer,
    InstrumentSerializer,
    LotMatchSerializer,
    LotSerializer,
    OptionMetaSerializer,
    TradeSerializer,
    VirtualTransferSerializer,
)
from django_assets.trades.models import Trade, VirtualTransfer


class OwnerScopedMixin:
    """Defense-in-depth (D-18 refined): the package still ships no auth
    or permission classes — hosts mount those — but when a host's auth
    HAS authenticated the request, user-owned rows scope to that user
    automatically, so a mounted-but-unscoped router can never leak
    another user's books. Anonymous requests (a host that deliberately
    mounted without auth) see the unscoped queryset — their choice.
    Subclasses set `owner_path` to the user-relation lookup."""

    owner_path: str = ""

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()  # type: ignore[misc]
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        if self.owner_path and user is not None and user.is_authenticated:
            queryset = queryset.filter(**{self.owner_path: user})
        return queryset


class ExchangeViewSet(viewsets.ReadOnlyModelViewSet[Exchange]):
    queryset = Exchange.objects.all()
    serializer_class = ExchangeSerializer


class InstrumentViewSet(viewsets.ReadOnlyModelViewSet[Instrument]):
    queryset = Instrument.objects.all()
    serializer_class = InstrumentSerializer


class IdentifierViewSet(viewsets.ReadOnlyModelViewSet[Identifier]):
    queryset = Identifier.objects.all()
    serializer_class = IdentifierSerializer


class AccountViewSet(OwnerScopedMixin, viewsets.ReadOnlyModelViewSet[Account]):
    owner_path = "owner"
    queryset = Account.objects.all()
    serializer_class = AccountSerializer


class CorporateActionViewSet(viewsets.ReadOnlyModelViewSet[CorporateAction]):
    queryset = CorporateAction.objects.all()
    serializer_class = CorporateActionSerializer


class OptionMetaViewSet(viewsets.ReadOnlyModelViewSet[OptionMeta]):
    queryset = OptionMeta.objects.select_related("instrument", "underlying").prefetch_related(
        "deliverables"
    )
    serializer_class = OptionMetaSerializer


class SchemaRegistryViewSet(viewsets.ViewSet):
    """Read-only registry listing (spec §7); not model-backed."""

    def list(self, request: Any) -> Any:
        return Response(
            [
                {
                    "broker": broker,
                    "document_kind": doc,
                    "format_kind": fmt,
                    "version": version,
                    "name": schema.name,
                    "definition": schema.definition,
                }
                for (broker, doc, fmt, version), schema in sorted(registry._schemas.items())
            ]
        )


class ImportLineViewSet(OwnerScopedMixin, viewsets.ReadOnlyModelViewSet[ImportLine]):
    """The review queue: list/detail plus match/unmatch actions."""

    owner_path = "batch__account__owner"
    queryset = ImportLine.objects.all().order_by("batch_id", "line_number")
    serializer_class = ImportLineSerializer

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()
        if self.request.query_params.get("matched") == "unmatched":
            queryset = queryset.filter(kind__startswith="broker_", matched_legs__isnull=True)
        return queryset

    @staticmethod
    def _leg_ids(request: Any) -> Any:
        data = request.data
        if hasattr(data, "getlist"):
            return data.getlist("legs")
        return data.get("legs", [])

    @action(detail=True, methods=["post"])
    def match(self, request: Any, pk: Any = None) -> Any:
        line = self.get_object()
        # Owner-scoped lookup: pks outside the batch owner's books 404
        # here rather than leak into match_line (IDOR hygiene on top of
        # the helper's own owner guard).
        legs = list(
            TransactionLeg.objects.filter(
                pk__in=self._leg_ids(request),
                account__owner=line.batch.account.owner,
            )
        )
        try:
            match_line(line, legs)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"matched": line.matched_legs.count()})

    @action(detail=True, methods=["post"])
    def unmatch(self, request: Any, pk: Any = None) -> Any:
        line = self.get_object()
        legs = list(TransactionLeg.objects.filter(pk__in=self._leg_ids(request)))
        unmatch_line(line, legs)
        return Response({"matched": line.matched_legs.count()})

    # -- ADR-0029 proposal review: one best proposal at a time ----------

    @action(detail=True, methods=["get"])
    def proposal(self, request: Any, pk: Any = None) -> Any:
        from django_assets.brokerage.review import current_proposal

        best = current_proposal(self.get_object())
        if best is None:
            return Response(None)
        data = ImportLineProposalSerializer(best).data
        data["more_candidates"] = (
            best.line.proposals.filter(resolution="").exclude(pk=best.pk).count()
        )
        return Response(data)

    @action(detail=True, methods=["post"])
    def confirm(self, request: Any, pk: Any = None) -> Any:
        from django_assets.brokerage.models import ImportLineProposal
        from django_assets.brokerage.review import confirm_proposal

        proposal = ImportLineProposal.objects.get(
            pk=request.data.get("proposal"), line=self.get_object()
        )
        try:
            confirm_proposal(proposal)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"resolution": "confirmed"})

    @action(detail=True, methods=["post"])
    def reject(self, request: Any, pk: Any = None) -> Any:
        from django_assets.brokerage.models import ImportLineProposal
        from django_assets.brokerage.review import reject_proposal

        proposal = ImportLineProposal.objects.get(
            pk=request.data.get("proposal"), line=self.get_object()
        )
        next_best = reject_proposal(proposal)
        return Response(ImportLineProposalSerializer(next_best).data if next_best else None)

    @action(detail=True, methods=["post"])
    def materialize(self, request: Any, pk: Any = None) -> Any:
        from django_assets.brokerage.review import materialize_new

        transactions = materialize_new(self.get_object())
        return Response({"transactions": [tx.pk for tx in transactions]})

    @action(detail=True, methods=["post"])
    def override(self, request: Any, pk: Any = None) -> Any:
        from django_assets.brokerage.review import override_match
        from django_assets.core.models import Transaction as CoreTransaction

        line = self.get_object()
        # Override is cross-account/day/batch by design (ADR-0029) but
        # never cross-OWNER: scope the lookup to the batch owner's books.
        target = (
            CoreTransaction.objects.filter(
                pk=request.data.get("transaction"),
                account__owner=line.batch.account.owner,
            )
            .distinct()
            .get()
        )
        try:
            override_match(line, target)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"resolution": "confirmed"})

    @action(detail=True, methods=["post"], url_path="confirm-split")
    def confirm_split(self, request: Any, pk: Any = None) -> Any:
        """Destructive: requires the type-to-confirm word 'replace'
        (the client must render the full manual Transaction first)."""
        from django_assets.brokerage.review import confirm_split as do_split

        group = request.data.get("proposal_group")
        try:
            do_split(group, confirmation=request.data.get("confirmation", ""))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"resolution": "confirmed"})


class TransactionViewSet(OwnerScopedMixin, viewsets.ReadOnlyModelViewSet):  # type: ignore[type-arg]
    """Read-only transactions + the ADR-0023 reconstruction endpoint."""

    from django_assets.core.models import Transaction as _Transaction
    from django_assets.serializers import TransactionSerializer as _TransactionSerializer

    owner_path = "account__owner"
    queryset = _Transaction.objects.all().order_by("timestamp", "id")
    serializer_class = _TransactionSerializer

    @action(detail=True, methods=["get"])
    def original(self, request: Any, pk: Any = None) -> Any:
        from django_assets.brokerage.disclosure import reconstruct_original

        return Response(reconstruct_original(self.get_object()))


class DisclosureEventViewSet(OwnerScopedMixin, viewsets.ReadOnlyModelViewSet[DisclosureEvent]):
    owner_path = "transaction__account__owner"
    queryset = DisclosureEvent.objects.select_related("transaction")
    serializer_class = DisclosureEventSerializer

    @action(detail=True, methods=["get"])
    def before(self, request: Any, pk: Any = None) -> Any:
        from django_assets.brokerage.disclosure import reconstruct_before

        return Response(reconstruct_before(self.get_object()))


class TradeViewSet(OwnerScopedMixin, viewsets.ReadOnlyModelViewSet[Trade]):
    """Trades queue + mutation actions (spec §7): rule violations come
    back as 400s, never 500s. Hosts mount URLs and own auth (D-18)."""

    owner_path = "user"
    queryset = Trade.objects.all().order_by("pk")
    serializer_class = TradeSerializer

    @action(detail=True, methods=["post"])
    def assign(self, request: Any, pk: Any = None) -> Any:
        from django_assets.core.models import Instrument as CoreInstrument
        from django_assets.core.models import Transaction as CoreTransaction
        from django_assets.trades.exceptions import OverAllocationError

        trade = self.get_object()
        try:
            transaction = CoreTransaction.objects.get(
                pk=request.data.get("transaction"), account__owner=trade.user
            )
            kwargs: dict[str, Any] = {}
            if request.data.get("fraction"):
                kwargs["fraction"] = request.data["fraction"]
            else:
                kwargs["quantity"] = request.data.get("quantity")
                kwargs["instrument"] = CoreInstrument.objects.get(pk=request.data.get("instrument"))
            allocations = trade.assign(transaction, **kwargs)
        except (OverAllocationError, ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"allocations": [allocation.pk for allocation in allocations]})

    @action(detail=True, methods=["post"])
    def unassign(self, request: Any, pk: Any = None) -> Any:
        from django_assets.core.models import Transaction as CoreTransaction

        trade = self.get_object()
        transaction = CoreTransaction.objects.get(
            pk=request.data.get("transaction"), account__owner=trade.user
        )
        return Response({"removed": trade.unassign(transaction)})

    @action(detail=True, methods=["post"])
    def reallocate(self, request: Any, pk: Any = None) -> Any:
        from django_assets.core.models import TransactionLeg as CoreLeg
        from django_assets.trades.exceptions import OverAllocationError

        trade = self.get_object()
        leg = CoreLeg.objects.get(pk=request.data.get("leg"), account__owner=trade.user)
        try:
            allocation = trade.reallocate(
                leg, request.data.get("amount"), category=request.data.get("category", "")
            )
        except (OverAllocationError, ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"allocation": allocation.pk})

    @action(detail=True, methods=["post"], url_path="transfer-position")
    def transfer_position(self, request: Any, pk: Any = None) -> Any:
        from django.utils.dateparse import parse_datetime

        from django_assets.core.models import Instrument as CoreInstrument
        from django_assets.trades.exceptions import UnbalancedVirtualTransferError
        from django_assets.trades.virtual import transfer_position as do_transfer

        trade = self.get_object()
        try:
            to_trade = Trade.objects.get(pk=request.data.get("to_trade"), user=trade.user)
            instrument = CoreInstrument.objects.get(pk=request.data.get("instrument"))
            timestamp = parse_datetime(str(request.data.get("timestamp")))
            if timestamp is None:
                raise ValueError("timestamp must be an ISO-8601 datetime")
            transfer = do_transfer(
                trade,
                to_trade,
                instrument=instrument,
                quantity=request.data.get("quantity"),
                price=request.data.get("price"),
                timestamp=timestamp,
            )
        except (UnbalancedVirtualTransferError, ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "transfer": transfer.pk,
                "warnings": [str(warning) for warning in transfer.warnings],
            }
        )


class VirtualTransferViewSet(OwnerScopedMixin, viewsets.ReadOnlyModelViewSet[VirtualTransfer]):
    owner_path = "user"
    queryset = VirtualTransfer.objects.prefetch_related("entries")
    serializer_class = VirtualTransferSerializer


class LotViewSet(OwnerScopedMixin, viewsets.ReadOnlyModelViewSet[Lot]):
    owner_path = "account__owner"
    queryset = Lot.objects.select_related("instrument").order_by("acquired_at", "id")
    serializer_class = LotSerializer


class LotMatchViewSet(OwnerScopedMixin, viewsets.ReadOnlyModelViewSet[LotMatch]):
    owner_path = "lot__account__owner"
    queryset = LotMatch.objects.select_related("lot").order_by("id")
    serializer_class = LotMatchSerializer
