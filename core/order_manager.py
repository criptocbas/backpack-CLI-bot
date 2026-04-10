"""Order management system."""

import threading
from typing import Dict, List, Optional
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
from api.backpack import BackpackClient


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
        self.price = Decimal(str(order_data.get("price", 0)))
        self.quantity = Decimal(str(order_data.get("quantity", 0)))
        self.filled_quantity = Decimal(str(order_data.get("executedQuantity", 0)))
        self.status = order_data.get("status")
        self.timestamp = order_data.get("timestamp")

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

    def place_market_order(self, symbol: str, side: str, quantity: float) -> Optional[Order]:
        """Place a market order.

        Args:
            symbol: Trading pair symbol
            side: "Bid" for buy, "Ask" for sell
            quantity: Order quantity

        Returns:
            Order object if successful, None otherwise
        """
        try:
            response = self.client.place_order(
                symbol=symbol,
                side=side,
                order_type="Market",
                quantity=quantity
            )
            order = Order(response)
            with self._orders_lock:
                self.open_orders[order.order_id] = order
            return order
        except Exception as e:
            print(f"Error placing market order: {e}")
            return None

    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float,
                         time_in_force: str = "GTC") -> Optional[Order]:
        """Place a limit order.

        Args:
            symbol: Trading pair symbol
            side: "Bid" for buy, "Ask" for sell
            quantity: Order quantity
            price: Order price
            time_in_force: Time in force (GTC, IOC, FOK)

        Returns:
            Order object if successful, None otherwise
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

    def buy_market(self, symbol: str, quantity: float) -> Optional[Order]:
        """Convenience method for market buy.

        Args:
            symbol: Trading pair symbol
            quantity: Order quantity

        Returns:
            Order object if successful
        """
        return self.place_market_order(symbol, "Bid", quantity)

    def sell_market(self, symbol: str, quantity: float) -> Optional[Order]:
        """Convenience method for market sell.

        Args:
            symbol: Trading pair symbol
            quantity: Order quantity

        Returns:
            Order object if successful
        """
        return self.place_market_order(symbol, "Ask", quantity)

    def buy_limit(self, symbol: str, quantity: float, price: float) -> Optional[Order]:
        """Convenience method for limit buy.

        Args:
            symbol: Trading pair symbol
            quantity: Order quantity
            price: Order price

        Returns:
            Order object if successful
        """
        return self.place_limit_order(symbol, "Bid", quantity, price)

    def sell_limit(self, symbol: str, quantity: float, price: float) -> Optional[Order]:
        """Convenience method for limit sell.

        Args:
            symbol: Trading pair symbol
            quantity: Order quantity
            price: Order price

        Returns:
            Order object if successful
        """
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

    def cancel_orders_in_price_range(self, symbol: str, price_low: float, price_high: float) -> tuple[int, int]:
        """Cancel orders within a specific price range.

        Args:
            symbol: Trading pair symbol
            price_low: Lower price bound
            price_high: Upper price bound

        Returns:
            Tuple of (successful_cancellations, total_orders_in_range)
        """
        # Fetch live orders from the exchange before filtering
        self.refresh_open_orders(symbol)

        low = Decimal(str(price_low))
        high = Decimal(str(price_high))
        with self._orders_lock:
            orders_in_range = [
                order for order in self.open_orders.values()
                if order.symbol == symbol and low <= order.price <= high
            ]

        if not orders_in_range:
            return 0, 0

        successful = 0
        total = len(orders_in_range)

        print(f"\nFound {total} order(s) in price range ${price_low:.4f} - ${price_high:.4f}")
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

    def _place_single_tiered_order(self, symbol: str, side: str, quantity: float,
                                   price: float, index: int, total: int) -> tuple[int, Optional[Order], str]:
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

    def place_tiered_orders(self, symbol: str, side: str, price_low: float, price_high: float,
                           num_orders: int, total_value: Optional[float] = None,
                           total_quantity: Optional[float] = None) -> List[Optional[Order]]:
        """Place multiple tiered limit orders across a price range (parallel).

        Exactly one of `total_value` or `total_quantity` must be provided.
        - total_value: total to spend in quote currency; quantity per level is derived per-price
        - total_quantity: total base-currency amount; split evenly across levels

        Args:
            symbol: Trading pair symbol
            side: "Bid" for buy, "Ask" for sell
            price_low: Lower price bound
            price_high: Higher price bound
            num_orders: Number of orders to place
            total_value: Total value in quote currency (optional)
            total_quantity: Total quantity in base currency (optional)

        Returns:
            List of Order objects (None for failed orders)
        """
        if (total_value is None) == (total_quantity is None):
            print("Must provide exactly one of total_value or total_quantity")
            return []

        if num_orders <= 0:
            print("Number of orders must be greater than 0")
            return []

        if price_low >= price_high:
            print("Lower price must be less than higher price")
            return []

        # Calculate price levels
        if num_orders == 1:
            prices = [(price_low + price_high) / 2]
        else:
            price_step = (price_high - price_low) / (num_orders - 1)
            prices = [price_low + (i * price_step) for i in range(num_orders)]

        # Build order specs and header based on mode
        if total_value is not None:
            value_per_order = total_value / num_orders
            order_specs = [(i, value_per_order / price, price) for i, price in enumerate(prices, 1)]
            print(f"\nPlacing {num_orders} tiered {side} orders:")
            print(f"Price range: ${price_low:.4f} - ${price_high:.4f}")
            print(f"Total value: ${total_value:.2f} (${value_per_order:.2f} per order)\n")
        else:
            quantity_per_order = total_quantity / num_orders
            order_specs = [(i, quantity_per_order, price) for i, price in enumerate(prices, 1)]
            print(f"\nPlacing {num_orders} tiered {side} orders:")
            print(f"Price range: ${price_low:.4f} - ${price_high:.4f}")
            print(f"Total quantity: {total_quantity:.4f} ({quantity_per_order:.4f} per order)\n")

        # Place orders in parallel (rate limiter in API client handles spacing)
        results: Dict[int, tuple[Optional[Order], str]] = {}
        max_workers = min(num_orders, 5)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._place_single_tiered_order,
                    symbol, side, qty, px, idx, num_orders
                ): idx
                for idx, qty, px in order_specs
            }
            for future in as_completed(futures):
                idx, order, msg = future.result()
                results[idx] = (order, msg)

        # Print results in order
        orders = []
        for idx, _qty, _px in order_specs:
            order, msg = results[idx]
            print(msg)
            orders.append(order)

        successful = sum(1 for o in orders if o is not None)
        print(f"\nPlaced {successful}/{num_orders} orders successfully")

        return orders

    def tiered_buy(self, symbol: str, total_value: float, price_low: float,
                   price_high: float, num_orders: int) -> List[Optional[Order]]:
        """Place tiered buy orders by total quote-currency value."""
        return self.place_tiered_orders(
            symbol, "Bid", price_low, price_high, num_orders, total_value=total_value
        )

    def tiered_sell(self, symbol: str, total_quantity: float, price_low: float,
                    price_high: float, num_orders: int) -> List[Optional[Order]]:
        """Place tiered sell orders by total base-currency quantity."""
        return self.place_tiered_orders(
            symbol, "Ask", price_low, price_high, num_orders, total_quantity=total_quantity
        )
