"""The R7c-b migration's PRE-FLIGHT guards, each shown to fire.

Plan step **R7c-b** (migration ``b6d41f0a9c27``) tightens four columns to
``NOT NULL``, completes ``ck_recurrence_rules_nominal_day`` with its clamp
equality, and adds ``ck_recurrence_rules_starts_on_range``.  Before each piece
of DDL it runs a Python guard that NAMES the rows the constraint would refuse,
because ``ALTER TABLE ... ADD CHECK`` reports the table and the constraint and
not the row: an operator meeting it mid-deploy would have a failed migration
and no way to see which rule to repair.

**Each guard grades a state the application cannot reach**, so it can never
fire in the suite by accident -- which is exactly why each is driven here,
with a control that shows it does NOT fire on healthy data
(``docs/plans/verification.md`` standard 4).

What this file covered until plan step R7c-c
---------------------------------------------

It graded the BACKFILL those two migrations share -- the SQL in
:mod:`migrations._recurrence_two_axis_backfill`, which derived the two-axis
columns from the closed-set ones -- against the write door as an independent
Python producer, over a matrix of schedules, cadences, clamp days and cycle
months, with a poison step as the firing control.

That grading is retired with the columns it read.  ``pattern_id``,
``day_of_month``, ``month_of_year``, ``start_date`` and ``start_period_id`` are
DROPPED (migration ``d9f5c1a48b73``), so the statement cannot be executed
against a database at head at all: not "it passes trivially", which would be
the free pass ``verification.md`` standard 3 warns about, but that it raises
``UndefinedColumn`` before reaching an assertion.

**The migrations themselves are unaffected and still exercised.**  Alembic runs
them at their own point in the chain, before the drop, on every build of the
test template -- and the evidence for what they did is in ``370a30cc`` and
``900e761a``, whose commit messages carry the measured matrix.  A migration is
graded where it executes; grading it against a schema three revisions ahead
would fail it for a change it cannot see.

Clock discipline (``.claude/rules/testing.md``): every date here is a literal.
"""
import pytest
import sqlalchemy as sa

from app.extensions import db
from tests._test_helpers import bare_expense_template
# The guards, IMPORTED rather than loaded from a revision by filename.
from migrations.versions import (  # noqa: E501  pylint: disable=line-too-long
    b6d41f0a9c27_the_two_axis_columns_become_authoritative as _R7CB,
)


def _ref_id(table: str, name: str) -> int:
    """Return a ``ref`` row's id by NAME, for a row this test must plant.

    The one place a test resolves a ``ref`` id from a string.  It is legitimate
    here and nowhere else: the row being built is one no application door can
    write, so ``ref_cache``'s enum-keyed accessors -- which is what production
    code must use -- have nothing to key on.

    Args:
        table: The ``ref`` table name.
        name: The row's ``name``.

    Returns:
        The row id.
    """
    return db.session.execute(
        sa.text(f"SELECT id FROM ref.{table} WHERE name = :name"),
        {"name": name},
    ).scalar_one()


def _plant_rule(seed_user, starts_on, nominal_day=None):
    """Insert one storable rule by raw SQL and return its id.

    Raw SQL rather than the write door, deliberately: every row these guards
    exist to find is one the door REFUSES, so authoring it would exercise the
    door and prove nothing about the guard.

    **The owning definition is built through the ORM, since plan step R-F6**:
    the rule carries a template's FK now under
    ``ck_recurrence_rules_one_owner``, and a raw INSERT with no owner would be
    refused by that constraint rather than reaching the guard these cases
    grade.  The definition is ordinary; only the RULE is planted by hand.

    Args:
        seed_user: The ``seed_user`` fixture dict, whose user owns the rule.
        starts_on: The rule's first occurrence, as an ISO date string.
        nominal_day: The nominal day to plant, or ``None``.

    Returns:
        int: The new rule's id.
    """
    return db.session.execute(sa.text(
        "INSERT INTO budget.recurrence_rules "
        "(transaction_template_id, interval_n, unit_id, unit_has_day_of_month, "
        " placement_id, shift_id, starts_on, nominal_day) "
        "VALUES (:owner, 1, :unit, "
        "        (SELECT has_day_of_month FROM ref.recurrence_units WHERE id = :unit), "
        "        :placement, :shift, CAST(:starts_on AS date), :nominal_day) "
        "RETURNING id"
    ), {
        "owner": bare_expense_template(db.session, seed_user).id,
        "unit": _ref_id("recurrence_units", "month"),
        "placement": _ref_id("period_placements", "containing_date"),
        "shift": _ref_id("business_day_shifts", "none"),
        "starts_on": starts_on,
        "nominal_day": nominal_day,
    }).scalar_one()


