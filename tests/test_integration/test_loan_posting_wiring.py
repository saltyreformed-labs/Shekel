"""Build-Order Step 4 wiring: loan-payment splits post at the transfer chokepoints.

Commit 4 built the loan-payment split-posting service (pure, unwired); Commit 5
WIRES it into every chokepoint that changes a loan's confirmed payments -- the
transfer settle / revert / edit / delete / restore paths
(:mod:`app.services.transfer_service`), the balance true-up
(:func:`app.services.loan_anchor_service.apply_loan_anchor_true_up`), the ARM rate
change and origination-rate / params edit routes, and loan-params creation (the
N1 back-post).  These integration tests drive each chokepoint through its REAL
entry point (the service call or the HTTP route), with NO manual loan sync
call anywhere, and assert the ledger ends in the right state.

The load-bearing invariant (plan Section 5 / 8.5): a Step-4 correction links
NO row -- it is a date-keyed correction since plan step ``balance:X-bi-6-3``
(ruling **R-BAL102**), and carried a NULL ``transfer_id`` before that -- so
the cash reader (movement-keyed, source-filtered) never sees it -- proven
end-to-end by asserting a revert / delete of a corrected payment posts the
FULL cash reversal (Checking returns to exactly 0), not a reversal short by
the correction.

Loans use a $100,000 anchor at 6%, so the first month accrues exactly $500.00
(``100000 * 0.06 / 12``); every asserted split is hand-computed in the docstring.
"today" is frozen to 2026-05-15 (inside the seed-period range, after every
payment period used) so the wiring's ``date.today()`` as-of is deterministic and
every settled payment is historical.  All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.models.scenario import Scenario
from app.services import (
    loan_anchor_service,
    posting_service,
    transfer_service,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from tests._test_helpers import (
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_transfer,
    find_loan_ledger_account,
    freeze_today,
    ledger_net,
    loan_correction_entries,
    SPLIT_LOAN,
    transfer_side_record,
)

# The shared synthetic split-loan fixture ($250,000 @ 6%, trued up to $100,000 --
# distinct so a correct interest figure proves the walk's anchor reset); see
# SPLIT_LOAN in tests/_test_helpers.py.  _P1/_P2/_P3 are the seed_periods indices
# (payment_day=1) due 02-01 / 03-01 / 04-01, in distinct months after the anchor.
(_ORIGINATION_PRINCIPAL, _ORIGINATION_DATE, _RATE, _ANCHOR_BALANCE,
 _ANCHOR_DATE, _P1, _P2, _P3) = SPLIT_LOAN


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Freeze today to 2026-05-15 so the wiring's date.today() as-of is fixed.

    Inside the seed-period range (period 9 is 2026-05-08..2026-05-21) and after
    every payment period used (P1/P2/P3 in Jan-Mar), so each settled payment is
    historical (eligible) regardless of the wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 5, 15))


def _make_loan(seed_user, *, anchor_balance=_ANCHOR_BALANCE, rate=_RATE):
    """Create a resolvable amortizing loan with the suite's controlled anchor."""
    return create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=_ANCHOR_DATE, rate=rate,
        origination_date=_ORIGINATION_DATE,
    )


def _settle(seed_user, loan, period, amount=Decimal("1000.00"), scenario=None):
    """Settle a Checking -> loan payment transfer through the service."""
    return create_settled_transfer(
        seed_user, _db.session, seed_user["account"], loan, period,
        amount=amount, scenario=scenario,
    )


def _interest_ledger(loan):
    """Return the loan's per-loan interest ledger account (must exist)."""
    ledger = find_loan_ledger_account(
        _db.session, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
    )
    assert ledger is not None, "interest ledger not minted by the wiring"
    return ledger


