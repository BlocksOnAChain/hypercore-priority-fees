/*
 * test_site.js — unit tests for the browser-ported logic in site/hl-core.js.
 * Mirrors the Python suite (test_hl.py) so the JS port is provably equivalent.
 *   node test_site.js            # all incl. live API smoke
 *   node test_site.js --no-live  # pure-logic only
 * Requires Node >= 18 (global fetch) with full-ICU (timezone support) — Node 22 ok.
 */
const H = require("./site/hl-core.js");

let PASS = 0, FAIL = 0;
function check(name, cond, extra = "") {
  if (cond) { PASS++; console.log("  ok   " + name); }
  else { FAIL++; console.log("  FAIL " + name + "  " + extra); }
}

// deterministic UTC instants (2026-06-29 is Monday; Jan 5 2026 is Monday)
const MON_1800 = new Date("2026-06-29T18:00:00Z"); // summer/EDT
const MON_0300 = new Date("2026-06-29T03:00:00Z");
const SAT_1200 = new Date("2026-06-27T12:00:00Z");
const JAN_2030 = new Date("2026-01-05T20:30:00Z"); // 15:30 EST
const JAN_1345 = new Date("2026-01-05T13:45:00Z"); // 08:45 EST

function testClassify() {
  console.log("classifyReference:");
  check("BTC core -> crypto", H.classifyReference("BTC", "core") === "crypto");
  check("SKHX -> kr_equity", H.classifyReference("xyz:SKHX", "xyz") === "kr_equity");
  check("KIOXIA -> jp_equity", H.classifyReference("xyz:KIOXIA", "xyz") === "jp_equity");
  check("NVDA -> us_equity", H.classifyReference("xyz:NVDA", "xyz") === "us_equity");
  check("GOLD -> commodity", H.classifyReference("xyz:GOLD", "xyz") === "commodity");
  check("EUR -> fx", H.classifyReference("xyz:EUR", "xyz") === "fx");
  check("SP500 -> us_index", H.classifyReference("xyz:SP500", "xyz") === "us_index");
  check("ZHIPU -> cn_equity", H.classifyReference("xyz:ZHIPU", "xyz") === "cn_equity");
  check("unknown -> other", H.classifyReference("xyz:WAT", "xyz") === "other");
}

function testClock() {
  console.log("referenceOpen / clockFactor:");
  check("crypto always open", H.referenceOpen("crypto", MON_1800) === true);
  check("US equity open Mon 18:00 UTC (EDT)", H.referenceOpen("us_equity", MON_1800) === true);
  check("US equity closed Mon 03:00 UTC", H.referenceOpen("us_equity", MON_0300) === false);
  check("KR equity open Mon 03:00 UTC", H.referenceOpen("kr_equity", MON_0300) === true);
  check("KR equity closed Mon 18:00 UTC", H.referenceOpen("kr_equity", MON_1800) === false);
  check("US equity closed Saturday", H.referenceOpen("us_equity", SAT_1200) === false);
  check("unknown bucket -> null", H.referenceOpen("other", MON_1800) === null);
  check("closed eq -> factor 1.6", H.clockFactor("kr_equity", MON_1800) === 1.6);
  check("open eq -> factor 1.0", H.clockFactor("kr_equity", MON_0300) === 1.0);
  check("crypto -> factor 1.0", H.clockFactor("crypto", MON_1800) === 1.0);
}

function testClockDst() {
  console.log("DST correctness (winter EST):");
  check("US equity OPEN 15:30 EST (Jan)", H.referenceOpen("us_equity", JAN_2030) === true);
  check("US equity CLOSED 08:45 EST (Jan)", H.referenceOpen("us_equity", JAN_1345) === false);
}

