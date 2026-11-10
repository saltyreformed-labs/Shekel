"""Commit 15: loan-resolver single-source-of-truth integration locks.

Symptom #5 (loan facet) and F-008 / F-015 / F-016 family: pre-E-18,
three sources rendered "this loan's current balance" -- the loan
card read the STORED ``LoanParams.current_principal`` column, the
/savings debt card read the same stored column, and the year-end
net-worth liability read the schedule rendered by
``amortization_engine.generate_schedule`` with payments=ALL.  The
three values silently diverged whenever the stored column was not
re-typed after a settle (the symptom-#3 frozen-principal bug).

Commit 15 routes every display surface through
``loan_resolver.resolve_loan`` so the same dollar appears on every
card.  These tests lock that invariant: render the loan dashboard
card, the /savings debt card row, and the resolver-derived
net-worth liability against the same fixture and assert they all
report the same Decimal.  Plus C15-6 (settled-transfer-reduces-
card, deferred from Commit 14) and C15-2 (ARM fixed-window payment
stability across surfaces).

Hand-computed expectations follow the same arithmetic conventions
as ``tests/test_integration/test_loan_principal_settles.py`` so the
two files reinforce each other.  Schema-tier locks for the migration
demotion (C15-4 column nullability, C15-5 downgrade round-trip) live
in ``tests/test_models/test_loan_params_demoted.py``; the grep
sweep gate (C15-3) lives in ``tests/test_audit_fixes.py``.
"""

import re
from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.extensions import db
from app.services import (
    loan_loaders,
    loan_payment_service,
    loan_resolver,
    savings_dashboard_service,
)
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    amount_basis_for_scenario,
    create_loan_account,
    create_settled_transfer,
    loan_params_for,
    unseeded_replay_balance,
)


# -- Hand-computed reference values (mirror principal-settle ones) ---------
#
# Loan: $300,000 fixed-rate, 6% annual, 360 months, origination
# 2026-01-01, payment_day=1.  Same fixture as
# ``test_loan_principal_settles.py``; identical arithmetic.
#
#     monthly_rate   = 0.06 / 12 = 0.005
#     contractual_pi = amortize(300000, 0.06, 360) = $1,798.65
#     after 1 settled payment in period 3:
#         interest = 300000 * 0.005          = 1500.00
#         principal_portion = 1798.65 - 1500 =  298.65
#         balance = 300000 - 298.65          = 299,701.35
#
# ARM fixed-window stability fixture follows Commit 13's
# ``test_resolved_balance_stable_across_future_as_of`` template:
#
#     5/5 ARM, $400,000, 6% annual, 360 months, origination
#     2026-01-01, arm_first_adjustment_months=60.  Anchor is the
#     origination event; no payments.  The fixed-window constant is
#         amortize(400000, 0.06, 360) = $2,398.20  (E-02 invariant)
ORIGINATION_DATE = date(2026, 1, 1)
# The fixed loan's: the month before its one payment's 2026-03-01 installment
# (``seed_periods[3]``).  Every contractual installment from origination is
# charged since plan step recurrence:R16-c-2 (ruling R-R101), so the ARM's
# 2026-01-01 would leave February unpaid ahead of that payment.
FIXED_ORIGINATION_DATE = date(2026, 2, 1)
FIXED_PRINCIPAL = Decimal("300000.00")
FIXED_RATE = Decimal("0.06000")
FIXED_TERM = 360
FIXED_PI = Decimal("1798.65")
BALANCE_AFTER_ONE_SETTLE = Decimal("299701.35")

ARM_PRINCIPAL = Decimal("400000.00")
ARM_RATE = Decimal("0.06000")
ARM_TERM = 360
ARM_WINDOW = 60
ARM_FIXED_WINDOW_PAYMENT = Decimal("2398.20")


# -- Fixture helpers -------------------------------------------------------


