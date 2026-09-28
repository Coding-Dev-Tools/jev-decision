# Portable v0.3 validation

The source validation on 2026-09-28 used Windows, Python 3.12.10, Node 24.15.0 and official MCP SDK 2.2.0. It made no live provider calls and changed no user harness profiles or credentials.

| Check | Observed result |
| --- | --- |
| Full Python suite | 355 passed, 1 skipped (Windows symlink privilege) |
| TypeScript build + Node suite | 81 passed |
| Shared Python/TypeScript fixtures | Passed against compiled TypeScript |
| Actual stdio subprocess | Legacy handshake, automatic discovery and 2026-07-28 passed; separate 2024-11-05 negotiation passed |
| Pipe integrity | Windows Unicode, malformed/oversized input recovery and UTF-8 JSON stdin passed |
| Native capture wrapper | PowerShell preserved producer exit 7, stdout and stderr artifacts; POSIX counterpart runs in CI |
| Packaging | Wheel and sdist installed outside checkout, core import without SDK, then legacy/current SDK subprocess smoke passed |
| npm packaging | Packed archive installed outside checkout; offline client invocation passed |
| Static checks | Python undefined/unused-name checks and Git whitespace checks passed |

CI runs the suite on Windows, macOS and Linux with Python 3.9–3.13. Core-only Python 3.9 skips optional SDK tests. Python 3.12 jobs also install wheel and source artifacts outside the checkout; all three Node jobs check npm archive installation. CI status must be read for the exact PR head before claiming those remote checks passed.

## Offline four-arm report

[portable-offline.json](portable-offline.json) records four synthetic source tasks, including two held-out groups, and 16 matched arm records. All labeled critical facts remain available. No primary model or Jev provider was invoked. Primary tokens, task success, total task latency and modeled cost remain unknown. The report is ineligible with `live_evaluation_required`; it cannot produce an enabled selection profile.

Reproduction and actual campaign observation contracts are in [EVALUATION.md](../EVALUATION.md). Report bytes include full tool envelopes, omission markers and metadata; bytes are not substituted for tokens. A repeated run can change response hashes because local artifact paths and runtime timing metadata differ; original source and label hashes remain the reproducibility anchors.

No named desktop/CLI harness version is promoted to live verified by these tests. The earlier Command Code pilot is preserved as an integration check. Paid qualification, OS vault usability and real client/provider invocations remain deployment-specific work. No universal token or latency savings claim is supported, and no automatic omission profile ships.
