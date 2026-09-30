"""Regressions for advisory authority and evidence preservation."""
import json
import threading
import time

import pytest
from test_qualification import WORKLOAD, qualified_documents

from jev_decision import DecisionBatch, JevClient, ScoreDecision
from jev_decision.evidence import read_evidence_file
from jev_decision.harness_guards import (
    MAX_WINDOW_BYTES,
    _select_from_shadow,
    guard_bash_command,
    prune_tool_output,
    verify_turn_completion,
)


class Scorer:
    model = "jev-1.13.0"
    def __init__(self, score=0.0, confidence=1.0, status="ok", delay=0):
        self.calls = []
        self.score, self.confidence, self.status, self.delay = score, confidence, status, delay
        self.active = self.maximum_active = 0
        self.lock = threading.Lock()
    def evaluate(self, state, questions, *, deadline_monotonic=None):
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.calls.append((state, questions, deadline_monotonic))
        try:
            if self.delay:
                time.sleep(self.delay)
            return DecisionBatch(status=self.status, source="provider" if self.status == "ok" else "none",
                resolved_model=self.model, attempts=1, usage={"input_tokens": 100, "output_tokens": 20},
                decisions={q.id: ScoreDecision(q.id, self.score, {}, self.confidence) for q in questions})
        finally:
            with self.lock:
                self.active -= 1


def log_text(count=150):
    return "".join("INFO ordinary cache observation %d xxxxxxxxxxxx\n" % i for i in range(count))


def saved_evidence(tmp_path, raw):
    path = tmp_path / "build.log"
    path.write_bytes(raw.encode("utf-8"))
    return read_evidence_file(str(path), "inspect", [str(tmp_path)], max_lines=10000, max_bytes=128 * 1024)

def test_missing_key_is_unavailable_not_safe():
    client = JevClient(api_key="")
    result = guard_bash_command("git status; delete-something", client=client)
    assert result["status"] == "unavailable"
    assert result["risk_probability"] is None
    assert "allow_auto" not in result
    assert result["permission_authority"] == "native_harness"

def test_intentions_do_not_certify_unexecuted_tests():
    result = verify_turn_completion("Ensure all tests passed", "edited file.py", "Tests not run yet",
                                    client=JevClient(offline_mode=True))
    assert result["status"] == "offline"
    assert "is_complete" not in result
    assert result["support_probability"] is None

def test_explicit_offline_never_fabricates_provider_results():
    batch = JevClient(offline_mode=True).evaluate("sample", {"q": {"type":"noul","instructions":"Is this text?"}})
    assert batch.status == "offline"
    assert batch.source != "provider"
    assert not batch.decisions

def test_windows_are_complete_batched_and_original_unchanged():
    raw = "".join("INFO boilerplate line %d %s\n" % (i, "z" * 20) for i in range(125))
    client = Scorer()
    output, stats = prune_tool_output(raw, "find useful information", client=client, max_retained_lines=30,
                                      mode="shadow", source_class="application_log")
    assert output == raw
    assert len(client.calls) == 1
    state, questions, deadline = client.calls[0]
    lines = raw.splitlines(keepends=True)
    assert deadline is not None
    for window in state["windows"].values():
        assert window["text"] == "".join(lines[window["first_line"] - 1:window["last_line"]])
    assert len(questions) <= 16
    assert not stats["pruned"]
    assert "token_savings_est" not in stats

def test_protected_failure_and_summary_spans_survive_qualified_pruning(tmp_path):
    lines = ["boilerplate %d xxxxxxxxxxxxxxxxxx\n" % i for i in range(125)]
    lines[55] = "AssertionError: required result missing\n"
    lines[82] = "45 tests passed; exit code 0\n"
    raw = "".join(lines)
    evidence = saved_evidence(tmp_path, raw)
    profile, report = qualified_documents("test_log")
    output, stats = prune_tool_output(raw, "debug issue", client=Scorer(), max_retained_lines=30,
        mode="select", source_class="test_log", source_ref=evidence["source_ref"],
        qualification=profile, qualification_report=report, expected_workload=WORKLOAD)
    assert lines[55] in output and lines[82] in output
    assert lines[0] in output and lines[-1] in output
    assert stats["saved_lines"] > 0
    assert "recover from source" in output

@pytest.mark.parametrize("client", [Scorer(confidence=0.2), Scorer(score=1.5), Scorer(status="unavailable")])
def test_uncertainty_retains_every_line(client, tmp_path):
    raw = log_text(125)
    evidence = saved_evidence(tmp_path, raw)
    profile, report = qualified_documents()
    output, stats = prune_tool_output(raw, "inspect", client=client, mode="select", source_class="application_log",
        source_ref=evidence["source_ref"], qualification=profile, qualification_report=report, expected_workload=WORKLOAD)
    assert output == raw
    assert not stats["pruned"]

