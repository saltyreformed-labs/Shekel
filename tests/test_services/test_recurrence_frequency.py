"""How often a recurrence fires, with no schedule (plan step R7a-2b).

``resolve()`` answers what a recurrence MEANS against one owner's pay
calendar, and most of that answer needs no calendar: the interval, the unit and
the placement are what the rule itself states.  This module covers that
schedule-free half.

**Written where the old shapes could not answer.**  The seven-branch switch and
the three-member ``frozenset`` this replaced were both correct for the cadences
someone had listed, so a test over those cadences alone cannot tell the
derivation from the enumeration.  Every case here either uses a cadence the
closed pattern set could not have named, varies the OWNER, or pins a boundary.

**Two classes LEFT at plan step R7c-c**, with the seam they covered:
``TestCadenceOfReadsAPatternWithNoSchedule`` (``cadence_of`` took a pattern id;
it takes a RULE now and its cases live in ``test_recurrence_reading``) and
``TestTheStorageEncodingRoundTrips`` (``encode_cadence`` / ``decode_pattern``
were inverses of each other and are both deleted).  What is here instead is
what the freed vocabulary owes: the OFFER SET is exactly the set the app can
HONOUR (plan step R8-a; it was "the resolvable set" while ``anchor_family``
gated it), the placement really is inert where the offer set says it is, and
one rhythm has one spelling.

Clock discipline (``.claude/rules/testing.md``): nothing here reads a clock.
"""

from decimal import Decimal

import pytest

from app.enums import (
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.services.pay_calendar import PayCadence
from app.services.pay_rhythm import FixedDays
from app.services.recurrence import (
    AuthorableCadence,
    Cadence,
    RecurrenceFrequencyError,
    authorable_cadences,
    can_repeat_within_month,
    canonical_cadence,
    emits_period_starts,
    fires_on_day_of_month,
    has_day_of_month_coordinate,
    is_authorable,
)

#: 14 days between paydays, 26 a year.
_BIWEEKLY = PayCadence(FixedDays(14))

#: 7 days between paydays, 52 a year.
_WEEKLY = PayCadence(FixedDays(7))

#: 30 days between paydays, 12 a year -- a paycheck IS a month.
_MONTHLY_PAID = PayCadence(FixedDays(30))


class TestUnitsPerYearIsExact:
    """The count before the interval divides it, and it is a whole number."""

    @pytest.mark.parametrize(
        "unit,expected",
        [
            (RecurrenceUnitEnum.WEEK, "52"),
            (RecurrenceUnitEnum.MONTH, "12"),
            (RecurrenceUnitEnum.YEAR, "1"),
        ],
    )
    def test_a_calendar_unit_is_the_same_for_every_owner(self, unit, expected):
        """No owner varies how many months are in a year.

        The pay cadence is handed in and must be ignored: a monthly bill is
        monthly for a weekly-paid owner too.  Asserting across three cadences
        is what makes "ignored" a measurement rather than a claim.
        """
        cadence = Cadence(interval_n=1, unit=unit)
        for pay in (_BIWEEKLY, _WEEKLY, _MONTHLY_PAID):
            assert cadence.units_per_year(pay) == Decimal(expected)

    def test_the_week_unit_derives_52_from_the_same_rule_as_a_pay_cadence(self):
        """``round(365.2425 / 7) = 52`` -- not a hardcoded 52.

        Plan step R8 is the WEEK unit's first writer; this pins that it
        inherits the paycheck-count rule rather than a second one invented for
        it, by asserting the two agree.
        """
        assert Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.WEEK,
        ).units_per_year(_BIWEEKLY) == PayCadence(
            FixedDays(7),
        ).periods_per_year

    def test_the_period_unit_is_the_OWNER_S_count(self):
        """The one unit whose year depends on who is asking.

        26 biweekly, 52 weekly, 12 monthly-paid -- three answers for one
        cadence value, which is the whole reason this takes a parameter.
        """
        cadence = Cadence(interval_n=1, unit=RecurrenceUnitEnum.PERIOD)
        assert cadence.units_per_year(_BIWEEKLY) == Decimal("26")
        assert cadence.units_per_year(_WEEKLY) == Decimal("52")
        assert cadence.units_per_year(_MONTHLY_PAID) == Decimal("12")

    def test_it_does_NOT_apply_the_interval(self):
        """Every 3 paychecks still reports 26 units a year, not 8.67.

        The accuracy decision this method exists for: handing back the exact
        whole count lets a money conversion divide ONCE by the exact integer
        ``interval_n * 12``.  Returning the quotient instead rounds twice and
        moved 31,072 displayed cents in a 52,000,000-case sweep -- wrongly.
        ``occurrences_per_year`` is the one that applies the interval.
        """
        every_third = Cadence(
            interval_n=3, unit=RecurrenceUnitEnum.PERIOD,
        )
        assert every_third.units_per_year(_BIWEEKLY) == Decimal("26")
        assert every_third.occurrences_per_year(_BIWEEKLY) == (
            Decimal("26") / 3
        )

    def test_a_unit_with_no_yearly_count_is_refused(self):
        """A member added to the enum without one raises rather than guesses.

        Constructed by hand because every member HAS a count today -- which is
        the point: the refusal is the control on the next member added, and a
        control that cannot be exercised is not one.  A silent default here
        would put a wrong monthly figure in the emergency-fund baseline.
        """
        class _Unlisted:  # pylint: disable=too-few-public-methods
            """A unit member this module has no yearly count for."""

            def __repr__(self):
                return "<Unlisted>"

        with pytest.raises(RecurrenceFrequencyError, match="no yearly count"):
            Cadence(
                interval_n=1, unit=_Unlisted(),
            ).units_per_year(_BIWEEKLY)


