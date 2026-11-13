"""Plan step ``credit_card:CC-5-4a-5`` leaf 5c-2a: the statement screen says what it withholds.

Ruling **R-CC137** (developer 2026-10-04, "Only Paid from, say why"), graded
through the statement screen's own routes: *"Checking's statement screen
does not offer Hotel while its payment is recorded on the Visa (the reconcile
panel's test, R-CC126), and says so on that screen ... Doing that marks Hotel
Paid on Checking dated today; Checking's screen then offers its payment ... A
bill's payment then changes account only through 'Paid from' ... and a match
from a page drawn before the payment moved is refused."*  What the screen
SAYS is ruling **R-CC140** (the same day, "Short", replacing R-CC137's
sentence), at the figure ruling **R-CC139** names (*"Don't display $120 if
the bill is $135."*).  The service-level half (the offer set, the refused
accept on Checking, the accept on the card) is
``test_cc5_3_settle_tender/test_the_tenders_books_and_links.py::
TestTheRowsClearingLinkStaysOnTheRowsAccount::
test_the_matcher_never_moves_a_card_payment``.

Every figure here is a made-up example.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.statement_match import StatementMatch
from app.services import entry_service, transaction_service
from app.services.statement_match import RowKind, accept_match
from app.services.transaction_service import settle_transaction
from app.utils.dates import display_today
# Pylint: ``shekel-private-module-import`` -- ``_candidates``' pricer is the
# seam the unpriceable case replaces, and the statement-match builders are
# the one way a test stages a scope and a bank line as the app does; the
# convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from app.services.statement_match import _candidates
from tests._test_helpers import (
    create_account_of_type,
    generate_row_of,
    make_expense_template,
    make_income_template,
    state_template_price,
    typed,
)
from tests.test_routes._statement_forms import (
    reconcile_form_fields,
    reconcile_offerable,
)
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    an_import,
)


def _screen(auth_client, account_id):
    """Return the account's statement screen body."""
    response = auth_client.get(f"/accounts/{account_id}/statements/reconcile")
    assert response.status_code == 200
    return response.data.decode()


def _press(auth_client, account_id, page, line_id):
    """OK *line_id*'s card on *page* and press Apply, posting what the page rendered.

    The fields are scraped from *page* -- a STALE page, in the cases that
    call this, which is the point: what a browser holding that page sends.
    """
    assert ("ok", str(line_id)) in reconcile_offerable(page), (
        "the page rendered no OK for this card, so a browser could not press it"
    )
    return auth_client.post(
        f"/accounts/{account_id}/statements/reconcile",
        data=MultiDict(
            [("csrf_token", "x")] + reconcile_form_fields(page)
            + [("ok", str(line_id))],
        ),
    )


def _press_paid(auth_client, txn_id, paid_from_id):
    """Press the full-edit popover's Paid button with 'Paid from' set to *paid_from_id*.

    Posts what the button sends: the picker it ``hx-include``s, set to an
    option the picker RENDERS, and its own ``shown_lines`` value, both
    scraped from the popover.
    """
    popover = auth_client.get(f"/transactions/{txn_id}/full-edit")
    assert popover.status_code == 200
    html = popover.data.decode()
    # Bounded to the 'Paid from' list: an unbounded ``.*?`` matched the
    # Status list's option of the same id (the leaf's delta review).
    assert re.search(
        rf'<select name="tender_account_id" id="tender-{txn_id}"'
        rf'(?:(?!</select>).)*?<option value="{paid_from_id}"',
        html, re.S,
    ), "the popover offers no such 'Paid from' account to press"
    (shown_lines,) = re.findall(
        r'hx-post="[^"]*/mark-done"[^>]*?'
        r'hx-vals=\'\{"shown_lines": "([^"]*)"\}\'',
        html, re.S,
    )
    return auth_client.post(
        f"/transactions/{txn_id}/mark-done",
        data={"tender_account_id": str(paid_from_id), "shown_lines": shown_lines},
    )


def _card(seed_user):
    """Create the owner's Rewards Card."""
    return create_account_of_type(
        seed_user, db.session, "Credit Card", "Rewards Card",
        anchor_balance=Decimal("-500.00"),
    )


