"""Independent collector regressions, including identity and matched-arm failures."""
import copy
import hashlib
import json

import pytest

from jev_decision.evaluation import (
    arm_order,
    assemble_report,
    load_dataset,
    local_repetitions,
    write_profile,
)
from jev_decision.evidence import read_evidence_file
from jev_decision.harness_guards import PROMPT_RUBRIC_SHA256, _select_from_shadow, _spans
from jev_decision.qualification import canonical_sha256, load_qualification, validate_qualification


def measured_fixture(tmp_path, *, text_factory=None, facts=None, count=30, source_class="test_log"):
    route = {"harness": "test-adapter", "harness_version": "1", "primary_model": "test-model", "primary_provider": "test-provider"}
    prices = {"as_of": "2026-09-28", "currency": "USD", "sources": ["https://example.test/prices"],
              "input_convention": "inclusive_of_cache", "jev_model": "jev-1.13.0",
              "primary_model": route["primary_model"], "primary_provider": route["primary_provider"],
              "input_per_million": 1, "output_per_million": 1,
              "cache_read_per_million": 1, "cache_write_per_million": 1,
              "jev_input_per_million": .042, "jev_output_per_million": 0}
    dataset, observations = {"version": 1, "label_method": "deterministic", "cases": []}, []
    for index in range(count):
        text = (text_factory(index) if text_factory else "INFO capture %d\n" % index +
                "INFO repeated transport bookkeeping with no additional detail\n" * 120 +
                "ERROR failed assertion\n1 failed, 12 passed\nexit status 1\n")
        source_path = tmp_path / ("capture-%d.log" % index)
        source_path.write_bytes(text.encode("utf-8"))
        source = hashlib.sha256(source_path.read_bytes()).hexdigest()
        dataset["cases"].append({"task_id": str(index), "group_id": str(index), "split": "held_out",
            "goal": "Identify the failure", "source": source_path.name, "source_class": source_class,
            "source_sha256": source, "critical_facts": facts or ["exit status 1"], "expected_answer": {"code": 1}})
        baseline = read_evidence_file(str(source_path), "Identify the failure", [tmp_path], mode="off", source_class=source_class)
        # Synthetic matched observations use known fixture timings, not disk jitter.
        baseline["stats"]["latency_ms"] = .5
        shadow = copy.deepcopy(baseline)
        shadow["stats"].update(mode="shadow", status="ok", calls=1, attempts=1,
            requested_model="jev-1.13.0", resolved_model="jev-1.13.0", source_sha256=source,
            usage={"input_tokens": 100, "output_tokens": 10},
            spans=[{key: value for key, value in span.items() if not key.startswith("_")}
                   for span in _spans(shadow["output"].splitlines(keepends=True), source_class, 1)])
        for span in shadow["stats"]["spans"]:
            if not span["protected"]:
                span.update(score=0, confidence=.99, assessed=True)
        selected = copy.deepcopy(shadow)
        selected["output"], selected["stats"] = _select_from_shadow(shadow["output"], shadow["stats"], source_ref=shadow["source_ref"])
        local = copy.deepcopy(baseline)
        local["output"] = local_repetitions(baseline["output"], source_class)
        local["stats"]["control"] = "exact_unprotected_repetition"
        responses = {"baseline": baseline, "local": local, "shadow": shadow, "select": selected}
        for order, arm in enumerate(arm_order(index)):
            semantic = arm in {"shadow", "select"}
            response = responses[arm]
            observations.append({"task_id": str(index), "arm": arm, "order": order, "trial": 1, "cache_state": "cold",
                "source_sha256": source, "tool_response": response, "tool_response_sha256": canonical_sha256(response),
                "trace_sha256": source, "route": route, "route_verified": True, "answer": {"code": 1},
                "primary_usage": {"input_tokens": 500 if arm == "select" else 1000, "output_tokens": 50,
                                  "cache_read_tokens": 0, "cache_write_tokens": 0},
                "jev_usage": response["stats"]["usage"] if semantic else {"input_tokens": 0, "output_tokens": 0},
                "retries": 0, "recovery_calls": 0, "preprocessing_ms": 1,
                "total_elapsed_ms": 90 if arm == "select" else 100})
    manifest = tmp_path / "dataset.json"
    manifest.write_text(json.dumps(dataset), encoding="utf-8")
    return load_dataset(manifest), observations, {**route, "run_mode": "live", "campaign_budget_usd": 1}, prices


