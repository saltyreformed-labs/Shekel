> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-au-f-2` as built: a generated transfer's amount is its definition's (2026-09-10)

**What shipped.** `budget.transfers.amount` is empty for every non-override
generated transfer. The parent transfer answers its own amount -- its
definition's dated series, or, for a loan payment, the cash that leaves the bank
-- and each shadow leg reads its parent. Every writer states an
`AmountOwnership` rather than a figure. One migration, `b7e4c1f38a20`.

It is the second and last leaf of `X-au-f`, and it is the whole cutover because
the developer RE-CUT the step on 2026-09-10 (**R-BAL14**). What that ruling
corrected is the subject of the next section, because it is the most transferable
thing this leaf learned.

## The leaf boundary was wrong, and the DESIGN was not

The step was split into three leaves on 2026-09-09: the readers take a resolved
figure (`X-au-f-1`, shipped), the parent gains its loan producer (`X-au-f-2`),
the migration empties the column (`X-au-f-3`). Building the second one found
that the second and third are two halves of ONE act -- moving a value's home --
and that ANY boundary between them leaves a commit in which every reader asks
the new home while the old one is still full.

**Measured before it was argued.** With the producer wired and the column not yet
emptied, 27 suite cases across three integration files fail:

  ==================================================  ===============  ===============
  a loan payment, in the window                       what it answers  what it should
  ==================================================  ===============  ===============
  derive mode, escrow risen `$300` -> `$400`/mo       `$1,499.10`      `$1,599.10`
  manual mode, series `$1,300` + standing extra `$150`  `$1,300.00`      `$1,450.00`
  ==================================================  ===============  ===============

The fold reads the legs (Transfer Invariant 5), so the middle column moves the
projected balance. `$0.00` on production, where `budget.loan_payment_settings`
holds no rows -- and ONE *auto-track* click from live, because `track_payment`
flips the settings row and never touches `amount_source_id`.

**The developer's question was the right one**: *is the from-scratch design
wrong, or are you re-cutting the leaves differently?* The answer was the second
in service of the first. **R-BAL10 is untouched.** A design error and a
sequencing error are different errors with different remedies, and reporting one
as the other is how a correct ruling gets re-opened for nothing.

**A narrower re-split was offered first and was wrong too**: leaf 2 covering only
loan payments. Tracing it found the same writer surface as the full cutover --
`DerivedTransferFields`, `TransferSpec`, `update_transfer`, `track_payment` --
because a writer cannot be half-converted. It would have narrowed only WHICH
ROWS the migration touches. That correction was carried back to the developer
before building, because an option's description becomes the ruling.

## What it moves

**On production: `$0.00` by the migration itself.** All 169 declared rows already
stored exactly what their definition answers on their own due date (measured at
stamp `a1c7e5d20f43`, 2026-09-09: 0 differing, `$0.00` net and gross), so the
cutover deletes a COPY. Two answer from a superseded version, which is the
population on which the series' time dimension is observable at all.

**What DOES move, deliberately, on a seeded loan:**

* a MANUAL loan payment's PARENT goes from the base to the base plus the standing
  extra. Its two legs already answered that (rule 4's old manual arm resolved the
  parent's figure plus the extra), so the FOLD moves `$0.00` and what changes is
  the parent agreeing with its own legs -- **R-BAL10**'s point;
* a RETAINED row's plan follows its definition where it used to keep a stale
  stored figure. Being retained protects what the row RECORDED and its place in
  the ledger; a plan is not a record (`X-au-c3`). An OVERRIDE still keeps its own
  figure;
* a SETTLED transfer's plan likewise follows its definition. What MOVED is on its
  legs in `settled_amount` and is untouched, and every money reader answers a
  settled row from its record before any producer runs.

## Three things the neutral reviews caught that the tests could not

Two adversarial reviews ran over the design and the diff. Both found defects that
were green in the suite, which is the whole reason for the pass.

**The migration would have FAILED THE DEPLOY.** Its per-class pre-flight graded a
manual loan payment's stored figure against *series + `extra_principal`*, with a
docstring asserting that grading against the series alone would strand a correct
row. The app stores the BASE: `routes/loan/payment_transfer` opens the series at
the typed figure and keeps the extra on the settings row, *"added live to every
payment, in BOTH modes"*. So the probe would have named every real manual payment
carrying an extra and `upgrade()` would have raised -- and migrations auto-run in
the deploy pipeline. **The test that "proved" the arm had hand-built an
`own(base + extra)` row, a shape no writer in `app/` produces**: the fixture
encoded the migration's assumption instead of the app's storage, so the case was
green and blind.

**The downgrade could not execute at all.** `UPDATE ... FROM LATERAL (...)`
cannot reference the update's target table; PostgreSQL raises
`InvalidColumnReference`. So the revision that empties a money column on 169 rows
had no working reverse, and the docstring's account of its two arms described
code that could not run. It is a plain join now, with `DISTINCT ON` because
"one live expense leg per transfer" is a property nothing enforces (**BAL-475**),
and it no longer filters soft-deleted legs -- a settled leg's record is history
whether or not the row was later discarded, and filtering made a whole class
invisible to the operator warning beside it.

**A live door onto a transfer's amount was never traced.** A PATCH addressed to a
transfer SHADOW is answered by updating its parent, and
`routes/transactions/_shadow_mutations` still sent the deleted `amount` /
`amount_authored` pair -- which `update_transfer` now ignores silently. A user
retyping a transfer's amount in the grid would have got a 200 and the old figure.
That file's own comment calls it *"the THIRD door onto a transfer's amount, and
the one that is easy to miss"*. It was missed, and no test covered the success
path. The same door also mapped `due_date` straight through, so **BAL-476**'s
defect was still reachable after the gate that was supposed to close it: the
predicate is `Transfer.due_date_is_its_definitions` now and BOTH doors render
their own refusal.

## What a LATER step must obey

* **`pricing_load_options` gained `Transaction.transfer -> Transfer.pay_period`**,
  because the derive arm dates its installment from the PARENT's columns now. A
  batch caller applying the old set pays a query per row. `X-bi-6a` re-points the
  projected-shadow readers and takes this set.
* **`Transaction.pay_period` is read by NO amount rule** and is kept in that set
  anyway -- five batch loaders apply it and their other reads are uncensused, so
  dropping it here could make one an N+1 in silence. Filed as **BAL-477**, owned
  by `X-bm`.
* **The cash is dated from the PARENT and the split from the SHADOW**, which is
  one value with two homes kept equal by a maintenance contract -- rule 14's own
  shape, created by this step rather than inherited. `X-bi-6` deletes the shadow
  rows, at which point one row is left to date anything from.
* **`transfer_budgets` can now RAISE where it could not.** A parent transfer used
  to take rule 1, a column read that cannot refuse; a derive-mode loan payment
  reaches `basis.loans.derive_cash` and refuses when its destination has no
  `LoanParams`. Seven of the nine render sites are recovery paths. The due-date
  gate closes the cleared-date route in; the missing-`LoanParams` route is open
  and unfenced.
* **The totality control grades the tables' DOMAINS and not the classifiers'
  RANGES** -- filed as **BAL-478**, owned by `X-au-l`, and that row is where the
  finding LIVES. It is named here because this leaf's review is what found it,
  and a clause written only in an archived record governs nothing by this
  document's own first line.
* **`_update.py` and `routes/transfers/templates.py` have no headroom.** This step
  split `_posting_sync.py` out of the first when it passed the ceiling; the second
  sits at exactly 1000 lines and the next edit to it must split rather than shave.

## Two module splits, both forced by the 1000-line ceiling

`_amount_source.py` reached 1199 lines, so the CLASSIFICATION tier moved into
`_amount_rule.py` -- the seam that module's own docstring already draws
(*ownership is DECLARED and the refinement is READ*). `_update.py` reached 1011,
so `_POSTING_RELEVANT_FIELDS` and the reconcile tail moved into `_posting_sync.py`,
the same "a concern with a rule of its own lives beside the orchestration"
argument `_amount.py`, `_endpoints.py` and `_status.py` were split out under.
Neither is a cycle: both new modules are strictly below the ones that import them.

## The rulings this leaf produced

**R-BAL14** (developer, 2026-09-10), recorded in `../../plans/rulings.md`: the
cutover is one leaf, `X-au-f-3` is absorbed, and R-BAL10 stands. It supersedes
nothing.

## The findings this leaf closed

Recorded here 2026-09-11, when the leaf was ticked. `ledger.md`'s own rule is
that a row leaves when its fix ships and its record moves to the arc's as-built
document under the same id; the tick that owed this section was a second commit
that never came, so the rows sat open for a day with their fix already in the
tree.

| id | what closed it |
|---|---|
| **N-263** | the derive-mode loan payment's PARENT gets a producer, so nulling the column leaves nothing unpriceable (**R-BAL10**) |
| **N-451** | `EVT_TRANSFER_AMOUNT_FROZEN` is deleted with its predicate, so the vacuity has no site left (**R-BAL12**) |
| **BAL-476** | the popover renders a generated transfer's due date as TEXT and a crafted request is backstopped on BOTH edit doors -- the transfer PATCH through
`_reject_generated_due_date_edit` and the shadow PATCH through the shared
`Transfer.due_date_is_its_definitions` predicate applied inline |
| **N-449** | the same remedy, which is this finding's whole subject: `X-au-e`'s transaction-side fix finally reaching the transfer side. It was NOT named in the commit message and is closed by it -- verified at `templates/transfers/_transfer_full_edit.html:275-289` and `routes/transfers/mutations.py:83,339` rather than taken from that message |

| **N-352** | the two forward tiers stop reading different sources. Its own closing condition -- *a non-override transfer row stops STORING an amount and reads the same series the estimate does* -- is what this leaf did. Both tiers now reach `template_amount_service.amount_as_of`: the PLANNED tier through `amounts_by_id` -> rule 5 -> the parent -> rule 3 (`_amount_source.py:500`), the ESTIMATED tier directly (`recurring_transfer_query.py:335`). Found by the neutral review of the TICK, not of the commit |

**N-448 was retired in the same pass**, closed at `X-au-h` (`825fd791`) since
2026-09-04 and kept alive only by a clause pointing at **N-451**. Retiring N-451
here would have left it the one live row citing two ids that exist only in an
archived record, which is the shape rule 5 forbids.

**N-450 is NOT closed here, against this commit's own message.** Its finding
text is about `transfer_templates.default_amount`, which still exists
(`models/transfer_template.py:69`, `nullable=False`) and is still written by
both routes it names (`routes/loan/payment_transfer.py:191`,
`routes/investment.py:296`). What this leaf emptied is the INSTANCE column,
`budget.transfers.amount`. The row's own status cell describes the instance and
its finding cell describes the template, so the row disagrees with itself about
its subject; that is the developer's to settle, and `X-bp` owns only the
TRANSACTION template's twin.
