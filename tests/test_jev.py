"""Regressions for advisory authority and evidence preservation."""
import pytest

from jev_decision import DecisionBatch, JevClient, ScoreDecision
from jev_decision.evidence import read_evidence_file
from jev_decision.harness_guards import (
    guard_bash_command,
    prune_tool_output,
    verify_turn_completion,
)


class Scorer:
    def __init__(self, score=0.0, confidence=1.0, status="ok"):
        self.calls = []
        self.score, self.confidence, self.status = score, confidence, status
    def evaluate(self, state, questions):
        self.calls.append((state, questions))
        return DecisionBatch(status=self.status, source="provider" if self.status == "ok" else "none",
            decisions={q.id: ScoreDecision(q.id, self.score, {}, self.confidence) for q in questions})

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
    raw = "".join("boilerplate line %d %s\n" % (i, "z" * 20) for i in range(125))
    client = Scorer()
    output, stats = prune_tool_output(raw, "find useful information", client=client, max_retained_lines=30)
    assert output == raw
    assert len(client.calls) == 1
    state, questions = client.calls[0]
    assert "".join(window["text"] for window in state["windows"].values()) == raw
    assert len(questions) == 5
    assert not stats["pruned"]
    assert "token_savings_est" not in stats

def test_protected_failure_and_summary_spans_survive_qualified_pruning():
    lines = ["boilerplate %d xxxxxxxxxxxxxxxxxx\n" % i for i in range(125)]
    lines[55] = "AssertionError: required result missing\n"
    lines[82] = "45 tests passed; exit code 0\n"
    raw = "".join(lines)
    output, stats = prune_tool_output(raw, "debug issue", client=Scorer(), max_retained_lines=30, allow_prune=True)
    assert lines[55] in output and lines[82] in output
    assert lines[0] in output and lines[-1] in output
    assert stats["saved_lines"] == 25
    assert "source lines 26-50" in output

@pytest.mark.parametrize("client", [Scorer(confidence=0.2), Scorer(score=1.5), Scorer(status="unavailable")])
def test_uncertainty_retains_every_line(client):
    raw = "".join("ordinary record %d xxxxxxxxxxxx\n" % i for i in range(125))
    output, stats = prune_tool_output(raw, "inspect", client=client, allow_prune=True)
    assert output == raw
    assert not stats["pruned"]

def test_large_windows_not_silently_truncated():
    raw = "x" * 15000 + "\n" + "line\n" * 125
    client = Scorer()
    output, stats = prune_tool_output(raw, "inspect", client=client, allow_prune=True)
    assert output == raw and not client.calls
    assert stats["status"] == "retained_input_limit"

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
