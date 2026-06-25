"""Tests for display formatters and order-input parsing."""

from decimal import Decimal

import pytest

from utils.helpers import (
    NA,
    format_currency,
    format_percentage,
    format_price,
    format_quantity,
    parse_order_input,
)


def test_format_price_accepts_decimal_float_int():
    assert format_price(Decimal("1.2345")) == "1.2345"
    assert format_price(1.5) == "1.5000"
    assert format_price(2) == "2.0000"


def test_formatters_render_none_as_na():
    # A missing ticker (current_price is None) must not crash the handlers.
    assert format_price(None) == NA
    assert format_quantity(None) == NA
    assert format_percentage(None) == NA
    assert format_currency(None) == NA


def test_format_currency_groups_thousands():
    assert format_currency(Decimal("1234567.5")) == "$1,234,567.50"


def test_format_percentage():
    assert format_percentage(12.3456) == "12.35%"


def test_parse_order_input_quantity_and_price():
    out = parse_order_input("10@100.5")
    assert out["quantity"] == Decimal("10")
    assert out["price"] == Decimal("100.5")


def test_parse_order_input_quantity_only():
    out = parse_order_input("10")
    assert out["quantity"] == Decimal("10")
    assert out["price"] is None


def test_parse_order_input_rejects_empty():
    with pytest.raises(ValueError):
        parse_order_input("   ")


def test_parse_order_input_rejects_non_numeric():
    with pytest.raises(ValueError):
        parse_order_input("abc@100")
