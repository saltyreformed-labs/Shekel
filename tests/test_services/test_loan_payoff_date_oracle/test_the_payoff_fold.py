"""The pure fold-to-zero on hand-built plans, and one charge per period however many payments fall
in it.
"""

import random
from datetime import date
from decimal import Decimal

from app.services.balance_at._plan import PlannedPayment
from tests.oracles.loan_forward_fold import (
    fold_forward,
    plan_interest_in_year,
    plan_payoff_date,
    plan_required_extra,
)
from app.utils.dates import add_months
from tests.oracles.loan_monthly_composition import charge_then_allocate

from ._helpers import _payment, _plan


class TestPlanPayoffDate:
    """The pure fold-to-zero, on hand-built plans (arithmetic anyone can check)."""

    def test_folds_to_zero_returns_the_clearing_installments_due_date(self):
        """The DUE date of the first payment whose running balance hits zero.

        At 0% interest every dollar is principal.  Seed $1000: after a $600
        payment the balance is $400; the next $600 payment clears the $400
        (principal capped at the balance), so the loan is paid off on the SECOND
        installment's due date.
        """
        seed = Decimal("1000.00")
        plan = _plan([
            _payment(date(2027, 1, 1), "600.00"),
            _payment(date(2027, 2, 1), "600.00"),
        ])
        # 1000 - 600 = 400 (2027-01-01); 400 - 400 = 0 (2027-02-01).
        assert plan_payoff_date(seed, plan) == date(2027, 2, 1)

    def test_folds_in_due_order_not_input_order(self):
        """The fold sorts by due date, so payoff keys on the contract, not input.

        Given a $900 payment due 01-01 and a $600 payment due 02-01, in REVERSE
        input order: due-order folds 900 first (1000 -> 100), then 600 (100 -> 0)
        on 02-01.  Folding in input order would clear it on 01-01 instead -- so
        asserting 02-01 proves the due-order sort.
        """
        seed = Decimal("1000.00")
        plan = _plan([
            _payment(date(2027, 2, 1), "600.00"),
            _payment(date(2027, 1, 1), "900.00"),
        ])
        # Due order: 1000 - 900 = 100 (01-01); 100 - 100 = 0 (02-01).
        assert plan_payoff_date(seed, plan) == date(2027, 2, 1)

    def test_already_retired_seed_returns_none(self):
        """A seed at or below zero has no forward crossing -- ``None``, not a date.

        A retired loan owes nothing at the projection seed; the first planned
        payment must NOT be mistaken for a payoff (the caller badges it via
        ``is_retired`` instead).
        """
        plan = _plan([_payment(date(2027, 1, 1), "600.00")])
        assert plan_payoff_date(Decimal("0.00"), plan) is None
        assert plan_payoff_date(Decimal("-5.00"), plan) is None

    def test_negative_amortization_never_reaches_zero_returns_none(self):
        """A payment below the period interest grows the balance -- ``None``.

        Seed $1000 at 12%/yr accrues $10.00 interest a month; a $5.00 payment
        leaves principal -$5.00, so the balance rises to $1005 and never clears.
        """
        seed = Decimal("1000.00")
        plan = _plan(
            [_payment(date(2027, m, 1), "5.00") for m in range(1, 7)],
            rate="0.12000",
        )
        assert plan_payoff_date(seed, plan) is None

    def test_empty_plan_returns_none(self):
        """No payments means the balance never moves to zero -- ``None``."""
        assert plan_payoff_date(Decimal("1000.00"), _plan([])) is None

    def test_truncated_plan_that_never_clears_returns_none(self):
        """The PURE fold on a plan whose installments run out above zero -- ``None``.

        ``plan_payoff_date`` folds exactly the plan it is handed: two $300 payments
        at 0% take $1000 down to $400, then the plan ENDS with the balance still
        positive, so no installment drives it to ``<= 0``.  This is the pure
        function's contract (a plan too short -> ``None``), independent of the
        SEAM's ESTIMATED tier, whose recurring payment's occurrences run past the
        contractual date so a real underpaying loan clears there instead
        (:class:`TestPayoffTailExtension`).
        """
        seed = Decimal("1000.00")
        plan = _plan([
            _payment(date(2027, 1, 1), "300.00"),
            _payment(date(2027, 2, 1), "300.00"),
        ])
        # 1000 - 300 = 700 (01-01); 700 - 300 = 400 (02-01); plan ends above zero.
        assert plan_payoff_date(seed, plan) is None


