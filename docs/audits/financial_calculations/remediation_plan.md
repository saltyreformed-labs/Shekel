> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Financial Calculation Audit -- Remediation Plan

- Version: 1.0
- Date: 2026-05-19
- Author: prepared for the solo developer (SaltyReformed)
- Source audit: `docs/audits/financial_calculations/` (Phases 0-9 complete; `08_findings.md` is the
  developer-facing finding register, 25 findings)
- Final deliverable location (written only after this plan is approved):
  `docs/audits/financial_calculations/remediation_plan.md`
- Prerequisite reading: `08_findings.md`, `05_symptoms.md`, `03_consistency.md`,
  `04_source_of_truth.md`, `00_priors.md` (the locked E-NN expectations), `CLAUDE.md` (Transfer
  Invariants, Rules), `docs/coding-standards.md`, `docs/testing-standards.md`.

---

## 0. Context

Shekel is a pay-period budgeting app where every downstream view (grid, /savings, /accounts,
dashboard, calendar, net worth, retirement, debt strategy) depends on a small set of financial
calculations. A read-only audit traced five developer-reported symptoms to structural defects and
produced 25 findings (5 CRITICAL, 8 HIGH, 7 MEDIUM, 5 LOW). The audit's central conclusion: **the
app has no single canonical producer for "what is this account's balance" or "what is this loan's
principal/payment/schedule," so the same number is computed several different ways and silently
disagrees.** That is why $160 on the grid is $114.29 on /savings, and why a fixed-window ARM payment
creeps a few dollars a month.

This is a budgeting app for real money. A wrong number that ships without an error is the worst
failure mode. The remediation therefore does not patch symptoms; it establishes single sources of
truth (the developer-locked E-NN expectations), routes every consumer through them, and locks the
behavior with hand-computed pinned tests and a cross-page equality regression test that did not
previously exist.

This plan has been verified against the live code, not taken on faith from the audit. The
verification confirmed every CRITICAL finding and surfaced refinements the audit under-counted
(Section 3). Audit line numbers have drifted; every commit instructs a re-grep before editing
(CLAUDE.md rule 2 and rule 7).

### Consequence of getting this wrong

Every figure on /savings, /accounts, the grid, net worth, and debt strategy is derived from the
balance and loan calculations. A residual defect in the canonical producers propagates to every page
at once. The test strategy in this plan (hand-computed Decimal pins, cross-page equality invariant,
ARM-window stability lock, FICA-cap regression) exists specifically so that a future change cannot
silently reintroduce drift.

---

## 1. Hard rules for executing this plan

These bind every commit. They restate CLAUDE.md and the testing standards in the context of this
remediation.

1. **Read the entire file before editing it.** Audit citations are stale on line numbers. Each
   commit's implementation section says "re-grep to confirm current lines." Never edit by remembered
   line number.
2. **Never modify a test to make it pass.** Where a fix changes a previously-shipping wrong number,
   the corresponding test currently pins the wrong value. The developer has confirmed (via the audit
   + the locked E-NN expectations) that the corrected behavior is the intended behavior, so those
   specific assertions are re-pinned to the hand-computed correct value, with a comment citing the
   finding ID and the arithmetic. This is the documented exception in CLAUDE.md rule 5; it is called
   out per commit in subsection E as "Re-pinned tests" and must never be applied silently or to a
   test whose value was not proven wrong by a finding.
3. **Decimal only, constructed from strings.** No float in monetary paths. Test expectations are
   `Decimal("...")` with the arithmetic shown in a comment.
4. **IDs for logic, strings for display.** Status/type comparisons use cached IDs or semantic
   boolean columns, never `name` strings (memory: id-based lookups).
5. **DRY/SOLID.** A concept is computed in exactly one place after this plan. No override parameters
   that paper over a broken internal (memory: no band-aid fixes). No gold-plating.
6. **Atomic commits, suite green after each.** Targeted tests per change; the full suite (`pytest`,
   ~62 s at the `pytest.ini` `-n 12` default) only as the final gate of each commit and as the
   plan's final gate (memory: targeted per change, full suite as final gate).
   `pylint app/ --fail-on=E,F` after every commit, no new warnings.
7. **Migrations: additive first, downgrade always works.** Destructive changes (drops, type
   narrowing, CHECK replacement) carry a `Review:` docstring line and explicit developer approval,
   and follow the three-step add-NOT-NULL pattern from the coding standards. New
   `auth`/`budget`/`salary` tables are added to `AUDITED_TABLES` and the template is rebuilt.
8. **Style.** No Unicode dashes anywhere (use `--` or `-`). Pythonic, type-hinted, substantive
   docstrings, specific exceptions.
9. **Scope.** Only the finding under the current commit. Out-of-scope issues are reported, not
   fixed.

---

## 2. Design decisions (made for this plan; confirm at review)

The developer selected these during planning:

- **D-A. Loan source of truth (E-18): new append-only `budget.loan_anchor_events` table.** An
  origination event plus user "balance true-up" events. The event-derived loan resolver replays
  confirmed payments forward from the latest anchor event. `LoanParams.current_principal` and
  `LoanParams.interest_rate` are demoted to nullable, non-authoritative seed columns that are never
  read for display. Additive migration (lowest data-loss risk), fully normalized. An optional later
  destructive drop of the demoted columns is listed in Section 5 as an enhancement, not done here.
- **D-B. Scope: comprehensive, critical-first.** One ordered document covering all 25 findings.
  Commits run CRITICAL -> HIGH -> MEDIUM -> LOW respecting the dependency DAG, so the wrong-number
  bugs and their regression lock land first.
- **D-C. "Edit current principal" UX becomes a dated balance true-up event** that appends a
  `LoanAnchorEvent`, mirroring exactly the existing checking-account `AccountAnchorHistory` true-up
  UX. Consistent mental model across account types.

---

## 3. Discovered refinements beyond the audit (folded into scope)

Live-code verification confirmed every CRITICAL finding and corrected/expanded several specifics.
These are folded into the relevant commits, not left as audit-vs-code gaps.

