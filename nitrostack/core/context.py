import logging
import os
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, List, Dict, Optional

from nitrostack.core.errors import TaskCancelledError

if TYPE_CHECKING:
    from nitrostack.protocol.meta import RequestMeta
    from nitrostack.protocol.observability import TraceContext

# Logger protocol used by ExecutionContext (Section 13)
class Logger(Protocol):
    def debug(self, message: str, meta: dict | None = None) -> None: ...
    def info(self, message: str, meta: dict | None = None) -> None: ...
    def warn(self, message: str, meta: dict | None = None) -> None: ...
    def error(self, message: str, meta: dict | None = None) -> None: ...

class FileLogger:
    """
    Logger implementation that writes to a file or stdout/stderr based on transport settings.
    This prevents corrupting the MCP stdio JSON-RPC transport.
    """
    def __init__(self, log_file: Optional[str] = None, name: str = "nitrostack"):
        self.logger = logging.getLogger(name)
        
        # Read log level from environment
        level_str = (
            os.environ.get("NITROSTACK_LOG_LEVEL")
            or os.environ.get("NITRO_LOG_LEVEL")
            or "DEBUG"
        ).upper()
        level = getattr(logging, level_str, logging.DEBUG)
        self.logger.setLevel(level)
        
        # Avoid adding duplicate handlers if initialized multiple times
        if not self.logger.handlers:
            formatter = logging.Formatter(
                '%(asctime)s [%(levelname)s] (%(name)s): %(message)s'
            )
            
            # Check if stdout logging is explicitly requested or safe (e.g. HTTP transport)
            log_to_stdout = (
                os.environ.get("NITROSTACK_LOG_TO_STDOUT", "false").lower() == "true"
                or os.environ.get("MCP_TRANSPORT_TYPE") == "http"
            )
            
            if log_to_stdout:
                sh = logging.StreamHandler(sys.stdout)
                sh.setLevel(level)
                sh.setFormatter(formatter)
                self.logger.addHandler(sh)
            else:
                # Determine log file path
                target_file = log_file or os.environ.get("NITROSTACK_LOG_FILE", "nitrostack.log")
                try:
                    fh = logging.FileHandler(target_file, encoding='utf-8')
                    fh.setLevel(level)
                    fh.setFormatter(formatter)
                    self.logger.addHandler(fh)
                except Exception:
                    # Fallback to sys.stderr to avoid stdout pollution in stdio transport
                    sh = logging.StreamHandler(sys.stderr)
                    sh.setLevel(level)
                    sh.setFormatter(formatter)
                    self.logger.addHandler(sh)
                
    def _format_message(self, message: str, meta: dict | None = None) -> str:
        if meta:
            return f"{message} | meta: {meta}"
        return message

    def debug(self, message: str, meta: dict | None = None) -> None:
        self.logger.debug(self._format_message(message, meta))

    def info(self, message: str, meta: dict | None = None) -> None:
        self.logger.info(self._format_message(message, meta))

    def warn(self, message: str, meta: dict | None = None) -> None:
        self.logger.warning(self._format_message(message, meta))

    def error(self, message: str, meta: dict | None = None) -> None:
        self.logger.error(self._format_message(message, meta))

@dataclass
class AuthContext:
    subject: str | None = None       # user/client identifier
    scopes: List[str] = field(default_factory=list)  # granted permissions
    client_id: str | None = None     # machine-to-machine
    exp: int | None = None           # expiration timestamp
    iat: int | None = None           # issued-at timestamp
    iss: str | None = None           # issuer URL
    aud: List[str] | None = None     # audience(s) this token was issued for (RFC 8707)
    claims: Dict[str, Any] = field(default_factory=dict)  # custom claims
    token_payload: Any = None        # full decoded token

class TaskContext:
    """
    Context representation for long-running asynchronous MCP tasks.

    Public methods (``update_progress``, ``cancel``, ``throw_if_cancelled``) are
    preserved for tool authors. Internally this wraps a ``TaskManager`` instance.

    ``session`` / ``progress_token`` are optional and, when both are present, let
    ``update_progress()`` push a live ``notifications/progress`` message over
    whichever transport (STDIO or Streamable HTTP) initiated the task —
    transport-agnostic since both use the same ``mcp.server.session.ServerSession``.
    The client only receives these if it supplied a ``progressToken`` in the
    original ``tools/call`` request's ``_meta``; ``TaskManager``-backed polling via
    ``tasks/get`` always works regardless, so this is additive, not required.
    """

    def __init__(
        self,
        task_id: str,
        task_manager: Any = None,
        *,
        session: Any = None,
        progress_token: Any = None,
        correlation_id: Any = None,
    ):
        self.task_id = task_id
        self.progress_message: str = ""
        self.is_cancelled: bool = False
        self._task_manager = task_manager
        self._session = session
        self._progress_token = progress_token
        self._correlation_id = correlation_id
        self._progress_count = 0

    def update_progress(self, message: str) -> None:
        self.progress_message = message
        manager = self._task_manager
        if manager is not None:
            updater = getattr(manager, "update_progress_sync", None)
            if updater is None:
                raise RuntimeError("Task manager does not support synchronous progress updates")
            updater(self.task_id, message)
        self._push_progress_notification(message)

    def _push_progress_notification(self, message: str) -> None:
        if self._session is None or self._progress_token is None:
            return
        self._progress_count += 1
        try:
            import asyncio
            asyncio.create_task(
                self._session.send_progress_notification(
                    progress_token=self._progress_token,
                    progress=self._progress_count,
                    message=message,
                    related_request_id=self._correlation_id,
                )
            )
        except Exception:
            # Best-effort: a client that didn't request progress updates, a
            # transport that already closed, or no running event loop should
            # never break task execution itself.
            pass

    def cancel(self) -> None:
        self.is_cancelled = True
        manager = self._task_manager
        if manager is None:
            return
        canceller = getattr(manager, "cancel_task_sync", None)
        if canceller is None:
            raise RuntimeError("Task manager does not support synchronous cancel")
        canceller(self.task_id)

    def throw_if_cancelled(self) -> None:
        manager = self._task_manager
        if manager is not None:
            try:
                if manager.is_task_cancelled_sync(self.task_id):
                    self.is_cancelled = True
            except Exception:
                pass
        if self.is_cancelled:
            raise TaskCancelledError(self.task_id)

@dataclass
class ExecutionContext:
    request_id: str
    correlation_id: str | None = None
    jsonrpc_id: Any = None
    tool_name: str | None = None
    logger: Logger = field(default_factory=lambda: FileLogger())
    metadata: dict = field(default_factory=dict)
    auth: AuthContext | None = None
    task: TaskContext | None = None
    input_responses: Dict[str, Any] = field(default_factory=dict)
    request_state: Optional[Dict[str, Any]] = None
    trace: "TraceContext | None" = None
    protocol_version: Optional[str] = None
    rpc_meta: Optional["RequestMeta"] = None
    mcp_headers: Dict[str, str] = field(default_factory=dict)
    mcp_param_headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.correlation_id:
            self.correlation_id = self.request_id

    @property
    def user(self) -> Optional[str]:
        """Verified identity only. Unsigned ``_meta.userId`` is never used."""
        if self.auth is None:
            return None
        return self.auth.subject
