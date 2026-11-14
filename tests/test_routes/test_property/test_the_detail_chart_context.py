"""The property route hands the band its equity-chart context, resolving once."""

import json
from datetime import date
from decimal import Decimal

from flask import template_rendered

from app.routes.accounts import detail as property_detail_module
from app.services.loan_loaders import load_loan_params
from app.utils.dates import add_months
from tests._test_helpers import create_loan_account, insert_tracking_start_event

from ._helpers import _make_property


class TestPropertyDetailChartContext:
    """The property route hands the band its equity-chart context, resolving once.

    Asserts the ``accounts.property_detail`` render context (captured via the
    ``template_rendered`` signal) carries the ``has_equity_chart`` / ``chart_json``
    / ``chart_state`` contract ``property_detail.js`` reads, and that each secured
    loan is resolved exactly once (D1).
    """

    def _context(self, app, auth_client, account_id):
        """Return the context ``property_detail`` handed ``render_template``."""
        recorded = []

        def _record(sender, template, context, **extra):
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get(f"/accounts/{account_id}/property")
        finally:
            template_rendered.disconnect(_record, app)

        assert response.status_code == 200
        matches = [
            ctx for tmpl, ctx in recorded
            if tmpl.name == "accounts/property_detail.html"
        ]
        assert matches, "property_detail.html did not render"
        return matches[0]

    def test_context_carries_serialized_equity_chart(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """A property with a secured loan exposes the standard chart contract."""
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Mtg",
                principal=Decimal("2400.00"), term=24,
                origination_date=date.today(),
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            prop_id = prop.id

        context = self._context(app, auth_client, prop_id)
        assert context["has_equity_chart"] is True
        assert context["chart_state"] == "standard"
        # A loan originated today has no pre-tracking gap, so no estimated tier.
        assert context["has_estimated_debt"] is False
        chart = json.loads(context["chart_json"])
        assert set(chart) == {
            "labels", "value", "debt", "equity", "today_index", "debt_tier",
        }
        assert (
            len(chart["labels"]) == len(chart["value"]) == len(chart["debt"])
            == len(chart["equity"]) == len(chart["debt_tier"])
        )
        # The value line anchors at the $400,000 market value at today.
        assert chart["value"][chart["today_index"]] == 400000.0

    def test_context_flags_estimated_debt_for_tracking_start_loan(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """A mid-life-imported loan sets ``has_estimated_debt`` True end to end.

        A loan originated 60 months ago but tracked only from 24 months ago has
        a pre-tracking contractual back-projection (the ``estimated`` tier), so
        the route exposes ``has_estimated_debt`` True -- the flag the caption's
        dotted pre-tracking clause renders on.  Wires the producer's tier through
        the route context (the tier math itself is pinned by the producer test).
        """
        with app.app_context():
            today = date.today()
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Imported",
                principal=Decimal("300000.00"), term=360,
                origination_date=add_months(today, -60), payment_day=1,
            )
            loan.collateral_account_id = prop.id
            params = load_loan_params(loan.id)
            insert_tracking_start_event(
                params, Decimal("260000.00"), add_months(today, -24),
            )
            db.session.commit()
            prop_id = prop.id

        context = self._context(app, auth_client, prop_id)
        assert context["has_equity_chart"] is True
        assert context["has_estimated_debt"] is True
        # The dotted clause is present exactly when the flag is set.
        chart = json.loads(context["chart_json"])
        assert "estimated" in chart["debt_tier"]

    def test_resolves_each_secured_loan_once(
        self, app, auth_client, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """Each secured loan is resolved exactly once per GET (D1: no double resolve).

        The equity hero, the LTV, and the debt chart all read the request's ONE
        :class:`~app.services.balance_at.BalanceContext`, so a spy on the
        db-facing resolver (``resolve_loan_bundle`` -- the single load the memo
        wraps) sees exactly one call for the secured loan.

        Spying on the BUNDLE rather than on this route's own import is what makes
        the assertion meaningful: it counts every resolution anywhere in the
        request, so a consumer that re-resolved the loan behind the route's back
        (which ``resolve_home_equity`` and the chart used to do) would be caught.
        """
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Mtg",
                principal=Decimal("2400.00"), term=24,
                origination_date=date.today(),
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            prop_id, loan_id = prop.id, loan.id

        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        from app.services.balance_at import (  # pylint: disable=import-outside-toplevel
            _resolution as resolution_module,
        )

        calls = []
        real_resolve = resolution_module.resolve_loan_bundle

        def _spy(account, ctx):
            calls.append(account.id)
            return real_resolve(account, ctx)

        monkeypatch.setattr(
            resolution_module, "resolve_loan_bundle", _spy,
        )
        response = auth_client.get(f"/accounts/{prop_id}/property")
        assert response.status_code == 200
        assert calls.count(loan_id) == 1

    def test_context_hides_chart_when_no_market_value(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """A property with no market value set exposes the empty-state flag."""
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
                anchor_balance=Decimal("0.00"),
            )
            prop_id = prop.id

        context = self._context(app, auth_client, prop_id)
        assert context["has_equity_chart"] is False
        assert context["has_estimated_debt"] is False
        chart = json.loads(context["chart_json"])
        assert chart["value"] == []
        assert chart["debt"] == []
