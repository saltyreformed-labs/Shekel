"""The movement-effect oracle agrees with the read, and a revert-and-move leaves the old paycheck
with no line and books the re-settle into the new one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum, StatusEnum
from app.extensions import db as _db
from app.services import status_seam, transfer_service
from app.services.ledger_lines import category_key, linked_key
from app.utils.dates import display_today
from tests._ledger_reads import account_net
from tests._records_oracle import posted_purchase_effect
from tests._test_helpers import (
    add_txn,
    an_entered_day,
    create_envelope_txn,
    settlement_if_settling,
)
from app.services.settle_day import record_settle_day

from tests._test_helpers import create_settled_transfer

from ._helpers import (
    _scenario_id,
    _period_nets_for_transaction,
    _period_nets_for_transfer,
    _add_txn_entry,
)


class TestPostedPurchaseEffect:
    """The movement-effect oracle agrees with the worked-out read.

    ``settled_transaction_effect``, the transaction source's oracle, went at
    plan step ``balance:X-bi-4a`` with the row's own leg (ruling **R-BAL80**):
    ``posted_purchase_effect`` is the whole non-transfer half of the oracle's
    per-account invariant now -- every DATED movement of a contributing
    parent, on the movement's account, whatever the parent's status.  Its
    per-transaction credit-sum subquery, which a case here pinned as
    correlated rather than global, went with it: the movement oracle excludes
    a card purchase by the movement's own flag, one row at a time.
    """

    def test_effect_matches_the_worked_out_net(self, app, db, seed_user):
        """The signed movement effect rides on top of the opening.

        Arithmetic: a $50 Paid expense (-50) and a $2000 Received income
        (+2000) on Checking net to +1950.00 -- each settled row's money is its
        ONE covering movement.  ``posted_purchase_effect`` (a source-table
        query over the movements) reports exactly that; both settles are
        dated today (post-assertion), so ``account_net`` (the worked-out
        ledger) carries the same +1950.00 on top of the $1000.00 opening --
        2950.00.  The read and the source query agree on the movements'
        contribution only because no transfers exist here, so the
        post-opening cash legs are entirely movement-sourced.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_on=display_today(),
            )
            add_txn(
                _db.session, seed_user, period, "Salary", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary", settled_on=display_today(),
            )
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            account_id = seed_user["account"].id
            # -50 (expense) + 2000 (income) = 1950; ledger 1000 + 1950.
            assert posted_purchase_effect(
                account_id, scenario_id,
            ) == Decimal("1950.00")
            assert account_net(
                _db.session, account_id, scenario_id,
            ) == Decimal("2950.00")

    def test_effect_excludes_credit_portion(self, app, db, seed_user):
        """The oracle sums the DATED DEBIT movements and leaves a card purchase out.

        Arithmetic: a settled envelope with $60 + $50 debit purchases, both
        dated, and a $40 card purchase.  The two debit movements are -110.00
        of expense; the card purchase leaves through its CC Payback and is
        excluded by its own ``is_credit`` flag, exactly as the read's loader
        leaves it out (``cash_ledger._movement_scope._dated_arms``).
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

            assert posted_purchase_effect(
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

            assert posted_purchase_effect(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("-80.00")


# ---------------------------------------------------------------------------
# Period attribution: a line is filed where its movement records it (review R2)
# ---------------------------------------------------------------------------


class TestPeriodAttribution:
    """A revert-and-move leaves the settle's paycheck with no line.

    The 2026-07-02 adversarial review's R2 rule (fixing H1): the supported
    revert-and-move PATCH applies the new ``pay_period_id`` together with the
    revert, so the stored ledger's reversal, stamped with the row's CURRENT
    period, would have landed in the NEW paycheck and left the original entry
    and its reversal straddling two -- where a later truncate of the new
    paycheck CASCADE-deleted one half and stranded the other.  The store's
    writer read the posted side back per period to avoid it; the read has no
    reversal to file, because an un-dated movement books nothing, so the old
    paycheck simply holds no line and the re-settle books into the new one.
    """

    def test_revert_and_move_reverses_into_the_original_period(
        self, app, db, seed_user, seed_periods,
    ):
        """The settle's paycheck holds nothing after the move; the re-settle books into F.

        Settle a $100 expense in period P (paid 2026-01-05).  Apply the
        revert-and-move PATCH exactly as the handler applies it --
        ``pay_period_id`` moved to F and the status set back through the seam,
        which clears the settle day.  P and F then hold no line.  A later
        re-settle books into F alone, dated the day the user re-settled it on
        -- the seam's stamp: the row's budget period and its cash day are
        allowed to disagree.
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
            groceries = category_key(
                seed_user["categories"]["Groceries"].id,
                LedgerAccountClassEnum.EXPENSE,
            )
            # The settle books into P.
            assert _period_nets_for_transaction(
                txn.id, seed_user["scenario"].id,
            ) == {
                period.id: {
                    linked_key(seed_user["account"]): Decimal("-100.00"),
                    groceries: Decimal("100.00"),
                },
            }

            # The revert-and-move PATCH, exactly as ``_apply_regular_update``
            # applies it: the new period lands, and the status goes through the
            # SEAM -- which is what clears the settle day, since plan step X-f1
            # made the two one write.
            txn.pay_period_id = moved_to.id
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.PROJECTED)),
            )
            _db.session.flush()
            assert txn.settled_on is None

            # Neither paycheck holds a line.
            assert _period_nets_for_transaction(
                txn.id, seed_user["scenario"].id,
            ) == {}

            # A later re-settle books into the NEW period alone, on the seam's
            # stamp.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.commit()
            assert txn.settled_on == display_today()
            assert _period_nets_for_transaction(
                txn.id, seed_user["scenario"].id,
            ) == {
                moved_to.id: {
                    linked_key(seed_user["account"]): Decimal("-100.00"),
                    groceries: Decimal("100.00"),
                },
            }

    def test_transfer_revert_and_move_reverses_into_the_original_period(
        self, app, db, seed_user, seed_periods, savings,
    ):
        """A transfer's revert-and-move leaves the settle's paycheck with no line, like a row's.

        Settle a $100 Checking -> Savings transfer in period P, then revert AND
        move it to F in one call through the transfer door: the revert
        un-dates both records (ruling **R-BAL61**), so neither P nor F holds a
        line -- the transfer twin of the transaction case above.
        """
        with app.app_context():
            period = seed_periods[0]
            moved_to = seed_periods[5]
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                period, amount=Decimal("100.00"),
            )
            _db.session.commit()
            # The settle books into P.
            assert set(_period_nets_for_transfer(
                transfer.id, seed_user["scenario"].id,
            )) == {period.id}

            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                pay_period_id=moved_to.id,
            )
            _db.session.commit()

            # P holds $0.00 on both real keys and transit, and F holds nothing:
            # the read holds no $0.00 line, so neither paycheck is here.
            assert _period_nets_for_transfer(
                transfer.id, seed_user["scenario"].id,
            ) == {}
