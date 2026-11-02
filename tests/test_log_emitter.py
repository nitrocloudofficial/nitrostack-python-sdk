from __future__ import annotations

import logging
import io
import threading

from nitrostack.core.context import FileLogger
from nitrostack.core.log_emitter import LogEmitter, LogEmitterHandler, log_emitter


def _wait_for(event: threading.Event) -> None:
    assert event.wait(2), "timed out waiting for log subscriber"


def test_nitrostack_warning_reaches_last_resort_stderr_without_subscribers():
    logger = logging.Logger("nitrostack.test.last-resort")
    # Detach from pytest's logging capture handlers so Python's fallback is
    # the only possible destination, as it is before any NitroStack handlers
    # or subscriptions have been installed.
    logger.parent = None
    old_last_resort = logging.lastResort
    fallback_stream = io.StringIO()
    fallback = logging.StreamHandler(fallback_stream)
    try:
        fallback.setLevel(logging.WARNING)
        logging.lastResort = fallback
        logger.setLevel(logging.WARNING)
        logger.propagate = True
        logger.warning("warning before subscription")
        assert not any(getattr(handler, "_nitrostack_log_emitter", False) for handler in logger.handlers)
        assert "warning before subscription" in fallback_stream.getvalue()
    finally:
        logging.lastResort = old_last_resort
        fallback.close()


def test_global_emitter_receives_nitrostack_records_and_extra_fields():
    received = []
    done = threading.Event()

    def collect(record):
        received.append(record)
        done.set()

    unsubscribe = log_emitter.subscribe(collect)
    try:
        logging.getLogger("nitrostack.test.emitter").warning(
            "hello %s", "tooling", extra={"request_id": "r-1"}
        )
        _wait_for(done)
    finally:
        unsubscribe()

    record = received[0]
    assert record.level == "WARNING"
    assert record.message == "hello tooling"
    assert record.logger_name == "nitrostack.test.emitter"
    assert record.extra == {"request_id": "r-1"}
    assert record.timestamp.endswith("+00:00")


