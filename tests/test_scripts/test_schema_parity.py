"""The comparison of the migrated schema with the models' build (finding plan#1858).

``scripts/schema_parity.py`` is what ``scripts/build_test_template.py`` refuses a template by.
Each case here alters the MIGRATED schema of its own clone, inside the test's transaction, the way
a migration that drifts from its model would, and asserts the comparison reports exactly that
object, with both builds' definitions.  Each "before" definition is read off the catalog by a query
of the test's own, so a case grades the comparison rather than echoing it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.extensions import db as _db
from scripts.schema_parity import (
    Drift,
    SchemaDriftError,
    refuse_schema_drift,
    schema_drift,
)


def _constraint_definition(schema: str, table: str, name: str) -> str:
    """Return PostgreSQL's definition of one constraint, read directly."""
    return _db.session.execute(text(
        "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
        "JOIN pg_class c ON c.oid = con.conrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :schema AND c.relname = :table AND con.conname = :name"
    ), {"schema": schema, "table": table, "name": name}).scalar_one()


def _index_definition(schema: str, name: str) -> str:
    """Return PostgreSQL's definition of one index, read directly."""
    return _db.session.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = :schema AND indexname = :name"
    ), {"schema": schema, "name": name}).scalar_one()


def _drift() -> tuple[Drift, ...]:
    """Run the comparison on the test's own connection, where its alterations are."""
    return schema_drift(_db.session.connection(), _db.metadata)


class TestTheTwoBuildsAgree:
    """The migrated template and the models build one schema."""

    def test_a_clone_of_the_template_shows_no_drift(self, app):
        """Nothing differs: the template's stale NOT NULL names and constraint triggers included.

        The clone holds eight NOT NULL constraints still named for a renamed table or column
        (``hysa_params_id_not_null`` on ``interest_params``) and the infrastructure's constraint
        triggers, neither of which a model declares; both kinds are outside the comparison.
        """
        del app
        assert not _drift()


class TestEachKindOfDifferenceIsReported:
    """One alteration of the migrated schema, one report naming it."""

    def test_a_column_drop_that_takes_a_check_with_it(self, app):
        """The finding's own case: the column AND the multi-column CHECK it silently took.

        ``ck_state_child_deductions_agi_order`` reads ``agi_max`` and ``agi_min``; PostgreSQL
        drops it with ``agi_max`` and says nothing, which is how such a migration built green.
        """
        del app
        check = _constraint_definition(
            "salary", "state_child_deductions", "ck_state_child_deductions_agi_order",
        )
        _db.session.execute(text("ALTER TABLE salary.state_child_deductions DROP COLUMN agi_max"))

        assert _drift() == (
            Drift("column", "salary.state_child_deductions.agi_max", None, "numeric(12,2)"),
            Drift(
                "constraint",
                "salary.state_child_deductions.ck_state_child_deductions_agi_order",
                None, check,
            ),
        )

    def test_a_partial_index_whose_predicate_is_inverted(self, app):
        """Same name, same columns, the opposite rows: the predicate is compared."""
        del app
        modelled = _index_definition("ref", "uq_account_types_seeded_name")
        _db.session.execute(text("DROP INDEX ref.uq_account_types_seeded_name"))
        _db.session.execute(text(
            "CREATE UNIQUE INDEX uq_account_types_seeded_name ON ref.account_types (name) "
            "WHERE user_id IS NOT NULL"
        ))
        migrated = _index_definition("ref", "uq_account_types_seeded_name")
        assert migrated != modelled

        assert _drift() == (
            Drift("index", "ref.account_types.uq_account_types_seeded_name", migrated, modelled),
        )

    def test_a_foreign_key_named_apart(self, app):
        """The shape this finding's four keys had: one key, a different name on each side."""
        del app
        definition = _constraint_definition("auth", "users", "fk_users_linked_owner")
        _db.session.execute(text(
            "ALTER TABLE auth.users RENAME CONSTRAINT fk_users_linked_owner "
            "TO users_linked_owner_id_fkey"
        ))

        assert _drift() == (
            Drift("constraint", "auth.users.fk_users_linked_owner", None, definition),
            Drift("constraint", "auth.users.users_linked_owner_id_fkey", definition, None),
        )

    def test_a_column_s_nullability_default_and_collation(self, app):
        """Each part of a column's definition is compared, not only its type."""
        del app
        _db.session.execute(text(
            "ALTER TABLE salary.pay_entries ALTER COLUMN payday DROP NOT NULL"
        ))
        _db.session.execute(text(
            "ALTER TABLE auth.users ALTER COLUMN failed_login_count DROP DEFAULT"
        ))
        _db.session.execute(text(
            "ALTER TABLE ref.account_types ALTER COLUMN name TYPE varchar(30) COLLATE \"C\""
        ))

        assert _drift() == (
            Drift(
                "column", "auth.users.failed_login_count",
                "integer NOT NULL", "integer NOT NULL DEFAULT 0",
            ),
            Drift(
                "column", "ref.account_types.name",
                'character varying(30) COLLATE "C" NOT NULL', "character varying(30) NOT NULL",
            ),
            Drift("column", "salary.pay_entries.payday", "date", "date NOT NULL"),
        )

    def test_a_table_only_the_chain_builds(self, app):
        """A table no model declares is reported with its columns."""
        del app
        _db.session.execute(text("CREATE TABLE budget.stray (id integer)"))

        assert _drift() == (
            Drift("table", "budget.stray", "table", None),
            Drift("column", "budget.stray.id", "integer", None),
        )


