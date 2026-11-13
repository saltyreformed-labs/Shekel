"""Invariants 1-3: the migrated FKs, pay_period_id's CASCADE, and RESTRICT's refusal."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests._test_helpers import make_salary_profile

from ._helpers import _M_C43, _constraint_exists, _fk_ondelete_code


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
    @pytest.mark.usefixtures("seed_user")
    def test_ref_fk_has_restrict_and_new_name(
        self, app, db, spec,
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
            legacy_name, new_name, _new_ondelete,
        ) = spec
        source_schema = spec[0]
        with app.app_context():
            new_code = _fk_ondelete_code(db.session, source_schema, new_name)
            assert new_code is not None, (
                f"Post-upgrade FK {source_schema}.{new_name} is missing. "
                f"Rebuild the template via "
                f"`python scripts/build_test_template.py`."
            )
            assert new_code == "r", (
                f"FK {source_schema}.{new_name} has confdeltype "
                f"{new_code!r}, expected 'r' (RESTRICT)."
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

    @pytest.mark.usefixtures("seed_user")
    def test_transfers_pay_period_id_has_cascade_and_new_name(
        self, app, db,
    ):
        """transfers.pay_period_id carries CASCADE under fk_transfers_pay_period_id.

        The realignment from RESTRICT to CASCADE is the load-bearing
        F-136 fix; the rename to the convention name is the
        parallel F-078 collateral.  Both must hold post-C-43.
        """
        (
            _src_schema, _src_table, _src_col,
            _tgt_schema, _tgt_table, _tgt_col,
            legacy_name, new_name, _new_ondelete,
        ) = _M_C43.TRANSFERS_PAY_PERIOD_SPEC
        with app.app_context():
            new_code = _fk_ondelete_code(db.session, "budget", new_name)
            assert new_code is not None, (
                f"Post-upgrade FK budget.{new_name} is missing."
            )
            assert new_code == "c", (
                f"FK budget.{new_name} has confdeltype {new_code!r}, "
                f"expected 'c' (CASCADE)."
            )
            assert not _constraint_exists(db.session, "budget", legacy_name), (
                f"Legacy FK budget.{legacy_name} is still present "
                f"alongside the new {new_name}."
            )


# ---------------------------------------------------------------------------
# 2. Cross-table pay_period_id consistency
# ---------------------------------------------------------------------------


class TestPayPeriodIdConsistency:
    """The child FKs onto pay_periods that F-136 named are CASCADE-consistent.

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
    replacements.  The other two FKs F-136 named are still there and both
    still CASCADE, so the consistency invariant is unchanged for the rows it
    still covers; the case below asserts the third FK is GONE rather than dropping
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
    @pytest.mark.usefixtures("seed_user")
    def test_pay_period_id_fk_cascades(
        self, app, db, schema, name,
    ):
        """Both of F-136's surviving child FKs onto pay_periods.id CASCADE.

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
            assert code == "c", (
                f"FK {schema}.{name} has confdeltype {code!r}, expected "
                f"'c' (CASCADE).  F-136 invariant broken."
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
            # The shared builder, so the profile holds its first pay
            # entry: the database refuses a profile with none at commit
            # (task plan#3167).  Its filing status is the row in use, read
            # before the commit: the rewound schema (``_c43_schema``) lacks
            # columns the model's joined loads name, so no row is reloaded.
            profile = make_salary_profile(seed_user, db.session)
            filing_status_id = profile.filing_status_id
            db.session.commit()
            try:
                # Attempt to delete the in-use ref row.  RESTRICT
                # must refuse immediately.
                with pytest.raises(IntegrityError):
                    db.session.execute(text(
                        "DELETE FROM ref.filing_statuses WHERE id = :id"
                    ), {"id": filing_status_id})
                    db.session.flush()
            finally:
                db.session.rollback()

    @pytest.mark.usefixtures("seed_user")
    def test_delete_account_type_category_in_use_raises_integrity_error(
        self, app, db,
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
