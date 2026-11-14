"""Balance-at seam tests: the forward fold's seed, scenario-independent terms, and the recorded
start.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum
from app.services import balance_at, liability_sign, property_equity_chart
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from app.services.balance_at._secured_debt import _back_projection_by_month
from app.services.installment_calendar import final_installment_date
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_transfer,
    freeze_today,
    insert_tracking_start_event,
    insert_trueup_event,
    loan_params_for,
)

from ._helpers import (
    PAID_LOAN_LAST_CONFIRMED_REMAINING,
    PAID_LOAN_TRUED_UP_TO,
    _paid_then_trued_loan,
)


class TestForwardFoldSeedsFromTheConfirmedPresent:
    """The forward fold starts from the confirmed present, not a stale schedule row.

    Since step C6b the seam's forward projection is a FOLD from the loan's
    confirmed-present seed over its :func:`~app.services.balance_at._plan.loan_plan`
    (payment records + contractual synthesis), not a walk of the resolver's
    schedule rows.  A trued-up loan's confirmed present is the TRUE-UP balance, and
    the fold seeds from exactly that -- never from a CONFIRMED schedule row's
    ``remaining_balance``, which is what the loan owed BEFORE the true-up and is
    arbitrarily stale (here by $48,496.25).

    This is the seam-level guard for that seeding.  The retired schedule walk had a
    ``_forward_rows`` ``is_confirmed`` filter to drop confirmed rows before walking
    (finding B-4); the fold has no such filter to get wrong -- it seeds from the
    ledger-confirmed balance and folds only PLANNED/ESTIMATED records forward, so a
    confirmed row's stale balance has no path into a future answer by construction.
    The unit-level proof that a settled payment is not re-folded as a future record
    is C6a's ``test_an_early_settled_payment_is_not_re_synthesized_as_estimated``;
    this pins the property at the seam, on a trued-up loan whose stale confirmed row
    would be the wrong answer.
    """

    def test_a_confirmed_rows_stale_balance_never_answers_a_future_date(
        self, app, db, seed_user, seed_periods,
    ):
        """Inside the window, the forward fold answers the TRUE-UP, not a paid row.

        On 2026-03-25 the loan owes exactly what the operator asserted on 03-15
        ($200,000.00): nothing is due until 04-01, so the fold has no record to
        apply and holds at its confirmed-present seed.  A walk that reached back to
        the 03-01 payment row would report $248,496.25 -- the balance the loan owed
        three weeks BEFORE the true-up, and $48,496.25 too much -- which is exactly
        the stale-row answer the fold's seeding avoids.

        The scalar reports what the loan HOLDS, negative when owed (ruling
        R-CC47, plan step credit_card:CC-5-5c), so both of its reads below go
        through ``liability_sign.owed`` before they meet the owed figures.
        """
        with app.app_context():
            periods = seed_periods
            loan = _paid_then_trued_loan(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            # The loan's schedule rows, off the read pass's one resolution: rows
            # carry no balance.  Every balance below comes from the seam, which
            # is the architecture this suite exists to defend.  (They were read
            # through the ``debt_schedule_rows`` accessor until plan step
            # recurrence:R16-c-2 deleted it with its last caller.)
            rows = sorted(
                resolved_loan(loan, bctx).state.schedule,
                key=lambda row: row.payment_date,
            )
            confirmed = [row for row in rows if row.is_confirmed]
            unconfirmed = [row for row in rows if not row.is_confirmed]

            # The fixture really is the shape this guards (non-vacuity): the
            # ledger booked two payments, and the stale balance their rows carry
            # is NOT the balance the loan actually owes.
            assert len(confirmed) == 2
            assert confirmed[-1].payment_date == date(2026, 3, 1)
            stale = confirmed[-1].remaining_balance
            assert stale == PAID_LOAN_LAST_CONFIRMED_REMAINING
            assert liability_sign.owed(balance_at.balance_at(
                loan, bctx, bctx.as_of,
            )) == PAID_LOAN_TRUED_UP_TO

            # The probe sits just AFTER the resolver's now and BEFORE the next
            # installment falls due, so the fold has no record to apply in the
            # window and must hold at the confirmed-present seed.
            probe = date(2026, 3, 25)
            assert bctx.as_of < probe < unconfirmed[0].payment_date

            owed = liability_sign.owed(balance_at.balance_at(loan, bctx, probe))

            # THE CONTROL, and the only line here that can fail: nothing is due
            # between 03-20 and 03-25, so the fold holds at the trued-up balance.
            # A forward path that seeded from the 03-01 confirmed row instead would
            # read $248,496.25 -- the stale pre-true-up balance -- which is the
            # answer the fold's confirmed-present seeding structurally avoids.
            assert owed == PAID_LOAN_TRUED_UP_TO
            # Documentation, not a control: with both operands pinned to
            # literals above, the arithmetic below holds however wrong the
            # producer is.  It records the blast radius -- $48,496.25, and
            # unbounded in the size of the true-up -- next to the code that
            # bounds it.
            assert stale - owed == Decimal("48496.25")


class TestLoanTermsAreScenarioIndependent:
    """Plan C8e: a loan's CONTRACT terms need no baseline scenario.

    ``LoanFigures`` used to carry the payment and the rate alongside the
    scenario-scoped predicates, and nothing exposed the mixture while every field
    happened to be scenario-independent.  Step C8d added the DERIVED payoff -- the
    first field that folds the loan's projected payments -- and the loan's
    non-balance WRITE surfaces (escrow editing, the rate-history swap, the
    recurring-payment amount) began raising the seam's ``require_scenario`` for a
    user whose baseline is missing.  Splitting the value along the dependency it
    actually has is the fix; these pin both halves of it.
    """

    def _loan(self, db, seed_user, periods):
        """A resolvable 24-month $12,000 loan at 5%, originated 2026-01-01."""
        return create_loan_account(
            seed_user, db.session, name="Terms Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 1, 1),
        )

    def _drop_baseline(self, db, seed_user):
        """Remove the user's baseline scenario -- the state baseline_service repairs."""
        # Pylint: ``import-outside-toplevel`` -- model import local to the one
        # helper that needs it, matching this suite's convention.
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
        db.session.query(Scenario).filter_by(
            user_id=seed_user["user"].id, is_baseline=True,
        ).delete()
        db.session.commit()

    def test_terms_answer_without_a_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """The payment and rate resolve with no scenario at all.

        Hand-checked: $12,000 over 24 months at 5%/12 amortizes to a level
        payment of $526.46, and the rate in effect is the origination rate.
        """
        with app.app_context():
            account = self._loan(db, seed_user, seed_periods)
            db.session.commit()
            self._drop_baseline(db, seed_user)

            ctx = BalanceContext.build(seed_user["user"].id)
            assert ctx.scenario is None
            terms = balance_at.loan_terms(account, ctx)
            assert terms is not None
            assert terms.monthly_payment == Decimal("526.46")
            assert terms.current_rate == Decimal("0.05000")
            assert terms.is_originated is True
            assert terms.is_arm is False

    def test_figures_still_fail_loud_without_a_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """The scenario-scoped bundle still refuses to answer -- deliberately.

        The split narrows the dependency; it does not soften the guard.  A payoff
        folded without the loan's projected payments would be a different loan's
        answer, so this raises rather than inventing one.
        """
        with app.app_context():
            account = self._loan(db, seed_user, seed_periods)
            db.session.commit()
            self._drop_baseline(db, seed_user)

            ctx = BalanceContext.build(seed_user["user"].id)
            with pytest.raises(ValueError, match="baseline scenario"):
                balance_at.loan_figures(account, ctx)

    def test_a_non_loan_answers_none_before_any_guard(
        self, app, db, seed_user, seed_periods,
    ):
        """Both entries keep the not-a-loan test AHEAD of the scenario guard.

        ``home_equity_service`` uses the ``None`` return as its configured-loan
        test for a user with no baseline, so an account with no ``LoanParams``
        must answer rather than raise.
        """
        with app.app_context():
            self._drop_baseline(db, seed_user)
            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_terms(seed_user["account"], ctx) is None
            assert balance_at.loan_figures(seed_user["account"], ctx) is None

    def test_figures_compose_the_same_terms(
        self, app, db, seed_user, seed_periods,
    ):
        """The wider bundle carries the SAME terms value, not a copy of its fields.

        Composition is what keeps the two from drifting: a consumer reading the
        payment off ``figures.terms`` and one reading it off ``loan_terms`` are
        reading one derivation.
        """
        with app.app_context():
            account = self._loan(db, seed_user, seed_periods)
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(account, ctx)
            terms = balance_at.loan_terms(account, ctx)
            assert figures is not None
            assert figures.terms == terms