def _create_fixed_loan(seed_user):
    """Materialise the canonical fixed-rate $300k mortgage.

    Routes through the shared :func:`create_loan_account` factory, which builds
    the :class:`Account`, the :class:`LoanParams` and the origination
    :class:`RateHistory` / anchor in one transaction -- the same dance
    ``app/routes/loan/params.py`` performs on every production loan write.
    Commits.  Returns the account.

    Args:
        seed_user: The ``seed_user`` fixture dict.
    """
    return create_loan_account(
        seed_user, db.session, name="Single-Source Mortgage",
        principal=FIXED_PRINCIPAL, rate=FIXED_RATE, term=FIXED_TERM,
        origination_date=FIXED_ORIGINATION_DATE, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )


def _create_arm_loan(seed_user):
    """Materialise the canonical 5/5 ARM in its fixed-rate window.

    Anchor at origination; no payments; the resolver's monthly_payment
    must equal :data:`ARM_FIXED_WINDOW_PAYMENT` for every ``as_of``
    inside the window (E-02 invariant, Commit 13 stability lock).

    The shared factory carries no ARM knobs, so the ARM columns are set the way
    production's own ARM edit does (``loan.update_params``): assign the params,
    then commit.  (Both the door and this helper re-synced the stored ledger's
    genesis postings before the commit until plan tasks plan#3076 and plan#3132.)

    Args:
        seed_user: The ``seed_user`` fixture dict.
    """
    account = create_loan_account(
        seed_user, db.session, name="Single-Source ARM",
        principal=ARM_PRINCIPAL, rate=ARM_RATE, term=ARM_TERM,
        origination_date=ORIGINATION_DATE, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    loan_params = loan_params_for(db.session, account.id)
    loan_params.is_arm = True
    loan_params.arm_first_adjustment_months = ARM_WINDOW
    loan_params.arm_adjustment_interval_months = 12
    db.session.commit()
    return account, loan_params


def _settle_one_payment(seed_user, loan_account, period):
    """Settle a PITI transfer through the sole writer, pinned to the period start.

    Routes through ``create_settled_transfer`` -- transfer_service, the same
    settle state machine (``update_transfer`` to DONE) the mark-done route drives
    -- with the settle day pinned to the period's start, so the payment is visible
    today under C2's settled-date clock.  The HTTP mark-done route stamps
    ``now()``, whose UTC-civil date can sit a day ahead of the host's
    ``date.today()`` and hide the payment; that route path is covered directly by
    ``test_loan_principal_settles.py``.
    """
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan_account, period,
        amount=FIXED_PI, settled_on=period.start_date,
    )
    # COMMITTED before the surfaces are read (plan step balance:X-i3).  The
    # whole point of this test is that the loan CARD -- rendered by a request
    # -- reports the same balance the replay does, and a request cannot see a
    # settle this fixture never committed.  ``expire_all`` alone dropped the
    # identity map without making the rows visible to anyone else.
    db.session.commit()
    db.session.expire_all()


def _replay_window(account_id, scenario_id):
    """Return the anchor + settled-payment replay balance as of today.

    The sanity-floor window the deleted ``LoanState.current_balance`` carried
    (plan step D2a): :func:`tests._test_helpers.unseeded_replay_balance`, the
    suite's ONE assembly of that replay, so the hand-computed pins below keep
    their values while the display surfaces under test read the seam.

    It took a loaded ``LoanContext`` -- and so the whole pricing tier -- until
    plan step **balance:X-bl-2b** put the replay on the amount-free feed.  It
    takes the SCENARIO now, because that is all the feed needs to be scoped.
    """
    return unseeded_replay_balance(account_id, scenario_id, date.today())


def _loan_card_principal(auth_client, account_id):
    """Extract the loan detail band's owed balance from the dashboard HTML.

    Parses the band hero -- "Balance owed" (the Fable 5 rebuild's owed hero,
    the same figure the old "Current Principal" row showed) -- and returns the
    dollar value as a Decimal.  This is the *display* contract: anything the
    route sets but does not render falls outside the lock.
    """
    resp = auth_client.get(f"/accounts/{account_id}/loan")
    assert resp.status_code == 200
    html = resp.data.decode()
    match = re.search(
        r"Balance owed[\s\S]*?\$([\d,]+\.\d{2})", html,
    )
    assert match, (
        "Did not find the Balance owed hero on the loan dashboard. "
        f"HTML excerpt: {html[:500]}"
    )
    return Decimal(match.group(1).replace(",", ""))


