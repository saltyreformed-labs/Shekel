"""The target-date search: on hand-built plans, its two silent wrong answers, and the seam on real
loans.
"""

import random
from datetime import date
from decimal import Decimal

from app.services import balance_at
from app.services.balance_at._plan import PlannedPayment
from tests.oracles.loan_forward_fold import plan_payoff_date, plan_required_extra
from app.services.balance_at import BalanceContext
from app.utils.dates import add_months
from tests.oracles.loan_monthly_composition import charge_then_allocate

from ._helpers import _payment, _plan, _create_loan, _attach_derive_extra, _current_period


def _clears_within(seed, plan, extra, target):
    """Whether *extra* a month puts the balance at zero BY *target*.

    The sweep's independent oracle: it re-folds the plan here rather than asking
    the producer, and keys on the payment's EFFECTIVE date -- when its cash
    actually moves -- which is the property "clear by the target" actually means.

    **It folds through the RETIRED ONE-PAYMENT-PER-MONTH composition
    deliberately** (plan step R16-a; ``tests.oracles.loan_monthly_composition``
    since plan step X-au-g-2c-3b-2 deleted it from ``app/``).  Every plan this
    oracle grades puts exactly one payment in each accrual period, and on that
    shape charging a month inside the per-payment step is the same arithmetic as
    charging it as its own event -- so agreeing with this oracle is the
    equivalence claim itself, measured rather than asserted.  A plan with two
    payments in one period is NOT gradeable here and is pinned directly instead
    (:class:`TestChargePerPeriodNotPerPayment`).
    """
    charged = {
        (charge.on_date.year, charge.on_date.month): charge
        for charge in plan.charges
    }
    balance = seed
    for payment in sorted(
        plan.payments, key=lambda p: (p.due_date, p.effective_date),
    ):
        charge = charged[(payment.due_date.year, payment.due_date.month)]
        parts = charge_then_allocate(
            payment.cash + extra, balance,
            charge.period.annual_rate, charge.escrow,
        )
        balance = parts.balance_after
        if balance <= Decimal("0.00"):
            return payment.effective_date <= target
    return False


class TestPlanRequiredExtra:
    """The pure target-date search, on hand-built plans (plan step C8f).

    ``plan_required_extra`` answers "what must I add to every payment to be done
    by X", folded from the SAME plan and seed the payoff and the balance use.  It
    replaced ``loan_resolver.target_date_outlook``, which binary-searched the
    contractual schedule walk -- a walk that amortizes installments nobody paid
    (finding B-9), so it could answer "no extra needed" for a target the loan does
    not actually reach, contradicting the payoff chip on the same page.

    Every case here runs at a 0% rate so the arithmetic is inspectable.
    """

    def test_a_plan_that_already_clears_by_the_target_needs_nothing(self):
        """Two $600 payments clear $1,000 by 2026-02-01, inside the target."""
        plan = _plan([
            _payment(date(2026, 1, 1), "600.00"),
            _payment(date(2026, 2, 1), "600.00"),
        ])
        assert plan_required_extra(
            Decimal("1000.00"), plan, date(2026, 3, 1),
        ) == Decimal("0.00")

    def test_the_searched_extra_is_the_least_that_reaches_the_target(self):
        """$1,000 against two $400 payments leaves $200 -- exactly $100 each.

        Without extra the plan pays $800 of $1,000 and never clears.  Spreading
        the $200 shortfall over the two payments is $100 apiece, so that is the
        answer; a cent less must miss, which the minimality half asserts by
        folding it back through the payoff.
        """
        plan = _plan([
            _payment(date(2026, 1, 1), "400.00"),
            _payment(date(2026, 2, 1), "400.00"),
        ])
        seed, target = Decimal("1000.00"), date(2026, 2, 1)
        assert plan_payoff_date(seed, plan) is None, (
            "precondition: the un-topped-up plan must NOT clear the loan"
        )

        found = plan_required_extra(seed, plan, target)
        assert found == Decimal("100.00")
        # Correctness: the found extra really does reach the target...
        reached = plan_payoff_date(seed, plan, found)
        assert reached is not None and reached <= target
        # ...and minimality: a cent less does not.
        missed = plan_payoff_date(seed, plan, found - Decimal("0.01"))
        assert missed is None or missed > target

    def test_an_unreachable_target_returns_none(self):
        """No installment falls on or before the target, so no amount lands in time."""
        plan = _plan([_payment(date(2026, 5, 1), "400.00")])
        assert plan_required_extra(
            Decimal("1000.00"), plan, date(2026, 1, 1),
        ) is None

    def test_a_retired_loan_needs_nothing(self):
        """A loan owing nothing is already done by any target."""
        assert plan_required_extra(
            Decimal("0.00"), _plan([_payment(date(2026, 1, 1), "400.00")]),
            date(2026, 1, 1),
        ) == Decimal("0.00")


