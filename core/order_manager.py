"""Order management system."""

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError
from api.backpack import BackpackClient


class Direction(str, Enum):
    """Direction of a perp position. Maps to a Backpack ``side``:
    LONG opens with ``Bid``; SHORT opens with ``Ask``.
    """

    LONG = "long"
    SHORT = "short"


class Distribution(str, Enum):
    """Tiered order distribution modes.

    LINEAR_EVEN:       equal dollar gap between rungs, equal size per rung.
                       Best for narrow ranges (<~5%), stablecoins, quick splits.

    GEOMETRIC_EVEN:    equal percentage gap between rungs, equal size per rung.
                       Best for wide ranges where you think in percentages.

    GEOMETRIC_PYRAMID: equal percentage gap between rungs, size weighted toward
                       the far end of the range (bottom for buys, top for sells).
                       Best for DCA accumulation and distribution — improves
                       average fill price on partial fills.
    """

    LINEAR_EVEN = "linear-even"
    GEOMETRIC_EVEN = "geometric-even"
    GEOMETRIC_PYRAMID = "geometric-pyramid"


def _generate_prices(
    price_low: Decimal,
    price_high: Decimal,
    num_orders: int,
    distribution: Distribution,
) -> List[Decimal]:
    """Generate price levels according to the distribution mode.

    For LINEAR_EVEN, rungs are evenly spaced in absolute price.
    For GEOMETRIC_EVEN and GEOMETRIC_PYRAMID, rungs are evenly spaced in
    log-price (equal percentage gap between consecutive rungs).
    """
    if num_orders == 1:
        return [(price_low + price_high) / Decimal(2)]

    if distribution == Distribution.LINEAR_EVEN:
        step = (price_high - price_low) / Decimal(num_orders - 1)
        return [price_low + step * Decimal(i) for i in range(num_orders)]

    # Geometric: P_i = low * r^i, r = (high/low)^(1/(N-1))
    ratio = (price_high / price_low) ** (Decimal(1) / Decimal(num_orders - 1))
    return [price_low * (ratio ** Decimal(i)) for i in range(num_orders)]


def _generate_size_weights(
    num_orders: int,
    distribution: Distribution,
    size_scale: Decimal,
    side: str,
) -> List[Decimal]:
    """Generate normalized size weights that sum to 1.0.

    Flat distributions return equal weights regardless of size_scale.
    GEOMETRIC_PYRAMID ramps linearly from 1.0 to size_scale across the N rungs:
      - side == "Bid" (buy): heavy at bottom (i=0, lowest price)
      - side == "Ask" (sell): heavy at top (i=N-1, highest price)
    """
    if num_orders == 1:
        return [Decimal(1)]

    is_pyramid = (
        distribution == Distribution.GEOMETRIC_PYRAMID and size_scale != Decimal(1)
    )
    if not is_pyramid:
        w = Decimal(1) / Decimal(num_orders)
        return [w] * num_orders

    # Linear ramp from 1.0 to size_scale across rungs
    raw: List[Decimal] = []
    for i in range(num_orders):
        t = Decimal(i) / Decimal(num_orders - 1)
        if side == "Bid":
            # Heavy at i=0 (lowest price)
            w = size_scale - (size_scale - Decimal(1)) * t
        else:
            # Heavy at i=N-1 (highest price)
            w = Decimal(1) + (size_scale - Decimal(1)) * t
        raw.append(w)

    total = sum(raw, Decimal(0))
    return [w / total for w in raw]


@dataclass
class TierPlan:
    """A pre-computed tiered order plan — all math done, ready to execute."""

    symbol: str
    side: str  # "Bid" or "Ask"
    distribution: Distribution
    size_scale: Decimal
    num_orders: int
    price_low: Decimal
    price_high: Decimal
    prices: List[Decimal]
    quantities: List[Decimal]
    values: List[Decimal]  # price * quantity per rung
    total_value: Decimal
    total_quantity: Decimal
    avg_fill_price: Decimal
    warnings: List[str] = field(default_factory=list)


