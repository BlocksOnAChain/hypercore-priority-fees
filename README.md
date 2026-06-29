# HyperCore Priority-Fee Opportunity Monitor

**A live research demo + model for a market-making edge created by Hyperliquid's transaction-ordering rules — extended with a full cross-venue arb & sniper system spanning Hyperliquid and Solana perpetuals.**

> 🔴 **Live demo → https://site-hazel-two-96.vercel.app**
> *(serverless static site — your browser calls the public Hyperliquid API directly; read-only, no backend, no keys, no auth)*

Hyperliquid sequences each block by `effective_time = arrival_time + f(action, priority_fee)`, where `f` is
strictly decreasing in the priority fee and **exactly 0 for cancels**. That one rule means **all cancels are
ordered ahead of all taker orders, and no taker can buy priority over a cancel at any price** — handing a
resting market-maker a free, un-outbiddable *last look*. This repo measures where that matters most, models
the edge, ships an interactive dashboard, and now includes a live multi-venue arb + sniper scanner across
Hyperliquid, Drift Protocol, Phoenix DEX, Mango Markets, and Pyth.

---

## What's here

### Core research (original)

| | |
|---|---|
| 🖥️ **[Live dashboard](https://site-hazel-two-96.vercel.app)** | PFI toxicity ranking across all 452 perp markets, the reference-clock cross-section, and an interactive maker last-look calculator — live from the HL API. |
| 📈 **[STRATEGY.md](STRATEGY.md)** | The money model: where P&L comes from, the flow-based equation, the decisive fee reality (Growth-Mode 0.3 bp vs standard 3 bp maker fee), and base/good/best scenarios grounded in measured data. |
| 🧮 **[MODEL.md](MODEL.md)** | The formal model: the exact `s* = αqL/(1−α+αq)` derivation, `q_hc ≪ q_lat`, the "free priority dominates purchasable priority" proposition, the SKHX calibration, and a **"what to attack"** section. |
| 📄 **[hyperliquid_priority_fees_tier1.md](hyperliquid_priority_fees_tier1.md)** | The research memo: mechanism, the three Tier-1 ideas, a Solana competitive-design read, and what's measured vs. needs-data. |
| 🧑‍🔬 **[REVIEW.md](REVIEW.md)** | 15-minute reviewer walkthrough (built for a quant reading it cold). |

### Cross-venue arb & sniper system (new)

| | |
|---|---|
| 📊 **[BACKTEST_FINDINGS.md](BACKTEST_FINDINGS.md)** | Deep-dive analysis of a 90-day synthetic backtest across 12 assets. P&L, Sharpe, fee sensitivity, RWA vs crypto split, timeout trade problem, ranked deployment roadmap. |
| 🌐 **[CROSS_CHAIN_PROPOSAL.md](CROSS_CHAIN_PROPOSAL.md)** | Venue landscape (HL + Drift = Tier 1, Phoenix spot + Mango = Tier 2), four cross-chain arb strategies with fee budgets, bridge viability analysis, capital allocation plan, month-by-month execution roadmap. |

---

## The idea in five sentences

1. Cancels are sequenced ahead of every taker and can't be out-bid (the `f = 0` rule) — a maker gets a free protocol-level last look; its only race is observe→cancel-before-the-block-boundary, not a fee war.
2. In a competitive (Glosten–Milgrom) frame the break-even half-spread is `s* = αqL`; the venue only changes `q` (pickoff probability), so a protected maker quotes **tighter / larger for the same adverse-selection budget**.
3. The edge is biggest exactly where toxicity (`α·L`) is biggest: **tokenized-RWA perps whose reference cash market is closed** (stale oracle → big mark/oracle dislocation).
4. The durable alpha is **fair-value pricing** (cancel-first is available to everyone, so it's a pricing race, not a speed race) — and the edge and the un-hedgeable closed-hours inventory risk are the *same phenomenon*.
5. Honest scope: **latency-sequenced stat-arb with adverse selection, not risk-free arb**; viable only in **Growth-Mode** HIP-3 markets at a **top maker-fee tier**, and PnL is **volume × a thin (sub-bp) edge**.

---

## Quickstart

### Live demo (no setup)
```bash
# Serverless static site — pure browser JS, calls HL API directly
cd site && python3 -m http.server 8089          # → http://localhost:8089

# Python reference + local server
python3 serve.py                                  # → http://localhost:8787
python3 hl_data.py                                # CLI: prints the live PFI ranking
```

### Arb & sniper scanner (mock mode — works anywhere)
```bash
# Single scan with fixture data (no API keys, works in cloud/sandbox)
python3 multi_venue_monitor.py --mock --once

# Continuous loop, 30-second interval
python3 multi_venue_monitor.py --mock --interval 30

# Show signal log tail
python3 multi_venue_monitor.py --tail 30

# 24h rolling statistics
python3 multi_venue_monitor.py --stats
```

### Arb & sniper scanner (live APIs — run on your local machine)
```bash
pip install hyperliquid-python-sdk driftpy solana websocket-client

# All venues: HL + Drift + Phoenix + Mango + Pyth
python3 multi_venue_monitor.py --interval 60

# HL + Drift + Pyth only, with WebSocket feeds for lower latency
python3 multi_venue_monitor.py --venues hl drift pyth --interval 30 --ws

# After logging for a while, check which signals repeat
python3 multi_venue_monitor.py --stats --stats-window 336   # 2-week window
```

### Backtesting
```bash
# Generate 90-day synthetic dataset (no API needed)
python3 data_collector.py --synthetic --days 90

# Run all strategies with parameter sweep
python3 backtest_engine.py --strategy all --sweep

# Collect real candle data (requires HL API access)
python3 data_collector.py --collect --days 30 --coins SKHX SMSN NVDA AAPL
```

---

## Tests

```bash
python3 test_hl.py        # Original HL reference impl — 56 tests
node    test_site.js      # Browser port (site/hl-core.js) — 51 tests
python3 test_arb.py       # Arb + sniper signal engine — 48 tests
#   add --no-live to test_hl.py / test_site.js to skip the live-API smoke test
```

---

## Repo structure

```
── Core research ─────────────────────────────────────────────────────────────
site/                       deployed static site (serverless, no backend)
  index.html                dashboard UI
  hl-core.js                data + PFI + DST-clock logic (dual browser/Node)
  vendor/                   Chart.js (vendored)
hl_data.py                  Python reference: PFI, market rows, funding
serve.py                    local server + cached JSON API
test_hl.py                  Python test suite (56 tests)
test_site.js                JS-port test suite (51 tests)
STRATEGY.md                 P&L model + execution requirements
MODEL.md                    formal model + "what to attack"
REVIEW.md                   reviewer walkthrough
hyperliquid_priority_fees_tier1.md   research memo

── Cross-venue arb & sniper system ──────────────────────────────────────────
multi_venue_monitor.py      Real-time 5-venue monitor (HL · Drift · Phoenix · Mango · Pyth)
loop_runner.py              Simpler looping scanner (HL + Drift, --mock support)
arb_scanner.py              Cross-venue arb signal engine: mark-spread, funding-rate, dislocation
sniper_signals.py           Sniper detectors: dislocation, funding cliff, momentum, liq. zone
solana_data.py              Drift Protocol + Pyth Hermes data fetchers
mock_data.py                Fixture data for offline testing (31 HL + 14 Drift markets)
arb_dashboard.html          Browser dashboard reading arb_report.json (auto-refresh)

── Backtesting & data ───────────────────────────────────────────────────────
backtest_engine.py          3-strategy backtester: dislocation, funding arb, mark-spread arb
data_collector.py           HL candle collector + synthetic GBM dataset generator
data/
  synthetic_90d_1h.jsonl    90-day × 12-asset synthetic dataset (25,920 rows)
  backtest_results.json     Backtest output (capped at 100 trades/strategy for size)
  monitor_logs/
    signals.jsonl           Live signal log (appended by multi_venue_monitor.py)

── Tests ────────────────────────────────────────────────────────────────────
test_arb.py                 Arb + sniper signal engine tests (48 tests)

── Research docs ────────────────────────────────────────────────────────────
BACKTEST_FINDINGS.md        Deep-dive 90-day backtest analysis
CROSS_CHAIN_PROPOSAL.md     Cross-chain arb proposal: HL × Solana
README.md                   ← you are here
```

---

## Backtest summary (90-day synthetic, 12 assets)

| Strategy | Trades | Total P&L | Avg bps/trade | Sharpe | Notes |
|---|---|---|---|---|---|
| Dislocation (30 bps entry) | 2,963 | −$13,461 | −4.54 | −0.02 | Needs tuning |
| Dislocation (60 bps entry, optimised) | 1,553 | +$4,705 | +3.03 | 0.01 | Growth Mode fees required |
| Funding-Rate Arb | 1,611 | +$53,517 | +33.22 | 2.30 | Synthetic bias — see BACKTEST_FINDINGS.md |
| Mark-Spread Arb (HL vs Drift) | 2,352 | +$46,800 | +19.90 | 2.43 | Viable at 50+ bps, RWA names only |

**Key findings:**
- Growth Mode fees (0.3 bps maker) are a hard gating condition — at standard 3 bps the dislocation strategy goes dead.
- RWA perps generate 3.4× more edge per trade than crypto (SKHX, NVDA, AAPL are the top names).
- Funding arb and mark-spread 100%/97.6% win rates are synthetic artefacts — realistic win rates are 60–75%.
- Full analysis with monthly P&L, fee sensitivity, and ranked deployment roadmap: **[BACKTEST_FINDINGS.md](BACKTEST_FINDINGS.md)**

---

## Multi-venue monitor signals

`multi_venue_monitor.py` detects five signal classes simultaneously:

| Signal | Source | Edge estimate |
|---|---|---|
| **Mark-spread arb** | HL mark vs Drift mark divergence | price gap × 40–70% capture |
| **Funding-rate arb** | HL funding vs Drift funding divergence | funding gap × holding period |
| **Dislocation sniper** | HL mark vs oracle gap on RWA names | dislocation × 40% |
| **Funding cliff** | Extreme funding → crowded positions → reversion | funding rate × 60% |
| **Phoenix basis arb** | HL perpetual premium vs Phoenix DEX spot | basis bps after fees |
| **Liquidation zone** | High OI/vol ratio + skewed funding | composite score |
| **Cross-venue momentum** | Price leader/lagger between HL and Drift | gap × 50% |

Signals are logged to `data/monitor_logs/signals.jsonl` and the latest scan state is written to `arb_report.json` (consumed by `arb_dashboard.html`).

---

## Status & disclaimer

| Claim | Status |
|---|---|
| Cancels beat all takers; maker last-look is free & un-outbiddable | **Proven** (docs) |
| Dislocation concentrates in reference-clock-closed RWA names | **Measured** (live cross-section) |
| Makers quote tighter than naive adverse selection allows | **Consistent-with**, not causal |
| Tighter spread for same toxic budget (`s*=αqL`, `q_hc<q_lat`) | **Modeled**; `α`, `q` not fitted |
| Backtest P&L numbers | **Synthetic-data simulation** — see caveats in BACKTEST_FINDINGS.md |

The exact priority `p` paid per order isn't in the public API. The backtest uses synthetic GBM paths with calibrated parameters — not real market data. Funding arb and mark-spread win rates are inflated by synthetic data construction. All API calls are read-only; no funds or private keys are used anywhere in this codebase.

**Research/illustrative model, assumptions-dependent. Not investment advice.**
