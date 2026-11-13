"""The loan's posted ledger against hand-worked figures: on and off schedule, the cash invariant
it supersedes, the full sweep, and scenario and owner isolation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.enums import LedgerAccountKindEnum
from app.extensions import db as _db
from app.models.loan_features import RateHistory
from app.models.scenario import Scenario
from app.services import loan_ledger, loan_loaders
from app.services.ledger_lines import loan_key
from tests._ledger_reads import (
    account_net,
    ledger_net,
    loan_correction_lines,
)
from tests._records_oracle import posted_purchase_effect, settled_transfer_effect
from tests._test_helpers import (
    create_loan_account,
    insert_tracking_start_event,
)

from ._helpers import (
    _ORIGINATION_PRINCIPAL,
    _ORIGINATION_DATE,
    _RATE,
    _ANCHOR_BALANCE,
    _ANCHOR_DATE,
    _P1,
    _P2,
    _SCHEDULED_PI,
    _ONE_SCHEDULED_MONTH,
    _TWO_SCHEDULED_MONTHS,
    _ORIGINATION_BEFORE_P1,
    _make_loan,
    _settle,
    _per_loan_correction_net,
    _ledger_balance,
    _posted_balance,
    _assert_loan_reconciles,
)


def _make_tracking_start_loan(
    user, *, tracking_balance=_ANCHOR_BALANCE, tracking_date=_ANCHOR_DATE,
    rate=_RATE, name="Tracking Loan",
):
    """A resolvable loan whose OPENING is a tracking-start (no true-up).

    The mid-life-import shape: origination is $250,000 (2025-01-01) but the
    confirmed ledger opens at *tracking_balance* as of *tracking_date* (default
    the suite's $100,000 / 2026-01-10, before every payment period).  The genesis
    reader synthesizes the opening from that tracking-start
    (``load_loan_anchor_facts``), so a payment's interest is charged on the
    tracking balance and never on the origination principal.
    """
    loan = create_loan_account(
        user, _db.session, name=name, principal=_ORIGINATION_PRINCIPAL,
        rate=rate, origination_date=_ORIGINATION_DATE, term=360,
    )
    insert_tracking_start_event(
        loan_loaders.load_loan_params(loan.id), tracking_balance, tracking_date,
    )
    _db.session.commit()
    return loan


def _add_rate_change(loan, effective_date, rate):
    """Append a :class:`RateHistory` rate change (an ARM recast) to a loan.

    Mirrors the unit suite's helper (``test_a_loans_lines._helpers._add_rate_change``)
    so both suites drive an ARM step the same way: the rate feed the walk reads
    picks it up, so the step governs the installment due on its day.
    """
    _db.session.add(RateHistory(
        account_id=loan.id, effective_date=effective_date, interest_rate=rate,
    ))
    _db.session.flush()


def _loan_ledger_net(loan, kind, scenario_id: int) -> Decimal:
    """Return the net of a loan's own line of *kind*."""
    return ledger_net(_db.session, loan_key(kind, loan.id), scenario_id)


# ---------------------------------------------------------------------------
# 1. The ledger against hand-worked figures (plan 8.2)
# ---------------------------------------------------------------------------


class TestLedgerAgainstHandWorkedFigures:
    """The posted ledger's balance equals a figure worked by hand, on and off schedule.

    It graded the ledger against the old schedule replay until plan task plan#3384
    (ruling plan#3383): that replay is deleted with walk 3, so every value here is
    a literal with its arithmetic shown.  The literal is also the stronger check:
    a replay that moved with the ledger could not see a bug they shared.
    """

    def test_a_scheduled_payment_books_its_hand_worked_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """Paying exactly the contract's P&I books its principal, worked by hand.

        Arithmetic (:data:`_SCHEDULED_PI`): interest round(100000.00 * 0.005) =
        500.00, principal 1498.88 - 500.00 = 998.88, so the ledger owes
        100000.00 - 998.88 = 99,001.12.  The genesis linked net is the opening
        (-250000) + the true-up (+150000) + that principal (+998.88) =
        -99,001.12.  Its correction is booked (the sweep is not vacuous).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI,
            )
            db.session.commit()

            assert _ledger_balance(loan.id, scenario_id) == _ONE_SCHEDULED_MONTH
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-99001.12")
            assert loan_correction_lines(db.session, xfer.id, loan.id) != []
            _assert_loan_reconciles(loan, scenario_id)

    def test_tracking_start_opening_books_from_the_tracking_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-life tracking-start loan accrues from the tracking balance, not origination.

        The opening is a tracking_start ($100,000 as of 2026-01-10), NOT the
        $250,000 origination.  One payment of the contract's P&I then charges
        round(100000.00 * 0.005) = 500.00 of interest and books 998.88 of
        principal, so the ledger owes 99,001.12 (:data:`_SCHEDULED_PI`).  Had the
        walk seeded from origination the interest would be
        round(250000.00 * 0.005) = 1250.00 and the balance 249,751.12.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_tracking_start_loan(seed_user)

            _settle(seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI)
            db.session.commit()

            assert _ledger_balance(loan.id, scenario_id) == _ONE_SCHEDULED_MONTH
            _assert_loan_reconciles(loan, scenario_id)

    def test_two_scheduled_payments_compound_on_the_lower_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Two on-schedule payments: the second month's interest is on the lower balance.

        Arithmetic (:data:`_SCHEDULED_PI`): month 1 leaves 99,001.12; month 2
        charges round(99001.12 * 0.005) = 495.01 and books 1498.88 - 495.01 =
        1,003.87, leaving 97,997.25.  A walk that charged the first month's
        $500.00 again would leave 98,002.24, so the running balance is what this
        pins.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)

            _settle(seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI)
            _settle(seed_user, loan, seed_periods[_P2], amount=_SCHEDULED_PI)
            db.session.commit()

            assert _ledger_balance(loan.id, scenario_id) == _TWO_SCHEDULED_MONTHS
            _assert_loan_reconciles(loan, scenario_id)

    def test_a_short_payment_books_its_real_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A short payment books only the principal the cash covers.

        Arithmetic (100000 @ 6%): interest 500.00; a $1,000 payment is short of
        the $1,498.88 contract P&I, so the real principal is 1000 - 500 =
        500.00 and the ledger owes 100000 - 500 = 99,500.00 -- more than the
        on-schedule 99,001.12, by the 498.88 the payment fell short.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()

            assert _ledger_balance(loan.id, scenario_id) == Decimal("99500.00")
            # Genesis: opening (-250000) + true-up (+150000) + principal (+500).
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-99500.00")
            _assert_loan_reconciles(loan, scenario_id)

    def test_extra_principal_books_the_whole_excess(
        self, app, db, seed_user, seed_periods,
    ):
        """An overpayment books the whole excess as principal.

        Arithmetic (100000 @ 6%): interest 500.00; a $2,000 payment exceeds the
        $1,498.88 contract P&I, so the real principal is 2000 - 500 = 1,500.00
        and the ledger owes 100000 - 1500 = 98,500.00 -- less than the
        on-schedule 99,001.12, by the 501.12 paid over.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("2000.00"))
            db.session.commit()

            assert _ledger_balance(loan.id, scenario_id) == Decimal("98500.00")
            # Genesis: opening (-250000) + true-up (+150000) + principal (+1500).
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-98500.00")
            _assert_loan_reconciles(loan, scenario_id)

    def test_arm_rate_step_matches_a_hand_computed_post_step_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """An ARM rate step: the ledger balance equals a HAND-COMPUTED literal.

        A rate step to 12% effective 2026-03-01 governs P2 (due 2026-03-01, the
        step's own day) while P1 (due 2026-02-01) keeps the 6% origination
        rate.  P2 and not a later installment since plan step
        recurrence:R16-c-2: every contractual installment is charged now, so a
        payment skipping March would clear March's interest too (the fixture
        is restated so nothing is left unpaid, ruling R-R103).  Each payment
        is a $1,000 SHORT payment, so the ledger's REAL principal (cash -
        interest) is hand-computable without the schedule (the same partition
        ``test_arm_rate_step_changes_interest`` pins at the unit level):
          P1 (6%):  interest = round(100000 * 0.06 / 12) = 500.00;
                    principal = 1000 - 500 = 500.00; balance 99,500.00.
          P2 (12%): interest = round( 99500 * 0.12 / 12) = 995.00;
                    principal = 1000 - 995 =   5.00; balance 99,495.00.
        So the ledger balance is 99,495.00 and the genesis linked total is
        opening (-250000) + true-up (+150000) + P1 principal (+500) + P2
        principal (+5) = -99,495.00.  The dated reader reads that same ledger
        bounded at today, which no line here is dated past, so it agrees.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _add_rate_change(loan, date(2026, 3, 1), Decimal("0.12000"))
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            _settle(seed_user, loan, seed_periods[_P2], amount=Decimal("1000.00"))
            db.session.commit()

            assert _ledger_balance(loan.id, scenario_id) == Decimal("99495.00")
            assert _posted_balance(loan.id, scenario_id) == Decimal("99495.00")
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-99495.00")
            _assert_loan_reconciles(loan, scenario_id)

    def test_pre_anchor_payment_is_correctly_summed_under_genesis(
        self, app, db, seed_user, seed_periods,
    ):
        """A pre-anchor payment is SPLIT and summed, not excluded -- genesis fix.

        Genesis retires the Step-4 pre-anchor exclusion (the read-switch boundary
        the prior draft carved out): the walk splits EVERY payment from
        origination, and the opening + true-up corrections absorb the pre-anchor
        principal, so the from-origination sum-of-postings lands on the verified
        balance with NO boundary rule.  With the trueup at 2026-02-15, P1 (due
        2026-02-01) is pre-trueup: it splits on the $250,000 origination balance
        (interest round(250000 * 0.005) = 1250.00, principal 1000 - 1250 =
        -250.00, so the walk carries 250250 to the trueup).  The opening books
        -250000 and the true-up 250250 - 100000 = +150250; with P1's cash
        (+1000) and correction loan leg (-1250) the loan-linked net is
        -100000.00 -- exactly -(the verified $100,000 true-up, which subsumes
        P1) -- so the pre-anchor payment is summed correctly, never the old
        double-count.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Trueup AFTER P1's 2026-02-01 due date, so P1 is pre-trueup.
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 2, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            # Genesis SPLITS the pre-trueup payment (Step 4 excluded it).
            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, scenario_id,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")
            assert loan_correction_lines(db.session, xfer.id, loan.id) != []

            # -(linked net) is the verified true-up, which subsumes P1.
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == -_ANCHOR_BALANCE  # -100000.00, no pre-anchor pollution


