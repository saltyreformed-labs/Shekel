"""A month-end loan keeps its month end, and a create may not stop before the derived start."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.models.category import Category
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.schemas.validation import end_bound_before_start_message
from app.services import loan_recurrence_sync
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, resolve
from tests._test_helpers import cadence_payload, create_loan_account, make_loan_payment_template

from ._helpers import ORIGINATION, PRINCIPAL


class TestAMonthEndLoanKeepsItsMonthEnd:
    """``loan_cadence_start`` asks the READER's question (plan step R7c-b).

    ``nominal_day`` is what stops a servicer's day-31 payment decaying to the
    30th forever when the first installment lands in a 30-day month.  Whether a
    cadence can carry one is a property of the UNIT --
    :func:`~app.services.recurrence.has_day_of_month_coordinate`, which
    :func:`~app.services.recurrence.offerable_nominal_days` and
    :attr:`ResolvedRecurrence.day_of_month` both read.

    The producer asked a DIFFERENT question: ``fires_on_day_of_month``, which
    answers whether a generated ROW is dated from a day of the month (it read
    an ANCHOR FAMILY router until plan step R8-a, which deleted it).  The two agree everywhere except ``Monthly First``, whose
    occurrences ARE days of the month even though its anchor is a paycheck --
    so under that placement the producer recorded no nominal day and the reader
    then answered the CLAMPED day.
    """

    #: ``(label, placement)``: both placements a MONTH-unit loan payment can
    #: carry.  Swept rather than sampled because the defect was visible on
    #: exactly one of them, and a case fixed on the wrong one passes.
    _PLACEMENTS = (
        ("the paycheck covering the date", PeriodPlacementEnum.CONTAINING_DATE),
        (
            "the first paycheck on or after",
            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        ),
    )

    @pytest.mark.parametrize("label,placement", _PLACEMENTS)
    def test_a_day_31_payment_records_its_nominal_day(
        self, app, seed_user, db, label, placement,  # pylint: disable=unused-argument
    ):
        """Originate 2026-03-10 with ``payment_day`` 31: April clamps it to 30.

        ``first_installment_date`` answers the ``payment_day`` of the month
        AFTER origination, so 2026-04-30 -- April's last day, and one the month
        was too short to hold 31 on.  The pair must therefore be
        ``(2026-04-30, 31)``, which is what keeps May's occurrence on the 31st.

        Under ``Monthly First`` the producer used to answer ``nominal_day
        None`` here, and the reader then said day 30 -- moving the FUNDING
        PAYCHECK a period early in every month whose schedule puts a payday on
        the 30th, on a mortgage-sized debit, for the life of the loan.
        """
        with app.app_context():
            params = SimpleNamespace(
                origination_date=date(2026, 3, 10),
                payment_day=31,
                account_id=1,
            )

            start = loan_recurrence_sync.loan_cadence_start(
                RecurrenceUnitEnum.MONTH, params,
            )

            assert start.starts_on == date(2026, 4, 30), label
            assert start.nominal_day == 31, label

    @pytest.mark.parametrize("label,placement", _PLACEMENTS)
    def test_the_reader_then_answers_the_day_the_contract_names(
        self, app, seed_user, db, label, placement,  # pylint: disable=unused-argument
    ):
        """The half that makes the pair matter: what the walk reads back.

        Asserting only the stored pair would leave this passing against a
        reader that ignored it.  ``ResolvedRecurrence.day_of_month`` is the ONE
        place ``starts_on``'s day and ``nominal_day`` are joined, and it is
        what the occurrence walk clamps into each month.
        """
        with app.app_context():
            params = SimpleNamespace(
                origination_date=date(2026, 3, 10),
                payment_day=31,
                account_id=1,
            )
            start = loan_recurrence_sync.loan_cadence_start(
                RecurrenceUnitEnum.MONTH, params,
            )

            resolved = resolve(
                RecurrenceSpec(
                    user_id=seed_user["user"].id,
                    unit=RecurrenceUnitEnum.MONTH,
                    placement=placement,
                    starts_on=start.starts_on,
                    nominal_day=start.nominal_day,
                ),
                calendar_for(seed_user["user"].id),
            )

            assert resolved.day_of_month == 31, label

    def test_a_paycheck_cadence_records_NO_nominal_day(
        self, app, seed_user, db,  # pylint: disable=unused-argument
    ):
        """The control: the drop is conditional on the unit, not general.

        A loan payment that bills by PAYCHECK has no day-of-month coordinate at
        all -- ``resolve`` normalises the installment onto the paycheck hosting
        it -- so there is no contractual day to keep, and recording one would
        be a pair ``RecurrenceSpec`` refuses at construction.  Without this arm
        a producer that recorded ``payment_day`` unconditionally would pass
        every case above.
        """
        with app.app_context():
            params = SimpleNamespace(
                origination_date=date(2026, 3, 10),
                payment_day=31,
                account_id=1,
            )

            start = loan_recurrence_sync.loan_cadence_start(
                RecurrenceUnitEnum.PERIOD, params,
            )

            assert start.nominal_day is None

    def test_a_day_the_installment_month_HELD_records_nothing(
        self, app, seed_user, db,  # pylint: disable=unused-argument
    ):
        """The second control: a day no month clamped is not a nominal day.

        ``payment_day`` 15 originating 2026-03-10 first bills 2026-04-15, which
        April holds -- so the date already says which day the rule fires on and
        a nominal day beside it would be the second representation ruling
        R-R16 removes (and one ``ck_recurrence_rules_nominal_day`` refuses).
        """
        with app.app_context():
            params = SimpleNamespace(
                origination_date=date(2026, 3, 10),
                payment_day=15,
                account_id=1,
            )

            start = loan_recurrence_sync.loan_cadence_start(
                RecurrenceUnitEnum.MONTH, params,
            )

            assert start.starts_on == date(2026, 4, 15)
            assert start.nominal_day is None


class TestALoanCreateMayNotStopBeforeTheDerivedStart:
    """A SECOND transfer's window, at the CREATE door, since ruling R-R81.

    Until plan step R7d-g-2 this class graded the route-side comparison the
    schema could not make: ``require_end_bound_after_start`` early-returns
    when ``starts_on`` is absent, a loan destination's row was locked and
    posted nothing, and the route DERIVED the start after validation -- so a
    past "Ends on" reached the write door beside a start it had never been
    compared to, and ``_refuse_bound_before_derived_start`` caught it.

    **MOVED BY RULING (R-R81)**: a second transfer's start is its OWNER's,
    the row is open and posts a date, and the schema's own comparison grades
    the pair; that route-side refusal is deleted with the derivation it
    served.  Both cases still create a SECOND transfer (the loan holds its
    standing payment first, so the stop is not refused outright, ruling
    **R-R60**), and what they pin now is that the window is graded against
    the TYPED start and the stop is stored beside it.
    """

    def _paid_loan(self, seed_user, db):
        """Return a COMMITTED loan already holding its standing payment.

        Args:
            seed_user: The owner fixture.
            db: The session fixture.

        Returns:
            The loan :class:`~app.models.account.Account`.
        """
        loan = create_loan_account(
            seed_user, db.session, origination_date=ORIGINATION,
            principal=PRINCIPAL, payment_day=1,
        )
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        return loan

    #: The start the owner types for the second transfer: after the loan's
    #: origination (2026-04-15) and its first installment (2026-05-01), so
    #: nothing but the window rule can refuse the pair.
    _TYPED_START = date(2026, 5, 15)

    def _post_a_bounded_monthly_transfer(
        self, auth_client, seed_user, db, to_account, ends_on,
    ):
        """POST /transfers with a MONTHLY cadence, the typed start, and *ends_on*.

        Args:
            auth_client: The signed-in test client.
            seed_user: The owner fixture.
            db: The session fixture.
            to_account: The destination :class:`Account`.
            ends_on: The closing date to state.

        Returns:
            The Flask response.
        """
        category = Category(
            user_id=seed_user["user"].id,
            group_name="Debt", item_name="Mortgage bound",
        )
        db.session.add(category)
        db.session.commit()

        with auth_client.application.app_context():
            monthly = cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, starts_on=self._TYPED_START,
            )
        return auth_client.post("/transfers", data={
            "name": f"To {to_account.name} bounded",
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(to_account.id),
            "amount": "1200.00",
            "category_id": str(category.id),
            "recurrence_end_mode": "on_date",
            "end_date": ends_on.isoformat(),
            **monthly,
        })

    def test_an_end_before_the_typed_start_is_refused(
        self, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """A past "Ends on" beside the owner's start, refused by the schema.

        An end of 2026-01-01 is before the typed 2026-05-15; the rule such a
        submission would author names no occurrence at all.  The message is
        asserted against the TYPED start, not the loan's first installment
        (2026-05-01): a loan create has more than one refusal that redirects
        to the same place, and only this one names the window.
        """
        loan = self._paid_loan(seed_user, db)
        rows_before = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == loan.id)
            .count()
        )

        resp = self._post_a_bounded_monthly_transfer(
            auth_client, seed_user, db, loan, date(2026, 1, 1),
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers/new")
        with auth_client.session_transaction() as sess:
            flashed = [message for _category, message in sess.get("_flashes", [])]
        assert end_bound_before_start_message(
            date(2026, 1, 1), self._TYPED_START,
        ) in flashed, flashed
        assert (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == loan.id)
            .count()
        ) == rows_before, (
            "a submission stating an impossible window must persist nothing"
        )

    def test_an_end_AFTER_it_still_saves(
        self, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The control, and it is what stops the door refusing every loan create.

        Without this arm a comparison inverted the wrong way -- or one that
        refused whenever a bound was stated at all -- would pass the refusal
        above while making a bounded second transfer into a loan uncreatable.
        The stored bound is read back: a redirect alone cannot tell a saved
        stop from a dropped one.
        """
        loan = self._paid_loan(seed_user, db)

        resp = self._post_a_bounded_monthly_transfer(
            auth_client, seed_user, db, loan, date(2030, 1, 1),
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        second = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=loan.id, name=f"To {loan.name} bounded")
            .one()
        )
        assert second.recurrence_rule.end_date == date(2030, 1, 1)
        assert second.recurrence_rule.starts_on == self._TYPED_START, (
            "a second transfer's start is its owner's (R-R81)"
        )
