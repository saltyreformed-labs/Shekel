"""The opening and true-up corrections drive the linked ledger to -(owed), re-file when the
calendar grows a period around them, and the all-scenarios sync posts a payment-less loan's
opening.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.services import loan_ledger, loan_posting_service, pay_period_admin
from tests._ledger_parity import assert_ledger_parity
from tests._ledger_reads import account_net
from tests._test_helpers import create_loan_account, last_covered_day

from ._helpers import (
    _ORIGINATION_PRINCIPAL,
    _ORIGINATION_DATE,
    _RATE,
    _P1,
    _P2,
    _P3,
    _ORIGINATION_BEFORE_P1,
    _make_loan,
    _settle_payment,
    _find_loan_ledger,
    _equity_net,
    _anchor_net,
    _anchor_lines,
    _anchor_correction_entries,
    _anchor_net_in_period,
)


class TestSyncLoanAnchorCorrections:
    """The opening + true-up corrections drive the linked ledger to -(owed)."""

    def test_opening_posts_original_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An origination-only loan posts the opening: linked -P0, equity +P0.

        A loan with only its origination anchor ($250,000) and no payments posts
        ONE opening entry: owed_before is 0 at origination, so the loan-linked
        ledger gets 0 - 250000 = -250000.00 and the per-loan opening-equity ledger
        its negative, +250000.00.  The linked net is then -250000, so the reader
        (which negates it) reports the full $250,000 owed.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _equity_net(loan, scenario_id) == Decimal("250000.00")
            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) == 1
            assert _anchor_lines(
                loan, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_and_trueup_drive_linked_to_negative_verified(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + true-up land the linked net on -(verified balance).

        Origination $250,000 then a trueup to $100,000 (no payments):
          opening (owed_before 0): linked 0 - 250000 = -250000.00.
          trueup (owed_before 250000, the origination balance carried to the
            trueup date): linked 250000 - 100000 = +150000.00.
        Linked net -250000 + 150000 = -100000.00 -- exactly -(the $100,000 the
        user verified), the opening + trueup reproducing the resolver's anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # origination 250000, trueup 100000
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _equity_net(loan, scenario_id) == Decimal("100000.00")

            opening = _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )
            trueup = _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )
            assert len(opening) == 1 and len(trueup) == 1
            assert _anchor_net(
                loan, scenario_id, PostingSourceEnum.LOAN_OPENING,
            ) == Decimal("-250000.00")
            assert _anchor_net(
                loan, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == Decimal("150000.00")

    def test_anchor_corrections_are_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """A second sync at the same state writes no new anchor entry."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            before = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) + len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            assert before == 2

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            after = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) + len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            assert after == 2

    def test_trueup_matching_the_walk_books_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A trueup equal to the walked balance posts no true-up entry.

        Origination $250,000 and a trueup ALSO of $250,000 (no payments): at the
        trueup date owed_before is 250000, so the correction 250000 - 250000 = 0
        books nothing.  Only the opening is posted; the linked net is -250000.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("250000.00"))
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _anchor_lines(
                loan, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_payments_trueup_net_to_the_hand_worked_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + payment corrections + true-up net to -(the hand-worked balance).

        The genesis payoff: three post-trueup $1,000 payments split to principal
        500.00 + 502.50 + 505.01 = 1507.51 (the running-balance walk from the
        $100,000 trueup), so the loan owes 100000 - 1507.51 = 98492.49.  With
        the opening (-250000) and true-up (+150000) added, the loan-linked net is
        (1507.51 payment nets) - 100000 = -98492.49, proving the
        from-origination sum-of-postings lands on the trued-up balance.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-98492.49")

    def test_trueup_self_heals_when_a_pre_trueup_payment_is_added(
        self, app, db, seed_user, seed_periods,
    ):
        """Settling a pre-trueup payment re-bases the true-up in the SAME reconcile.

        The true-up correction is owed_before - verified, and owed_before moves
        when a pre-trueup payment changes -- so the reconcile must self-heal
        (reconcile-to-target), not leave a stale snapshot.  Origination $250,000,
        trueup $100,000 @ 2026-02-15: with no payments the opening (-250000) and
        true-up (250000 - 100000 = +150000) net the linked ledger to -100000.00,
        and the per-loan opening-equity ledger holds their negatives, +100000.00.
        Settling P1 (due 02-01, PRE-trueup, cash 1000) splits it on the origination
        balance (interest round(250000 * 0.005) = 1250, principal -250) and grows
        owed_before to 250250 -- and because the settle fires the UNIFIED sync
        (:func:`sync_loan_postings`), the true-up re-bases to 250250 - 100000 =
        +150250 (a +250 linked delta) in the SAME transaction, so the linked ledger
        STAYS -100000.00 == -(verified $100,000), the pre-trueup payment fully
        absorbed; the equity ledger re-bases to 250000 - 150250 = +99750.00.  No
        stale window is ever observable through the go-forward path.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 2, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            # The opening + true-up (and their per-loan equity ledger) are posted
            # lazily on this first sync.
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            # Opening (+250000) + true-up (-150000) on the equity ledger.
            assert _equity_net(loan, scenario_id) == Decimal("100000.00")

            # Settle a pre-trueup payment; the unified wiring re-bases the true-up
            # in the same transaction (no manual anchor re-sync needed).
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            # Self-healed automatically: linked still -(verified 100000); the
            # equity ledger re-based to opening (+250000) + true-up (-150250).
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _equity_net(loan, scenario_id) == Decimal("99750.00")

            # A further unified sync is a no-op: no new true-up entry, balance holds.
            trueups_after_heal = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )) == trueups_after_heal
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert_ledger_parity(db.session, scenario_id)

    def test_payment_due_on_anchor_date_is_subsumed_by_the_reset(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due EXACTLY on the anchor date is walked before the reset, then subsumed.

        The strict ``anchor_date < monthly_due_date`` boundary means a payment due
        ON an anchor's date is PRE-anchor (subsumed).  The walk realises this by
        sorting a payment (tag 0) before an anchor (tag 1) on an equal date, so P1
        (period 1, due 2026-02-01 at payment_day=1) is split on the $250,000
        origination balance BEFORE the 2026-02-01 trueup resets to $100,000:
        interest round(250000 * 0.005) = 1250.00 lands in the interest ledger, but
        its principal is wiped by the reset -- the reader ends at the verified
        $100,000, exactly as the resolver (which does not replay P1).  A SWAPPED
        tie-break (reset first) would instead give interest 500.00 and a balance of
        99500.00, so this pins the ordering the whole trued-up-balance design rests
        on (no other fixture places a due date on an anchor date).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Trueup date EQUALS P1's 2026-02-01 due date (payment_day=1).
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 2, 1),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            # P1 is split BEFORE the reset -> interest on the origination balance.
            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, scenario_id,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")

            # With opening + true-up the reader lands on the verified 100000 --
            # P1's principal subsumed by the reset (a swapped tie-break -> -99500).
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")


class TestLoanAnchorPeriodAttribution:
    """A loan correction re-files when the calendar grows a period around it.

    The loan twin of the account side's R2 fix (plan step X-ai-r).  A loan
    anchor carries no stored ``pay_period_id`` -- ``budget.loan_anchor_events``
    has no such column and the origination is synthesized from ``LoanParams``
    -- so its period is DERIVED from its date through
    ``pay_calendar.PayCalendar.filing_period``.  When no period CONTAINS the
    date it clamps to the LATEST period OPENING on or before it (and to the
    earliest only when the date precedes the whole schedule).

    *That rule was ``loan_ledger.resolve_anchor_pay_period`` until pay-calendar
    plan step C2-d, whose fallback keyed on the latest period ENDING before the
    date.  The two give the same answer on a schedule that is non-overlapping
    AND whose ``period_index`` order matches its ``start_date`` order -- BOTH
    halves, because the old chain reduced by index and this one by date -- which
    is why nothing in this class had to change.  The precondition and its proofs
    are stated at ``pay_calendar.PayCalendar.filing_period``.*

    That fallback is where the calendar and the ledger come apart.  A user
    whose schedule ends before their asserted anchor date gets the correction
    filed against whichever period they happen to have last; extending the
    schedule (an ordinary ``extend_pay_periods`` or the rolling-window top-up)
    then creates the period that really contains it.  Under a period-blind
    reconcile key the target and the posted entry compared equal -- same source
    kind, same date -- so the delta was zero and the correction sat in the
    stale period forever, and any later adjustment would have been filed there
    too.  With the period in the key the stale period reverses to zero and the
    real one posts fresh.
    """

    _LATE_ANCHOR_DATE = date(2026, 6, 15)

    def test_extending_the_schedule_refiles_an_anchor_into_its_real_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A true-up dated past the schedule moves to the period that grows around it.

        ``seed_periods`` ends 2026-05-21, so a $100,000 true-up dated
        2026-06-15 has no containing period and books against the LAST one by
        fallback.  Its correction is ``owed_before - verified = 250000 -
        100000 = +150,000.00``.  Extending the schedule by three periods
        creates the one containing 2026-06-15, so the re-sync must net the
        stale period back to ``0.00`` and carry the full ``+150,000.00`` in the
        real one.  The loan's total is unchanged either way -- this is an
        attribution move, not a revaluation.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            loan = _make_loan(seed_user, anchor_date=self._LATE_ANCHOR_DATE)
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            stale_period = seed_periods[-1]
            # The precondition: no seeded period contains the anchor date, so
            # the true-up landed on the last one by fallback.
            assert last_covered_day(stale_period) < self._LATE_ANCHOR_DATE
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, stale_period.id,
            ) == Decimal("150000.00")

            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()
            containing = next(
                period for period in new_periods
                if period.start_date
                <= self._LATE_ANCHOR_DATE
                <= last_covered_day(period)
            )

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, stale_period.id,
            ) == Decimal("0.00")
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, containing.id,
            ) == Decimal("150000.00")
            # -250000 opening + 150000 true-up: the attribution moved, the
            # balance did not.
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert_ledger_parity(db.session, scenario_id)

    def test_the_refiled_anchor_reconcile_is_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-syncing after the re-file writes nothing and moves no period.

        The convergence half: the finer key must reach its fixpoint in one
        pass rather than shuttling the correction between the two periods.
        Asserts the re-filed STATE either side of the extra sync -- an earlier
        version checked only the entry count and the total, which a re-file
        that never happened satisfies just as well, so it passed against the
        un-fixed code and graded nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            loan = _make_loan(seed_user, anchor_date=self._LATE_ANCHOR_DATE)
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            stale_period = seed_periods[-1]
            new_periods = pay_period_admin.extend_pay_periods(user_id, 3)
            db.session.commit()
            containing = next(
                period for period in new_periods
                if period.start_date
                <= self._LATE_ANCHOR_DATE
                <= last_covered_day(period)
            )
            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()
            settled = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            # The re-file the fixpoint is being asserted OVER -- without this
            # the test cannot tell convergence from nothing having moved.
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, containing.id,
            ) == Decimal("150000.00")
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, stale_period.id,
            ) == Decimal("0.00")

            loan_posting_service.sync_loan_postings(
                loan.id, scenario_id,
            )
            db.session.commit()

            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )) == settled
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, containing.id,
            ) == Decimal("150000.00")
            assert _anchor_net_in_period(
                loan.id, scenario_id,
                PostingSourceEnum.LOAN_TRUEUP, stale_period.id,
            ) == Decimal("0.00")
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert_ledger_parity(db.session, scenario_id)


# ---------------------------------------------------------------------------
# sync_loan_postings_all_scenarios -- the unified loan-global entry point
# ---------------------------------------------------------------------------


class TestUnifiedAllScenariosSync:
    """The all-scenarios sync posts a payment-less loan's opening in the baseline."""

    def test_posts_opening_for_a_payment_less_loan_in_the_baseline(
        self, app, db, seed_user,
    ):
        """A brand-new loan with no payments gets its opening posted in the baseline.

        A payment-less loan is in NO scenario's payment set
        (``scenarios_with_loan_payments`` is empty), so the all-scenarios sync
        must add the owner's baseline -- otherwise ``create_params`` would post no
        opening for a fresh loan.  Origination $250,000, trueup $100,000: the
        opening (-250000) + true-up (+150000) net the baseline linked ledger to
        -100000.00 == -(anchor 100000), and the per-loan opening-equity ledger
        holds +100000; no payment-correction ledger is minted (there is no
        payment).  This is the ``create_params`` N1 path the chokepoint relies on.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id  # the owner's baseline
            loan = _make_loan(seed_user)  # no payments settled

            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            # Opening (-250000) + true-up (+150000), no payment.
            assert account_net(
                _db.session, loan.id, scenario_id,
            ) == Decimal("-100000.00")
            equity_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert equity_ledger is not None
            assert _equity_net(loan, scenario_id) == Decimal("100000.00")
            # No payment-correction ledger is minted without a payment.
            assert _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            ) is None
