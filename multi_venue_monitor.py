#!/usr/bin/env python3
"""
multi_venue_monitor.py — Real-time multi-venue arb & sniper monitor.

Simultaneously watches Hyperliquid (HL), Drift Protocol (Solana), Phoenix DEX spot,
Mango Markets, and Pyth oracle. Runs arb_scanner + sniper_signals logic every tick
and logs signals to JSONL with rolling statistics.

Usage:
  python3 multi_venue_monitor.py                                    # all venues, 60s polls
  python3 multi_venue_monitor.py --venues hl drift pyth            # selective
  python3 multi_venue_monitor.py --interval 30                     # faster polls
  python3 multi_venue_monitor.py --mock                            # fixture data (no APIs)
  python3 multi_venue_monitor.py --once                            # single scan
  python3 multi_venue_monitor.py --stats                           # show rolling stats
  python3 multi_venue_monitor.py --tail 20                         # tail the signal log
  python3 multi_venue_monitor.py --ws                              # enable WebSocket feeds (HL+Pyth)

Run on your local machine — live APIs require outbound HTTPS to HL, Drift, Pyth, Phoenix.
In cloud/sandboxed environments use --mock for fixture data.
"""
from __future__ import annotations
import argparse
import collections
import datetime
import json
import math
import os
import sys
import threading
import time
import traceback
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── Import local modules ─────────────────────────────────────────────────────
try:
    from hl_data import fetch_all_markets
    _HL_AVAILABLE = True
except ImportError:
    _HL_AVAILABLE = False

try:
    from solana_data import get_drift_snapshot, fetch_pyth_prices
    _SOLANA_AVAILABLE = True
except ImportError:
    _SOLANA_AVAILABLE = False

from arb_scanner import scan_arb_opportunities, print_signals, ArbSignal
from sniper_signals import run_full_sniper_scan, print_sniper_report, SniperSignal
from mock_data import make_hl_markets, make_drift_snapshot, make_pyth_prices

# ── Constants ────────────────────────────────────────────────────────────────
LOG_DIR      = os.path.join(os.path.dirname(__file__), "data", "monitor_logs")
SIGNAL_LOG   = os.path.join(LOG_DIR, "signals.jsonl")
STATS_FILE   = os.path.join(LOG_DIR, "rolling_stats.json")
REPORT_FILE  = os.path.join(os.path.dirname(__file__), "arb_report.json")

PHOENIX_API  = "https://api.phoenix.trade/v1"
MANGO_API    = "https://api.mngo.cloud/data/v4"
HL_WS_URL    = "wss://api.hyperliquid.xyz/ws"
PYTH_WS_URL  = "wss://hermes.pyth.network/ws"

# Pyth feed IDs (BTC, ETH, SOL, AVAX, BNB, DOGE, LINK)
PYTH_FEED_IDS: dict[str, str] = {
    "BTC":  "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH":  "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "SOL":  "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "AVAX": "0x93da3352f9f1d105fdfe4971cfa80e9dd777bfc5d0f683ebb6e1294b92137bb7",
    "BNB":  "0x2f95862b045670cd22bee3114c39763a4a08beeb663b145d283c31d7d1101c4f",
    "DOGE": "0xdcef50dd0a4cd2dcc17e45df1676dcb336a11a433ebb6c27ae0bf0f73e0dfee5",
    "LINK": "0x8ac0c70fff57e9aefdf5edf44b51d62c2d433653cbb2cf5cc06bb115af04d221",
    "XRP":  "0xec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8",
    "ARB":  "0x3fa4252848f9f0a1480be62745a4629d9eb1322aebab8a791e344b3b9c1adcf5",
    "OP":   "0x385f64d993f7b77d8182ed5003d97c60aa3361f3cecfe711544d2d59165e9bdf",
    "SUI":  "0x23d7315113f5b1d3ba7a83604c44b94d79f4fd69af77f804fc7f920a6dc65744",
    "INJ":  "0x7a5bc1d2b56ad029048cd63964b3ad2776eaface36e3ae8bf44d3f81ee7a4b78",
    "WIF":  "0x4ca4beeca86f0d164160323817a4e42b10010a724c2217c6ee41b54cd4cc61fc",
    "APT":  "0x03ae4db29ed4ae33d323568895aa00337e658e348b37509f5372ae51f0af00d5",
}

BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BLUE   = "\033[94m"
MAGENTA = "\033[95m"
RESET  = "\033[0m"


# ── Venue data structures ────────────────────────────────────────────────────

