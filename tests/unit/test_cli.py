from collections.abc import Coroutine
from typing import Any

import pytest

from mofiagent import cli
from mofiagent.config import Settings


def test_main_dispatches_server(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_log(level: str) -> None:
        captured["level"] = level

    def fake_run(app: str, **kwargs: Any) -> None:
        assert app == "mofiagent.api.app:app"
        captured.update(kwargs)

    monkeypatch.setattr(cli, "get_settings", lambda: Settings())
    monkeypatch.setattr(cli, "configure_logging", fake_log)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main(["serve"])

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8080
    assert captured["workers"] == 1


@pytest.mark.parametrize("command", ["migrate", "ingest"])
def test_main_dispatches_async_commands(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[Coroutine[Any, Any, Any]] = []

    def close_coroutine(coroutine: Coroutine[Any, Any, Any]) -> None:
        called.append(coroutine)
        coroutine.close()

    def fake_log(level: str) -> None:
        assert level == "INFO"

    monkeypatch.setattr(cli, "get_settings", lambda: Settings())
    monkeypatch.setattr(cli, "configure_logging", fake_log)
    monkeypatch.setattr(cli.asyncio, "run", close_coroutine)

    cli.main([command])

    assert len(called) == 1


def test_parser_rejects_missing_command() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])
