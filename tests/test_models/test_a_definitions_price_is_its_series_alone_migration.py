"""Tests for migration ``4d4eb9cd3850``: a definition's price is its series alone.

Plan step **balance:X-bp** (card plan#1045, rulings **R-IY** and **R-BAL16**).
The upgrade drops ``default_amount`` and its CHECK from
``budget.transaction_templates`` and ``budget.transfer_templates``.  The
downgrade is ruling **plan#2695**: each column comes back nullable, is
restored -- a definition that states its price from its series' NEWEST
version, a salary profile's definition from its latest non-deleted Received
row's recorded figure -- then made ``NOT NULL`` with its CHECK; a loan payment
that follows its loan, a paycheck no Received row records and a definition
with no series are REFUSED, by name, and nothing changes.

**Both directions are EXECUTED** (``run_migration_callable``) over each case's
own database, which the ``db`` fixture re-clones per test.  The models on this
tree do not map the column, so every world is built through the ORM FIRST and
the restored copies are read back by SQL.  Each refusal is a control SHOWN TO
FIRE, and every restore arm is built so that the predicate it rests on can
fail: a newest version that is not the newest by id, an archived profile's
definition beside a Received row it must not read, a deleted Received row
newer than the one it must read, and a reactivated profile's dormant series
that must not answer for it.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.utils.dates import display_today
from tests._test_helpers import (
    create_savings_account,
    generate_row_of,
    load_migration_module,
    make_every_period_rule,
    make_expense_template,
    make_income_template,
    make_salary_profile,
    make_transfer_template,
    run_migration_callable,
    settle_cash_row,
    state_template_price,
)

_MIGRATION = load_migration_module(
    "4d4eb9cd3850_a_definitions_price_is_its_series_alone.py"
)

#: Each table, the CHECK its copy carried, and that CHECK as PostgreSQL
#: states it.
_CHECKS = {
    "transaction_templates": (
        "ck_transaction_templates_nonneg_amount",
        "CHECK ((default_amount >= (0)::numeric))",
    ),
    "transfer_templates": (
        "ck_transfer_templates_positive_amount",
        "CHECK ((default_amount > (0)::numeric))",
    ),
}

#: The series' two floors, which the upgrade leaves standing.
_SERIES_FLOORS = (
    "ck_template_amount_versions_nonneg_amount",
    "ck_template_amount_versions_transfer_positive_amount",
)


# ── Reading the schema and the copies ────────────────────────────────


def _is_nullable(table):
    """Return ``'YES'`` / ``'NO'`` for *table*'s ``default_amount``, or ``None`` when absent."""
    return db.session.execute(sa.text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = :t "
        "AND column_name = 'default_amount'"
    ), {"t": table}).scalar()


def _constraint(table, name):
    """Return constraint *name*'s definition on ``budget.<table>``, or ``None``."""
    return db.session.execute(sa.text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = CAST(:rel AS regclass) AND conname = :n"
    ), {"rel": f"budget.{table}", "n": name}).scalar()


def _copy(table, definition_id):
    """Return definition *definition_id*'s restored ``default_amount``, by SQL."""
    return db.session.execute(sa.text(
        f"SELECT default_amount FROM budget.{table} WHERE id = :d"
    ), {"d": definition_id}).scalar_one()


def _assert_head_schema():
    """Neither table holds the copy or its CHECK; the series keeps both floors."""
    for table, (check, _definition) in _CHECKS.items():
        assert _is_nullable(table) is None, table
        assert _constraint(table, check) is None, table
    for floor in _SERIES_FLOORS:
        assert _constraint("template_amount_versions", floor) is not None, floor


# ── Building the definitions each arm reads ──────────────────────────


