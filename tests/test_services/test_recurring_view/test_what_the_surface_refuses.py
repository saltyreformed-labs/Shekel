"""What the surface refuses rather than words: a repeating definition under an owner with no
paydays, never read as one-time while a rule-less one still reads None; a cadence that cannot be
read, which refuses the active sections and not the archived drawer; and a prepared row holding
a rule without its reading, or a reading without its rule.
"""

from decimal import Decimal

import pytest

from app.extensions import db
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY, QUARTERLY
from app.models.pay_period import PayPeriod
from app.services import recurring_view
from app.services.obligations_aggregator import TodaysPrice
from app.services.pay_calendar import PayCalendar, PayCalendarError
from app.services.recurrence import RecurrenceResolutionError, read_rule

from ._helpers import _calendar, _ctx, _create_expense


def _empty_schedule(seed_user):
    """Return a read pass over an owner who has a cadence and NO paydays.

    The bootstrap period is deleted AFTER the case has authored its
    definition -- a rule is resolved against the schedule when it is written
    -- and the ``budget.pay_schedule`` row stays, so the calendar the pass
    derives is empty but still carries the owner's cadence.  That is the state
    :class:`TestNoneMeansDoesNotRepeat` is about, and it is deliberately not
    the cadence-less one :class:`TestAnAbsentCadenceIsRefused` covers.

    Args:
        seed_user: The owner whose paydays to delete.

    Returns:
        A pass whose ``calendar()`` holds no periods.
    """
    db.session.query(PayPeriod).filter_by(
        user_id=seed_user["user"].id,
    ).delete(synchronize_session=False)
    db.session.flush()
    ctx = _ctx(seed_user)
    assert not ctx.calendar().periods, "precondition: no pay periods"
    return ctx


class TestNoneMeansDoesNotRepeat:
    """One sentinel, one meaning, on the value the cell reads.

    ``recurrence is None`` renders "One-time".  ``resolved_recurrence`` also
    answers ``None`` for an owner with no pay periods, and letting the two
    share a sentinel would report a quarterly bill as a one-off -- on the
    surface whose whole job is to say how definitions repeat.  The empty
    schedule is unreachable through any application path (registration
    bootstraps period 0, ``truncate_pay_periods`` always keeps the period its
    ``keep_through_period_id`` names -- plan step C3-a, which replaced the
    ordinal wire key and with it the Marshmallow floor this sentence used to
    cite -- ``reset_pay_periods`` deletes and regenerates in one transaction),
    so it is refused rather than worded.

    **The empty schedules below carry a CADENCE, and that is deliberate** (plan
    step R7a-2a).  "No pay periods" and "no stated pay cadence" are two
    different states, and this class is about the first: an owner who HAS said
    how often they are paid but whose payday list is empty.  The second is
    refused one door earlier, by a different value, with a different exception
    -- see :class:`TestAnAbsentCadenceIsRefused`.  The state is built by
    deleting the owner's paydays and keeping their schedule row
    (:func:`_empty_schedule`), since plan step R7d-d put the surface on a read
    pass that derives its own calendar; a hand-built calendar with
    ``cadence_days=None`` would trip that earlier door and report a pass for a
    claim these cases never reached.
    """

    def test_an_empty_schedule_refuses_rather_than_reading_one_time(
        self, seed_user,
    ):
        """A repeating definition is never described as non-repeating."""
        tmpl = _create_expense(seed_user, QUARTERLY, Decimal("60.00"), day_of_month=2, month_of_year=3)
        ctx = _empty_schedule(seed_user)

        with pytest.raises(RecurrenceResolutionError, match="no pay periods"):
            recurring_view.build_view([], [tmpl], [], ctx)

    def test_the_archived_drawer_refuses_it_too(self, seed_user):
        """Both row kinds reach the same discriminator."""
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("25.00"), day_of_month=9)
        tmpl.is_active = False
        db.session.flush()
        ctx = _empty_schedule(seed_user)

        with pytest.raises(RecurrenceResolutionError, match="no pay periods"):
            recurring_view.build_archived_rows([tmpl], ctx)

    def test_a_rule_less_definition_still_answers_none(self, seed_user):
        """The control: the sentinel still means what it is supposed to.

        Without this, "raise whenever the resolution is absent" would pass the
        two cases above while breaking the only state ``None`` may describe.
        """
        tmpl = _create_expense(seed_user, None, Decimal("25.00"))
        ctx = _empty_schedule(seed_user)

        view = recurring_view.build_view([], [tmpl], [], ctx)

        assert view.expenses.rows[0].recurrence is None
        assert recurring_view.build_archived_rows(
            [tmpl], ctx,
        )[0].recurrence is None


