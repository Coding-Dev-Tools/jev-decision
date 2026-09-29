"""Wire identifiers are redacted, collision-free and restored per invocation."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jev_decision import ChoiceQuestion, JevClient, NoulQuestion, ScoreQuestion
from jev_decision.runtime import RuntimeConfig

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "ts/test/fixtures/question-ids.json").read_text(encoding="utf-8"))


class Ledger:
    def __init__(self):
        self.reservations = 0

    def reserve(self):
        self.reservations += 1
        return object()

    def settle(self, reservation, token_count=None):
        pass


@pytest.mark.parametrize("typed", [False, True])
def test_redacted_choice_labels_are_restored_for_current_caller_and_cache(typed):
    calls, ledger = [], Ledger()

    def transport(request, *_):
        payload = json.loads(request.data)
        calls.append(payload)
        criteria = payload["questions"]["route"]["criteria"]
        selected = next(key for key in criteria if key.startswith("password="))
        return 200, json.dumps({"model": payload["model"], "answers": {"route": {
            "type": "choice", "choice": selected, "confidence": .9,
            "probabilities": {key: 1 if key == selected else 0 for key in criteria}}}}).encode()

    client = JevClient(api_key="fixture-api-key", runtime=RuntimeConfig(enabled=True), budget_ledger=ledger, transport=transport)
    for index, label in enumerate(("password=alpha", "password=beta")):
        criteria = {label: None, "other": None}
        questions = ([ChoiceQuestion("route", "Choose the appropriate route", criteria=criteria)] if typed else
                     {"route": {"type": "choice", "instructions": "Choose the appropriate route", "criteria": criteria}})
        batch = client.evaluate("sample", questions)
        assert batch.status == "ok" and batch.source == ("provider" if index == 0 else "cache")
        decision = batch.get_choice("route")
        assert decision.selected == label and set(decision.probabilities) == set(criteria)
    assert len(calls) == ledger.reservations == 1
    assert "alpha" not in json.dumps(calls) and "beta" not in json.dumps(calls)
    collision = {"route": {"type": "choice", "instructions": "Choose", "criteria": {"password=alpha": None, "password=beta": None}}}
    assert client.evaluate("sample", collision).error_code == "invalid_request"
    assert len(calls) == ledger.reservations == 1


def run_case(spec, typed):
    ids = ["x" * spec.get("id_prefix_length", 0) + value for value in spec["ids"]]
    wire_ids, calls = [], 0
    ledger = Ledger()

    def transport(request, *_):
        nonlocal calls
        calls += 1
        payload = json.loads(request.data)
        wire_ids.extend(sorted(payload["questions"]))
        return 200, json.dumps({"model": payload["model"], "answers": {
            key: {"type": "noul", "noul": 0.75} for key in wire_ids
        }}).encode()

    client = JevClient(api_key=FIXTURE["api_key"], runtime=RuntimeConfig(enabled=True),
                       budget_ledger=ledger, transport=transport)
    questions = ([NoulQuestion(key, "Does this evidence support the task?") for key in ids] if typed
                 else {key: {"type": "noul", "instructions": "Does this evidence support the task?"} for key in ids})
    batch = client.evaluate("Example evidence", questions)
    assert batch.error_code == spec.get("error")
    assert wire_ids == sorted(spec.get("wire_ids", []))
    assert sorted(batch.decisions) == ([] if spec.get("error") else sorted(ids))
    assert all(decision.id == key for key, decision in batch.decisions.items())
    assert calls == ledger.reservations == (0 if spec.get("error") else 1)
    result = batch.to_dict()
    result.pop("latency_ms")
    result.pop("request_id")
    return {"result": result, "wire_ids": wire_ids, "calls": calls}


@pytest.mark.parametrize("typed", [False, True], ids=["native", "typed"])
@pytest.mark.parametrize("spec", FIXTURE["cases"], ids=lambda spec: spec["name"])
def test_question_ids(spec, typed):
    run_case(spec, typed)


def test_shared_question_id_parity():
    node = shutil.which("node")
    if not node or not (ROOT / "ts/dist/index.js").exists():
        pytest.skip("Build TypeScript to compare the wire-ID corpus")
    process = subprocess.run([node, str(ROOT / "ts/test/question-id-runner.cjs")],
                             capture_output=True, text=True, timeout=20, check=True, encoding="utf-8")
    assert json.loads(process.stdout) == {
        f'{spec["name"]}:{"typed" if typed else "native"}': run_case(spec, typed)
        for spec in FIXTURE["cases"] for typed in (False, True)
    }


def test_cached_identifiers_are_restored_for_current_caller():
    ledger = Ledger()

    def transport(request, *_):
        payload = json.loads(request.data)
        answers = {}
        for key, question in payload["questions"].items():
            kind = question["type"]
            if kind == "noul":
                answers[key] = {"type": kind, "noul": 0.75}
            elif kind == "choice":
                answers[key] = {"type": kind, "choice": "yes", "confidence": 0.8,
                                "probabilities": {"yes": 0.75, "no": 0.25}}
            else:
                answers[key] = {"type": kind, "score": 0.75, "confidence": 0.8,
                                "legend": {"0": "Low relevance", "1": "High relevance"},
                                "probabilities": {"0": 0.25, "1": 0.75}}
        return 200, json.dumps({"model": payload["model"], "answers": answers}).encode()

    client = JevClient(api_key=FIXTURE["api_key"], runtime=RuntimeConfig(enabled=True),
                       budget_ledger=ledger, transport=transport)
    for index, value in enumerate(("first", "second", "third")):
        questions = [
            NoulQuestion("password=" + value, "Assess the evidence"),
            ChoiceQuestion("api_key=" + value, "Choose a route", criteria={"yes": None, "no": None}),
            ScoreQuestion("secret=" + value, "Rate relevance", criteria=["Low relevance", "High relevance"]),
        ]
        batch = client.evaluate("Example evidence", questions)
        assert batch.status == "ok"
        assert batch.source == ("provider" if index == 0 else "cache")
        assert batch.attempts == (1 if index == 0 else 0)
        assert set(batch.decisions) == {question.id for question in questions}
        assert all(decision.id == key for key, decision in batch.decisions.items())
        assert batch.get_noul(questions[0].id).probability == 0.75
        assert batch.get_choice(questions[1].id).selected == "yes"
        assert batch.get_score(questions[2].id).score == 0.75
        if index:
            assert batch.usage == {"input_tokens": 0, "output_tokens": 0}
        batch.get_noul(questions[0].id).probability = 0
    assert ledger.reservations == 1


def test_original_id_in_provider_response_is_rejected_before_mapping():
    ledger = Ledger()
    original = "password=fixture"

    def transport(request, *_):
        payload = json.loads(request.data)
        return 200, json.dumps({"model": payload["model"], "answers": {
            original: {"type": "noul", "noul": 0.75}
        }}).encode()

    client = JevClient(api_key=FIXTURE["api_key"], runtime=RuntimeConfig(enabled=True),
                       budget_ledger=ledger, transport=transport)
    for _ in range(2):
        batch = client.evaluate("Example evidence", [NoulQuestion(original, "Assess the evidence")])
        assert batch.error_code == "invalid_response"
        assert not batch.decisions
        assert original not in json.dumps(batch.to_dict())
    assert ledger.reservations == 2
