#!/usr/bin/env python3
"""
solana_data.py — Solana perpetuals data fetcher.

Pulls from Drift Protocol's public REST API and Pyth Hermes price oracle.
No auth required. Falls back gracefully when endpoints are unavailable.

Drift DLOB REST:     https://dlob.drift.trade/
Drift market stats:  https://mainnet-beta.api.drift.trade/
Pyth Hermes:         https://hermes.pyth.network/v2/updates/price/latest
"""
from __future__ import annotations
import json
import time
import urllib.request
from typing import Optional

DRIFT_API = "https://mainnet-beta.api.drift.trade"
DRIFT_DLOB = "https://dlob.drift.trade"
PYTH_HERMES = "https://hermes.pyth.network"
HTTP_TIMEOUT = 15

# Drift mainnet perp market indices → symbol
DRIFT_PERP_MARKETS: dict[int, str] = {
    0: "SOL",
    1: "BTC",
    2: "ETH",
    3: "BNB",
    4: "SUI",
    5: "1KPEPE",
    6: "OP",
    7: "AVAX",
    8: "ARB",
    9: "DOGE",
    10: "BNB",
    11: "POL",
    12: "XRP",
    13: "APT",
    14: "LINK",
    15: "PYTH",
    16: "1KBONK",
    17: "JTO",
    18: "SEI",
    19: "INJ",
    20: "TIA",
    21: "JUP",
    22: "WIF",
    23: "TNSR",
    24: "W",
    25: "KMNO",
    26: "ONDO",
    27: "IO",
    28: "ZEX",
    29: "PONKE",
    30: "CLOUD",
    31: "PYUSD",
    32: "TRUMP",
    33: "MELANIA",
}

# Pyth price feed IDs for common assets (used as fallback oracle)
PYTH_FEED_IDS: dict[str, str] = {
    "SOL": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "BNB": "0x2f95862b045670cd22bee3114c39763a4a08beeb663b145d283c31d7d1101c4f",
    "AVAX": "0x93da3352f9f1d105fdfe4971cfa80e9dd777bfc5d0f683ebb6e1294b92137bb7",
    "DOGE": "0xdcef50dd0a4cd2dcc17e45df1676dcb336a11a61c69df7a0299b0150c672d25c",
    "SUI": "0x23d7315113f5b1d3ba7a83604c44b94d79f4fd69af77f804fc7f920a6dc65744",
    "OP": "0x385f64d993f7b77d8182ed5003d97c60aa3361f3cecfe711544d2d59165e9bdf",
    "ARB": "0x3fa4252848f9f0a1480be62745a4629d9eb1322aebab8a791e344b3b9c1adcf5",
    "XRP": "0xec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8",
    "LINK": "0x8ac0c70fff57e9aefdf5edf44b51d62c2d433653cbb2cf5cc06bb115af04d221",
    "INJ": "0x7a5bc1d2b56ad029048cd63964b3ad2776eaed1ebd255d6fc6cf8f76c4e0a0a2",
    "APT": "0x03ae4db29ed4ae33d323568895aa00337e658e348b37509f5372ae51f0af00d5",
    "JUP": "0x0a0408d619e9380abad35060f9192039ed5042fa6f82301d0e48bb52be830996",
    "WIF": "0x4ca4beeca86f0d164160323817a4e42b10010a724c2217c6ee41b54cd4cc61fc",
    "TRUMP": "0x879551021853eec7a7dc827578e8e69da7e4fa8148339aa0d3d5296405be4b1a",
}


def _get(url: str, timeout: int = HTTP_TIMEOUT) -> Optional[object]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def fetch_drift_markets() -> list[dict]:
    """Fetch all Drift perp markets from the public stats API."""
    data = _get(f"{DRIFT_API}/v2/perp/markets")
    if not data:
        return []
    markets = data if isinstance(data, list) else data.get("markets", [])
    result = []
    for m in markets:
        try:
            idx = int(m.get("marketIndex", m.get("market_index", -1)))
            symbol = m.get("symbol", m.get("name", DRIFT_PERP_MARKETS.get(idx, f"UNKNOWN_{idx}")))
            # Normalize symbol: remove "-PERP" suffix
            base = symbol.replace("-PERP", "").replace("_PERP", "").upper()
            result.append({
                "symbol": base,
                "market_index": idx,
                "mark_price": _fnum(m.get("markPrice", m.get("mark_price"))),
                "oracle_price": _fnum(m.get("oraclePrice", m.get("oracle_price"))),
                "funding_rate": _fnum(m.get("lastFundingRate", m.get("funding_rate"))),
                "open_interest": _fnum(m.get("openInterestNotional", m.get("open_interest"))),
                "volume_24h": _fnum(m.get("volume24H", m.get("volume_24h"))),
                "source": "drift",
            })
        except Exception:
            continue
    return result


