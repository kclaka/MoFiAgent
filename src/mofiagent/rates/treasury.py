from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx
from defusedxml import ElementTree

from mofiagent.rates.models import RateObservation

TREASURY_SOURCE_NAME = "U.S. Department of the Treasury"
TREASURY_FEED_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
)
MAX_FEED_BYTES = 5 * 1024 * 1024

ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
DATA_NAMESPACE = "http://schemas.microsoft.com/ado/2007/08/dataservices"
METADATA_NAMESPACE = "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"

TENOR_FIELDS: Mapping[str, Decimal] = {
    "BC_1MONTH": Decimal("1"),
    "BC_1_5MONTH": Decimal("1.5"),
    "BC_2MONTH": Decimal("2"),
    "BC_3MONTH": Decimal("3"),
    "BC_4MONTH": Decimal("4"),
    "BC_6MONTH": Decimal("6"),
    "BC_1YEAR": Decimal("12"),
    "BC_2YEAR": Decimal("24"),
    "BC_3YEAR": Decimal("36"),
    "BC_5YEAR": Decimal("60"),
    "BC_7YEAR": Decimal("84"),
    "BC_10YEAR": Decimal("120"),
    "BC_20YEAR": Decimal("240"),
    "BC_30YEAR": Decimal("360"),
}


class TreasuryFeedError(ValueError):
    """The Treasury feed is unavailable or violates its expected contract."""


def build_feed_url(year: int) -> str:
    if year < 1990 or year > 2100:
        raise ValueError("year is outside the supported range")
    return f"{TREASURY_FEED_URL}?data=daily_treasury_yield_curve&field_tdr_date_value={year}"


def parse_treasury_feed(payload: bytes, *, source_url: str) -> list[RateObservation]:
    if not payload:
        raise TreasuryFeedError("Treasury feed was empty")
    if len(payload) > MAX_FEED_BYTES:
        raise TreasuryFeedError("Treasury feed exceeded the maximum allowed size")

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise TreasuryFeedError("Treasury feed was not valid XML") from error

    namespaces = {"atom": ATOM_NAMESPACE, "d": DATA_NAMESPACE, "m": METADATA_NAMESPACE}
    entries = root.findall("atom:entry", namespaces)
    if not entries:
        raise TreasuryFeedError("Treasury feed contained no observations")

    observations: list[RateObservation] = []
    for entry in entries:
        properties = entry.find("atom:content/m:properties", namespaces)
        if properties is None:
            raise TreasuryFeedError("Treasury observation was missing properties")

        raw_date = properties.findtext("d:NEW_DATE", namespaces=namespaces)
        if not raw_date:
            raise TreasuryFeedError("Treasury observation was missing its date")
        try:
            observed_at = datetime.fromisoformat(raw_date.removesuffix("Z")).date()
        except ValueError as error:
            raise TreasuryFeedError("Treasury observation had an invalid date") from error

        for field_name, tenor_months in TENOR_FIELDS.items():
            element = properties.find(f"d:{field_name}", namespaces)
            if element is None or element.get(f"{{{METADATA_NAMESPACE}}}null") == "true":
                continue
            raw_rate = element.text
            if raw_rate is None:
                continue
            try:
                rate = Decimal(raw_rate)
            except InvalidOperation as error:
                raise TreasuryFeedError(f"Treasury field {field_name} was not numeric") from error

            observations.append(
                RateObservation(
                    observed_at=observed_at,
                    tenor_months=tenor_months,
                    rate_percent=rate,
                    source_url=source_url,
                )
            )

    if not observations:
        raise TreasuryFeedError("Treasury feed contained no usable rates")
    return observations


class TreasuryClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport

    async def fetch_year(self, year: int) -> tuple[str, bytes]:
        url = build_feed_url(year)
        headers = {
            "Accept": "application/xml",
            "User-Agent": "MoFiAgent/0.1 (+https://github.com/kclaka/MoFiAgent)",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers=headers,
            transport=self._transport,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_FEED_BYTES:
            raise TreasuryFeedError("Treasury feed exceeded the maximum allowed size")
        if len(response.content) > MAX_FEED_BYTES:
            raise TreasuryFeedError("Treasury feed exceeded the maximum allowed size")
        return str(response.url), response.content

    async def observations_for_year(self, year: int) -> list[RateObservation]:
        source_url, payload = await self.fetch_year(year)
        return parse_treasury_feed(payload, source_url=source_url)
