> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-au-g-2c-2` as built -- a transfer shadow's amount is its parent's

Commit `1f2b98a4`. Rulings **R-IN** and **R-IO** (developer, 2026-09-01).

## What shipped

Every transfer SHADOW declares `parent_transfer` and stores no figure, so Transfer Invariant 3 is
STRUCTURAL. Deleted with it: `update_transfer`'s amount copy, `restore_transfer`'s drift corrector,
`LoanPricing.live_cash` and its scenario-wide config map, `_loan_installment._manual_shadow_amount`,
and `_settle.frozen_amount`. `LoanPricing` takes neither a clock nor a scenario now, and deleting
its config map removed the cash-ledger package's ONLY `budget.transfers` query.

## Measured, on a restored copy of production (stamp `a4c6f1d92b73`, 2026-09-01)

| measurement | value |
|---|---|
| transfer shadows declared by the migration | 350 |
| non-shadow rows touched | 0 |
| shadows whose figure differed from their parent's, before | **0** |
| rows in `budget.loan_payment_settings` | 0 |
| downgrade round trip over all 1,028 transactions | **byte-identical** |

So it moved `$0.00`, and the loan arm is graded on a seeded mortgage rather than on live data.

## THREE fixes for one defect, and the first two were each half of it

**A period move through the FORM reverted an owner's typed figure to the contract.** The review
recorded below stopped the echoed amount from TAKING ownership of a derived leg; it did not ask
what the same echo does to a leg the owner had ALREADY taken. It handed it back. On the
derive-mode mortgage the owner's typed `$1,325.00` reverted to the contract's `$1,499.10` the
moment the row was moved to another pay period -- the `$174.10` ruling **R-IO** exists to keep,
discarded by an edit that never touched the amount box.

**Reproduced through the real door before it was fixed**, not argued:
`assert Decimal('1499.10') == Decimal('1325.00')`, one failing case beside seven passing siblings
in the same class.

**Why the two existing cases could not see it, and it is the same hole in both directions.**
`test_a_bare_period_move_does_not_TAKE_a_leg_back_either` starts from a TAKEN leg but sends the
CARRY-FORWARD payload, which states no amount. `test_the_FORM_s_period_move_echoes_the_amount_and_takes_nothing`
sends the BROWSER's payload but starts from a DERIVED leg -- and re-declaring a leg that is
already derived is a no-op, so its assertion held on the one row the defect could not move. The
intersection, a TAKEN leg moved by the FORM, was covered by neither.

**Root cause: the rule had TWO answers where it needs THREE.** A write could TAKE ownership or
RESTORE the derivation, and every write that was not a take fell into restore -- including the
writes that assert nothing about authorship at all. A human write that moves no figure now SAYS
NOTHING and leaves each leg as it stands, which is the answer the bare-flag arm already gave; the
defect was that the echoed-amount arm gave only half of it, refusing to take while still giving
back. The two payloads that express one act now reach one answer by one test.

**AND THAT FIX WAS ITSELF HALF A FIX**, which a fourth review -- the neutral one, on the fix rather
than on the step -- found within the hour. It was written INSIDE the `is_override` branch, so it
closed the period-move door and left the one that gets used. The route raises `is_override` only on
an amount or period DELTA, and `is_override` is not a field on `TransferUpdateSchema`, so **a save
that changes NOTES, CATEGORY or STATUS carries the echoed amount and no flag at all** and never
reached the guard. Reproduced the same way: `assert Decimal('1499.10') == Decimal('1325.00')` on a
notes-only save. **Worse than its sibling**, because ownership is decided at
`_update._apply_transfer_updates` BEFORE `_dispatch_settle` runs -- so a status-only save to Done
restored the legs and then booked the contract.

**What was actually wrong was WHERE the question was asked, not what it asked.** *Did the figure
move* now runs ONCE, first, for every caller, and the three answers fall out of it: a figure that
moved decides ownership, an explicit `is_override=False` hands the pair back, and everything else
says nothing. Asking it inside one branch answers it for one door -- which is the whole lesson, and
the reason two successive fixes each looked complete.

**Six mutations, one per arm and one per direction, no test standing in for another:**

| mutation | fails |
|---|---|
| take on the flag alone | 2 -- both definition-driven cases |
| hand-back arm deleted | 1 -- `test_clearing_the_flag_hands_a_taken_leg_back_with_no_new_amount` |
| definition arm silenced | 1 -- `test_a_later_definition_write_hands_a_taken_leg_back` |
| the ORIGINAL shipped body | 2 -- both new cases |
| **the FIRST fix (half)** | **1 -- the notes-only case ONLY** |
| say-nothing exit deleted | 2 -- the echo, through both doors |
| restored | **17 passed** |

The fifth row is the one that matters: grading a fix's own earlier version as a mutation is what
PROVES it was incomplete rather than asserting it.

**Both new cases now assert their own premise** -- that the period actually moved -- because
neither did, and a regression that silently dropped `pay_period_id` would have left them green
while they graded a no-op save.

**Opened by this pass: N-436.** `figure_moved` compares against `budget.transfers.amount`, which
`X-au-f` NULLs for a generated parent -- at which point the predicate is VACUOUSLY TRUE and every
`is_override=True` save takes ownership on the flag alone. It fails by always PERMITTING, so no
test that expects it to permit can see it. Filed before its own defect exists, owned by X-au-f.

**It moves `$0.00` on production**, and both figures behind that are QUOTED rather than re-taken
here, so each carries its date. `budget.loan_payment_settings` held zero rows when this step
measured it (2026-09-01, stamp `a4c6f1d92b73` -- the table above), so no live transfer is a
derive-mode loan payment, and on a plain transfer a derived leg's figure IS its parent's, which the
echo restores to the same number. The exposure is one `track_payment` click away: **47 projected
transfers across the two active loan-payment templates, measured 2026-08-12** and recorded in
finding **N-263**, which is three weeks old and was not re-counted for this note.

## What the FIRST TWO adversarial reviews found, and what each cost

* **A money defect this step was ADDING.** `routes/transfers/mutations.py` raises `is_override`
  when the amount changed OR THE PERIOD DID, and that form posts every input it renders -- so a
  period-only save arrives with the amount echoed unchanged beside the flag. Taking ownership on
  the flag alone froze a derive-mode loan payment's legs at the stale `transfers.amount` snapshot:
  finding **N-238**'s own exposure, added by the step whose docstring claimed to remove it.
  Ownership now requires the figure to have MOVED. **No test could see it** -- both cases written
  for period moves sent the carry-forward payload -- so a case now PATCHes the real form.
  Confirmed by mutation: with ownership on the flag alone, that case is the ONLY failure and its
  six siblings pass. **This fix was HALF a fix** and the section above is the other half: it
  stopped the echo TAKING and left it GIVING BACK.
* **The freeze log event was WIDER than the producer it replaced.** A first replacement asked the
  amount model for the RULE, which is true of any loan payment; the deleted producer's candidate
  map admitted only derive-mode payments and manual ones with a standing extra. It compares the
  booked figure against the transfer's own now, reproducing the old extension exactly. Its missing
  `held` arm -- a re-settle honouring a retained correction logged a freeze that had not happened,
  reporting the derivation's figure as the booked one -- is fixed and tested.
* **Two claims written about this step's own fixes were FALSE**, each naming a mutation that
  survives; corrected in place rather than deleted.
* **Four assertions could not fail**: an equality whose two sides came from one producer, an
  entailed inequality, two bare `IntegrityError` raises, and a `match=str(id)` that the revision
  id's own digits satisfied.
* **`pricing_load_options` named SEVEN relationships; the rules walk EIGHT.**
  `Transaction.pay_period` was found by this step's query-count control, not by the census -- and
  the same fix removed a duplicate loader strategy in `query_shadow_income`.
* **Two FALSE statements of current behaviour in `app/`**, one of them a reason paragraph a later
  step would have cited (`row_valuation` naming a survivor that `X-au-g-2c-1` had already routed).
* `Transaction.__repr__` renders `$None` for a derived row. Cosmetic; reported, untouched.

## Reported, not fixed

`tests/` is outside the lint gate and a SQLAlchemy declarative class has no `__slots__`, so
`obj.typo = value` followed by `commit()` succeeds silently. That is how two escrow probes in this
step's own tests wrote `EscrowLine.annual_amount` -- a column that lives on the VERSION -- and one
assertion passed because nothing had changed. A fixture helper asserting the name is mapped would
close the class; it touches every fixture and was left for a developer decision.
