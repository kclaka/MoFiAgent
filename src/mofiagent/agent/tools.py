from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast

from google.genai import types
from pydantic import BaseModel, ConfigDict, ValidationError

from mofiagent.agent.models import ModelToolCall, ToolResult
from mofiagent.rates.repository import RateNotFoundError, RateRepository

Tenor = Literal[
    "1m",
    "1.5m",
    "2m",
    "3m",
    "4m",
    "6m",
    "1y",
    "2y",
    "3y",
    "5y",
    "7y",
    "10y",
    "20y",
    "30y",
]

TENOR_MONTHS: dict[Tenor, Decimal] = {
    "1m": Decimal("1"),
    "1.5m": Decimal("1.5"),
    "2m": Decimal("2"),
    "3m": Decimal("3"),
    "4m": Decimal("4"),
    "6m": Decimal("6"),
    "1y": Decimal("12"),
    "2y": Decimal("24"),
    "3y": Decimal("36"),
    "5y": Decimal("60"),
    "7y": Decimal("84"),
    "10y": Decimal("120"),
    "20y": Decimal("240"),
    "30y": Decimal("360"),
}


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LatestRateInput(ToolInput):
    tenor: Tenor


class HistoricalRateInput(ToolInput):
    tenor: Tenor
    on_or_before: date


class CompareRateInput(ToolInput):
    tenor: Tenor
    start_date: date
    end_date: date


class CurveSpreadInput(ToolInput):
    short_tenor: Tenor
    long_tenor: Tenor
    on_or_before: date | None = None


TOOL_SCHEMAS: tuple[types.FunctionDeclaration, ...] = (
    types.FunctionDeclaration(
        name="get_latest_rate",
        description="Get the most recent official nominal U.S. Treasury yield for one tenor.",
        parameters_json_schema=LatestRateInput.model_json_schema(),
    ),
    types.FunctionDeclaration(
        name="get_historical_rate",
        description=(
            "Get an official nominal U.S. Treasury yield on a date, or the latest prior "
            "observation when that date is a weekend or holiday."
        ),
        parameters_json_schema=HistoricalRateInput.model_json_schema(),
    ),
    types.FunctionDeclaration(
        name="compare_rates",
        description=(
            "Compare one nominal U.S. Treasury tenor between two dates and return the exact "
            "percentage-point and basis-point change."
        ),
        parameters_json_schema=CompareRateInput.model_json_schema(),
    ),
    types.FunctionDeclaration(
        name="get_curve_spread",
        description=(
            "Get the difference in basis points between two nominal U.S. Treasury tenors on "
            "their latest common observation date."
        ),
        parameters_json_schema=CurveSpreadInput.model_json_schema(),
    ),
)


class RateTools:
    def __init__(self, repository: RateRepository) -> None:
        self._repository = repository

    async def execute(self, call: ModelToolCall) -> ToolResult:
        try:
            payload = await self._dispatch(call)
        except (ValidationError, ValueError, RateNotFoundError) as error:
            return ToolResult(
                name=call.name,
                ok=False,
                payload={"error": str(error)},
            )
        return ToolResult(name=call.name, ok=True, payload=payload)

    async def _dispatch(self, call: ModelToolCall) -> dict[str, Any]:
        if call.name == "get_latest_rate":
            arguments = LatestRateInput.model_validate(call.arguments)
            result = await self._repository.latest(TENOR_MONTHS[arguments.tenor])
            return result.model_dump(mode="json")

        if call.name == "get_historical_rate":
            arguments = HistoricalRateInput.model_validate(call.arguments)
            result = await self._repository.on_or_before(
                TENOR_MONTHS[arguments.tenor], arguments.on_or_before
            )
            return result.model_dump(mode="json")

        if call.name == "compare_rates":
            arguments = CompareRateInput.model_validate(call.arguments)
            result = await self._repository.compare(
                TENOR_MONTHS[arguments.tenor],
                arguments.start_date,
                arguments.end_date,
            )
            return result.model_dump(mode="json")

        if call.name == "get_curve_spread":
            arguments = CurveSpreadInput.model_validate(call.arguments)
            result = await self._repository.spread(
                TENOR_MONTHS[arguments.short_tenor],
                TENOR_MONTHS[arguments.long_tenor],
                arguments.on_or_before,
            )
            return result.model_dump(mode="json")

        raise ValueError(f"unknown tool: {call.name}")


def result_dates(payload: dict[str, Any]) -> list[date]:
    dates: list[date] = []
    for key in ("observed_at", "data_as_of"):
        raw_value = payload.get(key)
        if isinstance(raw_value, str):
            dates.append(date.fromisoformat(raw_value))
    for key in ("start", "end", "short_rate", "long_rate"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            dates.extend(result_dates(cast(dict[str, Any], nested)))
    return dates


def result_source_urls(payload: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    raw_url = payload.get("source_url")
    if isinstance(raw_url, str):
        urls.append(raw_url)
    for value in payload.values():
        if isinstance(value, dict):
            urls.extend(result_source_urls(cast(dict[str, Any], value)))
    return urls
