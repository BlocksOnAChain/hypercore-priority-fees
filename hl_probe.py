#!/usr/bin/env python3
"""Enumerate Hyperliquid perp markets (core + HIP-3 builder dexs) and compute
observable stale-liquidity / toxicity proxies from the public info API."""
import json, urllib.request, sys

URL = "https://api.hyperliquid.xyz/info"

def post(payload, timeout=25):
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def fnum(x):
    try: return float(x)
    except: return None

# 1) enumerate perp dexs (HIP-3 deployers). index 0 == None == core.
dexs = post({"type": "perpDexs"})
dex_names = []
for d in dexs:
    if d is None:
        dex_names.append(("", "core"))
    else:
        dex_names.append((d.get("name",""), d.get("fullName", d.get("name",""))))
print("=== PERP DEXS ===")
for nm, full in dex_names:
    print(f"  dex='{nm}'  ({full})")

rows = []  # (dex, coin, vol24h, oi_ntl, mark, oracle, disloc_bps, day_move_pct, funding)
for nm, full in dex_names:
    try:
        payload = {"type": "metaAndAssetCtxs"}
        if nm:
            payload["dex"] = nm
        meta, ctxs = post(payload)
    except Exception as e:
        print(f"  [skip dex '{nm}': {e}]")
        continue
    uni = meta.get("universe", [])
    for u, c in zip(uni, ctxs):
        coin = u.get("name")
        mark = fnum(c.get("markPx")); oracle = fnum(c.get("oraclePx"))
        mid = fnum(c.get("midPx")); prev = fnum(c.get("prevDayPx"))
        vol = fnum(c.get("dayNtlVlm")) or 0.0
        oi = fnum(c.get("openInterest"))
        oi_ntl = (oi * mark) if (oi is not None and mark) else None
        fund = fnum(c.get("funding"))
        disloc = (abs(mark-oracle)/oracle*1e4) if (mark and oracle) else None
        day_move = ((mark-prev)/prev*100) if (mark and prev) else None
        rows.append((nm or "core", coin, vol, oi_ntl, mark, oracle, disloc, day_move, fund))

print(f"\n=== {len(rows)} markets enumerated across {len(dex_names)} dexs ===")

# Top 25 by 24h notional volume
print("\n=== TOP 25 MARKETS BY 24h NOTIONAL VOLUME ===")
print(f"{'dex':<6}{'coin':<16}{'vol24h($M)':>12}{'OI($M)':>12}{'disloc(bps)':>12}{'dayMove%':>10}{'fund(bps/hr)':>13}")
for r in sorted(rows, key=lambda x: -(x[2] or 0))[:25]:
    dex, coin, vol, oi, mark, oracle, disloc, dm, fund = r
    print(f"{dex:<6}{coin:<16}{vol/1e6:>12.2f}{(oi or 0)/1e6:>12.2f}"
          f"{(disloc if disloc is not None else float('nan')):>12.2f}"
          f"{(dm if dm is not None else float('nan')):>10.2f}"
          f"{(fund*1e4 if fund is not None else float('nan')):>13.3f}")

# Top 20 by current mark/oracle dislocation (min volume filter)
print("\n=== TOP 20 BY MARK/ORACLE DISLOCATION (vol>$50k) ===")
flt = [r for r in rows if (r[6] is not None and (r[2] or 0) > 50_000)]
print(f"{'dex':<6}{'coin':<16}{'disloc(bps)':>12}{'vol24h($M)':>12}{'dayMove%':>10}")
for r in sorted(flt, key=lambda x: -(x[6] or 0))[:20]:
    dex, coin, vol, oi, mark, oracle, disloc, dm, fund = r
    print(f"{dex:<6}{coin:<16}{disloc:>12.2f}{vol/1e6:>12.2f}"
          f"{(dm if dm is not None else float('nan')):>10.2f}")

# Per-dex aggregate volume
print("\n=== VOLUME BY DEX ===")
agg = {}
for r in rows:
    agg.setdefault(r[0], [0.0,0])
    agg[r[0]][0] += (r[2] or 0); agg[r[0]][1] += 1
for dex, (v, n) in sorted(agg.items(), key=lambda x:-x[1][0]):
    print(f"  {dex:<8} {n:>4} markets  ${v/1e6:>10.2f}M 24h vol")