@dataclass
class VenueSnapshot:
    venue: str
    ts: str
    markets: dict              # symbol → {mark, oracle, funding_bps_hr, vol_24h, ...}
    fetch_ms: float = 0.0      # how long the fetch took
    error: Optional[str] = None


@dataclass
class SignalEvent:
    ts: str
    scan_id: int
    signal_class: str          # "arb" | "sniper"
    signal_type: str           # from ArbSignal/SniperSignal
    symbol: str
    venue: str
    confidence_or_urgency: str
    estimated_edge_bps: float
    detail: dict               # full signal dict


# ── Rolling statistics ────────────────────────────────────────────────────────

class RollingStats:
    """
    Tracks signal frequency, persistence, and per-symbol metrics
    over a configurable rolling window.
    """
    def __init__(self, window_hours: int = 24):
        self.window_sec = window_hours * 3600
        self._events: list[SignalEvent] = []
        self._lock = threading.Lock()

    def add(self, events: list[SignalEvent]) -> None:
        now = time.time()
        cutoff = now - self.window_sec
        with self._lock:
            self._events.extend(events)
            # Prune old events
            self._events = [e for e in self._events if _ts_to_unix(e.ts) > cutoff]

    def summary(self) -> dict:
        with self._lock:
            events = list(self._events)
        if not events:
            return {"window_hours": self.window_sec // 3600, "total_signals": 0}

        by_symbol: dict[str, list] = collections.defaultdict(list)
        by_type:   dict[str, int]  = collections.Counter()
        by_conf:   dict[str, int]  = collections.Counter()

        for e in events:
            by_symbol[e.symbol].append(e)
            by_type[e.signal_type] += 1
            by_conf[e.confidence_or_urgency] += 1

        top_symbols = sorted(by_symbol.items(), key=lambda x: len(x[1]), reverse=True)[:10]

        return {
            "window_hours": self.window_sec // 3600,
            "total_signals": len(events),
            "by_type": dict(by_type),
            "by_confidence": dict(by_conf),
            "top_symbols": [
                {"symbol": sym, "count": len(evts),
                 "avg_edge_bps": sum(e.estimated_edge_bps for e in evts) / len(evts)}
                for sym, evts in top_symbols
            ],
        }

    def print_summary(self) -> None:
        s = self.summary()
        print(f"\n{BOLD}{'─'*64}{RESET}")
        print(f"{BOLD}  ROLLING STATS  [{s['window_hours']}h window]  total={s['total_signals']}{RESET}")
        print(f"{'─'*64}")
        if not s.get("by_type"):
            print("  (no signals yet)")
            return
        print(f"  By type:       {s['by_type']}")
        print(f"  By confidence: {s['by_confidence']}")
        print(f"\n  Top symbols by signal frequency:")
        for row in s.get("top_symbols", [])[:10]:
            bar = "█" * min(20, row["count"])
            print(f"    {row['symbol']:<10} {bar:<22} {row['count']:>4} signals  "
                  f"avg_edge={row['avg_edge_bps']:.1f}bps")


def _ts_to_unix(ts: str) -> float:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


# ── Phoenix DEX fetcher ───────────────────────────────────────────────────────

def fetch_phoenix_spot(symbols: Optional[list[str]] = None) -> dict[str, dict]:
    """
    Fetch Phoenix DEX spot markets (Solana CLOB).
    Returns {symbol: {bid, ask, last, vol_24h}} for supported pairs.
    Phoenix is spot-only — use for HL perp vs Phoenix spot basis arb.
    """
    try:
        url = f"{PHOENIX_API}/markets"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())

        result = {}
        markets = data if isinstance(data, list) else data.get("markets", [])
        for m in markets:
            base = str(m.get("baseMint", "") or m.get("base", "")).upper()
            # Common Phoenix spot pairs
            if symbols and base not in symbols:
                continue
            bid = float(m.get("bestBidPrice") or m.get("bid") or 0)
            ask = float(m.get("bestAskPrice") or m.get("ask") or 0)
            result[base] = {
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2 if bid and ask else 0,
                "vol_24h": float(m.get("volume24h") or m.get("vol24h") or 0),
                "source": "phoenix_spot",
            }
        return result
    except Exception as e:
        return {"_error": str(e)}


