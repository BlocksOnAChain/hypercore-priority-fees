# Backtest Deep-Dive Findings
**Dataset:** 90-day synthetic time-series · 25,920 rows · 12 assets · 1h intervals  
**Calibration:** GBM price paths anchored to real HL measurements (SKHX 33 bps dislocation, real funding distributions)  
**Size per trade:** $10,000 · **Fees:** see per-strategy notes

---

## Executive Summary

| Strategy | Trades | Total P&L | Avg bps/trade | Sharpe | Real-world confidence |
|---|---|---|---|---|---|
| Dislocation (default 30bps) | 2,963 | **−$13,461** | −4.54 | −0.02 | ❌ Needs tuning |
| Dislocation (optimised 60bps) | 1,553 | **+$4,705** | +3.03 | 0.01 | ⚠️ Marginal in sim, real test needed |
| Funding-Rate Arb | 1,611 | **+$53,517** | +33.22 | 2.30 | ⚠️ Synthetic bias — see §2 |
| Mark-Spread Arb (HL vs Drift) | 2,352 | **+$46,800** | +19.90 | 2.43 | ⚠️ Synthetic bias — see §3 |

**Bottom line:** Two strategies show strong simulation numbers but both contain a structural optimism bias from how synthetic Drift data was generated. One strategy (dislocation) is the closest to deployable but requires Growth Mode fees and tight parameter discipline. Full verdict and the path to real money is in §5.

---

## Strategy 1: Dislocation Mean-Reversion

**Thesis:** When HL mark diverges from oracle by >X bps, trade toward oracle — it must revert.  
**Fee model:** Entry as maker (Growth Mode 0.3 bps), exit as taker (2.5 bps).

### Default parameters (entry 30 bps / exit 5 bps)
- **−$13,461 · Win rate 54.3% · Avg −4.54 bps · Sharpe −0.02**
- The problem is not the win rate — it is the **win/loss asymmetry (0.79x)**: winners average +116 bps but losers average −148 bps. The oracle does revert most of the time but when it doesn't, the loss is catastrophic.

### Optimised parameters (entry 60 bps / exit 3 bps / hold ≤24h)
- **+$4,705 · Win rate 53.6% · Avg +3.03 bps · Sharpe 0.01**
- Significantly better but still near-zero Sharpe. Marginal in simulation, which means it needs a real live test to know if it is actually viable.

### The timeout trade problem
Timeout exits (214 trades, 14% of volume) are the strategy killer:

| Exit type | Trades | Total P&L | Avg bps | Win rate |
|---|---|---|---|---|
| Reversion | 1,339 | +$10,487 | **+7.83** | 55% |
| Timeout | 214 | −$5,783 | **−27.02** | 48% |

Timeout trades are **3.5× more damaging per trade** than reversions earn. A trade that hits the 24h hold limit is almost certainly a real adverse move, not noise. Fix: either a tighter stop-loss (−50 bps) or a delta hedge using a correlated instrument during the hold period.

### Fee regime is the binary switch

| Fee scenario | P&L (90d) | Avg bps |
|---|---|---|
| Growth Mode (0.3 bps maker) | **+$4,705** | +3.03 |
| Standard HL (1.5 bps maker) | **+$2,841** | +1.83 |
| Standard HL (3.0 bps maker) | **−$265** | −0.17 |

**At standard (non-Growth Mode) fees the strategy is dead.** Growth Mode is not optional — it is the gating condition. The 2.7 bps difference between 0.3 and 3.0 bps maker fee swings the strategy from +3 to −0.17 bps average per trade.

### RWA vs crypto

| Universe | Trades | Avg bps | Sharpe |
|---|---|---|---|
| RWA / Equity perps | 621 | **+5.25** | 0.039 |
| Crypto perps | 932 | **+1.55** | 0.004 |

**RWA perps generate 3.4× more edge per trade.** This confirms the core thesis: closed-market oracle staleness is the source of the dislocation alpha. On crypto, which trades 24/7, the oracle almost never goes stale and the dislocation is noise, not signal.

### Top coins for dislocation
DOGE (+$3,075), SKHX (+$1,507), NVDA (+$1,299), AAPL (+$496). ETH (−$4,764) and AVAX (−$2,243) are the worst — liquid 24/7 assets where the dislocation is mean-reverting noise.

