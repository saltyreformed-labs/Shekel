"""Plan step ``credit_card:CC-5-2``, the GRID half: the picker, the name, the chip.

The purchase door took an account of its own in the first half of this step
(``tests/test_services/test_cc5_2_purchase_account_door.py``).  This module
grades the SURFACE that writes it and the surfaces that read it:

* the add-purchase form's **account picker** renders from the door's own
  tuple (:func:`app.services.cash_flow_set.purchase_accounts`, ruling
  **R-CC37**) on every surface that draws an envelope's entry list -- the
  grid's inline mobile card, the HTMX refresh, the companion page and the
  mobile-card fragment -- and only when there is a choice (ruling
  **R-CC34**);
* the entry list **names the account** a purchase moved through when it is
  not the row's own (ruling **R-CC15**);
* the **account chip** on the desktop cell and the mobile card is ONE rule
  over the row and its movements, less the balance line's account
  (``account_chips`` in ``grid/_grid_row_macros.html``), so a card swipe in
  a checking envelope chips the card from checking's grid and nothing extra
  from the card's;
* the chip's account read costs **no query** for a movement on a member of
  the owner's set, measured, so the grid's row query gains no eager load.

Every surface test plants the same world: the owner's checking account, one
card, one checking envelope in today's paycheck.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from app.extensions import db
from app.models.account import Account
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service
from app.services.account_resolver import resolve_owner_cash_flow_set
from app.services.cash_flow_set import purchase_accounts
from app.utils.dates import display_today
from tests._test_helpers import (
    capture_sql_statements,
    create_account_of_type,
    generate_row_of,
    make_expense_template,
    typed,
)

_CHIP = 'class="flag-chip'
_PICKER = '<select name="account_id" aria-label="Paid from"'


def _card(seed_user, name="Rewards Card"):
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"),
    )


def _envelope(seed_user, period, *, companion_visible=False, account=None):
    """A ``$500`` Groceries envelope in *period*, on checking unless told."""
    template = make_expense_template(
        db.session, seed_user, amount="500.00", name="Groceries",
        category_key="Groceries", is_envelope=True,
        companion_visible=companion_visible,
        **({} if account is None else {"account": account}),
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _swipe(row, user_id, account_id=None, amount="60.00", description="Kroger"):
    """Record a purchase through the door, dated today so it sits in its paycheck."""
    return entry_service.create_entry(
        row.id, user_id,
        entry_service.EntryDetails(
            figure=typed(Decimal(amount)), description=description,
            purchased_on=display_today(), account_id=account_id,
        ),
    )


def _inline_list(html, txn_id, prefix="tp"):
    """The inline entries partial of one mobile card, through its add form."""
    start = html.index(f'id="entry-list-{prefix}-{txn_id}"')
    return html[start:html.index("</form>", start) + len("</form>")]


def _cell(html, txn_id):
    """The desktop cell of one row, to its ``</td>``."""
    start = html.index(f'id="txn-cell-{txn_id}"')
    return html[start:html.index("</td>", start)]


def _card_header(html, txn_id, prefix="tp"):
    """The mobile card's header, before its expansion (and its entry list)."""
    start = html.index(f'id="card-{prefix}-{txn_id}"')
    return html[start:html.index("mobile-card-expansion", start)]


def _display_row(html, txn_id, entry_id):
    """One purchase's display row: from its ``<div`` to its own edit control."""
    edit = f"/transactions/{txn_id}/entries?editing={entry_id}"
    end = html.index(edit)
    start = html.rindex('<div class="d-flex justify-content-between align-items-center', 0, end)
    return html[start:end]


def _picker(block):
    """The picker's ``<select>`` element in *block*, or ``None``."""
    match = re.search(r"<select name=\"account_id\".*?</select>", block, re.S)
    return None if match is None else match.group(0)


def _options(select):
    """``[(value, selected, label), ...]`` of one ``<select>``, in order."""
    return [
        (int(value), bool(selected), label)
        for value, selected, label in re.findall(
            r'<option value="(\d+)"( selected)?>([^<]*)</option>', select,
        )
    ]


