"""Every loan surface reports the same current balance, and the balance rule holds at each point a
loan's per-period map is answered from.
"""

from datetime import date
from decimal import Decimal

from app.services import balance_at
from app.services.balance_at._resolution import resolved_loan
from app.services.liability_sign import owed

from ._helpers import _bctx, _assert_surfaces_equal, _LOAN_SURFACE_READERS


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

        The ledger balance is pinned to the hand-worked 196,000.00 (the
        fixture's docstring has the arithmetic), a figure only the actual cash
        produces: a replay of the contract's $1,498.88 would owe 199,501.12, so
        "surfaces == ledger" is non-vacuous -- before the C8/C9/C11 switches
        these surfaces WERE that replay.  They were also asserted unequal to
        the replay's own balance until plan task plan#3393 deleted its suite
        assembly (ruling plan#3383); the literal carries that check.  This
        closes the C8 M1 deferral in full: the card, its own schedule table,
        the trend, and the year-end all agree off-schedule.
        """
        with app.app_context():
            ctx = cross_page_loan_off_schedule_ctx
            ledger = ctx["ledger"]

            # Non-vacuity: the genesis reader opened the loan and reads the
            # balance the actual cash produces, worked by hand: 200000.00 -
            # (5000.00 - 1000.00 interest) = 196,000.00.
            assert ledger == Decimal("196000.00"), (
                f"fixture ledger {ledger!r} is not the hand-worked 196000.00"
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

            # Route wiring: the /savings page renders the off-schedule loan.
            resp = auth_client.get("/savings")
            assert resp.status_code == 200, (
                f"/savings returned {resp.status_code} for the off-schedule loan"
            )
