"""Tests for the c8f2b6a41d93 recurrence anchor-subtype migration.

Plan step **R2b** of ``docs/plans/implementation_plan_recurrence_redesign.md``,
as amended by plan step **R2d**.  The migration adds ``max_occurrences`` to
``budget.recurrence_rules`` with its two bound CHECKs, creates the two 0-or-1
anchor subtype tables, and audits them.  It writes no data at all.

Two groups, and the split is deliberate:

* **Schema at HEAD.**  The migration is already applied when these run (the
  template builder upgraded base->head), so the column and its constraints
  are asserted against the live catalogue rather than by re-running DDL in an
  xdist worker.  Every CHECK is additionally shown to REJECT the value it
  names -- a constraint nobody has seen fire is a constraint nobody knows is
  wired up.
* **The downgrade is a reversal**, read from the migration's AST.

**Both subtype tables are gone at HEAD, and the cases that graded them went
with them.**  ``budget.recurrence_month_anchors`` was dropped unwritten at plan
step R7c-c (migration ``d9f5c1a48b73``, ruling **R-R16**: the clamped day is
``recurrence_rules.nominal_day``), and ``budget.recurrence_weekday_anchors``
at plan step **R8-c** (migration ``897a6663d221``, rulings **R-R25** and
**plan#2668**: the rule states how its first date is read in
``recurrence_rules.weekday_reading``).  The emptiness, primary-key, uniqueness,
cascade, domain and audit cases this file carried for the weekday table grade
a relation that no longer exists; what replaced the table is graded where it
lives -- ``test_recurrence_rule_constraints.TestTheWeekdayReadingIsHeldToItsDate``
for its CHECKs, and ``test_a_rule_states_how_its_first_date_is_read_migration``
for the drop's refusal and a downgrade that restores the table with its
audit trigger and proves the trigger fires.

**What this file no longer covers, and where that coverage went.**  An earlier
version of this migration added four derived columns and backfilled all 50
live rules, so this file carried ~600 lines unit-testing a frozen COPY of the
derivation that lives in ``app.services.recurrence``.  Plan step R2d removed
the columns and the backfill with them, and the copy went too.  The derivation
itself is covered -- more thoroughly than the copy ever was -- by
``tests/test_services/test_recurrence_resolution.py``, which exercises the
real function at exact dates: six first-of-month cases where this file had
three, plus month-end clamping, totality past the horizon, and the malformed
input coercions.

The executable upgrade -> downgrade -> re-upgrade round trip is a
development-time step: it was run against the prod-clone dev database while
building R2d (13 columns -> 12 -> 13, 2 subtype tables -> 0 -> 2).  Executing
a downgrade inside an xdist worker would drop tables the whole session's ORM
depends on, so :class:`TestDowngradeIsAReversal` reads the migration's AST
instead -- parsed rather than grepped, because a name in a comment satisfies a
substring search.
"""
from __future__ import annotations

import ast
import pathlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests._test_helpers import bare_expense_template, load_migration_module

_MIGRATION_FILENAME = "c8f2b6a41d93_add_recurrence_anchor_subtypes.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_SOURCE = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()

#: The one column the migration adds, nullable and unwritten until step R8.
_NEW_COLUMNS: tuple[str, ...] = ("max_occurrences",)

