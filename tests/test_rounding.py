"""Tests for side-aware precision rounding."""

from decimal import ROUND_UP

from api.backpack import BackpackClient


def test_quantity_rounds_down_by_default(client: BackpackClient):
    assert client.round_to_precision("1.23456", "0.001") == "1.234"


def test_buy_price_rounds_down(client: BackpackClient):
    # default ROUND_DOWN: rounded bid never exceeds the user's target
    assert client.round_to_precision("100.999", "0.01") == "100.99"


def test_sell_price_rounds_up(client: BackpackClient):
    # ROUND_UP: rounded ask never drops below the user's target
    assert client.round_to_precision("1.23451", "0.01", rounding=ROUND_UP) == "1.24"


def test_trailing_zeros_are_stripped(client: BackpackClient):
    assert client.round_to_precision("100.10", "0.01") == "100.1"


def test_integer_tick_size(client: BackpackClient):
    assert client.round_to_precision("12345.67", "1") == "12345"


def test_already_aligned_value_is_unchanged(client: BackpackClient):
    assert client.round_to_precision("50", "0.01") == "50"


def test_accepts_float_and_int(client: BackpackClient):
    # floats/ints are coerced via str() — no binary drift at the tick
    assert client.round_to_precision(0.1, "0.01") == "0.1"
    assert client.round_to_precision(7, "0.01") == "7"
