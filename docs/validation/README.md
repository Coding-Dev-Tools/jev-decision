# Portable v0.3 validation

The source validation on 2026-09-28–29 used Windows, Python 3.12.10, Node 24.15.0 and official MCP SDK 2.2.0. It made no live provider calls and changed no user harness profiles or credentials.

| Check | Observed result |
| --- | --- |
| Full Python suite | 572 passed, 1 skipped (Windows symlink privilege) |
| TypeScript build + Node suite | 86 passed |
| Shared Python/TypeScript fixtures | Passed against compiled TypeScript |
| Provider usage limits | Input overruns reject answers before caching; unsafe telemetry stays unknown and unsupported accounting retains a conservative hold |
| Actual stdio subprocess | Legacy handshake, automatic discovery and 2026-07-28 passed; separate 2024-11-05 negotiation passed |
| Pipe integrity | Windows Unicode, malformed/oversized input recovery and UTF-8 JSON stdin passed |
| Native capture wrapper | PowerShell preserved producer exit 7, stdout and stderr artifacts; POSIX counterpart runs in CI |
| Packaging | Wheel and sdist installed outside checkout, core import without SDK, then legacy/current SDK subprocess smoke passed |
| npm packaging | Packed archive installed outside checkout; offline client invocation passed |
| Command Code recipe | Temporary user/project install, explicit skill, shared-entry conflict protection, restore and capture-to-off-read passed; installed 1.66.0 source checked |
| Evidence and policy review | Opened-file validation, real Windows junction races, stale roots, policy downgrade matrix and credential-free diagnostics passed |
| Setup credential preservation | Existing keyring references survive interactive reconfiguration; fresh setup/backend changes still offer masked entry |
| Credential format | Storage, environment loading and client validation share the same printable-ASCII contract; invalid input is rejected before vault access or replacement |
| Qualification review | Source-span grading excludes markers/redaction/gaps, matches pages across arms, detects tampering and rejects old grading methods |
| Release review regressions | Rounded-score feasibility, typed JSON equality, restored Choice labels and Score legends, omitted-mode off defaults, timing consistency, severity protection and isolated harness scopes passed |
| Installed capture | CLI preserves argv, binary streams and producer exit status without runtime configuration; original checkout wrappers retained |
| Static checks | Full configured Ruff rules and Git whitespace checks passed |

CI runs the suite on Windows, macOS and Linux with Python 3.9–3.14. Core-only Python 3.9 skips optional SDK tests. Python 3.12 jobs also install wheel and source artifacts outside the checkout; Node 22 and 24 jobs on all three systems check npm archive installation. CI status must be read for the exact PR head before claiming those remote checks passed.

The [comprehensive release review](release-review-20260928.md) records the four independent review lanes, corrected edge cases and remaining evidence boundaries. The [merge-readiness review of 2026-09-30](release-review-20260930.md) records later fixes, including generated POSIX venv interpreters, library opt-in, placeholder credentials, and evidence name screening. It also covers the escalate-only `jev hook` guard, the compact MCP schema, and the validation run on Python 3.9–3.14.

## Offline four-arm report

[portable-offline.json](portable-offline.json) records four synthetic source tasks, including two held-out groups, and 16 matched arm records. All labeled critical facts remain available in verified original source spans, using `source_spans_v1` grading. No primary model or Jev provider was invoked. Primary tokens, task success, total task latency and modeled cost remain unknown. The report is ineligible with `live_evaluation_required`; it cannot produce an enabled selection profile.

Reproduction and actual campaign observation contracts are in [EVALUATION.md](../EVALUATION.md). Report bytes include full tool envelopes, omission markers and metadata; bytes are not substituted for tokens. A repeated run can change response hashes because local artifact paths and runtime timing metadata differ; original source and label hashes remain the reproducibility anchors.

No named desktop/CLI harness version is promoted to live verified by these tests. The earlier Command Code pilot is preserved as an integration check. Paid qualification, OS vault usability and real client/provider invocations remain deployment-specific work. No universal token or latency savings claim is supported, and no automatic omission profile ships.

## Command Code and memory integration

The [2026-10-01 local review](command-code-memory-20261001.md) records structured
memory advice, the optional Engraphis question bridge, Command Code usability
repairs and clean package verification. It remains separate from live client
invocation, workload benefit, remote CI and publication.
