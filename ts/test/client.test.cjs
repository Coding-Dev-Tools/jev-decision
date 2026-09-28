const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  JevClient, DEFAULT_MODEL, DEFAULT_TYPESAFE_ENDPOINT, MAX_REQUEST_BYTES,
  MAX_RESPONSE_BYTES, normalizeQuestions, evaluateHeuristics,
  guardBashCommand, verifyTurnCompletion,
} = require("../dist/index.js");

const questions = () => [
  { id: "relevant", type: "noul", prompt: "Does this evidence address the stated task?" },
  { id: "route", type: "choice", prompt: "Select the appropriate evidence handling route.", criteria: { inspect: "Inspect directly relevant evidence", ignore: "Ignore unrelated background evidence" } },
  { id: "quality", type: "score", prompt: "Rate the evidence quality.", criteria: ["Unsupported assertion", "Partial evidence", "Direct verified evidence"] },
];
const answer = () => ({
  model: DEFAULT_MODEL,
  answers: {
    relevant: { type: "noul", noul: 0.85 },
    route: { type: "choice", choice: "inspect", confidence: 0.9, probabilities: { inspect: 0.8, ignore: 0.2 } },
    quality: { type: "score", score: 1.7, confidence: 0.75, legend: { 0: "Unsupported assertion", 1: "Partial evidence", 2: "Direct verified evidence" }, probabilities: { 0: 0.1, 1: 0.1, 2: 0.8 } },
  },
  usage: { input_tokens: 123, output_tokens: 15 },
});
const jsonResponse = data => new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json; charset=utf-8" } });
const clientFor = data => new JevClient({ apiKey: "test-credential", fetchImpl: async () => jsonResponse(data) });
const assertUnavailable = (result, code) => {
  assert.equal(result.status, "unavailable");
  assert.equal(result.error_code, code);
  assert.equal(result.source, "none");
  assert.deepEqual(result.decisions, {});
  assert.equal(result.is_fallback, false);
  assert.equal(result.resolved_model, null);
};
const batch = (decisions, status = "ok") => ({
  status, source: status === "ok" ? "provider" : "none", decisions,
  requested_model: DEFAULT_MODEL, resolved_model: status === "ok" ? DEFAULT_MODEL : null,
  usage: { input_tokens: 2, output_tokens: 1 }, latency_ms: 1, attempts: 1,
  request_id: "fixture", error_code: status === "ok" ? null : "missing_key", is_fallback: false,
});

test("canonical payload, pinned model, fractional score and Noul confidence parity", async () => {
  let seen;
  const client = new JevClient({ apiKey: "test-credential", fetchImpl: async (url, init) => {
    seen = { url, init, payload: JSON.parse(init.body) };
    return jsonResponse(answer());
  } });
  const result = await client.evaluate({ task: "Review evidence", excerpt: "Minimal sanitized excerpt" }, questions());
  assert.equal(result.status, "ok");
  assert.equal(result.source, "provider");
  assert.equal(result.requested_model, "jev-1.13.0");
  assert.equal(result.resolved_model, "jev-1.13.0");
  assert.equal(result.attempts, 1);
  assert.equal(result.decisions.relevant.confidence, null);
  assert.equal(result.decisions.quality.score, 1.7);
  assert.deepEqual(result.decisions.quality.legend, answer().answers.quality.legend);
  assert.deepEqual(result.usage, { input_tokens: 123, output_tokens: 15 });
  assert.equal(result.rawResponse, undefined);
  assert.equal(result.state, undefined);
  assert.equal(seen.url, DEFAULT_TYPESAFE_ENDPOINT);
  assert.equal(seen.init.redirect, "manual");
  assert.equal(seen.init.credentials, "omit");
  assert.equal(seen.payload.model, DEFAULT_MODEL);
  assert.deepEqual(seen.payload.questions.quality.criteria, questions()[2].criteria);
  assert.equal(seen.payload.questions.relevant.instructions, questions()[0].prompt);
  assert.equal(seen.init.headers.Authorization, "Bearer test-credential");
  assert.equal(client.mode, "unmanaged_explicit_api");
  assert.equal(client.timeoutMs, 5000);
  assert(!JSON.stringify(client).includes("test-credential"));
});

