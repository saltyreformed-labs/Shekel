"""
Shekel Budget App -- Entry-Level Credit Card Workflow Tests

Tests the entry-level credit card workflow that manages aggregated CC
Payback transactions from individual credit entries on entry-capable
transactions.  Covers the sync_entry_payback 2x2 state matrix, payback
field parity with the legacy workflow, entry link integrity, Decimal
precision, full lifecycle, the legacy credit guard, integration through
entry_service hooks, and session state correctness.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.category import Category
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import NotFoundError, ValidationError
from app.services import credit_workflow, entry_service, status_seam, transaction_service
from app.services.row_valuation import settled_figure
from app.services.entry_credit_workflow import sync_entry_payback
from tests._cards import add_card
from tests._legacy_credit import plant_old_credit, plant_old_flagged_purchase
from tests._test_helpers import (
    typed,
    an_entered_day,
    figure_source_columns,
    generate_row_of,
    one_off_row_of,
)


class TestSyncEntryPayback:
    """Tests for sync_entry_payback -- the 2x2 state matrix."""

    def _create_credit_entry(self, txn, user, amount="100.00", desc="Purchase"):
        """Create a credit entry directly, bypassing entry_service hooks.

        Returns the new TransactionEntry (flushed, id available).
        """
        entry = TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=user.id,
            amount=Decimal(amount),
            description=desc,
            purchased_on=date(2026, 1, 5),
            is_credit=True,
        )
        db.session.add(entry)
        db.session.flush()
        return entry

    def _create_debit_entry(self, txn, user, amount="50.00", desc="Purchase"):
        """Create a debit entry directly, bypassing entry_service hooks.

        Returns the new TransactionEntry (flushed, id available).
        """
        entry = TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=user.id,
            amount=Decimal(amount),
            description=desc,
            purchased_on=date(2026, 1, 5),
            is_credit=False,
        )
        db.session.add(entry)
        db.session.flush()
        return entry

    # ---- Plan tests 4.1 through 4.8, 4.11, 4.12 ----

    def test_sync_creates_payback_first_credit_entry(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.1: First credit entry on a fresh transaction creates a payback.

        Verifies the CREATE cell: total_credit > 0 AND no payback exists.
        The payback should appear in the next period with the correct amount.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_credit_entry(txn, user, "100.00")

            payback = sync_entry_payback(txn.id, user.id)

            assert payback is not None
            assert payback.estimated_amount == Decimal("100.00")
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.credit_payback_for_id == txn.id
            assert payback.name == f"CC Payback: {txn.name}"

    def test_sync_updates_payback_on_second_credit(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.2: Second credit entry updates the existing payback amount.

        Verifies the UPDATE cell: total_credit > 0 AND payback already exists.
        The payback amount should be the sum of both entries.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_credit_entry(txn, user, "100.00", "First")
            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            self._create_credit_entry(txn, user, "50.00", "Second")
            payback = sync_entry_payback(txn.id, user.id)

            # Same payback, updated amount: 100 + 50 = 150.
            assert payback.id == payback_id
            assert payback.estimated_amount == Decimal("150.00")

    def test_sync_deletes_payback_when_last_credit_removed(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.3: Deleting the last credit entry removes the payback.

        Verifies the DELETE cell: total_credit == 0 AND payback exists.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = self._create_credit_entry(txn, user, "100.00")
            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            db.session.delete(entry)
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            assert db.session.get(Transaction, payback_id) is None

    def test_sync_updates_on_credit_entry_edit(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.4: Editing a credit entry's amount updates the payback.

        Verifies the UPDATE cell after an amount change.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = self._create_credit_entry(txn, user, "100.00")
            sync_entry_payback(txn.id, user.id)

            entry.amount = Decimal("75.00")
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback.estimated_amount == Decimal("75.00")

    def test_sync_handles_credit_toggle_debit_to_credit(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.5: Toggling a debit entry to credit creates a payback.

        Verifies the transition from no-op to CREATE when an entry's
        is_credit flag is set to True.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = self._create_debit_entry(txn, user, "100.00")
            result = sync_entry_payback(txn.id, user.id)
            assert result is None

            entry.is_credit = True
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback is not None
            assert payback.estimated_amount == Decimal("100.00")

    def test_sync_handles_credit_toggle_credit_to_debit(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.6: Toggling the only credit entry to debit deletes the payback.

        Verifies the transition from UPDATE/CREATE to DELETE when all
        credit entries are toggled off.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = self._create_credit_entry(txn, user, "100.00")
            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            entry.is_credit = False
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            assert db.session.get(Transaction, payback_id) is None

    def test_sync_idempotent(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.7: Calling sync when payback amount already matches is idempotent.

        The payback object and amount should be unchanged.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_credit_entry(txn, user, "100.00")
            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            payback_again = sync_entry_payback(txn.id, user.id)

            assert payback_again.id == payback_id
            assert payback_again.estimated_amount == Decimal("100.00")

    def test_sync_no_next_period_raises(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.8: Creating a payback when no next period exists raises ValidationError.

        Transactions in the last generated period cannot produce paybacks
        because there is no subsequent period for the payback to land in.
        """
        with app.app_context():
            user = seed_user["user"]
            template = seed_entry_template["template"]

            # The definition's row in the LAST period -- no period follows it.
            txn = generate_row_of(template, seed_periods[-1])

            self._create_credit_entry(txn, user, "100.00")

            with pytest.raises(ValidationError, match="No next pay period"):
                sync_entry_payback(txn.id, user.id)

    def test_payback_links_all_credit_entries(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.11: All credit entries share the same credit_payback_id.

        Three credit entries created before the first sync should all be
        linked to the single payback.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = self._create_credit_entry(txn, user, "50.00", "Store A")
            e2 = self._create_credit_entry(txn, user, "30.00", "Store B")
            e3 = self._create_credit_entry(txn, user, "20.00", "Store C")

            payback = sync_entry_payback(txn.id, user.id)

            assert e1.credit_payback_id == payback.id
            assert e2.credit_payback_id == payback.id
            assert e3.credit_payback_id == payback.id
            # 50 + 30 + 20 = 100
            assert payback.estimated_amount == Decimal("100.00")

    def test_mixed_entries_only_credit_sum_in_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.12: Mixed debit/credit entries -- payback reflects only credit sum.

        Debit entries do not contribute to the payback amount.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_debit_entry(txn, user, "200.00", "Debit 1")
            self._create_debit_entry(txn, user, "200.00", "Debit 2")
            self._create_credit_entry(txn, user, "100.00", "Credit 1")
            self._create_credit_entry(txn, user, "50.00", "Credit 2")

            payback = sync_entry_payback(txn.id, user.id)

            # Only credit entries count: 100 + 50 = 150.
            assert payback.estimated_amount == Decimal("150.00")

    def test_sync_ignores_soft_deleted_payback_and_creates_fresh(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """sync excludes a soft-deleted payback and takes the CREATE branch (#58).

        The existing-payback lookup must filter ``is_deleted == False`` to
        match the partial unique index.  When the prior payback was
        soft-deleted but credit entries still exist, sync must take the
        CREATE branch and build a fresh live payback rather than
        resurrecting and mutating the soft-deleted one.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            self._create_credit_entry(txn, user, "100.00")
            first = sync_entry_payback(txn.id, user.id)
            first_id = first.id

            # Soft-delete the payback but keep the credit entry live.
            first.is_deleted = True
            db.session.flush()

            second = sync_entry_payback(txn.id, user.id)

            # A NEW live payback was created (CREATE branch), not the dead
            # one resurrected: without the filter the lookup returned the
            # soft-deleted row and the UPDATE branch reused its id.
            assert second is not None
            assert second.id != first_id
            assert second.is_deleted is False
            assert second.estimated_amount == Decimal("100.00")

            # The soft-deleted payback is left untouched for the audit trail.
            old = db.session.get(Transaction, first_id)
            assert old.is_deleted is True

    # ---- Defense-in-depth: ownership and not-found guards ----

    def test_sync_nonexistent_transaction_raises(self, app, db, seed_user):
        """sync_entry_payback raises NotFoundError for a nonexistent transaction."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                sync_entry_payback(999999, seed_user["user"].id)

    def test_sync_wrong_owner_raises(
        self, app, db, seed_user, seed_second_user,
        seed_periods, seed_entry_template,
    ):
        """sync_entry_payback raises NotFoundError when owner_id doesn't match.

        Defense-in-depth: even if the caller already checked ownership,
        sync verifies the row's own ``user_id`` column to prevent payback
        creation under the wrong user.  It walked ``pay_period.user_id`` until
        plan step ``pay_calendar:C13-b``; the two are held equal by
        ``fk_transactions_owner_period``.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            other_user = seed_second_user["user"]

            self._create_credit_entry(txn, seed_user["user"], "100.00")

            with pytest.raises(NotFoundError):
                sync_entry_payback(txn.id, other_user.id)


class TestACardRefundNeverDestroysThePayback:
    """Finding **N-411**: the else-arm answered a NEGATIVE total by DELETING.

    ``sync_entry_payback`` branched ``if total_credit > 0`` with an else-arm
    commented ``# total_credit == 0``.  While
    ``ck_transaction_entries_positive_amount`` was ``amount > 0`` those two arms
    really were total over the reachable states, so the comment was true.  Ruling
    **bank_import:R-II** relaxed the CHECK to ``amount <> 0``, at which point
    "everything not ``> 0``" silently included a negative -- and a card refund
    larger than the row's card charges took the DELETE path.

    **Measured before the fix, on a clone of the developer's own data**: an
    envelope carrying 4 card charges totalling ``$493.03`` lost its payback
    outright, leaving those charges with nothing booking them anywhere.

    The two tests below are the two directions, and they are deliberately a
    PAIR: one asserts the destructive arm is refused, the other asserts the
    legitimate delete still happens.  A fix that simply stopped deleting would
    pass the first alone.
    """

    def _envelope_with_a_card_charge(self, db, seed_user, seed_entry_template):
        """Return a Projected envelope carrying one $100 card purchase.

        Built through ``entry_service`` rather than by hand, so the payback is
        created by the same door the refund will later re-enter.
        """
        txn = seed_entry_template["transaction"]
        plant_old_flagged_purchase(
            transaction_id=txn.id,
            user_id=seed_user["user"].id,
            details=entry_service.EntryDetails(
                figure=typed(Decimal("100.00")),
                description="Card purchase",
                purchased_on=date(2026, 1, 5),
            ),
        )
        db.session.commit()
        return txn

    def test_a_refund_exceeding_the_card_charges_is_REFUSED(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A card that would owe the OWNER is refused, and nothing is deleted.

        **The survival check is taken BEFORE the rollback, and that ordering is
        the whole assertion.**  An earlier version queried after
        ``db.session.rollback()`` and claimed to grade survival; adversarial
        review measured that FALSE -- the rollback undoes a flushed DELETE, so a
        mutant that deleted the payback and THEN raised passed it.  Reading the
        session's ``deleted`` set while the failed unit of work is still open is
        what distinguishes *refused before acting* from *acted then refused*.
        """
        with app.app_context():
            txn = self._envelope_with_a_card_charge(
                db, seed_user, seed_entry_template,
            )
            payback = credit_workflow.get_active_payback(txn.id)
            assert payback is not None
            payback_id = payback.id
            assert payback.estimated_amount == Decimal("100.00")

            with pytest.raises(ValidationError, match="owes YOU"):
                plant_old_flagged_purchase(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("-150.00")),
                        description="Card refund",
                        purchased_on=date(2026, 1, 6),
                    ),
                )

            # **A RAW COUNT inside the still-open transaction**, and each word
            # of that is load-bearing.  Taken BEFORE the rollback, because the
            # rollback undoes a flushed DELETE and is what made the original
            # version of this assertion vacuous.  Raw SQL rather than
            # ``db.session.get``, because the identity map answers a deleted
            # row from memory.  And a COUNT rather than ``db.session.deleted``,
            # because that collection holds only deletes not yet FLUSHED -- a
            # mutant that deletes AND flushes before raising empties it, which
            # is the second vacuous assertion this case was written with.
            # Verified by driving exactly that mutant.
            assert db.session.execute(
                db.text(
                    "SELECT count(*) FROM budget.transactions "
                    "WHERE id = :i AND NOT is_deleted"
                ),
                {"i": payback_id},
            ).scalar() == 1, (
                "the payback was deleted before the refusal -- N-411 has "
                "regressed"
            )

            db.session.rollback()
            survived = db.session.get(Transaction, payback_id)
            assert survived is not None
            assert survived.estimated_amount == Decimal("100.00")
            assert not survived.is_deleted

    def test_a_refund_that_exactly_CANCELS_the_charges_still_deletes(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The legitimate delete is untouched: nothing owed, no payback.

        The other direction of the pair.  ``total_credit == 0`` means the card
        is owed nothing, which is the state the else-arm was always FOR -- and
        it is now reachable a second way, by refunds cancelling charges rather
        than by no card purchases remaining.  Both mean the same thing.
        """
        with app.app_context():
            txn = self._envelope_with_a_card_charge(
                db, seed_user, seed_entry_template,
            )
            assert credit_workflow.get_active_payback(txn.id) is not None

            plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("-100.00")),
                    description="Card refund",
                    purchased_on=date(2026, 1, 6),
                ),
            )
            db.session.commit()

            assert credit_workflow.get_active_payback(txn.id) is None


class TestPaybackCorrectness:
    """Verify payback field parity, period placement, name, and precision."""

    def test_payback_fields_match_legacy(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Entry-level payback has every field the old row-level press set.

        Compares each of the 10 fields the row-level Credit press set until
        plan task plan#2929 (``credit_workflow.create_cc_payback_transaction``'s
        shape) against the payback created by sync_entry_payback.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="Field parity",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

            # All 10 fields of the payback's shape.
            assert payback.account_id == txn.account_id
            assert payback.template_id is None
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.scenario_id == txn.scenario_id
            assert payback.status_id == projected_id
            assert payback.name == f"CC Payback: {txn.name}"
            assert payback.transaction_type_id == expense_type_id
            assert payback.estimated_amount == Decimal("100.00")
            assert payback.credit_payback_for_id == txn.id

            # Category must be the CC Payback category for the owner.
            cat = db.session.get(Category, payback.category_id)
            assert cat.group_name == "Credit Card"
            assert cat.item_name == "Payback"
            assert cat.user_id == user.id

    def test_payback_period_is_next_not_same(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Payback lands in the NEXT period, not the parent's period.

        The parent transaction is in seed_periods[0]; the payback must
        be in seed_periods[1].
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            assert txn.pay_period_id == seed_periods[0].id

            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Period check",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback.pay_period_id == seed_periods[1].id
            assert payback.pay_period_id != txn.pay_period_id

    def test_payback_name_format(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Payback name follows 'CC Payback: {parent_name}' pattern.

        The seed_entry_template transaction name is 'Weekly Groceries'.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Name check",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback.name == "CC Payback: Weekly Groceries"

    def test_independent_paybacks_per_transaction(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Two transactions from the same template have independent paybacks.

        Each transaction's credit entries produce a separate payback with
        the correct amount and credit_payback_for_id.
        """
        with app.app_context():
            txn1 = seed_entry_template["transaction"]
            user = seed_user["user"]
            template = seed_entry_template["template"]

            # The same definition's row in a later period.
            txn2 = generate_row_of(template, seed_periods[2])

            e1 = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn1.id, account_id=txn1.account_id, owner_id=txn1.user_id, user_id=user.id,
                amount=Decimal("100.00"), description="Txn1",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            e2 = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn2.id, account_id=txn2.account_id, owner_id=txn2.user_id, user_id=user.id,
                amount=Decimal("200.00"), description="Txn2",
                purchased_on=date(2026, 1, 30), is_credit=True,
            )
            db.session.add_all([e1, e2])
            db.session.flush()

            payback1 = sync_entry_payback(txn1.id, user.id)
            payback2 = sync_entry_payback(txn2.id, user.id)

            assert payback1.id != payback2.id
            assert payback1.estimated_amount == Decimal("100.00")
            assert payback2.estimated_amount == Decimal("200.00")
            assert payback1.credit_payback_for_id == txn1.id
            assert payback2.credit_payback_for_id == txn2.id

    def test_three_entries_sum_to_exact_hundred(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """$33.33 + $33.33 + $33.34 sums to exactly $100.00.

        Verifies Decimal arithmetic avoids float rounding errors.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            for amt in ["33.33", "33.33", "33.34"]:
                e = TransactionEntry(
                    **figure_source_columns(),
                    transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                    user_id=user.id,
                    amount=Decimal(amt),
                    description="Split",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                )
                db.session.add(e)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback.estimated_amount == Decimal("100.00")

    def test_single_penny_entry(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Single $0.01 credit entry produces a $0.01 payback.

        The smallest possible amount must produce a valid payback.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=Decimal("0.01"),
                description="Penny",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(e)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            assert payback.estimated_amount == Decimal("0.01")


class TestEntryLinkIntegrity:
    """Verify credit_payback_id links are maintained correctly."""

    def test_create_links_all_credit_entries(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """After CREATE: all credit entries have credit_payback_id set.

        Three entries created before sync should all be linked after sync.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = []
            for i, amt in enumerate(["30.00", "40.00", "30.00"]):
                e = TransactionEntry(
                    **figure_source_columns(),
                    transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                    user_id=user.id,
                    amount=Decimal(amt),
                    description=f"Store {i}",
                    purchased_on=date(2026, 1, 5),
                    is_credit=True,
                )
                db.session.add(e)
                entries.append(e)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            for e in entries:
                assert e.credit_payback_id == payback.id

    def test_update_links_new_credit_entry(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """After UPDATE: a newly added credit entry also gets linked.

        The second credit entry, added after the payback already exists,
        must be linked on the next sync.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="First",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(e1)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert e1.credit_payback_id == payback.id

            e2 = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=Decimal("50.00"),
                description="Second",
                purchased_on=date(2026, 1, 6),
                is_credit=True,
            )
            db.session.add(e2)
            db.session.flush()

            sync_entry_payback(txn.id, user.id)

            assert e2.credit_payback_id == payback.id

    def test_delete_path_clears_entry_links(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """DELETE path clears credit_payback_id before removing payback.

        When an entry is toggled from credit to debit (making total == 0),
        the DELETE path must clear the stale credit_payback_id and delete
        the payback.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="Toggle",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert entry.credit_payback_id == payback.id
            payback_id = payback.id

            # Toggle to debit -- total_credit becomes 0, triggers DELETE.
            entry.is_credit = False
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            assert db.session.get(Transaction, payback_id) is None
            assert entry.credit_payback_id is None

    def test_debit_entries_never_get_payback_link(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Debit entries NEVER get credit_payback_id set.

        Even when a payback exists for credit entries on the same
        transaction, debit entries must remain unlinked.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            debit = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=Decimal("200.00"),
                description="Debit",
                purchased_on=date(2026, 1, 5),
                is_credit=False,
            )
            credit = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=Decimal("100.00"),
                description="Credit",
                purchased_on=date(2026, 1, 5),
                is_credit=True,
            )
            db.session.add_all([debit, credit])
            db.session.flush()

            sync_entry_payback(txn.id, user.id)

            assert debit.credit_payback_id is None
            assert credit.credit_payback_id is not None

    def test_toggle_to_debit_clears_stale_link(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Toggling one entry to debit clears its stale credit_payback_id.

        When two credit entries exist and one is toggled to debit, the
        UPDATE path must clear the toggled entry's stale link while
        keeping the remaining credit entry linked.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=user.id,
                amount=Decimal("100.00"), description="A",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            e2 = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=user.id,
                amount=Decimal("50.00"), description="B",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            db.session.add_all([e1, e2])
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert e1.credit_payback_id == payback.id
            assert e2.credit_payback_id == payback.id

            e1.is_credit = False
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)

            # Payback updated to e2's amount only.
            assert payback.estimated_amount == Decimal("50.00")
            # e1's stale link was cleared.
            assert e1.credit_payback_id is None
            # e2 still linked.
            assert e2.credit_payback_id == payback.id


class TestPaybackLifecycle:
    """Full lifecycle tests through multiple entry mutations."""

    def test_full_lifecycle(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Create 3 credit entries -> update one -> delete one -> toggle one.

        Verifies the payback amount at each step of a realistic sequence
        of entry mutations.

        Step amounts:
          Start: $100 + $50 + $75 = $225
          After update e1 $100 -> $120: $120 + $50 + $75 = $245
          After delete e2: $120 + $75 = $195
          After toggle e3 to debit: $120
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=user.id,
                amount=Decimal("100.00"), description="A",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            e2 = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=user.id,
                amount=Decimal("50.00"), description="B",
                purchased_on=date(2026, 1, 6), is_credit=True,
            )
            e3 = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=user.id,
                amount=Decimal("75.00"), description="C",
                purchased_on=date(2026, 1, 7), is_credit=True,
            )
            db.session.add_all([e1, e2, e3])
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            assert payback.estimated_amount == Decimal("225.00")

            # Update e1 from $100 to $120.
            e1.amount = Decimal("120.00")
            db.session.flush()
            payback = sync_entry_payback(txn.id, user.id)
            assert payback.estimated_amount == Decimal("245.00")

            # Delete e2 ($50).
            db.session.delete(e2)
            db.session.flush()
            payback = sync_entry_payback(txn.id, user.id)
            assert payback.estimated_amount == Decimal("195.00")

            # Toggle e3 to debit.
            e3.is_credit = False
            db.session.flush()
            payback = sync_entry_payback(txn.id, user.id)
            assert payback.estimated_amount == Decimal("120.00")

    def test_credit_then_toggle_to_debit_deletes_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Create a credit entry then toggle it to debit -- payback is deleted.

        After the toggle, no credit entries remain, so the payback must
        be removed entirely.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=user.id,
                amount=Decimal("100.00"), description="Toggle",
                purchased_on=date(2026, 1, 5), is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = sync_entry_payback(txn.id, user.id)
            payback_id = payback.id

            entry.is_credit = False
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            assert db.session.get(Transaction, payback_id) is None

    def test_all_debit_entries_no_payback_ever_created(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """All debit entries from the start -- no payback is ever created.

        Verifies the no-op cell: total_credit == 0 AND no payback exists.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            for i in range(3):
                e = TransactionEntry(
                    **figure_source_columns(),
                    transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=user.id,
                    amount=Decimal("50.00"), description=f"Debit {i}",
                    purchased_on=date(2026, 1, 5), is_credit=False,
                )
                db.session.add(e)
            db.session.flush()

            result = sync_entry_payback(txn.id, user.id)

            assert result is None
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is None


class TestLegacyCreditGuard:
    """Guard tests for the row-level Credit press on tracked transactions."""

    def test_legacy_credit_blocked_on_tracked(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """4.9: Legacy mark_as_credit raises ValidationError on tracked transactions.

        Entry-capable transactions must use entry-level credit, not the
        legacy Credit status.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]

            with pytest.raises(ValidationError, match="individual purchase tracking"):
                transaction_service.mark_as_credit(txn)

    def test_legacy_credit_still_works_non_tracked(
        self, app, db, seed_user, seed_periods,
    ):
        """4.10: The Credit press still works on non-tracked transactions.

        Regression: the guard must not affect transactions whose template
        does not have is_envelope enabled.  Works means on the card since plan
        task plan#2929 (rulings plan#2887 / plan#2888): the $100.00 moves on
        the card and no payback is written, where this graded the payback.
        """
        with app.app_context():
            card = add_card(seed_user)
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            txn = one_off_row_of(
                seed_periods[0],
                name="Non-Tracked Expense",
                amount=Decimal("100.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.flush()

            transaction_service.mark_as_credit(txn)
            db.session.flush()

            movement = status_seam.covering_movement_of(txn)
            assert (movement.account_id, movement.amount) == (card.id, Decimal("100.00"))
            assert credit_workflow.get_active_payback(txn.id) is None

    def test_legacy_unmark_credit_still_works(
        self, app, db, seed_user, seed_periods,
    ):
        """Legacy unmark_credit still works (regression).

        Verify that reverting a legacy credit transaction restores
        Projected status and deletes the payback.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )

            txn = one_off_row_of(
                seed_periods[0],
                name="Legacy Expense",
                amount=Decimal("100.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.flush()

            payback = plant_old_credit(txn)
            db.session.flush()
            payback_id = payback.id

            credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            assert txn.status.name == "Projected"
            assert db.session.get(Transaction, payback_id) is None


class TestEntryServiceHooks:
    """Integration tests through entry_service hooks.

    These tests verify that entry_service.update_entry and delete_entry
    automatically trigger sync_entry_payback, producing the correct payback
    state without explicit sync calls, and that create_entry no longer does:
    since plan task plan#2930 a ticked add goes ON the owner's card and makes
    no payback (ruling plan#2888), and the payback arms serve the old flagged
    lines, planted here as the door wrote them (``plant_old_flagged_purchase``).
    """

    def test_a_ticked_add_goes_on_the_card_and_makes_no_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """create_entry with the CC tick files the purchase ON the card, with no payback.

        Plan task plan#2930, ruling plan#2888: the card owes the $100.00 from
        the day it was bought, and checking pays it through the card's payoff
        row, so a CC Payback would pay it twice.  The flag stays unwritten.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            card = add_card(seed_user)
            db.session.commit()

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Credit purchase",
                    purchased_on=date(2026, 1, 5),
                    on_card=True,
                ),
            )

            assert entry.account_id == card.id
            assert entry.is_credit is False
            assert entry.credit_payback_id is None
            assert (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            ) is None

    def test_create_debit_entry_no_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """create_entry with is_credit=False creates no payback."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Debit purchase",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is None

    def test_update_entry_toggle_credit_creates_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """update_entry toggling is_credit to True creates a payback."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Toggle test",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            entry_service.update_entry(entry.id, user.id, is_credit=True)

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert payback.estimated_amount == Decimal("100.00")

    def test_update_entry_toggle_credit_deletes_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """update_entry toggling is_credit to False deletes the payback."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Toggle test",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None

            entry_service.update_entry(entry.id, user.id, is_credit=False)

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is None

    def test_delete_credit_entry_updates_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """delete_entry on a credit entry updates the payback amount.

        After deleting one of two credit entries, the payback amount
        should reflect only the remaining entry.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="First",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("50.00")),
                    description="Second",
                    purchased_on=date(2026, 1, 6),
                ),
            )

            # Payback should be $150 (100 + 50).
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback.estimated_amount == Decimal("150.00")

            entry_service.delete_entry(e1.id, user.id)

            db.session.expire(payback)
            assert payback.estimated_amount == Decimal("50.00")

    def test_delete_last_credit_entry_deletes_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """delete_entry on the last credit entry deletes the payback."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Only",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            entry_service.delete_entry(entry.id, user.id)

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is None

    def test_update_entry_amount_updates_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """update_entry changing amount on credit entry updates payback.

        Verifies the hook fires for amount changes, not just is_credit
        toggles.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Amount change",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback.estimated_amount == Decimal("100.00")

            entry_service.update_entry(entry.id, user.id, figure=typed(Decimal("75.00")))

            db.session.expire(payback)
            assert payback.estimated_amount == Decimal("75.00")

    def test_a_companions_tick_files_on_the_owners_card(
        self, app, db, seed_user, seed_companion,
        seed_periods, seed_entry_template,
    ):
        """A companion's CC tick files the purchase on the OWNER's card, with no payback.

        Plan task plan#2930 (6 of the 26 old ticks were a companion's, the
        trace's review count): the card is the row OWNER's one card
        (``movement_account.the_card`` reads ``txn.user_id``, ruling
        **R-CC11**), and the companion, who owns no account, is the
        purchase's author.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            companion = seed_companion["user"]
            card = add_card(seed_user)
            db.session.commit()

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=companion.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Companion purchase",
                    purchased_on=date(2026, 1, 5),
                    on_card=True,
                ),
            )

            assert entry.account_id == card.id
            assert entry.owner_id == seed_user["user"].id
            assert entry.user_id == companion.id
            assert (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            ) is None

    def test_a_companions_old_flagged_line_has_its_payback_under_the_owner(
        self, app, db, seed_user, seed_companion,
        seed_periods, seed_entry_template,
    ):
        """An old flagged line a companion recorded has its payback under owner's data.

        The companion's user_id is resolved to the owner's user_id, so
        the CC Payback category is created for the owner, not the companion.
        The line is planted as the add door wrote it until plan task
        plan#2930 (6 of the 26 old flagged lines are a companion's).
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            companion = seed_companion["user"]

            plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=companion.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Companion purchase",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert payback.estimated_amount == Decimal("100.00")

            # Category belongs to the owner, not the companion.
            cat = db.session.get(Category, payback.category_id)
            assert cat.user_id == seed_user["user"].id


