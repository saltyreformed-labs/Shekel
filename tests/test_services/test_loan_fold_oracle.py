"""B2: the reference fold is the oracle, and it is exhaustive.

Plan step B2 (``docs/audits/balance_architecture/README.md``).  Parallel-runs
the loan fold (:func:`app.services.balance_at._fold.fold_loan_balances`) against
the sum of the worked-out ledger's lines
(:func:`tests._ledger_reads.posted_loan_balances_on`) on
**EVERY DAY** of each generated loan shape's domain -- the shape matrix step C3
must not move money across (plan Section 7.4).  The walk itself is
:func:`tests._ledger_reads.assert_fold_matches_postings_every_day`.

**The counterparty is the test suite's own window, since plan step E1e.**  The
production sum-of-postings readers were deleted there: the seam had folded away
every one of their ``app/`` callers, and grading the postings is the job this
file exists for, so the window belongs on this side of the line.  It sums the
lines by the rule ``-(sum where entry_date <= as_of)``, ``None`` for a loan with
no terms, and nothing can render it on a screen.

**What this proves, and what it does not.**  The fold reads the loan's SOURCE
events; the window sums the lines the read derives from the same walk.  So an
equality on every day grades how the read dates and signs those lines.  It says
nothing of the stored ledger, which it read until plan#2913 and does not now, and
it is NOT an independent proof of the split's VALUE.  That is pinned elsewhere: the
Step-4 reconciliation oracle's parallel run against the un-seeded resolver
(the ``test_posting_ledger_loan_reconciliation`` package), A2's hand-computed Taxes
oracle, and B1's hand-computed fold figures (``test_loan_ledger.py``).  Do not
read this file's equalities as the correctness proof.

**Sampling is forbidden.**  A 14-day sample once scored perfect while wrong by
$178,103.41 on 22% of days (plan Section 7.2), so every test walks EVERY day of
its domain, never a sample, and guards its loop so a vacuous domain cannot pass.

**N-11 (a raw transaction typed onto a loan) is closed by construction**, not
carried as an exception: the user paths that could create one -- the
transaction-create routes, the recurrence-template form, AND the salary-profile
account picker -- each refuse or exclude an amortizing account (ruling D4;
``_reject_transaction_on_loan``, the ``_validate_template_form`` gate, and the
salary picker's ``has_amortization`` filter), so no such row can enter the fold's
domain.  Were one forced in (a legacy row), the
fold would not see it while the reader would, diverging by its amount; the guard
is what keeps the every-day equality below complete.  See
:class:`TestRawLoanTransactionIsTheOnlyDivergence` and
``tests/test_routes/test_transaction_guards.py``.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.loan_features import RateHistory
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.services import loan_ledger, loan_loaders, loan_resolver
from app.services.balance_at._fold import fold_loan_balances
from app.services.rate_period_engine import period_for_date
from tests import _ledger_reads
from tests._ledger_reads import (
    assert_fold_matches_postings_every_day,
    posted_loan_balance_at,
)
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
    insert_tracking_start_event,
    last_covered_day,
)

# The controlled loan terms, shared with B1's hand-computed suite
# (``test_loan_ledger.py``) so the two read the same shape from two angles:
# $250,000 originated 2025-01-01 at 6%, trued up to $100,000 as of 2026-01-05.
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)
_ANCHOR_BALANCE = Decimal("100000.00")
_ANCHOR_DATE = date(2026, 1, 5)
_RATE = Decimal("0.06")

# A date before every event (origination included), so each domain also pins the
# pre-origination 0.00 -- truly "no debt" -- that the fold and reader must agree
# on.  The window between origination and a tracking-start is the honest plateau
# (finding B-11's zone, now the origination principal held flat), also walked.
_BEFORE_ALL = date(2024, 12, 31)


def _make_loan(seed_user, db, **kwargs):
    """Build the controlled trued-up loan (origination + a user true-up)."""
    return create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
        rate=_RATE, origination_date=_ORIGINATION_DATE, name="Oracle Loan",
        **kwargs,
    )


def _settle(seed_user, db, loan, period, amount=Decimal("1000.00")):
    """Settle a Checking -> loan payment transfer through the sole writer.

    Pins the settle day to the period's start (C2 keys visibility on the settled
    date), so the payment is visible from its period start -- the deterministic
    past date the every-day walk below values it from.
    """
    return create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, period,
        amount=amount, settled_on=period.start_date,
    )


class TestFoldMatchesPostingsAcrossTheShapeMatrix:
    """Every required shape (plan Section 7.4), every day: fold == the read's window.

    The fold walks the loan's anchors and settled-payment rows; the window sums
    the worked-out lines of that walk.  Agreement on EVERY day of the domain
    grades how the read dates and signs them.
    """

    def test_trued_up_loan_with_payments(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A2's paid shape: origination + true-up + several settled payments.

        The compounding-balance case B1 pins by hand; here it is walked day by
        day against the posted ledger over the whole pre-true-up year plus the
        payment window.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for period in (seed_periods[1], seed_periods[3], seed_periods[5]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            assert_fold_matches_postings_every_day(
                loan.id, seed_user["scenario"].id, _BEFORE_ALL, end,
                min_days=400,
            )

    def test_tracking_start_import(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A mid-life import: origination opening + a tracking-start assertion (C1).

        ``load_loan_anchor_facts`` opens at the 2025-01-01 origination and loads
        the tracking-start as an ordinary assertion, and the worked-out lines
        derive from the same walk -- so
        both producers open at ORIGINATION and read the $250,000 principal held
        FLAT across the pre-tracking window (the honest plateau that replaces the
        old false pre-opening zero, B-11), then reset at the tracking-start.  They
        must AGREE on the plateau and the reset, day by day.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, name="Import Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                loan_loaders.load_loan_params(loan.id),
                _ANCHOR_BALANCE, _ANCHOR_DATE,
            )
            db.session.commit()
            for period in (seed_periods[1], seed_periods[3]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            end = seed_periods[5].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            assert_fold_matches_postings_every_day(
                loan.id, scenario_id, _BEFORE_ALL, end, min_days=400,
            )
            # C1 realization (Section 7.4): pin the VALUES the equality alone
            # would miss if BOTH producers no-oped.  Before origination is 0.00
            # (no debt); a date between origination (2025-01-01) and the
            # tracking-start (2026-01-05) is the $250,000 origination principal
            # held FLAT -- the plateau, never the pre-C1 false 0.00.
            assert posted_loan_balance_at(
                loan.id, scenario_id, _BEFORE_ALL,
            ) == Decimal("0.00")
            assert posted_loan_balance_at(
                loan.id, scenario_id, date(2025, 6, 1),
            ) == _ORIGINATION_PRINCIPAL

    def test_arm_rate_step(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """An ARM recast: a rate change lifts the interest of later payments.

        The rate change (effective 2026-02-01) is appended BEFORE any payment
        settles, so the posted split of each post-change payment accrues at the
        new 9% and the fold resolves the same periods.  The period-1 payment
        (before the change) keeps 6%; period-3 / period-5 (after) take 9% -- the
        step reaches both producers, and the every-day walk proves they agree
        across it.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            db.session.add(RateHistory(
                account_id=loan.id, effective_date=date(2026, 2, 1),
                interest_rate=Decimal("0.09"),
            ))
            db.session.flush()
            for period in (seed_periods[1], seed_periods[3], seed_periods[5]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            # Prove the ARM step is LIVE (not a no-op both producers share): the
            # resolver both the fold and the posted split read accrues at 9%
            # after the change and 6% before it.
            rate_periods = loan_resolver.resolve_periods(
                loan_loaders.load_loan_params(loan.id),
                loan_loaders.load_rate_changes(loan.id),
            )
            assert period_for_date(
                rate_periods, date(2026, 2, 15),
            ).annual_rate == Decimal("0.09")
            assert period_for_date(
                rate_periods, date(2026, 1, 16),
            ).annual_rate == _RATE
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            assert_fold_matches_postings_every_day(
                loan.id, seed_user["scenario"].id, _BEFORE_ALL, end,
                min_days=400,
            )

    def test_escrow(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Each payment carries an escrow slice, and both producers split it out.

        A $3,600/yr escrow line ($300/mo) is active from origination, so each
        $1,000 payment divides interest + $300 escrow + principal.  The escrow
        leg posts to the loan's escrow ledger, NOT the linked (liability)
        ledger, so the balance the reader sums and the balance the fold walks
        both move by principal only -- and must agree every day.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db, escrow_annual=Decimal("3600.00"))
            for period in (seed_periods[1], seed_periods[3], seed_periods[5]):
                _settle(seed_user, db, loan, period)
            db.session.commit()
            # Prove escrow is REALIZED (not a no-op both producers share): the
            # fold's own per-payment split carries the $300/mo escrow slice.
            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert splits
            assert all(s.escrow == Decimal("300.00") for s in splits)
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            assert_fold_matches_postings_every_day(
                loan.id, seed_user["scenario"].id, _BEFORE_ALL, end,
                min_days=400,
            )

    def test_payoff_overpayment(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A payment overpays the balance; the surplus is a Refund, not principal.

        The loan is trued up to $1,000; a $1,500 payment drives principal to the
        $1,000 balance (paid off) and routes the ~$495 surplus to a Refund
        (Asset) leg -- NOT the linked liability ledger, so neither the reader's
        sum nor the fold's walk falls below 0.  A SECOND $1,500 payment after
        payoff is pure refund and moves neither producer off 0.  Every day of
        the payoff and post-payoff window must agree.
        """
        with app.app_context():
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_ORIGINATION_PRINCIPAL,
                anchor_balance=Decimal("1000.00"), anchor_date=_ANCHOR_DATE,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
                name="Payoff Loan",
            )
            _settle(
                seed_user, db, loan, seed_periods[1], amount=Decimal("1500.00"),
            )
            _settle(
                seed_user, db, loan, seed_periods[3], amount=Decimal("1500.00"),
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            # Prove the payoff is REALIZED (not a shared no-op): a split routes
            # surplus to a Refund (excess), and the fold actually reaches 0.
            splits = loan_ledger.compute_loan_payment_splits(loan.id, scenario_id)
            assert any(s.excess > Decimal("0.00") for s in splits)
            paid_off = seed_periods[2].start_date
            assert fold_loan_balances(
                loan.id, scenario_id, [paid_off],
            )[paid_off] == Decimal("0.00")
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            assert_fold_matches_postings_every_day(
                loan.id, seed_user["scenario"].id, _BEFORE_ALL, end,
                min_days=400,
            )

    def test_pre_anchor_payment(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A payment whose due date PRECEDES the latest anchor (read-switch boundary).

        The true-up is dated 2026-03-15; a payment budgeted to period 1 is due
        2026-02-01, BEFORE it.  The fold resets the balance at the true-up, so
        the pre-anchor payment's principal is subsumed by the true-up's
        ``owed_before`` -- and the posted ledger, summing the payment's split
        and the true-up correction, must land on the same balance every day,
        including the window between the payment's visibility and the true-up's.
        """
        with app.app_context():
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_ORIGINATION_PRINCIPAL,
                anchor_balance=_ANCHOR_BALANCE, anchor_date=date(2026, 3, 15),
                rate=_RATE, origination_date=_ORIGINATION_DATE,
                name="Pre-Anchor Loan",
            )
            _settle(
                seed_user, db, loan, seed_periods[1], amount=Decimal("2000.00"),
            )
            db.session.commit()
            # Prove the payment is genuinely PRE-anchor -- its installment
            # precedes the 2026-03-15 true-up, the boundary this shape names.
            params = loan_loaders.load_loan_params(loan.id)
            shadows = loan_loaders.settled_income_legs(
                loan.id, seed_user["scenario"].id, options=(),
            )
            assert loan_loaders.loan_payment_due_date(
                shadows[0], params.payment_day,
            ) < date(2026, 3, 15)
            end = seed_periods[6].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            assert_fold_matches_postings_every_day(
                loan.id, seed_user["scenario"].id, _BEFORE_ALL, end,
                min_days=400,
            )

    def test_payment_period_does_not_contain_its_due_date(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A payment whose cash period does NOT contain its installment date.

        ``payment_day`` is the 1st, so a payment budgeted to period 1
        (2026-01-16..01-29) satisfies the 2026-02-01 installment -- a date in
        period 2.  The fold dates the principal by the CASH period's start
        (visibility) and orders the split by the DUE date (contract),
        reproducing what the posted ledger does, so the two agree every day
        across the split.  (Ruling R-A / step C2 later moves visibility to the
        settled date; here both producers share today's period-start rule, so
        this pins the equality C2 must consciously break.)
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1])
            db.session.commit()
            params = loan_loaders.load_loan_params(loan.id)
            shadows = loan_loaders.settled_income_legs(
                loan.id, seed_user["scenario"].id, options=(),
            )
            due = loan_loaders.loan_payment_due_date(
                shadows[0], params.payment_day,
            )
            period = seed_periods[1]
            # The defining property of the shape, made explicit.
            assert not period.start_date <= due <= last_covered_day(period)
            end = seed_periods[4].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))
            assert_fold_matches_postings_every_day(
                loan.id, seed_user["scenario"].id, _BEFORE_ALL, end,
                min_days=400,
            )


