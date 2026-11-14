"""
Tests for the retired /obligations route.

The standalone obligations page was retired in the Recurring cluster
overhaul (Loop B): its monthly-equivalent committed totals moved into the
unified /templates (Recurring) surface's summary band and section
subtotals, and the dashboard / grid own the balance projection it used to
duplicate.  The /obligations URL is kept only as a redirect so old
bookmarks land on the surface that replaced it.

The monthly-equivalent arithmetic these tests used to assert now lives in
``tests/test_services/test_recurring_view/test_the_money_figures.py`` (producer) and
``tests/test_routes/test_recurring_list.py`` (rendered surface).
"""


class TestObligationsRedirect:
    """The retired page forwards to the unified Recurring surface."""

    def test_redirects_to_recurring_surface(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """GET /obligations 302-redirects to /templates for a logged-in user."""
        resp = auth_client.get("/obligations")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/templates")

    def test_followed_redirect_lands_on_recurring(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Following the redirect renders the unified Recurring surface."""
        resp = auth_client.get("/obligations", follow_redirects=True)
        assert resp.status_code == 200
        # The unified surface's page heading, not the old obligations page.
        assert b"Recurring" in resp.data

    def test_requires_login(self, client):
        """GET /obligations without authentication redirects to login."""
        resp = client.get("/obligations")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")
