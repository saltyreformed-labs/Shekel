"""Tests for the b2e9a47c3f18 "drop the closed pattern set's table" migration.

Plan step **R9** (shipped at ``800671a7``)
-- the last artefact of the closed pattern set.  The revision drops
``ref.recurrence_patterns``; the same commit deletes ``RecurrencePatternEnum``,
``ref_cache.recurrence_pattern_id`` and the ``app/ref_seeds.py`` entry.

Three subjects, and none of them re-runs the migration:

* **State at HEAD.**  The template builder upgraded base->head before these
  run, so "the table is gone" is asserted against the live catalogue.
* **The DROP is un-CASCADEd**, read from the AST.  That is the whole
  precondition of this step -- no inbound foreign key -- made structural: a
  plain ``DROP TABLE`` is refused by PostgreSQL while any FK depends on it, so
  the revision needs no hand-written guard and must not grow one that reaches
  for ``CASCADE``.
* **The downgrade is EXECUTED**, and it restores what the NEXT downgrade
  reads: ``d9f5c1a48b73`` is one step further down and its own downgrade
  re-seats every rule with
  ``SELECT id FROM ref.recurrence_patterns WHERE name = :pattern_name``, so
  this revision's reseed must carry every name that statement can look up.
  Graded against ``d9f5c1a48b73._PATTERN_BY_READING`` itself rather than
  against a list written here, which would be the second copy that drifts.

**Running the downgrade for real costs nothing, and an earlier draft of this
file said otherwise.**  It claimed DDL here would take locks against every
other xdist worker's database; the ``db`` fixture re-clones THIS worker's own
database for every test, so the CREATE reaches nobody else and survives no
other case.  Only the UPGRADE stays source-read, and for a different reason:
dropping the table would take it out from under the rest of the case.

**Three negative controls were driven against the real migration**, 2026-08-17,
because a downgrade test that has never been shown failing is a downgrade
nobody has checked:

* delete ``('Once')`` from the reseed -> three cases red
  (``..._the_whole_table_and_no_more``, ``..._the_retired_row_too``,
  ``..._reseed_really_is_idempotent``);
* delete ``ON CONFLICT (name) DO NOTHING`` -> exactly ONE case red, the
  idempotence one, which is what says that case grades the clause rather than
  the string;
* widen ``sa.String(length=20)`` to 40 -> exactly one case red, the shape one.

**What is still development-time evidence** is the round trip against real
data, recorded on the plan step: a production clone upgraded (8 rows -> table
absent), downgraded (table back, 8 rows), downgraded once further past
``d9f5c1a48b73`` (0 of 46 rules left with a NULL ``pattern_id``), and
re-upgraded.
"""
from __future__ import annotations

import ast
import pathlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.extensions import db
from tests._test_helpers import load_migration_module

_MIGRATION_FILENAME = "b2e9a47c3f18_drop_the_closed_pattern_sets_table.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)
_MIGRATION_SOURCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "migrations" / "versions" / _MIGRATION_FILENAME
).read_text(encoding="utf-8")

#: The revision one step further down the chain, whose downgrade READS the
#: table this one drops.  Imported rather than re-spelled so the cross-check
#: below grades the real statement.
_PREVIOUS = load_migration_module(
    "d9f5c1a48b73_the_closed_pattern_set_dies.py",
)

#: The one row that never had an enum member: plan step R2e-3 retired
#: ``RecurrencePatternEnum.ONCE`` and kept the row (ruling R-R11).  It is
#: absent from ``_PATTERN_BY_READING`` because no cadence reading maps to it --
#: "does not recur" is ``recurrence_rule_id IS NULL`` -- so it is named here,
#: which is the only value in this file not read off another module.
_RETIRED_PATTERN_NAME = "Once"


def _is_docstring(tree: ast.Module, node: ast.Constant) -> bool:
    """Return whether *node* is the docstring of some scope in *tree*.

    Args:
        tree: The parsed module.
        node: A string ``Constant`` found anywhere in it.

    Returns:
        ``True`` when the constant is a module, class or function docstring.
    """
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef)):
            continue
        first = scope.body[0] if scope.body else None
        if (isinstance(first, ast.Expr) and first.value is node):
            return True
    return False


