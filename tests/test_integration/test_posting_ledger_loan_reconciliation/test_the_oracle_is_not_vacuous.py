"""The loan oracle is not vacuous: the reconciliation catches each tamper of a stored figure."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum
from app.extensions import db as _db
from app.services.ledger_lines import LOAN_KINDS
from app.utils.money import accrue_monthly_interest
from tests import _ledger_reads
from tests._ledger_reads import trial_balance
from tests._stored_ledger import (
    find_loan_ledger_account,
    loan_correction_entries,
    stored_account_net,
    stored_per_loan_net,
    stored_trial_balance,
)

from ._helpers import (
    _P1,
    _SCHEDULED_PI,
    _ONE_SCHEDULED_MONTH,
    _make_loan,
    _settle,
    _per_loan_correction_net,
    _independent_settled_income_cash,
    _ledger_balance,
    _posted_balance,
    _assert_loan_reconciles,
)


# ---------------------------------------------------------------------------
# 6. Adversarial: the oracle is not vacuous (it fails on a broken seed)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the superseding invariant and trial balance catch real breakage."""

    def test_superseding_invariant_catches_a_tampered_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Tampering a payment's actual cash leaves the STORE behind the records.

        A reconciled $1,000 payment has a stored linked net of -99,500.00
        (opening -250,000 + true-up +150,000 + principal +500), income cash
        +1,000 and non-principal 100,500.00 (interest 500 + opening equity
        100,000), so ``linked == income - non_principal`` holds.  Forcing the
        TRANSFER's settled figure -- the cash its loan side's record moved,
        stored once on the transfer since plan step balance:X-bi-6-4d-2 (its
        4d-4 half; the record carried it before, and the income shadow before
        that, ruling R-BAL167 class 4) -- to 9,999 via raw SQL (no re-sync)
        pushes the income cash to +9,999 while the stored ledger is unchanged --
        so ``income - non_principal`` becomes -90,501.00, no longer the -99,500.00
        linked net.  A stale stored figure is the store's own state: the
        worked-out read follows the record, so the sweep's arm (a), the store
        against the read, is what names the drift.  This goes with the store;
        :meth:`test_the_superseding_invariant_catches_a_wrong_read` grades the
        invariant on the read.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            linked = stored_account_net(loan.id, scenario_id)
            non_principal = stored_per_loan_net(loan.id, scenario_id)
            income = _independent_settled_income_cash(loan.id, scenario_id)
            # Reconciled before tampering.
            assert linked == income - non_principal

            # Tamper the RECORDED cash (a settled transfer holding two dated
            # records and a figure above zero is a state the commit grade
            # admits, so this commits); the posted ledger is left untouched.
            # It is the recorded figure and not the plan since plan step
            # X-au-c3: a settled row's cash is what it recorded as having
            # moved.  The loan side's record hangs off the TRANSFER by its
            # to-side link, not off the income shadow (ruling R-BAL167 class
            # 4, plan step balance:X-bi-6-4d-2), and the figure it moved is
            # stored once on the transfer since that step's 4d-4 half.
            db.session.execute(_db.text(
                "UPDATE budget.transfers SET settled_figure = 9999 "
                "WHERE id = :t"
            ), {"t": xfer.id})
            db.session.commit()

            tampered_income = _independent_settled_income_cash(
                loan.id, scenario_id,
            )
            assert tampered_income == Decimal("9999.00")  # source drifted
            assert stored_account_net(
                loan.id, scenario_id,
            ) == linked  # stored ledger unchanged
            # The stored invariant now fails -- the drift shows.
            assert linked != tampered_income - non_principal
            # Drive the REAL production-wide sweep helper (not just the inline
            # re-derivation above).  ``match`` pins arm (a)'s message: the read
            # follows the tampered record, and only the store does not.
            with pytest.raises(AssertionError, match="account_posting_total"):
                _assert_loan_reconciles(loan, scenario_id)

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
                line for line in real_read(*args) if line.key.kind not in LOAN_KINDS
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

    def test_stored_trial_balance_catches_an_injected_leg(
        self, app, db, seed_user, seed_periods,
    ):
        """Injecting one extra leg on a correction pushes the stored trial balance off zero.

        A balanced book has trial balance 0.00.  Inserting one unmatched +50 leg
        onto the correction entry (raw SQL, flushed but never committed so the
        DEFERRED per-entry balanced trigger -- which validates only at COMMIT --
        never fires) makes the whole-ledger sum 0 + 50 = 50.00, so the
        trial-balance ``= 0`` assertion is a real check, not one the trigger makes
        vacuously true.  Rolled back so the leg never lands.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()
            assert stored_trial_balance() == Decimal("0.00")

            # Inject one extra, unmatched leg onto the correction entry, on the
            # loan's interest ledger.  Flush (not commit) makes it visible; the
            # deferred balanced trigger validates only at COMMIT, never reached.
            correction = loan_correction_entries(db.session, xfer.id, loan.id)[0]
            interest_ledger = find_loan_ledger_account(
                db.session, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": correction.id,
                "l": interest_ledger.id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            })
            db.session.flush()

            assert stored_trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert stored_trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            db.session.rollback()

    def test_walk_interest_injection_fails_the_value_checks_not_the_sweep(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A +$10 interest bug in the walk fails the value checks, survives the sweep.

        The executable form of the review's "+$10 interest injection failed 9 of
        11 tests" evidence (previously only prose in commit messages and
        docstrings, so a later edit that weakened a value assertion lost the
        oracle's teeth undetected).  Injecting $10 of phantom interest into the
        WALK's accrual makes every split book $10 too much interest and therefore
        $10 too little principal, so both ledger producers over-state the debt by
        exactly $10 against the hand-worked figure.  The honest reference is that
        literal, 99,001.12 (:data:`_SCHEDULED_PI`: interest 500.00, principal
        998.88); it was the old schedule replay until plan task plan#3384 deleted
        it with walk 3 (ruling plan#3383).  This proves two things at once,
        mirroring the manual experiment:

        * the VALUE assertions (``ledger == 99,001.12`` and ``reader ==
          99,001.12``) -- the ones that actually PIN the split -- now FAIL, so
          they have teeth and are not passing unconditionally; but
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

            # Inject $10 of phantom interest into the WALK's accrual.  The
            # module binding ``_replay.accrue_monthly_interest`` is patched -- it
            # was ``_split``'s until plan step X-au-g-2c-3b-2 moved the accrual
            # onto the shared replay, and only the BINDING moved.
            monkeypatch.setattr(
                "app.services.loan_ledger._replay"
                ".accrue_monthly_interest",
                lambda balance, rate: (
                    accrue_monthly_interest(balance, rate) + Decimal("10.00")
                ),
            )

            # Settle on-schedule WITH the bug active: the split posts $10 too much
            # interest -> $10 too little principal -> the ledger over-states debt.
            _settle(seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI)
            db.session.commit()

            ledger = _ledger_balance(loan.id, scenario_id)
            reader = _posted_balance(loan.id, scenario_id)
            # Both ledger producers over-state the debt by exactly the phantom
            # $10: 99001.12 + 10.00 = 99,011.12.
            assert ledger == Decimal("99011.12")
            assert reader == Decimal("99011.12")

            # The value checks that PIN the split now fail -- proving their teeth.
            with pytest.raises(AssertionError):
                assert ledger == _ONE_SCHEDULED_MONTH
            with pytest.raises(AssertionError):
                assert reader == _ONE_SCHEDULED_MONTH

            # ...yet the structural sweep is (by design) blind to the split value
            # and STILL reconciles: per_loan and the loan leg both shifted by $10.
            _assert_loan_reconciles(loan, scenario_id)
