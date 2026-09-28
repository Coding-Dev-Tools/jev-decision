"""Windows CurrentUser DPAPI credentials, never plaintext configuration."""

from __future__ import annotations

import ctypes
import getpass
import os
import re
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import Any, Dict, Optional

from .runtime import RuntimeConfig

_MAGIC = b"JEV-DPAPI-1\x00"
_ENTROPY = b"JevDecision credential store v1"


class CredentialError(RuntimeError):
    """Credential operation failed; never include secret data in messages."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _data_blob(value: bytes) -> Any:
    buffer = ctypes.create_string_buffer(value, len(value))
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi(value: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise CredentialError("Managed credentials require Windows CurrentUser DPAPI")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source, source_buffer = _data_blob(value)
    entropy, entropy_buffer = _data_blob(_ENTROPY)
    destination = _DataBlob()
    method = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    description_type = ctypes.POINTER(ctypes.c_wchar_p) if decrypt else ctypes.c_wchar_p
    method.argtypes = [ctypes.POINTER(_DataBlob), description_type, ctypes.POINTER(_DataBlob),
                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DataBlob)]
    method.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    description = None if decrypt else "JevDecision API credential"
    # CRYPTPROTECT_UI_FORBIDDEN; LOCAL_MACHINE is deliberately never set.
    if not method(ctypes.byref(source), description, ctypes.byref(entropy), None, None, 1,
                  ctypes.byref(destination)):
        raise CredentialError("Unable to unlock managed credential" if decrypt else "Unable to protect managed credential")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(destination.pbData, ctypes.c_void_p))
        # Keep these referenced until the native call completes.
        del source_buffer, entropy_buffer


def _restrict_acl(path: Path, *, directory: bool) -> None:
    if os.name != "nt":
        os.chmod(str(path), 0o700 if directory else 0o600)
        return
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run([str(system32 / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
                                capture_output=True, text=True, timeout=5, creationflags=creationflags)
        match = re.search(r"S-1-\d+(?:-\d+)+", result.stdout) if result.returncode == 0 else None
        if match is None:
            raise CredentialError("Unable to determine credential owner")
        inheritance = "OICI" if directory else ""
        sddl = "D:P(A;" + inheritance + ";FA;;;" + match.group(0) + ")(A;" + inheritance + ";FA;;;SY)"
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                                                                                ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = ctypes.c_int
        advapi32.SetFileSecurityW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
        advapi32.SetFileSecurityW.restype = ctypes.c_int
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        descriptor = ctypes.c_void_p()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise CredentialError("Unable to restrict credential permissions")
        try:
            # Replace the complete DACL, including old explicit grants. Disable
            # inheritance so only this user and SYSTEM retain access.
            if not advapi32.SetFileSecurityW(str(path), 0x00000004 | 0x80000000, descriptor):
                raise CredentialError("Unable to restrict credential permissions")
        finally:
            kernel32.LocalFree(descriptor)
    except (OSError, subprocess.SubprocessError):
        raise CredentialError("Unable to restrict credential permissions") from None


def _validated_key(value: str) -> str:
    if not isinstance(value, str):
        raise CredentialError("API key must be nonempty text")
    value = value.strip()
    if not value or len(value) > 4096 or any(character.isspace() or ord(character) < 32 for character in value):
        raise CredentialError("API key must be a single nonempty token")
    return value


def save_api_key(api_key: str, config: Optional[RuntimeConfig] = None) -> None:
    """Protect and atomically save a key supplied directly by a local UI."""
    config = config or RuntimeConfig.load()
    key = _validated_key(api_key)
    protected = _MAGIC + _dpapi(key.encode("utf-8"), decrypt=False)
    temporary = None
    try:
        config.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        _restrict_acl(config.home, directory=True)
        with tempfile.NamedTemporaryFile(mode="wb", dir=str(config.home), prefix=".credential-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            _restrict_acl(temporary, directory=False)
            stream.write(protected)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(config.credential_path))
    except OSError:
        raise CredentialError("Unable to save protected credential") from None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def set_api_key_interactive(config: Optional[RuntimeConfig] = None) -> None:
    """Prompt only in a private interactive terminal; never fall back to echoing."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass("TypeSafe API key (input hidden): ")
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise CredentialError("A private interactive terminal is required for credential setup") from None
    save_api_key(value, config)


def load_api_key(config: Optional[RuntimeConfig] = None, allow_environment: bool = True) -> Optional[str]:
    """Prefer the managed key; explicit standalone environments remain supported."""
    config = config or RuntimeConfig.load()
    path = config.credential_path
    if path.exists():
        try:
            if not path.is_file() or path.stat().st_size > 64 * 1024:
                raise CredentialError("Invalid managed credential file")
            protected = path.read_bytes()
            if not protected.startswith(_MAGIC) or len(protected) == len(_MAGIC):
                raise CredentialError("Invalid managed credential file")
            clear = _dpapi(protected[len(_MAGIC):], decrypt=True)
            return _validated_key(clear.decode("utf-8"))
        except (OSError, UnicodeError):
            raise CredentialError("Unable to read managed credential") from None
    if allow_environment:
        for name in ("TYPESAFE_API_KEY", "JEV_API_KEY"):
            value = os.environ.get(name)
            if value and value.strip():
                return _validated_key(value)
    return None


def credential_status(config: Optional[RuntimeConfig] = None) -> Dict[str, Any]:
    """Presence metadata only; this deliberately does not claim authentication."""
    config = config or RuntimeConfig.load()
    managed_present = config.credential_path.is_file()
    environment_present = any(bool(os.environ.get(name, "").strip()) for name in ("TYPESAFE_API_KEY", "JEV_API_KEY"))
    return {"managed_present": managed_present, "environment_present": environment_present,
            "source": "managed" if managed_present else "environment" if environment_present else "none",
            "authentication_verified": False}
