## Merge-readiness review: harness fixes, pre-execution guard, quickstart

This adds 14 focused commits on top of `f9ed66c`. Full record: [docs/validation/release-review-20260930.md](docs/validation/release-review-20260930.md).

### Fixes

- **High: generated entries were broken on macOS/Linux.** They used `Path(sys.executable).resolve()`, which turns a venv, pipx or uv interpreter (`bin/python` is a symlink) into the base Python, and that interpreter cannot import `jev_decision`. Every generated MCP entry and skill command failed with `No module named jev_decision`. POSIX now keeps the unresolved path; Windows keeps its resolved path for MSIX. `check_packages.py` now launches the generated interpreter.
- **Library opt-in.** `JevClient(api_key=...)`, or `JevClient()` with `TYPESAFE_API_KEY` set, returned `runtime_disabled` before `jev setup`. A key passed to the library now opts in with the default daily cap. The CLI and MCP still stay offline until setup.
- **Claude Code `${VAR}`.** An unset variable was passed through as literal text, accepted as a key, and left a budget hold on every call. Claude Code entries now use `${VAR:-}`, and any unexpanded placeholder counts as an absent key in Python and TypeScript.
- **Evidence name screen.** The screen ran over the whole absolute path, so projects under directories like `auth-service/` refused every read. It now applies only below the approved root.
- **Guard prompt.** The guard's Choice and Noul questions now give each option explicit criteria, following provider guidance and matching the TypeScript guard. The result adds `category_probabilities`.
- **Smaller fixes.** CLI hints for a fresh install or a zero budget, a single version source, and Python accepts plain `{id, type, instructions, criteria}` objects.

### Harness integration

- **New `jev hook run|config`.** An escalate-only pre-tool shell guard for Claude Code, Command Code, Codex, Cursor and Gemini CLI. It returns `ask` where the hook supports it; elsewhere it returns `deny`, by default only in no-prompt sessions. It never returns `allow`, skips simple in-project read-only commands, and always exits 0 with no decision on any local failure. Command Code behavior was checked against the `command-code` 1.72.4 hook runner. See [docs/HOOKS.md](docs/HOOKS.md).
- **Compact MCP schema.** `jev_decide` advertises a 2.7 KB input schema instead of 6.5 KB, saving about 960 model tokens per request in clients that send every schema. The server still validates the full schema.
- **Docs.** The README is now a four-step quickstart with a harness table. The Command Code guide has a new guard section.

### Validation

- Python 3.10, 3.11, 3.12, 3.13, 3.14.7: 645 passed, 3 skipped. Python 3.9: 627 passed, 21 skipped.
- TypeScript: 86 passed, plus a clean `check:package` install.
- Ruff and `git diff --check` pass.
- `scripts/check_packages.py` passes, including the generated-interpreter probe.
- End to end: `uv tool install` → `jev setup` → `jev harness install --target claude-code --scope project` → MCP handshake through the generated `.mcp.json` entry returns 6 tools. On the same setup, the old resolved interpreter fails with `ModuleNotFoundError`.
- CI now also runs Python 3.14 and Node 24.

Windows and macOS were not run locally, so please read this head's CI before merging. No provider calls were made.