test("native mappings and structured descriptive criteria preserve their meaning", () => {
  const input = {
    binary: { type: "noul", instructions: "Assess the evidence", criteria: { true: "Evidence directly supports the task", false: "Evidence does not support the task" } },
    pick: { type: "choice", instructions: { task: "Choose the matching category" }, criteria: { relevant: { description: "Direct supporting evidence", weight: 2 }, background: ["Background context only"] } },
    score: { type: "score", instructions: "Score the evidence", criteria: [{ description: "Unsupported claim" }, { description: "Verified supporting evidence" }] },
  };
  assert.deepEqual(normalizeQuestions(input), input);
  assert.deepEqual(normalizeQuestions([{ id: "q", type: "score", prompt: "Rate the excerpt", scale: ["Unrelated evidence", "Direct evidence"] }]).q.criteria, ["Unrelated evidence", "Direct evidence"]);
});

test("official Choice null descriptions are accepted without changing the payload", async () => {
  const input = { q: { type: "choice", instructions: "Choose a category", criteria: { yes: null, no: null } } };
  assert.deepEqual(normalizeQuestions(input), input);
  const client = new JevClient({ apiKey: "test-credential", fetchImpl: async (_url, init) => {
    assert.deepEqual(JSON.parse(init.body).questions, input);
    return jsonResponse({ model: DEFAULT_MODEL, answers: { q: { type: "choice", choice: "yes", confidence: 0.8, probabilities: { yes: 0.9, no: 0.1 } } } });
  } });
  assert.equal((await client.evaluate("An excerpt", input)).status, "ok");
});

for (const hint of ["60", "Mon, 28 Sep 2099 12:00:00 GMT"]) {
  test(`Retry-After beyond the remaining deadline prevents retry (${hint})`, async () => {
    let calls = 0;
    const client = new JevClient({ apiKey: "test-credential", timeoutMs: 200, fetchImpl: async () => {
      calls++;
      return new Response("", { status: 429, headers: { "retry-after": hint } });
    } });
    const result = await client.evaluate("An excerpt", questions());
    assertUnavailable(result, "rate_limited");
    assert.equal(calls, 1);
    assert.equal(result.attempts, 1);
  });
}

test("Retry-After within the deadline is a minimum delay and 529 retries at most once", async () => {
  let calls = 0;
  const client = new JevClient({ apiKey: "test-credential", timeoutMs: 1000, fetchImpl: async () => {
    calls++;
    return calls === 1 ? new Response("", { status: 529, headers: { "retry-after": "0.12" } }) : jsonResponse(answer());
  } });
  const started = performance.now();
  const result = await client.evaluate("An excerpt", questions());
  assert.equal(result.status, "ok");
  assert.equal(calls, 2);
  assert(performance.now() - started >= 110);
  assert.equal(result.usage.input_tokens, null);
});

test("no implicit environment credential, silent fallback, or favorable offline decisions", async () => {
  const previous = process.env.TYPESAFE_API_KEY;
  process.env.TYPESAFE_API_KEY = "environment-credential-must-not-be-used";
  let calls = 0;
  try {
    const client = new JevClient({ fetchImpl: async () => { calls++; throw new Error("must not send"); } });
    assert.equal(client.isConfigured, false);
    assertUnavailable(await client.evaluate("git status; rm -rf /", questions()), "missing_key");
    const offline = await new JevClient({ apiKey: "test-credential", offlineMode: true }).evaluate("all tests passed", questions());
    assert.equal(offline.status, "offline");
    assert.equal(offline.source, "none");
    assert.deepEqual(offline.decisions, {});
    assert.equal(evaluateHeuristics("git status", questions()).status, "offline");
    assert.deepEqual(evaluateHeuristics("all tests passed", questions()).decisions, {});
    assert.equal(calls, 0);
  } finally {
    if (previous === undefined) delete process.env.TYPESAFE_API_KEY;
    else process.env.TYPESAFE_API_KEY = previous;
  }
});

test("only the exact official origin and path are accepted", () => {
  for (const endpoint of [
    "http://api.typesafe.ai/v1/systemone", "https://api.typesafe.ai.evil.test/v1/systemone",
    "https://api.typesafe.ai/v1/systemone?key=secret", "https://api.typesafe.ai/v1/systemone#fragment",
    "https://user:secret@api.typesafe.ai/v1/systemone", "https://api.typesafe.ai/v1/systemone/",
    "https://api.typesafe.ai/v1/models", "https://api.typesafe.ai:443/v1/systemone",
  ]) assert.throws(() => new JevClient({ baseUrl: endpoint }), /Only the official/);
  assert.throws(() => new JevClient({ timeoutMs: 5001 }), /between 1 and 5000/);
  assert.throws(() => new JevClient({ apiKey: "secret\nHeader: injection" }), error => !error.message.includes("secret"));
});

