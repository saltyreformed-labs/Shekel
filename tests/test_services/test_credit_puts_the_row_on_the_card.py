"""
Shekel Budget App -- Credit puts the row on the card (plan task plan#2929)

Rulings **plan#2887** and **plan#2888**: Credit stays as the way the owner says a
purchase went on the card, and the press now records it ON the card -- the row
settles with its money moved on the card from its own date
(``transaction_service.mark_as_credit``, the settle verb with the card as its
tender) and no CC Payback row is written.

**The one example**: $800 of groceries dated Jan 3, its row in the Jan 2
paycheck, on a card whose books open before Jan 2, closing on the 5th and
falling due on the 27th.  **Paydays are Jan 2, Jan 16, Jan 30, Feb 13 and Feb
27.**  Checking opens at $1,000.00.  Each figure is computed by hand in the test
that asserts it.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import credit_workflow, status_seam, transaction_service
from app.services.cash_ledger import account_opening_fact
from app.utils.dates import display_today
from tests._cards import add_card
from tests._test_helpers import add_txn, make_expense_template, generate_row_of
from tests.test_services.test_card_payment_follows_what_is_owed import _balances, _payoff
from tests.test_services.test_card_payment_price import _card

_ZERO = Decimal("0.00")
_EIGHT_HUNDRED = Decimal("800.00")
_JAN_2 = date(2026, 1, 2)
_JAN_3 = date(2026, 1, 3)
_JAN_10 = date(2026, 1, 10)
_JAN_15 = date(2026, 1, 15)
_JAN_16 = date(2026, 1, 16)


def _groceries(seed_user, period, *, due_date=_JAN_3, account=None):
    """The $800 Groceries row in *period*, dated *due_date*, committed."""
    row = add_txn(
        db.session, seed_user, period, "Groceries", _EIGHT_HUNDRED,
        due_date=due_date, account=account,
    )
    db.session.commit()
    return row


def _press(row, **kwargs):
    """Press Credit on *row* and commit; return the row read fresh."""
    transaction_service.mark_as_credit(row, **kwargs)
    db.session.commit()
    db.session.expire_all()
    return db.session.get(Transaction, row.id)


def _assert_untouched(row_id):
    """Assert the row is still Projected, holds no movement, and has no payback."""
    db.session.rollback()
    db.session.expire_all()
    row = db.session.get(Transaction, row_id)
    assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
    assert not row.entries
    assert credit_workflow.get_active_payback(row_id) is None


class TestThePressPutsTheRowOnTheCard:
    """The row settles with its money moved on the card, and nothing else."""

    def test_the_row_is_paid_on_the_card_from_its_date(self, seed_user, seed_periods):
        """$800 dated Jan 3: Paid, ONE movement of $800.00 on the card, dated Jan 3, no payback."""
        card = _card(seed_user)
        row = _press(_groceries(seed_user, seed_periods[0]))

        assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
        assert row.settled_on == _JAN_3
        assert [(m.account_id, m.amount) for m in row.entries] == [(card.id, _EIGHT_HUNDRED)]
        assert credit_workflow.get_active_payback(row.id) is None

    def test_the_card_owes_it_from_its_date_and_checking_does_not_move(
        self, seed_user, seed_periods,
    ):
        """Read Jan 10: the card $0.00 Jan 2 and -$800.00 Jan 3; checking $1,000.00 both days.

        No payoff row yet, so nothing leaves checking: 1000 - 0 = 1000.00, and
        the card owes the $800 from its own date, 0 - 800 = -800.00.
        """
        card = _card(seed_user)
        _press(_groceries(seed_user, seed_periods[0]))

        assert _balances(seed_user, card, _JAN_10, _JAN_2) == (Decimal("1000.00"), _ZERO)
        assert _balances(seed_user, card, _JAN_10, _JAN_3) == (
            Decimal("1000.00"), Decimal("-800.00"),
        )

    def test_with_the_payoff_row_checking_is_the_old_credit_to_the_cent(
        self, seed_user, seed_periods,
    ):
        """S3 of the trace, read Jan 10: checking $1,000.00 Jan 15 and $200.00 Jan 16.

        The old Credit read the same: its payback took the $800 in Jan 16's
        paycheck, 1000 - 800 = 200.00.  The payoff row pays what the card
        owes the day before (ruling R-CC22), $800, so the card reads -$800.00
        Jan 15 and -800 + 800 = $0.00 Jan 16.
        """
        card = _card(seed_user)
        _payoff(seed_user, card)
        _press(_groceries(seed_user, seed_periods[0]))

        assert _balances(seed_user, card, _JAN_10, _JAN_15) == (
            Decimal("1000.00"), Decimal("-800.00"),
        )
        assert _balances(seed_user, card, _JAN_10, _JAN_16) == (Decimal("200.00"), _ZERO)

    def test_a_second_press_changes_nothing(self, seed_user, seed_periods):
        """A double click: the same one movement, the same day, no payback."""
        _card(seed_user)
        row = _press(_groceries(seed_user, seed_periods[0]))
        movement_id = row.entries[0].id

        row = _press(row)

        assert [m.id for m in row.entries] == [movement_id]
        assert row.settled_on == _JAN_3
        assert credit_workflow.get_active_payback(row.id) is None

    def test_a_reverted_rows_kept_payment_goes_on_the_card(self, seed_user, seed_periods):
        """Paid on checking, reverted, then Credit: its one kept movement moves to the card."""
        card = _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])
        transaction_service.settle_transaction(row)
        db.session.commit()
        kept_id = row.entries[0].id
        transaction_service.apply_requested_status(
            row, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.commit()

        row = _press(row)

        assert [(m.id, m.account_id, m.amount) for m in row.entries] == [
            (kept_id, card.id, _EIGHT_HUNDRED),
        ]


class TestTheDay:
    """The row's own date; today if it has not come; its paycheck's first day if it has none."""

    def test_a_date_that_has_not_come_is_today(self, seed_user, seed_periods):
        """Dated ten days ahead: put on the card today, since money cannot move in the future."""
        _card(seed_user)
        ahead = display_today() + timedelta(days=10)
        row = _press(_groceries(seed_user, seed_periods[0], due_date=ahead))

        assert row.settled_on == display_today()

    def test_an_undated_row_takes_its_paycheck_s_first_day(self, seed_user, seed_periods):
        """No date, in Jan 16's paycheck: Jan 16, or today if that has not come."""
        _card(seed_user)
        row = _press(_groceries(seed_user, seed_periods[1], due_date=None))

        assert row.settled_on == min(_JAN_16, display_today())

    def test_an_undated_row_in_a_paycheck_not_started_is_today(self, seed_user, seed_periods):
        """No date, in the last paycheck, which has not started: put on the card today."""
        _card(seed_user)
        assert seed_periods[-1].start_date > display_today(), "precondition: not started"
        row = _press(_groceries(seed_user, seed_periods[-1], due_date=None))

        assert row.settled_on == display_today()

    def test_a_day_inside_the_cards_opening_is_refused(self, seed_user, seed_periods):
        """Dated the day the card's books open: the opening already holds it, so refused."""
        card = _card(seed_user)
        opened_on = account_opening_fact(card.id).opened_on
        row = _groceries(seed_user, seed_periods[0], due_date=opened_on)

        with pytest.raises(ValidationError, match=f"Money cannot have moved on {opened_on}"):
            transaction_service.mark_as_credit(row)
        _assert_untouched(row.id)


