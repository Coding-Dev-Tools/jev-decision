# Jev Decision 0.3.0

Portable, selective [TypeSafe Jev](https://docs.typesafe.ai/api) advice for Python, TypeScript, JSON CLI, and MCP clients. Use small semantic classifications, relevance assessments, routing hints, and verification-gap checks when they can improve a task. Native permissions and executable verification remain authoritative.

**No workload ships qualified for automatic omission.** Savings depend on the source, primary model, harness, and task. This release supplies measurement and qualification tools, with an [offline integration report](docs/validation/portable-offline.json), rather than a universal savings percentage.

## Install and choose a setup

From a reviewed checkout, install a built package into your own virtual environment:

```sh
python -m venv .venv
# Activate .venv using your shell, then:
python -m pip install '.[mcp,setup]'
jev setup
```

Core Python and the JSON CLI support Python 3.9+. MCP uses the optional official Python SDK v2 and requires Python 3.10+. The `setup` extra adds OS credential storage and portable timezone data. Core-only installation is `python -m pip install .`. The TypeScript package requires Node 20+; [its API](ts/README.md) is explicit and does not share the Python budget ledger.

Fresh installations stay offline until setup. Setup asks for a credential source, approved evidence roots, daily budget, timezone, and selected harness. Zero budget keeps requests disabled; UTC is the portable default. For a headless environment, reference a variable name rather than putting a key in a command:

```sh
jev setup --non-interactive --credential-source env --key-env TYPESAFE_API_KEY --daily-budget 0.25 --timezone UTC --workspace /absolute/project --harness codex --scope user
jev harness install --target codex --dry-run
jev harness install --target codex --apply
jev doctor --json
```

Set the referenced variable privately in the harness launch environment. Windows supports CurrentUser DPAPI; macOS Keychain and Linux Secret Service are available through the optional `keyring` backend. `jev auth set` uses masked local input. Windows also offers `jev auth set --gui`. Environment references are the alternative for headless machines without an unlocked vault. Local status never unlocks a keychain or claims authentication.

The [Windows versioned installer](scripts/install-runtime.ps1) remains available with Python 3.11+. It prints an absolute installed Python path; use `& $jevPython -I -m jev_decision.cli setup` in PowerShell. Every generated integration binds to the selected runtime home. Keep that home and its ledger when upgrading. [Migration details](docs/MIGRATION_0_3.md).

## Select an integration

| Interface | Entry point | Budget/credentials |
| --- | --- | --- |
| Python | `from jev_decision import JevClient` | Shared managed runtime by default |
| JSON CLI | `jev decide --file request.json` | Same runtime |
| MCP stdio | `python -I -m jev_decision.mcp` or `jev-mcp` | Same runtime; optional SDK |
| TypeScript | `@coding-dev-tools/jev-decision` | Explicit key and application-owned budget |

The [support matrix and recipes](docs/INTEGRATIONS.md) cover Codex, Claude Code/Desktop, Cursor, Gemini CLI, Antigravity, OpenCode, existing skill clients, and generic MCP/CLI clients. Configuration tests and protocol tests are separate from live client verification. No new live client/version support claim is made by this release's offline suite.

Command Code users can follow the [dedicated guide](docs/COMMAND_CODE.md) for the optional `/jev-advice` skill, project configuration, and capture-before-reading workflow.

Install or restore only the selected target. Project scopes are supported where the client has a documented project configuration:

```sh
jev harness install --target cursor --scope project --project-root /absolute/project --dry-run
jev harness install --target cursor --scope project --project-root /absolute/project --apply
jev harness restore --target cursor --scope project --project-root /absolute/project
# Add --apply to execute the matching restoration.
```

Restoration preserves unrelated entries and reports user-modified conflicts. Existing clients need a reload. `doctor --live` sends one budgeted synthetic request and verifies provider authentication only; it does not prove the named harness invoked Jev.

## Small, typed decisions

```python
from jev_decision import JevClient

client = JevClient()  # Loads the explicitly configured managed runtime.
batch = client.evaluate(
    {"request": "Export needs a preview before downloading."},
    {"intent": {"type": "choice", "instructions": "Classify the requested change.",
                "criteria": {"feature": "New behavior", "bug": "Broken existing behavior", "unclear": None}}},
)
if batch.status == "ok":
    print(batch.get_choice("intent").selected)  # Advice, not permission.
else:
    print(batch.status, batch.error_code)        # Continue the normal workflow.
```

Runnable JSON examples: [classification](examples/classify.json), [evidence relevance](examples/relevance.json), [routing](examples/route.json), and [verification gaps](examples/verification-gap.json). Run `jev decide --file examples/route.json` after setup. Skip Jev when a deterministic rule or test already answers the question.

Choice supports native null descriptions. Score uses 2–10 ordered descriptive levels and preserves fractional values and legends. Noul returns a probability; separate confidence is unknown. Missing usage stays `null`. Python and TypeScript share contract fixtures, including valid provider probability rounding.

## Evidence before model ingestion

Capture output to original artifacts first, then return references to the agent. [Complete PowerShell/POSIX examples](docs/EVIDENCE.md) preserve stdout, stderr, and producer exit status. Sending a log to the primary model and then asking Jev to shorten it cannot reclaim tokens already consumed.

| Mode | Behavior |
| --- | --- |
| `off` | Redacted evidence and recovery references; zero Jev calls |
| `shadow` | Score eligible spans; retain all evidence and measure overhead |
| `select` | Omit only with a locally configured qualified profile and matching workload identity |

The saved runtime mode is a ceiling: CLI/MCP callers can request a less active mode, but cannot turn an `off` runtime into `shadow` or `select`. After initial setup, opt into measurement with `jev setup --non-interactive --selection-mode shadow` and restart existing Jev server processes. Setup itself makes no provider call. Use `--selection-mode off` to disable scoring again; a retained profile cannot override that choice. A mode-only update preserves the runtime's enabled state and other settings, even if its saved project or credential backend is unavailable.

`jev evidence --file /absolute/project/run/stdout.log --goal 'Find the failure cause' --mode shadow --json` then reads an approved source in measurement mode. Recover another page with `--start-line`, `--max-lines`, and `--expected-source-sha256`. Originals stay user-owned. Changed hashes reject recovery, and redaction preserves original line numbers.

Small inputs, fully protected output, unknown formats, and unavailable providers retain evidence. Supported records are grouped before scoring; tracebacks, test summaries, diff hunks, warnings, statuses, and adjacent context remain protected. Initial limits are 16 questions / about 16 KiB per batch, two concurrent requests, and five seconds for the selection operation. Unprocessed spans remain available. These are engineering bounds, not demonstrated optimal settings.

## Accounting and qualification

The managed runtime pins `jev-1.13.0`, sends only to the official HTTPS endpoint, reserves worst-case cost before each attempt, and permits at most one transient retry within the deadline. Budget reservation and settlement share that deadline. Crashes, unknown usage, and unfinished settlement retain conservative reservations. Timezone changes preserve the active accounting period. Separate runtime homes or standalone SDK calls have separate budgets.

[Evaluation instructions](docs/EVALUATION.md) compare unchanged output, deterministic-only, shadow, and selection arms. Qualification requires all labeled critical facts, no observed task-success loss, positive net tokens and modeled cost after Jev, and no p95 task-time increase. Reports bind source/label hashes, model/rubric/thresholds, harness versions, independent held-out source groups, cache conditions, and the explicit campaign budget. Inconclusive results stay in measurement mode. Provider usage and modeled cost are not invoices.

## Develop and prepare release artifacts

```sh
python -m pip install '.[test,mcp,setup]' build
python -m pytest -q
python scripts/evaluate_evidence.py --dataset examples/evaluation/dataset.json --offline --output /absolute/new-offline-report.json
python scripts/check_packages.py --output /absolute/new-release-candidate-directory
cd ts
npm ci
npm test
npm run check:package
```

CI tests Python 3.9–3.13 on Windows/macOS/Linux, enables optional MCP tests on supported Python versions, and installs wheel, source, and npm packages outside the checkout. Artifact preparation never publishes or merges. [MIT license](LICENSE) · [runtime contract](docs/SPECIFICATION.md).
