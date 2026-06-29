# The HyperCore Maker Last-Look Model

A formal treatment of the trading idea behind the priority-fee demo. The claim is narrow and falsifiable: **HyperCore's sequencing rule changes one parameter of the classical adverse-selection spread — the pickoff probability `q` — and leaves the rest of the dealer's economics intact.** Everything else follows mechanically.

## 0. Verified mechanism facts (the only inputs we treat as ground truth)

These are taken from the Hyperliquid docs and are not to be contradicted:

1. The HyperCore order book is protocol state. Within a block, transactions are ordered by `effective_time = arrival_time + f(action, priority_fee)`, where `f` is strictly decreasing in the priority fee and is **exactly 0** for a *prioritized* action class — cancels and passive maker updates.
2. Consequence (stated as a protocol property, see §3): all cancels are sequenced ahead of all immediately-executable (taker / IOC) orders in a block. A taker cannot buy priority over a cancel at any price.
3. The order priority fee applies to **IOC orders only**: empirically ≈ 45 ms of effective-time improvement per 1 bp paid, **capped at 8 bp** (so ≤ 360 ms). It is charged **on fill only** (a fraction of filled notional, paid in HYPE) and is **burned**.
4. A separate gossip/read priority auction exists (≈ 25 ms/slot, paid upfront). It affects when an actor *sees* state, not how HyperCore *orders* execution; we note it but it is orthogonal to the maker's last-look race.

## 1. The competitive zero-profit half-spread

We use the Glosten–Milgrom / Copeland–Galai competitive-dealer frame.

**Assumptions (all load-bearing, all stated):**
- Market makers are risk-neutral and competitive ⇒ expected per-trade profit is driven to zero.
- Single period, symmetric (ask side; the bid is the mirror).
- Inventory is ignored: no inventory-risk term, no skew.
- Order flow is a mixture: fraction `α` is *informed* (toxic) and `1−α` is *uninformed* (noise).
- `L` = expected adverse fair-value move (bps) conditional on being hit by informed flow.
- `q` = probability the maker is *actually* picked off given an informed event, i.e. the probability the maker **fails to cancel the stale quote in time**. A benign (uninformed) taker always transacts at the posted quote.
- `s` = posted half-spread (bps). A benign fill earns the maker `+s`; a fill that the maker failed to pull in time costs `−(L − s)` (it collects `s` but eats the full adverse move `L`).

**Conditional fill probabilities.** A benign taker always lifts the quote; a toxic event only results in a fill with probability `q` (otherwise the maker cancels first and there is no trade). So, conditional on a fill at the ask:

- P(toxic | fill) = `αq / (αq + (1−α))`
- P(benign | fill) = `(1−α) / (αq + (1−α))`

**Zero-profit condition.** Setting expected maker PnL per fill to zero, the benign rebate must exactly fund the toxic loss:

```
(1−α)·s  =  αq·(L − s)
```

Solving:

```
s* = αqL / (1 − α + αq)        (exact)
```

**Two reductions worth stating explicitly:**

- **Pure-latency / always-picked-off limit, q → 1:** the denominator collapses to `1`, giving `s* = αL` exactly — the canonical Glosten–Milgrom half-spread. It anchors the model: when the maker can never beat the sniper, it prices the *full* informed fraction.
- **Leading order (α ≪ 1, or s ≪ L):** the `αq` term in the denominator is negligible and `s* ≈ α·q·L`. **This is the working form** used throughout. It is an approximation, named as such; the exact spread is always *smaller* by the factor `1/(1−α+αq)` — see §7.

Read the linear form plainly: the half-spread is the *adverse-selection budget* `α·L` scaled by how often the venue actually lets the maker get picked off, `q`.

## 2. The venue changes only `q`

`α` (toxicity of the flow) and `L` (size of the information event) are properties of the **asset and the information environment**, not of the matching engine. The venue cannot change them. What the venue changes is `q`: how often a stale quote is actually hit before the maker can pull it.