class TestChaining:
    """The revision sits where it claims in the Alembic chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "b2e9a47c3f18"
        assert _MIGRATION.down_revision == "d9f5c1a48b73"

    def test_it_is_reviewed(self):
        """A destructive migration carries the ``Review:`` line the rules require.

        ``.claude/rules/database.md``: a migration that drops needs developer
        approval recorded in the module docstring.  Asserted because nothing
        else in the suite would notice the approval was missing.
        """
        assert "Review:" in _MIGRATION.__doc__


class TestTheTableIsGoneAtHead:
    """``ref.recurrence_patterns`` does not exist, and nothing named it."""

    def test_the_table_does_not_exist(self, app):
        """The upgrade's whole point, against the live catalogue."""
        with app.app_context():
            present = db.session.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                " WHERE table_schema = 'ref' AND table_name = :t"
            ), {"t": "recurrence_patterns"}).scalar_one()

        assert present == 0, (
            "ref.recurrence_patterns still exists at head; migration "
            "b2e9a47c3f18 drops it"
        )

    def test_no_column_anywhere_is_named_for_a_pattern(self, app):
        """No table kept a ``pattern`` column pointing at the dropped set.

        The drop is only lossless while nothing stores one of its ids, and
        ``budget.recurrence_rules.pattern_id`` (dropped at plan step R7c-c) was
        the last.  A column-name scan is the wider question than an FK scan:
        an id stored WITHOUT a foreign key would have survived the DROP
        silently, pointing at a table that no longer exists.
        """
        with app.app_context():
            columns = db.session.execute(text(
                "SELECT table_schema || '.' || table_name || '.' || "
                "       column_name "
                "  FROM information_schema.columns "
                " WHERE column_name LIKE '%pattern%' "
                "   AND table_schema IN "
                "       ('ref', 'auth', 'budget', 'salary', 'system')"
            )).scalars().all()

        assert columns == [], (
            f"a column still names a recurrence pattern: {columns}.  The "
            f"table those ids pointed at is dropped, so each is now a "
            f"dangling reference"
        )


