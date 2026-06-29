# Cross-Chain Arb & Sniper Proposal: Hyperliquid × Solana Perps

> **Note on "Drip":** There is no perpetuals venue called "Drip" on Solana. The main venues are **Drift Protocol** (largest Solana perps, what you likely mean), **Phoenix DEX** (spot CLOB only — no perps, but used for basis arb), **Zeta Markets** (perps + options), and **Mango Markets** (perps + lending). This proposal covers all of them in priority order.

---

## 1. Venue Stack — What You're Working With

### Tier 1: Trade Here (primary execution venues)

| Venue | Type | Why It Matters for Arb |
|---|---|---|
| **Hyperliquid (HL)** | Perps — all assets | Cancel-first rule = structural maker edge; HIP-3 RWA names = closed-market dislocation alpha |
| **Drift Protocol** | Perps — 50+ pairs | Largest Solana perps. Shares Pyth oracle with HL → same oracle, different prices = arb gap |

### Tier 2: Signal + Secondary Execution

| Venue | Type | Role in Strategy |
|---|---|---|
| **Phoenix DEX** | Spot CLOB only | Best on-chain spot price discovery for BTC/ETH/SOL. Use as the "true price" reference for perp/spot basis arb against HL |
| **Mango Markets** | Perps + lending | Secondary funding rate signal. Cross-margined with spot → capital efficiency. Can borrow USDC to deploy on both sides |

### Tier 3: Dislocation Targets (check periodically)

| Venue | Type | Why Check |
|---|---|---|
| **Adrena** | Perps (pool-based) | Pool-based AMMs create mark dislocations during volatility spikes — sniper opportunity |
| **Flash Trade** | Perps (pool-based) | Same pool-based mechanics as Adrena — larger gaps on low-liquidity names |
| **Zeta Markets** | Perps + options | Options IV surface tells you the market's implied move → calibrate your dislocation threshold |

### Why Phoenix is spot-only and still matters

Phoenix (by Ellipsis Labs) is a pure on-chain CLOB for spot markets — the closest thing Solana has to a Nasdaq-style order book. It doesn't have perpetuals. But it matters because:
- The Phoenix BTC/USDC mid-price is the most accurate "true spot" price on Solana
- HL BTC-PERP carries a premium above spot (the "perp basis") that is normally 5-30 bps
- **When HL BTC-PERP premium above Phoenix spot exceeds the historical average, the trade is: short HL BTC-PERP, long Phoenix BTC spot** — you're shorting the overpriced derivative and buying the underlying. The premium mean-reverts and you collect it plus any positive funding from the short side

---

## 2. The Three Cross-Chain Arb Strategies (Ranked)

### Strategy A: Delta-Neutral Funding Rate Arb (HL × Drift)
**Ranking: #1 — Start here. No bridging risk. Capital pre-positioned.**

**The setup:**
- Both chains pay funding every hour
- When HL funding significantly differs from Drift funding on the same pair, a delta-neutral position earns the spread
- Pre-position capital on both chains before the trade. No bridging happens mid-trade

```
Signal: HL_funding_bps_hr − Drift_funding_bps_hr > 5 bps/hr

Entry:
  If HL pays higher funding (more long congestion on HL):
    → SHORT on HL  (collect funding from longs)
    → LONG  on Drift (pay lower/zero funding)
    → Net earning: (HL_fund − Drift_fund) per hour

  If Drift pays higher funding (more long congestion on Drift):
    → LONG  on HL  (pay lower funding, possibly collect if HL is negative)
    → SHORT on Drift (collect from longs)

Exit: when gap compresses below 2 bps/hr OR after 48h max hold

Fee budget: HL taker ~2.5 bps + Drift taker ~10 bps = ~12.5 bps roundtrip total
Breakeven: gap must persist for at least 12.5 / 5 = 2.5 hours to cover fees
```

**Why this is the safest cross-chain arb:**
- No bridging mid-trade (capital is already on both chains)
- Delta-neutral: if BTC moves +10%, your HL short loses exactly what your Drift long gains
- Risk is only the funding convergence timing, not price direction

**Expected frequency:** 5-20 signals/month per pair across BTC, ETH, SOL  
**Expected edge:** 8-35 bps per trade after fees (depending on gap size and hold time)  
**Required capital:** $10k on HL + $10k on Drift = $20k deployed, $40k total (2× for margin safety)

---

### Strategy B: Perp-Spot Basis Arb (HL perp × Phoenix spot)
**Ranking: #2 — Capital efficient, clear signal, manageable execution.**

