"""Database CHECK constraint regression tests for budget.recurrence_rules.

Locks the STORAGE tier: what the table itself refuses, with no service, no
schema and no form in front of it.  Two families live here.

**The last day bound left at plan step recurrence:R5-a with its column.**
``ck_recurrence_rules_due_dom`` (migration f15a72a3da6c) bounded
``due_day_of_month`` to 1..31; ruling **R-R96** dropped the column (a rule's
own day is the day its rows are due), and its three cases went with it.

**Two SIBLING bounds left at plan step R7c-c with their columns.**
``ck_recurrence_rules_dom`` and ``ck_recurrence_rules_moy`` (both migration
1702cadcae54, the H-3 fix) bounded ``day_of_month`` and ``month_of_year``, the
storage encoding the write door derived from the resolved first occurrence.
Ruling **R-R16** collapsed both into ``starts_on`` -- a real DATE, which cannot
hold a 99th day or a 15th month at all -- so the states those CHECKs refused
are unrepresentable rather than refused, and
``ck_recurrence_rules_starts_on_range`` is what bounds the value that replaced
them.  This file's own docstring predicted the retirement one leaf earlier.

**The two CHECKs plan step R7c-b added**, which is the other half of that
step: with the two-axis columns authored and ``NOT NULL``, the table can state
rules it previously could not, and each closes a state a service guard used to
stand in for.  A THIRD was drafted -- ``end_date >= starts_on`` -- and held
back on a developer ruling until plan step R7d-g landed it as
``ck_recurrence_rules_valid_window``; :class:`TestTheWindowIsHeldAtTheTableToo`
carries the reason for the wait and pins the state it now refuses.

  * ck_recurrence_rules_nominal_day -- COMPLETED at R7c-b with the clamp
    equality, which is what let ``_occurrence._require_generable`` lose its
    third refusal;
  * ck_recurrence_rules_starts_on_range -- the first occurrence falls inside
    the calendar this application reaches.

Every rule here is built COLUMN BY COLUMN rather than through
``recurrence.author_rule``, and deliberately: the door refuses each of these
values before it writes, so authoring one would exercise the door and prove
nothing about the table.  :func:`_storable_columns` supplies the four ``NOT
NULL`` columns every row needs so that the INSERT reaches the CHECK under test
rather than dying on a null.

Audit reference: H-3 of
shekel-audits/security-2026-04-15/model-migration-drift.md.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from tests._test_helpers import bare_expense_template

#: A first occurrence inside the calendar window, on a day every month holds.
#:
#: Day 15 so no case below is accidentally month-end clamped: the nominal-day
#: CHECK turns on whether ``starts_on`` IS its own month's last day, and a
#: fixture that happened to sit on one would make the range cases pass for the
#: wrong reason.
_A_FIRST_OCCURRENCE = date(2026, 1, 15)


def _storable_columns(**overrides):
    """Return the columns a rule needs to REACH a CHECK, plus *overrides*.

    Plan step R7c-b made ``unit_id``, ``placement_id``, ``shift_id`` and
    ``starts_on`` ``NOT NULL``, so a row stating only a pattern now fails on a
    null before any CHECK is evaluated -- which would turn every case in this
    file green-for-the-wrong-reason if it were left to fail that way, since
    ``IntegrityError`` covers both.  Each case therefore starts from a storable
    row and poisons exactly the column it is about.

    An ordinary monthly cadence: the values name a real recurrence, so nothing
    here is itself the violation under test.

    Args:
        **overrides: Columns to set or replace.

    Returns:
        dict: The keyword arguments for a :class:`RecurrenceRule`.
    """
    columns = {
        "unit_id": ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH),
        "placement_id": ref_cache.period_placement_id(
            PeriodPlacementEnum.CONTAINING_DATE,
        ),
        "shift_id": ref_cache.business_day_shift_id(
            BusinessDayShiftEnum.NONE,
        ),
        "starts_on": _A_FIRST_OCCURRENCE,
    }
    columns.update(overrides)
    return columns


def _owner_id(seed_user):
    """Return a flushed, cadence-less template id for a rule to hang off.

    **Plan step R-F6 is why every construction below needs one**: the owning
    FK moved onto ``budget.recurrence_rules`` under
    ``ck_recurrence_rules_one_owner``, so a rule with no definition is refused
    by a constraint that is not the one any case here is about -- and a case
    that failed on the wrong constraint would still be green, which is exactly
    the confusion :func:`_refused` names the constraint to avoid.

    Args:
        seed_user: The ``seed_user`` fixture dict.

    Returns:
        The new template's id.
    """
    return bare_expense_template(db.session, seed_user).id


def _refused(seed_user, constraint, label="", **columns):
    """Flush a rule built from *columns* and assert *constraint* refuses it.

    Args:
        seed_user: The ``seed_user`` fixture dict, whose user owns the rule.
        constraint: The CHECK constraint name the message must name.
        label: What to name in the failure, for a parametrized case.
        **columns: Passed to :func:`_storable_columns`.

    Raises:
        AssertionError: When the flush succeeds, or a DIFFERENT constraint
            refuses it -- which is what tells a "NOT NULL caught it first"
            failure apart from the refusal the case is about.
    """
    rule = RecurrenceRule(
        transaction_template_id=_owner_id(seed_user), **_storable_columns(**columns),
    )
    db.session.add(rule)
    with pytest.raises(IntegrityError) as exc_info:
        db.session.flush()
    assert constraint in str(exc_info.value), label
    db.session.rollback()


class TestRecurrenceRuleRangeConstraints:
    """A rule's plain columns at flush time (the day bounds left with their
    columns; see the module docstring)."""

    def test_interval_n_defaults_non_null(self, app, db, seed_user):
        """A rule created without ``interval_n`` persists 1, never NULL.

        The column is NOT NULL with a server_default of 1 plus the model's
        Python ``default=``, so a rule constructed without setting it lands a
        real integer once persisted.  The occurrence walk divides and takes a
        modulus by it and the monthly equivalent divides by it, with NO
        ``or 1`` coalesce (deep-hunt #65), so this pins the invariant that
        makes those safe: a persisted rule can never feed them ``None``.

        **``offset_periods`` left this case at plan step R7c-c** with the
        column: the cycle phase is derived from the rule's first occurrence on
        every read, so there is no stored value to default.
        """
        with app.app_context():
            rule = RecurrenceRule(
                transaction_template_id=_owner_id(seed_user), **_storable_columns(),
            )
            db.session.add(rule)
            db.session.flush()
            assert rule.interval_n == 1
            db.session.rollback()

class TestTheNominalDayIsOnlyEverAClamp:
    """``ck_recurrence_rules_nominal_day``, completed at plan step R7c-b.

    ``nominal_day`` records the day a rule MEANS when its first occurrence's
    own month was too short to hold it (ruling R-R3), and NOTHING else.  Three
    conjuncts carry that, none implied by the others, and each has a case here
    because until R7c-b only two of them were on the table -- the third lived
    as a runtime guard in ``_occurrence._require_generable``, which that step
    deleted once the schema could say the whole rule.
    """

    def test_the_clamped_pair_is_admitted(self, app, db, seed_user):
        """The one shape the column exists for: April 30 meaning the 31st.

        The positive control.  Without it every refusal below would also pass
        against a CHECK that simply refused every non-NULL value, which would
        delete month-end recurrence from the application.
        """
        with app.app_context():
            rule = RecurrenceRule(
                transaction_template_id=_owner_id(seed_user),
                **_storable_columns(
                    starts_on=date(2026, 4, 30), nominal_day=31,
                ),
            )
            db.session.add(rule)
            db.session.flush()

            assert rule.id is not None
            db.session.rollback()

    def test_a_day_the_month_could_hold_is_refused(self, app, db, seed_user):
        """(2026-04-15, 30): a nominal day beside a date that never clamped.

        **The conjunct plan step R7c-b added, and the reason it is worth a
        case.**  30 is in range and it does exceed the date's 15, so R7c-a's
        two-conjunct CHECK admitted this row -- and April HAS a 30th, so the
        rule would fire on a day ``starts_on`` does not name and no surface
        could say which of the two was meant.

        NEGATIVE CONTROL: drop the LEAST(...) conjunct from
        ``ck_recurrence_rules_nominal_day`` and this goes green.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_nominal_day",
                starts_on=date(2026, 4, 15), nominal_day=30,
            )

    def test_a_day_at_or_below_the_dates_own_is_refused(
        self, app, db, seed_user,
    ):
        """(2026-01-31, 31) restates the day the date already carries.

        January HAS a 31st, so this pair says one thing twice -- the two
        representations ruling R-R16 removes.  The ``>`` conjunct is what
        refuses it.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_nominal_day",
                starts_on=date(2026, 1, 31), nominal_day=31,
            )

    def test_a_day_below_29_is_refused(self, app, db, seed_user):
        """(2026-02-27, 28) names a day no month is ever too short to hold.

        The domain conjunct.  Every month holds its first 28 days, so a value
        below 29 can never be a clamp; the pair also fails the equality above,
        which is why the case names the constraint rather than the branch.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_nominal_day",
                starts_on=date(2026, 2, 27), nominal_day=28,
            )


