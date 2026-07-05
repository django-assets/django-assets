"""Real-artifact acceptance (private data, git-excluded): imports every
brokerage CSV under ./artifacts and verifies the whole flow. Skips
cleanly when the directory is absent (CI, other machines)."""

import importlib.util
import sys
from pathlib import Path

import pytest

ARTIFACTS = Path(__file__).resolve().parents[4] / "artifacts"
RUNNER = Path(__file__).resolve().parents[4] / "scripts" / "import_artifacts.py"

pytestmark = [
    pytest.mark.ledger,
    pytest.mark.skipif(not ARTIFACTS.exists(), reason="no private artifacts present"),
]


def _runner():
    spec = importlib.util.spec_from_file_location("import_artifacts", RUNNER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["import_artifacts"] = module
    spec.loader.exec_module(module)
    return module


def test_all_artifact_csvs_import_flawlessly(db):
    runner = _runner()
    failures = []
    for broker, config in runner.BROKERS.items():
        base = ARTIFACTS / broker / broker
        if not base.exists():
            continue
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            if not any(p.suffix.lower() == ".csv" for p in folder.iterdir()):
                continue
            results, accounts = runner.import_account_folder(broker, folder, config)
            failures.extend(r for r in results if r["status"] == "FAIL")
            summary = runner.verify_downstream(f"{broker}/{folder.name}", accounts)
            assert summary["lot_vs_ledger_mismatches"] == []
    assert failures == []
