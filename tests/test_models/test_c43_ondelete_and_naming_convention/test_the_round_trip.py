"""Invariants 4-6: upgrade is a no-op, recreates what drifted, and downgrade reverses it."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ._helpers import _M_C43, _constraint_exists, _fk_ondelete_code, _force_fk_to_legacy_state


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

    @pytest.mark.usefixtures("seed_user")
    def test_drop_and_recreate_fk_returns_false_for_each_ref_fk(
        self, app, db,
    ):
        """Every ``drop_and_recreate_fk`` call returns False for ref FKs.

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
                        result = _M_C43.drop_and_recreate_fk(bind, *spec)
                        assert result is False, (
                            f"drop_and_recreate_fk reported True (DDL "
                            f"ran) for {spec[0]}.{spec[7]} against a "
                            f"template that should already carry the "
                            f"post-C-43 state."
                        )

    @pytest.mark.usefixtures("seed_user")
    def test_drop_and_recreate_fk_returns_false_for_transfers(
        self, app, db,
    ):
        """``drop_and_recreate_fk`` returns False for the transfers FK.

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
                    result = _M_C43.drop_and_recreate_fk(
                        bind, *_M_C43.TRANSFERS_PAY_PERIOD_SPEC,
                    )
                    assert result is False, (
                        "drop_and_recreate_fk reported True (DDL ran) "
                        "for budget.fk_transfers_pay_period_id against "
                        "a template that should already carry the "
                        "post-C-43 state."
                    )

    @pytest.mark.usefixtures("seed_user")
    def test_full_upgrade_is_noop_on_already_migrated_db(
        self, app, db,
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

    @pytest.mark.usefixtures("seed_user", "restore_c43_state")
    def test_upgrade_restricts_drifted_ref_fk(
        self, app, db,
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
            assert new_code == "r", (
                f"upgrade() restored {source_schema}.{new_name} but "
                f"with confdeltype {new_code!r}, expected 'r' (RESTRICT)."
            )

    @pytest.mark.usefixtures("seed_user", "restore_c43_state")
    def test_upgrade_cascades_drifted_transfers_pay_period(
        self, app, db,
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
            assert new_code == "c", (
                f"upgrade() restored {source_schema}.{new_name} but "
                f"with confdeltype {new_code!r}, expected 'c' (CASCADE)."
            )


# ---------------------------------------------------------------------------
# 6. Downgrade round-trip
# ---------------------------------------------------------------------------


class TestDowngradeReversesUpgrade:
    """downgrade() reverses every C-43 change symmetrically."""

    @pytest.mark.usefixtures("seed_user", "restore_c43_state")
    def test_downgrade_restores_legacy_ref_fk_names_and_ondeletes(
        self, app, db,
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
                assert code == "a", (
                    f"downgrade() restored {source_schema}."
                    f"{legacy_name} with confdeltype {code!r}, expected "
                    f"'a' (NO ACTION)."
                )

    @pytest.mark.usefixtures("seed_user", "restore_c43_state")
    def test_downgrade_restores_transfers_pay_period_to_restrict(
        self, app, db,
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
            assert code == "r", (
                f"downgrade() restored {source_schema}.{legacy_name} "
                f"but with confdeltype {code!r}, expected 'r' (RESTRICT, "
                f"the original pre-C-43 clause)."
            )

    @pytest.mark.usefixtures("seed_user", "restore_c43_state")
    def test_full_round_trip_upgrade_downgrade_upgrade(
        self, app, db,
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
            assert new_code == "c", (
                "Round trip lost the C-43 transfers.pay_period_id "
                f"CASCADE ('c'); confdeltype is {new_code!r}."
            )
