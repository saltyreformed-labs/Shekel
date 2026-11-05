"""
Shekel Budget App -- the obligations aggregator reads the DESTINATION's stop
(plan step R7d-e).

The money cases of that step, beside ``test_obligations_aggregator`` rather
than inside it because that module is at its line ceiling.  A loan payment
stops when its loan does, and until this step the only way that fact reached
the ``/obligations`` and ``/savings`` totals was a CACHE: ten chokepoints wrote
the loan's derived payoff into ``budget.recurrence_rules.end_date``, the
authored bound's own column, and ``recurrence.has_ended`` read that column.
So a retired loan's payment stayed in the Recurring surface's totals and the
emergency-fund floor until some chokepoint happened to run, and left on the
day it did -- a fact about when a page was saved rather than about the loan.

The aggregator reads the composed door now (ruling **R-R56** makes the door
read the app-written column as the cache it is), and the derived stop answers
"has this ended" under ruling **R-R57**: the definition owes no occurrence on
or after the day asked.  **Every case here is one direction of the money.**  A
loan that is FINISHED leaves both totals (the inflating direction); a loan
whose cached bound is EARLIER than its payoff stays in them (the understating
direction, plan ledger row **D35**'s shape, and the more dangerous of the two).

The fixture loan is ``$12,000.00`` at 5% over 24 months, and its MONTHLY
payment TRACKS the loan (derive mode, the loan door's own default), so it
commits the contract's level payment a month while it commits anything:
:data:`_CONTRACT`.  **It read ``$200.00`` until plan step X-bp**, the fixture's
stored snapshot, because the aggregator read the template's
``default_amount``; it prices a definition by the rule that prices its rows
now (ruling plan#2693: "a tracking payment's figure follows the loan"), and
the money each case is about -- whether the payment is IN the total at all --
is unchanged.  Today is frozen mid-2026 so the loan fixtures do not drift with
the calendar, and every pass is pinned to a day the case names.
"""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.services import (
    balance_at,
    obligations_aggregator,
    savings_dashboard_service,
)
from app.services.balance_at import BalanceContext, is_standing_loan_payment
from app.services.recurrence import EndsOnDate, reauthor_rule, recurrence_spec
from tests._test_helpers import (
    create_loan_account,
    freeze_today,
    make_loan_payment_template,
    make_retired_loan_payment,
)
from tests.oracles.recurrence_baseline import MONTHLY

