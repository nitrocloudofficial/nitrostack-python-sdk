from typing import Any


class ToolExecutionError(Exception):
    """Raised when tool execution fails."""
    pass

class ValidationError(Exception):
    """Raised when inputs or outputs fail schema validation."""
    pass

class RateLimitExceededError(ValueError):
    """Raised when a @rate_limit bucket is exhausted."""

    def __init__(self, message: str, retry_after_seconds: float):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds

class ResourceNotFoundError(Exception):
    """Raised when a requested resource is not found."""
    pass

class PromptNotFoundError(Exception):
    """Raised when a requested prompt template is not found."""
    pass

class DIError(Exception):
    """Base class for dependency injection errors."""
    pass

class DependencyResolutionError(DIError):
    """Raised when a dependency cannot be resolved by the DI container."""
    pass

class ConfigurationError(Exception):
    """Raised when configuration validation fails."""
    pass


class OAuthError(Exception):
    """Base class for OAuth / token-validation failures."""


class TokenInactiveError(OAuthError):
    """Raised when introspection or JWT verification reports an inactive token."""

    def __init__(self, message: str = "OAuth token is inactive or revoked"):
        super().__init__(message)


class AudienceMismatchError(OAuthError):
    """Raised when a token audience / resource indicator does not match."""

    def __init__(self, expected: Any = None, actual: Any = None):
        self.expected = expected
        self.actual = actual
        if expected is not None:
            super().__init__(f"Token audience mismatch: expected {expected!r}, got {actual!r}")
        else:
            super().__init__("Token audience mismatch")


class TaskNotFoundError(Exception):
    """Raised when a task ID is not present in the TaskManager store."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"Task {task_id} not found")


class TaskAlreadyTerminalError(Exception):
    """Raised when an operation is attempted on a task that is already terminal."""

    def __init__(self, task_id: str, status: Any):
        self.task_id = task_id
        self.status = status
        status_value = getattr(status, "value", status)
        super().__init__(
            f"Cannot modify task {task_id}: already in terminal status '{status_value}'"
        )


class InvalidTaskTransitionError(Exception):
    """Raised when a requested task status transition is not allowed."""

    def __init__(self, from_status: Any, to_status: Any):
        self.from_status = from_status
        self.to_status = to_status
        from_value = getattr(from_status, "value", from_status)
        to_value = getattr(to_status, "value", to_status)
        super().__init__(
            f"Invalid task status transition: {from_value} → {to_value}"
        )


class TaskExpiredError(Exception):
    """Raised when a task has expired (TTL elapsed) and cannot be used further."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"Task {task_id} has expired")


class TaskCancelledError(Exception):
    """Raised when an MCP background task has been cancelled."""

    def __init__(self, task_id: str | None = None, message: str | None = None):
        self.task_id = task_id
        if message is not None:
            super().__init__(message)
        elif task_id is not None:
            super().__init__(f"Task {task_id} has been cancelled.")
        else:
            super().__init__("Task has been cancelled.")
