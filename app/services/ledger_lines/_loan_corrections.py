"""A loan's corrections as ledger legs: its opening, true-ups and payment splits.

A loan's ledger is three kinds of balanced correction, all projected from ONE
running-balance walk (:func:`app.services.loan_ledger.walk_loan_ledger`) --
the opening and every true-up (:mod:`._loan_anchors`) and every settled
payment's principal / interest / escrow / refund split
(:mod:`._loan_payments`) -- and this module is their ONE combined answer
(plan task plan#2526), which the worked-out read sums (the stored ledger's
writer reconciled to it, ``loan_posting_service.reconcile_loan_corrections``,
until plan task plan#3133 deleted it).  The two halves must come off the same
walk: interest accrues on the running balance the anchors reset, so a
pre-true-up payment change re-splits
every later payment AND moves the true-up's ``owed_before``.

Pure over the walk it is handed: no query, no write.
"""

from app.models.account import Account
from app.services.loan_ledger import LoanLedgerWalk
from app.services.pay_calendar import PayCalendar

from ._loan_anchors import anchor_correction_targets
from ._loan_payments import payment_split_targets
from ._targets import ChartLegMap, CorrectionKey


def loan_correction_targets(
    walk: LoanLedgerWalk, loan: Account, calendar: PayCalendar,
) -> dict[CorrectionKey, ChartLegMap]:
    """Return a loan's opening, true-up and payment-split legs, keyed per correction.

    The anchors' targets (each filed by its day,
    :meth:`~app.services.pay_calendar.PayCalendar.filing_period`, ruling
    plan#2519) and the splits' (each in its payment's own pay period), in one
    map.  The two share no key: the split's carries the ``loan_payment``
    source kind and the anchors' the opening / true-up kinds, so a plain
    update merges them (ruling **R-BAL102**).

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`
            -- its ``anchor_corrections`` and ``settled_splits``, whole.
        loan: The loan the walk is of, with ``account_type`` loaded.
        calendar: The owner's whole pay calendar
            (:func:`app.services.pay_calendar.calendar_for`).

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {chart key: (amount,
        posting_kind_id)}}``.

    Raises:
        PayCalendarError: From
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period`, when
            an anchor has no materialised pay period to file in.
    """
    target = anchor_correction_targets(walk.anchor_corrections, loan, calendar)
    target.update(payment_split_targets(walk.settled_splits, loan))
    return target
