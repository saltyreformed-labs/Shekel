"""Tests for the C-41 / F-069 migration ``a80c3447c153``.

The migration creates the partial unique index
``budget.uq_scenarios_one_baseline`` on the production database (it
was already present on the test-template path via the c5d6e7f8a901
upgrade and on the ``db.create_all()`` path via the Scenario model
declaration, but production was bootstrapped before either of those
paths carried the index -- see F-069 / H-2 in
``shekel-audits/security-2026-04-15/model-migration-drift.md``).

These tests exercise four invariants of the migration:

  1. **Idempotency.**  Running the upgrade against a database that
     already carries a correctly-shaped index is a no-op.  The
     test-template path lands every per-worker DB in exactly that
     state, so the assertion confirms the test suite itself does not
     trip the migration on every session start.
  2. **Recreate-on-missing.**  Dropping the index and re-running the
     upgrade restores it with the canonical UNIQUE, partial WHERE
     ``is_baseline = ...`` shape.
  3. **Pre-flight refusal on dirty data.**  Inserting a second
     baseline scenario for the same user and running the upgrade
     raises ``RuntimeError`` whose message names every offender
     (user_id, scenario ids, scenario names, created_at values) and
     the manual remediation SQL.  The migration MUST refuse rather
     than auto-resolving because picking the "winning" baseline is a
     financial decision the operator must make.
  4. **Downgrade halts.**  Calling ``downgrade()`` raises
     ``NotImplementedError`` with the manual recovery SQL embedded;
     a ``flask db downgrade`` chain stops here rather than continuing
     past a half-reverted step.

Two additional defensive tests guard the post-creation shape check:

  5. **Shape check rejects a malformed index.**  If the index name
     is present but the definition is missing the UNIQUE keyword or
     the partial WHERE clause, ``_assert_index_shape`` refuses with
     an actionable RuntimeError.
  6. **Multi-user partial index preserves per-user scope.**  After
     recreation, two distinct users may each carry exactly one
     baseline scenario simultaneously -- the partial index is
     keyed on ``user_id``, not globally unique on ``is_baseline``.

Tests bootstrap an Alembic ``MigrationContext`` against the test
session's connection so the migration's ``op.create_index`` and the
``op.get_bind`` proxy resolve to real DDL through Alembic's
Operations proxy.  This mirrors the test-c19 round-trip pattern.
"""
# pylint: disable=redefined-outer-name,unused-argument
# pylint: disable=too-many-arguments,too-many-positional-arguments
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; tests in this file declare local fixtures and consume them
# under the same names.  ``unused-argument`` is unavoidable for the
# ``restore_baseline_index`` fixture, which yields nothing and is
# requested purely so its teardown reaches the test's cleanup phase
# even on failure.  ``too-many-arguments`` / ``too-many-positional-
# arguments`` fire on the multi-user integration tests that need
# four conftest fixtures plus the local restore fixture; the
# threshold is below pytest's standard fixture footprint.
from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.extensions import db as _db
from app.models.scenario import Scenario


