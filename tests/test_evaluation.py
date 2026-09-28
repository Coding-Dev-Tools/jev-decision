"""Independent collector regressions, including identity and matched-arm failures."""
import hashlib
import json

import pytest

from jev_decision.evaluation import arm_order, assemble_report, load_dataset, write_profile
from jev_decision.harness_guards import PROMPT_RUBRIC_SHA256
from jev_decision.qualification import canonical_sha256, load_qualification, validate_qualification


def measured_fixture():
    route = {"harness": "test-adapter", "harness_version": "1", "primary_model": "test-model", "primary_provider": "test-provider"}
    prices = {"as_of": "2026-09-28", "currency": "USD", "sources": ["https://example.test/prices"],
              "input_convention": "inclusive_of_cache", "jev_model": "jev-1.13.0",
              "primary_model": route["primary_model"], "primary_provider": route["primary_provider"],
              "input_per_million": 1, "output_per_million": 1,
              "cache_read_per_million": 1, "cache_write_per_million": 1,
              "jev_input_per_million": .042, "jev_output_per_million": 0}
    dataset, observations = {"version": 1, "label_method": "deterministic", "cases": []}, []
    for index in range(30):
        source = hashlib.sha256(str(index).encode()).hexdigest()
        dataset["cases"].append({"task_id": str(index), "group_id": str(index), "split": "held_out",
            "source_class": "test_log", "source_sha256": source, "critical_facts": ["exit status 1"], "expected_answer": {"code": 1}})
        for order, arm in enumerate(arm_order(index)):
            semantic = arm in {"shadow", "select"}
            stats = {"mode": "experimental_select" if arm == "select" else "shadow" if semantic else "off",
                     "status": "ok" if semantic else "disabled", "calls": int(semantic), "attempts": int(semantic),
                     "requested_model": "jev-1.13.0", "resolved_model": "jev-1.13.0",
                     "prompt_rubric_sha256": PROMPT_RUBRIC_SHA256, "source_class": "test_log",
                     "threshold_score": .25, "threshold_confidence": .9,
                     "usage": {"input_tokens": 100 if semantic else 0, "output_tokens": 10 if semantic else 0}}
            response = {"source_sha256": source, "output": "exit status 1\n", "stats": stats}
            observations.append({"task_id": str(index), "arm": arm, "order": order, "trial": 1, "cache_state": "cold",
                "source_sha256": source, "tool_response": response, "tool_response_sha256": canonical_sha256(response),
                "trace_sha256": source, "route": route, "route_verified": True, "answer": {"code": 1},
                "primary_usage": {"input_tokens": 500 if arm == "select" else 1000, "output_tokens": 50,
                                  "cache_read_tokens": 0, "cache_write_tokens": 0},
                "jev_usage": stats["usage"], "retries": 0, "recovery_calls": 0, "preprocessing_ms": 1,
                "total_elapsed_ms": 90 if arm == "select" else 100})
    return dataset, observations, {**route, "run_mode": "live", "campaign_budget_usd": 1}, prices


def test_all_arms_net_cost_and_report_bound_profile(tmp_path):
    dataset, observations, provenance, prices = measured_fixture()
    report = assemble_report(dataset, observations, provenance, prices)
    assert report["qualification_check"]["eligible"] is True
    assert report["uncertainty"]["held_out_tasks"] == 30
    assert report["uncertainty"]["mean_cost_savings_bootstrap_95"][0] > 0
    assert report["invoice_verified"] is False
    assert report["provenance"]["campaign_cost_usd"] > sum(row["selected_total_cost_usd"] for row in report["rows"])
    path, profile_path = tmp_path / "report.json", tmp_path / "profile.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    write_profile(report, path, profile_path)
    profile, restored = load_qualification(profile_path)
    validate_qualification(profile, restored, model="jev-1.13.0", prompt_rubric_sha256=PROMPT_RUBRIC_SHA256,
                           source_class="test_log", expected_workload={key: provenance[key] for key in
                               ("harness", "harness_version", "primary_model", "primary_provider")})


@pytest.mark.parametrize("field,value", [("mode", "off"), ("requested_model", "jev-0.0.0"),
    ("prompt_rubric_sha256", "f" * 64), ("threshold_score", .8), ("status", "offline")])
def test_wrong_observed_jev_identity_cannot_be_stamped_current(field, value):
    args = measured_fixture()
    observation = next(row for row in args[1] if row["arm"] == "select")
    observation["tool_response"]["stats"][field] = value
    observation["tool_response_sha256"] = canonical_sha256(observation["tool_response"])
    assert assemble_report(*args)["qualification_check"]["eligible"] is False


@pytest.mark.parametrize("field,value", [("trial", 99), ("cache_state", "warm"), ("trace_sha256", "x" * 64),
    ("retries", None), ("recovery_calls", None), ("preprocessing_ms", None), ("order", 99)])
def test_nonmatching_or_incomplete_arms_do_not_qualify(field, value):
    args = measured_fixture()
    next(row for row in args[1] if row["arm"] == "baseline")[field] = value
    assert assemble_report(*args)["qualification_check"]["eligible"] is False


def test_unknown_usage_and_price_identity_remain_unknown():
    args = measured_fixture()
    args[1][0]["primary_usage"]["cache_read_tokens"] = None
    report = assemble_report(*args)
    assert report["qualification_check"]["eligible"] is False
    assert report["provenance"]["campaign_cost_usd"] is None
    args = measured_fixture()
    del args[3]["as_of"]
    assert assemble_report(*args)["qualification_check"]["eligible"] is False


def test_jev_usage_cannot_be_underreported():
    args = measured_fixture()
    observation = next(row for row in args[1] if row["arm"] == "select")
    observation["jev_usage"] = {"input_tokens": 0, "output_tokens": 0}
    assert assemble_report(*args)["qualification_check"]["eligible"] is False


def test_four_unique_arms_required():
    args = measured_fixture()
    args[1].pop()
    with pytest.raises(ValueError, match="matched"):
        assemble_report(*args)


def test_duplicate_source_cannot_create_independent_groups():
    args = measured_fixture()
    same_hash = args[0]["cases"][0]["source_sha256"]
    for case in args[0]["cases"]:
        case["source_sha256"] = same_hash
    for observation in args[1]:
        observation["source_sha256"] = same_hash
        observation["tool_response"]["source_sha256"] = same_hash
        observation["tool_response_sha256"] = canonical_sha256(observation["tool_response"])
    result = assemble_report(*args)
    assert result["qualification_check"] == {"eligible": False, "reason": "source_group_mismatch"}


def test_dataset_source_and_group_integrity(tmp_path):
    source = tmp_path / "capture.log"
    source.write_text("exit status 1\n", encoding="utf-8")
    case = {"task_id": "task", "group_id": "project", "goal": "read", "source_class": "test_log",
            "source": source.name, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "critical_facts": ["exit status 1"], "expected_answer": {"code": 1}, "split": "development"}
    dataset = {"version": 1, "label_method": "deterministic", "cases": [case]}
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    assert load_dataset(path) == dataset
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_dataset(path)
