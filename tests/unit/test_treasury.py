from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from mofiagent.rates.treasury import (
    MAX_FEED_BYTES,
    TreasuryClient,
    TreasuryFeedError,
    build_feed_url,
    parse_treasury_feed,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "treasury_yield_curve.xml"
SOURCE_URL = "https://home.treasury.gov/example"


def test_parse_treasury_feed_normalizes_supported_tenors() -> None:
    observations = parse_treasury_feed(FIXTURE.read_bytes(), source_url=SOURCE_URL)

    assert len(observations) == 28
    latest_ten_year = next(
        observation
        for observation in observations
        if observation.observed_at == date(2026, 8, 12)
        and observation.tenor_months == Decimal("120")
    )
    assert latest_ten_year.rate_percent == Decimal("4.68")
    assert any(observation.tenor_months == Decimal("1.5") for observation in observations)


def test_parse_treasury_feed_skips_explicit_null_rate() -> None:
    payload = FIXTURE.read_text().replace(
        '<d:BC_30YEAR m:type="Edm.Double">5.24</d:BC_30YEAR>',
        '<d:BC_30YEAR m:null="true" />',
        1,
    )

    observations = parse_treasury_feed(payload.encode(), source_url=SOURCE_URL)

    assert len(observations) == 27


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty"),
        (b"not xml", "valid XML"),
        (b"<feed />", "no observations"),
    ],
)
def test_parse_treasury_feed_rejects_invalid_payload(payload: bytes, message: str) -> None:
    with pytest.raises(TreasuryFeedError, match=message):
        parse_treasury_feed(payload, source_url=SOURCE_URL)


def test_parse_treasury_feed_can_accept_valid_empty_new_year_feed() -> None:
    assert (
        parse_treasury_feed(
            b"<feed />",
            source_url=SOURCE_URL,
            allow_empty=True,
        )
        == []
    )


def test_parse_treasury_feed_rejects_oversized_payload() -> None:
    with pytest.raises(TreasuryFeedError, match="maximum allowed size"):
        parse_treasury_feed(b"x" * (MAX_FEED_BYTES + 1), source_url=SOURCE_URL)


def test_build_feed_url_is_bounded() -> None:
    assert "field_tdr_date_value=2026" in build_feed_url(2026)
    with pytest.raises(ValueError, match="supported range"):
        build_feed_url(1989)


@pytest.mark.asyncio
async def test_treasury_client_fetches_and_parses_feed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/xml"
        return httpx.Response(200, content=FIXTURE.read_bytes(), request=request)

    client = TreasuryClient(transport=httpx.MockTransport(handler))

    observations = await client.observations_for_year(2026)

    assert len(observations) == 28


@pytest.mark.asyncio
async def test_treasury_client_rejects_large_declared_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": str(MAX_FEED_BYTES + 1)},
            content=b"small",
            request=request,
        )

    client = TreasuryClient(transport=httpx.MockTransport(handler))

    with pytest.raises(TreasuryFeedError, match="maximum allowed size"):
        await client.fetch_year(2026)
