> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation plan: property equity chart rebuild (date-anchored axis)

Single implementation plan for **Option 3** (the full correct rebuild) of the property detail
equity-over-time chart, adopting **(a) contractual back-projection** for a loan's pre-tracking debt
(developer ruling, 2026-07-12).

Findings and reproductions this plan closes: `docs/design/property_detail_followups.md` (H1 paid-off
fallback unreachable, H2 front-aligned merge breaks hero reconciliation, H3 past-dated projected
months get a fabricated market value, D1 double resolution). Producer under rebuild:
`app/services/property_equity_chart.py`.

## 1. Root cause and target

The current producer reasons in **loan-schedule-index space** and assumes "today" is the
confirmed/projected boundary. It must reason in **calendar-date space anchored at `today`**. Every
per-month decision keys off a calendar date, not a schedule index:

- The debt line spans a loan's **origination .. payoff** on a shared monthly calendar axis.
- The value line holds today's anchor flat for every month `<= today` and compounds strictly after.
- "No outstanding secured debt" is decided by **future** balance, not schedule emptiness.
- Multiple loans sum **by calendar month**, never front-aligned at index 0.

The rebuild also makes the producer a **pure function** fed pre-resolved loan data (folds in D1):
the route resolves each loan once and feeds both the equity hero and the chart from that one pass.

## 2. Per-loan debt series (the (a) contractual back-projection)

For one secured loan, build a full monthly debt series across its own
**origination-first-payment .. payoff** span, in five regions. `state` is the loan's resolved
`LoanState` (`resolve_account_loan(...).schedule == history_rows + committed_forward`), and
`tracking_start` is `state.schedule[0].payment_date` (the first row the resolver produced from the
latest anchor).

| Region (by calendar month `m`) | Balance | Confidence tier |
| --- | --- | --- |
| `m < origination_first_payment` | `0.00` (the loan did not exist) | n/a (excluded from this loan's span) |
| `origination_first_payment <= m < tracking_start` | **contractual back-projection** from origination terms | `estimated` |
| `tracking_start <= m <= today` | actual confirmed balance (`history_rows`) | `confirmed` |
| `today < m <= payoff` | committed forward balance (`committed_forward`) | `projected` |
| `m > payoff` | `0.00` (loan gone) | n/a |

**Contractual back-projection producer.** New thin producer (reuses existing primitives, forks no
math): amortize the contractual schedule from origination and clip it to the pre-`tracking_start`
months.

- Inputs: the loan's `LoanParams` (`origination_date`, `original_principal`, `term_months`,
  `payment_day`) and its rate-change feed.
- IMPLEMENTED (commit 1) as
  `loan_resolution.contractual_schedule_from_origination(loan_params, rate_changes)`: seeds
  `loan_resolver.compute_payoff_scenarios` with a synthesized ORIGINATION anchor
  (`loan_loaders.synthesize_origination_anchor`, which forces the origination opening even for a
  tracking-start loan) and an empty payment feed, evaluated as of `origination_date`, and returns
  its `original_forward`. Chosen over a direct `project_forward` call so the schedule inherits the
  resolver's EXACT first-payment-date and remaining-term convention (`next_pay_date` one month after
  origination on `payment_day`, full `term_months` remaining) -- the back-projection and the
  resolved schedule land on the same monthly grid and cannot drift at the seam. Forks no
  amortization math. Pure (the caller supplies `rate_changes` via `loan_loaders.load_rate_changes`).
- Return the rows with `payment_date < tracking_start`.
  **No-op when `tracking_start` is already at or before origination's first payment** (a loan with
  only an origination anchor and no tracking- start true-up already has its full contractual span
  inside `state.schedule`), so back-projection fills a gap only for tracking-start / true-up loans
  -- exactly the real-mortgage case.

**The tracking-start seam is honest, not smoothed.** The back-projection ends at the contractual
balance the month before `tracking_start`; `state.schedule[0]` opens at the actual anchor balance.
They differ whenever the loan was not exactly on schedule before tracking began. That gap is the
truth ("what the contract implies" vs "what you recorded"), so the producer does NOT reconcile it
away. The data layer emits the `estimated` tier so Loop B can render the pre-tracking region
distinctly (see 8); it is never drawn as if it were recorded fact.

## 3. Value series (date-keyed flat / compound)

Replace `index <= current_index` with a **date** test. For each calendar month `m` on the merged
axis:

- `m <= today`: value is the flat anchor `market_value` (its past is unknown; today's anchor is the
  only honest present value -- unchanged design intent, corrected trigger).
- `m > today`: compound `market_value` from `today` to `m` via the one appreciation primitive
  `growth_engine.period_return_rate` (verified consistent with the app's period-tiling growth
  model).

