> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Amortization Engine Split -- Implementation Plan

- Version: 1.0
- Date: 2026-05-21
- Author: prepared for the solo developer (SaltyReformed)
- Architectural plan this implements:
  `docs/plans/2026-05-21-amortization-engine-split-replay-projection.md`
  (the "what and why")
- Prerequisite reading (per
  `docs/audits/financial_calculations/remediation_follow_up_common.md`):
  the architectural plan above, `CLAUDE.md` (Rules, Transfer
  Invariants), `docs/coding-standards.md`,
  `docs/testing-standards.md`, and this file in full before any code
  is edited.
- Standards: every commit follows
  `docs/audits/financial_calculations/remediation_follow_up_common.md`
  (work summary labels A-M, test-run conventions, pylint
  conventions, common grep guards).

---

## 0. Context

The Payoff Calculator on `/accounts/<id>/loan` renders an Accelerated
chart that diverges from the Original schedule starting at the
**origination date**, runs visually parallel to the Original through
the confirmed-payment window around today, then resumes its
accelerated descent after today. The same misuse corrupts the
`Months Saved`, `Interest Saved`, `Total Interest`, and
`Projected Payoff` numbers.

The architectural plan (above) traces this to a single defect in
`app/services/amortization_engine.py:generate_schedule`: replay of
confirmed history and projection of the future live in one loop,
sharing one parameter (`extra_monthly`) whose semantics had to be
"apply when no PaymentRecord exists for this month" -- because the
function has no other way to express "apply going forward." That
definition makes the bug possible: months with no payment record
(everything between origination and the first confirmed payment)
silently accept `extra_monthly`, producing a fictitious accelerated
past.

