"""Plan task plan#3142: ticking or un-ticking CC on a recorded purchase moves it.

Ruling plan#2888 (developer 2026-11-04): "the tick stays the one control, with a
pick only for two or more cards".  On the edit form the tick says where the
purchase's money went, as the add form's does (plan task plan#2930), so
changing it MOVES the purchase -- onto the owner's card, back to its row's
account, or between cards -- through the one write of a movement's account
(``movement_account.re_point_movement``, the core ``status_seam._covering.
_re_point`` moved there): the day it ends with graded on the new account's
books, every match naming it withdrawn under the save's press, its clearing
link released.  The edit form names the lines a move would unmatch before the
press and posts them back (rulings R-CC80, R-CC127); a companion's move that
would free one is refused naming the purchase (R-CC132).  CC-380's case, a tick
on a bank-matched purchase admitted with the match standing, is the first arm.

What is graded is what the edit form POSTS -- read off the rendered page,
control by control -- and what the door does with it, read back after a commit.
"""

from __future__ import annotations

import ast
import html as html_lib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, entry_service, transaction_service
from app.services.cash_ledger import account_opening_fact
from app.services.settle_day import SettleDay, record_settle_day
from app.services.statement_match import accept_match, matched_subjects
from tests._cards import add_card
from tests._legacy_credit import plant_old_flagged_purchase
from tests._test_helpers import create_account_of_type, typed
from tests.test_routes.test_cc5_4a3_captions import _claimed, _day
from tests.test_routes.test_cc5_4a5b_purchase_and_credit_doors import (
    _KROGER_LINE,
    _committed,
    _edit_form,
    _groceries,
    _list,
    _matched_kroger,
    _purchase,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_scope,
    a_submission,
    an_import,
)


def _ticked(fields):
    """The form's controls with CC ticked, as a browser posts them: box, then hidden twin."""
    if ("is_credit", "true") in fields:
        return list(fields)
    at = fields.index(("is_credit", "false"))
    return [*fields[:at], ("is_credit", "true"), *fields[at:]]


def _unticked(fields):
    """The form's controls with CC left off: the hidden twin alone posts."""
    return [pair for pair in fields if pair != ("is_credit", "true")]


def _picked(fields, card_id):
    """The form's controls with "Which card" set to *card_id*."""
    return [
        ("card_account_id", str(card_id)) if name == "card_account_id"
        else (name, value)
        for name, value in fields
    ]


def _save(client, txn, entry, change=lambda fields: fields):
    """Open *entry*'s edit form, apply *change* to what it would post, and Save it."""
    fields, url = _edit_form(_list(client, txn.id, editing=entry.id))
    return client.patch(url, data=MultiDict(change(fields)))


def _stored(entry_id):
    """*entry_id* as committed: its account, its flag, its posting day."""
    _committed()
    entry = db.session.get(TransactionEntry, entry_id)
    return entry.account_id, entry.is_credit, entry.settled_on


def _swipe(seed_user, txn, card):
    """A $12.34 Kroger bought on *card* under *txn*, through the add door's CC tick."""
    entry = entry_service.create_entry(
        txn.id, seed_user["user"].id, entry_service.EntryDetails(
            figure=typed(Decimal("12.34")), description="Kroger",
            purchased_on=_day(seed_user), on_card=True, card_account_id=card.id,
        ),
    )
    db.session.commit()
    return entry


def _caption(page):
    """The page's text as one line of words, entities decoded: what a reader sees."""
    return " ".join(html_lib.unescape(page).split())


def _paybacks(txn_id):
    """The CC Payback rows naming *txn_id*."""
    return db.session.query(Transaction).filter_by(
        credit_payback_for_id=txn_id,
    ).all()


