from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TREASURY_NOMINAL_SERIES = "treasury_nominal"
SUPPORTED_TENORS_MONTHS = frozenset(
    {
        Decimal("1"),
        Decimal("1.5"),
        Decimal("2"),
        Decimal("3"),
        Decimal("4"),
        Decimal("6"),
        Decimal("12"),
        Decimal("24"),
        Decimal("36"),
        Decimal("60"),
        Decimal("84"),
        Decimal("120"),
        Decimal("240"),
        Decimal("360"),
    }
)


class RateObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: date
    series: Literal["treasury_nominal"] = TREASURY_NOMINAL_SERIES
    tenor_months: Decimal = Field(ge=Decimal("1"), le=Decimal("360"))
    rate_percent: Decimal = Field(ge=Decimal("-5"), le=Decimal("100"))
    source_url: str = Field(min_length=1, max_length=2048)

    @field_validator("tenor_months")
    @classmethod
    def validate_supported_tenor(cls, value: Decimal) -> Decimal:
        if value not in SUPPORTED_TENORS_MONTHS:
            raise ValueError("unsupported Treasury tenor")
        return value


class RatePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: date
    series: Literal["treasury_nominal"] = TREASURY_NOMINAL_SERIES
    tenor_months: Decimal
    rate_percent: Decimal
    source_url: str

    @field_validator("tenor_months")
    @classmethod
    def validate_supported_tenor(cls, value: Decimal) -> Decimal:
        if value not in SUPPORTED_TENORS_MONTHS:
            raise ValueError("unsupported Treasury tenor")
        return value


class RateComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: RatePoint
    end: RatePoint
    percentage_point_change: Decimal
    basis_point_change: Decimal


class CurveSpread(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: date
    short_rate: RatePoint
    long_rate: RatePoint
    spread_basis_points: Decimal
