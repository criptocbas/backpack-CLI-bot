# Backpack CLI Bot

A Python CLI trading bot for Backpack Exchange. The flagship feature is **tiered (DCA) limit ladders** via `tb`/`ts` commands. Single-user, personal tool — no need for backwards-compat shims, feature flags, or multi-tenancy abstractions.

## Running it

```bash
./run.sh          # launch the TUI (wraps venv/bin/python main.py)
./venv/bin/python test_connection.py   # smoke-test API credentials only
```

Credentials live in `.env` (`BACKPACK_API_KEY`, `BACKPACK_API_SECRET`). `config.py` loads them.

## Architecture

Three strict layers, bottom-up. Don't blur the boundaries.

- **`api/backpack.py`** — HTTP client. ED25519 request signing, rate limiter, retry loop with jitter + `Retry-After`. One method per Backpack endpoint (`place_order`, `get_account`, `get_collateral`, `get_open_orders`, …).
- **`core/order_manager.py`** — Domain layer. `Order` wraps API responses. `TierPlan` is a pure dataclass. `build_tier_plan` does all the Decimal math (price/weight generation, per-rung rounding, exchange min/max filters). `execute_tier_plan` fans out with a `ThreadPoolExecutor` (5 workers, serialized at HTTP by the rate limiter).
- **`ui/cli.py`** — Rich TUI. One handler per keystroke (`handle_buy_market`, `handle_tiered_sell`, …). Preflight balance checks before calling into `order_manager`.
- `utils/` — pure formatters (no side effects).
- `main.py` — thin entry point.

A new command goes entirely through these layers: handler in `ui/cli.py` → existing method on `OrderManager` → existing API call. If you find yourself editing more than one layer for a simple feature, stop and rethink.

## Non-obvious things that will bite you

### Signing: Python booleans must be lowercased
`_generate_signature` builds `key=value&...` strings. Python's `f"{True}"` renders `"True"`, but `json.dumps(True)` (which `requests.json=` uses) emits `"true"`. If the two don't match, Backpack rejects with `INVALID_CLIENT_REQUEST - Invalid signature`. The fix lowercases bools in the signer. If you add any new bool body param, do **not** pre-stringify to `"True"`/`"False"` — leave it as a Python bool and let the signer handle it.

### `/api/v1/capital` does not include lent balances
The spot wallet endpoint returns `available`/`locked`/`staked` only. Lent balances (when auto-lend is on) live on `/api/v1/capital/collateral` under `lendQuantity`. `refresh_balances` merges both; the `Lent` column in the balances table comes from the collateral endpoint.

### Every order is sent with `autoLendRedeem: true`
This lets the user keep auto-lend permanently on — Backpack redeems exactly enough lent balance at fill time. Preflight therefore treats spendable = `free + lent`. Disable per-call with `place_order(..., auto_lend_redeem=False)` if you ever need to.

### Cross-margin collateral is a hidden ceiling
If SOL (or anything) is backing open perp positions, Backpack will reject any spot sell that'd take `netEquityAvailable` below maintenance margin. The bot's preflight doesn't know about this yet — it'll happily approve the order, then Backpack rejects it. If you add that check, pull `netEquityAvailable` from `get_collateral()`.

### Decimal everywhere, side-aware rounding
Prices and quantities are `Decimal` end-to-end. Don't introduce `float` in the order path — binary drift will break tick alignment. `round_to_precision` in `api/backpack.py` rounds **up** for sell limits and **down** for buy limits / all quantities, so the rounded order never crosses the user's intended price.

### Tiered execution is fail-fast
`execute_tier_plan` watches for the first rung failure, then cancels every pending rung (in-flight ones finish). Reason: if rung 1 hits a systemic issue (bad sig, insufficient balance, bad precision), the remaining 29 will hit the same thing — no reason to hammer the API. Error message is surfaced in the summary.

### Trigger-pending perp orders look weird
Old stop-loss / take-profit orders show up in `/api/v1/orders` with `status="TriggerPending"`, `quantity="0"`, and no `price`. The Order parser handles missing fields safely. They're rendered in the Open Orders table with zeros — not a bug.

## Style

- **Decimal for money, int for counts, str for IDs.** Never `float` in the order path.
- **No comments that paraphrase the code.** The codebase is mostly uncommented on purpose — add one only when *why* is non-obvious.
- **Silent failures are forbidden in order paths.** Either re-raise, or return a structured result the caller can log.
- **No tests yet.** `_generate_prices` and `_generate_size_weights` in `core/order_manager.py` plus `_generate_signature` in `api/backpack.py` are the highest-value targets when we add them.

## Known paper cuts (low-priority)

- **TOCTOU on preflight balance check** — balance is fetched once at handler entry, then the order is sent. A fill or deposit between the two can invalidate the check. Server rejects cleanly, so low impact; not worth fixing unless this tool goes multi-user.
- **`ui/cli.py` is ~900 lines** — readable, but the buy/sell handlers duplicate ~40 lines of preflight scaffolding. Extract `_preflight_buy` / `_preflight_sell` helpers when convenient.
- **No collateral-aware sellable ceiling in the UI** — see "Cross-margin collateral" above.
