# Jev v0.3 release review — 2026-09-28

This review covered the portable candidate from PR #1, starting at `27da47d`, with four independent Astra Max reviewers and one parent integrating changes. The lanes covered client/runtime contracts, onboarding and harness installation, evidence and qualification, and distribution/MCP compatibility. No worker delegated further or created a separate chat. This is engineering release evidence; no paid provider campaign or named-client invocation was performed.

## Corrected findings

| Area | Reproduced issue | Result after correction |
| --- | --- | --- |
| Score validation | Python accepted a displayed weighted score incompatible with the rounded probabilities' feasible unit-mass distribution | Python and TypeScript reject the same impossible four-level response; shared fixtures cover it |
| Choice identity | Redacted labels escaped into caller results and could break routing | Python restores the current caller's labels and probability keys after validation, including cache aliases; collisions fail before transmission |
| Score rubric identity | Redacted rubric values escaped into caller results | Python validates the wire legend, then restores the current caller's original nested rubric; cached wire values never substitute another caller's legend |
| Evidence mode default | Omitting MCP mode inherited the saved mode despite advertising `off` | Both CLI and MCP default omitted modes to `off`, without unlocking credentials or loading profiles; scoring requires an explicit per-call mode |
| Typed JSON | Python equated booleans with numeric rubric metadata and expected answers | Recursive comparison distinguishes booleans from numbers in legends and independent task grading |
| Timing qualification | Reported total task time could be shorter than the tool's own measured latency | Qualification requires valid `selection_latency <= preprocessing <= total` for every arm |
| Evidence retention | WARN, FATAL and CRITICAL records could receive low-relevance omission | Those severity records and neighboring context remain protected; the rubric hash changes |
| Deterministic control | An unrecognized page could become empty | The original text and complete source interval remain available |
| Scope selection | Explicit user-scope commands inherited the saved project root | Only project scope inherits that root; explicit incompatible arguments still fail |
| Target isolation | Unrelated client environment overrides blocked the selected installation | Only applicable target and scope locations are validated |
| Setup guidance | Useful configuration failures became an opaque error; keyring guidance named a nonexistent extra | Allowlisted errors provide safe corrective hints; optional storage points to `jev-decision[setup]` |
| Contention test | A standalone ledger call without an absolute deadline correctly rejected a reservation but exceeded an unsupported one-second test limit on macOS CI | The held-lock test verifies the actual SQLite busy timeout and zero reservations; explicit accounting and complete-client deadline tests remain unchanged |

## Less work for installed-package users

`jev capture --directory ABSOLUTE_NEW_DIRECTORY -- PROGRAM [ARGS...]` now ships in the core package. Users no longer need a checkout helper to keep producer output out of model context. Capture preserves argv, both binary streams, hashes and producer status; it prints only a manifest reference and does not load Jev configuration or credentials. The ordinary shell permission flow authorizes the producer. No MCP tool executes commands.

The Command Code guide and packaged skills use this entry point. Generic advice also reflects the provider's [documented Jev 1.13 limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13): keep exact arithmetic in code, minimize irrelevant state, ask direct questions, and calibrate thresholds for each question type. The [provider evidence-filtering example](https://docs.typesafe.ai/cookbooks/classifying_rag_passages) is workload guidance, not a universal threshold or savings guarantee.

## Validation and release gates

Local Windows verification used Python 3.12.10 and Node 24.15.0: **529 Python tests passed, one symlink-privilege test skipped; 86 TypeScript tests passed**. Shared native fixtures run against both implementations. Full configured Ruff rules and Git whitespace checks passed. Focused client and evidence fixes received independent re-review with no remaining findings.

The package checker builds wheel/source artifacts and installs each outside the checkout, then invokes packaged capture, Command Code skill installation/restoration, and legacy/current MCP subprocesses. The npm checker packs and installs outside the checkout. CI performs these checks on Windows, macOS and Linux and tests core Python 3.9–3.13; read the exact PR-head check results before release. Artifact creation does not publish a package or merge the PR.

The first CI attempt at `09cbde8` recorded a 1.17-second standalone ledger rejection against the former one-second assertion; the failed job passed on one diagnostic rerun. SQLite's [busy timeout](https://www.sqlite.org/c3ref/busy_timeout.html) controls accumulated lock-retry sleeping; it is not a wall-clock deadline for connection setup and filesystem work. The revised test checks the actual connection setting (positive and no more than 200 ms), a real held lock, and zero reservations. Production timeouts were not loosened. The precise cause of the extra elapsed time was not established.

The refreshed offline report still has four synthetic source tasks and 16 matched arm records. It remains ineligible with `live_evaluation_required`. Old profiles are invalidated by the changed protection policy's rubric hash.

| Gate | Release position |
| --- | --- |
| Portable advisory APIs, setup, capture, protected recovery and local tests | Implemented and validated offline |
| Cross-platform clean distributions | Required exact-head CI gate |
| Actual named-client/version invocation and OS vault usability | Deployment-specific, not established by these fixtures |
| Automatic omission, live task success and net token/cost/time improvement | Requires an independently labeled, budgeted campaign for the exact workload; disabled until qualified |
| Publication and merge | Separate release actions; not performed by this review |

Users can integrate and collect measurements now. This review does not establish a universally optimal configuration or savings percentage.
