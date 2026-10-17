# Recoverable evidence before ingestion

A primary model saves context tokens only when the large output never enters its context. Capture first, return the small reference, then use the evidence tool to read the approved artifact. Raw artifacts remain on the user's machine until the user removes them.

## Capture a producer

The installed `jev capture` command writes stdout and stderr directly to separate binary files, saves a metadata manifest with original hashes and exit status, prints only its reference, and exits with the producer's status. It requires no runtime setup, credentials, or repository checkout. The producer is an explicit argv command executed under ordinary shell permissions; no shell expansion is performed. MCP never executes it. Separate streams preserve their bytes but do not claim a combined chronological ordering. Use a new directory each time; an existing directory is refused. Configure the producer for UTF-8 if it is to be read by the evidence interface. The [checkout helper](../examples/capture.py) and shell wrappers remain compatible.

Windows capture rejects `.cmd`/`.bat` producers and PATH-resolved batch wrappers before launch because those can introduce implicit shell parsing. Call the underlying native executable directly, such as `node.exe --test`. An explicitly supplied command interpreter follows the caller's ordinary authorization for that shell invocation. Evidence decoding stays strict: non-UTF-8 text returns `evidence_encoding_not_utf8`. Retain the original bytes/hash and create a separate UTF-8 derivative using the known producer encoding; never replace undecodable bytes or reuse the original hash for that derivative.

POSIX shell (works when the enclosing script uses `set -e`):

```sh
capture_status=0
jev capture --directory "$PWD/.evidence/run-001" -- python -X utf8 -m pytest || capture_status=$?
# The command above returned only a capture.json reference, not the test output.
# Preserve capture_status in your surrounding harness; do not replace failure with success.
```

PowerShell:

```powershell
$captureDirectory = Join-Path (Get-Location).Path '.evidence\run-001'
jev capture --directory $captureDirectory -- python -X utf8 -m pytest
$producerStatus = $LASTEXITCODE
# Preserve producerStatus in the surrounding harness.
```

Read `capture.json` locally to obtain each stream path/hash and the producer exit status. Give the agent that compact reference. Read both streams when relevant; an empty stdout does not imply success. Never infer producer success from the Jev tool's own status.

## Read, measure, recover

Approve only the desired workspace with `jev setup --workspace /absolute/project ...`. Then:

```sh
jev evidence --file /absolute/project/.evidence/run-001/stdout.log --goal 'Diagnose the failed test' --mode off --max-lines 1000 --max-bytes 65536 --json
```

`off` redacts recognized secrets, preserves source line positions, and makes no semantic call. `shadow` scores eligible records while returning the complete page. `select` requires a locally configured qualified profile plus an explicit matching workload JSON (`--workload /absolute/workload.json`). Raw `prune` supports off/shadow measurement; it cannot omit without a recoverable original reference.

CLI/MCP requests can only downgrade the saved runtime mode. Omitting the per-call mode always uses `off`; request `shadow` or `select` explicitly when wanted. After initial setup, `jev setup --non-interactive --selection-mode shadow` permits measurement; `--selection-mode off` disables it again. Restart existing Jev server processes after changing settings. `select` additionally requires the reviewed profile and saved `selection_mode: "select"`; leaving a profile path on disk cannot enable omission when the saved mode is off or shadow. Response statistics report the effective mode.

Every page includes `source_path`, `source_sha256`, `source_ref`, `page.start_line/end_line/total_lines/next_line/has_more`, and selection statistics. Pagination is explicit, not a claim that the rest of the artifact is irrelevant. Keep paging until the task has enough evidence. To recover a range, including omitted spans:

```sh
jev evidence --file /absolute/project/.evidence/run-001/stdout.log --goal 'Recover original evidence' --mode off --start-line 101 --max-lines 50 --expected-source-sha256 ORIGINAL_64_CHARACTER_HASH --json
```

Pass the original full-file hash on every later read. A changed source is rejected. Recovery never deletes or rewrites the original. Returned content is sanitized; line numbers preserve CRLF, Unicode line separators and multiline secret replacements, while columns inside replacements may differ.

The default page is at most 1000 lines / 64 KiB. The bounded source reader accepts UTF-8 artifacts up to 2 MiB and refuses binary/sensitive paths and links outside approved roots. A line larger than the requested page budget returns an explicit unavailable result rather than severing it. Oversized originals remain available to the user's ordinary local tools.

## Selection behavior

Selection preserves source order and structural records. Tracebacks, nested exception lines, test summaries, diff hunks, warnings, exit statuses and neighboring context are protected. Unknown formats remain intact. A record larger than a scoring window is retained. Bounded windows replace the old 600-line bypass; unprocessed or failed windows retain all evidence.

Scoring batches contain at most 16 questions and approximately 16 KiB of request data. At most two evaluations run concurrently under one five-second selection deadline, including accounting and retries in the client. Exhausted budgets stop transmission. These defaults need workload-specific evaluation, not a claim of universal optimality.

Omission markers and the complete JSON tool envelope cost tokens too. Use observed primary-model usage after tool serialization, and include retries, recovery and Jev usage in net measurements. Scoring content already read by the primary model is a measurement exercise, not retroactive savings.
