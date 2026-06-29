#!/usr/bin/env python3
"""
backtest_engine.py — Replay historical snapshots through arb/sniper detectors and measure P&L.

Works with both synthetic data (data_collector.py --synthetic) and real collected HL data.

Usage:
  python3 backtest_engine.py --synthetic           # use synthetic 90d dataset
  python3 backtest_engine.py --data data/synthetic_90d_1h.jsonl
  python3 backtest_engine.py --data data/synthetic_90d_1h.jsonl --strategy dislocation
  python3 backtest_engine.py --data data/synthetic_90d_1h.jsonl --strategy all --report

Strategies:
  dislocation   - Buy/short when mark/oracle gap > threshold; exit on reversion
  funding_arb   - Long venue with lower funding, short venue with higher funding
  mark_spread   - Trade price gap between HL and Drift; exit when gap closes
  all           - Run all three and compare
"""
from __future__ import annotations
import argparse
import datetime
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


# ── Trade & position types ────────────────────────────────────────────────────

@dataclass
class Trade:
    entry_ts: int
    exit_ts: int
    coin: str
    strategy: str
    side: str                   # "LONG" | "SHORT"
    venue: str                  # "HL" | "DRIFT" | "SPREAD"
    entry_price: float
    exit_price: float
    size_usd: float
    signal_bps: float           # the signal magnitude at entry
    entry_fee_bps: float
    exit_fee_bps: float
    pnl_usd: float = 0.0
    pnl_bps: float = 0.0
    duration_h: float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    strategy: str
    trades: list[Trade] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_usd for t in self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.pnl_usd > 0) / len(self.trades)

    @property
    def avg_pnl_bps(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t.pnl_bps for t in self.trades) / len(self.trades)

    @property
    def sharpe(self) -> float:
        if len(self.trades) < 2:
            return 0.0
        pnls = [t.pnl_bps for t in self.trades]
        mu = sum(pnls) / len(pnls)
        sigma = math.sqrt(sum((p - mu) ** 2 for p in pnls) / len(pnls))
        return mu / sigma if sigma > 0 else 0.0

    @property
    def max_drawdown_usd(self) -> float:
        if not self.trades:
            return 0.0
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in sorted(self.trades, key=lambda x: x.entry_ts):
            running += t.pnl_usd
            peak = max(peak, running)
            max_dd = min(max_dd, running - peak)
        return max_dd

    def summary(self) -> dict:
        by_coin: dict[str, list[float]] = defaultdict(list)
        for t in self.trades:
            by_coin[t.coin].append(t.pnl_bps)
        coin_edges = {c: sum(v) / len(v) for c, v in by_coin.items()}
        top_coins = sorted(coin_edges.items(), key=lambda x: -x[1])[:5]
        return {
            "strategy": self.strategy,
            "n_trades": len(self.trades),
            "total_pnl_usd": round(self.total_pnl, 2),
            "win_rate_pct": round(self.win_rate * 100, 1),
            "avg_pnl_bps": round(self.avg_pnl_bps, 3),
            "sharpe": round(self.sharpe, 3),
            "max_drawdown_usd": round(self.max_drawdown_usd, 2),
            "best_coins": top_coins,
        }


# ── Data loading ──────────────────────────────────────────────────────────────

def load_synthetic_data(path: str) -> dict[str, list[dict]]:
    """Load JSONL rows and index by coin → sorted list of time-series rows."""
    by_coin: dict[str, list[dict]] = defaultdict(list)
    with open(path) as f:
        for line in f:
            row = json.loads(line.strip())
            by_coin[row["coin"]].append(row)
    for coin in by_coin:
        by_coin[coin].sort(key=lambda r: r["ts"])
    return dict(by_coin)


# ── Strategy 1: Dislocation mean-reversion ───────────────────────────────────

