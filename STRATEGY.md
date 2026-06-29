# How this earns money — the P&L model, best case, and what we must execute

> Illustrative model, assumptions-dependent, **not investment advice.** Every number is tied to an
> explicit assumption you can challenge. Pairs with the spread theory in [MODEL.md](MODEL.md).

## 1. Where the money actually comes from

The strategy is **passive market-making in HyperCore tokenized-RWA perps, concentrated in names whose
reference cash market is closed** — using the protocol's free, un-outbiddable cancel-first rule to quote
into toxic conditions a latency venue would punish.

The subtlety a quant will check first: at *competitive* equilibrium the per-fill break-even spread
`s* = αqL` makes expected profit **zero**. So the alpha is not "collect the spread" — it is two things:

1. **Under-competed markets.** In HIP-3 RWA names (especially closed reference hours, thin books) the
   *quoted* spread sits above what a *protected* maker's true adverse-selection cost is. You capture the
   gap. This gap erodes as sophisticated makers arrive — it is a **first-mover + scale** edge, not a moat.
2. **Superior fair-value pricing (the durable edge).** Cancel-first is available to *every* maker, so it
   doesn't differentiate you — it converts a **speed race into a pricing race.** Your realized pickoff rate
   `q = P(you hadn't updated fair value before the toxic taker filled)`. Whoever prices the closed-hours
   RWA best (overnight ADRs, correlated futures/ETFs, news) carries the lowest adverse selection. **Pricing
   is the alpha; cancel-first just makes the game playable for someone who isn't the fastest.**

> The edge and the risk are the *same phenomenon.* The un-hedgeable toxic inventory you take in
> closed-hours RWA is endogenous to the opportunity: wide dislocation = real news = the fill you just took
> is adversely selected *and* the underlying is shut so you can't hedge it. **You are profitable iff your
> fair-value model keeps realized adverse selection below captured spread net of fees.** Mediocre pricing
> loses money fastest in exactly the names you targeted.

## 2. The model (flows, not the per-fill identity)

```
Daily PnL ≈ V_passive · [ (1−α)·s_half  −  α·q·L  −  f_maker ]  −  hedging  −  opex
            └─ noise spread capture ─┘   └ toxic ┘   └ fee ┘
```

- `V_passive` = your passively-filled notional/day (capacity-limited by book depth × your fill share × turnover)
- `s_half` = half-spread you quote (bps); market-set
- `α` = informed (toxic) flow fraction; `q` = your realized pickoff prob (← your pricing skill); `L` = adverse move if picked off (bps)
- `f_maker` = maker fee (bps) — **decisive, see §3**

Define **net edge per unit passive notional**: `e = (1−α)·s_half − α·q·L − f_maker` (bps). Then
`Daily PnL ≈ V_passive · e/1e4 − costs`. Money = noise-flow spread capture; the killers are toxic fills and fees.

## 3. Fees are decisive — and they force the regime

Confirmed from Hyperliquid docs (sources below). HIP-3 perps are charged at 2× normal perps:

| Regime | Maker fee (tier 0 → top tier) | Verdict for a sub-bp edge |
|---|---|---|
| Standard HIP-3 | **3.0 bps → 2.4 bps** | **Dead** — fee ≥ the whole half-spread |
| **HIP-3 Growth Mode** (90% cut) | **0.30 bps → 0.0 bps** | **The only viable regime** |
| Standard perps (for reference) | 1.5 bps → 0 bps, rebates to −3 bps at high maker-volume | n/a (crypto, low dislocation) |

