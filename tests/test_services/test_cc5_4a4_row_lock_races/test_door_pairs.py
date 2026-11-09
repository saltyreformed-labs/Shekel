"""Each pair of doors raced against each other: a purchase, mark paid, mark credit and an actual
correction against a delete, an archive or each other, and no pair deadlocks.
"""

from __future__ import annotations

from sqlalchemy import text

from app.extensions import db as _db
from tests._request_race import race_two_requests, signed_in_clients

from ._helpers import (
    _state,
    _groceries,
    _hotel,
    _dinner,
    _paid_hotel,
    _purchase,
    _delete,
    _mark_paid,
    _archive,
    _mark_credit,
    _save_an_actual,
)
from tests._cards import add_card


#: The purchase door's refusal of a deleted row, naming it (ruling R-CC98),
#: as the add-purchase route shows it (ruling R-CC103).
_PURCHASE_REFUSED = (
    "Groceries was deleted: a purchase cannot be recorded under it.  "
    "Reload the page."
)


#: The same refusal for a row its recurring item's ARCHIVE hid (ruling R-CC107).
_PURCHASE_REFUSED_ARCHIVED = (
    "Groceries was archived: a purchase cannot be recorded under it.  "
    "Reload the page."
)


#: Mark Paid's refusal of a deleted Hotel, as the cell shows it (R-CC102).
_PAYMENT_REFUSED = (
    "Hotel was deleted: a payment cannot be recorded under it.  "
    "Reload the page."
)


#: The same refusal for a row its recurring item's ARCHIVE hid (R-CC107).
_PAYMENT_REFUSED_ARCHIVED = (
    "Hotel was archived: a payment cannot be recorded under it.  "
    "Reload the page."
)


#: The popover Save's refusal of a deleted Hotel (ruling R-CC105).
_SAVE_REFUSED = (
    "Hotel was deleted: this change cannot be saved.  Reload the page."
)


def _movements(row_id):
    """Return ``[(amount, is the row's own payment record)]`` under *row_id*, oldest first."""
    return [
        (str(amount), covers)
        for amount, covers in _db.session.execute(
            text("SELECT amount, covers_settlement "
                 "FROM budget.transaction_entries "
                 "WHERE transaction_id = :i ORDER BY id"),
            {"i": row_id},
        ).all()
    ]


def _status_name(row_id):
    """Return *row_id*'s status name, read fresh (display only, never logic)."""
    return _db.session.execute(
        text("SELECT s.name FROM budget.transactions t "
             "JOIN ref.statuses s ON s.id = t.status_id WHERE t.id = :i"),
        {"i": row_id},
    ).scalar_one()


def _paybacks(row_id):
    """Return the ids of the LIVE paybacks naming *row_id* as their source."""
    return _db.session.execute(
        text("SELECT id FROM budget.transactions "
             "WHERE credit_payback_for_id = :i AND NOT is_deleted"),
        {"i": row_id},
    ).scalars().all()


def _body(answer):
    """Return *answer*'s response body as text."""
    return answer.response.get_data(as_text=True)


