"""Transaction entry route tests: updating and deleting an entry, the derived posted indicator, and
an untouched save that keeps the date's basis.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry

from tests._test_helpers import (
    an_asserted_day,
    an_entered_day,
    append_balance_assertion,
    settle_instant_on,
)
from app.services.settle_day import record_settle_day, recorded_settle_day
from app.services.pay_calendar import calendar_for
from app.services.statement_match._valuation import purchase_candidate

from ._helpers import _PURCHASED_ON, _ASSERTED_ON, _POSTED_AFTER_THE_STATEMENT, _add_entry


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
        """PATCH with is_credit=true toggles a debit entry to credit."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"is_credit": "true"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.is_credit is True

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
                data={"is_credit": "false"},
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
    ``test_settled_on_and_mutation_surfaces.py``'s :class:`TestTheSettledOnEditPath` grades.

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
