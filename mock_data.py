#!/usr/bin/env python3
"""
mock_data.py — Realistic fixture data for testing arb/sniper logic without live APIs.

Generated from typical market conditions. Used by --mock flag and test suite.
"""
from __future__ import annotations
import math
import time

# ── HL market fixtures ───────────────────────────────────────────────────────
def make_hl_markets() -> list[dict]:
    """
    Simulated HL market rows matching the shape returned by hl_data.build_market_row().
    Includes a mix of crypto, RWA, and cross-venue arb scenarios.
    """
    now_utc = time.gmtime()
    base = [
        # (coin, dex, bucket, mark, oracle, funding_bps_hr, vol_24h, oi_ntl)
        # Standard crypto — tight spreads
        ("BTC",    "core", "crypto",    105_420.0, 105_430.0,  0.5,   2_100_000_000, 800_000_000),
        ("ETH",    "core", "crypto",      3_842.0,   3_844.0,  0.3,     850_000_000, 300_000_000),
        ("SOL",    "core", "crypto",        198.4,     198.5,  0.8,     420_000_000, 120_000_000),
        ("AVAX",   "core", "crypto",         38.2,      38.2,  0.2,      80_000_000,  20_000_000),
        ("BNB",    "core", "crypto",        720.5,     720.5,  0.1,     200_000_000,  60_000_000),
        ("DOGE",   "core", "crypto",          0.385,     0.386, 0.6,    150_000_000,  40_000_000),
        ("XRP",    "core", "crypto",          2.840,     2.842, 0.4,    180_000_000,  50_000_000),
        ("LINK",   "core", "crypto",         19.20,     19.22, 0.3,     45_000_000,  15_000_000),
        ("OP",     "core", "crypto",          1.820,     1.822, 0.5,    30_000_000,   8_000_000),
        ("ARB",    "core", "crypto",          0.940,     0.941, 0.4,    25_000_000,   7_000_000),
        ("SUI",    "core", "crypto",          4.810,     4.812, 0.7,    90_000_000,  25_000_000),
        ("INJ",    "core", "crypto",         22.50,     22.52, 0.6,    20_000_000,   6_000_000),
        ("WIF",    "core", "crypto",          2.240,     2.242, 1.2,    35_000_000,  10_000_000),
        ("APT",    "core", "crypto",         10.80,     10.82, 0.4,    18_000_000,   5_000_000),
        # RWA perps — closed market hours → larger dislocation
        # US equity — market CLOSED (weekend / outside trading hours)
        ("AAPL",   "xyz",  "us_equity",     226.50,    228.10, 1.2,      5_200_000,   2_100_000),
        ("NVDA",   "xyz",  "us_equity",     137.80,    142.30, 3.8,      8_500_000,   3_200_000),  # HIGH dislocation
        ("TSLA",   "xyz",  "us_equity",     248.60,    251.40, 2.1,      7_100_000,   2_800_000),
        ("MSFT",   "xyz",  "us_equity",     447.20,    449.80, 1.5,      4_800_000,   1_900_000),
        ("META",   "xyz",  "us_equity",     618.30,    621.50, 1.9,      3_200_000,   1_200_000),
        ("GOOGL",  "xyz",  "us_equity",     180.40,    181.80, 0.9,      2_900_000,   1_100_000),
        ("AMD",    "xyz",  "us_equity",     158.90,    165.40, 8.1,      4_100_000,   1_600_000),  # VERY HIGH dislocation spike
        ("COIN",   "xyz",  "us_equity",     268.50,    272.10, 4.2,      3_800_000,   1_500_000),
        # Korean equity — KST closed
        ("SKHX",   "xyz",  "kr_equity",      38_200.0,  38_450.0, 5.2, 2_100_000,    800_000),   # classic HL RWA case
        ("SMSN",   "xyz",  "kr_equity",      76_500.0,  77_800.0, 6.8, 1_800_000,    700_000),
        # Japanese equity — JST closed
        ("SONY",   "xyz",  "jp_equity",      23_400.0,  23_650.0, 4.1, 1_500_000,    600_000),
        ("TOYOTA", "xyz",  "jp_equity",       2_650.0,   2_680.0, 3.2,   900_000,    350_000),
        # EU equity — CET closed
        ("ASML",   "xyz",  "eu_equity",     903.40,    912.20,  8.6,  2_400_000,    900_000),    # HIGH — IMMEDIATE sniper
        ("SAP",    "xyz",  "eu_equity",     238.60,    241.30,  3.8,    800_000,    300_000),
        # Commodities
        ("GOLD",   "xyz",  "commodity",    3_380.0,   3_382.0,  0.8,  5_200_000,  2_000_000),
        ("SILVER", "xyz",  "commodity",      40.80,     40.82,  0.6,    800_000,    300_000),
        # Funding cliff scenario (extreme funding → sniper target)
        ("TRUMP",  "xyz",  "crypto",         22.80,     22.82, 18.5,  9_400_000,  3_800_000),  # extreme funding long
    ]

    rows = []
    for coin, dex, bucket, mark, oracle, fund_bps, vol, oi_ntl in base:
        disloc_bps = abs(mark - oracle) / oracle * 1e4 if oracle else None
        # Simple PFI proxy (real formula from hl_data)
        vol_contest = math.log10(1.0 + max(vol, 0) / 1e5)
        clock_f = 1.6 if bucket in ("kr_equity", "jp_equity", "eu_equity", "us_equity", "commodity") else 1.0
        pfi = (disloc_bps or 0) * vol_contest * clock_f

        rows.append({
            "dex": dex,
            "coin": coin,
            "bucket": bucket,
            "ref_open": bucket == "crypto",
            "vol_24h": vol,
            "oi_ntl": oi_ntl,
            "mark": mark,
            "oracle": oracle,
            "mid": (mark + oracle) / 2,
            "funding_bps_hr": fund_bps,
            "disloc_bps": disloc_bps,
            "day_move_pct": (mark - oracle) / oracle * 100 if oracle else None,
            "pfi": pfi,
        })
    return rows


