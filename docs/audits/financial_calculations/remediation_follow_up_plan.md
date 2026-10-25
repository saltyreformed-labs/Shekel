> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Financial-Calculation Follow-up Remediation Plan

Implementation plan that closes every NOT_DONE or PARTIAL item in
`remediation_follow_up.md`. Authored after the main remediation chain
(Commits 1-36 of `remediation_plan.md`) landed, before the
remediation-final-gate Commit 37.

Cross-references:

- Source list: `remediation_follow_up.md` (F-1 through F-23)
- Main remediation plan (Commits 1-37): `remediation_plan.md`
- Findings register: `08_findings.md`
- Coding standards: `../../coding-standards.md`
- Testing standards: `../../testing-standards.md`

---

## 0. Context

The main remediation chain (`remediation_plan.md`) landed in 36 commits
(`fc932f6` through `07724fb`). Each commit deliberately deferred
adjacent structural cleanups so the audit-fix diffs stayed focused;
those deferrals were logged in `remediation_follow_up.md`. This plan
closes them as a separate, atomic chain so the main chain's
Commit 37 final-gate can run against a fully resolved tree.

Verification of the documented status of every follow-up item against
the live codebase produced the table in Section 4. Several documented
statuses turned out to be stale; the actual statuses are folded into
the plan.

The same constraints that governed the main remediation apply here:

- You are the only safeguard. CI gates the merge, but a missed assertion
  ships defective financial math into production.
- Decimal-from-strings for every monetary value; IDs and boolean columns
  for business logic; never compare against ref-table `name` strings.
- DRY / SOLID / fully normalized schema.
- Type-hinted, substantive docstrings, specific exceptions, no Unicode
  em/en dashes.
- Never modify a test to make it pass. Every re-pinned assertion gets a
  comment naming the finding and the hand-computed arithmetic.

---

## 1. Hard rules for executing this plan

1. **Run commits in order.** Section 5 records dependencies; the order
   already respects them. Out-of-order execution risks merge conflicts
   in the multi-service refactors (Commits 17-19, 21).
2. **Re-grep cited lines first.** Numbers below are accurate at
   authoring time (2026-05-21). They will drift as the chain lands.
3. **Re-pinning a test requires a comment** naming the finding ID and
   the hand-computed arithmetic that produced the new expected value.
4. **Targeted pytest during edits; pylint `app/ --fail-on=E,F` clean;
   full pytest as the per-commit final gate** -- via `./scripts/test.sh`.
5. **Migrations** (Commit 13 is the only schema change) **must
   round-trip upgrade -> downgrade -> upgrade cleanly.** The destructive
   change carries a `Review:` line and was approved at plan time.
6. **No new packages** without approval. Every helper extracted here
   reuses existing stdlib / SQLAlchemy / Marshmallow primitives.
7. **Stay in scope.** Out-of-scope issues spotted during execution land
   in `remediation_follow_up.md` as a new F-N entry, never inline.
8. **Do not push.** After every commit lands locally and the full suite
   is green, present results and ask before pushing to `dev` (which
   triggers CI; PR-to-main is required for promotion).

---

## 2. Design decisions made for this plan

Captured at plan time so the developer can audit them before execution.

| Decision | Choice | Rationale |
|---|---|---|
| **F-3 calendar per-day filter** | `balance_contributing_clause` (Projected + Settled, excludes Cancelled + Credit) | Calendar UX shows realized payments at their settled date; the per-day total intentionally differs from the grid's Projected-only subtotal. |
| **F-13 negative SWR handling** | Reject with 422 via Marshmallow `Range(min=0, max=1)` on the `swr` query parameter | Cleanest contract; matches the storage-tier CHECK semantics; surfaces the error rather than silently zeroing the calculation. |
| **F-19 lump-sum investment fix** | Year-end calls `build_contribution_timeline` (Option 1) | Lowest-scope fix; mirrors the dashboard route's already-correct shape; preserves `calculate_investment_inputs` for the deduction-only callers. |
| **F-21 loan period-balance canonical** | Period-end-keyed (year-end semantic) | More accurate -- balance AFTER the payment due in the period containing the target month, not before it. Savings dashboard 3/6/12-month numbers move slightly; re-pinned with hand-computed arithmetic. |
| **F-18 destructive migration** | Included | Asymmetric CHECK on `loan_params.interest_rate` is a real gap; the migration is straightforward and reversible. Pre-check verifies no existing row violates the new bound. |
| **F-1 accounts.py split** | Included; Option A (single blueprint, file-split) | Highest-value DRY win already extracted (R-7 `anchor_service`); remaining work is mechanical file split with zero URL changes. |
| **OPT-1 / OPT-4 / OPT-5 / OPT-6** | Excluded from this plan | OPT-1 awaits a production cycle; OPT-4/5 are explicitly "listed only"; OPT-6 is a UI choice deferred to a separate ticket. |

---

## 3. Scope additions surfaced during verification

Re-verifying each follow-up item against the live code uncovered two
items whose documented status was stale or under-scoped. These are
folded into the plan.

- **R-FU-1 (F-7 is worse than the documented status).** The follow-up
  said "currently in sync." Side-by-side grep of `app/__init__.py:198-259`
  vs `tests/conftest.py:2088-2124` shows the conftest list is missing
  **eight** entries that exist in `app/__init__.py`:
  `TIMING_PRE_TAX`, `TIMING_POST_TAX`, `CALC_PERCENTAGE`, `CALC_FLAT`,
  `GOAL_MODE_FIXED`, `GOAL_MODE_INCOME_RELATIVE`, `INCOME_UNIT_PAYCHECKS`,
  `INCOME_UNIT_MONTHS`. Any template referencing the missing constants
  at test time would raise `UndefinedError`. Commit 6 fixes the drift
  as part of the extraction (the shared registration function makes
  the two-source-of-truth defect impossible going forward).
- **R-FU-2 (F-9 already resolved by Commit 15).** Verification confirmed
  `app/routes/loan.py:802-817` inserts the origination
  `LoanAnchorEvent` inline after `LoanParams` insert; the helper
  `tests/_test_helpers.insert_origination_event` exists for test
  callers. F-9 needs no implementation commit.

---

## 4. Verification status table

Result of re-grepping every follow-up against the current codebase.
Each NOT_DONE / PARTIAL item maps to exactly one commit below.

| Item | Status | Maps to commit |
|---|---|---|
| F-1 (accounts.py split) | NOT_DONE | C21 |
| F-2 (calendar anchor-None dead branches) | NOT_DONE | C11 |
| F-3 (calendar per-day filter) | NOT_DONE | C10 |
| F-4 (Commit 10 obligations doc drift) | DRIFTED, doc-only | C1 |
| F-5 (dead `_skip_user_bootstrap_period` flag) | NOT_DONE | C2 |
| F-6 (cross-page balance lock static guard) | NOT_DONE | C20 |
| F-7 (Jinja-globals duplication) | NOT_DONE (worse: lists out of sync) | C6 |
| F-8 (engine anchor-reset clobbers fixed-rate payment) | NOT_DONE (unreachable) | C14 |
| F-9 (origination LoanAnchorEvent) | DONE (Commit 15) | -- |
| F-10 (engine internals read demoted columns) | NOT_DONE | C15 |
| F-11 (truthiness `or Decimal("0")`) | NOT_DONE | C4 |
| F-12 (stylistic truthiness) | NOT_DONE | C4 |
| F-13 (negative SWR validation) | NOT_DONE | C7 |
| F-14 (defense-in-depth transfer template) | PARTIAL | C8 |
| F-15 (`Decimal("12")` constants) | NOT_DONE | C5 |
| F-16 (`pct_to_decimal` unused) | NOT_DONE | C3 |
| F-17 (investment / pension route-layer pct) | NOT_DONE (PARTIAL) | C12 |
| F-18 (missing upper-bound CHECK) | NOT_DONE | C13 |
| F-19 (lump-sum transfers averaged) | NOT_DONE | C16 |
| F-20 (off-engine `salary_gross_biweekly`) | NOT_DONE | C17 |
| F-21 (loan period-balance dispatcher) | NOT_DONE | C19 |
| F-22 (deduction-loader / projection-inputs helpers) | NOT_DONE | C18 |
| F-23 (companion float cast) | NOT_DONE | C9 |

---

## 5. Commit checklist

Twenty-one implementation commits plus a final gate. Each row is one
git commit; messages use `<type>(<scope>): <what>` per Definition of
Done.

| # | Commit message | Closes |
|---|---|---|
| 1 | `docs(audit): correct Commit 10 obligations cross-ref (F-4)` | F-4 |
| 2 | `chore(tests): remove dead _skip_user_bootstrap_period flag (F-5)` | F-5 |
| 3 | `chore(utils): delete unused pct_to_decimal helper (F-16)` | F-16 |
| 4 | `refactor(retirement): replace truthiness on financial values (F-11, F-12)` | F-11, F-12 |
| 5 | `refactor(services): use canonical MONTHS_PER_YEAR constant (F-15)` | F-15 |
| 6 | `refactor(tests): extract Jinja-globals registration helper + sync missing entries (F-7)` | F-7 |
| 7 | `fix(retirement): reject negative SWR slider override (F-13)` | F-13 |
| 8 | `fix(transfers): defense-in-depth filter on hard_delete_transfer_template (F-14)` | F-14 |
| 9 | `fix(companion): move entry pct derivation to service helper (F-23)` | F-23 |
| 10 | `fix(calendar): per-day filter via balance-contributing predicate (F-3)` | F-3 |
| 11 | `refactor(calendar): raise on unresolvable account/scenario; remove dead branches (F-2)` | F-2 |
| 12 | `refactor(schemas): unify percent conversion at @pre_load for investment + pension (F-17)` | F-17 |
| 13 | `fix(schema): add upper-bound CHECK on loan_params.interest_rate (F-18)` | F-18 |
| 14 | `fix(engine): gate anchor-reset payment recompute on not using_contractual (F-8)` | F-8 |
| 15 | `refactor(loan): delete dead get_loan_projection / calculate_balances_with_amortization (F-10)` | F-10 |
| 16 | `fix(year-end): investment projection via contribution timeline (F-19)` | F-19 |
| 17 | `refactor(income): canonical raise-aware gross-biweekly helper (F-20)` | F-20 |
| 18 | `refactor(investment): extract shared deduction-loader + projection-inputs helpers (F-22)` | F-22 |
| 19 | `refactor(loan): unify period-balance dispatcher; period-end-keyed canonical (F-21)` | F-21 |
| 20 | `test(routes): static guard against bypass of balance_resolver in grid/accounts (F-6)` | F-6 |
| 21 | `refactor(routes): split accounts.py into per-sub-domain modules (F-1)` | F-1 |
| 22 | `chore(release): follow-up final gate` | -- |

---

## 6. Commit dependency analysis

```text
Independent (parallelizable review):
  1 docs (F-4)
  2 conftest cleanup (F-5)
  3 delete pct_to_decimal (F-16)
  5 MONTHS_PER_YEAR (F-15)

Truthiness sweep (single file):
  4 retirement truthiness (F-11, F-12)

Test infra DRY:
  6 Jinja globals helper (F-7) -- depends on 5 only by file proximity

Targeted behavioral fixes:
  7 SWR Range(min=0) (F-13)
  8 transfer hard-delete defense-in-depth (F-14)
  9 companion pct helper (F-23)

Calendar group:
 10 per-day filter (F-3)
 11 anchor-None dead branches (F-2)  -- 10 must land first; both touch calendar_service

Schema/route reconciliation:
 12 investment + pension pct schemas (F-17)

Destructive migration:
 13 loan_params upper-bound CHECK (F-18)

Engine cleanup:
 14 fixed-rate anchor reset gate (F-8)
 15 delete dead engine functions (F-10) -- independent of 14

Investment + salary sweep:
 16 year-end timeline for lump sum (F-19)
 17 raise-aware paycheck producer (F-20) -- depends on 16 only by file overlap
 18 shared deduction + projection-inputs helpers (F-22) -- depends on 17

Loan dispatcher:
 19 loan period-balance map (F-21) -- depends on 18 only by file overlap

Test infra hardening:
 20 cross-page static guard (F-6) -- after 19 to land against final code

Largest mechanical refactor (last):
 21 accounts.py split (F-1) -- after every commit that touches accounts.py

Final gate:
 22 full suite + pylint + migration round-trip
```

Ordering rationale: documentation and dead-code removals first (cheap,
unblock review attention), then truthiness sweep (single-file), then
quick targeted fixes, then the calendar group together, then the
schema / destructive-migration block, then the engine cleanup, then the
investment / salary / loan refactor chain (largest cross-file work),
and finally the accounts.py split (mechanical, last to minimise merge
surface). The cross-page static guard (Commit 20) goes near the end so
it locks against the final code shape, not an intermediate one.

