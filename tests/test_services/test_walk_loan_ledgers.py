"""Tests for the owner's loan load (plan task plan#2649, ruling plan#2623).

:func:`app.services.loan_ledger.walk_loan_ledgers` walks many loans at once:
each loan's terms are loaded by the loans' ids in one statement
(:func:`app.services.loan_loaders.load_loans_params`), and every kind of
record the walks fold -- the standing statements, the rate history, the escrow
lines and the settled payments -- is loaded ONCE for all of them
(:func:`app.services.loan_loaders.load_loan_records`).  What these cases pin:

* **each walk is its loan's own**: equal to
  :func:`app.services.loan_ledger.walk_loan_ledger`'s for that loan, over two
  loans whose records differ in every kind (a true-up and a withdrawn one, a
  rate change, two escrow lines against none, three payments one of them a
  $0.00 close against one) and an amortizing account carrying no terms;
* **each loan's records are what its one-loan doors answer**, kind by kind;
* **the worked-out read books each loan off its own walk**: each loan's
  correction lines are its own walk's targets, the two loans' differing;
* **no loan, no statement**: an owner without a loan pays nothing for the
  loan half of a read.

That the worked-out read's statements stay fixed as the loans grow is
``test_ledger_lines/test_the_worked_out_read/test_what_it_reads.py``'s, and
that each walk is RIGHT is the loan walk's own suites' (``test_loan_ledger.py``
and the oracles), which reach it through the one-loan door this shares its
assembly with.
"""

from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.models.loan_anchor_withdrawal import LoanAnchorWithdrawal
from app.models.loan_features import RateHistory
from app.services import loan_loaders
from app.services.ledger_lines import loan_correction_targets, worked_out_lines
from app.services.loan_ledger import walk_loan_ledger, walk_loan_ledgers
from tests._test_helpers import (
    add_escrow_line,
    capture_sql_statements,
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
)

# Inside seed_periods' last paycheck, after every paycheck a case files in.
_TODAY = date(2026, 5, 15)


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Freeze the app's today at :data:`_TODAY` for every case."""
    freeze_today(monkeypatch, _TODAY)


def _two_loans_and_an_unconfigured_one(seed_user, seed_periods):
    """Return two loans whose records differ in every kind, and one without terms.

    The first: $250,000.00 at 6% from 2025-01-01, trued to $100,000.00 on
    2026-01-10, a rate change to 7% effective 2026-03-01, two escrow lines
    (taxes from origination, insurance from 2026-02-01), and three payments
    from Checking: $1,000.00 in the second and fourth paychecks and a $0.00
    close in the sixth.  The second: $50,000.00 at 4% from 2025-06-01, trued
    to $45,000.00 on 2026-01-20 and again to $44,000.00 on 2026-02-20, the
    second true-up withdrawn, no escrow, and one $500.00 payment in the third
    paycheck.  The third: an Auto Loan account carrying no ``LoanParams``.

    Returns:
        ``(first, second, unconfigured)``, the session expired.
    """
    checking = seed_user["account"]
    first = create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=Decimal("250000.00"),
        anchor_balance=Decimal("100000.00"),
        anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
        origination_date=date(2025, 1, 1), name="First Loan",
        escrow_annual=Decimal("2400.00"),
    )
    add_escrow_line(
        _db.session, first.id, "Insurance", Decimal("1200.00"),
        effective_date=date(2026, 2, 1),
    )
    _db.session.add(RateHistory(
        account_id=first.id, effective_date=date(2026, 3, 1),
        interest_rate=Decimal("0.07000"), monthly_pi=None,
    ))
    second = create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=Decimal("50000.00"),
        anchor_balance=Decimal("45000.00"),
        anchor_date=date(2026, 1, 20), rate=Decimal("0.04000"),
        origination_date=date(2025, 6, 1), name="Second Loan",
    )
    withdrawn = insert_trueup_event(
        loan_params_for(_db.session, second.id), Decimal("44000.00"),
        anchor_date=date(2026, 2, 20),
    )
    _db.session.add(LoanAnchorWithdrawal(
        account_id=second.id, anchor_event_id=withdrawn.id,
    ))
    unconfigured = create_account_of_type(
        seed_user, _db.session, "Auto Loan", "Unconfigured Loan",
    )
    _db.session.commit()
    for period in (seed_periods[1], seed_periods[3]):
        create_settled_transfer(
            seed_user, _db.session, checking, first, period,
            amount=Decimal("1000.00"),
        )
    create_settled_transfer(
        seed_user, _db.session, checking, first, seed_periods[5],
        amount=Decimal("1000.00"), settled_amount=Decimal("0.00"),
    )
    create_settled_transfer(
        seed_user, _db.session, checking, second, seed_periods[2],
        amount=Decimal("500.00"),
    )
    _db.session.commit()
    _db.session.expire_all()
    return first, second, unconfigured


