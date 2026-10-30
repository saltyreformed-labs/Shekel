"""Tests for the C-43 migration ``b4b588a49a0c``.

The migration closes audit findings F-073, F-078, and F-136 of the
2026-04-15 security remediation plan via two DDL operation groups:

  1. **Ref-FK ondelete sweep (F-073).**  Nine FKs that reference
     ``ref.*`` lookup tables defaulted to PostgreSQL implicit
     ``NO ACTION``; the migration drops and recreates each with the
     explicit ``ondelete="RESTRICT"`` clause the coding standards
     require, and (for seven of the nine) advances the constraint
     name from ``<table>_<column>_fkey`` to
     ``fk_<table>_<column_0_name>`` to match the
     ``SHEKEL_NAMING_CONVENTION`` documented in
     ``app/extensions.py``.
  2. **transfers.pay_period_id realignment (F-136).**  Drops
     ``transfers_pay_period_id_fkey`` (RESTRICT) and recreates it as
     ``fk_transfers_pay_period_id`` (CASCADE), matching the sibling
     ``transactions.pay_period_id`` and
     ``account_anchor_history.pay_period_id`` FKs.

The companion ``SHEKEL_NAMING_CONVENTION`` in ``app/extensions.py``
documents the naming pattern but does NOT install it on
``db.metadata``.  Doing so would break the chain-replay path used
by ``scripts/build_test_template.py`` because the convention would
cause un-named ``sa.ForeignKeyConstraint`` calls in pre-C-43
migrations to render new constraint names that subsequent
migrations could not drop by their original dialect-default names.
Forward enforcement is therefore manual: every new constraint in a
model carries an explicit ``name=`` shaped by the convention.  The
``TestNamingConventionContract`` class below asserts the
explicit-name rule for every FK touched by this commit.

The test suite exercises six invariants:

  1. **Post-upgrade DB shape.**  Every FK in ``REF_FK_SPECS`` carries
     ``ondelete=RESTRICT`` under the new name; the
     ``TRANSFERS_PAY_PERIOD_SPEC`` FK carries ``ondelete=CASCADE``
     under ``fk_transfers_pay_period_id``.
  2. **Cross-table pay_period_id consistency.**  All three child
     tables that reference ``budget.pay_periods.id``
     (``transactions``, ``transfers``, ``account_anchor_history``)
     resolve to ``ondelete=CASCADE`` after C-43.
  3. **Behavioral RESTRICT enforcement.**  Attempting to delete a
     ``ref.*`` row that is referenced by a budget or salary row
     raises ``IntegrityError`` immediately -- the behavioural proof
     that ``RESTRICT`` is doing the job ``NO ACTION`` previously
     deferred to commit time.
  4. **Idempotency.**  Running ``upgrade()`` against the post-C-43
     DB is a strict no-op: the FK recreate helper short-circuits
     (no DDL issued) and every artifact remains in place.
  5. **Recreate-on-drift.**  Reverting an individual artifact to its
     pre-C-43 form and re-running ``upgrade()`` restores it -- the
     production-replay invariant that lets operators recover from
     partial-apply states.
  6. **Downgrade round-trip.**  ``downgrade()`` reverses every change
     symmetrically: legacy ondelete restored, legacy FK names
     restored.  A second ``upgrade()`` after the downgrade leaves
     the DB in the same post-C-43 state.

Two additional defensive tests guard the surrounding contracts:

  7. **Naming convention contract.**  The model FK declarations for
     every constraint touched by C-43 carry an explicit ``name=``
     argument that matches the
     ``fk_<table>_<column>`` convention -- the manual forward
     enforcement mechanism that replaces the global
     ``MetaData.naming_convention`` setup the plan originally
     proposed (see the migration docstring for the chain-replay
     rationale).
  8. **Helper return-value contract.**  The migration's
     ``_drop_and_recreate_fk`` and ``_assert_fk_ondelete`` helpers
     return True iff DDL ran (or raise on mismatch).

Tests bootstrap an Alembic ``MigrationContext`` against the test
session's connection so the migration's ``op.execute`` and the
``op.get_bind`` proxy resolve to real DDL through Alembic's
Operations proxy.  Mirrors the C-42 round-trip pattern (see
``tests/test_models/test_c42_salary_indexes_and_fk_naming.py``).

Audit reference: F-073, F-078, F-136 of
shekel-audits/security-2026-04-15/findings.md; commit C-43 of
shekel-audits/security-2026-04-15/remediation-plan.md.
"""
# pylint: disable=redefined-outer-name,unused-argument
# pylint: disable=protected-access
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; tests in this file declare local fixtures and consume them
# under the same names.  ``unused-argument`` is unavoidable for
# fixture-only requests like ``seed_user`` that ensure conftest
# fixtures have produced a populated DB but whose return values the
# assertion does not depend on.  ``protected-access`` is required to
# call the migration's helper functions (prefixed with a leading
# underscore by convention to mark them as private to the migration
# module); the tests exercise these directly to assert their return-
# value contracts.
from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import patch

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests._test_helpers import rewind_paycheck_lines_rename

from app.extensions import SHEKEL_NAMING_CONVENTION
from app.models.paycheck_line import PaycheckLine
from app.models.ref import AccountType
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.savings_goal import SavingsGoal
from app.models.tax_config import StateTaxConfig, TaxBracketSet
from app.models.transfer import Transfer