#: The SELECT list every rule INSERT below shares: a storable Monthly rule.
#:
#: **Plan step R7c-b made ``unit_id``, ``placement_id``, ``shift_id`` and
#: ``starts_on`` NOT NULL**, so the four-column INSERT these cases used to make
#: is no longer storable.  Stating them once matters here beyond tidiness: the
#: cases below assert that a SPECIFIC constraint refuses a row, and a null in
#: any of the four raises the same ``IntegrityError`` from a different cause --
#: so six copies drifting apart would let a case pass without ever reaching the
#: constraint it names.
#:
#: The values name an ordinary monthly cadence starting 2026-01-15.  The
#: migration under test writes no data and reads none of them.
#:
#: **They were a SELECT list until plan step R9**, over a one-row
#: ``FROM ref.recurrence_patterns WHERE name = 'Monthly'`` used for nothing but
#: producing exactly one row.  R9 dropped that table; a ``VALUES`` clause is
#: what the statement always meant, and the scalar subqueries below are legal
#: there unchanged.
#: **``user_id`` became ``transaction_template_id`` at plan step R-F6**, which
#: moved the owning FK onto this table: a rule belongs to exactly one
#: definition (``ck_recurrence_rules_one_owner``), so the ``:u`` parameter
#: below binds a TEMPLATE id rather than a user id.  The callers pass one from
#: ``bare_expense_template``.
_STORABLE_RULE_COLUMNS = (
    "transaction_template_id, interval_n, unit_id, placement_id, shift_id, "
    "starts_on"
)
_STORABLE_RULE_VALUES = (
    ":u, 1, "
    "(SELECT id FROM ref.recurrence_units WHERE name = 'month'), "
    "(SELECT id FROM ref.period_placements WHERE name = 'containing_date'), "
    "(SELECT id FROM ref.business_day_shifts WHERE name = 'none'), "
    "DATE '2026-01-15'"
)


def _insert_rule_sql(extra_columns="", extra_values=""):
    """Return an INSERT that lands one storable rule, plus *extra_columns*.

    Args:
        extra_columns: Columns to append to :data:`_STORABLE_RULE_COLUMNS`,
            leading comma included.
        extra_values: The matching VALUES entries, leading comma included.

    Returns:
        str: The SQL, taking a ``:u`` OWNING-TEMPLATE parameter.
    """
    return (
        f"INSERT INTO budget.recurrence_rules "
        f"  ({_STORABLE_RULE_COLUMNS}{extra_columns}) "
        f"VALUES ({_STORABLE_RULE_VALUES}{extra_values})"
    )