class TestTheDropIsUnCascaded:
    """The refusal this step rests on is PostgreSQL's, not a Python guard.

    Parsed rather than grepped: ``CASCADE`` appears in this repository's
    migration prose often enough that a substring search over the file would
    accept a docstring mentioning it, and would miss the case that matters --
    a keyword argument on the call itself.
    """

    @staticmethod
    def _drop_calls():
        """Return every ``op.drop_table`` call node in ``upgrade``."""
        tree = ast.parse(_MIGRATION_SOURCE)
        upgrade = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        )
        return [
            node for node in ast.walk(upgrade)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "drop_table"
        ]

    def test_upgrade_drops_exactly_the_one_table(self):
        """One ``drop_table``, and it names ``ref.recurrence_patterns``."""
        calls = self._drop_calls()

        assert len(calls) == 1
        assert calls[0].args[0].value == "recurrence_patterns"
        schema = next(
            kw.value.value for kw in calls[0].keywords if kw.arg == "schema"
        )
        assert schema == "ref"

    def test_the_drop_names_no_cascade(self):
        """No ``CASCADE``, so an inbound FK makes the DROP itself refuse.

        The alternative -- a Python pre-check counting ``pg_constraint`` rows
        -- would be a second implementation of a refusal the database already
        makes structural, and the one that can rot.

        Both spellings are covered: a ``cascade=`` keyword on ``drop_table``
        and the word reaching executable code any other way (a hand-written
        ``op.execute`` of the DDL).  The module docstring says ``CASCADE``
        several times explaining why there is none, which is exactly why the
        second arm reads the module's CODE rather than its text.
        """
        for call in self._drop_calls():
            for keyword in call.keywords:
                assert keyword.arg != "cascade", (
                    "the DROP grew a cascade= argument, which would silently "
                    "take any table that came to reference this one"
                )

        tree = ast.parse(_MIGRATION_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _is_docstring(tree, node):
                    continue
                assert "CASCADE" not in node.value.upper(), (
                    f"a CASCADE reached executable code: {node.value!r}"
                )


class TestTheDowngradeRestoresWhatTheChainReads:
    """The downgrade is EXECUTED, and it restores what the chain below reads.

    **Executed rather than read from source, unlike the two classes above.**
    The ``db`` fixture re-clones the worker's own database for every test, so
    DDL here reaches no other worker and survives no other case -- which makes
    "the reseed is idempotent" and "the table comes back with production's
    shape" answerable by running them instead of by grepping the statement
    that would.  The upgrade's DROP is still source-read: it would take the
    table out from under the rest of the case.
    """

    @staticmethod
    def _run_downgrade(session) -> None:
        """Execute the revision's own ``downgrade`` against *session*.

        Installs Alembic's operation proxy over the test's connection, which
        is what lets the migration's module-level ``op`` calls run inside the
        per-test transaction rather than through ``flask db``.

        Args:
            session: The test session, whose transaction the DDL joins.
        """
        context = MigrationContext.configure(session.connection())
        with Operations.context(context):
            _MIGRATION.downgrade()

    @staticmethod
    def _restored_names(session) -> set[str]:
        """Return the names now in the restored table."""
        return set(session.execute(text(
            "SELECT name FROM ref.recurrence_patterns"
        )).scalars().all())

    def test_it_restores_every_pattern_the_previous_revision_LOOKS_UP(
        self, app, db,
    ):
        """``d9f5c1a48b73``'s downgrade cannot find a name this omits.

        That revision re-seats every rule on ``pattern_id`` by NAME, so a name
        missing here would make the row lookup return NULL and land a rule
        with no pattern at a revision where the column is ``NOT NULL`` -- a
        chain that walks down two steps and then dies, which no
        single-revision test would see.  Graded against that migration's own
        ``_PATTERN_BY_READING`` rather than a list written here, which would
        be the copy that drifts.
        """
        with app.app_context():
            self._run_downgrade(db.session)
            restored = self._restored_names(db.session)

        # pylint: disable=protected-access ## the statement under test.
        lookups = set(_PREVIOUS._PATTERN_BY_READING.values())

        assert lookups <= restored, (
            f"b2e9a47c3f18's downgrade does not restore "
            f"{sorted(lookups - restored)}, which d9f5c1a48b73's downgrade "
            f"looks up by name"
        )

    def test_it_restores_the_retired_row_too(self, app, db):
        """``Once`` comes back, though no cadence reading names it.

        It is not in ``_PATTERN_BY_READING`` -- no rule could still be a
        ``Once`` rule after plan step R2e-3 deleted them all -- but the ROW
        was in the table this revision dropped, and a downgrade that restored
        seven of eight rows would leave the schema subtly different from the
        one the target revision was built against.
        """
        with app.app_context():
            self._run_downgrade(db.session)

            assert _RETIRED_PATTERN_NAME in self._restored_names(db.session)

    def test_it_restores_the_whole_table_and_no_more(self, app, db):
        """Exactly eight names, which is what production held.

        Measured 2026-08-17 on the production database at ``d9f5c1a48b73``:
        8 rows.  Asserted as an exact set rather than a lower bound so a ninth
        name -- a value the table never carried -- is a failure rather than a
        silent widening of what a downgrade restores.
        """
        with app.app_context():
            self._run_downgrade(db.session)
            restored = self._restored_names(db.session)

        # pylint: disable=protected-access ## the statement under test.
        expected = set(_PREVIOUS._PATTERN_BY_READING.values()) | {
            _RETIRED_PATTERN_NAME,
        }

        assert len(restored) == 8, sorted(restored)
        assert restored == expected

    def test_the_reseed_really_is_idempotent(self, app, db):
        """Running the reseed a SECOND time neither raises nor duplicates.

        ``ON CONFLICT (name) DO NOTHING`` is what makes a downgrade re-run
        after a partial failure complete rather than die on the UNIQUE, and
        the clause is worth executing rather than grepping: a substring check
        passes for one pasted into the wrong statement.
        """
        with app.app_context():
            self._run_downgrade(db.session)
            # pylint: disable=protected-access ## the statement under test.
            db.session.execute(text(_MIGRATION._RESEED_SQL))

            assert len(self._restored_names(db.session)) == 8

    def test_the_restored_table_has_the_shape_production_had(self, app, db):
        """Two columns, a PK and a UNIQUE, at the types the table carried.

        ``name`` is ``varchar(20)`` because that is what the initial schema
        created and what production held (measured 2026-08-17); a downgrade
        that widened it would leave the target revision with a column its own
        model does not describe.  The constraint NAMES are PostgreSQL's
        generated ones, which is the convention for a single-column ``ref``
        lookup key (developer ruling 2026-08-14, ledger row ``recurrence:F-3``).
        """
        with app.app_context():
            self._run_downgrade(db.session)

            columns = db.session.execute(text("""
                SELECT column_name, data_type, character_maximum_length,
                       is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = 'ref'
                   AND table_name = 'recurrence_patterns'
                 ORDER BY ordinal_position
            """)).all()
            # Keys only: PostgreSQL 18 also lists a NOT NULL constraint per
            # column here (``contype = 'n'``), and nullability is asserted
            # from ``information_schema`` above rather than twice.  Foreign
            # keys are in the filter deliberately -- the restored table must
            # come back with none, which is what makes the re-DROP legal.
            constraints = db.session.execute(text("""
                SELECT conname, contype FROM pg_constraint
                 WHERE conrelid = 'ref.recurrence_patterns'::regclass
                   AND contype IN ('p', 'u', 'f')
                 ORDER BY conname
            """)).all()

        assert [tuple(row) for row in columns] == [
            ("id", "integer", None, "NO"),
            ("name", "character varying", 20, "NO"),
        ]
        assert [tuple(row) for row in constraints] == [
            ("recurrence_patterns_name_key", "u"),
            ("recurrence_patterns_pkey", "p"),
        ]
