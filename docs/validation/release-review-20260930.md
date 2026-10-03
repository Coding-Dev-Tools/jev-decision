# Jev v0.3 merge-readiness review — 2026-09-30

This independent review covered PR #1 at `f9ed66c`, whose 18 CI jobs had passed. It had two goals: find defects the earlier review lanes missed, and make Jev easier to adopt in coding-agent harnesses, Command Code first. For harness behavior it relied on current vendor documentation and, for Command Code, the hook runner shipped in `command-code` 1.72.4. No paid provider request was made, and no user harness profile or credential was changed.

## Defects found and fixed

| Severity | Issue | Fix |
| --- | --- | --- |
| High | On macOS/Linux every generated MCP entry and skill command used `Path(sys.executable).resolve()`. In a POSIX venv, pipx or uv tool environment, `bin/python` is a symlink to the base interpreter, which cannot import `jev_decision`, so the MCP server failed to start with `No module named jev_decision`. Windows venv interpreters are real files, which hid the bug. The tests asserted the same resolved path. | Entries keep the environment's unresolved interpreter on POSIX; Windows keeps its resolved path for MSIX runtimes. A regression test uses a symlinked interpreter, and `check_packages.py` now launches the generated interpreter. An end-to-end run from a `uv tool install` (symlinked `bin/python`) completed an MCP handshake through the generated `.mcp.json` entry, where the resolved interpreter failed. |
| Medium | Explicit library use before setup returned `runtime_disabled`. | An explicit `JevClient(api_key=...)` argument can opt in with the default daily cap and shared ledger. The 2026-10-01 combined review tightened the initial implementation: ambient keys alone cannot authorize fresh clients or hooks. Saved configuration always wins. |
| Medium | Claude Code entries used `${NAME}` without a default. Claude Code passes an unset reference through as literal text, which then passed the printable-ASCII key check, failed authentication on every call, and left a worst-case budget hold. | Claude Code entries use `${NAME:-}`. The environment loader and presence status treat `${…}`, `{env:…}`, `$NAME` and `%NAME%` values as absent keys. Storage and the TypeScript constructor reject them. |
| Medium | The evidence reader screened credential names across the whole absolute path, so any workspace under a directory such as `auth-service/`, `oauth_app/` or `secrets-manager/` refused every read. | Names are screened below the most specific approved root. Denied names inside the root (`.env`, `.git`, `secrets/`, `*.pem`, `credentials.*`) are still refused, and exact-path recovery keeps the whole-path screen. |
| Low | `jev guard`, MCP `jev_guard_command` and the hook asked an unlabeled Choice (option equals label), against the provider's guidance to state exact conditions. The TypeScript guard already used descriptive criteria. | Every category and the risk Noul have explicit criteria, and the result adds `category_probabilities`. |
| Low | Zero-budget and fresh runtimes reported a bare `runtime_disabled`. | CLI results and `doctor --live` include a corrective hint. |
| Low | The version string was repeated in seven places. | `jev_decision/_version.py` is the single Python source (read dynamically by setuptools). A test keeps the npm package, lockfile and User-Agent in step. |

## Harness integration improvements

- **Pre-execution guard (`jev hook`).** MCP-only integration relied on the primary model choosing to call `jev_guard_command`, which spends its tokens and depends on its compliance. The adapter reads Claude Code, Command Code, Codex, Cursor and Gemini CLI pre-tool payloads. It returns `ask` where hooks support it, and otherwise `deny`, by default only in no-prompt sessions. It never returns `allow`, skips plain in-project read-only commands without a request, and exits 0 with no decision on every local failure (exit 2 means "block" to several harnesses). `jev hook config HARNESS` prints the fragment to merge. See [HOOKS.md](../HOOKS.md).
- **Compact MCP discovery.** Discovery advertises the preferred array form of the `jev_decide` input schema. The server validates against the complete schema, so native maps and legacy fields keep working. The 2026-10-01 review restored whitespace, Choice label and duplicate Score-level constraints to discovery. Schema size does not establish token or billing savings for a client.
- **Plain Python questions.** Python accepts the same plain `{id, type, instructions, criteria}` objects as MCP and the CLI. TypeScript uses typed questions or native ID-keyed maps; the shared array requires the conversion in [MEMORY_SYSTEMS.md](../MEMORY_SYSTEMS.md).
- **Docs.** The README now starts with a four-step quickstart and a harness table; qualification caveats have their own section. The Command Code guide gains a guard section and the 1.72.4 evidence.

## Validation

| Check | Result |
| --- | --- |
| Python 3.12.3 (Linux), full suite | 645 passed, 3 skipped |
| Python 3.10.20 / 3.11.15 / 3.13.13 / 3.14.7 (Linux) | 645 passed, 3 skipped each |
| Python 3.9 (Linux, core only; MCP SDK tests skip) | 627 passed, 21 skipped |
| TypeScript build and Node 22 suite | 86 passed; `check:package` clean install passed |
| `scripts/check_packages.py` (wheel and sdist outside the checkout) | Passed, including the generated-interpreter import probe and legacy/current MCP handshakes |
| End-to-end `uv tool install` → `jev setup` → `harness install --target claude-code --scope project` → MCP handshake via the generated entry | 6 tools; the unset key reports `missing_key` with 0 attempts |
| `jev hook run` through the generated Claude Code fragment on a fresh install | Exit 0, no output (fail-open) |
| Ruff (configured rules) | Passed |

Windows and macOS were not run locally for this review. CI covers them, so read the exact PR-head results before merging.

## Not changed, with recommendations

- **Connection reuse.** Each request opens a new TLS connection and two SQLite transactions (about 8 ms of local overhead on Linux, excluding the handshake). Long-lived MCP servers would benefit from a pooled HTTPS connection and a persistent ledger connection. These touch the heavily tested deadline code, so they are left for a focused follow-up.
- **Ledger retention.** Reservation rows are never pruned. A frequently firing hook could grow the ledger by tens of MB per year, so a bounded prune at period rollover is worth adding.
- **`jev_prune_output`.** The tool makes the agent resend text that is already in context, which costs primary-model output tokens. Its description says so. Consider hiding it from discovery unless shadow or select mode is configured.
- **Automatic hook installation.** `jev hook config` prints the fragment rather than editing settings, because hook entries are array items that the ownership-tracked installer does not manage yet.
- **Node 20** reached end of life in April 2026. `engines` still allows it, but CI now tests Node 22 and 24.
