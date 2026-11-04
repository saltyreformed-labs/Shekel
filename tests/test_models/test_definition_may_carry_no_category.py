"""
Shekel Budget App -- a definition may carry no category (balance:X-bi-7b)

Migration ``9c1e4b7a2d3f`` (ruling **R-BAL24**) drops ``NOT NULL`` from
``budget.transaction_templates.category_id`` so the bank door's row for money
nothing can categorise -- ``mint_uncategorized``, ruling **R-FN** -- can be
born as a rule-less DEFINITION plus its row like every other one-off.  Schema
only; no row is written in either direction.

Graded from every side, on this test's own clone:

* the model admits a category-less definition (the state the upgrade exists
  for; that its row prices through it is ``test_one_off``'s case);
* the DOWNGRADE re-adds ``NOT NULL`` and the database then refuses such a
  definition -- so the drop was real and the bind was real;
* the downgrade REFUSES while a category-less definition exists, naming the
  count and the repair, and leaves the column nullable -- the refusal is
  FIRED here rather than left as the one path a green suite never runs;
* the UPGRADE makes the column nullable again.

The migration's two steps are driven through ``_run`` on the session's own
connection, the idiom ``test_template_row_needs_due_date`` established.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy
from alembic import op
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from tests._test_helpers import load_migration_module

_MIGRATION = load_migration_module(
    "9c1e4b7a2d3f_a_definition_may_carry_no_category.py",
)


def _column_is_nullable():
    """Return whether ``transaction_templates.category_id`` admits NULL."""
    return db.session.execute(sqlalchemy.text(
        "SELECT is_nullable = 'YES' FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'transaction_templates' "
        "AND column_name = 'category_id'"
    )).scalar()


def _run(step):
    """Drive one of the migration's steps over this test's own connection."""
    connection = db.session.connection()
    ctx = MigrationContext.configure(connection=connection)
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=connection):
            step()


def _category_less_definition(seed_user, name="POS DEBIT 4417"):
    """Return an UNFLUSHED rule-less definition carrying no category."""
    return TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=None,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
    )


class TestTheColumnAdmitsNull:
    """The upgraded schema, which the test database is baked at."""

    def test_the_column_is_nullable_and_a_category_less_definition_is_stored(
        self, app, seed_user,
    ):
        """The state the migration exists for is storable."""
        with app.app_context():
            assert _column_is_nullable() is True
            definition = _category_less_definition(seed_user)
            db.session.add(definition)
            db.session.commit()
            assert db.session.get(
                TransactionTemplate, definition.id,
            ).category_id is None


class TestTheMigrationRoundTrips:
    """``9c1e4b7a2d3f``'s two steps, driven over this test's own clone."""

    def test_the_downgrade_binds_not_null_and_the_upgrade_lifts_it(
        self, app, seed_user,
    ):
        """Down: NOT NULL bound and a NULL category refused.  Up: admitted again."""
        with app.app_context():
            assert _column_is_nullable() is True

            _run(_MIGRATION.downgrade)
            db.session.commit()
            assert _column_is_nullable() is False

            db.session.add(_category_less_definition(seed_user))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "category_id" in str(exc.value)
            db.session.rollback()

            _run(_MIGRATION.upgrade)
            db.session.commit()
            assert _column_is_nullable() is True

            db.session.add(_category_less_definition(seed_user))
            db.session.commit()

    def test_the_downgrade_refuses_while_a_category_less_definition_exists(
        self, app, seed_user,
    ):
        """The refusal FIRES, names the count and the repair, and binds nothing."""
        with app.app_context():
            db.session.add(_category_less_definition(seed_user, "One"))
            db.session.add(_category_less_definition(seed_user, "Two"))
            db.session.commit()

            with pytest.raises(RuntimeError) as exc:
                _run(_MIGRATION.downgrade)
            message = str(exc.value)
            assert message.startswith("2 budget.transaction_templates row(s)")
            assert "cutover downgrade" in message
            assert "categorise them by hand" in message
            db.session.rollback()
            assert _column_is_nullable() is True

    def test_the_upgrade_is_idempotent_on_an_already_nullable_column(
        self, app,
    ):
        """Running the upgrade over the upgraded schema changes nothing."""
        with app.app_context():
            _run(_MIGRATION.upgrade)
            db.session.commit()
            assert _column_is_nullable() is True


class TestTheMigrationIsSchemaOnly:
    """No row is written in either direction; the revision chain is one step."""

    def test_the_revision_names_its_parent_and_no_data_statement(self):
        """Structural: one ALTER TABLE per direction, one refusal, no UPDATE or INSERT."""
        import inspect  # pylint: disable=import-outside-toplevel

        source = inspect.getsource(_MIGRATION)
        assert _MIGRATION.revision == "9c1e4b7a2d3f"
        assert _MIGRATION.down_revision
        body = source[source.index("def upgrade"):]
        assert "op.alter_column" in body
        assert "UPDATE " not in body and "INSERT " not in body
        assert "COUNT(*)" in source
