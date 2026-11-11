"""
Shekel Budget App -- Companion Route Guard Tests

Comprehensive tests for Commit 9: verifies that @require_owner
blocks companion access on all owner-only routes, that companion
login routing works correctly, that mark_done allows companion
access on visible transactions, and that the nav bar renders
role-appropriate content.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from tests._test_helpers import (
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    payback_row_of,
)
from tests._two_factor_sign_in import sign_in_seed_companion


# ── Companion blocked from all guarded routes ────────────────────────


class TestCompanionGuardedRoutes:
    """Verify that companions receive 404 on every owner-only route.

    Tests all 17 guarded blueprints: grid, templates, settings,
    dashboard, accounts, analytics, categories, debt_strategy,
    investment, loan, obligations, pay_periods, retirement,
    salary, savings, transfers, charts.
    """

    @pytest.mark.parametrize("url", [
        "/grid",
        "/templates",
        "/settings",
        "/dashboard",
        "/accounts",
        "/analytics",
        "/categories",
        "/debt-strategy",
        "/accounts/99999/investment",
        "/accounts/99999/loan",
        "/obligations",
        "/pay-periods/generate",
        "/retirement",
        "/salary",
        "/savings",
        "/transfers",
        "/charts",
    ])
    def test_companion_blocked_from_guarded_route(
        self, companion_client, url,
    ):
        """Companion gets 404 on every owner-only route.

        The @require_owner decorator fires before the route body,
        so the companion is blocked regardless of URL parameters or
        data availability.  Returns 404 (not 403) per the security
        response rule.
        """
        resp = companion_client.get(url)
        assert resp.status_code == 404, (
            f"Expected 404 for companion on {url}, got {resp.status_code}"
        )


# ── Owner access regression ──────────────────────────────────────────


class TestOwnerAccessRegression:
    """Verify owner users still pass @require_owner on guarded routes.

    Tests a representative subset of guarded routes with seed_user
    data to confirm @require_owner does not block legitimate owners.
    """

    def test_owner_can_access_settings(self, auth_client):
        """Owner can access /settings -- not blocked by @require_owner."""
        resp = auth_client.get("/settings")
        assert resp.status_code == 200

    def test_owner_can_access_templates(self, auth_client):
        """Owner can access /templates -- empty list is fine."""
        resp = auth_client.get("/templates")
        assert resp.status_code == 200

    def test_owner_can_access_retirement(self, auth_client):
        """Owner can access /retirement planning page."""
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200

    def test_owner_can_access_grid(self, auth_client, seed_periods_today):
        """Owner can access /grid with pay periods."""
        resp = auth_client.get("/grid")
        assert resp.status_code == 200

    def test_owner_can_access_analytics(self, auth_client):
        """Owner can access /analytics -- verifies lazy tab loading."""
        resp = auth_client.get("/analytics")
        assert resp.status_code == 200

    def test_owner_can_access_salary(self, auth_client):
        """Owner can access /salary -- empty profile list is fine."""
        resp = auth_client.get("/salary")
        assert resp.status_code == 200

    def test_owner_can_access_savings(self, auth_client):
        """Owner can access /savings dashboard."""
        resp = auth_client.get("/savings")
        assert resp.status_code == 200

    def test_owner_can_access_transfers(self, auth_client):
        """Owner is not blocked from /transfers by the companion guards.

        /transfers now forwards to the unified Recurring surface (Loop B);
        following the redirect confirms the owner reaches it (200), i.e. the
        guards did not wrongly reject the owner.
        """
        resp = auth_client.get("/transfers", follow_redirects=True)
        assert resp.status_code == 200

    def test_owner_can_access_dashboard(self, auth_client, seed_periods_today):
        """Owner can access /dashboard with periods."""
        resp = auth_client.get("/dashboard")
        assert resp.status_code == 200


# ── Companion login routing ──────────────────────────────────────────


class TestCompanionLoginRouting:
    """Verify login redirects companions to /companion/ and owners to /dashboard."""

    def test_companion_login_redirects_to_companion_index(
        self, app, db, seed_companion,
    ):
        """POST /login as companion redirects to /companion/ (not /dashboard).

        Verifies the login route checks user.role_id after login_user()
        and redirects companions to their dedicated view.
        """
        client = app.test_client()
        resp = client.post("/login", data={
            "email": "companion@shekel.local",
            "password": "companionpass",
        })
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/companion/")

    def test_owner_login_redirects_to_dashboard(self, app, db, seed_user):
        """POST /login as owner redirects to /dashboard.

        Verifies the owner login path is unchanged by companion routing.
        """
        client = app.test_client()
        resp = client.post("/login", data={
            "email": "test@shekel.local",
            "password": "testpass",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/dashboard" in location or location.endswith("/")

    def test_companion_login_ignores_next_param(
        self, app, db, seed_companion,
    ):
        """Companion login ignores ?next=/grid and goes to /companion/.

        Prevents companions from following next URLs to guarded routes,
        which would result in 404 after @require_owner.
        """
        client = app.test_client()
        resp = client.post("/login?next=/grid", data={
            "email": "companion@shekel.local",
            "password": "companionpass",
        })
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/companion/")
        assert "/grid" not in resp.headers["Location"]

    def test_authenticated_companion_visiting_login_redirects(
        self, companion_client,
    ):
        """Already-authenticated companion visiting /login goes to /companion/.

        Tests the "already authenticated" check at the top of the login route.
        """
        resp = companion_client.get("/login")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/companion/")

    def test_authenticated_owner_visiting_login_redirects(self, auth_client):
        """Already-authenticated owner visiting /login goes to /dashboard.

        Verifies the owner's "already authenticated" redirect is unchanged.
        """
        resp = auth_client.get("/login")
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/dashboard" in location or location.endswith("/")

    def test_authenticated_companion_visiting_register_redirects(
        self, companion_client,
    ):
        """Already-authenticated companion visiting /register goes to /companion/.

        Prevents companions from being redirected to /dashboard from
        the registration page's authenticated-check.
        """
        resp = companion_client.get("/register")
        # Could be 302 (redirect) or 404 (registration disabled).
        if resp.status_code == 302:
            assert resp.headers["Location"].endswith("/companion/")


# ── Companion-accessible routes ──────────────────────────────────────


class TestCompanionAccessibleRoutes:
    """Verify routes that companions CAN access work correctly."""

    def test_companion_can_access_companion_page(self, companion_client):
        """Companion can access /companion/ and gets 200.

        The companion stub page should render without errors.
        """
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200

    def test_owner_redirected_from_companion_page(self, auth_client):
        """Owner accessing /companion/ is redirected to /grid.

        The companion index route checks role and redirects owners
        to the grid (their normal landing page).
        """
        resp = auth_client.get("/companion/")
        assert resp.status_code == 302
        assert "/grid" in resp.headers["Location"]

    def test_companion_can_access_login_page(self, app, db, seed_companion):
        """Companion can access GET /login (unauthenticated).

        Authentication routes must be accessible by all users.
        """
        client = app.test_client()
        resp = client.get("/login")
        assert resp.status_code == 200


# ── Decorator order ──────────────────────────────────────────────────


class TestDecoratorOrder:
    """Verify the login gate answers before @require_owner is consulted."""

    def test_unauthenticated_user_gets_login_redirect(self, client):
        """Unauthenticated user hitting a guarded route gets login redirect.

        The login gate (``app/login_gate.py``) answers before any view or
        decorator runs.  Without it, ``require_owner`` would raise on the
        anonymous principal's missing ``role_id`` (plan step
        ``bank_import:X-gs``), not redirect -- so the redirect is the proof
        that the gate ran first.
        """
        resp = client.get("/grid")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_unauthenticated_user_on_settings(self, client):
        """Unauthenticated user hitting /settings gets login redirect."""
        resp = client.get("/settings")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_unauthenticated_user_on_dashboard(self, client):
        """Unauthenticated user hitting /dashboard gets login redirect."""
        resp = client.get("/dashboard")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ── Mark-done companion access ───────────────────────────────────────


def _create_companion_test_transaction(
    db, seed_user, seed_periods_today, companion_visible, template_name="Test Item",
):
    """Create a definition and its engine-generated row for companion mark_done testing.

    The row is the definition's own (:func:`generate_row_of`, plan step
    balance:X-cf-4); the visibility under test is stated on the definition,
    which is where the guard reads it.

    Args:
        db: The database session fixture.
        seed_user: The seed_user fixture dict.
        seed_periods_today: The seed_periods_today fixture (list of PayPeriod).
        companion_visible: Whether the template is companion-visible.
        template_name: Name for the template and transaction.

    Returns:
        The created Transaction object.
    """
    template = make_expense_template(
        db.session, seed_user, amount="500.00", name=template_name,
        category_key=next(iter(seed_user["categories"])),
        companion_visible=companion_visible,
    )
    txn = generate_row_of(template, seed_periods_today[0])
    db.session.commit()
    return txn


class TestMarkDoneCompanionAccess:
    """Test companion access to the mark_done route."""

    def test_companion_marks_visible_transaction_done(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion can mark a companion-visible transaction as Paid.

        Verifies _get_accessible_transaction_for_status allows
        companion access when template.companion_visible is True.
        Arithmetic: status changes from Projected to Paid.
        """
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=True,
            template_name="Groceries",
        )

        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        done_id = ref_cache.status_id(StatusEnum.DONE)
        assert txn.status_id == done_id

    def test_companion_blocked_from_non_visible_transaction(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 for mark_done on non-visible transactions.

        Verifies _get_accessible_transaction_for_status rejects
        companion access when template.companion_visible is False.
        """
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=False,
            template_name="Mortgage",
        )

        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 404

    def test_companion_blocked_from_other_owner_transaction(
        self, app, db, seed_user, seed_periods_today, seed_companion,
        seed_second_user, seed_second_periods,
    ):
        """Companion gets 404 for transactions belonging to a different owner.

        The second owner holds a companion-visible row of their own
        definition -- the isolation fixtures' owner rather than one restated
        here (plan step balance:X-cf-4).  The companion (linked to seed_user)
        cannot mark it as done because the pay_period belongs to a different
        owner.
        """
        template = make_expense_template(
            db.session, seed_second_user, amount="400.00",
            name="Other Groceries", companion_visible=True,
        )
        txn = generate_row_of(template, seed_second_periods[0])
        db.session.commit()

        # Companion (linked to seed_user) tries to mark the second owner's txn.
        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 404

    def test_owner_mark_done_regression(
        self, auth_client, db, seed_user, seed_periods_today,
    ):
        """Owner can still mark their own transactions as Paid.

        Regression test ensuring the companion-aware access function
        does not break the existing owner mark_done flow.
        """
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=True,
            template_name="Owner Groceries",
        )

        resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        done_id = ref_cache.status_id(StatusEnum.DONE)
        assert txn.status_id == done_id

    @pytest.mark.parametrize("shown", [False, True])
    def test_a_placed_one_off_is_the_companions_by_its_definitions_flag(
        self, app, db, seed_user, seed_periods_today, seed_companion, shown,
    ):
        """A one-off placed today (balance:X-bi-7b): 404 unless its DEFINITION says shown.

        Both halves, so a guard answering 404 for everything (or nothing)
        fails one of them.
        """
        txn = one_off_row_of(
            seed_periods_today[0], name="One-off expense", amount="100.00",
            user_id=seed_periods_today[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=list(seed_user["categories"].values())[0].id,
            companion_visible=shown,
        )
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == (200 if shown else 404)

    def test_a_row_with_no_definition_is_never_the_companions(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 for a CC payback, even under a SHOWN parent.

        Ruling **R-BAL73**: a row that names no definition -- a transfer
        shadow, a CC payback -- answers ``False`` to ``visible_to_companion``,
        so ``get_accessible_transaction`` refuses it at the door.  The
        non-trivial arm: the payback's PARENT envelope is companion-visible
        (the companion may mark IT done), and the payback repaying it is
        still not theirs -- nothing states it may be.  Until the family's
        cutover (plan step ``balance:X-bi-7d-2``) a legacy link-less row was
        this case's specimen, refused off its own cell.
        """
        envelope = one_off_row_of(
            seed_periods_today[0], name="Card envelope", amount="100.00",
            user_id=seed_periods_today[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=list(seed_user["categories"].values())[0].id,
            is_envelope=True, companion_visible=True,
        )
        payback = payback_row_of(
            db.session, seed_user, envelope, Decimal("40.00"),
            seed_periods_today[0].start_date,
        )
        db.session.commit()
        assert envelope.visible_to_companion is True
        assert payback.visible_to_companion is False

        comp = sign_in_seed_companion(app)
        assert comp.post(f"/transactions/{envelope.id}/mark-done").status_code == 200
        resp = comp.post(f"/transactions/{payback.id}/mark-done")
        assert resp.status_code == 404


# ── Companion-guarded transaction routes ─────────────────────────────


class TestCompanionBlockedTransactionRoutes:
    """Verify companion is blocked from owner-only transaction routes.

    All transaction routes except mark_done have @require_owner.
    """

    def test_companion_blocked_from_update_transaction(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 on PATCH /transactions/<id>."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=True,
        )
        comp = sign_in_seed_companion(app)
        resp = comp.patch(
            f"/transactions/{txn.id}",
            data={"estimated_amount": "999.00"},
        )
        assert resp.status_code == 404

    def test_companion_blocked_from_cancel(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 on POST /transactions/<id>/cancel."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=True,
        )
        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/cancel")
        assert resp.status_code == 404

    def test_companion_blocked_from_mark_credit(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 on POST /transactions/<id>/mark-credit."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=True,
        )
        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-credit")
        assert resp.status_code == 404

    def test_companion_blocked_from_delete(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 on DELETE /transactions/<id>."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=True,
        )
        comp = sign_in_seed_companion(app)
        resp = comp.delete(f"/transactions/{txn.id}")
        assert resp.status_code == 404

    def test_companion_blocked_from_get_cell(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 on GET /transactions/<id>/cell."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=True,
        )
        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/transactions/{txn.id}/cell")
        assert resp.status_code == 404

    def test_companion_blocked_from_quick_edit(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 on GET /transactions/<id>/quick-edit."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=True,
        )
        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/transactions/{txn.id}/quick-edit")
        assert resp.status_code == 404

    def test_companion_blocked_from_full_edit(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 on GET /transactions/<id>/full-edit."""
        txn = _create_companion_test_transaction(
            db, seed_user, seed_periods_today,
            companion_visible=True,
        )
        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/transactions/{txn.id}/full-edit")
        assert resp.status_code == 404

    def test_companion_blocked_from_create_inline(
        self, app, db, seed_companion,
    ):
        """Companion gets 404 on POST /transactions/inline."""
        comp = sign_in_seed_companion(app)
        resp = comp.post("/transactions/inline", data={})
        assert resp.status_code == 404

    def test_companion_blocked_from_create_transaction(
        self, app, db, seed_companion,
    ):
        """Companion gets 404 on POST /transactions."""
        comp = sign_in_seed_companion(app)
        resp = comp.post("/transactions", data={})
        assert resp.status_code == 404

    def test_companion_blocked_from_carry_forward(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion gets 404 on POST /pay-periods/<id>/carry-forward."""
        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/pay-periods/{seed_periods_today[0].id}/carry-forward")
        assert resp.status_code == 404


# ── Nav bar content ──────────────────────────────────────────────────


class TestNavBarContent:
    """Verify the nav bar renders role-appropriate content."""

    def test_companion_nav_shows_my_budget(self, companion_client):
        """Companion sees 'My Budget' link in nav bar.

        The companion nav should show only 'My Budget' and logout,
        not the full owner navigation.
        """
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "My Budget" in html

    def test_companion_nav_hides_owner_links(self, companion_client):
        """Companion nav does NOT contain full owner navigation links.

        Verifies that Dashboard, Budget, Recurring, Salary, Settings,
        etc. are not present in the companion's rendered nav bar.
        """
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200
        html = resp.data.decode()
        # These are the text labels from the owner nav links.
        assert "> Dashboard" not in html
        assert "> Budget" not in html
        assert "> Recurring" not in html
        assert "> Salary" not in html
        assert "> Settings" not in html

    def test_owner_nav_shows_full_navigation(self, auth_client):
        """Owner sees full navigation links.

        Verifies Dashboard, Budget, Recurring, etc. are present
        in the owner's rendered nav bar.
        """
        resp = auth_client.get("/settings")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Dashboard" in html
        assert "Budget" in html
        assert "Recurring" in html
        assert "Settings" in html

    def test_companion_nav_shows_logout(self, companion_client):
        """Companion can see and use the Logout button."""
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Logout" in html


# ── Context processor ────────────────────────────────────────────────


class TestContextProcessor:
    """Verify COMPANION_ROLE_ID is injected into template context."""

    def test_companion_role_id_available_in_context(self, auth_client):
        """COMPANION_ROLE_ID context variable is set correctly.

        Verified indirectly: the owner's nav bar renders the full
        navigation (not companion nav), which proves the template
        compared current_user.role_id against COMPANION_ROLE_ID
        and branched correctly.
        """
        resp = auth_client.get("/settings")
        assert resp.status_code == 200
        html = resp.data.decode()
        # If COMPANION_ROLE_ID were None or missing, the template
        # conditional would fail or render companion nav for owners.
        assert "Dashboard" in html
        assert "My Budget" not in html
