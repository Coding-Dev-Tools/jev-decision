"""Small deterministic egress safeguards; model output is never authorization."""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

from .runtime import MAX_REQUEST_BYTES, OFFICIAL_ENDPOINT


class PolicyError(ValueError):
    """Rejected egress policy; messages contain no payload data."""


_SECRET_FIELD = re.compile(
    r"(?i)^(?:[a-z][a-z0-9]*[_-])*(?:typesafe_api_key|jev_api_key|api[_-]?key|api[_-]?token|secret|"
    r"password|passwd|passphrase|authorization|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"private[_-]?key|signing[_-]?key|aws_secret_access_key|aws_access_key_id|session[_-]?token|"
    r"_authToken|_auth)$"
)
_ASSIGNMENT = re.compile(
    r'''(?i)(["']?(?:typesafe_api_key|jev_api_key|api[_-]?key|api[_-]?token|secret|'''
    r'''password|passwd|passphrase|authorization|access[_-]?token|refresh[_-]?token|client[_-]?secret|'''
    r'''private[_-]?key|signing[_-]?key|aws_secret_access_key|aws_access_key_id|session[_-]?token|'''
    r'''_authToken|_auth)["']?\s*[:=]\s*)'''
    r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\[REDACTED\]|[^\s,;}\]]+)'''
)
_PEM = re.compile(r"-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY)-----.*?-----END (?:[A-Z0-9 ]*PRIVATE KEY)-----", re.S)
_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}|npm_[A-Za-z0-9]{12,}|apikey_[A-Za-z0-9_-]{16,})\b")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
# Any URI scheme can carry "user:password@" userinfo, so a database or message
# broker DSN discloses a credential exactly like an https URL does. The username
# may be empty ("redis://:password@host"), which still discloses the password.
_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]{1,31}://)[^\s/@]*:[^\s/@]+@")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_LINE_ENDINGS = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def validate_endpoint(url: str) -> str:
    if url != OFFICIAL_ENDPOINT:
        raise PolicyError("Only the exact official TypeSafe endpoint is allowed")
    return url


def enforce_request_size(payload: bytes, limit: int = MAX_REQUEST_BYTES) -> None:
    if not isinstance(payload, bytes):
        raise PolicyError("Serialized request must be bytes")
    if type(limit) is not int or not 0 < limit <= MAX_REQUEST_BYTES:
        raise PolicyError("Invalid request byte limit")
    if len(payload) > limit:
        raise PolicyError("Request exceeds the permitted byte limit")


def sanitize_excerpt(text: str, secrets: Sequence[str] = ()) -> str:
    """Redact known credentials and recognizable secret assignments in excerpts."""
    if not isinstance(text, str):
        raise PolicyError("Excerpt must be text")
    for secret in sorted((item for item in secrets if isinstance(item, str) and item), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = _PEM.sub("[REDACTED PRIVATE KEY]", text)
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _TOKEN.sub("[REDACTED]", text)
    text = _JWT.sub("[REDACTED]", text)
    return _ASSIGNMENT.sub(lambda match: match.group(1) + '"[REDACTED]"', text)


def sanitize_evidence(text: str, secrets: Sequence[str] = ()) -> str:
    """Redact evidence without changing any original line's numeric position.

Multiline secrets become a marker followed by the original newline sequence.
This preserves line identities, including CRLF and Unicode line separators;
columns inside a redacted span are intentionally not claimed to be unchanged.
"""
    if not isinstance(text, str):
        raise PolicyError("Excerpt must be text")

    def replacement(original: str, marker: str) -> str:
        return _LINE_ENDINGS.sub("", marker) + "".join(_LINE_ENDINGS.findall(original))

    for secret in sorted((item for item in secrets if isinstance(item, str) and item), key=len, reverse=True):
        text = text.replace(secret, replacement(secret, "[REDACTED]"))
    text = _PEM.sub(lambda match: replacement(match.group(), "[REDACTED PRIVATE KEY]"), text)
    text = _URL_USERINFO.sub(lambda match: replacement(match.group(), match.group(1) + "[REDACTED]@"), text)
    text = _BEARER.sub(lambda match: replacement(match.group(), "Bearer [REDACTED]"), text)
    text = _TOKEN.sub(lambda match: replacement(match.group(), "[REDACTED]"), text)
    text = _JWT.sub(lambda match: replacement(match.group(), "[REDACTED]"), text)
    return _ASSIGNMENT.sub(lambda match: replacement(match.group(), match.group(1) + '"[REDACTED]"'), text)


def sanitize_state(value: Any, secrets: Sequence[str] = (), _depth: int = 0) -> Any:
    """Copy JSON state with redaction; reject unsupported values and deep nesting."""
    if _depth > 32:
        raise PolicyError("State exceeds the permitted nesting depth")
    if isinstance(value, str):
        return sanitize_excerpt(value, secrets)
    if value is None or isinstance(value, bool) or type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PolicyError("State must contain finite JSON numbers")
        return value
    if isinstance(value, list):
        return [sanitize_state(item, secrets, _depth + 1) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PolicyError("State object keys must be strings")
            clean_key = sanitize_excerpt(key, secrets)
            if clean_key in result:
                raise PolicyError("Redacted state keys would be ambiguous")
            result[clean_key] = "[REDACTED]" if _SECRET_FIELD.fullmatch(key) else sanitize_state(item, secrets, _depth + 1)
        return result
    raise PolicyError("State must be a JSON value")


# The file-evidence interface uses the shorter spelling.
sanitize = sanitize_excerpt
