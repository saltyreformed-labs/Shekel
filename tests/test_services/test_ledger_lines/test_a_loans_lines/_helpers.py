"""The fixture and helpers every part of this package shares: the split loan, a payment, a read."""

from __future__ import annotations

from datetime import date

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum
from app.extensions import db as _db
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.services.ledger_lines import linked_key, loan_key
from tests._ledger_reads import (
    ledger_net,
    linked_key_of,
    loan_correction_lines,
    loan_correction_lines_at,
    source_lines,
    source_net,
)
from tests._test_helpers import (
    SPLIT_LOAN,
    create_loan_with_trueup,
    create_settled_transfer,
    transfer_side_record,
)

# The shared synthetic split-loan fixture ($250,000 @ 6%, trued up to $100,000 --
# distinct so a correct interest figure proves the walk's anchor reset); see
# SPLIT_LOAN in tests/_test_helpers.py.  _P1/_P2/_P3 are the seed_periods indices
# (payment_day=1) due 02-01 / 03-01 / 04-01, in distinct months after the anchor.
(_ORIGINATION_PRINCIPAL, _ORIGINATION_DATE, _RATE, _ANCHOR_BALANCE,
 _ANCHOR_DATE, _P1, _P2, _P3) = SPLIT_LOAN
_AS_OF = date(2026, 12, 31)
# The origination a case takes when a payment is walked BEFORE its true-up: the
# month before P1's 02-01 installment (plan step recurrence:R16-c-2, ruling
# R-R101).  Every contractual installment from origination is charged now, so
# SPLIT_LOAN's 2025-01-01 would have that payment clear twelve unpaid months
# first; restated, each figure stays what these cases were written to pin.
_ORIGINATION_BEFORE_P1 = date(2026, 1, 1)


def _loan_params(loan):
    """Return the loan account's :class:`LoanParams` row."""
    return _db.session.query(LoanParams).filter_by(account_id=loan.id).one()


def _make_loan(
    seed_user,
    *,
    anchor_balance=_ANCHOR_BALANCE,
    anchor_date=_ANCHOR_DATE,
    rate=_RATE,
    escrow_annual=None,
    name="Split Loan",
    origination_date=_ORIGINATION_DATE,
):
    """Create an amortizing loan with a controlled user-trueup anchor.

    Delegates to the shared ``create_loan_with_trueup`` factory, pinning this
    package's fixed origination principal / date (distinct from the anchor, so a
    correct interest figure proves the walk seeds from the trueup anchor).  A
    distinct *name* lets one owner carry more than one loan (the account name is
    unique per user).  A case that walks a payment before its true-up passes
    :data:`_ORIGINATION_BEFORE_P1` as *origination_date* (ruling R-R101).
    """
    return create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=origination_date, escrow_annual=escrow_annual,
        name=name,
    )


def _add_rate_change(loan, effective_date, rate):
    """Append a :class:`RateHistory` rate change (recasting the period)."""
    _db.session.add(RateHistory(
        account_id=loan.id, effective_date=effective_date, interest_rate=rate,
    ))
    _db.session.commit()


def _settle_payment(seed_user, loan, period, cash, actual=None, settled_on=None):
    """Settle a Checking -> loan payment transfer; return the transfer.

    Created and settled through ``transfer_service``.  It also drove the stored
    ledger's two writers until plan step plan#2914 deleted them; the
    worked-out read follows the records, so a settle is all a case needs.

    ``settled_on`` (a civil date) pins the payment's settle day, so a test
    reading a PAST balance can place the payment on a known day -- balance step
    C2 keys a payment's visibility on its SETTLED date.  Left ``None`` it keeps
    the fixture's realistic "settled today" default.
    """
    kwargs = {"amount": cash, "settled_amount": actual}
    if settled_on is not None:
        kwargs["settled_on"] = settled_on
    return create_settled_transfer(
        seed_user, _db.session, seed_user["account"], loan, period, **kwargs,
    )


def _loan_side_movement(xfer, loan):
    """Return the loan side's covering movement (its record).

    The movement filed under *xfer*'s to-side on *loan*, read by its own SQL
    (:func:`~tests._test_helpers.transfer_side_record`).  It hung off the
    income shadow (``shadow.covering_movements``) until plan step
    ``balance:X-bi-6-4d-2`` moved it onto the transfer (ruling R-BAL167
    class 4).
    """
    movement = transfer_side_record(_db.session, xfer.id, loan.id)
    assert movement is not None, (
        f"transfer {xfer.id}'s side on account {loan.id} holds no record"
    )
    return movement


def _ledger_net(kind, loan, scenario_id):
    """Return the net of one of the loan's own chart accounts, worked out."""
    return ledger_net(_db.session, loan_key(kind, loan.id), scenario_id)


def _interest_net(loan, scenario_id):
    """Return the net of the loan's interest account, worked out."""
    return _ledger_net(LedgerAccountKindEnum.LOAN_INTEREST, loan, scenario_id)


def _equity_net(loan, scenario_id):
    """Return the net of the loan's opening-equity account, worked out."""
    return _ledger_net(LedgerAccountKindEnum.EQUITY_OPENING, loan, scenario_id)


def _anchor_net(loan, scenario_id, source_enum):
    """Return one anchor kind's net on the loan's own line, worked out."""
    return source_net(
        _db.session, linked_key_of(_db.session, loan.id), scenario_id, source_enum,
    )


def _anchor_lines(loan, scenario_id, source_enum):
    """Return one anchor kind's lines on the loan's own line, worked out."""
    return source_lines(
        _db.session, linked_key_of(_db.session, loan.id), scenario_id, source_enum,
    )


def _by_key(lines):
    """Return a split's *lines* as ``{chart key: (amount, posting kind id)}``.

    A split links no row (ruling **R-BAL102**), and the read merges a key's
    legs per chart account, so each line is asserted to carry no movement and
    no chart account to hold two.
    """
    legs = {}
    for line in lines:
        assert line.movement_id is None, f"a split line names movement {line.movement_id}"
        assert line.key not in legs, f"two split lines on {line.key} at one key"
        legs[line.key] = (line.amount, line.posting_kind_id)
    return legs


def _split_lines(xfer, loan):
    """Return the split's lines at a settled payment's key, by chart account.

    The key is the payment's paycheck and its loan side's settle day
    (:func:`tests._ledger_reads.loan_correction_lines`).
    """
    return _by_key(loan_correction_lines(_db.session, xfer.id, loan.id))


def _split_lines_at(loan, scenario_id, period_id, day):
    """Return the split's lines at ONE key, by chart account.

    For a key no dated loan-side record names: after a revert or a delete, or
    a ``$0.00`` close, which is dated by its installment (ruling R-BAL139).
    """
    return _by_key(loan_correction_lines_at(
        _db.session, loan.id, scenario_id, period_id, day,
    ))


def _a_split(loan, loan_leg, interest):
    """Return the split a payment with no escrow and no excess books.

    Args:
        loan: The loan.
        loan_leg: The loan's own leg, the interest backed out of its cash
            (kind ``principal``).
        interest: The interest leg.

    Returns:
        ``{chart key: (amount, posting kind id)}``, as :func:`_split_lines`.
    """
    return {
        linked_key(loan): (
            loan_leg, ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
        ),
        loan_key(LedgerAccountKindEnum.LOAN_INTEREST, loan.id): (
            interest, ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
        ),
    }
