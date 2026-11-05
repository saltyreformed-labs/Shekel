"""The recurrence write door and the writers routed through it (R2c, R2d).

``tests/test_services/test_recurrence_resolution.py`` covers the pure
derivation.  This file covers what it means for the application to have ONE
write door: that every path which creates or changes a rule goes through it,
and that what each writer INTENDED is what the rule resolves to afterwards.

Two invariants run through it.

* **Authoring is idempotent.**  Reading a rule's spec back and re-authoring it
  changes no column (:func:`assert_reauthoring_changes_nothing`).  That is
  what makes "a caller owning one fact replaces that fact and re-authors" a
  safe idiom rather than a rewrite with side effects.
* **A rule always resolves, completely.**  Every pattern the application can
  author produces a whole :class:`~app.services.recurrence.ResolvedRecurrence`
  (:func:`assert_resolves_completely`), which is the property plan step R7c's
  NOT NULL columns will rest on.

**What this file no longer asserts, and why.**  Before plan step R2d the
invariant was "a rule's two-axis COLUMNS are always the resolution of its own
closed-set columns" -- a consistency check between two stored halves.  Those
columns are gone: the two-axis view is computed on demand, so the halves
cannot disagree and there is nothing to check.  What replaced it is stronger
and simpler -- the value is derived at every read, so the tests assert the
DERIVATION (in the resolution suite) and the AUTHORING (here), with no
consistency relation in between.
"""

import ast
import inspect
from dataclasses import fields, replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db as _db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import FilingStatus
from app.services import pay_period_admin
from app.services.pay_calendar import calendar_for
from tests.oracles.recurrence_baseline import (
    BASELINE_CADENCES,
    ShapeCadence,
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    MONTHLY_FIRST,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)
from app.services.recurrence import (
    EmptyAuthoredWindowError,
    EndsOnDate,
    RecurrenceSpec,
    ResolvedRecurrence,
    WeekdayReading,
    author_rule,
    occurrence_placements,
    scheduling_day_of_month,
    reauthor_rule,
    recurrence_spec,
    recurrence_spec_with_cadence,
    resolve,
    rule_occurrences,
)
# Imported as a MODULE, and from its DEFINITION site: the census below reads
# ``_author``'s own source, so naming the package re-export would let the
# assertion pass while looking at a different function than the one under test.
from app.services.recurrence import _authoring
from tests._test_helpers import (
    bind_rule_to_loan,
    rhythm_of,
    bare_expense_template,
    create_loan_account,
    create_savings_account,
    make_line_cadence_rule,
    make_expense_template,
    make_salary_profile,
    make_transfer_template,
    sole_rule_owned_by,
)


#: Every column of ``budget.recurrence_rules`` a CALLER states.  Named once so
#: :func:`authored_columns` cannot silently stop covering one that is added,
#: and pinned against the table by
#: :class:`TestTheAuthoredSurfaceIsWholeAndClosed`.
#:
#: **A caller's request decides these and nothing else does**, which is what
#: makes "re-authoring changes nothing" and "a schedule rebuild changes
#: nothing" meaningful assertions about them.
#:
#: **The list SHRANK at plan step R7c-c, and the cadence columns moved to the
#: DERIVED list rather than leaving the table.**  ``interval_n`` and ``unit_id``
#: are what a caller states, but they are not what is necessarily WRITTEN: the
#: door takes both off the ``resolve`` call, which canonicalises a whole number
#: of years authored in months onto the YEAR unit (ruling R-R17).  A column
#: whose stored value can differ from the request is derived, whatever the
#: request looks like -- and filing it here would have made
#: :func:`assert_reauthoring_changes_nothing` assert the wrong thing about it.
#: **``user_id`` LEFT this list at plan step R-F6, with the column.**  A rule's
#: owner is the definition holding it now, and
#: :attr:`~app.models.recurrence_rule.RecurrenceRule.user_id` reads through to
#: that definition's -- so it is neither authored nor derived here; it is not a
#: column at all, and the two owning FKs that replaced it are
#: :data:`_OWNING_ARC_COLUMNS`.
_AUTHORED_COLUMNS = (
    "end_date", "max_occurrences",
)

#: The OWNING ARC: which definition this rule belongs to (plan step R-F6).
#:
#: A FOURTH category, and it is a category rather than an exception because
#: these two columns are written by a different function than every other one:
#: :func:`~app.services.recurrence.author_rule` sets them from the ``owner`` it
#: takes, through ``owner.recurrence_rule``, while ``_author`` writes the
#: cadence.  The split is deliberate -- an owner is not part of what a caller
#: AUTHORS about a recurrence, and ``reauthor_rule`` must not be able to move a
#: rule between definitions -- so the assignment census below cannot see them
#: and a behavioural proof stands in its place
#: (:meth:`TestTheAuthoredSurfaceIsWholeAndClosed.test_the_owning_arc_is_written_and_exclusive`).
#:
#: Listing them here rather than loosening an assertion is what keeps the gate
#: strict: a further owning arm, or any other column added and forgotten,
#: still fails the partition because it will not be on this list.  The THIRD
#: arm, ``paycheck_line_id``, arrived at plan step salary:R15-b (ruling
#: R-SAL32) exactly that way -- this line was the census that named it.
_OWNING_ARC_COLUMNS = frozenset({
    "transaction_template_id", "transfer_template_id", "paycheck_line_id",
})

#: Every column the write door DERIVES, from ``resolve`` and the owner's
#: schedule rather than from the request.
#:
#: **They are a separate list because they answer a different question**, and
#: plan step R7c-a is what made the difference observable.  ``starts_on`` is the
#: first occurrence measured against the schedule the owner has NOW, so a full
#: rebuild legitimately moves it -- which ``TestScheduleRebuildRepoint``'s own
#: docstring already said about the anchor, one paragraph before this list
#: forced the two together.
#:
#: The distinction is not bookkeeping.  A derived column that failed to move
#: with its inputs would be the stale cache plan step R2d refused, and
#: :meth:`TestTheAuthoredSurfaceIsWholeAndClosed.test_the_derived_columns_equal_the_resolver`
#: is what says it moves.
#:
#: **``offset_periods`` left at plan step R7c-c** with the column: the phase is
#: derived on every read and stored nowhere.  ``interval_n`` ARRIVED in the same
#: step, from the authored list -- see it for why a canonicalised cadence makes
#: it a derived value even though a caller states it.  ``max_per_month``
#: ARRIVED at plan step salary:R15-a on the same footing: the write door takes
#: it off the canonical cadence ``resolve`` answers, beside the interval and
#: the unit, so the stored ceiling is the resolver's and not the request's.
#: ``weekday_reading`` ARRIVED at plan step recurrence:R8-c on the footing of
#: ``nominal_day`` beside it: the door writes the resolved value's.
#: ``unit_has_day_of_month`` ARRIVED at plan step plan#2302, off the same
#: resolved unit.
_DERIVED_COLUMNS = (
    "interval_n", "unit_id", "unit_has_day_of_month", "placement_id", "shift_id",
    "starts_on", "nominal_day", "weekday_reading", "max_per_month",
)

#: The two columns the DATABASE assigns, which no caller may author: the
#: surrogate key and the insert timestamp (``CreatedAtMixin``, server default).
_DB_ASSIGNED_COLUMNS = frozenset({"id", "created_at"})

