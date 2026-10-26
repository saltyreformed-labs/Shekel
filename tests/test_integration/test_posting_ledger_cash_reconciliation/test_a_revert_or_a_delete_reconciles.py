"""The ledger reconciles through a revert, a recategorize, a move and a hard delete, and over the
dated and undated purchases of settled, unsettled and cancelled envelopes.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountClassEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.models.account import AccountAnchorHistory
from app.models.transaction_entry import TransactionEntry
from app.services import posting_service, status_seam, transaction_service
from app.utils.balance_predicates import balance_excluded_status_ids
from tests._test_helpers import (
    family_journal_filter,
    figure_source_columns,
    add_txn,
    create_envelope_txn,
    create_settled_cash_transaction,
    linked_ledger_account,
    settle_day_columns,
    settlement_if_settling,
)

from ._helpers import (
    _independent_ledger_sum,
    _ledger_account_sum,
    _independent_movement_effect,
    _signed_cash_effect,
    _assert_full_reconciliation,
    _counter_ledger_id,
)


# ---------------------------------------------------------------------------
# THE BACKFILL-VS-GO-FORWARD CASE WAS DELETED AT PLAN STEP X-f1
# ---------------------------------------------------------------------------
#
# ``TestBackfillAndGoForwardAgree`` drove the historical migration's frozen raw
# SQL, which reads a ``paid_at`` column migration ``a3f7c8e21b64`` DROPS.  It is
# the same class the developer ruled on for the two dedicated backfill suites
# (2026-08-03): the path is UNREACHABLE with data -- the migration runs only at
# its own point in the chain, long before the drop, over an empty table, and
# ``a3f7c8e21b64``'s downgrade REFUSES so Alembic cannot rewind past the drop --
# so the case graded a producer that can never run again against one that runs
# every day.  What it asserted, and what the rest of this oracle still asserts
# without it: the two producers post leg-for-leg identical entries for one
# settled source, and the ledger reconciles either way.  The GO-FORWARD half of
# that is covered by every other case here; the historical half has no future.


# ---------------------------------------------------------------------------
# 8. Revert-and-recategorize regression lock (plan Section 2.8 CRITICAL)
# ---------------------------------------------------------------------------


class TestRevertAndRecategorizeReconciles:
    """A revert+recategorize PATCH keeps the whole-ledger sweep reconciled."""

    @pytest.mark.server_clock
    def test_revert_recategorize_resettle_reconciles_full_sweep(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Settle in A, revert+recategorize to B in one PATCH, re-settle; sweep.

        The route-level 2.8 CRITICAL: a Paid Groceries expense (posted to the
        Groceries-Expense counter) is reverted to Projected AND recategorized to
        Rent in ONE PATCH (the lock lifts on the revert), then re-settled.  The
        reconcile reverses the OLD counter read from the ledger, so Groceries nets
        to zero and Rent carries the expense.  Beyond the per-account check the
        Commit-6 lifecycle test makes, this asserts the COUNTER-ACCOUNT LINKAGE
        SWEEP reconciles after the move -- proving the linkage formula excludes
        the net-zero Groceries reversal pair and attributes the effect to Rent.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            rent_id = seed_user["categories"]["Rent"].id
            txn = add_txn(
                db.session, seed_user, seed_periods_today[0], "Groceries",
                "50.00", category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            # Settle (posts to Groceries-Expense), then revert+recategorize to
            # Rent in one PATCH, then re-settle (posts to Rent-Expense).
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(projected_id),
                    "category_id": str(rent_id),
                },
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id)},
            ).status_code == 200

            # Rent now carries the expense; Groceries netted to zero; the whole
            # ledger -- linked, counter (by linkage), per-entry, trial -- ties.
            rent_counter = _counter_ledger_id(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE, rent_id,
            )
            groceries_counter = _counter_ledger_id(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            assert _ledger_account_sum(
                rent_counter, scenario_id,
            ) == Decimal("50.00")
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("0.00")
            _assert_full_reconciliation(scenario_id)


class TestRevertAndMoveReconciles:
    """A revert+move PATCH keeps every period's ledger attribution intact."""

    @pytest.mark.server_clock
    def test_revert_move_resettle_attributes_per_period(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Settle in P, revert+move to F in one PATCH, re-settle; per-period ties.

        The route-level R2 regression (the 2026-07-02 adversarial review's H1
        class): a Paid $50 expense in period P is reverted to Projected AND
        moved to a future period F in ONE PATCH (the finalised lock lifts on
        the revert), then re-settled.  The handler applies the new
        ``pay_period_id`` BEFORE the end-of-handler reconcile (since ruling
        **R-BAL58** the revert's own reconcile runs first and the handler
        reconciles again after the move; this case holds under both), so a
        reversal stamped with the row's current period would land in F --
        leaving P's
        entry and its reversal straddling two periods, where truncating F
        CASCADE-deletes half the pair and permanently strands the other
        (``transaction_id`` SET NULL, unhealable).  Under the R2 attribution
        rule the reversal lands in P: P's entries net to zero per ledger
        account, F carries exactly the re-settled -50/+50, and the whole
        sweep ties after every step.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            original = seed_periods_today[0]
            moved_to = seed_periods_today[5]
            txn = add_txn(
                db.session, seed_user, original, "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)

            # Revert AND move in one PATCH (the H1 flow).
            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(projected_id),
                    "pay_period_id": str(moved_to.id),
                },
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)
            # The reversal landed in the ORIGINAL period: P nets to zero per
            # ledger account, and F holds no entries at all yet.
            assert _period_ledger_nets(txn_id, original.id) == {}
            assert not _entry_ids_in_period(txn_id, moved_to.id)

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id)},
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)

            # F carries exactly the re-settled split; P still nets to zero.
            # (LINKED-kind lookup: a bare-account_id .scalar() would raise
            # MultipleResultsFound beside the Step-5 anchor-equity twin.)
            cash_ledger = linked_ledger_account(
                _db.session, seed_user["account"].id,
            ).id
            moved_nets = _period_ledger_nets(txn_id, moved_to.id)
            assert moved_nets[cash_ledger] == Decimal("-50.00")
            assert sum(moved_nets.values()) == Decimal("0.00")
            assert _period_ledger_nets(txn_id, original.id) == {}