def fetch_mango_markets(symbols: Optional[list[str]] = None) -> dict[str, dict]:
    """
    Fetch Mango Markets perp data.
    Returns {symbol: {mark, oracle, funding_rate, vol_24h, oi}}.
    """
    try:
        url = f"{MANGO_API}/stats/perp-market-summary"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())

        result = {}
        markets = data if isinstance(data, list) else data.get("data", [])
        for m in markets:
            sym = str(m.get("name", "")).replace("-PERP", "").upper()
            if symbols and sym not in symbols:
                continue
            result[sym] = {
                "mark_price": float(m.get("markPrice") or 0),
                "oracle_price": float(m.get("oraclePrice") or 0),
                "funding_rate": float(m.get("fundingRate") or 0),
                "open_interest": float(m.get("openInterest") or 0),
                "volume_24h": float(m.get("volume24h") or 0),
                "source": "mango",
            }
        return result
    except Exception as e:
        return {"_error": str(e)}


# ── Phoenix basis arb detector ────────────────────────────────────────────────

@dataclass
class BasisSignal:
    ts: str
    symbol: str
    hl_perp_mark: float
    phoenix_spot_mid: float
    basis_bps: float          # (perp - spot) / spot * 1e4
    direction: str            # "LONG_SPOT_SHORT_PERP" | "LONG_PERP_SHORT_SPOT"
    confidence: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


BASIS_THRESHOLD_BPS = 30.0   # min basis to flag (after fees ~8bps total)


def detect_basis_arb(
    hl_markets: list[dict],
    phoenix_spot: dict[str, dict],
    mango_markets: Optional[dict[str, dict]] = None,
) -> list[BasisSignal]:
    """
    HL perp vs Phoenix spot basis arb.
    When the HL perpetual premium over spot exceeds fees, there is a cash-and-carry edge.
    Also checks Mango perps as an alternative venue.
    """
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    signals = []
    hl_by_sym = {m.get("coin", "").split(":")[-1].upper(): m for m in hl_markets}

    for sym, spot_m in phoenix_spot.items():
        if sym == "_error":
            continue
        hl_m = hl_by_sym.get(sym)
        if not hl_m:
            continue

        hl_mark = float(hl_m.get("mark") or 0)
        spot_mid = float(spot_m.get("mid") or 0)

        if not hl_mark or not spot_mid:
            continue

        basis_bps = (hl_mark - spot_mid) / spot_mid * 1e4

        if abs(basis_bps) < BASIS_THRESHOLD_BPS:
            continue

        direction = (
            "LONG_SPOT_SHORT_PERP" if basis_bps > 0   # perp expensive → short perp, buy spot
            else "LONG_PERP_SHORT_SPOT"                # spot expensive → buy perp, sell spot
        )
        conf = "HIGH" if abs(basis_bps) > 80 else ("MED" if abs(basis_bps) > 50 else "LOW")
        note = f"HL_perp={hl_mark:,.4f} phoenix_spot={spot_mid:,.4f}"

        # Check if Mango also has this perp (third leg for triangular)
        if mango_markets and sym in mango_markets:
            mango_m = mango_markets[sym]
            mango_mark = float(mango_m.get("mark_price") or 0)
            if mango_mark:
                mango_basis = (mango_mark - spot_mid) / spot_mid * 1e4
                note += f" mango_basis={mango_basis:+.1f}bps"

        signals.append(BasisSignal(
            ts=ts, symbol=sym,
            hl_perp_mark=hl_mark, phoenix_spot_mid=spot_mid,
            basis_bps=basis_bps, direction=direction, confidence=conf, note=note,
        ))

    return sorted(signals, key=lambda s: -abs(s.basis_bps))


# ── WebSocket feed (threading-based, non-blocking) ───────────────────────────

class WebSocketFeed:
    """
    Lightweight WebSocket reader using only stdlib (no websockets/aiohttp needed).
    Uses a background thread to maintain a live price cache from HL or Pyth WS.
    Falls back gracefully to REST if WS connection fails.
    """
    def __init__(self, url: str, name: str):
        self.url = url
        self.name = name
        self._cache: dict = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._last_msg = 0.0

    def start(self) -> bool:
        """Start background WebSocket reader. Returns False if websocket unavailable."""
        try:
            import websocket  # type: ignore  # pip install websocket-client
        except ImportError:
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Wait up to 3s for connection
        for _ in range(30):
            if self._connected:
                return True
            time.sleep(0.1)
        return self._connected

    def _run(self) -> None:
        try:
            import websocket
            ws = websocket.WebSocketApp(
                self.url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"  [{self.name}] WS error: {e}")

    def _on_open(self, ws) -> None:
        self._connected = True
        if "hyperliquid" in self.url:
            ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}}))
        elif "pyth" in self.url:
            feeds = list(PYTH_FEED_IDS.values())
            ws.send(json.dumps({"type": "subscribe", "ids": feeds}))

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
            with self._lock:
                if "hyperliquid" in self.url:
                    mids = data.get("data", {}).get("mids", {})
                    if mids:
                        for sym, price in mids.items():
                            self._cache[sym] = float(price)
                elif "pyth" in self.url:
                    for update in data.get("parsed", []):
                        feed_id = update.get("id", "")
                        for sym, fid in PYTH_FEED_IDS.items():
                            if fid.lstrip("0x") in feed_id.lstrip("0x"):
                                price_data = update.get("price", {})
                                if price_data:
                                    exponent = int(price_data.get("expo", 0))
                                    raw = int(price_data.get("price", 0))
                                    self._cache[sym] = raw * (10 ** exponent)
            self._last_msg = time.time()
        except Exception:
            pass

    def _on_error(self, ws, error) -> None:
        self._connected = False

    def _on_close(self, ws, code, msg) -> None:
        self._connected = False

    def stop(self) -> None:
        self._running = False

    def get_cache(self) -> dict:
        with self._lock:
            return dict(self._cache)

    @property
    def is_live(self) -> bool:
        return self._connected and (time.time() - self._last_msg < 60)


