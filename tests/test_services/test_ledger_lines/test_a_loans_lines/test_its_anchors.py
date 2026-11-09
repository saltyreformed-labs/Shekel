"""A loan's anchors on the worked-out read: the opening, each true-up, and the paycheck each files in."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.services import loan_ledger, loan_posting_service, pay_period_admin
from app.services.ledger_lines import loan_key
from tests._ledger_reads import (
    account_net,
    correction_net_in_period,
    linked_key_of,
    posted_loan_balance_at,
    posted_loan_balance_map,
    scenario_lines,
)
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    freeze_today,
    insert_tracking_start_event,
    last_covered_day,
)

from ._helpers import (
    _AS_OF,
    _ORIGINATION_BEFORE_P1,
    _ORIGINATION_DATE,
    _ORIGINATION_PRINCIPAL,
    _P1,
    _P2,
    _P3,
    _RATE,
    _anchor_lines,
    _anchor_net,
    _equity_net,
    _loan_params,
    _make_loan,
    _settle_payment,
)


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
        # Freeze today after the payment period, so no date the case states
        # is in the future on any CI clock.
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


class TestTheAnchorsBookTheOwedBalance:
    """The opening + true-up corrections land the loan's own line on -(owed)."""

    def test_the_opening_books_the_original_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An origination-only loan books the opening: own -P0, equity +P0.

        A loan with only its origination anchor ($250,000) and no payments
        books ONE opening line on its own line: owed_before is 0 at
        origination, so the loan's own line gets 0 - 250000 = -250000.00 and
        the per-loan opening-equity line its negative, +250000.00.  The own
        line's net is then -250000, so the reader (which negates it) reports
        the full $250,000 owed.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _equity_net(loan, scenario_id) == Decimal("250000.00")
            assert [
                line.amount for line in _anchor_lines(
                    loan, scenario_id, PostingSourceEnum.LOAN_OPENING,
                )
            ] == [Decimal("-250000.00")]
            assert _anchor_lines(
                loan, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_and_trueup_drive_linked_to_negative_verified(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + true-up land the loan's own line on -(verified balance).

        Origination $250,000 then a trueup to $100,000 (no payments):
          opening (owed_before 0): own 0 - 250000 = -250000.00.
          trueup (owed_before 250000, the origination balance carried to the
            trueup date): own 250000 - 100000 = +150000.00.
        Own net -250000 + 150000 = -100000.00 -- exactly -(the $100,000 the
        user verified), the opening + trueup reproducing the resolver's anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # origination 250000, trueup 100000
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _equity_net(loan, scenario_id) == Decimal("100000.00")
            assert _anchor_net(
                loan, scenario_id, PostingSourceEnum.LOAN_OPENING,
            ) == Decimal("-250000.00")
            assert _anchor_net(
                loan, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == Decimal("150000.00")

    def test_trueup_matching_the_walk_books_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A trueup equal to the walked balance books no true-up line.

        Origination $250,000 and a trueup ALSO of $250,000 (no payments): at the
        trueup date owed_before is 250000, so the correction 250000 - 250000 = 0
        books nothing.  Only the opening books; the own line nets -250000.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("250000.00"))
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _anchor_lines(
                loan, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_payments_trueup_reproduce_resolver_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + payment splits + true-up net to -(resolver balance).

        Three post-trueup $1,000 payments split to principal 500.00 + 502.50 +
        505.01 = 1507.51 (the running-balance walk from the $100,000 trueup),
        so the resolver's balance is 100000 - 1507.51 = 98492.49.  With the
        opening (-250000) and true-up (+150000) added, the loan's own net is
        (1507.51 payment nets) - 100000 = -98492.49 -- exactly -(the resolver
        balance), proving the from-origination sum of lines reproduces the
        resolver on a trued-up loan.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-98492.49")

    def test_a_pre_trueup_payment_re_bases_the_trueup(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling a pre-trueup payment re-bases the true-up.

        The true-up correction is owed_before - verified, and owed_before moves
        when a pre-trueup payment changes.  Origination $250,000, trueup
        $100,000 @ 2026-02-15: with no payments the opening (-250000) and
        true-up (250000 - 100000 = +150000) net the loan's own line to
        -100000.00, and the per-loan opening-equity line holds their negatives,
        +100000.00.  Settling P1 (due 02-01, PRE-trueup, cash 1000) splits it
        on the origination balance (interest round(250000 * 0.005) = 1250,
        principal -250) and grows owed_before to 250250, so the true-up
        re-bases to 250250 - 100000 = +150250 (a +250 delta) and the own line
        STAYS -100000.00 == -(verified $100,000), the pre-trueup payment fully
        absorbed; the equity line re-bases to 250000 - 150250 = +99750.00.
        It was ``test_trueup_self_heals_when_a_pre_trueup_payment_is_added``,
        the stored reconcile healing its posted true-up, until plan step
        plan#2914 deleted the store.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 2, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            db.session.commit()
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            # Opening (+250000) + true-up (-150000) on the equity line.
            assert _equity_net(loan, scenario_id) == Decimal("100000.00")
            assert _anchor_net(
                loan, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == Decimal("150000.00")

            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _equity_net(loan, scenario_id) == Decimal("99750.00")
            assert _anchor_net(
                loan, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == Decimal("150250.00")

    def test_payment_due_on_anchor_date_is_subsumed_by_the_reset(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due EXACTLY on the anchor date is walked before the reset, then subsumed.

        The strict ``anchor_date < monthly_due_date`` boundary means a payment due
        ON an anchor's date is PRE-anchor (subsumed).  The walk realises this by
        sorting a payment (tag 0) before an anchor (tag 1) on an equal date, so P1
        (period 1, due 2026-02-01 at payment_day=1) is split on the $250,000
        origination balance BEFORE the 2026-02-01 trueup resets to $100,000:
        interest round(250000 * 0.005) = 1250.00 lands in the interest line, but
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
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")

    def test_a_payment_less_loan_opens_in_the_baseline(
        self, app, db, seed_user,
    ):
        """A brand-new loan with no payments books its opening in the baseline.

        Origination $250,000, trueup $100,000: the opening (-250000) + true-up
        (+150000) net the baseline's own line to -100000.00 == -(anchor
        100000), and the per-loan opening-equity line holds +100000; no
        interest line books (there is no payment).  It was
        ``test_posts_opening_for_a_payment_less_loan_in_the_baseline``, the
        stored all-scenarios sync adding the owner's baseline to an empty
        payment set, until plan step plan#2914 deleted the store.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id  # the owner's baseline
            loan = _make_loan(seed_user)  # no payments settled
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _equity_net(loan, scenario_id) == Decimal("100000.00")
            interest = loan_key(LedgerAccountKindEnum.LOAN_INTEREST, loan.id)
            assert [
                line for line in scenario_lines(_db.session, scenario_id)
                if line.key == interest
            ] == []


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
    then creates the period that really contains it, and the read files the
    correction there.
    """

    _LATE_ANCHOR_DATE = date(2026, 6, 15)

    def test_extending_the_schedule_refiles_an_anchor_into_its_real_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A true-up dated past the schedule moves to the period that grows around it.

        ``seed_periods`` ends 2026-05-21, so a $100,000 true-up dated
        2026-06-15 has no containing period and files in the LAST one by
        fallback.  Its correction is ``owed_before - verified = 250000 -
        100000 = +150,000.00``.  Extending the schedule by three periods
        creates the one containing 2026-06-15, so the stale period nets back
        to ``0.00`` and the real one carries the full ``+150,000.00``.  The
        loan's total is unchanged either way -- this is an attribution move,
        not a revaluation.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            loan = _make_loan(seed_user, anchor_date=self._LATE_ANCHOR_DATE)
            db.session.commit()
            own = linked_key_of(_db.session, loan.id)

            stale_period = seed_periods[-1]
            # The precondition: no seeded period contains the anchor date, so
            # the true-up landed on the last one by fallback.
            assert last_covered_day(stale_period) < self._LATE_ANCHOR_DATE
            assert correction_net_in_period(
                _db.session, own, scenario_id,
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

            assert correction_net_in_period(
                _db.session, own, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, stale_period.id,
            ) == Decimal("0.00")
            assert correction_net_in_period(
                _db.session, own, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, containing.id,
            ) == Decimal("150000.00")
            # -250000 opening + 150000 true-up: the attribution moved, the
            # balance did not.
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
