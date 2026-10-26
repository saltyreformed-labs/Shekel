"""
Shekel Budget App -- Cash ledger: what one loan INSTALLMENT costs.

Amount rule 4's per-INSTALLMENT tier: the rule that prices one installment
against the loan's contract terms -- the DERIVE arm's installment P&I plus
that installment's own escrow plus any standing extra.  The MANUAL arm lived
here too until plan step X-au-g-2c-2; see the note at the foot of this file for
where it went and why.

**Nothing here takes a ROW, and plan step X-au-f-2 is what re-typed it**
(ruling **R-BAL10**).  :func:`_installment_cash` took the payment SHADOW and
read two columns off it; the answer's home is the PARENT transfer now, which
carries the identical two facts under the identical names and is not a
:class:`~app.models.transaction.Transaction`.  So it takes the two VALUES --
the same shape :func:`app.services.loan_loaders.installment_for` beneath it
already had, and the same shape
:func:`app.services.settle_day.settle_day_from_columns` takes precisely so a
transfer can answer it.  The module names no model at all as a result.

**Every contractual term here resolves on the INSTALLMENT it governs, never on
a read date** (ruling **R-IJ**, plan step X-au-g-2b), and the installment is the
one whose INTERVAL the payment falls in (ruling **R-R104**, which amends R-IJ).
The terms are the loan's :class:`~app.services.loan_ledger.LoanCalendar` --
the SAME value its charges are built from (plan step recurrence:R25, ruling
**R-R105**: "one bundle that pricing and charging both read"; it was a second
bundle of its own here, ``_LoanCashBasis`` plus the pricer's escrow lines,
finding **REC-545**) -- a pure function of the loan's params, rate feed and
escrow history, dated by nothing; :func:`_installment_cash` derives the
installment once and reads both the P&I and the escrow on it.  Nothing in this
module, or in the package above it, reads a wall clock.

**It lives in THIS package rather than in ``loan_payment_service``, and plan
step X-au-g-2a is what moved it.  This is the ONE place that argument is
written; every other site states the conclusion and points here.**  Rule 4's
producer answers *what does this row's amount resolve to*, which is the amount
model's own question rather than the loan reader's -- so hosting it a tier UP
forced the amount model to reach into ``loan_payment_service`` for it, and
:mod:`app.services.row_valuation` exists as a separate leaf only because of
that reach.  Moving the producer DOWN deletes it rather than routing around
it: this module names only loan TERM primitives (``loan_ledger``'s calendar,
``loan_loaders``, ``installment_calendar``, ``rate_period_engine``,
``escrow_calculator``), none of which names the cash ledger, so the arrow runs
one way and the loan READING tier is free to import this
package -- which plan step X-au-g-2c SPENT, routing
``loan_payment_service.get_payment_history`` through the amount model.  The
unwind is the one :mod:`app.services.row_valuation` says plan step ``X-au-g``
owes.

**THE CYCLE WAS REAL AND THE GATE COULD NOT SEE IT, which is why the number
this argument used to quote has been replaced by a measurement with a date on
it.**  Both this file and three others said a module-level ``cash_ledger``
import anywhere in the loan stack "raised EIGHT ``cyclic-import`` findings"
(measured 2026-08-12).  Re-measured 2026-08-31 while making this move, on a
clean ``git archive HEAD``, with ``pylint app/ --disable=all
--enable=cyclic-import`` and the exit code read unpiped:

  ==================================================  =======
  tree / experiment                                   R0401
  ==================================================  =======
  HEAD, untouched                                     0
  HEAD + the loan stack importing this package        **0**
  HEAD + that import, the ONE masking line deleted    **7**
  post-move + that import                             0
  post-move + that import, every mask here deleted    **0**
  ==================================================  =======

**pylint keys its excluded-edge set by ``(module, imported module)`` with no
line granularity and excludes on ``in_type_checking_block``**, so
``_amount_basis``'s ``if TYPE_CHECKING:`` import of ``loan_payment_service``
suppressed the finding for the RUNTIME function-level import of the SAME
module.  Row three deletes that ONE line and nothing else; row five deletes
every remaining mask in this package, and is the only arm a mask cannot
explain.  The count is not stable either -- pylint's cycle enumeration shares
one visited set per root -- so what is measured is that the cycle EXISTED,
that the gate reported nothing about it, and that after this move the
experiment is green with the masks gone.  That is the difference between a
green that is STRUCTURAL and one that is masked, and it is the whole
evidential case for the move.  **The masking is not this step's to fix and is
filed rather than repaired** (rule 6, finding **N-416**): asked with pylint's
own resolver, ``app/`` carries 49 excluded edges over 22 modules and **11 of
them are MASKS, over 9 modules** -- a target imported under
``if TYPE_CHECKING:`` and again at runtime.

**It reads the loan's TERMS and never its payment rows**, which is what makes
this leaf independent of the payment-history tier rather than merely ordered
after it.  The terms used to be resolved through
:func:`app.services.loan_payment_service.load_loan_context` -- and therefore
the loan's own payment history -- only to read
``resolve_loan(...).monthly_payment`` back out, a cycle plan step X-au-g-1
deleted; the terms' one loader since plan step recurrence:R25 is
:func:`app.services.loan_ledger.build_loan_calendar`, and
``test_loan_payment_service.test_the_contractual_payment
.TestALoansPriceDoesNotReadItsOwnPayments`` grades that it issues no statement
against the payment rows.

Imports no sibling, so it is the bottom of this package's pricing line:
``_loan_installment`` -> :mod:`._loan_pricing` -> :mod:`._amount_basis` ->
:mod:`._amount_source`.
"""

