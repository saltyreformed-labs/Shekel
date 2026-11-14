"""The RETIRED ``(seed, plan)`` forward-fold contract, expressed over the ONE timeline.

Plan step **recurrence:R16-c-1** (ruling **R-R90**) deleted the seam's second
replay of a loan -- ``balance_at._plan_fold._split_plan`` and the four readers
over it (``fold_forward``, ``plan_payoff_date``, ``plan_required_extra``,
``plan_interest_in_year``) -- which folded a loan's forward plan from a SEED
the settled fold had resolved.  The seam replays a loan's whole timeline from
its origination now (``balance_at._loan_stream``), and the plan's payments are
projections behind the recorded facts.

Three suites grade the forward arithmetic with plans STATED BY HAND and a seed
STATED BY HAND -- ``test_loan_plan_forward_oracle``,
``test_loan_payoff_date_oracle``'s package and ``test_loan_plan_assembly`` -- against
figures anyone can check.  Those figures are the grade, and they do not move:
this module keeps the retired signatures and answers them over the one
timeline, so every hand-computed assertion still fires against the SAME
producer (:func:`~app.services.loan_ledger.replay_loan_stream` over
:func:`~app.services.balance_at._loan_stream.merged_stream`, the production
path) with its inputs re-expressed rather than re-derived.

**How a ``(seed, plan)`` pair becomes a timeline, and why it is byte-identical.**
The seed becomes the loan's one assertion, a
:class:`~app.services.loan_ledger.LoanResetEvent` dated at the origination the
caller names (or a day before every plan date when it names none), and the
plan's payments are appended as projections through the seam's own mapping
(:func:`~app.services.balance_at._loan_stream.projection_events`).  With no
recorded payment, the projection boundary is the day after that assertion, so
every projection keeps its own date and the hypothetical extra accrues at
every charge after the assertion -- which is precisely what ``_split_plan`` did
from its seed.  ``fold_forward``'s origination gate (``0.00`` before
``owed_from``) is the empty prefix before the assertion.

**Two kinds of plan, one timeline** (plan step recurrence:R16-c-2, ruling
**R-R100**).  A production :class:`~app.services.balance_at._plan_records.LoanForwardPlan`
carries the loan's contract terms and no charge list; the seam composes it
exactly as a read pass does (:func:`~app.services.balance_at._loan_stream.merged_stream`,
charged from origination through its last event).  A :class:`HandPlan`
states its CHARGES by hand -- the ruling's promise that the hand-computed
oracles keep their hand-stated charges and figures -- and its stream is
built here with those charges and nothing else, so the replay these suites
grade is never fed by the calendar derivation that production uses.

Test infrastructure, never imported by ``app/``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from app.services.balance_at._fold import fold_from_walk
from app.services.balance_at._loan_stream import (
    merged_stream,
    projection_events,
)
from app.services.balance_at._plan_fold import (
    required_extra,
    timeline_payoff_date,
)
from app.services.balance_at._plan_records import (
    LoanForwardPlan,
    PlannedPayment,
)
from app.services.loan_ledger import (
    AccrualCharge,
    LoanEventStream,
    LoanLedgerWalk,
    LoanResetEvent,
    PaymentOutcome,
    replay_loan_stream,
)
from app.services.loan_loaders import LoanAnchorFact

_ZERO = Decimal("0.00")
# A day before every date a hand-built plan names, for callers that state no
# origination: the seed's assertion lands here and the boundary the day after.
_BEFORE_EVERYTHING = date(1900, 1, 1)


@dataclass(frozen=True)
class HandPlan:
    """A forward plan STATED BY HAND: its payments, and the charges they face.

    The hand-computed oracles' own plan value (plan step recurrence:R16-c-2,
    ruling **R-R100**): production's
    :class:`~app.services.balance_at._plan_records.LoanForwardPlan` carries the
    loan's contract terms and lets the leaf derive the charges, so a suite that
    grades the REPLAY against arithmetic anyone can check states the charges
    itself instead -- one per accrual period, each carrying the rate and escrow
    the test names.

    Attributes:
        payments: The plan's :class:`~app.services.balance_at._plan_records.PlannedPayment`
            records, in any order.
        charges: The :class:`~app.services.loan_ledger.AccrualCharge` list the
            payments face, ascending by ``on_date`` -- one standing over every
            payment, so the replay never asks for a calendar.
    """

    payments: Sequence[PlannedPayment]
    charges: Sequence[AccrualCharge]


def _stream(
    seed: Decimal, owed_from: date, plan: LoanForwardPlan | HandPlan,
) -> LoanEventStream:
    """Return the stream a ``(seed, plan)`` pair states: one assertion, then the plan.

    A :class:`HandPlan` is charged by its own hand-stated list; a production
    :class:`~app.services.balance_at._plan_records.LoanForwardPlan` is composed
    by the seam's :func:`~app.services.balance_at._loan_stream.merged_stream`.
    """
    # The assertion's source is the fact the walk's correction dates itself by
    # (``dated_deltas`` reads ``anchor.anchor_date``): an opening anchor for a
    # loan that exists only in this test.
    opening = LoanAnchorFact(
        account_id=0, anchor_date=owed_from, anchor_balance=seed,
        is_opening=True, created_at=datetime(1900, 1, 1, tzinfo=timezone.utc),
        event_id=0,
    )
    facts = LoanEventStream(
        charges=[],
        payments=[],
        resets=[
            LoanResetEvent(
                on_date=owed_from, balance=seed, source=opening,
                is_opening=True, is_tracking_start=False,
            ),
        ],
    )
    if isinstance(plan, HandPlan):
        return LoanEventStream(
            charges=plan.charges,
            payments=facts.payments,
            resets=facts.resets,
            projections=projection_events(plan.payments),
        )
    return merged_stream(facts, plan)


def timeline_from_plan(
    seed: Decimal,
    owed_from: date,
    plan: LoanForwardPlan | HandPlan,
    extra_monthly: Decimal = _ZERO,
) -> LoanLedgerWalk:
    """Return the timeline a ``(seed, plan)`` pair states: one assertion, then the plan.

    Args:
        seed: The balance the projection starts from -- the loan's one
            assertion here.
        owed_from: The day that assertion is dated; the loan owes ``0.00``
            before it.
        plan: A :class:`HandPlan`, or a production
            :class:`~app.services.balance_at._plan_records.LoanForwardPlan`.
        extra_monthly: The hypothetical per-period extra the replay takes.

    Returns:
        The :class:`~app.services.loan_ledger.LoanLedgerWalk` -- its
        ``projected_splits`` are the plan's outcomes.
    """
    return replay_loan_stream(
        _stream(seed, owed_from, plan), extra_per_period=extra_monthly,
    )


def fold_forward(
    seed: Decimal,
    owed_from: date,
    plan: LoanForwardPlan | HandPlan,
    dates: list[date],
    extra_monthly: Decimal = _ZERO,
) -> dict[date, Decimal]:
    """The retired ``fold_forward``: the balance owed on each of *dates*."""
    return fold_from_walk(
        timeline_from_plan(seed, owed_from, plan, extra_monthly), dates,
    )


def split_plan(
    seed: Decimal, plan: LoanForwardPlan | HandPlan, extra_monthly: Decimal = _ZERO,
) -> list[PaymentOutcome]:
    """The retired ``_split_plan``: one outcome per plan payment, in walk order."""
    return timeline_from_plan(
        seed, _BEFORE_EVERYTHING, plan, extra_monthly,
    ).projected_splits


def plan_payoff_date(
    seed: Decimal, plan: LoanForwardPlan | HandPlan, extra_monthly: Decimal = _ZERO,
) -> date | None:
    """The retired ``plan_payoff_date``: the DUE date the plan clears *seed* on."""
    return timeline_payoff_date(
        timeline_from_plan(seed, _BEFORE_EVERYTHING, plan, extra_monthly),
        _BEFORE_EVERYTHING, _BEFORE_EVERYTHING,
    )


def plan_required_extra(
    seed: Decimal, plan: LoanForwardPlan | HandPlan, target_date: date,
) -> Decimal | None:
    """The retired ``plan_required_extra``: the per-period extra that clears by *target_date*."""
    walk = timeline_from_plan(seed, _BEFORE_EVERYTHING, plan)
    return required_extra(
        walk, _BEFORE_EVERYTHING, _BEFORE_EVERYTHING, target_date,
        lambda extra: replay_loan_stream(walk.stream, extra_per_period=extra),
    )


def plan_interest_in_year(
    seed: Decimal, plan: LoanForwardPlan | HandPlan, year: int,
) -> Decimal:
    """The retired ``plan_interest_in_year``: the plan's interest paid in *year*.

    Attributed to each payment's visible (effective) year, as the producer's
    projected half is.  The retired reader also took an ``exclude_slots`` merge
    key; the timeline has no second half to merge against, so the parameter is
    gone with the merge.
    """
    return sum(
        (
            outcome.interest
            for outcome in split_plan(seed, plan)
            if outcome.visible_on.year == year
        ),
        _ZERO,
    )
