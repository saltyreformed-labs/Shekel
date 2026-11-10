"""Tests for the 5f2c46a35a79 "the per-user tax tables go" migration.

Plan step **salary:X-at-2** (card plan#1142; rulings **plan#3210**,
**plan#3211**).  The revision refuses while a stored row of the five per-user
tax tables is not a row of the law as plan step salary:X-at-1 left it, then
drops the five and ``ref.tax_types``; the same change deletes their models,
the ``TaxType`` model, the ref cache's ``tax_type_id`` and the five
``AUDITED_TABLES`` rows.

**Both directions are EXECUTED here.**  The ``db`` fixture re-clones this
worker's own database for every test, so the DDL reaches no other worker and
survives no other case: each refusal case runs the downgrade (the tables back,
empty), stores the rows it grades, then runs the real upgrade.

The rows stored are the law's own, written from the revision's frozen record
(:func:`_store_the_law`), and each refusal case changes one of them the way a
Settings edit could have.  The frozen record itself is graded against figures
read off the published sources by hand (:class:`TestTheFrozenLaw`), never
against ``app.tax_law``, which plan step salary:X-at-7 changes on purpose.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app import models, ref_cache
from app.audit_infrastructure import AUDITED_TABLES
from tests._test_helpers import load_migration_module
from tests.test_models._production_tax_tables_2026_11_10 import (
    PRODUCTION_COLUMNS,
    PRODUCTION_CONSTRAINTS,
    PRODUCTION_INDEXES,
)

_MIGRATION = load_migration_module("5f2c46a35a79_the_per_user_tax_tables_go.py")

#: The five salary tables, schema-qualified, then ``ref.tax_types``.
_SALARY_TABLES = tuple(f"salary.{table}" for table in _MIGRATION.TABLES)
_ALL_TABLES = (*_SALARY_TABLES, "ref.tax_types")


def _run(session, direction: str) -> None:
    """Execute the revision's own ``upgrade`` or ``downgrade`` on *session*'s connection.

    Installs Alembic's operation proxy over the test's connection, so the
    migration's module-level ``op`` calls run inside the per-test transaction.

    Args:
        session: The test session.
        direction: ``"upgrade"`` or ``"downgrade"``.
    """
    context = MigrationContext.configure(session.connection())
    with Operations.context(context):
        getattr(_MIGRATION, direction)()


def _exists(session, table: str) -> bool:
    """Return whether the schema-qualified *table* exists."""
    return session.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table},
    ).scalar_one()


def _counts(session) -> dict[str, int]:
    """Return each of the five tables' row count."""
    return {
        table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in _SALARY_TABLES
    }


def _store_the_law(session, user_id: int) -> dict[str, int]:
    """Store every row of the frozen law for *user_id*, as signup once copied it.

    Args:
        session: The test session, after the downgrade.
        user_id: The owner of the rows.

    Returns:
        The id of each bracket set, keyed ``"<year> <status>"``, so a case can
        reach one ladder's rungs.
    """
    set_ids = {}
    for (year, status), (deduction, child, other, cap, ladder) in _MIGRATION.FEDERAL.items():
        set_id = session.execute(text(
            "INSERT INTO salary.tax_bracket_sets (user_id, filing_status_id, tax_year, "
            " standard_deduction, child_credit_amount, other_dependent_credit_amount, "
            " child_credit_refundable_cap, description) "
            "VALUES (:u, (SELECT id FROM ref.filing_statuses WHERE name = :status), :year, "
            " :deduction, :child, :other, :cap, :label) RETURNING id"
        ), {
            "u": user_id, "status": status, "year": year, "deduction": deduction,
            "child": child, "other": other, "cap": cap, "label": f"{year} Federal",
        }).scalar_one()
        set_ids[f"{year} {status}"] = set_id
        for position, (floor, ceiling, rate) in enumerate(ladder):
            session.execute(text(
                "INSERT INTO salary.tax_brackets "
                "(bracket_set_id, min_income, max_income, rate, sort_order) "
                "VALUES (:s, :floor, :ceiling, :rate, :position)"
            ), {
                "s": set_id, "floor": floor, "ceiling": ceiling, "rate": rate,
                "position": position,
            })
    for year, figures in _MIGRATION.FICA.items():
        session.execute(text(
            "INSERT INTO salary.fica_configs (user_id, tax_year, ss_rate, ss_wage_base, "
            " medicare_rate, medicare_surtax_rate, medicare_surtax_threshold) "
            "VALUES (:u, :year, :a, :b, :c, :d, :e)"
        ), {"u": user_id, "year": year, **dict(zip("abcde", figures))})
    for (year, state), (tax_type, rate) in _MIGRATION.STATES.items():
        for status, deduction in _MIGRATION.NC_STANDARD_DEDUCTION.items():
            _store_state_row(session, user_id, state, year, status, tax_type, rate, deduction)
        for status, tiers in _MIGRATION.NC_CHILD_TIERS.items():
            for floor, ceiling, per_child in tiers:
                _store_tier(session, user_id, state, year, status, (floor, ceiling, per_child))
    return set_ids