def _period_ledger_nets(transaction_id, pay_period_id):
    """Return ``{ledger_account_id: net}`` for one transaction in one period.

    Zero nets are dropped, so a period whose entries fully cancel (an
    original + its reversal) returns ``{}`` -- the R2 attribution tests'
    "nets to zero per ledger account" shape.  Independent of the service's
    own per-period reader (a direct grouped query).
    """
    rows = (
        _db.session.query(
            Posting.ledger_account_id, _db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            family_journal_filter(transaction_id),
            JournalEntry.pay_period_id == pay_period_id,
        )
        .group_by(Posting.ledger_account_id)
        .all()
    )
    return {
        ledger_id: net for ledger_id, net in rows if net != 0
    }


def _entry_ids_in_period(transaction_id, pay_period_id):
    """Return the journal-entry ids a transaction holds in one period."""
    return [
        entry_id for (entry_id,) in (
            _db.session.query(JournalEntry.id)
            .filter(
                family_journal_filter(transaction_id),
                JournalEntry.pay_period_id == pay_period_id,
            )
            .all()
        )
    ]


# ---------------------------------------------------------------------------
# Reverted transaction reconciles at zero (append-only correction discipline)
# ---------------------------------------------------------------------------


class TestRevertedTransactionReconcilesAtZero:
    """A settled-then-reverted cash transaction reconciles to zero both sides."""

    def test_reverted_transaction_reconciles_at_zero(
        self, app, db, seed_user,
    ):
        """Settle +50 expense, revert; the ledger nets to zero and ties.

        Arithmetic: a $50 Groceries expense posts -50 / +50, then a revert to
        Projected reconciles a -50 / +50 reversal (append-only).
        Groceries-Expense nets to zero and Checking lands back on its
        $1000.00 opening, the revert UN-DATES the row's kept covering movement
        so it drops from the source effect too, and two entries survive (the
        original is never edited).

        **The pair's LINK assertion was re-expressed at plan step
        ``balance:X-bi-3e-2``** under ruling **R-BAL61**'s clause "a revert
        UN-DATES the covering movement and keeps it" and the ``X-bi-3e`` step
        sentence that ruled it: the old assertion (``transaction_entry_id IS
        NULL`` on both entries) pinned the SET NULL side effect of the
        revert DELETING the movement, which is the behaviour that ruling
        changed; the money assertions and the full sweep are unchanged.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]

            txn = create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            groceries_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("50.00")

            # Revert to Projected via the real primitives (seam, then reconcile).
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.PROJECTED)),
            )
            posting_service.sync_transaction_postings(txn)
            db.session.commit()

            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("0.00")
            # Two entries survive (settle + reversal); neither was edited.
            # They are the covering movement's (plan step X-bi-3a), and the
            # revert KEEPS that mirror un-dated (plan step X-bi-3e-2, ruling
            # R-BAL61) while the family reconcile reverses its legs -- so the
            # pair stands as PURCHASE-sourced history still LINKED to the
            # surviving movement, netting to zero.  (Through 3e-1 the revert
            # deleted the mirror and the pair stood unlinked, SET NULL.)
            (survivor,) = txn.covering_movements
            assert survivor.settled_on is None
            assert (
                _db.session.query(JournalEntry)
                .filter(
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.PURCHASE,
                    ),
                    JournalEntry.transaction_entry_id == survivor.id,
                )
                .count()
            ) == 2
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# A PURCHASE is a posting source of its own (plan step X-f3b, ruling R-FM)
# ---------------------------------------------------------------------------


class TestAPostedPurchaseReconcilesUnderAnUnsettledParent:
    """The sweep must describe the ledger a posted purchase produces.

    **The whole file was blind to this shape until plan step X-f3b**, and that
    is the finding it was added for: no test here creates a ``settled_on`` on a
    purchase, so both sweep assertions could go on passing while the ledger held
    legs neither of them could account for -- a linked total no ``effective``
    figure contains, and a counter leg carrying ``transaction_id = NULL`` that
    the hard-delete branch would have called a failed reversal.
    """

    def test_a_projected_envelopes_posted_purchase_reconciles_both_sweeps(
        self, app, db, seed_user,
    ):
        """A $40 purchase under a $100 Projected envelope, taken by the bank.

        Arithmetic: the envelope books nothing of its own (no row does since
        plan step ``balance:X-bi-4a``, and this one has not settled either);
        the purchase books ``Checking -40.00 / Groceries-Expense +40.00``
        on the day the bank took it.  Checking's linked ledger is therefore
        ``1000.00 - 40.00 = 960.00`` and the Groceries counter is ``+40.00``.

        Both figures are asserted directly AND the full sweep is run, because
        the sweep is what 15 other tests rely on: its linked arm's movement
        term (`_independent_movement_effect`) and its counter arm's parent
        resolution must both hold over a PURCHASE under an OPEN envelope --
        the one movement in this file that is not the seam's covering one.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]

            # The purchase is dated AFTER the account's opening assertion, which
            # is this whole file's stated precondition ("every settle in this
            # suite is stamped after the origination assertion, so the effects
            # ride on top of the opening").  Dated on or before it, the opening's
            # own correction would absorb the leg and the ledger would land on
            # the asserted balance instead -- true, and a different invariant
            # from the one these sweeps grade.
            posted_on = _db.session.query(
                _db.func.max(AccountAnchorHistory.observed_on),
            ).filter(
                AccountAnchorHistory.account_id == checking.id,
            ).scalar() + timedelta(days=1)
            txn = create_envelope_txn(
                seed_user, db.session, period, "Groceries", Decimal("100.00"),
            )
            txn.category_id = seed_user["categories"]["Groceries"].id
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user_id,
                amount=Decimal("40.00"),
                description="Kroger",
                purchased_on=posted_on,
                **settle_day_columns(posted_on),
                is_credit=False,
            )
            _db.session.add(entry)
            _db.session.flush()
            posting_service.sync_transaction_postings(txn)
            db.session.commit()

            groceries_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("960.00")
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("40.00")
            # The parent booked nothing of its own -- otherwise the $40.00
            # above could be its cash leg rather than the purchase's.
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transaction_id=txn.id)
                .count()
            ) == 0
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transaction_entry_id=entry.id)
                .count()
            ) == 1
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# A CANCELLED envelope's DATED purchase is worth nothing on either arm
# ---------------------------------------------------------------------------


