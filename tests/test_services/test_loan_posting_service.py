"""Tests for the genesis loan posting service (Build-Order Step 4 + read switch).

:mod:`app.services.loan_posting_service` posts a loan's confirmed history into the
double-entry ledger: the per-payment principal / interest / escrow / refund split
(layered on the Step-2 cash entry), and the once-per-loan OPENING plus every user
TRUE-UP as balanced anchor corrections.  Both derive from ONE chronological
running-balance walk that seeds at origination and RESETS at each anchor, and
both are reconciled by ONE loop (``sync_loan_postings``, since plan step
``balance:X-bi-6-3``, ruling **R-BAL102**: the split is a date-keyed
correction with no row link, exactly as the anchors are).  The payment split is
wired into the transfer chokepoints (auto-posted on settle via
``transfer_service``); the tests below drive the same one sync directly.  The
two single-half doors this module used to drive (``sync_loan_payment_postings``
/ ``sync_loan_anchor_corrections``) went with the split's row key -- a rule-5
class (b) re-expression Josh confirmed 2026-09-21.  TWO pinned figures moved
with it, both -99000 -> -100000 and both named in their own docstrings
(``test_hard_delete_reverses_the_correction_at_its_own_key``,
``test_resync_reverses_an_unsettled_payment``): each pinned a state only the
deleted direct doors produced -- the split reversed with the cash leg still
posted -- which no production door reaches.  What the posted legs SUM to is
read back through the test
suite's dated posting window (``tests._test_helpers.posted_loan_balance_at`` /
``posted_loan_balance_map``) -- plan step E1e deleted the production readers that
used to answer that, since nothing in ``app/`` called them; the values pinned
below are the LEDGER's, and the window is only how the test looks at it.  Those
tests freeze ``date.today()`` so the wiring's sync-as-of is deterministic across
CI clocks; the ledger they read is the same genesis ledger the split + anchor
tests above build.

The split fixtures are SYNTHETIC with HAND-COMPUTED literals: a $100,000 balance
at 6% gives a clean $500.00 first-month interest (``100000 * 0.06 / 12``), so
every expected interest / principal / escrow / refund is computed by hand and
shown in the docstring's arithmetic.  The loan's user-trueup anchor ($100,000)
deliberately differs from its origination principal ($250,000): under genesis the
walk seeds from origination and resets at the trueup, so an asserted $500 (vs the
$1,250 the origination balance would give) PROVES the reset, while a payment
placed BEFORE the trueup asserting $1,250 proves origination seeding (built-in
non-vacuity checks).

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.escrow_line import EscrowComponentVersion
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.models.scenario import Scenario
from app.services import (
    loan_ledger,
    loan_loaders,
    loan_posting_service,
    pay_period_admin,
    posting_service,
    status_seam,
    transaction_service,
    transfer_service,
)
from app.services._posting_write import _emit_balanced_entry, _PostingLeg
from app.services.transfer_legs import transfer_side_leg
from tests._test_helpers import (
    SPLIT_LOAN,
    add_escrow_line,
    an_entered_day,
    clear_loan_ledger,
    correction_net_in_period,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
    find_loan_ledger_account,
    freeze_today,
    insert_tracking_start_event,
    insert_trueup_event,
    last_covered_day,
    ledger_accounts_for_account,
    ledger_net,
    linked_net_by_date,
    loan_correction_entries,
    loan_correction_entries_at,
    on_both_sides,
    posted_loan_balance_at,
    posted_loan_balance_map,
    transfer_family_journal_filter,
    transfer_side_record,
    typed,
)
from app.models.amount_ownership import AmountOwnership

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


def _ledger_net(ledger_id, scenario_id):
    """Return the net of a ledger account's posting legs (shared query helper)."""
    return ledger_net(_db.session, ledger_id, scenario_id)


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
    interest_ledger = _find_loan_ledger(
        loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
    )
    return (
        len(_correction_entries(xfer, loan)),
        _ledger_net(interest_ledger.id, scenario_id),
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
# compute_loan_payment_splits -- hand-computed splits (the core)
# ---------------------------------------------------------------------------


class TestComputeLoanPaymentSplits:
    """The real split is computed from the actual cash, hand-computed literals."""

    def test_on_schedule_single_payment(self, app, db, seed_user, seed_periods):
        """One $1,000 payment: interest 500.00, principal 500.00, no escrow.

        Arithmetic: interest = round(100000 * 0.06 / 12) = 500.00; principal =
        cash - interest - escrow = 1000 - 500 - 0 = 500.00; excess = 0.  The
        500.00 interest also proves the walk seeds from the $100,000 trueup
        anchor (origination principal is $250,000, which would give $1,250).
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert len(splits) == 1
            split = splits[0]
            assert split.interest == Decimal("500.00")
            assert split.escrow == Decimal("0.00")
            assert split.principal == Decimal("500.00")
            assert split.excess == Decimal("0.00")

    def test_running_balance_across_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Three $1,000 payments accrue interest on the shrinking real balance.

        Arithmetic (6%/12 = 0.5% monthly):
          P1: interest round(100000.00*0.005)=500.00, principal 500.00,
              balance 99500.00
          P2: interest round(99500.00*0.005)=497.50, principal 502.50,
              balance 98997.50
          P3: interest round(98997.50*0.005)=494.99, principal 505.01,
              balance 98492.49
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2], seed_periods[_P3]):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("500.00"), Decimal("500.00")),
                (Decimal("497.50"), Decimal("502.50")),
                (Decimal("494.99"), Decimal("505.01")),
            ]

    def test_extra_principal_lands_in_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,500 payment pays $1,000 principal -- the extra is captured.

        Arithmetic: interest 500.00; principal = 1500 - 500 - 0 = 1000.00.  The
        resolver's contractual replay would book only the scheduled principal
        and need a true-up; the ledger captures the extra automatically.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1500.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].principal == Decimal("1000.00")
            assert splits[0].excess == Decimal("0.00")

    def test_short_payment_gives_negative_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $400 payment under-covers interest: principal -100.00, balance rises.

        Arithmetic: interest 500.00; principal = 400 - 500 - 0 = -100.00
        (negative amortization, surfaced not clamped); the next-period balance
        would be 100000 - (-100) = 100100.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("400.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].interest == Decimal("500.00")
            assert splits[0].principal == Decimal("-100.00")
            assert splits[0].excess == Decimal("0.00")

    def test_configured_escrow_is_subtracted(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,200/yr escrow component subtracts $100.00/mo from principal.

        Arithmetic: monthly escrow = round(1200 / 12) = 100.00; interest 500.00;
        principal = 1000 - 500 - 100 = 400.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user, escrow_annual=Decimal("1200.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].escrow == Decimal("100.00")
            assert splits[0].principal == Decimal("400.00")

    def test_escrow_change_is_effective_dated_not_retroactive(
        self, app, db, seed_user, seed_periods,
    ):
        """Each payment's escrow is the version in effect ON its date, and a
        LATER escrow change never re-splits an already-past payment.

        One escrow line, two effective-dated versions (supersession, no
        end_date): $1,200/yr ($100.00/mo) from origination, superseded by
        $2,400/yr ($200.00/mo) on 2026-03-01.  P1's installment (2026-02-01)
        resolves to the first version -> escrow 100.00; P2's (2026-03-01, the
        second version's own day) resolves to the second -> escrow 200.00.
        P2 and not a later payment since plan step recurrence:R16-c-2: every
        contractual installment is charged now, so a payment skipping March
        would clear March's escrow too (the fixture is restated so nothing is
        left unpaid, ruling R-R103).
        Then a THIRD version ($3,600/yr) effective 2026-06-01 supersedes the
        second FROM that date only, so it must leave BOTH earlier splits
        unchanged -- proving the split is immutable for a past date, the whole
        point of effective-dating escrow (the pre-fix code recomputed every
        payment at the current escrow, so the third change would have
        retroactively moved both to $300.00).
        """
        with app.app_context():
            loan = _make_loan(seed_user)  # no escrow via the helper
            # V1: $100/mo from origination.
            line_id = add_escrow_line(
                db.session, loan.id, "Escrow", Decimal("1200.00"),
                effective_date=_ORIGINATION_DATE,
            ).line_id
            # V2: $200/mo from 2026-03-01 (supersedes V1 from that date).
            db.session.add(EscrowComponentVersion(
                line_id=line_id, effective_date=date(2026, 3, 1),
                annual_amount=Decimal("2400.00"),
            ))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            # Chronological: P1 due 2026-02-01 (V1 $100); P2 due 2026-03-01
            # (V2 $200).  Distinct escrow proves the as-of keying.
            assert [s.escrow for s in splits] == [
                Decimal("100.00"), Decimal("200.00"),
            ]

            # A future escrow change: a THIRD version at 2026-06-01 supersedes
            # V2 from that date only.  Neither past payment is on/after it, so
            # both splits must hold (supersession never re-splits a past date).
            db.session.add(EscrowComponentVersion(
                line_id=line_id, effective_date=date(2026, 6, 1),
                annual_amount=Decimal("3600.00"),
            ))
            db.session.commit()

            resplits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [s.escrow for s in resplits] == [
                Decimal("100.00"), Decimal("200.00"),
            ]

    def test_payoff_overpayment_routes_excess_to_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """Overpaying a $300 balance caps principal and refunds the rest.

        Arithmetic (anchor balance 300.00 @ 6%): interest round(300*0.005)=1.50;
        principal0 = 1000 - 1.50 - 0 = 998.50 > 300 -> principal capped at
        300.00, excess = 998.50 - 300.00 = 698.50; balance closes at 0.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].interest == Decimal("1.50")
            assert splits[0].principal == Decimal("300.00")
            assert splits[0].excess == Decimal("698.50")

    def test_payment_after_payoff_is_all_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment on an already-closed loan is entirely a refund.

        Arithmetic: P1 (anchor 300 @ 6%) closes the loan (balance 0); P2's
        $500 cash accrues no interest and no escrow on the closed loan, so
        principal 0.00 and the whole 500.00 routes to refund.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("500.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert len(splits) == 2
            assert splits[1].interest == Decimal("0.00")
            assert splits[1].escrow == Decimal("0.00")
            assert splits[1].principal == Decimal("0.00")
            assert splits[1].excess == Decimal("500.00")

    def test_arm_rate_step_changes_interest(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-history rate step to 12% changes the later payment's interest.

        Arithmetic: P1 (due 2026-02-01, governed by the 6% origination rate)
        interest 500.00, principal 500.00, balance 99500.00.  A rate change
        effective 2026-03-01 to 12% governs P2 (due 2026-03-01, the change's
        own day): interest = round(99500 * 0.12 / 12) = 995.00, principal =
        1000 - 995 = 5.00.  P2 and not a later payment since plan step
        recurrence:R16-c-2: every contractual installment is charged now, so
        a payment skipping March would clear March's interest too (the
        fixture is restated so nothing is left unpaid, ruling R-R103).
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _add_rate_change(loan, date(2026, 3, 1), Decimal("0.12000"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("500.00"), Decimal("500.00")),
                (Decimal("995.00"), Decimal("5.00")),
            ]

    def test_actual_amount_drives_the_split(
        self, app, db, seed_user, seed_periods,
    ):
        """The split uses effective (actual) cash -- the adversarial non-vacuity proof.

        Settling with a $1,300 ACTUAL over a $1,000 estimate must move principal
        to 1300 - 500 = 800.00 (not the estimate's 500.00); a split that read
        ``transfers.amount`` instead of the shadow's effective amount would
        fail here.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                actual=Decimal("1300.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits[0].principal == Decimal("800.00")

    def test_pre_trueup_payment_is_split_from_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due before the trueup is split from the ORIGINATION reset.

        Genesis walks EVERY confirmed payment from origination and applies each
        anchor as a running-balance reset, so a payment due BEFORE the trueup is
        NOT excluded (the Step-4 behavior) -- it is split on the origination
        balance the walk carries until the trueup resets it.  With the trueup
        moved to 2026-03-15, period 1's payment (pay-period start 2026-01-16, due
        2026-02-01) precedes it and accrues on the $250,000 origination principal:
        interest = round(250000 * 0.06 / 12) = 1250.00;
        principal = 1000 - 1250 - 0 = -250.00 (negative amortization, surfaced).
        The 1250.00 (vs the trueup's 500.00) is what proves the origination reset.

        The trueup is dated 2026-03-15 rather than 2026-04-15 (its original value)
        because this suite freezes today at 2026-03-20 and the posting sync bounds
        its walk at today: a future-dated anchor posts NOTHING, leaving the loan
        half-opened (opening present, trueup missing) -- a shape production forbids
        outright (the trueup schema rejects a future ``anchor_date``).  Any date
        strictly after the payment's 2026-02-01 due date and on/before the frozen
        today reproduces the case, so every asserted number is unchanged.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 3, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")
            assert splits[0].principal == Decimal("-250.00")

    def test_mid_life_loan_resets_at_trueup(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment before the trueup splits from origination; one after, from the trueup.

        The genesis reset proven with a payment on each side of a trueup dated
        2026-02-15 (origination $250,000 @ 6%, trueup $100,000):
          P1 (period 1, due 2026-02-01, PRE-trueup): interest
            round(250000 * 0.005) = 1250.00, principal 1000 - 1250 = -250.00.
          Reset to 100000 at the 2026-02-15 trueup.
          P2 (period 3, due 2026-03-01, POST-trueup): interest
            round(100000 * 0.005) = 500.00, principal 1000 - 500 = 500.00.
        A from-origination walk with NO reset would accrue P2 on ~250000, not
        100000 -- so the distinct 500.00 is the reset's signature.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 2, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            _settle_payment(seed_user, loan, seed_periods[_P1], Decimal("1000.00"))
            _settle_payment(seed_user, loan, seed_periods[_P2], Decimal("1000.00"))
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("1250.00"), Decimal("-250.00")),
                (Decimal("500.00"), Decimal("500.00")),
            ]

    def test_projected_payment_is_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """An unsettled (Projected) payment is a future commitment, not history.

        Un-settled through the transfer's own revert door.  *Re-expressed under
        rule 5 (developer approval, 2026-09-24): it bulk-updated the two SHADOW
        rows back to Projected under a still-Paid transfer, which the loan no
        longer reads since plan step balance:X-bi-6-4b -- the transfer decides.*
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            # Settle one, then un-settle it back to Projected directly.
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits == []

    def test_no_loan_params_returns_empty(self, app, db, seed_user):
        """An account with no LoanParams is not yet resolvable -- no splits."""
        with app.app_context():
            checking = seed_user["account"]  # a plain Checking, no LoanParams
            splits = loan_ledger.compute_loan_payment_splits(
                checking.id, seed_user["scenario"].id,
            )
            assert splits == []


# ---------------------------------------------------------------------------
# the write walk reads no clock -- A3
# ---------------------------------------------------------------------------


class TestWalkReadsNoClock:
    """The persisted ledger is a function of the loan's DATA, not the wall clock.

    The walk used to drop any anchor dated after the sync's as-of, which made what
    the ledger PERSISTED depend on the moment the sync happened to run.  That is
    not a cache; it is a corruption generator, and it had two live consequences a
    loan configured before its closing date could reach through the ordinary UI.

    Both tests here use a $200,000 / 5% / 360mo mortgage originating 2026-04-15 --
    AFTER the suite's frozen today (2026-03-20), which ``origination_date``
    permits (unlike a true-up's ``anchor_date``, it carries no not-future
    validator, and the developer ruled the app must support it).

    Arithmetic (200,000 @ 5.000%, payment_day=1):
      monthly P&I = 1,073.64
      first installment 2026-05-01:
        interest  = round(200000 * 0.05/12) = 833.33
        principal = 1073.64 - 833.33        =   240.31
    """

    ORIGINATION = date(2026, 4, 15)
    PRINCIPAL = Decimal("200000.00")

    def _upcoming_mortgage(self, seed_user):
        """Create the mortgage that has not closed yet."""
        return create_loan_account(
            seed_user, _db.session, name="Closing In April",
            principal=self.PRINCIPAL, rate=Decimal("0.05000"),
            term=360, origination_date=self.ORIGINATION, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )

    def test_early_settled_payment_splits_against_the_real_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment settled before origination still splits on the opening balance.

        The payment is settled TODAY (2026-03-20) into the pay period starting
        2026-04-24, so its installment is DUE 2026-05-01 -- after the 2026-04-15
        origination.  Settling early is a supported state, not an abuse: the walk
        deliberately splits every settled payment whatever its pay period
        ("posting early changes when the fact is RECORDED, never when it is
        SHOWN").

        Before A3 the sync's as-of was 2026-03-20, so the origination anchor was
        dropped, the running balance seeded at $0.00, and ``split_one_payment``'s
        ``balance <= 0`` arm routed the ENTIRE $1,073.64 to ``excess`` -- real
        mortgage cash reclassified as a Refund Receivable asset, with the whole
        Schedule-A deductible interest erased.  Measured on the real Mortgage, the
        same line cost $7,643.80.

        NEGATIVE CONTROL: restore the ``anchor.anchor_date <= as_of`` filter --
        it would now go in ``loan_ledger.loan_event_stream``, where the reset
        events are built (it was ``merge_anchor_and_payment_events`` until plan
        step X-au-g-2c-3b-2) -- and this reports ``excess=1073.64`` with
        interest, escrow and principal all $0.00.
        """
        with app.app_context():
            loan = self._upcoming_mortgage(seed_user)
            due_period = next(
                p for p in seed_periods if p.start_date >= date(2026, 4, 24)
            )
            _settle_payment(
                seed_user, loan, due_period, Decimal("1073.64"),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert len(splits) == 1
            split = splits[0]
            # The installment this payment satisfies is due after origination, so
            # the loan exists by then and the cash divides normally.
            assert split.interest == Decimal("833.33")
            assert split.principal == Decimal("240.31")
            assert split.escrow == Decimal("0.00")
            assert split.excess == Decimal("0.00")

    def test_the_posted_ledger_is_identical_at_two_different_clocks(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Syncing before vs after origination persists the SAME ledger.

        The property A3 exists to establish, stated directly: the walk's output is
        a function of the loan's facts alone.  The loan is configured and synced at
        2026-03-20 (before it closes); the clock then moves past origination to
        2026-05-07 and the SAME sync re-runs.  Reconcile-to-target means an
        already-correct ledger is not re-posted, so an unchanged ledger is the
        proof: the first sync already recorded everything.

        Both halves are asserted, because the clock moved both: the OPENING leg
        (-$200,000.00 on the linked ledger) and the payment's split correction.

        NEGATIVE CONTROL (measured): restore the ``anchor.anchor_date <= as_of``
        filter and the first sync posts no opening AND routes the whole payment to
        Refund, so the linked ledger nets to exactly ``0.00`` -- the loan reads as
        owing NOTHING while $1,073.64 of the borrower's cash sits in a Refund
        Receivable.  The assert below then fails ``0.00 != -199759.69``.
        """
        with app.app_context():
            loan = self._upcoming_mortgage(seed_user)
            due_period = next(
                p for p in seed_periods if p.start_date >= date(2026, 4, 24)
            )
            _settle_payment(
                seed_user, loan, due_period, Decimal("1073.64"),
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            linked_id = _linked_ledger_id(loan)

            # The settle already synced, as of 2026-03-20 -- before origination.
            linked_after_first = _ledger_net(linked_id, scenario_id)
            entries_after_first = _genesis_entry_count(seed_user["user"].id)
            # -200,000.00 opening + 1,073.64 cash - 833.33 interest
            # - 0.00 escrow = -199,759.69 owed, from a sync run 26 days before
            # the loan even closes.
            assert linked_after_first == Decimal("-199759.69")

            # The clock crosses origination; the same sync re-runs.
            freeze_today(monkeypatch, date(2026, 5, 7))
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            assert _ledger_net(linked_id, scenario_id) == linked_after_first
            assert _genesis_entry_count(
                seed_user["user"].id,
            ) == entries_after_first


# ---------------------------------------------------------------------------
# tracking-start opening -- a mid-life loan opens at the recent balance
# ---------------------------------------------------------------------------


class TestTrackingStartOpening:
    """A tracking-start event seeds the walk at the recent balance, not origination."""

    def test_split_and_balance_open_from_tracking_start(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A mid-life loan opens at ORIGINATION; a tracking-start resets it (step C1).

        Origination is $250,000 @ 6% (2025-01-01) and IS the ledger opening, but
        the operator started tracking with a $100,000 balance as of 2026-01-05, an
        ordinary assertion that RESETS the walk.  The single $1,000 payment
        (2026-02-01, after the reset) therefore accrues interest on $100,000:

          * interest = round(100000 * 0.06 / 12) = 500.00 (NOT origination's
            250000 -> 1250.00, which is the pre-fix bug this pins against)
          * principal = 1000 - 500 - 0 = 500.00
          * confirmed balance at/after the tracking-start is 100000, amortizing
            to 100000 - 500 = 99500.00
          * a date BETWEEN origination and the tracking-start reads the $250,000
            origination opening held FLAT -- the honest pre-tracking plateau
            (B-11), never $0.00

        (The Schedule-A / paid-YTD interest figure moved off the postings onto the
        fold at step C6c; it is pinned in ``test_loan_paid_in_year.py``.)
        """
        # Freeze today after the payment period so the wiring's sync-as-of is
        # deterministic across CI clocks.
        as_of = date(2026, 6, 30)
        freeze_today(monkeypatch, as_of)
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, name="Mid-life Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                _loan_params(loan), Decimal("100000.00"), date(2026, 1, 5),
            )
            db.session.commit()

            # An explicit 2026 settle day so the Schedule-A year attribution does
            # not depend on the wall clock (CI runs in any year).
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                settled_on=date(2026, 2, 1),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, scenario_id,
            )
            assert len(splits) == 1
            split = splits[0]
            assert split.interest == Decimal("500.00")
            assert split.principal == Decimal("500.00")
            assert split.escrow == Decimal("0.00")

            assert posted_loan_balance_at(
                loan.id, scenario_id, as_of,
            ) == Decimal("99500.00")

            # C1: a date between origination (2025-01-01) and the tracking-start
            # (2026-01-05) reads the $250,000 origination opening held FLAT -- the
            # honest pre-tracking plateau -- never $0.00 (the pre-C1 false zero).
            assert posted_loan_balance_at(
                loan.id, scenario_id, date(2025, 6, 1),
            ) == _ORIGINATION_PRINCIPAL

    def test_drift_scorecard_labels_the_tracking_start(
        self, app, db, seed_user,
    ):
        """The drift scorecard shows origination + a labeled tracking-start (C1).

        A configured mid-life loan (no payments) shows two drift rows,
        chronological: the $250,000 origination OPENING (``is_opening``, no
        drift), then the $100,000 tracking-start ASSERTION flagged
        ``is_tracking_start`` so the display badges that row "Tracking start".
        The tracking-start's drift is the honest pre-tracking gap it booked:
        recorded 100000 - the walk's owed_before 250000 (origination held flat to
        that date) = -150000.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, name="Mid-life Loan 2",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                _loan_params(loan), Decimal("100000.00"), date(2026, 1, 5),
            )
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            rows = loan_posting_service.loan_balance_anchor_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert len(rows) == 2
            assert rows[0].is_opening is True
            assert rows[0].is_tracking_start is False
            assert rows[0].recorded == _ORIGINATION_PRINCIPAL
            assert rows[0].anchor_date == _ORIGINATION_DATE
            assert rows[1].is_opening is False
            assert rows[1].is_tracking_start is True
            assert rows[1].recorded == Decimal("100000.00")
            assert rows[1].anchor_date == date(2026, 1, 5)
            assert rows[1].drift == Decimal("-150000.00")


# ---------------------------------------------------------------------------
# sync_loan_postings -- posts the balanced split correction
# ---------------------------------------------------------------------------


class TestASkippedMonthIsTheNextPaymentsArrears:
    """The posted ledger charges every installment, and a payment clears its arrears first.

    Plan step recurrence:R16-c-2 (rulings **R-R72** part (1) and **R-R100**;
    finding **D53**'s past half): the settled walk charged one period per
    month its PAYMENTS occupied until that step, so a month nobody paid was
    never charged and its interest vanished from the posted ledger.  Every
    contractual installment from origination is charged now, and the next
    settled payment clears the months standing before it reaches principal --
    what a servicer's books say.  This is the catch-up ruling R-R101 promised
    a hand-computed test of when it restated the fixtures that had this shape
    by accident: SPLIT_LOAN's loan with no true-up, originated 2025-01-01.
    """

    def test_the_first_payment_clears_thirteen_months_and_books_them(
        self, app, db, seed_user, seed_periods,
    ):
        """$250,000 at 6% originated 2025-01-01, first payment $1,000 due 2026-02-01.

        Thirteen installments (2025-02-01 .. 2026-02-01) fall before the
        payment and nothing pays them, each charging round(250000 * 0.005) =
        1,250.00 on the unreduced balance: 16,250.00 of interest.  The $1,000
        payment clears what it can -- the whole allocation is interest, and
        principal is 1,000.00 - 16,250.00 = -15,250.00 (the balance GROWS, plan
        D5) -- so the loan owes 250,000.00 + 15,250.00 = 265,250.00.  The
        correction books it: Loan -16,250.00 (backing the interest out of the
        cash the loan received) / Interest +16,250.00.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, _db.session, name="Never Paid",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE, term=360,
                origination_date=_ORIGINATION_DATE, payment_day=1,
            )
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()

            [split] = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert split.due_date == date(2026, 2, 1)
            assert (split.interest, split.escrow, split.principal) == (
                Decimal("16250.00"), Decimal("0.00"), Decimal("-15250.00"),
            )

            entries = _correction_entries(xfer, loan)
            assert len(entries) == 1
            legs = _entry_legs(entries[0].id)
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("-16250.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("16250.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            assert posted_loan_balance_at(
                loan.id, seed_user["scenario"].id, date(2026, 3, 20),
            ) == Decimal("265250.00")


class TestSyncLoanPaymentPostings:
    """Syncing posts one balanced correction per payment.

    The setup settles through the transfer service, which fires the go-forward
    wiring and posts the loan's opening + true-up corrections alongside the
    payment splits, so ``account_posting_total(loan)`` is the FULL genesis balance
    ``-(current balance)``.  The payment split itself is pinned by the correction
    entry and the per-loan interest / escrow / refund legs, which the opening /
    true-up corrections never touch.  Every sync here is the ONE
    ``sync_loan_postings`` (plan step ``balance:X-bi-6-3``): the payment-only
    door it drove is gone with the split's row key, and the anchor half it
    runs beside was already posted by the wiring, so nothing a test pins moves.
    """

    def test_sync_posts_one_balanced_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """The correction is Loan -500 / Interest +500, summing to zero.

        Arithmetic: cash 1000, interest 500, principal 500.  The correction's
        loan leg (-500) nets against the Step-2 cash (+1000) to +500 of principal;
        the loan_interest ledger nets +500.00.  With the wiring's opening (-250000)
        and true-up (+150000) also posted, the loan-linked ledger nets to
        -(current balance 99500) = -99500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            entries = _correction_entries(xfer, loan)
            assert len(entries) == 1
            entry = entries[0]
            # A DERIVATION links no row (ruling R-BAL102): it is keyed by the
            # payment's period and visible day alone.  It carried
            # ``transaction_id == shadow.id`` through plan step
            # ``balance:X-bi-6-1b`` (a rule-5 class (a) re-expression).  The
            # day is the loan side's RECORD's since plan step
            # balance:X-bi-6-4d-2; it read the income shadow's own day until
            # then (ruling R-BAL167 class 1).  The period is the transfer's
            # since plan step balance:X-bi-6-4d-2; it read the shadow's
            # mirrored copy until then (ruling R-BAL167 class 1).
            assert entry.transfer_id is None
            assert entry.transaction_id is None
            assert entry.transaction_entry_id is None
            assert entry.pay_period_id == xfer.pay_period_id
            assert entry.entry_date == _loan_side_movement(xfer, loan).settled_on

            loan_ledger = _linked_ledger_id(loan)
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(entry.id)
            assert legs[loan_ledger] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            # Genesis: opening (-250000) + true-up (+150000) + principal (+500)
            # = -(current balance 99500); interest ledger holds the +500.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("500.00")

    def test_sync_posts_each_payment_in_a_multi_payment_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """Three payments each get a correction; the loan nets to -(final balance).

        Arithmetic (the running-balance walk from
        ``test_running_balance_across_payments``): principals 500.00 + 502.50 +
        505.01 = 1507.51, so the three Step-2 cash legs (+3000) plus the three
        correction loan legs (-1492.49) contribute +1507.51 of principal.  With
        the opening (-250000) and true-up (+150000) the loan-linked ledger nets
        -250000 + 150000 + 1507.51 = -98492.49 == -(anchor 100000 - 1507.51) =
        -(final balance 98492.49).  A second whole-loan sync writes nothing
        (idempotent across every payment).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            payments = []
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                payments.append(_settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                ))
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert all(
                len(_correction_entries(xfer, loan)) == 1 for xfer in payments
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-98492.49")

            # Idempotent across the whole loan: a re-sync adds no entries.
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert all(
                len(_correction_entries(xfer, loan)) == 1 for xfer in payments
            )

    def test_resync_is_idempotent(self, app, db, seed_user, seed_periods):
        """A second sync at the same target writes no new entry."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert len(_correction_entries(xfer, loan)) == 1

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert len(_correction_entries(xfer, loan)) == 1

    def test_no_escrow_loan_drops_the_escrow_leg(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan with no escrow components creates no loan_escrow ledger."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # no escrow component
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_ESCROW,
            ) is None

    def test_payoff_posts_a_refund_leg(self, app, db, seed_user, seed_periods):
        """A payoff overpayment books a refund-receivable leg.

        Arithmetic (anchor 300 @ 6%, cash 1000): interest 1.50, principal
        300.00, excess 698.50.  The loan_refund ledger nets +698.50; the payment
        contributes +300.00 of principal (cash 1000 - correction 700).  With the
        opening (-250000) and true-up (+249700 = 250000 - 300) the loan-linked
        ledger nets -300 + 300 = 0.00 -- the loan is paid off, -(balance 0).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            refund_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_REFUND,
            )
            assert refund_ledger is not None
            assert _ledger_net(refund_ledger.id, scenario_id) == Decimal("698.50")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("0.00")

    def test_sync_never_touches_checking(
        self, app, db, seed_user, seed_periods,
    ):
        """The loan sync moves only loan ledgers -- Checking is unchanged.

        The Step-2 cash entry already moved Checking (its $1000.00 Step-5
        opening - 1000 = 0.00); the loan correction must not move it
        further, so Checking's posted total is identical before and after
        the sync.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            checking_before = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            checking_after = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            assert checking_before == Decimal("0.00")
            assert checking_after == checking_before

    def test_correction_is_invisible_to_the_cash_reader(
        self, app, db, seed_user, seed_periods,
    ):
        """The cash reader (movement-keyed, source-filtered) never sees the correction.

        The CRITICAL invariant (plan Section 5): the correction carries the
        ``loan_payment`` source kind and links NO row (ruling **R-BAL102**),
        so a reader filtering the loan-side movement's link under the
        ``transfer_movement`` kind on the loan ledger sums only the cash
        (+1000), NOT the correction (-500) -- which is what keeps the cash
        path's reversals correct.  It read ``transfer_id == xfer.id`` while
        the cash was one entry per transfer (re-expressed at plan step
        ``balance:X-bi-6-3``); the correction is structurally invisible to a
        link-keyed reader now, and the SOURCE KIND term stays as the reader's
        own statement of which source is cash.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_ledger = _linked_ledger_id(loan)
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            # The cash reader sees only the cash leg -- neither the payment
            # correction nor the opening / true-up (other source kinds).
            assert _cash_reader_loan_net(
                xfer, loan, loan_ledger,
            ) == Decimal("1000.00")
            # But the full ledger (opening -250000 + true-up +150000 + cash 1000
            # + correction -500) nets to -(current balance 99500) = -99500.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")

    def test_new_trueup_resplits_later_payments_and_keeps_earlier(
        self, app, db, seed_user, seed_periods,
    ):
        """A new trueup re-splits payments after it and KEEPS those before it.

        Genesis retires the Step-4 "pushed behind anchor" reversal: a payment
        before a new trueup is NOT reversed -- it keeps its split from the prior
        anchor, and only LATER payments re-split from the new value.

        P1 (period 1, due 02-01) and P2 (period 3, due 03-01) settle and sync
        against the 100000 @ 01-10 trueup: interest 500.00 and
        round(99500 * 0.005) = 497.50 = 997.50 total.  A NEW trueup of 90000 @
        02-15 falls BETWEEN them, so after re-sync P1 keeps its 500.00 (it is due
        before the new trueup) and P2 re-splits from 90000 to
        round(90000 * 0.005) = 450.00 -- the loan_interest ledger holds
        500 + 450 = 950.00, and P1's correction is NOT reversed (one entry, where
        Step 4 left an original-plus-reversal pair).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            p1_xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert _ledger_net(
                interest_ledger.id, scenario_id,
            ) == Decimal("997.50")

            # New trueup at 90000 on 02-15 falls between P1 (due 02-01) and P2
            # (due 03-01): it re-bases P2 but leaves P1 untouched.
            insert_trueup_event(
                _loan_params(loan), Decimal("90000.00"), date(2026, 2, 15),
            )
            db.session.commit()
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            # P1's 500 kept, P2 re-split 497.50 -> 450: interest ledger 950.
            assert _ledger_net(
                interest_ledger.id, scenario_id,
            ) == Decimal("950.00")
            # P1's correction is NOT reversed -- genesis keeps pre-trueup splits.
            assert len(_correction_entries(p1_xfer, loan)) == 1

    def test_correction_is_disjoint_from_the_transaction_path(
        self, app, db, seed_user, seed_periods,
    ):
        """An ordinary row posted on the loan at a payment's own key leaves its split whole.

        Through plan step ``balance:X-bi-6-1b`` this pinned a GUARD: the
        transaction path's reader was link-keyed and the correction carried
        the shadow's ``transaction_id``, so the only thing keeping the split
        out of that reader's reversal was ``if txn.transfer_id is not None:
        return None``.  Leaf 1 of ``X-bi-6-3`` deleted the guard and gave
        every typed reader a source-kind term (ruling **R-BAL101**); leaf 2
        gave the split no row link at all (ruling **R-BAL102**).  So the
        property is structural now, and this pins it as such.

        It drove the poster on the payment's loan-side income shadow until
        plan step ``balance:X-bi-6-4d-2`` made a transfer one row: no
        transaction row can name the payment now (ruling **R-BAL258**).
        Re-expressed under ruling **R-BAL167** class 1 (the cp2a review's
        L-2, its RV-B3), an ordinary row standing where the shadow stood: an
        ORDINARY row on the LOAN account, in the payment's own period and on
        its loan side's own settle day, settled through the real go-forward
        primitives (which post it) and then driven through
        ``sync_transaction_postings`` again, then reverted through the status
        door and deleted through the delete door.  What is read is the
        split's EFFECT after every step of the row's life: its settle (its
        first post, the cp2b-0 review's M-1) and the repeat, its revert and
        its delete (the cp2b-1 review's L-1).  After each, the split is
        still one entry and the loan_interest ledger still nets its
        ``500.00``.  A reach into the split, by its link or by its key and
        through whichever source's reader reached it, would reverse it at
        the step that ran that reader, so every step is read; the row's own
        ``25.00`` on the loan's linked ledger is read beside it, so every
        step is shown to have posted or reversed the row.  The repeat
        writes nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            # The split: one entry, 100000 * 0.06 / 12 = 500.00 of interest.
            whole = (1, Decimal("500.00"))
            assert _split_and_interest(xfer, loan, scenario_id) == whole
            linked = _linked_ledger_id(loan)
            without_the_row = _ledger_net(linked, scenario_id)

            # Settling the row posts it: its FIRST post, the loan leg -25.00.
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[_P1], Decimal("25.00"),
                account=loan,
                settled_on=_loan_side_movement(xfer, loan).settled_on,
            )
            db.session.commit()
            # A repeat writes nothing: no entry returned.
            assert not posting_service.sync_transaction_postings(row)
            db.session.commit()
            assert _split_and_interest(xfer, loan, scenario_id) == whole
            assert _ledger_net(linked, scenario_id) == (
                without_the_row - Decimal("25.00")
            )

            # Reverted through the status door: the row's posting reversed.
            transaction_service.apply_requested_status(
                row, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            assert _split_and_interest(xfer, loan, scenario_id) == whole
            assert _ledger_net(linked, scenario_id) == without_the_row

            # Deleted through the delete door: the row's teardown.
            transaction_service.delete_transaction(
                row, seed_user["user"].id,
                purchases_named=(
                    transaction_service.preview_deletion(row).purchase_ids
                ),
            )
            db.session.commit()
            assert _split_and_interest(xfer, loan, scenario_id) == whole
            assert _ledger_net(linked, scenario_id) == without_the_row

    def test_early_settled_payment_splits_at_settle(
        self, app, db, monkeypatch, seed_user, seed_periods,
    ):
        """A payment settled before its period begins posts its split AND shows at settle.

        Two properties meet here.  The WRITE property (the 2026-07-02 review's R1,
        fixing H2): the Step-2 cash entry posts the moment a payment settles, so the
        split correction must post in the SAME moment or the loan-linked ledger
        holds raw cash with no interest backout.  The C2 DISPLAY property: a payment
        is visible from its SETTLED date, so an EARLY settle shows immediately -- its
        REAL split, never the raw cash the pre-split ledger would have held.

        Frozen today 2026-02-10.  P1 is settled 2026-01-20 (inside its period); P2
        (budgeted to period 3, due 03-01) is settled EARLY on 2026-02-05 -- its
        PERIOD has not begun, but its settle has.  The split keys on the DUE date,
        so P1 (due 02-01) splits first -- interest 100000 * 0.005 = 500.00 -> 99500
        -- then P2 -- interest round(99500 * 0.005) = 497.50, principal 502.50 ->
        98997.50.  The early payment is P2's and not a later installment's since
        plan step recurrence:R16-c-2: every contractual installment is charged
        now, so an early payment for April would clear March's standing interest
        too (the fixture is restated so nothing is left unpaid, ruling R-R103).  So:

        * the P2 correction exists AT SETTLE (no manual sync), legs
          Loan -497.50 / Interest +497.50, attributed to P2's period;
        * BOTH settled dates (01-20, 02-05) are on or before the frozen today, so
          the scalar reads the REAL 98997.50 -- never the raw cash
          99500 - 1000 = 98500.00 the unsplit ledger would show (H2).
        """
        with app.app_context():
            frozen = date(2026, 2, 10)
            freeze_today(monkeypatch, frozen)
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            early_xfer = _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
                settled_on=date(2026, 2, 5),
            )
            db.session.commit()
            # The premise: P2's PERIOD has not begun by the frozen today, but its
            # settle (2026-02-05) has -- an EARLY settle.
            assert seed_periods[_P2].start_date > frozen

            # The correction posted at settle, through the transfer wiring --
            # no manual sync call -- attributed to the payment's own period.
            entries = _correction_entries(early_xfer, loan)
            assert len(entries) == 1
            assert entries[0].pay_period_id == seed_periods[_P2].id
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(entries[0].id)
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("-497.50"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("497.50"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )

            # C2: both payments are visible from their settled dates (<= frozen),
            # so the scalar shows the REAL split -- never the raw cash 98500.00.
            assert posted_loan_balance_at(
                loan.id, scenario_id, frozen,
            ) == Decimal("98997.50")

            # The map at P2's period agrees (period-END keyed).
            balance_map = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            assert balance_map[seed_periods[_P2].id] == Decimal("98997.50")


# ---------------------------------------------------------------------------
# reverse + stale-correction reversal
# ---------------------------------------------------------------------------


class TestTheSplitIsADateKeyedCorrection:
    """The split links no row and is keyed (kind, period, day): ruling R-BAL102.

    Plan step ``balance:X-bi-6-3``.  The measured premise gap that re-ruled
    R-BAL100 and the hazards design_6_3.md s.10 names, each with its control:
    a payment of ``$0.00`` (no movement, a nonzero split), two payments at one
    key (one merged entry), a settled figure re-recorded to ``$0.00`` (the
    key's target unchanged), and residue at a key the walk no longer prices
    (self-heals).  Every figure is hand-computed on the SPLIT_LOAN fixture:
    ``$100,000`` @ 6% charges ``round(100000 * 0.005) = 500.00`` for P1.
    """

    def test_a_zero_dollar_payment_posts_its_charge_with_no_movement(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan payment recorded at $0.00 has no movement and a $500.00 split.

        The measured case (2026-09-21, before the ruling): the seam writes NO
        covering movement for a figure of nothing (``amount <> 0``), yet the
        walk still charges the installment -- ``apply_payment_cash(0, 100000,
        500, 0)`` gives principal ``-500.00`` -- so the split is ``{Loan
        -500.00 principal, Interest +500.00}`` and the debt grows by the
        interest not paid: the app's only way to accrue a missed installment.
        Keyed by the loan-side movement (R-BAL100) that split had nothing to
        hang on; keyed by ``(loan_payment, P1, its installment 2026-02-01)``
        it posts linking no row -- the INSTALLMENT's day since ruling
        **R-BAL139**, where a payment that moved nothing is dated (it was the
        settle day until plan step balance:X-bi-6-4b; rule-5 re-expression,
        developer approval 2026-09-24) -- and the checked-projection assert holds:
        the loan-linked ledger nets opening (-250000) + true-up (+150000) +
        principal (-500) = -100500.00 and the interest ledger 500.00.

        **Paid is pressed on a day the test states, and the loan side is read
        off the transfer, since plan step ``balance:X-bi-6-4d-2``** (ruling
        **R-BAL167** class 1): it asked the income shadow for its covering
        movements and its settle day, and both answers had been empty since
        plan step ``balance:X-bi-6-4d-2`` moved the record onto the transfer
        and stopped keeping a shadow's day, so the settle-day key it read was
        ``None``.  A ``$0.00`` close keeps no record (ruling **R-BAL141**), so
        the day it was pressed on is held nowhere but here.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            pressed_on = date(2026, 1, 20)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                actual=Decimal("0.00"), settled_on=pressed_on,
            )
            db.session.commit()

            assert transfer_side_record(db.session, xfer.id, loan.id) is None
            assert loan_correction_entries_at(
                db.session, loan.id, scenario_id, seed_periods[_P1].id,
                pressed_on,
            ) == []
            entries = loan_correction_entries_at(
                db.session, loan.id, scenario_id, seed_periods[_P1].id,
                date(2026, 2, 1),
            )
            assert len(entries) == 1
            entry = entries[0]
            assert (entry.transaction_id, entry.transfer_id,
                    entry.transaction_entry_id) == (None, None, None)
            legs = _entry_legs(entry.id)
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100500.00")
            # A repeat sync -- the assert inside it holding -- writes nothing.
            before = _source_entry_count(xfer.id, loan.id)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.commit()
            assert _source_entry_count(xfer.id, loan.id) == before

    def test_two_payments_at_one_key_merge_into_one_target(
        self, app, db, seed_user, seed_periods,
    ):
        """Two payments in one period on one day share ONE key and its target.

        Both settle in P1 on the same day against P1's installment and the
        next: the first (``$1,000``) clears the period's ``500.00`` charge and
        the second (``$800``, due 03-01, balance 99500) clears ``round(99500
        * 0.005) = 497.50`` and pays 302.50 down.  Merged at the key
        ``(loan_payment, P1, the day)`` the target is Loan -997.50 / Interest
        +997.50, and the ledger reaches it append-only: the first settle's
        entry (-500 / +500) plus the DELTA the second settle emits at the same
        key (-497.50 / +497.50), two entries summing to the target.  The
        payment-history table still shows two rows, 500.00 and 497.50,
        because it reads the walk and not the ledger; the ledger cannot say
        which payment paid what, and does not need to.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            day = seed_periods[_P1].start_date
            first_xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=day,
            )
            db.session.commit()
            second_xfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("800.00"), settled_on=day,
                due_date=date(2026, 3, 1),
            )
            db.session.commit()
            # One key: each payment's period off its transfer and its day off
            # its loan side's record.  Both were read off the income shadows
            # until plan step balance:X-bi-6-4d-2, whose day stopped being
            # kept at plan step balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
            assert (
                first_xfer.pay_period_id,
                _loan_side_movement(first_xfer, loan).settled_on,
            ) == (
                second_xfer.pay_period_id,
                _loan_side_movement(second_xfer, loan).settled_on,
            )

            entries = _correction_entries(first_xfer, loan)
            assert len(entries) == 2
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            by_entry = [_entry_legs(entry.id) for entry in entries]
            assert [legs[interest_ledger.id][0] for legs in by_entry] == [
                Decimal("500.00"), Decimal("497.50"),
            ]
            assert sum(
                legs[_linked_ledger_id(loan)][0] for legs in by_entry
            ) == Decimal("-997.50")
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("997.50")

            rows = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, date.today(),
            )
            assert [(row.installment, row.interest, row.principal) for row in rows] == [
                (date(2026, 2, 1), Decimal("500.00"), Decimal("500.00")),
                (date(2026, 3, 1), Decimal("497.50"), Decimal("302.50")),
            ]

    def test_a_figure_re_recorded_to_zero_moves_the_split_to_its_installment(
        self, app, db, seed_user, seed_periods,
    ):
        """Correcting a settled $1,000 payment to $0.00 moves its split, not its size.

        The seam withdraws the covering movement (a ``$0.00`` record carries
        nothing) and its cash leg reverses through the movement door.  The
        split's FIGURES are unchanged because the interest charged does not
        depend on the cash -- ``apply_payment_cash`` echoes the standing
        charge -- but its DAY moves: a payment that moved nothing is dated by
        the installment it skips (ruling **R-BAL139**), so the settle day's
        correction reverses and the same split books at 2026-02-01.  *Until
        plan step balance:X-bi-6-4b the day was the shadow's settle day and the
        key did not move (rule-5 re-expression, developer approval
        2026-09-24).*
        Under a movement key this correction would have SET NULL the split's
        link on the withdrawal and stranded it.  Arithmetic: -99500.00 (cash
        +1000, split -500 on the anchors' -100000) becomes -100500.00 (no
        cash, the same split); the interest ledger holds 500.00 throughout.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")
            assert len(_correction_entries(xfer, loan)) == 1
            # The settle day's key, captured before the act: the record that
            # carries the day is withdrawn by it.  The day and the record are
            # the loan SIDE's, off the transfer, since plan step
            # balance:X-bi-6-4d-2; it read the income shadow's covering
            # movement and kept day until then (ruling R-BAL167 class 1).
            settle_day = _loan_side_movement(xfer, loan).settled_on

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, figure=typed(Decimal("0.00")),
            )
            db.session.commit()

            assert transfer_side_record(db.session, xfer.id, loan.id) is None
            assert len(loan_correction_entries_at(
                db.session, loan.id, scenario_id, seed_periods[_P1].id,
                settle_day,
            )) == 2, (
                "the settle day's correction and its reversal"
            )
            assert len(loan_correction_entries_at(
                db.session, loan.id, scenario_id, seed_periods[_P1].id,
                date(2026, 2, 1),
            )) == 1
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100500.00")
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("500.00")

    def test_residue_at_a_key_the_walk_does_not_price_self_heals(
        self, app, db, seed_user, seed_periods,
    ):
        """A split posted at a key with no target is reversed by the next sync.

        Under the shadow key a ``loan_payment`` entry whose row was hard-deleted
        without the reversal-before was RESIDUE: its link SET NULL, no row to
        key a reversal by, the statements dropping it and the assert refusing
        the loan forever.  Under the date key every posted key is reconciled
        on every sync, so the same entry -- planted here by hand at P2 on a
        day the walk prices nothing -- is a posted key with no target and
        reverses in one pass: the key nets to zero, the interest ledger back
        to P1's 500.00, and the assert holds.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            stray_period = seed_periods[_P2]
            stray_day = stray_period.start_date
            stray = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=scenario_id,
                pay_period_id=stray_period.id,
                entry_date=stray_day,
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.LOAN_PAYMENT,
                ),
                description="Loan payment split: stray",
            )
            _emit_balanced_entry(stray, [
                _PostingLeg(
                    _linked_ledger_id(loan), Decimal("-7.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
                ),
                _PostingLeg(
                    interest_ledger.id, Decimal("7.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
                ),
            ])
            db.session.commit()
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("507.00")

            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.commit()

            at_key = loan_correction_entries_at(
                _db.session, loan.id, scenario_id, stray_period.id, stray_day,
            )
            assert len(at_key) == 2
            assert _entry_legs(at_key[-1].id)[interest_ledger.id][0] == (
                Decimal("-7.00")
            )
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("500.00")
            assert len(_correction_entries(xfer, loan)) == 1


class TestReverseLoanPaymentPostings:
    """A correction reverses at its own key, and stale ones self-heal.

    The split is a date-keyed correction with no row link since plan step
    ``balance:X-bi-6-3`` (ruling **R-BAL102**), so nothing reverses it
    "before a delete" any more: a payment that leaves the walk -- reverted,
    deleted, moved -- leaves its key with no target, and the one reconcile
    reverses the key.  The two tests that drove the deleted
    ``reverse_loan_payment_postings_for_shadow`` directly are re-expressed
    on the doors that own the act (a rule-5 class (b) re-expression Josh
    confirmed 2026-09-21).  Two of this class's figures moved, -99000 ->
    -100000 in ``test_hard_delete_reverses_the_correction_at_its_own_key``
    and ``test_resync_reverses_an_unsettled_payment``: the direct door left
    the cash leg posted beside a reversed split, a state no production door
    produces, and the doors these tests drive now reverse both halves.
    """

    def test_revert_and_move_reverses_into_the_original_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A reverted-and-moved payment's correction reverses into its old period.

        The loan twin of the R2 attribution rule (the 2026-07-02 review's H1
        class): one ``update_transfer`` call reverts the payment to Projected
        AND moves it to a later period -- the shadows carry the NEW period by
        the time the loan wiring reconciles.  The stale correction must
        reverse into the period it was POSTED in (P1's), never the shadow's
        new one, so the correction pair nets P1's period to zero and the new
        period holds no loan_payment entries at all.  Arithmetic: the P1
        split was interest 500.00 / principal 500.00, so the reversal legs
        are Loan +500.00 / Interest -500.00 in P1's period, and the interest
        ledger nets back to zero.  Under the date key the rule is structural
        -- the posted key IS ``(P1, the original day)`` and a revert releases
        the loan side's day (its record's, off the transfer since plan step
        balance:X-bi-6-4d-2; the shadow's until then, ruling R-BAL167 class
        1), so the entries are read at the key the split was posted under,
        captured before the revert.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            original_period_id = seed_periods[_P1].id
            # The day is the loan side's record's (ruling R-BAL167 class 1,
            # plan step balance:X-bi-6-4d-2; the shadow's own until then).
            original_day = _loan_side_movement(xfer, loan).settled_on
            moved_to = seed_periods[_P3]

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                pay_period_id=moved_to.id,
            )
            db.session.commit()

            entries = loan_correction_entries_at(
                _db.session, loan.id, scenario_id, original_period_id,
                original_day,
            )
            assert len(entries) == 2
            reversal = entries[-1]
            # The R2 rule: the reversal carries the ORIGINAL period -- the
            # pre-fix code stamped it with the shadow's NEW period.
            assert reversal.pay_period_id == original_period_id
            assert all(
                entry.pay_period_id == original_period_id for entry in entries
            )
            # And the new period holds no split at all: the walk no longer
            # prices the payment, so no key of its own was posted there.
            assert not loan_correction_entries_at(
                _db.session, loan.id, scenario_id, moved_to.id, original_day,
            )
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(reversal.id)
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            assert _ledger_net(interest_ledger.id, scenario_id) == (
                Decimal("0.00")
            )

    def test_hard_delete_reverses_the_correction_at_its_own_key(
        self, app, db, seed_user, seed_periods,
    ):
        """A hard-deleted payment's correction reverses at its key, leg for leg.

        The delete door reverses the cash leg BEFORE the row goes (the
        movement's link is what a hard delete severs) and re-syncs the loan
        AFTER: the walk no longer prices the payment, its key ``(P1, the
        settle day)`` has no target, and the reconcile emits the reversal
        there -- Loan +500.00 / Interest -500.00 against the original's
        Loan -500.00 / Interest +500.00, so the key nets to zero.  Nothing
        about the split needed the shadow to exist: it linked no row.  The
        loan-linked ledger returns to the opening (-250000) + true-up
        (+150000) = -100000.00 and the interest ledger to 0.00.  Re-expressed
        from a direct call to the deleted ``reverse_loan_payment_postings_
        for_shadow``, which produced a state no door produces (the split
        reversed with the cash still posted) and pinned -99000.00 for it;
        the figure is -100000.00 here because the delete door reverses the
        cash leg too -- the second of the two changed figures this module's
        docstring names.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            # The day is the loan side's record's (ruling R-BAL167 class 1,
            # plan step balance:X-bi-6-4d-2; the shadow's own until then).
            period_id = xfer.pay_period_id
            day = _loan_side_movement(xfer, loan).settled_on
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert len(
                loan_correction_entries_at(
                    _db.session, loan.id, scenario_id, period_id, day,
                )
            ) == 1

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            entries = loan_correction_entries_at(
                _db.session, loan.id, scenario_id, period_id, day,
            )
            assert len(entries) == 2
            legs = _entry_legs(entries[-1].id)
            assert legs[_linked_ledger_id(loan)] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("0")

    def test_deleting_an_unposted_payment_writes_no_split(
        self, app, db, seed_user, seed_periods,
    ):
        """Deleting a payment that carries no posted correction writes nothing.

        Commit 5 wires the split-posting into the transfer chokepoints, so a
        SETTLED loan payment auto-posts its correction; a genuinely unposted
        payment is therefore a PROJECTED one (never settled -> never in the
        walk).  Deleting it must write no ``loan_payment`` entry at all: the
        loan's re-sync after the delete finds no posted key and no target.
        Re-expressed from a direct call to the deleted reversal door.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=loan.id,
                    pay_period_id=seed_periods[_P1].id,
                    scenario_id=scenario_id,
                    amount_ownership=AmountOwnership.own(Decimal("1000.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                ),
            )
            db.session.commit()

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()
            loan_payment_source = ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT,
            )
            assert (
                _db.session.query(JournalEntry)
                .filter(
                    JournalEntry.user_id == seed_user["user"].id,
                    JournalEntry.source_kind_id == loan_payment_source,
                )
                .count()
            ) == 0

    def test_resync_reverses_an_unsettled_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment that leaves the eligible set is reversed by the next sync.

        Settle + sync (one correction; loan-linked -99500 = opening -250000 +
        true-up +150000 + principal 500), then revert the pair through the
        status SEAM alone -- the transfer's status and its sides' records, with
        NO posting sync behind it, standing in for a door that forgot the
        ledger -- and run the one loan sync bare: the now-stale correction's
        key has no target and reverses, the lineage probe finds the cash leg
        posted for a movement the revert un-dated and reverses it through
        the pair's door, and the loan-linked ledger drops to the opening +
        true-up = -100000 with the checked-projection assert holding.

        **The figure was -99000 through plan step ``balance:X-bi-6-1b``, and
        the fixture was an impossible state**: the payment-only door this
        test drove (deleted at ``X-bi-6-3``, ruling R-BAL102) ran no assert,
        so the test could un-settle the shadows' STATUS by raw SQL, leave the
        covering movement DATED (a state no revert leaves, ruling R-BAL61),
        and pin the cash leg still posted beside a reversed split.  The one
        sync refuses that state, correctly (its assert fires: walk 0.00 vs
        posted 1000.00 at the settle day), so the fixture is the honest
        revert and the figure is what the ledger holds after it.  A rule-5
        class (b) re-expression Josh confirmed 2026-09-21.

        **The seam's TRANSFER arm since plan step ``balance:X-bi-6-4d-2``**
        (ruling R-BAL167 class 2): the sides' records hang off the transfer,
        so the revert un-dates them through ``status_seam.sync_side_records``
        -- the call the transfer's status writer makes beside
        ``apply_status_change`` -- where it applied the seam to each shadow
        row, whose covering movement held the record, until then.  Reverting
        the twins and the transfer's status alone now leaves both records
        dated under a Projected transfer, which the database refuses at
        commit, so the stale ledger is reached through the arm instead.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")
            period_id = xfer.pay_period_id
            day = _loan_side_movement(xfer, loan).settled_on

            # Revert through the seam alone (no posting sync): the status and
            # both sides' records move, each record is un-dated, the ledger is
            # left stale on BOTH halves.
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            status_seam.sync_side_records(
                (
                    transfer_side_leg(xfer, is_income=False),
                    transfer_side_leg(xfer, is_income=True),
                ),
                projected, days=(None, None), settlement=None, press=None,
            )
            status_seam.apply_status_change(xfer, projected)
            db.session.commit()
            assert _loan_side_movement(xfer, loan).settled_on is None
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            # The correction is reversed at its own key and the cash leg
            # through the pair's door; the loan holds the opening + true-up.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            entries = loan_correction_entries_at(
                _db.session, loan.id, scenario_id, period_id, day,
            )
            assert len(entries) == 2
            assert posting_service.account_posting_total(
                seed_user["account"].id, scenario_id,
            ) == Decimal("1000.00")


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


