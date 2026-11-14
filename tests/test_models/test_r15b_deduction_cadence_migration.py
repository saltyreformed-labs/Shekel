"""Migration ``542c61e48ee8`` -- a payroll deduction's cadence is a recurrence rule.

Plan step **salary:R15-b** (rulings **R-SAL3**, **R-SAL29**, **R-SAL30**,
**R-SAL32**).  The migration adds the third arm of ``budget.recurrence_rules``'
owning arc, writes one rule per 24 / 12 line in the two shapes its docstring
spells, and drops ``salary.paycheck_deductions.deductions_per_year``; its
downgrade rebuilds the column from those two shapes and REFUSES any other
deduction-owned shape, because writing 26 for a line the owner said is
monthly moves money.  Both directions are driven through the migration's own
shipped callables, and every assertion reads the DATABASE for the objects --
and, for the rewrite, prices the migrated line through the engine against
the ordinal rule it replaced, which is the byte-identical claim in miniature.

**Every case that drives this revision's callables runs them under plan step
``salary:R18-a``'s downgrade** (:func:`~tests._test_helpers
.rewind_paycheck_lines_rename`, Alembic's newest-first order): head renamed
``salary.paycheck_deductions`` and the arm's column (ruling **R-SAL38**), so
the statements here find the schema they were written against only once the
later revision is undone -- and its upgrade is replayed before a line is read
back through the models this tree maps.  The one case that reads HEAD's
schema names the head objects.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.ref import CalcMethod, PaycheckLineKind
from app.services.pay_calendar import calendar_for, paydays_in_month_through
from app.services.payroll_basis import PayrollBasis
from app.services.recurrence import RecurrenceSpec, author_rule
from tests._test_helpers import (
    load_migration_module,
    make_line_cadence_rule,
    make_salary_profile,
    replay_paycheck_lines_rename,
    restore_rule_due_day_column,
    rewind_paycheck_lines_rename,
    run_migration_callable as _run,
)
from tests.test_models._rule_unit_flag import (
    replay_rule_unit_flag,
    rewind_rule_unit_flag,
)


def _rewind(db_session):
    """Undo head's schema back past what this revision's SQL reads, newest first.

    Plan step ``recurrence:R5-a`` dropped ``recurrence_rules.due_day_of_month``,
    which this revision's own SQL reads (``_DEDUCTION_RULES``, ``_unadorned``),
    and plan step ``salary:R18-a``/``R18-b``'s rename rewind after it.  Plan step
    plan#2302's NOT NULL flag goes first of all, being the newest: this revision's
    INSERT states no such column.

    Args:
        db_session: The test ``db.session``.
    """
    rewind_rule_unit_flag(db_session)
    restore_rule_due_day_column(db_session)
    rewind_paycheck_lines_rename(db_session)


def _replay(db_session):
    """Bring back what the ORM models map, for a case that reads through them.

    The head names, then the flag; the due day stays, the model not mapping it.

    Args:
        db_session: The test ``db.session``.
    """
    replay_paycheck_lines_rename(db_session)
    replay_rule_unit_flag(db_session)

#: This revision, loaded so its own callables are what this file drives.
_M_R15B = load_migration_module(
    "542c61e48ee8_a_deductions_cadence_is_a_rule.py",
)

#: This revision's objects, spelled as it wrote them -- the names the schema
#: carries under the R18-a rewind every driven case runs in.
_ARM = "paycheck_deduction_id"
_ARM_FK = "fk_recurrence_rules_paycheck_deduction_id"
_ARM_INDEX = "uq_recurrence_rules_paycheck_deduction_id"
_ARC = "ck_recurrence_rules_one_owner"
_OLD_COLUMN = "deductions_per_year"
_OLD_CHECK = "ck_paycheck_deductions_positive_per_year"
_LINES = "paycheck_deductions"
#: The same objects under HEAD's names (plan step salary:R18-a).
_HEAD_ARM = "paycheck_line_id"
_HEAD_ARM_FK = "fk_recurrence_rules_paycheck_line_id"
_HEAD_ARM_INDEX = "uq_recurrence_rules_paycheck_line_id"
_HEAD_LINES = "paycheck_lines"


def _columns(session, schema, table) -> set[str]:
    """Return a table's column names, read from the catalogue."""
    return {
        row[0] for row in session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table"
        ), {"schema": schema, "table": table})
    }