# ---------------------------------------------------------------------------
# The schema this revision was written against
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _c43_schema(app, db):
    """Run every case here under plan step ``salary:R18-a``'s downgrade.

    Two of C-43's ``REF_FK_SPECS`` live on ``salary.paycheck_deductions``,
    which head renamed to ``salary.paycheck_lines`` with the kind FK's name
    (ruling **R-SAL38**); the specs are read straight off the migration
    module, so the DB-shape cases would look for names the head template
    no longer carries and ``upgrade()`` would alter a table that is not
    there.  :func:`~tests._test_helpers.rewind_paycheck_lines_rename` puts
    the old names back first (the stacked, newest-first shape of
    :func:`~tests._test_helpers.rewind_pay_schedule_rhythm`); every case
    then reads and drives C-43 against the schema it was written for.  The
    model-contract cases read metadata only and are unaffected.  Nothing
    is replayed: the ``db`` fixture re-clones the per-worker database for
    every test, and no case here reads a line through the models.
    """
    with app.app_context():
        rewind_paycheck_lines_rename(db.session)


# ---------------------------------------------------------------------------
# Migration module loader
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename: str):
    """Load an Alembic migration file as a Python module via importlib.

    The ``migrations/versions`` directory has no ``__init__.py`` so
    a standard ``import`` would fail; alembic itself loads scripts
    via importlib at runtime.  We mirror that behaviour to access
    module-level constants (``REF_FK_SPECS``,
    ``TRANSFERS_PAY_PERIOD_SPEC``), helper functions, and the
    ``upgrade`` / ``downgrade`` callables directly.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_C43 = _load_migration(
    "b4b588a49a0c_c43_fk_ondelete_restrict_and_naming_.py"
)


# Mapping of (model class, column attribute name) for every FK that
# C-43 touched.  Used by ``TestNamingConventionContract`` to assert
# every model declaration carries the convention-matching
# ``name=`` argument.  Keyed by the same string the post-upgrade DB
# carries so the test can also report the matching DB constraint
# name on failure.
#
# Since plan step ``salary:R18-a`` (ruling **R-SAL38**) two of the FKs C-43
# named live on a table it renamed -- ``salary.paycheck_deductions`` is
# ``salary.paycheck_lines`` and its kind column ``paycheck_line_kind_id`` --
# so each row carries a FOURTH element: the name C-43's ``REF_FK_SPECS``
# wrote, which is the model's own name for every row but those two.
MODEL_FK_NAME_CONTRACT: tuple[tuple[type, str, str, str], ...] = (
    (AccountType, "category_id",
     "fk_account_types_category_id", "fk_account_types_category_id"),
    (SavingsGoal, "goal_mode_id",
     "fk_savings_goals_goal_mode_id", "fk_savings_goals_goal_mode_id"),
    (SavingsGoal, "income_unit_id",
     "fk_savings_goals_income_unit_id", "fk_savings_goals_income_unit_id"),
    (PaycheckLine, "calc_method_id",
     "fk_paycheck_lines_calc_method_id",
     "fk_paycheck_deductions_calc_method_id"),
    (PaycheckLine, "paycheck_line_kind_id",
     "fk_paycheck_lines_paycheck_line_kind_id",
     "fk_paycheck_deductions_deduction_timing_id"),
    (SalaryProfile, "filing_status_id",
     "fk_salary_profiles_filing_status_id",
     "fk_salary_profiles_filing_status_id"),
    (SalaryRaise, "raise_type_id",
     "fk_salary_raises_raise_type_id", "fk_salary_raises_raise_type_id"),
    (StateTaxConfig, "tax_type_id",
     "fk_state_tax_configs_tax_type_id", "fk_state_tax_configs_tax_type_id"),
    (TaxBracketSet, "filing_status_id",
     "fk_tax_bracket_sets_filing_status_id",
     "fk_tax_bracket_sets_filing_status_id"),
    (Transfer, "pay_period_id",
     "fk_transfers_pay_period_id", "fk_transfers_pay_period_id"),
)


# ---------------------------------------------------------------------------
# Catalog inspection helpers
# ---------------------------------------------------------------------------


def _constraint_exists(session, schema: str, name: str) -> bool:
    """Return True iff the named constraint exists in ``schema``.

    Mirrors the migration's ``_constraint_exists`` helper so the test
    assertions read against the same catalog view the production
    code does.
    """
    return bool(session.execute(text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_constraint cn "
        "  JOIN pg_class c ON c.oid = cn.conrelid "
        "  JOIN pg_namespace n ON n.oid = c.relnamespace "
        "  WHERE cn.conname = :name AND n.nspname = :schema"
        ")"
    ), {"schema": schema, "name": name}).scalar())


def _fk_ondelete_code(session, schema: str, name: str) -> str | None:
    """Return ``pg_constraint.confdeltype`` for the named FK, or None.

    Reads the raw single-character code (``a`` = NO ACTION, ``r`` =
    RESTRICT, ``c`` = CASCADE, ``n`` = SET NULL).  Callers map to
    human-readable form via ``_M_C43._CONFDELTYPE_TO_LABEL`` when
    needed; the raw code is returned here so the test can detect
    "constraint absent" (None) vs "constraint present with action X"
    in a single read.
    """
    return session.execute(text(
        "SELECT cn.confdeltype FROM pg_constraint cn "
        "JOIN pg_class c ON c.oid = cn.conrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE cn.conname = :name AND n.nspname = :schema "
        "AND cn.contype = 'f'"
    ), {"schema": schema, "name": name}).scalar()


def _force_fk_to_legacy_state(
    session,
    source_schema: str,
    source_table: str,
    source_column: str,
    target_schema: str,
    target_table: str,
    target_column: str,
    legacy_name: str,
    new_name: str,
    legacy_ondelete: str,
) -> None:
    """Tear the FK back to its pre-C-43 shape for the recreate-on-drift tests.

    Drops the current (new-name, new-ondelete) constraint and adds
    one under the legacy name with the legacy ondelete clause.  The
    helper is the test-side mirror of
    ``_M_C43._drop_and_recreate_fk`` -- we issue raw DDL here
    instead of calling the helper backwards so the test stays
    independent of the helper's API.

    Args:
        session: SQLAlchemy session bound to the test DB.
        source_schema/source_table/source_column: FK's referencing
            side.
        target_schema/target_table/target_column: FK's referenced
            side.
        legacy_name: Constraint name to install.
        new_name: Current constraint name to drop.
        legacy_ondelete: SQL keyword for the legacy ondelete clause.
    """
    session.execute(text(
        f"ALTER TABLE {source_schema}.{source_table} "
        f"DROP CONSTRAINT IF EXISTS {new_name}"
    ))
    session.execute(text(
        f"ALTER TABLE {source_schema}.{source_table} "
        f"ADD CONSTRAINT {legacy_name} "
        f"FOREIGN KEY ({source_column}) "
        f"REFERENCES {target_schema}.{target_table} ({target_column}) "
        f"ON DELETE {legacy_ondelete}"
    ))


# ---------------------------------------------------------------------------
# Restoration fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_c43_state(db):
    """Ensure every C-43 artifact is restored after the test.

    Several tests in this module force constraints back to their
    pre-C-43 names and ondelete clauses to simulate the production-
    drift state.  This fixture guarantees the post-C-43 state is
    back in place once the test exits, even if the body raised --
    otherwise every subsequent C-43 invariant test running in the
    same per-worker DB would silently pass against a degraded
    schema.

    Cleanup runs the migration's ``upgrade()`` against the current
    state of the DB.  Because the upgrade is idempotent, the
    re-run is a no-op when the test cleaned up its own changes and
    a full restore when it did not (e.g., when the test asserted on
    a partial state and raised before restoring).
    """
    yield
    # pylint: disable=import-outside-toplevel
    from alembic import op
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    db.session.rollback()
    ctx = MigrationContext.configure(connection=db.session.connection())
    with Operations.context(ctx):
        with patch.object(
            op, "get_bind",
            return_value=db.session.connection(),
        ):
            _M_C43.upgrade()
    db.session.commit()


# ---------------------------------------------------------------------------
# 1. Post-upgrade DB shape
# ---------------------------------------------------------------------------


class TestPostUpgradeDbShape:
    """The per-worker test DB carries every C-43 artifact by default.

    The test template runs the migration chain to head when built
    (see ``scripts/build_test_template.py``), so every per-worker
    clone inherits the post-C-43 state.  These tests are the
    template-state precondition for the rest of the suite -- if any
    fail, the template was built without the C-43 migration and
    needs to be rebuilt with
    ``python scripts/build_test_template.py``.
    """

    @pytest.mark.parametrize(
        "spec",
        list(_M_C43.REF_FK_SPECS),
        ids=[spec[7] for spec in _M_C43.REF_FK_SPECS],
    )
    def test_ref_fk_has_restrict_and_new_name(
        self, app, db, seed_user, spec,
    ):
        """Each of the nine ref-FKs carries RESTRICT under the new name.

        Three-step assertion: the new name is present, the catalog's
        confdeltype maps to RESTRICT, and the legacy name is absent
        (when it differs from the new name).  A partial-apply state
        is reported independently so the failure message tells the
        operator which half of the invariant broke.
        """
        (
            _src_schema, _src_table, _src_col,
            _tgt_schema, _tgt_table, _tgt_col,
            legacy_name, new_name, expected_ondelete,
        ) = spec
        source_schema = spec[0]
        with app.app_context():
            new_code = _fk_ondelete_code(db.session, source_schema, new_name)
            assert new_code is not None, (
                f"Post-upgrade FK {source_schema}.{new_name} is missing. "
                f"Rebuild the template via "
                f"`python scripts/build_test_template.py`."
            )
            assert _M_C43._CONFDELTYPE_TO_LABEL[new_code] == expected_ondelete, (
                f"FK {source_schema}.{new_name} has ondelete="
                f"{_M_C43._CONFDELTYPE_TO_LABEL[new_code]!r}, expected "
                f"{expected_ondelete!r}."
            )
            # The savings_goals FKs already used the convention name
            # pre-C-43 (the 4f2d894216ad migration installed them
            # that way); for those, legacy_name == new_name and the
            # "legacy absent" assertion would contradict the "new
            # present" assertion.  Skip the legacy-absent check in
            # that case.
            if legacy_name != new_name:
                assert not _constraint_exists(
                    db.session, source_schema, legacy_name,
                ), (
                    f"Legacy FK {source_schema}.{legacy_name} is still "
                    f"present alongside the new {new_name} -- the C-43 "
                    f"recreate produced a duplicate constraint."
                )

    def test_transfers_pay_period_id_has_cascade_and_new_name(
        self, app, db, seed_user,
    ):
        """transfers.pay_period_id carries CASCADE under fk_transfers_pay_period_id.

        The realignment from RESTRICT to CASCADE is the load-bearing
        F-136 fix; the rename to the convention name is the
        parallel F-078 collateral.  Both must hold post-C-43.
        """
        (
            _src_schema, _src_table, _src_col,
            _tgt_schema, _tgt_table, _tgt_col,
            legacy_name, new_name, expected_ondelete,
        ) = _M_C43.TRANSFERS_PAY_PERIOD_SPEC
        with app.app_context():
            new_code = _fk_ondelete_code(db.session, "budget", new_name)
            assert new_code is not None, (
                f"Post-upgrade FK budget.{new_name} is missing."
            )
            assert _M_C43._CONFDELTYPE_TO_LABEL[new_code] == expected_ondelete, (
                f"FK budget.{new_name} has ondelete="
                f"{_M_C43._CONFDELTYPE_TO_LABEL[new_code]!r}, expected "
                f"{expected_ondelete!r} (CASCADE)."
            )
            assert not _constraint_exists(db.session, "budget", legacy_name), (
                f"Legacy FK budget.{legacy_name} is still present "
                f"alongside the new {new_name}."
            )


# ---------------------------------------------------------------------------
# 2. Cross-table pay_period_id consistency
# ---------------------------------------------------------------------------


class TestPayPeriodIdConsistency:
    """All child tables that reference pay_periods are CASCADE-consistent.

    F-136 documented an asymmetry: ``transactions.pay_period_id`` and
    ``account_anchor_history.pay_period_id`` CASCADEd while
    ``transfers.pay_period_id`` was RESTRICT.  Post-C-43 all three
    converge on CASCADE.  Verifying the invariant against the live
    catalog (rather than the model file) catches catalog drift
    introduced by hand-edits to the DB.

    **``account_anchor_history`` left this set at plan step X-f1c3b, and it
    left by having no FK at all** (ruling R-EO).  Its ``pay_period_id`` column
    is DELETED: a balance assertion is a fact about a bank, and CASCADE meant a
    pay-period operation could destroy it -- measured, a schedule reset deleted
    all 78 of the developer's production assertions and fabricated 9
    replacements.  Two tables reference ``pay_periods`` now and both still
    CASCADE, so the consistency invariant is unchanged for the rows it still
    covers; the case below asserts the third FK is GONE rather than dropping
    it silently, because "the parametrize list got shorter" is not something a
    reader can distinguish from an accident.
    """

    def test_account_anchor_history_has_no_pay_period_fk(self, app, db):
        """A balance assertion carries no pay-period FK at all (ruling R-EO).

        Asserted rather than deleted: this class is the live-catalog guard on
        which children of ``pay_periods`` cascade, so a future migration
        re-adding the column would otherwise re-open the destroy-on-reset path
        with nothing objecting.  The companion behavioural lock is
        ``test_pay_period_reset.TestResetHappyPath
        .test_the_reset_preserves_every_balance_assertion``.
        """
        with app.app_context():
            assert _fk_ondelete_code(
                db.session, "budget",
                "account_anchor_history_pay_period_id_fkey",
            ) is None
            assert "pay_period_id" not in {
                column["name"] for column in inspect(db.engine).get_columns(
                    "account_anchor_history", schema="budget",
                )
            }

    @pytest.mark.parametrize(
        "schema, name",
        [
            ("budget", "transactions_pay_period_id_fkey"),
            ("budget", "fk_transfers_pay_period_id"),
        ],
        ids=["transactions", "transfers"],
    )
    def test_pay_period_id_fk_cascades(
        self, app, db, seed_user, schema, name,
    ):
        """Both surviving child FKs that reference pay_periods.id CASCADE.

        The ``transactions`` FK keeps its pre-C-43 Alembic-default name (the
        F-078 strategy retains ~35 default names rather than churning all of
        them); only the ``transfers`` FK was renamed by C-43.  The ondelete
        invariant applies to both regardless of name.
        """
        with app.app_context():
            code = _fk_ondelete_code(db.session, schema, name)
            assert code is not None, (
                f"FK {schema}.{name} is missing -- "
                f"pay_period_id consistency invariant cannot be "
                f"asserted."
            )
            assert _M_C43._CONFDELTYPE_TO_LABEL[code] == "CASCADE", (
                f"FK {schema}.{name} has ondelete="
                f"{_M_C43._CONFDELTYPE_TO_LABEL[code]!r}, expected "
                f"CASCADE.  F-136 invariant broken."
            )


# ---------------------------------------------------------------------------
# 3. Behavioural RESTRICT enforcement
# ---------------------------------------------------------------------------


class TestRestrictBehavior:
    """RESTRICT is doing the job NO ACTION previously deferred to commit.

    The audit-level distinction between NO ACTION and RESTRICT is
    timing -- NO ACTION defers the check to end-of-transaction;
    RESTRICT fires on the offending statement.  Both refuse the
    violating delete eventually.  These tests assert the
    catalog-level behavior is "delete refused" without depending on
    the exact statement that triggers the failure, so the assertions
    survive a hypothetical future migration that changes NO ACTION
    back to RESTRICT or vice versa.

    Note: the rollback / SAVEPOINT pattern is mandatory because a
    failed FK violation poisons the outer transaction; without an
    explicit rollback the conftest teardown's TRUNCATE pass would
    inherit the failed transaction and skip the truncation.
    """

    def test_delete_filing_status_in_use_raises_integrity_error(
        self, app, db, seed_user,
    ):
        """Deleting a referenced filing_status fails with IntegrityError.

        Creates a salary_profile that references one of the seeded
        ``ref.filing_statuses`` rows; the row is then "in use".
        Attempting to delete that seeded row must fail under
        RESTRICT.
        """
        with app.app_context():
            # ``seed_user`` returns a dict; the canonical scenario
            # row it seeds (the "Baseline" scenario for the test
            # user) is enough to satisfy salary_profiles.scenario_id
            # without manufacturing a new row.  We only need a
            # salary_profile row to take an FK against
            # ``ref.filing_statuses``.
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            db.session.execute(text(
                "INSERT INTO salary.salary_profiles "
                "(user_id, scenario_id, filing_status_id, state_code) "
                "VALUES (:uid, :sid, 1, 'NC')"
            ), {"uid": user_id, "sid": scenario_id})
            db.session.commit()
            try:
                # Attempt to delete the in-use ref row.  RESTRICT
                # must refuse immediately.
                with pytest.raises(IntegrityError):
                    db.session.execute(text(
                        "DELETE FROM ref.filing_statuses WHERE id = 1"
                    ))
                    db.session.flush()
            finally:
                db.session.rollback()

    def test_delete_account_type_category_in_use_raises_integrity_error(
        self, app, db, seed_user,
    ):
        """Deleting a referenced account_type_categories row fails.

        ``ref.account_types.category_id`` is one of the nine FKs
        whose ondelete was lifted to RESTRICT.  The seeded
        ``ref.account_types`` rows reference seeded
        ``ref.account_type_categories`` rows; the seeded data is
        sufficient -- no per-user fixture state is needed.
        """
        with app.app_context():
            try:
                with pytest.raises(IntegrityError):
                    db.session.execute(text(
                        "DELETE FROM ref.account_type_categories "
                        "WHERE id = 1"
                    ))
                    db.session.flush()
            finally:
                db.session.rollback()


# ---------------------------------------------------------------------------
# 4. Idempotency
# ---------------------------------------------------------------------------


class TestUpgradeIdempotent:
    """upgrade() is a strict no-op against an already-migrated DB.

    The per-worker template comes up post-C-43, so a second upgrade
    must leave every artifact untouched.  Two assertion levels:
    (a) helper return values report False (no DDL ran), and
    (b) the live catalog still reflects the post-upgrade shape.
    """

    def test_drop_and_recreate_fk_returns_false_for_each_ref_fk(
        self, app, db, seed_user,
    ):
        """Every ``_drop_and_recreate_fk`` call returns False for ref FKs.

        False is the no-op signal: the FK already has the new name
        and the new ondelete clause, no DDL was issued.  True would
        indicate the test template is in an unexpected pre-migration
        state and would surface as a template-rebuild bug.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            bind = db.session.connection()
            ctx = MigrationContext.configure(connection=bind)
            with Operations.context(ctx):
                with patch.object(op, "get_bind", return_value=bind):
                    for spec in _M_C43.REF_FK_SPECS:
                        result = _M_C43._drop_and_recreate_fk(bind, *spec)
                        assert result is False, (
                            f"_drop_and_recreate_fk reported True (DDL "
                            f"ran) for {spec[0]}.{spec[7]} against a "
                            f"template that should already carry the "
                            f"post-C-43 state."
                        )

    def test_drop_and_recreate_fk_returns_false_for_transfers(
        self, app, db, seed_user,
    ):
        """``_drop_and_recreate_fk`` returns False for the transfers FK.

        Same idempotency contract as the ref-FK loop but exercises
        the CASCADE path of the helper (every spec in REF_FK_SPECS
        uses RESTRICT).
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            bind = db.session.connection()
            ctx = MigrationContext.configure(connection=bind)
            with Operations.context(ctx):
                with patch.object(op, "get_bind", return_value=bind):
                    result = _M_C43._drop_and_recreate_fk(
                        bind, *_M_C43.TRANSFERS_PAY_PERIOD_SPEC,
                    )
                    assert result is False, (
                        "_drop_and_recreate_fk reported True (DDL ran) "
                        "for budget.fk_transfers_pay_period_id against "
                        "a template that should already carry the "
                        "post-C-43 state."
                    )

    def test_full_upgrade_is_noop_on_already_migrated_db(
        self, app, db, seed_user,
    ):
        """Running ``upgrade()`` against an already-migrated DB succeeds.

        Asserts the round trip: every artifact present before,
        upgrade() runs without raising, every artifact still present
        after.  Catches a regression where the migration's idempotency
        helpers report no-op but the upgrade() body issues DDL anyway.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            # Pre-condition: every artifact in place.
            for spec in _M_C43.REF_FK_SPECS:
                source_schema, _, _, _, _, _, _, new_name, _ = spec
                assert _constraint_exists(db.session, source_schema, new_name)
            transfers_spec = _M_C43.TRANSFERS_PAY_PERIOD_SPEC
            assert _constraint_exists(db.session, "budget", transfers_spec[7])

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C43.upgrade()
            db.session.commit()

            # Post-condition: every artifact still in place.
            for spec in _M_C43.REF_FK_SPECS:
                source_schema, _, _, _, _, _, legacy_name, new_name, _ = spec
                assert _constraint_exists(
                    db.session, source_schema, new_name,
                ), (
                    f"upgrade() dropped {source_schema}.{new_name} on a "
                    f"no-op run."
                )
                if legacy_name != new_name:
                    assert not _constraint_exists(
                        db.session, source_schema, legacy_name,
                    ), (
                        f"upgrade() resurrected {source_schema}."
                        f"{legacy_name} on a no-op run."
                    )