@pytest.mark.usefixtures("app")
def test_the_nominal_day_guard_names_the_rule_before_the_ddl_runs(db, seed_user):
    """``refuse_inconsistent_nominal_days`` finds the row and says which.

    The state is unreachable through the application -- every write door
    refuses the pair -- so anything this finds was hand-edited or restored.
    That is exactly why it must be driven from a test: it can never fire in
    the suite by accident.

    Planted with the CHECK dropped, because the completed constraint refuses
    the row this guard exists to REPORT: it is the migration's pre-flight, so
    the state it grades is one that exists only before the DDL runs.

    Args:
        db: The session fixture.
        seed_user: The owner fixture, whose account and category the rule's
            owning definition is built on.
    """
    db.session.execute(sa.text(
        "ALTER TABLE budget.recurrence_rules "
        "DROP CONSTRAINT ck_recurrence_rules_nominal_day"
    ))
    # April 15 was never clamped by anything, so a nominal day beside it names
    # a day the rule does not fire on -- the pair the completed CHECK closed.
    rule_id = _plant_rule(seed_user, "2026-04-15", nominal_day=30)
    db.session.flush()

    with pytest.raises(RuntimeError) as caught:
        _R7CB.refuse_inconsistent_nominal_days(db.session)

    message = str(caught.value)
    assert f"id={rule_id}" in message, "the refusal must name the offending rule"
    assert "2026-04-15" in message and "nominal_day=30" in message, (
        "and both halves of the pair, or an operator cannot repair it"
    )
    db.session.rollback()


@pytest.mark.usefixtures("app")
def test_the_nominal_day_guard_admits_a_real_clamp(db, seed_user):
    """The control: the guard grades the pair, not the presence of a day.

    April has no 31st, so ``(2026-04-30, 31)`` is precisely the state
    ``nominal_day`` exists for.  Without this arm a guard that refused every
    populated ``nominal_day`` -- or one whose query matched every row -- would
    pass the case above and abort a deploy on healthy data.

    Args:
        db: The session fixture.
        seed_user: The owner fixture, whose account and category the rule's
            owning definition is built on.
    """
    _plant_rule(seed_user, "2026-04-30", nominal_day=31)
    db.session.flush()

    # Must not raise; the assertion is the absence of the refusal.
    _R7CB.refuse_inconsistent_nominal_days(db.session)
    db.session.rollback()


@pytest.mark.usefixtures("app")
def test_the_calendar_window_guard_names_the_rule_before_the_ddl_runs(db, seed_user):
    """``refuse_out_of_range_starts`` finds a date past the app's calendar.

    ``ck_recurrence_rules_starts_on_range`` backs a MEASURED 500: past the
    saved horizon the pay calendar projects the covering paycheck by adding
    ``cadence_days`` to a start, so a date near ``date.max`` raises
    ``OverflowError`` from outside the recurrence package's error hierarchy.
    This is what names the rule that would block the constraint being added.

    Args:
        db: The session fixture.
        seed_user: The owner fixture, whose account and category the rule's
            owning definition is built on.
    """
    db.session.execute(sa.text(
        "ALTER TABLE budget.recurrence_rules "
        "DROP CONSTRAINT ck_recurrence_rules_starts_on_range"
    ))
    rule_id = _plant_rule(seed_user, "9999-12-31")
    db.session.flush()

    with pytest.raises(RuntimeError) as caught:
        _R7CB.refuse_out_of_range_starts(db.session)

    message = str(caught.value)
    assert f"id={rule_id}" in message, "the refusal must name the offending rule"
    assert "9999-12-31" in message, (
        "and the date, or an operator cannot see what to correct"
    )
    db.session.rollback()


@pytest.mark.usefixtures("app")
def test_the_calendar_window_guard_admits_a_date_inside_it(db, seed_user):
    """The control: a date the application's calendar reaches passes.

    Without it the case above would pass against a guard whose query matched
    every row, which would abort a deploy on every healthy database.

    Args:
        db: The session fixture.
        seed_user: The owner fixture, whose account and category the rule's
            owning definition is built on.
    """
    _plant_rule(seed_user, "2026-01-15")
    db.session.flush()

    # Must not raise; the assertion is the absence of the refusal.
    _R7CB.refuse_out_of_range_starts(db.session)
    db.session.rollback()