def _store_state_row(session, user_id, state, year, status, tax_type, rate, deduction) -> int:
    """Store one ``state_tax_configs`` row, its status and type by name; return its id."""
    return session.execute(text(
        "INSERT INTO salary.state_tax_configs (user_id, tax_type_id, state_code, tax_year, "
        " flat_rate, standard_deduction, filing_status_id) "
        "VALUES (:u, (SELECT id FROM ref.tax_types WHERE name = :tax_type), :state, :year, "
        " :rate, :deduction, (SELECT id FROM ref.filing_statuses WHERE name = :status)) "
        "RETURNING id"
    ), {
        "u": user_id, "tax_type": tax_type, "state": state, "year": year, "rate": rate,
        "deduction": deduction, "status": status,
    }).scalar_one()


def _store_tier(session, user_id, state, year, status, tier) -> int:
    """Store one ``state_child_deductions`` tier ``(floor, ceiling, per child)``; return its id."""
    floor, ceiling, per_child = tier
    return session.execute(text(
        "INSERT INTO salary.state_child_deductions (filing_status_id, state_code, tax_year, "
        " agi_min, agi_max, deduction_per_child, user_id) "
        "VALUES ((SELECT id FROM ref.filing_statuses WHERE name = :status), :state, :year, "
        " :floor, :ceiling, :per_child, :u) RETURNING id"
    ), {
        "status": status, "state": state, "year": year, "floor": floor, "ceiling": ceiling,
        "per_child": per_child, "u": user_id,
    }).scalar_one()


def _rung_id(session, set_id: int, position: int) -> int:
    """Return the id of the rung at *position* of the ladder *set_id*."""
    return session.execute(text(
        "SELECT id FROM salary.tax_brackets WHERE bracket_set_id = :s AND sort_order = :p"
    ), {"s": set_id, "p": position}).scalar_one()