- **R-1. The silent-degrade balance seam has more consumers than CRIT-01 listed.** Verified
  bare-query sites with no `selectinload(Transaction.entries)`, all silently degrading to
  `effective_amount`: `app/services/savings_dashboard_service.py`
  (~~:92`), `app/routes/accounts.py` checking detail ~~`:1272-1281`, call
  ~~:1291`), `app/services/calendar_service.py` ~~`:471`),
  `app/services/year_end_summary_service.py`
  (~~:2085`), **plus `app/routes/investment.py` two sites ~~`:95`,
  ~~:415`) and `app/services/retirement_dashboard_service.py` ~~`:410`) which the audit did not
  enumerate.** Correct (entries-loaded) sites: `app/routes/grid.py` (~`:229`,
  ~~:438`), `app/services/dashboard_service.py` ~~`:689`). The CRIT-01 rollout (Commits 5-8) routes
  **all** of these through the canonical producer, including the two extra investment sites and the
  retirement site.
- **R-2. E-18 is a consolidation, not a greenfield build.** The event-replay infrastructure already
  exists: `PaymentRecord` / `RateChangeRecord` frozen dataclasses in `amortization_engine.py`,
  `get_payment_history()` and `load_loan_context()` in `loan_payment_service.py` (confirmed-payment
  stream derived from shadow income via `status.is_settled`), `generate_schedule()` already accepts
  `payments`, `rate_changes`, `anchor_balance`, `anchor_date`, and
  `debt_strategy._compute_real_principal()` already replays confirmed payments for fixed-rate loans.
  The gap is the ARM path (returns the stored scalar verbatim, anchor pinned to `today`) and the
  absence of one resolver every surface reads. This materially de-risks Commits 12-17.
- **R-3. `current_anchor_balance`/`current_anchor_period_id` are nullable with no CHECK;
  `AccountAnchorHistory` already exists** (`app/models/account.py`, append-only true-up trail for
  checking, unique per account/period/balance/day). E-19 extends this established pattern rather
  than inventing one; the loan analog is the new `LoanAnchorEvent` (D-A).
- **R-4. RECEIVED is `is_settled=True, is_immutable=True`** in `app/ref_seeds.py` (verified),
  identical protection to DONE/SETTLED, yet `archive_helpers.py` `template_has_paid_history` /
  `transfer_template_has_paid_history` enumerate only `[DONE, SETTLED]` and `hard_delete_template`
  then bulk-deletes unconditionally. CRIT-05 confirmed exactly as written.
- **R-5. The `round_money` helper genuinely does not exist** (`app/utils/` has no `money.py`;
  `grep "def round_money" app/` empty). The amortization engine's quantize sites already pass
  `ROUND_HALF_UP` explicitly; the 24 risky sites are the **bare** `.quantize(Decimal("0.01"))` calls
  that silently get Python's default `ROUND_HALF_EVEN` (banker's). Commit 1 introduces the helper;
  the per-site migration commits prove cents are unchanged where already-correct and corrected where
  bare, against hand-computed expectations.
- **R-6. No cross-page balance-equality test exists** (the three audit-mandated greps return zero
  matches; the nearest `test_grid.py` test recomputes its own balance rather than rendering a second
  page). HIGH-01 is real; Commit 11 adds the missing lock.
- **R-7. Anchor true-up helper extraction (commit `d551f9c`, between Commit 7 and Commit 8).** An
  unplanned but in-scope pre-Commit-16 cleanup: `inline_anchor_update` and `true_up` in
  `app/routes/accounts.py` carried byte-identical `try/except StaleDataError + except IntegrityError`
  blocks wrapping the same conditional `entry_service.clear_entries_for_anchor_true_up` + commit
  pair, plus the same `is_unique_violation(_ANCHOR_HISTORY_UNIQUE_INDEX)` F-103 / C-22 discriminator.
  The plan's Commit 16 instruction "Route+schema+template mirror checking true-up" would have
  written a third inline copy.  Pre-emptively consolidated into
  `app/services/anchor_service.apply_anchor_true_up` returning an `AnchorTrueUpOutcome` enum
  (`COMMITTED` / `STALE_CONFLICT` / `DUPLICATE_SAME_DAY`).  No route-level behavior change (all 137
  `test_accounts.py` tests pass byte-identical).  Eight new pinned helper unit tests close a
  pre-existing coverage gap (`inline_anchor_update`'s F-103 path had no test).  Commit 16 now
  EXTENDS the helper for loan-anchor events rather than copying the pattern; see the Commit 16
  detail in Section 9 for the updated scope.

---

## 4. Concept -> single-source-of-truth map (the spine of the plan)

Every multi-path concept collapses onto one producer. This table is the contract the commits
implement.

| Concept | Canonical producer after remediation | Locked expectation | Commits |
|---|---|---|---|
| account/checking balance, projected_end_balance, period_subtotal, chart balance series | `balance_resolver` (date-anchored, entries-aware, always loads or computes entries) | E-19, E-25, E-27 | 3-11 |
| loan principal (real), monthly_payment, schedule, total_interest, interest_saved, months_saved, payoff_date | `loan_resolver` (pure, event-derived; replays confirmed payments from latest `LoanAnchorEvent`; honors ARM fixed-rate window) | E-18 | 12-17 |
| money rounding boundary | `app/utils/money.round_money` (2dp, ROUND_HALF_UP) + `round_money_ceiling` | E-26 | 1, then applied throughout |
| balance-contributing status predicate | one semantic predicate (`Status` boolean columns + one helper) | E-15 | 2, 29 |
| FICA (all paths incl. calibration) | wage-base cap enforced on every path | IRS invariant | 18 |
| calibration effective rates | server re-derived at confirm from stored actual_* + base (immutable snapshot) | E-20 | 19 |
| retirement SWR / weighted return | zero is a value, not "missing" (`is None`, never truthiness) | E-12 | 20 |
| template hard-delete guard | semantic `is_settled`; destructive delete constrained to non-settled | E-22 | 21 |
| monthly-equivalent obligations aggregate | one aggregator with shared ONCE/`end_date` filter; named 26/12 constants | E-24 | 23 |
| stored numeric domain | one DB CHECK consistent with Marshmallow | E-28 | 24 |
| entry-tracked bill row remaining/over-budget | one declared, disclosed base | E-21 | 30 |

The single remaining open question, **Q-26 sub-2** (whether a bracket-based fallback for
`estimated_retirement_tax_rate` should ever exist), is a product decision carried forward; LOW-05
only corrects the misleading comment (A-26's decided direction) and does not build a fallback.

---

## 5. Optional enhancements (listed per the developer's instruction; not in the default commit set unless promoted)

Each is independently valuable, low-risk, and called out so the developer can opt in. The plan flags
exactly which commit would carry each if promoted.

- **OPT-1. Destructive drop of demoted loan columns.** After Commit 15 proves nothing reads
  `current_principal`/`interest_rate`, a later destructive migration could drop them. Adds a
  `Review:` line; irreversible. Recommended only after a full production cycle confirms the
  resolver.
- **OPT-2. One-time integrity scan for already-destroyed RECEIVED history (CRIT-05).** A read-only
  script that reports any income templates whose linked RECEIVED transactions were hard-deleted
  before Commit 21. Cannot restore data; surfaces the blast radius. Folded as Commit 22 (read-only,
  safe) but marked optional.
- **OPT-3. Enforced "no Flask in services" import-linter test (B6-01).** Converts the 22 prose
  docstring contracts into one mechanical AST test over `app/services/*.py`. Folded as Commit 36
  (test-only, safe).
- **OPT-4. Property-based cross-page consistency harness.** Beyond the PT-01 fixture (Commit 11), a
  Hypothesis-style generator that fuzzes anchor/entries/status combinations and asserts all surfaces
  agree. Higher build cost; listed only.
- **OPT-5. CD-account support (LOW-03 / W-019).** Add nullable `maturity_date`/`term_months` to
  `interest_params`. This is an absent feature, not a bug; build only if CD accounts are wanted.
  Listed only.
- **OPT-6. Stale-anchor surfacing.** `calculate_balances` returns a `stale_anchor_warning` that only
  the grid consumes. The canonical `balance_resolver` could surface it uniformly (badge on every
  balance card). Folded as a documented extension point in Commit 5; UI promoted only if wanted.

---

## 6. Codebase inventory (files this plan touches)

Re-grep each path at edit time; line numbers below are audit-era and drift.

## New files

- `app/utils/money.py` (E-26 helper) -- Commit 1
- `app/utils/balance_predicates.py` or `Status`/`Transaction` semantic methods (E-15) -- Commit 2
- `app/services/balance_resolver.py` (E-19/E-25/E-27 canonical balance producer) -- Commits 4-10
- `app/models/loan_anchor_event.py` + migration (E-18 infra) -- Commit 12
- `app/services/loan_resolver.py` (E-18 canonical loan producer) -- Commit 13
- `app/services/investment_dashboard_service.py` (MED-01 extraction) -- Commit 28
- Test files mirroring each (`tests/test_services/`, `tests/test_integration/`,
  `tests/test_routes/`)

## Modified services

- `balance_calculator.py` (engine internals fold into / are called by `balance_resolver`; remove
  silent-degrade seam ~`:353-354`)
- `amortization_engine.py` (resolver consolidation; `get_loan_projection` -> DTO)
- `loan_payment_service.py` (payment-history feed into `loan_resolver`)
- `savings_dashboard_service.py`, `dashboard_service.py`, `calendar_service.py`,
  `year_end_summary_service.py`, `debt_strategy.py`, `retirement_dashboard_service.py`,
  `calibration_service.py`, `tax_calculator.py`, `paycheck_calculator.py`,
  `savings_goal_service.py`, `interest_projection.py`, `growth_engine.py`, `entry_service.py`,
  `transfer_recurrence.py`

## Modified routes

- `grid.py`, `accounts.py`, `savings.py`, `loan.py`, `debt_strategy.py`, `obligations.py`,
  `investment.py`, `salary.py`, `transactions.py`, `templates.py`

## Modified models / schemas / ref

- `account.py`, `loan_params.py`, `transaction.py`, `interest_params.py`, `investment_params.py`,
  `user.py`, `loan_features.py`, `schemas/validation.py`, `ref_seeds.py`,
  `app/audit_infrastructure.py` (`AUDITED_TABLES`)

## Templates / static

- `grid/_transaction_cell.html`, `loan/dashboard.html`, `loan/_escrow_list.html`,
  `loan/_schedule.html`, `obligations/summary.html`, retirement/variance JS
  (`retirement_gap_chart.js`, `chart_variance.js`)

---

## 7. Commit dependency analysis

```text
Foundations
  1 money.round_money ───────────────┐
  2 status predicate ───────────┐    │
                                 │    │
Balance SoT (E-19/E-25/E-27)     │    │
  3 anchor backfill+invariant    │    │
  4 anchor resolver ─────────────┤    │
  5 balance_resolver (grid,dash) ┴────┤   (uses 1,2,4)
  6 route /savings ──────────────┐    │
  7 route /accounts checking     │    │
  8 route year-end/networth/inv/ret    (all depend on 5)
  9 calendar balance-as-of-date (E-27, depends on 5)
 10 period_subtotal canonical (depends on 5; resolves Q-10)
 11 cross-page equality lock (depends on 5-10)

Loan SoT (E-18)  (depends on 1; independent of balance group)
 12 loan_anchor_events table+backfill
 13 loan_resolver (depends on 12; consolidates existing replay infra)
 14 settled-transfer reduces principal (test+verify; depends on 13)
 15 demote columns + route all loan consumers (depends on 13)
 16 principal true-up UX (depends on 12,15)
 17 loan amortization divergences via resolver (depends on 13,1)

Independent criticals
 18 FICA cap on calibration
 19 calibration immutable snapshot (depends on 18 shared signature)
 20 retirement zero-not-missing
 21 hard-delete semantic guard
 22 (opt) integrity scan (depends on 21 semantics)

HIGH structural   23-27   (23,24 independent; 25 depends on growth_engine; 26 depends on paycheck producer; 27 indep)
MEDIUM            28-32   (28 depends on 13 for DTO; 29 depends on 2; 30 depends on 5; 31 depends on producers; 32 broad)
LOW + gate        33-37
```

Ordering rationale: foundations (1-2) unblock the most consumers; the balance group (3-11) fixes
symptoms #1/#5-checking and installs the regression lock before anything else can regress it; the
loan group (12-17) fixes #2/#3/#4/#5-loan and is independent so it can proceed in parallel review;
independent criticals (18-21) have no cross-deps; HIGH/MEDIUM/LOW follow. Every commit leaves the
suite green; commits that change a shipping wrong number re-pin only the assertions that finding
proved wrong (rule 2, Section 1).

---

## 8. Commit checklist

| # | Commit message | Summary |
|---|---|---|
| 1 | `feat(utils): add money.round_money boundary helper (E-26)` | Single 2dp ROUND_HALF_UP rounding helper + `round_money_ceiling`; golden-cents tests; no call-site swaps yet |
| 2 | `refactor(status): centralize balance-contributing status predicate (E-15)` | One semantic predicate over `Status` boolean cols; tests; no behavior change |
| 3 | `fix(anchor): backfill origination anchor so anchor period is never NULL (E-19)` | Migration backfills every account's anchor; creation always writes one; invariant test |
| 4 | `feat(balance): date-anchored anchor resolver, NULL state unreachable (E-19)` | Pure anchor resolver; tests for every prior NULL-anchor fork |
| 5 | `feat(balance): canonical entries-aware balance/subtotal producer (E-25)` | `balance_resolver` always loads/computes entries; grid + dashboard routed; seam removed; re-pin tests |
| 6 | `fix(savings): route /savings balances through canonical producer` | Symptom #1: /savings now equals grid; re-pin /savings tests |
| 7 | `fix(accounts): route /accounts checking detail through canonical producer` | Symptom #5 checking facet; re-pin /accounts tests |
| 8 | `fix(balance): route year-end/net-worth/investment/retirement through producer` | Closes R-1 extra silent-degrade sites; re-pin affected tests |
| 9 | `fix(calendar): month-end balance via canonical balance-as-of-date (E-27)` | HIGH-02: true month-end date, entries-aware; re-pin calendar tests |
| 10 | `fix(grid): period_subtotal through canonical producer (Q-10, E-25)` | Removes same-page F-002 Pair C divergence; re-pin subtotal tests |
| 11 | `test(integration): cross-page balance-equality regression lock (HIGH-01)` | PT-01 fixture; asserts all surfaces equal for one tuple; permanent invariant |
| 12 | `feat(loan): append-only loan_anchor_events table + backfill (E-18)` | Model, migration, origination+trueup backfill, AUDITED_TABLES, template rebuild |
| 13 | `feat(loan): pure event-derived loan resolver (E-18)` | Consolidates projection/real-principal/payoff; ARM fixed-window honored; hand-computed + stability tests |
| 14 | `test(loan): settled transfer reduces resolved principal (symptom #3)` | Integration proof confirmed transfers reduce principal on every surface |
| 15 | `refactor(loan): demote current_principal/interest_rate; route all consumers (E-18)` | Columns nullable seed; loan card/debt-strategy/savings/net-worth via resolver; re-pin tests |
| 16 | `feat(loan): principal edit becomes dated balance true-up event (E-18 UX)` | Route+schema+template mirror checking true-up; EXTENDS `anchor_service.apply_anchor_true_up` (R-7) rather than pasting a third try/except copy; tests |
| 17 | `fix(loan): unify per-period/interest/payoff figures via resolver+round_money (HIGH-08)` | Remaining amortization divergences collapsed |
| 18 | `fix(tax): enforce SS wage-base cap on calibration path (CRIT-03)` | Thread cumulative YTD wages + ss_wage_base into calibration; hand-computed cap test |
| 19 | `fix(calibration): server-derive effective rates at confirm (E-20)` | Immutable pay-stub snapshot; schema cross-check; tests |
| 20 | `fix(retirement): zero is a value not missing (E-12, CRIT-04)` | `is None` SWR; zero-return account included; phantom-income test |
| 21 | `fix(templates): semantic is_settled hard-delete guard (E-22, CRIT-05)` | RECEIVED protected; bulk delete constrained to non-settled; data-loss test |
| 22 | `chore(audit): read-only scan for pre-fix destroyed RECEIVED history (OPT-2)` | Optional; reports blast radius; no mutation |
| 23 | `refactor(obligations): one monthly-equivalent aggregator (E-24, HIGH-05)` | Shared ONCE/end_date filter; named 26/12 constants; expired-template test |
| 24 | `fix(schema): reconcile Marshmallow domains with DB CHECK (E-28, HIGH-06)` | trend/rate/apy/limit/return defaults; migrations where CHECK changes |
| 25 | `fix(investment): unify employer-match across card/chart/year-end (HIGH-07)` | Capped contribution everywhere; three-surface equality test |
| 26 | `fix(savings): DTI gross from raise-aware paycheck producer (MED-06)` | One income producer; raise-applicable DTI test |
| 27 | `fix(interest): leap-year day count + biweekly residue reconcile (MED-05)` | Actual day count; annual residue reconciled |
| 28 | `refactor(investment): extract dashboard service; collapse dispatcher; DTO (MED-01)` | SRP/OCP/ISP/DIP; `LoanInputs` DTO |
| 29 | `refactor(status): route residual inline/Jinja predicates through helper (MED-02)` | Finishes E-15 centralization |
| 30 | `fix(dashboard): entry-tracked bill row single disclosed base (E-21, MED-03)` | Amount and remaining/over-budget on one declared base |
| 31 | `refactor(templates): move money math out of Jinja/JS into services (MED-04)` | Eliminate `|float`; Decimal in services only |
| 32 | `test(calc): replace loose assertions; add invariant coverage (MED-07)` | Pinned Decimal; sad-path/boundary/status-machine/annual reconciliation |
| 33 | `chore(tax): delete dead legacy calculate_federal_tax + its test (LOW-01)` | Remove inert divergence and TestLegacyWrapper together |
| 34 | `fix(transfer): route recurrence regen delete through transfer_service (LOW-02)` | Restores orphan-check + audit event on regen deletes |
| 35 | `docs(audit): correct comment/table drift (LOW-04, LOW-05, R-9, R-10)` | Doc-only; Q-26 sub-2 comment; escrow row; R-notes |
| 36 | `test(arch): enforce no-Flask-in-services import linter (OPT-3, B6-01)` | One mechanical AST test over services |
| 37 | `chore(release): full gate + save remediation doc` | Full suite, pylint, migrations up+down, invariants green; write `docs/audits/financial_calculations/remediation_plan.md` |

Promotable options not in the default count: OPT-1 (destructive column drop), OPT-4, OPT-5, OPT-6.

---

## 9. Commits (detailed)

Each commit follows the house format: A message, B problem, C files, D implementation, E tests, F
manual verification, G downstream, H rollback. Test IDs are `C<commit>-<n>`. "Re-pinned tests" lists
assertions changed under the Section 1 rule 2 exception with the finding ID and arithmetic.

### Commit 1 -- money.round_money boundary helper (E-26, HIGH-04)

**A. Commit message** `feat(utils): add money.round_money boundary helper (E-26)`

**B. Problem statement** HIGH-04: there is no `app/utils/money.py` and no `round_money`. 24 monetary
`.quantize(Decimal("0.01"))` sites pass no `rounding=` and therefore silently use Python's default
`ROUND_HALF_EVEN` (banker's), while every hand-computed test and the amortization engine assume
`ROUND_HALF_UP`. This commit introduces the single boundary helper. It does not yet swap call sites
(kept atomic and reviewable; swaps happen in their domain commits).

## C. Files modified

- `app/utils/money.py` (new): `round_money(value: Decimal) -> Decimal` and
  `round_money_ceiling(value: Decimal) -> Decimal`.
- `tests/test_utils/test_money.py` (new).

## D. Implementation approach

```python
"""Centralized monetary rounding boundary (E-26).

Full-precision Decimal arithmetic everywhere; rounding happens once,
here, at the display/persistence boundary. ROUND_HALF_UP is the only
default -- it is the convention every hand-computed financial test in
this project assumes. ROUND_HALF_EVEN (Python's Decimal default) is a
silent source of one-cent drift and must never be reached implicitly.
"""
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING

CENTS = Decimal("0.01")

def round_money(value: Decimal) -> Decimal:
    """Round a monetary Decimal to cents, half-up.

    Args:
        value: a Decimal in full precision. Passing float is a bug;
            callers construct Decimal from strings.
    Returns:
        value quantized to 0.01 using ROUND_HALF_UP.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"round_money expects Decimal, got {value!r}")
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)

def round_money_ceiling(value: Decimal) -> Decimal:
    """Round up to cents (sanctioned variant; e.g. savings-goal monthly
    contribution that must not under-fund). Named so the exception is
    explicit at the call site, never an implicit rounding mode."""
    if not isinstance(value, Decimal):
        raise TypeError(f"round_money_ceiling expects Decimal, got {value!r}")
    return value.quantize(CENTS, rounding=ROUND_CEILING)
```

No call-site changes. `pylint app/ --fail-on=E,F` clean.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C1-1 | test_round_money_half_up_boundary | -- | `round_money(Decimal("2.345"))` | `Decimal("2.35")` (half-up, not 2.34 banker's) | New |
| C1-2 | test_round_money_half_up_even_digit | -- | `round_money(Decimal("2.355"))` | `Decimal("2.36")` | New |
| C1-3 | test_round_money_negative_half_up | -- | `round_money(Decimal("-2.345"))` | `Decimal("-2.35")` | New |
| C1-4 | test_round_money_already_two_places | -- | `round_money(Decimal("100.00"))` | `Decimal("100.00")` | New |
| C1-5 | test_round_money_long_precision | -- | `round_money(Decimal("1234.5650001"))` | `Decimal("1234.57")` | New |
| C1-6 | test_round_money_zero | -- | `round_money(Decimal("0"))` | `Decimal("0.00")` | New |
| C1-7 | test_round_money_rejects_float | -- | `round_money(2.345)` | raises `TypeError` | New |
| C1-8 | test_round_money_ceiling_rounds_up | -- | `round_money_ceiling(Decimal("2.341"))` | `Decimal("2.35")` | New |
| C1-9 | test_round_money_ceiling_exact | -- | `round_money_ceiling(Decimal("2.340"))` | `Decimal("2.34")` | New |
| C1-10 | test_round_money_ceiling_rejects_float | -- | `round_money_ceiling(2.34)` | raises `TypeError` | New |

## F. Manual verification steps

1. `python -c "from app.utils.money import round_money; from decimal import Decimal; print(round_money(Decimal('2.345')))"`
   prints `2.35` (proves not banker's).
2. `pytest tests/test_utils/test_money.py -v` all pass.
3. `pylint app/ --fail-on=E,F` clean.

**G. Downstream effects** Pure addition; nothing imports it yet. Domain commits (13, 17, 18, 20, 24,
27) swap their bare quantize sites to this helper with per-site hand-computed proof.

**H. Rollback notes** Delete the two new files. No migration, no data, no behavior change.

---

### Commit 2 -- Centralize the balance-contributing status predicate (E-15, MED-02 foundation)

**A. Commit message** `refactor(status): centralize balance-contributing status predicate (E-15)`

**B. Problem statement** MED-02 / D6-09: the rule "which statuses contribute to a balance" is
re-expressed inline in 20+ places in three forms (Python `txn.status_id != projected_id`, SQLAlchemy
filters, Jinja conditionals), and `[CREDIT, CANCELLED]` is re-derived as two helpers plus inline. A
one-sided change silently drifts a balance. This commit creates one semantic predicate; it is
behavior-preserving (verified against the existing `Status.excludes_from_balance` boolean and
`Status.is_settled`).

## C. Files modified

- `app/utils/balance_predicates.py` (new): `is_balance_contributing(txn) -> bool`,
  `is_projected(txn) -> bool`, `balance_excluded_status_ids() -> set[int]`, and a SQLAlchemy clause
  builder `balance_contributing_clause()` so the ORM filter and the Python predicate share one
  definition.
- `tests/test_utils/test_balance_predicates.py` (new).
- No consumer is rewired in this commit (atomic; Commits 5/10/29 rewire callers as they touch them).

**D. Implementation approach** Use the existing semantic boolean columns
(`Status.excludes_from_balance`, `Status.is_settled`) and cached IDs from `ref_cache` -- never
`name` strings (rule 4). The Python predicate and the SQLAlchemy clause are generated from the same
`ref_cache` lookups so they cannot diverge.

```python
def is_balance_contributing(txn) -> bool:
    """True if this transaction's effective_amount participates in a
    projected balance. Mirrors Transaction.effective_amount's own
    exclusion of soft-deleted and excludes_from_balance statuses, in
    one place so SQL filters and Python loops cannot drift apart."""
    if txn.is_deleted:
        return False
    return not (txn.status and txn.status.excludes_from_balance)
```

The clause builder returns
`and_(Transaction.is_deleted.is_(False), Status.id.notin_(balance_excluded_status_ids()))` for use
in queries that join `Status`.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C2-1 | test_projected_contributes | Projected txn | `is_balance_contributing` | True | New |
| C2-2 | test_credit_excluded | Credit txn | `is_balance_contributing` | False | New |
| C2-3 | test_cancelled_excluded | Cancelled txn | `is_balance_contributing` | False | New |
| C2-4 | test_settled_contributes | Settled txn | `is_balance_contributing` | True | New |
| C2-5 | test_soft_deleted_excluded | is_deleted=True | `is_balance_contributing` | False | New |
| C2-6 | test_clause_matches_predicate | mixed-status set in DB | query with clause == Python-filtered list | identical id sets | New |
| C2-7 | test_excluded_ids_are_credit_cancelled | ref seeded | `balance_excluded_status_ids()` | exactly {Credit.id, Cancelled.id} | New |
| C2-8 | test_predicate_uses_ids_not_names | -- | assert no `.name ==` in module source (ast scan) | passes | New |

## F. Manual verification steps

1. `pytest tests/test_utils/test_balance_predicates.py -v` passes.
2. Confirm C2-6 (clause vs predicate parity) on a realistic seeded mix.
3. `pylint` clean.

**G. Downstream effects** Nothing rewired yet. Commit 5 (`balance_resolver`) and Commit 10 (period
subtotal) consume it as their only status gate; Commit 29 finishes routing residual inline/Jinja
predicates through it.

**H. Rollback notes** Delete two new files. No behavior change to revert.

---

### Commit 3 -- Backfill an origination anchor so the anchor period is never NULL (E-19 part 1)

**A. Commit message**
`fix(anchor): backfill origination anchor so anchor period is never NULL (E-19)`

**B. Problem statement** CRIT-01 / F-001 SCOPE_DRIFT: when `current_anchor_period_id IS NULL` the
five balance producers fork four different ways (grid blank, /accounts+/savings fallback projection,
dashboard+net-worth omit). E-19 makes the NULL state unreachable: every account always has an
anchor. This commit backfills existing data and makes account creation always write an anchor; the
resolver itself is Commit 4.

## C. Files modified

- `migrations/versions/<auto>_backfill_account_anchor.py` (new): three-step populated-table pattern
  -- (1) for any account with `current_anchor_period_id IS NULL`, derive the earliest pay period
  covering or preceding the account's first transaction (or the user's earliest period if none) and
  set `current_anchor_balance = COALESCE(current_anchor_balance, 0.00)`,
  `current_anchor_period_id = <derived>`; write the matching `AccountAnchorHistory` row; (2) verify
  zero NULLs remain (raise `RuntimeError` with the diagnostic SELECT if any survive); (3)
  `alter_column` both anchor columns to `nullable=False` and add `ck_account_anchor_period_not_null`
  is implicit via NOT NULL. Carries a
  `Review: solo developer, 2026-05-19 (audit financial_calculations CRIT-01/E-19)` docstring line
  (type change -> NOT NULL on populated table).
- `app/routes/accounts.py` + `app/services/auth_service.py` (account creation): always create the
  origination `AccountAnchorHistory` and set the anchor columns at creation (re-grep current
  creation sites; audit cites `auth_service.py:781-786`, `accounts.py:655`).
- `app/models/account.py`: `current_anchor_balance`/`current_anchor_period_id` -> `nullable=False`;
  add `CHECK (current_anchor_balance IS NOT NULL)` named `ck_accounts_anchor_balance_present`.

**D. Implementation approach** Downgrade re-widens the columns to nullable (reversible; data
retained). The derivation rule is documented in the migration docstring: anchor period = the
`PayPeriod` whose `[start_date, end_date]` contains the account's earliest non-deleted transaction's
pay period, else the earliest period for the user; anchor balance defaults to `0.00` (a real zero,
not "missing" -- E-12). This is deterministic and reproducible for staging rebuilds. No financial
figure changes for accounts that already had an anchor; accounts that had none move from "undefined
fork" to "explicit zero-anchored projection," which is the intended E-19 behavior.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C3-1 | test_migration_backfills_null_anchor | account with NULL anchor + 1 txn | upgrade | anchor_period_id = derived period; balance 0.00; history row exists | New |
| C3-2 | test_migration_leaves_existing_anchor_untouched | account with anchor set | upgrade | anchor unchanged byte-for-byte | New |
| C3-3 | test_migration_raises_if_unresolvable | engineered NULL with no periods | upgrade | `RuntimeError` with diagnostic SELECT | New |
| C3-4 | test_downgrade_rewidens_nullable | post-upgrade | downgrade | columns nullable again, data retained | New |
| C3-5 | test_account_creation_writes_anchor | new account via route | POST create | anchor columns non-NULL + history row | New/Mod |
| C3-6 | test_model_rejects_null_anchor | -- | construct Account with NULL anchor | IntegrityError on flush | New |

## F. Manual verification steps

1. On a clone of prod-like data: `flask db upgrade`; run the diagnostic SELECT -> zero NULL anchors.
2. `flask db downgrade` then `flask db upgrade` round-trips cleanly.
3. `python scripts/build_test_template.py` (migration changed the schema; CLAUDE.md requires
   rebuild).
4. Create a new account in the UI -> /accounts shows it immediately with a zero-anchored projection,
   no blank/omitted row.

**G. Downstream effects** Commit 4's resolver can assume a non-NULL anchor (deletes four
NULL-handling forks). `AUDITED_TABLES` already includes `accounts`/`account_anchor_history`; no
audit change. Template rebuild required (note in Commit 37 gate).

**H. Rollback notes** `flask db downgrade` re-widens columns; backfilled values and history rows are
retained (harmless). Revert the model/route edits.

---

### Commit 4 -- Date-anchored anchor resolver (E-19 part 2)

**A. Commit message** `feat(balance): date-anchored anchor resolver, NULL state unreachable (E-19)`

**B. Problem statement** CRIT-01 / F-001: each consumer resolves the anchor differently
(`account.current_anchor_period_id` vs fallback to current period vs omit). E-19 requires one
resolver that, given an account, returns `(anchor_balance: Decimal, anchor_period: PayPeriod)`
deterministically, never None, reading the latest `AccountAnchorHistory` event as the dated source
of truth (the column becomes a cache of that latest event).

## C. Files modified

- `app/services/balance_resolver.py` (new, anchor section):
  `resolve_anchor(account, scenario_id) -> AnchorPoint` where `AnchorPoint` is a frozen dataclass
  `(balance: Decimal, period: PayPeriod, as_of_date: date)`.
- `tests/test_services/test_balance_resolver_anchor.py` (new).

**D. Implementation approach** `resolve_anchor` reads the most recent `AccountAnchorHistory` row for
the account (already ordered `created_at desc` per the model relationship); its `anchor_balance` and
`pay_period` are authoritative. The `Account.current_anchor_*` columns are treated as a denormalized
cache of that latest event and are reconciled (not trusted) -- if they disagree with the latest
history row, the history row wins and a structured `log_event` records the reconciliation (no Flask
import; services stay pure). Returns Decimal balance constructed via `Decimal(str(...))`. Never
returns None (Commit 3 guarantees an event exists).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C4-1 | test_resolve_anchor_from_latest_history | 2 history rows | `resolve_anchor` | balance/period of newest row | New |
| C4-2 | test_resolve_anchor_history_wins_over_stale_column | column != latest row | `resolve_anchor` | history row value; reconciliation logged | New |
| C4-3 | test_resolve_anchor_never_none | freshly created account | `resolve_anchor` | origination AnchorPoint, not None | New |
| C4-4 | test_resolve_anchor_scenario_scoped | two scenarios | `resolve_anchor(acct, s2)` | s2 anchor | New |
| C4-5 | test_resolve_anchor_decimal_type | -- | type of `.balance` | `Decimal`, 2dp | New |
| C4-6 | test_resolve_anchor_zero_balance_is_value | history balance 0.00 | `resolve_anchor` | `Decimal("0.00")`, not treated as missing (E-12) | New |

## F. Manual verification steps

1. `pytest tests/test_services/test_balance_resolver_anchor.py -v`.
2. Manually true-up a checking account twice; confirm `resolve_anchor` returns the second event.
3. `pylint` clean (assert no `flask` import in the new service:
   `grep -nE '\bflask\b' app/services/balance_resolver.py` empty).

**G. Downstream effects** Commit 5 builds the balance producer on top of `resolve_anchor`. No
consumer rewired yet.

**H. Rollback notes** Delete the anchor section + its test. No data, no migration.

---

### Commit 5 -- Canonical entries-aware balance/subtotal producer; route grid + dashboard (E-25)

**A. Commit message** `feat(balance): canonical entries-aware balance/subtotal producer (E-25)`

**B. Problem statement** CRIT-01 root: `balance_calculator._entry_aware_amount` silently returns
`txn.effective_amount` when `'entries' not in txn.__dict__` (verified at
`balance_calculator.py:353-354`), so the same Projected envelope expense is one number when the
caller eager-loaded entries (grid: $160) and another when it did not (/savings: $114.29). E-25
requires one producer that is entries-aware by construction -- it loads the entries it needs (or
computes the reduction itself), so the result can never depend on an ORM eager-load detail.

## C. Files modified

- `app/services/balance_resolver.py`: add
  `balances_for(account, scenario_id, periods) -> BalanceResult` and `period_subtotal(...)`.
  Internally it owns the transaction query (with `selectinload(Transaction.entries)` and
  `selectinload(Transaction.status)` mandatory), calls `resolve_anchor` (Commit 4), uses
  `is_balance_contributing` (Commit 2) as the only status gate, and `round_money` (Commit 1) as the
  only rounding boundary. The existing `balance_calculator` math (`_sum_remaining`/`_sum_all`
  semantics, interest, amortization layering) is reused -- not rewritten (CLAUDE.md rule 10) -- but
  the entries-aware reduction is unconditional: the `'entries' not in __dict__` short-circuit is
  deleted because the producer guarantees entries are loaded.
- `app/routes/grid.py` and `app/services/dashboard_service.py`: call
  `balance_resolver.balances_for(...)` instead of building their own query + `calculate_balances`.
  These two already loaded entries, so their numbers do not change (regression-safety: their
  existing pinned tests stay green unchanged).
- `tests/test_services/test_balance_resolver.py` (new), plus targeted edits in
  `tests/test_routes/test_grid.py` only if a test asserted the engine's pre-consolidation internal
  shape (behavioral assertions unchanged).

**D. Implementation approach** `balances_for` is the single date-anchored entries-aware producer.
Algorithm: resolve anchor -> query the account's non-deleted transactions for the scenario and
period span with entries+status eager-loaded -> for the anchor period sum remaining (Projected only)
using the entry-aware reduction `max(estimated - cleared_debit - sum_credit, uncleared_debit)`, for
post-anchor periods sum all Projected the same way -> carry forward -> `round_money` at the
boundary. Because the producer owns the query, the seam is structurally gone. Keep
`balance_calculator`'s pure math functions; `balance_resolver` becomes their only caller for
page-facing balances. Do not delete `balance_calculator` yet (Commits 6-10 migrate the remaining
callers; Commit 17/28 revisit). Re-grep before editing: confirm the seam is still at
`_entry_aware_amount` and the grid/dashboard queries still eager-load entries.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C5-1 | test_producer_loads_entries_itself | Projected $500 expense, cleared entries $45.71, anchor $614.29, caller does NOT pre-load | `balances_for` | `Decimal("160.00")` (614.29 - max(500-45.71-0,0)=454.29) | New |
| C5-2 | test_producer_same_value_regardless_of_caller_preload | same, caller pre-loads entries | `balances_for` | `Decimal("160.00")` (identical to C5-1) | New |
| C5-3 | test_no_entries_uses_effective_amount | Projected $500, no entries at all | `balances_for` | `Decimal("114.29")` (614.29-500) -- correct here because there are genuinely no entries | New |
| C5-4 | test_credit_entry_reduces_reservation | $500 est, $500 credit entry | `balances_for` | reservation 0; balance = anchor | New |
| C5-5 | test_uncleared_floor | $500 est, $600 uncleared debit | `balances_for` | reservation = 600 (floor) | New |
| C5-6 | test_grid_value_unchanged | full grid fixture | grid route | byte-identical to pre-commit grid pinned values | Mod (assert-unchanged) |
| C5-7 | test_dashboard_value_unchanged | dashboard fixture | dashboard service | identical to pre-commit | Mod (assert-unchanged) |
| C5-8 | test_seam_removed | grep | `'entries' not in` absent from balance_resolver | passes | New |
| C5-9 | test_status_gate_is_shared_predicate | Credit + Cancelled present | `balances_for` | excluded via Commit 2 helper, not inline | New |
| C5-10 | test_anchor_zero_real_value | anchor 0.00, one $100 income | `balances_for` | `Decimal("100.00")` (0 is a value) | New |

Re-pinned tests: none (grid/dashboard already on the correct path; values unchanged by
construction).

## F. Manual verification steps

1. Reproduce symptom #1 data (anchor 614.29, Projected groceries 500.00, three cleared entries
   20.00/15.71/10.00). Grid shows 160.00 (unchanged).
2. `pytest tests/test_services/test_balance_resolver.py tests/test_routes/test_grid.py -v`.
3. `grep -n "not in txn.__dict__" app/services/balance_resolver.py` -> empty.

**G. Downstream effects** Commits 6-10 point the remaining consumers at this producer; after Commit
8 the silent-degrade seam has no live caller. OPT-6 extension point: `BalanceResult` can carry the
`stale_anchor_warning` uniformly.

**H. Rollback notes** Revert grid/dashboard to the prior call; delete the new producer methods +
tests. `balance_calculator` untouched, so reverting is local.

---

### Commit 6 -- Route /savings through the canonical producer (symptom #1)

**A. Commit message** `fix(savings): route /savings balances through canonical producer`

**B. Problem statement** CRIT-01 / F-009 / symptom #1: `savings_dashboard_service` queries
transactions without `selectinload(entries)` (verified ~`:92`) and calls the engine, so /savings
shows $114.29 where the grid shows $160.00 for the same inputs. Route it through
`balance_resolver.balances_for`, which loads entries itself.

## C. Files modified

- `app/services/savings_dashboard_service.py`: replace the manual query + `calculate_balances*`
  calls (re-grep; audit cites `:92-100`, `:335`, `:343`, `:352`) with
  `balance_resolver.balances_for`. Keep the per-account dispatch shape for now (MED-01/Commit 28
  collapses the dual dispatcher).
- `tests/test_services/test_savings_dashboard_service.py`: re-pin the checking-balance assertions to
  the hand-computed correct value.

**D. Implementation approach** The only behavioral change is the checking/HYSA balance now
reflecting the entry-aware reduction (the correct value). Re-grep the call sites; the audit's lines
have drifted (verification found the checking-detail query at a different line than the audit
stated).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C6-1 | test_savings_equals_grid_symptom1 | symptom #1 tuple | render /savings + grid | both `Decimal("160.00")` | New |
| C6-2 | test_savings_hysa_entry_aware | HYSA + cleared entries | savings service | entry-aware balance | New |
| C6-3 | test_savings_no_entries_unchanged | account with no entries | savings service | same as before commit | Mod (assert-unchanged) |

Re-pinned tests (rule 2 exception; finding F-009/CRIT-01): in `test_savings_dashboard_service.py`,
assertions previously expecting the pre-fix value (e.g. `Decimal("114.29")` for the symptom tuple)
become `Decimal("160.00")` with a comment:
`# F-009: was 114.29 (entries silently unloaded); correct = 614.29 - max(500-45.71-0,0) = 160.00`.

## F. Manual verification steps

1. Symptom #1 fixture: /savings checking tile == grid current-period balance == 160.00.
2. `pytest tests/test_services/test_savings_dashboard_service.py -v`.

**G. Downstream effects** /savings now agrees with grid/dashboard. Commit 11 locks this with the
cross-page invariant.

**H. Rollback notes** Revert the call substitution; revert re-pinned assertions to prior values
(documents the regression if rolled back).

---

### Commit 7 -- Route /accounts checking detail through the canonical producer (symptom #5 checking facet)

**A. Commit message** `fix(accounts): route /accounts checking detail through canonical producer`

**B. Problem statement** CRIT-01 / symptom #5: the /accounts checking-detail query omits
`selectinload(entries)` (verified ~`:1272-1281`, call ~`:1291`) and additionally forks on
anchor-NULL differently from grid. With Commit 3 (no NULL anchor) and `balance_resolver`, both
divergence axes close.

## C. Files modified

- `app/routes/accounts.py`: replace the checking-detail query +
  `calculate_balances_with_interest`/`calculate_balances` calls with
  `balance_resolver.balances_for`. Re-grep current lines.
- `tests/test_routes/test_accounts.py`: re-pin checking-detail balance assertions.

**D. Implementation approach** Behavioral change: checking detail now equals grid/savings. The
anchor-NULL fork is dead code after Commit 3 -- delete it rather than leave it unreachable (rule 5:
do it right).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C7-1 | test_accounts_checking_equals_grid | symptom tuple | /accounts detail vs grid | equal `Decimal("160.00")` | New |
| C7-2 | test_accounts_anchor_null_fork_removed | grep | no `or current_period` anchor fallback in checking path | passes | New |
| C7-3 | test_accounts_multi_account_each_correct | 2 checking accts w/ entries | /accounts | each entry-aware | New |

Re-pinned tests (F-001/CRIT-01): `test_accounts.py` checking-detail values updated to the
entry-aware value with finding-citing comments and the arithmetic.

## F. Manual verification steps

1. /accounts checking detail == grid == /savings for the symptom tuple.
2. New account (zero anchor) shows a populated zero-anchored projection, not blank/omitted.
3. `pytest tests/test_routes/test_accounts.py -v`.

**G. Downstream effects** Three of five producers now agree; Commit 8 finishes the rest.

**H. Rollback notes** Revert call substitution and re-pinned assertions.

---

### Commit 8 -- Route year-end/net-worth, investment (x2), retirement through the producer (closes R-1)

**A. Commit message**
`fix(balance): route year-end/net-worth/investment/retirement through producer`

**B. Problem statement** CRIT-01 + discovered refinement R-1: `year_end_summary_service`
(~~:2085`), `investment.py` ~~`:95`, ~~:415`) and `retirement_dashboard_service` ~~`:410`) also
query without `selectinload(entries)` and silently degrade. The audit enumerated only the first;
verification found the investment/retirement sites. Route every remaining live caller of
`calculate_balances*` for checking-style balances through `balance_resolver`.

## C. Files modified

- `app/services/year_end_summary_service.py` (net-worth/balance map; re-grep `:2085`, `:2102`,
  `:2108`, `:2127`, plus `:1245`, `:1602`).
- `app/routes/investment.py` (~`:95`, ~`:415`).
- `app/services/retirement_dashboard_service.py` (~`:410`).
- `tests/test_services/test_year_end_summary_service.py`, `tests/test_routes/test_investment.py`,
  `tests/test_services/test_retirement_dashboard_service.py`: re-pin affected balance assertions.

**D. Implementation approach** The loan/net-worth schedule path is handled by the loan resolver
(Commits 13-15); this commit only routes the checking-style balance reads. After this commit,
`grep -rn "calculate_balances" app/routes app/services` should show callers only inside
`balance_resolver` and `loan_resolver` (and `balance_calculator` internals) -- the silent-degrade
seam has no external live caller.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C8-1 | test_networth_balance_entry_aware | symptom tuple acct in net worth | year-end service | entry-aware value, equals grid | New |
| C8-2 | test_investment_holdings_entry_aware | investment acct w/ entries | /investment | entry-aware | New |
| C8-3 | test_investment_growth_chart_entry_aware | growth chart data | route | entry-aware series | New |
| C8-4 | test_retirement_projection_entry_aware | retirement acct w/ entries | retirement service | entry-aware | New |
| C8-5 | test_no_external_calculate_balances_callers | grep | no `calculate_balances` calls outside resolver/calculator | passes | New |

Re-pinned tests (R-1/CRIT-01): year-end/investment/retirement balance assertions updated to
entry-aware values with finding-citing comments + arithmetic.

## F. Manual verification steps

1. Net worth, /investment, /retirement balance figures equal the grid for the symptom tuple.
2. `pytest tests/test_services/test_year_end_summary_service.py tests/test_routes/test_investment.py tests/test_services/test_retirement_dashboard_service.py -v`.
3. `grep -rn "calculate_balances" app/routes app/services | grep -v balance_resolver | grep -v loan_resolver | grep -v balance_calculator`
   -> empty.

**G. Downstream effects** All five+ checking-balance producers now read one source. Commit 11 locks
it permanently.

**H. Rollback notes** Revert the substitutions and re-pinned assertions per file.

---

### Commit 9 -- Calendar month-end balance via canonical balance-as-of-date (E-27, HIGH-02)

**A. Commit message** `fix(calendar): month-end balance via canonical balance-as-of-date (E-27)`

**B. Problem statement** HIGH-02 / W-277: `calendar_service._compute_month_end_balance` is a second
non-entries-aware path that also picks the last pay period ending on-or-before the calendar
month-end (up to ~13 days stale) instead of a true balance-as-of-date. Two defects (entries + period
selection) in one path.

## C. Files modified

- `app/services/balance_resolver.py`: add
  `balance_as_of_date(account, scenario_id, as_of: date) -> Decimal` (E-27): resolve anchor, project
  periods, then within the period containing `as_of` apply entry-aware reduction only for entries
  dated on/before `as_of`. One canonical "balance as of date D."
- `app/services/calendar_service.py`: replace `_compute_month_end_balance` body with a call to
  `balance_as_of_date(account, scenario_id, month_end_date)`; delete the stale period-selection
  loop.
- `tests/test_services/test_calendar_service.py`: re-pin month-end balance assertions.

**D. Implementation approach** `balance_as_of_date` reuses `balances_for`'s period engine; the only
addition is the intra-period date cut for entry dates. Re-grep
`calendar_service.py:435,449-450,461-465,471-480`.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C9-1 | test_calendar_month_end_true_date | month-end mid-period | `balance_as_of_date` | balance at the actual month-end date, not last-period-end | New |
| C9-2 | test_calendar_entry_aware | Projected expense + cleared entries before month-end | calendar | entry-aware reduction applied | New |
| C9-3 | test_calendar_equals_resolver_at_period_boundary | month-end == period end | calendar vs balances_for | identical | New |
| C9-4 | test_calendar_entry_after_date_excluded | entry dated after month-end | `balance_as_of_date` | that entry not yet reflected | New |

Re-pinned tests (W-277/HIGH-02): calendar month-end values updated to the entries-aware true-date
value with finding-citing comments.

## F. Manual verification steps

1. Calendar "End Balance" for a month whose end falls mid-period equals the resolver's balance at
   that exact date.
2. `pytest tests/test_services/test_calendar_service.py -v`.

**G. Downstream effects** Calendar joins the single-source set. `balance_as_of_date` is reusable
(year-end snapshots, future features).

**H. Rollback notes** Restore `_compute_month_end_balance`; revert re-pins; delete
`balance_as_of_date` if unused elsewhere.

---

### Commit 10 -- period_subtotal through the canonical producer (Q-10 resolved, E-25)

**A. Commit message** `fix(grid): period_subtotal through canonical producer (Q-10, E-25)`

**B. Problem statement** F-002 Pair C / F-004 (UNKNOWN, blocked on Q-10): on one grid page the
subtotal row uses raw `txn.effective_amount` (grid inline ~`:263-279`) while the balance row uses
the entry-aware reduction -- a same-page divergence. The developer's locked answer (E-25) makes the
entries-aware producer canonical for the subtotal too; the subtotal is a shared financial concept,
not a grid-only detail.

## C. Files modified

- `app/services/balance_resolver.py`: `period_subtotal(account, scenario_id, period) -> Decimal`
  using the same entry-aware reduction and shared status predicate.
- `app/routes/grid.py`: replace the inline subtotal loop with `period_subtotal(...)`.
- `tests/test_routes/test_grid.py`: re-pin subtotal assertions; add the same-page relationship test.

Note: obligations monthly-equivalent aggregation is Commit 23 (E-24 / HIGH-05), not
`period_subtotal`. The `/obligations` route sums `amount_to_monthly(...)` across
`TransactionTemplate` / `TransferTemplate` rows (a monthly-equivalent rollup with no
`(account, scenario_id, period)` parameter set), so the canonical producer for that surface
is `obligations_aggregator.committed_monthly`, not `balance_resolver.period_subtotal`. F-4
documents the original plan-prose drift.

**D. Implementation approach** Q-10 resolution recorded in the commit body: subtotal is the
entry-aware sum of Projected items for the period, identical formula to the balance delta, so
`balance[p] - balance[p-1]` reconciles to `subtotal[p].net` by construction. Delete the grid inline
loop (no dead duplicate).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C10-1 | test_subtotal_entry_aware | Projected $500, cleared $462.34 | `period_subtotal` | `Decimal("37.66")` (entry-aware), not 500.00 | New |
| C10-2 | test_subtotal_reconciles_balance_delta | grid fixture | `balance[p]-balance[p-1]` vs `subtotal[p].net` | exactly equal | New |
| C10-3 | test_grid_inline_loop_removed | grep | no inline `sum(... effective_amount ...)` in grid.py | passes | New |

Re-pinned tests (F-002/F-004): grid subtotal assertions updated from raw-effective to entry-aware
with finding-citing comments.

## F. Manual verification steps

1. On a grid with a Projected envelope expense carrying cleared entries: footer subtotal + balance
   delta now agree.
2. `pytest tests/test_routes/test_grid.py -v`.

**G. Downstream effects** F-004 verdict moves from UNKNOWN to AGREE. Commit 11 includes subtotal in
the invariant.

**H. Rollback notes** Restore inline loops; revert re-pins.

---

### Commit 11 -- Cross-page balance-equality regression lock (HIGH-01)

**A. Commit message** `test(integration): cross-page balance-equality regression lock (HIGH-01)`

**B. Problem statement** HIGH-01 / R-6: the developer's two worst symptoms (#1, #5) have no
falsifying test. Three audit-mandated greps return zero matches. Without this lock the fixes in
Commits 5-10 can silently regress with the suite green.

## C. Files modified

- `tests/test_integration/test_cross_page_balance_equality.py` (new): the PT-01 fixture and
  invariant.
- `tests/conftest.py`: add a `seed_cross_page_account` fixture (account + anchor + one Projected
  envelope expense with cleared/uncleared/credit entries + one settled txn + a scenario), reusing
  existing fixtures (`seed_user`, `seed_periods_today`).

**D. Implementation approach** One fixture builds the exact symptom tuple. The test
renders/exercises every surface and asserts a single Decimal: grid current-period balance, /savings
checking tile, /accounts checking detail, dashboard balance card, net-worth per-account input,
calendar month-end (at a period boundary so it must match), and the period subtotal reconciliation.
All must equal the hand-computed value. The test is parameterized over a small matrix (zero anchor;
negative balance; credit-only entries; uncleared floor) so it locks the formula, not one number.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C11-1 | test_all_surfaces_equal_symptom_tuple | PT-01 | render 6 surfaces | all == `Decimal("160.00")` | New |
| C11-2 | test_all_surfaces_equal_zero_anchor | anchor 0.00 | 6 surfaces | all equal, no blank/omit | New |
| C11-3 | test_all_surfaces_equal_negative | overdraft | 6 surfaces | all equal negative Decimal | New |
| C11-4 | test_all_surfaces_equal_credit_only | credit entries only | 6 surfaces | all equal | New |
| C11-5 | test_subtotal_reconciles_on_all_pages | PT-01 | subtotal vs balance delta | equal everywhere | New |
| C11-6 | test_invariant_fails_if_seam_reintroduced | monkeypatch a consumer to skip the resolver | run invariant | test FAILS (proves it is a real lock) | New |

## F. Manual verification steps

1. `pytest tests/test_integration/test_cross_page_balance_equality.py -v` green.
2. Temporarily revert Commit 6 locally -> C11-1 fails (confirms the lock bites). Restore.

**G. Downstream effects** This invariant is permanent; every later balance-touching commit must keep
it green. It is the falsifying test the project lacked.

**H. Rollback notes** Delete the test file + fixture. (Not recommended -- this is the regression
anchor.)

### Commit 12 -- Append-only loan_anchor_events table + backfill (E-18 infrastructure)

**A. Commit message** `feat(loan): append-only loan_anchor_events table + backfill (E-18)`

**B. Problem statement** CRIT-02 / F-014: `loan_params.current_principal` has zero settle-driven
writers (grep-verified: `grep -rEn '\.current_principal\s*=[^=]' app/ scripts/` returns nothing but
the form edit). E-18 (decision D-A) makes the loan principal event-derived. This commit creates the
append-only event table and backfills it so the resolver (Commit 13) has a source.

## C. Files modified

- `app/models/loan_anchor_event.py` (new): `LoanAnchorEvent` -- `id`, `account_id` FK CASCADE NOT
  NULL, `anchor_date` Date NOT NULL, `anchor_balance` Numeric(12,2) NOT NULL CHECK >= 0, `source`
  Integer FK to a new ref enum (`origination`, `user_trueup`) NOT NULL, `created_at`
  (CreatedAtMixin), append-only (no update/delete in code; route only inserts). Unique index
  `uq_loan_anchor_events_acct_date_bal_day` mirroring the `AccountAnchorHistory` dedupe pattern.
- `app/enums.py` + `app/ref_seeds.py`: add `LoanAnchorSource` ref values (IDs for logic, strings for
  display -- rule 4).
- `app/audit_infrastructure.py`: add `loan_anchor_events` to `AUDITED_TABLES` (coding standard
  requires it; new `budget` table).
- `migrations/versions/<auto>_create_loan_anchor_events.py` (new): create table + ref seed; backfill
  -- for every existing loan account, insert one `origination` event
  `(origination_date, original_principal)` and, if `current_principal` differs from the
  confirmed-payment replay from origination, one `user_trueup` event `(today, current_principal)` so
  the resolver reproduces today's displayed principal exactly on first run (no visible jump).
  Three-step verified;
  `Review: solo developer, 2026-05-19 (audit financial_calculations CRIT-02/E-18, new audited table)`
  docstring line.
- `tests/test_models/test_loan_anchor_event.py`, `tests/test_models/test_loan_anchor_backfill.py`
  (new).

**D. Implementation approach** Backfill derivation is documented in the migration docstring and is
deterministic: origination event from immutable `LoanParams` fields; the optional trueup event
preserves the currently-displayed number so customers see no discontinuity when the resolver goes
live (Commit 15). After `python scripts/build_test_template.py` the audit-trigger count increases by
one; the entrypoint health check expects it (note in Commit 37 gate). Downgrade drops the table and
ref values (reversible; backfill is reproducible).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C12-1 | test_table_append_only_shape | -- | model metadata | NOT NULL cols, CHECK >= 0, unique index present | New |
| C12-2 | test_backfill_origination_event | existing loan | upgrade | one origination event = (origination_date, original_principal) | New |
| C12-3 | test_backfill_trueup_when_current_differs | current_principal != replay | upgrade | trueup event (today, current_principal) | New |
| C12-4 | test_backfill_no_trueup_when_consistent | current == replay | upgrade | no trueup event | New |
| C12-5 | test_audited_table_registered | -- | AUDITED_TABLES | contains loan_anchor_events | New |
| C12-6 | test_downgrade_drops_cleanly | post-upgrade | downgrade | table + ref values gone, no orphans | New |
| C12-7 | test_source_is_id_based | -- | source column | FK to ref, no string compare in code | New |

## F. Manual verification steps

1. `flask db upgrade` on prod-like clone; every loan account has >= 1 event; loans whose stored
   principal already matched replay have exactly one.
2. `flask db downgrade` then `upgrade` round-trips.
3. `python scripts/build_test_template.py`; entrypoint trigger-count check passes.

**G. Downstream effects** Commit 13 reads these events. `current_principal`/`interest_rate` still
authoritative until Commit 15 -- no display change yet.

**H. Rollback notes** `flask db downgrade` drops the table and ref rows. No other code depends on it
until Commit 13.

---

### Commit 13 -- Pure event-derived loan resolver (E-18 core; symptoms #2, #4)

**A. Commit message** `feat(loan): pure event-derived loan resolver (E-18)`

**B. Problem statement** CRIT-02 / F-013/F-015/F-026: there is no single owner of "this loan's
balance, monthly payment, schedule." 16+ sites assemble their own `(P, r, n)`; for an ARM the engine
reads the frozen stored principal and re-amortizes it over a calendar-shrinking `n`, so the payment
creeps a few dollars a month inside the fixed-rate window (verified $2,460.45 -> $2,463.28
hand-computation). E-18: one pure resolver derives the triple by replaying confirmed payments
forward from the latest `LoanAnchorEvent`, honoring the ARM fixed-rate window.

## C. Files modified

- `app/services/loan_resolver.py` (new):
  `resolve_loan(loan_params, anchor_events, payments, rate_changes, as_of) -> LoanState` where
  `LoanState` is a frozen dataclass
  `(current_balance, monthly_payment, schedule, payoff_date, total_interest)`. Pure: takes plain
  data, returns plain data, no DB/Flask (services boundary).
- Reuses existing infrastructure (refinement R-2): `amortization_engine.generate_schedule` (already
  accepts `payments`, `rate_changes`, `anchor_balance`, `anchor_date`),
  `PaymentRecord`/`RateChangeRecord`,
  `loan_payment_service.get_payment_history`/`load_loan_context`. The resolver is a thin pure
  consolidation that (a) picks the latest `LoanAnchorEvent` as `(anchor_balance, anchor_date)` for
  both ARM and fixed, (b) replays only confirmed payments forward from that anchor, (c) for an ARM
  inside `[origination, origination + arm_first_adjustment_months)` returns one payment computed
  once from the anchor balance over the remaining contractual term as of the anchor date and holds
  it constant for the whole window (the E-02 invariant), (d) uses `round_money` (Commit 1) as the
  only rounding boundary.
- `tests/test_services/test_loan_resolver.py` (new) with hand-computed expectations.

**D. Implementation approach** The ARM fixed-window fix is the crux. Pseudocode:

```text
anchor = latest LoanAnchorEvent (Commit 12 guarantees >= 1)
confirmed = [p for p in payments if p.is_confirmed and p.payment_date > anchor.anchor_date]
balance = replay(anchor.anchor_balance, confirmed, rate_schedule)   # full precision
if is_arm and anchor.anchor_date in fixed_window:
    n = contractual_term_months - months_between(origination, anchor.anchor_date)
    payment = round_money(amortize(anchor.anchor_balance, rate_at(anchor.anchor_date), n))
    # held constant for every as_of inside the window -- not recomputed per call
else:
    payment = round_money(amortize(balance, rate_at(as_of), remaining_months(as_of)))
schedule = generate_schedule(..., payments=confirmed, anchor_balance=anchor.anchor_balance,
                             anchor_date=anchor.anchor_date, rate_changes=rate_changes)
```

Re-grep `amortization_engine.py` for `calculate_monthly_payment`, `calculate_remaining_months`,
`generate_schedule`, the ARM `cur_balance = current_principal` line, and the
`original_principal is None` branch before editing. Do not rewrite `generate_schedule` from scratch
(rule 10); the resolver feeds it the anchor-derived inputs so the existing math is reused correctly.

**E. Test cases** (every monetary expectation carries the arithmetic in a comment)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C13-1 | test_arm_payment_constant_in_fixed_window | 5/5 ARM, $400k, 6% , 360mo, origination 2026-01-01, no rate change, no payments | `resolve_loan` at month 1..60 | identical `monthly_payment` every month (E-02); value = amortize(400000, .06/12, 360) hand-computed | New |
| C13-2 | test_arm_no_creep_month_24_vs_25 | same | resolve at 2027-12 vs 2028-01 | byte-identical (was 2460.45 vs 2463.28 pre-fix) | New |
| C13-3 | test_confirmed_payment_reduces_balance | $300k, one confirmed $1888.36 P&I after anchor | `resolve_loan` | balance = 300000 - (1888.36 - 300000*.005) = 299611.64 | New |
| C13-4 | test_projected_payment_not_replayed | one Projected (unconfirmed) payment | `resolve_loan` | balance unchanged (only confirmed replayed) | New |
| C13-5 | test_fixed_rate_replays_from_origination_anchor | fixed loan, 3 confirmed payments | `resolve_loan` | exact reduced balance, hand-computed | New |
| C13-6 | test_anchor_trueup_resets_replay | user_trueup event after N payments | `resolve_loan` | replay starts from trueup, ignores pre-trueup payments | New |
| C13-7 | test_rate_change_after_window_applied | ARM, rate change at month 61 | `resolve_loan` at 62 | payment re-amortized at new rate | New |
| C13-8 | test_resolver_is_pure | -- | ast/import scan | no flask/db.session in module | New |
| C13-9 | test_payment_uses_round_money | -- | grep | rounding via round_money only | New |
| C13-10 | test_zero_rate_loan | 0% loan | `resolve_loan` | payment = principal / n, no div-by-zero | New |
| C13-11 | test_payoff_date_and_total_interest | known schedule | `resolve_loan` | hand-computed payoff_date + total_interest | New |

## F. Manual verification steps

1. With account-3-like ARM params, call `resolve_loan` for 12 consecutive months inside the fixed
   window -> the payment is one value (paste the 12 identical Decimals).
2. Hand-compute `amortize(P, r, n)` for the window and confirm it equals the resolver output.
3. `pytest tests/test_services/test_loan_resolver.py -v`.

**G. Downstream effects** Nothing displays it yet (Commit 15 routes consumers). Commit 14 proves the
settle behavior; Commit 17 collapses the remaining loan figures onto it.

**H. Rollback notes** Delete `loan_resolver.py` + tests. `amortization_engine` untouched (reused,
not modified beyond re-grep reading), so revert is local.

---

### Commit 14 -- Settled transfer reduces resolved principal (symptom #3)

**A. Commit message** `test(loan): settled transfer reduces resolved principal (symptom #3)`

**B. Problem statement** Symptom #3: the developer expects a confirmed transfer to the mortgage to
reduce the principal. With the resolver (Commit 13) deriving on read from confirmed payments, this
is now true by construction -- but it is the single most important behavioral promise and must be
locked with an end-to-end test, not assumed.

## C. Files modified

- `tests/test_integration/test_loan_principal_settles.py` (new): end-to-end -- create loan account +
  monthly PITI transfer; settle it (status -> RECEIVED on the loan-side shadow income, the real
  settle path verified in `transfer_service`/`transactions.py`); assert the resolver's
  `current_balance` decreased by exactly the principal portion and escrow/interest did not reduce
  principal (E-01 split invariant).
- No production code change expected; if the test reveals the confirmed-payment feed misses settled
  shadows, that fix is in scope here (root cause, not band-aid).

**D. Implementation approach** Drive the real settle path (`transactions.py` mark-done -> RECEIVED
for income shadow), not a hand-set status, so the test proves the actual user workflow. Assert E-01:
checking balance dropped by full P&I&escrow; loan principal dropped by principal portion only.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C14-1 | test_settled_transfer_reduces_principal | loan + PITI transfer | settle via real path | resolver balance -= principal portion (hand-computed) | New |
| C14-2 | test_escrow_interest_do_not_reduce_principal | PITI w/ escrow | settle | principal reduced by P only; E-01 | New |
| C14-3 | test_unsettled_transfer_no_reduction | projected transfer | (none) | balance unchanged | New |
| C14-4 | test_multiple_settlements_cumulative | 3 settled | settle x3 | balance reduced by sum of principal portions | New |
| C14-5 | test_loan_card_reflects_after_settle | settle then load /accounts loan card | render | card shows reduced principal (depends on Commit 15 order; if run before 15, assert resolver value and mark xfail-free by ordering after 15) | New |

Note: C14-5 depends on Commit 15; sequence Commit 14 after 13 and ensure C14-5 lands with 15 if
display routing is required for it. Keep the commit atomic by asserting resolver output here and the
card in Commit 15's tests.

## F. Manual verification steps

1. Create a mortgage, add a monthly transfer, mark it received; the resolver principal drops by the
   principal portion.
2. `pytest tests/test_integration/test_loan_principal_settles.py -v`.

**G. Downstream effects** Locks symptom #3 permanently. Any future change that breaks
settle->principal fails here.

**H. Rollback notes** Delete the test file (not recommended). Any production fix discovered here is
reverted with its own diff.

---

### Commit 15 -- Demote stored columns; route all loan consumers through the resolver (E-18; symptom #5 loan facet, HIGH-08 partial)

**A. Commit message**
`refactor(loan): demote current_principal/interest_rate; route all consumers (E-18)`

**B. Problem statement** F-008/F-015/F-016/symptom #5 loan facet: three sources for one loan's
displayed balance (STORED at /accounts loan card, engine-walked at /savings, schedule at net-worth).
E-18 (D-A): the resolver is the only source; `current_principal`/`interest_rate` become nullable
non-authoritative seed, never read for display.

## C. Files modified

- `app/models/loan_params.py`: `current_principal`, `interest_rate` -> `nullable=True`;
  docstring/comment "non-authoritative seed; resolver is source of truth (E-18)". Keep CHECK
  constraints valid for nullable. Migration: `alter_column ... nullable=True` only (additive,
  reversible; no drop -- OPT-1 is the optional later drop). `Review:` line (type/constraint change).
- `app/routes/loan.py` (dashboard card ~`:104`/`:553-557`,
  payoff/refinance/create_payment_transfer), `app/routes/debt_strategy.py` (ARM
  `_compute_real_principal` ~`:169-173`), `app/services/savings_dashboard_service.py` (debt card
  ~`:840,855`), `app/services/year_end_summary_service.py` (net-worth schedule ~`:2079-2081`): all
  read `loan_resolver.resolve_loan(...)` instead of the stored column / ad-hoc projection.
- `tests/test_routes/test_loan.py`, `test_debt_strategy.py`,
  `test_services/test_savings_dashboard_service.py`,
  `test_services/test_year_end_summary_service.py`: re-pin loan principal/payment assertions.

**D. Implementation approach** After this commit `grep -rn "\.current_principal" app/` shows only
the resolver/backfill and the (now write-only at true-up, Commit 16) seed path -- no display read.
The loan card, debt strategy, savings debt card and net-worth liability all show the resolver's
`current_balance`, so symptom #5's three-base divergence collapses to one. Re-grep every cited line;
verification confirmed several drifted.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C15-1 | test_loan_card_equals_savings_equals_networth | fixed loan + confirmed payments | render 3 surfaces | identical resolver balance | New |
| C15-2 | test_arm_card_equals_everywhere | ARM in window | 3 surfaces | identical, constant payment | New |
| C15-3 | test_no_display_read_of_current_principal | grep | no `.current_principal` in display paths | passes | New |
| C15-4 | test_column_nullable_after_migration | -- | schema | both columns nullable | New |
| C15-5 | test_downgrade_restores_not_null | post-up | downgrade | columns NOT NULL again (data retained) | New |
| C15-6 | test_loan_card_reflects_settle (was C14-5) | settle then card | render | reduced principal shown | New |

Re-pinned tests (F-008/F-015/F-016/symptom #5): loan principal/payment assertions across the four
files updated to the resolver value with finding-citing comments + arithmetic.

## F. Manual verification steps

1. Fixed loan with confirmed payments: /accounts loan card == /savings debt card == /savings account
   card == net-worth liability.
2. ARM in window: same, and the payment equals Commit 13's hand-computed constant.
3. `pytest tests/test_routes/test_loan.py tests/test_routes/test_debt_strategy.py tests/test_services/test_savings_dashboard_service.py tests/test_services/test_year_end_summary_service.py -v`.

**G. Downstream effects** Symptom #5 fully closed (checking facet Commits 6-8, loan facet here).
Commit 16 makes the edit UX consistent; Commit 17 collapses residual loan figures. OPT-1 (drop the
demoted columns) becomes safe after a production cycle.

**H. Rollback notes** `flask db downgrade` restores NOT NULL; revert route/service reads and
re-pins. The columns still hold their seed values so reverting display reads is safe.

---

### Commit 16 -- Principal edit becomes a dated balance true-up event (E-18 UX, decision D-C)

**A. Commit message** `feat(loan): principal edit becomes dated balance true-up event (E-18 UX)`

**B. Problem statement** Under E-18 the "Current Principal" scalar input is no longer authoritative.
Per decision D-C the control becomes "record loan balance as of date D," appending a
`LoanAnchorEvent` -- identical UX to the existing checking-account true-up, so the mental model is
consistent across account types (DRY in UX too).

## C. Files modified

- `app/services/anchor_service.py` (MODIFIED, not new -- pre-extracted by R-7, commit `d551f9c`):
  extend to handle loan anchors.  Two viable shapes -- (a) parameterise `apply_anchor_true_up`
  with an `account_kind` discriminant + the appropriate unique-index name, or (b) add a sibling
  `apply_loan_anchor_true_up` that delegates to a private `_apply_true_up_core` helper shared with
  the checking path.  Either preserves the single try/except shape; choose the one with the
  smaller call-site surface at commit time.
- `app/routes/loan.py`: replace the `_PARAM_FIELDS` `current_principal` setattr path (re-grep
  `:668-674`) with an action that validates and calls the (extended) `anchor_service` helper.  The
  helper appends a `user_trueup` `LoanAnchorEvent` (append-only; never UPDATE/DELETE) and returns
  the same `AnchorTrueUpOutcome` enum the checking routes consume.  `interest_rate` edits continue
  to flow to `RateHistory` (already the rate-change path).
- `app/schemas/validation.py`: a Marshmallow schema for the true-up (date required, balance Decimal
  >= 0, date not in the future, not before origination).
- `app/templates/loan/dashboard.html`: replace the bare principal input with the dated true-up form
  mirroring the checking true-up partial (reuse the existing checking true-up template/markup
  pattern -- do not duplicate).
- `tests/test_services/test_anchor_service.py`: extend with loan-anchor cases (mirroring the
  existing eight checking-anchor cases:  COMMITTED, STALE_CONFLICT, DUPLICATE_SAME_DAY,
  same-day-different-balance, non-F-103 re-raise, plus the unique-index constant pin).
- `tests/test_routes/test_loan.py`: true-up route tests (UX-level: response shape, template
  rendering, HX headers).

**D. Implementation approach** EXTEND `anchor_service.apply_anchor_true_up` rather than mirror the
checking route shape inline (R-7 / commit `d551f9c` did the consolidation pre-emptively so this
commit lands a third caller without a third inline try/except copy).  Reuse the checking true-up
form structure and validation idioms (IDs not strings, CSRF, POST, partial response).  The loan
resolver (Commit 13) already consumes the latest event, so a new true-up immediately changes every
loan surface consistently.  The C-17 optimistic lock + F-103 / C-22 same-day same-balance
idempotency on the loan anchor history must match the checking semantics exactly; the helper's
existing branches are the contract.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C16-1 | test_trueup_appends_event | loan | POST true-up (date,balance) | new LoanAnchorEvent row; no UPDATE of prior | New |
| C16-2 | test_trueup_changes_all_surfaces | loan | true-up then render card/savings/networth | all reflect new anchor | New |
| C16-3 | test_trueup_rejects_future_date | -- | POST date>today | 422 validation | New |
| C16-4 | test_trueup_rejects_pre_origination | -- | POST date<origination | 422 | New |
| C16-5 | test_trueup_rejects_negative_balance | -- | POST balance<0 | 422 | New |
| C16-6 | test_trueup_is_append_only | 2 true-ups | -- | 2 events, prior untouched | New |
| C16-7 | test_trueup_csrf_required | no token | POST | rejected | New |

## F. Manual verification steps

1. /accounts/<id>/loan now shows a dated true-up form like the checking one; submit a balance as of
   a date; the card, /savings and net worth all update to a replay-from-that-date value.
2. Confirm no prior event row was mutated.
3. `pytest tests/test_routes/test_loan.py -v`; dark mode + mobile breakpoints render the form.

**G. Downstream effects** The loan principal lifecycle is now fully append-only and consistent with
checking. OPT-1 (drop demoted columns) is unblocked.

**H. Rollback notes** Restore the scalar setattr path + old template; the resolver still works (it
would just read origination + earlier true-ups). Reversible.

---

### Commit 17 -- Unify per-period/interest/payoff figures via the resolver + round_money (HIGH-08)

**A. Commit message**
`fix(loan): unify per-period/interest/payoff figures via resolver+round_money (HIGH-08)`

**B. Problem statement** HIGH-08 / F-017..F-023: six loan/debt figures still diverge across surfaces
(per-period principal, per-period interest, total_interest life-vs-calendar-vs-strategy,
interest_saved banker's-vs-half-up, months_saved four quantities, ARM payoff_date). All are
downstream of CRIT-02 and the missing rounding helper.

## C. Files modified

- `app/services/debt_strategy.py`, `app/routes/loan.py` (payoff/refinance ~`:957-968`),
  `app/services/year_end_summary_service.py` (mortgage interest aggregation),
  `app/services/loan_payment_service.py`: every per-period principal/interest, total_interest,
  interest_saved, months_saved, payoff_date comes from `loan_resolver.resolve_loan(...)` and rounds
  via `round_money` (Commit 1) -- replacing the bare `.quantize(Decimal("0.01"))` banker's sites.
- Re-pin affected tests in the corresponding test files with hand-computed values.

**D. Implementation approach** One definition each: total_interest = sum of schedule interest from
the resolver (life-of-loan); calendar-year and strategy-base views are explicit derived projections
of that one schedule, labeled, not separate computations. interest_saved/months_saved computed from
the resolver's two schedules (with/without extra payment). Every bare quantize in these paths ->
`round_money`; prove cents per site with a hand-computed test (R-5).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C17-1 | test_per_period_principal_single_source | known loan | resolver vs debt-strategy vs year-end | identical per period | New |
| C17-2 | test_total_interest_one_definition | known loan | all surfaces | one life-of-loan figure; calendar view = labeled subset | New |
| C17-3 | test_interest_saved_half_up | extra payment | interest_saved | round_money value (was banker's half-cent off) | New |
| C17-4 | test_months_saved_single_quantity | extra payment | months_saved | one hand-computed integer | New |
| C17-5 | test_arm_payoff_date_consistent | ARM | payoff_date all surfaces | identical | New |
| C17-6 | test_no_bare_quantize_in_loan_paths | grep | no `.quantize(Decimal("0.01"))` without rounding= in these files | passes | New |

Re-pinned tests (F-017..F-023/HIGH-08): updated to single-source hand-computed values with
finding-citing comments.

## F. Manual verification steps

1. Loan dashboard, debt strategy and year-end mortgage interest show the same per-period and total
   figures.
2. `pytest tests/test_services/test_debt_strategy.py tests/test_routes/test_loan.py tests/test_services/test_year_end_summary_service.py tests/test_services/test_loan_payment_service.py -v`.

**G. Downstream effects** The loan concept family is fully single-sourced. The cross-page lock
(Commit 11) can be extended with a loan-surface assertion (add here).

**H. Rollback notes** Revert each surface to its prior computation and re-pins; resolver remains for
the card.

### Commit 18 -- Enforce the SS wage-base cap on the calibration path (CRIT-03)

**A. Commit message** `fix(tax): enforce SS wage-base cap on calibration path (CRIT-03)`

**B. Problem statement** CRIT-03: Social Security tax legally stops at the annual wage base. The
bracket path enforces it (`tax_calculator.calculate_fica`,
`if cumulative >= ss_wage_base: ss_tax = ZERO`, verified ~`:300-306`); the calibration path
(`calibration_service.apply_calibration`, verified `:106`) has no cumulative-wages parameter and
never references the wage base, so a high earner who calibrates from a pay stub is over-charged SS
every period after the cap. Worked example confirmed: $312,000 salary -> +$7,905/yr FICA
overstatement.

## C. Files modified

- `app/services/calibration_service.py`: add `cumulative_wages: Decimal` and the FICA config (or
  `ss_wage_base`) to `apply_calibration`; compute the SS line with the same cap logic as the bracket
  path -- by delegating the SS portion to one shared capped-SS helper so the two paths cannot drift
  again (DRY; root cause, not a parallel patch).
- `app/services/paycheck_calculator.py`: the calibration gate
  (~~:160-173`) must pass the cumulative wages it already computes for the bracket branch ~~`:207`)
  into `apply_calibration` (re-grep; the bracket branch already calls `_get_cumulative_wages`).
- `tests/test_services/test_calibration_service.py`,
  `tests/test_services/test_paycheck_calculator.py`: hand-computed cap tests.

**D. Implementation approach** Extract the SS cap into a single function used by both
`calculate_fica` and `apply_calibration` (e.g.
`tax_calculator.capped_social_security(gross, cumulative, fica_config) -> Decimal`). The calibration
path keeps its calibrated effective rate for federal/state/medicare but the SS line is the capped
figure. This removes the divergence at the root (one SS definition) rather than copying the cap into
calibration.

**E. Test cases** (arithmetic in comments)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C18-1 | test_calibration_ss_capped_after_base | $312k salary, period crossing base | `apply_calibration` w/ cumulative | SS = 0 once cumulative >= base; partial in the crossing period | New |
| C18-2 | test_calibration_ss_uncapped_before_base | early period | `apply_calibration` | gross*ss_rate (unchanged) | New |
| C18-3 | test_calibration_year_total_matches_bracket_ss | full year both paths | sum SS | calibration year SS == bracket year SS (was +$7,905) | New |
| C18-4 | test_shared_helper_single_definition | grep | one capped-SS function, two callers | passes | New |
| C18-5 | test_partial_period_at_crossing | cumulative+gross straddles base | helper | ss = (base-cumulative)*rate, hand-computed | New |
| C18-6 | test_low_earner_unaffected | $60k | both paths | identical to before (no regression) | New |

Re-pinned tests (CRIT-03/F-037): any calibration test that asserted the uncapped year SS is
corrected to the capped value with the IRS-invariant citation and arithmetic.

## F. Manual verification steps

1. Calibrate a $312k profile from a pay stub; the FICA line zeroes after the wage base; net pay
   rises correctly post-cap.
2. `pytest tests/test_services/test_calibration_service.py tests/test_services/test_paycheck_calculator.py -v`.

**G. Downstream effects** Net pay and FICA on every calibrated paycheck for high earners are now
correct. Commit 19 builds the immutable-snapshot guarantee on the same path.

**H. Rollback notes** Revert the signature + gate; the shared helper can stay (harmless) or be
inlined back. Re-pins reverted documents the regression.

---

### Commit 19 -- Calibration is an immutable pay-stub snapshot; server-derive rates at confirm (E-20, HIGH-03)

**A. Commit message** `fix(calibration): server-derive effective rates at confirm (E-20)`

**B. Problem statement** HIGH-03 / Q-25: `calibrate_confirm` stores the four effective rates
straight from posted hidden form fields, never re-deriving them server-side, with no cross-check
that `rate == actual / base`; editing deductions/salary afterward never recomputes them. E-20:
calibration is an immutable pay-stub-grounded snapshot -- the confirm step re-derives the rates
server-side from the stored `actual_*` plus the taxable base and never trusts posted rate fields.

## C. Files modified

- `app/routes/salary.py`: `calibrate_confirm` (re-grep ~`:1130-1164`) recomputes the four rates via
  `calibration_service.derive_effective_rates(...)` from the stored `actual_*` and base; ignores any
  posted rate fields.
- `app/schemas/validation.py`: confirm schema validates the stored rate pair is consistent with the
  stored `actual_*` pair (within a one-cent tolerance), rejecting inconsistent input instead of
  range-only checks (~`:1827`).
- `tests/test_routes/test_salary.py`, `tests/test_services/test_calibration_service.py`.

**D. Implementation approach** The single derivation lives in
`calibration_service.derive_effective_rates` (already exists, `:34`); the confirm route calls it
rather than trusting the browser. The schema enforces the snapshot invariant
`effective_x == round_money(actual_x) / base` so a tampered or stale post is a 422, not silent bad
withholding.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C19-1 | test_confirm_rederives_server_side | post mismatched rate fields | calibrate_confirm | stored rates = server-derived, posted ignored | New |
| C19-2 | test_confirm_rejects_inconsistent_pair | actual/base implies 0.21, post 0.05 | confirm | 422 | New |
| C19-3 | test_snapshot_immutable_on_profile_edit | confirm, then edit deductions | reload | stored snapshot unchanged (immutable) | New |
| C19-4 | test_rate_consistency_invariant | valid stub | confirm | effective == actual/base within 0.01 | New |
| C19-5 | test_calibration_then_paycheck_uses_snapshot | confirm then project | paycheck | uses snapshot rates incl. capped SS (Commit 18) | New |

Re-pinned tests (HIGH-03/Q-25): assertions trusting posted rates updated to server-derived values
with citations.

## F. Manual verification steps

1. Calibrate, tamper a hidden rate field via devtools, submit -> server ignores it (or 422 on
   inconsistency).
2. Edit pre-tax deductions after calibrating -> stored snapshot does not silently change.
3. `pytest tests/test_routes/test_salary.py tests/test_services/test_calibration_service.py -v`.

**G. Downstream effects** Calibrated withholding is now trustworthy and stable. Q-25 resolved under
E-20.

**H. Rollback notes** Revert confirm to posted-field storage + range-only schema. Reversible.

---

### Commit 20 -- Retirement: zero is a value, not "missing" (E-12, CRIT-04)

**A. Commit message** `fix(retirement): zero is a value not missing (E-12, CRIT-04)`

**B. Problem statement** CRIT-04: `retirement_dashboard_service` resolves the SWR with `or "0.04"`
truthiness (verified ~~:217-221`) while the slider uses `is None` ~~`:303-309`), so an explicit
`safe_withdrawal_rate = 0.0000` shows 0.00% on the slider but the projection uses 4% -> phantom
$4,000/mo on a $1.2M balance. Separately `if params and params.assumed_annual_return:` (~`:321`)
drops a zero-return account from the weighted average (two $100k accounts at 0% and 7% display 7.00%
instead of 3.50%).

## C. Files modified

- `app/services/retirement_dashboard_service.py`: SWR resolution uses `is None` semantics and the
  existing named constant `_DEFAULT_SWR_PCT` (verified to exist, `:54`) consistently in both
  `compute_gap_data` and `compute_slider_defaults`; the weighted-return loop uses
  `params is not None and params.assumed_annual_return is not None` (zero honored).
- `tests/test_services/test_retirement_dashboard_service.py`, `test_retirement_gap_calculator.py`.

**D. Implementation approach** One SWR resolver helper used by both call sites (DRY -- removes the
`is None` vs `or` split entirely, not just patches one side). All money/rate "missing vs zero"
checks become explicit `is None` (coding standard: never truthiness on financial values).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C20-1 | test_explicit_zero_swr_no_phantom_income | swr=0.0000, balance $1.2M | compute_gap_data | projected income $0.00, slider 0.00% (consistent) | New |
| C20-2 | test_none_swr_uses_default | swr=None | both | `_DEFAULT_SWR_PCT` on both surfaces | New |
| C20-3 | test_zero_return_account_in_weighted_avg | $100k@0% + $100k@7% | weighted return | 3.50% (was 7.00%) | New |
| C20-4 | test_none_return_excluded_zero_included | one None, one 0% | weighted | None skipped, 0% included | New |
| C20-5 | test_swr_resolver_single_definition | grep | one resolver, no `or "0.04"` | passes | New |

Re-pinned tests (CRIT-04/F-042/PA-04/PA-05): phantom-income and weighted-return assertions corrected
with arithmetic.

## F. Manual verification steps

1. Set SWR to 0% explicitly: slider 0.00% and projected retirement income $0.00 agree.
2. Two equal accounts at 0% and 7%: blended return shows 3.50%.
3. `pytest tests/test_services/test_retirement_dashboard_service.py tests/test_services/test_retirement_gap_calculator.py -v`.

**G. Downstream effects** Retirement income/gap/sliders are internally consistent. Pattern (no
truthiness on money) reused in Commit 24.

**H. Rollback notes** Revert to truthiness resolution + re-pins. Reversible.

---

### Commit 21 -- Semantic is_settled hard-delete guard (E-22, CRIT-05)

**A. Commit message** `fix(templates): semantic is_settled hard-delete guard (E-22, CRIT-05)`

**B. Problem statement** CRIT-05 (data loss): `archive_helpers.template_has_paid_history` /
`transfer_template_has_paid_history` enumerate only `[DONE, SETTLED]` and omit RECEIVED, which is
`is_settled=True, is_immutable=True` (verified `ref_seeds.py`). `hard_delete_template` then
bulk-deletes every linked transaction unconditionally, irreversibly destroying received-income
history while telling the user it was "permanently deleted." E-22: guard on the semantic
`is_settled` boolean; constrain the destructive delete to non-settled rows.

## C. Files modified

- `app/utils/archive_helpers.py`: both predicates use `Status.is_settled.is_(True)` (semantic
  boolean, IDs/columns not name strings) instead of the `[DONE, SETTLED]` ID list -- this
  automatically covers RECEIVED and any future settled status.
- `app/routes/templates.py`: `hard_delete_template` (re-grep ~`:561-636`) restricts the bulk delete
  to non-settled rows; if any settled row exists it archives instead and the flash message is
  accurate.
- `tests/test_routes/test_templates.py`, `tests/test_utils/test_archive_helpers.py`.

**D. Implementation approach** Root cause is "enumerated statuses instead of the semantic boolean"
-- fixing the boolean fixes every current and future settled status at once (DRY/SOLID; the project
already has the `is_settled` column for exactly this). The destructive delete is constrained even if
a guard is somehow bypassed (defense in depth for an irreversible operation).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C21-1 | test_received_income_blocks_hard_delete | income template w/ RECEIVED txn | hard_delete | archived, not deleted; data intact | New |
| C21-2 | test_settled_blocks_hard_delete | SETTLED txn | hard_delete | archived | New (regression-keep) |
| C21-3 | test_done_blocks_hard_delete | DONE txn | hard_delete | archived | New (regression-keep) |
| C21-4 | test_projected_only_allows_delete | only Projected | hard_delete | deleted (intended) | New |
| C21-5 | test_bulk_delete_skips_settled_rows | mixed Projected+RECEIVED | hard_delete path | settled rows never deleted even if reached | New |
| C21-6 | test_transfer_template_received_guard | transfer template w/ RECEIVED shadow | hard_delete | archived | New |
| C21-7 | test_predicate_uses_is_settled_not_ids | grep | no `[DONE, SETTLED]` ID list | passes | New |
| C21-8 | test_flash_message_accurate | RECEIVED present | hard_delete | message says archived, not "permanently deleted" | New |

## F. Manual verification steps

1. Create a recurring income template, mark a paycheck received, attempt permanent delete -> it is
   archived, the RECEIVED transaction still exists.
2. `pytest tests/test_routes/test_templates.py tests/test_utils/test_archive_helpers.py -v`.

**G. Downstream effects** The irreversible data-loss path is closed for all settled statuses. Commit
22 (optional) scans for pre-fix damage.

**H. Rollback notes** Revert predicates + route guard. Reversible. (Data destroyed before this
commit cannot be recovered -- the reason this is CRITICAL.)

---

### Commit 22 -- Read-only scan for pre-fix destroyed RECEIVED history (OPT-2; optional)

**A. Commit message** `chore(audit): read-only scan for pre-fix destroyed RECEIVED history (OPT-2)`

**B. Problem statement** CRIT-05 destroyed data before Commit 21 cannot be restored, but the
developer should know the blast radius. This is an opt-in, strictly read-only diagnostic. Listed as
optional per the developer's "list opportunities as options" instruction.

## C. Files modified

- `scripts/scan_destroyed_received_history.py` (new): read-only; cross-references `system.audit_log`
  DELETE rows on `budget.transactions` whose template guard would today block deletion, reporting
  affected templates/periods/amounts. No mutation; respects the audit-role read constraints.
- `tests/test_scripts/test_scan_destroyed_received_history.py`.

**D. Implementation approach** Uses the tamper-resistant `system.audit_log` (the project's forensic
record) to reconstruct what was deleted. Output is a report only. Idempotent, no `--force`, prints
`[set via environment]` style for any sensitive value (script standards).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C22-1 | test_scan_reports_known_deletion | seed an audit DELETE row | run scan | report lists it | New |
| C22-2 | test_scan_read_only | -- | run scan | zero writes (audit log unchanged) | New |
| C22-3 | test_scan_empty_when_clean | no deletions | run scan | empty report, exit 0 | New |

## F. Manual verification steps

1. `python scripts/scan_destroyed_received_history.py` prints a report; `system.audit_log` row count
   unchanged before/after.

**G. Downstream effects** None (diagnostic only). Informs whether manual data reconstruction is
warranted.

**H. Rollback notes** Delete the script + test. Nothing depends on it.

### Commit 23 -- One monthly-equivalent obligations aggregator (E-24, HIGH-05)

**A. Commit message** `refactor(obligations): one monthly-equivalent aggregator (E-24, HIGH-05)`

**B. Problem statement** HIGH-05 / D6-05: "total committed monthly" is computed by four
near-identical loops; only the three `/obligations` loops skip a template whose recurrence
`end_date` is in the past. `savings_goal_service.compute_committed_monthly` (~`:287-328`) lacks the
`end_date < today` guard, so an expired recurring expense/transfer inflates the emergency-fund
baseline and every per-goal contribution floor forever. The 26/12 factor is named once and
re-inlined as a literal at three sites.

## C. Files modified

- `app/services/obligations_aggregator.py` (new) or a single function in an existing service:
  `committed_monthly(user_id, scenario_id, as_of) -> Decimal` applying the shared filter (skip ONCE
  pattern, skip `rule.end_date is not None and rule.end_date < as_of`).
- `app/routes/obligations.py` (re-grep `:335,:358,:380`) and `app/services/savings_goal_service.py`
  (`compute_committed_monthly`) call the one aggregator.
- Named constants `PAY_PERIODS_PER_YEAR = Decimal("26")`, `MONTHS_PER_YEAR = Decimal("12")` defined
  once (re-grep the four 26/12 sites: `savings_goal_service.py:17`, `:169`,
  `savings_dashboard_service.py:171,765`, `retirement_gap_calculator.py:69`) and imported, never
  re-inlined.
- `tests/test_services/test_obligations_aggregator.py`, plus re-pins in savings/obligations tests.

**D. Implementation approach** One function, one filter, one factor. The expired-template guard is
now applied to every consumer including the emergency-fund baseline. Re-pin only the savings figures
that were inflated by the missing guard, with the finding citation and arithmetic.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C23-1 | test_expired_template_excluded | expired recurring expense | `committed_monthly` | excluded (was inflating) | New |
| C23-2 | test_active_template_included | active | aggregator | included | New |
| C23-3 | test_once_pattern_counted_once | ONCE rule | aggregator | counted once | New |
| C23-4 | test_obligations_and_savings_agree | same user | /obligations vs savings baseline | identical | New |
| C23-5 | test_factor_single_definition | grep | one 26 and one 12 constant, imported | passes | New |
| C23-6 | test_emergency_fund_baseline_excludes_expired | expired template | savings emergency fund | not inflated (hand-computed) | New |

Re-pinned tests (HIGH-05/D6-05): emergency-fund and per-goal floor assertions corrected.

## F. Manual verification steps

1. Add then expire a recurring expense; the /savings emergency-fund baseline drops accordingly and
   equals /obligations.
2. `pytest tests/test_services/test_obligations_aggregator.py tests/test_services/test_savings_goal_service.py tests/test_routes/test_obligations.py -v`.

**G. Downstream effects** Emergency-fund and contribution-floor figures are consistent and correct.

**H. Rollback notes** Revert callers to local loops; remove new module; revert re-pins.

---

### Commit 24 -- Reconcile Marshmallow domains with DB CHECK (E-28, HIGH-06)

**A. Commit message** `fix(schema): reconcile Marshmallow domains with DB CHECK (E-28, HIGH-06)`

**B. Problem statement** HIGH-06: stored rate/threshold columns disagree with their DB CHECK or
behave wrong on blank/zero. `trend_alert_threshold` Marshmallow `Range(min=1,max=100)` vs DB
`CHECK(0..1)` (unwritable field); rate fields validated 0-100 but stored 0-1; `apy` blank first save
silently inherits the 4.5% `server_default`; stored `annual_contribution_limit = 0` means three
things; `assumed_annual_return` Python `default=0.07000` is a float literal.

## C. Files modified

- `app/schemas/validation.py`: align each Marshmallow domain with the DB CHECK (fraction vs
  percentage decided per column and documented).
- `app/models/interest_params.py`: require/normalize `apy` so first save cannot silently inherit the
  default (explicit validation, no silent server_default for a financial rate); construct any
  Decimal default from string.
- `app/models/investment_params.py`: `assumed_annual_return` default constructed from string
  (`Decimal("0.07000")`), and `annual_contribution_limit` 0-vs-NULL normalized to one meaning across
  the three consumers (re-grep `investment.py:231,:305,:667` vs `growth_engine.py:206`); apply the
  E-12 "zero is a value" rule.
- `migrations/versions/<auto>_reconcile_check_domains.py`: where a CHECK must change
  (drop-and-recreate is destructive -> `Review:` line + explicit approval + working downgrade or
  `NotImplementedError` with manual SQL).
- `tests/test_schemas/`, `tests/test_models/`.

**D. Implementation approach** One DB-enforced domain per column, schema mirrors it exactly (coding
standard: range validation must match between schema and DB, no gaps). `apy` and
`assumed_annual_return` follow the E-12 rule (explicit None handling, never truthiness;
string-constructed Decimals). Each CHECK change is reviewed and has a working downgrade.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C24-1 | test_trend_threshold_writable | valid value in reconciled domain | save | persists; schema and CHECK agree | New |
| C24-2 | test_rate_field_domain_matches_check | boundary values | schema vs DB | identical accept/reject | New |
| C24-3 | test_apy_blank_first_save_rejected_or_explicit | omit apy | save | no silent 4.5%; explicit error or required | New |
| C24-4 | test_contribution_limit_zero_one_meaning | limit=0 | three consumers | one consistent behavior (E-12) | New |
| C24-5 | test_assumed_return_default_is_decimal_string | -- | model default | `Decimal("0.07000")`, not float | New |
| C24-6 | test_check_migration_downgrade | post-up | downgrade | reversible or documented NotImplementedError | New |

Re-pinned tests (HIGH-06/PA-01/PA-02): any test asserting the silent-default or mismatched domain
corrected.

## F. Manual verification steps

1. Save `trend_alert_threshold` -> persists (was impossible).
2. First-time interest account without apy -> no silent 4.5% projection.
3. `pytest tests/test_schemas tests/test_models -v`; migration up/down both directions.

**G. Downstream effects** Stored numeric domains are internally consistent; no silent rate defaults.

**H. Rollback notes** `flask db downgrade` per the migration's documented reversal; revert
schema/model edits.

---

### Commit 25 -- Unify employer-match across card, chart, year-end (HIGH-07)

**A. Commit message** `fix(investment): unify employer-match across card/chart/year-end (HIGH-07)`

**B. Problem statement** HIGH-07 / F-043/F-055: the dashboard "Employer contribution per period"
card calls `calculate_employer_contribution` with the uncapped periodic contribution while the
growth chart's employer line and `year_summary_employer_total` feed the limit-capped contribution.
Near the annual limit the card overstates the match (worked example: card
$240 vs chart/year-end $100).

## C. Files modified

- `app/routes/investment.py` (re-grep `:183,:187-189`): pass the limit-capped contribution to
  `calculate_employer_contribution` so all three surfaces read one value.
- `tests/test_routes/test_investment.py`, `tests/test_services/test_growth_engine.py`,
  `tests/test_services/test_year_end_summary_service.py`.

**D. Implementation approach** `growth_engine.calculate_employer_contribution` (`:91`) stays the
single producer; the only fix is feeding it the capped contribution at the card site so it matches
the chart/year-end. The capping logic already exists in the engine (`:258-265`); the card must not
bypass it.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C25-1 | test_card_equals_chart_employer | account near limit | card vs chart | identical capped value (was 240 vs 100) | New |
| C25-2 | test_card_equals_year_end_total | full year | card per-period sum vs year_summary_employer_total | consistent | New |
| C25-3 | test_below_limit_unchanged | well below limit | card | unchanged (no regression) | New |
| C25-4 | test_match_type_employer_capped | match employer at limit | three surfaces | one value | New |

Re-pinned tests (HIGH-07/F-043/F-055): card employer-match assertions corrected with arithmetic.

## F. Manual verification steps

1. Account near its annual contribution limit: the per-period employer card equals the growth
   chart's employer line and the year-end employer total.
2. `pytest tests/test_routes/test_investment.py tests/test_services/test_growth_engine.py tests/test_services/test_year_end_summary_service.py -v`.

**G. Downstream effects** Employer-match figure is single-sourced across all three surfaces.

**H. Rollback notes** Revert the card to the uncapped argument + re-pins.

---

### Commit 26 -- DTI gross from the raise-aware paycheck producer (MED-06)

**A. Commit message** `fix(savings): DTI gross from raise-aware paycheck producer (MED-06)`

**B. Problem statement** MED-06 / F-032: `savings_dashboard_service` computes DTI monthly gross as
`biweekly * 26 / 12` (verified ~`:168-176`), a flat conversion that ignores scheduled raises, so for
any user with an applicable recurring raise the DTI denominator disagrees with the paycheck engine
(worked example: 26.9% vs 27.7%).

## C. Files modified

- `app/services/savings_dashboard_service.py`: DTI gross monthly income comes from the canonical
  raise-aware paycheck producer (`paycheck_calculator`) for the period span, not the flat factor.
- Use the named 26/12 constants from Commit 23 only where a genuine flat conversion is still correct
  (documented).
- `tests/test_services/test_savings_dashboard_service.py`.

**D. Implementation approach** One income producer (the paycheck engine) feeds the DTI numerator's
sibling denominator; the flat factor is removed from the DTI path. Re-pin the DTI assertions for
raise-applicable fixtures.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C26-1 | test_dti_with_applicable_raise | salary + 3% recurring raise | DTI | raise-aware gross; hand-computed ratio (was off ~1pp) | New |
| C26-2 | test_dti_no_raise_unchanged | no raise | DTI | identical to before (no regression) | New |
| C26-3 | test_dti_uses_paycheck_producer | grep | no flat `* 26 / 12` in DTI path | passes | New |
| C26-4 | test_dti_label_band_correct | borderline ratio | DTI band | correct band for the corrected ratio | New |

Re-pinned tests (MED-06/F-032): DTI assertions for raise fixtures corrected.

## F. Manual verification steps

1. User with a scheduled raise: /savings DTI matches the paycheck engine's monthly gross.
2. `pytest tests/test_services/test_savings_dashboard_service.py -v`.

**G. Downstream effects** DTI is consistent with the income engine.

**H. Rollback notes** Revert to the flat factor + re-pins.

---

### Commit 27 -- Leap-year day count + biweekly residue reconciliation (MED-05)

**A. Commit message** `fix(interest): leap-year day count + biweekly residue reconcile (MED-05)`

**B. Problem statement** MED-05 / PA-06/PA-07: `interest_projection` divides actual 366 days by a
hardcoded 365 in leap years (overstates daily interest ~~1.23 per $100k at 4.5% across a leap
crossing); `gross_biweekly = (annual / 26).quantize(...)` leaves an unreconciled per-cycle
residue~~$0.10/yr). Both are documented trade-offs the developer chose to fix properly.

## C. Files modified

- `app/services/interest_projection.py` (re-grep `DAYS_IN_YEAR = Decimal("365")` ~`:44`): thread the
  actual day count for the period (366 in leap years), keeping full precision and rounding via
  `round_money` only at the boundary.
- `app/services/paycheck_calculator.py` (re-grep `:133`): reconcile the biweekly rounding residue
  into the annual gross aggregate so the year reconciles exactly.
- `tests/test_services/test_interest_projection.py`, `test_paycheck_calculator.py`.

**D. Implementation approach** Use the real number of days in the projection window
(calendar-correct), not a constant. For the paycheck residue, the documented approach: track the
cumulative quantization residue and apply it so 26 periods sum to the exact annual figure (no silent
drift). Both are exact, hand-verified.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C27-1 | test_leap_year_uses_366 | window spanning Feb 29 | interest | uses 366 divisor; hand-computed (was +$1.23/$100k) | New |
| C27-2 | test_non_leap_uses_365 | normal year | interest | 365; unchanged | New |
| C27-3 | test_biweekly_residue_reconciles | $100,000/26 | sum 26 periods | exactly $100,000.00 (was $99,999.90) | New |
| C27-4 | test_residue_distribution_deterministic | -- | repeat | identical distribution each run | New |

Re-pinned tests (MED-05/PA-06/PA-07): leap-year and annual-gross assertions corrected with
arithmetic.

## F. Manual verification steps

1. Project HYSA interest across a Feb 29; figure matches the 366-day hand calc.
2. Sum a year of biweekly gross -> exactly the annual salary.
3. `pytest tests/test_services/test_interest_projection.py tests/test_services/test_paycheck_calculator.py -v`.

**G. Downstream effects** Interest and annual gross are calendar-exact.

**H. Rollback notes** Revert to the constant divisor / unreconciled residue + re-pins.

### Commit 28 -- Extract investment dashboard service; collapse dispatcher; LoanInputs DTO (MED-01)

**A. Commit message**
`refactor(investment): extract dashboard service; collapse dispatcher; DTO (MED-01)`

**B. Problem statement** MED-01 / S6-01/S6-03/S6-04/S6-06/S6-07: `investment.py` has 295/241-line
route bodies mixing HTTP + 8 inline ORM queries + business logic (SRP); there are two
per-account-type dispatchers (savings-dashboard vs year-end loan path) plus a hardcoded
`_DEDUCTION_PATH_TYPES` enum set (OCP); an 11-key `ctx` and 4-key `base_args` are passed whole when
1-2 keys are read (ISP); `get_loan_projection` duck-types a model (DIP). No wrong number today, but
this is the structural substrate under CRIT-01/CRIT-02.

## C. Files modified

- `app/services/investment_dashboard_service.py` (new): the extracted dashboard/growth-chart
  computation (no Flask). `app/routes/investment.py` becomes a thin delegator (mirrors how
  `savings.py` was already reduced to a 4-line delegator).
- `app/services/account_projection.py` (new) or consolidate into the resolver layer: one flag-driven
  per-account dispatcher (`has_amortization`/`has_interest`/`is_escrow`/`is_401k`), replacing the
  two dispatchers and `_DEDUCTION_PATH_TYPES`.
- `amortization_engine.get_loan_projection`: accept a declared frozen `LoanInputs` DTO (the 7
  attributes) instead of a duck-typed model (DIP); the resolver from Commit 13 already produces this
  shape.
- ISP: helpers in `year_end_summary_service` take only the fields they read, not the 11-key
  `ctx`/`base_args` whole (keep the sanctioned load-once W-052 pattern; only the parameter surface
  narrows).
- Tests across investment/year-end/amortization (behavioral assertions unchanged; this is a
  structure-only refactor -- assert byte-identical outputs).

**D. Implementation approach** Pure structural refactor: outputs do not change (assert-unchanged
tests prove it). Flag-driven dispatch replaces enum/string branching (memory: id/flag-based, not
name strings; OCP). Do not rewrite the calculation math (rule 10); move it intact into the service.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C28-1 | test_investment_route_is_thin_delegator | -- | line count / no ORM in route | route body small, no inline queries | New |
| C28-2 | test_dashboard_output_unchanged | fixture | service vs pre-refactor | byte-identical dict | Mod (assert-unchanged) |
| C28-3 | test_single_flag_driven_dispatcher | grep | one dispatcher, no `_DEDUCTION_PATH_TYPES` | passes | New |
| C28-4 | test_loan_inputs_dto_declared | -- | get_loan_projection signature | typed DTO param, not duck model | New |
| C28-5 | test_helpers_take_narrow_params | grep | no whole `ctx`/`base_args` splat into 1-2-key helpers | passes | New |
| C28-6 | test_no_flask_in_new_service | grep | clean | passes | New |

## F. Manual verification steps

1. /investment dashboard and growth chart render identically to before (visual + value spot check).
2. `pytest tests/test_routes/test_investment.py tests/test_services/test_year_end_summary_service.py tests/test_services/test_amortization_engine.py -v`.

**G. Downstream effects** Dispatch is open-closed; the resolver's DTO is consumed cleanly. No
numeric change.

**H. Rollback notes** Revert the extraction/dispatcher/DTO; outputs were unchanged so revert is
safe.

---

### Commit 29 -- Route residual inline/Jinja status predicates through the helper (MED-02 residual)

**A. Commit message**
`refactor(status): route residual inline/Jinja predicates through helper (MED-02)`

**B. Problem statement** MED-02 / D6-09 residual: after Commits 5/10 use the Commit 2 predicate, the
remaining inline `status_id != projected_id` Python sites, SQLAlchemy filters, and Jinja status
constants must also route through it so the rule has exactly one definition (E-15 fully realized).

## C. Files modified

- Remaining services/routes with inline status checks (re-grep `balance_calculator.py:365/411/443`,
  the 11 `== projected_id` filter sites, the two `[CREDIT, CANCELLED]` helper re-derivations).
- Jinja: pass status booleans/IDs from the route/service into the template; templates compare IDs
  only (coding standard: IDs not names in conditionals). The grid status constants come from one
  context provider.
- `tests/test_utils/test_balance_predicates.py` extended; affected route/service tests
  assert-unchanged.

**D. Implementation approach** Behavior-preserving consolidation. After this commit,
`grep -rn "status_id != \|status_id == \|\[CREDIT" app/` shows no business-logic status comparison
outside the predicate module and ref-cache. No numeric change (assert-unchanged tests).

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C29-1 | test_no_inline_status_business_logic | grep | none outside predicate/ref-cache | passes | New |
| C29-2 | test_jinja_uses_ids_not_names | grep templates | no `status.name ==` | passes | New |
| C29-3 | test_outputs_unchanged | broad fixtures | before vs after | identical | Mod (assert-unchanged) |
| C29-4 | test_credit_cancelled_single_definition | -- | one helper | passes | New |

## F. Manual verification steps

1. Grid/dashboard render identically; status styling unchanged.
2. `pytest -q` targeted on affected files green.

**G. Downstream effects** E-15 fully realized; a one-sided status-rule change is now impossible.

**H. Rollback notes** Revert per-site to inline; predicate module remains harmless.

---

### Commit 30 -- Entry-tracked bill row single disclosed base (E-21, MED-03)

**A. Commit message** `fix(dashboard): entry-tracked bill row single disclosed base (E-21, MED-03)`

**B. Problem statement** MED-03 / F-028/F-056 (was Q-08, resolved by E-21): the entry-tracked bill
row's amount cell uses `effective_amount` (tier-3 actual for settled) while its
`entry_remaining`/`entry_over_budget` are computed as `estimated - sum(entries)`, never consulting
`actual_amount` or status -- one row, two undisclosed bases. E-21: one declared, disclosed base,
consistent with the amount cell.

## C. Files modified

- `app/services/dashboard_service.py` (re-grep `:191`, `:203`) and
  `app/services/entry_service.compute_remaining` (re-grep `:405`): compute remaining/over-budget
  against the same declared base as the amount cell (per E-21: anchored on `estimated_amount`
  unconditionally), and surface the base so the row can disclose it.
- `app/templates/...` bill row: label the base.
- `tests/test_services/test_dashboard_service.py`, `test_entry_service.py`.

**D. Implementation approach** E-21 fixes the model: the row's remaining/over-budget is defined
against `estimated_amount` consistently, disclosed in the UI, and consistent with the amount cell.
One coherent plan-vs-actual model, not a mixed base.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C30-1 | test_row_single_base | actual=100, est=120, entries=80 | dashboard row | amount/remaining/over-budget all on the one declared base, internally consistent | New |
| C30-2 | test_base_disclosed_in_ui | same | render | base labeled | New |
| C30-3 | test_remaining_anchored_estimated | per E-21 | compute_remaining | uses estimated unconditionally | New |
| C30-4 | test_over_budget_consistent_with_amount | overspent envelope | row | over-budget flag agrees with amount cell | New |

Re-pinned tests (MED-03/F-028): row remaining/over-budget assertions corrected to the single-base
values.

## F. Manual verification steps

1. A finished entry-tracked bill whose actual != estimate shows an internally consistent row with a
   labeled base.
2. `pytest tests/test_services/test_dashboard_service.py tests/test_services/test_entry_service.py -v`.

**G. Downstream effects** F-028/F-056 resolved under E-21; `goal_progress` GP2 conditional flag
cleared.

**H. Rollback notes** Revert to the mixed-base computation + re-pins.

---

### Commit 31 -- Move money math out of Jinja/JS into services (MED-04)

**A. Commit message** `refactor(templates): move money math out of Jinja/JS into services (MED-04)`

**B. Problem statement** MED-04 / E-16/E-17: money is computed in 11 Jinja sites (e.g.
`_transaction_cell.html` `estimated - entries.total`, `_escrow_list.html` `annual|float / 12` -- a
Decimal through binary float) and 3 JS sites (retirement-gap chart, variance tooltip). Numerically
consistent today but any server-formula change not mirrored ships a silent wrong figure.

## C. Files modified

- Templates (re-grep `grid/_transaction_cell.html:21`, `loan/_escrow_list.html:37`, the other 9 TA
  sites): remove arithmetic; the route/service computes the value in Decimal (via the
  resolvers/round_money) and passes it ready to render. Eliminate every `|float`.
- JS (`retirement_gap_chart.js:24-25`, `chart_variance.js:69`): the server provides the computed
  series/diff; JS only renders (coding standard: JS monetary values display-only).
- `tests/` for the owning routes/services assert the now-server-computed values.

**D. Implementation approach** The producers already exist after earlier commits; this commit just
moves the last computations server-side and deletes the float casts. Assert-unchanged on the numbers
(they were consistent today), but now single-sourced.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C31-1 | test_no_arithmetic_in_jinja | grep templates | no `-`/`/`/`*` on money vars, no `|float` | passes | New |
| C31-2 | test_no_money_math_in_js | grep js | no monetary arithmetic | passes | New |
| C31-3 | test_escrow_per_period_server_decimal | escrow | route value | Decimal, == annual/12 hand-computed (no float) | New |
| C31-4 | test_transaction_cell_remaining_server | entries | route value | server-computed, matches resolver | New |
| C31-5 | test_rendered_values_unchanged | fixtures | before vs after | identical | Mod (assert-unchanged) |

## F. Manual verification steps

1. Grid cell, escrow list, retirement-gap chart, variance tooltip render identical numbers, now from
   the server.
2. `pytest` targeted on the owning routes green; CSP unaffected (no inline JS added).

**G. Downstream effects** All money is computed exactly once, in Python Decimal.

**H. Rollback notes** Restore template/JS arithmetic; values were unchanged so revert is safe.

---

### Commit 32 -- Replace loose assertions; add invariant coverage (MED-07)

**A. Commit message** `test(calc): replace loose assertions; add invariant coverage (MED-07)`

**B. Problem statement** MED-07 / PA-12..PA-30 residue: across calc modules some tests assert
direction/`is not None` instead of exact hand-computed Decimal, and several invariants have no test
(debt-balance depth, sad paths, boundaries, status-machine, HYSA full-year compounding, paycheck/tax
negative paths and annual reconciliation, transfer-recurrence boundaries, chart-data value,
amortization extra-payment, growth-engine). A deterministic error would ship uncaught.

## C. Files modified

- The genuinely-loose tests identified in `07_test_gaps.md` Part 7.A LOOSE-ONLY verdicts (test files
  only; no production code -- unless a pinned value reveals a real bug, which is then a finding
  handled in-scope per CLAUDE.md rule 4).
- Add the missing invariant tests (relationship tests like `net == gross - taxes - deductions`,
  annual reconciliation, status-machine legality, boundary/sad-path).

**D. Implementation approach** Every replaced assertion gets the hand-computed Decimal and the
arithmetic in a comment (testing standard: service tests assert exact expected values). This is the
leverage the developer needs to refactor safely later. If pinning a value surfaces a real defect,
stop and report it (rule 4), and fold the fix into its own commit.

**E. Test cases** (representative; this commit is mostly test additions)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C32-1 | test_net_equals_gross_minus_tax_minus_deductions | known paycheck | relationship | exact Decimal identity | New |
| C32-2 | test_debt_balance_sad_paths | zero/negative/overpay | engine | specific edge behavior asserted | New |
| C32-3 | test_hysa_full_year_compounding_pinned | known APY | full year | exact compounded Decimal | New/Mod |
| C32-4 | test_status_machine_illegal_transitions_rejected | each illegal pair | transition | raises | New |
| C32-5 | test_annual_tax_reconciliation | full year | sum periods | equals annual hand calc | New |
| C32-6 | test_amortization_extra_payment_pinned | extra payment | schedule | exact months/interest saved | New/Mod |
| C32-7 | test_no_directional_only_asserts_remain | grep | no `> 0`/`is not None`-only in targeted files | passes | New |

## F. Manual verification steps

1. Run the targeted modules: every assertion is an exact Decimal or a specific edge behavior.
2. `pytest tests/test_services -q` green.

**G. Downstream effects** The calculation suite is now hand-verified, giving safe refactor leverage.

**H. Rollback notes** Revert test changes. (Reverting reduces coverage -- not recommended.)

### Commit 33 -- Delete dead legacy calculate_federal_tax + its test (LOW-01)

**A. Commit message** `chore(tax): delete dead legacy calculate_federal_tax + its test (LOW-01)`

**B. Problem statement** LOW-01 / F-040: `tax_calculator.calculate_federal_tax` (re-grep `:215-234`)
computes tax differently (no pre-tax deduction subtraction, returns annual not per-period) but has
zero `app/` callers; the only references are its definition and `TestLegacyWrapper`
(`test_tax_calculator.py:510`). It is an inert divergence whose only risk is a future caller
inheriting the wrong base, and the CLAUDE.md rule-5 tension of a test pinning dead behavior.

## C. Files modified

- `app/services/tax_calculator.py`: delete `calculate_federal_tax`.
- `tests/test_services/test_tax_calculator.py`: delete `TestLegacyWrapper` (deleted together with
  the code it pins -- this is not "modifying a test to pass," it is removing a test for deleted dead
  code; the live engine's tests are untouched).

**D. Implementation approach** First re-confirm zero `app/` callers:
`grep -rn "calculate_federal_tax" app/` returns only the definition. Trace impact (rule 7) before
deleting. Delete code and its dedicated test in the same commit so neither dangles.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C33-1 | test_no_callers_remain | grep | `calculate_federal_tax` absent from app/ | passes | New |
| C33-2 | test_live_federal_engine_unchanged | known income | live withholding | identical to before (no regression) | Mod (assert-unchanged) |
| C33-3 | full suite collects without TestLegacyWrapper | -- | pytest collect | green | -- |

## F. Manual verification steps

1. `grep -rn calculate_federal_tax app/` empty;
   `pytest tests/test_services/test_tax_calculator.py -v` green; full suite collects.

**G. Downstream effects** Inert divergence removed; no future caller can inherit the wrong base.

**H. Rollback notes** Restore the function and its test class from git. Reversible.

---

### Commit 34 -- Route recurrence-regen deletion through transfer_service (LOW-02)

**A. Commit message**
`fix(transfer): route recurrence regen delete through transfer_service (LOW-02)`

**B. Problem statement** LOW-02 / B6-03: `transfer_recurrence.regenerate_for_template` (re-grep
`:200-201`) does `db.session.delete(xfer)` directly instead of `transfer_service.delete_transfer`.
The FK CASCADE keeps the shadow pair atomic (no balance drift), but the shortcut skips the canonical
orphan-verification self-check and the `EVT_TRANSFER_HARD_DELETED` audit event -- a forensic gap,
not an arithmetic one. Transfer Invariant 4 (all mutations through the transfer service) is
literally violated.

## C. Files modified

- `app/services/transfer_recurrence.py`: the deletion loop calls
  `transfer_service.delete_transfer(...)` (the single canonical hard-delete path: orphan verify +
  audit event).
- `tests/test_services/test_transfer_recurrence.py`.

**D. Implementation approach** Single canonical deletion path for `budget.transfers` (CLAUDE.md
Transfer Invariant 4). No balance change (FK cascade already kept the pair consistent); the gain is
the orphan self-check and the forensic audit row on regen deletes.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C34-1 | test_regen_delete_emits_audit_event | template regen deleting transfers | regenerate_for_template | `EVT_TRANSFER_HARD_DELETED` logged per deletion | New |
| C34-2 | test_regen_delete_runs_orphan_check | regen | -- | orphan verification executed | New |
| C34-3 | test_shadow_pair_still_atomic | regen | -- | both shadows gone, no orphan (unchanged) | New |
| C34-4 | test_no_bare_session_delete_transfer | grep | no `db.session.delete(xfer)` in transfer_recurrence | passes | New |

## F. Manual verification steps

1. Trigger a transfer-template regeneration that supersedes transfers; audit log shows the
   hard-delete events.
2. `pytest tests/test_services/test_transfer_recurrence.py tests/test_services/test_transfer_service.py -v`.

**G. Downstream effects** `budget.transfers` deletions have exactly one writer path (Invariant 4
fully held).

**H. Rollback notes** Revert to the bare delete loop. Reversible (FK cascade still protected the
pair).

---

### Commit 35 -- Documentation corrections (LOW-04, LOW-05/Q-26, R-9, R-10)

**A. Commit message** `docs(audit): correct comment/table drift (LOW-04, LOW-05, R-9, R-10)`

**B. Problem statement** Doc-only. LOW-04: `budget.escrow_components.inflation_rate` is
AUTHORITATIVE but missing its own row in the Phase-4 D3 table. LOW-05 / Q-26 sub-2: the `user.py`
`estimated_retirement_tax_rate` comment promises a bracket fallback the code does not implement
(A-26 decided: fix the comment, do not build the fallback). R-9/R-10: audit reconciliation notes
(PA-08 carry-forward scenario filter now present; PA-10/PA-11 single-producer tests exist but the
cross-page lock was absent until Commit 11).

## C. Files modified

- `app/models/user.py` (re-grep `:212-215`): correct the comment to "NULL = no retirement-tax
  adjustment applied" (matches code; A-26's decided direction). No code change, no fallback built
  (Q-26 sub-2 product decision remains carried).
- `docs/audits/financial_calculations/04_source_of_truth.md`: add the missing
  `escrow_components.inflation_rate -> AUTHORITATIVE` D3 row.
- `docs/audits/financial_calculations/08_findings.md` (or an addendum): record R-9/R-10
  reconciliation notes and that HIGH-01 (Commit 11) closed the cross-page lock gap.

**D. Implementation approach** Comment and audit-doc edits only; no logic. Q-26 sub-2 (should a
bracket fallback ever exist) stays an open product question, explicitly noted, not resolved here.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C35-1 | test_user_comment_matches_code | -- | read comment vs `retirement_gap_calculator` behavior | comment now accurate (no promised fallback) | New |
| C35-2 | (doc review) | -- | manual | D3 table has inflation_rate row; R-9/R-10 recorded | -- |

## F. Manual verification steps

1. Read `user.py:212-215`; the comment matches `retirement_gap_calculator.calculate_gap` (None -> no
   adjustment).
2. Confirm the D3 table and R-notes.

**G. Downstream effects** Documentation no longer misleads a future reader into trusting a
non-existent fallback. Q-26 sub-2 remains the single carried open question.

**H. Rollback notes** Revert the comment/doc edits. No behavior to roll back.

---

### Commit 36 -- Enforce no-Flask-in-services import linter (OPT-3, B6-01)

**A. Commit message** `test(arch): enforce no-Flask-in-services import linter (OPT-3, B6-01)`

**B. Problem statement** B6-01: the "services never import Flask objects" boundary is currently
asserted only by 22 prose docstrings. It holds today (grep-verified) but nothing mechanically
enforces it. This is a test-only safety net (optional per the developer's instruction; recommended
-- it locks the architecture the resolvers depend on).

## C. Files modified

- `tests/test_arch/test_services_no_flask.py` (new): AST-scan every `app/services/*.py`; fail on any
  import of `flask`, `request`, `session`, `current_app`, `g` (Flask's), or `render_template`. Allow
  `db.session` (SQLAlchemy, permitted).

**D. Implementation approach** One mechanical test replaces 22 prose contracts (DRY for the contract
itself). AST-based, not regex, to avoid the loop-variable `g` false positives the audit noted.

## E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C36-1 | test_no_flask_imports_in_services | all services | AST scan | zero violations (passes today) | New |
| C36-2 | test_linter_detects_injected_violation | temp file importing flask | scan | detected (proves it bites) | New |
| C36-3 | test_db_session_allowed | service using db.session | scan | not flagged | New |

## F. Manual verification steps

1. `pytest tests/test_arch/test_services_no_flask.py -v` green.
2. Temporarily add `from flask import request` to a service -> the test fails. Remove.

**G. Downstream effects** The services boundary (which keeps the resolvers pure/testable) is now
mechanically enforced forever.

**H. Rollback notes** Delete the test. (Not recommended -- it guards a load-bearing boundary.)

---

### Commit 37 -- Full gate + save remediation doc

**A. Commit message** `chore(release): full gate + save remediation doc`

**B. Problem statement** Final acceptance gate for the whole remediation, and the only non-code
action: persist this approved plan into the repository.

## C. Files modified

- `docs/audits/financial_calculations/remediation_plan.md` (new): the approved content of this plan,
  verbatim.
- No source/test/migration changes in this commit.

## D. Implementation approach (gate checklist -- all must pass before this commit)

1. `python scripts/build_test_template.py` (migrations + new audited table changed the schema;
   CLAUDE.md requires rebuild; entrypoint trigger-count health check expects the +1 from
   `loan_anchor_events`).
2. `pytest` (full suite, `-n 12` default) -- ends in `N passed`, zero failed/errors/xfailed (testing
   standard).
3. `pylint app/ --fail-on=E,F` -- clean, no new warnings vs baseline.
4. Every migration tested `flask db upgrade` then `flask db downgrade` then `upgrade` (both
   directions, per Definition of Done).
5. The cross-page invariant (Commit 11) and the ARM-window stability lock (Commit 13) green.
6. Hand-computed reconciliation appendix (Section 11) re-verified against the final code (spot-check
   10 random pinned values resolve to the cited code -- the audit's own trust-but-verify rule
   applied to this plan's output).
7. `git status` shows only intended files; commit messages follow `<type>(<scope>): <what>` and end
   with the required Co-Authored-By trailer; developer asked before any push (Git Workflow: dev
   branch, PR to main, green CI gate).

**E. Test cases** The entire suite is the test case. Acceptance: full green suite, clean pylint,
both-direction migrations, invariants green.

## F. Manual verification steps

1. Walk all five original symptoms in the running app and confirm each is resolved (Section 10).
2. Confirm `docs/audits/financial_calculations/remediation_plan.md` matches the approved plan.

**G. Downstream effects** Remediation complete; the plan is preserved in-repo for traceability
alongside the audit it answers.

**H. Rollback notes** The plan doc is additive. Code rollback is per-commit (each H above); the
cross-page and ARM-window locks make a silent regression detectable.

---

## 10. End-to-end verification (symptom walkthrough)

After Commit 37, each original symptom is re-tested in the running app and by automated invariant:

1. **Symptom #1 ($160 vs $114.29).** Build the symptom tuple (anchor 614.29; Projected groceries
   500.00; cleared entries 20.00/15.71/10.00). Grid, /savings, /accounts, dashboard, net worth,
   calendar all show `160.00`. Automated: Commit 11 C11-1.
2. **Symptom #2 (mortgage payment $1911/$1914/$1912).** Account-3-like ARM; the loan card, schedule
   first row, debt strategy, savings PITI all show one payment; re-read a month later -> unchanged.
   Automated: C13-1/C13-2, C15-2, C17-5.
3. **Symptom #3 (current_principal frozen).** Settle a monthly transfer; resolved principal drops by
   the principal portion on every surface. Automated: C14-1..C14-5, C15-6.
4. **Symptom #4 (5/5 ARM creep).** 12 consecutive months inside the fixed window return a
   byte-identical payment. Automated: C13-1/C13-2 (E-02 lock).
5. **Symptom #5 (/accounts matches nowhere).** Checking facet: C11-1..C11-4. Loan facet:
   C15-1/C15-2.

Plus the standing locks: cross-page balance equality (Commit 11), ARM fixed-window stability (Commit
13), FICA cap parity (Commit 18), no-Flask-in-services (Commit 36), and the hand-computed
reconciliation appendix.

## 11. Hand-computed reconciliation appendix (to be filled at execution)

Each CRITICAL/HIGH commit that changes a number records here: the inputs, the pre-fix value, the
hand arithmetic, and the post-fix value, so the developer can verify every corrected figure without
rerunning the audit. Seed entries (filled during execution):

- Symptom #1: anchor `614.29`, Projected `500.00`, cleared `45.71` -> `max(500-45.71-0,0)=454.29`;
  balance `614.29-454.29 = 160.00` (was `614.29-500 = 114.29`). Gap `45.71` = cleared debit already
  in the anchor.
- Symptom #2/#4: ARM `P=400000`, `i=0.06/12=0.005`, `n=360`;
  `M* = 400000*0.005 / (1-(1.005)^-360) = 2000 / 0.833958 = 2398.20` (constant in window). Pre-fix
  month 24 `n=336 -> 2460.45`, month 25 `n=335 -> 2463.28` (creep `+2.83`).
- CRIT-03: `$312,000`, 26 periods, `$12,000`/period; correct year SS `$11,439.00` vs pre-fix
  `$19,344.00` = `+$7,905.00` overstatement.
- CRIT-04: `swr=0.0000`, balance `$1,200,000` -> correct income `$0.00` (pre-fix
  `1,200,000*0.04/12 = $4,000.00`). Two `$100k` accounts at `0%`/`7%` -> correct blended `3.50%`
  (pre-fix `7.00%`).

## 12. Open questions carried forward

- **Q-26 sub-2 (product decision, not blocking).** Should a bracket-based fallback for
  `estimated_retirement_tax_rate` ever exist? A-26 decided the remediation direction (Commit 35
  corrects the misleading comment; no fallback built). Whether the feature should exist is deferred
  to broader retirement-module design and is the single open question after this plan. It does not
  block any commit.

## 13. Notes on executing this plan

- Run commits in order; the dependency DAG (Section 7) is binding. The balance group (3-11) and loan
  group (12-17) can be reviewed in parallel but each is internally ordered.
- Every commit: re-grep cited lines first (audit lines drift), targeted tests during,
  `pylint app/ --fail-on=E,F`, then the relevant batch green; full suite as the per-commit final
  gate and the plan-final gate (Commit 37).
- Never silently re-pin a test. The "Re-pinned tests" lines name the finding that proved the old
  value wrong and require the arithmetic in a comment (Section 1 rule 2).
- Migrations: additive first (the demoted loan columns are not dropped here; OPT-1 is the optional
  later destructive drop). Every migration has a working downgrade and, where destructive, a
  `Review:` line and explicit developer approval.
- This is a remediation plan only. No code is changed by producing it. Execution happens in separate
  sessions, one commit (or small group) per session, suite green before moving on.
