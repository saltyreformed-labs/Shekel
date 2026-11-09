"""C6c: the loan-detail paid-YTD chips fold the settled past.

Plan step **C6c** (``docs/audits/balance_architecture/README.md``).  Direct unit
coverage of the balance seam's two paid-in-year chip producers,
:func:`app.services.balance_at.loan_interest_paid_in_year` and
:func:`~app.services.balance_at.loan_principal_paid_in_year`, which the loan-detail
"Interest paid, YTD" / "Principal paid, YTD" chips read.  Each is the SETTLED side
of the loan fold -- the interest / principal a payment's real cash actually paid,
attributed to the DISPLAY-timezone civil year of its paid date (the L9 tax basis)
-- so they replaced the posting readers the chips read before
(``confirmed_loan_interest_in_year`` / ``confirmed_loan_principal_in_year``,
deleted at C6c).

Two properties are new versus those readers and pinned here:

* they fold the loan's SOURCE events, never the posting cache those readers read
  (which returned ``None`` for a loan it held no opening for, hiding the chip); and
* they are TOTAL -- a configured loan that paid nothing in the year folds ``0.00``,
  never ``None`` -- so the loan-detail page (which renders only for a configured
  loan) always shows the real figure.

The fixture is the synthetic split loan the reconciliation suites use ($250,000 @
6%, trued up to $100,000), so a $100,000 balance accrues exactly $500.00 the first
month (``100000 * 0.06 / 12``) and every expected figure is hand-computed and
shown in the docstring's arithmetic (never a producer as its own oracle, plan
N-7).  ``today`` is frozen after the seed window so every settled payment is
confirmed regardless of the wall clock.  All money is ``Decimal`` from strings.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.scenario import Scenario
from app.services import balance_at, transfer_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    SPLIT_LOAN,
)

(_ORIGINATION_PRINCIPAL, _ORIGINATION_DATE, _RATE, _ANCHOR_BALANCE,
 _ANCHOR_DATE, _P1, _P2, _P3) = SPLIT_LOAN
_FROZEN_TODAY = date(2027, 1, 1)


def _paid_on(year: int, month: int, day: int) -> datetime:
    """Return a noon-UTC settle instant, so its civil date is unambiguous."""
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def _split_loan(seed_user, *, escrow_annual=None):
    """Create the synthetic split loan (origination $250k, trued to $100k)."""
    return create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE, rate=_RATE,
        origination_date=_ORIGINATION_DATE, escrow_annual=escrow_annual,
    )


def _settle(seed_user, loan, period, cash, *, settled_on):
    """Settle one Checking -> loan payment (auto-posts its ledger split)."""
    return create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, period,
        amount=cash, settled_on=settled_on,
    )


class TestPaidInYearChips:
    """The chips report a loan's ACTUAL interest / principal paid in a year."""

    @pytest.fixture(autouse=True)
    def _frozen_today(self, monkeypatch):
        """Freeze today after the seed window so every settle is confirmed.

        The chip producers fold the clock-free walk, so ``today`` does not move
        their answer; freezing keeps the settle wiring deterministic across CI
        clocks (the settled shadows are what the fold reads).
        """
        freeze_today(monkeypatch, _FROZEN_TODAY)

    def test_single_payment_split_by_paid_year(
        self, app, db, seed_user, seed_periods,
    ):
        """One $1,000 payment paid 2026 folds $500.00 interest and $500.00 principal.

        Trued to $100,000 @ 6%: interest = round(100000 * 0.005) = 500.00;
        principal = cash - interest - escrow = 1000 - 500 - 0 = 500.00.  Paid
        2026-03-15, so 2026 holds both figures and the adjacent years hold nothing.
        """
        with app.app_context():
            loan = _split_loan(seed_user)
            _settle(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 3, 15),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("500.00")
            assert balance_at.loan_principal_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("500.00")
            for year in (2025, 2027):
                assert balance_at.loan_interest_paid_in_year(
                    loan, ctx, year,
                ) == Decimal("0.00")
                assert balance_at.loan_principal_paid_in_year(
                    loan, ctx, year,
                ) == Decimal("0.00")

    def test_running_split_across_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Three 2026 payments fold their real split on the shrinking balance.

        Interest accrues on 100000 -> 99500 -> 98997.50: interest
        500.00 / 497.50 / 494.99 = 1492.49; principal 500.00 / 502.50 / 505.01 =
        1507.51.  The two sum to the 3000 cash -- the ACTUAL figures, not the
        contractual replay.
        """
        with app.app_context():
            loan = _split_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2], seed_periods[_P3]):
                _settle(
                    seed_user, loan, period, Decimal("1000.00"),
                    settled_on=date(2026, 6, 1),
                )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("1492.49")
            assert balance_at.loan_principal_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("1507.51")

    def test_chips_sum_to_cash(self, app, db, seed_user, seed_periods):
        """The two chips together equal an escrow-free payment's cash.

        One $1,000 payment: principal 500 + interest 500 = 1000 = cash, proving the
        two chips describe ONE set of payments on one basis (they share the walk).
        """
        with app.app_context():
            loan = _split_loan(seed_user)
            _settle(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 6, 1),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            interest = balance_at.loan_interest_paid_in_year(loan, ctx, 2026)
            principal = balance_at.loan_principal_paid_in_year(loan, ctx, 2026)
            assert interest + principal == Decimal("1000.00")

    def test_attributes_by_the_paid_date_not_the_pay_period(
        self, app, db, seed_user, seed_periods,
    ):
        """A 2026-period payment PAID in 2025 folds into 2025 (both chips).

        A period-``_P1`` payment (a 2026 pay period) settled 2025-12-20 attributes
        its 500.00 interest and 500.00 principal to 2025 -- the chips key on the
        civil PAID date, not the scheduled period.
        """
        with app.app_context():
            loan = _split_loan(seed_user)
            _settle(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2025, 12, 20),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2025,
            ) == Decimal("500.00")
            assert balance_at.loan_principal_paid_in_year(
                loan, ctx, 2025,
            ) == Decimal("500.00")
            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("0.00")

    def test_new_years_eve_evening_settle_folds_into_the_display_year(
        self, app, db, seed_user, seed_periods,
    ):
        """THE L9 CASE: a settle at 8:05 PM EST Dec 31 folds into the Dec 31 year.

        A payment settled 2025-12-31 20:05 Eastern is stored 2026-01-01 01:05 UTC,
        so the balance ledger's UTC clock is already 2026.  The chip follows the
        user's WALL clock (L9): the 500.00 interest folds into 2025, and 2026 sees
        nothing.  A UTC-basis attribution would report the reverse.
        """
        with app.app_context():
            loan = _split_loan(seed_user)
            _settle(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2025, 12, 31),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2025,
            ) == Decimal("500.00")
            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("0.00")

    def test_extra_payment_lands_in_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,500 payment folds 500 interest and 1000 principal (extra included).

        interest 500.00, escrow 0, principal = 1500 - 500 - 0 = 1000.00 -- the
        real paydown, extra included, where the contractual split would show 500.
        """
        with app.app_context():
            loan = _split_loan(seed_user)
            _settle(
                seed_user, loan, seed_periods[_P1], Decimal("1500.00"),
                settled_on=date(2026, 6, 1),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("500.00")
            assert balance_at.loan_principal_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("1000.00")

    def test_escrow_is_excluded_from_both_chips(
        self, app, db, seed_user, seed_periods,
    ):
        """Escrow is neither interest nor principal, so it counts in neither chip.

        Escrow $1,200/yr = $100/mo.  A $1,100 payment splits interest 500.00,
        escrow 100.00, principal = 1100 - 500 - 100 = 500.00.  The interest chip
        reports 500.00 (not 600.00) and the principal chip 500.00 (not 600.00) --
        the $100 escrow is a wash for both.
        """
        with app.app_context():
            loan = _split_loan(seed_user, escrow_annual=Decimal("1200.00"))
            _settle(
                seed_user, loan, seed_periods[_P1], Decimal("1100.00"),
                settled_on=date(2026, 6, 1),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("500.00")
            assert balance_at.loan_principal_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("500.00")

    def test_reverted_payment_drops_out(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled-then-reverted payment folds nothing (it leaves the settled set).

        The fold reads the SETTLED shadows; reverting a payment to Projected
        removes it, so its 500.00 interest / 500.00 principal drops from the year
        cleanly, leaving 0.00 -- never a stranded figure.
        """
        with app.app_context():
            loan = _split_loan(seed_user)
            xfer = _settle(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                settled_on=date(2026, 3, 15),
            )
            db.session.commit()
            ctx_before = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_interest_paid_in_year(
                loan, ctx_before, 2026,
            ) == Decimal("500.00")

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            ctx_after = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_paid_in_year(
                loan, ctx_after, 2026,
            ) == Decimal("0.00")
            assert balance_at.loan_principal_paid_in_year(
                loan, ctx_after, 2026,
            ) == Decimal("0.00")

    def test_chips_are_scenario_scoped(
        self, app, db, seed_user, seed_periods,
    ):
        """Each scenario folds only its own paid interest, never the other's.

        The baseline settles TWO payments (interest 500.00 + 497.50 = 997.50); a
        what-if settles ONE (interest 500.00).  Each scenario's chip returns only
        its own total -- a leak would sum them (1497.50).
        """
        with app.app_context():
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _split_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2]):
                _settle(
                    seed_user, loan, period, Decimal("1000.00"),
                    settled_on=date(2026, 6, 1),
                )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_P1], amount=Decimal("1000.00"),
                settled_on=date(2026, 6, 1), scenario=whatif,
            )
            db.session.commit()

            baseline_ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_interest_paid_in_year(
                loan, baseline_ctx, 2026,
            ) == Decimal("997.50")

            whatif_ctx = BalanceContext(
                user_id=seed_user["user"].id, scenario=whatif,
                as_of=date(2027, 1, 1),
            )
            assert balance_at.loan_interest_paid_in_year(
                loan, whatif_ctx, 2026,
            ) == Decimal("500.00")

    def test_configured_loan_with_no_payments_folds_zero_not_none(
        self, app, db, seed_user,
    ):
        """A configured loan that paid nothing this year folds 0.00 (TOTAL, not None).

        The deleted posting readers returned ``None`` for an un-backfilled loan;
        the fold producers are total, so a configured loan with no settled payment
        this year returns ``Decimal("0.00")`` -- and the loan-detail page shows the
        chip rather than hiding it.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("0.00")
            assert balance_at.loan_principal_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("0.00")