class TestChaining:
    """The revision sits where it claims in the Alembic chain, and is reviewed."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "5f2c46a35a79"
        assert _MIGRATION.down_revision == "0fe9c332b9b0"

    def test_it_is_reviewed(self):
        """A destructive migration carries the ``Review:`` line, naming its two rulings.

        ``.claude/rules/database.md``: a migration that drops needs developer
        approval recorded in the module docstring.
        """
        assert "\nReview: developer, 2026-11-10 (rulings plan#3210 and plan#3211" in (
            _MIGRATION.__doc__
        )


class TestTheFrozenLaw:
    """The revision's copy of the law as X-at-1 left it, graded figure by figure.

    Each expected figure is read off its published source, not off the record
    or ``app.tax_law``: the 2026 head-of-household boundary is the figure the
    app seeded ($201,775), which plan step salary:X-at-7 corrects in the code
    and must not correct here.
    """

    def test_each_table_has_the_law_s_row_count(self):
        """8 bracket sets (2 years x 4 statuses), 56 rungs (8 x 7), 8 state rows, 2 FICA, 56 tiers."""
        assert {table: len(rows) for table, rows in _MIGRATION.law_rows().items()} == {
            "tax_bracket_sets": 8, "tax_brackets": 56, "state_tax_configs": 8,
            "fica_configs": 2, "state_child_deductions": 56,
        }

    def test_the_figures_the_app_seeded(self):
        """Spot figures across every table, both years and an edge of each ladder."""
        law = _MIGRATION.law_rows()
        assert (
            2025, "single", Decimal("15000.00"), Decimal("2200.00"), Decimal("500.00"),
            Decimal("1700.00"),
        ) in law["tax_bracket_sets"]
        assert (
            2026, "head_of_household", 3, Decimal("105700.00"), Decimal("201775.00"),
            Decimal("0.2400"),
        ) in law["tax_brackets"]
        assert (
            2026, "married_jointly", 6, Decimal("768700.00"), None, Decimal("0.3700"),
        ) in law["tax_brackets"]
        assert (
            "NC", 2025, "head_of_household", "flat", Decimal("0.0425"), Decimal("19125.00"),
        ) in law["state_tax_configs"]
        assert (
            2026, Decimal("0.0620"), Decimal("184500.00"), Decimal("0.0145"), Decimal("0.0090"),
            Decimal("200000.00"),
        ) in law["fica_configs"]
        assert (
            "NC", 2026, "married_separately", Decimal("70000.00"), None, Decimal("0.00"),
        ) in law["state_child_deductions"]


class TestTheTablesAreGoneAtHead:
    """At head the six tables, their triggers, their audited rows and their code are gone."""

    @pytest.mark.parametrize("table", _ALL_TABLES)
    def test_the_table_does_not_exist(self, db, table):
        """The upgrade's whole point, against the live catalogue."""
        assert _exists(db.session, table) is False

    def test_no_audit_trigger_is_named_for_them(self, db):
        """No trigger anywhere carries one of the five tables' audit trigger names."""
        found = db.session.execute(text(
            "SELECT count(*) FROM pg_trigger WHERE tgname = ANY(:names)"
        ), {"names": [f"audit_{table}" for table in _MIGRATION.TABLES]}).scalar_one()
        assert found == 0

    def test_the_audited_list_does_not_name_them(self):
        """``AUDITED_TABLES`` lost the five rows, so the trigger census expects five fewer."""
        assert not {("salary", table) for table in _MIGRATION.TABLES} & set(AUDITED_TABLES)

    @pytest.mark.parametrize("name", (
        "TaxBracketSet", "TaxBracket", "StateTaxConfig", "FicaConfig", "StateChildDeduction",
        "TaxType",
    ))
    def test_the_model_is_gone(self, name):
        """Each table's model went with it."""
        assert not hasattr(models, name)

    def test_the_ref_cache_names_no_tax_type(self):
        """The cache's accessor went with the table it read."""
        assert not hasattr(ref_cache, "tax_type_id")