class TestTheOracleHasTeeth:
    """The harness FAILS on a divergence -- it does not pass vacuously.

    An oracle that silently passed (compared a value to itself, walked no days)
    would give false assurance: the 14-day sample that scored perfect while
    wrong by $178,103.41 (plan Section 7.2).  This shows the every-day harness
    catches a forced $1.00 divergence on ONE day -- the negative control the whole
    matrix above rests on (plan Section 7.3).
    """

    def test_a_forced_divergence_makes_the_harness_fail(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Replace the posted window with one off by $1.00 on ONE day; it raises.

        One day, because a window off on every day fails any sample too, so it
        cannot tell a walk of every day from the sample this file forbids.  The
        197th day of the domain, a prime, so no stride from the domain's first day
        lands on it but a stride of 1 or of 197.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1])
            db.session.commit()
            end = seed_periods[3].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))

            real_window = _ledger_reads.posted_loan_balances_on
            wrong_day = _BEFORE_ALL + timedelta(days=197)

            def off_by_a_dollar(loan_id, scenario_id, days):
                read = real_window(loan_id, scenario_id, days)
                read[wrong_day] += Decimal("1.00")
                return read

            monkeypatch.setattr(
                _ledger_reads, "posted_loan_balances_on", off_by_a_dollar,
            )
            with pytest.raises(AssertionError, match="disagree on 1/"):
                assert_fold_matches_postings_every_day(
                    loan.id, seed_user["scenario"].id, _BEFORE_ALL, end,
                    min_days=1,
                )