# ── Venue fetcher with caching ────────────────────────────────────────────────

class VenueManager:
    """
    Manages data fetching across all venues with caching, error tracking,
    and optional WebSocket feeds.
    """
    def __init__(self, venues: set[str], mock: bool = False, use_ws: bool = False):
        self.venues = venues
        self.mock = mock
        self.use_ws = use_ws
        self._cache: dict[str, VenueSnapshot] = {}
        self._ws_hl: Optional[WebSocketFeed] = None
        self._ws_pyth: Optional[WebSocketFeed] = None
        self._error_counts: dict[str, int] = collections.defaultdict(int)
        self._fetch_times: dict[str, list[float]] = collections.defaultdict(list)

        if use_ws and not mock:
            self._start_ws()

    def _start_ws(self) -> None:
        if "hl" in self.venues:
            self._ws_hl = WebSocketFeed(HL_WS_URL, "HL-WS")
            ok = self._ws_hl.start()
            print(f"  HL WebSocket: {'connected' if ok else 'unavailable (REST fallback)'}")

        if "pyth" in self.venues:
            self._ws_pyth = WebSocketFeed(PYTH_WS_URL, "Pyth-WS")
            ok = self._ws_pyth.start()
            print(f"  Pyth WebSocket: {'connected' if ok else 'unavailable (REST fallback)'}")

    def stop_ws(self) -> None:
        if self._ws_hl:
            self._ws_hl.stop()
        if self._ws_pyth:
            self._ws_pyth.stop()

    def fetch_all(self) -> dict:
        """
        Fetch data from all active venues. Returns a dict:
        {
            "hl_markets": [...],
            "drift_snapshot": {...},
            "pyth_prices": {...},
            "phoenix_spot": {...},
            "mango_markets": {...},
            "fetch_stats": {...},
        }
        """
        if self.mock:
            return {
                "hl_markets": make_hl_markets(),
                "drift_snapshot": make_drift_snapshot(),
                "pyth_prices": make_pyth_prices(),
                "phoenix_spot": {},
                "mango_markets": {},
                "fetch_stats": {"mode": "mock"},
            }

        result: dict = {
            "hl_markets": [],
            "drift_snapshot": {},
            "pyth_prices": {},
            "phoenix_spot": {},
            "mango_markets": {},
            "fetch_stats": {},
        }
        threads = []

        if "hl" in self.venues:
            t = threading.Thread(target=self._fetch_hl, args=(result,), daemon=True)
            threads.append(t)

        if "drift" in self.venues:
            t = threading.Thread(target=self._fetch_drift, args=(result,), daemon=True)
            threads.append(t)

        if "pyth" in self.venues:
            t = threading.Thread(target=self._fetch_pyth, args=(result,), daemon=True)
            threads.append(t)

        if "phoenix" in self.venues:
            t = threading.Thread(target=self._fetch_phoenix, args=(result,), daemon=True)
            threads.append(t)

        if "mango" in self.venues:
            t = threading.Thread(target=self._fetch_mango, args=(result,), daemon=True)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        return result

    def _fetch_hl(self, result: dict) -> None:
        t0 = time.time()
        try:
            if self._ws_hl and self._ws_hl.is_live:
                # Augment cached REST data with WS live mids
                mids = self._ws_hl.get_cache()
                cached = self._cache.get("hl")
                markets = cached.markets if cached else {}
                for sym, mid in mids.items():
                    if sym in markets:
                        markets[sym]["mark"] = mid
                result["hl_markets"] = list(markets.values()) if markets else []
            else:
                if not _HL_AVAILABLE:
                    raise ImportError("hl_data not available")
                markets = fetch_all_markets()
                result["hl_markets"] = markets
                # Cache for WS augmentation
                self._cache["hl"] = VenueSnapshot(
                    venue="hl",
                    ts=datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    markets={m.get("coin", ""): m for m in markets},
                    fetch_ms=(time.time() - t0) * 1000,
                )
            self._error_counts["hl"] = 0
        except Exception as e:
            self._error_counts["hl"] += 1
            result["fetch_stats"]["hl_error"] = str(e)

        elapsed = (time.time() - t0) * 1000
        self._fetch_times["hl"].append(elapsed)
        if len(self._fetch_times["hl"]) > 100:
            self._fetch_times["hl"].pop(0)
        result["fetch_stats"]["hl_ms"] = round(elapsed, 1)

    def _fetch_drift(self, result: dict) -> None:
        t0 = time.time()
        try:
            if not _SOLANA_AVAILABLE:
                raise ImportError("solana_data not available")
            result["drift_snapshot"] = get_drift_snapshot()
            self._error_counts["drift"] = 0
        except Exception as e:
            self._error_counts["drift"] += 1
            result["fetch_stats"]["drift_error"] = str(e)
        result["fetch_stats"]["drift_ms"] = round((time.time() - t0) * 1000, 1)

    def _fetch_pyth(self, result: dict) -> None:
        t0 = time.time()
        try:
            if self._ws_pyth and self._ws_pyth.is_live:
                result["pyth_prices"] = self._ws_pyth.get_cache()
            else:
                if not _SOLANA_AVAILABLE:
                    raise ImportError("solana_data not available")
                syms = list(PYTH_FEED_IDS.keys())
                result["pyth_prices"] = fetch_pyth_prices(syms)
            self._error_counts["pyth"] = 0
        except Exception as e:
            self._error_counts["pyth"] += 1
            result["fetch_stats"]["pyth_error"] = str(e)
        result["fetch_stats"]["pyth_ms"] = round((time.time() - t0) * 1000, 1)

    def _fetch_phoenix(self, result: dict) -> None:
        t0 = time.time()
        try:
            result["phoenix_spot"] = fetch_phoenix_spot()
            self._error_counts["phoenix"] = 0
        except Exception as e:
            self._error_counts["phoenix"] += 1
            result["fetch_stats"]["phoenix_error"] = str(e)
        result["fetch_stats"]["phoenix_ms"] = round((time.time() - t0) * 1000, 1)

    def _fetch_mango(self, result: dict) -> None:
        t0 = time.time()
        try:
            result["mango_markets"] = fetch_mango_markets()
            self._error_counts["mango"] = 0
        except Exception as e:
            self._error_counts["mango"] += 1
            result["fetch_stats"]["mango_error"] = str(e)
        result["fetch_stats"]["mango_ms"] = round((time.time() - t0) * 1000, 1)

    def health_line(self) -> str:
        parts = []
        for v in sorted(self.venues):
            err = self._error_counts.get(v, 0)
            avg_ms = 0.0
            times = self._fetch_times.get(v, [])
            if times:
                avg_ms = sum(times[-10:]) / len(times[-10:])
            color = RED if err > 2 else (YELLOW if err > 0 else GREEN)
            ws_tag = ""
            if v == "hl" and self._ws_hl and self._ws_hl.is_live:
                ws_tag = "[WS]"
            elif v == "pyth" and self._ws_pyth and self._ws_pyth.is_live:
                ws_tag = "[WS]"
            parts.append(f"{color}{v.upper()}{ws_tag}{RESET}:{avg_ms:.0f}ms")
        return "  ".join(parts)


