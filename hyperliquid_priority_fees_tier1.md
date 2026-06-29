# Hyperliquid Priority Fees — Tier 1 Research & Execution

*Maker-side structural edge · On-chain priority-fee/toxicity signal · Solana competitive-design read*
*Prepared 2026-06-29. Empirical snapshot pulled from the public Hyperliquid info API at 18:13 UTC.*

---

## TL;DR

- **The mechanism that makes all three ideas work**: within a HyperCore block, *all cancels are sequenced ahead of all immediately-executable (taker/IOC) orders*. `effective_time = arrival_time + f(action, priority_fee)`, with `f` strictly decreasing in the fee and **`f = 0` for cancels**. A taker can buy at most **360 ms** of effective-time improvement (8 bp cap × ~45 ms/bp) and **cannot buy priority over a cancel at any price**. Order priority fees are charged **on fill only** (a fraction of filled notional, in HYPE) and are **burned**.
- **Idea 1 — Maker last-look (the real edge).** A HyperCore maker enjoys, *for free*, strictly higher sequencing priority than the maximum any taker can purchase. Its only race is against the **block boundary**, not against a sniper's wallet. This lets a competitive maker quote a tighter spread / larger size for the same adverse-selection budget — and the advantage is **largest exactly where toxic flow is worst**.
- **Idea 2 — Toxicity / opportunity signal.** We built a measurable cross-market proxy from public data. Result: **dislocation concentrates in tokenized-RWA names whose reference cash market is closed.** Live example — Korean equities mid-Asian-night sit at 23–33 bps mark/oracle gap on hundreds of $M of volume, while US names (open session) sit at 4–5 bps. This *is* the map of where priority-fee competition and stale-liquidity capture concentrate.
- **Idea 3 — Solana read.** HyperCore's protocol-level *semantic ordering* (cancels-beat-takers) + a *bounded, burned* sequencing auction is a genuinely different design from Solana's local fee markets + out-of-protocol Jito auctions. See §4.
- **What we proved vs. what needs one more key.** We measured the *opportunity surface* (where/why toxic flow concentrates) directly. The exact **distribution of priority `p` actually paid** is **not** in the public API — it lives in raw L1 action data. Recovering it needs an authenticated Allium/Dune warehouse (or a node). One-step unlock; spec in §5.

---

## 1. Confirmed mechanics (the load-bearing facts)

From the official [Hyperliquid priority-fees docs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/priority-fees), verified 2026-06-29:

| Property | Value | Why it matters |
|---|---|---|
| Ordering rule | `effective_time = arrival_time + f(action, priority_fee)`; `f` strictly decreasing in fee | Sequencing is a continuous function of fee, but quantized to blocks |
| Cancels / passive updates | `f = 0` — **categorically ahead of all takers** | Maker last-look that no fee can override |
| Eligible flow | IOC orders only, non-outcome assets (now beyond HIP-3) | Only aggressive flow is auctioned |
| Cap | **8 bp** (`p = 80000`, rate `= p/1e8`); cut from 20 bp on user feedback | Cap-cut implies it *was* binding on contested flow |
| Time value | ≈ **45 ms per 1 bp** ⇒ max **360 ms** | A hard latency ceiling: gap > 360 ms ⇒ fee can't save you |
| Charge timing | **On fill only**, fraction of filled notional, paid in HYPE from undelegated staking balance | A priority IOC is a **cheap option** — no fill, no cost |
| Fee destination | **Burned** (not to deployer, not to treasury) | Accrues to HYPE holders; *not* direct HIP-3 deployer revenue — see note |
| Read/gossip priority | separate auction, ~25 ms/slot, 0.1 HYPE floor, 10× escalation, paid upfront | Full-stack edge = read (see sooner) + write (act sooner) |

> **Memo note for the original write-up**: priority fees are *burned*, so the line "strengthens the monetization case for HIP-3 *for the deployer*" needs a caveat — the value accrues to the **HYPE token via burn**, and only indirectly to a deployer (as a HYPE holder / ecosystem value). It is not a deployer fee-share stream.

---

## 2. Idea 1 — The maker-side last-look edge (the cleanest real opportunity)