class TestThePreTrackingEstimateReadsTheRecordedStart:
    """Ruling R-R111: the property chart's pre-tracking estimate ends at the loan's recorded start.

    A loan imported mid-life has no record before its ``tracking_start``
    assertion, so the months before it take the contract's amortized balance
    (``_secured_debt._back_projection_by_month``).  Until plan step
    recurrence:R16-c-2 the reader took the FIRST schedule row's date as the
    tracking start; ruling R-R109 dates a confirmed row by the installment its
    payment pays, so a first payment due off the loan's day put that row
    BEFORE the tracking start and the month between read the untracked
    origination principal as ``confirmed``.

    Made-up figures: ``$300,000.00`` at 6% for 360 months from 2024-01-22,
    due the 22nd, level payment ``$1,798.65``; stepped month by month with
    each month's interest rounded to the cent, installment 24 (2026-01-22)
    leaves ``$292,404.74`` and installment 25 (2026-02-22) ``$292,068.11``.
    Tracked from 2026-03-01 at ``$290,000.00``; the first payment is due and
    paid Mar 10 -- installment 25's interval.
    """

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the payment (the seed periods run through 2026-05)."""
        freeze_today(monkeypatch, date(2026, 3, 20))

    def test_an_off_day_first_payment_does_not_move_the_start(
        self, app, db, seed_user, seed_periods,
    ):
        """February stays estimated at $292,068.11 though the first row reads Feb 22."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Imported Mortgage",
                principal=Decimal("300000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2024, 1, 22), payment_day=22,
            )
            db.session.commit()
            insert_tracking_start_event(
                loan_params_for(db.session, loan.id), Decimal("290000.00"),
                date(2026, 3, 1),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[4], amount=Decimal("1798.65"),
                settled_on=date(2026, 3, 10), due_date=date(2026, 3, 10),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id, date(2026, 3, 20))
            resolved = resolved_loan(loan, ctx)
            assert resolved.recorded_start == date(2026, 3, 1)
            assert resolved.state.schedule[0].payment_date == date(2026, 2, 22)
            estimated = _back_projection_by_month(
                ctx.loan_walk(loan).stream, resolved.recorded_start,
            )
            assert (estimated[(2026, 1)], estimated[(2026, 2)]) == (
                Decimal("292404.74"), Decimal("292068.11"),
            )
            assert (2026, 3) not in estimated

    @pytest.mark.usefixtures("seed_periods")
    def test_a_loan_tracked_from_origination_estimates_nothing(
        self, app, db, seed_user,
    ):
        """No tracking start: the record starts at origination, so no month is estimated."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="In-app Mortgage",
                principal=Decimal("300000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2026, 1, 22), payment_day=22,
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id, date(2026, 3, 20))
            resolved = resolved_loan(loan, ctx)
            assert resolved.recorded_start == date(2026, 1, 22)
            assert _back_projection_by_month(
                ctx.loan_walk(loan).stream, resolved.recorded_start,
            ) == {}

    @pytest.mark.parametrize(
        ("tracked_from", "tracked_at", "trued_up_on", "estimated_months"),
        [
            # Tracked after the term's last installment (Jan 15).
            (date(2026, 2, 1), Decimal("1000.00"), None,
             [(2025, 11), (2025, 12), (2026, 1)]),
            # Tracked before Dec 15, then trued up after the term's last
            # installment: the true-up is the latest statement.
            (date(2025, 12, 1), Decimal("2500.00"), date(2026, 2, 1),
             [(2025, 11)]),
        ],
        ids=["tracked-after-the-term", "trued-up-after-the-term"],
    )
    @pytest.mark.usefixtures("seed_periods")
    def test_a_statement_past_the_term_with_no_payment_estimates_the_contract(
        self, app, db, seed_user,
        tracked_from, tracked_at, trued_up_on, estimated_months,
    ):
        """No payment and a latest statement past the term: the months before tracking are estimated.

        Made-up figures: ``$3,000.00`` at 5% over three months from
        2025-10-15, due the 15th, level payment ``$1,008.34``.  From
        origination each installment charges the balance times 0.05 / 12,
        rounded to the cent: Nov 15 ``12.50`` of interest and ``995.84`` of
        principal leave ``$2,004.16``; Dec 15 ``8.35`` and ``999.99`` leave
        ``$1,004.17``; Jan 15 closes it, ``4.18`` of interest, ``$0.00``.

        Until plan step recurrence:R16-f the estimate returned nothing for a
        loan whose resolved schedule was empty, and this loan's is: no
        payment, and no installment left after its latest statement.  So
        those months read the fold's untracked ``$3,000.00`` origination
        principal, tiered ``confirmed``.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Matured Loan",
                principal=Decimal("3000.00"), rate=Decimal("0.05000"),
                term=3, origination_date=date(2025, 10, 15), payment_day=15,
            )
            house = create_account_of_type(
                seed_user, db.session, AcctTypeEnum.PROPERTY.value, "House",
                anchor_balance=Decimal("400000.00"),
            )
            db.session.flush()
            loan.collateral_account_id = house.id
            db.session.commit()
            params = loan_params_for(db.session, loan.id)
            insert_tracking_start_event(params, tracked_at, tracked_from)
            if trued_up_on is not None:
                insert_trueup_event(params, Decimal("1000.00"), trued_up_on)
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            # The state this pins (non-vacuity): no payment is recorded, and
            # the latest statement is past the term's last installment.
            assert ctx.loan_walk(loan).settled_splits == []
            latest = trued_up_on or tracked_from
            assert latest > final_installment_date(
                date(2025, 10, 15), 15, 3,
            ) == date(2026, 1, 15)

            (series,) = balance_at.secured_loan_series(house, ctx)
            contract = {
                (2025, 11): Decimal("2004.16"),
                (2025, 12): Decimal("1004.17"),
                (2026, 1): Decimal("0.00"),
            }
            estimated = property_equity_chart.TIER_ESTIMATED
            assert {
                month: entry for month, entry in series.month_balances.items()
                if entry[1] == estimated
            } == {month: (contract[month], estimated) for month in estimated_months}


