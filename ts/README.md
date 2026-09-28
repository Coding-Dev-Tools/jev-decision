# Jev advisory client for TypeScript

This is an **unmanaged, explicit API client**. Pass an API key directly from your
application's secret store. It does not read environment variables, store secrets,
implement Windows DPAPI, or enforce the managed harness's shared daily budget.
For Codex/ChatGPT, Command Code, Antigravity, and other installed harnesses, use
the repository's **Python MCP/CLI runtime** instead. Do not install this client as
a second harness runtime or describe its calls as covered by that runtime's cap.

From a reviewed checkout, prepare the local package with Node 20+:

```sh
cd ts
npm ci
npm run build
npm pack
```

In your consuming project, run `npm install /absolute/path/to/coding-dev-tools-jev-decision-0.3.0.tgz`.
This installs the prepared archive without depending on a registry release. Packing does not publish it.

```typescript
import { JevClient } from "@coding-dev-tools/jev-decision";

const client = new JevClient({ apiKey: keyFromYourSecretStore });
const result = await client.evaluate(sanitizedExcerpt, [{
  id: "relevance",
  type: "score",
  prompt: "How directly does this excerpt support the stated task?",
  criteria: [
    "Unrelated to the task or contradicted by available evidence",
    "Useful background but insufficient to answer the task",
    "Direct evidence needed to answer the task",
  ],
}]);
if (result.status === "ok") {
  // Use typed evidence as an advisory input to your normal model or application.
  // Existing authorization, executable checks, and task acceptance remain authoritative.
}
```

`evaluate(state, questions)` accepts typed questions or the provider's native
question map. Choice criteria allow descriptive values or native `null`. Score criteria are ordered
descriptions indexed from zero; the returned score can be fractional and includes
its legend. Noul returns a probability with `confidence: null`.
Reported two-decimal probabilities are accepted only when their rounding intervals
permit total probability one and a compatible score. Returned scores and
probabilities retain the provider's values; they are never renormalized.

Question IDs are local correlation labels. Recognizable secrets and the configured
API key are redacted from IDs before transmission; ambiguous redacted IDs reject
the request. Successful results restore your original IDs, including cache hits
and concurrent calls. Use non-sensitive IDs because results intentionally retain
them. This safeguard does not sanitize TypeScript state, prompts or criteria;
your application remains responsible for preparing those fields for disclosure.

Results use the same snake_case status contract as Python: `status`, `source`,
`decisions`, `requested_model`, `resolved_model`, `usage`, `latency_ms`, `attempts`,
`request_id`, `error_code`, and `is_fallback`. Missing credentials, failed requests,
and malformed responses produce `unavailable` with empty decisions. Explicit
offline execution produces `offline` with empty decisions. No regex-based model
decisions are fabricated. Unknown token usage stays `null`.
Usage across a retry remains unknown when an earlier attempt's usage is unknown.

Calls pin `jev-1.13.0` and the exact official HTTPS endpoint, reject redirects,
bound requests to 24,576 bytes and responses to 262,144 bytes, and have a total
five-second deadline including a maximum of one transient retry. A retry may be
billable; this standalone client does not enforce a spend ceiling. Only send
minimal, sanitized content you are authorized to disclose to TypeSafe.

Successful results are cached only in the client instance (up to 128 entries);
identical concurrent requests share an invocation. Cache hits report zero new
usage and attempts. `clearCache()` discards completed cached results. No provider
body, source state, or credential is included in returned error data.

`await guardBashCommand(command, cwd, client)` returns an **advisory risk
assessment**, with `execution_authority: "none"`; it never returns an execution
permission. `await verifyTurnCompletion(evidence, client)` returns advisory
evidence assessment with `task_success_authority: "none"`. Neither helper bypasses
the supplied client. These are intentional breaking corrections from v0.2.0.

Run `npm ci` and `npm test` for deterministic, mocked-transport Node tests. These
tests do not call TypeSafe and do not establish live model accuracy, latency,
account access, or savings.

`test/fixtures/contract.json` is the shared Python/TypeScript provider corpus.
`node test/contract-runner.cjs` emits the normalized results for cross-language
regression checks; it always uses a fake transport and never contacts TypeSafe.
