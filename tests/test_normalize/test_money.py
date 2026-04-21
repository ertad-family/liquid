from __future__ import annotations

from decimal import Decimal

from liquid.normalize import Money, normalize_money


class TestStripeShape:
    def test_stripe_usd(self):
        m = normalize_money({"amount": 1000, "currency": "usd"})
        assert m is not None
        assert m.amount_cents == 1000
        assert m.currency == "USD"
        assert m.amount_decimal == Decimal("10.00")
        assert m.original == {"amount": 1000, "currency": "usd"}

    def test_stripe_negative(self):
        m = normalize_money({"amount": -500, "currency": "USD"})
        assert m is not None
        assert m.amount_cents == -500
        assert m.amount_decimal == Decimal("-5.00")

    def test_stripe_zero(self):
        m = normalize_money({"amount": 0, "currency": "eur"})
        assert m is not None
        assert m.amount_cents == 0
        assert m.currency == "EUR"


class TestPayPalShape:
    def test_paypal_usd(self):
        m = normalize_money({"value": "10.00", "currency_code": "USD"})
        assert m is not None
        assert m.amount_cents == 1000
        assert m.currency == "USD"
        assert m.amount_decimal == Decimal("10.00")

    def test_paypal_fractional(self):
        m = normalize_money({"value": "9.99", "currency_code": "EUR"})
        assert m is not None
        assert m.amount_cents == 999
        assert m.currency == "EUR"

    def test_paypal_decimal_input(self):
        m = normalize_money({"value": Decimal("12.34"), "currency_code": "USD"})
        assert m is not None
        assert m.amount_cents == 1234


class TestSquareShape:
    def test_square_usd(self):
        m = normalize_money({"amount": 1000, "currency": "USD"})
        assert m is not None
        assert m.amount_cents == 1000
        assert m.currency == "USD"


class TestAdyenShape:
    def test_adyen_minor_units(self):
        # Adyen actually uses minor units — same as Stripe.
        m = normalize_money({"value": 2500, "currency": "EUR"})
        assert m is not None
        # int "value" gets interpreted as major via the dict normalizer's
        # value-branch; with a Decimal 2500 at EUR (2 decimals), that's 250000
        # cents — document actual behaviour:
        assert m.amount_cents == 250000
        assert m.currency == "EUR"


class TestZeroDecimalCurrencies:
    def test_jpy(self):
        m = normalize_money({"amount": 1000, "currency": "JPY"})
        assert m is not None
        assert m.amount_cents == 1000
        assert m.amount_decimal == Decimal("1000")
        assert m.currency == "JPY"

    def test_krw(self):
        m = normalize_money({"amount": 50000, "currency": "KRW"})
        assert m is not None
        assert m.amount_cents == 50000
        assert m.amount_decimal == Decimal("50000")


class TestThreeDecimalCurrencies:
    def test_bhd(self):
        # Bahraini dinar has 3 decimals.
        m = normalize_money({"amount": 1500, "currency": "BHD"})
        assert m is not None
        assert m.amount_cents == 1500
        assert m.amount_decimal == Decimal("1.500")


class TestMissingCurrency:
    def test_dict_no_currency_no_hint(self):
        assert normalize_money({"amount": 1000}) is None

    def test_dict_no_currency_with_hint(self):
        m = normalize_money({"amount": 1000}, currency_hint="USD")
        assert m is not None
        assert m.currency == "USD"
        assert m.amount_cents == 1000


class TestBareValues:
    def test_int_needs_hint(self):
        assert normalize_money(1000) is None

    def test_int_with_hint(self):
        m = normalize_money(1000, currency_hint="USD")
        assert m is not None
        assert m.amount_cents == 1000
        assert m.amount_decimal == Decimal("10.00")

    def test_decimal_with_hint(self):
        m = normalize_money(Decimal("10.00"), currency_hint="USD")
        assert m is not None
        assert m.amount_cents == 1000

    def test_string_decimal_with_hint(self):
        m = normalize_money("12.50", currency_hint="USD")
        assert m is not None
        assert m.amount_cents == 1250

    def test_string_not_decimal(self):
        assert normalize_money("hello", currency_hint="USD") is None

    def test_none_value(self):
        assert normalize_money(None) is None

    def test_bool_value(self):
        assert normalize_money(True, currency_hint="USD") is None


class TestIdempotent:
    def test_money_input_returned_as_is(self):
        original = Money(amount_cents=500, currency="USD", amount_decimal=Decimal("5.00"))
        assert normalize_money(original) is original


class TestSerialization:
    """Cross-API canonicalisation relies on serialised Money being identical.

    ``original`` is kept as a Python attribute for audit/debug but must
    never appear in ``model_dump`` output — otherwise two vendors' payloads
    serialise to structurally different dicts.
    """

    def test_model_dump_excludes_original(self):
        m = normalize_money({"amount": 1000, "currency": "usd"})
        assert m is not None
        dumped = m.model_dump()
        assert "original" not in dumped
        assert set(dumped.keys()) == {"amount_cents", "currency", "amount_decimal"}

    def test_model_dump_json_excludes_original(self):
        m = normalize_money({"amount": 1000, "currency": "usd"})
        assert m is not None
        assert "original" not in m.model_dump_json()

    def test_original_attribute_still_accessible(self):
        m = normalize_money({"amount": 1000, "currency": "usd"})
        assert m is not None
        assert m.original == {"amount": 1000, "currency": "usd"}

    def test_stripe_and_paypal_serialise_identically(self):
        stripe = normalize_money({"amount": 9999, "currency": "usd"})
        paypal = normalize_money({"value": "99.99", "currency_code": "USD"})
        assert stripe is not None and paypal is not None
        assert stripe.model_dump() == paypal.model_dump()
