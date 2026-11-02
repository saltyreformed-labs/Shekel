"""Salary route tests: the deduction form's cadence: rendering, the payload, adding and editing a
line's cadence, and the edit page's prefill (plan step salary:R15-c).

Split from ``tests/test_routes/test_salary.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from html import unescape
import json
import re
from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import CalcMethod, PaycheckLineKind
from app.schemas.validation import PaycheckLineCreateSchema
from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.pay_calendar import calendar_for
from app.services.payroll_basis import PayrollBasis

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
    """Render, payload, add, edit and prefill; members on _TestDeductionCadenceFormMembers."""

    def test_the_form_renders_the_cadence_and_span_controls_and_nothing_else_of_the_partial(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The four cadence controls, the span rows and their script; no due day or preview.

        Until plan step salary:R18-c this pinned the ABSENCE of the start and
        the end bound (rulings R-SAL30 / R-SAL31); ruling **R-SAL38** (2)
        amended both, so the same pin now holds them PRESENT -- the "Starts
        on" box BLANK, since blank is the opening payday -- and keeps holding
        the due day and the preview absent.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            html = response.data.decode()
            form = re.search(
                r'<form method="POST" id="line-form".*?</form>', html, re.S,
            ).group(0)
            for present in (
                'id="cadence-controls"', 'name="recurrence_unit"',
                'name="interval_n"', 'name="max_per_month"',
                'name="recurrence_placement"', "data-cadence-options=",
                'id="recurrence-fields"', 'name="starts_on"', 'name="nominal_day"',
                'name="recurrence_end_mode"', 'name="end_date"',
                'name="max_occurrences"', "Leave blank to start from your first paycheck",
            ):
                assert present in form, present
            starts_on = re.search(r'<input type="date" id="starts_on"[^>]*>', form).group(0)
            assert 'value=""' in starts_on, "the ADD form's start opens blank"
            for absent in (
                'id="recurrence-preview"', 'name="deductions_per_year"',
            ):
                assert absent not in form, absent
            assert "js/recurrence_form.js" in html

    def test_the_payload_is_what_the_form_renders(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The test payload's keys ARE the rendered form's control names, both ways.

        A hand-picked payload can leave a rendered control untested (a
        primary arm dead in a browser and green in the suite); pinning the
        helper to the template is what makes every route case below a case
        about what a browser posts.  The CSRF token is the one control the
        test client does not carry.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            html = auth_client.get(f"/salary/{profile.id}/edit").data.decode()
            form = re.search(
                r'<form method="POST" id="line-form".*?</form>', html, re.S,
            ).group(0)
            # ``\sname=`` and not ``name=``: the calc-method options carry a
            # ``data-name`` attribute that is not a control.
            rendered = set(re.findall(r'\sname="([a-z_]+)"', form)) - {"csrf_token"}
            # The inflation checkbox posts only when checked, so the helper
            # omits it; every other control posts, empty or not.
            posted = set(_line_form(
                "x",
                **_cadence_payload(
                    RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                ),
            )) | {"inflation_enabled"}
            assert posted == rendered

    def test_add_paychecks_at_most_two_a_month_is_the_migrated_twenty_four(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Column for column the rule ``542c61e48ee8`` writes for a 24, and priced as one."""
        with app.app_context():
            profile = _create_profile(seed_user)
            migrated = _a_line(profile, "Migrated", 24)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Authored",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD,
                        PeriodPlacementEnum.CONTAINING_DATE,
                        ceiling="2",
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            authored = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="Authored").one()
            )
            assert authored.recurrence_rule is not None
            assert _authored_columns(authored.recurrence_rule) == (
                _authored_columns(migrated.recurrence_rule)
            )
            assert authored.recurrence_rule.starts_on == date(2026, 1, 2)
            # The engine reads the form's rule as it reads the migrated one:
            # January 2026 holds three seeded paydays (2, 16, 30) and the
            # third is skipped; February's two are taken.
            basis = PayrollBasis(profile, calendar_for(seed_user["user"].id))
            for payday, taken in (
                (date(2026, 1, 2), True), (date(2026, 1, 16), True),
                (date(2026, 1, 30), False), (date(2026, 2, 13), True),
                (date(2026, 2, 27), True),
            ):
                assert basis.line_applies_on(authored, payday) is taken, payday
                assert basis.line_applies_on(migrated, payday) is taken, payday

    def test_add_monthly_first_paycheck_is_the_migrated_twelve(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Starts on the 1st of the opening month (R-SAL36), not on the opening payday."""
        with app.app_context():
            profile = _create_profile(seed_user)
            migrated = _a_line(profile, "Migrated", 12)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Authored",
                    **_cadence_payload(
                        RecurrenceUnitEnum.MONTH,
                        PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                        ceiling=None,
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            authored = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="Authored").one()
            )
            assert _authored_columns(authored.recurrence_rule) == (
                _authored_columns(migrated.recurrence_rule)
            )
            assert authored.recurrence_rule.starts_on == date(2026, 1, 1)
            basis = PayrollBasis(profile, calendar_for(seed_user["user"].id))
            for payday, taken in (
                (date(2026, 1, 2), True), (date(2026, 1, 16), False),
                (date(2026, 1, 30), False), (date(2026, 2, 13), True),
                (date(2026, 2, 27), False), (date(2026, 3, 13), True),
            ):
                assert basis.line_applies_on(authored, payday) is taken, payday

    def test_add_yearly_starts_on_january_first_of_the_opening_year(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The YEAR unit is offered (R-SAL37) and its zero is January 1st (R-SAL36)."""
        with app.app_context():
            profile = _create_profile(seed_user)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Dues",
                    **_cadence_payload(
                        RecurrenceUnitEnum.YEAR,
                        PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                        ceiling=None,
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            authored = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="Dues").one()
            )
            rule = authored.recurrence_rule
            assert rule is not None
            assert rule.unit_id == ref_cache.recurrence_unit_id(RecurrenceUnitEnum.YEAR)
            assert rule.interval_n == 1
            assert rule.starts_on == date(2026, 1, 1)
            assert rule.max_per_month is None
            basis = PayrollBasis(profile, calendar_for(seed_user["user"].id))
            assert basis.line_applies_on(authored, date(2026, 1, 2)) is True
            assert basis.line_applies_on(authored, date(2026, 1, 16)) is False

    def test_add_does_not_repeat_authors_no_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The empty unit is every paycheck (R-SAL3): no rule, whatever the other boxes say."""
        with app.app_context():
            profile = _create_profile(seed_user)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Every",
                    **_cadence_payload(
                        None, PeriodPlacementEnum.CONTAINING_DATE,
                        interval="3", ceiling="2",
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            added = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="Every").one()
            )
            assert added.recurrence_rule is None
            assert db.session.query(RecurrenceRule).filter_by(
                paycheck_line_id=added.id,
            ).count() == 0

    def test_add_every_paycheck_with_no_ceiling_is_no_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """'Paychecks, every 1' with an empty ceiling canonicalises to no rule (R-SAL29)."""
        with app.app_context():
            profile = _create_profile(seed_user)
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Every",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                        ceiling="",
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            added = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="Every").one()
            )
            assert added.recurrence_rule is None

    def test_add_every_other_paycheck_is_a_rule_of_its_own(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The canonical spelling is exact: interval 2 stores a rule."""
        with app.app_context():
            profile = _create_profile(seed_user)
            auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Alternate",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                        interval="2", ceiling="",
                    ),
                ),
                follow_redirects=True,
            )
            added = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="Alternate").one()
            )
            assert added.recurrence_rule is not None
            assert added.recurrence_rule.interval_n == 2
            assert added.recurrence_rule.max_per_month is None
            basis = PayrollBasis(profile, calendar_for(seed_user["user"].id))
            assert basis.line_applies_on(added, date(2026, 1, 2)) is True
            assert basis.line_applies_on(added, date(2026, 1, 16)) is False
            assert basis.line_applies_on(added, date(2026, 1, 30)) is True

    def test_edit_twenty_four_to_twelve_reauthors_the_same_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The rule keeps its id; every authored column becomes the 12 shape's."""
        with app.app_context():
            profile = _create_profile(seed_user)
            line = _a_line(profile, "Health", 24)
            twelve = _a_line(profile, "Reference", 12)
            rule_id = line.recurrence_rule.id
            response = auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data=_line_form(
                    "Health",
                    **_cadence_payload(
                        RecurrenceUnitEnum.MONTH,
                        PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                        ceiling=None,
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            db.session.expire_all()
            line = db.session.get(PaycheckLine, line.id)
            assert line.recurrence_rule.id == rule_id
            assert _authored_columns(line.recurrence_rule) == (
                _authored_columns(twelve.recurrence_rule)
            )

    def test_edit_twelve_to_twenty_four_reauthors_the_same_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The other direction: the derived start moves back onto the opening payday."""
        with app.app_context():
            profile = _create_profile(seed_user)
            line = _a_line(profile, "Transit", 12)
            twenty_four = _a_line(profile, "Reference", 24)
            rule_id = line.recurrence_rule.id
            auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data=_line_form(
                    "Transit",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                        ceiling="2",
                    ),
                ),
                follow_redirects=True,
            )
            db.session.expire_all()
            line = db.session.get(PaycheckLine, line.id)
            assert line.recurrence_rule.id == rule_id
            assert _authored_columns(line.recurrence_rule) == (
                _authored_columns(twenty_four.recurrence_rule)
            )

    def test_edit_to_does_not_repeat_deletes_the_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An empty unit on an edit is a stated clear: the row goes, the line stays."""
        with app.app_context():
            profile = _create_profile(seed_user)
            line = _a_line(profile, "Health", 24)
            rule_id = line.recurrence_rule.id
            response = auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data=_line_form(
                    "Health",
                    **_cadence_payload(None, PeriodPlacementEnum.CONTAINING_DATE),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            db.session.expire_all()
            assert db.session.get(RecurrenceRule, rule_id) is None
            assert db.session.get(PaycheckLine, line.id).recurrence_rule is None

    def test_edit_to_every_paycheck_with_no_ceiling_deletes_the_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Clearing the ceiling on a 24 line is the every-paycheck spelling: no rule (R-SAL29)."""
        with app.app_context():
            profile = _create_profile(seed_user)
            line = _a_line(profile, "Health", 24)
            rule_id = line.recurrence_rule.id
            auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data=_line_form(
                    "Health",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                        ceiling="",
                    ),
                ),
                follow_redirects=True,
            )
            db.session.expire_all()
            assert db.session.get(RecurrenceRule, rule_id) is None
            assert db.session.get(PaycheckLine, line.id).recurrence_rule is None

    def test_edit_with_the_ceiling_key_absent_keeps_the_stored_ceiling(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A crafted 'Paychecks, every 1' with NO ceiling key is not read as every paycheck.

        The form always posts the key beside a paycheck unit (the control is
        enabled there), so absence is a crafted POST; it is read the way the
        seam's update door reads every absent key -- leave the stored value
        alone -- rather than as the cleared box that would delete the rule.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            line = _a_line(profile, "Health", 24)
            rule_id = line.recurrence_rule.id
            auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data=_line_form(
                    "Health",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD, PeriodPlacementEnum.CONTAINING_DATE,
                        ceiling=None,
                    ),
                ),
                follow_redirects=True,
            )
            db.session.expire_all()
            rule = db.session.get(RecurrenceRule, rule_id)
            assert rule is not None, "the absent key was read as a cleared ceiling"
            assert rule.max_per_month == 2

    def test_edit_with_no_cadence_keys_leaves_the_rule_untouched(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A submission that says nothing about the cadence is a partial update of the line."""
        with app.app_context():
            profile = _create_profile(seed_user)
            line = _a_line(profile, "Health", 24)
            before = _authored_columns(line.recurrence_rule)
            rule_id = line.recurrence_rule.id
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
            auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data={
                    "name": "Health",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "125.00",
                },
                follow_redirects=True,
            )
            db.session.expire_all()
            line = db.session.get(PaycheckLine, line.id)
            assert line.amount == Decimal("125.00")
            assert line.recurrence_rule.id == rule_id
            assert _authored_columns(line.recurrence_rule) == before

    def test_the_edit_page_emits_each_lines_cadence_for_the_prefill(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Nine data attributes per row: the migrated shapes' values, empties for no rule.

        The migrated rules start on their unit's zero at the opening and never
        end, so their span attributes carry that start (a 24's the opening
        payday, a 12's the 1st of its month), no nominal day, the bound's
        ``never`` and empty values (plan step salary:R18-c).  A line with no
        rule carries a BLANK start -- not today, the template forms' default,
        which a re-save would read as a stated start -- and ``never``.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            twenty_four = _a_line(profile, "Health", 24)
            twelve = _a_line(profile, "Transit", 12)
            every = _a_line(profile, "401k", 26)
            html = auth_client.get(f"/salary/{profile.id}/edit").data.decode()
            never_ending = {
                "nominal-day": "", "end-mode": "never", "end-date": "",
                "max-occurrences": "",
            }
            period = str(ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD))
            month = str(ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH))
            containing = str(
                ref_cache.period_placement_id(PeriodPlacementEnum.CONTAINING_DATE),
            )
            first_on_or_after = str(
                ref_cache.period_placement_id(
                    PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                ),
            )
            assert _prefill_attributes(html, twenty_four.id) == {
                "unit-id": period, "interval": "1",
                "placement-id": containing, "max-per-month": "2",
                "starts-on": "2026-01-02", **never_ending,
            }
            assert _prefill_attributes(html, twelve.id) == {
                "unit-id": month, "interval": "1",
                "placement-id": first_on_or_after, "max-per-month": "",
                "starts-on": "2026-01-01", **never_ending,
            }
            assert _prefill_attributes(html, every.id) == {
                "unit-id": "", "interval": "", "placement-id": "", "max-per-month": "",
                "starts-on": "", **never_ending,
            }


class TestWeeksStayOffTheSalaryPage:
    """Ruling plan#2591: a payroll line repeats by paychecks, months or years.

    A line's rule picks WHICH paychecks take it -- the paycheck engine asks one
    yes/no per paycheck -- so a weekly line would be taken once per paycheck
    however many weeks it held: $25 a week on a biweekly schedule is $50 a
    paycheck, and the engine would take $25.  A weekly item is entered as its
    per-paycheck amount instead.  These cases hold the salary page's offer and
    its door to that set while the template forms offer the week unit (plan
    step R8-b).

    **The route cases assert the line schema's own words in the response**
    since plan step R8-b: until then the shared cadence refusal fired for the
    week unit beside this one and the flash showed the first, so the stored
    state was all a route case could grade.  The shared refusal no longer
    fires for weeks, so this one is the only message the flash can carry.
    """

    #: What the line schema says when a weekly cadence is posted.
    WEEKS_REFUSED = (
        "A payroll line repeats by paychecks, months or years, not weeks."
    )

    def test_the_repeats_control_offers_paychecks_months_and_years_only(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The unit select and the script's offer set both omit the week unit."""
        with app.app_context():
            profile = _create_profile(seed_user)
            html = auth_client.get(f"/salary/{profile.id}/edit").data.decode()
            form = re.search(
                r'<form method="POST" id="line-form".*?</form>', html, re.S,
            ).group(0)
            unit_select = re.search(
                r'<select id="recurrence_unit".*?</select>', form, re.S,
            ).group(0)
            offered = set(re.findall(r'<option value="(\d+)"', unit_select))
            assert offered == {
                str(ref_cache.recurrence_unit_id(unit))
                for unit in (
                    RecurrenceUnitEnum.PERIOD,
                    RecurrenceUnitEnum.MONTH,
                    RecurrenceUnitEnum.YEAR,
                )
            }
            wire = json.loads(unescape(
                re.search(r'data-cadence-options="([^"]*)"', form).group(1),
            ))
            assert {option["unit_id"] for option in wire} == {
                int(unit_id) for unit_id in offered
            }

    def test_add_a_weekly_line_is_refused_and_writes_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A crafted weekly POST is the schema's field error, with no line or rule written."""
        with app.app_context():
            profile = _create_profile(seed_user)
            rules_before = db.session.query(RecurrenceRule).count()
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data=_line_form(
                    "Union dues", amount="25.00",
                    **_cadence_payload(
                        RecurrenceUnitEnum.WEEK,
                        PeriodPlacementEnum.CONTAINING_DATE,
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            assert self.WEEKS_REFUSED in response.data.decode()
            assert db.session.query(PaycheckLine).filter_by(
                salary_profile_id=profile.id, name="Union dues",
            ).count() == 0
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_edit_a_line_to_weekly_is_refused_and_keeps_its_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Moving a stored 24-a-year line onto weeks is refused; every authored column stays."""
        with app.app_context():
            profile = _create_profile(seed_user)
            line = _a_line(profile, "Health", 24)
            rule_id = line.recurrence_rule.id
            before = _authored_columns(line.recurrence_rule)
            response = auth_client.post(
                f"/salary/lines/{line.id}/edit",
                data=_line_form(
                    "Health",
                    **_cadence_payload(
                        RecurrenceUnitEnum.WEEK,
                        PeriodPlacementEnum.CONTAINING_DATE,
                    ),
                ),
                follow_redirects=True,
            )
            assert response.status_code == 200
            assert self.WEEKS_REFUSED in response.data.decode()
            db.session.expire_all()
            line = db.session.get(PaycheckLine, line.id)
            assert line.recurrence_rule.id == rule_id
            assert _authored_columns(line.recurrence_rule) == before

    def test_the_line_schema_refuses_the_week_unit_in_its_own_words(
        self, app, seed_user, seed_periods,
    ):
        """The refusal names the units a line repeats by and what to do instead.

        Graded on the schema as well as the flash: the route cases above show
        this message reaches the user, and this one pins it as the field error
        on ``recurrence_unit``, the control the user changes.
        """
        with app.app_context():
            payload = _line_form(
                "Union dues", amount="25.00",
                **_cadence_payload(
                    RecurrenceUnitEnum.WEEK, PeriodPlacementEnum.CONTAINING_DATE,
                ),
            )
            with pytest.raises(ValidationError) as refused:
                PaycheckLineCreateSchema().load(payload)
            unit_messages = refused.value.normalized_messages()["recurrence_unit"]
            assert any(
                message.startswith(self.WEEKS_REFUSED) for message in unit_messages
            ), unit_messages