class TestSettleWiringAutoPosts:
    """Settling a loan payment auto-posts its split correction (no manual sync)."""

    def test_settle_auto_posts_split_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """A single $1,000 settle posts Loan -500 / Interest +500 with no sync call.

        Arithmetic: interest round(100000 * 0.005) = 500.00; principal 500.00.
        The wiring fires inside update_transfer's settle path, so right after
        create_settled_transfer the payment correction (+500 principal) AND the
        opening (-250000) + true-up (+150000) are posted: the loan-linked ledger
        nets -99500.00 == -(current balance 99500), the interest ledger holds
        +500, and Checking moved only by the Step-2 cash on top of its
        $1000.00 Step-5 opening: 1000 - 1000 = 0.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            assert len(
                loan_correction_entries(db.session, xfer.id, loan.id),
            ) == 1
            # Genesis: opening (-250000) + true-up (+150000) + principal (+500).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")
            assert ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            ) == Decimal("500.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("0.00")

    def test_settle_with_actual_amount_splits_from_actual_cash(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling with an actual over the estimate splits from the ACTUAL cash.

        A single payoff-overpayment isolates the correction's cash-reading (one
        settle, so no later re-sync can self-heal a wrong read).  On a $300
        anchor, a payment settled with a $100 estimate but a $1,000 ACTUAL in ONE
        update_transfer call pays the loan off -- interest round(300*0.005)=1.50,
        principal capped at 300.00 -- and routes the 1000 - 1.50 - 300 = 698.50
        remainder to the per-loan Refund ledger.  That Refund leg is
        cash-DEPENDENT: a split that read the $100 estimate would under-cover
        (principal 98.50, no cap, NO refund).  Its presence therefore pins both
        that the split reads effective (actual) cash AND that the wiring runs the
        sync AFTER the actual is applied in update_transfer (the actual-after-
        status ordering).  With the opening (-250000) and true-up (+249700 =
        250000 - 300) also posted, the loan-linked ledger nets 0.00 -- the loan is
        paid off (its 300.00 principal exactly retires the 300 balance).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("100.00"),
                settled_amount=Decimal("1000.00"),
            )
            db.session.commit()

            refund_ledger = find_loan_ledger_account(
                db.session, loan.id, LedgerAccountKindEnum.LOAN_REFUND,
            )
            assert refund_ledger is not None, (
                "no Refund leg -- the split did not read the overpaying actual"
            )
            assert ledger_net(
                db.session, refund_ledger.id, scenario_id,
            ) == Decimal("698.50")
            # Paid off: opening (-250000) + true-up (+249700) + principal (+300).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("0.00")


class TestRevertAndDeletePostFullCashReversal:
    """The CRITICAL 8.5 regression: a correction never shortens the cash reversal."""

    def test_revert_posts_full_cash_reversal_and_reverses_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """Reverting a corrected payment returns Checking to exactly 0.

        The correction (Loan -500 / Interest +500) carries a NULL transfer_id,
        so the Step-2 cash reader sees only the +1,000 cash on the loan ledger
        and posts the FULL -1,000 reversal: Checking returns to its $1000.00
        opening (not 1500).  The
        loan sync reverses the now-projected payment's correction; the opening
        (-250000) + true-up (+150000) remain (the revert does not touch them), so
        the loan-linked ledger returns to the trued-up baseline -100000.00 ==
        -(verified balance 100000).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            # Opening (-250000) + true-up (+150000) + principal (+500).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            # Full cash reversal: Checking back to exactly its 1000 opening.
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            # Payment correction + cash reversed; opening + true-up remain, so the
            # loan returns to the trued-up baseline -(verified 100000).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            ) == Decimal("0.00")

    def test_hard_delete_strands_nothing_and_full_cash_reversal(
        self, app, db, seed_user, seed_periods,
    ):
        """Hard-deleting a corrected payment strands nothing; the loan resets to baseline.

        The teardown door posts the FULL cash reversal before the rows go (the
        movement's link is what the CASCADE severs), and the loan's re-sync
        AFTER the delete reverses the payment's split at its own key -- the
        split links no row (ruling **R-BAL102**), so nothing about it needed
        the shadow to still exist.  Nothing stranded: Checking returns to its
        $1000.00 opening, the interest ledger nets to zero, and the loan-linked
        ledger returns to the trued-up baseline -100000.00 (opening + true-up
        remain -- the delete touches only the payment correction).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            # Opening + true-up remain; only the payment correction is reversed.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("0.00")

    def test_soft_delete_reverses_correction_and_full_cash(
        self, app, db, seed_user, seed_periods,
    ):
        """Soft-deleting a corrected payment reverses the correction and full cash."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            # Payment correction reversed; opening + true-up remain, so the loan
            # returns to the trued-up baseline -(verified 100000).
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")

    def test_delete_resplits_downstream_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Deleting an earlier payment re-splits the later one (running-balance coupling).

        P1 (interest 500.00) and P2 (interest round(99500*0.005)=497.50) settle,
        so the interest ledger holds 997.50.  Deleting P1 leaves its 02-01
        installment unpaid, and every contractual installment is charged since
        plan step recurrence:R16-c-2 (rulings R-R72 part (1) and R-R100), so P2
        clears February's arrears before its own month: interest
        round(100000*0.005) = 500.00 for February plus 500.00 for March, both
        on the anchor's $100,000 no payment has reduced -- 1,000.00, all of
        P2's cash.  The ledger holds 1,000.00 after: P1's reversed, P2's
        re-split UP from 497.50.  Until R16-c-2 a month no payment occupied
        was never charged, so P2 re-split to 500.00; the developer approved
        the moved figure (rule 5).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            x1 = _settle(seed_user, loan, seed_periods[_P1])
            _settle(seed_user, loan, seed_periods[_P2])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("997.50")

            transfer_service.delete_transfer(
                x1.id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("1000.00")


class TestRestoreWiring:
    """Restoring a soft-deleted loan payment that was Paid brings back a plan."""

    def test_restore_reposts_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """Settle (-99500), soft-delete (baseline -100000), restore (-100000: a plan).

        The loan-linked ledger carries the opening (-250000) + true-up (+150000)
        throughout; the payment correction (+500 principal) is reversed on
        delete, and the restore brings the payment back as a PLAN, so the
        linked total moves -99500 -> -100000 -> -100000 (the -100000 baseline
        is the trued-up balance with no payment).

        It re-posted the correction (-99500 after the restore) until plan step
        ``balance:X-bi-6-4d-2``.  That step's delete takes both payment
        records off (ruling **credit_card:R-CC75**), so nothing was left to
        re-post, and as first built the restore brought back a Paid payment
        holding nothing: a ``$0.00`` close, which the loan walk read as a
        missed installment, its ``$500.00`` interest charged (-100500,
        measured).  The developer ruled the delete sets the payment back to
        Projected (ruling **R-BAL246**, "Back as a plan, now", 2026-10-09:
        "the loan reads $100,000.00 owed and no missed installment is
        charged"), so the restore returns a planned payment and the figure
        stays at the no-payment baseline.  The name is kept as history.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99500.00")

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")

            transfer_service.restore_transfer(xfer.id, seed_user["user"].id)
            db.session.commit()
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert xfer.is_deleted is False
            for account_id in (xfer.from_account_id, xfer.to_account_id):
                assert transfer_side_record(
                    _db.session, xfer.id, account_id,
                ) is None, "a restored plan holds no payment record"
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")


class TestTrueUpWiring:
    """A balance true-up re-bases every scenario's split; Checking is never touched."""

    def test_trueup_resplits_later_keeps_earlier_checking_untouched(
        self, app, db, seed_user, seed_periods,
    ):
        """A new trueup re-splits later payments, keeps earlier, never moves Checking.

        P1 (due 02-01) and P2 (due 03-01) settle against 100000 @ 01-10
        (interest 500.00 + 497.50 = 997.50).  A user-trueup of 90000 @ 02-15 falls
        BETWEEN them: genesis keeps P1's 500.00 (it is due before the new trueup)
        and re-splits P2 from 90000 to round(90000 * 0.005) = 450.00, so the
        interest ledger holds 500 + 450 = 950.00 (Step 4 reversed P1 and left only
        450.00).  Checking is unchanged from its -1000.00 (the $1000.00
        opening minus the two settles) -- the true-up sync touches only the
        loan's own ledgers.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            _settle(seed_user, loan, seed_periods[_P2])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("997.50")
            checking_before = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            assert checking_before == Decimal("-1000.00")

            outcome = loan_anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("90000.00"),
                anchor_date=date(2026, 2, 15),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            # P1 (before 02-15) keeps 500.00; P2 (after) re-splits to 450.00.
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("950.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == checking_before

    def test_trueup_syncs_every_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """One true-up re-splits the payments in every scenario the loan has.

        A P1 payment settles in the baseline AND a second (what-if) scenario,
        each accruing interest 500.00 against 100000 @ 01-10.  A user-trueup of
        90000 @ 01-15 (after the 01-10 anchor, before P1's 02-01 due date)
        re-bases P1 in BOTH scenarios to round(90000 * 0.005) = 450.00 -- proving
        the sync loops every scenario, since the anchor is per-account.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if",
                is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], scenario=baseline)
            _settle(seed_user, loan, seed_periods[_P1], scenario=whatif)
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, baseline.id,
            ) == Decimal("500.00")
            assert ledger_net(
                db.session, interest_ledger_id, whatif.id,
            ) == Decimal("500.00")

            outcome = loan_anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("90000.00"),
                anchor_date=date(2026, 1, 15),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            # BOTH scenarios re-split to 450.00.
            assert ledger_net(
                db.session, interest_ledger_id, baseline.id,
            ) == Decimal("450.00")
            assert ledger_net(
                db.session, interest_ledger_id, whatif.id,
            ) == Decimal("450.00")

    def test_trueup_resubmit_is_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """A re-submit of the governing (date, balance) is an idempotent no-op.

        The second identical submit matches the governing ``user_trueup``, so
        the door writes nothing, rolls back and returns UNCHANGED, leaving the
        first commit's correction intact (interest still 450.00, not doubled or
        lost).  **It tripped a same-day unique index until plan step X-f1c4b**
        (ruling R-EQ); what this grades is the LEDGER outcome, which is the same
        either way and is why the test survived the mechanism change.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            first = loan_anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("90000.00"),
                anchor_date=date(2026, 1, 15),
            )
            assert first is AnchorTrueUpOutcome.COMMITTED
            interest_after_first = ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            )
            assert interest_after_first == Decimal("450.00")

            second = loan_anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("90000.00"),
                anchor_date=date(2026, 1, 15),
            )
            assert second is AnchorTrueUpOutcome.UNCHANGED
            assert ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            ) == Decimal("450.00")


class TestNonLoanTransferIgnored:
    """A non-loan transfer settle posts no loan-payment correction (wiring no-op)."""

    def test_savings_transfer_posts_no_loan_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling a Checking -> Savings transfer creates no loan_payment entry.

        classify_account(Savings) is not AMORTIZING, so the loan wiring
        short-circuits: no loan-payment-sourced journal entry is booked under
        the transfer's to-side.

        **Read at the savings side's key and over the owner's entries since
        plan step ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 1):
        it named the side by the income shadow, which no longer exists, and
        counted the entries linking that shadow's row id, which no split has
        carried since ruling **R-BAL102**.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Wiring Savings",
            )
            db.session.commit()
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_P1], amount=Decimal("250.00"),
            )
            db.session.commit()

            assert loan_correction_entries(
                db.session, xfer.id, savings.id,
            ) == []
            # And no loan_payment entry exists for this owner at all.
            loan_payment_source = ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT,
            )
            count = (
                db.session.query(JournalEntry)
                .filter_by(
                    user_id=seed_user["user"].id,
                    source_kind_id=loan_payment_source,
                )
                .count()
            )
            assert count == 0


class TestRouteChokepointWiring:
    """The route chokepoints (rate change, params create) fire the re-sync."""

    def test_rate_change_route_resplits_interest(
        self, app, db, seed_user, seed_periods, auth_client,
    ):
        """POSTing an ARM rate change re-splits a later payment's interest.

        P1 (due 02-01, 6% origination rate) and P2 (due 03-01) settle:
        interest 500.00 + round(99500*0.005)=497.50 = 997.50.  A rate change to
        12% effective 2026-03-01 governs P2's installment: interest
        round(99500 * 0.12 / 12) = 995.00.  The route wiring re-syncs, so the
        interest ledger holds 500.00 + 995.00 = 1495.00 after the POST.  P2
        and not a later installment since plan step recurrence:R16-c-2: every
        contractual installment is charged now, so a payment skipping March
        would clear March's interest too (the fixture is restated so nothing
        is left unpaid, ruling R-R103).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            # Mark ARM so the rate-change surface applies (mirrors the loan
            # route suite); the split itself is rate-feed driven regardless.
            loan_params = loan.loan_params
            loan_params.is_arm = True
            _settle(seed_user, loan, seed_periods[_P1])
            _settle(seed_user, loan, seed_periods[_P2])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("997.50")

            resp = auth_client.post(
                f"/accounts/{loan.id}/loan/rate",
                data={"effective_date": "2026-03-01", "interest_rate": "12.000"},
            )
            assert resp.status_code == 200

            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("1495.00")

    def test_params_update_origination_rate_resplits_interest(
        self, app, db, seed_user, seed_periods, auth_client,
    ):
        """POSTing a new origination rate re-splits every confirmed payment's interest.

        P1 settles at the 6% origination rate: interest round(100000*0.06/12) =
        500.00.  POSTing /loan/params with interest_rate 8.000 upserts the
        origination RateHistory row to 8% (payment_day unchanged), and
        update_params re-syncs UNCONDITIONALLY, so P1's interest re-splits to
        round(100000 * 0.08 / 12) = 666.67.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("500.00")

            resp = auth_client.post(
                f"/accounts/{loan.id}/loan/params",
                data={"interest_rate": "8.000", "payment_day": "1"},
            )
            assert resp.status_code == 302

            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("666.67")

    def test_params_payment_day_change_resplits_across_trueup(
        self, app, db, seed_user, seed_periods, auth_client,
    ):
        """Changing payment_day alone re-splits -- it moves the anchor boundary.

        Pins the reason update_params syncs UNCONDITIONALLY, not only on a rate
        change.  The trueup anchor is 2026-01-25.  P1 (period start 2026-01-16) has
        monthly due date 2026-02-01 at payment_day=1 -- AFTER the trueup, so it
        splits on the 100000 trueup (interest round(100000 * 0.005) = 500.00).
        POSTing /loan/params with payment_day=20 (rate unchanged) moves P1's due
        date to 2026-01-20 (the first day-20 on or after 01-16) -- now BEFORE the
        2026-01-25 trueup, so genesis re-splits it on the $250,000 origination
        balance: interest round(250000 * 0.005) = 1250.00 (Step 4 reversed it to
        zero; genesis carries the split across the boundary instead).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_ORIGINATION_PRINCIPAL,
                anchor_balance=_ANCHOR_BALANCE, anchor_date=date(2026, 1, 25),
                # P1 clears ONE month at either due day: at the 20th its 01-20
                # installment is the loan's first; at the 1st the 01-01
                # installment falls before the 01-25 true-up, which clears it
                # (ruling R-R72 part (2)), and P1 (02-01) faces February's
                # alone.  Every contractual installment from origination is
                # charged since plan step recurrence:R16-c-2 (ruling R-R101),
                # so at the 20th SPLIT_LOAN's 2025-01-01 would have P1 clear a
                # year of unpaid months.
                rate=_RATE, origination_date=date(2025, 12, 1),
            )
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            interest_ledger_id = _interest_ledger(loan).id
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("500.00")

            resp = auth_client.post(
                f"/accounts/{loan.id}/loan/params",
                data={"interest_rate": "6.000", "payment_day": "20"},
            )
            assert resp.status_code == 302

            # P1 crosses to pre-trueup -> re-split on origination, not reversed.
            assert ledger_net(
                db.session, interest_ledger_id, scenario_id,
            ) == Decimal("1250.00")

    def test_params_created_after_settle_backposts(
        self, app, db, seed_user, seed_periods, auth_client,
    ):
        """A payment settled before loan setup gets its correction on setup (N1).

        A bare amortizing-type account carries no LoanParams, so a payment
        settled into it posts NO correction (the settle-time sync short-circuits
        -- not resolvable).  POSTing /loan/setup configures the loan (origination
        anchor 100000 @ 2026-01-01) and the create_params wiring back-posts: the
        P1 correction now holds interest round(100000 * 0.005) = 500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_account_of_type(
                seed_user, db.session, "Auto Loan", "Unconfigured Loan",
            )
            db.session.commit()
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            # Not resolvable yet -> no correction at settle.
            assert loan_correction_entries(db.session, xfer.id, loan.id) == []

            resp = auth_client.post(
                f"/accounts/{loan.id}/loan/setup",
                data={
                    "original_principal": "100000.00",
                    # Stated for the origination day: the setup door then
                    # records no assertion (plan step R20), and the loan's
                    # only anchor stays the synthesized origination the
                    # back-posted split is measured against.
                    "anchor_balance": "100000.00",
                    "anchor_date": "2026-01-01",
                    "interest_rate": "6.000",
                    # <= the Auto Loan type's 120-month cap; the split's interest
                    # is balance*rate/12, independent of the term.
                    "term_months": "60",
                    "origination_date": "2026-01-01",
                    "payment_day": "1",
                },
            )
            assert resp.status_code == 302

            # Back-posted: the correction now exists with interest 500.00.
            entries = loan_correction_entries(db.session, xfer.id, loan.id)
            assert len(entries) == 1
            assert ledger_net(
                db.session, _interest_ledger(loan).id, scenario_id,
            ) == Decimal("500.00")
