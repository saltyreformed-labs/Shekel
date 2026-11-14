"""Every property and investment surface reports the same modelled figure, and a property secured
by a mortgage reconciles its market value, its secured debt and its equity across surfaces.
"""

from datetime import date
from decimal import Decimal

from app.services import balance_at, home_equity_service, savings_dashboard_service
from app.services.balance_at import BalanceContext
from app.services.liability_sign import owed

from ._helpers import (
    _bctx,
    _assert_surfaces_equal,
    _match_account_data,
    _savings_tile_value,
    _trend_assets_value,
    _investment_dashboard_value,
    _PROPERTY_SURFACE_READERS,
    _INVESTMENT_SURFACE_READERS,
    _modelled_current_balance,
)


class TestPropertyCrossPageEquality:
    """Every property surface reports the same MODELLED market value.

    A single isolated appreciating Property (asserted value V, anchored at the
    current period) must report ONE figure on the /savings tile, the
    home-equity market value (total_debt zero -- no secured loans), the
    year-end asset aggregate, and the net-worth trend's assets at today.

    **The figure is no longer V, and one surface has not followed yet** (plan
    step X-g2b).  Ruling R-Y gives the anchor period its own appreciation, so a
    Property is worth more than the number last typed into it from the day after
    that number was typed.  The cockpit tile and the net-worth trend both read
    the modelled map and must agree to the cent.  The property DETAIL page still
    reads the latest assertion (the cache column until plan step X-f1c3a), which
    is finding **N-83**, recorded and scheduled for its own commit alongside plan
    step X-e rather than fixed inside a money-moving cutover.

    That gap is asserted EXPLICITLY below rather than papered over: it is the
    asserted V exactly, and it is strictly below the modelled figure.  So it
    cannot drift silently, and N-83's own commit FAILS here and has to update
    this class -- which is what a recorded finding should cost its resolver.
    """

    def test_all_surfaces_equal(
        self, app, cross_page_property_ctx, auth_client,
    ):
        """Every property surface returns the SAME modelled value at today.

        The expected figure is read once from the seam and compared against all
        three surfaces, each of which reaches it by a different path (the
        cockpit's projection service, the home-equity producer, the net-worth
        trend).  It is asserted strictly above the asserted V, which is what
        would fail if any surface fell back to the asserted value.
        """
        with app.app_context():
            ctx = cross_page_property_ctx
            expected = _modelled_current_balance(ctx)
            assert expected > ctx["V"], (
                "the anchor period must earn its own days (ruling R-Y)"
            )
            surface_values = {
                name: reader(ctx)
                for name, reader in _PROPERTY_SURFACE_READERS.items()
            }
            # The two SEAM-fed surfaces agree to the cent.
            _assert_surfaces_equal(
                {k: v for k, v in surface_values.items()
                 if k != "property_detail"},
                expected, "property kind",
            )
            # The property detail page still reads the asserted value (N-83).
            assert surface_values["property_detail"] == ctx["V"]
            assert surface_values["property_detail"] < expected

            resp = auth_client.get(f"/accounts/{ctx['account_id']}/property")
            assert resp.status_code == 200, (
                f"/accounts/<id>/property returned {resp.status_code} for "
                "the property kind"
            )


