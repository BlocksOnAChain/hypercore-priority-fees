#!/usr/bin/env python3
"""
arb_scanner.py — Cross-venue arbitrage signal engine: Hyperliquid vs Drift (Solana).

Detects:
1. Mark-price spread arb: when HL and Drift prices diverge > threshold
2. Funding-rate arb: when funding rates diverge significantly between venues
3. Dislocation alpha: venues where oracle dislocation creates stale-quote opportunity

All signals are read-only and educational. No trades are placed.
"""
from __future__ import annotations
import datetime
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from hl_data import fetch_all_markets, compute_pfi
from solana_data import get_drift_snapshot, fetch_pyth_prices


# ── Signal types ────────────────────────────────────────────────────────────
@dataclass
class ArbSignal:
    ts: str
    signal_type: str          # "mark_spread" | "funding_arb" | "dislocation_spike"
    symbol: str
    hl_price: Optional[float]
    drift_price: Optional[float]
    price_gap_bps: Optional[float]
    hl_funding_bps: Optional[float]
    drift_funding_bps: Optional[float]
    funding_gap_bps: Optional[float]
    hl_disloc_bps: Optional[float]
    drift_disloc_bps: Optional[float]
    pfi_score: Optional[float]
    direction: str            # "BUY_HL_SELL_DRIFT" | "BUY_DRIFT_SELL_HL" | "FUNDING_LONG_HL" | etc.
    confidence: str           # "HIGH" | "MED" | "LOW"
    note: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ── Configuration ────────────────────────────────────────────────────────────
MARK_SPREAD_THRESHOLD_BPS = 5.0    # min price gap to flag (bps)
FUNDING_GAP_THRESHOLD_BPS = 2.0    # min funding divergence/hr (bps)
DISLOC_SPIKE_THRESHOLD_BPS = 20.0  # min oracle dislocation for sniper signal

# Common symbols traded on both venues
COMMON_SYMBOLS = {
    "BTC", "ETH", "SOL", "AVAX", "BNB", "DOGE", "LINK",
    "XRP", "ARB", "OP", "SUI", "APT", "INJ", "WIF",
}

# Mapping: HL coin names → Drift symbol (only where non-obvious)
HL_TO_DRIFT_SYMBOL: dict[str, str] = {
    "1KPEPE": "1KPEPE",
    "1KBONK": "1KBONK",
}