**Two hard execution constraints fall straight out of this:** (a) you must trade **Growth-Mode** HIP-3
markets (the `xyz` RWA deployer's standard tier), and (b) you must climb to a **high maker-volume fee tier**
to drive `f_maker` toward 0. At 3 bps you lose; at 0.30 bps a 1.5 bp half-spread leaves thin but positive
margin; at 0.0 bps it's clean. The fee is the same magnitude as the edge — this is the gating lever.

## 4. Net edge, grounded in our measured data

Anchor: `xyz:SKHX` (SK Hynix, Korea closed) measured live at **~2.94 bps spread (≈1.5 bp half-spread)**,
**~33 bps dislocation**, **~12 bps/min** vol, depth **~$150–250k within 10 bp**. Per-unit net edge
`e = (1−α)·s_half − α·q·L − f_maker`, with `s_half = 1.5`, and you keeping `q` low via good pricing:

| Scenario | α | q (pricing) | L (bp) | f_maker | **net edge e** |
|---|---|---|---|---|---|
| **Base** (competitive, growth tier 0) | 0.30 | 0.50 | 25 | 0.30 | ≈ **0.2 bp** |
| **Good** (strong maker, top fee tier) | 0.25 | 0.35 | 25 | 0.00 | ≈ **0.6 bp** |
| **Best** (great pricing, low q, top tier) | 0.20 | 0.25 | 25 | 0.00 | ≈ **0.95 bp** |

The edge is genuinely **thin (sub-bp to ~1 bp)** — so PnL is a **volume × thin-edge** business, and fee
tier and pricing skill swing it between dead and good.

## 5. The capacity frontier — why the best case *requires* HIP-3 to grow

The highest edge is in the **thinnest books** (juicy closed-hours names: ~$150–250k depth vs SP500's $3M+
at ~0.13 bp edge). So **$ PnL is capacity-limited exactly where the edge is highest.** The best scenario's
load-bearing assumption is therefore macro: **HIP-3 RWA volume grows and the high-edge names deepen.**

| Scenario | Assumed HIP-3 RWA daily vol | Your passive capture | Net edge | **Daily PnL** | **Annual (~350d)** |
|---|---|---|---|---|---|
| **Base** — current market, modest share | ~$3 B (today) | ~$30 M/day (≈1% of your names) | 0.2 bp | ~$0.9k | **~$0.3 M** |
| **Good** — market 3×, established maker | ~$10 B | ~$200 M/day (~2%) | 0.6 bp | ~$12k | **~$4 M** |
| **Best** — market ~10×, top-3 maker | ~$25–30 B | ~$1 B/day (~3–4%) | ~1.0 bp | ~$100k | **~$35 M** |

**Caveat on the top end:** $1 B/day passive capture is a *dominant global MM operation* across a basket —
that's the ceiling, tied explicitly to (volume growth × your share × edge), not a number to assume lightly.
The realistic near-term target is the **Base→Good band ($0.3–4 M/yr)** as a focused desk, with the Best case
contingent on the RWA-on-HyperCore thesis playing out and you scaling into it early.

> **Don't confuse opportunity with PnL.** The demo's dislocation surface and the protocol's $1.41 M of
> write-priority fees to date are the *opportunity*, not our revenue. Useful framing: write-priority fees ≈
> what takers pay to extract stale-liquidity value **from makers** — a lower-bound proxy for system adverse
> selection. **Our entire job is to be the maker who is *not* on the giving end of that.**

## 6. What we must do well (ordered by what actually wins)

1. **Fair-value pricing for closed-hours RWA — the alpha.** Price SK Hynix at 03:00 Seoul better than the
   stale oracle/book: overnight ADRs, correlated index futures/ETFs, sector moves, news ingestion. This sets
   your realized `q`; everything else is hygiene.
2. **Inventory & hedging book — the risk.** You can't hedge in the shut underlying, so hedge with correlated
   proxies (open index futures, ETFs, sector baskets) and run **hard position limits + auto-pull around
   scheduled events** (earnings, CPI, opens) where `L` spikes.
3. **Fast-*enough* cancel pipeline + gossip read-priority feed — a floor, not an arms race.** You only need
   to beat the block boundary; cancel-first means you can't be out-bid at any fee. Pay gossip read-priority
   to *see* state sooner.
4. **Get to a top maker-fee tier in Growth-Mode markets — decisive (§3).** Volume tiering toward 0 bp maker
   fee is the difference between viable and dead.
5. **Capacity-aware market selection.** Follow the PFI top, but size to depth — don't over-quote a
   $150k-deep book. Diversify across many closed-hours names to build `V_passive` without concentration.
6. **Toxicity signal wired to action.** Use the dashboard's dislocation/PFI spikes to auto-widen/pull and to
   rank where to allocate quoting capital intraday.

## 7. Honest risks / why this is hard
- **The edge compresses with competition** — cancel-first is universal; pricing skill, first-mover and scale
  are the only moats. Assume `s_half` tightens as makers arrive.
- **Fees can flip it negative** (non-growth-mode or low tier) — §3.
- **Pricing/news lag = you become the picked-off party.** Cancel-first only helps if *you* saw the move.
- **Inventory/gap risk** in un-hedgeable closed-hours names on real news.
- **Capacity ceiling** until HIP-3 RWA deepens; **deployer/param risk** (HIP-3 deployer can change fees,
  Growth Mode, oracle); regulatory questions on tokenized-equity perps.
- **What would confirm it's real:** the actual priority-`p` distribution (is the 8 bp cap binding?), a
  within-asset open-vs-closed dislocation time series, and a small **live pilot** measuring *realized*
  adverse selection vs captured spread on a few names. See [MODEL.md](MODEL.md) §7.

**Sources:** [Hyperliquid fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) ·
[HIP-3](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals) ·
[Growth Mode (CoinDesk)](https://www.coindesk.com/markets/2025/11/19/hyperliquid-unveils-hip-3-growth-mode-slashing-fees-by-90-to-boost-new-markets)