def backtest_dislocation(
    coin_data: dict[str, list[dict]],
    entry_threshold_bps: float = 30.0,
    exit_threshold_bps: float = 5.0,
    max_hold_h: int = 24,
    size_usd: float = 10_000,
    maker_fee_bps: float = 0.3,   # HL Growth Mode tier 0
    taker_fee_bps: float = 2.5,   # exit as taker (realistic)
    position_limit: int = 5,      # max concurrent positions
) -> BacktestResult:
    """
    Dislocation mean-reversion strategy:
    Entry: when |mark - oracle| > entry_threshold_bps → trade toward oracle
    Exit: when |mark - oracle| < exit_threshold_bps OR max_hold_h exceeded
    Fee model: enter as maker (Growth Mode 0.3bps), exit as taker (~2.5bps)
    """
    result = BacktestResult(strategy="dislocation")
    open_positions: list[dict] = []

    for coin, rows in coin_data.items():
        open_positions = []
        for i, row in enumerate(rows):
            ts = row["ts"]
            hl_p = row["hl_price"]
            oracle_p = row["oracle_price"]
            disloc = row.get("disloc_bps", 0)

            # Check exits first
            closed = []
            for pos in open_positions:
                hold_h = (ts - pos["entry_ts"]) / 3_600_000
                exit_disloc = abs(hl_p - oracle_p) / oracle_p * 1e4 if oracle_p else 0

                exit_reason = ""
                if exit_disloc <= exit_threshold_bps:
                    exit_reason = "reversion"
                elif hold_h >= max_hold_h:
                    exit_reason = "timeout"

                if exit_reason:
                    if pos["side"] == "LONG":
                        gross_pnl_bps = (hl_p - pos["entry_price"]) / pos["entry_price"] * 1e4
                    else:
                        gross_pnl_bps = (pos["entry_price"] - hl_p) / pos["entry_price"] * 1e4
                    net_pnl_bps = gross_pnl_bps - pos["entry_fee_bps"] - taker_fee_bps
                    pnl_usd = net_pnl_bps / 1e4 * size_usd

                    result.trades.append(Trade(
                        entry_ts=pos["entry_ts"], exit_ts=ts,
                        coin=coin, strategy="dislocation",
                        side=pos["side"], venue="HL",
                        entry_price=pos["entry_price"], exit_price=hl_p,
                        size_usd=size_usd, signal_bps=pos["signal_bps"],
                        entry_fee_bps=pos["entry_fee_bps"], exit_fee_bps=taker_fee_bps,
                        pnl_usd=pnl_usd, pnl_bps=net_pnl_bps,
                        duration_h=round(hold_h, 2), exit_reason=exit_reason,
                    ))
                    closed.append(pos)

            for p in closed:
                open_positions.remove(p)

            # Entry signal
            if disloc >= entry_threshold_bps and len(open_positions) < position_limit:
                side = "LONG" if hl_p < oracle_p else "SHORT"
                # Don't double-enter same side
                if not any(p["side"] == side for p in open_positions):
                    open_positions.append({
                        "entry_ts": ts, "entry_price": hl_p,
                        "side": side, "signal_bps": disloc,
                        "entry_fee_bps": maker_fee_bps,
                    })

    return result


# ── Strategy 2: Funding-rate arb ─────────────────────────────────────────────

def backtest_funding_arb(
    coin_data: dict[str, list[dict]],
    entry_gap_bps: float = 5.0,    # min funding diff to enter
    min_hold_h: int = 4,
    max_hold_h: int = 48,
    size_usd: float = 10_000,
    fee_bps: float = 2.0,          # roundtrip (both sides)
) -> BacktestResult:
    """
    Funding-rate arb: simultaneously long the lower-funding venue and short the higher.
    P&L = accumulated funding differential - fees.
    We simulate HL funding vs a synthetic "Drift" rate = HL rate + noise.
    """
    result = BacktestResult(strategy="funding_arb")

    import random
    rng = random.Random(99)

    for coin, rows in coin_data.items():
        open_pos = None
        accumulated_fund_bps = 0.0

        for i, row in enumerate(rows):
            ts = row["ts"]
            hl_fund = row.get("funding_bps_hr", 0)
            # Synthetic Drift funding: HL + noise (±3 bps)
            drift_fund = hl_fund + rng.gauss(0, 3)
            fund_gap = hl_fund - drift_fund
            interval_h = row.get("interval_h", 1)

            # Check exit
            if open_pos:
                hold_h = (ts - open_pos["entry_ts"]) / 3_600_000
                accumulated_fund_bps += abs(open_pos["fund_gap"]) * interval_h

                exit_reason = ""
                if hold_h >= max_hold_h:
                    exit_reason = "timeout"
                elif hold_h >= min_hold_h and abs(fund_gap) < entry_gap_bps * 0.3:
                    exit_reason = "gap_closed"

                if exit_reason:
                    net_pnl_bps = accumulated_fund_bps - fee_bps
                    pnl_usd = net_pnl_bps / 1e4 * size_usd
                    result.trades.append(Trade(
                        entry_ts=open_pos["entry_ts"], exit_ts=ts,
                        coin=coin, strategy="funding_arb",
                        side="LONG_LOW_FUND", venue="SPREAD",
                        entry_price=row["hl_price"], exit_price=row["hl_price"],
                        size_usd=size_usd, signal_bps=open_pos["fund_gap"],
                        entry_fee_bps=fee_bps / 2, exit_fee_bps=fee_bps / 2,
                        pnl_usd=pnl_usd, pnl_bps=net_pnl_bps,
                        duration_h=round(hold_h, 2), exit_reason=exit_reason,
                    ))
                    open_pos = None
                    accumulated_fund_bps = 0.0

            # Entry
            if open_pos is None and abs(fund_gap) >= entry_gap_bps:
                open_pos = {"entry_ts": ts, "fund_gap": fund_gap}
                accumulated_fund_bps = 0.0

    return result


