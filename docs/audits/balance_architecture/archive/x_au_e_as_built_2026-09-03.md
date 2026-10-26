> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-au-e` as built: a template row reads its template's series (2026-09-03)

**What shipped** (`c000d7f6` the cutover, `b846386a` the two defects its own adversarial review found; migration `c8f3a5d2e714`). An ordinary
recurring row DECLARES the definition that prices it and stores no figure, so
its amount has one producer -- its definition's effective-dated series, read on
the row's OWN due date through amount rule 3 -- where it had two: that series at
READ time and `recurrence_engine._amounts` at GENERATION time, with nothing
reconciling the stored copy. Rulings **R-FI**, **R-JB**, **R-JC**, and two this
step asked: **R-JD** and **R-JE**.

## The population

**525 non-override template rows on production**, settled ones included
(**R-JB**): 455 Projected, 55 Paid, 11 Received, 3 Cancelled, 1 Credit. 40
override rows keep their figures. Measured on a clone dumped 2026-09-03 from
stamp `a4c6f1d92b73` and migrated to `d4a92f6b13c8`. **The count was predicted
before the migration ran and the migration printed the same number.**

**A row an ACTIVE salary profile prices is excluded**, which is finding
**N-253** rather than tidiness: amount rule 2 claims every row of a
salary-linked definition and `income_service.salary_net_for` answers for an
INCOME row only, so declaring an expense row on such a definition would empty a
figure no producer can replace. `d7b2e6c1a483` owns that population. Zero such
rows exist.

## What it moves: `$0.00`, measured three ways rather than argued

1. **Row by row.** All 525 stored figures already equal what the series answers
   on the row's own due date: 0 differing, `$0.00` net, `$0.00` gross. 0 rows
   with no `due_date`, 0 whose template states no price. So the cutover deletes
   a COPY rather than a fact.
2. **`verify_balance_baseline`**, `origin/dev`'s code over the pre-migration
   clone against this branch's code over the post-migration one, each side
   folding its own tree: **byte-identical** over 9 accounts, 441 grid cells and
   6,174 daily points.
3. **`verify_amount_resolver`** over all 1,028 rows of the post-migration clone:
   934 rows cut over, 0 refusals, 0 mismatches, and **94 OWN rows moving by
   exactly a `$1,000` nudge** -- the control that says the harness can see a
   move at all.
   **The DERIVED half of that harness measures nothing, and a first draft of
   this record quoted it as evidence.** Its nudge loop is
   `if txn.estimated_amount is None: continue`, and
   `ck_transactions_amount_ownership` makes that column NULL on every derived
   row -- so the perturbation skips 100% of the derived population by
   construction and pass 2 re-resolves an unperturbed row. The honest sentence
   is that 934 rows store no figure at all, so **there is no column left to
   nudge and the invariance is structural rather than measured**. The same
   defect is in `X-au-d`'s "801 invariant". A real control for that half has to
   perturb the SOURCE -- the resolved version's amount -- and assert the
   derived rows move by exactly it while the OWN rows do not; it does not
   exist yet. Found by this step's adversarial review.

**32 rows answer from a SUPERSEDED version.** That is the population on which
the series' time dimension is observable, and it is exactly what the deleted
re-price arm would have moved.

## The downgrade

**R-JC**'s two arms, exact-from-the-record first. The round trip is
**byte-identical**: every transaction's `(id, estimated_amount,
amount_source_id)` before the upgrade equals its value after the downgrade,
`diff` exit 0 over 1,028 rows. 46 restored exactly from their settlement record,
479 from their definition's scalar -- and the second arm is exact here only
because `default_amount` happens to equal the stored figure on all but 7 rows,
every one of which the FIRST arm covers. That is a property of this data, not a
guarantee, which is what `settled_rows_whose_plan_is_not_recoverable` exists to
report: **20 rows on the `purchases` basis**, where `d7b2e6c1a483` had none.

**Reversing the two statements is shown to fail**, on the figure rather than on
a count: `$7.77` against `$450.00` in the test, 7 rows on the production clone.

## The two questions this step asked, and why the answers are not what the plan said

**R-JD -- the chooser keeps its OFFER and loses its FIGURE.** The specification
had the keep-vs-use decision DELETED, on the ground that a hand-edited month
owns its figure so the collision the chooser mediates cannot occur. Both halves
of that are true and neither reaches the offer. A census of all six
`is_override = False` writers in `app/` found that
`recurrence_engine._conflicts` is the ONLY one that clears the flag on an
EXISTING transaction -- the other five are the two create paths and three
transfer-side doors -- and it is likewise the only per-row un-delete outside
archiving the whole template. Deleting it would have stranded 40 production rows
permanently OWN until `X-au-h`. So "use" survives and means *hand this row back
to its definition*.

**The chooser is SHARED with the transfer-template route**, whose generated rows
still store their amount until `X-au-f`, so `RecurrenceConflictKind` carries
`use_states_a_figure` and both the dispatch and the page branch on it. **That
field, its branch and the page's figure arm are `X-au-f`'s to delete together.**

**R-JE -- a soft-deleted settled row is payment history.** Closes **N-444** and
guards **N-440**'s window. `template_has_paid_history` filtered
`is_deleted = False` while `hard_delete_template`'s bulk delete did not, so such
a row was invisible to the gate AND excluded from the delete; the template went
and the row was left holding its money with no link. Harmless while it OWNED its
figure, unpriceable once it declares. The remedy is the GATE rather than a CHECK
because **R-IY** already condemns `amount_source_id` and `X-au-l` sits at rank
\#83 behind `credit_card:CC4d`. Zero rows of that shape exist on production, so
nothing deletable today stops being so.

## What a LATER step must obey

* **`X-au-f` owes three deletions in one commit**: `use_states_a_figure`, its
  branch in `apply_conflict_decisions`, and the chooser page's figure arm. It
  also owes `transfer_template_has_paid_history` the edit its twin has taken --
  the transfer survivor is priced by rule 1 off the column it holds only until
  `transfers.amount` is emptied.
* **`TransactionTemplate.default_amount` is a stored figure that duplicates the
  series, and NO step removes it.** **17 Python read sites and 6 template
  sites** by AST census -- a first draft of this line said "three live
  readers", which would have mispriced the plan question it is offered for.
  The one that matters most is `obligations_aggregator.py:186`, a MONEY
  producer feeding `/obligations` and the emergency-fund baseline: it reads
  the scalar while every row of the same definition is priced by the series,
  so a scheduled future rise makes the two screens disagree. The others are
  this migration's downgrade, `routes/salary/profiles.delete_profile` opening
  an archived profile's series at it, the conflict chooser's gate, the edit
  form's prefill, the Recurring list, and a transfer's generation. Under rule 14 that is
  a stored copy of a derivable value with no step that deletes a home. Raised
  with the developer as a plan question; not decided here.
* **ANY new door that creates a transaction template must state a price
  through `template_amount_service.set_amount`.** It was a redundancy before
  this step -- generation copied `default_amount` onto the rows -- and it is
  now the only thing that can price them. Both existing doors do it;
  `bank_import:X-f6c`, which mints a template from a NEW-ENVELOPE merchant
  answer, is the next one that will need to.
* **A fixture that constructs a template and stops builds a definition the app
  cannot build.** Both create doors state a price through
  `template_amount_service.set_amount`; a template without a series generates
  rows `_stated_amount` refuses, by X-au-a's ruling rather than by oversight.
  `tests/_test_helpers.state_template_price` is the shared door.

## What the test infrastructure was hiding

Two fixture shapes were building rows the application cannot build, and both
were silent until the column went away.

1. **`settlement_if_settling` read `txn.estimated_amount`** as its stand-in for
   "what this settle books", on the stated ground that generated rows own their
   plan. That went false the moment one stopped, and every fixture settling a
   generated row died on the settlement record's own refusal -- *a 'derived'
   settlement must state the figure that moved*. It calls
   `transaction_service.settle_amount` over a basis built the way the real verb
   builds one now, which is what its own docstring already promised.
2. **Templates built without `set_amount`** have an empty series. 148 raw
   `TransactionTemplate(...)` constructions exist across 70 test files; the
   shared builders and every per-file factory the cutover reached now state a
   price.

`resolved_amount` is the assertion a derived row needs where the column was: it
keeps the test about the FIGURE rather than about the absence of a cache.


## Findings closed here, under their own ids

**N-244** (`X-au-a`'s adversarial backfill review, 2026-08-11) -- *the conflict
chooser's "use" action back-dates today's price onto a past row and clears the
flag that would mark it.* Closed, **but not by the remedy the row named**. That
row said the owning step "deletes regeneration's amount arm AND the chooser's
keep-vs-use decision"; the decision survives (**R-JD**) and what was deleted is
the FIGURE inside it. The defect is gone either way and for the row's own
reason: no writer re-prices a past row, because no writer states a price at all.
**32 rows on production answer from a superseded version**, which is the
population the deleted arm could have moved.

**N-247** (same review) -- *one date, two predicates: an amount edit's
regeneration selects rows by their pay PERIOD's end while the amount series
answers by a row's own DUE date.* Closed by DISSOLUTION, exactly as the row
predicted: the sweep writes a declaration rather than a figure, so which rows it
reaches decides nothing about money.

**N-444** (`X-au-d`'s adversarial review, 2026-09-03) -- *a hard template delete
can strand a declaration, and the rollback cannot repair it either.* Closed by
**R-JE**, the developer's answer to the question the row was filed with.

## What is left open, and where it went

**N-296** -- the eager-load obligation. `X-au-e` is the step the row said it
would bite at, and **it bit, measured rather than predicted**: on two clones of
one production dump differing only by this migration, an UNGUARDED batch caller
pricing 926 rows goes from **146 to 260** DBAPI statements while a caller
applying `pricing_load_options` stays at **8 and 8**. The cost is
per-DEFINITION, as `amount_relationships` says it is -- 525 declared rows added
114 statements, not 525 -- and the remedy is confirmed to be exactly the
published options. **This step did not do it** (rule 6: it is seven other
modules' loaders), so the row is re-owned to a developer decision: mint a leaf,
or attach it to `X-au-f`, which touches the same loaders.

**N-440** -- the window is now guarded at the DOOR (**R-JE**, plus
`_conflicts` skipping a row whose `template_id` is NULL) rather than left open
until `X-au-l`. Both are doors and not the deletion, so the row stays open and
its owner moves to `X-au-l`.

## One thing found in passing and NOT fixed here

**`ledger.md`'s `N-260` cites "N-247" for the entry-door policy census, and that
is the OTHER N-247** -- the id was reused, and the entry-door finding is `N-284`
("opened as **N-247**, briefly **N-280**"). The citation was already ambiguous
before this step; closing the sweep-predicate `N-247` makes a bare citation
resolve to nothing in the live table. Reported rather than corrected under rule
6: it is another finding's text and another arc's owner.


## What the adversarial review changed, after the fact

The review ran against `c000d7f6` and found two live defects this step ARMS.
Both are fixed here, and both were reproduced on a clone of production before
being fixed rather than argued from the code.

**A cleared Due Date made a derived row unpriceable and 500ed the whole grid.**
The popover rendered an editable, clearable date on any non-finalised row; a
date-only edit does not raise `is_override`, so the row stayed derived; rule 3
refuses without a date (ruling D5 forbids the period's bounds as a substitute);
and `routes/grid/page` prices every row it loads with no handler. Measured: the
identical act leaves 926 rows pricing before the cutover and raising after it.
**Ruled by the developer 2026-09-03**: a generated row's due date is its
DEFINITION's. It is `DerivedRowFields`' own field -- generation computes it and
the maintain splat rewrites it -- so the form was a second writer of a derived
value and its edit never survived a regeneration anyway. The popover now shows
it as text and names where the edit belongs; `_gates._reject_generated_due_date_edit`
is the crafted-request backstop. An AD-HOC row keeps the input: it owns its
figure, so no rule reads its date.

*What the data said, and it is why the ruling is not a compromise:* every one
of the 624 template-linked production rows already carries a generation-computed
due date. For a `period` cadence -- Groceries, Gas, Kayla's Spending Money,
Data Manager -- that date is `period.start_date`, the payday, with 31 distinct
days-of-month across the horizon. The owner never typed one and never could
have.

**Withdrawing a MIDDLE amount version silently re-priced rows.**
`delete_amount_version` refused only when the NEWEST price moved, which was
sufficient while generation copied the scalar onto rows. Measured: Geico's
2026-06-01 entry was withdrawable and taking it moved a row `$178.32` ->
`$178.00` under a flash reading "Amount history entry removed". The rule is now
one sentence -- a version may go only when the version BEFORE it states the
same amount -- which subsumes the newest-price case rather than sitting beside
it, and `_may_withdraw` is the ONE statement both the service and the display
builder's `is_deletable` call. The documented repair path stays open, and its
test now drives it end to end: a mis-dated entry is repaired by restating the
amount at the right date, which appends and makes the old entry redundant.
**Two suite cases had been asserting the defect** -- one named
`test_withdraws_a_later_version`, whose docstring said "removing the June entry
puts June back on the April price".

**The migration now REFUSES rather than trusting a week-old measurement.**
`rows_the_declare_would_strand` runs the declare predicate as a SELECT and
raises with the offending ids unless every row it would touch has a due date, a
non-empty series, and a stored figure equal to what that series answers. Proven
both ways on a production clone: passes and declares 525 on clean data; on a
clone with one due date nulled it names `txn 781` and leaves all 525 rows OWN.
**No cutover in this family had a test of its UPGRADE before this**; all three
exposed only their downgrade.
