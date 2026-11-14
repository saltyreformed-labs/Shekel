"""A loan's terms name the contract's LAST installment: the term-th from origination.

Plan step recurrence:R16-f (the root-cause check on plan#1249).  The loan's
:class:`~app.services.loan_ledger.LoanCalendar` carries the note's term and
the amount it lent, built from ``LoanParams`` by the one builder
(``loan_ledger._calendars.build_loan_calendar``), so the contract's last
installment is
:func:`~app.services.installment_calendar.final_installment_date` over the
calendar's own terms: the term-th date the one calendar rule lists.  Before
that step only the contractual projection's own walk could name it.

The pure cases (a due day of the 31st, a term ending in February, a one-month
term, the agreement with ``installment_dates``) are in
``test_loan_contract_calendar.py``.  This is the case a STORED loan adds: a
mid-life import, whose tracking starts long after its note began, read
through the read pass's memo the walk and the pricer share.
"""

from datetime import date
from decimal import Decimal

from app.services.installment_calendar import final_installment_date
from app.services.loan_ledger import LoanCalendars
from tests._test_helpers import create_loan_account


class TestAMidLifeImportKeepsItsNotesLastInstallment:
    """The term is the note's, counted from origination, whatever day tracking started."""

    def test_the_last_installment_is_the_term_th_from_origination(
        self, app, db, seed_user,
    ):
        """Originated Dec 31, 2024, due the 31st, 27 months; tracked from Jun 15, 2026.

        The first installment is Jan 31, 2025, and the 27th is Mar 31, 2027
        (January 2025 plus 26 months), across three Februaries, each clamped
        to the 28th.  Counted from the tracking start instead, the 27th
        installment after Jun 15, 2026 would be Aug 31, 2028; stepped a month
        at a time from the clamped Feb 28, 2027, it would be Mar 28.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Imported Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.06000"),
                term=27, origination_date=date(2024, 12, 31), payment_day=31,
                tracked_balance=Decimal("6000.00"),
                tracked_from=date(2026, 6, 15),
            )

            calendar = LoanCalendars().loan_calendar_of(loan.id)

            assert (calendar.term_months, calendar.original_principal) == (
                27, Decimal("12000.00"),
            )
            assert final_installment_date(
                calendar.origination_date, calendar.payment_day,
                calendar.term_months,
            ) == date(2027, 3, 31)
