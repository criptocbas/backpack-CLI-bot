"""Utility helper functions."""

from decimal import Decimal, InvalidOperation
from typing import Optional, Union

Number = Union[Decimal, float, int]


def _to_float(value: Number) -> float:
    """Accept Decimal, float, or int and return a float for string formatting."""
    return float(value)


def format_price(price: Number, decimals: int = 4) -> str:
    """Format price for display."""
    return f"{_to_float(price):.{decimals}f}"


def format_quantity(quantity: Number, decimals: int = 4) -> str:
    """Format quantity for display."""
    return f"{_to_float(quantity):.{decimals}f}"


def format_percentage(value: Number, decimals: int = 2) -> str:
    """Format percentage for display."""
    return f"{_to_float(value):.{decimals}f}%"


def format_currency(value: Number, currency: str = "$", decimals: int = 2) -> str:
    """Format currency value for display."""
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
