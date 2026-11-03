"""
Shekel Budget App -- ``budget.credit_card_params`` (plan step credit_card:CC-2)

The card's TERMS table, as designed in ``docs/design/credit_card_from_scratch.md``
3.4 (rulings **R-CC2**, **R-CC4**).  These tests pin the schema half of the
leaf:

* every CHECK refuses its out-of-domain value BY NAME, one case per
  constraint, with the control that an in-domain row is stored (a CHECK that
  refused everything would pass every refusal case);
* the CHECKs' text on the model equals the migrations', name for name,
  because Alembic autogenerate does not diff a CHECK and the migration's
  docstring names this module as what keeps them equal;
* a card holds one version of its terms per effective date (plan#2331,
  ruling plan#2333: the terms are a dated series), and each row's owner is
  the ACCOUNT's owner by key (``fk_credit_card_params_owner``) -- a row
  naming another owner is unstorable rather than checked by a reader;
* the rows die with their account (CASCADE on both keys);
* the migration round-trips on this test's own clone: down drops the table
  and its audit trigger, up restores both.  The dating revision
  ``0703995c8536`` is graded in ``test_card_terms_dated_migration.py``.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy
from alembic import op
from alembic.autogenerate import compare_metadata
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app.audit_infrastructure import AUDITED_TABLES
from app.extensions import db
from app.models.account import Account
from app.models.credit_card_params import CreditCardParams
from tests._test_helpers import create_account_of_type, load_migration_module

_MIGRATION = load_migration_module("97f92340fffc_the_cards_terms.py")
#: The revision after it on this table, which a round trip of the one above
#: must re-apply before the model can write a row again.
_DATED = load_migration_module("0703995c8536_a_cards_terms_are_dated.py")

#: An in-domain row, the base every refusal case perturbs one column of.
_VALID_TERMS = {
    "effective_date": date(2026, 1, 1),
    "statement_close_day": 20,
    "payment_due_day": 15,
    "min_payment_percent": Decimal("0.0200"),
    "min_payment_floor": Decimal("25.00"),
    "cashback_rate": Decimal("0.0150"),
    "auto_redeem_threshold": Decimal("25.00"),
    "credit_limit": Decimal("5000.00"),
}

#: One out-of-domain value per CHECK, keyed by the constraint each must name.
_REFUSED = [
    ("statement_close_day", 0, "ck_credit_card_params_statement_close_day"),
    ("statement_close_day", 32, "ck_credit_card_params_statement_close_day"),
    ("payment_due_day", 0, "ck_credit_card_params_payment_due_day"),
    ("payment_due_day", 32, "ck_credit_card_params_payment_due_day"),
    ("min_payment_percent", Decimal("1.0001"),
     "ck_credit_card_params_min_payment_percent"),
    ("min_payment_percent", Decimal("-0.0001"),
     "ck_credit_card_params_min_payment_percent"),
    ("cashback_rate", Decimal("1.0001"), "ck_credit_card_params_cashback_rate"),
    ("cashback_rate", Decimal("-0.0001"), "ck_credit_card_params_cashback_rate"),
    ("min_payment_floor", Decimal("-0.01"),
     "ck_credit_card_params_min_payment_floor"),
    ("auto_redeem_threshold", Decimal("0.00"),
     "ck_credit_card_params_auto_redeem_threshold"),
    ("credit_limit", Decimal("0.00"), "ck_credit_card_params_credit_limit"),
    ("effective_date", date(1999, 12, 31),
     "ck_credit_card_params_effective_date_range"),
    ("effective_date", date(2101, 1, 1),
     "ck_credit_card_params_effective_date_range"),
]


def _card(seed_user, name="Visa"):
    """A Credit Card account of *seed_user*'s, with no terms row."""
    return create_account_of_type(seed_user, db.session, "Credit Card", name)


def _terms_for(account, **overrides):
    """An UNFLUSHED terms row for *account*, valid unless *overrides* say not."""
    values = {**_VALID_TERMS, **overrides}
    return CreditCardParams(
        account_id=account.id, user_id=account.user_id, **values,
    )


