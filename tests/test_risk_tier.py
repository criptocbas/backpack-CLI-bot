"""Tests for plan building — the spot tier ladder and the perp risk-tier
ladder back-solved from a risk budget.

The centerpiece is the risk-budget invariant: realized loss == the risk
budget when all rungs fill. Invalid inputs raise ``PlanValidationError``
(carrying a human-readable reason), never silently return ``None``.
"""

from decimal import Decimal

import pytest

from core.order_manager import Direction, Distribution, PlanValidationError

# Realized loss is built from Decimal divisions, so allow a sub-cent residual.
EPS = Decimal("1e-6")


def _realized_loss(plan) -> Decimal:
    """Loss if every rung fills and the stop fires."""
    return plan.tier_plan.total_quantity * abs(
        plan.tier_plan.avg_fill_price - plan.sl_price
    )


# --- risk-budget invariant --------------------------------------------------

def test_long_risk_budget_invariant(manager):
    plan = manager.build_risk_tier_plan(
        symbol="SOL_USDC_PERP",
        direction=Direction.LONG,
        target_avg=Decimal("100"),
        sl_price=Decimal("95"),
        risk_amount=Decimal("50"),
        width_pct=Decimal("0.02"),
        num_orders=10,
        distribution=Distribution.LINEAR_EVEN,
    )
    assert abs(_realized_loss(plan) - plan.risk_amount) < EPS
    assert plan.max_loss_if_all_filled == plan.risk_amount


def test_short_risk_budget_invariant(manager):
    plan = manager.build_risk_tier_plan(
        symbol="SOL_USDC_PERP",
        direction=Direction.SHORT,
        target_avg=Decimal("100"),
        sl_price=Decimal("110"),
        risk_amount=Decimal("75"),
        width_pct=Decimal("0.03"),
        num_orders=8,
        distribution=Distribution.LINEAR_EVEN,
    )
    assert abs(_realized_loss(plan) - plan.risk_amount) < EPS


def test_linear_even_has_zero_drift(manager):
    """With clean terminating prices, linear-even actual avg == target avg."""
    plan = manager.build_risk_tier_plan(
        symbol="SOL_USDC_PERP",
        direction=Direction.LONG,
        target_avg=Decimal("100"),
        sl_price=Decimal("90"),
        risk_amount=Decimal("100"),
        width_pct=Decimal("0.05"),  # low=95, high=105, 5 rungs → step 2.5 (exact)
        num_orders=5,
        distribution=Distribution.LINEAR_EVEN,
    )
    assert plan.tier_plan.avg_fill_price == plan.target_avg
    assert plan.avg_drift_pct == Decimal(0)


def test_worst_rung_loss_is_below_full_risk_for_long(manager):
    # Only the worst-priced (highest) rung filling loses less than the full
    # budget, since fewer contracts are exposed.
    plan = manager.build_risk_tier_plan(
        symbol="SOL_USDC_PERP",
        direction=Direction.LONG,
        target_avg=Decimal("100"),
        sl_price=Decimal("95"),
        risk_amount=Decimal("50"),
        width_pct=Decimal("0.02"),
        num_orders=10,
        distribution=Distribution.LINEAR_EVEN,
    )
    assert 0 < plan.max_loss_if_only_worst_rung < plan.max_loss_if_all_filled


# --- risk-tier rejections (now raise instead of returning None) -------------

def test_rejects_non_perp_symbol(manager):
    with pytest.raises(PlanValidationError):
        manager.build_risk_tier_plan(
            symbol="SOL_USDC",
            direction=Direction.LONG,
            target_avg=Decimal("100"),
            sl_price=Decimal("95"),
            risk_amount=Decimal("50"),
            width_pct=Decimal("0.02"),
            num_orders=5,
        )


def test_rejects_long_sl_above_target(manager):
    with pytest.raises(PlanValidationError):
        manager.build_risk_tier_plan(
            symbol="SOL_USDC_PERP",
            direction=Direction.LONG,
            target_avg=Decimal("100"),
            sl_price=Decimal("105"),
            risk_amount=Decimal("50"),
            width_pct=Decimal("0.02"),
            num_orders=5,
        )


