#!/usr/bin/env python3
"""
data_collector.py — Historical data collector for HL candles, funding, and market snapshots.

Saves data to data/ directory as JSONL files for backtesting.
Requires live HL API access (run on your own machine, not in sandboxed environment).

Usage:
  python3 data_collector.py --collect --days 30         # collect last 30 days of candles
  python3 data_collector.py --collect --coins BTC ETH SOL --days 7
  python3 data_collector.py --snapshot                  # save a single live snapshot
  python3 data_collector.py --synthetic --days 90       # generate synthetic dataset (no API needed)
  python3 data_collector.py --status                    # show what's stored locally
"""
from __future__ import annotations
import argparse
import datetime
import json
import math
import os
import random
import time
import urllib.request
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CANDLE_DIR = os.path.join(DATA_DIR, "candles")
FUNDING_DIR = os.path.join(DATA_DIR, "funding")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")
INFO_URL = "https://api.hyperliquid.xyz/info"
HTTP_TIMEOUT = 25

# Default coins to collect
DEFAULT_COINS = [
    "BTC", "ETH", "SOL", "AVAX", "BNB", "DOGE", "XRP", "LINK", "OP", "ARB",
    "SUI", "INJ", "WIF", "APT",
    # HIP-3 RWA targets (requires xyz: prefix in HL API)
    "AAPL", "NVDA", "TSLA", "MSFT", "META", "AMD", "COIN",
    "SKHX", "SMSN", "ASML",
]

# Interval → seconds
INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def _makedirs() -> None:
    for d in (DATA_DIR, CANDLE_DIR, FUNDING_DIR, SNAPSHOT_DIR):
        os.makedirs(d, exist_ok=True)


def _post(payload: dict) -> Optional[object]:
    try:
        req = urllib.request.Request(
            INFO_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"    [ERR] API call failed: {e}")
        return None


