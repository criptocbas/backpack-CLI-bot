#!/usr/bin/env python3
"""Test script to verify Backpack API connection."""

import sys
from api.backpack import BackpackClient
from config import config


def test_connection():
    """Test API connection and credentials."""
    print("Testing Backpack API connection...\n")

    # Check configuration
    if not config.validate():
        print("❌ Error: API credentials not configured!")
        print("Please set BACKPACK_API_KEY and BACKPACK_API_SECRET in .env file")
        return False

    print(f"✓ Configuration loaded")
    print(f"  API Base URL: {config.API_BASE_URL}")
    print(f"  Default Symbol: {config.DEFAULT_SYMBOL}")
    print()

    # Initialize client
    api_key, api_secret = config.get_api_credentials()
    client = BackpackClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=config.API_BASE_URL
    )
    print("✓ Backpack client initialized")
    print()

    # Test public endpoint (ticker)
    try:
        print(f"Testing public API (ticker for {config.DEFAULT_SYMBOL})...")
        ticker = client.get_ticker(config.DEFAULT_SYMBOL)
        last_price = float(ticker.get("lastPrice", 0))
        print(f"✓ Public API working")
        print(f"  Last Price: ${last_price:.4f}")
        print()
    except Exception as e:
        print(f"❌ Public API failed: {e}")
        return False

    # Test authenticated endpoint (account)
    try:
        print("Testing authenticated API (account info)...")
        account = client.get_account()
        print("✓ Authentication successful")
        print(f"  Account data retrieved")

        # Backpack returns balances as {asset: {available, locked, staked}}
        # (decimal strings), not a list under "balances".
        if isinstance(account, dict) and account:
            shown = 0
            print(f"\n  Balances:")
            for asset, bal in account.items():
                if not isinstance(bal, dict):
                    continue
                try:
                    available = float(bal.get("available", 0))
                except (TypeError, ValueError):
                    continue
                if available > 0:
                    print(f"    {asset}: {available}")
                    shown += 1
                    if shown >= 5:
                        break
        print()
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        print("\nPossible issues:")
        print("  - Invalid API key or secret")
        print("  - API key doesn't have required permissions")
        print("  - Network connectivity issues")
        return False

    # Test order book
    try:
        print(f"Testing order book (depth for {config.DEFAULT_SYMBOL})...")
        depth = client.get_depth(config.DEFAULT_SYMBOL, limit=5)
        bids = depth.get("bids", [])
        asks = depth.get("asks", [])

        if bids and asks:
            best_bid = float(bids[0][0]) if bids else 0
            best_ask = float(asks[0][0]) if asks else 0
            spread = best_ask - best_bid
            spread_pct = (spread / best_bid) * 100 if best_bid > 0 else 0

            print("✓ Order book retrieved")
            print(f"  Best Bid: ${best_bid:.4f}")
            print(f"  Best Ask: ${best_ask:.4f}")
            print(f"  Spread: ${spread:.4f} ({spread_pct:.3f}%)")
        print()
    except Exception as e:
        print(f"❌ Order book failed: {e}")
        return False

    print("=" * 50)
    print("✓ All tests passed! You're ready to trade.")
    print("=" * 50)
    print("\nRun 'python main.py' to start the trading bot.")

    return True


if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
