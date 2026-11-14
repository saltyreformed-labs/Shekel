# Property detail follow-ups (equity chart)

Durable register of the equity-chart producer defects. Written to be executable cold in a fresh
session: current behavior, reproduced evidence, root cause, production reachability, fix direction,
and the acceptance test each defect must satisfy.

**2026-07-12 adversarial verification supersedes the original items 1-2.** Three correctness defects
(H1-H3) were reproduced against the real producers (not the docstrings) and confirmed; the prior
"front-aligned merge" item is now H2, upgraded from cosmetic to a hero-reconciliation break. The
double-resolution DRY item is retained as D1. All findings live in
`app/services/property_equity_chart.py` unless noted.

**STATUS (2026-07-12): H1 / H2 / H3 + D1 are FIXED on `dev`** by the date-anchored rebuild (commits
1 `247f8321` + 2 `4e6e63c2`); the reproductions below are now the passing regression tests in
`tests/test_routes/test_property/test_the_equity_chart_producer.py`. The rebuild is NOT yet
PR'd/shipped -- 2 owed tests + the Fable visual (commit 5) remain. Live status, commit ledger, and
the owed-test recipe: `docs/plans/historical/implementation_plan_property_equity_chart_rebuild.md`.

## Root cause shared by H1-H3

The producer reasons in **loan-schedule-index space** and assumes "today" coincides with the
confirmed / projected boundary. That assumption holds ONLY for the one shape the current tests use
(loans originated `date.today()`, no confirmed history, single or same-start). Three boundaries that
must be keyed to **calendar dates relative to `today`** are instead keyed to schedule structure:

| Boundary | Must be | Currently is | Breaks when |
| --- | --- | --- | --- |
| fallback / no-debt (H1) | no *future* outstanding balance | `schedule` empty | a loan is paid off |
| value flat vs compound (H3) | `month_date <= today` | `index <= current_index` | past-dated projected rows |
| multi-loan axis (H2) | date-aligned calendar axis | front-aligned at index 0 | loans of different age |

Each was reproduced with a passing test; those reproductions are the regression gate for the fix.
They use `FakeLoanParams` / `_origination_anchor` / `_rate_feed` from
`tests/test_services/test_loan_resolver.py` and the `_make_property` / `create_loan_account`
helpers.

## H1. The paid-off fallback is unreachable (correctness)

**Current behavior.** `_resolve_secured_schedules` skips a loan only when `state.schedule` is empty
(`if state.schedule:`), and the module docstring asserts "a paid-off loan (its schedule is empty)."
That is false: `LoanState.schedule == history_rows + committed_forward`. A loan paid off through
confirmed payments has an EMPTY `committed_forward` but keeps every confirmed history row, so
`state.schedule` is non-empty and the loan is never skipped. The `no_loans` / 120-month appreciation
fallback the design promises for a paid-off property is dead code.

Worse than a dead branch: because every retained row is confirmed, `current_index` (below) becomes
the full schedule length, so the value line is flat end to end and the x-axis terminates at the
payoff month -- in the PAST -- with no forward appreciation arc at all.

**Evidence (reproduced, passing).** A 12-month loan paid off by 12 confirmed payments:

```text
current_balance = 0.00      len(schedule) = 12      confirmed rows = 12
last row balance = 0.00      `if state.schedule:` => True  (NOT skipped => fallback never fires)
```

```python
def test_paid_off_loan_keeps_history_so_fallback_is_unreachable():
    """H1: a loan paid off via confirmed payments keeps a non-empty schedule."""
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1), term_months=12,
        original_principal=Decimal("12000.00"), interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    payments = [
        PaymentRecord(payment_date=d, amount=Decimal("1032.80"), is_confirmed=True)
        for d in ([date(2026, m, 1) for m in range(2, 13)] + [date(2027, 1, 1)])
    ]
    state = resolve_loan(
        LoanInputs(params, [_origination_anchor(params)], payments, _rate_feed(params)),
        date(2027, 2, 1),
    )
    assert state.current_balance == Decimal("0.00")          # paid off
    assert state.schedule != []                              # but schedule is NON-empty
    assert all(row.is_confirmed for row in state.schedule)   # all confirmed history
    # => _resolve_secured_schedules keeps it; chart_state is never "no_loans".
```

**Reachability.** Every linked mortgage that is eventually paid off and stays linked as collateral.

**Fix direction.** Decide "is there outstanding secured debt to chart" from **future** balance, not
schedule emptiness: a loan contributes to the debt line only if it has a positive balance on or
after `today` (equivalently, its resolved `current_balance > 0`, or its schedule has a row with
`payment_date >= today` and a non-zero balance). A property whose every secured loan is paid off
returns the `no_loans` fallback.

**Acceptance.** A property with one fully-paid-off secured loan yields `chart_state == "no_loans"`,
`debt == []`, `equity == []`, and a 120-month forward value arc. The reproduction above stays green
as the documentation of the pre-fix trap. Full suite green; pylint 10.00.

## H2. Multi-loan merge front-aligns, breaking hero reconciliation (correctness -- was item 2)