- **Pure-latency venue (or a generic priority-gas L1):** the maker's cancel races snipers *continuously*, and a sufficiently fast / well-funded sniper can be sequenced ahead of the cancel — the maker can be **out-bid** or simply out-run. Against many fast snipers, `q_lat → 1`.
- **HyperCore:** cancels are in the prioritized class (`f ≡ 0`) and are categorically ahead of all takers. A sniper cannot out-bid a cancel **at any fee**. The maker only loses across the **block boundary** — on a signal that arrived after it had already submitted but before its cancel landed in the next block. Hence `q_hc ≪ q_lat`.

In the linear regime this gives the central comparative-static:

```
s*_hc / s*_lat  =  q_hc / q_lat  <  1
```

Same adverse-selection budget `α·L`, strictly tighter spread (or, equivalently, larger size at the same spread). The **absolute** spread saving is

```
Δs  =  α·L·(q_lat − q_hc)
```

which is **largest where `α·L` is largest** — the most toxic, most information-sensitive markets (stale-oracle RWA names, names whose reference cash market is shut). That is precisely where the demo's signal points.

## 3. Proposition: free priority dominates purchasable priority

> **Proposition.** Within any block in which a maker's cancel and a taker's IOC are both present, the cancel is sequenced first, and this holds for the taker's *maximum purchasable* priority (8 bp / 360 ms). The maker therefore obtains, for free, strictly higher sequencing priority than any taker can buy.

**Justification.** This is *not* derived from the `effective_time` arithmetic alone — that formula, taken in isolation, would let a late-arriving cancel lose to an early taker. We rely on the **stated protocol property** that the prioritized action class (`f ≡ 0`: cancels, passive maker updates) sorts ahead of the immediately-executable class. Given both are in the block, the cancel wins regardless of the taker's fee, because the taker's `f` is bounded below by its own floor while still being in the *non-prioritized* class; no admissible priority fee moves an IOC into the prioritized class. The 8 bp cap is then irrelevant to the maker: it is a ceiling on a race the maker is not running.

**What the maker's race actually is.** Not a fee war. It is **observe-the-signal → submit-cancel → land-it-within-the-block, versus the block boundary.** The maker must only be fast *enough* to get its cancel into the same block as (or an earlier block than) the toxic taker. It need not be the fastest actor on the venue, and it cannot be priced out. The claim "free priority dominates purchasable priority" is therefore a *within-contested-block* statement.

## 4. Empirical calibration (illustrative — flagged, not fitted)

A live snapshot showed **`xyz:SKHX`** (an SK Hynix perp; Korea cash market closed at snapshot time) quoting a **≈ 2.94 bps** half-spread while sitting **≈ 33 bps from oracle** with **≈ 12 bps/min** realized vol. Plugging assumed structural values `α ≈ 0.3`, `L ≈ 30 bps` into the linear form:

- A **pure-latency** competitive maker (`q ≈ 1`) would need `s ≈ α·L ≈ 0.3 × 30 = 9 bps`.
- Observing **2.94 bps** implies an **effective `α·q ≈ 2.94 / 30 ≈ 0.10`**, i.e. cancel-first cuts the *effective* pickoff rate to roughly **one-third** of the naive informed fraction (`q_eff ≈ 0.10/0.30 ≈ 1/3`).

**This is one snapshot of one ticker and proves nothing on its own.** The competing explanation is mundane: the flow on this name is **mostly uninformed retail** (a genuinely low `α`), not a low `q` from cancel-first protection. A single point-in-time observation **cannot separate low `α` from low `q`** — both compress the spread identically. Treat the 2.94 → `αq ≈ 0.10` arithmetic as a consistency check, not a measurement.

## 5. The Priority-Fee Opportunity Index (PFI) used in the demo

```
PFI = dislocation(bps) × log10(1 + vol_24h / $100k) × clock_factor
clock_factor = 1.6 if the reference cash market is CLOSED, else 1.0
```

