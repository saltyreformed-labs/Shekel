"""Loan-detail display read producers: the Loop B rebuild's measured surfaces.

The display read side of the genesis loan sub-ledger: the loan DETAIL page's
two measured surfaces, both shaped from the ONE walk the postings are
reconciled from (:func:`app.services.loan_ledger.walk_loan_ledger`):

* the confirmed payment-history table (:func:`confirmed_loan_payment_history`,
  each payment's real cash / principal / interest / escrow split); and
* the balance-anchors drift scorecard (:func:`loan_balance_anchor_history`, each
  opening / true-up paired with what the ledger had computed just before it).

**The payment table reads the WALK, not the ledger's legs** (plan step
``balance:X-bi-6-3``, ruling **R-BAL102**).  Through that step a reader
module beside this one (``_reader``, deleted) summed each payment's posted
interest / escrow / principal legs back out of the ledger by the loan-side
shadow's ``transaction_id``, so the table would "cross-check the writer".  The
split is a date-keyed correction with no row link now, and two payments on
one day in one period share ONE entry, so the ledger cannot say which payment
paid what -- and never needed to: the E1a write-time assert
(:func:`._sync._assert_checked_projection`) already grades the posted ledger
against this same walk per date, and a display-side second grade was a
checker.  The paid-in-year chips moved OFF the postings onto the fold at step
C6c (:func:`app.services.balance_at.loan_interest_paid_in_year` /
:func:`~app.services.balance_at.loan_principal_paid_in_year`), so this module
no longer answers them.  Reads only -- no writes, no commit.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.installment_calendar import installment_paid_by
from app.services.loan_ledger import LoanLedgerWalk, walk_loan_ledger
from app.services.loan_loaders import load_loan_params
from app.services.posting_service import _ledger_account_for
from app.utils.money import round_money

from ._linked_ledger import _has_opening_posting


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
    loan_account_id: int, scenario_id: int, as_of: date,
) -> list[LoanPaymentHistoryRow] | None:
    """Return a loan's confirmed payments split into their real economic parts.

    One :class:`LoanPaymentHistoryRow` per confirmed payment whose SETTLED date
    has arrived by *as_of* (plan step C2's one clock: the outcome's
    ``visible_on``, the same cut the balance readers and the seam's confirmed
    view apply through :func:`~app.services.loan_ledger.confirmed_shadows_through`,
    so the table agrees with the balance and the schedule) -- chronological by
    installment, each carrying the ACTUAL cash paid and its real principal /
    interest / escrow split as the ONE walk allocated it
    (:func:`~app.services.loan_ledger.walk_loan_ledger`), never the schedule's
    contractual replay.

    **ONE walk, ONE load** (ruling **R-BAL102**).  Through plan step
    ``balance:X-bi-6-3`` this loaded the confirmed shadows once for the rows
    and summed each payment's posted legs back out of the ledger by the
    loan-side shadow's ``transaction_id`` in three more reads; the split has no
    row link now and the walk the ledger is reconciled from already holds
    every figure this table shows, so the walk is the table's one producer.
    ``cash`` is the outcome's (the settled payment leg's
    parent's :func:`~app.services.row_valuation.settled_contribution`, read once by
    the walk's event); ``principal`` equals the payment's net on the loan's linked
    ledger by the split's construction; for an ordinary payment ``principal +
    interest + escrow == cash``; a payoff overpayment's surplus is a lender
    refund excluded from all three (see :class:`LoanPaymentHistoryRow`).

    Returns ``None`` when the loan has no
    :class:`~app.models.loan_params.LoanParams` or no OPENING
    posting in the scenario (unconfigured / un-backfilled), so the caller hides
    the section rather than showing a misleading empty table -- the same fallback
    contract as the balance reader beside it.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose confirmed payments to read.
        scenario_id: The budget scenario to scope to.
        as_of: The display boundary; must be on or before ``date.today()``.  A
            payment whose cash has not moved by it is a forward projection,
            excluded on the SETTLED day -- not the pay period, which this said
            until plan step X-an corrected it.

    Returns:
        The chronological confirmed payment rows (possibly empty for a configured
        loan with no confirmed payment yet), or ``None`` when the ledger cannot
        answer for this loan / scenario.

    Raises:
        ValueError: If *as_of* is after ``date.today()`` (out of the confirmed
            reader's domain -- a future date is a forward projection).
        PostingError: If the loan account has no linked ledger account (from
            :func:`~app.services.posting_service._ledger_account_for`).
    """
    if as_of > date.today():
        raise ValueError(
            f"confirmed_loan_payment_history answers only as_of <= today; got "
            f"{as_of.isoformat()}.  A future date is a forward projection."
        )
    walk = _configured_loan_walk(loan_account_id, scenario_id)
    if walk is None:
        return None
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
        (
            outcome for outcome in walk.settled_splits
            if outcome.visible_on <= as_of
        ),
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


def _configured_loan_walk(
    loan_account_id: int, scenario_id: int,
) -> LoanLedgerWalk | None:
    """Walk a CONFIGURED loan's ledger, or return ``None`` when it cannot answer.

    The entry guard behind the payment-history table
    (:func:`confirmed_loan_payment_history`): a configured loan
    (:class:`~app.models.loan_params.LoanParams`) with an OPENING posting in the
    scenario, then the ONE walk.  ``None`` when the ledger cannot answer -- no
    params, or no opening posting -- so the surface hides on the identical
    condition the balance reader beside it hides on.

    Args:
        loan_account_id: The loan account to walk.
        scenario_id: The budget scenario to scope to.

    Returns:
        The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`, or
        ``None`` when the loan is unconfigured / not opened in the scenario.

    Raises:
        PostingError: If the loan account has no linked ledger account.
    """
    if load_loan_params(loan_account_id) is None:
        return None
    linked = _ledger_account_for(loan_account_id)
    if not _has_opening_posting(linked.id, scenario_id):
        return None
    return walk_loan_ledger(loan_account_id, scenario_id)


def loan_balance_anchor_history(
    loan_account_id: int, scenario_id: int, as_of: date,
) -> list[LoanAnchorDrift] | None:
    """Return a loan's anchor events with the ledger's pre-correction balance.

    One :class:`LoanAnchorDrift` per anchor the loan carries on or before *as_of*
    -- its origination OPENING and every user balance TRUE-UP -- chronological,
    each paired with what the genesis walk had computed the balance to be the
    instant BEFORE the anchor reset it (the walk's ``owed_before``).  The drift
    (``recorded - computed``) is the append-only jump the anchor booked: the
    running scorecard of the operator's asserted balance vs the ledger's replay
    that replaces the old use of the amortization schedule as a trust check.

    Derived from the SAME deterministic walk
    (:func:`app.services.loan_ledger.walk_loan_ledger`) the loan's
    opening / true-up postings are reconciled from, so a drift row and the posted
    correction it describes can never disagree.

    **The *as_of* bound is applied HERE, on the walk's output, not inside it.**
    The walk records every anchor the loan carries whatever its date (it reads no
    clock); deciding which have HAPPENED by a display date is this reader's job,
    and an anchor dated after *as_of* has not yet reset the balance, so it is not
    yet a drift row.  Filtering after the walk cannot change what the surviving
    rows say: an anchor's ``owed_before`` is the running balance of the events
    BEFORE it, which admitting a LATER anchor cannot move.

    Returns ``None`` when the loan has no
    :class:`~app.models.loan_params.LoanParams` (unconfigured -- not
    a loan yet), so the caller hides the card.  A configured loan always has at
    least the synthesized origination opening -- though a loan that has not
    originated by *as_of* correctly shows NO rows: nothing has happened to it yet.

    Reads only -- no writes, no commit.

    Args:
        loan_account_id: The loan account whose anchor history to read.
        scenario_id: The budget scenario the payments live in (drives the running
            balance the drift is measured against).
        as_of: The display boundary; an anchor dated after it has not yet reset
            the balance and is excluded.

    Returns:
        The chronological anchor drift rows (origination first), or ``None`` when
        the account is not a configured loan.
    """
    if load_loan_params(loan_account_id) is None:
        return None
    corrections = [
        correction
        for correction in walk_loan_ledger(
            loan_account_id, scenario_id,
        ).anchor_corrections
        if correction.anchor.anchor_date <= as_of
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