class TestNoPaymentMovesTheRecordedStart:
    """Ruling R-R114: a loan's record starts at its tracking start, whatever payments are recorded.

    Plan step recurrence:R16-c-2 built ruling R-R113 at its checkpoint 10 by
    reading the payment feed here: the record started at the ORIGINATION
    whenever a payment had settled before the tracking start.  Its review 5
    measured two ways that misfired on a loan imported mid-life -- the class
    above's mortgage, ``$300,000.00`` at 6% from 2024-01-22, due the 22nd,
    tracked from 2026-03-01 at ``$290,000.00``.  A ``$0.00`` close due Mar 10
    is dated by the installment it skips, Feb 22 (ruling R-R107), so it
    counted as recorded before the statement; a ``$1,798.65`` payment settled
    Feb 28 did too.  Either put the untracked ``$300,000.00`` in Dec, Jan and
    Feb as ``confirmed`` where the contract estimates ``$292,739.69`` /
    ``$292,404.74`` / ``$292,068.11`` (stepped month by month from the
    level payment ``$1,798.65``, each month's interest rounded to the cent).

    Ruling R-R114 ("Decide at the door") reads the statements alone and moves
    the decision to the door that adds a tracking start later, which refuses
    one dated on or before a recorded payment (``test_anchor_service.py``'s
    ``TestRecordLoanTrackingStart`` and ``test_loan/test_params.py``'s
    ``TestRecordTrackingStartRoute``).  So an owner correcting a loan kept in
    the app from its origination records a TRUE-UP, which starts nothing, and
    R-R113's own example reads the same as R-R113 built it.  That example,
    hand-checked: ``$20,000.00`` at 6% for 24 months from 2025-12-22, due the
    22nd, ``$2,000.00`` paid Jan 22, Feb 22 and Mar 22 2026.  Each month
    charges the balance times 0.005, rounded to the cent: 20,000.00 + 100.00 -
    2,000.00 = ``$18,100.00``; 18,100.00 + 90.50 - 2,000.00 = ``$16,190.50``;
    16,190.50 + 80.95 - 2,000.00 = ``$14,271.45``.  A true-up on 2026-04-01 at
    ``$15,000.00`` then corrects the balance.
    """

    @pytest.mark.parametrize(
        ("period_index", "settled_on", "due_date", "settled_amount"),
        [
            # The day before the statement, for the Mar 22 installment.
            (4, date(2026, 2, 28), date(2026, 3, 22), None),
            # The statement's own day.
            (4, date(2026, 3, 1), date(2026, 3, 22), None),
            # A $0.00 close due after the statement, dated by Feb 22's interval.
            (4, date(2026, 3, 10), date(2026, 3, 10), Decimal("0.00")),
        ],
        ids=["settled-day-before", "settled-same-day", "zero-close-due-after"],
    )
    def test_a_payment_around_the_statement_leaves_the_start(
        self, app, db, seed_user, seed_periods, monkeypatch,
        period_index, settled_on, due_date, settled_amount,
    ):
        """The record starts Mar 1 and Dec-Feb keep the contract's estimates."""
        freeze_today(monkeypatch, date(2026, 3, 25))
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Imported Mortgage",
                principal=Decimal("300000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2024, 1, 22), payment_day=22,
            )
            db.session.commit()
            insert_tracking_start_event(
                loan_params_for(db.session, loan.id), Decimal("290000.00"),
                date(2026, 3, 1),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[period_index], amount=Decimal("1798.65"),
                settled_amount=settled_amount,
                settled_on=settled_on, due_date=due_date,
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            resolved = resolved_loan(loan, ctx)

            # The loan's record, as the seam's own "has it been paid?" reads it
            # (plan task plan#3387); the payment list this read until plan task
            # plan#3388 is deleted.
            assert len(ctx.loan_walk(loan).settled_splits) == 1, (
                "precondition: the payment is recorded"
            )
            assert resolved.recorded_start == date(2026, 3, 1)
            estimated = _back_projection_by_month(
                ctx.loan_walk(loan).stream, resolved.recorded_start,
            )
            assert (
                estimated[(2025, 12)], estimated[(2026, 1)], estimated[(2026, 2)],
            ) == (
                Decimal("292739.69"), Decimal("292404.74"), Decimal("292068.11"),
            )

    @staticmethod
    def _corrected_loan(db, seed_user, seed_periods):
        """Ruling R-R113's loan with its three payments, corrected by a TRUE-UP on Apr 1."""
        loan = create_loan_account(
            seed_user, db.session, name="Kept in the app",
            principal=Decimal("20000.00"), rate=Decimal("0.06000"), term=24,
            origination_date=date(2025, 12, 22), payment_day=22,
        )
        db.session.commit()
        for period_index, paid_on in (
            (1, date(2026, 1, 22)), (3, date(2026, 2, 22)), (5, date(2026, 3, 22)),
        ):
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[period_index], amount=Decimal("2000.00"),
                settled_on=paid_on, due_date=paid_on,
            )
        db.session.commit()
        insert_trueup_event(
            loan_params_for(db.session, loan.id), Decimal("15000.00"),
            date(2026, 4, 1),
        )
        db.session.commit()
        return loan

    def test_a_corrected_loan_keeps_the_months_before_the_correction(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The record starts at 2025-12-22; Jan-Mar read the ledger, April the correction."""
        freeze_today(monkeypatch, date(2026, 4, 20))
        with app.app_context():
            loan = self._corrected_loan(db, seed_user, seed_periods)
            bctx = BalanceContext.build(seed_user["user"].id)

            recorded_start = resolved_loan(loan, bctx).recorded_start
            assert recorded_start == date(2025, 12, 22)
            assert _back_projection_by_month(
                bctx.loan_walk(loan).stream, recorded_start,
            ) == {}
            assert [
                liability_sign.owed(balance_at.balance_at(loan, bctx, day))
                for day in (
                    date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
                    date(2026, 4, 20),
                )
            ] == [
                Decimal("18100.00"), Decimal("16190.50"), Decimal("14271.45"),
                Decimal("15000.00"),
            ]

    def test_the_property_chart_reads_the_ledger_for_a_corrected_loan(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The loan's debt line holds Jan-Mar at the ledger's figures, tiered confirmed.

        Recorded as a tracking start instead (the state the door now refuses),
        the months before Apr 1 would read the contract's estimate:
        ``$19,213.59`` / ``$18,423.25`` / ``$17,628.96``, tiered ``estimated``.
        """
        freeze_today(monkeypatch, date(2026, 4, 20))
        with app.app_context():
            loan = self._corrected_loan(db, seed_user, seed_periods)
            house = create_account_of_type(
                seed_user, db.session, AcctTypeEnum.PROPERTY.value, "House",
                anchor_balance=Decimal("400000.00"),
            )
            db.session.flush()
            loan.collateral_account_id = house.id
            db.session.commit()

            (series,) = balance_at.secured_loan_series(
                house, BalanceContext.build(seed_user["user"].id),
            )

            confirmed = property_equity_chart.TIER_CONFIRMED
            assert [
                series.month_balances[month]
                for month in ((2026, 1), (2026, 2), (2026, 3), (2026, 4))
            ] == [
                (Decimal("18100.00"), confirmed),
                (Decimal("16190.50"), confirmed),
                (Decimal("14271.45"), confirmed),
                (Decimal("15000.00"), confirmed),
            ]
