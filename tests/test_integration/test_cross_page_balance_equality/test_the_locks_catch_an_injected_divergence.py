"""The negative controls: the cash lock and each per-kind lock fail when one surface is patched to
report a divergent figure, so neither equality test can pass by reading one producer twice.
"""

from decimal import Decimal

import pytest

from app.services.liability_sign import shown_figure

from ._helpers import (
    _CASES,
    _SURFACE_READERS,
    _all_surface_values,
    _assert_surfaces_equal,
    _LOAN_SURFACE_READERS,
    _PROPERTY_SURFACE_READERS,
    _INVESTMENT_SURFACE_READERS,
    _modelled_current_balance,
)


# ── C11-6: seam-injection negative control ─────────────────────────


class TestSeamInjectionLock:
    """The cross-page lock catches a real seam re-introduction.

    HIGH-01's value comes from the lock bites when a consumer
    bypasses the balance-at seam.  This test PROVES the lock is real
    -- it monkey-patches one consumer to short-circuit to a divergent
    Decimal and asserts that ``test_the_cash_surfaces_agree.py``'s
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

        The patch target is ``_helpers.py``'s :func:`_savings_value` -- the /savings
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
