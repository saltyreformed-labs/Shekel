"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_loan_posting_service.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.services.ledger_lines import loan_key
from tests._ledger_reads import (
    correction_net_in_period,
    ledger_net,
    linked_key_of,
    source_lines,
    source_net,
)
from tests._stored_ledger import (
    find_loan_ledger_account,
    ledger_accounts_for_account,
    loan_correction_entries,
    transfer_family_journal_filter,
)
from tests._test_helpers import (
    create_loan_with_trueup,
    create_settled_transfer,
    SPLIT_LOAN,
    transfer_side_record,
)


# The shared synthetic split-loan fixture ($250,000 @ 6%, trued up to $100,000 --
# distinct so a correct interest figure proves the walk's anchor reset); see
# SPLIT_LOAN in tests/_test_helpers/_loans.py.  _P1/_P2/_P3 are the seed_periods indices
# (payment_day=1) due 02-01 / 03-01 / 04-01, in distinct months after the anchor.
_ORIGINATION_PRINCIPAL = SPLIT_LOAN.origination_principal


_ORIGINATION_DATE = SPLIT_LOAN.origination_date


_RATE = SPLIT_LOAN.rate


_ANCHOR_BALANCE = SPLIT_LOAN.anchor_balance


_ANCHOR_DATE = SPLIT_LOAN.anchor_date


_P1 = SPLIT_LOAN.p1


_P2 = SPLIT_LOAN.p2


_P3 = SPLIT_LOAN.p3


_AS_OF = date(2026, 12, 31)


# The origination a case takes when a payment is walked BEFORE its true-up: the
# month before P1's 02-01 installment (plan step recurrence:R16-c-2, ruling
# R-R101).  Every contractual installment from origination is charged now, so
# SPLIT_LOAN's 2025-01-01 would have that payment clear twelve unpaid months
# first; restated, each figure stays what these cases were written to pin.
_ORIGINATION_BEFORE_P1 = date(2026, 1, 1)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


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
    suite's fixed origination principal / date (distinct from the anchor, so a
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

    Creates and settles the transfer through ``transfer_service`` (so the
    Step-2 cash entry auto-posts) and returns it.  It returned the loan-side
    income shadow beside it until plan step ``balance:X-bi-6-4d-2`` made a
    transfer one row (ruling R-BAL167 class 4): the transfer and the loan
    name the side the split books under now.

    ``settled_on`` (a civil date) pins the payment's settle day, so a test
    reading a PAST balance can place the payment on a known day -- balance step
    C2 keys a payment's visibility on its SETTLED date.  Left ``None`` it keeps
    the fixture's realistic "settled today" default.
    """
    kwargs = {"amount": cash, "settled_amount": actual}
    if settled_on is not None:
        kwargs["settled_on"] = settled_on
    xfer = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], loan, period, **kwargs,
    )
    return xfer


def _linked_ledger_id(account):
    """Return the linked (Asset/Liability) ledger account id for *account*."""
    return ledger_accounts_for_account(_db.session, account.id)[0].id


def _find_loan_ledger(loan_id, kind):
    """Return the per-loan ledger account of *kind*, or None (shared helper)."""
    return find_loan_ledger_account(_db.session, loan_id, kind)


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


def _genesis_entry_count(user_id):
    """Count a user's loan opening + true-up (genesis) journal entries."""
    opening = ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING)
    trueup = ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP)
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.source_kind_id.in_([opening, trueup]),
        )
        .count()
    )


def _loan_side_movement(xfer, loan):
    """Return the loan side's covering movement (its record).

    The movement filed under *xfer*'s to-side on *loan*, read by its own SQL
    (:func:`~tests._test_helpers.transfer_side_record`).  It hung off the
    income shadow (``shadow.covering_movements``) until plan step
    ``balance:X-bi-6-4d-2`` moved it onto the transfer, and the shadow named
    the transfer and side until plan step ``balance:X-bi-6-4d-2`` (ruling
    R-BAL167 class 4).
    """
    movement = transfer_side_record(_db.session, xfer.id, loan.id)
    assert movement is not None, (
        f"transfer {xfer.id}'s side on account {loan.id} holds no record"
    )
    return movement


