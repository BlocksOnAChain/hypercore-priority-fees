#!/usr/bin/env python3
"""
sniper_signals.py — Sniper opportunity detector for HL and Solana perps.

Detects four sniper patterns:
1. Dislocation spike: mark/oracle gap opens rapidly → stale-quote exploitation
2. Liquidation cascade zone: large OI + funding direction suggests forced unwind
3. Cross-venue momentum: price moving strongly on one venue before the other catches up
4. Funding cliff: funding rate extreme → likely mean reversion sniper entry

All analysis is read-only. No orders are placed.
"""
from __future__ import annotations
import datetime
import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class SniperSignal:
    ts: str
    pattern: str           # "dislocation" | "liquidation_zone" | "momentum" | "funding_cliff"
    symbol: str
    venue: str             # "HL" | "DRIFT" | "BOTH"
    entry_side: str        # "LONG" | "SHORT"
    trigger_value: float
    trigger_unit: str      # "bps" | "% funding" | "bps/hr"
    urgency: str           # "IMMEDIATE" | "WATCH" | "SETUP"
    estimated_edge_bps: float
    note: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def pretty(self) -> str:
        urgency_sym = {"IMMEDIATE": "🔴", "WATCH": "🟡", "SETUP": "🟢"}.get(self.urgency, "⚪")
        return (
            f"{urgency_sym} [{self.pattern.upper():<20}] {self.symbol:<10} {self.venue:<8} "
            f"{self.entry_side:<6} edge≈{self.estimated_edge_bps:.1f}bps  "
            f"trigger={self.trigger_value:.2f}{self.trigger_unit}  {self.note}"
        )


# ── Thresholds ───────────────────────────────────────────────────────────────
DISLOC_IMMEDIATE_BPS = 50.0
DISLOC_WATCH_BPS     = 25.0
DISLOC_SETUP_BPS     = 15.0

FUNDING_CLIFF_BPS    = 10.0   # per hour: extreme rate → reversion expected
FUNDING_WATCH_BPS    = 5.0

LIQUIDATION_OI_RATIO = 0.15   # OI/daily-vol ratio: high ratio = concentrated risk
MOMENTUM_THRESHOLD_PCT = 2.0   # cross-venue price diff as % → momentum signal


def _fnum(x) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def detect_dislocation_snipers(hl_markets: list[dict]) -> list[SniperSignal]:
    """
    Find HL markets where mark/oracle dislocation is large.
    These are classic sniper entry points: mark will mean-revert to oracle.
    Edge = dislocation (the spread the maker already captured is your cushion).
    """
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    signals = []
    for m in hl_markets:
        sym = m.get("coin", "").split(":")[-1].upper()
        # HL rows use: mark, oracle, disloc_bps, vol_24h, funding_bps_hr
        mark = _fnum(m.get("mark"))
        oracle = _fnum(m.get("oracle"))
        disloc = _fnum(m.get("disloc_bps"))
        if not mark or not oracle or oracle == 0:
            continue
        if disloc is None:
            disloc = abs(mark - oracle) / oracle * 1e4
        if disloc < DISLOC_SETUP_BPS:
            continue

        side = "SHORT" if mark > oracle else "LONG"  # trade toward oracle

        if disloc >= DISLOC_IMMEDIATE_BPS:
            urgency = "IMMEDIATE"
        elif disloc >= DISLOC_WATCH_BPS:
            urgency = "WATCH"
        else:
            urgency = "SETUP"

        # Estimated edge: a portion of dislocation (after spread/fees)
        edge_est = disloc * 0.4  # conservative: capture ~40% of dislocation

        vol = _fnum(m.get("vol_24h"))
        note = f"mark={mark:,.4f} oracle={oracle:,.4f}"
        if vol:
            note += f" vol24h=${vol/1e6:.1f}M"

        signals.append(SniperSignal(
            ts=ts, pattern="dislocation", symbol=sym, venue="HL",
            entry_side=side, trigger_value=disloc, trigger_unit="bps",
            urgency=urgency, estimated_edge_bps=edge_est, note=note,
        ))
    return signals


