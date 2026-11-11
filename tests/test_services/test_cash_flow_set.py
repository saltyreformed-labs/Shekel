"""
Shekel Budget App -- the cash-flow set: checking and its cards (plan step credit_card:CC-4-1)

The predicate developer ruling ``credit_card:R-CC16`` names -- the owner's
primary grid account plus their active revolving accounts, read as ONE SET by
every plan-item reader with the balance line still ONE account's -- and the
rule ruling ``credit_card:R-CC23`` adds to it: a transfer between two
members shows once, from the balance line's side.

Three subjects, each its own class: the resolver
(:func:`~app.services.account_resolver.resolve_cash_flow_set`), the ONE row
clause (:func:`~app.services.cash_flow_set.own_rows_clause`: every member's
own rows and no shadow, since leaf ``balance:X-bi-6-1b`` the only row clause
-- R-CC23's far leg is the LEG spelling's, ``leg_accounts_shown``, graded in
``test_transfer_legs/test_the_grids_legs.py``), and the orthogonal
``revolving`` filter the set is built on.
The set decides which rows the grid draws, not what its totals count (ruling
plan#2926, plan task plan#2932): the grid's totals, the balance account's own,
are graded in ``test_balance_at/test_cash_flow.py``
(``TestTheSubtotalsAreTheBalanceAccountsOwn``, ``TestACardPurchaseCountsOnce``);
the grid's render in ``test_routes/test_grid/test_accounts_and_cards.py``.
The far-leg query the seam's composed subtotal read went with that subtotal.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import UserSettings
from app.services import account_service
from app.services.account_resolver import (
    resolve_cash_flow_set,
    resolve_grid_account,
)
from app.services.cash_flow_set import (
    CashFlowSet,
    own_rows_clause,
)
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_savings_account,
    one_off_row_of,
)


def _card(seed_user, name="Rewards Card", **kwargs):
    """Create an active Credit Card account for *seed_user*."""
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"), **kwargs,
    )


def _settings(seed_user):
    """Return the owner's settings row."""
    return db.session.query(UserSettings).filter_by(
        user_id=seed_user["user"].id,
    ).one()


def _expense_row(seed_user, period, account, name, amount):
    """Place a projected one-off EXPENSE row on *account* in *period*."""
    return one_off_row_of(
        period, name=name, amount=Decimal(amount),
        user_id=seed_user["user"].id, account_id=account.id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )


def _loaded_ids(cash_flow, scenario_id):
    """Return the ids the own-rows clause loads for *cash_flow*."""
    return {
        row.id for row in
        db.session.query(Transaction)
        .filter(
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            own_rows_clause(cash_flow),
        )
        .all()
    }


