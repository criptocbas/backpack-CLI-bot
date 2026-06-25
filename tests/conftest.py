"""Shared pytest fixtures.

These tests cover only the pure, money-deciding logic — distribution math,
precision rounding, request signing, and risk-tier sizing. Nothing here
touches the network: the OrderManager build_* methods are exercised with a
stub client, and execution paths (which place real orders) are never called.
"""

import base64

import pytest
from decimal import Decimal
from nacl.signing import SigningKey

from api.backpack import BackpackClient
from core.order_manager import OrderManager


@pytest.fixture
def signing_key() -> SigningKey:
    """A throwaway ED25519 keypair for signature tests."""
    return SigningKey.generate()


@pytest.fixture
def client(signing_key: SigningKey) -> BackpackClient:
    """A BackpackClient wired to the throwaway key (no network is touched)."""
    api_secret = base64.b64encode(signing_key.encode()).decode()
    api_key = base64.b64encode(signing_key.verify_key.encode()).decode()
    return BackpackClient(api_key=api_key, api_secret=api_secret)


class FakeLimitsClient:
    """Minimal stand-in for BackpackClient used when *building* tier plans.

    ``build_tier_plan`` only calls ``get_market_limits``; with permissive
    (all-None) limits no rung is filtered, so the plan reflects the pure
    distribution math. Pass explicit limits to test the exchange-limit rails.
    """

    DEFAULT_LIMITS = {
        "tick_size": Decimal("0.01"),
        "step_size": Decimal("0.0001"),
        "min_price": None,
        "max_price": None,
        "min_quantity": None,
        "max_quantity": None,
    }

    def __init__(self, limits: dict | None = None):
        self._limits = limits if limits is not None else dict(self.DEFAULT_LIMITS)

    def get_market_limits(self, symbol: str) -> dict:
        return dict(self._limits)


@pytest.fixture
def manager() -> OrderManager:
    """An OrderManager backed by a permissive stub client."""
    return OrderManager(FakeLimitsClient())


@pytest.fixture
def manager_factory():
    """Build an OrderManager whose stub client returns the given limits.

    Use when a test needs to exercise the exchange-limit rails (min qty,
    min/max price) rather than the permissive defaults.
    """
    def _make(limits: dict | None = None) -> OrderManager:
        return OrderManager(FakeLimitsClient(limits))

    return _make