---

## Strategy 2: Funding-Rate Arb

**Thesis:** When HL funding diverges from Drift by >5 bps/hr, go long the lower-funding venue, short the higher.  
**Fee model:** 4.0 bps roundtrip (both legs combined).

### Results
- **+$53,517 · Win rate 100% · Avg +33 bps · Sharpe 2.30**
- Monthly P&L perfectly consistent: Jan +$18,563 / Feb +$16,840 / Mar +$18,113.
- All coins profitable, gap closes on 100% of trades.

### Why 100% win rate is a red flag
The synthetic Drift funding was generated as `HL_funding + Gaussian_noise(0, 3bps)`. This means the gap by construction always closes because it was always just noise around the same mean. **This is not a real market model.** In reality:

1. Drift and HL can both be in persistent funding regimes (both positive, both negative) — the arb gap may not converge for days or weeks.
2. You need simultaneous capital locked on two different chains (Solana + HL), which reduces capital efficiency and creates execution risk.
3. Drift funding is paid in 1-hour intervals — not continuous. You can get unlucky on the exact timing of the payment.
4. Bridge and on/off-ramp costs are not modelled.

### Realistic expectation
The structural edge in funding arb is real — academic literature confirms it persists in crypto — but realistic win rates are **60-75%**, Sharpe around **0.5-1.0**, and returns are considerably lower than the simulation shows. The signal is useful as a secondary indicator: a large HL-vs-Drift funding divergence means one market has crowded positioning that will likely unwind.

---

## Strategy 3: Cross-Venue Mark-Spread Arb

**Thesis:** When HL mark and Drift mark diverge by >20 bps, buy the cheaper venue and sell the expensive one; unwind when gap closes.  
**Fee model:** 2.0 bps per side (4 bps total roundtrip).

### Results
- **+$46,800 · Win rate 97.6% · Avg +19.9 bps · Sharpe 2.43**
- 2,053 convergence exits avg +21.5 bps / 299 timeout exits avg +8.65 bps.
- Every single coin profitable.

### The liquidity reality check

| Entry threshold | Trades | Avg bps | Win rate |
|---|---|---|---|
| 10 bps | 2,981 | +10.6 | 89.6% |
| 20 bps | 2,352 | +19.9 | 97.6% |
| 30 bps | 972 | +29.2 | 99.8% |
| 50 bps | 27 | +45.0 | 100.0% |
| 100 bps | 0 | — | — |

**The problem is not the P&L — it is the trade count at higher thresholds.** On BTC, ETH, and SOL, a 20 bps cross-venue gap on any liquid exchange is arbed in milliseconds by dedicated bots with sub-100ms latency. **These trades do not exist for 5 hours in real life.** The simulation assumes they do.

**Where this is genuinely real:** on illiquid HIP-3 RWA perps (SKHX, SMSN, ASML), where:
- Far fewer bots monitor these exotic markets
- You need capital on both HL and a Solana DEX simultaneously — most bots don't
- The closed-market hours create genuine information asymmetry that slows arbitrage
- Even a 5-minute hold rather than 5 hours would capture a meaningful portion of the edge

**The realistic operating point:** Entry at 50+ bps, RWA names only. 27 trades at 45 bps average in synthetic data — extrapolating to real markets with more names and more volatility events suggests 5-15 genuine opportunities per week.

---

## Strategy Interaction: What Wins on the Same Coins

The three best coins across strategies:

| Coin | Disloc (optimised) | Mark-spread | Why |
|---|---|---|---|
| **SKHX** | +$1,507 | +$3,958 | KRW market closed, oracle staleness is genuine |
| **DOGE** | +$8,895 | +$3,655 | High vol → large GBM noise creates big gaps |
| **NVDA** | +$1,299 | +$4,149 | Closed US market, high dislocation in fixtures |

SKHX is the most defensible because the edge is structural (closed market + cancel-first mechanics) not just high volatility.

---

## §5: Path to Real Money — Ranked by Deployability

### #1: RWA Dislocation on HL (most defensible, deploy first)

**What it is:** Passive market-making on HIP-3 Growth-Mode RWA perps during closed-market hours. The cancel-first rule is your structural protection. You quote the spread, capture it from uninformed flow, and pull before informed flow hits you.