from datetime import date
from decimal import Decimal

from app.services import escrow_calculator
from app.services.installment_calendar import installment_paid_by
from app.services.loan_ledger import LoanCalendar
from app.services.loan_loaders import installment_for
from app.services.rate_period_engine import period_for_date
from app.utils.money import round_money


def _installment_cash(
    calendar: LoanCalendar,
    due_date: "date | None",
    period_start: date,
    extra_principal: Decimal,
) -> Decimal:
    """Derive-mode live cash for one loan payment: its INSTALLMENT's P&I + escrow + extra.

    The single expression every reader of an AUTO-DERIVED loan payment's cash
    builds it from, so no two can disagree.  It backed a read-time override and
    a settle-time capture as one method until plan step X-au-g-2c-2 deleted
    both: a derive-mode payment stores no figure for an override to supersede,
    and a settle books what the amount model resolves
    (:func:`._amount_source.resolve_transfer_amount`), so the display figure and
    the booked one are one expression rather than two kept in step.

    **It takes the two DATING COLUMNS rather than the row that carries them,
    and plan step X-au-f-2 is what re-typed it** (ruling **R-BAL10**).  It was
    ``_shadow_live_amount(basis, escrow_lines, shadow, extra_principal)`` and
    read ``shadow.due_date`` and ``shadow.pay_period.start_date`` through
    :func:`app.services.loan_loaders.loan_payment_due_date`.  R-BAL10 puts a
    loan payment's amount on the PARENT transfer, which carries both of those
    facts under the same names and is not a
    :class:`~app.models.transaction.Transaction`, so a row-shaped reader could
    not be handed one.  Taking the values is the shape
    :func:`app.services.loan_loaders.installment_for` beneath it already had
    for exactly this reason -- the transfer WRITE boundary must date an
    installment before any row exists -- and the shape
    :func:`app.services.settle_day.settle_day_from_columns` takes *precisely so
    a transfer can answer it*.

    **BOTH contractual terms resolve on the INSTALLMENT the payment pays, and
    that is ruling R-IJ** (plan step X-au-g-2b) **as ruling R-R104 amends
    it**.  The payment's own due date in contract time
    (:func:`app.services.loan_loaders.installment_for`) is placed on the loan's
    installment calendar ONCE here -- the latest installment due on or before
    it (:func:`~app.services.installment_calendar.installment_paid_by`, worked
    out from the calendar's one rule rather than searched for, ruling
    **R-R105**), the interval ruling **R-R89** pairs a payment with, and the
    installment the loan page names the payment by (rulings **R-R108**,
    **R-R109**) -- and that installment
    drives the P&I --
    the level payment of the rate period containing it
    (:func:`~app.services.rate_period_engine.period_for_date`) -- and the
    escrow -- :func:`~app.services.escrow_calculator.escrow_monthly_as_of` on
    the same day.  One date for both is what makes them one installment's
    price rather than two answers about two moments; deriving it once rather
    than twice is what makes that structural.  Until R-IJ that held for the
    escrow alone: the P&I came from whatever period contained the READ date,
    so on an ARM whose rate had adjusted between the two the residual
    ``cash - interest - escrow`` absorbed the recast delta as PRINCIPAL
    (finding **N-40**).

    **Why the interval's installment and not the payment's own due date**
    (ruling **R-R104**, "Price on the interval").  Since plan step
    recurrence:R16-c-2 a loan is charged on its contract's installments,
    whatever its payments' due dates (rulings **R-R72**, **R-R89**), and the
    charge a payment clears resolves its rate period and its escrow on THAT
    installment's date (``loan_ledger._charges.contract_charges``).  While the
    cash was priced on the payment's own due date, a payment due off the
    contractual day built one escrow version into its cash and backed another
    out of its split whenever a change fell between the two dates, moving the
    difference into principal.  Made-up figures: escrow ``$100.00`` a month
    rising to ``$300.00`` on Mar 1, P&I ``$200.00``, a payment due Mar 10 on a
    loan due the 22nd that clears ``$50.00`` of interest.  Priced on Mar 10 its
    cash was ``$500.00`` against a split backing out Feb 22's ``$100.00``
    escrow, so ``$350.00`` went to principal where ``$150.00`` was paid down;
    priced on Feb 22 the cash is ``$300.00`` and the split ``$50.00`` /
    ``$100.00`` / ``$150.00``.  For a payment due ON the contractual day the
    installment IS its due date, so nothing moved for one: measured on a
    production copy 2026-09-24, no loan payment was due off its loan's
    contractual day.

    **Four payments clear no charge, so their whole cash -- escrow included
    -- is principal.**  A payment due before the loan's first installment
    (ruling R-C's early extra) has no installment to pay, so
    :func:`~app.services.installment_calendar.installment_paid_by` answers its
    own due date and it is priced there, as the replay reads its period there
    too.  An OVERDUE projection pushed past a later recorded fact is priced on
    its own interval's installment, as if nothing had pushed it, while the
    replay hands it what stands at the push -- nothing, since that fact cleared
    it (the catch-up rule of plan step recurrence:R16-c-2).  A SECOND payment
    inside one installment's interval deliberately clears no fresh charge (plan
    step X-au-g-2c-3b-2).  And a payment whose interval's charge a balance
    assertion cleared before it -- due Mar 10 on a loan due the 22nd, after a
    Mar 5 true-up -- faces nothing standing (ruling R-R72 part (2)): the
    assertion states the balance owed, so the escrow its cash carries pays
    principal.

    **The cash and the split's installment are dated from ONE row, the PARENT
    transfer.**  Plan step X-au-f-2 put the cash on the parent while the split
    still read the SHADOW's mirrored ``due_date`` -- one value with two homes
    kept equal by a maintenance contract, rule 14's own shape.  Plan step
    balance:X-bi-6-4b deleted the second read: the loan walk reads each
    settled payment's :class:`~app.services.transfer_legs.TransferLeg`, whose
    ``due_date`` and ``pay_period`` are its parent's, so both answers come off
    the same two columns.

    **Why contract time and not the pay-period start** (ruling D5, finding
    N-34): a pay period begins up to ~2 weeks before the installment it pays,
    so a version effective inside that window would build one figure into the
    cash and back a different one out of the split, silently moving the
    difference into principal.  Contract time governs both ends.  R-IJ is that
    same argument for the P&I term, and rejects the read pass's own ``as_of``
    on the same ground it rejects the period start: one figure for every
    installment is still the wrong figure for all but one of them.

    ``extra_principal`` (the standing overpayment, spec Sec. 6) is added on top
    in BOTH the display and the settle freeze, and the split's residual
    ``cash - interest - escrow`` lands it in principal automatically.
    **``round_money`` is ONE call over THREE terms and must not become two**
    (ruling E-26): summing then rounding once is the boundary, where
    ``round_money(round_money(pi + escrow) + extra)`` would double-round and
    part a cutover advertised as byte-identical from its predecessor by a cent.

    Args:
        calendar: The loan's :class:`~app.services.loan_ledger.LoanCalendar`
            -- the read pass's own, the one its charges are built from
            (:class:`~app.services.loan_ledger.LoanCalendars`).  Taken WHOLE
            rather than unpacked by every caller: its rate periods, its escrow
            lines, its payment day and its origination are parts of one figure
            -- the last two place the installment whose period and escrow are
            read -- so passing them separately would let a call site pair one
            loan's terms with another's calendar.
        due_date: The payment's own stored due date, or ``None``.  Both dating
            values come off ONE row at every call site, for the reason
            *calendar* is taken whole.
        period_start: The start date of the payment's pay period -- the
            fallback basis, read on every call because
            :func:`~app.services.loan_loaders.installment_for` takes it eagerly.
        extra_principal: The recurring payment's standing extra principal
            (``0.00`` when none), from :func:`loan_payment_config`.

    Returns:
        ``round_money(period_for_date(calendar.periods, installment).period_pi
        + escrow_monthly_as_of(calendar.escrow_lines, installment)
        + extra_principal)``, where
        ``installment`` is the one whose interval this payment's due date falls
        in -- the due date itself for a payment due on the contractual day or
        before the loan's first installment.
    """
    installment = installment_paid_by(
        calendar.origination_date, calendar.payment_day,
        installment_for(due_date, period_start, calendar.payment_day),
    )
    monthly_pi = period_for_date(calendar.periods, installment).period_pi
    escrow = escrow_calculator.escrow_monthly_as_of(
        calendar.escrow_lines, installment,
    )
    return round_money(monthly_pi + escrow + extra_principal)