### 2.1 The structural claim, stated precisely

A resting maker that wants to pull a stale quote submits a cancel. Because `f = 0` for cancels, that cancel is sequenced **ahead of every taker in the block, regardless of how much priority any taker pays.** Therefore:

> On HyperCore a maker enjoys, *for free*, strictly higher sequencing priority than the **maximum** any taker can buy (8 bp / 360 ms). The maker is picked off only if a toxic taker's order lands in an **earlier block** than the maker's cancel — i.e., the maker's only race is *observe→cancel within the same block as the taker's arrival*, **not** a fee war against the sniper.

This is qualitatively different from a pure-latency venue (or a generic priority-gas L1), where the pick-off is a continuous latency race the maker frequently loses to faster snipers, and where a sniper *can* outbid to jump the queue.

### 2.2 Why this lets you quote tighter — the model

Competitive market-making zero-profit condition (Glosten–Milgrom / Copeland–Galai). Let:
- `α` = fraction of flow that is informed (toxic),
- `L` = expected adverse fair-value move conditional on being hit by informed flow,
- `q` = probability the maker is *actually* picked off given an informed event (i.e., fails to cancel in time).

Break-even half-spread:  **`s* = α · q · L`**

The venue only changes `q`:
- Pure-latency venue: `q_lat ≈ 1` (fast snipers usually win the race; the maker can be *outbid* even when it reacts).
- HyperCore: `q_hc ≪ q_lat` — the maker loses only across the block boundary on signals it hadn't processed, and **cannot be outbid** within a block.

⇒ `s*_hc / s*_lat = q_hc / q_lat < 1`. **Same adverse-selection budget, tighter spread (or larger size).** And because the term is `α·q·L`, the *absolute* spread saving is largest where `α·L` is largest — i.e., **in the most toxic markets.** That is the whole edge in one line: *cancel-first is worth the most precisely where being picked off costs the most.*

### 2.3 The data confirms the regime — and is consistent with makers leaning on last-look

Live snapshot, 2026-06-29 18:13 UTC (full method + scripts in §6):

| Market | Ref. cash mkt | Status @18:13 UTC | Spread (bps) | Depth ±10bp ($k, bid/ask) | RV 1-min (bps) | Mark/oracle disloc (bps) | Day move |
|---|---|---|---:|---:|---:|---:|---:|
| `xyz:SKHX` (SK Hynix) | Korea (KRX) | **closed ~12h** | 2.94 | 257 / 154 | 11.81 | **33.1** | −5.3% |
| `xyz:SMSN` (Samsung) | Korea (KRX) | **closed ~12h** | 4.77 | 66 / 153 | 7.04 | **23.4** | −7.2% |
| `xyz:NVDA` | US (NYSE/Nasdaq) | **open** | 1.55 | 690 / 735 | 5.98 | 4.1 | +0.4% |
| `xyz:SP500` | US | **open** | 0.13 | 3,252 / 2,212 | 1.96 | 4.8 | +1.3% |
| `xyz:GOLD` | ~24h commodity | open | 0.25 | 1,257 / 853 | 2.61 | 7.2 | −1.4% |
| `BTC` (core) | 24/7 crypto | open | 0.17 | 12,361 / 982 | 8.40 | 4.6 | +1.5% |
| `SOL` (core) | 24/7 crypto | open | 0.13 | 774 / 555 | 19.33 | 1.7 | +6.1% |

**Read:** dislocation tracks the *reference-market clock*, not crypto vol. Korean names mid-Asian-night carry 5–8× the mark/oracle gap of US names in session; crypto majors (24/7 reference) stay tight even when realized vol is high (SOL RV 19 bps but disloc 1.7 bps). This is a clean natural experiment for "stale liquidity = reference-clock mismatch."