**Current behavior.** `build_property_equity_chart` merges multiple secured loans through the shared
`app.utils.chart_series.build_chart_series`, which front-aligns every series at index 0 and pads the
tail. Two loans originated in different calendar months are therefore summed against MISALIGNED
months: the younger loan's balance is added into columns that predate its origination, and no column
of the summed debt line equals the equity hero's `total_debt`. This contradicts the producer
docstring's core promise ("the chart's debt line and the equity hero cannot disagree").

**Evidence (reproduced, passing).** A $300k loan originated 36 months ago and a $50k loan originated
1 month ago, both securing the property:

```text
axis label[0] = Aug 2023                       new loan first sched date = 2026-07-01
debt[0] = 349317.54 = old_sched[0] 299639.54 + new_sched[0] 49678.00   <- new loan in a 2023 column
hero total_debt = 350000.00      any chart.debt column == hero total_debt?  = False
current_index = 0  =>  the "Today" marker is drawn at label "Aug 2023" (3 years in the past)
```

```python
def test_multiloan_frontalign_disagrees_with_hero(app, db, seed_user, seed_periods_today):
    """H2: a younger loan's balance is summed into months before it existed."""
    with app.app_context():
        today, scenario_id = date.today(), seed_user["scenario"].id
        prop = _make_property(db, seed_user, seed_periods_today, rate=Decimal("0"))
        old = create_loan_account(seed_user, db.session, name="Old",
            principal=Decimal("300000.00"), term=360, origination_date=add_months(today, -36))
        new = create_loan_account(seed_user, db.session, name="New",
            principal=Decimal("50000.00"), term=120, origination_date=add_months(today, -1))
        old.collateral_account_id = new.collateral_account_id = prop.id
        db.session.commit()
        chart = build_property_equity_chart(prop, scenario_id, Decimal("0"), today)
        equity = home_equity_service.resolve_home_equity(prop, scenario_id, today)
        # The new loan's origination-era balance lands in the OLD loan's first (2023) column,
        # and NO column of the debt line reconciles with the hero chip above it:
        assert not any(col == equity.total_debt for col in chart.debt)
```

**Reachability.** Any property securing two or more loans of different age -- mortgage + HELOC (a
case the codebase already anticipates in `test_home_equity.py::test_two_loans_sum`), a second
mortgage, or a refinance overlap. Not hit by the single current mortgage, but a real, ordinary
configuration.

**Financial ruling (RULED 2026-07-12: contractual back-projection).** What does a loan's debt line
mean before its own data begins? Months strictly before ORIGINATION: `$0.00` (the loan did not
exist). Months between origination and TRACKING START (the confirmed view begins at tracking start):
the balance is UNKNOWN, not zero -- zero-filling draws a false debt dip and overstates equity. The
developer ruled **(a) contractual back-projection**: amortize the pre-tracking months from the known
origination terms (principal / rate / term), drawn as an `estimated` tier distinct from confirmed
history; the tracking-start seam discontinuity (contractual vs recorded) is shown honestly, never
smoothed. Full mechanics in the implementation plan.

**Fix direction.** Merge on a shared **calendar-month axis** spanning
`min(first month) .. max(last month)`; place each loan's schedule by DATE; zero-fill strictly
pre-origination months; for unknown pre-tracking months, per the ruling above either flat-carry the
earliest known balance with an honest caption, or start the merged axis at the latest tracking
start. The single-mortgage case is unchanged (one series, nothing to align).

**Acceptance.** With two differently-originated loans, every debt column equals the sum of the
loans' balances *for that calendar month* (zero for a loan not yet originated), and the column at
`today` equals the hero `total_debt` to the cent. Reproduction above flips to asserting agreement.

## H3. Past-dated projected months get a fabricated market value (correctness)

**Current behavior.** `_value_series` chooses flat vs compounding by `index <= current_index`, where
`current_index` is a confirmed-ROW COUNT, not the position of `today`. When a schedule has
past-dated PROJECTED rows (index past `current_index` yet `payment_date < today` -- a loan whose
confirmed history does not run up to today), the branch tries to compound the anchor to a PAST date.
The span `(month_date - today).days + 1` goes non-positive, and `growth_engine.period_return_rate`
silently clamps it to 14 days ("fallback for degenerate (inverted) periods"). Every such month then
gets the identical factor `(1 + rate) ** (14 / 365)`, inventing appreciation in the past.

**Evidence (reproduced, passing).** One
$100k loan originated 18 months ago, no confirmed payments, 3%/yr, $400k property:

```text
current_index = 0      # past-dated projected rows = 18
value at past indices (distinct) = [400000.00, 400453.76]    # 400453.76 = 400000 * 1.03**(14/365)
count of the phantom value = 17          value[0] = 400000.00 (spared only by the index<=0 guard)
```

