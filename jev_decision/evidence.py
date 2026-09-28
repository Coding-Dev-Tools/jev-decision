"""Bounded read-only evidence access restricted to operator-configured roots."""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, Iterable, Optional

from .client import JevClient
from .evidence_file import read_evidence_bytes
from .harness_guards import detect_source_class, prune_tool_output
from .policy import sanitize_evidence

MAX_FILE_BYTES = 2 * 1024 * 1024

def read_evidence_file(path: str, goal: str, roots: Iterable[str], *, client: Optional[JevClient] = None,
                       allow_prune: bool = False, max_retained_lines: int = 100,
                       mode: str = "off", source_class: str = "auto", qualification: Any = None,
                       qualification_report: Any = None, start_line: int = 1, max_lines: int = 1000,
                       max_bytes: int = 64 * 1024, expected_source_sha256: Optional[str] = None,
                       expected_workload: Any = None) -> Dict[str, Any]:
    """Read a recoverable, sanitized page with original source line identities.

    Pass the first page's source hash on subsequent reads. A changed file is
    rejected instead of silently combining evidence from different versions.
    No original is rewritten, and the raw artifact is never a provider payload.
    """
    started = time.monotonic()
    for value, maximum in ((start_line, 2 * 1024 * 1024 + 1), (max_lines, 10000), (max_bytes, 128 * 1024)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("invalid_evidence_page")
    if expected_source_sha256 is not None and (not isinstance(expected_source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256) is None):
        raise ValueError("invalid_expected_source_sha256")
    resolved, data = read_evidence_bytes(path, roots, max_bytes=MAX_FILE_BYTES)
    if len(data) > MAX_FILE_BYTES or b"\x00" in data:
        raise ValueError("evidence_file_limit_or_binary")
    source_hash = hashlib.sha256(data).hexdigest()
    if expected_source_sha256 is not None and source_hash != expected_source_sha256:
        raise ValueError("source_hash_mismatch")
    raw = data.decode("utf-8-sig", errors="strict")
    safe = sanitize_evidence(raw)
    lines = safe.splitlines(keepends=True)
    if start_line > len(lines) + 1:
        raise ValueError("source_line_out_of_range")
    selected, consumed = [], 0
    end = start_line - 1
    for line in lines[start_line - 1:start_line - 1 + max_lines]:
        size = len(line.encode("utf-8"))
        if consumed + size > max_bytes:
            break
        selected.append(line)
        consumed += size
        end += 1
    more = end < len(lines)
    page = {"start_line": start_line, "end_line": end, "total_lines": len(lines),
            "next_line": end + 1 if more else None, "has_more": more,
            "max_bytes": max_bytes, "max_lines": max_lines}
    result: Dict[str, Any] = {"source_path": str(resolved), "source_sha256": source_hash,
        "original_bytes": len(data), "original_preserved": True, "redacted": safe != raw, "page": page}
    if not selected and more:
        # Do not sever a UTF-8 line/record or claim the inaccessible tail is gone.
        return {**result, "status": "unavailable", "error_code": "source_line_exceeds_page_limit"}
    text = "".join(selected)
    reference = {"source_path": str(resolved), "source_sha256": source_hash, "start_line": start_line,
                 "end_line": end, "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    if source_class == "auto":
        source_class = detect_source_class(safe)
    remaining = 5.0 - (time.monotonic() - started)
    output, stats = prune_tool_output(text, goal, client=client, allow_prune=allow_prune,
        max_retained_lines=max_retained_lines, mode=mode if remaining > 0 else "off",
        source_class=source_class, source_ref=reference, qualification=qualification,
        qualification_report=qualification_report, source_start_line=start_line,
        deadline_s=max(0.000001, remaining), expected_workload=expected_workload)
    if remaining <= 0:
        stats.update(status="retained_deadline")
    return {**result, "status": "ok", "output": output, "stats": stats,
            "source_ref": reference, "source_class": source_class}
