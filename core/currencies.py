import re


SUPPORTED_CURRENCIES = (
    "SGD",
    "CNY",
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "HKD",
    "TWD",
    "MYR",
    "IDR",
    "THB",
    "VND",
    "KRW",
    "AUD",
    "NZD",
    "CAD",
    "CHF",
    "INR",
    "PHP",
)

SUPPORTED_CURRENCY_SET = frozenset(SUPPORTED_CURRENCIES)

CURRENCY_ALIASES = {
    "RMB": "CNY",
    "人民币": "CNY",
    "中国人民币": "CNY",
    "中国元": "CNY",
    "新币": "SGD",
    "新加坡币": "SGD",
    "新加坡元": "SGD",
    "坡币": "SGD",
    "S$": "SGD",
    "美元": "USD",
    "美金": "USD",
    "US DOLLAR": "USD",
    "US DOLLARS": "USD",
    "欧元": "EUR",
    "英镑": "GBP",
    "日元": "JPY",
    "日币": "JPY",
    "港币": "HKD",
    "港元": "HKD",
    "台币": "TWD",
    "新台币": "TWD",
    "马币": "MYR",
    "令吉": "MYR",
    "马来西亚令吉": "MYR",
    "印尼盾": "IDR",
    "泰铢": "THB",
    "越南盾": "VND",
    "韩元": "KRW",
    "澳元": "AUD",
    "澳币": "AUD",
    "纽币": "NZD",
    "新西兰元": "NZD",
    "加元": "CAD",
    "加币": "CAD",
    "瑞郎": "CHF",
    "瑞士法郎": "CHF",
    "印度卢比": "INR",
    "菲律宾比索": "PHP",
}

CURRENCY_PROMPT_GUIDANCE = (
    "Use ISO 4217 currency codes. Common aliases: 人民币/RMB -> CNY, "
    "新币/新加坡元 -> SGD, 美元/美金 -> USD, 欧元 -> EUR, 英镑 -> GBP, "
    "日元 -> JPY, 港币 -> HKD, 台币 -> TWD, 马币/令吉 -> MYR."
)

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


def normalize_currency_code(
    value: str | None,
    *,
    default_currency: str | None = None,
) -> str | None:
    candidate = value if value is not None and value.strip() else default_currency
    if candidate is None:
        return None

    text = candidate.strip()
    if not text:
        return None

    alias = CURRENCY_ALIASES.get(text) or CURRENCY_ALIASES.get(text.upper())
    code = alias or text.upper()
    if not _CURRENCY_PATTERN.fullmatch(code):
        return None
    if code not in SUPPORTED_CURRENCY_SET:
        return None
    return code