@dataclass
class RiskTierPlan:
    """A tiered perp entry sized so that, if all rungs fill, the loss
    triggered by the stop-loss equals the user's risk budget exactly.

    Wraps a :class:`TierPlan` with the risk-side inputs (target avg, SL,
    risk amount) and derived metrics (worst-rung partial-fill loss,
    notional, distance to SL).
    """

    direction: Direction
    target_avg: Decimal
    sl_price: Decimal
    risk_amount: Decimal
    width_pct: Decimal  # half-width around target_avg, e.g. 0.02 = ±2%
    tier_plan: TierPlan
    notional: Decimal
    max_loss_if_all_filled: Decimal  # equals risk_amount by construction
    max_loss_if_only_worst_rung: Decimal  # if only the worst-priced rung fills
    distance_to_sl_pct: Decimal  # |actual_avg - sl| / actual_avg
    avg_drift_pct: Decimal  # (actual_avg - target_avg) / target_avg, signed


class Order:
    """Represents a trading order."""

    def __init__(self, order_data: Dict):
        """Initialize order from API response.

        Args:
            order_data: Order data from API
        """
        self.order_id = order_data.get("id")
        self.client_order_id = order_data.get("clientId")
        self.symbol = order_data.get("symbol")
        self.side = order_data.get("side")
        self.order_type = order_data.get("orderType")
        self.price = Decimal(str(order_data.get("price") or 0))
        self.quantity = Decimal(str(order_data.get("quantity") or 0))
        self.filled_quantity = Decimal(str(order_data.get("executedQuantity") or 0))
        self.executed_quote_quantity = Decimal(
            str(order_data.get("executedQuoteQuantity") or 0)
        )
        self.status = order_data.get("status")
        self.created_at = order_data.get("createdAt")

    @property
    def remaining_quantity(self) -> Decimal:
        """Get remaining unfilled quantity."""
        return self.quantity - self.filled_quantity

    @property
    def fill_percentage(self) -> float:
        """Get fill percentage."""
        if self.quantity == 0:
            return 0.0
        return float(self.filled_quantity / self.quantity * 100)

    def __repr__(self) -> str:
        """String representation of order."""
        return (f"Order({self.order_id}, {self.symbol}, {self.side}, "
                f"{self.order_type}, {self.quantity}@{self.price}, "
                f"filled: {self.fill_percentage:.1f}%)")