def _constraints(session, schema, table) -> dict[str, str]:
    """Return a table's constraints as ``{name: definition}``."""
    return dict(session.execute(text(
        "SELECT conname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "  JOIN pg_class t ON t.oid = c.conrelid "
        "  JOIN pg_namespace n ON n.oid = t.relnamespace "
        " WHERE n.nspname = :schema AND t.relname = :table"
    ), {"schema": schema, "table": table}).all())


def _indexes(session) -> set[str]:
    """Return the unique-owner index names on ``budget.recurrence_rules``."""
    return {
        row[0] for row in session.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'budget' "
            "AND tablename = 'recurrence_rules' AND indexname LIKE 'uq_recurrence_rules_%'"
        ))
    }


def _deduction_rules(session) -> list:
    """Every deduction-owned rule with the columns the two shapes are told apart by."""
    return session.execute(text("""
        SELECT r.paycheck_deduction_id, u.name AS unit, r.interval_n,
               p.name AS placement, r.max_per_month, r.starts_on
          FROM budget.recurrence_rules r
          JOIN ref.recurrence_units u ON u.id = r.unit_id
          JOIN ref.period_placements p ON p.id = r.placement_id
         WHERE r.paycheck_deduction_id IS NOT NULL
         ORDER BY r.paycheck_deduction_id
    """)).all()


def _seed_lines(seed_user, names):
    """A salary profile with one flat pre-tax line per name; returns ``{name: id}``."""
    profile = make_salary_profile(seed_user, db.session)
    db.session.flush()
    timing = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one().id
    method = db.session.query(CalcMethod).filter_by(name="flat").one().id
    ids = {}
    for name in names:
        line = PaycheckLine(
            salary_profile_id=profile.id, paycheck_line_kind_id=timing,
            calc_method_id=method, name=name, amount=Decimal("100.00"),
        )
        db.session.add(line)
        db.session.flush()
        ids[name] = line.id
    db.session.commit()
    return profile, ids


def _set_old_counts(session, counts):
    """Write ``deductions_per_year`` by id, on a tree where the column exists."""
    for line_id, count in counts.items():
        session.execute(
            text("UPDATE salary.paycheck_deductions SET deductions_per_year = :n WHERE id = :id"),
            {"n": count, "id": line_id},
        )
    session.commit()


