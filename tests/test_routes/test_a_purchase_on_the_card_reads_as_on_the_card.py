"""Plan task plan#3143: a purchase on the card reads as on the card, in the cell and the list.

From plan#2930's review (M5, reproduced): "on the card" had two spellings on
screen, and the grid's "on CC" figure read only the old one.  A purchase the CC
tick puts ON the card (plan tasks plan#2930 and plan#3142) carries no CC flag,
so a $120.00 purchase ticked onto the Visa in an $800.00 Groceries envelope
titled the cell "$120.00 / $800.00 -- $680.00 remaining -- 1 entry", with no
"includes $120.00 on CC", and beside an old $100.00 flagged line and a $50.00
tick it said "includes $100.00 on CC", $50.00 short.  The purchase list drew no
CC badge on the ticked purchase.

**One predicate now** (``movement_account.on_a_card``): the old flag, or a
purchase on a card other than the account its row lives on, which is what the
edit form's tick reads a purchase as (``movement_account.purchase_card``).  It
is NARROWER than the money side's "not on the row's account"
(``cash_ledger._cash_leg._moves_elsewhere``): a purchase paid from checking
under a row that lives on the Visa, or on Savings, has moved off its row's
account and is on no card, and the last class below grades that it is not
called one.

**The posted indicator does not read it** (the review of this task): a
purchase on a card is a movement ON the card, in flight there until its posting
day, so the list draws its indicator as it always did; only an old flagged
line, on no account's books, is drawn without one.

What is graded is what each screen draws: the desktop cell's title
(``/transactions/<id>/cell``), the purchase list (``/transactions/<id>/entries``)
and the phone card's inline list and label on the grid's first render, the
path whose context the grid macro unpacks key by key.
"""

from __future__ import annotations

