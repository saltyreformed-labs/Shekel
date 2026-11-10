"""Plan step ``credit_card:CC-5-4a-5`` leaf 5c-1: the reconcile panel names what a tick frees.

Three rulings, graded through the panel's own route as a browser drives it
(every number and hidden input the form renders, plus the ticked checkboxes):

* **R-CC125** (developer 2026-09-30, "Refuse it now"): *"The panel refuses a
  $0.00 payment today ... 'Hotel: a payment can't be $0.00. If it wasn't
  paid, leave it unticked or cancel it on the grid.' Nothing changes"* --
  the row box and the transfer box alike.  **R-CC136** (developer
  2026-10-04, "Deposit wording", leaf 5c-2a) words it for money coming in:
  *"'Paycheck: a deposit can't be $0.00. If it wasn't received, leave it
  unticked or cancel it on the grid.'"*
* **R-CC126** (developer 2026-09-30, "Only the card offers it"): *"Checking's
  list leaves it out ... so reconciling never moves a payment."*
* **R-CC76** with **R-CC127** (developer 2026-09-23 / 2026-09-30): the panel
  names what a tick would free before the press, from the same read it acts
  on, and posts back what it named; *"on the reconcile panel, one out-of-date
  row means nothing on it saves"*.  The one tick left that frees a line is an
  envelope settling FROM its purchases while holding the payment a revert
  kept, and its caption is the shared clause (the coordinator's application
  of R-CC56 / R-CC80, 2026-10-04): *"Closing it from its purchases withdraws
  1 accepted match, so 1 bank line is unexplained again on your statement
  screen: ..."*.

Every figure and bank line here is a made-up example.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from sqlalchemy import event
from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import entry_service, statement_match, transaction_service
from app.services.statement_match import accept_match
from app.services.transaction_service import settle_transaction
from app.utils.log_events import EVT_STATEMENT_MATCH_WITHDRAWN
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    generate_row_of,
    make_expense_template,
    make_income_template,
    typed,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    an_import,
)

#: One ``<input ...>`` tag, to read the controls the panel's form renders.
_INPUT = re.compile(r"<input\b[^>]*>", re.S)

#: R-CC125's refusal for the made-up Hotel bill, as the panel HTML-escapes it.
_HOTEL_REFUSED = (
    "Hotel: a payment can&#39;t be $0.00. If it wasn&#39;t paid, leave it "
    "unticked or cancel it on the grid."
)


def _attr(tag, name):
    """Return attribute *name* of an ``<input>`` *tag*, or ``None``."""
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    return None if match is None else match.group(1)


def _payload(body, ticks, typed_boxes=None):
    """Return what a browser submits for the panel with *ticks* checked.

    Every number input (an amount box) and every hidden input (a caption's
    posted lines) is submitted as rendered, whatever is ticked; a checkbox
    only when its ``(name, value)`` is in *ticks*.  *typed_boxes* overrides
    an amount box's value by field name -- what the owner typed over the
    prefill.
    """
    overrides = typed_boxes or {}
    payload = []
    for tag in _INPUT.findall(body):
        name, value, kind = _attr(tag, "name"), _attr(tag, "value"), _attr(tag, "type")
        if kind == "number":
            payload.append((name, overrides.get(name, value)))
        elif kind == "hidden":
            payload.append((name, value))
        elif kind == "checkbox" and (name, value) in ticks:
            payload.append((name, value))
    return MultiDict(payload)


def _panel(auth_client, account_id):
    """Return the account's reconcile panel body."""
    response = auth_client.get(f"/accounts/{account_id}/reconcile")
    assert response.status_code == 200
    return response.data.decode()


def _post(auth_client, account_id, payload):
    """POST the panel's form."""
    return auth_client.post(f"/accounts/{account_id}/reconcile", data=payload)


