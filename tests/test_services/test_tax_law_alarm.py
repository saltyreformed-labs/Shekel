"""
Shekel Budget App -- Tests for the tax-law alarm (plan step salary:X-at-4).

The ONE check every tax-law alarm reads (ruling salary:R-SAL74): the banner on
every owner page and the weekly GitHub run from November 1, CI's refusal from
December 1.  What "complete" means is ruling salary:R-SAL86: the due year is
in the law and lists every state an earlier year lists, a year may ship in
parts, and every alarm names what is still missing.

Every law here is MADE UP and every day is passed in, so nothing in this
module moves when a real year is published or the calendar turns (ruling
salary:R-SAL80's reason, and the weekly calendar sweep's).
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models.ref import FilingStatus
from tests._tax_configs import tax_configs_for
from app.services.tax_law_alarm import Gap, Stage, alarm, due_year, gaps, speaks_from
from tests._test_helpers import EMPTY_TAX_LAW, made_up_law


class TestTheDueYear:
    """Which year a stage asks for: next year from its start date, this year before it."""

    @pytest.mark.parametrize(("today", "expected"), [
        (date(2026, 10, 31), 2026),
        (date(2026, 11, 1), 2027),
        (date(2026, 12, 31), 2027),
        (date(2027, 1, 1), 2027),
        (date(2027, 6, 15), 2027),
    ])
    def test_the_notice_asks_for_next_year_from_november_1(self, today, expected):
        """The banner and the weekly run start on November 1 (ruling R-SAL74)."""
        assert due_year(today, Stage.NOTICE) == expected

    @pytest.mark.parametrize(("today", "expected"), [
        (date(2026, 11, 1), 2026),
        (date(2026, 11, 30), 2026),
        (date(2026, 12, 1), 2027),
        (date(2027, 1, 1), 2027),
    ])
    def test_the_refusal_asks_for_next_year_from_december_1(self, today, expected):
        """CI refuses a month after the notice starts."""
        assert due_year(today, Stage.REFUSE) == expected


class TestWhatALawLacks:
    """``gaps``: whole years the law does not carry, and states a later year drops."""

    def test_a_complete_law_lacks_nothing(self):
        """Every year through the due one, each listing its predecessor's states."""
        law = made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}))
        assert not gaps(law, 2026)

    def test_next_year_missing_is_one_whole_year_gap(self):
        """The forgotten year: priced on the newest year the law carries meanwhile."""
        law = made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}))
        assert gaps(law, 2027) == (Gap(tax_year=2027, state=None, fallback_year=2026),)

    def test_two_missing_years_are_two_gaps_on_the_same_fallback(self):
        """A January with neither year added: each named, both priced on the newest."""
        law = made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}))
        assert gaps(law, 2028) == (
            Gap(tax_year=2027, state=None, fallback_year=2026),
            Gap(tax_year=2028, state=None, fallback_year=2026),
        )

    def test_a_year_that_ships_without_a_state_names_that_state(self):
        """Federal first, the state later (R-SAL86): the alarm names the state until it lands."""
        law = made_up_law((2026, {"NC": "0.0399", "ZZ": "0.0500"}), (2027, {"ZZ": "0.0450"}))
        assert gaps(law, 2027) == (Gap(tax_year=2027, state="NC", fallback_year=2026),)

    def test_a_missing_state_is_priced_on_the_latest_year_that_lists_it(self):
        """Not simply the year before: that year may lack the state too."""
        law = made_up_law((2025, {"NC": "0.0425"}), (2026, {}), (2027, {}))
        assert gaps(law, 2027) == (
            Gap(tax_year=2026, state="NC", fallback_year=2025),
            Gap(tax_year=2027, state="NC", fallback_year=2025),
        )

    def test_a_past_gap_stays_named_after_a_later_year_lands(self):
        """Adding next year complete does not silence a state an earlier year still lacks."""
        law = made_up_law((2025, {"NC": "0.0425"}), (2026, {}), (2027, {"NC": "0.0399"}))
        assert gaps(law, 2027) == (Gap(tax_year=2026, state="NC", fallback_year=2025),)

    def test_a_state_first_listed_after_the_first_year_cannot_be_written(self):
        """A state the app starts supporting in 2026 is written back into 2025 too.

        Ruling R-SAL129 (plan step salary:X-at-3): a state is listed from the
        law's first year, so no earlier year is priced on a later year's
        rules.  Until X-at-3 this case read the opposite -- a state first
        listed in 2026 owed nothing to 2025 -- and the alarms were silent
        about it while the resolver reached forward.
        """
        with pytest.raises(ValueError, match="lists ZZ from 2026 but not in its first year, 2025"):
            made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399", "ZZ": "0.0500"}))

    def test_a_listed_state_is_owed_by_every_year_after(self):
        """Once listed, a state is listed in every later year.

        Listed from the law's first year since plan step salary:X-at-3 (ruling
        R-SAL129); it was first listed in 2026 of a law opening in 2025 until
        then, which the law now refuses.
        """
        law = made_up_law((2025, {"ZZ": "0.0500"}), (2026, {"ZZ": "0.0500"}), (2027, {}))
        assert gaps(law, 2027) == (Gap(tax_year=2027, state="ZZ", fallback_year=2026),)

    def test_a_missing_year_and_a_missing_state_are_both_named(self):
        """A year shipped without its state, then the next year not shipped at all.

        2028's federal and FICA lines price on 2027, but its NC line on 2026
        (2027 lacks NC), so the whole-year gap carries NC priced on 2026 --
        otherwise it would describe NC as priced on 2027 (ruling R-SAL91).
        """
        law = made_up_law((2026, {"NC": "0.0399"}), (2027, {}))
        assert gaps(law, 2028) == (
            Gap(tax_year=2027, state="NC", fallback_year=2026),
            Gap(tax_year=2028, state=None, fallback_year=2027, priced_older=(
                Gap(tax_year=2028, state="NC", fallback_year=2026),
            )),
        )

    def test_a_missing_year_names_no_state_the_newest_year_lists(self):
        """NC, which the newest year lists, prices on it like the rest: only ZZ is named apart."""
        law = made_up_law((2026, {"NC": "0.0399", "ZZ": "0.0500"}), (2027, {"NC": "0.0350"}))
        assert gaps(law, 2028) == (
            Gap(tax_year=2027, state="ZZ", fallback_year=2026),
            Gap(tax_year=2028, state=None, fallback_year=2027, priced_older=(
                Gap(tax_year=2028, state="ZZ", fallback_year=2026),
            )),
        )

    def test_several_missing_states_are_named_alphabetically(self):
        """By year, then by state: never the law's dict order nor a set's hash order.

        Eight states, listed out of order, so an unsorted walk of the set they
        are held in matches the alphabet by chance about once in 40,000 runs.
        """
        states = ("WY", "AL", "TX", "NC", "MA", "CA", "OR", "FL")
        law = made_up_law((2026, {state: "0.0400" for state in states}), (2027, {}))
        assert [gap.state for gap in gaps(law, 2027)] == sorted(states)

    def test_a_law_with_no_year_lacks_the_due_year_with_nothing_to_price_it(self):
        """No release ships one; a test installs one, so the alarm must still answer."""
        assert gaps(EMPTY_TAX_LAW, 2027) == (Gap(tax_year=2027, state=None, fallback_year=None),)