class TestSyncLoanAnchorCorrections:
    """The opening + true-up corrections drive the linked ledger to -(owed)."""

    def test_opening_posts_original_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An origination-only loan posts the opening: linked -P0, equity +P0.

        A loan with only its origination anchor ($250,000) and no payments posts
        ONE opening entry: owed_before is 0 at origination, so the loan-linked
        ledger gets 0 - 250000 = -250000.00 and the per-loan opening-equity ledger
        its negative, +250000.00.  The linked net is then -250000, so the reader
        (which negates it) reports the full $250,000 owed.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            equity = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _ledger_net(equity.id, scenario_id) == Decimal("250000.00")
            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) == 1
            assert _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_and_trueup_drive_linked_to_negative_verified(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + true-up land the linked net on -(verified balance).

        Origination $250,000 then a trueup to $100,000 (no payments):
          opening (owed_before 0): linked 0 - 250000 = -250000.00.
          trueup (owed_before 250000, the origination balance carried to the
            trueup date): linked 250000 - 100000 = +150000.00.
        Linked net -250000 + 150000 = -100000.00 -- exactly -(the $100,000 the
        user verified), the opening + trueup reproducing the resolver's anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # origination 250000, trueup 100000
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            linked_id = ledger_accounts_for_account(db.session, loan.id)[0].id
            equity = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _ledger_net(equity.id, scenario_id) == Decimal("100000.00")

            opening = _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )
            trueup = _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )
            assert len(opening) == 1 and len(trueup) == 1
            assert _entry_legs(opening[0].id)[linked_id][0] == Decimal("-250000.00")
            assert _entry_legs(trueup[0].id)[linked_id][0] == Decimal("150000.00")

    def test_anchor_corrections_are_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """A second sync at the same state writes no new anchor entry."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            before = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) + len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            assert before == 2

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            after = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) + len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            assert after == 2

    def test_trueup_matching_the_walk_books_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A trueup equal to the walked balance posts no true-up entry.

        Origination $250,000 and a trueup ALSO of $250,000 (no payments): at the
        trueup date owed_before is 250000, so the correction 250000 - 250000 = 0
        books nothing.  Only the opening is posted; the linked net is -250000.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("250000.00"))
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_payments_trueup_reproduce_resolver_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + payment corrections + true-up net to -(resolver balance).

        The genesis payoff: three post-trueup $1,000 payments split to principal
        500.00 + 502.50 + 505.01 = 1507.51 (the running-balance walk from the
        $100,000 trueup), so the resolver's balance is 100000 - 1507.51 =
        98492.49.  With the opening (-250000) and true-up (+150000) added, the
        loan-linked net is (1507.51 payment nets) - 100000 = -98492.49 -- exactly
        -(the resolver balance), proving the from-origination sum-of-postings
        reproduces the resolver on a trued-up loan.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-98492.49")

    def test_trueup_self_heals_when_a_pre_trueup_payment_is_added(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling a pre-trueup payment re-bases the true-up in the SAME reconcile.

        The true-up correction is owed_before - verified, and owed_before moves
        when a pre-trueup payment changes -- so the reconcile must self-heal
        (reconcile-to-target), not leave a stale snapshot.  Origination $250,000,
        trueup $100,000 @ 2026-02-15: with no payments the opening (-250000) and
        true-up (250000 - 100000 = +150000) net the linked ledger to -100000.00,
        and the per-loan opening-equity ledger holds their negatives, +100000.00.
        Settling P1 (due 02-01, PRE-trueup, cash 1000) splits it on the origination
        balance (interest round(250000 * 0.005) = 1250, principal -250) and grows
        owed_before to 250250 -- and because the settle fires the UNIFIED sync
        (:func:`sync_loan_postings`), the true-up re-bases to 250250 - 100000 =
        +150250 (a +250 linked delta) in the SAME transaction, so the linked ledger
        STAYS -100000.00 == -(verified $100,000), the pre-trueup payment fully
        absorbed; the equity ledger re-bases to 250000 - 150250 = +99750.00.  No
        stale window is ever observable through the go-forward path.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 2, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            # The opening + true-up (and their per-loan equity ledger) are posted
            # lazily on this first sync.
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            equity_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            # Opening (+250000) + true-up (-150000) on the equity ledger.
            assert _ledger_net(
                equity_ledger.id, scenario_id,
            ) == Decimal("100000.00")

            # Settle a pre-trueup payment; the unified wiring re-bases the true-up
            # in the same transaction (no manual anchor re-sync needed).
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            # Self-healed automatically: linked still -(verified 100000); the
            # equity ledger re-based to opening (+250000) + true-up (-150250).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _ledger_net(
                equity_ledger.id, scenario_id,
            ) == Decimal("99750.00")

            # A further unified sync is a no-op: no new true-up entry, balance holds.
            trueups_after_heal = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )) == trueups_after_heal
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")

    def test_payment_due_on_anchor_date_is_subsumed_by_the_reset(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due EXACTLY on the anchor date is walked before the reset, then subsumed.

        The strict ``anchor_date < monthly_due_date`` boundary means a payment due
        ON an anchor's date is PRE-anchor (subsumed).  The walk realises this by
        sorting a payment (tag 0) before an anchor (tag 1) on an equal date, so P1
        (period 1, due 2026-02-01 at payment_day=1) is split on the $250,000
        origination balance BEFORE the 2026-02-01 trueup resets to $100,000:
        interest round(250000 * 0.005) = 1250.00 lands in the interest ledger, but
        its principal is wiped by the reset -- the reader ends at the verified
        $100,000, exactly as the resolver (which does not replay P1).  A SWAPPED
        tie-break (reset first) would instead give interest 500.00 and a balance of
        99500.00, so this pins the ordering the whole trued-up-balance design rests
        on (no other fixture places a due date on an anchor date).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Trueup date EQUALS P1's 2026-02-01 due date (payment_day=1).
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 2, 1),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            # P1 is split BEFORE the reset -> interest on the origination balance.
            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, scenario_id,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")

            # With opening + true-up the reader lands on the verified 100000 --
            # P1's principal subsumed by the reset (a swapped tie-break -> -99500).
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")


def _anchor_net_in_period(loan_id, scenario_id, source_enum, period_id):
    """Sum a loan's anchor-correction legs on its LINKED ledger in ONE period.

    Resolves the loan's linked ledger, then defers to the shared
    ``tests._test_helpers.correction_net_in_period`` -- the same reading the
    account anchor suite makes, so the two halves of the R2 fix are graded by
    one query rather than two that happen to agree.
    """
    linked_id = ledger_accounts_for_account(_db.session, loan_id)[0].id
    return correction_net_in_period(
        _db.session, linked_id, scenario_id, source_enum, period_id,
    )


class TestLoanAnchorPeriodAttribution:
    """A loan correction re-files when the calendar grows a period around it.

    The loan twin of the account side's R2 fix (plan step X-ai-r).  A loan
    anchor carries no stored ``pay_period_id`` -- ``budget.loan_anchor_events``
    has no such column and the origination is synthesized from ``LoanParams``
    -- so its period is DERIVED from its date through
    ``pay_calendar.PayCalendar.filing_period``.  When no period CONTAINS the
    date it clamps to the LATEST period OPENING on or before it (and to the
    earliest only when the date precedes the whole schedule).

    *That rule was ``loan_ledger.resolve_anchor_pay_period`` until pay-calendar
    plan step C2-d, whose fallback keyed on the latest period ENDING before the
    date.  The two give the same answer on a schedule that is non-overlapping
    AND whose ``period_index`` order matches its ``start_date`` order -- BOTH
    halves, because the old chain reduced by index and this one by date -- which
    is why nothing in this class had to change.  The precondition and its proofs
    are stated at ``pay_calendar.PayCalendar.filing_period``.*

    That fallback is where the calendar and the ledger come apart.  A user
    whose schedule ends before their asserted anchor date gets the correction
    filed against whichever period they happen to have last; extending the
    schedule (an ordinary ``extend_pay_periods`` or the rolling-window top-up)
    then creates the period that really contains it.  Under a period-blind
    reconcile key the target and the posted entry compared equal -- same source
    kind, same date -- so the delta was zero and the correction sat in the
    stale period forever, and any later adjustment would have been filed there
    too.  With the period in the key the stale period reverses to zero and the
    real one posts fresh.
    """

    _LATE_ANCHOR_DATE = date(2026, 6, 15)

    def test_extending_the_schedule_refiles_an_anchor_into_its_real_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A true-up dated past the schedule moves to the period that grows around it.

        ``seed_periods`` ends 2026-05-21, so a $100,000 true-up dated
        2026-06-15 has no containing period and books against the LAST one by
        fallback.  Its correction is ``owed_before - verified = 250000 -
        100000 = +150,000.00``.  Extending the schedule by three periods
        creates the one containing 2026-06-15, so the re-sync must net the
        stale period back to ``0.00`` and carry the full ``+150,000.00`` in the
        real one.  The loan's total is unchanged either way -- this is an
        attribution move, not a revaluation.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            loan = _make_loan(seed_user, anchor_date=self._LATE_ANCHOR_DATE)
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            stale_period = seed_periods[-1]
            # The precondition: no seeded period contains the anchor date, so
            # the true-up landed on the last one by fallback.
            assert last_covered_day(stale_period) < self._LATE_ANCHOR_DATE
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, stale_period.id,
            ) == Decimal("150000.00")

            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()
            containing = next(
                period for period in new_periods
                if period.start_date
                <= self._LATE_ANCHOR_DATE
                <= last_covered_day(period)
            )

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, stale_period.id,
            ) == Decimal("0.00")
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, containing.id,
            ) == Decimal("150000.00")
            # -250000 opening + 150000 true-up: the attribution moved, the
            # balance did not.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")

    def test_the_refiled_anchor_reconcile_is_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-syncing after the re-file writes nothing and moves no period.

        The convergence half: the finer key must reach its fixpoint in one
        pass rather than shuttling the correction between the two periods.
        Asserts the re-filed STATE either side of the extra sync -- an earlier
        version checked only the entry count and the total, which a re-file
        that never happened satisfies just as well, so it passed against the
        un-fixed code and graded nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            loan = _make_loan(seed_user, anchor_date=self._LATE_ANCHOR_DATE)
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            stale_period = seed_periods[-1]
            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()
            containing = next(
                period for period in new_periods
                if period.start_date
                <= self._LATE_ANCHOR_DATE
                <= last_covered_day(period)
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            settled = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            # The re-file the fixpoint is being asserted OVER -- without this
            # the test cannot tell convergence from nothing having moved.
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, containing.id,
            ) == Decimal("150000.00")
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, stale_period.id,
            ) == Decimal("0.00")

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )) == settled
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, containing.id,
            ) == Decimal("150000.00")
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, stale_period.id,
            ) == Decimal("0.00")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")


# ---------------------------------------------------------------------------
# sync_loan_postings_all_scenarios -- the unified loan-global entry point
# ---------------------------------------------------------------------------


class TestUnifiedAllScenariosSync:
    """The all-scenarios sync posts a payment-less loan's opening in the baseline."""

    def test_posts_opening_for_a_payment_less_loan_in_the_baseline(
        self, app, db, seed_user,
    ):
        """A brand-new loan with no payments gets its opening posted in the baseline.

        A payment-less loan is in NO scenario's payment set
        (``scenarios_with_loan_payments`` is empty), so the all-scenarios sync
        must add the owner's baseline -- otherwise ``create_params`` would post no
        opening for a fresh loan.  Origination $250,000, trueup $100,000: the
        opening (-250000) + true-up (+150000) net the baseline linked ledger to
        -100000.00 == -(anchor 100000), and the per-loan opening-equity ledger
        holds +100000; no payment-correction ledger is minted (there is no
        payment).  This is the ``create_params`` N1 path the chokepoint relies on.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id  # the owner's baseline
            loan = _make_loan(seed_user)  # no payments settled

            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            # Opening (-250000) + true-up (+150000), no payment.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            equity_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert equity_ledger is not None
            assert _ledger_net(
                equity_ledger.id, scenario_id,
            ) == Decimal("100000.00")
            # No payment-correction ledger is minted without a payment.
            assert _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            ) is None


# ---------------------------------------------------------------------------
# What the posted legs SUM to, read through the dated posting window
# ---------------------------------------------------------------------------

# The frozen "today" for the class below: after the Jan-May 2026 seed periods and
# the 2026-12-31 _AS_OF, so the genesis walk is eligible for every fixture and no
# assertion depends on the wall clock.
_FROZEN_TODAY = date(2027, 1, 1)


class TestPostedLoanBalanceSums:
    """The posted legs sum to the loan's balance: -(sum of its linked postings).

    Every posting the write side books onto the loan's linked ledger -- the
    opening, each payment's Step-2 cash and Step-4 split, each true-up -- summed
    and negated, bounded by ``entry_date <= as_of`` and by scenario.  The
    literals are the same hand-computed figures the split tests above prove
    (100000 - 500 - 502.50 - 505.01 = 98492.49 over three $1,000 payments), read
    back off the ledger.

    **These pin the LEDGER, not a reader.**  Plan step E1e deleted the two
    production sum-of-postings readers -- nothing in ``app/`` called them once the
    balance seam folded a loan from source events -- so the sum is read through
    the test suite's own window (:func:`tests._test_helpers.posted_loan_balance_at`).
    Every expected value here is unchanged by that move: it was always the
    ledger's number.
    """

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so every fixture is deterministic.

        2027-01-01 sits after the Jan-May 2026 seed periods and the 2026-12-31
        ``_AS_OF``, so the genesis walk is eligible for every loan built here.
        Class-scoped, so only these tests freeze -- the split / anchor tests
        above keep the real clock.

        **The "no assertion depends on the wall clock" this docstring used to
        claim was FALSE, and finding N-65's structural fix is what proved it.**
        Six tests here settled their payment with no explicit date, so the day
        came from the DATABASE clock -- untouched by ``freeze_today``, which
        patches Python only.  The real clock happens to fall before ``_AS_OF``,
        so the payment landed inside the window and the assertions held.  Under a
        frozen database clock the settle lands on 2027-01-01, ONE DAY past
        ``_AS_OF``, and the payment is correctly excluded: the balance reads
        ``100000.00`` instead of ``99500.00``.  Which means these six would have
        gone red by themselves on 2027-01-01 with no code change at all.

        Every settle in this class therefore passes ``settled_on=`` explicitly,
        stating the date the test always meant.  No expected figure moved --
        the settle day bounds a posting's VISIBILITY (``entry_date <= as_of``) and
        never its split, which the pay period decides.
        """
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_opening_only_loan_reads_the_original_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An origination-only loan reads back its opening balance.

        A $250,000 loan with only its origination anchor (no true-up, no
        payments): the opening books linked -250000, so the reader negates it to
        the full $250,000 owed -- the ledger, not ``LoanParams``, is the source.
        With no payments the balance is flat, so every period's map value is the
        same $250,000.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("250000.00")
            # No payments -> the opening balance carries flat across every period.
            result = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            assert set(result.values()) == {Decimal("250000.00")}

    def test_reads_the_balance_after_a_single_confirmed_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """One $1,000 payment drops the balance by its real principal.

        Trued up to $100,000, one post-anchor $1,000 payment splits to interest
        round(100000 * 0.005) = 500.00 and principal 500.00, so the reader reports
        100000 - 500 = 99500.00 (opening -250000 + true-up +150000 + Step-2 cash
        +1000 + split -500, negated).  The settle wiring auto-posts all of it.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("99500.00")

    def test_extra_payment_is_captured_without_a_trueup(
        self, app, db, seed_user, seed_periods,
    ):
        """An off-schedule extra payment lowers the read balance -- no true-up.

        This is the arc's headline.  A $1,500 payment on the $100,000 balance
        splits to interest round(100000 * 0.005) = 500.00 and principal
        1500 - 500 = 1000.00, so the reader reports 100000 - 1000 = 99000.00.  The
        resolver's contractual replay would book only the scheduled ~$500
        principal and need a balance true-up to reconcile the extra $500; the
        ledger captures the real principal from the actual cash, so the reader
        already reflects it with no anchor edit.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1500.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("99000.00")

    def test_running_balance_reproduces_the_resolver_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Three $1,000 payments read back the resolver's balance, 98492.49.

        The running-balance walk (interest 500.00 / 497.50 / 494.99 on the
        shrinking real balance; principal 500.00 / 502.50 / 505.01) leaves
        100000 - 1507.51 = 98492.49 -- the resolver's confirmed balance
        (``test_opening_payments_trueup_reproduce_resolver_balance`` proves the
        equality against ``account_posting_total``); the reader negates the same
        linked net to the same figure.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                    settled_on=period.start_date,
                )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("98492.49")

    def test_paid_off_loan_reads_zero_not_none(
        self, app, db, seed_user, seed_periods,
    ):
        """A fully-paid-off loan reads 0.00, distinct from an unconfigured None.

        One $100,500 payment on the $100,000 verified balance splits to interest
        round(100000 * 0.005) = 500.00 and principal 100000.00 (cash - interest),
        exactly closing the loan, so the linked net is 0 and the reader reports
        Decimal("0.00") -- a real paid-off balance, NOT the ``None`` an
        unconfigured loan returns.  The zero is a clean +0.00 (the reader negates
        via ``0 - net``), never ``-0.00``.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("100500.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            result = posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            )
            # == 0.00 (not None): None == Decimal fails, so this proves both.
            assert result == Decimal("0.00")
            # A clean positive zero, never -0.00 (the 0 - net negation).
            assert not result.is_signed()

    def test_payoff_overpayment_reads_zero_with_excess_on_the_refund_ledger(
        self, app, db, seed_user, seed_periods,
    ):
        """An overpayment closes the loan at 0.00; the excess is not on the linked ledger.

        A $150,000 payment on the $100,000 balance: interest 500.00, principal
        capped at the 100000.00 that closes the loan, and the 49500.00 surplus
        routed to the per-loan Refund (Asset) ledger, NOT the linked ledger.  The
        reader sums only the linked ledger, so it reports 0.00 (paid off, never a
        negative balance), while the refund ledger separately holds the 49500.00
        the lender owes back -- proving the excess cannot corrupt the balance the
        reader returns.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("150000.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("0.00")
            # The surplus lives on the Refund ledger, invisible to the reader.
            refund = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_REFUND,
            )
            assert _ledger_net(refund.id, scenario_id) == Decimal("49500.00")

    def test_as_of_bounds_the_sum_by_settled_date(
        self, app, db, seed_user, seed_periods,
    ):
        """A historical as_of counts only the payments SETTLED by then (step C2).

        Over the SAME fully-posted ledger (three $1,000 payments in periods 1/3/5,
        each settled ON its period start here), the reader is a point-in-time sum
        bounded by each posting's ``entry_date`` -- a payment's SETTLED date:

          * before P1's settle (period-0 end): opening + true-up only -> 100000.00
          * P1's settle .. period end:          + P1 -> 99500.00 (settled on the
                                                period start, so stable through it)
          * P2's settle:                        + P2 -> 98997.50
          * P3's settle .. _AS_OF:              + P3 -> 98492.49

        No re-walk and no period boundary rule -- just which payments have settled.
        The split still keys on the DUE date (02-01 / 03-01 / 04-01), so the
        principal amounts are unchanged; only WHEN each is visible follows settle.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                    settled_on=period.start_date,
                )
            db.session.commit()

            def read(as_of):
                return posted_loan_balance_at(
                    loan.id, scenario_id, as_of,
                )

            # P1 settled on period 1's start; period 0 ends the day before.
            assert read(last_covered_day(seed_periods[0])) == Decimal("100000.00")
            assert read(seed_periods[_P1].start_date) == Decimal("99500.00")
            # Through P1's period its settle (the period start) is past -> 99500.
            assert read(last_covered_day(seed_periods[_P1])) == Decimal("99500.00")
            assert read(seed_periods[_P2].start_date) == Decimal("98997.50")
            assert read(seed_periods[_P3].start_date) == Decimal("98492.49")
            assert read(_AS_OF) == Decimal("98492.49")

    def test_unconfigured_loan_returns_none(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan with no opening posting reads None, never $0.

        A loan whose ledger carries no OPENING is unconfigured: both the scalar
        and the map return ``None`` so the caller routes to its needs-setup path,
        distinct from a real $0 (a paid-off) balance.

        The broken loan is built EXPLICITLY (``clear_loan_ledger``): the linked
        ledger still exists (the create hook pairs it) but carries no opening
        posting.  Production cannot reach this state -- ``loan.create_params``
        opens the ledger in the same transaction as the ``LoanParams`` insert --
        so the premise is stated rather than inherited from a builder that never
        opened the ledger at all.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            clear_loan_ledger(loan.id)

            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) is None
            assert posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            ) is None

    def test_a_future_as_of_carries_the_confirmed_sum_flat(
        self, app, db, seed_user, seed_periods,
    ):
        """Past today the sum is unchanged -- there is nothing later to add.

        Every posted entry is dated today or earlier, so ``entry_date <= as_of``
        selects the identical set for any future date and the window carries the
        confirmed balance FLAT.  It is NOT a projection and must never be read as
        one -- a forward balance comes from
        :func:`app.services.balance_at.balance_at`, which folds the plan.

        The deleted production reader RAISED here instead (a domain guard that
        forced its callers to route a future date to the projection).  That guard
        existed for callers; the window has none but this suite, and the flat
        answer is what lets the per-period form below check a period boundary
        past today.  Non-vacuity: the in-domain read at the true-up's own date is
        asserted too (C2 -- an anchor counts from its own civil date, so an
        earlier date would still read the origination principal).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, scenario_id, _ANCHOR_DATE,
            ) == Decimal("100000.00")
            # After the frozen 2027-01-01 today: the same sum, carried flat.
            assert posted_loan_balance_at(
                loan.id, scenario_id, date(2027, 1, 2),
            ) == Decimal("100000.00")

    def test_window_is_scenario_scoped_and_none_off_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """The window isolates scenarios; a scenario with no opening reads None.

        A $1,000 payment settled in the baseline auto-posts the opening, true-up,
        and split there only (a what-if with no payment is neither in the payment
        set nor the baseline).  The baseline reads 99500.00; the what-if -- which
        holds no opening -- reads ``None``, never a misleading $0 or the
        baseline's balance.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            assert posted_loan_balance_at(
                loan.id, baseline.id, _AS_OF,
            ) == Decimal("99500.00")
            assert posted_loan_balance_at(
                loan.id, whatif.id, _AS_OF,
            ) is None
            assert posted_loan_balance_map(
                loan.id, whatif.id, seed_periods,
            ) is None

    def test_per_period_map_runs_the_balance_and_carries_flat(
        self, app, db, seed_user, seed_periods,
    ):
        """The map gives each period the cumulative balance, flat between payments.

        Three $1,000 payments land in periods 1 / 3 / 5 (the opening + true-up in
        period 0), each settled ON its period start.  The map is period-END keyed
        (step C2), so each period holds the balance AFTER every payment settled by
        its end, carried flat across payment-less periods:

          period 0: 100000.00 (opening + true-up, pre-payment)
          period 1: 99500.00  (+ P1)      period 2: 99500.00  (flat)
          period 3: 98997.50  (+ P2)      period 4: 98997.50  (flat)
          period 5: 98492.49  (+ P3)      periods 6-9: 98492.49 (flat)

        The map keys by ``period.id`` in the passed order.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                    settled_on=period.start_date,
                )
            db.session.commit()

            result = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            expected = {
                seed_periods[0].id: Decimal("100000.00"),
                seed_periods[1].id: Decimal("99500.00"),
                seed_periods[2].id: Decimal("99500.00"),
                seed_periods[3].id: Decimal("98997.50"),
                seed_periods[4].id: Decimal("98997.50"),
                seed_periods[5].id: Decimal("98492.49"),
                seed_periods[6].id: Decimal("98492.49"),
                seed_periods[7].id: Decimal("98492.49"),
                seed_periods[8].id: Decimal("98492.49"),
                seed_periods[9].id: Decimal("98492.49"),
            }
            assert dict(result) == expected
            # Keyed in the passed period order.
            assert list(result.keys()) == [p.id for p in seed_periods]