---

## 7. Commits (detailed)

Each commit follows: A message, B problem, C files, D implementation,
E tests, F manual verification, G downstream, H rollback.

---

### Commit 1 -- Correct Commit 10 obligations cross-reference (F-4)

**A. Commit message** `docs(audit): correct Commit 10 obligations cross-ref (F-4)`

**B. Problem statement** `remediation_plan.md` Section 9 Commit 10 line
911 instructs the obligations route to be routed through
`balance_resolver.period_subtotal`. The obligations route does NOT
compute a per-period transaction subtotal -- it aggregates
`amount_to_monthly(...)` across templates, which is Commit 23's
territory (handled correctly there via `obligations_aggregator.committed_monthly`).
The doc text mixes the two concepts. Documentation-only fix.

**C. Files modified**
- `docs/audits/financial_calculations/remediation_plan.md` (one paragraph)
- `docs/audits/financial_calculations/remediation_follow_up.md`
  (mark F-4 as resolved)

**D. Implementation approach**
1. In `remediation_plan.md` Section 9 Commit 10, drop the obligations
   bullet from the "Files modified" list; rewrite the trailing
   sentence to say "obligations monthly-equivalent aggregation is
   Commit 23 (E-24 / HIGH-05), not period_subtotal."
2. In `remediation_follow_up.md` F-4, change Status line to
   "**Status:** resolved by Commit 1 of the follow-up plan, doc-only
   correction." Keep the body for traceability.

**E. Test cases** None (docs only).

**F. Manual verification steps**
1. `git diff` shows only docs changes.
2. Read the corrected Commit 10 section end-to-end.

**G. Downstream effects** None.
**H. Rollback notes** `git revert`.

---

### Commit 2 -- Remove dead `_skip_user_bootstrap_period` flag (F-5)

**A. Commit message** `chore(tests): remove dead _skip_user_bootstrap_period flag (F-5)`

**B. Problem statement** `tests/conftest.py:844` declares the global
flag and assigns it at `:853` (True) / `:857` (False) inside the
`bare_user` fixture. Project-wide grep shows zero readers; the flag
was intended to suppress an `after_insert` listener that no longer
exists. Dead code in a test fixture.

**C. Files modified**
- `tests/conftest.py` (remove `global` declaration and both assignments
  in `bare_user`).

**D. Implementation approach**
1. Read `tests/conftest.py:830-880` to confirm the full `bare_user`
   fixture body.
2. Delete the `global _skip_user_bootstrap_period` line and the two
   assignment lines.
3. Verify no module-level declaration of the flag remains; if a
   module-level `_skip_user_bootstrap_period = False` exists,
   delete it too.

**E. Test cases**
- `./scripts/test.sh -k "bare_user" -v` -- every test that consumes
  `bare_user` still passes.

**F. Manual verification steps**
1. `grep -rn "_skip_user_bootstrap_period" /home/josh/projects/Shekel/`
   returns empty.
2. Full test suite green.

**G. Downstream effects** None.
**H. Rollback notes** `git revert`.

---

### Commit 3 -- Delete unused `pct_to_decimal` helper (F-16)

**A. Commit message** `chore(utils): delete unused pct_to_decimal helper (F-16)`

**B. Problem statement** Commit 24 (HIGH-06) moved every percent-to-
fraction conversion into Marshmallow `@pre_load` hooks. The function
`app/utils/formatting.pct_to_decimal` has no remaining production
callers (verified by `grep -rn "pct_to_decimal" app/` returning only
the definition itself). Dead utility code.

**C. Files modified**
- `app/utils/formatting.py` (delete the function; if no other content
  remains, delete the file and `app/utils/formatting/__init__.py` if
  applicable).
- `tests/test_routes/test_loan.py` (remove the docstring line at
  `:419` that references the function).

**D. Implementation approach**
1. Read `app/utils/formatting.py` to confirm the function is the only
   content. If yes, delete the file. If other helpers live there,
   delete only `pct_to_decimal` and `_HUNDRED` if no other reader.
2. Read `tests/test_routes/test_loan.py:415-425` and rewrite the
   docstring sentence to drop the `pct_to_decimal` reference (the
   docstring describes the historical conversion site; rewrite to
   cite the current schema-side `_normalize_percent_fields` helper).
3. `grep -rn "from app.utils.formatting" /home/josh/projects/Shekel/`
   to confirm no remaining import.

**E. Test cases**
- `./scripts/test.sh tests/test_routes/test_loan.py -v`
- `./scripts/test.sh tests/test_utils/ -v` (if any test references
  the file)

**F. Manual verification steps**
1. `grep -rn "pct_to_decimal" /home/josh/projects/Shekel/` returns empty.
2. `pylint app/ --fail-on=E,F` clean.

**G. Downstream effects** None.
**H. Rollback notes** `git revert`.

---

### Commit 4 -- Retirement truthiness sweep (F-11, F-12)

**A. Commit message** `refactor(retirement): replace truthiness on financial values (F-11, F-12)`

**B. Problem statement** Post-Commit-20 (CRIT-04), the
post-truthiness-removal invariant for `retirement_dashboard_service.py`
is "no truthiness on financial values, no truthiness on
SQLAlchemy-object existence checks." Two sites still violate it:

- `app/services/retirement_dashboard_service.py:385` --
  `bal = proj.get("current_balance", acct.current_anchor_balance) or Decimal("0")`
  (F-11). Behaviourally inert today but a latent hazard if upstream
  `proj.get` ever returns `None`.
- `app/services/retirement_dashboard_service.py:507` --
  `if params and projection_periods:` where `params` is a SQLAlchemy
  `InvestmentParams` (F-12). Stylistic inconsistency with the
  post-Commit-20 `is not None` gates in the same file.

**C. Files modified**
- `app/services/retirement_dashboard_service.py`.
- `tests/test_services/test_retirement_dashboard_service.py` (new
  test pinning the upstream `proj.get` contract).

**D. Implementation approach**
1. Read `app/services/retirement_dashboard_service.py:380-395` and
   `:500-515` to confirm the full surrounding context.
2. For F-11: replace the trailing `or Decimal("0")` with an explicit
   `is None` guard:
   ```python
   bal = proj.get("current_balance", acct.current_anchor_balance)
   if bal is None:
       bal = Decimal("0")
   ```
   Add a one-line comment above naming the F-11 invariant.
3. For F-12: replace `if params and projection_periods:` with
   `if params is not None and projection_periods:`. No comment
   needed -- the file's convention is now consistent.
4. Add a unit test in
   `tests/test_services/test_retirement_dashboard_service.py` pinning
   the upstream contract: when `proj.get("current_balance", ...)`
   returns `Decimal("0")` for a real zero-balance account, the
   weighted-return loop includes the account at weight 0 (does not
   skip it). This locks the behaviour Commit 20 established for the
   compute_slider_defaults path against the F-11 site.

**E. Test cases**
- C4-1 zero-balance account is included in the weighted-return
  numerator with weight 0 (does NOT skip the account); hand-computed
  weighted return for two accounts (`$0` at 7%, `$100k` at 5%)
  = `($0 * 0.07 + $100,000 * 0.05) / ($0 + $100,000) = 0.05`.

**F. Manual verification steps**
1. `grep -nF 'or Decimal("0")' app/services/retirement_dashboard_service.py`
   returns no matches in money contexts.
2. `grep -nE 'if [a-z_]+ and ' app/services/retirement_dashboard_service.py`
   shows no truthiness gate on a SQLAlchemy object.
3. `pylint app/ --fail-on=E,F` clean.

**G. Downstream effects** Targeted retirement test suite re-runs.
**H. Rollback notes** `git revert`.

---

### Commit 5 -- Use canonical `MONTHS_PER_YEAR` constant (F-15)

**A. Commit message** `refactor(services): use canonical MONTHS_PER_YEAR constant (F-15)`

**B. Problem statement** Four service files define their own
`Decimal("12")` constant (or inline literal) for rate-periodicity
contexts (annual rate -> monthly compounding, months-elapsed -> years
for escrow). `app/utils/money.MONTHS_PER_YEAR` already exists as the
canonical "months per year" constant. The four local aliases are a
DRY violation.

Sites (verified 2026-05-21):
- `app/services/debt_strategy_service.py:33` -- `TWELVE = Decimal("12")`
- `app/services/escrow_calculator.py:118` -- inline `Decimal("12")`
- `app/services/interest_projection.py:48` -- `MONTHS_IN_YEAR = Decimal("12")`
- `app/services/loan_resolver.py:378` -- inline `Decimal("12")`

**C. Files modified**
- The four service files above.

**D. Implementation approach**
1. Add `from app.utils.money import MONTHS_PER_YEAR` to each file
   that does not already import it.
2. Delete the local `TWELVE` / `MONTHS_IN_YEAR` aliases.
3. Replace all in-file usages with `MONTHS_PER_YEAR`.
4. For the inline `Decimal("12")` sites, replace with
   `MONTHS_PER_YEAR` at the call site.
5. Verify with `grep -nF 'Decimal("12")' app/services/` returning
   only legitimate sites (e.g. paycheck calculator pay-periods-per-
   year contexts use `PAY_PERIODS_PER_YEAR`, not `12`; if any
   biweekly contexts surface, they were already handled by Commit 23).

**E. Test cases**
- `./scripts/test.sh tests/test_services/test_debt_strategy_service.py tests/test_services/test_escrow_calculator.py tests/test_services/test_interest_projection.py tests/test_services/test_loan_resolver.py -v`
- No re-pinning; the constant value is identical (`Decimal("12")`)
  so the math is unchanged.

**F. Manual verification steps**
1. `grep -rnE '(TWELVE|MONTHS_IN_YEAR) = Decimal\("12"\)' app/services/`
   returns empty.
2. `grep -rnF 'Decimal("12")' app/services/` returns at most legacy
   sites in `obligations_aggregator.py` (Commit 23 already routed
   the biweekly-to-monthly factor through `MONTHS_PER_YEAR`); the
   four rate-periodicity sites listed above are now `MONTHS_PER_YEAR`.
3. Full test suite green.

**G. Downstream effects** No behavioural change; same numeric
constant routed through one symbol.
**H. Rollback notes** `git revert`.

---

### Commit 6 -- Extract Jinja-globals registration helper + sync missing entries (F-7)

**A. Commit message** `refactor(tests): extract Jinja-globals registration helper + sync missing entries (F-7)`

**B. Problem statement** The ID-derived Jinja globals are registered in
two places:
- `app/__init__.py:198-259` -- inside `create_app()`, 45 entries.
- `tests/conftest.py:2088-2124` -- inside
  `_refresh_ref_cache_and_jinja_globals`, 37 entries.

The lists were intended to be byte-identical. Verification at plan time
showed the conftest list is missing **eight** entries:
`TIMING_PRE_TAX`, `TIMING_POST_TAX`, `CALC_PERCENTAGE`, `CALC_FLAT`,
`GOAL_MODE_FIXED`, `GOAL_MODE_INCOME_RELATIVE`, `INCOME_UNIT_PAYCHECKS`,
`INCOME_UNIT_MONTHS`. A template referencing any of the missing
constants would raise `UndefinedError` at request time during tests.
The DRY fix and the missing-entry sync land in one commit so the
extraction makes future drift impossible.

**C. Files modified**
- `app/jinja_globals.py` (new) -- one function
  `register_ref_id_globals(app)` that takes a Flask app and
  registers every globals entry.
- `app/__init__.py` -- replace the 45-line registration block in
  `create_app()` with a call to `register_ref_id_globals(app)`.
- `tests/conftest.py` -- replace the 37-line registration block in
  `_refresh_ref_cache_and_jinja_globals` with a call to
  `register_ref_id_globals(app)`.

**D. Implementation approach**
1. Read `app/__init__.py:160-260` and `tests/conftest.py:2080-2130`
   in full to capture every entry, including the eight missing from
   conftest.
2. Create `app/jinja_globals.py`:
   ```python
   """Single source of truth for ID-derived Jinja globals.

   Re-seats every constant the templates consume by ID rather than
   by ref-table name (the project's "IDs for logic, strings for
   display only" invariant). Called from `create_app()` once per
   app lifetime and from the conftest's per-test ref-cache reseat
   helper so per-test drop+reclone cannot leave templates raising
   `UndefinedError`.
   """
   from flask import Flask

   from app import ref_cache
   from app.enums import (...)  # complete enum imports

   def register_ref_id_globals(app: Flask) -> None:
       """Register every ID-derived Jinja global on the given app.

       Args:
           app: The Flask app whose `jinja_env.globals` map will be
               populated. Idempotent -- safe to call multiple times.
       """
       app.jinja_env.globals["STATUS_PROJECTED"] = ref_cache.status_id(
           StatusEnum.PROJECTED,
       )
       # ... every entry from app/__init__.py:198-259 follows.
   ```