@pytest.mark.usefixtures("seed_periods")
class TestTheRoundTrip:
    """The template carries the arm and not the column; down then up restores exactly that."""

    def test_the_upgrade_left_the_arm_and_took_the_column(self, app, db):
        """Every object the migration adds is present, and the column is gone."""
        with app.app_context():
            assert _HEAD_ARM in _columns(db.session, "budget", "recurrence_rules")
            constraints = _constraints(db.session, "budget", "recurrence_rules")
            assert _HEAD_ARM_FK in constraints
            assert "ON DELETE CASCADE" in constraints[_HEAD_ARM_FK]
            assert "(paycheck_line_id IS NOT NULL)" in constraints[_ARC]
            assert _HEAD_ARM_INDEX in _indexes(db.session)
            assert _OLD_COLUMN not in _columns(db.session, "salary", _HEAD_LINES)
            for name in _constraints(db.session, "salary", _HEAD_LINES):
                assert "per_year" not in name, name

    def test_down_then_up_restores_exactly_the_objects(self, app, db):
        """With no deduction rule the downgrade rebuilds the old shape and the upgrade the new."""
        with app.app_context():
            _rewind(db.session)
            rules_before = _constraints(db.session, "budget", "recurrence_rules")
            deds_before = _constraints(db.session, "salary", _LINES)
            rule_columns_before = _columns(db.session, "budget", "recurrence_rules")
            ded_columns_before = _columns(db.session, "salary", _LINES)

            _run(_M_R15B.downgrade, db.session)

            assert _ARM not in _columns(db.session, "budget", "recurrence_rules")
            assert _ARM_INDEX not in _indexes(db.session)
            down_rules = _constraints(db.session, "budget", "recurrence_rules")
            assert _ARM_FK not in down_rules
            assert "<>" in down_rules[_ARC], "the two-arm XOR was not restored"
            assert _OLD_COLUMN in _columns(db.session, "salary", _LINES)
            assert _OLD_CHECK in _constraints(db.session, "salary", _LINES)

            _run(_M_R15B.upgrade, db.session)

            assert _columns(db.session, "budget", "recurrence_rules") == rule_columns_before
            assert _columns(db.session, "salary", _LINES) == ded_columns_before
            assert _constraints(db.session, "budget", "recurrence_rules") == rules_before
            assert _constraints(db.session, "salary", _LINES) == deds_before
            _replay(db.session)