class TestACancelledEnvelopesDatedPurchaseReconcilesAtZero:
    """The contributing gate, graded on the per-row twin and the sweep.

    The writer posts a dated purchase only under a contributing parent
    (``purchase_posts``, ruling **R-FM**): cancelling the envelope reverses
    the purchase's leg.  The SQL oracle filters the same gate; the per-row
    twin the counter arm uses must too, or a writer that dropped the term
    would leave ``+40`` on the Groceries counter that the twin's ``-40``
    exactly explains, and only the linked arm would fire (the X-bi-4a M1
    fix's review, 2026-09-18).  So the twin is asserted at ``0`` DIRECTLY
    while the purchase is still dated, beside the sweep.
    """

    def test_cancelling_the_parent_zeroes_both_arms(self, app, db, seed_user):
        """A $40 dated purchase under a $100 envelope, then the envelope Cancelled.

        Arithmetic: the purchase posts ``Checking -40.00 / Groceries +40.00``
        on its bank day; the cancel reverses the pair (Checking back to its
        $1000.00 opening, Groceries 0.00).  The purchase keeps its
        ``settled_on``; the parent's exclusion is what makes the family worth
        ``0`` -- on the SQL oracle, on the per-row twin, and in the sweep.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]
            posted_on = _db.session.query(
                _db.func.max(AccountAnchorHistory.observed_on),
            ).filter(
                AccountAnchorHistory.account_id == checking.id,
            ).scalar() + timedelta(days=1)
            txn = create_envelope_txn(
                seed_user, db.session, period, "Groceries", Decimal("100.00"),
            )
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user_id,
                amount=Decimal("40.00"), description="Kroger",
                purchased_on=posted_on, **settle_day_columns(posted_on),
                is_credit=False,
            )
            _db.session.add(entry)
            _db.session.flush()
            posting_service.sync_transaction_postings(txn)
            db.session.commit()
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("960.00")
            assert _signed_cash_effect(txn) == Decimal("-40.00")

            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
            status_seam.apply_status_change(
                txn, cancelled_id,
                settlement=settlement_if_settling(txn, cancelled_id),
            )
            posting_service.sync_transaction_postings(txn)
            db.session.commit()
            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            (purchase,) = txn.purchases
            assert purchase.settled_on == posted_on  # still dated
            assert txn.status_id in balance_excluded_status_ids()

            # The twin, directly: without the gate this reads -40.00.
            assert _signed_cash_effect(txn) == Decimal("0")
            assert _independent_movement_effect(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            groceries_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE, txn.category_id,
            )
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("0.00")
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# A settled envelope's UN-DATED purchase is in flight, not in the ledger
# ---------------------------------------------------------------------------


class TestASettledEnvelopesUndatedPurchasePostsNothing:
    """The shape the oracle could not tell apart through ``X-bi-3e``.

    The reason the source side was re-expressed (the X-bi-4a review's M1):
    the pre-4a writer booked a settled envelope's un-dated purchases as the
    ROW's own leg on the close day, and an oracle spelling ``effective -
    Sigma(credit)`` over settled rows agreed with that ledger, so no case
    here could catch a writer that regressed to it.  Since plan step
    ``balance:X-bi-4a`` (rulings **R-BAL77** / **R-BAL80**) a purchase posts
    when it is DATED, on its own day, and the close books nothing; the
    movement oracle expects nothing for an un-dated purchase, so the
    regressed writer's leg makes the linked sweep FAIL.  Proven to fire
    under exactly that mutation on 2026-09-18 (``_emit_transaction_deltas``
    handed the pre-4a own-leg target for a settled row).
    """

    def test_a_closed_envelopes_undated_purchase_reconciles_at_zero(
        self, app, db, seed_user,
    ):
        """A $200 envelope closed over one un-dated $65 purchase posts nothing.

        Arithmetic: the envelope's record is ``purchases`` ($65.00, the one
        purchase), so the seam writes no covering movement; the purchase
        carries no ``settled_on``, so it posts nothing.  Checking's linked
        ledger stays at its $1000.00 opening, the Groceries counter at 0.00,
        the family holds no journal entry, and the independent movement
        effect is 0.00.  The sweep runs FIRST, because the sweep is the claim
        under test; the literals after it pin the same figures by hand.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]

            txn = create_envelope_txn(
                seed_user, db.session, period, "Groceries", Decimal("200.00"),
            )
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user_id,
                amount=Decimal("65.00"), description="Kroger",
                purchased_on=period.start_date, is_credit=False,
            ))
            db.session.flush()
            transaction_service.settle_transaction(txn)
            db.session.commit()
            txn_id = txn.id
            assert txn.status.is_settled
            assert txn.covering_movements == []
            (purchase,) = txn.purchases
            assert purchase.settled_on is None

            _assert_full_reconciliation(scenario_id)
            assert _independent_movement_effect(
                checking.id, scenario_id,
            ) == Decimal("0.00")
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            groceries_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE, txn.category_id,
            )
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("0.00")
            assert (
                _db.session.query(JournalEntry)
                .filter(family_journal_filter(txn_id))
                .count()
            ) == 0