def detect_funding_cliff_snipers(
    hl_markets: list[dict],
    drift_snapshot: dict,
) -> list[SniperSignal]:
    """
    Extreme funding rates signal crowded positions about to unwind.
    Pattern: very high positive funding → crowded longs → sniper short entry.
    """
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    signals = []

    # Merge HL + Drift markets
    all_markets: list[tuple[str, str, Optional[float]]] = []
    for m in hl_markets:
        sym = m.get("coin", "").split(":")[-1].upper()
        # HL rows: funding_bps_hr is already in bps/hr
        fund_bps_hr = _fnum(m.get("funding_bps_hr"))
        if fund_bps_hr is not None:
            all_markets.append((sym, "HL", fund_bps_hr))

    for sym, m in drift_snapshot.items():
        fund_raw = _fnum(m.get("funding_rate"))
        if fund_raw is not None:
            f_bps = fund_raw * 1e4 if abs(fund_raw) < 0.01 else fund_raw
            all_markets.append((sym, "DRIFT", f_bps))

    for sym, venue, fund_bps in all_markets:
        if abs(fund_bps) < FUNDING_WATCH_BPS:
            continue
        side = "SHORT" if fund_bps > 0 else "LONG"
        urgency = "IMMEDIATE" if abs(fund_bps) > FUNDING_CLIFF_BPS else "WATCH"
        edge_est = abs(fund_bps) * 0.6  # funding reversion edge estimate
        note = f"funding={fund_bps:+.3f}bps/hr → {'longs crowded' if fund_bps > 0 else 'shorts crowded'}"
        signals.append(SniperSignal(
            ts=ts, pattern="funding_cliff", symbol=sym, venue=venue,
            entry_side=side, trigger_value=abs(fund_bps), trigger_unit="bps/hr",
            urgency=urgency, estimated_edge_bps=edge_est, note=note,
        ))
    return signals


def detect_cross_venue_momentum(
    hl_markets: list[dict],
    drift_snapshot: dict,
    pyth_prices: dict[str, float],
) -> list[SniperSignal]:
    """
    When HL and Drift prices diverge significantly, the lagging venue is the
    momentum play. The leading venue's price is the anchor.
    """
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    signals = []

    hl_by_sym = {m.get("coin", "").split(":")[-1].upper(): m for m in hl_markets}

    for sym, drift_m in drift_snapshot.items():
        hl_m = hl_by_sym.get(sym)
        if not hl_m:
            continue

        hl_mark = _fnum(hl_m.get("mark"))
        drift_mark = _fnum(drift_m.get("mark_price"))
        if not hl_mark or not drift_mark or hl_mark == 0:
            continue

        mid = (hl_mark + drift_mark) / 2
        gap_pct = abs(hl_mark - drift_mark) / mid * 100

        if gap_pct < MOMENTUM_THRESHOLD_PCT:
            continue

        # Pyth is the true oracle; the venue closest to Pyth is "leading"
        pyth_ref = pyth_prices.get(sym)
        if pyth_ref:
            hl_err = abs(hl_mark - pyth_ref)
            drift_err = abs(drift_mark - pyth_ref)
            leader = "HL" if hl_err < drift_err else "DRIFT"
            lagger = "DRIFT" if leader == "HL" else "HL"
            lagger_mark = drift_mark if lagger == "DRIFT" else hl_mark
            side = "LONG" if lagger_mark < pyth_ref else "SHORT"
        else:
            leader = "HL" if hl_mark > drift_mark else "DRIFT"
            lagger = "DRIFT" if leader == "HL" else "HL"
            side = "LONG" if hl_mark > drift_mark else "SHORT"

        urgency = "IMMEDIATE" if gap_pct > 5 else ("WATCH" if gap_pct > 3 else "SETUP")
        edge_est = gap_pct * 10 * 0.5  # convert pct to bps, take 50%
        note = f"leader={leader} HL={hl_mark:,.4f} Drift={drift_mark:,.4f}"
        if pyth_ref:
            note += f" Pyth={pyth_ref:,.4f}"

        signals.append(SniperSignal(
            ts=ts, pattern="momentum", symbol=sym, venue=lagger,
            entry_side=side, trigger_value=gap_pct * 100,  # in bps equivalent
            trigger_unit="bps",
            urgency=urgency, estimated_edge_bps=edge_est, note=note,
        ))
    return signals


