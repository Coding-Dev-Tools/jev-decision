# Migrating from 0.2 to 0.3

This release intentionally changes unsafe or misleading result contracts. Update callers before upgrading a running integration.

1. Remove code that treats `allow_auto`, `escalate_to`, or `is_complete` as authority. Command assessment reports risk; completion assessment reports evidence support and gaps. Native permission checks and executed verifiers remain authoritative.
2. Check `status` and `source` before consuming an assessment. Missing keys, malformed provider replies and outages are unavailable. Offline mode returns no guessed decision. Unavailable results must preserve the normal model workflow and original evidence.
3. Use `jev-1.13.0` and the official HTTPS endpoint. The client validates all IDs, types, completeness, distributions, legends and the resolved model. Noul values are probabilities, Score values can be fractional, and missing usage or Noul confidence is null. Python returns the caller's original IDs, Choice labels and Score legends after validating the sanitized wire response, including cache hits; keep these values non-sensitive if logging results.
4. Supply descriptive Score criteria, rather than only numeric scales. Keep original artifacts, source references and command exit status. Batched evidence uses complete windows; oversized windows are retained instead of truncated for a provider call.
5. Automatic pruning now defaults off. Previous percentage-savings examples were not measured evidence and have been removed. Enable omission only after matched development and held-out validation demonstrates retained required facts and useful net savings.
6. Replace editable-checkout harness launchers with a built, versioned managed runtime. Enter credentials through `jev auth set --gui` on Windows or masked local terminal setup. Do not embed keys in MCP settings, skills, logs or shell command arguments.
7. The managed ledger is shared across processes at one runtime home. Every attempt reserves the documented maximum cost; unknown usage stays reserved. A separate provider SDK or different JEV_HOME can bypass this local accounting and is outside the managed integration.
8. Reinstall using the preview/apply workflow. Keep the private backup manifest for selective restoration. Restart existing Jev processes after credential, model-policy or configuration changes. Updating a launcher or tunnel alias does not replace an already running child; restart only the owned Jev process and verify its actual interpreter path before making a client request. Distinguish configured, authenticated and operational status.

No benchmark grade, permission boundary, completion claim or memory mutation should be based solely on a Jev assessment. The TypeScript guard now awaits the supplied client; it no longer silently uses a fallback path.

For memory integrations, prefer `assess_memory_relation` and `assess_memory_relevance` over the compatibility string classifier. They return null unknown judgments with complete advice metadata and enforce bounded excerpts without truncation. Python command/completion helpers also reject stale decisions on failed, offline, heuristic, fallback or mismatched batches. The [memory-system guide](MEMORY_SYSTEMS.md) explains the optional Engraphis question bridge and its unknown Noul-confidence limitation.

## Portable runtime v2 and evidence API changes

New installations load offline until `jev setup` records an explicit choice. Setup offers DPAPI, optional OS keyring, or an environment reference; UTC is the new portable timezone default. Budgets are operator-selected finite nonnegative amounts, with zero disabling requests. Core/CLI installation supports Python 3.9; install the optional `mcp` extra on Python 3.10+ for the official SDK v2 adapter.

Existing v1 config keeps its budget, enabled state, New York timezone, credential file and ledger. The old pruning boolean cannot enable unqualified omission. Keep the same physical runtime home through upgrade; generated MCP entries carry `JEV_HOME`, and CLI skills carry `--runtime-home`. Timezone changes do not reset the active spend window early.

Setup saves canonical workspace authorization paths. Reads preserve those paths;
replacing a saved root with a symlink or junction cannot authorize its new target.
Loading a redirected saved root fails until the operator reviews workspace setup.
Registry credential files, private key stores and `.kube`/`.docker` configuration
directories are excluded from evidence reads even inside an approved workspace.

Repeating interactive setup with an existing keyring configuration preserves its credential reference while changing budget, workspace or harness settings. Keyring presence remains unknown without unlocking the vault. Use `jev auth set` explicitly to add or replace the key; setup does not infer that an unknown credential is missing.

Use `harness install/restore --target NAME --scope user|project`; project scope additionally needs an absolute root. CLI mutations require an explicit target or the saved setup target. Previewing or installing never proves connection, authentication or live client invocation.

Evidence now has three explicit modes. `off` performs no semantic requests, `shadow` retains all evidence while scoring eligible records, and `select` requires a qualified local profile plus `expected_workload` in Python, `workload` in MCP, or `--workload FILE` in the CLI. `allow_prune=True` remains a compatibility spelling for selection and cannot bypass qualification. The old small-pilot result is not sufficient.

The saved mode is a ceiling for MCP/CLI calls. An omitted per-call mode always uses `off`; an explicit mode may only downgrade the saved ceiling. Use `jev setup --non-interactive --selection-mode shadow` after initial setup to allow measurement, or `--selection-mode off` to disable it; restart existing server processes. A saved profile path alone never enables selection. Local status, discovery and off evidence reads do not acquire/decrypt credentials; live diagnostics and inference are separate.

Evaluation reports now require source-bound retention grading (`source_spans_v1`). Regenerate earlier reports and qualification profiles from the original sources and observations; rendered omission markers and redaction placeholders cannot satisfy critical-fact labels. A mode-only setup update preserves disabled state and skips unrelated credential and harness setup.

File reads return page metadata and a `source_ref`. Pass `expected_source_sha256` with later range reads; changed sources fail. Redaction retains original line identities. Public raw-text pruning can measure but cannot omit content without a recoverable source. The old `MCPServer.handle_request` implementation is removed; embedders should use `create_sdk_server()` or the existing stdio entry points. The SDK owns wire-level compatibility.

See [integration recipes](INTEGRATIONS.md), [evidence capture](EVIDENCE.md), and [evaluation](EVALUATION.md) before enabling a profile. These changes prepare v0.3 artifacts; preparation is not publication.