class TestMigrationRevisionPair:
    """The migration chains off the R2a vocabulary head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "c8f2b6a41d93"
        assert _MIGRATION.down_revision == "e7a4d95c2b18"


class TestNewColumnShape:
    """``max_occurrences`` exists at HEAD, nullable."""

    def test_every_new_column_exists_and_is_nullable(self, app, db):
        """Nullable because it is genuinely optional, not because it is staged.

        ``max_occurrences`` is one of the two mutually exclusive closing
        bounds, so NULL is a real value meaning "not count-bounded" -- unlike
        the columns plan step R7c will add NOT NULL.
        """
        with app.app_context():
            for column in _NEW_COLUMNS:
                row = db.session.execute(text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'budget' "
                    "  AND table_name = 'recurrence_rules' "
                    "  AND column_name = :c"
                ), {"c": column}).fetchone()
                assert row is not None, (
                    f"budget.recurrence_rules.{column} missing at HEAD"
                )
                assert row[0] == "YES", (
                    f"{column} is NOT NULL; it is an optional closing bound "
                    f"and NULL means 'not count-bounded'."
                )


class TestRuleCheckConstraintsReject:
    """Each new CHECK is shown to refuse the value it names.

    A constraint asserted only by presence in ``pg_constraint`` is a
    constraint nobody has seen fire.  Each test writes the forbidden row and
    requires the database to refuse it.
    """

    def test_both_end_bounds_at_once_is_refused(self, app, db, seed_user):
        """``end_date`` and ``max_occurrences`` together are rejected.

        Two answers to "when does this stop" is a question the engine would
        have to break a tie on; the schema refuses the question instead.
        """
        with app.app_context():
            with pytest.raises(IntegrityError, match="single_end_bound"):
                db.session.execute(text(_insert_rule_sql(
                    extra_columns=", end_date, max_occurrences",
                    extra_values=", DATE '2026-12-31', 12",
                )), {"u": bare_expense_template(db.session, seed_user).id})
            db.session.rollback()

    def test_zero_max_occurrences_is_refused(self, app, db, seed_user):
        """A count bound of zero would mean "never", which NULL already means."""
        with app.app_context():
            with pytest.raises(IntegrityError, match="positive_max_occurrences"):
                db.session.execute(text(_insert_rule_sql(
                    extra_columns=", max_occurrences", extra_values=", 0",
                )), {"u": bare_expense_template(db.session, seed_user).id})
            db.session.rollback()


class TestDowngradeIsAReversal:
    """``downgrade`` undoes every part of ``upgrade``.

    Parsed from the AST rather than grepped for a quoted string: an earlier
    version asserted ``'"unit_id"' in downgrade_body``, which a mention in a
    COMMENT satisfies.  Executing the real downgrade is not possible in an
    xdist worker -- it would drop tables the whole session's ORM depends on --
    so the executable round trip is a development-time step, run against the
    prod-clone dev database while building R2d.

    The reversal is EXACT rather than best-effort because this revision writes
    no data: both subtype tables are created empty and stay empty until plan
    steps R7c and R8, and ``max_occurrences`` has no writer until R8.  There
    is nothing for a downgrade to reconstruct.
    """

    @staticmethod
    def _downgrade_calls(function_name):
        """Return the literal string arguments of each ``op.<fn>(...)`` call.

        Walks ``downgrade``'s AST so a name appearing in a comment, a
        docstring or an unrelated string cannot satisfy an assertion.
        """
        tree = ast.parse(_MIGRATION_SOURCE)
        body = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )
        calls = []
        for node in ast.walk(body):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr == function_name
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "op"):
                continue
            calls.append([
                arg.value for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ])
        return calls

    def test_it_drops_every_column_the_upgrade_adds(self):
        """The column is the target of a real ``drop_column``."""
        dropped = {
            args[1] for args in self._downgrade_calls("drop_column")
            if len(args) >= 2 and args[0] == "recurrence_rules"
        }
        assert dropped == set(_NEW_COLUMNS), (
            f"downgrade drops {sorted(dropped)} from budget.recurrence_rules, "
            f"expected {sorted(_NEW_COLUMNS)}"
        )

    def test_it_drops_both_subtype_tables(self):
        """Dropping each table takes its audit trigger with it.

        Read against THIS MIGRATION'S own two tables rather than against the
        set that survives at head, and the difference is the point: plan step
        R7c-c dropped ``budget.recurrence_month_anchors`` in a LATER revision,
        which does not change what ``c8f2b6a41d93`` created or what its
        downgrade must undo.  A migration is reversible at its own point in the
        chain; grading it against a schema three revisions ahead would fail it
        for a change it cannot see.
        """
        dropped = {
            args[0] for args in self._downgrade_calls("drop_table") if args
        }
        assert dropped == {
            "recurrence_weekday_anchors", "recurrence_month_anchors",
        }, (
            f"downgrade drops tables {sorted(dropped)}, expected both subtypes"
        )

    def test_it_drops_only_the_constraints_the_upgrade_added(self):
        """Both new CHECKs go, and nothing pre-existing is touched."""
        dropped = {
            args[0] for args in self._downgrade_calls("drop_constraint")
            if args
        }
        assert dropped == {
            "ck_recurrence_rules_single_end_bound",
            "ck_recurrence_rules_positive_max_occurrences",
        }, f"downgrade drops constraints {sorted(dropped)}"

    # ``test_the_upgrade_adds_no_derived_column`` was DELETED at plan step
    # R7c-a, which its own docstring instructed ("delete both at plan step
    # R7c").  It was the AST sibling of ``TestTheDerivedColumnsAreAbsent``:
    # both asserted that ``unit_id`` / ``anchor_date`` / ``placement_id`` /
    # ``shift_id`` were not columns, on the R2d ruling that a derivation must
    # not sit beside its own inputs.  R7c-a adds them under an expand /
    # migrate / contract -- nothing reads them until R7c-b, and from R7c-b
    # they are authored -- so what the pair guarded is no longer a state to
    # refuse.  Their coverage inverted into
    # ``test_recurrence_two_axis_backfill.py``, which asserts the columns
    # arrived with the right shape and hold what the write door would write.
