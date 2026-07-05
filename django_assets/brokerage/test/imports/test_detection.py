"""Upload step one: recognize which registered format a file is, then
import it through the detected schema (fabricated samples)."""

import pytest

from django_assets.brokerage.schemas import registry
from django_assets.brokerage.schemas.detection import (
    UnknownFormatError,
    detect_format,
    import_upload,
)

pytestmark = pytest.mark.ledger

SAMPLES = {
    ("robinhood", "activity"): (
        "activity.csv",
        '"Activity Date","Process Date","Settle Date","Instrument","Description",'
        '"Trans Code","Quantity","Price","Amount"\n',
    ),
    ("schwab", "transactions"): (
        "transactions.csv",
        '"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"\n',
    ),
    ("tradier", "statement"): (
        "statement.pdf",
        "ACCOUNT NUMBER 6XX-1\nNET ACCOUNT BALANCE 950.00 79.14 Cash\n",
    ),
    ("tdameritrade", "statement"): (
        "2023_1_Statement.pdf",
        "Cash Activity Summary Income & Expense Summary\nAccount Activity\n",
    ),
    ("tdameritrade", "advisor-statement"): (
        "947365774 2023-04.pdf",
        "Questions? Consult your Independent Advisor:\nTRANSACTIONS DETAIL\n",
    ),
    ("schwab", "statement"): (
        "Brokerage Statement_2024-11-30_534.pdf",
        "Transactions - Summary\nBeginningCash*asof11/01 + Deposits\n",
    ),
    ("robinhood", "statement"): (
        "2020-12.pdf",
        "Robinhood Securities, LLC\nAccount Summary Opening Balance Closing Balance\n"
        "Account Activity\n",
    ),
    ("homebroker", "resumen"): (
        "2023 RESUMEN_300718.pdf",
        "RESUMEN DE CUENTA\nComitente: 300718\nDETALLE DE MOVIMIENTOS\n",
    ),
}


def test_every_sample_detects_exactly_its_schema():
    for (broker, document_kind), (file_name, sample) in SAMPLES.items():
        schema = detect_format(file_name, sample)
        assert (schema.broker, schema.document_kind) == (broker, document_kind), (
            f"{file_name} detected as {schema.broker}/{schema.document_kind}"
        )


def test_samples_never_cross_match():
    """Each fingerprint claims its own sample and nobody else's."""
    for (broker, document_kind), (file_name, sample) in SAMPLES.items():
        container = "pdf" if file_name.endswith(".pdf") else "csv"
        claimants = [
            (s.broker, s.document_kind)
            for s in registry.all()
            if s.format_kind == container and type(s).sniff(sample)
        ]
        assert claimants == [(broker, document_kind)], f"{file_name}: {claimants}"


def test_unknown_format_is_loud():
    with pytest.raises(UnknownFormatError):
        detect_format("mystery.csv", "some,unknown,header\n1,2,3\n")


def test_import_upload_detects_then_processes(accounts, usd):
    csv_text = (
        '"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"\n'
        '"03/10/2026","Buy","AAPL","APPLE INC","10","175.50","0.55","-1755.55"\n'
    )
    batch = import_upload(
        account=accounts["cash"], file_name="upload.csv", content=csv_text.encode()
    )
    assert batch.schema_broker == "schwab"
    assert batch.schema_document_kind == "transactions"
    assert batch.transaction_count == 1
