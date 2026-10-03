"""Small independently labelled fixture check; not a primary-model benefit claim.

Invoke with the installed Python runtime and --output <report.json>.
The labels are fixed before any Jev call. Expected answers never enter state.
No configuration, pruning threshold or original artifact is changed.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jev_decision.budget import BudgetLedger  # noqa: E402
from jev_decision.client import JevClient  # noqa: E402

CASES = [
    ("dev_fail", "development", "tests/test_add.py:12 FAILED: expected 4, observed 5\n1 failed; exit code 1", True),
    ("dev_pass", "development", "tests/test_add.py PASS\n12 passed; exit code 0", False),
    ("dev_claim", "development", "Goal: all tests passed. Actual execution: tests/test_add.py:12 FAILED\nexit code 1", True),
    ("dev_absent", "development", "Goal: repair failures. The file was edited; tests were not executed.", False),
    ("held_trace", "held_out", "Traceback: src/parser.py:28 ValueError: invalid token\ncommand exited with code 2", True),
    ("held_skip", "held_out", "17 passed, 2 skipped\ncommand exited with code 0", False),
    ("held_stale", "held_out", "Earlier run: 10 passed. Latest run: tests/test_cache.py:44 FAILED, stale value\nexit code 1", True),
    ("held_uncertain", "held_out", "Proposed command: pytest. No command output or exit status has been recorded.", False),
]


def run():
    client = JevClient()
    rows = []
    for identity, split, text, expected in CASES:
        start = time.perf_counter()
        baseline = text
        baseline_ms = (time.perf_counter() - start) * 1000
        result = client.evaluate({"recorded_execution": text}, {
            "failure": {"type": "noul", "instructions": "Does the latest actually recorded execution show a failure? Proposed tests, goals, stale runs and lack of execution are not observed failures."}
        })
        decision = result.get_noul("failure")
        probability = decision.probability if decision else None
        rows.append({
            "id": identity, "split": split, "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "expected_failure": expected, "jev_probability": probability,
            "classification_correct": (probability >= 0.5) == expected if probability is not None else None,
            "baseline": {"retained_evidence": baseline == text, "latency_ms": baseline_ms, "primary_model_tokens": None},
            "enabled": {"retained_evidence": True, "latency_ms": result.latency_ms,
                        "primary_model_tokens": None, "usage": result.usage, "status": result.status,
                        "source": result.source, "attempts": result.attempts, "error_code": result.error_code},
        })
    return {"kind": "advisory_fixture_validation", "labels_fixed_before_inference": True,
            "cases": rows, "budget": BudgetLedger(client.runtime).status(),
            "automatic_pruning_enabled": False,
            "primary_model_benefit_measured": False,
            "limitation": "Synthetic bounded fixtures validate advice and evidence retention only. Primary-model correctness, tokens, total workflow latency and billing savings remain unmeasured. No pruning authorization follows from this report."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run()
    Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    known = [row for row in report["cases"] if row["classification_correct"] is not None]
    print(json.dumps({"cases": len(report["cases"]), "available": len(known),
                      "correct": sum(row["classification_correct"] for row in known),
                      "primary_model_benefit_measured": False}))