class TestResolveCashFlowSet:
    """The set is the primary plus the owner's active cards; the balance line is one member."""

    @pytest.mark.usefixtures("seed_periods_today")
    def test_an_owner_with_no_card_has_a_set_of_one(self, app, seed_user):
        """The pre-card shape: the primary alone, and it is the balance line.

        Every owner before the credit-card arc's release, and the whole of
        production on 2026-09-18 -- which is why the grid of such an owner is
        byte-identical to the one before this step.
        """
        with app.app_context():
            cash_flow = resolve_cash_flow_set(seed_user["user"].id)
            checking = seed_user["account"]
            assert cash_flow.balance.id == checking.id
            assert cash_flow.member_ids == (checking.id,)

    @pytest.mark.usefixtures("seed_periods_today")
    def test_active_cards_join_the_set_after_the_primary_in_picker_order(
        self, app, db, seed_user,
    ):
        """Two cards, ordered by ``(sort_order, id)``; an inactive one and a loan stay out."""
        with app.app_context():
            checking = seed_user["account"]
            second = _card(seed_user, "Second Card")
            first = _card(seed_user, "First Card")
            first.sort_order = -1
            closed = _card(seed_user, "Closed Card")
            closed.is_active = False
            create_loan_account(
                seed_user, db.session, name="Van Loan",
                principal=Decimal("15000.00"), rate=Decimal("0.05000"),
                term=60,
            )
            db.session.commit()

            cash_flow = resolve_cash_flow_set(seed_user["user"].id)

            assert cash_flow.balance.id == checking.id
            assert cash_flow.member_ids == (checking.id, first.id, second.id)

    @pytest.mark.usefixtures("seed_periods_today", "bare_periods")
    def test_another_owners_card_is_not_a_member(
        self, app, db, seed_user, bare_user,
    ):
        """The set is owner-scoped: a card the other user holds never joins it."""
        with app.app_context():
            other = dict(seed_user)
            other["user"] = bare_user["user"]
            mine = _card(seed_user, "My Card")
            theirs = create_account_of_type(
                other, db.session, "Credit Card", "Their Card",
                anchor_balance=Decimal("-10.00"),
            )
            db.session.commit()

            cash_flow = resolve_cash_flow_set(seed_user["user"].id)

            assert mine.id in cash_flow.member_ids
            assert theirs.id not in cash_flow.member_ids

    @pytest.mark.usefixtures("seed_periods_today")
    def test_the_primary_is_the_grid_resolvers_no_override_answer(
        self, app, db, seed_user,
    ):
        """``default_grid_account_id`` names the primary, as it names the grid account.

        ONE definition of the primary for every reader: the set's first
        member is exactly ``resolve_grid_account(user_id, settings)``.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            card = _card(seed_user)
            settings = _settings(seed_user)
            settings.default_grid_account_id = savings.id
            db.session.commit()

            cash_flow = resolve_cash_flow_set(seed_user["user"].id, settings)

            assert cash_flow.balance.id == savings.id
            assert cash_flow.balance.id == resolve_grid_account(
                seed_user["user"].id, settings,
            ).id
            assert cash_flow.member_ids == (savings.id, card.id)

    @pytest.mark.usefixtures("seed_periods_today")
    def test_an_override_naming_a_member_moves_the_balance_line_only(
        self, app, db, seed_user,
    ):
        """``?account_id=<card>``: the card's balance, the same paycheck's rows."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            db.session.commit()

            cash_flow = resolve_cash_flow_set(
                seed_user["user"].id, None, card.id,
            )

            assert cash_flow.balance.id == card.id
            assert cash_flow.member_ids == (checking.id, card.id)

    @pytest.mark.usefixtures("seed_periods_today")
    def test_an_override_outside_the_set_is_that_accounts_single_account_view(
        self, app, db, seed_user,
    ):
        """``?account_id=<savings>`` keeps the savings grid exactly as it was.

        The set collapses to that one account: no card row on the savings
        grid, no "On other accounts" term, no chip.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            _card(seed_user)
            db.session.commit()

            cash_flow = resolve_cash_flow_set(
                seed_user["user"].id, None, savings.id,
            )

            assert cash_flow.balance.id == savings.id
            assert cash_flow.member_ids == (savings.id,)

    @pytest.mark.usefixtures("seed_periods_today", "bare_periods")
    def test_a_refused_override_falls_through_to_the_primary(
        self, app, db, seed_user, bare_user,
    ):
        """Another owner's account, a loan, an id no account holds: the primary's set.

        Each refused override falls through to the set the grid always
        resolved with no override.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            other = dict(seed_user)
            other["user"] = bare_user["user"]
            theirs = create_account_of_type(
                other, db.session, "Checking", "Their Checking",
                anchor_balance=Decimal("10.00"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Van Loan",
                principal=Decimal("15000.00"), rate=Decimal("0.05000"),
                term=60,
            )
            db.session.commit()

            for refused in (theirs.id, loan.id, 999_999):
                cash_flow = resolve_cash_flow_set(
                    seed_user["user"].id, None, refused,
                )
                assert cash_flow.balance.id == checking.id, refused
                assert cash_flow.member_ids == (checking.id, card.id), refused

    @pytest.mark.usefixtures("bare_periods")
    def test_an_owner_whose_only_account_is_a_card_gets_a_set_of_that_card(
        self, app, db, bare_user,
    ):
        """The primary can itself be revolving (the resolver's step 4); it is not listed twice."""
        with app.app_context():
            card = create_account_of_type(
                bare_user, db.session, "Credit Card", "Only Card",
                anchor_balance=Decimal("-1.00"),
            )
            db.session.commit()

            cash_flow = resolve_cash_flow_set(bare_user["user"].id)

            assert cash_flow.balance.id == card.id
            assert cash_flow.member_ids == (card.id,)

    def test_no_eligible_account_answers_none(self, app, bare_user):
        """The zero-accounts owner: ``None``, the state the grid renders empty for."""
        with app.app_context():
            assert resolve_cash_flow_set(bare_user["user"].id) is None

    def test_the_value_refuses_a_balance_line_outside_its_members(self, app):
        """The type's one invariant is checked at construction, not trusted."""
        with app.app_context():
            balance = Account(id=1, name="a")
            member = Account(id=2, name="b")
            with pytest.raises(ValueError, match="must be a member"):
                CashFlowSet(balance=balance, members=(member,))


class TestOwnRowsClause:
    """The ONE clause every plan-item reader appends for its ROWS (R-CC16).

    Every member's own rows and no shadow.  It was ``paycheck_rows_clause``
    -- the same member filter, keeping the NEAR shadow of a transfer as a row
    and dropping the far one (R-CC23) -- until leaf ``balance:X-bi-6-1b``
    left that clause with no reader: every reader draws a transfer as a LEG
    of its parent now, and R-CC23 is the leg spelling's
    (``leg_accounts_shown``, graded case for case in
    ``test_transfer_legs/test_the_grids_legs.py``:
    both endpoints members from either balance line, one endpoint in the set,
    both in and the line on neither).  What is left to grade here is the row
    half.  No shadow can be a row on either side because the database refuses
    one (ruling R-BAL258); the CHECK's own cases grade that refusal, and this
    class's copy of it was deleted under ruling **R-BAL397**.
    """

    def test_every_members_rows_load_and_an_outsiders_do_not(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A card bill is a paycheck row; a savings row is not."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            period = seed_periods_today[3]
            grocery = _expense_row(seed_user, period, checking, "Grocery", "500.00")
            phone = _expense_row(seed_user, period, card, "Phone", "45.00")
            goal = _expense_row(seed_user, period, savings, "Goal", "20.00")
            db.session.commit()

            loaded = _loaded_ids(
                resolve_cash_flow_set(seed_user["user"].id),
                seed_user["scenario"].id,
            )

            assert {grocery.id, phone.id} <= loaded
            assert goal.id not in loaded

    def test_a_one_member_set_is_the_old_single_account_filter(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No card: exactly ``account_id == checking`` over the own rows.

        A savings row sits beside the checking one, so a clause that dropped
        its member filter would load a row the single-account filter does
        not; with no row off checking in the scenario, the equality held
        whatever the clause filtered on.  The filter it is compared with
        also read ``transfer_id IS NULL`` until plan step
        ``balance:X-bi-6-4d-2``: no row could name a transfer
        (``ck_transactions_names_no_transfer``, ruling **R-BAL258**, until
        plan step balance:X-bi-6-5 dropped the column), so that term held on
        every row and is gone.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            period = seed_periods_today[3]
            grocery = _expense_row(seed_user, period, checking, "Grocery", "500.00")
            _expense_row(seed_user, period, savings, "Goal", "20.00")
            db.session.commit()
            cash_flow = resolve_cash_flow_set(seed_user["user"].id)
            assert cash_flow.member_ids == (checking.id,)

            loaded = _loaded_ids(cash_flow, seed_user["scenario"].id)
            expected = {
                row.id for row in
                db.session.query(Transaction).filter(
                    Transaction.scenario_id == seed_user["scenario"].id,
                    Transaction.is_deleted.is_(False),
                    Transaction.account_id == checking.id,
                ).all()
            }

            assert grocery.id in loaded
            assert loaded == expected


class TestRevolvingFilter:
    """``active_accounts_query``'s orthogonal ``revolving`` value (design 3.8)."""

    @pytest.mark.usefixtures("seed_periods_today")
    def test_true_false_and_none_partition_the_active_accounts(
        self, app, db, seed_user,
    ):
        """``True`` is the active card, ``False`` the checking account, ``None`` both.

        ``None`` is the default, and the inactive card is in none of the three.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            card = _card(seed_user)
            closed = _card(seed_user, "Closed Card")
            closed.is_active = False
            db.session.commit()

            def ids(**kwargs):
                return {
                    a.id for a in account_service.active_accounts_query(
                        user_id, amortizing=False, **kwargs,
                    ).all()
                }

            assert ids(revolving=True) == {card.id}
            assert ids(revolving=False) == {checking.id}
            assert ids() == ids(revolving=None) == {checking.id, card.id}