def _paycheck_definition(seed_user, name, *, is_active=True, stated=None):
    """Return a paycheck definition a salary profile names, and the profile.

    *stated* opens a price series first, as an archive does
    (``routes/salary/profiles.delete_profile``); a profile active again
    afterwards leaves that series DORMANT.  Without it the definition states
    no price, as the create door builds one.

    Args:
        seed_user: The seeded owner bundle.
        name: The definition's and the profile's name.
        is_active: Whether the profile is active (``False``: archived).
        stated: The series' one price, as a string, or ``None`` for none.

    Returns:
        ``(definition, profile)``, flushed.
    """
    if stated is None:
        definition = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Salary"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            name=name,
        )
        db.session.add(definition)
        db.session.flush()
        make_every_period_rule(db.session, definition)
    else:
        definition = make_income_template(
            db.session, seed_user, amount=stated, name=name,
        )
    profile = make_salary_profile(seed_user, db.session, name=name)
    profile.template_id = definition.id
    profile.is_active = is_active
    db.session.flush()
    return definition, profile


def _received(definition, period, recorded):
    """Return *definition*'s row in *period*, Received, recording *recorded*.

    The engine's row (:func:`generate_row_of`), settled Received on its
    paycheck's start through the status seam with *recorded* typed, so its
    covering movement holds that figure -- and none for ``$0.00``, which the
    seam writes none for (ruling R-BAL82).

    Args:
        definition: The paycheck definition.
        period: The pay period the row is funded in.
        recorded: The figure the bank or the owner recorded, as a string.

    Returns:
        The flushed row.
    """
    return settle_cash_row(
        generate_row_of(definition, period), is_income=True,
        settled_amount=Decimal(recorded), settled_on=period.start_date,
    )


def _loan_payment(seed_user, account, name, *, derive_from_loan, stated=None):
    """Return a loan-payment transfer definition, following its loan or not.

    Args:
        seed_user: The seeded owner bundle.
        account: The destination account.
        name: The definition's name.
        derive_from_loan: Whether its payment follows the loan (amount rule 4
            prices it, and it states no price).
        stated: The price a fixed payment states, as a string.

    Returns:
        The flushed definition.
    """
    definition = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=account.id,
        name=name,
    )
    definition.settings = LoanPaymentSettings(
        derive_from_loan=derive_from_loan, extra_principal=Decimal("0.00"),
    )
    db.session.add(definition)
    db.session.flush()
    if stated is not None:
        state_template_price(definition, Decimal(stated))
    return definition


# ── The upgrade ──────────────────────────────────────────────────────


class TestTheUpgrade:
    """Both copies and their CHECKs go; the series and its floors stay."""

    def test_head_holds_no_copy_and_the_series_keeps_both_floors(self, app):
        """The suite's database is built at head, through this revision."""
        with app.app_context():
            _assert_head_schema()

    def test_down_then_up_drops_both_copies_again_and_moves_no_price(
        self, app, db, seed_user,
    ):
        """A round trip leaves the schema at head and every version as it was."""
        with app.app_context():
            make_expense_template(db.session, seed_user, amount="15.49", name="Netflix")
            db.session.commit()
            versions = db.session.execute(sa.text(
                "SELECT * FROM budget.template_amount_versions ORDER BY id"
            )).mappings().all()

            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert _is_nullable("transaction_templates") == "NO"
            run_migration_callable(_MIGRATION.upgrade, db.session)

            _assert_head_schema()
            assert db.session.execute(sa.text(
                "SELECT * FROM budget.template_amount_versions ORDER BY id"
            )).mappings().all() == versions


# ── The downgrade's restores ─────────────────────────────────────────


