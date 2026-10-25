"""End-to-end integration test for Symptom #3 -- confirmed transfers reduce loan principal.

Commit 14 of the financial-calculation audit remediation locks the
single most important behavioural promise of E-18: when a user marks
a PITI transfer as paid through the real grid workflow, the loan
resolver's ``current_balance`` drops by exactly the principal portion
of that payment.  Escrow and interest portions never reduce
principal (E-01 split invariant).  Projected transfers never reduce
principal (only ``is_confirmed`` payments are replayed).

The test drives the live ``transfer_service`` / ``transactions.py``
``mark_done`` path -- not a manual ``status_id`` overwrite -- so the
assertions prove the assembled production pipeline behaves correctly:
the transfer moves to a settled status and its loan side's payment record
is dated (the income shadow carried both until plan step
``balance:X-bi-6-4d-2``), :func:`loan_payment_service.get_payment_history`
lists it as a
confirmed :class:`PaymentRecord`, :func:`load_loan_context` prepares
it for the engine, and :func:`loan_resolver.resolve_loan` reduces
``current_balance`` by the principal portion only.

Test IDs map to the Commit 14 plan in
``docs/audits/financial_calculations/remediation_plan.md`` Section 9
(C14-1 through C14-4).  C14-5 (the loan-card display reflects the
reduction) is intentionally deferred to Commit 15, when the dashboard
card is routed through the resolver; until then the card renders the
stored ``current_principal`` column unchanged by settles (the
symptom-#3 freeze).

Every monetary expectation carries the arithmetic in a comment so a
future reader can verify the assertion by hand.  Hand-computed
expectations follow the Symptom #3 worked example in
``docs/audits/financial_calculations/05_symptoms.md`` lines 791-817
(adjusted to a $1,798.65 contractual P&I so the same arithmetic ties
into the C13-5 fixed-rate replay test).

A real defect would surface here as either (a) the resolver balance
not dropping, or (b) the drop including the escrow / interest
portions.  Either case is a Commit-14-scope production fix per the
plan's "Note for this commit" (CLAUDE.md rule 4) -- not a
band-aid in the test.

**Every balance here is read AT OR AFTER the day the money moved, and that
matters since plan step X-an** (finding N-187).  ``mark-done`` stamps the
payment record's ``settled_on`` (the shadow's until plan step
``balance:X-bi-6-4d-2``) with the user's today, and the resolver's
replay-vs-projection cut is now that day -- the SAME day the balance seam
counts the payment's principal from, which is the point of the step.  These
tests used to read the balance on a fixed ``2026-03-01`` while the payment's
cash moved on the day the suite ran, and they passed because the resolver cut
on the payment's PAY PERIOD instead: the replay reported the reduction months
before the money left, and the seam -- measured, on this exact fixture --
reported ``$300,000.00`` on that date and ``$299,701.35`` only on the settle
day.  The dates below are therefore derived from ``display_today()``, never
written down; the hand-computed amounts are unchanged.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    StatusEnum,
)
from app.extensions import db
from app.models.transfer import Transfer
from app.services import balance_at, transfer_service
from app.utils.dates import add_months, display_today
from tests._test_helpers import (
    add_escrow_line,
    create_loan_account,
    loan_params_for,
    transfer_side_record,
    unseeded_replay_balance,
)
from app.services.row_valuation import leg_settled_contribution
from tests.test_integration.test_loan_transfer_live_amount._helpers import _side_leg
from app.models.amount_ownership import AmountOwnership


# -- Hand-computed reference values -----------------------------------------
#
# All test scenarios use a single fixed-rate mortgage to keep the
# arithmetic transparent.  The contractual P&I value matches the
# C13-5 resolver unit test in
# ``tests/test_services/test_loan_resolver.py`` so the integration
# layer's hand-computed cumulative reduction ties out byte-for-byte
# with the resolver-level lock.
#
# Loan: $300,000 fixed-rate, 6% annual, 360 months, origination
# 2026-01-01, payment_day=1.
#
#     monthly_rate   = 0.06 / 12 = 0.005
#     contractual_pi = amortize(300000, 0.06, 360) = $1,798.65
#
# Per-payment arithmetic (each starts from the prior balance):
#
#     m1: i = 300000.00 * 0.005   = 1500.00
#         p = 1798.65 - 1500.00   =  298.65
#         bal = 300000.00 - 298.65 = 299,701.35
#     m2: i = 299701.35 * 0.005   = 1498.51 (HALF_UP from 1498.50675)
#         p = 1798.65 - 1498.51   =  300.14
#         bal = 299701.35 - 300.14 = 299,401.21
#     m3: i = 299401.21 * 0.005   = 1497.01 (HALF_UP from 1497.00605)
#         p = 1798.65 - 1497.01   =  301.64
#         bal = 299401.21 - 301.64 = 299,099.57
#
# E-01 invariant: a transfer carrying $400 of escrow per month sends
# a total of $2,198.65 (P&I + escrow) through the checking account,
# but only the $298.65 principal portion reduces the loan balance --
# the escrow $400 never touches principal.
# The month before the first payment's 2026-03-01 installment (every case
# here pays from ``periods[3]`` onward): every contractual installment from
# origination is charged since plan step recurrence:R16-c-2 (ruling R-R101),
# so the 2026-01-01 it carried until then left February unpaid ahead of it.
ORIGINATION_DATE = date(2026, 2, 1)
ORIGINAL_PRINCIPAL = Decimal("300000.00")
INTEREST_RATE = Decimal("0.06000")  # Numeric(7,5) storage scale.
TERM_MONTHS = 360
CONTRACTUAL_PI = Decimal("1798.65")
MONTHLY_ESCROW_ANNUAL = Decimal("4800.00")  # $400/mo -> $4,800/yr.
PITI_WITH_ESCROW = Decimal("2198.65")       # 1798.65 P&I + 400.00 escrow.

# Hand-computed balances after each cumulative settle (arithmetic above).
BALANCE_AFTER_1 = Decimal("299701.35")
BALANCE_AFTER_2 = Decimal("299401.21")
BALANCE_AFTER_3 = Decimal("299099.57")

# Principal portions per month (the "reduction" amounts).  Stored as
# constants so the test can assert the diff directly, not just the
# absolute balance.
PRINCIPAL_PORTION_M1 = Decimal("298.65")



# -- Helpers ----------------------------------------------------------------


def _after_the_money_moved(months_later: int = 0) -> date:
    """Return an evaluation date at or after the day ``mark-done`` records.

    The settle door stamps ``settled_on`` with the OWNER's civil day
    (``status_seam.apply_status_change`` -> ``display_today()``), and since plan
    step X-an that day IS the resolver's replay cut -- the same day the balance
    seam counts the payment's principal from.  So "after the payment" is a
    function of the suite's own run day, never a written-down date: on any
    earlier date the payment genuinely has not happened, and asserting a
    reduction there would pin the divergence X-an removed.

    Args:
        months_later: Whole months to advance past that day.  ``0`` is the day
            itself, which the cut includes (the bound is inclusive).

    Returns:
        The evaluation date.
    """
    return add_months(display_today(), months_later)


def _create_mortgage(
    seed_user,
    anchor_period,
    *,
    escrow_annual: Decimal | None = None,
):
    """Materialise the canonical fixed-rate mortgage ready for the resolver.

    Routes through the shared :func:`create_loan_account` factory (Mortgage
    account via ``account_service.create_account``, the matching
    :class:`LoanParams`, the origination :class:`RateHistory` / anchor the
    resolver requires, and the loan's GENESIS POSTING LEDGER -- opened in the
    same transaction, exactly as ``app/routes/loan/params.py`` does on every
    production loan write).  The old hand-rolled block here omitted the ledger,
    so its loans exercised the no-ledger fallback production never takes.

    Args:
        seed_user: The ``seed_user`` fixture dict (supplies owner + scenario).
        anchor_period: The :class:`PayPeriod` to anchor the Account against
            (E-19 / Commit 3 requires every Account to have one).
        escrow_annual: If provided, attach a single escrow line (one
            origination-dated version) with this annual amount so the
            resolved monthly escrow is a non-zero figure.

    Returns:
        Tuple of (account, loan_params).  The factory commits; the escrow line
        is flushed and carried by the caller's commit.
    """
    account = create_loan_account(
        seed_user, db.session, name="Principal-Settle Mortgage",
        principal=ORIGINAL_PRINCIPAL, rate=INTEREST_RATE, term=TERM_MONTHS,
        origination_date=ORIGINATION_DATE, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    loan_params = loan_params_for(db.session, account.id)

    if escrow_annual is not None:
        add_escrow_line(
            db.session, account.id, "Property Tax", escrow_annual,
            effective_date=ORIGINATION_DATE,
        )

    return account, loan_params


def _create_piti_transfer(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    *,
    user_id: int,
    from_account_id: int,
    to_account_id: int,
    pay_period_id: int,
    scenario_id: int,
    amount: Decimal,
    category_id: int,
):
    """Create a PITI transfer in Projected status via the transfer service.

    Routed through :func:`transfer_service.create_transfer` so the transfer
    is written the same way the production transfer route writes it: one
    row since plan step ``balance:X-bi-6-4d-2`` (ruling **R-BAL258**), where
    the door also wrote the two shadows Transfer Invariant 1 named.  Status
    is Projected; the caller settles via the live ``mark_done`` route when
    it wants the resolver to count the payment as confirmed.
    """
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=user_id,
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            pay_period_id=pay_period_id,
            scenario_id=scenario_id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=projected_id,
            category_id=category_id,
            notes="C14 PITI transfer",
        ),
    )
    return xfer


def _mark_done_from_the_loan_leg(auth_client, transfer_id: int, loan_account_id: int):
    """Settle the transfer the way the grid does since plan step balance:X-bi-6-1.

    The grid draws a transfer as a LEG read off its parent and the leg's Mark
    Paid posts to the TRANSFER's door with ``leg_account_id`` (ruling
    R-BAL87); these cases posted to the loan-side SHADOW row's transaction
    door until that leaf deleted the shadow branch.  The response is the
    loan-side leg's cell.
    """
    return auth_client.post(
        f"/transfers/instance/{transfer_id}/mark-done",
        data={"leg_account_id": str(loan_account_id)},
    )


def _resolve_balance(
    account_id: int, loan_params, scenario_id: int, as_of: date,
) -> Decimal:
    """Load the replay inputs and return the anchor-replay balance.

    The window the deleted ``LoanState.current_balance`` carried on this
    (unseeded) path (plan step D2a):
    :func:`tests._test_helpers.unseeded_replay_balance`, the suite's ONE
    assembly of the anchor + settled-payment replay, which is AMOUNT-FREE since
    plan step **balance:X-bl-2b** (finding **N-432**) -- the replay reads three
    dates per payment and no figure, so an ``AmountUnresolvable`` can no longer
    break a control here that never looks at an amount.

    ``loan_params`` is taken and unused: every caller holds it, and the helper
    re-loads it from the account id (one loader call, no query the callers do
    not already make).
    """
    del loan_params  # identity carried by account_id; kept for call clarity
    return unseeded_replay_balance(account_id, scenario_id, as_of)


# -- Test class -------------------------------------------------------------


class TestLoanPrincipalSettles:
    """Symptom #3 lock: confirmed transfers reduce resolved principal.

    Each test creates a fresh mortgage, drives one or more transfers
    through the real ``mark_done`` route, then calls the resolver
    and asserts the hand-computed balance.  No status is set
    manually -- the production state-machine path is the entire
    point of the test.
    """

    def _setup_loan(self, seed_user, seed_periods, *, escrow=False):
        """Materialise the mortgage and return ids the tests need.

        Helper for the common pre-action setup: create the loan,
        commit, and hand back the ids tests need to drive the
        ``mark_done`` route.  Returns a dict so additions (e.g.
        the escrow components for C14-2) can be returned without
        changing every test's unpacking shape.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        user = seed_user["user"]
        periods = seed_periods

        category = seed_user["categories"]["Car Payment"]

        escrow_annual = MONTHLY_ESCROW_ANNUAL if escrow else None
        mortgage, loan_params = _create_mortgage(
            seed_user, periods[0], escrow_annual=escrow_annual,
        )
        db.session.commit()

        return {
            "user_id": user.id,
            "scenario_id": scenario.id,
            "checking_id": checking.id,
            "mortgage_id": mortgage.id,
            "category_id": category.id,
            "loan_params": loan_params,
            "periods": periods,
        }

    def test_settled_transfer_reduces_principal(  # C14-1
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C14-1: One settled PITI transfer drops the resolved principal by
        exactly the hand-computed principal portion.

        Setup: fresh $300k / 6% / 360mo fixed-rate mortgage with the
        Commit-12-shaped origination anchor at 2026-01-01.  One PITI
        transfer of $1,798.65 in pay period 3 (start 2026-02-13).
        The grid posts the loan-side LEG's Mark Paid to the transfer's
        door (``/transfers/instance/<id>/mark-done`` with
        ``leg_account_id``, plan step balance:X-bi-6-1); the route settles
        through ``transfer_service.settle_transfer`` so the transfer reaches
        the DONE status (``is_settled = True``; it is the one status, where
        both shadows were asserted until ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2) and the loan-payment feed picks the transfer up
        as a confirmed :class:`PaymentRecord`.

        Hand-computed expectation:

            interest = 300000.00 * 0.005          = 1500.00
            principal_portion = 1798.65 - 1500.00 =  298.65
            balance = 300000.00 - 298.65          = 299,701.35
        """
        with app.app_context():
            ctx = self._setup_loan(seed_user, seed_periods)

            # Sanity floor: the resolver returns the anchor balance
            # when no confirmed payments are present.  Without this
            # the cumulative-diff assertion could not distinguish a
            # broken setup from a working settle.
            balance_before = _resolve_balance(
                ctx["mortgage_id"], ctx["loan_params"],
                ctx["scenario_id"], _after_the_money_moved(),
            )
            assert balance_before == ORIGINAL_PRINCIPAL, (
                f"Expected anchor balance {ORIGINAL_PRINCIPAL} before "
                f"settle; got {balance_before}.  Likely the origination "
                f"event was not created or the resolver inputs drifted."
            )

            xfer = _create_piti_transfer(
                user_id=ctx["user_id"],
                from_account_id=ctx["checking_id"],
                to_account_id=ctx["mortgage_id"],
                pay_period_id=ctx["periods"][3].id,
                scenario_id=ctx["scenario_id"],
                amount=CONTRACTUAL_PI,
                category_id=ctx["category_id"],
            )
            db.session.commit()
            xfer_id = xfer.id

            resp = _mark_done_from_the_loan_leg(
                auth_client, xfer_id, ctx["mortgage_id"],
            )
            assert resp.status_code == 200, (
                f"mark_done returned {resp.status_code}; body={resp.data!r}"
            )

            # Re-fetch the transfer so the test's session sees the
            # commit produced by the request.
            db.session.expire_all()
            settled_transfer = db.session.get(Transfer, xfer_id)
            assert settled_transfer.status.is_settled is True, (
                f"Expected the transfer to be settled after mark_done; "
                f"status={settled_transfer.status.name!r}"
            )

            balance_after = _resolve_balance(
                ctx["mortgage_id"], ctx["loan_params"],
                ctx["scenario_id"], _after_the_money_moved(),
            )

            # Primary symptom-#3 assertion: principal drops by exactly
            # the hand-computed portion.
            assert balance_after == BALANCE_AFTER_1, (
                f"Expected balance after one settle = {BALANCE_AFTER_1}; "
                f"got {balance_after}."
            )
            assert (
                balance_before - balance_after == PRINCIPAL_PORTION_M1
            ), (
                f"Expected principal reduction = {PRINCIPAL_PORTION_M1}; "
                f"got {balance_before - balance_after}."
            )

    def test_escrow_interest_do_not_reduce_principal(  # C14-2
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C14-2: E-01 split -- escrow rides through checking but never
        reduces loan principal.

        Setup: same $300k fixed-rate mortgage but with a $400/month
        escrow component attached, so the user's PITI transfer is
        $2,198.65 ($1,798.65 P&I + $400.00 escrow).  The
        ``loan_payment_service.prepare_payments_for_engine`` step
        subtracts the escrow from the above-P&I excess before the
        engine sees the payment, so the resolver replays only the
        $1,798.65 P&I portion and reduces principal by $298.65 --
        not $698.65.

        Hand-computed expectation:

            interest = 300000.00 * 0.005          = 1500.00
            principal_portion = 1798.65 - 1500.00 =  298.65
            balance = 300000.00 - 298.65          = 299,701.35

        Sanity counter-example: if escrow leaked into principal the
        balance would be 300000.00 - 698.65 = 299,301.35.  The test
        asserts the correct value AND the absence of the wrong
        value so a regression that silently swaps the formulae fails
        loudly.

        The checking side is valued as the transfer and its side's record,
        where it was read off the checking expense twin (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            ctx = self._setup_loan(
                seed_user, seed_periods, escrow=True,
            )

            xfer = _create_piti_transfer(
                user_id=ctx["user_id"],
                from_account_id=ctx["checking_id"],
                to_account_id=ctx["mortgage_id"],
                pay_period_id=ctx["periods"][3].id,
                scenario_id=ctx["scenario_id"],
                amount=PITI_WITH_ESCROW,
                category_id=ctx["category_id"],
            )
            db.session.commit()

            resp = _mark_done_from_the_loan_leg(
                auth_client, xfer.id, ctx["mortgage_id"],
            )
            assert resp.status_code == 200, (
                f"mark_done returned {resp.status_code}; body={resp.data!r}"
            )

            db.session.expire_all()

            # E-01 cross-cut: the checking side carries the full
            # $2,198.65 PITI -- so when consumers route through the
            # balance resolver (Commits 5-10) checking is debited by
            # the full amount.  We assert the side itself here rather
            # than driving the balance resolver because Commit 14 is
            # loan-focused; the checking-side producer assertion
            # belongs to the cross-page lock in Commit 11 (already
            # landed).  The side is its LEG -- the transfer's status
            # and the side's record -- since plan step
            # balance:X-bi-6-4d-2, which stopped keeping the expense
            # shadow's status and figure.
            checking_side = _side_leg(xfer.id, ctx["checking_id"])
            assert leg_settled_contribution(checking_side) == PITI_WITH_ESCROW, (
                f"Expected checking side = {PITI_WITH_ESCROW} "
                f"(full PITI per E-01); got "
                f"{leg_settled_contribution(checking_side)}."
            )

            balance_after = _resolve_balance(
                ctx["mortgage_id"], ctx["loan_params"],
                ctx["scenario_id"], _after_the_money_moved(),
            )
            assert balance_after == BALANCE_AFTER_1, (
                f"E-01 violation: expected principal-only reduction to "
                f"{BALANCE_AFTER_1}; got {balance_after}.  If the value "
                f"is 299301.35 escrow leaked into principal; if it is "
                f"300000.00 the settle did not propagate."
            )
            assert balance_after != Decimal("299301.35"), (
                "E-01 violation: balance equals the escrow-included "
                "reduction (300000 - 698.65), meaning escrow was "
                "treated as extra principal."
            )

    def test_unsettled_transfer_no_reduction(  # C14-3
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C14-3: A Projected (unconfirmed) transfer leaves the resolver
        balance unchanged.

        Future commitments are not historical fact.  Only
        ``is_confirmed`` payments (those whose status has
        ``is_settled = True``) reduce the principal.  Without this
        guard the resolver would double-count every projected
        recurrence-engine row alongside the actual settlements.
        The ``auth_client`` fixture is included to keep the test
        symmetric with the settle-driven cases (and to confirm the
        Flask test context is live), but no HTTP POST is issued --
        the transfer remains Projected.

        The pre-condition reads the transfer and its loan side's record,
        where it read the loan-side income shadow's status until plan step
        ``balance:X-bi-6-4d-2`` made a transfer one row (ruling **R-BAL167**
        class 1): Projected, and no payment recorded on the loan.
        """
        _ = auth_client  # Fixture kept for context-setup symmetry.
        with app.app_context():
            ctx = self._setup_loan(seed_user, seed_periods)

            xfer = _create_piti_transfer(
                user_id=ctx["user_id"],
                from_account_id=ctx["checking_id"],
                to_account_id=ctx["mortgage_id"],
                pay_period_id=ctx["periods"][3].id,
                scenario_id=ctx["scenario_id"],
                amount=CONTRACTUAL_PI,
                category_id=ctx["category_id"],
            )
            db.session.commit()

            assert xfer.status.is_settled is False, (
                f"Pre-condition: the transfer must start unsettled; "
                f"status={xfer.status.name!r}"
            )
            assert transfer_side_record(
                db.session, xfer.id, ctx["mortgage_id"],
            ) is None, "Pre-condition: the loan side must hold no payment record"

            balance = _resolve_balance(
                ctx["mortgage_id"], ctx["loan_params"],
                ctx["scenario_id"], _after_the_money_moved(),
            )
            assert balance == ORIGINAL_PRINCIPAL, (
                f"Projected transfer must not reduce principal; "
                f"expected {ORIGINAL_PRINCIPAL}, got {balance}."
            )

    def test_multiple_settlements_cumulative(  # C14-4
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """C14-4: Three settled transfers reduce the resolved principal by
        the sum of their principal portions.

        Setup: same $300k fixed-rate loan; three PITI transfers in
        pay periods 3 (Feb 13), 5 (Mar 13), and 7 (Apr 10) so each
        lands in a distinct calendar month -- avoids the biweekly
        same-month redistribution path in
        :func:`loan_payment_service.prepare_payments_for_engine`
        and keeps the arithmetic in lockstep with the C13-5
        resolver-level test.

        Hand-computed cumulative reductions (see module docstring
        arithmetic table):

            after 1 settle:  bal = 299,701.35  (-298.65)
            after 2 settles: bal = 299,401.21  (-300.14)
            after 3 settles: bal = 299,099.57  (-301.64)

        Asserting the running balance after each settle (not just
        the final state) confirms the resolver composes correctly:
        a future bug that reduces by the constant first-month
        amount instead of recomputing interest off the new balance
        would pass on the first settle and fail on the second.
        """
        with app.app_context():
            ctx = self._setup_loan(seed_user, seed_periods)

            settle_targets = [
                (ctx["periods"][3].id, BALANCE_AFTER_1),
                (ctx["periods"][5].id, BALANCE_AFTER_2),
                (ctx["periods"][7].id, BALANCE_AFTER_3),
            ]

            for period_id, expected_balance in settle_targets:
                xfer = _create_piti_transfer(
                    user_id=ctx["user_id"],
                    from_account_id=ctx["checking_id"],
                    to_account_id=ctx["mortgage_id"],
                    pay_period_id=period_id,
                    scenario_id=ctx["scenario_id"],
                    amount=CONTRACTUAL_PI,
                    category_id=ctx["category_id"],
                )
                db.session.commit()

                resp = _mark_done_from_the_loan_leg(
                    auth_client, xfer.id, ctx["mortgage_id"],
                )
                assert resp.status_code == 200, (
                    f"mark_done returned {resp.status_code}; "
                    f"body={resp.data!r}"
                )
                db.session.expire_all()

                balance = _resolve_balance(
                    ctx["mortgage_id"], ctx["loan_params"],
                    ctx["scenario_id"], _after_the_money_moved(),
                )
                assert balance == expected_balance, (
                    f"Cumulative settle through period {period_id}: "
                    f"expected {expected_balance}, got {balance}.  "
                    f"A constant-reduction bug would yield "
                    f"{ORIGINAL_PRINCIPAL - PRINCIPAL_PORTION_M1 * 3}."
                )


@pytest.mark.parametrize(
    "as_of_offset_months",
    [1, 2, 3, 6],
    ids=["as_of_+1mo", "as_of_+2mo", "as_of_+3mo", "as_of_+6mo"],
)
def test_resolved_balance_stable_across_future_as_of(  # noqa: D401
    app, auth_client, seed_user, seed_periods, db, as_of_offset_months,
):
    """Symptom-#3 corollary: once a transfer settles, the resolved balance is
    the same for any ``as_of`` after the money moved, regardless of how far
    into the future the consumer asks.

    Pre-fix the displayed principal moved only when the user
    re-edited the form -- it would have read $300,000.00 on every
    surface for every ``as_of`` value.  Post-fix the balance reflects
    the settle and stays at $299,701.35 one, two, three and six months
    on, because no further payments have arrived.  Parametrising
    over multiple ``as_of`` proves the replay does not silently reset
    or drift when the resolver is called multiple times for the same
    underlying state.

    The offsets run from the day the money MOVED, not from the payment's pay
    period: before that day the payment has not happened, and the balance is
    correctly still $300,000.00 (see the module docstring).
    """
    with app.app_context():
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        user = seed_user["user"]
        periods = seed_periods
        category = seed_user["categories"]["Car Payment"]

        mortgage, loan_params = _create_mortgage(seed_user, periods[0])
        db.session.commit()

        xfer = _create_piti_transfer(
            user_id=user.id,
            from_account_id=checking.id,
            to_account_id=mortgage.id,
            pay_period_id=periods[3].id,
            scenario_id=scenario.id,
            amount=CONTRACTUAL_PI,
            category_id=category.id,
        )
        db.session.commit()

        resp = _mark_done_from_the_loan_leg(auth_client, xfer.id, mortgage.id)
        assert resp.status_code == 200

        db.session.expire_all()
        # The base is the day the settle door recorded, so every offset lands
        # after the money moved on whatever day the suite runs.
        as_of = _after_the_money_moved(as_of_offset_months)

        balance = _resolve_balance(
            mortgage.id, loan_params, scenario.id, as_of,
        )
        assert balance == BALANCE_AFTER_1, (
            f"Resolver drifted for as_of={as_of}: expected "
            f"{BALANCE_AFTER_1}, got {balance}."
        )


@pytest.mark.parametrize("days_before_settle", [1, 0])
def test_the_replay_and_the_balance_seam_agree_on_what_has_happened(
    app, auth_client, seed_user, seed_periods, db, days_before_settle,
):
    """Plan step **X-an** / finding **N-187**: ONE answer, two producers.

    The oracle the step exists to restore, and the one no unit test can give:
    the resolver's anchor replay and the ``balance_at`` seam -- the producer
    every screen actually reads -- must classify a payment the same way on
    every date.  They did not.  ``mark-done`` on a transfer budgeted to the
    2026-02-13 pay period stamps the settle day with the suite's own today, and
    the seam counts the principal from that day; the replay counted it from the
    pay period, months earlier, so the two disagreed by exactly one
    installment's principal on every date in between.

    Parametrised across the seam itself: the day BEFORE the money moved (no
    reduction, on both producers) and the day OF (the full $298.65, on both).

    The settle day is read off the loan side's record, where it was read off
    the income twin's ``settled_on`` (ruling R-BAL167 class 1, plan step
    balance:X-bi-6-4d-2).
    """
    with app.app_context():
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        user = seed_user["user"]
        periods = seed_periods
        category = seed_user["categories"]["Car Payment"]

        mortgage, loan_params = _create_mortgage(seed_user, periods[0])
        db.session.commit()

        xfer = _create_piti_transfer(
            user_id=user.id,
            from_account_id=checking.id,
            to_account_id=mortgage.id,
            pay_period_id=periods[3].id,
            scenario_id=scenario.id,
            amount=CONTRACTUAL_PI,
            category_id=category.id,
        )
        db.session.commit()
        xfer_id = xfer.id

        resp = _mark_done_from_the_loan_leg(auth_client, xfer_id, mortgage.id)
        assert resp.status_code == 200
        db.session.expire_all()

        cash_day = transfer_side_record(
            db.session, xfer_id, mortgage.id,
        ).settled_on
        assert cash_day == display_today(), (
            "Pre-condition: the settle door stamps the owner's civil day, "
            f"which this test's dates are derived from; got {cash_day}."
        )
        as_of = cash_day - timedelta(days=days_before_settle)
        expected = (
            ORIGINAL_PRINCIPAL if days_before_settle else BALANCE_AFTER_1
        )

        replayed = _resolve_balance(
            mortgage.id, loan_params, scenario.id, as_of,
        )
        ctx = balance_at.BalanceContext.build(user.id, as_of=as_of)
        seam = balance_at.positions(mortgage, ctx, [as_of])[as_of]

        assert replayed == expected, (
            f"Replay at {as_of} (cash moved {cash_day}): expected "
            f"{expected}, got {replayed}."
        )
        assert seam == expected, (
            f"Seam at {as_of} (cash moved {cash_day}): expected "
            f"{expected}, got {seam}."
        )
        assert replayed == seam, (
            f"The two producers disagree at {as_of}: replay {replayed} vs "
            f"seam {seam}.  They classify a payment by the same day since "
            f"plan step X-an."
        )
