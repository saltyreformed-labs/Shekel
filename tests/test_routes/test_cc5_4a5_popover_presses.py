"""Plan step ``credit_card:CC-5-4a-5``: every popover press posts what its page named.

Ruling **R-CC127** (developer 2026-09-30), verbatim: *"Each warning also sends
back the bank lines it named, and the function compares them with what it
would undo. At 10:10 they differ, so nothing is saved and the popover reloads
with '...'; press again to go ahead ... A button with no warning sends
nothing, so it can undo a match only by naming its ruling in code, and a test
that submits exactly what the page shows fails if a warning is missing."*
Ruling **R-CC128** (developer 2026-10-04, "Redraw all"): *"The whole popover
redraws from what is true now ... Every box and warning comes from one moment,
so the second press is checked against a page that is current everywhere."*

What is graded here is what each press POSTS -- read off the rendered card,
never a hand-picked payload (the form's controls, a button's ``hx-vals``) --
and what the door does with it: the press goes ahead when its page named
exactly what it frees; a page out of date (a match undone in another tab, or
made after the card was drawn) is answered with the card redrawn and nothing
written, and the redrawn card's own press goes ahead; a request carrying no
field is the owner's silent Mark Paid (ruling **R-CC56**) or, at every other
door, a press that named nothing.  Ruling **R-CC129** (developer 2026-10-04,
"Warn in both places") names the one Save typing a $0.00 estimate beside Paid
under the Estimated box; ruling **R-CC130** ("Companion refuses") refuses a
companion's Mark Paid that would free a line.  The row delete's dialog is
graded in ``test_cc5_4a5_delete_dialog``.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    transaction_service,
    transfer_service,
)
from app.services.settle_day import SettleDay
from app.services.statement_match import (
    accept_match,
    matched_subjects,
    release_match,
)
from app.utils.log_events import EVT_STATEMENT_MATCH_WITHDRAWN
from tests._test_helpers import (
    create_account_of_type,
    side_records_by_key,
    typed,
)
from tests.test_routes._statement_forms import ReconcileFormReader
from tests.test_routes.test_cc5_4a3_captions import (
    _claimed,
    _day,
    _form_fields,
    _frees,
    _hotel,
    _matched,
    _matched_transfer,
    _popover,
    _reverted_envelope,
    _settle,
    _transfer_popover,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does; the convention ``test_statement_reconcile`` keeps.
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)


def _vals(html, method, path):
    """What the button pressing *method* on *path* sends beside the request.

    Its ``hx-vals`` JSON, or ``{}`` for a button carrying none.
    """
    tag = re.search(
        rf'<button[^>]*hx-{method}="{re.escape(path)}"[^>]*>', html, re.S,
    )
    assert tag is not None, f"the card renders no {method} button for {path}"
    found = re.search(r"hx-vals='([^']*)'", tag.group(0))
    return json.loads(found.group(1)) if found else {}


def _paid_vals(html, txn_id):
    """What the card's Paid / Received button sends beside its request."""
    return _vals(html, "post", f"/transactions/{txn_id}/mark-done")


def _delete_vals(html, txn_id):
    """What the card's Delete button sends: the lines and purchases its dialog named."""
    return _vals(html, "delete", f"/transactions/{txn_id}")


def dialog_delete_values(client, txn_id):
    """What the popover's Delete sends if pressed now: its dialog's ``hx-vals``.

    The bank lines and the purchases the dialog names as the card is drawn at
    this moment (rulings R-CC127 / R-CC131) -- for a test that deletes a row
    the way the card does, rather than with a hand-picked query.

    Args:
        client: A signed-in test client.
        txn_id: The row.

    Returns:
        The query the Delete button sends.
    """
    response = client.get(f"/transactions/{txn_id}/full-edit")
    assert response.status_code == 200
    return _delete_vals(response.get_data(as_text=True), txn_id)


def _undo_elsewhere(seed_user, line, account=None):
    """The statement screen's Undo, pressed in another tab (``release_match``)."""
    match_id = (
        db.session.query(StatementMatch.id)
        .join(StatementMatch.members)
        .filter_by(bank_statement_line_id=line.id)
        .scalar()
    )
    release_match(
        match_id, seed_user["user"].id,
        (account or seed_user["account"]).id,
    )
    db.session.commit()