This deletes the H3 degenerate-span path: no month `<= today` ever reaches the compounding branch,
so `period_return_rate` is never handed a non-positive span. **Additionally** harden the
appreciation call so a non-positive span returns factor `1` (no elapsed growth) rather than
borrowing `period_return_rate`'s 14-day inverted-period clamp -- that clamp is a pay-period
safeguard and must never fabricate appreciation in this context. Keep it as belt-and-suspenders even
though the date test makes it unreachable.

## 4. Fallback decision (future balance, not empty schedule)

A loan contributes to the debt line only if it has outstanding balance on or after `today`
(`state.current_balance > 0`, or equivalently its schedule has a `payment_date >= today` row with a
non-zero balance). When no secured loan is outstanding (none linked, none configured, or
**all paid off**), return the loan-less fallback (120-month forward appreciation arc, `no_loans`
state, `debt` and `equity` empty). This makes the paid-off case reach the fallback (closes H1).

## 5. Multi-loan merge (date-aligned)

Merge on a shared monthly calendar axis spanning
`min(origination_first_payment over loans) .. max(payoff over loans)`. For each month, each loan
contributes its region balance from 2 (0.00 before its origination and after its payoff). Sum per
calendar month into one debt line. The per-month debt **confidence tier** is the least-confident
contributing loan's tier (`estimated` < `confirmed` < `projected`... rendered so a month mixing an
estimated loan and a confirmed loan reads as estimated). The single-mortgage case reduces to one
series and three contiguous tiers.

## 6. Reconciliation guarantees (pin them with tests)

- **Chart vs hero at the LAST CONFIRMED month** (corrected during the build -- NOT `today_index`). A
  loan's last confirmed schedule row carries `remaining_balance == state.current_balance`, so at
  that month's axis index `debt == sum(current_balance) == home_equity.total_debt` and
  `equity == home_equity.equity`, to the cent. `today_index` (today's calendar month) is NOT the
  reconciliation point: if this month's payment is not yet confirmed, today's month holds a
  projected balance one payment below `current_balance`. A no-confirmed-history loan therefore does
  not reconcile at all (its schedule projects contractual reductions the hero does not reflect).
  This is review finding M1; the test recipe is in section 10.
- **Value continuity.** `value[today_index] == market_value` exactly (the flat region includes
  today).
- **Internal identity.** `equity[i] == value[i] - debt[i]` for every `i` (holds; tested in commit
  2).

## 7. Purity and single resolution (folds D1)

Split I/O from math:

- **Route / thin orchestrator (I/O).** Resolve each secured loan **once** via
  `resolve_account_loan(loan.id, scenario_id, today)`; skip `None`. Build each loan's contractual
  back-projection prefix (2). Read `market_value` and `appreciation_rate` once. Use ONE `today`.
- **`home_equity_service`.** Feed the already-resolved `state.current_balance` list into the
  existing pure `compute_home_equity(market_value, balances)` -- no second resolution.
- **`build_property_equity_chart` becomes pure.** New signature (rows + scalars in, series out):
  takes, per loan, `(back_projection_rows, schedule_rows)` plus `market_value`, `appreciation_rate`,
  `today`; returns the `PropertyEquityChart`. No `resolve_account_loan` import, no DB. The three
  reproductions become fast pure unit tests.

Do NOT change `resolve_account_loan`'s return shape (traced consumers: net worth, year-end, debt
strategy, home equity, loan card, recurrence sync -- rule 7). The back-projection producer loads its
own light rate-change feed; only the heavy resolve is done once.

## 8. Shared axis primitive (generalize, keep the loan band bit-identical)

`app/utils/chart_series.build_chart_series` currently front-aligns at index 0 -- correct for the
loan band (its `original` / `committed` / `accelerated` series share one start month and cadence)
but wrong for cross-loan merges. Generalize it to **date-align on the union of month dates**:

- For same-start series (the loan band), the union of dates equals the longest series' dates and
  each shorter series pads at the tail exactly as today -> output is **bit-identical**. Gate: the 53
  loan-chart tests in `tests/test_routes/test_loan.py` must stay green unchanged.
- For different-start series (the property merge), date-alignment places each loan by calendar
  month.