class TestThePerMonthCeilingCountsAndPrices:
    """The cadence's third value reaches every "how often" (plan step salary:R15-a).

    A rule that admits at most N occurrences a month fires at most
    ``12 N`` times a year, and a monthly equivalent priced off the pair alone
    would charge the developer's eleven "at most 2 a month" payroll lines
    (``$517.21`` a paycheck between them) at ``26/12`` of their real monthly
    cost.  Every figure below is hand-computed beside the assertion.
    """

    def test_a_ceiling_caps_the_yearly_rate(self):
        """Every paycheck at most 2 a month is 24 a year on a biweekly owner."""
        cadence = Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.PERIOD, max_per_month=2,
        )

        # min(26 / 1, 12 x 2) = 24.
        assert cadence.occurrences_per_year(_BIWEEKLY) == Decimal("24")
        # min(52 / 1, 12 x 2) = 24 on a weekly owner too.
        assert cadence.occurrences_per_year(_WEEKLY) == Decimal("24")
        # A monthly-paid owner has 12 paychecks; the ceiling never binds.
        assert cadence.occurrences_per_year(_MONTHLY_PAID) == Decimal("12")

    def test_a_ceiling_the_pair_never_reaches_does_not_bind(self):
        """Weekly at most 5 a month stays 52: no month holds five weeks."""
        cadence = Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.WEEK, max_per_month=5,
        )

        # 12 x 5 x 1 = 60 is not below 52, so the pair's rate stands.
        assert not cadence.ceiling_binds(_WEEKLY)
        assert cadence.occurrences_per_year(_WEEKLY) == Decimal("52")
        # 12 x 4 x 1 = 48 IS below 52: the first four weeks of each month.
        assert Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.WEEK, max_per_month=4,
        ).ceiling_binds(_WEEKLY)

    def test_no_ceiling_never_binds_and_leaves_the_rate_alone(self):
        """``None`` is every rule authored before the step."""
        cadence = Cadence(interval_n=3, unit=RecurrenceUnitEnum.PERIOD)

        assert not cadence.ceiling_binds(_BIWEEKLY)
        assert cadence.occurrences_per_year(_BIWEEKLY) == Decimal("26") / 3

    def test_a_binding_ceiling_prices_exactly_amount_times_ceiling(self):
        """$517.21 at most twice a month is $1,034.42 a month, exact."""
        cadence = Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.PERIOD, max_per_month=2,
        )

        # 517.21 x 2 = 1,034.42 -- an integer multiply, no division at all.
        assert cadence.monthly_equivalent(
            Decimal("517.21"), _BIWEEKLY,
        ) == Decimal("1034.42")

    def test_an_unceilinged_rule_prices_by_the_one_division(self):
        """The arm the aggregator spelled inline until this step, verbatim."""
        cadence = Cadence(interval_n=3, unit=RecurrenceUnitEnum.PERIOD)

        # 100 x 26 / (3 x 12) = 2600 / 36 = 72.222...
        assert cadence.monthly_equivalent(
            Decimal("100"), _BIWEEKLY,
        ) == Decimal("100") * Decimal("26") / Decimal("36")

    def test_a_non_binding_ceiling_prices_like_no_ceiling(self):
        """Weekly at most 5 a month prices as weekly: the pair is the rate."""
        ceilinged = Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.WEEK, max_per_month=5,
        )
        plain = Cadence(interval_n=1, unit=RecurrenceUnitEnum.WEEK)

        assert ceilinged.monthly_equivalent(
            Decimal("10"), _WEEKLY,
        ) == plain.monthly_equivalent(Decimal("10"), _WEEKLY)

    @pytest.mark.parametrize(
        ("unit", "expected"),
        [
            (RecurrenceUnitEnum.PERIOD, True),
            (RecurrenceUnitEnum.WEEK, True),
            (RecurrenceUnitEnum.MONTH, False),
            (RecurrenceUnitEnum.YEAR, False),
        ],
        ids=lambda value: str(value),
    )
    def test_which_units_can_repeat_within_a_month(self, unit, expected):
        """Exactly the units WITHOUT a day-of-month coordinate: paychecks and weeks.

        The EXTENSION written out beside the derivation, for the reason the
        offer set's is: a predicate asserted only against its own source
        agrees with any rule at all.
        """
        assert can_repeat_within_month(unit) is expected
        assert can_repeat_within_month(unit) is (
            not has_day_of_month_coordinate(unit)
        )

    def test_canonical_cadence_carries_the_ceiling(self):
        """The rewrite that folds months into years never sees a ceiling; the rest carry it."""
        assert canonical_cadence(
            2, RecurrenceUnitEnum.PERIOD, 1,
        ) == Cadence(
            interval_n=2, unit=RecurrenceUnitEnum.PERIOD, max_per_month=1,
        )
        # The fold carries a ceiling through too -- the honest reading of a
        # value ``ResolvedRecurrence`` then refuses beside a YEAR unit, which
        # is that type's job and not this function's.
        assert canonical_cadence(12, RecurrenceUnitEnum.MONTH, 3) == Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.YEAR, max_per_month=3,
        )


