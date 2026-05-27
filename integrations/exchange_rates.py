import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from core.currencies import normalize_currency_code
from core.exchange_rates import ExchangeRateConversion, ExchangeRateProviderError


FetchJson = Callable[[str], object]


@dataclass(frozen=True)
class _Rate:
    base: str
    quote: str
    rate: Decimal
    rate_date: str


class FrankfurterExchangeRateProvider:
    def __init__(
        self,
        *,
        base_url: str = "https://api.frankfurter.dev",
        fetch_json: FetchJson | None = None,
        lookback_days: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._fetch_json = fetch_json or _fetch_json
        self._lookback_days = lookback_days
        self._cache: dict[tuple[str, str, str], _Rate] = {}

    def convert(
        self,
        amount: Decimal,
        *,
        from_currency: str,
        to_currency: str,
        date: str,
    ) -> ExchangeRateConversion:
        base = _normalize_supported_currency(from_currency)
        quote = _normalize_supported_currency(to_currency)
        _parse_date(date)

        if base == quote:
            return ExchangeRateConversion(
                original_amount=amount,
                original_currency=base,
                converted_amount=amount,
                converted_currency=quote,
                rate=Decimal("1"),
                rate_date=date,
            )

        rate = self._rate_for(base=base, quote=quote, date_value=date)
        return ExchangeRateConversion(
            original_amount=amount,
            original_currency=base,
            converted_amount=amount * rate.rate,
            converted_currency=quote,
            rate=rate.rate,
            rate_date=rate.rate_date,
        )

    def _rate_for(self, *, base: str, quote: str, date_value: str) -> _Rate:
        cache_key = (base, quote, date_value)
        cached_rate = self._cache.get(cache_key)
        if cached_rate is not None:
            return cached_rate

        try:
            rate = self._fetch_direct_rate(
                base=base,
                quote=quote,
                date_value=date_value,
            )
        except ExchangeRateProviderError:
            rate = self._fetch_previous_available_rate(
                base=base,
                quote=quote,
                date_value=date_value,
            )

        self._cache[cache_key] = rate
        return rate

    def _fetch_direct_rate(
        self,
        *,
        base: str,
        quote: str,
        date_value: str,
    ) -> _Rate:
        url = (
            f"{self._base_url}/v2/rate/{base}/{quote}"
            f"?date={urllib.parse.quote(date_value)}"
        )
        return _parse_rate_payload(
            self._fetch_json(url),
            expected_base=base,
            expected_quote=quote,
            requested_date=date_value,
        )

    def _fetch_previous_available_rate(
        self,
        *,
        base: str,
        quote: str,
        date_value: str,
    ) -> _Rate:
        end_date = _parse_date(date_value)
        start_date = end_date - timedelta(days=self._lookback_days)
        query = urllib.parse.urlencode(
            {
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
                "base": base,
                "quotes": quote,
            }
        )
        url = f"{self._base_url}/v2/rates?{query}"
        payload = self._fetch_json(url)
        rates = [
            _parse_rate_payload(
                row,
                expected_base=base,
                expected_quote=quote,
                requested_date=date_value,
            )
            for row in _extract_rate_rows(payload)
        ]
        if not rates:
            raise ExchangeRateProviderError(
                f"No exchange rate found for {base}/{quote} on or before {date_value}."
            )
        return max(rates, key=lambda rate: rate.rate_date)


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "expense-agent/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise ExchangeRateProviderError(
            f"Exchange-rate provider returned HTTP {error.code}: {message}"
        ) from error
    except Exception as error:
        raise ExchangeRateProviderError("Failed to fetch exchange rate.") from error

    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise ExchangeRateProviderError("Exchange-rate response was not JSON.") from error


def _parse_rate_payload(
    payload: object,
    *,
    expected_base: str,
    expected_quote: str,
    requested_date: str,
) -> _Rate:
    if not isinstance(payload, Mapping):
        raise ExchangeRateProviderError("Exchange-rate payload must be an object.")

    try:
        rate_date = str(payload["date"])
        base = str(payload["base"]).upper()
        quote = str(payload["quote"]).upper()
        rate = Decimal(str(payload["rate"]))
    except Exception as error:
        raise ExchangeRateProviderError("Exchange-rate payload is malformed.") from error

    if base != expected_base or quote != expected_quote:
        raise ExchangeRateProviderError("Exchange-rate payload returned wrong pair.")
    if rate <= Decimal("0") or not rate.is_finite():
        raise ExchangeRateProviderError("Exchange-rate payload returned invalid rate.")

    parsed_rate_date = _parse_date(rate_date)
    requested = _parse_date(requested_date)
    if parsed_rate_date > requested:
        raise ExchangeRateProviderError("Exchange-rate payload returned a future rate.")

    return _Rate(base=base, quote=quote, rate=rate, rate_date=rate_date)


def _extract_rate_rows(payload: object) -> Sequence[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise ExchangeRateProviderError("Exchange-rate time series payload is malformed.")


def _normalize_supported_currency(value: str) -> str:
    code = normalize_currency_code(value)
    if code is None:
        raise ExchangeRateProviderError(f"Unsupported currency: {value}")
    return code


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ExchangeRateProviderError(f"Invalid exchange-rate date: {value}") from error