3. In `app/__init__.py`, import and call:
   ```python
   from app.jinja_globals import register_ref_id_globals
   # ... inside create_app, after ref_cache.prime():
   register_ref_id_globals(app)
   ```
4. In `tests/conftest.py`, replace the inline registration in
   `_refresh_ref_cache_and_jinja_globals` with the same call.
5. Re-grep both files to confirm no inline `app.jinja_env.globals[`
   assignment remains outside the helper.

**E. Test cases**
- C6-1 a route that consumes one of the eight previously-missing
  constants (e.g. an income-goal template referencing
  `INCOME_UNIT_PAYCHECKS`) renders without `UndefinedError`. Pick
  one such route from `grep -rn "INCOME_UNIT_PAYCHECKS"
  app/templates/`; pin the route test to assert the rendered HTML
  contains an expected fragment dependent on the constant.
- C6-2 invoke `register_ref_id_globals(app)` twice in succession;
  assert the second call is a no-op (idempotent).

**F. Manual verification steps**
1. `grep -nF "jinja_env.globals[" app/__init__.py tests/conftest.py`
   shows only the call sites; no inline assignment.
2. `python -c "from app.jinja_globals import register_ref_id_globals; print(register_ref_id_globals)"`
   imports cleanly.
3. Full test suite green; in particular, every route test that
   renders income-goal / pay-timing / calc-method templates passes
   (these were the previously-broken paths).

**G. Downstream effects** Templates can now consume any of the eight
previously-missing constants safely during tests; no production
change (those constants were always present in `create_app`).

**H. Rollback notes** `git revert`. The helper module is additive;
inlined registrations can be restored by reverting the two consumer
files.

---

### Commit 7 -- Reject negative SWR slider override (F-13)

**A. Commit message** `fix(retirement): reject negative SWR slider override (F-13)`

**B. Problem statement** `retirement_gap_calculator.calculate_gap`
guards `safe_withdrawal_rate > 0` and silently returns
`required_retirement_savings = ZERO` for any non-positive SWR. The
column's CHECK constraint admits `0 <= rate <= 1`, so a negative SWR
cannot arrive through the storage path. The hazard is the
`/retirement/gap_analysis?swr=...` route, which currently accepts any
float and divides by 100 before invoking the calculator. A negative
slider value silently collapses the analysis to zero with no error
feedback.

**C. Files modified**
- `app/schemas/validation.py` -- new `RetirementGapQuerySchema` (or
  add `Range(min=0, max=1)` to the existing query schema if one
  already exists; verify by reading the file).
- `app/routes/retirement.py` -- route the `/gap_analysis` query
  parameters through Marshmallow validation.
- `tests/test_services/test_retirement_gap_calculator.py:309-311` --
  remove the `BUG:` / `TODO:` comment block; update
  `test_safe_withdrawal_rate_negative` to assert the calculator
  remains permissive (no change to the calculator itself; the
  rejection happens at the route layer).
- `tests/test_routes/test_retirement.py` -- new test asserting a
  negative `swr` query parameter returns 422 with a validation error
  on the `swr` field.

**D. Implementation approach**
1. Read `app/routes/retirement.py` to find the `/gap_analysis`
   route, the current `swr` parameter parsing, and the
   `Decimal("100")` division site.
2. Read `app/schemas/validation.py` for the existing retirement
   schemas. If a query schema for gap analysis already exists, add
   `swr = fields.Decimal(allow_none=True, validate=Range(min=Decimal("0"), max=Decimal("1")))`
   to it. Otherwise add a new `RetirementGapQuerySchema` and have
   the route load query args through it.
3. Move the `Decimal("100")` division into the schema's `@pre_load`
   hook (matches the Commit 24 pattern; the route stops doing money
   math).
4. The route catches `ValidationError` from the schema load and
   returns 422 with the standard error envelope.
5. The calculator itself is unchanged -- it continues to treat
   non-positive SWR as zero internally (defensive depth).
6. Remove the `BUG:` / `TODO:` comment block from the test file.

**E. Test cases**
- C7-1 GET `/retirement/gap_analysis?swr=-5` returns 422; response
  body contains a `swr` field error.
- C7-2 GET `/retirement/gap_analysis?swr=0` returns 200; the
  calculator returns `required_retirement_savings = ZERO`
  (existing zero-rate behaviour preserved at the calculator).
- C7-3 GET `/retirement/gap_analysis?swr=4` (the default 4%
  slider) returns 200 and the standard gap result.
- C7-4 the `test_safe_withdrawal_rate_negative` calculator test
  still passes (calculator semantics unchanged).

**F. Manual verification steps**
1. Start the dev server; open `/retirement` in a browser; move the
   slider to a negative value via DevTools URL edit; assert a 422
   or graceful error message.
2. `grep -nF "BUG:" tests/test_services/test_retirement_gap_calculator.py`
   returns no matches.

**G. Downstream effects** Slider UI in the retirement template must
gracefully render 422 responses; if the slider already constrains to
`[0, 100]` client-side, the server-side rejection only fires on
URL-edited requests. Verify the slider min via
`grep -n "input.*range.*swr" app/templates/retirement/`.

**H. Rollback notes** `git revert`. Calculator behaviour is
unchanged; route-layer rejection is the only new contract.

---

### Commit 8 -- Defense-in-depth filter on `hard_delete_transfer_template` (F-14)

**A. Commit message** `fix(transfers): defense-in-depth filter on hard_delete_transfer_template (F-14)`

**B. Problem statement** Commit 21 (CRIT-05) fixed the predicate
`transfer_template_has_paid_history` to filter on `Status.is_settled`,
so the guard at `app/routes/transfers.py:629` now correctly catches
RECEIVED transfers. The destructive bulk-delete loop at
`app/routes/transfers.py:673-679` is therefore unreachable on the
happy path. However, the parallel `hard_delete_template`
(`app/routes/templates.py:636`) received the additional
defense-in-depth filter (`Transaction.status_id.notin_(settled_status_ids)`)
that constrains the bulk delete itself; the transfer-template route
did not. Mirror the templates.py pattern.

**C. Files modified**
- `app/routes/transfers.py::hard_delete_transfer_template`.
- `tests/test_routes/test_transfers.py` -- new route test.

**D. Implementation approach**
1. Read `app/routes/transfers.py` end-to-end for the
   `hard_delete_transfer_template` route and the
   `transfer_service.delete_transfer` call site at `:673-679`.
2. Read `app/routes/templates.py::hard_delete_template` for the
   `Transaction.status_id.notin_(settled_status_ids)` pattern as a
   reference shape.
3. Refactor `hard_delete_transfer_template` to:
   a. Build `settled_status_ids` scalar subquery from
      `Status.is_settled.is_(True)`.
   b. Partition the linked transfers into settled vs non-settled
      lists via `Transaction.status_id.in_(settled_status_ids)` /
      `notin_`.
   c. Loop `transfer_service.delete_transfer(soft=False)` only over
      the non-settled list.
   d. Surviving settled transfers retain their
      `transfer_template_id`; the column's FK is `ON DELETE SET NULL`
      so they survive as detached settled rows when the template is
      removed.
4. The test asserts the invariant: monkey-patch
   `transfer_template_has_paid_history` to return `False` so the
   guard short-circuits; POST the hard-delete; assert the settled
   transfer plus its two shadow rows survive with original amounts
   and statuses.

**E. Test cases**
- C8-1 monkey-patch `transfer_template_has_paid_history` to `False`;
  POST `/transfers/template/<id>/delete?hard=true`; assert the
  settled transfer and its two shadow transactions are still in
  the DB with their original amounts and `status_id` values.
- C8-2 the existing shadow-invariant test
  `test_hard_delete_preserves_shadow_invariant` continues to pass
  unchanged (filtered loop preserves invariants 1-5 for the rows
  it touches).
- C8-3 a non-settled (PROJECTED) transfer is still deleted by the
  bulk loop -- the filter is additive, not a wholesale block.

**F. Manual verification steps**
1. `grep -nF "notin_(settled_status_ids)" app/routes/transfers.py`
   returns the new filter site.
2. Full test suite green.

**G. Downstream effects** Transfers that were already hard-deletable
(non-settled) continue to be hard-deleted; settled transfers now
survive in the rare case where the upstream guard is bypassed.

**H. Rollback notes** `git revert`.

---

### Commit 9 -- Move companion entry pct to service helper (F-23)

**A. Commit message** `fix(companion): move entry pct derivation to service helper (F-23)`

**B. Problem statement** `app/routes/companion.py:53-57` computes
`pct = float(total / txn.estimated_amount * Decimal("100"))` inside
the route -- Decimal-only arithmetic cast through binary float to
satisfy the template's progress-bar width consumer. Two violations:
(a) money math in a route (MED-04 / E-16 invariant), and
(b) `float(Decimal_expression)` cast on monetary arithmetic. Mirror
the established `_safe_pct_complete` helper in
`app/services/dashboard_service.py:567-579`.

**C. Files modified**
- `app/services/entry_service.py` -- new helper
  `pct_complete(total: Decimal, target: Decimal) -> Decimal` that
  returns a Decimal capped at 100, matching `_safe_pct_complete`'s
  signature.
- `app/routes/companion.py` -- call the helper; remove the
  `float(...)` cast.
- `app/templates/companion/*.html` (or the consuming template) --
  ensure it formats the Decimal correctly; Jinja's default `{{ pct }}`
  renders a Decimal as its string representation, which is acceptable
  for CSS `width: {{ pct }}%`.
- `tests/test_routes/test_companion_routes.py` -- pin the rendered
  pct to a hand-computed Decimal value for at least one fixture.

**D. Implementation approach**
1. Read `app/services/dashboard_service.py:567-579` for the
   `_safe_pct_complete` reference shape.
2. Add `pct_complete(total: Decimal, target: Decimal) -> Decimal` to
   `app/services/entry_service.py`. Substantive docstring naming
   the MED-04 / E-16 standard. The function:
   ```python
   def pct_complete(total: Decimal, target: Decimal) -> Decimal:
       """Compute percent complete, clamped to [0, 100].

       Used by the entry-tracking surfaces (dashboard tile, companion
       row) to feed a CSS progress-bar width. Returns a Decimal so
       money math never crosses the Decimal/float boundary at the
       route layer (MED-04 / E-16). The two-decimal-place result is
       safe to render in CSS width values as-is.

       Args:
           total: Sum of entries against the budgeted line.
           target: Budgeted estimated amount; if <= 0 returns 0.

       Returns:
           Decimal in [0, 100] quantised to two decimal places.
       """
       if target <= ZERO:
           return ZERO
       pct = (total / target * Decimal("100")).quantize(
           Decimal("0.01"), rounding=ROUND_HALF_UP,
       )
       if pct > _HUNDRED:
           return Decimal("100.00")
       if pct < ZERO:
           return ZERO
       return pct
   ```
3. In `app/routes/companion.py:53-57`, replace the `float(...)` cast:
   ```python
   from app.services.entry_service import pct_complete
   # ... in _build_entry_data:
   pct = pct_complete(total, txn.estimated_amount)
   ```
4. Verify the template uses the value as a CSS width; the Decimal
   format `{{ pct }}` renders as `"55.50"` etc., which is valid CSS.
