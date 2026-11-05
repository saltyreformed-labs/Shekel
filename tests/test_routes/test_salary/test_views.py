"""Salary route tests: the deduction frequency display, the breakdown, its redirect stubs, and the
projection summary.

Split from ``tests/test_routes/test_salary.py`` by ``tools/split`` (ruling R-BAL301).
"""

import re
from decimal import Decimal

from app.extensions import db
from app.models.salary_raise import SalaryRaise
from app.models.paycheck_line import PaycheckLine
from app.models.pay_period import PayPeriod
from app.models.ref import CalcMethod, PaycheckLineKind, RaiseType
from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum

from tests._test_helpers import make_line_cadence_rule, NO_TAX_LAW

from ._helpers import (
    _create_profile,
    _create_other_user_profile,
    _cadence_payload,
    _prefill_attributes,
    _capture_render,
)


# ── Deduction Frequency Display ──────────────────────────────────


class TestDeductionFrequencyDisplay:
    """The deductions table's Frequency cell states each line's cadence.

    Since plan step **salary:R15-b** (rulings **R-SAL3**, **R-SAL32**) a
    line's frequency is a recurrence rule on the row, or none for *every
    paycheck*, and the cell reads it through the recurrence package's ONE
    phrase producer (``app.services.payroll_line_cadence``): the three
    hand-worded labels the cell carried ("26x/yr (every paycheck)", "24x/yr
    (skip 3rd paycheck)", "12x/yr (monthly)") went with the column.  The
    cases seed the two migrated shapes through the shared builder the
    migration's SQL mirrors.
    """

    @staticmethod
    def _cell(html, deduction_id):
        """The rendered Frequency cell for one line."""
        match = re.search(
            r'<td data-line-cadence="%d">(.*?)</td>' % deduction_id, html, re.S,
        )
        assert match, f"no Frequency cell for deduction {deduction_id}"
        return match.group(1).strip()

    @staticmethod
    def _seed(profile, name, per_year):
        """A flat pre-tax line, with the migrated rule for a 24 / 12."""
        pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
        flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
        deduction = PaycheckLine(
            salary_profile_id=profile.id,
            paycheck_line_kind_id=pre_tax.id,
            calc_method_id=flat_method.id,
            name=name,
            amount=Decimal("100.00"),
        )
        db.session.add(deduction)
        db.session.flush()
        if per_year != 26:
            make_line_cadence_rule(db.session, deduction, per_year)
        db.session.commit()
        return deduction.id

    def test_a_line_with_no_rule_reads_every_paycheck(
        self, app, auth_client, seed_user, seed_periods
    ):
        """No rule is R-SAL3's NULL: every paycheck, worded by the one producer."""
        with app.app_context():
            profile = _create_profile(seed_user)
            ded_id = self._seed(profile, "401k", 26)
            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            assert self._cell(response.data.decode(), ded_id) == "Every paycheck"

    def test_the_twenty_four_shape_reads_at_most_two_a_month(
        self, app, auth_client, seed_user, seed_periods
    ):
        """The migrated 24 is every paycheck with a ceiling of 2, and says so."""
        with app.app_context():
            profile = _create_profile(seed_user)
            ded_id = self._seed(profile, "Health Insurance", 24)
            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            assert self._cell(response.data.decode(), ded_id) == (
                "Every paycheck (at most 2 a month)"
            )

    def test_the_twelve_shape_reads_monthly_first_paycheck(
        self, app, auth_client, seed_user, seed_periods
    ):
        """The migrated 12 is monthly on the first paycheck, and says so."""
        with app.app_context():
            profile = _create_profile(seed_user)
            ded_id = self._seed(profile, "Transit", 12)
            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            assert self._cell(response.data.decode(), ded_id) == "Monthly (first paycheck)"

    def test_every_line_reads_its_own_cadence(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Three lines, three phrases, each on its own cell."""
        with app.app_context():
            profile = _create_profile(seed_user)
            ids = {
                per_year: self._seed(profile, f"D{per_year}", per_year)
                for per_year in (26, 24, 12)
            }
            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            html = response.data.decode()
            assert self._cell(html, ids[26]) == "Every paycheck"
            assert self._cell(html, ids[24]) == "Every paycheck (at most 2 a month)"
            assert self._cell(html, ids[12]) == "Monthly (first paycheck)"
            for stale in ("26x/yr", "24x/yr", "12x/yr", "skip 3rd paycheck"):
                assert stale not in html, f"the old label {stale!r} survived"

    def test_the_page_derives_no_calendar_for_lines_without_a_rule(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A rule is described against the owner's calendar; no rule, no calendar of its own.

        Since plan step salary:X-av-3a the edit page prices the pay list, so
        it derives the owner's calendar ONCE for every profile
        (``edit_profile``'s ``PayrollBasis``); the cadence reader must add no
        derivation of its own for a line that carries no rule.  Counted at
        the cursor: a calendar is derived by loading the owner's
        ``budget.pay_periods`` rows (their ``start_date``s); the page's own
        ``SELECT EXISTS`` over that table is not a derivation and is let
        through.  Exactly one, so a counter that never fired reads 0 and
        fails here.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy import event

        with app.app_context():
            profile = _create_profile(seed_user)
            self._seed(profile, "401k", 26)

            def calendar_loads():
                statements = []

                def _count(*args):  # pylint: disable=unused-argument
                    statements.append(args[2])

                event.listen(db.engine, "before_cursor_execute", _count)
                try:
                    response = auth_client.get(f"/salary/{profile.id}/edit")
                finally:
                    event.remove(db.engine, "before_cursor_execute", _count)
                assert response.status_code == 200
                return [s for s in statements if "pay_periods.start_date" in s]

            loads = calendar_loads()
            assert len(loads) == 1, (
                f"the edit page derived {len(loads)} calendars for a profile "
                "whose one line has no rule; the pay list's is the one"
            )
            # The positive control: give one line a rule and the page still
            # derives ONE calendar -- the pass's, which the rule is described
            # against (the lines section is handed the pass's memo, plan step
            # salary:X-av-3a; it derived a second of its own until then,
            # measured at 2).  It also proves the counter fires.
            self._seed(profile, "Health Insurance", 24)
            assert len(calendar_loads()) == 1, (
                "a line with a rule made the edit page derive the owner's "
                "calendar more than once"
            )

    def test_deduction_frequency_column_header(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Deduction table header reads 'Frequency', not 'Per Year'."""
        with app.app_context():
            profile = _create_profile(seed_user)
            self._seed(profile, "401k", 26)
            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            html = response.data.decode()
            assert "<th>Frequency</th>" in html
            assert "<th>Per Year</th>" not in html

    def test_the_htmx_add_response_reads_the_new_lines_cadence(
        self, app, auth_client, seed_user, seed_periods
    ):
        """A line added with a cadence carries its rule, and the fragment says so.

        The form's cadence controls landed at plan step salary:R15-c (one PR
        with R15-b, ruling R-SAL35): "Paychecks, every 1, at most 2 a month"
        is the migrated 24 shape, and the swapped-in fragment reads it the
        way the full page does -- and carries the prefill the next edit of
        the line starts from.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    "name": "401k",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "200.00",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD,
                        PeriodPlacementEnum.CONTAINING_DATE,
                        ceiling="2",
                    ),
                },
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            added = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="401k").one()
            )
            assert added.recurrence_rule is not None
            html = response.data.decode()
            assert self._cell(html, added.id) == "Every paycheck (at most 2 a month)"
            assert 'name="deductions_per_year"' not in html
            assert _prefill_attributes(html, added.id) == {
                "unit-id": str(ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD)),
                "interval": "1",
                "placement-id": str(
                    ref_cache.period_placement_id(PeriodPlacementEnum.CONTAINING_DATE),
                ),
                "max-per-month": "2",
                "starts-on": "2026-01-02", "repeats-on": "",
                "end-mode": "never", "end-date": "", "max-occurrences": "",
            }

    def test_the_htmx_edit_response_keeps_the_lines_rule(
        self, app, auth_client, seed_user, seed_periods
    ):
        """An edit of a migrated 24 line re-renders its cadence unchanged."""
        with app.app_context():
            profile = _create_profile(seed_user)
            ded_id = self._seed(profile, "Health Insurance", 24)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
            response = auth_client.post(
                f"/salary/lines/{ded_id}/edit",
                data={
                    "name": "Health Insurance",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "175.00",
                },
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            assert self._cell(response.data.decode(), ded_id) == (
                "Every paycheck (at most 2 a month)"
            )
            db.session.expire_all()
            assert db.session.get(PaycheckLine, ded_id).amount == Decimal("175.00")


# ── Breakdown & Projection ────────────────────────────────────────


class TestBreakdown:
    """Tests for the retired-breakdown redirect stubs and projection view."""

    def test_breakdown_redirects_to_cockpit(self, app, auth_client, seed_user, seed_periods):
        """GET /salary/<id>/breakdown/<period_id> 302s into the cockpit.

        The per-period breakdown page was folded into the cockpit; the URL
        is now an ownership-checked redirect stub carrying the focus params.
        """
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(
                f"/salary/{profile.id}/breakdown/{seed_periods[0].id}"
            )

            assert response.status_code == 302
            assert f"profile={profile.id}" in response.location
            assert f"period={seed_periods[0].id}" in response.location

    def test_breakdown_current_redirects(self, app, auth_client, seed_user, seed_periods):
        """GET /salary/<id>/breakdown 302s into the cockpit at the current period."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/breakdown")

            assert response.status_code == 302
            assert f"profile={profile.id}" in response.location
            assert "period=" in response.location

    def test_projection_renders(self, app, auth_client, seed_user, seed_periods):
        """GET /salary/<id>/projection renders the projection ledger."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/projection")

            assert response.status_code == 200
            assert b"Projection ledger" in response.data
            assert b"Day Job" in response.data
            assert b"Annual Salary" in response.data

    def test_projection_collapses_raise_badge_to_run_start(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The ledger badges a raise only on the paycheck where it takes effect.

        A MERIT raise effective March 2026 badges BOTH March paychecks
        (seed_periods[5] on 2026-03-13 and [6] on 2026-03-27; the calculator
        marks every period of the raise month).  The projection ledger shows
        the sal-badge-raise exactly once -- on the run start (period 5) -- not
        again on the continuation (period 6) (P-SA1, projection surface).
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_month=3,
                effective_year=2026,
                percentage=Decimal("0.0250"),
            ))
            db.session.commit()

            response = auth_client.get(f"/salary/{profile.id}/projection")
            assert response.status_code == 200
            html = response.data.decode()
            # Exactly one raise badge: the run start, not every March paycheck.
            assert html.count("sal-badge-raise") == 1
            assert "Merit +2.5%" in html

    def test_breakdown_no_current_period(self, app, auth_client, seed_user):
        """GET /salary/<id>/breakdown with no periods lands on the cockpit blocker.

        The stub redirects to the cockpit carrying only the profile; the
        cockpit then shows its generate-periods empty state.
        """
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(
                f"/salary/{profile.id}/breakdown",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"No pay periods found" in response.data
            assert b"Generate pay periods" in response.data

    def test_breakdown_other_users_profile_redirects(
        self, app, auth_client, seed_user, seed_periods
    ):
        """GET /salary/<id>/breakdown/<period_id> for another user's profile returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_profile()

            response = auth_client.get(
                f"/salary/{other['profile'].id}/breakdown/{seed_periods[0].id}",
                follow_redirects=True,
            )

            assert response.status_code == 404


