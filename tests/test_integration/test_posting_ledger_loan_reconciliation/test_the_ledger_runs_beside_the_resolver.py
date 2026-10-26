"""The loan's posted ledger run beside the resolver: the parallel run, the cash invariant it
supersedes, the full sweep, scenario and owner isolation, and backfill against go-forward.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.loan_features import RateHistory
from app.models.scenario import Scenario
from app.services import loan_ledger, loan_loaders, loan_posting_service, posting_service
from tests._test_helpers import (
    create_loan_account,
    find_loan_ledger_account,
    insert_tracking_start_event,
    derived_span,
    ledger_net,
    load_migration_module,
    loan_correction_entries,
    posted_loan_balance_at,
    posted_loan_balance_map,
)

from ._helpers import (
    _ORIGINATION_PRINCIPAL,
    _ORIGINATION_DATE,
    _RATE,
    _ANCHOR_BALANCE,
    _ANCHOR_DATE,
    _P1,
    _P2,
    _ORIGINATION_BEFORE_P1,
    _AS_OF,
    _make_loan,
    _resolved_at,
    _settle,
    _independent_loan_linked_net,
    _per_loan_correction_net,
    _resolver_balance,
    _ledger_balance,
    _posted_balance,
    _posted_period_map,
    _assert_loan_reconciles,
)


# The two boundary migrations, loaded so their raw-SQL teardowns can reproduce
# the pre-wiring historical state directly (the same pattern the backfill suite
# uses).  The genesis teardown (opening / true-up + equity accounts) runs before
# the Step-4 payment teardown -- the real downgrade-chain order.
_BACKFILL_MIGRATION = load_migration_module(
    "e2a9f1c7b4d6_backfill_loan_payment_split_postings.py"
)


_GENESIS_MIGRATION = load_migration_module(
    "f3d6b1a8c2e4_loan_genesis_postings_data_boundary.py"
)


def _make_tracking_start_loan(
    user, *, tracking_balance=_ANCHOR_BALANCE, tracking_date=_ANCHOR_DATE,
    rate=_RATE, name="Tracking Loan",
):
    """A resolvable loan whose OPENING is a tracking-start (no true-up).

    The mid-life-import shape: origination is $250,000 (2025-01-01) but the
    confirmed ledger opens at *tracking_balance* as of *tracking_date* (default
    the suite's $100,000 / 2026-01-10, before every payment period).  Both the
    resolver and the genesis reader synthesize the opening from that tracking-start
    (their shared ``load_loan_anchor_facts``), so the parallel run pits their
    disjoint balance math against the SAME tracking-start anchor.
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

    Mirrors the unit suite's helper (``test_loan_posting_service._add_rate_change``)
    so both suites drive an ARM step the same way: the rate feed the walk AND
    the resolver read (via ``load_rate_history`` / ``load_loan_context``) both
    pick it up, so the rate step reaches both producers of the parallel run.
    """
    _db.session.add(RateHistory(
        account_id=loan.id, effective_date=effective_date, interest_rate=rate,
    ))
    _db.session.flush()


def _clear_all_loan_postings():
    """Reproduce the pre-wiring historical state: remove EVERY loan posting.

    Runs the two boundary migrations' own raw-SQL teardowns in real
    downgrade-chain order -- the genesis teardown FIRST (the opening / true-up
    entries + per-loan opening-equity accounts), THEN the Step-4 payment teardown
    (the loan_payment corrections + interest / escrow / refund accounts) -- then
    commits.  The order is load-bearing: the genesis boundary migration
    (``f3d6b1a8c2e4``) is the head, above the Step-4 boundary, so its teardown
    runs first in a real downgrade; running the payment teardown first would
    strand the per-loan opening-equity legs on an already-deleted account.
    Single-sources that ordering so the callers (the backfill-equivalence and the
    reader-gap tests) cannot drift on it -- the same helper the backfill suite
    keeps as ``_clear_corrections``.
    """
    _GENESIS_MIGRATION._remove_loan_genesis_postings(_db.session)
    _BACKFILL_MIGRATION._remove_loan_payment_postings(_db.session)
    _db.session.commit()


def _loan_ledger_net(loan, kind, scenario_id: int) -> Decimal:
    """Return the net of a loan's per-loan ledger of *kind* (0 if not minted)."""
    ledger = find_loan_ledger_account(_db.session, loan.id, kind)
    if ledger is None:
        return Decimal("0")
    return ledger_net(_db.session, ledger.id, scenario_id)


# ---------------------------------------------------------------------------
# 1. Parallel run vs the resolver (plan 8.2)
# ---------------------------------------------------------------------------


class TestParallelRunAgainstResolver:
    """The posted ledger and the independent resolver agree on-schedule, diverge off."""

    def test_on_schedule_payment_matches_resolver(
        self, app, db, seed_user, seed_periods,
    ):
        """Paying exactly the scheduled P&I keeps the ledger == the resolver.

        The resolver reports the loan's scheduled monthly P&I (``period_pi``);
        settling a payment of exactly that amount is on-schedule, so the ledger's
        real principal (cash - interest) equals the resolver's scheduled principal
        (period_pi - interest) and the two balances -- derived by disjoint code
        paths from the SAME anchor -- must agree to the penny.  A payment did post
        (the sweep is not vacuous), and Checking is untouched by the loan sync.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            # The resolver's scheduled monthly P&I; paying exactly it is
            # on-schedule (the loan carries no escrow, so cash == P&I).
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=scheduled_pi,
            )
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == resolver, (
                f"on-schedule ledger {ledger} != resolver {resolver}"
            )
            # Non-vacuity: the genesis linked total is opening (-250000) + true-up
            # (+150000) + the real principal (scheduled P&I - round(100000*0.005)
            # = -500.00 interest), i.e. principal - _ANCHOR_BALANCE.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == scheduled_pi - Decimal("500.00") - _ANCHOR_BALANCE
            assert len(
                loan_correction_entries(db.session, xfer.id, loan.id),
            ) == 1
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_tracking_start_opening_matches_resolver(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-life tracking-start loan: an on-schedule payment keeps ledger == resolver.

        The opening is a tracking_start ($100,000 as of 2026-01-10), NOT the
        $250,000 origination.  Both the genesis ledger reader and the independent
        resolver seed from that tracking-start (their shared anchor facts), so an
        on-schedule payment keeps the two disjoint balance paths locked to the
        penny -- and the balance opens from $100,000, provably below the
        origination principal.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_tracking_start_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == resolver, (
                f"tracking-start ledger {ledger} != resolver {resolver}"
            )
            # Opened at the tracking-start 100000, so the balance is far below
            # the 250000 origination -- proving the walk did NOT seed from it.
            assert ledger < _ORIGINATION_PRINCIPAL
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_on_schedule_multi_payment_matches_resolver(
        self, app, db, seed_user, seed_periods,
    ):
        """Two on-schedule payments keep the ledger == the resolver across the walk.

        Both payments are exactly the scheduled P&I, in distinct due months, so
        the ledger's real-principal walk and the resolver's scheduled-principal
        walk stay locked step-for-step from the shared anchor -- the balances
        agree after both.  This exercises the running-balance coupling that a
        single payment cannot.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            _settle(seed_user, loan, seed_periods[_P2], amount=scheduled_pi)
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == resolver
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_short_payment_diverges_and_ledger_owes_more(
        self, app, db, seed_user, seed_periods,
    ):
        """A short payment: the ledger owes MORE than the contractual resolver.

        Arithmetic (100000 @ 6%): interest 500.00; a $1,000 payment is short of
        the ~$1,498.88 scheduled P&I, so the real principal is 1000 - 500 =
        500.00 and the ledger balance is 100000 - 500 = 99,500.00 (hand-computed,
        no need for the schedule).  The resolver books the FULL scheduled
        principal (it ignores the cash), so it shows a LOWER balance -- they
        diverge, and the ledger is the honest record of a partial paydown that the
        resolver would need an anchor true-up to reflect.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The ledger's real balance, hand-computed.
            assert ledger == Decimal("99500.00")
            # Genesis: opening (-250000) + true-up (+150000) + principal (+500).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")
            # They DIVERGE, and the ledger (short paydown) owes MORE.
            assert ledger != resolver
            assert resolver < ledger
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_extra_principal_diverges_and_ledger_owes_less(
        self, app, db, seed_user, seed_periods,
    ):
        """Extra principal: the ledger owes LESS than the contractual resolver.

        Arithmetic (100000 @ 6%): interest 500.00; a $2,000 payment exceeds the
        ~$1,498.88 scheduled P&I, so the real principal is 2000 - 500 = 1,500.00
        and the ledger balance is 100000 - 1500 = 98,500.00 (hand-computed).  The
        resolver books only the scheduled principal, so it shows a HIGHER balance
        -- the extra $500-ish of principal the ledger captured automatically is
        exactly what the resolver drops on the floor (and would need a true-up to
        recover).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("2000.00"))
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert ledger == Decimal("98500.00")
            # Genesis: opening (-250000) + true-up (+150000) + principal (+1500).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-98500.00")
            # They DIVERGE, and the ledger (extra paydown) owes LESS.
            assert ledger != resolver
            assert ledger < resolver
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_arm_rate_step_matches_a_hand_computed_post_step_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """An ARM rate step: the ledger balance equals a HAND-COMPUTED literal.

        The parallel run agrees on-schedule and diverges off, but BOTH the walk
        and the resolver read the same ``rate_period_engine``, so a shared
        rate-step bug would move the two producers together and the parallel run
        alone would NOT catch it (review M4(b) / M7).  This case closes that gap
        with a hand-computed post-step ledger balance -- the one producer a
        shared-kernel bug cannot hide from -- which is exactly why the fixture
        does NOT read the balance back from either producer.

        A rate step to 12% effective 2026-03-01 governs P2 (due 2026-03-01, the
        step's own day) while P1 (due 2026-02-01) keeps the 6% origination
        rate.  P2 and not a later installment since plan step
        recurrence:R16-c-2: every contractual installment is charged now, so a
        payment skipping March would clear March's interest too (the fixture
        is restated so nothing is left unpaid, ruling R-R103).  Each payment
        is a $1,000 SHORT payment, so the ledger's REAL principal (cash - interest) is hand-computable without the
        schedule (the same partition ``test_arm_rate_step_changes_interest``
        pins at the unit level):
          P1 (6%):  interest = round(100000 * 0.06 / 12) = 500.00;
                    principal = 1000 - 500 = 500.00; balance 99,500.00.
          P2 (12%): interest = round( 99500 * 0.12 / 12) = 995.00;
                    principal = 1000 - 995 =   5.00; balance 99,495.00.
        So the ledger balance is 99,495.00 and the genesis linked total is
        opening (-250000) + true-up (+150000) + P1 principal (+500) + P2
        principal (+5) = -99,495.00.  The reader reads that same ledger, so it
        agrees to the penny; the resolver books the (recast, far larger)
        SCHEDULED principal at each rate, so it shows a LOWER balance -- the
        short-payment divergence, now proven to survive an ARM step.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _add_rate_change(loan, date(2026, 3, 1), Decimal("0.12000"))
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            _settle(seed_user, loan, seed_periods[_P2], amount=Decimal("1000.00"))
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            reader = _posted_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The hand-computed post-step balance -- the shared-kernel teeth.
            assert ledger == Decimal("99495.00")
            assert reader == Decimal("99495.00")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99495.00")
            # The resolver books scheduled principal at each rate; short payments
            # mean it owes LESS -- divergence survives the rate step.
            assert resolver < ledger
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_pre_anchor_payment_is_correctly_summed_under_genesis(
        self, app, db, seed_user, seed_periods,
    ):
        """A pre-anchor payment is SPLIT and summed, not excluded -- genesis fix.

        Genesis retires the Step-4 pre-anchor exclusion (the read-switch boundary
        the prior draft carved out): the walk splits EVERY payment from
        origination, and the opening + true-up corrections absorb the pre-anchor
        principal, so the from-origination sum-of-postings reproduces the resolver
        with NO boundary rule.  With the trueup at 2026-02-15, P1 (due 2026-02-01)
        is pre-trueup: it splits on the $250,000 origination balance (interest
        round(250000 * 0.005) = 1250.00, principal 1000 - 1250 = -250.00, so the
        walk carries 250250 to the trueup).  The opening books -250000 and the
        true-up 250250 - 100000 = +150250; with P1's cash (+1000) and correction
        loan leg (-1250) the loan-linked net is -100000.00 -- exactly -(the
        resolver's 100000 anchor, which subsumes P1) -- so the pre-anchor payment
        is summed correctly, never the old double-count.
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
            assert loan_correction_entries(db.session, xfer.id, loan.id) != []

            # Post the opening + true-up (the read-switch corrections): the
            # ONE sync since plan step ``balance:X-bi-6-3`` (the anchor-only
            # door went with the split's row key, ruling R-BAL102; the split
            # it reconciles beside is already at target, so nothing moves).
            loan_posting_service.sync_loan_postings(loan.id, scenario_id)
            db.session.commit()

            # -(linked net) reproduces the resolver's anchor, which subsumes P1.
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert resolver == _ANCHOR_BALANCE  # 100000.00
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
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
        ``account_posting_total(A) == settled_transfer_effect(A) +
        settled_transaction_effect(A)``.  For this loan after one $1,000 payment
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

            linked = posting_service.account_posting_total(loan.id, scenario_id)
            cash_effect = posting_service.settled_transfer_effect(
                loan.id, scenario_id,
            )
            txn_effect = posting_service.posted_purchase_effect(
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
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)


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
        loan is paid off.  FOUR per-loan ledgers are minted (interest, escrow,
        refund, opening-equity) and the whole sweep ties (this is the only fixture
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
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("0.00")
            # Per-loan net = non-principal (1000) + opening-equity legs (+1000,
            # the negatives of opening -250000 + true-up +249000).
            assert _per_loan_correction_net(
                loan.id, scenario_id,
            ) == Decimal("2000.00")
            # The loan is paid off: the ledger balance is zero.
            assert _ledger_balance(loan.id, scenario_id) == Decimal("0.00")
            # Four per-loan ledger accounts: interest, escrow, refund, opening-equity.
            assert (
                db.session.query(LedgerAccount)
                .filter_by(loan_account_id=loan.id)
                .count()
            ) == 4
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)


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
            assert posting_service.account_posting_total(
                loan.id, baseline.id,
            ) == Decimal("-99500.00")
            assert posting_service.account_posting_total(
                loan.id, whatif.id,
            ) == Decimal("-99500.00")
            _assert_loan_reconciles(loan, baseline.id, _AS_OF)
            _assert_loan_reconciles(loan, whatif.id, _AS_OF)

    def test_two_owners_reconcile_independently(
        self, app, db, seed_user, seed_second_user, seed_periods,
        seed_second_periods,
    ):
        """Two owners each settle a loan payment; neither sees the other's.

        Owner 1 settles a $1,000 payment on their loan (real principal 500); owner
        2 settles a $2,000 payment on theirs (real principal 1,500).  Each loan
        reconciles in its owner's own baseline scenario, every correction's
        journal entry carries its own owner's ``user_id``, and a ``Posting``
        carries no ``user_id`` of its own -- so ownership is reachable only through
        the entry and cannot cross-contaminate.
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
            assert posting_service.account_posting_total(
                loan1.id, scenario1,
            ) == Decimal("-99500.00")
            assert posting_service.account_posting_total(
                loan2.id, scenario2,
            ) == Decimal("-98500.00")

            # Ownership is normalized onto the journal entry, not the posting.
            assert not hasattr(Posting, "user_id")
            owner1_id = seed_user["user"].id
            owner2_id = seed_second_user["user"].id
            loan_payment_source = ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT,
            )
            corrections = (
                db.session.query(JournalEntry)
                .filter_by(source_kind_id=loan_payment_source)
                .all()
            )
            assert corrections  # both owners posted at least one
            for entry in corrections:
                assert entry.user_id in (owner1_id, owner2_id)
                for posting in entry.postings:
                    assert posting.ledger_account.user_id == entry.user_id

            _assert_loan_reconciles(loan1, scenario1, _AS_OF)
            _assert_loan_reconciles(loan2, scenario2, _AS_OF)


# ---------------------------------------------------------------------------
# 5. Backfill == go-forward (plan 8.8)
# ---------------------------------------------------------------------------


class TestBackfillEqualsGoForward:
    """The backfill reproduces the go-forward ledger AND makes the reader authoritative.

    Two faces of the C7 backfill guarantee (plan 8.8 / Section 4 commit 7): a
    ledger rebuilt by the backfill reconciles identically to the go-forward one
    (leg for leg), and the dated posting window -- ``None`` while no opening is
    posted -- reads back == the resolver to the penny once the backfill posts it.
    The second is the plan's "the oracle detects the unposted-opening gap before
    and zero mismatches after," proven at a point in time AND at every period
    boundary, so the historical-data path is pinned directly, not only by
    transitivity through Sections 5 and 7.
    """

    def test_backfilled_ledger_reconciles_identically(
        self, app, db, seed_user, seed_periods,
    ):
        """Clearing then backfilling a payment's correction restores the same ledger.

        Settling posts the payment corrections AND the opening / true-up
        go-forward; the sweep ties.  Clearing every loan posting (the anchor
        corrections first, then the migration's own payment teardown) reproduces
        the pre-wiring historical state (settled, no postings), and the historical
        backfill (``backfill_all_loan_postings``, reusing the identical go-forward
        sync) re-posts them all.  The linked and per-loan nets return to their
        exact go-forward values and the whole sweep reconciles again -- backfill
        == go-forward, leg for leg.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            _settle(seed_user, loan, seed_periods[_P2], amount=Decimal("1000.00"))
            db.session.commit()

            forward_linked = _independent_loan_linked_net(loan.id, scenario_id)
            forward_per_loan = _per_loan_correction_net(loan.id, scenario_id)
            # Opening (-250000) + true-up (+150000) + principal (500 + 502.50).
            assert forward_linked == Decimal("-98997.50")
            # Interest (500 + 497.50) + opening-equity legs (+100000).
            assert forward_per_loan == Decimal("100997.50")
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

            # Reproduce the pre-wiring state (no loan postings), then backfill.
            _clear_all_loan_postings()
            assert _per_loan_correction_net(loan.id, scenario_id) == Decimal("0")
            loan_posting_service.backfill_all_loan_postings()
            db.session.commit()

            # The backfilled nets equal the go-forward nets, and the sweep ties.
            assert _independent_loan_linked_net(
                loan.id, scenario_id,
            ) == forward_linked
            assert _per_loan_correction_net(
                loan.id, scenario_id,
            ) == forward_per_loan
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_reader_reads_none_before_backfill_then_matches_resolver_after(
        self, app, db, seed_user, seed_periods,
    ):
        """The reader shows the unposted-opening gap before backfill, == resolver after.

        The read switch (plan Section 8) turns every displayed loan balance onto the
        genesis reader, so the C7 backfill is what makes that reader authoritative on
        HISTORICAL data -- a loan / payment settled before the go-forward wiring
        shipped, carrying no postings.  This is the plan's "gap before, zero
        mismatches after," pinned on the exact producers the switch flips onto:

        * BEFORE the backfill (openings cleared) the reader returns ``None`` --
          needs-setup, the unposted-opening GAP -- in BOTH forms of the window,
          while the resolver, which never reads the ledger, still reports the true
          balance unchanged.  Any surface reading the postings HERE would wrongly
          show this loan needs-setup.
        * AFTER the backfill both read back == the resolver to the penny.

        On-schedule (cash == scheduled P&I, no escrow), so the ledger's real
        principal equals the resolver's scheduled principal and the two agree
        exactly.  The resolver is invariant across the clear / backfill (the
        teardowns remove only postings, not the anchors / transactions it replays),
        so it is the stable independent reference both phases pin to.  Non-vacuity:
        the after-balance is the $100,000 anchor less the real principal (scheduled
        P&I - 500.00 interest), not the untouched anchor, and the map steps strictly
        down as the payment lands.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment
            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            # The resolver never reads the ledger, so its balance is invariant across
            # the clear / backfill -- the stable reference for both phases.  The
            # go-forward reader already matches it (the C6 gate); the contrast is the
            # point: authoritative now, None in a moment, == again after.
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert _posted_balance(loan.id, scenario_id) == resolver

            # Reproduce the pre-wiring historical state (no loan postings).
            _clear_all_loan_postings()

            # BEFORE: the unposted-opening gap.  No OPENING leg -> the reader cannot
            # produce a balance (both producers return None), yet the resolver -- which
            # never read the ledger -- is unchanged, so the gap is purely the ledger's.
            assert posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            ) is None
            assert posted_loan_balance_map(
                loan.id, scenario_id, seed_periods,
            ) is None
            assert _resolver_balance(loan.id, scenario_id, _AS_OF) == resolver

            loan_posting_service.backfill_all_loan_postings()
            db.session.commit()

            # AFTER: zero mismatch.  The scalar (C8 producer) and every period of the
            # map (C9 producer) read back == the resolver to the penny.
            reader = _posted_balance(loan.id, scenario_id)
            assert reader == resolver, f"reader {reader} != resolver {resolver}"
            balance_map = _posted_period_map(loan.id, scenario_id, seed_periods)
            for period in seed_periods:
                assert balance_map[period.id] == _resolver_balance(
                    loan.id, scenario_id, period.start_date,
                ), (
                    f"period {derived_span(period).period_index}: "
                    f"map {balance_map[period.id]} "
                    f"!= resolver at {period.start_date}"
                )
            # Non-vacuity: the anchor less the real principal (not the untouched
            # anchor), and the map steps strictly down as the payment lands.
            assert reader == _ANCHOR_BALANCE - (scheduled_pi - Decimal("500.00"))
            assert balance_map[seed_periods[0].id] == _ANCHOR_BALANCE
            assert (
                balance_map[seed_periods[_P1].id]
                < balance_map[seed_periods[0].id]
            )