def _redrawn(response, card_id):
    """Assert *response* is ruling R-CC128's redraw of *card_id*; return its body."""
    assert response.status_code == 400
    assert response.headers["HX-Retarget"] == f"#{card_id}"
    assert response.headers["HX-Reswap"] == "outerHTML"
    assert response.headers["Shekel-Designed-Fragment"] == "1"
    html = response.get_data(as_text=True)
    assert f'id="{card_id}"' in html, "the body IS the card it replaces"
    assert "Nothing was saved: this page was out of date." in html
    assert "Here it is as it is now; press again to go ahead." in html
    return html


def _committed():
    """Commit, then forget every loaded row: what a later request would read.

    A refused press's rollback is graded by what a COMMIT after it writes, not
    by ``session.dirty`` (an error path's re-fetch autoflushes the staged
    change and empties ``dirty`` either way).
    """
    db.session.commit()
    db.session.expire_all()


def _zero_estimate_reverted_bill(seed_user, auth_client):
    """A Paid $120.00 Hotel whose payment is matched, reverted, its estimate set to $0.00.

    The estimate is changed through the card's own Save, as the owner would:
    reverting keeps the payment un-dated (R-BAL61) and its match standing, and
    a Paid that books the $0.00 estimate takes that payment off the books.
    """
    txn, line = _hotel(seed_user)
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    fields = _form_fields(_popover(auth_client, txn.id))
    fields["estimated_amount"] = "0.00"
    response = auth_client.patch(f"/transactions/{txn.id}", data=fields)
    assert response.status_code == 200
    db.session.expire_all()
    return db.session.get(Transaction, txn.id), line


def _reverted(txn):
    """Set *txn* back to Projected, as the card's Status dropdown would; commit."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


def _mark_paid_form(html, txn_id):
    """What a phone card's Mark Paid form submits, control by control."""
    start = html.index(f'hx-post="/transactions/{txn_id}/mark-done"')
    start = html.rindex("<form", 0, start)
    reader = ReconcileFormReader()
    reader.feed(html[start:html.index("</form>", start)])
    return dict(reader.fields)


