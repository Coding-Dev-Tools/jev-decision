"""Bounded read-only evidence access restricted to operator-configured roots."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .client import JevClient
from .harness_guards import prune_tool_output

MAX_FILE_BYTES = 2 * 1024 * 1024
_DENIED = re.compile(r"(^\.env(?:\.|$))|(?:credentials?|secrets?|passwords?|tokens?|auth(?:entication)?)(?:[._-]|$)|\.(?:pem|key|pfx|p12|dpapi|jks|sqlite|db)$", re.I)
_DENIED_DIRS = {".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", "secrets", "credentials", "node_modules"}
_DENIED_NAMES = {".npmrc", ".pypirc", ".netrc", "_netrc", ".git-credentials",
                 "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}


def _check_name(path: Path) -> None:
    for part in path.parts:
        normalized = part.rstrip(" .").casefold()
        if (normalized in _DENIED_DIRS or normalized in _DENIED_NAMES
                or _DENIED.search(normalized)
                or (":" in part and part != path.anchor)):
            raise ValueError("credential_or_private_file_denied")


def _opened_path(stream: Any) -> Path:
    """Identify the opened file, after any symlink/junction substitution."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        query = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        query.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        query.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = query(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise ValueError("evidence_opened_path_unavailable")
        name = buffer.value
        if name.startswith("\\\\?\\UNC\\"):
            name = "\\\\" + name[8:]
        elif name.startswith("\\\\?\\"):
            name = name[4:]
        return Path(name)
    if sys.platform.startswith("linux"):
        return Path(os.readlink("/proc/self/fd/" + str(stream.fileno())))
    if sys.platform == "darwin":
        import fcntl
        return Path(os.fsdecode(fcntl.fcntl(stream.fileno(), 50, b"\0" * 1024).split(b"\0", 1)[0]))
    raise ValueError("evidence_opened_path_unavailable")

def read_evidence_file(path: str, goal: str, roots: Iterable[str], *, client: Optional[JevClient] = None,
                       allow_prune: bool = False, max_retained_lines: int = 100) -> Dict[str, Any]:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("absolute_evidence_path_required")
    resolved = candidate.resolve(strict=True)
    # RuntimeConfig already stores absolute resolved roots. Do not re-resolve an
    # approved path through a replacement junction during this read.
    approved = [Path(root) for root in roots]
    if any(not root.is_absolute() for root in approved):
        raise ValueError("absolute_workspace_root_required")
    for checked in (candidate, resolved):
        _check_name(checked)
    if not any(_within(resolved, root) for root in approved):
        raise ValueError("outside_approved_workspace")
    if not resolved.is_file() or resolved.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("evidence_file_limit")
    with resolved.open("rb") as stream:
        opened = _opened_path(stream)
        _check_name(opened)
        if not any(_within(opened, root) for root in approved):
            raise ValueError("outside_approved_workspace")
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
            raise ValueError("evidence_file_limit")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES or b"\x00" in data:
        raise ValueError("evidence_file_limit_or_binary")
    raw = data.decode("utf-8-sig", errors="strict")
    from .policy import sanitize
    safe = sanitize(raw)
    output, stats = prune_tool_output(safe, goal, client=client, allow_prune=allow_prune,
                                      max_retained_lines=max_retained_lines)
    if len(output.encode("utf-8")) > 128 * 1024:
        return {"source_path": str(opened), "source_sha256": hashlib.sha256(data).hexdigest(),
                "status": "unavailable", "error_code": "evidence_exceeds_response_limit",
                "original_bytes": len(data), "original_preserved": True}
    return {"source_path": str(opened), "source_sha256": hashlib.sha256(data).hexdigest(),
            "original_bytes": len(data), "redacted": safe != raw, "output": output, "stats": stats}

def _within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(str(path)), os.path.normcase(str(root))]) == os.path.normcase(str(root))
    except ValueError:
        return False