# ── Signal logger ─────────────────────────────────────────────────────────────

class SignalLogger:
    """Logs all signal events to JSONL and tracks a rolling in-memory buffer."""
    def __init__(self, log_path: str):
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.log_path = log_path
        self._buffer: list[dict] = []

    def log(self, events: list[SignalEvent]) -> None:
        rows = [asdict(e) for e in events]
        self._buffer.extend(rows)
        if len(self._buffer) > 10_000:
            self._buffer = self._buffer[-10_000:]
        with open(self.log_path, "a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def tail(self, n: int = 20) -> list[dict]:
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path) as f:
            lines = f.readlines()
        result = []
        for line in lines[-n:]:
            try:
                result.append(json.loads(line))
            except Exception:
                pass
        return result


# ── Display helpers ───────────────────────────────────────────────────────────

def _banner() -> None:
    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║        Multi-Venue Arb & Sniper Monitor  v2.0                   ║{RESET}")
    print(f"{BOLD}{CYAN}║  Venues: Hyperliquid · Drift · Phoenix · Mango · Pyth           ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════════════════════════╝{RESET}\n")


def _print_basis_signals(signals: list[BasisSignal], top_n: int = 10) -> None:
    if not signals:
        return
    conf_color = {"HIGH": RED, "MED": YELLOW, "LOW": GREEN}
    print(f"\n  {BOLD}── PHOENIX BASIS ARB (HL perp vs spot)  ({len(signals)} signals) ──{RESET}")
    print(f"  {'SYM':<8} {'HL_PERP':>12} {'PHX_SPOT':>12} {'BASIS(bps)':>12} {'DIR':<28} {'CONF'}")
    for s in signals[:top_n]:
        col = conf_color.get(s.confidence, "")
        print(f"  {s.symbol:<8} {s.hl_perp_mark:>12,.4f} {s.phoenix_spot_mid:>12,.4f} "
              f"{s.basis_bps:>+12.2f} {s.direction:<28} {col}{s.confidence}{RESET}")