def fetch_drift_l2(market_index: int) -> Optional[dict]:
    """Fetch L2 orderbook for a Drift perp market."""
    data = _get(f"{DRIFT_DLOB}/l2?marketIndex={market_index}&marketType=perp&depth=5")
    if not data:
        return None
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if not bids or not asks:
        return None
    best_bid = float(bids[0]["price"]) if bids else None
    best_ask = float(asks[0]["price"]) if asks else None
    mid = (best_bid + best_ask) / 2 if (best_bid and best_ask) else None
    spread_bps = ((best_ask - best_bid) / mid * 1e4) if (mid and best_bid and best_ask) else None
    return {
        "market_index": market_index,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": spread_bps,
    }


def fetch_pyth_prices(symbols: list[str]) -> dict[str, float]:
    """
    Fetch latest prices from Pyth Hermes for given symbols.
    Returns {symbol: price_usd}.
    """
    feed_ids = {s: PYTH_FEED_IDS[s] for s in symbols if s in PYTH_FEED_IDS}
    if not feed_ids:
        return {}

    ids_query = "&".join(f"ids[]={fid}" for fid in feed_ids.values())
    data = _get(f"{PYTH_HERMES}/v2/updates/price/latest?{ids_query}")
    if not data:
        return {}

    # Build reverse map: feed_id -> symbol
    rev = {v: k for k, v in feed_ids.items()}
    prices: dict[str, float] = {}
    for item in data.get("parsed", []):
        fid = item.get("id", "")
        full_id = f"0x{fid}" if not fid.startswith("0x") else fid
        sym = rev.get(full_id)
        if not sym:
            continue
        price_data = item.get("price", {})
        raw_price = _fnum(price_data.get("price"))
        expo = int(price_data.get("expo", 0))
        if raw_price is not None:
            prices[sym] = raw_price * (10 ** expo)
    return prices


def fetch_drift_funding_rates() -> dict[str, float]:
    """Fetch latest funding rates from Drift markets (annualized bps/hr)."""
    markets = fetch_drift_markets()
    return {m["symbol"]: m["funding_rate"] for m in markets if m["funding_rate"] is not None}


def get_drift_snapshot() -> dict[str, dict]:
    """
    Get a full snapshot of Drift perp markets.
    Returns {symbol: {mark, oracle, funding, oi, vol24h, spread_bps}}.
    """
    markets = fetch_drift_markets()
    if not markets:
        # Fallback: try to at least get Pyth prices for major assets
        pyth = fetch_pyth_prices(list(PYTH_FEED_IDS.keys()))
        return {
            sym: {
                "symbol": sym,
                "mark_price": price,
                "oracle_price": price,
                "funding_rate": None,
                "open_interest": None,
                "volume_24h": None,
                "spread_bps": None,
                "source": "pyth_fallback",
            }
            for sym, price in pyth.items()
        }

    snapshot: dict[str, dict] = {}
    for m in markets:
        sym = m["symbol"]
        # Optionally enrich with L2 spread
        l2 = None
        if m["mark_price"] is None:
            l2 = fetch_drift_l2(m["market_index"])
            if l2:
                m["mark_price"] = l2["mid"]
                m["spread_bps"] = l2["spread_bps"]
        snapshot[sym] = m

    return snapshot


def _fnum(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    print("=== Drift Protocol Snapshot ===")
    snap = get_drift_snapshot()
    if not snap:
        print("[!] No data from Drift API — trying Pyth fallback")
        pyth = fetch_pyth_prices(["BTC", "ETH", "SOL"])
        for sym, price in pyth.items():
            print(f"  {sym}: ${price:,.2f} (Pyth)")
    else:
        print(f"{'Symbol':<12}{'Mark($)':>14}{'Oracle($)':>14}{'Fund(bps/hr)':>14}{'Vol24h($M)':>12}")
        for sym, m in sorted(snap.items(), key=lambda x: -(x[1].get("volume_24h") or 0))[:20]:
            mark = m.get("mark_price")
            oracle = m.get("oracle_price")
            fund = m.get("funding_rate")
            vol = m.get("volume_24h")
            print(
                f"  {sym:<12}"
                f"{(mark or 0):>14,.2f}"
                f"{(oracle or 0):>14,.2f}"
                f"{(fund * 1e4 if fund else float('nan')):>14.4f}"
                f"{(vol / 1e6 if vol else 0):>12.2f}M"
            )

    print("\n=== Pyth Oracle Prices ===")
    pyth = fetch_pyth_prices(["BTC", "ETH", "SOL", "AVAX", "DOGE"])
    for sym, price in pyth.items():
        print(f"  {sym}: ${price:,.4f}")