class TestPurchaseAgainstDelete:
    """The review's own race: a purchase and the owner's Delete on one occurrence."""

    def test_delete_first_the_purchase_is_refused_in_words(self, app, db, seed_user):
        """Delete lands first: the purchase waits, then meets the door's sentence; nothing is written."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = signed_in_clients(app)
            delete, purchase = race_two_requests(
                app, seed_user["user"].id,
                _delete(one, row_id), _purchase(two, row_id),
            )
            assert delete.response.status_code == 200
            assert purchase.waited
            assert purchase.response.status_code == 404
            assert _PURCHASE_REFUSED in _body(purchase)
            assert _state(row_id) == (True, 0)

    def test_purchase_first_the_delete_is_refused_naming_it(
        self, app, db, seed_user,
    ):
        """Purchase lands first: the delete waits, then is refused -- its dialog named no purchase.

        Ruling R-CC131 (developer 2026-10-04, "Refuse and redraw"), the
        clause ruling R-CC96 promised: until it, this delete removed the row
        AND the $12.34 purchase its dialog never named (rule-5 re-expression,
        developer-ruled: this read ``delete.response.status_code == 200`` and
        ``_state(row_id) == (True, 0)``).
        """
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = signed_in_clients(app)
            purchase, delete = race_two_requests(
                app, seed_user["user"].id,
                _purchase(one, row_id), _delete(two, row_id),
            )
            assert purchase.response.status_code == 200
            assert delete.waited
            assert delete.response.status_code == 400
            assert "Groceries now holds 1 purchase the page did not name" in (
                _body(delete)
            )
            assert _state(row_id) == (False, 1)


class TestMarkPaidAgainstDelete:
    """Mark Paid on a Hotel occurrence against its Delete."""

    def test_delete_first_mark_paid_is_refused_in_words(self, app, db, seed_user):
        """Delete lands first: Mark Paid waits, then its cell says the row was deleted; no payment."""
        with app.app_context():
            _template, row_id = _hotel(seed_user)
            one, two = signed_in_clients(app)
            delete, paid = race_two_requests(
                app, seed_user["user"].id,
                _delete(one, row_id), _mark_paid(two, row_id),
            )
            assert delete.response.status_code == 200
            assert paid.waited
            assert paid.response.status_code == 404
            assert _PAYMENT_REFUSED in _body(paid)
            assert _state(row_id) == (True, 0)

    def test_mark_paid_first_the_delete_removes_the_paid_row(
        self, app, db, seed_user,
    ):
        """Mark Paid lands first: the delete waits, then reads Hotel Paid and deletes it, payment too.

        The delete reads the row after Mark Paid committed, so it deletes what
        the row now is -- the $120.00 payment goes off the books with it.
        Until plan step ``balance:X-bn`` the delete had read the row before
        its row lock, and its version-pinned write answered the change with
        the route's 409; whether a Delete pressed on a row another tab has
        since paid should be told so is a stale page's question, plan step
        ``balance:X-da``'s.
        """
        with app.app_context():
            _template, row_id = _hotel(seed_user)
            one, two = signed_in_clients(app)
            paid, delete = race_two_requests(
                app, seed_user["user"].id,
                _mark_paid(one, row_id), _delete(two, row_id),
            )
            assert paid.response.status_code == 200
            assert delete.waited
            assert delete.response.status_code == 200
            assert _state(row_id) == (True, 0)


class TestPurchaseAgainstArchive:
    """A purchase on one of a definition's rows against the definition's archive."""

    def test_archive_first_the_purchase_is_refused_in_words(self, app, db, seed_user):
        """Archive lands first: the row is hidden empty, and the purchase meets the sentence.

        The sentence says "was archived" since ruling **R-CC107** (developer
        2026-09-24, "Say archived": *"(and the same for a Save or a
        purchase)"*).
        """
        with app.app_context():
            template, row_id = _groceries(seed_user)
            one, two = signed_in_clients(app)
            archive, purchase = race_two_requests(
                app, seed_user["user"].id,
                _archive(one, template.id), _purchase(two, row_id),
            )
            assert archive.response.status_code == 302
            assert purchase.waited
            assert purchase.response.status_code == 404
            assert _PURCHASE_REFUSED_ARCHIVED in _body(purchase)
            assert _state(row_id) == (True, 0)

    def test_purchase_first_the_archive_keeps_the_row(self, app, db, seed_user):
        """Purchase lands first: the archive waits, then keeps the row that now holds money."""
        with app.app_context():
            template, row_id = _groceries(seed_user)
            one, two = signed_in_clients(app)
            purchase, archive = race_two_requests(
                app, seed_user["user"].id,
                _purchase(one, row_id), _archive(two, template.id),
            )
            assert purchase.response.status_code == 200
            assert archive.waited
            assert archive.response.status_code == 302
            assert _state(row_id) == (False, 1)


class TestMarkPaidAgainstArchive:
    """Mark Paid on a Hotel occurrence against its definition's archive (ruling R-CC107)."""

    def test_archive_first_mark_paid_says_archived(self, app, db, seed_user):
        """Archive lands first: Mark Paid waits, then its cell says Hotel was archived; no payment."""
        with app.app_context():
            template, row_id = _hotel(seed_user)
            one, two = signed_in_clients(app)
            archive, paid = race_two_requests(
                app, seed_user["user"].id,
                _archive(one, template.id), _mark_paid(two, row_id),
            )
            assert archive.response.status_code == 302
            assert paid.waited
            assert paid.response.status_code == 404
            assert _PAYMENT_REFUSED_ARCHIVED in _body(paid)
            assert _state(row_id) == (True, 0)


class TestNoDoorPairDeadlocks:
    """Two of the same door on one row queue and both end cleanly -- never ``DeadlockDetected``."""

    def test_two_purchases_on_one_row_both_land(self, app, db, seed_user):
        """Two tabs each add a $12.34 purchase to Groceries at once: both land."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = signed_in_clients(app)
            first, second = race_two_requests(
                app, seed_user["user"].id,
                _purchase(one, row_id), _purchase(two, row_id),
            )
            assert first.response.status_code == 200
            assert second.waited
            assert second.response.status_code == 200
            assert _state(row_id) == (False, 2)

    def test_two_deletes_of_one_row_end_deleted_once(self, app, db, seed_user):
        """Two tabs press Delete on one Groceries occurrence: one deletes it, the other is told it is gone."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = signed_in_clients(app)
            first, second = race_two_requests(
                app, seed_user["user"].id,
                _delete(one, row_id), _delete(two, row_id),
            )
            assert first.response.status_code == 200
            assert second.waited
            assert second.response.status_code == 404
            assert _state(row_id) == (True, 0)