class TestBreakdownRedirectStubs:
    """The retired /salary/<id>/breakdown[/<pid>] ownership-checked stubs."""

    def test_breakdown_period_location_params(
        self, app, auth_client, seed_user, seed_periods
    ):
        """The per-period stub's Location carries both profile and period."""
        with app.app_context():
            profile = _create_profile(seed_user)
            resp = auth_client.get(
                f"/salary/{profile.id}/breakdown/{seed_periods[3].id}"
            )
            assert resp.status_code == 302
            assert f"profile={profile.id}" in resp.location
            assert f"period={seed_periods[3].id}" in resp.location

    def test_breakdown_period_other_period_404(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Own profile but another user's period 404s BEFORE any redirect."""
        with app.app_context():
            profile = _create_profile(seed_user)
            other = _create_other_user_profile()
            other_period = (
                db.session.query(PayPeriod)
                .filter_by(user_id=other["user"].id)
                .first()
            )
            resp = auth_client.get(
                f"/salary/{profile.id}/breakdown/{other_period.id}"
            )
            assert resp.status_code == 404

    def test_breakdown_current_other_user_404(self, app, auth_client, seed_user):
        """The current-period stub 404s for another user's profile."""
        with app.app_context():
            other = _create_other_user_profile()
            resp = auth_client.get(f"/salary/{other['profile'].id}/breakdown")
            assert resp.status_code == 404


class TestProjectionSummary:
    """The projection route's summary framing context (P3 renders it)."""

    def test_projection_summary_values(self, app, auth_client, seed_user, seed_periods, tax_law):
        """yearly_nets sums the 10 no-tax periods; no future raise/third.

        Under the no-tax law each of the 10 seeded periods nets its gross,
        round_money(75000 / 26) = 2884.62, so the 2026 total is
        2884.62 * 10 = 28846.20.  There are no raises and (after today
        2026-03-20) no remaining third paycheck in the seeded window.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            profile = _create_profile(seed_user)

            response, ctx = _capture_render(
                app, auth_client, f"/salary/{profile.id}/projection",
                "salary/projection.html",
            )
            assert response.status_code == 200
            summary = ctx["projection_summary"]
            assert summary["yearly_nets"] == [(2026, Decimal("28846.20"))]
            assert summary["next_raise"] is None
            assert summary["next_third"] is None