def _table_exists():
    """Whether ``budget.credit_card_params`` exists on this connection."""
    return db.session.execute(sqlalchemy.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'budget' AND table_name = 'credit_card_params')"
    )).scalar()


def _trigger_exists():
    """Whether the table's audit trigger exists on this connection."""
    return db.session.execute(sqlalchemy.text(
        "SELECT EXISTS (SELECT 1 FROM pg_trigger "
        "WHERE tgname = 'audit_credit_card_params')"
    )).scalar()


def _run(step):
    """Drive one of the migration's steps over this test's own connection."""
    connection = db.session.connection()
    ctx = MigrationContext.configure(connection=connection)
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=connection):
            step()


class TestTheDomainIsTheDatabases:
    """Each CHECK refuses its value by name; the in-domain row is admitted."""

    def test_an_in_domain_row_is_stored_whole(self, app, seed_user):
        """The control: every column round-trips, ``cashback_rate`` included.

        Without this the refusal cases below could be satisfied by a table
        that refused every row.
        """
        with app.app_context():
            account = _card(seed_user)
            db.session.add(_terms_for(account))
            db.session.commit()
            stored = db.session.query(CreditCardParams).filter_by(
                account_id=account.id,
            ).one()
            for column, value in _VALID_TERMS.items():
                assert getattr(stored, column) == value, column
            assert stored.user_id == seed_user["user"].id

    def test_the_bound_of_each_domain_is_admitted(self, app, seed_user):
        """Each domain's bounds are stored: the date window's, day 1 and 31, 0% and 100%.

        Jan 1, 2000 and Dec 31, 2100, a zero floor and a one-cent limit too.

        The refusal cases below probe one step OUTSIDE each domain; without
        this, a CHECK written one step too tight (``<= 30``, ``< 1``) would
        pass every one of them.  Two rows, so both ends of each two-sided
        domain are stored.
        """
        with app.app_context():
            low = _card(seed_user, name="Low")
            high = _card(seed_user, name="High")
            db.session.add(_terms_for(
                low, effective_date=date(2000, 1, 1),
                statement_close_day=1, payment_due_day=1,
                min_payment_percent=Decimal("0"), cashback_rate=Decimal("0"),
                min_payment_floor=Decimal("0.00"),
                auto_redeem_threshold=Decimal("0.01"),
                credit_limit=Decimal("0.01"),
            ))
            db.session.add(_terms_for(
                high, effective_date=date(2100, 12, 31),
                statement_close_day=31, payment_due_day=31,
                min_payment_percent=Decimal("1"), cashback_rate=Decimal("1"),
            ))
            db.session.commit()
            stored_low = db.session.query(CreditCardParams).filter_by(
                account_id=low.id,
            ).one()
            stored_high = db.session.query(CreditCardParams).filter_by(
                account_id=high.id,
            ).one()
            assert stored_low.effective_date == date(2000, 1, 1)
            assert stored_high.effective_date == date(2100, 12, 31)
            assert (stored_low.statement_close_day, stored_low.payment_due_day) == (1, 1)
            assert stored_low.min_payment_percent == stored_low.cashback_rate == Decimal("0")
            assert stored_low.min_payment_floor == Decimal("0")
            assert stored_low.auto_redeem_threshold == stored_low.credit_limit == Decimal("0.01")
            assert (stored_high.statement_close_day, stored_high.payment_due_day) == (31, 31)
            assert stored_high.min_payment_percent == stored_high.cashback_rate == Decimal("1")

    def test_the_cashback_default_is_zero_on_the_orm_tier(self, app, seed_user):
        """A row built without ``cashback_rate`` stores ``0`` (E-12: a value)."""
        with app.app_context():
            account = _card(seed_user)
            values = {k: v for k, v in _VALID_TERMS.items() if k != "cashback_rate"}
            db.session.add(CreditCardParams(
                account_id=account.id, user_id=account.user_id, **values,
            ))
            db.session.commit()
            stored = db.session.query(CreditCardParams).filter_by(
                account_id=account.id,
            ).one()
            assert stored.cashback_rate == Decimal("0")

    def test_the_cashback_default_is_zero_on_the_storage_tier(self, app, seed_user):
        """A raw INSERT omitting ``cashback_rate`` stores ``0`` too.

        The Python-side default serves the ORM constructor only; the
        ``server_default`` is what a writer outside the ORM gets, and the two
        must agree or ``compare_server_default`` reports a drift (the class
        ledger row **SAL-563** names four of).
        """
        with app.app_context():
            account = _card(seed_user)
            db.session.execute(sqlalchemy.text(
                "INSERT INTO budget.credit_card_params "
                "(effective_date, statement_close_day, payment_due_day, "
                " min_payment_percent, min_payment_floor, account_id, user_id) "
                "VALUES ('2026-01-01', 20, 15, 0.02, 25.00, :account_id, :user_id)"
            ), {"account_id": account.id, "user_id": account.user_id})
            stored = db.session.execute(sqlalchemy.text(
                "SELECT cashback_rate FROM budget.credit_card_params "
                "WHERE account_id = :account_id"
            ), {"account_id": account.id}).scalar()
            assert stored == Decimal("0")
            db.session.rollback()

    @pytest.mark.parametrize("column, value, check_name", _REFUSED)
    def test_an_out_of_domain_value_is_refused_by_name(
        self, app, seed_user, column, value, check_name,
    ):
        """*column* = *value* fails *check_name* on INSERT.

        The refusal must NAME the constraint: a bare integrity error could be
        any of the table's other constraints, and a CHECK dropped from the
        table would leave the sibling cases green.
        """
        with app.app_context():
            account = _card(seed_user)
            db.session.add(_terms_for(account, **{column: value}))
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert check_name in str(excinfo.value)

    def test_autogenerate_sees_no_drift_on_the_table(self, app):
        """The migrated table and the model agree on every column, type, default and key.

        Alembic's own comparison over the test database (migrated, never
        ``create_all``'d), scoped to this one table, with server defaults
        compared -- the check that found the four ``SAL-563`` drifts on older
        tables.  A CHECK is outside its sight; the test below covers those.
        """
        with app.app_context():
            connection = db.session.connection()
            ctx = MigrationContext.configure(
                connection=connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_schemas": True,
                    # Filters BOTH sides (the model's tables and the
                    # database's), so an older table's drift stays out of
                    # this test's verdict; ``include_name`` would filter
                    # only the reflected side and report every model table
                    # as an ``add_table``.
                    "include_object": lambda obj, name, type_, *_: (
                        type_ != "table"
                        or (obj.schema, name) == ("budget", "credit_card_params")
                    ),
                },
            )
            assert compare_metadata(ctx, db.metadata) == []

    def test_the_model_and_the_migration_state_the_same_checks(self, app):
        """Every CHECK's SQL on the model equals the migrations', by name.

        The table's CHECKs come from two revisions: the seven of the one that
        creates it, and the date window the dating revision adds.

        Compared on the model's own rendering of each constraint the test
        database was created WITH, not on prose; and compared as a MAPPING so
        a CHECK added on one side and not the other is a failure too.
        """
        with app.app_context():
            model_checks = {
                c.name: str(c.sqltext)
                for c in CreditCardParams.__table__.constraints
                if isinstance(c, sqlalchemy.CheckConstraint)
            }
            # pylint: disable=protected-access
            assert model_checks == {
                **_MIGRATION._CHECKS,
                _DATED._DATE_WINDOW: _DATED._DATE_WINDOW_SQL,
            }


