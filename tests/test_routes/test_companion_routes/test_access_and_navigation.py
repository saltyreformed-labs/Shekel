"""Companion route tests: who may open the companion view, its period navigation and arrows, and
its empty states.
"""

from datetime import date

from app.models.user import User, UserSettings
from app.services.auth_service import hash_password
from tests._test_helpers import generate_row_of, open_owner_calendar
from tests._two_factor_sign_in import sign_in_seed_companion

from ._helpers import _make_template


# ── Route Access ─────────────────────────────────────────────────────


class TestRouteAccess:
    """Verify access control on companion routes."""

    def test_companion_gets_200_with_visible_transactions(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion GET /companion/period/<id> returns 200 with visible names.

        Creates visible and non-visible templates.  Response HTML
        contains only visible transaction names.  Uses explicit
        period_id to avoid date-sensitivity from the default
        current-period logic.
        """
        t_vis = _make_template(seed_user, companion_visible=True, name="Groceries")
        t_hid = _make_template(seed_user, companion_visible=False, name="Mortgage")
        generate_row_of(t_vis, seed_periods_today[0])
        generate_row_of(t_hid, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Groceries" in html
        assert "Mortgage" not in html

    def test_owner_redirected_from_companion(
        self, auth_client,
    ):
        """Plan 10.11: Owner GET /companion/ redirects to /grid."""
        resp = auth_client.get("/companion/")
        assert resp.status_code == 302
        assert "/grid" in resp.headers["Location"]

    def test_unauthenticated_redirected_to_login(self, client):
        """Unauthenticated GET /companion/ redirects to /login."""
        resp = client.get("/companion/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_companion_view_with_no_periods(
        self, app, db, seed_user, seed_companion,
    ):
        """Companion with no owner periods gets 200 (empty state).

        When no period contains the owner's day, the route should
        gracefully render the empty state.
        Note: seed_periods_today is not included, so the owner has no periods.
        """
        comp = sign_in_seed_companion(app)
        resp = comp.get("/companion/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No pay periods available" in html


# ── Period Navigation ────────────────────────────────────────────────


class TestPeriodNavigation:
    """Verify period navigation routes and arrow rendering."""

    def test_companion_period_view_valid(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.3: GET /companion/period/<valid_id> returns 200.

        Shows the correct period's transactions.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries P1")
        generate_row_of(template, seed_periods_today[1])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[1].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Groceries P1" in html

    def test_companion_period_view_other_owner(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion accessing another owner's period gets 404."""
        second_user = User(
            email="other@test.local",
            password_hash=hash_password("otherpass"),
            display_name="Other",
        )
        db.session.add(second_user)
        db.session.flush()
        settings = UserSettings(user_id=second_user.id)
        db.session.add(settings)

        # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
        other_period = open_owner_calendar(second_user.id, date(2026, 1, 2))[0]
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{other_period.id}")
        assert resp.status_code == 404

    def test_companion_period_view_nonexistent(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion accessing a non-existent period gets 404."""
        comp = sign_in_seed_companion(app)
        resp = comp.get("/companion/period/999999")
        assert resp.status_code == 404

    def test_prev_next_links_present(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Response HTML contains previous/next period links when applicable.

        Viewing period[1] (middle period) should show both arrows.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        generate_row_of(template, seed_periods_today[1])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[1].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Previous link points to period[0].
        assert f"/companion/period/{seed_periods_today[0].id}" in html
        # Next link points to period[2].
        assert f"/companion/period/{seed_periods_today[2].id}" in html

    def test_no_prev_link_on_first_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """First period has no previous link (no chevron-left href).

        Viewing period[0] should not contain a link to a previous period.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # There should be a next link but no previous link.
        assert f"/companion/period/{seed_periods_today[1].id}" in html
        # The chevron-left should not be an anchor.
        assert "bi-chevron-left" not in html or (
            "bi-chevron-left" in html
            and f"/companion/period/{seed_periods_today[0].id}" not in html
        )

    def test_no_next_link_on_last_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Last period has no next link.

        Viewing the last period should not contain a link to a next period.
        """
        last_period = seed_periods_today[-1]
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        generate_row_of(template, last_period)
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{last_period.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Previous link should exist (second-to-last period).
        prev_period = seed_periods_today[-2]
        assert f"/companion/period/{prev_period.id}" in html

    def test_owner_redirected_from_period_view(
        self, auth_client, seed_periods_today,
    ):
        """Owner GET /companion/period/<id> redirects to /grid."""
        resp = auth_client.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 302
        assert "/grid" in resp.headers["Location"]


# ── Empty States ─────────────────────────────────────────────────────


class TestEmptyStates:
    """Verify empty state rendering."""

    def test_period_with_no_visible_transactions(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Period with no visible transactions shows empty state message."""
        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No transactions for this period" in html

    def test_period_with_only_non_visible_shows_empty(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Period with only non-visible transactions shows empty state.

        The transactions exist but are filtered out by visibility,
        so the companion sees the empty state.
        """
        template = _make_template(
            seed_user, companion_visible=False, name="Mortgage",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No transactions for this period" in html
        assert "Mortgage" not in html
