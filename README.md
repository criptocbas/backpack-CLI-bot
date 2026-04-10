# Backpack CLI Bot

A terminal-based trading tool for [Backpack Exchange](https://backpack.exchange) built for one specific job: **placing DCA-style ladders of limit orders across a price range without clicking through the exchange UI twenty times**.

It is not a high-frequency trading bot. It is a fast, keyboard-driven CLI for building and managing tiered limit-order ladders on spot markets.

---

## Contents

- [What it's for](#what-its-for)
- [Features](#features)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the bot](#running-the-bot)
- [Command reference](#command-reference)
- [Tiered orders (the flagship feature)](#tiered-orders-the-flagship-feature)
  - [Distribution modes](#distribution-modes)
  - [Worked example](#worked-example)
  - [Safety rails](#safety-rails)
- [Cancelling orders by price range](#cancelling-orders-by-price-range)
- [Reliability features](#reliability-features)
- [Testing the connection](#testing-the-connection)
- [Project structure](#project-structure)
- [Security](#security)

---

## What it's for

If your workflow looks like any of these, this tool will save you a lot of clicks:

- **Accumulating a position on dips:** "I want to buy $5,000 of SOL spread across 15 limit orders between $75 and $85, with bigger clips at the bottom."
- **Distributing a position into rallies:** "I want to sell 2 BTC spread across 10 limit orders between $100k and $115k, with bigger clips at the top."
- **Cleaning up:** "Cancel every open order I have between $80 and $82, but leave everything else alone."

The bot handles the math, the API calls, and the error handling. You just describe the ladder.

---

## Features

- **Three distribution modes** for tiered order ladders: `linear-even`, `geometric-even`, and `geometric-pyramid` (DCA-optimized, see below)
- **Plan preview before execution** — see prices, quantities, and projected average fill price, confirm, then submit
- **Parallel order placement** — a 20-order ladder takes ~4 seconds instead of ~20
- **Built-in rate limiting** (5 req/s) with exponential backoff retry on 429/5xx errors
- **Thread-safe order cache** shared between auto-refresh and foreground commands
- **Price-range cancellation** — cancel every order in `[low, high]` with live state refresh
- **Symbol validation** — rejects typos before you waste a trade
- **Market, limit, and tiered order placement** for both sides
- **Live dashboard** — auto-refreshes balances, open orders, and price every 10 seconds
- **Uses `Decimal` throughout** — no floating-point drift in order math

---

## Installation

Requires Python 3.10+.

```bash
git clone https://github.com/criptocbas/backpack-CLI-bot.git
cd backpack-CLI-bot

# Recommended: use a virtualenv
python -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## Configuration

Copy the example env file and add your Backpack API credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```env
BACKPACK_API_KEY=your_base64_public_key_here
BACKPACK_API_SECRET=your_base64_private_key_here

DEFAULT_SYMBOL=SOL_USDC
API_BASE_URL=https://api.backpack.exchange
```

Get API keys at [backpack.exchange/settings/api](https://backpack.exchange/settings/api). The bot needs **trading** permissions. You can restrict by IP for extra safety.

> **Never commit `.env` to git.** It's already in `.gitignore` — keep it that way.

## Running the bot

```bash
python main.py
```

You'll see a dashboard with your current symbol, price, portfolio value, open orders, and balances. From there everything is keyboard-driven.

To verify your API keys work without placing any orders:

```bash
python test_connection.py
```

---

## Command reference

Type any of these at the `Command:` prompt:

### Order placement

| Key | Action | Prompts for |
|---|---|---|
| `b` | Market buy | quantity |
| `s` | Market sell | quantity |
| `l` | Limit buy | `quantity@price` (e.g. `10@82.5`) |
| `k` | Limit sell | `quantity@price` |
| `tb` | **Tiered buy** (DCA ladder) | total value, price range, number of orders, distribution |
| `ts` | **Tiered sell** (DCA ladder) | total quantity, price range, number of orders, distribution |

### Order management

| Key | Action |
|---|---|
| `o` | Refresh open orders from the exchange |
| `c` | Cancel **all** open orders for the current symbol |
| `cr` | Cancel open orders in a **price range** (asks for upper/lower bounds) |

### Navigation & data

| Key | Action |
|---|---|
| `sym` | Change trading pair (validates against the exchange) |
| `r` | Refresh balances, orders, and price |
| `h` | Show keyboard shortcuts |
| `q` | Quit |

---

## Tiered orders (the flagship feature)

A tiered order is a ladder of limit orders spread across a price range. Instead of placing one $10,000 buy at a single price, you split it into N smaller orders between `price_low` and `price_high`.

Type `tb` for tiered buy or `ts` for tiered sell. The bot will ask for:

1. **Total value** (for buys, in quote currency like USDC) or **total quantity** (for sells, in base currency like SOL)
2. **Lower price bound**
3. **Upper price bound**
4. **Number of orders**
5. **Distribution mode** (see below)
6. **Size scale** (only for pyramid mode)

You'll get a **preview** showing every rung with its price, quantity, and dollar value — plus the projected average fill price — before anything hits the exchange. Type `y` to submit, anything else to cancel.

### Distribution modes

Three modes cover ~90% of real DCA use cases:

| Mode | Price spacing | Size weighting | Best for |
|---|---|---|---|
| `linear-even` | Equal dollar gap between rungs | Equal per rung | Narrow ranges (<5%), stablecoin pairs, "just split my order" |
| `geometric-even` | Equal **percentage** gap between rungs | Equal per rung | Wide ranges (>10%), BTC/SOL accumulation — matches how crypto actually moves |
| `geometric-pyramid` | Equal percentage gap between rungs | **Weighted toward the far end** (bottom for buys, top for sells) | **DCA workflows** — improves average fill price on partial fills |

**The default is `geometric-pyramid` with a `size_scale` of `1.5x`.** Press Enter through the prompts to get the recommended behavior.

#### Why pyramid weighting helps DCA

With **flat** sizing, if price only retraces halfway into your buy ladder, you filled roughly half your rungs at roughly the middle of your range — you barely averaged down. With **pyramid** sizing, the heavier clips are waiting at the bottom, so when a dip actually reaches them, they dominate the weighted average and drag your fill price lower.

The `size_scale` scalar controls how aggressive the weighting is:

- `1.0` — flat (equal per rung)
- `1.5` — mild pyramid: the farthest rung is 1.5× the nearest rung **(recommended default)**
- `3.0` — aggressive: farthest rung is 3× the nearest
- `>3.0` — approaches martingale risk, you'll get a warning

### Worked example

Suppose you want to accumulate $100 of SOL between $80 and $100 across 5 tiers.

**Flat geometric** (`geometric-even`, scale 1.0):

| Rung | Price | Value | Quantity |
|---|---|---|---|
| 1 | $80.00 | $20.00 | 0.2500 |
| 2 | $84.59 | $20.00 | 0.2364 |
| 3 | $89.44 | $20.00 | 0.2236 |
| 4 | $94.57 | $20.00 | 0.2115 |
| 5 | $100.00 | $20.00 | 0.2000 |

- Total quantity: 1.1216 SOL
- **Average fill price: $89.17**

**Pyramid geometric** (`geometric-pyramid`, scale 1.5):

| Rung | Price | Value | Quantity |
|---|---|---|---|
| 1 | $80.00 | $24.00 | 0.3000 *(heaviest)* |
| 2 | $84.59 | $22.00 | 0.2601 |
| 3 | $89.44 | $20.00 | 0.2236 |
| 4 | $94.57 | $18.00 | 0.1903 |
| 5 | $100.00 | $16.00 | 0.1600 *(lightest)* |

- Total quantity: 1.1340 SOL
- **Average fill price: $88.18**

**Pyramid gives ~$1 (1.1%) better average fill for free**, just by redistributing the same $100 across the same 5 prices.

### Safety rails

The bot warns you (but still lets you proceed) if:

- **`size_scale > 3.0`** — approaching martingale territory, risk grows fast
- **Geometric spacing on a range <2% wide** — degenerates to linear; pick `linear-even` instead
- **Smallest rung value <$1** — may fall below Backpack's minimum notional and fail

It outright rejects:

- Inverted ranges (`low >= high`)
- Zero or negative orders
- Missing or conflicting `total_value`/`total_quantity`
- `size_scale < 1.0`

---

## Cancelling orders by price range

Type `cr` to cancel open orders within a specific price band. Useful for:

- Killing off a ladder that's out of range after a move
- Cleaning up stale orders at a specific level
- Partial ladder rollups (cancel the top half, replace at different prices)

The bot **fetches live open orders from the exchange before filtering**, so it always operates on current state — not a stale cache.

You'll see a preview of every order that matches, then confirm before anything is cancelled.

---

## Reliability features

These are all baked into the API client and the order manager, so you don't have to think about them:

- **Rate limiting** — minimum 200ms between requests (5 req/s). Protects you from Backpack throttling when placing large ladders.
- **Retry with exponential backoff** — automatic retry on HTTP 429, 500, 502, 503, 504, and connection errors. Backoff: 0.3s → 0.6s → 1.2s, up to 3 retries. Signatures are re-generated on each attempt so timestamps stay fresh.
- **Parallel placement** — tiered orders fan out across up to 5 worker threads. The rate limiter still serializes actual HTTP calls, so a 20-order ladder takes ~4 seconds instead of ~20.
- **Parallel data refresh** — balances, open orders, and ticker fetch concurrently on every refresh.
- **Thread-safe order cache** — reads and writes to the open-orders cache are protected by a lock, so the auto-refresh thread and your foreground commands never race.
- **Market spec cache with TTL** — tick sizes and step sizes are cached for 5 minutes and auto-refresh.
- **Symbol validation** — changing symbol via `sym` checks against Backpack's live market list before accepting.

---

## Testing the connection

Run this any time you update your API keys or suspect something is broken:

```bash
python test_connection.py
```

It:

1. Loads your `.env` configuration
2. Hits the public ticker endpoint (no auth)
3. Hits the authenticated account endpoint (confirms signing works)
4. Fetches the order book

All four steps must pass before `main.py` will work.

---

## Project structure

```
backpack-CLI-bot/
├── main.py                   # Entry point
├── config.py                 # Config loader (reads .env)
├── test_connection.py        # Smoke test for API credentials
├── requirements.txt          # Python dependencies
├── .env.example              # Template for credentials
├── api/
│   └── backpack.py           # HTTP client: signing, rate limiting, retries
├── core/
│   └── order_manager.py      # Order/TierPlan logic, distributions, execution
├── ui/
│   └── cli.py                # Keyboard-driven dashboard and command handlers
└── utils/
    └── helpers.py            # Formatting, input parsing
```

**If you want to understand the math, read `core/order_manager.py`.** The key pieces:

- `Distribution` enum — the three modes
- `_generate_prices()` — linear vs geometric price spacing
- `_generate_size_weights()` — flat vs pyramid size weighting
- `TierPlan` dataclass — the pre-computed plan shown in the preview
- `build_tier_plan()` — pure math, no API calls
- `execute_tier_plan()` — parallel placement of a pre-built plan

---

## Security

Read [SECURITY.md](SECURITY.md) for the full list of best practices. The short version:

1. **Never commit `.env`** — it's in `.gitignore`, keep it that way
2. **Never share your API secret** — not with anyone, not on Discord, not in screenshots
3. **Use IP restrictions** on your API key if your IP is stable
4. **Rotate keys** periodically (monthly is reasonable)
5. **Use keys with only the permissions you need** — if you only trade, don't enable withdrawal
6. **Enable 2FA** on your Backpack account

---

## Further reading

- [QUICKSTART.md](QUICKSTART.md) — 5-minute setup
- [SETUP.md](SETUP.md) — detailed installation & troubleshooting
- [TRADING_GUIDE.md](TRADING_GUIDE.md) — general trading concepts
- [SECURITY.md](SECURITY.md) — API key hygiene

## License

MIT