class TestTheOfferSetIsWhatTheAppCanHonour:
    """What a form may offer is what the app can carry out (plan step R8-a).

    **The property plan step R7b-2 gave the picker, re-based twice.**  While a
    cadence had to have a NAME the binding constraint was storage, and the
    offer set was the ENCODER's table inverted; R7c-c made every reading
    storable and left the gate on ``anchor_family``, which asked whether a
    FIRST OCCURRENCE could be derived -- a question ruling **R-R16** had
    already answered for every unit by making that date authored.  R8-a
    replaced the router with two live rules, and plan step R8-b deleted the
    one that withheld the ``WEEK`` unit by dating a weekly row from its
    occurrence.  These cases grade the set against the rule that is left
    rather than against a list written here, which is the difference between
    a derivation and an enumeration that happens to agree.
    """

    def test_the_offer_set_is_exactly_these_seven_readings(self):
        """The offer set's EXTENSION, written out, beside the derivation.

        **This case used to assert ``has_row_date_coordinate(offer.unit)`` over
        the offer set**, which is the condition ``authorable_cadences`` filtered
        ON -- a tautology whose docstring claimed it proved a row could be
        dated.  Both adversarial reviews of plan step R8-a named it.  What a
        test can honestly hold here is the EXTENSION: seven readings since plan
        step R8-b (paychecks once, weeks / months / years at both placements),
        written out, so a change to the derived rule surfaces as a diff a reader
        has to justify rather than as a silently different set.

        The sweep below still grades the DERIVATION; this grades the ANSWER,
        and the pair is the point -- a derivation asserted only against itself
        agrees with any rule at all.
        """
        assert set(authorable_cadences()) == {
            AuthorableCadence(unit=unit, placement=placement)
            for unit, placement in [
                (RecurrenceUnitEnum.PERIOD,
                 PeriodPlacementEnum.CONTAINING_DATE),
                (RecurrenceUnitEnum.WEEK,
                 PeriodPlacementEnum.CONTAINING_DATE),
                (RecurrenceUnitEnum.WEEK,
                 PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER),
                (RecurrenceUnitEnum.MONTH,
                 PeriodPlacementEnum.CONTAINING_DATE),
                (RecurrenceUnitEnum.MONTH,
                 PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER),
                (RecurrenceUnitEnum.YEAR,
                 PeriodPlacementEnum.CONTAINING_DATE),
                (RecurrenceUnitEnum.YEAR,
                 PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER),
            ]
        }

    def test_every_honourable_pair_is_offered_unless_it_is_inert(self):
        """And nothing honourable is silently withheld.

        The REVERSE direction, and it is the one an enumeration fails in: a
        pair the rules admit but the picker forgot is a cadence the app can run
        and the user cannot choose.  The only admitted exception is a placement
        that changes nothing
        (:func:`~app.services.recurrence.emits_period_starts`), and the case
        below is what proves that claim rather than trusting it.
        """
        honourable = set()
        for unit in RecurrenceUnitEnum:
            for placement in PeriodPlacementEnum:
                if emits_period_starts(unit) and (
                    placement is not PeriodPlacementEnum.CONTAINING_DATE
                ):
                    continue
                honourable.add(AuthorableCadence(unit=unit, placement=placement))

        assert set(authorable_cadences()) == honourable

    def test_the_year_unit_offers_BOTH_placements(self):
        """The one reading plan step R8-a WIDENED.

        ``anchor_family`` refused ``(YEAR, first paycheck)`` because
        ``_resolution._first_of_month_anchor`` would have seated it on "the 1st
        of the first qualifying month", firing a yearly bill in whichever month
        the owner's schedule happened to open in.  Ruling **R-R16** deleted
        that derivation at plan step R7c-b and the refusal outlived it: a
        year-scale rule fires on its own authored date and defers onto the next
        paycheck, exactly as its MONTH twin already did.

        Keyed on the YEAR unit rather than on a count of offers, so a later
        step that adds a placement does not have to edit this case to keep it
        meaningful.
        """
        year_placements = {
            offer.placement for offer in authorable_cadences()
            if offer.unit is RecurrenceUnitEnum.YEAR
        }

        assert year_placements == set(PeriodPlacementEnum)

    def test_month_and_year_are_authorable_on_the_SAME_placements(self):
        """The property ``canonical_cadence`` rests on, proven not assumed.

        That function rewrites ``(12k, MONTH)`` to ``(k, YEAR)`` with no
        placement guard from plan step R8-a, and the guard it dropped existed
        because the two units were authorable on DIFFERENT placement sets.
        They cannot be now -- both have a day-of-month coordinate and neither
        emits period starts, so :func:`authorable_cadences`' two rules answer
        identically for them -- and asserting it here is what makes the
        substitution safe by derivation rather than by inspection.
        """
        by_unit = {}
        for offer in authorable_cadences():
            by_unit.setdefault(offer.unit, set()).add(offer.placement)

        assert by_unit[RecurrenceUnitEnum.MONTH] == (
            by_unit[RecurrenceUnitEnum.YEAR]
        )

    def test_a_row_is_dated_from_a_day_for_exactly_these_two_readings(self):
        """``fires_on_day_of_month``'s EXTENSION over the whole product.

        **This case asserted the function's own body against itself until an
        adversarial review of plan step R8-a named it.**  It read
        ``fires_on_day_of_month(u, p) is (has_day_of_month_coordinate(u) and p
        is CONTAINING_DATE)``, which after R8-a is character-for-character what
        the function computes -- a tautology whose docstring claimed to be the
        measurement that justified collapsing the anchor-family router.  It
        could not be: that measurement compared the router against this
        expression, and the router is DELETED, so nothing in the suite can
        re-derive it.  It is recorded where a retired measurement belongs, in
        ``historical/recurrence_r8a_as_built_2026-08-16.md``.

        What a live case can hold is the extension, written out.  It is the
        answer that decides which day of the month a rule schedules its rows on
        (``_reading.scheduling_day_of_month``); since plan step R8-b it no
        longer decides whether a row is dated from its occurrence, which a
        weekly row is with no day of the month (``_row_day.date_row``).
        Writing the pairs down is what makes a change arrive as a diff rather
        than as a quietly different set.
        """
        dated_from_a_day = {
            (unit, placement)
            for unit in RecurrenceUnitEnum
            for placement in PeriodPlacementEnum
            if fires_on_day_of_month(unit, placement)
        }

        assert dated_from_a_day == {
            (RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.CONTAINING_DATE),
            (RecurrenceUnitEnum.YEAR, PeriodPlacementEnum.CONTAINING_DATE),
        }
        # And the two facts it is composed of really are different questions:
        # every DEFERRING reading of a month-spanning unit has a day-of-month
        # coordinate and is still dated from its paycheck.  That divergence is
        # the one plan step R7c-b's wrong-money defect turned on.
        for unit in (RecurrenceUnitEnum.MONTH, RecurrenceUnitEnum.YEAR):
            assert has_day_of_month_coordinate(unit)
            assert not fires_on_day_of_month(
                unit, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )

    def test_the_month_unit_offers_BOTH_placements(self):
        """Plan ledger row **D32**'s defect ceasing to exist.

        The closed set stored "every 1 month funded from the first paycheck"
        and had no quarterly or semi-annual twin, so a placement was a property
        of the ``(unit, interval)`` PAIR: raising a Monthly First rule's
        interval silently reassigned the funding choice and hid the row.  The
        offer set names pairs rather than triples now, so the MONTH unit admits
        both placements at every interval and there is no such reassignment
        left to notice.
        """
        month_placements = {
            offer.placement for offer in authorable_cadences()
            if offer.unit is RecurrenceUnitEnum.MONTH
        }

        assert month_placements == set(PeriodPlacementEnum)

    @pytest.mark.parametrize("interval_n", [1, 2, 3, 6, 7, 12, 500])
    def test_is_authorable_agrees_with_the_offer_set_at_every_interval(
        self, interval_n,
    ):
        """The validator and the form cannot disagree about the set.

        Swept over intervals the closed set could name and intervals it could
        not, because the whole content of freeing the interval is that the
        answer no longer depends on it.
        """
        for unit in RecurrenceUnitEnum:
            for placement in PeriodPlacementEnum:
                offered = AuthorableCadence(
                    unit=unit, placement=placement,
                ) in set(authorable_cadences())

                assert is_authorable(interval_n, unit, placement) is offered, (
                    f"{interval_n} {unit} {placement}"
                )

    @pytest.mark.parametrize("interval_n", [0, -1, -12])
    def test_is_authorable_refuses_a_non_positive_interval(self, interval_n):
        """The one thing about a cadence the interval still decides.

        A negative control for the sweep above: without it that sweep passes
        against an ``is_authorable`` that ignores the interval entirely, which
        is exactly what freeing it invites.
        """
        for offer in authorable_cadences():
            assert not is_authorable(
                interval_n, offer.unit, offer.placement,
            )


