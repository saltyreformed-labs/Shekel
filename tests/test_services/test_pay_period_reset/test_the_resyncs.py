"""Where a full reset files what the period wipe cannot reach: the loan's genesis lines and the
other accounts' openings, each on the rebuilt schedule and netting what it did before.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.enums import PostingSourceEnum
from app.models.account import Account, AccountAnchorHistory
from app.services import pay_period_admin
from app.services.ledger_lines import linked_key
from scripts.integrity_check import check_referential_integrity
from tests._ledger_reads import account_net, filed_period_id, source_lines
from tests._test_helpers import (
    rhythm_of,
    all_periods,
    assert_pay_period_invariants,
    create_loan_with_trueup,
    derived_span,
)

from ._helpers import _NEW_START, _seed_old_schedule


# A configured loan for the genesis-resync regression (review M2 / R7):
# origination $250,000, trued up to $100,000, so its genesis ledger nets
# -(100000) = opening -250000 + true-up +150000.  Both anchor dates predate the
# rebuilt schedule's 2026-06-05 start, so after reset every correction
# re-attributes to the new earliest period.
_LOAN_ORIGINATION = Decimal("250000.00")


_LOAN_ANCHOR_BALANCE = Decimal("100000.00")


_LOAN_ANCHOR_DATE = date(2026, 1, 10)


_LOAN_ORIGINATION_DATE = date(2025, 1, 1)


_LOAN_RATE = Decimal("0.06000")


_LOAN_GENESIS_NET = Decimal("-100000.00")


def _genesis_filed_in(db_session, loan, scenario_id):
    """Return the paychecks a loan's opening and true-up lines are filed in."""
    return {
        filed_period_id(line, loan.user_id)
        for source in (PostingSourceEnum.LOAN_OPENING, PostingSourceEnum.LOAN_TRUEUP)
        for line in source_lines(db_session, linked_key(loan), scenario_id, source)
    }


class TestAResetFilesTheLoanGenesis:
    """A full reset files the loan's genesis lines on the rebuilt schedule.

    A configured loan's opening and true-up are worked out from its source facts
    (``LoanParams``, the ``user_trueup`` ``LoanAnchorEvent``), which the period
    wipe does not touch, so after the reset they file in the rebuilt schedule
    and the loan nets what it did (review M2 / R7).  *The reset re-synced the
    stored ledger's copy of them until plan task plan#3076; this class was
    ``TestResetResyncsLoanGenesis``, and its case beside this one,
    ``test_the_repopulation_cannot_invalidate_either_resync``, graded that the
    repopulation left both re-syncs at their fixpoint, and went with them.*
    """

    def test_the_genesis_files_anew_and_nets_the_same_after_a_reset(
        self, app, db, seed_user,
    ):
        """Genesis nets -(anchor) before AND after reset, re-attributed anew.

        Opening -250000 + true-up +150000 = -100000 == -(anchor 100000).  After
        the reset the opening and true-up lines file in the rebuilt schedule's
        earliest period (both anchor dates precede its 2026-06-05 start), and the
        loan's own line nets -100000 again.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            _seed_old_schedule(db.session, seed_user)
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_LOAN_ORIGINATION,
                anchor_balance=_LOAN_ANCHOR_BALANCE,
                anchor_date=_LOAN_ANCHOR_DATE,
                rate=_LOAN_RATE,
                origination_date=_LOAN_ORIGINATION_DATE,
            )
            db.session.commit()

            assert account_net(
                db.session, loan.id, scenario_id,
            ) == _LOAN_GENESIS_NET

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # The genesis lines file in the new earliest period (both anchor
            # dates precede it), and the loan's own line nets -(anchor) again.
            assert _genesis_filed_in(db.session, loan, scenario_id) == {new_periods[0].id}
            assert account_net(
                db.session, loan.id, scenario_id,
            ) == _LOAN_GENESIS_NET
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))


class TestAResetFilesTheAccountOpenings:
    """A full reset files the non-loan account openings on the rebuilt schedule.

    The Step-5 analogue of the loan class above (plan Section 3.3, point 4):
    a non-loan account's opening is worked out from its assertion, which the
    wipe does not reach, so after the reset it files in the rebuilt schedule
    and the account nets what it did.

    **What it re-derives FROM changed at ruling R-EO** (plan step X-f1c3c).
    The wipe used to take the account's ``AccountAnchorHistory`` rows with it
    -- they carried a ``pay_period_id`` on a CASCADE FK -- and a
    ``_reanchor_accounts`` pass staged one fabricated origination row per
    account for this re-sync to read.  The assertion carries no period now, so
    the wipe cannot reach it and the opening is worked out from the
    observations that were always there.  ``_reanchor_accounts`` is deleted;
    this docstring named it until X-f1c3c.
    """

    def test_the_openings_file_anew_and_net_the_same_after_a_reset(
        self, app, db, seed_user,
    ):
        """The Checking opening nets +1000 before AND after, re-attributed anew.

        The seed Checking's $1000.00 opening nets 1000.00 before the reset;
        after it, the opening's line files in the rebuilt schedule's earliest
        period and the account's own line nets 1000.00 again.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking_id = seed_user["account"].id
            _seed_old_schedule(db.session, seed_user)
            assert account_net(
                db.session, checking_id, scenario_id,
            ) == Decimal("1000.00")

            pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Re-fetch: the reset's identity-map wipe detached the fixture
            # object.
            checking = db.session.get(Account, checking_id)
            # The opening correction books in the period CONTAINING the
            # assertion's own day (ruling R-EA: the period is DERIVED from the
            # day, never read beside it).  The containing period is resolved
            # HERE from the dates, not by calling the resolver the writer
            # calls, so the two cannot agree by sharing one implementation.
            #
            # A first version of this assertion only checked membership in the
            # set of all live periods -- true of any of the six, so it would
            # have passed against a correction filed in the wrong one.
            rebuilt = all_periods(user_id)
            opening_assertion = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=checking_id)
                .order_by(AccountAnchorHistory.observed_on.asc())
                .first()
            )
            # The fixture's Checking is asserted on 2024-01-05 and the rebuilt
            # schedule starts 2026-06-05, so the assertion's day precedes EVERY
            # period.  ``pay_calendar.PayCalendar.filing_period`` clamps such a
            # correction into the user's EARLIEST period -- index 0 -- so the
            # reader, which bounds by period start, counts it from the first
            # period on.
            assert all(
                opening_assertion.observed_on < p.start_date for p in rebuilt
            ), (
                f"this test's expected period is the no-containing-period "
                f"branch; the assertion ({opening_assertion.observed_on}) must "
                f"precede every rebuilt period"
            )
            earliest = min(rebuilt, key=lambda p: derived_span(p).period_index)
            assert derived_span(earliest).period_index == 0
            assert {
                filed_period_id(line, checking.user_id) for line in source_lines(
                    db.session, linked_key(checking), scenario_id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )
            } == {earliest.id}
            assert account_net(
                db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))