**The setup:**
Perpetual futures always carry a "basis" (premium) above spot. In normal conditions this basis is +5 to +30 bps. When it spikes above historical average, the perp is expensive relative to spot. The trade: short the overpriced perp, long the underlying spot. When the basis normalises you close both legs and keep the premium.

```
Signal: (HL_mark − Phoenix_spot) / Phoenix_spot × 10000 > basis_threshold_bps

Example with BTC:
  Phoenix spot BTC:  $105,400
  HL BTC-PERP mark:  $105,830   ← 40 bps premium
  Historical basis:  ~15 bps
  Gap above normal:  25 bps

Entry:
  → SHORT HL BTC-PERP (sells the expensive derivative)
  → LONG  Phoenix BTC/USDC spot (buys the cheap underlying)
  Net position: delta-neutral + long the basis convergence

While holding (if basis stays wide):
  → HL short side collects funding (when mark > oracle, longs pay shorts)
  → Phoenix long needs no carrying cost (spot, no funding)

Exit:
  → When (HL_mark − Phoenix_spot) returns to historical basis (±5 bps)
  → Or after 72h maximum hold

Fee budget: HL taker ~2.5 bps + Phoenix taker ~3 bps = ~5.5 bps roundtrip
Minimum gap: 15 bps to be worth entering
```

**Critical risk:** If you hold the Phoenix spot through a large BTC price move, the spot leg is properly hedged by the HL short. The only exposure is if the HL short gets margin-called before the basis closes — avoid this with 3-5× margin buffer.

**Expected frequency:** 2-8 signals/month per pair (basis spikes are event-driven)  
**Expected edge:** 15-50 bps per trade, held for hours to days  
**Required capital:** $10k on HL (for perp margin) + $10k on Solana (for Phoenix spot) = $20k, 2 chains

---

### Strategy C: Oracle Dislocation Sniper (per-chain, independent)
**Ranking: #3 — Highest edge per trade, requires fastest execution, no cross-chain needed.**

**The setup:**
Both HL and Drift use Pyth as their oracle. When a venue's mark price deviates significantly from Pyth's feed, it will revert. On HL, the cancel-first rule lets you take this position as a maker (protected). On Drift, you take it as a taker.

```
On HL (use cancel-first protection):
  Signal: |HL_mark − Pyth_price| / Pyth_price × 10000 > 60 bps
  Trade:  Quote passively TOWARD Pyth price, cancel if adverse fill risk
  Exit:   When mark reverts to within 3 bps of Pyth oracle
  Edge:   60-400 bps per trade (RWA names can spike much higher)
  Fee:    0.3 bps maker (Growth Mode only)

On Drift (taker entry, higher cost):
  Signal: |Drift_mark − Pyth_price| / Pyth_price × 10000 > 100 bps
  Trade:  Market order toward Pyth price
  Exit:   When mark reverts
  Edge:   100-300 bps per trade (higher threshold needed to cover 10bps taker fees)
  Fee:    10 bps taker
```

**The critical difference:** On HL you can maker-quote into the dislocation (cancel-first protects you). On Drift you take a market order. This changes the fee calculus completely — HL is far more capital-efficient for this strategy.

**HL is objectively better for dislocation sniping.** The only reason to use Drift here is if a Solana-native event (e.g., a Solana validator outage, SOL-specific news) causes a dislocation that hits Drift before HL has repriced it.

---

### Strategy D: Pool-Based AMM Sniper (Adrena / Flash Trade)
**Ranking: #4 — Event-driven, high reward, requires speed.**

Pool-based perps (Adrena, Flash Trade) set their mark price as `Pyth_price × (1 ± pool_imbalance_factor)`. During large one-sided flow events, the pool imbalance spikes and the mark deviates from Pyth by 50-200 bps before the pool rebalances via funding. This is a time-limited sniper window.

```
Signal:  |AMM_mark − Pyth| > 100 bps  AND  abs(pool_utilization) > 70%
Entry:   Market order in the direction of Pyth (fade the AMM)
Exit:    When AMM mark reverts (< 20 bps from Pyth), typically within 15-60 min
Edge:    80-180 bps after fees
Risk:    If Pyth itself moves in the same direction you're fading, you lose
```

---

## 3. The Cross-Chain Arb Bridge Question

**Can you arb opportunities that require moving money between chains mid-trade?**

Short answer: **Only for slow strategies (hours-to-days), not fast ones (minutes).**