def test_all_arms_net_cost_and_report_bound_profile(tmp_path):
    dataset, observations, provenance, prices = measured_fixture(tmp_path)
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
def test_wrong_observed_jev_identity_cannot_be_stamped_current(tmp_path, field, value):
    args = measured_fixture(tmp_path)
    observation = next(row for row in args[1] if row["arm"] == "select")
    observation["tool_response"]["stats"][field] = value
    observation["tool_response_sha256"] = canonical_sha256(observation["tool_response"])
    assert assemble_report(*args)["qualification_check"]["eligible"] is False


@pytest.mark.parametrize("field,value", [("trial", 99), ("cache_state", "warm"), ("trace_sha256", "x" * 64),
    ("retries", None), ("recovery_calls", None), ("preprocessing_ms", None), ("order", 99)])
def test_nonmatching_or_incomplete_arms_do_not_qualify(tmp_path, field, value):
    args = measured_fixture(tmp_path)
    next(row for row in args[1] if row["arm"] == "baseline")[field] = value
    assert assemble_report(*args)["qualification_check"]["eligible"] is False


def test_unknown_usage_and_price_identity_remain_unknown(tmp_path):
    args = measured_fixture(tmp_path)
    args[1][0]["primary_usage"]["cache_read_tokens"] = None
    report = assemble_report(*args)
    assert report["qualification_check"]["eligible"] is False
    assert report["provenance"]["campaign_cost_usd"] is None
    args = measured_fixture(tmp_path)
    del args[3]["as_of"]
    assert assemble_report(*args)["qualification_check"]["eligible"] is False


def test_jev_usage_cannot_be_underreported(tmp_path):
    args = measured_fixture(tmp_path)
    observation = next(row for row in args[1] if row["arm"] == "select")
    observation["jev_usage"] = {"input_tokens": 0, "output_tokens": 0}
    assert assemble_report(*args)["qualification_check"]["eligible"] is False


@pytest.mark.parametrize("latency", [4000, None, True, -1])
def test_task_timing_must_include_valid_selection_latency(tmp_path, latency):
    args = measured_fixture(tmp_path)
    observation = next(row for row in args[1] if row["arm"] == "select")
    observation["tool_response"]["stats"]["latency_ms"] = latency
    observation["tool_response_sha256"] = canonical_sha256(observation["tool_response"])
    assert assemble_report(*args)["qualification_check"]["eligible"] is False


def test_boolean_answers_do_not_pass_numeric_task_grading(tmp_path):
    args = measured_fixture(tmp_path)
    for observation in args[1]:
        if observation["arm"] == "select":
            observation["answer"] = {"code": True}
    report = assemble_report(*args)
    assert report["qualification_check"]["eligible"] is False
    assert all(row["success"] is False for row in report["arms"] if row["arm"] == "select")


def test_json_equality_is_recursive_and_accepts_equivalent_json_numbers():
    from jev_decision.jsonutil import json_equal
    assert json_equal({"nested": [0, {"value": 1}]}, {"nested": [0.0, {"value": 1.0}]})
    assert not json_equal({"nested": [0, {"value": 1}]}, {"nested": [False, {"value": True}]})
    assert not json_equal({"nested": [True]}, {"nested": [1]})


