"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_match/test_books_bound.py``, moved verbatim by
``tools/split``.
"""

from datetime import timedelta
from decimal import Decimal

from app.enums import AccountOpeningSourceEnum
from app.services import account_service, opening_service


def _books_open_on(db, seed_user, day, equity="1000.00"):
    """Restate the seeded account's books onto *day*, through the ONE writer.

    Args:
        db: The test ``db`` fixture.
        seed_user: The seeded user bundle.
        day: The civil day the books should open on.
        equity: What they open holding.

    Returns:
        *day*, so a caller can build lines around it in one expression.

    Note:
        The WRITER, not the door: ``stage_account_opening`` appends without
        bounding the day (``opening_service`` states that in as many words),
        so a caller in this package is responsible for choosing a day the
        door would take.  Every day the package passes is one the account's
        own records allow.
    """
    opening_service.stage_account_opening(
        account=seed_user["account"],
        opening=opening_service.BooksOpening(
            opened_on=day, equity=Decimal(equity),
        ),
        source=AccountOpeningSourceEnum.USER_DECLARED,
    )
    db.session.flush()
    return day


def _the_calendars_first_day(db, seed_user):
    """Open the books ON the pay calendar's first day -- production's shape.

    Args:
        db: The test ``db`` fixture.
        seed_user: The seeded user bundle.

    Returns:
        That day.
    """
    return _books_open_on(
        db, seed_user, seed_user["bootstrap_period"].start_date,
    )


def _an_account_opening_inside_the_calendar(db, seed_user, days_in):
    """Return an account whose books open *days_in* days after the calendar.

    **Account 10's production shape**: Fidelity Money Market Savings opens
    2026-04-05 against a pay calendar that opens 2026-03-26, so ten whole days
    sit inside the calendar and inside the books at once.  The seeded checking
    account cannot be restated into that shape -- its origination assertion is
    on the calendar's first day and the books may never open after an
    assertion (:func:`~app.services.cash_ledger
    .reject_books_open_after_an_assertion`) -- so the account is created with
    a later ``observed_on`` instead, which is how the real one got there.

    Args:
        db: The test ``db`` fixture.
        seed_user: The seeded user bundle.
        days_in: How far into the calendar the books open.

    Returns:
        ``(account, opened_on)``.
    """
    opened_on = (
        seed_user["bootstrap_period"].start_date + timedelta(days=days_in)
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Fidelity Money Market Savings",
            anchor_balance=Decimal("1000.00"),
            observed_on=opened_on,
        ),
    )
    db.session.flush()
    return account, opened_on