const badRequests = [
  ["empty question set", []],
  ["duplicate question IDs", [questions()[0], questions()[0]]],
  ["unknown type", [{ id: "q", type: "text", prompt: "Produce free text" }]],
  ["numeric score scale", [{ id: "q", type: "score", prompt: "Rate the evidence", scale: [0, 1, 2] }]],
  ["numeric string rubric", [{ id: "q", type: "score", prompt: "Rate the evidence", criteria: ["0", "1"] }]],
  ["one score level", [{ id: "q", type: "score", prompt: "Rate the evidence", criteria: ["Direct evidence"] }]],
  ["eleven score levels", [{ id: "q", type: "score", prompt: "Rate the evidence", criteria: Array(11).fill("Direct evidence") }]],
  ["empty criterion", [{ id: "q", type: "choice", prompt: "Pick the evidence", criteria: { good: "Direct evidence", bad: "" } }]],
  ["numeric structured criteria", [{ id: "q", type: "choice", prompt: "Pick the evidence", criteria: { good: { value: 1 }, bad: { value: 2 } } }]],
  ["single choice", [{ id: "q", type: "choice", prompt: "Pick the evidence", options: ["Direct evidence"] }]],
  ["duplicate choices", [{ id: "q", type: "choice", prompt: "Pick the evidence", options: ["Direct evidence", "Direct evidence"] }]],
  ["conflicting options and criteria", [{ id: "q", type: "choice", prompt: "Pick the evidence", options: ["other", "ignored"], criteria: { relevant: "Direct evidence", background: "Background only" } }]],
  ["question without meaningful instruction", [{ id: "q", type: "noul", prompt: "" }]],
  ["too many questions", Array.from({ length: 129 }, (_, n) => ({ ...questions()[0], id: `q${n}` }))],
  ["overlong ID", [{ ...questions()[0], id: "q".repeat(201) }]],
  ["duplicate score levels", [{ id: "q", type: "score", prompt: "Rate evidence", criteria: ["Direct evidence", "Direct evidence"] }]],
  ["missing native instructions", { q: { type: "noul" } }],
  ["unexpected native field", { q: { type: "noul", instructions: "Assess the evidence", hidden: "ignored" } }],
];
for (const [name, input] of badRequests) test(`invalid request: ${name}`, async () => {
  let calls = 0;
  const result = await new JevClient({ apiKey: "test-credential", fetchImpl: async () => { calls++; return jsonResponse(answer()); } }).evaluate("state", input);
  assertUnavailable(result, "invalid_request");
  assert.equal(calls, 0);
});

test("state must be bounded JSON and aliases cannot override the model pin", async () => {
  const client = clientFor(answer());
  const cycle = {}; cycle.self = cycle;
  assertUnavailable(await client.evaluate(cycle, questions()), "invalid_request");
  assertUnavailable(await client.evaluate({ value: NaN }, questions()), "invalid_request");
  assertUnavailable(await client.evaluate({ value: 2n }, questions()), "invalid_request");
  for (const empty of ["", "  ", {}, []]) assertUnavailable(await client.evaluate(empty, questions()), "invalid_request");
  assertUnavailable(await client.evaluate("state", questions(), "jev-latest"), "invalid_request");
  assertUnavailable(await client.evaluate("é".repeat(MAX_REQUEST_BYTES / 2), questions()), "request_too_large");
  let deep = "value"; for (let i = 0; i < 33; i++) deep = { nested: deep };
  assertUnavailable(await client.evaluate(deep, questions()), "invalid_request");
});

