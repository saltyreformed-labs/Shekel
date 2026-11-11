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
sentinel is what arrives, so a member hardcoded anywhere between the reading
and the producer fails: each of the reading's three callers -- the resolver's
terms, the balance seam's terms (the payoff strategy's page) and the refinance
calculator's hypothetical loan -- is driven here.

**Where it arrives is the one interest producer**
(:func:`app.utils.money.installment_interest`), spied on in every module that
calls it (:data:`_WALKS`, held to the modules that import it by a census), so
each walk is graded on what it HANDS the producer: the loan's convention and
the window its installment charges for.
"""

import ast
import pathlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest

from app import ref_cache
from app.enums import InterestAccrualConventionEnum
from app.services import debt_strategy_service, loan_resolver
from app.services.amortization_engine import (
    PaymentDates,
    PeriodTerms,
    ProjectionInputs,
    RateChangeRecord,
    project_forward,
)
from app.services.installment_calendar import installment_dates
from app.services.loan_ledger import (
    LoanCalendar,
    LoanCashEvent,
    LoanEventStream,
    LoanResetEvent,
    replay_loan_events,
    with_contract_charges,
)
from app.services.loan_ledger._charges import contract_charges
from app.services.rate_period_engine import BalanceAnchor, replay_schedule
from app.utils.money import installment_interest
from tests._test_helpers import create_loan_account
from tests.oracles.loan_monthly_composition import rate_period

_MONTHLY = InterestAccrualConventionEnum.MONTHLY_1_12
_ORIGINATION = date(2026, 1, 15)
_PRINCIPAL = Decimal("100000.00")

# Anchored to THIS file, so the census grades the tree from any working directory.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Every module that calls the one interest producer, by the walk it is: the
#: posted ledger's replay, the contractual schedule (its replay rows and its
#: walk between two rate periods), the forward projection, and the payoff
#: strategy.  :class:`TestTheSpiesSeeEveryCaller` holds this to the modules
#: that import the producer, so a sixth caller cannot charge interest unspied.
_WALKS = {
    "replay": "app.services.loan_ledger._replay",
    "schedule": "app.services.rate_period_engine",
    "projection": "app.services.amortization_engine._projection",
    "strategy": "app.services.debt_strategy_service",
}


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


@dataclass(frozen=True)
class _Call:
    """What one walk handed the producer: the balance, the window and the convention."""

    balance: Decimal
    start: date
    end: date
    convention: object


@pytest.fixture(name="producer_calls")
def _producer_calls(monkeypatch, sentinel_convention):
    """Spy on the producer in every walk: record each call, then price it for real.

    The sentinel cannot be priced (the producer's rule table is keyed by real
    members, so it is a ``KeyError``), so the spy hands the producer
    ``MONTHLY_1_12`` IN ITS PLACE and only there: a walk that named a member
    itself passes that member through, and is recorded holding it.

    Returns:
        ``{walk: [_Call, ...]}`` for each walk in :data:`_WALKS`, filled as
        the test runs.
    """
    calls: dict[str, list[_Call]] = {walk: [] for walk in _WALKS}

    def spy_for(walk: str):
        """The producer, recording into *walk*'s list first."""
        def spy(balance, annual_rate, start, end, convention):
            calls[walk].append(_Call(balance, start, end, convention))
            return installment_interest(
                balance, annual_rate, start, end,
                _MONTHLY if convention is sentinel_convention else convention,
            )
        return spy

    for walk, module in _WALKS.items():
        monkeypatch.setattr(f"{module}.installment_interest", spy_for(walk))
    return calls


def _windows(calls: list[_Call]) -> list[tuple[date, date]]:
    """The windows a walk handed the producer, in order."""
    return [(call.start, call.end) for call in calls]


class TestTheConventionEntersOnce:
    """The stored id is read back once, and what it reads rides the periods."""

    def test_the_stored_id_reads_back_as_its_member(self):
        """A loan row holding ``monthly_1_12``'s id reads back as the member itself."""
        assert loan_resolver.accrual_convention(_params()) is _MONTHLY

    @pytest.mark.parametrize(
        "lacks", ["the-column", "a-listed-id"],
    )
    def test_an_id_naming_no_member_is_refused_where_it_is_read(self, lacks):
        """``None`` (a row built without the column) and an id the list lacks are refused.

        At the reading, before any period is built, with a message naming the
        id read: not at a walk's first charge, and not never on a path that
        charges nothing.
        """
        convention_id = (
            None if lacks == "the-column"
            else ref_cache.interest_accrual_convention_id(_MONTHLY) + 1
        )
        params = _params(interest_accrual_convention_id=convention_id)
        refusal = (
            f"interest_accrual_convention_id {convention_id!r} names no "
            "InterestAccrualConventionEnum member"
        )

        with pytest.raises(KeyError, match=refusal):
            loan_resolver.accrual_convention(params)
        with pytest.raises(KeyError, match=refusal):
            loan_resolver.resolve_periods(params, _origination_rate())

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