class TestTheEditFormDrawsTheTickAsTheDoorReadsIt:
    """``movement_account.purchase_card``: the tick and the pick drawn as the purchase is."""

    def test_with_no_card_a_checking_purchase_has_no_tick(
        self, app, auth_client, seed_user,
    ):
        """No card: the door could only refuse a tick, so none is drawn."""
        with app.app_context():
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            fields, _url = _edit_form(_list(auth_client, txn.id, editing=kroger.id))
            assert all(name != "is_credit" for name, _value in fields)

    def test_one_card_draws_the_tick_off_and_names_the_card(
        self, app, auth_client, seed_user,
    ):
        """One card and a checking purchase: the tick unticked, titled for the card, no pick."""
        with app.app_context():
            add_card(seed_user, "Visa")
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            page = _list(auth_client, txn.id, editing=kroger.id)
            fields, _url = _edit_form(page)
            assert ("is_credit", "true") not in fields
            assert ("is_credit", "false") in fields
            assert all(name != "card_account_id" for name, _value in fields)
            assert 'title="Bought on Visa"' in page

    def test_a_purchase_on_a_card_is_drawn_ticked_with_its_card_picked(
        self, app, auth_client, seed_user,
    ):
        """Two cards and a purchase on the Amex: ticked, and the pick names the Amex."""
        with app.app_context():
            add_card(seed_user, "Visa")
            amex = add_card(seed_user, "Amex")
            txn = _groceries(seed_user)
            swipe = _swipe(seed_user, txn, amex)
            fields, _url = _edit_form(_list(auth_client, txn.id, editing=swipe.id))
            assert ("is_credit", "true") in fields
            assert ("card_account_id", str(amex.id)) in fields

    def test_an_old_flagged_line_is_drawn_ticked_with_no_pick_even_with_no_card(
        self, app, auth_client, seed_user,
    ):
        """A flagged line stays un-tickable: its tick drawn though no card exists, no pick."""
        with app.app_context():
            txn = _groceries(seed_user)
            a_later_period(seed_user)
            db.session.commit()
            flagged = _purchase(seed_user, txn, "12.34", "Kroger", is_credit=True)
            fields, _url = _edit_form(_list(auth_client, txn.id, editing=flagged.id))
            assert ("is_credit", "true") in fields
            assert all(name != "card_account_id" for name, _value in fields)

    def test_an_untouched_save_moves_nothing(self, app, auth_client, seed_user):
        """Saved as drawn, a purchase stays on the Amex: the form draws what the door reads."""
        with app.app_context():
            add_card(seed_user, "Visa")
            amex = add_card(seed_user, "Amex")
            txn = _groceries(seed_user)
            swipe = _swipe(seed_user, txn, amex)

            response = _save(auth_client, txn, swipe)

            assert response.status_code == 200, response.get_data(as_text=True)
            assert _stored(swipe.id) == (amex.id, False, None)