class TestInvestmentCrossPageEquality:
    """Every investment surface reports the same MODELLED balance.

    A single isolated Investment (asserted balance V, anchored at the current
    period with no current-period contribution) must report ONE figure on the
    /savings tile, the investment dashboard, the year-end asset aggregate, and
    the net-worth trend's assets at today.

    **The figure is no longer V** (plan step X-g2b, ruling R-Y): the anchor
    period earns its own days, so an account anchored in it is worth more than
    the number last typed into it.

    Scope note: at anchor==current all four surfaces legitimately resolve
    through the same base producer (the resolver's current-period balance),
    so this class is a four-surface WIRING lock at the agreement point, not a
    cross-producer divergence lock the way the loan boundary is.  The
    cross-producer investment lock -- where the model-from-anchor kernel
    value (the anchor compounded forward to today) diverges from the
    cash-basis tile -- requires an anchor-in-past fixture that diverges on
    today's code, so it is added alongside the savings-tile reroute (the
    Model-from-anchor unification), not here.  The growth math itself is
    covered by tests/test_services/test_balance_at/test_investment.py.
    """

    def test_all_surfaces_equal(
        self, app, cross_page_investment_ctx, auth_client,
    ):
        """Every investment surface returns the SAME modelled balance.

        V = $100,000, anchored at the current period with no contribution.
        The expected figure is read once from the seam and compared against
        each surface, which reach it by three different paths; it is asserted
        strictly above V, so a surface that fell back to the asserted number
        would fail rather than agree by accident.
        """
        with app.app_context():
            ctx = cross_page_investment_ctx
            expected = _modelled_current_balance(ctx)
            assert expected > ctx["V"]  # ruling R-Y: the anchor period accrues
            surface_values = {
                name: reader(ctx)
                for name, reader in _INVESTMENT_SURFACE_READERS.items()
            }
            _assert_surfaces_equal(
                surface_values, expected, "investment kind",
            )

            # Route wiring: the investment detail page renders without
            # raising (parity with the property route check above and
            # ``test_the_loan_surfaces_agree.py``'s loan route check).
            resp = auth_client.get(f"/accounts/{ctx['account_id']}/investment")
            assert resp.status_code == 200, (
                f"/accounts/<id>/investment returned {resp.status_code} for "
                "the investment kind"
            )

    def test_anchor_in_past_tile_adopts_modeled_value(
        self, app, cross_page_investment_past_anchor_ctx,
    ):
        """The /savings tile AND the investment dashboard adopt the modeled value.

        The Level 1 cross-producer investment lock the class docstring above
        defers to the savings-tile reroute.  With the investment anchored 6
        months in the past at a 7% return, the kernel's model-from-anchor map
        compounds the $100,000 opening balance forward to today.  The /savings
        tile, the investment-dashboard headline, the year-end asset aggregate,
        and the net-worth trend all read that SAME modeled value at today --
        and it is strictly greater than the flat $100,000 cash-basis carry the
        pre-reroute surfaces showed, which is what makes the lock
        non-tautological (an unrerouted surface would read the flat $100,000
        and fail).

        The investment-dashboard headline now reads the model-from-anchor
        balance through the ``balance_at`` seam (the dashboards-commit
        reroute), so it joins this lock.  Its forward growth chart still seeds
        from the cash basis -- a separate figure, not asserted here.
        """
        with app.app_context():
            ctx = cross_page_investment_past_anchor_ctx

            # The canonical model-from-anchor value at today, read straight
            # from the seam (the producer the rerouted tile now reads).
            modeled = balance_at.balance_map(
                ctx["account"], _bctx(ctx),
            )[ctx["current_period"].id]

            # Non-tautological AND magnitude-bounded: the modeled balance must
            # compound strictly ABOVE the flat cash-basis carry (so a tile
            # still reading the flat value fails) but stay BELOW a full year of
            # growth at the 7% assumed return -- the anchor is ~6 months in the
            # past, so any correct model-from-anchor value sits in (V0, V0 *
            # 1.07).  Both bounds are hand-computed and independent of the
            # growth engine's per-period day-count convention; the EXACT value
            # is calendar-relative (the fixture builds its periods from today),
            # so it is pinned penny-exact -- with its arithmetic -- in
            # tests/test_services/test_balance_at/test_investment.py (the anchor-in-past
            # kernel-equality cases), not here.  The upper bound is what an
            # "all surfaces == seam" check alone could not give: it catches a
            # shared over-compounding bug (wrong period count or rate) in the
            # seam and every rerouted surface at once.
            v0 = ctx["V0"]
            assert v0 < modeled < v0 * Decimal("1.07"), (
                f"modeled balance {modeled!r} fell outside the hand-computed "
                f"(V0, V0*1.07) band for a ~6-month 7% projection from {v0!r}: "
                "expected strictly above the flat carry but below one full "
                "year's growth"
            )

            # Every kernel-modeled surface -- now including the investment
            # dashboard headline -- reads that same value at today.
            modeled_readers = {
                "savings": _savings_tile_value,
                "investment_dashboard": _investment_dashboard_value,
                "net_worth_trend": _trend_assets_value,
            }
            surface_values = {
                name: reader(ctx) for name, reader in modeled_readers.items()
            }
            _assert_surfaces_equal(
                surface_values, modeled,
                "investment kind (anchor-in-past, model-from-anchor)",
            )


