"""Read-only DRF viewsets (instruments spec §5, ADR-0017 posture).

Host-mounted: the package ships no urls and sets no authentication or
permission classes — hosts wire routers and auth themselves. Import is
DRF-guarded the same way as django_assets.serializers [D-8].
"""

try:
    from rest_framework import viewsets
except ImportError as exc:  # pragma: no cover — same posture as serializers
    raise ImportError(
        "django_assets.viewsets requires djangorestframework. "
        'Install the extra: pip install "django-assets[drf]".'
    ) from exc

from django_assets.core.models import Account, Exchange, Identifier, Instrument
from django_assets.instruments.models import CorporateAction
from django_assets.instruments.options.models import OptionMeta
from django_assets.serializers import (
    AccountSerializer,
    CorporateActionSerializer,
    ExchangeSerializer,
    IdentifierSerializer,
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