Verification against live code (see Section 3) confirmed the
diagnosis and the proposed shape of the fix: split the engine into
two narrow primitives -- `replay_confirmed_history` (deterministic
past, no `extra_monthly`) and `project_forward` (parameterized
future, `extra_monthly` plus an explicit `monthly_override` for
projected transfers) -- and add a `compute_payoff_scenarios`
composer in `loan_resolver` so chart and summary derive from one
return value. This makes the bug syntactically unexpressible and
matches the architectural trajectory the codebase already telegraphs
("Commit 17 will collapse these direct `generate_schedule` calls
into the resolver" -- `app/routes/loan.py:583-585`, `:1219-1220`).

### Consequence of getting this wrong

The loan amortization engine drives the loan card balance, payoff
projections, schedule rows, debt strategy ordering, year-end debt
aggregation, and refinance comparisons. A residual defect in the new
primitives propagates everywhere at once. The test pyramid in this
plan (primitive unit tests, composer integration tests with a
multi-month gap between origination and confirmed history, the HTTP
route test that captures the user's exact reproduction) exists
specifically so a future change cannot silently reintroduce
"extra applied to ghost historical months" -- the gap class
`tests/test_services/test_amortization_engine.py::TestPaymentAwareSchedule`
missed by setting `ORIGINATION = date(2026, 1, 1)` with its first
payment in the same month.

This plan is verified against the live code as of 2026-05-21
(`main` at commit `527393c`). Audit-style line numbers below are
re-grep targets, not fixed coordinates; the architectural plan
already documents one (the `payoff_calculate` 1184-1364 line range).

---

## 1. Hard rules for executing this plan

These bind every commit. They restate
`docs/audits/financial_calculations/remediation_follow_up_common.md`
in the context of this implementation; the union of the common
rules and the per-commit specifics holds.

1. **The plan's specification for a commit is the floor, not the
   ceiling.** If verification surfaces an in-scope refinement
   (e.g. a line range that drifted, a fourth consumer of
   `planned_schedule` not enumerated in Section 9), fold it in and
   explain in the work summary.
2. **Read the entire file before editing it.** No edit by remembered
   line number. The original architectural plan's "loan.py:1184-1312"
   was wrong (actual `1184-1364`); assume every range is suspect
   until re-grepped.
3. **Never modify a test to make it pass** except the documented
   exception: tests pinning a wrong shipping number this finding
   proves wrong. The payoff-calculator chart tests are the only
   candidates here and they currently DO NOT exist (verified --
   `tests/test_routes/test_loan.py::TestPayoffCalculator` asserts
   only that the partial renders with "Months Saved" present, not
   the chart values). New tests are net additions, not re-pins; the
   work summary's "D. Re-pinned tests" section is therefore "none"
   for almost every commit in this plan. Exceptions are called out
   per commit.
4. **Decimal money from strings, IDs not name strings for ref-table
   logic, no `Status.name` comparisons, DRY/SOLID, fully
   normalized.** No band-aid override parameters that bypass broken
   internals; the engine split IS the root-cause fix and must not
   be patched around. Use `app.utils.money.round_money` as the only
   rounding boundary; no bare `Decimal("0.01")` quantize in
   production code (existing engine sites already pass
   `ROUND_HALF_UP` explicitly and stay correct).
5. **Atomic commits, suite green after each.** Targeted tests
   (`./scripts/test.sh tests/path -v`) during edits; `pylint app/
   --fail-on=E,F` clean after every commit; the full suite
   (`./scripts/test.sh`, ~65 s at `-n 12`) as the final per-commit
   gate. Migrations (Commit 8) round-trip
   `flask db upgrade -> downgrade -> upgrade`. Rebuild the test
   template (`python scripts/build_test_template.py`) only if the
   schema or `app/audit_infrastructure.py` changes -- this plan
   does NOT change either (no new tables, no new triggers), so the
   rebuild is not needed.
6. **Stay in scope.** Out-of-scope issues spotted during
   verification go to `J. OUT OF SCOPE -- flagged, not fixed` in
   the work summary with `file:line` + reason; add an `F-N` entry
   in `docs/audits/financial_calculations/remediation_follow_up.md`
   if not directly handled by a future commit in this plan.
7. **Do not push.** After green, present the work summary and ASK
   whether to commit and push to `dev` (this triggers CI;
   PR-to-`main` is the promotion path).
8. **Style.** No Unicode em/en dashes (use `--` or `-`). Pythonic,
   type-hinted, substantive docstrings, specific exceptions, no
   broad `except Exception`.

---

## 2. Design decisions (made at plan time; confirm at review)

- **D-A. Two primitives plus a composer, not a fix-in-place.** The
  alternatives (gate `extra_monthly` on `is_confirmed=False`; add a
  `replay_through_date` parameter; compose at the resolver while
  keeping the engine) each leave a re-entry path for the same class
  of bug. Splitting `generate_schedule` into
  `replay_confirmed_history` + `project_forward` makes the buggy
  parameter combination syntactically unexpressible; the composer
  collapses scenario composition off the routes. This is the only
  approach that matches the user's stated goal of "do not introduce
  more problems" and aligns with the codebase's existing trajectory
  toward `loan_resolver` as the loan SSOT.
- **D-B. Projected payments route through `monthly_override` on the
  projection side ONLY.** The replay side has no concept of a
  monthly override; history is what it is. The composer maps every
  projected payment in the input `payments` list to a
  `(year, month) -> Decimal` entry on the override dict. Confirmed
  payments past `as_of` are treated as projections (rare; data
  hygiene) and routed the same way.
- **D-C. The composer is in `loan_resolver`, not a new module.**
  `loan_resolver` is already the established SSOT for loan figures
  (E-18 / Commit 13 from the financial-calculation remediation);
  putting `compute_payoff_scenarios` there matches the existing
  surface every consumer reads through and avoids inventing a new
  service boundary.
- **D-D. `LoanState.schedule` becomes the committed-no-extra
  composition after Phase 6.** The resolver's `state.schedule` is
  redefined as `history_rows + committed_forward`, which is the
  shape every existing consumer (debt strategy, savings dashboard,
  year-end summary, refinance) already reads as "the user's planned
  trajectory." This is a clarification of semantics, not a
  behavioral change for those callers.
- **D-E. Phase 8 inlines the small replay loop into migration
  `d3d25212504b` rather than keeping a `generate_schedule` shim.**
  Migrations should survive service-layer rewrites. The shim option
  (option (a) in the architectural plan) is explicitly rejected
  here because it keeps a dead surface alive in production code
  indefinitely; the inline option (option (a) in the architectural
  plan's refinement #1) is self-contained and the recommended
  path.
- **D-F. `calculate_payoff_by_date` migrates for architectural
  consistency only, NOT to fix the "projected payments ignored in
  required-extra" latent issue.** The latent issue is real but is
  a separate behavior change that deserves its own user-facing
  decision (a current $500/mo overpayer would suddenly be told they
  need less extra than the UI used to say). This plan does the
  refactor; the behavior change is left to a follow-up entry in
  `docs/audits/financial_calculations/remediation_follow_up.md`.
  Documented per the architectural plan's refinement #2.

---

## 3. Discovered refinements beyond the architectural plan (folded into scope)

Live-code verification of `2026-05-21-amortization-engine-split-replay-projection.md`
confirmed every core claim and surfaced five corrections / scope
expansions. These are folded into the relevant commits, not left as
plan-vs-code gaps. (See the verification report at
`/home/josh/.claude/plans/radiant-riding-floyd.md` -- not in-repo;
this section is the canonical in-repo summary.)

- **R-1. Phase 8 deletion would break migration replay.**
  `migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py:315`
  calls `amortization_engine.generate_schedule` to derive the
  from-origination balance after replaying confirmed payments. The
  architectural plan did not mention this; deleting
  `generate_schedule` without addressing it would fail every
  fresh-database rebuild from migrations. Commit 8 inlines the
  small replay loop into the migration before Commit 9 deletes
  the engine functions (D-E). The migration only needs "confirmed
  payments reduce balance" math -- no `extra_monthly`, no rate
  re-amortization, no projected entries -- so the inline is short
  and self-contained.
- **R-2. The dashboard's engine touch count is FOUR, not two.**
  `app/routes/loan.py:488-749` has: the resolver call (via
  `_load_loan_context` at line 504), `planned_schedule` at line
  533, `original_schedule` at line 589, and `floor_schedule` at
  line 619. The architectural plan said "two combinations
  disappear." Commit 5 explicitly enumerates all four and lists
  every consumer of `planned_schedule`: the amortization tab
  (`amortization_schedule`), the payment breakdown
  (`_compute_payment_breakdown` at line 574), the schedule totals
  (`_compute_schedule_totals` at line 697), the recurrence
  end_date update (`_update_transfer_end_date` at line 660), and
  the `summary` construction (line 557).
- **R-3. The `calculate_payoff_by_date` "latent bug" rationale in
  the architectural plan is overstated.** The route at
  `loan.py:1337` already passes `state.current_balance` as the
  starting principal, so the "computes against original-principal
  contractual schedule" framing is wrong. The actual latent issue
  is that projected payments from a recurring transfer template
  do not factor into "required extra" -- a separate behavior
  change (D-F). Commit 7's rationale is corrected; behavior is
  preserved.
- **R-4. Existing tests do NOT exercise the bug.**
  `TestPaymentAwareSchedule` at
  `tests/test_services/test_amortization_engine.py:811` sets
  `ORIGINATION = date(2026, 1, 1)` and places its first confirmed
  payment on `date(2026, 2, 15)` -- the same month as schedule row
  0. There is no temporal gap between origination and confirmed
  history, so "extra applied to ghost historical months" never
  fires. Every new unit and composer test in this plan
  deliberately includes a multi-month gap (the architectural
  plan's regression test uses origination 2024-01-01 + confirmed
  payments Jan-Apr 2026); Commit 3's tests pin this property.
- **R-5. The architectural plan's `is_confirmed` framing is too
  broad.** The engine's schedule loop has no `is_confirmed` branch
  -- the gate is `month_key in amount_by_month` only
  (`amortization_engine.py:535`). The resolver already filters
  payments to confirmed-only at `loan_resolver.py:565` before they
  reach the engine. The composer's `monthly_override` replaces
  that filtering convention with an explicit forward-only
  parameter; Commit 3's docstring and Commit 6's resolver
  migration both reflect this scoping.

Also note (R-6, documentation only): the F-022 invariant test at
`tests/test_integration/test_loan_unified_figures.py:394` compares
two quantities computed without `payments=`, so it cannot
distinguish the buggy and fixed implementations of the engine. It
will still pass after the migration (by construction), but it is
not a regression lock for THIS bug. Commit 3's TestComputePayoffScenarios
provides that lock.

---

## 4. Concept -> single-source-of-truth map

Every multi-path concept collapses onto one producer. This table is
the contract the commits implement.

| Concept | Canonical producer after this plan | Locked expectation | Commits |
|---|---|---|---|
| Replay of confirmed history up to `as_of` | `amortization_engine.replay_confirmed_history` | History has no `extra_monthly` parameter; cannot be what-if'ed | 1 |
| Forward projection from a known starting state | `amortization_engine.project_forward` | `monthly_override` for planned/projected payments; `extra_monthly` applied only where no override exists | 2 |
| Payoff Calculator scenarios (Original / Committed / Accelerated) | `loan_resolver.compute_payoff_scenarios` | One return value drives chart and summary; cannot diverge | 3 |
| Payoff Calculator route HTTP shape | `app/routes/loan.py::payoff_calculate` -> composer | Three direct engine calls + `calculate_summary` collapse to one composer call | 4 |
| Dashboard loan chart (Original / Committed / Floor) | composer | Floor = "Committed with the projected portion of `payments` filtered out"; original and committed both via composer | 5 |
| `LoanState.schedule` | resolver internals on new primitives | `history_rows + committed_forward` from a single composer call | 6 |
| `calculate_payoff_by_date` binary search | `project_forward` from `state.current_balance` | One forward primitive; same external behavior (no override yet -- D-F) | 7 |
| Refinance "current" baseline + refi projection | resolver state + one `project_forward` | Single primitive for the refi schedule from a known starting balance | 7 |
| Migration backfill replay | inlined replay loop in `d3d25212504b` | Migration self-contained; no live dependency on deleted engine functions | 8 |
| Engine deletion | `generate_schedule` and `calculate_summary` removed | No production caller; tests rewritten on new primitives | 9 |

---

## 5. Optional enhancements (listed; not in default commit set)

- **OPT-1. Phase 7 also fixes the "projected payments ignored in
  required-extra" latent issue.** Add `monthly_override=projected_by_month`
  to the Phase 7 binary search inside `calculate_payoff_by_date`.
  Behavior change for any user with a recurring template paying
  over contractual: the "required extra" number drops. Listed only;
  D-F defers it.
- **OPT-2. Add a stale-anchor warning on `PayoffScenarios`.**
  If the latest `LoanAnchorEvent` is older than N months, expose a
  `stale_anchor: bool` field on `PayoffScenarios` so the route can
  surface a UI badge. Mirrors OPT-6 from the financial-calculation
  remediation plan. Listed only.
- **OPT-3. Delete `floor_schedule` from the dashboard entirely.**
  Floor is a "what if I cancel all extras today" series that the
  user has not explicitly requested as a distinct slice; the
  Committed series under the projected-payments-only composer call
  already conveys this. Listed for discussion; not promoted.
- **OPT-4. Rename `LoanState.schedule` to make its "committed-no-extra
  forward" semantics explicit.** Pure rename; high blast radius
  across every consumer; listed only.

---

## 6. Codebase inventory (files this plan touches)

Re-grep each path at edit time; line numbers drift.

### Modified services

- `app/services/amortization_engine.py` -- add `replay_confirmed_history`
  in Commit 1, add `project_forward` in Commit 2, delete
  `generate_schedule` and `calculate_summary` in Commit 9.
- `app/services/loan_resolver.py` -- add `compute_payoff_scenarios` +
  `PayoffScenarios` dataclass in Commit 3; switch `resolve_loan`
  internals in Commit 6.

### Modified routes

- `app/routes/loan.py` -- `payoff_calculate` (Commit 4),
  `dashboard` (Commit 5), `calculate_payoff_by_date` call site
  (Commit 7), `refinance_calculate` (Commit 7).

### Modified migration

- `migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py`
  -- inline the small replay loop in Commit 8 so the engine
  deletion in Commit 9 does not break replay.

### Modified templates (only if context-key names change)

- `app/templates/loan/_payoff_results.html` -- adjust if a chart
  context key is renamed (Commit 4). Current keys
  (`chart_original`, `chart_committed`, `chart_accelerated`,
  `payoff_summary`, `committed_months_saved`,
  `committed_interest_saved`) are preserved if possible.
- `app/templates/loan/dashboard.html` -- same caveat (Commit 5).
  Current keys (`chart_original`, `chart_committed`,
  `chart_floor`) are preserved if possible.

### New / modified tests

- `tests/test_services/test_amortization_engine.py` -- add
  `TestReplayConfirmedHistory` (Commit 1), `TestProjectForward`
  (Commit 2), delete `TestPaymentAwareSchedule` and other
  `generate_schedule` / `calculate_summary`-specific classes in
  Commit 9 (after the primitives have equivalent coverage).
- `tests/test_services/test_loan_resolver.py` -- add
  `TestComputePayoffScenarios` (Commit 3); update resolver-internals
  tests in Commit 6.
- `tests/test_routes/test_loan.py` -- add payoff-route chart tests
  (Commit 4); update dashboard-route tests (Commit 5).
- `tests/test_integration/test_loan_unified_figures.py` -- no
  modifications expected; F-022 invariant passes by construction.

---

## 7. Commit dependency analysis

```text
Foundations (new primitives; no callers)
  1 replay_confirmed_history ────────────┐
  2 project_forward ──────────────┐      │
                                  │      │
Composer (uses 1 + 2)             │      │
  3 compute_payoff_scenarios ─────┴──────┘

Migration to composer (user-visible fix lands at 4)
  4 payoff_calculate -> composer (depends on 3)
  5 dashboard chart paths -> composer (depends on 3)
  6 resolve_loan internals -> primitives (depends on 1, 2; resolver chokepoint)
  7 calculate_payoff_by_date + refinance -> primitives (depends on 1, 2)

Cleanup (prereqs + deletion)
  8 inline replay in migration d3d25212504b (depends on 1; prereq for 9)
  9 delete generate_schedule + calculate_summary (depends on 4, 5, 6, 7, 8)

Gate
 10 full gate + verification appendix
```

Ordering rationale:

- 1, 2, 3 are pure additions; the suite stays green by construction.
- 4 is the user-visible fix (the Payoff Calculator regression). It
  lands as early as possible after the primitives are available.
- 5 migrates the dashboard chart for architectural consistency
  (no user-visible bug today; the chart paths do not exhibit the
  fictitious-past bug because none of them passes `extra_monthly`,
  but their composition through the route is the same anti-pattern).
- 6 collapses the resolver's internal `generate_schedule` call.
  Once `LoanState.schedule` is sourced from the new primitives,
  every downstream consumer (debt strategy, savings dashboard,
  year-end summary) is automatically on the new primitives via
  the resolver chokepoint with no changes to those consumers.
- 7 migrates the two remaining route-level callers
  (`calculate_payoff_by_date` and `refinance_calculate`).
- 8 inlines the migration backfill's small replay loop. Must
  precede 9.
- 9 deletes `generate_schedule` and `calculate_summary`, and the
  old `TestPaymentAwareSchedule` test class. Acceptance: every
  test passes on the new primitives.
- 10 is the final acceptance gate.

Every commit leaves the suite green. Commits 1-3 add no callers
and do not change existing behavior; Commits 4-7 change behavior
only along the bug-fix axis (Commit 4) or are architectural
consistency (5-7); Commit 8 is migration-only; Commit 9 is
deletion of unreferenced code.

---

## 8. Commit checklist

| # | Commit message | Summary |
|---|---|---|
| 1 | `feat(amortization): add replay_confirmed_history primitive` | New deterministic-past primitive in `amortization_engine.py`; no callers; `TestReplayConfirmedHistory` |
| 2 | `feat(amortization): add project_forward primitive` | New parameterized-future primitive in `amortization_engine.py`; no callers; `TestProjectForward` |
| 3 | `feat(loan): scenario composer in loan_resolver` | `compute_payoff_scenarios` + `PayoffScenarios` dataclass; `TestComputePayoffScenarios` including the originally-reported-bug regression test |
| 4 | `fix(loan): payoff calculator routes through scenario composer` | `payoff_calculate` extra-payment mode: three direct engine calls + `calculate_summary` collapse to one composer call; payoff route chart tests; user-visible bug fixed |
| 5 | `refactor(loan): dashboard chart paths via scenario composer` | Original / Committed / Floor + `planned_schedule` all derive from the composer; dashboard tests assert byte-identical chart values where behavior is unchanged |
| 6 | `refactor(loan): resolve_loan internals on new primitives` | `LoanState.schedule = history_rows + committed_forward`; resolver chokepoint; downstream consumers unchanged by construction |
| 7 | `refactor(loan): calculate_payoff_by_date and refinance on new primitives` | Both route-level binary-search / projection calls reframed in terms of `project_forward`; behavior preserved (D-F defers projected-payments fix) |
| 8 | `refactor(migrations): inline replay loop in d3d25212504b backfill` | Migration becomes self-contained against the engine deletion in Commit 9 |
| 9 | `refactor(amortization): remove generate_schedule and calculate_summary` | Functions deleted; `TestPaymentAwareSchedule` and other engine-specific test classes deleted; equivalent coverage already on the primitives + composer |
| 10 | `chore(release): full gate + verification appendix` | Final suite + pylint + appendix; symptom walkthrough; no code change |

---

## 9. Commits (detailed)

Each commit follows the house format: A message, B problem, C
files, D implementation, E tests, F manual verification, G
downstream, H rollback. Test IDs are `C<commit>-<n>`. "Re-pinned
tests" follows the Section 1 rule 3 exception only; for this plan
the answer is "none" on every commit except Commit 4 (where a
single existing route test changes its assertion target -- see
Commit 4 detail). Work-summary labels (A-M) per
`docs/audits/financial_calculations/remediation_follow_up_common.md`
are produced at the end of each session.

### Commit 1 -- replay_confirmed_history primitive

**A. Commit message** `feat(amortization): add replay_confirmed_history primitive`

**B. Problem statement** The architectural defect documented in
`docs/plans/2026-05-21-amortization-engine-split-replay-projection.md`
is that `generate_schedule` fuses replay-of-history with
projection-of-future in one loop. The first half of the fix is to
introduce a primitive that does ONLY replay, with no
`extra_monthly` parameter, so history cannot be "what-if'ed."

## C. Files modified

- `app/services/amortization_engine.py`: add `ReplayResult` frozen
  dataclass and `replay_confirmed_history(...)`. No existing
  callers are touched. The function reuses `_advance_month`,
  `_build_payment_lookups`, `_build_rate_change_list`,
  `_find_applicable_rate`, `calculate_monthly_payment`, and the
  existing per-month interest/principal/anchor-snap logic from
  `generate_schedule` -- extracted into the new function intact
  (no rewrite from scratch; CLAUDE.md rule 10).
- `tests/test_services/test_amortization_engine.py`: add
  `class TestReplayConfirmedHistory` with the test inventory in
  section E.

**D. Implementation approach** Re-grep
`amortization_engine.py:326-633` for the current
`generate_schedule` structure; copy the per-month payment-record
branch (lines 537-574 today) into the new function and STRIP every
reference to `extra_monthly` and the contractual fallback branch
(no record path is replay's responsibility -- replay stops at the
last confirmed payment_date <= as_of and does not fabricate
contractual rows for missed months). Honor anchor (ARM) snap at
`anchor_date` and rate changes during the replayed window. Stop at
the last payment with `payment_date <= as_of`. All rows have
`is_confirmed=True` (replay only consumes confirmed inputs at this
phase; the caller filters before calling). Use
`app.utils.money.round_money` as the rounding boundary (already
established by the financial-calculation remediation Commit 1).

```python
@dataclass(frozen=True)
class ReplayResult:
    """Result of replaying confirmed history up to as_of.

    Attributes:
        rows: AmortizationRow per replayed month; is_confirmed=True
            throughout.  Empty list when no confirmed payments
            exist in [origination_date, as_of].
        balance_as_of: Outstanding balance at the close of the
            last replayed month (or the anchor balance / original
            principal when rows is empty).  Full precision; the
            caller rounds at the persistence boundary.
        next_pay_date: First payment_date a forward projection
            should use.  When rows is empty, this is
            origination_date + 1 month.  When rows is non-empty,
            this is the month after rows[-1].payment_date.
        remaining_months_as_of: term_months minus the count of
            replayed months.  Floors at 0.
        applicable_rate_as_of: The annual rate in effect at
            next_pay_date (the last applied rate change, or the
            base rate when no rate changes have effective_date
            <= as_of).
    """

    rows: list[AmortizationRow]
    balance_as_of: Decimal
    next_pay_date: date
    remaining_months_as_of: int
    applicable_rate_as_of: Decimal

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

    History has no extra_monthly parameter -- it cannot be
    what-if'ed.  Pre-origination payments are filtered (existing
    engine behavior).  An ARM anchor snaps the running balance at
    anchor_date.  Rows stop at the last payment with
    payment_date <= as_of; gaps in confirmed payments are NOT
    filled with contractual rows (that is the caller's
    responsibility to reason about).
    """
```

`pylint app/ --fail-on=E,F` clean. No imports of Flask.

**E. Test cases** (every monetary expectation carries the
arithmetic in a comment)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C1-1 | test_empty_confirmed_payments | `confirmed_payments=[]` | `replay_confirmed_history` | `rows == []`, `balance_as_of == original_principal`, `next_pay_date == origination + 1 month`, `remaining_months_as_of == term_months` | New |
| C1-2 | test_single_confirmed_payment_month_1 | 30yr/$300k/6%, one confirmed contractual payment in month 1 | replay | one row, `is_confirmed=True`, balance reduced by principal portion (hand-computed: 1798.65 - 1500.00 = 298.65 principal -> 300000.00 - 298.65 = 299701.35) | New |
| C1-3 | test_multiple_confirmed_payments_span_months_1_to_3 | three contractual payments months 1-3 | replay | three rows, balance monotonically decreasing, all rows `is_confirmed=True`, `remaining_months_as_of == term_months - 3` | New |
| C1-4 | test_gap_in_payments_months_1_2_4 | confirmed months 1, 2, 4; month 3 missing | replay | three rows (for months 1, 2, 4); replay does NOT fabricate a row for month 3 | New |
| C1-5 | test_payments_past_as_of_filtered | confirmed payments months 1-6, `as_of` = end of month 3 | replay | three rows (months 1-3) | New |
| C1-6 | test_pre_origination_payments_filtered | payments dated before origination_date | replay | filtered (existing engine behavior preserved) | New |
| C1-7 | test_arm_anchor_snaps_balance | ARM with `anchor_balance=$250000.00`, `anchor_date=2025-12-15`, confirmed payments straddling | replay | balance snaps to 250000.00 at the first payment after anchor_date; pre-anchor rows have approximate P&I split; post-anchor rows are exact | New |
| C1-8 | test_rate_change_during_replay | rate change at month 13 during a 24-confirmed-payment window | replay | applicable_rate_as_of returns the post-change rate; interest recomputed at new rate | New |
| C1-9 | test_balance_as_of_matches_generate_schedule_replay | 30yr/$300k/6%, three contractual payments, as_of = end of month 3 | replay | `balance_as_of` equals the value `generate_schedule` produces for row 2's `remaining_balance` (cross-check during migration; deleted in Commit 9) | New |
| C1-10 | test_replay_rows_all_confirmed | mixed-confirmation `confirmed_payments` input | replay | every output row has `is_confirmed=True` | New |
| C1-11 | test_next_pay_date_correct | last replayed payment month 3 | replay | `next_pay_date.month == month after row 3.payment_date.month` | New |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_services/test_amortization_engine.py::TestReplayConfirmedHistory -v` all pass.
2. `pylint app/ --fail-on=E,F` clean.
3. `grep -nE '^(from|import)\s+flask\b' app/services/amortization_engine.py` empty (engine remains pure).
4. Spot-check: `python -c "from app.services.amortization_engine import replay_confirmed_history; ..."` produces the C1-2 balance.

**G. Downstream effects** Pure addition; no callers. Commit 3
will be the first consumer (via the composer); Commit 6 will be
the second (via the resolver). `generate_schedule` /
`calculate_summary` still authoritative until Commit 9.

**H. Rollback notes** Delete the new function + dataclass + test
class. No migration, no data, no behavior change to revert.

---

### Commit 2 -- project_forward primitive

**A. Commit message** `feat(amortization): add project_forward primitive`

**B. Problem statement** The second half of the architectural fix
is a primitive that does ONLY forward projection from a known
starting state. `extra_monthly` lives here and only here; an
explicit `monthly_override` parameter routes the user's planned
payments (from recurring transfer templates) through a
forward-only channel. Projection has no concept of history and
cannot rewrite the past.

## C. Files modified

- `app/services/amortization_engine.py`: add `project_forward(...)`.
  Reuses `calculate_monthly_payment`, `_advance_month`,
  `_find_applicable_rate`, and the per-month interest /
  principal-split / overpayment-cap / extra-apply / negative-am
  logic from `generate_schedule` (extracted intact; no rewrite).
- `tests/test_services/test_amortization_engine.py`: add
  `class TestProjectForward`.

**D. Implementation approach** Re-read the current
`generate_schedule` lines 575-605 (contractual / extra branch) and
537-574 (payment-record branch). The new function flips the
control flow: it accepts an explicit `monthly_override` dict and
applies `extra_monthly` only when no override exists for the
month. Confirmed payments do not exist here -- this function has
no concept of confirmed vs. projected; everything is a projection
parameterized by overrides + extra.

```python
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
    payment for that month (e.g., from projected transfer
    templates).  When an override exists for a month, it is used
    as the total payment and extra_monthly is NOT added.  When no
    override exists, the row uses contractual_payment +
    extra_monthly.

    Cannot rewrite history -- has no concept of history.  All
    rows have is_confirmed=False (projection only).
    """
```

Contractual payment is FROZEN at projection start (the caller
passes it in -- typically the engine's contractual amount derived
from `(original_principal, annual_rate, term_months)` for
fixed-rate, or the re-amortized amount for ARM outside the
fixed-rate window). The function does not recompute the
contractual payment per row except at rate-change boundaries when
`rate_changes_remaining` is non-empty -- there it re-amortizes
the remaining balance over the remaining months at the new rate,
preserving existing ARM engine behavior.

Negative amortization is preserved: when `monthly_override[m]` is
below the period's interest, `principal_portion` is negative and
the balance grows (existing behavior at engine line 547-549).

Overpayment cap is preserved: when `principal_portion >= balance`,
the row absorbs the remaining balance and `extra_payment` is
capped accordingly (existing behavior at engine line 555-561 and
585-591).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C2-1 | test_no_override_no_extra_is_contractual | $300k start, 6%, 360 months, contractual=$1798.65, no override, no extra | `project_forward` | 360 rows, each row.payment == contractual_payment, each row.extra_payment == 0, final row absorbs the residual balance | New |
| C2-2 | test_monthly_override_only | one override `(2026, 6) -> $2000.00`, no extra | project | row for June 2026 has `payment == 2000.00`, `extra_payment == 0`; other rows use contractual | New |
| C2-3 | test_extra_monthly_only_no_override | no override, extra=$500 | project | every row has `extra_payment == 500.00` (or capped at remaining balance for the final row); schedule shortens | New |
| C2-4 | test_override_plus_extra_extra_not_added_to_override_months | override at (2026,6) -> $2000.00, extra=$500 | project | June 2026 row: `payment == 2000.00`, `extra_payment == 0` (CRITICAL: extra NOT added to override months); other rows: contractual + 500 | New |
| C2-5 | test_override_below_interest_negative_amortization | override $50 against $1500 monthly interest | project | `principal_portion` negative, balance grows; existing engine behavior preserved | New |
| C2-6 | test_arm_rate_change_during_projection | rate change at month 13 | project | payment re-amortizes from the new rate at month 13 over the remaining months; existing engine ARM behavior preserved | New |
| C2-7 | test_overpayment_cap_final_row | override or extra would drive balance below zero | project | final row absorbs remaining balance exactly; `extra_payment` capped; balance == 0.00 | New |
| C2-8 | test_zero_starting_balance_returns_empty | `starting_balance=0` | project | empty list | New |
| C2-9 | test_zero_remaining_months_returns_empty | `remaining_months=0` | project | empty list | New |
| C2-10 | test_hand_computed_payoff_with_extra | $279,985 starting balance, 6%, 336 remaining months, $200 extra | project | hand-computed `len(rows)` and final `payment_date` match independently-computed value (architectural plan's hand-computed expectation, line 328-330) | New |
| C2-11 | test_round_money_is_only_rounding_boundary | -- | grep within `project_forward` body | no bare `.quantize(Decimal("0.01"))` without `rounding=`; rounding goes through `round_money` or explicit `ROUND_HALF_UP` | New |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_services/test_amortization_engine.py::TestProjectForward -v` all pass.
2. C2-4 must pass: this is the regression-prevention assertion
   that the new API makes the bug structurally impossible
   (override months never receive extra).
3. `pylint` clean.
4. `grep -n "is_confirmed=True" app/services/amortization_engine.py | grep project_forward` empty (projection rows never claim confirmation).

**G. Downstream effects** Pure addition; no callers. Commit 3
consumes both primitives via the composer.

**H. Rollback notes** Delete the new function and test class. No
data, no migration, no behavior change.

---

### Commit 3 -- scenario composer in loan_resolver

**A. Commit message** `feat(loan): scenario composer in loan_resolver`

**B. Problem statement** The route-layer scenario composition in
`payoff_calculate` (three direct `generate_schedule` calls +
`calculate_summary`, each with hand-chosen parameters) is the
mechanism by which chart and summary can disagree. The fix is to
own scenario composition in `loan_resolver` so chart and summary
derive from one return value. After this commit no caller is
rewired (Commits 4-7 do that); the composer simply exists and is
tested.

## C. Files modified

- `app/services/loan_resolver.py`: add `PayoffScenarios` frozen
  dataclass and `compute_payoff_scenarios(...)`.
- `tests/test_services/test_loan_resolver.py`: add
  `class TestComputePayoffScenarios`.

**D. Implementation approach** The composer calls
`replay_confirmed_history` ONCE and `project_forward` THREE times
from the same `(starting_balance, starting_date, remaining_months,
applicable_rate)` tuple. Inputs:

- `loan_params`: provides origination_date, original_principal,
  annual_rate (base), term_months, payment_day, ARM flags.
- `anchor_events`: passed to a private helper that mirrors
  `loan_resolver._select_latest_anchor` (already exists at
  `loan_resolver.py:128-161`). For ARM, the anchor seeds
  replay's `anchor_balance` / `anchor_date`.
- `payments`: confirmed + projected, as produced by
  `loan_payment_service.prepare_payments_for_engine` (already
  exists). The composer:
  - filters confirmed to `<= as_of` and passes to replay
  - groups projected past `as_of` by `(year, month)` and
    constructs the `monthly_override` dict; confirmed payments
    PAST `as_of` (rare; data hygiene) are also routed into
    `monthly_override`.
- `rate_changes`: replay sees the entire list (its
  `_build_rate_change_list` filters pre-origination internally);
  projection sees only entries with
  `effective_date > replay_result.next_pay_date - 1 day` (the
  "remaining" rate changes).
- `extra_monthly`: applied to the Accelerated projection only.
- `as_of`: the evaluation date (caller passes `date.today()` in
  the route).

```python
@dataclass(frozen=True)
class PayoffScenarios:
    history_rows: list[AmortizationRow]
    original_forward: list[AmortizationRow]
    committed_forward: list[AmortizationRow]
    accelerated_forward: list[AmortizationRow]
    months_saved: int
    interest_saved: Decimal
    payoff_date_committed: date
    payoff_date_accelerated: date
    total_interest_committed: Decimal
    total_interest_accelerated: Decimal

def compute_payoff_scenarios(
    *,
    loan_params,
    anchor_events: list,
    payments: list[PaymentRecord],
    rate_changes: list[RateChangeRecord] | None,
    extra_monthly: Decimal,
    as_of: date,
) -> PayoffScenarios:
    """Single source of truth for the payoff calculator.

    Calls replay_confirmed_history ONCE, then project_forward
    THREE times from the same starting state.  Chart and summary
    cannot diverge because they derive from the same return
    value.
    """
```

The three forward projections share starting state; the only
differences are `monthly_override` and `extra_monthly`:

- Original: `monthly_override=None`, `extra_monthly=0`
- Committed: `monthly_override=<projected_by_month>`, `extra_monthly=0`
- Accelerated: `monthly_override=<projected_by_month>`, `extra_monthly=<X>`

Summary metrics derive from the same forward slices:

- `months_saved = len(committed_forward) - len(accelerated_forward)`
- `interest_saved = sum(committed_forward.interest) - sum(accelerated_forward.interest)`
- `payoff_date_committed = committed_forward[-1].payment_date` (or `as_of` for empty)
- `payoff_date_accelerated = accelerated_forward[-1].payment_date`
- `total_interest_committed = sum(committed_forward.interest)`
- `total_interest_accelerated = sum(accelerated_forward.interest)`

`round_money` at the summary aggregation boundary; the forward
slices keep `AmortizationRow`'s existing quantization (already
ROUND_HALF_UP in the engine).

Composer is pure: no Flask, no `db.session`, takes plain data,
returns plain data (matches the resolver's services-boundary
contract).

## E. Test cases (Decimal expectations show arithmetic)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C3-1 | test_history_shared | 30yr/$300k/6%, four confirmed Jan-Apr 2026, `as_of=2026-05-21` | `compute_payoff_scenarios` | `len(history_rows) == 4`; every row `payment_date <= as_of` | New |
| C3-2 | test_forward_same_starting_balance | same setup, extra=$500 | composer | first row of each of original/committed/accelerated starts from the same balance (`replay.balance_as_of` minus that row's principal) | New |
| C3-3 | test_forward_first_row_date_matches_next_pay_date | same | composer | `original_forward[0].payment_date == replay.next_pay_date` and same for committed/accelerated | New |
| C3-4 | test_history_byte_identical_across_scenarios | same | composer | `history_rows` prefix is the SAME object (identity assertion or byte-equal sequence) | New |
| C3-5 | test_original_ignores_projections_and_extra | one projected $2000 payment in June 2026, extra=$500 | composer | every row of `original_forward` uses contractual payment; no override, no extra applied | New |
| C3-6 | test_committed_honors_projections | one projected $2000 payment in June 2026 | composer | June 2026 row in `committed_forward` has `payment == 2000.00`, `extra_payment == 0` | New |
| C3-7 | test_accelerated_honors_projections_and_extra | projection at June 2026 == $2000, extra=$500 | composer | June 2026 in `accelerated_forward` has `payment == 2000.00`, `extra_payment == 0`; July 2026 (no override) has `payment == contractual`, `extra_payment == 500.00` | New |
| C3-8 | test_months_saved_metric | committed payoff 25 yr later, accelerated 22 yr later | composer | `months_saved == 36` (hand-computed) | New |
| C3-9 | test_interest_saved_metric | same | composer | `interest_saved == sum(committed.interest) - sum(accelerated.interest)` (hand-computed example value) | New |
| C3-10 | test_originally_reported_bug_regression | 30yr/$300k/6%, originated 2024-01-01, four confirmed contractual payments Jan-Apr 2026, no projected transfers, extra=$500, `as_of=2026-05-21` | composer | `len(history_rows) == 4` (nothing fabricated for 2024-2025); every row of `accelerated_forward` past `as_of` has `extra_payment == 500.00`; `accelerated_forward[0].payment_date == 2026-05-01` (first post-`as_of` month); `months_saved` matches the hand-computed $279,985-ish starting balance + $500 extra value (NOT the inflated buggy value) | New |
| C3-11 | test_temporal_gap_property | parametrize: origination dates spanning 12, 24, 36 months before first confirmed payment | composer | `len(history_rows)` equals the count of confirmed payments (not the count of months from origination); no fabricated rows | New |
| C3-12 | test_composer_is_pure | -- | grep `app/services/loan_resolver.py` for `from flask` | empty (composer is pure; loan_resolver was already verified pure -- see resolver docstring lines 14-15) | New |
| C3-13 | test_summary_metrics_match_chart | same as C3-7 | composer | `months_saved == len(committed_forward) - len(accelerated_forward)`; `interest_saved` reconciles bit-for-bit to `sum(committed.interest) - sum(accelerated.interest)`; no second computation path | New |
| C3-14 | test_arm_anchor_preserved | ARM with `anchor_balance=$250000`, `anchor_date=2025-12-15`, confirmed payments after | composer | history rows reflect the anchor snap; forward starts from the resulting balance | New |
| C3-15 | test_confirmed_past_as_of_routed_to_override | one confirmed payment dated 2026-08-01 with `as_of=2026-05-21` | composer | that payment appears in `monthly_override` for the three forward projections, NOT in history rows | New |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_services/test_loan_resolver.py::TestComputePayoffScenarios -v` all pass.
2. C3-10 (the originally-reported-bug regression) must pass.
3. `grep -nE '^(from|import)\s+flask\b' app/services/loan_resolver.py` empty.
4. `grep -n '\.quantize(Decimal("0.01"))' app/services/loan_resolver.py` empty (use `round_money`).

**G. Downstream effects** No caller is rewired yet. The composer
exists and is tested; Commits 4-7 migrate callers.

**H. Rollback notes** Delete `PayoffScenarios` + composer
function + test class. No data, no migration.

---

### Commit 4 -- payoff calculator routes through scenario composer (USER-VISIBLE FIX)

**A. Commit message** `fix(loan): payoff calculator routes through scenario composer`

**B. Problem statement** The bug reported on `/accounts/3/loan`
(Accelerated diverges from origination, runs parallel through the
confirmed window, resumes after today) is produced by
`payoff_calculate`'s three direct `generate_schedule` calls plus
its `calculate_summary` call. The accelerated call passes both
`payments=payments` and `extra_monthly=extra`, and the engine's
"apply extra when no PaymentRecord exists" semantics treat every
pre-confirmed-history month as a no-record month, applying extra
fictitiously. Routing through the composer (Commit 3) collapses
the four engine touches to one composer call where extra cannot
be applied to history by construction.

## C. Files modified

- `app/routes/loan.py`: `payoff_calculate` `mode == "extra_payment"`
  branch (re-grep `:1184-1364`; the bug-bearing branch is lines
  1224-1312). Replace:
  - line 1226 `calculate_summary(...)` -- DELETE
  - line 1243 `generate_schedule(...)` (original) -- DELETE
  - line 1251 `generate_schedule(...)` (committed) -- DELETE
  - line 1263 `generate_schedule(...)` (accelerated) -- DELETE
  - with one `compute_payoff_scenarios(...)` call.
  Chart data builds from `scenarios.history_rows +
  scenarios.<slice>_forward` via `_balances` (a small helper
  introduced here that uses `[float(row.remaining_balance) for row
  in seq]`; the existing `_build_chart_data` returns labels +
  balances together so the labels portion is shared from
  `history + original_forward`).
  The `mode == "target_date"` branch (lines 1314-1359) is NOT
  touched here -- it migrates in Commit 7.
- `app/templates/loan/_payoff_results.html`: NO changes if context
  keys are preserved (see D). Re-grep current context keys.
- `tests/test_routes/test_loan.py`: add `class TestPayoffChartShape`
  with the route HTTP integration tests below. The existing
  `TestPayoffCalculator::test_payoff_extra_payment` test currently
  asserts only that "Months Saved" appears in the response; it
  stays green by construction (the new code path still renders
  "Months Saved").

**D. Implementation approach** Preserve every existing context key
the template reads:

- `payoff_summary` -- the template reads
  `.payoff_date_with_extra`, `.months_saved`, `.interest_saved`.
  Construct an `AmortizationSummary` (or equivalent) from
  `scenarios.payoff_date_accelerated`, `scenarios.months_saved`,
  `scenarios.interest_saved`, plus `monthly_payment`,
  `total_interest`, `total_interest_with_extra`, `payoff_date`
  derived from the composer's metrics.
- `chart_labels`, `chart_original`, `chart_committed`,
  `chart_accelerated`, `has_payments`, `committed_months_saved`,
  `committed_interest_saved` -- all preserved as-is.

`committed_months_saved` and `committed_interest_saved` (the
"Current Plan vs. Original" labels at template lines 24-42) are
defined relative to `original_forward` vs `committed_forward`:
- `committed_months_saved = len(scenarios.original_forward) -
  len(scenarios.committed_forward)`
- `committed_interest_saved = round_money(sum(original_forward.interest)
  - sum(committed_forward.interest))`

These match the existing route logic (lines 1283-1299) which
subtracts committed from original schedules.

Pseudocode:

```python
ctx = _load_loan_context(account, params)
extra = Decimal(str(data.get("extra_monthly", "0")))
scenarios = compute_payoff_scenarios(
    loan_params=params,
    anchor_events=_load_anchor_events(account.id),
    payments=ctx["payments"],
    rate_changes=ctx["rate_changes"],
    extra_monthly=extra,
    as_of=date.today(),
)
def _balances(seq):
    return [float(row.remaining_balance) for row in seq]
def _labels(seq):
    return [row.payment_date.strftime("%b %Y") for row in seq]
chart_labels      = _labels(scenarios.history_rows + scenarios.original_forward)
chart_original    = _balances(scenarios.history_rows + scenarios.original_forward)
chart_committed   = _balances(scenarios.history_rows + scenarios.committed_forward)
chart_accelerated = _balances(scenarios.history_rows + scenarios.accelerated_forward)

payoff_summary = AmortizationSummary(
    monthly_payment=ctx["state"].monthly_payment,
    total_interest=scenarios.total_interest_committed,
    payoff_date=scenarios.payoff_date_committed,
    total_interest_with_extra=scenarios.total_interest_accelerated,
    payoff_date_with_extra=scenarios.payoff_date_accelerated,
    months_saved=scenarios.months_saved,
    interest_saved=scenarios.interest_saved,
)
committed_months_saved = (
    len(scenarios.original_forward) - len(scenarios.committed_forward)
)
committed_interest_saved = round_money(
    sum(r.interest for r in scenarios.original_forward)
    - sum(r.interest for r in scenarios.committed_forward)
)
```

Re-grep `app/templates/loan/_payoff_results.html` for every Jinja
variable read; if any context key the template requires is not
produced by the new code, fix the gap rather than restoring the
old call. After this commit no production caller of
`calculate_summary` remains (the only one was here, verified by
the exploration in Section 3); `calculate_summary` itself stays
in `amortization_engine.py` until Commit 9 to keep this commit
atomic.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C4-1 | test_chart_lengths_equal | symptom tuple loan, POST `/accounts/<id>/loan/payoff` `mode=extra_payment` extra=$500 | parse rendered partial for data-original, data-committed, data-accelerated | three arrays of equal length | New |
| C4-2 | test_accelerated_equals_committed_in_historical_region | symptom tuple loan, POST extra payment | parse arrays | for every index `i` whose label is <= today's month, `data_accelerated[i] == data_committed[i]` (the user's reported visual bug; this assertion FAILED on the buggy code) | New |
| C4-3 | test_accelerated_below_committed_post_today | same | parse arrays | at least one index past today's month has `data_accelerated[i] < data_committed[i]` strictly | New |
| C4-4 | test_summary_consistent_with_chart | same | parse displayed Months Saved + Interest Saved labels; compare to chart divergence count and sum | match within rounding tolerance | New |
| C4-5 | test_no_payment_history_chart_starts_at_origination | loan with zero confirmed payments, extra requested | POST extra payment | no history rows; chart starts at origination with three series overlaying correctly | New |
| C4-6 | test_target_date_mode_unchanged | same loan, POST `mode=target_date` | parse response | byte-identical to pre-commit (Commit 7 migrates this branch) | Mod (assert-unchanged) |
| C4-7 | test_no_direct_calculate_summary_call | grep | `app/routes/loan.py` `payoff_calculate` body | no `amortization_engine.calculate_summary` reference | New |
| C4-8 | test_no_direct_generate_schedule_call | grep | same body, extra_payment branch | no `amortization_engine.generate_schedule` reference | New |

Re-pinned tests: **none** (verified: the only existing payoff
calculator route test
(`TestPayoffCalculator::test_payoff_extra_payment`) asserts a 200
status + "Months Saved" substring presence, both of which the new
code path still satisfies; no existing test pinned the buggy
chart shape). The new tests are net additions.

## F. Manual verification steps

1. Open `/accounts/3/loan`, submit the Payoff Calculator with a
   nonzero extra (e.g. $500). The Accelerated line tracks the
   Committed line through the historical region; it departs from
   Committed only at and after today's month boundary. The
   "Months Saved" / "Interest Saved" labels match the chart's
   visible divergence.
2. `./scripts/test.sh tests/test_routes/test_loan.py -v` green;
   the new `TestPayoffChartShape` tests pass.
3. `pylint app/ --fail-on=E,F` clean.
4. Confirm no template change required:
   `git diff app/templates/loan/_payoff_results.html` empty (or
   includes only intentional changes if context keys had to be
   renamed; this is unlikely if D's preservation strategy held).

**G. Downstream effects** The user-visible bug is fixed. The
dashboard chart (Commit 5) still uses direct engine calls but
does not exhibit the bug because none of its three calls passes
`extra_monthly`; Commit 5 is for consistency, not fix.
`calculate_summary` is dead code in production after this commit
(deleted in Commit 9).

**H. Rollback notes** Revert `payoff_calculate` to the four
direct engine calls; the bug returns immediately. The composer
remains harmless (no caller). Re-pin none -- new tests fail on
the reverted code, which is correct (they were never green on
the old code, so reverting just deletes the new green coverage).

---

### Commit 5 -- dashboard chart paths via scenario composer

**A. Commit message** `refactor(loan): dashboard chart paths via scenario composer`

**B. Problem statement** The dashboard has FOUR engine touches
(see Section 3 R-2) and no current user-visible bug, but its
composition style is the same anti-pattern as Commit 4's
`payoff_calculate`. Migrating it now is architectural
consistency and a prerequisite for the resolver's internal
migration in Commit 6 (because that commit redefines
`LoanState.schedule` to be the committed-no-extra composition,
and the dashboard should be reading that, not generating its own
`planned_schedule`).

## C. Files modified

- `app/routes/loan.py`: `dashboard` (re-grep `:488-749`). Replace
  the four engine touches:
  - line 504 `_load_loan_context(account, params)` -- KEEP (this
    is the resolver call; it stays).
  - line 533 `planned_schedule = generate_schedule(...)` --
    DELETE. The amortization tab, payment breakdown, schedule
    totals, recurrence end_date update, and summary construction
    all switch to `scenarios.history_rows +
    scenarios.committed_forward` (with extra=0, so committed ==
    "planned trajectory") via a SINGLE composer call.
  - line 589 `original_schedule = generate_schedule(...)` --
    DELETE. Chart `original` becomes
    `_balances(scenarios.history_rows + scenarios.original_forward)`.
  - line 619 `floor_schedule = generate_schedule(...)` -- DELETE.
    Floor becomes a SECOND composer call where the projected
    portion of `payments` is filtered out (only confirmed
    payments are passed in, extra=0). The resulting
    `committed_forward` is "what I owe if I cancel all extras
    today" -- the floor's semantic.
  Net result: one composer call for the displayed chart's
  original / committed (and reuse for planned), plus a second
  composer call for floor. Both share the same resolver state
  and base inputs.
- `app/templates/loan/dashboard.html`: NO changes if context
  keys (`chart_labels`, `chart_original`, `chart_committed`,
  `chart_floor`, `amortization_schedule`, `schedule_row_totals`,
  `schedule_row_rates_pct`, `show_rate_column`, `schedule_totals`,
  `payment_breakdown`, `summary`, `current_principal_display`,
  `total_payment`, `has_payments`, etc.) are preserved. Re-grep
  every Jinja variable read.
- `tests/test_routes/test_loan.py`: dashboard tests assert
  byte-identical chart values + summary numbers where behavior is
  unchanged (regression-safety) plus a new test that the chart
  paths no longer call `generate_schedule` directly.

**D. Implementation approach** Construct two `PayoffScenarios`
values:

- `scenarios_main`: full payments list (confirmed + projected),
  `extra_monthly=0`. Drives chart `original` and `committed`,
  the amortization tab data (`history + committed_forward`), the
  payment breakdown, schedule totals, recurrence end_date update,
  and summary.
- `scenarios_floor`: payments filtered to confirmed-only,
  `extra_monthly=0`. Drives chart `floor` = `_balances(history +
  committed_forward)` -- which is "Committed with projections
  cancelled" -- exactly the floor's semantic per the architectural
  plan line 176-178.

Pseudocode (paraphrased):

```python
scenarios_main = compute_payoff_scenarios(
    loan_params=params,
    anchor_events=anchor_events,
    payments=payments,
    rate_changes=rate_changes,
    extra_monthly=Decimal("0.00"),
    as_of=date.today(),
)
confirmed_only = [p for p in payments if p.is_confirmed]
scenarios_floor = compute_payoff_scenarios(
    loan_params=params,
    anchor_events=anchor_events,
    payments=confirmed_only,
    rate_changes=rate_changes,
    extra_monthly=Decimal("0.00"),
    as_of=date.today(),
)

planned_schedule = scenarios_main.history_rows + scenarios_main.committed_forward
chart_labels    = _labels(scenarios_main.history_rows + scenarios_main.original_forward)
chart_original  = _balances(scenarios_main.history_rows + scenarios_main.original_forward)
chart_committed = _balances(scenarios_main.history_rows + scenarios_main.committed_forward) if has_payments else []
chart_floor     = _balances(scenarios_floor.history_rows + scenarios_floor.committed_forward) if has_payments else []
```

Every existing consumer of `planned_schedule` reads its new
incarnation unchanged. `_compute_payment_breakdown`,
`_compute_schedule_totals`, `_update_transfer_end_date`,
`_find_current_period_row` all accept a list of
`AmortizationRow`; they keep their existing signatures.

`summary` is built from `scenarios_main`:

```python
summary = AmortizationSummary(
    monthly_payment=state.monthly_payment,
    total_interest=scenarios_main.total_interest_committed,
    payoff_date=scenarios_main.payoff_date_committed,
    total_interest_with_extra=scenarios_main.total_interest_committed,
    payoff_date_with_extra=scenarios_main.payoff_date_committed,
    months_saved=0,
    interest_saved=Decimal("0.00"),
)
```

(With `extra_monthly=0` the "with_extra" fields equal the
"committed" fields -- matches the existing dashboard summary
construction at lines 557-565.)

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C5-1 | test_dashboard_chart_values_unchanged | fixed loan with confirmed payments + projected transfer template | GET `/accounts/<id>/loan` | `chart_original`, `chart_committed`, `chart_floor` arrays byte-identical to pre-commit values (regression-safety) | Mod (assert-unchanged) |
| C5-2 | test_amortization_tab_rows_unchanged | same | parse rendered amortization table | each row's Payment / Principal / Interest / Extra / Rate matches pre-commit values | Mod (assert-unchanged) |
| C5-3 | test_payment_breakdown_unchanged | same | parse breakdown card | principal / interest / escrow / total / percentages match pre-commit | Mod (assert-unchanged) |
| C5-4 | test_recurrence_end_date_update_idempotent | template's end_date matches new payoff | GET dashboard | end_date unchanged (no spurious write) | Mod (assert-unchanged) |
| C5-5 | test_no_direct_generate_schedule_in_dashboard | grep | `app/routes/loan.py` `dashboard` body | no `amortization_engine.generate_schedule` reference | New |
| C5-6 | test_floor_equals_committed_with_projections_cancelled | loan with both confirmed + projected payments | dashboard | `chart_floor[i] >= chart_committed[i]` for every `i` past today (floor sits above committed because the projected payments stop reducing principal) | New |
| C5-7 | test_floor_equals_committed_when_no_projections | loan with only confirmed payments, no recurring transfer template | dashboard | `chart_floor == chart_committed` (no projections to cancel) | New |
| C5-8 | test_arm_dashboard_chart_unchanged | ARM in fixed-rate window | dashboard | chart values byte-identical to pre-commit (the ARM anchor passes through the composer correctly) | Mod (assert-unchanged) |

Re-pinned tests: **none**. C5-1..C5-4 and C5-8 are
"assert-unchanged" -- they pin the existing dashboard output, NOT
a buggy value the migration is fixing. If any of them surfaces a
true byte-difference, that is an architectural-plan bug; stop and
report (rule 4).

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_loan.py -v` green
   (including the new `TestDashboardChartComposer` block).
2. Visually compare `/accounts/<id>/loan` chart before and after
   -- the three lines should be unchanged.
3. `pylint app/ --fail-on=E,F` clean.

**G. Downstream effects** Dashboard chart paths now route through
the composer. Commit 6's resolver migration becomes safer because
`LoanState.schedule` consumers in the dashboard already match the
new shape.

**H. Rollback notes** Restore the three direct engine calls.
Chart values unchanged regardless (the migration was
behavior-preserving), so revert is safe.

---

### Commit 6 -- resolve_loan internals on new primitives

**A. Commit message** `refactor(loan): resolve_loan internals on new primitives`

**B. Problem statement** `loan_resolver.resolve_loan` calls
`generate_schedule` directly at line 598. Phase 6 of the
architectural plan replaces that call with
`replay_confirmed_history` + `project_forward` so
`LoanState.schedule` becomes `history_rows + forward_rows` from a
"Committed with no extra" composer invocation. This is the
single chokepoint other callers (year-end summary,
savings_dashboard, debt strategy, refinance) already read
through, so they migrate automatically with zero changes on their
side.

## C. Files modified

- `app/services/loan_resolver.py`: `resolve_loan` (re-grep
  `:478-649`). Replace the direct `generate_schedule(...)` call
  at line 598-610 with `compute_payoff_scenarios(...)`
  (extra_monthly=0) and assemble `LoanState.schedule` as
  `scenarios.history_rows + scenarios.committed_forward`. The
  `current_balance` derivation
  (`_replay_balance_from_anchor`) stays unchanged -- it operates
  on the primary data (anchor + confirmed payments) without
  depending on the schedule. The `_compute_monthly_payment` ARM
  branch stays unchanged. `payoff_date` and `total_interest`
  derive from the composer's metrics.
- `tests/test_services/test_loan_resolver.py`: existing tests
  assert `LoanState.schedule` length, balance progression,
  `payoff_date`, `total_interest`. Verify all still pass with
  the new internals (`history_rows + committed_forward` should
  match the previous full schedule by construction for any loan
  with no projected payments past `as_of` matching no override).
  Where projected payments existed, the schedule shape was
  already "history + committed" in semantics, so the values
  match.

**D. Implementation approach** Re-read the entire
`resolve_loan` function before editing. The fixed-rate and ARM
branches both currently call `generate_schedule` with the same
parameters (lines 598-610), differing only in `anchor_balance` /
`anchor_date` (None for fixed, `state.current_balance` /
`date.today()` for ARM -- wait, re-check: actually
`anchor_balance=anchor_balance` and
`anchor_date=anchor_date` from the latest anchor event; the
"current balance for ARM" anchor is only used by the displayed
chart paths in `dashboard` / `payoff_calculate`, NOT by the
resolver).

Re-verify by reading lines 589-610:

```python
if is_arm:
    engine_anchor_balance = anchor_balance
    engine_anchor_date = anchor_date
    engine_original = None
else:
    engine_anchor_balance = None
    engine_anchor_date = None
    engine_original = orig_principal

schedule = generate_schedule(
    current_principal=orig_principal,
    annual_rate=base_rate,
    remaining_months=loan_params.term_months,
    origination_date=loan_params.origination_date,
    payment_day=loan_params.payment_day,
    original_principal=engine_original,
    term_months=loan_params.term_months,
    payments=confirmed_after_anchor,
    rate_changes=rate_changes,
    anchor_balance=engine_anchor_balance,
    anchor_date=engine_anchor_date,
)
```

Replace with a composer call:

```python
scenarios = compute_payoff_scenarios(
    loan_params=loan_params,
    anchor_events=anchor_events,
    payments=confirmed_after_anchor,    # confirmed-only here per resolver semantics
    rate_changes=rate_changes,
    extra_monthly=Decimal("0.00"),
    as_of=as_of,
)
schedule = scenarios.history_rows + scenarios.committed_forward
```

Note the subtle change: the resolver currently passes
`confirmed_after_anchor` as `payments=`; the engine's behavior
is that any month with a confirmed payment record uses the
recorded amount as total payment. After this commit those
confirmed payments are split: replay consumes them (producing
`history_rows`), and projection uses NO `monthly_override`
(because the resolver passes a confirmed-only payments list,
which the composer routes through replay only -- there are no
projected entries past `as_of` to become overrides). The forward
projection therefore uses contractual payments. The resulting
schedule is `history_rows + contractual_forward_from_balance_as_of`,
which is the same shape `generate_schedule` produced before for
the same inputs (confirmed payments replayed + contractual
forward).

`payoff_date` and `total_interest`:

```python
if schedule:
    payoff_date = schedule[-1].payment_date
    total_interest_full = sum(
        (row.interest for row in schedule), ZERO_MONEY,
    )
else:
    payoff_date = loan_params.origination_date
    total_interest_full = ZERO_MONEY
```

stays unchanged (operates on the new schedule).

After this commit `grep -n "generate_schedule" app/services/loan_resolver.py`
shows zero matches; the resolver is fully on the new primitives
via the composer.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C6-1 | test_resolver_schedule_unchanged_no_payments | fresh loan, no payments | `resolve_loan` | schedule byte-identical to pre-commit (pure contractual projection) | Mod (assert-unchanged) |
| C6-2 | test_resolver_schedule_unchanged_with_confirmed | loan with 6 confirmed payments | `resolve_loan` | schedule byte-identical (replay 6 + project remainder) | Mod (assert-unchanged) |
| C6-3 | test_resolver_arm_schedule_unchanged | ARM in fixed-rate window with anchor | `resolve_loan` | schedule byte-identical | Mod (assert-unchanged) |
| C6-4 | test_resolver_current_balance_unchanged | -- | `state.current_balance` | identical to pre-commit | Mod (assert-unchanged) |
| C6-5 | test_resolver_monthly_payment_unchanged | -- | `state.monthly_payment` | identical | Mod (assert-unchanged) |
| C6-6 | test_resolver_total_interest_unchanged | -- | `state.total_interest` | identical | Mod (assert-unchanged) |
| C6-7 | test_resolver_payoff_date_unchanged | -- | `state.payoff_date` | identical | Mod (assert-unchanged) |
| C6-8 | test_no_generate_schedule_in_resolver | grep | `app/services/loan_resolver.py` | zero matches | New |
| C6-9 | test_history_rows_marked_confirmed | resolver with 3 confirmed payments | `state.schedule[:3]` | every row has `is_confirmed=True` | New |
| C6-10 | test_forward_rows_marked_unconfirmed | same | `state.schedule[3:]` | every row has `is_confirmed=False` | New |

Re-pinned tests: **none**. C6-1..C6-7 are assert-unchanged
(behavior-preserving refactor). If any surfaces a true
byte-difference between old and new, that is a real regression
caught here -- stop and report.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_services/test_loan_resolver.py -v` green.
2. `./scripts/test.sh tests/test_integration/test_loan_unified_figures.py -v` green (F-022 invariant + ARM payoff consistency + per-period principal interest).
3. `./scripts/test.sh tests/test_services/test_debt_strategy.py tests/test_services/test_savings_dashboard_service.py tests/test_services/test_year_end_summary_service.py -v` green (downstream resolver consumers unchanged).
4. `grep -n "generate_schedule" app/services/loan_resolver.py` empty.
5. `pylint` clean.

**G. Downstream effects** Year-end summary, savings dashboard,
debt strategy, refinance all read `LoanState.schedule` through
the same resolver and inherit the new internals automatically.
No changes to those services.

**H. Rollback notes** Restore the direct `generate_schedule`
call in `resolve_loan`. The composer remains harmless. Schedule
values were unchanged, so revert is safe.

---

### Commit 7 -- calculate_payoff_by_date and refinance on new primitives

**A. Commit message** `refactor(loan): calculate_payoff_by_date and refinance on new primitives`

**B. Problem statement** Two route-level callers of
`generate_schedule` remain after Commit 6:

- `payoff_calculate`'s `target_date` branch calls
  `amortization_engine.calculate_payoff_by_date` (route line
  1336), which internally calls `generate_schedule` twice
  (lines 766 standard + 811 binary search inner). The route's
  `current_principal` is correctly the resolver's
  `state.current_balance` (route line 1330) -- no bug today, but
  the migration consolidates the function on the new primitives.
- `refinance_calculate` calls `generate_schedule` once at route
  line 1444 for a fresh forward projection from a known starting
  point. No bug today; migrated for consistency.

Per design decision D-F, neither migration changes external
behavior. The `calculate_payoff_by_date` projected-payments fix
(OPT-1) is explicitly deferred to a separate follow-up.

## C. Files modified

- `app/services/amortization_engine.py`: rewrite
  `calculate_payoff_by_date` body to use `project_forward`
  (re-grep `:740-832`). The function's external signature is
  unchanged; only its internals collapse onto the new primitive.
  Both internal `generate_schedule` calls become
  `project_forward` calls.
- `app/routes/loan.py`: `refinance_calculate` (re-grep
  `:1367-1513`). Replace the line 1444 `generate_schedule(...)`
  call with `project_forward(...)`. The refi inputs (principal,
  rate, term) map directly onto `project_forward` parameters:
  - `starting_balance = refi_principal`
  - `starting_date = schedule_start + 1 month` (the day-1-of-month
    pattern the existing code uses; pass the right
    `starting_date` so `_advance_month` works correctly)
  - `annual_rate = refi_rate`
  - `remaining_months = refi_term`
  - `payment_day = params.payment_day`
  - `contractual_payment = calculate_monthly_payment(refi_principal, refi_rate, refi_term)`
  - `monthly_override = None`
  - `extra_monthly = Decimal("0.00")`
- `tests/test_services/test_amortization_engine.py`:
  `TestPayoffByDate` tests still pass (assert-unchanged behavior).
- `tests/test_routes/test_loan.py`: `TestLoanRefinance` tests
  still pass (assert-unchanged).

**D. Implementation approach**

For `calculate_payoff_by_date`:

```python
def calculate_payoff_by_date(
    current_principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
    target_date: date,
    origination_date: date,
    payment_day: int,
    original_principal: Decimal | None = None,
    term_months: int | None = None,
    rate_changes: list[RateChangeRecord] | None = None,
) -> Decimal | None:
    # Compute the contractual payment from original terms (existing logic)
    if original_principal is not None and term_months is not None:
        contractual = calculate_monthly_payment(
            original_principal, annual_rate, term_months,
        )
    else:
        contractual = calculate_monthly_payment(
            current_principal, annual_rate, remaining_months,
        )

    starting_date = _advance_month(
        origination_date.year, origination_date.month, payment_day,
    )

    # Standard projection: no extra
    standard = project_forward(
        starting_balance=current_principal,
        starting_date=starting_date,
        annual_rate=annual_rate,
        remaining_months=remaining_months,
        payment_day=payment_day,
        contractual_payment=contractual,
        extra_monthly=Decimal("0.00"),
        rate_changes_remaining=rate_changes,
    )
    if not standard:
        return Decimal("0.00")

    standard_payoff = standard[-1].payment_date
    if standard_payoff <= target_date:
        return Decimal("0.00")

    target_months = _months_between(origination_date, target_date) + 1
    if target_months <= 0:
        return None
    if target_months >= remaining_months:
        return Decimal("0.00")

    # Binary search
    lo = Decimal("0.01")
    hi = current_principal
    for _ in range(100):
        mid = round_money((lo + hi) / 2)
        schedule = project_forward(
            starting_balance=current_principal,
            starting_date=starting_date,
            annual_rate=annual_rate,
            remaining_months=remaining_months,
            payment_day=payment_day,
            contractual_payment=contractual,
            extra_monthly=mid,
            rate_changes_remaining=rate_changes,
        )
        if not schedule:
            return mid
        if schedule[-1].payment_date <= target_date:
            hi = mid
        else:
            lo = mid
        if hi - lo <= Decimal("0.01"):
            break
    return hi
```

For `refinance_calculate`:

```python
refi_monthly = amortization_engine.calculate_monthly_payment(
    refi_principal, refi_rate, refi_term,
)
schedule_start = date.today().replace(day=1)
starting_date = _advance_month(
    schedule_start.year, schedule_start.month, params.payment_day,
)
refi_schedule = amortization_engine.project_forward(
    starting_balance=refi_principal,
    starting_date=starting_date,
    annual_rate=refi_rate,
    remaining_months=refi_term,
    payment_day=params.payment_day,
    contractual_payment=refi_monthly,
    monthly_override=None,
    extra_monthly=Decimal("0.00"),
    rate_changes_remaining=None,
)
```

(Note: `_advance_month` is a private engine helper. Either expose
it as `amortization_engine.advance_to_next_payment_date(...)` or
let the route compute the next payment date inline. Decide at
commit-authoring time; preferred is exposing it as a public
helper since `project_forward` callers all need the same
calculation.)

After this commit
`grep -n "amortization_engine.generate_schedule" app/` shows the
function definition only; `grep -n "amortization_engine.calculate_summary" app/`
empty (the only call site was removed in Commit 4). Both
functions are still defined but unreferenced from production.
Commit 9 deletes them.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C7-1 | test_calculate_payoff_by_date_target_in_past | target_date before today | function | returns `None` (existing behavior) | Mod (assert-unchanged) |
| C7-2 | test_calculate_payoff_by_date_target_already_achieved | target_date >= standard payoff | function | returns `Decimal("0.00")` | Mod (assert-unchanged) |
| C7-3 | test_calculate_payoff_by_date_converges | 30yr/$300k/6%, target 20yr out | function | returns Decimal extra payment within $0.01 tolerance of hand-computed value | Mod (assert-unchanged) |
| C7-4 | test_calculate_payoff_by_date_unchanged_vs_pre_commit | parameterize 5 representative inputs | function | byte-identical to pre-commit results | Mod (assert-unchanged) |
| C7-5 | test_refinance_unchanged_vs_pre_commit | symptom refi fixture | route | rendered partial values byte-identical (monthly_savings, interest_savings, break_even_months, refi_total_interest) | Mod (assert-unchanged) |
| C7-6 | test_no_generate_schedule_in_calculate_payoff_by_date | grep | `app/services/amortization_engine.py` `calculate_payoff_by_date` body | no `generate_schedule` reference | New |
| C7-7 | test_no_generate_schedule_in_refinance | grep | `app/routes/loan.py` `refinance_calculate` body | no `generate_schedule` reference | New |
| C7-8 | test_target_date_route_branch_unchanged | POST `/accounts/<id>/loan/payoff` `mode=target_date` | parse response | required_extra, total_monthly, monthly_payment match pre-commit | Mod (assert-unchanged) |

Re-pinned tests: **none**. Every test is assert-unchanged
(behavior-preserving refactor).

## F. Manual verification steps

1. `./scripts/test.sh tests/test_services/test_amortization_engine.py::TestPayoffByDate tests/test_routes/test_loan.py::TestLoanRefinance tests/test_routes/test_loan.py::TestPayoffCalculator -v` green.
2. On the running app, run the Payoff Calculator's "Target Date"
   mode for a known input and confirm the returned required extra
   matches pre-commit (paste both values).
3. On the running app, run the Refinance Calculator with a known
   input and confirm break-even months / monthly savings /
   interest savings byte-identical.
4. `grep -n "generate_schedule" app/` shows ONLY the function
   definition in `amortization_engine.py:326` plus the migration
   backfill call at `d3d25212504b.py:315` (Commit 8 removes that
   too).
5. `pylint` clean.

**G. Downstream effects** Only the migration call site and the
function definition remain. Commit 8 removes the migration site;
Commit 9 deletes the functions.

**H. Rollback notes** Revert both function bodies (engine
`calculate_payoff_by_date`, route `refinance_calculate`). The
new primitives remain harmless. Values unchanged, so revert is
safe.

---

### Commit 8 -- inline replay loop in d3d25212504b backfill

**A. Commit message** `refactor(migrations): inline replay loop in d3d25212504b backfill`

**B. Problem statement** (R-1) Migration
`d3d25212504b_create_loan_anchor_events_table_for_.py:315` calls
`amortization_engine.generate_schedule` to derive the
from-origination balance after replaying confirmed payments
during the loan-anchor-events backfill. The Phase 8 deletion of
`generate_schedule` (Commit 9) would break every fresh-database
rebuild that replays migrations. The migration only needs
"confirmed payments reduce balance" math -- no `extra_monthly`,
no rate-change re-amortization, no projected entries. Inline the
small replay loop so the migration is self-contained against
future engine refactors.

## C. Files modified

- `migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py`:
  re-grep current `_locate_current_principal` function (audit
  line ~250-329; verify with a fresh read). Replace the
  `amortization_engine.generate_schedule` call (line 315) with
  an inline replay loop that mirrors the engine's confirmed-payment
  branch logic (engine lines 537-574) for the subset the
  migration needs: confirmed payments only, no extras, no rate
  changes (the migration accepts None for `rate_changes` per the
  current code), no anchor (the migration is deriving the
  original from-origination balance).
- `tests/test_models/test_loan_anchor_backfill.py`: existing
  tests verify the backfill produces the same `current_principal`
  for each loan account; they stay green by construction.

**D. Implementation approach** Re-read the entire migration file
before editing. The inline loop:

```python
def _replay_from_origination_inline(
    original_principal: Decimal,
    annual_rate: Decimal,
    term_months: int,
    origination_date: date,
    payment_day: int,
    confirmed_payments: list,  # list of (payment_date, amount) tuples
) -> Decimal:
    """Inline replay of confirmed payments from origination.

    Inlined here (rather than calling amortization_engine.generate_schedule)
    so the migration is self-contained against future engine refactors.
    The migration only needs "confirmed payments reduce balance"
    math -- no extra_monthly, no rate changes, no projected
    entries.  This is the engine's confirmed-payment branch
    (amortization_engine.py:537-574) reduced to its essentials.
    """
    from decimal import Decimal, ROUND_HALF_UP
    import calendar as _calendar
    two_places = Decimal("0.01")
    if original_principal <= 0 or term_months <= 0 or not confirmed_payments:
        return original_principal

    # Build year-month lookup of confirmed payments.
    by_month: dict[tuple[int, int], Decimal] = {}
    for pay_date, amount in confirmed_payments:
        if pay_date < origination_date:
            continue  # pre-origination filter mirrors engine
        key = (pay_date.year, pay_date.month)
        by_month[key] = by_month.get(key, Decimal("0")) + Decimal(str(amount))

    monthly_rate = (
        Decimal(str(annual_rate)) / 12 if annual_rate > 0 else Decimal("0")
    )
    balance = Decimal(str(original_principal))

    pay_year = origination_date.year
    pay_month = origination_date.month + 1
    if pay_month > 12:
        pay_month = 1
        pay_year += 1

    for _ in range(term_months):
        if balance <= 0:
            break
        interest = (balance * monthly_rate).quantize(
            two_places, rounding=ROUND_HALF_UP,
        )
        key = (pay_year, pay_month)
        if key in by_month:
            total = by_month[key]
            principal_portion = total - interest
            if principal_portion >= balance:
                balance = Decimal("0.00")
            else:
                balance = (balance - principal_portion).quantize(
                    two_places, rounding=ROUND_HALF_UP,
                )
                if balance < 0:
                    balance = Decimal("0.00")
        # No payment record for this month: balance unchanged
        # (the migration does not apply contractual payments where
        # the user did not record one -- mirrors engine behavior
        # for missing months when payments=confirmed_only).

        pay_month += 1
        if pay_month > 12:
            pay_month = 1
            pay_year += 1

    return balance
```

Wait -- re-check: the engine's behavior for "month with no
payment record" applies the contractual payment, not "balance
unchanged." That is, `generate_schedule(payments=confirmed_only)`
fills in contractual rows for unrecorded months. The migration's
intent is exactly this: replay confirmed payments AND apply
contractual to recorded-then-missing months, to derive the
from-origination balance. So the inline loop must include the
contractual fallback branch too.

Revised loop:

```python
def _replay_from_origination_inline(...):
    # ... (setup same as above)

    # Compute contractual payment from original terms.
    if annual_rate <= 0:
        monthly_payment = (
            Decimal(str(original_principal)) / term_months
        ).quantize(two_places, rounding=ROUND_HALF_UP)
    else:
        factor = (1 + monthly_rate) ** term_months
        monthly_payment = (
            Decimal(str(original_principal)) * (monthly_rate * factor) / (factor - 1)
        ).quantize(two_places, rounding=ROUND_HALF_UP)

    for _ in range(term_months):
        if balance <= 0:
            break
        interest = (balance * monthly_rate).quantize(
            two_places, rounding=ROUND_HALF_UP,
        )
        key = (pay_year, pay_month)
        if key in by_month:
            total = by_month[key]
            principal_portion = total - interest
        else:
            # No record this month: apply contractual.
            principal_portion = monthly_payment - interest
        if principal_portion >= balance:
            balance = Decimal("0.00")
        else:
            balance = (balance - principal_portion).quantize(
                two_places, rounding=ROUND_HALF_UP,
            )
            if balance < 0:
                balance = Decimal("0.00")
        pay_month += 1
        if pay_month > 12:
            pay_month = 1
            pay_year += 1

    return balance
```

This matches `generate_schedule`'s output for the migration's
input shape: original principal as starting balance, no
`extra_monthly`, no `rate_changes`, no `anchor`, confirmed-only
payments. Verify by reading the engine code at `:537-605` --
the migration uses both branches (recorded month vs contractual
fallback) so the inline must too.

Replace the call:

```python
# BEFORE (line 315):
schedule = amortization_engine.generate_schedule(
    current_principal=orig_principal,
    annual_rate=rate,
    remaining_months=term_months,
    origination_date=origination_date,
    payment_day=payment_day,
    original_principal=orig_principal,
    term_months=term_months,
    payments=payments,
)
for row in reversed(schedule):
    if row.is_confirmed:
        return row.remaining_balance
return orig_principal

# AFTER:
confirmed_tuples = [
    (p.payment_date, p.amount) for p in payments
]
return _replay_from_origination_inline(
    original_principal=orig_principal,
    annual_rate=rate,
    term_months=term_months,
    origination_date=origination_date,
    payment_day=payment_day,
    confirmed_payments=confirmed_tuples,
)
```

Note one semantic difference: the original code walked
`reversed(schedule)` looking for the LAST row with
`is_confirmed=True`, returning that row's `remaining_balance`.
The inline replay returns the running balance AFTER processing
every month up to term_months (or the balance reaches zero). For
the migration's input (confirmed-only payments, contractual
fallback for missing months), these are equivalent at the
last-confirmed-month boundary IF the trailing contractual months
do not exist (which they would, in the original code -- it
walked the full term). The original code's
`reversed(schedule)... if row.is_confirmed` short-circuited at
the last confirmed row.

Decision: preserve the original behavior exactly. The inline
loop should stop at the LAST confirmed month, not walk the full
term:

```python
def _replay_from_origination_inline(...):
    # ... (same as above, but track last_confirmed_month)
    last_confirmed_balance = original_principal  # fallback
    for _ in range(term_months):
        # ... compute principal_portion and update balance
        if key in by_month:
            last_confirmed_balance = balance  # captured AFTER the confirmed payment applied
        # ... advance month
    return last_confirmed_balance
```

This matches the original `reversed(schedule)... if row.is_confirmed`
semantic exactly: return the balance after the last confirmed
payment was applied (not after subsequent contractual months).

Re-verify against `_locate_current_principal`'s actual current
behavior by reading the migration file in full before editing.

After this commit
`grep -n "amortization_engine" migrations/` shows
`d3d25212504b` no longer imports the module; the inline function
is its only replay path.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C8-1 | test_backfill_origination_balance_unchanged_no_payments | loan with no confirmed payments | upgrade | backfilled `current_principal` == `original_principal` (unchanged) | Mod (assert-unchanged) |
| C8-2 | test_backfill_origination_balance_with_confirmed | loan with three confirmed payments | upgrade | backfilled `current_principal` == post-last-confirmed-payment balance (byte-identical to pre-commit) | Mod (assert-unchanged) |
| C8-3 | test_backfill_idempotent_after_inline | -- | upgrade twice | second upgrade is a no-op (existing behavior) | Mod (assert-unchanged) |
| C8-4 | test_migration_no_engine_import | grep | `migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py` | no `from app.services import amortization_engine` and no `amortization_engine.generate_schedule` | New |
| C8-5 | test_migration_downgrade_round_trip | post-up | downgrade then upgrade | cleanly round-trips, no orphans | Mod (assert-unchanged) |
| C8-6 | test_inline_matches_engine_for_representative_inputs | three representative loans (no payments, partial payments, full term confirmed) | inline replay vs pre-commit engine result | identical Decimal | New |

Re-pinned tests: **none** (the inline loop must produce
byte-identical results to the engine for the migration's input
shape; if it does not, that is a real bug -- stop and report).

## F. Manual verification steps

1. `./scripts/test.sh tests/test_models/test_loan_anchor_backfill.py -v` green.
2. `flask db downgrade base && flask db upgrade head` on a
   prod-like clone produces the same `current_principal` for
   every loan account as the previous migration run (paste 3
   representative diffs -- they should be empty).
3. `grep -n "amortization_engine" migrations/` returns no
   references in `d3d25212504b`.
4. `pylint` clean.

**G. Downstream effects** Migration is now self-contained. Phase
8 / Commit 9 can safely delete `generate_schedule` without
breaking replay.

**H. Rollback notes** Restore the
`amortization_engine.generate_schedule` call. The engine
function still exists at this point (Commit 9 deletes it), so
revert is safe. If revert happens AFTER Commit 9 has landed, the
engine function must be restored first, or the inline loop must
stay.

---

### Commit 9 -- remove generate_schedule and calculate_summary

**A. Commit message** `refactor(amortization): remove generate_schedule and calculate_summary`

**B. Problem statement** After Commits 4-8 every production
caller of `generate_schedule` and `calculate_summary` has been
migrated to the new primitives. The functions are dead
production code. Their dedicated tests
(`TestPaymentAwareSchedule`, parts of `TestGenerateSchedule`,
`TestCalculateSummary`) pin behavior that the new primitives
already cover via `TestReplayConfirmedHistory`,
`TestProjectForward`, and `TestComputePayoffScenarios`. Delete
both functions and their tests in one atomic commit so the
codebase carries no dead surfaces.

## C. Files modified

- `app/services/amortization_engine.py`: delete
  `generate_schedule` (lines 326-633 in current code) and
  `calculate_summary` (lines 636-737). Also delete
  `AmortizationSummary` IF no caller remains -- re-grep
  `app/` for usages; `AmortizationSummary` is currently
  constructed inline by `payoff_calculate` and `dashboard` in
  `app/routes/loan.py` (route lines 557, 1226's return) so it
  may still be needed for the route-side summary object. KEEP
  `AmortizationSummary` if those routes still construct it
  (Commit 4 / Commit 5 may have kept the dataclass as a return
  shape adapter); delete if not.
- `tests/test_services/test_amortization_engine.py`: delete the
  test classes `TestPaymentAwareSchedule`, `TestGenerateSchedule`,
  `TestCalculateSummary`, and any other class that only tests
  `generate_schedule` or `calculate_summary`. Keep
  `TestCalculateMonthlyPayment`, `TestPayoffByDate` (still
  exists; uses `project_forward` internals after Commit 7),
  `TestCalculateRemainingMonths`, `TestAmortizationEngineRegression`
  (re-purpose its assertions to use the primitives where they
  apply), `TestPaymentRecordValidation`, `TestRateChangeRecordValidation`,
  `TestReplayConfirmedHistory` (new in Commit 1),
  `TestProjectForward` (new in Commit 2).

**D. Implementation approach** Re-grep
`grep -rn "generate_schedule\|calculate_summary" app/ tests/`
before deleting. Expected matches in `app/` after Commits 4-8:
- `app/services/amortization_engine.py` (definitions only)
Expected in `tests/`:
- `tests/test_services/test_amortization_engine.py` (the test
  classes being deleted)
- Possibly stub imports in other test files (delete if unused).

If any unexpected `app/` match exists, that caller was missed by
a prior commit -- stop and report.

Delete the functions and their test classes in one diff. If
`AmortizationSummary` is still used as a route-side return
shape, keep it as a frozen dataclass; if not, delete it too.

After this commit:
- `grep -n "def generate_schedule\|def calculate_summary" app/` empty
- `grep -n "generate_schedule(\|calculate_summary(" app/ tests/` empty
- The amortization engine surface is `replay_confirmed_history`,
  `project_forward`, `calculate_monthly_payment`,
  `calculate_remaining_months`, `calculate_payoff_by_date`,
  plus the dataclasses (`PaymentRecord`, `RateChangeRecord`,
  `AmortizationRow`, `ReplayResult`, and possibly
  `AmortizationSummary`).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C9-1 | test_engine_surface | grep | `app/services/amortization_engine.py` | only the documented surface functions remain; `generate_schedule` and `calculate_summary` absent | New |
| C9-2 | test_no_remaining_callers | grep | `app/` and `migrations/` | zero references to `generate_schedule` or `calculate_summary` outside the deleted definitions | New |
| C9-3 | test_full_suite_green_without_old_engine_tests | pytest collection | -- | pytest collects without the deleted test classes; no import errors | New |
| C9-4 | full suite passes | `./scripts/test.sh` | -- | `N passed`, 0 failed/errors/xfailed | -- |

Re-pinned tests: **none**. The deleted test classes are removed
together with the code they pinned -- per `remediation_plan.md`
Section 9 Commit 33's rule: "deleted together with the code it
pins -- this is not 'modifying a test to pass,' it is removing a
test for deleted dead code."

## F. Manual verification steps

1. `grep -rn "generate_schedule\|calculate_summary" app/ migrations/` returns nothing (no callers, no definitions).
2. `./scripts/test.sh` full suite green (~65 s, `N passed`, zero failed/errors/xfailed).
3. `pylint app/ --fail-on=E,F` clean.
4. On the running app, exercise: Payoff Calculator (both modes),
   Dashboard (chart + amortization tab + payment breakdown),
   Refinance Calculator. Every page renders correctly with no
   500.
5. Walk through the Symptom Walkthrough (Section 10) end-to-end.

**G. Downstream effects** Engine surface is now the two
primitives + the helpers. No dead code. The composer is the
canonical scenario-composition path. Future scenarios
(refinance, lump-sum, rate what-ifs) extend the composer in
`loan_resolver`, not by adding parameters to a single overloaded
engine function.

**H. Rollback notes** Restoring `generate_schedule` /
`calculate_summary` requires reverting Commits 4-8 in reverse
order (they each removed a caller). Pure revert of this commit
alone (re-adding the functions) leaves dead code; not
recommended.

---

### Commit 10 -- full gate + verification appendix

**A. Commit message** `chore(release): full gate + verification appendix`

**B. Problem statement** Final acceptance gate for the whole
amortization split, plus the non-code action of populating the
hand-computed reconciliation appendix (Section 11) with the
post-fix values produced during execution.

## C. Files modified

- `docs/plans/2026-05-21-amortization-engine-split-implementation.md`:
  populate Section 11's appendix with the actual pre-fix /
  post-fix values from execution (filled inline as each
  prior commit lands; this commit is the final consistency
  check).
- No source/test/migration changes.

## D. Implementation approach (gate checklist -- all must pass)

1. `./scripts/test.sh` full suite green (`N passed`, zero
   failed/errors/xfailed; ~65 s wall-clock).
2. `pylint app/ --fail-on=E,F` clean (no new warnings vs
   baseline).
3. `flask db upgrade && flask db downgrade base && flask db upgrade head`
   round-trip clean. (No schema changes in this plan, so the
   round-trip is purely confirming nothing else regressed.)
4. F-022 invariant
   (`tests/test_integration/test_loan_unified_figures.py::test_months_saved_single_quantity`)
   green by construction. The new composer regression test
   (Commit 3 C3-10) is the actual regression lock for THIS bug;
   confirm it is green.
5. The five end-to-end verification scenarios (Section 10) all
   pass on the running app.
6. Section 11 appendix is filled (every CRITICAL number has its
   pre-fix value, hand arithmetic, and post-fix value).
7. `git status` shows only intended files; commit messages
   follow `<type>(<scope>): <what>` with the required
   Co-Authored-By trailer per `CLAUDE.md`; developer asked
   before pushing.

## E. Test cases

The entire test suite is the test case. Acceptance: full green,
clean pylint, F-022 + Commit 3 C3-10 lock green.

## F. Manual verification steps

1. Walk all five end-to-end scenarios in Section 10 in the
   running app; each renders the corrected chart/figures.
2. Confirm Section 11 appendix matches the executed code.

**G. Downstream effects** Implementation complete; the plan is
preserved in-repo for traceability alongside the architectural
plan it implements.

**H. Rollback notes** The plan doc is additive. Code rollback
is per-commit (each H above). The Commit 3 C3-10 regression
lock makes a silent regression detectable.

---

## 10. End-to-end verification (symptom walkthrough)

After Commit 10, each scenario is re-tested in the running app
and locked by an automated invariant.

1. **Originally reported visual bug on `/accounts/3/loan`.**
   Open the page, run the Payoff Calculator with `extra=$500`
   (or whatever produces the reproduction). The Accelerated
   line tracks the Committed line through the historical region
   and departs from Committed only at and after today's month
   boundary. "Months Saved" and "Interest Saved" labels match
   the chart's visible divergence.
   - Automated: Commit 3 C3-10 (composer-level regression),
     Commit 4 C4-2 / C4-3 (HTTP-level regression).
2. **Loan with recurring transfer template generating projected
   future payments.** Same calculator with same extra. Extra is
   still applied forward (projections route through
   `monthly_override` and do NOT suppress extra in the
   projection layer).
   - Automated: Commit 2 C2-4 (the critical
     regression-prevention assertion at the primitive level),
     Commit 3 C3-7 (composer level).
3. **Loan with zero confirmed payments yet (newly set up).**
   Chart starts at origination with Original / Committed /
   Accelerated overlaying correctly.
   - Automated: Commit 4 C4-5.
4. **Year-end debt summary, debt strategy, savings dashboard
   unchanged.** Same numbers as before this plan.
   - Automated: Commit 6 C6-1..C6-7 (resolver assert-unchanged),
     plus the existing
     `tests/test_integration/test_loan_unified_figures.py`
     invariants pass.
5. **Refinance and "by date" payoff modes unchanged.** Same
   required-extra and refinance break-even as before.
   - Automated: Commit 7 C7-4 / C7-5 / C7-8.

Standing locks:

- Composer regression (Commit 3 C3-10): the user's exact
  reproduction will fail loud if any future change reintroduces
  "extra applied to ghost historical months."
- Override regression (Commit 2 C2-4): `extra_monthly` cannot
  be applied to override months in `project_forward` -- a
  primitive-level lock.
- Engine surface (Commit 9 C9-2): no production code may call
  `generate_schedule` or `calculate_summary` (they no longer
  exist).
- Resolver chokepoint (Commit 6 C6-8): `loan_resolver.py`
  contains no direct `generate_schedule` reference.

---

## 11. Hand-computed reconciliation appendix (filled at execution)

Each commit that changes a chart shape or summary number records
here: inputs, pre-fix value, hand arithmetic, post-fix value.
Values below were populated at Commit 10 from the executed code
and the hand-computed assertions pinned in the test suite. Every
post-fix value is the literal Decimal the composer / primitive
returns for the named inputs and is verified by a named test.

### Originally reported bug (Commit 4 user-visible fix)

Inputs: 30 yr / $300,000 / 6%, originated 2024-01-01, four
confirmed contractual payments Jan-Apr 2026 (each $1,798.65 on
day 1), no projected transfers, `extra_monthly=$500.00`,
`as_of=2026-05-21`.

Hand calculation of the contractual payment ($M^*$):
`P=300000`, `i=0.06/12=0.005`, `n=360`.
`M^* = P * i / (1 - (1+i)^-n) = 1500 / 0.83395769... = 1798.65` (USD).
Pinned in
`tests/test_services/test_loan_resolver.py::_four_contractual_payments_jan_to_apr_2026`
and matched by the new replay primitive at
`TestReplayConfirmedHistory::test_balance_after_replay_300k_4_payments`.

Replay snapshot after four contractual payments (C13-5 / C3-2):
`balance_as_of = $298,796.42`, `next_pay_date = 2026-05-01`,
`remaining_months_as_of = 356`, `applicable_rate_as_of = 0.06`.
Hand arithmetic for row 0 of each forward slice:
- `interest = 298796.42 * 0.005 = 1493.98` (ROUND_HALF_UP)
- `principal = 1798.65 - 1493.98 = 304.67`
- `balance(original) = balance(committed) = 298796.42 - 304.67 = 298,491.75`
- `balance(accelerated) = 298,491.75 - 500.00 = 297,991.75`

Pre-fix chart shape (the bug): Accelerated diverges below
Original from month 1 (Feb 2024); runs parallel to Committed
through Jan-Apr 2026 (confirmed window suppresses extra); resumes
accelerated descent May 2026 onward. The reported visual symptom.

Post-fix chart shape (locked by `TestPayoffChartShape::test_accelerated_equals_committed_in_historical_region`
and `::test_accelerated_below_committed_post_today`): Accelerated
equals Committed for every chart index whose label is at or
before today's month; Accelerated strictly below Committed for at
least one index past today.

| Field | Pre-fix (buggy `generate_schedule` flow) | Post-fix (composer) | Test lock |
|---|---|---|---|
| `len(history_rows)` | n/a (single fused schedule; ~24 ghost-history rows treated as forward) | 4 | C3-1 / C3-10 |
| `accelerated_forward[0].payment_date` | 2024-02-01 (fictitious 2024 acceleration) | 2026-05-01 | C3-3 / C3-10 |
| `months_saved` | >200 (inflated by ~23 months of ghost-history extra) | 145 | C3-8 / C3-10 |
| `interest_saved` | inflated (sum over ghost history included) | $156,559.54 | C3-9 |
| `total_interest_committed` | mixed history/forward sum | $341,524.42 | C3-9 (verified at composer output) |
| `total_interest_accelerated` | inflated (extra applied to ~$300k for fictitious months) | $184,964.88 | C3-9 |
| `payoff_date_committed` | computed from buggy summary | composer's `committed_forward[-1].payment_date` | derived from C3-8 / committed length 356 |
| `payoff_date_accelerated` | inflated (earlier by ~23 fictitious-extra months) | composer's `accelerated_forward[-1].payment_date` | derived from C3-8 / accelerated length 211 |

Closed-form verification of `months_saved`:
- Committed (no extra, P&I = $1798.65, starting balance $298,796.42, $i=0.005$):
  `n = -log(1 - P*i/M) / log(1+i) = -log(0.169393) / log(1.005)`
  approx 356 months. Composer returns 356.
- Accelerated (P&I = $1798.65 + $500 = $2298.65 base, $298,796.42 starting balance):
  `n = -log(1 - 298796.42*0.005/2298.65) / log(1.005) = -log(0.350067) / log(1.005)`
  approx 210.44, ceiled to 211 at month-boundary HALF_UP. Composer returns 211.
- `months_saved = 356 - 211 = 145`. Matches C3-8 assertion.

### Override + extra interaction (Commit 2 C2-4 primitive lock)

Inputs: `starting_balance=$300,000.00`, `annual_rate=0.06`,
`remaining_months=360`, `payment_day=1`,
`contractual_payment=$1,798.65`,
`monthly_override={(2026, 6): $2,000.00}`,
`extra_monthly=$500.00`.

Hand arithmetic:
- June 2026 (override month): `payment = $2,000.00`,
  `extra_payment = $0.00` (CRITICAL: extra is NEVER added to an
  override month, even when `extra_monthly` is set).
- July 2026 (no override): `payment = contractual = $1,798.65`,
  `extra_payment = $500.00`. Total cash out July = $2,298.65.
- Every non-final override-less month past June 2026 carries
  `extra_payment = $500.00`. Final row's `extra_payment` is
  capped at remaining balance per the existing overpayment-cap
  branch.

Pre-fix: in `generate_schedule`, a projected payment record for
June 2026 would suppress `extra_monthly` (gate was "any record
present"); the same gate misfired on origination-to-first-confirmed
months, allowing extra against fictitious history.

Post-fix: `project_forward` accepts `monthly_override` and
`extra_monthly` as independent parameters; the override path
unconditionally sets `extra_payment = $0.00`. Locked by
`TestProjectForward::test_override_plus_extra_extra_not_added_to_override_months`
(C2-4). The buggy parameter combination is now syntactically
unexpressible.

### Resolver behavior preservation (Commit 6 assert-unchanged)

Sample inputs: $300k / 6% / 360 mo, origination 2026-01-01, three
confirmed contractual payments Feb-Apr 2026 of $1,798.65 each,
`as_of=2026-05-01`. (The shortened gap variant; the long-gap
variant is the C3-10 regression scenario above.)

Post-fix invariants pinned by C6-9 / C6-10:
- `state.schedule[0..2].is_confirmed == True` (history rows from
  replay).
- `state.schedule[3..].is_confirmed == False` (forward rows from
  projection).
- `state.current_balance`, `state.monthly_payment`,
  `state.total_interest`, `state.payoff_date` byte-identical to
  pre-Commit-6 values. Commit 6 was a behavior-preserving
  refactor; the seven `test_resolver_*_unchanged` cases (C6-1..
  C6-7) pinned the existing values and all pass.

ARM in-window monthly payment (5/5 ARM, $400k, 2028-01-01 trueup
to $380,000.00, resolved at `as_of=2028-06-01` and `as_of=2030-06-01`):
- `state.monthly_payment = $2,337.47` -- byte-identical at both
  as_of dates inside the fixed window (verified by the existing
  C13-1 test, which Commit 6 was required to preserve).

### Migration backfill replay (Commit 8 C8-6)

Inputs: representative loans (no payments / partial confirmed
payments / full term confirmed) processed by
`_replay_from_origination_inline` in
`migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py`.

Post-fix: the inline loop's running balance after each replayed
month is byte-identical to the value `generate_schedule` produced
for the same inputs (confirmed-only, no extras, no rate changes,
no anchor). The migration is self-contained against the Commit 9
deletion of `generate_schedule`. Verified by C8-6
(`test_inline_matches_engine_for_representative_inputs`) and by
the migration round-trip on a throwaway database
(`shekel_roundtrip_check`): upgrade head -> downgrade past
`d3d25212504b` to `cfb15e782f86` -> upgrade head clean. Full
downgrade to base halts at `a80c3447c153` by design (the C-41 /
F-069 partial unique index migration is irreversible-by-design
per `docs/coding-standards.md`, raising `NotImplementedError`
with the manual recovery SQL); this is pre-existing and unrelated
to the engine split.

### Engine surface after Commit 9

After the deletion of `generate_schedule` and `calculate_summary`:
- `grep -rn "generate_schedule\|calculate_summary" app/ migrations/`
  returns no production references (only the historical commit
  messages in git, which are not in the working tree).
- `loan_resolver.py` source contains no `generate_schedule`
  string (C6-8 lock at
  `test_no_generate_schedule_in_resolver`).
- The amortization engine surface is now:
  `replay_confirmed_history`, `project_forward`,
  `calculate_monthly_payment`, `calculate_remaining_months`,
  `calculate_payoff_by_date`, plus the dataclasses
  (`PaymentRecord`, `RateChangeRecord`, `AmortizationRow`,
  `ReplayResult`). `AmortizationSummary` was retained in
  `app/services/amortization_engine.py` because
  `payoff_calculate` and `dashboard` still construct it as the
  route-side summary return shape.

---

## 12. Open questions carried forward

- **Q-1. Should `calculate_payoff_by_date` ALSO honor projected
  payments via `monthly_override`?** D-F defers this. The latent
  issue: a user who already pays $500/mo over contractual via a
  recurring transfer template is told they need $X extra, when
  in reality they need $X-$500. Fixing it is a behavior change
  (the "required extra" number drops for those users) and
  deserves its own follow-up entry. Recommendation: add an
  `F-N` entry to
  `docs/audits/financial_calculations/remediation_follow_up.md`
  during Commit 7 execution.
- **Q-2. Should `LoanState.schedule` be renamed to make its
  "committed-no-extra forward" semantics explicit?** OPT-4
  lists this. The current name is correct but ambiguous in light
  of the new composer (which has explicit "committed_forward"
  and "accelerated_forward"). Recommendation: leave alone for
  now; rename only if a future bug surfaces from semantic
  ambiguity.
- **Q-3. Should the dashboard expose a "scenarios_floor" badge
  for stale anchors?** OPT-2 lists this. Independent of this
  plan; carried for a future UI iteration.

---

## 13. Notes on executing this plan

- Run commits in order; the dependency DAG (Section 7) is
  binding. Commits 1-3 can be authored back-to-back with no
  pause between them (each is a pure addition). Commit 4 lands
  the user-visible fix. Commits 5-7 are architectural
  consistency. Commit 8 is the migration prerequisite for
  Commit 9; do not reorder.
- Every commit: re-grep cited lines first (architectural plan's
  line ranges drift -- e.g. `payoff_calculate` was wrong);
  targeted tests during edits (`./scripts/test.sh
  tests/path/test_file.py -v`); `pylint app/ --fail-on=E,F`
  after edits; full suite via `./scripts/test.sh` as the
  per-commit final gate. `SKIP_DB_RESTART=1` on follow-up
  invocations in the same session.
- The test template does NOT need to be rebuilt by this plan
  (no schema changes, no `app/ref_seeds.py` or
  `app/audit_infrastructure.py` edits).
- Never silently re-pin a test. The plan calls out "Re-pinned
  tests: none" on every commit; if execution surfaces a test
  that needs re-pinning, name the finding (this plan's section
  or the architectural plan's section) and the hand arithmetic
  in a comment, per the common rules' Section 1 rule 4.
- Per `remediation_follow_up_common.md`, every session ends with
  the work summary using labels A-M verbatim. The summary
  documents what landed, what stayed in scope, what was flagged
  out of scope (with `file:line` + `F-N` entries where needed),
  and asks "Ready to commit and push to dev?" -- do not push
  without explicit approval.
- This is an implementation plan only. No code is changed by
  producing this document. Execution happens in separate
  sessions, one commit per session, suite green before moving
  on.