class TestSecuredHomeEquityEquality:
    """The property<->mortgage home-equity relationship reconciles across surfaces.

    Unlike the single-value kinds this is a RELATIONSHIP: a property (market
    value PV) secured by a mortgage (current balance MC).  Three legs must
    agree across surfaces -- the property leg (market value == the property's
    /savings tile == PV), the mortgage leg (total secured debt == the
    mortgage's /savings tile == the loan-detail balance == MC), and the
    equity (PV - MC == the year-end net-worth aggregate == the net-worth
    trend's net at today).
    """

    def test_equity_relationship(self, app, cross_page_secured_ctx):
        """market_value == PV, total_debt == MC, equity == PV - MC everywhere.

        PV = $400,000 ASSERTED, MC = $250,000.  The home-equity producer's
        market_value / total_debt / equity reconcile to the /savings tiles, the
        loan-detail balance, the year-end aggregate, and the net-worth trend
        net.

        **The property leg is still the ASSERTED PV** (finding N-83): the equity
        producer reads the latest assertion (``cash_ledger.resolve_anchor``), where
        the cockpit tile beside it reads the modelled map ruling R-Y moved.  The gap is
        asserted explicitly so it cannot drift, and it is N-83's own commit that
        closes it.  The DEBT leg is untouched and still pins MC exactly -- a
        loan's balance is its schedule, which no part of this step moves, and
        that half is the standing regression gate.
        """
        with app.app_context():
            ctx = cross_page_secured_ctx
            pv, mc = ctx["PV"], ctx["MC"]
            # Finding N-83: the cockpit tile has moved to the modelled value
            # and this producer has not, so they are knowingly apart here.
            assert _modelled_current_balance({
                "user_id": ctx["property_account"].user_id,
                "account": ctx["property_account"],
                "all_periods": ctx["all_periods"],
            }) > pv
            equity = home_equity_service.resolve_home_equity(
                ctx["property_account"],
                BalanceContext.build(ctx["property_account"].user_id),
            )
            dashboard = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(ctx["user_id"]),
            )
            prop_tile = _match_account_data(
                dashboard, ctx["property_account_id"],
            ).current_balance
            mortgage_tile = _match_account_data(
                dashboard, ctx["mortgage_account_id"],
            ).shown_balance
            # The mortgage leg reads what each page SHOWS, which for a debt is
            # what it owes: the tile's shown figure, and the loan-detail page's
            # owed() of the seam scalar (plan step C4; the resolver bundle
            # carries no balance since D2a), which is HELD since plan step
            # credit_card:CC-5-5c -- see ``_loan_detail_value``.
            loan_detail = owed(balance_at.balance_at(
                ctx["mortgage_account"],
                BalanceContext.build(ctx["mortgage_account"].user_id),
                date.today(),
            ))

            # Property leg: the equity producer reads the latest assertion,
            # so it pins the ASSERTED PV, while the cockpit tile beside it now
            # reads the modelled map (ruling R-Y).  Finding N-83 owns the gap;
            # both halves are asserted so neither can drift and so N-83's own
            # commit fails here.
            assert equity.market_value == pv, (
                f"property leg disagreed: market_value={equity.market_value!r}, "
                f"PV={pv!r}"
            )
            assert prop_tile > pv, (
                f"the cockpit tile must carry the modelled value "
                f"(tile={prop_tile!r}, asserted={pv!r})"
            )
            # Mortgage leg: total secured debt == the mortgage tile == the
            # loan-detail balance == MC.
            assert (
                equity.total_debt == mortgage_tile == loan_detail == mc
            ), (
                f"mortgage leg disagreed: total_debt={equity.total_debt!r}, "
                f"savings_tile={mortgage_tile!r}, "
                f"loan_detail={loan_detail!r}, MC={mc!r}"
            )
            # Equity == PV - MC on the equity producer's own (asserted-value)
            # basis, and the TREND nets the modelled value against the same
            # debt -- so the two differ by exactly the N-83 gap measured above,
            # never by anything else.  Asserting the difference rather than the
            # equality is what keeps this a lock while the finding is open.
            series = dashboard["net_worth"].series
            trend_net = series.net[series.current_index]
            # PV - MC = 400000.00 - 250000.00 = 150000.00.
            assert equity.equity == (pv - mc), (
                f"equity disagreed: equity={equity.equity!r}, "
                f"PV-MC={(pv - mc)!r}"
            )
            assert trend_net - equity.equity == prop_tile - pv, (
                f"the trend and the equity card differ by something other than "
                f"finding N-83's gap: trend_net={trend_net!r}, "
                f"equity={equity.equity!r}, tile={prop_tile!r}, PV={pv!r}"
            )
