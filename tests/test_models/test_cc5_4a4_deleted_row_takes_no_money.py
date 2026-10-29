"""Plan step ``credit_card:CC-5-4a-4``: the database keeps money out of a deleted row.

Ruling **R-CC89** (developer 2026-09-23), its first layer: *"The database refuses
any payment or purchase written under a deleted row, so no door, now or later,
can do it."*, and **R-CC92** extending it the same day ("Refuse both ways"): *"The
database also refuses hiding a row that still holds a payment or purchase,
except a transfer's half (BAL-532's, until X-bi-6-4). The check runs when the
change is saved, so a door that takes the money off and hides the row in the
same save still works."*  Review 3 measured the door that made the rule
necessary: a stale popover's Actual correction wrote a $125.00 payment record
under a deleted Paid Hotel, which locked its pay period and refused Reset for
good.  The pages' and writers' layers are
``tests/test_routes/test_cc5_4a4_hidden_row_doors.py``; this grades
:mod:`app.deleted_row_infrastructure`'s two triggers against the database the
suite runs on, with raw SQL where a writer nobody enumerated would use it.

Four things are graded, each against its control:

* **a movement cannot ARRIVE under a deleted row** -- an ``INSERT`` (raw, and
  through the ORM) or an ``UPDATE`` re-pointing one there is refused, and the
  same statement against a live row lands;
* **a row cannot be HIDDEN holding one** -- refused at COMMIT, so the delete
  door's own shape (the money off and the row hidden in one save) commits and
  an empty row hides; a transfer's twin was excepted until plan step
  ``balance:X-bi-6-4d-2``, which retired the exception (design D7) and gave
  the TRANSFER the same rule, so neither a twin nor a transfer can be hidden
  holding a payment (finding **BAL-532**'s state, unstorable since);
* **what is neither passes** -- a movement a hidden transfer holds can still
  be written in place and taken off inside the save that hides it, so the
  delete door's own order is not refused by this rule;
* **both are installed at head**, where the suite's template carries them.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from app.deleted_row_infrastructure import DELETED_ROW_TRIGGERS
from app.extensions import db as _db
from app.models.transaction_entry import TransactionEntry
from app.services import transaction_service
from tests._test_helpers import (
    add_entry,
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
    generate_row_of,
    make_expense_template,
    refused_by_database_rule,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a one-off envelope as the grid's create door
# writes it (the convention ``test_cc5_4a4_row_keeps_its_movements`` keeps).
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_one_off_envelope,
)

#: The arrival arm's refusal, as the database words it.
_ARRIVAL_REFUSED = "was deleted: a payment or purchase cannot be recorded under it"

#: The hiding arm's refusal, as the database words it.
_HIDING_REFUSED = "was deleted while it still holds a recorded payment or purchase"

#: The transfer hiding arm's refusal (plan step ``balance:X-bi-6-4d-2``).
_TRANSFER_HIDING_REFUSED = "was deleted while it still holds a recorded payment: take it off"

#: Every column of a movement but its key and its row, so a raw statement can
#: copy one under another row the way a bulk writer would.
_COPIED = ", ".join(
    column.name for column in TransactionEntry.__table__.columns
    if column.name not in ("id", "transaction_id")
)


def _day(seed_user):
    """A purchase day inside the bootstrap period, after the books opened."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _holding(seed_user, name):
    """A one-off envelope holding a $25.00 purchase; returns ``(row, entry)``."""
    row = a_one_off_envelope(seed_user, name=name)
    add_entry(
        _db.session, seed_user, row, Decimal("25.00"), _day(seed_user),
    )
    _db.session.commit()
    entry = _db.session.query(TransactionEntry).filter_by(
        transaction_id=row.id,
    ).one()
    return row, entry


def _hide(row_id):
    """Stage ``UPDATE ... SET is_deleted`` on *row_id*; the caller commits."""
    _db.session.execute(
        text("UPDATE budget.transactions SET is_deleted = TRUE WHERE id = :id"),
        {"id": row_id},
    )


