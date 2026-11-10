"""The cash oracle is not vacuous: the reconciliation catches a read that loses a line."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.enums import LedgerAccountKindEnum
from tests import _ledger_reads
from tests._ledger_reads import account_net, trial_balance
from tests._test_helpers import create_settled_cash_transaction

from ._helpers import _assert_full_reconciliation


# ---------------------------------------------------------------------------
# Adversarial: the oracle is not vacuous (it fails on a broken seed)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the reconciliation and trial-balance checks catch real breakage."""

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
                seed_user, seed_user["bootstrap_period"],
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
                seed_user, seed_user["bootstrap_period"],
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
