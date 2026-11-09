"""Salary route tests: button placement, the cockpit context, and the anatomy fragment.

Split from ``tests/test_routes/test_salary.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.salary_raise import SalaryRaise
from app.models.pay_period import PayPeriod
from app.models.ref import RaiseType

from tests._test_helpers import NO_TAX_LAW

from ._helpers import (
    _create_profile,
    _create_other_user_profile,
    _create_inactive_profile,
    _capture_render,
)


# ── Button Placement ──────────────────────────────────────────────


class TestButtonPlacement:
    """Tests for the edit form's navigation buttons (Fable 5 rebuild).

    The former salary LIST page (and its per-row action buttons) was
    removed; the restyled edit form carries "Open cockpit" / "Projection
    ledger" / "Tax rates" buttons on the submit row instead of the old
    "View Breakdown" / "View Projection" pair.  These tests pin the new
    contract: labels, targets (cockpit with the profile focused, the
    projection page, the honest direct settings link), and the
    create-form's hidden state.
    """

    def test_salary_form_edit_buttons_appear_before_deductions(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Edit-form nav buttons appear before the Deductions section."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/edit")
            html = response.data.decode()

            cockpit_pos = html.index("Open cockpit")
            projection_pos = html.index("Projection ledger")
            deductions_pos = html.index('id="lines-section"')

            assert cockpit_pos < deductions_pos
            assert projection_pos < deductions_pos

    def test_salary_form_edit_buttons_link_to_correct_routes(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Edit-form buttons target the cockpit (profile focused), the
        projection page, and the DIRECT settings tax section (no redirect
        bounce through /salary/tax-config)."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/edit")
            html = response.data.decode()

            assert f"/salary?profile={profile.id}" in html
            assert f"/salary/{profile.id}/projection" in html
            assert "/settings?section=tax" in html

    def test_salary_form_create_hides_buttons(
        self, app, auth_client, seed_user
    ):
        """The create-profile form hides the profile-scoped nav buttons."""
        with app.app_context():
            response = auth_client.get("/salary/new")
            html = response.data.decode()

            assert "Open cockpit" not in html
            assert "Projection ledger" not in html

    def test_salary_form_buttons_inline_with_submit(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Nav buttons share the submit row, above the deductions section."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/edit")
            html = response.data.decode()

            # The buttons and the submit button share a flex container.
            submit_pos = html.index("Update Profile")
            cockpit_pos = html.index("Open cockpit")
            projection_pos = html.index("Projection ledger")
            deductions_pos = html.index('id="lines-section"')

            # Buttons are near the submit button, before the deductions section.
            assert cockpit_pos > submit_pos
            assert cockpit_pos < deductions_pos
            assert projection_pos < deductions_pos

            # Exactly one projection link on the form (the old duplicate
            # bottom set stays removed).
            projection_url = f"/salary/{profile.id}/projection"
            assert html.count(projection_url) == 1

    def test_breakdown_route_accessible_from_button(
        self, app, auth_client, seed_user, seed_periods
    ):
        """The breakdown URL that buttons link to redirects into the cockpit."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(
                f"/salary/{profile.id}/breakdown", follow_redirects=True
            )

            assert response.status_code == 200
            # The card's element, not its title, which the strip also
            # carries since ruling R-SAL126.
            assert b'id="anatomy-composition"' in response.data

    def test_projection_route_accessible_from_button(
        self, app, auth_client, seed_user, seed_periods
    ):
        """The projection URL that buttons link to returns a successful response."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/projection")

            assert response.status_code == 200
            assert b"Projection ledger" in response.data


class TestCockpitContext:
    """Cockpit context contract: selection, chips, composition, chart JSON."""

    def test_context_single_profile(self, app, auth_client, seed_user, seed_periods, tax_law):
        """Chips/composition are hand-correct for a no-tax $75,000 profile.

        Under the no-tax law every withholding is $0, so net == gross.
        Gross biweekly = the one pay entry, 2884.62 ($75,000 / 26 =
        2884.6153..., half-up to cents).  Net == gross == 2884.62; annual
        salary (no raises) is the paycheck times the count (ruling
        R-SAL59): 2884.62 x 26 = 75000.12; take-home =
        2884.62/2884.62*100 = 100; net is 100.0% of gross.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            profile = _create_profile(seed_user)

            response, ctx = _capture_render(
                app, auth_client, "/salary", "salary/cockpit.html",
            )

            assert response.status_code == 200
            assert ctx is not None
            assert ctx["empty_state"] is None
            assert ctx["profile"].id == profile.id
            assert ctx["chips"]["gross"] == Decimal("2884.62")
            assert ctx["chips"]["annual_salary"] == Decimal("75000.12")
            assert ctx["chips"]["take_home_rate_pct"] == Decimal("100")
            assert ctx["composition"]["net"] == Decimal("2884.62")
            assert ctx["composition"]["taxes_total"] == Decimal("0")
            assert ctx["composition"]["pct_net"] == Decimal("100.0")

    def test_chart_json_structure(self, app, auth_client, seed_user, seed_periods):
        """chart_json carries the periods/thirds/raises/today contract.

        seed_periods anchors on 2026-01-02, cadence 14: three periods start
        in January (01-02, 01-16, 01-30), so the 01-30 period is a third
        paycheck.  With no raises the raises list is empty; today is frozen
        at 2026-03-20.
        """
        # Pylint: import-outside-toplevel -- file-wide test convention.
        import json  # pylint: disable=import-outside-toplevel

        with app.app_context():
            _create_profile(seed_user)

            _response, ctx = _capture_render(
                app, auth_client, "/salary", "salary/cockpit.html",
            )
            chart = json.loads(ctx["chart_json"])

            assert set(chart.keys()) == {"periods", "thirds", "raises", "today"}
            assert chart["today"] == "2026-03-20"
            # 10 seeded periods, all inside the +18-month window.
            assert len(chart["periods"]) == 10
            assert all("start" in p and "net" in p for p in chart["periods"])
            # The single third paycheck is the 2026-01-30 period.
            assert [t["start"] for t in chart["thirds"]] == ["2026-01-30"]
            # No raises configured.
            assert chart["raises"] == []

    def test_empty_state_no_profiles(self, app, auth_client, seed_user):
        """Zero active profiles renders the create-CTA empty state."""
        with app.app_context():
            response, ctx = _capture_render(
                app, auth_client, "/salary", "salary/cockpit.html",
            )
            assert response.status_code == 200
            assert ctx["empty_state"] == "no_profiles"
            assert ctx["chips"] is None
            assert ctx["chart_json"] is None
            assert b"New salary profile" in response.data

    def test_empty_state_no_periods(self, app, auth_client, seed_user):
        """A profile with no pay periods renders the generate-periods blocker."""
        with app.app_context():
            _create_profile(seed_user)  # no seed_periods fixture -> zero periods

            response, ctx = _capture_render(
                app, auth_client, "/salary", "salary/cockpit.html",
            )
            assert response.status_code == 200
            assert ctx["empty_state"] == "no_periods"
            assert ctx["profile"] is not None
            assert ctx["chips"] is None
            assert b"Generate pay periods" in response.data

    def test_profile_query_other_user_404(self, app, auth_client, seed_user, seed_periods):
        """?profile=<other user's id> is 404 (owned-and-active required)."""
        with app.app_context():
            _create_profile(seed_user)
            other = _create_other_user_profile()

            resp = auth_client.get(f"/salary?profile={other['profile'].id}")
            assert resp.status_code == 404

    def test_profile_query_inactive_404(self, app, auth_client, seed_user, seed_periods):
        """?profile=<own but inactive id> is 404 (inactive is not a target)."""
        with app.app_context():
            _create_profile(seed_user)  # active default so we reach selection
            inactive = _create_inactive_profile(seed_user)

            resp = auth_client.get(f"/salary?profile={inactive.id}")
            assert resp.status_code == 404

    def test_period_query_nonexistent_404(self, app, auth_client, seed_user, seed_periods):
        """?period=<nonexistent id> is 404."""
        with app.app_context():
            _create_profile(seed_user)
            resp = auth_client.get("/salary?period=999999")
            assert resp.status_code == 404

    def test_period_query_other_user_404(self, app, auth_client, seed_user, seed_periods):
        """?period=<other user's period id> is 404 (owned period required)."""
        with app.app_context():
            _create_profile(seed_user)
            other = _create_other_user_profile()
            other_period = (
                db.session.query(PayPeriod)
                .filter_by(user_id=other["user"].id)
                .first()
            )

            resp = auth_client.get(f"/salary?period={other_period.id}")
            assert resp.status_code == 404

    def test_multi_profile_switcher(self, app, auth_client, seed_user, seed_periods):
        """Two active profiles put both in context and render the switcher."""
        with app.app_context():
            _create_profile(seed_user)
            # Second active profile (distinct name to clear the unique
            # constraint); reactivate the helper's inactive profile.
            second = _create_inactive_profile(seed_user, name="Side Gig")
            second.is_active = True
            second.template.is_active = True
            db.session.commit()

            response, ctx = _capture_render(
                app, auth_client, "/salary", "salary/cockpit.html",
            )
            assert response.status_code == 200
            assert len(ctx["profiles"]) == 2
            assert b"Side Gig" in response.data
            assert b"Day Job" in response.data