#: RETIRED columns: on the table, written by nobody, read by nobody, awaiting
#: the migration that drops them.
#:
#: A third category rather than a loosened assertion, and the distinction is
#: the point.  The census below is the gate that catches a column the write
#: door FORGOT -- one that would keep its server default forever while every
#: behavioural test passed, because a value nobody writes also never changes.
#: Naming a retired column here is what keeps that gate STRICT for everything
#: else: a column added and forgotten still fails, because it will not be on
#: this list.
#:
#: **It is EMPTY from plan step R7c-c, which is what the leaf means.**  It held
#: ``start_period_id`` (retired at R7b-4), then ``month_of_year`` and
#: ``start_date`` (retired at R7b-c) -- three columns on the table that nothing
#: wrote and nothing read, kept only because dropping a column belongs with the
#: leaf that drops the rest.  That leaf ran: every column of
#: ``budget.recurrence_rules`` is now authored, derived, or assigned by the
#: database, and there is no fourth category.  This list emptying is the
#: three-leaf expand / migrate / contract (ruling R-R18) finishing, and the
#: paragraph that used to predict it can be read in ``900e761a``.
_RETIRED_COLUMNS: frozenset[str] = frozenset()


def authored_columns(rule: RecurrenceRule) -> dict:
    """Return every authored column of *rule* as a plain dict.

    Args:
        rule: The rule to read.

    Returns:
        ``{column_name: value}`` over :data:`_AUTHORED_COLUMNS`.
    """
    return {name: getattr(rule, name) for name in _AUTHORED_COLUMNS}


def derived_columns(rule: RecurrenceRule) -> dict:
    """Return every DERIVED column of *rule* as a plain dict.

    Args:
        rule: The rule to read.

    Returns:
        ``{column_name: value}`` over :data:`_DERIVED_COLUMNS`.
    """
    return {name: getattr(rule, name) for name in _DERIVED_COLUMNS}


def resolved_for(rule: RecurrenceRule) -> ResolvedRecurrence:
    """Return the two-axis meaning of a persisted rule.

    The rule stores no part of this: it is recomputed from the rule's own
    authored columns and its owner's schedule, which is exactly how every
    reader in the application will obtain it.

    Args:
        rule: The persisted rule to resolve.

    Returns:
        Its :class:`~app.services.recurrence.ResolvedRecurrence`.
    """
    return resolve(recurrence_spec(rule), calendar_for(rule.user_id))


def spec_for(cadence: ShapeCadence, **overrides) -> RecurrenceSpec:
    """Return a spec for *cadence*, with *overrides*.

    The cadence is a SHARED constant for the reason the twin helper in
    ``test_recurrence_resolution.py`` records: every case here was written
    against one of
    :data:`~tests.oracles.recurrence_baseline.BASELINE_CADENCES` and restating
    the axes per case would be a silent opportunity to change what each one
    measures.  Plan step R9 replaced the closed set's display names with the
    constants themselves, when the table those names came from was dropped.

    ``interval_n`` is applied AFTER the cadence, so a case may state an
    interval the constant does not -- which is the whole point of the two-axis
    vocabulary.

    ``starts_on`` defaults to the owner's OPENING PAYDAY, which is what an
    unbounded rule of any cadence resolved to before plan step R7c-b made the
    first occurrence authored.  A case that cares about the date states one;
    the rest are about columns and round trips, and they measure the same
    thing they always did.

    Args:
        cadence: One of the baseline oracle's cadence constants.
        **overrides: Any :class:`~app.services.recurrence.RecurrenceSpec`
            field to set.  ``user_id`` is required, as it is on the spec.

    Returns:
        The spec.
    """
    # TWO for the reason the twin helper in ``test_recurrence_resolution.py``
    # records: at one, ``EVERY_N_PERIODS`` reads identically to
    # ``EVERY_PERIOD`` and drops out of every whole-vocabulary sweep built on
    # this helper.
    interval_override = overrides.pop("interval_n", None)
    stated = 2 if cadence.interval_n is None else cadence.interval_n
    overrides.setdefault(
        "starts_on", calendar_for(overrides["user_id"]).opening_bound(),
    )
    return RecurrenceSpec(
        unit=cadence.unit,
        interval_n=stated if interval_override is None else interval_override,
        placement=cadence.placement,
        **overrides,
    )


def assert_reauthoring_changes_nothing(rule: RecurrenceRule) -> None:
    """Assert re-authoring a rule from its own spec is a no-op.

    Idempotence is what makes the read-modify-re-author idiom safe: a caller
    that owns ONE fact reads the spec, replaces that fact, and writes the
    whole thing back, so every OTHER fact must survive the round trip
    untouched.  A writer that derived something differently on the way out
    than on the way in would move a column here.

    Args:
        rule: The persisted rule to round-trip.
    """
    before = authored_columns(rule)
    before_derived = derived_columns(rule)

    reauthor_rule(rule, recurrence_spec(rule), calendar_for(rule.user_id))

    assert authored_columns(rule) == before
    # The DERIVED columns are stable too, and they are asserted separately
    # because they are stable for a different reason: an authored column
    # survives because nothing rewrote it, a derived one because the same
    # inputs produce the same answer.  A caller that re-authors after the
    # SCHEDULE moved gets a different answer legitimately -- see
    # ``TestScheduleRebuildRepoint``, which is why this helper is not used
    # across a reset.
    assert derived_columns(rule) == before_derived


def assert_resolves_completely(rule: RecurrenceRule) -> None:
    """Assert *rule* resolves to a whole two-axis value.

    The property plan step R7c's NOT NULL columns will rest on: no rule the
    application can author may resolve to a partial value, because at that
    step the partial value becomes an un-migratable row.

    Args:
        rule: The persisted rule to check.
    """
    resolved = resolved_for(rule)

    assert resolved.starts_on is not None
    assert isinstance(resolved.unit, RecurrenceUnitEnum)
    assert isinstance(resolved.placement, PeriodPlacementEnum)
    assert isinstance(resolved.shift, BusinessDayShiftEnum)
    assert resolved.interval_n >= 1


def _columns_assigned_by_the_write_door() -> set[str]:
    """Return every ``rule.<column> = ...`` target inside ``_author``.

    Read from the SOURCE rather than inferred from behaviour, because the
    property is about what the function can write at all, and a column it never
    assigns is invisible to every behavioural check -- a value nobody writes
    also never changes.

    Returns:
        The assigned attribute names.
    """
    source = inspect.getsource(_authoring)
    tree = ast.parse(source)
    door = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_author"
    )
    return {
        target.attr
        for node in ast.walk(door)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "rule"
    }