class TestPlanRequiredExtraEdges:
    """The two ways the search can silently return a WRONG amount.

    Both were found by the randomized sweep below, not by hand-written cases, and
    both return a plausible-looking figure that does not do what it claims -- the
    worst failure mode for a number a user acts on.
    """

    def test_the_answer_rounds_UP_so_it_cannot_miss_by_a_fraction(self):
        """A threshold between cents must round UP, never to nearest.

        $1,000 against four $300 payments at 0%: the plan pays $900 by the third
        installment, so clearing by then needs $100 spread over three payments --
        $33.3333... each, which is not a whole cent.  Rounded to NEAREST that is
        $33.33, and 3 x $333.33 = $999.99 leaves a CENT owing, pushing the payoff
        to the fourth installment, past the target.  Rounded UP it is $33.34, and
        3 x $333.34 = $1,000.02 clears.  A sub-cent shortfall costs a whole
        installment because it lands exactly at the payoff boundary.
        """
        plan = _plan([
            _payment(date(2026, 1, 1), "300.00"),
            _payment(date(2026, 2, 1), "300.00"),
            _payment(date(2026, 3, 1), "300.00"),
            _payment(date(2026, 4, 1), "300.00"),
        ])
        seed, target = Decimal("1000.00"), date(2026, 3, 1)

        found = plan_required_extra(seed, plan, target)
        assert found == Decimal("33.34")
        reached = plan_payoff_date(seed, plan, found)
        assert reached is not None and reached <= target
        # The half-up answer is the control: it misses by one installment.
        missed = plan_payoff_date(seed, plan, Decimal("33.33"))
        assert missed is None or missed > target

    def test_a_target_in_the_past_is_unreachable_not_a_huge_number(self):
        """An overdue-but-projected payment must not make a PAST target look met.

        Ruling D1 clamps an overdue projected payment's EFFECTIVE date forward to
        ``as_of + 1d`` but leaves its DUE date in the past.  A reachability test
        keyed on due dates therefore passes for a target that has already gone by,
        the fold "clears" the loan on that past due date, and the search returns a
        six-figure extra for a date on which no money can be paid any more --
        rendered as the panel's headline.  Keyed on the EFFECTIVE date (when the
        cash actually moves) the target is correctly unreachable.
        """
        overdue = PlannedPayment(
            due_date=date(2026, 2, 1),        # already passed...
            effective_date=date(2026, 7, 20),  # ...but the cash moves tomorrow
            cash=Decimal("500.00"),
            is_estimated=False,
        )
        plan = _plan([overdue, _payment(date(2026, 8, 1), "500.00")])
        # A target BEFORE the overdue payment's effective date but AFTER its due
        # date -- the exact window the due-date test got wrong.
        assert plan_required_extra(
            Decimal("100000.00"), plan, date(2026, 6, 19),
        ) is None

    def test_the_upper_bound_is_found_not_assumed(self):
        """A payment smaller than its own interest breaks the obvious bound.

        Paying the whole balance as extra looks like a guaranteed upper bound --
        surely the first installment then clears the loan.  It is not: the ONE
        allocation (``app.utils.money.apply_payment_cash``) takes the standing
        interest out of the cash FIRST, so on
        $100,000 at 25% (a $2,083.33 monthly accrual, quantized by the shared
        split) against $500 payments, an extra of exactly the balance still
        leaves $1,583.33 owing at the first installment.  Clearing by then needs
        the balance PLUS that accrual, less the payment's own cash:
        $100,000.00 + $2,083.33 - $500.00 = $101,583.33, which pays exactly
        $100,000.00 of principal and lands the balance on zero.
        """
        plan = _plan(
            [
                _payment(date(2026, 1, 1), "500.00"),
                _payment(date(2026, 2, 1), "500.00"),
            ],
            rate="0.25",
        )
        seed, target = Decimal("100000.00"), date(2026, 1, 1)
        # The naive bound really does fall short -- this is why it is searched.
        naive = plan_payoff_date(seed, plan, seed)
        assert naive is not None and naive > target

        found = plan_required_extra(seed, plan, target)
        assert found == Decimal("101583.33")
        reached = plan_payoff_date(seed, plan, found)
        assert reached is not None and reached <= target

    def test_every_generated_plan_gets_an_answer_that_holds(self):
        """A seeded sweep: whatever it returns must be true of the fold.

        Property-based, because the two bugs above both produced figures that
        looked entirely reasonable.  For 250 generated loans it asserts the only
        three things the contract promises: a returned extra REACHES the target,
        it is minimal to within a cent, and ``None`` is returned only when no
        installment falls on or before the target.  The seed is fixed, so a
        failure is reproducible rather than a flake.
        """
        rng = random.Random(20260719)
        violations = []
        for trial in range(250):
            seed = Decimal(str(rng.randrange(1000, 500000))) + Decimal("0.37")
            count = rng.randrange(2, 60)
            cash = Decimal(str(rng.randrange(50, 3000)))
            rate = Decimal(rng.choice(["0.00", "0.03", "0.06", "0.12", "0.25"]))
            # A third of the sweeps carry the ruling-D1 clamp on their earliest
            # records: an overdue-but-still-projected payment keeps its PAST due
            # date while its cash moves at ``as_of + 1d``.  Without this the
            # generated plans all had ``effective == due`` and the sweep was
            # blind to every date question that turns on which of the two is
            # read -- which is how finding H1 reached a review.
            clamp_to = add_months(date(2026, 1, 1), 6) if trial % 3 == 0 else None
            payments = []
            for offset in range(count):
                due = add_months(date(2026, 1, 1), offset)
                effective = (
                    max(due, clamp_to) if clamp_to is not None else due
                )
                payments.append(PlannedPayment(
                    due_date=due,
                    effective_date=effective,
                    cash=cash,
                    is_estimated=False,
                ))
            plan = _plan(payments, rate=str(rate))
            # Drawn from BEYOND the plan's span in both directions, not just
            # from its own due dates: a target before the first installment (or
            # in the past) is the case the earlier version of this sweep could
            # never generate, and it is exactly where finding H1 lived.
            target = add_months(
                payments[rng.randrange(0, count)].effective_date,
                rng.randrange(-18, 6),
            )

            found = plan_required_extra(seed, plan, target)
            if found is None:
                # A ``None`` is only honest if NO extra reaches the target.  The
                # oracle is the fold itself at an absurd extra, not a restatement
                # of the producer's own guard.
                huge = seed * 1000 + Decimal("1000000.00")
                if _clears_within(seed, plan, huge, target):
                    violations.append(
                        f"None though {huge} clears by {target} (seed {seed})"
                    )
                continue
            if not _clears_within(seed, plan, found, target):
                violations.append(
                    f"extra {found} does not clear by {target} (seed {seed})"
                )
            if found > Decimal("0.00") and _clears_within(
                seed, plan, found - Decimal("0.02"), target,
            ):
                violations.append(
                    f"extra {found} is not minimal (seed {seed})"
                )
        assert not violations, violations[:5]