class TestOneVersionPerDayAndOwned:
    """One version per card per date, and its owner is the account's by key."""

    def test_a_second_version_on_the_same_date_is_refused(self, app, seed_user):
        """``(account_id, effective_date)`` is UNIQUE: one version per day.

        The key the terms door's ``ON CONFLICT`` names
        (``card_terms.set_terms``), so a same-day save corrects the version
        rather than adding a second one the as-of read could not choose
        between.
        """
        with app.app_context():
            account = _card(seed_user)
            db.session.add(_terms_for(account))
            db.session.commit()
            db.session.add(_terms_for(account, statement_close_day=5))
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "uq_credit_card_params_account_effective_date" in str(
                excinfo.value,
            )

    def test_a_second_date_is_a_second_version(self, app, seed_user):
        """The pair for the refusal above: another day stores beside the first.

        Without it, a key still on ``account_id`` alone would pass the case
        above too.
        """
        with app.app_context():
            account = _card(seed_user)
            db.session.add(_terms_for(account))
            db.session.add(_terms_for(
                account, effective_date=date(2026, 3, 1), statement_close_day=5,
            ))
            db.session.commit()
            stored = db.session.query(CreditCardParams).filter_by(
                account_id=account.id,
            ).order_by(CreditCardParams.effective_date).all()
            assert [(r.effective_date, r.statement_close_day) for r in stored] == [
                (date(2026, 1, 1), 20), (date(2026, 3, 1), 5),
            ]

    def test_a_row_naming_another_owner_is_unstorable(
        self, app, seed_user, second_user,
    ):
        """``user_id`` must be the ACCOUNT's owner, or ``fk_credit_card_params_owner`` refuses.

        Both users exist and the second's id satisfies the bare
        ``user_id -> auth.users`` key perfectly well; it is the composite key
        onto ``uq_accounts_id_user`` that makes the pair unwritable.  This is
        the IDOR every create door probes for by hand, made structural.
        """
        with app.app_context():
            account = _card(seed_user)
            row = _terms_for(account)
            row.user_id = second_user["user"].id
            db.session.add(row)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "fk_credit_card_params_owner" in str(excinfo.value)

    def test_the_rows_die_with_their_account(self, app, seed_user):
        """Deleting the account cascades to every version (both keys CASCADE).

        A card with no transactions is deletable; the cascade is what keeps a
        terms version from outliving the account it describes.
        """
        with app.app_context():
            account = _card(seed_user)
            db.session.add(_terms_for(account))
            db.session.add(_terms_for(account, effective_date=date(2026, 3, 1)))
            db.session.commit()
            account_id = account.id
            db.session.delete(db.session.get(Account, account_id))
            db.session.commit()
            assert db.session.query(CreditCardParams).filter_by(
                account_id=account_id,
            ).count() == 0