class TestPostedLoanBalanceFuturePeriods:
    """The posted sum carries future periods flat, in both forms.

    A separate class because it freezes today MID seed-window (so some seed
    periods are genuinely after today), where
    :class:`TestPostedLoanBalanceSums` freezes after the window.
    """

    @pytest.fixture(autouse=True)
    def _frozen_mid_window(self, monkeypatch):
        """Freeze today inside the seed window so periods 4-9 are in the future.

        2026-02-20 sits in period 3 of the Jan-May seed window, leaving periods
        4-9 starting after today -- the future tail the sum must answer, carried
        flat because no posted entry is dated later than today.
        """
        freeze_today(monkeypatch, date(2026, 2, 20))

    def test_map_carries_flat_for_future_periods(
        self, app, db, seed_user, seed_periods,
    ):
        """Post-today periods carry the confirmed sum flat, scalar and map alike.

        A payment-less trued-up loan (opening -250000 + true-up +150000 =
        -100000) posts its opening + true-up as of the frozen 2026-02-20.  Every
        period -- including periods 4-9, which start after today -- carries the
        flat 100000.00 confirmed balance, because no posted entry is dated later
        than today and so none is added by a later boundary.  The scalar agrees at
        a future period's start, which is what makes the two forms one derivation
        rather than two.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # opening + true-up, no payments
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            # period 3 contains 2026-02-20; period 4 starts 2026-02-27 (future).
            assert seed_periods[3].start_date <= date(2026, 2, 20)
            assert seed_periods[4].start_date > date(2026, 2, 20)

            result = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            # Every period -- historical AND future -- carries the flat balance.
            assert set(result.values()) == {Decimal("100000.00")}
            assert len(result) == len(seed_periods)

            # Historical AND future points agree with the map.
            assert posted_loan_balance_at(
                loan.id, scenario_id, seed_periods[3].start_date,
            ) == Decimal("100000.00")
            assert posted_loan_balance_at(
                loan.id, scenario_id, seed_periods[4].start_date,
            ) == Decimal("100000.00")


class TestUserScopedResync:
    """``resync_user_loan_postings`` + its enumerator reconcile ONE owner's loans.

    The per-user counterpart to the deploy-wide ``backfill_all_loan_postings``,
    called by ``pay_period_admin.reset_pay_periods`` to re-post a reset user's
    loan genesis entries the period wipe cascades -- scoped to that one user so a
    single-user reset never reconciles another owner's loans inside its
    transaction (review M2 / R7).
    """

    @pytest.fixture(autouse=True)
    def _freeze(self, monkeypatch):
        """Pin today after both anchor dates so the genesis walk is eligible."""
        freeze_today(monkeypatch, date(2026, 6, 15))

    def test_enumerator_returns_only_the_users_loans(
        self, app, db, seed_user, seed_second_user,
    ):
        """load_loan_account_ids_for_user filters LoanParams by owner, ascending.

        Two loans on the first user and one on the second: each owner's scoped
        enumeration returns exactly its own loan ids, and the all-owners sweep
        returns their union -- proving the join is owner-scoped, not global.
        """
        with app.app_context():
            loan_a = _make_loan(seed_user, name="Loan A")
            loan_b = _make_loan(seed_user, name="Loan B")
            loan_c = _make_loan(seed_second_user, name="Loan C")
            db.session.commit()

            assert loan_loaders.load_loan_account_ids_for_user(
                seed_user["user"].id,
            ) == sorted([loan_a.id, loan_b.id])
            assert loan_loaders.load_loan_account_ids_for_user(
                seed_second_user["user"].id,
            ) == [loan_c.id]
            # The scoped sets partition the global sweep.
            assert loan_loaders.load_all_loan_account_ids() == sorted(
                [loan_a.id, loan_b.id, loan_c.id],
            )

    def test_resync_posts_only_the_users_genesis(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Re-syncing user 1 posts its genesis and leaves user 2's loan untouched.

        Both users own a configured loan (genesis net -100000 = opening -250000 +
        true-up +150000).  Re-syncing only the first user returns just its loan
        id, posts its opening + true-up (the loan-linked ledger nets -100000), and
        posts NOTHING for the second user (zero genesis entries) -- the scoping
        that keeps a single-user reset from reconciling another owner's loans.

        Both ledgers are CLEARED first (``clear_loan_ledger``), which is what the
        re-sync's real caller does to them: ``pay_period_admin.reset_pay_periods``
        wipes the user's pay periods, and ``journal_entries.pay_period_id`` is ON
        DELETE CASCADE, so the reset disposes exactly these genesis entries and
        this re-sync is what re-derives them.  The builders open both ledgers (as
        production does), so the un-posted starting state has to be made, not
        inherited.
        """
        with app.app_context():
            loan1 = _make_loan(seed_user)
            loan2 = _make_loan(seed_second_user)
            db.session.commit()
            clear_loan_ledger(loan1.id)
            clear_loan_ledger(loan2.id)
            u1 = seed_user["user"].id
            u2 = seed_second_user["user"].id

            posted = loan_posting_service.resync_user_loan_postings(u1)
            db.session.commit()

            assert posted == [loan1.id]
            assert posting_service.account_posting_total(
                loan1.id, seed_user["scenario"].id,
            ) == Decimal("-100000.00")
            # User 2's loan was never visited: no genesis entries posted.
            assert _genesis_entry_count(u2) == 0
            assert loan2.id not in posted


