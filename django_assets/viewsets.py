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

from django_assets.brokerage.models import ImportLine
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
    ExchangeSerializer,
    IdentifierSerializer,
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
        legs = list(TransactionLeg.objects.filter(pk__in=self._leg_ids(request)))
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
