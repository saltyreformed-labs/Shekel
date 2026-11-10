"""
Shekel Budget App -- Transaction Entry Route Tests

Tests the entry CRUD endpoints (list, create, update, delete),
ownership and companion access controls, HTMX response format,
entry-transaction mismatch guards, and popover integration.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.transaction_entry import TransactionEntry
from app.models.ref import AccountType, TransactionType
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password
from app.services import account_service
from app.utils.dates import display_today

from tests._cards import add_card
from tests._test_helpers import (
    an_asserted_day,
    an_entered_day,
    append_balance_assertion,
    create_account_of_type,
    figure_source_columns,
    freeze_today,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    settle_instant_on,
)
from app.services.settle_day import (
    record_settle_day,
    recorded_settle_day,
)
from app.services.pay_calendar import calendar_for
from app.services.statement_match._valuation import purchase_candidate

# The three days the derived reconciled indicator turns on.  FIXED rather
# than today-relative: the indicator compares two STORED days, so nothing
# about it reads a clock, and a fixture that did would be calendar-dependent
# for no reason (.claude/rules/testing.md; findings N-131 / N-132).
_PURCHASED_ON = date(2026, 1, 5)
_ASSERTED_ON = date(2026, 1, 10)
_POSTED_AFTER_THE_STATEMENT = date(2026, 1, 12)


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    Entry tests use hardcoded purchase dates such as
    date(2026, 1, 5) and date(2026, 4, 12) that must fall inside the
    calendar-anchored seed_periods range.  Freezing today inside the
    seeded range keeps "which paycheck contains today" deterministic without
    disturbing those calendar values.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


def _add_entry(txn, user, amount, description,
               purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Args:
        txn: Parent Transaction object.
        user: dict with 'user' key (seed_user shape) or User object.
        amount: Decimal-compatible string or Decimal.
        description: Entry description.
        purchased_on: Date object (defaults to 2026-01-05).
        is_credit: Boolean.

    Returns:
        Committed TransactionEntry object.
    """
    uid = user["user"].id if isinstance(user, dict) else user.id
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=uid,
        amount=Decimal(str(amount)),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def _create_visible_tracked_txn(seed_user, seed_periods):
    """Create a tracked, companion-visible definition and its engine-generated row.

    ``seed_entry_template`` with ``companion_visible=True`` stated at
    creation time, to avoid session-expiry issues with in-place
    modification; the row is the definition's own (:func:`generate_row_of`,
    plan step balance:X-cf-4), as that fixture's is.

    Args:
        seed_user: The seed_user fixture dict.
        seed_periods: List of PayPeriod objects.

    Returns:
        dict with keys: template, transaction, category.
    """
    category = seed_user["categories"]["Groceries"]
    template = make_expense_template(
        db.session, seed_user, amount="500.00", name="Weekly Groceries",
        category_key="Groceries", is_envelope=True, companion_visible=True,
    )
    txn = generate_row_of(template, seed_periods[0])
    db.session.commit()

    return {"template": template, "transaction": txn, "category": category}


def _login_companion(app):
    """Create a test client and log in as the companion user.

    Must be called within an app context, after seed_companion has
    created the companion user.

    Args:
        app: The Flask application.

    Returns:
        An authenticated FlaskClient for the companion user.
    """
    comp = app.test_client()
    resp = comp.post("/login", data={
        "email": "companion@shekel.local",
        "password": "companionpass",
    })
    assert resp.status_code == 302, (
        f"Companion login failed with status {resp.status_code}"
    )
    return comp


