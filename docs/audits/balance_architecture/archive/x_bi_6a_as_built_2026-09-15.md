> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-bi-6a` as built: a projected transfer's legs are read off the parent (2026-09-15)

**What shipped.** No reader that folds a projection reads a projected transfer
SHADOW row any more. A still-projected transfer's two legs are DERIVED from the
parent row in `budget.transfers` -- the from-side an expense, the to-side an
income, each worth `resolve_transfer_amount` on the parent, which is exactly
what amount rule 5 answered for the shadow it replaces -- by ONE leaf,
`app/services/transfer_legs.py` (`PlannedTransferLeg(transfer, account_id,
is_income)` and `planned_transfer_legs`). Rulings **R-BAL13** (the design) and
**R-BAL38** (the two forks ruled the night it was built). Transfer Invariant 5
was restated in `CLAUDE.md` and the reviewer mirror in the same commit. No
migration.

## The five readers, and why the spec's four were five

The README's entry said the READ side was "FOUR sites rather than the one the
first draft named". The census found the loan partition's projected half,
`loan_loaders.income_shadows().projected`, feeding TWO consumers -- the named
`balance_at/_plan.py` PLANNED tier and the unnamed
`loan_ledger.payment_installments` -> `loan_payment_service.get_payment_history`
(the amortization feed) -- so re-pointing `_plan.py` alone would have left two
producers of a loan's projected payment set, rule 14's tell. The developer
ruled the PARTITION re-pointed once (R-BAL38 (1)). What each reader became:

| reader | before | after |
|---|---|---|
| `cash_ledger._facts.planned_cash_rows` (every account's cash fold) | every still-Projected row on the account, shadows included, kind-blind on `transfer_id` | the account's OWN rows (`transfer_id IS NULL`, the one narrowing `X-bi-6` deletes with the rows) plus one leg per side of every live still-Projected transfer it is on |
| `cash_ledger._flows.sum_projected` | rows only | ONE reduction over rows and legs; a leg is placed on the income or expense side by which side of the parent the account is on, and its parent is re-checked `is_projected` exactly as a row is |
| `loan_loaders.projected_income_shadows` | the shadow-income query narrowed to Projected | `projected_income_legs`: the to-side legs, sorted `(period start, transfer id)` |
| `balance_at/_plan.py` PLANNED tier | `_planned_from_shadows` over `amounts_by_id` | `_planned_from_legs` over `planned_leg_contribution`; `loan_payment_due_date` takes a leg |
| `loan_ledger.payment_installments` / `get_payment_history` | one list of shadows priced by `contributions_by_id` | `PaymentInstallment.source` is the settled SHADOW or the projected LEG; settled priced from the record with `options=()`, legs by `planned_leg_contribution` with `transfer_pricing_load_options()`; merged on `(period start, parent transfer id)`, the one key both relations carry |
| `_asset_contributions._recorded_contributions` | `query_shadow_income` (settled + projected) | `settled_income_shadows` + `projected_income_legs` |
| `load_shadow_income_contributions_for_accounts` | one shadow query, screened in Python | a settled shadow query (`status_id IN settled`) plus one load of the parents INTO the accounts in any status: projected ones become records, all of them the link set |

`projection_inputs.py` crossed the 1000-line ceiling under the last of those,
so the two contribution loaders moved whole into
`app/services/recorded_contributions.py` rather than that module shaving. The
W9909 registry (`tools/pylint/shekel_checkers/_fence_rulings.py`) crossed the
same ceiling at 1,007 when `planned_leg_contribution` was classified, so the
cash-ledger entry's names and whys moved whole into
`_fence_rulings_cash_ledger.py` (a pure move, graded against HEAD's table),
the second time that registry has been split for the reason its own docstring
gives.

**Left on shadows, deliberately:** the reconcile panel's offer list
(`reconcile_service._transfers.outstanding_transfers`), a settle DOOR keyed by
shadow id, which is `X-bi-3`'s and `X-bi-6`'s; and every display-tier renderer
of a shadow row (the grid cells, the pulse's still-due totals, the calendar,
the spending analysis, the companion), which are `X-bi-6`'s 38 sites.

## What it moved: `$0.00`, measured, and where the harnesses are blind

On the dev production snapshot (`b3f7c2a91d4e`, cloned to `shekel_xbi6a` and
migrated to head so its 98 projected transfers price through their
definitions): 175 transfers, every one with exactly two shadows, **0 of 350
shadow rows drifted** from the parent on status, period, due date, deletion,
scenario or side. Base (`0600b6dd`) against head, same database:
`verify_balance_baseline` (9 accounts, 441 grid cells, 6,174 daily points),
`verify_investment_cutover`, `verify_projection_axis` and
`verify_savings_producers` **byte-identical**, with **196 legs live** in the
head-side plans (98 on Checking, 23 Mortgage, 24 Van Loan, 51 Money Market)
and 0 shadow rows in any plan.

Byte-identity proves the re-point moved nothing; it cannot show WHICH relation
is read, so the step's firing controls write past Transfer Invariant 4 -- a
shadow soft-deleted alone, a shadow's due date moved alone -- and assert the
fold follows the PARENT (`tests/test_services/test_transfer_legs.py`). Under a
mutation restoring shadow reading, 4 of the 5 fold cases fail and the
byte-identical one passes, as designed; under a mutation dropping the leg
eager set, the priced-feed control reports the per-definition walk (2
statements against `budget.transfer_templates` where an eager load takes 1).

## What the neutral review refuted, and the ruling on it

The step's own sentence -- *the double count Invariant 5 guards against is
unrepresentable: a shadow is excluded from the plan by the same `transfer_id`
test that identifies it* -- is true for a PROJECTED shadow and **false under a
STATUS drift**. The record half keys settled-ness on the shadow's status and
the plan half on the parent's, so a settled shadow beneath a still-Projected
parent is counted by BOTH: `$250.00` checking -> savings, the checking shadow
settled around the service, folds checking at `-$500.00`; the reverse drift
(parent settled, shadows Projected) folds `$0.00` on both sides. Before this
step either drift counted ONCE, because only the shadow was read. The state is
forbidden by Invariants 3 and 4, written by no door (every status change goes
through `apply_status_to_all_three`), and absent from the snapshot; but
`transfer_service._restore` carries a corrector for it, so the class has
occurred.

**The developer ruled: disclose and pin, build nothing to tear down.** The
false sentences were replaced with the measured statement (CLAUDE.md, the
reviewer mirror, `cash_ledger/__init__.py`, `_facts.py`, `ShadowSets`), and
three controls pin both drift outcomes and the loan feed's double listing with
`X-bi-4` named as the step that moves them -- the instrument before the
measurement. Refused: a refusal in the record half (a fence `X-bi-6` deletes
and `X-bi-4` may have to un-teach), and keying the record half off the
parent's status now (lane 2's live territory, a discriminator `X-bi-4`
deletes). The structural fix is `X-bi-4` + `X-bi-6`: status in ONE row, and
the drift not a state at all.

**What the drift exposes for the END state, filed for `X-bi-4`'s spec.** One
leg settled while the parent is still planned is not a forbidden state there
but a LEGITIMATE transitional one -- the checking statement clears the outgoing
leg before the savings statement clears the incoming one -- and a plan half
that emits both legs off the parent's status double counts the settled side
exactly as tonight's drift does. The from-scratch answer to rule before
`X-bi-4` builds: a plan leg is emitted only for a side whose movement does not
yet exist, the per-leg answer R-BAL13 already gave the record half.

## Three tests the ruling changed, and two the review re-armed

Two fixtures wrote past Invariant 4 on a projected shadow and encoded the
shadow-reading design: `test_loan_plan_assembly._project_loan_payment` set
`shadow.due_date` directly (the parent undated), and
`test_loan_payment_service.test_excludes_deleted_transactions` set
`shadow.is_deleted` directly (the parent live). Both go through the door now
(`TransferSpec(due_date=...)`, `delete_transfer(soft=True)`), each keeping its
claim; the second runs once per relation. `test_loan_installments`' eager-load
controls measured `budget.transfers` as "the pricing table", which this step
made the PLAN relation itself; they re-base on `budget.transfer_templates` with
two template-generated projected transfers, since one definition lazy-loads
once and cannot be told from an eager load.

The review found two controls the step had silently weakened:
`TestALoansPriceDoesNotReadItsOwnPayments` emptied only `budget.transactions`
(the feed still held 3 records) and probed only that table; both now cover
both relations, and the emptied-feed assertion was shown to fire.

## What a LATER step must obey

* **`settled_cash_facts` still applies `valuation_load_options()`**, whose
  parent chain served projected shadows; every row it loads now is settled and
  valued from its record, so four selectin round trips per fold are dead on any
  account with a settled shadow. `X-bi-4` re-keys that loader and should drop
  them.
* **`loan_payment_due_date` takes a shadow OR a leg**; a caller adding a third
  row shape adds it there, not beside it.
* **The census marker `due_date` in `app/**/*.py` moved 56 -> 57** in
  `implementation_plan_recurrence_redesign.md` because the new leaf names it;
  a new module can move ANOTHER arc's marker, and CI runs the plan gate in
  every scope.

## The README entry as it stood before the 2026-09-22 condensing (`balance:X-bi-6-3`'s tick), verbatim

Condensed to one line under `docs/plans/conventions.md` rule 5, the README standing at 1,310 of its 1,330 lines with `X-cu` and `X-cv` to specify. Carried WITHOUT re-verification.

  * [x] **X-bi-6a** `323400d9` -- every PROJECTED-shadow READER re-pointed onto the parent's derived
    legs (**R-BAL13**, ruled **R-BAL38**, the status drift disclosed and pinned under **R-BAL42**;
    five readers, `$0.00` measured). Record: `archive/x_bi_6a_as_built_2026-09-15.md`.