class TestEveryWalkHandsTheProducerTheLoansConvention:
    """Each walk asks the one producer under the loan's convention, over its own window.

    A fixed-rate loan from Jan 15, 2026, due the 1st, read through the loan's
    own periods and terms, so the convention reaches each walk from the one
    reading and from nowhere else.
    """

    def test_the_posted_replay_charges_each_installment_under_it(
        self, producer_calls, sentinel_convention,
    ):
        """``loan_ledger._replay``: one call per installment, over that installment's window.

        Payments Mar 1 and Apr 1 stand the stream's charges at Feb 1, Mar 1
        and Apr 1 (the first installment falls the month after origination),
        each opening on the due day before it: the windows abut.
        """
        calendar = LoanCalendar(
            origination_date=_ORIGINATION,
            payment_day=1,
            periods=loan_resolver.resolve_periods(_params(), _origination_rate()),
            escrow_lines=[],
        )
        stream = with_contract_charges(
            LoanEventStream(
                charges=(),
                payments=[
                    LoanCashEvent(
                        on_date=due, cash=Decimal("600.00"), source=None,
                        visible_on=due,
                    )
                    for due in (date(2026, 3, 1), date(2026, 4, 1))
                ],
                resets=[LoanResetEvent(
                    on_date=_ORIGINATION, balance=_PRINCIPAL, source=None,
                    is_opening=True, is_tracking_start=False,
                )],
            ),
            calendar,
        )
        replay_loan_events(Decimal("0.00"), stream)

        assert _windows(producer_calls["replay"]) == [
            (charge.opens_on, charge.on_date) for charge in stream.charges
        ] == [
            (date(2026, 1, 1), date(2026, 2, 1)),
            (date(2026, 2, 1), date(2026, 3, 1)),
            (date(2026, 3, 1), date(2026, 4, 1)),
        ]
        assert all(
            call.convention is sentinel_convention
            for call in producer_calls["replay"]
        )

    def test_the_schedule_replay_charges_each_payment_under_it(
        self, producer_calls, sentinel_convention,
    ):
        """``rate_period_engine._replay_payment_row``: each payment's installment window.

        One period, so building it walks nothing forward and every call
        recorded is a replayed row's: Feb 1's and Mar 1's.
        """
        periods = loan_resolver.resolve_periods(_params(), _origination_rate())
        assert producer_calls["schedule"] == []

        replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(balance=_PRINCIPAL, as_of_date=_ORIGINATION),
            payments=[
                PaymentDates(period_start=due, due_date=due, settled_on=due)
                for due in (date(2026, 2, 1), date(2026, 3, 1))
            ],
            payment_day=1,
            as_of=date(2026, 3, 1),
        )

        assert _windows(producer_calls["schedule"]) == [
            (date(2026, 1, 1), date(2026, 2, 1)),
            (date(2026, 2, 1), date(2026, 3, 1)),
        ]
        assert all(
            call.convention is sentinel_convention
            for call in producer_calls["schedule"]
        )

    def test_the_walk_between_two_periods_charges_under_it(
        self, producer_calls, sentinel_convention,
    ):
        """``rate_period_engine._amortize_forward``: the months from one period to the next.

        A rate change Apr 15 starts a second period, so the first is walked
        forward its three months, Jan 15 to Apr 15, to find the balance the
        second's payment amortizes; each month's window opens on the start's
        day.
        """
        loan_resolver.resolve_periods(_params(), [
            *_origination_rate(),
            RateChangeRecord(
                effective_date=date(2026, 4, 15), interest_rate=Decimal("0.07"),
            ),
        ])

        assert _windows(producer_calls["schedule"]) == [
            (date(2026, 1, 15), date(2026, 2, 15)),
            (date(2026, 2, 15), date(2026, 3, 15)),
            (date(2026, 3, 15), date(2026, 4, 15)),
        ]
        assert all(
            call.convention is sentinel_convention
            for call in producer_calls["schedule"]
        )

    def test_the_projection_charges_each_row_under_it(
        self, producer_calls, sentinel_convention,
    ):
        """``amortization_engine._projection``: each row's installment window.

        Three rows from Feb 1.  The first row's window opens Jan 1, the due
        day before it, never on the projection's own starting day: a window
        inside one month would charge nothing.
        """
        rows = project_forward(ProjectionInputs(
            starting_balance=_PRINCIPAL,
            starting_date=date(2026, 2, 1),
            remaining_months=3,
            payment_day=1,
            terms_schedule=loan_resolver.engine_terms(
                _params(), _origination_rate(),
            ),
        ))

        assert [row.payment_date for row in rows] == [
            date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1),
        ]
        assert _windows(producer_calls["projection"]) == [
            (date(2026, 1, 1), date(2026, 2, 1)),
            (date(2026, 2, 1), date(2026, 3, 1)),
            (date(2026, 3, 1), date(2026, 4, 1)),
        ]
        assert all(
            call.convention is sentinel_convention
            for call in producer_calls["projection"]
        )

    def test_the_payoff_strategy_charges_a_stored_loan_under_it(
        self, auth_client, seed_user, db, seed_periods_today,
        producer_calls, sentinel_convention,
    ):
        """``debt_strategy_service``: a stored loan's convention reaches the payoff projection.

        Through the page itself, so the path the convention travels is the
        production one: the loan row, the balance seam's terms, the page's
        :class:`~app.services.debt_strategy_service.DebtAccount`, the
        simulation.  Every call the page made, the strategy's and the
        balance read's alike, carried the sentinel.
        """
        create_loan_account(
            seed_user, db.session, name="Van", principal=Decimal("25000.00"),
            rate=Decimal("0.05500"), term=60, origination_date=date(2025, 1, 1),
            payment_day=15,
        )
        db.session.commit()

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200", "strategy": "avalanche",
        })

        assert resp.status_code == 200
        assert b"Van" in resp.data
        assert producer_calls["strategy"]
        assert producer_calls["replay"]
        assert all(
            call.convention is sentinel_convention
            for calls in producer_calls.values() for call in calls
        )

    def test_the_refinance_prices_its_hypothetical_loan_under_it(
        self, auth_client, seed_user, db, seed_periods_today,
        producer_calls, sentinel_convention,
    ):
        """The refinance calculator: the hypothetical loan keeps the refinanced loan's convention.

        The form asks for a new rate and term, never a new accrual rule, so
        the new loan's projection charges under what the stored loan reads
        back as.  Refinanced into $123,456.78 over 120 months at 5%: by hand
        r = 0.05 / 12 and the level payment is 123,456.78 x r / (1 - (1 +
        r)^-120) = $1,309.45, which the page shows, and the new loan's first
        row is the one projection call opening on that principal (the $25,000
        loan's own walks never hold it).  Every call the page made, the
        current loan's and the new loan's alike, carried the sentinel.
        """
        account = create_loan_account(
            seed_user, db.session, name="Van", principal=Decimal("25000.00"),
            rate=Decimal("0.05500"), term=60, origination_date=date(2025, 1, 1),
            payment_day=15,
        )
        db.session.commit()

        resp = auth_client.post(f"/accounts/{account.id}/loan/refinance", data={
            "new_rate": "5.0", "new_term_months": "120",
            "new_principal": "123456.78",
        })

        assert resp.status_code == 200
        assert b"$1,309.45" in resp.data
        assert [
            call.balance for call in producer_calls["projection"]
            if call.balance == Decimal("123456.78")
        ] == [Decimal("123456.78")]
        assert all(
            call.convention is sentinel_convention
            for calls in producer_calls.values() for call in calls
        )

    def test_the_payoff_strategy_charges_each_simulated_month(
        self, producer_calls, sentinel_convention,
    ):
        """``debt_strategy_service._accrue_interest``: month ``m`` is the month ending ``m`` months in.

        Started Jan 31, 2026, so the windows re-clamp from the meant day: the
        month to Feb 28, then the month to Mar 31.  A debt carrying the
        sentinel hands it over unchanged.
        """
        debt_strategy_service.calculate_strategy(
            debt_strategy_service.StrategyRequest(
                debts=[debt_strategy_service.DebtAccount(
                    account_id=1, name="Van", current_principal=Decimal("1000.00"),
                    interest_rate=Decimal("0.06"),
                    minimum_payment=Decimal("100.00"),
                    convention=sentinel_convention,
                )],
                extra_monthly=Decimal("0.00"),
                strategy=debt_strategy_service.STRATEGY_AVALANCHE,
                start_date=date(2026, 1, 31),
                max_horizon_months=2,
            ),
        )

        assert _windows(producer_calls["strategy"]) == [
            (date(2026, 1, 31), date(2026, 2, 28)),
            (date(2026, 2, 28), date(2026, 3, 31)),
        ]
        assert all(
            call.convention is sentinel_convention
            for call in producer_calls["strategy"]
        )