class TestTheTickMovesThePurchase:
    """Onto the card, back to checking, between cards: one write, no flag, no payback."""

    def test_ticking_moves_a_checking_purchase_onto_the_card(
        self, app, auth_client, seed_user,
    ):
        """$12.34 Kroger ticked: on the Visa from the day it was bought, off checking, no payback.

        The envelope holds 300.00 - 12.34 = 287.66 on checking either way; what
        moves is which account the 12.34 is in flight on.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            scenario_id = seed_user["scenario"].id
            assert [m.entry_id for m in cash_ledger.in_flight_movements(
                seed_user["account"].id, scenario_id,
            )] == [kroger.id]

            response = _save(auth_client, txn, kroger, _ticked)

            assert response.status_code == 200, response.get_data(as_text=True)
            assert _stored(kroger.id) == (visa.id, False, None)
            assert _paybacks(txn.id) == []
            assert [
                (m.entry_id, m.delta, m.purchased_on)
                for m in cash_ledger.in_flight_movements(visa.id, scenario_id)
            ] == [(kroger.id, Decimal("-12.34"), _day(seed_user))]
            assert cash_ledger.in_flight_movements(
                seed_user["account"].id, scenario_id,
            ) == []

    def test_un_ticking_moves_a_card_purchase_back_with_its_pick_posted(
        self, app, auth_client, seed_user,
    ):
        """Two cards: the pick still posts the Amex when only the tick changed; that is no pick.

        The form draws "Which card" with the purchase's card selected, so an
        un-tick posts it back (``_details.tick_edit``'s echo rule); refusing it
        as "a card is picked but CC is not ticked" would refuse every un-tick.
        """
        with app.app_context():
            add_card(seed_user, "Visa")
            amex = add_card(seed_user, "Amex")
            txn = _groceries(seed_user)
            swipe = _swipe(seed_user, txn, amex)
            fields, url = _edit_form(_list(auth_client, txn.id, editing=swipe.id))
            assert ("card_account_id", str(amex.id)) in _unticked(fields)

            response = auth_client.patch(url, data=MultiDict(_unticked(fields)))

            assert response.status_code == 200, response.get_data(as_text=True)
            assert _stored(swipe.id) == (txn.account_id, False, None)

    def test_picking_another_card_moves_it_there(self, app, auth_client, seed_user):
        """The Visa purchase re-picked as the Amex is on the Amex."""
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            amex = add_card(seed_user, "Amex")
            txn = _groceries(seed_user)
            swipe = _swipe(seed_user, txn, visa)

            response = _save(
                auth_client, txn, swipe, lambda fields: _picked(fields, amex.id),
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            assert _stored(swipe.id) == (amex.id, False, None)

    def test_another_card_picked_with_the_tick_off_is_refused(
        self, app, auth_client, seed_user,
    ):
        """The Amex picked and the box un-ticked on a Visa purchase says two things: refused."""
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            amex = add_card(seed_user, "Amex")
            txn = _groceries(seed_user)
            swipe = _swipe(seed_user, txn, visa)

            response = _save(
                auth_client, txn, swipe,
                lambda fields: _unticked(_picked(fields, amex.id)),
            )

            assert response.status_code == 400
            assert "A card is picked for Kroger but CC is not ticked" in (
                html_lib.unescape(response.get_data(as_text=True))
            )
            assert _stored(swipe.id) == (visa.id, False, None)

    def test_an_edit_form_drawn_before_the_deploy_is_refused_and_moves_nothing(
        self, app, auth_client, seed_user,
    ):
        """The old form ticked its box only for the flag: saved over a card purchase, refused.

        The review of plan#3142 (M2).  A grid tab open across the deploy
        draws the Visa purchase's box off and posts no ``tick_drawn``; read
        as the new tick it would be an un-tick and move the purchase onto
        checking while the owner only renamed it.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            txn = _groceries(seed_user)
            swipe = _swipe(seed_user, txn, visa)
            fields, url = _edit_form(_list(auth_client, txn.id, editing=swipe.id))
            old_form = [
                ("description", "Kroger #2") if name == "description"
                else (name, value)
                for name, value in _unticked(fields) if name != "tick_drawn"
            ]

            response = auth_client.patch(url, data=MultiDict(old_form))

            assert response.status_code == 422
            assert "drawn before the CC box started moving purchases" in (
                html_lib.unescape(response.get_data(as_text=True))
            )
            assert _stored(swipe.id) == (visa.id, False, None)
            assert db.session.get(TransactionEntry, swipe.id).description == "Kroger"

    def test_another_owners_card_picked_is_not_found_and_moves_nothing(
        self, app, auth_client, seed_user, seed_second_user,
    ):
        """A pick naming another owner's card, or no account at all: a 404, nothing written."""
        with app.app_context():
            add_card(seed_user, "Visa")
            add_card(seed_user, "Amex")
            theirs = add_card(seed_second_user, "Their Visa")
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")

            for named in (theirs.id, theirs.id + 1_000_000):
                response = _save(
                    auth_client, txn, kroger,
                    lambda fields, named=named: _picked(_ticked(fields), named),
                )
                assert response.status_code == 404
                assert "Account not found." in response.get_data(as_text=True)
            assert _stored(kroger.id) == (txn.account_id, False, None)

    def test_two_cards_and_no_pick_is_refused_in_words(
        self, app, auth_client, seed_user,
    ):
        """A checking purchase ticked with "Which card" on its prompt: the door asks which."""
        with app.app_context():
            add_card(seed_user, "Visa")
            add_card(seed_user, "Amex")
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")

            response = _save(auth_client, txn, kroger, _ticked)

            assert response.status_code == 400
            assert "You have 2 credit cards: pick the one Kroger went on." in (
                response.get_data(as_text=True)
            )
            assert _stored(kroger.id) == (txn.account_id, False, None)

    def test_a_settled_envelope_draws_no_tick_and_refuses_a_crafted_one(
        self, app, auth_client, seed_user,
    ):
        """Its purchases are closed: no tick drawn, and a crafted one refused, nothing moved."""
        with app.app_context():
            add_card(seed_user, "Visa")
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            transaction_service.apply_requested_status(
                db.session.get(Transaction, txn.id),
                ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            fields, url = _edit_form(_list(auth_client, txn.id, editing=kroger.id))
            assert all(name != "is_credit" for name, _value in fields)

            response = auth_client.patch(url, data=MultiDict(
                [*fields, ("is_credit", "true"), ("tick_drawn", "true")],
            ))

            assert response.status_code == 400
            assert "Groceries has settled; its purchases are closed" in (
                response.get_data(as_text=True)
            )
            account_id, _flag, _day_stored = _stored(kroger.id)
            assert account_id == txn.account_id

    def test_an_old_flagged_line_un_ticked_loses_its_flag_and_its_payback(
        self, app, auth_client, seed_user,
    ):
        """The old lines keep the payback arms: un-ticked, the flag and the payback both go."""
        with app.app_context():
            add_card(seed_user, "Visa")
            txn = _groceries(seed_user)
            a_later_period(seed_user)
            db.session.commit()
            flagged = _purchase(seed_user, txn, "12.34", "Kroger", is_credit=True)
            assert len(_paybacks(txn.id)) == 1

            response = _save(auth_client, txn, flagged, _unticked)

            assert response.status_code == 200, response.get_data(as_text=True)
            assert _stored(flagged.id) == (txn.account_id, False, None)
            assert _paybacks(txn.id) == []

    def test_a_card_named_beside_an_old_flagged_lines_tick_is_refused(
        self, app, seed_user,
    ):
        """Putting a flagged line on a card by name is plan#1034's conversion: refused in words."""
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            txn = _groceries(seed_user)
            a_later_period(seed_user)
            db.session.commit()
            flagged = plant_old_flagged_purchase(
                txn.id, seed_user["user"].id, entry_service.EntryDetails(
                    figure=typed(Decimal("12.34")), description="Kroger",
                    purchased_on=_day(seed_user),
                ),
            )
            with pytest.raises(
                ValidationError, match=r"still repaid by its CC Payback",
            ):
                entry_service.update_entry(
                    flagged.id, seed_user["user"].id,
                    on_card=True, card_account_id=visa.id,
                )


class TestThePostingDayGoesWithTheOldAccount:
    """``_details.settle_day_after_move``: a day the save did not move is the old account's."""

    def test_a_posted_purchase_moved_onto_the_card_loses_its_day_and_link(
        self, app, auth_client, seed_user,
    ):
        """Posted on checking the day after it was bought, ticked: on the Visa from its own date.

        A checking true-up's day says nothing of when the card took it, and
        a purchase on a card counts from the day it was bought (rulings
        plan#2926, plan#2958), so the day and the link go with checking.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            record_settle_day(kroger, SettleDay(
                day=_day(seed_user) + timedelta(days=1),
                basis=SettledDayBasisEnum.ENTERED,
            ))
            db.session.commit()

            response = _save(auth_client, txn, kroger, _ticked)

            assert response.status_code == 200, response.get_data(as_text=True)
            assert _stored(kroger.id) == (visa.id, False, None)
            moved = db.session.get(TransactionEntry, kroger.id)
            assert (moved.settled_day_basis_id, moved.reconciled_by_id) == (None, None)

    def test_a_day_typed_in_the_same_save_is_kept_for_the_new_account(
        self, app, seed_user,
    ):
        """The owner's word for the card, typed with the tick, stands, entered."""
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            txn = _groceries(seed_user)
            kroger = _purchase(seed_user, txn, "12.34", "Kroger")
            typed_day = SettleDay(
                day=_day(seed_user) + timedelta(days=1),
                basis=SettledDayBasisEnum.ENTERED,
            )

            entry_service.update_entry(
                kroger.id, seed_user["user"].id,
                on_card=True, settle_day=typed_day,
            )

            assert _stored(kroger.id) == (visa.id, False, typed_day.day)

    def test_a_typed_day_the_cards_books_do_not_reach_is_refused(
        self, app, seed_user,
    ):
        """The day graded on the CARD's books, before anything is written."""
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            cards_opening = account_opening_fact(card.id).opened_on
            txn = _groceries(seed_user)
            kroger = entry_service.create_entry(
                txn.id, seed_user["user"].id, entry_service.EntryDetails(
                    figure=typed(Decimal("12.34")), description="Kroger",
                    purchased_on=cards_opening - timedelta(days=1),
                ),
            )
            db.session.commit()

            with pytest.raises(ValidationError, match=r"books open on"):
                entry_service.update_entry(
                    kroger.id, seed_user["user"].id, on_card=True,
                    settle_day=SettleDay(
                        day=cards_opening, basis=SettledDayBasisEnum.ENTERED,
                    ),
                )
            db.session.rollback()
            assert _stored(kroger.id) == (txn.account_id, False, None)


class TestAMatchedPurchaseNamesItsLineFirst:
    """CC-380's case: the move frees the purchase's line, named before the press."""

    def test_the_tick_names_the_line_and_its_save_frees_it(
        self, app, auth_client, seed_user,
    ):
        """Kroger matched to its checking line: the caption names it, the save frees it."""
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            txn, kroger, line = _matched_kroger(seed_user)
            page = _list(auth_client, txn.id, editing=kroger.id)
            assert (
                "Ticking CC puts this on Visa and withdraws 1 accepted match, "
                "so 1 bank line is unexplained again on your statement screen: "
                f"{_day(seed_user):%-m/%-d} {_KROGER_LINE} -$12.34."
                in _caption(page)
            )
            fields, url = _edit_form(page)
            assert ("shown_lines", str(line.id)) in fields

            response = auth_client.patch(url, data=MultiDict(_ticked(fields)))

            assert response.status_code == 200, response.get_data(as_text=True)
            assert _stored(kroger.id)[:2] == (visa.id, False)
            assert not _claimed(seed_user, line)
            assert _paybacks(txn.id) == []

    def test_an_untouched_save_of_a_matched_purchase_keeps_its_match(
        self, app, auth_client, seed_user,
    ):
        """The caption's lines posted with no move: no match step is reached, so none is graded."""
        with app.app_context():
            add_card(seed_user, "Visa")
            txn, kroger, line = _matched_kroger(seed_user)

            response = _save(auth_client, txn, kroger)

            assert response.status_code == 200, response.get_data(as_text=True)
            account_id, _flag, _posted = _stored(kroger.id)
            assert account_id == txn.account_id
            assert _claimed(seed_user, line)

    def test_a_form_drawn_before_the_match_is_refused_and_nothing_moves(
        self, app, auth_client, seed_user,
    ):
        """The form named no line; the move would free one: out of date, nothing saved."""
        with app.app_context():
            add_card(seed_user, "Visa")
            txn, kroger, line = _matched_kroger(seed_user)
            fields, url = _edit_form(_list(auth_client, txn.id, editing=kroger.id))
            stale = [
                ("shown_lines", "") if name == "shown_lines" else (name, value)
                for name, value in _ticked(fields)
            ]

            response = auth_client.patch(url, data=MultiDict(stale))

            assert response.status_code == 400
            assert "Nothing was saved: this page was out of date." in (
                response.get_data(as_text=True)
            )
            account_id, _flag, _posted = _stored(kroger.id)
            assert account_id == txn.account_id
            assert _claimed(seed_user, line)

    def test_a_companions_tick_is_refused_naming_the_purchase(
        self, app, companion_client, seed_user,
    ):
        """A companion is shown no line: its form names none, and its move is refused."""
        with app.app_context():
            add_card(seed_user, "Visa")
            txn, kroger, line = _matched_kroger(seed_user, companion_visible=True)
            page = _list(companion_client, txn.id, editing=kroger.id)
            assert "unexplained" not in page
            fields, url = _edit_form(page)
            assert all(name != "shown_lines" for name, _value in fields)

            response = companion_client.patch(url, data=MultiDict(_ticked(fields)))

            assert response.status_code == 400
            assert (
                "Kroger is matched to a line on the bank statement, so only "
                "the account owner can move it onto or off a card."
                in html_lib.unescape(response.get_data(as_text=True))
            )
            account_id, _flag, _posted = _stored(kroger.id)
            assert account_id == txn.account_id
            assert _claimed(seed_user, line)

    def test_un_ticking_a_purchase_matched_on_the_card_names_the_cards_line(
        self, app, auth_client, seed_user,
    ):
        """A Visa purchase matched to the Visa's line: moving it off the Visa frees that line."""
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            txn = _groceries(seed_user)
            swipe = _swipe(seed_user, txn, visa)
            line = a_bank_line(
                seed_user, an_import(seed_user, visa), amount="-12.34",
                posted_on=_day(seed_user), description=_KROGER_LINE,
            )
            db.session.commit()
            accept_match(
                a_submission(a_scope(seed_user, visa), lines=[line], entries=[swipe]),
                a_scope(seed_user, visa),
            )
            db.session.commit()
            page = _list(auth_client, txn.id, editing=swipe.id)
            assert (
                "Moving this off Visa, by un-ticking CC, withdraws 1 accepted "
                "match" in _caption(page)
            )
            fields, url = _edit_form(page)
            assert ("shown_lines", str(line.id)) in fields

            response = auth_client.patch(url, data=MultiDict(_unticked(fields)))

            assert response.status_code == 200, response.get_data(as_text=True)
            assert _stored(swipe.id)[0] == txn.account_id
            assert line.id not in matched_subjects(visa.id).lines


class TestTheMoveIsOneWrite:
    """Rule 14: the two doors that move a movement's account reach one function."""

    def test_both_doors_call_the_one_write_and_neither_assigns_the_column(self, app):
        """Source census: the seam's re-point and the edit door call ``re_point_movement``.

        Behaviour cannot tell one write from two spellings that agree, so the
        census is of the source: each door calls the shared write, and neither
        assigns a movement's ``account_id`` itself (the create door's
        constructor keyword is a new movement's, not a move).
        """
        services = Path(app.root_path) / "services"
        for door in ("status_seam/_covering.py", "entry_service/_doors.py"):
            source = (services / door).read_text(encoding="utf-8")
            assert "re_point_movement(" in source, door
            assigned = [
                node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Attribute) and target.attr == "account_id"
            ]
            assert assigned == [], door