# ---------------------------------------------------------------------------
# 5. Recreate-on-drift
# ---------------------------------------------------------------------------


class TestUpgradeRecreatesDriftedArtifacts:
    """upgrade() restores artifacts that have drifted away from spec.

    Simulates production-drift states (legacy FK names re-introduced,
    legacy ondelete restored) by forcing each artifact back to its
    pre-C-43 form, then running the upgrade against the otherwise-
    clean DB.
    """

    def test_upgrade_restricts_drifted_ref_fk(
        self, app, db, seed_user, restore_c43_state,
    ):
        """Reverting a ref-FK to NO ACTION + legacy name; upgrade restores.

        Single representative FK exercised (``ref.account_types``)
        rather than parametrising across all nine -- the per-spec
        Post-upgrade DB Shape tests already prove every FK reaches
        the new state from the template, so this test's job is to
        prove the migration's drop+recreate path works on a drifted
        DB.  One round trip suffices.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        spec = _M_C43.REF_FK_SPECS[0]  # ref.account_types.category_id
        (
            source_schema, source_table, source_column,
            target_schema, target_table, target_column,
            legacy_name, new_name, _new_ondelete,
        ) = spec
        with app.app_context():
            _force_fk_to_legacy_state(
                db.session,
                source_schema, source_table, source_column,
                target_schema, target_table, target_column,
                legacy_name, new_name, "NO ACTION",
            )
            db.session.commit()
            assert _constraint_exists(db.session, source_schema, legacy_name)
            assert not _constraint_exists(db.session, source_schema, new_name)

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C43.upgrade()
            db.session.commit()

            assert _constraint_exists(db.session, source_schema, new_name), (
                f"upgrade() did not restore {source_schema}.{new_name}."
            )
            new_code = _fk_ondelete_code(db.session, source_schema, new_name)
            assert (
                _M_C43._CONFDELTYPE_TO_LABEL[new_code] == "RESTRICT"
            ), (
                f"upgrade() restored {source_schema}.{new_name} but "
                f"with ondelete="
                f"{_M_C43._CONFDELTYPE_TO_LABEL[new_code]!r}, expected "
                f"RESTRICT."
            )

    def test_upgrade_cascades_drifted_transfers_pay_period(
        self, app, db, seed_user, restore_c43_state,
    ):
        """Reverting transfers.pay_period_id to RESTRICT + legacy name; upgrade restores.

        The transfers FK is the only CASCADE recreation in C-43 (the
        other nine recreations target RESTRICT).  A dedicated test
        proves the CASCADE path of the helper recovers from drift.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        (
            source_schema, source_table, source_column,
            target_schema, target_table, target_column,
            legacy_name, new_name, _new_ondelete,
        ) = _M_C43.TRANSFERS_PAY_PERIOD_SPEC
        with app.app_context():
            _force_fk_to_legacy_state(
                db.session,
                source_schema, source_table, source_column,
                target_schema, target_table, target_column,
                legacy_name, new_name, "RESTRICT",
            )
            db.session.commit()
            assert _constraint_exists(db.session, source_schema, legacy_name)
            assert not _constraint_exists(db.session, source_schema, new_name)

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C43.upgrade()
            db.session.commit()

            new_code = _fk_ondelete_code(db.session, source_schema, new_name)
            assert (
                _M_C43._CONFDELTYPE_TO_LABEL[new_code] == "CASCADE"
            ), (
                f"upgrade() restored {source_schema}.{new_name} but "
                f"with ondelete="
                f"{_M_C43._CONFDELTYPE_TO_LABEL[new_code]!r}, expected "
                f"CASCADE."
            )