class TestTheWindowIsHeldAtTheTableToo:
    """``end_date >= starts_on`` is a CHECK since plan step R7d-g (``ck_recurrence_rules_valid_window``).

    Until R7d-g this class pinned the CHECK's ABSENCE as the ruling it was
    (developer, 2026-08-15): the two columns held two different KINDS of fact
    -- a USER-authored stop before the start is a mistake to report, while the
    window ``loan_recurrence_sync`` DERIVED for a loan payment was empty
    whenever the loan owed nothing yet, a correct answer -- and a constraint
    cannot tell them apart, so the rule lived at the two authoring doors alone
    and the table's side of that division was asserted here so nobody re-added
    the CHECK by accident.  R7d-g deleted the derived writer, so every stored
    pair is an owner's word and the CHECK landed (migration ``bf50951a3599``,
    ruling **R-R80**); the doors keep the rule as the layer that can REPORT it.
    ``test_recurrence_window_check`` grades the constraint in every writer
    direction and the migration round trip; this class keeps the boundary and
    the two admitted shapes beside the other constraints of the table.
    """

    def test_an_end_before_the_start_is_REFUSED_by_the_table(
        self, app, db, seed_user,
    ):
        """The state that was storable until R7d-g, and is not now.

        The measured shape that held the CHECK back: a loan originating
        2026-08-01 with ``payment_day`` 1 owes its first installment 2026-09-01
        and is trued to zero on 2026-08-15, so the derived window is
        ``[2026-09-01, 2026-08-15]``.  The sync wrote that pair; nothing does
        now, and the composed door answers ``EMPTY`` for it as a value
        (``recurrence.DerivedStop``) that no column carries.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_valid_window",
                starts_on=date(2026, 9, 1), end_date=date(2026, 8, 15),
            )

    def test_an_end_ON_the_start_is_admitted(self, app, db, seed_user):
        """The boundary the DOORS use ``>=`` rather than ``>`` for.

        A real cadence -- a one-off whose closing bound is its own first
        occurrence, which fires exactly once.  Pinned here as well as at the
        doors because it is the case a tightening would break first.
        """
        with app.app_context():
            rule = RecurrenceRule(
                transaction_template_id=_owner_id(seed_user),
                **_storable_columns(
                    starts_on=date(2026, 6, 1), end_date=date(2026, 6, 1),
                ),
            )
            db.session.add(rule)
            db.session.flush()

            assert rule.id is not None
            db.session.rollback()

    def test_no_end_at_all_is_admitted(self, app, db, seed_user):
        """The common case: a rule that never ends, which 41 of 46 live rules are.

        Pinned so a future tightening cannot make every unbounded recurrence
        unstorable without breaking here loudly.
        """
        with app.app_context():
            rule = RecurrenceRule(
                transaction_template_id=_owner_id(seed_user),
                **_storable_columns(end_date=None),
            )
            db.session.add(rule)
            db.session.flush()

            assert rule.id is not None
            db.session.rollback()


class TestTheStartFallsInsideTheCalendar:
    """``ck_recurrence_rules_starts_on_range``, added at plan step R7c-b."""

    @pytest.mark.parametrize(
        "label,starts_on",
        [
            ("below the window", date(1999, 12, 31)),
            ("above the window", date(2101, 1, 1)),
        ],
    )
    def test_a_start_outside_the_window_is_refused(
        self, app, db, seed_user, label, starts_on,
    ):
        """2000-01-01..2100-12-31 is how far this application's calendar reaches.

        It backs a MEASURED 500 rather than tidiness: past the saved horizon
        the pay calendar projects the covering paycheck by adding
        ``cadence_days`` to a start, so a date near ``date.max`` raised
        ``OverflowError`` -- from outside the recurrence package's error
        hierarchy, so the preview endpoint's own handler did not catch it.

        Both edges, because a one-sided bound would leave the other half of
        the window open and the failure would look identical.
        """
        with app.app_context():
            _refused(
                seed_user, "ck_recurrence_rules_starts_on_range",
                label=label, starts_on=starts_on,
            )

    @pytest.mark.parametrize(
        "label,starts_on",
        [
            ("the first day", date(2000, 1, 1)),
            ("the last day", date(2100, 12, 31)),
        ],
    )
    def test_the_windows_own_edges_are_admitted(
        self, app, db, seed_user, label, starts_on,
    ):
        """BETWEEN is inclusive, and both endpoints are real dates.

        The positive control for the case above: an off-by-one that excluded
        an endpoint would make the refusals pass while quietly narrowing the
        window the application says it reaches.
        """
        with app.app_context():
            rule = RecurrenceRule(
                transaction_template_id=_owner_id(seed_user),
                **_storable_columns(starts_on=starts_on),
            )
            db.session.add(rule)
            db.session.flush()

            assert rule.id is not None, label
            db.session.rollback()
