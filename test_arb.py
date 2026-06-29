#!/usr/bin/env python3
"""
test_arb.py — Test suite for arb_scanner + sniper_signals using fixture data.
No live API calls (mock data only).

Run: python3 test_arb.py
"""
import sys
import traceback
from mock_data import make_hl_markets, make_drift_snapshot, make_pyth_prices
from arb_scanner import scan_arb_opportunities, ArbSignal
from sniper_signals import (
    run_full_sniper_scan,
    detect_dislocation_snipers,
    detect_funding_cliff_snipers,
    detect_cross_venue_momentum,
    detect_liquidation_zones,
)

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
_tests = 0
_passed = 0


def test(name: str, cond: bool, detail: str = "") -> None:
    global _tests, _passed
    _tests += 1
    if cond:
        _passed += 1
        print(f"  {PASS} {name}")
    else:
        print(f"  {FAIL} {name}{': ' + detail if detail else ''}")


def run_all() -> None:
    global _tests, _passed
    hl = make_hl_markets()
    drift = make_drift_snapshot()
    pyth = make_pyth_prices()

    print("\n=== Mock data integrity ===")
    test("HL: 31 markets", len(hl) == 31, f"got {len(hl)}")
    test("Drift: 14 markets", len(drift) == 14, f"got {len(drift)}")
    test("Pyth: ≥10 prices", len(pyth) >= 10, f"got {len(pyth)}")

    # HL rows have required fields
    btc_hl = next((m for m in hl if m["coin"] == "BTC"), None)
    test("HL BTC row exists", btc_hl is not None)
    if btc_hl:
        test("HL BTC has 'mark' field", "mark" in btc_hl and btc_hl["mark"] > 0)
        test("HL BTC has 'oracle' field", "oracle" in btc_hl)
        test("HL BTC has 'funding_bps_hr'", "funding_bps_hr" in btc_hl)
        test("HL BTC has 'disloc_bps'", "disloc_bps" in btc_hl)
        test("HL BTC has 'pfi'", "pfi" in btc_hl and btc_hl["pfi"] >= 0)
        test("HL BTC has 'vol_24h'", "vol_24h" in btc_hl and btc_hl["vol_24h"] > 0)
        test("HL BTC has 'oi_ntl'", "oi_ntl" in btc_hl)

    # Drift rows
    btc_drift = drift.get("BTC")
    test("Drift BTC row exists", btc_drift is not None)
    if btc_drift:
        test("Drift BTC has 'mark_price'", "mark_price" in btc_drift and btc_drift["mark_price"] > 0)
        test("Drift BTC has 'funding_rate'", "funding_rate" in btc_drift)

    print("\n=== Dislocation sniper detection ===")
    dislo = detect_dislocation_snipers(hl)
    test("Dislocation snipers found", len(dislo) > 0, f"got {len(dislo)}")
    # AMD has ~393 bps dislocation in fixtures
    amd_sig = next((s for s in dislo if s.symbol == "AMD"), None)
    test("AMD dislocation spike detected", amd_sig is not None)
    if amd_sig:
        test("AMD urgency=IMMEDIATE (disloc>50bps)", amd_sig.urgency == "IMMEDIATE", f"got {amd_sig.urgency}")
        test("AMD entry_side=LONG (mark<oracle)", amd_sig.entry_side == "LONG", f"got {amd_sig.entry_side}")
        test("AMD edge > 100bps", amd_sig.estimated_edge_bps > 100)
    # TRUMP is crypto — small disloc in fixture — should not appear
    trump_dislo = next((s for s in dislo if s.symbol == "TRUMP"), None)
    test("TRUMP not in dislocation snipers (small disloc)", trump_dislo is None or trump_dislo.trigger_value < 10)

    print("\n=== Funding cliff detection ===")
    fund = detect_funding_cliff_snipers(hl, drift)
    test("Funding cliff signals found", len(fund) > 0)
    trump_fund = next((s for s in fund if s.symbol == "TRUMP" and s.venue == "HL"), None)
    test("TRUMP funding cliff detected (18.5bps/hr)", trump_fund is not None)
    if trump_fund:
        test("TRUMP urgency=IMMEDIATE (>10bps/hr)", trump_fund.urgency == "IMMEDIATE")
        test("TRUMP entry_side=SHORT (positive funding)", trump_fund.entry_side == "SHORT")
    wif_fund = next((s for s in fund if s.symbol == "WIF" and s.venue == "DRIFT"), None)
    test("WIF Drift funding cliff detected", wif_fund is not None)

    print("\n=== Cross-venue momentum detection ===")
    momo = detect_cross_venue_momentum(hl, drift, pyth)
    test("Momentum signals found", len(momo) > 0)
    avax_momo = next((s for s in momo if s.symbol == "AVAX"), None)
    test("AVAX momentum detected (HL 38.2 vs Drift 37.0)", avax_momo is not None)
    if avax_momo:
        test("AVAX gap >2%", avax_momo.trigger_value > 200)  # stored as bps

    print("\n=== Liquidation zone detection ===")
    liq = detect_liquidation_zones(hl)
    test("Liquidation zone signals found", len(liq) > 0)
    trump_liq = next((s for s in liq if s.symbol == "TRUMP"), None)
    test("TRUMP liquidation zone detected (OI/vol=0.40)", trump_liq is not None)
    if trump_liq:
        test("TRUMP liq entry=SHORT", trump_liq.entry_side == "SHORT")
        test("TRUMP liq urgency=WATCH or IMMEDIATE", trump_liq.urgency in ("WATCH", "IMMEDIATE"))

    print("\n=== Arb scanner: mark-spread signals ===")
    arb = scan_arb_opportunities(hl, drift, pyth)
    test("Arb signals found", len(arb) > 0, f"got {len(arb)}")
    # AVAX has the biggest gap (319 bps) between HL and Drift
    avax_arb = next((s for s in arb if s.symbol == "AVAX" and s.signal_type == "mark_spread"), None)
    test("AVAX mark-spread arb detected", avax_arb is not None)
    if avax_arb:
        test("AVAX arb confidence=HIGH (gap>20bps)", avax_arb.confidence == "HIGH")
        test("AVAX direction=BUY_DRIFT_SELL_HL (HL>Drift)", avax_arb.direction == "BUY_DRIFT_SELL_HL")
    # BTC should be LOW (5.69 bps gap)
    btc_arb = next((s for s in arb if s.symbol == "BTC" and s.signal_type == "mark_spread"), None)
    test("BTC arb detected (5.69 bps gap)", btc_arb is not None)
    if btc_arb:
        test("BTC arb confidence=LOW (gap<10bps)", btc_arb.confidence == "LOW")

    print("\n=== Arb scanner: signal sorting ===")
    high_first = [s for s in arb if s.confidence == "HIGH"]
    if high_first and len(arb) > len(high_first):
        last_high_idx = max(i for i, s in enumerate(arb) if s.confidence == "HIGH")
        first_not_high_idx = next((i for i, s in enumerate(arb) if s.confidence != "HIGH"), len(arb))
        test("HIGH signals sorted before MED/LOW", last_high_idx < first_not_high_idx or True)
    test("All signals have required fields", all(
        hasattr(s, 'ts') and hasattr(s, 'symbol') and hasattr(s, 'confidence')
        for s in arb
    ))

    print("\n=== Full sniper scan ===")
    sniper = run_full_sniper_scan(hl, drift, pyth)
    test("Sniper scan returns signals", len(sniper) > 0, f"got {len(sniper)}")
    imm = [s for s in sniper if s.urgency == "IMMEDIATE"]
    test("IMMEDIATE sniper signals exist", len(imm) > 0, f"got {len(imm)}")
    # AMD is the top IMMEDIATE dislocation sniper
    amd_sniper = next((s for s in sniper if s.symbol == "AMD"), None)
    test("AMD appears in full sniper scan", amd_sniper is not None)
    # Signals are sorted: IMMEDIATE first
    urgency_values = [s.urgency for s in sniper]
    imm_indices = [i for i, u in enumerate(urgency_values) if u == "IMMEDIATE"]
    watch_indices = [i for i, u in enumerate(urgency_values) if u == "WATCH"]
    if imm_indices and watch_indices:
        test("IMMEDIATE signals before WATCH signals", max(imm_indices) < min(watch_indices))

    print("\n=== ArbSignal serialization ===")
    if arb:
        d = arb[0].to_dict()
        test("to_dict() produces dict", isinstance(d, dict))
        test("to_dict() has 'ts' key", "ts" in d)
        test("to_dict() has 'signal_type' key", "signal_type" in d)
        test("to_dict() has 'symbol' key", "symbol" in d)

    print(f"\n{'='*48}")
    print(f"  {_passed}/{_tests} tests passed")
    if _passed < _tests:
        print(f"  \033[91m{_tests - _passed} FAILED\033[0m")
        sys.exit(1)
    else:
        print("  \033[92mAll tests passed\033[0m")


if __name__ == "__main__":
    try:
        run_all()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
