"""
Shekel Budget App -- migration ``c956c1d83108``: a transaction names no transfer.

Plan step ``balance:X-bi-6-5``'s first leaf, card plan#1844 (rulings
**R-BAL86**, plan#1843).  The revision drops ``budget.transactions.transfer_id``
with its key, its two indexes and ``ck_transactions_names_no_transfer``,
re-cuts ``ck_transactions_one_pricing_link`` to two terms, deletes the
``parent_transfer`` amount source, refusing while a stored row declares it,
and drops ``82f7b7ed4332``'s twin copy (ruling plan#1851).
The cases read the schema at head, drive the downgrade and the upgrade, and
grade what the catalog and the reference table hold after each.
"""

from decimal import Decimal

import contextlib
import io

import pytest
from sqlalchemy import exc, text

from app.extensions import db as _db
from tests._test_helpers import (
    add_txn,
    constraint_name_from,
    create_savings_account,
    generate_transfer_of,
    load_migration_module,
    make_transfer_template,
    run_migration_callable,
)

_MIGRATION = load_migration_module("c956c1d83108_a_transaction_names_no_transfer.py")

#: The revision below, whose upgrade keeps the copy this one drops.
_TWINS_DELETED = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")

#: The pricing CHECK as PostgreSQL renders each form of it.
_TWO_TERMS = (
    "CHECK (((((template_id IS NOT NULL))::integer + "
    "((credit_payback_for_id IS NOT NULL))::integer) = 1))"
)
_THREE_TERMS = (
    "CHECK ((((((template_id IS NOT NULL))::integer + "
    "((transfer_id IS NOT NULL))::integer) + "
    "((credit_payback_for_id IS NOT NULL))::integer) = 1))"
)

#: What a database below the revision holds, as rendered at ``b7d2e4f19a63``
#: on the test image (2026-10-27): the column's key, its two indexes and its
#: refusal, beside the three-term CHECK.
_BELOW = {
    "column": ["transfer_id"],
    "constraints": {
        "ck_transactions_names_no_transfer": "CHECK ((transfer_id IS NULL))",
        "ck_transactions_one_pricing_link": _THREE_TERMS,
        "fk_transactions_transfer_id": (
            "FOREIGN KEY (transfer_id) REFERENCES budget.transfers(id) "
            "ON DELETE CASCADE"
        ),
    },
    "indexes": {
        "idx_transactions_transfer": (
            "CREATE INDEX idx_transactions_transfer ON budget.transactions "
            "USING btree (transfer_id) WHERE (transfer_id IS NOT NULL)"
        ),
        "uq_transactions_transfer_type_active": (
            "CREATE UNIQUE INDEX uq_transactions_transfer_type_active ON "
            "budget.transactions USING btree (transfer_id, transaction_type_id) "
            "WHERE ((transfer_id IS NOT NULL) AND (is_deleted = false))"
        ),
    },
    "amount_sources": ["parent_transfer", "template"],
    "twin_copy": False,
}

#: What head holds: none of them, and the two-term CHECK.
_HEAD = {
    "column": [],
    "constraints": {"ck_transactions_one_pricing_link": _TWO_TERMS},
    "indexes": {},
    "amount_sources": ["template"],
    "twin_copy": False,
}


def _schema():
    """Return what the revision changes, read from the catalog, as one value.

    The column, every CHECK or foreign key on ``budget.transactions`` naming
    it or the pricing rule (a key into ``budget.transfers`` by any name, so a
    ``create_all`` database's unnamed one is seen too), every index over it,
    the ``ref.amount_sources`` names, and whether the twin copy exists.  The
    copy is absent on BOTH sides: the upgrade drops it and the downgrade does
    not re-create it (its rows went with it).
    """
    column = _db.session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'transactions' "
        "AND column_name = 'transfer_id'"
    )).scalars().all()
    constraints = dict(_db.session.execute(text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'budget.transactions'::regclass "
        "AND (conname = 'ck_transactions_one_pricing_link' "
        "     OR pg_get_constraintdef(oid) ~ '\\mtransfer_id\\M' "
        "     OR confrelid = 'budget.transfers'::regclass)"
    )).all())
    indexes = dict(_db.session.execute(text(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'budget' AND tablename = 'transactions' "
        "AND indexdef ~ '\\mtransfer_id\\M'"
    )).all())
    sources = _db.session.execute(text(
        "SELECT name FROM ref.amount_sources ORDER BY name"
    )).scalars().all()
    twin_copy = _db.session.execute(text(
        "SELECT to_regclass('system.transfer_twin_purge') IS NOT NULL"
    )).scalar_one()
    return {
        "column": column,
        "constraints": constraints,
        "indexes": indexes,
        "amount_sources": sources,
        "twin_copy": twin_copy,
    }


