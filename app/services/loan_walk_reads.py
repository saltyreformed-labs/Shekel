"""Shekel Budget App -- the loan detail page's two surfaces, read off the pass's walk.

Both are shaped from the read pass's ONE walk of the loan's facts
(:meth:`app.services.balance_at.BalanceContext.loan_walk`), the walk the page's
balance, confirmed schedule and paid-this-year chips already fold, and neither
reads a stored ledger row:

* the confirmed payment-history table (:func:`confirmed_loan_payment_history`,
  each payment's real cash / principal / interest / escrow split); and
* the balance-anchors drift scorecard (:func:`loan_balance_anchor_history`, each
  opening / true-up paired with what the walk had computed just before it).

**They lived in the loan POSTING package until plan task plan#3133** (its
``_display`` module), which deletes that package's writers with the rest of
the stored ledger's and moved these two out first: a home named for postings
held no reader of one.  This module is named for what they read.

**The payment table reads the WALK, not the ledger's legs** (plan step
``balance:X-bi-6-3``, ruling **R-BAL102**).  Through that step a reader
module beside this one (``_reader``, deleted) summed each payment's posted
interest / escrow / principal legs back out of the ledger by the loan-side
shadow's ``transaction_id``, so the table would "cross-check the writer".  The
split is a date-keyed correction with no row link now, and two payments on
one day in one period share ONE entry, so the ledger cannot say which payment
paid what -- and never needed to: the stored ledger was a projection of this
same walk, and a display-side second grade was a checker.  The paid-in-year
chips moved OFF the postings onto the fold at step C6c
(:func:`app.services.balance_at.loan_interest_paid_in_year` /
:func:`~app.services.balance_at.loan_principal_paid_in_year`), so this module
does not answer them.

**They take the READ PASS and walk nothing of their own** (plan task
plan#3081, finding plan#2874).  Each called
:func:`app.services.loan_ledger.walk_loan_ledger` until then, so one GET of
the loan page walked the loan three times: the pass's walk and one per
surface.  The two walks are not the same function.  The pass's replays the
facts VISIBLE by its ``as_of`` (ruling **R-R91**), where the ledger's replays
every recorded fact and each surface then filtered it by ``as_of``.  For a
pass on or after every fact's VISIBLE day they are one walk, and the page's
pass is not always one.  It is built at today, on or after every settle day
and assertion date the write doors admit (the census
:func:`app.services.loan_ledger.load_loan_stream` states, which rests on what
no code enforces), but a ``$0.00`` close is dated by the installment it skips
(``loan_ledger._visible.payment_visible_on``, rulings **R-BAL139** /
**R-R107**), which can fall after today.  So the page's own pass can part
the two walks, as can a pass pinned earlier -- by a payment due before a
visible one whose cash moved after the pass's day, which the ledger's walk
splits first.  Measured 2026-11-10 at a pass on 2026-02-20: a close of the
03-01 installment and an early 04-01 payment read ``$1,500.00`` interest and
``-$500.00`` principal on the page's table, against ``$505.00`` and
``$495.00`` in the ledger's walk.  Where they part, the pass's walk is the
answer the rest of the page already shows, so the two tables agree with the
balance and the chips beside them rather than with the ledger's walk;
finding **plan#3391** owns the root cause (under X-db, plan#1041).

**A loan with no terms reads an empty list** (ruling **plan#2917**).  It walks
to nothing, no anchor and no payment, so both surfaces read ``[]``.  Until
that ruling each returned ``None`` through the one gate both asked
(``_configured_loan_walk``), a branch no page reached: the dashboard sends a
loan with no terms to its setup page before either surface is asked, and the
template hides an empty table exactly as it hid ``None``.

Reads only -- no writes, no commit.  It reads the clock once, in the payment
table's refusal of a pass dated after today, which is why it is not a module of
the walk's own package: :mod:`app.services.loan_ledger` reads no clock.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services.balance_at import BalanceContext
from app.services.installment_calendar import installment_paid_by
from app.utils.money import round_money


@dataclass(frozen=True)
class LoanPaymentHistoryRow:
    """One confirmed loan payment, split into its real economic parts.

    The display row of :func:`confirmed_loan_payment_history`: a single confirmed
    payment's outcome in the genesis walk, carrying the ACTUAL cash paid and its
    real principal / interest / escrow split (the walk's allocation of what
    moved, not the schedule's contractual replay -- so an extra or short
    payment shows honestly).  Every row is confirmed by construction (the
    producer bounds to payments whose CASH had moved by ``as_of``), so the
    table renders a Confirmed badge on each.

    ``cash`` is the payment's full cash (the settled payment leg's
    parent's :func:`~app.services.row_valuation.settled_contribution`, read once by
    the walk's event); ``principal + interest + escrow`` equals it for an
    ordinary payment.  The one case they diverge is a payoff OVERPAYMENT, whose
    surplus is a lender refund (a receivable) rather than principal -- there
    ``cash`` exceeds the split sum by that refund; see
    :func:`~app.utils.money.apply_payment_cash`.  A SECOND payment inside one
    accrual period is the other shape worth expecting: it clears no fresh charge,
    so its ``interest`` and ``escrow`` are ``0.00`` and its ``principal`` alone
    equals its cash (plan step X-au-g-2c-3b-2).

    Attributes:
        installment: The contractual installment the payment PAYS, which the
            card names it by (ruling **R-R108**): the installment its own due
            date (:func:`app.services.loan_loaders.loan_payment_due_date`) falls
            in, :func:`~app.services.installment_calendar.installment_paid_by`
            -- the one answer its charge, its cash price, the ledger and the
            confirmed schedule row read (rulings **R-R104**, **R-R109**).  A
            payment due Mar 10 on a loan due the 22nd pays, and is listed
            under, Feb 22; one due on the contractual day is listed under its
            due date, and one due before the loan's first installment under
            its own.  It was the due date itself until R-R108.  NOT derived
            from the pay period or the settled day, so a payment settled late
            still reports the installment it actually paid.
        cash: The full cash paid (the settled payment leg's
            parent's :func:`~app.services.row_valuation.settled_contribution`),
            cent-quantized.
        principal: The real debt paid down, cent-quantized; may be negative
            for an underpayment, and equals the payment's net on the loan's
            linked ledger by the split's construction (``cash - interest -
            escrow - refund``, capped at payoff).
        interest: The real interest the payment cleared, cent-quantized.
        escrow: The real escrow the payment cleared, cent-quantized.
    """

    installment: date
    cash: Decimal
    principal: Decimal
    interest: Decimal
    escrow: Decimal


@dataclass(frozen=True)
class LoanAnchorDrift:
    """One anchor event with the ledger's pre-correction balance (the drift row).

    The display row of :func:`loan_balance_anchor_history`: a loan's origination
    OPENING or a user balance TRUE-UP, paired with what the ledger had computed
    the balance to be the instant BEFORE the anchor reset it -- the running
    scorecard of recorded-vs-reality.

    ``drift = recorded - computed`` is the append-only jump the anchor booked:
    for a true-up it is "the lender said ``recorded``; my ledger had amortized to
    ``computed``, off by ``drift``."  For the origination opening ``computed`` is
    ``0.00`` (the loan opens from nothing), so ``drift`` equals the original
    principal and is NOT a meaningful correction -- the display treats the
    opening row specially (``is_opening``).

    Attributes:
        anchor_date: The date the balance was asserted (origination date for the
            opening).
        recorded: The asserted balance (``anchor_balance``): the original
            principal for the opening, the operator's dated assertion for a
            true-up.
        computed: The ledger's running balance JUST BEFORE this anchor's reset
            (the walk's ``owed_before``); ``0.00`` for the opening.
        drift: ``recorded - computed`` -- the correction the anchor booked.
        is_opening: ``True`` for the loan's opening (its origination), ``False``
            for a tracking-start or a user true-up (both balance assertions).
        is_tracking_start: ``True`` for a ``tracking_start`` assertion (a mid-life
            import's balance-as-of-date), so the display badges that row "Tracking
            start"; ``False`` for the origination opening (badged "Origination")
            and every user true-up.
    """

    anchor_date: date
    recorded: Decimal
    computed: Decimal
    drift: Decimal
    is_opening: bool
    is_tracking_start: bool


def confirmed_loan_payment_history(
    account: Account, balance_ctx: BalanceContext,
) -> list[LoanPaymentHistoryRow]:
    """Return a loan's confirmed payments split into their real economic parts.

    One :class:`LoanPaymentHistoryRow` per payment the read pass's walk holds
    (:meth:`~app.services.balance_at.BalanceContext.loan_walk`) --
    chronological by installment, each carrying the ACTUAL cash paid and its
    real principal / interest / escrow split as that ONE walk allocated it,
    never the schedule's contractual replay.

    **The pass's walk IS the confirmed set**, so the table states no bound of
    its own.  That walk holds a payment iff its visible day has arrived by the
    pass's ``as_of`` (plan step C2's one clock: its SETTLED day, or for a
    ``$0.00`` close the installment it skips;
    :func:`~app.services.loan_ledger.load_loan_stream`'s ``visible_by``,
    through :func:`~app.services.loan_ledger.confirmed_shadows_through`), the
    cut the balance and the confirmed schedule read off the same walk.  Until
    plan task plan#3081 this read the ledger's walk of every settled payment
    and kept each outcome whose ``visible_on`` had arrived by ``as_of``: the
    same rule stated a second time, since an outcome's ``visible_on`` is the
    :func:`~app.services.loan_ledger.payment_visible_on` that load keeps it by.

    **ONE walk, ONE load** (ruling **R-BAL102**).  Through plan step
    ``balance:X-bi-6-3`` this loaded the confirmed shadows once for the rows
    and summed each payment's posted legs back out of the ledger by the
    loan-side shadow's ``transaction_id`` in three more reads; the split has no
    row link now and the walk already holds every figure this table shows, so
    the walk is the table's one producer.  ``cash`` is the outcome's (the
    settled payment leg's parent's
    :func:`~app.services.row_valuation.settled_contribution`, read once by the
    walk's event); ``principal`` equals the payment's net on the loan's linked
    ledger by the split's construction; for an ordinary payment ``principal +
    interest + escrow == cash``; a payoff overpayment's surplus is a lender
    refund excluded from all three (see :class:`LoanPaymentHistoryRow`).

    Reads ``[]`` for a loan with no
    :class:`~app.models.loan_params.LoanParams` (ruling **plan#2917**), and
    for a configured loan until a payment of its in the pass's scenario is
    confirmed: the loan's opening is a term of the loan, not of a scenario.

    Reads only -- no writes, no commit.  The walk is the pass's memo, so the
    first surface of a render to ask may load it and every later one reads it.

    Args:
        account: The loan account whose confirmed payments to read.
        balance_ctx: The read pass: its scenario scopes the payments, and its
            ``as_of`` is the display boundary, which must be on or before
            ``date.today()``.  A payment whose cash has not moved by it is a
            forward projection, excluded on the SETTLED day -- not the pay
            period, which this said until plan step X-an corrected it.

    Returns:
        The chronological confirmed payment rows; empty for a loan with no
        terms or no confirmed payment.

    Raises:
        ValueError: If the pass's ``as_of`` is after ``date.today()`` (out of
            the confirmed reader's domain -- a future date is a forward
            projection).
        ForeignAccountError: When *account* belongs to another owner than the
            pass (:meth:`~app.services.balance_at.BalanceContext.loan_walk`).
        BaselineMissingError: When the pass has no baseline scenario.
    """
    if balance_ctx.as_of > date.today():
        raise ValueError(
            f"confirmed_loan_payment_history answers only as_of <= today; got "
            f"{balance_ctx.as_of.isoformat()}.  A future date is a forward "
            f"projection."
        )
    walk = balance_ctx.loan_walk(account)
    # Sorted by the payment's own DUE date, matching how the ledger seam's
    # confirmed view orders its rows, and so by the INSTALLMENT it pays too:
    # ``installment_paid_by`` never falls as the due date rises.  The walk's
    # outcomes arrive in CONTRACT order already, but a payment pre-paid for a
    # later installment and one paid late for an earlier one are ordered here
    # by the same key the schedule rows by, and the payment's TRANSFER id
    # breaks a tie (two payments due on one day) with the stable recording
    # order.  It was the shadow's id until plan step balance:X-bi-6-4b; the
    # two orders agree wherever a transfer's shadows were written with it (0
    # inversions measured on the 2026-09-23 21:17 production dump).
    by_installment = sorted(
        walk.settled_splits,
        key=lambda outcome: (outcome.due_date, outcome.source.transfer.id),
    )
    # The loan's calendar rides on the walk's stream (ruling R-R100), so the
    # installment each row is named by is placed on the one calendar the walk
    # charged it against.
    calendar = walk.stream.calendar
    return [
        LoanPaymentHistoryRow(
            installment=installment_paid_by(
                calendar.origination_date, calendar.payment_day,
                outcome.due_date,
            ),
            cash=round_money(outcome.cash),
            principal=round_money(outcome.principal),
            interest=round_money(outcome.interest),
            escrow=round_money(outcome.escrow),
        )
        for outcome in by_installment
    ]


def loan_balance_anchor_history(
    account: Account, balance_ctx: BalanceContext,
) -> list[LoanAnchorDrift]:
    """Return a loan's anchor events with the walk's pre-correction balance.

    One :class:`LoanAnchorDrift` per anchor the loan carries on or before the
    pass's ``as_of`` -- its origination OPENING and every user balance TRUE-UP
    -- chronological, each paired with what the read pass's walk
    (:meth:`~app.services.balance_at.BalanceContext.loan_walk`) had computed
    the balance to be the instant BEFORE the anchor reset it (the walk's
    ``owed_before``).  The drift (``recorded - computed``) is the append-only
    jump the anchor booked: the running scorecard of the operator's asserted
    balance vs the walk's replay that replaces the old use of the amortization
    schedule as a trust check.

    **The pass's walk, and the ledger's.**  The worked-out ledger books the
    loan's opening / true-up corrections
    (:func:`app.services.ledger_lines.loan_correction_targets`) off its own
    walk of every recorded fact
    (:func:`app.services.loan_ledger.walk_loan_ledgers`), where this reads the
    facts visible by the pass's ``as_of``.  The two are one walk for a pass on
    or after every recorded fact, the page's, so there a drift row and the
    correction it describes agree; the module docstring says where a pass
    pinned earlier parts them.  Until plan task plan#3081 this read the
    ledger's walk, and said the two could never disagree.

    **The bound here is what a drift ROW is**: an anchor dated after ``as_of``
    has not yet reset the balance.  The pass's walk holds an assertion (a
    true-up or a tracking start) only from its own date, but the OPENING
    always -- a loan configured to close next
    month projects its debt from it
    (:func:`~app.services.loan_ledger.load_loan_stream`) -- so on that walk the
    bound drops exactly an opening whose date has not arrived, and a loan that
    has not originated by ``as_of`` correctly shows NO rows: nothing has
    happened to it yet.  It is stated over every anchor because it is this
    scorecard's rule, not a copy of the load's.  Dropping a row cannot change
    what a surviving one says: an anchor's ``owed_before`` is the running
    balance of the events BEFORE it, and the opening is the first.

    Reads ``[]`` for a loan with no
    :class:`~app.models.loan_params.LoanParams` (ruling **plan#2917**): it
    walks to nothing.  A configured loan always has at least the synthesized
    origination opening.

    Reads only -- no writes, no commit.

    Args:
        account: The loan account whose anchor history to read.
        balance_ctx: The read pass: its scenario holds the payments that drive
            the running balance the drift is measured against, and its
            ``as_of`` is the display boundary.

    Returns:
        The chronological anchor drift rows (origination first); empty for a
        loan with no terms or one not originated by the pass's ``as_of``.

    Raises:
        ForeignAccountError: When *account* belongs to another owner than the
            pass (:meth:`~app.services.balance_at.BalanceContext.loan_walk`).
        BaselineMissingError: When the pass has no baseline scenario.
    """
    walk = balance_ctx.loan_walk(account)
    corrections = [
        correction
        for correction in walk.anchor_corrections
        if correction.anchor.anchor_date <= balance_ctx.as_of
    ]
    return [
        LoanAnchorDrift(
            anchor_date=correction.anchor.anchor_date,
            recorded=round_money(correction.anchor.anchor_balance),
            computed=round_money(correction.owed_before),
            drift=round_money(
                correction.anchor.anchor_balance - correction.owed_before
            ),
            is_opening=correction.anchor.is_opening,
            is_tracking_start=correction.anchor.is_tracking_start,
        )
        for correction in corrections
    ]
