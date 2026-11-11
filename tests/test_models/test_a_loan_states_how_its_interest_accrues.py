"""A loan states how its interest accrues (plan step recurrence:R16-d, finding D52).

The convention list ``ref.interest_accrual_conventions``, the key a loan row
holds into it (``budget.loan_params.interest_accrual_convention_id``), the one
door that writes a loan row, and migration ``08fda2e0b18b`` in both directions,
with its refusal of a loan the backfill missed.

The list has ONE member, ``monthly_1_12`` (developer ruling plan#3354), so
every case here expects that one id: a loan cannot name any other because no
other exists.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import InterestAccrualConventionEnum
from app.models.loan_params import LoanParams
from app.models.ref import AccountType, InterestAccrualConvention
from app.services import account_service
from tests._test_helpers import (
    create_loan_account,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "08fda2e0b18b_a_loan_states_how_its_interest_accrues.py",
)

_KEY = "fk_loan_params_interest_accrual_convention"


def _monthly_id() -> int:
    """Return the id ``ref_cache`` holds for ``monthly_1_12``."""
    return ref_cache.interest_accrual_convention_id(
        InterestAccrualConventionEnum.MONTHLY_1_12,
    )


class TestTheList:
    """``ref.interest_accrual_conventions`` holds exactly the enum's members."""

    def test_the_list_is_the_one_member(self, app, db):
        """One row, ``monthly_1_12``, and the cache names it both ways."""
        with app.app_context():
            rows = db.session.query(InterestAccrualConvention).all()
            assert [(row.id, row.name) for row in rows] == [
                (_monthly_id(), "monthly_1_12"),
            ]
            assert ref_cache.interest_accrual_convention_member(
                _monthly_id(),
            ) is InterestAccrualConventionEnum.MONTHLY_1_12

    def test_an_id_the_list_lacks_names_no_member(self, app):
        """The inverse answers ``None`` for an id no row holds, never a guess."""
        with app.app_context():
            assert ref_cache.interest_accrual_convention_member(
                _monthly_id() + 1,
            ) is None


class TestTheLoanRow:
    """Every loan row names a convention, and the setup door states it."""

    @pytest.mark.usefixtures("seed_periods")
    def test_the_setup_door_states_monthly(self, auth_client, seed_user, db):
        """A loan set up through the door holds ``monthly_1_12``, the one member."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Convention Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )

        assert resp.status_code == 302
        params = db.session.query(LoanParams).filter_by(account_id=account.id).one()
        assert params.interest_accrual_convention_id == _monthly_id()

    def test_a_row_naming_no_convention_is_refused(self, app, db, seed_user):
        """NOT NULL is the backstop: a writer that forgets the key cannot insert."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Refused Loan")
            params = db.session.query(LoanParams).filter_by(
                account_id=loan.id,
            ).one()
            db.session.delete(params)
            db.session.flush()
            db.session.add(LoanParams(
                account_id=loan.id,
                original_principal=Decimal("20000.00"),
                term_months=60,
                origination_date=date(2026, 1, 15),
                payment_day=15,
            ))
            with pytest.raises(IntegrityError, match="interest_accrual_convention_id"):
                db.session.flush()
            db.session.rollback()


def _loan_params_state(db) -> tuple[dict, set]:
    """Return ``loan_params``' columns (name -> nullability) and its constraint names."""
    columns = dict(db.session.execute(sqlalchemy.text(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'loan_params'"
    )).all())
    constraints = set(db.session.execute(sqlalchemy.text(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'budget.loan_params'::regclass"
    )).scalars())
    return columns, constraints


def _list_exists(db) -> bool:
    """Return whether ``ref.interest_accrual_conventions`` exists."""
    return db.session.execute(sqlalchemy.text(
        "SELECT to_regclass('ref.interest_accrual_conventions') IS NOT NULL"
    )).scalar_one()


@pytest.mark.xdist_group("loan_params_ddl")
class TestTheMigration:
    """``08fda2e0b18b`` over this test's own clone, in both directions."""

    def test_down_removes_the_three_objects_and_up_restores_them(self, app, db):
        """Down drops the key, the column and the list; up restores each, NOT NULL."""
        with app.app_context():
            before_columns, before_constraints = _loan_params_state(db)
            assert before_columns["interest_accrual_convention_id"] == "NO"
            assert _KEY in before_constraints

            run_migration_callable(_MIGRATION.downgrade, db.session)

            columns, constraints = _loan_params_state(db)
            assert "interest_accrual_convention_id" not in columns
            # PostgreSQL 18 records a column's NOT NULL as a constraint of its
            # own, named ``<table>_<column>_not_null``; it goes with the column.
            assert before_constraints - constraints == {
                _KEY, "loan_params_interest_accrual_convention_id_not_null",
            }
            assert not _list_exists(db)

            run_migration_callable(_MIGRATION.upgrade, db.session)

            assert _loan_params_state(db) == (before_columns, before_constraints)
            assert _list_exists(db)
            names = db.session.execute(sqlalchemy.text(
                "SELECT name FROM ref.interest_accrual_conventions"
            )).scalars().all()
            assert names == ["monthly_1_12"]
            db.session.rollback()

    def test_the_backfill_gives_every_existing_loan_monthly(
        self, app, db, seed_user,
    ):
        """Two loans present before the revision both come out ``monthly_1_12``."""
        with app.app_context():
            first = create_loan_account(seed_user, db.session, name="Mortgage A")
            second = create_loan_account(seed_user, db.session, name="Van B")
            account_ids = [first.id, second.id]
            run_migration_callable(_MIGRATION.downgrade, db.session)

            run_migration_callable(_MIGRATION.upgrade, db.session)

            stored = db.session.execute(sqlalchemy.text(
                "SELECT lp.account_id, c.name FROM budget.loan_params AS lp "
                "JOIN ref.interest_accrual_conventions AS c "
                "ON c.id = lp.interest_accrual_convention_id "
                "WHERE lp.account_id = ANY(:ids)"
            ), {"ids": account_ids}).all()
            assert sorted(stored) == sorted(
                (account_id, "monthly_1_12") for account_id in account_ids
            )
            db.session.rollback()

    def test_a_loan_the_backfill_missed_stops_the_revision(
        self, app, db, seed_user,
    ):
        """A NULL left after the backfill refuses the revision, naming the count and the SELECT.

        No real upgrade reaches the refusal: the backfill names the row the
        same revision has just seeded.  So the state it reads is built by
        hand -- the revision downgraded, then the column re-added nullable and
        empty under the one loan -- and the refusal is run on it, so its
        message is code that has run.
        """
        with app.app_context():
            create_loan_account(seed_user, db.session, name="Missed Loan")
            run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.execute(sqlalchemy.text(
                "ALTER TABLE budget.loan_params "
                "ADD COLUMN interest_accrual_convention_id integer"
            ))

            with pytest.raises(RuntimeError) as refusal:
                run_migration_callable(
                    _MIGRATION._refuse_a_surviving_null, db.session,
                )

            assert str(refusal.value) == (
                "1 budget.loan_params row(s) hold no interest accrual "
                "convention after the backfill to 'monthly_1_12'.  Inspect "
                "with: SELECT id, account_id FROM budget.loan_params WHERE "
                "interest_accrual_convention_id IS NULL;"
            )
            db.session.rollback()
