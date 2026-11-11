"""Every loan walk reads its loan's interest convention, through the one producer.

Plan step **recurrence:R16-d**, its third piece (finding **D52**; ruling
plan#3345).  The convention enters ONCE, where the stored id is read back as
its member (:func:`app.services.loan_resolver.accrual_convention`), and rides
the values every walk already holds: the rate periods, the projection's terms,
the replay's charges, and the payoff strategy's debts.

**Why a sentinel.**  The list has one member, so a walk that ignored the loan
and named ``MONTHLY_1_12`` itself would pass any test that only compared
members.  Each test here replaces the one reading with a SENTINEL that passes
every type check (a ``MagicMock`` specced on the enum) and asserts the
sentinel is what arrives, so a hardcoded member anywhere on the path fails.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest

from app import ref_cache
from app.enums import InterestAccrualConventionEnum
from app.services import loan_resolver
from app.services.amortization_engine import PeriodTerms, RateChangeRecord
from app.services.installment_calendar import installment_dates
from app.services.loan_ledger import LoanCalendar
from app.services.loan_ledger._charges import contract_charges
from tests.oracles.loan_monthly_composition import rate_period

_MONTHLY = InterestAccrualConventionEnum.MONTHLY_1_12
_ORIGINATION = date(2026, 1, 15)


def _params(**overrides) -> SimpleNamespace:
    """A fixed-rate LoanParams stand-in: $100,000.00 over 360 months, due the 1st."""
    fields = {
        "origination_date": _ORIGINATION,
        "original_principal": Decimal("100000.00"),
        "term_months": 360,
        "payment_day": 1,
        "is_arm": False,
        "arm_first_adjustment_months": None,
        "arm_adjustment_interval_months": None,
        "interest_accrual_convention_id": ref_cache.interest_accrual_convention_id(
            _MONTHLY,
        ),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _origination_rate() -> list[RateChangeRecord]:
    """The origination rate row every loan carries: 6%."""
    return [RateChangeRecord(effective_date=_ORIGINATION, interest_rate=Decimal("0.06"))]


@pytest.fixture(name="sentinel_convention")
def _sentinel_convention(monkeypatch):
    """Make the ONE reading of the stored id answer a sentinel member.

    ``MagicMock(spec=...)`` reports the enum as its class, so it passes the
    carriers' ``isinstance`` checks; nothing but the reading can produce it.
    """
    sentinel = mock.MagicMock(spec=InterestAccrualConventionEnum)
    monkeypatch.setattr(
        ref_cache, "interest_accrual_convention_member",
        lambda convention_id: sentinel,
    )
    return sentinel


class TestTheConventionEntersOnce:
    """The stored id is read back once, and what it reads rides the periods."""

    def test_the_stored_id_reads_back_as_its_member(self):
        """A loan row holding ``monthly_1_12``'s id reads back as the member itself."""
        assert loan_resolver.accrual_convention(_params()) is _MONTHLY

    def test_every_rate_period_carries_what_the_reading_answered(
        self, sentinel_convention,
    ):
        """The loan's periods carry the reading's answer, not a member of their own."""
        periods = loan_resolver.resolve_periods(_params(), _origination_rate())
        assert periods
        assert all(period.convention is sentinel_convention for period in periods)

    def test_the_projections_terms_carry_it_too(self, sentinel_convention):
        """``engine_terms`` maps each period's convention onto its ``PeriodTerms``."""
        terms = loan_resolver.engine_terms(_params(), _origination_rate())
        assert terms
        assert all(entry.convention is sentinel_convention for entry in terms)

    @pytest.mark.parametrize(
        "not_a_member", [1, None, "monthly_1_12"],
        ids=["a-stored-id", "none", "the-members-name"],
    )
    def test_projection_terms_refuse_a_value_that_is_not_a_member(
        self, not_a_member,
    ):
        """The stored id, ``None`` and the name string are refused where terms are built.

        Not at the first row the projection charges: the three values a
        caller could hand over by mistake fail at construction.
        """
        with pytest.raises(TypeError, match="convention must be an"):
            PeriodTerms(
                start_date=date(2026, 1, 1),
                annual_rate=Decimal("0.06"),
                monthly_pi=Decimal("1500.00"),
                convention=not_a_member,
            )


class TestEachChargeCarriesItsWindow:
    """The replay's charges each open on the installment before them."""

    @pytest.mark.parametrize("payment_day", [1, 15, 28, 30, 31])
    def test_each_charge_opens_on_the_previous_installment(self, payment_day):
        """Every window abuts the one before it, so no month is charged twice or skipped.

        Over two years of a loan originated Dec 31, 2026, so a 31st-of-month
        loan crosses two Februaries (2027's 28th; 2028's leap 29th).
        """
        origination = date(2026, 12, 31)
        calendar = LoanCalendar(
            origination_date=origination,
            payment_day=payment_day,
            periods=[rate_period(Decimal("0.06"), start_date=origination)],
            escrow_lines=[],
        )
        charges = contract_charges(calendar, date(2028, 12, 31))
        dues = installment_dates(origination, payment_day, date(2028, 12, 31))
        assert [charge.on_date for charge in charges] == dues
        for previous, charge in zip(dues, charges[1:]):
            assert charge.opens_on == previous

    def test_the_first_charge_opens_on_the_origination_months_due_day(self):
        """Originated Jan 15, due the 1st: the first installment Feb 1 opens on Jan 1.

        Not on the origination date: one calendar month, the window
        ``MONTHLY_1_12`` charges for (see ``accrual_window``).
        """
        calendar = LoanCalendar(
            origination_date=_ORIGINATION,
            payment_day=1,
            periods=[rate_period(Decimal("0.06"), start_date=_ORIGINATION)],
            escrow_lines=[],
        )
        first = contract_charges(calendar, date(2026, 2, 1))[0]
        assert (first.opens_on, first.on_date) == (date(2026, 1, 1), date(2026, 2, 1))