class TestLoanRequiredExtraSeam:
    """The seam entry on real loans (plan step C8f)."""

    def test_a_standing_extra_lowers_the_required_top_up(
        self, app, seed_user, seed_periods_today,
    ):
        """F-27's surviving acceptance: paying more already needs less on top.

        The finding F-27 closed was that a user already overpaying was told they
        needed the full raw extra again.  Two identical loans, one carrying a
        $500/mo standing overpayment: the overpayer must need STRICTLY less added
        to hit the same target, because their plan's cash already carries the
        extra.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            plain, _ = _create_loan(
                seed_user, current, current.start_date, name="Extra None",
            )
            paying, _ = _create_loan(
                seed_user, current, current.start_date, name="Extra Rich",
            )
            _attach_derive_extra(seed_user, paying, Decimal("500.00"))
            target = add_months(today, 240)

            ctx = BalanceContext.build(seed_user["user"].id)
            plain_extra = balance_at.loan_required_extra(plain, ctx, target)
            paying_extra = balance_at.loan_required_extra(paying, ctx, target)
            assert plain_extra is not None and plain_extra > Decimal("0.00")
            assert paying_extra is not None
            assert paying_extra < plain_extra

    def test_it_agrees_with_the_payoff_the_chip_shows(
        self, app, seed_user, seed_periods_today,
    ):
        """"No extra needed" and the payoff chip cannot contradict each other.

        The H1 defect in one assertion: both read the same fold, so the search
        reports ``0.00`` for a target at or after the derived payoff and a real
        amount for one before it.  The pre-C8f search walked the contractual
        schedule and could answer ``0.00`` for a target the chip's payoff was
        months past.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="Agreement",
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            payoff = balance_at.loan_payoff_date(account, ctx)
            assert payoff is not None
            assert balance_at.loan_required_extra(
                account, ctx, payoff,
            ) == Decimal("0.00")
            earlier = add_months(payoff, -12)
            needed = balance_at.loan_required_extra(account, ctx, earlier)
            assert needed is not None and needed > Decimal("0.00")
