# Command Code and memory integration review — 2026-10-01

Improvements incorporated into [PR #1](https://github.com/Coding-Dev-Tools/jev-decision/pull/1),
based on the reviewed v0.3 candidate `f9ed66c1983f6b4d63ac903901f1203c603de680`.
The main branch, other worktrees and existing user client configuration were
preserved. These changes have not been merged or published as a release.

## Resulting behavior

- Public Python `assess_memory_relation` and `assess_memory_relevance` return
  structured advisory results with nullable judgments, probabilities, descriptive
  fractional scores, model/request/source/usage metadata and host memory authority.
  Excerpts are bounded without truncation; relevance references stay local and
  candidates remain in caller order. Neither helper changes a memory record.
- The installed dependency-free `EngraphisDecisionClient` translates authorized
  host questions to Jev and retains the host's per-call consent/classification
  boundary. The legacy supersession label becomes potential contradiction on the
  wire. Unknown Noul confidence remains unknown, so Engraphis' experimental
  grounded-support path defers rather than receiving invented confidence.
- Failed, offline, fallback, heuristic, errored or mismatched batches cannot supply
  stale Python command/completion/memory judgments. Injected memory-client replies
  must have fixed diagnostic fields, valid numeric/usage metadata and canonical
  UUID-or-empty request IDs; malformed metadata becomes `invalid_response`.
- Command Code detection includes its documented executable names and excludes
  native Windows `cmd.exe`. Initial onboarding uses user scope, documents
  `/skill:jev-advice`, and explains shared project paths and Engraphis coexistence.
- Credential-variable names cannot clobber Jev runtime settings. Windows capture
  rejects explicit/PATH-resolved batch wrappers before launch. Non-UTF-8 evidence
  produces a recoverable content-free diagnostic while retaining original bytes.
- Source archives include the memory guide/native JSON recipe; wheels include
  helpers and the bridge. The npm README includes a standalone memory recipe.
  CI now retains package verification receipts alongside distribution files.
- Review fixes align user-scope installation and restoration, convert the shared
  MCP array into a TypeScript native question map, and reject stale evidence
  scores before marking spans assessed or permitting omission.
- Registry `_authToken`/`_auth` fields and recognizable `npm_`/`apikey_` tokens
  are redacted, including Python egress and both clients' wire identifiers.
  Sensitive package-manager, SSH, encrypted-store, Kubernetes and Docker files
  are denied before content reads. Windows name aliases and alternate streams
  cannot bypass the filter. POSIX colon filenames remain usable.
- Evidence reads preserve approved canonical roots; replacing a root with a
  junction/symlink cannot authorize its new target. Reload also rejects a saved
  root that resolves elsewhere. Existing descriptor-based race checks remain.

## Verification

On Windows with Python 3.12.10 and the optional official MCP SDK 2.2.0:

- Full Python suite: **682 passed, 1 skipped**. The sole skip requires Windows
  symlink privilege; directory-junction safeguards and other file checks ran.
- TypeScript build and client suite: **87 passed**, no skips.
- Full configured Ruff checks and `git diff --check`: passed.
- Clean wheel and source installs outside the checkout: passed core imports,
  offline memory helpers, bridge refusal, packaged Command Code skill/capture,
  and legacy/current (`2026-07-28`) official MCP subprocesses.
- Clean npm archive installation and offline execution: passed.
- Local guide links resolve. The native JSON example uses all three canonical
  decision types and has Python offline validation plus a TypeScript conversion
  regression. Shared wire-ID fixtures cover the additional redaction patterns.

Commands: `python -m pytest -q -ra`, `python -m ruff check .`, `git diff --check`,
`npm test`, `npm run check:package`, and
`python scripts/check_packages.py --output <new-directory>`.
The package check emits `verification.json` with smoke coverage, zero provider
calls and `published: false`. Validation used an isolated environment and synthetic
provider transports; it did not read user credentials or modify installed clients.

Four bounded internal workers reviewed Command Code, Engraphis, portable contracts
and delivery in each review pass. All four returned, no descendants or separate
worker chats were created, and the parent performed all integration. Independent
rechecks covered injected metadata, redaction, evidence scoring and root scope.

## Older local work reconciled

The existing older worktree at `2c321a9` was inspected without changing its files.
Its tracked patch and untracked MCP schema test were additionally preserved in a
local archive. The current root's updates are delivered together in PR #1.

| Older changes | Disposition in the current PR |
| --- | --- |
| Python/TypeScript client rounding, JSON equality, retry, Score rubric restoration and accounting comments/tests | Already incorporated or superseded by newer validated contracts and shared fixtures. |
| MCP schemas and schema tests; test dependency metadata | Incorporated by shared schemas, native-map compatibility checks and official SDK handling. |
| Evidence open-handle validation | Superseded by stronger pinned directory/descriptor validation. Missing sensitive-name exclusions and root authorization fixes were incorporated. |
| Registry and TypeSafe redaction, associated regressions | Incorporated in Python and TypeScript, with shared ID fixtures and original-line/source preservation checks. |
| Installer MSIX physical-interpreter discovery and manifest refresh | Already incorporated; current installation also verifies the optional SDK. |
| Marker-first runtime home and blanket secret-ID rejection | Superseded by explicit runtime-home selection and validated sanitized-ID restoration. Copying the older contracts would undo the reviewed behavior. |
| Documentation and examples | Reconciled against the current contracts; added owned-child restart/interpreter verification guidance. |

## Evidence limits

These are source, fixture, local protocol and installed-package results. They do
not establish a live Command Code/Engraphis invocation, provider authentication,
better recall, token/time/billing savings, cross-platform CI for this local patch,
or a release. Automatic omission remains off without workload qualification.
GitHub check/review status belongs to the exact PR head and is reported there;
earlier green checks do not qualify a later commit. No live campaign, merge,
package publication or protected runtime/client setting change was performed.