class TestThePlacementIsReallyInertForPaychecks:
    """``emits_period_starts`` is a claim about the WALK, proven over one.

    The offer set withholds the pay-period unit's second placement on the
    ground that both carry a payday back to its own paycheck.  That is an
    argument in a docstring until something drives it: a wrong answer here
    would mean the form hides a control that DOES decide which paycheck pays a
    bill, which is money.
    """

    def test_both_placements_place_every_payday_identically(self, app):
        """Over a whole schedule, not a sampled date.

        Every payday the shared calendar holds, under both placements: the
        pairs are equal or the unit's placement is not inert and the offer set
        is withholding a real choice.
        """
        # Pylint: ``import-outside-toplevel`` -- the shared calendar builder
        # lives in a sibling test module and importing it at module scope would
        # make this file's collection depend on that one's.
        # pylint: disable=import-outside-toplevel
        from app.services.recurrence import place
        from tests.test_services.test_recurrence_resolution import (
            build_calendar,
        )

        with app.app_context():
            calendar = build_calendar()
            assert calendar.periods, "the shared schedule must hold periods"

            for period in calendar.periods:
                payday = period.start_date
                containing = place(
                    payday, calendar, PeriodPlacementEnum.CONTAINING_DATE,
                )
                starting = place(
                    payday, calendar,
                    PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                )

                assert containing == starting, payday

    def test_a_MID_PERIOD_date_is_where_the_two_differ(self, app):
        """The negative control, SHOWN to fire.

        Without it the case above passes against a ``place`` that ignored the
        placement entirely -- which would make every "Funded from" choice on
        the form inert rather than one unit's.  A date strictly inside a
        paycheck is the shape the two placements exist to tell apart.
        """
        # pylint: disable=import-outside-toplevel
        from datetime import timedelta

        from app.services.recurrence import place
        from tests.test_services.test_recurrence_resolution import (
            build_calendar,
        )

        with app.app_context():
            calendar = build_calendar()
            mid = calendar.periods[0].start_date + timedelta(days=1)

            containing = place(
                mid, calendar, PeriodPlacementEnum.CONTAINING_DATE,
            )
            starting = place(
                mid, calendar,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )

            assert containing != starting
            assert emits_period_starts(RecurrenceUnitEnum.PERIOD)
            assert not emits_period_starts(RecurrenceUnitEnum.MONTH)