class TestTheTableIsAudited:
    """The table is in the audited set and carries its trigger."""

    def test_the_table_is_in_the_audited_set(self):
        """``AUDITED_TABLES`` names it, so the entrypoint's trigger count includes it."""
        assert ("budget", "credit_card_params") in AUDITED_TABLES

    def test_a_write_leaves_an_audit_row(self, app, seed_user):
        """An INSERT through the ORM lands in ``system.audit_log`` for this table."""
        with app.app_context():
            account = _card(seed_user)
            db.session.add(_terms_for(account))
            db.session.commit()
            count = db.session.execute(sqlalchemy.text(
                "SELECT COUNT(*) FROM system.audit_log "
                "WHERE table_schema = 'budget' "
                "AND table_name = 'credit_card_params' "
                "AND operation = 'INSERT'"
            )).scalar()
            assert count == 1


class TestTheMigrationRoundTrips:
    """``97f92340fffc``'s two steps, driven over this test's own clone."""

    def test_the_downgrade_drops_the_table_and_the_upgrade_restores_it(
        self, app, seed_user,
    ):
        """Down: table and trigger gone.  Up: both back, and a row is storable.

        The table this revision's upgrade creates is the PRE-dating shape, so
        the dating revision (``0703995c8536``) is applied after it before the
        model writes a row: the head shape is the one the model maps.
        """
        with app.app_context():
            assert _table_exists() is True
            assert _trigger_exists() is True

            _run(_MIGRATION.downgrade)
            db.session.commit()
            assert _table_exists() is False
            assert _trigger_exists() is False

            _run(_MIGRATION.upgrade)
            db.session.commit()
            assert _table_exists() is True
            assert _trigger_exists() is True

            _run(_DATED.upgrade)
            db.session.commit()

            account = _card(seed_user)
            db.session.add(_terms_for(account))
            db.session.commit()
            assert db.session.query(CreditCardParams).filter_by(
                account_id=account.id,
            ).count() == 1
