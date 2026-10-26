> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-au-f-1` as built: every transfer fragment takes a resolved figure (2026-09-09)

**What shipped.** The three surfaces that show a parent transfer's amount -- the
display cell, the quick-edit box and the full-edit popover -- stopped reading the
`budget.transfers.amount` COLUMN and now read what the amount model answers,
through one producer (`routes/_render_helpers.transfer_budgets`) threaded to nine
render sites. No schema change, no migration, no figure moved. It is the first of
`X-au-f`'s three leaves and the twin of what `X-au-c2` did for transactions a
phase earlier, which the transfer side never had.

## Why it is a leaf of its own

`X-au-f` empties `transfers.amount` for a generated transfer. Jinja renders
`None` as the literal string `"None"`, and both edit forms POST their box back
through `TransferUpdateSchema`, which refuses it -- so on the day the column
empties every transfer save returns 400. That is finding **N-452**, filed by
`X-au-h`'s neutral design review on 2026-09-04 against `X-au-h`'s own template
comment, which had claimed the companion field was "indifferent to" the cutover.

Splitting it out is what makes the claim below provable BEFORE anything is at
risk: this leaf can be shown to move nothing, and the cutover then lands on
readers already pointed at the producer.

## What it moves: `$0.00`, and by CONSTRUCTION rather than by measurement

The templates now render `budgets[xfer.id]`, where
`budgets = {xfer.id: cash_ledger.resolve_transfer_amount(xfer)}`. For a transfer
carrying `amount_source_id IS NULL` that resolver takes amount rule 1 and returns
`_own_figure(xfer.amount, ...)` -- **the same `Decimal` object the templates used
to read**, so the rendered string cannot differ.

**Every transfer any writer in `app/` can produce is in that state.** AST-free
census of both write doors onto a row's ownership, 2026-09-09, over all of
`app/`:

* `amount_ownership.declare_derived` has four call sites, and **not one takes a
  `Transfer`** -- three are transfer SHADOWS (`transfer_service/_create.py:191`,
  `_amount.py:192`, `:199`) and one is a transaction
  (`recurrence_engine/_conflicts.py:200`);
* the only writes to a transfer's ownership are `state_own_amount(rows.transfer,
  ...)` (`transfer_service/_amount.py:185`) and
  `AmountOwnership.own(amount)` at construction
  (`transfer_service/_create.py:353`). Both are OWN.

So the byte-identity is a property of the writers, not of today's rows. It is
CORROBORATED by the data rather than resting on it -- measured 2026-09-09 against
production at stamp `a1c7e5d20f43`: 175 transfers, **0 declared**, 0 with a NULL
amount.

## The population the later leaves inherit, measured the same day

Read-only against production at stamp `a1c7e5d20f43`:

* **175 transfers, every one template-generated. Not one ad-hoc row exists.**
* **169 cut over** at `X-au-f-3`: 96 Projected, 13 Paid, 9 Cancelled, 51
  soft-deleted Projected. The remaining 6 are overrides and keep their figures.
* **0 of the 169 differ** from what their definition's series answers on their
  own due date: `$0.00` net, `$0.00` gross. **0 carry no due date; 0 have an
  empty series.** The cutover therefore deletes a COPY, not a fact.
* **2 answer from a SUPERSEDED version** -- the population on which the series'
  time dimension is observable at all.
* **`budget.loan_payment_settings` holds 0 rows**, so amount rule 4 prices
  `$0.00` on production and every loan arm `X-au-f-2` builds grades on a seeded
  loan. It is one click from live: **N-263** counts 47 projected transfers on the
  two loan-payment-shaped templates (Mortgage 23, Van Payment 24).

## Two design choices inside the leaf

**The producer is SEPARATE from `transfer_settlement_amounts` rather than a third
field on it.** The transaction twin bundles all three maps in one
`RenderAmounts` because every transaction fragment renders all three. Two of the
three transfer fragments render a PLAN and no record, and
`transfer_settlement_amounts` loads the shadow pair and reads two settlement
records to answer. Bundling would have made every cell swap pay for two figures
it does not display. The popover, which shows all three, calls both. *A first
draft justified that by naming **N-296**, and the review opened the row: N-296 is
a per-DEFINITION eager load in BATCH callers. The argument needs no citation.*

**It is a MAP rather than a scalar**, for the reason `fragment_amounts` already
states: two of the three fragments POST this figure back into a money box, and a
missing scalar renders `value=""` in silence where an absent map raises. What it
does NOT buy is protection from the WRONG map: `budgets` is one context key over
two id spaces that are not disjoint, so a transaction-keyed map handed to a
transfer fragment answers a HIT rather than a `KeyError`. No path does that
today, and the guarantee is that a MISSING map is loud.

## What the adversarial review changed

The review ran against the pre-review tree and made eight findings; all eight
are acted on here, three of them by writing something down rather than by
changing behaviour.

**The control this leaf shipped with graded NOTHING, and that is the finding
that mattered.** The first draft asserted that the rendered box, its companion
and `resolve_transfer_amount` all agree -- which is true on `dev` too, because
every transfer owns its figure there, so reverting the whole leaf left it green.
The control that replaced it declares the transfer DERIVED first, which is the
state `X-au-f-3`'s migration creates, and asserts the fragments render the
series' price as of the transfer's OWN due date with two versions bracketing it.
**Proven to fail on the code it replaces**: run on a worktree at `dev`
(`de128ddf`), all three arms fail on `value="None"` -- finding N-452's predicted
failure, reached rather than argued.

**Six hand-assembled cell renders became one `render_transfer_cell`.** This
module's own opening paragraph says a cell render has exactly ONE definition with
a public name, and the transaction cell had that while the transfer cell had six
copies across three modules. This leaf is what raised the shared context from two
items to three, which is the moment the omission is cheapest to remove: a seventh
site added without `budgets` is a 500, not a blank, and five of the six sites are
ERROR paths where a 500 replaces the message the user needed.

**A citation was wrong and is deleted rather than corrected.** The split between
this producer and `transfer_settlement_amounts` was justified by naming finding
N-296. N-296 is a per-DEFINITION eager load in BATCH callers, remedied by
`pricing_load_options` at `X-bm`; it says nothing about this. The argument stands
without it.

**Three claims were overstated and are now stated as measured.** The map does not
protect against the WRONG map (`budgets` is one key over two id spaces that are
not disjoint, so a mismatch is a hit rather than a `KeyError`); ownership is
established transitively rather than by an owner-scoped load at one of the nine
callers; and an earlier draft of the single-row paragraph said this function
"builds a basis per call", which was false -- `resolve_transfer_amount` takes
none, so it costs no query at all today.

**The dead zero guard is DELETED.** `{% if xfer.amount != 0 %}` was unreachable
on both of the resolver's arms -- `ck_transfers_positive_amount` and
`ck_template_amount_versions_transfer_positive_amount` each force `> 0` -- and
its false branch drew the cell with no figure at all, so had it ever become live
it would have hidden money rather than shown a zero. The two CHECKs are what make
it unnecessary.

**The one finding NOT fixed here is `BAL-476`**, and it is the review's second
high: this leaf wires nine render sites to a producer that will raise, and the
transaction side already paid for that exact defect (`_gates.py` records the
measurement -- one dateless derived row took out the grid, the dashboard and the
companion, reproduced on a production clone). A generated transfer's `due_date`
is its DEFINITION's, but the popover renders it editable on any Projected
transfer and `_LOCKED_EDIT_FIELDS` guards it only once finalised, so there is no
transfer twin of `_reject_generated_due_date_edit`. It is `$0.00` and unreachable
today and it ARRIVES with `X-au-f-3`, which is why it is filed against that leaf
rather than taken here.

## What a LATER step must obey

* **`X-au-f-2` hands `transfer_budgets` a read pass.** `resolve_transfer_amount`
  takes no basis today, so this producer costs no query at all -- it answers off
  the row already loaded. The moment the parent's loan producer exists it will
  build or take one, and the single-row boundary in its docstring is written for
  that day rather than for this one.
* **Every render of the three fragments must carry `budgets`.** There are nine
  and they are all one-row HTMX swaps; a tenth added without it is an
  `UndefinedError` on a live screen, not a blank. The grid is not among them --
  it renders a transfer's two SHADOWS as ordinary rows off its own map (Transfer
  Invariant 5).
* **`X-au-f-2`'s two producers are typed on the WRONG ROW, and nothing else says
  so.** **R-BAL10** puts the answer on the PARENT, but both producers it must
  reach take a shadow: `loan_loaders.loan_payment_due_date(shadow: Transaction,
  payment_day)` and `_loan_pricing.LoanPricing.derive_cash(shadow: Transaction,
  ...)`, which passes it on to `_loan_installment._shadow_live_amount`. A
  `Transfer` carries both facts either one reads -- its own `due_date`, and the
  `pay_period` the fallback reconstructs from -- so it can answer the identical
  question and neither signature will accept it. **The in-repo precedent for the
  fix is exact**: `settle_day.settle_day_from_columns` takes the two VALUES
  rather than a row *precisely so a transfer can answer it* (`Transfer.
  settle_day_columns` states the argument). Traced at this leaf and written down
  here because it is the first thing `X-au-f-2` hits and no plan document names
  it.
* **The `round_money` boundary is ONE call and must not become two.** Today's
  derive arm is `round_money(monthly_pi + escrow + extra)` -- three terms, summed
  then rounded once, the E-26 boundary. Composing it as
  `round_money(round_money(pi + escrow) + extra)` double-rounds, which is how a
  cutover advertised as byte-identical parts from its predecessor by a cent.
* **The box and its `amount_as_rendered` companion must read the SAME
  expression.** Ruling **R-JR**'s authorship comparison asks whether what came
  back differs from what was shown; two expressions that could resolve
  differently would report a re-price nobody made.

## Reported and NOT taken here (rule 6)

* **N-420** -- `_transfer_cell.html` prints money through a raw
  `"{:,.0f}".format(...)` rather than the `money()` macro, so it renders `1,234`
  where every other money surface renders `$1,234`. This leaf touched that exact
  line and deliberately only swapped the expression inside it: routing it through
  the macro CHANGES the rendered string, which is the one thing this leaf claims
  not to do. It stays owned by `X-au-f`, paired with **N-303** as its own row
  says.
* **`loan_payment_settings.extra_principal` is an undated scalar inside a money
  figure** -- the defect `default_amount` carried before `X-au-a` gave it
  `budget.template_amount_versions`. It is read as of NOW for every UNSETTLED
  installment whatever that installment's due date, so raising a standing
  overpayment re-prices occurrences already past, and nothing records when the
  standing amount changed. A settled occurrence is safe: `X-au-c3` prices it from
  its own record. Found while tracing the loan question **R-BAL10** answers.
  **It is NOT filed, and the reason is rule 4.** A finding is born with an owner
  and this one needs a step of its own; a step needs a specification in this
  README (rule 12); and this README stands at exactly its 20-line headroom floor
  with no completed span left to archive. A cap that binds is a QUESTION for the
  developer, so it is raised rather than written down here.

## The rulings this leaf's trace produced

Three, all developer, 2026-09-09, and all recorded in `../../plans/rulings.md`:
**R-BAL10** (a loan payment's amount is one value with one producer, on the
parent), **R-BAL11** (a transfer write states an `AmountOwnership`, not a figure
plus two flags) and **R-BAL12** (`EVT_TRANSFER_AMOUNT_FROZEN` is deleted, not
re-pointed). Only the first two shape leaves that remain; the third disposes of
**N-451**.

**R-BAL10 was DESIGNED FROM SCRATCH rather than picked.** The question was put as
a three-way choice and the developer refused the premise, asking what the design
would be if nothing existed. What came back is on the ruling row, and the part
worth repeating here is the test that decided it: after `X-au-m` stops a leg
storing an owner's typed figure, keeping the standing extra at the leg gives an
owner who types `$1,325.00` legs worth `$1,425.00` (manual) or `$1,610.95`
(auto-track) -- contradicting **R-IO** and re-creating exactly the `$174.10`
state **R-JM** was ruled to make unrepresentable.
