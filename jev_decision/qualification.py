"""Hash-bound, independently labelled evidence required for public selection.

These checks establish what an evaluation report records, not an independent
attestation of its author. Operators review and configure the profile locally;
model-supplied profiles must never be accepted by an MCP or CLI entry point.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

SOURCE_CLASSES = frozenset({"test_log", "build_log", "application_log", "jsonl", "diff"})
MIN_HELD_OUT_TASKS = 30
MAX_PROFILE_BYTES = 64 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class QualificationError(ValueError):
    """Content-free qualification failure."""


def canonical_sha256(value: Any) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise QualificationError("invalid_qualification_json") from None
    return hashlib.sha256(payload).hexdigest()


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _number(value: Any, *, positive: bool = False) -> bool:
    try:
        return (type(value) in (int, float) and math.isfinite(value)
                and (value > 0 if positive else value >= 0))
    except (OverflowError, ValueError):
        return False


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def validate_thresholds(score: Any, confidence: Any) -> None:
    # Initial guardrails may become stricter through calibration, not looser.
    if not _number(score) or score > 0.25 or not _number(confidence) or not 0.9 <= confidence <= 1:
        raise QualificationError("invalid_selection_thresholds")


def _classes(values: Iterable[str]) -> list[str]:
    if not isinstance(values, (list, tuple)) or not values:
        raise QualificationError("invalid_source_classes")
    if any(not isinstance(value, str) or value not in SOURCE_CLASSES for value in values):
        raise QualificationError("invalid_source_classes")
    if len(set(values)) != len(values):
        raise QualificationError("duplicate_source_class")
    return list(values)


def _percentile95(values: list[float]) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)]


def summarize_report(report: Dict[str, Any], source_classes: Iterable[str]) -> Dict[str, Any]:
    """Recompute held-out metrics; no asserted summary or success flag is trusted."""
    classes = _classes(source_classes)
    if not isinstance(report, dict) or type(report.get("version")) is not int or report["version"] != 1 or report.get("kind") != "jev_selection_evaluation":
        raise QualificationError("invalid_evaluation_report")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("run_mode") != "live":
        raise QualificationError("live_evaluation_required")
    if provenance.get("label_method") not in ("human", "deterministic") or provenance.get("split_by") != "task":
        raise QualificationError("independent_task_labels_required")
    if (not _number(provenance.get("campaign_budget_usd"), positive=True)
            or not _number(provenance.get("campaign_cost_usd"))
            or provenance["campaign_cost_usd"] > provenance["campaign_budget_usd"]):
        raise QualificationError("bounded_campaign_accounting_required")
    if provenance.get("counterbalanced") is not True:
        raise QualificationError("counterbalanced_evaluation_required")
    for key in ("dataset_sha256", "labels_sha256", "price_snapshot_sha256"):
        if not _digest(provenance.get(key)):
            raise QualificationError("evaluation_provenance_required")
    for key in ("harness", "harness_version", "primary_model", "primary_provider"):
        if not isinstance(provenance.get(key), str) or not provenance[key].strip():
            raise QualificationError("evaluation_provenance_required")
    if not _digest(report.get("prompt_rubric_sha256")) or not isinstance(report.get("model"), str):
        raise QualificationError("evaluation_identity_required")
    validate_thresholds(report.get("threshold_score"), report.get("threshold_confidence"))
    if _classes(report.get("source_classes")) != classes:
        raise QualificationError("report_source_classes_mismatch")
    rows = report.get("rows")
    if not isinstance(rows, list) or not rows:
        raise QualificationError("evaluation_rows_required")
    seen, groups, source_splits, source_groups, held = {}, {}, {}, {}, []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("task_id"), str) or not row["task_id"].strip():
            raise QualificationError("invalid_evaluation_row")
        identity = row["task_id"]
        split = row.get("split")
        if split not in ("development", "held_out"):
            raise QualificationError("invalid_evaluation_split")
        if identity in seen:
            raise QualificationError("duplicate_or_leaked_task")
        seen[identity] = split
        group = row.get("group_id")
        if not isinstance(group, str) or not group.strip():
            raise QualificationError("independent_task_group_required")
        if group in groups and groups[group] != split:
            raise QualificationError("development_group_leakage")
        groups[group] = split
        source_hash = row.get("source_sha256")
        if not _digest(source_hash):
            raise QualificationError("evaluation_source_hash_required")
        if source_hash in source_splits and source_splits[source_hash] != split:
            raise QualificationError("development_source_leakage")
        source_splits[source_hash] = split
        if source_hash in source_groups and source_groups[source_hash] != group:
            raise QualificationError("source_group_mismatch")
        source_groups[source_hash] = group
        if split != "held_out" or row.get("source_class") not in classes:
            continue
        if row.get("route_verified") is not True or not _digest(row.get("source_sha256")):
            raise QualificationError("unverified_evaluation_route")
        if (not isinstance(row.get("arms_verified"), list)
                or any(not isinstance(arm, str) for arm in row["arms_verified"])
                or sorted(row["arms_verified"]) != ["baseline", "local", "select", "shadow"]
                or row.get("cache_state") not in ("cold", "warm", "mixed")
                or type(row.get("trial")) is not int or row["trial"] < 1):
            raise QualificationError("complete_matched_arms_required")
        for key in ("critical_evidence_total", "critical_evidence_retained", "baseline_input_tokens",
                    "selected_input_tokens", "jev_input_tokens", "baseline_output_tokens",
                    "selected_output_tokens", "jev_output_tokens"):
            if not _count(row.get(key)):
                raise QualificationError("complete_evaluation_metrics_required")
        if row["critical_evidence_total"] < 1 or row["critical_evidence_retained"] > row["critical_evidence_total"]:
            raise QualificationError("invalid_critical_evidence_counts")
        for key in ("baseline_success", "selected_success"):
            if type(row.get(key)) is not bool:
                raise QualificationError("independent_task_outcomes_required")
        for key in ("baseline_total_cost_usd", "selected_total_cost_usd", "jev_cost_usd"):
            if not _number(row.get(key)):
                raise QualificationError("complete_evaluation_metrics_required")
        if row["selected_total_cost_usd"] < row["jev_cost_usd"]:
            raise QualificationError("selected_cost_must_include_jev")
        for key in ("baseline_latency_ms", "selected_latency_ms"):
            if not _number(row.get(key), positive=True):
                raise QualificationError("complete_evaluation_metrics_required")
        held.append(row)
    if not held:
        raise QualificationError("held_out_evaluation_required")
    for source_class in classes:
        if len({row["group_id"] for row in held if row["source_class"] == source_class}) < MIN_HELD_OUT_TASKS:
            raise QualificationError("insufficient_held_out_tasks")
    retained = all(row["critical_evidence_retained"] == row["critical_evidence_total"] for row in held)
    regressions = sum(row["baseline_success"] and not row["selected_success"] for row in held)
    def tokens_saved(row: dict) -> int:
        return (row["baseline_input_tokens"] + row["baseline_output_tokens"] - row["selected_input_tokens"]
                - row["selected_output_tokens"] - row["jev_input_tokens"] - row["jev_output_tokens"])
    tokens = sum(tokens_saved(row) for row in held)
    cost = math.fsum(row["baseline_total_cost_usd"] - row["selected_total_cost_usd"] for row in held)
    latency_ratio = (_percentile95([row["selected_latency_ms"] for row in held]) /
                     _percentile95([row["baseline_latency_ms"] for row in held]))
    # Gate each class separately: a good source class cannot hide a bad one.
    for source_class in classes:
        group = [row for row in held if row["source_class"] == source_class]
        if (not all(row["critical_evidence_retained"] == row["critical_evidence_total"] for row in group)
                or any(row["baseline_success"] and not row["selected_success"] for row in group)):
            raise QualificationError("evidence_or_task_regression")
        if (sum(tokens_saved(row) for row in group) <= 0
                or math.fsum(row["baseline_total_cost_usd"] - row["selected_total_cost_usd"] for row in group) <= 0):
            raise QualificationError("positive_net_benefit_required")
        if _percentile95([row["selected_latency_ms"] for row in group]) > _percentile95([row["baseline_latency_ms"] for row in group]):
            raise QualificationError("p95_latency_regression")
    return {"report_sha256": canonical_sha256(report), "sample_size": len(held),
            "critical_evidence_retained": retained, "task_regressions": regressions,
            "net_tokens_saved": tokens, "net_cost_savings": cost,
            "p95_latency_ratio": latency_ratio, "held_out": True}


def validate_qualification(profile: Any, report: Any, *, model: str,
                           prompt_rubric_sha256: str, source_class: str,
                           expected_workload: Any = None) -> Dict[str, Any]:
    """Validate current identities and recompute every qualification metric."""
    if (not isinstance(profile, dict) or type(profile.get("version")) is not int
            or profile["version"] != 1 or not isinstance(report, dict)):
        raise QualificationError("qualified_profile_required")
    workload_keys = {"harness", "harness_version", "primary_model", "primary_provider"}
    provenance = report.get("provenance", {})
    if (not isinstance(expected_workload, dict) or set(expected_workload) != workload_keys
            or not isinstance(provenance, dict)
            or any(not isinstance(expected_workload[key], str) or not expected_workload[key].strip()
                   or expected_workload[key] != provenance.get(key) for key in workload_keys)):
        raise QualificationError("qualified_workload_identity_required")
    classes = _classes(profile.get("source_classes"))
    if source_class not in classes:
        raise QualificationError("unqualified_source_class")
    for key, expected in (("model", model), ("prompt_rubric_sha256", prompt_rubric_sha256)):
        if profile.get(key) != expected or report.get(key) != expected:
            raise QualificationError("qualification_identity_mismatch")
    validate_thresholds(profile.get("threshold_score"), profile.get("threshold_confidence"))
    for key in ("threshold_score", "threshold_confidence"):
        if profile[key] != report.get(key):
            raise QualificationError("qualification_threshold_mismatch")
    metrics = summarize_report(report, classes)
    claimed = profile.get("qualification")
    if not isinstance(claimed, dict) or set(claimed) != set(metrics):
        raise QualificationError("qualification_summary_mismatch")
    for key, expected in metrics.items():
        actual = claimed[key]
        if type(expected) is float:
            if not _number(actual) or not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12):
                raise QualificationError("qualification_summary_mismatch")
        elif type(actual) is not type(expected) or actual != expected:
            raise QualificationError("qualification_summary_mismatch")
    return {"threshold_score": profile["threshold_score"],
            "threshold_confidence": profile["threshold_confidence"], **metrics}


def _load_json(path: Path, limit: int) -> Dict[str, Any]:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise QualificationError("qualification_file_limit")
        value = json.loads(data.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise QualificationError("invalid_qualification_json")
        canonical_sha256(value)
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, QualificationError):
            raise
        raise QualificationError("qualification_file_unavailable") from None


def load_qualification(path: str | Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load operator-configured profile and a bounded report beneath its folder."""
    profile_path = Path(path)
    if not profile_path.is_absolute():
        raise QualificationError("absolute_profile_path_required")
    profile_path = profile_path.resolve()
    profile = _load_json(profile_path, MAX_PROFILE_BYTES)
    relative = profile.get("report_path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise QualificationError("relative_report_path_required")
    report_path = (profile_path.parent / relative).resolve()
    try:
        report_path.relative_to(profile_path.parent)
    except ValueError:
        raise QualificationError("report_outside_profile_directory") from None
    report = _load_json(report_path, MAX_REPORT_BYTES)
    if (not isinstance(profile.get("qualification"), dict)
            or profile["qualification"].get("report_sha256") != canonical_sha256(report)):
        raise QualificationError("qualification_report_hash_mismatch")
    return profile, report
