"""Tests for the onboarding welcome banner context processor."""

from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from app.models.ref import FilingStatus, TransactionType
from app.services.pay_calendar import calendar_for
from app.utils.dates import display_today
from tests._test_helpers import counting_calls, start_test_pay_list

#: The one door every checklist fact queries through (ruling
#: ``balance:R-BAL117``), as ``(module path, attribute)`` for
#: :func:`~tests._test_helpers.counting_calls`.
_FACT_DOOR = ("app.services.onboarding_service", "_exists")


def _add_salary_profile(owner):
    """Give *owner* a salary profile, the checklist's salary step.

    Args:
        owner: A seed-user fixture's dict (``seed_user`` or
            ``seed_second_user``).
    """
    filing_status = db.session.query(FilingStatus).filter_by(
        name="single"
    ).one()
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=owner["user"].id,
        scenario_id=owner["scenario"].id,
        filing_status_id=filing_status.id,
        name="Main",
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("2307.69"))  # $60,000.00 a year / 26
    db.session.commit()


def _add_template(owner):
    """Give *owner* a recurring transaction template, the checklist's last step.

    Args:
        owner: A seed-user fixture's dict (``seed_user`` or
            ``seed_second_user``).
    """
    income_type = db.session.query(TransactionType).filter_by(
        name="Income"
    ).one()
    db.session.add(TransactionTemplate(
        user_id=owner["user"].id,
        account_id=owner["account"].id,
        category_id=owner["categories"]["Salary"].id,
        transaction_type_id=income_type.id,
        name="Paycheck",
    ))
    db.session.commit()


def _complete_setup(owner):
    """Give *owner* both steps ``OnboardingChecklist.complete`` reads.

    Args:
        owner: A seed-user fixture's dict.
    """
    _add_salary_profile(owner)
    _add_template(owner)


def _banner(html):
    """Return the welcome banner's checklist markup, or ``""`` when not drawn.

    Scoping an assertion to the banner keeps it from matching the same words
    elsewhere on the page -- the grid's "No Pay Periods" card, or a flash.

    Args:
        html: A rendered page, decoded.

    Returns:
        The markup from the banner's opening tag to the end of its checklist.
    """
    start = html.find('id="welcome-banner"')
    if start == -1:
        return ""
    return html[start:html.index("</ul>", start)]


class TestNoChecklistAtTheTwoFactorHold:
    """A user without two-factor is shown no checklist (plan#3777).

    The login gate holds them at two-factor setup (``app/login_gate.py``,
    plan#3452), so setting it up is the one thing they can do, and each of the
    checklist's links would lead back there.
    """

    def test_a_held_owners_setup_page_draws_no_checklist(self, no_two_factor_client):
        """The seed owner, his two-factor reset, sees setup and no checklist or its queries.

        The same owner with two-factor, his checklist as incomplete, sees it
        on his own page (:meth:`TestOnboardingBanner.test_banner_shows_for_new_user`).
        """
        with counting_calls(_FACT_DOOR) as counts:
            resp = no_two_factor_client.get("/mfa/setup")

        assert resp.status_code == 200
        assert b"Verify and Enable" in resp.data
        assert b'id="welcome-banner"' not in resp.data
        assert counts["_exists"] == 0


