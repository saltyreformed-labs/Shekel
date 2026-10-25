# Account Detail Pages Audit (Fable 5 overhaul)

Per-surface diagnosis of the type-specific account detail pages, the fourth rebuild target of the
Fable 5 UI/UX overhaul (after grid, dashboard, accounts cockpit). Written 2026-07-02 from a code
trace of the routes, templates, and the cockpit's `detail_endpoint` routing; line references are to
the code as of `dev` at PR #52 (`2d81705`).

## Scope

The cockpit card's Details action routes to five destinations
(`app/templates/savings/_cockpit.html:34-41`). This audit covers the three that live under
`app/routes/accounts/detail.py` and are "largely tabular data":

| Route | Template | Serves |
| ----- | -------- | ------ |
| `/accounts/<id>/checking` | `accounts/checking_detail.html` | Checking |
| `/accounts/<id>/interest` | `accounts/interest_detail.html` | any `has_interest` type (HYSA, Money Market, CD, HSA) |
| `/accounts/<id>/property` | `accounts/property_detail.html` | any `has_appreciation` type (Property) |

Out of scope, confirmed at the gate below: `loan.dashboard` (amortizing types; developer excluded
loans from this pass) and `investment.dashboard` (retirement / investment types; its own screen in
the overhaul's provisional order, and it is chart-bearing, not tabular).

**Coverage gap (in scope as a decision):** Savings, Credit Card, and any plain custom type have NO
detail page at all -- `detail_endpoint` falls through to an empty branch, so their cockpit cards
render no Details link (this same gap already forced hard-delete into the edit form's danger zone;
`accounts_audit.md` decision 12).

## The screens' jobs

- **Checking / interest detail:** "Where is this account's balance heading, period by period, from
  its anchored reality?" -- plus, for interest-bearing types, "what is it earning?"
- **Property detail:** "What is this property worth, and how much of it do I own once its mortgage
  is netted out?"

Data is already sound: balances flow through the balance-at seam (`balance_at.cash_balance_map` /
`balance_map` + `net_worth_kernel.interest_by_period_for_account`), the anchor resolves via the
dated `AccountAnchorHistory` source of truth, and equity comes from `home_equity_service` (the same
producer as the cockpit's debt figures). The F-6 static guard pins the seam usage. This is a
presentation rebuild; the two data-adjacent findings are #2 (anchor caption) and the route additions
if the gate approves them.

## Surface 1: Shared page chrome (all three pages)

- **Should show:** where you are, one way back, the account's identity in the cockpit's visual
  vocabulary.
- **Actually does:** a breadcrumb ("Accounts Dashboard / name") AND a "Back to Accounts" button on
  the same header row, both to `savings.dashboard` (`checking_detail.html:5-20`, same in the other
  two). Header icons diverge from the cockpit's `acct_glyph` vocabulary: interest pages use
  `bi-bank` (cockpit uses `bi-piggy-bank` for interest types), property uses `bi-houses` (cockpit:
  `bi-house-door`). The `setup=1` post-create alert (interest, property) is useful onboarding.
- **Divergence:** duplicated navigation (two CTAs to the same place); icon vocabulary drift against
  the cockpit cards the user just clicked.
- **Verdict: fix.** One navigation affordance; reuse the cockpit glyph mapping; keep the setup
  alert.

## Surface 2: Account Summary card (checking + interest)

- **Should show:** the account's headline balance as the hero, its anchor provenance, and where the
  balance is heading at a glance.
- **Actually does:** a half-width card (`col-md-6`; on checking the right half of the row is simply
  empty) of small label/value rows: Type, Anchor Date (checking) or APY + Compounding (interest),
  then Current Balance as an `fs-5` row styled `text-accent`, then a "Projected:" list of 3 / 6 /
  12-month figures in `small` (`checking_detail.html:22-63`, `interest_detail.html:29-71`).
- **Divergence (confirmed):**
  1. **The number is not the hero** (principle 1). The balance is one row among rows, at `fs-5`,
     while rebuilt screens give the headline figure hero scale (cockpit `nw-hero__num`).
  2. **"Anchor Date" caption disagrees with its figure** (principle 2). It renders
     `anchor_period.start_date` -- the anchor PERIOD's start -- not the anchor event's date. The
     resolver already returns the real thing (`AnchorPoint.as_of_date`,
     `balance_resolver.py:104-119`), and the cockpit's "as of" display uses it. A user who trued up
     mid-period reads the wrong date here.
  3. **Accent used as money color.** `text-accent` on the balance breaks the Steel Ink rule that the
     accent is the only non-money chroma (controls / brand), never the money figure itself; the
     cockpit hero is plain `font-mono` with state color only when negative.
- **Verdict: fix.** Balance becomes the hero with an honest "anchored <as_of_date>" caption;
  horizons become scannable chips (the cockpit `pulse-chip` pattern); type/APY/compounding become
  quiet metadata.

## Surface 3: Parameters forms (interest APY + compounding; property appreciation rate)

- **Should show:** set-and-forget configuration, editable but not competing with the numbers.
- **Actually does:** a full half-width card ("Interest Parameters" / "Appreciation") with a percent
  input, a compounding `<select>` (rendered by ref id -- correct), and a submit that full-page
  redirects with a flash (`interest_detail.html:73-106`, `property_detail.html:59-87`). CSRF
  present, POST used, schema-validated percent-domain conversion handled in the route/schema.
- **Divergence:** prominence only -- configuration occupies the same visual rank as the money it
  parameterizes. Mechanics are sound and simple (a full reload is fine for a rare edit).
- **Verdict: keep + demote.** Same fields and POST endpoints, restyled as a quiet, collapsed or
  side-rail config surface. No service changes.

## Surface 4: Balance Projection table (checking + interest)

- **Should show:** the trajectory (the trend IS the answer) plus exact per-period figures (tabular
  money).
- **Actually does:** a flat `table-sm` of every period in the rolling window (dozens of rows, about
  two years of biweekly periods), columns Period / Balance (+ Interest Earned on the interest page),
  no chart, no "today" marker, no grouping, rendered oldest-first so the current period is buried
  mid-list (`checking_detail.html:65-94`, `interest_detail.html:109-146`).
- **Divergence (confirmed):**
  1. **No trend presentation.** The design language routes "a trend IS the answer" screens through a
     chart first (the dashboard's 13-period end-balance chart is canonical); here the user
     scroll-scans dozens of rows to see a shape.
  2. **No current-period anchor point.** Nothing marks "you are here"; the first rows are past
     periods.
  3. **Raw Bootstrap theming.** `thead class="table-light"` hardcodes a light header band that
     ignores `data-bs-theme` (nothing in the token layer remaps it -- verify visually in Loop B),
     and the interest column uses `text-success` instead of the token-mapped state treatment
     (`--shekel-done`).
  4. Interest "+$x.xx" pairs sign and color correctly (color not the only signal) -- keep that.
- **Verdict: fix.** Chart-first trend with the full tabular list preserved beneath it (principle 3:
  no figure the user relies on is dropped), current-period marker, token-based table styling shared
  with the grid vocabulary.

## Surface 5: Property Summary + Home Equity (property page)

- **Should show:** market value, appreciation assumption, and the equity story (value minus secured
  debt, LTV), with working guidance for updating the value.
- **Actually does:** summary card (Type / Appreciation Rate / Market Value at `fs-5 text-accent`),
  the appreciation form, then a full-width Home Equity card: Market Value, Secured Debt, Equity
  (green/red via `text-success` / `text-danger`), LTV, and a "Secured by" loan list linking to
  `loan.dashboard` with a good empty state (`property_detail.html:31-146`).
- **Divergence (confirmed):**
  1. **Stale copy pointing at a retired screen.** "Update it from the accounts list to true up the
     home's value" (`property_detail.html:51-54`) -- the `/accounts` list was retired in cockpit P4
     and now redirects; the live edit surface is the cockpit card's click-to-edit balance. Breaks
     principle 4 (every call to action goes somewhere useful).
  2. Market Value repeats in both cards; the summary card's copy of it is the redundant one.
  3. Same raw-Bootstrap state colors and accent-as-money issues as above.
  4. Equity is the page's real headline but renders at the same `fs-5` rank as its supporting
     figures.
- **Verdict: fix.** Equity becomes the hero with market value and secured debt as its supporting
  pair (a figure minus a figure), LTV as a chip, corrected copy (or an in-place click-to-edit for
  market value, gate call), tokens throughout, keep the "Secured by" list and empty state.

## Surface 6: The missing detail page (Savings, Credit Card, plain custom types)

- **Should show:** the same "where is this balance heading" answer every other cash account gets.
- **Actually does:** nothing -- no route, no link on the cockpit card. The type's balance still
  projects (the cockpit sparkline and `savings_dashboard_service` already compute it via the same
  producers), so the data exists; only the page is missing.
- **Divergence:** an entire account class has no drill-down, which is why the cockpit card's primary
  click affordance silently does nothing for them.
- **Verdict: add (gate decision).** The checking page IS the plain-cash detail page in all but its
  type guard; generalizing it covers these types with no new financial logic. Route work is
  Opus-scope per the model discipline.

## Cross-cutting notes

- `checking_detail.html` and `interest_detail.html` are about 80 percent the same template; the
  interest page adds APY/compounding rows, the params form, and one table column. DRY argues for one
  shared cash-detail template (and possibly one route) with the interest surfaces conditional on
  `has_interest`.
- The money macro is used throughout (good; negative formatting standardized). No inline styles or
  scripts. No template money math. Display-only `.name` usage only. CSRF everywhere.
- Empty states: with no scenario or no periods the pages render the summary card with "--" and no
  table -- acceptable, but the rebuild should say why ("no pay periods yet") rather than showing a
  bare card.
- Nothing here mutates balances; the cockpit remains the true-up surface (unless the gate moves
  click-to-edit onto the detail page too).

## Rebuild decisions (Gate A)

Decided 2026-07-02 (developer ruling). Locked.

1. **One cash-detail template.** `checking_detail.html` and `interest_detail.html` merge into a
   single cash-account detail page; the APY/compounding metadata, the params form, and the interest
   table column render only for `has_interest` types. One route family serves it.
2. **Coverage extends to the missing types.** Savings, Credit Card, and plain custom types get the
   same cash detail page: the checking route's type guard generalizes to all plain cash types and
   the cockpit `detail_endpoint` macro gains their branch. No new financial logic (their projections
   already flow from the same producers); the route/guard edit is an Opus-scope commit per the model
   discipline.
3. **Chart-first, table preserved.** A trend chart with a today marker and 3/6/12-month horizon
   chips answers the trajectory question; the complete per-period table remains below it, so no
   exact figure is dropped (principles 3 and 5). **AMENDED at the Loop A round-1 pick (2026-07-02,
   developer ruling): the per-period table is DROPPED.** The developer's lived workflow: per-period
   detail tracking happens on the GRID, and only for checking (the volatile account); the detail
   pages' job is trend, health, and easy parameter management. Exact per-period values are re-homed
   to the trend chart's hover tooltip (the app's standard chart behavior), so the figures remain
   reachable -- removal of the table is the explicit product decision principle 3 requires, not a
   silent drop. Health signals (e.g. projected interest over the next year, computed in the route
   with Decimal) join the chip row.
4. **Investment/retirement dashboard stays out.** It remains its own later rebuild target per the
   overhaul's provisional order.
5. **Property page** rebuilds per the Surface 5 verdict (equity hero, supporting value/debt pair,
   LTV chip, corrected copy, tokens). Whether market value gets in-place click-to-edit here or the
   copy simply points at the cockpit's editor is a Loop A presentation call.
6. **Layout direction: A "Cockpit band"** (Loop A round-1 pick, 2026-07-02): hero + horizon chips
   - trend chart in one banded canvas, continuing the dashboard/cockpit hero-band grammar. Round 2
     explores where the promoted parameters surface lives (resident card below the band vs inline in
     the band) and gives the checking variant an "Open grid" call to action, since the grid is
     checking's detail home.
7. **Parameters live in a card BELOW the band** (Loop A round-2 pick, 2026-07-02): the in-band
   variant was rejected as too crowded. Final anatomy, whole family: band = hero + honest anchored
   caption, horizon chips (with the done-tinted "Interest, next 12 mo" chip for `has_interest`
   types), and the trend chart with its TODAY marker; below the band, a Parameters card (APY and
   compounding, or the property appreciation rate). The plain-cash variant (Checking / Savings /
   Credit Card) has no parameters card and carries an "Open grid" action in the band instead; the
   property page keeps its Secured-by card below alongside Parameters. Loop A CLOSED; the scratch
   mockups (`cash_detail_r2.html`, `property_detail_explore.html`) stay in /tmp per the
   anti-anchoring rule and are deleted when Loop B completes.

## Loop B as-built (2026-07-03)

Built on `feat/account-detail-rebuild` (worktree off `dev` at PR #52). All seven rebuild decisions
shipped as specified; deltas and additions worth recording:

- **Route shape:** one `GET /accounts/<id>/details` (`accounts.cash_detail`) serving every cash
  kind; the old `/checking` and `/interest` URLs remain as ownership-checked redirect stubs (an IDOR
  probe of a not-yours stub URL 404s BEFORE redirecting -- the full suite caught the stub initially
  answering 302, fixed at the root in `_redirect_to_cash_detail`).
- **Per-kind producer paths preserved verbatim** (interest: `balance_at.balance_map` + kernel
  interest accessor; plain: `balance_at.cash_balance_map`); the F-6 static guard was re-pointed and
  pins both seam entries on the merged route.
- **Anchor caption fixed** (audit finding #2): the hero caption renders `AnchorPoint.as_of_date` --
  the true-up event date -- never the anchor period's start date.
- **Horizon chips carry Decimal deltas** computed in the route; the interest health chip sums
  `interest_by_period` over periods `[current+1, current+26]`.
- **Chart:** `data-chart` JSON (labels / balance floats / current_index) mirroring the cockpit
  serializer's conventions; solid-history vs dashed-projection and the Today marker come from shared
  `ShekelChart` helpers extracted from `net_worth_cockpit.js` into `chart_theme.js` (`hexToRgba` /
  `splitSegment` / `todayMarkerPlugin`), so the two trend charts cannot drift. Long series (> 26
  periods) drop resting point dots and thin the x labels to ~13 (`maxTicksLimit`); below-zero points
  stay visible as danger-colored warning dots.
- **Cockpit macros extracted** to `_acct_macros.html` (glyph + detail endpoint shared across the
  cockpit and detail pages); every cash card now has a Details destination.
- **Live-verified 2026-07-03** on prod-clone dev data (worktree app on :5001, both themes, desktop
  and mobile):
  - Money Market: hero, chips, chart, and the $279 projected-interest chip (3.29% APY) reconcile.
  - Checking: the volatile sawtooth, a NEGATIVE 3-month horizon chip (danger-colored, signed), and
    below-zero danger dots.
  - Property: $350,000.00 minus $177,554.69 = $172,445.31 equity to the cent, LTV 50.7%.
- **Deferred / follow-ups:** in-place market-value editing on the property page (copy points at the
  cockpit's click-to-edit balance instead; small follow-up reusing the anchor editor if wanted). The
  `setup=1` post-create banner and all params POST endpoints unchanged.

## Property equity chart (2026-07-06)

Enrichment of the Surface 5 property page: its band is the only one in the detail family with no
chart (the slot holds helper copy). The developer wants the equity story over time -- market value
appreciating, secured debt amortizing, equity as the difference. New surface work, so it re-enters
the protocol here: this audit section, then Loop A mockups, then Loop B.

### The surface's job

"How much of this property do I own, and where is that share heading?" The answer is an arc, not a
window: the value line climbs at the appreciation rate, the debt line falls to zero at payoff, and
equity -- the gap -- widens from both edges until it is the whole property. The chart must make that
arc legible; the hero and chips already carry the as-of-today snapshot.

### First attempt, rejected (2026-07-06)

A pay-period-window chart (the cash-detail axis reused verbatim) was built out of protocol and
rejected on a live drive. Findings, which the redesign must answer:

1. **Flat lines.** A ~2-year biweekly window barely moves a mortgage balance, and a property whose
   appreciation rate is still the zero sentinel has a dead-flat value line by construction. Two
   horizontal lines answer nothing. The equity story lives on the decades scale.
2. **Ambiguous x labels.** The family's `%b %-d` (month + day, no year) label format is unreadable
   on any multi-year axis; the reader cannot tell what span they are looking at.
3. **Dead space.** A zero-based y-axis with a top-anchored value line and a mid-chart debt line left
   the bottom third of the plot as empty ink.

### Gate A rulings (developer, 2026-07-06 -- LOCKED)

1. **Horizon: to payoff.** Monthly axis ending at the last secured loan's payoff -- the loan detail
   band's own axis grammar. (Fallback horizon for a paid-off or loan-less property is an open Loop A
   item below.)
2. **Include loan history.** Confirmed history renders solid, the forward projection dashed, with
   the Today marker at the boundary (the loan band's splice). The value line flat-carries backward
   from today's anchor -- its past is unknown -- and that flat carry must be captioned honestly.
3. **Fill style and the remaining visual forks are NOT ruled.** They are Loop A material, judged on
   rendered mockups, not descriptions.

### Open Loop A forks

- **Fill style:** stacked shares (debt region tinted from zero as the bank's share, equity band
  tinted between debt and value as the owner's share) vs equity-fill-only (only the gap tinted) vs a
  single equity line. The dead-space finding argues for the stacked reading; the mockups decide.
- **Value-history caption:** how the flat-carried value history announces itself.
- **Label cadence:** `%b %Y` labels thinned to a readable count (the loan chart's convention); exact
  tick density on a decades axis.
- **Legend / tooltip composition:** exact figures live in the hover tooltip per the family rule
  (decision 3 amendment); what the legend and tooltip rows/footer carry.
- **The zero-rate state:** how a flat value line (appreciation rate still the 0% sentinel) is
  captioned and pointed at the Parameters card so it reads as "unset," not "broken."
- **Paid-off / loan-less fallback horizon:** proposed 10 years of appreciation-only projection;
  confirm or adjust at the Loop A lock.

### Loop B sketch (finalized at the Loop A lock)

Data (Opus scope): per secured loan, the monthly confirmed-history + committed-forward rows the loan
band already charts (`build_baseline_scenarios` / `compute_payoff_scenarios` with
`confirmed_loan_view`); the multi-loan merge and post-payoff zero padding follow the
`_build_chart_series` pattern in `app/routes/loan/_helpers.py` (hoist to a shared home, do not
duplicate). The value line compounds today's anchor at each month via the growth engine's
`(1 + rate) ** (days / 365)` primitive (`growth_engine.period_return_rate`) so there is exactly one
appreciation formula in the codebase; equity is the per-month `Decimal` difference. Visual (Fable
scope): the band canvas + a `property_detail.js` renderer on the shared `chart_theme.js` helpers, to
whatever direction Loop A locks.

### Loop A lock (2026-07-11 -- developer pick, round 1; Loop A CLOSED)

Three fill directions were mocked on the full band (real stylesheets + the real `chart_theme.js`
linked into the scratch mockup, shot in both themes and both viewports): A stacked shares, B
equity-gap-only, C single equity line. **The developer picked A -- stacked shares** -- and confirmed
the loan-less fallback horizon. The as-mocked anatomy, locked with the pick:

1. **Fill (the ruled fork):** debt region washed danger at 10% from zero (the bank's share); equity
   band washed accent at 10% between the debt and value lines (the owner's share). At payoff the
   danger wedge pinches out and the whole plot is equity -- the arc IS the story, and the stacked
   reading answers the first attempt's dead-space finding. In-region identity labels ("Equity",
   "Debt") draw in secondary ink on the canvas; a top legend with line keys names the two line
   series (color is never the only signal).
2. **Series colors follow the cockpit split-view convention:** market value = accent, secured debt =
   danger; debt splits solid confirmed history vs dashed committed projection via
   `ShekelChart.splitSegment`, with the shared Today marker at the boundary.
3. **The value line is never solid** -- it is assumption end to end: short dots (`[2,3]`) at 45%
   alpha flat-carrying today's anchor before Today, the family projection dash (`[6,5]`) compounding
   at the appreciation rate after. The caption under the chart states this honestly and absorbs the
   old in-band helper copy's edit pointer (market value is trued up on the Accounts card).
4. **Axis grammar:** `%b %Y` labels thinned to 13 ticks max, no rotation (the loan band's
   convention); y ticks whole-dollar `formatMoney` with the zero gridline emphasized.
5. **Tooltip carries the exact figures** (family rule, decision 3 amendment): index mode, line keys,
   rows Market value + Secured debt, footer Equity.
6. **Zero-rate state:** the chart still renders (value flat-carries forward too); the caption swaps
   to name the unset rate and link to the Parameters card -- it reads as "unset," never "broken."
7. **Loan-less / paid-off fallback horizon: 10 years** of appreciation-only projection (confirmed at
   the pick); value line + origin fill only, no legend.
8. **Palette validation** (dataviz skill validator): the light pair passes all checks; dark
   `--shekel-danger` `#FB6D63` sits just above the categorical lightness-band ceiling -- the S16
   AA-as-chip-text lift -- with CVD separation from accent at 4x the floor and 3:1+ surface
   contrast. Kept: the token layer is the contract, and identity never rides on color alone (see 1).
   Recorded so nobody "fixes" the token for a chart-only reason.

Scratch artifacts (`property_equity_explore.html` + shots) are RETAINED past Loop B by developer
instruction (2026-07-12) for the acceptance check of this build; durable copy at
`/home/josh/projects/shekel_theme/property_equity_loop_a/` (outside the repo per the anti-anchoring
rule). Delete after the developer's check. Deferred defects from the build are registered in
`property_detail_followups.md` (single-resolution DRY refactor + multi-loan date alignment).

### Loop B build contract (pinned 2026-07-11, at the Loop A lock)

Two commits, split by the model discipline; either half is resumable from this contract alone.

**Commit 1 -- data (Opus scope).** `accounts.property_detail` gains the chart context. No template /
JS / CSS changes in this commit.

- **Series JSON** (one `chart_json` string, serialized like the loan band's `build_band_chart`;
  floats only at this boundary): `labels` (list[str], `%b %Y`), `value` (list[float]), `debt`
  (list[float]), `equity` (list[float]), `current_index` (int -- count of merged confirmed-history
  months, the solid/dashed + Today boundary). For the no-loans fallback `debt` and `equity` are
  empty lists and `current_index` is 0.
- **Extra context:** `chart_state` in `{"standard", "zero_rate", "no_loans"}` (drives the caption
  variant; `zero_rate` when the appreciation rate is the zero sentinel), `has_equity_chart` (False
  only when there is no market value anchor to chart), and the display rate the caption prints.
- **Debt series:** per secured loan, confirmed history + committed forward via the loan band's own
  producers (`compute_payoff_scenarios` / `confirmed_loan_view`); merge multiple loans by summing
  per-month balances on the longest series' monthly axis, padding shorter series with the literal
  0.00 post-payoff balance -- the `_build_chart_series` pattern in `app/routes/loan/_helpers.py`,
  HOISTED to a shared home both callers import (do not duplicate it, do not leave loan importing
  from accounts or vice versa).
- **Value series:** flat at today's anchor for indices `<= current_index`; after, compound the
  anchor via the growth engine's `(1 + rate) ** (days / 365)` primitive
  (`growth_engine.period_return_rate`) against each month's date -- exactly one appreciation formula
  in the codebase. No-loans fallback: 120 months from today.
- **Equity:** per-month `Decimal` `value - debt`, computed in the route/service, never in JS.
- **Edge cases owed tests:** multi-loan merge with different payoff dates; a loan with no confirmed
  history (`current_index` 0); all secured loans paid off -- no outstanding schedule means the
  10-year fallback and the `no_loans` state, since the developer confirmed the fallback covers
  "paid-off / loan-less" together; zero-rate flat value; exact-`Decimal` assertions per testing
  standards.

**Commit 2 -- visual (Fable scope).** `property_detail.html` band gains the `pulse-chart` canvas
(`data-chart`, `role="img"`, aria-label) + the caption line under it; new
`app/static/js/property_detail.js` renders the locked direction A anatomy on the shared
`chart_theme.js` helpers (`splitSegment` / `todayMarkerPlugin` / `hexToRgba` / `formatMoney`);
caption styling joins `accounts.css` (`.acctd-chart-cap`, token-only). Both themes, both viewports,
live-verified via `tests/manual/shoot.py`; then the scratch mockup is deleted.

### As-built (2026-07-12): the date-anchored rebuild + three-tier debt line

The Loop B build contract above was DATA-superseded before Fable shipped. Three correctness defects
(H1 paid-off fallback unreachable, H2 front-aligned multi-loan merge, H3 fabricated past values)
plus a double-resolution DRY issue were reproduced against the real producers and closed by a full
rebuild: `docs/plans/historical/implementation_plan_property_equity_chart_rebuild.md`. The producer
is now PURE and reasons on a CALENDAR-DATE axis anchored at `today`, so the JSON contract changed
from the pinned version above:

- `current_index` -> **`today_index`** (the month containing today: the value flat/compound split
  and the Today marker). It is NOT the confirmed/projected boundary -- recorded history can end
  before today (finding M1).
- Added **`debt_tier`** (per month: `estimated` / `confirmed` / `projected`) and
  **`has_estimated_debt`** (context flag gating the caption's dotted clause). The
  `_build_chart_series` hoist was DROPPED (the band and property merges are genuinely different
  operations); the producer owns its own date-aligned calendar merge.

The Loop A lock's direction A (stacked shares) and its fill/color/axis/tooltip anatomy (items 1-8
above) all still hold. What the rebuild ADDED to the visual, ruled at the Loop B gate (section 12 of
the plan, all resolved 2026-07-12):

1. **Three-tier debt line, not two.** The debt line is styled by `debt_tier` per segment
   (`debtSegment`, keyed off `ctx.p1DataIndex`), NOT by `splitSegment` at a single index: solid
   `confirmed`, dashed `projected`, and **faint dots (`[2,3]` at 45% danger) for `estimated`** --
   the pre-tracking contractual back-projection for a mid-life-imported loan. Dots reuse the value
   line's assumption texture, so across the whole chart dots = estimate, solid = recorded, dashes =
   plan.
2. **Faint "Tracking start" seam marker** at each `estimated -> non-estimated` transition (muted,
   finer-dashed than Today), **suppressed within 100px of the Today marker** so a near-today seam
   (the real single-mortgage case, records begin ~3 months before today) yields to Today rather than
   colliding.
3. **Caption + aria** name all three textures (dotted clause gated on `has_estimated_debt`); the
   `no_loans` caption reworded to "no outstanding secured debt" (accurate for paid-off-but-linked).

**Correctness fix found at the accept-check (commit `936db7df`).** A loan's resolved schedule is not
one-row-per-calendar-month; the real mortgage had a rowless July between a June and an August row.
The debt sum contributed `$0.00` on such gap months, collapsing the debt line to zero at today
(cliff + phantom equity spike, reconciliation broken). `_dense_month_balances` now forward-fills the
prior balance across gap months within a loan's span. Re-verified to the cent on 203 Chalmers Dr.

Live-verified 2026-07-12 (real prod-clone data, both themes, desktop + mobile); acceptance shots at
`/home/josh/projects/shekel_theme/property_equity_loop_a/shots-live/property_rebuilt__*`. Scratch
mockup RETAINED per the developer's 2026-07-12 instruction (delete after the developer's check).

## Surface 7: Balance history (2026-08-10, plan step X-f2-b)

New surface on the cash detail page, so it re-enters the protocol here: this audit section, then
Loop A, then Loop B. Plan step **X-f2-b** (shipped at `a41b5ebf`, ruling **R-EV**); it closes
findings **N-205**, **N-204** and **N-206**.

### The surface's job

"What balances have I told this account it held, and how far off were my records each time?" The
cash twin of the loan dashboard's Balance anchors card (`loan/dashboard.html:427-478`, producer
`loan_posting_service._display.loan_balance_anchor_history`), which renders As of / Recorded /
Ledger / Drift per anchor.

### What exists today, and the divergence

- **Nothing durable.** An AST pass over `AccountAnchorHistory` confirms it: no template reads the
  table except the governing-assertion caption (`anchor_as_of`, `_cash_band.html:21-22`), which by
  definition is the CURRENT assertion and never a back-dated one. Verified again 2026-08-10 by
  grepping every non-model reference; every other consumer is a service.
- **The only evidence a back-dated assertion landed is an 8s toast**
  (`accounts/_anchor_recorded_toast.html`, `data-bs-delay="8000"`). A user who looked away has no
  retrieval path at all (finding **N-205**, WCAG 2.2 SC 2.2.1 shape).
- **A second acknowledgement destroys a still-visible first** (finding **N-206**):
  `hx-swap-oob="true"` is an outerHTML swap of `#anchor-ack-mount`, and nothing calls `dispose()`,
  so each detached toast is retained in Bootstrap's element-keyed map with its autohide timer still
  armed.
- **The acknowledgement is keyed on the wrong question** (finding **N-204**):
  `_submission_is_the_coverage_boundary` asks "was the submitted day the coverage boundary", so
  re-confirming an unchanged balance on a LATER day appends a row, moves `reconciled_through`, and
  takes the prompt branch, whose prompt is `""` whenever nothing is outstanding. The write lands
  with no acknowledgement and an unchanged figure.
- **Verdict: add.** A durable Balance history card below the band. It needs NO new producer:
  `cash_ledger.walk_cash_ledger(...).anchor_corrections` already carries all four columns
  (`observed_on`, `anchor.anchor_balance`, `balance_before`, `delta`).

### Measured on the production clone (2026-08-10, 79 assertion rows)

Run through the shipped `walk_cash_ledger`, not read off the table, so the Ledger and Difference
columns are the ones the card would actually render.

| account | kind | assertions | zero-difference | gross correction | net correction |
| ------- | ---- | ---------- | --------------- | ---------------- | -------------- |
| Checking | PLAIN | 57 | 2 | $16,656.94 | -$850.74 |
| Fidelity Savings (HYSA) | INTEREST | 1 | 0 | $4,863.56 | $4,863.56 |
| Fidelity Money Market | INTEREST | 3 | 0 | $4,909.51 | $4,909.51 |

Four facts the design has to answer, none of them visible from the loan twin, which renders one row:

1. **Scale.** Checking carries 57 assertions over 133 days, about 13 a month and growing. The loan
   card's "render every row" shape does not transfer.
2. **A day is not a key.** Three days carry more than one assertion (2026-04-15 carries THREE,
   2026-05-07 and 2026-06-03 two each). On 2026-04-15 the three read $1,172.44, $1,133.47 and
   $1,087.61, and each row's Ledger is the previous row's Recorded, so they are successive
   corrections and all three have to render, in walk order.
3. **The OPENING row's Ledger is not zero and is not a correction.** Checking's opening reads
   Recorded $2,746.58, Ledger $2,057.42, Difference $689.16. That $2,057.42 is the sum of settled
   rows dated BEFORE the opening assertion, replayed from a zero seed --
   `cash_ledger._walk.dated_deltas` calls it "not a balance the account ever had" and what a reader
   should show there is OPEN finding **N-37**. The gap of $689.16 is the account's opening equity,
   the figure plan step X-f5 books, so captioning it as a difference would read "my records were off
   by that much the day I started", which is false.
4. **A modelled account's Difference is a model-vs-market gap, not untracked spend.** The HYSA's
   single row is $4,863.56 on a $5,363.56 balance (91 percent of it), and the Money Market's two
   non-opening rows are $15.01 and $15.24 -- accrued interest, which a "money Shekel has not
   accounted for" caption would misname. This is the scope argument `records_balance_at` already
   makes for X-f2-a's preview (finding **N-213**).

A fifth fact is about the card's own job rather than its figures:
**sorted by `observed_on`, a back-dated row does not appear at the top.** A balance recorded today
for 2026-07-15 lands between the 07-10 and 07-16 rows, 40 rows down. Since the card exists to give a
back-dated write a retrieval path (N-205), the ordering alone does not discharge it.

### Loop A rulings (developer, 2026-08-10 -- LOCKED)

1. **Depth: the 12 most recent, with Show all.** The card shows twelve rows and a disclosure expands
   the rest in place. The twelve are the most recently RECORDED, rendered in `observed_on` order, so
   fact 5's back-dated row is always among them; the header names what is shown.
2. **Scope: one card, every cash kind, the reconciliation pair nullable per row.** Rather than two
   card variants, `ledger` and `difference` are `Decimal | None` and render `--` where they have no
   agreed meaning -- which makes the opening row (ruling 3) and a modelled account ONE rule with one
   expression. The two columns and the caption are gated by a `reconcilable` flag read from
   `classify_account`, never inferred from the rows: a PLAIN account with exactly one assertion has
   only its opening, whose pair is withheld, and an inferred flag would hide the columns on every
   freshly created checking account. Leaves **N-213** open and undecided.
3. **The opening row renders `--` for Ledger and Difference**, badged Opening -- the loan twin's
   treatment, for fact 3's reason.
4. **The acknowledgement re-keys onto "did anything visible happen".** The reconcile prompt keeps
   its own predicate (`_submission_is_the_coverage_boundary`), which is correct for it; the toast
   fires when the governing balance did not move AND the prompt did not open. They stay mutually
   exclusive as a consequence rather than as a construction. N-204's defect was welding the
   acknowledgement to the prompt's complement, not the prompt's own question.
5. **Copy branches on the write outcome.** Ruling R-EQ re-asserting the governing balance for the
   same day writes nothing and rolls back, and that state now reaches the toast, so `COMMITTED` says
   "Balance recorded" and `UNCHANGED` says the figure already matched. Without it the re-key ships a
   toast that lies.
6. **Placement: below the Outstanding purchases card.** The band is the answer, the reconcile panel
   is the task, the history is the record.