const malformed = [
  ["missing model", data => delete data.model, "model_mismatch"],
  ["missing answer", data => delete data.answers.relevant],
  ["extra answer", data => { data.answers.extra = { type: "noul", noul: 1 }; }],
  ["wrong answer type", data => { data.answers.relevant.type = "score"; }],
  ["missing noul", data => delete data.answers.relevant.noul],
  ["string noul", data => { data.answers.relevant.noul = "0.9"; }],
  ["boolean noul", data => { data.answers.relevant.noul = true; }],
  ["out of range noul", data => { data.answers.relevant.noul = 1.01; }],
  ["negative noul", data => { data.answers.relevant.noul = -0.01; }],
  ["nonfinite noul", data => { data.answers.relevant.noul = NaN; }],
  ["unsupported wire noul confidence", data => { data.answers.relevant.confidence = 1; }],
  ["unknown selected option", data => { data.answers.route.choice = "unknown"; }],
  ["selected option not maximal", data => { data.answers.route.choice = "ignore"; }],
  ["missing choice confidence", data => delete data.answers.route.confidence],
  ["out of range confidence", data => { data.answers.route.confidence = 2; }],
  ["missing distribution option", data => delete data.answers.route.probabilities.ignore],
  ["extra distribution option", data => { data.answers.route.probabilities.other = 0; }],
  ["invalid distribution total", data => { data.answers.route.probabilities.inspect = 0.5; }],
  ["negative probability", data => { data.answers.route.probabilities.inspect = -0.2; }],
  ["coerced probability", data => { data.answers.route.probabilities.inspect = "0.8"; }],
  ["missing score legend", data => delete data.answers.quality.legend],
  ["changed score legend", data => { data.answers.quality.legend[0] = "Different scale"; }],
  ["missing score level", data => delete data.answers.quality.legend[0]],
  ["score outside range", data => { data.answers.quality.score = 2.1; }],
  ["score disagrees with distribution", data => { data.answers.quality.score = 1.5; }],
  ["score string", data => { data.answers.quality.score = "1.7"; }],
  ["extra score field", data => { data.answers.quality.provider_note = "private"; }],
];
for (const [name, mutate, code = "invalid_response"] of malformed) test(`reject complete batch atomically: ${name}`, async () => {
  const data = answer(); mutate(data);
  const result = await clientFor(data).evaluate("state", questions());
  assertUnavailable(result, code);
  assert.equal(result.attempts, 1);
});

test("resolved model mismatch is explicit and cannot enter cache", async () => {
  let calls = 0;
  const client = new JevClient({ apiKey: "test-credential", fetchImpl: async () => { calls++; return jsonResponse({ ...answer(), model: "jev-latest" }); } });
  assertUnavailable(await client.evaluate("state", questions()), "model_mismatch");
  assertUnavailable(await client.evaluate("state", questions()), "model_mismatch");
  assert.equal(calls, 2);
});

test("missing or invalid usage stays unknown, and wire Noul confidence is never invented", async () => {
  const data = answer(); delete data.usage;
  const result = await clientFor(data).evaluate("state", questions());
  assert.equal(result.status, "ok");
  assert.deepEqual(result.usage, { input_tokens: null, output_tokens: null });
  assert.equal(result.decisions.relevant.confidence, null);
  data.usage = { input_tokens: "123", output_tokens: -1 };
  assert.deepEqual((await clientFor(data).evaluate("state", questions())).usage, { input_tokens: null, output_tokens: null });
  data.usage = { input_tokens: 0, output_tokens: 0 };
  assert.deepEqual((await clientFor(data).evaluate("state", questions())).usage, { input_tokens: 0, output_tokens: 0 });
});

test("session cache keys canonical content, isolates mutations, and records no new usage", async () => {
  let calls = 0;
  const client = new JevClient({ apiKey: "test-credential", fetchImpl: async () => { calls++; return jsonResponse(answer()); } });
  const first = await client.evaluate({ task: "Review", excerpt: "evidence" }, questions());
  first.decisions.relevant.probability = 0;
  const second = await client.evaluate({ excerpt: "evidence", task: "Review" }, questions());
  assert.equal(second.source, "cache");
  assert.equal(second.attempts, 0);
  assert.equal(second.decisions.relevant.probability, 0.85);
  assert.deepEqual(second.usage, { input_tokens: 0, output_tokens: 0 });
  assert.notEqual(first.request_id, second.request_id);
  assert.equal(calls, 1);
  await client.evaluate("different state", questions());
  assert.equal(calls, 2);
  client.clearCache();
  await client.evaluate("different state", questions());
  assert.equal(calls, 3);
});

