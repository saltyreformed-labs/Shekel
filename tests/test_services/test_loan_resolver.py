"""Tests for ``app.services.loan_resolver`` (Commit 13 / E-18).

These tests pin the resolver's behavior with hand-computed Decimal
expectations.  The resolver is a pure function, so every test
constructs a duck-typed loan-params object and a list of
anchor-event-shaped objects directly -- no database fixtures are
needed.  This keeps the tests fast (the full file runs in well
under a second) and isolates the resolver's logic from the rest of
the system.

Test IDs map to the Commit 13 plan in
``docs/audits/financial_calculations/remediation_plan.md`` section 9.
Every monetary expectation carries the arithmetic in a comment so a
future reader can verify the assertion by hand.
"""

import inspect
import io
import pathlib
import tokenize
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import InterestAccrualConventionEnum
from app.services import loan_resolver
from app.services.amortization_engine import (
    PaymentDates,
    PaymentRecord,
    RateChangeRecord,
)
from app.services.loan_resolver import (
    ConfirmedLedgerView,
    LoanInputs,
    compute_payoff_scenarios,
    current_rate_baseline,
    resolve_loan,
    resolve_periods,
)
from app.services.loan_resolver._periods import _replay_from_anchor
from app.utils.dates import has_settled_by
from app.services.installment_calendar import monthly_due_date
from app.utils.dates import add_months
from app.utils.money import round_money


def _loan_resolver_package_source() -> str:
    """Concatenated source of every module in the ``loan_resolver`` package.

    The E-18 resolver was split into the ``app/services/loan_resolver/``
    package (Phase-3 pylint cleanup), so ``inspect.getsource(loan_resolver)``
    now returns only ``__init__.py``.  The purity / rounding / no-engine
    source guards must scan every sub-module where the resolver's code
    actually lives, so they read the package directory directly.
    """
    package_dir = pathlib.Path(loan_resolver.__file__).parent
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package_dir.glob("*.py"))
    )


# -- Duck-typed fixtures ----------------------------------------------------


@dataclass
class FakeLoanParams:
    """Minimal duck-type for LoanParams.

    The resolver reads only the listed attributes; this avoids a
    DB-bound LoanParams instance and keeps the tests pure.
    """

    origination_date: date
    term_months: int
    original_principal: Decimal
    interest_rate: Decimal
    payment_day: int
    is_arm: bool = False
    arm_first_adjustment_months: int | None = None
    arm_adjustment_interval_months: int | None = None
    # A real row's NOT NULL column (plan step recurrence:R16-d); every loan
    # holds the list's one member, so the fake states it rather than each test.
    interest_accrual_convention_id: int = field(
        default_factory=lambda: ref_cache.interest_accrual_convention_id(
            InterestAccrualConventionEnum.MONTHLY_1_12,
        ),
    )


@dataclass
class FakeAnchorEvent:
    """Minimal duck-type for a :class:`LoanAnchorFact`.

    Carries the three terms of the resolver's chronology key
    (:func:`app.services.loan_resolver.select_latest_anchor`).  ``created_at``
    and ``event_id`` both default to a deterministic value so the latest-anchor
    pick is stable in every test that does not care about a tie; a test that
    DOES construct one sets them explicitly.
    """

    anchor_date: date
    anchor_balance: Decimal
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    event_id: int = 0


def _arm_400k_params() -> FakeLoanParams:
    """Return a 5/5 ARM at $400k / 6% / 360 months from 2026-01-01.

    Matches the 05_symptoms.md Symptom #4 worked example exactly so
    the hand-computed constant payment of $2,398.20 ties out.
    """
    return FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("400000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
        is_arm=True,
        arm_first_adjustment_months=60,
    )


def _origination_anchor(
    params: FakeLoanParams, balance: Decimal | None = None,
) -> FakeAnchorEvent:
    """Return the origination anchor Commit 12 backfilled for *params*."""
    return FakeAnchorEvent(
        anchor_date=params.origination_date,
        anchor_balance=balance or params.original_principal,
    )


def _origination_rate_change(params: FakeLoanParams) -> RateChangeRecord:
    """Return the origination (period-0) ``RateChangeRecord`` for *params*.

    DH-#56 retired ``LoanParams.interest_rate``; the resolver now derives a
    loan's base / period-0 rate from the earliest entry in its rate-change
    feed -- the origination :class:`RateHistory` row every loan carries
    (effective at ``origination_date``).  These resolver tests build
    duck-typed :class:`FakeLoanParams` rather than DB-bound ORM rows, so the
    in-memory analogue of that origination row is a
    :class:`RateChangeRecord` effective at ``origination_date`` carrying the
    params' ``interest_rate``.  ``_origination_rate`` raises ``ValueError``
    on an empty feed, so every ``resolve_loan`` / ``compute_payoff_scenarios``
    call must include this record.  The rate value is unchanged from the
    retired column, so every hand-computed expectation stays byte-identical.
    """
    return RateChangeRecord(
        effective_date=params.origination_date,
        interest_rate=params.interest_rate,
        monthly_pi=None,
    )


def _rate_feed(
    params: FakeLoanParams,
    rate_changes: list[RateChangeRecord] | None = None,
) -> list[RateChangeRecord]:
    """Return the loan's full rate-change feed including the origination rate.

    Prepends the origination (period-0) :class:`RateChangeRecord` (see
    :func:`_origination_rate_change`) to any later ARM ``rate_changes`` the
    test supplies.  When the test passes no later changes the feed is just
    the origination rate -- enough for the resolver to resolve period 0.
    The origination rate equals the retired ``LoanParams.interest_rate``, so
    period 0 (and every downstream expectation) is byte-identical.
    """
    feed = [_origination_rate_change(params)]
    if rate_changes:
        feed.extend(rate_changes)
    return feed


def _replay_balance(inputs: LoanInputs, as_of: date) -> Decimal:
    """Return the anchor + confirmed-payment replay balance for *inputs*.

    The window the deleted ``LoanState.current_balance`` carried (plan step
    D2a): the same production derivation one level down
    (:func:`app.services.loan_resolver._periods._replay_from_anchor`, which
    still seeds the schedule composer's starting state on the unseeded path),
    so every hand-computed pin below keeps its value while the resolver's
    public bundle carries no balance.  Rounded to the cent exactly as the
    deleted field was.
    """
    periods = resolve_periods(inputs.loan_params, inputs.rate_changes)
    return round_money(
        _replay_from_anchor(
            anchor_events=inputs.anchor_events,
            periods=periods,
            payments=[payment.dates for payment in inputs.payments or []],
            payment_day=inputs.loan_params.payment_day,
            as_of=as_of,
        ).balance_as_of
    )


# -- C13-1 -- ARM payment constant across the fixed-rate window -------------


def test_arm_payment_constant_in_fixed_window():
    """C13-1: 5/5 ARM payment is byte-identical for every month in [0, 60).

    Hand-computed contractual payment for the 5/5 ARM at $400k/6%/
    360mo (per 05_symptoms.md:957-961):

        i = 0.06 / 12 = 0.005
        (1.005)^360 = 6.022575
        denom = 1 - (1.005)^(-360) = 1 - 0.166042 = 0.833958
        M* = 400000 * 0.005 / 0.833958 = $2,398.20

    Pre-fix, the engine's ARM scalar site re-amortized the frozen
    stored principal over a calendar-shrinking ``n``, so the
    displayed Monthly P&I drifted upward every month.  The
    resolver computes the payment once from the anchor balance and
    holds it constant for every ``as_of`` in the window
    (E-02 invariant; symptom #4 fix).
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)
    expected = Decimal("2398.20")

    payments_observed = set()
    for month_offset in range(60):
        as_of = add_months(params.origination_date, month_offset)
        state = resolve_loan(
            LoanInputs(params, [anchor], None, _rate_feed(params)), as_of,
        )
        payments_observed.add(state.monthly_payment)

    assert payments_observed == {expected}, (
        f"E-02 violation: payment varied across the fixed window: "
        f"{sorted(payments_observed)}"
    )


# -- C13-2 -- ARM no creep month 24 vs month 25 -----------------------------


def test_arm_no_creep_month_24_vs_25():
    """C13-2: Resolver payment at month 24 == month 25 (no creep).

    Pre-fix the engine returned $2,460.45 at month 24 and $2,463.28
    at month 25 (hand-recomputed in 05_symptoms.md:965-973 -- both
    differ from and exceed the correct constant $2,398.20).  The
    resolver returns one constant for every as_of in the window.
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)

    state_24 = resolve_loan(
        LoanInputs(params, [anchor], None, _rate_feed(params)),
        add_months(params.origination_date, 24),
    )
    state_25 = resolve_loan(
        LoanInputs(params, [anchor], None, _rate_feed(params)),
        add_months(params.origination_date, 25),
    )

    # Byte-identical Decimal comparison (not numeric equality with
    # different scales).  The pre-fix values $2,460.45 / $2,463.28
    # both differed from $2,398.20 and from each other; we assert
    # the resolver pins them to the same correct value.
    assert state_24.monthly_payment == Decimal("2398.20")
    assert state_25.monthly_payment == Decimal("2398.20")
    assert state_24.monthly_payment == state_25.monthly_payment


# -- DH-#56 -- current_rate is the resolver-derived rate in effect today -----


