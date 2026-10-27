"""Plan step ``credit_card:CC-5-4a-4``: a row's delete never takes its movements.

Rulings **R-CC54** parts (2) and (3) and **R-CC64** (developer 2026-09-22):
the database stops cascading a row's delete to its payments and purchases, a
match's key to its payment or purchase stops cascading like its key to the
bank line, and the object layer stops cascading too -- "No door at any layer,
now or written later, can delete a row still holding a payment or purchase:
it errors where tests see it."

Three things are graded here, each against the SHIPPED schema or code:

* **the keys refuse** -- a bulk ``DELETE`` of a row holding a purchase (the
  shape the template, account and pay-period doors used), an ORM
  ``session.delete`` of one (the object layer's copy of the cascade, which
  flipping the database key alone would have left open), and a ``DELETE`` of
  a movement a match names;
* **the one act still removes a movement** -- explicitly, the row staying;
* **migration ``c4a4e7d1b9f2``** -- driven through its shipped ``upgrade`` /
  ``downgrade`` (Definition of Done item 7, the shape
  ``test_cc5_4a2_member_rekey`` uses): the three keys' exact definitions
  each way, the refusal on an act already naming no movement, which is
  the one state the deleted leftover-match check existed for, and the
  deleted-row triggers (ruling **R-CC89**) going and coming back with it.

The purchase is staged the way finding **CC-363**'s P5 measured it: a one-off
envelope 'Home Improvement' holding a $25.00 purchase recorded from the bank's
line through the real create door, which also records the match.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db as _db
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import match_press, match_withdrawal, movement_removal
from tests._test_helpers import (
    add_entry,
    create_account_of_type,
    create_settled_transfer,
    load_migration_module,
    run_migration_callable as _run,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does (the convention ``test_cc5_4a2_member_rekey`` keeps).
# pylint: disable=shekel-private-module-import
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_one_off_envelope,
    an_import,
    filed_by,
)

#: This step's own revision, loaded so its SHIPPED callables are what runs.
_M = load_migration_module(
    "c4a4e7d1b9f2_a_row_s_delete_never_takes_its_movements.py",
)

#: Plan step ``balance:X-bi-6-4d-2``'s revision, which deletes every transfer
#: shadow and refuses a new one; its downgrade builds a transfer's two, which
#: the hidden-leg case below needs.
_TWINS_DELETED = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")

#: Plan step ``balance:X-bi-6-4d-2``'s last revision, which empties a side's
#: record of the figure its transfer holds; its downgrade, run before
#: :data:`_TWINS_DELETED`'s, copies the figure back onto the records and
#: withdraws the band's figure arm, the shape every revision below holds.
_SIDE_RECORD_FIGURE = load_migration_module(
    "b7d2e4f19a63_a_side_record_holds_no_figure.py",
)

#: The three keys this step flips, as ``pg_get_constraintdef`` prints them at
#: head -- no ``ON DELETE`` clause is NO ACTION.
_AT_HEAD = {
    "fk_transaction_entries_transaction_id": (
        "FOREIGN KEY (transaction_id) REFERENCES budget.transactions(id)"
    ),
    "fk_transaction_entries_owner_transaction": (
        "FOREIGN KEY (transaction_id, owner_id) REFERENCES "
        "budget.transactions(id, user_id)"
    ),
    "fk_statement_match_members_entry_account": (
        "FOREIGN KEY (transaction_entry_id, account_id) REFERENCES "
        "budget.transaction_entries(id, account_id)"
    ),
}

#: ...and as the downgrade restores them: ``c4a4e7d1b9f2``'s parent, read off
#: the 2026-09-22 17:06 production dump with ``pg_get_constraintdef`` (the
#: single-column key under Postgres' default name).
_BEFORE = {
    "transaction_entries_transaction_id_fkey": (
        "FOREIGN KEY (transaction_id) REFERENCES budget.transactions(id) "
        "ON DELETE CASCADE"
    ),
    "fk_transaction_entries_owner_transaction": (
        "FOREIGN KEY (transaction_id, owner_id) REFERENCES "
        "budget.transactions(id, user_id) ON DELETE CASCADE"
    ),
    "fk_statement_match_members_entry_account": (
        "FOREIGN KEY (transaction_entry_id, account_id) REFERENCES "
        "budget.transaction_entries(id, account_id) ON DELETE CASCADE"
    ),
}

_NAMES = tuple(set(_AT_HEAD) | set(_BEFORE))


def _keys(session):
    """Return ``{name: definition}`` for whichever of the keys exist now."""
    rows = session.execute(
        text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            " WHERE conname = ANY(:names)"
        ),
        {"names": list(_NAMES)},
    ).all()
    return dict(rows)


#: The ROW arm's two attachments, the ones this revision installs.  Named
#: rather than read off ``DELETED_ROW_TRIGGERS``, which lists HEAD's set and
#: grew a third, the transfer arm's, at plan step ``balance:X-bi-6-4d-2``.
_ROW_ARM_TRIGGERS = ("ck_movement_row_not_deleted", "ck_hidden_row_holds_nothing")


def _deleted_row_rule(session):
    """Return ``(triggers, functions)`` of ruling R-CC89's two arms present now."""
    return (
        session.execute(
            text("SELECT count(*) FROM pg_trigger "
                 "WHERE tgname = ANY(:names) AND NOT tgisinternal"),
            {"names": list(_ROW_ARM_TRIGGERS)},
        ).scalar(),
        session.execute(
            text("SELECT count(*) FROM pg_proc p "
                 "JOIN pg_namespace n ON n.oid = p.pronamespace "
                 "WHERE n.nspname = 'budget' AND p.proname IN ("
                 "'refuse_movement_under_deleted_row', "
                 "'refuse_hiding_a_row_holding_money')"),
        ).scalar(),
    )


