"""The write walk reads no clock (A3), a tracking-start event opens a mid-life loan at its recent
balance, and a skipped month is the next payment's arrears.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, LedgerAccountKindEnum, PostingKindEnum
from app.extensions import db as _db
from app.services import loan_ledger, loan_posting_service
from tests._ledger_reads import account_net, posted_loan_balance_at
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    freeze_today,
    insert_tracking_start_event,
)

from ._helpers import (
    _ORIGINATION_PRINCIPAL,
    _ORIGINATION_DATE,
    _RATE,
    _P1,
    _AS_OF,
    _loan_params,
    _settle_payment,
    _linked_ledger_id,
    _find_loan_ledger,
    _genesis_entry_count,
    _correction_entries,
    _entry_legs,
)


# ---------------------------------------------------------------------------
# the write walk reads no clock -- A3
# ---------------------------------------------------------------------------


class TestWalkReadsNoClock:
    """The persisted ledger is a function of the loan's DATA, not the wall clock.

    The walk used to drop any anchor dated after the sync's as-of, which made what
    the ledger PERSISTED depend on the moment the sync happened to run.  That is
    not a cache; it is a corruption generator, and a loan configured before its
    closing date reached it through the ordinary UI: its opening went unposted.

    The test here uses a $200,000 / 5% / 360mo mortgage originating 2026-04-15 --
    AFTER the suite's frozen today (2026-03-20), which ``origination_date``
    permits (unlike a true-up's ``anchor_date``, it carries no not-future
    validator, and the developer ruled the app must support it).  No payment
    into it can be recorded before it starts: ruling plan#4040 refuses money
    into a loan on or before its origination.  So the filter's other
    consequence, a payment settled before closing split against a $0.00
    balance, is a state that can no longer be stored, and the opening is the
    half left to pin.
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

    def test_the_posted_ledger_is_identical_at_two_different_clocks(
        self, app, db, seed_user, monkeypatch,
    ):
        """Syncing before vs after origination persists the SAME opening.

        The property A3 exists to establish, stated directly: the walk's output is
        a function of the loan's facts alone.  The loan is configured, and its
        ledger opened, at 2026-03-20 (before it closes); the clock then moves past
        origination to 2026-05-07 and the SAME sync re-runs.  Reconcile-to-target
        means an already-correct ledger is not re-posted, so an unchanged entry
        count is the proof: the first sync already recorded everything.  The
        read's figure beside it grades that the walk's answer did not move with
        the clock.

        The OPENING leg is what the clock moved: -$200,000.00 on the linked
        ledger.  No payment rides beside it, since none can be recorded at the
        first clock (ruling plan#4040; the class docstring).

        NEGATIVE CONTROL: restore the ``anchor.anchor_date <= as_of`` filter --
        it would now go in ``loan_ledger.loan_event_stream``, where the reset
        events are built (it was ``merge_anchor_and_payment_events`` until plan
        step X-au-g-2c-3b-2), reading today -- and the first sync posts no
        opening, so the linked ledger nets to exactly ``0.00``: the loan reads
        as owing NOTHING.  The first assert below then fails
        ``0.00 != -200000.00``.
        """
        with app.app_context():
            loan = self._upcoming_mortgage(seed_user)
            scenario_id = seed_user["scenario"].id

            # Creating the loan opened its ledger, as the setup door does, as of
            # 2026-03-20 -- before origination.
            linked_after_first = account_net(_db.session, loan.id, scenario_id)
            entries_after_first = _genesis_entry_count(seed_user["user"].id)
            # The -200,000.00 opening alone, posted by a sync run 26 days
            # before the loan even closes.
            assert linked_after_first == Decimal("-200000.00")

            # The clock crosses origination; the same sync re-runs.
            freeze_today(monkeypatch, date(2026, 5, 7))
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            assert account_net(_db.session, loan.id, scenario_id) == linked_after_first
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