class TestTheWalkWorksOutTheLedgerOnce:
    """What keeps the every-day walk affordable, whichever test calls it."""

    def test_a_walk_of_400_days_works_out_the_ledger_once(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A 400-day walk works out the ledger once, not once per day.

        Each working-out is the owner's whole ledger.  Measured 2026-11-06, asking
        per day took the nine tests that walk from 2-4 s to 14-31 s on CI, and one
        reading of 45, at 30.87 s, refused a pull request whatever it changed
        (plan#3017).  Counted rather than timed, so this cannot itself fail on a
        slow runner.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1])
            db.session.commit()
            end = seed_periods[3].start_date
            freeze_today(monkeypatch, end + timedelta(days=1))

            workings_out = []
            real_read = _ledger_reads.worked_out_lines

            def counted(*args):
                workings_out.append(args)
                return real_read(*args)

            monkeypatch.setattr(_ledger_reads, "worked_out_lines", counted)
            assert_fold_matches_postings_every_day(
                loan.id, seed_user["scenario"].id, _BEFORE_ALL, end,
                min_days=400,
            )
            assert len(workings_out) == 1


class TestRawLoanTransactionIsTheOnlyDivergence:
    """N-11: the one shape where the reader is RIGHT and the fold is INCOMPLETE.

    A raw settled transaction typed onto a loan account books a cash line the
    suite's window over the worked-out lines counts but the fold (the loan's
    anchors and the settled transfers into it) cannot see, so the two diverge by its
    amount.  This is the one shape the fold does not close on its own -- so
    ruling D4 forbids it at every source, making the every-day equality above
    complete by construction.  Both halves are pinned: the divergence is real,
    and the only user path to it is refused.
    """

    def test_a_forced_raw_loan_transaction_diverges_the_two_producers(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Directly posting a raw transaction on the loan diverges fold from reader.

        Bypassing the create guard with a direct settled-cash insert (the shape
        a legacy row would be), a $300 transaction posts onto the loan's linked
        ledger.  The reader counts it; the fold does not.  This is why the shape
        must be forbidden rather than folded -- and why B2's equality needs the
        guard, not an N-11 exception.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            on = seed_periods[2].start_date
            freeze_today(monkeypatch, seed_periods[3].start_date)

            before_fold = fold_loan_balances(
                loan.id, scenario_id, [on],
            )[on]
            before_read = posted_loan_balance_at(loan.id, scenario_id, on)
            assert before_fold == before_read  # agree before the raw txn

            create_settled_cash_transaction(
                seed_user, seed_periods[1], Decimal("300.00"),
                account=loan, name="Typed On Loan",
                settled_on=seed_periods[1].start_date,
            )
            db.session.commit()

            after_fold = fold_loan_balances(
                loan.id, scenario_id, [on],
            )[on]
            after_read = posted_loan_balance_at(loan.id, scenario_id, on)
            # The fold is blind to the raw transaction; the reader is not.
            assert after_fold == before_fold
            assert abs(after_read - before_read) == Decimal("300.00")

    def test_the_only_source_of_the_shape_is_refused(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The create route refuses a loan account (422), so the shape cannot arise.

        The divergence above cannot occur in production: ruling D4's guard
        rejects the only user path (the transaction-create route; the recurrence
        template is gated the same way), so the fold's blindness to raw
        transactions is correct by construction.  The guard's own home is
        ``test_transaction_guards.py``; this re-pins it as B2's premise.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Fold Guard Loan",
                principal=Decimal("200000.00"), rate=_RATE,
                origination_date=_ORIGINATION_DATE,
            )
            db.session.commit()
            expense = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            category = list(seed_user["categories"].values())[0]
            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "300.00",
                "account_id": loan.id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": expense.id,
                "scenario_id": seed_user["scenario"].id,
            })
            assert resp.status_code == 422
            assert (
                db.session.query(Transaction)
                .filter_by(account_id=loan.id).count() == 0
            )
