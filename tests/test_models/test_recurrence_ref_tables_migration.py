"""Tests for the e7a4d95c2b18 two-axis recurrence ref-table migration.

Plan step **R2a** of ``docs/plans/implementation_plan_recurrence_redesign.md``
-- the vocabulary half of R2.  The migration creates and inline-seeds the
three ``ref`` lookup tables the redesign's two-axis model is expressed in
(``recurrence_units``, ``period_placements``, ``business_day_shifts``) and
changes nothing else: no ``budget.recurrence_rules`` column moves, and no
rule is read or written.

The migration is already at HEAD when these tests run (the template builder
upgraded base->head), so the per-worker DB shows the post-migration schema.
These tests assert, without re-executing DDL in the worker:

  * the migration is correctly chained (revision / down_revision);
  * each table exists with the declared shape -- an integer identity PK and a
    NOT NULL ``name`` carrying a UNIQUE constraint -- which is the proof that
    ``upgrade`` ran and matches the model.  The complementary "the models and
    the migration do not drift" check is the empty ``flask db migrate``
    autogenerate diff, run against the prod-clone dev DB during development;
  * ``upgrade`` actually EXECUTES each seed constant it defines -- the one
    dual-seed leg no runtime assertion can reach, because every test database
    is reseeded by ``seed_reference_data`` after being migration-built (see
    ``TestUpgradeExecutesEverySeed`` for why the production consequence is a
    failed deploy, not a missing row);
  * the inline seed is genuinely idempotent -- the migration's own SQL
    constants are re-executed here and must insert nothing;
  * neither the tables nor a trigger were added to the audited set (these are
    read-only seed catalogues, so ``AUDITED_TABLES`` deliberately excludes
    them, exactly as it excludes ``ref.statuses``);
  * ``downgrade`` is not a bare pass -- it drops every table ``upgrade``
    creates.

A full executable upgrade -> downgrade round-trip belongs in the
Alembic-driven environment, not an in-test xdist worker (executing the
downgrade here would DROP tables the whole session's ORM depends on, breaking
every other test in the worker).  The executable round-trip was run during
development against the prod-clone dev DB: ``flask db upgrade`` created and
seeded the three tables, ``flask db migrate`` then produced no diff for them,
``flask db downgrade`` dropped all three cleanly, and a re-upgrade re-seeded
them to the identical row set.  The source-level downgrade check below is the
safe in-worker analogue (the same split the ledger-account-kind schema
migration test uses).

Enum <-> row parity and the ``ref_cache`` accessors are covered in
``tests/test_ref_cache.py``; the "every enum value is inline-seeded by SOME
migration AND listed in ``app/ref_seeds.py``" three-legged scan lives in
``test_posting_ref_seed_parity.py``, which these enums are registered in.

"the identity sequence is ahead of its max id, so the next id-less INSERT
cannot collide" USED to be asserted here for these three tables.  Plan step
R-F1 generalised it to every ``ref`` table, discovered by query, in
``test_ref_identity_sequences.py``; these three are covered there and the
narrower copy was removed rather than left to duplicate it.
"""
from __future__ import annotations

import pathlib

from sqlalchemy import text

from app.audit_infrastructure import AUDITED_TABLES
from tests._test_helpers import load_migration_module


_MIGRATION_FILENAME = "e7a4d95c2b18_add_two_axis_recurrence_ref_tables.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_SOURCE = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()

#: The three tables the migration creates, with the declared ``name`` length.
#: Held as data so every shape assertion below runs against all three rather
#: than spot-checking one.
_TABLES: tuple[tuple[str, int], ...] = (
    ("recurrence_units", 10),
    ("period_placements", 30),
    ("business_day_shifts", 10),
)

#: The migration's inline-seed SQL constants, re-executed by the idempotency
#: test.  Read off the migration module itself rather than retyped, so the
#: test cannot drift from the statement it is checking.
_SEED_SQL: tuple[str, ...] = (
    _MIGRATION._SEED_RECURRENCE_UNITS_SQL,      # pylint: disable=protected-access
    _MIGRATION._SEED_PERIOD_PLACEMENTS_SQL,     # pylint: disable=protected-access
    _MIGRATION._SEED_BUSINESS_DAY_SHIFTS_SQL,   # pylint: disable=protected-access
)