def _day(seed_user):
    """A bank day inside the bootstrap period, after the books opened."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _home_improvement(seed_user):
    """P5's envelope holding a $25.00 purchase recorded from the bank's line.

    Returns:
        ``(row, created)`` -- the envelope and the create door's
        :class:`~app.services.statement_match.CreatedPurchase` (the purchase
        and the act that matches it to the line).
    """
    row = a_one_off_envelope(seed_user)
    line = a_bank_line(
        seed_user, an_import(seed_user), amount="-25.00",
        posted_on=_day(seed_user), description="HOME DEPOT",
    )
    _db.session.commit()
    created = filed_by(seed_user, line, row, by_rule=False)
    _db.session.commit()
    return row, created


def _unmatched_purchase(seed_user):
    """A one-off envelope holding a $25.00 purchase no match names."""
    row = a_one_off_envelope(seed_user)
    add_entry(
        _db.session, seed_user, row, Decimal("25.00"), _day(seed_user),
    )
    _db.session.commit()
    return row


class TestTheKeysRefuse:
    """No statement can delete a row holding a movement, or a named movement."""

    def test_a_bulk_delete_of_a_row_holding_a_purchase_is_refused(
        self, app, db, seed_user,
    ):
        """The template / account / pay-period doors' shape: one bulk DELETE.

        It cascaded the purchase away until this step (finding **CC-363**,
        P5: Checking read $25.00 above the bank).
        """
        with app.app_context():
            row = _unmatched_purchase(seed_user)
            with pytest.raises(IntegrityError) as caught:
                db.session.query(Transaction).filter_by(id=row.id).delete(
                    synchronize_session=False,
                )
                db.session.flush()
            db.session.rollback()
            assert "fk_transaction_entries_" in str(caught.value)
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=row.id,
            ).count() == 1

    def test_an_orm_delete_of_a_row_holding_a_purchase_is_refused(
        self, app, db, seed_user,
    ):
        """The object layer's copy of the cascade is gone too (ruling R-CC64).

        ``cascade="all, delete-orphan"`` on ``Transaction.entries`` had the
        unit of work DELETE the purchase before the row, so the database never
        saw a row holding one; a later door deleting such a row directly
        would have erased its purchase silently.
        """
        with app.app_context():
            row = _unmatched_purchase(seed_user)
            with pytest.raises(IntegrityError) as caught:
                db.session.delete(row)
                db.session.flush()
            db.session.rollback()
            assert "fk_transaction_entries_" in str(caught.value)
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=row.id,
            ).count() == 1

    def test_a_movement_a_match_names_cannot_be_deleted(
        self, app, db, seed_user,
    ):
        """The member's movement key is NO ACTION, like its line key (R-CC54 (3))."""
        with app.app_context():
            _row, created = _home_improvement(seed_user)
            with pytest.raises(IntegrityError) as caught:
                db.session.query(TransactionEntry).filter_by(
                    id=created.entry_id,
                ).delete(synchronize_session=False)
                db.session.flush()
            db.session.rollback()
            assert "fk_statement_match_members_entry_account" in str(
                caught.value,
            )
            assert db.session.query(StatementMatchMember).filter_by(
                transaction_entry_id=created.entry_id,
            ).count() == 1


