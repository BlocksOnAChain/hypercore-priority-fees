# Hyperliquid Priority Fees — idea + demo for review

> **For a quant reviewer.** Self-contained package: the idea, the formal model, a live
> dashboard, the data methodology, and two test suites. Read in ~15 min.

**Live demo:** **https://site-hazel-two-96.vercel.app** *(deployed static site — calls the public Hyperliquid API directly from your browser; read-only, no backend, no keys)*

---

## The idea in five sentences

1. HyperCore sequences transactions in a block by `effective_time = arrival_time + f(action, priority_fee)`, where `f` is strictly decreasing in the fee and **exactly 0 for cancels** — so *all cancels are ordered ahead of all taker/IOC orders, and no taker can buy priority over a cancel at any price* (the order-priority fee caps at 8 bp ≈ 360 ms and is charged on fill, then burned).
2. That gives a resting **maker a free, un-outbiddable last look**: its only race is observe→cancel-before-the-block-boundary, not a fee war against snipers.
3. In a competitive zero-profit (Glosten–Milgrom) frame, the break-even half-spread is `s* = α·q·L`; the venue only changes `q` (pickoff probability), so `q_hc ≪ q_lat` ⇒ a HyperCore maker can quote **tighter / larger for the same adverse-selection budget**, and the edge is biggest exactly where toxicity (`α·L`) is biggest.
4. Toxicity concentrates in **tokenized-RWA perps whose reference cash market is closed** (stale oracle → big mark/oracle dislocation) — the demo measures this live across all 452 markets and ranks it with a Priority-Fee Opportunity Index.
5. Honest scope: this is **latency-sequenced stat-arb with adverse selection, not risk-free arb**; the taker-side sniping game is largely inaccessible to non-latency players, but the **maker-side last-look edge needs you to be fast *enough*, not fastest.**

## What to read
- **[STRATEGY.md](STRATEGY.md)** — the money model: where the P&L actually comes from (under-competed markets + pricing edge, not the zero-profit per-fill identity), the flow-based P&L equation, the **decisive fee reality** (Growth-Mode maker 0.30→0 bp vs standard 3 bp), and a base/good/best scenario table grounded in the measured data + the capacity frontier.
- **[MODEL.md](MODEL.md)** — the formal model: the exact `s* = αqL/(1−α+αq)` derivation (with `αqL` as the leading-order form), the `q_hc ≪ q_lat` argument, the "free priority dominates purchasable priority" proposition, the SKHX calibration, and a **"what to attack"** section (weakest assumptions + falsifying tests).
- **[hyperliquid_priority_fees_tier1.md](hyperliquid_priority_fees_tier1.md)** — the broader research memo (mechanism, the three Tier-1 ideas, the Solana competitive read, what's measured vs. needs-data).
- **The live demo** — PFI ranking + the reference-clock cross-section + an interactive maker last-look calculator.

## Run it locally
Two equivalent implementations; both pull live from the public Hyperliquid info API (no auth).

```bash
# A) Serverless static site (what's deployed) — pure browser JS:
cd site && python3 -m http.server 8089        # → http://localhost:8089

# B) Python reference + local server (same logic, server-side):
python3 serve.py                               # → http://localhost:8787
python3 hl_data.py                             # CLI: prints the live PFI ranking
```

## Tests (both suites pass; this is the verification surface)
```bash
python3 test_hl.py        # Python reference impl — 56 tests
node test_site.js         # browser-port (site/hl-core.js) — 51 tests
# add --no-live to either to skip the live-API smoke test
```
They cover: reference-market classification, **DST-correct session clock** (incl. winter-EST cases the naive UTC windows got wrong), PFI monotonicity in dislocation/volume, the clock-factor lift, true median (even-`n`), realized-vol math, malformed-input robustness, path-traversal guard (Python server), and the maker model. The two impls are cross-checked against the same fixtures so the browser port is provably equivalent to the Python.

## Honest status (for the skeptic)
| Claim | Status |
|---|---|
| Cancels beat all takers; maker last-look is free & un-outbiddable | **Proven** (docs) |
| Dislocation concentrates in reference-clock-closed RWA names | **Measured** (live cross-section) |
| Makers quote tighter than naive adverse selection allows | **Consistent-with**, not causal (snapshot) |
| Tighter spread for same toxic budget (`s*=α·q·L`, `q_hc<q_lat`) | **Modeled**; `α`, `q` not fitted |
| 8 bp priority cap is frequently *binding* | **Inferred** (cap cut 20→8 on feedback); needs the actual `p` distribution |

**The one data unlock:** the exact priority `p` paid per order is *not* in the public API (fills carry only a total fee). Confirming "is the cap binding" and turning the cross-section into a true signal needs an indexed warehouse (Allium/Dune) or raw L1 node data. Everything here is the best *public* proxy.

## File map
```
site/                 deployed static site (serverless)
  index.html          dashboard UI
  hl-core.js          data + PFI + DST-clock logic, ported to browser JS
  vendor/             Chart.js (vendored; no CDN dependency)
hl_data.py            Python reference implementation (same formulas)
serve.py              local server + cached JSON API (path-traversal-guarded, bounded cache)
test_hl.py            Python test suite (56)
test_site.js          JS-port test suite (51)
MODEL.md              formal model + "what to attack"
hyperliquid_priority_fees_tier1.md   research memo
PROGRESS.md           build log / decisions / adversarial-review trail
```