**The striking part:** `xyz:SKHX` quotes a **2.94 bps spread** while sitting **33 bps from oracle** with **11.8 bps/min** vol and a −5.3% day. Plug into §2.2: on a pure-latency venue, a competitive maker facing `L ≈ 30 bps` would need `s ≈ α·L` — e.g. `α=0.3 ⇒ ~9 bps`. Observing a **2.94 bps** spread instead implies an effective `α·q ≈ 2.94/30 ≈ 0.10` — i.e. cancel-first is cutting the effective pick-off rate to **~1/3** of the naive informed fraction. *(Illustrative calibration; assumes competitive zero-profit MMs and given `α`. Alternative explanation: SKHX flow is mostly uninformed retail. Both can be true; a snapshot can't separate them — see §5 for the test that can.)*

### 2.4 How you'd actually run it
- **Be a maker in reference-clock-closed RWA names during their off-hours** (Korean/Japanese/European equities overnight; single-stock names around earnings). Quote inside the naive adverse-selection spread, defended by fast cancels rather than fat spreads.
- **You need to be fast *enough*, not fastest.** Your budget is "react before the next block," and you cannot be outbid — so a competent (not HFT-elite) cancel pipeline suffices. This is the key accessibility point: the edge is *not* gated on winning a latency arms race.
- **Size scales with protection.** Because `q_hc` is low, you can post larger resting size for the same expected toxic loss — useful where depth is thin (SKHX ask depth is only ~$154k; a protected maker can add real size).
- **Risk controls:** the residual exposure is cross-block (a toxic taker in block *N* vs your cancel in *N+1*) and feed-latency (you must *see* the move). Co-locate the *read* path (gossip priority helps here) and monitor the dislocation signal in §3 to widen/pull pre-emptively.

---

## 3. Idea 2 — Priority-fee / toxicity opportunity signal

### 3.1 What's directly measurable now
The priority value `p` paid per order is **not** in the public info API (fills return only a total `fee`, inclusive of builder fee; order objects carry `tif`/`cloid` but no `grouping`/`p`). So instead of measuring the *price paid for immediacy*, we measure **the thing immediacy is paid for**: the live stale-liquidity surface. Components, all public:
- **Mark/oracle dislocation (bps)** — direct gap between book price and external reference = the capturable edge.
- **24h notional volume** — how much contested flow the market carries.
- **Realized vol + spread + depth** — adverse-selection intensity and how thin the book is.
- **Reference-clock-closed flag** — equities/commodities/FX whose cash market is shut = structural staleness windows.

### 3.2 Cross-market result (snapshot)
- **452 markets across 10 perp DEXs.** Core $4.84B/24h (230 mkts); **`xyz` (tokenized equities/commodities/FX) $3.18B/24h across 97 mkts** — the second venue and the epicenter of reference-clock risk. All other HIP-3 deployers are <$22M/24h today.
- **Highest dislocation × volume (the hot zone):** `xyz:SKHX` ($352M, 33 bps), `xyz:SMSN` ($48M, 23 bps), `xyz:SPCX` ($154M, 9 bps), `xyz:SILVER` ($130M, 7 bps), `xyz:GOLD` ($33M, 9 bps), `xyz:MSTR` ($83M, +13% day). Pure-crypto majors sit low (BTC/ETH/SOL 2–5 bps) despite huge volume.
- **Dynamic:** SKHX moved 29.5→33.1 bps between two pulls minutes apart — the surface fluctuates intra-minute, which is what makes it a tradable *signal* rather than a static ranking.

### 3.3 Productizing it
- **Priority-Fee Opportunity Index (PFI), v1** per market = `dislocation_bps × √(24h notional) × clock_mismatch_flag`, sampled on a short interval. Rank descending = real-time heatmap of where stale-liquidity capture (and thus write-priority competition) concentrates.
- **Uses:** (a) *maker* — auto-widen/pull when your market's PFI spikes; (b) *desk/research* — flag where edge exists and how contested; (c) *gossip auction price* (0.1 HYPE floor, 10× escalation) as an orthogonal congestion/competition gauge.
- **To make it live:** repeated snapshots on a 5–30 s cadence + event windows (cash-market open/close, scheduled earnings/CPI). This is a `Monitor`/cron job over the same two scripts; happy to wire it up.

---

## 4. Idea 3 — Solana competitive-design read

**Where ordering authority lives — the crux.** On HyperCore, intra-block order is *protocol state* governed by a semantic rule (cancels/passive updates sequenced ahead of all taker flow; `f=0` for them, so no fee buys a taker past a maker's cancel). On Solana there is no analogous rule at any layer: intra-block order is **leader / off-chain-auction discretion**, not consensus. The protocol's only ordering-relevant guarantee is write-lock conflict avoidance, not intent sequencing.

- **Solana, protocol layer.** Agave's banking-stage scheduler prioritizes by `P = R/(1+C)` (reward over compute) and resolves write-lock conflicts so contenders don't deadlock. It runs genuine **per-writable-account local fee markets** (`PrioritizationFeeCache.min_writable_account_fees`): touching a hot account (a popular market's book) must clear a higher CU-price floor. But this raises the *price* of touching a hot account — it never reorders by *what the transaction is trying to do*. A cancel and a take on the same book are just two writes racing on one lock. No global mempool (Gulf Stream forwards to the scheduled leader); ordering = leader output.
- **Jito — out of protocol.** Most stake runs jito-solana, adding an *off-chain* block-space auction: searchers submit atomic **bundles** + **tips** to the Block Engine; winners execute top-of-block, all-or-nothing. Two things matter here: the auction is centralized/off-chain (enforced only by the leader choosing to run it), and **tips accrue to validators/stakers, not burned**. Solana captures/redistributes MEV efficiently, but the sequencing logic is opaque, off-chain, fee-maximizing — the opposite of a protocol-level maker-protection rule.

**Implication for Solana CLOB makers.** Makers on Phoenix/OpenBook *are* exposed to same-block pick-off in a way HyperCore makers structurally are not: a stale maker's cancel and a sniper's take race on the same write-lock, decided by priority fee / Jito tip / leader, with no last-look favoring the cancel. The **fee asymmetry compounds it** — Solana priority fees *and* Jito tips are paid win-or-lose (a maker pays for every *losing* cancel) and go to stakers; HyperCore's write priority is charged **on fill only** and **burned**. "Pay even when you lose, pay the validator" vs. "pay only on fill, it's burned" is a real maker-friendliness gap.

**Mitigations and their limits.** Phoenix is a crankless, no-CPI book with atomic FIFO matching inside the place-order instruction — great for execution determinism and composability, but unrelated to sniper adverse selection (it does not replicate cancel-beats-take). OpenBook v2 matches on-chain at placement with event-heap/crank settlement — again no semantic last-look. Neither can give the HyperCore guarantee, because that requires owning intra-block *intent* ordering, which a permissionless program does not.

**What's portable — and the cost.** App-level semantic ordering is achievable on Solana only by moving the ordering decision *off* the permissionless public book:
- **Pyth Express Relay** — sealed off-chain auction (~400 ms) for OEV/liquidation flow, with on-chain enforcement that only the winning searcher executes. Proves app-controlled sequencing is enforceable on-chain, but it's a liquidation/OEV tool, not general CLOB last-look.
- **DFlow** — application-level last-look on Solana *today*: RFQ flow routes to a winning MM and the signed tx is sent back for last-look. Strongest evidence maker last-look is portable — but it works by pulling flow *off* the public CLOB into a permissioned, toxicity-segmented RFQ network.

There is no shipped *protocol-level* "application-controlled ordering" primitive on Solana; the shipped answers are off-chain-auction or off-chain-last-look + on-chain enforcement.

**Honest BD narrative (axis-dependent, not "Solana wins").**
- *Where HyperCore is genuinely better:* for a single vertically-integrated derivatives venue optimizing maker quality, baking cancel-beats-take into protocol state delivers tighter spreads and adverse-selection protection *without trusting an off-chain sequencer*. Real, defensible advantage for that one venue's market quality.
- *Where Solana wins:* general-purpose composability and choice — a Phoenix fill composes atomically with lending/options/a stablecoin leg in one tx; Jito captures and redistributes MEV to stakers; apps opt into the protection model they want (RFQ/last-look via DFlow, OEV auctions via Express Relay) instead of one protocol rule. The cost of HyperCore's guarantee is that it lives only inside one app's state machine; the cost of Solana's flexibility is that bare permissionless CLOBs inherit no maker last-look and must build it at the app layer.

### Sharpest contrasts

| Dimension | HyperCore | Solana (today) |
|---|---|---|
| **Ordering authority** | Intra-block order is protocol state; semantic rule sequences cancels/maker-updates before takers | Leader / off-chain Jito-auction discretion; protocol only avoids write-lock conflicts, never sequences by intent |
| **Maker last-look** | Protocol-level: no fee buys a taker past a maker's cancel | None: cancel and take race on the same write-lock, decided by priority fee / Jito tip |
| **Fee trigger & destination** | On fill only; **burned** | Priority fees + Jito tips paid win-or-lose (maker pays for losing cancels too); accrue to validators/stakers |
| **Portability of the guarantee** | Native | Only by moving flow off the public book (Express Relay / DFlow) + on-chain enforcement; sacrifices permissionless single-tx composability |

*Receipts:* `anza-xyz/agave` (scheduler `P=R/(1+C)`, per-account fee markets), `jito-foundation/jito-solana` (off-chain auction, bundles, tips→stakers), `Ellipsis-Labs/phoenix-v1` (crankless/no-CPI/FIFO), `openbook-dex/openbook-v2` (event-heap crank), [Pyth Express Relay](https://docs.pyth.network/express-relay/how-express-relay-works), [Helius — DFlow](https://www.helius.dev/blog/dflow). HyperCore constants (45 ms/bp, 8 bp, 25 ms read slots, burn) are from the verified Hyperliquid docs, not independently re-derived.

---

## 5. What we proved, what we modeled, and the one key that unlocks the rest

| Claim | Status | Evidence |
|---|---|---|
| Cancels beat all takers; maker last-look is free & un-outbiddable | **Proven** (docs) | §1, official docs |
| Dislocation concentrates in reference-clock-closed RWA names | **Measured** | §2.3 / §3.2 snapshot |
| HyperCore makers quote tighter than naive adverse selection allows | **Consistent-with** (not causal) | SKHX 2.94 bps @ 33 bps disloc |
| Makers can quote tighter/larger for the same toxic budget | **Modeled** | §2.2 (`s*=α·q·L`) |
| The 8 bp cap is frequently *binding* on contested flow | **Inferred** | cap cut 20→8 bp on feedback; needs `p` data |
| Priority `p` clusters near cap in hot-zone names | **Untested** | requires raw L1 / warehouse |

**The single unlock — the `p` distribution.** Two paths:
1. **Authenticated Allium** (`run_sql_query` over HyperCore order-action tables) — *if* Allium parses the `grouping.p` field. Setup is one CLI line: `claude mcp add --scope user --transport http allium https://mcp.allium.so --header "X-API-KEY: <key>"`. I'd then query: filled IOC orders on `xyz:*` over 30d → distribution of `p`, % at/near 8 bp cap, segmented by market and by external price-jump windows. That directly settles "is the cap binding" and turns §3's proxy into the real thing.
2. **Raw L1 / node archives** (heavier) — parse signed order actions for `grouping: priorityGrouping`.

Until then, §3's dislocation surface is the best public proxy and is independently useful.

---

## 6. Appendix — reproducible method

Two standalone Python scripts (public API, no auth):
- [`hl_probe.py`](hl_probe.py) — enumerates all perp DEXs + markets via `perpDexs` / `metaAndAssetCtxs`; computes per-market 24h volume, OI, mark/oracle dislocation, day move, funding; ranks by volume and by dislocation.
- [`hl_probe2.py`](hl_probe2.py) — pulls `l2Book` (spread, ±10 bp depth) and `candleSnapshot` (1-min realized vol) for a representative set, joined to live mark/oracle.

Endpoints used: `POST https://api.hyperliquid.xyz/info` with `{type: perpDexs | metaAndAssetCtxs(+dex) | l2Book | candleSnapshot}`. All figures above are a single 18:13 UTC 2026-06-29 snapshot; re-run for current values.

**Caveats:** single snapshot (not a time series); `α`/`q` in §2.2 are assumed, not fit; base vs priority fee split not separable in public data; "consistent-with" ≠ causal for the SKHX spread observation.
