# Analytics Audit

Per-surface diagnosis of `/analytics` for the Fable 5 overhaul, per the shekel-design skill Step 1.
Status: diagnosis complete 2026-07-04. **Gate A LOCKED 2026-07-04** (all six rulings recorded in
"Rebuild decisions" below). **Loop A COMPLETE for all four pills 2026-07-04** (records below; locked
anatomy in "Locked anatomy"). Calendar slice: P1 (data) AND P2 (presentation) COMPLETE 2026-07-04;
as-built records below. Next: calendar slice P3 (developer acceptance on real data), then slice 2
(Taxes). Line references are as of `dev` @ `0d9c3fe8` (2026-07-04); re-verify before acting on them.

## Developer context (2026-07-04 session)

The developer is a Data Manager and former Data Analyst and wants this page to be something to be
proud of; a total overhaul is explicitly on the table. Per tab:

- **Calendar:** "the most potential... if the calendar was done right I think it could rival the
  grid for the most useful page in the app."
- **Year-End:** built as a W-2 peek, but he has not filed taxes since building Shekel. "The most
  useful feature would be an estimate of my tax refund."
- **Variance:** "the least useful. Everything shows zero variance most of the time." Doubts it needs
  its own tab.
- **Trends:** could be useful, but Shekel is too new for reliable trends; "not confident the numbers
  are entirely accurate."

## The screen's job (proposed)

The grid answers "which paycheck covers what" and the dashboard answers "am I healthy right now."
Analytics should own the three questions neither of those can:

1. **Day granularity** -- which day does each dollar land or leave, and what is my balance that day?
   (Calendar)
2. **Retrospective truth** -- where did money actually go? (statements, spending, trends)
3. **Annual tax position** -- where will I stand with the IRS at filing time? (year-end, refund)

## Inventory

The page ships SIX HTMX lazy-loaded tabs, not four: Calendar, Year-End, Variance, Trends, plus the
Step 5 confirmed-ledger pair, Income Statement and Balance Sheet (`analytics.html:16-77`).

| File | Lines | Role |
| ---- | ----- | ---- |
| `app/routes/analytics.py` | 755 | Blueprint: 7 handlers + shared window/CSV helpers |
| `app/services/calendar_service.py` | 616 | Calendar tab |
| `app/services/budget_variance_service.py` | 452 | Variance tab |
| `app/services/spending_trend_service.py` | 637 | Trends tab |
| `app/services/year_end_summary_service/` | 2285 (11 files) | Year-End tab |
| `app/services/ledger_report_service/` | 1390 (5 files) | Income Statement + Balance Sheet |
| `app/templates/analytics/` | 8 templates | Shell + one partial per tab |
| `app/static/css/analytics.css` | 237 | Steel Ink tokens throughout (one stray raw rgba shadow, `:91`) |
| `app/static/js/` | `calendar.js`, `chart_variance.js`, `chart_year_end.js` | Day detail, two charts |
| `tests/test_routes/test_analytics.py` | 2692 | Behavior pins |
| `tests/test_routes/test_c30_analytics_ownership.py` | 775 | IDOR pins |

Compliance notes: no Jinja money arithmetic (display-only formatting and boolean gating); `float()`
only at chart JSON boundaries; Steel Ink token layer already adopted; CSV export exists for
calendar, year-end, and variance windows (preserve or retire explicitly at the gate, not by
accident).

## Shared infrastructure facts

- **Cash-flow-set scope** (since `credit_card:CC-4-3`, 2026-09-18; this bullet read "Checking-only
  scope" over `resolve_analytics_account` until then). Calendar and Spending both read the
  paycheck's items across the owner's cash-flow SET -- checking and its cards: the set's own rows
  through `cash_flow_set.own_rows_clause` and, since `balance:X-bi-6-1b`, one LEG per transfer the
  set touches, both from the one loader `cash_flow_set.load_plan_items` since plan#2130 (each
  screen's scope stated once in a `PlanScope`; it was `set_transfer_legs_in_periods` beside each row
  query, and the Spending report's calendar-span arm with its own join; before that
  `paycheck_rows_clause`, which carried a transfer's near shadow row and was deleted with its last
  reader) -- behind ONE member's balance line. The CALENDAR resolves that line through
  `account_resolver.resolve_analytics_cash_flow_set`: `?account_id=` names the balance member, an
  owned cash-flow account outside the set gets its own single-account calendar, a refused id is a
  404, and the default is the set's primary. SPENDING takes no `?account_id=`: its scope is the
  grid's `resolve_cash_flow_set` with no override, so money on an account outside the set is
  invisible there and no pill says so on screen. Variance and Trends are retired URLs (folded into
  Spending at Slice 3, their URLs retired at Slice 4: `routes/analytics.retired_tab`).
- **Baseline scenario only** (all six tabs).
- **Status predicates** (`app/utils/balance_predicates.py`): calendar uses
  `balance_contributing_clause()` = Projected + Settled minus Credit/Cancelled (`:374-399`);
  variance excludes Credit/Cancelled (`:82-109`); trends and year-end spending use
  `settled_status_ids()` = Paid/Received/Settled (`:112-147`).

## The structural finding: three data semantics, unlabeled

The page mixes three kinds of "number" with no visual distinction:

| Semantics | Surfaces |
| --------- | -------- |
| Projection mixed with actuals in one figure | Calendar day cells and month totals |
| Actuals only (settled rows) | Trends; Year-End spending, transfers, timeliness |
| Pure model (never reads a real transaction) | Year-End income/taxes, net worth, savings progress |
| Confirmed double-entry ledger | Income Statement, Balance Sheet |

A former data analyst reads a figure and asks "is this measured or modeled?" This page never
answers. Whatever survives Gate A, every surviving figure gets an explicit measured / modeled /
mixed treatment (caption or chip), per the design principle "a figure and its caption never
disagree."

## Tab 1 -- Calendar

**Should show:** the daily texture of cash flow: what lands and leaves each day, and what that does
to the balance.

**Actually produces:** month grid (Sunday-start) where each day cell shows the day's summed income
(green) and expense (red) with paycheck-day tint and today outline; pre-rendered per-day detail
tables (name / category / amount / Paid-or-Projected badge) toggled by `calendar.js`; month summary
row of Income / Expenses / Net / projected month-end balance (`calendar_service.py:140-615`,
`_calendar_month.html`). Year view: 12 month cards with the same totals. Attribution: `due_date`,
else the period's `start_date` (`:516-540`). Row set is Projected plus Settled combined
(`_query_transactions_for_range`, `:259-328` -- the locked Choice-2 semantic). Month-end balance is
`balance_at.cash_balance_at(last_day)` (`:578-615`), a projection even for past months.
`large_transaction_threshold` defaults to 500 (`analytics.py:148`).

**Divergence:** no balance anywhere except one month-end figure -- the single most valuable daily
fact (what is my balance that day; where is the trough) is absent. Projected and settled amounts are
summed into one undifferentiated figure per cell; only the click-detail badge distinguishes them. No
link from a day or entry to the grid/period that owns it.

**Proposed verdict: KEEP + REBUILD as the flagship surface.** Day cells gain the running projected
end-of-day balance from the `balance_at` seam (needs a one-pass daily-series producer; 30 separate
`cash_balance_at` calls per render is the naive trap), trough-of-period flagged, due items as chips
consistent with the grid's status-chip vocabulary, paycheck markers kept, elapsed vs remaining split
in the month summary so measured and modeled money are not summed silently.

## Tab 2 -- Year-End

**Should show:** the year's financial story; per the developer, the headline should be the expected
tax refund.

**Actually produces:** seven sections (`year_end_summary_service/_orchestrator.py:40-139`):
income/taxes with W-2 box labels, mortgage interest, spending by category, transfers, net worth (12
monthly `balance_at` endpoints + chart), debt progress, savings progress, payment timeliness.