function testPfi() {
  console.log("computePFI:");
  check("null disloc -> 0", H.computePFI(null, 1e8, "crypto", MON_1800) === 0.0);
  check("null volume -> no throw, 0", H.computePFI(20, null, "crypto", MON_1800) === 0.0);
  check("increasing in dislocation",
    H.computePFI(30, 1e7, "us_equity", MON_1800) > H.computePFI(5, 1e7, "us_equity", MON_1800));
  check("increasing in volume",
    H.computePFI(20, 1e8, "crypto", MON_1800) > H.computePFI(20, 1e6, "crypto", MON_1800));
  const pOpen = H.computePFI(20, 1e7, "kr_equity", MON_0300);
  const pClosed = H.computePFI(20, 1e7, "kr_equity", MON_1800);
  check("closed scores higher", pClosed > pOpen);
  check("closed == 1.6x open", Math.abs(pClosed - 1.6 * pOpen) < 1e-9);
}

function testMedian() {
  console.log("median (true median, even-n):");
  check("even -> average of centers", H.median([10, 40]) === 25);
  check("[1,2,3,4] -> 2.5", H.median([1, 2, 3, 4]) === 2.5);
  check("odd -> middle", H.median([1, 2, 3]) === 2);
  check("single", H.median([7]) === 7);
  check("empty -> null", H.median([]) === null);
}

function testRealizedVol() {
  console.log("realizedVolBps:");
  check("non-array -> null", H.realizedVolBps(null) === null);
  check("too few -> null", H.realizedVolBps([{ c: "1" }, { c: "1" }]) === null);
  check("flat -> 0", H.realizedVolBps(Array(10).fill({ c: "100" })) === 0);
  check("malformed candle -> no throw",
    H.realizedVolBps([{ c: "100" }, {}, { x: 1 }, { c: "101" }]) === null);
  const noisy = [{ c: "100" }, { c: "101" }, { c: "100" }, { c: "101" }, { c: "100" }];
  check("oscillating -> positive", H.realizedVolBps(noisy) > 0);
}

function testBuildRow() {
  console.log("buildMarketRow (fixture):");
  const u = { name: "xyz:SKHX" };
  const c = { markPx: "100.33", oraclePx: "100.0", midPx: "100.3", prevDayPx: "105.0",
    dayNtlVlm: "351000000", openInterest: "2000000", funding: "0.0001" };
  const row = H.buildMarketRow("xyz", u, c, MON_1800);
  check("disloc ~33bps", Math.abs(row.disloc_bps - 33.0) < 0.5, String(row.disloc_bps));
  check("bucket kr_equity", row.bucket === "kr_equity");
  check("ref closed Mon 18:00", row.ref_open === false);
  check("oi notional set", row.oi_ntl !== null);
  check("pfi positive", row.pfi > 0);
  const row2 = H.buildMarketRow("core", { name: "BTC" }, { markPx: "100000", dayNtlVlm: "1" }, MON_1800);
  check("missing oracle -> disloc null", row2.disloc_bps === null);
  check("missing oracle -> pfi 0", row2.pfi === 0.0);
}

function testMaker() {
  console.log("makerBreakevenSpreadBps:");
  check("s* = a*q*L", Math.abs(H.makerBreakevenSpreadBps(0.3, 0.5, 30) - 4.5) < 1e-9);
  check("HyperCore tighter", H.makerBreakevenSpreadBps(0.3, 0.3, 30) < H.makerBreakevenSpreadBps(0.3, 0.9, 30));
}

async function testLive() {
  console.log("LIVE smoke (public API, browser-style fetch):");
  const t0 = Date.now();
  const rows = await H.fetchAllMarkets();
  check("fetched >100 markets", rows.length > 100, "got " + rows.length);
  check("fetch < 30s", Date.now() - t0 < 30000);
  check("BTC present", rows.some(r => r.coin === "BTC"));
  check("xyz dex present", rows.some(r => r.dex === "xyz"));
  const d = await H.fetchMarketDetail("BTC");
  check("BTC detail has spread", d.spread_bps !== null, JSON.stringify(d));
}

(async () => {
  const live = !process.argv.includes("--no-live");
  testClassify(); testClock(); testClockDst(); testPfi();
  testMedian(); testRealizedVol(); testBuildRow(); testMaker();
  if (live) await testLive();
  console.log(`\n${PASS} passed, ${FAIL} failed`);
  process.exit(FAIL ? 1 : 0);
})();