@pytest.mark.parametrize("source_class", ["test_log", "build_log", "jsonl", "application_log"])
def test_deterministic_control_preserves_unrecognized_page(source_class):
    from jev_decision.evaluation import _local_repetition_selection
    text = "detail below without header\nsecond line\n"
    output, intervals = _local_repetition_selection(text, source_class, first_line=101)
    assert output == text and intervals == [(101, 102)]


def test_four_unique_arms_required(tmp_path):
    args = measured_fixture(tmp_path)
    args[1].pop()
    with pytest.raises(ValueError, match="matched"):
        assemble_report(*args)


def test_duplicate_source_cannot_create_independent_groups(tmp_path):
    args = measured_fixture(tmp_path)
    first = args[0]["cases"][0]
    for case in args[0]["cases"]:
        case["source_sha256"], case["source"] = first["source_sha256"], first["source"]
    manifest = tmp_path / "dataset.json"
    manifest.write_text(json.dumps(args[0]), encoding="utf-8")
    with pytest.raises(ValueError, match="source_group_mismatch"):
        load_dataset(manifest)


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


def _selected_observation(args):
    return next(row for row in args[1] if row["arm"] == "select")


def _selected_record(report):
    return next(row for row in report["arms"] if row["arm"] == "select")


def _rebind(observation):
    observation["tool_response_sha256"] = canonical_sha256(observation["tool_response"])


def test_omission_markers_and_metadata_never_supply_critical_facts(tmp_path):
    text = "INFO start\n" + "INFO padding\n" * 50 + "INFO payload 1\n" + "INFO padding\n" * 50 + "INFO done\n"
    args = measured_fixture(tmp_path, text_factory=lambda _: text, facts=["1"], count=1, source_class="application_log")
    observation = _selected_observation(args)
    assert "1" in observation["tool_response"]["output"]  # Marker ranges contain 1.
    observation["tool_response"]["stats"]["metadata"] = {"critical_fact": "1"}
    _rebind(observation)
    record = _selected_record(assemble_report(*args))
    assert record["evidence_verified"] is True
    assert record["critical_evidence_retained"] == 0


def test_removed_ranges_cannot_fabricate_a_fact_by_joining_survivors(tmp_path):
    fact = "INFO left\nINFO right\n"
    text = ("INFO start\nINFO header\nINFO left\n" + "INFO padding\n" * 45 + fact +
            "INFO padding\n" * 45 + "INFO right\nINFO footer\nINFO done\n")
    args = measured_fixture(tmp_path, text_factory=lambda _: text, facts=[fact], count=1, source_class="application_log")
    report = assemble_report(*args)
    record = _selected_record(report)
    assert record["evidence_verified"] is True
    lines = text.splitlines(keepends=True)
    incorrectly_joined = "".join("".join(lines[span["start_line"] - 1:span["end_line"]])
                                 for span in record["retained_source_spans"])
    assert fact in incorrectly_joined  # Demonstrates why ranges must stay separate.
    assert record["critical_evidence_retained"] == 0


def test_redaction_placeholder_cannot_replace_an_omitted_fact(tmp_path):
    text = ("ERROR credential password=example-only\n" + "INFO padding\n" * 50 +
            "INFO literal [REDACTED]\n" + "INFO padding\n" * 50 + "INFO done\n")
    args = measured_fixture(tmp_path, text_factory=lambda _: text, facts=["[REDACTED]"], count=1, source_class="application_log")
    assert "[REDACTED]" in _selected_observation(args)["tool_response"]["output"]
    record = _selected_record(assemble_report(*args))
    assert record["evidence_verified"] is True and record["critical_evidence_retained"] == 0


def test_contiguous_multiline_facts_and_crlf_are_retained(tmp_path):
    fact = "1 failed, 12 passed\r\nexit status 1"
    text = "\ufeffINFO start\r\n" + "INFO padding\r\n" * 120 + fact + "\r\n"
    args = measured_fixture(tmp_path, text_factory=lambda _: text, facts=[fact], count=1)
    record = _selected_record(assemble_report(*args))
    assert record["evidence_verified"] is True and record["critical_evidence_retained"] == 1