class TestThePopoversPaidPostsWhatItsCaptionNamed:
    """Paid / Received on the card: the button's own ``hx-vals``."""

    def test_purchases_replacing_the_payment_post_its_line_and_withdraw(
        self, app, auth_client, seed_user,
    ):
        """A reverted envelope's purchases replace its matched payment: Paid posts that line.

        The caption beside Paid names the 9/24 HOTEL line, the button sends its
        id, and the press withdraws the act -- what it named, nothing else.
        """
        with app.app_context():
            txn, line = _reverted_envelope(seed_user, with_purchase=True)
            vals = _paid_vals(_popover(auth_client, txn.id), txn.id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done", data=vals,
            )

            assert response.status_code == 200
            _committed()
            assert db.session.query(StatementMatch).count() == 0
            assert not _claimed(seed_user, line)

    def test_a_paid_that_records_zero_is_captioned_and_withdraws(
        self, app, auth_client, seed_user,
    ):
        """The $0.00 arm of Paid's caption (plan step CC-5-4a-5's extension)."""
        with app.app_context():
            txn, line = _zero_estimate_reverted_bill(seed_user, auth_client)
            html = _popover(auth_client, txn.id)
            assert (
                f"Marking this paid records $0.00 and {_frees(seed_user)}"
                in html
            )
            vals = _paid_vals(html, txn.id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done", data=vals,
            )

            assert response.status_code == 200
            _committed()
            assert db.session.query(StatementMatch).count() == 0
            assert not _claimed(seed_user, line)
            settled = db.session.get(Transaction, txn.id)
            assert settled.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert settled.covering_movements == [], "a $0.00 record moves nothing"

    def test_a_received_that_records_zero_is_captioned_and_withdraws(
        self, app, auth_client, seed_user,
    ):
        """The income twin of the $0.00 arm: 'Marking this received records $0.00'."""
        with app.app_context():
            txn = a_transaction(
                seed_user, name="Refund", amount="120.00", income=True,
                template=False,
            )
            db.session.commit()
            _settle(seed_user, txn)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="120.00",
                posted_on=_day(seed_user), description="REFUND",
            )
            db.session.commit()
            scope = a_scope(seed_user)
            accept_match(
                a_submission(scope, lines=[line], transactions=[txn]), scope,
            )
            db.session.commit()
            _reverted(txn)
            fields = _form_fields(_popover(auth_client, txn.id))
            fields["estimated_amount"] = "0.00"
            assert auth_client.patch(
                f"/transactions/{txn.id}", data=fields,
            ).status_code == 200
            db.session.expire_all()
            html = _popover(auth_client, txn.id)
            assert (
                "Marking this received records $0.00 and withdraws 1 accepted "
                "match" in html
            )
            vals = _vals(html, "post", f"/transactions/{txn.id}/mark-done")
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done", data=vals,
            )

            assert response.status_code == 200
            _committed()
            assert line.id not in matched_subjects(seed_user["account"].id).lines
            settled = db.session.get(Transaction, txn.id)
            assert settled.status_id == ref_cache.status_id(StatusEnum.RECEIVED)
            assert settled.covering_movements == []

    def test_a_stale_paid_is_redrawn_and_the_second_press_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """Drawn while matched; the match undone in another tab; Paid pressed."""
        with app.app_context():
            txn, line = _reverted_envelope(seed_user, with_purchase=True)
            stale = _paid_vals(_popover(auth_client, txn.id), txn.id)
            assert stale == {"shown_lines": str(line.id)}
            _undo_elsewhere(seed_user, line)

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done", data=stale,
            )

            html = _redrawn(response, f"txn-full-edit-{txn.id}")
            assert "purchases-withdraws-" not in html, "nothing is matched now"
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert len(row.covering_movements) == 1, "the kept payment stays"

            again = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data=_paid_vals(html, txn.id),
            )

            assert again.status_code == 200
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert row.covering_movements == [], "the purchases are the record"

    def test_the_grid_one_click_posts_nothing_and_withdraws_silently(
        self, app, auth_client, seed_user, caplog,
    ):
        """The cell's checkmark sends no field: ruling R-CC56's silent door."""
        with app.app_context():
            txn, line = _reverted_envelope(seed_user, with_purchase=True)

            with caplog.at_level(logging.INFO):
                response = auth_client.post(
                    f"/transactions/{txn.id}/mark-done",
                )

            assert response.status_code == 200
            _committed()
            assert not _claimed(seed_user, line)
            (record,) = [
                record for record in caplog.records
                if getattr(record, "event", None)
                == EVT_STATEMENT_MATCH_WITHDRAWN
            ]
            assert record.silent_by == "R-CC56"

    def test_the_tender_caption_alone_names_what_paid_frees(
        self, app, auth_client, seed_user,
    ):
        """'Paid from' another account re-points a kept payment holding a typed figure.

        Review finding M2 (MF): on this row the "Paid from" caption is the
        card's ONLY caption -- the typed $120.00 outranks the $125.00
        estimate, so neither Paid's $0.00 arm nor the Estimated box's
        (ruling R-CC129) applies -- and Paid must still post its line.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            txn = a_transaction(seed_user, name="Hotel", amount="125.00")
            db.session.commit()
            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("120.00")),
                settle_day=SettleDay(
                    day=_day(seed_user), basis=SettledDayBasisEnum.ENTERED,
                ),
            )
            db.session.commit()
            line = _matched(seed_user, txn)
            _reverted(txn)
            html = _popover(auth_client, txn.id)
            assert f'id="tender-withdraws-{txn.id}"' in html
            assert f'id="estimate-withdraws-{txn.id}"' not in html
            assert f'id="purchases-withdraws-{txn.id}"' not in html
            vals = _paid_vals(html, txn.id)
            assert vals == {"shown_lines": str(line.id)}

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={**vals, "tender_account_id": str(card.id)},
            )

            assert response.status_code == 200
            _committed()
            assert not _claimed(seed_user, line)
            (movement,) = db.session.get(Transaction, txn.id).covering_movements
            assert movement.account_id == card.id
            assert movement.amount == Decimal("120.00")


class TestACompanionsMarkPaidMayFreeNoLine:
    """Ruling R-CC130 (developer 2026-10-04, "Companion refuses")."""

    def test_a_companions_press_is_refused_and_the_owners_same_press_goes_ahead(
        self, app, companion_client, auth_client, seed_user, caplog,
    ):
        """The companion page's Mark Paid on Hotel is refused; the owner's same press withdraws.

        Refused with nothing changed; the owner's withdraws silently (R-CC56).

        Both requests post exactly what the companion page's card renders --
        one phone-card partial, shared with the owner's phone grid -- each from
        its own signed-in client.
        """
        with app.app_context():
            txn, line = _reverted_envelope(
                seed_user, with_purchase=True, companion_visible=True,
            )
            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            )
            assert page.status_code == 200
            fields = _mark_paid_form(page.get_data(as_text=True), txn.id)
            assert "shown_lines" not in fields, "no companion surface names a line"

            refused = companion_client.post(
                f"/transactions/{txn.id}/mark-done", data=fields,
            )

            assert refused.status_code == 400
            assert refused.headers["Shekel-Designed-Fragment"] == "1"
            assert (
                "Hotel is matched to a line on the bank statement, so only "
                "the account owner can mark it paid."
                in refused.get_data(as_text=True)
            )
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert len(row.covering_movements) == 1, "the kept payment stays"
            assert _claimed(seed_user, line)

            with caplog.at_level(logging.INFO):
                owners = auth_client.post(
                    f"/transactions/{txn.id}/mark-done", data=fields,
                )

            assert owners.status_code == 200
            _committed()
            assert not _claimed(seed_user, line)
            (record,) = [
                record for record in caplog.records
                if getattr(record, "event", None)
                == EVT_STATEMENT_MATCH_WITHDRAWN
            ]
            assert record.silent_by == "R-CC56"

    def test_a_companions_crafted_lines_are_not_read(
        self, app, companion_client, seed_user,
    ):
        """A companion posting the right line id is still refused.

        The owner's statement is never the companion's to name.

        The card's own controls plus a crafted ``shown_lines`` naming the very
        line the press frees -- what passes for a popover's Paid.
        """
        with app.app_context():
            txn, line = _reverted_envelope(
                seed_user, with_purchase=True, companion_visible=True,
            )
            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            )
            fields = _mark_paid_form(page.get_data(as_text=True), txn.id)

            refused = companion_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={**fields, "shown_lines": str(line.id)},
            )

            assert refused.status_code == 400
            assert "only the account owner can mark it paid" in (
                refused.get_data(as_text=True)
            )
            _committed()
            assert _claimed(seed_user, line)

    def test_a_companions_press_that_frees_nothing_goes_ahead(
        self, app, companion_client, seed_user,
    ):
        """The refusal is for a match only: an unmatched reverted envelope is marked paid."""
        with app.app_context():
            txn, line = _reverted_envelope(
                seed_user, with_purchase=True, companion_visible=True,
            )
            _undo_elsewhere(seed_user, line)
            page = companion_client.get(
                f"/companion/period/{txn.pay_period_id}",
            )

            response = companion_client.post(
                f"/transactions/{txn.id}/mark-done",
                data=_mark_paid_form(page.get_data(as_text=True), txn.id),
            )

            assert response.status_code == 200
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE)


class TestTheEstimateCaption:
    """Ruling R-CC129 (developer 2026-10-04, "Warn in both places")."""

    def test_one_save_typing_zero_beside_paid_is_named_and_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """Review finding H2's dead end: Estimated 0.00 AND Status Paid in ONE Save.

        The Paid caption reads the STORED $120.00, so until R-CC129 the card
        named nothing and the act refused the Save as out of date at every try.
        """
        with app.app_context():
            txn, line = _hotel(seed_user)
            _reverted(txn)
            html = _popover(auth_client, txn.id)
            assert f"Saving this as Paid at $0.00 {_frees(seed_user)}" in html
            assert f'id="purchases-withdraws-{txn.id}"' not in html, (
                "the stored estimate is $120.00, so Paid alone frees nothing"
            )
            fields = _form_fields(html)
            assert fields["shown_lines"] == str(line.id)
            fields["estimated_amount"] = "0.00"
            fields["status_id"] = str(ref_cache.status_id(StatusEnum.DONE))

            response = auth_client.patch(f"/transactions/{txn.id}", data=fields)

            assert response.status_code == 200
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert row.covering_movements == [], "a $0.00 record moves nothing"
            assert not _claimed(seed_user, line)

    def test_no_caption_on_a_settled_row(self, app, auth_client, seed_user):
        """A settled Hotel's $0.00 is the Actual box's caption (R-CC56), not the Estimated box's.

        The second review's L2: dropping the Projected half of the predicate
        survived every test.
        """
        with app.app_context():
            settled, _line = _hotel(seed_user)

            html = _popover(auth_client, settled.id)

            assert f'id="zero-withdraws-{settled.id}"' in html
            assert f'id="estimate-withdraws-{settled.id}"' not in html

    def test_no_caption_on_a_row_that_settles_from_its_purchases(
        self, app, auth_client, seed_user,
    ):
        """A reverted envelope holding purchases settles FROM them, which Paid's caption names.

        The second review's L2: dropping the purchases half of the predicate
        survived every test.
        """
        with app.app_context():
            envelope, _line = _reverted_envelope(seed_user, with_purchase=True)

            html = _popover(auth_client, envelope.id)

            assert f'id="purchases-withdraws-{envelope.id}"' in html
            assert f'id="estimate-withdraws-{envelope.id}"' not in html

    def test_an_income_row_says_received(self, app, auth_client, seed_user):
        """A reverted matched paycheck's caption names Received, as its button does."""
        with app.app_context():
            txn = a_transaction(
                seed_user, name="Refund", amount="120.00", income=True,
                template=False,
            )
            db.session.commit()
            _settle(seed_user, txn)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="120.00",
                posted_on=_day(seed_user), description="REFUND",
            )
            db.session.commit()
            scope = a_scope(seed_user)
            accept_match(
                a_submission(scope, lines=[line], transactions=[txn]), scope,
            )
            db.session.commit()
            _reverted(txn)

            html = _popover(auth_client, txn.id)

            assert (
                "Saving this as Received at $0.00 withdraws 1 accepted match"
                in html
            )

    def test_saves_that_free_nothing_on_that_card_go_ahead(
        self, app, auth_client, seed_user,
    ):
        """The card names the line on every Save; a Save that frees none is not refused for it.

        A notes-only Save, then Paid at the $120.00 estimate (which re-dates
        the kept payment): neither takes a movement off, so neither is graded
        against the line, and the match stands.
        """
        with app.app_context():
            txn, line = _hotel(seed_user)
            _reverted(txn)
            fields = _form_fields(_popover(auth_client, txn.id))
            assert fields["shown_lines"] == str(line.id)
            fields["notes"] = "checked the folio"

            assert auth_client.patch(
                f"/transactions/{txn.id}", data=fields,
            ).status_code == 200
            _committed()
            assert db.session.get(Transaction, txn.id).notes == "checked the folio"
            assert _claimed(seed_user, line)

            vals = _paid_vals(_popover(auth_client, txn.id), txn.id)
            assert vals == {"shown_lines": str(line.id)}
            paid = auth_client.post(
                f"/transactions/{txn.id}/mark-done", data=vals,
            )

            assert paid.status_code == 200
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert [m.amount for m in row.covering_movements] == [
                Decimal("120.00"),
            ]
            assert _claimed(seed_user, line)


