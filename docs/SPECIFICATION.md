# Jev advisory runtime contract

Version 0.3.0 pins jev-1.13.0 and the native TypeSafe API. See https://docs.typesafe.ai/api and https://docs.typesafe.ai/models for provider behavior and pricing. Local policy is stricter than the provider maximum to bound transmission, latency and accounting.

## Data flow

A harness invokes an absolute installed launcher. The runtime loads public policy and a Windows CurrentUser DPAPI credential, sanitizes bounded state and questions, and checks the session cache. For a provider attempt it reserves the maximum documented request cost in a shared SQLite transaction, sends only to the official HTTPS endpoint, and validates the entire typed response. Known input usage settles the reservation; absent or invalid usage retains it. A retry reserves again. Diagnostics contain no key, state or raw response.

The result records status, source, requested and resolved model, optional usage, attempts and a fixed error code. Missing or malformed answers reject the complete response. Question IDs are unique and complete. Noul has no provider confidence field. Choice and Score require finite valid probability distributions. Scores retain fractional values and descriptive legends.

## Authority

Risk assessments never grant execution permission. Evidence assessments never certify completion. Jev output cannot change canonical benchmark grades, evidence artifacts or memory by itself. Offline and unavailable states carry no synthetic decision. Consumers resume their ordinary model and executable verification workflow.

## Evidence selection

Saved UTF-8 evidence can be read through approved workspace roots. Resolve and check paths, reject sensitive names, enforce byte limits, sanitize excerpts, and retain original SHA-256 and source line locations. Large windows that exceed provider bounds remain available locally rather than being truncated into misleading evidence. All bounded windows are assessed together against complete shared state.

Pruning is off by default. Even when explicitly enabled after calibration, uncertain assessments retain content. Failure details, test summaries, exit codes, diff boundaries and original artifacts remain protected. Actual token savings require primary-model telemetry; byte counts are not tokens. A tool cannot remove text already consumed by a model.

## Accounting and transport

The shared allowance is $1 per America/New_York calendar day. A request reserves $0.002688, derived from 64,000 input tokens at $0.042 per million. SQLite immediate transactions serialize reservations across processes. Crashes and unknown usage retain the reservation. Day rollover uses New York calendar boundaries, including DST. Provider billing remains separately unknown.

The default five-second evaluation deadline includes no more than two attempts. Requests and responses are size bounded. Redirects and endpoint overrides are rejected. Identical successful requests can be reused only in the same client session. Existing MCP processes must restart after credential or policy changes. Separate JEV_HOME directories, unmanaged provider SDKs and standalone TypeScript clients are outside the managed shared ledger.

## MCP and installation

The server validates JSON-RPC 2.0 envelopes, tool arguments and bounded newline-delimited messages. Errors preserve valid request IDs and exclude payloads. Tools expose advisory decisions, risk assessment, evidence-gap assessment, evidence reading and local status. No tool runs the assessed command or approves another tool.

The installer records only Jev-owned changes, previews updates, keeps private backups and restores matching entries selectively. Native MCP clients share the installed Python runtime. CLI skills use that same absolute executable. Hosted ChatGPT needs a separately authorized private OpenAI Secure MCP Tunnel forwarding to the same runtime. Configuration success is distinct from credential authentication, actual client operation and demonstrated benefit.
