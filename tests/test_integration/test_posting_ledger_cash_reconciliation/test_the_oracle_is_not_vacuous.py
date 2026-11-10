"""The cash oracle is not vacuous: the reconciliation catches each tamper of a stored figure."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from tests import _ledger_reads
from tests._ledger_reads import account_net, trial_balance
from tests._stored_ledger import (
    family_journal_filter,
    linked_ledger_account,
    stored_account_net,
    stored_trial_balance,
)
from tests._test_helpers import create_settled_cash_transaction

from ._helpers import (
    _opening_anchor,
    _independent_movement_effect,
    _assert_full_reconciliation,
)


# ---------------------------------------------------------------------------
# Adversarial: the oracle is not vacuous (it fails on a broken seed)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the reconciliation and trial-balance checks catch real breakage."""

    def test_per_account_reconciliation_catches_a_tampered_transaction(
        self, app, db, seed_user,
    ):
        """Tampering a settled expense's movement leaves the STORE behind the records.

        A reconciled $100 Groceries expense has Checking's stored ledger -100 and
        a source effect of -100.  Forcing its covering movement's amount to 999
        via raw SQL leaves the stored ledger at -100 but pushes the source effect
        to -999.  A stale stored figure is the store's own state: the worked-out
        read follows the record, so the linked comparison holds and the sweep's
        parity arm is what names the drift.  This goes with the store;
        :meth:`test_the_linked_sweep_catches_a_wrong_read` grades the linked
        comparison on the read.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]

            txn = create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            txn_id = txn.id
            # Reconciled (absolutely) before tampering.
            assert stored_account_net(
                checking.id, scenario_id,
            ) == _opening_anchor(checking.id) + _independent_movement_effect(
                checking.id, scenario_id,
            )

            # Tamper the MOVEMENT's figure -- the fold's and the oracle's one
            # input since plan step ``balance:X-bi-4a`` (ruling **R-BAL80**);
            # the row's ``settled_amount`` beside it is the stale cache
            # ``X-bi-4b`` deletes and moves nothing here.  (Through
            # ``X-bi-3e`` this tampered the row's recorded figure, plan step
            # X-au-c3's subject.)  Entries carry no balance trigger, so the
            # tamper commits.
            db.session.execute(_db.text(
                "UPDATE budget.transaction_entries SET amount = 999 "
                "WHERE transaction_id = :i AND covers_settlement"
            ), {"i": txn_id})
            db.session.commit()

            ledger = stored_account_net(checking.id, scenario_id)
            effect = _independent_movement_effect(checking.id, scenario_id)
            assert ledger == Decimal("900.00")  # ledger unchanged
            assert effect == Decimal("-999.00")  # transaction truth drifted
            # opening (1000) + effect (-999) != ledger (900): the drift shows.
            assert ledger != _opening_anchor(checking.id) + effect
            # Drive the REAL production-wide sweep helper (not just the inline
            # re-derivation above).  ``match`` pins the parity arm's message: the
            # read and the source agree after this tamper, and only the store
            # does not.
            with pytest.raises(AssertionError, match="parts from the stored one"):
                _assert_full_reconciliation(scenario_id)

    def test_the_linked_sweep_catches_a_wrong_read(
        self, app, db, seed_user, monkeypatch,
    ):
        """A read that loses every movement's lines fails the linked comparison.

        A $100 Groceries expense on the $1,000.00 Checking reconciles: ledger
        900.00 = opening 1000.00 + effect -100.00.  A read dropping every
        movement line answers ledger 1000.00, opening + effect still 900.00, so
        the sweep FAILS.  ``match`` pins the linked-account reconciliation
        message specifically, so a future edit that weakened THAT comparison but
        left another arm firing cannot keep this test green.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"), category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            _assert_full_reconciliation(scenario_id)

            real_read = _ledger_reads.worked_out_lines
            monkeypatch.setattr(_ledger_reads, "worked_out_lines", lambda *args: [
                line for line in real_read(*args) if line.movement_id is None
            ])

            assert account_net(
                db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            with pytest.raises(AssertionError, match="combined source effect"):
                _assert_full_reconciliation(scenario_id)

    def test_the_trial_balance_catches_a_wrong_read(
        self, app, db, seed_user, monkeypatch,
    ):
        """A read that loses one side of an expense pushes the trial balance off zero.

        A $100 Groceries expense books Checking -100.00 and Groceries +100.00.  A
        read dropping the category line leaves 0 - 100 = -100.00, so the
        trial-balance ``= 0`` assertion is a real check on the read.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"), category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            assert trial_balance(db.session) == Decimal("0.00")

            real_read = _ledger_reads.worked_out_lines
            monkeypatch.setattr(_ledger_reads, "worked_out_lines", lambda *args: [
                line for line in real_read(*args)
                if line.key.kind is not LedgerAccountKindEnum.CATEGORY
            ])

            assert trial_balance(db.session) == Decimal("-100.00")

    def test_stored_trial_balance_catches_an_injected_leg(self, app, db, seed_user):
        """Injecting one extra leg pushes the stored trial balance off zero.

        A balanced book has trial balance 0.00.  Inserting one unmatched +50 leg
        (raw SQL, flushed but never committed so the deferred per-entry trigger
        never fires) makes the whole-ledger sum 0 + 50 = 50.00 -- so the
        trial-balance ``= 0`` assertion is a real check, not one the per-entry
        trigger makes vacuously true.  Rolled back so the leg never lands.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            assert stored_trial_balance() == Decimal("0.00")

            # Inject one extra, unmatched leg onto the transaction's entry
            # (picked by its link -- the Step-5 openings mean several entries
            # exist).  Flush (not commit) makes it visible; the DEFERRED
            # balanced trigger validates only at COMMIT, which we never reach.
            entry_id = (
                _db.session.query(JournalEntry.id)
                .filter(family_journal_filter(txn))
                .scalar()
            )
            _db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id,
                "l": linked_ledger_account(
                    _db.session, seed_user["account"].id,
                ).id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
            })
            _db.session.flush()

            assert stored_trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert stored_trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            _db.session.rollback()
