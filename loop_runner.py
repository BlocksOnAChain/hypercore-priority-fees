#!/usr/bin/env python3
"""
loop_runner.py — Background scanning loop for Solana + Hyperliquid arb & sniper signals.

Usage:
  python3 loop_runner.py                    # default: scan every 60s, log to arb_log.jsonl
  python3 loop_runner.py --interval 30      # scan every 30 seconds
  python3 loop_runner.py --once             # single scan then exit
  python3 loop_runner.py --quiet            # suppress terminal output (log only)
  python3 loop_runner.py --report           # print full report then exit
  python3 loop_runner.py --no-drift         # skip Drift API (HL + Pyth only)

All signals are logged to arb_log.jsonl as newline-delimited JSON.
"""
from __future__ import annotations
import argparse
import datetime
import json
import os
import sys
import time
import traceback

from hl_data import fetch_all_markets
from solana_data import get_drift_snapshot, fetch_pyth_prices
COMMON_SYMBOLS = {"BTC", "ETH", "SOL", "AVAX", "BNB", "DOGE", "LINK", "XRP", "ARB", "OP", "SUI", "APT", "INJ", "WIF"}
from arb_scanner import scan_arb_opportunities, print_signals
from sniper_signals import run_full_sniper_scan, print_sniper_report
from mock_data import make_hl_markets, make_drift_snapshot, make_pyth_prices

LOG_FILE = os.path.join(os.path.dirname(__file__), "arb_log.jsonl")
REPORT_FILE = os.path.join(os.path.dirname(__file__), "arb_report.json")

BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"


def banner() -> None:
    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║   HyperCore + Solana Arb & Sniper Loop  |  loop_runner.py   ║{RESET}")
    print(f"{BOLD}{CYAN}║   Venues: Hyperliquid (HL)  ×  Drift Protocol (Solana)      ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════════════════════╝{RESET}\n")


def fetch_all_data_mock() -> tuple[list[dict], dict, dict[str, float]]:
    """Return fixture data for offline/test use."""
    hl = make_hl_markets()
    drift = make_drift_snapshot()
    pyth = make_pyth_prices()
    print(f"  [MOCK] HL: {len(hl)} markets  Drift: {len(drift)} markets  Pyth: {len(pyth)} prices")
    return hl, drift, pyth


def fetch_all_data(skip_drift: bool = False) -> tuple[list[dict], dict, dict[str, float]]:
    """Fetch HL markets, Drift snapshot, and Pyth prices in parallel (sequential for stdlib)."""
    t0 = time.time()
    hl_markets: list[dict] = []
    drift_snapshot: dict = {}
    pyth_prices: dict[str, float] = {}

    try:
        hl_markets = fetch_all_markets()
        print(f"  HL: {len(hl_markets)} markets in {time.time()-t0:.1f}s")
    except Exception as e:
        print(f"  {RED}[WARN] HL fetch failed: {e}{RESET}")

    if not skip_drift:
        t1 = time.time()
        try:
            drift_snapshot = get_drift_snapshot()
            print(f"  Drift: {len(drift_snapshot)} markets in {time.time()-t1:.1f}s")
        except Exception as e:
            print(f"  {YELLOW}[WARN] Drift fetch failed: {e} — signals will be HL-only{RESET}")
        try:
            syms = list({m.get("coin", "").split(":")[-1].upper() for m in hl_markets}
                        & set(COMMON_SYMBOLS))
            pyth_prices = fetch_pyth_prices(syms[:20])  # cap to avoid rate limiting
            print(f"  Pyth: {len(pyth_prices)} prices")
        except Exception as e:
            print(f"  {YELLOW}[WARN] Pyth fetch failed: {e}{RESET}")

    return hl_markets, drift_snapshot, pyth_prices


def run_scan(
    hl_markets: list[dict],
    drift_snapshot: dict,
    pyth_prices: dict[str, float],
    quiet: bool = False,
) -> dict:
    """Run arb + sniper scan and return a summary dict."""
    scan_ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    arb_signals = scan_arb_opportunities(hl_markets, drift_snapshot, pyth_prices)
    sniper_sigs = run_full_sniper_scan(hl_markets, drift_snapshot, pyth_prices)

    if not quiet:
        print(f"\n{BOLD}── ARB SIGNALS ─────────────────────────────────────────────────{RESET}")
        print_signals(arb_signals, top_n=15)
        print(f"\n{BOLD}── SNIPER SIGNALS ──────────────────────────────────────────────{RESET}")
        print_sniper_report(sniper_sigs, top_n=20)

    high_arb   = [s for s in arb_signals if s.confidence == "HIGH"]
    imm_sniper = [s for s in sniper_sigs if s.urgency == "IMMEDIATE"]

    summary = {
        "ts": scan_ts,
        "hl_market_count": len(hl_markets),
        "drift_market_count": len(drift_snapshot),
        "arb_signals_total": len(arb_signals),
        "arb_signals_high": len(high_arb),
        "sniper_signals_total": len(sniper_sigs),
        "sniper_signals_immediate": len(imm_sniper),
        "top_arb": [s.to_dict() for s in arb_signals[:5]],
        "top_sniper": [s.to_dict() for s in sniper_sigs[:5]],
    }
    return summary


def log_summary(summary: dict) -> None:
    """Append summary to JSONL log file."""
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(summary) + "\n")


def write_report(summary: dict) -> None:
    """Write latest state to JSON report file (overwrite)."""
    with open(REPORT_FILE, "w") as f:
        json.dump(summary, f, indent=2)


