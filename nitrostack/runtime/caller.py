"""One place that decides who is calling.

Transforms, task access and guards must agree on the caller's identity, so the
rules live here instead of being re-derived per feature.

* ``subject`` is the ``sub`` of a verified bearer token. Unsigned ``userId``
  fields and ``_meta`` claims are never identity.
* ``session_id`` only means something on the sessionful (``legacy``) wire.
  Sessionless eras mint no durable session, so none is reported there.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from nitrostack.auth.request import authorization_token_from_request, verify_bearer_payload
from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.version import wire_mode_for_era


def session_id_from_request_context(rc: Any) -> Optional[str]:
    direct = getattr(rc, "session_id", None)
    if direct:
        return str(direct)
    session = getattr(rc, "session", None)
    session_id = getattr(session, "id", None) or getattr(session, "session_id", None)
    if session_id:
        return str(session_id)
    # The session manager only routes a request whose Mcp-Session-Id names a
    # live session it issued, so on the sessionful wire the header is the id.
    headers = getattr(rc, "headers", None) or getattr(getattr(rc, "request", None), "headers", None)
    if headers is not None:
        try:
            header = headers.get(LEGACY_SESSION_HEADER) or headers.get(LEGACY_SESSION_HEADER.lower())
        except Exception:
            header = None
        if header:
            return str(header)
    return None


def verified_claims(rc: Any) -> Optional[dict]:
    """Claims of the verified bearer token, ``None`` if absent or invalid."""
    if rc is None:
        return None
    token = authorization_token_from_request(rc)
    if not token:
        return None
    return verify_bearer_payload(token)


def caller_identity(rc: Any, era: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(session_id, subject)`` for the request in ``rc``."""
    if rc is None:
        return None, None
    session_id = session_id_from_request_context(rc)
    if era is not None and wire_mode_for_era(era) != "sessionful":
        session_id = None
    claims = verified_claims(rc)
    subject = claims.get("sub") if claims else None
    if not (isinstance(subject, str) and subject.strip()):
        subject = None
    return session_id, subject
