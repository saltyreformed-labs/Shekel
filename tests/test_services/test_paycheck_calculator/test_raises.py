"""Raises: a recurring raise compounds across years, same-date raises apply flat before percentage,
a raise stops where its own belief stops, applications land in date order, and every engine read
of a raise goes through the basis's raise set.
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app.models.salary_raise import SalaryRaise
from app.services.paycheck_calculator import calculate_paycheck
from app.services.paycheck_calculator._calendar_questions import _get_cumulative_wages
from app.services.paycheck_calculator._lines import _LineContext, _priced_lines
from app.utils.money import ZERO
from app import ref_cache
from app.enums import RaiseTypeEnum
from app.services.salary_raises import RaiseTerms, terms_of
from tests._test_helpers import payroll_basis

from ._helpers import (
    _timing_id,
    FakeRaise,
    _period,
    FakeDeduction,
    FakeProfile,
    _WALK_OPENS,
    _walked_pay,
)


# ── Existing Tests: Recurring Raise Compounding ──────────────────


class TestRecurringRaiseCompounding:
    """Verify that recurring raises compound correctly across years.

    Re-stated on the pay list's walk at plan step salary:X-av-3a (the
    developer's rule-5 ruling "Re-state each on the walk"): the same cases,
    asserted per paycheck.  $100,000.00 a year was $3,846.15 a paycheck
    (100000 / 26 = 3846.1538... -> 3846.15), priced from 2025-12-19
    (:data:`_WALK_OPENS`), and each 3% step rounds to the cent (R-SAL60):
    3846.15 x 1.03 = 3961.5345 -> 3961.53; x 1.03 = 4080.3759 -> 4080.38;
    x 1.03 = 4202.7914 -> 4202.79.
    """

    @staticmethod
    def _march_merit():
        """A 3% raise recurring every March from 2026, on $3,846.15 a paycheck."""
        return FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )

    def test_recurring_raise_not_yet_effective(self):
        """Before effective month in effective year, raise should not apply."""
        # 2026-02-13, February's first payday: the March raise has not landed.
        assert _walked_pay(self._march_merit(), date(2026, 2, 13)) == Decimal("3846.15")

    def test_recurring_raise_first_year_at_effective_month(self):
        """In effective year at effective month, raise should apply once."""
        # 2026-03-13, March's first payday: 3846.15 x 1.03 = 3961.5345 -> 3961.53
        assert _walked_pay(self._march_merit(), date(2026, 3, 13)) == Decimal("3961.53")

    def test_recurring_raise_first_year_after_effective_month(self):
        """Later in effective year, raise should still apply once."""
        # 2026-06-05: still the one application, 3961.53
        assert _walked_pay(self._march_merit(), date(2026, 6, 5)) == Decimal("3961.53")

    def test_recurring_raise_second_year_before_month(self):
        """Next year before effective month: still only 1 application."""
        # 2027-01-01: 2027's March not reached, so still 3961.53
        assert _walked_pay(self._march_merit(), date(2027, 1, 1)) == Decimal("3961.53")

    def test_recurring_raise_second_year_after_month(self):
        """Next year after effective month: 2 total applications (compounded)."""
        # 2027-04-09: 3961.53 x 1.03 = 4080.3759 -> 4080.38
        assert _walked_pay(self._march_merit(), date(2027, 4, 9)) == Decimal("4080.38")

    def test_recurring_raise_third_year(self):
        """Two years later after effective month: 3 total applications.

        2028-06-02: 4080.38 x 1.03 = 4202.7914 -> 4202.79 -- one cent under
        the yearly walk's 109,272.70 / 26 = 4202.80, which is the per-raise
        rounding ruling R-SAL60 accepts.
        """
        assert _walked_pay(self._march_merit(), date(2028, 6, 2)) == Decimal("4202.79")

    def test_one_time_raise_applies_once(self):
        """A non-recurring raise should only apply once."""
        profile = FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=[FakeRaise(percentage="0.05", effective_month=1,
                              effective_year=2026, is_recurring=False)],
        )
        # Check in 2027 (2027-06-04) -- still just one application:
        # 3846.15 x 1.05 = 4038.4575 -> 4038.46.
        assert _walked_pay(profile, date(2027, 6, 4)) == Decimal("4038.46")

    def test_recurring_flat_raise(self):
        """Recurring flat raise should add the flat amount each year.

        $5,000 a year is 5000 / 26 = 192.3077 a paycheck (R-SAL65), each
        application rounded (R-SAL60): 3846.15 + 192.3077 = 4038.4577 ->
        4038.46; + 192.3077 = 4230.7677 -> 4230.77; + 192.3077 = 4423.0777 ->
        4423.08 -- the same 115,000 / 26 = 4423.08 the yearly walk priced.
        """
        profile = FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=[FakeRaise(flat_amount="5000", effective_month=1,
                              effective_year=2026, is_recurring=True)],
        )
        # 2028-06-02: 3 applications (2026, 2027, 2028).
        assert _walked_pay(profile, date(2028, 6, 2)) == Decimal("4423.08")

    def test_recurring_raise_second_year_at_effective_month(self):
        """March 2026 raise checked in March 2027 = 2 applications.

        Validates the `+1` in the application count formula:
          years_passed = 2027 - 2026 = 1
          period_month (3) >= eff_month (3) → applications = 1 + 1 = 2
        This is correct because the raise applied in March 2026 AND
        recurs again in March 2027.  On 2027-03-12, March's first payday:
        3846.15 x 1.03 = 3961.53; x 1.03 = 4080.3759 -> 4080.38.
        """
        assert _walked_pay(self._march_merit(), date(2027, 3, 12)) == Decimal("4080.38")


class TestRaiseOrdering:
    """Same-date raises apply flat-before-percentage, deterministically (M-01 / deep-hunt #12)."""

    def test_same_date_flat_applies_before_percentage(self):
        """A flat + percentage raise on one date apply flat-first, whatever the input order.

        Raise application is non-commutative, so the documented
        flat-before-percentage contract must hold regardless of the
        order the DB returns the rows in.  Per paycheck on the pay list's
        walk (plan step salary:X-av-3a), $100,000.00 a year being $3,846.15
        and $5,000 a year 5000 / 26 = 192.3077 a paycheck:
          flat-first:        (3846.15 + 192.3077) -> 4038.46; x 1.03 = 4159.6138 -> 4159.61
          percentage-first:   3846.15 x 1.03 -> 3961.53; + 192.3077 = 4153.8377 -> 4153.84
        The engine must always produce the flat-first value.
        """
        flat = FakeRaise(flat_amount="5000", effective_month=1, effective_year=2026)
        pct = FakeRaise(percentage="0.03", effective_month=1, effective_year=2026)
        as_of = date(2026, 6, 5)
        expected = Decimal("4159.61")  # (3846.15 + 5000 / 26) -> 4038.46; x 1.03

        # Both input orders must yield the flat-first result.  The
        # [pct, flat] order is the revert-proof case: without the method
        # tie-break the stable sort keeps percentage first -> 4153.84.
        for order in ([flat, pct], [pct, flat]):
            profile = FakeProfile(
                pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
                raises=order,
            )
            assert _walked_pay(profile, as_of) == expected


class TestRaiseTermination:
    """A raise stops accruing where its own belief stops.

    ``terminal_year`` is the last year a raise is believed to happen.  The
    pay list's walk (:func:`_walked_pay`; ``apply_raises`` until plan step
    salary:X-av-3a) honours it by not accruing applications past that year,
    and by never applying a one-time raise dated beyond it.  These cases
    grade the clamp itself; the mapping that DECIDES each raise's terminal
    year is graded in ``test_pension_calculator``.  Re-stated per paycheck at
    X-av-3a: $100,000.00 a year is $3,846.15 a paycheck, priced from
    2025-12-19, before the first raise lands on 2026-01-01.
    """

    @staticmethod
    def _pay_on(raise_row, payday):
        """What $3,846.15 a paycheck from 2025-12-19 pays on *payday* under *raise_row*."""
        profile = FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=[raise_row],
        )
        return _walked_pay(profile, payday)

    @staticmethod
    def _raise(percentage, *, month=1, year=2026, recurring=True,
               terminal_year=None):
        """A percentage :class:`SalaryRaise`, defaulting to recurring.

        An UNATTACHED row rather than a hand-rolled double: plan step
        salary:S3-c deleted ``TerminatedRaise``, the fabricated value the
        pension projector used to build, so a real row is the only shape
        the walk is ever handed now and the only one worth grading it
        against.  Every attribute the walk reads is set explicitly, because
        a client-side ``default`` fires at INSERT and this row never reaches
        a session -- ``raise_type_id`` included since plan step
        salary:X-av-3a, because the walk reads each row through
        ``RaiseTerms.of``, which reads ``raise_type_name`` off it.
        """
        return SalaryRaise(
            effective_year=year, effective_month=month,
            is_recurring=recurring, percentage=Decimal(percentage),
            flat_amount=None, terminal_year=terminal_year,
            raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
        )

    def test_the_orm_rows_end_year_defaults_to_nothing_so_paychecks_are_unmoved(self):
        """A new ``SalaryRaise`` starts at "believed indefinitely", so nothing clamps.

        **This replaces the absence tripwire plan step salary:S3-a left
        here, which fired as designed when plan step salary:S3-b added the
        column** (ruling **R-SAL11**, developer 2026-09-05: the horizon a
        recurring raise decays over is a fact on the raise).  That test
        asserted ``not hasattr(SalaryRaise, "terminal_year")`` and said in
        its own failure message that the premise would need re-deriving if
        the column arrived.  It has, so this is the re-derivation rather
        than a test edited to pass.

        **The premise moved from ABSENCE to a DEFAULT of nothing.**  The
        paycheck engine passes ``profile.raises`` -- real ORM rows -- and
        ``_applications`` reads ``raise_obj.terminal_year`` on every one of
        them.  What keeps a paycheck unmoved is that the field answers
        ``None`` for a raise nobody gave an end year.  A ``default`` on the
        column would end that silently and in the money-moving direction,
        which is what these assertions guard.

        *The read was ``getattr(raise_obj, "terminal_year", None)`` until
        plan step salary:S3-c, whose deletion of the pension projector's
        fabricated values left the row as the only shape passed.  A missing
        attribute now raises instead of defaulting, which is a tightening
        this class does not grade -- ``test_s3b_raise_terminal_year.py``
        does.*

        **It is NOT strictly stronger than the check it replaces, and an
        adversarial review of this step corrected a draft that said it
        was** -- along with the reason that draft gave, which was simply
        false: a defaulted column HAS the attribute, so the old
        ``not hasattr`` would have caught one.  What the old check bought
        was foreclosing the engine reading anything at all, and the ruling
        spent that deliberately.  What is left is narrower and is stated
        rather than claimed: given the column must now exist, this fails on
        a default the old check could not have distinguished from the
        column's mere existence.

        **The instance probe is the surface that governs**, and it is here
        because the deleted test argued for it: it used ``hasattr`` rather
        than ``__table__.columns`` precisely because a ``property`` or
        ``hybrid_property`` would satisfy the engine's attribute read while
        the column set would not.  ``SalaryRaise().terminal_year is None``
        is that argument kept.

        **A ``server_default`` is asserted against the DATABASE, not this
        model**, in ``tests/test_models/test_s3b_raise_terminal_year.py``
        -- a migration could add one without touching this class, and
        SQLAlchemy omits a valueless column from the INSERT, so the DB
        default would fire with a model-level assertion still green.  That
        is the F-068 / F-134 model-migration drift class
        ``test_c25_column_invariants`` exists for.  The arithmetic IDENTITY
        is measured there too, over a committed row.
        """
        column = SalaryRaise.__table__.columns["terminal_year"]
        assert column.nullable is True
        assert column.default is None, (
            "salary_raises.terminal_year gained a client-side default; "
            "every new raise now terminates and the paycheck engine "
            "clamps rows nobody set an end year on"
        )
        assert SalaryRaise().terminal_year is None, (
            "a fresh SalaryRaise answers something other than None for "
            "terminal_year, so the paycheck engine now clamps a "
            "raise nobody gave an end year -- check for a property, a "
            "hybrid_property or an __init__ default shadowing the column"
        )

    def test_a_raise_with_no_terminal_year_compounds_indefinitely(self):
        """3% recurring from 2026, asked at 2036, is 11 applications.

        Each rounded to the cent (R-SAL60): 3846.15 -> 3961.53 -> 4080.38 ->
        4202.79 -> 4328.87 -> 4458.74 -> 4592.50 -> 4730.28 (4730.275, half
        up) -> 4872.19 -> 5018.36 -> 5168.91 -> 5323.98 on 2036-12-05 -- the
        yearly walk's 138,423.39 / 26 = 5323.98 too.  This is the shape
        every in-window paycheck uses.
        """
        result = self._pay_on(self._raise("0.03"), date(2036, 12, 5))
        assert result == Decimal("5323.98")

    def test_a_terminated_recurring_raise_stops_accruing(self):
        """Terminating at 2030 caps the same raise at five applications.

        2026..2030 is 5, so 3846.15 -> 3961.53 -> 4080.38 -> 4202.79 ->
        4328.87 -> 4458.74 (the yearly walk's 115,927.41 / 26 = 4458.75 less
        R-SAL60's cent) -- and it stays there however far past 2030 the
        caller asks, because the raise stopped rather than slowed.
        """
        terminated = self._raise("0.03", terminal_year=2030)
        assert self._pay_on(terminated, date(2036, 12, 5)) == Decimal("4458.74")
        assert self._pay_on(terminated, date(2099, 12, 11)) == Decimal("4458.74")

    def test_a_one_time_raise_past_its_terminal_year_never_applies(self):
        """A 2035 one-time raise believed only through 2030 is dropped."""
        beyond = self._raise("0.10", year=2035, recurring=False,
                             terminal_year=2030)
        result = self._pay_on(beyond, date(2036, 12, 5))
        assert result == Decimal("3846.15")

    def test_a_one_time_raise_within_its_terminal_year_still_applies(self):
        """Its 2028 sibling is kept: 3846.15 * 1.10 = 4230.765 -> 4230.77.

        Without this, the case above would pass on a rule that dropped
        every one-time raise.
        """
        within = self._raise("0.10", year=2028, recurring=False,
                             terminal_year=2030)
        result = self._pay_on(within, date(2036, 12, 5))
        assert result == Decimal("4230.77")

    def test_a_raise_applies_through_the_END_of_its_terminal_year(self):
        """A July raise terminating in 2026 still gets its 2026 application.

        Termination is a whole-year fact, so a raise effective later in
        that year is still counted in it: ``3846.15 * 1.03 = 3961.5345 ->
        3961.53``.
        """
        july = self._raise("0.03", month=7, terminal_year=2026)
        result = self._pay_on(july, date(2036, 12, 5))
        assert result == Decimal("3961.53")

    def test_inside_the_terminal_year_the_callers_own_month_still_governs(self):
        """March 2026 has not reached a July raise, terminal year or not.

        The clamp is a ceiling on the asked date, never a floor: it must
        not back-date a raise into months before it exists.  Same raise as
        the case above, asked earlier, answers the untouched base.
        """
        july = self._raise("0.03", month=7, terminal_year=2026)
        result = self._pay_on(july, date(2026, 3, 13))
        assert result == Decimal("3846.15")