def _true_up(auth_client, account_id, balance="5000.00"):
    """Assert today's balance through the real PATCH route: the statement the panel reconciles."""
    response = auth_client.patch(
        f"/accounts/{account_id}/true-up", data={"anchor_balance": balance},
    )
    assert response.status_code == 200


def _card(seed_user):
    """An active Credit Card account whose books open before any day used here."""
    return create_account_of_type(
        seed_user, db.session, "Credit Card", "Rewards Card",
        anchor_balance=Decimal("-500.00"),
    )


def _row(seed_user, period, name, amount, *, is_envelope=False):
    """The engine's one row of a new every-paycheck definition on checking, in *period*."""
    template = make_expense_template(
        db.session, seed_user, amount=amount, name=name,
        category_key="Groceries", is_envelope=is_envelope,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _match(seed_user, txn, amount, description, account=None):
    """Match *txn*'s payment to a new bank line on *account*; return ``(line, act id)``.

    The row is settled, so the act names its covering movement (ruling
    R-CC43).  The line posts on the settle's own day, the user's today.
    """
    line = a_bank_line(
        seed_user, an_import(seed_user, account), amount=amount,
        posted_on=txn.settled_on, description=description,
    )
    db.session.commit()
    scope = a_scope(seed_user, account)
    accepted = accept_match(
        a_submission(scope, lines=[line], transactions=[txn]), scope,
    )
    db.session.commit()
    return line, accepted.match_id


def _revert(txn):
    """Reopen *txn* through the door production reverts by: its payment is KEPT, un-dated."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


def _matched_reverted_hotel(seed_user, period):
    """R-CC125's example: Hotel $120.00 paid, matched to HOTEL -$120.00, set back to Projected."""
    hotel = _row(seed_user, period, "Hotel", "120.00")
    settle_transaction(hotel)
    db.session.commit()
    line, match_id = _match(seed_user, hotel, "-120.00", "HOTEL")
    _revert(hotel)
    return hotel, line, match_id


def _kept_payment_envelope(
    seed_user, period, *, name="Groceries", close="120.00", matched=True,
):
    """An envelope closed at a typed *close* figure, matched, reopened, then given a $30.00 purchase.

    It settles FROM its purchase now, and that ``purchases`` record takes the
    payment the revert kept (``status_seam._covering._withdraw``):
    the one panel tick left that can free a bank line.
    """
    envelope = _row(seed_user, period, name, "300.00", is_envelope=True)
    settle_transaction(envelope, submitted=typed(Decimal(close)))
    db.session.commit()
    line = match_id = None
    if matched:
        line, match_id = _match(seed_user, envelope, f"-{close}", name.upper())
    _revert(envelope)
    entry_service.create_entry(
        envelope.id, seed_user["user"].id,
        entry_service.EntryDetails(
            figure=typed(Decimal("30.00")), description="Kroger",
            purchased_on=period.start_date,
        ),
    )
    db.session.commit()
    return envelope, line, match_id


def _status(txn_id):
    """Return the row's status id, re-read."""
    db.session.expire_all()
    return db.session.get(Transaction, txn_id).status_id


class _Withdrawals(logging.Handler):
    """Collect the withdrawal module's EVT_STATEMENT_MATCH_WITHDRAWN events for one block."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.records = []
        self._logger = logging.getLogger("app.services.match_withdrawal")
        self._prior_level = self._logger.level

    def emit(self, record):
        if getattr(record, "event", None) == EVT_STATEMENT_MATCH_WITHDRAWN:
            self.records.append(record)

    def __enter__(self):
        self._logger.addHandler(self)
        self._logger.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self)
        self._logger.setLevel(self._prior_level)


class TestAZeroBoxIsRefused:
    """R-CC125: a ticked $0.00 box refuses the whole press; nothing saves."""

    def test_the_rulings_own_hotel_is_refused_and_keeps_its_payment_and_match(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Hotel's box typed 0.00 and ticked: the ruled sentence, a 400, and nothing changes."""
        with app.app_context():
            checking_id = seed_user["account"].id
            hotel, _line, match_id = _matched_reverted_hotel(
                seed_user, seed_periods_today[0],
            )
            hotel_id = hotel.id
            _true_up(auth_client, checking_id)
            body = _panel(auth_client, checking_id)
            # A bill settles from its figure, so ticking it at its own figure
            # takes nothing out of its match: no caption and no posted lines
            # (the caption's predicate, graded on its negative side).
            assert "Closing it from its purchases" not in body
            assert f'name="shown_lines-{hotel_id}"' not in body

            response = _post(auth_client, checking_id, _payload(
                body, {("transaction_ids", str(hotel_id))},
                typed_boxes={f"settled_amount-{hotel_id}": "0.00"},
            ))

            assert response.status_code == 400
            assert response.headers.get("Shekel-Designed-Fragment") == "1"
            assert _HOTEL_REFUSED in response.data.decode()
            assert _status(hotel_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            (payment,) = db.session.get(Transaction, hotel_id).covering_movements
            assert payment.amount == Decimal("120.00")
            assert payment.settled_on is None
            assert db.session.get(StatementMatch, match_id) is not None

    def test_a_zero_box_left_unticked_is_not_read(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Hotel's box at 0.00 but unticked; another row ticked lands and Hotel is untouched."""
        with app.app_context():
            checking_id = seed_user["account"].id
            hotel, _line, match_id = _matched_reverted_hotel(
                seed_user, seed_periods_today[0],
            )
            hotel_id = hotel.id
            water_id = _row(seed_user, seed_periods_today[0], "Water", "45.00").id
            _true_up(auth_client, checking_id)
            body = _panel(auth_client, checking_id)

            response = _post(auth_client, checking_id, _payload(
                body, {("transaction_ids", str(water_id))},
                typed_boxes={f"settled_amount-{hotel_id}": "0.00"},
            ))

            assert response.status_code == 200
            assert _status(water_id) == ref_cache.status_id(StatusEnum.DONE)
            assert _status(hotel_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            assert db.session.get(StatementMatch, match_id) is not None

    def test_a_ticked_zero_transfer_box_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A transfer's box at 0.00, ticked: refused by the leg's own label; the pair stays Projected."""
        with app.app_context():
            checking_id = seed_user["account"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("2000.00"),
            )
            xfer = create_transfer(
                seed_user, seed_user["account"], savings,
                seed_periods_today[0], Decimal("500.00"),
            )
            db.session.commit()
            xfer_id = xfer.id
            _true_up(auth_client, checking_id)
            body = _panel(auth_client, checking_id)
            assert f'name="transfer_amount-{xfer_id}"' in body

            response = _post(auth_client, checking_id, _payload(
                body, {("transfer_ids", str(xfer_id))},
                typed_boxes={f"transfer_amount-{xfer_id}": "0.00"},
            ))

            assert response.status_code == 400
            assert (
                "Transfer to Savings: a payment can&#39;t be $0.00. If it "
                "wasn&#39;t paid, leave it unticked or cancel it on the grid."
            ) in response.data.decode()
            db.session.expire_all()
            assert db.session.get(Transfer, xfer_id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )


class TestAZeroDepositIsRefusedAsADeposit:
    """R-CC136: money coming IN is refused as a deposit; nothing saves."""

    def test_a_paycheck_at_zero_is_refused_as_a_deposit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A paycheck's box typed 0.00 and ticked: the ruled deposit sentence, and it stays Projected."""
        with app.app_context():
            checking_id = seed_user["account"].id
            paycheck = generate_row_of(
                make_income_template(
                    db.session, seed_user, amount="2000.00", name="Paycheck",
                ),
                seed_periods_today[0],
            )
            db.session.commit()
            paycheck_id = paycheck.id
            _true_up(auth_client, checking_id)
            body = _panel(auth_client, checking_id)
            assert f'name="settled_amount-{paycheck_id}"' in body

            response = _post(auth_client, checking_id, _payload(
                body, {("transaction_ids", str(paycheck_id))},
                typed_boxes={f"settled_amount-{paycheck_id}": "0.00"},
            ))

            assert response.status_code == 400
            text = response.data.decode()
            assert (
                "Paycheck: a deposit can&#39;t be $0.00. If it wasn&#39;t "
                "received, leave it unticked or cancel it on the grid."
            ) in text
            assert "a payment can&#39;t be" not in text
            assert _status(paycheck_id) == ref_cache.status_id(StatusEnum.PROJECTED)

    def test_a_transfers_receiving_side_at_zero_is_refused_as_a_deposit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The incoming leg on Savings' panel at 0.00: refused by its own label, as a deposit."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("2000.00"),
            )
            xfer = create_transfer(
                seed_user, seed_user["account"], savings,
                seed_periods_today[0], Decimal("500.00"),
            )
            db.session.commit()
            savings_id, xfer_id = savings.id, xfer.id
            _true_up(auth_client, savings_id)
            body = _panel(auth_client, savings_id)
            assert f'name="transfer_amount-{xfer_id}"' in body

            response = _post(auth_client, savings_id, _payload(
                body, {("transfer_ids", str(xfer_id))},
                typed_boxes={f"transfer_amount-{xfer_id}": "0.00"},
            ))

            assert response.status_code == 400
            text = response.data.decode()
            assert (
                "Transfer from Checking: a deposit can&#39;t be $0.00. If it "
                "wasn&#39;t received, leave it unticked or cancel it on the grid."
            ) in text
            assert "a payment can&#39;t be" not in text
            db.session.expire_all()
            assert db.session.get(Transfer, xfer_id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            )


class TestTheCheckingListLeavesOutACardPayment:
    """R-CC126: a bill whose payment moved on the card is offered by the card alone."""

    def test_the_checking_panel_omits_it_and_a_forged_tick_moves_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Groceries paid from the card, matched there, reopened: Checking neither offers nor moves it."""
        with app.app_context():
            checking_id = seed_user["account"].id
            card = _card(seed_user)
            card_id = card.id
            groceries = _row(seed_user, seed_periods_today[0], "Groceries", "120.00")
            settle_transaction(groceries, tender_account_id=card_id)
            db.session.commit()
            _line, match_id = _match(
                seed_user, groceries, "-120.00", "GROCERIES", account=card,
            )
            _revert(groceries)
            groceries_id = groceries.id
            _true_up(auth_client, checking_id)

            body = _panel(auth_client, checking_id)
            assert f'name="transaction_ids" value="{groceries_id}"' not in body
            assert (
                f'name="transaction_ids" value="{groceries_id}"'
                in _panel(auth_client, card_id)
            )

            response = _post(auth_client, checking_id, MultiDict([
                ("transaction_ids", str(groceries_id)),
            ]))

            assert response.status_code == 200
            assert _status(groceries_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            (payment,) = db.session.get(Transaction, groceries_id).covering_movements
            assert payment.account_id == card_id
            assert payment.settled_on is None
            assert db.session.get(StatementMatch, match_id) is not None


class TestTheEnvelopeCloseNamesWhatItFrees:
    """R-CC76 / R-CC127: the caption under the row, its posted lines, one check per save (R-CC135)."""

    def test_the_caption_names_the_line_and_the_press_frees_exactly_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Caption and hidden field from one withdrawal; posted as rendered, the close lands."""
        with app.app_context():
            checking_id = seed_user["account"].id
            envelope, line, match_id = _kept_payment_envelope(
                seed_user, seed_periods_today[0],
            )
            envelope_id, line_id = envelope.id, line.id
            posted_on = line.posted_on.strftime("%-m/%-d")
            _true_up(auth_client, checking_id)

            body = _panel(auth_client, checking_id)
            assert (
                "Closing it from its purchases withdraws 1 accepted match, so 1 "
                "bank line is unexplained again on your statement screen: "
                f"{posted_on} GROCERIES"
            ) in body
            assert (
                f'<input type="hidden" name="shown_lines-{envelope_id}" '
                f'value="{line_id}">'
            ) in body
            # The checkbox names the caption as its description.
            caption_id = f"reconcile-reconcile-panel-{checking_id}-t{envelope_id}-withdraws"
            assert f'aria-describedby="{caption_id}"' in body
            assert f'<div class="form-text mt-1" id="{caption_id}">' in body

            with _Withdrawals() as events:
                response = _post(auth_client, checking_id, _payload(
                    body, {("transaction_ids", str(envelope_id))},
                ))

            assert response.status_code == 200
            assert _status(envelope_id) == ref_cache.status_id(StatusEnum.DONE)
            assert db.session.get(Transaction, envelope_id).covering_movements == []
            assert db.session.get(StatementMatch, match_id) is None
            (record,) = events.records
            assert record.freed_line_ids == [line_id]
            assert record.silent_by is None

    def test_a_page_that_named_nothing_is_refused_and_redrawn_with_the_caption(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same tick without the caption's field -- a page drawn before the match: nothing saves.

        A reopened envelope's kept payment cannot be matched while the row is
        Projected (the matcher prices a row holding purchases at nothing), so
        the stale page is the payload a page with no caption sends: the
        rendered form less the hidden field.
        """
        with app.app_context():
            checking_id = seed_user["account"].id
            envelope, _line, match_id = _kept_payment_envelope(
                seed_user, seed_periods_today[0],
            )
            envelope_id = envelope.id
            _true_up(auth_client, checking_id)
            payload = _payload(
                _panel(auth_client, checking_id),
                {("transaction_ids", str(envelope_id))},
            )
            payload.poplist(f"shown_lines-{envelope_id}")

            response = _post(auth_client, checking_id, payload)

            assert response.status_code == 400
            redrawn = response.data.decode()
            assert (
                "Nothing was saved: this page was out of date. As things are "
                "now, this press would leave a bank line unexplained that this "
                "page did not mention. Here it is again -- tick what your "
                "statement shows."
            ) in redrawn
            assert "Reload the page" not in redrawn
            assert "Closing it from its purchases withdraws 1 accepted match" in redrawn
            assert _status(envelope_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            assert db.session.get(Transaction, envelope_id).covering_movements
            assert db.session.get(StatementMatch, match_id) is not None

    def test_a_page_naming_a_line_undone_in_another_tab_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Drawn with the caption, the match undone elsewhere, then posted: nothing saves, redrawn bare."""
        with app.app_context():
            checking_id = seed_user["account"].id
            envelope, _line, match_id = _kept_payment_envelope(
                seed_user, seed_periods_today[0],
            )
            envelope_id = envelope.id
            _true_up(auth_client, checking_id)
            stale = _panel(auth_client, checking_id)
            assert f'name="shown_lines-{envelope_id}"' in stale
            statement_match.release_match(
                match_id, seed_user["user"].id, checking_id,
            )
            db.session.commit()

            response = _post(auth_client, checking_id, _payload(
                stale, {("transaction_ids", str(envelope_id))},
            ))

            assert response.status_code == 400
            redrawn = response.data.decode()
            assert (
                "As things are now, this press leaves 0 bank lines unexplained "
                "again on your statement screen, and the page named 1. Here it "
                "is again -- tick what your statement shows."
            ) in redrawn
            assert "Closing it from its purchases" not in redrawn
            assert _status(envelope_id) == ref_cache.status_id(StatusEnum.PROJECTED)
            assert db.session.get(Transaction, envelope_id).covering_movements

    def test_two_closes_on_one_account_each_post_their_own_lines(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two envelopes on checking, each freeing a line: both land, one save graded on both lines."""
        with app.app_context():
            checking_id = seed_user["account"].id
            groceries, groceries_line, groceries_match = _kept_payment_envelope(
                seed_user, seed_periods_today[0],
            )
            # A second figure: two lines on one day at one figure are one
            # bank line to the import's identity key.
            dining, dining_line, dining_match = _kept_payment_envelope(
                seed_user, seed_periods_today[0], name="Dining", close="85.00",
            )
            ids = (groceries.id, dining.id)
            _true_up(auth_client, checking_id)
            body = _panel(auth_client, checking_id)
            assert f'name="shown_lines-{ids[0]}" value="{groceries_line.id}"' in body
            assert f'name="shown_lines-{ids[1]}" value="{dining_line.id}"' in body

            response = _post(auth_client, checking_id, _payload(
                body, {("transaction_ids", str(txn_id)) for txn_id in ids},
            ))

            assert response.status_code == 200
            for txn_id in ids:
                assert _status(txn_id) == ref_cache.status_id(StatusEnum.DONE)
            assert db.session.get(StatementMatch, groceries_match) is None
            assert db.session.get(StatementMatch, dining_match) is None

    def test_a_close_on_checking_names_the_cards_line_its_kept_payment_frees(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An envelope closed from the card, matched there, reopened, given a purchase: Checking names the CARD's line.

        R-CC126 keeps it on Checking's list -- "A bill paid by its own
        purchases stays on its own list" -- because its tick settles FROM the
        purchase and names no tender.  That ``purchases`` record takes the
        card's kept payment, so the caption on Checking's panel names the
        card's line, and the act grades it on the card's account (the
        movement it takes is there).
        """
        with app.app_context():
            checking_id = seed_user["account"].id
            card = _card(seed_user)
            card_id = card.id
            envelope = _row(
                seed_user, seed_periods_today[0], "Groceries", "300.00",
                is_envelope=True,
            )
            settle_transaction(
                envelope, submitted=typed(Decimal("120.00")),
                tender_account_id=card_id,
            )
            db.session.commit()
            line, match_id = _match(
                seed_user, envelope, "-120.00", "GROCERIES", account=card,
            )
            _revert(envelope)
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")), description="Kroger",
                    purchased_on=seed_periods_today[0].start_date,
                ),
            )
            db.session.commit()
            envelope_id, line_id = envelope.id, line.id
            assert line.account_id == card_id
            _true_up(auth_client, checking_id)

            body = _panel(auth_client, checking_id)
            assert "Closing it from its purchases withdraws 1 accepted match" in body
            assert f'name="shown_lines-{envelope_id}" value="{line_id}"' in body

            response = _post(auth_client, checking_id, _payload(
                body, {("transaction_ids", str(envelope_id))},
            ))

            assert response.status_code == 200
            assert _status(envelope_id) == ref_cache.status_id(StatusEnum.DONE)
            assert db.session.get(Transaction, envelope_id).covering_movements == []
            assert db.session.get(StatementMatch, match_id) is None

    def test_an_unmatched_kept_payment_draws_no_caption_and_closes(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same envelope with nothing matched: no caption, no field, and the close lands."""
        with app.app_context():
            checking_id = seed_user["account"].id
            envelope, _line, _match_id = _kept_payment_envelope(
                seed_user, seed_periods_today[0], matched=False,
            )
            envelope_id = envelope.id
            _true_up(auth_client, checking_id)
            body = _panel(auth_client, checking_id)
            assert "Closing it from its purchases" not in body
            assert f'name="shown_lines-{envelope_id}"' not in body

            response = _post(auth_client, checking_id, _payload(
                body, {("transaction_ids", str(envelope_id))},
            ))

            assert response.status_code == 200
            assert _status(envelope_id) == ref_cache.status_id(StatusEnum.DONE)
            assert db.session.get(Transaction, envelope_id).covering_movements == []


class TestEachListBooksOnTheStatementsAccount:
    """R-CC15's outcome, held by construction since R-CC126: a tick books where that statement's money moved."""

    def test_ticks_on_checking_book_on_checking_and_the_cards_on_the_card(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A fresh bill and a bill reopened over its checking payment, on Checking; a card-paid bill, on the card.

        Ruling R-CC15: a statement-driven settle books on the statement's own
        account.  The panel named that account as the tender until plan step
        credit_card:CC-5-4a-5c-1; after R-CC126 the name changed nothing a
        tick books (``_transactions._settle_one`` says why), so the leaf
        deleted it -- the coordinator's application, 2026-10-04 -- and this
        pins the outcome it used to force for Checking's own list (a fresh
        bill, and a bill reopened over its checking payment) and the card's
        "Paid from this account" list (a bill planned on Checking, paid from
        the card).
        """
        with app.app_context():
            checking_id = seed_user["account"].id
            card = _card(seed_user)
            card_id = card.id
            water_id = _row(seed_user, seed_periods_today[0], "Water", "45.00").id
            hotel = _row(seed_user, seed_periods_today[0], "Hotel", "120.00")
            settle_transaction(hotel)
            db.session.commit()
            _revert(hotel)
            groceries = _row(seed_user, seed_periods_today[0], "Groceries", "80.00")
            settle_transaction(groceries, tender_account_id=card_id)
            db.session.commit()
            _revert(groceries)
            hotel_id, groceries_id = hotel.id, groceries.id
            _true_up(auth_client, checking_id)

            checking_body = _panel(auth_client, checking_id)
            assert _post(auth_client, checking_id, _payload(
                checking_body,
                {("transaction_ids", str(water_id)), ("transaction_ids", str(hotel_id))},
            )).status_code == 200
            card_body = _panel(auth_client, card_id)
            assert _post(auth_client, card_id, _payload(
                card_body, {("transaction_ids", str(groceries_id))},
            )).status_code == 200

            db.session.expire_all()
            for txn_id, account_id in (
                (water_id, checking_id), (hotel_id, checking_id),
                (groceries_id, card_id),
            ):
                row = db.session.get(Transaction, txn_id)
                assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
                (payment,) = row.covering_movements
                assert payment.account_id == account_id


class TestTheReadCostsNothingWhenNoTickWithdraws:
    """One read for the panel's captions, and none at all when no row qualifies."""

    def test_a_panel_of_ordinary_rows_never_reads_the_match_members(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A bill and an envelope holding a purchase but no kept payment: no statement reads the members.

        The envelope settles from its purchase, so it passes the read's first
        test; what keeps the members table out of it is that the read is over
        the row's KEPT PAYMENT (none here) and not its entries -- a read over
        the purchase would ask the table about it.  (The panel's reader,
        ``pending_alone_and_together`` since leaf 5c-2c-1, itself asks nothing
        for an empty set, so this grades WHICH movements are read, not the
        comprehension's second clause.)
        """
        with app.app_context():
            checking_id = seed_user["account"].id
            _row(seed_user, seed_periods_today[0], "Water", "45.00")
            envelope = _row(
                seed_user, seed_periods_today[0], "Groceries", "300.00",
                is_envelope=True,
            )
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("30.00")), description="Kroger",
                    purchased_on=seed_periods_today[0].start_date,
                ),
            )
            db.session.commit()
            _true_up(auth_client, checking_id)
            statements = []

            def record(_conn, _cursor, statement, *_args):
                statements.append(statement)

            event.listen(db.engine, "before_cursor_execute", record)
            try:
                body = _panel(auth_client, checking_id)
            finally:
                event.remove(db.engine, "before_cursor_execute", record)

            assert "Water" in body
            assert not [
                statement for statement in statements
                if "statement_match_members" in statement
            ]
