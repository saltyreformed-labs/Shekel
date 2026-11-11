"""Plan step X-au-g-2c-3a: a loan's cash is divided in exactly ONE place.

Finding **N-409** was not a typo in one branch.  Four producers each stated
"cash covers interest and escrow first, the rest pays the debt down", and one of
them -- ``loan_payment_service._engine_prep``'s escrow floor -- disagreed, so a
``$1,700.00`` payment against a ``$1,910.95`` installment read as exactly on
schedule while the balance seam put the owner ``$210.95`` further behind.

**The duplication was FORCED BY THE LAYERING rather than chosen**, which is what
these tests pin.  The rule lived in ``loan_ledger._split`` (import closure 23
modules); ``amortization_engine`` (closure 2) and ``rate_period_engine``
(closure 3) sit BELOW it, so reaching it was the cycle ``loan_ledger._split ->
rate_period_engine -> amortization_engine``.  Each restated it instead.  The
remedy was to move :func:`~app.utils.money.apply_payment_cash` down beside
the CHARGE half, shared from that leaf since E-24 (``accrue_monthly_interest``
until plan step recurrence:R16-d, :func:`~app.utils.money.installment_interest`
since) -- so both halves of a loan payment's arithmetic are reachable from
every walk.

Two properties are pinned, and they fail for different reasons:

* :class:`TestEveryWalkAllocatesThroughTheOneRule` -- each producer's split
  EQUALS the shared rule's over a swept input space.  It fails if a producer
  restates the rule, however subtly.
* :class:`TestTheAllocationSitsBelowEveryWalk` -- the shared rule is still
  reachable from every walk.  It fails if the rule is moved back up, which is
  the state that MADE restating it necessary.  A green first class with a red
  second one means the duplication is about to be re-forced.
"""

import ast
import pathlib
import random
from datetime import date
from decimal import Decimal

import pytest

from app.enums import InterestAccrualConventionEnum
from app.services.amortization_engine._projection import (
    _apply_contractual_payment,
)
from app.services.installment_calendar import accrual_window
from app.utils.money import (
    apply_payment_cash,
    installment_interest,
    round_money,
)

# Anchored to THIS file, not the working directory: the closure tests below
# walk the tree, and a relative path would silently grade an empty set from
# any cwd but the repo root.
APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"

ZERO = Decimal("0.00")
PAY_DATE = date(2026, 10, 1)
#: The window the installment due on ``PAY_DATE`` charges for: September.
PAY_WINDOW = accrual_window(PAY_DATE, PAY_DATE.day)
# Fixed seed: a sweep that changes run to run cannot be quoted in a review.
SWEEP_SEED = 20260901
SWEEP_TRIALS = 2_000


def _sweep():
    """Yield ``(balance, annual_rate, interest, cash, extra)`` across the space.

    Deliberately includes the boundaries each producer branches on: a payment
    below the period interest (negative amortization), one that exactly clears
    the balance, and one that overruns it.
    """
    rng = random.Random(SWEEP_SEED)
    for _ in range(SWEEP_TRIALS):
        balance = Decimal(str(round(rng.uniform(0.01, 400_000), 2)))
        rate = Decimal(str(round(rng.uniform(0.0, 0.15), 5)))
        interest = installment_interest(
            balance, rate, *PAY_WINDOW,
            InterestAccrualConventionEnum.MONTHLY_1_12,
        )
        # A third of the draws land at or past the balance, so the cap and
        # overrun arms are swept rather than left to chance on a wide uniform.
        cash = rng.choice([
            Decimal(str(round(rng.uniform(0.0, 6_000), 2))),
            round_money(balance + interest),
            round_money(balance + interest + Decimal(str(rng.uniform(0, 500)))),
        ])
        extra = Decimal(str(round(rng.choice(
            [0.0, 0.0, rng.uniform(0, 3_000)]), 2)))
        yield balance, rate, interest, cash, extra