class TestTheDowngradeRecreatesThemEmpty:
    """The downgrade brings the six back in production's shape (ruling plan#3211)."""

    @pytest.mark.parametrize("table", _ALL_TABLES)
    def test_the_columns_are_production_s(self, db, table):
        """Each column in production's order, with its type, nullability and default."""
        _run(db.session, "downgrade")
        schema, name = table.split(".")
        columns = db.session.execute(text(
            "SELECT column_name, data_type, numeric_precision, numeric_scale, "
            "       character_maximum_length, is_nullable, column_default "
            "  FROM information_schema.columns "
            " WHERE table_schema = :schema AND table_name = :name ORDER BY ordinal_position"
        ), {"schema": schema, "name": name}).all()
        assert [tuple(row) for row in columns] == PRODUCTION_COLUMNS[table]

    @pytest.mark.parametrize("table", _ALL_TABLES)
    def test_the_constraints_are_production_s(self, db, table):
        """Every key and CHECK, by production's names and definitions."""
        _run(db.session, "downgrade")
        constraints = db.session.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            " WHERE conrelid = CAST(:t AS regclass) AND contype <> 'n' ORDER BY conname"
        ), {"t": table}).all()
        assert [tuple(row) for row in constraints] == PRODUCTION_CONSTRAINTS[table]

    @pytest.mark.parametrize("table", _ALL_TABLES)
    def test_the_indexes_are_production_s(self, db, table):
        """Every index, by production's names and definitions."""
        _run(db.session, "downgrade")
        schema, name = table.split(".")
        indexes = db.session.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            " WHERE schemaname = :schema AND tablename = :name ORDER BY indexname"
        ), {"schema": schema, "name": name}).all()
        assert [tuple(row) for row in indexes] == PRODUCTION_INDEXES[table]

    def test_the_tax_types_come_back_by_name(self, db):
        """The three names the previous image's ref cache reads, and nothing else."""
        _run(db.session, "downgrade")
        names = db.session.execute(text("SELECT name FROM ref.tax_types")).scalars().all()
        assert sorted(names) == ["bracket", "flat", "none"]

    def test_the_five_come_back_empty_and_audited(self, db):
        """No row is restored, and each carries production's audit trigger again.

        The previous image refuses a deploy while a table its ``AUDITED_TABLES``
        names lacks its trigger, so the downgrade attaches them.
        """
        _run(db.session, "downgrade")
        triggers = db.session.execute(text(
            "SELECT c.relname, t.tgname, p.proname, pg_get_triggerdef(t.oid) "
            "  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            "  JOIN pg_proc p ON p.oid = t.tgfoid "
            " WHERE n.nspname = 'salary' AND c.relname = ANY(:tables) AND NOT t.tgisinternal "
            " ORDER BY c.relname"
        ), {"tables": list(_MIGRATION.TABLES)}).all()
        assert _counts(db.session) == dict.fromkeys(_SALARY_TABLES, 0)
        assert [(row[0], row[1], row[2]) for row in triggers] == [
            (table, f"audit_{table}", "audit_trigger_func")
            for table in sorted(_MIGRATION.TABLES)
        ]
        assert all(
            "AFTER INSERT OR DELETE OR UPDATE" in row[3] and "FOR EACH ROW" in row[3]
            for row in triggers
        )

    def test_the_upgrade_runs_again_after_it(self, db, capsys):
        """Down then up: empty tables pass, and all six go again."""
        _run(db.session, "downgrade")
        _run(db.session, "upgrade")
        printed = capsys.readouterr().out
        assert not any(_exists(db.session, table) for table in _ALL_TABLES)
        assert (
            "X-at-2: every stored row of the five per-user tax tables is a row of the law "
            "as X-at-1 left it: 0 in salary.tax_bracket_sets, 0 in salary.tax_brackets, "
            "0 in salary.state_tax_configs, 0 in salary.fica_configs, "
            "0 in salary.state_child_deductions."
        ) in printed


class TestTheLawsOwnRowsPass:
    """The rows signup copied, unchanged, are dropped (production's case, measured 2026-11-10)."""

    def test_two_users_full_copies_pass_and_go(self, db, seed_user, seed_second_user, capsys):
        """Two users' every row of both years: the upgrade passes and drops them.

        Production held this shape: 260 rows, two users' 2025 and 2026.
        """
        _run(db.session, "downgrade")
        _store_the_law(db.session, seed_user["user"].id)
        _store_the_law(db.session, seed_second_user["user"].id)
        assert _counts(db.session) == {
            "salary.tax_bracket_sets": 16, "salary.tax_brackets": 112,
            "salary.state_tax_configs": 16, "salary.fica_configs": 4,
            "salary.state_child_deductions": 112,
        }
        _run(db.session, "upgrade")
        assert not any(_exists(db.session, table) for table in _ALL_TABLES)
        assert (
            "16 in salary.tax_bracket_sets, 112 in salary.tax_brackets, "
            "16 in salary.state_tax_configs, 4 in salary.fica_configs, "
            "112 in salary.state_child_deductions."
        ) in capsys.readouterr().out

    def test_a_row_the_law_has_and_a_table_lacks_refuses_nothing(self, db, seed_user):
        """A missing rung and a missing tier throw no figure away."""
        _run(db.session, "downgrade")
        set_ids = _store_the_law(db.session, seed_user["user"].id)
        db.session.execute(text("DELETE FROM salary.tax_brackets WHERE id = :id"), {
            "id": _rung_id(db.session, set_ids["2026 single"], 6),
        })
        db.session.execute(text(
            "DELETE FROM salary.state_child_deductions WHERE agi_max IS NULL"
        ))
        _run(db.session, "upgrade")
        assert not _exists(db.session, "salary.tax_brackets")


