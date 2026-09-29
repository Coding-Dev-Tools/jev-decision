"""Protected OS credentials or an explicit environment reference; never plaintext files."""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any, Dict, Optional

from .runtime import RuntimeConfig

_MAGIC = b"JEV-DPAPI-1\x00"
_ENTROPY = b"JevDecision credential store v1"
_KEYRING_SERVICE = "jev-decision"


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


def _valid_key_format(value: Any) -> bool:
    """Match the HTTP client's credential format without accessing any store."""
    return (isinstance(value, str) and 1 <= len(value) <= 4096
            and value.lower() not in ("mock", "offline")
            and all(33 <= ord(character) <= 126 for character in value))


def _validated_key(value: str) -> str:
    if not isinstance(value, str):
        raise CredentialError("API key must be nonempty text")
    value = value.strip()
    if not _valid_key_format(value):
        raise CredentialError("API key must be a printable ASCII token of 1-4096 characters, excluding mock/offline")
    return value


def _os_keyring():
    """Use only known OS vaults, never a plaintext/third-party fallback.

    Merely checking credential status never invokes this function: desktop
    keychains can display an unlock prompt even for a read.
    """
    try:
        import keyring
        backend = keyring.get_keyring()
        platform = "linux" if sys.platform.startswith("linux") else sys.platform
        allowed = {
            "win32": {"keyring.backends.Windows.WinVaultKeyring"},
            "darwin": {"keyring.backends.macOS.Keyring"},
            "linux": {"keyring.backends.SecretService.Keyring", "keyring.backends.kwallet.DBusKeyring",
                      "keyring.backends.kwallet.DBusKeyringKWallet4"},
        }.get(platform, set())
        def identity(item):
            return type(item).__module__ + "." + type(item).__name__
        candidates = backend.backends if identity(backend) == "keyring.backends.chainer.ChainerBackend" else [backend]
        for candidate in candidates:
            if identity(candidate) in allowed and candidate.priority > 0:
                return candidate
    except ImportError:
        raise CredentialError("Install jev-decision[setup] or choose an environment reference") from None
    except Exception:
        raise CredentialError("OS credential storage is unavailable; choose an environment reference") from None
    raise CredentialError("A supported OS credential backend is required; plaintext backends are refused")


def _keyring_account(config: RuntimeConfig) -> str:
    # Different runtime homes intentionally have separate credential identities.
    return hashlib.sha256(os.path.normcase(str(config.home)).encode("utf-8")).hexdigest()


def validate_credential_source(config: RuntimeConfig) -> Dict[str, Any]:
    """Validate configuration/backend availability without retrieving a key."""
    source = config.credential_source
    if source == "dpapi" and os.name != "nt":
        raise CredentialError("DPAPI is available only on Windows")
    result = {"source": source, "authentication_verified": False}
    if source == "keyring":
        backend = _os_keyring()
        result["backend"] = type(backend).__module__ + "." + type(backend).__name__
    return result


def save_api_key(api_key: str, config: Optional[RuntimeConfig] = None) -> None:
    """Protect and atomically save a key supplied directly by a local UI."""
    config = config or RuntimeConfig.load()
    key = _validated_key(api_key)
    if config.credential_source == "env":
        raise CredentialError("Set the selected environment variable outside Jev; no plaintext key is stored")
    if config.credential_source == "keyring":
        try:
            _os_keyring().set_password(_KEYRING_SERVICE, _keyring_account(config), key)
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("Unable to save the credential in OS storage") from None
        return
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
    """Read only the selected source; auto preserves the v1 compatibility order."""
    config = config or RuntimeConfig.load()
    if config.credential_source == "keyring":
        try:
            value = _os_keyring().get_password(_KEYRING_SERVICE, _keyring_account(config))
            return _validated_key(value) if value is not None else None
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("Unable to read the credential from OS storage") from None
    path = config.credential_path
    if config.credential_source in {"auto", "dpapi"} and path.exists():
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
    if allow_environment and config.credential_source in {"auto", "env"}:
        names = (config.key_env,) if config.credential_source == "env" else ("TYPESAFE_API_KEY", "JEV_API_KEY")
        for name in names:
            value = os.environ.get(name)
            if value and value.strip():
                return _validated_key(value)
    return None


def credential_status(config: Optional[RuntimeConfig] = None) -> Dict[str, Any]:
    """Presence metadata only; this deliberately does not claim authentication."""
    config = config or RuntimeConfig.load()
    managed_present = config.credential_path.is_file() if config.credential_source in {"auto", "dpapi"} else False
    names = (config.key_env,) if config.credential_source == "env" else ("TYPESAFE_API_KEY", "JEV_API_KEY")
    environment_present = (any(bool(os.environ.get(name, "").strip()) for name in names)
                           if config.credential_source in {"auto", "env"} else False)
    keyring_selected = config.credential_source == "keyring"
    return {"managed_present": managed_present, "environment_present": environment_present,
            "credential_present": None if keyring_selected else managed_present or environment_present,
            "source": "keyring" if keyring_selected else "managed" if managed_present else "environment" if environment_present else "none",
            "configured_source": config.credential_source,
            "presence_status": "not_checked" if keyring_selected else "present" if managed_present or environment_present else "missing",
            "authentication_verified": False}
