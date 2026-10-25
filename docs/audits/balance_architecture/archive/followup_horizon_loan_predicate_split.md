> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Follow-up: the horizon answers "is this a loan?" two different ways

**Status:** CLOSED 2026-07-13 -- **FALSE ALARM.** The three producers already agree; the divergence
described below is not reachable.

The premise is that the domain / milestones select loans by `ad["loan_params"]` while the band asks the
canonical classifier, so a type-drifted account (a Mortgage re-typed to Credit Card, keeping its orphan
`LoanParams` row) would enter one set and not the other.  But `account_data` never carries `loan_params`
for such an account: `_data._load_loan_params_and_escrow` builds `loan_params_map` from the accounts
whose TYPE carries `has_amortization` (`_data.py:92-95`) -- the SAME flag `classify_account` branches on
(`account_projection.py:102`).  A drifted account never enters the map, so it gets no `loan_params`, no
`payoff_date`, and no `is_paid_off`, and the domain and the milestones skip it exactly as the band does.
One flag, one answer, three consumers.

This document correctly noted "there is no such test today, which is why the divergence was invisible."
There is now: `TestTypeDriftedLoanParamsRow` (commit `84c6e066`) builds the drifted account exactly as
the edit route would and asserts all three producers agree.

Original filing follows.

---

## The two predicates

The Horizon chart decides "is this account an amortizing loan?" in two incompatible ways.

**By the canonical classifier** (account TYPE flags -- `account_type.has_amortization`,
`app/services/account_projection.py:102`):

- the liability BAND, since it now asks the seam
  (`app/services/balance_at/_liability.py`, which selects
  `classify_account(account) is AccountProjectionKind.AMORTIZING`).

**By the presence of a `LoanParams` row** (`ad.get("loan_params")`):

- the chart DOMAIN -- `_resolve_horizon_domain`,
  `app/services/savings_dashboard_service/_horizon.py:139-147` (it takes the last future
  `payoff_date` and runs the x-axis to payoff + 1 year);
- the MILESTONE flags -- `_structural_milestones`, same file, `:549-552` (one "paid off" flag per
  retiring loan, plus the "Debt-free" flag).

On clean data the two coincide, so nothing is wrong today.

## The state where they disagree

An account can carry a `LoanParams` row while its TYPE is not amortizing. This is reachable through
supported UI, not a corrupt-data hypothetical:

1. `account_type_id` is an editable field on the account edit form
   (`app/routes/accounts/crud.py:79`, `_ACCOUNT_UPDATE_FIELDS`).
2. The update route explicitly SUPPORTS crossing the amortizing boundary -- its
   `_reconcile_anchor_and_type_effects` re-classes the linked ledger row and "swaps correction
   families" precisely so an amortizing crossing does not strand one (`crud.py:~400-410`).
3. Nothing deletes the `LoanParams` row on a type change. `LoanParams` is deleted only in
   `hard_delete_account` (`crud.py:689`).

So: change a Mortgage (has_amortization) to another LIABILITY type that does not amortize -- a Credit
Card -- and the `LoanParams` row survives. The account is now `is_liability=True`, carries
`loan_params`, `payoff_date`, and `is_paid_off` in `account_data`
(`savings_dashboard_service/_projections.py:245-250` resolves a loan whenever `LoanParams` exists,
without consulting the type), and classifies as PLAIN.

## The symptom

For that account, the Horizon chart draws a self-contradicting picture:

- **Domain**: runs out to its `payoff_date` + 1 year (`loan_params` present -> counted).
- **Milestones**: shows a "paid off" flag for it, and it can set the "Debt-free" date.
- **Liability band**: holds it FLAT at its current owed magnitude forever (`classify_account` says
  PLAIN -> no forward model -> the seam's flat carry).

The chart therefore plants a flag saying the debt retires on a date where the debt line does not move,
and the net-worth trajectory stays depressed by a liability the milestones claim is gone. A liability
line that never retires, on an axis sized for it to retire.

## What actually changed (the honest part)

Before the liability-seam work, `_liability_band` selected its loans with `ad.get("loan_params")` --
the SAME predicate as the domain and the milestones. All three agreed, and in the drifted state the
band amortized the account (`resolve_account_loan` loads `LoanParams` by account id and does not
consult the type either), so the chart was at least self-consistent, if arguably wrong about what the
account is.

Routing the band through the seam moved it onto the canonical classifier -- which is the RIGHT
authority (`CLAUDE.md`: flags drive logic; the classifier is the project's single answer to "which
engine for this account?"), and which the seam must use, since it cannot depend on a consumer's
`account_data` dict shape. But it left the horizon's other two producers on the old predicate. The
inconsistency is the cost of moving one of the three; the fix is to move the other two, not to move
the band back.

## Options

1. **Unify the horizon on the canonical classifier (recommended).** Give `account_data` one derived
   flag -- e.g. `ad["is_amortizing"] = classify_account(acct) is AccountProjectionKind.AMORTIZING`,
   set once in `_project_one_account` -- and have `_resolve_horizon_domain` and `_structural_milestones`
   read THAT instead of `ad.get("loan_params")`. Small, local, and it makes the three producers agree
   by construction. The drifted account then drops out of the domain and the milestones too, and is
   simply a flat liability -- which is what its type says it is.
2. **Fix it at the source: stop resolving a loan for a non-amortizing account.** In
   `_projections._project_one_account:245`, gate `loan_result` on
   `kind is AccountProjectionKind.AMORTIZING and acct_loan_params is not None`, so a drifted account
   never gets `payoff_date` / `is_paid_off` at all. This is the deeper fix -- the loan TILE currently
   renders a payment and payoff for an account that is not a loan -- but it changes `account_data` for
   every consumer of the savings package (tiles, metrics, DTI, debt summary), so it needs its own
   impact trace. Strictly better than (1) if it holds up; (1) is a subset of it.
3. **Delete the orphaned params row on a type change.** Make `update_account` drop `LoanParams` when
   the new type is not `has_amortization` (and the analogous params rows for the other kinds). This
   removes the drifted state entirely rather than teaching every reader to tolerate it. Attractive, but
   it is DESTRUCTIVE (a mis-click on the type dropdown would silently discard a loan's origination
   terms, rate history, and payment linkage), so at minimum it needs a confirmation step and probably a
   soft-delete. Do NOT do this casually.

**Recommendation: (1) now, and open (2) and (3) as their own questions.** (1) makes the chart
self-consistent for the cost of one derived flag and two call sites. (2) is the honest fix for the
underlying "we resolve a loan for a non-loan" behavior and deserves a scoped trace of every
`account_data` consumer. (3) is a data-lifecycle decision with real destructive risk and belongs to
the accounts CRUD owner, not this chart.

## Work plan (option 1)

1. In `savings_dashboard_service/_projections.py::_project_one_account`, add
   `"is_amortizing": kind is AccountProjectionKind.AMORTIZING` to the returned dict (`kind` is already
   computed at `:240`). Document it in the function's Returns block.
2. Update `_horizon._resolve_horizon_domain:142` and `_horizon._structural_milestones:550` to read
   `ad.get("is_amortizing")` instead of `ad.get("loan_params")`.
3. Update both docstrings (they currently say the loans "carry `loan_params`").
4. Tests: seed an account with a `LoanParams` row whose type is a NON-amortizing liability (build it
   by creating a Mortgage then re-pointing `account_type_id`, mirroring what the edit route does), and
   assert the three producers agree -- the domain does not extend to its payoff, no "paid off" flag is
   raised for it, and the band holds it flat. There is no such test today, which is why the divergence
   was invisible.
5. Gates: `pylint app/` 10.00 with all `--fail-on`; targeted
   `tests/test_services/test_savings_dashboard_service.py`; full suite.

## Pointers

- The two predicates: `app/services/savings_dashboard_service/_horizon.py:139-147` and `:549-552`
  (LoanParams-based) vs `app/services/balance_at/_liability.py` (classifier-based).
- The canonical classifier: `app/services/account_projection.py:67` (`classify_account`), keyed on
  `account_type.has_amortization` at `:102`.
- Where `account_data` gets its loan fields: `savings_dashboard_service/_projections.py:240-290`.
- Why the type can drift from the params row: `app/routes/accounts/crud.py:79`, `:377-410`, `:689`.
