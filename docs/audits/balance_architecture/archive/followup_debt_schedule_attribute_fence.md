> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Follow-up: `DebtSchedule.current_balance` is a balance-at-T no fence can see

**Status:** RESOLVED 2026-07-13 (commit `84c6e066`), via the recommended **option 1**.

`net_worth_kernel.debt_schedule_rows` now returns the rows with NO balance attached, and all five
out-of-cluster consumers take it -- they each wanted the `AmortizationRow` list and nothing else, as the
table below records.  `generate_debt_schedules` joined `_BALANCE_PRODUCERS`; its remaining callers are
all inside the cluster.  A consumer that wants a loan's balance now has no choice but
`balance_at.balance_at`, which was the point.  Two checker tests
(`test_flags_generate_debt_schedules_from_consumer`, `test_allows_debt_schedule_rows_from_consumer`)
lock both halves.

The sibling hole this document's reasoning applies to one axis over -- `LoanState.current_balance`,
reachable through the loan RESOLVER -- was closed in the same arc (`7b7c909b`): consumers take
`balance_at.loan_figures`, which carries the rich detail with the balance deliberately absent.

Original filing follows.

**Severity:** latent. Nothing renders the wrong number today (verified below). The problem is that
nothing STOPS it, and the thing standing between the codebase and the bug is "a human will remember"
-- the exact assumption that failed twice already (`investment_base_balance_map`, then
`loan_owed_at_dates`).

---

## The hole in one paragraph

The balance fence binds on **functions**. `W9906` (`shekel-balance-producer-bypass`) flags a consumer
that CALLS or IMPORTS a balance producer, and `W9909` (`shekel-unclassified-fenced-export`) flags a
public function in a fenced module that nobody classified. Neither can see an **attribute read**.
`net_worth_kernel.generate_debt_schedules` is ruled a NON-producer, so any consumer may call it --
but it returns a `DebtSchedule`, and `DebtSchedule.current_balance` **is the loan's ledger-confirmed
balance at T=today**. The moment any consumer writes `schedules[account.id].current_balance` into a
template context, a balance-at-T reaches a screen without passing the seam, and every gate stays
silent.

## Why `generate_debt_schedules` is (correctly) ruled a non-producer

It is the batch sibling of `resolve_account_loan`, which the fence has always ruled a rich
projection-detail primitive rather than a balance map (`tools/pylint/shekel_checkers/balance_seam.py`,
the `_BALANCE_PRODUCERS` header). Its consumers want the amortization ROWS, not a balance. Fencing it
would break three legitimate callers and force them through a seam entry that returns something they
do not want.

That ruling is verified true **as of 2026-07-13**. Every out-of-cluster consumer reads only
`.schedule`:

| Consumer | Reads | Purpose |
|---|---|---|
| `tax_report_service.py:637` | `.schedule` rows (via `_compute_mortgage_interest`) | Schedule A mortgage interest |
| `savings_dashboard_service/_net_worth.py:262-264` | `schedule_info.schedule` | first-payment date (honest-history gate) |
| `savings_dashboard_service/_orchestrator.py:457` | passes the map to the gate | same |
| `year_end_summary_service/_income_tax.py:236,255` | `debt.schedule` rows | yearly interest |
| `year_end_summary_service/_net_worth.py:198-199` | `schedule_info.schedule` | membership gate only |

Nobody reads `.current_balance`. The loan tile's displayed balance comes from `resolve_loan_seeded`
(`savings_dashboard_service/_projections.py:160`), not from a `DebtSchedule`.

**That is a fact about the current tree, not a property of the code.** It is exactly the kind of fact
that quietly stops being true.

## Why this is not paranoia

`DebtSchedule` (`app/services/net_worth_kernel.py:131-153`) deliberately bundles the schedule WITH
the resolver's `current_balance`, and its own docstring says why: so a caller can report today's
balance rather than the loan's original principal. In other words, the field exists precisely so that
somebody can read it as a balance. It is a loaded gun with the safety documented in a docstring.

A consumer doing this would ship a wrong number the fence was built to prevent:

```python
# A future "small" change to a loan card or a debt table:
schedules = net_worth_kernel.generate_debt_schedules(loan_accounts, scenario_id)
return render_template("...", owed=schedules[account.id].current_balance)
#                                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
# A balance-at-T on a screen, never through balance_at. pylint: 10.00/10.
```

It would even be RIGHT most of the time (the resolver seeds `current_balance` from the genesis
ledger), which is what makes it dangerous: it is right until the loan has a state the resolver's
`today` snapshot does not capture, and then it is quietly wrong on one screen while the rest of the
app disagrees. That is the precise failure signature of the whole balance-bug family
(`adversarial_review_balance_architecture_2026-07-02.md`).

## Options

1. **Do not hand `current_balance` to out-of-cluster callers (recommended).** Split the return type:
   `generate_debt_schedules` keeps its bundle for the cluster (the seam genuinely needs the balance to
   seed the forward projection), and consumers get a rows-only accessor -- e.g.
   `debt_schedule_rows(accounts, scenario_id) -> {account_id: list[AmortizationRow]}`. All five
   consumer sites above want exactly that and lose nothing. A consumer that then wants the balance has
   no choice but `balance_at.balance_at(...)`, which is the point. Cost: one new kernel function, five
   call-site edits, and a `_BALANCE_PRODUCERS` entry for the bundle-returning original (its remaining
   callers -- the seam and the cluster -- are all allowlisted, so W9906 stays green). This removes the
   hazard rather than policing it.
2. **Extend the checker with an ATTRIBUTE rule.** Add a guarded-attribute set (`current_balance` on a
   `DebtSchedule`-typed expression) and flag `<expr>.current_balance` outside the cluster. Honest but
   weak: a syntactic checker cannot reliably infer the type of `schedules[a.id]`, so it would either
   over-fire (every `.current_balance` in the tree, including the many legitimate `LoanState` reads --
   there are ~30, see `git grep '\.current_balance'`) or under-fire. Astroid inference could narrow it,
   but a fence with false negatives is a fence you stop trusting. Not recommended alone.
3. **Accept and document.** Leave the ruling, keep the comment in `balance_seam.py` that pins the
   verification date, and rely on review. This is the status quo, and it is what this document exists
   to say is not good enough.

**Recommendation: (1).** It converts "no consumer reads this attribute" from a fact someone has to
keep re-verifying into a fact the type system enforces. (2) may be worth adding afterwards as a
belt-and-braces check on the cluster-internal bundle, but only if it can be made precise.

## Work plan (option 1)

1. Add `debt_schedule_rows(debt_accounts, scenario_id) -> dict[int, list]` to `net_worth_kernel`,
   implemented over `generate_debt_schedules` (no second resolution). Classify it in the W9909 ruling
   for `app.services.net_worth_kernel` -- it is a ROWS accessor, so a non-producer.
2. Reroute the five consumer sites in the table above to it.
3. Add `generate_debt_schedules` to `_BALANCE_PRODUCERS` (it now returns a balance-bearing bundle and
   has only cluster callers left). Confirm `pylint app/` stays clean -- if it fires, a consumer was
   missed, which is the check working.
4. Note that `balance_at._inputs._assemble_inputs` and `_liability` both call the bundle form; they are
   inside the seam, so they are unaffected.
5. Gates: `pylint app/` 10.00 with all `--fail-on`; `pytest tools/pylint/tests -c /dev/null`; full suite.

## Pointers

- The bundle: `app/services/net_worth_kernel.py:131` (`DebtSchedule`), `:156`
  (`generate_debt_schedules`).
- The ruling (with its verification-date comment): `tools/pylint/shekel_checkers/balance_seam.py`, the
  `app.services.net_worth_kernel` entry in `_FENCED_MODULE_RULINGS`.
- The fence's two axes and why neither sees an attribute: `ShekelBalanceSeamChecker.visit_call` /
  `.visit_importfrom` / `.visit_functiondef` in the same file.
- Related: `followup_fence_loan_owed_at_dates.md` (the fail-closed work this residual came out of).