class TestTheAlarm:
    """``alarm`` is ``gaps`` through the day's due year: one check for all three alarms."""

    _LAW = made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}))
    _MISSING_2027 = (Gap(tax_year=2027, state=None, fallback_year=2026),)

    @pytest.mark.parametrize(("today", "stage", "expected"), [
        (date(2026, 10, 31), Stage.NOTICE, ()),
        (date(2026, 11, 1), Stage.NOTICE, _MISSING_2027),
        (date(2026, 11, 30), Stage.REFUSE, ()),
        (date(2026, 12, 1), Stage.REFUSE, _MISSING_2027),
        (date(2027, 3, 1), Stage.NOTICE, _MISSING_2027),
        (date(2027, 3, 1), Stage.REFUSE, _MISSING_2027),
    ])
    def test_each_stage_speaks_from_its_own_day(self, today, stage, expected):
        """Silent before its start date; from it, the missing year until it is added."""
        assert alarm(self._LAW, today, stage) == expected

    def test_adding_the_year_silences_every_stage(self):
        """The whole cure is the release that adds the year."""
        law = made_up_law(
            (2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}), (2027, {"NC": "0.0350"}),
        )
        for stage in Stage:
            assert not alarm(law, date(2026, 12, 15), stage)


class TestWhenAStageStartsSpeaking:
    """``speaks_from``: the one day from which ``alarm`` is non-empty, never before it."""

    @pytest.mark.parametrize("years", [
        ((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"})),
        ((2026, {"NC": "0.0399"}), (2027, {})),
        ((2025, {"NC": "0.0425"}), (2026, {}), (2027, {"NC": "0.0350"})),
        ((2026, {}),),
    ])
    @pytest.mark.parametrize("stage", list(Stage))
    def test_the_alarm_is_silent_before_it_and_speaks_on_and_after_it(self, years, stage):
        """Checked on every day of 2025-2028, so the boundary is the only change."""
        law = made_up_law(*years)
        start = speaks_from(law, stage)
        day = date(2025, 1, 1)
        while day <= date(2028, 12, 31):
            assert bool(alarm(law, day, stage)) == (day >= start), day
            day += timedelta(days=1)

    def test_a_complete_law_starts_on_the_stage_date_of_its_newest_year(self):
        """Through 2026: the notice from 2026-11-01, the refusal from 2026-12-01."""
        law = made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"}))
        assert speaks_from(law, Stage.NOTICE) == date(2026, 11, 1)
        assert speaks_from(law, Stage.REFUSE) == date(2026, 12, 1)

    def test_a_law_with_no_year_speaks_on_every_date(self):
        """``None``: there is no day before which it is complete."""
        assert speaks_from(EMPTY_TAX_LAW, Stage.REFUSE) is None


class TestTheNoticeNamesTheYearTheFiguresUse:
    """``fallback_year`` is the year the PRICING door resolves, not a second rule for it."""

    @staticmethod
    def _profile(state_code="NC"):
        """A stand-in profile: the resolver reads its filing status and state only.

        Any filing status will do -- a made-up year states the same federal
        rules and state deduction for every status -- so the row is read by
        position, never by its name.
        """
        filing_status = db.session.query(FilingStatus).order_by(FilingStatus.id).first()
        return SimpleNamespace(filing_status_id=filing_status.id, state_code=state_code)

    def test_a_missing_state_is_priced_on_the_year_the_notice_names(self, app, tax_law):
        """2027 lacks NC: the state line prices on 2025's rate, and the gap says 2025."""
        law = tax_law(made_up_law((2025, {"NC": "0.0425"}), (2026, {}), (2027, {})))
        with app.app_context():
            (gap_2026, gap_2027) = gaps(law, 2027)
            priced = tax_configs_for(self._profile(), 2027)["state_config"]

            assert gap_2027 == Gap(tax_year=2027, state="NC", fallback_year=2025)
            assert gap_2026.fallback_year == 2025
            assert priced.flat_rate == Decimal("0.0425")

    def test_a_missing_years_state_line_is_priced_on_the_year_its_own_gap_names(
        self, app, tax_law,
    ):
        """2028 absent and 2027 lacking NC: FICA on 2027, NC on 2026 -- as the two gaps say."""
        law = tax_law(made_up_law((2026, {"NC": "0.0399"}), (2027, {})))
        with app.app_context():
            priced = tax_configs_for(self._profile(), 2028)

            assert gaps(law, 2028)[1:] == (
                Gap(tax_year=2028, state=None, fallback_year=2027, priced_older=(
                    Gap(tax_year=2028, state="NC", fallback_year=2026),
                )),
            )
            assert priced["fica_config"] is law.years[-1].fica
            assert priced["state_config"].flat_rate == Decimal("0.0399")

    def test_a_missing_year_is_priced_on_the_year_the_notice_names(self, app, tax_law):
        """2028 absent: FICA prices on 2026's rules, and the gap says 2026."""
        law = tax_law(made_up_law((2025, {"NC": "0.0425"}), (2026, {"NC": "0.0399"})))
        with app.app_context():
            gap_2028 = gaps(law, 2028)[-1]
            priced = tax_configs_for(self._profile(), 2028)

            assert gap_2028 == Gap(tax_year=2028, state=None, fallback_year=2026)
            assert priced["fica_config"] is law.years[-1].fica
            assert priced["state_config"].flat_rate == Decimal("0.0399")
