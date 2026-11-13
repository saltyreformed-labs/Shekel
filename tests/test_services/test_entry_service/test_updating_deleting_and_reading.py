"""The update and delete doors, the future-dated entry both write doors refuse, and reading a
transaction's entries.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.transaction_entry import TransactionEntry
from app.services import entry_service
from app.exceptions import NotFoundError, ValidationError
from app.utils.dates import display_today
from tests._test_helpers import typed

from ._helpers import _make_entry


# ── Update Tests ──────────────────────────────────────────────────


class TestUpdateEntry:
    """Tests for entry_service.update_entry()."""

    def test_update_entry_amount(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update amount on an existing entry."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="50.00")

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, figure=typed(Decimal("75.00")),
            )

            assert updated.amount == Decimal("75.00")
            assert updated.id == entry.id

    def test_update_entry_credit_toggle(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Toggle is_credit from False to True."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], is_credit=False)

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, is_credit=True,
            )

            assert updated.is_credit is True

    def test_update_entry_description(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update description field."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], description="Old")

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, description="New store",
            )

            assert updated.description == "New store"

    def test_update_entry_date(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update purchased_on field."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            new_date = date(2026, 1, 10)
            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, purchased_on=new_date,
            )

            assert updated.purchased_on == new_date

    def test_update_entry_multiple_fields(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Update multiple fields in one call."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id,
                figure=typed(Decimal("99.99")),
                description="Updated",
                is_credit=True,
            )

            assert updated.amount == Decimal("99.99")
            assert updated.description == "Updated"
            assert updated.is_credit is True

    def test_update_entry_empty_kwargs(
        self, app, db, seed_user, seed_entry_template,
    ):
        """ValidationError when no fields to update are provided."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(ValidationError, match="No fields to update"):
                entry_service.update_entry(entry.id, seed_user["user"].id)

    def test_update_entry_unknown_kwargs(
        self, app, db, seed_user, seed_entry_template,
    ):
        """ValidationError when unknown field names are passed."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(ValidationError, match="Cannot update fields"):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id,
                    transaction_id=999,
                )

    def test_update_entry_rejects_other_user(
        self, app, db, seed_user, seed_second_user, seed_entry_template,
    ):
        """NotFoundError when updating an entry the user does not own."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(NotFoundError):
                entry_service.update_entry(
                    entry.id, seed_second_user["user"].id,
                    figure=typed(Decimal("99.00")),
                )

    def test_update_entry_nonexistent(self, app, db, seed_user):
        """NotFoundError when entry_id does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.update_entry(
                    999999, seed_user["user"].id, figure=typed(Decimal("10.00")),
                )


# ── Future-date refusal (plan step X-c0, ruling R-M) ──────────────


class TestAFutureEntryDateIsRefused:
    """Both write doors refuse an entry dated after the user's today.

    Plan step **X-c0** (``docs/audits/balance_architecture/README.md``), ruling
    R-M.  An entry records a purchase that HAPPENED; a purchase not yet made is
    what the row's remaining budget already models.  The guard is what lets the
    reservation's ``as_of`` window be DELETED at plan step X-c2 instead of
    ruled, so these tests are the control that window's deletion rests on: if
    the refusal stops firing, an entry can be dated past a reader's now again
    and the two shipping surfaces (the calendar windows, the grid does not)
    have something to disagree about once more.

    The boundary is :func:`~app.utils.dates.display_today` -- the user's civil
    date -- so these tests derive their dates from it rather than from
    ``date.today()``, which is the server's UTC day and differs from it for
    part of every evening (CI runs UTC, the dev host runs Eastern).
    """

    def test_the_create_door_refuses_tomorrow(
        self, app, db, seed_user, seed_entry_template,
    ):
        """create_entry raises on a date one day past the user's today."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            tomorrow = display_today() + timedelta(days=1)

            with pytest.raises(ValidationError) as exc_info:
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("150.00")),
                        description="Costco run I have not made",
                        purchased_on=tomorrow,
                    ),
                )

            # The message names BOTH dates: what was rejected and what the
            # boundary was, so the surface can show the user why.
            message = str(exc_info.value)
            assert tomorrow.isoformat() in message
            assert display_today().isoformat() in message

            # Nothing was written -- the refusal precedes the INSERT.
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).count() == 0

    def test_the_create_door_accepts_today(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The boundary is exclusive: the user's own today is allowed.

        The add form posts exactly this value, so a refusal here would make the
        app reject its own form.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            today = display_today()

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("42.87")),
                    description="Walmart",
                    purchased_on=today,
                ),
            )

            assert entry.purchased_on == today

    def test_the_create_door_accepts_a_backdated_purchase(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Backdating stays allowed -- the real data uses it.

        A purchase logged days after it happened, or dated into the previous
        pay period, is ordinary; only the FUTURE is refused.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            long_ago = display_today() - timedelta(days=45)

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("101.06")),
                    description="Walmart, logged late",
                    purchased_on=long_ago,
                ),
            )

            assert entry.purchased_on == long_ago

    def test_the_update_door_refuses_moving_a_date_forward(
        self, app, db, seed_user, seed_entry_template,
    ):
        """update_entry raises when the new date is past the user's today.

        This is the door that could actually produce one: the add form posts a
        hidden date fixed to today, while the edit form offers a free picker.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"],
                purchased_on=display_today() - timedelta(days=2),
            )
            original = entry.purchased_on
            tomorrow = display_today() + timedelta(days=1)

            with pytest.raises(ValidationError):
                entry_service.update_entry(
                    entry.id, seed_user["user"].id, purchased_on=tomorrow,
                )

            # The refusal precedes the setattr loop, so the in-session object
            # is untouched -- asserted unconditionally rather than behind a
            # reload that could vacuously skip.
            assert entry.purchased_on == original

    def test_the_update_door_accepts_a_backdated_move(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Correcting a date backwards -- the intended use of the picker."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])
            corrected = display_today() - timedelta(days=3)

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, purchased_on=corrected,
            )

            assert updated.purchased_on == corrected

    def test_a_partial_update_that_omits_the_date_is_unaffected(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The guard fires on the value being SET, never on the stored one.

        An amount-only edit must not be refused for a date it is not touching.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="50.00",
                purchased_on=display_today(),
            )

            updated = entry_service.update_entry(
                entry.id, seed_user["user"].id, figure=typed(Decimal("75.00")),
            )

            assert updated.amount == Decimal("75.00")
            assert updated.purchased_on == display_today()


# ── Delete Tests ──────────────────────────────────────────────────


class TestDeleteEntry:
    """Tests for entry_service.delete_entry()."""

    def test_delete_entry(self, app, db, seed_user, seed_entry_template):
        """Hard-delete an entry. Entry no longer exists in the database."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])
            entry_id = entry.id

            result = entry_service.delete_entry(entry_id, seed_user["user"].id)

            assert result == txn.id
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_delete_entry_returns_transaction_id(
        self, app, db, seed_user, seed_entry_template,
    ):
        """delete_entry returns the parent transaction_id for CC Payback sync."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            result = entry_service.delete_entry(entry.id, seed_user["user"].id)
            assert result == txn.id

    def test_delete_entry_rejects_other_user(
        self, app, db, seed_user, seed_second_user, seed_entry_template,
    ):
        """NotFoundError when deleting an entry the user does not own."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"])

            with pytest.raises(NotFoundError):
                entry_service.delete_entry(
                    entry.id, seed_second_user["user"].id,
                )

    def test_delete_entry_nonexistent(self, app, db, seed_user):
        """NotFoundError when entry_id does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.delete_entry(999999, seed_user["user"].id)


# ── Get Entries Tests ─────────────────────────────────────────────


class TestGetEntries:
    """Tests for entry_service.get_entries_for_transaction()."""

    def test_get_entries_ordered_by_date(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Entries returned in purchased_on ASC order."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            # Create entries out of chronological order.
            _make_entry(txn, user, description="Third",
                        purchased_on=date(2026, 1, 10))
            _make_entry(txn, user, description="First",
                        purchased_on=date(2026, 1, 3))
            _make_entry(txn, user, description="Second",
                        purchased_on=date(2026, 1, 7))

            entries = entry_service.get_entries_for_transaction(
                txn.id, user.id,
            )

            assert len(entries) == 3
            assert entries[0].description == "First"
            assert entries[1].description == "Second"
            assert entries[2].description == "Third"

    def test_get_entries_empty(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Empty list when transaction has no entries."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entries = entry_service.get_entries_for_transaction(
                txn.id, seed_user["user"].id,
            )
            assert entries == []

    def test_get_entries_rejects_other_user(
        self, app, db, seed_user, seed_second_user, seed_entry_template,
    ):
        """NotFoundError when requesting entries for another user's transaction."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            with pytest.raises(NotFoundError):
                entry_service.get_entries_for_transaction(
                    txn.id, seed_second_user["user"].id,
                )

    def test_get_entries_nonexistent_transaction(self, app, db, seed_user):
        """NotFoundError when transaction does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                entry_service.get_entries_for_transaction(
                    999999, seed_user["user"].id,
                )
