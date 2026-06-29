# HyperCore Priority-Fee Opportunity Monitor

**A live research demo + model for a market-making edge created by Hyperliquid's transaction-ordering rules.**

> 🔴 **Live demo → https://site-hazel-two-96.vercel.app**
> *(serverless static site — your browser calls the public Hyperliquid API directly; read-only, no backend, no keys, no auth)*

Hyperliquid sequences each block by `effective_time = arrival_time + f(action, priority_fee)`, where `f` is
strictly decreasing in the priority fee and **exactly 0 for cancels**. That one rule means **all cancels are
ordered ahead of all taker orders, and no taker can buy priority over a cancel at any price** — handing a
resting market-maker a free, un-outbiddable *last look*. This repo measures where that matters most, models
the edge, and ships an interactive dashboard.

---

## What's here

| | |
|---|---|
| 🖥️ **[Live dashboard](https://site-hazel-two-96.vercel.app)** | PFI toxicity ranking across all 452 perp markets, the reference-clock cross-section, and an interactive maker last-look calculator — live from the HL API. |
| 📈 **[STRATEGY.md](STRATEGY.md)** | The money model: where P&L comes from, the flow-based equation, the decisive fee reality (Growth-Mode 0.3 bp vs standard 3 bp maker fee), and base/good/best scenarios grounded in measured data. |
| 🧮 **[MODEL.md](MODEL.md)** | The formal model: the exact `s* = αqL/(1−α+αq)` derivation, `q_hc ≪ q_lat`, the "free priority dominates purchasable priority" proposition, the SKHX calibration, and a **"what to attack"** section. |
| 📄 **[hyperliquid_priority_fees_tier1.md](hyperliquid_priority_fees_tier1.md)** | The research memo: mechanism, the three Tier-1 ideas, a Solana competitive-design read, and what's measured vs. needs-data. |
| 🧑‍🔬 **[REVIEW.md](REVIEW.md)** | 15-minute reviewer walkthrough (built for a quant reading it cold). |
| ✅ **Tests** | `test_hl.py` (Python, 56) + `test_site.js` (JS port, 51), cross-checked against the same fixtures. |

## The idea in five sentences

1. Cancels are sequenced ahead of every taker and can't be out-bid (the `f = 0` rule) — a maker gets a free protocol-level last look; its only race is observe→cancel-before-the-block-boundary, not a fee war.
2. In a competitive (Glosten–Milgrom) frame the break-even half-spread is `s* = αqL`; the venue only changes `q` (pickoff probability), so a protected maker quotes **tighter / larger for the same adverse-selection budget**.
3. The edge is biggest exactly where toxicity (`α·L`) is biggest: **tokenized-RWA perps whose reference cash market is closed** (stale oracle → big mark/oracle dislocation).
4. The durable alpha is **fair-value pricing** (cancel-first is available to everyone, so it's a pricing race, not a speed race) — and the edge and the un-hedgeable closed-hours inventory risk are the *same phenomenon*.
5. Honest scope: **latency-sequenced stat-arb with adverse selection, not risk-free arb**; viable only in **Growth-Mode** HIP-3 markets at a **top maker-fee tier**, and PnL is **volume × a thin (sub-bp) edge**.

## Quickstart

The deployed site needs nothing. To run locally (both pull live from the public HL API, no auth):

```bash
# A) Serverless static site (what's deployed) — pure browser JS
cd site && python3 -m http.server 8089          # → http://localhost:8089

# B) Python reference + local server (same logic, server-side)
python3 serve.py                                  # → http://localhost:8787
python3 hl_data.py                                # CLI: prints the live PFI ranking
```

## Tests

```bash
python3 test_hl.py        # Python reference impl — 56 tests
node    test_site.js      # browser port (site/hl-core.js) — 51 tests
#   add --no-live to either to skip the live-API smoke test
```
Covers: reference-market classification, **DST-correct session clock** (incl. winter-EST cases naive UTC
windows got wrong), PFI monotonicity, the clock-factor lift, true median, realized-vol math,
malformed-input robustness, path-traversal guard, and the maker model. The two implementations are
cross-checked against identical fixtures, so the browser port is provably equivalent to the Python.

## How the demo works

- **Data:** the public Hyperliquid info API (`POST https://api.hyperliquid.xyz/info`) — `perpDexs`,
  `metaAndAssetCtxs`, `l2Book`, `candleSnapshot`. CORS is open (`access-control-allow-origin: *`), so the
  static site calls it straight from the browser.
- **PFI** = `dislocation(bps) × log10(1 + vol_24h/$100k) × clock_factor` (`1.6` if the reference cash market
  is closed). A ranking heuristic for where immediacy is most valuable — **not** a price of immediacy.
- **Reference open/closed** is a DST-correct session heuristic per market's home exchange timezone.

## Repo structure

```
site/                 deployed static site (serverless, no backend)
  index.html          dashboard UI
  hl-core.js          data + PFI + DST-clock logic, ported to browser JS (dual browser/Node)
  vendor/             Chart.js (vendored — no CDN dependency)
hl_data.py            Python reference implementation (same formulas)
serve.py              local server + cached JSON API (path-traversal-guarded, bounded cache)
test_hl.py            Python test suite (56)
test_site.js          JS-port test suite (51)
README.md             ← you are here
REVIEW.md             reviewer walkthrough
STRATEGY.md           P&L model + execution requirements
MODEL.md              formal model + "what to attack"
hyperliquid_priority_fees_tier1.md   research memo
PROGRESS.md           build log / decisions / adversarial-review trail
```

## Status & disclaimer

| Claim | Status |
|---|---|
| Cancels beat all takers; maker last-look is free & un-outbiddable | **Proven** (docs) |
| Dislocation concentrates in reference-clock-closed RWA names | **Measured** (live cross-section) |
| Makers quote tighter than naive adverse selection allows | **Consistent-with**, not causal |
| Tighter spread for same toxic budget (`s*=αqL`, `q_hc<q_lat`) | **Modeled**; `α`, `q` not fitted |
| 8 bp priority cap is frequently *binding* | **Inferred**; needs the actual `p` distribution |

The exact priority `p` paid per order isn't in the public API — confirming "is the cap binding" and turning
the cross-section into a true signal needs an indexed warehouse (Allium/Dune) or raw L1 node data.
**Research/illustrative model, assumptions-dependent. Not investment advice.**
