#!/usr/bin/env python3
"""
hl_data.py — live HyperCore market data + Priority-Fee Opportunity Index (PFI).

All data is pulled from the PUBLIC Hyperliquid info API (no auth, read-only).
The exact priority fee `p` paid per order is NOT exposed by the public API, so
instead of measuring the *price of immediacy* we measure the *thing immediacy is
paid for*: the live stale-liquidity surface (mark/oracle dislocation), weighted
by contested volume and a reference-market-clock factor.

Pure stdlib. Importable (used by serve.py + tests) and runnable as a CLI.
"""
from __future__ import annotations
import datetime
import json
import math
import time
import urllib.request
from zoneinfo import ZoneInfo

INFO_URL = "https://api.hyperliquid.xyz/info"
HTTP_TIMEOUT = 25


# --------------------------------------------------------------------------- #
# Low-level API
# --------------------------------------------------------------------------- #
def post(payload: dict, timeout: int = HTTP_TIMEOUT) -> object:
    req = urllib.request.Request(
        INFO_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fnum(x):
    """Best-effort float; None on failure."""
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Reference-market classification + session clock
#
# RWA perps reference an external cash market that trades on its own clock. When
# that market is closed, on-chain quotes drift from the (stale) reference -> the
# dislocation we observe empirically. Classification is heuristic and explicit;
# the dislocation number itself is the ground truth, this is the overlay.
# --------------------------------------------------------------------------- #
KR_EQUITY = {"SKHX", "SMSN", "HYUNDAI", "KAKAO", "NAVER", "KOSPI"}
JP_EQUITY = {"KIOXIA", "SOFTBANK", "IBIDEN", "TOYOTA", "SONY", "NINTENDO"}
CN_EQUITY = {"ZHIPU", "MINIMAX", "BABA", "ALIBABA", "TENCENT", "BYD", "NIO", "PDD"}
EU_EQUITY = {"ASML", "SAP", "LVMH", "NESTLE", "NOVO", "SIEMENS"}
US_EQUITY = {
    "AAPL", "NVDA", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AMD", "INTC",
    "MU", "AVGO", "MRVL", "ARM", "DELL", "COIN", "MSTR", "HOOD", "CRCL", "CRWV",
    "NBIS", "PLTR", "SNDK", "BE", "BX", "EBAY", "DKNG", "GME", "HIMS", "LLY",
    "COST", "KR", "NFLX", "NOW", "ORCL", "IBM", "AMAT", "CBRS", "SPCX", "STRC",
    "BIRD", "BOT", "LITE", "H100", "DRAM", "QCOM", "TXN", "MRVL", "SMCI",
    "TSLA", "UBER", "PYPL", "SHOP", "ABNB", "RBLX", "SOFI", "RDDT",
}
TW_EQUITY = {"TSM", "TSMC", "MEDIATEK", "FOXCONN"}
COMMODITY = {
    "GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER", "ALUMINIUM", "ALUMINUM",
    "CL", "BRENTOIL", "NATGAS", "CORN", "WHEAT", "SOYBEAN",
}
FX = {"EUR", "GBP", "JPY", "KRW", "DXY", "CHF", "CAD", "AUD", "CNY", "CNH"}
US_INDEX = {"SP500", "XYZ100", "NDX", "DJI", "RUSSELL"}
ASIA_INDEX = {"NIFTY", "JP225", "KR200", "HSI", "EWJ", "EWY", "EWT", "KR"}
LATAM_INDEX = {"IBOV", "IBOVESPA", "EWZ", "MEXBOL"}


def classify_reference(coin: str, dex: str) -> str:
    """Return a reference-market bucket for a market symbol."""
    if dex in ("", "core"):
        # core perps are crypto (BTC, ETH, SOL, HYPE, alts...)
        return "crypto"
    base = coin.split(":")[-1].upper() if coin else ""
    if base in KR_EQUITY:
        return "kr_equity"
    if base in JP_EQUITY:
        return "jp_equity"
    if base in TW_EQUITY:
        return "tw_equity"
    if base in CN_EQUITY:
        return "cn_equity"
    if base in EU_EQUITY:
        return "eu_equity"
    if base in US_EQUITY:
        return "us_equity"
    if base in COMMODITY:
        return "commodity"
    if base in FX:
        return "fx"
    if base in US_INDEX:
        return "us_index"
    if base in ASIA_INDEX:
        return "asia_index"
    if base in LATAM_INDEX:
        return "latam_index"
    return "other"


# Cash-market sessions as (IANA timezone, open, close) in LOCAL minutes-of-day,
# Mon-Fri. Using a real tz makes the open/closed call DST-correct year-round
# (vs. hardcoded UTC windows, which are an hour wrong outside summer).
# Still coarse: ignores holidays, half-days, and lunch breaks. asia_index/
# latam_index use a representative venue (the set is heterogeneous).
BUCKET_TZ = {
    "us_equity":   ("America/New_York",  9 * 60 + 30, 16 * 60),       # NYSE/Nasdaq
    "us_index":    ("America/New_York",  9 * 60 + 30, 16 * 60),
    "kr_equity":   ("Asia/Seoul",        9 * 60,      15 * 60 + 30),  # KRX
    "jp_equity":   ("Asia/Tokyo",        9 * 60,      15 * 60),       # TSE
    "tw_equity":   ("Asia/Taipei",       9 * 60,      13 * 60 + 30),  # TWSE
    "cn_equity":   ("Asia/Shanghai",     9 * 60 + 30, 15 * 60),       # SSE/SZSE
    "eu_equity":   ("Europe/Berlin",     9 * 60,      17 * 60 + 30),  # XETRA
    "asia_index":  ("Asia/Tokyo",        9 * 60,      15 * 60),       # representative
    "latam_index": ("America/Sao_Paulo", 10 * 60,     17 * 60),       # B3, representative
}


def reference_open(bucket: str, now_utc: time.struct_time | None = None) -> bool | None:
    """Is the reference cash market likely open right now?
    True/False for clock-bound markets; True for ~24h (crypto/fx/commodity);
    None for 'other' (unknown). DST handled via the reference market's own tz."""
    if now_utc is None:
        now_utc = time.gmtime()
    if bucket == "crypto":
        return True              # 24/7
    if bucket == "fx":
        # FX trades ~24h Sun 22:00 UTC -> Fri 22:00 UTC
        wd = now_utc.tm_wday     # Mon=0 .. Sun=6
        mod = now_utc.tm_hour * 60 + now_utc.tm_min
        if wd == 5:              # Sat
            return False
        if wd == 6:              # Sun: open after 22:00
            return mod >= 22 * 60
        if wd == 4:              # Fri: closes 22:00
            return mod < 22 * 60
        return True
    if bucket == "commodity":
        # Globex-ish ~23h/day, closed ~21:00-22:00 UTC + weekend. Approximate open.
        wd = now_utc.tm_wday
        mod = now_utc.tm_hour * 60 + now_utc.tm_min
        if wd == 5:
            return False
        if wd == 6:
            return mod >= 22 * 60
        return not (21 * 60 <= mod < 22 * 60)
    cfg = BUCKET_TZ.get(bucket)
    if cfg is None:
        return None              # unknown reference
    tzname, open_min, close_min = cfg
    dt_utc = datetime.datetime(
        now_utc.tm_year, now_utc.tm_mon, now_utc.tm_mday,
        now_utc.tm_hour, now_utc.tm_min, now_utc.tm_sec,
        tzinfo=datetime.timezone.utc)
    local = dt_utc.astimezone(ZoneInfo(tzname))
    if local.weekday() >= 5:     # weekend: equity cash markets closed
        return False
    mod = local.hour * 60 + local.minute
    return open_min <= mod < close_min


def clock_factor(bucket: str, now_utc: time.struct_time | None = None) -> float:
    """Weight applied to dislocation in the PFI. A closed reference market means
    quotes are structurally stale -> the opportunity (and toxic-flow risk) is
    larger. 24/7 markets get a neutral 1.0."""
    is_open = reference_open(bucket, now_utc)
    if is_open is None:
        return 1.0
    if bucket in ("crypto", "fx", "commodity"):
        return 1.0
    return 1.6 if not is_open else 1.0


# --------------------------------------------------------------------------- #
# Market enumeration + PFI
# --------------------------------------------------------------------------- #
def fetch_perp_dexs() -> list[tuple[str, str]]:
    """Return [(dex_name, full_name)] including ('','core') for the core book."""
    dexs = post({"type": "perpDexs"})
    out = []
    for d in dexs:
        if d is None:
            out.append(("", "core"))
        else:
            out.append((d.get("name", ""), d.get("fullName", d.get("name", ""))))
    return out


def fetch_all_markets(now_utc: time.struct_time | None = None) -> list[dict]:
    """Enumerate every perp market across every dex with derived metrics."""
    if now_utc is None:
        now_utc = time.gmtime()
    rows = []
    for nm, _full in fetch_perp_dexs():
        payload = {"type": "metaAndAssetCtxs"}
        if nm:
            payload["dex"] = nm
        try:
            meta, ctxs = post(payload)
        except Exception:
            continue
        for u, c in zip(meta.get("universe", []), ctxs):
            rows.append(build_market_row(nm or "core", u, c, now_utc))
    return rows


def build_market_row(dex: str, u: dict, c: dict, now_utc: time.struct_time) -> dict:
    """Build one market record from a universe entry + asset context.
    Split out from the network loop so it is unit-testable with fixtures."""
    coin = u.get("name")
    mark = fnum(c.get("markPx"))
    oracle = fnum(c.get("oraclePx"))
    mid = fnum(c.get("midPx"))
    prev = fnum(c.get("prevDayPx"))
    vol = fnum(c.get("dayNtlVlm")) or 0.0
    oi = fnum(c.get("openInterest"))
    funding = fnum(c.get("funding"))
    oi_ntl = (oi * mark) if (oi is not None and mark) else None
    disloc_bps = (abs(mark - oracle) / oracle * 1e4) if (mark and oracle) else None
    day_move = ((mark - prev) / prev * 100) if (mark and prev) else None
    bucket = classify_reference(coin, dex)
    is_open = reference_open(bucket, now_utc)
    pfi = compute_pfi(disloc_bps, vol, bucket, now_utc)
    return {
        "dex": dex,
        "coin": coin,
        "bucket": bucket,
        "ref_open": is_open,
        "vol_24h": vol,
        "oi_ntl": oi_ntl,
        "mark": mark,
        "oracle": oracle,
        "mid": mid,
        "funding_bps_hr": (funding * 1e4) if funding is not None else None,
        "disloc_bps": disloc_bps,
        "day_move_pct": day_move,
        "pfi": pfi,
    }


def compute_pfi(disloc_bps, vol_24h, bucket: str, now_utc=None) -> float:
    """Priority-Fee Opportunity Index (v1), transparent and component-based.

        PFI = edge_relevance(disloc) * contest(vol) * clock_factor(bucket)

    - edge_relevance: dislocation in bps, the capturable gross edge. We do NOT
      cap it at the 8bp fee ceiling — a 30bp gap means takers will pay up to the
      full 8bp cap, so larger gaps => more competition, not less.
    - contest: log-scaled 24h notional volume (more flow => more often hit, more
      bidders => closer to the fee cap).
    - clock_factor: >1 when the reference cash market is closed (stale quotes).

    Returns 0.0 when dislocation is unknown.
    """
    if disloc_bps is None:
        return 0.0
    contest = math.log10(1.0 + max(vol_24h or 0.0, 0.0) / 1e5)  # ~0 at $100k, ~2 at $10M
    return disloc_bps * contest * clock_factor(bucket, now_utc)


def fetch_market_detail(coin: str, now_ms: int | None = None) -> dict:
    """Order-book spread/depth + 1-minute realized vol for one market."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    out = {"coin": coin, "spread_bps": None, "bid_depth_10bp": None,
           "ask_depth_10bp": None, "rv_1m_bps": None}
    book = post({"type": "l2Book", "coin": coin})
    levels = book.get("levels", [[], []]) if isinstance(book, dict) else [[], []]
    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []

    def px(level):  # tolerate missing keys / malformed levels
        return fnum(level.get("px")) if isinstance(level, dict) else None

    def notional(level):
        p, s = px(level), (fnum(level.get("sz")) if isinstance(level, dict) else None)
        return p * s if (p is not None and s is not None) else 0.0

    if bids and asks:
        b0, a0 = px(bids[0]), px(asks[0])
        if b0 and a0:
            mid = (b0 + a0) / 2
            out["spread_bps"] = (a0 - b0) / mid * 1e4
            lo, hi = mid * (1 - 0.001), mid * (1 + 0.001)
            out["bid_depth_10bp"] = sum(notional(l) for l in bids if (px(l) or 0) >= lo)
            out["ask_depth_10bp"] = sum(notional(l) for l in asks if (px(l) or 1e18) <= hi)
    candles = post({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "1m",
        "startTime": now_ms - 90 * 60 * 1000, "endTime": now_ms}})
    out["rv_1m_bps"] = realized_vol_bps(candles)
    return out


def realized_vol_bps(candles: list) -> float | None:
    """Stdev of 1-minute log returns, in bps. Split out for unit testing."""
    if not isinstance(candles, list):
        return None
    closes = [fnum(c.get("c")) if isinstance(c, dict) else None for c in candles]
    rets = []
    for i in range(1, len(closes)):
        p0, p1 = closes[i - 1], closes[i]
        if p0 and p1 and p0 > 0:
            rets.append(math.log(p1 / p0))
    if len(rets) < 3:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * 1e4


# --------------------------------------------------------------------------- #
# Maker last-look model (pure; mirrors the JS calculator in the dashboard)
# --------------------------------------------------------------------------- #
def maker_breakeven_spread_bps(alpha: float, q: float, L_bps: float) -> float:
    """Competitive zero-profit half-spread s* = alpha * q * L (Glosten-Milgrom).
    alpha = informed fraction, q = pickoff prob given informed event,
    L_bps = expected adverse move conditional on informed flow."""
    return alpha * q * L_bps


if __name__ == "__main__":
    t0 = time.time()
    rows = fetch_all_markets()
    rows = [r for r in rows if r["disloc_bps"] is not None]
    rows.sort(key=lambda r: -r["pfi"])
    print(f"{len(rows)} markets, fetched in {time.time()-t0:.1f}s "
          f"({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())})\n")
    hdr = f"{'rank':>4} {'dex':<6}{'coin':<16}{'bucket':<11}{'open':<5}" \
          f"{'PFI':>8}{'disloc':>9}{'vol$M':>9}{'day%':>8}"
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(rows[:30], 1):
        print(f"{i:>4} {r['dex']:<6}{r['coin']:<16}{r['bucket']:<11}"
              f"{str(r['ref_open']):<5}{r['pfi']:>8.1f}"
              f"{r['disloc_bps']:>9.1f}{r['vol_24h']/1e6:>9.1f}"
              f"{(r['day_move_pct'] or 0):>8.2f}")
