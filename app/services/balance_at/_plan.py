"""Balance-at-T seam -- BUILDING a loan's forward plan: records, not schedule rows.

Plan step **C6a** (``docs/audits/balance_architecture/README.md``).  A loan's
future balance is a fold over what it will be CHARGED and what it will PAY;
this module builds that model (:class:`LoanForwardPlan`) and :mod:`._plan_fold`
folds it.

**The two halves are separate values, and plan step R16-a is why.**  A loan
charges interest because time passed and impounds escrow because a month began;
a payment moves cash on whatever date its recurring definition names.  While the
charge rode ON the payment record, the payment COUNT was the clock -- measured on
a production clone, 30 payments of ``$531.94`` fourteen days apart charged the
same ``$1,096.34`` as 30 a month apart, split for split, so a loan paid twice as
fast modelled the identical interest.  :class:`AccrualCharge` is the time half
and :class:`PlannedPayment` the cash half, and neither can be derived from the
other.

The PAYMENTS come in two tiers:

* **PLANNED** -- the loan's PROJECTED transfers, each as the leg of its parent
  row INTO the loan (:func:`app.services.loan_loaders.projected_income_legs`;
  plan step **balance:X-bi-6a**, ruling **R-BAL13** -- a projected payment is
  derived from its parent transfer, not read off a shadow row), each at what
  the amount model resolves the PARENT to
  (:func:`app.services.cash_ledger.resolve_amount`, the SAME cash the
  checking side shows leaving).  A record is the evidence a payment will
  happen; where the record's due date has already passed but it has not
  settled, it is clamped forward to ``as_of + 1d`` -- "a plan cannot have
  already happened" (ruling D1).
* **ESTIMATED** -- **what a generate pass over the owner's whole schedule
  would write, priced as those rows would be priced** (plan step **R16-b-2**).
  EVERY active recurring transfer into the loan is walked on its OWN cadence
  under its AUTHORED closing (:func:`._plan_definitions.estimated_from_definitions`); each
  occurrence the schedule can place that NO ROW IN ANY STATE answers is dated
  as generation would date its row and priced through the amount model's own
  arm (:func:`app.services.cash_ledger.definition_cash`).  A loan with no
  definition at all is priced by its CONTRACT, one installment per future
  installment no record covers (:func:`_estimated_from_contract`), dated and rated by
  the contractual schedule
  (:func:`~app.services.balance_at._resolution.contractual_schedule_from_origination`)
  -- its installment DATE and P&I, never its ``remaining_balance``, which this
  re-folds.

**Why every definition, and why on its own cadence** (rulings **R-R35**,
**R-R36**, **R-R37**, **R-R65**; findings **D47**, **D48**).  Until R16-b-2
this tier synthesized one slot per CONTRACTUAL month and priced every slot
from ONE definition picked by lowest id, so it modelled the contract's rhythm
carrying one definition's price: re-authoring the Van's payment to every
paycheck left the estimate byte-identical at 12 installments a year against
the rule's 26.07, and re-pointing a ``$500.00`` every-paycheck sweep at the
Mortgage priced every uncovered installment at ``$500.00`` against a
``$616.99`` escrow, read the payoff ``None`` and grew the balance to
``$1,059,869.99`` by 2053 -- where summing each definition's occurrences
answered ``2034-10-01``.  The other two tiers (the settled fold and the
PLANNED tier) already summed every transfer into the loan with no template
filter; this was the one outlier, and four tie-break remedies were refused
to delete the question rather than answer it (R-R35).

**Why the fold prices an occurrence NO ROW answers, past or future** (ruling
**R-R64**, finding **D46**).  Generation has been exhaustive over the
schedule since R17, so "no row at all" for an occurrence the schedule places
means "not generated yet" -- and a cancelled or deleted row still ANSWERS
its occurrence (the owner un-planned it).  Before R-R64 an occurrence dated
before ``as_of`` that no row answered when a pass read the loan was priced by
neither tier (B-9's "the past is the ACTUAL fold's"), so the payoff read one
installment late and generation bounded by that payoff wrote one installment
past the loan's life: ``$1,200`` at 3% over three months, ``as_of``
2026-03-01, the 02-15 row wiped by the reset door -- 4 rows against 3.
**Bounded by the SCHEDULE**, which the ruling's first statement did not say
and which is what makes it correct on live data: an occurrence the owner's
pay calendar cannot place (before its first payday under ``CONTAINING_DATE``)
is neither generated nor estimated.  The Mortgage's rule starts 2019-01-01
against a schedule opening 2026-03-26; priced literally, its 87 pre-schedule
occurrences would pay ``$166,252.65`` the day after ``as_of``.

**Why the DEFINITION and not the contract** (plan step **R7d-a**).  This tier
priced every uncovered slot at the contract's P&I, whatever the loan's own
recurring payment said it would pay, so "what will this loan be paid in month
M" had two answers and which one the fold used depended on whether the row had
been WRITTEN yet.  R16-b-2 finished that argument: the estimate is now the
row's own pricing function over the columns the row would carry, so the two
answers are one by construction rather than by two arms agreeing.  Two
consequences of the contract-priced tier, both measured on a production clone
with a PLANTED underpayment -- the Van Loan's definition moved to ``$300.00``
against its ``$531.94`` contractual installment, rolled back after each
probe:

* the plan SWITCHED figures at the materialized horizon, projecting a payoff of
  ``2030-02-22`` with rows to 2028-07 and ``2030-04-22`` with rows to 2029-01 --
  the owner's figure inside the horizon and the servicer's past it;
* and the payoff moved with materialization.  ``regenerate_pay_periods`` deletes
  the rebuildable tail of pay periods (``budget.transfers.pay_period_id``
  CASCADEs) and repopulates, and the payoff read AT that moment was
  ``2029-02-22`` against ``2030-02-22`` before and after -- twelve months early,
  on the very figure the recurrence's closing bound is derived from.

One rule for both tiers closes both: the loan is modelled as paying what the
owner has told it to pay, resolved AS OF each installment, materialised or not.
**Unplanted, on the developer's own data, it moves ``$0.00``** -- 776 forward
figures byte-identical -- and the reason is worth stating, because it is a
precondition rather than a general result: both templates state exactly P&I plus
an escrow that is CONSTANT across the whole horizon, so ``stated price - escrow``
equals the contractual P&I at every installment.  A future-dated escrow version
would break that equality, and there is none to measure against.

**Why this replaces the schedule walk (finding B-9).**  The retired forward walk
(``account_projection.forward_balance_at_date``, deleted at step C6b) amortized
one contractual installment per month whether or not any payment was recorded, so
an overdue installment nobody paid still paid the loan down.  Here an installment
pays the loan down only where a payment RECORD stands behind it (PLANNED) or where
the owner's definitions say the loan will be paid (ESTIMATED); an occurrence the
owner UN-PLANNED -- a cancelled or deleted row -- pays nothing, and since R16-b-2
its month is CHARGED anyway (rulings **R-R37**, **R-R71**; finding **D53**), so
a delinquent balance grows in the projection rather than holding flat.  What
B-9 keeps is the narrower claim: the fold never invents a payment nothing
stands behind -- and never one the write door would refuse: an occurrence at or
before the loan's origination (ruling R-C, the door's
:func:`~app.services.loan_loaders.precedes_origination`) is dropped with every
other payment at or before the loan's latest assertion, of which the
origination is the first (:func:`loan_plan`, ruling **R-R72**), because
generation cannot write it and the fold would erase it if it could.

**The CHARGE calendar is the contract's, and it is not built here** (plan
step **R16-b-2**, rulings **R-R68** and **R-R71**, findings **D53** and
**D54**; since plan step **recurrence:R16-c-2**, rulings **R-R72**, **R-R89**
and **R-R100**).  A loan is charged on EVERY contractual installment from
origination, whether or not a payment lands in it, in the settled walk and the
forward plan alike: the plan carries the loan's contract terms
(:attr:`LoanForwardPlan.calendar`) and the leaf charges the whole timeline
through its last event (:func:`~app.services.loan_ledger.with_contract_charges`),
so no month can be charged by two calendars (D54's double charge) or by none
(D53's past half: a skipped month the posted ledger never charged).  Until
R16-c-2 this module built a second calendar -- the installments after the
loan's latest assertion, minus the ``(year, month)`` slots the settled walk had
charged -- and that set of slots was the partition the step deleted.  Deriving
the calendar from the payments was exact only while every month held one: a
quarterly definition would have charged 40 months where the contract owes 120.
**The loan's latest assertion bounds the PAYMENTS** (ruling **R-R72**): a
planned row or an estimated occurrence whose installment is due at or before it
is not folded, because the settled walk will not fold it either once it
settles -- it walks the payment and then resets at the anchor
(:func:`~app.services.loan_ledger.replay_loan_events`), so its cash never
reaches the post-assertion balance; an adversarial review of R16-b-2 measured
the plan folding projected rows a true-up had already subsumed, money that
vanished the day they settled.  The same assertion clears every charge
standing before it (R-R72 part (2); the loan's first balance, when that
balance is a tracking start, charges the installment it lands in again, on
its own figure, when that installment's own payment walks after it and no
Record balance shares its day, ruling **R-R118**), which is what lets the
calendar run from origination: a loan configured mid-life has its
pre-tracking months charged and cleared by the balance stated at setup,
which plan step ``R20`` records as
the assertion it is (a ``tracking_start``, finding **REC-519**).  A loan
configured before R20 without one reads as unpaid since origination -- which
is what its records say, and not a bound this module guesses around.

**Why in the seam, not the ``loan_ledger`` leaf.**  The plan composes the loan's
projected records, its live D3 cash, and the resolver's contractual schedule --
all above the pure leaf -- so it is a SEAM responsibility, exactly as
:func:`app.services.balance_at.positions` composes the past fold with the forward
projection.  What :mod:`._plan_fold` does NOT own is the arithmetic: since plan
step X-au-g-2c-3b-2 it maps this model onto
:func:`app.services.loan_ledger.replay_loan_events`, the ONE replay the settled
walk runs, so a PLANNED / ESTIMATED payment is charged and allocated
byte-identically to an ACTUAL settled one.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services import escrow_calculator, loan_loaders
from app.services.loan_ledger import LoanCalendar
from app.services.cash_ledger import (
    AmountBasis,
    resolve_amount,
    transfer_pricing_load_options,
)
from app.services.installment_calendar import installment_dates, installment_of
from app.services.loan_loaders import loan_payment_due_date
from app.services.rate_period_engine import due_after_anchor, period_for_date
from app.services.transfer_legs import TransferLeg, parent_of
from app.utils.dates import add_months
from app.utils.money import round_money

from ._context import BalanceContext
from ._memoize import _memoize_once, require_scenario
from ._plan_definitions import estimated_from_definitions
from ._plan_records import _ONE_DAY, LoanForwardPlan, PlannedPayment
from ._resolution import (
    contractual_schedule_from_origination,
    resolved_loan,
)

_ZERO_MONEY = Decimal("0.00")

# How far past the CONTRACTUAL payoff the ESTIMATED tier keeps synthesizing the
# level monthly payment (finding N-16).  A loan paying below contract leaves a
# balance behind the contractual schedule, so its fold does not reach zero at the
# contractual last installment; these extra installments let it clear a few months
# later -- a real (slightly-later) payoff instead of "no payoff".  The cap bounds
# a SEVERE underpayment (never clears within five years past contract) back to the
# ``None`` the fold reports for genuine non-amortization -- the drift that far past
# contract is what C7's payment-drift warning exists to surface.  It costs nothing
# for a healthy loan: the fold reaches zero AT the contractual date, so
# :func:`._plan_fold.timeline_payoff_date` returns that FIRST crossing and these
# later installments fold to no-ops (the allocator's closed-loan arm on a zero
# balance).
_PAYOFF_EXTENSION_MONTHS = 60


@dataclass(frozen=True)
class _ForwardInputs:
    """The resolved, record-free context a forward plan is built and folded from.

    Bundles the loan facts both tiers of :func:`loan_plan` read -- its contract
    terms (each installment's rate and P&I, its escrow, its due day) and the
    read pass's as-of clamp -- so the builders take one cohesive value instead
    of loose arguments, and the loan is resolved once.  The terms are the
    plan's own :class:`~app.services.loan_ledger.LoanCalendar` since plan step
    recurrence:R16-c-2 (ruling **R-R100**), the ONE value the leaf charges the
    timeline from, rather than three loose fields beside a copy of them.

    **It carried the loan's STANDING payment until plan step R16-b-2**, the one
    definition ``active_recurring_transfer_template`` picks by lowest id, which
    priced every ESTIMATED installment.  The tier sums EVERY definition now
    (:func:`._plan_definitions.estimated_from_definitions`) and prices each occurrence through
    the amount model's own arm, so no definition rides here: the PLANNED tier
    prices its rows through the amount model and the ESTIMATED tier prices its
    occurrences through the same arms, and a standing extra lands in each
    exactly once because both read it off the definition's settings row.

    Attributes:
        calendar: The loan's contract terms -- its rate periods
            (:func:`app.services.loan_resolver.resolve_periods`), its escrow
            lines with their full version history, and its contractual due day
            (the due-date fallback) -- as the plan carries them.
        as_of: The read pass's as-of; the clamp floor is ``as_of + 1d`` and the
            past/future boundary is ``as_of`` itself.
    """

    calendar: LoanCalendar
    as_of: date


def _planned_from_legs(
    legs: list[TransferLeg],
    basis: AmountBasis,
    fwd: _ForwardInputs,
) -> list[PlannedPayment]:
    """Build the PLANNED tier: one record per projected transfer into the loan.

    Each still-projected transfer into the loan -- as the
    :class:`~app.services.transfer_legs.TransferLeg` of its parent row
    (plan step **balance:X-bi-6a**, ruling **R-BAL13**) -- becomes a
    :class:`PlannedPayment` at what a SCREEN would show for it, which is what
    the amount model RESOLVES the parent to
    (:func:`~app.services.cash_ledger.resolve_amount` of the leg's parent).  It was
    ``_planned_from_shadows`` until that step, taking the projected shadow rows
    and a map priced over their ids; a shadow's answer was its parent's (rule
    5), so the figure is the same and the walk is one row shorter.

    **It read ``settled_contribution`` with the live map laid over it by hand
    until plan step X-au-g-2c-1**, and that was the SECOND unrouted reader of a
    projected loan-side shadow -- the same row class, the same accessor, the
    same refusal.  The first was
    ``loan_payment_service.get_payment_history``; routing only that one would
    have left this to 500 the moment the cutover emptied the column, on
    ``/savings`` and every surface that folds a loan's forward plan.  An
    adversarial review found it by censusing the accessor's callers rather than
    trusting the finding's own count of them, which is the lesson: **"one
    unrouted reader" was itself an unmeasured claim.**

    The two-line merge it replaced -- a resolved figure with a live override on
    top -- was ``display_amounts_by_id``, extracted at X-au-c2b so one
    composition lived in one place rather than at two call sites (finding
    **N-224**'s shape).  Both cutovers have since deleted the override term
    itself, so that function collapsed onto ``amounts_by_key`` and the merge has
    no second half left to get wrong.

    **The cycle a much older draft blamed is DELETED and was never the
    reason.**  It read: asking the resolver here would ask the loan to price
    the rows its own price is derived from, because pricing routed
    ``resolve_transaction_amount`` -> ``LoanPricing.derive_cash`` ->
    ``_resolve_loan_basis`` -> ``load_loan_context`` ->
    ``get_payment_history``.  Pricing reads the loan's TERMS and nothing else
    -- the pass's :class:`~app.services.loan_ledger.LoanCalendar` since plan
    step recurrence:R25 deleted ``_resolve_loan_basis`` -- so it loads no
    payment history at all.

    **Finding N-266 (a) is CLOSED at plan step X-au-g-2c-1, and its DIAGNOSIS
    was wrong twice before its remedy was right.**  It first recorded an
    irreducible CYCLE: the rule that priced a loan payment routed back through
    the payment feed.  Plan step X-au-g-1 deleted that path, leaving the
    conclusion standing on something smaller -- ``get_payment_history`` priced
    each row through
    :func:`~app.services.row_valuation.settled_contribution`, which refused a row
    whose plan was derived (since plan step X-bx it refuses any row that has not
    settled, whatever prices it) -- and the row was restated as **"ONE unrouted
    reader"**.  That count was itself unmeasured, and an adversarial review of
    X-au-g-2c-1 censused the accessor: there were **TWO**, and the second is
    this function.  Both are routed now, so the loan-side INCOME leg is
    declarable; had only the named one moved, the cutover would have 500'd
    every surface that folds a loan's forward plan.

    **The other six callers of that accessor really are settled-only**, which
    is what makes "two" a census rather than a second guess (a seventh,
    ``cash_ledger.settled_cash_leg``, is deleted at plan step
    ``balance:X-bi-4a``): ``loan_ledger._events.loan_event_stream``
    (``._split.split_one_payment`` until plan step X-au-g-2c-3b-2),
    ``loan_posting_service._sync`` and ``._display``,
    ``savings_dashboard_service._metrics``, and the spending report's
    ``_window`` and ``_breakdown`` -- each loading rows filtered to the settled
    statuses in SQL.

    **What has NOT changed is the DATA, and that is a fact about production
    rather than about the loan.**  ``ck_transactions_amount_ownership`` is a
    biconditional -- ``(amount_source_id IS NULL) = (estimated_amount IS NOT
    NULL)`` -- so a row states EITHER what prices it or its own figure, never
    both.  Every loan-payment shadow is still on the second side of it:
    re-measured against production 2026-09-01 (stamp ``a4c6f1d92b73``), all 58
    shadows and all 175 transfers carry ``amount_source_id IS NULL``, so the
    resolver classifies them ``AmountRule.OWN`` and answers from the very
    column the old fallback read.  **This routing therefore moves ``$0.00``**;
    what it removes is the interval in which stamping that column would have
    broken this surface.

    **Until then a projected row's figure is a stored copy of its definition's
    price and nothing keeps the two equal** (finding **N-401**, owned by
    ``X-au-f``).  They are equal today -- all 48
    projected loan shadows match their definition to the cent on the same clone
    -- but an edit to a generator-written row moves the derived payoff with no
    signal: halving the Van Loan's 24 future rows while the definition sat at
    ``$531.94`` moved it ``2029-02-22`` -> ``2030-04-22``.

    **Reading the DEFINITION here instead is not the remedy, and it is a money
    defect in its own right.**  It was built and reverted: an ad-hoc
    ``$2,100.00`` payment into a loan whose contractual installment is
    ``$2,035.15`` was repriced to ``$2,035.15``, discarding ``$64.85`` of
    principal the owner is actually paying
    (``test_loan_plan_assembly.test_a_projected_record_makes_its_slot_planned_not_estimated``
    is what caught it), and the underpayment direction is worse -- a ``$500.00``
    payment would project as ``$2,035.15`` and claim a payoff the owner never
    reaches.  BOTH directions of this column are wrong, which is the whole
    argument for the cutover: ``estimated_amount`` carries two facts, the plan's
    price and what the OWNER said, and no reading rule can tell them apart.
    ``amount_source_id`` is what distinguishes them.  **Stamping it used to
    FORCE this call site to move, and that is why it moved FIRST**: the old
    fallback raised on a NULL column, so the cutover that empties it would have
    taken this surface down in the interval before the move.  Routing ahead of
    the stamp is byte-identical -- every one of these rows carries
    ``amount_source_id IS NULL`` today, so the resolver dispatches to
    ``AmountRule.OWN`` and answers from the same column -- which is what makes
    it safe to do early rather than late.

    **It resolves NO rate and NO escrow since plan step R16-a**: those belong to
    the period, not to the payment, and the leaf's calendar resolves them on
    the installment's own date
    (:func:`~app.services.loan_ledger.with_contract_charges`, plan step
    recurrence:R16-c-2), which for a record due on the contractual day IS this
    record's due date, so ruling D5's contract time is unchanged.  What this
    builds is cash and its two dates.

    Args:
        legs: The loan's projected payments as legs of their parents
            (:func:`app.services.loan_loaders.projected_income_legs`).
        basis: The pass's own :class:`~app.services.cash_ledger.AmountBasis`,
            which a derive-mode loan payment's parent prices through.
        fwd: The resolved :class:`_ForwardInputs`.

    Returns:
        One :class:`PlannedPayment` per projected leg (``is_estimated=False``).
    """
    planned: list[PlannedPayment] = []
    clamp_floor = fwd.as_of + _ONE_DAY
    for leg in legs:
        due = loan_payment_due_date(leg, fwd.calendar.payment_day)
        # ONE producer, no fallback.  The ``is None`` dance this replaced
        # existed because a live cash of ``Decimal("0")`` is a real answer (a
        # waived payment) and truthiness would have priced the shadow off the
        # column the loan superseded.  There is no column left to fall back to
        # and no override left to choose between: the parent's resolved amount
        # is the leg's only answer.
        planned.append(PlannedPayment(
            due_date=due,
            effective_date=max(due, clamp_floor),
            cash=resolve_amount(parent_of(leg), basis),
            is_estimated=False,
        ))
    return planned


def _estimated_from_contract(
    contractual: list,
    recorded_dues: list[date],
    fwd: _ForwardInputs,
) -> list[PlannedPayment]:
    """Build the ESTIMATED tier for a loan with NO definition: the contract's own installments.

    Walks the pure contractual schedule
    (:func:`app.services.balance_at._resolution.contractual_schedule_from_origination`)
    and synthesizes a :class:`PlannedPayment` for every FUTURE installment
    (``payment_date >= as_of``) that no payment already covers, at the
    contract's P&I plus that installment's escrow.  Nothing else is known about
    how such a loan will be paid, so the contract is the only estimate there is
    -- and it is the CONTRACT's figure, residual last row included, rather than
    a definition's level payment.

    **The one arm plan step R16-b-2 left here.**  Until that step this
    function was the whole ESTIMATED tier, pricing every uncovered slot from
    the loan's standing payment; a loan WITH definitions is estimated by
    :func:`._plan_definitions.estimated_from_definitions` now, each definition on its own
    cadence and by occurrence identity rather than by month slot.  A strictly-PAST
    installment (``payment_date < as_of``) is never synthesized here: with no
    definition there is nothing generation would write, so ruling **R-R64**
    has no occurrence to price and B-9's rule stands for this arm.

    **An installment is COVERED when a record is due inside its interval** --
    from its own due date up to the next installment's, the interval a
    payment's charge is read from and, since ruling **R-R104**, its cash is
    priced on (:func:`~app.services.installment_calendar.installment_of`; ruling
    **R-R89**, "B: contract interval"; plan step recurrence:R16-c-2).  Until
    that step a record covered its CALENDAR month (finding **D55**): for a
    loan due on the 22nd, a payment on the 10th covered the installment twelve
    days after it rather than the one it pays into.  Two kinds of record cover
    one, and BOTH matter -- the contractual synthesis is a schedule-row
    estimate, so it can collide with a real payment:

    * a **PLANNED** record -- a projected transfer this pass will fold forward;
    * a **settled** payment the pass's recorded stream already folds -- one
      settled by ``as_of`` whose installment is due at or after ``as_of`` (an
      early- or on-day-settled payment).  Without this the ESTIMATED tier would
      re-synthesize an installment the facts already paid, and the timeline's
      fold would subtract its principal a SECOND time (understating the debt by
      one installment).

    **The escrow PRICES the installment here and is CHARGED by the period**, and
    plan step R16-a is what separated the two.  The contract's installment is
    escrow-INCLUSIVE as the owner pays it, so this tier reads the escrow to
    know what the installment costs; what the fold then backs out of principal
    is the CHARGE the leaf's calendar states for the installment
    (:func:`~app.services.loan_ledger.with_contract_charges`), once, however
    many payments fall in it.

    **Past the contractual last row it keeps synthesizing** the level monthly
    payment for up to :data:`_PAYOFF_EXTENSION_MONTHS` more months (finding N-16):
    an UNDERPAYING loan is a balance behind the contractual schedule, so it has not
    reached zero at the contractual date, and these installments let it clear a few
    months later -- a real payoff rather than the ``None`` a truncated plan would
    report.  A HEALTHY or overpaying loan has already folded to zero by the
    contractual date, so :func:`._plan_fold.timeline_payoff_date` returns THAT
    crossing and these fold to no-ops (the balance cap) -- it cannot move.

    Args:
        contractual: The pure contractual schedule from origination to payoff.
        recorded_dues: The due dates of every PLANNED record and every settled
            payment the pass has seen, in any order.
        fwd: The resolved :class:`_ForwardInputs` (its calendar's rate periods
            govern each installment's rate, its escrow lines price every
            synthesized installment, and its installment sequence names each
            record's interval and continues past the contract
            (:func:`_extension_dates`); its ``as_of`` is the past/future
            boundary).

    Returns:
        One :class:`PlannedPayment` per uncovered future contractual installment
        (``is_estimated=True``), plus the post-contractual extension installments.
    """
    estimated: list[PlannedPayment] = []
    clamp_floor = fwd.as_of + _ONE_DAY
    calendar = fwd.calendar
    extension = _extension_dates(contractual, calendar)

    def _interval(due: date) -> date | None:
        """The installment whose interval *due* falls in, or None before the first."""
        return installment_of(calendar.origination_date, calendar.payment_day, due)

    covered = {_interval(due) for due in recorded_dues} - {None}

    def _synthesize(due: date, contractual_pi: Decimal) -> None:
        """Append one uncovered future ESTIMATED installment."""
        if due < fwd.as_of or _interval(due) in covered:
            # The past is ACTUAL-only for a loan nothing generates into (B-9 /
            # D1); an installment a PLANNED record or a settled payment
            # already answers would double-count here.
            return
        # The installment's OWN escrow, on its OWN due date -- ruling D5's
        # contract time, the same date and function the PLANNED tier and the
        # genesis split read (``_installment_cash``), so an escrow version
        # effective mid-horizon reaches every tier alike.
        escrow = escrow_calculator.escrow_monthly_as_of(
            fwd.calendar.escrow_lines, due,
        )
        estimated.append(PlannedPayment(
            due_date=due,
            effective_date=max(due, clamp_floor),
            cash=round_money(contractual_pi + escrow),
            is_estimated=True,
        ))

    for row in contractual:
        _synthesize(row.payment_date, row.payment)

    # Extend past the contractual payoff so an underpaying loan clears a few months
    # late instead of reporting no payoff (N-16).  The contractual P&I past the
    # schedule is the last rate period's (period_for_date returns it for any date
    # past the periods).  The fold caps a healthy loan's extra installments to
    # no-ops.
    for due in extension:
        _synthesize(due, period_for_date(fwd.calendar.periods, due).period_pi)
    return estimated


def _extension_dates(contractual: list, calendar: LoanCalendar) -> list[date]:
    """Return the :data:`_PAYOFF_EXTENSION_MONTHS` installment dates past the contract's last.

    The contract-only estimate's extension and the definition walk's window
    (finding N-16: an underpaying loan clears late, and a plan that stops at the
    contract reads as no payoff): the loan's own installment sequence
    (:func:`~app.services.installment_calendar.installment_dates`, the one calendar)
    continued past its last contractual row.  Until plan step
    recurrence:R16-c-2 it stepped a month count from that last row with
    ``add_months``, which keeps a clamped day: a contract ending on a
    February 28th for a loan due on the 31st continued on the 28th.  It was
    also the charge calendar's reach, which the leaf now takes from the
    timeline's own last event (ruling **R-R100**).

    **Counted, not bounded by a date.**  The sequence is read to one month past
    the extension (``add_months`` of the last row, one month further) and the
    first :data:`_PAYOFF_EXTENSION_MONTHS` installments after the contract are
    kept.  A date bound at exactly sixty months carries the last row's clamp
    and can fall short of the sixtieth installment: a contract ending on a
    clamped February 28th for a loan due on the 31st bounds at the 28th five
    years on, and in a leap year the installment there is the 29th.

    Args:
        contractual: The pure contractual schedule from origination to payoff.
        calendar: The loan's contract terms.

    Returns:
        The extension dates, ascending, one per month after the last
        contractual installment; empty for an empty schedule.
    """
    if not contractual:
        return []
    last_due = contractual[-1].payment_date
    return [
        due
        for due in installment_dates(
            calendar.origination_date,
            calendar.payment_day,
            add_months(last_due, _PAYOFF_EXTENSION_MONTHS + 1),
        )
        if due > last_due
    ][:_PAYOFF_EXTENSION_MONTHS]


def loan_plan(account: Account, ctx: BalanceContext) -> LoanForwardPlan:
    """Return *account*'s forward model -- what it will PAY, and the terms it is charged on.

    The unified forward record stream a loan's projected balance folds (see the
    module docstring): every projected transfer into the loan at its resolved cash,
    plus what every definition paying into the loan would generate that no row
    answers yet -- or, for a loan with no definition, the contract's own
    installments -- out to payoff and the post-contractual extension, LESS
    every payment due at or before the loan's latest balance assertion (the
    assertion subsumes it, ruling R-R72); and, beside them, the loan's contract
    terms (:class:`~app.services.loan_ledger.LoanCalendar`), from which the
    leaf charges the whole timeline (ruling **R-R100**).  The value carries NO
    balance and no charge; the seam appends its payments to the loan's recorded
    stream as the projection and replays the whole timeline once
    (:func:`._loan_stream.loan_timeline`, plan step recurrence:R16-c-1).

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the projected transfers and the resolution, its
            calendar places the definitions' occurrences, and its ``as_of`` is
            the clamp floor and the past/future boundary.

    Returns:
        The loan's :class:`LoanForwardPlan` -- its payments ascending by
        ``(effective_date, due_date)``.  No payment and no calendar when
        *account* is not a configured loan (no
        :class:`~app.models.loan_params.LoanParams`).

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        AmountUnresolvable: When a definition states no price for an
            occurrence it names (see :func:`._plan_definitions.estimated_from_definitions`).
    """
    require_scenario(ctx)
    resolved = resolved_loan(account, ctx)
    if resolved is None:
        return LoanForwardPlan(payments=[], calendar=None)
    params = resolved.params
    rate_changes = resolved.context.rate_changes
    # What the pass's recorded stream already accounts for, read off the
    # pass's OWN walk (the stream this plan is appended to, bounded once at
    # its load, ruling R-R91) rather than derived again from the rows: its
    # settled payments, its latest balance assertion, and the contract terms
    # it was charged on.  The terms are the WALK's, not built here a second
    # time (plan step recurrence:R16-c-2, rule 14): the plan carries the one
    # calendar the facts were charged on (ruling R-R100), so the timeline the
    # seam composes charges both on one object.  The opening is always in
    # that stream, so a loan not yet originated answers its origination date
    # with no default spelled here.
    facts = ctx.loan_walk(account).stream
    calendar = facts.calendar
    fwd = _ForwardInputs(calendar=calendar, as_of=ctx.as_of)

    # The pass's OWN loan derivation, not a second one built here: this line
    # called ``live_loan_transfer_amounts`` directly while the cash fold built a
    # basis that called it again, so one request resolved the same loan twice
    # (finding **N-268**'s shape).  Plan step X-au-c2b made the derivation a
    # read-pass value, so both readers ask the same one.
    planned = _planned_from_legs(
        loan_loaders.projected_income_legs(
            account.id, ctx.scenario_id,
            options=transfer_pricing_load_options(),
        ),
        ctx.amounts(),
        fwd,
    )

    last_anchor = max(reset.on_date for reset in facts.resets)
    contractual = contractual_schedule_from_origination(params, rate_changes)
    if resolved.definitions and contractual:
        # The walk's window: the extension past the contract, or the same
        # span past the READ when the loan has outlived it -- a matured loan
        # still owing, with a live definition, is paid by that definition's
        # occurrences beyond the extension (an adversarial review of this
        # step found the window ending in 2025 for a loan read in 2026, so
        # the plan held only the saved horizon's rows and the payoff read
        # ``None`` where the definition cleared it).
        estimated = estimated_from_definitions(
            account, resolved, ctx, fwd.as_of,
            through=max(
                _extension_dates(contractual, calendar)[-1],
                add_months(fwd.as_of, _PAYOFF_EXTENSION_MONTHS),
            ),
        )
    else:
        # No definition at all: the contract is the only estimate there is.
        estimated = _estimated_from_contract(
            contractual,
            [payment.due_date for payment in planned]
            + [payment.on_date for payment in facts.payments],
            fwd,
        )

    # An installment due at or before the loan's latest assertion is INSIDE
    # that assertion, planned or estimated (ruling R-R72): the settled walk
    # resets at the anchor after walking the day's payments, so a row due
    # before it contributes nothing to the post-assertion balance once it
    # settles -- and a plan that folded it would move by its whole cash the
    # day it did.  The same assertion clears every charge standing before it
    # in the replay (R-R72 part (2)), save the loan's first tracking start's
    # re-charge of the installment it lands in (ruling R-R118; the replay
    # states when it applies).
    payments = sorted(
        (
            payment for payment in planned + estimated
            if due_after_anchor(last_anchor, payment.due_date)
        ),
        key=lambda payment: (payment.effective_date, payment.due_date),
    )
    return LoanForwardPlan(payments=payments, calendar=calendar)


def memoized_plan(account: Account, ctx: BalanceContext) -> LoanForwardPlan:
    """Return *account*'s forward plan for this read pass, built at most once.

    The seam's ONE funnel for the plan: it fills the read pass's per-loan plan
    cache (:attr:`~app.services.balance_at.BalanceContext.plans`) from
    :func:`loan_plan` through the shared store-once primitive
    (``_memoize._memoize_once``), so a build happens at most once per account per
    pass and every later read replays it.  Every seam reader that folds a loan's
    future -- the balance (:func:`~app.services.balance_at.positions`), the derived
    payoff, the required-extra search, the projected interest, the equity chart's
    axis -- goes through here, so one ``/savings`` or property render builds a
    loan's plan exactly once.

    **The context receives no builder (plan step D-ctx-b).**  ``loan_plan`` lives
    in this seam module ABOVE the context, so the context cannot import it back to
    build the plan itself without inverting the dependency arrow and closing a real
    import cycle (finding N-25).  The earlier design INJECTED the builder into a
    context method; this funnel now fills a PUBLIC pass-through cache instead -- the
    seam owns the derivation, the context owns the storage -- so there is no builder
    argument a caller could get wrong (the Section 8 lesson the injection conceded).

    Args:
        account: The loan account to plan.  Must belong to ``ctx.user_id`` (the
            caller owns the ownership check).
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The pass's memoized :class:`LoanForwardPlan` for this loan.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None -- on EVERY call, not just the
            first.  A build that raises is never cached (the cache assigns only on
            a returned value), and ``ctx.scenario`` is frozen for the pass, so the
            guard cannot be worn down by retrying: there is no state in which a
            no-baseline context starts answering.  That is the property that makes
            the fail-loud trustworthy rather than first-call-only.
    """
    return _memoize_once(
        ctx, ctx.plans, account, lambda: loan_plan(account, ctx),
    )