class TestChronologicalRaiseOrder:
    """Applications land in DATE order, not grouped by raise.

    Until this rule, ``apply_raises`` sorted the RAISES and then applied
    each one's whole run of yearly applications before starting the next.
    A percentage raise therefore multiplied flat dollars that had not
    arrived yet.  These cases grade the interleaving; the counts are
    unchanged, so an owner holding only percentage raises is unaffected,
    which the last case pins.

    Re-stated on the pay list's walk at plan step salary:X-av-3a
    (:func:`_walked_pay`, which reads
    :func:`~app.services.salary_raises.applications_between` for the same
    order): $100,000.00 a year is $3,846.15 a paycheck from 2025-12-19, a
    flat raise's yearly dollars spread over 26 paychecks (R-SAL65), each
    step rounded to the cent (R-SAL60).
    """

    @staticmethod
    def _profile(raises):
        """$3,846.15 a paycheck from 2025-12-19 under *raises*, in the order given."""
        return FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=raises,
        )

    def test_a_percentage_does_not_compound_flat_dollars_that_arrive_later(self):
        """A recurring flat COLA and a recurring percentage merit interleave yearly.

        $3,846.15 a paycheck; flat $1,500 a year (1500 / 26 = 57.6923 a
        paycheck) and 4%, both recurring from 2026-01.  Each year the flat
        lands, then the percentage multiplies what is there -- so the
        percentage never grows a dollar that arrives after it:
          2026: (3846.15 + 57.6923) -> 3903.84; x 1.04 = 4059.9936 -> 4059.99
          2027: (4059.99 + 57.6923) -> 4117.68; x 1.04 = 4282.3872 -> 4282.39
          2028: (4282.39 + 57.6923) -> 4340.08; x 1.04 = 4513.6832 -> 4513.68

        The old grouped order added all three flats to the base first and
        compounded once: 3846.15 -> 3903.84 -> 3961.53 -> 4019.22, then
        x 1.04 three times = 4521.08 at 2028, which credited three years of
        growth to dollars that had not arrived.
        """
        flat = FakeRaise(flat_amount="1500", effective_month=1,
                         effective_year=2026, is_recurring=True)
        pct = FakeRaise(percentage="0.04", effective_month=1,
                        effective_year=2026, is_recurring=True)
        profile = self._profile([flat, pct])
        assert _walked_pay(profile, date(2026, 12, 4)) == Decimal("4059.99")
        assert _walked_pay(profile, date(2027, 12, 3)) == Decimal("4282.39")
        assert _walked_pay(profile, date(2028, 12, 1)) == Decimal("4513.68")

    def test_input_order_still_does_not_change_the_answer(self):
        """Reversing the input list changes nothing (M-01 determinism preserved).

        The ordering key is the application's date and method, never the
        row order, so the DB may return these two in either order.  Same
        2028 value as the case above.
        """
        flat = FakeRaise(flat_amount="1500", effective_month=1,
                         effective_year=2026, is_recurring=True)
        pct = FakeRaise(percentage="0.04", effective_month=1,
                        effective_year=2026, is_recurring=True)
        profile = self._profile([pct, flat])
        assert _walked_pay(profile, date(2028, 12, 1)) == Decimal("4513.68")

    def test_a_one_time_raise_lands_on_its_own_date_not_last(self):
        """A mid-series one-time raise compounds only what preceded it.

        $3,846.15 a paycheck; a recurring flat $1,000 a year from 2026-01
        (1000 / 26 = 38.4615 a paycheck) and a ONE-TIME 10% on 2027-06.  In
        date order:
          2026-01 + 38.4615 -> 3884.61
          2027-01 + 38.4615 -> 3923.07
          2027-06 x 1.10    -> 4315.377 -> 4315.38
          2028-01 + 38.4615 -> 4353.84

        The old order applied the flat's three additions first and then
        the 10%, reaching 3961.53 x 1.10 = 4357.68 -- the extra $3.84 being
        10% of the 2028 addition, credited in 2027.
        """
        flat = FakeRaise(flat_amount="1000", effective_month=1,
                         effective_year=2026, is_recurring=True)
        once = FakeRaise(percentage="0.10", effective_month=6,
                         effective_year=2027, is_recurring=False)
        result = _walked_pay(self._profile([flat, once]), date(2028, 12, 1))
        assert result == Decimal("4353.84")

    def test_percentage_only_raises_are_completely_unaffected(self):
        """Every raise a percentage: the order leaves the COUNTS alone, and can move a cent.

        Multiplication commutes, so regrouping the applications changed
        nothing on the yearly walk -- which is why this rule moved no money
        for an owner whose raises are all percentages.  3% from 2026-01 and
        2% from 2026-07, at 2027-12: both have two applications.  **Since
        plan step salary:X-av-3a each step rounds to the cent (ruling
        R-SAL60), so the order can move a cent**: in date order 3846.15 x
        1.03 -> 3961.53; x 1.02 -> 4040.76; x 1.03 -> 4161.98; x 1.02 =
        4245.2196 -> 4245.22, where the grouped order (3%, 3%, 2%, 2%)
        rounds to 4245.23.  The COUNTS are what this rule leaves unaffected;
        the pin is the date order's figure.
        """
        merit = FakeRaise(percentage="0.03", effective_month=1,
                          effective_year=2026, is_recurring=True)
        cola = FakeRaise(percentage="0.02", effective_month=7,
                         effective_year=2026, is_recurring=True)
        result = _walked_pay(self._profile([merit, cola]), date(2027, 12, 3))
        assert result == Decimal("4245.22")