**Divergence (the big one):** the income/tax section is a pure simulation. `_compute_income_tax`
(`_income_tax.py:19-80`) runs `paycheck_calculator.project_salary` over ALL pay periods of the
year -- no settled filter, never reads a received paycheck's `actual_amount`, never reconciles
against reality. In July it shows a full-year model dressed in W-2 box labels. Mortgage interest is
the honest exception (confirmed ledger actuals + projected remainder, `_income_tax.py:190-263`).
Spending/transfers/timeliness are settled-only (real). Net worth, debt, and savings sections now
duplicate surfaces the cockpit and retirement pages own. And nothing computes filing-time liability,
so the one number the developer wants (refund) does not exist.

**Proposed verdict: REBUILD as a Taxes tab.** Refund estimate hero (see option space below), W-2
preview retained beneath it on a hybrid basis (elapsed periods at calibrated actual rates, remainder
projected, labeled as such), mortgage interest kept (Schedule A input). Net worth / debt / savings
sections: DROP from analytics (cockpit and retirement own them) unless the developer wants a slim
annual-recap strip.

## Tab 3 -- Variance

**Should show:** where reality diverged from plan.

**Actually produces:** group -> item -> transaction tree of Estimated / Actual / Variance for a
pay-period, month, or year window, plus a grouped bar chart (`budget_variance_service.py:147-188`,
`_variance.html`, `chart_variance.js`).

**Divergence -- zero by construction, confirmed at `_compute_actual` (`:403-415`):** every unsettled
row returns `actual := estimated` (variance exactly 0); every settled row with no entered
`actual_amount` falls back to `estimated` (variance exactly 0). Only settled rows with an explicitly
entered, different actual can ever be nonzero. The tab does not compare a budget to reality; it
compares each row's estimate-at-entry to its actual-at-settle, a number pair that is identical for
most rows most of the time. The developer's "everything shows zero" is the design working as coded,
not sparse data. Shekel deliberately has no category budgets, so a classic budget-vs-actual can
never exist here; the premise of a standalone tab is faulty.

**Proposed verdict: REMOVE the tab.** The one real signal it contains -- settled rows whose actual
differed from the estimate -- survives as a compact "surprises" list on the Spending surface (and is
the seed of a future period-recap analytic). Retire the variance CSV export explicitly or move it
with the kernel.

## Tab 4 -- Trends

**Should show:** which categories are drifting up or down.

**Actually produces:** settled-only expenses (`_query_paid_expenses`,
`spending_trend_service.py:281-321`), completed periods only (`:240-278`), per-period per-category
series with recent-half vs prior-half change (`:396-433`), materiality floor
$20/period, new baseline floor $5, top-5 up/down cards, group drilldown, all-items table,
data-sufficiency banner (&lt;3 distinct paid months = insufficient; &gt;=6 = 6-month window; else
preliminary).

**Divergence:** the engine is the most trustworthy of the four legacy tabs -- it never mixes
projections in. The developer's distrust is better explained by (a) the sufficiency gate counting
distinct calendar MONTHS while the series it then builds is per PAY PERIOD (`:207-237` vs
`:373-393`), a unit mismatch that can flip the banner independently of the window; (b) checking
account scoping silently excluding spend from other accounts; (c) half-window averages over a young
dataset being legitimately noisy, which the banner already partially concedes.

**Proposed verdict: KEEP the engine, MERGE the presentation** into a single Spending surface
(category breakdown for a chosen window + trend deltas as a column/badge, not a separate tab), fix
the month/period unit mismatch, and label the account scope.

## Tabs 5 and 6 -- Income Statement and Balance Sheet