class TestPrePeriodAnchor:
    """An anchor older than every pay period the user has.

    Direct coverage for the ``entry_date`` reader bound (step C2's one clock).
    The shape that once needed a special rule: a journal entry carries its true
    civil ``entry_date`` AND a NOT NULL ``pay_period_id``, and when an anchor
    predates every pay period the user has, the filing rule
    (``pay_calendar.PayCalendar.filing_period``) is FORCED to clamp it into the
    earliest period.  A reader bounding by the pay
    period believed that and reported a loan originated before the user's
    pay-period history as owing NOTHING for the whole span in between (the
    year-end summary turned that $0 into a NEGATIVE principal-paid figure).
    Bounding by ``entry_date`` -- the anchor's own civil date -- reads it
    correctly with no special case, which is what these pin.
    """

    def test_anchor_older_than_every_pay_period_is_still_owed(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan originated BEFORE the user's first pay period still owes it all.

        ``seed_periods`` begins 2026-01-02.  This loan is originated 2025-06-01, so
        its opening is forced into the earliest period (there is no period covering
        2025) -- and before this fix the reader therefore reported the loan as owing
        $0.00 for every date in 2025, despite the debt plainly existing.

        The opening asserts $250,000 and no payment is recorded, so the honest
        balance on 2025-12-31 is the full $250,000 -- and so is EVERY seed
        period's, since they all end in 2026 (after the origination) and no
        payment ever lands.  Both are pinned to that hand-computed literal, at a
        date inside the pre-period gap and at every period boundary.

        **Both halves assert the literal, deliberately.**  The obvious
        alternative -- assert the per-period value equals the scalar at that
        period's end -- is now ``f(x) == f(x)``: the per-period window is
        DEFINED as the scalar applied at each period end (plan step E1e), where
        the deleted production map was an independent prefix-sum whose boundary
        handling such a comparison did pin.  A cross-derivation check that
        cannot fail is worse than none, so the value carries the teeth here.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, name="Old Loan",
                principal=Decimal("250000.00"), rate=_RATE, term=360,
                origination_date=date(2025, 6, 1),
            )

            # A date inside the gap: after origination, before any pay period.
            owed = posted_loan_balance_at(
                loan.id, scenario_id, date(2025, 12, 31),
            )
            assert owed == Decimal("250000.00"), (
                f"a loan originated 2025-06-01 read as owing {owed} on "
                f"2025-12-31; the ledger is dating its opening by the pay period "
                f"it was FILED under, not the date it asserts"
            )

            # Every period boundary reads the same $250,000: the opening counts
            # from its own 2025-06-01 civil date (step C2), which precedes every
            # seed period's end, and nothing pays it down.
            bmap = posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            )
            assert bmap is not None
            assert len(bmap) == len(seed_periods)
            assert set(bmap.values()) == {Decimal("250000.00")}, (
                f"a pre-period opening read unevenly across period boundaries: "
                f"{sorted(str(v) for v in set(bmap.values()))}"
            )