def test_large_windows_not_silently_truncated():
    raw = "x" * 15000 + "\n" + "line\n" * 125
    client = Scorer()
    output, stats = prune_tool_output(raw, "inspect", client=client, mode="shadow", source_class="unknown")
    assert output == raw and not client.calls
    assert stats["status"] == "retained_unknown_format"


@pytest.mark.parametrize("mode,raw,source_class,status", [
    ("off", log_text(), "application_log", "disabled"),
    ("shadow", "INFO short\n", "application_log", "skipped_small_input"),
    ("shadow", "1 test passed\n" * 150, "test_log", "retained_protected"),
    ("shadow", "unrecognized prose\n" * 150, "unknown", "retained_unknown_format"),
    ("shadow", "unrecognized prose\n" * 150, "application_log", "retained_unknown_format"),
    ("shadow", "unrecognized prose\n" * 150, "test_log", "retained_unknown_format"),
    ("shadow", "unrecognized prose\n" * 150, "build_log", "retained_unknown_format"),
    ("shadow", "not JSON\n" * 150, "jsonl", "retained_unknown_format"),
])
def test_zero_call_bypasses(mode, raw, source_class, status):
    client = Scorer()
    output, stats = prune_tool_output(raw, "inspect", client=client, mode=mode, source_class=source_class)
    assert output == raw and not client.calls and stats["status"] == status
    assert stats["calls"] == 0 and "token_savings_est" not in stats


def test_incremental_windows_bounded_and_at_most_two_concurrent():
    raw, client = log_text(1200), Scorer(delay=0.01)
    output, stats = prune_tool_output(raw, "inspect", client=client, mode="shadow", source_class="application_log")
    assert output == raw and len(client.calls) > 1 and stats["status"] == "ok"
    assert 1 <= client.maximum_active <= 2
    requested = {}
    for state, questions, deadline in client.calls:
        assert deadline is not None and len(questions) <= 16
        payload = {"model": client.model, "state": state, "questions": {q.id: q.to_wire() for q in questions}}
        assert len(json.dumps(payload).encode()) <= MAX_WINDOW_BYTES
        requested.update(state["windows"])
    source_lines = raw.splitlines(keepends=True)
    cursor = 1
    for span in stats["spans"]:
        assert span["start_line"] == cursor
        cursor = span["end_line"] + 1
        if not span["protected"]:
            window = requested["span_" + str(span["start_line"])]
            assert window["text"] == "".join(source_lines[span["start_line"] - 1:span["end_line"]])
            assert span["assessed"]
    assert cursor == len(source_lines) + 1


def test_select_requires_real_recovery_and_qualification(tmp_path):
    raw, client = log_text(), Scorer()
    output, stats = prune_tool_output(raw, "inspect", client=client, mode="select", source_class="application_log")
    assert output == raw and not client.calls and stats["status"] == "retained_unrecoverable_source"
    evidence = saved_evidence(tmp_path, raw)
    output, stats = prune_tool_output(raw, "inspect", client=client, mode="select", source_class="application_log",
                                      source_ref=evidence["source_ref"])
    assert output == raw and not client.calls and stats["status"] == "retained_unqualified"


def test_complete_trace_and_hunk_survive_experimental_selection(tmp_path):
    raw = (log_text(60) + "Traceback (most recent call last):\n" + "  File src/a.py:20\n" * 35 +
           "ValueError: invalid value\n" + log_text(60) + "diff --git a/file b/file\n@@ -1,3 +1,3 @@\n" +
           " retained context\n" * 40 + "+new line\n" + log_text(30))
    evidence = saved_evidence(tmp_path, raw)
    client = Scorer()
    _, stats = prune_tool_output(raw, "inspect", client=client, mode="shadow", source_class="test_log")
    calls = len(client.calls)
    output, selected = _select_from_shadow(raw, stats, source_ref=evidence["source_ref"])
    assert "Traceback (most recent call last):\n" + "  File src/a.py:20\n" * 35 + "ValueError: invalid value\n" in output
    assert raw[raw.index("diff --git"):] in output
    assert selected["pruned"] and selected["production_qualified"] is False and not stats["pruned"]
    assert len(client.calls) == calls


