"""Utility helper functions."""


def format_price(price: float, decimals: int = 4) -> str:
    """Format price for display.

    Args:
        price: Price value
        decimals: Number of decimal places

    Returns:
        Formatted price string
    """
    return f"{price:.{decimals}f}"


def format_quantity(quantity: float, decimals: int = 4) -> str:
    """Format quantity for display.

    Args:
        quantity: Quantity value
        decimals: Number of decimal places

    Returns:
        Formatted quantity string
    """
    return f"{quantity:.{decimals}f}"


def format_percentage(value: float, decimals: int = 2) -> str:
    """Format percentage for display.

    Args:
        value: Percentage value
        decimals: Number of decimal places

    Returns:
        Formatted percentage string
    """
    return f"{value:.{decimals}f}%"


def format_currency(value: float, currency: str = "$", decimals: int = 2) -> str:
    """Format currency value for display.

    Args:
        value: Currency value
        currency: Currency symbol
        decimals: Number of decimal places

    Returns:
        Formatted currency string
    """
    return f"{currency}{value:,.{decimals}f}"


def parse_order_input(input_str: str) -> dict:
    """Parse order input string.

    Format: "quantity@price" or just "quantity"

    Args:
        input_str: Input string

    Returns:
        Dictionary with quantity and optional price

    Raises:
        ValueError: If the input format is invalid
    """
    stripped = input_str.strip()
    if not stripped:
        raise ValueError("Input is empty. Use format: quantity or quantity@price")

    parts = stripped.split("@")

    try:
        quantity = float(parts[0].strip())
    except ValueError:
        raise ValueError(f"Invalid quantity: '{parts[0].strip()}'. Must be a number")

    if len(parts) > 1:
        try:
            price = float(parts[1].strip())
        except ValueError:
            raise ValueError(f"Invalid price: '{parts[1].strip()}'. Must be a number")
    else:
        price = None

    return {"quantity": quantity, "price": price}
