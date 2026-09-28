"""One native provider corpus shared by Python and TypeScript."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jev_decision.client import JevClient
from jev_decision.runtime import RuntimeConfig

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "ts/test/fixtures/contract.json").read_text(encoding="utf-8"))


class FixtureLedger:
    # This corpus compares provider contracts, including exact error codes.
    # A slow disk may legitimately produce a deadline error instead; real SQLite
    # and deadline behavior are covered by the runtime/accounting test suites.
    def reserve(self):
        return object()

    def settle(self, reservation, token_count=None):
        pass


def materialize(spec):
    response = copy.deepcopy(FIXTURE["response"])
    for patch in spec["patches"]:
        target = response
        for key in patch["path"][:-1]:
            target = target[key]
        key = patch["path"][-1]
        if patch["op"] == "remove":
            del target[key]
        else:
            target[key] = copy.deepcopy(patch["value"])
    return spec.get("raw_response", json.dumps(response)).encode("utf-8")


def normalized_result(spec):
    body = materialize(spec)
    statuses = spec.get("http_statuses", [200])
    calls = 0

    def transport(*args):
        nonlocal calls
        status = statuses[min(calls, len(statuses) - 1)]
        calls += 1
        return status, body

    client = JevClient(api_key="fixture-only-not-a-real-key", runtime=RuntimeConfig(enabled=True),
                       transport=transport, budget_ledger=FixtureLedger())
    result = client.evaluate(FIXTURE["state"], FIXTURE["questions"]).to_dict()
    result.pop("latency_ms")
    result.pop("request_id")
    return result


@pytest.mark.parametrize("spec", FIXTURE["cases"], ids=lambda case: case["name"])
def test_shared_native_contract(spec):
    expected = copy.deepcopy(FIXTURE["expected_" + spec["expected"]])
    expected.update(spec.get("expected_overrides", {}))
    assert normalized_result(spec) == expected


def test_typescript_python_parity():
    node = shutil.which("node")
    if not node or not (ROOT / "ts/dist/index.js").exists():
        pytest.skip("Build TypeScript with npm test to run cross-language comparison")
    process = subprocess.run([node, str(ROOT / "ts/test/contract-runner.cjs")],
                             capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(process.stdout) == {case["name"]: normalized_result(case) for case in FIXTURE["cases"]}