def _fnum(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize_symbol(coin: str) -> str:
    """Strip dex prefix (e.g. 'xyz:SKHX' → 'SKHX')."""
    return coin.split(":")[-1].upper() if coin else ""


def funding_to_bps_hr(raw_funding: Optional[float], source: str = "hl") -> Optional[float]:
    """
    Convert raw funding to bps per hour.
    HL funding is already in per-hour decimal (e.g. 0.0001 = 1 bps/hr).
    Drift funding varies by implementation (may be per-period); normalize here.
    """
    if raw_funding is None:
        return None
    if source == "hl":
        return raw_funding * 1e4       # decimal/hr → bps/hr
    elif source == "drift":
        # Drift returns funding in 1e-6 per period (1hr default)
        # If it comes back as decimal it's already per-period
        f = raw_funding
        # Heuristic: if |f| < 0.01 treat as decimal fraction
        if abs(f) < 0.01:
            return f * 1e4
        # Otherwise treat as already in bps
        return f
    return raw_funding * 1e4


# ── Main scan function ───────────────────────────────────────────────────────

def scan_arb_opportunities(
    hl_markets: Optional[list[dict]] = None,
    drift_snapshot: Optional[dict] = None,
    pyth_prices: Optional[dict[str, float]] = None,
) -> list[ArbSignal]:
    """
    Run a full arb scan across HL + Drift.
    Fetches live data if not provided (allows pre-fetched data for efficiency).
    Returns a list of ArbSignal objects sorted by confidence then price gap.
    """
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    # Fetch live data if not pre-supplied
    if hl_markets is None:
        try:
            hl_markets = fetch_all_markets()
        except Exception as e:
            hl_markets = []
            print(f"  [WARN] HL fetch failed: {e}")

    if drift_snapshot is None:
        try:
            drift_snapshot = get_drift_snapshot()
        except Exception as e:
            drift_snapshot = {}
            print(f"  [WARN] Drift fetch failed: {e}")

    if pyth_prices is None:
        try:
            pyth_prices = fetch_pyth_prices(list(COMMON_SYMBOLS))
        except Exception:
            pyth_prices = {}

    # Index HL markets by normalized symbol
    hl_by_symbol: dict[str, dict] = {}
    for m in hl_markets:
        sym = normalize_symbol(m.get("coin", ""))
        if sym in COMMON_SYMBOLS:
            hl_by_symbol[sym] = m

    signals: list[ArbSignal] = []

    # ── 1. Mark-price spread arb ─────────────────────────────────────────────
    for sym in COMMON_SYMBOLS:
        drift_sym = HL_TO_DRIFT_SYMBOL.get(sym, sym)
        hl_m = hl_by_symbol.get(sym)
        drift_m = drift_snapshot.get(drift_sym) or drift_snapshot.get(sym)

        if not hl_m or not drift_m:
            continue

        # HL rows use: mark, oracle, funding_bps_hr, disloc_bps, pfi
        hl_mark = _fnum(hl_m.get("mark"))
        drift_mark = _fnum(drift_m.get("mark_price"))

        # Use Pyth as reference oracle if available
        pyth_ref = pyth_prices.get(sym)

        if hl_mark is None or drift_mark is None or hl_mark == 0:
            continue

        gap_bps = abs(hl_mark - drift_mark) / ((hl_mark + drift_mark) / 2) * 1e4
        if gap_bps < MARK_SPREAD_THRESHOLD_BPS:
            continue

        direction = "BUY_HL_SELL_DRIFT" if hl_mark < drift_mark else "BUY_DRIFT_SELL_HL"

        # Confidence: higher if gap is large and pyth reference confirms direction
        if pyth_ref:
            pyth_confirms_hl = abs(hl_mark - pyth_ref) < abs(drift_mark - pyth_ref)
            pyth_confirms_drift = abs(drift_mark - pyth_ref) < abs(hl_mark - pyth_ref)
        else:
            pyth_confirms_hl = pyth_confirms_drift = False

        if gap_bps > 20:
            conf = "HIGH"
        elif gap_bps > 10:
            conf = "MED"
        else:
            conf = "LOW"

        hl_disloc = _fnum(hl_m.get("disloc_bps"))
        drift_oracle = _fnum(drift_m.get("oracle_price"))
        drift_disloc = abs(drift_mark - drift_oracle) / drift_oracle * 1e4 if (drift_mark and drift_oracle and drift_oracle > 0) else None

        # HL funding already in bps/hr (field: funding_bps_hr); Drift is raw decimal
        hl_fund = _fnum(hl_m.get("funding_bps_hr"))
        drift_fund = funding_to_bps_hr(_fnum(drift_m.get("funding_rate")), "drift")
        fund_gap = abs(hl_fund - drift_fund) if (hl_fund is not None and drift_fund is not None) else None

        note = ""
        if pyth_ref:
            note = f"Pyth={pyth_ref:,.2f} confirms {'HL' if pyth_confirms_hl else 'Drift'}"

        signals.append(ArbSignal(
            ts=ts,
            signal_type="mark_spread",
            symbol=sym,
            hl_price=hl_mark,
            drift_price=drift_mark,
            price_gap_bps=gap_bps,
            hl_funding_bps=hl_fund,
            drift_funding_bps=drift_fund,
            funding_gap_bps=fund_gap,
            hl_disloc_bps=hl_disloc,
            drift_disloc_bps=drift_disloc,
            pfi_score=_fnum(hl_m.get("pfi")),
            direction=direction,
            confidence=conf,
            note=note,
        ))

    # ── 2. Funding-rate arb ──────────────────────────────────────────────────
    for sym in COMMON_SYMBOLS:
        drift_sym = HL_TO_DRIFT_SYMBOL.get(sym, sym)
        hl_m = hl_by_symbol.get(sym)
        drift_m = drift_snapshot.get(drift_sym) or drift_snapshot.get(sym)

        if not hl_m or not drift_m:
            continue

        hl_fund = _fnum(hl_m.get("funding_bps_hr"))
        drift_fund = funding_to_bps_hr(_fnum(drift_m.get("funding_rate")), "drift")

        if hl_fund is None or drift_fund is None:
            continue

        fund_gap = hl_fund - drift_fund
        if abs(fund_gap) < FUNDING_GAP_THRESHOLD_BPS:
            continue

        # Funding arb: long the lower-funding venue, short the higher-funding venue
        if fund_gap > 0:
            direction = "LONG_DRIFT_SHORT_HL"  # HL charges more → short HL
            note = f"HL pays {hl_fund:+.3f}bps/hr vs Drift {drift_fund:+.3f}bps/hr"
        else:
            direction = "LONG_HL_SHORT_DRIFT"
            note = f"Drift pays {drift_fund:+.3f}bps/hr vs HL {hl_fund:+.3f}bps/hr"

        conf = "HIGH" if abs(fund_gap) > 8 else ("MED" if abs(fund_gap) > 4 else "LOW")

        # Skip if already captured as mark_spread (avoid duplicate signals on same symbol)
        existing_syms = {s.symbol for s in signals if s.signal_type == "funding_arb"}
        if sym not in existing_syms:
            hl_mark = _fnum(hl_m.get("mark"))
            drift_mark = _fnum(drift_m.get("mark_price"))
            hl_oracle = _fnum(hl_m.get("oracle"))

            signals.append(ArbSignal(
                ts=ts,
                signal_type="funding_arb",
                symbol=sym,
                hl_price=hl_mark,
                drift_price=drift_mark,
                price_gap_bps=None,
                hl_funding_bps=hl_fund,
                drift_funding_bps=drift_fund,
                funding_gap_bps=abs(fund_gap),
                hl_disloc_bps=None,
                drift_disloc_bps=None,
                pfi_score=_fnum(hl_m.get("pfi")),
                direction=direction,
                confidence=conf,
                note=note,
            ))

    # ── 3. HL dislocation spikes (sniper targets) ────────────────────────────
    for m in hl_markets:
        sym = normalize_symbol(m.get("coin", ""))
        mark = _fnum(m.get("mark"))
        oracle = _fnum(m.get("oracle"))
        if not mark or not oracle or oracle == 0:
            continue
        disloc = abs(mark - oracle) / oracle * 1e4
        if disloc < DISLOC_SPIKE_THRESHOLD_BPS:
            continue

        pfi = _fnum(m.get("pfi"))
        direction = "SNIPER_LONG" if mark < oracle else "SNIPER_SHORT"
        conf = "HIGH" if disloc > 50 else ("MED" if disloc > 30 else "LOW")

        # Only flag if not already in signals
        existing = {s.symbol for s in signals if s.signal_type == "dislocation_spike"}
        if sym not in existing:
            signals.append(ArbSignal(
                ts=ts,
                signal_type="dislocation_spike",
                symbol=sym,
                hl_price=mark,
                drift_price=None,
                price_gap_bps=disloc,
                hl_funding_bps=funding_to_bps_hr(_fnum(m.get("funding") or m.get("funding_rate")), "hl"),
                drift_funding_bps=None,
                funding_gap_bps=None,
                hl_disloc_bps=disloc,
                drift_disloc_bps=None,
                pfi_score=pfi,
                direction=direction,
                confidence=conf,
                note=f"mark={'$' + f'{mark:,.4f}'} oracle={'$' + f'{oracle:,.4f}'}",
            ))

    # Sort: HIGH first, then by magnitude
    def sort_key(s: ArbSignal) -> tuple:
        conf_rank = {"HIGH": 0, "MED": 1, "LOW": 2}.get(s.confidence, 3)
        mag = s.price_gap_bps or s.funding_gap_bps or 0
        return (conf_rank, -mag)

    return sorted(signals, key=sort_key)


def print_signals(signals: list[ArbSignal], top_n: int = 20) -> None:
    """Pretty-print the top N signals to stdout."""
    if not signals:
        print("  (no signals above thresholds)")
        return

    # Group by type
    by_type: dict[str, list[ArbSignal]] = {}
    for s in signals:
        by_type.setdefault(s.signal_type, []).append(s)

    conf_color = {"HIGH": "\033[91m", "MED": "\033[93m", "LOW": "\033[92m"}
    reset = "\033[0m"

    for stype, group in by_type.items():
        label = {
            "mark_spread": "MARK-PRICE SPREAD ARB",
            "funding_arb": "FUNDING-RATE ARB",
            "dislocation_spike": "DISLOCATION SPIKES (HL SNIPER)",
        }.get(stype, stype.upper())
        print(f"\n  ── {label} ({len(group)} signals) ──")

        if stype == "mark_spread":
            print(f"  {'SYM':<8}{'HL $':>12}{'Drift $':>12}{'Gap(bps)':>10}{'Dir':<26}{'Conf':<6}{'Note'}")
            for s in group[:top_n]:
                col = conf_color.get(s.confidence, "")
                print(f"  {s.symbol:<8}{(s.hl_price or 0):>12,.3f}{(s.drift_price or 0):>12,.3f}"
                      f"{(s.price_gap_bps or 0):>10.2f}  {s.direction:<24}{col}{s.confidence}{reset}  {s.note}")

        elif stype == "funding_arb":
            print(f"  {'SYM':<8}{'HL fund(bps/hr)':>17}{'Drift fund':>12}{'Gap':>8}{'Dir':<28}{'Conf'}")
            for s in group[:top_n]:
                col = conf_color.get(s.confidence, "")
                print(f"  {s.symbol:<8}{(s.hl_funding_bps or 0):>17.4f}{(s.drift_funding_bps or 0):>12.4f}"
                      f"{(s.funding_gap_bps or 0):>8.3f}  {s.direction:<26}{col}{s.confidence}{reset}")

        elif stype == "dislocation_spike":
            print(f"  {'SYM':<12}{'Mark $':>14}{'Disloc(bps)':>13}{'PFI':>8}{'Dir':<16}{'Conf':<6}{'Note'}")
            for s in group[:top_n]:
                col = conf_color.get(s.confidence, "")
                print(f"  {s.symbol:<12}{(s.hl_price or 0):>14,.3f}"
                      f"{(s.hl_disloc_bps or 0):>13.2f}"
                      f"{(s.pfi_score or 0):>8.1f}"
                      f"  {s.direction:<14}{col}{s.confidence}{reset}  {s.note}")


if __name__ == "__main__":
    print("=== Cross-Venue Arb Scanner: Hyperliquid vs Drift ===")
    print(f"[{datetime.datetime.utcnow().isoformat(timespec='seconds')}Z] Fetching live data...")
    t0 = time.time()
    signals = scan_arb_opportunities()
    elapsed = time.time() - t0
    print(f"  Scan complete in {elapsed:.1f}s — {len(signals)} signals found")
    print_signals(signals)