# ---------------------------------------------------------------------------
# 2. Supersedes the cash per-account invariant for loans (plan 8.7)
# ---------------------------------------------------------------------------


class TestSupersedesCashInvariantForLoans:
    """The loan-aware invariant holds exactly where the cash per-account one breaks."""

    def test_loan_aware_invariant_holds_where_cash_invariant_breaks(
        self, app, db, seed_user, seed_periods,
    ):
        """Once a correction exists, the loan nets to principal, not to the cash.

        The Step-2 / Step-3 oracle's per-account invariant is
        ``account_net(A) == settled_transfer_effect(A) +
        posted_purchase_effect(A)``.  For this loan after one $1,000 payment
        the settled transfer effect (cash in) is +1,000 and there are no ordinary
        transactions, so the cash invariant would demand the ledger net +1,000 --
        but under genesis the loan-linked ledger nets -99500 (the $500 interest
        moved off it, plus the opening -250000 and true-up +150000).  So the cash
        invariant PROVABLY breaks (-99500 != +1000), while the loan-aware
        superseding invariant (``linked == settled_transfer_effect - per-loan
        corrections``, the per-loan side now including the opening-equity legs)
        holds.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()

            linked = account_net(_db.session, loan.id, scenario_id)
            cash_effect = settled_transfer_effect(
                loan.id, scenario_id,
            )
            txn_effect = posted_purchase_effect(
                loan.id, scenario_id,
            )
            non_principal = _per_loan_correction_net(loan.id, scenario_id)

            # The cash per-account invariant would demand linked == cash + txn.
            assert cash_effect == Decimal("1000.00")
            assert txn_effect == Decimal("0.00")
            # Genesis: opening (-250000) + true-up (+150000) + principal (+500).
            assert linked == Decimal("-99500.00")
            assert linked != cash_effect + txn_effect  # the cash invariant BREAKS
            # The loan-aware superseding invariant holds; the per-loan side is the
            # $500 interest plus the opening-equity legs (+100000 = -(opening
            # -250000 + true-up +150000)).
            assert non_principal == Decimal("100500.00")
            assert linked == cash_effect - non_principal
            _assert_loan_reconciles(loan, scenario_id)


# ---------------------------------------------------------------------------
# 3. A rich fixture: interest + escrow + refund legs all reconcile (8.1/8.3/8.4)
# ---------------------------------------------------------------------------


class TestRichFixtureFullSweep:
    """A payoff-overpayment on an escrow loan books all four parts and reconciles."""

    def test_escrow_and_refund_reconcile_full_sweep(
        self, app, db, seed_user, seed_periods,
    ):
        """One $2,000 payment on a $1,000 escrow loan books interest, escrow, refund.

        Arithmetic (anchor 1,000 @ 6%, escrow $1,200/yr = $100.00/mo): interest =
        round(1000 * 0.005) = 5.00; escrow 100.00; principal0 = 2000 - 5 - 100 =
        1,895.00 > 1,000, so principal caps at the 1,000 balance and the surplus
        1,895 - 1,000 = 895.00 routes to the Refund receivable; the loan closes at
        0.  So the correction books interest +5.00 (Expense), escrow +100.00
        (Expense), refund +895.00 (Asset), and the loan leg -1,000.00.  The
        payment contributes +1,000 of principal; with the opening (-250000) and
        true-up (+249000 = 250000 - 1000) the loan-linked ledger nets 0.00 -- the
        loan is paid off, and the whole sweep ties (this is the only fixture
        exercising interest + escrow + refund together).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(
                seed_user, anchor_balance=Decimal("1000.00"),
                escrow_annual=Decimal("1200.00"),
            )
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("2000.00"))
            db.session.commit()

            # Each per-loan leg is hand-computed.
            assert _loan_ledger_net(
                loan, LedgerAccountKindEnum.LOAN_INTEREST, scenario_id,
            ) == Decimal("5.00")
            assert _loan_ledger_net(
                loan, LedgerAccountKindEnum.LOAN_ESCROW, scenario_id,
            ) == Decimal("100.00")
            assert _loan_ledger_net(
                loan, LedgerAccountKindEnum.LOAN_REFUND, scenario_id,
            ) == Decimal("895.00")
            # Paid off: opening (-250000) + true-up (+249000) + principal (+1000).
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("0.00")
            # Per-loan net = non-principal (1000) + opening-equity legs (+1000,
            # the negatives of opening -250000 + true-up +249000).
            assert _per_loan_correction_net(
                loan.id, scenario_id,
            ) == Decimal("2000.00")
            # The loan is paid off: the ledger balance is zero.
            assert _ledger_balance(loan.id, scenario_id) == Decimal("0.00")
            _assert_loan_reconciles(loan, scenario_id)


