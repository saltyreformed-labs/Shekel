> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Follow-up: each loan is resolved 4x per `/savings` render

**Status:** RESOLVED 2026-07-13 (commits `b61aee9c`, `866e30b0`, `84c6e066`, `7b7c909b`).
Full arc, deviations, and residuals: `implementation_plan_loan_resolution_context.md`.

**The original filing was directionally right, materially undercounted, and wrong on its central
claim.**  Kept in full below for the record; the corrections come first.

---

## What was actually true

**The count was 11, not 8.**  Instrumenting the real producers against the dev database (user 1:
Mortgage + Van Loan) showed ONE `compute_dashboard_data` running the loan resolver **eleven times for
two loans** -- 321 SQL queries, ~630 ms.  The filing missed two callers: `compute_property_equity` ->
`resolve_home_equity` -> `resolve_account_loan`, and `_loan_ever_paid_off` ->
`resolve_loan(as_of=date.max)`.  It was also not a `/savings` quirk: `/dashboard`'s tracks section ran
8, the standalone horizon 6.

**"Not a correctness bug" was FALSE.**  The filing said "every resolution returns the same answer --
all four paths seed from the same genesis ledger."  Two of them could not seed from the ledger and
never did.  `_loan_ever_paid_off` resolved with no `confirmed_view`, and `confirmed_loan_view` returns
`None` for any `as_of` after today *by contract* -- so the probe was structurally pinned to the
pre-read-switch anchor replay, which is **blind to money**: `_replay_from_anchor` feeds the engine
`ConfirmedPayment(period_start, due_date)` with NO AMOUNT, advancing one SCHEDULED step per confirmed
payment and discarding the cash actually paid.

`is_paid_off` gates the debt card's `total_debt`, the principal-paid fraction, the Horizon's domain
and debt-free date, the payoff milestones, and the "Paid off" badge.  Both failure directions were
reachable through the production settle path:

* Retire a $1,000 loan with one settled $1,100 payment.  The ledger books the real principal and reads
  $0.00; the hero shows $0.  The replay took a single ~$40 scheduled step, still owed ~$960, and the
  loan was never marked paid off -- no badge, still active debt on the Horizon, "Debt-free" pushed
  years out.  This is why the app *required* a manual balance true-up to $0 after a lump-sum payoff: a
  band-aid for a producer that could not see the cash.
* Pay a 2-month loan SHORT.  The replay exhausts the term and reaches $0.00 while the ledger still
  owes ~$810.  `is_paid_off` went True and the loan **vanished from `total_debt`**, its full original
  principal counted as paid.  Real, still-owed debt left the debt card.

The ten resolutions that agreed are what made the eleventh invisible.  **Redundant derivation is not a
performance smell; it is where a divergence hides.**

## The root cause, and the fix

A loan's resolved state was not a first-class, as-of-pinned, read-pass-scoped value.  With no single
"the loan, resolved" object, every consumer re-derived it off a hidden clock -- some outside the seam,
one with a retired producer that could not read the ledger.  The DRY violation and the correctness
holes were one defect seen from two sides, and the "waste, not a wrong number" framing is what kept
the holes invisible.

A cache was therefore the wrong fix.  `flask.g` would break the Flask-free service boundary and go
stale across the write-then-render paths (`loan_recurrence_sync` and the transfer posting sync resolve
loans mid-mutation).  Threading raw `debt_schedules` into the seam -- this document's own
recommendation -- would have fixed only `/savings`, added an optional input that corrupts silently
when stale, and fixed none of the correctness holes.

**Shipped instead:** `app/services/resolution_context.py` -- a `BalanceContext` built ONCE per read
pass, carrying the pinned `as_of`, the baseline scenario, and a lazy memo of each loan's
`ResolvedLoan`, threaded into the `balance_at` seam in place of the bare scenario.  Plus: `is_paid_off`
onto the genesis ledger; the loan's displayed balance rerouted through the seam (`loan_figures` carries
the rich detail with the balance DELIBERATELY ABSENT); the resolver entries fenced; and the
phantom-paydown defect that reroute exposed in the seam's own scalar (see below).

## Measured result

| producer | resolutions | SQL queries |
|---|---|---|
| `/savings` | 11 -> **2** | 321 -> 209 |
| `/dashboard` tracks | 8 -> **2** | 113 -> 92 |
| `/savings` horizon | 6 -> **2** | 145 -> 118 |

Exactly one resolution per loan, and `TestOneResolutionPerLoanPerReadPass` makes it a deterministic
gate rather than a hope.  Every figure across all ten producers on the dev database is byte-identical
to the pre-change snapshot.

## What it exposed on the way

Routing the loan tile's balance through the seam made the scalar and the resolver disagree -- and the
**seam was wrong**.  On its no-ledger fallback, `amortizing_balance_at` walked the FULL schedule, so
UNCONFIRMED rows dated on or before the valuation date reduced the balance by principal the borrower
never paid: a $240,000 loan originated 18 months ago and never paid read as **$236,853.27** owed, as
though 17 unpaid installments had been made.  It is the identical defect `loan_owed_at_dates`
explicitly refuses to commit at its own boundary, and the fallback's own comment always claimed it read
the confirmed rows.  Now it does.  Unreachable in production (every production loan is opened in the
ledger), but it silently corrupted the un-opened case everywhere.

