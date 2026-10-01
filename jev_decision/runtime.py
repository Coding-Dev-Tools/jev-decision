"""Public, non-secret configuration shared by every local Jev harness."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OFFICIAL_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"
MAX_REQUEST_BYTES = 24 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
DAILY_LIMIT_USD = Decimal("1.00")
CONFIG_VERSION = 2


class RuntimeConfigError(ValueError):
    """Invalid local configuration; messages never include setting values."""


def _default_home() -> Path:
    override = os.environ.get("JEV_HOME")
    if override:
        result = Path(override).expanduser()
    elif (Path(sys.prefix) / "jev-runtime-home.txt").is_file():
        # A built runtime carries a non-secret physical home reference. This
        # keeps MSIX-redirected desktop and ordinary CLI processes on one ledger.
        marker = Path(sys.prefix) / "jev-runtime-home.txt"
        if marker.stat().st_size > 4096:
            raise RuntimeConfigError("Invalid installed runtime home reference")
        result = Path(marker.read_text(encoding="utf-8-sig").strip())
    elif os.environ.get("LOCALAPPDATA"):
        result = Path(os.environ["LOCALAPPDATA"]) / "JevDecision"
    elif os.name == "nt":
        result = Path.home() / "AppData" / "Local" / "JevDecision"
    else:
        result = Path.home() / ".local" / "state" / "JevDecision"
    if not result.is_absolute():
        raise RuntimeConfigError("Jev state directory must be an absolute path")
    return result.resolve()


@dataclass(frozen=True)
class RuntimeConfig:
    home: Path = field(default_factory=_default_home)
    endpoint: str = OFFICIAL_ENDPOINT
    model: str = DEFAULT_MODEL
    timeout_s: float = 5.0
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_response_bytes: int = MAX_RESPONSE_BYTES
    daily_budget_usd: Decimal = DAILY_LIMIT_USD
    timezone: str = "UTC"
    workspace_roots: Tuple[Path, ...] = ()
    enabled: bool = True
    pruning_enabled: bool = False
    setup_complete: bool = True
    credential_source: str = "auto"
    key_env: str = "TYPESAFE_API_KEY"
    selection_mode: str = "off"
    qualified_profile_path: Optional[Path] = None
    harness_target: Optional[str] = None
    harness_scope: str = "user"
    project_root: Optional[Path] = None

    def __post_init__(self) -> None:
        home = Path(self.home).expanduser()
        if not home.is_absolute():
            raise RuntimeConfigError("Jev state directory must be an absolute path")
        object.__setattr__(self, "home", home.resolve())
        if self.endpoint != OFFICIAL_ENDPOINT:
            raise RuntimeConfigError("Only the official TypeSafe endpoint is allowed")
        if self.model != DEFAULT_MODEL:
            raise RuntimeConfigError("Jev model must match the configured version pin")
        if not isinstance(self.timezone, str) or not self.timezone:
            raise RuntimeConfigError("Invalid budget timezone")
        # UTC needs no optional timezone database. The ledger preserves the
        # previous New York behavior on hosts without system tzdata.
        if self.timezone not in {"UTC", "America/New_York"}:
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                raise RuntimeConfigError("Unknown timezone; install timezone data or use UTC") from None
        if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, (int, float)):
            raise RuntimeConfigError("Invalid request deadline")
        if not 0 < self.timeout_s <= 5:
            raise RuntimeConfigError("Request deadline must be at most five seconds")
        for value, limit in ((self.max_request_bytes, MAX_REQUEST_BYTES),
                             (self.max_response_bytes, MAX_RESPONSE_BYTES)):
            if type(value) is not int or not 0 < value <= limit:
                raise RuntimeConfigError("Invalid request or response byte limit")
        try:
            budget = Decimal(str(self.daily_budget_usd))
        except (InvalidOperation, ValueError):
            raise RuntimeConfigError("Invalid daily budget") from None
        if not budget.is_finite() or budget < 0:
            raise RuntimeConfigError("Daily budget must be finite and nonnegative")
        parts = budget.as_tuple()
        excess_places = -parts.exponent - 9
        if excess_places > 0 and any(parts.digits[-excess_places:]):
            raise RuntimeConfigError("Daily budget has unsupported precision")
        object.__setattr__(self, "daily_budget_usd", budget)
        if any(type(value) is not bool for value in (self.enabled, self.pruning_enabled, self.setup_complete)):
            raise RuntimeConfigError("Runtime switches must be booleans")
        if budget == 0:
            object.__setattr__(self, "enabled", False)
        if not isinstance(self.credential_source, str) or self.credential_source not in {"auto", "env", "dpapi", "keyring"}:
            raise RuntimeConfigError("Unsupported credential source")
        if not isinstance(self.key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", self.key_env):
            raise RuntimeConfigError("Invalid credential environment variable name")
        if self.key_env.upper() in {"JEV_HOME", "JEV_ENDPOINT_URL", "JEV_OFFLINE_MODE"}:
            raise RuntimeConfigError("Credential environment variable conflicts with Jev runtime settings")
        if not isinstance(self.selection_mode, str) or self.selection_mode not in {"off", "shadow", "select"}:
            raise RuntimeConfigError("Unsupported evidence selection mode")
        for name in ("qualified_profile_path", "project_root"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, (str, Path)) or not Path(value).expanduser().is_absolute():
                    raise RuntimeConfigError("Configuration paths must be absolute")
                object.__setattr__(self, name, Path(value).expanduser().resolve())
        if self.selection_mode == "select" and self.qualified_profile_path is None:
            raise RuntimeConfigError("Selection requires a qualified profile path")
        # The legacy boolean never upgrades an unqualified configuration to
        # selection. Callers must also validate the profile before omission.
        object.__setattr__(self, "pruning_enabled", self.selection_mode == "select")
        if self.harness_target is not None and (not isinstance(self.harness_target, str) or
                not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.harness_target)):
            raise RuntimeConfigError("Invalid harness target")
        if not isinstance(self.harness_scope, str) or self.harness_scope not in {"user", "project"}:
            raise RuntimeConfigError("Invalid harness scope")
        if self.harness_scope == "project" and self.project_root is None:
            raise RuntimeConfigError("Project scope requires a project root")
        if not isinstance(self.workspace_roots, (tuple, list)):
            raise RuntimeConfigError("Workspace roots must be a list of absolute paths")
        roots = []
        for value in self.workspace_roots:
            if not isinstance(value, (str, Path)):
                raise RuntimeConfigError("Workspace roots must be absolute paths")
            root = Path(value).expanduser()
            if not root.is_absolute():
                raise RuntimeConfigError("Workspace roots must be absolute paths")
            resolved = root.resolve()
            if resolved not in roots:
                roots.append(resolved)
        object.__setattr__(self, "workspace_roots", tuple(roots))

    @property
    def config_path(self) -> Path:
        return self.home / "config.json"

    @property
    def credential_path(self) -> Path:
        return self.home / "credential.dpapi"

    @property
    def ledger_path(self) -> Path:
        return self.home / "budget.sqlite3"

    @classmethod
    def load(cls) -> "RuntimeConfig":
        home = _default_home()
        path = home / "config.json"
        if not path.exists():
            # Library callers explicitly constructing RuntimeConfig retain
            # their opt-in behavior. Merely installing the CLI is offline.
            return cls(home=home, enabled=False, setup_complete=False)
        try:
            if path.stat().st_size > 64 * 1024:
                raise RuntimeConfigError("Runtime configuration is too large")
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise RuntimeConfigError("Unable to read runtime configuration") from None
        fields = {"endpoint", "model", "timeout_s", "max_request_bytes", "max_response_bytes",
                  "daily_budget_usd", "timezone", "workspace_roots", "enabled", "pruning_enabled",
                  "setup_complete", "credential_source", "key_env", "selection_mode", "qualified_profile_path",
                  "harness_target", "harness_scope", "project_root"}
        if not isinstance(data, dict) or not set(data).issubset(fields | {"version"}):
            raise RuntimeConfigError("Runtime configuration contains unsupported fields")
        version = data.get("version", 1)
        if type(version) is not int or version not in {1, CONFIG_VERSION}:
            raise RuntimeConfigError("Unsupported runtime configuration version")
        data.pop("version", None)
        if version == 1:
            data.setdefault("timezone", "America/New_York")
            data.setdefault("daily_budget_usd", "1.00")
            data.setdefault("enabled", True)
            data.setdefault("setup_complete", True)
            data["selection_mode"] = "off"
            data["pruning_enabled"] = False
        else:
            # A hand-written/incomplete v2 file is not an implicit opt-in.
            data.setdefault("enabled", False)
            data.setdefault("setup_complete", False)
        config = cls(home=home, **data)
        # Setup persists canonical approval paths. Loading must not follow a
        # newly substituted junction/symlink and grant its target fresh access.
        stored_roots = tuple(dict.fromkeys(Path(value).expanduser() for value in data.get("workspace_roots", ())))
        from .evidence_file import _parts
        if tuple(map(_parts, config.workspace_roots)) != tuple(map(_parts, stored_roots)):
            raise RuntimeConfigError("Configured workspace root changed; review workspace setup")
        return config

    def _public_config(self) -> Dict[str, Any]:
        return {
            "version": CONFIG_VERSION, "endpoint": self.endpoint, "model": self.model,
            "timeout_s": self.timeout_s, "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "daily_budget_usd": str(self.daily_budget_usd), "timezone": self.timezone,
            "workspace_roots": [str(root) for root in self.workspace_roots],
            "enabled": self.enabled, "pruning_enabled": self.pruning_enabled,
            "setup_complete": self.setup_complete,
            "credential_source": self.credential_source, "key_env": self.key_env,
            "selection_mode": self.selection_mode,
            "qualified_profile_path": str(self.qualified_profile_path) if self.qualified_profile_path else None,
            "harness_target": self.harness_target, "harness_scope": self.harness_scope,
            "project_root": str(self.project_root) if self.project_root else None,
        }

    def public_status(self) -> Dict[str, Any]:
        """Return configuration metadata without inspecting or returning a key."""
        return dict(self._public_config(), home=str(self.home))

    def save(self) -> None:
        """Atomically persist only public configuration; no credential fields exist."""
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(self.home),
                                             prefix=".config-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                if os.name != "nt":
                    os.chmod(stream.name, 0o600)
                json.dump(self._public_config(), stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary), str(self.config_path))
        except OSError:
            raise RuntimeConfigError("Unable to save runtime configuration") from None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