If generalization proves to perturb the band, fall back to a property-local date-merge and leave
`build_chart_series` untouched (the band's front-align stays correct for its use). Prefer the
generalization (one primitive) but do not force it against a band regression.

## 9. Data contract (`PropertyEquityChart` -> route JSON -> `property_detail.js`)

- `labels: list[str]` (`%b %Y`), spanning `min origination .. max payoff` (or 120-month arc in the
  fallback).
- `value: list[float]`, `debt: list[float]`, `equity: list[float]` (route floats the `Decimal`
  series at the JSON boundary only; empty `debt`/`equity` in the fallback).
- `today_index: int` -- the flat/compound + solid/dashed boundary (the month `== today`, or the last
  month `< today`). Replaces `current_index`.
- `debt_tier: list[str]` -- per-month `"estimated" | "confirmed" | "projected"` for the debt line's
  three-way styling (empty in the fallback). Loop B renders estimated distinctly from confirmed
  (solid) and projected (dashed).
- `chart_state: "standard" | "zero_rate" | "no_loans"` (unchanged meaning).

## 10. Test plan

All in `tests/test_routes/test_property.py`. Loans dated relative to `today` so CI is date-robust,
but deliberately spanning past origination / tracking-start / paid-off, which the pre-rebuild suite
never exercised. Exact-`Decimal` assertions throughout.

DONE (commit 2, 16 tests):

- **H1:** a zero-balance loan WITH a non-empty schedule -> `chart_state == "no_loans"`,
  `debt == []`, 120-month arc (proves the fix keys on the balance, not schedule emptiness).
- **H2:** old $300k (36mo) + new $50k (1mo) -> `debt[0]` (earliest = old's first month) == the OLD
  loan's balance ALONE, `!= old + new` (the new loan is not summed into a pre-origination month).
- **H3:** an 18mo-old loan with no confirmed history -> every `value[m <= today] == market_value`;
  compounds only after; `today_index > 0`.
- **Value/equity identity, zero-rate, route context contract, D1 resolve-once spy.**

DONE (commit 3, 2 tests -- both green, both empirically probed):

- **`test_chart_reconciles_with_hero_at_last_confirmed_month`** (closes review finding M1). Settles
  two real monthly payments (`create_settled_transfer`, `from_account=seed_user["account"]`,
  `to_account=loan`, `seed_periods[1]`/`[3]`), resolves ONCE, and feeds that one resolution to both
  `compute_home_equity` and the chart. Asserts `debt[i] == hero.total_debt` and
  `equity[i] == hero.equity` at `i = the last confirmed month's label index`, and -- with today
  frozen to April, last confirmed in March -- proves today's month is NOT the reconciliation point
  (`index < today_index`; `debt_tier[today_index] == "projected"`;
  `debt[today_index] < current_balance`). **Learned + corrected against the recipe:** an
  origination-anchored loan's resolved schedule opens at its FIRST CONFIRMED payment, so the
  origination-to-first-payment months become a real (non-empty) `estimated` back-projection here --
  NOT the empty prefix the recipe assumed. The reconciliation still keys off the `confirmed` tier,
  so the test is correct; the assumption was refined.
- **`test_back_projection_estimated_tier_and_tracking_start_seam`** (proves the (a) back-projection
  end to end). `create_loan_account(..., origination_date=add_months(today, -60))` +
  `insert_tracking_start_event(params, Decimal("260000.00"), add_months(today, -24))` (260k is well
  below the ~285k contractual, so the seam gap is unmistakable). Builds via `_series_for`. Asserts:
  every pre-tracking month is `"estimated"` with `debt` equal to
  `contractual_schedule_from_origination(...)` clipped to `payment_date < tracking_start`; the
  tracked region (no settled payments) is `"projected"`; and the seam sits on ADJACENT axis months
  (`seam == first_tracked - 1`) with the estimated contractual balance differing from the recorded
  opening -- the gap is asserted, never smoothed. (The `"confirmed"` region the original recipe
  mentioned needs settled payments and is covered by the M1 test above, not this one.)

Gates: `pylint app/` 10.00 with the full `--fail-on` set; the 53 loan-chart tests unchanged (commit
4); full suite green.

## 11. Sequencing (commits, model discipline)

Data first (Opus), visual second (Fable), each resumable from this plan.

1. **Contractual back-projection producer** + its unit tests (pure, reuses the resolver composer).
   DONE: `contractual_schedule_from_origination` + `synthesize_origination_anchor` +
   `tests/test_services/test_loan_resolution.py` (3 tests: exact 12-month amortization, 360-month
   span, tracking-start-independence). pylint 10.00; 329-test consumer regression green.
2 + 3. **Pure date-anchored producer + route resolve-once** (MERGED -- the producer's new pure
signature and its sole caller must change together, rule 7, to keep the tree green). DONE:
`build_property_equity_chart` rewritten pure/date-anchored (date-keyed value, future-debt fallback
owned by the producer, calendar-month merge, `today_index` + `debt_tier` contract, per-loan
`SecuredLoanSeries` input); `property_detail` resolves each loan ONCE and feeds both
`compute_home_equity` and the chart (one `today`); `_secured_loan_series` builds the clipped
back-projection. Tests (`tests/test_routes/test_property.py`, 16): fallback, H1
(zero-balance-with-nonempty-schedule -> fallback), H2 (date-aligned merge), H3 (past months flat),
value/equity identity, zero-rate, route context contract, resolve-once spy. Fixed a C7-2 dead
anchor-NULL fork the rewire briefly introduced. pylint 10.00; full route suite 2727 green.
2. **The 2 owed producer tests** (M1 reconciliation + estimated-tier/seam) -- DONE, see section 10
   above (18 property tests green; both empirically probed; the M1 test proves today's month is NOT
   the reconciliation point).
3. **Shared primitive generalization** (8) -- **DROPPED (developer decision, 2026-07-12).** Commit 2
   made the producer self-contained (its own date-aligned calendar merge, no `chart_series` import),
   which orphaned the working-tree `chart_series.py` hoist (one consumer, the loan band, and a
   docstring falsely claiming the property chart shared it). Rather than unify, the hoist was
   DISCARDED: the two merges are genuinely different operations (the band front-aligns by index and
   picks one series; the property date-aligns by calendar, sums, and tiers per month), so unifying
   them would be a leaky abstraction risking the 258 band tests for no real DRY gain. Reverted
   `app/routes/loan/_helpers.py` and `calculators.py` to HEAD, deleted `app/utils/chart_series.py`.
   Loan band keeps its local merge; property keeps its own. Both suites green post-revert (258 loan
   tests, 18 property tests).
4. **Fable visual** (Loop B) -- **DONE (2026-07-12, commit `7946d68f`).** The design session ran the
   section 12 sub-decisions (all resolved below) and the JS was rewired: `property_detail.js` reads
   `today_index` (value flat/compound split + Today marker), renders the debt line by per-month
   `debt_tier` -- solid `confirmed`, dashed `projected`, and faint dots (`[2,3]` at 45% danger) for
   the `estimated` back-projection, reusing the value line's assumption texture so dots always mean
   estimate -- and the confirmed/projected split is data-driven, NOT keyed to today. A faint
   "Tracking start" seam marker draws at each `estimated -> non-estimated` transition, suppressed
   within `SEAM_TODAY_MIN_GAP_PX` (100px) of the Today marker so a near-today seam (the real
   single-mortgage case, records begin ~3 months before today) yields to Today. Captions name all
   three textures (the dotted clause gated on a new `has_estimated_debt` route flag) and the
   `no_loans` caption is reworded to stay accurate for a paid-off-but-still-linked loan; the
   aria-label is kept in parity. Live-verified on the real property page (203 Chalmers Dr) in both
   themes and both viewports; shots at `shots-live/property_rebuilt__*`.
5. **Post-build correctness fix -- debt line gap months** --
   **DONE (2026-07-12, commit `936db7df`; found at the live accept-check).** Tracing the real
   mortgage exposed a defect the synthetic tests never hit: a loan's resolved `schedule` is NOT
   one-row-per-calendar-month (the biweekly-to- monthly redistribution left the real mortgage a
   July-less gap between a June and an August row), and `_debt_series` contributed `$0.00` for any
   axis month a loan had no row in -- so the debt line collapsed to `$0` exactly at today,
   fabricating a debt cliff + phantom full-equity spike and breaking chart-vs-hero reconciliation by
   the whole balance. Fixed at the root: `_dense_month_balances` forward-fills the prior balance
   across gap months WITHIN a loan's active span (a debt balance is piecewise-constant between
   payments); gap-free schedules are unchanged. Two regression tests pin a confirmed gap at today
   and a projected gap after. Re-verified on real data: debt@today `177,554.69` (carried June),
   continuous line, reconciles at the last confirmed month (Aug: `177,277.97` == `current_balance`).

## 12. Sub-decisions (RESOLVED at the Loop B gate, 2026-07-12)

- **Seam rendering (Loop B) -- RESOLVED: faint dots + a faint marker.** The `estimated` pre-tracking
  region is styled as faint dots (`[2,3]` at 45% danger), reusing the value line's assumption
  texture (dots always mean estimate). The seam discontinuity gets a faint "Tracking start" vertical
  marker (muted, finer-dashed than Today), suppressed when it would collide with the Today marker
  (developer pick, from previewed options).
- **Multi-loan mixed-tier month styling (Loop B) -- RESOLVED: least-confident tier wins.** Kept the
  default; the merged month renders the least-confident contributing tier's texture (already the
  data-layer behavior in `_debt_series`).
- **Fallback caption for paid-off vs never-secured (Low) -- RESOLVED: no flag needed.** The
  `no_loans` caption was reworded to "This property has no outstanding secured debt, so equity is
  the full market value," which is accurate for both a never-secured and a paid-off-but-still-linked
  property, so no paid-off/never-secured flag is required.