# ── salary:S3-f-1: the basis NAMES its raise set ────────────────────


class TestTheBasisNamesItsRaiseSet:
    """Every engine read of a raise goes through ``basis.raises``.

    Plan step **salary:S3-f-1** (ruling **R-SAL20**).  The engine read
    ``basis.profile.raises`` at three sites -- the paycheck's own gross, the
    FICA wage-base cumulative and a capped deduction's year-to-date -- and
    badged the raise event off the profile too, so the only raise set a
    paycheck could be priced under was the stored one.  A what-if over one
    raise's end year (plan step salary:S3-f) needs all four to read the SAME
    supplied set, or the verdict beside it is priced under two beliefs.

    **Each case is one read, and each states the figure a read that still
    went to the rows would produce**, so a site left on ``profile.raises``
    fails the case that covers it rather than hiding behind the three that
    happen to agree.  The fixture: ``$60,000`` a year, ``$2,307.69`` a
    paycheck since plan step salary:X-av-3a, one recurring 5% merit raise
    every March from 2027 believed FOREVER on the rows, and TERMS carrying the
    same raise believed through 2027 alone -- so a 2029 paycheck is priced
    off ``$2,671.43`` under the rows (2307.69 x 1.05 = 2423.0745 -> 2423.07;
    x 1.05 = 2544.2235 -> 2544.22; x 1.05 = 2671.431 -> 2671.43, each step
    rounded by ruling R-SAL60) and ``$2,423.07`` (one application) under the
    terms.

    **The pay entry is dated 2026-01-09**, a payday on this calendar's rhythm
    (2029-01-05 less 78 fortnights) before the raise's first landing on
    2027-03-01: an entry replaces every raise landing on or before its payday
    (ruling R-SAL59), so an entry on the calendar's opening 2029-01-05 would
    already hold the 2027 and 2028 applications and the rows and the terms
    would differ by one application where this class needs three against one
    (the developer's rule-5 ruling "Keep each raise working").
    """

    @staticmethod
    def _rows_and_terms():
        """The profile (raise believed forever) and the terms (through 2027)."""
        forever = FakeRaise(
            percentage="0.05", effective_month=3, effective_year=2027,
            is_recurring=True, terminal_year=None,
        )
        through_2027 = FakeRaise(
            percentage="0.05", effective_month=3, effective_year=2027,
            is_recurring=True, terminal_year=2027,
        )
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 9),  # $60,000.00 a year / 26
            raises=[forever], created_at=date(2026, 1, 1),
        )
        return profile, (through_2027,)

    @staticmethod
    def _periods_2029():
        """26 biweekly 2029 periods from Friday 2029-01-05."""
        start = date(2029, 1, 5)
        return [
            _period(start_date=start + timedelta(days=14 * i), period_id=i + 1)
            for i in range(26)
        ]

    def test_no_terms_prices_the_profiles_own_rows(self):
        """The default is the rows' own terms -- the stored plan, unchanged.

        ``raises`` is the rows converted to ``RaiseTerms`` values, never the
        rows themselves: the value's fields are the engine's whole contract
        with a raise, so a walk reading anything else fails here rather than
        pricing rows and values differently.
        """
        profile, _ = self._rows_and_terms()
        basis = payroll_basis(profile, self._periods_2029())

        assert basis.raise_terms is None
        assert basis.raises == terms_of(profile.raises)
        assert all(isinstance(term, RaiseTerms) for term in basis.raises)
        # 2029-06: three applications (2027, 2028, 2029) of 5% on $2,307.69:
        # 2671.43 a paycheck, 2671.43 x 26 = 69457.18 a year (R-SAL59).
        assert basis.base_pay_on(date(2029, 6, 1)).per_paycheck == Decimal("2671.43")
        assert basis.base_pay_on(date(2029, 6, 1)).annual == Decimal("69457.18")

    def test_a_supplied_set_is_canonicalised_too(self):
        """``raises`` is ``RaiseTerms`` values whichever arm supplied them.

        A supplied set spelled with raise-shaped objects is converted like
        the default arm's rows, so "the engine prices only the value" is a
        property of the basis and not of its callers' care -- a second
        adversarial review of this step found the first draft converting
        only the default arm.
        """
        profile, terms = self._rows_and_terms()
        believed = replace(
            payroll_basis(profile, self._periods_2029()), raise_terms=terms,
        )

        assert believed.raises == terms_of(terms)
        assert all(isinstance(term, RaiseTerms) for term in believed.raises)
        assert believed.raises[0].terminal_year == 2027

    def test_the_gross_is_priced_off_the_supplied_terms(
        self, simple_tax_configs,
    ):
        """Site 1: the paycheck's own gross reads the terms, not the rows.

        ``$2,423.07`` under the terms; a read that still went to the rows
        would price ``$2,671.43`` (the class docstring's walk).
        """
        profile, terms = self._rows_and_terms()
        periods = self._periods_2029()
        june = periods[11]  # 2029-06-08
        stored = payroll_basis(profile, periods)
        believed = replace(stored, raise_terms=terms)

        assert calculate_paycheck(
            stored, june, simple_tax_configs,
        ).earnings.gross_biweekly == Decimal("2671.43")
        assert calculate_paycheck(
            believed, june, simple_tax_configs,
        ).earnings.gross_biweekly == Decimal("2423.07"), (
            "the paycheck's gross was priced off the profile's rows, not the "
            "raise set the basis names"
        )

    def test_the_raise_event_is_badged_off_the_supplied_terms(
        self, simple_tax_configs,
    ):
        """The banner announces the set the paycheck was PRICED under.

        March 2029 badges the forever raise on the rows; under terms believed
        through 2027 it moved nothing that year and must badge nothing --
        otherwise the surface announces a raise the gross beside it was not
        priced with, the two-walks defect ``salary_raises`` records.
        """
        profile, terms = self._rows_and_terms()
        periods = self._periods_2029()
        march = periods[4]  # 2029-03-02
        stored = payroll_basis(profile, periods)
        believed = replace(stored, raise_terms=terms)

        assert calculate_paycheck(
            stored, march, simple_tax_configs,
        ).period.raise_event == "MERIT +5.00%"
        assert calculate_paycheck(
            believed, march, simple_tax_configs,
        ).period.raise_event == "", (
            "the raise event was badged off the profile's rows while the "
            "paycheck was priced off the basis's terms"
        )

    def test_the_wage_base_cumulative_reads_the_supplied_terms(self):
        """Site 3: the year-to-date gross sums the terms' salary.

        Ten prior 2029 paydays at ``$2,423.07`` sum to ``$24,230.70`` under
        the terms.  Under the rows the January and February paydays carry two
        applications (``$2,544.22``) and the March-May ones three
        (``$2,671.43``): ``4 x 2,544.22 + 6 x 2,671.43 = $26,205.46``.
        """
        profile, terms = self._rows_and_terms()
        periods = self._periods_2029()
        eleventh = periods[10]  # 2029-05-25; ten paydays before it in 2029
        stored = payroll_basis(profile, periods)
        believed = replace(stored, raise_terms=terms)

        assert _get_cumulative_wages(stored, eleventh.start_date) == Decimal("26205.46")
        assert _get_cumulative_wages(believed, eleventh.start_date) == Decimal("24230.70"), (
            "the FICA wage-base cumulative summed the profile's rows' salary, "
            "not the raise set the basis names"
        )

    def test_a_capped_deductions_year_to_date_reads_the_supplied_terms(self):
        """Site 2: the annual-cap replay prices prior paydays off the terms.

        A 10% deduction capped at ``$2,500.00`` a year.  Under the terms the
        ten prior paydays took ``10 x round($2,423.07 x 0.10) = $2,423.10``,
        leaving ``$76.90`` of room, so the eleventh paycheck's line is
        clamped to exactly that.  Under the rows they took
        ``4 x 254.42 + 6 x 267.14 = $2,620.52``, past the cap, so the line is
        ``$0.00`` -- which is also what a replay that read the rows while the
        live gross read the terms would produce, so ``$76.90`` pins the
        replay's raise set and not merely the paycheck's.
        """
        profile, terms = self._rows_and_terms()
        profile.lines = [FakeDeduction(
            name="HSA", amount="0.10", calc_method="percentage",
            annual_cap="2500",
        )]
        periods = self._periods_2029()
        eleventh = periods[10]
        stored = payroll_basis(profile, periods)
        believed = replace(stored, raise_terms=terms)

        def line_under(basis):
            return _priced_lines(
                _LineContext(
                    basis, eleventh.start_date,
                    basis.base_pay_on(eleventh.start_date).per_paycheck,
                ),
                _timing_id("pre_tax_deduction"),
            )[0].amount

        assert line_under(stored) == ZERO
        assert line_under(believed) == Decimal("76.90"), (
            "the capped deduction's year-to-date replay priced prior paydays "
            "off the profile's rows, not the raise set the basis names"
        )