**Honesty statement.** The `1.6` clock-factor lift is a **design choice, not an estimated parameter.** The empirical content of the demo is the per-bucket **dislocation cross-section** (closed-reference RWA names carry larger mark-oracle dislocation than open-reference ones), *not* the PFI lift itself — the lift is imposed, the dislocation is observed. Moreover the dislocation evidence is a **point-in-time cross-section across different tickers**, so it is confounded by composition (the closed-market and open-market buckets are different assets with different liquidity, listing vintage, and flow mix). It is **not** a within-asset open-vs-closed experiment. PFI is a ranking heuristic for *where immediacy is most likely valuable*, not an estimate of the price of immediacy.

## 6. Accessibility — who actually captures this

- **Taker-side sniping is largely inaccessible to non-latency players.** Above ≈ 8 bp of edge the fee cap binds, so the contest reverts to raw latency: the fast, well-capitalized incumbent pays the cap and wins on speed. A slower entrant only ever wins the *marginal*, most-adversely-selected residual — a classic **winner's curse**. You do not want to be the slow taker.
- **The maker-side last-look edge is the accessible one.** It requires being fast *enough* to beat the block boundary, not fastest, and — by §3 — it **cannot be out-bid.** That asymmetry is the whole thesis: the venue hands makers a priority that takers cannot purchase at any price, and the value of that priority is exactly `α·L·(q_lat − q_hc)`, concentrated in the most toxic markets.

## 7. What a quant should attack

In rough order of how much they should worry me:

1. **The dropped denominator.** The headline `s* = αqL` is the *linear* form. The exact spread is `αqL/(1−α+αq)`, so the exact ratio is `s*_hc/s*_lat = (q_hc/q_lat)·(1−α+αq_lat)/(1−α+αq_hc) > q_hc/q_lat`. The true spread *saving* is therefore modestly **smaller** than the linear model advertises, and the correction bites **hardest exactly where `q` is smallest** — i.e. the HyperCore case being sold. Re-run the comparative statics with the exact form before trusting any size of `Δs`.
2. **`q_hc` and `α` are not measured.** The entire edge is `q_lat − q_hc`, and neither endpoint is observed. The §4 calibration cannot distinguish low `q` (the thesis) from low `α` (boring retail flow). Falsifier: measure realized cancel-vs-fill outcomes around live information events.
3. **No within-asset experiment.** The dislocation evidence is a cross-ticker snapshot, confounded by composition. Confirmer: a **persisted within-asset time series** of dislocation around the *same* name's cash-market open/close, earnings, CPI.
4. **The 8 bp cap may not bind.** §3 and §6 assume the cap is reached. That needs the actual **priority-fee `p` distribution** (not exposed by the public info API — requires indexed warehouse or raw L1 node data). If most IOCs pay near zero, the "fast-and-rich wins above the cap" story is moot.
5. **Glosten–Milgrom is single-period and inventory-free.** No inventory skew, no multi-period learning, no quote-fade dynamics. A real maker's spread carries an inventory term we have set to zero; that can dominate `αqL` in thin names.
6. **The `1.6` clock factor is arbitrary.** It changes the PFI ranking but is not estimated. Any presentation that leans on PFI magnitudes (rather than the underlying dislocation cross-section) is leaning on a chosen constant.
7. **HyperBFT block time is unmodeled.** It sets the maker's entire reaction budget (the "block boundary" in §2–§3) and the size of `q_hc`, yet enters the model only implicitly. The edge is mechanically a function of (signal-to-cancel latency) / (block time); neither is pinned down here.

**Bottom line.** The model rests on one clean, verifiable mechanism (`f ≡ 0` for cancels ⇒ uncontestable maker priority) and one unmeasured quantity (`q_hc`). The mechanism is solid; the magnitude of the edge is an open empirical question, and the tests above are how you'd settle it.