def test_current_rate_is_rate_at_today_not_a_stored_scalar():
    """DH-#56: ``state.current_rate`` is the rate in effect on ``as_of``.

    A 5/5 ARM originated at 6% with a recorded adjustment to 7% effective at
    its first reset (month 60, 2031-01-01).  Inside the fixed-rate window the
    current rate is the 6% origination rate; after the reset it is the 7%
    rate now in effect.  This is the headline DH-#56 fix: the retired
    ``LoanParams.interest_rate`` mirror drifted to the LATEST recorded rate on
    every change (corrupting period 0 for a backdated / out-of-order change),
    whereas ``current_rate`` is resolved per-date from the rate-period series,
    so it always reports the rate actually in effect.

    Revert-proof: a single stored scalar cannot be BOTH 6% and 7%, so any
    regression that re-sourced ``current_rate`` from one column would fail
    one of the two assertions.
    """
    params = _arm_400k_params()  # 2026-01-01, 6%, 5/5 ARM
    anchor = _origination_anchor(params)
    reset = RateChangeRecord(
        effective_date=date(2031, 1, 1),  # month 60: the first ARM reset
        interest_rate=Decimal("0.07"),
        monthly_pi=None,
    )
    feed = _rate_feed(params, [reset])

    in_window = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2027, 1, 1),
    )
    assert in_window.current_rate == Decimal("0.06")

    after_reset = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2031, 6, 1),
    )
    assert after_reset.current_rate == Decimal("0.07")


def test_current_rate_baseline_equals_the_resolved_current_rate():
    """``current_rate_baseline`` == ``resolve_loan(...).current_rate`` (plan C4).

    The standalone schedule route reads the loan's current rate off this cheap
    rate-period accessor rather than a full resolve (so it does not derive its
    schedule twice).  It must return exactly the same rate the resolver would --
    the governing rate period's annual rate -- BOTH inside an ARM's fixed-rate
    window (the 6% origination rate) and after its first reset (the recorded 7%),
    where a single stored scalar could not be both.  A regression that re-sourced
    the rate from anything but the per-date rate-period series fails one clause.
    """
    params = _arm_400k_params()  # 2026-01-01, 6%, 5/5 ARM
    reset = RateChangeRecord(
        effective_date=date(2031, 1, 1),  # month 60: the first ARM reset
        interest_rate=Decimal("0.07"),
        monthly_pi=None,
    )
    feed = _rate_feed(params, [reset])
    anchor = _origination_anchor(params)

    for as_of, expected in [
        (date(2027, 1, 1), Decimal("0.06")),   # inside the fixed-rate window
        (date(2031, 6, 1), Decimal("0.07")),   # after the reset
    ]:
        baseline = current_rate_baseline(params, feed, as_of)
        resolved = resolve_loan(
            LoanInputs(params, [anchor], None, feed), as_of,
        ).current_rate
        assert baseline == expected
        assert baseline == resolved


# -- C13-3 -- confirmed payment reduces balance -----------------------------


def test_confirmed_payment_reduces_balance():
    """C13-3 (re-pinned): a confirmed payment reduces the balance by the
    SCHEDULED principal, independent of the cash amount paid.

    Re-pinned under the decided contractual-schedule model (CLAUDE rule
    5 exception; the developer chose "each confirmed payment reduces
    principal by period P&I - interest; deliberate extra principal is an
    explicit event").  The prior assertion ($299,611.64) reduced the
    balance by the cash amount ($1,888.36 - interest), so escrow or an
    overpayment bundled into the transfer leaked into principal.  Now
    only the payment's occurrence (date) matters; its amount does not.

    Setup: $300k fixed-rate, 6%, 360mo, origination 2026-01-01.  One
    confirmed payment on 2026-02-15 (cash $1,888.36, deliberately above
    the contractual P&I to show the excess is ignored):

        period P&I = amortize(300000, 0.06, 360) = 1,798.65
        interest   = 300000 * 0.005 = 1,500.00
        principal  = 1,798.65 - 1,500.00 = 298.65
        balance    = 300,000.00 - 298.65 = 299,701.35

    The $89.71 paid above the contractual P&I is NOT auto-applied to
    principal (that requires an explicit prepayment event); the balance
    follows the contractual schedule.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
        is_arm=False,
    )
    anchor = _origination_anchor(params)
    payment = PaymentRecord(
        PaymentDates(
            period_start=date(2026, 2, 15),
            due_date=monthly_due_date(date(2026, 2, 15), 1),
            settled_on=date(2026, 2, 15),
        ),
        amount=Decimal("1888.36"),
    )

    inputs = LoanInputs(params, [anchor], [payment], _rate_feed(params))

    assert _replay_balance(inputs, date(2026, 3, 1)) == Decimal("299701.35")


# -- C13-4 -- projected payment is not replayed -----------------------------


def test_projected_payment_not_replayed():
    """C13-4: An unconfirmed (projected) payment leaves the balance unchanged.

    Future commitments are not historical fact and must not reduce
    the resolved principal.  Symptom #3 is closed precisely because
    only confirmed payments count.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)
    projected = PaymentRecord(
        PaymentDates(
            period_start=date(2026, 2, 15),
            due_date=monthly_due_date(date(2026, 2, 15), 1),
            settled_on=None,
        ),
        amount=Decimal("1888.36"),
    )

    inputs = LoanInputs(params, [anchor], [projected], _rate_feed(params))

    # No confirmed payments; the replay balance equals the anchor balance
    # (= original_principal for the Commit-12 origination anchor).
    assert _replay_balance(inputs, date(2026, 3, 1)) == Decimal("300000.00")


def test_a_projected_payment_does_not_ride_the_resolver_schedule():
    """R7d-g-3: the resolver's schedule is the CONTRACT's; the plan is the seam's.

    Until plan step R7d-g-3 this pinned step 8 the other way round: a
    projected recurring payment above contractual rode forward through
    ``monthly_override`` and appeared in ``LoanState.schedule`` as the
    committed trajectory.  Ruling **R-R88** made the balance seam's plan fold
    the one walk of what a loan is projected to PAY, and this schedule the
    contract's: every reader of it takes a DATE (the net-worth trend's
    first-payment gate, the equity chart's tracking start), never a balance.
    So a $2,500 projected March payment leaves the resolver's March row at the
    contractual $1,798.65, identical to the no-payment resolve, and the
    replayed balance still stays at the origination anchor (the C13-4
    invariant: a projected payment is not replayed).
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)
    projected_overpay = PaymentRecord(
        PaymentDates(
            period_start=date(2026, 3, 1),
            due_date=monthly_due_date(date(2026, 3, 1), 1),
            settled_on=None,
        ),
        amount=Decimal("2500.00"),
    )

    planned_inputs = LoanInputs(
        params, [anchor], [projected_overpay], _rate_feed(params),
    )
    planned = resolve_loan(planned_inputs, date(2026, 1, 15))
    contractual = resolve_loan(
        LoanInputs(params, [anchor], [], _rate_feed(params)),
        date(2026, 1, 15),
    )

    def _march(state):
        return next(
            row for row in state.schedule
            if row.payment_date == date(2026, 3, 1)
        )

    assert _march(planned).payment == Decimal("1798.65")
    assert _march(planned) == _march(contractual)
    assert planned.schedule == contractual.schedule
    # The projected payment is not replayed: the balance is the anchor's.
    assert _replay_balance(planned_inputs, date(2026, 1, 15)) == Decimal("300000.00")



# -- C13-5 -- fixed-rate, three confirmed payments --------------------------


def test_fixed_rate_replays_from_origination_anchor():
    """C13-5: Three confirmed contractual payments cumulatively reduce balance.

    Setup: $300k / 6% / 360mo; contractual payment = $1,798.65
    (amortize($300k, 0.06, 360)).  Three confirmed payments at the
    contractual amount in months 2, 3, 4.  Hand-computed cumulative
    reduction:

        m1: i = 300000.00 * 0.005   = 1500.00; p = 298.65;
            bal = 299701.35
        m2: i = 299701.35 * 0.005   = 1498.51 (HALF_UP); p = 300.14;
            bal = 299401.21
        m3: i = 299401.21 * 0.005   = 1497.01 (HALF_UP); p = 301.64;
            bal = 299099.57
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)
    payments = [
        PaymentRecord(PaymentDates(date(2026, 2, 1), monthly_due_date(date(2026, 2, 1), 1), date(2026, 2, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 3, 1), monthly_due_date(date(2026, 3, 1), 1), date(2026, 3, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 4, 1), monthly_due_date(date(2026, 4, 1), 1), date(2026, 4, 1)), Decimal("1798.65")),
    ]

    inputs = LoanInputs(params, [anchor], payments, _rate_feed(params))

    assert _replay_balance(inputs, date(2026, 5, 1)) == Decimal("299099.57")


# -- C13-6 -- trueup anchor resets the replay -------------------------------