def _declare_parent_transfer(table, row_id):
    """Declare *row_id* of *table* priced by ``parent_transfer``, below the revision.

    The pair moves together, as ``ck_*_amount_ownership`` requires: a derived
    row stores no figure.
    """
    figure = "estimated_amount" if table == "transactions" else "amount"
    _db.session.execute(
        text(
            f"UPDATE budget.{table} SET {figure} = NULL, amount_source_id = "
            "(SELECT id FROM ref.amount_sources WHERE name = 'parent_transfer') "
            "WHERE id = :id"
        ),
        {"id": row_id},
    )
    _db.session.commit()


def _generated_transfer(seed_user, seed_periods):
    """Return the engine's transfer of a savings definition: it declares ``template``."""
    savings = create_savings_account(
        seed_user, _db.session, "Savings", Decimal("0.00"),
    )
    template = make_transfer_template(_db.session, seed_user, savings)
    _db.session.commit()
    xfer = generate_transfer_of(template, seed_periods[0])
    _db.session.commit()
    return xfer


class TestTheRevision:
    """Where the revision sits in the chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration onto 4d's head."""
        assert _MIGRATION.revision == "c956c1d83108"
        assert _MIGRATION.down_revision == "b7d2e4f19a63"


class TestTheSchemaEachWay:
    """Head holds none of the column's objects; the downgrade restores each."""

    def test_head_holds_no_transfer_column(self, app, db):  # pylint: disable=unused-argument
        """The test template is built at head: no column, no key, two terms, one source.

        Pylint: ``unused-argument`` -- ``db`` is requested for the test's own
        database the catalog is read from.
        """
        with app.app_context():
            assert _schema() == _HEAD

    def test_the_downgrade_restores_each_piece_and_the_upgrade_drops_it(
        self, app, db,  # pylint: disable=unused-argument
    ):
        """Down restores what ``b7d2e4f19a63`` held, by name and definition; up is head again.

        The key comes back as ``fk_transactions_transfer_id`` with ``ON DELETE
        CASCADE``, the name ``772043eee094``'s downgrade drops, and the
        reference row by name.

        Pylint: ``unused-argument`` -- ``db`` is requested for the test's own
        database the migration runs on.
        """
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, _db.session)
            assert _schema() == _BELOW
            run_migration_callable(_MIGRATION.upgrade, _db.session)
            assert _schema() == _HEAD


class TestTheReCutCheckStillRefuses:
    """``= 1`` over two terms: a row with no link and a row with both are refused."""

    @pytest.mark.parametrize(
        "assignment",
        ["template_id = NULL", "credit_payback_for_id = id"],
        ids=["no-link", "two-links"],
    )
    def test_a_row_not_priced_through_exactly_one_relation(
        self, app, seed_user, seed_periods, assignment,
    ):
        """A one-off's row names its definition; stripping it or adding a payback link is refused.

        The control for the re-cut: ``DROP COLUMN`` alone would have dropped
        the whole CHECK, and both statements would then be stored.
        """
        with app.app_context():
            txn = add_txn(
                _db.session, seed_user, seed_periods[0], "Haircut", "35.00",
            )
            _db.session.commit()
            with pytest.raises(exc.IntegrityError) as raised:
                _db.session.execute(
                    text(f"UPDATE budget.transactions SET {assignment} WHERE id = :id"),
                    {"id": txn.id},
                )
            _db.session.rollback()
            assert constraint_name_from(raised.value) == (
                "ck_transactions_one_pricing_link"
            )