test("concurrent identical requests share one invocation", async () => {
  let calls = 0;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const client = new JevClient({ apiKey: "test-credential", fetchImpl: async () => { calls++; await gate; return jsonResponse(answer()); } });
  const first = client.evaluate("state", questions());
  const second = client.evaluate("state", questions());
  release();
  const results = await Promise.all([first, second]);
  assert.equal(calls, 1);
  assert.deepEqual(results.map(r => r.source), ["provider", "cache"]);
  assert.deepEqual(results.map(r => r.attempts), [1, 0]);
});

test("cache is bounded and can be explicitly disabled", async () => {
  let calls = 0;
  const fetchImpl = async () => { calls++; return jsonResponse(answer()); };
  const client = new JevClient({ apiKey: "test-credential", fetchImpl });
  for (let i = 0; i < 129; i++) await client.evaluate(`state ${i}`, questions());
  await client.evaluate("state 0", questions());
  assert.equal(calls, 130);
  const uncached = new JevClient({ apiKey: "test-credential", fetchImpl, cacheEnabled: false });
  await uncached.evaluate("same state", questions());
  await uncached.evaluate("same state", questions());
  assert.equal(calls, 132);
});

test("one transient retry shares the deadline and returns the final valid response", async () => {
  let calls = 0;
  const client = new JevClient({ apiKey: "test-credential", fetchImpl: async () => ++calls === 1 ? new Response("private provider error", { status: 503 }) : jsonResponse(answer()) });
  const result = await client.evaluate("state", questions());
  assert.equal(result.status, "ok");
  assert.equal(result.attempts, 2);
  assert.equal(calls, 2);
  assert.deepEqual(result.usage, { input_tokens: null, output_tokens: null });
});

for (const [status, code, callsExpected] of [[401, "authentication_error", 1], [403, "authentication_error", 1], [400, "provider_error", 1], [429, "rate_limited", 2], [503, "provider_error", 2], [529, "provider_error", 2]]) {
  test(`HTTP ${status} produces sanitized ${code} with bounded retries`, async () => {
    let calls = 0;
    const client = new JevClient({ apiKey: "test-credential", fetchImpl: async () => { calls++; return new Response("private-provider-body-test-credential", { status }); } });
    const result = await client.evaluate("private-state", questions());
    assertUnavailable(result, code);
    assert.equal(calls, callsExpected);
    assert(!JSON.stringify(result).includes("private"));
    assert(!JSON.stringify(result).includes("test-credential"));
  });
}

test("transport exceptions never expose their message and retry at most once", async () => {
  let calls = 0;
  const client = new JevClient({ apiKey: "test-credential", fetchImpl: async () => { calls++; throw new Error("private credentials and provider body"); } });
  const result = await client.evaluate("state", questions());
  assertUnavailable(result, "transport_error");
  assert.equal(calls, 2);
  assert(!JSON.stringify(result).includes("private"));
});

test("redirect responses and an already-redirected transport are rejected without retry", async () => {
  for (const response of [new Response(null, { status: 302, headers: { location: "https://untrusted.example/collect" } }), Object.defineProperty(jsonResponse(answer()), "redirected", { value: true }), Object.defineProperty(jsonResponse(answer()), "url", { value: "https://untrusted.example/collect" })]) {
    let calls = 0;
    const result = await new JevClient({ apiKey: "test-credential", fetchImpl: async () => { calls++; return response; } }).evaluate("state", questions());
    assertUnavailable(result, "redirect_rejected");
    assert.equal(calls, 1);
  }
});

test("content type, malformed JSON, and invalid UTF-8 cannot be accepted", async () => {
  const responses = [
    new Response("<html>private response</html>", { headers: { "content-type": "text/html" } }),
    new Response("{private bad json", { headers: { "content-type": "application/json" } }),
    new Response(new Uint8Array([0xff, 0xfe]), { headers: { "content-type": "application/json" } }),
  ];
  for (const response of responses) {
    const result = await new JevClient({ apiKey: "test-credential", fetchImpl: async () => response }).evaluate("state", questions());
    assertUnavailable(result, "invalid_response");
    assert(!JSON.stringify(result).includes("private"));
  }
});