# ---------------------------------------------------------------------------
# Hard delete: SET-NULL'd legs reconcile via the transaction_id-NULL branch
# ---------------------------------------------------------------------------


class TestHardDeletedTransactionReconcilesAtZero:
    """A hard-deleted settled cash transaction leaves a balanced net-zero pair."""

    def test_hard_delete_settled_cash_reconciles_via_null_linkage(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Hard-deleting a Paid ad-hoc expense severs the link but stays balanced.

        Arithmetic: a $50 Groceries expense posts -50 / +50.  Deleting the ad-hoc
        (template-less) row hard-deletes it: the route reverses the postings FIRST
        (a -50 / +50 reversal), then the row is removed and
        ``journal_entries.transaction_id`` SET-NULLs on BOTH the original and the
        reversal (the immutable legs survive, append-only).  The Groceries-Expense
        counter therefore nets to zero across a ``transaction_id IS NULL`` group --
        the exact branch of the counter sweep that asserts a hard-deleted
        transaction's legs were reversed, not stranded.  This is the route-driven
        production path (``delete_transaction`` ->
        ``reverse_postings_before_delete`` -> FK SET NULL) the oracle claims to
        cover.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods_today[0], "Groceries",
                "50.00", category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            groceries_counter = _counter_ledger_id(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("50.00")

            # Hard delete (ad-hoc row): reverse-before-delete, then SET NULL.
            assert auth_client.delete(
                f"/transactions/{txn_id}",
            ).status_code == 200

            # The row is gone; both legs' transaction_id SET-NULLed; the counter
            # nets to zero across the NULL-linkage group.
            assert db.session.get(Transaction, txn_id) is None
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transaction_id=txn_id)
                .count()
            ) == 0
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("0.00")
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            # Drives the transaction_id-NULL branch of the counter sweep.
            _assert_full_reconciliation(scenario_id)