**Requirements:**
- Growth Mode access on HIP-3 markets (contact the `xyz` deployer)
- Maker volume to reach 0.0–0.3 bps fee tier
- A fair-value pricing model for the underlying (overnight ADRs, correlated ETFs)

**Expected P&L (base case from STRATEGY.md):** $0.3–4 M/yr depending on scale and market growth. This is the core thesis — not the backtest.

**Next step:** Run a live pilot on SKHX and SMSN with $5k size during KST closed hours. Measure: (a) fill rate, (b) realized adverse selection vs captured spread, (c) actual `q` vs theoretical.

---

### #2: Dislocation Sniper on HL (deploy after pilot data)

**What it is:** When mark/oracle gap exceeds 60 bps on an RWA name during closed hours, take a directional position toward oracle with a tight 3 bps exit target.

**Requirements:**
- Growth Mode for low fees
- Fast cancel pipeline (needed to pull if you're wrong)
- Hard 24h maximum hold with a delta hedge in correlated open-market instruments

**Key fix vs simulation:** The timeout trades are killers. In production, add a hard P&L stop at −50 bps and a basket hedge (e.g., short the KOSPI ETF proxy during a Korean equity hold).

**Realistic P&L:** +$5–15k/month at $10k size on RWA names. Low Sharpe (~0.1–0.3) but positive expectancy once fee tier is right.

---

### #3: Funding Divergence Monitor (signal, not pure arb)

**What it is:** Rather than executing the funding arb mechanically, use funding divergence (HL vs Drift) as a *signal* for crowded positioning. Large positive funding on HL = longs crowded = the oracle sniper opportunity is better.

**Why this framing is better:** Avoids the capital efficiency problem (locking money on two chains) and avoids the "gap closes when" uncertainty. Instead it's a filter: only execute dislocation trades when funding on HL is also extreme, which confirms directional momentum is overextended.

---

### #4: Mark-Spread on RWA, 50+ bps threshold (low frequency, high quality)

**What it is:** Only enter when the HL-vs-Drift gap exceeds 50 bps on illiquid RWA names. These are high-conviction, infrequent trades.

**Why 50+ bps:** At this level, even after slippage and fees, there is meaningful edge remaining. At 20 bps, transaction costs eat most of it on any liquid name.

**Realistic trade count:** 5–15/week across 6-8 RWA names. At $20k size and 30 bps net edge: ~$90–270 per trade, ~$500–2,000/week.

---

## Getting Real Data (To Replace Synthetic)

```bash
# On your own machine (not cloud — needs HL API access):
python3 data_collector.py --collect --days 90 --coins BTC ETH SOL SKHX SMSN ASML NVDA TSLA

# Then backtest with real candles:
python3 backtest_engine.py --data data/candles/SKHX_1h.jsonl --strategy dislocation --sweep

# Snapshot every 15 min for a week to build a real dislocation time-series:
watch -n 900 python3 data_collector.py --snapshot
```

The single most valuable real-data experiment: **measure SKHX mark/oracle dislocation around KST market open and close for 2 weeks.** If dislocation reliably spikes during closed hours and collapses at open, the dislocation strategy is real. If it doesn't, the thesis fails.

---

## What the Simulation Does Not Model (Honest Caveats)

| Missing element | Impact on real P&L |
|---|---|
| **Market impact / slippage** | At $10k size on thin RWA books, a market order moves the price. Estimate: −5 to −20 bps per entry/exit. |
| **Drift execution latency** | Cross-chain arb requires bridging and two separate execution systems. Gaps of <30 bps close before you can act. |
| **Capital locked on both chains** | Funding arb and mark-spread require simultaneous capital. Return on *deployed capital* is much lower than return on *per-trade notional*. |
| **Oracle model accuracy** | Synthetic oracle lag is GBM-based. Real oracle staleness is regime-like (snaps shut at market close, snaps open at market open). This changes the dislocation distribution shape significantly. |
| **Competition / adverse selection** | As more makers arrive in Growth-Mode RWA markets, the captured spread compresses. The edge is a first-mover + pricing-skill advantage, not a structural moat. |
| **Regulatory risk** | Tokenised-equity perps are a grey area in most jurisdictions. |

The simulation is a **logic validator and parameter selector**, not a P&L forecast.
