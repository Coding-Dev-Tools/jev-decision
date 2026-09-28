# Jev Decision 0.3.0

Selective, budgeted TypeSafe Jev assistance alongside your usual model. Jev can assess evidence relevance, classify bounded inputs, apply descriptive rubrics, and identify verification gaps. The normal model remains responsible for reasoning; native harness permissions and executable checks remain authoritative.

Missing credentials, outages, invalid responses and exhausted budgets return explicit unavailable results. Offline mode produces no substitute predictions. Routine commands and straightforward tasks should make no Jev request.

## Managed Windows installation

From a reviewed source checkout with Python 3.11 or newer, run PowerShell:

```powershell
./scripts/install-runtime.ps1
```

The script builds a wheel, installs it in a versioned environment under LocalAppData/JevDecision/runtimes, and writes a non-secret installation manifest. It does not replace your normal model or configure harnesses automatically. Use the absolute Python path printed by the script for the commands below:

```powershell
& $jevPython -m jev_decision.cli auth set --gui
& $jevPython -m jev_decision.cli doctor --json
& $jevPython -m jev_decision.cli harness install --dry-run
& $jevPython -m jev_decision.cli harness install --apply
& $jevPython -m jev_decision.cli doctor --live --json
```

Enter the key only into the masked local window. Windows CurrentUser DPAPI encrypts the credential, with access restricted to the current user and SYSTEM. Harness entries contain only absolute launcher references. Restart existing Jev MCP processes after credential or runtime configuration changes. A protected file does not prove provider authentication; only a successful live response does.

Preview restoration with `harness restore`, then apply with `harness restore --apply`. Restoration affects Jev-owned entries that still match the recorded installation; user-modified entries are reported rather than overwritten. Existing models, hooks, Engraphis servers and unrelated settings are preserved.

## Shared runtime policy

- Pinned model: `jev-1.13.0`; endpoint: `https://api.typesafe.ai/v1/systemone`.
- Five-second total evaluation deadline, including at most one transient retry. Redirects are rejected. Requests and responses are bounded, and diagnostics omit payloads and credentials.
- A transactional SQLite ledger accounts for at most $1 per America/New_York calendar day across processes using the same managed runtime home.
- Before every attempt, reserve $0.002688: the documented 64,000-token maximum times $0.042 per million input tokens. Reconcile valid reported usage; retain the full reservation when usage is unknown or a process crashes. This is conservative local accounting, not a provider billing report.
- Cache identical successful evaluations within one client session. Failure or budget exhaustion returns control to the normal workflow.
- Automatic pruning is disabled. Advisory scores alone do not demonstrate improved accuracy, token savings or latency.

The shared non-secret `config.json` supports approved absolute workspace roots, disabling the runtime, lowering the daily cap and enabling pruning only after validation. Environment credentials remain a Python library compatibility path; the managed installation uses protected storage. Independent SDK calls that bypass this runtime do not share its budget.

## MCP and CLI interfaces

Run `python -m jev_decision.mcp` for bounded JSON-RPC stdio. Tools:

| Tool | Purpose |
| --- | --- |
| `jev_decide` | Native typed questions against bounded, sanitized state |
| `jev_guard_command` | Advisory command-risk assessment; never authorization |
| `jev_verify_completion` | Assess supplied evidence and verification gaps; never certification |
| `jev_prune_output` | Batch complete evidence windows; retain content by default |
| `jev_read_evidence` | Read a saved UTF-8 artifact within approved roots before model ingestion |
| `jev_status` | Credential-presence, policy and local budget metadata; no network call |

`jev decide --file request.json` accepts a JSON object with `state` and `questions`. Native questions are keyed by stable, nonempty IDs, for example:

```json
{"state":{"log":"FAIL example; exit code 1"},"questions":{"failure":{"type":"noul","instructions":"Does the log report a failure?"}}}
```

Choice questions require 2–255 unique descriptive criteria. Score questions require 2–10 descriptive rubric levels. Responses must contain every requested answer with the correct type, resolved model and a valid probability distribution where applicable. Scores preserve fractional values and legends. Noul confidence and absent usage are unknown, not fabricated zeroes.

File-backed evidence selection rejects traversal outside configured roots, sensitive filenames, binary files and oversized inputs. It sanitizes excerpts, records original hashes and line spans, preserves the original artifact, and retains failure details, test summaries, exit codes and uncertain content. It cannot transparently shorten output a primary model has already read. Do not pass arbitrary private files for external evaluation.

## Harnesses and validation

The installer supports native MCP configuration for Codex, Command Code, Antigravity, Claude Code, Cursor, OpenCode and Crush. Pi, Hermes, OMP and OpenClaude use a client-supported skill invoking the same CLI. Existing profiles without a verified runnable client remain inactive. Desktop and CLI editions must be verified separately after reload; configuration alone is not operational proof.

Hosted ChatGPT requires a private Secure MCP Tunnel, developer-mode access, workspace association, tunnel permissions and a separate OpenAI tunnel credential. Local stdio installation does not connect hosted ChatGPT. A tunnel must forward to this managed runtime and its ledger, and depends on this computer remaining online. See [OpenAI's tunnel requirements](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

Run `python -m pytest -q` and, under `ts`, `npm ci` followed by `npm test`. The TypeScript package implements contract parity for library users; installed harnesses use Python so their accounting stays shared. Use independently labeled development and held-out examples before enabling automatic omission. Record correctness, retained evidence, total latency, primary-model tokens, Jev usage and fallback rate; unmeasured metrics remain unknown.

See [migration notes](docs/MIGRATION_0_3.md), [protocol specification](docs/SPECIFICATION.md), and the [TypeSafe API contract](https://docs.typesafe.ai/api).
