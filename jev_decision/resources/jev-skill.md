---
name: jev-advice
description: Use Jev for a small, useful semantic classification, comparison, or evidence-gap assessment when ordinary reasoning or deterministic checks leave material uncertainty. Skip routine work and questions already settled by tests.
---

# Selective Jev advice

{{ACTIVATION}}
Keep the normal LLM in charge. Use one small batch of atomic Choice, Score, or Noul questions when the answer can improve the current task. Send only the minimum sanitized excerpts needed; omit credentials, private identifiers, whole repositories, raw conversations and unrelated logs. The shared runtime enforces the same protected credential, pinned model, request limits and $1/day ceiling across clients.

Prefer the available Jev MCP tools. `jev_decide` handles typed questions; `jev_guard_command` describes ambiguous effects without authorizing execution; `jev_verify_completion` identifies evidence gaps without certifying completion. Use `jev_status` to diagnose availability, not routinely on every turn.

For a client without Jev MCP tools, save a minimal JSON object with `state` and `questions`, then use its existing shell tool:

```{{SHELL}}
{{CLI_COMMAND}} decide --file 'sanitized-jev-input.json'
```

Example input:

```json
{"state":{"excerpt":"Test parser_handles_empty failed; 12 other tests passed."},"questions":{"has_failure":{"type":"noul","instructions":"Does this excerpt report a failed test?"}}}
```

For a saved log that has not entered model context, `jev_read_evidence` or the CLI `evidence --file <approved-log> --goal <goal> --json` can assess bounded evidence windows under configured workspace roots. Content is retained by default. Never enable pruning automatically or discard failures, caveats, file/line references or verification evidence. Scoring text already ingested cannot reclaim its context tokens, and no savings or accuracy improvement is presumed.

If Jev is unavailable, the budget is exhausted, or an answer is uncertain, continue normal reasoning and deterministic checks. Do not loop retries, bypass the shared runtime, increase the budget, switch providers, or treat a score as permission or proof. Retain contradictory evidence and validate consequential conclusions with the original source or executable tests.