class TestWhichCard:
    """The owner's one card; none or two refused unnamed; a named one gated."""

    def test_with_no_card_the_press_is_refused(self, seed_user, seed_periods):
        """No credit card in the app: refused with a sentence, nothing written."""
        row = _groceries(seed_user, seed_periods[0])

        with pytest.raises(ValidationError, match="there is no credit card in the app yet"):
            transaction_service.mark_as_credit(row)
        _assert_untouched(row.id)

    def test_with_two_cards_the_press_must_name_one(self, seed_user, seed_periods):
        """Two cards and none named: refused; the second named: the $800 is on it."""
        _card(seed_user)
        second = add_card(seed_user, name="Amex")
        db.session.commit()
        row = _groceries(seed_user, seed_periods[0])

        with pytest.raises(ValidationError, match="You have 2 credit cards"):
            transaction_service.mark_as_credit(row)
        _assert_untouched(row.id)

        row = _press(db.session.get(Transaction, row.id), card_account_id=second.id)
        assert [(m.account_id, m.amount) for m in row.entries] == [(second.id, _EIGHT_HUNDRED)]

    def test_a_row_on_a_card_goes_on_its_own_card(self, seed_user, seed_periods):
        """A row living on the Amex, with two cards and none named: on the Amex."""
        _card(seed_user)
        amex = add_card(seed_user, name="Amex")
        db.session.commit()
        row = _press(_groceries(seed_user, seed_periods[0], account=amex))

        assert [m.account_id for m in row.entries] == [amex.id]

    def test_a_named_account_of_another_owner_is_not_found(
        self, seed_user, seed_second_user, seed_periods,
    ):
        """Another owner's card: not found, as "Paid from" answers it, nothing written."""
        _card(seed_user)
        theirs = add_card(seed_second_user, name="Theirs")
        db.session.commit()
        row = _groceries(seed_user, seed_periods[0])

        with pytest.raises(NotFoundError):
            transaction_service.mark_as_credit(row, card_account_id=theirs.id)
        _assert_untouched(row.id)

    @pytest.mark.parametrize("named, sentence", [
        ("checking", "is not a credit card"),
        ("an archived card", "is archived"),
    ])
    def test_a_named_account_that_is_not_an_active_card_is_refused_in_words(
        self, seed_user, seed_periods, named, sentence,
    ):
        """Checking, or the owner's archived card: refused with a sentence, nothing written."""
        _card(seed_user)
        if named == "checking":
            account_id = seed_user["account"].id
        else:
            old = add_card(seed_user, name="Old")
            old.is_active = False
            account_id = old.id
        db.session.commit()
        row = _groceries(seed_user, seed_periods[0])

        with pytest.raises(ValidationError, match=sentence):
            transaction_service.mark_as_credit(row, card_account_id=account_id)
        _assert_untouched(row.id)


