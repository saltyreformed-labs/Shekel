"""A credit card for a test to put a row on (plan task plan#2929).

A Credit press puts the row on the owner's card, so every test of the press
needs one.  Many files build their own (``_card`` in a dozen of them); this is
the one the Credit press's tests share.  And the one way left to keep a BILL's
payment on a card under a row that is not the card's
(:func:`move_definition_to_checking`).
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.services import recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import create_account_of_type


def add_card(seed_user, name: str = "Visa") -> Account:
    """Return a new active credit card of the ``seed_user`` owner's, owing nothing.

    Its books open before any day a fixture could date a row
    (``create_account_of_type``), so a press on a past row is not refused
    as dated inside its opening.

    Args:
        seed_user: The ``seed_user`` fixture dict (the owner).
        name: The card's name.

    Returns:
        The card :class:`~app.models.account.Account`, flushed.
    """
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name, anchor_balance=Decimal("0.00"),
    )


def add_savings(seed_user, name: str = "Savings", *, observed_on=None) -> Account:
    """Return a new active Savings account of the owner's: where a bill paid from Checking lives.

    **Why a test of a payment kept elsewhere plans its bill on Savings**
    (ruling plan#3104, developer 2026-11-09, whose option text moved those
    tests "to a second checking or savings account, where it still works"):
    a revert of a row put on the card takes its payment home now, and a
    payment may name only the row's own account, the owner's primary Checking
    or a card (``movement_account.admitted_movement_account_id``).  So a bill
    reopened with its payment on another account is one planned on Savings
    and paid from Checking.

    Args:
        seed_user: The ``seed_user`` fixture dict (the owner).
        name: The account's name.
        observed_on: Its opening assertion's day; the day before today by
            default (``create_account_of_type``).

    Returns:
        The Savings :class:`~app.models.account.Account`, flushed.
    """
    return create_account_of_type(
        seed_user, db.session, "Savings", name,
        anchor_balance=Decimal("100.00"), observed_on=observed_on,
    )


def move_definition_to_checking(seed_user, txn: Transaction) -> None:
    """Move *txn*'s definition onto the owner's Checking through the maintain pass, and commit.

    **The one way left to keep a bill's payment on a card under a row that is
    not the card's** (ruling plan#3104, finding plan#3105).  A refund received
    into the card and reopened keeps its money there too, but it is income and
    never drawn Credit.  A revert of a row put on the card takes its payment
    home (``status_seam._covering._move_home``), but
    a bill that LIVES on the card keeps its payment there when reopened, and
    the maintain pass then moves the row with its definition while the payment
    stays where its money moved (ruling R-CC42's moved-definition clause).
    The pass is the door that moves a row's account in production
    (``recurrence_engine.regenerate_for_template``, which the template edit
    runs); it is run over *txn*'s own pay period alone, so it makes no row in
    another.

    Args:
        seed_user: The ``seed_user`` fixture dict (the owner).
        txn: A generated row, Projected, its definition's account a card.
    """
    template = txn.template
    template.account_id = seed_user["account"].id
    recurrence_engine.regenerate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id), {txn.pay_period_id},
        ),
        seed_user["scenario"].id,
    )
    db.session.commit()
