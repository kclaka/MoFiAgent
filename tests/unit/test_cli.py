from collections.abc import Coroutine
from datetime import date
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


def test_scheduled_ingestion_overlaps_years_during_early_january() -> None:
    assert cli.ingestion_targets(None, today=date(2027, 1, 1)) == (
        (2026, False),
        (2027, True),
    )
    assert cli.ingestion_targets(None, today=date(2027, 1, 7)) == (
        (2026, False),
        (2027, True),
    )


def test_scheduled_ingestion_uses_only_current_year_after_grace_period() -> None:
    assert cli.ingestion_targets(None, today=date(2027, 1, 8)) == ((2027, False),)
    assert cli.ingestion_targets(None, today=date(2027, 8, 12)) == ((2027, False),)


def test_explicit_ingestion_year_bypasses_scheduled_policy() -> None:
    assert cli.ingestion_targets(2025, today=date(2027, 1, 1)) == ((2025, False),)