| Bridge | Transfer time | Cost | Safety |
|---|---|---|---|
| **Wormhole** | 15-30 min for large amounts | ~0.001 SOL + gas | High (most audited) |
| **deBridge** | 5-10 min | 0.1% fee | High |
| **Allbridge** | 2-5 min | ~0.3% fee | Medium |
| **Mayan Finance** | 1-3 min | 0.05-0.2% fee | Medium |

**The verdict:** For funding arb (hold measured in hours), you can bridge to rebalance capital after the trade closes — not during. For mark-spread arb (convergence in minutes), bridging is never fast enough. **Pre-position capital on both chains before you start.**

### Capital allocation for pre-positioned cross-chain operation

```
HL wallet:       $30k USDC  (margin for perp positions)
  - 20k as margin for up to 3 concurrent positions at 3× leverage
  - 10k as safety buffer / margin for rebalancing

Solana wallet:   $30k
  - 15k USDC on Drift (margin for perp positions)
  - 10k USDC on Phoenix (spot positions)
  - 5k SOL/USDC for gas + small AMM positions

Total deployed:  $60k
Expected monthly at this size (conservative): $600-2,500
Expected monthly at this size (good month):   $2,000-8,000
```

---

## 4. Technology Stack — Full Build

### Data layer (already built or extend)
```python
# Multi-venue real-time feed
hl_data.py         → HL REST + WebSocket
solana_data.py     → Drift DLOB REST (extend to WebSocket)
phoenix_data.py    → Phoenix orderbook REST + WebSocket (NEW)
pyth_feed.py       → Pyth Hermes WebSocket for 50ms oracle updates (NEW)
mango_data.py      → Mango REST for funding rates (NEW)
```

### Signal layer (built)
```python
arb_scanner.py     → mark-spread, funding, dislocation signals
sniper_signals.py  → four sniper patterns
loop_runner.py     → background loop with --interval 30
```

### Execution layer (to build — needs wallets + keys)
```python
hl_executor.py     → hyperliquid-python-sdk orders
drift_executor.py  → driftpy orders (Solana keypair needed)
phoenix_executor.py→ phoenix-sdk spot orders
risk_manager.py    → position limits, PnL stops, exposure aggregator
```

### Infrastructure
```bash
# Python packages needed
pip install hyperliquid-python-sdk   # HL execution
pip install driftpy                  # Drift execution
pip install solders solana           # Solana base layer
pip install anchorpy                 # Solana program interface
pip install websockets aiohttp       # async feeds
pip install pyth-client              # Pyth prices
```

---

## 5. Execution Plan — Month by Month

### Month 1: Data and Signal Validation (no capital at risk)

**Week 1-2: Spin up the multi-venue monitor**
```bash
python3 multi_venue_monitor.py --venues hl drift pyth --interval 60
```
Run this on your local machine (the APIs work there). Log everything to JSONL. After 2 weeks you will know:
- How often funding gaps appear and how long they last
- What the real mark-spread distribution looks like on BTC, ETH, SOL
- Whether the Phoenix-HL basis is predictable

**Week 3-4: Calibrate signal thresholds against real data**
- Run backtest_engine.py against your collected real data (not synthetic)
- Validate which thresholds generate signal quality matching synthetic predictions
- Identify the 3-5 pairs with highest signal frequency

**Deliverable:** A spreadsheet of real opportunity counts per strategy per week. This is your go/no-go gate before deploying capital.

---

### Month 2: Paper Trading (simulated P&L, real signals)

Build a paper trading layer on top of the execution layer:
```bash
python3 paper_trader.py --strategy funding_arb --size 10000
```
Log every "would-have-filled" trade with slippage assumptions:
- Funding arb: assume 5 bps slippage per side
- Basis arb: assume 3 bps on HL + 2 bps on Phoenix
- Dislocation sniper: assume 0.3 bps maker (HL) + 5 bps on Drift

Target: 4 weeks of paper P&L that matches within 40% of backtest estimates. If paper P&L is 60%+ below backtest, recalibrate before going live.

---

### Month 3: Live Pilot — Start Small

**Size: $5k per side, max $20k deployed**

Start with exactly ONE strategy on ONE pair:
**→ Funding arb on BTC-PERP (HL × Drift), $5k each side**

Why this first:
- Fully delta-neutral (no directional risk)
- No execution race (funding pays out every hour, not every second)
- Clean P&L attribution (funding income is unambiguous)
- If it goes wrong, max loss is the spread between entry and exit prices (not a directional move)

