"""The two defensive contracts: the FK naming convention and the helpers' return values."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.extensions import SHEKEL_NAMING_CONVENTION
from app.models.paycheck_line import PaycheckLine
from app.models.ref import AccountType
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.savings_goal import SavingsGoal
from app.models.transfer import Transfer

from ._helpers import _M_C43, _force_fk_to_legacy_state


#: The tables two of C-43's ``REF_FK_SPECS`` are on, which plan step
#: salary:X-at-2 dropped with their models (ruling plan#3235): no model is left
#: to declare those keys, so the model-contract check skips them by name.
_TABLES_NO_MODEL_DECLARES = frozenset({
    ("salary", "state_tax_configs"),
    ("salary", "tax_bracket_sets"),
})


# Mapping of (model class, column attribute name) for every FK that
# C-43 touched and a model still declares (the other two are on the
# tables ``_TABLES_NO_MODEL_DECLARES`` names).  Used by
# ``TestNamingConventionContract`` to assert every model declaration
# carries the convention-matching
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
    (Transfer, "pay_period_id",
     "fk_transfers_pay_period_id", "fk_transfers_pay_period_id"),
)


# ---------------------------------------------------------------------------
# 7. Naming convention contract
# ---------------------------------------------------------------------------


class TestNamingConventionContract:
    """SHEKEL_NAMING_CONVENTION is documented and enforced by model contract.

    The convention is not applied globally to ``db.metadata`` (doing
    so would break the migration chain replay -- see
    ``app/extensions.py`` and the C-43 migration docstring for the
    rationale).  Instead, every FK C-43 touched that a model still
    declares carries an explicit ``name=`` argument that matches the
    convention; these tests verify the contract by inspecting the
    SQLAlchemy model metadata directly.
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
        """Each C-43 FK a model declares has an explicit name= matching the convention.

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
            if (_src_schema, _src_table) in _TABLES_NO_MODEL_DECLARES:
                continue
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

    @pytest.mark.usefixtures("seed_user", "restore_c43_state")
    def test_drop_and_recreate_fk_returns_true_when_creating(
        self, app, db,
    ):
        """``drop_and_recreate_fk`` returns True when DDL runs.

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
                    result = _M_C43.drop_and_recreate_fk(bind, *spec)
            db.session.commit()
            assert result is True, (
                "drop_and_recreate_fk returned False when the FK was "
                "in the legacy state -- the helper's True/False "
                "contract is broken."
            )

    @pytest.mark.usefixtures("seed_user")
    def test_assert_fk_ondelete_passes_when_clause_matches(
        self, app, db,
    ):
        """``assert_fk_ondelete`` passes silently on the canonical state."""
        with app.app_context():
            bind = db.session.connection()
            # No raise = pass.
            _M_C43.assert_fk_ondelete(
                bind, "ref", "fk_account_types_category_id", "RESTRICT",
            )

    @pytest.mark.usefixtures("seed_user")
    def test_assert_fk_ondelete_raises_on_mismatch(
        self, app, db,
    ):
        """``assert_fk_ondelete`` raises RuntimeError on ondelete mismatch."""
        with app.app_context():
            bind = db.session.connection()
            with pytest.raises(RuntimeError, match="ondelete="):
                _M_C43.assert_fk_ondelete(
                    bind, "ref", "fk_account_types_category_id", "CASCADE",
                )

    @pytest.mark.usefixtures("seed_user")
    def test_assert_fk_ondelete_raises_on_missing(
        self, app, db,
    ):
        """``assert_fk_ondelete`` raises RuntimeError when the FK is absent."""
        with app.app_context():
            bind = db.session.connection()
            with pytest.raises(RuntimeError, match="is .*missing"):
                _M_C43.assert_fk_ondelete(
                    bind, "ref", "fk_does_not_exist_anywhere", "RESTRICT",
                )
