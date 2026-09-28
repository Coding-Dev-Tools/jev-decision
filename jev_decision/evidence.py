"""Bounded read-only evidence access restricted to operator-configured roots."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .client import JevClient
from .harness_guards import prune_tool_output

MAX_FILE_BYTES = 2 * 1024 * 1024
_DENIED = re.compile(r"(^\.env(?:\.|$))|(?:credentials?|secrets?|passwords?|tokens?|auth(?:entication)?)(?:[._-]|$)|\.(?:pem|key|pfx|p12|sqlite|db)$", re.I)
_DENIED_DIRS = {".git", ".ssh", ".aws", ".azure", ".gnupg", "secrets", "credentials", "node_modules"}

def read_evidence_file(path: str, goal: str, roots: Iterable[str], *, client: Optional[JevClient] = None,
                       allow_prune: bool = False, max_retained_lines: int = 100) -> Dict[str, Any]:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("absolute_evidence_path_required")
    resolved = candidate.resolve(strict=True)
    approved = [Path(root).resolve(strict=True) for root in roots]
    for checked in (candidate, resolved):
        if any(part.lower() in _DENIED_DIRS or _DENIED.search(part) for part in checked.parts):
            raise ValueError("credential_or_private_file_denied")
    if not any(_within(resolved, root) for root in approved):
        raise ValueError("outside_approved_workspace")
    if not resolved.is_file() or resolved.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("evidence_file_limit")
    with resolved.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES or b"\x00" in data:
        raise ValueError("evidence_file_limit_or_binary")
    raw = data.decode("utf-8-sig", errors="strict")
    from .policy import sanitize
    safe = sanitize(raw)
    output, stats = prune_tool_output(safe, goal, client=client, allow_prune=allow_prune,
                                      max_retained_lines=max_retained_lines)
    if len(output.encode("utf-8")) > 128 * 1024:
        return {"source_path": str(resolved), "source_sha256": hashlib.sha256(data).hexdigest(),
                "status": "unavailable", "error_code": "evidence_exceeds_response_limit",
                "original_bytes": len(data), "original_preserved": True}
    return {"source_path": str(resolved), "source_sha256": hashlib.sha256(data).hexdigest(),
            "original_bytes": len(data), "redacted": safe != raw, "output": output, "stats": stats}

def _within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(str(path)), os.path.normcase(str(root))]) == os.path.normcase(str(root))
    except ValueError:
        return False
