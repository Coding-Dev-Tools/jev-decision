"use strict";
const { JevClient } = require("../dist/index.js");
const fixture = require("./fixtures/contract.json");

function materialize(spec) {
  const response = structuredClone(fixture.response);
  for (const patch of spec.patches) {
    let target = response;
    for (const key of patch.path.slice(0, -1)) target = target[key];
    const key = patch.path.at(-1);
    if (patch.op === "remove") delete target[key];
    else if (patch.op === "set") target[key] = structuredClone(patch.value);
    else throw new Error("Unknown shared fixture operation");
  }
  return spec.raw_response ?? JSON.stringify(response);
}
function expected(spec) {
  return { ...structuredClone(spec.expected === "ok" ? fixture.expected_ok : fixture.expected_unavailable), ...structuredClone(spec.expected_overrides ?? {}) };
}
async function runCorpus() {
  const results = {};
  for (const spec of fixture.cases) {
    const statuses = spec.http_statuses ?? [200];
    let calls = 0;
    const client = new JevClient({
      apiKey: "fixture-only-not-a-real-key",
      fetchImpl: async () => new Response(materialize(spec), { status: statuses[Math.min(calls++, statuses.length - 1)], headers: { "content-type": "application/json" } }),
    });
    const result = await client.evaluate(fixture.state, fixture.questions);
    // Only elapsed time and random correlation ID vary across implementations.
    const { latency_ms, request_id, ...normalized } = result;
    results[spec.name] = normalized;
  }
  return results;
}
module.exports = { fixture, materialize, expected, runCorpus };
if (require.main === module) {
  runCorpus().then(results => process.stdout.write(JSON.stringify(results))).catch(() => {
    process.stderr.write("Shared contract runner failed\n");
    process.exitCode = 1;
  });
}