@pytest.mark.usefixtures("seed_periods")
class TestTheUpgradeRewritesEachLine:
    """26 -> no rule; 24 -> at most 2 a month from the opening payday; 12 -> monthly, first paycheck."""

    def test_the_three_values_become_the_two_shapes_and_nothing(self, app, db, seed_user):
        """Each stored count is re-expressed exactly as the migration's docstring spells."""
        with app.app_context():
            _profile, ids = _seed_lines(seed_user, ["every", "twenty_four", "twelve"])
            _rewind(db.session)
            _run(_M_R15B.downgrade, db.session)
            _set_old_counts(db.session, {
                ids["every"]: 26, ids["twenty_four"]: 24, ids["twelve"]: 12,
            })

            _run(_M_R15B.upgrade, db.session)

            opening = calendar_for(seed_user["user"].id).opening_bound()
            rules = {row.paycheck_deduction_id: row for row in _deduction_rules(db.session)}
            assert set(rules) == {ids["twenty_four"], ids["twelve"]}, (
                "a 26 line must carry NO rule (R-SAL3's NULL)"
            )
            twenty_four = rules[ids["twenty_four"]]
            assert (twenty_four.unit, twenty_four.interval_n, twenty_four.placement,
                    twenty_four.max_per_month, twenty_four.starts_on) == (
                "period", 1, "containing_date", 2, opening,
            )
            twelve = rules[ids["twelve"]]
            assert (twelve.unit, twelve.interval_n, twelve.placement,
                    twelve.max_per_month, twelve.starts_on) == (
                "month", 1, "period_starting_on_or_after", None, opening.replace(day=1),
            )
            assert _OLD_COLUMN not in _columns(db.session, "salary", _LINES)
            _replay(db.session)

    def test_the_migrated_rules_admit_exactly_the_paydays_the_ordinal_rule_did(
        self, app, db, seed_user,
    ):
        """The engine's membership under the rule equals the retired ordinal rule, payday by payday.

        The byte-identical claim, graded on the seeded calendar rather than
        quoted: over every payday the owner's calendar holds, a migrated 24
        is taken exactly where the month ordinal is 1 or 2, and a migrated 12
        exactly where it is 1 -- the two predicates ``_deduction_applies_at``
        applied until this step, re-derived here from the same
        ``paydays_in_month_through`` producer.
        """
        with app.app_context():
            profile, ids = _seed_lines(seed_user, ["twenty_four", "twelve"])
            _rewind(db.session)
            _run(_M_R15B.downgrade, db.session)
            _set_old_counts(db.session, {ids["twenty_four"]: 24, ids["twelve"]: 12})
            _run(_M_R15B.upgrade, db.session)
            _replay(db.session)

            db.session.expire_all()
            profile = db.session.get(type(profile), profile.id)
            calendar = calendar_for(seed_user["user"].id)
            basis = PayrollBasis(profile, calendar)
            by_name = {line.name: line for line in profile.lines}
            paydays = [period.start_date for period in calendar.periods]
            assert len(paydays) >= 3
            for payday in paydays:
                ordinal = len(paydays_in_month_through(calendar, payday))
                assert basis.line_applies_on(by_name["twenty_four"], payday) is (
                    ordinal < 3
                ), f"24-line on {payday} (ordinal {ordinal})"
                assert basis.line_applies_on(by_name["twelve"], payday) is (
                    ordinal == 1
                ), f"12-line on {payday} (ordinal {ordinal})"
            # Non-vacuous: the seeded calendar holds a first, a second and a
            # third paycheck of some month, so each arm of both predicates
            # fired at least once.
            ordinals = {len(paydays_in_month_through(calendar, p)) for p in paydays}
            assert {1, 2, 3} <= ordinals, ordinals

    def test_a_value_the_vocabulary_cannot_say_refuses_by_name(self, app, db, seed_user):
        """52 is neither 26, 24 nor 12: the upgrade names the row and stops."""
        with app.app_context():
            _profile, ids = _seed_lines(seed_user, ["weekly"])
            _rewind(db.session)
            _run(_M_R15B.downgrade, db.session)
            _set_old_counts(db.session, {ids["weekly"]: 52})

            with pytest.raises(RuntimeError) as excinfo:
                _run(_M_R15B.upgrade, db.session)
            db.session.rollback()

            message = str(excinfo.value)
            assert f"deduction {ids['weekly']} ('weekly') carries 52" in message
            assert "neither 26, 24 nor 12" in message
            # Refused means untouched: the arm was added inside the same
            # transaction and rolled back with it; the column survives.
            assert _OLD_COLUMN in _columns(db.session, "salary", _LINES)
            assert _ARM not in _columns(db.session, "budget", "recurrence_rules")
            # Put the template back the way the suite expects it.
            _set_old_counts(db.session, {ids["weekly"]: 26})
            _run(_M_R15B.upgrade, db.session)
            _replay(db.session)

    def test_a_line_whose_owner_has_no_payday_refuses_by_name(self, app, db, seed_user):
        """No opening payday, no rule to start (R-SAL30): the upgrade names the line and stops."""
        with app.app_context():
            _profile, ids = _seed_lines(seed_user, ["orphan"])
            _rewind(db.session)
            _run(_M_R15B.downgrade, db.session)
            _set_old_counts(db.session, {ids["orphan"]: 24})
            db.session.execute(text(
                "DELETE FROM budget.pay_periods WHERE user_id = :u"
            ), {"u": seed_user["user"].id})
            db.session.commit()

            with pytest.raises(RuntimeError) as excinfo:
                _run(_M_R15B.upgrade, db.session)
            db.session.rollback()

            message = str(excinfo.value)
            assert f"deduction {ids['orphan']} ('orphan', 24/yr) of user {seed_user['user'].id}" in message
            assert "opening payday" in message
            assert _OLD_COLUMN in _columns(db.session, "salary", _LINES)
            _set_old_counts(db.session, {ids["orphan"]: 26})
            _run(_M_R15B.upgrade, db.session)
            _replay(db.session)


