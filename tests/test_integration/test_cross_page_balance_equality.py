"""
Shekel Budget App -- Cross-Page Balance Equality Lock (HIGH-01, Commit 11)

The single regression test the developer's two worst reported symptoms
lacked.  Symptom #1 ($160 on the grid vs $114.29 on /savings for the
same Projected envelope expense with cleared entries) and symptom #5
(/accounts matches nowhere) had zero falsifying coverage before this
commit: the three audit-plan-mandated cross-page-equality greps over
the pre-commit ``tests/`` tree returned exit-1 (zero matches), and the
near-miss ``test_checking_detail_matches_grid_balance`` recomputes its
own entries-absent balance instead of rendering a second page (it
passes against the divergent code).

This module's contract: for one Account / Scenario / pay-period
configuration with a Projected envelope expense carrying any
combination of cleared / uncleared / credit entries, every balance-
rendering surface MUST return the identical Decimal:

  1. Grid                    -- ``GET /grid`` + the seam's cash-flow entry
                                ``balance_at.cash_balance_map`` (the FOLD since
                                plan step X-c2b2).
  2. /savings                 -- ``savings_dashboard_service`` +
                                ``GET /savings``.
  3. /accounts cash detail    -- the balance ``GET /accounts/<id>/details``
                                renders, read off its ``data-current-balance``
                                hook (the L6 route-render lock: the ONE surface
                                verified against real route output, not a
                                re-call of the seam beneath it).
  4. Dashboard               -- ``dashboard_service.compute_balance_section``
                                (the pulse hero) + ``GET /dashboard``.
  5. Calendar month-end      -- ``calendar_service.get_month_detail``'s
                                ``projected_end_balance`` at the
                                calendar month-end of the anchor
                                period (the C9-3 boundary invariant
                                guarantees equality with the
                                resolver's anchor-period balance).

Five parameter cases lock the formula, not one number: the F-009
worked example (PT-01 base), zero anchor (E-12 "zero is a value"),
negative overdraft balance, credit-only entries (reservation zeroed
by a same-period credit), and uncleared-floor (the
``max(estimated - cleared_debit - sum_credit, uncleared_debit)``
floor that no cleared/credit drift can squeeze below).

The subtotal-remainder assertion
(:class:`TestSubtotalReconciliation`) closes Q-10 / E-25's same-formula
invariant on ruling R-K's basis: for one ``GridColumn`` per period,
``balance[p] - balance[p-1] == net[p] + period_timing[p] + book_vs_bank[p]``
to the penny, with
the remainder pinned per case as the fixture's own true-up.  When this fails the
grid's subtotal rows and balance row have re-grown the F-002 Pair C / F-004
same-page divergence (the inline ``sum(... effective_amount ...)`` loop the
pre-Commit-10 grid had).

The seam-injection negative-control test
``test_invariant_fails_if_seam_reintroduced`` proves the lock is
load-bearing, not a coincidence: monkey-patching one consumer to
bypass the balance-at seam and report ``effective_amount`` directly
makes the cross-page equality assertion fail, which is the failure
mode the developer needs to see in CI before a regression ships.

Test IDs are C11-1..C11-6 mapping to the remediation plan's
Commit 11 specification.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

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
from app.services.liability_sign import owed, shown_figure
from app.services.savings_dashboard_service._display import LIABILITY_KEY
from app.services.savings_dashboard_service._net_worth import _ASSET_BANDS
from tests._test_helpers import (
    current_pay_period,
    freeze_today,
)


@pytest.fixture(autouse=True)
def _freeze_today_mid_month(monkeypatch):
    """Run this module MID-MONTH, because its periods are calendar months.

    **Found 2026-07-31 with six of this module's tests red on that day, at
    ``HEAD`` and at every commit for weeks, while CI had last been green on
    2026-07-28.**  ``_build_cross_page_calendar_periods`` builds one period per
    calendar month and the anchor is the month containing today, so on the LAST
    day of any month ``date.today() == anchor.end_date`` -- and every
    hand-computed ``expected_balance`` in ``_CASES`` assumes today is inside its
    period, not on its final day.  The suite was a time bomb that fired roughly
    12 days a year and blocked the merge gate on each of them.

    **The production behaviour it trips over was MEASURED CORRECT and is NOT
    changed**, which is the only reason freezing the clock here is a fix and not
    a cover-up.  Ruling R-G lands a still-projected row on
    ``max(attribution_day, as_of + 1)`` -- a plan cannot have already happened
    -- so on a period's last day ``as_of + 1`` is outside the period and the
    whole remaining plan legitimately rolls into the next one.  The column then
    equals the settled balance, and the money is conserved: measured on the
    prod-shape clone, the current column went ``$406.92 -> $2,824.26`` on the
    period's last day while every later column and the horizon's last stayed
    put.  Relaxing the floor to ``as_of`` was tried and REJECTED by measurement:
    it moves the real Checking account's balance TODAY from ``$2,824.26`` to
    ``$2,978.28``, counting income merely expected today as money in hand, and
    ``$2,824.26`` is the figure verified to the cent against the persisted
    double-entry ledger.

    The 15th rather than a pinned calendar date (the
    ``tests/test_services/conftest.py`` precedent freezes to ``2026-03-20``):
    the property this module needs is "strictly inside the month", and deriving
    it from the real month keeps the fixture's year range current instead of
    ageing into a second kind of rot.
    """
    freeze_today(monkeypatch, date.today().replace(day=15))


def test_the_frozen_clock_is_strictly_inside_its_month():
    """The precondition the module's expectations rest on, asserted.

    A fixture that silently stops exercising what it names is this codebase's
    signature defect.  Without this, a future edit to the freeze above could put
    the clock back on a month boundary and every ``expected_balance`` would be
    graded against the wrong day again -- silently for 11 months of the year.
    """
    today = date.today()
    first_of_month = today.replace(day=1)
    next_month = (
        first_of_month.replace(year=today.year + 1, month=1)
        if today.month == 12
        else first_of_month.replace(month=today.month + 1)
    )
    last_of_month = next_month - timedelta(days=1)
    assert first_of_month < today < last_of_month, (
        f"the module clock is frozen to {today}, which is a month BOUNDARY; "
        "every hand-computed expected_balance here assumes today is strictly "
        "inside its calendar-month pay period"
    )


# ── Parameter matrix (cases 1..5 of the plan's Commit 11 spec) ─────


# The ``seed_user`` factory's origination anchor for the checking account
# (``conftest`` creates it with ``anchor_balance=Decimal("1000.00")``).
# ``seed_cross_page_account`` leaves that assertion in place and APPENDS the
# case's own, so the difference between the two is a true-up that lands in the
# anchor period -- which ruling R-K's Reconciliation row carries and
# ``TestSubtotalReconciliation`` pins per case.
_FACTORY_ORIGINATION_BALANCE = Decimal("1000.00")

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


# ── C11-1..C11-4: all surfaces equal across the parameter matrix ───


class TestCrossPageBalanceEquality:
    """All six balance-rendering surfaces return the identical Decimal.

    The HIGH-01 / R-6 regression lock the suite lacked: every
    parameter row in ``_CASES`` exercises one symptom-tuple shape and
    asserts every surface produces the case's hand-computed expected
    Decimal AND that all six surfaces produce identical values.  The
    cases collectively lock the formula
    ``balance = anchor - max(estimated - cleared_debit - sum_credit,
    uncleared_debit)`` -- one number per case is not enough to lock
    the formula because the same number can survive many wrong
    formulas (a producer that always returned the anchor would pass a
    single-row test but fail ``zero_anchor`` and ``negative_overdraft``).
    """

    @pytest.mark.parametrize(
        "case", _CASES, ids=[c["id"] for c in _CASES],
    )
    def test_all_surfaces_equal(
        self,
        app,
        seed_cross_page_account,
        auth_client,
        case,
    ):
        """C11-1..C11-4: every surface returns the case's expected Decimal.

        Hand-computed arithmetic for each case is in the case dict's
        comment block above.  The assertion is dual: (a) every surface
        equals the case's ``expected_balance``, (b) the surfaces all
        equal each other (the cross-page invariant E-04 / HIGH-01
        governs).  Asserting both catches the failure mode "every
        surface drifted by the same wrong amount" -- the cross-page
        equality alone would silently miss it; the
        ``expected_balance`` pin alone would silently miss
        per-surface drifts that cancel in the aggregate.

        Route coverage is layered (the L6 fix).  The ``accounts_checking``
        reader drives the real ``GET /accounts/<id>/details`` and asserts the
        balance the route RENDERED -- read off its ``data-current-balance``
        hook -- so the equality set includes one surface verified against
        actual route output.  This closes the gap an earlier revision left: a
        service-level reader that re-calls the seam cannot catch a route that
        drifted onto a different producer or mis-rendered the figure, because
        it hardcodes the correct call; a render read can.  Reading a
        raw-Decimal ``data-`` attribute (not the formatted currency text)
        keeps that read robust, so it is NOT the fragile HTML scraping the
        earlier revision rejected on fragility grounds.  The other HTTP
        surfaces (``GET /grid``, ``GET /savings``, ``GET /dashboard``) still
        get a 200 liveness check below; their rendered Decimal equals the
        service reader's by the same seam the cash-detail route now proves is
        wired.
        """
        with app.app_context():
            ctx = seed_cross_page_account(
                anchor_balance=case["anchor_balance"],
                expense_amount=case["expense_amount"],
                entries=case["entries"],
            )
            # The accounts cash-detail reader drives the real route, so give it
            # the authenticated client (the same fixture the liveness checks
            # below use).
            ctx["client"] = auth_client

            surface_values = _all_surface_values(ctx)

            # Dual-assert (every surface == expected AND the set is a
            # singleton) via the shared helper, so the cash matrix and the
            # per-kind locks fire the identical cross-page invariant.
            expected = case["expected_balance"]
            _assert_surfaces_equal(
                surface_values, expected, f"case {case['id']!r}",
            )

            # Route-level liveness for the SERVICE-read surfaces: each returns
            # 200 for the same fixture, so the route plumbing does not raise on
            # the symptom-tuple data even when the Decimal is negative or zero
            # (the empty-state / falsy guards the routes used to have are not
            # silently zeroing the balance).  ``GET /accounts/<id>/details`` is
            # exercised AND value-checked by ``_accounts_checking_value`` above,
            # so it is not re-fetched here.
            resp = auth_client.get("/grid")
            assert resp.status_code == 200, (
                f"/grid returned {resp.status_code} for case {case['id']!r}; "
                "route surface is the primary user-facing path and must "
                "render the symptom-tuple data without raising"
            )
            resp = auth_client.get("/savings")
            assert resp.status_code == 200, (
                f"/savings returned {resp.status_code} for case {case['id']!r}"
            )
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200, (
                f"/dashboard returned {resp.status_code} for case {case['id']!r}"
            )


# ── C11-5: subtotal reconciles to balance delta on every page ──────


class TestSubtotalReconciliation:
    """The grid column's rows reconcile to its balance on every parameter case.

    E-25 / Q-10 / F-002 Pair C: ONE valued row set drives both the per-period
    subtotal rows and the balance row, so the period-to-period balance delta
    must equal the column's net plus ruling R-K's remainder plus the accrual, to
    the penny.  Before Commit 10 the grid's inline ``sum(...
    effective_amount ...)`` subtotal loop violated this whenever a
    Projected envelope expense carried cleared entries -- the subtotal
    row reported $500 while the balance row reflected the entries-aware
    $454.29 reduction.  This test fires the moment a future edit
    re-grows that divergence.

    **Read off ONE ``GridBalanceView`` since plan step X-c2b3.**  It differenced
    ``_cash_engine.balances_for`` against ``cash_ledger.period_subtotal``, and
    both deleted -- the balance replaced by the fold at X-c2b2, the subtotal by
    ``cash_period_view``, which is R-K's basis and carries the remainder term the
    old two-producer form had no name for.  The identity's SHAPE therefore
    changed with it: what a subtotal counts is no longer "the unpaid rows" but
    "every row attributed here", and what the balance counts is money that
    MOVED, so the two reconcile through a named remainder rather than exactly.
    """

    @pytest.mark.parametrize(
        "case", _CASES, ids=[c["id"] for c in _CASES],
    )
    def test_subtotal_reconciles_balance_delta(
        self,
        app,
        seed_cross_page_account,
        case,
    ):
        """C11-5: ``balance delta == net[p] + timing[p] + book_vs_bank[p]``.

        For each case, read ONE grid view over the whole period list and
        reconcile the anchor period's column against its predecessor's, then the
        post-anchor period's against the anchor's.

        The post-anchor period carries NO transactions of its own, so its net is
        exactly zero and its balance equals the anchor period's -- the
        no-transaction reconciliation step, and the case that would catch a
        remainder quietly absorbing a mis-grouped row.

        **The anchor period's remainder is the fixture's own TRUE-UP, and
        pinning its exact value is what keeps the identity from being
        untestable.**  ``seed_cross_page_account`` leaves the ``seed_user``
        factory's ``$1,000.00`` origination assertion in place and appends a
        SECOND one at the case's ``anchor_balance`` (latest-wins, E-19), both
        landing in the anchor period.  The opening books nothing in its own
        period, so the re-assertion's correction --
        ``anchor_balance - $1,000.00`` -- is money that moved through the column
        with no row to explain it: exactly what ruling R-K's remainder rows
        exist to show, here on five different anchor balances.  Asserting it as
        a computed figure rather than letting it fall out of the identity is
        Section 7.2's rule: a remainder read as a residual makes the identity
        arithmetically true, and would silently absorb a mis-grouped row.

        **It lands on ``book_vs_bank`` specifically** (plan step S1-c, ruling
        R-DH (f)).  The single remainder split in two, and this fixture makes
        exactly ONE of them non-zero: a balance ASSERTION with no row beside it,
        which is a book-vs-bank fact.  ``period_timing`` is pinned at ``$0.00``
        on both columns, and that assertion is what would catch a producer
        attributing a true-up to a timing difference -- a different diagnosis
        with different advice attached, and one the combined figure could not
        tell apart.

        The POST-anchor period carries neither an assertion nor a row, so both
        its remainders and its net are exactly zero -- the case that would catch
        a remainder leaking forward.  A PLAIN account accrues nothing, asserted
        on both columns.
        """
        with app.app_context():
            ctx = seed_cross_page_account(
                anchor_balance=case["anchor_balance"],
                expense_amount=case["expense_amount"],
                entries=case["entries"],
            )

            columns = balance_at.grid_balance_view(
                CashFlowSet.single(ctx["account"]), _bctx(ctx),
            ).columns

            anchor_idx = next(
                i for i, p in enumerate(ctx["all_periods"])
                if p.id == ctx["anchor_period"].id
            )
            assert anchor_idx > 0, (
                "fixture invariant: the anchor period must have a predecessor "
                "to difference against"
            )
            assert anchor_idx + 1 < len(ctx["all_periods"]), (
                "fixture invariant: anchor period must not be the last "
                "period in the projected window"
            )
            prior_period = ctx["all_periods"][anchor_idx - 1]
            next_period = ctx["all_periods"][anchor_idx + 1]

            anchor_column = columns[ctx["anchor_period"].id]
            next_column = columns[next_period.id]

            # The anchor column's remainder holds BOTH assertions the fixture
            # puts in this period, and naming them separately is what keeps
            # this a hand-computed claim (plan step X-f3c-2a).
            #
            #   (a) the fixture's RE-ASSERTION: the factory's $1,000.00
            #       origination corrected to the case's anchor;
            #   (b) the ORIGINATION's own correction, which is the sum of the
            #       case's CLEARED DEBIT entries.  A cleared purchase is a cash
            #       fact in its own right (ruling R-FM) dated before this
            #       fixture's origination assertion, so the books carry it and
            #       the origination then corrects it straight back out.  That
            #       is the pre-opening double count plan step X-f3c-2b closes,
            #       and until X-f3c-2a it was invisible here: the fold moved
            #       the origination's whole delta into its seed (ruling R-I)
            #       and this row excluded it by construction.
            #
            # The cases with no cleared debit -- ``negative_overdraft`` and
            # ``credit_only`` -- put $0.00 in (b), which is why they read the
            # same figure before and after that step.
            expected_trueup = (
                case["anchor_balance"] - _FACTORY_ORIGINATION_BALANCE
            )
            expected_opening = sum(
                (
                    amount
                    for amount, is_credit, is_cleared in case["entries"]
                    if is_cleared and not is_credit
                ),
                Decimal("0.00"),
            )
            assert anchor_column.book_vs_bank == (
                expected_trueup + expected_opening
            ), (
                f"case {case['id']!r}: anchor column book-vs-bank "
                f"{anchor_column.book_vs_bank!r} != the fixture's true-up "
                f"{expected_trueup!r} plus its origination's own correction "
                f"{expected_opening!r} "
                f"({case['anchor_balance']!r} asserted over the factory's "
                f"{_FACTORY_ORIGINATION_BALANCE!r} origination).  R-K's "
                f"book-vs-bank row exists to carry exactly this."
            )
            # Every row in this fixture is budgeted to, and lands in, the
            # anchor period, so NOTHING is a timing difference.  A figure
            # here would mean the true-up had been booked to the wrong row.
            assert anchor_column.period_timing == Decimal("0.00"), (
                f"case {case['id']!r}: anchor column carries a "
                f"{anchor_column.period_timing!r} timing remainder, but every "
                f"row is budgeted to and lands in this period"
            )
            # The post-anchor period has neither an assertion nor a row, so
            # nothing can leak into either of its remainders.
            assert next_column.period_timing == Decimal("0.00"), (
                f"case {case['id']!r}: post-anchor column has a "
                f"{next_column.period_timing!r} timing remainder, but no row "
                f"is attributed to it"
            )
            assert next_column.book_vs_bank == Decimal("0.00"), (
                f"case {case['id']!r}: post-anchor column has a "
                f"{next_column.book_vs_bank!r} book-vs-bank remainder, but "
                f"no assertion falls inside it"
            )
            for label, column in (
                ("anchor", anchor_column), ("next", next_column),
            ):
                assert column.accrual == Decimal("0.00"), (
                    f"case {case['id']!r}: {label} column carries an accrual "
                    f"({column.accrual!r}) on a PLAIN account"
                )
                assert column.contribution == Decimal("0.00"), (
                    f"case {case['id']!r}: {label} column carries a modelled "
                    f"contribution ({column.contribution!r}) on a PLAIN account"
                )

            anchor_delta = (
                anchor_column.balance - columns[prior_period.id].balance
            )
            assert anchor_delta == (
                anchor_column.net + anchor_column.period_timing
                + anchor_column.book_vs_bank
            ), (
                f"case {case['id']!r}: anchor-period balance delta "
                f"{anchor_delta!r} != net {anchor_column.net!r} + "
                f"period_timing {anchor_column.period_timing!r} + "
                f"book_vs_bank {anchor_column.book_vs_bank!r}.  The "
                f"balance row and the subtotal rows disagree on the "
                f"entries-aware formula -- the F-002 Pair C / F-004 same-page "
                f"divergence has re-grown."
            )

            forward_delta = next_column.balance - anchor_column.balance
            assert forward_delta == (
                next_column.net + next_column.period_timing
                + next_column.book_vs_bank
            ), (
                f"case {case['id']!r}: post-anchor balance delta "
                f"{forward_delta!r} != net {next_column.net!r} + "
                f"period_timing {next_column.period_timing!r} + "
                f"book_vs_bank {next_column.book_vs_bank!r} "
                f"-- carry-forward broken"
            )
            # And with no transactions in the post-anchor period the
            # net is exactly zero, locking the empty-period case.
            assert next_column.net == Decimal("0.00"), (
                f"case {case['id']!r}: post-anchor period has no "
                f"transactions but its net is {next_column.net!r}; "
                f"expected 0.00"
            )


# ── C11-6: seam-injection negative control ─────────────────────────


class TestSeamInjectionLock:
    """The cross-page lock catches a real seam re-introduction.

    HIGH-01's value comes from the lock bites when a consumer
    bypasses the balance-at seam.  This test PROVES the lock is real
    -- it monkey-patches one consumer to short-circuit to a divergent
    Decimal and asserts that
    :class:`TestCrossPageBalanceEquality.test_all_surfaces_equal`'s
    inner assertion now FAILS.  Without this negative control a
    silently-broken equality test (e.g. one that read the same value
    twice from the same producer) would still report green.
    """

    def test_invariant_fails_if_seam_reintroduced(
        self,
        app,
        seed_cross_page_account,
        auth_client,
        monkeypatch,
    ):
        """C11-6: monkey-patching one surface produces a divergence the lock catches.

        The patch target is :func:`_savings_value` -- the /savings
        surface reader.  Replacing it with a function that returns a
        Decimal known to differ from the canonical producer's output
        (the pre-Commit-5 silent-degrade value, in fact, for the
        PT-01 base case: $114.29 instead of $160.00) makes the
        cross-page equality assertion fail.  The test asserts that
        the assertion-failing path raises ``AssertionError`` -- i.e.
        the lock did its job.

        Why the patch is on the READER, not the consumer service:
        we want to prove the cross-page equality assertion catches a
        divergence in any of its six inputs without actually
        introducing a bug into ``savings_dashboard_service`` (a real
        regression there would break thousands of unrelated tests
        too).  Patching the reader is the minimal counterfactual
        that exercises the lock's failure path; if the equality
        assertion catches THIS, it would catch a real seam
        re-introduction in any consumer the same way.
        """
        with app.app_context():
            # Pick the PT-01 base case so the canonical balance is
            # the well-known 160.00 and the divergent value 114.29
            # below has documented provenance (the audit's symptom #1
            # mismatch, F-009).
            case = next(c for c in _CASES if c["id"] == "pt01_base")
            ctx = seed_cross_page_account(
                anchor_balance=case["anchor_balance"],
                expense_amount=case["expense_amount"],
                entries=case["entries"],
            )
            # The accounts cash-detail reader drives the real route (it returns
            # the correct 160.00 here); the divergence under test is injected
            # into /savings below, not cash-detail.
            ctx["client"] = auth_client

            # Patch the /savings reader to bypass the canonical
            # producer and return the silently-degraded value (the
            # pre-fix /savings number).  This simulates a future
            # regression where /savings stops eager-loading entries
            # and reverts to ``effective_amount``.
            def _broken_savings_reader(_ctx):
                """Simulate the pre-Commit-5 silent-degrade /savings value."""
                return Decimal("114.29")

            monkeypatch.setitem(
                _SURFACE_READERS, "savings", _broken_savings_reader,
            )

            # Run the same equality logic the positive test runs, through
            # the shared helper.  Expect AssertionError -- the seam
            # re-introduction must be caught.  If this raises something
            # else, the lock is broken in a different way and the test must
            # still fail loudly (no broad ``except``).
            with pytest.raises(AssertionError) as excinfo:
                _assert_surfaces_equal(
                    _all_surface_values(ctx),
                    case["expected_balance"],
                    f"case {case['id']!r}",
                )

            # The AssertionError must name the savings surface and
            # the divergent Decimal -- if it does not, the equality
            # assertion is happening but not on the surface we
            # patched (the lock would then bite for the wrong
            # reason).
            assert "'savings'" in str(excinfo.value), (
                "seam-injection negative control fired AssertionError "
                "but the message did not reference 'savings' -- the "
                f"lock caught a different divergence: {excinfo.value!r}"
            )
            assert "114.29" in str(excinfo.value), (
                "seam-injection negative control fired AssertionError "
                "but the message did not reference the divergent "
                f"Decimal 114.29: {excinfo.value!r}"
            )

    def test_route_render_lock_catches_a_cash_detail_route_misrender(
        self,
        app,
        seed_cross_page_account,
        auth_client,
        monkeypatch,
    ):
        """L6: the cash-detail reader catches a ROUTE that mis-renders.

        The other cash readers re-call the seam and reconstruct the number the
        page ought to show, so a route that drifted onto a different producer
        (or a template bug) would still pass them -- they hardcode the correct
        call.  This control proves the accounts cash-detail reader is genuinely
        different: it reads what ``GET /accounts/<id>/details`` actually
        rendered.  Monkeypatching ONLY the route's ``_current_period_balance``
        (the service-level readers are untouched, isolating the divergence to
        the route-render path) makes the rendered ``data-current-balance``
        diverge, and the cross-page equality assertion must fail naming the
        ``accounts_checking`` surface.  Without this control a reader that
        silently reconstructed the seam value -- the L6 defect the wiring
        fixed -- would report green here, so this is the non-vacuity proof the
        route-render lock bites.
        """
        with app.app_context():
            case = next(c for c in _CASES if c["id"] == "pt01_base")
            ctx = seed_cross_page_account(
                anchor_balance=case["anchor_balance"],
                expense_amount=case["expense_amount"],
                entries=case["entries"],
            )
            ctx["client"] = auth_client

            def _misrender(_balances, _current_period, _anchor):
                """Force ONLY the cash-detail route to render a divergent balance."""
                return Decimal("999.99")

            monkeypatch.setattr(
                "app.routes.accounts.detail._current_period_balance",
                _misrender,
            )

            with pytest.raises(AssertionError) as excinfo:
                _assert_surfaces_equal(
                    _all_surface_values(ctx),
                    case["expected_balance"],
                    f"case {case['id']!r}",
                )

            assert "'accounts_checking'" in str(excinfo.value), (
                "route-render negative control fired AssertionError but the "
                "message did not name 'accounts_checking' -- the cash-detail "
                f"reader is not reading the route's render: {excinfo.value!r}"
            )
            assert "999.99" in str(excinfo.value), (
                "route-render negative control fired AssertionError but the "
                "message did not reference the divergent rendered Decimal "
                f"999.99 -- the reader did not read the route's value: "
                f"{excinfo.value!r}"
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


def _balance_at_scalar_value(ctx, target):
    """Read what a loan OWES at *target* by the DATE-PRECISE scalar (C11 surface).

    The year-end debt-progress section values a loan at exact civil dates via
    ``balance_at.balance_at`` -- a walk over the resolver schedule's rows.
    Since the C11 history read switch those rows carry the ledger's REAL
    per-payment balances, so the date-precise scalar at any date through the
    LAST CONFIRMED payment equals the ledger balance (the C9-deferred scalar
    half).  The seam reports that scalar HELD since plan step
    credit_card:CC-5-5c (ruling R-CC47), so this returns ``owed()`` of it --
    the sign of the ledger balance it is compared against.  Evaluated at a
    caller-chosen date rather than today because the scalar's walk keeps its
    pre-existing DUE-BASIS attribution beyond the confirmed rows: a scheduled payment due before ``target`` but not yet
    made is counted as if paid (the period-end-keyed F-21 semantic), so at
    "today" with an overdue payment it deliberately reads below the card.
    """
    return owed(balance_at.balance_at(
        ctx["account"], _bctx(ctx), target,
    ))


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


class TestLoanCrossPageEquality:
    """Every loan surface reports the same positive current balance C.

    A single isolated amortizing loan (current balance C, original principal
    P, with C != P) must report C identically on the /savings tile, the
    loan-detail producer, the negated year-end liability aggregate, and the
    net-worth trend's liabilities at today.

    The boundary assertions additionally lock the balance rule at the three
    points a loan's per-period map is answered from, since each has a DIFFERENT
    producer and only one of them can see any given defect.  The map is HELD
    since plan step credit_card:CC-5-5c (ruling R-CC47) -- the loan holds
    ``-C`` -- so each assertion reads what the loan OWES through ``owed()``:

    * **A begun period at/after the true-up** (the anchor period) -- the confirmed
      LEDGER.  It owes C, never the original principal P.
    * **A begun period that ended BEFORE the true-up** -- the confirmed ledger
      again, which reports what it knew then: the $240,000 opening, since this loan
      has no recorded payment.  A re-anchored schedule must never back-project
      today's balance over a past it has no evidence for.
    * **The first FUTURE period** -- the forward PROJECTION, which amortizes DOWN
      from C, so what it owes sits below C.  A map reporting the original
      principal here (one that fell back to the whole-schedule walk) would sit
      far above C.

    A note on what this test can NO LONGER catch, so nobody trusts it for more
    than it does.  Before the ledger read switch, EVERY period came from the
    schedule walk seeded with ``current_balance``, so a pre-payment period was a
    live probe of that seed -- which is what made it the PR #44 / aba0242 lock
    (that bug passed ``original_principal`` where the seed belongs).  Now the
    ledger owns every BEGUN period, and the seed is read only for a target
    preceding the first scheduled payment, so the seed is invisible to all three
    assertions above.  Confirmed by reintroducing the defect: seeding the forward
    projection with ``original_principal`` changes no value this test sees.  The
    fence on that argument is now STRUCTURAL, not this test: there is no forward
    seed at all since plan step recurrence:R16-c-1 -- the loan's timeline replays
    from its opening ASSERTION (never ``original_principal``; it was the one
    source of ``net_worth_kernel._projection_seed`` until that step deleted the
    seed with the second fold) -- so no call site passes a seed (C6b deleted the
    schedule-forward primitives that once took it, and the W9905 checker that
    policed them retired with them).
    """

    def test_all_surfaces_equal(self, app, cross_page_loan_ctx, auth_client):
        """Every loan surface returns C; the anchor period is C, the past is the ledger.

        C = $200,000 (trued up today) and P = $240,000 (origination principal)
        differ, so none of the boundary assertions is tautological.  All five
        cross-page surfaces read C at today -- each the figure its page shows,
        which for a loan is what it owes.  The seam's map is HELD (the loan
        holds ``-C``, plan step credit_card:CC-5-5c), so the boundaries read
        what it owes through ``owed()``: C at the anchor period (never P), the
        ledger's $240,000 opening at a period that ended before the true-up was
        asserted, and a value below C at the first future period (the
        projection amortizing down from C).
        """
        with app.app_context():
            ctx = cross_page_loan_ctx
            expected = ctx["C"]  # the trued-up current balance
            surface_values = {
                name: reader(ctx)
                for name, reader in _LOAN_SURFACE_READERS.items()
            }
            _assert_surfaces_equal(surface_values, expected, "loan kind")

            balances = balance_at.balance_map(
                ctx["account"], _bctx(ctx),
            )

            # Boundary lock (PR #44 / aba0242): at the anchor period -- the
            # period the true-up lands in, and still pre-first-payment -- the
            # loan owes the current balance C flat (the seam's map holds -C;
            # every boundary here reads what it owes through owed()).
            # Returning the original principal P for the loan's CURRENT balance
            # is the exact PR #44 bug (its cause: the schedule map was seeded
            # with original_principal).  C != P is what makes this
            # non-tautological.
            anchor_balance = owed(balances[ctx["anchor_period"].id])
            assert anchor_balance == ctx["C"], (
                f"anchor-period balance {anchor_balance!r} != current balance "
                f"{ctx['C']!r}; the loan pre-payment boundary regressed"
            )
            assert anchor_balance != ctx["P"], (
                f"anchor-period balance {anchor_balance!r} == original principal "
                f"{ctx['P']!r}; this is the exact PR #44 boundary bug"
            )

            # Ledger authority: the true-up is dated TODAY, so a period that
            # ENDED before it reports what the confirmed ledger knew then -- the
            # $240,000 opening, undisturbed, because this loan has no recorded
            # payment.  C is an assertion about today and is NOT back-projected
            # across the past.  Verified against the real dev clone, whose
            # Mortgage likewise steps down at each recorded event rather than
            # carrying today's balance backward.
            pre_balance = owed(balances[ctx["pre_anchor_period"].id])
            assert pre_balance == ctx["P"], (
                f"pre-anchor balance {pre_balance!r} != the ledger's opening "
                f"{ctx['P']!r}; the schedule is answering for the past again"
            )

            # The future belongs to the projection, and it amortizes DOWN from C:
            # what the first future period owes must sit strictly below C.  This
            # catches a forward projection that reports the original principal --
            # e.g. one that carried today's balance backward -- which would land
            # it near P, far ABOVE C.
            #
            # It does NOT catch a wrong forward SEED in isolation, and no assertion
            # on this fixture can: since step C6b the forward branch folds the
            # loan's PLAN behind its recorded facts in ONE timeline (positions()
            # -> loan_timeline, since recurrence:R16-c-1), every period here has
            # BEGUN (so it reads the recorded facts) except the future ones, and
            # the true-up dated today puts the first installment inside the very
            # next period -- so there is no future period before the first
            # paydown to expose the projection's starting balance on its own.
            # That balance is the replay's own, from the opening ASSERTION (never
            # original_principal).
            future = [
                p for p in ctx["all_periods"] if p.start_date > date.today()
            ]
            assert future, "expected a future period"
            first_future = owed(balances[future[0].id])
            assert first_future < ctx["C"], (
                f"first future period {first_future!r} is not below the trued-up "
                f"balance {ctx['C']!r}; the forward projection is not amortizing "
                f"down from the current balance"
            )

            # Route wiring: the loan detail page renders without raising.
            resp = auth_client.get(f"/accounts/{ctx['account_id']}/loan")
            assert resp.status_code == 200, (
                f"/accounts/<id>/loan returned {resp.status_code} for the "
                "loan kind"
            )

    def test_unpaid_loan_owes_its_opening_on_every_surface(
        self, app, cross_page_loan_unpaid_ctx, auth_client,
    ):
        """A never-paid loan owes its FULL opening on every surface, at every period.

        The shape the cross-page lock was blind to.  ``cross_page_loan_ctx``
        true-ups the loan TODAY, which re-anchors the schedule today-forward and
        leaves no past-dated unpaid rows -- the one loan shape in which a
        schedule-walking producer cannot phantom-pay the debt down.  This loan was
        originated 18 months ago and never paid, so its schedule carries ~17
        PROJECTED installments dated on or before today.

        Not one of them was paid, so not one dollar of principal was: every
        surface must report the full $240,000 opening.  A producer that reduces
        the balance by unpaid scheduled principal reports LESS -- and because only
        some producers walk the schedule, the page contradicts itself (the
        /savings tile and the net-worth trend's own 'today' point disagreeing was
        the symptom that opened this arc).

        Non-vacuity is asserted, not assumed: the schedule really does carry
        unpaid rows dated on or before today, so a phantom paydown had something
        to bite on.
        """
        with app.app_context():
            ctx = cross_page_loan_unpaid_ctx
            expected = ctx["P"]  # never paid -> still owes the whole opening
            bctx = _bctx(ctx)

            # Non-vacuity: unpaid installments dated on or before today exist.
            schedule = resolved_loan(ctx["account"], bctx).state.schedule
            stale_projected = [
                row for row in schedule
                if not row.is_confirmed and row.payment_date <= bctx.as_of
            ]
            assert stale_projected, (
                "fixture regressed: the schedule must carry unpaid rows dated on "
                "or before today, or this test pins nothing"
            )

            surface_values = {
                name: reader(ctx)
                for name, reader in _LOAN_SURFACE_READERS.items()
            }
            _assert_surfaces_equal(surface_values, expected, "unpaid loan")

            # The per-period map agrees with the scalar the surfaces read -- at
            # today AND at a past period.  These are the two producers that
            # diverged: the scalar walked confirmed rows, its per-period sibling
            # walked all of them.  The map is HELD (plan step
            # credit_card:CC-5-5c), so it is read as what the loan owes.
            balances = balance_at.balance_map(
                ctx["account"], bctx,
            )
            assert owed(balances[ctx["anchor_period"].id]) == expected
            assert owed(balances[ctx["past_period"].id]) == expected

            resp = auth_client.get(f"/accounts/{ctx['account_id']}/loan")
            assert resp.status_code == 200, (
                f"/accounts/<id>/loan returned {resp.status_code} for the "
                "never-paid loan"
            )

    def test_all_surfaces_read_the_ledger_off_schedule(
        self, app, cross_page_loan_off_schedule_ctx, auth_client,
    ):
        """All SIX loan surfaces read the LEDGER off-schedule (C8 + C9 + C11).

        The C8 read switch (plan Section 8) flipped the two SCALAR surfaces --
        the /savings tile (``_compute_loan_account``) and the loan-detail
        balance (the ``balance_at`` seam scalar).  The C9 per-period read switch
        (plan Section 9) flipped the two MAP surfaces -- the year-end
        net-worth aggregate and the net-worth-trend liabilities lane, both
        fed by the ``balance_at`` seam's per-period map, spliced from the
        confirmed ledger for begun periods.  The C11 history read switch
        flips the last two: the amortization TABLE's confirmed rows (now
        ledger-derived, so its last confirmed row equals the card) and the
        ``balance_at`` DATE-PRECISE scalar (the year-end debt-progress walk,
        which now walks ledger-real row balances -- the C9-deferred half),
        asserted at the last confirmed payment's due date, the edge of the
        confirmed domain (beyond it the walk keeps its pre-existing
        due-basis projection attribution -- see ``_balance_at_scalar_value``).
        Off-schedule -- one confirmed payment far above the scheduled P&I --
        every surface shows the genesis-ledger confirmed balance (the REAL
        principal paid down), NOT the schedule replay.

        The fixture pins the ledger balance and the un-seeded replay balance;
        the assertions require them to DIVERGE, so "surfaces == ledger" is
        non-vacuous: before the C8/C9/C11 switches these surfaces WERE the
        replay.  This closes the C8 M1 deferral in full: the card, its own
        schedule table, the trend, and the year-end all agree off-schedule.
        """
        with app.app_context():
            ctx = cross_page_loan_off_schedule_ctx
            ledger = ctx["ledger"]
            replay = ctx["replay"]

            # Non-vacuity: the loan is genuinely off-schedule -- the genesis reader
            # opened it (not None) and its real balance is STRICTLY BELOW the
            # schedule replay by the extra principal the replay drops.
            assert ledger is not None, "fixture did not open the loan in the ledger"
            assert ledger < replay, (
                f"fixture is not off-schedule: ledger {ledger!r} not < replay "
                f"{replay!r}; the surface check would be vacuous"
            )

            # Every loan surface -- the two C8 scalars AND the two C9 maps --
            # now reads the ledger at today, so the whole cross-page set agrees
            # on the real balance off-schedule (the M1 divergence is closed).
            surface_values = {
                name: reader(ctx)
                for name, reader in _LOAN_SURFACE_READERS.items()
            }
            _assert_surfaces_equal(
                surface_values, ledger, "loan kind (off-schedule)",
            )

            # And crucially none still reads the old schedule-replay value: each
            # surface -- scalar and map alike -- moved onto the ledger (the flip).
            for name, value in surface_values.items():
                assert value != replay, (
                    f"surface {name!r} still reads the schedule replay "
                    f"{replay!r}; the read switch did not move it onto the ledger"
                )

            # The C9-deferred DATE-PRECISE scalar: at the last confirmed
            # payment's due date -- the edge of the confirmed domain, where
            # nothing has settled since -- the ledger-derived rows make the
            # walk read the REAL balance, equal to today's card, and NOT the
            # replay's scheduled figure.
            resolved = resolved_loan(ctx["account"], _bctx(ctx))
            confirmed_rows = [
                row for row in resolved.state.schedule if row.is_confirmed
            ]
            assert confirmed_rows, "fixture lost its confirmed payment"
            scalar = _balance_at_scalar_value(
                ctx, confirmed_rows[-1].payment_date,
            )
            assert scalar == ledger, (
                f"balance_at scalar {scalar!r} != ledger {ledger!r} at the "
                "last confirmed due date; the C11 scalar half regressed"
            )
            assert scalar != replay

            # Route wiring: the /savings page renders the off-schedule loan.
            resp = auth_client.get("/savings")
            assert resp.status_code == 200, (
                f"/savings returned {resp.status_code} for the off-schedule loan"
            )


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
            # raising (parity with the loan and property route checks above).
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


class TestPerKindSeamInjectionLock:
    """Each per-kind cross-page lock catches an injected single-surface divergence.

    The per-kind analogue of :class:`TestSeamInjectionLock`: monkeypatching
    ONE reader in a kind's reader dict to return a deliberately wrong
    Decimal must make :func:`_assert_surfaces_equal` raise an AssertionError
    naming the patched surface and the wrong value.  Without this a per-kind
    equality test that happened to read the same producer twice would still
    report green.  Parametrised over the three single-value kinds to stay
    DRY.

    **It fired whether or not the injection landed, and that is finding N-94**
    (repaired at plan step X-h).  It compared every surface against the
    fixture's ASSERTED value -- ``ctx["V"]`` / ``ctx["C"]`` -- and since plan
    step X-g2b gave the anchor period its own accrual (ruling R-Y) no modelled
    surface returns that number.  Measured on the fixtures at the repair:

    ======================  ==============  ==============  ================
    kind                    asserted        modelled        blind?
    ======================  ==============  ==============  ================
    loan                    ``$200,000.00`` ``$200,000.00`` no
    property                ``$400,000.00`` ``$401,005.45`` YES
    investment              ``$100,000.00`` ``$100,576.29`` YES
    ======================  ==============  ==============  ================

    So two of three cases raised with the patch and without it, and the
    name/value assertions then matched against the "All surface values: {...}"
    dump rather than against the lock actually biting -- the shape Section 7.3
    of the balance plan exists to prevent.

    Two things repair it.  The comparand is now the seam's own modelled
    current balance, read through :func:`_modelled_current_balance`, which is
    the identical oracle the sibling positive tests use -- so this control can
    never again drift from the figure those tests assert.  And each case
    carries the surfaces its positive test EXCLUDES: the property kind's
    ``property_detail`` still reads the asserted value (finding N-83), so
    including it would leave the unpatched set non-uniform for a reason that
    has nothing to do with the injection.

    **Since plan step credit_card:CC-5-5c the comparand is that balance in the
    words the pages speak** -- ``shown_figure`` of it.  The seam reports the
    loan HELD (``-$200,000.00``, ruling R-CC47) and every loan reader returns
    what it OWES, so the loan's comparand is ``$200,000.00``; for the two asset
    kinds the shown figure IS the balance.  The table above is the measurement
    at the repair, when the seam still reported a loan owed.

    The control for the control is asserted in the test itself: the UNPATCHED
    set must pass before the patch is applied.  Without that premise a lock
    that fires for any reason reads exactly like a lock that fires for the
    right one.
    """

    _WRONG = Decimal("-99999.99")  # no fixture balance equals this

    # One per-kind negative-control case: (ctx fixture name, that kind's
    # reader dict, the surfaces its positive test excludes, the surface to
    # break).  Bundled into a single parametrize value so the test stays a
    # cohesive 5-argument method rather than threading four parallel
    # parametrize columns.
    @pytest.mark.parametrize(
        "spec",
        [
            ("cross_page_loan_ctx", _LOAN_SURFACE_READERS, (), "savings"),
            (
                "cross_page_property_ctx", _PROPERTY_SURFACE_READERS,
                ("property_detail",), "savings",
            ),
            (
                "cross_page_investment_ctx", _INVESTMENT_SURFACE_READERS,
                (), "savings",
            ),
        ],
        ids=["loan", "property", "investment"],
    )
    def test_injected_divergence_is_caught(self, app, request, monkeypatch, spec):
        """Patching one reader to a wrong Decimal makes the lock fire on it.

        The patched surface reports ``_WRONG`` while every other surface
        reports the kind's true balance, so :func:`_assert_surfaces_equal`
        must raise an AssertionError whose message names the patched surface
        and the wrong value -- proving the lock bites on a real
        single-surface regression, not a coincidence.

        The unpatched set is asserted to PASS first (finding N-94): a control
        that raises before the injection lands proves nothing about the
        injection.
        """
        ctx_fixture, readers, excluded, patched_surface = spec
        ctx = request.getfixturevalue(ctx_fixture)
        with app.app_context():
            # The figure every page SHOWS for this account: what the loan
            # owes, the modelled balance for an asset (see the class docstring).
            expected = shown_figure(
                ctx["account"].account_type, _modelled_current_balance(ctx),
            )

            def _read(reader_dict):
                """Read every surface this case locks, minus its known gaps."""
                return {
                    name: reader(ctx)
                    for name, reader in reader_dict.items()
                    if name not in excluded
                }

            # The control FOR the control: without the injection the lock is
            # silent, so the raise below is caused by the patch and nothing
            # else.
            _assert_surfaces_equal(_read(readers), expected, ctx_fixture)

            def _broken_reader(_ctx):
                """Return a deliberately wrong Decimal for the patched surface."""
                return self._WRONG

            monkeypatch.setitem(readers, patched_surface, _broken_reader)

            with pytest.raises(AssertionError) as excinfo:
                _assert_surfaces_equal(_read(readers), expected, ctx_fixture)

            message = str(excinfo.value)
            assert repr(patched_surface) in message, (
                f"AssertionError did not name the patched surface "
                f"{patched_surface!r}: {message!r}"
            )
            assert str(self._WRONG) in message, (
                f"AssertionError did not name the wrong value "
                f"{self._WRONG!r}: {message!r}"
            )