def _print_venue_bar(scan_id: int, ts: str, summary: dict, venue_manager: VenueManager) -> None:
    arb_h = summary.get("arb_high", 0)
    arb_t = summary.get("arb_total", 0)
    snp_i = summary.get("sniper_immediate", 0)
    snp_t = summary.get("sniper_total", 0)
    bas_h = summary.get("basis_high", 0)
    bas_t = summary.get("basis_total", 0)

    arb_col  = RED if arb_h > 0 else (YELLOW if arb_t > 0 else GREEN)
    snp_col  = RED if snp_i > 0 else (YELLOW if snp_t > 0 else GREEN)
    bas_col  = RED if bas_h > 0 else (YELLOW if bas_t > 0 else GREEN)

    print(f"\n{BOLD}[{ts}]  Scan #{scan_id}{RESET}")
    print(
        f"  Arb: {arb_col}{arb_h}H/{arb_t}T{RESET}  "
        f"Sniper: {snp_col}{snp_i}IMM/{snp_t}T{RESET}  "
        f"Basis: {bas_col}{bas_h}H/{bas_t}T{RESET}"
    )
    print(f"  Venues: {venue_manager.health_line()}")


# ── Core scan ────────────────────────────────────────────────────────────────

def run_scan(
    data: dict,
    scan_id: int,
    quiet: bool = False,
    top_n: int = 15,
) -> tuple[dict, list[SignalEvent]]:
    """
    Run all signal detectors against fetched venue data.
    Returns (summary_dict, [SignalEvent, ...]).
    """
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    hl_markets     = data.get("hl_markets", [])
    drift_snapshot = data.get("drift_snapshot", {})
    pyth_prices    = data.get("pyth_prices", {})
    phoenix_spot   = data.get("phoenix_spot", {})
    mango_markets  = data.get("mango_markets", {})

    # 1. Cross-venue arb (HL vs Drift)
    arb_signals    = scan_arb_opportunities(hl_markets, drift_snapshot, pyth_prices)
    # 2. Sniper patterns
    sniper_signals = run_full_sniper_scan(hl_markets, drift_snapshot, pyth_prices)
    # 3. Basis arb (HL perp vs Phoenix spot)
    basis_signals  = detect_basis_arb(hl_markets, phoenix_spot, mango_markets) if phoenix_spot else []

    if not quiet:
        if arb_signals:
            print(f"\n{BOLD}── CROSS-VENUE ARB  (HL × Drift){RESET}")
            print_signals(arb_signals, top_n=top_n)
        if sniper_signals:
            print(f"\n{BOLD}── SNIPER SIGNALS{RESET}")
            print_sniper_report(sniper_signals, top_n=top_n)
        if basis_signals:
            _print_basis_signals(basis_signals, top_n=10)

    # Convert to SignalEvent list for logging
    events: list[SignalEvent] = []
    for s in arb_signals:
        events.append(SignalEvent(
            ts=ts, scan_id=scan_id, signal_class="arb",
            signal_type=s.signal_type, symbol=s.symbol,
            venue="HL_DRIFT", confidence_or_urgency=s.confidence,
            estimated_edge_bps=s.price_gap_bps or s.funding_gap_bps or 0,
            detail=s.to_dict(),
        ))
    for s in sniper_signals:
        events.append(SignalEvent(
            ts=ts, scan_id=scan_id, signal_class="sniper",
            signal_type=s.pattern, symbol=s.symbol,
            venue=s.venue, confidence_or_urgency=s.urgency,
            estimated_edge_bps=s.estimated_edge_bps,
            detail=s.to_dict(),
        ))
    for s in basis_signals:
        events.append(SignalEvent(
            ts=ts, scan_id=scan_id, signal_class="basis",
            signal_type="phoenix_basis", symbol=s.symbol,
            venue="HL_PHOENIX", confidence_or_urgency=s.confidence,
            estimated_edge_bps=abs(s.basis_bps),
            detail=s.to_dict(),
        ))

    summary = {
        "ts": ts,
        "scan_id": scan_id,
        "hl_markets": len(hl_markets),
        "drift_markets": len(drift_snapshot),
        "phoenix_markets": len(phoenix_spot),
        "mango_markets": len(mango_markets),
        "arb_total": len(arb_signals),
        "arb_high": sum(1 for s in arb_signals if s.confidence == "HIGH"),
        "sniper_total": len(sniper_signals),
        "sniper_immediate": sum(1 for s in sniper_signals if s.urgency == "IMMEDIATE"),
        "basis_total": len(basis_signals),
        "basis_high": sum(1 for s in basis_signals if s.confidence == "HIGH"),
        "fetch_stats": data.get("fetch_stats", {}),
        "top_arb": [s.to_dict() for s in arb_signals[:5]],
        "top_sniper": [s.to_dict() for s in sniper_signals[:5]],
        "top_basis": [s.to_dict() for s in basis_signals[:3]],
    }
    return summary, events


