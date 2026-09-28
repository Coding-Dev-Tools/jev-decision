"""Collect existing four-arm observations or reproduce an offline integration report.

This command never launches a harness, sends a provider request or enables
selection. Live observations must come from a separately budgeted campaign.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jev_decision.client import JevClient  # noqa: E402
from jev_decision.evaluation import (  # noqa: E402
    arm_order,
    assemble_report,
    load_dataset,
    write_profile,
)
from jev_decision.evidence import read_evidence_file  # noqa: E402
from jev_decision.harness_guards import _select_from_shadow, _spans  # noqa: E402
from jev_decision.qualification import canonical_sha256  # noqa: E402


def local_repetitions(text, source_class):
    """Experimental deterministic control: compact only identical unprotected runs."""
    lines, result = text.splitlines(keepends=True), []
    for span in _spans(lines, source_class, 1):
        content = span["_text"]
        parts = content.splitlines(keepends=True)
        if not span["protected"] and len(parts) > 2 and len(set(parts)) == 1:
            replacement = parts[0] + "[Repeated identical source lines %d-%d; original retained]\n" % (span["start_line"] + 1, span["end_line"])
            result.append(replacement if len(replacement) < len(content) else content)
        else:
            result.append(content)
    return "".join(result)


def offline_observations(dataset, directory):
    observations = []
    route = {"harness": "offline-integration-fixture", "harness_version": "0.3.0",
             "primary_model": "not_invoked", "primary_provider": "none"}
    for index, case in enumerate(dataset["cases"]):
        source = (directory / case["source"]).resolve()
        baseline = read_evidence_file(str(source), case["goal"], [directory], mode="off", source_class=case["source_class"])
        if baseline["page"]["has_more"]:
            raise ValueError("offline_fixture_must_fit_one_page")
        shadow = read_evidence_file(str(source), case["goal"], [directory], mode="shadow",
                                    source_class=case["source_class"], client=JevClient(offline_mode=True))
        selected = copy.deepcopy(shadow)
        selected["output"], selected["stats"] = _select_from_shadow(shadow["output"], shadow["stats"], source_ref=shadow["source_ref"])
        local = copy.deepcopy(baseline)
        local["output"] = local_repetitions(baseline["output"], case["source_class"])
        local["stats"]["control"] = "exact_unprotected_repetition"
        responses = {"baseline": baseline, "local": local, "shadow": shadow, "select": selected}
        for position, arm in enumerate(arm_order(index)):
            response = responses[arm]
            observations.append({"task_id": case["task_id"], "arm": arm, "order": position, "trial": 1,
                "source_sha256": case["source_sha256"], "tool_response": response,
                "tool_response_sha256": canonical_sha256(response), "trace_sha256": "0" * 64,
                "route": route, "route_verified": False, "cache_state": "cold",
                "primary_usage": {key: None for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")},
                "jev_usage": {"input_tokens": 0, "output_tokens": 0},
                "retries": 0, "recovery_calls": 0, "total_elapsed_ms": None,
                "preprocessing_ms": None})
    return observations, {**route, "run_mode": "offline", "campaign_budget_usd": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--offline", action="store_true")
    source.add_argument("--observations", help="JSON array of actual four-arm observations")
    parser.add_argument("--provenance", help="JSON campaign identity and explicit authorized budget")
    parser.add_argument("--prices", help="JSON dated normalized price snapshot; never an invoice")
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", help="Write a new profile only if every qualification gate passes")
    args = parser.parse_args(argv)
    dataset_path = Path(args.dataset).resolve()
    dataset = load_dataset(dataset_path)
    if args.offline:
        observations, provenance = offline_observations(dataset, dataset_path.parent)
        prices = {"status": "unmeasured_offline"}
    else:
        if not args.provenance or not args.prices:
            parser.error("Observations require provenance and a price snapshot")
        observations = json.loads(Path(args.observations).read_text(encoding="utf-8-sig"))
        provenance = json.loads(Path(args.provenance).read_text(encoding="utf-8-sig"))
        prices = json.loads(Path(args.prices).read_text(encoding="utf-8-sig"))
    report = assemble_report(dataset, observations, provenance, prices)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if args.profile:
        write_profile(report, output, Path(args.profile))
    print(json.dumps({"report": str(output), "qualification": report["qualification_check"],
                      "provider_calls": 0, "runtime_changed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