def _create_other_user_txn():
    """Create a second owner with a pay period and a one-off of their own.

    Used for cross-user isolation tests.  The row is a rule-less
    definition's whose ``is_envelope`` is False, so it is not entry-capable.

    Returns:
        dict with keys: user, transaction.
    """
    other_user = User(
        email="other_owner@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other Owner",
    )
    db.session.add(other_user)
    db.session.flush()


    # The account_service factory requires the user to have at least one pay
    # period to anchor against.
    # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
    from datetime import date as _date
    from tests._test_helpers import open_owner_calendar as _open_calendar
    _bootstrap = _open_calendar(other_user.id, _date(2024, 1, 5))[0]
    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(
        name="Checking",
    ).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=other_user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    category = Category(
        user_id=other_user.id,
        group_name="Family",
        item_name="Groceries",
    )
    db.session.add(category)
    db.session.flush()

    periods = record_paydays_across_a_hole(
        user_id=other_user.id,
        first_payday=date(2026, 1, 2),
        num_periods=3,
        rhythm=rhythm_of(14),
    )
    db.session.flush()

    expense_type = db.session.query(TransactionType).filter_by(
        name="Expense",
    ).one()

    txn = one_off_row_of(
        periods[0],
        name="Other Groceries",
        amount=Decimal("300.00"),
        user_id=periods[0].user_id,
        account_id=account.id,
        scenario_id=scenario.id,
        transaction_type_id=expense_type.id,
        category_id=category.id,
    )
    db.session.commit()

    return {"user": other_user, "transaction": txn}


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
        """POST with the CC tick files the purchase ON the owner's card.

        Plan task plan#2930, ruling plan#2888: the form's tick (posted as
        ``is_credit``, loaded as ``on_card``) files the purchase on the one
        card, the add door writes no CC flag, and the list chips the card.
        """
        with app.app_context():
            card = add_card(seed_user, "Visa")
            db.session.commit()
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "80.00",
                    "description": "Amazon",
                    "purchased_on": "2026-01-06",
                    "is_credit": "true",
                },
            )
            assert resp.status_code == 200

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.account_id == card.id
            assert entry.is_credit is False
            assert b'title="On Visa"' in resp.data

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