Confirmed-ledger reporting (`ledger_report_service/`), shipped with Step 5 (PR #58/#59): income
statement over pay-period/month/year windows; balance sheet as-of a date with derived retained
earnings and a two-part trial-balance tie-out. These are the only surfaces on the page whose numbers
are arithmetically guaranteed (SUM = 0 trigger + tie-out).

**Proposed verdict: KEEP, group as one "Statements" surface** (sub-toggle between the two) so the
measured end of the page reads as one thing. No data work needed.

## Candidate analytics option space

What would a pay-period budget app's analytics page most usefully contain? Mapped against Shekel's
actual data assets (2-year projections, settled/actual splits, `balance_at` seam, confirmed posting
ledger, full salary/tax simulation with seeded 2025-2026 bracket tables, loan principal/interest
actuals):

**Build now (recommended):**

1. **Daily cash-flow calendar** -- per-day end balance, period trough, due chips. Data exists
   (`balance_at` seam); needs a daily-series producer for one-pass performance.
2. **Tax refund estimate** -- the missing piece is an annual LIABILITY calculator (brackets are
   already seeded per user with standard deduction + CTC/ODC amounts; `auth_service.py:297-446`).
   Refund = total withheld (elapsed actuals + projected remainder) minus liability. Feasibility
   detail below.
3. **Unified Spending surface** -- one window-picker view: category breakdown (from the year-end
   Section 3 logic generalized to any window) + trend deltas (Trends engine) + the settled surprises
   list (Variance kernel).
4. **Statements** -- already shipped; group and keep.

**Later (real value, not first):**

- **Period recap / projection accuracy** -- for each closed period: projected end balance vs settled
  reality, median absolute projection error over trailing periods. Data-analyst catnip and the
  honest successor to Variance; needs a snapshot-at-close design decision (what was "the projection"
  before settlement mutated it), so it is its own gated feature.
- **Interest analytics** -- interest paid (loan ledger REAL splits) vs interest earned over time.
- **Savings rate per period** -- income vs outflow ratio trend.
- **Seasonality views** -- month-over-month heatmaps; needs 2+ years of settled data to be honest.

**Rejected:**

- **Category budget vs actual** -- Shekel has no category budgets by design; this was Variance's
  false premise.
- **Merchant/payee analytics** -- no merchant data model exists.
- **Generic fintech decoration** (score gauges, streaks, confetti) -- against the design language.

## Tax refund estimate: feasibility (mapped 2026-07-04)

**Exists today:** filing status, qualifying children / other dependents, W-4 step 4 fields per
salary profile (`salary_profile.py:70-107`); per-user seeded bracket sets for 2025 + 2026 with
standard deduction and credit amounts (`auth_service.py:297-415`); FICA configs (`:417-433`); NC
flat state config (`:435-446`); Pub 15-T percentage-method withholding engine (`tax_calculator.py`);
one calibration stub per profile storing actual federal/state/SS/Medicare from a real pay stub with
derived effective rates (`calibration_override.py:81-93`).

**Missing:** any annual liability computation (nothing anywhere computes tax owed at filing);
persisted per-paycheck actual withholding (received paychecks store net only,
`transaction.py:170-171`); filing-time credits beyond CTC/ODC as withholding offsets; non-wage
income and itemized-vs-standard election (mortgage interest is computed but never fed into a
liability); household/joint combination across profiles; state brackets for non-flat states
(irrelevant for NC).

**Shape of the identity** (single filer, NC): Box 1 wages = gross - pre-tax deductions. Federal
taxable = Box 1 - standard deduction (2026 single: `$16,100`, already seeded). Liability L = seeded
brackets applied annually to taxable, minus child/dependent credits. Withheld W = sum of per-period
federal withholding: elapsed periods at calibrated actual rates, remaining periods projected.
**Refund = W - L** (negative = owed). Same identity for NC state with the flat rate and the
`$12,750` state standard deduction. No new tax constants are required for v1.

**Two-stage proposal:** v1 = modeled estimate from the calibrated projection engine + new annual
liability service, clearly labeled as modeled, with an assumptions box (filing status, std
deduction, credits, calibration date). v2 = a YTD checkpoint (user types YTD gross/fed/state/SS/
Medicare from a real pay stub; estimate re-anchors on measured withholding). v2 is a small table
plus a form, and turns the biggest error source (modeled withholding) into measured data.
(Superseded by Gate A ruling 5: both stages ship as ONE arc.)

**Liability basis ruling (developer, 2026-07-04, worked-example fork):** the filing-time liability
counts W-4 Step 4(a) additional income as REAL income (in both the federal and NC bases) and
EXCLUDES Step 4(b) additional deductions -- 4(b) stays a withholding-only hint because the Schedule
A check owns the itemize-vs-standard election at filing. NC base = wages gross + 4(a) - pre-tax (the
same AGI-style base the withholding path already uses via ``taxable_biweekly``,
``paycheck_calculator.py:239``), minus the NC standard deduction inside ``calculate_state_tax``.
CTC/ODC are applied as nonrefundable (liability clamps at zero; ACTC refundability out of scope
v1) -- disclosed in the assumptions box. Worked anchor (single, NC, 2026; salary 110,000, pre-tax
12,000, 4(a) 1,200, 4(b) 3,000): federal taxable 83,100 -> liability 12,994.00; NC (98,000 + 1,200 -
12,750) x 3.99% = 3,449.36. Build process ruling: T-P1..T-P3 built by Opus subagents with review +
verification between phases (the dashboard/salary pattern).

## Loop A record (Spending and Taxes slices)

- **Round 1 (2026-07-04, ledger-leaning):** Taxes A "1040 ledger" / B "withheld-vs-tax bars";
  Spending A "ranked ledger" / B "bars + movers". Superseded the same day by the display grammar
  ruling above - the recommendations had drifted tabular.
- **Round 2 (2026-07-04, cockpit grammar):** Taxes A "Pace" (cumulative withholding vs tax line) / B
  "Coverage" (linear meters); Spending A "Rhythm" (monthly columns + share bars) / B "Race"
  (per-category sparkline rows). Developer rulings:
  - **Spending:** Where It Went (share bars + trend chips) KEPT; Top Movers KEPT; the per-category
    breakdown rows KEPT; Monthly Rhythm card DROPPED (unclear what it showed; the vs-May /
    vs-6-month-average hero chips carry that job). Sparklines liked tentatively but readability
    doubted - round 3 compares trend-visual options head to head.
  - **Taxes:** BOTH chart centerpieces ruled unclear. Developer principle recorded: "I don't want
    charts for the sake of having a chart" - reassess whether any chart earns its place, else the
    tab is a ledger. Assessment: the refund is a scalar plus a derivation, so the v1 body is the
    cockpit hero band (hero + chips + YTD checkpoint + assumptions) over the derivation ledger, W-2
    preview, and Schedule A cards - consistent with the display ruling's exception for surfaces
    where the table IS the artifact. DEFERRED, not built: an "estimate convergence" line (the refund
    estimate re-plotted at each saved YTD checkpoint) is the one genuinely chart-worthy tax
    question, but it needs checkpoint history that will only exist after the feature has been used
    across several stubs. Revisit post-ship.
- **Round 3 (Spending trend visuals, built 2026-07-04):** one converged layout (hero band; category
  rows carrying share bar + amount + trend visual + delta chip; Top Movers and Estimate Surprises in
  the rail); four directions varying ONLY the trend-visual cell: (a) monthly sparkline, (b) monthly
  sparkbars, (c) half-window slope pair - two dots joined, prior-half vs recent-half per-period
  average, which is EXACTLY the metric the trend engine computes (the sparkline/sparkbar variants
  show monthly totals, an approximation of the per-period half-window chip), (d) chip-only with a
  direction glyph.
- **Round 3 outcome (2026-07-04): SPARKLINES picked** ("I like the sparklines the best"). Build
  rules adopted to resolve the honesty and readability caveats raised during the round:
  1. The sparkline is sourced from the SAME per-period category series the trend engine already
     computes for the chip (not monthly totals), so the visual and the chip share one data source
     and cannot disagree.
  2. Flat-guard the per-category auto-scale: when a series' range is a small fraction of its mean,
     render the line flat/centered instead of stretching noise to full cell height (the Housing
     +0.4% exaggeration observed in round 3).
- **Taxes round 3 outcome (2026-07-04): ACCEPTED** ("Taxes looks good") - merged view: cockpit hero
  band (refund hero + chips, YTD checkpoint, assumptions) over the derivation ledger, W-2 preview,
  and Schedule A. No chart in v1; the estimate-convergence line stays deferred until checkpoint
  history exists.

## Locked anatomy (Loop A complete, 2026-07-04)

| Pill | Locked composition |
| ---- | ------------------ |
| Calendar | Summary strip (balance today / so-far / remaining / projected end / month trough); month flow strip (EOD balance line, measured solid vs projected dashed, threshold from settings, payday dots, trough dot, Today marker); day cells with up to 3 named flows + "+N more" residual and EOD balance hero |
| Spending | Hero band (spent / vs prior / vs 6-mo avg / payment timing); Where It Went rows: share bar + amount/% + per-period sparkline (flat-guarded) + delta chip; rail: Top Movers + Estimate Surprises (Variance kernel) |
| Statements | One pill, Income Statement / Balance Sheet toggle; stat strips + sectioned statements; tie-out banner; confirmed-ledger chip |
| Taxes | Hero band (refund hero + federal/NC/effective/marginal/next-stub chips, YTD checkpoint card with update-from-stub, assumptions card); derivation ledger; hybrid W-2 preview; Schedule A check. No chart in v1 |

## Proposed target IA

Four pills replace six tabs:

| Pill | Contents | Source semantics |
| ---- | -------- | ---------------- |
| Calendar | daily cash-flow ledger, trough, due chips | mixed, split visibly |
| Spending | category breakdown + trend deltas + surprises | measured (settled) |
| Statements | income statement + balance sheet | confirmed ledger |
| Taxes | refund hero + hybrid W-2 preview + Schedule A | modeled, labeled, calibrated |

Cross-cutting fixes regardless of direction: state the account scope on screen; measured / modeled /
mixed chips on every surface; decide each CSV export's fate explicitly.

## Gate A questions for the developer

1. **Calendar:** confirm flagship rebuild; is per-day end-of-day balance the right hero for a day
   cell (vs in/out totals as today)?
2. **Variance:** confirm tab removal; does the surprises kernel live on Spending, or wait for the
   period-recap feature?
3. **Trends:** confirm merge into Spending (no standalone tab)?
4. **Year-End:** confirm slimming to Taxes; drop net worth / debt / savings sections from analytics,
   or keep a one-line annual recap strip?
5. **Refund estimate:** v1 modeled now with v2 YTD checkpoint as a follow-up -- or is v2's measured
   anchoring required before the number is trustworthy enough to show at all?
6. **Statements:** one pill with an internal toggle, or keep two pills?

## Rebuild decisions (Gate A)

LOCKED 2026-07-04. The developer ruled on all six gate questions:

1. **Calendar: flagship rebuild CONFIRMED.** Per-day end-of-day balance IS the day-cell hero
   (replacing today's income/expense pair as the primary figure).
2. **Variance: tab REMOVED.** The settled-surprises kernel moves to the Spending surface (the
   audit's recommended default, not contradicted at the gate).
3. **Trends: folded into Spending.** No standalone tab. The engine survives; the month/period
   sufficiency unit mismatch is fixed as part of the merge.
4. **Year-End: slimmed to Taxes.** Net worth, debt progress, and savings progress sections are FULLY
   DROPPED from analytics -- the accounts cockpit and retirement pages own those stories. No annual
   recap strip.
5. **Taxes: built as ONE arc, no v1/v2 staging.** The annual liability service, the refund hero, the
   hybrid W-2 preview, AND the YTD checkpoint (user-entered YTD gross/federal/state/SS/ Medicare
   from a real pay stub, re-anchoring withholding on measured data) ship together.
6. **Statements: one pill** with an internal toggle between Income Statement and Balance Sheet.

Target IA is therefore the four-pill layout from "Proposed target IA": Calendar / Spending /
Statements / Taxes. Cross-cutting fixes ride along: on-screen account-scope label, measured /
modeled / mixed treatment on every surface, explicit per-export CSV decisions at each slice.

### Build order (proposed sequencing, one slice at a time)

1. **Slice 1 -- Calendar** (flagship): daily end-of-day balance series producer (one-pass, via the
   `balance_at` seam; Opus scope), trough flag, due chips, elapsed-vs-remaining month summary.
   Phases P1-P3 in "Loop B build plan (Calendar slice)" below.
2. **Slice 2 -- Taxes** (replaces the Year-End pill; one arc per Gate A ruling 5):
   - T-P1 (Opus): annual liability service - applies the seeded bracket tables ANNUALLY to (gross -
     pre-tax - standard deduction), minus CTC/ODC credits; federal plus NC flat state;
     hand-confirmed assertions per rule 5.
   - T-P2 (Opus): YTD checkpoint - new small table (per profile: as-of stub date, YTD gross /
     federal / state / SS / Medicare), migration both directions, entry form + update route;
     withholding-to-date = measured checkpoint + calibrated projection for the remainder.
   - T-P3 (Opus): refund producer (refund = withheld - liability, federal and state) + hybrid W-2
     preview producer (measured through the checkpoint + projected remainder) + Schedule A check
     (mortgage interest via the loan ledger reader + state tax + property tax vs the standard
     deduction).
   - T-P4 (Fable): the Taxes page per the locked anatomy (hero band + chips + checkpoint +
     assumptions cards over the derivation ledger, W-2 preview, Schedule A). NO chart in v1.
   - T-P5: acceptance; the year-end CSV's fate is decided here (it dies with the tab unless the
     developer wants a tax-summary export).
3. **Slice 3 -- Spending** (retires the Variance and Trends pills):
   - S-P1 (Opus): window producer generalizing the year-end category breakdown to
     pay-period/month/year windows; trend deltas from the existing engine with the month/period
     sufficiency unit mismatch fixed; per-category PER-PERIOD sparkline series (the same series as
     the chip, flat-guarded); surprises producer (settled rows where actual differs from estimate,
     capped list + net); movers; hero figures (vs prior window, vs 6-window average, payment
     timing).
   - S-P2 (Fable): the Spending page per the locked anatomy. The variance CSV retires with its tab
     (explicit decision; revisit at build if the developer objects).
4. **Slice 4 -- Shell**: Statements grouped to one pill with an internal toggle, nav collapsed to
   four pills, account-scope labels and measured/modeled/mixed chips applied page-wide, redirects
   for retired tab URLs, remaining CSV endpoints re-audited.

Each slice gets its own Loop A round (scratch mockups in /tmp per `visual_loop.md`, never committed)
and its own gated Loop B build with the full suite green.

## Display grammar ruling (2026-07-04, applies to every slice)

After the first Taxes/Spending mockup round the developer corrected a drift in the overhaul
documents: the LEDGER identity applies to the CALCULATIONS (double-entry postings, tie-outs,
reconciliation oracles - the correctness guarantee), NOT to the display. The display should be
graphical and easy to read, following the cockpit grammar of /dashboard, /retirement, and /savings:
hero number + chips + a chart that IS the answer + supporting cards. He stares at numbers all day at
work; dense ranked tables recreate his day job. Tabular presentation remains correct only where the
table IS the artifact: the grid (per the design language's Differentiation section) and the literal
statements (Income Statement / Balance Sheet). Recommendations must lead with chart-centric
directions; "show the math" tables are secondary, collapsible support.

## Loop A record (Calendar slice)

- **Round 1 (2026-07-04):** three directions on hand-computed July-2026 mock data (biweekly Thursday
  paydays, 3-paycheck month, below-threshold trough): A "Ledger cells" (per-day flows + end-of-day
  balance hero), B "Waterline" (per-week balance sparkline bands under the cells), C "Runway bars"
  (per-cell balance level bar). Developer outcome: **A is the base** -- seeing the actual flows next
  to the balance is what he wants. **C REJECTED.** B's per-week bands rejected as-built, but the
  developer explicitly wants the month's flow shape, so round 2 explores A plus a SINGLE month-wide
  waterline strip above the grid.
- **New hard requirement from round 1:** day cells must handle MULTIPLE same-day flows gracefully.
  Envelope-style expenses (groceries, spending money, gas) carry system due dates that cluster on
  paydays, so a payday cell routinely holds a paycheck plus several expenses. Round 2 mock: up to
  three named flow lines per cell, then a "+N more" collapse with the residual amount; the full list
  stays on the day click-detail.
- **Round 2 (2026-07-04):** A upgraded with payday flow clusters ("+N more" collapse) vs B = the
  same cells plus a SINGLE month-wide flow strip (end-of-day balance line: solid measured / dashed
  projected split at today, area fill, threshold line, payday dots, labeled trough dot, Today
  marker, weekly gridlines). Developer outcome: **B LOCKED** -- "I like the month flow strip of B."
  One correction ruled: the threshold must come from the user's low-balance-threshold setting (the
  same source the grid uses), never a hardcoded value; the mock's $1,000 was sample data.

### Calendar rebuild decisions (Loop A locked 2026-07-04)

1. **Direction: ledger cells + one month-wide flow strip** (round 2 "B").
2. **Day cell anatomy:** day number; markers (PAY tag with period tint, infrequent glyph); up to
   three named flow lines (income first, then expenses by descending magnitude); four or more flows
   collapse to a "+N more" line whose residual sum is computed in the service (templates never do
   money math); end-of-day balance is the hero, right-aligned tabular mono. Measured days render
   solid; modeled days render in secondary ink with a leading tilde; below-threshold and trough
   balances render danger; today gets the accent outline.
3. **Month flow strip anatomy:** end-of-day balance by day for the whole month; solid line +
   stronger fill through today, dashed line + lighter fill after; area baseline at zero; threshold
   line + label; green payday dots; red trough dot with amount label; Today marker; weekly gridlines
   with date labels.
4. **Threshold comes from settings** (developer ruling, round 2): the strip line, the
   below-threshold cell coloring, and the trough tile all read the user's low-balance-threshold
   setting -- one source shared with the grid and dashboard.
5. **Caption honesty:** the trough tile is the MONTH's minimum end-of-day balance and is captioned
   "Month trough" (the dashboard's 13-period trough is a separate concept and keeps its own name).
6. **Summary strip:** Balance today (measured) / So far in+out (measured) / Remaining in+out
   (projected) / Projected month end / Month trough. Elapsed vs remaining split at TODAY in the
   display timezone (America/New_York), per the timezone display policy.
7. **Row set unchanged:** the locked Choice-2 balance-contributing predicate stays; the
   measured/modeled treatment is a DATE split (before/after today), not a status split.
8. **Chart implementation:** prefer Chart.js through the existing `chart_theme.js` factory (theme
   reactivity + per-day tooltips) IF dashed-segment split, point styling, and the threshold line are
   achievable with the vendored version and no new dependency; otherwise a hand-built SVG with a
   theme-change rebuild hook. Decide at P2 start after reading `chart_theme.js`. No new packages
   without approval.
9. **CSV:** keep the month CSV export; add an end-of-day balance column (explicit decision, not
   drift).
10. **Year view:** out of slice-1 scope; ships unchanged and is revisited after the month view
    lands.

### Calendar daily-balance semantics (Gate A follow-up, LOCKED 2026-07-04)

Tracing the seam before P1 surfaced a contradiction between the locked round-2 mock B (a
day-textured balance line with payday dots and a labeled single-day trough dot) and the P1 line's
originally-written oracle ("series equals per-day `cash_balance_at` calls"). `cash_balance_at` ->
`balance_resolver.balance_as_of_date` is **period-granular, not day-granular**: `_sum_period_as_of`
sums the WHOLE pay period's projected income/expense with no `due_date` filter, so for a projected
month it returns the identical value for every day in a period. A series built from it is flat for
~2 weeks and steps only at period boundaries: paydays do not move it, the "trough" is a two-week
span not a day, and the day cells visibly disagree with the flat line (violating "a figure and its
caption never disagree"). The developer ruled (worked-example fork, 2026-07-04):

1. **Daily balance = a projected running "checkbook" balance**, re-anchored at every pay-period
   boundary to the seam's tested period-end balance. It ramps on each day's flows; payday is a
   visible jump; the trough is a real day.
2. **Projected-only, entry-aware flows.** The seam period net (`sum_projected`) is Projected-only,
   so the daily line ramps only on Projected rows using the entry-aware amount -- this is what makes
   `series[P.end_date] == cash_balance_at(P.end_date)` (reconciles with the grid period-end by
   construction). Settled rows still render in the day cells (Paid badge, context) but do not move
   the projected line, exactly as on the grid; `stale_anchor_warning` is surfaced when post-anchor
   settled rows exist.
3. **Period-clamped attribution.** A transaction's flow lands on its `due_date` (fallback: the
   period `start_date`) clamped into its own period's `[start_date, end_date]`, so every period's
   flows sum by the period end and boundaries always reconcile. The day cells attribute by the SAME
   clamped rule so a flow's cell and the line's step share one day.
4. **Corrected P1 oracle:** for each period P overlapping the month,
   `series[P.end_date] == cash_balance_at(account, scenario, P.end_date)`; the line is continuous
   across boundaries; a day's step equals that day's clamped projected net. (The old "equals per-day
   `cash_balance_at`" line is retired -- it encoded the period-flat semantics rejected here.)

### Loop B build plan (Calendar slice)

- **P1 -- data (Opus scope):** one-pass daily end-of-day balance series producer on the `balance_at`
  seam per the "Calendar daily-balance semantics" ruling above (projected-only, entry-aware,
  period-clamped running balance re-anchored to the seam period-end; oracle:
  `series[P.end_date] == cash_balance_at(P.end_date)` for each overlapping period, plus continuity
  and per-day step == clamped projected net); calendar service reshape (grouped per-day flows with
  collapse fields, summary figures, month trough, `low_balance_threshold` from settings); display-tz
  day boundaries; targeted tests then full suite.
- **P2 -- presentation (Fable scope):** month template + `analytics.css` + strip chart/JS under CSP
  (data via `data-*`), both themes, `shoot.py` verification against the dev app.
- **P3 -- acceptance:** developer drives the live page on real data; as-built record here; CSV
  column verified.

### Loop B P1 as-built (COMPLETE 2026-07-04, on dev, full suite 7185 green)

Data layer shipped; presentation (P2) not started. What landed:

- **Producer:** new `app/services/daily_balance_series.py` (`build_daily_series`), a member of the
  balance-seam cluster (added to the W9906 allowlist AND to `_BALANCE_PRODUCERS`), exposed as
  `balance_at.cash_daily_balance_series`. It seeds from `balance_as_of_date` at the day before the
  first overlapping period and ramps day by day using projected-only, entry-aware, period-clamped
  nets from `balance_calculator.sum_projected`. Oracle test:
  `series[P.end_date] == cash_balance_at(P.end_date)` for each overlapping period, plus continuity,
  per-day step, pre-anchor-flat, settled-excluded, clamp, and within-period-entry reconciliation.
- **Shared attribution rule:** `pay_calendar.DerivedPeriod.attribution_day` (due_date or period
  start, clamped into the period span) is used by BOTH the producer's ramp and the calendar's day
  grouping, so a flow's cell and the line's step share one day. It was
  `app/utils/dates.attribution_date` until pay-calendar plan step `C4-a-2` moved the rule onto the
  value that carries the span and deleted the free function.
- **Calendar query change:** `calendar_service._query_transactions_for_range` now selects by PERIOD
  MEMBERSHIP (`pay_period_id.in_(period_ids)`), not raw `due_date` (`monthly_attribution_clause`
  retired from calendar) -- required by the clamp so a stray-dated flow is not dropped. Two tests
  whose data had a due_date outside its period were corrected (clamp behavior, developer-approved).
- **Reshape:** `MonthSummary` gained `day_overflow` (the "+N more" residual) and `daily`
  (`DailyView`: per-day balances, month trough, balance-today, elapsed/remaining split at display-tz
  today). Day cells now order income-first then expense-desc. `low_balance_threshold` plumbed to the
  template; per-day `daily_balance` added to the grid; display-tz `today` used for the today marker
  and the split. Month CSV gained an "End-of-Day Balance ($)" column.

Decisions / boundaries recorded for P2/P3 (P1 hand-off notes; P2's own record follows below):

1. **Measured vs modeled basis (NOT a bug -- audit's core finding).** The balance line + day-cell
   EOD hero are the PROJECTED basis (projected-only, entry-aware, override-aware) and reconcile with
   the grid. The day-cell flow amounts and the summary strip in/out are the MEASURED / nominal basis
   (`effective_amount`, from `day_totals`) and tie to each other. They coincide in ordinary data and
   deliberately diverge where the projection excludes a settled row (already in the anchor), applies
   an envelope's entry-aware reservation, or takes a live override.
   **P2 MUST visually label measured vs modeled** so a user does not read
   `balance_today != anchor + so-far-net` as an error.
2. **Month-end figure.** `MonthSummary.projected_end_balance` is the period-flat seam scalar at the
   last day (kept for the year view + backward compat); the month view's honest "balance on the last
   day of the month" is `daily.daily_balances[last]` (the flow strip's right end).
   **P2's month-end tile uses the running value**, not `projected_end_balance`.
3. **Reconciliation boundary (M1):** the invariant holds whenever entries are dated within their
   period (the normal case). A purchase entry dated AFTER its period end is the one anomaly where
   the undated ramp and the date-cut `cash_balance_at` could drift; documented in the producer
   docstring.
4. **Stale anchor:** `cash_daily_balance_series` does not yet surface `stale_anchor_warning`; if P2
   wants the calendar to show the grid's stale-anchor badge, add it from `cash_balance_map` at P2/P3
   (not in the locked anatomy, so deferred).
5. **Year view** unchanged (`daily=None`; no per-day balance read), per Calendar decision 10.

### Loop B P2 as-built (COMPLETE 2026-07-04, on dev)

Presentation layer shipped per the locked anatomy. What landed:

- **Template** (`_calendar_month.html`, rebuilt): summary strip (Balance today / So far / Remaining
  / Month end / Month trough, reusing the shared `.pulse-chip` vocabulary per the accounts-cockpit
  precedent; So far hides on a wholly future month, Remaining on a wholly past one); flow strip
  canvas; day cells with head markers (day number, PAY tag, infrequent glyph), up to three named
  flow lines (visible cap DERIVED from the service overflow count, no constant copy in the
  template), the "+N more" residual line, and the end-of-day balance hero (right-aligned mono;
  modeled days = leading tilde + secondary ink; below-threshold and trough days = danger). Nav, CSV
  button, 3rd-paycheck badge, and the click day-detail templates kept; the detail table's amount
  colors moved from Bootstrap `text-success`/`text-danger` to the token classes.
- **Route** (`analytics.py`): `_serialize_flow_strip` (the calendar's single Chart.js float
  boundary, mirroring the dashboard's `_serialize_chart`): labels, values, `current_index` (count of
  measured days; 0 wholly-future, day-count wholly-past -- net-worth cockpit semantics), `threshold`
  (the low-balance setting), `payday_indices`, `trough_index`, `week_tick_indices` (the 1st +
  Sundays). `_build_calendar_weeks` gained `is_modeled` (date split at display-tz today, decision
  7). `month_end_balance` = the RUNNING last-day balance (P1 decision 2), direct dict index so a
  producer-contract violation fails loud.
- **JS** (`calendar_flow_strip.js`, new; wired into `analytics.html`): ShekelChart factory chart.
  Measured and projected spans are TWO datasets sharing the boundary point -- the locked anatomy
  requires a fill-strength split (stronger through today, lighter after), which single-dataset
  `splitSegment` styling cannot do; dashed projection via `borderDash`. Threshold line = the
  dashboard pulse chart's flat dashed credit dataset idiom, tooltip-filtered. Shared
  `todayMarkerPlugin` for the Today marker; a small inline plugin draws the trough amount label and
  the "low balance $N" threshold caption. Payday dots (done green) and the trough dot (danger) via
  scriptable point options with boundary dedupe; tooltips append " projected" on days after today; x
  ticks/gridlines render only at the weekly indices. Re-inits on `htmx:afterSwap` when the swapped
  content holds the canvas; theme toggles re-resolve colors through the factory.
- **CSS** (`analytics.css` calendar section rewritten): flex-column day-cell anatomy, flow-line and
  hero styles, pay tag, chip caption/pair classes, strip heights. Grid tracks are `minmax(0, 1fr)`
  (bare `1fr` floors at min-content and clipped the Saturday column on mobile); the balance hero
  carries a nowrap/ellipsis guard. Mobile (<= 767.98px): named flow lines collapse to a
  flow-presence dot (suppressed on payday cells -- the PAY tag fills the head), hero kept at
  0.625rem.
- **Tests:** one pin updated under the approved Gate A change
  (`test_calendar_month_totals_displayed` -> `test_calendar_month_summary_strip_displayed`; the old
  Income/Expenses/Net totals row no longer exists) and five new hand-computed pins (payload shape
  incl. paydays/Sunday ticks/threshold, trough + danger-cell rendering, future-month modeled
  treatment via display-tz today, the 3-line cap + "+2 more" residual, the PAY tag).
- **Verification:** pylint app/ 10.00/10 with the full --fail-on set; biome clean (JS + CSS); 156
  analytics route tests + 91 ownership/calendar-service/daily-series tests green; live verification
  on a self-seeded throwaway instance (scratch DB cloned from the test template; July-2026 story
  with a 3-paycheck month, payday flow cluster, one settled row, and a below-threshold trough)
  screenshotted in BOTH themes and BOTH viewports -- every rendered figure matched the hand-computed
  expectations (balance today 4207.00, month end 4532.00, trough 452.00 on Jul 10, So far expenses
  1840.35, overflow "+2 more -$78").

P3 notes / residuals:

1. Mobile day cells ellipsize a balance wider than the cell (5+ digit figures at 390px); the exact
   value stays one tap away in the day detail. Revisit at P3 if real data hits it.
2. The infrequent glyph has no route-test pin (it needs a template + recurrence-rule seed); verified
   visually only. Add a pin if it regresses.
3. Deferred unchanged: stale-anchor badge (P1 note 4), year view (decision 10), account-scope label
   and measured/modeled chips page-wide (slice 4).
4. The full-suite gate could not be run in isolation this session (shared test DB busy with parallel
   harness work); it must be green before the P2 commit ships. (Resolved same day: run in isolation
   after clearance, 7190 passed; committed as `11d10c80`.)

### P3 acceptance (COMPLETE 2026-07-04)

Developer drove the live page on real data and accepted: "Calendar looks good." Slice 1 (Calendar)
is CLOSED -- P1 `19b453a7`, P2 `11d10c80`, acceptance recorded here. The P3 residuals above stand as
noted (no defects raised during the drive). Next: slice 2, Taxes (T-P1 annual liability service).

## Taxes slice as-built (T-P1..T-P4 COMPLETE 2026-07-04/05, on dev)

Built per Gate A ruling 5 (one arc, no v1/v2 staging), the liability-basis ruling, and the locked
Taxes anatomy. Opus subagents built T-P1..T-P3 with orchestrator review + gate re-runs between
phases; T-P4 was the Fable presentation phase.

- **T-P1 `6936b8f9`** -- annual filing-time liability:
  `tax_calculator.calculate_annual_federal_liability` (same bracket/credit primitives as
  withholding; 4(a) in, 4(b)/4(c) out; nonrefundable clamp) +
  `tax_liability_service.compute_annual_liability` (per-year configs via the SSOT loader; NC base =
  wages + 4(a) - pre-tax, state std deduction inside `calculate_state_tax`). 20 hand-confirmed tests
  incl. the locked worked anchor.
- **T-P2 `c7dd0a64`** -- `salary.ytd_tax_checkpoints` (history-keeping per (profile, as_of_date);
  migration `3e501a622c8f` verified both directions; audit trigger manually attached; 9 named
  CHECKs), `POST /salary/<id>/checkpoint` (salary blueprint per the domain-write convention;
  Marshmallow validation w/ display-tz future bound; `_tax_checkpoint_card.html` partial), and
  `tax_withholding_service.compute_withholding_to_date`: measured stub + modeled remainder,
  partitioned at payday (`start_date > as_of_date`), projected with FULL-year engine context (a
  remainder-only list restarts cumulative wages -- caught in orchestrator review, fixed, pinned by
  the 260k cap test where total projected SS == the 11,439.00 statutory max exactly).
- **T-P3 `20ad520b`** -- `tax_report_service.compute_tax_report`: RefundEstimate (withheld -
  liability per jurisdiction, positive = refund), hybrid W2Preview (Box 1/16 = gross - modeled
  pretax; Box 3 SS-base-capped; per-box measured/modeled mix), ScheduleACheck (mortgage interest
  REUSED from the year-end ledger+schedule hybrid; property tax OMITTED -- escrow lines are
  free-text named, no ref-kind separates tax from insurance; SALT cap not applied, disclosed),
  TaxChips (effective 4dp zero-safe; marginal via `marginal_rate_for`, exact edge = lower tier; next
  stub), structured TaxAssumptions. Single-filer identity: wages/pretax/withholding summed across
  active profiles, filing inputs from the primary. Shared-loader extraction into `projection_inputs`
  closed a cross-file R0801 caught live by the Stop hook mid-build. NOTE: imports the private
  `year_end_summary_service._income_tax._compute_mortgage_interest`; when slice 4 retires the
  year-end tab, that hybrid should MOVE here and the private import disappear.
- **T-P4** (this commit) -- `/analytics/taxes` + `_taxes.html` per the locked anatomy: hero band
  (refund/owed hero + federal/NC/effective/marginal/next-stub chips) on the read-only
  `nw-sky`/`pulse-chip` cockpit vocabulary; YTD checkpoint card (the T-P2 partial) + assumptions
  card; derivation ledger (the table-IS-the-artifact exception, measured/modeled splits inline);
  hybrid W-2 preview; Schedule A card. NO chart (the estimate-convergence line stays deferred until
  checkpoint history exists). Signed figures split route-side into magnitude + direction
  ("$X owed" never renders "-$X owed"); rates precomputed as 2dp percents (the `_pct` precedent).
  `TaxReport.primary_profile_id` added so the route wires the checkpoint card without re-deriving
  the primary rule. The Year-End PILL is replaced by Taxes; the `/analytics/year-end` ROUTE stays
  until the slice-4 shell collapse retires it with redirects. Tests: 4 new route pins on
  hand-computed figures (incl. the checkpoint re-anchor identity) + the six-pills pin updated
  (approved Gate A change). Live-verified on a self-seeded instance (130k single/NC, 26 periods,
  June-30 stub), both themes and viewports; every rendered figure tied out by hand (hero 103.78 =
  federal +132.97 - NC 29.19; Box 4 8,060.00; Schedule A margin 11,450.91).

T-P5 (developer acceptance) remains: drive the live tab on real data; decide the year-end CSV's fate
(it dies with the tab at slice 4 unless a tax-summary export is wanted).

### T-P5 acceptance findings (2026-07-05): the refund model vs filing reality

The developer's drive surfaced a real-data reconciliation gap: the tab showed a zero federal refund
and an NC balance due, vs his actual 2025 filing (roughly $4,000 federal and $800 NC refunds).
Quantified diagnosis against his live data (MFJ, 4 qualifying children, calibrated zero-dollar
per-period federal withholding, one saved checkpoint):

1. **Federal:** the model is arithmetically correct AND useless for this household -- tax before
   credits (~5,321) is fully absorbed by 4 x 2,000 CTC, the v1 nonrefundable clamp floors the
   liability at zero, withholding is genuinely zero, so refund = 0 by construction. His real refund
   is (almost) entirely the REFUNDABLE Additional Child Tax Credit -- the exact mechanism the v1
   ruling excluded-and-disclosed. For a household whose CTC exceeds its tax, the disclosed caveat IS
   the whole refund.
2. **NC:** two unmodeled filing rules -- (a) the NC per-child deduction (AGI-tiered, not in
   `StateTaxConfig` at all) and (b) `StateTaxConfig.standard_deduction` is FILING-STATUS-BLIND,
   seeded with the single-filer 12,750 while MFJ gets roughly double; both overstate NC liability
   for this household by hundreds of dollars each.

**Developer ruling (2026-07-05): build both extensions** -- federal refundable ACTC + the NC child
deduction (and the filing-status-aware NC standard deduction the diagnosis exposed) -- with 2025 +
2026 constants VERIFIED AGAINST PRIMARY SOURCES (IRS / NCDOR) at build time, not from model memory;
seed updates backfill existing per-user config rows in the migration.

### T-P5 ACCEPTED (2026-07-05) -- slice 2 CLOSED

Extensions shipped as `70ac1689` (which also corrected the stale OBBBA CTC seed 2,000 -> 2,200 per
Rev. Proc. 2025-32); the developer re-drove the live tab on real data and accepted: "The Taxes tab
looks right now." Live figures at acceptance: federal refund 3,478.92 (ACTC), NC refund 298.89,
total 3,777.81.

**CSV ruling (2026-07-05):** "I don't really need a CSV export of anything right now." The year-end
CSV dies with the Year-End tab at the slice-4 shell collapse, with NO tax-summary export replacing
it; the variance CSV retires with its tab at slice 3 (the audit's default, now developer-confirmed).
The calendar month CSV KEEPS shipping per the earlier locked Calendar decision 9 (that ruling
stands; this one addresses only the new-export question).

Slice 2 (Taxes) is CLOSED: T-P1 `6936b8f9`, T-P2 `c7dd0a64`, T-P3 `20ad520b`, T-P4 `6a3366d8`, T-P5
extensions `70ac1689`. Next: slice 3, Spending (S-P1 producers, then S-P2 page).

## Spending slice as-built (S-P1..S-P2, on dev)

**SUPERSEDED 2026-07-10 by the S14 "months lead" cockpit rebuild** (polish audit ruling D7 in
`ui_ux_polish_audit.md`, where the as-built record now lives): the per-period trend enrichment
(sparklines, trend delta chips, Top Movers) retired for a month-over-month change basis, one pulse
canvas with a trailing-12 emphasis month chart, and a By size / By change ledger lens. The Gate A
rulings below (checking-only labeled; calendar-month picker defaulting to the most recent COMPLETED
month; no CSV) still stand. The section is kept as the historical S-P1..S-P2 record.

Built per the then-locked Spending anatomy (hero band; Where It Went rows with share bar + amount/%

- per-period sparkline + delta chip; rail = Top Movers + Estimate Surprises) and the S-P1 Gate
  rulings (checking-only labeled; calendar-month picker defaulting to the most recent COMPLETED
  month; no CSV).

- **S-P1 `c7bc39a4`** -- the unified
  `spending_report_service.compute_spending_report(user_id, window)` producer + shared
  `spending_analysis.py` primitives (query_settled_expenses, resolved_actual_amount, signed_pct,
  category_names, payment_timeliness_from_txns, validate_window, calendar_window_bounds -- Variance,
  Year-End, and the Income Statement now delegate). Trend unit-mismatch fixed: sufficiency + window
  counted in COMPLETED PAY PERIODS (the series' unit); `ItemTrend` gained `period_totals` so the
  sparkline shares the chip's source. Full suite 7300.

- **S-P2 (this build)** -- `/analytics/spending` + `_spending.html` per the locked anatomy:
  - Route (`analytics.py`): month window (default = prior completed month; Next capped at the
    current month; no `period_id`/`account_id` param, so no IDOR surface). No CSV (the 2026-07-05
    ruling).
  - View-model helpers were EXTRACTED to a new `app/routes/analytics_view.py` (pure builders, no
    Flask/db) -- forced by the 1000-line module ceiling and taken as the SOLID split: the Spending
    helpers (`build_spending_display` magnitude/direction split, `build_spending_nav`,
    `spending_sparklines` SVG geometry) plus the pre-existing `build_taxes_display`,
    `serialize_flow_strip`, `build_calendar_weeks`, `build_variance_chart_data` moved verbatim; all
    call sites rewired; 196 analytics tests + full suite green confirm no regression.
  - Presentation: cockpit hero band (`pulse-canvas`/`nw-sky`) with spent hero + vs-prior /
    vs-6-mo-avg / payment-timing chips; "Where It Went" group->item rows on one shared CSS grid,
    share bars via `progress_bar.js` + `data-progress-pct` (CSP-safe), per-category trend sparklines
    as SERVER-RENDERED inline `<svg><polyline>` (geometry attrs, theme-reactive via a direction CSS
    class, zero new JS), delta chips reusing the `trend-up`/`down`/`flat` color vocabulary; rail
    with Top Movers + Estimate Surprises. Groups default-expanded, Bootstrap-collapsible.
  - Basis honesty: a "measured" chip + on-screen "Checking" scope label; the sparkline is captioned
    "recent per-period trend" (it is the rolling-window trend, not the viewed month) so a
    back-navigated month never reads as disagreeing.
  - Nav: the Variance + Trends PILLS are replaced by one Spending pill; the `/analytics/variance`
    and `/analytics/trends` ROUTES stay reachable until the slice-4 shell collapse (the T-P4
    pattern).
  - Tests: `TestSpendingTab` (8 hand-computed pins: breakdown/shares, vs-prior chip, surprises +
    net, sparklines + movers under sufficiency, empty-month, no-account empty state, default-month,
    non-HTMX redirect) + the pills pin updated (developer-locked Gate A change).
  - Gates: `pylint app/` 10.00/10 (full --fail-on, incl. cross-file duplicate-code across the
    extraction); biome clean; full suite 7308 passed. Live-verified via the real `_spending.html` +
    real CSS rendered on a rich seeded story, screenshotted in BOTH themes and BOTH viewports; every
    figure tied out by hand (hero 3,056 = Home 1,390 + Family 910 + Credit Card 406 + Auto 350;
    Costco surprise +170 net; movers Payback +23.68% / Rent +15.86%). Independent code-reviewer
    pass: clean, no Critical/High/Medium; the two moved-helper doc-comment references were
    corrected.

### S-P3 ACCEPTED (2026-07-05) -- slice 3 CLOSED

The developer drove the live tab and accepted: "Spending looks good." Neither carry-forward item was
raised at acceptance, so both stand as-built:

1. **Transfer-out shadows stay counted as spending** (the S-P1 `query_settled_expenses` includes
   them). Accepted as-is; not excluded. Revisit only if a future real-data view makes
   transfers-to-savings misread as spending -- that would be a producer (S-P1) change.
2. **The sparkline is the recent rolling-window trend, captioned "recent per-period trend"** (not
   the viewed month). Read clearly; kept.

Slice 3 (Spending) is CLOSED: S-P1 `c7bc39a4`, S-P2 (this commit; route + `_spending.html` + the
`analytics_view` view-model extraction). The Variance and Trends ROUTES remain reachable until the
slice-4 shell collapse; their CSVs and the year-end CSV die there (the 2026-07-05 CSV ruling).

Next: **Slice 4 -- Shell.** Group Income Statement + Balance Sheet into one "Statements" pill with
an internal toggle; collapse the nav to the four target pills (Calendar / Spending / Statements /
Taxes); apply the on-screen account-scope label and the measured / modeled / mixed basis chips
page-wide (unifying `tax-basis-chip` + `spend-basis-chip` into one vocabulary); add redirects for
the retired `/analytics/variance`, `/analytics/trends`, and `/analytics/year-end` tab URLs; and
retire those tabs' CSV exports and templates. Re-audit any remaining CSV endpoints (calendar month
CSV KEEPS per Calendar decision 9).

## Shell slice as-built (Slice 4, on dev 2026-07-05)

The four-pill target IA is now fully realized: the nav is Calendar / Spending / Statements / Taxes,
every surface carries an explicit basis chip and (where account-scoped) an on-screen scope label,
and no dead route, template, service, JS, or CSV code is left behind. Built in one pass (Opus for
routes/services/tests, applying the design language to the templates/CSS directly), gated and
live-verified before commit.

Three developer rulings taken at the build gate:

1. **`budget_variance_service` DELETED (module + test).** Once the Variance route became a redirect
   it had zero functional callers -- the settled-surprises kernel already lives independently in
   `spending_report_service._build_surprises` via the shared `spending_analysis` primitives.
   Deleting it also orphaned `analytics_view.build_variance_chart_data` (removed),
   `chart_variance.js` (removed), AND `balance_predicates.monthly_attribution_clause` (removed --
   its last live caller was the deleted variance query; the calendar retired it at P1). The
   `duplicate-code` suppression that anchored on `VarianceWindow` was re-seated: `StatementWindow`
   becomes the un-disabled anchor of the Statement<->Spending selector pair, `SpendingWindow` keeps
   the one-sided disable (pylint 10.00/10 confirms both stay useful; `useless-suppression` is on).
2. **Statements chip = a distinct `confirmed` term** (the audit's fourth data semantics, a stronger
   claim than "measured"). Page-wide vocabulary: Calendar = `mixed`, Spending = `measured`,
   Statements = `confirmed`, Taxes = `modeled`.
3. **Statements CSV exports RETIRED** (Income Statement + Balance Sheet). Combined with the earlier
   ruling, the calendar month CSV is now the ONLY surviving export.

What landed:

- **Shell** (`analytics.html`): four pills; the Statements pill loads `income_statement_tab` by
  default; `chart_variance.js` / `chart_year_end.js` script tags removed.
- **Statements grouping**: new `_statements_toggle.html` (title + `confirmed` chip + "all accounts /
  confirmed ledger" scope note + an Income Statement / Balance Sheet `btn-group` toggle, each
  targeting `#tab-content`). Included at the top of `_income_statement.html` and
  `_balance_sheet.html` (their per-report `<h5>` and CSV buttons removed; each keeps its own window
  / as-of selector). The route passes `active_statement` = "income"/"balance"; the nav pill stays
  active while the toggle swaps content (the toggle is not a nav pill).
- **Routes** (`analytics.py`): `variance_tab` / `trends_tab` / `year_end_tab` collapsed into one
  `retired_tab` redirect (three `@route` decorators -> `redirect(url_for("analytics.page"))`). The
  `format=csv` branches + `export_income_statement_csv` / `export_balance_sheet_csv` calls removed
  from the two statement routes; the dead `_window_csv_filename` helper deleted; the
  `budget_variance_service` / `spending_trend_service` / `year_end_summary_service` / `Decimal`
  imports pruned. The income-statement `period_id` IDOR guard stays intact.
- **Calendar scope** (`calendar_service.py`): `MonthSummary` gained `account_name: str` (single
  construction site, set from the already-resolved `account.name`); the month template renders the
  `mixed` chip + "Checking account: <name>." The year view is unchanged (scope label is month-view
  only, per Calendar decision 10).
- **CSV service** (`csv_export_service.py`): pruned to the calendar exports + shared formatting
  helpers; the five retired export functions, the `_pct` helper, the `TWO_PLACES` / `HUNDRED`
  constants, and the `ROUND_HALF_UP` import all removed.
- **CSS** (`analytics.css`): `.tax-basis-chip` / `.spend-basis-chip` renamed to one
  `.analytics-basis-chip`; the dead `.year-end-*`, `.variance-*`, and orphaned `.trend-*`
  (arrow/item/pct/abs/flagged/payment-late) rule blocks removed (`.trend-up`/`down`/`flat` kept --
  Spending reuses them).
- **Deleted templates/JS**: `_variance.html`, `_trends.html`, `_year_end.html`, `chart_variance.js`,
  `chart_year_end.js`.
- **Stale references**: every docstring/comment that named the deleted `budget_variance_service` as
  a CURRENT consumer was corrected across `account_resolver`, `scenario_resolver`,
  `net_worth_kernel`, `spending_analysis`, `spending_report_service`,
  `ledger_report_service._types`, `pay_period_service`, `year_end_summary_service._spending`,
  `routes/templates`, and `balance_predicates`; the surviving in-app references are all explicitly
  past-tense. Two test-file docstrings (`test_services_no_flask`, `test_balance_predicates`) keep
  `budget_variance_service.py` as accurate historical narrative of a past audit finding.
- **Tests**: deleted `TestYearEndTab` / `TestVarianceTab` / `TestTrendsTab`, the retired export +
  export-button tests, `test_budget_variance_service.py`, the variance IDOR ownership classes, and
  the year-end/trends baseline tests; the CSV service test rewritten to the calendar-only exports.
  Added redirect tests (302 -> `/analytics`), Statements-toggle tests (confirmed chip + IS/BS toggle
  target + no CSV button + `?format=csv` no longer exports), a calendar basis-chip + scope test, and
  repointed the period_id audit-event test from the retired variance route to the surviving income
  statement route. The pills test now asserts the four current pills.
- **Gates**: `pylint app/` 10.00/10 (full `--fail-on`); `biome lint` clean; full suite 7167 passed;
  live-verified all four pills in BOTH themes on real data (Statements toggle switches IS<->BS with
  the pill staying active and the trial balance tying out; the four chips render mixed / measured /
  confirmed / modeled; the calendar scope label reads "Checking account: Checking"). Independent
  `code-reviewer` pass: clean on correctness / financial / security / IDOR / test-quality; only
  low-severity stale docstrings, corrected before commit.

The `/analytics/variance`, `/analytics/trends`, and `/analytics/year-end` ROUTES survive as redirect
stubs (not deleted) so old bookmarks land on the page. `year_end_summary_service` and
`spending_trend_service` are KEPT (deeply reused elsewhere; only their analytics routes / templates
/ CSV / JS retire). The Taxes tab's private import of
`year_end_summary_service._income_tax._compute_mortgage_interest` stays (its relocation was a T-P3
note, not shell work).

The four-pill analytics overhaul (Calendar / Spending / Statements / Taxes) is COMPLETE pending the
developer's live acceptance drive.
