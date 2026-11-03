"""C-18 optimistic-lock tests: every versioned table carries a NOT NULL, positive version_id the
mapper declares, a read leaves it alone and an update bumps it by one, and a bump made outside
the session raises StaleDataError.
"""

from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.amount_ownership import state_own_amount
from tests._test_helpers import one_off_row_of

from ._helpers import (
    _bump_version_outside_session,
    _read_version,
    _make_template,
    _make_transaction,
    _make_envelope_template_and_txn,
    _make_entry,
    _make_savings_account,
    _make_savings_goal,
    _make_salary_profile,
    _make_salary_raise,
    _make_paycheck_line,
    _make_transfer,
    _make_transfer_template,
)


# ═════════════════════════════════════════════════════════════════════
# Schema invariants
# ═════════════════════════════════════════════════════════════════════


# Each entry: (schema, table, model, check_constraint_name)
_VERSIONED_ROWS = [
    ("budget", "transactions", Transaction, "ck_transactions_version_id_positive"),
    ("budget", "transfers", Transfer, "ck_transfers_version_id_positive"),
    (
        "budget", "transaction_templates", TransactionTemplate,
        "ck_transaction_templates_version_id_positive",
    ),
    (
        "budget", "transfer_templates", TransferTemplate,
        "ck_transfer_templates_version_id_positive",
    ),
    ("budget", "savings_goals", SavingsGoal, "ck_savings_goals_version_id_positive"),
    (
        "budget", "transaction_entries", TransactionEntry,
        "ck_transaction_entries_version_id_positive",
    ),
    ("salary", "salary_profiles", SalaryProfile, "ck_salary_profiles_version_id_positive"),
    ("salary", "salary_raises", SalaryRaise, "ck_salary_raises_version_id_positive"),
    (
        "salary", "paycheck_lines", PaycheckLine,
        "ck_paycheck_lines_version_id_positive",
    ),
]


@pytest.mark.parametrize("schema, table, _model, _check", _VERSIONED_ROWS)
def test_version_id_column_present_and_not_null(app, schema, table, _model, _check):
    """Every versioned table carries a NOT NULL ``version_id``.

    A NULL counter would silently disable the optimistic lock on
    that row (``WHERE version_id IS NULL`` does not match the
    SQLAlchemy-emitted comparison), so this assertion is the
    minimal regression guard against a future migration drift.
    """
    with app.app_context():
        insp = inspect(db.engine)
        cols = {
            c["name"]: c
            for c in insp.get_columns(table, schema=schema)
        }

        assert "version_id" in cols, (
            f"{schema}.{table}.version_id column missing -- migration "
            f"a6c122211261 may not have run."
        )
        assert cols["version_id"]["nullable"] is False, (
            f"{schema}.{table}.version_id must be NOT NULL or the "
            f"optimistic lock silently fails on NULL counters."
        )


@pytest.mark.parametrize("schema, table, _model, check_name", _VERSIONED_ROWS)
def test_version_id_check_constraint_present(app, schema, table, _model, check_name):
    """Every versioned table has a ``version_id > 0`` CHECK.

    The CHECK is the database-tier guard against a future raw-SQL
    path that writes 0 or a negative value.  Its presence is
    asserted independently of the model declaration so a future
    edit that drops the constraint without removing it from the
    matching ``__table_args__`` block does not silently pass.
    """
    with app.app_context():
        insp = inspect(db.engine)
        checks = {
            c["name"]: c["sqltext"]
            for c in insp.get_check_constraints(table, schema=schema)
        }
        assert check_name in checks, (
            f"{check_name} missing from {schema}.{table} -- the schema "
            f"no longer matches the model declaration."
        )
        normalised = checks[check_name].lower().replace(" ", "")
        assert "version_id>0" in normalised, (
            f"CHECK constraint expression on {check_name} has changed; "
            f"rerun the migration or update the model in lockstep."
        )


@pytest.mark.parametrize("_schema, _table, model, _check", _VERSIONED_ROWS)
def test_mapper_declares_version_id_col(app, _schema, _table, model, _check):
    """Every versioned model exposes ``version_id_col`` on its mapper.

    Without this declaration SQLAlchemy emits ``UPDATE`` without
    the ``WHERE version_id = ?`` narrowing and the optimistic-lock
    contract collapses; the rest of the test class would still
    pass against the schema but production would silently regress.
    """
    with app.app_context():
        mapper = inspect(model)
        assert mapper.version_id_col is not None, (
            f"{model.__name__} mapper has no version_id_col -- "
            f"__mapper_args__ regression."
        )
        assert mapper.version_id_col.name == "version_id"


# ═════════════════════════════════════════════════════════════════════
# Lifecycle: insert, read, update, delete bumps
# ═════════════════════════════════════════════════════════════════════