class TestAnAbsentCadenceIsRefused:
    """No stated pay cadence -> no per-paycheck column (plan step R7a-2a).

    A DIFFERENT state from :class:`TestNoneMeansDoesNotRepeat`'s empty
    schedule, and refused one door earlier.  Every money figure this surface
    publishes exists in two units, and the second unit is "per paycheck" --
    which is unanswerable for an owner who has never said how often they are
    paid.  The alternative was to assume biweekly, which is precisely the
    hardcoded ``PAY_PERIODS_PER_YEAR = 26`` this step deleted: it would have
    reported a weekly-paid owner's commitments at double their per-paycheck
    cost with nothing on the page saying so.

    Unreachable through any application path -- registration writes the
    ``budget.pay_schedule`` row AND the paydays (plan step X-ad-a) -- so this
    is the control on a broken invariant, not on a user state.  *The paragraph
    here also said ``resolve_cadence`` "falls back to the last period's length
    for legacy owners"; plan step ``pay_calendar:C4-b-2`` deleted that
    inference and ``fk_pay_periods_schedule`` deleted the owner it served.*

    **Where the refusal LIVES moved at plan step ``pay_calendar:C4-d``**
    (ruling **R-PC45**).  This surface used to meet the cadence-less owner as a
    CALENDAR carrying ``cadence_days=None``, and refused when it read the
    cadence.  There is no such calendar now: ``pay_calendar.calendar_for``
    refuses that owner outright, so the surface is never handed one.

    *One case that stood here is DELETED rather than rewritten, and an
    adversarial review of that step is why.*  It asserted that ``build_view``
    refused a calendar built with ``cadence_days=None``.  The first rewrite
    made it assert that such a calendar cannot be BUILT and that
    ``calendar_for`` refuses -- two claims that touch this module not at all,
    already graded by ``TestTheCadenceIsRequired`` in
    ``test_pay_calendar_derivation/test_what_the_derivation_refuses.py`` and
    by ``test_pay_calendar_loader.py``, sitting in a file about
    ``recurring_view`` inside a class named for a property of it.  A duplicate in the wrong file
    is a test that goes stale where nobody looks for it.

    **No coverage was lost.**  The extra subject that case carried -- a
    RULE-LESS definition still reaching the section subtotal's cadence read --
    is exercised by ``test_a_rule_less_definition_still_answers_none``, which
    passes a rule-less template through ``build_view``.  What remains here is
    the one property that is still about THIS module: the archived drawer must
    not read a cadence the active sections do.
    """

    def test_the_archived_drawer_does_NOT_need_a_cadence(
        self, seed_user, seed_periods_today, monkeypatch,
    ):
        """The drawer has no money columns, so it must not read the cadence.

        **The asymmetry cannot be shown with a fixture, and an earlier draft of
        this test pretended otherwise.** It passed a normal calendar and
        asserted the drawer rendered -- which it would whether or not it read
        the cadence, so the control could not fail.  The two states that would
        distinguish it are both unconstructible: ``derive_periods`` refuses a
        non-empty payday set with ``cadence_days=None``, and an EMPTY calendar
        makes the drawer raise ``RecurrenceResolutionError`` for an unrelated
        reason (its sibling above pins that).

        So the cadence is made to REFUSE on a calendar that is otherwise
        healthy.  The drawer must still answer; ``build_view`` on the same
        calendar must not.  Both halves are asserted, because the first alone
        would pass if the property were removed from the value entirely.
        """
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("25.00"), day_of_month=9)
        tmpl.is_active = False
        db.session.flush()
        ctx = _ctx(seed_user)

        def _refuse(_self):
            raise PayCalendarError("the cadence was read")

        monkeypatch.setattr(PayCalendar, "cadence", property(_refuse))

        # The drawer words each rule's cadence phrase off the schedule -- which
        # needs paydays -- and converts no money, so it must not touch this.
        rows = recurring_view.build_archived_rows([tmpl], ctx)
        assert rows[0].recurrence.cadence == "Monthly (day 9)"

        # The control on the control: the active sections DO convert, so the
        # same planted refusal must reach them.  Without this half, deleting
        # ``PayCalendar.cadence`` outright would pass the assertion above.
        tmpl.is_active = True
        db.session.flush()
        with pytest.raises(PayCalendarError, match="the cadence was read"):
            recurring_view.build_view([], [tmpl], [], ctx)


#: The price the two cases below hand a prepared row: the $10.00 their
#: template states, as ``todays_price`` would answer it.  Neither case reads
#: it; the row requires one (plan step X-bp).
_PRICE_TEN = TodaysPrice(amount=Decimal("10.00"), paycheck_cadence=None)


class TestTheValuesCannotDisagree:
    """The pairs are checked, not merely documented."""

    def test_a_prepared_row_refuses_a_rule_without_its_reading(
        self, seed_user, seed_periods_today,
    ):
        """A docstring guarantee the generated ``__init__`` does not enforce
        is what ``OccurrencePlacement.__post_init__`` exists to stop repeating.
        """
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("10.00"))

        with pytest.raises(ValueError, match="rule and its reading"):
            recurring_view._PreparedRow(
                template=tmpl, price=_PRICE_TEN, monthly_full=None,
                rule=tmpl.recurrence_rule, reading=None,
            )

    def test_a_prepared_row_refuses_a_reading_without_its_rule(
        self, seed_user, seed_periods_today,
    ):
        """The other direction, so the check is a biconditional not a guard."""
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("10.00"))
        reading = read_rule(
            tmpl.recurrence_rule, _calendar(seed_periods_today),
        )

        with pytest.raises(ValueError, match="rule and its reading"):
            recurring_view._PreparedRow(
                template=tmpl, price=_PRICE_TEN, monthly_full=None, rule=None,
                reading=reading,
            )