@pytest.mark.parametrize("mutation", ["invented_output", "input_hash", "source_ref_hash", "missing_source_ref",
                                     "span_start", "span_retained", "span_protected", "page_end", "page_limit"])
def test_response_hash_alone_cannot_attest_original_source_spans(tmp_path, mutation):
    args = measured_fixture(tmp_path, count=1)
    observation = _selected_observation(args)
    response = observation["tool_response"]
    if mutation == "invented_output":
        response["output"] += "exit status 1\n"
    elif mutation == "input_hash":
        response["stats"]["input_sha256"] = "f" * 64
    elif mutation == "source_ref_hash":
        response["source_ref"]["source_sha256"] = "f" * 64
    elif mutation == "missing_source_ref":
        del response["source_ref"]
    elif mutation.startswith("span_"):
        span = next(item for item in response["stats"]["spans"] if not item["retained"])
        span[{"span_start": "start_line", "span_retained": "retained", "span_protected": "protected"}[mutation]] = 1 if mutation == "span_start" else True
    elif mutation == "page_end":
        response["page"]["end_line"] -= 1
    else:
        response["page"]["max_lines"] = 1
    _rebind(observation)  # Even a new matching envelope hash cannot hide a forgery.
    record = _selected_record(assemble_report(*args))
    assert record["evidence_verified"] is False
    assert record["critical_evidence_retained"] is None
    assert record["route_verified"] is False


def test_four_arms_must_measure_identical_source_pages(tmp_path):
    args = measured_fixture(tmp_path, count=1)
    observation = _selected_observation(args)
    original_stats = observation["tool_response"]["stats"]
    response = read_evidence_file(str(tmp_path / "capture-0.log"), "Identify the failure", [tmp_path],
                                  source_class="test_log", start_line=120, mode="off")
    stats = response["stats"]
    for key in ("mode", "status", "calls", "attempts", "requested_model", "resolved_model", "usage",
                "threshold_score", "threshold_confidence"):
        stats[key] = original_stats[key]
    stats["source_sha256"] = response["source_sha256"]
    stats["spans"] = [{key: value for key, value in span.items() if not key.startswith("_")}
                      for span in _spans(response["output"].splitlines(keepends=True), "test_log", 120)]
    observation["tool_response"] = response
    _rebind(observation)
    report = assemble_report(*args)
    assert _selected_record(report)["evidence_verified"] is True
    assert _selected_record(report)["critical_evidence_retained"] == 1
    assert report["rows"][0]["route_verified"] is False
    assert report["rows"][0]["arms_verified"] == []


def test_collector_requires_current_loaded_manifest_and_sources(tmp_path):
    args = measured_fixture(tmp_path, count=1)
    with pytest.raises(ValueError, match="load_dataset_required"):
        assemble_report(dict(args[0]), *args[1:])
    args[0]["cases"][0]["goal"] = "Changed after loading"
    with pytest.raises(ValueError, match="dataset_changed_since_loading"):
        assemble_report(*args)
    args = measured_fixture(tmp_path, count=1)
    (tmp_path / "capture-0.log").write_text("Changed after loading", encoding="utf-8")
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        assemble_report(*args)


def test_independent_labels_must_occur_in_original_source(tmp_path):
    with pytest.raises(ValueError, match="critical_facts_must_match_source"):
        measured_fixture(tmp_path, count=1, facts=["Not present in the source"])


def test_report_contains_versioned_spans_and_no_source_text(tmp_path):
    args = measured_fixture(tmp_path, count=1)
    report = assemble_report(*args)
    assert report["provenance"]["retention_method"] == "source_spans_v1"
    serialized = json.dumps(report)
    assert "retained_source_spans" in serialized
    assert "exit status 1" not in serialized
    assert "repeated transport bookkeeping" not in serialized
