from decimal import Decimal

import pytest

from core.exchange_rates import ExchangeRateProviderError
from integrations.exchange_rates import FrankfurterExchangeRateProvider


def test_frankfurter_provider_converts_with_direct_historical_rate():
    seen_urls: list[str] = []

    def fetch_json(url: str) -> object:
        seen_urls.append(url)
        return {
            "date": "2026-05-20",
            "base": "CNY",
            "quote": "SGD",
            "rate": 0.18,
        }

    provider = FrankfurterExchangeRateProvider(fetch_json=fetch_json)

    conversion = provider.convert(
        Decimal("30"),
        from_currency="CNY",
        to_currency="SGD",
        date="2026-05-20",
    )

    assert conversion.converted_amount == Decimal("5.40")
    assert conversion.rate == Decimal("0.18")
    assert conversion.rate_date == "2026-05-20"
    assert seen_urls == [
        "https://api.frankfurter.dev/v2/rate/CNY/SGD?date=2026-05-20"
    ]


def test_frankfurter_provider_preserves_returned_previous_available_rate_date():
    provider = FrankfurterExchangeRateProvider(
        fetch_json=lambda url: {
            "date": "2026-05-22",
            "base": "USD",
            "quote": "SGD",
            "rate": 1.35,
        }
    )

    conversion = provider.convert(
        Decimal("5"),
        from_currency="USD",
        to_currency="SGD",
        date="2026-05-24",
    )

    assert conversion.converted_amount == Decimal("6.75")
    assert conversion.rate_date == "2026-05-22"


def test_frankfurter_provider_returns_identity_conversion_without_http_call():
    def fetch_json(url: str) -> object:
        raise AssertionError("same-currency conversion should not call Frankfurter")

    provider = FrankfurterExchangeRateProvider(fetch_json=fetch_json)

    conversion = provider.convert(
        Decimal("12.30"),
        from_currency="SGD",
        to_currency="SGD",
        date="2026-05-20",
    )

    assert conversion.converted_amount == Decimal("12.30")
    assert conversion.rate == Decimal("1")
    assert conversion.rate_date == "2026-05-20"


def test_frankfurter_provider_falls_back_to_previous_rate_from_time_series():
    seen_urls: list[str] = []

    def fetch_json(url: str) -> object:
        seen_urls.append(url)
        if "/v2/rate/" in url:
            raise ExchangeRateProviderError("exact rate unavailable")
        return [
            {
                "date": "2026-05-21",
                "base": "USD",
                "quote": "SGD",
                "rate": 1.34,
            },
            {
                "date": "2026-05-22",
                "base": "USD",
                "quote": "SGD",
                "rate": 1.35,
            },
        ]

    provider = FrankfurterExchangeRateProvider(fetch_json=fetch_json)

    conversion = provider.convert(
        Decimal("10"),
        from_currency="USD",
        to_currency="SGD",
        date="2026-05-24",
    )

    assert conversion.converted_amount == Decimal("13.50")
    assert conversion.rate_date == "2026-05-22"
    assert seen_urls == [
        "https://api.frankfurter.dev/v2/rate/USD/SGD?date=2026-05-24",
        "https://api.frankfurter.dev/v2/rates?from=2026-05-14&to=2026-05-24&base=USD&quotes=SGD",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"date": "2026-05-20", "base": "USD", "quote": "SGD", "rate": 0},
        {"date": "2026-05-20", "base": "EUR", "quote": "SGD", "rate": 1.1},
    ],
)
def test_frankfurter_provider_rejects_malformed_rate_payload(payload: object):
    provider = FrankfurterExchangeRateProvider(fetch_json=lambda url: payload)

    with pytest.raises(ExchangeRateProviderError):
        provider.convert(
            Decimal("10"),
            from_currency="USD",
            to_currency="SGD",
            date="2026-05-20",
        )