class TestTheSaveIsCheckedAgainstWhatItsCardNamed:
    """The card's Save: the form's hidden ``shown_lines``."""

    def test_a_stale_zero_save_is_redrawn_and_the_second_press_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """R-CC128's own example: 0.00 typed at 10:00, the match undone at 10:05."""
        with app.app_context():
            txn, line = _hotel(seed_user)
            fields = _form_fields(_popover(auth_client, txn.id))
            assert fields["shown_lines"] == str(line.id)
            fields["settled_amount"] = "0.00"
            _undo_elsewhere(seed_user, line)

            response = auth_client.patch(f"/transactions/{txn.id}", data=fields)

            html = _redrawn(response, f"txn-full-edit-{txn.id}")
            assert "zero-withdraws-" not in html, "nothing is matched now"
            _committed()
            row = db.session.get(Transaction, txn.id)
            assert [m.amount for m in row.covering_movements] == [
                Decimal("120.00"),
            ], "the $120.00 record stands"

            fresh = _form_fields(html)
            assert fresh["shown_lines"] == ""
            assert fresh["settled_amount"] == "120.00", "what you typed is gone"
            fresh["settled_amount"] = "0.00"
            again = auth_client.patch(f"/transactions/{txn.id}", data=fresh)

            assert again.status_code == 200
            _committed()
            assert db.session.get(Transaction, txn.id).covering_movements == []

    def test_a_save_without_the_field_is_refused_on_the_cell(
        self, app, auth_client, seed_user,
    ):
        """No card posts this; a request that names nothing and would free a line is refused."""
        with app.app_context():
            txn, line = _hotel(seed_user)
            fields = _form_fields(_popover(auth_client, txn.id))
            del fields["shown_lines"]
            fields["settled_amount"] = "0.00"

            response = auth_client.patch(f"/transactions/{txn.id}", data=fields)

            assert response.status_code == 400
            assert "HX-Retarget" not in response.headers, (
                "a request no popover posted has no card to redraw"
            )
            assert "this page was out of date" in response.get_data(as_text=True)
            _committed()
            assert _claimed(seed_user, line)
            assert db.session.query(StatementMatch).count() == 1