class TestTheOneActStillRemovesAMovement:
    """``movement_removal`` deletes each movement itself; its row stays."""

    def test_an_unmatched_purchase_goes_and_its_row_stays(
        self, app, db, seed_user,
    ):
        """No ``delete-orphan`` any more: the act's explicit DELETE is the path.

        Removed from the collection BEFORE it was deleted, the flush would
        NULL ``transaction_id`` and ``ck_transaction_entries_one_parent``
        would refuse it (``NOT NULL`` did until plan step
        ``balance:X-bi-6-4d-2``).
        """
        with app.app_context():
            row = _unmatched_purchase(seed_user)
            (purchase,) = row.entries
            purchase_id = purchase.id
            movement_removal.remove_movements(
                [purchase], seed_user["user"].id,
                because=match_withdrawal.LEFT_THE_BOOKS,
                press=None,
            )
            assert row.entries == []
            db.session.commit()
            assert db.session.get(TransactionEntry, purchase_id) is None
            assert db.session.get(Transaction, row.id) is not None

    def test_a_matched_purchase_goes_with_its_act(self, app, db, seed_user):
        """Out of the match FIRST, so the NO ACTION member key never meets it."""
        with app.app_context():
            row, created = _home_improvement(seed_user)
            purchase = db.session.get(TransactionEntry, created.entry_id)
            with match_press.Press(match_press.Shown(
                    match_withdrawal.pending_for_movements([purchase]).line_ids,
                )) as press:
                movement_removal.remove_movements(
                    [purchase],
                    seed_user["user"].id,
                    because=match_withdrawal.LEFT_THE_BOOKS,
                    press=press,
                )
            db.session.commit()
            assert db.session.get(TransactionEntry, created.entry_id) is None
            assert db.session.get(StatementMatch, created.match_id) is None
            assert db.session.get(Transaction, row.id) is not None


