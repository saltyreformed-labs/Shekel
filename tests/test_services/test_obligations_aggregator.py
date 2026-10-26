"""
Shekel Budget App -- obligations_aggregator service tests (E-24, HIGH-05).

Locks the single canonical monthly-equivalent aggregator behind both
the Recurring surface and the ``/savings`` emergency-fund baseline +
per-goal contribution floors. Before this aggregator, four near-
identical loops applied the filter (skip ONCE / skip expired / skip
no-rule / skip missing-or-zero amount); only the three
``/obligations`` loops applied the expired-rule guard, so an expired
recurring expense inflated the EF baseline forever (HIGH-05 / D6-05).

Every test below sets up real ORM templates against the test DB so
the relationship-driven attribute access in
``template_monthly_or_none`` is exercised end-to-end, and computes
its expected value by hand from the named factors -- no test
inlines a literal 26/12 for the expectation, so a regression of
the factor would surface here.

**The aggregator takes the READ PASS since plan step R7d-e**, where it took
the pass's day and calendar as two arguments: its expired filter reads what
stops a definition through the composed door -- the rule's own bound AND its
destination's derived stop -- and the door needs the pass to fold a loan.  The
cases that state a synthetic schedule build the pass over it with
:func:`~tests._test_helpers.read_pass_over_paydays` (the one seam the
developer ruled for that, plan finding **P54**), so no hand-computed figure
moved; the cases that need a loan behind the definition build the pass the
route does.  The step's own money is ``test_obligations_derived_stop``: a
retired loan's payment leaves both totals on the day the loan closed, where it
used to leave on the day a chokepoint last rewrote a cached column.

**One of those factors stopped being a constant at plan step R7a-2a.**
``PAY_PERIODS_PER_YEAR`` was a module-level ``Decimal("26")`` while
``budget.pay_schedule.cadence_days`` is user-selectable 1..365, so this
"single canonical aggregator" produced a figure that was simply wrong for an
owner not paid biweekly.  Every case here now states the cadence it was
hand-computed at (:data:`_BIWEEKLY`) and no assertion moved.
``test_the_conversion_side_paycheck_count_has_one_producer`` is where a second
cadence is exercised end to end, and it is the regression guard that replaced
the constant's own.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum, TxnTypeEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import obligations_aggregator, recurring_view
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCadence, calendar_for
from app.services.pay_rhythm import FixedDays
from app.services.recurrence import RecurrenceResolutionError
from app.utils.money import MONTHS_PER_YEAR
from tests._test_helpers import make_cadence_rule, read_pass_over_paydays
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    MONTHLY,
)

#: 14 days between paydays, 26 a year -- the cadence every hand-computed
#: figure in this file assumes, and the one the retired
#: ``PAY_PERIODS_PER_YEAR`` constant assumed for every owner (R7a-2a).
_BIWEEKLY = PayCadence(FixedDays(14))

#: A schedule at that cadence, carried by the READ PASS the aggregator takes
#: since plan step R7d-e (the calendar itself since R7b-3) -- the CADENCE for
#: the conversion, and the paydays for the filter step that has to know when a
#: bounded rule spent its bound.
#:
#: Three years of paydays so a count bound has somewhere to be spent.  The
#: schedule has no owner of its own: each case names the owner its pass is
#: built for (:func:`_pass`), because the filter resolves the rule against this
#: schedule and ``resolve`` refuses a rule paired with another owner's.  Every
#: figure below is hand-computed against ``_BIWEEKLY``, which is ``.cadence``
#: of this schedule, so no assertion moves.
_SCHEDULE_START = date(2025, 1, 3)
_SCHEDULE_PAYDAYS = 78

#: The owner of every duck-typed template :func:`_fake_template` builds, and so
#: of the pass each case that reads one builds.  No user row has this id (every
#: user the test database holds is numbered above a million, ruling
#: ``balance:R-BAL356``), which is fine: nothing here reads the owner from the
#: database, only compares the rule's owner with the pass's.
_STAND_IN_OWNER = 1


def _pass(
    as_of: date, cadence_days: int = 14, *, user_id: int,
) -> BalanceContext:
    """Return a read pass over a three-year synthetic schedule, pinned at *as_of*.

    Built through the one seam the developer ruled for a pass over
    hand-written paydays (plan finding **P54**), so it is a real frozen
    :class:`~app.services.balance_at.BalanceContext` whose calendar memo is
    this schedule and whose scenario is ``None`` -- which is enough for every
    definition here that pays into no loan, because the composed door answers
    "no derived stop" for those before it ever needs a scenario.

    Args:
        as_of: The day the pass is pinned to -- the day the expired filter
            asks about.
        cadence_days: Days between paydays.  14 unless a case is about the
            cadence itself.
        user_id: The owner, with no default so every case states it: the
            seed user's id for a real template, :data:`_STAND_IN_OWNER` for a
            duck-typed one.  The rule's ``user_id`` must match it, or
            ``resolve`` refuses the pairing.

    Returns:
        The :class:`~app.services.balance_at.BalanceContext`.
    """
    return read_pass_over_paydays(
        paydays=[
            _SCHEDULE_START + timedelta(days=cadence_days * index)
            for index in range(_SCHEDULE_PAYDAYS)
        ],
        cadence_days=cadence_days,
        as_of=as_of,
        user_id=user_id,
    )


def _fake_template(
    unit_id: int, interval_n: int, amount: Decimal | None, *,
    end_date: date | None = None,
) -> SimpleNamespace:
    """Return a duck-typed template whose rule the composed door can READ.

    The stand-in this module's conversion cases build in place of an ORM row.
    It carries every column :func:`~app.services.recurrence.recurrence_spec`
    reads -- not the four the retired filter happened to touch -- because the
    aggregator resolves the rule through the door since plan step R7d-e, and
    a stand-in that lists only what one reader touches stops being one the
    moment the reader changes.  ``user_id`` is :data:`_STAND_IN_OWNER`, which
    the case's pass must name too; ``starts_on`` is the schedule's first payday
    and the placement is the containing one, so the value resolves for every
    authorable unit.

    Args:
        unit_id: The ``ref.recurrence_units`` id -- possibly one the enum does
            not model, which is the case that must REFUSE.
        interval_n: The cadence interval.
        amount: The template's ``default_amount``.
        end_date: An authored closing date, or ``None``.

    Returns:
        The :class:`types.SimpleNamespace` template.
    """
    return SimpleNamespace(
        recurrence_rule=SimpleNamespace(
            user_id=_STAND_IN_OWNER,
            unit_id=unit_id,
            interval_n=interval_n,
            starts_on=_SCHEDULE_START,
            nominal_day=None,
            placement_id=ref_cache.period_placement_id(
                PeriodPlacementEnum.CONTAINING_DATE,
            ),
            end_date=end_date,
            max_occurrences=None,
            max_per_month=None,
        ),
        default_amount=amount,
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _create_expense(seed_user, cadence, amount, *, name="Expense",
                    interval_n=1, end_date=None):
    """Create and flush an expense TransactionTemplate with its cadence.

    ``cadence`` may be ``None`` -- that is how a definition says "does not
    repeat" since plan step R2e-3.

    **It authors the cadence itself, since plan step R-F6**: a rule carries
    its owning template's FK, so it cannot be built before the template and a
    separate ``_create_rule`` helper had nothing to hand back.  A caller that
    needs the rule reads ``tmpl.recurrence_rule``.
    """
    tmpl = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=amount,
        is_active=True,
    )
    db.session.add(tmpl)
    db.session.flush()
    if cadence is not None:
        make_cadence_rule(
            tmpl, cadence, interval_n=interval_n, end_date=end_date,
        )
    return tmpl


# ── Tests ────────────────────────────────────────────────────────────


class TestObligationsAggregator:
    """End-to-end behavior of obligations_aggregator.committed_monthly."""

    def test_expired_template_excluded(self, app, seed_user):
        """C23-1 (HIGH-05 / D6-05): a recurring template whose rule's
        end_date is strictly before ``as_of`` contributes zero.

        Pre-Commit-23 ``compute_committed_monthly`` lacked this guard
        and a $100 biweekly expired template inflated the EF baseline
        and every per-goal floor by:
            $100 * 26 / 12 = $216.67 / month forever.
        After Commit 23 the aggregator returns Decimal("0.00") for the
        same setup. Arithmetic: filter is per-template, expired -> None,
        the only template contributes nothing, sum -> 0.00.
        """
        as_of = date(2026, 5, 20)
        expired_end = as_of - timedelta(days=1)
        with app.app_context():
            tmpl = _create_expense(
                seed_user, EVERY_PERIOD, Decimal("100.00"),
                name="Expired Biweekly", end_date=expired_end,
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [tmpl], _pass(as_of, user_id=seed_user["user"].id),
            )

            assert result == Decimal("0.00"), (
                f"Expired template must not contribute (HIGH-05). "
                f"Pre-fix value was {Decimal('100') * _BIWEEKLY.periods_per_year / MONTHS_PER_YEAR}; "
                f"got {result}."
            )

    def test_active_template_included(self, app, seed_user):
        """C23-2: an active recurring template with no end_date (or an
        end_date >= as_of) contributes its full monthly equivalent.

        Arithmetic: $100 biweekly * 26 / 12 = $216.6666...; the
        aggregator quantizes at the boundary to $216.67.
        """
        as_of = date(2026, 5, 20)
        with app.app_context():
            tmpl = _create_expense(
                seed_user, EVERY_PERIOD, Decimal("100.00"),
                name="Active Biweekly",
            )
            db.session.commit()

            expected = (
                Decimal("100") * _BIWEEKLY.periods_per_year / MONTHS_PER_YEAR
            ).quantize(Decimal("0.01"))
            result = obligations_aggregator.committed_monthly(
                [tmpl], _pass(as_of, user_id=seed_user["user"].id),
            )

            assert result == Decimal("216.67"), (
                f"Active biweekly $100 -> $100 * 26/12 = {expected}; got {result}"
            )

    def test_non_repeating_template_contributes_zero(self, app, seed_user):
        """C23-3: a template that does not repeat contributes zero.

        A one-time commitment is not a recurring monthly obligation.  Setup:
        one rule-less template for $5,000 plus one EVERY_PERIOD template for
        $100.  Expected total = $100 * 26 / 12 = $216.67 (the rule-less entry
        returns ``None`` from ``template_monthly_or_none`` before there is a
        pattern to convert).  If it were counted the total would be $5,000 +
        $216.67 = $5,216.67 -- this assertion proves it is not.

        Named for the ``Once`` PATTERN until plan step R2e-3 retired it;
        rule-less is now the only spelling of "does not repeat".
        """
        as_of = date(2026, 5, 20)
        with app.app_context():
            once_tmpl = _create_expense(
                seed_user, None, Decimal("5000.00"),
                name="One-Time",
            )
            recurring_tmpl = _create_expense(
                seed_user, EVERY_PERIOD, Decimal("100.00"),
                name="Recurring",
            )
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [once_tmpl, recurring_tmpl],
                _pass(as_of, user_id=seed_user["user"].id),
            )

            # Hand-computed: ONCE excluded -> $100 * 26 / 12 = $216.67.
            assert result == Decimal("216.67"), (
                f"ONCE must not contribute; recurring -> $216.67; got {result}"
            )

    def test_recurring_surface_and_savings_agree(
        self, app, seed_user, auth_client,
    ):
        """C23-4: the unified Recurring surface's expense subtotal and
        savings_dashboard's EF committed_monthly baseline -- both route
        through obligations_aggregator -- agree on the same dollar number
        for the same templates.

        The /obligations page retired (Loop B); its monthly-equivalent
        kernel moved to the unified /templates surface, reached here by
        following the /obligations redirect.

        Setup: two expenses on checking ($100 biweekly + $500 monthly).
        Hand-computed monthly equivalents:
            $100 * 26 / 12 = $216.67
            $500           = $500.00
            total expense  = $716.67
        The rendered subtotal and the EF baseline both call the canonical
        aggregator, so they cannot diverge.
        """
        with app.app_context():
            _create_expense(
                seed_user, EVERY_PERIOD, Decimal("100.00"),
                name="Biweekly Bill",
            )
            _create_expense(
                seed_user, MONTHLY, Decimal("500.00"),
                name="Monthly Bill",
            )
            db.session.commit()

            # Expense subtotal read from the rendered unified Recurring
            # surface (via the /obligations redirect) so we test the actual
            # route, not just the function.
            resp = auth_client.get("/obligations", follow_redirects=True)
            assert resp.status_code == 200
            assert "$716.67" in resp.data.decode(), (
                "unified Recurring surface expense subtotal must show $716.67"
            )

            # Same templates fed through the aggregator directly --
            # this is the function the EF baseline calls.
            templates = (
                db.session.query(TransactionTemplate)
                .filter_by(
                    user_id=seed_user["user"].id,
                    is_active=True,
                )
                .all()
            )
            agg_total = obligations_aggregator.committed_monthly(
                templates, _pass(date.today(), user_id=seed_user["user"].id),
            )
            assert agg_total == Decimal("716.67"), (
                f"Aggregator total must equal /obligations subtotal "
                f"($716.67); got {agg_total}"
            )

    def test_an_unmodelled_pattern_is_refused_not_silently_dropped(self, app):
        """A rule the app cannot read stops the total instead of shrinking it.

        Plan step R7a-2b, ruled 2026-08-11.  The filter used to end "skip if
        the conversion returns ``None``", so a rule naming a pattern the enum
        does not name was left OUT of every total this module feeds -- while
        the Recurring surface 500'd on the same row, because ``read_rule``
        resolves and raises.  One row, counted on one page and not the other,
        and the quiet side was the one feeding the emergency-fund baseline.

        The surplus id is computed from the enum rather than written down: a
        literal would stop naming an unmodelled unit the day a member is added.

        **The unreadable COLUMN moved at plan step R7c-c.**  This planted a
        ``pattern_id`` the closed pattern set's enum did not name; that
        column is dropped, and the state a rule can still reach is a
        ``unit_id`` the enums do not model.  Same broken invariant, same
        disposition, on the column that replaced it.
        """
        surplus = max(
            ref_cache.recurrence_unit_id(member)
            for member in RecurrenceUnitEnum
        ) + 1
        with app.app_context():
            template = _fake_template(surplus, 1, Decimal("100.00"))
            with pytest.raises(RecurrenceResolutionError):
                obligations_aggregator.committed_monthly(
                    [template], _pass(date(2026, 5, 20), user_id=_STAND_IN_OWNER),
                )

    def test_the_three_surviving_filters_still_answer_none(self, app):
        """The control on the case above: only the fourth rule went.

        No rule, expired, and zero amount are all still ``None`` -- they are
        statements about a definition that is not a recurring commitment, not
        about one the app cannot read.  Without this, "raise whenever the
        conversion cannot run" would pass the test above while turning three
        legitimate skips into 500s.
        """
        as_of = date(2026, 5, 20)
        every_period = ref_cache.recurrence_unit_id(
            RecurrenceUnitEnum.PERIOD,
        )
        with app.app_context():
            no_rule = SimpleNamespace(
                recurrence_rule=None, default_amount=Decimal("100.00"),
            )
            expired = _fake_template(
                every_period, 1, Decimal("100.00"),
                end_date=as_of - timedelta(days=1),
            )
            zero = _fake_template(every_period, 1, Decimal("0.00"))
            for template in (no_rule, expired, zero):
                assert obligations_aggregator.template_monthly_or_none(
                    template, _pass(as_of, user_id=_STAND_IN_OWNER),
                ) is None

    def test_the_conversion_reproduces_every_retired_branch(self, app):
        """One expression, seven hand-computed answers.

        ``amount_to_monthly`` was a seven-branch switch; this is
        ``amount * units_per_year / (interval_n * 12)``.  Each figure below is
        hand-computed at the biweekly cadence from the branch it replaced, so
        the derivation has to reproduce all seven before it is trusted to
        answer for the cadences plan step R8 adds.
        """
        cases = [
            (RecurrenceUnitEnum.PERIOD, 1, "216.67"),   # 100*26/12
            (RecurrenceUnitEnum.PERIOD, 2, "108.33"),   # 100*26/2/12
            (RecurrenceUnitEnum.MONTH, 1, "100.00"),    # unchanged
            (RecurrenceUnitEnum.MONTH, 3, "33.33"),     # 100/3
            (RecurrenceUnitEnum.MONTH, 6, "16.67"),     # 100/6
            (RecurrenceUnitEnum.YEAR, 1, "8.33"),       # 100/12
            # The two the closed pattern set could NOT name, which plan step
            # R7c-c makes storable: every other month is six a year, and every
            # other year is half of one.
            (RecurrenceUnitEnum.MONTH, 2, "50.00"),     # 100*12/2/12
            (RecurrenceUnitEnum.YEAR, 2, "4.17"),       # 100/24
        ]
        with app.app_context():
            for unit, interval_n, expected in cases:
                template = _fake_template(
                    ref_cache.recurrence_unit_id(unit), interval_n,
                    Decimal("100.00"),
                )
                assert obligations_aggregator.committed_monthly(
                    [template], _pass(date(2026, 5, 20), user_id=_STAND_IN_OWNER),
                ) == Decimal(expected), (unit, interval_n)

    def test_the_conversion_side_paycheck_count_has_one_producer(self):
        """C23-5, as plan step R7a-2a restated it: ONE producer, no constant.

        This test's SUBJECT moved and its purpose did not.  It used to assert
        that the biweekly-to-monthly factor was a constant defined in exactly
        one module (``app.utils.money.PAY_PERIODS_PER_YEAR``); the defect that
        step fixed is that "how many paychecks a year" is not a constant at
        all -- ``budget.pay_schedule.cadence_days`` is user-selectable 1..365.
        So the invariant is now "one DERIVATION, reachable through both doors,
        and no module-level number to drift from it", and this is where a
        reintroduced constant is caught.

        **Named for the CONVERSION side, and since plan step R-F16 that is
        the whole story.**  ``salary.salary_profiles.pay_periods_per_year`` was
        a second, stored, user-selected paycheck count on the PRODUCTION side
        -- the divisor the paycheck engine used -- with nothing tying the two
        together.  That column is dropped and both sides now derive from
        ``budget.pay_schedule.cadence_days``.  The developer ruled that second
        producer's removal into this arc on 2026-08-11, as the leaf after
        R7a-2a.  A name claiming "one producer" flat would be the false claim
        this file exists to catch.

        ``MONTHS_PER_YEAR`` stays a constant and stays here, because 12 is a
        property of the calendar rather than of an owner.
        """
        from app.services import savings_goal_service
        from app.services.pay_calendar import PayCadence as _Cadence
        from app.utils import money

        # 1. The month denominator is still one named constant in one module.
        assert MONTHS_PER_YEAR == Decimal("12")
        assert money.MONTHS_PER_YEAR is MONTHS_PER_YEAR

        # 2. The paycheck count is GONE as a constant -- from the module that
        #    held it and from the module that held it privately before that.
        assert not hasattr(money, "PAY_PERIODS_PER_YEAR"), (
            "app.utils.money must not re-declare a paychecks-per-year "
            "constant: the count is a function of the owner's cadence "
            "(plan step R7a-2a)."
        )
        assert not hasattr(savings_goal_service, "_PAY_PERIODS_PER_YEAR")
        assert not hasattr(savings_goal_service, "_MONTHS_PER_YEAR")

        # 3. The one derivation, at the three cadences a real schedule uses.
        #    round(365.2425 / days): 14 -> 26, 7 -> 52, 30 -> 12.
        assert _Cadence(FixedDays(14)).periods_per_year == Decimal("26")
        assert _Cadence(FixedDays(7)).periods_per_year == Decimal("52")
        assert _Cadence(FixedDays(30)).periods_per_year == Decimal("12")

        # 4. The aggregator reads THAT value and nothing else: a cadence the
        #    old constant would have called 26 answers 52 here.  A duck-typed
        #    template rather than an ORM row -- the exception to this file's
        #    "real ORM templates" rule, taken because the subject is the
        #    CADENCE and an ORM round-trip would add a second variable to a
        #    test whose whole point is that only one thing changed.
        #    $100 every paycheck weekly = 100 * 52 / 12 = $433.33.
        weekly_template = _fake_template(
            ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD), 1,
            Decimal("100.00"),
        )
        weekly = _pass(
            date(2026, 5, 20), cadence_days=7, user_id=_STAND_IN_OWNER,
        )
        assert weekly.calendar().cadence == _Cadence(FixedDays(7))
        assert obligations_aggregator.committed_monthly(
            [weekly_template], weekly,
        ) == Decimal("433.33")
        assert obligations_aggregator.committed_monthly(
            [weekly_template], _pass(date(2026, 5, 20), user_id=_STAND_IN_OWNER),
        ) == Decimal("216.67")

    def test_emergency_fund_baseline_excludes_expired(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C23-6 (HIGH-05 / D6-05): on /savings the emergency-fund
        committed_monthly baseline excludes an expired recurring
        template.

        Setup: one EVERY_PERIOD expense template for $1,500 whose
        rule.end_date is strictly before today (1 day ago).

        Pre-Commit-23 the EF baseline would compute:
            $1,500 * 26 / 12 = $3,250 / month
        forever, even though the obligation has stopped recurring.
        After Commit 23 the aggregator drops the expired template and
        the displayed baseline goes to $0 -- no "/mo" string for the
        $3,250 inflated value should appear.
        """
        with app.app_context():
            _create_expense(
                seed_user, EVERY_PERIOD, Decimal("1500.00"),
                name="Expired Bill",
                end_date=date.today() - timedelta(days=1),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Hand-computed pre-fix inflated baseline -- the assertion
            # that protects against regression.
            inflated = (
                Decimal("1500") * _BIWEEKLY.periods_per_year / MONTHS_PER_YEAR
            ).quantize(Decimal("0.01"))
            assert inflated == Decimal("3250.00")
            assert "$3,250/mo" not in html, (
                "Expired template must not inflate EF baseline "
                "(HIGH-05 / D6-05 regression)."
            )


class TestASpentCountLeavesTheObligationsTotal:
    """Plan step R7b-3: the filter answers for BOTH shapes of closing bound.

    The shared filter's step 2 read ``rule.end_date < as_of`` and had no
    answer for a count at all.  Giving ``max_occurrences`` its first writer is
    what made that reachable: a $500/mo commitment set to "ends after 12"
    would have gone on inflating ``/obligations`` and the ``/savings``
    emergency-fund baseline forever -- while the SAME row's "Next" column,
    which walks occurrences, showed blank.  One row disagreeing with itself
    about whether a commitment is over is the HIGH-05 defect this module
    exists to have fixed, on the other bound.

    Every case here uses a real ORM rule against the test DB, because the
    count arm is the one that RESOLVES the rule against the owner's schedule.
    """

    def _template(self, seed_user, *, count):
        """Create a $100 every-paycheck expense bounded after *count* firings.

        Args:
            seed_user: The seeded owner fixture.
            count: The rule's ``max_occurrences``.

        Returns:
            The flushed template.
        """
        tmpl = _create_expense(
            seed_user, EVERY_PERIOD, Decimal("100.00"), name=f"After {count}",
        )
        # Reached through the OWNING template (plan step R-F6): the rule
        # cannot be built before it, so a count bound is set on the row the
        # template now holds.
        tmpl.recurrence_rule.max_occurrences = count
        db.session.flush()
        return tmpl

    def test_a_spent_count_contributes_nothing(
        self, app, seed_user, seed_periods,
    ):
        """Every occurrence fell before today, so the commitment is over.

        ``seed_periods`` opens well before today, so a one-occurrence rule
        fired on the first payday and never again.
        """
        with app.app_context():
            template = self._template(seed_user, count=1)
            db.session.commit()

            assert obligations_aggregator.template_monthly_or_none(
                template, BalanceContext.build(seed_user["user"].id),
            ) is None

    def test_a_count_with_occurrences_left_still_counts(
        self, app, seed_user, seed_periods,
    ):
        """The control, and the direction that matters most.

        Dropping a LIVE commitment out of the emergency-fund baseline
        understates what the owner is committed to, which is the more
        dangerous error of the two.  $100 every paycheck at 26 a year is
        100 * 26 / 12 = $216.67 a month.
        """
        with app.app_context():
            template = self._template(seed_user, count=10_000)
            db.session.commit()

            assert obligations_aggregator.template_monthly_or_none(
                template, BalanceContext.build(seed_user["user"].id),
            ) == Decimal("100") * Decimal("26") / MONTHS_PER_YEAR

    def test_a_schedule_that_has_not_reached_the_count_leaves_it_live(
        self, app, seed_user, seed_periods,
    ):
        """An un-extended pay schedule is not a finished commitment.

        The count's exhaustion depends on when the paychecks fall, so a
        schedule the owner has not extended yields fewer occurrences than the
        bound names -- and answering "ended" there would silently remove a
        real commitment from two money totals.  Asked as of a day BEFORE the
        schedule opens, so the walk yields nothing at all.
        """
        with app.app_context():
            template = self._template(seed_user, count=2)
            db.session.commit()
            calendar = calendar_for(seed_user["user"].id)
            before_opening = calendar.opening_bound() - timedelta(days=1)

            assert obligations_aggregator.template_monthly_or_none(
                template,
                BalanceContext.build(seed_user["user"].id, before_opening),
            ) is not None

    def test_the_count_shape_is_live_on_the_day_its_last_occurrence_falls(
        self, app, seed_user, seed_periods,
    ):
        """The off-by-one the walk's INCLUSIVE window makes easy to get wrong.

        A closing bound is asked about occurrences STRICTLY BEFORE the day,
        while ``occurrences(through=)`` is inclusive -- so passing the day
        itself would count an occurrence falling ON it and report a commitment
        finished while its last payment is still due today.  That is the same
        boundary the date shape holds (``end_date < as_of``, never ``<=``), and
        the two must agree or two equivalent rules leave the total on different
        days.
        """
        with app.app_context():
            template = self._template(seed_user, count=2)
            db.session.commit()
            owner = seed_user["user"].id
            second_payday = calendar_for(owner).periods[1].start_date

            # ON the day the second (and last) occurrence falls: still live.
            assert obligations_aggregator.template_monthly_or_none(
                template, BalanceContext.build(owner, second_payday),
            ) is not None
            # The day after: spent.
            assert obligations_aggregator.template_monthly_or_none(
                template,
                BalanceContext.build(owner, second_payday + timedelta(days=1)),
            ) is None

    def test_the_date_shape_answers_from_OCCURRENCES_not_from_the_date(
        self, app, seed_user, seed_periods,
    ):
        """The ruled change (developer 2026-08-13, plan ledger row **D33**).

        A date bound used to keep a commitment counted until the bound date
        passed, even where the rule had already fired for the last time.  It
        answers the same question the count shape does now -- does the rule
        still OWE an occurrence -- so two ways of writing one schedule cannot
        leave the total on different days.

        Bounded on a payday, so the rule owes that occurrence ON the bound and
        nothing after it: live that day, finished the next.  A bound set
        BETWEEN paydays is the case that moved -- see the sibling below.
        """
        with app.app_context():
            owner = seed_user["user"].id
            payday = calendar_for(owner).periods[1].start_date
            template = _create_expense(
                seed_user, EVERY_PERIOD, Decimal("100.00"),
                name="Ends On A Payday", end_date=payday,
            )
            db.session.commit()

            assert obligations_aggregator.template_monthly_or_none(
                template, BalanceContext.build(owner, payday),
            ) is not None
            assert obligations_aggregator.template_monthly_or_none(
                template, BalanceContext.build(owner, payday + timedelta(days=1)),
            ) is None

    def test_a_bound_between_occurrences_stops_at_the_LAST_one(
        self, app, seed_user, seed_periods,
    ):
        """What ruling D33 moved, stated as the case that moves.

        A rule bounded the day BEFORE its next payday owes nothing from the
        day after its last one -- so it leaves the total then, rather than
        lingering until the bound date the old reading waited for.  On a
        biweekly schedule that is up to 13 days; on a yearly bill bounded at
        year end it was eleven months.
        """
        with app.app_context():
            owner = seed_user["user"].id
            calendar = calendar_for(owner)
            last_fired = calendar.periods[1].start_date
            next_payday = calendar.periods[2].start_date
            template = _create_expense(
                seed_user, EVERY_PERIOD, Decimal("100.00"),
                name="Ends Mid-Cycle",
                end_date=next_payday - timedelta(days=1),
            )
            db.session.commit()

            assert obligations_aggregator.template_monthly_or_none(
                template, BalanceContext.build(owner, last_fired),
            ) is not None
            assert obligations_aggregator.template_monthly_or_none(
                template,
                BalanceContext.build(owner, last_fired + timedelta(days=1)),
            ) is None

    def test_the_recurring_surface_agrees_with_itself(
        self, app, seed_user, seed_periods,
    ):
        """A row's monthly figure and its "Next" date state the same thing.

        The defect this closes, seen from the surface: the Next column walks
        occurrences and so always honoured the count, while the monthly
        equivalent read only the date.  A spent count made one row say both
        "nothing further" and "$216.67 a month, indefinitely".
        """
        with app.app_context():
            template = self._template(seed_user, count=1)
            db.session.commit()

            view = recurring_view.build_view(
                [], [template], [],
                BalanceContext.build(seed_user["user"].id, date.today()),
            )
            row = view.expenses.rows[0]

            assert row.next_date is None
            assert row.equivalent.monthly is None


class TestACeilingedRulePricesAtItsCeiling:
    """A per-month ceiling reaches the monthly equivalent (plan step salary:R15-a).

    The developer's eleven "at most 2 a month" payroll lines come to $517.21 a
    paycheck; as a recurring template that cadence costs exactly $1,034.42 a
    month, and pricing it off the ``(interval, unit)`` pair alone -- 26/12 of
    the amount -- would overstate it by $86.20 every month.
    """

    def test_every_paycheck_at_most_twice_a_month_is_amount_times_two(
        self, app, seed_user,
    ):
        """$517.21 at most 2 a month -> $1,034.42, an exact multiply."""
        as_of = date(2026, 5, 20)
        with app.app_context():
            # No cadence at creation: the ceilinged rule is authored below,
            # and a template holds exactly one rule.
            tmpl = _create_expense(
                seed_user, None, Decimal("517.21"), name="Benefit Premiums",
            )
            make_cadence_rule(tmpl, EVERY_PERIOD, max_per_month=2)
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [tmpl], _pass(as_of, user_id=seed_user["user"].id),
            )

            # 517.21 x 2 = 1034.42; the uncapped reading would be
            # 517.21 x 26 / 12 = 1120.62.
            assert result == Decimal("1034.42")

    def test_a_ceiling_the_cadence_never_reaches_prices_as_the_pair(
        self, app, seed_user,
    ):
        """Every paycheck at most 3 a month on a biweekly owner is still 26/12."""
        as_of = date(2026, 5, 20)
        with app.app_context():
            tmpl = _create_expense(
                seed_user, None, Decimal("100.00"), name="Loose Ceiling",
            )
            make_cadence_rule(tmpl, EVERY_PERIOD, max_per_month=3)
            db.session.commit()

            result = obligations_aggregator.committed_monthly(
                [tmpl], _pass(as_of, user_id=seed_user["user"].id),
            )

            # 12 x 3 = 36 is not below 26, so 100 x 26 / 12 = 216.67.
            assert result == Decimal("216.67")
