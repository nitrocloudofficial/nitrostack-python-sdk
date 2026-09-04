"""OAuth 2.1 security helpers (Doc 08)."""

from __future__ import annotations


class AuthorizationIssuerMismatchError(ValueError):
    """Raised when RFC 9207 ``iss`` does not match the expected authorization server."""


def validate_authorization_iss(response_iss: str | None, expected_issuer: str) -> None:
    """
    Validate RFC 9207 ``iss`` anti-mixup parameter (Doc 08 §5.1).

    Authorization responses MUST include ``iss`` and it MUST match the intended
    authorization server's issuer identifier.
    """
    if not expected_issuer or not expected_issuer.strip():
        raise ValueError("expected_issuer is required")

    if not response_iss or not str(response_iss).strip():
        raise AuthorizationIssuerMismatchError("Authorization response missing required iss parameter")

    normalized_expected = expected_issuer.rstrip("/")
    normalized_actual = str(response_iss).strip().rstrip("/")
    if normalized_actual != normalized_expected:
        raise AuthorizationIssuerMismatchError(
            f"Authorization iss mismatch: expected '{normalized_expected}', got '{normalized_actual}'"
        )