class TestChargePerPeriodNotPerPayment:
    """A period charges ONCE however many payments fall inside it (plan step R16-a).

    **The defect these pin is that the fold had no time dimension.**  While
    interest was charged inside the per-payment step, the payment COUNT was the
    clock: measured on a production clone, 30 payments of ``$531.94`` fourteen
    days apart charged the identical ``$1,096.34`` as 30 a month apart, split for
    split, so the Van Loan paid off in half the time modelled the same interest
    Nothing in the live corpus
    puts two payments in one month -- both loans are MONTH-unit with one
    definition each -- so the frozen production baseline is byte-identical and
    CANNOT see this change.  These are what see it.

    Every case runs at 12%/yr on a $10,000 seed, where a month's interest is
    exactly $100.00, so the two models are told apart by inspection rather than
    by a rounding difference.
    """

    _SEED = Decimal("10000.00")
    _JANUARY = Decimal("100.00")   # 10,000 x 12% / 12

    def _two_in_january(self, *, rate="0.12", escrow="0.00"):
        """A plan paying twice inside one accrual period, on the 1st and the 15th."""
        return _plan(
            [
                _payment(date(2026, 1, 1), "500.00"),
                _payment(date(2026, 1, 15), "500.00"),
            ],
            rate=rate, escrow=escrow,
        )

    def test_a_second_payment_in_one_period_clears_no_fresh_interest(self):
        """Two payments inside January charge January ONCE.

        The charge lands on the period's opening (the 1st, its earliest due), so
        the first payment splits $500 into $100.00 interest and $400.00
        principal, leaving $9,600.00.  The second payment clears no fresh charge
        and is $500.00 of pure principal, leaving **$9,100.00**.

        Charging per PAYMENT gives $9,196.00 instead -- the second payment would
        accrue a second month on $9,600.00 ($96.00) and pay only $404.00 down --
        so the balance below tells the two models apart outright.
        """
        plan = self._two_in_january()
        balances = fold_forward(
            self._SEED, date(2020, 1, 1), plan, [date(2026, 1, 31)],
        )
        assert balances[date(2026, 1, 31)] == Decimal("9100.00")
        assert plan_interest_in_year(self._SEED, plan, 2026) == self._JANUARY

    def test_the_charge_is_applied_BEFORE_a_payment_sharing_its_date(self):
        """January's interest accrues on the balance the 1st has not yet reduced.

        The charge and the first payment fall on the same day.  Charged first,
        January costs 12% / 12 of $10,000.00 = $100.00; allocated first, it would
        cost 12% / 12 of $9,500.00 = $95.00.  Asserting $100.00 pins the order.
        """
        assert plan_interest_in_year(
            self._SEED, self._two_in_january(), 2026,
        ) == self._JANUARY

    def test_escrow_is_impounded_once_a_period_not_once_a_payment(self):
        """A month's escrow is a month's obligation, whoever pays it.

        At 0% with a $200.00 monthly escrow, the first payment covers the escrow
        and pays $300.00 down ($9,700.00); the second owes no further escrow and
        pays $500.00 down, leaving **$9,200.00**.  Impounding per payment would
        take $200.00 twice and leave $9,400.00 -- an escrow the owner never paid.
        """
        plan = self._two_in_january(rate="0.00", escrow="200.00")
        balances = fold_forward(
            self._SEED, date(2020, 1, 1), plan, [date(2026, 1, 31)],
        )
        assert balances[date(2026, 1, 31)] == Decimal("9200.00")

    def test_the_hypothetical_extra_is_per_MONTH_not_per_payment(self):
        """``plan_required_extra`` answers what its own name promises.

        $1,000.00 at 0% against three $300.00 payments -- two in January (the 1st
        and the 15th) and one in February -- pays $900.00 and never clears.  The
        $100.00 shortfall spread over the TWO months the plan spans is **$50.00 a
        month**.  Added per PAYMENT it would be $33.34, which is $100.02 of extra
        against a $100.00 gap: the figure the panel reports as "add this much a
        month" would be a third short of what a fortnightly payer must actually
        find.
        """
        plan = _plan([
            _payment(date(2026, 1, 1), "300.00"),
            _payment(date(2026, 1, 15), "300.00"),
            _payment(date(2026, 2, 1), "300.00"),
        ])
        seed, target = Decimal("1000.00"), date(2026, 2, 1)
        assert plan_payoff_date(seed, plan) is None, (
            "precondition: the un-topped-up plan must NOT clear the loan"
        )
        found = plan_required_extra(seed, plan, target)
        assert found == Decimal("50.00")
        reached = plan_payoff_date(seed, plan, found)
        assert reached is not None and reached <= target
        missed = plan_payoff_date(seed, plan, found - Decimal("0.01"))
        assert missed is None or missed > target

    def test_a_closed_loan_charges_nothing_and_refunds_the_cash(self):
        """Past payoff a period charges no interest and no escrow.

        The first payment clears the whole $500.00 seed; the February charge then
        has nothing to accrue on, and February's payment is a refund in full --
        so the balance stays at zero rather than going NEGATIVE by a phantom
        paydown or POSITIVE by a phantom accrual.
        """
        plan = _plan(
            [
                _payment(date(2026, 1, 1), "500.00"),
                _payment(date(2026, 2, 1), "500.00"),
            ],
            rate="0.12", escrow="50.00",
        )
        seed = Decimal("445.00")   # 500 cash - 4.45 interest - 50.00 escrow
        balances = fold_forward(
            seed, date(2020, 1, 1), plan,
            [date(2026, 1, 31), date(2026, 3, 1)],
        )
        assert balances[date(2026, 1, 31)] == Decimal("0.00")
        assert balances[date(2026, 3, 1)] == Decimal("0.00")
        assert plan_interest_in_year(seed, plan, 2026) == Decimal("4.45")

    def test_a_generated_sweep_of_MONTHLY_plans_folds_exactly_as_it_did(self):
        """The equality claim over the input SPACE, not over two live loans.

        The production baseline (``verify_r7d_estimate_equality.py``) is
        byte-identical across R16-a, but it can only say so about two monthly
        loans at one rate each -- it cannot vary the axis the change lives on.
        This sweeps 300 generated ONE-PAYMENT-PER-MONTH plans over rate, escrow,
        cash, seed, length and the ruling-D1 clamp, and grades the new
        charge-then-allocate fold against the RETIRED fold R16-a replaced:
        charge a month INSIDE each payment's split
        (``tests.oracles.loan_monthly_composition.charge_then_allocate``, which
        composes exactly that, and which plan step X-au-g-2c-3b-2 moved out of
        ``app/`` into the oracle package precisely so this comparison survives
        the deletion).  Every payoff date and every year's interest must agree to
        the cent.

        The seed is fixed, so a failure is reproducible rather than a flake.
        """
        rng = random.Random(20260826)
        for trial in range(300):
            rate = Decimal(rng.choice(
                ["0.00", "0.02875", "0.06", "0.06875", "0.12", "0.25"],
            ))
            escrow = Decimal(rng.choice(["0.00", "100.00", "616.99"]))
            cash = Decimal(str(rng.randrange(50, 4000)))
            seed = Decimal(str(rng.randrange(500, 400000))) + Decimal("0.37")
            count = rng.randrange(1, 48)
            clamp_to = add_months(date(2026, 1, 1), 4) if trial % 4 == 0 else None
            payments = []
            for offset in range(count):
                due = add_months(date(2026, 1, 1), offset)
                payments.append(PlannedPayment(
                    due_date=due,
                    effective_date=(
                        max(due, clamp_to) if clamp_to is not None else due
                    ),
                    cash=cash,
                    is_estimated=False,
                ))
            plan = _plan(payments, rate=rate, escrow=str(escrow))

            # The fold R16-a replaced, transcribed here over the same inputs.
            balance, payoff_before = seed, None
            interest_before: dict[int, Decimal] = {}
            for payment in payments:
                parts = charge_then_allocate(
                    payment.cash, balance, rate, escrow,
                )
                balance = parts.balance_after
                year = payment.effective_date.year
                interest_before[year] = (
                    interest_before.get(year, Decimal("0.00")) + parts.interest
                )
                if balance <= Decimal("0.00") and payoff_before is None:
                    payoff_before = payment.due_date

            assert plan_payoff_date(seed, plan) == payoff_before, (
                f"trial {trial}: payoff moved (rate {rate}, escrow {escrow}, "
                f"cash {cash}, seed {seed}, {count} payments)"
            )
            for year in sorted(interest_before):
                assert plan_interest_in_year(seed, plan, year) == (
                    interest_before[year]
                ), f"trial {trial}: {year} interest moved"
