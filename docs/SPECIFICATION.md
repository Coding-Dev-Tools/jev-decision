# Jev advisory runtime contract, v0.3

The managed runtime pins `jev-1.13.0` and the exact official TypeSafe HTTPS endpoint. Native questions and complete typed answers follow the [provider API](https://docs.typesafe.ai/api); local limits bound latency, transmission and conservative accounting.

## Client results

Python and TypeScript expose `status`, `source`, `decisions`, requested/resolved model, usage, latency, attempts, request ID and a content-free error code. Unknown usage remains null, including retries with unknown earlier usage. Cache hits report zero new attempts/usage. Native Choice descriptions may be null. Score preserves fractional rubric positions, legends and provider rounding; it does not renormalize the wire response. Invalid or missing answers reject the whole batch.

Offline, missing credentials, expired deadlines and provider failures return no synthetic decisions. Command assessment cannot grant permission, completion assessment cannot certify execution, and advice cannot overwrite canonical benchmark or memory evidence.

## Execution and accounting

Fresh runtime loads stay disabled until configured. Explicit library construction can opt in, either by passing a runtime or by supplying a key (argument or `TYPESAFE_API_KEY`/`JEV_API_KEY`) before any configuration is saved. That opt-in uses the default daily cap and shared ledger. CLI and MCP processes pass their loaded runtime and never opt in implicitly. One monotonic deadline covers preprocessing, reservation, connection, transmission, bounded response reading, validation and settlement. A late connection cannot transmit after cancellation. Retry-After seconds/dates, transient failures including 529 and jitter stay inside one optional retry. Unknown or unfinished accounting retains a conservative reservation; it never creates a success/cache entry after the deadline.

Before every request, SQLite serializes a maximum-request reservation across processes sharing `JEV_HOME`. The configured nonnegative daily budget can exceed the former personal $1 setting; zero disables calls. UTC is the portable default. v1 settings keep their enabled state, budget, New York timezone and ledger history. Timezone changes take effect after the active accounting period so they cannot reset spend early. Provider usage and modeled cost remain distinct from invoices.

DPAPI protects Windows credentials. Optional OS keyring backends support macOS Keychain/Linux Secret Service; plaintext fallbacks are refused. An explicit environment variable reference supports headless use. Status inspects presence metadata without unlocking a keychain; it never claims authentication. TypeScript is an explicit unmanaged client and does not share Python's local cap.

## Evidence and profiles

`off` makes no semantic calls. `shadow` scores bounded eligible records while retaining evidence. `select` requires the exact recoverable original, a qualified profile/report, and matching workload identity. Unknown formats, unprocessed spans and uncertain answers remain intact. File reads preserve source line positions through redaction and expose bounded pagination and changed-hash rejection. Originals are never automatically removed.

The initial limits are 16 questions/~16 KiB per batch, two concurrent evaluations and a five-second selection deadline. Critical structural groups and adjacent context remain protected. These bounds are engineering defaults requiring workload calibration. Omission markers, JSON envelopes, retries, recovery and Jev all count toward measurement.

Qualification recomputes held-out metrics rather than trusting summary flags. It binds source/label/report hashes, Jev model/rubric/thresholds/source classes, explicit primary model/harness identity, independent task/project groups, matched arms/cache strata and campaign budget. It requires retained critical facts, no observed task-success loss, positive net tokens and modeled cost, and no p95 task-time increase. See [evaluation](EVALUATION.md).

## MCP and installation

The optional official Python MCP SDK v2 owns protocol negotiation, JSON-RPC framing and errors. It serves legacy and current clients with complete tool schemas. A bounded byte reader rejects oversized/invalid frames without echoing their payload. UTF-8 is explicit for Windows pipes. Core library/JSON CLI imports do not load the SDK.

Selected user/project installation previews and applies Jev-owned entries only. Restoration keeps unrelated settings and reports modified conflicts. Absolute launchers and runtime-home bindings keep processes on one credential/ledger. Configuration, connection, authentication, actual invocation and workload qualification are separate states. No tool executes assessed commands or changes permission policy.

`jev hook run HARNESS` adapts one pre-tool hook payload (Claude Code, Command Code, Codex, Cursor, Gemini CLI) to an escalate-only decision: `ask` where the hook supports it, otherwise `deny`, and by default only in sessions without approval prompts. It never emits `allow`. It skips plain read-only commands and always exits 0 with no decision on any local failure.

The separate local `jev capture` CLI command explicitly runs the producer argv chosen by its caller, without shell expansion or Jev inference. It preserves both byte streams, their hashes and producer exit status, returns a manifest reference, and requires a new output directory. It runs without loading runtime configuration. Capture is not exposed over MCP and cannot be triggered by an advisory result.
