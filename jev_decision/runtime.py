"""Public, non-secret configuration shared by every local Jev harness."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Tuple

OFFICIAL_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"
MAX_REQUEST_BYTES = 24 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
DAILY_LIMIT_USD = Decimal("1.00")


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
    timezone: str = "America/New_York"
    workspace_roots: Tuple[Path, ...] = ()
    enabled: bool = True
    pruning_enabled: bool = False

    def __post_init__(self) -> None:
        home = Path(self.home).expanduser()
        if not home.is_absolute():
            raise RuntimeConfigError("Jev state directory must be an absolute path")
        object.__setattr__(self, "home", home.resolve())
        if self.endpoint != OFFICIAL_ENDPOINT:
            raise RuntimeConfigError("Only the official TypeSafe endpoint is allowed")
        if self.model != DEFAULT_MODEL:
            raise RuntimeConfigError("Jev model must match the configured version pin")
        if self.timezone != "America/New_York":
            raise RuntimeConfigError("Budget timezone must be America/New_York")
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
        if not budget.is_finite() or not 0 < budget <= DAILY_LIMIT_USD:
            raise RuntimeConfigError("Daily budget must be positive and at most one dollar")
        if budget * 1_000_000_000 != (budget * 1_000_000_000).to_integral_value():
            raise RuntimeConfigError("Daily budget has unsupported precision")
        object.__setattr__(self, "daily_budget_usd", budget)
        if type(self.enabled) is not bool or type(self.pruning_enabled) is not bool:
            raise RuntimeConfigError("Runtime switches must be booleans")
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
            return cls(home=home)
        try:
            if path.stat().st_size > 64 * 1024:
                raise RuntimeConfigError("Runtime configuration is too large")
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise RuntimeConfigError("Unable to read runtime configuration") from None
        fields = {"endpoint", "model", "timeout_s", "max_request_bytes", "max_response_bytes",
                  "daily_budget_usd", "timezone", "workspace_roots", "enabled", "pruning_enabled"}
        if not isinstance(data, dict) or not set(data).issubset(fields | {"version"}):
            raise RuntimeConfigError("Runtime configuration contains unsupported fields")
        if type(data.get("version", 1)) is not int or data.get("version", 1) != 1:
            raise RuntimeConfigError("Unsupported runtime configuration version")
        data.pop("version", None)
        return cls(home=home, **data)

    def _public_config(self) -> Dict[str, Any]:
        return {
            "version": 1, "endpoint": self.endpoint, "model": self.model,
            "timeout_s": self.timeout_s, "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "daily_budget_usd": str(self.daily_budget_usd), "timezone": self.timezone,
            "workspace_roots": [str(root) for root in self.workspace_roots],
            "enabled": self.enabled, "pruning_enabled": self.pruning_enabled,
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
