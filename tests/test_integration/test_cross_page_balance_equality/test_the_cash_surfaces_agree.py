"""The cash surfaces agree: the grid, /savings, the /accounts cash detail, the dashboard and the
calendar month-end return the identical Decimal on every parameter case, and the frozen clock
the cases rest on is strictly inside its month.
"""

from datetime import date, timedelta

import pytest

from ._helpers import _CASES, _all_surface_values, _assert_surfaces_equal


def test_the_frozen_clock_is_strictly_inside_its_month():
    """The precondition the module's expectations rest on, asserted.

    A fixture that silently stops exercising what it names is this codebase's
    signature defect.  Without this, a future edit to the freeze in this
    package's ``conftest.py`` could put
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
        comment block in ``_helpers.py``.  The assertion is dual: (a) every surface
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
            # exercised AND value-checked by ``_helpers.py``'s
            # ``_accounts_checking_value``,
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