#: The frozen "today": inside the seeded 52-period schedule, after the retired
#: loan closes and on the live loan's origination.
_TODAY = date(2026, 7, 1)
#: The retired loan: originates 2026-05-01 with a ``payment_day`` of 1, so its
#: first installment is 2026-06-01, and the true-up on the 15th follows ONE
#: fired occurrence -- the derived stop is ``ClosesOn(2026-06-15)``.
_ORIGINATED = date(2026, 5, 1)
_CLEARED = date(2026, 6, 15)
#: What one installment of every loan here costs, and so -- monthly rule, one
#: installment a month -- what its tracking payment commits a month.  The
#: level payment on $12,000.00 at 5% over 24 months, with no escrow line:
#: r = 0.05 / 12; 12000 * r / (1 - (1 + r) ** -24) = 50 / (1 - 1/1.1049413)
#: = 526.4569, rounded to the cent.
_CONTRACT = Decimal("526.46")


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today so the loan fixtures do not depend on the calendar."""
    freeze_today(monkeypatch, _TODAY)


def _live_loan_payment(seed_user):
    """Return a MONTHLY ``$200.00`` payment against a loan originating today.

    The control's loan: still owing, first installment 2026-08-01, payoff the
    door's own tests pin at 2028-07-01 for this shape.

    Args:
        seed_user: The owner.

    Returns:
        ``(loan, template)``, committed.
    """
    loan = create_loan_account(
        seed_user, db.session, name="Live Loan",
        principal=Decimal("12000.00"), rate=Decimal("0.05000"),
        term=24, origination_date=_TODAY, payment_day=1,
    )
    template = make_loan_payment_template(
        db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
    )
    db.session.commit()
    return loan, template


def _cache_the_bound(template, bound: date, ctx) -> None:
    """Write *bound* into the rule's column through the real write door.

    Named for what it did until plan step R7d-g -- reproduce the chokepoints'
    cache -- and kept because the column holds exactly what the door leaves
    and the test's precondition can assert it; what it writes now is a stop
    an owner authored (ruling **R-R82**).

    Args:
        template: The loan payment whose rule takes the bound.
        bound: The date to store.
        ctx: The owner's read pass: its calendar is what the write door
            re-authors against, and its loan-resolution memo is what the
            standing-payment identity is read off.
    """
    reauthor_rule(
        template.recurrence_rule,
        replace(
            recurrence_spec(template.recurrence_rule),
            end_bound=EndsOnDate(on=bound),
        ),
        ctx.calendar(),
    )
    db.session.commit()
    assert template.recurrence_rule.end_date == bound, (
        "precondition: the column holds the cached bound"
    )
    assert is_standing_loan_payment(template, ctx), (
        "precondition: this is the definition whose bound the app writes"
    )


class TestADerivedStopLeavesTheObligationsTotal:
    """A loan payment leaves the totals on the day its loan closed."""

    def test_a_retired_loans_payment_leaves_the_total_on_the_day_the_loan_closed(
        self, app, seed_user, seed_periods_52,
    ):
        """Live the day before the true-up, gone from the true-up day on.

        The rule's ``end_date`` column is NULL (asserted), so the authored
        bound alone says "never ends" and before this step the aggregator
        counted ``$200.00`` a month, indefinitely, for a loan that was
        finished.  Read ON the day the loan closed, the definition owes
        nothing on or after that day -- its 2026-07-01 installment falls past
        the close -- so under R-R57 the commitment has ended that day, where
        "the closing date has passed" would have counted it one day more.
        """
        with app.app_context():
            loan, tpl = make_retired_loan_payment(
                db.session, seed_user,
                origination_date=_ORIGINATED, cleared_on=_CLEARED,
            )
            assert tpl.recurrence_rule.end_date is None, (
                "precondition: nothing stored could have supplied the stop"
            )
            owner = seed_user["user"].id
            on_close = BalanceContext.build(owner, _CLEARED)
            assert balance_at.loan_figures(loan, on_close).closing_date == (
                _CLEARED
            ), "precondition: the loan closed on the day it was trued to zero"

            # The day before: the loan still owes, so the payment commits.
            assert obligations_aggregator.template_monthly_or_none(
                tpl, BalanceContext.build(owner, _CLEARED - timedelta(days=1)),
            ) == _CONTRACT
            # The day it closed, and after: nothing is owed on or after it.
            assert obligations_aggregator.template_monthly_or_none(
                tpl, on_close,
            ) is None, (
                "a payment against a loan trued to zero today still counts as "
                "a monthly commitment"
            )
            assert obligations_aggregator.committed_monthly(
                [tpl], BalanceContext.build(owner, _TODAY),
            ) == Decimal("0.00")

    def test_a_cached_payoff_no_longer_keeps_a_finished_payment_counted(
        self, app, seed_user, seed_periods_52,
    ):
        """The column holds a FUTURE payoff a chokepoint cached before the clear.

        The shape the step's sentence names: the payment left the totals "on
        the day some chokepoint last ran".  A loan projected to pay off in
        2028 is cleared early; its rule's column still holds the 2028 date the
        last sync wrote, and ``has_ended`` read that column -- a date that has
        not passed -- so the payment stayed in both totals for two more years
        unless a later chokepoint happened to rewrite it.  The door composes
        BOTH halves and the EARLIER binds: the loan's own closing date is
        the day it cleared, so the payment is finished whatever a later date
        in the column says.  (Until plan step R7d-g that column was the
        cache and the door read it as none, ruling **R-R56**; nothing writes
        a cache there now and a stored date is the owner's word, ruling
        **R-R82** -- the composition answers the same either way, because
        the derived stop is the earlier.)
        """
        with app.app_context():
            _loan, tpl = make_retired_loan_payment(
                db.session, seed_user,
                origination_date=_ORIGINATED, cleared_on=_CLEARED,
            )
            today = BalanceContext.build(seed_user["user"].id, _TODAY)
            _cache_the_bound(tpl, date(2028, 5, 1), today)

            assert obligations_aggregator.template_monthly_or_none(
                tpl, today,
            ) is None, (
                "a cached 2028 payoff kept a loan cleared in June 2026 in the "
                "committed total"
            )

    def test_an_owners_stop_on_the_standing_payment_is_honoured_by_the_total(
        self, app, seed_user, seed_periods_52,
    ):
        """Ruling **R-R82** in the committed total: a stored stop binds, on both sides of it.

        Until plan step R7d-g this case pinned plan ledger row **D35**'s
        understating direction: the column held a cache EARLIER than the
        payoff and the door read it as none (ruling **R-R56**), so the
        payment stayed in the total.  A date in that column is the owner's
        word now.  A live loan originating on the frozen day, first
        installment 2026-08-01, whose standing payment carries an owner's
        stop of 2026-08-15: read on 2026-07-20 the 08-01 installment is still
        owed and the payment counts (:data:`_CONTRACT` a month, one
        installment a month);
        read on 2026-08-05 it has ended -- under ruling **R-R45** "ended"
        means no occurrence is owed on or after the day asked, and the next
        1st falls past the stop -- so it leaves the total while the loan
        still owes twenty-three installments, which is what the owner said
        and what the summed plan (ruling **R-R37**) prices too.  The stop
        follows the rule's start so the stored pair is the ordered one the
        window CHECK admits.
        """
        with app.app_context():
            loan, tpl = _live_loan_payment(seed_user)
            owner = seed_user["user"].id
            authored = date(2026, 8, 15)
            assert tpl.recurrence_rule.starts_on <= authored, (
                "precondition: the stored pair must be the ordered one the "
                "window CHECK admits"
            )
            _cache_the_bound(tpl, authored, BalanceContext.build(owner, _TODAY))
            before_the_stop = BalanceContext.build(owner, date(2026, 7, 20))
            after_the_stop = BalanceContext.build(owner, date(2026, 8, 5))
            assert balance_at.loan_figures(
                loan, after_the_stop,
            ).payoff_date is None, (
                "precondition: with its only payment stopped after one "
                "installment the loan never pays off"
            )

            assert obligations_aggregator.template_monthly_or_none(
                tpl, before_the_stop,
            ) == _CONTRACT, (
                "a stop with an installment still owed before it dropped a "
                "live loan payment out of the committed total"
            )
            assert obligations_aggregator.template_monthly_or_none(
                tpl, after_the_stop,
            ) is None, (
                "a stop the owner authored, and the pass has passed, kept the "
                "payment in the committed total"
            )

    def test_the_emergency_fund_floor_drops_a_retired_loans_payment(
        self, app, seed_user, seed_periods_52,
    ):
        """The ``/savings`` surface end to end, with its control beside it.

        ``avg_monthly_expenses`` is the higher of the last six periods' settled
        checking expenses and the committed floor -- the aggregator's total
        over the checking account's outgoing definitions.  With nothing
        settled the floor IS the aggregator's answer, so a retired loan's
        payment leaving from checking inflated the emergency-fund baseline by
        its monthly figure until this step.  The control is a second loan
        still owing: its identical payment must count -- the contract,
        :data:`_CONTRACT` a month -- or a floor of zero would prove only that
        the floor reads nothing.
        """
        with app.app_context():
            make_retired_loan_payment(
                db.session, seed_user,
                origination_date=_ORIGINATED, cleared_on=_CLEARED,
            )
            owner = seed_user["user"].id

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(owner, _TODAY),
            )
            assert data["avg_monthly_expenses"] == Decimal("0.00"), (
                "a retired loan's payment inflates the emergency-fund floor"
            )

            # The control: the same payment against a loan that still owes.
            _live_loan_payment(seed_user)

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(owner, _TODAY),
            )
            assert data["avg_monthly_expenses"] == _CONTRACT, (
                "the control does not fire: a live loan's tracking payment "
                "must set the committed floor at the contract a month"
            )
