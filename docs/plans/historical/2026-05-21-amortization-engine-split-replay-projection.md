> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Architectural Fix: Split Loan-Schedule Replay From Forward Projection

## Context

On a loan detail page (e.g. `/accounts/3/loan`), submitting the
Payoff Calculator in **extra-monthly** mode renders an Accelerated
chart series that diverges below the Original schedule starting at
the **origination date**, runs visually parallel to the Original
through a window around the **current date**, then resumes its
accelerated descent **after the current date**. The same underlying
misuse also corrupts the `Months Saved`, `Interest Saved`,
`Total Interest`, and `Projected Payoff` numbers shown beside the
chart.

This is one visible symptom of a deeper architectural defect in
`app/services/amortization_engine.py`: the engine has no concept of
"as-of date" -- no boundary between the deterministic past
(governed by what actually happened) and the projected future
(governed by scenario parameters). Both live in the same loop in
`generate_schedule`, sharing the same parameters. Every caller has
to invent its own convention for telling them apart, and the
payoff calculator's convention is wrong.

The audit you just completed collapsed parallel computation paths
onto `loan_resolver.LoanState` as the single source of truth for
"what's true now." The payoff-calculator surface is the one path
that still calls the engine directly (the `loan.py` comments
reference "Commit 17 will collapse these direct `generate_schedule`
calls into the resolver"). This plan finishes that collapse and
does it on two narrow engine primitives instead of one overloaded
one, so the same class of bug cannot recur on future scenarios
(refinance, lump-sum, rate what-ifs).

## The Architectural Root Cause

`generate_schedule` is asked to do two semantically different
operations in one pass: **replay confirmed history** and **project
the future**. To express "do both," its API has one parameter,
`extra_monthly`, whose semantics had to be defined as "apply when no
PaymentRecord exists for this month" -- because the function has no
other way to express "apply going forward." That definition is what
makes the bug possible:

- Pre-recording history has no records, so extra is applied
  (fictitious accelerated past).
- Confirmed-payment months have records, so extra is suppressed
  (visually "parallel to Original" through the recorded window).
- Post-today gap months have no records, so extra is applied
  (correctly).
- Post-today projected months have records (from recurring transfer
  templates), so extra is suppressed (incorrect: extra now does
  nothing for users with templates).

Three compounding problems:

1. **Replay and projection are conflated in one function.** No API
   shape prevents a caller from accidentally applying a forward-only
   concept to historical months.
2. **The route owns scenario composition.** `payoff_calculate`
   builds Original / Committed / Accelerated by three direct engine
   calls with hand-chosen parameter combinations. Any drift between
   them yields incoherent results that no single test catches.
3. **The engine's schedule loop has no `is_confirmed` branch.** The
   only gate is "is there any payment record for this month?"
   (`amortization_engine.py:535`). Both confirmed and projected
   payments suppress `extra_monthly` identically once they reach
   the engine. The resolver already filters payments to
   confirmed-only at `loan_resolver.py:565` before reaching the
   engine, so this matters for the engine's surface, not the
   resolver's. The composer's `monthly_override` replaces that
   "is there a payment record?" convention with an explicit
   forward-only parameter.

## The Fix

Three structural changes. Each layer compiles and tests
independently before the next is built.

### Layer 1: Engine primitives

Replace `generate_schedule` and `calculate_summary` with two narrow
functions whose APIs make the bug structurally impossible:

```python
@dataclass(frozen=True)
class ReplayResult:
    rows: list[AmortizationRow]      # is_confirmed=True throughout
    balance_as_of: Decimal           # at the close of as_of's month
    next_pay_date: date              # first projection month
    remaining_months_as_of: int      # term_months minus replayed
    applicable_rate_as_of: Decimal   # for project_forward to use

def replay_confirmed_history(
    *,
    origination_date: date,
    original_principal: Decimal,
    annual_rate: Decimal,
    term_months: int,
    payment_day: int,
    confirmed_payments: list[PaymentRecord],
    rate_changes: list[RateChangeRecord] | None,
    anchor_balance: Decimal | None,
    anchor_date: date | None,
    as_of: date,
) -> ReplayResult:
    """Deterministic replay of confirmed payments up to as_of.
    No extra_monthly parameter -- history cannot be 'what-if'ed.
    Pre-origination payments filtered.  Anchor (ARM) snaps balance
    at anchor_date.  Stops at the last payment_date <= as_of."""

def project_forward(
    *,
    starting_balance: Decimal,
    starting_date: date,             # first pay_date of projection
    annual_rate: Decimal,
    remaining_months: int,
    payment_day: int,
    contractual_payment: Decimal,    # frozen at projection start
    monthly_override: dict[tuple[int, int], Decimal] | None = None,
    extra_monthly: Decimal = Decimal("0.00"),
    rate_changes_remaining: list[RateChangeRecord] | None = None,
) -> list[AmortizationRow]:
    """Pure forward projection from a known starting state.
    monthly_override maps (year, month) to the user's planned
    payment (e.g., from projected transfer templates).  When an
    override exists for a month, it is used as the total payment
    and extra_monthly is NOT added.  When no override exists,
    contractual_payment + extra_monthly is used.  Cannot rewrite
    history -- has no concept of history."""
```

`as_of` is a first-class concept on the replay side; `extra_monthly`
lives only on the projection side. Mixing them is not expressible.

### Layer 2: Scenario composer in the resolver

Extend `loan_resolver` with one function that returns the full set
of payoff-calculator scenarios from one starting state:

```python
@dataclass(frozen=True)
class PayoffScenarios:
    # All three start with the same replayed history rows
    # (everything before as_of), then diverge.
    history_rows: list[AmortizationRow]
    original_forward: list[AmortizationRow]    # contractual, no extra
    committed_forward: list[AmortizationRow]   # planned, no extra
    accelerated_forward: list[AmortizationRow] # planned + extra

    # Summary metrics derived from the same forward slices.
    months_saved: int                # len(committed) - len(accelerated)
    interest_saved: Decimal          # sum(committed.interest) - sum(accelerated.interest)
    payoff_date_committed: date
    payoff_date_accelerated: date
    total_interest_committed: Decimal
    total_interest_accelerated: Decimal

def compute_payoff_scenarios(
    *,
    loan_params: LoanParams,
    anchor_events: list[AnchorEvent],
    payments: list[PaymentRecord],   # confirmed + projected
    rate_changes: list[RateChangeRecord] | None,
    extra_monthly: Decimal,
    as_of: date,
) -> PayoffScenarios:
    """Single source of truth for the payoff calculator.

    Calls replay_confirmed_history ONCE, then calls project_forward
    THREE times from the same (starting_balance, starting_date,
    remaining_months_as_of, applicable_rate_as_of):

      - Original:   monthly_override=None,     extra_monthly=0
      - Committed:  monthly_override=projected_by_month, extra_monthly=0
      - Accelerated: monthly_override=projected_by_month, extra_monthly=X

    Projected payments are routed through monthly_override (forward
    only).  Confirmed payments past as_of are treated as projections
    (rare; data hygiene).  Chart and summary cannot diverge because
    they derive from the same return value."""
```

The dashboard chart's Original / Committed / Floor series should
migrate to the same composer (Floor = "Committed with the
recurring-transfer projection truncated at today's date"), so every
chart on every loan surface goes through one function.

### Layer 3: Route collapse

`payoff_calculate` (`loan.py:1184-1364`) reduces to:

```python
scenarios = compute_payoff_scenarios(
    loan_params=params,
    anchor_events=anchor_events,
    payments=payments,
    rate_changes=rate_changes,
    extra_monthly=extra,
    as_of=date.today(),
)
chart_labels = [r.payment_date.strftime("%b %Y")
                for r in scenarios.history_rows + scenarios.original_forward]
chart_original    = _balances(scenarios.history_rows + scenarios.original_forward)
chart_committed   = _balances(scenarios.history_rows + scenarios.committed_forward)
chart_accelerated = _balances(scenarios.history_rows + scenarios.accelerated_forward)
# payoff_summary derived from scenarios.*  (same SSOT)
```

`dashboard` (`loan.py:488-749`) collapses analogously. The current
dashboard has FOUR engine touches: the resolver call (via
`_load_loan_context` at line 504, ultimately `loan_resolver.py:598`),
`planned_schedule` (line 533), `original_schedule` (line 589), and
`floor_schedule` (line 619). After the migration:

- chart `original` -> `_balances(scenarios.history_rows +
  scenarios.original_forward)`
- chart `committed` -> `_balances(scenarios.history_rows +
  scenarios.committed_forward)`
- chart `floor` -> a composer invocation with the projected
  portion of `payments` filtered out (committed-style, confirmed-
  only)
- `planned_schedule` -> `scenarios.history_rows +
  scenarios.committed_forward`. After Phase 6, this is also what
  `LoanState.schedule` will hold by construction, so the
  dashboard reads from `state.schedule` instead of generating its
  own planned schedule.

Every existing consumer of `planned_schedule` reads from one of
those slices unchanged: the amortization tab
(`amortization_schedule`), the payment breakdown
(`_compute_payment_breakdown`, `loan.py:574`), the schedule totals
(`_compute_schedule_totals`, `loan.py:697`), the recurrence
end_date update (`_update_transfer_end_date`, `loan.py:660`), and
the `summary` construction (`loan.py:557`). Per-row Payment /
Principal / Interest / Extra / Rate columns are still on
`AmortizationRow` and survive the migration unchanged. Phase 5
must walk this consumer list explicitly so none is missed.

`calculate_payoff_by_date` (binary search for required extra to hit
a target date) is reframed in terms of `project_forward` from
`loan_state.current_balance`. The current route at `loan.py:1337`
already passes `state.current_balance` as the starting principal
and `state.monthly_payment` as the contractual P&I, so the
starting balance is correct today; the migration is for
architectural consistency, not a bug fix. After migration it uses
`replay_confirmed_history` once to obtain the starting state,
then `project_forward` inside the binary search.

There IS a separate latent issue that Phase 7 SHOULD decide
explicitly: the function does not accept `payments`, so projected
transfers from a recurring template (part of the user's actual
planned monthly outflow) do not factor into "required extra." A
user already paying $500/mo over contractual through their
template is told they need $X extra, when in reality they need
$X - $500. Adding `monthly_override` to the Phase 7 binary search
closes this; omitting it preserves current behavior. Pick one
deliberately, do not let the choice ride as an incidental side
effect of the refactor.

## Why This Prevents The Whole Class Of Bug

| Failure mode | Current design allows it because... | New design prevents it because... |
|---|---|---|
| Extra applied to historical months | `extra_monthly` is gated on "no record," conflating past gaps with future months | `extra_monthly` is a parameter of `project_forward` only; replay has no such parameter |
| Projected payments suppressing extra forward | Engine treats projected and confirmed identically | Projections go through `monthly_override` of `project_forward`; confirmed payments stop being relevant after `as_of` |
| Chart and summary numbers disagreeing | Route builds them from independent engine calls with hand-chosen params | Both derive from one `PayoffScenarios` return value |
| Chart starting from wrong balance | Each caller passes `current_principal` with its own interpretation (origination vs. now) | `project_forward(starting_balance=replay.balance_as_of)` -- no caller chooses the interpretation |
| Future scenario (refinance, lump-sum, rate what-if) repeating this class of bug | New scenarios added as new parameter combinations on `generate_schedule` | New scenarios added as new compositions in the resolver, reusing the two primitives unchanged |
| ARM anchor and extra interacting in subtle ways | Two overlapping temporal concepts (`anchor_date`, hypothetical `extra_starts_at`) | One temporal concept: `as_of`. Anchor is just "replay starts from a verified balance instead of original principal" -- still a replay primitive |
| Round-tripping a schedule re-applies extra | Implicit -- the engine doesn't distinguish replay from projection | Replay rows are flagged `is_confirmed=True`; projection rows aren't; cannot be confused even if recombined |

## Migration Ordering

Eight phases. Each phase ends with a green targeted-test run before
the next phase begins. The full suite runs as the gate at the end
of each PR-equivalent boundary (phases 3, 5, 7, 8).

**Phase 1 -- Add `replay_confirmed_history`.** New function in
`amortization_engine.py`. No removals. No callers yet. Pylint
clean.

**Phase 2 -- Add `project_forward`.** Same module. Same rules.

**Phase 3 -- Add `compute_payoff_scenarios` in `loan_resolver.py`.**
Uses the two new primitives. No callers yet. Full suite green at
the end of this phase.

**Phase 4 -- Migrate `payoff_calculate` to the composer.** Delete
the route's three direct engine calls and its
`calculate_summary` call. Update `_payoff_results.html` if any
context-key names changed. Update route tests.

**Phase 5 -- Migrate `dashboard` chart paths to the composer.**
Original / Committed / Floor all derive from
`compute_payoff_scenarios` (Floor = composer call with the
projected portion of `payments` filtered out). Update dashboard
template if any context-key names changed. Full suite green.

**Phase 6 -- Migrate `loan_resolver.resolve_loan` internals.**
Replace its `generate_schedule` call (line 598) with
`replay_confirmed_history` + `project_forward`. `LoanState.schedule`
becomes `history_rows + forward_rows` from a "Committed with no
extra" composer call. This is the single chokepoint other callers
(year-end summary, debt aggregation) already read through.

**Phase 7 -- Migrate `calculate_payoff_by_date` and the refinance
calculator.** Both reframe in terms of the two primitives. The
refinance calculator's existing
`current_principal_after_closing_costs` logic is the
`starting_balance` input to a new projection; no scenario
composition needed (it's a one-off).

**Phase 8 -- Delete `generate_schedule` and `calculate_summary`.**
Once no production code calls them, delete both functions, delete
all of their dedicated unit tests, and replace those tests with
equivalent coverage on the two new primitives + the composer
(many of these tests will already exist by phase 8).

One non-obvious dependency MUST be handled before the deletion:
the
`migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py`
backfill imports `amortization_engine` and calls
`generate_schedule` at line 315 to derive the from-origination
balance after replaying confirmed payments. If `generate_schedule`
is deleted outright, a fresh database rebuild that replays
migrations will fail at that revision. The backfill only needs
"confirmed payments reduce balance" math (no `extra_monthly`, no
rate-change re-amortization, no projected entries). The
recommended fix is to **inline the small replay loop directly
into the migration** so the migration is self-contained against
future engine refactors -- migrations are supposed to survive
service-layer rewrites. Less-preferred alternatives:
(a) keep a thin shim of `generate_schedule` in the engine that
proxies to `replay_confirmed_history` for the backfill caller
only, or
(b) squash the migration (destructive; project policy keeps full
migration history -- not acceptable).
Pick the inline option before deleting the engine functions.

## Test Pyramid

The pyramid mirrors the layering. Each layer's tests assert
properties that layer is responsible for; nothing crosses layers.

### Engine primitives (unit, fastest)

`tests/test_services/test_amortization_engine.py` adds two new test
classes.

**`TestReplayConfirmedHistory`:**

- Empty `confirmed_payments`: returns zero rows, `balance_as_of ==
  original_principal`, `next_pay_date == origination_date + 1
  month`, `remaining_months_as_of == term_months`.
- Single confirmed payment in month 1: one row, balance reduced by
  principal portion, `is_confirmed == True`, `next_pay_date ==
  month 2`.
- Multiple confirmed payments spanning months 1-N: N rows, balance
  monotonically decreasing, all rows `is_confirmed == True`.
- Gap in payments (months 1, 2, 4 confirmed; month 3 missing):
  three rows for months 1, 2, 4 -- replay does NOT fabricate
  contractual rows for missed months. Replay returns only what was
  recorded; the missing month is the caller's responsibility to
  reason about. (Distinguishes replay from projection cleanly.)
- Confirmed payments past `as_of`: filtered to `<= as_of`.
- Pre-origination payments: filtered (existing behavior).
- ARM with `anchor_balance` / `anchor_date` set: balance snaps at
  anchor; `balance_as_of` derived from post-anchor replay.
- Rate change during replay: applicable rate updates per existing
  engine logic; `applicable_rate_as_of` returns the last-applied
  rate.
- Hand-computed balance check: 30 yr / $300k @ 6%, three
  contractual payments in months 1-3, assert `balance_as_of` ==
  the value `generate_schedule` would have produced for that row
  (cross-check during migration; deleted in phase 8).

**`TestProjectForward`:**

- No `monthly_override`, no extra: produces the contractual
  schedule from `starting_balance` over `remaining_months`. Final
  row absorbs balance.
- `monthly_override` only (no extra): each override month uses the
  override amount as total payment, P&I split correctly,
  `extra_payment` field is 0.
- `extra_monthly` only (no override): contractual payment +
  extra applied every month. Extra capped at remaining balance for
  the final month.
- `monthly_override` + `extra_monthly`: extra applied only to months
  WITHOUT an override. Override months have `extra_payment` field
  == 0. (The critical regression-prevention assertion.)
- Override amount below interest-only: principal portion is
  negative (existing neg-am behavior preserved).
- ARM `rate_changes_remaining` triggers mid-projection: payment
  recomputes at the new rate per existing engine logic.
- Hand-computed payoff-date check: $279,985 starting balance, 6%,
  336 remaining months, $200 extra -> assert `len(rows)` and final
  `payment_date` match an independently-computed value.

### Scenario composer (integration, mid-speed)

`tests/test_services/test_loan_resolver.py` (or a sibling) adds
`TestComputePayoffScenarios`.

- **History is shared.** `len(history_rows)` matches the count of
  confirmed payments at or before `as_of`. Each history row's
  `payment_date` is at or before `as_of`.
- **Same starting balance.** First row of each of
  `original_forward`, `committed_forward`, `accelerated_forward`
  starts from the same balance (the replay's `balance_as_of`
  minus that row's principal payment).
- **First row date.** First row of each forward series has
  `payment_date == replay.next_pay_date`. (Catches the bug class
  "Accelerated curve started one month earlier / later than the
  others.")
- **Pre-`as_of` rows are identical across scenarios.** For chart
  rendering, `history_rows + any_forward` is plotted; the
  `history_rows` prefix is byte-identical regardless of scenario
  parameters. Assert by reference.
- **Original ignores projections and extra.**
  `original_forward[i].payment` equals contractual P&I for every
  row.
- **Committed honors projections.** Where `monthly_override` has a
  value, `committed_forward[i].payment` equals that value.
- **Accelerated honors projections AND extra.** Where
  `monthly_override` has a value, `accelerated_forward[i].payment`
  equals that value and `extra_payment == 0`. Where no override
  exists, `payment == contractual` and `extra_payment ==
  extra_monthly` (or capped at remaining balance for the final
  row).
- **Summary metrics derive from the forward slices.**
  `months_saved == len(committed_forward) - len(accelerated_forward)`.
  `interest_saved ==
   sum(committed_forward.interest) - sum(accelerated_forward.interest)`.
  History is excluded.
- **The originally reported bug, regression test.** 30 yr / $300k @
  6%, originated 2024-01-01, four confirmed contractual payments
  Jan-Apr 2026 (matching the reproduction in this plan), no
  projected transfers, `extra_monthly=$500`, `as_of=2026-05-21`.
  Assert:
  - `len(history_rows) == 4` (Jan-Apr 2026 only; nothing
    fabricated for 2024-2025).
  - Every row of `accelerated_forward[i]` past `as_of` has
    `extra_payment == $500` (no override exists for those months).
  - `accelerated_forward[0].payment_date == 2026-05-01` (first
    post-`as_of` month, no fictitious 2024 acceleration).
  - `months_saved` matches a hand-computed value derived from a
    $279,985-ish starting balance with $500 extra (not the
    inflated value the buggy code returns).

#### Why no existing test caught this

`TestPaymentAwareSchedule` in
`tests/test_services/test_amortization_engine.py:811` exercises
the `payments` + `extra_monthly` interaction but does not catch
the bug because its `ORIGINATION = date(2026, 1, 1)` and its
first confirmed payment is on `date(2026, 2, 15)` -- the same
month as schedule row 0. There is no gap between origination and
confirmed history, so the "extra applied to ghost historical
months" pathway never fires. The
`test_extra_monthly_not_added_when_payment_exists` assertion
passes for the local correct reason while the systemic bug ships
unobserved. The regression test above is the FIRST test in the
suite that exercises a temporal gap between origination and
confirmed history, which is precisely the shape that surfaces
the defect. Phases 1-3 should retain that test design property
in all new unit and composer tests: any scenario that does not
include a multi-month gap between origination and the first
confirmed payment cannot distinguish the buggy and fixed
implementations.

### Route (HTTP integration)

`tests/test_routes/test_loan.py` adds three tests.

- **Chart JSON shape and content.** POST to
  `/accounts/<id>/loan/payoff` with `mode=extra_payment` and a
  nonzero extra. Parse `data-original`, `data-committed`,
  `data-accelerated` from the rendered partial. Assert:
  - Lengths are equal (all three plotted against the same x-axis
    of original-schedule labels).
  - For every index `i` whose label is at or before today's month,
    `data-accelerated[i] == data-committed[i]`. (Catches the
    user's reported visual bug at the HTTP layer.)
  - For at least one index past today, strict inequality.
- **Summary numbers consistent with chart.** Parse the displayed
  `Months Saved` and `Interest Saved` from the same response.
  Assert they match the divergence count and sum visible in the
  chart arrays.
- **No payment history case.** Loan with zero confirmed payments,
  extra payment requested. Assert no history rows are rendered,
  and the chart starts at origination with the Original / Committed
  / Accelerated overlay computed purely from projection.

### Existing invariants must still pass

- `tests/test_integration/test_loan_unified_figures.py::test_..._months_saved...`
  (F-022 "no parallel computation path"): must still pass without
  modification. The composer becomes the single path, so by
  construction the assertion holds.
- Year-end debt aggregation tests: unaffected (they read
  `LoanState.schedule`, which still exists, now sourced from the
  composer in phase 6).
- Debt-strategy tests: unaffected (debt-strategy uses its own
  `extra_pool` allocation, not `extra_monthly`).
- Refinance tests: re-baseline in phase 7.

## Critical Files

- `app/services/amortization_engine.py` -- add primitives in
  phases 1-2, delete `generate_schedule` / `calculate_summary` in
  phase 8.
- `migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py:315`
  -- inline the small replay loop in phase 8 so the migration
  stops depending on the deleted `generate_schedule`. Without
  this, fresh database rebuilds will fail at that revision.
- `app/services/loan_resolver.py` -- add composer in phase 3,
  switch internals in phase 6.
- `app/routes/loan.py:488-749` (`dashboard`) -- migrate in
  phase 5.
- `app/routes/loan.py:1184-1364` (`payoff_calculate`) -- migrate
  in phase 4. The `mode == "extra_payment"` branch (1224-1312)
  has the three direct engine calls; the `mode == "target_date"`
  branch (1314-1359) calls `calculate_payoff_by_date` and is
  migrated in phase 7.
- `app/routes/loan.py` `refinance_calculate` -- migrate in
  phase 7.
- `app/templates/loan/_payoff_results.html` and
  `app/templates/loan/dashboard.html` -- adjust context keys if
  they change in phases 4-5.
- `tests/test_services/test_amortization_engine.py` -- add primitive
  tests in phases 1-2, delete old-function tests in phase 8.
- `tests/test_services/test_loan_resolver.py` -- add composer
  tests in phase 3.
- `tests/test_routes/test_loan.py` -- add route tests in phase 4.
- `tests/test_integration/test_loan_unified_figures.py` -- existing
  invariants, no changes expected.

## Verification

End-to-end after phase 5 (the user-visible fix is live):

1. Open `/accounts/3/loan`, run the Payoff Calculator with a nonzero
   extra. The Accelerated line tracks the Committed line through
   the historical region. It departs from Committed only at and
   after today's month boundary. The "Months Saved" / "Interest
   Saved" labels match the chart's visible divergence.
2. Try the same on a loan with a recurring transfer template
   generating projected future payments. Extra is still applied
   forward (projections route through `monthly_override`, do not
   suppress extra in the projection layer).
3. Try with a loan that has zero confirmed payments yet (newly set
   up). Chart starts at origination with Original / Committed /
   Accelerated overlaying correctly.
4. Year-end debt summary and debt strategy unchanged.
5. `pylint app/ --fail-on=E,F` clean.
6. `./scripts/test.sh` full suite green.
