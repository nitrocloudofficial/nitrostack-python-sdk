"""``SecretValue``: a wrapper that keeps a secret out of logs, ``repr``, tracebacks,
pickles and serialized output.

The raw value is only reachable through :meth:`SecretValue.unwrap`. Everything else
that could print or serialize the object shows ``[SecretValue: REDACTED]`` instead.

>>> s = SecretValue.from_value("a-long-test-secret-value", allow_hardcoded=True)
>>> f"{s}", repr(s)
('[SecretValue: REDACTED]', '[SecretValue: REDACTED]')
"""

from __future__ import annotations

import hmac
import os
import secrets as _secrets
import warnings
from typing import Any, Optional, Union

REDACTED = "[SecretValue: REDACTED]"

# Secrets shorter than this still work, but trigger a warning.
MIN_RECOMMENDED_LENGTH = 16

# Per-process key so hash() never exposes anything derived from the value alone.
_HASH_KEY = _secrets.token_bytes(32)


def _redacted_after_pickle(source: str) -> "SecretValue":
    """Rebuild a ``SecretValue`` that carries no value (used when unpickling)."""
    return SecretValue._empty(source)


class SecretValue:
    """Holds a secret string and refuses to reveal it by accident."""

    __slots__ = ("_value", "source")

    def __init__(self, value: str, source: str = "env") -> None:
        if not isinstance(value, str):
            raise TypeError("SecretValue needs a str value")
        if not value:
            raise ValueError("Secret value cannot be empty")
        if len(value) < MIN_RECOMMENDED_LENGTH:
            warnings.warn(
                f"[SecretValue] Secret is shorter than {MIN_RECOMMENDED_LENGTH} characters. "
                "Consider using a longer secret.",
                UserWarning,
                stacklevel=2,
            )
        self._value: Optional[str] = value
        self.source = source

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def _empty(cls, source: str) -> "SecretValue":
        """A secret that was redacted on its way through pickle. It has no value."""
        obj = cls.__new__(cls)
        obj._value = None
        obj.source = source
        return obj

    @classmethod
    def from_env(cls, env_var_name: str) -> "SecretValue":
        """Read a required secret from an environment variable."""
        value = os.environ.get(env_var_name)
        if not value:
            raise ValueError(
                f"Environment variable {env_var_name} is not set. "
                "Set it in your environment or .env file."
            )
        return cls(value, "env")

    @classmethod
    def from_env_optional(cls, env_var_name: str) -> Optional["SecretValue"]:
        """Read a secret from an environment variable, or ``None`` when it is not set."""
        value = os.environ.get(env_var_name)
        if not value:
            return None
        return cls(value, "env")

    @classmethod
    def from_value(
        cls,
        value: str,
        allow_hardcoded: bool = False,
        reason: Optional[str] = None,
    ) -> "SecretValue":
        """Wrap a value that is written in code. This must be allowed explicitly."""
        if not allow_hardcoded:
            raise ValueError(
                "Hardcoded secrets are not allowed. Use SecretValue.from_env() to load "
                "secrets from environment variables. If you must use a hardcoded value "
                "(for example in a test), pass allow_hardcoded=True."
            )
        warnings.warn(
            "[SecretValue] Using a hardcoded secret value. "
            f"Reason: {reason or 'not specified'}. Do not do this in production.",
            UserWarning,
            stacklevel=2,
        )
        return cls(value, "explicit")

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def unwrap(self) -> str:
        """Return the raw secret. Call this only where the value is really needed."""
        if self._value is None:
            raise ValueError(
                "This SecretValue was redacted during serialization and has no value."
            )
        return self._value

    def is_from_environment(self) -> bool:
        return self.source == "env"

    # ------------------------------------------------------------------
    # Redaction: str, repr, format, f-strings, logging, tracebacks
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        return REDACTED

    def __repr__(self) -> str:
        return REDACTED

    def __format__(self, format_spec: str) -> str:
        return format(REDACTED, format_spec)

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretValue):
            other_value = other._value
        elif isinstance(other, str):
            other_value = other
        else:
            return NotImplemented
        if self._value is None or other_value is None:
            return False
        return hmac.compare_digest(self._value.encode("utf-8"), other_value.encode("utf-8"))

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self) -> int:
        if self._value is None:
            return hash(("SecretValue", "redacted"))
        return hash(hmac.digest(_HASH_KEY, self._value.encode("utf-8"), "sha256"))

    def __bool__(self) -> bool:
        return self._value is not None

    # ------------------------------------------------------------------
    # Pickle and copy
    # ------------------------------------------------------------------

    def __reduce__(self):
        # The pickle holds only the source. Loading it gives a SecretValue with no
        # value, so a secret can neither leak through a pickle nor be silently
        # replaced by a placeholder string.
        return (_redacted_after_pickle, (self.source,))

    def __copy__(self) -> "SecretValue":
        return self  # immutable, so sharing is safe

    def __deepcopy__(self, memo: Any) -> "SecretValue":
        return self

    # ------------------------------------------------------------------
    # Pydantic v2: validation and JSON/dict serialization
    # ------------------------------------------------------------------

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any):
        from pydantic_core import core_schema

        def validate(value: Any) -> "SecretValue":
            if isinstance(value, SecretValue):
                return value
            if isinstance(value, str):
                return cls(value, "explicit")
            raise ValueError("expected a SecretValue or a string")

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda _value: REDACTED,
                return_schema=core_schema.str_schema(),
                when_used="always",
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema_: Any, handler: Any):
        return {"type": "string", "format": "password", "writeOnly": True}


def is_secret_value(value: Any) -> bool:
    """True when ``value`` is a ``SecretValue``."""
    return isinstance(value, SecretValue)


def unwrap_secret(secret: Union[SecretValue, str]) -> str:
    """Return the raw string for a ``SecretValue`` or a plain ``str``.

    Plain strings are accepted so existing code keeps working.
    """
    if isinstance(secret, SecretValue):
        return secret.unwrap()
    if isinstance(secret, str):
        return secret
    raise TypeError(f"Expected a SecretValue or str, got {type(secret).__name__}")