# ── Main loop ─────────────────────────────────────────────────────────────────

def monitor_loop(
    venues: set[str],
    interval: int,
    quiet: bool,
    mock: bool,
    use_ws: bool,
    top_n: int = 15,
    stats_window_h: int = 24,
) -> None:
    _banner()
    if mock:
        print(f"  {YELLOW}[MOCK MODE] Using fixture data — no live API calls{RESET}")
    print(f"  Venues: {', '.join(sorted(venues)).upper()}")
    print(f"  Interval: {interval}s | WS: {'enabled' if use_ws else 'disabled'}\n")

    mgr    = VenueManager(venues, mock=mock, use_ws=use_ws)
    logger = SignalLogger(SIGNAL_LOG)
    stats  = RollingStats(window_hours=stats_window_h)

    scan_id  = 0
    try:
        while True:
            scan_id += 1
            ts_now = datetime.datetime.utcnow().isoformat(timespec="seconds")
            print(f"\n{BOLD}{'━'*66}{RESET}")
            print(f"  Scan #{scan_id}  {ts_now}Z  interval={interval}s{'  [MOCK]' if mock else ''}")
            print(f"{'━'*66}")

            try:
                t0 = time.time()
                data = mgr.fetch_all()
                fetch_t = time.time() - t0
                print(f"  Fetch: {fetch_t*1000:.0f}ms  "
                      f"HL={len(data['hl_markets'])}  Drift={len(data['drift_snapshot'])}  "
                      f"Phoenix={len(data['phoenix_spot'])}  Pyth={len(data['pyth_prices'])}")

                summary, events = run_scan(data, scan_id, quiet=quiet, top_n=top_n)
                logger.log(events)
                stats.add(events)

                # Write report
                with open(REPORT_FILE, "w") as f:
                    json.dump(summary, f, indent=2)

                _print_venue_bar(scan_id, summary["ts"], summary, mgr)

                # Print rolling stats every 10 scans
                if scan_id % 10 == 0:
                    stats.print_summary()

            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"\n  {RED}[ERROR] scan #{scan_id} failed: {e}{RESET}")
                traceback.print_exc()

            print(f"\n  Next scan in {interval}s... (Ctrl-C to stop)")
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n{YELLOW}  Monitor stopped.{RESET}")
        stats.print_summary()
        mgr.stop_ws()
        print(f"  Signal log: {SIGNAL_LOG}")
        print(f"  Report:     {REPORT_FILE}")


