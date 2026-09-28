# Workload qualification and reproducible reports

The current pilot (`scripts/benchmark_harness.py`) remains a small Command Code integration check. It does not qualify selection. The portable report at [validation/portable-offline.json](validation/portable-offline.json) uses four synthetic source tasks (two development, two held-out), four arms per task, zero provider requests and no primary model. Its token, cost and end-to-end latency measurements are unknown. It deliberately fails qualification.

Reproduce that integration report into a new file:

```sh
python scripts/evaluate_evidence.py --dataset examples/evaluation/dataset.json --offline --output /absolute/new-offline-report.json
```

## Four matched arms

| Arm | Evidence given to the primary model |
| --- | --- |
| `baseline` | Full sanitized unchanged output and its source reference |
| `local` | Only a deterministic control, with complete recoverable evidence |
| `shadow` | Jev scoring plus the full original page |
| `select` | The measured candidate selection, including omission markers and metadata |

The included deterministic control collapses only identical unprotected repeated records. It is an experiment control, not an automatic production transformation. `jev_decision.evaluation.local_repetitions` and the private `_select_from_shadow` helper provide reproducible candidate construction. The latter validates the original file/range and the exact shadow assessment before applying thresholds; it is absent from public MCP and CLI dispatch.

The vendor's [passage-classification cookbook](https://docs.typesafe.ai/cookbooks/classifying_rag_passages) also separates atomic questions from deterministic routing and calls for corpus-specific thresholds. Its illustrative thresholds are not qualification evidence for your logs, model, or harness. Neither a relevance score nor an injection classifier replaces permissions or verification.

## Collect an authorized live campaign

This repository's collector **does not launch paid experiments**. Before running your harness, agree an explicit total campaign budget and enforce it in the harness/provider account as well as the Jev runtime. Keep failed attempts and unknown usage charged conservatively. Do not infer permission to spend from the existence of these scripts.

1. Freeze independent critical-fact labels and expected task answers before model scoring. Split by source task/project using `group_id`; a group or exact source hash cannot cross development and held-out sets. Tune on development data only. Use at least 30 independent held-out groups per source class; this is a minimum engineering gate, not a statistical guarantee.
2. Record the exact source artifact hash, Jev package/model/rubric, harness version, primary model/provider and dated price snapshot. Keep the primary task prompt, tool route and grading fixed. Do not expose expected answers to the primary model.
3. Run each task through all four arms in `arm_order(index)` rotation: baseline/local/shadow/select, then local/shadow/select/baseline, and so on. Use the same trial and comparable cache condition across all four arms. Retain failures; do not select only favorable attempts.
4. Record actual complete tool responses and sanitized client traces. Measure from before preprocessing to task completion, including scoring, primary inference, retries, and recovery. Replaying precomputed selected text without charging its scoring cost/time cannot qualify selection.
5. Normalize primary usage to input tokens **including** cache-read and cache-write tokens, plus output tokens. Include every primary request and recovery. Record cache components separately so modeled cost uses the appropriate rates. Unknown values stay `null`; zero requires evidence.
6. Collect Jev usage across every attempt from result statistics. Cache hits have zero new usage; unknown retry usage stays unknown. Count all retries and recovery calls. Modeled cost is not verified billing.

Keep original artifacts and detailed traces user-owned. Public reports contain hashes and metrics, not raw private logs or credentials.

## Input contract and collector

A dataset uses `examples/evaluation/dataset.json` as its shape. Each case supplies `task_id`, `group_id`, `split`, `source_class`, relative source path and SHA-256, goal, independently labeled `critical_facts`, and `expected_answer`. Sources must remain beneath the dataset directory and match their original hashes. Critical facts must be distinct, nonempty source substrings; use descriptive facts that identify the evidence needed for the task. Load manifests with `load_dataset` before `assemble_report`; copied or modified dictionaries are not verified datasets, and sources are rechecked during collection.

The collector reconstructs each exact sanitized source page and its rendered response. All four arms must use the same line range, text hash and page limits. It counts critical facts only within contiguous retained original intervals: omission markers, redaction placeholders and text formed across omitted gaps earn no retention credit. Changed redacted lines are conservatively excluded. Reports include content-free retained ranges and `provenance.retention_method: "source_spans_v1"`. Reports and profiles made with the earlier rendered-substring grader must be regenerated from the original sources and observations; they cannot qualify by retaining old counts.

