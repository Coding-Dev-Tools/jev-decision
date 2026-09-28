"""Recoverable paging and original source locations survive sanitization."""
import hashlib

import pytest

from jev_decision.evidence import read_evidence_file
from jev_decision.policy import sanitize_evidence
from test_jev import Scorer, log_text


def test_pages_reconstruct_file_larger_than_old_response_limit_without_calls(tmp_path):
    path = tmp_path / "build.log"
    raw = log_text(5000)
    path.write_bytes(raw.encode())
    expected = hashlib.sha256(raw.encode()).hexdigest()
    cursor, pieces, client = 1, [], Scorer()
    while True:
        result = read_evidence_file(str(path), "inspect", [str(tmp_path)], client=client,
                                   start_line=cursor, max_lines=700, max_bytes=16 * 1024,
                                   expected_source_sha256=expected)
        assert result["source_sha256"] == expected and result["original_preserved"]
        assert len(result["output"].encode()) <= 16 * 1024
        assert result["page"]["start_line"] == cursor
        pieces.append(result["output"])
        if not result["page"]["has_more"]:
            break
        cursor = result["page"]["next_line"]
    assert "".join(pieces) == raw and not client.calls
    assert path.read_bytes() == raw.encode()


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r", "\u2028"])
def test_redaction_preserves_original_line_identity_and_endings(ending):
    raw = ending.join(["INFO before", "-----BEGIN PRIVATE KEY-----", "synthetic private material",
                       "-----END PRIVATE KEY-----", "important evidence", ""])
    clean = sanitize_evidence(raw)
    assert "synthetic private material" not in clean
    assert len(clean.splitlines()) == len(raw.splitlines())
    assert clean.splitlines()[4] == "important evidence"
    assert clean.count(ending) == raw.count(ending)


def test_multiline_assignment_redaction_keeps_later_source_line():
    raw = 'password="synthetic\ncontinued"\nINFO evidence\n'
    clean = sanitize_evidence(raw)
    assert "synthetic" not in clean and "continued" not in clean
    assert clean.splitlines()[2] == "INFO evidence"


def test_page_spans_and_redaction_refer_to_original_source(tmp_path):
    lines = log_text(200).splitlines(keepends=True)
    lines[10:14] = ["-----BEGIN PRIVATE KEY-----\n", "synthetic material\n",
                    "still synthetic\n", "-----END PRIVATE KEY-----\n"]
    lines[70] = "INFO required evidence marker\n"
    raw = "".join(lines)
    path = tmp_path / "build.log"
    path.write_bytes(b"\xef\xbb\xbf" + raw.encode())
    client = Scorer()
    result = read_evidence_file(str(path), "inspect", [str(tmp_path)], client=client,
        start_line=10, max_lines=140, mode="shadow", source_class="application_log", max_retained_lines=20)
    assert result["redacted"] and "synthetic material" not in result["output"]
    assert result["output"].splitlines()[71 - 10] == "INFO required evidence marker"
    spans = result["stats"]["spans"]
    assert spans[0]["start_line"] == 10 and spans[-1]["end_line"] == 149
    assert any(span["protected"] and span["start_line"] <= 71 <= span["end_line"] for span in spans)
    assert result["source_ref"]["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_changed_file_cannot_be_read_as_same_evidence(tmp_path):
    path = tmp_path / "build.log"
    path.write_text(log_text(), encoding="utf-8")
    first = read_evidence_file(str(path), "inspect", [str(tmp_path)], max_lines=30)
    path.write_text("INFO replacement\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        read_evidence_file(str(path), "inspect", [str(tmp_path)],
                           expected_source_sha256=first["source_sha256"])


def test_oversized_single_record_is_explicit_and_never_severed(tmp_path):
    path = tmp_path / "build.log"
    path.write_text("INFO " + "x" * 200000 + "\n", encoding="utf-8")
    client = Scorer()
    result = read_evidence_file(str(path), "inspect", [str(tmp_path)], client=client, mode="shadow")
    assert result["status"] == "unavailable" and result["error_code"] == "source_line_exceeds_page_limit"
    assert result["page"]["has_more"] and result["page"]["next_line"] == 1
    assert "output" not in result and not client.calls


@pytest.mark.parametrize("arguments", [{"start_line": 0}, {"max_lines": True}, {"max_bytes": 131073},
    {"expected_source_sha256": "not a hash"}, {"start_line": 9999}])
def test_invalid_page_arguments_rejected(tmp_path, arguments):
    path = tmp_path / "build.log"
    path.write_text("INFO line\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_evidence_file(str(path), "inspect", [str(tmp_path)], **arguments)
