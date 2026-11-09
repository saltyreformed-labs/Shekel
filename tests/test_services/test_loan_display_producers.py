"""Tests for the loan-detail display read producers (Loop B rebuild).

:mod:`app.services.loan_posting_service._display` answers the loan detail page's
posting-derived measured surfaces:

* ``confirmed_loan_payment_history`` -- the confirmed payment table, each
  payment's real cash / principal / interest / escrow split; and
* ``loan_balance_anchor_history`` -- the balance-anchors drift scorecard, each
  opening / true-up paired with the ledger's pre-correction balance.

(The interest / principal paid-YTD chips folded off the postings onto the loan
ledger at step C6c; they are pinned in ``test_loan_paid_in_year.py``.)

The fixtures are the SAME synthetic split-loan the reconciliation suites use
($250,000 @ 6%, trued up to $100,000): a $100,000 balance at 6% accrues exactly
$500.00 the first month (``100000 * 0.06 / 12``), so every expected figure is
hand-computed and shown in the docstring's arithmetic.  The trueup anchor
($100,000) differs from origination ($250,000), so a correct figure proves the
walk seeds from the trueup.  ``today`` is frozen after the seed window so every
settled payment is confirmed regardless of the wall clock.  All money is
``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.services import account_service, loan_posting_service, transfer_service
from app.services.loan_posting_service import LoanAnchorDrift, LoanPaymentHistoryRow
from tests._test_helpers import (
    SPLIT_LOAN,
    capture_sql_statements,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    seam_confirmed_view,
)

(_ORIGINATION_PRINCIPAL, _ORIGINATION_DATE, _RATE, _ANCHOR_BALANCE,
 _ANCHOR_DATE, _P1, _P2, _P3) = SPLIT_LOAN
_AS_OF = date(2026, 12, 31)
_FROZEN_TODAY = date(2027, 1, 1)
# The seed periods (payment_day=1) are due 02-01 / 03-01 / 04-01 2026, all after
# the 2026-01-10 trueup anchor, so every payment splits from the $100,000 anchor.
_P1_DUE = date(2026, 2, 1)
# A $1,000.00 payment on the $100,000 anchor at 6%: interest
# 100000.00 * 0.06 / 12 = 500.00, principal 1000.00 - 500.00 = 500.00.
_P1_ROW = LoanPaymentHistoryRow(
    installment=_P1_DUE,
    cash=Decimal("1000.00"),
    principal=Decimal("500.00"),
    interest=Decimal("500.00"),
    escrow=Decimal("0.00"),
)
# The stored ledger's tables, by name: what the display's two surfaces never read.
_STORED_LEDGER_TABLES = ("journal_entries", "account_postings", "ledger_accounts")


def _paid_on(year: int, month: int, day: int) -> datetime:
    """Return a noon-UTC settle instant, so its civil date is unambiguous."""
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def _make_split_loan(seed_user, db_session, *, escrow_annual=None, name="Split Loan"):
    """Create the synthetic split loan (origination $250k, trued to $100k)."""
    return create_loan_with_trueup(
        seed_user, db_session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE, rate=_RATE,
        origination_date=_ORIGINATION_DATE, escrow_annual=escrow_annual,
        name=name,
    )


def _settle(seed_user, db_session, loan, period, cash, *, settled_on):
    """Settle one Checking -> loan payment (auto-posts its ledger split)."""
    return create_settled_transfer(
        seed_user, db_session, seed_user["account"], loan, period,
        amount=cash, settled_on=settled_on,
    )


# ---------------------------------------------------------------------------
# confirmed_loan_payment_history -- the confirmed payment table
# ---------------------------------------------------------------------------


class TestConfirmedLoanPaymentHistory:
    """One row per confirmed payment, split into real cash / P&I / escrow."""

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so every settle is confirmed."""
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_single_payment_row(self, app, db, seed_user, seed_periods):
        """A $1,000 payment: cash 1000, principal 500, interest 500, no escrow.

        Dated at the true monthly due date (2026-02-01 for period _P1).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1000.00"), settled_on=date(2026, 6, 1),
            )
            db.session.commit()

            rows = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert rows == [_P1_ROW]

    def test_multi_payment_rows_are_chronological(
        self, app, db, seed_user, seed_periods,
    ):
        """Three $1,000 payments: the shrinking-balance split, cash 1000 each.

        P1 500/500, P2 497.50/502.50, P3 494.99/505.01 (interest/principal).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            for period in (seed_periods[_P1], seed_periods[_P2], seed_periods[_P3]):
                _settle(
                    seed_user, db.session, loan, period, Decimal("1000.00"),
                    settled_on=date(2026, 6, 1),
                )
            db.session.commit()

            rows = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert [(r.principal, r.interest, r.cash) for r in rows] == [
                (Decimal("500.00"), Decimal("500.00"), Decimal("1000.00")),
                (Decimal("502.50"), Decimal("497.50"), Decimal("1000.00")),
                (Decimal("505.01"), Decimal("494.99"), Decimal("1000.00")),
            ]

    def test_escrow_splits_out_and_cash_reconciles(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment with escrow: cash = principal + interest + escrow exactly.

        Escrow $1,200/yr = $100/mo.  A $1,100 payment: interest 500, escrow 100,
        principal = 1100 - 500 - 100 = 500; and 500 + 500 + 100 = 1100 = cash.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(
                seed_user, db.session, escrow_annual=Decimal("1200.00"),
            )
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1100.00"), settled_on=date(2026, 6, 1),
            )
            db.session.commit()

            [row] = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert row.cash == Decimal("1100.00")
            assert row.principal == Decimal("500.00")
            assert row.interest == Decimal("500.00")
            assert row.escrow == Decimal("100.00")
            assert row.principal + row.interest + row.escrow == row.cash

    def test_extra_payment_row_shows_the_actual_split(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,500 payment: cash 1500, principal 1000, interest 500."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1500.00"), settled_on=date(2026, 6, 1),
            )
            db.session.commit()

            [row] = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert row.cash == Decimal("1500.00")
            assert row.principal == Decimal("1000.00")
            assert row.interest == Decimal("500.00")

    def test_split_matches_the_amortization_history_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """The table's split equals the schedule's confirmed rows (same payments).

        The table reads the ledger's walk; the schedule rows fold the read pass's
        walk (plan step E1d-b).  A payment's (principal, interest) must be
        identical between them, and this is where they are compared.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(
                    seed_user, db.session, loan, period, Decimal("1000.00"),
                    settled_on=date(2026, 6, 1),
                )
            db.session.commit()

            table = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            schedule = seam_confirmed_view(
                loan.id, scenario_id, _AS_OF,
            ).history_rows
            assert [(r.principal, r.interest) for r in table] == [
                (row.principal, row.interest) for row in schedule
            ]

    def test_a_payment_is_excluded_until_its_settled_date(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled payment is excluded from the table until its SETTLED date (C2).

        The table shares the balance readers' visibility cut, which is a payment's
        SETTLED date since C2.  This payment is budgeted to period 5 (a March
        period) but settled 2026-06-01, so reading the day BEFORE it settled yields
        no row; on the settled date it yields one.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            settled = date(2026, 6, 1)
            _settle(
                seed_user, db.session, loan, seed_periods[_P3],
                Decimal("1000.00"), settled_on=date(2026, 6, 1),
            )
            db.session.commit()

            before = settled - timedelta(days=1)
            assert loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, before,
            ) == []
            assert len(loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, settled,
            )) == 1

    def test_reverted_payment_drops_from_the_table(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled-then-reverted payment leaves no row (its legs net to zero)."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            xfer = _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1000.00"), settled_on=date(2026, 6, 1),
            )
            db.session.commit()
            # Revert to Projected through the same chokepoint the route uses.
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            assert loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            ) == []

    def test_unconfigured_loan_returns_none(self, app, db, seed_user):
        """A loan account with no terms reads None, and so does its scorecard.

        It walks to nothing, so both surfaces hide on the one gate rather than
        showing a misleading empty table.
        """
        with app.app_context():
            loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=loan_type.id,
                    name="No Terms Loan",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.commit()

            scenario_id = seed_user["scenario"].id
            assert loan_posting_service.confirmed_loan_payment_history(
                account.id, scenario_id, _AS_OF,
            ) is None
            assert loan_posting_service.loan_balance_anchor_history(
                account.id, scenario_id, _AS_OF,
            ) is None

    def test_a_configured_loan_with_no_payment_reads_an_empty_table(
        self, app, db, seed_user,
    ):
        """A configured loan's walk opened, so no payment yet is ``[]``, not None."""
        with app.app_context():
            loan = _make_split_loan(seed_user, db.session)
            db.session.commit()
            assert loan_posting_service.confirmed_loan_payment_history(
                loan.id, seed_user["scenario"].id, _AS_OF,
            ) == []

    def test_a_scenario_shows_only_its_own_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Each scenario's table holds its own payments; one with none reads ``[]``.

        The loan's opening and true-up are the loan's, so every scenario walks
        from the same $100,000; the $1,000.00 payment sits in the what-if alone,
        so only the what-if lists it (see ``_P1_ROW``'s arithmetic), and the
        scenario holding no payment of the loan reads the empty table the page
        hides.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            whatif = Scenario(user_id=user_id, name="What-if", is_baseline=False)
            unopened = Scenario(user_id=user_id, name="Unopened", is_baseline=False)
            db.session.add_all([whatif, unopened])
            db.session.commit()

            loan = _make_split_loan(seed_user, db.session)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                settled_on=date(2026, 6, 1), scenario=whatif,
            )
            db.session.commit()

            history = loan_posting_service.confirmed_loan_payment_history
            assert history(loan.id, whatif.id, _AS_OF) == [_P1_ROW]
            assert history(loan.id, seed_user["scenario"].id, _AS_OF) == []
            assert history(loan.id, unopened.id, _AS_OF) == []

    def test_neither_surface_reads_the_stored_ledger(
        self, app, db, seed_user, seed_periods,
    ):
        """The table lists the walk's payment, and no statement names a stored table.

        Plan task plan#2779: whether the table shows is the walk's answer.  The
        statements are captured, so a read of a stored table that happened to
        agree could not pass.  A load FROM the terms' table is what says the
        capture measured the walk, which reads the terms by the loan's id: merely
        naming that table says nothing, since ``Account.loan_params`` joins it
        into any load of the account.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            _settle(
                seed_user, db.session, loan, seed_periods[_P1],
                Decimal("1000.00"), settled_on=date(2026, 6, 1),
            )
            db.session.commit()

            rows, table_reads = capture_sql_statements(
                lambda: loan_posting_service.confirmed_loan_payment_history(
                    loan.id, scenario_id, _AS_OF,
                ),
            )
            drifts, scorecard_reads = capture_sql_statements(
                lambda: loan_posting_service.loan_balance_anchor_history(
                    loan.id, scenario_id, _AS_OF,
                ),
            )
            assert rows == [_P1_ROW]
            assert [drift.recorded for drift in drifts] == [
                Decimal("250000.00"), Decimal("100000.00"),
            ]
            for reads in (table_reads, scorecard_reads):
                assert any(
                    "FROM budget.loan_params" in text for text, _params in reads
                )
                assert not [
                    text for text, _params in reads
                    if any(table in text for table in _STORED_LEDGER_TABLES)
                ]

    def test_future_as_of_raises(self, app, db, seed_user):
        """A future as_of is a forward projection, out of the reader's domain."""
        with app.app_context():
            loan = _make_split_loan(seed_user, db.session)
            db.session.commit()
            with pytest.raises(ValueError, match="as_of <= today"):
                loan_posting_service.confirmed_loan_payment_history(
                    loan.id, seed_user["scenario"].id, date(2027, 6, 1),
                )


