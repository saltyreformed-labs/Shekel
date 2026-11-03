"""Salary route tests: a payroll line read by its weekday (plan step recurrence:R8-c).

The line form shares the recurrence partial, so its "repeating on" control
offers the weekday readings too: a monthly weekday cadence picks ONE paycheck
a month, which ruling **R-SAL37** as amended admits (only weeks were withheld,
plan#2591).  ``seed_periods`` is ten biweekly paychecks from Fri 2026-01-02;
the paycheck engine reads projected paydays past them the same way.  The third
Tuesdays of 2026, counted off a calendar: Jan 20, Feb 17, Mar 17, Apr 21, May
19, Jun 16.  Read by its day instead, a line from Jan 20 is taken on the
paycheck holding the 20th -- the same paychecks through May, and in June the
one starting Jun 19 rather than Jun 5.
"""
from datetime import date

import pytest

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.services.pay_calendar import calendar_for
from app.services.payroll_basis import PayrollBasis
from tests.test_routes._repeats_on_page import weekday_token

from ._helpers import (
    _cadence_payload,
    _create_profile,
    _line_form,
    _prefill_attributes,
)

#: Every payday from the opening through June 2026, with whether a monthly
#: third-Tuesday line from Jan 20 is taken on it: the paycheck CONTAINING
#: each third Tuesday (Jan 16-29, Feb 13-26, Mar 13-26, Apr 10-23, May 8-21,
#: Jun 5-18), one a month.
_THIRD_TUESDAY_PAYCHECKS = [
    (date(2026, 1, 2), False), (date(2026, 1, 16), True),
    (date(2026, 1, 30), False), (date(2026, 2, 13), True),
    (date(2026, 2, 27), False), (date(2026, 3, 13), True),
    (date(2026, 3, 27), False), (date(2026, 4, 10), True),
    (date(2026, 4, 24), False), (date(2026, 5, 8), True),
    (date(2026, 5, 22), False), (date(2026, 6, 5), True),
    (date(2026, 6, 19), False),
]


def _line(profile, name):
    """Return *profile*'s line called *name*, as the request left it."""
    db.session.expire_all()
    return db.session.query(PaycheckLine).filter_by(
        salary_profile_id=profile.id, name=name,
    ).one()


def _rule_columns(line):
    """Return the stored ``(starts_on, nominal_day, weekday_reading)`` of *line*'s rule."""
    rule = line.recurrence_rule
    return rule.starts_on, rule.nominal_day, rule.weekday_reading


@pytest.mark.usefixtures("seed_periods")
class TestALineReadByItsWeekday:
    """Create, price and prefill a monthly third-Tuesday line."""

    def _add_third_tuesday_line(self, auth_client, seed_user):
        """Add the line through the line form, its weekday token read off the page."""
        profile = _create_profile(seed_user)
        page = auth_client.get(f"/salary/{profile.id}/edit").data.decode()
        response = auth_client.post(
            f"/salary/{profile.id}/lines",
            data=_line_form(
                "Union dues",
                **_cadence_payload(
                    RecurrenceUnitEnum.MONTH,
                    PeriodPlacementEnum.CONTAINING_DATE,
                    ceiling=None,
                ),
                starts_on="2026-01-20",
                repeats_on=weekday_token(page, counts_from_end=False),
            ),
            follow_redirects=True,
        )
        assert response.status_code == 200
        return profile

    def test_the_line_is_taken_on_the_paycheck_holding_each_third_tuesday(
        self, auth_client, seed_user,
    ):
        """One paycheck a month, and June's is the one starting Jun 5."""
        profile = self._add_third_tuesday_line(auth_client, seed_user)
        line = _line(profile, "Union dues")

        assert _rule_columns(line) == (date(2026, 1, 20), None, "from_start")
        basis = PayrollBasis(profile, calendar_for(seed_user["user"].id))
        assert [
            (payday, basis.line_applies_on(line, payday))
            for payday, _taken in _THIRD_TUESDAY_PAYCHECKS
        ] == _THIRD_TUESDAY_PAYCHECKS

    def test_the_edit_prefill_carries_the_reading(self, auth_client, seed_user):
        """``data-line-repeats-on`` is the token the form posts back."""
        profile = self._add_third_tuesday_line(auth_client, seed_user)
        line = _line(profile, "Union dues")

        html = auth_client.get(f"/salary/{profile.id}/edit").data.decode()

        attributes = _prefill_attributes(html, line.id)
        assert (attributes["starts-on"], attributes["repeats-on"]) == (
            "2026-01-20", "from_start",
        )


@pytest.mark.usefixtures("seed_periods")
class TestAReadingBesideABlankStart:
    """A reading posted beside a blank start is dropped with the derivation."""

    @pytest.mark.parametrize(
        ("cadence", "derived"),
        [
            # Thu 2026-01-01, the derived 1st, is a first Thursday: a kept
            # reading would FIT, and the line would walk first Thursdays.
            pytest.param(
                (
                    RecurrenceUnitEnum.MONTH,
                    PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER, "1", None,
                ),
                date(2026, 1, 1), id="monthly",
            ),
            # The opening payday: a paycheck cadence has no month coordinate,
            # so a kept reading would be refused by the spec -- a 500.
            pytest.param(
                (
                    RecurrenceUnitEnum.PERIOD,
                    PeriodPlacementEnum.CONTAINING_DATE, "2", "",
                ),
                date(2026, 1, 2), id="every-other-paycheck",
            ),
        ],
    )
    def test_the_line_is_read_by_the_derived_dates_day(
        self, auth_client, seed_user, cadence, derived,
    ):
        """The script disables the control while the box is blank; this is a crafted POST.

        *cadence* is the unit, the placement, the interval box and the
        ceiling box (``None`` for the disabled one beside a monthly unit).
        """
        unit, placement, interval, ceiling = cadence
        profile = _create_profile(seed_user)

        response = auth_client.post(
            f"/salary/{profile.id}/lines",
            data=_line_form(
                "Crafted",
                **_cadence_payload(unit, placement, interval=interval, ceiling=ceiling),
                repeats_on="from_start",
            ),
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert _rule_columns(_line(profile, "Crafted")) == (derived, None, None)