class TestAnatomyFragment:
    """HTMX anatomy fragment: owner-only, prev/next context, OOB lines card."""

    def test_anatomy_renders(self, app, auth_client, seed_user, seed_periods):
        """GET the fragment returns the composition + OOB lines cards."""
        with app.app_context():
            profile = _create_profile(seed_user)

            resp = auth_client.get(
                f"/salary/{profile.id}/anatomy/{seed_periods[5].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'id="anatomy-composition"' in html
            assert 'id="anatomy-lines"' in html
            assert 'hx-swap-oob="true"' in html
            assert "Where this paycheck goes" in html

    def test_anatomy_prev_next_middle(self, app, auth_client, seed_user, seed_periods):
        """A middle period exposes both prev and next stepper targets."""
        with app.app_context():
            profile = _create_profile(seed_user)

            resp = auth_client.get(
                f"/salary/{profile.id}/anatomy/{seed_periods[5].id}"
            )
            html = resp.data.decode()
            # Prev -> period 4, next -> period 6; both hx-get to /anatomy/.
            assert f"/anatomy/{seed_periods[4].id}" in html
            assert f"/anatomy/{seed_periods[6].id}" in html
            assert html.count("/anatomy/") == 2

    def test_anatomy_first_period_prev_disabled(
        self, app, auth_client, seed_user, seed_periods
    ):
        """The first period disables prev (only the next stepper hx-gets)."""
        with app.app_context():
            profile = _create_profile(seed_user)

            resp = auth_client.get(
                f"/salary/{profile.id}/anatomy/{seed_periods[0].id}"
            )
            html = resp.data.decode()
            # Only one active stepper (next -> period 1); prev is disabled.
            assert html.count("/anatomy/") == 1
            assert f"/anatomy/{seed_periods[1].id}" in html
            assert "disabled" in html

    def test_anatomy_other_user_profile_404(self, app, auth_client, seed_user, seed_periods):
        """The fragment 404s for another user's profile."""
        with app.app_context():
            other = _create_other_user_profile()
            resp = auth_client.get(
                f"/salary/{other['profile'].id}/anatomy/{seed_periods[0].id}"
            )
            assert resp.status_code == 404

    def test_anatomy_nonexistent_period_404(self, app, auth_client, seed_user, seed_periods):
        """The fragment 404s for a nonexistent period."""
        with app.app_context():
            profile = _create_profile(seed_user)
            resp = auth_client.get(f"/salary/{profile.id}/anatomy/999999")
            assert resp.status_code == 404

    def test_anatomy_raise_banner_shows_on_the_first_paycheck_it_reaches(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The raise banner shows on the paycheck a raise first reaches.

        A MERIT raise from 2026-03-01 first reaches seed_periods[5]
        (2026-03-13, paying 02-22..03-07), so the engine badges it there
        (ruling plan#3092) and its anatomy fragment shows the "Raise: Merit
        +2.5%" banner.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 3, 1),
                percentage=Decimal("0.0250"),
            ))
            db.session.commit()

            resp = auth_client.get(
                f"/salary/{profile.id}/anatomy/{seed_periods[5].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Raise:" in html
            assert "Merit +2.5%" in html

    def test_anatomy_raise_banner_absent_on_a_later_paycheck(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The raise banner does NOT repeat on a later paycheck.

        seed_periods[6] (2026-03-27) pays 03-08..03-21, wholly after the
        2026-03-01 raise that [5] (2026-03-13) first reaches, so the engine
        badges nothing there and its anatomy fragment omits the banner while
        the composition card still renders.  It was the cockpit's run-start
        filter that hid it until task plan#3111, when the engine badged both
        March paychecks (P-SA1).
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 3, 1),
                percentage=Decimal("0.0250"),
            ))
            db.session.commit()

            resp = auth_client.get(
                f"/salary/{profile.id}/anatomy/{seed_periods[6].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # The engine badges the raise on period 5 alone, so period 6
            # shows no "Raise:" alert.
            assert "Raise:" not in html
            # Sanity: the fragment still rendered its composition card.
            assert "Where this paycheck goes" in html

    def test_anatomy_an_equal_raise_on_the_next_paycheck_shows_its_own_banner(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Two 2.5% merit raises, from 03-01 and 03-15: 03-27 shows the second.

        03-15 falls in seed_periods[6]'s 03-08..03-21, so the engine badges
        [6] with the second raise as it badges [5] with the first, two
        banners with one label.  The run-start filter this replaced (task
        plan#3111) read [6] as the first raise's run going on and hid it.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()
            for day in (date(2026, 3, 1), date(2026, 3, 15)):
                db.session.add(SalaryRaise(
                    salary_profile_id=profile.id,
                    raise_type_id=raise_type.id,
                    effective_date=day,
                    percentage=Decimal("0.0250"),
                ))
            db.session.commit()

            for period in (seed_periods[5], seed_periods[6]):
                resp = auth_client.get(f"/salary/{profile.id}/anatomy/{period.id}")
                assert resp.status_code == 200
                html = resp.data.decode()
                assert "Raise:" in html, f"no banner on {period.start_date}"
                assert "Merit +2.5%" in html