class TestSessionState:
    """Verify SQLAlchemy session state between flush and sync."""

    def test_create_entry_flush_visible_to_sync(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """After create_entry flushes, sync sees the new entry.

        Verifies that the SQLAlchemy session state is correct between
        the entry flush in create_entry and the entries read in
        sync_entry_payback, on an old flagged line planted as the door wrote
        it until plan task plan#2930 (the add door syncs no payback since).
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Session test",
                    purchased_on=date(2026, 1, 5),
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback is not None
            assert payback.estimated_amount == Decimal("100.00")

            # Entry should be linked by sync.
            db.session.refresh(entry)
            assert entry.credit_payback_id == payback.id

    def test_delete_entry_flush_invisible_to_sync(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """After delete_entry flushes, sync does not see the deleted entry.

        Verifies that the deleted entry is excluded from the credit sum
        and payback amount.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            e1 = plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="First",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            plant_old_flagged_purchase(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("50.00")),
                    description="Second",
                    purchased_on=date(2026, 1, 6),
                ),
            )

            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .first()
            )
            assert payback.estimated_amount == Decimal("150.00")

            # Delete first entry ($100) -- only second ($50) remains.
            entry_service.delete_entry(e1.id, user.id)

            db.session.expire(payback)
            assert payback.estimated_amount == Decimal("50.00")