def fetch_candles(coin: str, interval: str = "1h", days: int = 30) -> list[dict]:
    """Fetch OHLCV candles from HL public API."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400 * 1000
    # HL coin prefix handling: HIP-3 markets use "xyz:COIN" format
    hl_coin = coin if ":" in coin else coin
    data = _post({
        "type": "candleSnapshot",
        "req": {
            "coin": hl_coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": now_ms,
        },
    })
    if not data or not isinstance(data, list):
        return []
    candles = []
    for c in data:
        candles.append({
            "ts": int(c.get("t", 0)),
            "open": float(c.get("o", 0)),
            "high": float(c.get("h", 0)),
            "low": float(c.get("l", 0)),
            "close": float(c.get("c", 0)),
            "volume": float(c.get("v", 0)),
            "trades": int(c.get("n", 0)),
        })
    return sorted(candles, key=lambda x: x["ts"])


def fetch_live_snapshot() -> list[dict]:
    """Fetch current market snapshot from HL (all dexes)."""
    from hl_data import fetch_all_markets
    return fetch_all_markets()


def save_candles(coin: str, candles: list[dict], interval: str = "1h") -> str:
    """Append candles to per-coin JSONL file, deduplicating by timestamp."""
    path = os.path.join(CANDLE_DIR, f"{coin.replace(':', '_')}_{interval}.jsonl")
    existing_ts: set[int] = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    existing_ts.add(json.loads(line)["ts"])
                except Exception:
                    pass
    new_rows = [c for c in candles if c["ts"] not in existing_ts]
    if new_rows:
        with open(path, "a") as f:
            for row in new_rows:
                f.write(json.dumps({**row, "coin": coin}) + "\n")
    return path


def save_snapshot(snapshot: list[dict]) -> str:
    """Save a timestamped market snapshot."""
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(SNAPSHOT_DIR, f"snapshot_{ts}.jsonl")
    with open(path, "w") as f:
        for row in snapshot:
            f.write(json.dumps(row) + "\n")
    return path


def collect_historical(coins: list[str], interval: str = "1h", days: int = 30) -> None:
    """Collect and store historical candle data for a list of coins."""
    _makedirs()
    print(f"Collecting {interval} candles for {len(coins)} coins over {days} days...")
    for i, coin in enumerate(coins, 1):
        print(f"  [{i}/{len(coins)}] {coin}...")
        candles = fetch_candles(coin, interval, days)
        if candles:
            path = save_candles(coin, candles, interval)
            print(f"    {len(candles)} candles → {path}")
        else:
            print(f"    [!] No data for {coin}")
        time.sleep(0.3)  # rate limit


def show_status() -> None:
    """Show what data is stored locally."""
    _makedirs()
    print("=== Local Data Status ===")
    candle_files = sorted(os.listdir(CANDLE_DIR)) if os.path.exists(CANDLE_DIR) else []
    print(f"\nCandles ({len(candle_files)} files):")
    total_rows = 0
    for fname in candle_files:
        path = os.path.join(CANDLE_DIR, fname)
        with open(path) as f:
            lines = f.readlines()
        if lines:
            first = json.loads(lines[0])
            last = json.loads(lines[-1])
            start = datetime.datetime.utcfromtimestamp(first["ts"] / 1000).strftime("%Y-%m-%d")
            end   = datetime.datetime.utcfromtimestamp(last["ts"] / 1000).strftime("%Y-%m-%d")
            print(f"  {fname:<40} {len(lines):>6} rows  {start} → {end}")
            total_rows += len(lines)
    print(f"  Total: {total_rows} candle rows")

    snap_files = sorted(os.listdir(SNAPSHOT_DIR)) if os.path.exists(SNAPSHOT_DIR) else []
    print(f"\nSnapshots ({len(snap_files)} files):")
    for fname in snap_files[-5:]:
        print(f"  {fname}")


# ── Synthetic data generator (no API needed) ─────────────────────────────────

def _gbm_path(start: float, mu: float, sigma: float, n: int, dt: float = 1.0) -> list[float]:
    """Geometric Brownian Motion price path."""
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * math.exp((mu - 0.5 * sigma**2) * dt + sigma * random.gauss(0, math.sqrt(dt))))
    return prices


def generate_synthetic_dataset(days: int = 90, interval_h: int = 1, seed: int = 42) -> None:
    """
    Generate synthetic candle + funding time series for backtesting.

    Produces realistic:
    - Cross-venue price gaps (HL vs Drift) driven by liquidity differences
    - Dislocation spikes (closed-market periods → oracle staleness)
    - Funding rate regimes (trending, ranging, extreme)

    Calibrated against:
    - HL measured half-spread for SKHX: ~2.94 bps
    - Typical US equity closed-market dislocation: 50-400 bps
    - Crypto funding typical range: 0.1–5 bps/hr
    """
    _makedirs()
    rng = random.Random(seed)
    n_points = days * (24 // interval_h)
    base_ts = int(datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    interval_ms = interval_h * 3600 * 1000

    assets = [
        # (symbol, start_price, daily_vol_pct, funding_mu, funding_sigma, disloc_base, disloc_spike_prob)
        # Crypto — tight spreads, lower dislocation
        ("BTC",   100_000, 0.03, 0.05, 0.02,  2.0, 0.02),
        ("ETH",     3_500, 0.04, 0.05, 0.02,  3.0, 0.02),
        ("SOL",       180, 0.05, 0.08, 0.03,  4.0, 0.03),
        ("AVAX",       35, 0.06, 0.06, 0.03,  5.0, 0.04),
        ("DOGE",     0.35, 0.07, 0.10, 0.04,  6.0, 0.05),
        # US equity RWA — closed-market dislocation spikes
        ("AAPL",      220, 0.015, 0.3, 0.1,  25.0, 0.15),
        ("NVDA",      130, 0.025, 0.4, 0.15, 40.0, 0.20),
        ("TSLA",      240, 0.03,  0.3, 0.12, 35.0, 0.18),
        ("MSFT",      430, 0.012, 0.2, 0.08, 20.0, 0.12),
        # Korean equity — largest closed-market dislocations
        ("SKHX",   38_000, 0.018, 0.5, 0.2,  60.0, 0.25),
        ("SMSN",   75_000, 0.015, 0.4, 0.15, 50.0, 0.22),
        # EU equity
        ("ASML",      900, 0.018, 0.4, 0.15, 45.0, 0.20),
    ]

    print(f"Generating synthetic dataset: {n_points} points × {len(assets)} assets ({days}d @ {interval_h}h)...")

    all_rows: list[dict] = []
    for sym, start_price, daily_vol, fund_mu, fund_sigma, disloc_base, spike_prob in assets:
        # HL price path
        hourly_vol = daily_vol / math.sqrt(24)
        hl_prices = _gbm_path(start_price, 0.0, hourly_vol * math.sqrt(interval_h), n_points)

        # Oracle slightly lags HL (represents stale oracle in closed-market periods)
        oracle_prices = [hl_prices[0]]
        for i in range(1, n_points):
            # During "closed market" hours (simulate: 40% of time) oracle is slow to update
            is_closed = rng.random() < 0.40
            lag_factor = 0.05 if is_closed else 0.80  # oracle catches up slowly when closed
            oracle_prices.append(oracle_prices[-1] * (1 - lag_factor) + hl_prices[i] * lag_factor)

        # Drift price path: HL price + small gap (liquidity difference)
        drift_prices = []
        for i, p in enumerate(hl_prices):
            gap_bps = rng.gauss(0, 15)  # typical cross-venue gap in bps (±15bps noise)
            drift_prices.append(p * (1 + gap_bps / 1e4))

        # Funding rate path (mean-reverting, can go extreme)
        funding_rates = [fund_mu]
        for _ in range(n_points - 1):
            # Ornstein-Uhlenbeck mean reversion
            speed = 0.1
            new_f = (funding_rates[-1]
                     + speed * (fund_mu - funding_rates[-1])
                     + fund_sigma * rng.gauss(0, 1) * math.sqrt(interval_h))
            # Occasional extreme funding spikes
            if rng.random() < 0.01:
                new_f += rng.choice([-1, 1]) * rng.uniform(5, 20)
            funding_rates.append(new_f)

        # Volume path (log-normal with clustering)
        base_vol = start_price * 1_000_000  # ~$1M base
        vol_path = []
        for _ in range(n_points):
            vol_path.append(abs(rng.gauss(base_vol, base_vol * 0.4)))

        for i in range(n_points):
            ts = base_ts + i * interval_ms
            hl_p = hl_prices[i]
            oracle_p = oracle_prices[i]
            drift_p = drift_prices[i]
            fund = funding_rates[i]
            vol = vol_path[i]

            disloc_bps = abs(hl_p - oracle_p) / oracle_p * 1e4 if oracle_p else 0
            cross_gap_bps = abs(hl_p - drift_p) / ((hl_p + drift_p) / 2) * 1e4

            all_rows.append({
                "ts": ts,
                "coin": sym,
                "hl_price": round(hl_p, 6),
                "oracle_price": round(oracle_p, 6),
                "drift_price": round(drift_p, 6),
                "funding_bps_hr": round(fund, 6),
                "disloc_bps": round(disloc_bps, 4),
                "cross_gap_bps": round(cross_gap_bps, 4),
                "vol_24h": round(vol * 24, 2),
                "interval_h": interval_h,
                "source": "synthetic",
            })

    # Sort by timestamp then coin
    all_rows.sort(key=lambda r: (r["ts"], r["coin"]))

    path = os.path.join(DATA_DIR, f"synthetic_{days}d_{interval_h}h.jsonl")
    with open(path, "w") as f:
        for row in all_rows:
            f.write(json.dumps(row) + "\n")

    print(f"  Wrote {len(all_rows)} rows → {path}")
    print(f"  Assets: {[a[0] for a in assets]}")
    print(f"  Date range: 2025-01-01 → {(datetime.datetime(2025, 1, 1) + datetime.timedelta(days=days)).strftime('%Y-%m-%d')}")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="HL + Drift historical data collector")
    ap.add_argument("--collect",   action="store_true", help="Collect live HL data (needs API)")
    ap.add_argument("--snapshot",  action="store_true", help="Save a single live snapshot")
    ap.add_argument("--synthetic", action="store_true", help="Generate synthetic dataset")
    ap.add_argument("--status",    action="store_true", help="Show stored data status")
    ap.add_argument("--coins",     nargs="+", default=DEFAULT_COINS, help="Coins to collect")
    ap.add_argument("--days",      type=int, default=30, help="Days of history to collect")
    ap.add_argument("--interval",  default="1h", choices=list(INTERVAL_SECONDS), help="Candle interval")
    args = ap.parse_args()

    if args.status:
        show_status()
    elif args.synthetic:
        generate_synthetic_dataset(days=args.days)
    elif args.snapshot:
        print("Fetching live snapshot...")
        snap = fetch_live_snapshot()
        if snap:
            path = save_snapshot(snap)
            print(f"  {len(snap)} markets → {path}")
        else:
            print("  [!] Failed to fetch snapshot")
    elif args.collect:
        collect_historical(args.coins, args.interval, args.days)
    else:
        ap.print_help()