# ── Strategy 3: Cross-venue mark-spread arb ───────────────────────────────────

def backtest_mark_spread(
    coin_data: dict[str, list[dict]],
    entry_gap_bps: float = 20.0,
    exit_gap_bps: float = 3.0,
    max_hold_h: int = 12,
    size_usd: float = 10_000,
    fee_bps_per_side: float = 2.0,  # both venues combined roundtrip
) -> BacktestResult:
    """
    Cross-venue spread arb: Buy cheaper venue, sell expensive.
    P&L = gap at entry - gap at exit - fees.
    """
    result = BacktestResult(strategy="mark_spread")

    for coin, rows in coin_data.items():
        open_pos = None

        for row in rows:
            ts = row["ts"]
            hl_p = row["hl_price"]
            drift_p = row.get("drift_price", hl_p)
            gap_bps = row.get("cross_gap_bps", 0)
            interval_h = row.get("interval_h", 1)

            if open_pos:
                hold_h = (ts - open_pos["entry_ts"]) / 3_600_000
                current_gap = abs(hl_p - drift_p) / ((hl_p + drift_p) / 2) * 1e4

                exit_reason = ""
                if current_gap <= exit_gap_bps:
                    exit_reason = "convergence"
                elif hold_h >= max_hold_h:
                    exit_reason = "timeout"

                if exit_reason:
                    spread_captured_bps = open_pos["entry_gap"] - current_gap
                    net_pnl_bps = spread_captured_bps - fee_bps_per_side * 2
                    pnl_usd = net_pnl_bps / 1e4 * size_usd
                    result.trades.append(Trade(
                        entry_ts=open_pos["entry_ts"], exit_ts=ts,
                        coin=coin, strategy="mark_spread",
                        side="BUY_CHEAP_SELL_DEAR", venue="SPREAD",
                        entry_price=(hl_p + drift_p) / 2, exit_price=(hl_p + drift_p) / 2,
                        size_usd=size_usd, signal_bps=open_pos["entry_gap"],
                        entry_fee_bps=fee_bps_per_side, exit_fee_bps=fee_bps_per_side,
                        pnl_usd=pnl_usd, pnl_bps=net_pnl_bps,
                        duration_h=round(hold_h, 2), exit_reason=exit_reason,
                    ))
                    open_pos = None

            if open_pos is None and gap_bps >= entry_gap_bps:
                open_pos = {"entry_ts": ts, "entry_gap": gap_bps}

    return result


# ── Parameter sweep (find best params) ───────────────────────────────────────

def param_sweep_dislocation(coin_data: dict[str, list[dict]]) -> list[dict]:
    """Grid search over dislocation strategy parameters."""
    results = []
    for entry_thr in [15, 25, 40, 60]:
        for exit_thr in [3, 8, 15]:
            for max_hold in [8, 24, 48]:
                if exit_thr >= entry_thr:
                    continue
                r = backtest_dislocation(
                    coin_data,
                    entry_threshold_bps=entry_thr,
                    exit_threshold_bps=exit_thr,
                    max_hold_h=max_hold,
                )
                s = r.summary()
                s.update({"entry_thr": entry_thr, "exit_thr": exit_thr, "max_hold": max_hold})
                results.append(s)
    return sorted(results, key=lambda x: -x["sharpe"])


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_result(r: BacktestResult) -> None:
    s = r.summary()
    BOLD = "\033[1m"; RESET = "\033[0m"; GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"
    pnl_col = GREEN if s["total_pnl_usd"] >= 0 else RED
    print(f"\n{BOLD}── {s['strategy'].upper()} ──{RESET}")
    print(f"  Trades:      {s['n_trades']}")
    print(f"  Total P&L:   {pnl_col}${s['total_pnl_usd']:+,.2f}{RESET}")
    print(f"  Win rate:    {s['win_rate_pct']:.1f}%")
    print(f"  Avg edge:    {s['avg_pnl_bps']:+.3f} bps/trade")
    print(f"  Sharpe:      {s['sharpe']:.3f}")
    print(f"  Max DD:      ${s['max_drawdown_usd']:,.2f}")
    if s["best_coins"]:
        print(f"  Best coins:  {', '.join(f'{c}={v:.2f}bps' for c, v in s['best_coins'])}")

    # Equity curve snapshot
    running = 0.0
    for t in sorted(r.trades, key=lambda x: x.entry_ts)[:5]:
        running += t.pnl_usd
        dt = datetime.datetime.utcfromtimestamp(t.entry_ts / 1000).strftime("%m-%d %H:%M")
        col = GREEN if t.pnl_usd >= 0 else RED
        print(f"    [{dt}] {t.coin:<8} {t.side:<24} {t.duration_h:>5.1f}h "
              f"signal={t.signal_bps:.1f}bps  {col}{t.pnl_bps:+.2f}bps  ${t.pnl_usd:+.2f}{RESET}  [{t.exit_reason}]")
    if len(r.trades) > 5:
        print(f"    ... ({len(r.trades) - 5} more trades)")


