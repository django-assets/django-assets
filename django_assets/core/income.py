"""Income character (ADR-0038): the three-source characterization
hierarchy. Income is `unclassified` when the broker prints nothing;
broker-printed classes map on import (`source="broker"`); the user may
characterize at import time or any time after (`source="user"`), and a
user assertion is never overwritten by re-import. Character is
transaction metadata — interpretation, not broker-attested amounts —
so ADR-0024 reconciliation locks do not apply.
"""

from typing import Any

from django.utils import timezone

from django_assets.core.models import Transaction

INCOME_CHARACTERS = (
    "ordinary",
    "qualified",
    "exempt",
    "capital_gain_lt",
    "capital_gain_st",
    "return_of_capital",
    "interest",
    "unclassified",
)


def income_character_metadata(
    character: str = "unclassified", label: str = "", source: str = "broker"
) -> dict[str, Any]:
    """The metadata keys income templates persist. `source` is recorded
    only for actual classifications — unclassified has no source."""
    if character not in INCOME_CHARACTERS:
        raise ValueError(f"unknown income character {character!r}; one of {INCOME_CHARACTERS}")
    metadata: dict[str, Any] = {"income_character": character}
    if label:
        metadata["income_label"] = label
    if character != "unclassified":
        metadata["income_character_source"] = source
    return metadata


def characterize_income(
    transaction: Transaction, character: str, *, label: str = ""
) -> Transaction:
    """User assertion (ADR-0038 §1): takes strict precedence and
    survives re-import. Prior value lands in metadata history; the
    broker's verbatim label is never removed."""
    if character not in INCOME_CHARACTERS:
        raise ValueError(f"unknown income character {character!r}; one of {INCOME_CHARACTERS}")
    history = transaction.metadata.get("income_character_history", [])
    history.append(
        {
            "income_character": transaction.metadata.get("income_character"),
            "income_character_source": transaction.metadata.get("income_character_source"),
            "at": timezone.now().isoformat(),
        }
    )
    transaction.metadata = {
        **transaction.metadata,
        "income_character": character,
        "income_character_source": "user",
        "income_character_history": history,
        **({"income_label": label} if label else {}),
    }
    transaction.save(update_fields=["metadata"])
    return transaction
