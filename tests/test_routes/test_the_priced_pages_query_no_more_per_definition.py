"""The Recurring and savings pages load each definition's price with the definitions.

Plan step X-bp (plan#1045), task plan#2733: Review A of plan#2686, finding 2.
Both pages price every definition on the read pass's day through
``obligations_aggregator.todays_price`` (ruling plan#2693), which reads a
definition's price history (``amount_versions``) and whether it owns its price:
a transaction definition's salary link (``salary_profiles``), a transfer's
loan-payment settings (``settings``).  All three are lazy relationships, so a
query of definitions that does not load them with the rows costs the page
statements per definition, which no page did before X-bp read the price there.

Each case builds two of every definition the page prices, measures one GET,
adds six more of each and measures again: the count must not move.  The
definitions are one active expense on checking (the Recurring page's expense
section, the ``/savings`` emergency-fund floor), one active transfer from
checking into a goal's account (the transfer section, the floor and the goal's
monthly contribution) and one archived of each kind (the Archived drawer), so
every loader behind the two pages is measured.
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models.savings_goal import SavingsGoal
from tests._test_helpers import (
    capture_sql_statements,
    create_savings_account,
    make_expense_template,
    make_transfer_template,
)


def _add_definitions(seed_user, savings, first, count):
    """Add *count* of each definition the two pages price, numbered from *first*.

    Committed, so every instance is expired and the page loads it afresh.

    Args:
        seed_user: The ``seed_user`` fixture's dict.
        savings: The goal's account each transfer pays into.
        first: The number the first added definitions are named with.
        count: How many of each kind to add.
    """
    for number in range(first, first + count):
        make_expense_template(
            db.session, seed_user, amount="100.00", name=f"Bill {number}",
        )
        make_expense_template(
            db.session, seed_user, amount="100.00", is_active=False,
            name=f"Old Bill {number}",
        )
        for prefix, active in (("", True), ("Old ", False)):
            transfer = make_transfer_template(
                db.session, seed_user, savings, amount="50.00",
            )
            # The builder names every transfer "To Savings", which is unique
            # per owner, so each is renamed before the next is flushed.
            transfer.name = f"{prefix}To Savings {number}"
            transfer.is_active = active
            db.session.flush()
    db.session.commit()


def _statements_for(auth_client, path):
    """How many SQL statements one GET of *path* runs, the page rendered."""
    resp, statements = capture_sql_statements(lambda: auth_client.get(path))
    assert resp.status_code == 200, (path, resp.status_code)
    return len(statements)


@pytest.mark.usefixtures("seed_periods_today")
@pytest.mark.parametrize("path", ["/templates", "/savings"])
def test_the_page_runs_no_more_statements_for_more_definitions(
    app, auth_client, seed_user, path,
):
    """Two of each definition and eight of each: the same statement count."""
    with app.app_context():
        savings = create_savings_account(
            seed_user, db.session, "Goal Savings", Decimal("0.00"),
        )
        db.session.add(SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=savings.id,
            name="Cushion",
            target_amount=Decimal("5000.00"),
        ))
        _add_definitions(seed_user, savings, first=1, count=2)
        # The first request's one-time reads (the session's user, the
        # reference caches) are not the page's, so neither count holds them.
        assert auth_client.get(path).status_code == 200

        two = _statements_for(auth_client, path)
        _add_definitions(seed_user, savings, first=3, count=6)
        eight = _statements_for(auth_client, path)

        assert eight == two, (
            f"{path} ran {two} statements with two of each definition and "
            f"{eight} with eight"
        )
