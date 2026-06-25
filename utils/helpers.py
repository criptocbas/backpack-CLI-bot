"""Utility helper functions."""

from decimal import Decimal, InvalidOperation
from typing import Optional, Union

Number = Union[Decimal, float, int]


# Placeholder shown when a value is missing (e.g. ticker not yet fetched).
NA = "N/A"


def _to_float(value: Number) -> float:
    """Accept Decimal, float, or int and return a float for string formatting."""
    return float(value)


def format_price(price: Optional[Number], decimals: int = 4) -> str:
    """Format price for display. ``None`` (e.g. a missing ticker) renders as N/A."""
    if price is None:
        return NA
    return f"{_to_float(price):.{decimals}f}"


def format_quantity(quantity: Optional[Number], decimals: int = 4) -> str:
    """Format quantity for display. ``None`` renders as N/A."""
    if quantity is None:
        return NA
    return f"{_to_float(quantity):.{decimals}f}"


def format_percentage(value: Optional[Number], decimals: int = 2) -> str:
    """Format percentage for display. ``None`` renders as N/A."""
    if value is None:
        return NA
    return f"{_to_float(value):.{decimals}f}%"


def format_currency(value: Optional[Number], currency: str = "$", decimals: int = 2) -> str:
    """Format currency value for display. ``None`` renders as N/A."""
    if value is None:
        return NA
    return f"{currency}{_to_float(value):,.{decimals}f}"


def _parse_decimal(raw: str, field_name: str) -> Decimal:
    """Parse a user-entered number string into Decimal, or raise a clear ValueError."""
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid {field_name}: '{raw}'. Must be a number")


def parse_order_input(input_str: str) -> dict:
    """Parse order input string into Decimals.

    Format: "quantity@price" or just "quantity"

    Returns:
        Dictionary with "quantity" (Decimal) and "price" (Decimal or None).

    Raises:
        ValueError: If the input format is invalid.
    """
    stripped = input_str.strip()
    if not stripped:
        raise ValueError("Input is empty. Use format: quantity or quantity@price")

    parts = stripped.split("@")

    quantity = _parse_decimal(parts[0].strip(), "quantity")
    price: Optional[Decimal]
    if len(parts) > 1:
        price = _parse_decimal(parts[1].strip(), "price")
    else:
        price = None

    return {"quantity": quantity, "price": price}
