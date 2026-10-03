"""Qualification cannot be earned by summaries, missing metrics or leaked labels."""
import copy
import hashlib
import json

import pytest

from jev_decision.harness_guards import PROMPT_RUBRIC_SHA256
from jev_decision.qualification import (
    RETENTION_METHOD,
    QualificationError,
    canonical_sha256,
    load_qualification,
    summarize_report,
    validate_qualification,
)

WORKLOAD = {"harness": "fixture", "harness_version": "1", "primary_model": "fixture", "primary_provider": "fixture"}


def qualified_documents(source_class="application_log"):
    report = {"version": 1, "kind": "jev_selection_evaluation", "model": "jev-1.13.0",
        "source_classes": [source_class], "prompt_rubric_sha256": PROMPT_RUBRIC_SHA256,
        "threshold_score": 0.25, "threshold_confidence": 0.9,
        "provenance": {"run_mode": "live", "dataset_sha256": "a" * 64, "labels_sha256": "b" * 64,
            "price_snapshot_sha256": "c" * 64, "label_method": "deterministic", "split_by": "task",
            "retention_method": RETENTION_METHOD,
            "harness": "fixture", "harness_version": "1", "primary_model": "fixture", "primary_provider": "fixture",
            "campaign_budget_usd": 10.0, "campaign_cost_usd": 3.0, "counterbalanced": True},
        "rows": [{"task_id": str(i), "group_id": "independent-" + str(i), "split": "held_out",
            "source_class": source_class, "source_sha256": hashlib.sha256(str(i).encode()).hexdigest(), "route_verified": True,
            "arms_verified": ["baseline", "local", "shadow", "select"], "cache_state": "cold", "trial": 1,
            "critical_evidence_total": 3, "critical_evidence_retained": 3,
            "baseline_success": True, "selected_success": True,
            "baseline_input_tokens": 1000, "selected_input_tokens": 500, "jev_input_tokens": 100,
            "baseline_output_tokens": 50, "selected_output_tokens": 50, "jev_output_tokens": 20,
            "baseline_total_cost_usd": 0.02, "selected_total_cost_usd": 0.01, "jev_cost_usd": 0.0001,
            "baseline_latency_ms": 100, "selected_latency_ms": 90} for i in range(30)]}
    profile = {"version": 1, "model": report["model"], "prompt_rubric_sha256": PROMPT_RUBRIC_SHA256,
        "source_classes": [source_class], "threshold_score": 0.25, "threshold_confidence": 0.9,
        "qualification": summarize_report(report, [source_class])}
    return profile, report


def validate(profile, report):
    return validate_qualification(profile, report, model="jev-1.13.0",
                                  prompt_rubric_sha256=PROMPT_RUBRIC_SHA256, source_class="application_log",
                                  expected_workload=WORKLOAD)


def test_qualification_recomputes_totals_and_includes_all_jev_tokens():
    profile, report = qualified_documents()
    result = validate(profile, report)
    assert result["sample_size"] == 30 and result["net_tokens_saved"] == 30 * 380
    assert result["p95_latency_ratio"] == 0.9 and result["task_regressions"] == 0
    assert result["report_sha256"] == canonical_sha256(report)


@pytest.mark.parametrize("key,value", [("selected_success", False), ("critical_evidence_retained", 2),
    ("route_verified", False), ("jev_input_tokens", None), ("jev_output_tokens", None),
    ("selected_total_cost_usd", None), ("baseline_latency_ms", 0), ("baseline_input_tokens", True),
    ("arms_verified", ["baseline", "select"]), ("cache_state", "unknown")])
def test_bad_held_out_observation_cannot_be_hidden_in_summary(key, value):
    profile, report = qualified_documents()
    report["rows"][0][key] = value
    with pytest.raises(QualificationError):
        validate(profile, report)


@pytest.mark.parametrize("key,value", [("run_mode", "offline"), ("label_method", "jev"),
    ("counterbalanced", False), ("campaign_budget_usd", 0), ("campaign_cost_usd", None),
    ("dataset_sha256", "unknown"), ("labels_sha256", None)])
def test_unproven_provenance_cannot_qualify(key, value):
    profile, report = qualified_documents()
    report["provenance"][key] = value
    with pytest.raises(QualificationError):
        validate(profile, report)


def test_no_p95_regression_even_if_median_improves():
    profile, report = qualified_documents()
    for row in report["rows"][-2:]:
        row["selected_latency_ms"] = 101
    with pytest.raises(QualificationError, match="p95_latency"):
        validate(profile, report)


def test_positive_primary_reduction_does_not_hide_jev_overhead():
    profile, report = qualified_documents()
    for row in report["rows"]:
        row["jev_input_tokens"] = 600
    with pytest.raises(QualificationError, match="net_benefit"):
        validate(profile, report)


def test_independent_groups_and_held_out_minimum():
    profile, report = qualified_documents()
    report["rows"][-1]["group_id"] = report["rows"][0]["group_id"]
    with pytest.raises(QualificationError, match="insufficient"):
        validate(profile, report)
    profile, report = qualified_documents()
    development = copy.deepcopy(report["rows"][0])
    development.update(task_id="development", split="development")
    report["rows"].append(development)
    with pytest.raises(QualificationError, match="leakage"):
        validate(profile, report)
    development["group_id"] = "apparently different task"
    with pytest.raises(QualificationError, match="source_leakage"):
        validate(profile, report)


@pytest.mark.parametrize("workload", [None, {}, {**WORKLOAD, "primary_model": "another-model"}])
def test_profile_requires_matching_caller_workload(workload):
    profile, report = qualified_documents()
    with pytest.raises(QualificationError, match="workload_identity"):
        validate_qualification(profile, report, model="jev-1.13.0",
            prompt_rubric_sha256=PROMPT_RUBRIC_SHA256, source_class="application_log", expected_workload=workload)


@pytest.mark.parametrize("key,value", [("model", "jev-9.9.9"), ("prompt_rubric_sha256", "f" * 64),
    ("source_classes", ["test_log"]), ("threshold_score", 0.5), ("threshold_confidence", 0.5)])
def test_identity_and_threshold_changes_invalidate_profile(key, value):
    profile, report = qualified_documents()
    profile[key] = value
    with pytest.raises(QualificationError):
        validate(profile, report)


def test_summary_is_not_an_attestation():
    profile, report = qualified_documents()
    profile["qualification"]["net_tokens_saved"] = 999999
    with pytest.raises(QualificationError, match="summary"):
        validate(profile, report)


@pytest.mark.parametrize("method", [None, "substring", "source_spans_v0"])
def test_old_retention_grader_cannot_qualify_even_with_rebound_hash(method):
    profile, report = qualified_documents()
    if method is None:
        del report["provenance"]["retention_method"]
    else:
        report["provenance"]["retention_method"] = method
    profile["qualification"]["report_sha256"] = canonical_sha256(report)
    with pytest.raises(QualificationError, match="source_bound_retention_required"):
        validate(profile, report)


def test_loader_binds_report_and_rejects_escape(tmp_path):
    profile, report = qualified_documents()
    profile["report_path"] = "report.json"
    report_path, profile_path = tmp_path / "report.json", tmp_path / "profile.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    loaded = load_qualification(profile_path)
    assert loaded == (profile, report)
    report["rows"][0]["selected_success"] = False
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(QualificationError, match="hash_mismatch"):
        load_qualification(profile_path)
    profile["report_path"] = "../outside.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(QualificationError, match="outside"):
        load_qualification(profile_path)