5. Pin `tests/test_routes/test_companion_routes.py`: for a fixture
   with `total=Decimal("55.50")`, `estimated_amount=Decimal("100.00")`,
   assert the rendered HTML contains `width: 55.50%` (or whatever
   the template's CSS shape is). Hand-arithmetic: `55.50 / 100 * 100 = 55.50`.

**E. Test cases**
- C9-1 `pct_complete(Decimal("50"), Decimal("100"))` returns
  `Decimal("50.00")`.
- C9-2 `pct_complete(Decimal("150"), Decimal("100"))` returns
  `Decimal("100.00")` (clamp).
- C9-3 `pct_complete(Decimal("50"), Decimal("0"))` returns
  `Decimal("0")` (guard).
- C9-4 companion row HTML for an entry-tracked transaction with
  total $55.50 against target $100.00 renders `55.50` in the
  progress-bar width attribute.

**F. Manual verification steps**
1. `grep -nF "float(" app/routes/companion.py` returns no matches in
   money contexts (the only legitimate float would be a non-money
   value).
2. Run the dev server; open `/companion`; visually inspect a
   progress bar at 50% and another at 100% (overflow case).

**G. Downstream effects** Companion progress bars render byte-
identical widths (same numeric value); other consumers of the entry
data dict unchanged (still get `total`, `remaining`, `count`, `pct`
keys).

**H. Rollback notes** `git revert`.

---

### Commit 10 -- Calendar per-day filter via balance-contributing predicate (F-3)

**A. Commit message** `fix(calendar): per-day filter via balance-contributing predicate (F-3)`

**B. Problem statement** `app/services/calendar_service.py::_assign_transactions_to_days`
classifies every non-deleted transaction returned by
`_query_transactions_for_range` (the only filter is `is_deleted=False`).
The grid uses the balance-contributing predicate
(`app/utils/balance_predicates.balance_contributing_clause`,
Commit 2 / E-15) to exclude Cancelled and Credit transactions. The
drift was named W-065 (HIGH-02 / F-004 cross-ref). Commit 9 fixed the
month-end balance; the per-day classification was left for a separate
design decision.

**Design choice locked at plan time:** balance-contributing predicate
(Projected + Settled, excludes Cancelled + Credit). Per-day totals
will intentionally differ from the grid's Projected-only subtotal --
the calendar shows realized payments at their settled date, which is
the calendar UX users expect. Re-pin existing calendar tests.

**C. Files modified**
- `app/services/calendar_service.py::_query_transactions_for_range`
  (add the predicate to the SQL filter).
- `app/services/calendar_service.py::_assign_transactions_to_days`
  (apply the predicate in the Python loop -- belt-and-suspenders so
  SQL and Python agree).
- `tests/test_services/test_calendar_service.py` -- re-pin per-day
  totals against hand-computed values for fixtures with mixed
  status; add a new test pinning the locked predicate so a regression
  to all-status would fail loud.

**D. Implementation approach**
1. Read `app/services/calendar_service.py:190-310` end-to-end for the
   query and classification path.
2. Read `app/utils/balance_predicates.py:334-359` for the
   `balance_contributing_clause()` signature.
3. In `_query_transactions_for_range`, add the predicate to the
   `.filter(...)` chain. The clause is a SQL boolean over
   `Status.is_balance_contributing.is_(True)`, so it composes with
   the existing `is_deleted=False` filter via `and_`.
4. In `_assign_transactions_to_days`, re-apply the same predicate in
   the Python loop so SQL and Python agree (the loop iterates
   already-filtered rows but the explicit guard prevents future
   regressions when the SQL filter is edited without the Python).
5. Add docstring lines naming the W-065 lock and the Choice-2
   semantic (calendar shows realized payments; calendar day-totals
   intentionally differ from grid Projected-only subtotal).

**E. Test cases**
- C10-1 fixture: one calendar day with one Projected $500 expense
  AND one Settled $200 expense. Calendar day-total expenses =
  `$700.00` (both contribute). Hand arithmetic: `500 + 200 = 700`.
- C10-2 same day plus a Cancelled $100 expense and a Credit $50
  expense. Day-total expenses still = `$700.00` (Cancelled and
  Credit excluded).
- C10-3 the same fixture viewed on the grid shows the Projected-only
  subtotal = `$500.00` for the period containing the day. The two
  surfaces intentionally diverge here (Choice 2 documented).
- C10-4 a regression that drops the predicate (simulated by reverting
  the SQL filter only) would cause C10-2 to fail with `$850.00`
  instead of `$700.00`.

**F. Manual verification steps**
1. `grep -nF "balance_contributing_clause" app/services/calendar_service.py`
   returns matches at the SQL filter and the Python loop sites.
2. Start the dev server; open `/calendar/2026/05` for a user with
   mixed-status fixture data; click into a day with mixed entries;
   confirm the displayed totals match the locked predicate.

**G. Downstream effects** Calendar per-day totals change for users
with Cancelled or Credit transactions on the affected dates (their
day-totals drop by the excluded amount). This is the locked
intentional behaviour change.

**H. Rollback notes** `git revert`. The predicate library is unchanged.

---

### Commit 11 -- Calendar raises on unresolvable account/scenario; remove dead branches (F-2)

**A. Commit message** `refactor(calendar): raise on unresolvable account/scenario; remove dead branches (F-2)`

**B. Problem statement** After Commit 3 (origination anchor backfill)
and Commit 4 (date-anchored resolver), the E-19 invariant is "anchor
is never NULL; `resolve_anchor` raises or returns a valid
`AnchorPoint`." Three branches in `app/services/calendar_service.py`
still short-circuit on the pre-E-19 NULL-anchor state and return
zeroed `MonthSummary` / `YearOverview` objects:

- `get_month_detail` (`:115-116`) -- `if account is None: return _empty_month(...)`.
- `get_month_detail` (`:118-120`) -- `if scenario is None: return _empty_month(...)`.
- `get_year_overview` (`:158, :162`) -- matching `_empty_year(year)` returns.
- `_empty_month` (`:474`) -- the zeroed factory, including a
  hardcoded `projected_end_balance=Decimal("0")` (the
  "D6-02 fifth anchor-None behavior" the audit named).

Treating an unresolvable account / scenario as a zeroed calendar
masks the upstream defect; the user sees `$0.00` with no error.

**C. Files modified**
- `app/services/calendar_service.py` -- raise a specific exception
  for unresolvable account / scenario; delete `_empty_month` /
  `_empty_year`.
- `app/routes/calendar.py` (or wherever the route lives -- read to
  confirm) -- catch the exception and return a 404 response.
- `tests/test_services/test_calendar_service.py` -- update tests
  that relied on the implicit zeroed return.
- `tests/test_routes/test_calendar.py` -- assert the 404 response
  shape.

**D. Implementation approach**
1. Read `app/services/calendar_service.py` end-to-end to identify
   every caller of `_empty_month` / `_empty_year` and every branch
   that short-circuits on `account is None` / `scenario is None`.
2. Define a new exception class
   `CalendarAccountNotResolvableError(LookupError)` in the same
   module (or in `app/services/exceptions.py` if a sibling exception
   module exists).
3. Replace each short-circuit:
   ```python
   if account is None:
       raise CalendarAccountNotResolvableError(
           f"Analytics account not resolvable for user={user_id} year={year}",
       )
   if scenario is None:
       raise CalendarAccountNotResolvableError(
           f"Baseline scenario not resolvable for user={user_id}",
       )
   ```
4. Delete `_empty_month` and `_empty_year` (no remaining caller).
5. Read the calendar route(s) (`grep -rn "calendar" app/routes/`)
   and add a `try: ... except CalendarAccountNotResolvableError`
   that returns `abort(404)` -- matches the "404 for both 'not
   found' and 'not yours'" security rule.
6. Update existing tests that relied on the zeroed `MonthSummary`
   contract to either (a) raise the exception and assert the 404
   response, or (b) set up a valid account / scenario fixture.

**E. Test cases**
- C11-1 calendar route returns 404 when `resolve_analytics_account`
  returns `None` (simulated by deleting the user's only checking
  account before the request).
- C11-2 calendar route returns 404 when the baseline scenario is
  missing (simulated by deleting the user's scenarios).
- C11-3 calendar route returns 200 with the correct summary for a
  user with a valid account and scenario (locks the happy path).
- C11-4 `grep -nF "_empty_month" app/services/calendar_service.py`
  returns no matches (dead factory deleted).
- C11-5 `grep -nF '"0.00"' app/services/calendar_service.py` and
  `grep -nF 'Decimal("0")' app/services/calendar_service.py` show no
  hardcoded zero summaries.

**F. Manual verification steps**
1. Start the dev server; delete the user's checking account; open
   `/calendar/2026/05`; assert 404 (or 302 to dashboard with a flash
   message -- depends on the route's error rendering).
2. Restore the account; assert the calendar renders normally.

**G. Downstream effects** Users on a freshly-created account with no
analytics target will see a 404 instead of a zeroed calendar -- but
the route's `resolve_analytics_account` should already short-circuit
to "no accounts" before hitting the calendar service. Verify by
walking the route in the dev server.

**H. Rollback notes** `git revert`. Re-introducing `_empty_month` is
trivial if the route-layer handling proves user-hostile.

---

### Commit 12 -- Unify percent conversion at @pre_load for investment + pension (F-17)

**A. Commit message** `refactor(schemas): unify percent conversion at @pre_load for investment + pension (F-17)`

**B. Problem statement** Commit 24 (HIGH-06) standardised the percent-
to-fraction conversion at the schema `@pre_load` boundary for six
schemas (InterestParams, LoanParams, RateChange, Refinance,
EscrowComponent, UserSettings). Two pre-existing schema families
still convert at the route layer:

- `app/routes/investment.py:319-337` --
  `_convert_percentage_inputs` rewrites the form payload before
  `schema.load` for `InvestmentParamsCreateSchema` /
  `InvestmentParamsUpdateSchema`.
- `app/routes/retirement.py:117-118, :213-214, :348-351` --
  inline `Decimal("100")` divisions for `benefit_multiplier`,
  `safe_withdrawal_rate`, `estimated_retirement_tax_rate`.

Both paths produce correct results today (the schemas validate the
fraction, the DB CHECK accepts the same fraction). The inconsistency
is stylistic; collapsing it removes the carve-out in
`app/schemas/validation.py`'s docstring and the bespoke conversion
helpers in routes.

**C. Files modified**
- `app/schemas/validation.py` --
  - Add `_PERCENT_FIELDS` and `@pre_load normalize_inputs` to
    `InvestmentParamsCreateSchema`, `InvestmentParamsUpdateSchema`,
    `PensionProfileCreateSchema`, `PensionProfileUpdateSchema`,
    `RetirementSettingsSchema`.
  - Remove the "Two pre-existing schemas..." carve-out paragraph
    from the module-level docstring.
- `app/routes/investment.py` -- delete `_convert_percentage_inputs`
  and its call sites.
- `app/routes/retirement.py` -- delete the three inline
  `Decimal("100")` divisions; the schemas now own the conversion.
- `tests/test_routes/test_investment.py`,
  `tests/test_routes/test_retirement.py` -- existing tests should
  pass unchanged (same wire-level behaviour); add one test per
  schema that POSTs percent input and asserts the stored fraction.

**D. Implementation approach**
1. Read `app/schemas/validation.py` in full for the Commit-24
   `_PERCENT_FIELDS` / `_normalize_percent_fields` pattern.
2. Read `app/routes/investment.py:319-337` for the existing
   `_convert_percentage_inputs` to map which fields convert.
3. Read `app/routes/retirement.py:117-118, :213-214, :348-351` for
   the three inline divisions and their field names.
4. For each target schema, add:
   ```python
   _PERCENT_FIELDS = ("assumed_return", "expense_ratio", ...)

   @pre_load
   def normalize_inputs(self, data, **kwargs):
       """Convert user-facing percent inputs to stored fractions.

       Matches the Commit-24 / HIGH-06 convention: schemas own
       the percent-to-fraction conversion, routes do no money
       math. `app/utils/money.MONTHS_PER_YEAR`-style canonical
       constants live in `app.utils.money`.
       """
       data = strip_empty_strings(data)
       return _normalize_percent_fields(data, self._PERCENT_FIELDS)
   ```
5. Delete `_convert_percentage_inputs` from
   `app/routes/investment.py` and its call sites.
6. Delete the three `Decimal("100")` divisions from
   `app/routes/retirement.py`.
7. Read `app/schemas/validation.py` module-level docstring; remove
   the "Two pre-existing schemas..." carve-out paragraph.

**E. Test cases**
- C12-1 POST `/investment/<id>/params/update` with
  `assumed_return=7.5` (the percent input); assert the stored
  `assumed_return = Decimal("0.075")`.
- C12-2 POST `/retirement/pension/create` with
  `benefit_multiplier=2.5` (percent); assert stored
  `Decimal("0.025")`.
- C12-3 POST `/retirement/settings/update` with
  `safe_withdrawal_rate=4` and `estimated_retirement_tax_rate=22`;
  assert stored `Decimal("0.04")` and `Decimal("0.22")` respectively.
- C12-4 the existing investment / retirement test suites pass
  unchanged (same wire contract).

**F. Manual verification steps**
1. `grep -nF "_convert_percentage_inputs" app/routes/`
   returns no matches.
2. `grep -nF "Decimal(\"100\")" app/routes/retirement.py`
   returns no matches in money-conversion contexts (any remaining
   match is unrelated, e.g. a comment).
3. `grep -nF "Two pre-existing" app/schemas/validation.py` returns
   no matches.
4. Full test suite green.

**G. Downstream effects** Routes stop manipulating form payloads;
schema-side conversion is now universal across every rate /
percent input. Future schemas inherit the pattern.

**H. Rollback notes** `git revert`. The route-layer conversion can
be re-introduced from history if the schema pattern fails any test.

---

### Commit 13 -- Add upper-bound CHECK on `loan_params.interest_rate` (F-18) -- DESTRUCTIVE

**A. Commit message** `fix(schema): add upper-bound CHECK on loan_params.interest_rate (F-18)`

**B. Problem statement** `app/models/loan_params.py:63-66` defines
only `CHECK(interest_rate >= 0)`, while the three sibling rate
columns enforce both bounds:

- `interest_params.apy`: `>= 0 AND <= 1`
- `loan_features.rate_history.rate`: `>= 0 AND <= 1`
- `investment_params.assumed_return`: `>= -1 AND <= 1`
- `loan_features.escrow_inflation_rate`: `IS NULL OR (>= 0 AND <= 1)`

The Marshmallow schema (Commit 24) pins `Range(0, 1)` on
`interest_rate`, so the application tier rejects any percent input
that would produce a fraction > 1. The database tier does not, so a
raw-SQL writer (or a future bug that bypasses the schema) could
commit a fraction of e.g. `Decimal("9.5")` (950% APR) with no
storage-tier rejection. Defense-in-depth requires the CHECK at the
DB tier too.

**This is a destructive migration:** approved at plan time by the
developer's scope selection.

**C. Files modified**
- `app/models/loan_params.py` -- add the second CHECK constraint
  to `__table_args__`.
- `migrations/versions/<new>_loan_params_interest_rate_upper.py`
  (new) -- the migration.
- `tests/test_routes/test_loan.py` -- assert that a raw-SQL INSERT
  bypassing the schema and writing `interest_rate = 9.5` fails with
  `IntegrityError` (locks the storage-tier guarantee).

**D. Implementation approach**
1. Read `app/models/loan_params.py:60-72` for the existing
   `__table_args__` shape and naming convention.
2. Add the new constraint:
   ```python
   db.CheckConstraint(
       "interest_rate IS NULL OR interest_rate <= 1",
       name="ck_loan_params_interest_rate_upper",
   ),
   ```
   `IS NULL OR ...` preserves the E-18 / Commit 15 demotion that
   made the column nullable.
3. Generate the migration with
   `flask db migrate -m "add upper-bound CHECK on loan_params.interest_rate (F-18)"`.
4. Review the autogenerated migration -- it should be a single
   `op.create_check_constraint('ck_loan_params_interest_rate_upper', 'loan_params', 'interest_rate IS NULL OR interest_rate <= 1', schema='budget')`.
5. **Pre-check in the migration body:** before the
   `create_check_constraint`, run a `SELECT COUNT(*) FROM
   budget.loan_params WHERE interest_rate > 1` and raise a
   `RuntimeError` with the diagnostic if any row violates the new
   bound. This makes the upgrade safe across staging / disaster-
   recovery replays:
   ```python
   def upgrade():
       conn = op.get_bind()
       violations = conn.execute(
           sa.text(
               "SELECT COUNT(*) FROM budget.loan_params "
               "WHERE interest_rate > 1"
           )
       ).scalar()
       if violations:
           raise RuntimeError(
               f"Cannot add CHECK ck_loan_params_interest_rate_upper: "
               f"{violations} row(s) violate the bound. Run "
               f"`SELECT id, interest_rate FROM budget.loan_params "
               f"WHERE interest_rate > 1` and decide whether to clamp "
               f"or delete before re-running."
           )
       op.create_check_constraint(
           'ck_loan_params_interest_rate_upper',
           'loan_params',
           'interest_rate IS NULL OR interest_rate <= 1',
           schema='budget',
       )

   def downgrade():
       op.drop_constraint(
           'ck_loan_params_interest_rate_upper',
           'loan_params',
           type_='check',
           schema='budget',
       )
   ```
6. Add the `Review:` line to the module-level docstring:
   `Review: solo developer, 2026-05-21 (audit follow-up plan, F-18 destructive migration approved at plan time).`
7. After running `flask db upgrade`, rebuild the test template:
   `python scripts/build_test_template.py`.

**E. Test cases**
- C13-1 raw-SQL `INSERT INTO budget.loan_params (..., interest_rate, ...) VALUES (..., 9.5, ...)`
  raises `IntegrityError` mentioning
  `ck_loan_params_interest_rate_upper`.
- C13-2 INSERT with `interest_rate = 1.0` succeeds (boundary).
- C13-3 INSERT with `interest_rate = NULL` succeeds (E-18 demotion
  preserved).
- C13-4 migration `flask db upgrade` then `flask db downgrade -1`
  then `flask db upgrade` round-trips cleanly (no schema drift).

**F. Manual verification steps**
1. `flask db upgrade` shows the new migration applied.
2. Connect to `psql` and verify:
   `\d+ budget.loan_params` shows both
   `ck_loan_params_interest_rate` and
   `ck_loan_params_interest_rate_upper`.
3. `python scripts/build_test_template.py` succeeds and reports
   the expected trigger count.
4. Full test suite green.

**G. Downstream effects** Any future code path that writes
`interest_rate > 1` will fail at the storage tier with a clean
`IntegrityError` instead of silently storing nonsense. The
application-tier Marshmallow `Range(0, 1)` continues to surface a
422 first; the storage CHECK is belt-and-suspenders.

**H. Rollback notes** `flask db downgrade -1` drops the CHECK
constraint; no data loss. `git revert` reverts the model change.
After downgrade, rebuild the template: `python scripts/build_test_template.py`.

---

### Commit 14 -- Gate engine anchor-reset payment recompute on `not using_contractual` (F-8)

**A. Commit message** `fix(engine): gate anchor-reset payment recompute on not using_contractual (F-8)`

**B. Problem statement** `app/services/amortization_engine.py:486-493`
unconditionally re-computes `monthly_payment` at the anchor reset:

```python
if (anchor_balance is not None and anchor_date is not None
        and not anchor_applied and pay_date > anchor_date):
    balance = anchor_balance
    anchor_applied = True
    months_left = max_months - month_num + 1
    monthly_payment = calculate_monthly_payment(
        balance, current_annual_rate, months_left,
    )
```

For ARM loans (`using_contractual=False`) this is correct -- re-
amortization at the anchor is intentional. For fixed-rate loans
(`using_contractual=True`), `max_months = remaining_months +
term_months` is a generous upper bound for the early-payoff case,
so `months_left` at the anchor reset is roughly `2 * term_months`,
which produces a payment about half the contractual amount. Every
subsequent row in the schedule then uses the wrong payment.

The bug is **unreachable in production today** because every call
site that passes anchor parameters explicitly skips the anchor for
fixed-rate loans (`anchor_bal_planned = state.current_balance if params.is_arm else None`).
Closing the gap lets fixed-rate trueup events (Commit 16) safely
project from the corrected balance.

**C. Files modified**
- `app/services/amortization_engine.py:486-493` -- two-line gate.
- `tests/test_services/test_amortization_engine.py` -- new test
  covering the fixed-rate anchor pathway.

**D. Implementation approach**
1. Read `app/services/amortization_engine.py:430-510` for the full
   `generate_schedule` body and the `using_contractual` variable's
   definition.
2. Edit the anchor-reset block:
   ```python
   if (anchor_balance is not None and anchor_date is not None
           and not anchor_applied and pay_date > anchor_date):
       balance = anchor_balance
       anchor_applied = True
       if not using_contractual:
           months_left = max_months - month_num + 1
           monthly_payment = calculate_monthly_payment(
               balance, current_annual_rate, months_left,
           )
   ```
   For fixed-rate loans, the contractual `monthly_payment` (set at
   loop entry from `compute_contractual_pi`) remains in force --
   exactly the post-condition every fixed-rate consumer expects.
3. Add a docstring line above the gate naming F-8 and explaining
   why ARM re-amortizes but fixed-rate does not.

**E. Test cases**
- C14-1 fixed-rate loan, anchor at origination,
  `original_principal=Decimal("400000")`, `interest_rate=Decimal("0.06")`,
  `term_months=360`. Generate the schedule with the anchor passed
  explicitly. Every row's `payment_amount` equals
  `calculate_monthly_payment(Decimal("400000"), Decimal("0.06"), 360)`
  = `Decimal("2398.20")`. Without the gate, post-anchor rows would
  use ~`Decimal("1199.10")` (half). Hand arithmetic:
  `M = 400000 * (0.06/12) / (1 - (1 + 0.06/12)^-360) = 2398.2046...`
  -> rounded to `Decimal("2398.20")`.
- C14-2 ARM loan, anchor mid-loan, balance diverges from from-
  origination projection. The anchor-reset DOES re-amortize (the
  pre-existing ARM behaviour preserved); pin one row's payment to
  the hand-computed value.
- C14-3 the existing engine test suite passes unchanged (no
  regression on the ARM path).

**F. Manual verification steps**
1. `grep -nF "if not using_contractual:" app/services/amortization_engine.py`
   returns the new gate.
2. Full test suite green.

**G. Downstream effects** The Commit-16 loan-anchor-event UX can
safely pass anchor parameters for fixed-rate loans too, knowing the
contractual payment is preserved through the anchor reset.

**H. Rollback notes** `git revert`.

---

### Commit 15 -- Delete dead `get_loan_projection` / `calculate_balances_with_amortization` (F-10)

**A. Commit message** `refactor(loan): delete dead get_loan_projection / calculate_balances_with_amortization (F-10)`

**B. Problem statement** Commit 15 of the main remediation
("Refactor loan to demote columns; route consumers through resolver")
verified that no production reader still hits the demoted columns,
but the verification gate explicitly scoped only the display paths.
Three engine-internal readers remain:

- `app/services/amortization_engine.py:941-942` -- `get_loan_projection`
  reads `params.current_principal` and `params.interest_rate`.
  Orphaned in production after Commit 15 (zero callers); kept alive
  by `tests/test_services/test_amortization_engine.py::TestGetLoanProjection`
  and the ARM-projection tests.
- `app/services/balance_calculator.py:227` --
  `calculate_balances_with_amortization` reads
  `loan_params.current_principal`. Orphaned in production after
  Commit 8 routed savings-dashboard through `balance_resolver`;
  kept alive by `tests/test_services/test_balance_calculator_debt.py`.
- `app/services/loan_payment_service.py:261-262` --
  `compute_contractual_pi` reads both columns to compute the
  "above-P&I excess" boundary used by `prepare_payments_for_engine`'s
  escrow subtraction. Still actively called by `load_loan_context`.

Pragmatically, the first two are dead production code; the third is
an internal helper. Closing the gap removes the last residual reads
so the OPT-1 destructive-drop migration (if ever promoted) becomes
trivial.

**C. Files modified**
- `app/services/amortization_engine.py` -- delete
  `get_loan_projection` function AND the `LoanProjection` dataclass.
- `app/services/balance_calculator.py` -- delete
  `calculate_balances_with_amortization` function.
- `app/services/loan_payment_service.py::compute_contractual_pi` --
  compute the escrow boundary from `original_principal` and the
  BASE `interest_rate` (which remains a required Marshmallow input,
  hence non-NULL in practice; reading it is allowed because the
  resolver itself reads it as the base rate via
  `_rate_at_date` fallback).
- `tests/test_services/test_amortization_engine.py` -- delete the
  `TestGetLoanProjection` class; if any sibling tests rely on it,
  rewrite them to drive `loan_resolver.resolve_loan` directly.
- `tests/test_services/test_balance_calculator_debt.py` -- delete
  the entire file (no remaining production target).

**D. Implementation approach**
1. Read each of the three engine files in full to understand
   each function's signature and callers.
2. Run `grep -rn "get_loan_projection\|calculate_balances_with_amortization" /home/josh/projects/Shekel/`
   to confirm no remaining production caller.
3. Delete `get_loan_projection` and the `LoanProjection` dataclass
   from `amortization_engine.py`. Update the module docstring if it
   references the deleted symbols.
4. Delete `calculate_balances_with_amortization` from
   `balance_calculator.py`. Update the module docstring.
5. Rewrite `compute_contractual_pi`:
   ```python
   def compute_contractual_pi(params: LoanParams) -> Decimal:
       """Compute the contractual P&I payment from the seed columns.

       Uses `original_principal` (always non-NULL) and `interest_rate`
       (resolver's BASE rate fallback; required at insert by the
       Marshmallow schema, so non-NULL in practice). Replaces the
       pre-F-10 read of `current_principal` which became
       non-authoritative under E-18 / Commit 15.
       """
       if params.original_principal is None or params.interest_rate is None:
           return Decimal("0")
       return calculate_monthly_payment(
           Decimal(str(params.original_principal)),
           Decimal(str(params.interest_rate)),
           params.term_months,
       )
   ```
   This matches the original boundary EXACTLY for fixed-rate loans
   (whose contractual payment is always derived from
   original_principal + base rate + term). For ARMs the boundary is
   slightly less precise (uses base rate instead of current-rate-at-
   reset), but the boundary is a heuristic for escrow subtraction
   ordering and never directly affects the resolver's output
   (verified by the existing escrow tests).
6. Delete `tests/test_services/test_balance_calculator_debt.py`
   entirely.
7. Delete the `TestGetLoanProjection` class from
   `tests/test_services/test_amortization_engine.py`. For any
   sibling ARM tests that depended on `get_loan_projection`,
   rewrite to call `loan_resolver.resolve_loan` directly.

**E. Test cases**
- C15-1 the full engine test suite passes (every test that
  exercised `get_loan_projection` is either deleted or rewritten
  to drive the resolver).
- C15-2 `compute_contractual_pi(LoanParams(...))` returns the same
  Decimal for a fixed-rate loan before and after the refactor
  (hand-computed: `400000 * (0.06/12) / (1 - (1.005)^-360) = 2398.20`).
- C15-3 the escrow subtraction tests in
  `tests/test_services/test_loan_payment_service.py` pass unchanged
  (the boundary heuristic still orders escrow correctly).
- C15-4 the integration tests in
  `tests/test_integration/test_loan_principal_settles.py` and
  `tests/test_integration/test_loan_resolver_arm.py` (or wherever
  the resolver integration lives) pass unchanged.

**F. Manual verification steps**
1. `grep -rn "get_loan_projection\|calculate_balances_with_amortization\|LoanProjection" /home/josh/projects/Shekel/`
   returns empty (or matches only deleted comments).
2. `grep -rn "\.current_principal" app/ | grep -v migrations | grep -v loan_resolver | grep -v loan_anchor_event | grep -v models/`
   returns empty.
3. `grep -rn "\.interest_rate" app/ | grep -v migrations | grep -v loan_resolver | grep -v loan_anchor_event | grep -v models/ | grep -v schemas/`
   returns only legitimate sites (the new `compute_contractual_pi`
   read and any docstring references).
4. Full test suite green.

**G. Downstream effects** The post-Commit-15 OPT-1 destructive-drop
of the demoted columns is now trivial -- no remaining production
reader.

**H. Rollback notes** `git revert`. Deleted functions can be restored
from history.

---

### Commit 16 -- Year-end investment projection via contribution timeline (F-19)

**A. Commit message** `fix(year-end): investment projection via contribution timeline (F-19)`

**B. Problem statement** `app/services/investment_projection.py:142-161`
(`calculate_investment_inputs` Step 2) averages all transfers by
distinct pay-period count: a lump-sum transfer of $23,300 plus a
recurring $1,500 deduction yields a "per-period contribution" of
$24,800, which the year-end projection then applies to every period
in the year. The dashboard route partially compensates by passing a
real-per-period `contributions` timeline (via
`build_contribution_timeline`); the year-end
`_project_investment_for_year` does NOT, so it consumes the inflated
`periodic_contribution` directly.

**Locked direction:** Option 1 (year-end uses the contribution timeline).
No change to `calculate_investment_inputs`; year-end mirrors the
dashboard's already-correct shape.

**C. Files modified**
- `app/services/year_end_summary_service.py::_project_investment_for_year`
  -- call `build_contribution_timeline` over the year's pay periods
  and pass the result to `growth_engine.project_balance`.
- `tests/test_services/test_year_end_summary_service.py` -- new
  fixture: one lump-sum settled transfer + one recurring deduction;
  pin the year-end employer/growth totals to hand-computed values
  that respect the per-period contribution history.

**D. Implementation approach**
1. Read `app/services/year_end_summary_service.py:1031-1180` for the
   full `_project_investment_for_year` body, including how it
   constructs the `InvestmentInputs` passed to `project_balance`.
2. Read `app/services/investment_projection.py` and the dashboard
   route consumer to understand `build_contribution_timeline`'s
   signature: `(deductions, transfers, periods) -> dict[period_id, Decimal]`
   (verify by reading the function).
3. Read `app/services/growth_engine.project_balance` to confirm it
   accepts a `contributions` parameter that overrides the
   `periodic_contribution` fallback.
4. Refactor `_project_investment_for_year`:
   ```python
   # Build the per-period contribution timeline.
   contributions = build_contribution_timeline(
       deductions, all_contributions, year_periods,
   )
   # Pass it to the engine instead of (or alongside)
   # periodic_contribution.
   projection = project_balance(
       starting_balance=current_balance,
       contributions=contributions,
       # ... other args unchanged
   )
   ```
5. Hand-compute the expected year-end employer match and growth
   for the new fixture; re-pin the assertion.

**E. Test cases**
- C16-1 fixture: one settled lump-sum 401(k) transfer of $23,300 on
  the last day of the year + one recurring per-paycheck $1,500
  401(k) deduction. Employer match is 50% of employee contribution
  capped at 6% of gross. With a $100k annual salary
  (`gross_biweekly = 3846.15`), the cap is `6% * 3846.15 = 230.77`.
  Year-end employer match for the 25 recurring periods =
  `min(1500, 230.77) * 0.50 * 25 = $2,884.63`. The lump-sum period
  also gets capped at $230.77 / 2 = $115.38. Total = `~$3,000.01`.
  Pre-fix year-end overstated this because the inflated
  `periodic_contribution = $24,800` exceeded the cap in every
  period; post-fix the cap is correctly applied per-period.
- C16-2 fixture with only recurring deductions (no lump sums) --
  the year-end output is byte-identical pre vs post (the timeline
  for this case collapses to the same per-period value).
- C16-3 the dashboard chart for the same fixture remains byte-
  identical (the dashboard already used the timeline; no regression).

**F. Manual verification steps**
1. `grep -nF "build_contribution_timeline" app/services/year_end_summary_service.py`
   returns the new call site.
2. Full test suite green.

**G. Downstream effects** Year-end employer-match and investment-
growth totals for users with lump-sum contributions change to the
correct values; users with only recurring contributions see no
change.

**H. Rollback notes** `git revert`.

---

### Commit 17 -- Canonical raise-aware gross-biweekly helper (F-20)

**A. Commit message** `refactor(income): canonical raise-aware gross-biweekly helper (F-20)`

**B. Problem statement** Commit 26 of the main remediation routed the
savings-dashboard DTI denominator through the raise-aware paycheck
engine. Six sibling call sites still read the off-engine
`annual_salary / pay_periods` quantity with no raise application:

- `app/services/savings_dashboard_service.py:304-314` --
  `_load_account_params` (the producer).
- `app/services/year_end_summary_service.py:2047-2075` --
  `_load_salary_gross_biweekly`.
- `app/services/retirement_dashboard_service.py:447-450`.
- `app/services/investment_dashboard_service.py:128-133` --
  `_salary_gross_biweekly`.
- Several consumer call sites in the same files (passes the value
  forward).

For users with applicable `SalaryRaise` rows, the value diverges
from the paycheck engine's per-period gross by the raise factor;
the employer-match / investment-projection / retirement-gap / year-
end-employer figures drift by the same factor (the audit's F-032
worked example: `$107,120` vs `$104,000` = ~2.99% understatement).

**Locked direction:** Option 1 (lift the engine call to one service).
Every consumer that wants the raise-aware per-period gross calls one
canonical function that wraps `calculate_paycheck`.

**C. Files modified**
- `app/services/income_service.py` (new) -- one function
  `get_current_gross_biweekly(user_id, scenario, *, as_of=None) -> Decimal`
  that wraps `calculate_paycheck` and returns the raise-aware
  per-period gross. The helper reads the active salary profile,
  invokes the paycheck engine for the current pay period (or the
  caller-supplied `as_of` date), and returns
  `PaycheckBreakdown.gross_biweekly`.
- The four consumer files above -- replace the inline off-engine
  division with a call to `income_service.get_current_gross_biweekly`.
- Tests pinning the raise-applicable path for each consumer.

**D. Implementation approach**
1. Read `app/services/paycheck_calculator.calculate_paycheck` to
   understand its full signature and side effects. Read Commit 26's
   `_get_current_paycheck_breakdown` (if it exists) for the
   reference pattern.
2. Create `app/services/income_service.py`:
   ```python
   """Single source of truth for raise-aware income quantities.

   Wraps `paycheck_calculator.calculate_paycheck` so every
   consumer that wants the per-period gross gets the same
   raise-applied value. Pre-F-20, six call sites computed it as
   `Decimal(str(profile.annual_salary)) / (profile.pay_periods_per_year or 26)`,
   which silently understated income for any user with an
   applicable SalaryRaise row.
   """
   from datetime import date
   from decimal import Decimal

   from app.models import SalaryProfile, Scenario
   from app.services import paycheck_calculator
   from app.utils.money import ZERO

   def get_current_gross_biweekly(
       user_id: int,
       scenario: Scenario,
       *,
       as_of: date | None = None,
   ) -> Decimal:
       """Return the raise-aware gross-per-period income.

       Args:
           user_id: User whose salary profile to load.
           scenario: Baseline scenario for `SalaryRaise` resolution.
           as_of: Optional date for which to compute the gross;
               defaults to today. Pass the period start_date when
               computing per-period quantities in a fixed-period
               context.

       Returns:
           The paycheck-engine `gross_biweekly` for the active
           salary profile at `as_of`. `Decimal("0")` if the user
           has no active salary profile.
       """
       profile = (
           SalaryProfile.query
           .filter_by(user_id=user_id, scenario_id=scenario.id, is_active=True)
           .first()
       )
       if profile is None:
           return ZERO
       breakdown = paycheck_calculator.calculate_paycheck(
           profile=profile,
           pay_date=as_of or date.today(),
           scenario=scenario,
       )
       return breakdown.gross_biweekly
   ```
   (Adjust the `calculate_paycheck` signature to match the actual
   function; read it first.)
3. Replace each consumer's inline division with a call to the
   helper. Preserve the `salary_gross_biweekly` parameter name in
   downstream signatures so callers' fixtures stay valid; only the
   producer changes.
4. For `_load_salary_gross_biweekly` in
   `year_end_summary_service.py`, the function becomes a thin
   wrapper that delegates to `income_service.get_current_gross_biweekly`.
   Consider deleting the wrapper if all callers can switch to the
   canonical helper directly.

**E. Test cases**
- C17-1 raise-applicable fixture: user with `annual_salary = $104,000`
  and a `SalaryRaise` of 3% effective in period N. For period N+1:
  - Pre-fix: `104000 / 26 = $4,000.00` per period (no raise).
  - Post-fix: `income_service.get_current_gross_biweekly(...)`
    returns `104000 * 1.03 / 26 = $4,120.00` per period.
  Year-end employer match, retirement-gap, and investment-projection
  consumers all reflect the raise.
- C17-2 no-raise fixture: pre and post values are byte-identical.
- C17-3 no-active-profile fixture: returns `Decimal("0")` (matches
  the pre-fix `Decimal("0")` fallback).
- C17-4 dashboard / year-end / retirement integration tests pin
  one raise-applicable scenario across all four consumers, asserting
  they read the same engine-derived value.

**F. Manual verification steps**
1. `grep -nE "Decimal\(str\([^)]*annual_salary[^)]*\)\)\s*/" app/services/ app/routes/`
   returns no matches outside `income_service.py` and
   `paycheck_calculator.py` (the engine itself).
2. Open the dev server as a user with a recent raise; verify the
   savings DTI, retirement-gap denominator, investment-projection
   contribution cap, and year-end employer-match all reflect the
   raised salary.
3. Full test suite green.

**G. Downstream effects** Users with applicable raises see corrected
income-derived values on every dashboard. The corrected values are
ALWAYS higher than the pre-fix (raises are positive), so no user
will see a regression to a worse number; the only delta is the
under-stated values increasing.

**H. Rollback notes** `git revert`. Per-consumer rollback is also
possible by reverting individual consumer-file changes; the helper
stays inert until callers re-adopt it.

---

### Commit 18 -- Extract shared deduction-loader and projection-inputs helpers (F-22)

**A. Commit message** `refactor(investment): extract shared deduction-loader + projection-inputs helpers (F-22)`

**B. Problem statement** Pylint R0801 (similar-lines) flags two
duplicates across the investment / retirement / savings dashboards:

- `_load_deductions_for_account`-shaped active-paycheck-deduction
  filter query duplicated across `investment_dashboard_service`
  (`:146-153`), `retirement_dashboard_service` (`:416-422`), and
  `savings_dashboard_service` (`:289-295`).
- The `calculate_investment_inputs(...)` kwargs splat (six identical
  keyword arguments) duplicated across the same three services
  (`investment_dashboard_service:258-263`,
  `retirement_dashboard_service:518-525`,
  `savings_dashboard_service:564-569`).

Both are inherent to the engine API shape, not a logic duplication,
but consolidating them removes the R0801 warning and centralises the
engine-input contract.

**Direction:** Option B (introduce a shared
`build_investment_projection_inputs` helper in
`app/services/investment_projection.py` that takes account context
and returns engine-ready inputs). Closes both duplicates at the cost
of one larger touch.

**C. Files modified**
- `app/services/investment_projection.py` (or a new
  `app/services/projection_inputs.py`) -- new helper functions
  `load_active_deductions_for_account(user_id, account_id) -> list[PaycheckDeduction]`
  and `build_investment_projection_inputs(account, params, user_id, all_periods, current_period) -> InvestmentInputs`.
- `app/services/investment_dashboard_service.py` -- delete
  `_load_deductions_for_account`; route through the shared helpers.
- `app/services/retirement_dashboard_service.py` -- same.
- `app/services/savings_dashboard_service.py` -- same.

**D. Implementation approach**
1. Read all three current implementations side-by-side to confirm
   the queries are byte-identical (same filters, same joins).
2. Read the `calculate_investment_inputs` signature and the six-
   keyword splat to design the helper's return DTO. If
   `InvestmentInputs` already exists as a dataclass (it should --
   verify), the helper returns it directly.
3. Add the two helpers to `investment_projection.py` (the natural
   home; it already owns `calculate_investment_inputs`):
   ```python
   def load_active_deductions_for_account(
       user_id: int,
       account_id: int,
   ) -> list[PaycheckDeduction]:
       """Load active paycheck deductions targeting one investment account."""
       return (
           db.session.query(PaycheckDeduction)
           .join(SalaryProfile)
           .filter(
               SalaryProfile.user_id == user_id,
               SalaryProfile.is_active.is_(True),
               PaycheckDeduction.target_account_id == account_id,
               PaycheckDeduction.is_active.is_(True),
           )
           .all()
       )

   def build_investment_projection_inputs(
       account: Account,
       params: InvestmentParams,
       user_id: int,
       all_periods: list[PayPeriod],
       current_period: PayPeriod,
       salary_gross_biweekly: Decimal,
   ) -> InvestmentInputs:
       """Build the engine-ready InvestmentInputs for one account."""
       deductions = load_active_deductions_for_account(user_id, account.id)
       contributions = _load_active_contributions(...)  # whatever existing helper
       return calculate_investment_inputs(
           deductions=deductions,
           all_contributions=contributions,
           all_periods=all_periods,
           current_period=current_period,
           salary_gross_biweekly=salary_gross_biweekly,
       )
   ```
4. Replace each consumer's deductions query and kwargs splat with
   a call to the shared helper.
5. Verify the consumers' downstream code (e.g. each dashboard's
   custom adaptation of deductions) is preserved -- only the
   loading step is centralised.

**E. Test cases**
- C18-1 pylint R0801 over the three services shows no duplicate
  matching the deductions query or the kwargs splat (confirmed
  by `pylint --disable=all --enable=R0801 app/services/`).
- C18-2 each dashboard's existing test suite passes unchanged
  (outputs byte-identical).
- C18-3 a fixture with one user-deduction and one
  account-contribution returns the same `InvestmentInputs` from
  the helper as from the previous inline construction (lock test).

**F. Manual verification steps**
1. `grep -nF "_load_deductions_for_account" app/services/`
   returns only the helper definition.
2. `grep -nE "salary_gross_biweekly=salary_gross_biweekly,\s*\)" app/services/`
   returns matches only at the shared helper site.
3. Full test suite green.

**G. Downstream effects** Future investment-projection consumers
inherit the shared helper rather than re-implementing the query.

**H. Rollback notes** `git revert`.

---

### Commit 19 -- Unify loan period-balance dispatcher; period-end-keyed canonical (F-21)

**A. Commit message** `refactor(loan): unify period-balance dispatcher; period-end-keyed canonical (F-21)`

**B. Problem statement** Two surfaces derive a loan's projected
balance at a forward horizon differently:

- `app/services/savings_dashboard_service.py:467-475` walks
  `state.schedule` for the last row on-or-before
  `date(target_y, target_m, 1)` for 3 / 6 / 12-month projected
  balances (target-month-first semantic).
- `app/services/year_end_summary_service.py:1572-1612`
  (`_schedule_to_period_balance_map`) walks the schedule for the
  last row on-or-before `period.end_date` (period-end-keyed
  semantic).

The two derivations answer slightly different questions and produce
different cents-precise values for the same loan + same horizon. The
S6-03 audit finding flagged the dual derivation; Commit 28 of the
main remediation collapsed the classification step (via
`account_projection.classify_account`) but left the balance derivation
itself dual.

**Locked canonical:** Period-end-keyed (year-end semantic). More
accurate -- shows the balance AFTER any payment due in the period
containing the target month. Savings-dashboard 3/6/12-month numbers
will move slightly; re-pin with hand-computed arithmetic.

**C. Files modified**
- `app/services/account_projection.py` -- add
  `compute_loan_period_balance_map(schedule, periods, original_principal) -> dict[period_id, Decimal]`
  (move the existing `_schedule_to_period_balance_map` body here;
  delete it from year_end_summary_service.py).
- `app/services/savings_dashboard_service.py:445-475` -- replace
  the target-month-first walk with a call to the shared dispatcher
  resolving the period containing the target month.
- `app/services/year_end_summary_service.py` -- call the shared
  dispatcher; delete the now-empty `_schedule_to_period_balance_map`.
- `tests/test_services/test_savings_dashboard_service.py` -- re-pin
  the 3 / 6 / 12-month projected balances for at least one loan
  fixture with the hand-computed period-end balances in a comment.
- `tests/test_services/test_year_end_summary_service.py` -- pin the
  output is unchanged for the year-end debt path (it already uses
  period-end-keyed; no value change there).

**D. Implementation approach**
1. Read `app/services/account_projection.py` to confirm the module's
   current contents and the appropriate place for the new helper.
2. Read `app/services/year_end_summary_service.py:1572-1612` for
   `_schedule_to_period_balance_map`; this is the canonical
   implementation. Move it verbatim (with renaming) to
   `account_projection.py`:
   ```python
   def compute_loan_period_balance_map(
       schedule: list[AmortizationRow],
       periods: list[PayPeriod],
       original_principal: Decimal,
   ) -> dict[int, Decimal]:
       """Map an amortization schedule to per-period remaining balances.

       For each period, returns the remaining_balance from the last
       schedule row whose payment_date is on or before period.end_date.
       Periods entirely before the first scheduled payment return
       original_principal.

       The period-end-keyed semantic is the project's canonical
       loan-balance derivation as of F-21; the pre-F-21 savings-
       dashboard's target-month-first semantic was a divergent
       second derivation. See `remediation_follow_up.md::F-21`.

       Args:
           schedule: Loan amortization rows sorted chronologically.
           periods: Pay periods sorted by period_index.
           original_principal: Balance before any scheduled payment.

       Returns:
           OrderedDict mapping period.id -> Decimal remaining_balance.
       """
       # ... body identical to existing _schedule_to_period_balance_map
   ```
3. In `savings_dashboard_service.py:445-475`, replace:
   ```python
   for label, month_offset in [("3 months", 3), ("6 months", 6), ("1 year", 12)]:
       target_m = today.month + month_offset
       target_y = today.year + (target_m - 1) // 12
       target_m = (target_m - 1) % 12 + 1
       target_dt = date(target_y, target_m, 1)
       for row in reversed(state.schedule):
           if row.payment_date <= target_dt:
               projected[label] = row.remaining_balance
               break
   ```
   With:
   ```python
   balance_map = compute_loan_period_balance_map(
       state.schedule, all_periods, params.original_principal,
   )
   for label, month_offset in [("3 months", 3), ("6 months", 6), ("1 year", 12)]:
       target_period = _resolve_period_containing(
           all_periods, today.replace(day=1) + relativedelta(months=month_offset),
       )
       projected[label] = balance_map.get(target_period.id, params.original_principal)
   ```
   (Adjust to whatever period-lookup helper already exists; if
   none, add a tiny one in `account_projection.py`.)
4. In `year_end_summary_service.py`, delete the local
   `_schedule_to_period_balance_map` and update its sole caller
   (`_get_account_balance_map` at `:2147`) to call the shared
   dispatcher.
5. Hand-compute the new 3/6/12-month projected balances for at
   least one savings-dashboard fixture; re-pin with the arithmetic
   in a comment.

**E. Test cases**
- C19-1 fixture: fixed-rate $400k mortgage at 6%, 30 years, monthly
  payment $2,398.20, today's date 2026-05-21, current_balance
  $395,000.00. Pre-fix 3-month projected = balance at date(2026, 8, 1)
  = the last schedule row on-or-before Aug 1. Post-fix 3-month
  projected = balance at the end of the pay period containing
  Aug 1, which (assuming biweekly periods aligned to a typical
  schedule) is the schedule row on-or-before mid-August. Hand-
  compute both values and assert the post-fix value is the period-
  end one.
- C19-2 year-end debt-progress section produces byte-identical
  outputs (it already used period-end-keyed -- this commit only
  centralises the implementation).
- C19-3 the C8 cross-page balance-equality fixture's loan path
  produces consistent values across savings + year-end (the F-21
  motivating goal).

**F. Manual verification steps**
1. `grep -nF "compute_loan_period_balance_map" app/services/` returns
   the new definition and the two consumer sites.
2. `grep -nF "_schedule_to_period_balance_map" app/services/` returns
   only the deleted-comment matches (or nothing if the comments are
   updated too).
3. Open the dev server; for a user with a mortgage, the savings-
   dashboard 3/6/12-month projected balances now match what the
   year-end summary would show for the same horizons (small
   numeric shift relative to pre-fix dashboard).
4. Full test suite green.

**G. Downstream effects** Savings-dashboard 3/6/12-month projected
loan balances shift slightly (now reflect balance AFTER the period's
payment). Users see the period-end-keyed numbers across all surfaces.

**H. Rollback notes** `git revert`. The dispatcher is a pure
extraction; restoring the inline walks restores pre-fix behaviour.

---

### Commit 20 -- Static guard against bypass of `balance_resolver` (F-6)

**A. Commit message** `test(routes): static guard against bypass of balance_resolver in grid/accounts (F-6)`

**B. Problem statement** The cross-page balance-equality lock
(Commit 11 of the main remediation) re-runs
`balance_resolver.balances_for(...)` in its grid and /accounts
checking readers, rather than parsing the rendered HTML. A
hypothetical regression that bypasses `balance_resolver` in the
route handler (e.g. a hand-rolled balance loop re-introduced in
`app/routes/grid.py`) would not be caught by the equality assertion.

The four other surface readers (dashboard, /savings, year-end,
calendar) DO call the surface's public service function. The grid
and /accounts paths are the remaining gap.

**Direction:** Option (a) static lock on the route source -- add a
grep-style guard test modeled on the existing
`test_grid_inline_subtotal_loop_removed` in
`tests/test_routes/test_grid.py:3794`.

**C. Files modified**
- `tests/test_routes/test_grid.py` -- new test
  `test_grid_balance_computation_routed_through_resolver`.
- `tests/test_routes/test_accounts.py` -- new test
  `test_accounts_checking_balance_routed_through_resolver`.

**D. Implementation approach**
1. Read `tests/test_routes/test_grid.py:3794` for
   `test_grid_inline_subtotal_loop_removed` as the reference shape.
2. Add the new tests:
   ```python
   def test_grid_balance_computation_routed_through_resolver(self):
       """Lock the grid route's balance computation against bypassing balance_resolver.

       Without this guard, a regression that re-introduces a hand-
       rolled balance loop in app/routes/grid.py would silently
       cause cross-page divergence. The cross-page equality lock
       (HIGH-01 / Commit 11) cannot catch this because its grid
       reader re-runs balance_resolver itself.

       This test reads the grid route source and asserts the
       balance computation routes through balance_resolver and
       nothing else.
       """
       grid_source = Path("app/routes/grid.py").read_text()
       # Allow the canonical producer:
       assert "balance_resolver.balances_for" in grid_source, (
           "grid route lost its balance_resolver call -- "
           "regression on E-25 / Commit 5 contract"
       )
       # Forbid known anti-patterns: an inline accumulator loop or
       # a balance_calculator.calculate_balances call.
       for forbidden in (
           "balance_calculator.calculate_balances",
           "for txn in transactions:\n",  # inline accumulator
           # ... whatever shapes the audit identified as regressions
       ):
           assert forbidden not in grid_source, (
               f"grid route contains '{forbidden}' -- "
               f"this bypasses balance_resolver"
           )
   ```
3. Mirror for `tests/test_routes/test_accounts.py` against
   `app/routes/accounts.py` (or the eventual `app/routes/accounts/detail.py`
   if Commit 21 has landed by execution time -- adjust the file path).
4. Verify the test bites: temporarily remove
   `balance_resolver.balances_for` from the grid route; assert the
   test fails. Restore.

**E. Test cases**
- C20-1 the new grid guard fires when `balance_resolver` is removed
  from `app/routes/grid.py`.
- C20-2 the new accounts guard fires when `balance_resolver` is
  removed from the checking-detail route.
- C20-3 both tests pass on current main (no false positive).

**F. Manual verification steps**
1. `grep -nF "balance_resolver.balances_for" app/routes/grid.py app/routes/accounts.py`
   returns matches.
2. The new tests appear in pytest's collection.
3. Full test suite green.

**G. Downstream effects** Future PRs touching the grid or accounts
checking routes are forced to either keep the canonical producer
call or explicitly update the lock test.

**H. Rollback notes** `git revert`.

---

### Commit 21 -- Split `accounts.py` into per-sub-domain modules (F-1)

**A. Commit message** `refactor(routes): split accounts.py into per-sub-domain modules (F-1)`

**B. Problem statement** `app/routes/accounts.py` is 1,511 lines and
breaks the project's 1,000-line per-module ceiling (pylint
`C0302 too-many-lines`, pre-existing). It holds 21 endpoints across
five distinguishable sub-domains separated by banner comments today.
The R-7 anchor-history extraction landed during Commit 7 (the highest-
value DRY win), so what remains is a mechanical file split with no
URL changes.

**Direction:** Option A (single blueprint, file split by import). One
`accounts_bp` blueprint; routes split across
`app/routes/accounts/__init__.py`, `accounts/crud.py`,
`accounts/anchor.py`, `accounts/types.py`, `accounts/detail.py`.

**C. Files modified**
- `app/routes/accounts/__init__.py` (new) -- declares `accounts_bp`,
  imports each sub-module so its decorators run, re-exports the
  blueprint.
- `app/routes/accounts/crud.py` (new) -- `list_accounts`,
  `new_account`, `create_account`, `edit_account`,
  `update_account`, `archive_account`, `unarchive_account`,
  `hard_delete_account`.
- `app/routes/accounts/anchor.py` (new) -- `inline_anchor_update`,
  `inline_anchor_form`, `inline_anchor_display`, `true_up`,
  `anchor_form`, `anchor_display` (the inline-anchor + true-up
  endpoints, both consumers of `anchor_service.apply_anchor_true_up`).
- `app/routes/accounts/types.py` (new) -- `create_account_type`,
  `update_account_type`, `delete_account_type`.
- `app/routes/accounts/detail.py` (new) -- `interest_detail`,
  `update_interest_params`, `checking_detail`.
- `app/utils/account_validation.py` (new) -- the `_visible_account_types`,
  `_owned_account_type`, `_validate_update_account`,
  `_account_type_is_visible` helpers; the six Marshmallow schema
  singletons.
- `app/routes/accounts.py` -- DELETED (replaced by the package).
- `app/__init__.py` -- import update if it does
  `from app.routes.accounts import accounts_bp` (verify by reading;
  no change if it uses the package).

**D. Implementation approach**
1. Read `app/routes/accounts.py` end-to-end (1,511 lines). Map each
   endpoint to its sub-domain banner.
2. Read `app/utils/auth_helpers.py` and `app/services/anchor_service.py`
   for the canonical extraction patterns (R-7 lives in
   `anchor_service`).
3. Verify the R-7 extraction is complete: `inline_anchor_update`
   and `true_up` both call `apply_anchor_true_up`; if any inline
   `IntegrityError + uq_anchor_history_account_period_balance_day`
   discriminator remains, fold it into the extraction first.
4. Create the package directory and move helpers + schemas to
   `app/utils/account_validation.py`. Update imports in every
   route file accordingly.
5. Move each endpoint family to its sub-module. Decorators register
   against the shared `accounts_bp` import.
6. `__init__.py`:
   ```python
   from flask import Blueprint

   accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")

   # Import sub-modules for side-effect of registering routes.
   from app.routes.accounts import crud  # noqa: F401, E402
   from app.routes.accounts import anchor  # noqa: F401, E402
   from app.routes.accounts import types  # noqa: F401, E402
   from app.routes.accounts import detail  # noqa: F401, E402
   ```
7. Delete `app/routes/accounts.py`.
8. Run `grep -rn "from app.routes.accounts import" /home/josh/projects/Shekel/`
   to confirm consumers still import `accounts_bp` correctly.
9. Run pylint on the new modules; they should each be under the
   1,000-line ceiling.

**E. Test cases**
- C21-1 the existing test file `tests/test_routes/test_accounts.py`
  passes unchanged (137 tests; URLs and behaviours preserved).
- C21-2 the F-6 static guard from Commit 20 still bites against the
  new file path (`app/routes/accounts/detail.py`); adjust if needed.
- C21-3 `pylint app/routes/accounts*` shows no `C0302` warnings.

**F. Manual verification steps**
1. `wc -l app/routes/accounts/*.py` shows every file under 1,000 lines.
2. `grep -rn "url_for(\"accounts\." app/templates/ app/static/ tests/`
   returns all matches; every URL still resolves at runtime.
3. Start the dev server; visit /accounts, click into a checking
   account, edit an anchor, edit an account, create an account
   type, delete an account type. All five sub-domain flows work.
4. Full test suite green.

**G. Downstream effects** Future audit / refactor commits that
touch one sub-domain no longer collide with unrelated edits in the
same file. The pylint ceiling violation is resolved.

**H. Rollback notes** `git revert`. The split is a pure
re-organisation; restoring the monolithic file is mechanical.

---

### Commit 22 -- Follow-up final gate

**A. Commit message** `chore(release): follow-up final gate`

**B. Problem statement** Acceptance gate for the entire follow-up
chain. Confirms every commit landed cleanly, the full suite is
green, pylint is clean, and the destructive migration round-trips.
This commit is bookkeeping only -- no code changes.

**C. Files modified**
- `docs/audits/financial_calculations/remediation_follow_up.md` --
  update every F-N item's `**Status:**` line to reference the
  closing commit in this plan (mechanical sweep).

**D. Implementation approach (gate checklist -- all must pass before this commit)**
1. `python scripts/build_test_template.py` (Commit 13's migration
   changed the schema; CLAUDE.md requires rebuild).
2. `./scripts/test.sh` -- ends in `N passed`, zero
   failed/errors/xfailed. Capture the final summary line and
   include it in the commit body.
3. `pylint app/ --fail-on=E,F` -- clean, no new warnings vs baseline.
   In particular, pylint `R0801` over
   `investment_dashboard_service.py`, `retirement_dashboard_service.py`,
   `savings_dashboard_service.py` shows no duplicate matching F-22's
   targets.
4. `flask db upgrade` then `flask db downgrade -1` then
   `flask db upgrade` for the Commit-13 migration -- both
   directions clean, no schema drift.
5. The cross-page invariant (Commit 11 of the main remediation),
   the ARM-window stability lock (Commit 13 of the main
   remediation), and the new F-6 static guards (Commit 20 here)
   all green.
6. Sweep `remediation_follow_up.md`: for each F-N item closed by a
   commit in this plan, change the `**Status:**` line from
   "**Status:** not started" to
   "**Status:** resolved by Commit N of `remediation_follow_up_plan.md`."
   F-9 keeps its "resolved by Commit 15" status (main remediation).
7. `git status` shows only the docs file changed (this commit is
   gate + bookkeeping).

**E. Test cases** The entire suite is the test case. Acceptance:
full green suite, clean pylint, both-direction migration.

**F. Manual verification steps**
1. Walk the symptom paths in the dev server: open /calendar for a
   user with mixed-status entries (F-3), submit a negative SWR
   slider (F-13), check savings-dashboard 3/6/12-month loan numbers
   against year-end (F-21), view a companion progress bar (F-23).
2. Confirm `remediation_follow_up.md` is up-to-date.

**G. Downstream effects** The follow-up chain is closed. The main
remediation chain's Commit 37 (final remediation gate) can now run
against a fully resolved tree.

**H. Rollback notes** No code change; revert only the docs file if
the gate fails on rebuild.

---

## 8. End-to-end verification (symptom walkthrough)

After Commit 22 the documented follow-up symptoms are each re-tested:

1. **Calendar drift (F-3 / W-065).** Build a fixture with mixed-status
   entries on one day; assert the calendar day-total matches the
   balance-contributing predicate (excludes Cancelled / Credit). The
   grid Projected-only subtotal for the same period intentionally
   differs.
2. **Negative SWR slider (F-13).** GET `/retirement/gap_analysis?swr=-5`
   returns 422.
3. **Year-end lump-sum bug (F-19).** Year-end employer-match for a
   $23,300 lump-sum + recurring $1,500 fixture matches the hand-
   computed per-period-capped total.
4. **Salary raise drift (F-20).** Apply a 3% raise; every consumer
   (savings DTI, retirement gap, investment projection, year-end
   employer) reads the raise-adjusted gross.
5. **Loan period-balance dispatcher (F-21).** Savings 3/6/12-month
   projected loan balances match year-end's period-end-keyed
   balances for the same loan.
6. **Calendar zeroed dead-branches (F-2).** Calendar route returns
   404 when the account is missing (no longer silently zeros).
7. **Storage-tier loan rate guard (F-18).** Raw INSERT with
   `interest_rate = 9.5` raises `IntegrityError`.
8. **Fixed-rate engine anchor reset (F-8).** Generate a fixed-rate
   schedule with `anchor_balance` passed; every row's payment equals
   the contractual P&I (not half).
9. **Companion float cast removed (F-23).** Companion progress bar
   width renders byte-identical to pre-fix; route source contains
   no `float(Decimal)` cast.
10. **accounts.py split (F-1).** `wc -l app/routes/accounts/*.py`
    shows every file under 1,000 lines.

---

## 9. Out-of-scope items flagged during planning

Discovered during verification or while drafting commits; deliberately
NOT included in this plan but logged for the developer to choose
whether to promote later.

- **OPT-1 (drop demoted loan columns).** Plan says "Recommended only
  after a full production cycle confirms the resolver." Defer.
- **OPT-4 (Hypothesis cross-page fuzz harness).** Plan says "listed
  only; higher build cost." Defer.
- **OPT-5 (CD-account support).** Plan says "absent feature, not a
  bug; build only if CD accounts are wanted." Defer.
- **OPT-6 (stale-anchor uniform UI surfacing).** Resolver returns
  the warning; only grid consumes it. Uniform UI promotion is a
  design choice not in this scope; can land as a focused PR later.
- **`app/services/calendar_service.py` other zeroed-fallback shapes.**
  Verification confirmed `savings_dashboard_service.py`,
  `dashboard_service.py`, `year_end_summary_service.py` show no
  `_empty_*` zeroed factories. If a future audit finds equivalent
  zeroed fallbacks elsewhere (e.g. in `obligations_aggregator`,
  `retirement_gap_calculator`), they should be handled in a single
  PR with the same "raise on unresolvable; remove dead branches"
  shape as F-2.
- **Stale `Decimal("12")` in non-service code.** F-15 covers four
  service-tier sites. If templates or migrations carry the same
  literal in similar contexts, address in a follow-up.

These are **suggestions**, not commitments. Mention to the developer
at plan-presentation time and let them choose to promote any to a
follow-up commit.

---

## 10. Notes on executing this plan

- Run commits in order; Section 6's dependency DAG is binding.
- Every commit: re-grep cited lines first, targeted tests during,
  `pylint app/ --fail-on=E,F`, then the relevant batch green; full
  suite as the per-commit final gate and the plan-final gate
  (Commit 22).
- Never silently re-pin a test. The "Re-pinned tests" lines in
  Commit 10 and Commit 19 name the finding ID and require the
  arithmetic in a comment.
- The Commit 13 migration is destructive; the developer approved
  it at plan time. Authoring confirms the `Review:` line is in the
  migration docstring and the upgrade pre-check raises clearly on
  any violating row.
- This plan is a remediation plan only. No code is changed by
  producing it. Execution happens in separate sessions, one commit
  (or small group) per session, suite green before moving on.
- After Commit 22 lands, the main remediation chain's Commit 37
  (final remediation gate from `remediation_plan.md`) can run.