def _loan_card_monthly_payment(auth_client, account_id):
    """Extract the loan card's Monthly P&I display.

    Returns the displayed monthly P&I as a Decimal.  Locks the ARM
    fixed-window stability invariant (C15-2 / E-02).
    """
    resp = auth_client.get(f"/accounts/{account_id}/loan")
    assert resp.status_code == 200
    html = resp.data.decode()
    match = re.search(
        r"Monthly P&I[\s\S]*?\$([\d,]+\.\d{2})", html,
    )
    assert match, (
        "Did not find the Monthly P&I row on the loan dashboard. "
        f"HTML excerpt: {html[:500]}"
    )
    return Decimal(match.group(1).replace(",", ""))


def _savings_debt_card_total_debt(user_id):
    """Return the total_debt figure rendered by the /savings debt card.

    Drives the same ``savings_dashboard_service.compute_dashboard_data``
    helper the route calls, then reads ``debt_summary.total_debt``
    -- the Decimal that backs the "Total Debt" tile.
    """
    data = savings_dashboard_service.compute_dashboard_data(
        BalanceContext.build(user_id),
    )
    summary = data["debt_summary"]
    assert summary is not None, "Expected at least one loan account"
    return summary.total_debt


# -- C15-1 / C15-6 fixed-rate cross-surface tests --------------------------


def test_fixed_loan_card_equals_savings_equals_resolver_before_settle(
    app, auth_client, seed_user, seed_periods, db,
):
    """C15-1 (pre-settle): every surface displays the same $300,000 anchor.

    Fresh fixed-rate mortgage with one origination event and zero
    confirmed payments.  Both display surfaces must show
    ``$300,000.00`` exactly -- the seam-folded balance for
    ``as_of = date.today()`` (the anchor replay agrees, the sanity floor).

    Pre-Commit-15 this would have rendered the stored
    ``LoanParams.current_principal`` ($300,000) on the loan card and
    /savings debt card while the year-end branch derived $300,000
    from the schedule -- coincidentally aligned because no payments
    had settled, but breakage hides in this case (the stored column
    and the schedule could diverge by an arbitrary amount once any
    settle landed; see :func:`test_fixed_loan_card_equals_savings_after_settle`).
    """
    with app.app_context():
        account = _create_fixed_loan(seed_user)

        replayed = _replay_window(account.id, seed_user["scenario"].id)
        assert replayed == FIXED_PRINCIPAL, (
            f"Sanity floor: the anchor replay should report {FIXED_PRINCIPAL} "
            f"for a fresh loan, got {replayed}."
        )

        card_balance = _loan_card_principal(auth_client, account.id)
        debt_balance = _savings_debt_card_total_debt(seed_user["user"].id)

        assert card_balance == FIXED_PRINCIPAL, (
            f"Loan card displayed {card_balance}, expected {FIXED_PRINCIPAL}"
        )
        assert debt_balance == FIXED_PRINCIPAL, (
            f"/savings debt card displayed {debt_balance}, "
            f"expected {FIXED_PRINCIPAL}"
        )