class TestASettledPaybackCannotBeReDerived:
    """A settled payback records what moved; a later purchase cannot rewrite it.

    **The control for a regression plan step X-au-c3 introduced and this guard
    closes.**  ``sync_entry_payback`` rewrites the payback's
    ``estimated_amount`` from the source's credit entries.  While a settled
    row's value was ``COALESCE(actual_amount, estimated_amount)`` that write
    moved the money, because an uncorrected settled payback carried a NULL
    actual and fell through to its plan.  A settled row is now worth what it
    RECORDED, so the same write became inert -- and the liability the user had
    just added left the projection with nothing booking it.

    Measured on the shape below before the guard existed: a payback settled at
    ``$100.00``, a later ``$50.00`` card purchase, ``estimated_amount`` moved to
    ``$150.00``, and every balance went on reading ``$100.00``.
    """

    def test_a_later_card_purchase_is_refused_not_silently_dropped(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The $50 that used to disappear is a designed 400 instead."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            plant_old_flagged_purchase(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")), description="Card buy",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            payback = sync_entry_payback(txn.id, user.id)
            db.session.flush()

            transaction_service.settle_transaction(payback)
            # COMMITTED, so the rollback below undoes only the refused act and
            # not the setup -- otherwise the payback the assertion re-reads was
            # never persisted and the test fails on its own fixture.
            db.session.commit()
            payback_id = payback.id
            assert settled_figure(payback) == Decimal("100.00")

            # The second card purchase would take the payback to $150.00.
            with pytest.raises(ValidationError, match="has settled at"):
                plant_old_flagged_purchase(
                    transaction_id=txn.id, user_id=user.id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")), description="Later card buy",
                        purchased_on=date(2026, 1, 9),
                    ),
                )

            # Nothing moved: the record stands and the plan was not rewritten.
            db.session.rollback()
            reloaded = db.session.get(Transaction, payback_id)
            assert settled_figure(reloaded) == Decimal("100.00")
            assert reloaded.estimated_amount == Decimal("100.00")

    def test_a_sync_that_changes_NOTHING_is_still_allowed(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The guard compares against what the payback RECORDED, not its plan.

        Without this case the refusal could be written as "any sync touching a
        settled payback", which would fail every re-sync the source triggers for
        an unrelated reason -- a description edit, a debit entry added beside the
        credit one -- none of which changes what the payback owes.

        **Its act is a description edit of the card purchase since plan task
        plan#2930**: it was a debit purchase ADDED, and the add door syncs no
        payback since, so the guard was no longer reached (the task's review,
        L3, measured the case passing with the guard mutated).  The edit door
        still syncs, over a write that leaves the credit total as it was.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            card_buy = plant_old_flagged_purchase(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")), description="Card buy",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            payback = sync_entry_payback(txn.id, user.id)
            db.session.flush()
            transaction_service.settle_transaction(payback)
            db.session.flush()

            # A description edit does not change the credit total.
            entry_service.update_entry(
                card_buy.id, user.id, description="Card buy, groceries",
            )
            assert settled_figure(payback) == Decimal("100.00")

    def test_removing_the_last_card_purchase_cannot_DELETE_a_settled_payback(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The DELETE arm's twin of the refusal above, and the larger harm.

        **The guard above shipped without this one, and two independent
        adversarial reviews found the asymmetry** (2026-08-17): the same
        function refused to RE-DERIVE a payback whose money had moved while
        going on to DESTROY one outright the moment the last credit purchase
        was removed.  Deleting a settled row erases a record of money that left
        the account -- strictly worse than re-pricing it -- and it happened with
        no refusal and an INFO log line as the only trace.

        The source row stays PROJECTED throughout, which is what makes this
        reachable: ``_reject_settled_parent`` guards the SOURCE's entries and
        has nothing to say about the payback's own status.

        Shown to FIRE: replacing the refusal with ``pass`` deletes the payback
        and this reads ``None``.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = plant_old_flagged_purchase(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")), description="Card buy",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            payback = sync_entry_payback(txn.id, user.id)
            db.session.flush()
            transaction_service.settle_transaction(payback)
            db.session.commit()
            payback_id, entry_id = payback.id, entry.id
            assert settled_figure(payback) == Decimal("100.00")

            with pytest.raises(ValidationError, match="cannot be removed"):
                entry_service.delete_entry(entry_id, user.id)

            db.session.rollback()
            reloaded = db.session.get(Transaction, payback_id)
            assert reloaded is not None, (
                "a SETTLED payback was hard-deleted: $100.00 that had already "
                "left the account, erased with no refusal"
            )
            assert settled_figure(reloaded) == Decimal("100.00")


class TestASettledPaybackWithDRIFTStillAdmitsUnrelatedEdits:
    """Finding **N-323**: the guard's POLICY is right, its PREDICATE was wider.

    The refusal exists because a settled payback records what MOVED, so a later
    card purchase cannot be re-derived underneath it -- that stays.  What was
    wrong is the question it asked: it compared the recorded figure against the
    credit sum NOW and refused whenever they differed AT ALL, so a payback
    already carrying drift refused every subsequent edit on its envelope,
    including edits that cannot touch the credit sum.

    **Measured on the developer's production clone**: envelope 2275's payback
    2457 recorded ``$50.80`` against ``$49.52`` of credit entries, and envelope
    2276's payback 2590 recorded ``$123.18`` against ``$181.58`` -- ``$59.68``
    of drift no screen reports.  Six debit purchases sit under those two
    envelopes, and **5 of 124 statement proposals worth `$706.35` could not be
    accepted at all**, because accepting one stamps a debit purchase's bank
    posting day and that ran into a guard about credit.
    """

    def _drifted(self, txn, user):
        """Return a payback that RECORDED less than its envelope's card spend.

        **Built through a real door rather than by writing the column**, so the
        state under test is one the app can actually reach: the reconcile
        panel offers a payback as CORRECTABLE (``is_correctable`` is ``not
        settles_from_entries``, and a payback holds no entries of its own), so
        a tick may book a figure of the user's rather than the plan.  Settling
        ``$100.00`` against ``$150.00`` of credit purchases is exactly the
        shape production carries -- payback 2590 recorded ``$123.18`` against
        ``$181.58`` -- and it leaves ``$50.00`` of drift that no screen reports.

        Returns:
            ``(payback, entry)`` -- the drifted payback and its credit purchase.
        """
        entry = plant_old_flagged_purchase(
            transaction_id=txn.id, user_id=user.id,
            details=entry_service.EntryDetails(
                figure=typed(Decimal("150.00")), description="Card buy",
                purchased_on=date(2026, 1, 5),
            ),
        )
        payback = sync_entry_payback(txn.id, user.id)
        db.session.flush()
        transaction_service.settle_transaction(
            payback, submitted=typed(Decimal("100.00")),
        )
        db.session.flush()
        # The drift itself, asserted so the cases below cannot silently grade
        # a payback whose record and card spend agree -- against which the
        # old predicate and the new one are the same guard.
        assert settled_figure(payback) == Decimal("100.00")
        # Queried rather than read off ``txn.entries``: ``sync_entry_payback``
        # expires that collection, so the relationship is stale here and a
        # stale read would assert 0 and pass for the wrong reason.
        assert db.session.query(
            db.func.coalesce(db.func.sum(TransactionEntry.amount), 0),
        ).filter(
            TransactionEntry.transaction_id == txn.id,
            TransactionEntry.is_credit.is_(True),
        ).scalar() == Decimal("150.00")
        return payback, entry

    def test_a_debit_purchases_posting_day_can_still_be_stamped(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The case N-323 measured, worth `$706.35` of blocked acceptances.

        Stamping ``settled_on`` on a DEBIT purchase changes no credit entry, so
        the credit sum is identical before and after -- and the guard has
        nothing to say about it.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            payback, _ = self._drifted(txn, user)

            debit = entry_service.create_entry(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("25.00")), description="Cash buy",
                    purchased_on=date(2026, 1, 9),
                ),
            )
            # Drift, created after the settle exactly as production's was.
            entry_service.update_entry(
                debit.id, user.id, figure=typed(Decimal("30.00")),
            )
            db.session.flush()

            # THE ACT: record the day the bank took that debit purchase.
            entry_service.update_entry(
                debit.id, user.id, settle_day=an_entered_day(date(2026, 1, 11)),
            )
            db.session.flush()

            assert db.session.get(
                TransactionEntry, debit.id,
            ).settled_on == date(2026, 1, 11)
            # And the settled payback is untouched: its record is its record.
            assert settled_figure(payback) == Decimal("100.00")

    def test_a_write_that_DOES_move_the_credit_total_is_still_refused(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The policy half, unchanged -- narrowing is not deleting.

        Without this the case above would pass for a guard that had simply been
        removed, which is the outcome a first draft of X-au-i actually reached
        before a test named for the policy caught it.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            payback, _ = self._drifted(txn, user)

            with pytest.raises(ValidationError) as exc:
                plant_old_flagged_purchase(
                    transaction_id=txn.id, user_id=user.id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("50.00")), description="Second card buy",
                        purchased_on=date(2026, 1, 9),
                    ),
                )

            assert "has settled at" in str(exc.value)
            assert settled_figure(payback) == Decimal("100.00")

    def test_a_DEBIT_amount_edit_cannot_move_the_credit_total_either(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The case a field-NAME predicate still gets wrong.

        A first narrowing asked "was ``amount`` or ``is_credit`` submitted",
        reusing the constant that names what re-costs a ROW.  That still
        refuses this edit: ``amount`` was submitted, but on a DEBIT purchase,
        which is not in the credit sum at all.  The shipped predicate compares
        the entry's own CONTRIBUTION to that sum before and after, so it
        answers every combination rather than the two that were remembered.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            payback, _ = self._drifted(txn, user)

            debit = entry_service.create_entry(
                transaction_id=txn.id, user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("25.00")), description="Cash buy",
                    purchased_on=date(2026, 1, 9),
                ),
            )
            entry_service.update_entry(
                debit.id, user.id, figure=typed(Decimal("40.00")),
            )
            db.session.flush()

            assert db.session.get(
                TransactionEntry, debit.id,
            ).amount == Decimal("40.00")
            assert settled_figure(payback) == Decimal("100.00")