@pytest.mark.usefixtures("seed_periods")
class TestTheDowngradeRebuildsTheColumnAndRefusesWhatItCannotSay:
    """The two shapes go back to 24 / 12 and their rules are deleted; any other shape refuses."""

    def test_the_two_shapes_rebuild_their_counts(self, app, db, seed_user):
        """Down: 24 and 12 are written from the shapes, the rules deleted, 26 for the rest."""
        with app.app_context():
            _profile, ids = _seed_lines(seed_user, ["every", "twenty_four", "twelve"])
            for name, per_year in (("twenty_four", 24), ("twelve", 12)):
                make_line_cadence_rule(
                    db.session, db.session.get(PaycheckLine, ids[name]), per_year,
                )
            db.session.commit()

            _rewind(db.session)
            _run(_M_R15B.downgrade, db.session)

            counts = dict(db.session.execute(text(
                "SELECT id, deductions_per_year FROM salary.paycheck_deductions"
            )).all())
            assert counts == {ids["every"]: 26, ids["twenty_four"]: 24, ids["twelve"]: 12}
            assert db.session.execute(text(
                "SELECT count(*) FROM budget.recurrence_rules "
                "WHERE transaction_template_id IS NULL AND transfer_template_id IS NULL"
            )).scalar() == 0, "a deduction-owned rule survived the downgrade"
            _run(_M_R15B.upgrade, db.session)
            _replay(db.session)

    def test_a_monthly_rule_from_a_day_other_than_the_first_is_not_a_twelve(
        self, app, db, seed_user,
    ):
        """Monthly on the first paycheck on or after the 26th is NOT the column's 12.

        The 12 shape's ``starts_on`` is a 1st; a monthly rule from the opening
        payday itself (what R-SAL30 read literally would author for the MONTH
        unit) names a different paycheck in a month whose first payday falls
        before the 26th, and rebuilding it as 12 would move that line silently
        (an adversarial review of this step).
        """
        with app.app_context():
            _profile, ids = _seed_lines(seed_user, ["late"])
            user_id = seed_user["user"].id
            calendar = calendar_for(user_id)
            opening = calendar.opening_bound()
            assert opening.day != 1, "the seeded opening must not be a 1st for this case to bite"
            rule = author_rule(
                RecurrenceSpec(
                    user_id=user_id, unit=RecurrenceUnitEnum.MONTH,
                    placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                    starts_on=opening,
                ),
                calendar,
                db.session.get(PaycheckLine, ids["late"]),
            )
            db.session.commit()
            # Read the id BEFORE the rewind: an expired ORM attribute reloads
            # through the head model, which maps the column the rewind renamed.
            rule_id = rule.id
            _rewind(db.session)

            with pytest.raises(RuntimeError) as excinfo:
                _run(_M_R15B.downgrade, db.session)
            db.session.rollback()

            assert f"rule {rule_id} on deduction {ids['late']} (every 1 month" in str(excinfo.value)
            assert _ARM in _columns(db.session, "budget", "recurrence_rules")

    def test_a_shape_the_column_cannot_say_refuses_by_name(self, app, db, seed_user):
        """A quarterly line (every 3 months) has no count; the downgrade names it and stops."""
        with app.app_context():
            _profile, ids = _seed_lines(seed_user, ["quarterly"])
            user_id = seed_user["user"].id
            calendar = calendar_for(user_id)
            rule = author_rule(
                RecurrenceSpec(
                    user_id=user_id, unit=RecurrenceUnitEnum.MONTH, interval_n=3,
                    placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                    starts_on=calendar.opening_bound().replace(day=1),
                ),
                calendar,
                db.session.get(PaycheckLine, ids["quarterly"]),
            )
            db.session.commit()
            rule_id = rule.id
            _rewind(db.session)

            with pytest.raises(RuntimeError) as excinfo:
                _run(_M_R15B.downgrade, db.session)
            db.session.rollback()

            message = str(excinfo.value)
            assert f"rule {rule_id} on deduction {ids['quarterly']} (every 3 month" in message
            assert "moves money" in message
            # Refused means untouched.
            assert _ARM in _columns(db.session, "budget", "recurrence_rules")
            assert _OLD_COLUMN not in _columns(db.session, "salary", _LINES)
            _replay(db.session)
            db.session.expire_all()
            assert db.session.get(PaycheckLine, ids["quarterly"]).recurrence_rule is not None
