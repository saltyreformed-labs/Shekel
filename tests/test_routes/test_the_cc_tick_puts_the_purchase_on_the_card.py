"""Plan task plan#2930: the CC tick on a new purchase puts it on the card.

Ruling plan#2888 (developer 2026-11-04) and the card's re-plan: "the tick stays
the one control, with a pick only for two or more cards".  Ticked, the add door
files the purchase ON the owner's card (``entry_service._details.
purchase_account_id``, through ``movement_account.the_card``, the answer the
row's Credit press asks too), dated the day it was bought as every swipe in
flight is, and writes no CC Payback: checking pays the card through the card's
payoff row, so a payback would pay it twice.  The add door writes no CC flag
(the edit door's tick is plan#3142).

Each case posts what the add form draws (``grid/_transaction_entries.html``):
the tick posts ``is_credit=true`` (loaded as ``on_card``) and the pick
``card_account_id``.
"""

from __future__ import annotations

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, transaction_service
from app.utils.dates import display_today
from tests._cards import add_card
from tests._test_helpers import generate_row_of, make_expense_template, planted_basis


def _envelope(seed_user, period, amount="800.00", account=None):
    """A Projected Groceries envelope of *amount* in *period*, on *account* or checking."""
    template = make_expense_template(
        db.session, seed_user, amount=amount, name="Groceries",
        category_key="Groceries", is_envelope=True,
        **({"account": account} if account is not None else {}),
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _add_form(auth_client, row):
    """Return the purchase list fragment for *row*, add form and all."""
    response = auth_client.get(f"/transactions/{row.id}/entries")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _post(auth_client, row, **fields):
    """POST a Kroger purchase bought today on *row* ($120.00 unless *fields* say), with *fields* beside it."""
    return auth_client.post(
        f"/transactions/{row.id}/entries",
        data={
            "amount": "120.00",
            "direction": "charge",
            "description": "Kroger",
            "purchased_on": display_today().isoformat(),
            **fields,
        },
    )


def _purchases(row_id):
    """The purchases stored under *row_id*, straight off the table."""
    return db.session.query(TransactionEntry).filter_by(
        transaction_id=row_id,
    ).all()


def _paybacks(row_id):
    """The CC Payback rows naming *row_id*."""
    return db.session.query(Transaction).filter_by(
        credit_payback_for_id=row_id,
    ).all()


class TestTheFormDrawsTheTickWhereItMeansACard:
    """``movement_account.tick_cards``: no card no tick, one card the tick, two a pick."""

    def test_with_no_card_there_is_no_tick(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An owner with no card sees no CC control: the door could only refuse it."""
        with app.app_context():
            row = _envelope(seed_user, seed_periods[0])
            body = _add_form(auth_client, row)
            assert 'name="is_credit"' not in body
            assert 'name="card_account_id"' not in body

    def test_with_one_card_the_tick_names_it_and_there_is_no_pick(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """One card: the tick alone, its title naming the card."""
        with app.app_context():
            add_card(seed_user, "Visa")
            row = _envelope(seed_user, seed_periods[0])
            body = _add_form(auth_client, row)
            assert 'name="is_credit"' in body
            assert 'title="Bought on Visa"' in body
            assert 'name="card_account_id"' not in body

    def test_with_two_cards_the_tick_has_a_pick_of_both(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Two cards: the tick and a "Which card" pick holding each, none chosen."""
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            amex = add_card(seed_user, "Amex")
            row = _envelope(seed_user, seed_periods[0])
            body = _add_form(auth_client, row)
            assert 'name="is_credit"' in body
            assert 'name="card_account_id"' in body
            assert '<option value="" selected>Which card</option>' in body
            assert f'<option value="{visa.id}">Visa</option>' in body
            assert f'<option value="{amex.id}">Amex</option>' in body

    def test_a_row_on_a_card_has_no_tick(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An envelope that lives on the card files every purchase there already."""
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            row = _envelope(seed_user, seed_periods[0], account=visa)
            body = _add_form(auth_client, row)
            assert 'name="is_credit"' not in body


class TestTheTickFilesThePurchaseOnTheCard:
    """The add door with the tick: on the card, no payback, no flag."""

    def test_one_card_takes_the_ticked_purchase_and_no_payback_is_made(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The $120.00 is on the Visa, unflagged, and the list chips the Visa."""
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            row = _envelope(seed_user, seed_periods[0])

            response = _post(auth_client, row, is_credit="true")

            assert response.status_code == 200
            [entry] = _purchases(row.id)
            assert entry.account_id == visa.id
            assert entry.is_credit is False
            assert entry.credit_payback_id is None
            assert _paybacks(row.id) == []
            assert 'title="On Visa"' in response.get_data(as_text=True)

    def test_two_cards_take_the_picked_one(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The pick names which card: the Amex, not the Visa listed first."""
        with app.app_context():
            add_card(seed_user, "Visa")
            amex = add_card(seed_user, "Amex")
            row = _envelope(seed_user, seed_periods[0])

            response = _post(
                auth_client, row, is_credit="true", card_account_id=str(amex.id),
            )

            assert response.status_code == 200
            [entry] = _purchases(row.id)
            assert entry.account_id == amex.id
            assert _paybacks(row.id) == []

    def test_two_cards_and_no_pick_is_refused_in_words(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The form's "Which card" left on its prompt posts nothing; the door asks which."""
        with app.app_context():
            add_card(seed_user, "Visa")
            add_card(seed_user, "Amex")
            row = _envelope(seed_user, seed_periods[0])

            response = _post(auth_client, row, is_credit="true", card_account_id="")

            assert response.status_code == 400
            assert (
                "You have 2 credit cards: pick the one Kroger went on."
                in response.get_data(as_text=True)
            )
            assert _purchases(row.id) == []

    def test_a_pick_without_the_tick_is_refused_in_words(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The Amex picked and the box left off says two things: refused, nothing written.

        The review of plan#2930 (M4): filed on checking, the purchase would
        ignore the card the owner chose.
        """
        with app.app_context():
            add_card(seed_user, "Visa")
            amex = add_card(seed_user, "Amex")
            row = _envelope(seed_user, seed_periods[0])

            response = _post(auth_client, row, card_account_id=str(amex.id))

            assert response.status_code == 400
            assert (
                "A card is picked for Kroger but CC is not ticked"
                in response.get_data(as_text=True)
            )
            assert _purchases(row.id) == []

    def test_unticked_with_the_pick_on_its_prompt_stays_on_the_rows_account(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No tick and "Which card" left on its prompt (posted empty): checking paid."""
        with app.app_context():
            add_card(seed_user, "Visa")
            add_card(seed_user, "Amex")
            row = _envelope(seed_user, seed_periods[0])

            response = _post(auth_client, row, card_account_id="")

            assert response.status_code == 200
            [entry] = _purchases(row.id)
            assert entry.account_id == row.account_id

    def test_a_form_drawn_before_the_deploy_puts_its_tick_on_the_card(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The old box posted ``is_credit=on`` with no value: it is the tick, so the card takes it.

        The review of plan#2930 (M3): had the field been renamed on the wire,
        the old page's tick would be dropped as unknown and the purchase filed
        on checking unasked.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            row = _envelope(seed_user, seed_periods[0])

            response = _post(auth_client, row, is_credit="on")

            assert response.status_code == 200
            [entry] = _purchases(row.id)
            assert (entry.account_id, entry.is_credit) == (visa.id, False)
            assert _paybacks(row.id) == []

    def test_another_owners_card_is_not_found(
        self, app, auth_client, seed_user, seed_second_user, seed_periods,
    ):
        """A pick naming another owner's card, or no account at all, is a 404 and writes nothing."""
        with app.app_context():
            add_card(seed_user, "Visa")
            add_card(seed_user, "Amex")
            theirs = add_card(seed_second_user, "Their Visa")
            row = _envelope(seed_user, seed_periods[0])

            for named in (theirs.id, theirs.id + 1_000_000):
                response = _post(
                    auth_client, row, is_credit="true", card_account_id=str(named),
                )
                assert response.status_code == 404
            assert _purchases(row.id) == []


class TestAPartCardEnvelope:
    """The task's measurements: $800 with $120 on the card holds $680 on checking; closing changes neither."""

    def test_checking_holds_the_rest_and_the_card_carries_the_purchase(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """$800.00 budgeted, $120.00 ticked: checking holds $680.00, the card -$120.00.

        The envelope's hold on checking is its budget less every purchase on
        any account (``cash_ledger._amounts._entry_aware_amount``, read through
        the package's ``sum_projected``): 800.00 - 120.00 = 680.00.  The purchase is in flight on the CARD from
        the day it was bought, and checking carries no movement for it.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            row = _envelope(seed_user, seed_periods[0])
            assert _post(auth_client, row, is_credit="true").status_code == 200
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            [entry] = row.purchases

            assert cash_ledger.sum_projected([row], planted_basis(row)) == (
                Decimal("0.00"), Decimal("680.00"),
            )
            scenario_id = seed_user["scenario"].id
            on_the_card = cash_ledger.in_flight_movements(visa.id, scenario_id)
            assert [
                (m.entry_id, m.delta, m.purchased_on) for m in on_the_card
            ] == [(entry.id, Decimal("-120.00"), display_today())]
            assert cash_ledger.in_flight_movements(
                seed_user["account"].id, scenario_id,
            ) == []

    def test_closing_a_whole_card_envelope_changes_neither_balance(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """S2 of the task: $800 all ticked, then the envelope marked Paid.

        Before and after the close checking holds 800.00 - 800.00 = 0.00 and
        carries no movement, the card carries -800.00 from the purchase's day,
        and no payback exists: the close books only what its purchases did
        not, and they all went on the card.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            row = _envelope(seed_user, seed_periods[0])
            assert _post(
                auth_client, row, is_credit="true", amount="800.00",
            ).status_code == 200
            scenario_id = seed_user["scenario"].id

            def _state():
                db.session.expire_all()
                fresh = db.session.get(Transaction, row.id)
                return (
                    cash_ledger.sum_projected([fresh], planted_basis(fresh)),
                    [m.delta for m in cash_ledger.in_flight_movements(visa.id, scenario_id)],
                    cash_ledger.in_flight_movements(seed_user["account"].id, scenario_id),
                    _paybacks(row.id),
                )

            before = _state()
            assert before == (
                (Decimal("0.00"), Decimal("0.00")), [Decimal("-800.00")], [], [],
            )
            transaction_service.apply_requested_status(
                db.session.get(Transaction, row.id),
                ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            after = _state()
            assert after[1:] == before[1:]