class TestTheTransferPopover:
    """The transfer card: its Save's and its Paid's ``shown_lines``."""

    def test_a_stale_transfer_zero_save_is_redrawn_and_the_second_press_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """R-CC128 on the transfer card: 0.00 typed, the match undone elsewhere, Save.

        The card is redrawn with nothing written; the redrawn card's own Save
        of 0.00 then takes both legs' payments off.
        """
        with app.app_context():
            xfer, (line,) = _matched_transfer(seed_user, legs=("checking",))
            xfer_id = xfer.id
            fields = _form_fields(_transfer_popover(auth_client, xfer_id))
            assert fields["shown_lines"] == str(line.id)
            fields["settled_amount"] = "0.00"
            _undo_elsewhere(seed_user, line)

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}", data=fields,
            )

            html = _redrawn(response, f"xfer-full-edit-{xfer_id}")
            _committed()
            assert db.session.get(Transfer, xfer_id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            )

            fresh = _form_fields(html)
            assert fresh["shown_lines"] == ""
            fresh["settled_amount"] = "0.00"
            again = auth_client.patch(
                f"/transfers/instance/{xfer_id}", data=fresh,
            )

            assert again.status_code == 200
            _committed()
            assert db.session.get(Transfer, xfer_id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            )
            assert side_records_by_key(db.session.get(Transfer, xfer_id)) == {}, (
                "a $0.00 record moves nothing on either leg"
            )

    def test_the_transfer_paid_posts_its_cards_empty_field(
        self, app, auth_client, seed_user,
    ):
        """No arm prices a transfer at $0.00, so its Paid names nothing, and says so."""
        with app.app_context():
            xfer, _lines = _matched_transfer(seed_user, legs=())
            transfer_service_revert(seed_user, xfer.id)
            html = _transfer_popover(auth_client, xfer.id)
            fields = _form_fields(html)
            assert fields["shown_lines"] == ""
            paid = re.search(
                rf'<button[^>]*hx-post="/transfers/instance/{xfer.id}/mark-done"[^>]*>',
                html, re.S,
            )
            assert paid is not None
            assert f'hx-include="#xfer-shown-lines-{xfer.id}"' in paid.group(0)
            assert f'id="xfer-shown-lines-{xfer.id}"' in html

            response = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done",
                data={"shown_lines": fields["shown_lines"]},
            )

            assert response.status_code == 200
            _committed()
            assert db.session.get(Transfer, xfer.id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            )