class TestTheDowngradeRestoresEachCopy:
    """Ruling plan#2695's two restores, each arm's predicate able to fail."""

    def test_a_stated_price_comes_back_as_its_series_newest_version(
        self, app, db, seed_user, seed_periods,
    ):
        """The NEWEST by effective date, a price scheduled ahead included.

        Netflix states $15.49 today, then $17.99 from 30 days on, then is
        back-dated to $12.00 from 30 days ago: by id the last version is
        $12.00, by date it is $17.99, and the copy always held the latter
        (``set_amount`` kept it on the newest).  A transfer at $500.00 then
        $250.00, a loan payment at a fixed $531.94 (its settings row does
        not make it follow the loan), and an ARCHIVED profile's paycheck
        whose series opened at $2,611.95 come back from their series too --
        the last beside a Received $2,572.35 the paycheck arm must not read,
        because no active profile names it.  Then both columns are
        ``NOT NULL`` under their CHECKs again.
        """
        with app.app_context():
            today = display_today()
            netflix = make_expense_template(
                db.session, seed_user, amount="15.49", name="Netflix",
            )
            state_template_price(
                netflix, Decimal("17.99"), effective_on=today + timedelta(days=30),
            )
            state_template_price(
                netflix, Decimal("12.00"), effective_on=today - timedelta(days=30),
            )
            savings = create_savings_account(
                seed_user, db.session, "Money Market", Decimal("0.00"),
            )
            transfer = make_transfer_template(
                db.session, seed_user, savings, amount="500.00",
            )
            state_template_price(
                transfer, Decimal("250.00"), effective_on=today + timedelta(days=14),
            )
            van = _loan_payment(
                seed_user, savings, "Van Payment", derive_from_loan=False,
                stated="531.94",
            )
            archived, _profile = _paycheck_definition(
                seed_user, "Old Job", is_active=False, stated="2611.95",
            )
            _received(archived, seed_periods[0], "2572.35")
            db.session.commit()

            run_migration_callable(_MIGRATION.downgrade, db.session)

            assert _copy("transaction_templates", netflix.id) == Decimal("17.99")
            assert _copy("transfer_templates", transfer.id) == Decimal("250.00")
            assert _copy("transfer_templates", van.id) == Decimal("531.94")
            assert _copy("transaction_templates", archived.id) == Decimal("2611.95")
            for table, (check, definition) in _CHECKS.items():
                assert _is_nullable(table) == "NO", table
                assert _constraint(table, check) == definition, table

    def test_a_paycheck_comes_back_as_its_latest_received_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The latest non-deleted Received row's entries, never a newer deleted one.

        Received $2,500.00 in the first paycheck and $2,572.35 in the second;
        a third, Received and recording nothing, is soft-deleted (one holding
        a recorded payment cannot be: ``budget.refuse_hiding_a_row_holding_
        money``), so reading it would answer ``$0.00``; a fourth is still
        Projected.  The copy is $2,572.35, the figure the old code kept on
        the paycheck's edit page until its next salary save.
        """
        with app.app_context():
            paycheck, _profile = _paycheck_definition(seed_user, "Data Manager")
            _received(paycheck, seed_periods[0], "2500.00")
            _received(paycheck, seed_periods[1], "2572.35")
            deleted = _received(paycheck, seed_periods[2], "0.00")
            deleted.is_deleted = True
            generate_row_of(paycheck, seed_periods[3])
            db.session.commit()

            run_migration_callable(_MIGRATION.downgrade, db.session)

            assert _copy("transaction_templates", paycheck.id) == Decimal("2572.35")

    def test_a_received_row_recording_nothing_comes_back_as_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled row with no entry recorded ``$0.00`` (ruling R-BAL82).

        The latest Received paycheck recorded nothing, so its figure is
        ``$0.00``: not the older $2,500.00, and not a refusal naming a
        paycheck "no Received row records", which would be false.
        """
        with app.app_context():
            paycheck, _profile = _paycheck_definition(seed_user, "Unpaid Leave")
            _received(paycheck, seed_periods[0], "2500.00")
            _received(paycheck, seed_periods[1], "0.00")
            db.session.commit()

            run_migration_callable(_MIGRATION.downgrade, db.session)

            assert _copy("transaction_templates", paycheck.id) == Decimal("0.00")


# ── The downgrade's refusals ─────────────────────────────────────────