@pytest.mark.usefixtures("below_the_transfer_column_drop")
class TestTheMigration:
    """``c4a4e7d1b9f2``: the three keys each way, and its two refusals.

    Every case runs below ``c956c1d83108`` (the fixture): this revision's
    upgrade installs the deleted-row rule's row-only text, which names
    ``budget.transactions.transfer_id``, and its hidden-row refusal reads it.
    That revision restores none of the three keys, so they read as head's.
    """

    def test_the_keys_are_no_action_at_head(self, app, db):
        """The test database is built at head, so the keys read as upgraded."""
        with app.app_context():
            assert _keys(db.session) == _AT_HEAD

    def test_the_downgrade_restores_every_cascade_and_the_upgrade_flips_back(
        self, app, db,
    ):
        """Both directions, exactly -- including the single-column key's name."""
        with app.app_context():
            _run(_M.downgrade, db.session)
            assert _keys(db.session) == _BEFORE
            _run(_M.upgrade, db.session)
            assert _keys(db.session) == _AT_HEAD

    def test_the_downgrade_removes_the_deleted_row_rule_and_the_upgrade_restores_it(
        self, app, db,
    ):
        """Ruling **R-CC89**'s two triggers and functions go and come back with the revision."""
        with app.app_context():
            assert _deleted_row_rule(db.session) == (2, 2)
            _run(_M.downgrade, db.session)
            assert _deleted_row_rule(db.session) == (0, 0)
            _run(_M.upgrade, db.session)
            assert _deleted_row_rule(db.session) == (2, 2)

    def test_an_act_already_naming_no_movement_refuses_the_upgrade(
        self, app, db, seed_user,
    ):
        """The stored past a cascade stranded, asked of once before the check goes.

        Staged under the downgraded schema the way the template delete made
        it before this step: the envelope's bulk ``DELETE`` cascades the
        purchase AND its member, and the act keeps its bank line alone.  The
        upgrade names the act and writes nothing -- the keys still cascade.
        """
        with app.app_context():
            row, created = _home_improvement(seed_user)
            _run(_M.downgrade, db.session)
            db.session.query(Transaction).filter_by(id=row.id).delete(
                synchronize_session=False,
            )
            db.session.commit()
            assert db.session.get(StatementMatch, created.match_id) is not None
            with pytest.raises(RuntimeError) as caught:
                _run(_M.upgrade, db.session)
            # Read in the SAME transaction, before the rollback: a refusal
            # placed after the DDL would show the flipped keys here.
            assert _keys(db.session) == _BEFORE
            db.session.rollback()
            assert f"ids [{created.match_id}]" in str(caught.value)
            assert "name no movement" in str(caught.value)

    def test_a_hidden_row_still_holding_a_purchase_refuses_the_upgrade(
        self, app, db, seed_user,
    ):
        """A row the old code hid while it held a purchase stops the upgrade.

        Ruling **R-CC82**: the state an archive or a recurring occurrence's
        delete made before this release -- the row soft-deleted, its $25.00
        purchase kept under it -- staged directly under the downgraded schema.
        The upgrade names the row and writes nothing: the keys still cascade.
        """
        with app.app_context():
            row = _unmatched_purchase(seed_user)
            _run(_M.downgrade, db.session)
            db.session.execute(
                text("UPDATE budget.transactions SET is_deleted = TRUE "
                     "WHERE id = :id"),
                {"id": row.id},
            )
            db.session.commit()
            with pytest.raises(RuntimeError) as caught:
                _run(_M.upgrade, db.session)
            # Before the rollback, as the stranded-act test reads it.
            assert _keys(db.session) == _BEFORE
            db.session.rollback()
            assert f"ids [{row.id}]" in str(caught.value)
            assert "hidden row(s) hold a recorded payment or purchase" in (
                str(caught.value)
            )

    def test_a_hidden_transfer_leg_holding_its_payment_does_not_refuse(
        self, app, db, seed_user,
    ):
        """A soft-deleted transfer's leg holding its payment is BAL-532's, not this refusal's.

        The developer's follow-up to R-CC82 ("Non-transfer rows"): the
        transfer's soft delete still made this state after the release, so
        the upgrade does not stop over it.  A settled $100.00 transfer, the
        transfer and both shadows flagged hidden with their payments in place.

        **The shadows are built and the payments put back under them first**,
        the shape this revision ran against.  No transfer has a shadow since
        plan step ``balance:X-bi-6-4d-2`` (``82f7b7ed4332`` deletes them and
        refuses a new one), so the case runs that revision's downgrade, which
        builds both, each settled like its transfer, after the two above it:
        ``c956c1d83108``'s (the class's fixture), which puts back the column a
        shadow names its transfer by, and ``b7d2e4f19a63``'s, which puts the
        transfer's figure back on each side's record.  Since plan step
        ``balance:X-bi-6-4d-2`` each side's record hangs off the TRANSFER
        (ruling **R-BAL88**), and the downgrade leaves it there, so both
        records are then re-parented under their shadows in one statement,
        which the status band allows as a ``$0.00`` close's shape (the setup
        ruling **R-BAL167** approved at that step).  What is asserted is
        unchanged.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "R-CC82 Savings",
            )
            db.session.commit()
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            db.session.commit()
            _run(_SIDE_RECORD_FIGURE.downgrade, db.session)
            _run(_TWINS_DELETED.downgrade, db.session)
            db.session.execute(
                text("UPDATE budget.transaction_entries e "
                     "SET transaction_id = t.id, expense_transfer_id = NULL, "
                     "income_transfer_id = NULL "
                     "FROM budget.transactions t "
                     "WHERE t.transfer_id = :t AND t.account_id = e.account_id "
                     "AND (e.expense_transfer_id = :t "
                     "OR e.income_transfer_id = :t)"),
                {"t": transfer.id},
            )
            db.session.commit()
            _run(_M.downgrade, db.session)
            db.session.execute(
                text("UPDATE budget.transactions SET is_deleted = TRUE "
                     "WHERE transfer_id = :t"),
                {"t": transfer.id},
            )
            db.session.execute(
                text("UPDATE budget.transfers SET is_deleted = TRUE "
                     "WHERE id = :t"),
                {"t": transfer.id},
            )
            db.session.commit()
            held = db.session.execute(
                text("SELECT count(*) FROM budget.transaction_entries e "
                     "JOIN budget.transactions t ON t.id = e.transaction_id "
                     "WHERE t.transfer_id = :t AND t.is_deleted"),
                {"t": transfer.id},
            ).scalar()
            assert held == 2
            _run(_M.upgrade, db.session)
            assert _keys(db.session) == _AT_HEAD

    def test_the_refusals_fire_only_on_what_they_name(
        self, app, db, seed_user,
    ):
        """Neither refusal fires on the states beside the ones it names (review 2, L1).

        Three controls in one upgrade, each the neighbour of a refusal's
        predicate: a LIVE act naming its purchase (the stranded-act query's
        ``NOT EXISTS``), a VISIBLE row holding a purchase (the hidden-row
        query's ``is_deleted``) and a hidden row holding NOTHING (its
        ``EXISTS``).  Dropping any one of those three clauses makes the
        upgrade refuse here.
        """
        with app.app_context():
            _home_improvement(seed_user)
            _unmatched_purchase(seed_user)
            empty = a_one_off_envelope(seed_user, name="Garage Sale")
            _db.session.commit()
            _run(_M.downgrade, db.session)
            db.session.execute(
                text("UPDATE budget.transactions SET is_deleted = TRUE "
                     "WHERE id = :id"),
                {"id": empty.id},
            )
            db.session.commit()
            _run(_M.upgrade, db.session)
            assert _keys(db.session) == _AT_HEAD
