"""
Shekel Budget App -- C-30 Analytics Cross-User Ownership Tests

Route-level coverage for commit C-30 of the 2026-04-15 security
remediation plan: ``analytics.calendar_tab`` rejects a cross-user
or non-existent ``account_id`` with 404 (F-039), and the analytics
route that lifts a ``period_id`` off the query string rejects a
cross-user or non-existent one with 404 (F-098).

Threat model.  Both gaps shared the same shape: the route lifted a
foreign-key id straight off the query string and handed it to the
service layer, which itself either silently fell back to a user-
scoped default (calendar) or read victim metadata into the response
label (the period_id vector).  Neither service raised a security
exception, so the IDOR probe surfaced as a normal-looking 200
instead of a 404, masking the boundary breach behind plausible
response bodies.

  * F-039 (calendar): ``calendar_service._resolve_account`` checks
    ownership but on failure silently falls through to the user's
    own default checking account -- the requester sees their own
    data with no error.  An attacker probing for valid victim
    account ids cannot distinguish "owned" from "not owned" but
    also gets no security signal.  The route-level 404 closes the
    silent-fallback gap and emits the standard
    ``access_denied_cross_user`` audit event.

  * F-098 (period_id): a windowed report's txn filter joins
    ``account_id`` (user-owned) with ``pay_period_id`` and so
    returns no rows on a cross-user period_id, BUT the service
    reads ``PayPeriod.start_date`` for the window LABEL without an
    ownership re-check, leaking the victim's start_date into the
    response.  The variance tab that first carried this vector was
    retired at Slice 4 (its route now redirects); the income
    statement inherited the same period_id shape and carries the
    route-boundary guard, so the F-098 coverage lives there now.

The route-level guard delegates ownership to
:func:`app.utils.auth_helpers.get_or_404` (Pattern A in
``auth_helpers``) so the existing structured logging contract
(INFO ``resource_not_found`` for missing pk, WARNING
``access_denied_cross_user`` for cross-user pk) covers both the
analytics routes and every other route that uses the helper.

Test scope.  The calendar finding is exercised through the HTMX
partial path and the direct (non-HTMX) shell path.  The calendar CSV
export was removed with P-AN4, and D13 makes a direct tab GET render
the analytics shell instead of redirecting; the ownership guard runs
before the shell render on that path, so a cross-user account_id still
404s rather than being served a page.  The income statement period_id
guard is exercised through HTML and across window types that ignore
period_id downstream -- the boundary check must not depend on whether
the value happens to be consumed.
"""

from datetime import date

import pytest

from app.extensions import db
from app.models.pay_period import PayPeriod
from tests._test_helpers import freeze_today


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to 2026-03-20 so seed_periods (Jan-May 2026) is current.

    The seeded pay-period range spans 2026-01-02 through roughly
    2026-05-08.  Freezing today inside that window keeps the
    analytics defaults (current period lookup, year selector) on a
    real period regardless of the wall-clock date when the test
    runs.  Mirrors the autouse freeze in ``test_analytics/conftest.py`` so
    fixture behavior is consistent.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


# ── analytics.calendar_tab -- F-039 ──────────────────────────────────