def test_rejects_short_sl_below_target(manager):
    with pytest.raises(PlanValidationError):
        manager.build_risk_tier_plan(
            symbol="SOL_USDC_PERP",
            direction=Direction.SHORT,
            target_avg=Decimal("100"),
            sl_price=Decimal("95"),
            risk_amount=Decimal("50"),
            width_pct=Decimal("0.02"),
            num_orders=5,
        )


def test_rejects_ladder_crossing_sl(manager):
    # width 10% → ladder low = 90, but the SL at 92 sits inside the ladder.
    with pytest.raises(PlanValidationError):
        manager.build_risk_tier_plan(
            symbol="SOL_USDC_PERP",
            direction=Direction.LONG,
            target_avg=Decimal("100"),
            sl_price=Decimal("92"),
            risk_amount=Decimal("50"),
            width_pct=Decimal("0.10"),
            num_orders=5,
        )


def test_rejects_width_out_of_range(manager):
    with pytest.raises(PlanValidationError):
        manager.build_risk_tier_plan(
            symbol="SOL_USDC_PERP",
            direction=Direction.LONG,
            target_avg=Decimal("100"),
            sl_price=Decimal("95"),
            risk_amount=Decimal("50"),
            width_pct=Decimal("0.6"),
            num_orders=5,
        )


def test_rejects_non_positive_risk(manager):
    with pytest.raises(PlanValidationError):
        manager.build_risk_tier_plan(
            symbol="SOL_USDC_PERP",
            direction=Direction.LONG,
            target_avg=Decimal("100"),
            sl_price=Decimal("95"),
            risk_amount=Decimal("0"),
            width_pct=Decimal("0.02"),
            num_orders=5,
        )


def test_rejection_carries_a_reason(manager):
    # The message is what the CLI surfaces to the user — it must be specific.
    with pytest.raises(PlanValidationError, match="perp"):
        manager.build_risk_tier_plan(
            symbol="SOL_USDC",
            direction=Direction.LONG,
            target_avg=Decimal("100"),
            sl_price=Decimal("95"),
            risk_amount=Decimal("50"),
            width_pct=Decimal("0.02"),
            num_orders=5,
        )


# --- build_tier_plan validation ---------------------------------------------

def test_tier_plan_builds_with_total_value(manager):
    plan = manager.build_tier_plan(
        symbol="SOL_USDC",
        side="Bid",
        price_low=Decimal("90"),
        price_high=Decimal("110"),
        num_orders=5,
        total_value=Decimal("1000"),
        distribution=Distribution.LINEAR_EVEN,
    )
    assert plan.num_orders == 5
    assert plan.total_value == Decimal("1000")
    assert len(plan.prices) == len(plan.quantities) == 5


def test_tier_plan_rejects_inverted_bounds(manager):
    with pytest.raises(PlanValidationError):
        manager.build_tier_plan(
            symbol="SOL_USDC",
            side="Bid",
            price_low=Decimal("110"),
            price_high=Decimal("90"),
            num_orders=5,
            total_value=Decimal("1000"),
        )


def test_tier_plan_rejects_both_sizings(manager):
    with pytest.raises(PlanValidationError):
        manager.build_tier_plan(
            symbol="SOL_USDC",
            side="Bid",
            price_low=Decimal("90"),
            price_high=Decimal("110"),
            num_orders=5,
            total_value=Decimal("1000"),
            total_quantity=Decimal("10"),
        )


def test_tier_plan_rejects_bad_side(manager):
    with pytest.raises(PlanValidationError):
        manager.build_tier_plan(
            symbol="SOL_USDC",
            side="Long",  # not Bid/Ask
            price_low=Decimal("90"),
            price_high=Decimal("110"),
            num_orders=5,
            total_value=Decimal("1000"),
        )


def test_tier_plan_enforces_exchange_min_quantity(manager_factory):
    """A rung below the exchange minQuantity is rejected, not silently sent."""
    mgr = manager_factory({
        "tick_size": Decimal("0.01"),
        "step_size": Decimal("0.0001"),
        "min_price": None,
        "max_price": None,
        "min_quantity": Decimal("1000"),  # absurdly high → every rung too small
        "max_quantity": None,
    })
    with pytest.raises(PlanValidationError, match="minQuantity"):
        mgr.build_tier_plan(
            symbol="SOL_USDC",
            side="Bid",
            price_low=Decimal("90"),
            price_high=Decimal("110"),
            num_orders=5,
            total_value=Decimal("1000"),
        )