#: The off-day loan: $100,000.00 at 6% from 2026-01-22, due on the 22nd, so
#: its installments fall 02-22 (#1), 03-22 (#2), ... and one month's interest
#: on $100,000.00 is ``100000.00 * 0.06 / 12 = 500.00``.
_OFF_DAY_ORIGINATION = date(2026, 1, 22)
_FEB_22 = date(2026, 2, 22)
_MAR_10 = date(2026, 3, 10)
_MAR_22 = date(2026, 3, 22)


class TestAPaymentIsNamedByTheInstallmentItPays:
    """Rulings R-R108 and R-R109: the loan page names a payment by its installment.

    A payment due off the loan's contractual day pays the installment whose
    interval its due date falls in (ruling R-R104) -- a payment due 03-10 on a
    loan due the 22nd pays 02-22's -- and the charge, the cash price, the
    balance and the ledger already read that installment.  The loan page now
    names it the same way: the payment-history card lists it under that
    installment (R-R108), and the confirmed schedule dates and numbers its row
    there (R-R109).  Until then both read the payment's own due date, so each
    03-10 payment showed as ``Mar 2026`` and row ``#2 . 2026-03-10``.

    Josh's pick, tested exactly: "Both Mar 10 payments show under 'Feb 2026'
    on the history card and number as installment #1 on the confirmed
    schedule; payments due on the due day are unchanged."
    """

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so every settle is confirmed."""
        freeze_today(monkeypatch, _FROZEN_TODAY)

    @staticmethod
    def _off_day_loan(seed_user, db_session):
        """The $100,000.00 loan at 6% from 2026-01-22, due on the 22nd."""
        loan = create_loan_account(
            seed_user, db_session, name="Off Day Loan",
            principal=Decimal("100000.00"), rate=Decimal("0.06000"), term=360,
            origination_date=_OFF_DAY_ORIGINATION, payment_day=22,
        )
        db_session.commit()
        return loan

    def test_both_mar_10_payments_are_named_by_the_feb_22_installment(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two payments due 03-10 read ``Feb 2026`` / #1; the 03-22 one is unchanged.

        Walk (charges 02-22 and 03-22; the two 03-10 payments in recording
        order, both in ``seed_periods[4]``):

        * 02-22 charge: 500.00 of interest.
        * 03-10, $1,000.00 moved: interest 500.00, principal 500.00; owed
          99,500.00.
        * 03-10, $0.00 (a close): the SECOND payment inside 02-22's interval,
          so it clears nothing -- 0.00 / 0.00; owed 99,500.00.
        * 03-22 charge: ``99500.00 * 0.06 / 12 = 497.50``.
        * 03-22, $1,000.00: interest 497.50, principal 502.50; owed 98,997.50.

        Both 03-10 payments pay 02-22 -- listed under it on the card, row #1
        dated 02-22 on the schedule -- and the payment due ON the day keeps its
        own date, 03-22, row #2.
        """
        with app.app_context():
            loan = self._off_day_loan(seed_user, db.session)
            scenario_id = seed_user["scenario"].id
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[4], amount=Decimal("1000.00"),
                settled_on=_MAR_10, due_date=_MAR_10,
            )
            # Planned at $1,200.00 so the two 03-10 transfers are distinct
            # ad-hoc rows (``uq_transfers_adhoc_dedupe``); it moves $0.00.
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[4], amount=Decimal("1200.00"),
                settled_amount=Decimal("0.00"), settled_on=date(2026, 3, 12),
                due_date=_MAR_10,
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[5], amount=Decimal("1000.00"),
                settled_on=_MAR_22, due_date=_MAR_22,
            )
            db.session.commit()

            card = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert [
                (row.installment, row.cash, row.interest, row.principal)
                for row in card
            ] == [
                (_FEB_22, Decimal("1000.00"), Decimal("500.00"), Decimal("500.00")),
                (_FEB_22, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")),
                (_MAR_22, Decimal("1000.00"), Decimal("497.50"), Decimal("502.50")),
            ]

            schedule = seam_confirmed_view(
                loan.id, scenario_id, _AS_OF,
            ).history_rows
            assert [
                (row.month, row.payment_date, row.remaining_balance)
                for row in schedule
            ] == [
                (1, _FEB_22, Decimal("99500.00")),
                (1, _FEB_22, Decimal("99500.00")),
                (2, _MAR_22, Decimal("98997.50")),
            ]

    def test_a_payment_due_before_the_first_installment_keeps_its_own_date(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Due 01-28, before the 02-22 first installment: named 01-28, row #0.

        Ruling R-C's early extra pays no installment, so there is none to name
        it by: it keeps its own due date on the card and the schedule, and --
        no charge standing yet -- its whole $1,000.00 is principal.  It falls
        in the origination month, which the schedule numbers ``0``.
        """
        with app.app_context():
            loan = self._off_day_loan(seed_user, db.session)
            scenario_id = seed_user["scenario"].id
            due = date(2026, 1, 28)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
                settled_on=due, due_date=due,
            )
            db.session.commit()

            [row] = loan_posting_service.confirmed_loan_payment_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert (row.installment, row.interest, row.principal) == (
                due, Decimal("0.00"), Decimal("1000.00"),
            )
            [confirmed] = seam_confirmed_view(
                loan.id, scenario_id, _AS_OF,
            ).history_rows
            assert (confirmed.month, confirmed.payment_date) == (0, due)


# ---------------------------------------------------------------------------
# loan_balance_anchor_history -- the drift scorecard
# ---------------------------------------------------------------------------


class TestLoanBalanceAnchorHistory:
    """Each anchor paired with the ledger's pre-correction (owed_before) balance."""

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so post-anchor payments settle."""
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_opening_only_loan_shows_the_opening_row(
        self, app, db, seed_user,
    ):
        """An origination-only loan shows one opening row: computed 0, drift = principal.

        The loan opens from nothing, so ``computed`` is 0.00 and the opening's
        ``drift`` equals the original principal (not a meaningful correction --
        ``is_opening`` flags it for the display).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            assert loan_posting_service.loan_balance_anchor_history(
                loan.id, scenario_id, _AS_OF,
            ) == [LoanAnchorDrift(
                anchor_date=_ORIGINATION_DATE,
                recorded=Decimal("250000.00"),
                computed=Decimal("0.00"),
                drift=Decimal("250000.00"),
                is_opening=True,
                is_tracking_start=False,
            )]

    def test_opening_and_trueup_drift(self, app, db, seed_user):
        """A trueup with no intervening payments drifts from the un-amortized balance.

        The walk does not auto-amortize between anchors: with no payments between
        origination (2025-01-01, opens 250000) and the trueup (2026-01-10, asserts
        100000), the trueup's ``computed`` is still 250000, so drift = 100000 -
        250000 = -150000 -- the ledger had no payments recorded, so it read high.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            db.session.commit()

            assert loan_posting_service.loan_balance_anchor_history(
                loan.id, scenario_id, _AS_OF,
            ) == [
                LoanAnchorDrift(
                    anchor_date=_ORIGINATION_DATE,
                    recorded=Decimal("250000.00"),
                    computed=Decimal("0.00"),
                    drift=Decimal("250000.00"),
                    is_opening=True,
                    is_tracking_start=False,
                ),
                LoanAnchorDrift(
                    anchor_date=_ANCHOR_DATE,
                    recorded=Decimal("100000.00"),
                    computed=Decimal("250000.00"),
                    drift=Decimal("-150000.00"),
                    is_opening=False,
                    is_tracking_start=False,
                ),
            ]

    def test_post_trueup_payments_do_not_move_the_scorecard(
        self, app, db, seed_user, seed_periods,
    ):
        """Payments after the trueup do not change either anchor's owed_before.

        The drift scorecard measures each anchor against the balance JUST BEFORE
        it; payments due after the trueup (2026-02-01+) sort after it, so the
        trueup's computed stays 250000 -- the scorecard is stable under new
        payments.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_split_loan(seed_user, db.session)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(
                    seed_user, db.session, loan, period, Decimal("1000.00"),
                    settled_on=date(2026, 6, 1),
                )
            db.session.commit()

            rows = loan_posting_service.loan_balance_anchor_history(
                loan.id, scenario_id, _AS_OF,
            )
            assert [(r.recorded, r.computed, r.drift) for r in rows] == [
                (Decimal("250000.00"), Decimal("0.00"), Decimal("250000.00")),
                (Decimal("100000.00"), Decimal("250000.00"),
                 Decimal("-150000.00")),
            ]

    def test_unconfigured_loan_returns_none(self, app, db, seed_user):
        """A non-loan account is not a configured loan, so the card is None."""
        with app.app_context():
            assert loan_posting_service.loan_balance_anchor_history(
                seed_user["account"].id, seed_user["scenario"].id, _AS_OF,
            ) is None