def save_results(results: list[BacktestResult], path: str) -> None:
    with open(path, "w") as f:
        json.dump(
            [{"summary": r.summary(), "trades": [t.__dict__ for t in r.trades[:100]]} for r in results],
            f, indent=2,
        )
    print(f"\n  Results saved → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="HyperCore arb/sniper backtest engine")
    ap.add_argument("--data",      help="Path to JSONL data file")
    ap.add_argument("--synthetic", action="store_true", help="Auto-generate and use synthetic data")
    ap.add_argument("--days",      type=int, default=90, help="Days of synthetic data (default 90)")
    ap.add_argument("--strategy",  default="all",
                    choices=["dislocation", "funding_arb", "mark_spread", "all"],
                    help="Strategy to backtest")
    ap.add_argument("--sweep",     action="store_true", help="Parameter sweep for dislocation strategy")
    ap.add_argument("--report",    action="store_true", help="Save JSON report")
    ap.add_argument("--size",      type=float, default=10_000, help="Trade size in USD (default $10k)")
    ap.add_argument("--fee",       type=float, default=0.3, help="Maker fee in bps (default 0.3 = Growth Mode)")
    args = ap.parse_args()

    # Resolve data path
    if args.synthetic or not args.data:
        from data_collector import generate_synthetic_dataset
        data_path = os.path.join(DATA_DIR, f"synthetic_{args.days}d_1h.jsonl")
        if not os.path.exists(data_path):
            data_path = generate_synthetic_dataset(days=args.days)
        else:
            print(f"  Using existing synthetic data: {data_path}")
    else:
        data_path = args.data

    print(f"\n=== HyperCore Arb Backtest Engine ===")
    print(f"  Data:     {data_path}")
    print(f"  Strategy: {args.strategy}")
    print(f"  Size:     ${args.size:,.0f}")
    print(f"  Fee:      {args.fee:.2f} bps (maker)\n")

    coin_data = load_synthetic_data(data_path)
    print(f"  Loaded: {sum(len(v) for v in coin_data.values())} rows across {len(coin_data)} coins")

    results = []

    if args.sweep:
        print("\n=== PARAMETER SWEEP: Dislocation Strategy ===")
        sweep = param_sweep_dislocation(coin_data)
        print(f"\n{'Entry':>8}{'Exit':>8}{'MaxHold':>9}{'Trades':>8}{'P&L($)':>12}{'WinRate%':>10}{'AvgBps':>10}{'Sharpe':>8}")
        for row in sweep[:15]:
            print(f"{row['entry_thr']:>8}{row['exit_thr']:>8}{row['max_hold']:>9}"
                  f"{row['n_trades']:>8}{row['total_pnl_usd']:>12.2f}"
                  f"{row['win_rate_pct']:>10.1f}{row['avg_pnl_bps']:>10.3f}{row['sharpe']:>8.3f}")
        return

    if args.strategy in ("dislocation", "all"):
        r = backtest_dislocation(coin_data, size_usd=args.size, maker_fee_bps=args.fee)
        print_result(r)
        results.append(r)

    if args.strategy in ("funding_arb", "all"):
        r = backtest_funding_arb(coin_data, size_usd=args.size)
        print_result(r)
        results.append(r)

    if args.strategy in ("mark_spread", "all"):
        r = backtest_mark_spread(coin_data, size_usd=args.size)
        print_result(r)
        results.append(r)

    if args.report and results:
        out = os.path.join(DATA_DIR, "backtest_results.json")
        save_results(results, out)


if __name__ == "__main__":
    main()