# ---------------------------------------------------------------------------
# The checked projection (plan step E1a): sum(postings) == fold(events),
# asserted per visible date at every write, and the date-aware reconcile
# (finding N-13) that makes the assert safe on a legitimate settle-day edit.
# ---------------------------------------------------------------------------


def _linked_net_by_date(ledger_id, scenario_id):
    """Return ``{entry_date: net}`` over one ledger's postings in a scenario.

    Delegates to the shared :func:`tests._test_helpers.linked_net_by_date` -- an
    INDEPENDENT re-implementation of the grouped read the checked-projection assert
    performs (production groups via ``_visible_nets``), kept independent of the
    production query so this suite still has teeth if that query drifts.
    """
    return linked_net_by_date(_db.session, ledger_id, scenario_id)


def _source_entry_count(transfer_id, loan_account_id):
    """Count the journal entries a payment's lineage owns (cash + corrections).

    The cash is two per-movement entries since plan step ``balance:X-bi-6-3``
    (``transfer_family_journal_filter``); the split is a date-keyed correction
    with no row link (ruling **R-BAL102**), so the corrections counted are
    every ``loan_payment`` entry on the loan's chart rows -- the tests here
    hold one payment per loan, and a convergence check ("a repeat sync writes
    nothing") is a count over the whole lineage either way.
    """
    # pylint: disable=import-outside-toplevel  -- the reconcile's own scope
    # helper, imported here so the count reads the same rows the writer does.
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