class TestEveryWalkAllocatesThroughTheOneRule:
    """Each walk's split equals :func:`apply_payment_cash`'s, over the sweep.

    Three walks since plan step R7d-g-3: the projection's override path
    (``_apply_override_payment``, the planned-outlay arm the loan resolver's
    committed slice rode) went with that slice when the balance seam's fold
    became the one planned walk (ruling **R-R88**), so the arm this class
    graded beside the two below no longer exists to restate anything.
    """

    def test_the_sweep_reaches_every_branch_it_claims_to(self):
        """The sweep is only evidence if it visits the arms it names.

        Without this, a change that narrowed the generator would leave the
        three tests below green while testing one branch each.
        """
        seen = set()
        for balance, _rate, interest, cash, _extra in _sweep():
            parts = apply_payment_cash(cash, balance, interest, ZERO)
            if parts.principal < 0:
                seen.add("negative_amortization")
            if parts.excess > 0:
                seen.add("overrun")
            if parts.balance_after == ZERO and parts.excess == ZERO:
                seen.add("exact_payoff")
            if parts.balance_after > 0 and parts.principal > 0:
                seen.add("ordinary")
        assert seen == {
            "negative_amortization", "overrun", "exact_payoff", "ordinary",
        }, f"sweep missed branches: {seen}"

    def test_the_contractual_path_allocates_through_the_one_rule(self):
        """``amortization_engine._apply_contractual_payment`` restates nothing.

        ``is_last_month`` stays its own and is asserted separately: forcing the
        final row to absorb the residue is a SCHEDULE-closing rule, not a
        statement about how cash divides.
        """
        for balance, _rate, interest, cash, extra in _sweep():
            principal, payment, applied_extra, new_balance = (
                _apply_contractual_payment(
                    balance, interest, cash, extra, False)
            )
            parts = apply_payment_cash(cash, balance, interest, ZERO)
            if parts.excess > 0 or parts.balance_after <= 0:
                assert (principal, payment) == (balance, balance + interest)
                assert (applied_extra, new_balance) == (ZERO, ZERO)
            else:
                assert principal == parts.principal
                assert payment == cash
                assert new_balance == round_money(
                    parts.balance_after - applied_extra)

    def test_the_last_scheduled_month_absorbs_the_residue_regardless(self):
        """``is_last_month`` closes the schedule and is NOT the allocation.

        Pinned separately so the test above cannot be read as saying the final
        row is a split like any other.
        """
        balance, interest, cash = (
            Decimal("5000.00"), Decimal("25.00"), Decimal("100.00"))
        assert _apply_contractual_payment(
            balance, interest, cash, ZERO, True,
        ) == (balance, balance + interest, ZERO, ZERO)
        # The same inputs on a NON-final month leave the balance standing, so
        # the assertion above is about ``is_last_month`` and nothing else.
        assert _apply_contractual_payment(
            balance, interest, cash, ZERO, False,
        )[3] > ZERO


class TestTheAllocationSitsBelowEveryWalk:
    """The shared rule stays reachable from every walk that needs it."""

    @staticmethod
    def _closure(start: str) -> set[str]:
        """Return *start*'s transitive ``app.`` import closure, by package."""
        def package(path: pathlib.Path) -> str:
            parts = list(
                path.resolve().relative_to(APP_ROOT.parent).with_suffix("").parts
            )
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if len(parts) >= 3 and parts[1] in {
                    "services", "utils", "models", "routes"}:
                return ".".join(parts[:3])
            return ".".join(parts)

        def normalise(name: str) -> str:
            parts = name.split(".")
            if len(parts) >= 3 and parts[1] in {
                    "services", "utils", "models", "routes"}:
                return ".".join(parts[:3])
            return name

        edges: dict[str, set[str]] = {}
        for path in APP_ROOT.rglob("*.py"):
            source = package(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            # ``ast.walk``, not ``tree.body``: a FUNCTION-LOCAL import is the
            # one shape that keeps the coupling while dodging the cycle
            # Python would otherwise raise, so it is the regression this has
            # to see.  ``from app.services import x`` also has to be joined
            # with its alias -- its ``node.module`` is bare ``app.services``,
            # which a prefix test on ``"app.services."`` silently misses.
            for node in ast.walk(tree):
                targets: set[str] = set()
                if isinstance(node, ast.ImportFrom) and node.level == 0:
                    if node.module and node.module.startswith("app"):
                        targets.add(node.module)
                        targets.update(
                            f"{node.module}.{alias.name}"
                            for alias in node.names
                        )
                elif isinstance(node, ast.Import):
                    targets.update(
                        alias.name for alias in node.names
                        if alias.name.startswith("app")
                    )
                for target in targets:
                    if target.startswith("app"):
                        edges.setdefault(source, set()).add(normalise(target))
        seen: set[str] = set()
        stack = [start]
        while stack:
            for dependency in edges.get(stack.pop(), ()):
                if dependency not in seen:
                    seen.add(dependency)
                    stack.append(dependency)
        return seen

    def test_the_rule_depends_on_nothing_in_services(self):
        """``app.utils.money`` is a LEAF, which is what makes it reachable.

        The moment it imports a service, some walk below that service can no
        longer call it and has to restate the rule -- exactly the state
        ``loan_ledger._split`` was in.
        """
        closure = self._closure("app.utils.money")
        offenders = {name for name in closure
                     if name == "app.services"
                     or name.startswith("app.services.")}
        assert not offenders, (
            f"app.utils.money reaches {sorted(offenders)}; the allocation is "
            "no longer below every walk"
        )

    @pytest.mark.parametrize("walk", [
        "app.services.amortization_engine",
        "app.services.loan_ledger",
        "app.services.balance_at",
    ])
    def test_every_walk_can_reach_the_rule(self, walk):
        """Each loan walk imports the leaf the rule lives in.

        Three since plan step recurrence:R16-e deleted the fourth, the rate
        engine's ``replay_schedule``; its row step and its entry here went with
        it (ruling plan#3382).
        """
        assert "app.utils.money" in self._closure(walk), (
            f"{walk} cannot reach app.utils.money, so it cannot call the one "
            "allocation and will have to restate it"
        )
