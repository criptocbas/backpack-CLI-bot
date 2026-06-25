"""Tests for the price-level and size-weight generators."""

from decimal import Decimal

from core.order_manager import (
    Distribution,
    _generate_prices,
    _generate_size_weights,
)

# Decimal power uses context precision, so geometric endpoints carry a tiny
# residual; compare against this tolerance rather than for exact equality.
EPS = Decimal("1e-9")


# --- _generate_prices -------------------------------------------------------

def test_single_order_is_midpoint():
    prices = _generate_prices(Decimal("90"), Decimal("110"), 1, Distribution.LINEAR_EVEN)
    assert prices == [Decimal("100")]


def test_linear_even_endpoints_and_constant_spacing():
    prices = _generate_prices(Decimal("100"), Decimal("200"), 5, Distribution.LINEAR_EVEN)
    assert prices[0] == Decimal("100")
    assert prices[-1] == Decimal("200")
    diffs = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    assert all(d == Decimal("25") for d in diffs)


def test_geometric_even_constant_ratio_and_geomean_midpoint():
    prices = _generate_prices(Decimal("100"), Decimal("400"), 3, Distribution.GEOMETRIC_EVEN)
    assert prices[0] == Decimal("100")
    assert abs(prices[-1] - Decimal("400")) < EPS
    # equal percentage gap → constant ratio between rungs
    assert abs(prices[1] / prices[0] - prices[2] / prices[1]) < EPS
    # middle rung is the geometric mean sqrt(100*400) = 200
    assert abs(prices[1] - Decimal("200")) < EPS


def test_geometric_prices_are_monotonic_increasing():
    prices = _generate_prices(Decimal("50"), Decimal("150"), 8, Distribution.GEOMETRIC_PYRAMID)
    assert prices == sorted(prices)
    assert len(prices) == 8


# --- _generate_size_weights -------------------------------------------------

def test_flat_weights_equal_and_sum_to_one():
    for dist in (Distribution.LINEAR_EVEN, Distribution.GEOMETRIC_EVEN):
        w = _generate_size_weights(5, dist, Decimal("1.5"), "Bid")
        assert all(x == w[0] for x in w)
        assert sum(w) == Decimal(1)


def test_pyramid_scale_one_is_flat():
    w = _generate_size_weights(4, Distribution.GEOMETRIC_PYRAMID, Decimal("1"), "Bid")
    assert all(x == w[0] for x in w)


def test_pyramid_bid_is_heavy_at_bottom():
    w = _generate_size_weights(5, Distribution.GEOMETRIC_PYRAMID, Decimal("3"), "Bid")
    assert abs(sum(w) - Decimal(1)) < EPS
    # heaviest weight is the first rung (lowest price) for a buy
    assert w == sorted(w, reverse=True)
    assert w[0] > w[-1]


def test_pyramid_ask_is_heavy_at_top():
    w = _generate_size_weights(5, Distribution.GEOMETRIC_PYRAMID, Decimal("3"), "Ask")
    assert abs(sum(w) - Decimal(1)) < EPS
    # heaviest weight is the last rung (highest price) for a sell
    assert w == sorted(w)
    assert w[-1] > w[0]


def test_single_order_weight_is_one():
    assert _generate_size_weights(1, Distribution.GEOMETRIC_PYRAMID, Decimal("3"), "Bid") == [Decimal(1)]