def test_anchor_trueup_resets_replay():
    """C13-6: A later user_trueup anchor makes pre-trueup payments irrelevant.

    Setup: $300k / 6% / 360mo.  Two confirmed payments BEFORE a
    user_trueup, one confirmed payment AFTER the trueup.  The
    trueup balance ($250,000) is intentionally far from the
    engine's from-origination projection (~$299,700) so the test
    proves the resolver starts the post-trueup replay from the
    trueup balance, not from the engine's projection.

    Hand-computed post-trueup arithmetic:

        anchor_balance = 250,000.00 (trueup)
        i = 250000.00 * 0.005   = 1250.00
        p = 1798.65 - 1250.00   = 548.65
        balance = 250000.00 - 548.65 = 249,451.35

    Pre-trueup payments (2026-02 and 2026-03) are filtered out by
    replay_schedule's due-date boundary -- their monthly due dates
    (payment_day=1, so 2026-02-01 and 2026-03-01) fall on or before the
    2026-04-01 trueup anchor -- and never enter the replay.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    origination_anchor = FakeAnchorEvent(
        anchor_date=date(2026, 1, 1),
        anchor_balance=Decimal("300000.00"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    trueup_anchor = FakeAnchorEvent(
        anchor_date=date(2026, 4, 1),
        anchor_balance=Decimal("250000.00"),
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    payments = [
        # Pre-trueup -- filtered out by the resolver.
        PaymentRecord(PaymentDates(date(2026, 2, 1), monthly_due_date(date(2026, 2, 1), 1), date(2026, 2, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 3, 1), monthly_due_date(date(2026, 3, 1), 1), date(2026, 3, 1)), Decimal("1798.65")),
        # Post-trueup -- replayed.
        PaymentRecord(PaymentDates(date(2026, 5, 1), monthly_due_date(date(2026, 5, 1), 1), date(2026, 5, 1)), Decimal("1798.65")),
    ]

    inputs = LoanInputs(
        params,
        [origination_anchor, trueup_anchor],
        payments,
        _rate_feed(params),
    )

    assert _replay_balance(inputs, date(2026, 6, 1)) == Decimal("249451.35")


def test_payment_due_after_trueup_replays_though_pay_period_started_before():
    """Regression: a payment keyed to a pay-period start before a mid-period
    true-up still replays, because the boundary uses its real due date.

    The production bug (mortgage account 3): a balance true-up entered
    2026-05-22 ($177,829.83, the pre-payment statement balance) lands one
    day after the biweekly pay period that begins 2026-05-21 and carries
    the 2026-06-01 mortgage payment.  The PaymentRecord is keyed to the
    pay-period START (05-21), so the old "payment_date > anchor_date"
    boundary stranded it (05-21 is not after 05-22) -- the loan card froze
    at the anchor and marking the payment paid never moved it.  Keyed to
    its true monthly DUE date (payment_day=1 -> 06-01), it is correctly
    after the anchor and replays.

    Setup: $300k / 6% / 360mo, contractual P&I $1,798.65.  Two earlier
    confirmed payments (keyed 03-26 -> due 04-01 and 04-23 -> due 05-01)
    are already baked into the trued-up balance and stay excluded; only
    the 06-01 payment replays:

        anchor    = 177,829.83 (trueup, 2026-05-22)
        i         = 177829.83 * 0.06/12 = 889.15 (889.14915 -> HALF_UP)
        p         = 1798.65 - 889.15    = 909.50
        balance   = 177829.83 - 909.50  = 176,920.33
    """
    params = FakeLoanParams(
        origination_date=date(2020, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    origination_anchor = FakeAnchorEvent(
        anchor_date=date(2020, 1, 1),
        anchor_balance=Decimal("300000.00"),
        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    trueup_anchor = FakeAnchorEvent(
        anchor_date=date(2026, 5, 22),
        anchor_balance=Decimal("177829.83"),
        created_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    payments = [
        # Already reflected in the trueup balance (due 04-01, 05-01).
        PaymentRecord(PaymentDates(date(2026, 3, 26), monthly_due_date(date(2026, 3, 26), 1), date(2026, 3, 26)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 4, 23), monthly_due_date(date(2026, 4, 23), 1), date(2026, 4, 23)), Decimal("1798.65")),
        # Keyed to its pay-period start 05-21; due 06-01, after the
        # 05-22 trueup -- must replay.
        PaymentRecord(PaymentDates(date(2026, 5, 21), monthly_due_date(date(2026, 5, 21), 1), date(2026, 5, 21)), Decimal("1798.65")),
    ]

    inputs = LoanInputs(
        params,
        [origination_anchor, trueup_anchor],
        payments,
        _rate_feed(params),
    )

    # The 06-01 payment reduced the balance; the card is NOT frozen at the
    # anchor (the bug) and the two pre-trueup payments did not double-count.
    replayed = _replay_balance(inputs, date(2026, 6, 2))
    assert replayed == Decimal("176920.33")
    assert replayed != trueup_anchor.anchor_balance


# -- C13-7 -- rate change after window applied ------------------------------


def test_rate_change_after_window_applied():
    """C13-7 (re-pinned): post-adjustment ARM holds the period recast, constant.

    Re-pinned under the rate-period model (CLAUDE rule 5 exception; the
    developer chose to hold the ARM payment constant within each
    fixed-rate period).  The prior assertion pinned $2,830.61 -- the
    payment from re-amortizing the FROZEN original $400,000 over a
    calendar-shrinking term every month (the symptom-#4 creep).  That
    behavior is gone.

    Setup: 5/5 ARM, $400k / 6% / 360mo, origination 2026-01-01, first
    adjustment at month 60 (2031-01-01), rate change to 7% effective
    2031-01-01.  No confirmed payments.

    For any as_of inside the second period, the monthly payment is the
    period's level recast: amortize(the contractual balance at month 60,
    7%, 300).  A $400k/6%/360 loan paid on schedule sits at ~$372,217 at
    month 60, and amortize(~$372,217, 7%, 300) = $2,630.76.  Critically
    it is held CONSTANT for every as_of in the period -- no creep.
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)
    rate_changes = [
        RateChangeRecord(
            effective_date=date(2031, 1, 1),
            interest_rate=Decimal("0.07"),
        ),
    ]

    feed = _rate_feed(params, rate_changes)
    state_feb = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2031, 2, 1),
    )
    state_later = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2033, 6, 1),
    )

    # Period recast of the reduced month-60 balance at 7% (NOT the old
    # $2,830.61 re-amortization of the frozen original principal).
    assert state_feb.monthly_payment == Decimal("2630.76")
    # Held constant across the period -- the anti-creep guarantee.
    assert state_feb.monthly_payment == state_later.monthly_payment


# -- C13-8 -- resolver module is pure (no Flask, no db.session) -------------


def test_resolver_is_pure_no_flask_no_db():
    """C13-8: Source of ``loan_resolver`` contains no Flask or db.session refs.

    Static guard against the services-boundary regression where a
    later refactor sneaks a ``from flask import request`` or a
    ``db.session.query(...)`` into the resolver.  The resolver MUST
    be a pure function: takes plain data, returns plain data.
    Adding I/O would silently break callers in test or task
    contexts where no app context is available.

    Strips string literals and comments before scanning so that
    documentation mentions of "db.session" or "Flask" (which are
    legitimate prose explaining why the resolver avoids them) do
    not trip the guard.  Only executable code is inspected.
    """
    source = _loan_resolver_package_source()
    code_tokens = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        # Exclude string literals (including docstrings) and comments;
        # those are prose, not code paths.
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        # Python 3.12+: f-string substring tokens carry literal text
        # the same way STRING does.  Skip them too.
        if tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
            continue
        code_tokens.append(tok.string)
    code_only = " ".join(code_tokens)

    forbidden = (
        "from flask",
        "import flask",
        "current_user",
        "db.session",
        "request.",
        "session[",
    )
    for marker in forbidden:
        assert marker not in code_only, (
            f"the loan_resolver package contains forbidden marker "
            f"{marker!r} in executable code; the resolver "
            f"must remain pure."
        )


# -- C13-9 -- rounding via round_money only ---------------------------------


def test_resolver_rounds_via_round_money_only():
    """C13-9: Resolver source uses ``round_money`` for its own rounding.

    The resolver may call helper functions (e.g. the engine's
    ``calculate_monthly_payment``) that quantize internally; what
    this test guards against is the resolver itself reaching
    ``Decimal.quantize`` directly, which would silently inherit
    Python's default ``ROUND_HALF_EVEN`` and drift one cent at
    half-cent boundaries.  ``round_money`` is the only boundary
    rounding called from this module.
    """
    source = _loan_resolver_package_source()
    assert ".quantize(" not in source, (
        "the loan_resolver package reached .quantize directly; route "
        "through app.utils.money.round_money instead (E-26 boundary rule)."
    )
    assert "round_money(" in source, (
        "the loan_resolver package must import and use round_money."
    )


# -- C13-10 -- zero-rate loan -----------------------------------------------