class TestWhatIsNotCompared:
    """The two exclusions are deliberate, and each is graded."""

    def test_a_not_null_constraint_s_name(self, app):
        """A NOT NULL's name is PostgreSQL's, not the model's: renaming one is no drift."""
        del app
        _db.session.execute(text(
            "ALTER TABLE budget.transaction_entries RENAME CONSTRAINT "
            "transaction_entries_entry_date_not_null TO any_name_at_all"
        ))
        assert not _drift()

    def test_a_constraint_trigger(self, app):
        """A constraint trigger is the infrastructure's on both paths: adding one is no drift."""
        del app
        _db.session.execute(text(
            "CREATE FUNCTION budget.stray_check() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RETURN NULL; END $$"
        ))
        _db.session.execute(text(
            "CREATE CONSTRAINT TRIGGER stray_trigger AFTER INSERT ON auth.users "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION budget.stray_check()"
        ))
        assert not _drift()


class TestTheComparisonLeavesTheCallerAsItFoundIt:
    """It builds the models in a savepoint it rolls back, and unsets what it set."""

    def test_its_build_is_gone_and_the_caller_s_work_is_not(self, app):
        """No scratch schema survives, the caller's own table does, and its map is restored."""
        del app
        _db.session.execute(text("CREATE TABLE budget.stray (id integer)"))
        connection = _db.session.connection()
        before = connection.get_execution_options().get("schema_translate_map")

        schema_drift(connection, _db.metadata)

        assert _db.session.execute(text(
            "SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'models\\_%'"
        )).scalar_one() == 0
        assert _db.session.execute(text(
            "SELECT to_regclass('budget.stray') IS NOT NULL"
        )).scalar_one() is True
        assert connection.get_execution_options().get("schema_translate_map") == before


class TestTheRefusal:
    """What the template build calls: silent on agreement, every difference on drift."""

    def test_agreement_raises_nothing(self, app):
        """A clean clone passes."""
        del app
        assert refuse_schema_drift(_db.session.connection(), _db.metadata) is None

    def test_drift_raises_naming_every_difference(self, app):
        """The error carries the differences and prints one line for each."""
        del app
        _db.session.execute(text(
            "ALTER TABLE auth.users RENAME CONSTRAINT fk_users_linked_owner "
            "TO users_linked_owner_id_fkey"
        ))

        with pytest.raises(SchemaDriftError) as raised:
            refuse_schema_drift(_db.session.connection(), _db.metadata)

        assert raised.value.drift == _drift()
        assert len(raised.value.drift) == 2
        for item in raised.value.drift:
            assert item.describe() in str(raised.value)
        assert "constraint auth.users.fk_users_linked_owner: the migrations build nothing; " \
            "the models build FOREIGN KEY" in str(raised.value)