class TestPurchaseAgainstMarkPaid:
    """Ruling **R-CC99** (a): a purchase and Mark Paid racing on one empty envelope.

    Measured before the fix, in BOTH orders: Groceries ($300.00, nothing spent)
    ended Paid holding a $300.00 payment record AND the companion's $12.34
    purchase -- $312.34 recorded against one $300.00 envelope, the purchase
    counted twice (finding N-318's state, which it called unreachable).  Mark
    Paid decided its figure from a read taken before the purchase committed;
    the purchase door's settled-row refusal read a ``status`` the locking
    statement handed back as ``None`` after its wait.
    """

    def test_purchase_first_mark_paid_settles_at_the_purchases(
        self, app, db, seed_user,
    ):
        """Purchase lands first: Mark Paid waits, then settles Groceries at its purchases, $12.34."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = signed_in_clients(app)
            purchase, paid = race_two_requests(
                app, seed_user["user"].id,
                _purchase(one, row_id), _mark_paid(two, row_id),
            )
            assert purchase.response.status_code == 200
            assert paid.waited
            assert paid.response.status_code == 200
            assert _status_name(row_id) == "Paid"
            assert _movements(row_id) == [("12.34", False)]

    def test_mark_paid_first_the_purchase_is_refused_in_words(
        self, app, db, seed_user,
    ):
        """Mark Paid lands first: the purchase waits, then meets the settled-row refusal, naming the row."""
        with app.app_context():
            _template, row_id = _groceries(seed_user)
            one, two = signed_in_clients(app)
            paid, purchase = race_two_requests(
                app, seed_user["user"].id,
                _mark_paid(one, row_id), _purchase(two, row_id),
            )
            assert paid.response.status_code == 200
            assert purchase.waited
            assert purchase.response.status_code == 400
            assert "Groceries has settled and records a fixed figure" in (
                _body(purchase)
            )
            assert _status_name(row_id) == "Paid"
            assert _movements(row_id) == [("300.00", True)]


class TestMarkCreditAgainstDelete:
    """Ruling **R-CC99** (b): Mark Credit against the row's Delete.

    Measured before the fix: Delete first, then the stale tab's Mark Credit,
    turned the hidden $80.00 Dinner Credit and created a live $80.00 payback in
    the next period with no visible source.  Ruling **R-CC89**: "the stale Mark
    Credit gets 'not found', exactly as for another user's row".
    """

    def test_delete_first_mark_credit_is_not_found(
        self, app, db, seed_user, seed_periods,
    ):
        """Delete lands first: Mark Credit waits, then answers "not found"; no payback is born."""
        with app.app_context():
            row_id = _dinner(seed_user, seed_periods)
            one, two = signed_in_clients(app)
            delete, credit = race_two_requests(
                app, seed_user["user"].id,
                _delete(one, row_id), _mark_credit(two, row_id),
            )
            assert delete.response.status_code == 200
            assert credit.waited
            assert credit.response.status_code == 404
            assert _state(row_id) == (True, 0)
            assert _status_name(row_id) == "Projected"
            assert not _paybacks(row_id)

    def test_mark_credit_first_the_delete_takes_the_payback_too(
        self, app, db, seed_user, seed_periods,
    ):
        """Mark Credit lands first: the delete waits, then deletes the row and its payment.

        The delete reads Dinner after Mark Credit committed, so it deletes the
        row and takes its $80.00 off the card with it.  Since plan task
        plan#2929 the press puts the row on the card and writes no payback, so
        the card the fixture adds is the press's precondition; until then the
        delete took the Credit row's payback down, as a Delete of a Credit row
        always does.  Until plan step ``balance:X-bn`` the delete
        had read the row before its row lock and answered the change with the
        route's 409, keeping the payback; whether a Delete pressed on a row
        another tab has since changed should be told so is plan step
        ``balance:X-da``'s question.
        """
        with app.app_context():
            add_card(seed_user)
            row_id = _dinner(seed_user, seed_periods)
            one, two = signed_in_clients(app)
            credit, delete = race_two_requests(
                app, seed_user["user"].id,
                _mark_credit(one, row_id), _delete(two, row_id),
            )
            assert credit.response.status_code == 200
            assert delete.waited
            assert delete.response.status_code == 200
            assert _state(row_id) == (True, 0)
            assert not _paybacks(row_id)


class TestActualCorrectionAgainstDelete:
    """The popover's Actual correction on a Paid row against its Delete.

    The correction reaches the covering writer through
    ``transaction_service.apply_requested_status``'s correction arm, straight
    into the status seam -- the door review 3 measured writing a $125.00
    payment under a deleted Paid Hotel.
    """

    def test_delete_first_the_correction_is_refused_in_words(
        self, app, db, seed_user,
    ):
        """Delete lands first: the $125.00 Save waits, then its cell says the change cannot be saved."""
        with app.app_context():
            row_id = _paid_hotel(seed_user)
            one, two = signed_in_clients(app)
            correction = _save_an_actual(two, row_id, "125.00")
            delete, save = race_two_requests(
                app, seed_user["user"].id, _delete(one, row_id), correction,
            )
            assert delete.response.status_code == 200
            assert save.waited
            assert save.response.status_code == 404
            assert _SAVE_REFUSED in _body(save)
            assert _state(row_id) == (True, 0)