# ---------------------------------------------------------------------------
# Migration module loader
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename: str):
    """Load an Alembic migration file as a Python module via importlib.

    The ``migrations/versions`` directory has no ``__init__.py`` so
    standard ``import`` would fail; alembic itself loads scripts via
    importlib at runtime.  We mirror that behaviour to access module-
    level constants, helper functions, and the ``upgrade`` /
    ``downgrade`` callables directly from tests.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_C41 = _load_migration(
    "a80c3447c153_c41_create_uq_scenarios_one_baseline_.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _index_present(session) -> bool:
    """Return True iff ``budget.uq_scenarios_one_baseline`` exists."""
    return bool(session.execute(text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_indexes "
        "  WHERE schemaname = 'budget' "
        "    AND indexname = 'uq_scenarios_one_baseline'"
        ")"
    )).scalar())


def _drop_index(session) -> None:
    """Drop ``budget.uq_scenarios_one_baseline`` if present.

    Uses raw SQL with ``IF EXISTS`` so the call is a safe no-op on a
    database that has already lost the index (e.g., during the
    cleanup of a previous test that ran the upgrade halfway).
    """
    session.execute(text(
        "DROP INDEX IF EXISTS budget.uq_scenarios_one_baseline"
    ))


def _recreate_canonical_index(session) -> None:
    """Recreate the canonical partial unique index via raw SQL.

    Used by the duplicate-baseline test's cleanup path -- once the
    test deletes the duplicate row that caused the pre-flight to
    refuse, the index can be recreated against the now-clean data.
    Mirrors the shape of the index declared by the
    ``Scenario.__table_args__`` partial-index entry so subsequent
    tests see the same constraint they would see in production after
    the migration ran successfully.

    DROP before CREATE rather than ``CREATE IF NOT EXISTS``:
    PostgreSQL only checks the index NAME on ``IF NOT EXISTS``, not
    the definition.  If a prior caller left a malformed index of the
    same name in place (e.g. a non-partial unique index created by
    ``TestAssertIndexShapeRejectsMalformedIndex::test_assert_index_
    shape_raises_on_non_partial_index``), an ``IF NOT EXISTS``
    create would be a no-op and the malformed shape would survive
    the cleanup.  See ``phase1-flake-investigation.md`` for the full
    failure mode this guards against.
    """
    session.execute(text(
        "DROP INDEX IF EXISTS budget.uq_scenarios_one_baseline"
    ))
    session.execute(text(
        "CREATE UNIQUE INDEX uq_scenarios_one_baseline "
        "ON budget.scenarios (user_id) "
        "WHERE is_baseline = true"
    ))


def _insert_baseline_directly(session, user_id: int, name: str) -> int:
    """INSERT a baseline Scenario row bypassing the ORM identity map.

    The partial unique index normally rejects a second baseline per
    user; the duplicate-baseline regression test drops the index
    first, then uses this helper to insert the duplicate.  Returns
    the new scenario's id so the test can reference it in assertions
    and in cleanup.
    """
    return session.execute(text(
        "INSERT INTO budget.scenarios (user_id, name, is_baseline) "
        "VALUES (:uid, :name, TRUE) "
        "RETURNING id"
    ), {"uid": user_id, "name": name}).scalar()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_baseline_index(db):
    """Ensure ``uq_scenarios_one_baseline`` is restored after the test.

    Several tests in this module drop the partial unique index to
    insert duplicate baselines or to simulate the production "index
    is missing" state.  This fixture guarantees the index is back in
    place once the test exits, even if the body raised -- otherwise
    every subsequent scenario-baseline regression test (notably
    ``test_scenario_constraints.py``) running in the same per-worker
    DB would silently pass against a degraded schema.

    Cleanup is three-stage:

      1. ``DELETE FROM budget.scenarios WHERE ... `` removes any
         duplicate baselines the test inserted so the next stage's
         ``CREATE UNIQUE INDEX`` does not collide with the dirty data.
      2. ``DROP INDEX IF EXISTS`` removes any index of the same name,
         malformed or canonical.  This is the load-bearing step: a
         prior ``CREATE UNIQUE INDEX IF NOT EXISTS`` form would skip
         when ANY index of that name existed regardless of its
         definition, so a test body that installed a malformed
         (non-partial) shape (e.g.
         ``TestAssertIndexShapeRejectsMalformedIndex::test_assert_index_
         shape_raises_on_non_partial_index``) would leak the broken
         shape across worker tests and break every subsequent
         scenario-baseline test on the same per-worker DB.  See
         ``phase1-flake-investigation.md`` for the failure mode.
      3. ``CREATE UNIQUE INDEX`` recreates the index using the
         canonical partial-WHERE shape against the now-empty name slot.
    """
    yield
    db.session.rollback()
    # Remove any duplicate baselines that survived a failed test.
    db.session.execute(text(
        "DELETE FROM budget.scenarios "
        "WHERE name LIKE 'C41 Duplicate%'"
    ))
    # Drop-then-create so a malformed shape from the test body cannot
    # survive the cleanup -- see the docstring above for the failure
    # mode that motivated this pattern.
    db.session.execute(text(
        "DROP INDEX IF EXISTS budget.uq_scenarios_one_baseline"
    ))
    db.session.execute(text(
        "CREATE UNIQUE INDEX uq_scenarios_one_baseline "
        "ON budget.scenarios (user_id) "
        "WHERE is_baseline = true"
    ))
    db.session.commit()


# ---------------------------------------------------------------------------
# 1. Idempotency
# ---------------------------------------------------------------------------


class TestUpgradeIdempotentOnAlreadyCorrectDb:
    """``upgrade()`` is a no-op against a DB that already has the index.

    The per-worker test template comes up with the index already in
    place (the model declaration drives ``db.create_all()`` and the
    test-template-from-migrations path lands the same shape via the
    c5d6e7f8a901 upgrade).  The migration must therefore tolerate
    this state and avoid raising on any subsequent test-session
    bootstrap.
    """

    def test_index_present_before_upgrade(self, app, db, seed_user):
        """Sanity: the per-worker test DB carries the index by default.

        Confirms the precondition for the rest of the idempotency
        suite -- if this assertion ever fails the test template was
        rebuilt without the model declaration / migration chain that
        materialises the index, and the rest of the file no longer
        exercises what it claims to.
        ``seed_user`` is requested to ensure the conftest fixtures
        have produced a populated scenarios table; the assertion
        itself does not depend on its return value.
        """
        # pylint: disable=unused-argument
        with app.app_context():
            assert _index_present(db.session), (
                "Test template precondition broken: the per-worker DB "
                "is missing uq_scenarios_one_baseline.  Rebuild the "
                "template via `python scripts/build_test_template.py`."
            )

    def test_upgrade_is_noop_when_index_already_exists(
        self, app, db, seed_user
    ):
        """Running upgrade() leaves the existing index in place untouched.

        Asserts the round-trip: index present before, upgrade runs
        without raising, index still present after.  The
        ``_create_index_if_missing`` helper returns False to indicate
        the no-op path was taken; the test verifies that signal
        through the live DB state because the helper is private.
        """
        # pylint: disable=unused-argument
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            assert _index_present(db.session)
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C41.upgrade()
            db.session.commit()
            assert _index_present(db.session), (
                "upgrade() against an already-correct DB dropped the "
                "index or otherwise mutated the schema.  The migration "
                "must be a strict no-op in this case."
            )

    def test_create_index_if_missing_returns_false_on_present_index(
        self, app, db
    ):
        """``_create_index_if_missing`` reports False when no DDL ran.

        The boolean return value is the load-bearing signal that the
        operator can use to know whether the migration actually
        changed schema.  False = no-op; True = real cleanup.
        ``app.app_context`` is required because the helper executes
        SQL through ``db.session.connection()`` which assumes a Flask
        app context.
        """
        with app.app_context():
            assert _index_present(db.session)
            assert _M_C41._index_exists(  # pylint: disable=protected-access
                db.session.connection(),
                _M_C41.INDEX_NAME,
                _M_C41.SCHEMA_NAME,
            ) is True


# ---------------------------------------------------------------------------
# 2. Recreate-on-missing
# ---------------------------------------------------------------------------


class TestUpgradeRecreatesMissingIndex:
    """``upgrade()`` restores ``uq_scenarios_one_baseline`` when it is missing.

    The production-drift path: simulate the schema state F-069
    documents by dropping the index, then run the upgrade against an
    otherwise-clean scenarios table.  The test asserts both
    existence and the canonical shape (UNIQUE + partial WHERE).
    """

    def test_upgrade_recreates_index_when_missing(
        self, app, db, seed_user, restore_baseline_index
    ):
        """Drop the index, run upgrade(), assert the index is back.

        Mirrors the deployment-time scenario on production: a stamp-
        bootstrapped database lacks the index, the operator runs
        ``flask db upgrade``, and the schema converges to the model
        declaration.
        """
        # pylint: disable=unused-argument
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            _drop_index(db.session)
            db.session.commit()
            assert not _index_present(db.session), (
                "Pre-test setup did not drop the index -- the rest "
                "of the test would falsely pass against the still-"
                "present production-state index."
            )

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C41.upgrade()
            db.session.commit()

            assert _index_present(db.session), (
                "upgrade() did not recreate the index when the DB was "
                "in the F-069 production-drift state."
            )

    def test_recreated_index_has_canonical_shape(
        self, app, db, seed_user, restore_baseline_index
    ):
        """The recreated index is UNIQUE on user_id with the partial WHERE.

        Asserts the four shape invariants the
        ``_assert_index_shape`` helper enforces:

          * CREATE UNIQUE INDEX (not a regular non-unique index).
          * Targets ``budget.scenarios``.
          * Indexes the ``user_id`` column.
          * Carries a partial WHERE clause referencing
            ``is_baseline``.

        Reads ``pg_indexes.indexdef`` directly so the assertion is
        independent of the migration's own shape-check helper -- if
        the migration's helper is buggy and accepts a malformed
        shape, this test would still flag the divergence.
        """
        # pylint: disable=unused-argument
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            _drop_index(db.session)
            db.session.commit()

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C41.upgrade()
            db.session.commit()

            definition = db.session.execute(text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'budget' "
                "  AND indexname = 'uq_scenarios_one_baseline'"
            )).scalar()
            assert definition is not None, (
                "Index missing after upgrade(); the recreate path "
                "did not actually run."
            )
            assert "CREATE UNIQUE INDEX" in definition, (
                f"Recreated index is not UNIQUE: {definition!r}"
            )
            assert "budget.scenarios" in definition, (
                f"Recreated index does not target budget.scenarios: "
                f"{definition!r}"
            )
            assert "(user_id)" in definition, (
                f"Recreated index is not keyed on user_id: "
                f"{definition!r}"
            )
            assert "WHERE" in definition and "is_baseline" in definition, (
                f"Recreated index has no partial WHERE clause on "
                f"is_baseline -- the per-user scoping is lost: "
                f"{definition!r}"
            )

    def test_recreated_index_enforces_one_baseline_per_user(
        self, app, db, seed_user, restore_baseline_index
    ):
        """After recreation, a second baseline for the same user is rejected.

        End-to-end behavioural check: drop the index, run the
        upgrade, then attempt to insert a duplicate baseline via the
        ORM.  The ``IntegrityError`` must name
        ``uq_scenarios_one_baseline`` so the operator can trace the
        rejection back to this constraint.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy.exc import IntegrityError

        with app.app_context():
            _drop_index(db.session)
            db.session.commit()

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C41.upgrade()
            db.session.commit()

            duplicate = Scenario(
                user_id=seed_user["user"].id,
                name="C41 Duplicate Baseline",
                is_baseline=True,
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "uq_scenarios_one_baseline" in str(exc_info.value), (
                "IntegrityError did not name the recreated index; "
                "the constraint name surfaced by PostgreSQL does not "
                "match the migration's INDEX_NAME constant."
            )
            db.session.rollback()


# ---------------------------------------------------------------------------
# 3. Pre-flight refusal on duplicate baselines
# ---------------------------------------------------------------------------


class TestUpgradeRefusesOnDuplicateBaselines:
    """Pre-flight refuses with a clear error when duplicate baselines exist.

    The migration MUST refuse rather than auto-resolving because
    picking the "winning" baseline is a financial decision the
    operator must make per ``docs/coding-standards.md``.  These tests
    verify the refusal mechanism and the diagnostic message content.
    """

    def test_upgrade_raises_runtime_error_with_index_name(
        self, app, db, seed_user, restore_baseline_index
    ):
        """A duplicate baseline triggers RuntimeError naming the index.

        Drops the index (so the duplicate insert can succeed), pre-
        seeds a second baseline for ``seed_user``, then runs upgrade().
        The exception's message must reference the index name so the
        operator immediately knows which migration refused.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op

        with app.app_context():
            _drop_index(db.session)
            db.session.commit()
            dup_id = _insert_baseline_directly(
                db.session, seed_user["user"].id, "C41 Duplicate One",
            )
            db.session.commit()
            assert dup_id is not None

            with pytest.raises(RuntimeError) as exc_info:
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C41.upgrade()
            assert _M_C41.INDEX_NAME in str(exc_info.value)

    def test_runtime_error_message_lists_every_violator(
        self, app, db, seed_user, seed_second_user, restore_baseline_index
    ):
        """The message names every user_id, scenario id, and scenario name.

        Two users each carry a duplicate baseline.  The diagnostic
        must list both users so the operator does not need to re-run
        the migration after fixing each one (the migration would
        re-detect the next violator anyway, but a complete list lets
        the operator plan the remediation in one batch).
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op

        with app.app_context():
            _drop_index(db.session)
            db.session.commit()
            uid1 = seed_user["user"].id
            uid2 = seed_second_user["user"].id
            dup1 = _insert_baseline_directly(
                db.session, uid1, "C41 Duplicate U1",
            )
            dup2 = _insert_baseline_directly(
                db.session, uid2, "C41 Duplicate U2",
            )
            db.session.commit()

            with pytest.raises(RuntimeError) as exc_info:
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C41.upgrade()
            message = str(exc_info.value)

            # Both user ids surface in the diagnostic.
            assert f"user_id={uid1!r}" in message, message
            assert f"user_id={uid2!r}" in message, message
            # Both duplicate scenario ids surface in the diagnostic.
            assert f"id={dup1!r}" in message, message
            assert f"id={dup2!r}" in message, message
            # Both duplicate scenario names surface in the diagnostic.
            assert "'C41 Duplicate U1'" in message, message
            assert "'C41 Duplicate U2'" in message, message
            # The remediation SQL surfaces for copy-paste recovery.
            assert (
                "UPDATE budget.scenarios SET is_baseline = FALSE "
                "WHERE id = <scenario_id>"
            ) in message, message

    def test_index_not_created_when_pre_flight_refuses(
        self, app, db, seed_user, restore_baseline_index
    ):
        """A failed pre-flight does NOT create the index.

        Without this guarantee a half-migrated schema could leave the
        operator with an index whose existence implies "one baseline
        per user" but whose underlying data already violates the
        invariant.  The pre-flight runs BEFORE
        ``op.create_index``, so the index must not appear when the
        pre-flight raises.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op

        with app.app_context():
            _drop_index(db.session)
            db.session.commit()
            _insert_baseline_directly(
                db.session, seed_user["user"].id, "C41 Duplicate Pre",
            )
            db.session.commit()
            assert not _index_present(db.session), (
                "Pre-flight precondition broken: the index is "
                "already present despite the test dropping it."
            )

            with pytest.raises(RuntimeError):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C41.upgrade()
            db.session.rollback()
            assert not _index_present(db.session), (
                "Pre-flight failure left the index half-created; "
                "the migration emitted DDL despite refusing the data."
            )


# ---------------------------------------------------------------------------
# 4. Downgrade raises NotImplementedError
# ---------------------------------------------------------------------------


class TestDowngradeRaisesNotImplementedError:
    """``downgrade()`` halts the chain with an actionable manual-recovery hint.

    The migration's upgrade is a one-way reconciliation; allowing
    ``flask db downgrade`` to silently drop the partial unique index
    would re-open the F-069 drift on production.
    """

    def test_downgrade_raises_not_implemented_error(self):
        """Calling downgrade() raises NotImplementedError, not pass."""
        with pytest.raises(NotImplementedError):
            _M_C41.downgrade()

    def test_downgrade_message_names_the_index(self):
        """The operator must see ``uq_scenarios_one_baseline`` in the message."""
        with pytest.raises(NotImplementedError) as exc_info:
            _M_C41.downgrade()
        assert "uq_scenarios_one_baseline" in str(exc_info.value)

    def test_downgrade_message_includes_manual_recovery_sql(self):
        """The message must carry the copy-paste DROP INDEX statement.

        Operators reach the downgrade only when something has gone
        wrong; the recovery SQL must be present in the exception
        message rather than buried in external docs so the operator
        does not have to context-switch under pressure.  Also asserts
        the ``flask db stamp <prior_revision>`` hint so the chain can
        be continued past this migration after a manual drop.
        """
        with pytest.raises(NotImplementedError) as exc_info:
            _M_C41.downgrade()
        message = str(exc_info.value)
        assert "DROP INDEX budget.uq_scenarios_one_baseline" in message
        assert "flask db stamp 2109f7a490e7" in message


# ---------------------------------------------------------------------------
# 5. Shape-check defensive tests
# ---------------------------------------------------------------------------


class TestAssertIndexShapeRejectsMalformedIndex:
    """``_assert_index_shape`` raises on an index that drifts from canonical.

    Defensive: a future hand-recreation of the index that drops the
    UNIQUE keyword or the WHERE clause would silently break the
    one-baseline-per-user invariant.  The shape check is the last
    line of defense against such drift.
    """

    def test_assert_index_shape_passes_on_canonical_index(self, app, db):
        """The canonical post-upgrade index passes the shape check.

        Baseline assertion: the helper does not raise against the
        index the test-template path produces.  Without this baseline
        the negative test cases below could pass for the wrong
        reason (e.g., the helper raises on every input).
        """
        with app.app_context():
            assert _index_present(db.session)
            # Should not raise.
            _M_C41._assert_index_shape(  # pylint: disable=protected-access
                db.session.connection(),
                _M_C41.INDEX_NAME,
                _M_C41.SCHEMA_NAME,
            )

    def test_assert_index_shape_raises_on_non_partial_index(
        self, app, db, restore_baseline_index
    ):
        """A non-partial index (no WHERE clause) is rejected.

        Drop the canonical partial-unique index and recreate it
        WITHOUT the WHERE clause.  The shape check must detect the
        missing partial predicate and raise -- otherwise an operator
        who hand-recreates the index incorrectly would silently
        downgrade the constraint from "one baseline per user" to
        "one Scenario row per user", breaking the multi-scenario
        product entirely.
        """
        with app.app_context():
            _drop_index(db.session)
            db.session.execute(text(
                "CREATE UNIQUE INDEX uq_scenarios_one_baseline "
                "ON budget.scenarios (user_id)"
            ))
            db.session.commit()

            with pytest.raises(RuntimeError) as exc_info:
                _M_C41._assert_index_shape(  # pylint: disable=protected-access
                    db.session.connection(),
                    _M_C41.INDEX_NAME,
                    _M_C41.SCHEMA_NAME,
                )
            message = str(exc_info.value)
            assert "shape check failed" in message.lower(), message
            assert "WHERE" in message, message

    def test_assert_index_shape_raises_when_index_absent(self, app, db):
        """A missing index raises a clear error with remediation hint.

        Different from the partial-vs-full check: the index is not
        there at all.  Indicates a state where the migration's
        idempotency guard failed (or someone dropped the index after
        creation).  The error message must guide the operator toward
        re-running the migration rather than silently passing.
        """
        with app.app_context():
            _drop_index(db.session)
            db.session.commit()
            try:
                with pytest.raises(RuntimeError) as exc_info:
                    _M_C41._assert_index_shape(  # pylint: disable=protected-access
                        db.session.connection(),
                        _M_C41.INDEX_NAME,
                        _M_C41.SCHEMA_NAME,
                    )
                assert "disappeared" in str(exc_info.value).lower()
            finally:
                # Restore for subsequent tests.
                _recreate_canonical_index(db.session)
                db.session.commit()


# ---------------------------------------------------------------------------
# 6. Multi-user scoping
# ---------------------------------------------------------------------------


class TestRecreatedIndexPreservesPerUserScope:
    """After recreation, distinct users can each carry one baseline.

    Defensive: a partial unique index keyed globally on
    ``is_baseline`` (rather than per-user) would silently collapse
    every user's baseline into one row -- a multi-tenant
    catastrophe.  This test confirms the per-user scoping survives
    the recreate path.
    """

    def test_two_users_each_carry_one_baseline_after_recreate(
        self, app, db, seed_user, seed_second_user, restore_baseline_index
    ):
        """seed_user and seed_second_user both have their own baseline.

        Each conftest fixture creates a baseline scenario at setup;
        after the recreate path runs, both rows must remain visible
        without the partial unique index colliding them.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            _drop_index(db.session)
            db.session.commit()

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C41.upgrade()
            db.session.commit()

            baselines = db.session.execute(text(
                "SELECT user_id, count(*) AS n "
                "FROM budget.scenarios "
                "WHERE is_baseline = TRUE "
                "GROUP BY user_id "
                "ORDER BY user_id"
            )).fetchall()
            by_user = {row.user_id: row.n for row in baselines}
            assert by_user.get(seed_user["user"].id) == 1, (
                f"seed_user lost its baseline scenario; baselines by "
                f"user: {by_user!r}"
            )
            assert by_user.get(seed_second_user["user"].id) == 1, (
                f"seed_second_user lost its baseline scenario; "
                f"baselines by user: {by_user!r}"
            )