def test_zero_rate_loan_payment_is_principal_over_n():
    """C13-10: Zero-rate loan returns payment = principal / n; no div-by-zero.

    A 0% loan amortizes as equal-principal payments with no
    interest accrual.  The engine's ``calculate_monthly_payment``
    handles the ``annual_rate <= 0`` branch as
    ``principal / remaining_months``.  The resolver must surface
    this without dividing by zero or producing a ``Decimal('NaN')``.

    Setup: $12,000 / 0% / 12 months.  Expected payment = $1,000.00.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=12,
        original_principal=Decimal("12000.00"),
        interest_rate=Decimal("0"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)

    inputs = LoanInputs(params, [anchor], None, _rate_feed(params))
    state = resolve_loan(inputs, date(2026, 2, 1))

    assert state.monthly_payment == Decimal("1000.00")
    # Balance unchanged with no confirmed payments.
    assert _replay_balance(inputs, date(2026, 2, 1)) == Decimal("12000.00")


# -- C13-11 -- payoff date and total_interest -------------------------------


def test_payoff_date_and_total_interest():
    """C13-11 (re-pinned at C6): hand-computable payoff date and total interest.

    Setup: small fixed-rate loan $10,000 / 6% / 12 months from
    2026-01-01.  Contractual payment =
    amortize(10000, 0.06, 12) = $860.66 (HALF_UP from the exact
    $860.6643...).

    Hand-computed schedule (per-row interest is
    ``balance * 0.005`` quantized HALF_UP, principal is
    ``860.66 - interest`` in rows 1-11, balance reduced accordingly):

        r1  i=50.00 p=810.66 bal=9189.34
        r2  i=45.95 p=814.71 bal=8374.63
        r3  i=41.87 p=818.79 bal=7555.84
        r4  i=37.78 p=822.88 bal=6732.96
        r5  i=33.66 p=827.00 bal=5905.96
        r6  i=29.53 p=831.13 bal=5074.83
        r7  i=25.37 p=835.29 bal=4239.54
        r8  i=21.20 p=839.46 bal=3400.08
        r9  i=17.00 p=843.66 bal=2556.42
        r10 i=12.78 p=847.88 bal=1708.54
        r11 i= 8.54 p=852.12 bal= 856.42
        r12 i= 4.28 p=856.42 bal=   0.00  (final row absorbs residue)

    Twelve rows ending 2027-01-01.  Total interest = $327.96
    (sum of rows 1-12; cross-check: also equals the sum the
    pre-C6 engine produced because the pre-C6 13-row schedule had
    interest=$0.00 on its phantom $0.04 residue row, so the total
    is unchanged).

    Re-pinning context (per
    ``remediation_follow_up_common.md`` Apply-rule 4): the pre-C6
    ``generate_schedule`` produced a 13-row schedule ending
    2027-02-01 with row 13 being a phantom payment=$0.04 row
    that absorbed sub-penny residue left by rounding the
    contractual payment to two places.  That residue row was a
    math artifact of ``generate_schedule``'s ``max_months =
    remaining_months + term_months`` slack; it was never user-
    facing-correct (a 12-month loan does not "pay off in month
    13").  ``project_forward`` (Commit 2 primitive; used here via
    ``compute_payoff_scenarios`` in :func:`resolve_loan` after
    Commit 6) forces ``is_final = month_num == remaining_months``
    on the final scheduled month so the last row absorbs residue
    into a slightly larger final payment ($860.70 = $856.42
    principal + $4.28 interest).  This matches real-lender
    practice (the final scheduled payment fully retires the loan
    within the contractual term).  See
    ``docs/plans/2026-05-21-amortization-engine-split-implementation.md``
    Section 9 Commit 6 for the architectural finding.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=12,
        original_principal=Decimal("10000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)

    state = resolve_loan(
        LoanInputs(params, [anchor], None, _rate_feed(params)),
        date(2026, 2, 1),
    )

    # The schedule's last row IS the contractual payoff (the resolver no longer
    # publishes a payoff_date field -- plan C8d moved the payoff to the balance
    # seam's fold-to-zero; this pins the SCHEDULE, which is what this test is
    # about).
    assert state.schedule[-1].payment_date == date(2027, 1, 1)
    assert state.total_interest == Decimal("327.96")
    # Twelve rows: the final row absorbs residue into the
    # contractual term rather than emitting a phantom 13th row.
    assert len(state.schedule) == 12
    assert state.schedule[-1].remaining_balance == Decimal("0.00")


# -- Defensive coverage beyond the plan's enumerated cases ------------------


def test_empty_anchor_events_raises_value_error():
    """The anchor-fact guarantee is structural: empty raises loud.

    A configured loan's origination anchor is SYNTHESIZED from its
    immutable params (``loan_loaders.load_loan_anchor_facts``), so an
    empty anchor list means the caller bypassed the shared loader;
    silently producing a "no anchor, project from origination" answer
    would mask the bug.  The resolver raises ValueError to surface it.
    """
    params = _arm_400k_params()
    with pytest.raises(ValueError, match="at least one anchor fact"):
        resolve_loan(
            LoanInputs(params, [], None, _rate_feed(params)),
            date(2026, 6, 1),
        )


def test_latest_anchor_breaks_tie_by_created_at():
    """Two anchors on the same date: latest created_at wins.

    Mirrors the ORM ``backref(order_by="anchor_date DESC, created_at DESC")``
    so a same-day correction (operator typed the wrong number, hit
    save, fixed it, saved again -- both with the same anchor_date)
    deterministically prefers the later row.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    earlier = FakeAnchorEvent(
        anchor_date=date(2026, 6, 1),
        anchor_balance=Decimal("280000.00"),
        created_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
    )
    later = FakeAnchorEvent(
        anchor_date=date(2026, 6, 1),
        anchor_balance=Decimal("275000.00"),
        created_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
                   + timedelta(seconds=5),
    )

    inputs = LoanInputs(params, [earlier, later], None, _rate_feed(params))

    # Latest anchor's balance is returned (no confirmed payments
    # to replay forward from it).
    assert _replay_balance(inputs, date(2026, 7, 1)) == Decimal("275000.00")


def test_loan_state_is_frozen():
    """LoanState is a frozen dataclass; consumers cannot mutate the snapshot.

    The resolver returns a snapshot the caller renders; mutating
    fields between consumers would silently produce divergent
    surfaces (the bug E-18 exists to prevent).  ``frozen=True``
    on the dataclass enforces this at runtime.
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)
    state = resolve_loan(
        LoanInputs(params, [anchor], None, _rate_feed(params)),
        date(2026, 6, 1),
    )

    with pytest.raises(AttributeError):
        # Type-checked at runtime by @dataclass(frozen=True).
        state.monthly_payment = Decimal("0")  # type: ignore[misc]


def test_arm_trueup_does_not_change_payment():
    """A balance true-up corrects the balance only -- the payment is unchanged.

    Re-pinned under the decided "balance-only true-up" behavior (CLAUDE
    rule 5 exception; developer decision).  The prior test asserted a
    true-up BORN a new constant payment ($2,337.47, from re-amortizing
    the trued-up $380,000); that coupling is exactly what made the
    displayed payment wander every time the user corrected the balance,
    and it is gone.  The monthly P&I is the current rate period's
    contractual level payment, independent of the anchor balance.

    Setup: 5/5 ARM, $400k/6%/360mo, origination 2026-01-01.  A
    user_trueup at 2028-01-01 (month 24) sets the balance to $380,000.
    The origination-period P&I is amortize($400,000, 6%, 360) =
    $2,398.20 and stays that for every in-period as_of; only the
    balance reflects the true-up.
    """
    params = _arm_400k_params()
    origination_anchor = _origination_anchor(params)
    trueup_anchor = FakeAnchorEvent(
        anchor_date=date(2028, 1, 1),
        anchor_balance=Decimal("380000.00"),
        created_at=datetime(2028, 1, 1, tzinfo=timezone.utc),
    )

    # Resolve at two as_of dates past the trueup.
    inputs = LoanInputs(
        params, [origination_anchor, trueup_anchor], None,
        _rate_feed(params),
    )
    state_a = resolve_loan(inputs, date(2028, 6, 1))
    state_b = resolve_loan(inputs, date(2030, 6, 1))

    # Payment unchanged by the true-up: the origination-period level P&I.
    assert state_a.monthly_payment == Decimal("2398.20")
    assert state_a.monthly_payment == state_b.monthly_payment
    # The true-up DID move the balance (no confirmed payments after it).
    assert _replay_balance(inputs, date(2028, 6, 1)) == Decimal("380000.00")


