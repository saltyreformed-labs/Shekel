"""The analytics page's sign-in gate, its shell and nav, and the retired export and tab URLs."""


# ── Auth Tests ──────────────────────────────────────────────────────


class TestAnalyticsAuth:
    """Tests for authentication requirements on analytics endpoints."""

    def test_analytics_requires_auth(self, app, client):
        """GET /analytics redirects unauthenticated users to login."""
        with app.app_context():
            resp = client.get("/analytics")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_all_tabs_require_auth(self, app, client):
        """Every analytics tab endpoint requires auth.

        The five current tab endpoints AND the three retired-redirect URLs
        (/variance, /trends, /year-end) all sit behind ``@login_required``,
        so an unauthenticated GET 302s to /login before any content render
        or redirect-to-page fires.
        """
        tab_urls = [
            "/analytics/calendar",
            "/analytics/spending",
            "/analytics/taxes",
            "/analytics/income-statement",
            "/analytics/balance-sheet",
            "/analytics/variance",
            "/analytics/trends",
            "/analytics/year-end",
        ]
        with app.app_context():
            for url in tab_urls:
                resp = client.get(url)
                assert resp.status_code == 302, (
                    f"{url} did not require auth"
                )
                assert "/login" in resp.headers["Location"], (
                    f"{url} did not redirect to login"
                )


# ── Page Rendering Tests ──────────────────────────────────────────


class TestAnalyticsPage:
    """Tests for GET /analytics page structure and content."""

    def test_analytics_page_renders(self, app, auth_client, seed_user):
        """GET /analytics returns 200 with Analytics heading."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"Analytics" in resp.data

    def test_analytics_page_has_pills(self, app, auth_client, seed_user):
        """GET /analytics shows the four Slice-4 nav pills.

        The Slice-4 shell collapse reduced the nav to four pills: Calendar,
        Spending, Statements, and Taxes.  The Income Statement and Balance
        Sheet are no longer shell pills -- they load inside the Statements
        pill's internal toggle via HTMX, so their labels do not appear on
        the bare page.  The retired Year-End / Variance / Trends labels are
        gone from the nav (their routes now redirect to the page).
        """
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data
            assert b"Calendar" in html
            assert b"Spending" in html
            assert b"Statements" in html
            assert b"Taxes" in html
            assert b"Year-End" not in html
            assert b"Variance" not in html
            assert b"Trends" not in html
            # Income Statement / Balance Sheet load inside the Statements
            # toggle (HTMX), so they are absent from the bare shell.
            assert b"Income Statement" not in html
            assert b"Balance Sheet" not in html

    def test_analytics_page_has_tab_content_div(self, app, auth_client, seed_user):
        """GET /analytics contains the #tab-content target div for HTMX swaps."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b'id="tab-content"' in resp.data

    def test_calendar_tab_is_default_load(self, app, auth_client, seed_user):
        """The shell auto-loads Calendar by default from inside #tab-content.

        D13 moved the auto-load off the pill onto the #tab-content spinner
        (so the initial fetch never pushes a URL); on the bare /analytics page
        (active_tab="calendar") that loader targets the calendar tab.
        """
        with app.app_context():
            resp = auth_client.get("/analytics")
            html = resp.data.decode()
            # Exactly one element auto-loads on page load, and it fetches the
            # Calendar partial.
            assert 'hx-trigger="load"' in html
            loader = html.split('hx-trigger="load"')[0].rsplit("<div", 1)[1]
            assert "/analytics/calendar" in loader

    def test_other_tabs_no_auto_load(self, app, auth_client, seed_user):
        """Only one element auto-loads; the pills are click-only (D13).

        The pills push their URL on click (hx-push-url) and no longer carry a
        'load' trigger -- the single auto-load lives on the #tab-content
        spinner -- so exactly one 'load' trigger exists on the page.
        """
        with app.app_context():
            resp = auth_client.get("/analytics")
            html = resp.data.decode()
            load_triggers = html.count('hx-trigger="load"')
            assert load_triggers == 1, (
                f"Expected exactly 1 'load' trigger, found {load_triggers}"
            )
            # Every pill pushes its own URL (D13).
            assert html.count('hx-push-url="true"') == 4

    def test_tab_content_has_spinner(self, app, auth_client, seed_user):
        """The #tab-content div contains spinner markup as initial content."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"spinner-border" in resp.data

    def test_analytics_uses_scroll_pills(self, app, auth_client, seed_user):
        """GET /analytics uses the shekel-scroll-pills class for scroll behavior."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"shekel-scroll-pills" in resp.data


