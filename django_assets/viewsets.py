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
from django_assets.serializers import (
    AccountSerializer,
    CorporateActionSerializer,
    DisclosureEventSerializer,
    ExchangeSerializer,
    IdentifierSerializer,
    ImportLineProposalSerializer,
    ImportLineSerializer,
    InstrumentSerializer,
    OptionMetaSerializer,
)


class ExchangeViewSet(viewsets.ReadOnlyModelViewSet[Exchange]):
    queryset = Exchange.objects.all()
    serializer_class = ExchangeSerializer


class InstrumentViewSet(viewsets.ReadOnlyModelViewSet[Instrument]):
    queryset = Instrument.objects.all()
    serializer_class = InstrumentSerializer


class IdentifierViewSet(viewsets.ReadOnlyModelViewSet[Identifier]):
    queryset = Identifier.objects.all()
    serializer_class = IdentifierSerializer


class AccountViewSet(viewsets.ReadOnlyModelViewSet[Account]):
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


class ImportLineViewSet(viewsets.ReadOnlyModelViewSet[ImportLine]):
    """The review queue: list/detail plus match/unmatch actions."""

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


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):  # type: ignore[type-arg]
    """Read-only transactions + the ADR-0023 reconstruction endpoint."""

    from django_assets.core.models import Transaction as _Transaction
    from django_assets.serializers import TransactionSerializer as _TransactionSerializer

    queryset = _Transaction.objects.all().order_by("timestamp", "id")
    serializer_class = _TransactionSerializer

    @action(detail=True, methods=["get"])
    def original(self, request: Any, pk: Any = None) -> Any:
        from django_assets.brokerage.disclosure import reconstruct_original

        return Response(reconstruct_original(self.get_object()))


class DisclosureEventViewSet(viewsets.ReadOnlyModelViewSet[DisclosureEvent]):
    queryset = DisclosureEvent.objects.select_related("transaction")
    serializer_class = DisclosureEventSerializer

    @action(detail=True, methods=["get"])
    def before(self, request: Any, pk: Any = None) -> Any:
        from django_assets.brokerage.disclosure import reconstruct_before

        return Response(reconstruct_before(self.get_object()))