Observations are a JSON array with exactly one entry per `(task_id, arm)`:

```json
{
  "task_id": "task-001", "arm": "select", "order": 3,
  "trial": 1, "cache_state": "cold",
  "source_sha256": "64-lowercase-hex-source-hash",
  "tool_response": {"source_sha256": "...", "output": "...", "stats": {}},
  "tool_response_sha256": "canonical-full-response-hash",
  "trace_sha256": "sanitized-client-trace-hash", "route_verified": true,
  "route": {"harness": "chosen-client", "harness_version": "exact-version", "primary_model": "exact-pin", "primary_provider": "provider"},
  "answer": {"your": "task-answer"},
  "primary_usage": {"input_tokens": null, "output_tokens": null, "cache_read_tokens": null, "cache_write_tokens": null},
  "jev_usage": {"input_tokens": null, "output_tokens": null},
  "retries": 0, "recovery_calls": 0,
  "preprocessing_ms": null, "total_elapsed_ms": null
}
```

This is an intentionally incomplete illustration; copy the actual tool response, including its `source_ref`, `page` and full stats. The collector checks semantic-arm mode, requested/resolved Jev model, rubric, source class, thresholds, status, usage, source and response hashes. Baseline/local must make zero Jev calls. Responses from old rubrics, offline calls, unmatched trials, pages or cache conditions cannot be stamped current. Task success is computed against independent expected answers, not a reported success flag.

Canonical hashes use UTF-8 JSON, sorted keys, separators `(',', ':')`, `ensure_ascii=False`, and no NaN. `jev_decision.qualification.canonical_sha256` implements that convention.

The provenance JSON needs `run_mode: "live"`, the four route identity fields, and positive `campaign_budget_usd`. The collector derives campaign modeled cost across **all four arms**, counterbalancing, source/label hashes, and independent grading. The price JSON needs:

- `as_of` (ISO date), `currency: "USD"`, source HTTPS URLs in `sources`, `primary_model`, `primary_provider`, `jev_model`, and `input_convention: "inclusive_of_cache"`;
- `input_per_million`, `output_per_million`, `cache_read_per_million`, `cache_write_per_million`, `jev_input_per_million`, and `jev_output_per_million`.

Use actual dated provider rates applicable to that deployment. Incomplete price identity produces unknown costs and prevents qualification.

```sh
python scripts/evaluate_evidence.py --dataset /absolute/campaign/dataset.json --observations /absolute/campaign/observations.json --provenance /absolute/campaign/provenance.json --prices /absolute/campaign/prices.json --output /absolute/campaign/report.json
```

Reports keep primary usage, Jev usage, retries, recovery, complete serialized response bytes, total/preprocessing time, and modeled costs separately. They publish held-out task/source-group counts, cost-complete pair counts, a deterministic 2000-draw bootstrap interval for source-group mean modeled cost savings and a zero-observed-regressions binomial bound where applicable. The count and uncertainty accompany point estimates. Cost/usage summaries are not invoices, and declared route receipts are not an independent attestation of their author.

## Qualification and deployment

A profile is eligible only when every requested source class has all labeled critical facts retained, zero observed baseline-pass/selection-fail outcomes, positive total token savings **after both primary and Jev input/output**, positive modeled cost savings, and selected p95 total task time no greater than baseline p95. Missing measurements, source leakage, unverified routes, incomplete arms or insufficient independent held-out groups reject qualification.

Use `--profile /absolute/campaign/profile.json` with the collector to request profile generation. It writes a new profile only if all gates pass; report and profile must share a containing directory. It never changes runtime settings. The profile binds the exact report, model, rubric, thresholds and source classes. Changing these invalidates qualification.

After review, the operator may set `selection_mode: "select"` and `qualified_profile_path` in the runtime's public config. Restart existing processes. Every selection call must supply the actual four-field workload identity matching the report, through MCP `workload` or CLI `--workload workload.json`. Missing or changed harness/model identity retains evidence. This identity is a caller assertion: the generic MCP server cannot independently inspect another client's selected model. Keep profiles tied to the measured deployment and requalify after relevant changes.

Inconclusive workloads stay `off` or `shadow`. Nothing in the shipped offline report enables automatic omission.
