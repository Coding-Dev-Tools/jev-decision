"""Reproducible four-arm report assembly; never launches a paid campaign.

Harness adapters collect observations. This module verifies artifact identities,
independently grades exact answers, and keeps incomplete measurements unknown.
An observation is a local evidence record, not a provider billing attestation.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import date
from pathlib import Path

from .harness_guards import PROMPT_RUBRIC_SHA256
from .qualification import canonical_sha256, summarize_report
from .runtime import DEFAULT_MODEL

ARMS = ("baseline", "local", "shadow", "select")


def arm_order(index):
    """Rotate the four arms across independent tasks, preserving all positions."""
    offset = index % len(ARMS)
    return list(ARMS[offset:] + ARMS[:offset])


def _count(value):
    return type(value) is int and value >= 0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _sum_known(values):
    return sum(values) if all(_number(value) for value in values) else None


def _price_identity(prices, provenance):
    try:
        date.fromisoformat(prices.get("as_of", ""))
    except (ValueError, TypeError):
        return False
    return (prices.get("currency") == "USD" and prices.get("jev_model") == DEFAULT_MODEL
            and prices.get("input_convention") == "inclusive_of_cache"
            and all(prices.get(key) == provenance.get(key) for key in ("primary_model", "primary_provider"))
            and isinstance(prices.get("sources"), list) and bool(prices["sources"])
            and all(isinstance(url, str) and url.startswith("https://") and "@" not in url
                    for url in prices["sources"]))


def modeled_primary_cost(usage, prices):
    """Input is inclusive of cache reads/writes; adapters normalize this explicitly."""
    names = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    if not isinstance(usage, dict) or not all(_count(usage.get(key)) for key in names):
        return None
    rates = ("input_per_million", "output_per_million", "cache_read_per_million", "cache_write_per_million")
    if not all(_number(prices.get(key)) for key in rates):
        return None
    uncached = usage["input_tokens"] - usage["cache_read_tokens"] - usage["cache_write_tokens"]
    if uncached < 0:
        return None
    return (uncached * prices[rates[0]] + usage["output_tokens"] * prices[rates[1]] +
            usage["cache_read_tokens"] * prices[rates[2]] + usage["cache_write_tokens"] * prices[rates[3]]) / 1_000_000


def _bootstrap_interval(values, seed=0):
    """Deterministic task bootstrap: uncertainty, not a population guarantee."""
    if not values or not all(_number(abs(value)) for value in values):
        return None
    rng = random.Random(seed)
    draws = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(2000))
    return [draws[49], draws[1949]]


def _p95(values):
    return sorted(values)[max(0, math.ceil(len(values) * .95) - 1)] if values else None


def load_dataset(path):
    path = Path(path).resolve()
    dataset = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(dataset, dict) or dataset.get("version") != 1 or
            dataset.get("label_method") not in {"human", "deterministic"} or not dataset.get("cases")):
        raise ValueError("invalid_dataset")
    identities, partitions, source_groups = set(), {}, {}
    for case in dataset["cases"]:
        for key in ("task_id", "group_id", "goal", "source_class", "source", "source_sha256"):
            if not isinstance(case.get(key), str) or not case[key]:
                raise ValueError("dataset_identity_required")
        identity, group, split = case["task_id"], case["group_id"], case.get("split")
        if identity in identities or split not in {"development", "held_out"}:
            raise ValueError("duplicate_task_or_invalid_split")
        identities.add(identity)
        if group in partitions and partitions[group] != split:
            raise ValueError("source_group_leaks_across_splits")
        partitions[group] = split
        source = (path.parent / case["source"]).resolve()
        source.relative_to(path.parent)
        if source.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("source_limit")
        if hashlib.sha256(source.read_bytes()).hexdigest() != case["source_sha256"]:
            raise ValueError("source_hash_mismatch")
        if case["source_sha256"] in source_groups and source_groups[case["source_sha256"]] != group:
            raise ValueError("source_group_mismatch")
        source_groups[case["source_sha256"]] = group
        facts = case.get("critical_facts")
        if not isinstance(facts, list) or not facts or not all(isinstance(fact, str) and fact for fact in facts):
            raise ValueError("independent_critical_facts_required")
        if "expected_answer" not in case:
            raise ValueError("independent_task_answer_required")
    return dataset


def assemble_report(dataset, observations, provenance, prices):
    """Join matched arms; no inferred success, invented token counts or invoice claims.

    Tool response hashes include JSON envelopes, omission markers and metadata.
    Adapters must measure the entire task, including recovery, retries and Jev.
    """
    if not isinstance(observations, list):
        raise ValueError("invalid_observations")
    cases = {case["task_id"]: case for case in dataset["cases"]}
    by_identity = {}
    for observation in observations:
        key = observation.get("task_id"), observation.get("arm")
        if key[0] not in cases or key[1] not in ARMS or key in by_identity:
            raise ValueError("unmatched_or_duplicate_observation")
        by_identity[key] = observation
    if len(by_identity) != 4 * len(cases):
        raise ValueError("four_matched_arms_required")
    rows, arm_records, campaign_costs = [], [], []
    price_verified = _price_identity(prices, provenance)
    complete_order = True
    for index, case in enumerate(dataset["cases"]):
        matched = {}
        for position, arm in enumerate(arm_order(index)):
            observation = by_identity[(case["task_id"], arm)]
            response = observation.get("tool_response")
            route = observation.get("route", {})
            route_ok = (observation.get("source_sha256") == case["source_sha256"] and
                        isinstance(response, dict) and isinstance(response.get("output"), str) and
                        observation.get("tool_response_sha256") == canonical_sha256(response) and
                        response.get("source_sha256") == case["source_sha256"] and
                        observation.get("route_verified") is True and
                        all(route.get(key) == provenance.get(key) for key in
                            ("harness", "harness_version", "primary_model", "primary_provider")) and
                        isinstance(observation.get("trace_sha256"), str) and
                        len(observation["trace_sha256"]) == 64 and
                        all(char in "0123456789abcdef" for char in observation["trace_sha256"]))
            stats = response.get("stats", {}) if isinstance(response, dict) else {}
            if arm in {"shadow", "select"}:
                route_ok &= (stats.get("mode") in ({"shadow"} if arm == "shadow" else {"select", "experimental_select"})
                    and stats.get("requested_model") == DEFAULT_MODEL and stats.get("resolved_model") == DEFAULT_MODEL
                    and stats.get("prompt_rubric_sha256") == PROMPT_RUBRIC_SHA256
                    and stats.get("source_class") == case["source_class"] and stats.get("status") == "ok")
                if arm == "select":
                    route_ok &= stats.get("threshold_score") == .25 and stats.get("threshold_confidence") == .9
            else:
                route_ok &= stats.get("mode") == "off" and stats.get("calls") == 0
            route_ok &= (type(observation.get("retries")) is int and observation["retries"] >= 0
                         and type(observation.get("recovery_calls")) is int and observation["recovery_calls"] >= 0
                         and _number(observation.get("preprocessing_ms"))
                         and _number(observation.get("total_elapsed_ms"))
                         and observation["total_elapsed_ms"] >= observation["preprocessing_ms"])
            complete_order &= observation.get("order") == position
            usage = observation.get("primary_usage", {})
            primary_cost = modeled_primary_cost(usage, prices) if price_verified else None
            jev_usage = observation.get("jev_usage", {})
            jev_in, jev_out = jev_usage.get("input_tokens"), jev_usage.get("output_tokens")
            if arm in {"shadow", "select"}:
                route_ok &= (stats.get("usage") == jev_usage and _count(stats.get("attempts")) and
                             _count(stats.get("calls")) and observation.get("retries", -1) >= max(0, stats["attempts"] - stats["calls"]))
            else:
                route_ok &= jev_in == 0 and jev_out == 0
            jev_cost = ((jev_in * prices["jev_input_per_million"] + jev_out * prices["jev_output_per_million"]) / 1_000_000
                if price_verified and all(_count(value) for value in (jev_in, jev_out)) and
                all(_number(prices.get(key)) for key in ("jev_input_per_million", "jev_output_per_million")) else None)
            total_cost = _sum_known([primary_cost, jev_cost])
            campaign_costs.append(total_cost)
            output = response.get("output", "") if isinstance(response, dict) else ""
            record = {"task_id": case["task_id"], "arm": arm, "order": observation.get("order"),
                      "route_verified": route_ok, "trace_sha256": observation.get("trace_sha256"),
                      "source_sha256": case["source_sha256"],
                      "tool_response_sha256": observation.get("tool_response_sha256"),
                      "tool_response_bytes": len(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                      "primary_usage": usage, "jev_usage": jev_usage,
                      "retries": observation.get("retries"), "recovery_calls": observation.get("recovery_calls"),
                      "cache_state": observation.get("cache_state"), "trial": observation.get("trial"),
                      "total_elapsed_ms": observation.get("total_elapsed_ms"),
                      "preprocessing_ms": observation.get("preprocessing_ms"),
                      "modeled_primary_cost_usd": primary_cost, "modeled_jev_cost_usd": jev_cost,
                      "modeled_total_cost_usd": total_cost,
                      "critical_evidence_total": len(case["critical_facts"]),
                      "critical_evidence_retained": sum(fact in output for fact in case["critical_facts"]),
                      "success": (observation["answer"] == case["expected_answer"] if "answer" in observation else None)}
            matched[arm] = record
            arm_records.append(record)
        baseline, selected = matched["baseline"], matched["select"]
        comparable = all(record["cache_state"] == selected["cache_state"] and record["trial"] == selected["trial"]
                         for record in matched.values())
        row = {"task_id": case["task_id"], "group_id": case["group_id"], "split": case["split"],
               "source_class": case["source_class"], "source_sha256": case["source_sha256"],
               "route_verified": comparable and all(value["route_verified"] for value in matched.values()),
               "arms_verified": [arm for arm in ARMS if comparable and matched[arm]["route_verified"]],
               "trial": selected["trial"], "cache_state": selected["cache_state"],
               "critical_evidence_total": selected["critical_evidence_total"],
               "critical_evidence_retained": selected["critical_evidence_retained"],
               "baseline_success": baseline["success"], "selected_success": selected["success"],
               "baseline_input_tokens": baseline["primary_usage"].get("input_tokens"),
               "baseline_output_tokens": baseline["primary_usage"].get("output_tokens"),
               "selected_input_tokens": selected["primary_usage"].get("input_tokens"),
               "selected_output_tokens": selected["primary_usage"].get("output_tokens"),
               "jev_input_tokens": selected["jev_usage"].get("input_tokens"),
               "jev_output_tokens": selected["jev_usage"].get("output_tokens"),
               "baseline_total_cost_usd": baseline["modeled_total_cost_usd"],
               "selected_total_cost_usd": selected["modeled_total_cost_usd"],
               "jev_cost_usd": selected["modeled_jev_cost_usd"],
               "baseline_latency_ms": baseline["total_elapsed_ms"],
               "selected_latency_ms": selected["total_elapsed_ms"]}
        rows.append(row)
    classes = sorted({case["source_class"] for case in dataset["cases"]})
    labels = [{key: case[key] for key in ("task_id", "group_id", "split", "critical_facts", "expected_answer")}
              for case in dataset["cases"]]
    report = {"version": 1, "kind": "jev_selection_evaluation", "model": DEFAULT_MODEL,
              "prompt_rubric_sha256": PROMPT_RUBRIC_SHA256, "threshold_score": .25,
              "threshold_confidence": .9, "source_classes": classes,
              "provenance": {**provenance, "dataset_sha256": canonical_sha256(dataset),
                  "labels_sha256": canonical_sha256(labels), "label_method": dataset["label_method"],
                  "split_by": "task", "price_snapshot_sha256": canonical_sha256(prices),
                  "counterbalanced": complete_order, "campaign_cost_usd": _sum_known(campaign_costs)},
              "rows": rows, "arms": arm_records, "invoice_verified": False,
              "price_snapshot": {key: prices.get(key) for key in ("as_of", "currency", "sources", "primary_model",
                  "primary_provider", "jev_model", "input_convention", "input_per_million", "output_per_million",
                  "cache_read_per_million", "cache_write_per_million", "jev_input_per_million", "jev_output_per_million")}}
    held = [row for row in rows if row["split"] == "held_out"]
    deltas = [row["baseline_total_cost_usd"] - row["selected_total_cost_usd"] for row in held
              if _number(row["baseline_total_cost_usd"]) and _number(row["selected_total_cost_usd"])]
    grouped = {}
    for row in held:
        if _number(row["baseline_total_cost_usd"]) and _number(row["selected_total_cost_usd"]):
            grouped.setdefault(row["group_id"], []).append(row["baseline_total_cost_usd"] - row["selected_total_cost_usd"])
    group_means = [sum(values) / len(values) for values in grouped.values()]
    n = len(held)
    group_count = len({row["group_id"] for row in held})
    report["uncertainty"] = {"held_out_tasks": n, "source_groups": group_count,
        "cost_complete_pairs": len(deltas), "mean_cost_savings_bootstrap_95": _bootstrap_interval(group_means),
        "zero_observed_regressions_one_sided_95_upper_rate": (1 - .05 ** (1 / group_count)) if group_count and
            all(row["baseline_success"] is True and row["selected_success"] is True for row in held) else None,
        "method": "2000 deterministic bootstrap draws of source-group mean modeled cost savings; zero-event binomial bound across groups. Independence is assumed, not proven."}
    try:
        summarize_report(report, classes)
        report["qualification_check"] = {"eligible": True}
    except ValueError as error:
        report["qualification_check"] = {"eligible": False, "reason": str(error)}
    return report


def write_profile(report, report_path, profile_path):
    """Explicit operator action; never enables a runtime or modifies settings."""
    report_path, profile_path = Path(report_path).resolve(), Path(profile_path).resolve()
    relative = report_path.relative_to(profile_path.parent)
    # Derived inspection fields cannot participate in their own canonical hash.
    metrics = summarize_report(report, report["source_classes"])
    profile = {"version": 1, "model": report["model"], "prompt_rubric_sha256": report["prompt_rubric_sha256"],
        "source_classes": report["source_classes"], "threshold_score": report["threshold_score"],
        "threshold_confidence": report["threshold_confidence"], "report_path": relative.as_posix(),
        "qualification": metrics}
    with profile_path.open("x", encoding="utf-8") as stream:
        json.dump(profile, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return profile