def test_fixed_loan_card_equals_savings_after_settle(  # C15-1 / C15-6
    app, auth_client, seed_user, seed_periods, db,
):
    """C15-1 (post-settle) + C15-6: after one settled PITI transfer,
    every display surface shows the same hand-computed balance.

    Pre-Commit-15 the loan card and /savings debt card both rendered
    ``LoanParams.current_principal = $300,000`` (unchanged by the
    settle -- the symptom-#3 freeze), while the year-end schedule
    showed $299,701.35.  Three surfaces, two values.

    Post-Commit-15 every surface reads the resolver and shows
    ``$299,701.35`` exactly:

        interest          = 300000.00 * 0.005   = 1500.00
        principal_portion = 1798.65  - 1500.00  =  298.65
        balance           = 300000.00 -  298.65 = 299,701.35
    """
    with app.app_context():
        account = _create_fixed_loan(seed_user)
        _settle_one_payment(seed_user, account, seed_periods[3])

        scenario_id = seed_user["scenario"].id
        assert _replay_window(account.id, scenario_id) == BALANCE_AFTER_ONE_SETTLE

        # F-008 / F-015 / F-016 / symptom #5 re-pin: loan card display
        # equals the seam-folded balance, not the stored
        # ``current_principal`` column.  Arithmetic above; same Decimal
        # the replay window reports.
        card_balance = _loan_card_principal(auth_client, account.id)
        assert card_balance == BALANCE_AFTER_ONE_SETTLE, (
            f"Loan card displayed {card_balance}, expected "
            f"{BALANCE_AFTER_ONE_SETTLE} (resolver-derived)."
        )

        # /savings debt card: total_debt sums the seam-folded balances
        # across loan accounts.  Single loan, so total == card balance.
        debt_balance = _savings_debt_card_total_debt(seed_user["user"].id)
        assert debt_balance == BALANCE_AFTER_ONE_SETTLE, (
            f"/savings debt card total_debt={debt_balance}, "
            f"expected {BALANCE_AFTER_ONE_SETTLE} (resolver)."
        )


# -- C15-2 ARM cross-surface stability ------------------------------------


def test_arm_monthly_payment_card_equals_resolver_constant(  # C15-2
    app, auth_client, seed_user, seed_periods, db,
):
    """C15-2 / E-02 invariant: ARM in its fixed-rate window displays
    the SAME monthly P&I as the resolver's hand-computed constant.

    Pre-Commit-15 the loan card rendered ``summary.monthly_payment``
    derived from the (now-deleted, follow-up F-10 / Commit 15)
    ``amortization_engine.get_loan_projection`` wrapper's
    re-amortization branch which, for an ARM, picked the contractual
    payment from the stored ``current_principal`` over a calendar-
    shrinking ``remaining_months`` count -- producing the
    symptom-#4 payment creep ($2,460.45 month 24 -> $2,463.28 month 25
    for a 5/5 ARM at $400k/6%/360mo, both diverging from the correct
    constant $2,398.20).

    Post-Commit-15 the card reads ``state.monthly_payment``, which
    for an ARM whose anchor and as_of both fall inside the
    half-open ``[origination, origination + arm_first_adjustment_months)``
    interval is the level-amortization of the anchor balance over the
    remaining contractual term as of the anchor date.  For our
    fixture (anchor = origination, $400,000, 6%, 360 months) this
    is exactly ``$2,398.20`` and is held constant for every ``as_of``
    inside the 60-month fixed-rate window.
    """
    with app.app_context():
        account, loan_params = _create_arm_loan(seed_user)

        ctx = loan_payment_service.load_loan_context(
            account.id, amount_basis_for_scenario(seed_user["scenario"].id),
            loan_params,
        )
        resolver_state = loan_resolver.resolve_loan(
            loan_resolver.LoanInputs(
                loan_params,
                loan_loaders.load_loan_anchor_facts(loan_params),
                ctx.payments,
                ctx.rate_changes,
            ),
            date.today(),
        )
        # Resolver-side stability lock: the same value Commit 13's
        # ARM-window tests pin.  Hand-computed above.
        assert resolver_state.monthly_payment == ARM_FIXED_WINDOW_PAYMENT

        # Card display lock: the loan card MUST render the same
        # Decimal.  Anything else means the resolver's constant is
        # being silently overwritten somewhere between the route and
        # the template.
        card_payment = _loan_card_monthly_payment(auth_client, account.id)
        assert card_payment == ARM_FIXED_WINDOW_PAYMENT, (
            f"ARM card Monthly P&I={card_payment}, expected "
            f"{ARM_FIXED_WINDOW_PAYMENT} (E-02 fixed-window constant)."
        )
