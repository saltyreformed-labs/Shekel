"""The loan replay over ONE stream: facts, then projections (recurrence:R16-c-1).

Plan step **recurrence:R16-c-1** (ruling **R-R90**) merges a loan's recorded
past and its projected future into one :class:`LoanEventStream`, replayed once
from one seed.  Two rules the merge introduces are observable ONLY on a
hand-built stream on this tree, and each is pinned here rather than left to the
step that first makes it reachable on live data:

* **A RESET clears whatever charges stand before it** (ruling **R-R72** part
  (2)).  Reachable on live data since plan step ``recurrence:R16-c-2``, which
  charges every contractual installment from origination, so a skipped
  month's charge meets a true-up.  (The what-if extra cannot meet one at all:
  it accrues at the charges on or after the projection boundary only, and
  those walk behind every recorded fact.)
* **A projection is never placed before a recorded fact**
  (:func:`~app.services.loan_ledger.projection_boundary`).  The recorded
  payments' splits are a function of the facts alone, whether or not the plan
  follows them, which is what lets the posted ledger and a screen agree on
  every settled payment.
* **A loan's FIRST tracking start clears the months before it one by one**
  (ruling **R-R117**), so a payment the start holds pays its own month alone
  -- in every shape pinned here, exactly as the app splits it where only the
  months holding a payment are charged (not in every shape: an early extra
  before the first installment differs by step R16-c-2's own pairing); a
  true-up, and a start dated after another balance, keep the reset's own
  clearing.
* **That start charges the month it lands in on its own figure when that
  month's payment walks after it** (ruling **R-R118**): a payment due off
  the contractual day, after the start but inside the standing installment's
  interval, pays that month's interest on the start's balance, recorded or
  planned, where a Record balance in the same place still clears it (R-R72
  part (2)) and the payment pays pure principal -- in the ruling's words,
  "$102.52 less interest than today's app", by design.

Pure: no database, no app context.  Every figure is stated by hand and checked
against :func:`~app.utils.money.installment_interest` /
:func:`~app.utils.money.apply_payment_cash`, the ONE accrual and the ONE
allocation.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.enums import InterestAccrualConventionEnum
from app.services.loan_ledger import (
    LoanCalendar,
    LoanCashEvent,
    LoanEventStream,
    LoanResetEvent,
    dated_deltas,
    projection_boundary,
    replay_loan_events,
    replay_loan_stream,
)
from app.utils.money import installment_interest
from tests.oracles.loan_monthly_composition import accrual_charge, rate_period

_ZERO = Decimal("0.00")
_RATE = Decimal("0.06")          # $50.00 a month on $10,000.00
_PRINCIPAL = Decimal("10000.00")


def _charge(on_date: date):
    """One hand-stated charge at 6% with no escrow."""
    return accrual_charge(on_date, _RATE, _ZERO)


def _payment(on_date: date, cash: str, *, visible_on: date | None = None):
    """A recorded or projected cash event dated at its installment."""
    return LoanCashEvent(
        on_date=on_date, cash=Decimal(cash), source=None,
        visible_on=visible_on or on_date,
    )


def _reset(
    on_date: date, balance: str, *,
    is_opening: bool = False, is_tracking_start: bool = False,
):
    """An assertion of the balance owed on *on_date*: a true-up unless stated.

    Its source is the plain fact a production stream carries (a
    :class:`~app.services.loan_loaders.LoanAnchorFact`'s date and balance), so
    :func:`~app.services.loan_ledger.dated_deltas` can date its correction.
    """
    return LoanResetEvent(
        on_date=on_date, balance=Decimal(balance),
        source=SimpleNamespace(anchor_date=on_date, anchor_balance=Decimal(balance)),
        is_opening=is_opening, is_tracking_start=is_tracking_start,
    )


class TestAResetClearsWhatStands:
    """Ruling R-R72 part (2): an assertion supersedes the charges before it."""

    def test_a_skipped_months_charge_does_not_survive_a_true_up(self):
        """Charge, no payment, true-up, payment: the payment pays pure principal.

        Origination at $10,000.00 on Jan 1; February's charge falls with no
        payment ($50.00 standing); on Feb 15 the owner asserts $9,000.00; the
        March payment of $500.00 then meets NOTHING standing from February --
        the assertion superseded it -- so it pays March's $45.00 on the
        asserted balance and $455.00 of principal.  Were the charge carried
        across, the payment would pay $95.00 of interest and $405.00 of
        principal, which is the second row asserted against.
        """
        stream = LoanEventStream(
            charges=[_charge(date(2026, 2, 1)), _charge(date(2026, 3, 1))],
            payments=[_payment(date(2026, 3, 1), "500.00")],
            resets=[
                _reset(date(2026, 1, 1), "10000.00"),
                _reset(date(2026, 2, 15), "9000.00"),
            ],
        )
        replay = replay_loan_events(_ZERO, stream)
        [outcome] = replay.payments
        march = installment_interest(
            Decimal("9000.00"), _RATE, date(2026, 2, 1), date(2026, 3, 1),
            InterestAccrualConventionEnum.MONTHLY_1_12,
        )
        assert march == Decimal("45.00")
        assert outcome.interest == march
        assert outcome.principal == Decimal("455.00")
        assert outcome.balance_after == Decimal("8545.00")
        # The displaced balance a true-up's correction is booked against is the
        # balance JUST BEFORE it -- the standing charge was never in it.
        assert [reset.balance_before for reset in replay.resets] == [
            _ZERO, _PRINCIPAL,
        ]


#: Ruling R-R117's worked example (the REC-552 study's second question):
#: $30,000.00 at 5% from 2025-01-01, $500.00 due the 15th.  A month's interest
#: on the untouched principal is round(30,000.00 x 0.05 / 12) = $125.00, so the
#: thirteen installments from 2025-02-15 through 2026-02-15 cost $1,625.00.
_EXAMPLE_RATE = Decimal("0.05")
_EXAMPLE_ORIGINATION = date(2025, 1, 1)


def _example_charges(through: date, escrow: str = "0.00"):
    """The example loan's installments, 2025-02-15 through *through*, by hand."""
    installments = [
        date(2025 + month // 12, month % 12 + 1, 15) for month in range(1, 26)
    ]
    return [
        accrual_charge(on_date, _EXAMPLE_RATE, Decimal(escrow))
        for on_date in installments if on_date <= through
    ]


def _example_walk(
    payments, *assertions, through: date, escrow: str = "0.00", projections=(),
):
    """Replay the example loan: its opening, then *assertions*, *payments* and *projections*."""
    return replay_loan_stream(LoanEventStream(
        charges=_example_charges(through, escrow),
        payments=payments,
        resets=[
            _reset(_EXAMPLE_ORIGINATION, "30000.00", is_opening=True),
            *assertions,
        ],
        projections=projections,
    ))


def _owed_on(walk, on_date: date) -> Decimal:
    """The balance a reader shows on *on_date*: every step visible by then."""
    return sum(
        (delta for day, delta in dated_deltas(walk) if day <= on_date), _ZERO,
    )


class TestTheFirstTrackingStartClearsMonthByMonth:
    """Ruling R-R117: the loan's first tracking start drops each unpaid month before it.

    "The loan's first tracking start (its earliest-dated balance, given at
    setup or with 'Record tracking start') drops the interest of every month
    before its date that has no payment recorded for it, even when a payment
    it includes would otherwise pay that interest; months after it are still
    charged, and a Record balance, or a tracking start dated after another
    balance, is untouched."  The reset's own clearing (R-R72 part (2)) comes
    too late for a payment the start holds -- due before it, marked paid after
    it -- because that payment walks first, by its due date, and would pay
    every unrecorded month since origination as its interest.

    Every figure here equals what the app splits where only the months holding
    a payment are charged (dev ``0eef2d800``, measured on these shapes
    2026-09-30), which is the equality the ruling promised.
    """

    def test_a_payment_the_start_holds_pays_its_own_month_alone(self):
        """Tracking from Feb 20 at $24,604.17; Feb 15's $500.00 marked paid Feb 25.

        The start holds the payment (due before it), so every month before
        Feb 15 that no payment of its own cleared is dropped and Feb 15's
        payment pays its own month: $125.00 of interest on $30,000.00 and
        $375.00 of principal, leaving $29,625.00, which the start then
        corrects to $24,604.17.  Without the rule it paid all thirteen
        months, $1,625.00 of interest and -$1,125.00 of principal.

        The reads: Feb 19 shows the opening held flat, $30,000.00; Feb 20
        the start less the principal not yet visible, $24,604.17 + $375.00 =
        $24,979.17; Feb 25 the start, $24,604.17.
        """
        start = _reset(date(2026, 2, 20), "24604.17", is_tracking_start=True)
        walk = _example_walk(
            [_payment(date(2026, 2, 15), "500.00", visible_on=date(2026, 2, 25))],
            start, through=date(2026, 2, 15),
        )
        [feb] = walk.payment_splits
        assert (feb.interest, feb.principal, feb.balance_after) == (
            Decimal("125.00"), Decimal("375.00"), Decimal("29625.00"),
        )
        assert [c.owed_before for c in walk.anchor_corrections] == [
            _ZERO, Decimal("29625.00"),
        ]
        assert [
            _owed_on(walk, on)
            for on in (date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 25))
        ] == [Decimal("30000.00"), Decimal("24979.17"), Decimal("24604.17")]

    @pytest.mark.parametrize(
        ("earlier_due", "earlier_paid", "owed_feb_20"),
        [
            # Dec 15 held, Jan missed.
            (date(2025, 12, 15), date(2026, 2, 25), Decimal("25355.73")),
            # Nov 15 held, Dec and Jan missed.
            (date(2025, 11, 15), date(2026, 2, 25), Decimal("25355.73")),
            # Dec 15 held but itself paid late, on Jan 10; Jan missed.
            (date(2025, 12, 15), date(2026, 1, 10), Decimal("24980.73")),
        ],
        ids=["one-missed", "two-missed", "held-payment-late"],
    )
    def test_a_missed_month_between_two_held_payments_is_dropped(
        self, earlier_due, earlier_paid, owed_feb_20,
    ):
        """Two payments the Feb 20 start holds, with missed months between them.

        The earlier payment pays its own month, $125.00 / $375.00, leaving
        $29,625.00.  The missed months after it are dropped, so Feb 15 pays
        its own month alone: round(29,625.00 x 0.05 / 12) = $123.44 of
        interest (123.4375) and $376.56 of principal, leaving $29,248.44,
        which the start corrects to $24,604.17 (a -$4,644.27 step on Feb 20).

        Feb 20 reads the start plus each principal not yet visible:
        $24,604.17 + $375.00 + $376.56 = $25,355.73 when both are marked Feb
        25, and $24,604.17 + $376.56 = $24,980.73 when the earlier one moved
        Jan 10.  Feb 25 reads $24,604.17 in every case.
        """
        start = _reset(date(2026, 2, 20), "24604.17", is_tracking_start=True)
        walk = _example_walk(
            [
                _payment(earlier_due, "500.00", visible_on=earlier_paid),
                _payment(date(2026, 2, 15), "500.00", visible_on=date(2026, 2, 25)),
            ],
            start, through=date(2026, 2, 15),
        )
        earlier, feb = walk.payment_splits
        assert (earlier.interest, earlier.principal, earlier.balance_after) == (
            Decimal("125.00"), Decimal("375.00"), Decimal("29625.00"),
        )
        assert (feb.interest, feb.principal, feb.balance_after) == (
            Decimal("123.44"), Decimal("376.56"), Decimal("29248.44"),
        )
        assert _owed_on(walk, date(2026, 2, 20)) == owed_feb_20
        assert _owed_on(walk, date(2026, 2, 25)) == Decimal("24604.17")

    def test_a_start_on_an_installment_day_holds_that_days_payment(self):
        """Tracking from Feb 15 itself; Feb 15's own payment marked paid Feb 25.

        A payment due on the start's own day walks between that day's charge
        and the start, so the start holds it -- and the months BEFORE the day
        are dropped all the same: it pays February alone, $125.00 / $375.00.
        Clearing only the charges strictly before the start would leave
        January's $125.00 standing under February's, and the payment would
        pay $250.00 of interest and $250.00 of principal.  Reads: Feb 15
        $24,604.17 + $375.00 = $24,979.17 (the payment not yet visible), Feb
        25 $24,604.17.
        """
        start = _reset(date(2026, 2, 15), "24604.17", is_tracking_start=True)
        walk = _example_walk(
            [_payment(date(2026, 2, 15), "500.00", visible_on=date(2026, 2, 25))],
            start, through=date(2026, 2, 15),
        )
        [feb] = walk.payment_splits
        assert (feb.interest, feb.principal) == (
            Decimal("125.00"), Decimal("375.00"),
        )
        assert _owed_on(walk, date(2026, 2, 15)) == Decimal("24979.17")
        assert _owed_on(walk, date(2026, 2, 25)) == Decimal("24604.17")

    def test_the_escrow_of_a_dropped_month_is_dropped_with_its_interest(self):
        """The first case with $100.00 of escrow a month.

        Feb 15's payment clears its own month's $125.00 of interest and
        $100.00 of escrow, and $275.00 is principal, leaving $29,725.00.
        Clearing the interest alone would leave twelve earlier months'
        $1,200.00 of escrow for it to pay first.
        """
        start = _reset(date(2026, 2, 20), "24604.17", is_tracking_start=True)
        walk = _example_walk(
            [_payment(date(2026, 2, 15), "500.00", visible_on=date(2026, 2, 25))],
            start, through=date(2026, 2, 15), escrow="100.00",
        )
        [feb] = walk.payment_splits
        assert (feb.interest, feb.escrow, feb.principal, feb.balance_after) == (
            Decimal("125.00"), Decimal("100.00"), Decimal("275.00"),
            Decimal("29725.00"),
        )

    def test_a_start_holding_no_payment_walks_as_it_did(self):
        """The normal case: nothing due before the start is recorded.

        Tracking from Feb 20 at $24,604.17 and Mar 15's $500.00 paid on Mar
        15.  The start's own reset already cleared every month before it, so
        marking it a tracking start changes no figure: the walk is the one a
        true-up on the same day gives, Mar 15 paying round(24,604.17 x 0.05
        / 12) = $102.52 (102.5174) and $397.48 of principal.
        """
        payments = [_payment(date(2026, 3, 15), "500.00")]
        as_start = _example_walk(
            payments,
            _reset(date(2026, 2, 20), "24604.17", is_tracking_start=True),
            through=date(2026, 3, 15),
        )
        as_true_up = _example_walk(
            payments, _reset(date(2026, 2, 20), "24604.17"),
            through=date(2026, 3, 15),
        )
        [mar] = as_start.payment_splits
        assert (mar.interest, mar.principal) == (
            Decimal("102.52"), Decimal("397.48"),
        )
        assert as_start.payment_splits == as_true_up.payment_splits
        assert [c.owed_before for c in as_start.anchor_corrections] == [
            c.owed_before for c in as_true_up.anchor_corrections
        ]


class TestTheRuleReachesOnlyTheLoansFirstBalance:
    """R-R117's scope: a Record balance, or a start after another balance, is untouched.

    Review 6 of the REC-552 study (finding M3) measured the month-by-month
    rule misfiring on both: it cancels interest a held payment paid, or lets
    a later start override an earlier-dated balance.  Each keeps the reset's
    own clearing (ruling R-R72 part (2)).  A balance SHARING the start's date
    is neither, and does not disqualify it.
    """

    @pytest.mark.parametrize(
        "first_balance",
        [
            _reset(date(2026, 1, 20), "25000.00"),
            _reset(date(2026, 1, 20), "25000.00", is_tracking_start=True),
        ],
        ids=["record-balance-first", "earlier-tracking-start"],
    )
    def test_a_start_after_another_balance_keeps_the_resets_clearing(
        self, first_balance,
    ):
        """A balance of $25,000.00 on Jan 20, then a start on Mar 20; Feb missed.

        Mar 15's $500.00, marked paid Mar 25, is held by the Mar 20 start.
        That start is not the loan's first balance, so February -- charged
        after the Jan 20 balance, on $25,000.00 -- stays owed: Mar 15 pays
        round(25,000.00 x 0.05 / 12) = $104.17 for each of February and
        March, $208.34, and $291.66 of principal.  Were the Mar 20 start to
        clear month by month, February would be dropped: $104.17 / $395.83.
        """
        walk = _example_walk(
            [_payment(date(2026, 3, 15), "500.00", visible_on=date(2026, 3, 25))],
            first_balance,
            _reset(date(2026, 3, 20), "24708.34", is_tracking_start=True),
            through=date(2026, 3, 15),
        )
        [mar] = walk.payment_splits
        assert (mar.interest, mar.principal) == (
            Decimal("208.34"), Decimal("291.66"),
        )

    def test_a_record_balance_on_the_starts_own_day_does_not_disqualify_it(self):
        """A Record balance and the tracking start, both on Feb 20.

        The ruling leaves untouched "a tracking start dated after another
        balance"; one sharing the earliest date is not dated after it, so the
        start is still the loan's first balance and R-R117 applies: Feb 15's
        payment, marked paid Feb 25, pays its own month, $125.00 / $375.00.
        Were a same-day true-up to disqualify it, the payment would pay all
        thirteen months, $1,625.00 / -$1,125.00.
        """
        walk = _example_walk(
            [_payment(date(2026, 2, 15), "500.00", visible_on=date(2026, 2, 25))],
            _reset(date(2026, 2, 20), "24604.17"),
            _reset(date(2026, 2, 20), "24604.17", is_tracking_start=True),
            through=date(2026, 2, 15),
        )
        [feb] = walk.payment_splits
        assert (feb.interest, feb.principal) == (
            Decimal("125.00"), Decimal("375.00"),
        )

    def test_a_record_balance_alone_is_untouched(self):
        """The first case with a Record balance where the start was.

        A true-up is not a tracking start, so the rule does not reach it and
        Feb 15's payment pays all thirteen months since origination, $1,625.00
        of interest and -$1,125.00 of principal.  This pins R-R117's SCOPE,
        not the figure's truth: what a Record balance does to a month with no
        payment recorded is a separate question the REC-552 round deferred,
        and this figure moves when that is decided.
        """
        walk = _example_walk(
            [_payment(date(2026, 2, 15), "500.00", visible_on=date(2026, 2, 25))],
            _reset(date(2026, 2, 20), "24604.17"),
            through=date(2026, 2, 15),
        )
        [feb] = walk.payment_splits
        assert (feb.interest, feb.principal) == (
            Decimal("1625.00"), Decimal("-1125.00"),
        )


#: Ruling R-R118's worked example: tracking from Feb 20 at $24,604.17, and a
#: $500.00 payment due Mar 1, off the loan's 15th, so inside the interval of
#: the Feb 15 installment (Feb 15 - Mar 14) standing when the start walks.
_STRADDLE_START = date(2026, 2, 20)
_STRADDLE_DUE = date(2026, 3, 1)
_STRADDLE_MONTH = date(2026, 2, 15)


class TestTheFirstTrackingStartChargesTheMonthItLandsIn:
    """Ruling R-R118: the start re-charges its month when that month's payment follows.

    "A loan's first tracking start charges a month whose own payment comes
    after it on the start's figure, as the 'lender's day' design does, so the
    example books $102.52 / $397.48, as today.  A Record balance in the same
    place keeps your Sept 11 rule: that payment books $0.00 / $500.00."  The
    start's own clearing (R-R72 part (2)) left the Mar 1 payment nothing to
    pay; the start now charges February's installment again on the balance
    it states: round(24,604.17 x 0.05 / 12) = $102.52 (102.5174).
    """

    @pytest.mark.parametrize("projected", [False, True], ids=["recorded", "planned"])
    def test_the_months_payment_after_the_start_pays_it_on_the_starts_figure(
        self, projected,
    ):
        """Mar 1's $500.00 pays $102.52 of interest and $397.48 of principal.

        Recorded or still planned alike: $24,604.17 - $397.48 = $24,206.69.
        It pays February's installment, the one its date falls in.  The
        start's displaced balance is unmoved -- the opening held flat,
        $30,000.00 -- since a charge never touches the balance; the reads are
        $24,604.17 on Feb 20 and, recorded, $24,206.69 from Mar 1.
        """
        start = _reset(_STRADDLE_START, "24604.17", is_tracking_start=True)
        payment = _payment(_STRADDLE_DUE, "500.00")
        walk = _example_walk(
            [] if projected else [payment], start,
            through=_STRADDLE_DUE, projections=[payment] if projected else [],
        )
        [mar] = walk.payment_splits
        assert mar.is_projected is projected
        assert (mar.interest, mar.principal, mar.balance_after) == (
            Decimal("102.52"), Decimal("397.48"), Decimal("24206.69"),
        )
        assert mar.charge_date == _STRADDLE_MONTH
        assert [c.owed_before for c in walk.anchor_corrections] == [
            _ZERO, Decimal("30000.00"),
        ]
        if not projected:
            assert [
                _owed_on(walk, on) for on in (_STRADDLE_START, _STRADDLE_DUE)
            ] == [Decimal("24604.17"), Decimal("24206.69")]
        # The what-if extra joins at a charge on or after the projection
        # boundary only -- the day after the latest recorded fact, Mar 2 with
        # the payment recorded, Feb 21 with it planned; February's
        # installment is before it either way, so the re-charge carries none
        # and "an extra $100 a month" leaves Mar 1's split as it is.
        topped = replay_loan_stream(walk.stream, extra_per_period=Decimal("100.00"))
        assert topped.payment_splits == walk.payment_splits

    def test_a_start_on_an_installment_day_charges_that_installment(self):
        """Tracking from Feb 15 itself, no payment due that day; Mar 1's follows.

        The start lands on February's installment day, after its charge, so
        February is the installment standing when it walks, and Mar 1 is its
        own payment: $102.52 / $397.48, as with a start on Feb 20.
        """
        walk = _example_walk(
            [_payment(_STRADDLE_DUE, "500.00")],
            _reset(_STRADDLE_MONTH, "24604.17", is_tracking_start=True),
            through=_STRADDLE_DUE,
        )
        [mar] = walk.payment_splits
        assert (mar.interest, mar.principal, mar.charge_date) == (
            Decimal("102.52"), Decimal("397.48"), _STRADDLE_MONTH,
        )

    def test_the_months_escrow_is_charged_again_with_its_interest(self):
        """The example with $100.00 of escrow a month.

        Mar 1 clears $102.52 of interest and $100.00 of escrow, and $297.48
        is principal, leaving $24,306.69.
        """
        walk = _example_walk(
            [_payment(_STRADDLE_DUE, "500.00")],
            _reset(_STRADDLE_START, "24604.17", is_tracking_start=True),
            through=_STRADDLE_DUE, escrow="100.00",
        )
        [mar] = walk.payment_splits
        assert (mar.interest, mar.escrow, mar.principal, mar.balance_after) == (
            Decimal("102.52"), Decimal("100.00"), Decimal("297.48"),
            Decimal("24306.69"),
        )

    @pytest.mark.parametrize(
        "assertions",
        [
            (_reset(date(2026, 2, 20), "24604.17"),),
            (
                _reset(date(2026, 2, 20), "25000.00", is_tracking_start=True),
                _reset(date(2026, 2, 20), "24604.17"),
            ),
            (
                _reset(date(2026, 2, 20), "25000.00"),
                _reset(date(2026, 2, 20), "24604.17", is_tracking_start=True),
            ),
            (
                _reset(date(2026, 2, 20), "24604.17", is_tracking_start=True),
                _reset(date(2026, 2, 25), "24604.17"),
            ),
            (
                _reset(date(2026, 1, 20), "25000.00"),
                _reset(date(2026, 2, 20), "24604.17", is_tracking_start=True),
            ),
            (
                _reset(date(2026, 1, 20), "25000.00", is_tracking_start=True),
                _reset(date(2026, 2, 20), "24604.17", is_tracking_start=True),
            ),
        ],
        ids=[
            "record-balance-alone",
            "record-balance-after-the-start-on-its-day",
            "record-balance-before-the-start-on-its-day",
            "record-balance-later-in-the-month",
            "start-after-a-record-balance",
            "start-after-an-earlier-start",
        ],
    )
    def test_any_other_balance_keeps_its_clearing(self, assertions):
        """A Record balance in the same place, or a start that is not the loan's first.

        A Record balance clears what stands: alone, on the start's own day
        whichever of the two the loader orders first (the design walks a
        Record balance at its own date, after February's charge), or later in
        the month.  A start dated after another balance is not the loan's
        first.  Each keeps R-R72 part (2): Mar 1 pays $0.00 / $500.00,
        leaving $24,104.17.  On the start's own day the loader closes the day
        on the later statement, $24,604.17 in both orders here, where the
        design would close it on the Record balance: the residual the
        replay's module docstring states, which in the Record-balance-first
        case here would leave $24,500.00 instead.
        """
        walk = _example_walk(
            [_payment(_STRADDLE_DUE, "500.00")], *assertions,
            through=_STRADDLE_DUE,
        )
        [mar] = walk.payment_splits
        assert (mar.interest, mar.principal, mar.balance_after) == (
            _ZERO, Decimal("500.00"), Decimal("24104.17"),
        )

    def test_two_starts_on_the_first_day_charge_on_the_later_ones_figure(self):
        """Two tracking starts on Feb 20, at $25,000.00 and then $24,604.17.

        Both are the loan's first tracking start and no Record balance shares
        their day, so each re-charges February and the later, the day's
        closing balance, stands: Mar 1 pays $102.52 / $397.48, not
        round(25,000.00 x 0.05 / 12) = $104.17.
        """
        walk = _example_walk(
            [_payment(_STRADDLE_DUE, "500.00")],
            _reset(_STRADDLE_START, "25000.00", is_tracking_start=True),
            _reset(_STRADDLE_START, "24604.17", is_tracking_start=True),
            through=_STRADDLE_DUE,
        )
        [mar] = walk.payment_splits
        assert (mar.interest, mar.principal) == (
            Decimal("102.52"), Decimal("397.48"),
        )

    def test_a_second_payment_in_the_month_pays_pure_principal(self):
        """Feb 15's own payment walks before the start; Mar 1's is the month's second.

        Feb 15 pays its own month, $125.00 / $375.00 on $30,000.00 (R-R117),
        so nothing of February is left for Mar 1: it pays $0.00 / $500.00,
        leaving $24,104.17, as a second payment inside one interval always
        does.
        """
        walk = _example_walk(
            [
                _payment(_STRADDLE_MONTH, "500.00"),
                _payment(_STRADDLE_DUE, "500.00"),
            ],
            _reset(_STRADDLE_START, "24604.17", is_tracking_start=True),
            through=_STRADDLE_DUE,
        )
        feb, mar = walk.payment_splits
        assert (feb.interest, feb.principal) == (
            Decimal("125.00"), Decimal("375.00"),
        )
        assert (mar.interest, mar.principal, mar.balance_after) == (
            _ZERO, Decimal("500.00"), Decimal("24104.17"),
        )

    def test_an_overdue_plan_pushed_past_the_next_charge_is_outside_the_month(self):
        """Mar 15's $500.00 recorded; Mar 1's still planned, so pushed to Mar 16.

        Walked behind the facts, the planned payment follows the Mar 15
        charge, so no payment of February's walks between the start and the
        next charge: the start clears February, Mar 15 pays its own month on
        the start's figure, $102.52 / $397.48, and the pushed catch-up pays
        what then stands, nothing: $0.00 / $500.00, leaving $23,706.69.
        Re-charging February would have made Mar 15 pay two months, $205.04.
        """
        walk = _example_walk(
            [_payment(date(2026, 3, 15), "500.00")],
            _reset(_STRADDLE_START, "24604.17", is_tracking_start=True),
            through=date(2026, 3, 15),
            projections=[
                _payment(_STRADDLE_DUE, "500.00", visible_on=date(2026, 3, 16)),
            ],
        )
        mar_15, mar_1 = walk.payment_splits
        assert (mar_15.interest, mar_15.principal) == (
            Decimal("102.52"), Decimal("397.48"),
        )
        assert mar_1.is_projected
        assert (mar_1.interest, mar_1.principal, mar_1.balance_after) == (
            _ZERO, Decimal("500.00"), Decimal("23706.69"),
        )


class TestAProjectionNeverPrecedesAFact:
    """The boundary: the day after the latest recorded payment or assertion."""

    def test_the_boundary_is_the_day_after_the_latest_fact(self):
        """Payments and resets move it; charges do not; no fact means none."""
        facts = LoanEventStream(
            charges=[_charge(date(2026, 12, 1))],
            payments=[_payment(date(2026, 3, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
        )
        assert projection_boundary(facts) == date(2026, 3, 2)
        later_reset = LoanEventStream(
            charges=[], payments=facts.payments,
            resets=[*facts.resets, _reset(date(2026, 6, 30), "9000.00")],
        )
        assert projection_boundary(later_reset) == date(2026, 7, 1)
        assert projection_boundary(LoanEventStream(
            charges=[_charge(date(2026, 2, 1))], payments=[], resets=[],
        )) is None

    def test_an_overdue_projection_is_walked_behind_the_facts(self):
        """The Aug installment skipped, Sept paid: the settled split is unmoved.

        Recorded: origination $10,000.00 (Jan 1), charges Aug 1 and Sept 1
        ($50.00 each, the balance untouched between them), a $500.00 payment
        on the Sept 1 installment.  Projected: the overdue Aug 1 row, still
        open.  In contract order the projection would precede the settled
        payment and hand it a different split; the replay keys it at the
        boundary (Sept 2) instead, so the settled payment clears BOTH charges
        ($100.00 interest, $400.00 principal) exactly as it does with no
        projection in the stream, and the projection pays what stands after
        it: nothing, $500.00 of pure principal.
        """
        facts = LoanEventStream(
            charges=[_charge(date(2026, 8, 1)), _charge(date(2026, 9, 1))],
            payments=[_payment(date(2026, 9, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
        )
        with_plan = LoanEventStream(
            charges=facts.charges, payments=facts.payments,
            resets=facts.resets,
            projections=[
                _payment(
                    date(2026, 8, 1), "500.00", visible_on=date(2026, 9, 16),
                ),
            ],
        )
        alone = replay_loan_stream(facts)
        merged = replay_loan_stream(with_plan)

        [settled] = alone.payment_splits
        assert (settled.interest, settled.principal) == (
            Decimal("100.00"), Decimal("400.00"),
        )
        # The fact prefix of the merged walk IS the facts-only walk.
        assert merged.settled_splits == alone.payment_splits
        assert merged.anchor_corrections == alone.anchor_corrections
        [projected] = merged.projected_splits
        assert projected.is_projected and not settled.is_projected
        assert projected.due_date == date(2026, 8, 1)
        assert projected.visible_on == date(2026, 9, 16)
        assert (projected.interest, projected.principal) == (
            _ZERO, Decimal("500.00"),
        )
        assert projected.balance_after == Decimal("9100.00")
        # One list, walk order: the fact first, the projection behind it.
        assert merged.payment_splits == [settled, projected]

    def test_pushed_projected_events_keep_contract_order_among_themselves(self):
        """Two skipped months behind a later fact: cleared by it, then pure-principal catch-ups.

        Facts: origination $10,000.00 (Jan 1) and a $500.00 payment on the
        June 1 installment; the stream states April's, May's and June's
        charges by hand (the contract's calendar would charge February and
        March too -- this pure-arithmetic case omits them, so June clears
        three months, not five).  Projected: April's and May's catch-ups, pushed to the
        boundary (June 2).  A charge is never pushed (plan step
        recurrence:R16-c-2, rulings R-R72 part (1) and R-R100), so all three
        charges walk before the June fact and it clears them; the catch-ups
        then walk in CONTRACT order among themselves, April before May, with
        nothing standing:

          Jun fact:  150.00 interest (50.00 x 3), 350.00 principal -> 9,650.00
          Apr:         0.00 interest, 500.00 principal -> 9,150.00
          May:         0.00 interest, 500.00 principal -> 8,650.00

        Each catch-up stands under June's charge, the latest walked before it.
        Until R16-c-2 the April and May charges were PROJECTED charges pushed
        with their catch-ups (June 50.00 / 450.00, April 47.75 / 452.25, May
        45.49 / 454.51, each under its own month); the developer approved the
        moved figures (rule 5).
        """
        stream = LoanEventStream(
            charges=[
                _charge(date(2026, 4, 1)),
                _charge(date(2026, 5, 1)),
                _charge(date(2026, 6, 1)),
            ],
            payments=[_payment(date(2026, 6, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
            projections=[
                _payment(date(2026, 4, 1), "500.00", visible_on=date(2026, 6, 16)),
                _payment(date(2026, 5, 1), "500.00", visible_on=date(2026, 6, 16)),
            ],
        )
        june, april, may = replay_loan_stream(stream).payment_splits
        assert (june.interest, june.principal) == (
            Decimal("150.00"), Decimal("350.00"),
        )
        assert (april.interest, april.principal, april.balance_after) == (
            _ZERO, Decimal("500.00"), Decimal("9150.00"),
        )
        assert (may.interest, may.principal, may.balance_after) == (
            _ZERO, Decimal("500.00"), Decimal("8650.00"),
        )
        assert (april.due_date, may.due_date) == (
            date(2026, 4, 1), date(2026, 5, 1),
        )
        assert (april.charge_date, may.charge_date) == (
            date(2026, 6, 1), date(2026, 6, 1),
        )

    def test_a_future_projection_keeps_its_own_date(self):
        """A projection due after the boundary interleaves with the calendar.

        Same facts through Sept; a projection due Oct 1 is keyed at Oct 1, so
        the October charge (dated Oct 1, applied first) stands over it: $48.00
        of interest on the $9,600.00 the September payment left ($500.00 less
        the $100.00 it cleared).  The October charge is the contract's, in the
        stream's one charge list since plan step recurrence:R16-c-2 (ruling
        R-R100); it was a separate PROJECTED charge list before it.
        """
        stream = LoanEventStream(
            charges=[
                _charge(date(2026, 8, 1)),
                _charge(date(2026, 9, 1)),
                _charge(date(2026, 10, 1)),
            ],
            payments=[_payment(date(2026, 9, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
            projections=[_payment(date(2026, 10, 1), "500.00")],
        )
        [_settled, projected] = replay_loan_stream(stream).payment_splits
        assert projected.charge_date == date(2026, 10, 1)
        assert projected.interest == installment_interest(
            Decimal("9600.00"), _RATE, date(2026, 9, 1), date(2026, 10, 1),
            InterestAccrualConventionEnum.MONTHLY_1_12,
        ) == Decimal("48.00")

    def test_the_extra_accrues_only_at_charges_after_the_facts(self):
        """"An extra $100 a month" never reprices a recorded month.

        Charges Aug 1 and Sept 1 fall before the projection boundary (Sept 2,
        the day after the last fact); October's falls after it.  The extra
        accrues at a charge on or after the boundary only (plan step
        recurrence:R16-c-2: one charge list, the boundary deciding; until then
        it accrued at the separate PROJECTED charges), so with $100.00 a month
        the recorded September payment is byte-identical to the no-extra walk,
        and the October projection carries exactly one helping.
        """
        stream = LoanEventStream(
            charges=[
                _charge(date(2026, 8, 1)),
                _charge(date(2026, 9, 1)),
                _charge(date(2026, 10, 1)),
            ],
            payments=[_payment(date(2026, 9, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
            projections=[_payment(date(2026, 10, 1), "500.00")],
        )
        plain = replay_loan_stream(stream)
        topped = replay_loan_stream(stream, extra_per_period=Decimal("100.00"))
        assert topped.settled_splits == plain.settled_splits
        [projected] = topped.projected_splits
        # 500.00 + 100.00 - 48.00 interest on 9,600.00.
        assert projected.principal == Decimal("552.00")


class TestTheOutcomeIsTheRecord:
    """The replay's outcome is read flat, and a chargeless payment names its period."""

    def test_a_payment_no_charge_stands_over_reads_the_calendar_period(self):
        """Ruling R-C's early extra: dated before the first charge, pure principal.

        The stream's calendar supplies the governing period; the outcome
        carries it, ``charge_date`` is ``None``, and the cash is principal.
        The charges are stated by hand, so the calendar's dates are never
        read here -- only its rate periods.
        """
        period = rate_period(_RATE, start_date=date(2026, 1, 1))
        stream = LoanEventStream(
            charges=[_charge(date(2026, 3, 1))],
            payments=[_payment(date(2026, 1, 20), "250.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
            calendar=LoanCalendar(
                origination_date=date(2026, 1, 1), payment_day=1,
                term_months=period.term_months_at_start,
                original_principal=_PRINCIPAL,
                periods=[period], escrow_lines=[],
            ),
        )
        [early] = replay_loan_stream(stream).payment_splits
        assert early.charge is None and early.charge_date is None
        assert early.period is period
        assert (early.interest, early.principal, early.excess) == (
            _ZERO, Decimal("250.00"), _ZERO,
        )
        assert early.cash == Decimal("250.00")

    def test_a_chargeless_payment_with_no_calendar_is_refused(self):
        """No period to name is an error, never a silent default."""
        stream = LoanEventStream(
            charges=[], payments=[_payment(date(2026, 1, 20), "250.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
        )
        with pytest.raises(ValueError, match="non-empty period list"):
            replay_loan_stream(stream)

    def test_the_walk_carries_the_stream_it_replayed(self):
        """A caller holding the walk can replay the same events again."""
        stream = LoanEventStream(
            charges=[_charge(date(2026, 2, 1))],
            payments=[_payment(date(2026, 2, 1), "500.00")],
            resets=[_reset(date(2026, 1, 1), "10000.00")],
        )
        walk = replay_loan_stream(stream)
        assert walk.stream is stream
        assert replay_loan_stream(walk.stream).payment_splits == (
            walk.payment_splits
        )
