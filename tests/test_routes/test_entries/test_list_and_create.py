"""Transaction entry route tests: listing entries, creating one, a future date refused, a sign
picked and never typed, and the add form's account.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.ref import TransactionType
from app.utils.dates import display_today

from tests._test_helpers import create_account_of_type, one_off_row_of

from ._helpers import _add_entry, _create_other_user_txn


# ---- List entries (GET) -------------------------------------------------

class TestListEntries:
    """Tests for GET /transactions/<txn_id>/entries."""

    def test_returns_partial_with_entries(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET with entries returns 200 and HTML containing descriptions and amounts."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _add_entry(txn, seed_user, "50.00", "Kroger")
            _add_entry(txn, seed_user, "30.00", "Target")

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"Kroger" in resp.data
            assert b"Target" in resp.data
            assert b"50.00" in resp.data
            assert b"30.00" in resp.data

    def test_empty_state_message(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET with no entries shows empty state message."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"No purchases recorded yet" in resp.data

    def test_shows_remaining_balance(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET shows correct remaining balance (estimated - sum of entries)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            # Estimated = $500, entry = $200 -> remaining = $300.
            _add_entry(txn, seed_user, "200.00", "Costco")

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"300.00" in resp.data
            assert b"Remaining" in resp.data

    def test_remaining_negative_overspent(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Overspent remaining is displayed with text-danger class."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            # Estimated = $500, entries total = $530 -> remaining = -$30.
            _add_entry(txn, seed_user, "300.00", "Costco")
            _add_entry(txn, seed_user, "230.00", "Target")

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"text-danger" in resp.data
            assert b"30.00" in resp.data

    def test_nonexistent_transaction_returns_404(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """GET for nonexistent transaction returns 404."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/entries")
            assert resp.status_code == 404

    def test_other_user_transaction_returns_404(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """GET for another user's transaction returns 404 (IDOR guard)."""
        with app.app_context():
            other = _create_other_user_txn()
            resp = auth_client.get(
                f"/transactions/{other['transaction'].id}/entries",
            )
            assert resp.status_code == 404

    def test_html_contains_entry_list_container(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Response HTML contains the #entry-list-{txn_id} container div."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert f'id="entry-list-{txn.id}"'.encode() in resp.data

    def test_editing_param_shows_edit_form(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET with ?editing=<entry_id> renders an inline edit form."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.get(
                f"/transactions/{txn.id}/entries?editing={entry.id}",
            )
            assert resp.status_code == 200
            # Edit form uses hx-patch for submission.
            assert b"hx-patch" in resp.data

    def test_credit_entry_shows_cc_badge(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Credit entries display a CC badge in the entry list."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _add_entry(txn, seed_user, "40.00", "Amazon", is_credit=True)

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"CC" in resp.data
            assert b"bg-warning-subtle" in resp.data

    def test_out_of_period_entry_shows_warning(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Entries with dates outside the pay period show a warning icon."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            # Period 0 is 2026-01-02 to 2026-01-15.
            # Use a date well outside.
            _add_entry(
                txn, seed_user, "50.00", "Late Purchase",
                purchased_on=date(2026, 2, 15),
            )

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"Date outside pay period range" in resp.data


# ---- Create entry (POST) ------------------------------------------------

class TestCreateEntry:
    """Tests for POST /transactions/<txn_id>/entries."""

    def test_create_with_valid_data(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with valid form data creates an entry and returns updated list."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            assert b"Kroger" in resp.data
            assert b"50.00" in resp.data

            # Verify database state.
            entries = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).all()
            assert len(entries) == 1
            assert entries[0].amount == Decimal("50.00")
            assert entries[0].description == "Kroger"

    def test_create_credit_entry(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with is_credit=on creates a credit entry."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "80.00",
                    "description": "Amazon",
                    "purchased_on": "2026-01-06",
                    "is_credit": "on",
                },
            )
            assert resp.status_code == 200

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.is_credit is True
            assert b"CC" in resp.data

    def test_create_minimum_fields(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with only required fields uses default is_credit=False."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "25.50",
                    "description": "Gas Station",
                    "purchased_on": "2026-01-04",
                },
            )
            assert resp.status_code == 200

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.is_credit is False
            assert entry.amount == Decimal("25.50")

    def test_validation_error_zero_amount(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with amount=0 returns 422 validation error."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "0",
                    "description": "Test",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 422

    def test_validation_error_negative_amount(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with negative amount returns 422 validation error."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "-5.00",
                    "description": "Test",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 422

    def test_validation_error_missing_fields(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with no form data returns 422 (missing required fields)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={},
            )
            assert resp.status_code == 422

    def test_validation_error_empty_description(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with empty description returns 422 (stripped by pre_load, then missing)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 422

    def test_non_tracked_transaction_returns_400(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST on a non-tracked transaction returns 400 (service ValidationError)."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            # A one-off whose definition is no envelope (not entry-capable).
            txn = one_off_row_of(
                seed_periods[0],
                name="Phone Bill",
                amount=Decimal("80.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "Test",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 400

    def test_hx_trigger_balance_changed(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST returns HX-Trigger: balanceChanged header."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_entry_user_id_matches_current_user(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Created entry's user_id matches the authenticated user."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                },
            )

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.user_id == seed_user["user"].id


# ---- Future entry date refused (plan step X-c0, ruling R-M) -------------

class TestAFutureEntryDateIsRefusedAtTheRoute:
    """Both HTTP doors reject an entry dated after the user's today.

    Plan step **X-c0**, ruling R-M.  The service owns the boundary; these pin
    that a user actually reaching the endpoint gets a 400 with the reason,
    that nothing is written, and that the surface a hand-built request bypasses
    (the picker's ``max``) is not the only thing standing in the way.

    The dates come from :func:`~app.utils.dates.display_today` rather than the
    file's frozen ``date.today()``: the boundary is the user's civil date, and
    the two frames differ for part of every evening.
    """

    def test_post_with_a_future_date_is_refused_and_writes_nothing(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST a tomorrow-dated purchase -> 400, no row, reason shown."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            tomorrow = display_today() + timedelta(days=1)

            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "150.00",
                    "description": "Costco run I have not made",
                    "purchased_on": tomorrow.isoformat(),
                },
            )

            assert resp.status_code == 400
            assert b"cannot be in the future" in resp.data
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0

    def test_post_with_todays_date_succeeds(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The add form posts exactly today, so today must be accepted.

        The complement of the refusal above: without it, a guard that rejected
        its own form would look identical to a working one from the refusal
        test alone.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            today = display_today()

            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "42.87",
                    "description": "Walmart",
                    "purchased_on": today.isoformat(),
                },
            )

            assert resp.status_code == 200
            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.purchased_on == today

    def test_patch_moving_a_date_forward_is_refused(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH an existing entry's date into the future -> 400, unchanged."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=display_today() - timedelta(days=2),
            )
            original = entry.purchased_on
            tomorrow = display_today() + timedelta(days=1)

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"purchased_on": tomorrow.isoformat()},
            )

            assert resp.status_code == 400
            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).purchased_on == original

    def test_the_edit_picker_is_bounded_at_the_users_today(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The rendered date input carries max=<display today>.

        A courtesy bound, not the gate -- but if it drifts off the service's
        clock the form starts offering values the server refuses.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.get(
                f"/transactions/{txn.id}/entries?editing={entry.id}",
            )

            assert resp.status_code == 200
            assert (
                f'max="{display_today().isoformat()}"'.encode() in resp.data
            )


# ---- Update entry (PATCH) -----------------------------------------------

class TestASignIsPICKEDAndNeverTyped:
    """A typed minus cannot book a refund, on EITHER purchase form.

    Developer ruling 2026-09-01, plan step ``bank_import:X-gj-2b-3``, after an
    adversarial design review measured what the previous shape cost.

    Ruling **bank_import:R-II** moved positivity off the table onto the
    hand-entry door, and the branch that did it then deleted the bound from the
    EDIT door as well -- because the inline form resubmits its amount box even
    when the owner only changed a date, so a stored refund could not be
    re-described while the box refused a negative.  That defect was real; the
    remedy was aimed at the wrong tier.

    **What it cost.**  ``EntryUpdateSchema`` is reached ONLY by the human PATCH
    route, so both doors carrying this rule are doors a person types at -- and
    one of them had no bound at all.  A typed ``-45.00`` where ``45.00`` was
    meant booked a REFUND in silence and moved the projection by twice the
    figure, while the identical keystroke on the ADD form was a 422.

    Both forms take a MAGNITUDE now and state the direction beside it, so a
    minus is not refused -- it is unrepresentable.  Every case here posts what
    the template actually emits.
    """

    def test_a_typed_MINUS_is_refused_on_the_EDIT_form(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The keystroke that silently booked a refund.

        Asserts the STORED figure as well as the status: a 422 that had already
        written the row would be the worse failure, and only the second
        assertion can see it.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "-45.00", "direction": "charge"},
            )

            assert resp.status_code == 422
            db.session.refresh(entry)
            assert entry.amount == Decimal("50.00"), (
                "the refused edit must not have written anything"
            )

    def test_a_typed_MINUS_is_refused_on_the_ADD_form(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The control the edit form was measured against, unchanged."""
        with app.app_context():
            txn = seed_entry_template["transaction"]

            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "-45.00", "direction": "charge",
                    "description": "Kroger", "purchased_on": "2026-01-05",
                },
            )

            assert resp.status_code == 422
            assert db.session.query(TransactionEntry).count() == 0

    def test_PICKING_refund_is_what_stores_a_negative(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The deliberate act, which must still work -- on the ADD form.

        Without this the two refusals above are satisfied by a door that
        cannot record a refund at all, which is the state ruling **R-II**
        exists to end.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]

            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "45.00", "direction": "refund",
                    "description": "Amazon refund",
                    "purchased_on": "2026-01-05",
                },
            )

            assert resp.status_code == 200
            [entry] = db.session.query(TransactionEntry).all()
            assert entry.amount == Decimal("-45.00")

    def test_FLIPPING_the_control_flips_a_stored_purchase(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """*This was actually a refund* is a real edit, and it is the control.

        The magnitude is unchanged and only the direction moves, which is what
        the form posts when the owner changes the select and nothing else.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "50.00", "direction": "refund"},
            )

            assert resp.status_code == 200
            db.session.refresh(entry)
            assert entry.amount == Decimal("-50.00")

    def test_an_AMOUNT_without_its_DIRECTION_is_refused(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The pair travels together, and a half-submission is not guessed.

        No browser can produce this -- the form renders both controls and
        submits every control it holds -- so it is a stale or hand-made
        payload.  Guessing a sign either way writes money the owner did not
        state, which is the same choice ``_reject_incomplete_new_envelope``
        makes about a new envelope stated by halves.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "75.00"},
            )

            assert resp.status_code == 422
            db.session.refresh(entry)
            assert entry.amount == Decimal("50.00")

    def test_an_edit_touching_NEITHER_still_works(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The other half of "both or neither", and the case that keeps the
        refusal from being a blanket one.

        A settled parent renders no amount box and no direction control, so a
        date-only save sends neither -- and must still be accepted.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"description": "Kroger Market"},
            )

            assert resp.status_code == 200
            db.session.refresh(entry)
            assert entry.description == "Kroger Market"
            assert entry.amount == Decimal("50.00")

    def test_BOTH_forms_render_the_direction_control(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The control has to EXIST, or every case above grades a payload
        no screen can produce.

        Reads the rendered entry list rather than asserting on the template
        source: what a browser is handed is the subject.  The EDIT form renders
        only for the entry being edited (``?editing=<id>``), so the list is
        asked for in both states rather than once.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            listing = auth_client.get(
                f"/transactions/{txn.id}/entries",
            ).data.decode()
            editing = auth_client.get(
                f"/transactions/{txn.id}/entries?editing={entry.id}",
            ).data.decode()

            assert listing.count('name="direction"') == 1, (
                "the ADD form states the direction"
            )
            assert editing.count('name="direction"') == 2, (
                "the EDIT form states it too, beside the add form"
            )
            assert '<option value="refund"' in editing
            # The stored sign PRESELECTS the control, and the box shows the
            # magnitude -- so opening a refund and saving it unchanged writes
            # the same figure back rather than flipping it.
            assert 'value="50.00"' in editing


class TestTheAddFormNamesAnAccount:
    """POST ``account_id`` (plan step ``credit_card:CC-5-2``, ruling **R-CC15**).

    The form posts the control only when the owner has a card to choose
    (ruling **R-CC34**), so the payloads above, which post none, are the
    byte-identical case.  Here the field is posted: the card is written, a
    foreign account answers the security response rule's 404 as a designed
    fragment, and the ``CC`` box beside the card is the door's refusal at 400.
    Through ``CC-5-1`` no ``NotFoundError`` could reach this route's handler
    at all, so the status it now answers is asserted rather than assumed.
    """

    @staticmethod
    def _card(seed_user):
        return create_account_of_type(
            seed_user, db.session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
        )

    def test_a_swipe_posted_on_the_card_is_stored_on_the_card(
        self, app, auth_client, seed_user, seed_periods, seed_entry_template,
    ):
        """The written purchase names the card; its row stays on checking."""
        with app.app_context():
            card = self._card(seed_user)
            db.session.commit()
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "60.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                    "account_id": str(card.id),
                },
            )
            assert resp.status_code == 200

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.account_id == card.id
            assert entry.transaction.account_id == seed_user["account"].id

    def test_another_owners_account_is_404_as_a_designed_fragment(
        self, app, auth_client, seed_user, seed_periods, seed_entry_template,
        second_user,
    ):
        """404 for "not yours", rendered so the surface reads the reason.

        The same status a missing id gets (the door raises one exception for
        both), carrying the designed-fragment marker so htmx swaps the banner
        in rather than dropping the body.  Nothing is written.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            for foreign_id in (second_user["account"].id, 999_999):
                resp = auth_client.post(
                    f"/transactions/{txn.id}/entries",
                    data={
                        "amount": "60.00",
                        "description": "Kroger",
                        "purchased_on": "2026-01-05",
                        "account_id": str(foreign_id),
                    },
                )
                assert resp.status_code == 404, foreign_id
                assert resp.headers.get("Shekel-Designed-Fragment") == "1"
                assert b"Account not found." in resp.data
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0

    def test_the_cc_box_beside_the_card_is_refused(
        self, app, auth_client, seed_user, seed_periods, seed_entry_template,
    ):
        """Ruling **R-CC35**: the box stays, and the door refuses it beside the card."""
        with app.app_context():
            card = self._card(seed_user)
            db.session.commit()
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "60.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                    "account_id": str(card.id),
                    "is_credit": "on",
                },
            )
            assert resp.status_code == 400
            assert b"not both" in resp.data
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0