def single_scan_cmd(venues: set[str], quiet: bool, mock: bool, use_ws: bool) -> None:
    _banner()
    if mock:
        print(f"  {YELLOW}[MOCK]{RESET} Using fixture data")
    mgr    = VenueManager(venues, mock=mock, use_ws=False)
    logger = SignalLogger(SIGNAL_LOG)
    stats  = RollingStats()

    print(f"  [{datetime.datetime.utcnow().isoformat(timespec='seconds')}Z] Fetching...")
    data = mgr.fetch_all()
    print(f"  HL={len(data['hl_markets'])}  Drift={len(data['drift_snapshot'])}  "
          f"Phoenix={len(data['phoenix_spot'])}  Pyth={len(data['pyth_prices'])}")

    summary, events = run_scan(data, scan_id=1, quiet=quiet)
    logger.log(events)
    stats.add(events)

    _print_venue_bar(1, summary["ts"], summary, mgr)

    with open(REPORT_FILE, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  Signal log: {SIGNAL_LOG}")
    print(f"  Report:     {REPORT_FILE}")


def tail_signals_cmd(n: int) -> None:
    logger = SignalLogger(SIGNAL_LOG)
    rows = logger.tail(n)
    if not rows:
        print(f"[!] No signal log found at {SIGNAL_LOG}")
        return
    print(f"=== Last {len(rows)} signals from {SIGNAL_LOG} ===\n")
    for r in rows:
        urgency = r.get("confidence_or_urgency", "?")
        col = RED if urgency in ("HIGH", "IMMEDIATE") else (YELLOW if urgency in ("MED", "WATCH") else GREEN)
        print(
            f"  [{r.get('ts', '?')}] scan#{r.get('scan_id','?'):<4} "
            f"{r.get('signal_class','?'):<8} {r.get('signal_type','?'):<22} "
            f"{r.get('symbol','?'):<10} venue={r.get('venue','?'):<12} "
            f"{col}{urgency:<12}{RESET} edge={r.get('estimated_edge_bps', 0):.1f}bps"
        )


def stats_cmd(window_h: int) -> None:
    logger = SignalLogger(SIGNAL_LOG)
    all_rows = logger.tail(100_000)
    if not all_rows:
        print(f"[!] No signal log found at {SIGNAL_LOG}")
        return
    stats = RollingStats(window_hours=window_h)
    for row in all_rows:
        try:
            e = SignalEvent(**{k: row[k] for k in SignalEvent.__dataclass_fields__})
            stats._events.append(e)
        except Exception:
            pass
    stats.print_summary()


# ── CLI ───────────────────────────────────────────────────────────────────────

ALL_VENUES = {"hl", "drift", "pyth", "phoenix", "mango"}

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Multi-venue arb & sniper monitor: HL + Drift + Phoenix + Mango + Pyth",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 multi_venue_monitor.py --mock --once          # quick test with fixture data
  python3 multi_venue_monitor.py --venues hl drift pyth # HL+Drift+Pyth only, live
  python3 multi_venue_monitor.py --interval 30 --ws     # 30s polls with WS for HL+Pyth
  python3 multi_venue_monitor.py --tail 50              # show last 50 logged signals
  python3 multi_venue_monitor.py --stats                # 24h rolling statistics
""",
    )
    ap.add_argument(
        "--venues", nargs="+", default=list(ALL_VENUES),
        choices=list(ALL_VENUES), metavar="VENUE",
        help="Venues to monitor (default: all). Options: hl drift pyth phoenix mango",
    )
    ap.add_argument("--interval", type=int, default=60, help="Scan interval in seconds (default: 60)")
    ap.add_argument("--once",     action="store_true",  help="Single scan then exit")
    ap.add_argument("--quiet",    action="store_true",  help="Suppress verbose signal tables")
    ap.add_argument("--mock",     action="store_true",  help="Use fixture data (no live APIs)")
    ap.add_argument("--ws",       action="store_true",  help="Enable WebSocket feeds for HL and Pyth")
    ap.add_argument("--tail",     type=int, default=0,  help="Tail last N signals from log")
    ap.add_argument("--stats",    action="store_true",  help="Show rolling statistics from log")
    ap.add_argument("--stats-window", type=int, default=24, metavar="HOURS",
                    help="Rolling window for stats in hours (default: 24)")
    ap.add_argument("--top",      type=int, default=15, help="Top N signals to display per category")
    args = ap.parse_args()

    venues = set(args.venues)

    if args.tail > 0:
        tail_signals_cmd(args.tail)
        return

    if args.stats:
        stats_cmd(args.stats_window)
        return

    if args.once:
        single_scan_cmd(venues, quiet=args.quiet, mock=args.mock, use_ws=args.ws)
        return

    monitor_loop(
        venues=venues,
        interval=args.interval,
        quiet=args.quiet,
        mock=args.mock,
        use_ws=args.ws,
        top_n=args.top,
        stats_window_h=args.stats_window,
    )


if __name__ == "__main__":
    main()
