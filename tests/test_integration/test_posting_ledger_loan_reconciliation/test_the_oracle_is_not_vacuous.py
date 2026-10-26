"""The loan oracle is not vacuous: the reconciliation catches each tamper of a stored figure."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingKindEnum
from app.extensions import db as _db
from app.utils.money import accrue_monthly_interest
from tests._test_helpers import find_loan_ledger_account, loan_correction_entries

from ._helpers import (
    _P1,
    _AS_OF,
    _make_loan,
    _resolved_at,
    _settle,
    _independent_loan_linked_net,
    _per_loan_correction_net,
    _independent_settled_income_cash,
    _trial_balance,
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

    def test_superseding_invariant_catches_a_tampered_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Tampering a payment's actual cash breaks the loan-aware invariant.

        A reconciled $1,000 payment has linked net +500, income cash +1,000, and
        non-principal corrections +500, so ``linked == income - non_principal``
        holds.  Forcing the TRANSFER's settled figure -- the cash its loan
        side's record moved, stored once on the transfer since plan step
        balance:X-bi-6-4d-2 (its 4d-4 half; the record carried it before, and
        the income shadow before that, ruling R-BAL167 class 4) -- to 9,999
        via raw SQL (no re-sync) pushes the income cash to
        +9,999 while the posted ledger is
        unchanged -- so ``income - non_principal`` becomes 9,499, no longer the
        +500 linked net.  The superseding invariant the sweep relies on now FAILS,
        proving it is a real comparison, not one that passes unconditionally.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            linked = _independent_loan_linked_net(loan.id, scenario_id)
            non_principal = _per_loan_correction_net(loan.id, scenario_id)
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
            assert _independent_loan_linked_net(
                loan.id, scenario_id,
            ) == linked  # ledger unchanged
            # The invariant the sweep checks now fails -- the drift is caught.
            assert linked != tampered_income - non_principal
            # Drive the REAL production-wide sweep helper (not just the inline
            # re-derivation above) so a regression that broke the helper itself --
            # e.g. one that stopped comparing the income cash -- would fail here.
            # ``match`` pins the SUPERSEDING invariant's message specifically, so a
            # future edit that weakened THAT comparison but left some other assertion
            # (a non-empty guard, the trial balance) firing under tamper no longer
            # keeps this test green -- the tooth cannot be lost undetected (L-1).
            with pytest.raises(AssertionError, match="non-principal corrections"):
                _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_trial_balance_catches_an_injected_leg(
        self, app, db, seed_user, seed_periods,
    ):
        """Injecting one extra leg on a correction pushes the trial balance off zero.

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
            assert _trial_balance() == Decimal("0.00")

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

            assert _trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert _trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            db.session.rollback()

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
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)