class TestTheUpgradeRefuses:
    """The upgrade writes nothing while a stored row declares a parent transfer."""

    @staticmethod
    def _refused(*matches):
        """Drive the upgrade, assert each of *matches* in its refusal, then grade nothing moved."""
        with pytest.raises(RuntimeError) as raised:
            run_migration_callable(_MIGRATION.upgrade, _db.session)
        _db.session.rollback()
        for match in matches:
            assert match in str(raised.value), str(raised.value)
        assert _schema() == _BELOW

    def test_a_transaction_declaring_it(self, app, seed_user, seed_periods):
        """A definition's row declaring ``parent_transfer`` passes every CHECK: named by table and id."""
        with app.app_context():
            txn = add_txn(
                _db.session, seed_user, seed_periods[0], "Haircut", "35.00",
            )
            _db.session.commit()
            run_migration_callable(_MIGRATION.downgrade, _db.session)
            _declare_parent_transfer("transactions", txn.id)
            self._refused(f"('budget.transactions', 1, [{txn.id}])")

    def test_a_transfer_declaring_it(self, app, seed_user, seed_periods):
        """A generated transfer re-declared ``parent_transfer`` is named by its table too."""
        with app.app_context():
            xfer = _generated_transfer(seed_user, seed_periods)
            run_migration_callable(_MIGRATION.downgrade, _db.session)
            _declare_parent_transfer("transfers", xfer.id)
            self._refused(f"('budget.transfers', 1, [{xfer.id}])")

    def test_both_tables_are_named_at_once(self, app, seed_user, seed_periods):
        """One of each: the refusal names both, so one repair pass sees everything."""
        with app.app_context():
            txn = add_txn(
                _db.session, seed_user, seed_periods[0], "Haircut", "35.00",
            )
            xfer = _generated_transfer(seed_user, seed_periods)
            run_migration_callable(_MIGRATION.downgrade, _db.session)
            _declare_parent_transfer("transactions", txn.id)
            _declare_parent_transfer("transfers", xfer.id)
            self._refused(
                f"('budget.transactions', 1, [{txn.id}])",
                f"('budget.transfers', 1, [{xfer.id}])",
            )

    def test_a_row_declaring_its_definition_passes(
        self, app, seed_user, seed_periods,
    ):
        """CONTROL half: the engine's transfer declares ``template`` and the upgrade runs."""
        with app.app_context():
            _generated_transfer(seed_user, seed_periods)
            run_migration_callable(_MIGRATION.downgrade, _db.session)
            run_migration_callable(_MIGRATION.upgrade, _db.session)
            assert _schema() == _HEAD


class TestTheTwinCopyIsDropped:
    """Ruling plan#1851: the upgrade drops ``82f7b7ed4332``'s copy, kept twins and all."""

    def test_a_copy_holding_kept_twins_is_dropped(
        self, app, seed_user, seed_periods,
    ):
        """Two twins kept by the revision below are dropped with the copy, and counted.

        The engine's transfer goes down through both revisions -- the one below
        builds its two twins -- and back up through the one below, which keeps
        them in the copy and deletes them; this upgrade then drops the copy
        and says how many twins went with it.
        """
        with app.app_context():
            _generated_transfer(seed_user, seed_periods)
            run_migration_callable(_MIGRATION.downgrade, _db.session)
            run_migration_callable(_TWINS_DELETED.downgrade, _db.session)
            run_migration_callable(_TWINS_DELETED.upgrade, _db.session)
            kept = _db.session.execute(text(
                "SELECT count(*) FROM system.transfer_twin_purge"
            )).scalar_one()
            assert kept == 2
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                run_migration_callable(_MIGRATION.upgrade, _db.session)
            assert _schema() == _HEAD
            assert (
                "system.transfer_twin_purge dropped with its 2 kept twin(s)"
                in printed.getvalue()
            )

    def test_a_database_holding_no_copy_upgrades(self, app, db):  # pylint: disable=unused-argument
        """A database with no copy (``create_all``'s, or this downgrade's) upgrades and says so.

        Pylint: ``unused-argument`` -- ``db`` is requested for the test's own
        database the migration runs on.
        """
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, _db.session)
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                run_migration_callable(_MIGRATION.upgrade, _db.session)
            assert _schema() == _HEAD
            assert "no copy to drop" in printed.getvalue()