class TestEachWalkIsItsLoansOwn:
    """The owner's one load hands each loan the walk its own door gives."""

    def test_each_walk_equals_its_loans_walk(
        self, app, db, seed_user, seed_periods,
    ):
        """Every walk equals ``walk_loan_ledger``'s for its loan, as a value.

        The walks hold what their records say, so the comparison compares
        something: the first loan splits three payments (the $0.00 close
        among them, cash 0.00) and corrects its opening and true-up; the
        second splits one payment and corrects its opening and its standing
        true-up, the withdrawn one absent; the unconfigured account walks
        nothing.  A load that handed one loan another's statements, payments
        or terms makes that loan's walk differ from its door's, which loads
        its records alone.
        """
        with app.app_context():
            first, second, unconfigured = _two_loans_and_an_unconfigured_one(
                seed_user, seed_periods,
            )
            scenario_id = seed_user["scenario"].id

            walks = walk_loan_ledgers(
                [first.id, second.id, unconfigured.id], scenario_id,
            )

            assert walks == {
                loan.id: walk_loan_ledger(loan.id, scenario_id)
                for loan in (first, second, unconfigured)
            }
            assert [
                outcome.cash for outcome in walks[first.id].settled_splits
            ] == [Decimal("1000.00"), Decimal("1000.00"), Decimal("0.00")]
            assert [
                correction.anchor.anchor_balance
                for correction in walks[first.id].anchor_corrections
            ] == [Decimal("250000.00"), Decimal("100000.00")]
            assert [
                outcome.cash for outcome in walks[second.id].settled_splits
            ] == [Decimal("500.00")]
            assert [
                correction.anchor.anchor_balance
                for correction in walks[second.id].anchor_corrections
            ] == [Decimal("50000.00"), Decimal("45000.00")]
            assert not walks[unconfigured.id].payment_splits
            assert not walks[unconfigured.id].anchor_corrections

    def test_each_loans_records_are_what_its_doors_answer(
        self, app, db, seed_user, seed_periods,
    ):
        """Each kind of a loan's records equals its one-loan door's answer.

        The statements equal ``load_loan_anchor_facts``', the rate feed
        ``load_rate_changes``' (the first loan's two rates, newest first),
        the escrow lines ``load_escrow_lines``' (the first loan's two, the
        second's none) and the settled payments ``settled_income_legs``'
        (three and one).  The doors are bodies over one loan's id, so a
        slice that took rows of another loan differs from them.
        """
        with app.app_context():
            first, second, _unconfigured = _two_loans_and_an_unconfigured_one(
                seed_user, seed_periods,
            )
            scenario_id = seed_user["scenario"].id
            loans = [first.loan_params, second.loan_params]

            records = loan_loaders.load_loan_records(loans, scenario_id)

            assert set(records) == {first.id, second.id}
            for params in loans:
                loan_id = params.account_id
                assert records[loan_id].anchor_facts == (
                    loan_loaders.load_loan_anchor_facts(params)
                )
                assert records[loan_id].rate_changes == (
                    loan_loaders.load_rate_changes(loan_id)
                )
                assert records[loan_id].escrow_lines == (
                    loan_loaders.load_escrow_lines(loan_id)
                )
                assert records[loan_id].settled == (
                    loan_loaders.settled_income_legs(
                        loan_id, scenario_id, options=(),
                    )
                )
            assert [
                change.interest_rate
                for change in records[first.id].rate_changes
            ] == [Decimal("0.07000"), Decimal("0.06000")]
            assert len(records[first.id].escrow_lines) == 2
            assert records[second.id].escrow_lines == []
            assert len(records[first.id].settled) == 3
            assert len(records[second.id].settled) == 1


def _target_lines(targets):
    """Return a correction target's non-zero legs as a multiset of tuples."""
    return Counter(
        (key, pay_period_id, entry_date, amount, source_kind_id, kind_id)
        for (source_kind_id, pay_period_id, entry_date), legs in targets.items()
        for key, (amount, kind_id) in legs.items()
        if amount != 0
    )


class TestTheReadBooksEachLoanOffItsOwnWalk:
    """The worked-out read hands each loan the walk the owner's load built for it."""

    def test_each_loans_lines_are_its_own_walks(
        self, app, db, seed_user, seed_periods,
    ):
        """Each loan's correction lines are its own walk's targets, as values.

        Every chart key a loan's targets name carries that loan's id, so the
        read's lines on those keys are that loan's alone, and they must be
        exactly the targets :func:`loan_correction_targets` books from the
        loan's door walk.  The two loans' targets differ (the first opens at
        $250,000.00 and splits three payments, the second at $50,000.00 and
        splits one), so a read that booked one loan off another's walk -- its
        own keys, the other's amounts and days -- fails this, which the
        statement control cannot see: its loans' records are identical.
        """
        with app.app_context():
            first, second, _unconfigured = _two_loans_and_an_unconfigured_one(
                seed_user, seed_periods,
            )
            owner_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id

            lines = worked_out_lines(owner_id, scenario_id)

            expected = {
                loan.id: _target_lines(loan_correction_targets(
                    walk_loan_ledger(loan.id, scenario_id), loan,
                ))
                for loan in (first, second)
            }
            assert expected[first.id] != expected[second.id]
            for loan in (first, second):
                keys = {key for key, *_rest in expected[loan.id]}
                assert Counter(
                    (
                        line.key, line.budgeted_period_id, line.entry_date,
                        line.amount, line.source_kind_id,
                        line.posting_kind_id,
                    )
                    for line in lines
                    if line.movement_id is None and line.key in keys
                ) == expected[loan.id]
            # Each tuple's third field is its day: the opening's and the
            # true-up's are among each loan's own.
            assert {
                entry_date for _key, _period, entry_date, *_rest
                in expected[first.id]
            } >= {date(2025, 1, 1), date(2026, 1, 10)}
            assert {
                entry_date for _key, _period, entry_date, *_rest
                in expected[second.id]
            } >= {date(2025, 6, 1), date(2026, 1, 20)}


class TestNoLoanNoStatement:
    """An owner without a loan pays nothing for the loan half of a read."""

    def test_no_loan_loads_nothing(self, app, db, seed_user):
        """``load_loan_records([])`` answers ``{}`` and issues no statement.

        So the worked-out read of an owner holding no loan issues no loan
        statement: each of the four loads would otherwise run over an empty
        set of ids and find nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            records, statements = capture_sql_statements(
                lambda: loan_loaders.load_loan_records([], scenario_id),
            )

            assert records == {}
            assert statements == []