class TestPurchaseAccountsIsTheDoorsOwnTuple:
    """``cash_flow_set.purchase_accounts``: one spelling, the door's and the picker's."""

    def test_the_rows_own_account_first_then_the_sets_other_members(
        self, app, seed_user, seed_periods_today,
    ):
        """Checking row: (Checking, Rewards Card).  Card row: (Rewards Card, Checking)."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            on_checking = _envelope(seed_user, seed_periods_today[4])
            on_card = _envelope(seed_user, seed_periods_today[4], account=card)
            owner_set = resolve_owner_cash_flow_set(seed_user["user"].id)
            assert owner_set.member_ids == (checking.id, card.id)

            assert [a.id for a in purchase_accounts(owner_set, on_checking)] == [
                checking.id, card.id,
            ]
            assert [a.id for a in purchase_accounts(owner_set, on_card)] == [
                card.id, checking.id,
            ]

    def test_a_row_outside_the_set_offers_its_own_account_then_the_set(
        self, app, seed_user, seed_periods_today,
    ):
        """A Savings envelope: (Savings, Checking, Rewards Card) -- what the door admits.

        Ruling R-CC37's two halves in one tuple: the row's own account is
        always admitted (first, the default), and so is every member of the
        owner's set -- so a hotel deposit paid from checking against a
        savings envelope can say so.  The developer confirmed this reading
        on 2026-09-21 (ruling **R-CC39**, "Union") against R-CC34's
        narrower parenthetical and the (1/2) handoff's "no choice outside
        the set".  The DOOR test
        grades the same tuple's refusals
        (``test_cc5_2_purchase_account_door.py``).
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            row = _envelope(seed_user, seed_periods_today[4], account=savings)
            owner_set = resolve_owner_cash_flow_set(seed_user["user"].id)
            assert savings.id not in owner_set.member_ids

            assert [a.id for a in purchase_accounts(owner_set, row)] == [
                savings.id, checking.id, card.id,
            ]

    def test_no_set_means_the_rows_own_account_alone(
        self, app, seed_user, seed_periods_today,
    ):
        """An owner with no grid-eligible account: the one choice is the row's."""
        with app.app_context():
            row = _envelope(seed_user, seed_periods_today[4])
            assert [a.id for a in purchase_accounts(None, row)] == [row.account_id]

    def test_the_doors_read_the_pickers_function(self, app):
        """Source gate for rule 14: both doors test membership in ``purchase_accounts``.

        The door's inline spelling (``account.id == txn.account_id or account.id
        in cash_flow.member_ids``) and the picker's tuple AGREE today, which is
        exactly why no behavioural test can tell one function from two
        spellings -- so the census is of the source.  **The spelling moved at
        plan step ``credit_card:CC-5-3``** (the census followed the leaf):
        the ONE gate is ``movement_account.admitted_movement_account_id``,
        which reads the tuple exactly once, and every writer of a movement
        onto another account -- the purchase door, the settle verb's tender
        and the settled row's "Paid from" correction -- calls it and spells
        nothing of its own.  Delete the call from any of the three, or
        re-spell the predicate in one, and this fails while every write
        still lands.  The developer confirmed this re-expression on
        2026-09-21 (rule 5): "The census follows the leaf: one shared gate,
        both doors graded, no door spells the rule itself."
        """
        services = Path(app.root_path) / "services"
        gate = (services / "movement_account.py").read_text(encoding="utf-8")
        assert gate.count("purchase_accounts(cash_flow, txn)") == 1
        writers = (
            "entry_service/_doors.py",
            "transaction_service/_settle.py",
            "transaction_service/_door.py",
        )
        for door in writers:
            source = (services / door).read_text(encoding="utf-8")
            assert "admitted_movement_account_id(" in source, door
            assert "purchase_accounts(" not in source, door
            assert "cash_flow.member_ids" not in source, door

    def test_the_door_admits_exactly_the_tuple(
        self, app, seed_user, seed_periods_today,
    ):
        """Every member of the tuple is writable; the door reads the same function.

        The parity R-CC37 rules is graded by WRITING through the door with
        each account the tuple names, on a row outside the set -- the case
        where "the row's own" and "the set" are three different accounts.
        """
        with app.app_context():
            _card(seed_user)
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            row = _envelope(seed_user, seed_periods_today[4], account=savings)
            offered = purchase_accounts(
                resolve_owner_cash_flow_set(seed_user["user"].id), row,
            )
            assert len(offered) == 3
            for account in offered:
                written = _swipe(row, seed_user["user"].id, account_id=account.id)
                assert written.account_id == account.id
            db.session.commit()
            assert sorted(
                e.account_id for e in
                db.session.query(TransactionEntry).filter_by(transaction_id=row.id)
            ) == sorted(a.id for a in offered)


