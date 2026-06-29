#!/usr/bin/env python3
"""Tests for hl_data.py. Pure-logic tests run offline with fixtures; the live
smoke test hits the public API and is skipped with --no-live.

    python3 test_hl.py            # all tests incl. live smoke
    python3 test_hl.py --no-live  # offline only
"""
import sys
import time
import hl_data as H

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


# Fixed reference instants (UTC) for deterministic clock tests.
# 2026-06-29 is a Monday.
MON_1800 = time.struct_time((2026, 6, 29, 18, 0, 0, 0, 180, 0))   # Mon 18:00 UTC (summer/EDT)
MON_0300 = time.struct_time((2026, 6, 29, 3, 0, 0, 0, 180, 0))    # Mon 03:00 UTC
SAT_1200 = time.struct_time((2026, 6, 27, 12, 0, 0, 5, 178, 0))   # Sat 12:00 UTC
# Winter (EST, UTC-5) instants that the old hardcoded-UTC windows got WRONG:
JAN_2030 = time.struct_time((2026, 1, 5, 20, 30, 0, 0, 5, 0))     # Mon 20:30 UTC = 15:30 EST
JAN_1345 = time.struct_time((2026, 1, 5, 13, 45, 0, 0, 5, 0))     # Mon 13:45 UTC = 08:45 EST


def test_classify():
    print("classify_reference:")
    check("BTC core -> crypto", H.classify_reference("BTC", "core") == "crypto")
    check("SKHX -> kr_equity", H.classify_reference("xyz:SKHX", "xyz") == "kr_equity")
    check("KIOXIA -> jp_equity", H.classify_reference("xyz:KIOXIA", "xyz") == "jp_equity")
    check("NVDA -> us_equity", H.classify_reference("xyz:NVDA", "xyz") == "us_equity")
    check("GOLD -> commodity", H.classify_reference("xyz:GOLD", "xyz") == "commodity")
    check("EUR -> fx", H.classify_reference("xyz:EUR", "xyz") == "fx")
    check("SP500 -> us_index", H.classify_reference("xyz:SP500", "xyz") == "us_index")
    check("ZHIPU -> cn_equity", H.classify_reference("xyz:ZHIPU", "xyz") == "cn_equity")
    check("unknown -> other", H.classify_reference("xyz:WAT", "xyz") == "other")


def test_clock():
    print("reference_open / clock_factor:")
    check("crypto always open", H.reference_open("crypto", MON_1800) is True)
    check("US equity open Mon 18:00", H.reference_open("us_equity", MON_1800) is True)
    check("US equity closed Mon 03:00", H.reference_open("us_equity", MON_0300) is False)
    check("KR equity open Mon 03:00", H.reference_open("kr_equity", MON_0300) is True)
    check("KR equity closed Mon 18:00", H.reference_open("kr_equity", MON_1800) is False)
    check("US equity closed Saturday", H.reference_open("us_equity", SAT_1200) is False)
    check("unknown bucket -> None", H.reference_open("other", MON_1800) is None)
    check("closed eq -> factor 1.6",
          H.clock_factor("kr_equity", MON_1800) == 1.6)
    check("open eq -> factor 1.0",
          H.clock_factor("kr_equity", MON_0300) == 1.0)
    check("crypto -> factor 1.0", H.clock_factor("crypto", MON_1800) == 1.0)


def test_clock_dst():
    print("DST correctness (winter EST):")
    # 15:30 EST -> NYSE open; old summer-UTC window (13:30-20:00) wrongly said closed
    check("US equity OPEN 15:30 EST (Jan)", H.reference_open("us_equity", JAN_2030) is True)
    # 08:45 EST -> NYSE closed; old window wrongly said open
    check("US equity CLOSED 08:45 EST (Jan)", H.reference_open("us_equity", JAN_1345) is False)
    # same instants, summer offset sanity (10:00 vs 16:30 EDT)
    check("US equity OPEN 10:00 EDT (Jun)",
          H.reference_open("us_equity",
                           time.struct_time((2026, 6, 29, 14, 0, 0, 0, 180, 0))) is True)


def test_pfi():
    print("compute_pfi:")
    check("None disloc -> 0", H.compute_pfi(None, 1e8, "crypto", MON_1800) == 0.0)
    check("None volume -> no raise, 0", H.compute_pfi(20, None, "crypto", MON_1800) == 0.0)
    # monotonic in dislocation
    p_lo = H.compute_pfi(5, 1e7, "us_equity", MON_1800)
    p_hi = H.compute_pfi(30, 1e7, "us_equity", MON_1800)
    check("increasing in dislocation", p_hi > p_lo)
    # monotonic in volume
    p_v1 = H.compute_pfi(20, 1e6, "crypto", MON_1800)
    p_v2 = H.compute_pfi(20, 1e8, "crypto", MON_1800)
    check("increasing in volume", p_v2 > p_v1)
    # clock factor lifts closed markets
    p_open = H.compute_pfi(20, 1e7, "kr_equity", MON_0300)   # KR open
    p_closed = H.compute_pfi(20, 1e7, "kr_equity", MON_1800)  # KR closed
    check("closed market scores higher", p_closed > p_open)
    check("closed == 1.6x open", abs(p_closed - 1.6 * p_open) < 1e-9)


