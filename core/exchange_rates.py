from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


class ExchangeRateProviderError(Exception):
    """Raised when an exchange-rate provider cannot return a usable rate."""


@dataclass(frozen=True)
class ExchangeRateConversion:
    original_amount: Decimal
    original_currency: str
    converted_amount: Decimal
    converted_currency: str
    rate: Decimal
    rate_date: str


class ExchangeRateProvider(Protocol):
    def convert(
        self,
        amount: Decimal,
        *,
        from_currency: str,
        to_currency: str,
        date: str,
    ) -> ExchangeRateConversion:
        raise NotImplementedError