def detect_liquidation_zones(hl_markets: list[dict]) -> list[SniperSignal]:
    """
    High OI relative to daily volume + extreme funding = concentrated risk.
    When forced unwinds begin, the price gap is the sniper's opportunity.
    """
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    signals = []
    for m in hl_markets:
        sym = m.get("coin", "").split(":")[-1].upper()
        vol = _fnum(m.get("vol_24h"))
        oi_notional = _fnum(m.get("oi_ntl"))
        fund_bps_hr = _fnum(m.get("funding_bps_hr"))
        mark = _fnum(m.get("mark"))

        if not vol or not oi_notional or fund_bps_hr is None or vol == 0:
            continue

        oi_ratio = oi_notional / vol
        fund_bps = fund_bps_hr

        # High OI/vol ratio + skewed funding → compressed spring
        if oi_ratio < LIQUIDATION_OI_RATIO:
            continue
        if abs(fund_bps) < 2.0:
            continue

        side = "SHORT" if fund_bps > 0 else "LONG"
        urgency = "WATCH" if oi_ratio > 0.3 else "SETUP"
        edge_est = abs(fund_bps) * oi_ratio * 10
        note = (
            f"OI/vol={oi_ratio:.2f} funding={fund_bps:+.3f}bps/hr "
            f"OI=${oi_notional/1e6:.1f}M vol=${vol/1e6:.1f}M"
        )
        signals.append(SniperSignal(
            ts=ts, pattern="liquidation_zone", symbol=sym, venue="HL",
            entry_side=side, trigger_value=oi_ratio,
            trigger_unit="OI/vol ratio",
            urgency=urgency, estimated_edge_bps=edge_est, note=note,
        ))
    return signals


def run_full_sniper_scan(
    hl_markets: list[dict],
    drift_snapshot: dict,
    pyth_prices: dict[str, float],
) -> list[SniperSignal]:
    """Run all four sniper detectors and return merged, de-duped, sorted results."""
    all_signals: list[SniperSignal] = []
    all_signals += detect_dislocation_snipers(hl_markets)
    all_signals += detect_funding_cliff_snipers(hl_markets, drift_snapshot)
    all_signals += detect_cross_venue_momentum(hl_markets, drift_snapshot, pyth_prices)
    all_signals += detect_liquidation_zones(hl_markets)

    # Sort: IMMEDIATE first, then WATCH, then SETUP; within each by edge
    urgency_rank = {"IMMEDIATE": 0, "WATCH": 1, "SETUP": 2}
    return sorted(all_signals, key=lambda s: (urgency_rank.get(s.urgency, 3), -s.estimated_edge_bps))


def print_sniper_report(signals: list[SniperSignal], top_n: int = 30) -> None:
    """Print sniper signals to stdout with color coding."""
    if not signals:
        print("  (no sniper signals detected)")
        return
    print(f"\n  {'#':<3} {'PATTERN':<22} {'SYM':<10} {'VENUE':<8} {'SIDE':<6} {'EDGE(bps)':>10} {'URG':<12} NOTE")
    for i, s in enumerate(signals[:top_n], 1):
        urg_col = {
            "IMMEDIATE": "\033[91m",
            "WATCH": "\033[93m",
            "SETUP": "\033[92m",
        }.get(s.urgency, "")
        reset = "\033[0m"
        print(
            f"  {i:<3} {s.pattern:<22} {s.symbol:<10} {s.venue:<8} {s.entry_side:<6} "
            f"{s.estimated_edge_bps:>10.2f} "
            f"{urg_col}{s.urgency:<12}{reset} {s.note}"
        )
