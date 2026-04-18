"""Money normalization — canonical shape across Stripe / PayPal / Square / Adyen / etc."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

# ISO 4217 currencies that use zero fractional digits (smallest unit == major unit).
# Not exhaustive, but covers the common ones. Fall back to 2 decimals otherwise.
_ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "ISK",
        "JPY",
        "KMF",
        "KRW",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    }
)

# Currencies with three decimal places (smallest unit = 1/1000).
_THREE_DECIMAL_CURRENCIES: frozenset[str] = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})


class Money(BaseModel):
    """Canonical money representation.

    - ``amount_cents``: integer in the smallest currency unit
      (cents for USD/EUR; whole yen for JPY; millidinars for BHD).
    - ``currency``: ISO 4217 uppercase.
    - ``amount_decimal``: human-readable decimal in the major unit.
    - ``original``: the untouched source value (kept for reference / debugging).
    """

    amount_cents: int
    currency: str
    amount_decimal: Decimal
    original: dict[str, Any] = Field(default_factory=dict)


def _decimals_for(currency: str) -> int:
    c = currency.upper()
    if c in _ZERO_DECIMAL_CURRENCIES:
        return 0
    if c in _THREE_DECIMAL_CURRENCIES:
        return 3
    return 2


def _from_minor_units(amount: int, currency: str) -> Money:
    d = _decimals_for(currency)
    major = Decimal(amount) / (Decimal(10) ** d) if d else Decimal(amount)
    return Money(
        amount_cents=int(amount),
        currency=currency.upper(),
        amount_decimal=major,
        original={"amount": amount, "currency": currency},
    )


def _from_major_decimal(value: Decimal | str | float | int, currency: str) -> Money | None:
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    d = _decimals_for(currency)
    minor = int((dec * (Decimal(10) ** d)).quantize(Decimal("1")))
    return Money(
        amount_cents=minor,
        currency=currency.upper(),
        amount_decimal=dec,
        original={"value": str(value), "currency": currency},
    )


def normalize_money(value: Any, *, currency_hint: str | None = None) -> Money | None:
    """Detect the shape of ``value`` and return a canonical :class:`Money`.

    Supports:
    - Stripe / Square: ``{"amount": 1000, "currency": "usd"}`` — integer minor units.
    - PayPal / Adyen: ``{"value": "10.00", "currency_code": "USD"}`` — decimal major units.
    - Plain int + ``currency_hint`` — treated as minor units.
    - Plain ``Decimal`` / decimal-looking string + ``currency_hint`` — treated as major units.
    - Existing ``Money`` instance — returned as-is.

    Returns ``None`` for anything it can't confidently parse (never raises on bad input).
    """
    if value is None:
        return None

    if isinstance(value, Money):
        return value

    if isinstance(value, dict):
        return _normalize_dict_money(value, currency_hint)

    # Integer alone → minor units, requires currency hint.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if currency_hint is None:
            return None
        m = _from_minor_units(value, currency_hint)
        m.original = {"amount": value}
        return m

    # Decimal / decimal string → major units, requires currency hint.
    if isinstance(value, Decimal | float):
        if currency_hint is None:
            return None
        m = _from_major_decimal(value, currency_hint)
        if m is not None:
            m.original = {"value": str(value)}
        return m

    if isinstance(value, str):
        if currency_hint is None:
            return None
        try:
            dec = Decimal(value)
        except (InvalidOperation, ValueError):
            return None
        m = _from_major_decimal(dec, currency_hint)
        if m is not None:
            m.original = {"value": value}
        return m

    return None


def _normalize_dict_money(value: dict[str, Any], currency_hint: str | None) -> Money | None:
    currency = _extract_currency(value) or currency_hint
    if currency is None:
        return None

    # Stripe / Square style: integer ``amount``.
    amount = value.get("amount")
    if isinstance(amount, bool):
        amount = None
    if isinstance(amount, int):
        m = _from_minor_units(amount, currency)
        m.original = dict(value)
        return m

    # PayPal / Adyen style: decimal ``value``.
    raw_value = value.get("value")
    if isinstance(raw_value, str | int | float | Decimal) and not isinstance(raw_value, bool):
        m = _from_major_decimal(raw_value, currency)
        if m is not None:
            m.original = dict(value)
        return m

    # Adyen sometimes uses ``amount`` as a string decimal.
    if isinstance(amount, str):
        try:
            Decimal(amount)
        except (InvalidOperation, ValueError):
            return None
        m = _from_major_decimal(amount, currency)
        if m is not None:
            m.original = dict(value)
        return m

    return None


def _extract_currency(value: dict[str, Any]) -> str | None:
    for key in ("currency", "currency_code", "currencyCode", "currency_iso"):
        found = value.get(key)
        if isinstance(found, str) and found.strip():
            return found.strip().upper()
    return None
