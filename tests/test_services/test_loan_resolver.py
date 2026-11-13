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
from app.services.amortization_engine import RateChangeRecord
from app.services.loan_resolver import (
    ConfirmedLedgerView,
    LoanInputs,
    compute_payoff_scenarios,
    resolve_loan,
)
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
            LoanInputs(params, [anchor], _rate_feed(params)), as_of,
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
        LoanInputs(params, [anchor], _rate_feed(params)),
        add_months(params.origination_date, 24),
    )
    state_25 = resolve_loan(
        LoanInputs(params, [anchor], _rate_feed(params)),
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
        LoanInputs(params, [anchor], feed), date(2027, 1, 1),
    )
    assert in_window.current_rate == Decimal("0.06")

    after_reset = resolve_loan(
        LoanInputs(params, [anchor], feed), date(2031, 6, 1),
    )
    assert after_reset.current_rate == Decimal("0.07")


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
        LoanInputs(params, [anchor], feed), date(2031, 2, 1),
    )
    state_later = resolve_loan(
        LoanInputs(params, [anchor], feed), date(2033, 6, 1),
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

    inputs = LoanInputs(params, [anchor], _rate_feed(params))
    state = resolve_loan(inputs, date(2026, 2, 1))

    assert state.monthly_payment == Decimal("1000.00")
    # The contract line's first row, Feb 1: no interest at 0%, so the whole
    # 1,000.00 is principal, leaving 12,000.00 - 1,000.00 = 11,000.00.
    first = state.schedule[0]
    assert (first.interest, first.principal, first.remaining_balance) == (
        Decimal("0.00"), Decimal("1000.00"), Decimal("11000.00"),
    )


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
        LoanInputs(params, [anchor], _rate_feed(params)),
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
            LoanInputs(params, [], _rate_feed(params)),
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

    inputs = LoanInputs(params, [earlier, later], _rate_feed(params))

    # The later-created anchor governs, and the contract line (no view)
    # seeds from its balance: Jul 1's interest is round(275000.00 x 0.005)
    # = 1,375.00, where the earlier one's would be 1,400.00.
    assert loan_resolver.select_latest_anchor(inputs.anchor_events) is later
    first = compute_payoff_scenarios(loan_inputs=inputs).original_forward[0]
    assert (first.payment_date, first.interest) == (
        date(2026, 7, 1), Decimal("1375.00"),
    )


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
        LoanInputs(params, [anchor], _rate_feed(params)),
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
        params, [origination_anchor, trueup_anchor], _rate_feed(params),
    )
    state_a = resolve_loan(inputs, date(2028, 6, 1))
    state_b = resolve_loan(inputs, date(2030, 6, 1))

    # Payment unchanged by the true-up: the origination-period level P&I.
    assert state_a.monthly_payment == Decimal("2398.20")
    assert state_a.monthly_payment == state_b.monthly_payment
    # The true-up DID move the balance: the contract line (no view) seeds
    # from it, so Feb 1 2028's interest is round(380000.00 x 0.005) = 1,900.00.
    first = state_a.schedule[0]
    assert (first.payment_date, first.interest) == (
        date(2028, 2, 1), Decimal("1900.00"),
    )


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
        LoanInputs(params, [anchor], feed), date(2032, 6, 1),
    )
    state_late = resolve_loan(
        LoanInputs(params, [anchor], feed), date(2035, 6, 1),
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
        LoanInputs(params, [anchor], feed), date(2028, 1, 1),
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
        LoanInputs(params, [anchor], feed), date(2030, 11, 15),
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
        LoanInputs(params, [anchor], feed), date(2032, 6, 1),
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


class TestComputePayoffScenarios:
    """C3-1..C3-15: scenario composer in loan_resolver.

    Pins the composer's two standing invariants: it takes no extra, no plan
    and no evaluation date, and it is pure.  *Its history, starting-balance,
    first-row-date, row-dating, temporal-gap and ARM-anchor tests (C3-1, C3-2,
    C3-3, C3-5, C3-11, C3-14 and the due-date regression) graded the deleted
    replay's rows and start, and went with it at plan step recurrence:R16-e
    (ruling plan#3382): the view supplies the rows now (``test_confirmed_view``),
    and the start is ``test_the_contract_line_start``'s.*
    """


    def test_the_composer_takes_no_extra_and_no_plan(self):
        """R7d-g-3: the composer walks the contract; the fold walks the plan.

        Until plan step R7d-g-3 this composer took a loan-level
        ``extra_principal`` (Leaf A deleted it: a projected row's cash
        carries its own definition's extra through amount rule 4, so the
        composer adding one again paid it twice on every row-covered month)
        and the lever's ``extra_monthly`` beside a ``monthly_override`` of the
        projected rows (ruling **R-R88** deleted both: the balance seam's plan
        fold is the one walk of what a loan is projected to pay).  Four
        pins: none of the three keywords exists -- ``as_of`` went with the
        replay it bounded at plan step recurrence:R16-e -- so a re-threading
        cannot land silently; and the forward it does compose is the CONTRACT,
        extra-free rows, every one at the contractual P&I.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        inputs = LoanInputs(
            loan_params=params,
            anchor_events=[anchor],
            rate_changes=_rate_feed(params),
        )
        for gone in ("extra_principal", "extra_monthly", "as_of"):
            with pytest.raises(TypeError, match=gone):
                compute_payoff_scenarios(
                    loan_inputs=inputs, **{gone: Decimal("500.00")},
                )
        scenarios = compute_payoff_scenarios(loan_inputs=inputs)
        june = next(
            row for row in scenarios.original_forward
            if row.payment_date == date(2026, 6, 1)
        )
        assert june.payment == Decimal("1798.65")
        for row in scenarios.original_forward:
            assert row.extra_payment == Decimal("0.00")

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

class TestConfirmedLedgerView:
    """The read-switch seam: ``confirmed_view`` (the ConfirmedLedgerView bundle).

    The one bundle the loan read switch threads into the resolver so a loan's
    displayed balance, its confirmed schedule rows, and every figure projected
    from them come from the genesis ledger.  Supplying it overrides the
    forward projection's starting balance and start, AND the schedule's
    confirmed slice (one bundle, threaded once), so the card, the table, and
    the chart cannot desync off-schedule; omitting it (``None``) seeds the
    resolver from the loan's latest assertion with no history (the anchor
    replay it fell back to went at plan step recurrence:R16-e).  These pin the
    seam directly at
    ``resolve_loan``, ``compute_payoff_scenarios``, and
    ``target_date_outlook`` -- the three entry points the loaders and the
    loan-detail chart / payoff calculators wire.  The fixtures carry no
    payments, so the view's ``history_rows`` are the empty ledger history a
    payment-less opened loan reads.
    """

    AS_OF = date(2026, 1, 1)
    SEED = Decimal("290000.00")

    def _view(self):
        """The payment-less ledger view: the seed balance, no history rows.

        Its last event is the loan's opening on ``AS_OF``, the origination,
        so the line starts at the first installment, Feb 1 2026.
        """
        return ConfirmedLedgerView(
            balance=self.SEED, history_rows=[], last_recorded_on=self.AS_OF,
        )

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

        The origination anchor puts the un-seeded line at the full $300,000.
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
        loan_inputs = LoanInputs(params, [anchor], _rate_feed(params))

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
        """The composer's contractual forward starts from the seed, not the anchor.

        The direct-call path the loan-detail chart's x-axis and the lever's
        contract reference wire (they bypass ``resolve_loan``).  With the
        $290,000 seed the contractual slice's first row leaves 289,651.35 (vs
        299,701.35 un-seeded) and its life-of-remaining interest is strictly
        less.
        """
        params, anchor = self._fixed_300k()
        loan_inputs = LoanInputs(params, [anchor], _rate_feed(params))
        seeded = compute_payoff_scenarios(
            loan_inputs=loan_inputs, confirmed_view=self._view(),
        )
        unseeded = compute_payoff_scenarios(loan_inputs=loan_inputs)
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

    Until X-an the resolver asked whether a payment's PAY PERIOD had begun,
    while the posted ledger that seeds the projection counted the same payment
    from the day its cash moved (``loan_ledger.payment_visible_on``).  A
    payment settled EARLY -- before the paycheck period funding it opens --
    was already in the ledger's balance and was planned again, so the forward
    chart paid it twice: measured on a $300,000 / 6% / 360-month loan whose
    2026-08-01 installment is funded by the pay period opening 2026-07-31 and
    whose cash left 2026-07-30, read on 2026-07-30 the projected balance was
    $310.81 low the following month, growing to $1,789.69 at the tail.

    **Since plan step recurrence:R16-e the cut has ONE home** (ruling
    plan#3382): the walk the confirmed view folds holds the payments visible
    by the pass's date, and the view carries the record's last event, here the
    August installment's own due date; the contract line starts at the
    installment after it.  The replay's own half of the cut, and the partition
    tests that graded it against ``has_settled_by``, went with the replay.
    These two pin what the reader sees: the line opens in September, from the
    balance seven installments leave.
    """

    PI = Decimal("1798.65")

    def _params(self):
        """$300k / 6% / 360mo from 2026-01-01, due on the 1st."""
        return FakeLoanParams(
            origination_date=date(2026, 1, 1),
            term_months=360,
            original_principal=Decimal("300000.00"),
            interest_rate=Decimal("0.06"),
            payment_day=1,
        )

    def _ledger_view(self, payments_counted: int):
        """The view a walk reports after the Feb.. installments, N of them.

        Walked by hand at the loan's single rate period: each installment
        accrues ``balance * 0.06 / 12`` and pays ``1798.65 - interest`` of
        principal.  This is what the fold answers on 2026-07-30, where all
        SEVEN payments have settled -- the August one that very day -- and
        the last of them is due on the first of month ``1 + N``.
        """
        balance = Decimal("300000.00")
        for _ in range(payments_counted):
            interest = round_money(balance * Decimal("0.06") / Decimal("12"))
            balance = round_money(balance - (self.PI - interest))
        return ConfirmedLedgerView(
            balance=balance, history_rows=[],
            last_recorded_on=date(2026, 1 + payments_counted, 1),
        )

    def test_an_early_settled_payment_is_not_planned_again(self):
        """The August installment is history, so the plan starts in September.

        Its cash moved 2026-07-30 and the read is as of that day, so the ledger
        seed already contains it, and the view's last event is its Aug 1 due
        date.  The forward slice must therefore open at the SEPTEMBER
        installment.  Reading the pay period instead (07-31, still ahead of the
        read) left it in ``monthly_override``, so the committed slice opened at
        2026-08-01 and paid the installment a second time on a balance that
        already reflected it.
        """
        params = self._params()
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                params, [_origination_anchor(params)], _rate_feed(params),
            ),
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
                params, [_origination_anchor(params)], _rate_feed(params),
            ),
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