def test_arm_second_period_uses_recorded_recast_held_constant():
    """A 5/5 ARM's second fixed period uses its recorded recast, held constant.

    Exercises ``arm_adjustment_interval_months`` (now load-bearing) and
    the recorded-recast path end to end through ``resolve_loan``: the
    lender's stated P&I at the adjustment is held flat for the whole
    period, and the origination period is unaffected.  This is the
    production shape for a mid-life 5/5 ARM whose current P&I was
    recorded at setup.

    Setup: 5/5 ARM, $400k/6%/360mo, origination 2026-01-01, first
    adjustment at month 60 then every 60 (interval).  At 2031-01-01 the
    rate adjusts to 7% with a recorded recast P&I of $2,500.00.  No
    confirmed payments.  The second period is [2031-01-01, 2036-01-01).
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("400000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
        is_arm=True,
        arm_first_adjustment_months=60,
        arm_adjustment_interval_months=60,
    )
    anchor = _origination_anchor(params)
    rate_changes = [
        RateChangeRecord(
            effective_date=date(2031, 1, 1),
            interest_rate=Decimal("0.07"),
            monthly_pi=Decimal("2500.00"),
        ),
    ]

    feed = _rate_feed(params, rate_changes)

    # Two as_of dates inside the second period -> the recorded recast,
    # held constant (no month-to-month re-amortization).
    state_early = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2032, 6, 1),
    )
    state_late = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2035, 6, 1),
    )
    assert state_early.monthly_payment == Decimal("2500.00")
    assert state_early.monthly_payment == state_late.monthly_payment

    # DH-#25: the SCHEDULE rows agree with the card.  Every projected
    # second-period row pays the recorded $2,500.00 at 7% -- this was
    # the uncovered assertion that let the card ($2,500.00) and the
    # schedule ($2,582.13, re-amortized) silently diverge (DH-#1);
    # proven to fail against the pre-SSOT engine.  The final row is
    # exempt (it absorbs the closing residue), as are rows past the
    # third boundary (2036-01-01, a later derived recast).
    second_period_rows = [
        row for row in state_early.schedule
        if date(2031, 1, 1) <= row.payment_date < date(2036, 1, 1)
        and not row.is_confirmed
    ]
    assert second_period_rows, "schedule must cover the second period"
    for row in second_period_rows:
        assert row.payment == Decimal("2500.00"), (
            f"{row.payment_date}: schedule pays {row.payment}, card "
            f"shows the recorded 2500.00"
        )
        assert row.interest_rate == Decimal("0.07")
    # And the pre-recast projected rows pay the ORIGINATION period's
    # P&I (the engine fills the no-payments gap at period-true terms,
    # not the as_of period's).
    first_period_rows = [
        row for row in state_early.schedule
        if row.payment_date < date(2031, 1, 1) and not row.is_confirmed
    ]
    assert first_period_rows
    for row in first_period_rows:
        assert row.payment == Decimal("2398.20")

    # The origination period is unaffected: still the contractual P&I.
    state_p0 = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2028, 1, 1),
    )
    assert state_p0.monthly_payment == Decimal("2398.20")


def test_future_recorded_recast_notice_honored_in_schedule():
    """DH-#1 state A: an advance adjustment notice governs forward rows.

    ARM lenders send the rate-adjustment notice 60-120 days BEFORE the
    new payment takes effect, stating the exact new P&I -- entering it
    in advance is the natural ``add_rate_change`` workflow (the route
    accepts any post-origination ``effective_date``).  The schedule's
    rows past the future recast must pay the recorded figure, while the
    card (today, pre-recast) keeps the current period's P&I.  The
    pre-SSOT engine re-amortized $2,622.20 here from its own balance.

    Setup: the 5/5 ARM at $400k/6%/360 from 2026-01-01; a true-up
    anchor 2030-11-01 at $372,000; notice recorded for 2031-01-01 ->
    7% with monthly_pi $2,500.00; as_of 2030-11-15 (recast in the
    future).
    """
    params = _arm_400k_params()
    anchor = FakeAnchorEvent(
        anchor_date=date(2030, 11, 1),
        anchor_balance=Decimal("372000.00"),
        created_at=datetime(2030, 11, 1, tzinfo=timezone.utc),
    )
    feed = _rate_feed(params, [
        RateChangeRecord(
            effective_date=date(2031, 1, 1),
            interest_rate=Decimal("0.07"),
            monthly_pi=Decimal("2500.00"),
        ),
    ])
    state = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2030, 11, 15),
    )

    # Card today: still the origination period's contractual P&I.
    assert state.monthly_payment == Decimal("2398.20")

    # Forward rows past the recast pay the notice's recorded figure
    # (final residue-absorbing row exempt).
    post_recast = [
        row for row in state.schedule
        if row.payment_date >= date(2031, 1, 1)
    ]
    assert post_recast, "schedule must reach the recast"
    for row in post_recast[:-1]:
        assert row.payment == Decimal("2500.00"), (
            f"{row.payment_date}: paid {row.payment}, the notice "
            f"recorded 2500.00"
        )
    # The pre-recast forward row keeps the current contractual.
    pre_recast = [
        row for row in state.schedule
        if row.payment_date < date(2031, 1, 1) and not row.is_confirmed
    ]
    assert pre_recast
    assert all(r.payment == Decimal("2398.20") for r in pre_recast)


def test_stale_anchor_derived_recast_schedule_matches_card():
    """DH-#1 state C: card == schedule for a DERIVED recast, stale anchor.

    With no recorded ``monthly_pi``, both the card and the schedule
    must read the rate-period engine's derived level payment.  Under
    the pre-SSOT engine the projection started at the origination
    anchor with the as_of period's payment and re-amortized at the
    boundary from its own walked balance -- card $2,630.76 vs schedule
    $2,517.60.  Now the schedule's post-recast rows equal the card by
    construction.

    Setup: the 5/5 ARM at $400k/6%/360 anchored at origination (the
    exact anchor ``create_params`` writes for every new loan), rate
    change 2031-01-01 -> 7% with NO recorded P&I, no confirmed
    payments, as_of 2032-06-01.
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)
    feed = _rate_feed(params, [
        RateChangeRecord(
            effective_date=date(2031, 1, 1),
            interest_rate=Decimal("0.07"),
            monthly_pi=None,
        ),
    ])
    state = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2032, 6, 1),
    )

    # Hand arithmetic: the derived second-period P&I amortizes the
    # CONTRACTUAL balance at the boundary over the remaining 300
    # months at 7% -- the value period_for_date gives the card.
    assert state.monthly_payment == Decimal("2630.76")

    post_recast = [
        row for row in state.schedule
        if row.payment_date >= date(2031, 1, 1)
    ]
    assert post_recast
    for row in post_recast[:-1]:
        assert row.payment == state.monthly_payment, (
            f"{row.payment_date}: schedule pays {row.payment}, card "
            f"shows {state.monthly_payment}"
        )
    # Pre-recast forward-fill rows pay the origination period's P&I.
    pre_recast = [
        row for row in state.schedule
        if row.payment_date < date(2031, 1, 1) and not row.is_confirmed
    ]
    assert pre_recast
    assert all(r.payment == Decimal("2398.20") for r in pre_recast)


# -- C6-8 -- resolver chokepoint: no direct generate_schedule reference -----


def test_no_generate_schedule_in_resolver():
    """C6-8: ``loan_resolver`` source contains no ``generate_schedule`` ref.

    Phase 6 of the amortization-engine split moves the resolver's
    schedule generation off the legacy ``generate_schedule`` entry
    point and onto :func:`compute_payoff_scenarios`.  The resolver
    is the single chokepoint other surfaces (year-end debt
    aggregation, savings dashboard debt card, debt-strategy, the
    refinance calculator) read through, so locking it pure-of-engine
    here keeps the downstream consumers automatically on the new
    primitives.

    Inspects the raw module source (including comments and
    docstrings).  A regression that reintroduces ``generate_schedule``
    in any form would surface here loud before reaching any
    downstream consumer.
    """
    source = _loan_resolver_package_source()
    assert "generate_schedule" not in source, (
        "the loan_resolver package references generate_schedule; the "
        "resolver must route schedule generation through "
        "compute_payoff_scenarios (Phase 6 / Commit 6 of the "
        "amortization-engine split)."
    )


# -- C6-9 / C6-10 -- is_confirmed flags on history vs forward rows ----------


def test_history_rows_marked_confirmed():
    """C6-9: schedule rows backing confirmed payments are is_confirmed=True.

    Setup: $300k / 6% / 360 mo, three confirmed contractual payments
    Feb-Apr 2026, ``as_of=2026-05-01``.  The composer's replay
    consumes the three confirmed payments and emits three history
    rows; the resolver's ``LoanState.schedule`` starts with those
    three rows.  Every history row carries ``is_confirmed=True``
    so a downstream caller can distinguish recorded history from
    projection without re-tracing the row through the payment list.

    This invariant is load-bearing for any future surface that
    needs to render history differently from projection (e.g. a
    "shade past months" treatment in the amortization tab) without
    re-querying the payment store.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)
    payments = [
        PaymentRecord(PaymentDates(date(2026, 2, 1), monthly_due_date(date(2026, 2, 1), 1), date(2026, 2, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 3, 1), monthly_due_date(date(2026, 3, 1), 1), date(2026, 3, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 4, 1), monthly_due_date(date(2026, 4, 1), 1), date(2026, 4, 1)), Decimal("1798.65")),
    ]

    state = resolve_loan(
        LoanInputs(params, [anchor], payments, _rate_feed(params)),
        date(2026, 5, 1),
    )

    # First three rows are the replayed confirmed payments.
    assert len(state.schedule) >= 3
    for idx, row in enumerate(state.schedule[:3]):
        assert row.is_confirmed is True, (
            f"history row {idx} ({row.payment_date}) is not "
            f"flagged is_confirmed=True"
        )


def test_forward_rows_marked_unconfirmed():
    """C6-10: schedule rows past as_of are is_confirmed=False.

    Same setup as :func:`test_history_rows_marked_confirmed`.  The
    fourth row onward comes from :func:`project_forward` (via the
    composer's ``original_forward`` slice) and is unconfirmed by
    construction -- projection rows are not facts about the
    recorded past.

    Together with C6-9 this pins the contract that
    ``LoanState.schedule`` carries an authoritative confirmation
    flag per row: callers can rely on ``is_confirmed`` to
    distinguish historical fact from projection without re-deriving
    the boundary from ``as_of``.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)
    payments = [
        PaymentRecord(PaymentDates(date(2026, 2, 1), monthly_due_date(date(2026, 2, 1), 1), date(2026, 2, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 3, 1), monthly_due_date(date(2026, 3, 1), 1), date(2026, 3, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 4, 1), monthly_due_date(date(2026, 4, 1), 1), date(2026, 4, 1)), Decimal("1798.65")),
    ]

    state = resolve_loan(
        LoanInputs(params, [anchor], payments, _rate_feed(params)),
        date(2026, 5, 1),
    )

    # All rows past the three history rows are forward projections.
    assert len(state.schedule) > 3
    for idx, row in enumerate(state.schedule[3:], start=3):
        assert row.is_confirmed is False, (
            f"forward row {idx} ({row.payment_date}) is flagged "
            f"is_confirmed=True; only history rows should be"
        )


# -- TestComputePayoffScenarios (Commit 3, C3-1..C3-15) ---------------------
#
# The composer is the load-bearing fix for the architectural defect
# described in
# ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``:
# replay of confirmed history and projection of the future are split
# into two primitives, and the composer is the single producer that
# every Payoff Calculator surface reads through.  Chart series and
# summary metrics derive from one return value, so they cannot
# diverge.
#
# C3-10 is the originally-reported-bug regression lock.  The
# reproduction has a multi-month temporal gap between origination
# (2024-01-01) and the first confirmed payment (2026-01-01); this is
# the input shape that surfaces the "extra applied to ghost
# historical months" defect that
# ``tests/test_services/test_amortization_engine.py::TestPaymentAwareSchedule``
# could never catch (it set ORIGINATION = first confirmed payment's
# month, eliminating the gap).  Every test in this class deliberately
# preserves a gap unless the test name says otherwise.