class TestARefusedPressWithNoCardToDraw:
    """The no-card arm of the refused-press answer (``routes._refused_press``)."""

    def test_it_answers_not_found_and_writes_nothing(
        self, app, auth_client, seed_user,
    ):
        """A press refused as out of date whose card cannot be drawn: the card GET's "not found".

        Reached here by a crafted leg that is not the transfer's (the leg
        resolver falls back, the card's draw rule refuses).  Pinned because
        routing this arm through the door's own refusal was measured worse --
        a soft-deleted transfer drawn as live (the third review) -- and
        nothing else would catch its return.  The "writes nothing" half pins
        the end state only: the press flushes the legs' posting reversal
        before the act refuses, and the request's teardown rollback
        (credit_card:R-CC123) discards it either way, so this check cannot
        tell the arm's rollback from none; the redraw tests grade that
        rollback.
        """
        with app.app_context():
            xfer, (line,) = _matched_transfer(seed_user, legs=("checking",))
            xfer_id = xfer.id
            fields = _form_fields(_transfer_popover(auth_client, xfer_id))
            assert fields["shown_lines"] == str(line.id)
            fields["settled_amount"] = "0.00"
            fields["leg_account_id"] = "999999"
            _undo_elsewhere(seed_user, line)

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}", data=fields,
            )

            assert response.status_code == 404
            assert response.get_data(as_text=True) == "Not found"
            assert "Shekel-Designed-Fragment" not in response.headers
            _committed()
            assert db.session.get(Transfer, xfer_id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            )
            assert len(side_records_by_key(db.session.get(Transfer, xfer_id))) == 2, (
                "the refused $0.00 took neither leg's payment off"
            )

    def test_the_same_crafted_leg_on_a_current_page_goes_ahead(
        self, app, auth_client, seed_user,
    ):
        """The sibling: that 404 is the no-card arm's, not a door refusing a bad leg up front."""
        with app.app_context():
            xfer, (line,) = _matched_transfer(seed_user, legs=("checking",))
            xfer_id = xfer.id
            fields = _form_fields(_transfer_popover(auth_client, xfer_id))
            assert fields["shown_lines"] == str(line.id)
            fields["settled_amount"] = "0.00"
            fields["leg_account_id"] = "999999"

            response = auth_client.patch(
                f"/transfers/instance/{xfer_id}", data=fields,
            )

            assert response.status_code == 200
            _committed()
            assert side_records_by_key(db.session.get(Transfer, xfer_id)) == {}


