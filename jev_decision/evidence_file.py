"""Read bounded evidence only after validating the opened file descriptor.

Path resolution is an early filter, not authority to read a subsequently opened
file. Linux, macOS and Windows must all identify that opened file before any
content is read. Other platforms fail closed instead of using a racy fallback.
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path
from typing import Iterable, Tuple

_DENIED = re.compile(r"(^\.env(?:\.|$))|(?:credentials?|secrets?|passwords?|tokens?|auth(?:entication)?)(?:[._-]|$)|\.(?:pem|key|pfx|p12|sqlite|db)$", re.I)
_DENIED_DIRS = {".git", ".ssh", ".aws", ".azure", ".gnupg", "secrets", "credentials", "node_modules"}


def _check_name(path: Path) -> None:
    if any(part.lower() in _DENIED_DIRS or _DENIED.search(part) for part in path.parts):
        raise ValueError("credential_or_private_file_denied")


def _plain_windows_name(name: str) -> str:
    if name.startswith("\\\\?\\UNC\\"):
        return "\\\\" + name[8:]
    return name[4:] if name.startswith("\\\\?\\") else name


def _parts(path: Path) -> tuple:
    if os.name == "nt":
        path = Path(_plain_windows_name(str(path)))
    parts = path.parts
    # Resolved/handle paths already have canonical component spelling. Do not
    # case-fold components: Windows supports case-sensitive directories too.
    return (parts[0].casefold(), *parts[1:]) if os.name == "nt" and parts else parts


def _within(path: Path, root: Path) -> bool:
    child, parent = _parts(path), _parts(root)
    return len(child) > len(parent) and child[:len(parent)] == parent


def _windows_open(path: Path) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    information = kernel.GetFileInformationByHandleEx
    information.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    information.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value
    parents = []
    opened = None

    def open_handle(component: Path, directory: bool):
        name = str(component)
        if not name.startswith("\\\\?\\"):
            name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
        # OPEN_REPARSE_POINT prevents following the final component. Already
        # opened ancestors deny delete-sharing, pinning them during traversal.
        handle = create(name, 0x80 if directory else 0x80000000, 0x3, None, 3,
                        0x00200000 | (0x02000000 if directory else 0), None)
        if handle == invalid:
            raise ValueError("evidence_file_open_failed")
        try:
            attributes = AttributeTag()
            if not information(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
                raise ValueError("evidence_file_type_unavailable")
            if attributes.attributes & 0x400 or bool(attributes.attributes & 0x10) != directory:
                raise ValueError("evidence_source_changed")
            return handle
        except BaseException:
            close(handle)
            raise

    try:
        for parent in reversed(path.parents):
            parents.append(open_handle(parent, True))
        opened = open_handle(path, False)
        descriptor = msvcrt.open_osfhandle(opened, os.O_RDONLY | os.O_BINARY)
        opened = None  # The descriptor now owns the native handle.
        return descriptor
    finally:
        if opened is not None:
            close(opened)
        for parent in reversed(parents):
            close(parent)


def _open_descriptor(path: Path) -> int:
    if os.name == "nt":
        return _windows_open(path)
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("safe_evidence_open_unavailable")
    # Walk the resolved absolute path through pinned directory descriptors.
    # O_NOFOLLOW on the final component alone would miss parent replacements.
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    directory = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                       | getattr(os, "O_CLOEXEC", 0), dir_fd=directory)
    finally:
        os.close(directory)


def _handle_path(descriptor: int) -> Path:
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        final_path = kernel.GetFinalPathNameByHandleW
        final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        final_path.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = final_path(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise ValueError("evidence_handle_path_unavailable")
        name = _plain_windows_name(buffer.value)
    elif sys.platform.startswith("linux"):
        name = os.readlink("/proc/self/fd/" + str(descriptor))
        if name.endswith(" (deleted)"):
            raise ValueError("evidence_source_changed")
    elif sys.platform == "darwin":
        import fcntl

        # macOS F_GETPATH returns the kernel path for the opened descriptor.
        name = os.fsdecode(fcntl.fcntl(descriptor, 50, b"\0" * 1024).split(b"\0", 1)[0])
    else:
        raise ValueError("safe_evidence_open_unavailable")
    path = Path(name)
    if not path.is_absolute():
        raise ValueError("evidence_handle_path_unavailable")
    return path


def _snapshot(info: os.stat_result) -> tuple:
    # Windows Python versions can expose different ctime meanings through stat
    # and fstat. Compare ctime only between two samples of the same descriptor.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _validate_handle(descriptor: int, expected: Path, roots: list[Path],
                     original: os.stat_result, max_bytes: int) -> Tuple[Path, os.stat_result]:
    actual = os.fstat(descriptor)
    if not stat.S_ISREG(actual.st_mode) or not 0 <= actual.st_size <= max_bytes:
        raise ValueError("evidence_file_limit")
    if _snapshot(actual) != _snapshot(original):
        raise ValueError("evidence_source_changed")
    opened = _handle_path(descriptor)
    _check_name(opened)
    if not any(_within(opened, root) for root in roots):
        raise ValueError("outside_approved_workspace")
    if _parts(opened) != _parts(expected):
        raise ValueError("evidence_source_changed")
    return opened, actual


def read_evidence_bytes(path: str | Path, roots: Iterable[str | Path], *,
                        max_bytes: int, exact_path: bool = False) -> Tuple[Path, bytes]:
    """Return the canonical opened path and bounded bytes, or fail before egress.

    ``exact_path`` is for recovery of a previously returned canonical source:
    replacement links must not redirect it, even to another approved location.
    Missing/unmounted roots are ignored independently of other approved roots.
    """
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("invalid_evidence_file_limit")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("absolute_evidence_path_required")
    _check_name(candidate)
    resolved = candidate.resolve(strict=True)
    _check_name(resolved)
    if exact_path and _parts(resolved) != _parts(candidate):
        raise ValueError("evidence_source_changed")
    approved = []
    for root in roots:
        try:
            canonical = Path(root).resolve(strict=True)
            if canonical.is_dir():
                approved.append(canonical)
        except (OSError, ValueError, RuntimeError):
            continue
    if not any(_within(resolved, root) for root in approved):
        raise ValueError("outside_approved_workspace")
    original = os.stat(resolved, follow_symlinks=False)
    if not stat.S_ISREG(original.st_mode) or not 0 <= original.st_size <= max_bytes:
        raise ValueError("evidence_file_limit")
    descriptor = _open_descriptor(resolved)
    try:
        opened, before_read = _validate_handle(descriptor, resolved, approved, original, max_bytes)
        chunks, length = [], 0
        while length <= max_bytes:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        if length > max_bytes:
            raise ValueError("evidence_file_limit")
        _, after_read = _validate_handle(descriptor, resolved, approved, before_read, max_bytes)
        if before_read.st_ctime_ns != after_read.st_ctime_ns:
            raise ValueError("evidence_source_changed")
        data = b"".join(chunks)
        if len(data) != original.st_size:
            raise ValueError("evidence_source_changed")
        return opened, data
    finally:
        os.close(descriptor)