def _fixed_rate_300k_params() -> FakeLoanParams:
    """30 yr / $300k / 6% / payment_day=1 from 2024-01-01.

    Matches the C3-10 originally-reported-bug regression setup.
    Used by most tests in this class -- the long origination-to-first-
    confirmed-payment gap (2024-01-01 to 2026-01-01) is the shape
    the architectural bug exists in.
    """
    return FakeLoanParams(
        origination_date=date(2024, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )


def _four_contractual_payments_jan_to_apr_2026() -> list[PaymentRecord]:
    """Four confirmed contractual payments on the first of Jan-Apr 2026.

    Amount $1798.65 is the contractual P&I for
    amortize($300k, 0.06, 360) -- hand-computed at C13-5 (existing
    fixed-rate replay test) and used by the architectural plan's
    regression scenario.
    """
    return [
        PaymentRecord(PaymentDates(date(2026, 1, 1), monthly_due_date(date(2026, 1, 1), 1), date(2026, 1, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 2, 1), monthly_due_date(date(2026, 2, 1), 1), date(2026, 2, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 3, 1), monthly_due_date(date(2026, 3, 1), 1), date(2026, 3, 1)), Decimal("1798.65")),
        PaymentRecord(PaymentDates(date(2026, 4, 1), monthly_due_date(date(2026, 4, 1), 1), date(2026, 4, 1)), Decimal("1798.65")),
    ]


class TestComputePayoffScenarios:
    """C3-1..C3-15: scenario composer in loan_resolver.

    Pins the composer's invariants (history shared, forward slices
    derive from one starting state, override months never carry
    extra) plus the originally-reported-bug regression lock (C3-10)
    and the temporal-gap property (C3-11) that generalizes it.
    """

    AS_OF = date(2026, 5, 21)

    def test_history_shared(self):
        """C3-1: history_rows has one entry per confirmed payment <= as_of.

        Four confirmed contractual payments Jan-Apr 2026 produce
        exactly four history rows.  Replay does NOT fabricate
        contractual rows for the 23 unrecorded months between
        2024-02 and 2025-12 (this is the load-bearing distinction
        between replay and projection); the row count equals the
        confirmed-payment count, not the months-since-origination
        count.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
            as_of=self.AS_OF,
        )
        assert len(scenarios.history_rows) == 4
        for row in scenarios.history_rows:
            assert row.payment_date <= self.AS_OF
            assert row.is_confirmed is True

    def test_forward_starts_from_the_replay_balance(self):
        """C3-2: the contractual forward's row 0 splits the replay's balance.

        replay.balance_as_of after four $1798.65 payments is
        $298,796.42 (verified at C13-5).  Hand arithmetic for row 0:
            interest    = 298796.42 * 0.005 = 1493.98 (HALF_UP)
            principal   = 1798.65 - 1493.98 = 304.67
            balance     = 298796.42 - 304.67 = 298491.75
        Until plan step R7d-g-3 this pinned the committed and accelerated
        slices against the same row; those slices are the balance seam's fold
        now (ruling R-R88).
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
            as_of=self.AS_OF,
        )
        original_row0 = scenarios.original_forward[0]
        assert original_row0.interest == Decimal("1493.98")
        assert original_row0.principal == Decimal("304.67")
        assert original_row0.remaining_balance == Decimal("298491.75")
        assert original_row0.extra_payment == Decimal("0.00")

    def test_forward_first_row_date_matches_next_pay_date(self):
        """C3-3: the contractual forward starts at replay.next_pay_date.

        Last replayed payment is 2026-04-01, so projection picks up
        at 2026-05-01.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
            as_of=self.AS_OF,
        )
        assert scenarios.original_forward[0].payment_date == date(2026, 5, 1)

    def test_schedule_rows_dated_by_monthly_due_date(self):
        """Schedule rows show the true monthly due date, not the pay-period start.

        Regression for the user-reported display bug (mortgage account 3):
        a confirmed payment keyed to its biweekly pay-period START
        (2026-05-21) was printed on the schedule as "May 21" and the
        projection then began one month early (the next Projected row read
        "Jun 1" when the borrower's next payment is Jul 1).  The schedule
        now dates each row by the true monthly DUE date: the confirmed
        payment shows Jun 1 (its real statement date) and the first
        projected row shows Jul 1 (the following month).

        A user_trueup anchor on 2026-05-22 makes only the 2026-05-21 pay
        period (due 2026-06-01) post-anchor; the 2026-06-18 pay period
        (due 2026-07-01) is a Projected forward payment.  Both pay-period
        starts precede their monthly due dates, so the old pay-period-start
        dating mislabeled both rows.
        """
        params = _fixed_rate_300k_params()
        anchors = [
            _origination_anchor(params),
            FakeAnchorEvent(
                anchor_date=date(2026, 5, 22),
                anchor_balance=Decimal("200000.00"),
                created_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
            ),
        ]
        payments = [
            # Confirmed, keyed to its pay-period start 2026-05-21; due 06-01.
            PaymentRecord(PaymentDates(date(2026, 5, 21), monthly_due_date(date(2026, 5, 21), 1), date(2026, 5, 21)), Decimal("1798.65")),
            # Projected, keyed to pay-period start 2026-06-18; due 07-01.
            PaymentRecord(PaymentDates(date(2026, 6, 18), monthly_due_date(date(2026, 6, 18), 1), None), Decimal("1798.65")),
        ]
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=anchors,
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
            as_of=date(2026, 6, 2),
        )
        # Confirmed history row carries the true due date (06-01), not the
        # pay-period start (05-21) it was keyed to.
        assert len(scenarios.history_rows) == 1
        assert scenarios.history_rows[0].is_confirmed is True
        assert scenarios.history_rows[0].payment_date == date(2026, 6, 1)
        # The projection picks up the FOLLOWING month (07-01), not 06-01.
        assert scenarios.original_forward[0].is_confirmed is False
        assert scenarios.original_forward[0].payment_date == date(2026, 7, 1)

    def test_original_ignores_projections(self):
        """C3-5: original_forward uses contractual every row, no extras.

        One projected $2000 payment in June 2026 in the feed: every
        original_forward row uses the contractual $1798.65 P&I and
        ``extra_payment == 0``.  The original line is "what the
        lender would amortize" with NO planning data and NO
        acceleration.  The final row absorbs any sub-penny residue
        and may report a slightly different payment amount (engine's
        ``is_final`` branch); that row is excluded from the pointwise
        contractual assertion below.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        payments = _four_contractual_payments_jan_to_apr_2026() + [
            PaymentRecord(PaymentDates(date(2026, 6, 1), monthly_due_date(date(2026, 6, 1), 1), None), Decimal("2000.00")),
        ]
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
            as_of=self.AS_OF,
        )
        # All but the final row use the exact contractual payment;
        # the engine's final-row branch absorbs the balance residue
        # and may have a sub-cent difference.
        for row in scenarios.original_forward[:-1]:
            assert row.payment == Decimal("1798.65")
            assert row.extra_payment == Decimal("0.00")
        # The final row also carries no extra (extra is forward-only
        # and original has no extras by construction).
        assert scenarios.original_forward[-1].extra_payment == Decimal("0.00")

    def test_the_composer_takes_no_extra_and_no_plan(self):
        """R7d-g-3: the composer walks the contract; the fold walks the plan.

        Until plan step R7d-g-3 this composer took a loan-level
        ``extra_principal`` (Leaf A deleted it: a projected row's cash
        carries its own definition's extra through amount rule 4, so the
        composer adding one again paid it twice on every row-covered month)
        and the lever's ``extra_monthly`` beside a ``monthly_override`` of the
        projected rows (ruling **R-R88** deleted both: the balance seam's plan
        fold is the one walk of what a loan is projected to pay).  Three
        pins: neither keyword exists, so a re-threading cannot land silently;
        and the forward it does compose is the CONTRACT -- extra-free rows,
        every one at the contractual P&I, whatever the feed's projected rows
        say.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        inputs = LoanInputs(
            loan_params=params,
            anchor_events=[anchor],
            payments=_four_contractual_payments_jan_to_apr_2026() + [
                PaymentRecord(PaymentDates(date(2026, 6, 1), monthly_due_date(date(2026, 6, 1), 1), None), Decimal("2000.00")),
            ],
            rate_changes=_rate_feed(params),
        )
        for gone in ("extra_principal", "extra_monthly"):
            with pytest.raises(TypeError, match=gone):
                compute_payoff_scenarios(
                    loan_inputs=inputs, as_of=self.AS_OF,
                    **{gone: Decimal("500.00")},
                )
        scenarios = compute_payoff_scenarios(
            loan_inputs=inputs, as_of=self.AS_OF,
        )
        june = next(
            row for row in scenarios.original_forward
            if row.payment_date == date(2026, 6, 1)
        )
        assert june.payment == Decimal("1798.65")
        for row in scenarios.original_forward:
            assert row.extra_payment == Decimal("0.00")

    def test_temporal_gap_property(self):
        """C3-11: history row count tracks confirmed-payment count, not gap.

        Parameterized origination dates create gaps of 12, 24, and
        36 months before the first confirmed payment.  The same four
        confirmed payments always produce four history rows
        regardless of gap -- replay does not fabricate.  This
        generalizes C3-10's load-bearing assertion to arbitrary
        origination-to-first-confirmed gaps.

        The architectural plan calls this property out at lines
        467-469 (architectural plan path):
        "any scenario that does not include a multi-month gap
        between origination and the first confirmed payment cannot
        distinguish the buggy and fixed implementations."  This test
        is the gap-class regression lock.
        """
        for gap_months in (12, 24, 36):
            # First confirmed payment fixed at 2026-01-01; vary
            # origination to create the requested gap.
            origination = add_months(
                date(2026, 1, 1), -gap_months,
            )
            params = FakeLoanParams(
                origination_date=origination,
                term_months=360,
                original_principal=Decimal("300000.00"),
                interest_rate=Decimal("0.06"),
                payment_day=1,
            )
            anchor = _origination_anchor(params)
            payments = _four_contractual_payments_jan_to_apr_2026()
            scenarios = compute_payoff_scenarios(
                loan_inputs=LoanInputs(
                    loan_params=params,
                    anchor_events=[anchor],
                    payments=payments,
                    rate_changes=_rate_feed(params),
                ),
                as_of=self.AS_OF,
            )
            assert len(scenarios.history_rows) == 4, (
                f"gap_months={gap_months}: expected 4 history rows "
                f"(one per confirmed payment), got "
                f"{len(scenarios.history_rows)}"
            )
            # All history rows must fall in the confirmed window;
            # none in the 2024-2025 gap.
            for row in scenarios.history_rows:
                assert row.payment_date >= date(2026, 1, 1)

    def test_composer_is_pure(self):
        """C3-12: compute_payoff_scenarios source has no Flask / db references.

        The composer lives in ``loan_resolver`` (the resolver itself
        was already locked pure at C13-8); this assertion focuses on
        the composer's own body to catch a future refactor that
        sneaks a ``db.session.query`` or ``current_user`` into
        scenario composition.  Composing scenarios requires only
        plain data + the two engine primitives; any I/O dependency
        would break test/task contexts where no app context is
        available.
        """
        source = inspect.getsource(compute_payoff_scenarios)
        code_tokens = []
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            if tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                continue
            code_tokens.append(tok.string)
        code_only = " ".join(code_tokens)
        for marker in (
            "from flask", "import flask", "current_user",
            "db.session", "request.", "session[",
        ):
            assert marker not in code_only, (
                f"compute_payoff_scenarios references {marker!r} in "
                f"executable code; the composer must remain pure."
            )

    def test_arm_anchor_preserved(self):
        """C3-14: ARM anchor snaps replay's balance to the verified value.

        ARM 5/5 originated 2024-01-01, $400k/6%/360mo.  Trueup
        anchor at 2025-12-15 with anchor_balance=$250,000 -- well
        inside the 60-month fixed window
        ``[2024-01-01, 2029-01-01)``.  Two confirmed payments after
        the trueup (2026-01-01, 2026-02-01) at the in-window
        contractual P&I of $2398.20 (Symptom #4 worked example).

        Replay's first post-snap row reflects the verified balance
        rather than the from-origination projection.  Hand
        arithmetic for the first replayed row (Jan 2026, post-snap):

            balance(at trueup snap) = 250000.00
            interest                = 250000.00 * 0.005 = 1250.00
            principal               = 2398.20 - 1250.00 = 1148.20
            balance(end of Jan)     = 250000 - 1148.20 = 248851.80
        """
        params = FakeLoanParams(
            origination_date=date(2024, 1, 1),
            term_months=360,
            original_principal=Decimal("400000.00"),
            interest_rate=Decimal("0.06"),
            payment_day=1,
            is_arm=True,
            arm_first_adjustment_months=60,
        )
        anchor_origin = FakeAnchorEvent(
            anchor_date=date(2024, 1, 1),
            anchor_balance=Decimal("400000.00"),
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        anchor_trueup = FakeAnchorEvent(
            anchor_date=date(2025, 12, 15),
            anchor_balance=Decimal("250000.00"),
            created_at=datetime(2025, 12, 15, tzinfo=timezone.utc),
        )
        payments = [
            PaymentRecord(PaymentDates(date(2026, 1, 1), monthly_due_date(date(2026, 1, 1), 1), date(2026, 1, 1)), Decimal("2398.20")),
            PaymentRecord(PaymentDates(date(2026, 2, 1), monthly_due_date(date(2026, 2, 1), 1), date(2026, 2, 1)), Decimal("2398.20")),
        ]
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor_origin, anchor_trueup],
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
            as_of=date(2026, 3, 1),
        )
        # Two post-trueup history rows; the first reflects the snap
        # to $250,000.
        assert len(scenarios.history_rows) == 2
        assert (
            scenarios.history_rows[0].payment_date == date(2026, 1, 1)
        )
        assert (
            scenarios.history_rows[0].remaining_balance
            == Decimal("248851.80")
        )
        # Second post-trueup row continues from the snap:
        #     interest    = 248851.80 * 0.005 = 1244.26 (HALF_UP)
        #     principal   = 2398.20 - 1244.26 = 1153.94
        #     balance     = 248851.80 - 1153.94 = 247697.86
        assert (
            scenarios.history_rows[1].payment_date == date(2026, 2, 1)
        )
        assert (
            scenarios.history_rows[1].remaining_balance
            == Decimal("247697.86")
        )

class TestConfirmedLedgerView:
    """The read-switch seam: ``confirmed_view`` (the ConfirmedLedgerView bundle).

    The one bundle the loan read switch threads into the resolver so a loan's
    displayed balance, its confirmed schedule rows, and every figure projected
    from them come from the genesis ledger, not the schedule replay.
    Supplying it overrides the headline ``current_balance``, the forward
    projection's starting balance, AND the schedule's confirmed slice (one
    bundle, threaded once), so the card, the table, and the chart cannot
    desync off-schedule; omitting it (``None``) leaves the resolver on its
    anchor replay, unchanged.  These pin the seam directly at
    ``resolve_loan``, ``compute_payoff_scenarios``, and
    ``target_date_outlook`` -- the three entry points the loaders and the
    loan-detail chart / payoff calculators wire.  The fixtures carry no
    payments, so the view's ``history_rows`` are the empty ledger history a
    payment-less opened loan reads.
    """

    AS_OF = date(2026, 1, 1)
    SEED = Decimal("290000.00")

    def _view(self):
        """The payment-less ledger view: the seed balance, no history rows."""
        return ConfirmedLedgerView(balance=self.SEED, history_rows=[])

    def _fixed_300k(self):
        """$300k / 6% / 360mo from 2026-01-01, origination anchor, no payments."""
        params = FakeLoanParams(
            origination_date=date(2026, 1, 1),
            term_months=360,
            original_principal=Decimal("300000.00"),
            interest_rate=Decimal("0.06"),
            payment_day=1,
        )
        return params, _origination_anchor(params)

    def test_resolve_loan_seed_overrides_the_projection_not_the_payment(self):
        """A seed overrides the forward projection's start, not the payment.

        The origination anchor puts the un-seeded replay at the full $300,000.
        Seeding $290,000 (a $10,000 lower confirmed balance, as an off-schedule
        paydown would leave) makes the forward projection amortize the seed: at
        $1,798.65 contractual P&I and 0.5% monthly interest the first projected
        row pays 1,798.65 - round(290000 * 0.005)=1,450.00 -> 348.65 of
        principal, leaving 289,651.35 (vs 299,701.35 un-seeded:
        300000 - (1798.65 - 1500.00)).  The seed is projection-only: the
        rate-period-derived payment and rate do not move, while the lower
        balance pays off sooner with less total interest.  (The seed's old
        second job -- the headline ``current_balance`` -- was deleted at plan
        step D2a: the bundle carries no balance, and the seam folds one from
        the loan's recorded events.)
        """
        params, anchor = self._fixed_300k()
        loan_inputs = LoanInputs(params, [anchor], None, _rate_feed(params))

        seeded = resolve_loan(
            loan_inputs, self.AS_OF, confirmed_view=self._view(),
        )
        unseeded = resolve_loan(loan_inputs, self.AS_OF)

        # Forward projection seeds from the view's balance (first projected row).
        assert seeded.schedule[0].remaining_balance == Decimal("289651.35")
        assert unseeded.schedule[0].remaining_balance == Decimal("299701.35")

        # Balance-only: the payment and rate do not move; the lower balance pays
        # off sooner and accrues less total interest.
        assert seeded.monthly_payment == unseeded.monthly_payment
        assert seeded.current_rate == unseeded.current_rate
        assert seeded.total_interest < unseeded.total_interest
        assert (
            seeded.schedule[-1].payment_date
            < unseeded.schedule[-1].payment_date
        )

    def test_compute_payoff_scenarios_seeds_the_forward_slice(self):
        """The composer's contractual forward starts from the seed, not the replay.

        The direct-call path the loan-detail chart's x-axis and the lever's
        contract reference wire (they bypass ``resolve_loan``).  With the
        $290,000 seed the contractual slice's first row leaves 289,651.35 (vs
        299,701.35 un-seeded) and its life-of-remaining interest is strictly
        less.
        """
        params, anchor = self._fixed_300k()
        loan_inputs = LoanInputs(params, [anchor], None, _rate_feed(params))
        seeded = compute_payoff_scenarios(
            loan_inputs=loan_inputs, as_of=self.AS_OF, confirmed_view=self._view(),
        )
        unseeded = compute_payoff_scenarios(
            loan_inputs=loan_inputs, as_of=self.AS_OF,
        )
        assert seeded.original_forward[0].remaining_balance == Decimal(
            "289651.35"
        )
        assert unseeded.original_forward[0].remaining_balance == Decimal(
            "299701.35"
        )
        assert sum(
            (row.interest for row in seeded.original_forward), Decimal("0.00"),
        ) < sum(
            (row.interest for row in unseeded.original_forward), Decimal("0.00"),
        )

    # ``test_target_date_outlook_uses_the_seed`` was deleted with
    # ``target_date_outlook`` at plan step C8f (see the note above
    # ``TestConfirmedLedgerView``).  What it pinned -- that the confirmed-present
    # seed, not the anchor replay, drives the target-date answer -- is now
    # structural rather than testable here: the seam's ``loan_required_extra``
    # reads the SAME memoized timeline ``positions()`` and ``loan_payoff_date``
    # read (one replay from the origination since recurrence:R16-c-1), so there
    # is no second seeding path left to diverge.  ``TestLoanRequiredExtraSeam``
    # grades the producer.



class TestTheReplayProjectionCutIsTheSettledDay:
    """Plan step **X-an** / finding **N-187**: one cut, on the day cash moved.

    The resolver's replay takes what has happened on ONE predicate --
    ``_replay_from_anchor`` reads ``has_settled_by`` -- and what has not is the
    balance seam's plan's (since plan step R7d-g-3, ruling **R-R88**; until
    then the composer's own ``_build_monthly_override`` planned it, and both
    halves of this class were the composer's).  (A settled payment CAN be in
    neither the replay nor the plan;
    ``test_a_payment_an_anchor_subsumes_is_in_neither_half`` below is the
    counter-example, and "exact complements" is what this deliberately is
    not.)  Until X-an both sides asked whether the payment's PAY PERIOD had
    begun, while the posted ledger that seeds the projection counted the same
    payment from the day its cash moved (``loan_ledger.payment_visible_on``).
    The two dates differ in both directions, and both failures move a figure the
    developer reads:

    * settled EARLY (before the paycheck period funding it opens): the ledger
      had already paid the installment down and the resolver planned it again,
      so the forward chart paid it twice;
    * settled AFTER an evaluation date its period already covers (an ordinary
      state for a read of a PAST date): history to the resolver, not to the
      ledger, so it fell out of both the balance and the plan.

    Measured on a $300,000 / 6% / 360-month loan whose 2026-08-01 installment is
    funded by the pay period opening 2026-07-31 and whose cash left 2026-07-30:
    read on 2026-07-30 the projected balance was $310.81 low the following
    month, growing to $1,789.69 at the tail of the schedule.

    **The end-to-end test does not grade the cut on its own, and the
    partition test is not redundant with it.**  A replay a day off the
    predicate still opens its forward slice in September on this fixture, so
    the end-to-end test stays green;
    ``test_the_replay_takes_exactly_the_settled_by_set`` is what fails there,
    and it does so by reading the predicate directly.
    """

    PI = Decimal("1798.65")
    PAY_PERIOD_STARTS = {
        2: date(2026, 1, 23), 3: date(2026, 2, 20), 4: date(2026, 3, 20),
        5: date(2026, 4, 17), 6: date(2026, 5, 15), 7: date(2026, 6, 26),
        8: date(2026, 7, 31),
    }
    #: The August installment's cash left on 07-30, the day BEFORE the pay
    #: period funding it opens.  Every other one settled on its own due date.
    SETTLED_ON = {
        2: date(2026, 2, 1), 3: date(2026, 3, 1), 4: date(2026, 4, 1),
        5: date(2026, 5, 1), 6: date(2026, 6, 1), 7: date(2026, 7, 1),
        8: date(2026, 7, 30),
    }
    AS_OF = date(2026, 7, 30)

    def _params(self):
        """$300k / 6% / 360mo from 2026-01-01, due on the 1st."""
        return FakeLoanParams(
            origination_date=date(2026, 1, 1),
            term_months=360,
            original_principal=Decimal("300000.00"),
            interest_rate=Decimal("0.06"),
            payment_day=1,
        )

    def _payments(self, through_month: int = 8):
        """The Feb..*through_month* installments, all settled."""
        return [
            PaymentRecord(
                PaymentDates(
                    period_start=self.PAY_PERIOD_STARTS[month],
                    due_date=date(2026, month, 1),
                    settled_on=self.SETTLED_ON[month],
                ),
                amount=self.PI,
            )
            for month in range(2, through_month + 1)
        ]

    def _ledger_view(self, payments_counted: int):
        """The balance a sum-of-postings ledger reports after N payments.

        Walked by hand at the loan's single rate period: each installment
        accrues ``balance * 0.06 / 12`` and pays ``1798.65 - interest`` of
        principal.  This is what the fold answers on 2026-07-30, where all
        SEVEN payments have settled -- the August one that very day.
        """
        balance = Decimal("300000.00")
        for _ in range(payments_counted):
            interest = round_money(balance * Decimal("0.06") / Decimal("12"))
            balance = round_money(balance - (self.PI - interest))
        return ConfirmedLedgerView(balance=balance, history_rows=[])

    def test_an_early_settled_payment_is_not_planned_again(self):
        """The August installment is history, so the plan starts in September.

        Its cash moved 2026-07-30 and the read is as of that day, so the ledger
        seed already contains it.  The forward slice must therefore open at the
        SEPTEMBER installment.  Reading the pay period instead (07-31, still
        ahead of the read) left it in ``monthly_override``, so the committed
        slice opened at 2026-08-01 and paid the installment a second time on a
        balance that already reflected it.
        """
        params = self._params()
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                params, [_origination_anchor(params)],
                self._payments(), _rate_feed(params),
            ),
            as_of=self.AS_OF,
            confirmed_view=self._ledger_view(7),
        )
        assert scenarios.original_forward[0].payment_date == date(2026, 9, 1)

    def test_the_projected_balance_is_not_one_installment_low(self):
        """The figure the double count moved, pinned by hand.

        Ledger balance on 2026-07-30 after seven installments is $297,877.84.
        The first forward month is September: interest
        ``round(297877.84 * 0.005) = 1,489.39``, principal
        ``1798.65 - 1489.39 = 309.26``, leaving **297,568.58**.  With the
        August installment re-planned the schedule reached that figure a month
        early and then kept going, so every later month read one installment's
        principal low -- $310.81 by October, $1,789.69 at the tail.
        """
        params = self._params()
        view = self._ledger_view(7)
        assert view.balance == Decimal("297877.84")

        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                params, [_origination_anchor(params)],
                self._payments(), _rate_feed(params),
            ),
            as_of=self.AS_OF,
            confirmed_view=view,
        )
        by_month = {
            (row.payment_date.year, row.payment_date.month):
                row.remaining_balance
            for row in scenarios.original_forward
        }
        assert (2026, 8) not in by_month
        assert by_month[(2026, 9)] == Decimal("297568.58")
        assert by_month[(2026, 10)] == Decimal("297257.77")

    def _partition(self, as_of: date, anchors: list | None = None):
        """Return ``(replayed_months, settled_by_months)``.

        The replay's half of the cut, read off the call site, and the
        predicate's own answer, so the assertions below compare what the code
        does against what the rule says rather than against a second
        hand-maintained expectation.  Until plan step R7d-g-3 this returned
        the composer's PLANNED half too (``_build_monthly_override``, the
        complement); that half is the balance seam's plan now (ruling
        **R-R88**), and its clamp is graded where it lives
        (``test_loan_plan_assembly``).
        """
        params = self._params()
        payments = self._payments()
        periods = resolve_periods(params, _rate_feed(params))
        anchor_events = (
            [_origination_anchor(params)] if anchors is None else anchors
        )
        replayed = _replay_from_anchor(
            anchor_events=anchor_events,
            periods=periods,
            payments=[payment.dates for payment in payments],
            payment_day=params.payment_day,
            as_of=as_of,
        ).rows
        replayed_months = {
            (row.payment_date.year, row.payment_date.month)
            for row in replayed
        }
        settled_by_months = {
            (p.dates.due_date.year, p.dates.due_date.month)
            for p in payments
            if has_settled_by(p.dates.settled_on, as_of)
        }
        assert len(replayed_months) == len(replayed), (
            "the replay produced two rows in one month, which the "
            "biweekly redistribution exists to prevent"
        )
        return replayed_months, settled_by_months

    @pytest.mark.parametrize("as_of_day", list(range(25, 32)))
    def test_the_replay_takes_exactly_the_settled_by_set(self, as_of_day):
        """The replay's half IS the predicate's answer, on every day.

        ``has_settled_by`` is the WHOLE split: what it answers ``True`` for
        is a replay candidate, and what it answers ``False`` for is the
        balance seam's plan's (ruling R-R88).  Graded against the predicate
        directly, so the call site cannot drift from the rule without this
        failing.  Swept across 2026-07-25..07-31, which brackets both the
        August installment's settle day (07-30) and the pay period that used
        to decide it (07-31), so the sweep crosses the seam whichever rule is
        in force.  On this fixture (one origination anchor, no payoff) every
        candidate does replay;
        :meth:`test_a_payment_an_anchor_subsumes_is_in_neither_half` is the
        case where it does NOT.
        """
        replayed, settled_by = self._partition(date(2026, 7, as_of_day))
        assert replayed == settled_by

    def test_a_payment_an_anchor_subsumes_is_in_neither_half(self):
        """The case the single-anchor fixture cannot ask about, stated as a rule.

        A true-up asserts the balance owed on its own date, so every installment
        due at or before it is ALREADY inside that figure.  The replay drops
        those (``anchor_date < due_date``), and the plan must NOT take them back
        -- planning an installment the anchor already contains would pay it
        twice, the same defect from the other side.

        So "exact complements" is false as an unqualified claim, and this is the
        counter-example: with a true-up dated 2026-06-15, the February..June
        installments are in neither the replay nor the plan.  What IS invariant
        is what the test above grades -- the replay is the settled-by set minus
        what the anchor (or a payoff) already accounts for.
        """
        params = self._params()
        trueup = FakeAnchorEvent(
            anchor_date=date(2026, 6, 15),
            anchor_balance=Decimal("297900.00"),
            created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
        replayed, settled_by = self._partition(
            self.AS_OF, anchors=[_origination_anchor(params), trueup],
        )
        subsumed = {(2026, month) for month in range(2, 7)}

        assert subsumed & settled_by == subsumed, (
            "pre-condition: all five installments HAD settled by the read"
        )
        assert subsumed.isdisjoint(replayed)
        # Only the July and August installments survive the anchor bound.
        assert replayed == {(2026, 7), (2026, 8)}