class TestCheckedProjection:
    """The posted linked ledger must equal the fold of the loan's events (E1a).

    ``sync_loan_postings`` runs the per-visible-date assert after both
    reconciles: the walk's dated deltas (negated into posting space) must match
    the linked ledger's per-``entry_date`` nets exactly.  These tests prove the
    assert has teeth (a forced $1.00 walk-invisible posting fires it) and that
    the date-aware reconcile keeps it from firing on the one legitimate edit
    that moves a date without moving an amount -- a settled settle-day edit
    (finding N-13) -- converging in ONE pass in both directions.
    """

    def test_walk_invisible_posting_fires_the_assert(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1.00 posting the walk cannot model makes the sync refuse to run green.

        The negative control (verification standard 7.3): a balanced raw entry
        with a $1.00 leg on the loan's LINKED ledger -- the N-11 legacy shape,
        forbidden at the source since BG/R6 but forced here directly -- is
        invisible to the walk, so the per-date comparison must fail AT THAT
        DATE and name it.  Without this control the assert could be deleted
        and every test would stay green.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()
            # The settle chokepoint already ran the sync + assert green.

            equity_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            drift_entry = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=scenario_id,
                pay_period_id=seed_periods[_P1].id,
                entry_date=date(2026, 2, 15),
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSACTION
                ),
                description="forced walk-invisible drift",
            )
            db.session.add(drift_entry)
            db.session.flush()
            expense_kind = ref_cache.posting_kind_id(PostingKindEnum.EXPENSE)
            db.session.add(Posting(
                journal_entry_id=drift_entry.id,
                ledger_account_id=_linked_ledger_id(loan),
                amount=Decimal("1.00"),
                posting_kind_id=expense_kind,
            ))
            db.session.add(Posting(
                journal_entry_id=drift_entry.id,
                ledger_account_id=equity_ledger.id,
                amount=Decimal("-1.00"),
                posting_kind_id=expense_kind,
            ))
            db.session.flush()

            with pytest.raises(
                posting_service.PostingError,
                match="diverges from the fold .*2026-02-15",
            ):
                loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.rollback()

    def test_settle_day_edit_redates_the_postings(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled settle-day edit moves every posting to the new settle date.

        The N-13 regression.  P1's $1,000.00 payment settles 2026-01-20:
        interest 100000 * 0.06 / 12 = 500.00, principal 500.00, so the linked
        ledger nets +1000.00 (cash) - 500.00 (correction) = +500.00 on 01-20.
        A PURE settle-day edit to 2026-02-05 changes no amount, so the
        pre-E1a reconcile wrote nothing and the entries kept the old date --
        while the fold moved to 02-05, a divergence the checked-projection
        assert now refuses.  After the edit: 01-20 nets 0.00 (reversed at its
        own date), 02-05 nets +500.00, and a repeat sync writes NOTHING (the
        reconcile converged in one pass -- no churn).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()
            linked_id = _linked_ledger_id(loan)
            assert _linked_net_by_date(linked_id, scenario_id)[
                date(2026, 1, 20)
            ] == Decimal("500.00")

            # The pure settle-day edit -- no amount, status, or period change.
            # update_transfer runs the (now date-aware) reconciles + the
            # checked-projection assert; a raise here IS the N-13 regression.
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(date(2026, 2, 5)),
                ),
            )
            db.session.commit()

            by_date = _linked_net_by_date(linked_id, scenario_id)
            # Old date: reversed at its own date (present but net zero).
            assert by_date[date(2026, 1, 20)] == Decimal("0.00")
            # New date: the full effect -- cash +1000.00, correction -500.00.
            assert by_date[date(2026, 2, 5)] == Decimal("500.00")

            # Convergence: a repeat of BOTH syncs writes nothing.
            before = _source_entry_count(xfer.id, loan.id)
            posting_service.sync_transfer_postings(xfer)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.flush()
            assert _source_entry_count(xfer.id, loan.id) == before

    def test_backward_settle_day_move_converges(
        self, app, db, seed_user, seed_periods,
    ):
        """Moving the settle day EARLIER re-dates and converges in one pass too.

        The churn control: a latest-entry-date staleness heuristic would see
        the old-dated reversal as "the latest posting" after a BACKWARD move
        and re-churn (reverse + repost) on every later sync forever.  The
        per-(period, date) reconcile keys each delta to its exact date, so the
        backward move (02-05 -> 01-20) lands in one pass: 02-05 nets 0.00,
        01-20 nets +500.00, and a repeat sync writes nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 2, 5),
            )
            db.session.commit()
            linked_id = _linked_ledger_id(loan)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(date(2026, 1, 20)),
                ),
            )
            db.session.commit()

            by_date = _linked_net_by_date(linked_id, scenario_id)
            assert by_date[date(2026, 2, 5)] == Decimal("0.00")
            assert by_date[date(2026, 1, 20)] == Decimal("500.00")

            before = _source_entry_count(xfer.id, loan.id)
            posting_service.sync_transfer_postings(xfer)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.flush()
            assert _source_entry_count(xfer.id, loan.id) == before

    def test_legacy_stale_dated_cash_pair_self_heals(
        self, app, db, seed_user, seed_periods,
    ):
        """A pre-E1a cross-date transfer residue heals in the loan sync itself.

        The REAL-data shape the dev-clone sweep found (the Mortgage's July
        payment): the date-blind pre-E1a reconcile reversed a cash entry at a
        DIFFERENT date than it posted, leaving a net-zero transfer-source pair
        straddling two dates -- residue no loan-side reconcile can touch, so
        without the lineage-transfer pass the checked-projection assert would
        fire on it forever (the C9a self-500 class).  Forged here by re-dating
        the settled payment's cash entry (exactly what the legacy data
        contains), the loan sync must re-date it back (reverse at the forged
        date, re-post at the settle date), pass its own assert, and converge
        (a second sync writes nothing).  The cash entry is the loan-side
        MOVEMENT's since plan step ``balance:X-bi-6-3``, and the probe keys
        on it (re-expressed; the forge targeted the one ``transfer_id``
        entry).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()
            linked_id = _linked_ledger_id(loan)

            # Forge the legacy shape: the loan-side cash entry carries a WRONG
            # date, as a pre-E1a revert / re-settle left it.  Raw SQL, because
            # the forge must bypass the ORM append-only listener exactly as
            # the legacy writes predate it (the test runs as the table owner;
            # production's role REVOKE does not bind here).
            cash_entry_id = (
                db.session.query(JournalEntry.id)
                .filter(
                    JournalEntry.transaction_entry_id
                    == _loan_side_movement(xfer, loan).id,
                    JournalEntry.scenario_id == scenario_id,
                )
                .scalar()
            )
            db.session.execute(
                sa.text(
                    "UPDATE budget.journal_entries SET entry_date = :day "
                    "WHERE id = :entry_id"
                ),
                {"day": date(2026, 1, 5), "entry_id": cash_entry_id},
            )
            db.session.commit()
            assert _linked_net_by_date(linked_id, scenario_id)[
                date(2026, 1, 5)
            ] == Decimal("1000.00")

            # The loan sync heals it: lineage-transfer pass re-dates, then
            # the checked-projection assert passes.
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.commit()
            by_date = _linked_net_by_date(linked_id, scenario_id)
            # Forged date: reversed at its own date; settle date: the full
            # effect (cash +1000.00, correction -500.00).
            assert by_date[date(2026, 1, 5)] == Decimal("0.00")
            assert by_date[date(2026, 1, 20)] == Decimal("500.00")

            # Convergence: a repeat sync writes nothing.
            before = _source_entry_count(xfer.id, loan.id)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.flush()
            assert _source_entry_count(xfer.id, loan.id) == before

    def test_reverted_transfer_stale_residue_self_heals(
        self, app, db, seed_user, seed_periods,
    ):
        """Pre-E1a cross-date residue on a REVERTED payment heals too (review H2).

        A reverted payment is OUTSIDE the walk's settled set, so a heal pass
        keyed on the walk's payments would never touch its entries -- while
        the pre-E1a date-blind reconcile could leave its settle/reversal pair
        straddling two dates (net zero in TOTAL, nonzero per DATE), and the
        checked-projection assert would then fire on every later sync of the
        loan with nothing able to converge it.  The lineage pass therefore
        derives its candidates from the LEDGER: forged here by re-dating the
        reverted payment's reversal entry (the legacy shape), the loan sync
        must re-sync that transfer with its CURRENT unsettled sense, zero
        every date, pass its own assert, and converge.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 1, 20),
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            linked_id = _linked_ledger_id(loan)
            # Clean revert: the settle date nets zero (the anchor dates --
            # the opening and true-up -- legitimately keep their nets).
            assert _linked_net_by_date(linked_id, scenario_id)[
                date(2026, 1, 20)
            ] == Decimal("0.00")

            # Forge the pre-E1a residue: the loan-side REVERSAL entry (the
            # newest entry linked to the loan-side movement, plan step
            # ``balance:X-bi-6-3``) re-dated away from the settle date, so
            # 01-20 holds +1000.00 and 01-08 holds -1000.00.
            reversal_id = (
                db.session.query(JournalEntry.id)
                .filter(
                    JournalEntry.transaction_entry_id
                    == _loan_side_movement(xfer, loan).id,
                    JournalEntry.scenario_id == scenario_id,
                )
                .order_by(JournalEntry.id.desc())
                .limit(1)
                .scalar()
            )
            db.session.execute(
                sa.text(
                    "UPDATE budget.journal_entries SET entry_date = :day "
                    "WHERE id = :entry_id"
                ),
                {"day": date(2026, 1, 8), "entry_id": reversal_id},
            )
            db.session.commit()
            by_date = _linked_net_by_date(linked_id, scenario_id)
            assert by_date[date(2026, 1, 20)] == Decimal("1000.00")
            assert by_date[date(2026, 1, 8)] == Decimal("-1000.00")

            # The loan sync heals it from the LEDGER-derived candidate set
            # (the transfer is NOT in the walk -- it is reverted) and passes
            # its own checked-projection assert: both payment dates net zero.
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.commit()
            by_date = _linked_net_by_date(linked_id, scenario_id)
            assert by_date[date(2026, 1, 20)] == Decimal("0.00")
            assert by_date[date(2026, 1, 8)] == Decimal("0.00")

            # Convergence: a repeat sync writes nothing.
            before = _source_entry_count(xfer.id, loan.id)
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.flush()
            assert _source_entry_count(xfer.id, loan.id) == before