def test_whole_operation_deadline_retains_inflight_windows():
    raw, client = log_text(1200), Scorer(delay=0.3)
    started = time.monotonic()
    output, stats = prune_tool_output(raw, "inspect", client=client, mode="shadow", source_class="application_log", deadline_s=0.04)
    assert time.monotonic() - started < 0.25
    assert output == raw and len(client.calls) <= 2 and not any(span["assessed"] for span in stats["spans"])
    assert stats["usage"]["input_tokens"] is None


def test_source_mutation_during_inference_prevents_omission(tmp_path):
    raw = log_text()
    evidence = saved_evidence(tmp_path, raw)
    profile, report = qualified_documents()
    class Mutating(Scorer):
        def evaluate(self, *args, **kwargs):
            (tmp_path / "build.log").write_text("changed", encoding="utf-8")
            return super().evaluate(*args, **kwargs)
    output, stats = prune_tool_output(raw, "inspect", client=Mutating(), mode="select", source_class="application_log",
        source_ref=evidence["source_ref"], qualification=profile, qualification_report=report, expected_workload=WORKLOAD)
    assert output == raw and stats["status"] == "retained_source_changed" and not stats["pruned"]


def test_prefixed_stack_frames_are_protected_beyond_fixed_line_chunks(tmp_path):
    trace = "INFO Traceback (most recent call last):\n" + "INFO   File src/parser.py:37\n" * 60
    raw = log_text(60) + trace + "INFO AssertionError: wrong result\n" + log_text(60)
    evidence = saved_evidence(tmp_path, raw)
    _, stats = prune_tool_output(raw, "inspect", client=Scorer(), mode="shadow", source_class="application_log")
    output, _ = _select_from_shadow(raw, stats, source_ref=evidence["source_ref"])
    assert trace in output


def test_jsonl_records_are_complete_and_protected_evidence_survives(tmp_path):
    records = [json.dumps({"kind": "observation", "value": "x" * 80, "n": i}) + "\n" for i in range(150)]
    records[70] = json.dumps({"kind": "error", "actual": 3, "expected": 4}) + "\n"
    raw = "".join(records)
    evidence = saved_evidence(tmp_path, raw)
    _, stats = prune_tool_output(raw, "inspect", client=Scorer(), mode="shadow", source_class="jsonl")
    output, _ = _select_from_shadow(raw, stats, source_ref=evidence["source_ref"])
    assert records[70] in output
    for line in output.splitlines():
        if not line.startswith("[Jev omitted"):
            assert isinstance(json.loads(line), dict)

def test_file_evidence_redacts_and_preserves_source(tmp_path):
    original = b"api_key=secret-test-value\nbuild information\n"
    path = tmp_path / "build.log"
    path.write_bytes(original)
    result = read_evidence_file(str(path), "inspect", [str(tmp_path)], client=Scorer())
    assert "secret-test-value" not in result["output"]
    assert result["redacted"]
    assert path.read_bytes() == original

def test_file_evidence_denies_secrets_and_escape(tmp_path):
    for name in [".env", "credentials.json", "private.key"]:
        path = tmp_path / name
        path.write_text("content")
        with pytest.raises(ValueError):
            read_evidence_file(str(path), "inspect", [str(tmp_path)])
    outside = tmp_path / "outside"
    outside.mkdir()
    approved = tmp_path / "approved"
    approved.mkdir()
    target = outside / "build.log"
    target.write_text("content")
    with pytest.raises(ValueError, match="outside_approved"):
        read_evidence_file(str(approved / ".." / "outside" / "build.log"), "inspect", [str(approved)])

@pytest.mark.parametrize("ancestor", ["auth-service", "oauth_app", "secrets-manager", "token.bridge"])
def test_private_name_screen_starts_below_the_approved_root(tmp_path, ancestor):
    # Operators commonly keep projects under names like auth-service/. The
    # approved root itself is explicit consent; only names below it are screened.
    root = tmp_path / ancestor / "workspace"
    (root / "run").mkdir(parents=True)
    (root / "run" / "stdout.log").write_text("collected 3 items\n")
    result = read_evidence_file(str(root / "run" / "stdout.log"), "inspect", [str(root)])
    assert result["status"] == "ok" and result["output"] == "collected 3 items\n"
    for relative in (".env", ".git/config", "secrets/run.log", "run/credentials.json", "keys/server.pem"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")
        with pytest.raises(ValueError, match="credential_or_private_file_denied"):
            read_evidence_file(str(path), "inspect", [str(root)])

def test_file_evidence_denies_symlink_escape(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("private")
    link = approved / "build.log"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(ValueError, match="outside_approved"):
        read_evidence_file(str(link), "inspect", [str(approved)])
