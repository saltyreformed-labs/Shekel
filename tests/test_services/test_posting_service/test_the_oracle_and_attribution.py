"""The movement-effect oracle agrees with the posting total, and a reversal carries the period and
date of the postings it reverses.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.services import posting_service, status_seam, transfer_service
from app.utils.dates import display_today
from tests._test_helpers import (
    add_txn,
    an_entered_day,
    create_envelope_txn,
    create_settled_transfer,
    settlement_if_settling,
)
from app.services.settle_day import record_settle_day

from ._helpers import (
    _ledger_id,
    _transit_ledger_id,
    _side_entries,
    _legs_by_ledger,
    _scenario_id,
    _period_nets_for_transaction,
    _period_nets_for_transfer,
    _add_txn_entry,
)


class TestPostedPurchaseEffect:
    """The movement-effect oracle agrees with the posting total.

    ``settled_transaction_effect``, the transaction source's oracle, went at
    plan step ``balance:X-bi-4a`` with the row's own leg (ruling **R-BAL80**):
    ``posted_purchase_effect`` is the whole non-transfer half of the oracle's
    per-account invariant now -- every DATED movement of a contributing
    parent, on the movement's account, whatever the parent's status.  Its
    per-transaction credit-sum subquery, which a case here pinned as
    correlated rather than global, went with it: the movement oracle excludes
    a card purchase by the movement's own flag, one row at a time.
    """

    def test_effect_matches_posting_total(self, app, db, seed_user):
        """The signed movement effect rides on top of the opening.

        Arithmetic: a $50 Paid expense (-50) and a $2000 Received income
        (+2000) on Checking net to +1950.00 -- each settled row's money is its
        ONE covering movement.  ``posted_purchase_effect`` (a source-table
        query over the movements) reports exactly that; both settles are
        dated today (post-assertion), so ``account_posting_total`` (the ledger
        sum) carries the same +1950.00 on top of the $1000.00 opening --
        2950.00.  The two independent tables agree on the movements'
        contribution only because no transfers exist here, so the
        post-opening cash legs are entirely movement-sourced.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            expense = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_on=display_today(),
            )
            income = add_txn(
                _db.session, seed_user, period, "Salary", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary", settled_on=display_today(),
            )
            _db.session.commit()
            posting_service.sync_transaction_postings(expense)
            posting_service.sync_transaction_postings(income)
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            account_id = seed_user["account"].id
            # -50 (expense) + 2000 (income) = 1950; ledger 1000 + 1950.
            assert posting_service.posted_purchase_effect(
                account_id, scenario_id,
            ) == Decimal("1950.00")
            assert posting_service.account_posting_total(
                account_id, scenario_id,
            ) == Decimal("2950.00")

    def test_effect_excludes_credit_portion(self, app, db, seed_user):
        """The oracle sums the DATED DEBIT movements and leaves a card purchase out.

        Arithmetic: a settled envelope with $60 + $50 debit purchases, both
        dated, and a $40 card purchase.  The two debit movements are -110.00
        of expense; the card purchase leaves through its CC Payback and is
        excluded by its own ``is_credit`` flag, exactly as the go-forward
        ``purchase_posts`` leaves it out of the ledger.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Groceries Env",
                Decimal("200.00"),
            )
            sixty = _add_txn_entry(seed_user, txn, "60.00", is_credit=False)
            fifty = _add_txn_entry(seed_user, txn, "50.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "40.00", is_credit=True)
            record_settle_day(sixty, an_entered_day(display_today()))
            record_settle_day(fifty, an_entered_day(display_today()))
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.commit()

            assert posting_service.posted_purchase_effect(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("-110.00")

    def test_effect_is_blind_to_the_parents_status(self, app, db, seed_user):
        """A dated purchase under a PROJECTED envelope counts exactly as one under a closed one.

        The narrowing the oracle lost at plan step ``balance:X-bi-4a``: it
        read purchases on UNSETTLED parents alone while the settled parent's
        own leg carried the rest.  A $30.00 purchase dated today under an open
        envelope and a $50.00 bill settled today net -80.00, one term.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            envelope = create_envelope_txn(
                seed_user, _db.session, period, "Open Env", Decimal("200.00"),
            )
            thirty = _add_txn_entry(seed_user, envelope, "30.00", is_credit=False)
            record_settle_day(thirty, an_entered_day(display_today()))
            add_txn(
                _db.session, seed_user, period, "Rent", "50.00",
                status_enum=StatusEnum.DONE, category_key="Rent",
                settled_on=display_today(),
            )
            _db.session.commit()

            assert posting_service.posted_purchase_effect(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("-80.00")


# ---------------------------------------------------------------------------
# Period attribution: corrections land in the period they correct (review R2)
# ---------------------------------------------------------------------------


class TestPeriodAttribution:
    """A reversal carries the period and date of the postings it reverses.

    The 2026-07-02 adversarial review's R2 rule (fixing H1): the supported
    revert-and-move PATCH applies the new ``pay_period_id`` BEFORE the
    end-of-handler reconcile (and, since ruling **R-BAL58**, the revert's own
    reconcile runs before the move and the handler reconciles again after
    it), so a reversal stamped with the source row's CURRENT period would
    land in the NEW period -- leaving the original entry
    and its reversal straddling two periods, where a later truncate of the new
    period CASCADE-deletes one half and permanently strands the other
    (``transaction_id`` SET NULL, unhealable).  The reconcile instead reads
    the posted side back per period, so the reversal nets the ORIGINAL period
    to zero and inherits the latest entry date it reverses.
    """

    def test_revert_and_move_reverses_into_the_original_period(
        self, app, db, seed_user, seed_periods,
    ):
        """The reversal lands in the settle period, dated by the settle date.

        Settle a $100 expense in period P (paid 2026-01-05, so the settle
        entry is dated 2026-01-05 in P).  Simulate the revert-and-move PATCH
        exactly as the handler applies it -- ``pay_period_id`` moved to F and
        the settle day cleared BEFORE the reconcile -- then reconcile with
        ``settled=False``.  The reversal entry must carry period P (never F)
        and the inherited 2026-01-05 date (never F's start or today), so P
        nets to zero per ledger account and F holds nothing.  A later
        re-settle posts into F alone.
        """
        with app.app_context():
            period = seed_periods[0]
            moved_to = seed_periods[5]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_on=date(2026, 1, 5),
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])

            [settle_entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()
            assert settle_entry.pay_period_id == period.id
            assert settle_entry.entry_date == date(2026, 1, 5)

            # The revert-and-move PATCH, exactly as ``_apply_regular_update``
            # applies it: the new period lands, and the status goes through the
            # SEAM -- which is what clears the settle day, since plan step X-f1
            # made the two one write.  Both land BEFORE the reconcile.
            txn.pay_period_id = moved_to.id
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.PROJECTED)),
            )
            _db.session.flush()
            assert txn.settled_on is None
            [reversal] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            # The R2 rule: the reversal carries the ORIGINAL period and the
            # settle entry's date -- the pre-fix code stamped it with the NEW
            # period (moved_to) and the reversal-time fallback date.
            assert reversal.pay_period_id == period.id
            assert reversal.entry_date == date(2026, 1, 5)
            legs = _legs_by_ledger(reversal.id)
            assert legs[cash_ledger] == Decimal("100.00")

            # Period P nets to zero per ledger account; F holds nothing.
            per_period = _period_nets_for_transaction(txn.id)
            assert per_period[period.id][cash_ledger] == Decimal("0.00")
            assert moved_to.id not in per_period

            # A later re-settle posts into the NEW period alone, dated the day
            # the user re-settled it on -- the seam's stamp.  It dated at the
            # period's ``start_date`` until plan step X-f1, because the cleared
            # instant fell back to it; a re-settle records a real day now, and
            # the row's budget period and its cash day are allowed to disagree.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.flush()
            [resettle] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()
            assert resettle.pay_period_id == moved_to.id
            assert resettle.entry_date == display_today()
            per_period = _period_nets_for_transaction(txn.id)
            assert per_period[period.id][cash_ledger] == Decimal("0.00")
            assert per_period[moved_to.id][cash_ledger] == Decimal("-100.00")

    def test_transfer_revert_and_move_reverses_into_the_original_period(
        self, app, db, seed_user, seed_periods, savings,
    ):
        """The transfer reversal lands in the settle period, like a transaction.

        Settle a $100 Checking -> Savings transfer in period P (auto-posted at
        creation as two per-movement entries), then revert AND move it to F
        in one PATCH through the transfer door -- the revert un-dates both
        movements (ruling **R-BAL61**) and the door's reconcile reverses what
        is posted: each side's reversal entry carries P, both real ledgers
        and transit net to zero in P, and F holds nothing -- the transfer
        twin of the transaction case above.  Re-expressed at plan step
        ``balance:X-bi-6-3``: it moved the parent's period by hand and called
        the sync with ``settled=False``; the door reads the movements now, so
        the revert-and-move is made the way the PATCH makes it.
        """
        with app.app_context():
            period = seed_periods[0]
            moved_to = seed_periods[5]
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                period, amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                pay_period_id=moved_to.id,
            )
            _db.session.commit()

            by_side = _side_entries(transfer.id)
            [_, from_reversal] = by_side[checking.id]
            [_, to_reversal] = by_side[savings.id]
            assert from_reversal.pay_period_id == period.id
            assert to_reversal.pay_period_id == period.id
            assert _legs_by_ledger(to_reversal.id) == {
                savings_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            assert _legs_by_ledger(from_reversal.id) == {
                checking_ledger: Decimal("100.00"),
                transit_ledger: Decimal("-100.00"),
            }
            per_period = _period_nets_for_transfer(transfer.id)
            assert per_period[period.id][savings_ledger] == Decimal("0.00")
            assert per_period[period.id][checking_ledger] == Decimal("0.00")
            assert per_period[period.id][transit_ledger] == Decimal("0.00")
            assert moved_to.id not in per_period
