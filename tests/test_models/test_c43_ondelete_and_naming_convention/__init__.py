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
model carries an explicit ``name=`` shaped by the convention.
``TestNamingConventionContract``, in ``test_the_contracts.py``,
asserts the explicit-name rule for every FK touched by this commit.

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
     ``drop_and_recreate_fk`` and ``assert_fk_ondelete`` helpers
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
