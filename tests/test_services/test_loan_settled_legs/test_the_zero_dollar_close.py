"""A $0.00 close's day: the installment it skips (ruling R-BAL139), its interval's installment on a
loan due off the paycheck's day (ruling R-R107), and the read pass's visibility bound, placed on
the loan's own calendar.
"""

from datetime import date
from decimal import Decimal

from app.enums import LedgerAccountKindEnum
from app.extensions import db
from app.models.journal_entry import Posting
from app.services import loan_loaders
from app.services.loan_ledger import (
    LoanCalendars,
    confirmed_shadows_through,
    load_loan_stream,
    payment_installments,
    payment_visible_on,
    walk_loan_ledger,
)
from app.services.posting_service import _ledger_account_for
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    find_loan_ledger_account,
    loan_correction_entries_at,
    loan_params_for,
)

from ._helpers import _ORIGINATION, _PAYMENT_DAY, _DUE, _PERIOD, _CLOSED_ON, _loan


class TestAZeroDollarCloseIsDatedByTheInstallmentItSkips:
    """Ruling R-BAL139: no movement, so no day of its own -- the installment's."""

    def test_the_close_is_visible_from_its_installment_not_its_settle_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Closed 03-05 at $0.00 against the 03-01 installment: visible 03-01.

        The leg carries no record, so :func:`payment_visible_on` answers the
        installment it skips -- its interval's (ruling R-R107), which for a
        payment due on the contractual day is its own due date -- and never
        the settle day the close was stated on.  The
        walk's outcome, the confirmed bound and the installment feed all read
        that ONE day: a pass on 03-02 has seen the close, a pass on 02-28 has
        not.  The payment moved nothing, so the whole charge stands unpaid:
        cash 0.00, interest 500.00, principal -500.00.
        """
        with app.app_context():
            loan = _loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=_CLOSED_ON,
                due_date=_DUE,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            legs = loan_loaders.settled_income_legs(
                loan.id, scenario_id, options=(),
            )
            assert len(legs) == 1
            assert legs[0].record is None, "a $0.00 close carries no movement"
            assert payment_visible_on(legs[0], _ORIGINATION, _PAYMENT_DAY) == _DUE

            [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
            assert (outcome.due_date, outcome.visible_on) == (_DUE, _DUE)
            assert (outcome.cash, outcome.interest, outcome.principal) == (
                Decimal("0.00"), Decimal("500.00"), Decimal("-500.00"),
            )

            assert confirmed_shadows_through(
                loan.id, scenario_id, date(2026, 3, 2), _ORIGINATION, _PAYMENT_DAY,
            ) == legs
            assert confirmed_shadows_through(
                loan.id, scenario_id, date(2026, 2, 28), _ORIGINATION, _PAYMENT_DAY,
            ) == []

            [installment] = payment_installments(
                loan.id, scenario_id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
            )
            assert installment.dates.settled_on == _DUE

    def test_the_ledger_books_the_unpaid_charge_on_the_installment(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The split correction is keyed (loan_payment, the period, 03-01).

        R-BAL139's second half: "the ledger books the correction on that
        day".  One correction at the installment's key and none at the
        settle day's, so the posted balance grows by the unpaid charge on
        the day the loan's own split says it did: the loan's linked row
        -500.00 (principal) against the interest row +500.00.
        """
        with app.app_context():
            loan = _loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=_CLOSED_ON,
                due_date=_DUE,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            period_id = seed_periods[_PERIOD].id

            [entry] = loan_correction_entries_at(
                db.session, loan.id, scenario_id, period_id, _DUE,
            )
            interest_row = find_loan_ledger_account(
                db.session, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert dict(
                db.session.query(Posting.ledger_account_id, Posting.amount)
                .filter(Posting.journal_entry_id == entry.id)
            ) == {
                _ledger_account_for(loan.id).id: Decimal("-500.00"),
                interest_row.id: Decimal("500.00"),
            }
            assert loan_correction_entries_at(
                db.session, loan.id, scenario_id, period_id, _CLOSED_ON,
            ) == []


#: The off-day loan: $100,000.00 at 6% from 2026-01-22, due the 22nd, so its
#: first installment is 2026-02-22 and one month's charge is 500.00.
_OFF_DAY_ORIGINATION = date(2026, 1, 22)


_OFF_DAY_PAYMENT_DAY = 22


def _off_day_loan(seed_user):
    """A $100,000.00 loan at 6% from 2026-01-22, due on the 22nd."""
    loan = create_loan_account(
        seed_user, db.session, name="Off Day Loan",
        principal=Decimal("100000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=_OFF_DAY_ORIGINATION,
        payment_day=_OFF_DAY_PAYMENT_DAY,
    )
    db.session.commit()
    return loan


class TestAnOffDayZeroDollarCloseIsDatedByItsIntervalsInstallment:
    """Ruling R-R107 (amends R-BAL139): the installment it skips is its INTERVAL's.

    A payment due off the loan's contractual day belongs to the latest
    installment due on or before it -- the one its charge, its cash price and
    the plan already read (ruling R-R104) -- so a ``$0.00`` close of it is
    visible, and booked, on THAT installment's day, not on its own due date.
    """

    def test_a_close_due_mar_10_is_dated_by_the_feb_22_installment(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Due 03-10 on a loan due the 22nd, closed 03-12 at $0.00: dated 02-22.

        The latest installment on or before 03-10 is 02-22, and it is the only
        charge standing (03-22 falls after the stream's last event): 500.00 of
        interest, so cash 0.00 splits interest 500.00, principal -500.00.  The
        walk's outcome, the confirmed bound, the installment feed and the
        posted correction all read 02-22; the payment keeps 03-10 as its due
        date.  Under R-BAL139 as first built, every one of them read 03-10.
        """
        with app.app_context():
            loan = _off_day_loan(seed_user)
            due = date(2026, 3, 10)
            installment = date(2026, 2, 22)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=date(2026, 3, 12),
                due_date=due,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            [leg] = loan_loaders.settled_income_legs(
                loan.id, scenario_id, options=(),
            )
            assert leg.record is None, "a $0.00 close carries no movement"
            assert payment_visible_on(
                leg, _OFF_DAY_ORIGINATION, _OFF_DAY_PAYMENT_DAY,
            ) == installment

            [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
            assert (outcome.due_date, outcome.charge_date, outcome.visible_on) == (
                due, installment, installment,
            )
            assert (outcome.cash, outcome.interest, outcome.principal) == (
                Decimal("0.00"), Decimal("500.00"), Decimal("-500.00"),
            )

            assert confirmed_shadows_through(
                loan.id, scenario_id, installment,
                _OFF_DAY_ORIGINATION, _OFF_DAY_PAYMENT_DAY,
            ) == [leg]
            assert confirmed_shadows_through(
                loan.id, scenario_id, date(2026, 2, 21),
                _OFF_DAY_ORIGINATION, _OFF_DAY_PAYMENT_DAY,
            ) == []
            # The read pass's bound, which takes its origination and due day
            # off the loan's calendar: seen from 02-22, not before.
            [seen] = load_loan_stream(
                loan.id, scenario_id, LoanCalendars(), visible_by=installment,
            ).payments
            assert (seen.source, seen.visible_on) == (leg, installment)
            assert load_loan_stream(
                loan.id, scenario_id, LoanCalendars(),
                visible_by=date(2026, 2, 21),
            ).payments == []

            [fed] = payment_installments(
                loan.id, scenario_id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
            )
            assert (fed.dates.due_date, fed.dates.settled_on) == (
                due, installment,
            )

            period_id = seed_periods[_PERIOD].id
            [entry] = loan_correction_entries_at(
                db.session, loan.id, scenario_id, period_id, installment,
            )
            interest_row = find_loan_ledger_account(
                db.session, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert dict(
                db.session.query(Posting.ledger_account_id, Posting.amount)
                .filter(Posting.journal_entry_id == entry.id)
            ) == {
                _ledger_account_for(loan.id).id: Decimal("-500.00"),
                interest_row.id: Decimal("500.00"),
            }
            assert loan_correction_entries_at(
                db.session, loan.id, scenario_id, period_id, due,
            ) == []

    def test_a_close_due_before_the_first_installment_keeps_its_due_date(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Due 02-10, before the 02-22 first installment: it skips none, dated 02-10.

        No installment falls on or before 02-10, so there is no interval to
        date it by and no charge standing against it: it keeps its own due
        date and splits nothing (0.00 / 0.00 / 0.00).
        """
        with app.app_context():
            loan = _off_day_loan(seed_user)
            due = date(2026, 2, 10)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[2], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=date(2026, 2, 11),
                due_date=due,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            [leg] = loan_loaders.settled_income_legs(
                loan.id, scenario_id, options=(),
            )
            assert payment_visible_on(
                leg, _OFF_DAY_ORIGINATION, _OFF_DAY_PAYMENT_DAY,
            ) == due
            [outcome] = walk_loan_ledger(loan.id, scenario_id).settled_splits
            assert (outcome.visible_on, outcome.charge_date) == (due, None)
            assert (outcome.cash, outcome.interest, outcome.principal) == (
                Decimal("0.00"), Decimal("0.00"), Decimal("0.00"),
            )


class TestTheReadPassBoundReadsTheLoansCalendar:
    """The walk's visibility bound places a close on the loan's OWN calendar.

    :func:`~app.services.loan_ledger.load_loan_stream` hands the bound its
    calendar's origination and due day (plan step recurrence:R16-c-2, rulings
    R-R100 and R-R107).  The off-day close above grades both through the
    interval's installment; a close storing NO due date grades the due day a
    second way, as the fallback that dates it from its pay period.
    """

    def test_an_undated_close_enters_the_pass_on_its_installment(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An ad-hoc close with no due date in the 02-27 period: seen from 03-01.

        Its due date is the first due day on or after its period's start --
        03-01 on this loan due the 1st -- which is also that interval's
        installment, so a pass visible by 03-01 holds it and one visible by
        02-28 does not.  Read on any other due day (the 17th: 03-17) the 03-01
        pass would miss it.
        """
        with app.app_context():
            loan = _loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], amount=Decimal("1000.00"),
                settled_amount=Decimal("0.00"), settled_on=_CLOSED_ON,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            [leg] = loan_loaders.settled_income_legs(
                loan.id, scenario_id, options=(),
            )
            assert leg.due_date is None, "the close must store no due date"

            seen = load_loan_stream(
                loan.id, scenario_id, LoanCalendars(), visible_by=_DUE,
            )
            assert [event.source for event in seen.payments] == [leg]
            assert [event.visible_on for event in seen.payments] == [_DUE]
            assert load_loan_stream(
                loan.id, scenario_id, LoanCalendars(),
                visible_by=date(2026, 2, 28),
            ).payments == []
