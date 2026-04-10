"""Backpack Exchange API Client."""

import time
import base64
import threading
import requests
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode
from nacl.signing import SigningKey
from nacl.encoding import Base64Encoder
from decimal import Decimal, ROUND_DOWN


class BackpackClient:
    """Client for interacting with Backpack Exchange API."""

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.backpack.exchange"):
        """Initialize Backpack API client.

        Args:
            api_key: Base64-encoded ED25519 public key
            api_secret: Base64-encoded ED25519 private key (secret key)
            base_url: Base URL for API endpoints
        """
        self.api_key = api_key
        # Decode the base64-encoded private key and create SigningKey
        private_key_bytes = base64.b64decode(api_secret)
        self.signing_key = SigningKey(private_key_bytes)
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        })
        # Cache for market specifications (tick size, step size)
        self._market_cache: Dict[str, Dict] = {}
        self._market_cache_time: Dict[str, float] = {}
        self._market_cache_ttl = 300  # 5 minutes

        # Rate limiting: track last request time, enforce minimum gap
        self._last_request_time = 0.0
        self._min_request_interval = 0.2  # 5 req/s max
        self._rate_lock = threading.Lock()

        # Retry settings
        self._max_retries = 3
        self._retry_backoff_factor = 0.3
        self._retryable_status_codes = {429, 500, 502, 503, 504}

        # Valid symbols cache
        self._valid_symbols: Optional[set] = None
        self._valid_symbols_time: float = 0

    def _generate_signature(self, instruction: str, params: Optional[Dict] = None, window: int = 5000) -> tuple[str, str, str]:
        """Generate ED25519 signature for authenticated requests.

        Args:
            instruction: Instruction type (e.g., "balanceQuery", "orderExecute")
            params: Query or body parameters
            window: Validity window in milliseconds (default 5000, max 60000)

        Returns:
            Tuple of (signature, timestamp, window)
        """
        timestamp = str(int(time.time() * 1000))

        # Build the signing message according to Backpack documentation:
        # 1. Start with instruction
        # 2. Add alphabetically sorted parameters
        # 3. Append timestamp and window

        message_parts = [f"instruction={instruction}"]

        # Add parameters if they exist, alphabetically sorted
        if params:
            sorted_params = sorted(params.items())
            for key, value in sorted_params:
                message_parts.append(f"{key}={value}")

        # Append timestamp and window
        message_parts.append(f"timestamp={timestamp}")
        message_parts.append(f"window={window}")

        # Join with '&'
        signing_message = "&".join(message_parts)

        # Sign with ED25519 private key
        signed = self.signing_key.sign(signing_message.encode('utf-8'))

        # Base64 encode the signature
        signature = base64.b64encode(signed.signature).decode('utf-8')

        return signature, timestamp, str(window)

    def _wait_for_rate_limit(self):
        """Enforce minimum interval between API requests."""
        with self._rate_lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._min_request_interval:
                time.sleep(self._min_request_interval - elapsed)
            self._last_request_time = time.time()

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None,
                 instruction: Optional[str] = None) -> Dict:
        """Make HTTP request to Backpack API with rate limiting and retry.

        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            data: Request body
            instruction: Instruction type for signed requests (e.g., "balanceQuery")

        Returns:
            Response JSON data
        """
        url = f"{self.base_url}{endpoint}"
        last_exception = None

        for attempt in range(self._max_retries + 1):
            # Re-sign on each attempt (timestamp must be fresh)
            headers = {}
            if instruction:
                signature_params = {}
                if params:
                    signature_params.update(params)
                if data:
                    signature_params.update(data)

                signature, timestamp, window = self._generate_signature(
                    instruction, signature_params if signature_params else None
                )
                headers["X-Signature"] = signature
                headers["X-Timestamp"] = timestamp
                headers["X-Window"] = window

            self._wait_for_rate_limit()

            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                    headers=headers,
                    timeout=10
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if hasattr(e, 'response') else 0

                # Retry on retryable status codes
                if status_code in self._retryable_status_codes and attempt < self._max_retries:
                    delay = self._retry_backoff_factor * (2 ** attempt)
                    time.sleep(delay)
                    last_exception = e
                    continue

                # Build error message without exposing sensitive data
                error_msg = f"API request failed with status {status_code}"
                try:
                    if hasattr(e.response, 'text') and e.response.text and status_code != 401:
                        error_msg = f"{error_msg} - {e.response.text[:200]}"
                except Exception:
                    pass
                raise Exception(error_msg)
            except requests.exceptions.ConnectionError as e:
                # Retry on connection errors
                if attempt < self._max_retries:
                    delay = self._retry_backoff_factor * (2 ** attempt)
                    time.sleep(delay)
                    last_exception = e
                    continue
                raise Exception(f"API connection failed after {self._max_retries + 1} attempts: {str(e)}")
            except requests.exceptions.RequestException as e:
                raise Exception(f"API request failed: {str(e)}")

        raise Exception(f"API request failed after {self._max_retries + 1} attempts: {str(last_exception)}")

    # Public Endpoints

    def get_markets(self) -> List[Dict]:
        """Get all available markets with their specifications.

        Returns:
            List of market data including tick sizes and lot sizes
        """
        return self._request("GET", "/api/v1/markets")

    def get_valid_symbols(self) -> set:
        """Get the set of valid trading symbols, cached for 5 minutes.

        Returns:
            Set of valid symbol strings
        """
        now = time.time()
        if self._valid_symbols is not None and (now - self._valid_symbols_time) < self._market_cache_ttl:
            return self._valid_symbols

        markets = self.get_markets()
        self._valid_symbols = {m["symbol"] for m in markets if "symbol" in m}
        self._valid_symbols_time = now
        return self._valid_symbols

    def is_valid_symbol(self, symbol: str) -> bool:
        """Check if a symbol is a valid trading pair on the exchange.

        Args:
            symbol: Trading pair symbol to validate

        Returns:
            True if valid, False otherwise
        """
        try:
            return symbol in self.get_valid_symbols()
        except Exception:
            return False

    def get_market(self, symbol: str) -> Dict:
        """Get market information for a specific symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Market data including filters (tickSize, stepSize, etc.)
        """
        # Check cache first (with TTL)
        if symbol in self._market_cache:
            age = time.time() - self._market_cache_time.get(symbol, 0)
            if age < self._market_cache_ttl:
                return self._market_cache[symbol]

        # Fetch from API and cache
        market_data = self._request("GET", f"/api/v1/market", params={"symbol": symbol})
        self._market_cache[symbol] = market_data
        self._market_cache_time[symbol] = time.time()
        return market_data

    def get_market_precision(self, symbol: str) -> tuple[str, str]:
        """Get tick size and step size for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Tuple of (tick_size, step_size) as strings
        """
        try:
            market = self.get_market(symbol)
            filters = market.get("filters", {})

            # Extract from nested structure
            price_filters = filters.get("price", {})
            quantity_filters = filters.get("quantity", {})

            tick_size = price_filters.get("tickSize", "0.01")
            step_size = quantity_filters.get("stepSize", "0.01")

            return tick_size, step_size
        except Exception as e:
            # Default fallback values
            return "0.01", "0.01"

    def round_to_precision(self, value: float, precision: str) -> str:
        """Round a value to match exchange precision.

        Args:
            value: Value to round
            precision: Precision string (e.g., "0.01" for 2 decimals)

        Returns:
            Rounded value as string
        """
        # Convert to Decimal for precise rounding
        value_decimal = Decimal(str(value))
        precision_decimal = Decimal(precision)

        # Round down to nearest precision increment
        rounded = (value_decimal / precision_decimal).quantize(Decimal('1'), rounding=ROUND_DOWN) * precision_decimal

        # Format as string, removing trailing zeros
        result = str(rounded)
        if '.' in result:
            result = result.rstrip('0').rstrip('.')

        return result

    def get_ticker(self, symbol: str) -> Dict:
        """Get ticker information for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., "SOL_USDC")

        Returns:
            Ticker data
        """
        return self._request("GET", f"/api/v1/ticker", params={"symbol": symbol})

    def get_depth(self, symbol: str, limit: int = 20) -> Dict:
        """Get order book depth.

        Args:
            symbol: Trading pair symbol
            limit: Number of levels to return

        Returns:
            Order book data
        """
        return self._request("GET", f"/api/v1/depth", params={"symbol": symbol, "limit": limit})

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        """Get kline/candlestick data.

        Args:
            symbol: Trading pair symbol
            interval: Interval (1m, 5m, 1h, etc.)
            limit: Number of klines to return

        Returns:
            List of kline data
        """
        return self._request("GET", f"/api/v1/klines", params={
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        })

    # Private Endpoints (require authentication)

    def get_account(self) -> Dict:
        """Get account information including balances.

        Returns:
            Account data
        """
        return self._request("GET", "/api/v1/capital", instruction="balanceQuery")

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get all open orders.

        Args:
            symbol: Optional symbol to filter orders

        Returns:
            List of open orders
        """
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/api/v1/orders", params=params, instruction="orderQueryAll")

    def get_order(self, symbol: str, order_id: str) -> Dict:
        """Get specific order details.

        Args:
            symbol: Trading pair symbol
            order_id: Order ID

        Returns:
            Order details
        """
        return self._request("GET", f"/api/v1/order", params={
            "symbol": symbol,
            "orderId": order_id
        }, instruction="orderQuery")

    def place_order(self, symbol: str, side: str, order_type: str, quantity: float,
                   price: Optional[float] = None, time_in_force: str = "GTC",
                   client_order_id: Optional[str] = None) -> Dict:
        """Place a new order.

        Args:
            symbol: Trading pair symbol
            side: Order side ("Bid" for buy, "Ask" for sell)
            order_type: Order type ("Limit" or "Market")
            quantity: Order quantity
            price: Order price (required for limit orders)
            time_in_force: Time in force (GTC, IOC, FOK)
            client_order_id: Optional client order ID

        Returns:
            Order response
        """
        # Get market precision for this symbol
        tick_size, step_size = self.get_market_precision(symbol)

        # Round quantity to step size
        quantity_str = self.round_to_precision(quantity, step_size)

        data = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "quantity": quantity_str,
            "timeInForce": time_in_force
        }

        if price is not None:
            # Round price to tick size
            price_str = self.round_to_precision(price, tick_size)
            data["price"] = price_str

        if client_order_id:
            data["clientId"] = client_order_id

        return self._request("POST", "/api/v1/order", data=data, instruction="orderExecute")

    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """Cancel an order.

        Args:
            symbol: Trading pair symbol
            order_id: Order ID to cancel

        Returns:
            Cancellation response
        """
        return self._request("DELETE", "/api/v1/order", data={
            "symbol": symbol,
            "orderId": order_id
        }, instruction="orderCancel")

    def cancel_all_orders(self, symbol: str) -> Dict:
        """Cancel all open orders for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Cancellation response
        """
        return self._request("DELETE", "/api/v1/orders", data={
            "symbol": symbol
        }, instruction="orderCancelAll")

    def get_fills(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get recent fills/trades.

        Args:
            symbol: Optional symbol to filter
            limit: Number of fills to return

        Returns:
            List of fills
        """
        params = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/api/v1/fills", params=params, instruction="fillHistoryQueryAll")

    def get_positions(self) -> List[Dict]:
        """Get current positions.

        Returns:
            List of positions
        """
        return self._request("GET", "/api/v1/positions", instruction="positionQuery")
