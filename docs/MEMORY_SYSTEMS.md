# Jev advice for Engraphis and other memory systems

Use Jev when a small semantic assessment can help interpret already authorized
memory evidence. Your memory system owns access control, workspace/repo/session
routing, validity, retrieval, correction and retention. Jev never reads a memory
database or changes a record. Ordinary deterministic checks need no Jev call.

## Structured Python advice

The installed core package exports two helpers. They share the supplied
`JevClient`'s configured credentials, deadline, cache and managed budget:

```python
from jev_decision import JevClient, assess_memory_relation, assess_memory_relevance

client = JevClient()  # Fresh runtime loads remain disabled until setup.
relation = assess_memory_relation(
    "The staging timeout is 90 seconds.",
    "The staging timeout is 30 seconds.",
    client=client,
)
scores = assess_memory_relevance(
    "How do interrupted imports resume?",
    {"candidate_a": "Resume from the saved checkpoint.",
     "candidate_b": "The settings panel supports a dark theme."},
    client=client,
)
if relation["status"] == "ok":
    print(relation["relation"], relation["confidence"])
else:
    print(relation["status"], relation["error_code"])
```

Relations are `potential_contradiction`, `reinforces`, `orthogonal`, or `unclear`.
Unavailable/offline advice has `relation: null`, `confidence: null` and
`probabilities: null`; it does not become an orthogonal judgment. A potential
contradiction establishes neither which fact is correct nor which supersedes the
other. `classify_memory_relation()` remains a compatibility label helper; use the
structured result when uncertainty and provenance matter.

Relevance returns `candidates` under the original caller IDs and an unchanged
`candidate_order`. Each entry has a fractional `score`, `confidence`,
`probabilities` and a descriptive `legend`. The four-level rubric runs from
unrelated through uncertain/incomplete and useful background to direct evidence.
Missing scores stay null. Scores never reorder, omit or write memories. Keep the
host's normal recall available when scoring fails or remains uncertain.

Both results retain status, provider/cache source, requested/resolved model,
request ID, attempts, latency, usage, fallback state and a content-free error code.
They expose `advisory_only: true` and `memory_authority: "host_memory_system"`.
They return no source text or raw provider response. A cache result is advice from
an earlier successful call and does not establish fresh authentication.

Limits are 16 relevance candidates, 4,096 UTF-8 bytes per excerpt, a 2,048-byte
query and 16 KiB of total relevance text. Relation excerpts each have the same
4,096-byte limit. Oversized/invalid input fails before client construction or
provider calls; no evidence is silently truncated. An empty candidate map makes
zero calls. Non-empty calls remain subject to the runtime's serialized request
limit and shared budget. These are engineering bounds, not measured optimal values.

## Keep scope and privacy in the host

Before calling either helper, the host filters records by the caller's authorized
workspace, repository, session, validity and review eligibility. Supply only the
smallest excerpts approved for this provider; skip private, quarantined,
secret-bearing or unapproved material. Recognizable-secret redaction is a
best-effort safeguard, not permission to transmit a memory store.

Relevance candidate IDs remain local; the provider sees positional
`candidate_0`, `candidate_1` references. Keep the original record IDs, provenance,
timestamps and source hashes in the host. Excerpts can contain instructions;
they are untrusted evidence, and the fixed assessment prompt treats them as data.
Never turn a classification into an automatic correction, deletion, scope change,
verified claim, or completion decision.

## Optional Engraphis injected-client bridge

The wheel also includes `jev_decision.engraphis.EngraphisDecisionClient`. It imports
no Engraphis package and owns no memory store or credentials. It translates the
host's `DecisionQuestion(id, prompt, kind, options)` objects to native Jev
questions. Directly injecting `JevClient` does not translate those host dataclasses.

For an Engraphis installation exposing the experimental backend contract:

```python
from engraphis.backends.jev_decision import JevDecisionBackend
from jev_decision import DEFAULT_MODEL, JevClient
from jev_decision.engraphis import EngraphisDecisionClient

backend = JevDecisionBackend(
    client=EngraphisDecisionClient(JevClient()),
    model=DEFAULT_MODEL,
)
# The host supplies an authorized MemoryRecord and explicitly approves remote use:
# backend.classify_contradiction(candidate_text, existing_record,
#     allow_remote=True, data_classification="internal")
```

Every bridge call defaults to no remote authorization. It checks explicit
`allow_remote=True`, a `public`/`internal` classification, no heuristic fallback,
the configured model pin, input bounds and the full response contract. Failed,
offline, mismatched and malformed responses contain no decisions. Classification
is supplied by the host; labeling private content internal cannot authorize it.

Engraphis' legacy `contradicts_and_supersedes` label is translated to
`potential_contradiction` on the wire and mapped back only for its advisory
interface. It remains subject to the host's deterministic resolution rules.
The bridge never introduces a supersession operation.

**Grounded-support limitation:** portable Jev Noul answers have a probability and
unknown separate confidence (`None`). Engraphis' experimental support adapter
requires a numeric confidence above its threshold, so this path safely defers.
The bridge preserves `None`; it never manufactures confidence. Keep deterministic
grounded verification and the host's abstention rules authoritative.

Compatibility tests use Engraphis-shaped local fixtures and synthetic transport.
They establish question translation and refusal behavior, not live deployment,
provider authentication, improved recall, or measured savings. Recheck the host
contract when updating either package.

## CLI, MCP and TypeScript

[memory-advice.json](../examples/memory-advice.json) supplies one small synthetic
batch with relation, memory-type, relevance and verification-gap questions. Use
this canonical object/array format through CLI and MCP:

```sh
jev decide --file examples/memory-advice.json
```

For MCP, pass the file's `state` object and `questions` array to `jev_decide`.
For TypeScript, convert the canonical MCP array to its native ID-keyed question
map after the application has approved and sanitized the request:

```typescript
const questions = Object.fromEntries(
  request.questions.map(({ id, ...question }) => [id, question]),
);
const advice = await client.evaluate(request.state, questions);
```

TypeScript's convenience arrays use `prompt`; native maps use `instructions`.
Its client has an application-owned budget; it does not share the Python ledger.
The example is available from a reviewed checkout/source archive and the repository
documentation; installed Python helpers and the bridge need no checkout.

Treat unknown memory types as review hints. Memory type does not choose a
workspace. A verification-gap probability identifies missing evidence and cannot
certify support or a completed task. Keep usage unknown when absent and measure
benefit with independently labeled workloads before making savings claims.