def transfer_service_revert(seed_user, xfer_id):
    """Set a transfer back to Projected, as the card's Status dropdown would."""
    transfer_service.update_transfer(
        xfer_id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


class TestTheTransferInstanceDeleteRefusesRatherThanFailing:
    """The instance DELETE no template renders: a refusal, not a 500."""

    def test_a_hard_delete_that_would_free_a_line_is_refused_and_writes_nothing(
        self, app, auth_client, seed_user,
    ):
        """An ad-hoc pair with a matched leg: the field-less DELETE names nothing; refused.

        cp1 raised the act's refusal out of this door uncaught, a 500; it is
        the designed 400 now, and the pair and its match stand.
        """
        with app.app_context():
            xfer, (line,) = _matched_transfer(seed_user, legs=("checking",))
            xfer_id = xfer.id
            assert xfer.transfer_template_id is None, "an ad-hoc pair deletes HARD"

            response = auth_client.delete(f"/transfers/instance/{xfer_id}")

            assert response.status_code == 400
            assert response.headers["Shekel-Designed-Fragment"] == "1"
            assert "this page was out of date" in response.get_data(as_text=True)
            _committed()
            kept = db.session.get(Transfer, xfer_id)
            assert kept is not None and not kept.is_deleted
            assert _claimed(seed_user, line)