class TestTheAuthoredSurfaceIsWholeAndClosed:
    """Every column of the table is either authored or assigned by the database.

    The door writes a whole ``RecurrenceSpec`` rather than a field at a time,
    which is only meaningful if the door actually COVERS the table.  Add a
    column to ``budget.recurrence_rules`` and forget the door, and it silently
    stops being able to author it: the column takes its server default forever,
    no writer can set it, and no other test notices -- the round-trip and
    idempotence checks both pass, because a value nobody writes also never
    changes.

    **Asserted against what ``_author`` ASSIGNS, not against the spec's field
    names**, and plan step R7b is why.  The two were the same set while every
    spec field was a column; they are not any more -- a caller authors ``unit``
    and ``placement`` and the door encodes both into ``pattern_id`` -- and a
    shape comparison between two vocabularies can only be kept true by a
    hand-maintained exception list, which is the thing this class exists
    instead of.  The assignment census is also STRONGER: it fails on a column
    the spec carries and the door forgets, which the old comparison could not
    see.

    **It DID fail, on purpose, at plan step R7c-a**, which is what this
    paragraph used to predict -- though it named ``anchor_date``, a column the
    D28 ruling of 2026-08-14 replaced with ``starts_on``.  Exactly one of the
    two arms fired, and which one is the useful part: the door already assigned
    all five new columns, so the census passed; what failed was
    :meth:`test_the_helper_covers_every_authored_column`, because the list the
    comparison helpers read had not grown with the table.  That is the arm
    catching a column that would otherwise sit outside every round-trip
    assertion in this file.
    """

    def test_the_write_door_assigns_every_column_the_database_does_not(self):
        """``_author`` writes every column but the DB-assigned and RETIRED ones."""
        table_columns = {
            column.key for column in RecurrenceRule.__table__.columns
        }

        assert _columns_assigned_by_the_write_door() == (
            table_columns - _DB_ASSIGNED_COLUMNS - _RETIRED_COLUMNS
            - _OWNING_ARC_COLUMNS
        ), (
            "budget.recurrence_rules and the write door have diverged.  A "
            "column ``_author`` does not assign cannot be authored at all -- "
            "it would keep its server default forever, and neither the "
            "round-trip nor the idempotence check would notice, because a "
            "value nobody writes also never changes."
        )

    def test_the_helpers_cover_every_column_between_them(self):
        """The two lists PARTITION the table, so no column escapes both.

        The comparison helpers in this file read only these names, so a name
        missing from both would exempt that column from every assertion built
        on them.  Keyed on the TABLE because that is what they ``getattr`` off.

        **Disjointness is asserted too**: a column on both lists would be
        claimed as caller-stated AND as resolver-derived, and the two carry
        opposite expectations under a schedule rebuild -- one must not move,
        the other legitimately does.
        """
        assert not set(_AUTHORED_COLUMNS) & set(_DERIVED_COLUMNS), (
            "a column cannot be both authored and derived: the reset and "
            "round-trip assertions expect it to hold still, and the resolver "
            "assertion expects it to follow the schedule."
        )
        table_columns = {
            column.key for column in RecurrenceRule.__table__.columns
        }

        assert set(_AUTHORED_COLUMNS) | set(_DERIVED_COLUMNS) == (
            table_columns - _DB_ASSIGNED_COLUMNS - _RETIRED_COLUMNS
            - _OWNING_ARC_COLUMNS
        )

    @pytest.mark.usefixtures("seed_periods")
    def test_the_owning_arc_is_written_and_exclusive(self, seed_user, db):
        """``author_rule`` fills exactly one arm of the arc, for either kind.

        The behavioural stand-in for the AST census above, which cannot see
        these two columns because ``_author`` does not assign them --
        :func:`~app.services.recurrence.author_rule` does, from the ``owner``
        it takes.  Without this the pair would sit outside every assertion in
        this file: a door that stopped linking the owner would leave both NULL,
        and only ``ck_recurrence_rules_one_owner`` firing at flush would say
        so, which is a constraint reporting a code defect rather than a test
        catching one.

        Every kind, because the arc has three arms and one call site fills
        any -- ``owner.recurrence_rule`` dispatches on the owner's own mapper,
        so a regression that hard-coded one arm would pass on the other
        definitions.  The third arm, a payroll deduction, arrived at plan step
        salary:R15-b (ruling R-SAL32) and reads its owner through its profile.

        Args:
            seed_user: The owner fixture.
            db: The session fixture.
        """
        # pylint: disable=import-outside-toplevel
        from app.models.paycheck_line import PaycheckLine
        from app.models.ref import CalcMethod, PaycheckLineKind

        user_id = seed_user["user"].id
        savings = create_savings_account(
            seed_user, db.session, "Arc Savings", Decimal("0.00"),
        )
        expense = make_expense_template(db.session, seed_user)
        transfer = make_transfer_template(db.session, seed_user, savings)
        profile = make_salary_profile(seed_user, db.session)
        db.session.flush()
        deduction = PaycheckLine(
            salary_profile_id=profile.id,
            paycheck_line_kind_id=db.session.query(PaycheckLineKind).first().id,
            calc_method_id=db.session.query(CalcMethod).first().id,
            name="Arc Health", amount=Decimal("100.00"),
        )
        db.session.add(deduction)
        db.session.flush()
        make_line_cadence_rule(db.session, deduction, 24)

        arms = ("transaction_template_id", "transfer_template_id", "paycheck_line_id")
        for owner, filled in (
            (expense, "transaction_template_id"),
            (transfer, "transfer_template_id"),
            (deduction, "paycheck_line_id"),
        ):
            rule = owner.recurrence_rule
            assert rule is not None, (
                f"{type(owner).__name__} carries no rule, so the arc this "
                f"test grades was never written"
            )
            assert getattr(rule, filled) == owner.id, (
                f"author_rule did not point {filled} at the "
                f"{type(owner).__name__} it was given"
            )
            for empty in arms:
                if empty == filled:
                    continue
                assert getattr(rule, empty) is None, (
                    f"author_rule filled {empty} beside {filled}; "
                    f"ck_recurrence_rules_one_owner allows exactly one"
                )
            assert rule.user_id == user_id, (
                "the derived user_id does not read through to the owner's"
            )

    def test_the_derived_columns_equal_the_resolver(self, seed_user, db, seed_periods):  # pylint: disable=unused-argument
        """Every DERIVED column holds what the resolver answers, per cadence.

        **What this proves, stated narrowly because an adversarial review of
        plan step R7c-a found the wider claim false.**  It says the write door
        ASSIGNS every derived column from the resolver and that the read door
        round-trips them -- so a door that forgot one, or a read that lost a
        field, fails here.  It does **not** grade ``first_occurrence``: both
        sides of the comparison call it, so it is a producer checked against
        itself, which ``docs/plans/verification.md`` standard 2 rules out.
        The independent oracle for that function is the occurrence WALK, in
        ``test_recurrence_occurrence.TestTheFirstOccurrenceIsTheWalksFirstYield``.

        Asserted per cadence rather than once because the door's branch set is
        per unit -- a pay-period rule's ``starts_on`` is a PAYDAY and a
        calendar rule's is the anchor date.

        Args:
            seed_user: The owner fixture.
            db: The session fixture.
            seed_periods: The owner's schedule.
        """
        user_id = seed_user["user"].id
        calendar = calendar_for(user_id)
        for cadence in BASELINE_CADENCES:
            rule = author_rule(
                spec_for(
                    cadence, user_id=user_id,
                    starts_on=date(2026, 1, 15),
                ),
                calendar,
                bare_expense_template(db.session, seed_user),
            )
            db.session.flush()
            resolved = resolve(recurrence_spec(rule), calendar)
            assert derived_columns(rule) == {
                "interval_n": resolved.interval_n,
                "unit_id": ref_cache.recurrence_unit_id(resolved.unit),
                "unit_has_day_of_month": (
                    ref_cache.recurrence_unit_has_day_of_month(resolved.unit)
                ),
                "placement_id": ref_cache.period_placement_id(
                    resolved.placement,
                ),
                "shift_id": ref_cache.business_day_shift_id(resolved.shift),
                "starts_on": resolved.starts_on,
                "nominal_day": resolved.nominal_day,
                # The member's value is what the column names (plan step
                # recurrence:R8-c); every baseline cadence reads its day.
                "weekday_reading": (
                    None if resolved.weekday_reading is None
                    else resolved.weekday_reading.value
                ),
                "max_per_month": resolved.max_per_month,
            }, (
                f"the {cadence} rule's stored cadence columns disagree "
                f"with what the resolver answers for it, so the table states "
                f"its cadence twice and the two have drifted."
            )

    def test_every_retired_column_still_exists(self):
        """:data:`_RETIRED_COLUMNS` names columns, not memories.

        The exemption list above is what keeps the write-door census passing
        for a column nothing writes, so a stale name on it would silently
        exempt nothing while reading as though it did -- and once plan step
        R7c drops these columns, this is what says the list must empty with
        them.
        """
        table_columns = {
            column.key for column in RecurrenceRule.__table__.columns
        }

        assert _RETIRED_COLUMNS <= table_columns, (
            f"_RETIRED_COLUMNS names "
            f"{_RETIRED_COLUMNS - table_columns}, which "
            f"budget.recurrence_rules does not carry.  A dropped column needs "
            f"no exemption; remove the name with the migration that drops it."
        )

    def test_every_spec_field_reaches_the_row(self):
        """No field of the spec is silently dropped on the way to the table.

        The census above says the door fills every column; this says the door
        READS every field.  Together they close the two directions a write door
        can be incomplete in.

        **The exception list is EMPTY since plan step R7b-4**, and that is the
        strongest this assertion has ever been.  ``offset_periods`` sat on it
        -- a spec field ``_author`` deliberately did not read, taking the
        resolver's answer instead because the value is DERIVED on every write
        (defect **D1**'s fix).  A field a caller can state and the door
        ignores is a field that lies about what it does, so R7b-4 removed it
        from the spec rather than from this list: nobody authors a phase now.

        **Two names, not one, since plan step R7c-b.**  A spec field reaches
        the row by one of two routes: the door copies it (``spec.x``) or the
        RESOLVER answers it and the door copies that (``resolved.x``).
        ``starts_on`` travels the second route and must -- a paycheck-space
        rule's first occurrence is normalised onto a payday, so the value the
        column holds is deliberately not the one the caller stated -- and
        counting only ``spec.`` reads would have reported it as dropped.
        Widening the census does not weaken it: a field on NEITHER name is
        still a field nothing carries, and whether ``resolve`` carries what it
        is handed is graded separately, by
        ``test_recurrence_resolution.TestTotality``.
        """
        source = inspect.getsource(_authoring)
        carriers = {"spec", "resolved"}
        read_from_spec = {
            node.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in carriers
        }
        spec_fields = {field.name for field in fields(RecurrenceSpec)}

        assert spec_fields - read_from_spec == set(), (
            "a RecurrenceSpec field the write door never reads -- on the spec "
            "OR on the value the resolver returns -- is one a caller can "
            "state and the table will not carry."
        )