def _paid_transfer(seed_user):
    """A settled $100.00 transfer, committed; returns it."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "R-CC89 Savings",
    )
    _db.session.commit()
    transfer = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], amount=Decimal("100.00"),
    )
    _db.session.commit()
    return transfer


def _hiding_a_paid_transfer(seed_user):
    """BAL-532's state, STAGED and not committed: a paid transfer hidden holding its payments.

    Hidden the way ``transfer_service.delete_transfer(..., soft=True)`` left it
    until plan step ``balance:X-bi-6-4d-2`` -- the transfer flagged, the
    payments in place.  It flagged both twins too until plan step
    ``balance:X-bi-6-4d-2``, where no twin is born: that statement reached 0
    rows (measured) and is gone.  The hiding rule is checked at COMMIT, so
    the state exists only inside this save (the delete door takes the payments
    off in the same save since ``X-bi-6-4d-2``, ruling **credit_card:R-CC75**); the
    caller commits, rolls back, or takes the payments off first.  Returns
    ``(transfer id, its expense side's record id)``.
    """
    transfer = _paid_transfer(seed_user)
    _db.session.execute(
        text("UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"),
        {"t": transfer.id},
    )
    record_id = _db.session.execute(
        text("SELECT id FROM budget.transaction_entries "
             "WHERE expense_transfer_id = :t"),
        {"t": transfer.id},
    ).scalar_one()
    return transfer.id, record_id


def _held_by_transfer(transfer_id):
    """How many records transfer *transfer_id*'s sides hold, read from the database."""
    return _db.session.execute(
        text("SELECT count(*) FROM budget.transaction_entries "
             "WHERE expense_transfer_id = :t OR income_transfer_id = :t"),
        {"t": transfer_id},
    ).scalar()


def _copy_under(entry_id, row_id):
    """``INSERT`` a copy of movement *entry_id* under row *row_id*, in raw SQL."""
    _db.session.execute(
        text(
            f"INSERT INTO budget.transaction_entries (transaction_id, {_COPIED}) "
            f"SELECT :row, {_COPIED} FROM budget.transaction_entries "
            "WHERE id = :entry"
        ),
        {"row": row_id, "entry": entry_id},
    )


def _re_point(entry_id, row_id):
    """``UPDATE`` movement *entry_id* to sit under row *row_id*, in raw SQL."""
    _db.session.execute(
        text("UPDATE budget.transaction_entries SET transaction_id = :row "
             "WHERE id = :entry"),
        {"row": row_id, "entry": entry_id},
    )


def _held_by(row_id):
    """How many movements row *row_id* holds, read from the database."""
    return _db.session.execute(
        text("SELECT count(*) FROM budget.transaction_entries "
             "WHERE transaction_id = :row"),
        {"row": row_id},
    ).scalar()


def _is_hidden(row_id):
    """Whether row *row_id* is soft-deleted, read from the database."""
    return _db.session.execute(
        text("SELECT is_deleted FROM budget.transactions WHERE id = :row"),
        {"row": row_id},
    ).scalar()


class TestAMovementCannotArriveUnderADeletedRow:
    """INSERT and a re-pointing UPDATE are refused under a deleted row, not a live one."""

    def test_an_insert_under_a_deleted_row_is_refused(self, app, db, seed_user):
        """The writer nobody enumerated: a raw INSERT under a hidden envelope."""
        with app.app_context():
            _row, entry = _holding(seed_user, "Home Improvement")
            hidden = a_one_off_envelope(seed_user, name="Garage Sale")
            _hide(hidden.id)
            db.session.commit()
            with refused_by_database_rule(_ARRIVAL_REFUSED) as caught:
                _copy_under(entry.id, hidden.id)
            assert f"transaction {hidden.id} " in str(caught.value)
            db.session.rollback()
            assert _held_by(hidden.id) == 0

    def test_the_same_insert_under_a_live_row_lands(self, app, db, seed_user):
        """The control: the same statement under a visible envelope."""
        with app.app_context():
            _row, entry = _holding(seed_user, "Home Improvement")
            live = a_one_off_envelope(seed_user, name="Garage Sale")
            _copy_under(entry.id, live.id)
            db.session.commit()
            assert _held_by(live.id) == 1

    def test_the_orm_write_is_refused_too(self, app, db, seed_user):
        """A door writing through the ORM meets the same rule at its flush."""
        with app.app_context():
            hidden = a_one_off_envelope(seed_user, name="Garage Sale")
            _hide(hidden.id)
            db.session.commit()
            db.session.expire_all()
            with refused_by_database_rule(_ARRIVAL_REFUSED):
                add_entry(
                    db.session, seed_user, hidden, Decimal("12.34"),
                    _day(seed_user),
                )
            db.session.rollback()
            assert _held_by(hidden.id) == 0

    def test_re_pointing_a_movement_onto_a_deleted_row_is_refused(
        self, app, db, seed_user,
    ):
        """An UPDATE that moves a movement under a hidden row is an arrival."""
        with app.app_context():
            row, entry = _holding(seed_user, "Home Improvement")
            hidden = a_one_off_envelope(seed_user, name="Garage Sale")
            _hide(hidden.id)
            db.session.commit()
            with refused_by_database_rule(_ARRIVAL_REFUSED):
                _re_point(entry.id, hidden.id)
            db.session.rollback()
            assert (_held_by(row.id), _held_by(hidden.id)) == (1, 0)

    def test_re_pointing_onto_a_live_row_lands(self, app, db, seed_user):
        """The control: the same UPDATE onto a visible envelope."""
        with app.app_context():
            row, entry = _holding(seed_user, "Home Improvement")
            live = a_one_off_envelope(seed_user, name="Garage Sale")
            _re_point(entry.id, live.id)
            db.session.commit()
            assert (_held_by(row.id), _held_by(live.id)) == (0, 1)


class TestARowCannotBeHiddenHoldingMoney:
    """Hiding a non-transfer row that holds a movement is refused when the change is saved."""

    def test_hiding_a_row_holding_a_purchase_is_refused_at_commit(
        self, app, db, seed_user,
    ):
        """The statement passes; the COMMIT refuses, and nothing is hidden."""
        with app.app_context():
            row, _entry = _holding(seed_user, "Home Improvement")
            _hide(row.id)
            with refused_by_database_rule(_HIDING_REFUSED) as caught:
                db.session.commit()
            assert f"transaction {row.id} " in str(caught.value)
            db.session.rollback()
            assert (_is_hidden(row.id), _held_by(row.id)) == (False, 1)

    def test_hiding_a_row_holding_its_payment_record_is_refused_at_commit(
        self, app, db, seed_user,
    ):
        """The PAYMENT half: a Paid $40.00 row's covering record, not a purchase.

        The account door's archive arm for a hidden row holding a payment or
        purchase went at plan step ``balance:X-bi-6-4d-2`` on this refusal, and
        the case above holds a purchase only.
        """
        with app.app_context():
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("40.00"), settled_on=_day(seed_user),
            )
            db.session.commit()
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=row.id, covers_settlement=True,
            ).count() == 1
            _hide(row.id)
            with refused_by_database_rule(_HIDING_REFUSED) as caught:
                db.session.commit()
            assert f"transaction {row.id} " in str(caught.value)
            db.session.rollback()
            assert (_is_hidden(row.id), _held_by(row.id)) == (False, 1)

    def test_an_empty_row_hides(self, app, db, seed_user):
        """The control: the same UPDATE on a row holding nothing commits."""
        with app.app_context():
            empty = a_one_off_envelope(seed_user, name="Garage Sale")
            _hide(empty.id)
            db.session.commit()
            assert _is_hidden(empty.id) is True

    def test_the_money_off_and_the_row_hidden_in_one_save_commits(
        self, app, db, seed_user,
    ):
        """The money off and the row hidden in one save, written row FIRST, commits.

        The order an immediate check would refuse (measured: made immediate,
        this test fails while the delete door below still passes, because a
        read inside that door autoflushes its ``DELETE`` first).  Checked at
        COMMIT, the row's ``UPDATE`` meeting the movement still there is not a
        refusal: by the commit it holds nothing.
        """
        with app.app_context():
            row, entry = _holding(seed_user, "Home Improvement")
            _hide(row.id)
            db.session.execute(
                text("DELETE FROM budget.transaction_entries WHERE id = :e"),
                {"e": entry.id},
            )
            db.session.commit()
            assert (_is_hidden(row.id), _held_by(row.id)) == (True, 0)

    def test_the_delete_door_still_hides_a_holding_occurrence(
        self, app, db, seed_user,
    ):
        """End to end: a recurring $300 Groceries occurrence holding a $40 purchase, deleted.

        ``transaction_service.delete_transaction`` takes the purchase off
        through the one removal act and hides the row in one save (ruling
        **R-CC75**); the hiding arm lets it commit.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="300.00", name="Groceries",
                category_key="Groceries", is_envelope=True,
            )
            row = generate_row_of(template, seed_user["bootstrap_period"])
            add_entry(
                db.session, seed_user, row, Decimal("40.00"), _day(seed_user),
            )
            db.session.commit()
            assert row.recurs is True

            outcome = transaction_service.delete_transaction(
                row, seed_user["user"].id,
                # The purchases the dialog names, as the card posts them back
                # (plan step credit_card:CC-5-4a-5, ruling R-CC131).
                purchases_named=(
                    transaction_service.preview_deletion(row).purchase_ids
                ),
            )
            db.session.commit()

            assert outcome.soft is True
            assert (_is_hidden(row.id), _held_by(row.id)) == (True, 0)

    def test_hiding_a_transfer_holding_its_payment_records_is_refused_at_commit(
        self, app, db, seed_user,
    ):
        """BAL-532's state is REFUSED at commit since plan step ``balance:X-bi-6-4d-2``.

        It committed until then (ruling **R-CC92**'s exception for a
        transfer's half).  The payments hang off the transfer since that step,
        and the TRANSFER arm refuses hiding one that holds a record (ruling
        **R-BAL167** class 2: a plant the new database refuses).
        """
        with app.app_context():
            transfer_id, _record_id = _hiding_a_paid_transfer(seed_user)
            with refused_by_database_rule(_TRANSFER_HIDING_REFUSED) as caught:
                db.session.commit()
            assert f"transfer {transfer_id} " in str(caught.value)
            db.session.rollback()
            assert _held_by_transfer(transfer_id) == 2

    # ``test_stripping_a_hidden_leg_of_its_transfer_is_refused`` stood here
    # until plan step balance:X-bi-6-4d-2 (ruling **R-BAL300**, developer
    # 2026-10-15: "Delete all 27").  It re-parented both of a paid transfer's
    # side records, each under its own side's TWIN, hid the expense twin, and
    # asserted the row arm's refusal at commit: the arm excepts no twin since
    # plan step balance:X-bi-6-4d-2 (design D7).  No twin can exist now: no
    # transaction has a transfer column since plan step balance:X-bi-6-5, and
    # ``ck_transactions_names_no_transfer`` refused a row naming one until
    # then (ruling R-BAL258, pinned once, below that drop, by
    # ``TestTheUpgrade::test_the_database_refuses_a_twin`` in
    # ``tests/test_models/test_a_transfer_is_one_row/test_the_upgrade.py``),
    # so no row can carry the carve-out's ``transfer_id``; hiding a row that
    # holds money is
    # :meth:`test_hiding_a_row_holding_a_purchase_is_refused_at_commit`, and a
    # hidden transfer holding one
    # :meth:`test_hiding_a_transfer_holding_its_payment_records_is_refused_at_commit`.


class TestWhatIsNeitherPasses:
    """A movement a hidden transfer holds can be written in place and taken off.

    BAL-532's state, staged the way the transfer's soft delete left it -- and
    since plan step ``balance:X-bi-6-4d-2`` staged INSIDE the save that hides
    it, the one place it can exist (the hiding rule is checked at commit;
    ruling **R-BAL167** class 2).  The delete door takes the payments off in
    that save, and neither arm may stand in its way.
    """

    def test_an_in_place_write_and_a_same_row_save_pass(
        self, app, db, seed_user,
    ):
        """Neither moves a movement anywhere, so neither is refused."""
        with app.app_context():
            _transfer_id, entry_id = _hiding_a_paid_transfer(seed_user)
            db.session.execute(
                text("UPDATE budget.transaction_entries "
                     "SET description = 'renamed', "
                     "expense_transfer_id = expense_transfer_id "
                     "WHERE id = :entry"),
                {"entry": entry_id},
            )
            assert db.session.execute(
                text("SELECT description FROM budget.transaction_entries "
                     "WHERE id = :entry"),
                {"entry": entry_id},
            ).scalar() == "renamed"
            # The save itself is the hiding rule's to refuse (the payments
            # are still on); the arrival arm let the write through.
            db.session.rollback()

    def test_taking_it_off_passes(self, app, db, seed_user):
        """A DELETE is a departure, never an arrival."""
        with app.app_context():
            transfer_id, _entry_id = _hiding_a_paid_transfer(seed_user)
            db.session.execute(
                text("DELETE FROM budget.transaction_entries "
                     "WHERE expense_transfer_id = :t "
                     "OR income_transfer_id = :t"),
                {"t": transfer_id},
            )
            # A settled transfer holding no record is a close of nothing,
            # which states ``0.00`` and who typed it on the transfer (the
            # band's figure arm, checked at the same commit).
            db.session.execute(
                text("UPDATE budget.transfers SET settled_figure = 0.00, "
                     "settled_figure_source_id = (SELECT id FROM "
                     "ref.movement_figure_sources WHERE name = 'typed') "
                     "WHERE id = :t"),
                {"t": transfer_id},
            )
            db.session.commit()
            assert _held_by_transfer(transfer_id) == 0


class TestTheRuleIsInstalledAtHead:
    """The suite's template is built at head, so both attachments are there."""

    def test_each_trigger_is_attached_once(self, app, db):
        """``DELETED_ROW_TRIGGERS`` names every attachment the database carries."""
        with app.app_context():
            found = db.session.execute(
                text("SELECT tgname, tgrelid::regclass::text FROM pg_trigger "
                     "WHERE tgname = ANY(:names) AND NOT tgisinternal "
                     "ORDER BY tgname"),
                {"names": [name for name, _table in DELETED_ROW_TRIGGERS]},
            ).all()
            assert [tuple(row) for row in found] == sorted(DELETED_ROW_TRIGGERS)