class TestOnboardingBanner:
    """Welcome banner should appear only when setup is incomplete."""

    def test_banner_shows_for_new_user(self, auth_client):
        """A fresh user with no data sees the welcome banner."""
        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert b"Welcome to Shekel!" in resp.data

    def test_banner_hidden_when_all_setup_complete(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Banner disappears once a salary profile and a template exist."""
        _complete_setup(seed_user)

        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert b"Welcome to Shekel!" not in resp.data

    def test_banner_shows_checkmarks_for_completed_steps(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Completed steps show a check icon; incomplete steps show links."""
        resp = auth_client.get("/")
        banner = _banner(resp.data.decode())

        # The account and the categories are provisioned with the owner, so
        # both are checked off.
        assert "bi-check-circle-fill" in banner
        assert "line-through" in banner

        # Salary and templates don't exist, so should show links.  Read off
        # the banner, not the page: the navbar links /salary on every page.
        assert 'href="/salary"' in banner
        assert "Set up a salary profile" in banner
        assert "Set up recurring transactions" in banner

    def test_banner_shows_account_as_complete(self, auth_client, seed_user):
        """Auto-provisioned account shows as completed in the banner."""
        resp = auth_client.get("/")
        html = resp.data.decode()
        assert "Account created" in html

    def test_banner_shows_categories_as_complete(self, auth_client, seed_user):
        """Auto-provisioned categories show as completed in the banner."""
        resp = auth_client.get("/")
        html = resp.data.decode()
        assert "Budget categories set up" in html

    def test_the_salary_and_recurring_rows_never_lock(
        self, bare_auth_client, bare_user,
    ):
        """Both rows are links even for an owner holding no pay period.

        Re-expressed at plan step X-x3 under ruling ``balance:R-BAL116``, which
        deleted the checklist's pay-period row and the two "generate pay
        periods first" locks that deferred to it.  Until then this test was
        ``test_banner_locks_salary_when_no_periods`` and asserted the lock; the
        ruling is the developer's confirmation that the expected behaviour
        changed.  ``bare_user`` still holds no pay period, so the state the
        lock answered is the state asserted here.
        """
        resp = bare_auth_client.get("/")
        banner = _banner(resp.data.decode())

        assert "Welcome to Shekel!" in banner
        assert "generate pay periods first" not in banner.lower()
        assert "bi-lock" not in banner
        assert 'href="/salary"' in banner
        assert 'href="/templates"' in banner

    def test_the_salary_row_is_a_link_for_an_owner_with_periods(
        self, auth_client, seed_user, seed_periods_today
    ):
        """Salary step is an active link for an owner holding pay periods.

        Read off the banner: the navbar links ``/salary`` on every owner page,
        so a page-wide match passed with no banner at all (X-x3's review
        measured it).
        """
        resp = auth_client.get("/")
        banner = _banner(resp.data.decode())
        assert 'href="/salary"' in banner
        assert "bi-lock" not in banner

    def test_a_salary_profile_alone_does_not_complete_it(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Salary done, templates not: the banner stays, one row struck, one a link.

        The AND in ``complete`` (ruling ``balance:R-BAL116``) asserted as what
        the page shows, not only as a query count.
        """
        _add_salary_profile(seed_user)

        resp = auth_client.get("/")
        banner = _banner(resp.data.decode())

        assert "Welcome to Shekel!" in banner
        assert "line-through\">Set up a salary profile" in banner
        assert 'href="/salary"' not in banner
        assert 'href="/templates"' in banner

    def test_a_template_alone_does_not_complete_it(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Templates done, salary not: the banner stays, one row struck, one a link."""
        _add_template(seed_user)

        resp = auth_client.get("/")
        banner = _banner(resp.data.decode())

        assert "Welcome to Shekel!" in banner
        assert "line-through\">Set up recurring transactions" in banner
        assert 'href="/templates"' not in banner
        assert 'href="/salary"' in banner

    def test_each_fact_is_the_viewing_owners_own(
        self, seed_user, second_auth_client, seed_second_user,
    ):
        """One owner's finished setup completes nobody else's checklist.

        The first owner holds a salary profile and a template; the second
        holds neither and must still see the banner with both rows as links.
        A fact that dropped its ``user_id`` filter would read the first
        owner's rows, hide the second owner's banner and fail here.

        Only the second owner requests a page, and the precondition below pins
        who was served.  The first owner's hidden banner is
        :meth:`test_banner_hidden_when_all_setup_complete`'s to assert.
        """
        _complete_setup(seed_user)

        resp = second_auth_client.get("/")
        html = resp.data.decode()
        banner = _banner(html)

        assert resp.status_code == 200
        assert "Second User" in html, "precondition: served as the second owner"
        assert "Welcome to Shekel!" in banner
        assert 'href="/salary"' in banner
        assert 'href="/templates"' in banner

    def test_a_lapsed_schedule_is_told_by_the_grid_alone(
        self, auth_client, seed_user, seed_periods,
    ):
        """No period covers today: the grid says so, and the checklist is silent.

        ``seed_periods`` runs ten biweekly periods from 2026-01-02, so the
        schedule ended in May and no saved period covers today -- the state
        ruling R-DA wanted the checklist to report.  Ruling
        ``balance:R-BAL116`` gives it ONE surface instead: the grid's own
        "No Pay Periods" card, drawn on the same response as a checklist that
        carries no pay-period row to disagree with it.  The precondition is
        asserted rather than assumed, so a clock or fixture change fails here
        and not as a mystery below.
        """
        assert calendar_for(seed_user["user"].id).period_containing(
            display_today(),
        ) is None, "precondition: no saved period covers today"

        resp = auth_client.get("/grid")
        html = resp.data.decode()

        assert resp.status_code == 200
        assert "No Pay Periods" in html
        banner = _banner(html)
        assert "Set up a salary profile" in banner
        assert "pay period" not in banner.lower()

    def test_banner_not_shown_to_anonymous_user(self, client):
        """Anonymous users should not see the banner (redirected to login)."""
        resp = client.get("/", follow_redirects=False)
        # Grid requires login, so redirects
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("Location", "")

    def test_banner_not_shown_to_companion(self, companion_client):
        """Companions cannot perform onboarding tasks, so never see the banner.

        A companion user shares the linked owner's budget data and cannot
        create accounts, categories, pay periods, salary profiles, or
        templates on their own behalf.  Every step in the welcome checklist
        is therefore inapplicable to them, and the banner must be suppressed
        on the companion landing page.
        """
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200
        assert b"Welcome to Shekel!" not in resp.data


class TestTheChecklistAsksOnlyWhatItDraws:
    """Each checklist fact is asked when read, and at most once per render.

    Ruling ``balance:R-BAL117`` (plan step X-x3, ledger row balance:N-328).  The
    context processor ran five ``EXISTS`` queries on every template an
    owner's request rendered, HTMX fragments that never draw the layout
    included.  Counted
    at the one door every fact queries through, so a fact that bypassed it
    would read as zero here -- which is why the drawn case pins an EXACT
    non-zero count beside the fragment's zero: the instrument is shown to
    count before its zero is believed.
    """

    def test_a_fragment_asks_no_fact(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """An HTMX fragment that draws no layout runs no checklist query."""
        with counting_calls(_FACT_DOOR) as counts:
            resp = auth_client.get("/grid/balance-row?periods=6&offset=0")

        assert resp.status_code == 200
        assert b"Projected End Balance" in resp.data
        assert b"welcome-banner" not in resp.data
        assert counts["_exists"] == 0

    def test_a_drawn_checklist_asks_each_fact_once(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """A page drawing the banner asks each of the four facts exactly once.

        The template reads ``has_salary`` twice -- once through ``complete``,
        once for its own row -- so an unmemoized checklist asks five here
        (measured by swapping ``cached_property`` for ``property``).  With no
        salary profile, ``complete`` short-circuits before ``has_templates``,
        which is why the figure is five and not six.
        """
        with counting_calls(_FACT_DOOR) as counts:
            resp = auth_client.get("/")

        assert resp.status_code == 200
        assert "Welcome to Shekel!" in _banner(resp.data.decode())
        assert counts["_exists"] == 4

    def test_a_complete_checklist_asks_only_the_two_that_decide_it(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """With setup complete, only the salary and template facts are asked."""
        _complete_setup(seed_user)

        with counting_calls(_FACT_DOOR) as counts:
            resp = auth_client.get("/")

        assert resp.status_code == 200
        assert b"Welcome to Shekel!" not in resp.data
        assert counts["_exists"] == 2