class TestUpdateEntry:
    """Tests for PATCH /transactions/<txn_id>/entries/<entry_id>."""

    def test_update_amount(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with new amount updates the entry, other fields unchanged."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "75.00", "direction": "charge"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.amount == Decimal("75.00")
            assert entry.description == "Kroger"

    def test_update_description(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with new description updates only that field."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"description": "Walmart"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.description == "Walmart"
            assert entry.amount == Decimal("50.00")

    def test_update_toggle_credit_on(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with is_credit=true moves a checking purchase onto the owner's card.

        Plan task plan#3142, ruling plan#2888: the tick says where the money
        went, so it MOVES the purchase (``entry_service._details.tick_edit``)
        and writes no CC flag; it set the flag until then, which the ruling
        changed.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"is_credit": "true", "tick_drawn": "true"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert (entry.account_id, entry.is_credit) == (visa.id, False)

    def test_update_toggle_credit_off(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with is_credit=false toggles a credit entry to debit."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Amazon", is_credit=True,
            )

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"is_credit": "false", "tick_drawn": "true"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.is_credit is False

    def test_update_validation_error_zero_amount(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with amount=0 returns 422 validation error.

        **It posts the DIRECTION too, and without it this case stopped
        grading zero** (plan step ``bank_import:X-gj-2b-3``).  The amount and
        the direction are one answer, so an amount alone is refused by the
        PAIRING rule before the magnitude bound is ever reached -- the 422
        would still arrive and would say nothing about zero.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "0", "direction": "charge"},
            )
            assert resp.status_code == 422
            db.session.refresh(entry)
            assert entry.amount == Decimal("50.00")

    def test_hx_trigger_balance_changed(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH returns HX-Trigger: balanceChanged header."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "75.00", "direction": "charge"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_nonexistent_entry_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH for nonexistent entry returns 404."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/999999",
                data={"amount": "75.00", "direction": "charge"},
            )
            assert resp.status_code == 404


# ---- Delete entry (DELETE) -----------------------------------------------

class TestDeleteEntry:
    """Tests for DELETE /transactions/<txn_id>/entries/<entry_id>."""

    def test_delete_removes_from_database(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE removes the entry from the database."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")
            entry_id = entry.id

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry_id}",
            )
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_delete_returns_updated_list(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE response contains updated list without the deleted entry."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry1 = _add_entry(txn, seed_user, "50.00", "Kroger")
            _add_entry(txn, seed_user, "30.00", "Target")

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry1.id}",
            )
            assert resp.status_code == 200
            assert b"Kroger" not in resp.data
            assert b"Target" in resp.data

    def test_nonexistent_entry_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE for nonexistent entry returns 404."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/999999",
            )
            assert resp.status_code == 404

    def test_hx_trigger_balance_changed(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE returns HX-Trigger: balanceChanged header."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry.id}",
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_delete_last_entry_shows_empty_state(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Deleting the last entry shows the empty state message."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry.id}",
            )
            assert resp.status_code == 200
            assert b"No purchases recorded yet" in resp.data


# ---- Manual is_cleared toggle ------------------------------------------

class TestTheDerivedPostedIndicator:
    """The read-only indicator that replaced the cleared TOGGLE route.

    ``PATCH /transactions/<txn_id>/entries/<entry_id>/cleared`` is DELETED with
    the ``is_cleared`` column it wrote (plan step S1-c, ruling R-DH (d)).  A
    stored flag a user could flip let the indicator and the projection be set
    against each other, and when it is wrong the fact that is actually wrong is
    the POSTING DAY -- which the edit form carries, and which
    :class:`TestTheSettledOnEditPath` below grades.

    So the entry row shows a DERIVED answer, and since plan step X-f3b (ruling
    **R-FM**) it is the SAME fact the reservation buckets on: has this
    purchase's bank posting day been recorded.  It asked the account's clearing
    rule until then -- ``coverage.is_cleared(entry)`` -- which gave the tooltip
    a third state, *"posted, but after the balance you last entered, so the
    budget is still held back"*, that is no longer true of anything.  These
    tests drive the real ``GET`` list route and read the rendered row.
    """

    #: The screen-reader text of each arm -- the accessible name is what the
    #: indicator MEANS, and it is stable where an icon class is decoration.
    _INSIDE = b"Already left your account"
    _OUTSTANDING = b"Still outstanding"

    @staticmethod
    def _list(auth_client, txn_id):
        """GET the entry list for a transaction and return its HTML bytes."""
        resp = auth_client.get(f"/transactions/{txn_id}/entries")
        assert resp.status_code == 200
        return resp.data

    def test_a_purchase_with_a_posting_day_reads_posted(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A recorded posting day -> the money has left, and the row says so.

        The account asserts a balance for 2026-01-10; the purchase was made and
        taken by the bank on the 5th.  The assertion is left in the fixture
        deliberately -- the answer must not depend on it, which is what the
        sibling test below (posted AFTER that day, same answer) proves.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=_PURCHASED_ON,
            )
            record_settle_day(entry, an_entered_day(_PURCHASED_ON))
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1000.00"), settle_instant_on(_ASSERTED_ON),
            )
            db.session.commit()

            body = self._list(auth_client, txn.id)

        assert self._INSIDE in body
        assert self._OUTSTANDING not in body

    def test_a_purchase_with_no_posting_day_reads_outstanding(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A NULL settled_on is "not observed to have posted" -- outstanding.

        The conservative arm, and the default a fresh purchase is in: the
        envelope keeps holding its whole budget back until the user confirms
        the money has left, so the row must not claim otherwise.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=_PURCHASED_ON,
            )
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1000.00"), settle_instant_on(_ASSERTED_ON),
            )
            db.session.commit()

            body = self._list(auth_client, txn.id)

        assert self._OUTSTANDING in body
        assert self._INSIDE not in body

    def test_a_purchase_posted_after_the_statement_reads_posted_TOO(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """settled_on AFTER the asserted day -> posted, exactly the same.

        **Re-ruled at plan step X-f3b** (ruling **R-FM**), and this pair is the
        control for it: the row read "Still outstanding" while the indicator
        asked whether a declared balance contained the purchase, and it reads
        "Already left your account" now that it asks whether the bank took the
        money.  Which statement CLEARED that movement is a separate question the
        walk asks, and no longer one this row answers.

        It must move WITH the reservation and not merely alongside it: the same
        purchase is released from its envelope's budget
        (``cash_ledger._amounts._entry_checking_impact``), so a row still saying
        "the budget is still held back" would contradict the number beside it --
        the exact defect ``entry_list_view`` was created to make unrepresentable.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=_PURCHASED_ON,
            )
            record_settle_day(entry, an_entered_day(_POSTED_AFTER_THE_STATEMENT))
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1000.00"), settle_instant_on(_ASSERTED_ON),
            )
            db.session.commit()

            body = self._list(auth_client, txn.id)

        assert self._INSIDE in body
        assert self._OUTSTANDING not in body

    def test_a_credit_purchase_shows_no_indicator_at_all(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A credit-card purchase never touches checking, so the question is moot.

        It leaves through its own CC Payback sibling rather than this account's
        statement, so the reservation ignores its dates entirely.  Rendering
        either arm of the indicator for one would be answering a question that
        does not apply.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _add_entry(
                txn, seed_user, "50.00", "Amazon",
                purchased_on=_PURCHASED_ON, is_credit=True,
            )
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1000.00"), settle_instant_on(_ASSERTED_ON),
            )
            db.session.commit()

            body = self._list(auth_client, txn.id)

        assert b"Amazon" in body, "the entry must actually have rendered"
        assert self._INSIDE not in body
        assert self._OUTSTANDING not in body

    def test_the_cleared_toggle_route_is_gone(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The deleted endpoint answers 404 -- no half-live write door.

        A route left routed after its service function is deleted is how a
        surface keeps a control the model no longer supports.  Asserted rather
        than assumed, because a stale template button pointing here would
        otherwise fail silently at the browser.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}/cleared",
            )

        assert resp.status_code == 404


class TestTheSettledOnEditPath:
    """``settled_on`` on the entry edit form -- the fact the toggle stood in for.

    The write door a user has for "when did my bank actually take this",
    replacing the toggle (plan step S1-c).  It is on the EDIT form and
    deliberately not on the ADD form: at the moment a purchase is recorded
    there is nothing to have observed, and a value typed then would be a
    forecast in a fact column.
    """

    def test_flipping_a_TICKED_purchase_to_card_releases_its_link(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A reproducible unhandled 500, found by X-f3b's trace 2026-08-15.

        ``ck_transaction_entries_card_purchase_clears_nowhere`` (plan step
        X-f3a-1) refuses a purchase that is BOTH flagged for a card and linked
        to a statement, and the edit door's flag flip once left the link
        standing, an ``IntegrityError`` no handler caught (reproduced on a
        production clone against entry 87).  Since plan task plan#3142 the
        tick MOVES the purchase onto the card instead of flagging it, and the
        link is a checking statement's, which never showed money on the card,
        so the move releases it (``movement_account.re_point_movement``,
        ``fk_transaction_entries_reconciled_by`` its storage half).  The
        posting day was checking's, so it goes with the old account
        (``entry_service._details.settle_day_after_move``).  Asserted on every
        column -- a fix that swallowed the move would satisfy a status check
        alone.
        """
        with app.app_context():
            visa = add_card(seed_user, "Visa")
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=_PURCHASED_ON,
            )
            record_settle_day(entry, an_entered_day(_PURCHASED_ON))
            statement = append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1000.00"), settle_instant_on(_ASSERTED_ON),
            )
            entry.reconciled_by_id = statement.id
            db.session.commit()
            entry_id = entry.id

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry_id}",
                data={"is_credit": "true", "tick_drawn": "true"},
            )

            assert resp.status_code == 200
            db.session.expire_all()
            moved = db.session.get(TransactionEntry, entry_id)
            assert (moved.account_id, moved.is_credit) == (visa.id, False)
            assert moved.reconciled_by_id is None, (
                "a checking statement never showed money on the card, so the "
                "statement it named is released rather than left to violate "
                "the key"
            )
            assert (moved.settled_on, moved.settled_day_basis_id) == (None, None)

    def test_the_edit_form_records_a_posting_day(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCHing ``settled_on`` stores it and fires ``balanceChanged``.

        The trigger matters as much as the column: recording a posting day can
        move every rendered projection (it is what lets the reservation stop
        holding the purchase back), so a surface showing a balance must
        recompute.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )
            assert entry.settled_on is None

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": "2026-01-07"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == date(2026, 1, 7)

    def test_an_empty_value_CLEARS_the_posting_day(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """An emptied date input puts the purchase back among the outstanding.

        This is a real user action -- "I marked this posted and my statement
        does not actually show it" -- and it is the reason the schema field
        carries ``allow_none``: without it an empty input would be dropped as
        "not provided" and the wrong observation would be unretractable
        through the UI.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )
            record_settle_day(entry, an_entered_day(date(2026, 1, 7)))
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": ""},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_a_posting_day_before_the_purchase_is_refused(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Money cannot leave the account before it was spent.

        The door's own check (``entry_service._reject_settled_before_purchase``)
        so the user gets a message naming both dates rather than a 500 from the
        ``ck_transaction_entries_settled_not_before_purchase`` IntegrityError.

        400, not 422: the date parses and the SCHEMA is satisfied, so this is
        the service refusing a semantically impossible pair rather than the
        form failing validation -- the same split every other entries route
        makes.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": "2026-01-04"},
            )

            assert resp.status_code == 400
            assert b"before you make it" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_a_posting_day_after_today_is_REFUSED(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The upper bound plan step X-f3b added, and why it inverted.

        **This test asserted the OPPOSITE until X-f3b** (ruling **R-FM**), on a
        reason that was true then: a forward posting day was the conservative
        direction, because no assertion closed over it, so the purchase stayed
        outstanding and its whole budget stayed reserved.  A recorded posting
        day is now the moment the money leaves the book, so a forward one
        RELEASES the reservation today and books the cash later -- already-spent
        money back in today's projection, which is exactly what
        ``status_seam.reject_future_settle_day`` refuses on a transaction and
        why the two columns' rationales no longer differ.

        Nothing expressible is lost: "I bought this and my bank has not taken it
        yet" is ``settled_on`` LEFT EMPTY, which is what that state has always
        meant and which holds the whole budget back.  Measured before the bound
        was added: ZERO of 91 production purchases carried a forward day.

        The refusal must also leave the stored value ALONE -- a guard that
        refuses and writes anyway is worse than no guard.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )
            forward = display_today() + timedelta(days=3)

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": forward.isoformat()},
            )

            assert resp.status_code == 400
            assert b"cannot be in the future" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_another_users_entry_cannot_be_dated(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCHing another user's entry returns 404 and writes nothing.

        Carried over from the retired toggle class: the ownership guard is the
        same one on the same shape of door, and dropping the toggle must not
        drop its IDOR coverage.
        """
        with app.app_context():
            other = _create_other_user_txn()
            other_entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=other["transaction"].id,
                account_id=other["transaction"].account_id,
                owner_id=other["transaction"].user_id,
                user_id=other["user"].id,
                amount=Decimal("50.00"),
                description="Other",
                purchased_on=date(2026, 1, 5),
                is_credit=False,
            )
            db.session.add(other_entry)
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{other['transaction'].id}"
                f"/entries/{other_entry.id}",
                data={"settled_on": "2026-01-07"},
            )
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, other_entry.id,
            ).settled_on is None

    def test_a_cross_transaction_entry_id_cannot_be_dated(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The parameter-confusion guard, carried over from the toggle class.

        An entry id that does not belong to the transaction in the URL is 404,
        so a companion cannot reach a non-visible transaction's entries by
        pairing them with a visible transaction's id.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")
            other_txn = _create_visible_tracked_txn(
                seed_user, seed_periods,
            )["transaction"]

            resp = auth_client.patch(
                f"/transactions/{other_txn.id}/entries/{entry.id}",
                data={"settled_on": "2026-01-07"},
            )
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None


# ---- Surface routing: host param + desktop OOB cell refresh -------------

class TestEntryMutationSurfaces:
    """The ``host`` query param and the desktop OOB grid-cell refresh.

    Every entries-CRUD control in ``grid/_transaction_entries.html``
    ships ``?host=<prefix>`` so the route's re-render reconstructs the
    same entry-list root id the request's ``hx-target`` named (bare
    ``entry-list-<id>`` for the desktop popover, ``entry-list-tp-<id>``
    for the inline mobile/companion card list).  Desktop-popover
    mutations additionally carry an out-of-band re-render of the parent
    transaction's grid cell, because an entry mutation changes the
    envelope's "spent / budget" progress display and the primary swap
    only replaces the entry list inside the popover -- without the OOB
    fragment the on-grid amount stays stale until a full page reload
    (the 2026-06-11 grid-bug fix batch).
    """

    def test_create_carries_oob_cell_with_updated_progress(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A popover-surface create returns the OOB cell with new progress.

        The envelope budget is $500 (seed_entry_template) and the new
        entry is $50, so the re-rendered cell's progress display must
        read ``$50 / $500`` (entry total / estimated).

        **It read ``50 / 500`` until plan step ``bank_import:X-gj-2b-3``**
        (developer ruling 2026-09-01).  The cell formatted these two figures
        with a raw ``"{:,.0f}".format`` rather than the ``money`` macro gate
        B7 makes the one currency formatter; the raw spelling could not show a
        sign while every purchase was positive, and ruling
        **bank_import:R-II** made a refund a NEGATIVE purchase.  The macro is
        what the mobile card beside it has always used for the same two
        numbers.
        """
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
            html = resp.data.decode()
            # Primary swap body: the popover's bare entry-list id.
            assert f'id="entry-list-{txn.id}"' in html
            # OOB fragment: the grid cell wrapper rides along...
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html
            # ...carrying the refreshed spent/budget progress display.
            assert "$50 / $500" in html

    def test_update_carries_oob_cell(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A popover-surface amount update returns the OOB cell.

        $50 entry updated to $75 against the $500 budget -> the cell
        progress must read ``$75 / $500`` -- through the ``money`` macro since
        plan step ``bank_import:X-gj-2b-3``, for the reason
        :meth:`test_create_carries_oob_cell_with_updated_progress` states.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "75.00", "direction": "charge"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html
            assert "$75 / $500" in html

    def test_delete_carries_oob_cell(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A popover-surface delete returns the OOB cell.

        Deleting the only $50 entry leaves zero spent; with no entries
        the cell drops the progress display and shows the estimated
        amount (``500``) again.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry.id}",
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html
            assert "50 / 500" not in html

    def test_recording_a_posting_day_carries_oob_cell(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A popover-surface posting-day edit returns the OOB cell.

        Successor to the cleared-toggle case (plan step S1-c): the toggle
        route is deleted and ``settled_on`` on the edit form is the door that
        records the same fact.  It needs the OOB cell for the same reason the
        toggle did -- recording a posting day changes what the envelope holds
        back, so the on-grid "spent / budget" figure behind the popover goes
        stale without it.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": "2026-01-07"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html

    def test_host_tp_reconstructs_inline_card_list_id(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """``?host=tp`` re-renders under the inline card's prefixed id.

        The inline mobile/companion card list targets
        ``#entry-list-tp-<id>``; the response must reconstruct that id
        (and thread the host through its own CRUD controls) so the swap
        lands on the element the request named, and it must NOT carry
        the desktop OOB cell -- the companion page has no
        ``#txn-cell-<id>`` element to swap into.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries?host=tp",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'id="entry-list-tp-{txn.id}"' in html
            assert f'id="entry-list-{txn.id}"' not in html
            # CRUD controls inside the list re-carry the host.
            assert "host=tp" in html
            # No desktop OOB cell on the inline-card surface.
            assert f'id="txn-cell-{txn.id}"' not in html

    def test_invalid_host_degrades_to_popover_surface(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A host outside the short-token shape renders the bare surface.

        Free-form input must not be echoed into a DOM id; the route
        treats it as the popover surface (bare id + OOB cell).
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries?host=NOT%20a%20token!",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'id="entry-list-{txn.id}"' in html
            assert "NOT a token" not in html
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html

    def test_list_entries_honors_host(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET list (edit/cancel buttons) reconstructs the prefixed id too."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(
                f"/transactions/{txn.id}/entries?host=tp",
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'id="entry-list-tp-{txn.id}"' in html
            assert f'id="entry-list-{txn.id}"' not in html

    def test_full_edit_lazy_entry_list_swaps_outerhtml(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The popover's lazy entries container replaces itself outerHTML.

        The list_entries response's ROOT element is another div with
        this same ``entry-list-<id>`` id; the default innerHTML swap
        nested two identically-id'd elements until the first CRUD swap
        collapsed them -- invalid HTML, and any other
        ``#entry-list-<id>`` resolution (CRUD targets, OOB fragments)
        bound ambiguously in the interim.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            start = html.index('hx-trigger="load"')
            tag = html[start:html.index(">", start)]
            assert 'hx-swap="outerHTML"' in tag
            assert f'id="entry-list-{txn.id}"' in tag

    def test_companion_never_receives_oob_cell(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """A companion stripping ``host`` must not get the owner's cell.

        The OOB fragment is the OWNER's desktop-grid cell, whose
        aria-label/title markup includes ``txn.notes`` -- a field no
        companion-reachable surface renders.  ``host`` is
        client-controlled, so the gate must be ownership, not the
        param: a companion request without ``host`` gets the entry
        list only.
        """
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            txn.notes = "owner-private-note"
            db.session.commit()

            comp = _login_companion(app)
            resp = comp.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "45.00",
                    "description": "Aldi",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # Entry list still returned (the mutation itself is allowed)...
            assert f'id="entry-list-{txn.id}"' in html
            # ...but no owner-only OOB cell, and no notes leak.
            assert f'id="txn-cell-{txn.id}"' not in html
            assert "owner-private-note" not in html


# ---- Entry / transaction ID mismatch ------------------------------------

class TestEntryTransactionMismatch:
    """Tests that entry_id must belong to the txn_id in the URL."""

    @staticmethod
    def _create_second_tracked_txn(seed_periods, seed_entry_template):
        """Generate the fixture's definition a second row, in the second paycheck.

        Returns:
            Transaction object in the second pay period.
        """
        txn2 = generate_row_of(seed_entry_template["template"], seed_periods[1])
        db.session.commit()
        return txn2

    def test_patch_entry_wrong_txn_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with entry_id from a different transaction returns 404."""
        with app.app_context():
            txn1 = seed_entry_template["transaction"]
            txn2 = self._create_second_tracked_txn(
                seed_periods, seed_entry_template,
            )
            # Entry belongs to txn2.
            entry = _add_entry(txn2, seed_user, "50.00", "Aldi")

            # Try to PATCH via txn1's URL.
            resp = auth_client.patch(
                f"/transactions/{txn1.id}/entries/{entry.id}",
                data={"amount": "75.00", "direction": "charge"},
            )
            assert resp.status_code == 404

    def test_delete_entry_wrong_txn_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE with entry_id from a different transaction returns 404."""
        with app.app_context():
            txn1 = seed_entry_template["transaction"]
            txn2 = self._create_second_tracked_txn(
                seed_periods, seed_entry_template,
            )
            entry = _add_entry(txn2, seed_user, "50.00", "Aldi")

            resp = auth_client.delete(
                f"/transactions/{txn1.id}/entries/{entry.id}",
            )
            assert resp.status_code == 404

    def test_companion_mismatch_blocks_non_visible_entry(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template, seed_companion,
    ):
        """Companion cannot modify entries from non-visible txns via URL confusion.

        Attack scenario: companion sends PATCH to a visible transaction's
        URL but includes an entry_id belonging to a non-visible transaction.
        The entry-transaction mismatch guard returns 404.
        """
        with app.app_context():
            # Make the first template companion-visible.
            template_visible = seed_entry_template["template"]
            template_visible.companion_visible = True
            db.session.commit()

            txn_visible = seed_entry_template["transaction"]

            # Create a second definition that is NOT companion-visible, and
            # its own row beside the visible one's.
            template_hidden = make_expense_template(
                db.session, seed_user, amount="300.00",
                name="Secret Groceries", category_key="Groceries",
                is_envelope=True, companion_visible=False,
            )
            txn_hidden = generate_row_of(template_hidden, seed_periods[0])
            db.session.commit()

            # Entry on the hidden transaction.
            entry = _add_entry(txn_hidden, seed_user, "50.00", "Secret Store")

            # Companion logs in and uses visible txn URL with hidden entry ID.
            comp = _login_companion(app)
            resp = comp.patch(
                f"/transactions/{txn_visible.id}/entries/{entry.id}",
                data={"amount": "75.00", "direction": "charge"},
            )
            assert resp.status_code == 404


# ---- Companion access ----------------------------------------------------

class TestCompanionAccess:
    """Tests for companion user access to entry routes."""

    def test_companion_lists_entries_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can list entries on a companion-visible transaction."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            _add_entry(txn, seed_user, "50.00", "Kroger")

            comp = _login_companion(app)
            resp = comp.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"Kroger" in resp.data

    def test_companion_creates_entry_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can create entries; entry.user_id is the companion's ID."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]

            comp = _login_companion(app)
            resp = comp.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "45.00",
                    "description": "Aldi",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.user_id == seed_companion["user"].id

    def test_companion_updates_entry_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can update entries on companion-visible transactions."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            comp = _login_companion(app)
            resp = comp.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "60.00", "direction": "charge"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.amount == Decimal("60.00")

    def test_companion_deletes_entry_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can delete entries on companion-visible transactions."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")
            entry_id = entry.id

            comp = _login_companion(app)
            resp = comp.delete(
                f"/transactions/{txn.id}/entries/{entry_id}",
            )
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_companion_rejected_for_non_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template, seed_companion,
    ):
        """Companion gets 404 for transactions not flagged companion_visible."""
        with app.app_context():
            # companion_visible defaults to False.
            txn = seed_entry_template["transaction"]
            comp = _login_companion(app)
            resp = comp.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 404

    def test_companion_rejected_for_other_owner_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion gets 404 for transactions belonging to a different owner."""
        with app.app_context():
            other = _create_other_user_txn()
            comp = _login_companion(app)
            resp = comp.get(
                f"/transactions/{other['transaction'].id}/entries",
            )
            assert resp.status_code == 404

    def test_both_users_see_all_entries(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Both owner and companion see all entries on a shared transaction."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            # Owner creates an entry.
            _add_entry(txn, seed_user, "50.00", "Kroger")
            # Companion creates an entry via route.
            comp = _login_companion(app)
            comp.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "30.00",
                    "description": "Aldi",
                    "purchased_on": "2026-01-06",
                },
            )

            # Owner sees both.
            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert b"Kroger" in resp.data
            assert b"Aldi" in resp.data

            # Companion sees both.
            resp = comp.get(f"/transactions/{txn.id}/entries")
            assert b"Kroger" in resp.data
            assert b"Aldi" in resp.data


# ---- Popover integration ------------------------------------------------

class TestPopoverIntegration:
    """Tests for entry section rendering in the full edit popover."""

    def test_tracked_txn_has_entries_section(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Full edit popover for tracked transaction contains entries section."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"Purchases" in resp.data
            # Verify the lazy-load hx-get URL is present.
            expected_url = f"/transactions/{txn.id}/entries"
            assert expected_url.encode() in resp.data

    def test_non_tracked_txn_no_entries_section(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Full edit popover for non-tracked transaction has no entries section."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

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

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"Purchases" not in resp.data


class TestAnUntouchedSaveDoesNotLaunderTheDaysBASIS:
    """Plan step **X-az**: the ECHO rule, at the door it was measured on.

    **This is the defect two independent adversarial reviews found in X-az's
    first build, and it is a REGRESSION they measured rather than reasoned.**
    The purchase popover renders the Posted date input on every entry, prefilled
    with the stored day and OUTSIDE the settled-parent guard -- so on a closed
    envelope it is the only editable control there is.  An owner who opens the
    row and saves re-submits the day it already carried.

    Wrapping that submission as ``entered`` unconditionally rewrote the
    reconcile panel's ``asserted`` UPPER BOUND as the owner's own typing, with
    the day unchanged -- so nothing released the clearing link and nothing
    signalled it -- and ``CandidateRow.expected_window`` then collapsed the
    purchase from the span ``(purchased_on, settled_on)`` to a POINT at the
    assertion day, out of reach of its own bank line.  An unexplained line is
    what the merchant policy offers to RECORD, which is the duplicate mechanism
    finding **N-332** prices at 50 purchases and `$3,590.00`.

    Measured on production 2026-08-22: **59 of 66 linked purchases,
    `$4,173.07`**, one innocuous save each.
    """

    _MADE_ON = date(2026, 1, 5)
    _ASSERTED_FOR = date(2026, 2, 20)

    def _ticked_purchase(self, txn, seed_user):
        """Return a purchase in the state the reconcile panel's tick leaves.

        An ``asserted`` day -- the day a BALANCE was asserted for, which is an
        upper bound -- plus the link naming that assertion.
        """
        entry = _add_entry(
            txn, seed_user, "18.64", "Food Lion",
            purchased_on=self._MADE_ON,
        )
        anchor = append_balance_assertion(
            db.session, seed_user["account"],
            Decimal("1000.00"), settle_instant_on(self._ASSERTED_FOR),
        )
        record_settle_day(entry, an_asserted_day(self._ASSERTED_FOR))
        entry.reconciled_by_id = anchor.id
        db.session.commit()
        return entry

    def test_re_submitting_the_stored_day_keeps_the_asserted_basis(
        self, app, db, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The defect itself, through the real PATCH the popover sends."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = self._ticked_purchase(txn, seed_user)

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": self._ASSERTED_FOR.isoformat()},
            )

            assert resp.status_code == 200
            db.session.expire_all()
            saved = db.session.get(TransactionEntry, entry.id)
            assert recorded_settle_day(saved) == an_asserted_day(
                self._ASSERTED_FOR,
            ), "an untouched save laundered a BOUND into the owner's own day"

    def test_the_window_the_matcher_bounds_by_still_spans(
        self, app, db, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """What the basis is FOR, asserted at the reader rather than the column.

        The span is the whole cost of the defect: collapse it and the purchase
        is unreachable from the bank line that explains it.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = self._ticked_purchase(txn, seed_user)

            auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": self._ASSERTED_FOR.isoformat()},
            )

            db.session.expire_all()
            saved = db.session.get(TransactionEntry, entry.id)
            assert purchase_candidate(
                saved, calendar_for(seed_user["user"].id),
            ).expected_window == (
                self._MADE_ON, self._ASSERTED_FOR,
            )

    def test_a_day_the_owner_really_MOVED_is_their_own(
        self, app, db, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The firing half: a genuine correction still records ``entered``.

        Without it the rule is satisfied by one that never restates the basis,
        which would report a day the owner typed as one a balance bounded --
        the same laundering in the other direction.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = self._ticked_purchase(txn, seed_user)
            corrected = self._ASSERTED_FOR - timedelta(days=3)

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": corrected.isoformat()},
            )

            assert resp.status_code == 200
            db.session.expire_all()
            saved = db.session.get(TransactionEntry, entry.id)
            assert recorded_settle_day(saved) == an_entered_day(corrected)
            # A day that MOVED still releases the observation, which is the
            # rule the echo test above must not have weakened.
            assert saved.reconciled_by_id is None


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
        """The CC tick beside "Paid from" naming the card is refused: two controls, one fact.

        Ruling **R-CC35** kept the box; plan task plan#2930 made it the tick
        that files the purchase on the card, so it beside an account other than
        the row's is still refused, by the same sentence.
        """
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
                    "is_credit": "true",
                },
            )
            assert resp.status_code == 400
            assert b"not both" in resp.data
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0
