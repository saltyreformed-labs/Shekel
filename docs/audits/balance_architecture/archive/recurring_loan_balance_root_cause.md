> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Recurring loan-balance bugs: root-cause analysis and remedy options

Date: 2026-06-26. Scope: why "the loan shows the wrong balance" keeps recurring,
what the root cause is, what the industry-standard design is, and the concrete
options for a permanent fix. Triggered by PR #44 (commit `494e55f`), the latest
in a multi-month series of fixes for the same class of defect.

This document is the durable record. The Level 0 remedy (below) was implemented
in the same session that produced this analysis; Levels 1 and 2 remain open
options.

---

## 1. What PR #44 fixed (plain language)

A loan's balance for a future pay period was being read as the loan's **original
principal** (the amount borrowed at the start) instead of its **current balance**
(what is owed today).

The loan engine builds an amortization schedule starting from **today and going
forward**. For any pay period before the loan's **next** scheduled payment there
is no schedule row yet, so the code falls back to a "starting value." It used
`original_principal` as that fallback. The loan therefore reported the full
origination amount for those early periods and then snapped down to its real
balance the moment the first payment landed. On the net-worth cockpit this snap
appeared as a phantom **+$18,503.86** jump (of which $16,738.86 was pure
artifact: original principal $32,402.45 minus current balance $15,663.59).

The fix changed that fallback to the resolver's **`current_balance`**, routed the
cockpit and year-end surfaces through one shared helper
(`account_projection.balance_from_schedule_at_date`, `app/services/account_projection.py:162`),
bundled the schedule and the balance into one `DebtSchedule`
(`app/services/net_worth_kernel.py`) so they cannot come from different places,
and deleted the duplicate copy of the logic in the year-end summary. The
function docstrings at `account_projection.py:162` and `:205` now spell out the
hazard explicitly.

That fix is correct, but it was applied per-site and did not stop the next one.

---

## 2. Why this keeps recurring (root cause)

### 2a. The recurrence is real, across different files

The same family of "wrong loan balance" defect has been fixed repeatedly in
*different* code paths since this app was a few weeks old (traced from `git log`):

