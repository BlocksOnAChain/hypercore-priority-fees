/*
 * hl-core.js — HyperCore market data + Priority-Fee Opportunity Index (PFI),
 * ported to browser JS so the dashboard runs with NO backend: the browser calls
 * the public Hyperliquid info API directly (it sends `access-control-allow-origin: *`).
 *
 * Dual-use: defines a global `HLCore` for the browser AND exports for Node
 * (so test_site.js can unit-test the pure logic). No build step, no deps.
 *
 * Reference Python implementation: ../hl_data.py (kept in sync; same formulas).
 */
(function (root) {
  "use strict";

  const INFO_URL = "https://api.hyperliquid.xyz/info";

  async function post(payload) {
    const r = await fetch(INFO_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  function fnum(x) {
    if (x === null || x === undefined) return null;
    const v = Number(x);
    return Number.isFinite(v) ? v : null;
  }

  // ---- reference-market classification (mirrors hl_data.py) ----
  const S = a => new Set(a);
  const KR_EQUITY = S(["SKHX", "SMSN", "HYUNDAI", "KAKAO", "NAVER", "KOSPI"]);
  const JP_EQUITY = S(["KIOXIA", "SOFTBANK", "IBIDEN", "TOYOTA", "SONY", "NINTENDO"]);
  const TW_EQUITY = S(["TSM", "TSMC", "MEDIATEK", "FOXCONN"]);
  const CN_EQUITY = S(["ZHIPU", "MINIMAX", "BABA", "ALIBABA", "TENCENT", "BYD", "NIO", "PDD"]);
  const EU_EQUITY = S(["ASML", "SAP", "LVMH", "NESTLE", "NOVO", "SIEMENS"]);
  const US_EQUITY = S(["AAPL", "NVDA", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
    "AMD", "INTC", "MU", "AVGO", "MRVL", "ARM", "DELL", "COIN", "MSTR", "HOOD", "CRCL",
    "CRWV", "NBIS", "PLTR", "SNDK", "BE", "BX", "EBAY", "DKNG", "GME", "HIMS", "LLY",
    "COST", "KR", "NFLX", "NOW", "ORCL", "IBM", "AMAT", "CBRS", "SPCX", "STRC", "BIRD",
    "BOT", "LITE", "H100", "DRAM", "QCOM", "TXN", "SMCI", "UBER", "PYPL", "SHOP",
    "ABNB", "RBLX", "SOFI", "RDDT"]);
  const COMMODITY = S(["GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER", "ALUMINIUM",
    "ALUMINUM", "CL", "BRENTOIL", "NATGAS", "CORN", "WHEAT", "SOYBEAN"]);
  const FX = S(["EUR", "GBP", "JPY", "KRW", "DXY", "CHF", "CAD", "AUD", "CNY", "CNH"]);
  const US_INDEX = S(["SP500", "XYZ100", "NDX", "DJI", "RUSSELL"]);
  const ASIA_INDEX = S(["NIFTY", "JP225", "KR200", "HSI", "EWJ", "EWY", "EWT"]);
  const LATAM_INDEX = S(["IBOV", "IBOVESPA", "EWZ", "MEXBOL"]);

  function classifyReference(coin, dex) {
    if (dex === "" || dex === "core") return "crypto";
    const base = (coin ? coin.split(":").pop() : "").toUpperCase();
    if (KR_EQUITY.has(base)) return "kr_equity";
    if (JP_EQUITY.has(base)) return "jp_equity";
    if (TW_EQUITY.has(base)) return "tw_equity";
    if (CN_EQUITY.has(base)) return "cn_equity";
    if (EU_EQUITY.has(base)) return "eu_equity";
    if (US_EQUITY.has(base)) return "us_equity";
    if (COMMODITY.has(base)) return "commodity";
    if (FX.has(base)) return "fx";
    if (US_INDEX.has(base)) return "us_index";
    if (ASIA_INDEX.has(base)) return "asia_index";
    if (LATAM_INDEX.has(base)) return "latam_index";
    return "other";
  }

  // ---- session clock (DST-correct via Intl timezone) ----
  // [IANA tz, openMinuteLocal, closeMinuteLocal]
  const BUCKET_TZ = {
    us_equity:   ["America/New_York", 9 * 60 + 30, 16 * 60],
    us_index:    ["America/New_York", 9 * 60 + 30, 16 * 60],
    kr_equity:   ["Asia/Seoul", 9 * 60, 15 * 60 + 30],
    jp_equity:   ["Asia/Tokyo", 9 * 60, 15 * 60],
    tw_equity:   ["Asia/Taipei", 9 * 60, 13 * 60 + 30],
    cn_equity:   ["Asia/Shanghai", 9 * 60 + 30, 15 * 60],
    eu_equity:   ["Europe/Berlin", 9 * 60, 17 * 60 + 30],
    asia_index:  ["Asia/Tokyo", 9 * 60, 15 * 60],
    latam_index: ["America/Sao_Paulo", 10 * 60, 17 * 60],
  };
  const _WD = { Sun: 6, Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5 };

  function localParts(tz, date) {
    const f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    });
    const p = {};
    for (const part of f.formatToParts(date)) p[part.type] = part.value;
    return { wd: _WD[p.weekday], min: (+p.hour) * 60 + (+p.minute) };
  }

  // date defaults to now; pass a Date for deterministic tests.
  function referenceOpen(bucket, date) {
    date = date || new Date();
    if (bucket === "crypto") return true;
    if (bucket === "fx" || bucket === "commodity") {
      // approximate ~24h with weekend break, evaluated in UTC
      const wd = (date.getUTCDay() + 6) % 7; // Mon=0..Sun=6
      const mod = date.getUTCHours() * 60 + date.getUTCMinutes();
      if (bucket === "fx") {
        if (wd === 5) return false;
        if (wd === 6) return mod >= 22 * 60;
        if (wd === 4) return mod < 22 * 60;
        return true;
      }
      if (wd === 5) return false;
      if (wd === 6) return mod >= 22 * 60;
      return !(mod >= 21 * 60 && mod < 22 * 60);
    }
    const cfg = BUCKET_TZ[bucket];
    if (!cfg) return null;
    const [tz, open, close] = cfg;
    const { wd, min } = localParts(tz, date);
    if (wd >= 5) return false;
    return open <= min && min < close;
  }

  function clockFactor(bucket, date) {
    const open = referenceOpen(bucket, date);
    if (open === null) return 1.0;
    if (bucket === "crypto" || bucket === "fx" || bucket === "commodity") return 1.0;
    return open ? 1.0 : 1.6;
  }

  function computePFI(dislocBps, vol24h, bucket, date) {
    if (dislocBps === null || dislocBps === undefined) return 0.0;
    const contest = Math.log10(1.0 + Math.max(vol24h || 0, 0) / 1e5);
    return dislocBps * contest * clockFactor(bucket, date);
  }

  function buildMarketRow(dex, u, c, date) {
    const coin = u.name;
    const mark = fnum(c.markPx), oracle = fnum(c.oraclePx), mid = fnum(c.midPx);
    const prev = fnum(c.prevDayPx), oi = fnum(c.openInterest), funding = fnum(c.funding);
    const vol = fnum(c.dayNtlVlm) || 0.0;
    const oiNtl = (oi !== null && mark) ? oi * mark : null;
    const disloc = (mark && oracle) ? Math.abs(mark - oracle) / oracle * 1e4 : null;
    const dayMove = (mark && prev) ? (mark - prev) / prev * 100 : null;
    const bucket = classifyReference(coin, dex);
    return {
      dex, coin, bucket,
      ref_open: referenceOpen(bucket, date),
      vol_24h: vol, oi_ntl: oiNtl, mark, oracle, mid,
      funding_bps_hr: funding !== null ? funding * 1e4 : null,
      disloc_bps: disloc, day_move_pct: dayMove,
      pfi: computePFI(disloc, vol, bucket, date),
    };
  }

  async function fetchPerpDexs() {
    const dexs = await post({ type: "perpDexs" });
    return dexs.map(d => (d === null ? "" : (d.name || "")));
  }

  async function fetchAllMarkets() {
    const date = new Date();
    const dexes = await fetchPerpDexs();
    const results = await Promise.all(dexes.map(async (nm) => {
      const payload = { type: "metaAndAssetCtxs" };
      if (nm) payload.dex = nm;
      try {
        const [meta, ctxs] = await post(payload);
        const uni = meta.universe || [];
        return uni.map((u, i) => buildMarketRow(nm || "core", u, ctxs[i], date));
      } catch (e) { return []; }
    }));
    return results.flat();
  }

  function realizedVolBps(candles) {
    if (!Array.isArray(candles)) return null;
    const closes = candles.map(c => (c && typeof c === "object") ? fnum(c.c) : null);
    const rets = [];
    for (let i = 1; i < closes.length; i++) {
      const p0 = closes[i - 1], p1 = closes[i];
      if (p0 && p1 && p0 > 0) rets.push(Math.log(p1 / p0));
    }
    if (rets.length < 3) return null;
    const m = rets.reduce((s, x) => s + x, 0) / rets.length;
    const v = rets.reduce((s, x) => s + (x - m) ** 2, 0) / (rets.length - 1);
    return Math.sqrt(v) * 1e4;
  }

  async function fetchMarketDetail(coin) {
    const out = { coin, spread_bps: null, bid_depth_10bp: null, ask_depth_10bp: null, rv_1m_bps: null };
    const px = l => (l && typeof l === "object") ? fnum(l.px) : null;
    const ntl = l => { const p = px(l), s = (l && typeof l === "object") ? fnum(l.sz) : null;
      return (p !== null && s !== null) ? p * s : 0; };
    try {
      const book = await post({ type: "l2Book", coin });
      const levels = (book && book.levels) || [[], []];
      const bids = levels[0] || [], asks = levels[1] || [];
      if (bids.length && asks.length) {
        const b0 = px(bids[0]), a0 = px(asks[0]);
        if (b0 && a0) {
          const mid = (b0 + a0) / 2;
          out.spread_bps = (a0 - b0) / mid * 1e4;
          const lo = mid * 0.999, hi = mid * 1.001;
          out.bid_depth_10bp = bids.filter(l => (px(l) || 0) >= lo).reduce((s, l) => s + ntl(l), 0);
          out.ask_depth_10bp = asks.filter(l => (px(l) || 1e18) <= hi).reduce((s, l) => s + ntl(l), 0);
        }
      }
      const now = Date.now();
      const candles = await post({ type: "candleSnapshot", req: {
        coin, interval: "1m", startTime: now - 90 * 60 * 1000, endTime: now } });
      out.rv_1m_bps = realizedVolBps(candles);
    } catch (e) { out.error = String(e); }
    return out;
  }

  // ---- shared helpers (also used by UI) ----
  const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y);
    const m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const mean = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;
  const makerBreakevenSpreadBps = (alpha, q, Lbps) => alpha * q * Lbps;

  const HLCore = {
    INFO_URL, post, fnum, classifyReference, referenceOpen, clockFactor, computePFI,
    buildMarketRow, fetchPerpDexs, fetchAllMarkets, realizedVolBps, fetchMarketDetail,
    median, mean, makerBreakevenSpreadBps, BUCKET_TZ,
    CLOCK_BUCKETS: new Set(["us_equity", "kr_equity", "jp_equity", "cn_equity",
      "eu_equity", "tw_equity", "us_index", "asia_index", "latam_index"]),
  };

  root.HLCore = HLCore;
  if (typeof module !== "undefined" && module.exports) module.exports = HLCore;
})(typeof globalThis !== "undefined" ? globalThis : this);