# ``_manual_shadow_amount`` lived here until plan step X-au-g-2c-2, and what
# deleted it is the state it read becoming unrepresentable rather than a
# caller changing its mind.  It derived a MANUAL-mode loan payment's cash as
# ``round_money(shadow.estimated_amount + extra_principal)`` -- the operator's
# stored base plus the standing extra -- under a docstring asserting that
# column was "the recurring base".  A transfer shadow stores no figure at all
# now: it declares ``PARENT_TRANSFER`` and reads its parent through the amount
# model, so the manual arm is the parent's own series price plus the extra
# (:func:`._amount_source._loan_payment_cash`) -- the DEFINITION's price
# rather than a copy of it, which is the same arm ruling **R-FI** gives every
# other manually-priced row.  Plan step X-au-f-2 moved that arm off the SHADOW
# dispatch onto the TRANSFER's (**R-BAL10**), which is where both arms of rule
# 4 now live.
#
# **The two answered the same number for every row that existed**: Transfer
# Invariant 3 held on the column, so a shadow's ``estimated_amount`` WAS its
# parent's ``amount`` (measured 2026-09-01 on production, stamp
# ``a4c6f1d92b73``: 0 of 350 shadows differed).  What is gone is the way they
# could come apart -- and the ordering hazard the balance README recorded,
# where plan step X-au-f would have NULLed the very column this read.