# ---------------------------------------------------------------------------
# 4. Scenario and owner isolation of the whole sweep (plan 8.8)
# ---------------------------------------------------------------------------


class TestScenarioAndOwnerIsolation:
    """Corrections in one scenario / owner never reconcile against another's."""

    def test_two_scenarios_reconcile_independently(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment in each of two scenarios reconciles independently.

        The loan's anchor and rate live on the account, so a $1,000 payment splits
        the same in both scenarios (interest 500, principal 500); the opening +
        true-up post per-scenario too.  Scoped to the baseline the loan-linked
        ledger nets -99500 (opening -250000 + true-up +150000 + principal 500);
        scoped to the what-if it also nets -99500 -- and neither picks up the
        other (never -199000).  Each scenario's whole sweep ties on its own.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _make_loan(seed_user)
            _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
                scenario=baseline,
            )
            _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
                scenario=whatif,
            )
            db.session.commit()

            # Each scenario nets -99500 in isolation -- never -199000.
            assert account_net(
                _db.session, loan.id, baseline.id,
            ) == Decimal("-99500.00")
            assert account_net(
                _db.session, loan.id, whatif.id,
            ) == Decimal("-99500.00")
            _assert_loan_reconciles(loan, baseline.id)
            _assert_loan_reconciles(loan, whatif.id)

    def test_two_owners_reconcile_independently(
        self, app, db, seed_user, seed_second_user, seed_periods,
        seed_second_periods,
    ):
        """Two owners each settle a loan payment; neither sees the other's.

        Owner 1 settles a $1,000 payment on their loan (real principal 500); owner
        2 settles a $2,000 payment on theirs (real principal 1,500).  Each loan
        reconciles in its owner's own baseline scenario, and neither owner's
        figure carries the other's.
        """
        with app.app_context():
            loan1 = _make_loan(seed_user, name="Owner1 Loan")
            _settle(seed_user, loan1, seed_periods[_P1], amount=Decimal("1000.00"))
            loan2 = _make_loan(seed_second_user, name="Owner2 Loan")
            _settle(
                seed_second_user, loan2, seed_second_periods[_P1],
                amount=Decimal("2000.00"),
            )
            db.session.commit()

            scenario1 = seed_user["scenario"].id
            scenario2 = seed_second_user["scenario"].id
            # Genesis linked net = opening (-250000) + true-up (+150000) + real
            # principal: owner 1 -> -99500 (+500), owner 2 -> -98500 (+1500).
            assert account_net(
                _db.session, loan1.id, scenario1,
            ) == Decimal("-99500.00")
            assert account_net(
                _db.session, loan2.id, scenario2,
            ) == Decimal("-98500.00")

            _assert_loan_reconciles(loan1, scenario1)
            _assert_loan_reconciles(loan2, scenario2)