class OrderManager:
    """Manages order placement and tracking."""

    def __init__(self, client: BackpackClient):
        """Initialize order manager.

        Args:
            client: Backpack API client
        """
        self.client = client
        self.open_orders: Dict[str, Order] = {}
        self._orders_lock = threading.Lock()

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: Optional[Decimal] = None,
        quote_quantity: Optional[Decimal] = None,
    ) -> Optional[Order]:
        """Place a market order.

        Exactly one of ``quantity`` (base) or ``quote_quantity`` (quote, e.g.
        "spend $100") must be provided.
        """
        try:
            response = self.client.place_order(
                symbol=symbol,
                side=side,
                order_type="Market",
                quantity=quantity,
                quote_quantity=quote_quantity,
            )
            order = Order(response)
            with self._orders_lock:
                self.open_orders[order.order_id] = order
            return order
        except Exception as e:
            print(f"Error placing market order: {e}")
            return None

    def place_limit_order(self, symbol: str, side: str, quantity: Decimal, price: Decimal,
                         time_in_force: str = "GTC") -> Optional[Order]:
        """Place a limit order.

        Quantities and prices should be Decimal; floats are accepted but
        lose precision at the edge.
        """
        try:
            response = self.client.place_order(
                symbol=symbol,
                side=side,
                order_type="Limit",
                quantity=quantity,
                price=price,
                time_in_force=time_in_force
            )
            order = Order(response)
            with self._orders_lock:
                self.open_orders[order.order_id] = order
            return order
        except Exception as e:
            print(f"Error placing limit order: {e}")
            return None

    def buy_market(
        self,
        symbol: str,
        quantity: Optional[Decimal] = None,
        quote_quantity: Optional[Decimal] = None,
    ) -> Optional[Order]:
        """Market buy by base quantity or quote amount (e.g. "spend $100")."""
        return self.place_market_order(symbol, "Bid", quantity, quote_quantity)

    def sell_market(
        self,
        symbol: str,
        quantity: Optional[Decimal] = None,
        quote_quantity: Optional[Decimal] = None,
    ) -> Optional[Order]:
        """Market sell by base quantity or target quote receipt."""
        return self.place_market_order(symbol, "Ask", quantity, quote_quantity)

    def buy_limit(self, symbol: str, quantity: Decimal, price: Decimal) -> Optional[Order]:
        """Convenience method for limit buy."""
        return self.place_limit_order(symbol, "Bid", quantity, price)

    def sell_limit(self, symbol: str, quantity: Decimal, price: Decimal) -> Optional[Order]:
        """Convenience method for limit sell."""
        return self.place_limit_order(symbol, "Ask", quantity, price)

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel a specific order.

        Args:
            order_id: Order ID to cancel
            symbol: Trading pair symbol

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.cancel_order(symbol, order_id)
            with self._orders_lock:
                self.open_orders.pop(order_id, None)
            return True
        except Exception as e:
            print(f"Error canceling order: {e}")
            return False

    def cancel_all_orders(self, symbol: str) -> bool:
        """Cancel all open orders for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.cancel_all_orders(symbol)
            # Remove cancelled orders from local cache
            with self._orders_lock:
                for order_id in list(self.open_orders.keys()):
                    if self.open_orders[order_id].symbol == symbol:
                        self.open_orders.pop(order_id)
            return True
        except Exception as e:
            print(f"Error canceling all orders: {e}")
            return False

    def cancel_orders_in_price_range(self, symbol: str, price_low, price_high) -> tuple[int, int]:
        """Cancel orders within a specific price range.

        Args:
            symbol: Trading pair symbol
            price_low: Lower price bound (Decimal or float)
            price_high: Upper price bound (Decimal or float)

        Returns:
            Tuple of (successful_cancellations, total_orders_in_range)
        """
        # Fetch live orders from the exchange before filtering
        self.refresh_open_orders(symbol)

        low = price_low if isinstance(price_low, Decimal) else Decimal(str(price_low))
        high = price_high if isinstance(price_high, Decimal) else Decimal(str(price_high))
        with self._orders_lock:
            orders_in_range = [
                order for order in self.open_orders.values()
                if order.symbol == symbol and low <= order.price <= high
            ]

        if not orders_in_range:
            return 0, 0

        successful = 0
        total = len(orders_in_range)

        print(f"\nFound {total} order(s) in price range ${low:.4f} - ${high:.4f}")
        print("Canceling orders...")

        for order in orders_in_range:
            try:
                self.client.cancel_order(symbol, order.order_id)
                with self._orders_lock:
                    self.open_orders.pop(order.order_id, None)
                successful += 1
                print(f"  ✓ Cancelled: {order.side} {order.quantity:.4f} @ ${order.price:.4f} (ID: {order.order_id[:8]})")
            except Exception as e:
                print(f"  ✗ Failed to cancel order {order.order_id[:8]}: {e}")

        return successful, total

    def refresh_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Refresh open orders from API.

        Args:
            symbol: Optional symbol to filter

        Returns:
            List of open orders
        """
        try:
            orders_data = self.client.get_open_orders(symbol)
            orders = [Order(d) for d in orders_data]
            with self._orders_lock:
                self.open_orders.clear()
                for order in orders:
                    self.open_orders[order.order_id] = order
            return orders
        except Exception as e:
            print(f"Error refreshing orders: {e}")
            return []

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get open orders from local cache.

        Args:
            symbol: Optional symbol to filter

        Returns:
            List of open orders
        """
        with self._orders_lock:
            if symbol:
                return [o for o in self.open_orders.values() if o.symbol == symbol]
            return list(self.open_orders.values())

    def get_order_by_id(self, order_id: str) -> Optional[Order]:
        """Get order by ID.

        Args:
            order_id: Order ID

        Returns:
            Order object if found, None otherwise
        """
        with self._orders_lock:
            return self.open_orders.get(order_id)

    def _place_single_tiered_order(self, symbol: str, side: str, quantity: Decimal,
                                   price: Decimal, index: int, total: int) -> tuple[int, Optional[Order], str]:
        """Place a single order for tiered placement (used by thread pool).

        Returns:
            Tuple of (index, order_or_none, status_message)
        """
        try:
            order = self.place_limit_order(symbol, side, quantity, price)
            if order:
                return (index, order, f"  Order {index}/{total}: {quantity:.4f} @ ${price:.4f} - Placed (ID: {order.order_id})")
            else:
                return (index, None, f"  Order {index}/{total}: {quantity:.4f} @ ${price:.4f} - Failed")
        except Exception as e:
            return (index, None, f"  Order {index}/{total}: {quantity:.4f} @ ${price:.4f} - Error: {e}")

    def build_tier_plan(
        self,
        symbol: str,
        side: str,
        price_low,
        price_high,
        num_orders: int,
        total_value=None,
        total_quantity=None,
        distribution: Distribution = Distribution.GEOMETRIC_PYRAMID,
        size_scale=Decimal("1.5"),
    ) -> Optional[TierPlan]:
        """Compute a tiered order plan without placing any orders.

        Exactly one of ``total_value`` or ``total_quantity`` must be provided.
        - total_value: total to spend in quote currency (best for buys)
        - total_quantity: total base-currency amount (best for sells)

        Args:
            symbol: Trading pair symbol
            side: "Bid" for buy, "Ask" for sell
            price_low: Lower price bound
            price_high: Upper price bound
            num_orders: Number of rungs
            total_value: Total value in quote currency (optional)
            total_quantity: Total quantity in base currency (optional)
            distribution: Price/size distribution mode
            size_scale: Pyramid scale factor (1.0=flat, 1.5=mild, 3.0=aggressive)

        Returns:
            TierPlan on success, None on invalid input (error printed).
        """
        def _as_dec(x):
            return x if isinstance(x, Decimal) else Decimal(str(x))

        # Validation
        if (total_value is None) == (total_quantity is None):
            print("Must provide exactly one of total_value or total_quantity")
            return None
        if num_orders <= 0:
            print("Number of orders must be greater than 0")
            return None
        low = _as_dec(price_low)
        high = _as_dec(price_high)
        scale = _as_dec(size_scale)
        if low >= high:
            print("Lower price must be less than higher price")
            return None
        if low <= 0 or high <= 0:
            print("Prices must be greater than 0")
            return None
        if scale < Decimal(1):
            print("size_scale must be >= 1.0")
            return None
        if side not in ("Bid", "Ask"):
            print(f"Invalid side: {side} (expected 'Bid' or 'Ask')")
            return None
        if total_value is not None and _as_dec(total_value) <= 0:
            print("total_value must be > 0")
            return None
        if total_quantity is not None and _as_dec(total_quantity) <= 0:
            print("total_quantity must be > 0")
            return None

        warnings: List[str] = []

        # Safety rail: geometric spacing on narrow ranges degenerates to linear
        range_pct = (high - low) / low * Decimal(100)
        if distribution in (Distribution.GEOMETRIC_EVEN, Distribution.GEOMETRIC_PYRAMID):
            if range_pct < Decimal(2):
                warnings.append(
                    f"Range is only {range_pct:.2f}% — geometric spacing degenerates "
                    f"to linear at narrow ranges"
                )

        # Safety rail: aggressive pyramid approaches martingale territory
        if distribution == Distribution.GEOMETRIC_PYRAMID and scale > Decimal(3):
            warnings.append(
                f"size_scale={scale} is very aggressive (>3x) — approaches "
                f"martingale risk profile"
            )

        # Compute prices and weights
        prices = _generate_prices(low, high, num_orders, distribution)
        weights = _generate_size_weights(num_orders, distribution, scale, side)

        # Compute quantities and values
        if total_value is not None:
            tv = _as_dec(total_value)
            # value_i = total_value * w_i;  qty_i = value_i / price_i
            values = [tv * w for w in weights]
            quantities = [v / p for v, p in zip(values, prices)]
            total_v = tv
            total_q = sum(quantities, Decimal(0))
        else:
            tq = _as_dec(total_quantity)
            # qty_i = total_quantity * w_i
            quantities = [tq * w for w in weights]
            values = [q * p for q, p in zip(quantities, prices)]
            total_q = tq
            total_v = sum(values, Decimal(0))

        avg_fill = total_v / total_q if total_q > 0 else Decimal(0)

        # Safety rail: enforce exchange-side limits up front so the plan can
        # only be built if every rung will be acceptable to the matching
        # engine. This avoids the fail-after-partial-placement footgun where
        # the first N-1 rungs go in and the last one is rejected.
        try:
            limits = self.client.get_market_limits(symbol)
        except Exception:
            limits = {}

        min_quantity = limits.get("min_quantity")
        min_price = limits.get("min_price")
        max_price = limits.get("max_price")
        step_size = limits.get("step_size")

        min_qty_rung = min(quantities)
        if min_quantity is not None and min_qty_rung < min_quantity:
            print(
                f"Smallest rung qty {min_qty_rung} is below exchange "
                f"minQuantity {min_quantity} for {symbol} — increase "
                f"total or reduce number of orders"
            )
            return None
        if step_size is not None and step_size > 0 and min_qty_rung < step_size:
            print(
                f"Smallest rung qty {min_qty_rung} is below step size "
                f"{step_size} — it will round down to zero"
            )
            return None
        if min_price is not None and low < min_price:
            print(f"Lower price {low} is below exchange minPrice {min_price}")
            return None
        if max_price is not None and high > max_price:
            print(f"Upper price {high} exceeds exchange maxPrice {max_price}")
            return None

        # Soft signal: still surface dust warnings for visibility
        min_rung_value = min(values)
        if min_rung_value < Decimal(1):
            warnings.append(
                f"Smallest rung is ${float(min_rung_value):.4f} — may incur "
                f"disproportionate fees"
            )

        return TierPlan(
            symbol=symbol,
            side=side,
            distribution=distribution,
            size_scale=scale,
            num_orders=num_orders,
            price_low=low,
            price_high=high,
            prices=prices,
            quantities=quantities,
            values=values,
            total_value=total_v,
            total_quantity=total_q,
            avg_fill_price=avg_fill,
            warnings=warnings,
        )

    def execute_tier_plan(self, plan: TierPlan) -> List[Optional[Order]]:
        """Execute a pre-computed tier plan in parallel.

        The rate limiter in the API client serializes actual HTTP calls at
        safe intervals, so it's safe to fan out with up to 5 workers.

        Fail-fast: on the first rung failure, cancel any rungs that haven't
        started yet and stop submitting new work. Rungs already in-flight
        will complete normally. This avoids hammering the API with 29 more
        requests when the first one reveals a systemic issue (bad signing,
        insufficient balance, bad precision, etc).

        Returns:
            List of Order objects in rung order (None for failed or
            cancelled rungs).
        """
        n = plan.num_orders
        print(
            f"\nPlacing {n} tiered {plan.side} orders "
            f"({plan.distribution.value})..."
        )

        results: Dict[int, tuple[Optional[Order], str]] = {}
        max_workers = min(n, 5)
        first_error: Optional[str] = None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._place_single_tiered_order,
                    plan.symbol,
                    plan.side,
                    qty,
                    px,
                    i + 1,
                    n,
                ): i + 1
                for i, (px, qty) in enumerate(zip(plan.prices, plan.quantities))
            }
            for future in as_completed(futures):
                try:
                    idx, order, msg = future.result()
                except CancelledError:
                    continue
                results[idx] = (order, msg)
                if order is None and first_error is None:
                    first_error = msg.strip()
                    for f in futures:
                        f.cancel()

        orders: List[Optional[Order]] = []
        for i in range(1, n + 1):
            if i in results:
                order, msg = results[i]
                print(msg)
                orders.append(order)
            else:
                print(f"  Order {i}/{n}: skipped (aborted after earlier failure)")
                orders.append(None)

        successful = sum(1 for o in orders if o is not None)
        if first_error:
            attempted = len(results)
            skipped = n - attempted
            print(
                f"\nAborted after first failure: {first_error}\n"
                f"Placed {successful}/{attempted} attempted, {skipped} skipped."
            )
        else:
            print(f"\nPlaced {successful}/{n} orders successfully")
        return orders

    def place_tiered_orders(
        self,
        symbol: str,
        side: str,
        price_low,
        price_high,
        num_orders: int,
        total_value=None,
        total_quantity=None,
        distribution: Distribution = Distribution.GEOMETRIC_PYRAMID,
        size_scale=Decimal("1.5"),
    ) -> List[Optional[Order]]:
        """Build and execute a tiered order plan in one call.

        Convenience wrapper around :meth:`build_tier_plan` +
        :meth:`execute_tier_plan`. Callers that need to preview the plan or
        ask for confirmation should call the two methods separately.
        """
        plan = self.build_tier_plan(
            symbol,
            side,
            price_low,
            price_high,
            num_orders,
            total_value=total_value,
            total_quantity=total_quantity,
            distribution=distribution,
            size_scale=size_scale,
        )
        if plan is None:
            return []
        return self.execute_tier_plan(plan)

    def tiered_buy(
        self,
        symbol: str,
        total_value,
        price_low,
        price_high,
        num_orders: int,
        distribution: Distribution = Distribution.GEOMETRIC_PYRAMID,
        size_scale=Decimal("1.5"),
    ) -> List[Optional[Order]]:
        """Place tiered buy orders by total quote-currency value."""
        return self.place_tiered_orders(
            symbol,
            "Bid",
            price_low,
            price_high,
            num_orders,
            total_value=total_value,
            distribution=distribution,
            size_scale=size_scale,
        )

    def tiered_sell(
        self,
        symbol: str,
        total_quantity,
        price_low,
        price_high,
        num_orders: int,
        distribution: Distribution = Distribution.GEOMETRIC_PYRAMID,
        size_scale=Decimal("1.5"),
    ) -> List[Optional[Order]]:
        """Place tiered sell orders by total base-currency quantity."""
        return self.place_tiered_orders(
            symbol,
            "Ask",
            price_low,
            price_high,
            num_orders,
            total_quantity=total_quantity,
            distribution=distribution,
            size_scale=size_scale,
        )

    def build_risk_tier_plan(
        self,
        symbol: str,
        direction: Direction,
        target_avg,
        sl_price,
        risk_amount,
        width_pct,
        num_orders: int,
        distribution: Distribution = Distribution.LINEAR_EVEN,
        size_scale=Decimal("1.0"),
    ) -> Optional[RiskTierPlan]:
        """Build a perp entry ladder centered on ``target_avg`` and sized
        so that loss-if-stopped equals ``risk_amount``.

        Width semantics: half-width around ``target_avg`` — the ladder spans
        ``target_avg × (1 − width_pct)`` to ``target_avg × (1 + width_pct)``,
        so ``width_pct=0.02`` means ±2% around the target (4% total span).

        The ladder uses the existing ``build_tier_plan`` machinery for
        prices and weights. Total quantity is back-solved from the *actual*
        weighted-average fill price (which can drift a few bps from
        ``target_avg`` under non-flat distributions). Sizing off the actual
        avg makes risk = ``risk_amount`` exact when all rungs fill.

        Args:
            symbol: A perp symbol (must end in ``_PERP``).
            direction: Direction.LONG or Direction.SHORT.
            target_avg: Desired average entry price.
            sl_price: Stop-loss price. Must be on the loss side of target_avg.
            risk_amount: Quote-currency loss budget if SL fires after full fill.
            width_pct: Half-width as a fraction (0.02 for ±2%).
            num_orders: Number of rungs.
            distribution: Price/size distribution mode (defaults to
                LINEAR_EVEN — keeps actual_avg = target_avg exactly).
            size_scale: Pyramid scale factor (only meaningful for
                GEOMETRIC_PYRAMID).

        Returns:
            RiskTierPlan on success, None on invalid input (error printed).
        """
        def _as_dec(x):
            return x if isinstance(x, Decimal) else Decimal(str(x))

        if not symbol.endswith("_PERP"):
            print(
                f"Risk-tier plans are perp-only — '{symbol}' is not a "
                f"perp symbol. Use a *_PERP market."
            )
            return None

        target_avg = _as_dec(target_avg)
        sl_price = _as_dec(sl_price)
        risk_amount = _as_dec(risk_amount)
        width_pct = _as_dec(width_pct)
        size_scale = _as_dec(size_scale)

        if target_avg <= 0 or sl_price <= 0:
            print("Prices must be greater than 0")
            return None
        if risk_amount <= 0:
            print("Risk amount must be greater than 0")
            return None
        if num_orders <= 0:
            print("Number of orders must be greater than 0")
            return None
        if width_pct <= 0 or width_pct >= Decimal("0.5"):
            print("width_pct must be in (0, 0.5) — half-width around target_avg")
            return None

        # SL must be on the loss side of the target avg.
        if direction == Direction.LONG:
            if sl_price >= target_avg:
                print(
                    f"Long SL {sl_price} must be below target avg {target_avg}"
                )
                return None
            side = "Bid"
        elif direction == Direction.SHORT:
            if sl_price <= target_avg:
                print(
                    f"Short SL {sl_price} must be above target avg {target_avg}"
                )
                return None
            side = "Ask"
        else:
            print(f"Invalid direction: {direction}")
            return None

        low = target_avg * (Decimal(1) - width_pct)
        high = target_avg * (Decimal(1) + width_pct)

        # Reject if the worst-priced rung would already be a loss before
        # the SL even fires.
        if direction == Direction.LONG and low <= sl_price:
            print(
                f"Ladder low {low} crosses SL {sl_price} — reduce width_pct "
                f"or move SL further from target"
            )
            return None
        if direction == Direction.SHORT and high >= sl_price:
            print(
                f"Ladder high {high} crosses SL {sl_price} — reduce width_pct "
                f"or move SL further from target"
            )
            return None

        # Compute the ladder's actual weighted avg, then size total qty
        # so that Q × |actual_avg - SL| == risk_amount exactly.
        prices = _generate_prices(low, high, num_orders, distribution)
        weights = _generate_size_weights(num_orders, distribution, size_scale, side)
        actual_avg = sum((p * w for p, w in zip(prices, weights)), Decimal(0))
        distance = abs(actual_avg - sl_price)
        if distance <= 0:
            print("Actual average equals SL — distance is zero")
            return None

        total_qty = risk_amount / distance

        plan = self.build_tier_plan(
            symbol,
            side,
            low,
            high,
            num_orders,
            total_quantity=total_qty,
            distribution=distribution,
            size_scale=size_scale,
        )
        if plan is None:
            return None

        # Worst-priced rung = highest price for a long, lowest for a short.
        worst_idx = num_orders - 1 if direction == Direction.LONG else 0
        worst_qty = plan.quantities[worst_idx]
        worst_price = plan.prices[worst_idx]
        max_loss_worst = worst_qty * abs(worst_price - sl_price)

        avg_drift = (
            (plan.avg_fill_price - target_avg) / target_avg
            if target_avg > 0 else Decimal(0)
        )
        # Surface drift if the chosen distribution skews actual_avg far
        # from the user's target. LINEAR_EVEN/flat keeps it at zero.
        if abs(avg_drift) > Decimal("0.005"):
            plan.warnings.append(
                f"Actual avg ${float(plan.avg_fill_price):.4f} drifts "
                f"{float(avg_drift) * 100:+.2f}% from target "
                f"${float(target_avg):.4f} — switch to linear-even for an "
                f"exact match"
            )

        return RiskTierPlan(
            direction=direction,
            target_avg=target_avg,
            sl_price=sl_price,
            risk_amount=risk_amount,
            width_pct=width_pct,
            tier_plan=plan,
            notional=plan.total_value,
            max_loss_if_all_filled=risk_amount,
            max_loss_if_only_worst_rung=max_loss_worst,
            distance_to_sl_pct=distance / plan.avg_fill_price,
            avg_drift_pct=avg_drift,
        )

    def execute_risk_tier_plan(
        self, plan: RiskTierPlan
    ) -> Tuple[List[Optional[Order]], Optional[Order]]:
        """Execute the entry ladder, then place a single reduce-only stop.

        Order of operations: entries first (limit orders away from market,
        unlikely to fill in the sub-second window), then the SL. The SL
        is placed with ``triggerQuantity="100%"`` and ``reduceOnly=True``
        so it tracks the *actual* position size — works correctly whether
        zero, some, or all entry rungs filled.

        If SL placement itself fails, the partial position is unhedged.
        That case is surfaced loudly so the user can place SL manually.

        Returns:
            (entry_orders, sl_order) — sl_order is None if placement failed.
        """
        entry_orders = self.execute_tier_plan(plan.tier_plan)

        # SL closes a long with an Ask, a short with a Bid.
        sl_side = "Ask" if plan.direction == Direction.LONG else "Bid"

        try:
            response = self.client.place_order(
                symbol=plan.tier_plan.symbol,
                side=sl_side,
                order_type="Market",
                trigger_price=plan.sl_price,
                trigger_by="MarkPrice",
                trigger_quantity="100%",
                reduce_only=True,
                auto_lend_redeem=False,
            )
            sl_order = Order(response)
            with self._orders_lock:
                self.open_orders[sl_order.order_id] = sl_order
            print(
                f"\nStop-loss placed: trigger ${float(plan.sl_price):.4f} "
                f"(MarkPrice), reduceOnly, 100% of position"
            )
            return entry_orders, sl_order
        except Exception as e:
            print(
                f"\n⚠ FAILED to place stop-loss: {e}\n"
                f"⚠ Any filled entry rungs are UNHEDGED. Place a stop "
                f"manually at {plan.sl_price} immediately, or cancel "
                f"the entry ladder."
            )
            return entry_orders, None