| When | Commit | Where | The mistake |
|------|--------|-------|-------------|
| 2026-04-02 | `76c6ffb` | amortization_engine / loan.py (9 sites) | ARM payment used original terms, not re-amortized from current balance |
| 2026-04-10 | `d2455e8` | get_loan_projection callers | schedule started from today, past payments never matched |
| 2026-04-11 | `6d27717` | amortization_engine / loan.py | ARM balance drifted from the user-verified current balance; added an anchor |
| 2026-05-21 | `8f5ef89` | account_projection / savings / year-end | two surfaces walked the schedule differently -> cents drift; consolidated into `compute_loan_period_balance_map` |
| 2026-05-22 | `a50e8a5` | amortization_engine / loan_payment_service | replay snapped to the anchor incorrectly; stale P&I threshold |
| 2026-06-26 | `494e55f` | account_projection / net_worth_kernel / year-end | original principal used as the pre-payment fallback (PR #44) |

Different files, different months, same shape: *"which value does a loan's
balance start from for periods where there is no schedule row?"* Every surface
that needs a loan balance has had to answer that for itself, and each has gotten
it wrong at least once.

### 2b. The fourth, still-live instance PR #44 missed (now fixed at Level 0)

PR #44 fixed three call sites. A fourth was left live, verified firsthand:

- `app/services/savings_dashboard_service/_projections.py` -- `_compute_loan_account`
  called `_loan_projected_horizons(..., acct_loan_params.original_principal, ...)`.
- `_loan_projected_horizons` passed that value straight into the third argument of
  `compute_loan_period_balance_map` -- the argument PR #44 had renamed to
  `current_balance` precisely because passing `original_principal` there is the bug.

It was masked for an ordinary active loan (the 3/6/12-month horizons land on real
schedule rows, so the fallback is not reached) but surfaced the wrong number for
a **paid-off loan** (empty schedule -> every horizon returns the original
principal) or a loan whose **next payment is more than a year out**
(deferment/forbearance). This is the argument in one example: the fix was applied
per-site, and a site was missed. With the pre-Level-0 design, nothing could have
caught the miss.

### 2c. The actual root cause

There is **no single "what is account A's balance at time T?" function.** Balances
over time are produced by roughly six independent code paths, and each invents its
own rule for the boundary -- the value for periods before an account's first known
data point. Reading the live code, those rules disagree four different ways:

| Producer (file) | Behavior before the first known point |
|-----------------|---------------------------------------|
| `balance_resolver.balances_for` (`balance_resolver.py:419`) | OMITS the period (consumer reads 0) |
| `balance_resolver.balance_as_of_date` (`:787`) | RETURNS the anchor balance |
| `balance_calculator.calculate_balances[_with_interest]` (`:58`/`:130`) | OMITS (`else: continue` at `:114-116`) |
| `account_projection.compute_loan_period_balance_map` (`:205`) | RETURNS current_balance (was original_principal -- the recurring bug) |
| `net_worth_kernel._build_investment_balance_map` | reverse-projects growth backward |
| `net_worth_kernel._build_appreciation_balance_map` (`:466`) | flat-carries the anchor backward |

Consumers paper over the disagreement with one-off gates (`_CASH_GATING_KINDS`,
`_loan_schedule_start_index`, `_honest_history_start_index` in
`savings_dashboard_service/_net_worth.py:225,231,274`), and those gates still
miss cases.

**Why the loan path is the worst offender (the defect generator):** account types
are not stored the same way.

- **Cash / checking is *materialized*.** The recurrence engine writes real
  `Transaction` rows (`recurrence_engine.py`). A cash balance is therefore just
  *anchor + sum of stored rows up to T*. There is no boundary rule to get wrong;
  the rows exist or they do not.
- **Loans, investments, and property are *recomputed at read time*.** They are not
  stored as balance-change rows. Every screen re-derives them from parameters and
  a today-forward projection (`loan_resolver.resolve_loan` replays from a
  `LoanAnchorEvent`; `growth_engine` compounds forward). Because the projection
  only goes forward from today, every consumer has to bolt on its own answer for
  the earlier periods -- and that bolt-on is where the bug lives, every time.

The recurrence is the predictable output of an architecture where the same balance
question is answered in six places, three of which recompute from scratch, each
with its own edge-case rule. It is a DRY violation (the loan-balance derivation is
duplicated and re-derived), a single-responsibility violation (no object owns
"balance at T"), and a missing single source of truth for an account's value on a
date.

---

## 3. The industry standard

Every serious financial system answers "what is the balance at time T?" the same
way, and it is the opposite of recompute-at-read:

- **A balance is a running total of immutable events, never a recomputed value.**
  Double-entry bookkeeping stores *entries* (postings); the balance on any date is
  the sum of entries up to that date (a "trial balance" is literally that).
- **Martin Fowler's accounting patterns** name this. The **Account** pattern is
  "an Audit Log of some value... its value at any point in the past, and each
  discrete change"; balance is computed by summing entries within a date range. If
  only the value over time is needed (not entry-level history), the lighter
  **Temporal Property** pattern suffices, and **Snapshot** fixes a date once to ask
  many questions as-of it.
- **Event sourcing** generalizes this: state is a *projection* of a stored event
  log, so any past state is reproducible by replaying events up to T.
- **Corrections use bitemporal records, not edits.** A wrong past figure is fixed
  with a dated correcting entry (valid-time vs transaction-time), not by mutating
  history. Shekel's `LoanAnchorEvent` true-ups already do this for loans -- the
  right instinct, applied only to loans.
- **For loans specifically, the amortization schedule *is* the materialization.**
  Treat each principal reduction as a posting against the loan, so the balance at T
  is "sum of principal postings up to T" -- identical to how cash already works.

The throughline: never recompute a balance with a special boundary rule; sum
stored changes up to a date. Shekel's cash accounts already do this; loans,
investments, and property are the exception, and the exception is the bug
generator.

Sources: Fowler, *Accounting Patterns* / *Account* (martinfowler.com/eaaDev/Account.html),
*Temporal Patterns* (eaaDev/timeNarrative.html); double-entry bookkeeping
(en.wikipedia.org/wiki/Double-entry_bookkeeping); event sourcing and accounting
(dev.to/dealeron/event-sourcing-and-the-history-of-accounting-1aah); bitemporal
modeling (roelantvos.com/blog/a-gentle-introduction-to-bitemporal-data-challenges).

---

## 4. Remedy options

Three levels, additive. Level 1 includes Level 0; Level 2 builds on Level 1.

### Level 0 -- Fix the live bug and lock it (DONE 2026-06-26)

- Fixed the missed call site: `savings_dashboard_service/_projections.py`
  (`_compute_loan_account` now passes `state.current_balance`; `_loan_projected_horizons`
  renamed its parameter `original_principal -> current_balance`).
- Added a custom pylint checker `shekel-original-principal-as-balance` (W9905) in
  `tools/pylint/shekel_checkers.py` (`ShekelLoanBalanceSourceChecker`): flags passing
  `original_principal` / `current_principal` (the two demoted, non-authoritative loan
  columns) as the balance argument to `compute_loan_period_balance_map` or
  `balance_from_schedule_at_date`. Wired into every `--fail-on` gate (the per-edit
  hook, CI, pre-commit, `/standards`, CLAUDE.md). Unit-tested in
  `tools/pylint/tests/test_shekel_checkers.py` (9 cases, every flagged form paired
  with a conforming form).
- Added a regression test `TestLoanProjectedHorizons` in
  `tests/test_services/test_savings_dashboard_service.py`: a horizon before the
  first schedule row -- and an empty (paid-off) schedule -- reports the resolver
  `current_balance`, never the original principal.

Level 0 stops *this* bug and makes the call-site pattern a build failure, but
leaves the six-producer structure intact: a *new* surface can still invent a new
boundary rule (the checker only guards the two existing producers).

### Level 1 -- One balance-at-T seam (recommended, ~1-2 weeks, OPEN)

Introduce a single module `app/services/balance_at.py` that is the **only** public
way any screen obtains an account's balance over time:

- `balance_at(account, scenario, date) -> Decimal`
- `balance_map(account, scenario, periods) -> {period_id: Decimal}`

It reuses the existing engines (it does not rewrite them): internally it dispatches
per account kind to the same cash / interest / loan / investment / property
producers, which become private. Then it applies **one** boundary rule, defined and
tested once:

> For any account, every period before its first known data point equals its dated
> anchor / current value, held flat. Never an origination amount, never an
> omitted/zero period, never a forward-schedule row.

Enforcement makes it stick: a pylint checker that flags any module *outside the
seam* calling a balance producer directly (`balances_for`, `balance_as_of_date`,
`compute_loan_period_balance_map`, the net-worth map builders), so a new surface
*cannot* re-derive a balance. Invariant tests: loan pre-payment balance ==
current_balance; today's hero balance == trend series period-0; sum-of-accounts net
worth == per-account map sums; the cross-page equality test extended to every
account kind.

Migration is phased: (0) add the seam delegating to today's producers with parity
tests, behavior unchanged; (1) route the net-worth and savings consumers through it;
(2) route the cash/date consumers; (3) turn on the checker; (4) delete the gates.
Each phase gates on the full suite, pylint 10.00, and the cross-page equality lock.

Why recommended: it permanently fences the entire bug class (no surface can ever
again invent a boundary rule), reuses all existing math, and yields a correctness
oracle Level 2 would need anyway -- without a full ledger rewrite.

### Level 2 -- Materialize everything as postings (weeks, optional end-state, OPEN)

Make every account work the way cash already does. Add an `account_posting` table
(account, scenario, date, signed amount, kind, source reference, projected-vs-
confirmed flag). Turn each loan principal reduction, each investment growth /
contribution step, and each appreciation step into posting rows. Then *every*
balance, for *every* account kind, is uniformly "sum of postings up to T" -- the
recompute-at-read asymmetry that generates the bug is gone, not merely fenced.

This is the textbook double-entry / event-sourced design from section 3. It is also
genuinely large: a new table and reference kinds, a posting generator, regeneration/
invalidation for projected postings (a future 401k row changes when the assumed
return changes; an ARM recast rewrites all future principal splits), corrections as
`TRUEUP_CORRECTION` postings, and a one-time backfill that must reconcile exactly
against the Level 1 seam. Real new failure modes (stale projected postings,
regeneration races) come with it. Recommendation: do not start here; build Level 1
first (it becomes the reconciliation oracle), pursue Level 2 only if read-time
recompute cost or projected-balance modeling pressure later justifies it.

NB: no double-entry / ledger / postings system has ever been built or committed to
in this codebase; Level 2 is an external-literature end-state option, presented as
such -- not an existing branch or a prior plan.

---

## 5. Verification done for Level 0

- `pytest tools/pylint/tests/test_shekel_checkers.py` -- 47 passed (38 existing + 9
  new checker cases).
- `TestLoanProjectedHorizons` (2 cases) and the full
  `tests/test_services/test_savings_dashboard_service.py` (107 passed) -- green.
- `pylint app/ --fail-under=10 --fail-on=...,shekel-original-principal-as-balance`
  -- 10.00/10, exit 0, zero W9905 (the fix cleared the last site; the checker is
  clean across `app/`).
- Full suite (`./scripts/test.sh`): 6317 passed in ~103 s -- the final gate. The 3
  warnings are the pre-existing upstream `flask_login` `utcnow` deprecation,
  unrelated to this change.
- End-to-end: the registered checker fires through a real pylint run on a probe
  passing `params.original_principal`, and stays silent on `state.current_balance`.

## 6. Files in the Level 0 commit

- `app/services/savings_dashboard_service/_projections.py` -- the fix
  (`_compute_loan_account` passes `state.current_balance`; `_loan_projected_horizons`
  parameter renamed `original_principal -> current_balance`).
- `tools/pylint/shekel_checkers.py` -- `ShekelLoanBalanceSourceChecker` (W9905) plus
  registration; `tools/pylint/tests/test_shekel_checkers.py` -- 9 checker unit tests.
- `tests/test_services/test_savings_dashboard_service.py` -- the
  `TestLoanProjectedHorizons` regression (empty / pre-first-payment schedule).
- `.github/workflows/ci.yml`, `.pre-commit-config.yaml`,
  `scripts/hooks/post-edit-python.sh`, `.pylintrc`, `.claude/commands/standards.md`,
  `CLAUDE.md` -- wire `shekel-original-principal-as-balance` into the `--fail-on`
  gates and the checker enumerations.
- `docs/audits/balance_architecture/recurring_loan_balance_root_cause.md` -- this
  document.

See [project_balance_projection_architecture] and
[project_loan_balance_self_calculation] in the agent memory for the longer history,
and `docs/audits/financial_calculations/remediation_follow_up.md` (F-21 / Commit 19) for
the consolidation this defect descends from.