class TestOneRhythmHasOneSpelling:
    """Ruling **R-R17**, applied at the door (plan step R7c-c).

    Freeing the interval makes ``(12, MONTH)`` authorable, and it is the same
    rhythm as ``(1, YEAR)``: same month stride, same day-of-month coordinate,
    same yearly
    count.  Two spellings is the second vocabulary this arc removed from the
    table arriving back through the form -- the Recurring surface would word
    one annual bill "Every 12 months" and another "Yearly", and the obligations
    filter would group them apart.
    """

    @pytest.mark.parametrize(
        "interval_n,expected",
        [
            (12, 1),
            (24, 2),
            (120, 10),
        ],
    )
    def test_a_whole_number_of_years_in_months_becomes_years(
        self, interval_n, expected,
    ):
        """The substitution itself, at three multiples."""
        assert canonical_cadence(
            interval_n, RecurrenceUnitEnum.MONTH,
        ) == Cadence(interval_n=expected, unit=RecurrenceUnitEnum.YEAR)

    @pytest.mark.parametrize("interval_n", [1, 2, 3, 6, 11, 13, 18])
    def test_a_month_count_that_is_not_whole_years_is_left_alone(
        self, interval_n,
    ):
        """Every other month interval keeps the unit the caller stated.

        18 months is a year and a half and has no YEAR spelling; rewriting it
        to 1 or 2 years would move every occurrence after the first.
        """
        assert canonical_cadence(
            interval_n, RecurrenceUnitEnum.MONTH,
        ) == Cadence(interval_n=interval_n, unit=RecurrenceUnitEnum.MONTH)

    @pytest.mark.parametrize("placement", list(PeriodPlacementEnum))
    def test_the_substitution_holds_under_EVERY_placement(self, placement):
        """The guard that LEFT at plan step R8-a, replaced by this sweep.

        ``(12, MONTH, first paycheck)`` used to be the one cadence the
        substitution had to skip: it was authorable while its YEAR spelling was
        not, so rewriting it would have turned a storable cadence into a
        refusal at the door about to store it.  That asymmetry was
        ``anchor_family`` refusing the year-scale deferred reading on a
        derivation R7c-b had deleted; with the reading admitted the two units
        are authorable on the same placements by derivation, so the guard
        checked a state the offer set can no longer produce.

        Swept over the whole placement axis rather than dropped, because that
        is the claim the deletion rests on: the rewrite must be safe for EVERY
        placement, not merely for the one it used to skip.
        """
        canonical = canonical_cadence(12, RecurrenceUnitEnum.MONTH)

        assert canonical == Cadence(
            interval_n=1, unit=RecurrenceUnitEnum.YEAR,
        )
        assert is_authorable(12, RecurrenceUnitEnum.MONTH, placement)
        assert is_authorable(
            canonical.interval_n, canonical.unit, placement,
        )
        assert fires_on_day_of_month(RecurrenceUnitEnum.MONTH, placement) is (
            fires_on_day_of_month(canonical.unit, placement)
        )

    @pytest.mark.parametrize(
        "unit", [RecurrenceUnitEnum.PERIOD, RecurrenceUnitEnum.YEAR],
    )
    def test_no_other_unit_is_touched(self, unit):
        """Twelve PAYCHECKS is not a year, and twelve years is not anything.

        The substitution reads a MONTH span, so a unit measured in paychecks
        has none and a unit already coarse has nothing coarser to become.
        """
        assert canonical_cadence(12, unit) == Cadence(
            interval_n=12, unit=unit,
        )

    def test_it_is_idempotent(self):
        """Canonicalising twice is canonicalising once.

        ``resolve`` runs it on every read as well as every write, so a
        non-idempotent rule would re-spell a stored cadence on each pass.
        """
        once = canonical_cadence(24, RecurrenceUnitEnum.MONTH)
        twice = canonical_cadence(once.interval_n, once.unit)

        assert once == twice

    @pytest.mark.parametrize("interval_n", [12, 24, 120])
    def test_both_spellings_fire_the_same_number_of_times_a_year(
        self, interval_n,
    ):
        """The substitution is behaviour-preserving, measured not asserted.

        If the two spellings did not name one rhythm, canonicalising would
        change what a rule COSTS per month on ``/obligations`` -- so the yearly
        rate is the honest thing to compare.
        """
        months = Cadence(
            interval_n=interval_n, unit=RecurrenceUnitEnum.MONTH,
        )
        years = canonical_cadence(interval_n, RecurrenceUnitEnum.MONTH)

        assert years.occurrences_per_year(_BIWEEKLY) == (
            months.occurrences_per_year(_BIWEEKLY)
        )