# ---------------------------------------------------------------------------
# 6. Downgrade round-trip
# ---------------------------------------------------------------------------


class TestDowngradeReversesUpgrade:
    """downgrade() reverses every C-43 change symmetrically."""

    def test_downgrade_restores_legacy_ref_fk_names_and_ondeletes(
        self, app, db, seed_user, restore_c43_state,
    ):
        """After downgrade, every ref-FK carries its pre-C-43 name + NO ACTION.

        For the seven FKs that were originally named
        ``<table>_<column>_fkey``, downgrade restores that name.
        For the two ``fk_savings_goals_*`` FKs that already used
        the convention name pre-C-43, the name is unchanged but the
        ondelete reverts from RESTRICT to NO ACTION.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C43.downgrade()
            db.session.commit()

            for spec in _M_C43.REF_FK_SPECS:
                (
                    source_schema, _, _, _, _, _,
                    legacy_name, new_name, _new_ondelete,
                ) = spec
                assert _constraint_exists(
                    db.session, source_schema, legacy_name,
                ), (
                    f"downgrade() did not restore "
                    f"{source_schema}.{legacy_name}."
                )
                if legacy_name != new_name:
                    assert not _constraint_exists(
                        db.session, source_schema, new_name,
                    ), (
                        f"downgrade() left {source_schema}.{new_name} "
                        f"in place."
                    )
                # ondelete must be NO ACTION post-downgrade.
                code = _fk_ondelete_code(
                    db.session, source_schema, legacy_name,
                )
                assert _M_C43._CONFDELTYPE_TO_LABEL[code] == "NO ACTION", (
                    f"downgrade() restored {source_schema}."
                    f"{legacy_name} with ondelete="
                    f"{_M_C43._CONFDELTYPE_TO_LABEL[code]!r}, expected "
                    f"NO ACTION."
                )

    def test_downgrade_restores_transfers_pay_period_to_restrict(
        self, app, db, seed_user, restore_c43_state,
    ):
        """After downgrade, transfers.pay_period_id is RESTRICT under legacy name."""
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        (
            source_schema, _, _, _, _, _,
            legacy_name, new_name, _new_ondelete,
        ) = _M_C43.TRANSFERS_PAY_PERIOD_SPEC
        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C43.downgrade()
            db.session.commit()

            assert _constraint_exists(
                db.session, source_schema, legacy_name,
            )
            assert not _constraint_exists(
                db.session, source_schema, new_name,
            )
            code = _fk_ondelete_code(
                db.session, source_schema, legacy_name,
            )
            assert _M_C43._CONFDELTYPE_TO_LABEL[code] == "RESTRICT", (
                f"downgrade() restored {source_schema}.{legacy_name} "
                f"but with ondelete={_M_C43._CONFDELTYPE_TO_LABEL[code]!r}"
                f", expected RESTRICT (the original pre-C-43 clause)."
            )

    def test_full_round_trip_upgrade_downgrade_upgrade(
        self, app, db, seed_user, restore_c43_state,
    ):
        """upgrade -> downgrade -> upgrade returns the DB to the post-C-43 state.

        End-to-end round trip starting from the post-upgrade state.
        Verifies that downgrade is lossless (no data corruption,
        no orphaned constraints) and that upgrade is rerunnable
        after a downgrade.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C43.downgrade()
                    _M_C43.upgrade()
            db.session.commit()

            # Spot-check two representative artifacts (one per
            # operation group) rather than the full ten -- the
            # group-specific round-trip tests above prove each set
            # in isolation; this test proves the whole chain works
            # without leaking state between groups.
            assert _constraint_exists(
                db.session, "ref", "fk_account_types_category_id",
            ), "Round trip lost the C-43 ref-FK rename."
            new_code = _fk_ondelete_code(
                db.session, "budget", "fk_transfers_pay_period_id",
            )
            assert _M_C43._CONFDELTYPE_TO_LABEL[new_code] == "CASCADE", (
                "Round trip lost the C-43 transfers.pay_period_id "
                "CASCADE."
            )