class TestThePickerRendersOnEverySurface:
    """The ``<select name="account_id">`` on the four surfaces, and its absence."""

    def test_the_grids_inline_form_offers_the_card_with_the_rows_account_selected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods_today[4])
            ids = (checking.id, card.id, row.id)
            response = auth_client.get("/grid?periods=1&offset=0")
        checking_id, card_id, row_id = ids
        assert response.status_code == 200
        select = _picker(_inline_list(response.get_data(as_text=True), row_id))
        assert select is not None
        assert _options(select) == [
            (checking_id, True, "Checking"),
            (card_id, False, "Rewards Card"),
        ]

    def test_without_a_card_the_form_carries_no_account_control(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling R-CC34: one choice, no control -- the door's ``None`` arm files it.

        Graded on the whole inline partial and on the HTMX refresh, so the
        production population (no card) renders the form it always did.
        """
        with app.app_context():
            row = _envelope(seed_user, seed_periods_today[4])
            row_id = row.id
            page = auth_client.get("/grid?periods=1&offset=0")
            refresh = auth_client.get(f"/transactions/{row_id}/entries")
        assert page.status_code == 200 and refresh.status_code == 200
        inline = _inline_list(page.get_data(as_text=True), row_id)
        assert 'hx-post="/transactions/' in inline and "/entries" in inline
        assert 'name="account_id"' not in inline
        assert 'name="account_id"' not in refresh.get_data(as_text=True)

    def test_the_htmx_refresh_carries_the_same_select_the_page_does(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``GET /transactions/<id>/entries`` -- what replaces the list after every act."""
        with app.app_context():
            _card(seed_user)
            row = _envelope(seed_user, seed_periods_today[4])
            row_id = row.id
            page = auth_client.get("/grid?periods=1&offset=0")
            refresh = auth_client.get(f"/transactions/{row_id}/entries")
        assert refresh.status_code == 200
        on_page = _picker(_inline_list(page.get_data(as_text=True), row_id))
        on_refresh = _picker(refresh.get_data(as_text=True))
        assert on_page is not None
        assert on_refresh == on_page

    def test_the_companion_page_carries_the_owners_picker(
        self, app, seed_user, seed_periods_today, companion_client,
    ):
        """A companion records the OWNER's purchase (R-CC11): the owner's card is offered.

        The companion owns no account, so a picker resolved for the requester
        would be empty and the control would vanish; it is resolved for the
        row's owner.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            row = _envelope(
                seed_user, seed_periods_today[4], companion_visible=True,
            )
            ids = (checking.id, card.id, row.id, seed_periods_today[4].id)
            response = companion_client.get(f"/companion/period/{ids[3]}")
        checking_id, card_id, row_id, _ = ids
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        select = _picker(_inline_list(html, row_id))
        assert select is not None
        assert _options(select) == [
            (checking_id, True, "Checking"),
            (card_id, False, "Rewards Card"),
        ]
        # No balance line on this page, so no row chip -- the picker is a
        # different question and has an answer here.
        assert _CHIP not in _card_header(html, row_id)

    def test_the_companions_refresh_carries_the_owners_picker_too(
        self, app, seed_user, seed_periods_today, companion_client,
    ):
        """``GET /transactions/<id>/entries`` as the companion: resolved for ``txn.user_id``.

        The refresh is what replaces the companion's list after every act
        they take; resolved for ``current_user`` it would find no account
        and hide the control the page had just drawn.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            row = _envelope(
                seed_user, seed_periods_today[4], companion_visible=True,
            )
            ids = (checking.id, card.id, row.id)
            response = companion_client.get(f"/transactions/{ids[2]}/entries")
        checking_id, card_id, _ = ids
        assert response.status_code == 200
        select = _picker(response.get_data(as_text=True))
        assert select is not None
        assert _options(select) == [
            (checking_id, True, "Checking"),
            (card_id, False, "Rewards Card"),
        ]

    def test_on_an_outside_overrides_grid_the_picker_is_still_the_owners(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?account_id=<savings>``: the view collapses to Savings; the picker does not.

        The page shows Savings' rows behind Savings' balance line, and its
        ``cash_flow`` is the one-account view.  A Savings envelope's picker
        still offers (Savings, Checking, Rewards Card), because what a
        purchase may name is the OWNER's set and the door admits exactly
        that -- read from ``owner_cash_flow``, the same walk's other half.
        Rendered from the view instead, the control would vanish on this
        page and appear on the refresh.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            row = _envelope(seed_user, seed_periods_today[4], account=savings)
            ids = (checking.id, card.id, savings.id, row.id)
            response = auth_client.get(
                f"/grid?periods=1&offset=0&account_id={ids[2]}",
            )
        checking_id, card_id, savings_id, row_id = ids
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        select = _picker(_inline_list(html, row_id))
        assert select is not None
        assert _options(select) == [
            (savings_id, True, "Rainy Day"),
            (checking_id, False, "Checking"),
            (card_id, False, "Rewards Card"),
        ]

    def test_the_mobile_card_fragment_carries_the_picker(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A card-targeted refusal re-renders the still-projected card with its form.

        ``settled_amount=-1`` is refused at the schema tier (422) and the
        response is the card itself (``render=mobile_card``), add form
        included -- the one path ``render_mobile_card`` draws a picker on,
        since a SETTLED card withdraws the form.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods_today[4])
            ids = (checking.id, card.id, row.id)
            response = auth_client.post(
                f"/transactions/{ids[2]}/mark-done",
                data={
                    "render": "mobile_card", "card_prefix": "tp",
                    "can_edit": "1", "settled_amount": "-1",
                },
            )
        checking_id, card_id, row_id = ids
        assert response.status_code == 422
        body = response.get_data(as_text=True)
        assert f'id="card-tp-{row_id}"' in body
        select = _picker(_inline_list(body, row_id))
        assert select is not None
        assert _options(select) == [
            (checking_id, True, "Checking"),
            (card_id, False, "Rewards Card"),
        ]

    def test_a_purchase_posted_through_the_picker_lands_on_the_card_and_is_named(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Round trip: the form's control, the door, the list's name.

        The refreshed list names the card beside the swipe and nothing
        beside a purchase on the row's own account (ruling R-CC15).
        """
        with app.app_context():
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods_today[4])
            ids = (card.id, row.id)
            today = display_today().isoformat()
            swipe = auth_client.post(
                f"/transactions/{ids[1]}/entries",
                data={
                    "amount": "60.00", "description": "Kroger",
                    "purchased_on": today, "account_id": str(ids[0]),
                },
            )
            own = auth_client.post(
                f"/transactions/{ids[1]}/entries",
                data={
                    "amount": "15.00", "description": "Pharmacy",
                    "purchased_on": today,
                },
            )
        card_id, row_id = ids
        assert swipe.status_code == 200 and own.status_code == 200
        with app.app_context():
            entries = {
                e.description: (e.id, e.account_id)
                for e in db.session.query(TransactionEntry).filter_by(
                    transaction_id=row_id,
                )
            }
        assert {d: a for d, (_, a) in entries.items()} == {
            "Kroger": card_id, "Pharmacy": seed_user["account"].id,
        }
        html = own.get_data(as_text=True)
        # Each display row sliced by ITS OWN edit control: the two purchases
        # share a date, and ``entries`` orders by ``purchased_on`` alone, so
        # their order on the page is not one this test may lean on.
        kroger = _display_row(html, row_id, entries["Kroger"][0])
        assert kroger.count(_CHIP) == 1
        assert 'title="On Rewards Card">Rewards Card</span>' in kroger
        assert _CHIP not in _display_row(html, row_id, entries["Pharmacy"][0])


class TestTheChipIsOneRuleOverTheRowAndItsMovements:
    """``account_chips``: the row's account and its movements' accounts, less the balance line's."""

    @staticmethod
    def _world(seed_user, periods):
        """Checking envelope in today's paycheck holding one card swipe and one checking purchase."""
        checking = seed_user["account"]
        card = _card(seed_user)
        row = _envelope(seed_user, periods[4])
        _swipe(row, seed_user["user"].id, account_id=card.id)
        _swipe(row, seed_user["user"].id, amount="15.00", description="Pharmacy")
        db.session.commit()
        return checking.id, card.id, row.id

    def test_from_checking_grid_the_cell_and_the_card_chip_the_card_once(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Seen from checking: the row is on the line; its swipe is on the card.

        ONE chip on the desktop cell and ONE on the mobile card's header,
        both ``Rewards Card``; the checking purchase adds none.  The card's
        expansion carries the entry list's own name beside the swipe, which
        is the list's marker and not the header's chip, so the header is
        graded before the expansion.
        """
        with app.app_context():
            _, _, row_id = self._world(seed_user, seed_periods_today)
            response = auth_client.get("/grid?periods=1&offset=0")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        cell = _cell(html, row_id)
        assert cell.count(_CHIP) == 1, cell
        assert 'title="On Rewards Card">Rewards Card</span>' in cell
        # A caption UNDER the amount, in its own block, as CC-4-2 drew it.
        assert '<div class="mt-1"><span class="flag-chip flag-chip--neutral' in cell
        header = _card_header(html, row_id)
        assert header.count(_CHIP) == 1, header
        assert 'title="On Rewards Card">Rewards Card</span>' in header

    def test_from_the_cards_grid_the_row_chips_checking_and_the_swipe_chips_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?account_id=<card>``: the balance line is the card, so the swipe is home.

        The row itself is on checking, so it carries CC-4-2's chip; the
        swipe on the card carries none.  Exactly one chip, ``Checking``.
        """
        with app.app_context():
            _, card_id, row_id = self._world(seed_user, seed_periods_today)
            response = auth_client.get(
                f"/grid?periods=1&offset=0&account_id={card_id}",
            )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        cell = _cell(html, row_id)
        assert cell.count(_CHIP) == 1, cell
        assert 'title="On Checking">Checking</span>' in cell
        assert "Rewards Card" not in cell
        header = _card_header(html, row_id)
        assert header.count(_CHIP) == 1, header
        assert 'title="On Checking">Checking</span>' in header

    def test_a_row_whose_money_moved_on_the_line_chips_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A checking envelope with a checking purchase, seen from checking: no bytes.

        The macro's empty case, on both surfaces, with a card in the set --
        so the absence is the rule's answer and not the absence of a card.
        """
        with app.app_context():
            _card(seed_user)
            row = _envelope(seed_user, seed_periods_today[4])
            _swipe(row, seed_user["user"].id, amount="15.00", description="Pharmacy")
            db.session.commit()
            row_id = row.id
            response = auth_client.get("/grid?periods=1&offset=0")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert _CHIP not in _cell(html, row_id)
        assert _CHIP not in _card_header(html, row_id)

    def test_two_swipes_on_the_card_are_one_chip(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Distinct accounts, not movements: two card swipes chip the card once."""
        with app.app_context():
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods_today[4])
            _swipe(row, seed_user["user"].id, account_id=card.id)
            _swipe(
                row, seed_user["user"].id, account_id=card.id,
                amount="20.00", description="Gas",
            )
            db.session.commit()
            row_id = row.id
            response = auth_client.get("/grid?periods=1&offset=0")
        html = response.get_data(as_text=True)
        assert _cell(html, row_id).count(_CHIP) == 1
        assert _card_header(html, row_id).count(_CHIP) == 1

    def test_the_cell_fragment_draws_the_same_chips_the_page_does(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``GET /transactions/<id>/cell`` agrees byte for byte on the chip block."""
        with app.app_context():
            _, _, row_id = self._world(seed_user, seed_periods_today)
            page = auth_client.get("/grid?periods=1&offset=0").get_data(as_text=True)
            fragment = auth_client.get(f"/transactions/{row_id}/cell")
        assert fragment.status_code == 200
        body = fragment.get_data(as_text=True)
        block = re.search(r"<div class=\"mt-1\">.*?</div>", body).group(0)
        assert block.count(_CHIP) == 1
        assert block in _cell(page, row_id)

    def test_the_three_chip_sites_read_one_macro(self, app):
        """Source census: the chip markup is spelled once, in the macro.

        The three inline copies CC-4-2 left are gone: the macro file holds
        the one ``flag-chip--neutral`` literal (the macro's), and the cell
        template holds none.  A copy pasted back in would render right on
        its surface and diverge from the other two the day the rule moves.
        """
        markup = 'class="flag-chip flag-chip--neutral'
        templates = Path(app.root_path) / "templates/grid"
        macros = (templates / "_grid_row_macros.html").read_text(encoding="utf-8")
        cell = (templates / "_transaction_cell.html").read_text(encoding="utf-8")
        assert macros.count(markup) == 1
        assert markup not in cell
        assert macros.count("account_chips(") == 3  # the definition + two callers
        assert cell.count("account_chips(") == 1


def _folded(text):
    """Return *text* with each expanded ``IN (...)`` bind list folded to one spelling.

    SQLAlchemy renders ``column.in_(ids)`` as ``IN (%(name_1)s::INTEGER,
    %(name_2)s::INTEGER, ...)`` -- one bind per id, each with the bind cast
    the psycopg 3 dialect writes (psycopg2's wrote none; plan step
    balance:X-dj) -- so the SAME statement over more ids has a longer text.
    Folded, two renders' statements compare as statements rather than as id
    counts (plan step credit_card:CC-5-4a-5b).  The cast is optional and
    must repeat on every bind of one list.
    """
    return re.sub(
        r"IN \(%\((\w+?)_\d+\)s((?:::[\w ]+(?:\([\d, ]+\))?)?)"
        r"(?:, %\(\1_\d+\)s\2)*\)",
        r"IN (%(\1_*)s\2)",
        text,
    )


class TestTheChipsAccountReadCostsNoQuery:
    """The eager-load measurement: a movement on a set member reads its account from the identity map."""

    @staticmethod
    def _statements_for_grid(auth_client, url="/grid?periods=1&offset=0"):
        response, statements = capture_sql_statements(lambda: auth_client.get(url))
        assert response.status_code == 200
        return statements

    def test_a_card_swipe_adds_no_statement_to_the_grid_render(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Same render, with and without the swipe: the same statement count.

        ``TransactionEntry.account`` is a lazy many-to-one (its ``joined``
        load widened the fold's ``_movements_of`` at CC-5-1's review).  A
        lazy many-to-one checks the identity map first, and the card is
        already in it -- ``resolve_cash_flow_set`` loaded it for the page --
        so the chip's ``entry.account.name`` costs nothing.  If it cost a
        query the count would rise by one here, and this is what decides
        that ``routes/grid/_items.py`` gains no ``selectinload`` on the
        movement's account.
        """
        with app.app_context():
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods_today[4])
            _swipe(row, seed_user["user"].id, amount="15.00", description="Pharmacy")
            db.session.commit()
            before = self._statements_for_grid(auth_client)
            _swipe(row, seed_user["user"].id, account_id=card.id)
            db.session.commit()
            after = self._statements_for_grid(auth_client)
        assert len(after) == len(before), (
            f"{len(before)} statements without the swipe, {len(after)} with it"
        )

    def test_a_swipe_on_an_archived_card_reads_its_account_once_per_render(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The bound on the other side: an account OUTSIDE the set loads once, not per movement.

        A card archived after its swipes leaves the set, so nothing on the
        page loaded it; the chip's first read is one indexed query and the
        second swipe's is an identity-map hit.  Two swipes, one statement
        more than the render without them.

        **Both renders see the SAME archived card**: archiving changes the
        set and with it the page's own statement count (53 to 48 on the
        first cut of this test, which compared a live card to an archived
        one and read the lazy load as a saving), so the card is archived
        before EACH render and un-archived only to record the swipes, which
        the door refuses on an archived account.
        """
        with app.app_context():
            card = _card(seed_user)
            row = _envelope(seed_user, seed_periods_today[4])
            _swipe(row, seed_user["user"].id, amount="15.00", description="Pharmacy")
            db.session.commit()
            card_id = card.id
            assert auth_client.post(f"/accounts/{card_id}/archive").status_code == 302
            assert db.session.get(Account, card_id).is_active is False
            before = self._statements_for_grid(auth_client)

            assert auth_client.post(f"/accounts/{card_id}/unarchive").status_code == 302
            _swipe(row, seed_user["user"].id, account_id=card_id)
            _swipe(
                row, seed_user["user"].id, account_id=card_id,
                amount="20.00", description="Gas",
            )
            db.session.commit()
            assert auth_client.post(f"/accounts/{card_id}/archive").status_code == 302
            assert db.session.get(Account, card_id).is_active is False
            after = self._statements_for_grid(auth_client)
        # Graded by the STATEMENT, not the count alone (the review of this
        # leaf): the one extra statement is the account's load by primary
        # key, bound to the card's id -- anything else with the same count
        # would be a per-render read this test was misattributing.
        #
        # An expanded ``IN (...)`` bind list is FOLDED before the texts are
        # compared (plan step credit_card:CC-5-4a-5b, ruling R-CC133 under
        # rule 5, "Fold id lists"): the purchase list's one read of which
        # purchases a match names binds every purchase id on the page, so its
        # text grows with them while it stays ONE statement per render --
        # measured 1 id before the swipes and 3 after.  The count and the
        # account-load attribution below are unchanged.
        seen = [_folded(text) for text, _ in before]
        extra = [
            (text, params) for text, params in after
            if _folded(text) not in seen
        ]
        assert len(after) == len(before) + 1 and len(extra) == 1, (
            f"{len(before)} statements without the swipes, {len(after)} with two "
            f"on an archived card; extra by text: {[t[:80] for t, _ in extra]}"
        )
        text, params = extra[0]
        assert text.startswith("SELECT budget.accounts.") and "WHERE budget.accounts.id = " in text
        assert dict(params) == {"pk_1": card_id}