---

## Original filing (for the record)

**Status:** OPEN (not started). Found 2026-07-12 while closing the W9906 fence hole on
`loan_owed_at_dates` (`followup_fence_loan_owed_at_dates.md`). Deliberately NOT fixed there: it is a
distinct concern (DRY / performance, not the fence), it needs its own design decision, and folding it
in would have made a behavior-preserving refactor unreviewable.

**Not a correctness bug.** Every resolution returns the same answer -- the loan resolver is
deterministic and all four paths seed from the same genesis ledger. This is waste, not a wrong
number. It is filed because redundant producer calls in one request are a DRY violation by this
project's standard, and because the resolver is the single most expensive thing on the page.

---

## The measurement

Instrumented `resolve_account_loan` / `resolve_loan_seeded` / `generate_debt_schedules` and drove one
real `compute_dashboard_data(user_id)` against the dev database (user 1: **Mortgage** + **Van Loan**):

```text
=== ONE compute_dashboard_data(/savings) render ===
  generate_debt_schedules (per call)         5
  resolve_loan_seeded     (per loan)         2
  resolve_account_loan    (per loan)         6
```

`resolve_account_loan` itself calls `resolve_loan_seeded`, so that is **8 full resolver runs for 2
loans = 4 resolutions per loan, per render.**

Each run is not cheap: it loads the loan context (payments, escrow, rate changes), replays the rate
periods, seeds from the confirmed ledger, and projects the amortization forward to payoff -- for a
30-year mortgage, ~360 rows.

## Where the four come from

```text
1. _account_balance_maps        -> balance_at.build_maps -> _assemble_inputs -> generate_debt_schedules
2. build_account_net_worth_maps -> balance_at.build_maps -> _assemble_inputs -> generate_debt_schedules
3. _build_trend_window          -> generate_debt_schedules            (direct; the honest-history gate
                                                                       needs each loan's FIRST payment
                                                                       date, which the balance maps do
                                                                       not carry)
4. _retirement_investment_bands -> load_projection_batch -> balance_at.build_maps -> generate_debt_schedules
5. _liability_band              -> balance_at.liability_owed_at_dates -> loan_owed_at_dates
                                                                       (was the W9906 hole; now behind
                                                                        the seam, but still its own
                                                                        resolution)
```

(Five `generate_debt_schedules` calls, but two of them are handed loan-free account subsets, hence 6
`resolve_account_loan` calls rather than 10.)

Plus `_compute_loan_account` in `_projections.py`, which resolves each loan directly via
`resolve_loan_seeded` for the loan TILE (current balance, payment, rate, payoff) -- deliberately, and
documented as such, because the tile wants the rich `LoanState`, not a balance map.

## The root cause

The `balance_at` seam is **stateless by design**: every entry re-assembles its own inputs
(`_assemble_inputs`) so that single-account and batch reads cannot drift. That is the right call for
correctness, and it is what makes the seam safe to call from anywhere. The cost is that N seam calls
in one request do N input assemblies -- including N loan resolutions.

Nothing in the request threads the already-resolved `DebtSchedule` set from one consumer to the next.

## Options (needs a decision before building)

1. **Request-scoped resolution cache.** Memoize `generate_debt_schedules` / `resolve_account_loan` per
   `(account_id, scenario_id, today)` for the life of a request (Flask `g`, or an explicit cache
   object threaded by the orchestrator). Smallest diff, biggest win, and every existing call site
   keeps its current shape. Risk: a cache keyed on `date.today()` is a footgun in a long-lived
   process or a test that freezes time; it also hides the redundancy rather than removing it. The
   services layer is Flask-free (`CLAUDE.md`), so it must NOT reach for `flask.g` -- the cache would
   have to be an explicit parameter, which erodes the "call the seam from anywhere" property.
2. **Thread the resolved schedules through the savings orchestrator.** The orchestrator already loads
   core data once and passes it down; it could resolve the debt schedules ONCE and hand them to the
   seam (an optional `debt_schedules=` input on `_assemble_inputs`). Honest -- the redundancy is
   removed, not hidden -- and keeps the seam Flask-free. Larger diff, and it widens the seam's
   signature, which the Level-1 plan deliberately kept narrow.
3. **Do nothing, document it.** The page is not slow enough to be a complaint today. Revisit if the
   loan count or the render time grows.

Recommendation: **(2)**, because it removes the waste instead of masking it and respects the
service-boundary rule -- but it is a real design change to the seam's input contract and should be
decided deliberately, not slipped into an unrelated commit.

## Pointers

- Orchestrator: `app/services/savings_dashboard_service/_orchestrator.py`
  (`_compute_net_worth_section`, `_build_trend_window`).
- Seam input assembly: `app/services/balance_at/_inputs.py` (`_assemble_inputs`).
- Resolver entry: `app/services/loan_resolution.py` (`resolve_account_loan` -> `resolve_loan_seeded`).
- The loan tile's deliberate direct resolution: `savings_dashboard_service/_projections.py`
  (`_compute_loan_account`).
