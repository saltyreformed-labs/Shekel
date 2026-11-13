"""A credit card for a test to put a row on (plan task plan#2929).

A Credit press puts the row on the owner's card, so every test of the press
needs one.  Many files build their own (``_card`` in a dozen of them); this is
the one the Credit press's tests share.  A card with its one payment, as the
create door makes it (plan task plan#2933): through the door itself
(:func:`add_card_by_its_door`), or by its acts on a calendar fixed in the past
(:func:`add_card_with_its_payoff`).
And the one way left to keep a BILL's payment on a card under a row that is
not the card's (:func:`move_definition_to_checking`).  A card owing $0.00 with
one charge put on it this paycheck, and its payoff row in each paycheck from
this one on (:func:`payoffs_around_a_charge`).
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.user import UserSettings
from app.services import card_payoff, recurrence_engine
from app.services.account_resolver import resolve_grid_account
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import (
    create_account_of_type,
    current_pay_period,
    derived_span,
    one_off_row_of,
)


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


def add_card_by_its_door(
    auth_client, seed_user, *, owed_on: date, name: str = "Visa",
) -> Account:
    """POST the create form for a card owing nothing on *owed_on*; return the card it made.

    The door makes the card's one payment with it (plan task plan#2933) on its
    read pass for today, so the payment repeats from the paycheck holding
    today.  *owed_on* is the day the $0.00 owed is true, which is the day its
    books open too: a purchase put on the card after it is owed, and one dated
    on or before it is inside that $0.00.

    Args:
        auth_client: The owner's logged-in client.
        seed_user: The ``seed_user`` fixture dict (the owner).
        owed_on: The form's "balance as of" day.
        name: The card's name.

    Returns:
        The card :class:`~app.models.account.Account`.
    """
    card_type = db.session.query(AccountType).filter_by(
        name="Credit Card", user_id=None,
    ).one()
    resp = auth_client.post("/accounts", data={
        "name": name,
        "account_type_id": card_type.id,
        "anchor_balance": "0.00",
        "observed_on": owed_on.isoformat(),
    }, follow_redirects=True)
    assert f"Account &#39;{name}&#39; created." in resp.get_data(as_text=True)
    return db.session.query(Account).filter_by(
        user_id=seed_user["user"].id, name=name,
    ).one()


def add_card_with_its_payoff(
    seed_user, name: str = "Visa", *, added_on: date,
) -> Account:
    """Return a new card of the owner's, owing nothing, with its one payment made as of *added_on*.

    **The create door's three acts, in its order** (``routes/accounts/crud.
    create_account``, plan task plan#2933): the payment settled against the
    owner's primary before the card is written
    (``card_payoff.plan_payoff``), the card, then the payment made on a read
    pass (``card_payoff.make_payoff``).  The pass is held at *added_on*
    because a test's calendar is fixed -- ``seed_periods`` opens on Jan 2 2026
    -- while the door's pass reads today, so the day the card is added is the
    test's to state.  Its books open before any day a fixture could date a
    row (:func:`add_card`), so its first row is the payday of the paycheck
    holding *added_on*, priced at what the card owes the day before.

    Args:
        seed_user: The ``seed_user`` fixture dict (the owner); its Checking is
            the primary that pays the card.
        name: The card's name.
        added_on: The day the card is added: its payment repeats from the
            paycheck holding it (ruling plan#2669).

    Returns:
        The card :class:`~app.models.account.Account`, its payment and rows
        flushed.
    """
    user_id = seed_user["user"].id
    payoff = card_payoff.plan_payoff(
        user_id, name,
        resolve_grid_account(
            user_id,
            db.session.query(UserSettings).filter_by(user_id=user_id).one(),
        ),
    )
    card = add_card(seed_user, name)
    card_payoff.make_payoff(
        card, payoff, BalanceContext.build(user_id, as_of=added_on),
    )
    return card


def payoffs_around_a_charge(
    auth_client, seed_user, seed_periods_today,
) -> "tuple[Account, list[Transfer]]":
    """Add the Visa owing $0.00 and put $75.00 on it this paycheck; return it and its payoff rows.

    Moved out of ``tests/test_routes/test_grid/test_a_payoff_with_nothing_to_pay.py``
    (task plan#3699), whose example it is: the Visa is added by its door owing
    $0.00 the day before this paycheck began, and a $75.00 Restaurant row in
    this paycheck goes on it by Credit.  So this paycheck's payoff pays the
    $0.00 owed the day before it began, the next paycheck's the $75.00, and
    every one after $0.00 again.

    Args:
        auth_client: The owner's logged-in client.
        seed_user: The ``seed_user`` fixture dict (the owner).
        seed_periods_today: The ``seed_periods_today`` fixture's periods.

    Returns:
        ``(card, rows)``: the card, and its payoff transfer in each paycheck
        from this one on, in paycheck order.
    """
    current = current_pay_period(seed_user["user"].id)
    card = add_card_by_its_door(
        auth_client, seed_user, owed_on=current.start_date - timedelta(days=1),
    )
    restaurant = one_off_row_of(
        current,
        name="Restaurant",
        amount=Decimal("75.00"),
        user_id=current.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.commit()
    assert auth_client.post(
        f"/transactions/{restaurant.id}/mark-credit",
    ).status_code == 200

    template = db.session.query(TransferTemplate).filter_by(
        to_account_id=card.id,
    ).one()
    row_in = {
        row.pay_period_id: row
        for row in db.session.query(Transfer).filter_by(
            transfer_template_id=template.id,
        )
    }
    index = derived_span(current).period_index
    from_now = sorted(
        (period for period in seed_periods_today
         if derived_span(period).period_index >= index),
        key=lambda period: derived_span(period).period_index,
    )
    return card, [row_in[period.id] for period in from_now]


def add_savings(seed_user, name: str = "Savings", *, observed_on=None) -> Account:
    """Return a new active Savings account of the owner's: where a bill paid from Checking lives.

    **Why a test of a payment kept elsewhere plans its bill on Savings**
    (ruling plan#3104, developer 2026-11-09, whose option text moved those
    tests "to a second checking or savings account, where it still works"):
    a revert of a row put on the card takes its payment home now, and "Paid
    from" names the row's own account or another account of the owner's
    that holds cash, never a card (ruling plan#3227,
    ``movement_account.admitted_movement_account_id``).  So a bill reopened
    with its payment on another account is paid from one cash account and
    planned on another: here, planned on Savings and paid from Checking.

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
