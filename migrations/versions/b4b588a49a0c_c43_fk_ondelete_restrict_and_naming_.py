"""C-43: ref-FK ondelete=RESTRICT sweep + transfers.pay_period_id alignment
+ FK naming-convention forward enforcement

Closes audit findings F-073 (Medium), F-078 (Medium), and F-136 (Low)
of the 2026-04-15 security remediation plan.  Two groups of DDL
operations plus one documentation deliverable:

  1. **Ref-FK ondelete sweep (F-073).**  Nine foreign keys that
     reference ``ref.*`` lookup tables were created without an
     explicit ``ondelete`` clause and therefore defaulted to
     PostgreSQL's implicit ``NO ACTION``.  Coding standards (see
     ``docs/coding-standards.md`` "SQL / Database / Schema Design")
     require ``ondelete="RESTRICT"`` for every ref-table FK.  The
     practical distinction matters because:

       * ``NO ACTION`` defers the check to the end of the
         transaction, so a multi-statement transaction that deletes
         a ref row and then re-inserts a dependent row may succeed
         (violating the operator's expectation that the catalog
         protects the seed data).
       * ``RESTRICT`` raises immediately on the offending DELETE
         statement, giving a clean traceback at the point of the
         violating operation.

     The nine FKs recreated (old name -> new name):

       * ``ref.account_types.category_id``:
         ``account_types_category_id_fkey`` ->
         ``fk_account_types_category_id``
       * ``budget.savings_goals.goal_mode_id``:
         ``fk_savings_goals_goal_mode_id`` (name preserved;
         ondelete only)
       * ``budget.savings_goals.income_unit_id``:
         ``fk_savings_goals_income_unit_id`` (name preserved;
         ondelete only)
       * ``salary.paycheck_deductions.calc_method_id``:
         ``paycheck_deductions_calc_method_id_fkey`` ->
         ``fk_paycheck_deductions_calc_method_id``
       * ``salary.paycheck_deductions.deduction_timing_id``:
         ``paycheck_deductions_deduction_timing_id_fkey`` ->
         ``fk_paycheck_deductions_deduction_timing_id``
       * ``salary.salary_profiles.filing_status_id``:
         ``salary_profiles_filing_status_id_fkey`` ->
         ``fk_salary_profiles_filing_status_id``
       * ``salary.salary_raises.raise_type_id``:
         ``salary_raises_raise_type_id_fkey`` ->
         ``fk_salary_raises_raise_type_id``
       * ``salary.state_tax_configs.tax_type_id``:
         ``state_tax_configs_tax_type_id_fkey`` ->
         ``fk_state_tax_configs_tax_type_id``
       * ``salary.tax_bracket_sets.filing_status_id``:
         ``tax_bracket_sets_filing_status_id_fkey`` ->
         ``fk_tax_bracket_sets_filing_status_id``

     ``ALTER TABLE ... DROP CONSTRAINT`` followed by ``ADD
     CONSTRAINT`` is used (not ``ALTER CONSTRAINT``) because the
     ondelete clause is part of the constraint definition and
     PostgreSQL has no in-place form for changing it.  Both
     statements run inside the migration's single transaction,
     so the window during which the FK is absent is invisible to
     concurrent statements.

  2. **transfers.pay_period_id realignment (F-136).**  Sibling
     tables that share ``pay_period_id`` (``budget.transactions``,
     ``budget.account_anchor_history``) CASCADE on the pay-period
     parent; ``budget.transfers`` alone uses RESTRICT.  The
     asymmetry was unintentional drift and produces two concrete
     bugs:

       * User account deletion fans out into ``pay_periods``,
         ``transactions``, and ``transfers`` simultaneously
         through their respective user_id-CASCADE FKs.
         PostgreSQL evaluates every referential action for one
         DELETE in a single pass; the RESTRICT on
         ``transfers.pay_period_id`` previously raised even
         though every row was destined for deletion.
       * The transfer invariant (every transfer has exactly two
         linked shadow transactions) becomes briefly violated
         during a pay-period deletion: the shadows cascade
         through ``transactions.pay_period_id``-CASCADE first,
         then the RESTRICT on ``transfers.pay_period_id`` rolls
         the whole DELETE back -- leaving the operator with a
         confusing "cannot delete from pay_periods" error that
         masks the real reason (the orphan-shadow guard).
         Switching to CASCADE makes the cascade atomic: transfer
         + shadows + pay_period all disappear together.

     The FK is dropped and recreated with name
     ``fk_transfers_pay_period_id`` and ``ondelete="CASCADE"``.

  3. **Forward naming-convention enforcement (F-078).**  The
     companion ``app/extensions.py`` change documents
     ``SHEKEL_NAMING_CONVENTION`` -- a dictionary mirroring the
     placeholder template SQLAlchemy's
     ``MetaData.naming_convention`` would consume if the dictionary
     were applied globally.  It is NOT applied globally because
     doing so causes the chain-replay path (the test template build
     via ``scripts/build_test_template.py``) to render new
     constraint names for the un-named
     ``sa.ForeignKeyConstraint`` calls in pre-C-43 migrations,
     which then breaks the later migrations that DROP those
     constraints by their original dialect-default names.

     Forward enforcement is instead manual: every new
     ``ForeignKey``, ``UniqueConstraint``, ``CheckConstraint`` in a
     model carries an explicit ``name=`` shaped by the convention.
     The contract is enforced by code review and by the regression
     test
     ``tests/test_models/test_c43_ondelete_and_naming_convention/test_the_contracts.py``
     which asserts the explicit-name rule for every ref-FK and the
     transfers.pay_period_id FK touched by this commit.

     The remaining ~35 Alembic-default ``<table>_<column>_fkey``
     names in pre-C-43 migrations are retained -- retroactive
     renames are high churn for cosmetic gain and Alembic
     ``compare_metadata`` matches FKs by structural signature, not
     by name, so the inconsistency does not produce phantom
     autogenerate diffs.

All operations are idempotent.  FK drop/recreate is guarded by a
``pg_constraint`` check for the new name with the new ondelete; if
both already match, the operation is skipped.  This makes the
migration safe to replay against any combination of databases: a
fresh test template that runs the chain to head, a production DB
that pre-dates these constraints, and a sandbox DB that has been
hand-corrected.

Downgrade is symmetric.  FK changes reverse to the original
ondelete (``NO ACTION`` for the seven that originally lacked an
explicit clause; ``NO ACTION`` for the two savings_goals FKs which
were also created without an explicit ondelete in
``4f2d894216ad``) and the original constraint name.  No data is
touched in either direction.

Review: solo developer, 2026-05-12 (audit 2026-04-15, C-43
retroactive sweep).  Destructive (audit tags D-12 + D-13 + D-14):
the upgrade drops and recreates 10 FK constraints (group 1 +
group 2), each classified under "constraint removal" and "rename"
by the destructive-migration policy in
``docs/coding-standards.md``.  Downgrade is symmetric and fully
working.

Revision ID: b4b588a49a0c
Revises: c42b1d9a4e8f
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "b4b588a49a0c"
down_revision = "c42b1d9a4e8f"
branch_labels = None
depends_on = None


# Ref-FK recreation specifications (F-073).  Each tuple is the data
# required to ``DROP CONSTRAINT <old>`` and ``ADD CONSTRAINT <new>``
# on the referencing table:
#
#   (source_schema, source_table, source_column,
#    target_schema, target_table, target_column,
#    legacy_name, new_name, new_ondelete)
#
# The two ``fk_savings_goals_*`` entries reuse the same name as the
# legacy because migration ``4f2d894216ad`` already created them
# with convention-matching names; only the ondelete clause changes.
# Every other entry advances the name from the Alembic-default
# ``<table>_<column>_fkey`` pattern to the convention-matching
# ``fk_<table>_<column_0_name>`` pattern.
REF_FK_SPECS: tuple[tuple[str, str, str, str, str, str, str, str, str], ...] = (
    (
        "ref", "account_types", "category_id",
        "ref", "account_type_categories", "id",
        "account_types_category_id_fkey",
        "fk_account_types_category_id",
        "RESTRICT",
    ),
    (
        "budget", "savings_goals", "goal_mode_id",
        "ref", "goal_modes", "id",
        "fk_savings_goals_goal_mode_id",
        "fk_savings_goals_goal_mode_id",
        "RESTRICT",
    ),
    (
        "budget", "savings_goals", "income_unit_id",
        "ref", "income_units", "id",
        "fk_savings_goals_income_unit_id",
        "fk_savings_goals_income_unit_id",
        "RESTRICT",
    ),
    (
        "salary", "paycheck_deductions", "calc_method_id",
        "ref", "calc_methods", "id",
        "paycheck_deductions_calc_method_id_fkey",
        "fk_paycheck_deductions_calc_method_id",
        "RESTRICT",
    ),
    (
        "salary", "paycheck_deductions", "deduction_timing_id",
        "ref", "deduction_timings", "id",
        "paycheck_deductions_deduction_timing_id_fkey",
        "fk_paycheck_deductions_deduction_timing_id",
        "RESTRICT",
    ),
    (
        "salary", "salary_profiles", "filing_status_id",
        "ref", "filing_statuses", "id",
        "salary_profiles_filing_status_id_fkey",
        "fk_salary_profiles_filing_status_id",
        "RESTRICT",
    ),
    (
        "salary", "salary_raises", "raise_type_id",
        "ref", "raise_types", "id",
        "salary_raises_raise_type_id_fkey",
        "fk_salary_raises_raise_type_id",
        "RESTRICT",
    ),
    (
        "salary", "state_tax_configs", "tax_type_id",
        "ref", "tax_types", "id",
        "state_tax_configs_tax_type_id_fkey",
        "fk_state_tax_configs_tax_type_id",
        "RESTRICT",
    ),
    (
        "salary", "tax_bracket_sets", "filing_status_id",
        "ref", "filing_statuses", "id",
        "tax_bracket_sets_filing_status_id_fkey",
        "fk_tax_bracket_sets_filing_status_id",
        "RESTRICT",
    ),
)


# transfers.pay_period_id realignment (F-136).  The constraint is
# dropped from RESTRICT and recreated as CASCADE under the
# convention-matching name.  Standalone tuple because the downgrade
# must restore RESTRICT (not NO ACTION) here.
TRANSFERS_PAY_PERIOD_SPEC: tuple[
    str, str, str, str, str, str, str, str, str
] = (
    "budget", "transfers", "pay_period_id",
    "budget", "pay_periods", "id",
    "transfers_pay_period_id_fkey",
    "fk_transfers_pay_period_id",
    "CASCADE",
)


# Confdeltype codes used in pg_constraint.confdeltype.  Only the
# four ondelete actions actually used in this project are listed --
# 'd' (SET DEFAULT) is part of the SQL standard but absent from the
# Shekel schema.
_CONFDELTYPE_TO_LABEL: dict[str, str] = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
}


def _constraint_exists(bind, schema: str, name: str) -> bool:
    """Return True iff ``schema.name`` is a constraint in ``pg_constraint``.

    Uses the schema-qualified ``pg_constraint`` lookup pattern
    established by C-42 (see
    ``c42b1d9a4e8f_c42_repair_salary_indexes_and_fk_naming.py``).
    The query joins ``pg_class`` and ``pg_namespace`` to honour the
    multi-schema layout -- ``pg_constraint`` itself is cluster-wide
    and a global ``WHERE conname = ?`` would return constraints
    from any schema, including the system catalog.

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute``.
        schema: Schema the constraint belongs to.
        name: Constraint name.

    Returns:
        True if a constraint with this name in this schema exists,
        False otherwise.
    """
    return bool(bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_constraint cn "
            "  JOIN pg_class c ON c.oid = cn.conrelid "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            "  WHERE cn.conname = :name AND n.nspname = :schema"
            ")"
        ),
        {"schema": schema, "name": name},
    ).scalar())


def _fk_ondelete(bind, schema: str, name: str) -> str | None:
    """Return the human-readable ondelete clause of an FK, or None.

    Reads ``pg_constraint.confdeltype`` and maps the single-character
    code to the SQL keyword that would round-trip through CREATE
    CONSTRAINT.  Returns ``None`` when the constraint is absent or
    not a foreign key (callers use the ``None`` return for the
    idempotency check -- "constraint missing" and "constraint present
    but wrong type" both flag the spec as not-yet-applied).

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute``.
        schema: Schema the constraint belongs to.
        name: Constraint name.

    Returns:
        ``"NO ACTION"``, ``"RESTRICT"``, ``"CASCADE"``, ``"SET NULL"``,
        or ``None`` when the FK is absent.  ``"SET DEFAULT"`` is not
        listed because the Shekel schema does not use it; encountering
        it would surface as ``KeyError`` from this helper, which is
        the desired behavior -- it would mean the upstream DDL drifted
        from the expectations encoded in the audit findings.
    """
    code = bind.execute(
        sa.text(
            "SELECT cn.confdeltype FROM pg_constraint cn "
            "JOIN pg_class c ON c.oid = cn.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE cn.conname = :name AND n.nspname = :schema "
            "AND cn.contype = 'f'"
        ),
        {"schema": schema, "name": name},
    ).scalar()
    if code is None:
        return None
    return _CONFDELTYPE_TO_LABEL[code]


def drop_and_recreate_fk(
    bind,
    source_schema: str,
    source_table: str,
    source_column: str,
    target_schema: str,
    target_table: str,
    target_column: str,
    legacy_name: str,
    new_name: str,
    new_ondelete: str,
) -> bool:
    """Drop the legacy FK constraint and recreate with new ondelete + name.

    The single-character return drives the idempotency contract:

      * Returns True iff DDL ran on this call (the typical first-run
        path: drop the legacy constraint, create the new one with
        the new name and ondelete).
      * Returns False iff the FK was already in the desired state
        (the typical second-run / forward-migrated path: new name
        present, ondelete already matches the spec).

    Both ``DROP CONSTRAINT`` and ``ADD CONSTRAINT`` run inside the
    migration's single transaction, so the window during which the
    FK is absent is invisible to concurrent statements -- a
    concurrent INSERT that would violate the constraint waits for
    the ADD to commit and then sees the FK as having existed all
    along (PostgreSQL's MVCC + DDL transactional semantics).

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute``.
        source_schema: Schema of the referencing table.
        source_table: Referencing table name.
        source_column: Referencing column name (single-column FKs
            only -- the Shekel schema has no composite ref FKs).
        target_schema: Schema of the referenced table.
        target_table: Referenced table name.
        target_column: Referenced column name (always ``id`` in
            practice; the parameter is here for symmetry).
        legacy_name: Current constraint name in pg_constraint.
        new_name: Constraint name to install after the rename.  May
            equal ``legacy_name`` when only the ondelete changes
            (the savings_goals FKs).
        new_ondelete: SQL keyword for the new ondelete clause --
            ``"RESTRICT"`` for the nine ref-FKs, ``"CASCADE"`` for
            the transfers.pay_period_id realignment.

    Returns:
        True iff DDL ran on this call.
    """
    # Idempotency: short-circuit when the new state is already in place.
    current_ondelete = _fk_ondelete(bind, source_schema, new_name)
    if current_ondelete == new_ondelete:
        return False
    # Drop the legacy constraint when it still exists.  The check
    # tolerates the partial-application state where the new
    # constraint was already created out-of-band but the old one
    # remains (this is unusual but produces a clean error path
    # rather than a Postgres "constraint does not exist" exception).
    if _constraint_exists(bind, source_schema, legacy_name):
        # Safe to interpolate: every value in REF_FK_SPECS and
        # TRANSFERS_PAY_PERIOD_SPEC is a module-level literal that
        # cannot contain SQL injection characters.
        op.execute(
            f"ALTER TABLE {source_schema}.{source_table} "
            f"DROP CONSTRAINT {legacy_name}"
        )
    # Create the new constraint.  ``op.create_foreign_key`` would
    # accept this through its Python API but emits the same ALTER
    # TABLE ADD CONSTRAINT DDL; the raw form is used here so the
    # constraint spec reads identically to a ``\d+`` PostgreSQL
    # rendering.
    op.execute(
        f"ALTER TABLE {source_schema}.{source_table} "
        f"ADD CONSTRAINT {new_name} "
        f"FOREIGN KEY ({source_column}) "
        f"REFERENCES {target_schema}.{target_table} ({target_column}) "
        f"ON DELETE {new_ondelete}"
    )
    return True


def assert_fk_ondelete(
    bind, schema: str, name: str, expected_ondelete: str,
) -> None:
    """Verify the FK has the expected ondelete clause after DDL.

    Closes the loop on the drop+recreate operations in group 1
    and group 2: if the ADD CONSTRAINT silently fell back to a
    different ondelete (caller bug, dialect oddity), this check
    fails loudly rather than letting a subtle catalog drift ship.

    Args:
        bind: SQLAlchemy ``Connection``-like object exposing
            ``execute``.
        schema: Schema the FK belongs to.
        name: FK constraint name.
        expected_ondelete: SQL keyword the catalog must report.

    Raises:
        RuntimeError: When the catalog reports a different ondelete
            or no FK at all.
    """
    actual = _fk_ondelete(bind, schema, name)
    if actual is None:
        raise RuntimeError(
            f"Post-recreate check failed: FK {schema}.{name} is "
            f"missing after the drop/recreate pass.  Inspect the "
            f"table with `\\d+ {schema}.<table>` and recreate the "
            f"FK by hand before retrying."
        )
    if actual != expected_ondelete:
        raise RuntimeError(
            f"Post-recreate check failed: FK {schema}.{name} has "
            f"ondelete={actual!r}, expected {expected_ondelete!r}.  "
            f"The ADD CONSTRAINT statement appears to have rendered "
            f"the wrong ondelete clause; inspect the migration body "
            f"and re-run after correcting."
        )


def upgrade():
    """Apply the C-43 sweep in two groups.

      1. Ref-FK drop+recreate with ondelete=RESTRICT and ``fk_*``
         names (9 constraints).  Post-recreate ondelete + presence
         assertion per constraint.
      2. transfers.pay_period_id drop+recreate with ondelete=CASCADE
         and ``fk_transfers_pay_period_id`` (1 constraint).
         Post-recreate ondelete + presence assertion.

    All operations run inside Alembic's single migration transaction;
    a failure in group 2 rolls back group 1 cleanly, no manual
    intervention required.
    """
    bind = op.get_bind()

    # Group 1: Ref-FK ondelete sweep.
    for (
        source_schema, source_table, source_column,
        target_schema, target_table, target_column,
        legacy_name, new_name, new_ondelete,
    ) in REF_FK_SPECS:
        drop_and_recreate_fk(
            bind,
            source_schema, source_table, source_column,
            target_schema, target_table, target_column,
            legacy_name, new_name, new_ondelete,
        )
        assert_fk_ondelete(bind, source_schema, new_name, new_ondelete)

    # Group 2: transfers.pay_period_id realignment.
    (
        src_schema, src_table, src_col,
        tgt_schema, tgt_table, tgt_col,
        legacy, new, new_action,
    ) = TRANSFERS_PAY_PERIOD_SPEC
    drop_and_recreate_fk(
        bind,
        src_schema, src_table, src_col,
        tgt_schema, tgt_table, tgt_col,
        legacy, new, new_action,
    )
    assert_fk_ondelete(bind, src_schema, new, new_action)


def downgrade():
    """Reverse the C-43 sweep.

    Order is the reverse of upgrade:

      1. Restore transfers.pay_period_id to ondelete=RESTRICT under
         its original Alembic-default name.
      2. Restore each ref-FK to its pre-C-43 ondelete (``NO ACTION``
         for the seven that originally had no explicit clause;
         ``NO ACTION`` for the two savings_goals FKs which were
         also created without an explicit ondelete in
         ``4f2d894216ad``).

    The downgrade is fully reversible because every operation is a
    pure DDL change: no data is modified, deleted, or transformed.
    Operators can take the schema back to the C-42 state losslessly.

    Review: solo developer, 2026-05-12 (audit 2026-04-15, C-43
    retroactive sweep).
    """
    bind = op.get_bind()

    # Reverse group 2: transfers.pay_period_id ondelete RESTORE to RESTRICT.
    (
        src_schema, src_table, src_col,
        tgt_schema, tgt_table, tgt_col,
        legacy, new, _new_action,
    ) = TRANSFERS_PAY_PERIOD_SPEC
    # Swap legacy <-> new and use the pre-C-43 ondelete (RESTRICT).
    drop_and_recreate_fk(
        bind,
        src_schema, src_table, src_col,
        tgt_schema, tgt_table, tgt_col,
        legacy_name=new,
        new_name=legacy,
        new_ondelete="RESTRICT",
    )

    # Reverse group 1: ref-FK ondelete restoration.  All nine FKs
    # had no explicit ondelete pre-C-43 (PostgreSQL implicit NO
    # ACTION).  Swap legacy <-> new and use NO ACTION.
    for (
        source_schema, source_table, source_column,
        target_schema, target_table, target_column,
        legacy_name, new_name, _new_ondelete,
    ) in REF_FK_SPECS:
        drop_and_recreate_fk(
            bind,
            source_schema, source_table, source_column,
            target_schema, target_table, target_column,
            legacy_name=new_name,
            new_name=legacy_name,
            new_ondelete="NO ACTION",
        )