def _revert(row):
    """Set *row* back to Projected through the door production reverts by."""
    transaction_service.apply_requested_status(
        row, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


def _reverted_card_paid_hotel(seed_user, period):
    """Hotel $120.00 planned on checking, marked paid from the card, set back to Projected."""
    card = _card(seed_user)
    hotel = generate_row_of(
        make_expense_template(
            db.session, seed_user, amount="120.00", name="Hotel",
            category_key="Groceries",
        ),
        period,
    )
    db.session.commit()
    settle_transaction(hotel, tender_account_id=card.id)
    db.session.commit()
    _revert(hotel)
    return hotel, card


class TestTheScreenSaysWhatItWithholds:
    """R-CC137 + R-CC140: the withheld row is named on its own account's screen."""

    def test_the_checking_screen_names_the_card_paid_bill(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The ruled sentence, the account named as the owner named it; the row is not offered."""
        with app.app_context():
            checking = seed_user["account"]
            hotel, card = _reverted_card_paid_hotel(
                seed_user, seed_periods_today[0],
            )
            body = _screen(auth_client, checking.id)

            assert (
                f"Hotel $120.00 is not listed here because the app has it as "
                f"paid from {card.name}. To change that, edit Hotel on the grid."
            ) in body
            assert [
                row for row in a_scope(seed_user).candidates.rows
                if row.transaction_id == hotel.id
            ] == []

    def test_the_note_states_what_paid_would_record_now(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-CC139: *"Don't display $120 if the bill is $135."* -- the plan edited since.

        The kept payment's stored column still reads `$120.00`; pressing
        Paid would record the plan's `$135.00`, and so does the card's own
        screen offer the payment, so that is the figure the note names.
        """
        with app.app_context():
            checking = seed_user["account"]
            hotel, card = _reverted_card_paid_hotel(
                seed_user, seed_periods_today[0],
            )
            state_template_price(hotel.template, Decimal("135.00"))
            db.session.commit()
            (payment,) = hotel.covering_movements
            assert payment.amount == Decimal("120.00"), "the column is the old figure"

            body = _screen(auth_client, checking.id)

            assert (
                f"Hotel $135.00 is not listed here because the app has it as "
                f"paid from {card.name}. To change that, edit Hotel on the grid."
            ) in body
            assert "Hotel $120.00" not in body
            (on_card,) = [
                row for row in a_scope(seed_user, card).candidates.rows
                if row.transaction_id == hotel.id
            ]
            assert on_card.cash_amount == Decimal("-135.00")

    def test_money_coming_in_reads_received_into(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-CC140's money-in sentence: a Refund received into the card, set back to Projected."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            refund = generate_row_of(
                make_income_template(
                    db.session, seed_user, amount="38.00", name="Refund",
                ),
                seed_periods_today[0],
            )
            db.session.commit()
            settle_transaction(refund, tender_account_id=card.id)
            db.session.commit()
            _revert(refund)

            body = _screen(auth_client, checking.id)

            assert (
                f"Refund $38.00 is not listed here because the app has it as "
                f"received into {card.name}. To change that, edit Refund on the "
                f"grid."
            ) in body
            (held,) = a_scope(seed_user).candidates.held_elsewhere
            assert (held.figure, held.is_income) == (Decimal("38.00"), True)

    def test_paid_from_checking_ends_the_note_and_a_match_moves_its_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The remedy, through the popover's own Paid button, then the match R-CC137 describes.

        *"press Paid in Hotel's popover with Paid from set to Checking. Doing
        that marks Hotel Paid on Checking dated today; Checking's screen then
        offers its payment, and matching it to 9/24 HOTEL -$120.00 moves the
        date to 9/24."*  The popover renders Checking among its 'Paid from'
        options, and the press posts the option a browser sends.
        """
        with app.app_context():
            checking = seed_user["account"]
            hotel, _card_account = _reverted_card_paid_hotel(
                seed_user, seed_periods_today[0],
            )
            pressed = _press_paid(auth_client, hotel.id, checking.id)

            assert pressed.status_code == 200
            db.session.expire_all()
            (payment,) = hotel.covering_movements
            assert payment.account_id == checking.id
            assert payment.settled_on == display_today()
            assert "is not listed here because" not in _screen(
                auth_client, checking.id,
            )
            scope = a_scope(seed_user)
            (offered,) = [
                row for row in scope.candidates.rows
                if row.transaction_id == hotel.id
            ]
            assert (offered.kind, offered.row_id) == (
                RowKind.SETTLEMENT, payment.id,
            )
            bank_day = display_today() - timedelta(days=2)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00",
                posted_on=bank_day, description="HOTEL",
            )
            db.session.commit()
            scope = a_scope(seed_user)

            accept_match(
                a_submission(scope, lines=[line], transactions=[hotel]), scope,
            )
            db.session.commit()

            db.session.expire_all()
            (payment,) = hotel.covering_movements
            assert (payment.account_id, payment.settled_on) == (
                checking.id, bank_day,
            )

    def test_a_typed_figure_kept_across_the_revert_is_the_figure_named(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """What Paid records honours a figure the owner typed (R-BAL61), plan edit or not.

        The lane's application of ruling **balance:R-BAL207** to the case the
        R-CC139 question never showed: Hotel paid from the card at a typed
        `$118.00`, set back to Projected, its plan edited to `$135.00`.
        The card's screen offers `-118.00` and Paid records `$118.00`, so
        the note names `$118.00`.  (The grid cell shows the plan's `$135.00`,
        which the balance counts, with the kept `$118.00` beside it.)
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            hotel = generate_row_of(
                make_expense_template(
                    db.session, seed_user, amount="120.00", name="Hotel",
                    category_key="Groceries",
                ),
                seed_periods_today[0],
            )
            db.session.commit()
            settle_transaction(
                hotel, submitted=typed(Decimal("118.00")),
                tender_account_id=card.id,
            )
            db.session.commit()
            _revert(hotel)
            state_template_price(hotel.template, Decimal("135.00"))
            db.session.commit()

            body = _screen(auth_client, checking.id)

            assert (
                f"Hotel $118.00 is not listed here because the app has it as "
                f"paid from {card.name}. To change that, edit Hotel on the grid."
            ) in body
            (on_card,) = [
                row for row in a_scope(seed_user, card).candidates.rows
                if row.transaction_id == hotel.id
            ]
            assert on_card.cash_amount == Decimal("-118.00")

            assert _press_paid(
                auth_client, hotel.id, checking.id,
            ).status_code == 200
            db.session.expire_all()
            (payment,) = hotel.covering_movements
            assert (payment.account_id, payment.amount) == (
                checking.id, Decimal("118.00"),
            )

    def test_an_envelope_holding_purchases_is_not_named_and_its_purchase_is_offered(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """'A bill paid by its own purchases stays on its own list' (R-CC126, the panel's test).

        Groceries closed at `$120.00` from the card, set back to Projected,
        then given a `$30.00` Kroger purchase on Checking (filing into it
        stays allowed, ruling **R-CC141**).  It settles from its purchases
        now, so it is not withheld for its kept card payment and not named;
        the row itself is worth nothing to the offer (its purchase is the
        figure), and Checking offers that purchase.
        """
        with app.app_context():
            checking = seed_user["account"]
            envelope = generate_row_of(
                make_expense_template(
                    db.session, seed_user, amount="300.00", name="Groceries",
                    category_key="Groceries", is_envelope=True,
                ),
                seed_periods_today[0],
            )
            db.session.commit()
            card = _card(seed_user)
            settle_transaction(
                envelope, submitted=typed(Decimal("120.00")),
                tender_account_id=card.id,
            )
            db.session.commit()
            _revert(envelope)
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")), description="Kroger",
                    purchased_on=seed_periods_today[0].start_date,
                ),
            )
            db.session.commit()

            body = _screen(auth_client, checking.id)

            assert "is not listed here because" not in body
            candidates = a_scope(seed_user).candidates
            assert candidates.held_elsewhere == ()
            (purchase,) = envelope.purchases
            (offered,) = [
                row for row in candidates.rows
                if row.kind is RowKind.PURCHASE and row.parent_id == envelope.id
            ]
            assert offered.row_id == purchase.id
            assert offered.cash_amount == Decimal("-30.00")
            assert [
                row for row in candidates.rows
                if row.transaction_id == envelope.id
            ] == []


class TestAHeldRowNothingCanPriceOrWorthNothing:
    """The two rows a held one may not be named as: unpriceable, and worth `$0.00`."""

    def test_a_held_row_worth_nothing_is_neither_named_nor_offered(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A `$0.00` row is offered on no screen, so it is not 'not listed here because' anything.

        The lane's decision under ruling **balance:R-BAL207** (the edge case
        the developer cannot see): naming it would give a reason that is not
        why it is missing -- with its payment on Checking it would be
        missing all the same.
        """
        with app.app_context():
            checking = seed_user["account"]
            hotel, card = _reverted_card_paid_hotel(
                seed_user, seed_periods_today[0],
            )
            state_template_price(hotel.template, Decimal("0.00"))
            db.session.commit()

            body = _screen(auth_client, checking.id)

            assert "is not listed here because" not in body
            candidates = a_scope(seed_user).candidates
            assert candidates.held_elsewhere == ()
            assert candidates.unpriceable == ()
            for account in (checking, card):
                assert [
                    row for row in a_scope(seed_user, account).candidates.rows
                    if row.transaction_id == hotel.id
                ] == []

    def test_a_held_row_nothing_can_price_is_counted_not_named(
        self, app, seed_user, seed_periods_today, monkeypatch,
    ):
        """No amount model, no figure: among the unpriceable, as any row here is.

        Unreachable from today's data (every production row still owns its
        figure; ``AmountUnresolvable`` is live from the first per-kind
        cutover), so the pricer is replaced for the one payment -- the way
        ``test_batch`` reaches its own unreachable arms -- rather than
        leaving the arm ungraded.
        """
        with app.app_context():
            hotel, _card_account = _reverted_card_paid_hotel(
                seed_user, seed_periods_today[0],
            )
            (payment,) = hotel.covering_movements
            priced = _candidates.settlement_price
            monkeypatch.setattr(
                _candidates, "settlement_price",
                lambda entry, basis: (
                    None if entry.id == payment.id else priced(entry, basis)
                ),
            )

            candidates = a_scope(seed_user).candidates

            assert candidates.held_elsewhere == ()
            assert (RowKind.TRANSACTION, hotel.id) in candidates.unpriceable


class TestAPageDrawnBeforeThePaymentMovedIsRefused:
    """R-CC137: *"a match from a page drawn before the payment moved is refused"*, by name."""

    def test_a_checking_page_drawn_before_the_card_paid_and_reverted_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Drawn while plain Projected, pressed after a card-pay and revert: nothing lands."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            hotel = generate_row_of(
                make_expense_template(
                    db.session, seed_user, amount="120.00", name="Hotel",
                    category_key="Groceries",
                ),
                seed_periods_today[0],
            )
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-120.00",
                posted_on=seed_periods_today[0].start_date + timedelta(days=1),
                description="HOTEL",
            )
            db.session.commit()
            page = _screen(auth_client, checking.id)
            assert [
                value for name, value in reconcile_form_fields(page)
                if name == f"rows-{line.id}"
            ] == [f"transaction:{hotel.id}:-120.00:{hotel.version_id}"], (
                "the page did not propose Hotel for the line, so this grades nothing"
            )
            settle_transaction(hotel, tender_account_id=card.id)
            db.session.commit()
            _revert(hotel)
            (kept,) = hotel.covering_movements
            before = (kept.account_id, kept.settled_on, kept.version_id)

            response = _press(auth_client, checking.id, page, line.id)

            assert response.status_code == 200
            assert "no longer available" in response.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.query(StatementMatch).count() == 0
            (kept,) = hotel.covering_movements
            assert (kept.account_id, kept.settled_on, kept.version_id) == before
            assert kept.account_id == card.id and kept.settled_on is None
            assert hotel.status_id == ref_cache.status_id(StatusEnum.PROJECTED)

    def test_a_card_page_drawn_before_paid_from_checking(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The card offered its kept payment; Paid from Checking moved it; nothing lands."""
        with app.app_context():
            checking = seed_user["account"]
            hotel, card = _reverted_card_paid_hotel(
                seed_user, seed_periods_today[0],
            )
            (kept,) = hotel.covering_movements
            line = a_bank_line(
                seed_user, an_import(seed_user, card), amount="-120.00",
                posted_on=seed_periods_today[0].start_date + timedelta(days=1),
                description="HOTEL",
            )
            db.session.commit()
            page = _screen(auth_client, card.id)
            assert [
                value for name, value in reconcile_form_fields(page)
                if name == f"rows-{line.id}"
            ] == [
                f"settlement:{kept.id}:-120.00:"
                f"{kept.version_id + hotel.version_id}",
            ], "the card's page did not propose the kept payment, so this grades nothing"
            settle_transaction(hotel, tender_account_id=checking.id)
            db.session.commit()
            (moved,) = hotel.covering_movements
            after_paid = (moved.id, moved.account_id, moved.settled_on)
            assert after_paid == (kept.id, checking.id, display_today())

            response = _press(auth_client, card.id, page, line.id)

            assert response.status_code == 200
            assert "no longer available" in response.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.query(StatementMatch).count() == 0
            (moved,) = hotel.covering_movements
            assert (moved.id, moved.account_id, moved.settled_on) == after_paid