@pytest.mark.usefixtures("seed_periods")
class TestThePerMonthCeilingRoundTrips:
    """The cadence's third value survives the write door and the read door.

    Plan step salary:R15-a: ``_author`` writes ``max_per_month`` off the
    resolved value, ``recurrence_spec`` reads it back, and the read door that
    takes a STATED cadence drops it where that cadence cannot hold it -- the
    nominal day's own rule, one column over.
    """

    def test_a_ceilinged_rule_stores_and_reads_its_ceiling(self, seed_user, db):
        """Every paycheck at most 2 a month round-trips through the row."""
        user_id = seed_user["user"].id
        calendar = calendar_for(user_id)

        rule = author_rule(
            spec_for(EVERY_PERIOD, user_id=user_id, max_per_month=2),
            calendar,
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        assert rule.max_per_month == 2
        assert recurrence_spec(rule).max_per_month == 2
        assert resolve(recurrence_spec(rule), calendar).max_per_month == 2
        assert_reauthoring_changes_nothing(rule)

    def test_the_stated_cadence_reader_drops_a_ceiling_a_month_unit_cannot_hold(
        self, seed_user, db,
    ):
        """Re-cadencing a ceilinged paycheck rule to monthly reads no ceiling.

        The edit form's repair path: it states the NEW cadence over the stored
        row, and a stored ceiling carried across would build the pair
        ``RecurrenceSpec`` refuses, turning an ordinary edit into a 500.
        """
        user_id = seed_user["user"].id
        calendar = calendar_for(user_id)
        rule = author_rule(
            spec_for(EVERY_PERIOD, user_id=user_id, max_per_month=2),
            calendar,
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        restated = recurrence_spec_with_cadence(
            rule,
            interval_n=1,
            unit=RecurrenceUnitEnum.MONTH,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            max_per_month=rule.max_per_month,
        )

        assert restated.max_per_month is None
        # And a paycheck cadence keeps what was stated for it.
        assert recurrence_spec_with_cadence(
            rule,
            interval_n=3,
            unit=RecurrenceUnitEnum.PERIOD,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            max_per_month=1,
        ).max_per_month == 1

    def test_the_database_refuses_a_zero_ceiling(self, seed_user, db):
        """``ck_recurrence_rules_positive_max_per_month`` is the floor at the table."""
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(EVERY_PERIOD, user_id=user_id, max_per_month=2),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        rule.max_per_month = 0
        with pytest.raises(IntegrityError, match="positive_max_per_month"):
            db.session.flush()
        db.session.rollback()


@pytest.mark.usefixtures("seed_periods")
class TestTheWeekdayReadingRoundTrips:
    """How the first date is read survives the write door and the read door.

    Plan step recurrence:R8-c: ``_author`` writes the resolved reading's value
    into ``weekday_reading``, the read door reads the member back, and the
    reader that takes a STATED cadence drops it where that cadence has no
    month coordinate -- the nominal day's rule and the ceiling's.
    """

    @pytest.mark.parametrize(
        ("starts_on", "reading", "stored"),
        [
            # Tue 2026-11-17: the 17th by day, the third Tuesday by weekday.
            (date(2026, 11, 17), None, None),
            (date(2026, 11, 17), WeekdayReading.FROM_START, "from_start"),
            # Fri 2027-01-29: the last Friday of January 2027.
            (date(2027, 1, 29), WeekdayReading.FROM_END, "from_end"),
        ],
        ids=["day", "from-start", "from-end"],
    )
    def test_every_reading_stores_and_reads_back(
        self, seed_user, db, starts_on, reading, stored,
    ):
        """The row holds the member's value; the spec and the value read it back."""
        user_id = seed_user["user"].id
        calendar = calendar_for(user_id)
        rule = author_rule(
            spec_for(
                MONTHLY, user_id=user_id, starts_on=starts_on,
                weekday_reading=reading,
            ),
            calendar,
            bare_expense_template(db.session, seed_user),
        )
        db.session.commit()
        db.session.expire(rule)

        assert rule.weekday_reading == stored
        assert recurrence_spec(rule).weekday_reading is reading
        assert resolve(recurrence_spec(rule), calendar).weekday_reading is reading
        assert_reauthoring_changes_nothing(rule)

    def test_the_stated_cadence_reader_drops_a_reading_a_paycheck_unit_cannot_hold(
        self, seed_user, db,
    ):
        """Re-cadencing a third-Tuesday rule onto paychecks reads no reading.

        The edit form's repair path: a reading carried onto a cadence with no
        month coordinate would build the spec ``RecurrenceSpec`` refuses.  A
        yearly cadence has one, and keeps it.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                MONTHLY, user_id=user_id, starts_on=date(2026, 11, 17),
                weekday_reading=WeekdayReading.FROM_START,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        def restated(unit):
            return recurrence_spec_with_cadence(
                rule, interval_n=1, unit=unit,
                placement=PeriodPlacementEnum.CONTAINING_DATE,
                max_per_month=None,
            ).weekday_reading

        assert restated(RecurrenceUnitEnum.PERIOD) is None
        assert restated(RecurrenceUnitEnum.WEEK) is None
        assert restated(RecurrenceUnitEnum.YEAR) is WeekdayReading.FROM_START

    def test_a_stored_weekday_rule_schedules_on_no_one_day(self, seed_user, db):
        """The third Tuesday is the 15th-21st, so the row names no day; the 17th does."""
        user_id = seed_user["user"].id
        calendar = calendar_for(user_id)

        def stored(reading):
            return author_rule(
                spec_for(
                    MONTHLY, user_id=user_id, starts_on=date(2026, 11, 17),
                    weekday_reading=reading,
                ),
                calendar,
                bare_expense_template(db.session, seed_user),
            )

        assert scheduling_day_of_month(stored(None)) == 17
        assert scheduling_day_of_month(stored(WeekdayReading.FROM_START)) is None


class TestTheWriteDoorRefusesAnInvertedStoredPair:
    """``EmptyAuthoredWindowError`` (plan step R7d-g): one comparison at the one writer.

    ``_author`` stores the NORMALISED first occurrence beside the RAW closing
    bound, so a pair every door admits -- the stop at or after the AUTHORED
    start -- can invert once a paycheck-space start is lifted onto its payday.
    ``ck_recurrence_rules_valid_window`` would refuse it at the flush as an
    ``IntegrityError``; the door refuses it first, off the two dates it would
    have stored, and leaves the row untouched.  Measured before the fix (an
    adversarial review of R7d-g-1): an every-paycheck rule authored
    ``2025-06-01`` to ``2025-12-01`` on a schedule opening ``2026-01-02``
    passed both doors and stored ``[2026-01-02, 2025-12-01]``.
    """

    def test_a_paycheck_rule_authored_before_the_schedule_opens_is_refused_with_the_stored_dates(
        self, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The reviewer's shape: the authored pair is ordered, the stored pair is not."""
        user_id = seed_user["user"].id
        calendar = calendar_for(user_id)
        assert calendar.opening_bound() == date(2026, 1, 2), (
            "precondition: the schedule opens after the authored window"
        )
        template = bare_expense_template(db.session, seed_user)
        spec = spec_for(
            EVERY_PERIOD, user_id=user_id,
            starts_on=date(2025, 6, 1), end_bound=EndsOnDate(on=date(2025, 12, 1)),
        )

        with pytest.raises(EmptyAuthoredWindowError) as refused:
            author_rule(spec, calendar, template)

        assert refused.value.starts_on == date(2026, 1, 2), (
            "the refusal must name the first occurrence the row would have "
            "STORED, not the date the owner typed"
        )
        assert refused.value.end_date == date(2025, 12, 1)
        assert template.recurrence_rule is None, "the refusal wrote a rule"

    def test_a_paycheck_rule_whose_stop_reaches_the_opening_payday_is_written(
        self, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The boundary: a stop ON the payday the start lifts to names one occurrence."""
        user_id = seed_user["user"].id
        calendar = calendar_for(user_id)
        template = bare_expense_template(db.session, seed_user)
        rule = author_rule(
            spec_for(
                EVERY_PERIOD, user_id=user_id,
                starts_on=date(2025, 6, 1), end_bound=EndsOnDate(on=date(2026, 1, 2)),
            ),
            calendar, template,
        )
        db.session.flush()
        assert rule.starts_on == date(2026, 1, 2)
        assert rule.end_date == date(2026, 1, 2)

    def test_a_re_author_that_would_invert_the_stored_pair_leaves_the_row_as_it_was(
        self, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The update shape: a MONTH rule switched to every paycheck with its bounds kept.

        ``[2025-06-01, 2025-12-01]`` is an ordered MONTH-unit pair the table
        admits; re-authored as every paycheck, the start lifts to
        ``2026-01-02`` and the pair inverts.  The refusal fires BEFORE the
        first column assignment, so every column reads as it did.
        """
        user_id = seed_user["user"].id
        calendar = calendar_for(user_id)
        template = bare_expense_template(db.session, seed_user)
        rule = author_rule(
            spec_for(
                MONTHLY, user_id=user_id,
                starts_on=date(2025, 6, 1), end_bound=EndsOnDate(on=date(2025, 12, 1)),
            ),
            calendar, template,
        )
        db.session.flush()
        before = derived_columns(rule) | {"end_date": rule.end_date}

        with pytest.raises(EmptyAuthoredWindowError):
            reauthor_rule(
                rule,
                replace(
                    recurrence_spec(rule),
                    unit=RecurrenceUnitEnum.PERIOD,
                    placement=PeriodPlacementEnum.CONTAINING_DATE,
                    interval_n=1,
                ),
                calendar,
            )

        assert derived_columns(rule) | {"end_date": rule.end_date} == before


class TestSalaryProfileWriter:
    """``salary.create_profile`` -- one of the five production writers."""

    def test_a_created_profile_carries_a_resolved_every_period_rule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The salary template's rule resolves to every-paycheck from period 0.

        ``seed_periods`` opens the schedule on 2026-01-02, and the rule names
        no start period, so its first occurrence is that opening payday.
        """
        filing_status = db.session.query(FilingStatus).filter_by(
            name="single",
        ).one()

        resp = auth_client.post("/salary", data={
            "name": "Day Job",
            "pay_amount": "4000.00",  # $104,000.00 a year / 26
            "pay_payday": seed_periods[0].start_date.isoformat(),
            "filing_status_id": str(filing_status.id),
            # A state the law lists: since plan step salary:X-at-3 the create
            # door refuses any other (it posted "PA" until then).
            "state_code": "NC",
        })
        # A refused create redirects too, so the 302 alone cannot tell the two
        # apart: the profile must exist.
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/edit"), resp.headers["Location"]

        rule = sole_rule_owned_by(seed_user["user"].id)
        assert_resolves_completely(rule)
        assert_reauthoring_changes_nothing(rule)
        resolved = resolved_for(rule)
        assert resolved.starts_on == seed_periods[0].start_date
        assert resolved.unit is RecurrenceUnitEnum.PERIOD
        assert resolved.interval_n == 1


class TestLoanPaymentTransferWriter:
    """``loan.create_payment_transfer`` plus the loan-sync re-author."""

    @pytest.mark.usefixtures("seed_periods")
    def test_the_created_rule_anchors_on_the_loans_contractual_day(
        self, auth_client, seed_user, db,
    ):
        """The first occurrence IS the loan's first contractual installment.

        With an origination of 2023-06-01 and a payment day of 1, that
        installment is 2023-07-01, and since plan step R7c-b that is what the
        column holds: ``loan_cadence_start`` derives it once, before the rule
        is built (developer ruling, Fork 2).

        **It used to hold 2026-02-01**, the first day-1 occurrence at or after
        the schedule's opening -- because the date was an opening BOUND and the
        resolver clamped it to the schedule before deriving an anchor.  That
        made the stored value HORIZON-DEPENDENT, which is plan ledger row
        D10's own shape: extending the schedule backwards moved it.  The
        loan's first installment is a fact about the loan.

        **Generation is unchanged, and that was measured rather than assumed**:
        the walk emits 35 occurrences from 2023-07-01, of which 4 place -- the
        first at 2026-02-01, exactly as before.  The 31 that precede the
        schedule place nowhere and are dropped, which is the same reason a
        payment does not generate before its loan exists.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Mortgage",
            principal=Decimal("250000.00"), rate=Decimal("0.06500"), term=360,
            origination_date=date(2023, 6, 1), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )

        resp = auth_client.post(
            f"/accounts/{loan.id}/loan/create-transfer",
            data={"source_account_id": str(seed_user["account"].id)},
        )
        assert resp.status_code == 302

        rule = sole_rule_owned_by(seed_user["user"].id)
        assert_resolves_completely(rule)
        assert_reauthoring_changes_nothing(rule)
        assert scheduling_day_of_month(rule) == 1
        assert rule.starts_on == date(2023, 7, 1)
        resolved = resolved_for(rule)
        assert resolved.starts_on == date(2023, 7, 1)
        assert resolved.unit is RecurrenceUnitEnum.MONTH
        # The first occurrence the SCHEDULE can host is still 2026-02-01, so
        # what generates is unchanged; only where the fact is recorded moved.
        placed = [
            placement
            for placement in occurrence_placements(
                resolved, calendar_for(rule.user_id),
            )
            if placement.period is not None
        ]
        assert placed[0].occurrence == date(2026, 2, 1)

    @pytest.mark.usefixtures("seed_periods")
    def test_a_payment_day_edit_moves_the_anchor_with_it(
        self, seed_user, db,
    ):
        """Moving the payment day moves the first occurrence with it.

        The first occurrence is DERIVED from the loan's terms, so the two
        cannot disagree -- which is the point of plan step R2d, and of the
        ruling that made ``loan_cadence_start`` the one producer.  Before the
        write door existed, ``_sync_loan_cadence`` wrote ``day_of_month`` and
        ``start_date`` alone; while the anchor was a stored column that left
        it on the day the servicer no longer bills, with no query able to tell
        the stale value from a fresh one.

        Origination 2023-06-01: at payment day 1 the first installment is
        2023-07-01, and at 20 it is 2023-07-20 -- the first billing month
        is the one after origination either way, so only the DAY moves.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Auto",
            principal=Decimal("20000.00"), rate=Decimal("0.04000"), term=48,
            origination_date=date(2023, 6, 1), payment_day=1,
            account_type=AcctTypeEnum.AUTO_LOAN,
        )
        rule = author_rule(
            spec_for(
                MONTHLY,
                user_id=seed_user["user"].id,
                starts_on=date(2026, 2, 1),
            ),
            calendar_for(seed_user["user"].id),
            bare_expense_template(db.session, seed_user),
        )
        bind_rule_to_loan(rule, loan.id)
        db.session.flush()
        assert resolved_for(rule).starts_on == date(2023, 7, 1)

        params = loan.loan_params
        params.payment_day = 20
        bind_rule_to_loan(rule, loan.id)
        db.session.flush()

        assert scheduling_day_of_month(rule) == 20
        assert resolved_for(rule).starts_on == date(2023, 7, 20)
        assert_reauthoring_changes_nothing(rule)


class TestScheduleRebuildRepoint:
    """What a full schedule RESET does to a recurrence rule: nothing.

    ``pay_period_admin`` used to capture every rule carrying a start period
    before the wipe and re-point it at the new schedule's first period
    afterwards, because the pay-period delete cascade SET-NULLed that FK --
    which made a rule the cascade nulled indistinguishable from one that
    legitimately had no explicit start.  Plan step R7b-4 deleted both halves:
    a rule's opening bound is a DATE, which no cascade can reach, and
    ``resolve`` measures it against whatever schedule the owner has now.

    So the class name outlives the function it named, and these cases pin what
    replaced it.
    """

    def test_a_reset_leaves_a_bounded_rule_untouched(
        self, seed_user, db, seed_periods,
    ):
        """The rule is not written at all, and its anchor still follows.

        Rebuilding from 2027-03-05 leaves every authored column byte-identical
        -- there is no FK to re-point -- while the anchor moves, because it is
        ``max(new opening payday, start_date)`` and the new opening dominates
        a bound from the deleted schedule.  Same first occurrence the re-point
        produced, from a rule nothing rewrote.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                EVERY_PERIOD,
                user_id=user_id,
                starts_on=seed_periods[0].start_date,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()
        assert resolved_for(rule).starts_on == date(2026, 1, 2)
        before = authored_columns(rule)

        new_periods = pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, rhythm=rhythm_of(14),
        )
        db.session.flush()

        assert new_periods[0].start_date == date(2027, 3, 5)
        assert authored_columns(rule) == before
        assert resolved_for(rule).starts_on == date(2027, 3, 5)

        # **The stored date is stale and the READ answer is not, which is what
        # plan step R7c-b narrowed this to.**  The door writes ``starts_on`` on
        # every RULE write and on no other event, so a schedule rebuilt with
        # nothing rewriting the rule leaves the column holding a payday the
        # owner no longer has.  What changed at R7c-b is the blast radius: a
        # CALENDAR cadence's date is authored and a rebuild must not move it at
        # all, while a PAY-PERIOD one is normalised onto the paycheck that
        # hosts it -- so the READ re-normalises against whatever schedule
        # exists now and answers the new opening payday.  The stored value is
        # repaired by the next write, and nothing reads it without resolving.
        #
        # The MIGRATION re-ran the backfill for exactly this reason: a database
        # that sat between R7c-a and R7c-b through a rebuild would otherwise
        # have carried the stale date into the leaf that makes it
        # authoritative.
        new_calendar = calendar_for(user_id)
        assert rule.starts_on == date(2026, 1, 2)
        assert resolve(
            recurrence_spec(rule), new_calendar,
        ).starts_on == date(2027, 3, 5)

        reauthor_rule(rule, recurrence_spec(rule), new_calendar)
        db.session.flush()
        assert rule.starts_on == date(2027, 3, 5), (
            "a rule REWRITTEN after the rebuild must pick the new schedule up"
        )
        assert_reauthoring_changes_nothing(rule)

    def test_a_reset_keeps_a_bound_that_falls_INSIDE_the_new_schedule(
        self, seed_user, db, seed_periods,
    ):
        """The half the re-point got wrong, and the reason it is gone.

        The re-point moved EVERY captured rule to the new first period,
        whatever date the user had chosen.  Rebuild a schedule to open on
        2026-01-02 while a rule states it starts 2026-03-27, and the re-point
        answered "starts 2026-01-02" -- silently discarding the user's stated
        start because the FK it had to restore could only name the first
        period.  A date has nothing to restore: it survives the rebuild
        untouched.

        2026-01-02 + 6 x 14 days is 2026-03-27, so the stated date IS index
        6's payday -- which is what makes it a legal first occurrence for a
        paycheck-space rule, and why the normalisation leaves it alone.
        """
        user_id = seed_user["user"].id
        stated_start = date(2026, 3, 27)
        rule = author_rule(
            spec_for(
                EVERY_PERIOD,
                user_id=user_id,
                starts_on=stated_start,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        pay_period_admin.reset_pay_periods(
            user_id, date(2026, 1, 2), num_periods=10, rhythm=rhythm_of(14),
        )
        db.session.flush()

        assert rule.starts_on == stated_start
        assert resolved_for(rule).starts_on == stated_start

    def test_a_reset_re_phases_an_every_n_rule_through_the_new_schedule(
        self, seed_user, db, seed_periods,
    ):
        """The phase follows the bound onto the rebuilt schedule.

        The pre-seam bulk update wrote ``offset_periods = 0`` as a
        hand-maintained copy of ``first_period.period_index % interval_n``.
        Nothing transcribes it now and nothing re-points either: the rule
        keeps its stated bound, the new schedule's opening dominates it, and
        the phase is the ordinal of the paycheck that maximum falls in --
        index 0, so 0 for every interval.  Same value the copy asserted, with
        no writer left to get it wrong.

        The COLUMN is stale until something re-authors the rule, and that is
        correct rather than overlooked: nothing reads it (plan step R7b-4), so
        the resolved answer below is what every consumer sees.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                EVERY_N_PERIODS,
                user_id=user_id,
                interval_n=3,
                starts_on=seed_periods[2].start_date,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()
        assert resolved_for(rule).offset_periods == 2  # index 2 % interval 3

        pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, rhythm=rhythm_of(14),
        )
        db.session.flush()

        resolved = resolved_for(rule)
        assert resolved.offset_periods == 0  # new index 0 % interval 3
        assert resolved.interval_n == 3

        # **The stale-phase COLUMN this used to assert about is DROPPED**
        # (plan step R7c-c).  It held the OLD schedule's phase until a
        # re-author brought it into line, and the case pinned both halves
        # because a reader could otherwise suspect the reset had left
        # something wrong.  There is no column to be stale: the phase is
        # derived from the first occurrence on every read, which is what the
        # assertion above already measures.  Re-authoring is still exercised,
        # because idempotence after a reset is a different claim from the
        # resolved answer being right.
        reauthor_rule(rule, recurrence_spec(rule), calendar_for(user_id))
        db.session.flush()
        assert resolved_for(rule).offset_periods == 0

    def test_a_rule_with_NO_start_period_follows_the_reset_without_a_write(
        self, seed_user, db, seed_periods,
    ):
        """Its anchor moves because it is COMPUTED, not because it is rewritten.

        This is the case that made plan step R2d worth doing, and it inverts
        an earlier test.  ``resolve`` measures the anchor from the GREATEST of
        the start period, the rule's ``start_date``, and the SCHEDULE'S
        OPENING PAYDAY -- so a reset moves it for a rule naming no start
        period at all.  While the anchor was a stored column that had to be
        re-WRITTEN, and a neutral review found that the re-point pass did not:
        three of the developer's 50 live rules kept a first occurrence from
        the schedule that had just been deleted, a value NOT NULL could never
        have caught because it was not null, only wrong.

        With the anchor computed there is nothing to strand.  The rule is not
        touched at all -- every authored column is byte-identical afterwards
        -- and it still resolves onto the new schedule.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                EVERY_PERIOD,
                user_id=user_id,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()
        assert rule.starts_on == seed_periods[0].start_date
        assert resolved_for(rule).starts_on == seed_periods[0].start_date
        before = authored_columns(rule)

        pay_period_admin.reset_pay_periods(
            user_id, date(2027, 3, 5), num_periods=10, rhythm=rhythm_of(14),
        )
        db.session.flush()

        # Every authored column survives the reset unchanged.  This does not
        # by itself prove the rule was never re-authored -- re-authoring it
        # would also be a value no-op -- and it does not need to: what matters
        # is that no stored value went stale, which is the failure the old
        # stored anchor had.
        assert authored_columns(rule) == before
        # And it still answers for the schedule that exists now.
        assert resolved_for(rule).starts_on == date(2027, 3, 5)


class TestTheClampIsResolvedAndStoredOnTheRule:
    """The month-end clamp is carried by the RULE, never by a subtype row.

    **Rewritten at plan step R7c-a**, which falsified what this class used to
    say: that ``budget.recurrence_month_anchors`` holds the day a clamped
    anchor lost and that "there is no such column until plan step R7c, so the
    table must stay empty".  There is such a column now --
    ``recurrence_rules.nominal_day`` -- and ruling R-R16 means the satellite
    table is never written at all: it is dropped unwritten at R7c-c.

    So the guard flips.  It is no longer "nothing is stored"; it is **the
    clamp is stored on the rule and cleared from the rule**, and the subtype
    stays empty because it has no writer and never will.  Asserting the
    CLEARING is what the previous version could not: it checked the resolved
    value only, and a write door that set ``nominal_day`` and never unset it
    would have passed -- restoring the 31st on the next read of a rule the
    user had moved to the 15th, which is the exact residue the old row-based
    design was rejected for.
    """

    @pytest.mark.usefixtures("seed_periods")
    def test_a_clamped_day_resolves_its_nominal_day_and_writes_no_row(
        self, seed_user, db,
    ):
        """A day-31 rule anchored in a 30-day month: April has no 31st.

        The resolved anchor clamps to 2026-04-30 while ``nominal_day``
        carries the 31 the user meant, so nothing is lost -- and the day the
        rule fires on is that pair READ TOGETHER rather than a third column
        holding it, which is what ruling R-R16 collapsed and plan step R7c-c
        dropped.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                MONTHLY,
                user_id=user_id,
                starts_on=date(2026, 4, 30),
                nominal_day=31,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        resolved = resolved_for(rule)
        assert resolved.starts_on == date(2026, 4, 30)
        assert resolved.nominal_day == 31
        assert scheduling_day_of_month(rule) == 31
        # The COLUMNS carry what the resolved value carries (plan step
        # R7c-a): the date the month could hold, and the day the rule meant.
        assert rule.starts_on == date(2026, 4, 30)
        assert rule.nominal_day == 31

    @pytest.mark.usefixtures("seed_periods")
    def test_changing_the_day_changes_the_resolved_clamp(
        self, seed_user, db,
    ):
        """Moving the rule to the 15th drops the clamp, with no row to clean up.

        Presence was the discriminator when the row existed, and a surviving
        row would have restored the 31st on the next read of a rule the user
        moved to the 15th -- the residue an upsert-only backfill leaves.
        Recomputing removes the failure mode rather than the residue.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                MONTHLY,
                user_id=user_id,
                starts_on=date(2026, 4, 30),
                nominal_day=31,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()
        assert resolved_for(rule).nominal_day == 31

        reauthor_rule(
            rule,
            replace(
                recurrence_spec(rule),
                starts_on=date(2026, 4, 15), nominal_day=None,
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        resolved = resolved_for(rule)
        assert resolved.starts_on == date(2026, 4, 15)
        assert resolved.nominal_day is None
        # **The CLEARING, which is what this case exists for.**  A door that
        # set the column and never unset it would leave 31 here and restore
        # the month-end on the next read.
        assert rule.starts_on == date(2026, 4, 15)
        assert rule.nominal_day is None
        assert scheduling_day_of_month(rule) == 15
        assert_reauthoring_changes_nothing(rule)


class TestTheIntervalColumnSaysWhatTheCadenceSays:
    """``interval_n`` and the resolved cadence agree, for every cadence.

    **This class asserted the OPPOSITE until plan step R7c-c, deliberately.**
    ``encode_cadence`` wrote ``1`` for every pattern whose interval was baked
    into its NAME, so a Quarterly rule stored ``(unit_id = month, interval_n =
    1)`` -- read at face value, MONTHLY: 12 occurrences a year where 4 are
    owed, over the whole projection, in generated rows and in the projected
    balance.  Nothing read it that way, because every reader went through
    ``decode_pattern``; the class pinned the inequality so that it would go red
    the moment someone re-pointed the column, and whoever did that would be the
    person who had to move the readers with it.

    That happened.  The migration re-points the column and drops the pattern it
    had to be read through, so the pair can be honest and this class says so.
    It is the same guard turned around: it goes red if an encoding ever comes
    back.
    """

    @pytest.mark.usefixtures("seed_periods")
    @pytest.mark.parametrize(
        ("cadence", "two_axis_interval"),
        [
            (EVERY_PERIOD, 1),
            (MONTHLY, 1),
            (MONTHLY_FIRST, 1),
            (QUARTERLY, 3),
            (SEMI_ANNUAL, 6),
            (ANNUAL, 1),
        ],
        ids=lambda value: getattr(value, "label", None),
    )
    def test_the_column_holds_the_cadences_own_interval(
        self, seed_user, db, cadence, two_axis_interval,
    ):
        """The stored column and the resolved cadence name one number.

        Swept over every named cadence rather than the two the encoding
        touched, because the property is now about all of them: a re-introduced
        encoding would show up on whichever one it re-encoded.

        Args:
            seed_user: The owner fixture.
            db: The session fixture.
            cadence: The cadence to author.
            two_axis_interval: The interval it means.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                cadence, user_id=user_id, starts_on=date(2026, 1, 15),
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        assert rule.interval_n == two_axis_interval, (
            "the column stopped saying what the cadence says.  A stored "
            "interval that disagrees with the rule's own rhythm generates a "
            "bill at the wrong frequency for the life of the projection"
        )
        assert resolved_for(rule).interval_n == two_axis_interval


class TestPhasePreservedAcrossAnEdit:
    """Defect D1: an edit used to re-phase every future occurrence."""

    def test_re_authoring_keeps_the_phase_the_start_period_states(
        self, seed_user, db, seed_periods,
    ):
        """An edit that does not touch the schedule leaves the phase alone.

        The pre-seam update path wrote ``offset_periods`` from the payload,
        and no template renders an offset input -- so the value was always the
        schema default 0, and every future occurrence of an every-3-paychecks
        rule shifted by one pay period on an amount-only edit.

        **The defect became UNCONSTRUCTIBLE at plan step R7b-4**, and this
        case is what says so.  Plan step R2d made the phase derive from the
        rule's start period on every write, which fixed the behaviour while
        leaving a phase field on the spec that a caller could still state and
        the door still ignored.  R7b-4 deleted the field: an edit re-reads the
        rule's authored state, replaces the one fact it owns, and re-authors,
        so there is no longer any value a payload can carry that names a
        phase at all.  The edit below is the very one that used to re-phase
        the rule -- everything but the amount left alone -- and the phase is
        still the paycheck the bound falls in.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                EVERY_N_PERIODS,
                user_id=user_id,
                interval_n=3,
                starts_on=seed_periods[2].start_date,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()
        # index 2 % interval 3.  Read through the RESOLVER since plan step
        # R7c-c dropped the column: the phase is derived from the rule's
        # first occurrence on every read, which is the whole reason defect
        # D1's stale-column shape is now unconstructible rather than guarded.
        assert resolved_for(rule).offset_periods == 2

        # An amount-only edit: read the rule whole, change nothing the phase
        # depends on, write it whole.
        reauthor_rule(
            rule, recurrence_spec(rule), calendar_for(user_id),
        )
        db.session.flush()

        assert resolved_for(rule).offset_periods == 2


class TestWhatTheWriteDoorNORMALISES:
    """The one column a re-author still moves, stated rather than discovered.

    **Two normalisations left at plan step R7c-c with the encoding that made
    them**: "every 1 paycheck" was stored under ``Every Period``'s name (two
    names for one reading, which the closed set forced a choice between), and a
    stale ``offset_periods`` was rewritten to 0 (that column is dropped, and
    the phase is derived on every read).

    What survives is the pay-period NORMALISATION of the first occurrence,
    which is not an encoding at all: a paycheck-space cadence fires on PAYDAYS,
    so a caller may author any date and ``resolve`` answers the payday of the
    paycheck that hosts it.  A stored value that was not a real occurrence is
    what ruling R-R16 removed, and this is the case that says the door applies
    it.
    """

    @pytest.mark.usefixtures("seed_periods")
    def test_a_mid_period_date_is_seated_on_its_paychecks_payday(
        self, seed_user, db, seed_periods,
    ):
        """A pay-period cadence stores a PAYDAY, never the authored date.

        Asserted with the occurrences either side, because a column pinned
        alone says nothing about whether the rule fires where the user meant:
        the first occurrence the walk yields must BE the stored value, which is
        what makes ``starts_on`` a real occurrence by construction rather than
        by two functions agreeing.
        """
        user_id = seed_user["user"].id
        payday = seed_periods[1].start_date
        mid_period = payday + timedelta(days=3)

        rule = author_rule(
            spec_for(
                EVERY_PERIOD, user_id=user_id, starts_on=mid_period,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        assert rule.starts_on == payday
        assert rule_occurrences(rule, calendar_for(user_id))[0].occurrence == (
            payday
        )

    @pytest.mark.usefixtures("seed_periods")
    def test_a_whole_number_of_years_in_months_is_stored_as_years(
        self, seed_user, db,
    ):
        """Ruling **R-R17** at the door: one rhythm, one spelling.

        Reachable from the form the moment the interval became a free box --
        a user may type "every 12 months" -- and it is the same rhythm as
        "every 1 year".  Two spellings would word one annual bill two ways on
        the Recurring surface and group them apart in the obligations filter.

        The pure substitution is covered in ``test_recurrence_frequency``; this
        is what says the DOOR applies it, which is what stores it.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                MONTHLY, user_id=user_id, interval_n=12,
                starts_on=date(2026, 3, 15),
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        assert rule.interval_n == 1
        assert rule.unit_id == ref_cache.recurrence_unit_id(
            RecurrenceUnitEnum.YEAR,
        )
        assert resolved_for(rule).unit is RecurrenceUnitEnum.YEAR
        assert_reauthoring_changes_nothing(rule)


class TestTheIntervalRoundTripsThroughTheColumn:
    """An authored interval is the interval read back, for every unit.

    **This class changed subject twice.**  It first asserted that a calendar
    PATTERN named its own interval so the form's hidden input could not reach
    it; plan step R7b moved that protection into ``decode_pattern``, which
    ignored a posted interval for every pattern that named one; plan step R7c-c
    deletes both, because there is no encoding left for a value to survive.

    What is left is the property the whole seam rests on and the only one that
    was ever about money: an authored cadence written through the door and read
    back is the SAME cadence.  It is now a straight round trip through one
    column, which is the point of the leaf.
    """

    @pytest.mark.usefixtures("seed_periods")
    @pytest.mark.parametrize(
        ("cadence", "interval_n"),
        [
            (EVERY_N_PERIODS, 4),
            (MONTHLY, 2),
            (MONTHLY, 5),
            (QUARTERLY, 3),
            (ANNUAL, 2),
        ],
        ids=lambda value: getattr(value, "label", None),
    )
    def test_an_authored_interval_reads_back_unchanged(
        self, seed_user, db, cadence, interval_n,
    ):
        """Including the intervals the closed pattern set could not name.

        ``(2, MONTH)`` and ``(2, YEAR)`` are the two cadences this arc exists
        for: they resolve and walk correctly and had nowhere to be written
        until this leaf.  Sweeping them beside the ones the closed set could
        name is what says the column is general rather than widened for a case.

        Args:
            seed_user: The owner fixture.
            db: The session fixture.
            cadence: The cadence whose unit and placement to author.
            interval_n: The interval to state.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                cadence, user_id=user_id, interval_n=interval_n,
                starts_on=date(2026, 2, 10),
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        assert rule.interval_n == interval_n
        assert recurrence_spec(rule).interval_n == interval_n
        assert resolved_for(rule).interval_n == interval_n

    def test_switching_from_paychecks_to_quarterly_restates_the_cadence(
        self, seed_user, db, seed_periods,
    ):
        """An edit that changes the UNIT changes the interval with it.

        A caller states ``(3, MONTH)`` and the row cannot come to mean "every 4
        months", because the interval it stated is what it gets back.  The
        column carried a PAYCHECK count before the edit, so this is also the
        case that says nothing of the old cadence survives into the new one.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                EVERY_N_PERIODS,
                user_id=user_id,
                interval_n=4,
                starts_on=seed_periods[0].start_date,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        reauthor_rule(
            rule,
            replace(
                recurrence_spec(rule),
                unit=RecurrenceUnitEnum.MONTH,
                interval_n=3,
                starts_on=date(2026, 2, 10),
            ),
            calendar_for(user_id),
        )
        db.session.flush()

        resolved = resolved_for(rule)
        assert resolved.interval_n == 3
        assert resolved.unit is RecurrenceUnitEnum.MONTH
        assert resolved.placement is PeriodPlacementEnum.CONTAINING_DATE
        assert rule.interval_n == 3
        assert_reauthoring_changes_nothing(rule)


class TestEveryCadenceAuthorsAndResolves:
    """Both invariants, stated over every cadence the app can author."""

    @pytest.mark.parametrize(
        "cadence", BASELINE_CADENCES, ids=lambda c: c.label,
    )
    def test_an_authored_rule_resolves_and_round_trips(
        self, seed_user, db, seed_periods, cadence,
    ):
        """Every cadence resolves completely, and re-authoring is a no-op.

        Parametrised over the whole shared vocabulary rather than a sample.
        Completeness is the property the ``NOT NULL`` columns rest on -- a
        cadence that resolved partially would be an un-storable row -- and
        idempotence is what makes the read-modify-re-author idiom safe for
        every writer in the application.
        """
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                cadence,
                user_id=user_id,
                starts_on=seed_periods[1].start_date,
            ),
            calendar_for(user_id),
            bare_expense_template(db.session, seed_user),
        )
        db.session.flush()

        assert_resolves_completely(rule)
        assert_reauthoring_changes_nothing(rule)

    @pytest.mark.usefixtures("db", "seed_periods")
    def test_the_rule_is_flushed_with_an_id_the_caller_can_link(
        self, seed_user,
    ):
        """``author_rule`` flushes, because every caller links the rule next."""
        user_id = seed_user["user"].id
        rule = author_rule(
            spec_for(
                EVERY_PERIOD,
                user_id=user_id,
            ),
            calendar_for(user_id),
            bare_expense_template(_db.session, seed_user),
        )

        assert rule.id is not None
        assert _db.session.get(RecurrenceRule, rule.id) is rule