test("duplicate JSON keys, including escaped aliases and nested keys, are rejected", async () => {
  for (const text of [
    JSON.stringify(answer()).replace('"model":"jev-1.13.0"', '"model":"jev-1.13.0","model":"jev-1.13.0"'),
    JSON.stringify(answer()).replace('"noul":0.85', '"noul":0.85,"\\u006eoul":0.85'),
    JSON.stringify(answer()).replace('"inspect":0.8', '"inspect":0.8,"inspect":0.8'),
  ]) {
    const client = new JevClient({ apiKey: "test-credential", fetchImpl: async () => new Response(text, { headers: { "content-type": "application/json" } }) });
    assertUnavailable(await client.evaluate("state", questions()), "invalid_response");
  }
});

test("public property overrides cannot change the credential endpoint or deadline", async () => {
  let seenUrl;
  const client = new JevClient({ apiKey: "test-credential", timeoutMs: 40, fetchImpl: async url => { seenUrl = url; return new Promise(() => {}); } });
  Object.defineProperty(client, "baseUrl", { value: "https://untrusted.example/collect" });
  Object.defineProperty(client, "timeoutMs", { value: 50_000 });
  const result = await client.evaluate("state", questions());
  assertUnavailable(result, "timeout");
  assert.equal(seenUrl, DEFAULT_TYPESAFE_ENDPOINT);
  assert(result.latency_ms < 750);
});

test("validation compares against the payload snapshot, not later caller mutations", async () => {
  const input = questions();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const client = new JevClient({ apiKey: "test-credential", fetchImpl: async () => { await gate; return jsonResponse(answer()); } });
  const pending = client.evaluate("state", input);
  input[2].criteria[0] = "Changed after sending";
  release();
  const result = await pending;
  assert.equal(result.status, "ok");
  assert.equal(result.decisions.quality.legend[0], "Unsupported assertion");
});

test("response byte bounds cover declared length and streamed body", async () => {
  const responses = [
    new Response("{}", { headers: { "content-length": String(MAX_RESPONSE_BYTES + 1) } }),
    new Response("x".repeat(MAX_RESPONSE_BYTES + 1), { headers: { "content-type": "application/json" } }),
    new Response("x".repeat(MAX_RESPONSE_BYTES + 1), { headers: { "content-length": "2", "content-type": "application/json" } }),
  ];
  for (const response of responses) {
    assertUnavailable(await new JevClient({ apiKey: "test-credential", fetchImpl: async () => response }).evaluate("state", questions()), "response_too_large");
  }
});

test("deadline covers uncooperative fetch and response-body streams", async () => {
  for (const fetchImpl of [async () => new Promise(() => {}), async () => new Response(new ReadableStream({ start() {} }))]) {
    const started = Date.now();
    const result = await new JevClient({ apiKey: "test-credential", timeoutMs: 40, fetchImpl }).evaluate("state", questions());
    assertUnavailable(result, "timeout");
    assert.equal(result.attempts, 1);
    assert(Date.now() - started < 750, "deadline failed to bound a hanging operation");
  }
});

test("retry does not reset the end-to-end deadline", async () => {
  let calls = 0;
  const started = Date.now();
  const client = new JevClient({ apiKey: "test-credential", timeoutMs: 150, fetchImpl: async () => ++calls === 1 ? new Response("private", { status: 503 }) : new Promise(() => {}) });
  const result = await client.evaluate("state", questions());
  assertUnavailable(result, "timeout");
  assert.equal(result.attempts, 2);
  assert.equal(calls, 2);
  assert(Date.now() - started < 750);
});

test("guardBashCommand awaits and uses the supplied client; it never grants permission", async () => {
  let calls = 0;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const client = { async evaluate(state, input) {
    calls++;
    assert.equal(state.command, "git status; rm -rf /");
    assert.equal(state.cwd, "/workspace");
    assert.equal(input.length, 2);
    assert(input[1].criteria.destructive_or_leak.includes("secrets"));
    await gate;
    return batch({ material_risk: { type: "noul", probability: 0.99, confidence: null }, category: { type: "choice", selected: "destructive_or_leak", confidence: 0.9, probabilities: {} } });
  } };
  let finished = false;
  const pending = guardBashCommand("git status; rm -rf /", "/workspace", client).then(result => { finished = true; return result; });
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(finished, false);
  release();
  const result = await pending;
  assert.equal(result.risk_probability, 0.99);
  assert.equal(result.category, "destructive_or_leak");
  assert.equal(result.advisory, true);
  assert.equal(result.execution_authority, "none");
  assert.equal(Object.hasOwn(result, "allowAuto"), false);
  assert.equal(Object.hasOwn(result, "escalateToUser"), false);
  const unavailable = await guardBashCommand("git status", "/workspace", { evaluate: async () => batch({}, "unavailable") });
  assert.equal(unavailable.risk_probability, null);
  assert.equal(unavailable.category, null);
});

