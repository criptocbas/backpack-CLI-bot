"""Tests for get_market_precision — the order-path precision source.

The key guarantee: a *fetch failure* must propagate (so the order is never
sent with guessed precision), while a successful-but-sparse response still
falls back to sane defaults.
"""

import pytest

from api.backpack import BackpackClient, BackpackAPIError


def test_parses_tick_and_step_from_filters(client: BackpackClient, monkeypatch):
    monkeypatch.setattr(client, "get_market", lambda s: {
        "filters": {"price": {"tickSize": "0.001"},
                    "quantity": {"stepSize": "0.1"}}
    })
    assert client.get_market_precision("SOL_USDC") == ("0.001", "0.1")


def test_defaults_when_fields_missing(client: BackpackClient, monkeypatch):
    # Successful response, but no filters → benign 0.01 defaults.
    monkeypatch.setattr(client, "get_market", lambda s: {"filters": {}})
    assert client.get_market_precision("SOL_USDC") == ("0.01", "0.01")


def test_fetch_failure_propagates(client: BackpackClient, monkeypatch):
    def boom(symbol):
        raise BackpackAPIError("market fetch failed", status_code=500)

    monkeypatch.setattr(client, "get_market", boom)
    # Must NOT silently return ("0.01", "0.01") — that could misprice an order.
    with pytest.raises(BackpackAPIError):
        client.get_market_precision("SOL_USDC")