class TestWhatIsAlreadyCredit:
    """An old Credit row holding its payback is a press made; a bare Credit status is not."""

    def test_a_credit_status_with_no_payback_is_refused(self, seed_user, seed_periods):
        """Status Credit, no payback (finding plan#2133's shape): refused, as the old press did."""
        _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])
        status_seam.apply_status_change(row, ref_cache.status_id(StatusEnum.CREDIT))
        db.session.commit()

        with pytest.raises(ValidationError, match="Only projected transactions"):
            transaction_service.mark_as_credit(row)
        db.session.rollback()
        assert not db.session.get(Transaction, row.id).entries


class TestTheDoor:
    """The route over the verb: the owner's press, and a companion's 404."""

    def test_the_owners_press_puts_the_row_on_the_card(
        self, auth_client, seed_user, seed_periods,
    ):
        """POST mark-credit: 200, the grid refreshed, the $800.00 on the card from Jan 3."""
        card = _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])

        response = auth_client.post(f"/transactions/{row.id}/mark-credit")

        assert response.status_code == 200
        assert response.headers["HX-Trigger"] == "gridRefresh"
        assert "800.00" in response.get_data(as_text=True)
        db.session.expire_all()
        movement = status_seam.covering_movement_of(db.session.get(Transaction, row.id))
        assert (movement.account_id, movement.amount) == (card.id, _EIGHT_HUNDRED)

    def test_a_companion_s_press_is_not_found(
        self, companion_client, seed_user, seed_periods,
    ):
        """Review J: the companion may see the row, and the press is still a 404."""
        _card(seed_user)
        template = make_expense_template(
            db.session, seed_user, amount="800.00", name="Groceries",
            category_key="Groceries", companion_visible=True,
        )
        row = generate_row_of(template, seed_periods[0])
        db.session.commit()

        response = companion_client.post(f"/transactions/{row.id}/mark-credit")

        assert response.status_code == 404
        _assert_untouched(row.id)

    def test_the_refusal_says_why_on_the_cell(self, auth_client, seed_user, seed_periods):
        """No card: a 400 cell carrying the sentence, the row unchanged."""
        row = _groceries(seed_user, seed_periods[0])

        response = auth_client.post(f"/transactions/{row.id}/mark-credit")

        assert response.status_code == 400
        assert "there is no credit card in the app yet" in response.get_data(as_text=True)
        _assert_untouched(row.id)
