"""Opt-out PostHog telemetry for the NitroStack Python CLI (`nitrostack-py`).

Non-blocking: events are queued by the PostHog SDK; call :func:`shutdown_telemetry`
at process exit (short flush timeout). Never raises into user command paths.
"""

from __future__ import annotations

import atexit
import hashlib
import logging
import os
import platform
import socket
import sys
import threading
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

PRODUCT = "python_sdk"
SURFACE = "terminal"

# Public PostHog project key (same CLI/SDK ingest as the TypeScript CLI).
# Override with NITROSTACK_POSTHOG_KEY.
DEFAULT_POSTHOG_PROJECT_KEY = "phc_" + "OufG1OuiamSbCBMVHfO70IyFWKBzsiaDpOWqcNwtz6G"
DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

_POSTHOG_LOCK = threading.Lock()
_client: Any = None
_posthog_unavailable = False
_anonymous_distinct_id: Optional[str] = None
_distinct_id_override: Optional[str] = None
_shutdown_done = False


def _env_truthy(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in _TRUE


def _env_falsy(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in _FALSE


def resolved_posthog_api_key() -> str:
    """PostHog project API key: ``NITROSTACK_POSTHOG_KEY`` if set, else built-in default."""
    env = (os.environ.get("NITROSTACK_POSTHOG_KEY") or "").strip()
    if env:
        return env
    return DEFAULT_POSTHOG_PROJECT_KEY


def resolved_posthog_host() -> str:
    """PostHog ingest host (default US Cloud)."""
    return (os.environ.get("NITROSTACK_POSTHOG_HOST") or DEFAULT_POSTHOG_HOST).strip()


def is_telemetry_disabled() -> bool:
    """True when capture must not run (opt-out, tests, CI, or missing configuration)."""
    if _env_truthy("DO_NOT_TRACK") or _env_truthy("PYTEST_CURRENT_TEST"):
        return True
    if _env_falsy("NITROSTACK_TELEMETRY"):
        return True
    if (os.environ.get("NITROSTACK_TELEMETRY") or "").strip() == "":
        if _env_truthy("CI") or os.environ.get("GITHUB_ACTIONS"):
            return True
    return False


def _sdk_version() -> str:
    try:
        return metadata.version("nitrostack")
    except metadata.PackageNotFoundError:
        return "unknown"


def _runtime_environment() -> str:
    explicit = (os.environ.get("NITROSTACK_ENVIRONMENT") or "").strip().lower()
    if explicit in ("production", "staging", "development"):
        return explicit
    node_env = (os.environ.get("NODE_ENV") or "").lower()
    if _env_truthy("NITROSTACK_PRODUCTION") or node_env == "production":
        return "production"
    if (os.environ.get("ENVIRONMENT") or "").strip().lower() == "staging":
        return "staging"
    return "development"


def _anonymous_id_from_machine() -> str:
    global _anonymous_distinct_id
    if _anonymous_distinct_id is not None:
        return _anonymous_distinct_id
    try:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
        raw = f"{socket.gethostname()}:{user}".encode("utf-8", errors="replace")
        _anonymous_distinct_id = hashlib.sha256(raw).hexdigest()[:16]
    except Exception:
        _anonymous_distinct_id = "anonymous"
    return _anonymous_distinct_id


def get_distinct_id() -> str:
    """Current analytics distinct id (Cloud user id after login, else anonymous hash)."""
    if _distinct_id_override:
        return _distinct_id_override
    return _anonymous_id_from_machine()


def _super_properties() -> Dict[str, Any]:
    return {
        "product": PRODUCT,
        "surface": SURFACE,
        "environment": _runtime_environment(),
        "app_version": _sdk_version(),
        "python_version": platform.python_version(),
        "sdk_version": _sdk_version(),
        "os_platform": sys.platform,
        "os_arch": platform.machine() or "unknown",
    }


def _get_client() -> Any:
    global _client, _posthog_unavailable
    if is_telemetry_disabled():
        return None
    if _posthog_unavailable:
        return None
    with _POSTHOG_LOCK:
        if _client is None:
            try:
                from posthog import Posthog
            except ImportError:
                _posthog_unavailable = True
                logger.debug("nitrostack telemetry: posthog package not installed")
                return None

            api_key = resolved_posthog_api_key()
            host = resolved_posthog_host()
            _client = Posthog(
                api_key,
                host=host,
                flush_at=10,
                flush_interval=0.5,
            )
        return _client


def track_event(event: str, properties: Optional[Mapping[str, Any]] = None) -> None:
    """Fire-and-forget capture; swallowed on failure."""
    if is_telemetry_disabled():
        return
    client = _get_client()
    if client is None:
        return
    props: Dict[str, Any] = dict(_super_properties())
    if properties:
        props.update(dict(properties))
    try:
        client.capture(distinct_id=get_distinct_id(), event=event, properties=props)
    except Exception:
        logger.debug("nitrostack telemetry: capture failed", exc_info=True)


def track_command_invoked(command: str) -> None:
    track_event("cli_command_invoked", {"command": command})


def track_init_completed(
    *,
    template: str,
    skip_install: bool,
    duration_ms: int,
    has_custom_description: bool,
) -> None:
    track_event(
        "cli_init_completed",
        {
            "template": template,
            "skip_install": skip_install,
            "duration_ms": duration_ms,
            "has_custom_description": has_custom_description,
        },
    )


def track_deploy_completed() -> None:
    track_event("cli_deploy_completed", {})


def track_deploy_failed(*, error_code: str) -> None:
    track_event("cli_deploy_failed", {"error_code": error_code})


def identify_from_cloud_login(
    *,
    user_id: str,
    email: str,
    organization_id: Optional[str] = None,
) -> None:
    """Alias anonymous device id to Cloud user id, identify, optional org group, then journey event.

    Call when a future `login` flow succeeds, matching intended TS CLI behaviour (#9).
    """
    global _distinct_id_override
    if is_telemetry_disabled():
        return
    client = _get_client()
    if client is None:
        return
    anonymous = _anonymous_id_from_machine()
    uid = (user_id or "").strip()
    em = (email or "").strip()
    if not uid or not em:
        return
    props = _super_properties()
    props["user_email"] = em
    props["cloud_user_id"] = uid
    try:
        client.alias(previous_id=anonymous, distinct_id=uid)
        client.identify(distinct_id=uid, properties={"email": em})
        if organization_id:
            client.group_identify(
                "organization",
                organization_id,
                properties={},
                distinct_id=uid,
            )
        _distinct_id_override = uid
        client.capture(
            distinct_id=uid,
            event="cli_login_completed",
            properties={
                **props,
                "user_email": em,
                "cloud_user_id": uid,
            },
        )
    except Exception:
        logger.debug("nitrostack telemetry: identify/login failed", exc_info=True)


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "NitroStack"
    return Path.home() / ".config" / "nitrostack"


def _first_run_flag_path() -> Path:
    return _config_dir() / ".telemetry_first_run_notice_shown"


def maybe_print_first_run_notice() -> None:
    """One-line notice on first CLI invocation (when telemetry may send)."""
    if is_telemetry_disabled():
        return
    path = _first_run_flag_path()
    try:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1\n", encoding="utf-8")
    except OSError:
        return
    msg = (
        "NitroStack: anonymous usage telemetry is enabled. "
        "Disable with DO_NOT_TRACK=1 or NITROSTACK_TELEMETRY=0.\n"
    )
    try:
        sys.stderr.write(msg)
        sys.stderr.flush()
    except OSError:
        pass


def shutdown_telemetry(timeout: float = 2.0) -> None:
    """Flush queued events without blocking the user indefinitely."""
    global _client, _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True
    c = _client
    if c is None:
        return

    def _run() -> None:
        try:
            c.shutdown()
        except Exception:
            logger.debug("nitrostack telemetry: shutdown failed", exc_info=True)

    t = threading.Thread(target=_run, name="nitrostack-telemetry-shutdown", daemon=True)
    t.start()
    t.join(timeout=timeout)


def _atexit_shutdown() -> None:
    if is_telemetry_disabled():
        return
    shutdown_telemetry()


# Avoid duplicate handlers under pytest reloads; tests call shutdown explicitly.
if not os.environ.get("PYTEST_CURRENT_TEST"):
    atexit.register(_atexit_shutdown)