def print_stats_line(summary: dict) -> None:
    """Print a compact one-liner status."""
    arb_h = summary["arb_signals_high"]
    arb_t = summary["arb_signals_total"]
    snp_i = summary["sniper_signals_immediate"]
    snp_t = summary["sniper_signals_total"]
    ts    = summary["ts"]
    arb_col  = RED if arb_h > 0 else (YELLOW if arb_t > 0 else GREEN)
    snp_col  = RED if snp_i > 0 else (YELLOW if snp_t > 0 else GREEN)
    print(
        f"\n{BOLD}[{ts}]{RESET} "
        f"Arb: {arb_col}{arb_h} HIGH / {arb_t} total{RESET}  |  "
        f"Sniper: {snp_col}{snp_i} IMMEDIATE / {snp_t} total{RESET}  |  "
        f"HL: {summary['hl_market_count']} mkts  Drift: {summary['drift_market_count']} mkts"
    )


def loop(interval: int, quiet: bool, skip_drift: bool, mock: bool = False) -> None:
    """Main scan loop — runs indefinitely until Ctrl-C."""
    banner()
    if mock:
        print(f"  {YELLOW}[MOCK MODE] Using fixture data (no live API calls){RESET}\n")
    scan_num = 0
    try:
        while True:
            scan_num += 1
            ts = datetime.datetime.utcnow().isoformat(timespec="seconds")
            print(f"\n{BOLD}{'━'*64}{RESET}")
            print(f"  Scan #{scan_num}  |  {ts}Z  |  interval={interval}s{'  [MOCK]' if mock else ''}")
            print(f"{'━'*64}")

            try:
                if mock:
                    hl_markets, drift_snapshot, pyth_prices = fetch_all_data_mock()
                else:
                    hl_markets, drift_snapshot, pyth_prices = fetch_all_data(skip_drift=skip_drift)
                summary = run_scan(hl_markets, drift_snapshot, pyth_prices, quiet=quiet)
                log_summary(summary)
                write_report(summary)
                print_stats_line(summary)
            except Exception as e:
                print(f"\n  {RED}[ERROR] scan failed: {e}{RESET}")
                traceback.print_exc()

            print(f"\n  Next scan in {interval}s... (Ctrl-C to stop)")
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n{YELLOW}  Loop stopped by user.{RESET}")
        print(f"  Log written to: {LOG_FILE}")
        print(f"  Report at:      {REPORT_FILE}")


def single_scan(quiet: bool, skip_drift: bool, mock: bool = False) -> dict:
    """Run one scan and return the summary."""
    banner()
    if mock:
        print(f"  {YELLOW}[MOCK MODE] Using fixture data (no live API calls){RESET}")
    print(f"  [{datetime.datetime.utcnow().isoformat(timespec='seconds')}Z] Running single scan...")
    if mock:
        hl_markets, drift_snapshot, pyth_prices = fetch_all_data_mock()
    else:
        hl_markets, drift_snapshot, pyth_prices = fetch_all_data(skip_drift=skip_drift)
    summary = run_scan(hl_markets, drift_snapshot, pyth_prices, quiet=quiet)
    log_summary(summary)
    write_report(summary)
    print_stats_line(summary)
    print(f"\n  Log: {LOG_FILE}")
    print(f"  Report: {REPORT_FILE}")
    return summary


def print_report_cmd() -> None:
    """Print the latest saved report."""
    if not os.path.exists(REPORT_FILE):
        print("[!] No report file found. Run a scan first.")
        return
    with open(REPORT_FILE) as f:
        data = json.load(f)
    print(json.dumps(data, indent=2))


def tail_log(n: int = 20) -> None:
    """Print the last N entries from the log."""
    if not os.path.exists(LOG_FILE):
        print("[!] No log file found. Run a scan first.")
        return
    with open(LOG_FILE) as f:
        lines = f.readlines()
    print(f"=== Last {min(n, len(lines))} log entries from {LOG_FILE} ===")
    for line in lines[-n:]:
        try:
            entry = json.loads(line)
            ts   = entry.get("ts", "?")
            arb  = entry.get("arb_signals_high", 0)
            snp  = entry.get("sniper_signals_immediate", 0)
            print(f"  [{ts}] arb_HIGH={arb} sniper_IMMEDIATE={snp}")
        except Exception:
            print(f"  {line.strip()}")


def main() -> None:
    ap = argparse.ArgumentParser(description="HyperCore + Solana Arb & Sniper Loop")
    ap.add_argument("--interval", type=int, default=60, help="Scan interval in seconds (default: 60)")
    ap.add_argument("--once",     action="store_true",  help="Run one scan then exit")
    ap.add_argument("--quiet",    action="store_true",  help="Suppress verbose output")
    ap.add_argument("--report",   action="store_true",  help="Print latest report and exit")
    ap.add_argument("--tail",     type=int, default=0,  help="Tail last N log entries")
    ap.add_argument("--no-drift", action="store_true",  help="Skip Drift API (HL + Pyth only)")
    ap.add_argument("--mock",     action="store_true",  help="Use fixture data (no live API calls)")
    args = ap.parse_args()

    if args.report:
        print_report_cmd()
        return

    if args.tail > 0:
        tail_log(args.tail)
        return

    if args.once:
        single_scan(quiet=args.quiet, skip_drift=args.no_drift, mock=args.mock)
        return

    loop(interval=args.interval, quiet=args.quiet, skip_drift=args.no_drift, mock=args.mock)


if __name__ == "__main__":
    main()