# ── CSV Export Tests ──────────────────────────────────────────────


class TestCsvExportRetired:
    """The calendar CSV export was removed root-and-branch (P-AN4).

    The calendar month/year CSV was the last surviving analytics export (the
    Slice-4 shell collapse had already retired the year-end, variance, trends,
    income-statement, and balance-sheet exports).  With P-AN4 no analytics
    export remains: neither calendar view offers a CSV button, and a stale
    ``?format=csv`` is now an inert query arg that renders the normal calendar
    rather than a download.
    """

    def test_calendar_month_has_no_csv_button(self, app, auth_client, seed_user,
                                              seed_periods):
        """The calendar month view offers no CSV download control."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "format=csv" not in html
            assert "bi-download" not in html

    def test_calendar_year_has_no_csv_button(self, app, auth_client, seed_user,
                                             seed_periods):
        """The calendar year view offers no CSV download control."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "format=csv" not in html
            assert "bi-download" not in html

    def test_format_csv_is_inert_on_htmx_request(self, app, auth_client,
                                                 seed_user, seed_periods):
        """A stale ?format=csv on an HTMX calendar GET renders the grid.

        The format arg has no branch anymore, so it is ignored: the request
        returns the normal calendar partial, never a text/csv attachment.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data
            assert "text/csv" not in resp.headers.get("Content-Type", "")

    def test_format_csv_non_htmx_renders_shell_not_csv(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stale ?format=csv on a direct (non-HTMX) GET serves the shell.

        With the CSV branch gone, format=csv no longer bypasses the HTMX
        guard; a non-HTMX hit renders the analytics shell (Calendar active,
        D13), never a CSV attachment.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month&year=2026&month=1",
            )
            assert resp.status_code == 200
            assert "text/csv" not in resp.headers.get("Content-Type", "")
            assert "shekel-scroll-pills" in resp.data.decode()


# ── Retired Tab Redirect Tests ────────────────────────────────────


class TestRetiredTabRedirects:
    """The retired Variance / Trends / Year-End URLs redirect to /analytics.

    Slice-4 shell collapse: these three tabs were folded into Spending and
    Taxes, so their routes are now a single redirect stub.  Old bookmarks and
    the former ?format=csv exports land on the current page instead of 404ing.
    """

    def test_retired_urls_redirect_to_page(self, app, auth_client, seed_user):
        """Each retired URL 302s to /analytics for an authenticated user."""
        with app.app_context():
            for url in ("/analytics/variance", "/analytics/trends",
                        "/analytics/year-end"):
                resp = auth_client.get(url)
                assert resp.status_code == 302, f"{url} did not redirect"
                assert resp.headers["Location"].endswith("/analytics"), (
                    f"{url} did not redirect to /analytics"
                )

    def test_retired_csv_urls_also_redirect(self, app, auth_client, seed_user):
        """The former ?format=csv exports redirect too (no CSV body)."""
        with app.app_context():
            resp = auth_client.get("/analytics/year-end?format=csv&year=2026")
            assert resp.status_code == 302
            assert "text/csv" not in resp.headers.get("Content-Type", "")
            assert resp.headers["Location"].endswith("/analytics")

    def test_retired_urls_still_require_auth(self, app, client):
        """The redirect stub is behind login_required: unauth -> /login."""
        with app.app_context():
            resp = client.get("/analytics/variance")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


# ── Nav Bar Tests ─────────────────────────────────────────────────


class TestAnalyticsNav:
    """Tests for nav bar updates after Charts-to-Analytics rename."""

    def test_nav_shows_analytics_link(self, app, auth_client, seed_user):
        """Authenticated pages show Analytics link in the nav bar."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            assert b"Analytics" in resp.data
            assert b'href="/analytics"' in resp.data

    def test_nav_does_not_show_charts_link(self, app, auth_client, seed_user):
        """Nav bar no longer shows a link pointing to /charts."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'href="/charts"' not in html

    def test_charts_route_redirects(self, app, auth_client, seed_user):
        """GET /charts returns 301 redirect to /analytics."""
        with app.app_context():
            resp = auth_client.get("/charts")
            assert resp.status_code == 301
            assert "/analytics" in resp.headers["Location"]

    def test_analytics_active_nav_state(self, app, auth_client, seed_user):
        """GET /analytics shows the Analytics nav item as active."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The nav link for /analytics should have the active class.
            assert 'class="nav-link active" href="/analytics"' in html