import html as html_lib
from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service
from tests._cards import add_card, add_savings
from tests._legacy_credit import plant_old_flagged_purchase
from tests._test_helpers import (
    current_pay_period,
    generate_row_of,
    make_expense_template,
    typed,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the suite's one way to stage the later paycheck an old flagged line's CC
# payback lands in; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import a_later_period

#: The CC badge the purchase list draws on a purchase on a card.
_CC_BADGE = 'title="Credit card">CC</span>'

#: The posted indicator's two screen-reader words, one of which every
#: purchase it is drawn for carries.
_INDICATOR_WORDS = ("Still outstanding", "Already left your account")

#: The indicator's words for a purchase whose posting day is not recorded.
_UNPOSTED = "Still outstanding"


def _bought_on(period):
    """The day after *period*'s payday: inside it, and before today."""
    return period.start_date + timedelta(days=1)


def _groceries(seed_user, period, *, account=None):
    """A Projected $800.00 Groceries envelope in *period*, on *account* or checking."""
    template = make_expense_template(
        db.session, seed_user, amount="800.00", name="Groceries",
        category_key="Groceries", is_envelope=True,
        **({"account": account} if account is not None else {}),
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _tick(client, row, amount, description, bought_on):
    """Record a purchase with CC ticked, as the add form posts it; return it."""
    response = client.post(
        f"/transactions/{row.id}/entries",
        data={
            "amount": amount,
            "direction": "charge",
            "description": description,
            "purchased_on": bought_on.isoformat(),
            "is_credit": "true",
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return _the(row.id, description)


def _recorded(seed_user, row, amount, description, *, account_id=None, card=None):
    """Record a purchase bought in the bootstrap paycheck, through the add door.

    "Paid from" *account_id*, or its row's account; or, given *card*, ticked
    CC with *card* picked.
    """
    entry = entry_service.create_entry(
        row.id, seed_user["user"].id, entry_service.EntryDetails(
            figure=typed(Decimal(amount)), description=description,
            purchased_on=_bought_on(seed_user["bootstrap_period"]),
            account_id=account_id, on_card=card is not None,
            card_account_id=None if card is None else card.id,
        ),
    )
    db.session.commit()
    return entry


def _flagged(seed_user, row, amount, description, bought_on):
    """Plant an old flagged line, its CC payback in the next paycheck."""
    a_later_period(seed_user)
    entry = plant_old_flagged_purchase(
        row.id, seed_user["user"].id, entry_service.EntryDetails(
            figure=typed(Decimal(amount)), description=description,
            purchased_on=bought_on,
        ),
    )
    db.session.commit()
    return entry


def _the(row_id, description):
    """The stored purchase *description* under *row_id*."""
    db.session.expire_all()
    return db.session.query(TransactionEntry).filter_by(
        transaction_id=row_id, description=description,
    ).one()


def _words(page):
    """The page's text as one line of words, entities decoded: what a reader sees."""
    return " ".join(html_lib.unescape(page).split())


def _cell(client, row):
    """The desktop cell for *row*, as one line of words."""
    response = client.get(f"/transactions/{row.id}/cell")
    assert response.status_code == 200
    return _words(response.get_data(as_text=True))


def _list(client, row):
    """The purchase list for *row*, as the popover and the phone card refresh draw it."""
    response = client.get(f"/transactions/{row.id}/entries")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _display_row(page, description, *, after=0):
    """The purchase list's display row for *description*: its badge, chip and indicator.

    Each display row opens with the same ``<div>``; the row is the text from
    its opening to the next one (or the Remaining line after the last), so
    what it holds is that purchase's alone.  *after* starts the search there,
    for a page holding more than one list.
    """
    opener = (
        '<div class="d-flex justify-content-between align-items-center '
        'py-1 border-bottom small">'
    )
    at = page.index(f"{description}\n", page.index(opener, after))
    start = page.rindex(opener, after, at)
    ends = [
        index for index in (
            page.find(opener, at), page.find("Remaining", at),
        ) if index != -1
    ]
    return page[start:min(ends)]


class TestTheCellCountsAPurchaseOnTheCardOnCC:
    """The cell's title and the phone card's label sum every purchase on a card."""

    def test_a_purchase_ticked_onto_the_card_is_included_on_cc(
        self, app, auth_client, seed_user,
    ):
        """$120.00 ticked onto the Visa in an $800.00 envelope: the title says so.

        Arithmetic: 800.00 - 120.00 = 680.00 remaining, and the one purchase,
        $120.00, is on the card.  Before plan#3143 the title stopped at
        "1 entry": the sum read only the old flag, which the tick never writes.
        """
        with app.app_context():
            add_card(seed_user, "Visa")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period)
            _tick(auth_client, row, "120.00", "Kroger", _bought_on(period))

            assert (
                "$120.00 / $800.00 -- $680.00 remaining -- 1 entry -- "
                "includes $120.00 on CC"
            ) in _cell(auth_client, row)

    def test_an_old_flagged_line_and_a_tick_are_both_on_cc(
        self, app, auth_client, seed_user,
    ):
        """An old $100.00 flagged line beside a $50.00 tick: $150.00 on CC, not $100.00.

        Arithmetic: 100.00 + 50.00 = 150.00 spent and on a card, and
        800.00 - 150.00 = 650.00 remaining.  Before plan#3143 the title said
        "includes $100.00 on CC", the flagged line alone.
        """
        with app.app_context():
            add_card(seed_user, "Visa")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period)
            _flagged(seed_user, row, "100.00", "Costco", _bought_on(period))
            _tick(auth_client, row, "50.00", "Kroger", _bought_on(period))

            assert (
                "$150.00 / $800.00 -- $650.00 remaining -- 2 entries -- "
                "includes $150.00 on CC"
            ) in _cell(auth_client, row)

    def test_a_checking_purchase_beside_a_tick_is_not_on_cc(
        self, app, auth_client, seed_user,
    ):
        """$30.00 on checking beside a $120.00 tick: only the tick is on CC.

        Arithmetic: 30.00 + 120.00 = 150.00 spent, 800.00 - 150.00 = 650.00
        remaining, $120.00 of it on the card.
        """
        with app.app_context():
            add_card(seed_user, "Visa")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period)
            _recorded(seed_user, row, "30.00", "Aldi")
            _tick(auth_client, row, "120.00", "Kroger", _bought_on(period))

            assert (
                "$150.00 / $800.00 -- $650.00 remaining -- 2 entries -- "
                "includes $120.00 on CC"
            ) in _cell(auth_client, row)

    @pytest.mark.usefixtures("seed_periods_today")
    def test_the_phone_cards_label_on_the_grid_includes_the_tick(
        self, app, auth_client, seed_user,
    ):
        """The grid's first render: the phone card's label sums the tick on CC too.

        The label is ``render_one_card``'s, from the same sums the cell
        reads (``entry_service.build_entry_sums_dict``).  Arithmetic as the
        first case: 800.00 - 120.00 = 680.00.
        """
        with app.app_context():
            add_card(seed_user, "Visa")
            period = current_pay_period(seed_user["user"].id)
            row = _groceries(seed_user, period)
            _tick(auth_client, row, "120.00", "Kroger", period.start_date)

            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert (
                "Groceries: $120.00 / $800.00 -- $680.00 remaining -- "
                "1 entry -- includes $120.00 on CC"
            ) in _words(response.get_data(as_text=True))


class TestThePurchaseListMarksAPurchaseOnTheCard:
    """The CC badge on every purchase on a card; the posted indicator on all but a flagged line."""

    def test_a_ticked_purchase_has_the_badge_and_its_indicator(
        self, app, auth_client, seed_user,
    ):
        """The tick's purchase: badged CC, its card's chip beside, and its posted indicator.

        Before plan#3143 it drew no badge.  The indicator stays: the purchase
        is a movement on the Visa, in flight there until its posting day is
        recorded, so whether it has posted is the card's to show.
        """
        with app.app_context():
            add_card(seed_user, "Visa")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period)
            _tick(auth_client, row, "120.00", "Kroger", _bought_on(period))

            kroger = _display_row(_list(auth_client, row), "Kroger")
            assert _CC_BADGE in kroger
            assert 'title="On Visa">Visa</span>' in kroger
            assert _UNPOSTED in kroger

    def test_an_old_flagged_line_keeps_the_badge_and_no_indicator(
        self, app, auth_client, seed_user,
    ):
        """The old flagged line reads as it did: badged CC, no indicator, no chip."""
        with app.app_context():
            add_card(seed_user, "Visa")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period)
            _flagged(seed_user, row, "100.00", "Costco", _bought_on(period))

            costco = _display_row(_list(auth_client, row), "Costco")
            assert _CC_BADGE in costco
            assert "flag-chip" not in costco
            assert not any(word in costco for word in _INDICATOR_WORDS)

    def test_a_checking_purchase_has_the_indicator_and_no_badge(
        self, app, auth_client, seed_user,
    ):
        """A purchase on its row's checking: no badge, and the indicator drawn."""
        with app.app_context():
            add_card(seed_user, "Visa")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period)
            _recorded(seed_user, row, "30.00", "Aldi")

            aldi = _display_row(_list(auth_client, row), "Aldi")
            assert _CC_BADGE not in aldi
            assert _UNPOSTED in aldi

    def test_a_purchase_on_an_archived_card_is_still_badged_and_ticked(
        self, app, auth_client, seed_user,
    ):
        """The Visa archived after the tick: no card is offered, and the purchase is still on it.

        With the owner's only card archived, ``tick_cards`` is empty, so the
        edit form draws its tick for this purchase only because it IS on a
        card (``on_a_card``), drawn ticked so an untouched Save leaves it
        there; the display row keeps its badge.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period)
            kroger = _tick(auth_client, row, "120.00", "Kroger", _bought_on(period))
            visa_id, kroger_id = visa.id, kroger.id
            assert auth_client.post(f"/accounts/{visa_id}/archive").status_code == 302

            assert _CC_BADGE in _display_row(_list(auth_client, row), "Kroger")
            response = auth_client.get(
                f"/transactions/{row.id}/entries",
                query_string={"editing": kroger_id},
            )
            assert response.status_code == 200
            page = response.get_data(as_text=True)
            tick_id = f'id="edit-cc-entry-list-{row.id}-{kroger_id}"'
            start = page.index(tick_id)
            tick = page[page.rindex("<input", 0, start):page.index(">", start) + 1]
            assert 'name="is_credit"' in tick
            assert "checked" in tick

    @pytest.mark.usefixtures("seed_periods_today")
    def test_the_grids_first_render_badges_the_tick_on_the_phone_card(
        self, app, auth_client, seed_user,
    ):
        """The phone card's inline list, from the grid macro's unpacked context, badges it too."""
        with app.app_context():
            add_card(seed_user, "Visa")
            period = current_pay_period(seed_user["user"].id)
            row = _groceries(seed_user, period)
            _tick(auth_client, row, "120.00", "Kroger", period.start_date)

            response = auth_client.get("/grid")
            assert response.status_code == 200
            page = response.get_data(as_text=True)
            # This Period's phone card draws its list under the ``tp`` prefix
            # (``grid/_mobile_this_period.html``).
            kroger = _display_row(
                page, "Kroger", after=page.index(f'id="entry-list-tp-{row.id}"'),
            )
            assert _CC_BADGE in kroger
            assert _UNPOSTED in kroger


class TestOnACardIsNarrowerThanOffItsRowsAccount:
    """A purchase off its row's account and on no card is not on CC; one on another card is."""

    def test_checking_under_a_row_on_the_card_is_not_on_cc(
        self, app, auth_client, seed_user,
    ):
        """Groceries on the Visa, one purchase paid from checking: no CC anywhere.

        ``_moves_elsewhere`` is true of it (checking is not the row's account)
        and it is on no card, so the cell says nothing on CC, the list draws
        no badge, and the indicator stands: the purchase's money left
        checking, so whether it has posted is checking's question.
        Arithmetic: 800.00 - 45.00 = 755.00 remaining.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period, account=visa)
            _recorded(
                seed_user, row, "45.00", "Target",
                account_id=seed_user["account"].id,
            )

            cell = _cell(auth_client, row)
            assert "$45.00 / $800.00 -- $755.00 remaining -- 1 entry" in cell
            assert "on CC" not in cell
            target = _display_row(_list(auth_client, row), "Target")
            assert _CC_BADGE not in target
            assert _UNPOSTED in target

    def test_a_purchase_on_the_cards_own_row_is_not_on_cc(
        self, app, auth_client, seed_user,
    ):
        """Groceries on the Visa, a purchase on the Visa: the row says the card, once.

        The edit form draws no tick on a row that lives on a card
        (``movement_account.tick_cards``), so a badge here would mark a
        purchase CC that its own edit form cannot un-tick; the row's chip is
        what says it is the card's.  Arithmetic: 800.00 - 60.00 = 740.00.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period, account=visa)
            _recorded(seed_user, row, "60.00", "Shell")

            cell = _cell(auth_client, row)
            assert "$60.00 / $800.00 -- $740.00 remaining -- 1 entry" in cell
            assert "on CC" not in cell
            assert _CC_BADGE not in _display_row(_list(auth_client, row), "Shell")

    def test_checking_under_a_row_on_savings_is_not_on_cc(
        self, app, auth_client, seed_user,
    ):
        """Groceries on Savings, one purchase paid from checking: no CC anywhere.

        "Paid from" offers a Savings row Checking too (ruling plan#3227), so
        this purchase has moved off its row's account and is on no card.
        Arithmetic: 800.00 - 45.00 = 755.00 remaining.
        """
        with app.app_context():
            add_card(seed_user, "Visa")
            savings = add_savings(seed_user)
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period, account=savings)
            _recorded(
                seed_user, row, "45.00", "Target",
                account_id=seed_user["account"].id,
            )

            cell = _cell(auth_client, row)
            assert "$45.00 / $800.00 -- $755.00 remaining -- 1 entry" in cell
            assert "on CC" not in cell
            assert _CC_BADGE not in _display_row(_list(auth_client, row), "Target")

    def test_another_card_under_a_row_on_the_card_is_on_cc(
        self, app, auth_client, seed_user,
    ):
        """Groceries on the Visa, $50.00 on the Visa and $30.00 on the Amex: $30.00 on CC.

        The Amex purchase is on a card other than its row's account, so the
        list badges it and the cell counts it; the Visa purchase is on its
        row's account, which the row's chip names once, so neither does.
        The cell's figure is the sum of what the list badges, one predicate
        for both (``movement_account.on_a_card``).  Arithmetic: 50.00 + 30.00
        = 80.00 spent, 800.00 - 80.00 = 720.00 remaining, 30.00 on CC.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            amex = add_card(seed_user, "Amex")
            period = seed_user["bootstrap_period"]
            row = _groceries(seed_user, period, account=visa)
            _recorded(seed_user, row, "50.00", "Shell")
            _recorded(seed_user, row, "30.00", "Kroger", card=amex)

            assert (
                "$80.00 / $800.00 -- $720.00 remaining -- 2 entries -- "
                "includes $30.00 on CC"
            ) in _cell(auth_client, row)
            page = _list(auth_client, row)
            assert _CC_BADGE in _display_row(page, "Kroger")
            assert _CC_BADGE not in _display_row(page, "Shell")
