"use strict";
const assert = require("node:assert/strict");
const { JevClient, DEFAULT_MODEL } = require("../dist/index.js");
const fixture = require("./fixtures/question-ids.json");

async function runCorpus() {
  const results = {};
  for (const spec of fixture.cases) {
    for (const typed of [false, true]) {
      const ids = spec.ids.map(id => "x".repeat(spec.id_prefix_length ?? 0) + id);
      let wireIds = [];
      let calls = 0;
      const client = new JevClient({ apiKey: fixture.api_key, fetchImpl: async (_url, init) => {
        calls++;
        wireIds = Object.keys(JSON.parse(init.body).questions).sort();
        return new Response(JSON.stringify({ model: DEFAULT_MODEL, answers: Object.fromEntries(wireIds.map(id => [id, { type: "noul", noul: 0.75 }])) }), { headers: { "content-type": "application/json" } });
      } });
      const questions = typed ? ids.map(id => ({ id, type: "noul", prompt: "Does this evidence support the task?" }))
        : Object.fromEntries(ids.map(id => [id, { type: "noul", instructions: "Does this evidence support the task?" }]));
      const batch = await client.evaluate("Example evidence", questions);
      assert.equal(batch.error_code, spec.error ?? null, spec.name);
      assert.deepEqual(wireIds, [...(spec.wire_ids ?? [])].sort());
      assert.deepEqual(Object.keys(batch.decisions).sort(), spec.error ? [] : [...ids].sort());
      assert.equal(calls, spec.error ? 0 : 1);
      const { latency_ms, request_id, ...result } = batch;
      results[`${spec.name}:${typed ? "typed" : "native"}`] = { result, wire_ids: wireIds, calls };
    }
  }
  return results;
}
module.exports = { fixture, runCorpus };
if (require.main === module) {
  runCorpus().then(results => process.stdout.write(JSON.stringify(results))).catch(() => {
    process.stderr.write("Question ID corpus failed\n");
    process.exitCode = 1;
  });
}
