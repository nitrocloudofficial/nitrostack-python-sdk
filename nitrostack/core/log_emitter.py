"""Process-wide bridge from Python logging to tooling subscribers."""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


DEFAULT_BUFFER_SIZE = 256
_STANDARD_RECORD_FIELDS = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
) | {"message", "asctime"}


@dataclass(frozen=True)
class LogRecord:
    """A structured logging record delivered to tooling subscribers."""

    level: str
    levelno: int
    message: str
    timestamp: str
    logger_name: str
    extra: dict[str, Any]


class _Subscriber:
    def __init__(self, callback: Callable[[LogRecord], Any], max_buffer: int):
        self.callback = callback
        self.queue: queue.Queue[LogRecord | None] = queue.Queue(maxsize=max_buffer)
        self.active = True
        self.condition = threading.Condition()
        self.callback_thread: int | None = None
        self.thread = threading.Thread(target=self._deliver, daemon=True, name="nitrostack-log-subscriber")
        self.thread.start()

    def enqueue(self, record: LogRecord) -> None:
        with self.condition:
            if not self.active:
                return
            try:
                self.queue.put_nowait(record)
            except queue.Full:
                # Preserve the newest logs when a subscriber cannot keep up.
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.queue.put_nowait(record)
                except queue.Full:
                    pass

    def unsubscribe(self) -> None:
        with self.condition:
            if not self.active:
                return
            self.active = False
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            self.queue.put_nowait(None)
            if self.callback_thread != threading.get_ident():
                while self.callback_thread is not None:
                    self.condition.wait()

    def _deliver(self) -> None:
        while True:
            record = self.queue.get()
            if record is None:
                return
            with self.condition:
                if not self.active:
                    continue
                self.callback_thread = threading.get_ident()
            try:
                self.callback(record)
            except Exception:
                # Subscriber code must not interfere with logging or peers.
                continue
            finally:
                with self.condition:
                    self.callback_thread = None
                    self.condition.notify_all()


class LogEmitter:
    """Thread-safe subscription point for records from ``nitrostack`` loggers."""

    def __init__(self, max_buffer: int = DEFAULT_BUFFER_SIZE):
        if max_buffer < 1:
            raise ValueError("max_buffer must be at least 1")
        self._max_buffer = max_buffer
        self._subscribers: set[_Subscriber] = set()
        self._lock = threading.Lock()
        self._installed_handlers: dict[str, logging.Handler] = {}
        # Loggers to bridge while subscribers exist. A logger with
        # ``propagate=False`` never reaches ``nitrostack``, so it needs its own.
        self._bridged_loggers: set[str] = {"nitrostack"}

    def subscribe(self, callback: Callable[[LogRecord], Any]) -> Callable[[], None]:
        """Subscribe to records; return an idempotent function to stop delivery."""
        subscriber = _Subscriber(callback, self._max_buffer)
        with self._lock:
            self._subscribers.add(subscriber)
            # Only the process-wide emitter owns NitroStack logger wiring.
            # Standalone emitters are connected explicitly with
            # LogEmitterHandler, avoiding duplicate delivery from the shared
            # ``nitrostack`` logger hierarchy.
            if self is log_emitter:
                self._install_handlers_locked()

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.discard(subscriber)
                if not self._subscribers:
                    self._remove_handlers_locked()
            subscriber.unsubscribe()

        return unsubscribe

    def _publish(self, record: LogRecord) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            subscriber.enqueue(record)

    def _install_handlers_locked(self) -> None:
        for logger_name in self._bridged_loggers:
            self._install_handler_locked(logger_name)

    def _install_handler_locked(self, logger_name: str) -> None:
        if logger_name in self._installed_handlers:
            return
        logger = logging.getLogger(logger_name)
        handler = LogEmitterHandler(self)
        handler._nitrostack_log_emitter = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
        self._installed_handlers[logger_name] = handler

    def _remove_handlers_locked(self) -> None:
        for name, handler in self._installed_handlers.items():
            logging.getLogger(name).removeHandler(handler)
            handler.close()
        self._installed_handlers.clear()


class LogEmitterHandler(logging.Handler):
    """Logging handler that publishes structured records to a ``LogEmitter``."""

    def __init__(self, emitter: LogEmitter):
        super().__init__(logging.NOTSET)
        self.emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat()
            extra = {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_")
            }
            self.emitter._publish(
                LogRecord(
                    level=record.levelname,
                    levelno=record.levelno,
                    message=record.getMessage(),
                    timestamp=timestamp,
                    logger_name=record.name,
                    extra=extra,
                )
            )
        except Exception:
            self.handleError(record)


log_emitter = LogEmitter()


def install_log_handler(logger_name: str = "nitrostack") -> None:
    """Bridge a logger to the global emitter whenever it has subscribers.

    The name is remembered, so a later ``subscribe()`` still attaches to it.
    """
    with log_emitter._lock:
        log_emitter._bridged_loggers.add(logger_name)
        if log_emitter._subscribers:
            log_emitter._install_handler_locked(logger_name)