class TestTheRefusal:
    """Ruling plan#3210: no drop while a stored row is not a row of the law X-at-1 left.

    Each case stores the whole law for the seeded user, changes one row the
    way a Settings edit could have, and runs the real upgrade.  A refusal
    writes nothing: every table and every row is still there.
    """

    @staticmethod
    def _refusal(session) -> str:
        """Run the upgrade, which must refuse; return its message after checking nothing moved."""
        before = _counts(session)
        with pytest.raises(RuntimeError) as refused:
            _run(session, "upgrade")
        assert all(_exists(session, table) for table in _ALL_TABLES)
        assert _counts(session) == before
        message = str(refused.value)
        assert message.startswith("X-at-2 refuses: 1 stored row(s) of the five per-user tax ")
        assert message.endswith("Nothing was written: the revision rolls back whole.")
        return message

    def test_a_hand_typed_correct_figure_refuses(self, db, seed_user):
        """The ruling's example: 2026 head-of-household's 24% rung typed as $201,750.

        The IRS figure, and the one X-at-7 gives the code, but not one the app
        seeded, so it may be someone's and is not thrown away unseen (ruling
        plan#3210's correction to its question).
        """
        _run(db.session, "downgrade")
        user_id = seed_user["user"].id
        set_ids = _store_the_law(db.session, user_id)
        rung = _rung_id(db.session, set_ids["2026 head_of_household"], 3)
        db.session.execute(text(
            "UPDATE salary.tax_brackets SET max_income = 201750.00 WHERE id = :id"
        ), {"id": rung})
        assert (
            f"salary.tax_brackets row {rung} (user {user_id}): tax_year 2026, "
            "filing_status head_of_household, sort_order 3, min_income 105700.00, "
            "max_income 201750.00, rate 0.2400"
        ) in self._refusal(db.session)

    def test_a_changed_standard_deduction_refuses(self, db, seed_user):
        """2025 single's $15,000.00 changed to $15,750.00 on the bracket set."""
        _run(db.session, "downgrade")
        user_id = seed_user["user"].id
        set_ids = _store_the_law(db.session, user_id)
        db.session.execute(text(
            "UPDATE salary.tax_bracket_sets SET standard_deduction = 15750.00 WHERE id = :id"
        ), {"id": set_ids["2025 single"]})
        assert (
            f"salary.tax_bracket_sets row {set_ids['2025 single']} (user {user_id}): "
            "tax_year 2025, filing_status single, standard_deduction 15750.00"
        ) in self._refusal(db.session)

    def test_an_extra_rung_refuses(self, db, seed_user):
        """An eighth rung on 2026 single's seven-rung ladder."""
        _run(db.session, "downgrade")
        set_ids = _store_the_law(db.session, seed_user["user"].id)
        db.session.execute(text(
            "INSERT INTO salary.tax_brackets (bracket_set_id, min_income, max_income, rate, "
            " sort_order) VALUES (:s, 900000.00, NULL, 0.3900, 7)"
        ), {"s": set_ids["2026 single"]})
        assert "sort_order 7, min_income 900000.00, max_income None, rate 0.3900" in (
            self._refusal(db.session)
        )

    def test_a_year_the_law_never_had_refuses(self, db, seed_user):
        """A 2024 FICA row: a year the app never carried."""
        _run(db.session, "downgrade")
        user_id = seed_user["user"].id
        _store_the_law(db.session, user_id)
        db.session.execute(text(
            "INSERT INTO salary.fica_configs (user_id, tax_year) VALUES (:u, 2024)"
        ), {"u": user_id})
        assert (
            f"(user {user_id}): tax_year 2024, ss_rate 0.0620, ss_wage_base 176100.00"
        ) in self._refusal(db.session)

    def test_a_state_the_law_never_had_refuses(self, db, seed_user):
        """A South Carolina row: a state the app never carried."""
        _run(db.session, "downgrade")
        user_id = seed_user["user"].id
        _store_the_law(db.session, user_id)
        row = _store_state_row(
            db.session, user_id, "SC", 2026, "single", "flat", "0.0620", "14600.00",
        )
        assert (
            f"salary.state_tax_configs row {row} (user {user_id}): state_code SC, "
            "tax_year 2026, filing_status single, tax_type flat, flat_rate 0.0620, "
            "standard_deduction 14600.00"
        ) in self._refusal(db.session)

    def test_a_filing_status_the_law_never_had_refuses(self, db, seed_user):
        """A tier under a fifth filing status the law does not model."""
        _run(db.session, "downgrade")
        user_id = seed_user["user"].id
        _store_the_law(db.session, user_id)
        db.session.execute(text(
            "INSERT INTO ref.filing_statuses (name) VALUES ('qualifying_widow')"
        ))
        _store_tier(
            db.session, user_id, "NC", 2026, "qualifying_widow",
            ("0.00", "40000.00", "3000.00"),
        )
        assert "filing_status qualifying_widow, agi_min 0.00" in (
            self._refusal(db.session)
        )

    def test_a_changed_tax_type_refuses(self, db, seed_user):
        """North Carolina 2025 single stored as a bracket state."""
        _run(db.session, "downgrade")
        user_id = seed_user["user"].id
        _store_the_law(db.session, user_id)
        db.session.execute(text(
            "UPDATE salary.state_tax_configs "
            "   SET tax_type_id = (SELECT id FROM ref.tax_types WHERE name = 'bracket') "
            " WHERE tax_year = 2025 AND filing_status_id = "
            "       (SELECT id FROM ref.filing_statuses WHERE name = 'single')"
        ))
        assert "tax_year 2025, filing_status single, tax_type bracket, flat_rate 0.0425" in (
            self._refusal(db.session)
        )

    def test_a_changed_child_deduction_refuses(self, db, seed_user):
        """2026 joint filers' lowest tier given $3,500.00 a child."""
        _run(db.session, "downgrade")
        user_id = seed_user["user"].id
        _store_the_law(db.session, user_id)
        db.session.execute(text(
            "UPDATE salary.state_child_deductions SET deduction_per_child = 3500.00 "
            " WHERE tax_year = 2026 AND agi_min = 0 AND filing_status_id = "
            "       (SELECT id FROM ref.filing_statuses WHERE name = 'married_jointly')"
        ))
        assert (
            "state_code NC, tax_year 2026, filing_status married_jointly, agi_min 0.00, "
            "agi_max 40000.00, deduction_per_child 3500.00"
        ) in self._refusal(db.session)

    def test_a_changed_fica_figure_refuses(self, db, seed_user):
        """2026's Social Security wage base stored as the 2025 figure."""
        _run(db.session, "downgrade")
        user_id = seed_user["user"].id
        _store_the_law(db.session, user_id)
        db.session.execute(text(
            "UPDATE salary.fica_configs SET ss_wage_base = 176100.00 WHERE tax_year = 2026"
        ))
        assert "tax_year 2026, ss_rate 0.0620, ss_wage_base 176100.00" in (
            self._refusal(db.session)
        )

    def test_the_second_owner_s_row_is_named_with_its_owner(
        self, db, seed_user, seed_second_user,
    ):
        """The comparison reads every user's rows: the second owner's edit refuses too."""
        _run(db.session, "downgrade")
        _store_the_law(db.session, seed_user["user"].id)
        other = seed_second_user["user"].id
        set_ids = _store_the_law(db.session, other)
        db.session.execute(text(
            "UPDATE salary.tax_bracket_sets SET child_credit_amount = 2000.00 WHERE id = :id"
        ), {"id": set_ids["2025 married_jointly"]})
        assert (
            f"salary.tax_bracket_sets row {set_ids['2025 married_jointly']} (user {other}):"
        ) in self._refusal(db.session)

    def test_every_differing_row_is_named(self, db, seed_user):
        """Two edits in two tables: both named, and the count says two."""
        _run(db.session, "downgrade")
        user_id = seed_user["user"].id
        set_ids = _store_the_law(db.session, user_id)
        db.session.execute(text(
            "UPDATE salary.tax_bracket_sets SET standard_deduction = 15750.00 WHERE id = :id"
        ), {"id": set_ids["2025 single"]})
        db.session.execute(text(
            "UPDATE salary.fica_configs SET ss_rate = 0.0630 WHERE tax_year = 2025"
        ))
        with pytest.raises(RuntimeError) as refused:
            _run(db.session, "upgrade")
        message = str(refused.value)
        assert message.startswith("X-at-2 refuses: 2 stored row(s)")
        assert "standard_deduction 15750.00" in message
        assert "ss_rate 0.0630" in message
