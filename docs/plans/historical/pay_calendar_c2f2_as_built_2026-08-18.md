> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The pay calendar, as built: the C2-f2 span (2026-08-18)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_pay_calendar.md`; this record exists so that document's shipped
entries can be pointers rather than accounts of themselves (`conventions.md` rule 5). Archived to
make room for `C2-f2e`'s account, under the rule that says to shrink the record of what is DONE and
never the specification of what remains.

**`C2-f2` is a COMPLETED span**: five leaves shipped and the container ticked with the last of them.
Every reader at a surface that already holds a `BalanceContext` now takes the calendar off
`ctx.calendar()`, and `get_current_period` is `calendar().period_containing(ctx.as_of)` at every one
of them. What the code does now is stated by `balance_at/_context.py`, `pay_calendar/_calendar.py`
and the four consumer packages' own docstrings.

`C2-f2d`'s own four leaves have their own record, `pay_calendar_c2f2d_as_built_2026-08-16.md`; this
one does not repeat them.

## The six entries

- **C2-f2 -- the readers at a surface that already holds a read pass.** `531c1402`. The DECOMPOSED
  parent, split into five leaves by PACKAGE 2026-08-14 (developer) and ticked with `C2-f2e`. Closed
  **P36**: a route and the balance seam no longer read the owner's periods separately, so the
  destructive-write race between the two reads and the 12-71 per-render period queries are gone
  together. The measurement the split rested on: 20 call sites, 23 `app/` modules and 12 templates
  in seven packages -- and all 12 templates were the grid's, so only `C2-f2b` carried any.

- **C2-f2a -- the SEAM's own reader.** `dd5c48a5`. Closed **P37**. No module under
  `app/services/balance_at/` imports `pay_period_service`. Proof: `verify_balance_baseline`
  byte-identical over 9 accounts / 427 grid cells / 5,978 daily points on a production clone, with
  the harness SHOWN firing on a planted wrong axis -- taking the calendar collapsed the two wiring
  sites into ONE derivation, so one plant moved the Empower's grid column, its `balance_map` and its
  2029 scalar together (`-$182.29`, `-$182.29`, `-$190.39`).

- **C2-f2b -- the GRID.** `f4d4abe6`. Carried **P36**'s grid half. All six sites answer from the
  pass's calendar and `get_periods_in_range` is DELETED, taking the six readers from 47 `app/` call
  sites to 39; the COMPANION moved with it (one shared partial), `routes/grid.py` became a package
  first (`29e4fab8`, a pure move off the 1000-line ceiling), and `period_containing` now ENFORCES
  the "SAVED" it claimed. Proof: `verify_grid_cutover`'s docstring -- byte-identical, 0 mismatches
  on PRODUCTION, and SHOWN skipping `$5,827.75`.

- **C2-f2c -- `/investment`.** `d4621147`. Closed **P48**; opened **P52**-**P54**. No module in that
  package imports `pay_period_service`; its three public entries take the calendar AND the clock off
  the pass's `BalanceContext`, and the marker scan retired into `PeriodWindow.containing_index` --
  the OFFSET, because `containing` alone answers the period. A contribution carries its PAYDAY
  (`241b7b40`), which let the period list leave three SHARED signatures without moving
  `/retirement`. Proof: `verify_investment_cutover`'s docstring.

- **C2-f2d -- `/savings` and `/retirement`.** `c95519dd`. The container and its four leaves,
  condensed into `pay_calendar_c2f2d_as_built_2026-08-16.md` with the THREE shapes a later step must
  not undo. Closed **P43**, **P57**, **P58**, **P59**; opened **P55**, **P56**, **P60**-**P63**. Its
  last leaf took three rulings WIDER than its spec, so it also carried the paycheck-engine cutover
  and `C2-f2e`'s `/accounts/<id>` half.

- **C2-f2e -- the budget dashboard.** `531c1402`. Closed **P36**, **P55**, **P61** and **P56**'s two
  dashboard doors; opened **P65**. Three commits: `7a719f9c` (a PURE MOVE making the two dashboard
  modules one package), `3efcba64` (P55) and `531c1402` (the cutover). Its own account is below.

## What C2-f2e did, in full

**The ROUTE opens the render's one read pass.** `/` held TWO -- one opened by
`compute_pulse_section` through `_resolve_section_context`, one by `compute_tracks_section` -- so it
derived the owner's pay calendar TWICE a render where `/grid`, `/savings` and `/retirement` each
derived it once. Nothing under `app/services/dashboard_service/` calls `BalanceContext.build` now.

**Three duplications the row never named, found while measuring it.** The account was resolved TWICE
per render (the route for its `has_account` flag, the producer for its own use) and the settings row
was queried TWICE inside the pulse producer alone (once to resolve the account, once for the hero's
staleness threshold). `_resolve_section_context` returned
`(Account | None, BalanceContext | None, PayPeriod | None)` under a coupling rule written only in
its docstring, and that shape is what allowed all of it. `DashboardSection` replaced it:
`resolve_section` answers `None` for an owner with no resolvable account, so the coupling is the
RETURN TYPE, and a section that EXISTS carries the pass, the account and the settings.

**The two dashboard modules became a PACKAGE first** (`7a719f9c`, a pure move). They had been split
for the 1000-line pylint cap alone and were sharing four private names across a module boundary the
W9910 package-privacy gate deliberately cannot see -- package-private sharing spelled without a
package.

**The period is DERIVED, and that is not a refactor.** Run against the merge base `5ab457b7`, the
step's own case seeds a stored `end_date` that disagrees with the paydays and the WHOLE pulse region
answers `None` -- an owner mid-paycheck shown "No pay period covers today" because a derived column
drifted.

**The three clock reads this producer OWNED now read `balance_ctx.as_of`** -- the hero's staleness
count, the street band's "Today" marker, each bill's `days_until_due`. That is narrower than "the
render reads one clock", and the first draft of this line was not: `BalanceContext.amounts()` still
reads `date.today()` for its loan half, which is finding **N-40** and plan step **X-i2**, and this
region reaches it through `contributions_by_id`.

**P55, closed with it.** `compute_pension_summary`, `compute_gap_net_biweekly` and
`build_employer_salary_basis` each opened the owner's projected salary path at `date.today().year`,
once per PLAN POINT over about ten lever probes. Measured on the merge base with `freeze_today`:
the same pension gave `[2026, 2027, 2028, 2029, 2030]` on 2026-12-31 and `[2027, 2028, 2029, 2030]`
on 2027-01-01 -- a salary path one year shorter for no reason but the moment the process ran. An AST
census reports ZERO live `date.today()` calls left in the retirement RENDER chain; the one survivor,
`routes/retirement.py:158`, is a form validator on a POST with no read pass to take a day from, and
the first draft of this line said "the retirement chain" and was refuted by the census it named.

**Measured through the routes**, same probe and fixture both sides (salary profile, 401(k),
mortgage, active goal):

| render | passes | `calendar_for` | `get_current_period` | `get_all_periods` | settings | account | queries |
|---|---|---|---|---|---|---|---|
| `/` | 2 -> 1 | 2 -> 1 | 1 -> 0 | 1 -> 0 | 2 -> 1 | 2 -> 1 | 70 -> 62 |
| `/dashboard/pulse` | 1 -> 1 | 1 -> 1 | 1 -> 0 | 1 -> 0 | 2 -> 1 | 1 -> 1 | 21 -> 18 |
| `/dashboard/balance` | 1 -> 1 | 0 -> 1 | 1 -> 0 | 0 -> 0 | 1 -> 1 | 1 -> 1 | 15 -> 16 |

The fragment's +1 is the deliberate trade: it answered "which period is current" from SQL over the
stored span against its own clock, and it is the anchor editor's revert target swapping back into
the pulse region, so both must name the same paycheck.

**Proof**: `verify_dashboard_cutover`'s docstring -- BYTE-IDENTICAL on a production clone AND on a
seeded copy of it that makes the five pulse keys production leaves empty carry data, which the first
run was blind on. The gates SHOWN firing on the merge base are
`test_dashboard_render_opens_one_read_pass` (2 passes), `test_dashboard_derives_the_calendar_once`
(2 derivations) and both `TestOneSubjectResolutionPerRender` render cases (2 accounts, 2 settings
reads) -- the two FRAGMENT cases beside them are pins rather than gates and say so in their own
docstrings, and the first draft of this line claimed all of them fired. For P55,
`TestTheRenderDayOpensTheSalaryPath`, whose render-level case was shown failing on a planted
`as_of = date.today()` at `retirement_plan._derive_picture` while its three unit cases stayed
green.

## What a LATER step must still obey

Repeated in the live document's rulings table where it is a ruling, so this archive is not
load-bearing for any of it:

- **A producer below the route takes the read pass and does not build one.** Five service modules
  still do (`calendar_service`, `investment_dashboard_service/_context` and `/_orchestrator`,
  `loan_recurrence_sync`, `tax_report_service`); row **P56** carries the layer predicate and C2-f3
  lands it.
- **The account guard runs BEFORE the period derivation** in `compute_pulse_section`. Reading
  `section.current_period` derives the calendar, which RAISES for a legacy owner (row **P35**), and
  a no-account owner reached no calendar before this step. Reversing that order is the widening
  `/grid` already took at C2-f2b.
- **`DashboardSection.current_period` is a PROPERTY, not a field.** Deriving it in the resolver made
  a producer that returns early raise `PayCalendarError` -- the defect `_DashboardCoreData` records
  from C2-f2d-3, one bundle over.
- **The read pass is built AFTER `top_up_rolling_window`.** That writer can CREATE pay periods and
  commit them, and the pass memoizes the calendar derived from those rows.