# ---------------------------------------------------------------------------
# 7. Naming convention contract
# ---------------------------------------------------------------------------


class TestNamingConventionContract:
    """SHEKEL_NAMING_CONVENTION is documented and enforced by model contract.

    The convention is not applied globally to ``db.metadata`` (doing
    so would break the migration chain replay -- see
    ``app/extensions.py`` and the C-43 migration docstring for the
    rationale).  Instead, every FK touched by C-43 carries an
    explicit ``name=`` argument that matches the convention; these
    tests verify the contract by inspecting the SQLAlchemy model
    metadata directly.
    """

    def test_convention_has_full_template_set(self):
        """SHEKEL_NAMING_CONVENTION carries the five canonical entries.

        Asserts both the keys and the values so a typo or stray entry
        surfaces as a test failure.  The values mirror SQLAlchemy's
        placeholder template format (``%(...)s``) so the dictionary
        can be re-applied globally in the future if the migration-
        chain replay issue is resolved.
        """
        assert SHEKEL_NAMING_CONVENTION == {
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s",
            "pk": "pk_%(table_name)s",
        }, (
            "SHEKEL_NAMING_CONVENTION drifted from the canonical "
            "dictionary documented in app/extensions.py.  This "
            "would break the model FK-name contract enforced "
            "below."
        )

    @pytest.mark.parametrize(
        "model_cls, column_name, expected_fk_name",
        [(cls, col, name) for cls, col, name, _c43 in MODEL_FK_NAME_CONTRACT],
        ids=[name for _cls, _col, name, _c43 in MODEL_FK_NAME_CONTRACT],
    )
    def test_model_fk_has_explicit_convention_name(
        self, model_cls, column_name, expected_fk_name,
    ):
        """Every C-43 FK has an explicit name= matching the convention.

        Walks the model's column metadata, finds the ForeignKey
        object on the named column, and asserts its ``name``
        attribute matches the convention-derived expectation.
        The explicit name is what keeps the SQLAlchemy-rendered FK
        in sync with the DB-installed FK after C-43 -- without it
        the dialect-default ``<table>_<column>_fkey`` would creep
        back in on any future model edit.
        """
        column = model_cls.__table__.columns[column_name]
        foreign_keys = list(column.foreign_keys)
        assert len(foreign_keys) == 1, (
            f"{model_cls.__name__}.{column_name} has "
            f"{len(foreign_keys)} ForeignKey entries; expected "
            f"exactly one for the C-43 contract."
        )
        fk = foreign_keys[0]
        assert fk.name == expected_fk_name, (
            f"{model_cls.__name__}.{column_name} ForeignKey carries "
            f"name={fk.name!r}; expected {expected_fk_name!r} per the "
            f"C-43 naming-convention contract."
        )

    def test_ref_fk_models_declare_restrict_ondelete(self):
        """Every C-43 ref-FK model declares ondelete=RESTRICT.

        Distinct invariant from name above: even with the convention
        name, an ondelete drift would silently regress the F-073
        protection.  The transfers FK is explicitly excluded because
        it uses CASCADE per F-136; the per-spec ondelete is encoded
        in MODEL_FK_NAME_CONTRACT only implicitly via expected_fk_name,
        so the test reads the contract directly from REF_FK_SPECS.
        """
        for spec in _M_C43.REF_FK_SPECS:
            _src_schema, _src_table, _src_col, _, _, _, _, new_name, _ondelete = spec
            # Look up the model + column from MODEL_FK_NAME_CONTRACT, by the
            # name C-43 wrote (its fourth element).
            matching = [
                entry for entry in MODEL_FK_NAME_CONTRACT
                if entry[3] == new_name
            ]
            assert len(matching) == 1, (
                f"REF_FK_SPECS entry {new_name!r} has no matching "
                f"MODEL_FK_NAME_CONTRACT row; update the contract "
                f"table when adding/removing C-43 FKs."
            )
            model_cls, column_name, _, _ = matching[0]
            column = model_cls.__table__.columns[column_name]
            fk = next(iter(column.foreign_keys))
            assert fk.ondelete == "RESTRICT", (
                f"{model_cls.__name__}.{column_name} ForeignKey "
                f"carries ondelete={fk.ondelete!r}; expected 'RESTRICT' "
                f"per the F-073 contract.  Allowing a different "
                f"ondelete would silently regress the audit fix."
            )

    def test_transfers_pay_period_id_declares_cascade_ondelete(self):
        """Transfer.pay_period_id model declares ondelete=CASCADE.

        F-136's behavioural fix is the ondelete change; the rename
        is collateral.  This dedicated test guards against a future
        edit that restores RESTRICT while keeping the convention
        name.
        """
        column = Transfer.__table__.columns["pay_period_id"]
        fk = next(iter(column.foreign_keys))
        assert fk.ondelete == "CASCADE", (
            f"Transfer.pay_period_id ForeignKey carries ondelete="
            f"{fk.ondelete!r}; expected 'CASCADE' per the F-136 "
            f"contract."
        )