def test_unsubscribe_stops_future_delivery():
    emitter = LogEmitter()
    received = []
    ready = threading.Event()
    unsubscribe = emitter.subscribe(lambda record: (received.append(record), ready.set()))
    logger = logging.getLogger("nitrostack.test.unsubscribe")
    handler = LogEmitterHandler(emitter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("before")
        _wait_for(ready)
        unsubscribe()
        logger.info("after")
        assert len(received) == 1
    finally:
        logger.removeHandler(handler)


def test_unsubscribe_waits_for_in_flight_callback():
    emitter = LogEmitter()
    entered = threading.Event()
    release = threading.Event()
    drained = threading.Event()
    unsubscribe_returned = threading.Event()
    received = []

    def callback(record):
        received.append(record)
        entered.set()
        release.wait(2)

    unsubscribe = emitter.subscribe(callback)
    logger = logging.getLogger("nitrostack.test.unsubscribe-in-flight")
    handler = LogEmitterHandler(emitter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    worker = threading.Thread(target=lambda: logger.info("in flight"))
    try:
        worker.start()
        _wait_for(entered)

        def stop_subscription():
            unsubscribe()
            unsubscribe_returned.set()

        stopper = threading.Thread(target=stop_subscription)
        stopper.start()
        assert not unsubscribe_returned.wait(0.05)
        release.set()
        _wait_for(unsubscribe_returned)
        stopper.join(2)
        logger.info("after unsubscribe")
        assert len(received) == 1
    finally:
        release.set()
        worker.join(2)
        logger.removeHandler(handler)


def test_file_logger_keeps_file_output_and_publishes_to_subscribers(tmp_path):
    received = []
    done = threading.Event()
    unsubscribe = log_emitter.subscribe(lambda record: (received.append(record), done.set()))
    logger = FileLogger(log_file=str(tmp_path / "sdk.log"), name="nitrostack.test.filelogger")
    try:
        logger.info("through FileLogger", meta={"source": "test"})
        _wait_for(done)
        assert "through FileLogger" in (tmp_path / "sdk.log").read_text(encoding="utf-8")
        assert received[0].logger_name == "nitrostack.test.filelogger"
        assert received[0].extra == {}
        assert "meta: {'source': 'test'}" in received[0].message
    finally:
        unsubscribe()
        for handler in list(logger.logger.handlers):
            logger.logger.removeHandler(handler)
            handler.close()


def test_http_logger_with_propagation_disabled_reaches_subscribers():
    from nitrostack.transports.http import _enable_trace_logging, logger as http_logger

    received = []
    done = threading.Event()
    unsubscribe = log_emitter.subscribe(lambda record: (received.append(record), done.set()))
    old_level = http_logger.level
    old_propagate = http_logger.propagate
    old_trace_handlers = {
        handler
        for handler in http_logger.handlers
        if getattr(handler, "_nitrostack_trace", False)
    }
    try:
        _enable_trace_logging()
        http_logger.info("isolated HTTP logger")
        _wait_for(done)
        assert received[0].logger_name == "nitrostack.transports.http"
        assert received[0].message == "isolated HTTP logger"
    finally:
        unsubscribe()
        http_logger.setLevel(old_level)
        http_logger.propagate = old_propagate
        for handler in list(http_logger.handlers):
            if (
                getattr(handler, "_nitrostack_trace", False)
                and handler not in old_trace_handlers
            ):
                http_logger.removeHandler(handler)
                handler.close()


def test_http_logger_enabled_before_subscribe_reaches_subscribers():
    from nitrostack.transports.http import _enable_trace_logging, logger as http_logger

    received = []
    done = threading.Event()
    old_level = http_logger.level
    old_propagate = http_logger.propagate
    old_trace_handlers = {
        handler
        for handler in http_logger.handlers
        if getattr(handler, "_nitrostack_trace", False)
    }
    # Server start order: trace logging is enabled before tooling subscribes.
    _enable_trace_logging()
    unsubscribe = log_emitter.subscribe(lambda record: (received.append(record), done.set()))
    try:
        http_logger.info("HTTP record after late subscribe")
        _wait_for(done)
        assert received[0].logger_name == "nitrostack.transports.http"
        assert received[0].message == "HTTP record after late subscribe"
    finally:
        unsubscribe()
        assert not any(
            getattr(handler, "_nitrostack_log_emitter", False)
            for handler in http_logger.handlers
        )
        http_logger.setLevel(old_level)
        http_logger.propagate = old_propagate
        for handler in list(http_logger.handlers):
            if (
                getattr(handler, "_nitrostack_trace", False)
                and handler not in old_trace_handlers
            ):
                http_logger.removeHandler(handler)
                handler.close()


def test_raising_subscriber_does_not_prevent_another_subscriber():
    emitter = LogEmitter()
    received = []
    done = threading.Event()
    unsubscribe_raising = emitter.subscribe(
        lambda record: (_ for _ in ()).throw(RuntimeError("subscriber failure"))
    )
    unsubscribe_healthy = emitter.subscribe(lambda record: (received.append(record), done.set()))
    logger = logging.getLogger("nitrostack.test.failure-isolation")
    handler = LogEmitterHandler(emitter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("still delivered")
        _wait_for(done)
        assert received[0].message == "still delivered"
    finally:
        logger.removeHandler(handler)
        unsubscribe_raising()
        unsubscribe_healthy()


def test_slow_subscriber_has_bounded_buffer_and_keeps_recent_records():
    emitter = LogEmitter(max_buffer=2)
    entered = threading.Event()
    release = threading.Event()
    drained = threading.Event()
    received = []

    def slow(record):
        received.append(record.message)
        if record.message == "0":
            entered.set()
            release.wait(2)
        if record.message == "5":
            drained.set()

    unsubscribe_slow = emitter.subscribe(slow)
    logger = logging.getLogger("nitrostack.test.bounded")
    handler = LogEmitterHandler(emitter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("0")
        _wait_for(entered)
        for index in range(1, 5):
            logger.info("%s", index)
        finished = threading.Event()

        def last_seen(record):
            if record.message == "5":
                finished.set()

        # A second subscriber provides an asynchronous completion signal.
        unsubscribe = emitter.subscribe(last_seen)
        logger.info("5")
        _wait_for(finished)
        release.set()
        _wait_for(drained)
        unsubscribe()
        assert received[0] == "0"
        assert received[-2:] == ["4", "5"]
    finally:
        release.set()
        logger.removeHandler(handler)
        unsubscribe_slow()