After 2 weeks on BTC-PERP, add ETH-PERP. After another 2 weeks, add SOL-PERP.

Only move to dislocation sniping after you have 4+ weeks of live funding arb data.

---

### Month 4+: Scale and Diversify

| Stage | Capital | Strategies active | Expected monthly |
|---|---|---|---|
| Pilot | $20k | Funding arb BTC/ETH | $100-500 |
| Early | $60k | Funding arb + basis arb | $400-2,000 |
| Growth | $150k | All 4 strategies, 8+ pairs | $1,500-8,000 |
| Scale | $500k | All strategies, RWA names added | $8,000-40,000 |

The RWA dislocation on HL is the highest-edge strategy but requires Growth Mode access and a fair-value pricing model for closed-market names. It should be Layer 2, not Layer 1 — get the funding arb infrastructure right first.

---

## 6. Risk Framework

### Position-level risks

| Risk | Mitigation |
|---|---|
| **One leg fills, other fails** | Reduce size or treat as directional until second leg fills. Never hold a naked large position. |
| **Funding flips sign mid-hold** | Monitor hourly. Auto-close if gap reverses by >3bps. |
| **Margin call on one chain** | Always hold 3× margin buffer. Position limit = 30% of capital per side. |
| **Pyth flash crash / manipulation** | Use 5-candle moving average of Pyth price as oracle reference, not raw tick. Ignore dislocations that appear and vanish within 1 minute. |
| **Smart contract exploit** | Never keep >$50k on any single protocol. Spread across HL, Drift, Phoenix. |

### Execution risks

| Risk | Mitigation |
|---|---|
| **Slippage eats edge** | Only trade when signal > 2× expected slippage. For BTC/ETH: signal >15 bps needed at $10k size. |
| **Latency (second leg fills at worse price)** | Submit both legs simultaneously to their respective chains using async tasks. Accept that cross-chain "simultaneous" is ~500ms apart. |
| **Drift fill failures** | Always check DLOB liquidity before submitting. Reject signal if spread > 5 bps. |

### Capital risks

| Risk | Mitigation |
|---|---|
| **Bridge hack** | Never bridge mid-trade. Only bridge to rebalance capital when flat. Use Wormhole for large amounts (most audited). |
| **Correlated drawdown** | The 4 strategies are not fully independent — all lose in a flash crash. Keep total deployed ≤ 40% of liquid net worth. |
| **Regulatory** | Keep accounts separate. Perps trading on HL is EVM-native. Drift is permissionless Solana. Neither currently KYC-gated. |

---

## 7. What Makes This Defensible Long-Term

Most cross-chain arb gets competed away within weeks when volume grows. The strategies here that survive longer-term:

1. **HL HIP-3 RWA dislocation**: Depends on a structural protocol rule (cancel-first) that all takers face identically. The moat is **pricing skill** (who can price SKHX at 3am Seoul time better), not capital size. This doesn't disappear with more competition — it migrates to better pricing.

2. **Basis arb (HL perp × Phoenix spot)**: Requires capital on two chains simultaneously. Most bots are single-chain. The technical overhead keeps competition thinner.

3. **Funding arb (HL × Drift)**: Most liquid, most competed — but funding rate regimes persist for hours to days, not seconds. This is not a speed race. The moat is having clean monitoring infrastructure and the discipline to hold the position until convergence.

The **least defensible** is pure mark-spread arb on liquid pairs (BTC, ETH, SOL) — those gaps are closed in under a second by professional bots and the simulation is simply wrong about them being available for 5 hours.

---

## 8. Immediate Next Steps

```
[ ] 1. Add your local machine API keys to a .env file (NOT committed to git)
        HL_PRIVATE_KEY=...
        SOLANA_PRIVATE_KEY=...

[ ] 2. Run multi_venue_monitor.py for 2 weeks, log real data

[ ] 3. Run backtest_engine.py --data data/real_collected_data.jsonl
        Compare real vs synthetic results

[ ] 4. Deploy $5k each on HL + Drift, execute funding arb on BTC-PERP

[ ] 5. After 4 weeks of clean funding arb: add basis arb (HL × Phoenix)

[ ] 6. Apply for Growth Mode access on HL HIP-3 markets
        → Required for 0.3 bps maker fee → required for RWA dislocation to be profitable

[ ] 7. Build fair-value model for SKHX (Korean ADR proxy, overnight futures)
        → This is the alpha of the whole system
```
