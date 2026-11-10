"""The loan oracle is not vacuous: the reconciliation catches a wrong read, and the value checks a
wrong split.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.enums import LedgerAccountKindEnum
# The two families' kind tables are the chart's own, package-private since plan
# task plan#3155 deleted their public sets (the stored chart's writer filed the
# two under different columns); a family filter reads them by name so the fact
# keeps one spelling.
from app.services.ledger_lines._chart import _LOAN_KINDS
from app.utils.money import accrue_monthly_interest
from tests import _ledger_reads
from tests._ledger_reads import trial_balance

from ._helpers import (
    _P1,
    _AS_OF,
    _make_loan,
    _resolved_at,
    _settle,
    _per_loan_correction_net,
    _resolver_balance,
    _ledger_balance,
    _posted_balance,
    _assert_loan_reconciles,
)


# ---------------------------------------------------------------------------
# 6. Adversarial: the oracle is not vacuous (it fails on a broken seed)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the superseding invariant and trial balance catch real breakage."""

    def test_the_superseding_invariant_catches_a_wrong_read(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A read that loses a loan's component lines breaks the loan-aware invariant.

        A reconciled $1,000 payment reads linked -99,500.00 = income cash 1,000.00 -
        non-principal 100,500.00 (interest 500 + opening equity 100,000).  A read
        dropping every loan component line answers non-principal 0.00 with the
        linked net unmoved, and 1,000.00 - 0.00 is no longer -99,500.00, so the
        sweep FAILS.  ``match`` pins the SUPERSEDING invariant's message
        specifically, so a future edit that weakened THAT comparison but left some
        other assertion firing cannot keep this test green (L-1).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()
            _assert_loan_reconciles(loan, scenario_id)
            assert _per_loan_correction_net(
                loan.id, scenario_id,
            ) == Decimal("100500.00")

            real_read = _ledger_reads.worked_out_lines
            monkeypatch.setattr(_ledger_reads, "worked_out_lines", lambda *args: [
                line for line in real_read(*args) if line.key.kind not in _LOAN_KINDS
            ])

            assert _per_loan_correction_net(
                loan.id, scenario_id,
            ) == Decimal("0.00")
            with pytest.raises(AssertionError, match="non-principal corrections"):
                _assert_loan_reconciles(loan, scenario_id)

    def test_the_trial_balance_catches_a_wrong_read(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A read that loses a payment's interest line pushes the trial balance off zero.

        A $1,000 payment on the $100,000 balance at 6% splits $500.00 onto the
        loan's interest line (100,000 x 0.06 / 12).  A read dropping that line
        leaves 0 - 500 = -500.00, so the trial-balance ``= 0`` assertion is a real
        check on the read.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()
            assert trial_balance(db.session) == Decimal("0.00")

            real_read = _ledger_reads.worked_out_lines
            monkeypatch.setattr(_ledger_reads, "worked_out_lines", lambda *args: [
                line for line in real_read(*args)
                if line.key.kind is not LedgerAccountKindEnum.LOAN_INTEREST
            ])

            assert trial_balance(db.session) == Decimal("-500.00")

    def test_walk_interest_injection_fails_the_value_checks_not_the_sweep(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A +$10 interest bug in the walk fails the parallel run, survives the sweep.

        The executable form of the review's "+$10 interest injection failed 9 of
        11 tests" evidence (previously only prose in commit messages and
        docstrings, so a later edit that weakened a value assertion lost the
        oracle's teeth undetected).  Injecting $10 of phantom interest into the
        WALK's accrual -- and ONLY the walk; the resolver accrues through
        ``rate_period_engine``'s separate import, so it stays the honest reference
        -- makes every split book $10 too much interest and therefore $10 too
        little principal, so both ledger producers over-state the debt by exactly
        $10.  This proves two things at once, mirroring the manual experiment:

        * the parallel-run VALUE assertions (``ledger == resolver`` and
          ``reader == resolver``) -- the ones that actually PIN the split -- now
          FAIL, so they have teeth and are not passing unconditionally; but
        * the STRUCTURAL sweep ``_assert_loan_reconciles`` STILL PASSES, because
          it is an accounting identity -- the correction's loan leg and the
          per-loan leg both shift by the same $10, so ``linked == income -
          per_loan`` holds no matter what interest the split booked.  This is
          exactly why the injection failed the value tests but not the
          invariant-only ones -- now a CI-run negative control, not a claim.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            # The honest scheduled P&I, read BEFORE injecting: paying exactly it
            # is on-schedule, so absent the bug ledger == resolver to the penny.
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            # Inject $10 of phantom interest into the WALK's accrual only.  The
            # module binding ``_replay.accrue_monthly_interest`` is patched -- it
            # was ``_split``'s until plan step X-au-g-2c-3b-2 moved the accrual
            # onto the shared replay, and only the BINDING moved; the resolver's
            # ``rate_period_engine.accrue_monthly_interest`` is a DISTINCT import
            # and stays honest, so the two diverge by exactly $10.
            monkeypatch.setattr(
                "app.services.loan_ledger._replay"
                ".accrue_monthly_interest",
                lambda balance, rate: (
                    accrue_monthly_interest(balance, rate) + Decimal("10.00")
                ),
            )

            # Settle on-schedule WITH the bug active: the split posts $10 too much
            # interest -> $10 too little principal -> the ledger over-states debt.
            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            ledger = _ledger_balance(loan.id, scenario_id)
            reader = _posted_balance(loan.id, scenario_id)
            # Both ledger producers over-state the debt by exactly the phantom $10.
            assert ledger == resolver + Decimal("10.00")
            assert reader == resolver + Decimal("10.00")

            # The value checks that PIN the split now fail -- proving their teeth.
            with pytest.raises(AssertionError):
                assert ledger == resolver
            with pytest.raises(AssertionError):
                assert reader == resolver

            # ...yet the structural sweep is (by design) blind to the split value
            # and STILL reconciles: per_loan and the loan leg both shifted by $10.
            _assert_loan_reconciles(loan, scenario_id)