@pytest.fixture(name="unrestorable")
def _unrestorable(app, db, seed_user, seed_periods):
    """A world holding one restorable definition and the three no fact restores.

    * ``Rent`` states $1,200.00: restored, so it is not named;
    * ``Day Job``, an ACTIVE profile's paycheck with only a Projected row --
      and a DORMANT series at $2,611.95 from an earlier archive, which must
      not answer for it (the series arm skips a definition an active
      profile names);
    * ``Gadget``, a definition no write door leaves without a series;
    * ``Mortgage``, a loan payment that follows its loan.

    Returns:
        ``{"rent": ..., "day_job": ..., "gadget": ..., "mortgage": ...}``.
    """
    with app.app_context():
        rent = make_expense_template(db.session, seed_user, amount="1200.00")
        day_job, _profile = _paycheck_definition(
            seed_user, "Day Job", stated="2611.95",
        )
        generate_row_of(day_job, seed_periods[0])
        gadget = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            name="Gadget",
        )
        db.session.add(gadget)
        savings = create_savings_account(
            seed_user, db.session, "Escrow", Decimal("0.00"),
        )
        mortgage = _loan_payment(
            seed_user, savings, "Mortgage", derive_from_loan=True,
        )
        db.session.commit()
        return {
            "rent": rent.id, "day_job": day_job.id,
            "gadget": gadget.id, "mortgage": mortgage.id,
        }


class TestTheDowngradeRefuses:
    """No stored fact restores three kinds, and the downgrade changes nothing."""

    def test_the_restores_leave_exactly_the_three_named_with_their_reasons(
        self, app, db, unrestorable,
    ):
        """The module-level probe, driven over the re-added columns.

        Ordered by table, then id; each reason is the one the by-hand line
        needs, and the restorable ``Rent`` is not among them.
        """
        with app.app_context():
            run_migration_callable(_MIGRATION.readd_columns, db.session)
            connection = db.session.connection()
            _MIGRATION.restore_copies(connection)

            missing = [tuple(row) for row in _MIGRATION.unrestored(connection)]

            assert missing == sorted([
                ("transaction_templates", unrestorable["day_job"], "Day Job",
                 "a salary profile's paycheck that no Received row records"),
                ("transaction_templates", unrestorable["gadget"], "Gadget",
                 "a definition with no stated price"),
                ("transfer_templates", unrestorable["mortgage"], "Mortgage",
                 "a loan payment that follows its loan"),
            ], key=lambda row: (row[0], row[1]))
            assert _copy("transaction_templates", unrestorable["rent"]) == (
                Decimal("1200.00")
            )
            db.session.rollback()

    def test_the_downgrade_refuses_naming_each_and_changes_nothing(
        self, app, db, unrestorable,
    ):
        """It raises before the ``NOT NULL``, and the rollback leaves head.

        The message counts the three, names each with the one line that
        states its copy in a by-hand run, and gives the diagnostic SELECT
        per table; afterwards neither column exists.
        """
        with app.app_context():
            with pytest.raises(RuntimeError, match="NOTHING has been changed") as exc:
                run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.rollback()

            message = str(exc.value)
            assert "X-bp downgrade refused: 3 definition(s)" in message
            for table, key, name in (
                ("transaction_templates", "day_job", "Day Job"),
                ("transaction_templates", "gadget", "Gadget"),
                ("transfer_templates", "mortgage", "Mortgage"),
            ):
                row_id = unrestorable[key]
                assert f"  {table} {row_id} ({name!r})," in message, name
                assert (
                    f"UPDATE budget.{table} SET default_amount = <figure> "
                    f"WHERE id = {row_id};"
                ) in message, name
            assert (
                "UPDATE budget.transaction_templates SET default_amount = "
                f"<figure> WHERE id = {unrestorable['rent']};"
            ) not in message
            assert (
                "  SELECT id, name FROM budget.transaction_templates "
                "WHERE (default_amount >= 0) IS NOT TRUE;\n"
                "  SELECT id, name FROM budget.transfer_templates "
                "WHERE (default_amount > 0) IS NOT TRUE;"
            ) in message
            _assert_head_schema()
