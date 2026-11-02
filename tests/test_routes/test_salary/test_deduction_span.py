"""Salary route tests: the deduction form's span: a line's stated start and stop, its refusals,
and regeneration (plan step salary:R15-c).

Split from ``tests/test_routes/test_salary.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
import re
from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.paycheck_line import PaycheckLine
from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.schemas.validation import end_bound_before_start_message
from app.services.pay_calendar import calendar_for
from app.services.payroll_basis import PayrollBasis

from app.services.balance_at import BalanceContext

from tests._test_helpers import rebuild_calendar

from ._helpers import (
    _create_profile,
    _cadence_payload,
    _prefill_attributes,
    _authored_columns,
    _a_line,
    _line_form,
    _TestDeductionCadenceFormMembers,
)


class TestDeductionCadenceForm(_TestDeductionCadenceFormMembers):
    """Start, stop, refusals, regeneration; members on _TestDeductionCadenceFormMembers."""

    def test_a_stated_start_and_bound_reach_the_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``starts_on`` and a closing bound are the owner's and are authored.

        Until plan step salary:R18-c every one of these was dropped on the
        wire (rulings R-SAL30 / R-SAL31); ruling **R-SAL38** (2) gives every
        line a start and an optional end, so a monthly line that begins
        2026-04-01 and ends 2026-06-30 is stored exactly so.
        ``max_occurrences`` beside an ``on_date`` mode is
        the input that shape does not need and is dropped by the compose.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    **_line_form(
                        "Spanned",
                        **_cadence_payload(
                            RecurrenceUnitEnum.MONTH,
                            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                            ceiling=None,
                        ),
                    ),
                    "starts_on": "2026-04-01",
                    "recurrence_end_mode": "on_date",
                    "end_date": "2026-06-30",
                    "max_occurrences": "2",
                },
                follow_redirects=True,
            )
            assert response.status_code == 200
            added = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="Spanned").one()
            )
            rule = added.recurrence_rule
            assert rule.starts_on == date(2026, 4, 1)
            assert rule.nominal_day is None
            assert rule.end_date == date(2026, 6, 30)
            assert rule.max_occurrences is None
            # The section words the span beside the cadence, and the edit
            # button carries it for the prefill.
            html = response.data.decode()
            assert "from Apr 01, 2026" in html
            assert 'data-line-starts-on="2026-04-01"' in html
            assert 'data-line-end-mode="on_date"' in html
            assert 'data-line-end-date="2026-06-30"' in html

    def test_a_blank_start_is_the_opening_and_a_line_with_no_rule_prefills_blank(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Blank start: the unit's zero at the opening (R-SAL36) as before; a rule-less line's edit button carries no date."""
        with app.app_context():
            profile = _create_profile(seed_user)
            auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Monthly", **_cadence_payload(
                        RecurrenceUnitEnum.MONTH,
                        PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                        ceiling=None,
                    ),
                ),
                follow_redirects=True,
            )
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form("Every", **_cadence_payload(
                    RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                )),
                follow_redirects=True,
            )
            monthly = db.session.query(PaycheckLine).filter_by(name="Monthly").one()
            every = db.session.query(PaycheckLine).filter_by(name="Every").one()
            assert monthly.recurrence_rule.starts_on == date(2026, 1, 1)
            assert every.recurrence_rule is None
            html = response.data.decode()
            every_button = re.search(
                rf'<button[^>]*data-line-edit="{every.id}"[^>]*>', html, re.S,
            ).group(0)
            assert 'data-line-starts-on=""' in every_button
            assert 'data-line-end-mode="never"' in every_button
            # The derived default is worded as nothing: no "from" on the monthly line.
            assert "from Jan 01, 2026" not in html

    def test_an_every_paycheck_line_with_a_span_keeps_a_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Every paycheck FROM a date, or UNTIL one, is a rule (R-SAL29's canonicalisation needs no span).

        The developer's ended `$100` allowance is this shape: taken on every
        paycheck between two dates.  The engine then admits exactly the
        paydays inside the span.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    **_line_form("Bounded", **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                    )),
                    "starts_on": "2026-02-01",
                    "recurrence_end_mode": "on_date",
                    "end_date": "2026-03-01",
                },
                follow_redirects=True,
            )
            line = db.session.query(PaycheckLine).filter_by(name="Bounded").one()
            rule = line.recurrence_rule
            assert rule is not None
            calendar = calendar_for(seed_user["user"].id)
            # A paycheck cadence's first occurrence IS a paycheck: the seam
            # places a stated date on the payday whose period holds it (the
            # rule every template form's start already follows).
            first_payday = calendar.period_containing(date(2026, 2, 1)).start_date
            assert (rule.starts_on, rule.end_date, rule.max_per_month) == (
                first_payday, date(2026, 3, 1), None,
            )
            assert first_payday == date(2026, 1, 30)
            basis = PayrollBasis(profile, calendar)
            paydays = [period.start_date for period in calendar.saved()]
            # The ten seeded paydays run 2026-01-02 .. 2026-05-08 a fortnight
            # apart; the span admits the three inside it and none of the
            # other seven.
            assert len(paydays) == 10
            assert [
                payday for payday in paydays if basis.line_applies_on(line, payday)
            ] == [date(2026, 1, 30), date(2026, 2, 13), date(2026, 2, 27)]
            # The Frequency cell words the span beside the cadence, the stop
            # in the recurrence package's own format and the start in the same.
            html = response.data.decode()
            assert "Every paycheck, from Jan 30, 2026, until Mar 01, 2026" in html

    def test_a_stop_before_the_derived_opening_is_refused_and_no_line_is_written(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A blank start with a stop before the opening payday is the write door's refusal, not a 500.

        The schema grades an inverted window only when the submission states
        BOTH dates; a blank start states none, so the pair meets the write
        door with the derived opening (2026-01-02) as its start and the stated
        2025-12-15 as its stop.  The create helper every template form uses
        words that refusal (an adversarial review of plan step salary:R18-c
        found the line door reaching the write door directly, where the
        refusal was a 500), and the flushed line is rolled back with it.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    **_line_form("Stopped", **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                    )),
                    "recurrence_end_mode": "on_date",
                    "end_date": "2025-12-15",
                },
                follow_redirects=True,
            )
            assert response.status_code == 200
            assert end_bound_before_start_message(
                date(2025, 12, 15), date(2026, 1, 2),
            ) in response.data.decode()
            assert db.session.query(PaycheckLine).filter_by(
                salary_profile_id=profile.id, name="Stopped",
            ).count() == 0

    def test_a_nominal_day_posted_beside_a_blank_start_is_dropped_with_the_derivation(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A crafted nominal day beside a blank start does not reach the spec beside the derived 1st.

        The schema's nominal-day rule is skipped when no start is stated, so
        ``repeats_on=31`` beside a blank start passes it; the derived
        2026-01-01 never clamped, and the pair would be refused by the spec as
        a broken invariant -- a 500.  The derivation drops the day with the
        blank it rode beside (the script disables that control; only a
        crafted POST carries it).
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    **_line_form("Crafted", **_cadence_payload(
                        RecurrenceUnitEnum.MONTH,
                        PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                        ceiling=None,
                    )),
                    "repeats_on": "31",
                },
                follow_redirects=True,
            )
            assert response.status_code == 200
            line = db.session.query(PaycheckLine).filter_by(
                salary_profile_id=profile.id, name="Crafted",
            ).one()
            assert (line.recurrence_rule.starts_on, line.recurrence_rule.nominal_day) == (
                date(2026, 1, 1), None,
            )

    def test_an_edit_that_clears_the_start_box_is_back_to_the_derived_default(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stated start cleared on an edit re-derives the unit's zero at the opening, in place."""
        with app.app_context():
            profile = _create_profile(seed_user)
            monthly = _cadence_payload(
                RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                ceiling=None,
            )
            auth_client.post(
                f"/salary/{profile.id}/lines",
                data={**_line_form("Spanned", **monthly), "starts_on": "2026-04-01"},
                follow_redirects=True,
            )
            line = db.session.query(PaycheckLine).filter_by(name="Spanned").one()
            rule_id = line.recurrence_rule.id
            assert line.recurrence_rule.starts_on == date(2026, 4, 1)
            auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data=_line_form("Spanned", **monthly),
                follow_redirects=True,
            )
            db.session.expire_all()
            line = db.session.get(PaycheckLine, line.id)
            assert line.recurrence_rule.id == rule_id
            assert (line.recurrence_rule.starts_on, line.recurrence_rule.nominal_day) == (
                date(2026, 1, 1), None,
            )

    def test_an_every_paycheck_line_gaining_an_end_on_edit_gains_a_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A rule-less every-paycheck line given a stop on its edit form is a rule from the opening."""
        with app.app_context():
            profile = _create_profile(seed_user)
            every = _cadence_payload(
                RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
            )
            auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form("Ending", **every), follow_redirects=True,
            )
            line = db.session.query(PaycheckLine).filter_by(name="Ending").one()
            assert line.recurrence_rule is None
            response = auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data={
                    **_line_form("Ending", **every),
                    "recurrence_end_mode": "on_date",
                    "end_date": "2026-03-01",
                },
                follow_redirects=True,
            )
            assert response.status_code == 200
            db.session.expire_all()
            line = db.session.get(PaycheckLine, line.id)
            rule = line.recurrence_rule
            assert rule is not None
            assert (rule.starts_on, rule.end_date, rule.max_occurrences, rule.max_per_month) == (
                date(2026, 1, 2), date(2026, 3, 1), None, None,
            )
            calendar = calendar_for(seed_user["user"].id)
            basis = PayrollBasis(profile, calendar)
            assert [
                period.start_date for period in calendar.saved()
                if basis.line_applies_on(line, period.start_date)
            ] == [
                date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
                date(2026, 2, 13), date(2026, 2, 27),
            ]
            # From the opening is the derived default, worded as nothing.
            assert "Every paycheck, until Mar 01, 2026" in response.data.decode()

    def test_a_stated_start_on_the_opening_payday_is_a_span_and_keeps_a_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Typing the opening payday into "Starts on" is a stated start, so the line keeps a rule.

        A blank box and a typed 2026-01-02 derive the same date, and a first
        draft canonicalised both to NO rule; they are not one spelling below
        a stated ``history_opens_on``, where a rule-less line prices on the
        replayed backdated paydays and a rule from the opening does not (an
        adversarial review of plan step salary:R18-c).  A typed date is the
        user's and is stored as a rule; what a blank means there is the
        developer's question, reported with this leaf.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    **_line_form("Typed", **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                    )),
                    "starts_on": "2026-01-02",
                },
                follow_redirects=True,
            )
            line = db.session.query(PaycheckLine).filter_by(name="Typed").one()
            rule = line.recurrence_rule
            assert rule is not None, "a typed start was read as the blank box"
            assert (rule.starts_on, rule.end_date, rule.max_occurrences) == (
                date(2026, 1, 2), None, None,
            )

    def test_an_edit_with_no_bound_keys_keeps_a_bounded_lines_stop(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A payload with no bound keys leaves the stored stop standing, so the rule is kept.

        The update door reads an ABSENT bound as "the form said nothing about
        it" (a disabled control, a crafted POST) and leaves the stored bound
        alone; the every-paycheck canonicalisation must read the same stored
        bound, or a bounded every-paycheck line re-saved without the keys
        would be canonicalised to NO rule and lose its stop (an adversarial
        review of plan step salary:R18-c).  The blank start box re-derives
        the opening, as it does on any edit.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            every = _cadence_payload(
                RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
            )
            auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    **_line_form("Bounded", **every),
                    "starts_on": "2026-02-01",
                    "recurrence_end_mode": "on_date",
                    "end_date": "2026-03-01",
                },
                follow_redirects=True,
            )
            line = db.session.query(PaycheckLine).filter_by(name="Bounded").one()
            rule_id = line.recurrence_rule.id
            assert line.recurrence_rule.starts_on == date(2026, 1, 30)
            payload = {**_line_form("Bounded", amount="110.00", **every)}
            for key in ("recurrence_end_mode", "end_date", "max_occurrences"):
                del payload[key]
            auth_client.post(
                f"/salary/lines/{line.id}/edit", data=payload, follow_redirects=True,
            )
            db.session.expire_all()
            line = db.session.get(PaycheckLine, line.id)
            assert line.amount == Decimal("110.00")
            assert line.recurrence_rule is not None, "the stored stop was read as no span"
            assert line.recurrence_rule.id == rule_id
            assert (line.recurrence_rule.starts_on, line.recurrence_rule.end_date) == (
                date(2026, 1, 2), date(2026, 3, 1),
            )

    def test_a_refused_cadence_is_heard_in_its_own_words(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A unit with no interval is refused with the schema's sentence, not the generic prompt."""
        with app.app_context():
            profile = _create_profile(seed_user)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Half",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                        interval="", ceiling="",
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            assert b"Say how often this repeats." in response.data
            assert db.session.query(PaycheckLine).filter_by(
                salary_profile_id=profile.id, name="Half",
            ).count() == 0

    def test_a_ceiling_beside_a_monthly_cadence_is_refused_not_stored(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The pair the write door refuses is a field error here, and nothing is written."""
        with app.app_context():
            profile = _create_profile(seed_user)
            line = _a_line(profile, "Health", 24)
            before = _authored_columns(line.recurrence_rule)
            response = auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data=_line_form(
                    "Health",
                    **_cadence_payload(
                        RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.CONTAINING_DATE,
                        ceiling="2",
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            assert b"a per-month limit has nothing to limit" in response.data
            db.session.expire_all()
            assert _authored_columns(
                db.session.get(PaycheckLine, line.id).recurrence_rule,
            ) == before

    def test_a_line_added_by_the_form_regenerates_the_profiles_paychecks(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The line AND its rule are on the profile when the regeneration prices it.

        The route flushes the line, authors the rule and THEN regenerates,
        and the regeneration's one stored figure -- the paycheck template's
        amount, re-stated to the CURRENT paycheck's net (today is frozen at
        2026-03-20 in this module; the paycheck of Mar 13) -- is what grades
        it: a regeneration that ran before the rule existed, or that priced
        a ``profile.lines`` collection the new line never joined, would
        have stated the net WITHOUT the line.  The second is the shape this
        test measured on the first cut: pricing the paycheck BEFORE the add
        loads the collection into the session the request shares, and a
        line constructed by FK id never joins it -- the route constructs it
        through the relationship now.  The rows themselves carry no figure
        (balance:X-au-e), so the template's amount is the observable.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            ctx = BalanceContext.build(seed_user["user"].id)
            current = ctx.calendar().period_containing(date(2026, 3, 20))
            net_without = ctx.paychecks().for_profile(profile).at(current).earnings.net_pay

            auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Health",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                        ceiling="2",
                    ),
                ),
                follow_redirects=True,
            )
            db.session.expire_all()
            profile = db.session.get(SalaryProfile, profile.id)
            ctx = BalanceContext.build(seed_user["user"].id)
            priced = ctx.paychecks().for_profile(profile).at(current)
            # Mar 13 is March's first payday: taken under the ceiling of 2.
            assert priced.deductions.total_pre_tax == Decimal("100.00")
            assert priced.earnings.net_pay < net_without
            assert profile.template.default_amount == priced.earnings.net_pay, (
                "the regeneration stated a net that did not carry the line: "
                "it ran before the rule was authored"
            )

    def test_every_twelve_months_survives_an_amount_only_edit_unmoved(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A whole number of years spelled in months is stored as YEAR, and re-saves agree.

        The adversarial review of this step's first cut traced a money
        mover: on a MARCH opening, 'months, every 12, first paycheck on or
        after' derived March 1st from the stated unit, the door stored it as
        (1, YEAR) (R-R17), the edit form read YEAR back, and an amount-only
        save then derived January 1st -- the rule re-phased by an edit that
        never touched it.  The seeded January opening cannot see it (the
        month's and the year's zeros coincide), so the calendar is rebuilt
        to open in March here.  Fixed by deriving the zero off the STORED
        unit; this pins every authored column across the round trip.
        """
        with app.app_context():
            rebuild_calendar(seed_user["user"].id, date(2026, 3, 13), 10, 14)
            profile = _create_profile(seed_user)
            auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Dues",
                    **_cadence_payload(
                        RecurrenceUnitEnum.MONTH,
                        PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                        interval="12", ceiling=None,
                    ),
                ),
                follow_redirects=True,
            )
            line = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="Dues").one()
            )
            rule = line.recurrence_rule
            assert rule.unit_id == ref_cache.recurrence_unit_id(RecurrenceUnitEnum.YEAR)
            assert rule.interval_n == 1
            assert rule.starts_on == date(2026, 1, 1), (
                "the zero must be the STORED year's, not the stated month's March 1st"
            )
            before = _authored_columns(rule)
            rule_id = rule.id
            # What the edit form renders for this row -- and therefore posts
            # back on an amount-only save -- is the stored cadence: YEAR / 1.
            html = auth_client.get(f"/salary/{profile.id}/edit").data.decode()
            prefill = _prefill_attributes(html, line.id)
            assert prefill["unit-id"] == str(
                ref_cache.recurrence_unit_id(RecurrenceUnitEnum.YEAR),
            )
            assert prefill["interval"] == "1"
            auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data=_line_form(
                    "Dues", amount="120.00",
                    **_cadence_payload(
                        RecurrenceUnitEnum.YEAR,
                        PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                        interval=prefill["interval"], ceiling=None,
                    ),
                ),
                follow_redirects=True,
            )
            db.session.expire_all()
            line = db.session.get(PaycheckLine, line.id)
            assert line.amount == Decimal("120.00")
            assert line.recurrence_rule.id == rule_id
            assert _authored_columns(line.recurrence_rule) == before
