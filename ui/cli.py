"""Command-line interface for Backpack CLI Bot."""

import sys
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from rich.console import Console
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from datetime import datetime

from api.backpack import BackpackClient
from core.order_manager import (
    OrderManager, Distribution, TierPlan, RiskTierPlan, Direction,
)
from utils.helpers import (
    format_price, format_quantity, format_percentage,
    format_currency, parse_order_input
)
from config import config


def _dec(raw: str, field: str) -> Decimal:
    """Parse a prompt input into Decimal, raising ValueError with a clear message."""
    try:
        return Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid {field}: '{raw}'. Must be a number")


class CLI:
    """Command-line interface for trading bot."""

    def __init__(self, client: BackpackClient):
        """Initialize CLI.

        Args:
            client: Backpack API client
        """
        self.client = client
        self.console = Console()
        self.order_manager = OrderManager(client)

        self.current_symbol = config.DEFAULT_SYMBOL
        self.running = False
        self.current_price: Optional[Decimal] = None
        self.balances = {}

        # Auto-refresh settings
        self.auto_refresh_interval = 10  # seconds
        self.last_refresh_time = 0
        self.refresh_lock = threading.Lock()

    def clear_screen(self):
        """Clear the terminal screen."""
        sys.stdout.write("\033c")
        sys.stdout.flush()

    def display_header(self) -> Panel:
        """Create header panel.

        Returns:
            Rich Panel with header info
        """
        # Calculate portfolio value from USDC/USDT balances
        portfolio_value = Decimal(0)
        for asset in ["USDC", "USDT"]:
            if asset in self.balances:
                portfolio_value += self.balances[asset].get("total", Decimal(0))

        # Calculate time since last refresh
        time_since_refresh = int(time.time() - self.last_refresh_time) if self.last_refresh_time > 0 else 0

        header_text = Text()
        header_text.append("Backpack Spot Trading Bot", style="bold cyan")
        header_text.append(f" | Symbol: ", style="white")
        header_text.append(f"{self.current_symbol}", style="bold yellow")
        header_text.append(f" | Price: ", style="white")
        if self.current_price is None or self.current_price <= 0:
            header_text.append("N/A", style="bold red")
        else:
            header_text.append(f"${format_price(self.current_price)}", style="bold white")
        header_text.append(f" | Portfolio: ", style="white")
        header_text.append(f"{format_currency(portfolio_value)}", style="bold green")
        header_text.append(f" | Updated: ", style="white")
        header_text.append(f"{time_since_refresh}s ago", style="dim")

        return Panel(header_text, border_style="blue")


    def display_orders(self) -> Table:
        """Create orders table.

        Returns:
            Rich Table with open orders
        """
        table = Table(title="Open Orders", show_header=True, header_style="bold magenta")
        table.add_column("ID", style="dim")
        table.add_column("Symbol", style="cyan")
        table.add_column("Side", style="white")
        table.add_column("Type", style="white")
        table.add_column("Quantity", justify="right")
        table.add_column("Price", justify="right")
        table.add_column("Filled", justify="right")

        orders = self.order_manager.get_open_orders()

        if not orders:
            table.add_row("No open orders", "-", "-", "-", "-", "-", "-")
        else:
            for order in orders:
                table.add_row(
                    order.order_id[:8],
                    order.symbol,
                    order.side,
                    order.order_type,
                    format_quantity(order.quantity),
                    format_price(order.price),
                    f"{format_percentage(order.fill_percentage)}"
                )

        return table

    def display_balances(self) -> Table:
        """Create balances table.

        Returns:
            Rich Table with balances
        """
        table = Table(title="Balances", show_header=True, header_style="bold magenta")
        table.add_column("Asset", style="cyan")
        table.add_column("Free", justify="right", style="green")
        table.add_column("Locked", justify="right", style="yellow")
        table.add_column("Lent", justify="right", style="magenta")
        table.add_column("Staked", justify="right", style="blue")
        table.add_column("Total", justify="right", style="white")

        if not self.balances:
            table.add_row("No balances", "-", "-", "-", "-", "-")
        else:
            for asset, balance in self.balances.items():
                if balance["total"] > 0:  # Only show non-zero balances
                    table.add_row(
                        asset,
                        format_quantity(balance["free"]),
                        format_quantity(balance["locked"]),
                        format_quantity(balance.get("lent", 0)),
                        format_quantity(balance.get("staked", 0)),
                        format_quantity(balance["total"])
                    )

        return table

    def display_help(self) -> Panel:
        """Create help panel.

        Returns:
            Rich Panel with keyboard shortcuts
        """
        help_text = Text()
        help_text.append("Keyboard Shortcuts:\n", style="bold yellow")
        help_text.append("b - Buy market | ", style="green")
        help_text.append("s - Sell market | ", style="red")
        help_text.append("l - Limit buy | ", style="cyan")
        help_text.append("k - Limit sell\n", style="magenta")
        help_text.append("tb - Tiered buy | ", style="bold cyan")
        help_text.append("ts - Tiered sell\n", style="bold magenta")
        help_text.append("tlong - Risk-sized perp long | ", style="bold green")
        help_text.append("tshort - Risk-sized perp short\n", style="bold red")
        help_text.append("o - Refresh orders | ", style="white")
        help_text.append("c - Cancel all orders | ", style="white")
        help_text.append("cr - Cancel price range\n", style="yellow")
        help_text.append("sym - Change symbol | ", style="yellow")
        help_text.append("r - Refresh all\n", style="white")
        help_text.append("h - Show help | ", style="white")
        help_text.append("q - Quit", style="white")

        return Panel(help_text, title="Help", border_style="yellow")

    def refresh_balances(self):
        """Refresh account balances from API.

        Backpack returns balances as decimal strings; we store them as
        Decimal to avoid binary-float drift when they're summed or compared
        against order sizes.
        """
        try:
            account_data = self.client.get_account()
            self.balances.clear()

            for asset, balance_data in account_data.items():
                if isinstance(balance_data, dict):
                    free = Decimal(str(balance_data.get("available") or 0))
                    locked = Decimal(str(balance_data.get("locked") or 0))
                    staked = Decimal(str(balance_data.get("staked") or 0))
                    lent = Decimal(str(balance_data.get("lent") or 0))
                    self.balances[asset] = {
                        "free": free,
                        "locked": locked,
                        "staked": staked,
                        "lent": lent,
                        "total": free + locked + staked + lent,
                    }

            # /api/v1/capital reports the spot wallet only; auto-lent
            # balances live on the collateral endpoint. Merge them in so
            # the dashboard and preflight see lent SOL/USDC/etc.
            try:
                collateral = self.client.get_collateral() or {}
                for entry in collateral.get("collateral", []) or []:
                    asset = entry.get("symbol")
                    if not asset:
                        continue
                    lent = Decimal(str(entry.get("lendQuantity") or 0))
                    if lent <= 0:
                        continue
                    bal = self.balances.setdefault(asset, {
                        "free": Decimal(0),
                        "locked": Decimal(0),
                        "staked": Decimal(0),
                        "lent": Decimal(0),
                        "total": Decimal(0),
                    })
                    bal["lent"] = lent
                    bal["total"] = bal["free"] + bal["locked"] + bal["staked"] + lent
            except Exception as e:
                print(f"Error refreshing collateral: {e}")
        except Exception as e:
            print(f"Error refreshing balances: {e}")

    def refresh_data(self, silent=False):
        """Refresh all data from API (parallel).

        Args:
            silent: If True, don't print error messages (for auto-refresh)
        """
        try:
            with self.refresh_lock:
                errors = []

                def _refresh_balances():
                    try:
                        self.refresh_balances()
                    except Exception as e:
                        errors.append(f"balances: {e}")

                def _refresh_orders():
                    try:
                        self.order_manager.refresh_open_orders()
                    except Exception as e:
                        errors.append(f"orders: {e}")

                def _refresh_ticker():
                    try:
                        ticker = self.client.get_ticker(self.current_symbol)
                        last = ticker.get("lastPrice")
                        self.current_price = Decimal(str(last)) if last else None
                    except Exception as e:
                        errors.append(f"ticker: {e}")

                with ThreadPoolExecutor(max_workers=3) as executor:
                    executor.submit(_refresh_balances)
                    executor.submit(_refresh_orders)
                    executor.submit(_refresh_ticker)

                self.last_refresh_time = time.time()

                if errors and not silent:
                    for err in errors:
                        self.console.print(f"[red]Refresh error ({err})[/red]")

        except Exception as e:
            if not silent:
                self.console.print(f"[red]Error refreshing data: {e}[/red]")

    def display_dashboard(self):
        """Display main dashboard."""
        self.clear_screen()

        # Display header
        self.console.print(self.display_header())
        self.console.print()

        # Display orders
        self.console.print(self.display_orders())
        self.console.print()

        # Display balances
        self.console.print(self.display_balances())
        self.console.print()

        # Display help
        self.console.print(self.display_help())

    def _split_symbol(self) -> tuple[str, str]:
        """Return (base, quote) for self.current_symbol."""
        parts = self.current_symbol.split("_", 1)
        if len(parts) != 2:
            return self.current_symbol, ""
        return parts[0], parts[1]

    def _free_balance(self, asset: str) -> Decimal:
        """Spendable balance for an asset: available + lent.

        Orders are placed with autoLendRedeem=True, so Backpack will
        auto-redeem lent balance to fill the order. Count lent funds
        as spendable for preflight to match the exchange's behavior.
        """
        bal = self.balances.get(asset)
        if not bal:
            return Decimal(0)
        return Decimal(str(bal.get("free", 0))) + Decimal(str(bal.get("lent", 0)))

    def _confirm(self, prompt: str) -> bool:
        """Explicit yes/no confirmation — accepts only 'y' / 'yes'."""
        answer = self.console.input(f"[yellow]{prompt} (y/n): [/yellow]").strip().lower()
        return answer in ("y", "yes")

    def _parse_market_amount(self, raw: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
        """Parse a market-order amount prompt.

        A leading '$' or a trailing 'q' means "this is quote currency"
        (quoteQuantity) — e.g. '$100' or '100q' for "spend 100 USDC".
        Otherwise the value is treated as a base-asset quantity.

        Returns (base_quantity, quote_quantity) — exactly one is non-None.
        """
        s = raw.strip()
        if not s:
            raise ValueError("Amount is empty")
        if s.startswith("$"):
            return None, _dec(s[1:], "amount")
        if s.endswith(("q", "Q")):
            return None, _dec(s[:-1], "amount")
        return _dec(s, "quantity"), None

    def handle_buy_market(self):
        """Handle market buy order (accepts base qty or '$<quote>' / '<quote>q')."""
        try:
            base, quote = self._split_symbol()
            self.console.print(
                f"[dim]Enter a base quantity (e.g. 1.5) or a quote amount "
                f"(e.g. $100 or 100q to spend {quote or 'quote'}).[/dim]"
            )
            raw = self.console.input("[green]Buy amount: [/green]")
            qty, quote_qty = self._parse_market_amount(raw)

            if (qty is not None and qty <= 0) or (quote_qty is not None and quote_qty <= 0):
                self.console.print("[red]Amount must be greater than 0[/red]")
                return

            # Preflight: balance check against the quote asset
            free_quote = self._free_balance(quote)
            need_quote = (
                quote_qty if quote_qty is not None
                else (qty * self.current_price if self.current_price else None)
            )
            if need_quote is not None and quote and free_quote < need_quote:
                self.console.print(
                    f"[red]Insufficient {quote} balance: "
                    f"have {free_quote}, need ~{need_quote}[/red]"
                )
                return

            # Confirmation
            if quote_qty is not None:
                summary = f"MARKET BUY spending {quote_qty} {quote or 'quote'} of {base or self.current_symbol}"
            else:
                est = f" (~${qty * self.current_price:.2f})" if self.current_price else ""
                summary = f"MARKET BUY {qty} {base or 'base'}{est}"
            self.console.print(f"\n[bold]{summary}[/bold]")
            if not self._confirm("Place this order?"):
                self.console.print("[yellow]Cancelled[/yellow]")
                return

            order = self.order_manager.buy_market(
                self.current_symbol, quantity=qty, quote_quantity=quote_qty
            )
            if order:
                self.console.print(f"[green]Market buy order placed: {order}[/green]")
            else:
                self.console.print("[red]Failed to place order[/red]")

        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        self.console.input("\nPress Enter to continue...")

    def handle_sell_market(self):
        """Handle market sell order (accepts base qty or '$<quote>' / '<quote>q')."""
        try:
            base, quote = self._split_symbol()
            self.console.print(
                f"[dim]Enter a base quantity (e.g. 1.5) or a target quote "
                f"amount (e.g. $100 or 100q) — with quote mode the engine "
                f"fills up to that notional.[/dim]"
            )
            raw = self.console.input("[red]Sell amount: [/red]")
            qty, quote_qty = self._parse_market_amount(raw)

            if (qty is not None and qty <= 0) or (quote_qty is not None and quote_qty <= 0):
                self.console.print("[red]Amount must be greater than 0[/red]")
                return

            # Preflight: for base-qty sells we can compare directly.
            # For quote-qty sells we conservatively check base >= quote/price.
            free_base = self._free_balance(base)
            if qty is not None:
                need_base = qty
            elif self.current_price and self.current_price > 0:
                need_base = quote_qty / self.current_price
            else:
                need_base = None

            if need_base is not None and base and free_base < need_base:
                self.console.print(
                    f"[red]Insufficient {base} balance: "
                    f"have {free_base}, need ~{need_base}[/red]"
                )
                return

            if quote_qty is not None:
                summary = f"MARKET SELL to receive ~{quote_qty} {quote or 'quote'} of {base or self.current_symbol}"
            else:
                est = f" (~${qty * self.current_price:.2f})" if self.current_price else ""
                summary = f"MARKET SELL {qty} {base or 'base'}{est}"
            self.console.print(f"\n[bold]{summary}[/bold]")
            if not self._confirm("Place this order?"):
                self.console.print("[yellow]Cancelled[/yellow]")
                return

            order = self.order_manager.sell_market(
                self.current_symbol, quantity=qty, quote_quantity=quote_qty
            )
            if order:
                self.console.print(f"[green]Market sell order placed: {order}[/green]")
            else:
                self.console.print("[red]Failed to place order[/red]")

        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        self.console.input("\nPress Enter to continue...")

    def handle_buy_limit(self):
        """Handle limit buy order."""
        try:
            input_str = self.console.input("[cyan]Enter quantity@price (e.g., 10@100.5): [/cyan]")
            input_data = parse_order_input(input_str)
            quantity: Decimal = input_data["quantity"]
            price: Optional[Decimal] = input_data["price"]

            if quantity <= 0 or price is None or price <= 0:
                self.console.print("[red]Invalid quantity or price[/red]")
                return

            base, quote = self._split_symbol()
            need_quote = quantity * price
            free_quote = self._free_balance(quote)
            if quote and free_quote < need_quote:
                self.console.print(
                    f"[red]Insufficient {quote} balance: "
                    f"have {free_quote}, need {need_quote}[/red]"
                )
                return

            order = self.order_manager.buy_limit(self.current_symbol, quantity, price)
            if order:
                self.console.print(f"[green]Limit buy order placed: {order}[/green]")
            else:
                self.console.print("[red]Failed to place order[/red]")

        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        self.console.input("\nPress Enter to continue...")

    def handle_sell_limit(self):
        """Handle limit sell order."""
        try:
            input_str = self.console.input("[magenta]Enter quantity@price (e.g., 10@100.5): [/magenta]")
            input_data = parse_order_input(input_str)
            quantity: Decimal = input_data["quantity"]
            price: Optional[Decimal] = input_data["price"]

            if quantity <= 0 or price is None or price <= 0:
                self.console.print("[red]Invalid quantity or price[/red]")
                return

            base, _quote = self._split_symbol()
            free_base = self._free_balance(base)
            if base and free_base < quantity:
                self.console.print(
                    f"[red]Insufficient {base} balance: "
                    f"have {free_base}, need {quantity}[/red]"
                )
                return

            order = self.order_manager.sell_limit(self.current_symbol, quantity, price)
            if order:
                self.console.print(f"[green]Limit sell order placed: {order}[/green]")
            else:
                self.console.print("[red]Failed to place order[/red]")

        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        self.console.input("\nPress Enter to continue...")

    def handle_cancel_all(self):
        """Handle cancel all orders."""
        try:
            confirm = self.console.input(f"[yellow]Cancel all orders for {self.current_symbol}? (y/n): [/yellow]")
            if confirm.lower() == 'y':
                success = self.order_manager.cancel_all_orders(self.current_symbol)
                if success:
                    self.console.print("[green]All orders cancelled[/green]")
                else:
                    self.console.print("[red]Failed to cancel orders[/red]")
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        self.console.input("\nPress Enter to continue...")

    def handle_cancel_price_range(self):
        """Handle cancel orders in price range."""
        try:
            self.console.print(f"[bold yellow]Cancel Orders in Price Range - {self.current_symbol}[/bold yellow]")
            self.console.print(f"Current price: ${format_price(self.current_price)}\n")

            # Prompt lower first (matches typical UI convention), then upper;
            # swap automatically if the user enters them the other way round.
            lower_str = self.console.input("[yellow]Enter lower price bound: [/yellow]")
            price_low = _dec(lower_str, "lower price")

            upper_str = self.console.input("[yellow]Enter upper price bound: [/yellow]")
            price_high = _dec(upper_str, "upper price")

            if price_low <= 0 or price_high <= 0:
                self.console.print("[red]Prices must be greater than 0[/red]")
                self.console.input("\nPress Enter to continue...")
                return

            if price_low > price_high:
                price_low, price_high = price_high, price_low

            # Show preview of orders that will be cancelled
            orders_in_range = [
                order for order in self.order_manager.get_open_orders(self.current_symbol)
                if price_low <= order.price <= price_high
            ]

            if not orders_in_range:
                self.console.print(
                    f"\n[yellow]No orders found in price range "
                    f"${price_low:.4f} - ${price_high:.4f}[/yellow]"
                )
                self.console.input("\nPress Enter to continue...")
                return

            # Display orders to be cancelled
            self.console.print(f"\n[bold]Orders to be cancelled:[/bold]")
            for order in orders_in_range:
                self.console.print(f"  {order.side} {order.quantity:.4f} @ ${order.price:.4f} (ID: {order.order_id[:8]})")

            # Confirm cancellation
            confirm = self.console.input(f"\n[yellow]Cancel {len(orders_in_range)} order(s)? (y/n): [/yellow]")
            if confirm.lower() != 'y':
                self.console.print("[yellow]Cancelled[/yellow]")
                self.console.input("\nPress Enter to continue...")
                return

            # Cancel orders
            successful, total = self.order_manager.cancel_orders_in_price_range(
                self.current_symbol, price_low, price_high
            )

            if successful == total:
                self.console.print(f"\n[bold green]Successfully cancelled {successful}/{total} orders[/bold green]")
            else:
                self.console.print(f"\n[bold yellow]Cancelled {successful}/{total} orders[/bold yellow]")

        except ValueError:
            self.console.print("[red]Invalid input. Please enter valid numbers.[/red]")
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        self.console.input("\nPress Enter to continue...")

    def handle_change_symbol(self):
        """Handle symbol change."""
        new_symbol = self.console.input(
            "[yellow]Enter new symbol (spot: BTC_USDC, perp: SOL_USDC_PERP): [/yellow]"
        )
        new_symbol = new_symbol.strip().upper()

        if "_" not in new_symbol:
            self.console.print("[red]Invalid symbol format. Use format: BASE_QUOTE[/red]")
            self.console.input("\nPress Enter to continue...")
            return

        self.console.print(f"[dim]Validating {new_symbol}...[/dim]")
        if not self.client.is_valid_symbol(new_symbol):
            self.console.print(f"[red]'{new_symbol}' is not a valid trading pair on Backpack Exchange[/red]")
            if new_symbol.endswith("_PERP") and new_symbol.count("_") == 1:
                base = new_symbol[: -len("_PERP")]
                self.console.print(
                    f"[yellow]Hint: Backpack perps are quoted in USDC — try "
                    f"'{base}_USDC_PERP'.[/yellow]"
                )
            self.console.input("\nPress Enter to continue...")
            return

        self.current_symbol = new_symbol
        self.console.print(f"[green]Symbol changed to {self.current_symbol}[/green]")
        self.refresh_data()
        self.console.input("\nPress Enter to continue...")

    def _prompt_distribution(self) -> tuple[Distribution, Decimal]:
        """Prompt user for distribution mode and size scale with sensible defaults.

        Returns:
            (Distribution, size_scale). size_scale is only meaningful for
            GEOMETRIC_PYRAMID; it's 1.0 for flat modes.
        """
        self.console.print("\n[dim]Distribution modes:[/dim]")
        self.console.print("[dim]  1 = linear-even       (equal $ gap, equal size)[/dim]")
        self.console.print("[dim]  2 = geometric-even    (equal % gap, equal size)[/dim]")
        self.console.print(
            "[dim]  3 = geometric-pyramid (equal % gap, weighted toward far end) "
            "[bold]\u2190 recommended for DCA[/bold][/dim]"
        )
        choice = self.console.input(
            "[yellow]Distribution [1/2/3] (default: 3): [/yellow]"
        ).strip() or "3"

        mode_map = {
            "1": Distribution.LINEAR_EVEN,
            "2": Distribution.GEOMETRIC_EVEN,
            "3": Distribution.GEOMETRIC_PYRAMID,
        }
        distribution = mode_map.get(choice, Distribution.GEOMETRIC_PYRAMID)

        size_scale = Decimal("1.0")
        if distribution == Distribution.GEOMETRIC_PYRAMID:
            scale_str = self.console.input(
                "[yellow]Size scale (1.0=flat, 1.5=mild, 3.0=aggressive; "
                "default: 1.5): [/yellow]"
            ).strip()
            if scale_str:
                size_scale = _dec(scale_str, "size scale")
                if size_scale < Decimal(1):
                    self.console.print("[yellow]size_scale clamped to 1.0[/yellow]")
                    size_scale = Decimal("1.0")
            else:
                size_scale = Decimal("1.5")

        return distribution, size_scale

    def _render_plan_preview(self, plan: TierPlan) -> None:
        """Render a rich preview of a TierPlan before execution."""
        range_pct = float((plan.price_high - plan.price_low) / plan.price_low * 100)

        self.console.print("\n[bold]Plan Preview:[/bold]")
        self.console.print(f"  Symbol:       {plan.symbol}")
        self.console.print(f"  Side:         {plan.side}")
        self.console.print(f"  Distribution: {plan.distribution.value}")
        if plan.distribution == Distribution.GEOMETRIC_PYRAMID:
            self.console.print(f"  Size scale:   {float(plan.size_scale):.2f}x")
        self.console.print(
            f"  Range:        ${float(plan.price_low):.4f} - "
            f"${float(plan.price_high):.4f} ({range_pct:+.2f}%)"
        )
        self.console.print(f"  Orders:       {plan.num_orders}")
        self.console.print(f"  Total value:  ~${float(plan.total_value):.2f}")
        self.console.print(f"  Total qty:    ~{float(plan.total_quantity):.4f}")
        self.console.print(
            f"  [bold]Avg fill:[/bold]     "
            f"[bold]${float(plan.avg_fill_price):.4f}[/bold]"
        )

        # Show per-rung breakdown in a compact table
        rung_table = Table(
            title="Rungs",
            show_header=True,
            header_style="bold magenta",
            title_style="dim",
        )
        rung_table.add_column("#", justify="right", style="dim")
        rung_table.add_column("Price", justify="right")
        rung_table.add_column("Quantity", justify="right")
        rung_table.add_column("Value", justify="right")

        min_val = min(plan.values)
        max_val = max(plan.values)

        for i, (px, qty, val) in enumerate(
            zip(plan.prices, plan.quantities, plan.values), 1
        ):
            tag = ""
            if val == max_val and plan.num_orders > 1:
                tag = " [green]heaviest[/green]"
            elif val == min_val and plan.num_orders > 1:
                tag = " [dim]lightest[/dim]"
            rung_table.add_row(
                str(i),
                f"${float(px):.4f}",
                f"{float(qty):.6f}",
                f"${float(val):.2f}{tag}",
            )

        self.console.print(rung_table)

        # Warnings
        for warning in plan.warnings:
            self.console.print(f"[yellow]\u26a0  {warning}[/yellow]")

    def handle_tiered_buy(self):
        """Handle tiered buy orders."""
        try:
            self.console.print("[bold cyan]Tiered Buy Orders[/bold cyan]")
            self.console.print(f"Current price: ${format_price(self.current_price)}\n")

            total_value = _dec(
                self.console.input("[green]Total value to buy (USD): [/green]"),
                "total value",
            )
            price_low = _dec(
                self.console.input("[green]Lower price bound: [/green]"),
                "lower price",
            )
            price_high = _dec(
                self.console.input("[green]Upper price bound: [/green]"),
                "upper price",
            )
            num_orders = int(
                self.console.input("[green]Number of orders: [/green]").strip()
            )

            if total_value <= 0 or price_low <= 0 or price_high <= 0 or num_orders <= 0:
                self.console.print("[red]All values must be greater than 0[/red]")
                self.console.input("\nPress Enter to continue...")
                return

            if price_low >= price_high:
                self.console.print(
                    "[red]Lower price must be less than upper price[/red]"
                )
                self.console.input("\nPress Enter to continue...")
                return

            base, quote = self._split_symbol()
            free_quote = self._free_balance(quote)
            if quote and free_quote < total_value:
                self.console.print(
                    f"[red]Insufficient {quote} balance: "
                    f"have {free_quote}, need {total_value}[/red]"
                )
                self.console.input("\nPress Enter to continue...")
                return

            distribution, size_scale = self._prompt_distribution()

            plan = self.order_manager.build_tier_plan(
                self.current_symbol,
                "Bid",
                price_low,
                price_high,
                num_orders,
                total_value=total_value,
                distribution=distribution,
                size_scale=size_scale,
            )
            if plan is None:
                self.console.print("[red]Failed to build plan[/red]")
                self.console.input("\nPress Enter to continue...")
                return

            self._render_plan_preview(plan)

            confirm = self.console.input("\n[yellow]Confirm? (y/n): [/yellow]")
            if confirm.lower() != 'y':
                self.console.print("[yellow]Cancelled[/yellow]")
                self.console.input("\nPress Enter to continue...")
                return

            orders = self.order_manager.execute_tier_plan(plan)
            successful = sum(1 for o in orders if o is not None)
            self.console.print(
                f"\n[bold green]Placed {successful}/{num_orders} orders "
                f"successfully[/bold green]"
            )

        except ValueError:
            self.console.print("[red]Invalid input. Please enter valid numbers.[/red]")
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        self.console.input("\nPress Enter to continue...")

    def handle_tiered_sell(self):
        """Handle tiered sell orders."""
        try:
            self.console.print("[bold magenta]Tiered Sell Orders[/bold magenta]")
            self.console.print(f"Current price: ${format_price(self.current_price)}\n")

            total_quantity = _dec(
                self.console.input(
                    "[red]Total quantity to sell (base currency): [/red]"
                ),
                "total quantity",
            )
            price_low = _dec(
                self.console.input("[red]Lower price bound: [/red]"),
                "lower price",
            )
            price_high = _dec(
                self.console.input("[red]Upper price bound: [/red]"),
                "upper price",
            )
            num_orders = int(
                self.console.input("[red]Number of orders: [/red]").strip()
            )

            if total_quantity <= 0 or price_low <= 0 or price_high <= 0 or num_orders <= 0:
                self.console.print("[red]All values must be greater than 0[/red]")
                self.console.input("\nPress Enter to continue...")
                return

            if price_low >= price_high:
                self.console.print(
                    "[red]Lower price must be less than upper price[/red]"
                )
                self.console.input("\nPress Enter to continue...")
                return

            base, _quote = self._split_symbol()
            free_base = self._free_balance(base)
            if base and free_base < total_quantity:
                self.console.print(
                    f"[red]Insufficient {base} balance: "
                    f"have {free_base}, need {total_quantity}[/red]"
                )
                self.console.input("\nPress Enter to continue...")
                return

            distribution, size_scale = self._prompt_distribution()

            plan = self.order_manager.build_tier_plan(
                self.current_symbol,
                "Ask",
                price_low,
                price_high,
                num_orders,
                total_quantity=total_quantity,
                distribution=distribution,
                size_scale=size_scale,
            )
            if plan is None:
                self.console.print("[red]Failed to build plan[/red]")
                self.console.input("\nPress Enter to continue...")
                return

            self._render_plan_preview(plan)

            confirm = self.console.input("\n[yellow]Confirm? (y/n): [/yellow]")
            if confirm.lower() != 'y':
                self.console.print("[yellow]Cancelled[/yellow]")
                self.console.input("\nPress Enter to continue...")
                return

            orders = self.order_manager.execute_tier_plan(plan)
            successful = sum(1 for o in orders if o is not None)
            self.console.print(
                f"\n[bold green]Placed {successful}/{num_orders} orders "
                f"successfully[/bold green]"
            )

        except ValueError:
            self.console.print("[red]Invalid input. Please enter valid numbers.[/red]")
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        self.console.input("\nPress Enter to continue...")

    def _render_risk_plan_preview(self, plan: RiskTierPlan) -> None:
        """Preview a risk-sized tier plan with the underlying ladder and
        the risk-side metrics (target avg, SL, max-loss scenarios)."""
        direction_label = "LONG" if plan.direction == Direction.LONG else "SHORT"
        side_label = "Bid" if plan.direction == Direction.LONG else "Ask"

        self.console.print("\n[bold]Risk-sized Plan Preview:[/bold]")
        self.console.print(f"  Symbol:        {plan.tier_plan.symbol}")
        self.console.print(
            f"  Direction:     [bold]{direction_label}[/bold] ({side_label})"
        )
        self.console.print(f"  Target avg:    ${float(plan.target_avg):.4f}")
        self.console.print(
            f"  Actual avg:    ${float(plan.tier_plan.avg_fill_price):.4f} "
            f"({float(plan.avg_drift_pct) * 100:+.3f}%)"
        )
        self.console.print(
            f"  Stop-loss:     ${float(plan.sl_price):.4f} "
            f"({float(plan.distance_to_sl_pct) * 100:.2f}% from avg)"
        )
        self.console.print(
            f"  Width:         ±{float(plan.width_pct) * 100:.2f}% "
            f"around target"
        )
        self.console.print(
            f"  Risk budget:   ${float(plan.risk_amount):.2f}"
        )
        self.console.print(
            f"  Total qty:     {float(plan.tier_plan.total_quantity):.6f}"
        )
        self.console.print(
            f"  Notional:      ${float(plan.notional):.2f}"
        )
        self.console.print(
            f"  Max loss (all rungs filled, SL hit):  "
            f"[bold]${float(plan.max_loss_if_all_filled):.2f}[/bold]"
        )
        self.console.print(
            f"  Max loss (only worst rung filled):    "
            f"${float(plan.max_loss_if_only_worst_rung):.2f}"
        )

        # Reuse the existing rung-table renderer for the underlying ladder.
        self._render_plan_preview(plan.tier_plan)

    def _show_account_leverage(self) -> None:
        """Best-effort display of account-wide leverage. Silent on failure."""
        try:
            settings = self.client.get_account_settings() or {}
            lev = settings.get("leverageLimit")
            if lev is not None:
                self.console.print(
                    f"[dim]Account leverage limit: {lev}x "
                    f"(account-wide; change via Backpack settings)[/dim]"
                )
        except Exception:
            pass

    def _handle_risk_tiered(self, direction: Direction):
        """Shared flow for tlong / tshort.

        Prompts for target avg, SL, risk, width, # rungs; previews the plan
        with risk metrics; on confirm, places the entry ladder then a single
        reduce-only stop tracking 100% of the resulting position.
        """
        try:
            label = "LONG" if direction == Direction.LONG else "SHORT"
            color = "green" if direction == Direction.LONG else "red"
            self.console.print(
                f"[bold {color}]Risk-sized Tiered {label} Entry "
                f"(perps only)[/bold {color}]"
            )
            self.console.print(
                f"Symbol: {self.current_symbol} | "
                f"Current price: ${format_price(self.current_price)}"
            )
            self._show_account_leverage()
            self.console.print()

            if not self.current_symbol.endswith("_PERP"):
                self.console.print(
                    f"[red]{self.current_symbol} is not a perp market. "
                    f"Switch with 'sym' to a *_USDC_PERP symbol "
                    f"(e.g., SOL_USDC_PERP) first.[/red]"
                )
                self.console.input("\nPress Enter to continue...")
                return

            target_avg = _dec(
                self.console.input(
                    f"[{color}]Target average entry: [/{color}]"
                ),
                "target average",
            )
            sl_price = _dec(
                self.console.input(f"[{color}]Stop-loss price: [/{color}]"),
                "stop-loss",
            )
            risk_amount = _dec(
                self.console.input(
                    f"[{color}]Risk amount (USD to lose if SL hits): "
                    f"[/{color}]"
                ),
                "risk amount",
            )
            width_str = self.console.input(
                f"[{color}]Half-width % around target (e.g. 2 for "
                f"±2%): [/{color}]"
            ).strip()
            width_pct = _dec(width_str, "width") / Decimal(100)
            num_orders = int(
                self.console.input(
                    f"[{color}]Number of rungs: [/{color}]"
                ).strip()
            )

            distribution, size_scale = self._prompt_distribution()

            plan = self.order_manager.build_risk_tier_plan(
                symbol=self.current_symbol,
                direction=direction,
                target_avg=target_avg,
                sl_price=sl_price,
                risk_amount=risk_amount,
                width_pct=width_pct,
                num_orders=num_orders,
                distribution=distribution,
                size_scale=size_scale,
            )
            if plan is None:
                self.console.print("[red]Failed to build plan[/red]")
                self.console.input("\nPress Enter to continue...")
                return

            self._render_risk_plan_preview(plan)

            self.console.print(
                "\n[dim]Confirming will place the entry ladder, then a "
                "MarkPrice stop-loss with reduceOnly=true and "
                "triggerQuantity=100% (auto-tracks position size).[/dim]"
            )
            confirm = self.console.input("\n[yellow]Confirm? (y/n): [/yellow]")
            if confirm.lower() != 'y':
                self.console.print("[yellow]Cancelled[/yellow]")
                self.console.input("\nPress Enter to continue...")
                return

            entries, sl = self.order_manager.execute_risk_tier_plan(plan)
            successful = sum(1 for o in entries if o is not None)
            self.console.print(
                f"\n[bold green]Entry rungs placed: "
                f"{successful}/{num_orders}[/bold green]"
            )
            if sl is not None:
                self.console.print(
                    f"[bold green]Stop-loss order ID: "
                    f"{sl.order_id}[/bold green]"
                )
            else:
                self.console.print(
                    f"[bold red]Stop-loss FAILED — see warning above[/bold red]"
                )

        except ValueError:
            self.console.print(
                "[red]Invalid input. Please enter valid numbers.[/red]"
            )
        except Exception as e:
            self.console.print(f"[red]Error: {e}[/red]")

        self.console.input("\nPress Enter to continue...")

    def handle_tiered_long(self):
        """Handle a risk-sized tiered long entry on a perp market."""
        self._handle_risk_tiered(Direction.LONG)

    def handle_tiered_short(self):
        """Handle a risk-sized tiered short entry on a perp market."""
        self._handle_risk_tiered(Direction.SHORT)

    def _auto_refresh_worker(self):
        """Background worker that auto-refreshes data."""
        while self.running:
            time.sleep(self.auto_refresh_interval)
            if self.running:
                self.refresh_data(silent=True)

    def run(self):
        """Run the CLI interface."""
        self.running = True

        # Validate configuration
        if not config.validate():
            self.console.print("[red]Error: API credentials not configured![/red]")
            self.console.print("[yellow]Please set BACKPACK_API_KEY and BACKPACK_API_SECRET in .env file[/yellow]")
            return

        self.console.print("[cyan]Starting Backpack CLI Bot...[/cyan]")
        self.console.print("[dim]Auto-refresh enabled: Updates every 10 seconds[/dim]")
        self.refresh_data()

        # Start auto-refresh background thread
        refresh_thread = threading.Thread(target=self._auto_refresh_worker, daemon=True)
        refresh_thread.start()

        while self.running:
            self.display_dashboard()

            # Get user command
            command = self.console.input("\n[bold cyan]Command: [/bold cyan]").strip().lower()

            if command == 'b':
                self.handle_buy_market()
            elif command == 's':
                self.handle_sell_market()
            elif command == 'l':
                self.handle_buy_limit()
            elif command == 'k':
                self.handle_sell_limit()
            elif command == 'tb':
                self.handle_tiered_buy()
            elif command == 'ts':
                self.handle_tiered_sell()
            elif command == 'tlong':
                self.handle_tiered_long()
            elif command == 'tshort':
                self.handle_tiered_short()
            elif command == 'o':
                self.order_manager.refresh_open_orders()
                self.console.print("[green]Orders refreshed[/green]")
                self.console.input("\nPress Enter to continue...")
            elif command == 'c':
                self.handle_cancel_all()
            elif command == 'cr':
                self.handle_cancel_price_range()
            elif command == 'sym':
                self.handle_change_symbol()
            elif command == 'r':
                self.refresh_data()
                self.console.print("[green]All data refreshed[/green]")
                self.console.input("\nPress Enter to continue...")
            elif command == 'h':
                self.console.input("\nPress Enter to continue...")
            elif command == 'q':
                self.running = False
                self.console.print("[cyan]Goodbye![/cyan]")
            else:
                self.console.print("[red]Unknown command. Press 'h' for help.[/red]")
                self.console.input("\nPress Enter to continue...")