def _cash_reader_loan_net(xfer, loan, ledger_id):
    """Sum a payment's CASH postings on one ledger, as the cash reader does.

    Mirrors the cash source's own read exactly -- the loan-side movement's
    ``transaction_entry_id`` under the ``transfer_movement`` source kind, the
    filter ``loan_posting_service._linked_ledger._movement_nets_by_date`` and
    the writer's reconcile both apply since plan step ``balance:X-bi-6-3``
    (rulings **R-BAL45**, **R-BAL101**) -- WITHOUT calling either, so a test
    can prove a Step-4 correction is invisible to that reader.  It mirrored
    ``JournalEntry.transfer_id == transfer_id`` while the cash was one entry
    per transfer.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.transaction_entry_id
            == _loan_side_movement(xfer, loan).id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER_MOVEMENT,
            ),
            Posting.ledger_account_id == ledger_id,
        )
        .scalar()
    )


def _correction_entries(xfer, loan):
    """Return the loan_payment corrections at a payment's key (shared query helper)."""
    return loan_correction_entries(_db.session, xfer.id, loan.id)


def _split_and_interest(xfer, loan, scenario_id):
    """The payment's split as ``(its entry count, the loan_interest ledger's net)``.

    The ledger is the loan's, so its net is the split's interest only while
    the payment is the loan's one split.
    """
    return (
        len(_correction_entries(xfer, loan)),
        _interest_net(loan, scenario_id),
    )


def _entry_legs(entry_id):
    """Return ``{ledger_account_id: (amount, posting_kind_id)}`` for an entry."""
    return {
        leg.ledger_account_id: (leg.amount, leg.posting_kind_id)
        for leg in _db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


# ---------------------------------------------------------------------------
# sync_loan_postings -- opening + true-up (the genesis read switch)
# ---------------------------------------------------------------------------


def _anchor_correction_entries(loan_id, scenario_id, source_enum):
    """Return a loan's anchor-correction entries of *source_enum* (linked-scoped).

    Finds the ``loan_opening`` / ``loan_trueup`` journal entries in *scenario_id*
    that touch the loan's linked ledger -- the way the reconcile scopes them to
    one loan (the linked ledger is per-account).
    """
    linked_id = ledger_accounts_for_account(_db.session, loan_id)[0].id
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                source_enum,
            ),
            JournalEntry.id.in_(
                _db.session.query(Posting.journal_entry_id)
                .filter(Posting.ledger_account_id == linked_id)
            ),
        )
        .all()
    )


def _anchor_net_in_period(loan_id, scenario_id, source_enum, period_id):
    """Sum a loan's anchor-correction legs on its LINKED ledger in ONE period.

    Defers to the shared ``tests._ledger_reads.correction_net_in_period`` -- the
    same reading the account anchor suite makes, so the two halves of the R2 fix
    are graded by one read rather than two that happen to agree.
    """
    return correction_net_in_period(
        _db.session, linked_key_of(_db.session, loan_id), scenario_id,
        source_enum, period_id,
    )


# ---------------------------------------------------------------------------
# What the posted legs SUM to, read through the dated posting window
# ---------------------------------------------------------------------------

# The frozen "today" for the class below: after the Jan-May 2026 seed periods and
# the 2026-12-31 _AS_OF, so the genesis walk is eligible for every fixture and no
# assertion depends on the wall clock.
_FROZEN_TODAY = date(2027, 1, 1)


# ---------------------------------------------------------------------------
# The checked projection (plan step E1a): sum(postings) == fold(events),
# asserted per visible date at every write, and the date-aware reconcile
# (finding N-13) that makes the assert safe on a legitimate settle-day edit.
# ---------------------------------------------------------------------------


def _source_entry_count(transfer_id, loan_account_id):
    """Count the journal entries a payment's lineage owns (cash + corrections).

    The cash is two per-movement entries since plan step ``balance:X-bi-6-3``
    (``transfer_family_journal_filter``); the split is a date-keyed correction
    with no row link (ruling **R-BAL102**), so the corrections counted are
    every ``loan_payment`` entry on the loan's chart rows -- the tests here
    hold one payment per loan, and a convergence check ("a repeat sync writes
    nothing") is a count over the whole lineage either way.
    """
    # The reconcile's own scope helper, imported here so the count reads the
    # same rows the writer does.
    # pylint: disable-next=import-outside-toplevel
    from app.services._posting_reconcile import account_chart_row_ids

    on_loan_rows = (
        _db.session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id.in_(
            account_chart_row_ids(loan_account_id),
        ))
    )
    return (
        _db.session.query(JournalEntry)
        .filter(
            _db.or_(
                transfer_family_journal_filter(transfer_id),
                _db.and_(
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.LOAN_PAYMENT,
                    ),
                    JournalEntry.id.in_(on_loan_rows),
                ),
            )
        )
        .count()
    )
