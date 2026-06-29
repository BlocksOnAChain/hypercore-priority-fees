# Build progress — HyperCore Priority-Fee demo

## Goal
Turn the Tier-1 research (maker last-look edge, on-chain priority-fee/toxicity signal,
Solana competitive read) into a presentable, runnable demo. Local web dashboard over the
public Hyperliquid API.

## Decisions taken (reversible — no user gate needed)
- **Form:** local web dashboard, Python stdlib only (no npm/build) for reliability.
- **Network/security:** read-only public API; **no public deploy, no funds, no keys.**
  Public deployment would need explicit user approval — left local by design.
- **PFI v1:** `dislocation × log10(1+vol/$100k) × clock_factor(1.6 if ref closed)`.

## Status: CONVERGED ✅ (adversarial review triaged; all real findings fixed)

| Component | State | Evidence |
|---|---|---|
| `hl_data.py` | done | CLI prints live PFI ranking; closed-RWA names top it; DST-correct tz logic |
| `serve.py` | done | /api/*, /vendor all 200; traversal blocked; cache bounded |
| `index.html` | done | renders; charts + capped table + calculator verified via preview eval |
| `test_hl.py` | done | **56/56 pass**, incl. live smoke, DST, malformed, traversal |
| Real run | done | live API → 452 markets, $8.23B 24h; charts + KPIs confirmed in browser |
| Adversarial review | done | subagent; 8 findings, all real ones fixed (below) |
| Docs | done | README.md + this file |

## Adversarial review findings → resolutions
- **HIGH DST mislabeling** → rewrote `reference_open` with `zoneinfo` per-market tz (DST-correct year-round). Tested with winter EST instants.
- **HIGH/MED KPI cross-section confound** → relabeled section "live cross-section", added UI caveat that it's a point-in-time cross-section across different tickers (not a within-asset experiment).
- **MED JS median upper-middle bias** (inflated the featured closed-RWA number) → fixed to true median (avg of two centers for even n). Verified med([10,40])=25.
- **MED XSS via innerHTML on untrusted HIP-3 names** → `esc()` escaper on coin/bucket/error. Verified neutralizes `<img onerror>`.
- **LOW vendor prefix traversal** → `safe_vendor_path` requires trailing os.sep; tested.
- **LOW compute_pfi(None vol) / malformed book+candle** → guarded; tested.
- **LOW unbounded detail cache** → `TTLCache(max_entries=512)` with oldest-eviction; tested.
- **PFI clock_factor=1.6 circular** (LOW) → kept as explicit ranking heuristic; README + UI frame it as an assumption, not a finding (empirical evidence is the per-bucket dislocation, not the lift).
- Reviewer's "Asia weekend bug" → self-retracted (KST/JST sessions never touch a UTC weekend).

## Verification log (3 ways)
1. **Tests:** `python3 test_hl.py` → 44 passed, 0 failed (classification, clock windows,
   PFI monotonicity, realized vol, row-building fixtures, maker model, + live smoke).
2. **Real run:** server started via preview manager on :8787; live pulls returned 452
   markets across 10 perp dexs; `/api/detail?coin=BTC` returned real spread/depth/vol.
   Browser render confirmed: corrected KPIs, bubble + bar charts painting, table sorted by PFI.
3. **Adversarial review:** subagent in progress.

## Bugs found & fixed during build (self-adversarial pass)
- **KPI direction bug (HIGH):** original "median dislocation open vs closed" lumped 24/7
  crypto into "open", whose illiquid-alt stale-oracle tail (median 9.4bps, mean 2200+)
  inverted the headline (open > closed). Fixed: restrict to clock-bound equity/index
  buckets → closed 5.3 median / 47.5 mean vs open 4.2 / 28.2 (correct direction; tail-driven).
- **Charts blank (MED):** Chart.js via CDN didn't render in the preview sandbox + canvas
  had no sized container. Fixed: vendored Chart.js locally (`/vendor/`) + fixed-height
  `.chartbox` containers with `maintainAspectRatio:false`.
- **Path traversal (guard):** `/vendor/` route hardened with `normpath` + prefix check
  (verified `/vendor/../serve.py` → 404).
- **Page height (MED):** 452-row table made a 19,500px page. Capped table to top 80 with
  a "narrow with filters" footer row.
- **Stored XSS (HIGH, preempted):** HIP-3 market names are permissionlessly deployed →
  untrusted. `coin`/`bucket` were rendered via `innerHTML`. Added an `esc()` HTML-escaper
  (verified it neutralizes `xyz:<img onerror=...>`), applied to coin/bucket/error strings.

## Open decision-gate items for the user
- **None blocking.** If you want this hosted at a shareable URL (vs local-only), that's an
  outward-facing deploy I'd want your explicit OK for first.

## Natural next steps (not built)
- Persist a time series → turn the cross-sectional PFI into a live spiking signal around
  cash-market open/close and scheduled events.
- Authenticate Allium/Dune → pull the actual priority `p` distribution and validate "is the
  8bp cap binding" (memo §5).