def test_realized_vol():
    print("realized_vol_bps:")
    check("too few candles -> None", H.realized_vol_bps([{"c": "1"}, {"c": "1"}]) is None)
    flat = [{"c": "100"} for _ in range(10)]
    check("flat series -> ~0 vol", H.realized_vol_bps(flat) == 0.0)
    moving = [{"c": str(100 * (1.001 ** i))} for i in range(10)]  # const log-return
    rv = H.realized_vol_bps(moving)
    check("constant-growth series -> ~0 stdev", rv is not None and rv < 1.0)
    noisy = [{"c": "100"}, {"c": "101"}, {"c": "100"}, {"c": "101"}, {"c": "100"}]
    check("oscillating series -> positive vol", H.realized_vol_bps(noisy) > 0)
    check("non-list input -> None", H.realized_vol_bps(None) is None)
    check("malformed candle (missing c) -> no raise",
          H.realized_vol_bps([{"c": "100"}, {}, {"x": 1}, {"c": "101"}]) is None)


def test_build_row():
    print("build_market_row (fixture):")
    u = {"name": "xyz:SKHX"}
    c = {"markPx": "100.33", "oraclePx": "100.0", "midPx": "100.3",
         "prevDayPx": "105.0", "dayNtlVlm": "351000000", "openInterest": "2000000",
         "funding": "0.0001"}
    row = H.build_market_row("xyz", u, c, MON_1800)
    check("disloc ~33bps", abs(row["disloc_bps"] - 33.0) < 0.5, str(row["disloc_bps"]))
    check("day move negative", row["day_move_pct"] < 0)
    check("bucket kr_equity", row["bucket"] == "kr_equity")
    check("ref closed Mon 18:00", row["ref_open"] is False)
    check("oi notional set", row["oi_ntl"] is not None)
    check("pfi positive", row["pfi"] > 0)
    # missing oracle -> graceful
    row2 = H.build_market_row("core", {"name": "BTC"},
                              {"markPx": "100000", "dayNtlVlm": "1"}, MON_1800)
    check("missing oracle -> disloc None", row2["disloc_bps"] is None)
    check("missing oracle -> pfi 0", row2["pfi"] == 0.0)


def test_maker_model():
    print("maker_breakeven_spread_bps:")
    check("s* = a*q*L", abs(H.maker_breakeven_spread_bps(0.3, 0.5, 30) - 4.5) < 1e-9)
    s_lat = H.maker_breakeven_spread_bps(0.3, 0.9, 30)
    s_hc = H.maker_breakeven_spread_bps(0.3, 0.3, 30)
    check("HyperCore tighter than latency", s_hc < s_lat)


def test_serve_vendor_guard():
    print("serve.safe_vendor_path (path traversal):")
    import serve
    check("serves real vendor file", serve.safe_vendor_path("chart.umd.min.js") is not None)
    check("blocks ../serve.py escape", serve.safe_vendor_path("../serve.py") is None)
    check("blocks absolute escape", serve.safe_vendor_path("/etc/hosts") is None)
    check("blocks sibling-prefix escape", serve.safe_vendor_path("../vendorEVIL.js") is None)
    check("blocks nonexistent", serve.safe_vendor_path("nope.js") is None)


def test_cache_bound():
    print("TTLCache bound:")
    import serve
    c = serve.TTLCache(max_entries=5)
    for i in range(20):
        c.get_or_compute(f"k{i}", 1e9, lambda i=i: i)
    check("cache bounded to max_entries", len(c._store) <= 5, str(len(c._store)))


def test_live():
    print("LIVE smoke (public API):")
    t0 = time.time()
    rows = H.fetch_all_markets()
    dt = time.time() - t0
    check("fetched >100 markets", len(rows) > 100, f"got {len(rows)}")
    check("fetch < 30s", dt < 30, f"{dt:.1f}s")
    with_disloc = [r for r in rows if r["disloc_bps"] is not None]
    check("most rows have dislocation", len(with_disloc) > 0.5 * len(rows))
    check("BTC present", any(r["coin"] == "BTC" for r in rows))
    check("xyz dex present", any(r["dex"] == "xyz" for r in rows))
    detail = H.fetch_market_detail("BTC")
    check("BTC detail has spread", detail["spread_bps"] is not None,
          str(detail))


if __name__ == "__main__":
    live = "--no-live" not in sys.argv
    test_classify()
    test_clock()
    test_clock_dst()
    test_pfi()
    test_realized_vol()
    test_build_row()
    test_maker_model()
    test_serve_vendor_guard()
    test_cache_bound()
    if live:
        test_live()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