test("completion evidence assessment uses the supplied client without task-success authority", async () => {
  let calls = 0;
  const result = await verifyTurnCompletion("Reported tests passed; acceptance evidence attached", { async evaluate(state, input) {
    calls++;
    assert(state.includes("acceptance"));
    assert.equal(input[0].id, "completion_evidence");
    await Promise.resolve();
    return batch({ completion_evidence: { type: "noul", probability: 0.97, confidence: null } });
  } });
  assert.equal(calls, 1);
  assert.equal(result.evidence_probability, 0.97);
  assert.equal(result.task_success_authority, "none");
  assert.equal(Object.hasOwn(result, "success"), false);
  assert.equal(Object.hasOwn(result, "halt"), false);
});

test("shared Python/TypeScript provider corpus matches the canonical public contract", async () => {
  const { fixture, expected, runCorpus } = require("./contract-runner.cjs");
  const results = await runCorpus();
  for (const spec of fixture.cases) assert.deepEqual(results[spec.name], expected(spec), spec.name);
});

test("observed two-decimal provider scores preserve values within feasible rounding intervals", async () => {
  const criteria = ["No direct evidence", "Weak partial evidence", "Substantial evidence", "Complete verified evidence"];
  const input = { quality: { type: "score", instructions: "Rate this evidence", criteria } };
  for (const [score, values] of [[0.12, [0.91, 0.05, 0.03, 0.01]], [1.89, [0.18, 0.05, 0.46, 0.31]]]) {
    const probabilities = Object.fromEntries(values.map((p, index) => [String(index), p]));
    const response = { model: DEFAULT_MODEL, answers: { quality: { type: "score", score, confidence: 0.8, legend: Object.fromEntries(criteria.map((value, index) => [String(index), value])), probabilities } } };
    const result = await clientFor(response).evaluate("Sanitized evidence excerpt", input);
    assert.equal(result.status, "ok");
    assert.equal(result.decisions.quality.score, score);
    assert.deepEqual(result.decisions.quality.probabilities, probabilities);
    response.answers.quality.score = score === 0.12 ? 0.1 : 1.86;
    assertUnavailable(await clientFor(response).evaluate("Sanitized evidence excerpt", input), "invalid_response");
  }
});

test("rounding cannot admit impossible total mass, all-zero probabilities, or a lower reported choice", async () => {
  const data = answer();
  data.answers.route.probabilities = { inspect: 0.8, ignore: 0.19 };
  const valid = await clientFor(data).evaluate("state", questions());
  assert.equal(valid.status, "ok");
  assert.deepEqual(valid.decisions.route.probabilities, { inspect: 0.8, ignore: 0.19 });
  data.answers.route.choice = "ignore";
  assertUnavailable(await clientFor(data).evaluate("state", questions()), "invalid_response");
  data.answers.route.choice = "inspect";
  data.answers.route.probabilities = { inspect: 0.8, ignore: 0.18 };
  assertUnavailable(await clientFor(data).evaluate("state", questions()), "invalid_response");
  const criteria = Object.fromEntries(Array.from({ length: 201 }, (_, index) => [`option${index}`, `Criterion option ${index}`]));
  const response = { model: DEFAULT_MODEL, answers: { route: { type: "choice", choice: "option0", confidence: 0, probabilities: Object.fromEntries(Object.keys(criteria).map(key => [key, 0])) } } };
  assertUnavailable(await clientFor(response).evaluate("state", { route: { type: "choice", instructions: "Choose a criterion", criteria } }), "invalid_response");
});

test("fine-precision probabilities retain the existing strict weighted tolerance", async () => {
  const data = answer();
  data.answers.quality.probabilities = { 0: 0.1001, 1: 0.1001, 2: 0.7998 };
  data.answers.quality.score = 1.6997;
  assert.equal((await clientFor(data).evaluate("state", questions())).status, "ok");
  data.answers.quality.score = 1.69;
  assertUnavailable(await clientFor(data).evaluate("state", questions()), "invalid_response");
});