```python
def test_past_dated_projected_months_get_phantom_value(app, db, seed_user, seed_periods_today):
    """H3: stale (past-dated projected) months get a clamped phantom market value."""
    with app.app_context():
        today, scenario_id = date.today(), seed_user["scenario"].id
        prop = _make_property(db, seed_user, seed_periods_today, rate=Decimal("0.03000"))
        loan = create_loan_account(seed_user, db.session, name="Stale",
            principal=Decimal("100000.00"), term=120, origination_date=add_months(today, -18))
        loan.collateral_account_id = prop.id
        db.session.commit()
        chart = build_property_equity_chart(prop, scenario_id, Decimal("0.03000"), today)
        sched = _resolve_secured_schedules(prop, scenario_id, today)[str(loan.id)]
        past = [chart.value[i] for i, r in enumerate(sched) if r.payment_date < today]
        # A past month must hold the anchor flat, never a compounded phantom above it:
        assert all(v == Decimal("400000.00") for v in past)   # FAILS today: 17 are 400453.76
```

**Reachability.** Any linked loan whose confirmed history does not extend to today: an un-backfilled
ledger, a couple of unrecorded recent payments, or an old loan just linked. The single real mortgage
likely shows a one-or-two-month version of this unless payments are entered right up to the current
date. Common, not hypothetical.

**Fix direction.** Key the value line's flat / compound split to the **date**: hold the anchor flat
for every `month_date <= today`, compound only strictly after. Independently, harden
`_AppreciationSpan` / the appreciation call so a non-positive span returns "no elapsed growth"
(factor 1) rather than borrowing `period_return_rate`'s 14-day inverted-period clamp, which is a
pay-period footgun in an appreciation context.

**Acceptance.** For a loan with past-dated projected rows, every value point at a
`month_date <= today` equals the anchor exactly; the "Today" marker (`current_index`) sits at the
last month `<= today`, never in the past. Reproduction above flips to `all(v == anchor)`.

## D1. Double loan resolution per property page load (DRY -- was item 1)

**Current behavior.** One `GET /accounts/<id>/property` resolves every secured loan TWICE through
`app.services.loan_resolution.resolve_account_loan` -- the heaviest computation on the page
(schedule replay + full forward projection): once in `home_equity_service.resolve_home_equity` (for
`current_balance`, the hero + chips) and again in `build_property_equity_chart` /
`_resolve_secured_schedules` (for `state.schedule`, the debt line). The route also calls
`date.today()` twice, so the two surfaces can resolve on different days across a midnight boundary.

**Fix design.** Resolve each secured loan ONCE in the route (or a thin orchestrator) and thread the
resolved `LoanState` into both consumers.
`home_equity_service.compute_home_equity(market_value, balances)` already accepts pre-resolved
balances; `build_property_equity_chart` should accept the schedules (see the Remediation approach --
Option 3 folds this in by making the producer pure). Do not weaken the reconciliation property:
hero, chips, and chart derive from the one resolution pass.

**Acceptance.** Exactly one `resolve_account_loan` per secured loan per `property_detail` GET
(call-counting spy in a route test); one `date.today()`; hero / chips / chart still reconcile to the
cent; no other `home_equity_service` consumer gains a second resolution path.

## Remediation approach

H1 and H3 are property-chart-local; H2's merge and D1's resolution are shared concerns. Three ways
to land the fix, cheapest to most-correct:

- **Option 1 -- local symptom patches.** Swap the H1 predicate, date-key the H3 split, date-align
  the H2 merge inline; leave the module shape and the double-resolution. Fastest; leaves the
  index-based structure, pressures the shared `build_chart_series`, and does not touch D1 or the
  financial ruling.
- **Option 2 -- rebuild the producer on a calendar-date axis, contained to
  `property_equity_chart.py`.** One monthly axis anchored at `today`; date-aligned merge; value
  split and fallback keyed to `today`. Fixes H1-H3 at the root with a small blast radius (loan band
  untouched), but re-implements a date-merge beside the front-align primitive and defers D1.
- **Option 3 -- full correct rebuild (recommended).** Date-anchored axis; generalize the shared axis
  primitive to date-align (the loan band's same-start series stay bit-identical, since date-align
  reduces to front-align when the dates coincide -- re-verified by the 53 loan-chart tests); make
  `build_property_equity_chart` a PURE function fed pre-resolved `LoanState`s so the route resolves
  each loan ONCE (folds in D1); rule the pre-origination / pre-tracking debt semantics (H2) at a
  proper gate with worked examples. Largest scope; most DRY / SOLID / robust / financially correct.

**Decision (2026-07-12): Option 3, with H2 ruled as (a) contractual back-projection.** Full
implementation plan (data contract, per-loan five-region debt series, date-keyed value, resolve-once
purity, shared-primitive generalization, test plan, sequencing):
`docs/plans/historical/implementation_plan_property_equity_chart_rebuild.md`.

## Scratch mockup retention (developer instruction, 2026-07-12)

The Loop A mockup and screenshots are RETAINED (overriding the delete-at-Loop-B-close default) for
the developer's acceptance check. Durable copy (outside the repo, per the anti-anchoring rule -- do
not commit these): `/home/josh/projects/shekel_theme/property_equity_loop_a/`
(`property_equity_explore.html`, `shots/` mockup rounds, `shots-live/` live-app acceptance shots).
Delete after the developer's check, not before.