# ── Drift snapshot fixtures ──────────────────────────────────────────────────
def make_drift_snapshot() -> dict[str, dict]:
    """
    Simulated Drift perp market snapshot.
    Includes intentional price gaps vs HL to trigger arb signals.
    """
    markets = [
        # (symbol, mark, oracle, funding_rate_decimal, vol_24h, oi)
        # BTC: Drift slightly higher → BUY_HL_SELL_DRIFT
        ("BTC",   105_480.0, 105_430.0,  0.000050,  1_800_000_000, 650_000_000),
        # ETH: Drift lower → BUY_DRIFT_SELL_HL
        ("ETH",     3_828.0,   3_844.0,  0.000030,    720_000_000, 250_000_000),
        # SOL: essentially parity
        ("SOL",       198.6,     198.5,  0.000080,    350_000_000, 100_000_000),
        # AVAX: big gap → arb signal
        ("AVAX",       37.0,      38.2,  0.000020,     60_000_000,  16_000_000),
        # BNB: tight
        ("BNB",       721.0,     720.5,  0.000010,    170_000_000,  50_000_000),
        # DOGE: slight gap
        ("DOGE",        0.390,     0.386, 0.000060,   130_000_000,  35_000_000),
        # XRP: parity
        ("XRP",         2.845,     2.842, 0.000040,   160_000_000,  45_000_000),
        # LINK: Drift notably higher
        ("LINK",       19.50,     19.22,  0.000030,    38_000_000,  12_000_000),
        # OP: gap
        ("OP",          1.780,     1.822, 0.000050,    25_000_000,   7_000_000),
        # ARB: small gap
        ("ARB",         0.952,     0.941, 0.000040,    22_000_000,   6_000_000),
        # SUI: near parity
        ("SUI",         4.820,     4.812, 0.000070,    80_000_000,  22_000_000),
        # INJ: gap
        ("INJ",        22.80,     22.52,  0.000060,    17_000_000,   5_500_000),
        # WIF: extreme funding on Drift → funding_cliff signal
        ("WIF",         2.260,     2.242, 0.000600,    30_000_000,   9_000_000),
        # APT: tight
        ("APT",        10.85,     10.82,  0.000040,    15_000_000,   4_500_000),
    ]
    return {
        sym: {
            "symbol": sym,
            "market_index": i,
            "mark_price": mark,
            "oracle_price": oracle,
            "funding_rate": fund,
            "open_interest": oi,
            "volume_24h": vol,
            "source": "mock_drift",
        }
        for i, (sym, mark, oracle, fund, vol, oi) in enumerate(markets)
    }


# ── Pyth price fixtures ──────────────────────────────────────────────────────
def make_pyth_prices() -> dict[str, float]:
    """Simulated Pyth oracle prices (mid-point between HL and Drift marks)."""
    return {
        "BTC":   105_435.0,
        "ETH":     3_843.0,
        "SOL":       198.5,
        "AVAX":       38.1,
        "BNB":       720.6,
        "DOGE":        0.387,
        "XRP":         2.841,
        "LINK":       19.30,
        "OP":          1.810,
        "ARB":         0.944,
        "SUI":         4.815,
        "INJ":        22.55,
        "WIF":         2.245,
        "APT":        10.83,
        "TRUMP":      22.81,
        "GOLD":    3_381.0,
    }
