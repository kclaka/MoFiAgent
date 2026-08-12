from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from mofiagent.agent.models import ModelToolCall
from mofiagent.agent.tools import RateTools, result_dates, result_source_urls
from mofiagent.rates.models import CurveSpread, RateComparison, RatePoint
from mofiagent.rates.repository import RateRepository

SOURCE_URL = "https://home.treasury.gov/example"


def point(tenor: str, rate: str, observed_at: date = date(2026, 8, 12)) -> RatePoint:
    return RatePoint(
        observed_at=observed_at,
        tenor_months=Decimal(tenor),
        rate_percent=Decimal(rate),
        source_url=SOURCE_URL,
    )


class FakeRepository:
    async def latest(self, tenor_months: Decimal) -> RatePoint:
        return point(str(tenor_months), "4.68")

    async def on_or_before(self, tenor_months: Decimal, requested_date: date) -> RatePoint:
        return point(str(tenor_months), "4.70", requested_date)

    async def compare(
        self, tenor_months: Decimal, start_date: date, end_date: date
    ) -> RateComparison:
        return RateComparison(
            start=point(str(tenor_months), "4.70", start_date),
            end=point(str(tenor_months), "4.68", end_date),
            percentage_point_change=Decimal("-0.02"),
            basis_point_change=Decimal("-2"),
        )

    async def spread(
        self,
        short_tenor_months: Decimal,
        long_tenor_months: Decimal,
        requested_date: date | None = None,
    ) -> CurveSpread:
        observed_at = requested_date or date(2026, 8, 12)
        return CurveSpread(
            observed_at=observed_at,
            short_rate=point(str(short_tenor_months), "4.20", observed_at),
            long_rate=point(str(long_tenor_months), "4.68", observed_at),
            spread_basis_points=Decimal("48"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments", "expected_key"),
    [
        ("get_latest_rate", {"tenor": "10y"}, "rate_percent"),
        (
            "get_historical_rate",
            {"tenor": "10y", "on_or_before": "2026-08-11"},
            "rate_percent",
        ),
        (
            "compare_rates",
            {"tenor": "10y", "start_date": "2026-08-11", "end_date": "2026-08-12"},
            "basis_point_change",
        ),
        (
            "get_curve_spread",
            {"short_tenor": "2y", "long_tenor": "10y"},
            "spread_basis_points",
        ),
    ],
)
async def test_rate_tools_validate_and_dispatch(
    name: str, arguments: dict[str, str], expected_key: str
) -> None:
    tools = RateTools(cast(RateRepository, FakeRepository()))

    result = await tools.execute(ModelToolCall(name=name, arguments=arguments))

    assert result.ok is True
    assert expected_key in result.payload


@pytest.mark.asyncio
async def test_rate_tools_return_bounded_errors() -> None:
    tools = RateTools(cast(RateRepository, FakeRepository()))

    invalid_tenor = await tools.execute(
        ModelToolCall(name="get_latest_rate", arguments={"tenor": "99y"})
    )
    unknown_tool = await tools.execute(ModelToolCall(name="drop_database", arguments={}))

    assert invalid_tenor.ok is False
    assert unknown_tool.payload == {"error": "unknown tool: drop_database"}


def test_tool_metadata_extractors_recurse() -> None:
    payload = {
        "observed_at": "2026-08-12",
        "start": {
            "observed_at": "2026-08-11",
            "source_url": SOURCE_URL,
        },
    }

    assert result_dates(payload) == [date(2026, 8, 12), date(2026, 8, 11)]
    assert result_source_urls(payload) == [SOURCE_URL]
