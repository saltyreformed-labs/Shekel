"""
Shekel Budget App -- migration ``0703995c8536``: a card's terms are dated

Plan#2331, developer ruling **plan#2333**: ``budget.credit_card_params`` gains
``effective_date`` and its one-row-per-card key becomes one row per card per
date.  What is pinned, each on this test's own clone:

* the UPGRADE dates an existing row by the day it was first saved, read in
  the owner's display timezone -- a row saved at 03:30 UTC on Mar 1 was saved
  on Feb 28 in New York (EST, UTC-5), and is dated Feb 28 -- and leaves the
  new key in place (a second date stores, a second row on one date does not);
* the DOWNGRADE, while every card holds one version, restores the
  one-row-per-card key and drops the column;
* the downgrade REFUSES while a card holds two versions, names that card, and
  changes nothing -- which version a card keeps is the owner's to choose.
"""

from datetime import date

import pytest
import sqlalchemy
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from tests._test_helpers import (
    create_account_of_type,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module("0703995c8536_a_cards_terms_are_dated.py")

#: A raw INSERT of one terms row in the PRE-dating shape, with its
#: ``created_at`` stated so the backfill's timezone reading is graded.
_INSERT_UNDATED = (
    "INSERT INTO budget.credit_card_params "
    "(statement_close_day, payment_due_day, min_payment_percent, "
    " min_payment_floor, account_id, user_id, created_at) "
    "VALUES (20, 15, 0.02, 25.00, :account_id, :user_id, :created_at)"
)

#: A raw INSERT of one terms row in the DATED shape.
_INSERT_DATED = (
    "INSERT INTO budget.credit_card_params "
    "(effective_date, statement_close_day, payment_due_day, "
    " min_payment_percent, min_payment_floor, account_id, user_id) "
    "VALUES (:effective_date, 20, 15, 0.02, 25.00, :account_id, :user_id)"
)


def _card(seed_user, name="Visa"):
    """A Credit Card account of *seed_user*'s, committed, with no terms."""
    account = create_account_of_type(seed_user, db.session, "Credit Card", name)
    db.session.commit()
    return account


def _has_column():
    """Whether ``budget.credit_card_params.effective_date`` exists."""
    return db.session.execute(sqlalchemy.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'credit_card_params' "
        "AND column_name = 'effective_date')"
    )).scalar()


def _constraints():
    """The table's UNIQUE constraint names."""
    return set(db.session.execute(sqlalchemy.text(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'budget.credit_card_params'::regclass "
        "AND contype = 'u'"
    )).scalars())


def _insert_dated(account, effective_date):
    """Store one dated version for *account* by raw SQL and flush it."""
    db.session.execute(sqlalchemy.text(_INSERT_DATED), {
        "effective_date": effective_date,
        "account_id": account.id, "user_id": account.user_id,
    })


class TestTheUpgradeDatesEachRow:
    """Down to the undated shape, plant a row, up again."""

    def test_a_row_is_dated_the_day_it_was_saved_in_new_york(
        self, app, seed_user,
    ):
        """03:30 UTC on Mar 1 is 22:30 on Feb 28 in New York: dated Feb 28.

        The instant is chosen to straddle midnight between the two clocks, so
        a backfill reading ``created_at`` in UTC (Mar 1) fails this case.
        """
        with app.app_context():
            account = _card(seed_user)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert _has_column() is False
            db.session.execute(sqlalchemy.text(_INSERT_UNDATED), {
                "account_id": account.id, "user_id": account.user_id,
                "created_at": "2026-03-01 03:30:00+00",
            })

            run_migration_callable(_MIGRATION.upgrade, db.session)

            stored = db.session.execute(sqlalchemy.text(
                "SELECT effective_date FROM budget.credit_card_params "
                "WHERE account_id = :account_id"
            ), {"account_id": account.id}).scalar()
            assert stored == date(2026, 2, 28)

    def test_the_upgrade_keys_one_row_per_card_per_date(self, app, seed_user):
        """After the upgrade a second date stores and a second row on one date does not."""
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, db.session)
            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert _constraints() >= {"uq_credit_card_params_account_effective_date"}
            assert "credit_card_params_account_id_key" not in _constraints()

            account = _card(seed_user)
            _insert_dated(account, date(2026, 1, 1))
            _insert_dated(account, date(2026, 3, 1))
            db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                _insert_dated(account, date(2026, 3, 1))
            db.session.rollback()
            assert "uq_credit_card_params_account_effective_date" in str(
                excinfo.value,
            )


class TestTheDowngrade:
    """It restores one row per card only while that is what the cards hold."""

    def test_one_version_per_card_restores_the_old_key(self, app, seed_user):
        """One version: the column goes and ``account_id`` is UNIQUE again."""
        with app.app_context():
            account = _card(seed_user)
            _insert_dated(account, date(2026, 1, 1))
            db.session.commit()

            run_migration_callable(_MIGRATION.downgrade, db.session)

            assert _has_column() is False
            assert "credit_card_params_account_id_key" in _constraints()
            assert "uq_credit_card_params_account_effective_date" not in _constraints()
            assert db.session.execute(sqlalchemy.text(
                "SELECT COUNT(*) FROM budget.credit_card_params "
                "WHERE account_id = :account_id"
            ), {"account_id": account.id}).scalar() == 1
            run_migration_callable(_MIGRATION.upgrade, db.session)

    def test_two_versions_refuse_naming_the_card_and_change_nothing(
        self, app, seed_user,
    ):
        """A card with two versions: refused, named, and the table as it was."""
        with app.app_context():
            account = _card(seed_user)
            _insert_dated(account, date(2026, 1, 1))
            _insert_dated(account, date(2026, 3, 1))
            db.session.commit()

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.rollback()

            assert f"account {account.id} (2 versions)" in str(excinfo.value)
            assert _has_column() is True
            assert "uq_credit_card_params_account_effective_date" in _constraints()
            assert db.session.execute(sqlalchemy.text(
                "SELECT COUNT(*) FROM budget.credit_card_params "
                "WHERE account_id = :account_id"
            ), {"account_id": account.id}).scalar() == 2