class TestCalendarTabAccountIdOwnership:
    """``analytics.calendar_tab`` rejects a cross-user ``account_id``.

    The service-layer ``_resolve_account`` silently falls back to
    the requester's default checking account when ``account_id``
    fails the ownership check, which masks the IDOR probe behind a
    successful 200.  The route-boundary check elevates the response
    to 404 so the security boundary is observable to monitoring
    and to integration tests.
    """

    def test_own_account_id_html_succeeds(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A's HTMX calendar request with their own ``account_id`` returns 200.

        Establishes the success baseline so a regression that
        over-rejects (404 on every account_id) is visible.
        """
        with app.app_context():
            own_account_id = seed_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?account_id={own_account_id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200, (
                f"Owner's own account_id must return 200, got "
                f"{resp.status_code}"
            )

    def test_own_account_id_non_htmx_renders_shell(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A's direct (non-HTMX) calendar GET with their own ``account_id`` is 200.

        D13 makes a direct tab navigation render the shell (Calendar active)
        after the ownership guard.  This is the success baseline for the
        non-HTMX path: an owned account_id passes the guard and the shell is
        served (never a 404), so a regression that over-rejects is visible.
        """
        with app.app_context():
            own_account_id = seed_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?view=month&year=2026"
                f"&month=1&account_id={own_account_id}",
            )
            assert resp.status_code == 200
            assert "shekel-scroll-pills" in resp.data.decode()

    def test_cross_user_account_id_html_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user,
    ):
        """A's HTMX calendar request with B's ``account_id`` returns 404.

        F-039 baseline: without the route-level guard this would
        return 200 with the requester's default-account data
        (silent service-layer fallback in
        ``calendar_service._resolve_account``).  The 404 follows
        the project security response rule.
        """
        with app.app_context():
            attacker_target = seed_second_user["account"].id
            assert attacker_target != seed_user["account"].id, (
                "fixture sanity: the two seeded users must own "
                "distinct accounts for the cross-user probe"
            )
            resp = auth_client.get(
                f"/analytics/calendar?account_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404, (
                "cross-user account_id must return 404 (security "
                "response rule: 404 for both 'not found' and "
                "'not yours'); got "
                f"{resp.status_code}.  This indicates the silent "
                "service-layer fallback re-emerged as the response."
            )

    def test_cross_user_account_id_non_htmx_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user,
    ):
        """A's direct (non-HTMX) calendar GET with B's ``account_id`` is 404.

        F-039 on the non-HTMX path: D13 renders the shell on a direct
        navigation, but ``_validate_owned_or_abort`` runs BEFORE
        ``_tab_shell_if_not_htmx``, so a cross-user account_id 404s instead of
        being served the shell.  The 404 body is the standard error page, not
        the analytics shell -- the guard fired before any render.
        """
        with app.app_context():
            attacker_target = seed_second_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?view=month&year=2026"
                f"&month=1&account_id={attacker_target}",
            )
            assert resp.status_code == 404
            assert "shekel-scroll-pills" not in resp.data.decode(), (
                "404 response must not carry the analytics shell -- the "
                "ownership guard must fire before the D13 shell render, so a "
                "cross-user probe cannot even reach a rendered page"
            )

    def test_cross_user_account_id_year_view_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user,
    ):
        """A's calendar year view with B's ``account_id`` returns 404.

        ``view=year`` calls ``calendar_service.get_year_overview``
        rather than ``get_month_detail``; both paths share the same
        ``_resolve_account`` fallback and the route-boundary check
        must cover both.
        """
        with app.app_context():
            attacker_target = seed_second_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?view=year&year=2026"
                f"&account_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_nonexistent_account_id_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A non-existent ``account_id`` returns 404 (treats not found same as not yours).

        ``9_999_999`` is a deliberately out-of-range integer that
        no user has ever owned, exercising the
        ``record is None`` branch of ``get_or_404``.  The same 404
        response keeps the client unable to distinguish "no such
        row" from "not yours" by status or body shape.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?account_id=9999999",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_no_account_id_param_uses_default(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A calendar request without ``account_id`` falls back to the user's default.

        Confirms the validation helper bypasses the check when the
        query arg is absent, so the legitimate "no filter --> default
        checking account" service-layer path is preserved.  A
        regression here would 404 every calendar load.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_malformed_account_id_uses_default(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A non-integer ``account_id`` is treated as absent.

        ``request.args.get("account_id", None, type=int)`` returns
        None on parse failure rather than raising; the validator
        treats None as "no filter" and the route renders against
        the user's default account.  This guards against a
        regression that would 404 on malformed input rather than
        falling back -- malformed input is a UX bug, not a
        security incident, so the "not supplied" semantics apply.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?account_id=notanumber",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200


# ── Audit emission -- verify the cross-user log event fires ─────────


class TestCrossUserAuditEvent:
    """Cross-user analytics probes emit ``access_denied_cross_user``.

    The route-boundary helper delegates to ``get_or_404``, which
    is contractually responsible for the structured audit event.
    These tests are smoke checks that confirm the log call still
    fires in the analytics path -- a regression where the helper
    was bypassed (e.g. raw ``db.session.get`` + manual abort)
    would silently drop the SOC alert that depends on this event
    being emitted from every cross-user code path.

    The structured event name is carried in ``LogRecord.event``
    (set via the ``extra`` kwarg in :func:`log_event`), not in
    the message text -- so the assertions read the attribute
    rather than scanning the message string.
    """

    def test_cross_user_account_id_emits_audit_event(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user, caplog,
    ):
        """A cross-user account_id probe writes ``access_denied_cross_user`` at WARNING.

        Asserts on the structured ``event`` attribute rather than
        a mock so the test survives implementation refactors as
        long as the contractual event name is preserved.
        ``caplog`` is configured at WARNING -- the lower INFO
        ``resource_not_found`` event for the missing-pk branch is
        covered by the ``get_or_404`` unit tests, not duplicated
        here.
        """
        import logging  # pylint: disable=import-outside-toplevel

        caplog.set_level(logging.WARNING, logger="app.utils.auth_helpers")
        with app.app_context():
            attacker_target = seed_second_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?account_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

            event_records = [
                r for r in caplog.records
                if getattr(r, "event", None) == "access_denied_cross_user"
            ]
            observed = [
                (r.levelname, r.getMessage(), getattr(r, "event", None))
                for r in caplog.records
            ]
            assert event_records, (
                "cross-user account_id must emit "
                "'access_denied_cross_user' at WARNING; "
                f"observed {observed}"
            )
            # And the event must record both user ids so SOC tooling
            # can correlate the probe to the target.
            ev = event_records[-1]
            assert getattr(ev, "model", None) == "Account"
            assert getattr(ev, "user_id", None) == seed_user["user"].id
            assert (
                getattr(ev, "owner_id", None)
                == seed_second_user["user"].id
            )

    def test_cross_user_period_id_emits_audit_event(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user, seed_second_periods, caplog,
    ):
        """A cross-user period_id probe writes ``access_denied_cross_user`` at WARNING.

        The income statement is the surviving period_id IDOR surface (the
        variance tab that once carried this vector was retired at Slice 4).
        Its route-boundary guard emits the same event name as the account_id
        case so SOC dashboards correlate IDOR attempts under a single rule.
        """
        import logging  # pylint: disable=import-outside-toplevel

        caplog.set_level(logging.WARNING, logger="app.utils.auth_helpers")
        with app.app_context():
            attacker_target = seed_second_periods[0].id
            resp = auth_client.get(
                f"/analytics/income-statement?window=pay_period"
                f"&period_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

            event_records = [
                r for r in caplog.records
                if getattr(r, "event", None) == "access_denied_cross_user"
            ]
            observed = [
                (r.levelname, r.getMessage(), getattr(r, "event", None))
                for r in caplog.records
            ]
            assert event_records, (
                "cross-user period_id must emit "
                "'access_denied_cross_user' at WARNING; "
                f"observed {observed}"
            )
            ev = event_records[-1]
            assert getattr(ev, "model", None) == "PayPeriod"
            assert getattr(ev, "user_id", None) == seed_user["user"].id
            assert (
                getattr(ev, "owner_id", None)
                == seed_second_user["user"].id
            )


# ── analytics.income_statement_tab -- Step 5 (same period_id vector) ──


class TestIncomeStatementTabPeriodIdOwnership:
    """``analytics.income_statement_tab`` rejects a cross-user ``period_id``.

    Build-Order Step 5's income statement shares the F-098 vector: the
    statement's money queries are user-scoped (a foreign period yields an
    empty report), but ``compute_income_statement`` reads the period for
    its window LABEL without an ownership re-check, so a cross-user
    ``period_id`` would leak the victim's period dates into the HTML
    response.  The route validates ``period_id`` at the boundary before
    ``_resolve_window_params`` runs, exactly like ``variance_tab``.
    """

    def test_own_period_id_html_succeeds(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A legitimate same-user ``period_id`` renders successfully."""
        with app.app_context():
            resp = auth_client.get(
                f"/analytics/income-statement?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_cross_user_period_id_html_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_periods,
    ):
        """A cross-user ``period_id`` returns 404 before the label is built."""
        with app.app_context():
            attacker_target = seed_second_periods[0].id
            assert attacker_target != seed_periods[0].id, (
                "fixture sanity: the two seeded users must have "
                "distinct period ids for the probe"
            )
            resp = auth_client.get(
                f"/analytics/income-statement?window=pay_period"
                f"&period_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_cross_user_period_id_does_not_leak_start_date(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_periods,
    ):
        """A 404 response must not embed the victim's period ``start_date``.

        The leak vector is the window label
        (``compute_income_statement`` -> ``_window_label``), built from
        the period's ``start_date`` / ``end_date`` and rendered as
        ``"<start %b %d> - <end %b %d>, <year>"``.  After the boundary 404
        the label helper never runs, so the victim's date -- in that
        rendered form, the shape a real leak would take -- must appear
        nowhere in the response body.
        """
        with app.app_context():
            victim_period = db.session.get(
                PayPeriod, seed_second_periods[0].id,
            )
            # The actual leak token is the label's strftime form, not ISO:
            # asserting on the rendered shape makes this genuinely
            # load-bearing rather than checking a format the label never
            # emits.
            victim_start = victim_period.start_date.strftime("%b %d")

            resp = auth_client.get(
                f"/analytics/income-statement?window=pay_period"
                f"&period_id={victim_period.id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

            body = resp.data.decode(errors="replace")
            assert victim_start not in body, (
                f"Response body leaked victim's start_date {victim_start!r}"
            )

    def test_nonexistent_period_id_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A non-existent ``period_id`` returns 404 (same as 'not yours')."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=pay_period"
                "&period_id=9999999",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_cross_user_period_id_with_month_window_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_periods,
    ):
        """A cross-user ``period_id`` still 404s under a ``month`` window.

        The month window ignores ``period_id`` downstream, but the
        always-validate boundary posture must not depend on whether the
        value is consumed -- the guard fires before the window is resolved.
        """
        with app.app_context():
            attacker_target = seed_second_periods[0].id
            resp = auth_client.get(
                f"/analytics/income-statement?window=month&month=1&year=2026"
                f"&period_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404