class TestTransactionVersionLifecycle:
    """End-to-end behaviour of ``Transaction.version_id`` through the ORM."""

    def test_new_transaction_starts_at_version_one(
        self, app, seed_user, seed_periods,
    ):
        """``server_default='1'`` populates new rows at version 1.

        A FRESH row -- the one-off builder's, one INSERT and no UPDATE --
        rather than :func:`_make_transaction`'s re-priced row, whose
        re-price is that row's first UPDATE.
        """
        with app.app_context():
            txn = one_off_row_of(
                seed_periods[0], name="Test Txn", amount="50.00",
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            assert txn.version_id == 1

    def test_version_increments_only_on_update(
        self, app, seed_user, seed_periods,
    ):
        """Pure SELECT does not bump the counter; UPDATE bumps by one.

        A regression in either direction would be catastrophic:
        bumping on read would make every cached form stale; not
        bumping on update would make the lock no-op.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            v0 = txn.version_id
            txn_id = txn.id

            # Five reads must not bump the counter.
            for _ in range(5):
                _ = db.session.get(Transaction, txn_id).estimated_amount
                db.session.expire_all()
            assert _read_version(Transaction, txn_id) == v0

            # First update bumps by exactly one.
            txn = db.session.get(Transaction, txn_id)
            txn.notes = "updated"
            db.session.commit()
            assert _read_version(Transaction, txn_id) == v0 + 1

            # Second update bumps by exactly one more.
            txn = db.session.get(Transaction, txn_id)
            txn.notes = "updated again"
            db.session.commit()
            assert _read_version(Transaction, txn_id) == v0 + 2


@pytest.mark.parametrize(
    "factory_fn",
    [
        # Each factory is a callable taking (seed_user, seed_periods)
        # and returning the model id to bump.  Keeps the parameterised
        # test row-shape narrow.
        pytest.param("transfer", id="Transfer"),
        pytest.param("txn_template", id="TransactionTemplate"),
        pytest.param("xfer_template", id="TransferTemplate"),
        pytest.param("savings_goal", id="SavingsGoal"),
        pytest.param("salary_profile", id="SalaryProfile"),
        pytest.param("salary_raise", id="SalaryRaise"),
        pytest.param("paycheck_line", id="PaycheckLine"),
        pytest.param("transaction_entry", id="TransactionEntry"),
    ],
)
def test_concurrent_update_raises_stale_data_error(
    app, seed_user, seed_periods, factory_fn,
):
    """A version-bump out from under the test session raises StaleDataError.

    Same shape as test_concurrent_update_raises_stale_data_error
    in test_accounts/test_stale_data.py but parameterised across every C-18
    versioned model.  This is the load-bearing invariant that
    makes the SQLAlchemy tier of the optimistic lock work.
    """
    with app.app_context():
        period = seed_periods[0]
        if factory_fn == "transfer":
            obj = _make_transfer(seed_user, period)
            schema, table, model = "budget", "transfers", Transfer

            def mutate(o):
                state_own_amount(o, Decimal("999.99"))
        elif factory_fn == "txn_template":
            cat = seed_user["categories"]["Groceries"]
            obj = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            schema, table, model = (
                "budget", "transaction_templates", TransactionTemplate,
            )

            # A column of the row itself: the price it mutated went with plan
            # step X-bp, and lives in the series, a row of another table.
            def mutate(o):
                o.name = "Concurrently Renamed"
        elif factory_fn == "xfer_template":
            savings = _make_savings_account(seed_user["user"].id)
            cat = seed_user["categories"]["Groceries"]
            obj = _make_transfer_template(
                seed_user["user"].id,
                seed_user["account"].id,
                savings.id,
                cat.id,
            )
            schema, table, model = (
                "budget", "transfer_templates", TransferTemplate,
            )

            # A column of the row itself (see the transaction twin above).
            def mutate(o):
                o.name = "Concurrently Renamed"
        elif factory_fn == "savings_goal":
            savings = _make_savings_account(seed_user["user"].id)
            obj = _make_savings_goal(seed_user["user"].id, savings.id)
            schema, table, model = "budget", "savings_goals", SavingsGoal

            def mutate(o):
                o.target_amount = Decimal("999.99")
        elif factory_fn == "salary_profile":
            obj = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            schema, table, model = (
                "salary", "salary_profiles", SalaryProfile,
            )

            # A monetary column of the row (the yearly salary it mutated
            # went with plan step salary:X-av-3a).
            def mutate(o):
                o.additional_income = Decimal("99999.99")
        elif factory_fn == "salary_raise":
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            obj = _make_salary_raise(profile.id)
            schema, table, model = "salary", "salary_raises", SalaryRaise

            def mutate(o):
                o.percentage = Decimal("0.05")
        elif factory_fn == "paycheck_line":
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            obj = _make_paycheck_line(profile.id)
            schema, table, model = (
                "salary", "paycheck_lines", PaycheckLine,
            )

            def mutate(o):
                o.amount = Decimal("999.99")
        else:  # transaction_entry
            _tmpl, txn = _make_envelope_template_and_txn(seed_user, period)
            obj = _make_entry(txn.id, seed_user["user"].id)
            schema, table, model = (
                "budget", "transaction_entries", TransactionEntry,
            )

            def mutate(o):
                o.amount = Decimal("99.99")

        obj_id = obj.id
        assert _read_version(model, obj_id) == 1

        # Test session loads at version 1.
        loaded = db.session.get(model, obj_id)
        # Concurrent commit bumps the row to version 2.
        _bump_version_outside_session(schema, table, obj_id)
        # Test session attempts to mutate the stale row.
        mutate(loaded)
        with pytest.raises(StaleDataError):
            db.session.commit()
        db.session.rollback()

        # Persisted row carries the winner's version, not the loser's.
        assert _read_version(model, obj_id) == 2
