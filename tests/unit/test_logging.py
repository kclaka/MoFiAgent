import json
import logging

from mofiagent.telemetry.logging import JsonFormatter, configure_logging


def test_json_formatter_includes_structured_context_and_exception() -> None:
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="mofiagent.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=10,
            msg="request failed",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )
    record.request_id = "abc123"

    payload = json.loads(formatter.format(record))

    assert payload["severity"] == "ERROR"
    assert payload["request_id"] == "abc123"
    assert "ValueError: boom" in payload["exception"]


def test_configure_logging_replaces_root_handlers() -> None:
    root = logging.getLogger()
    original_handlers = root.handlers.copy()
    original_level = root.level
    try:
        configure_logging("WARNING")
        assert root.level == logging.WARNING
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers[:] = original_handlers
        root.setLevel(original_level)