# ---------------------------------------------------------------------------
# 8. Helper return-value contracts
# ---------------------------------------------------------------------------


class TestHelperReturnValueContracts:
    """The migration's helpers return True iff DDL ran on this call."""

    def test_drop_and_recreate_fk_returns_true_when_creating(
        self, app, db, seed_user, restore_c43_state,
    ):
        """``_drop_and_recreate_fk`` returns True when DDL runs.

        Drops the new FK and re-installs the legacy state; calls the
        helper; asserts True.  Mirrors the C-42
        test_create_index_returns_true_when_creating pattern.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        spec = _M_C43.REF_FK_SPECS[0]  # ref.account_types.category_id
        (
            source_schema, source_table, source_column,
            target_schema, target_table, target_column,
            legacy_name, new_name, _new_ondelete,
        ) = spec
        with app.app_context():
            _force_fk_to_legacy_state(
                db.session,
                source_schema, source_table, source_column,
                target_schema, target_table, target_column,
                legacy_name, new_name, "NO ACTION",
            )
            db.session.commit()

            bind = db.session.connection()
            ctx = MigrationContext.configure(connection=bind)
            with Operations.context(ctx):
                with patch.object(op, "get_bind", return_value=bind):
                    result = _M_C43._drop_and_recreate_fk(bind, *spec)
            db.session.commit()
            assert result is True, (
                "_drop_and_recreate_fk returned False when the FK was "
                "in the legacy state -- the helper's True/False "
                "contract is broken."
            )

    def test_assert_fk_ondelete_passes_when_clause_matches(
        self, app, db, seed_user,
    ):
        """``_assert_fk_ondelete`` passes silently on the canonical state."""
        with app.app_context():
            bind = db.session.connection()
            # No raise = pass.
            _M_C43._assert_fk_ondelete(
                bind, "ref", "fk_account_types_category_id", "RESTRICT",
            )

    def test_assert_fk_ondelete_raises_on_mismatch(
        self, app, db, seed_user,
    ):
        """``_assert_fk_ondelete`` raises RuntimeError on ondelete mismatch."""
        with app.app_context():
            bind = db.session.connection()
            with pytest.raises(RuntimeError, match="ondelete="):
                _M_C43._assert_fk_ondelete(
                    bind, "ref", "fk_account_types_category_id", "CASCADE",
                )

    def test_assert_fk_ondelete_raises_on_missing(
        self, app, db, seed_user,
    ):
        """``_assert_fk_ondelete`` raises RuntimeError when the FK is absent."""
        with app.app_context():
            bind = db.session.connection()
            with pytest.raises(RuntimeError, match="is .*missing"):
                _M_C43._assert_fk_ondelete(
                    bind, "ref", "fk_does_not_exist_anywhere", "RESTRICT",
                )