class TestTheSpiesSeeEveryCaller:
    """The walks spied on above are every module in ``app/`` that charges interest."""

    def test_every_importer_of_the_producer_is_a_walk_spied_on(self):
        """Census of ``app/``: who imports the producer, by either of its names.

        ``installment_interest`` and the unrounded ``accrued_interest`` beneath
        it; savings' own ``accrued_interest`` (``interest_projection``) is a
        different function, outside ``app.utils.money``, until plan#3356
        folds it in.  A module importing ``app.utils.money`` whole would hide
        from this, so the census refuses that shape too.
        """
        importers: set[str] = set()
        whole_module: set[str] = set()
        for path in (_REPO_ROOT / "app").rglob("*.py"):
            module = ".".join(
                path.relative_to(_REPO_ROOT).with_suffix("").parts,
            ).removesuffix(".__init__")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module == "app.utils.money":
                    if {alias.name for alias in node.names} & {
                        "installment_interest", "accrued_interest",
                    }:
                        importers.add(module)
                elif isinstance(node, ast.ImportFrom) and node.module == "app.utils":
                    if any(alias.name == "money" for alias in node.names):
                        whole_module.add(module)
                elif isinstance(node, ast.Import):
                    if any(alias.name == "app.utils.money" for alias in node.names):
                        whole_module.add(module)

        assert importers == set(_WALKS.values())
        assert not whole_module
