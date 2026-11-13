"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_integration/test_cross_page_balance_equality.py``, moved verbatim by
``tools/split``.
"""

import re
from datetime import date
from decimal import Decimal

from app.services import (
    balance_at,
    calendar_service,
    dashboard_service,
    home_equity_service,
    investment_dashboard_service,
    savings_dashboard_service,
)
from app.services.cash_flow_set import CashFlowSet
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from app.services.liability_sign import owed
from app.services.savings_dashboard_service._display import LIABILITY_KEY
from app.services.savings_dashboard_service._net_worth import _ASSET_BANDS
from tests._test_helpers import current_pay_period


# Each case is a dict so pytest's ``ids=`` parametrize hook can label
# tests by the case's stable short name.  Every Decimal here is built
# via ``Decimal("...")`` from a string per ``docs/coding-standards.md``;
# constructing a Decimal from a Python float (``Decimal(0.1)``) would
# silently introduce floating-point drift and break the hand-computed
# assertions below.
_CASES = [
    {
        "id": "pt01_base",
        # F-009 worked example -- the audit's symptom #1 tuple:
        #   cleared_debit   = 20.00 + 15.71 + 10.00 = 45.71
        #   uncleared_debit = 0
        #   sum_credit      = 0
        #   reservation     = max(500.00 - 45.71 - 0, 0) = 454.29
        #   anchor_balance  = 614.29
        #   balance         = 614.29 - 454.29 = 160.00
        # Pre-Commit-5 the same tuple yielded 114.29 on /savings
        # and any other non-eager-loading surface; the cross-page
        # equality assertion catches that divergence here.
        "anchor_balance": Decimal("614.29"),
        "expense_amount": Decimal("500.00"),
        "entries": [
            (Decimal("20.00"), False, True),
            (Decimal("15.71"), False, True),
            (Decimal("10.00"), False, True),
        ],
        "expected_balance": Decimal("160.00"),
    },
    {
        "id": "zero_anchor",
        # E-12: zero is a value, not "missing".  A surface that
        # treated 0 as "missing" and fell back to a different scalar
        # (or omitted the row) would diverge here.
        #   cleared_debit   = 500.00
        #   reservation     = max(500.00 - 500.00 - 0, 0) = 0
        #   balance         = 0.00 - 0 = 0.00
        "anchor_balance": Decimal("0.00"),
        "expense_amount": Decimal("500.00"),
        "entries": [
            (Decimal("500.00"), False, True),
        ],
        "expected_balance": Decimal("0.00"),
    },
    {
        "id": "negative_overdraft",
        # Negative balances are legitimate (the user is overdrawn);
        # any surface that clamped to >= 0 or absoluted the value
        # would diverge here.
        #   no entries -> reservation = effective_amount = 500.00
        #   balance = 100.00 - 500.00 = -400.00
        "anchor_balance": Decimal("100.00"),
        "expense_amount": Decimal("500.00"),
        "entries": [],
        "expected_balance": Decimal("-400.00"),
    },
    {
        "id": "credit_only",
        # A same-period credit entry zeroes the reservation entirely
        # (the user already got the money back; the envelope's hold
        # on checking is released).
        #   cleared_debit   = 0
        #   uncleared_debit = 0
        #   sum_credit      = 300.00
        #   reservation     = max(300.00 - 0 - 300.00, 0) = 0
        #   balance         = 500.00 - 0 = 500.00
        "anchor_balance": Decimal("500.00"),
        "expense_amount": Decimal("300.00"),
        "entries": [
            (Decimal("300.00"), True, False),
        ],
        "expected_balance": Decimal("500.00"),
    },
    {
        "id": "uncleared_floor",
        # The reduction is a max of "remaining envelope" and
        # "uncleared debit", so an uncleared debit always pulls at
        # least its own amount out of checking even if cleared/credits
        # would otherwise zero the reservation.
        #   cleared_debit   = 50.00
        #   uncleared_debit = 300.00
        #   sum_credit      = 0
        #   reservation     = max(200.00 - 50.00 - 0, 300.00)
        #                   = max(150.00, 300.00) = 300.00
        #   balance         = 500.00 - 300.00 = 200.00
        "anchor_balance": Decimal("500.00"),
        "expense_amount": Decimal("200.00"),
        "entries": [
            (Decimal("50.00"), False, True),
            (Decimal("300.00"), False, False),
        ],
        "expected_balance": Decimal("200.00"),
    },
]


# ── Surface readers ─────────────────────────────────────────────────


# Centralised so the seam-injection test can re-use the exact same
# extraction logic the equality test does; if the readers drifted apart
# the lock would silently weaken.  Each reader returns the same Decimal
# the surface displays for the anchor period (or its calendar-month
# analog).


def _bctx(ctx):
    """Return the read-pass BalanceContext for a cross-page fixture.

    The fixtures hand back a raw ``scenario``; the seam now takes the
    context that owns it (the scenario, the pinned as-of, and the memo
    that resolves each loan exactly once for the pass).
    """
    return BalanceContext(
        user_id=ctx["account"].user_id,
        scenario=ctx["scenario"],
        as_of=date.today(),
    )


def _grid_value(ctx):
    """Read the grid surface's balance for the anchor period.

    Through ``balance_at.grid_balance_view`` -- the entry the grid ROUTE calls
    (``routes/grid/_shared.py:_build_grid_view``) -- so this reader is on the production
    path rather than one producer beneath it.

    **It read ``cash_balance_map`` until plan step X-g3b**, which was harmless
    only while the two agreed.  They agree for a PLAIN account by construction
    (a cash account models no return, so the replay resolves no tier and its
    columns ARE its cash fold, measured unmoved on 60 of 60 columns for every
    real PLAIN account on both databases), and this module's cash matrix is
    PLAIN -- but for a modelled kind they now answer differently BY DESIGN
    (ruling R-W), and a reader on the wrong one would prove nothing about the
    grid while looking like it did.  One reader, on the entry the route uses,
    for the cash matrix and both modelled locks below.
    """
    view = balance_at.grid_balance_view(
        CashFlowSet.single(ctx["account"]), _bctx(ctx),
    )
    return view.columns[ctx["anchor_period"].id].balance


def _grid_current_period_value(ctx):
    """Read the grid's balance for the CURRENT period.

    The modelled per-kind locks below compare every surface at the current
    period (``_modelled_current_balance``), where the cash matrix compares at
    the anchor -- so the grid needs a reader at each convention rather than one
    that silently answers the wrong column.  Both go through the same
    ``grid_balance_view`` entry the route calls; only the column differs.
    """
    view = balance_at.grid_balance_view(
        CashFlowSet.single(ctx["account"]), _bctx(ctx),
    )
    current = current_pay_period(ctx["user_id"])
    return view.columns[current.id].balance


def _dashboard_value(ctx):
    """Read the dashboard's hero balance from the service dict.

    After the Terminal Road rebuild the dashboard's headline balance is
    the pulse hero, served by ``compute_balance_section`` (the narrow
    producer the anchor-edit revert fragment also renders); its
    ``hero["balance"]`` is the figure the page's ``_pulse_balance.html``
    renders verbatim.  Since plan step X-c2b2 it is the hero's own period map at
    the CURRENT period -- so it IS the chart's first point rather than a second
    producer that has to agree with it (finding N-60: the label promises the
    period's END, and the fold made the as-of-today scalar diverge from it by
    the unpaid remainder of the period).  The fixture pins today inside the
    anchor period, so this equals the anchor-period balance.

    It goes through ``resolve_section`` since pay-calendar plan step C2-f2e,
    which is what the ROUTE does: the producer takes the resolved section
    rather than a ``user_id``, and reading this surface any other way would
    grade a door production does not use.  The section is built over
    :func:`_bctx`'s pass, so every surface in this lock still shares one
    scenario and one clock.
    """
    data = dashboard_service.compute_balance_section(
        dashboard_service.resolve_section(_bctx(ctx)),
    )
    return data["hero"]["balance"]


def _savings_value(ctx):
    """Read /savings's per-account ``current_balance`` for our account.

    The /savings dashboard service computes a list of account dicts;
    each carries a ``current_balance`` Decimal that the template
    renders directly into the per-account card.
    """
    data = savings_dashboard_service.compute_dashboard_data(
        BalanceContext.build(ctx["user_id"]),
    )
    matches = [
        ad for ad in data["account_data"]
        if ad.account.id == ctx["account_id"]
    ]
    assert len(matches) == 1, (
        f"/savings account_data did not surface exactly one entry "
        f"for account_id={ctx['account_id']}: matched {len(matches)}"
    )
    return matches[0].current_balance


def _accounts_checking_value(ctx):
    """Read the /accounts cash-detail balance the ROUTE actually rendered.

    Unlike the other cash readers, this one does not re-call the seam and
    reconstruct the number the page ought to show -- it drives the real
    ``GET /accounts/<id>/details`` request (the Fable 5 overhaul's unified
    cash-detail page) and reads the balance the template rendered, off the
    ``data-current-balance`` hook the hero balance carries (the raw Decimal,
    not the formatted ``money()`` string, so the read is immune to
    display-format changes).  This is the L6 route-render lock: a reader that
    reconstructs the seam call would still match even if the route drifted onto
    a different producer or a template bug mis-rendered the figure, because the
    reader hardcodes the correct call.  Reading the rendered attribute makes
    the cash-detail surface a genuinely distinct check (it was formerly a
    byte-identical twin of :func:`_grid_value`'s ``cash_balance_map`` call) AND
    catches a route that stopped rendering the seam's value.

    The fixture pins ``today`` inside the anchor period, so the route's
    ``current_period`` IS the anchor period and the rendered ``current_bal``
    (``balances.get(current_period.id)``) equals ``balances[anchor_period.id]``
    -- the same Decimal every other surface must agree on.
    """
    resp = ctx["client"].get(f"/accounts/{ctx['account_id']}/details")
    assert resp.status_code == 200, (
        f"/accounts/{ctx['account_id']}/details returned {resp.status_code}; "
        "the cash-detail route must render the symptom-tuple data without "
        "raising (the empty-state / falsy guards must not be zeroing the balance)"
    )
    match = re.search(r'data-current-balance="([^"]*)"', resp.text)
    assert match is not None, (
        "the cash-detail response carried no data-current-balance attribute -- "
        "the hero balance hook this route-render lock reads is gone; either the "
        "template dropped it or the route rendered the empty-state branch"
    )
    return Decimal(match.group(1))


def _calendar_value(ctx):
    """Read the calendar surface's ``projected_end_balance`` for the anchor month.

    The calendar service projects via the seam's
    :func:`app.services.balance_at.cash_balance_at` at the calendar month-end --
    the same fold read at one date (``balance_as_of_date``, the producer it
    called before plan step X-c2b2, is deleted).  The fixture makes the
    anchor period's last covered day the last day of its calendar month, so
    the C9-3 boundary invariant guarantees the calendar's
    ``projected_end_balance`` equals the resolver's anchor-period
    balance for the same data.
    """
    detail = calendar_service.get_month_detail(
        user_id=ctx["user_id"],
        year=ctx["year"],
        month=ctx["month"],
        account_id=ctx["account_id"],
        user_settings=None,
    )
    return detail.projected_end_balance


_SURFACE_READERS = {
    "grid": _grid_value,
    "dashboard": _dashboard_value,
    "savings": _savings_value,
    "accounts_checking": _accounts_checking_value,
    "calendar": _calendar_value,
}


def _all_surface_values(ctx):
    """Return the surface -> Decimal mapping the equality test asserts on.

    Centralised so the seam-injection negative control can compose the
    same readers against a monkey-patched consumer and observe the
    divergence at the same level the equality assertion fires.
    """
    return {name: reader(ctx) for name, reader in _SURFACE_READERS.items()}


def _assert_surfaces_equal(surface_values, expected, label):
    """Assert every surface in *surface_values* returns the identical *expected*.

    The one dual-assert behind every cross-page equality test -- the cash
    matrix here AND the per-kind locks (loan / property / investment /
    secured) -- and the seam-injection negative controls that drive it:

      (a) every surface equals *expected*, with a message naming the
          offending surface and its value; and
      (b) the set of all surface values is exactly ``{expected}`` (the
          cross-page invariant -- no two surfaces produced different
          Decimals, even if none individually missed *expected*).

    *label* names the case in every message so a failure -- real or
    injected by a negative control -- points at the surface, its value, and
    the case.  The message shape is what the negative controls assert on
    (the patched surface name and its wrong value both appear), so it must
    stay stable.

    Args:
        surface_values: ``{surface_name: Decimal}`` from a reader dict.
        expected: The single Decimal every surface must return.
        label: A short case label woven into each assertion message.
    """
    for name, value in surface_values.items():
        assert value == expected, (
            f"surface {name!r} returned {value!r}; expected {expected!r} "
            f"for {label}.  All surface values: {surface_values!r}"
        )
    unique_values = set(surface_values.values())
    assert unique_values == {expected}, (
        f"surfaces produced more than one Decimal ({unique_values!r}) "
        f"for {label} -- this is the cross-page divergence HIGH-01 locks"
    )


# ── Per-kind cross-page locks: loan / property / investment / secured ──
#
# The cash matrix above locks the five checking surfaces.  These classes
# extend the same cross-page contract to the recompute-at-read kinds the
# balance_at seam (Level 1) will reroute -- loan, property (appreciating),
# investment -- plus the property<->mortgage home-equity relationship.  Each
# per-kind fixture isolates ONE account of that kind (the seed_user checking
# is neutralised to $0) because two of the surfaces (year-end net worth and
# the savings net-worth trend) are AGGREGATE-only: they sum over ALL of the
# user's accounts, so a single-account fixture is the only way to read one
# kind's contribution.  Each reader reads the figure its PAGE SHOWS, so the
# equality assertion stays uniform: every loan reader reads what the loan
# OWES (the seam reports a loan HELD, negative when owed, since plan step
# credit_card:CC-5-5c, ruling R-CC47), the loan trend reader reads the
# liability band, and the asset readers read the balance.


def _match_account_data(dashboard_data, account_id):
    """Return the single ``/savings`` account_data entry for *account_id*.

    The shared per-account tile lookup the per-kind savings readers reuse;
    asserts exactly one entry matched so a missing or duplicated account
    fails loudly rather than silently reading the wrong tile.
    """
    matches = [
        ad for ad in dashboard_data["account_data"]
        if ad.account.id == account_id
    ]
    assert len(matches) == 1, (
        f"/savings account_data did not surface exactly one entry for "
        f"account_id={account_id}: matched {len(matches)}"
    )
    return matches[0]


def _net_worth_series(ctx):
    """Return the ``/savings`` net-worth trend series dict for the user.

    Carries the parallel ``net`` / ``assets`` / ``liabilities`` lists plus
    ``current_index`` (the position of today's period in the trend window);
    the per-kind trend readers index into it at ``current_index``.
    """
    data = savings_dashboard_service.compute_dashboard_data(
        BalanceContext.build(ctx["user_id"]),
    )
    return data["net_worth"].series


def _savings_tile_value(ctx):
    """Read the figure the ``/savings`` per-account tile SHOWS for the account.

    Shared by all three single-account kinds (loan / property / investment):
    the tile shows ``AccountProjection.shown_balance`` -- what a debt OWES and
    every other account's balance (ruling R-CC47) -- so one reader serves
    every kind's ``savings`` surface.  It read ``current_balance`` until plan
    step credit_card:CC-5-5c, when that was a loan's owed figure; it is HELD
    since then (``-C`` for a loan owing C), and for a property or an
    investment the shown figure IS that balance.
    """
    data = savings_dashboard_service.compute_dashboard_data(
        BalanceContext.build(ctx["user_id"]),
    )
    return _match_account_data(data, ctx["account_id"]).shown_balance


def _trend_assets_value(ctx):
    """Sum the net-worth trend's ASSET bands at the current index.

    The producer published a parallel ``assets`` total until plan step X-s1
    deleted it: it was the sum of these bands by construction (one per-period
    sum feeds both), and once the chart payload stopped carrying it across it
    had no ``app/`` reader at all.  Summing the bands here reads the figure
    from where it is actually derived, so this oracle now compares the other
    pages against the series the chart really draws.
    """
    series = _net_worth_series(ctx)
    index = series.current_index
    return sum(
        (series.composition[band][index] for band in _ASSET_BANDS),
        Decimal("0.00"),
    )


def _trend_liabilities_value(ctx):
    """Read the net-worth trend's LIABILITY band at the current index.

    The band is what the liabilities OWE -- ``owed()`` of each HELD balance
    since plan step credit_card:CC-5-5c, so a credit reads negative (it was
    ``abs(balance)`` before) -- so for an isolated loan it equals what that
    loan owes, the figure every other loan reader returns.  It was also published
    as a parallel ``liabilities`` total until plan step X-s1 deleted that copy
    (see :func:`_trend_assets_value`).
    """
    series = _net_worth_series(ctx)
    return series.composition[LIABILITY_KEY][series.current_index]


def _loan_detail_value(ctx):
    """Read what the loan-detail page shows: what the loan OWES today.

    The service-level equivalent of ``GET /accounts/<id>/loan``: since plan
    step C4 the loan detail page reads ``balance_at.balance_at`` (the fold),
    and since D2a the resolver bundle carries no balance at all.  The seam
    reports the loan HELD since plan step credit_card:CC-5-5c (ruling
    R-CC47), and the page renders ``_RouteLoanContext.current_owed`` --
    ``owed()`` of that scalar at today -- so this reads the same composition.
    It cannot call the property itself: ``_load_route_context`` builds that
    context from ``current_user`` plus the loan's ``LoanContext`` and figures,
    not from ``(account, ctx)`` in one call.
    """
    return owed(
        balance_at.balance_at(ctx["account"], _bctx(ctx), date.today()),
    )


def _property_detail_value(ctx):
    """Read the property-detail home-equity market value (the anchor balance).

    The service-level equivalent of ``GET /accounts/<id>/property``:
    ``resolve_home_equity(...).market_value`` is the property's latest
    assertion; with no secured loans its ``total_debt`` is
    zero, so market value alone is the cross-page value.
    """
    return home_equity_service.resolve_home_equity(
        ctx["account"], _bctx(ctx),
    ).market_value


def _investment_dashboard_value(ctx):
    """Read the investment-dashboard producer current_balance for the account."""
    return investment_dashboard_service.compute_dashboard_data(
        ctx["user_id"], ctx["account"],
    )["current_balance"]


def _loan_schedule_table_value(ctx):
    """Read the amortization table's LAST CONFIRMED row balance (C11 surface).

    The loan-detail schedule tab renders ``LoanState.schedule``; its last
    confirmed row's ``remaining_balance`` is the balance the table shows the
    user beside their most recent real payment.  Since the C11 history read
    switch those confirmed rows are ledger-derived, so this must equal the
    loan card / tile to the penny.  A row's ``remaining_balance`` is what the
    loan OWES.  A loan with no confirmed row yet reads what the card shows
    (an empty table shows no history) -- ``owed()`` of the seam's scalar,
    which is HELD since plan step credit_card:CC-5-5c (ruling R-CC47) --
    keeping the reader total, and in one sign, for the on-schedule kind test
    too.
    """
    resolved = resolved_loan(ctx["account"], _bctx(ctx))
    assert resolved is not None, (
        f"resolved_loan returned None for loan "
        f"account_id={ctx['account_id']}"
    )
    confirmed_rows = [
        row for row in resolved.state.schedule if row.is_confirmed
    ]
    if not confirmed_rows:
        return owed(
            balance_at.balance_at(ctx["account"], _bctx(ctx), date.today()),
        )
    return confirmed_rows[-1].remaining_balance


# Per-kind reader dicts.  Each maps a surface name to a reader returning the
# figure that page SHOWS, which is ONE quantity per kind -- what the loan OWES
# (positive when owed), and the account's balance for property and
# investment -- so one ``_assert_surfaces_equal`` call locks every kind.  The
# shared ``_savings_tile_value`` serves the ``savings`` surface in all three.
_LOAN_SURFACE_READERS = {
    "savings": _savings_tile_value,
    "loan_detail": _loan_detail_value,
    "net_worth_trend": _trend_liabilities_value,
    "schedule_table": _loan_schedule_table_value,
}


# The GRID joins both modelled locks at plan step X-g3b (ruling R-W): it renders
# the modelled balance for every kind now, so it is a surface these figures must
# agree on rather than one holding a deliberate cash-basis gap.  It reads through
# ``_grid_value``, which calls ``grid_balance_view`` -- NOT ``cash_balance_map``,
# which after that step answers a modelled account differently by design.
_PROPERTY_SURFACE_READERS = {
    "savings": _savings_tile_value,
    "grid": _grid_current_period_value,
    "property_detail": _property_detail_value,
    "net_worth_trend": _trend_assets_value,
}


_INVESTMENT_SURFACE_READERS = {
    "savings": _savings_tile_value,
    "grid": _grid_current_period_value,
    "investment_dashboard": _investment_dashboard_value,
    "net_worth_trend": _trend_assets_value,
}


def _modelled_current_balance(ctx) -> Decimal:
    """Return the account's modelled balance at the current period, from the seam.

    The independent oracle the cross-page classes compare their surfaces
    against since plan step X-g2b.  It used to be the fixture's asserted V,
    which worked only while an account anchored in the current period earned
    nothing in it -- the state ruling R-Y deleted.  Reading it once HERE, from
    the seam's own per-period map, keeps the classes what they are: a lock that
    every surface reaches ONE figure, by three different paths.
    """
    balance_ctx = BalanceContext.build(ctx["user_id"])
    current = current_pay_period(ctx["user_id"])
    return balance_at.balance_map(
        ctx["account"], balance_ctx,
    )[current.id]