class TestMigrationRevisionPair:
    """The migration chains off the X-f1e2 assertion-table head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "e7a4d95c2b18"
        assert _MIGRATION.down_revision == "b5e3d9c1a7f2"


class TestMigratedTableShape:
    """Each table exists at HEAD with an integer PK and a unique NOT NULL name."""

    def test_tables_exist_in_ref_schema(self, app, db):
        """All three tables are present in the ``ref`` schema at HEAD."""
        with app.app_context():
            for table, _ in _TABLES:
                found = db.session.execute(text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'ref' AND table_name = :t"
                ), {"t": table}).scalar()
                assert found == 1, f"ref.{table} missing at HEAD"

    def test_name_column_is_not_null_varchar_of_declared_length(self, app, db):
        """``name`` is a NOT NULL varchar of the model's declared length.

        The length is asserted per table because ``period_placements`` needs
        30 to hold ``period_starting_on_or_after`` (27 chars) while the other
        two are 10; a copy-paste of the 10 would truncate-fail that seed.
        """
        with app.app_context():
            for table, length in _TABLES:
                row = db.session.execute(text(
                    "SELECT data_type, is_nullable, character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'ref' AND table_name = :t "
                    "  AND column_name = 'name'"
                ), {"t": table}).fetchone()
                assert row is not None, f"ref.{table}.name missing at HEAD"
                assert row[0] == "character varying", (
                    f"ref.{table}.name is {row[0]}, expected character varying"
                )
                assert row[1] == "NO", f"ref.{table}.name must be NOT NULL"
                assert row[2] == length, (
                    f"ref.{table}.name is varchar({row[2]}), expected "
                    f"varchar({length})"
                )

    def test_id_is_the_primary_key(self, app, db):
        """``id`` is the sole primary-key column of each table."""
        with app.app_context():
            for table, _ in _TABLES:
                cols = db.session.execute(text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a "
                    "  ON a.attrelid = i.indrelid "
                    " AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = ('ref.' || :t)::regclass "
                    "  AND i.indisprimary"
                ), {"t": table}).scalars().all()
                assert cols == ["id"], (
                    f"ref.{table} primary key is {cols}, expected ['id']"
                )

    def test_name_is_unique(self, app, db):
        """``name`` carries a UNIQUE constraint on each table.

        The dual-seed pattern's idempotency depends on it: the migration's
        ``ON CONFLICT (name) DO NOTHING`` needs a unique index on ``name`` to
        have a conflict target at all, and ``ref_cache`` resolves each enum
        member by looking its value up by name.

        **Grouped by INDEX since plan step plan#2302**, which gave
        ``ref.recurrence_units`` a second unique index: the superkey
        ``uq_recurrence_units_id_has_day_of_month`` a rule's composite key
        targets (ruling plan#2748).  A flat list of every unique column read
        ``['name', 'id', 'has_day_of_month']`` there and could no longer say
        whether ``name`` had an index of its own, so each table's whole set of
        unique indexes is pinned instead: ``name`` alone everywhere, and the
        superkey beside it on the one table that carries it.
        """
        expected = {
            "recurrence_units": {("name",), ("id", "has_day_of_month")},
            "period_placements": {("name",)},
            "business_day_shifts": {("name",)},
        }
        with app.app_context():
            for table, _ in _TABLES:
                indexes = db.session.execute(text(
                    "SELECT array_agg(a.attname ORDER BY k.ord) "
                    "FROM pg_index i "
                    "CROSS JOIN LATERAL unnest(i.indkey) "
                    "  WITH ORDINALITY AS k(attnum, ord) "
                    "JOIN pg_attribute a "
                    "  ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
                    "WHERE i.indrelid = ('ref.' || :t)::regclass "
                    "  AND i.indisunique AND NOT i.indisprimary "
                    "GROUP BY i.indexrelid"
                ), {"t": table}).scalars().all()
                unique_sets = {tuple(columns) for columns in indexes}
                assert unique_sets == expected[table], (
                    f"ref.{table} unique indexes are {sorted(unique_sets)}, "
                    f"expected {sorted(expected[table])}"
                )


class TestInlineSeedIdempotent:
    """Re-running the migration's own seed SQL inserts nothing.

    The dual-seed pattern re-runs these statements every deploy (once from the
    migration on a fresh database, then from ``ref_seeds`` on every start), so
    a seed that is not idempotent would fail the deploy on the second run.
    Executing the migration's ACTUAL constants -- not a retyped copy -- is what
    makes this a test of the shipped statement.
    """

    def test_reexecuting_each_seed_inserts_no_rows(self, app, db):
        """Each seed statement reports zero rows inserted on a seeded DB."""
        with app.app_context():
            for statement in _SEED_SQL:
                before = db.session.execute(text(
                    "SELECT count(*) FROM ("
                    " SELECT id FROM ref.recurrence_units"
                    " UNION ALL SELECT id FROM ref.period_placements"
                    " UNION ALL SELECT id FROM ref.business_day_shifts"
                    ") AS all_rows"
                )).scalar()
                result = db.session.execute(text(statement))
                assert result.rowcount == 0, (
                    f"seed statement inserted {result.rowcount} row(s) on an "
                    f"already-seeded database; it is not idempotent:\n"
                    f"{statement}"
                )
                after = db.session.execute(text(
                    "SELECT count(*) FROM ("
                    " SELECT id FROM ref.recurrence_units"
                    " UNION ALL SELECT id FROM ref.period_placements"
                    " UNION ALL SELECT id FROM ref.business_day_shifts"
                    ") AS all_rows"
                )).scalar()
                assert after == before, (
                    f"row count moved {before} -> {after} while re-running an "
                    f"idempotent seed:\n{statement}"
                )
            db.session.rollback()


class TestNotAudited:
    """The three ref tables are deliberately outside the audited set.

    ``AUDITED_TABLES``'s inclusion criteria admit ``budget`` / ``salary`` /
    ``auth`` tables holding user-controlled state plus the one multi-tenant
    ref table (``ref.account_types``).  These three are read-only seed
    catalogues like ``ref.statuses``, so adding them would drown the trail in
    seed noise and change ``EXPECTED_TRIGGER_COUNT`` for no forensic gain.
    The plan document's section 3 says "all three new tables go into
    ``AUDITED_TABLES``"; measured against those criteria that is wrong for the
    ref tables, and this test pins the measured answer.
    """

    def test_ref_tables_are_not_in_audited_tables(self):
        """None of the three appears in ``AUDITED_TABLES``."""
        for table, _ in _TABLES:
            assert ("ref", table) not in AUDITED_TABLES, (
                f"ref.{table} is a read-only seed catalogue and must not be "
                f"audited (it would also change EXPECTED_TRIGGER_COUNT)."
            )

    def test_no_audit_trigger_is_attached(self, app, db):
        """No ``audit_*`` trigger exists on any of the three tables."""
        with app.app_context():
            for table, _ in _TABLES:
                triggers = db.session.execute(text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = ('ref.' || :t)::regclass "
                    "  AND NOT tgisinternal"
                ), {"t": table}).scalars().all()
                assert triggers == [], (
                    f"ref.{table} carries triggers {triggers}; the migration "
                    f"attaches none."
                )


class TestUpgradeExecutesEverySeed:
    """``upgrade`` actually runs each seed constant it defines.

    **The one dual-seed leg no runtime assertion can reach.**  Every test
    database is migration-built and THEN reseeded by ``seed_reference_data``
    (``scripts/build_test_template.py`` runs ``alembic upgrade head``, then
    the seed), so deleting the three ``op.execute(...)`` calls from
    ``upgrade`` leaves the whole suite green: the rows are present either
    way, and the idempotency test above would still see ``rowcount == 0``.

    **What the inline seed still protects.**  Until ruling R-BAL122 the deploy
    read the strict ``ref_cache`` straight after migrating and before seeding,
    so a created-but-unseeded ref table -- one that EXISTS with no row for its
    enum members -- aborted it with ``RuntimeError: ... RecurrenceUnit
    .PERIOD (expected name='period')``.  The deploy now seeds before that read
    (``scripts/init_database.py``'s ``_bring_to_release``), so the reference
    seed would supply the rows; what still needs them is any later migration
    in the chain that reads them, and every chain run with no seed after it (a
    bare ``flask db upgrade``).  The migration says it seeds; this pins that
    it does.

    Source-level, like the downgrade check below, because the behavioural
    proof would require re-running DDL inside an xdist worker.
    """

    def test_upgrade_executes_every_seed_constant(self):
        """Each ``_SEED_*_SQL`` constant is ``op.execute``d by ``upgrade``.

        A constant defined but never executed is the silent form of this
        failure: the migration reads as though it seeds, the suite stays
        green, and the deploy is the first thing that finds out.
        """
        upgrade_body = _MIGRATION_SOURCE[
            _MIGRATION_SOURCE.index("def upgrade()"):
            _MIGRATION_SOURCE.index("def downgrade()")
        ]
        for constant in (
            "_SEED_RECURRENCE_UNITS_SQL",
            "_SEED_PERIOD_PLACEMENTS_SQL",
            "_SEED_BUSINESS_DAY_SHIFTS_SQL",
        ):
            assert f"op.execute({constant})" in upgrade_body, (
                f"upgrade() defines {constant} but never executes it -- a "
                f"bare `flask db upgrade` would create the table empty, and "
                f"anything that reads it before a reference seed runs would "
                f"find no row for its enum members."
            )


class TestDowngradeDropsEverythingUpgradeCreates:
    """``downgrade`` is a real revert, not the bare ``pass`` the rules FAIL."""

    def test_downgrade_drops_all_three_tables(self):
        """The downgrade source drops each table the upgrade creates.

        Asserting the three exact ``op.drop_table`` calls subsumes the
        "not a stub" check the project's migration rules demand: a body
        carrying all three drops cannot also be a bare ``pass``.
        """
        source = _MIGRATION_SOURCE
        downgrade_body = source[source.index("def downgrade()"):]
        for table, _ in _TABLES:
            assert f'op.drop_table("{table}", schema="ref")' in downgrade_body, (
                f"downgrade does not drop ref.{table}"
            )
