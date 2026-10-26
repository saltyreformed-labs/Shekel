"""A loan recurrence's VALIDITY WINDOW: the resolver, and the one bound still written.

A loan's recurring payment (:class:`~app.models.recurrence_rule.RecurrenceRule`)
is bounded at BOTH ends by the loan's own facts, so the recurrence engine
generates a payment only while the loan actually exists and owes -- and since
plan step R7d-g the two ends are held two different ways:

* ``starts_on`` = the loan's FIRST CONTRACTUAL INSTALLMENT (plan step C9a),
  WRITTEN, for the loan's STANDING payment (ruling **R-R29**: it is the
  cadence anchor a MONTH-unit rule has no day to fire on without, and a
  contract fact that resolves for an owner with no baseline scenario).  A
  pre-origination payment is not merely early -- the fold ERASES it (it splits
  against a zero balance and the origination anchor resets over it: $0.00
  principal, the whole payment to Refund) while the cash side still debits
  it, so a mortgage closing one month out projected $3,220.92 of payments for
  a loan that did not exist.  Written into the payload at the two transfer
  form doors where a definition is created or edited into the standing
  payment (``_loan_destination``), and re-derived by
  :func:`sync_loan_payment_start` at every door where WHICH definition is
  standing, or WHAT the contract says, can move (plan step R7d-g-2, ruling
  **R-R85**): loan setup, the params edit, archive, unarchive, hard-delete
  and the transfer update door.
* the CLOSING bound = the loan's CLOSING DATE, DERIVED on every read by
  :func:`loan_payment_window` and stored NOWHERE: the projected payoff while
  the loan still owes, and the day it LAST became closed once it does not.

**Until R7d-g the closing bound was WRITTEN too**, into
``budget.recurrence_rules.end_date`` -- the authored bound's own column -- by
ten chokepoints sharing one entry (``sync_recurring_payment_bounds``: a params
/ rate edit, a balance true-up, and every transfer settle / revert / edit /
delete / restore of a loan payment), each recomputing the payoff and writing
it if it had moved.  **A persisted derivation is a cache, and that one was
measurably behind on live data** (plan ledger row **D35**): rule 48 stored
``end_date`` ``2029-01-22`` where the Van Loan's derived payoff was
``2029-02-22``, so extending the calendar generated rows only to the stored
date and the ``$531.94`` installment due ``2029-02-22`` was never created.
Plan step R7d put every reader on the resolver one leaf at a time -- R7d-b
built it, R7d-d the Recurring surface, R7d-e the monthly totals, R7d-f the
recurrence form, R7d-c-2 generation -- and R7d-g then deleted the nine
writers, NULLed the cache (ruling **R-R80**: the standing payment of every
loan and every ARCHIVED transfer into one) and landed
``ck_recurrence_rules_valid_window``, true by construction because every
stored closing bound is its owner's word and the ONE writer of the pair
(``recurrence._authoring._author``) refuses an inverted one off the values
it stores.  Nothing in ``app/`` writes ``end_date`` from a derivation any
more; the ONE state a door could still reach that the writer refuses -- the
standing payment's re-derived start moved past a stop its owner authored
(ruling **R-R82**) -- is translated in :func:`_sync_loan_cadence` into a
sentence naming the transfer and refused whole at whichever door reached it
(``_standing_payment.sync_loan_payment_start_or_refuse``).

Flask-isolated: plain ``account_id`` in, no ``request`` / ``session`` reads;
the writer flushes into the caller's transaction and never commits (the
caller owns the transaction boundary); the resolver is a pure read.

The RESOLVER
------------

**Since R7d-d a reader does not ask this function directly.**  The composed
door (:func:`app.services.recurring_definition.resolved_definition`) calls it
once and puts the answer on the resolved recurrence's
:class:`~app.services.recurrence.Closing`, ANDed with the bound the rule
authors, so the occurrence walk and the display's phrase read one value rather
than each performing the conjunction.  A caller reaching for this function
instead is asking for half the answer.

**Only the CLOSING bound stopped being stored** (ruling **R-R29**).
``starts_on`` is a contract fact rather than a fold -- it resolves for an
owner with no baseline scenario, where a payoff cannot -- and it is the
cadence ANCHOR that a MONTH-unit rule has no day to fire on without, so
:func:`_sync_loan_cadence` stays a writer, for the standing payment alone
(ruling **R-R81**: a second transfer's start is its owner's).  That
asymmetry is why the two halves are separate functions here and always were.

**The resolver takes the DEFINITION and not the loan** (ruling **R-R35**), so
it never has to answer which recurring transfer into a loan is "the" payment --
a question nothing in the schema answers and every consumer of
:func:`~app.services.recurring_transfer_query.active_recurring_transfer_template`
currently tie-breaks on ``id``.  Every recurring transfer into a loan is paying
it down, and each stops when the loan does.  See that function's docstring for
the measurement and for the half of plan ledger row **D47** that remains.
"""

import logging
from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING

from app.enums import RecurrenceUnitEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.services import balance_at, installment_calendar, loan_loaders
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    EMPTY,
    INDEFINITE,
    ClosesOn,
    DerivedStop,
    EmptyAuthoredWindowError,
    RecurrenceSpec,
    ResolvedRecurrence,
    offerable_nominal_days,
    reauthor_rule,
    recurrence_spec,
    resolve,
)
from app.services.recurring_transfer_query import (
    active_recurring_transfer_template,
    destination_account,
)
from app.services.balance_at import BalanceContext
from app.utils.log_events import (
    BUSINESS,
    EVT_LOAN_RECURRENCE_START_DATE_UPDATED,
    log_event,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only; these are ORM row types
    from app.models.loan_params import LoanParams
    from app.models.recurrence_rule import RecurrenceRule
    from app.models.transfer_template import TransferTemplate
    # Type-only, both: ``RecurrenceOwner`` names only ``loan_payment_window``'s
    # parameter here, and ``recurring_definition`` imports THIS module at
    # runtime, so the edge back is a forward reference and nothing more.
    from app.services.recurrence import RecurrenceOwner
    from app.services.recurring_definition import UnsavedDefinition

logger = logging.getLogger(__name__)

LOAN_START_WOULD_PASS_AN_AUTHORED_STOP: str = (
    "The recurring transfer '{name}' is set to end on {stop}, before the "
    "loan's first installment ({starts_on}) it would pay from. Set it up "
    "again without an end date, or archive it."
)
"""Refusal when a door would move the standing payment's start past its stop.

The ONE state ``ck_recurrence_rules_valid_window`` refuses that a door can
reach (plan step R7d-g): the standing payment's ``starts_on`` is the loan's
contract fact and follows ``payment_day``; its closing bound is its owner's
word (ruling **R-R82**) and follows nothing.  The write door refuses the pair
(:class:`~app.services.recurrence.EmptyAuthoredWindowError`);
:func:`_sync_loan_cadence` words it with the transfer's name, and the door
that reached it refuses its edit whole.  Worded for EVERY such door since plan
step R7d-g-2 (ruling **R-R85**) -- the params edit, where the installment
moves; archive and hard-delete, where a second transfer carrying a stop is
promoted; unarchive, where a restored one is -- so it names the transfer and
the two dates and nothing about which edit was made.  The remedy names
archiving or re-creating, not editing: the standing payment's "Ends" row is
locked and a stated bound is refused, so the only door to that stop is the
archive.
"""


def loan_payment_window(
    template: "RecurrenceOwner | UnsavedDefinition",
    resolved: ResolvedRecurrence,
    ctx: BalanceContext,
) -> DerivedStop | None:
    """Return when *template* stops paying its destination loan, or ``None``.

    **The RESOLVER plan step R7d-b built, and since plan step R7d-g the ONLY
    producer of a loan payment's closing bound** -- ten call sites wrote the
    same value into ``budget.recurrence_rules.end_date`` until then, and the
    column holds nothing but an owner's word now.
    A loan's payoff is a fold over its forward plan, so a bound persisted at
    mutation time was a CACHE of a derivation -- and it was measurably behind
    on live data: rule 48 stored ``2029-01-22`` where the Van Loan's derived
    payoff was ``2029-02-22``, so extending the calendar generated rows only
    to the stored date and the ``$531.94`` installment due ``2029-02-22`` was
    never created (plan ledger row **D35**).  Asking rather than storing is
    what makes that unconstructible.

    **It takes the DEFINITION, not the loan, and that is a developer ruling of
    2026-08-25** (**R-R35**) rather than the ``(account, ctx)`` this step was
    originally specified with.  Asking "when does this loan's payment stop"
    forces a prior question -- WHICH of the recurring transfers into the loan
    is its payment -- that nothing in the schema answers, so
    :func:`~app.services.recurring_transfer_query.active_recurring_transfer_template`
    tie-breaks it on ``id``.  Measured on a production clone with the sweep's
    id FORCED below the Mortgage payment's (that tie-break is ascending, so a
    row created later loses it and the reachable ordering is the sweep
    authored FIRST): a ``$200.00``/mo transfer into the Mortgage drives the
    derived payoff from ``2048-12-01`` to ``None``, against a ``$616.99``
    monthly escrow the ``$200.00`` does not even cover.  Asking "when does THIS
    transfer into a loan stop" needs no such answer: **every** recurring
    transfer into a loan is paying it down --
    the settled fold
    (:func:`~app.services.loan_loaders.settled_income_legs`) and the PLANNED
    tier (``balance_at._plan.loan_plan``) both already sum every
    one of them with no template filter -- and each of them stops when the loan
    does, because past payoff the ONE allocation
    (:func:`~app.utils.money.apply_payment_cash`) routes the whole cash
    to ``excess`` (a Refund) rather than to principal.

    **What that buys is precise, and it is less than "the tie-break is gone".**
    This function's SUBJECT is no longer chosen by one -- every definition into
    a loan is asked about and every one gets the same answer -- but the
    ANSWER's value still travelled through it: ``loan_figures`` ->
    ``resolved_loan`` -> ``standing_payment`` ->
    ``active_recurring_transfer_template``, whose ``.order_by(id).first()``
    priced the ESTIMATED tier.  In the `$200.00` case above the resolver
    returned ``INDEFINITE`` for BOTH definitions: they agreed, and both were
    wrong.  Plan ledger row **D47** carried that half and **R16-b-2** closed
    it by making the estimate SUM, as its two sibling tiers already did; the
    chain's ``standing_payment`` link went at plan step R7d-g-3 with the last
    figure read off the picked definition (**D49**).

    **EMPTY is decided against the definition's RESOLVED first occurrence**,
    read off the *resolved* value the caller already holds
    (:attr:`~app.services.recurrence.ResolvedRecurrence.starts_on`).  "The
    loan closed before this definition ever fires" is exactly
    ``closes < resolved.starts_on``, and it is the same comparison as "no
    occurrence of this rule falls inside the window" because every walk
    ascends from that date.  Two things follow from TAKING that value rather
    than deriving it here.  The rule is resolved ONCE per read pass: the
    composed door resolves it to build the value and hands the value down,
    where the first build of plan step R7d-d resolved the same rule a second
    time inside this function (``CLAUDE.md`` rule 14's ONE WALK).  And the
    stored column cannot reach this decision.  ``budget.recurrence_rules
    .starts_on`` equals the first occurrence for every unit but ``PERIOD``,
    which ``_resolution`` re-normalises on every read to the START of the
    paycheck covering the stored date, so after a pay-schedule edit the
    walk's first occurrence can land BEFORE the column (plan ledger row
    **D39**) and a comparison against the column would answer EMPTY --
    "finished" -- about a definition with a live occurrence.  A bare ``date``
    parameter would have let a caller pass that column back in; the resolved
    value the door hands down is not built from it -- it is the rule's authored
    cadence resolved against the owner's calendar.

    **Its first reader arrived at plan step R7d-d**, which is the composed
    door :func:`app.services.recurring_definition.resolved_definition` -- the
    Recurring surface's cadence sentence and next date read this answer
    through it, so that row's stop line and next date come from this answer
    and not the column.  Until plan step R7d-g the door read the stored copy
    as the CACHE it was for the definition
    :func:`~app.services.balance_at.is_standing_loan_payment` names (ruling
    **R-R56**, an arm deleted with the column's last writer); the monthly
    equivalent took the door at plan step R7d-e, when
    ``obligations_aggregator`` began judging the composed closing; a closing
    bound an owner states for a loan's own payment is refused at create
    (rulings **R-R60** and **R-R61**) and at the two edits that make a
    definition a loan's recurring transfer (plan ledger row **REC-521**,
    rulings **R-R76** and **R-R77**).  R7d-f moved the form's locked "Ends"
    control and its inverted-window refusal onto the same door; R7d-c-2 moved
    generation (``recurrence_engine.resolve_generation_plan`` reads the door's
    placements, so a loan payment is generated only while the loan owes and
    the maintain pass retires what it no longer justifies); R7d-g then
    stopped the column being written at all.

    A pure READ: it opens no transaction, writes nothing and reads no clock of
    its own (*ctx* carries the pass's ``as_of``).

    Args:
        template: The recurring definition being asked about -- a
            ``TransferTemplate``, or a ``TransactionTemplate``, which can never
            pay into an account and always answers ``None``; or, since plan
            step R7d-f-2, the door's
            :class:`~app.services.recurring_definition.UnsavedDefinition`,
            the destination a form names for a definition nothing stores yet.
            ``getattr`` on
            the FK COLUMN is what keeps this kind-agnostic across the three, the
            same way :func:`~app.services.balance_at.is_standing_loan_payment`
            is.  **Must belong to
            ``ctx.user_id``** -- the caller owns the ownership check, as every
            seam entry this reaches states.  A pairing of one owner's
            definition with another's read pass is refused by the pass itself
            when the loan is memoized (``ForeignAccountError`` from
            ``balance_at._memoize._memoize_once``, plan step X-i4); the composed
            door reaches the rule's own refusal first, before any account is
            loaded.
        resolved: What *template*'s rule MEANS against the owner's schedule
            -- the value :func:`~app.services.recurrence.resolved_recurrence`
            built for it -- read here for its first occurrence alone.  Taken
            rather than re-derived, for the two reasons above.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.  Its ``as_of`` is
            the day the loan's state is read at -- since plan step
            ``recurrence:R7d-h`` it selects WHICH crossing answers, and is no
            longer itself a retired loan's bound -- and its scenario scopes the
            fold; the pass
            is TAKEN and never built here (the 2026-08-16 ruling -- a producer
            below the route does not build one).

    Returns:
        The :class:`~app.services.recurrence.DerivedStop`, or ``None`` when
        nothing about a loan bounds this definition: it pays into no account,
        or its destination is not a configured loan.  (A definition with no
        rule never arrives here -- there is no resolved value to ask about, and
        the composed door answers *does not repeat* for it one call up.)
        ``None`` is "this question does not apply here" and never a fourth
        shape -- the three shapes are the answers a loan gives, and a
        definition with no loan behind it has no derived stop at all.

        **The shapes live in the recurrence package since plan step R7d-d**,
        and this function is what stayed: deciding WHICH shape applies means
        folding a loan's balance, which needs the ORM and the balance seam,
        while the shapes themselves are plain values over dates.  Splitting
        them that way is what lets the occurrence walk and the display's
        phrase-writer both read this answer -- neither could import this
        module without a cycle.  A loan is one supplier of a derived stop and
        nothing in the type says it is the only one.

    Raises:
        BaselineMissingError: When the destination IS a configured loan and
            *ctx* has no baseline scenario, from the seam's own
            ``require_scenario``.  Ruling **R-R30** (2026-08-19): a producer
            that needs a scenario REFUSES, to the single application-level
            handler, rather than early-returning -- the early return it
            replaces left the last-written bound standing, and once nothing is
            stored there is nothing to stand.  The not-a-loan answer above is
            reached FIRST, so a savings or investment transfer still resolves
            for an owner with no baseline.
    """
    # The column, then a lookup -- see
    # :func:`~app.services.recurring_transfer_query.destination_account` for
    # the joined-relationship staleness that rules out ``template.to_account``.
    # ``None`` is the same early return :func:`sync_loan_payment_start`
    # makes for an account it cannot load.
    account = destination_account(template)
    if account is None:
        return None
    # ``loan_figures`` is asked for the not-a-loan answer as well as for the
    # payoff, rather than a ``load_loan_params`` pre-check beside it: that
    # would be a second producer of "is this a configured loan", and the seam
    # runs its own test BEFORE the scenario guard precisely so a caller may
    # use it this way (see its docstring).
    figures = balance_at.loan_figures(account, ctx)
    if figures is None:
        return None
    closes = figures.closing_date
    if closes is None:
        return INDEFINITE
    # The RESOLVED first occurrence, never ``rule.starts_on``: see the EMPTY
    # paragraph above for the ``PERIOD``-unit drift (plan ledger row **D39**)
    # that makes the column the wrong side of this comparison.
    if closes < resolved.starts_on:
        return EMPTY
    return ClosesOn(on=closes)


@dataclass(frozen=True)
class LoanCadenceStart:
    """When a loan's recurring payment first fires, as its rule states it.

    The two fields ``budget.recurrence_rules`` uses to say "the last day of
    every month starting in April", held together because they are one fact and
    the schema refuses them apart
    (``ck_recurrence_rules_nominal_day``); see :func:`loan_cadence_start`.

    Attributes:
        starts_on: The loan's first contractual installment, and therefore the
            recurrence's first occurrence.
        nominal_day: The contractual ``payment_day`` when *starts_on*'s own
            month was too short to hold it, else ``None``.
    """

    starts_on: date
    nominal_day: int | None


def loan_cadence_start(
    unit: RecurrenceUnitEnum, params: "LoanParams",
) -> LoanCadenceStart:
    """Return the first occurrence a loan's contract implies for *unit*.

    **The ONE producer of "when does this loan's payment start", and plan step
    R7c-b is what made it one** (developer ruling, 2026-08-15).  Two callers
    need the answer and each used to compute it: :func:`_sync_loan_cadence`
    below, and ``routes/loan/payment_transfer.py``, which built the rule with a
    typed cadence and then let the sync overwrite it a few lines later.  That
    second shape was worse than duplication -- on the generic transfer form the
    date the USER typed was written and then silently discarded (see
    :data:`~app.routes._recurrence_form_refusals.LOAN_PAYMENT_BOUND_IS_DERIVED`,
    which states the same rule for the EDIT path).  Deriving it BEFORE the rule
    is built is the ruling: nothing is authored that something else immediately
    replaces.

    **``starts_on`` is the loan's first contractual installment.**  A payment
    cannot precede the loan, and generation before origination is not merely
    early -- the fold ERASES it (the payment splits against a zero balance and
    the origination anchor then resets over it: $0.00 principal, the whole
    payment to Refund) while the cash side still debits it, so a mortgage
    closing one month out projected $3,220.92 of payments for a loan that did
    not exist (plan step C9a).

    **``nominal_day`` is what keeps a month-end loan on the month's end.**  A
    servicer's ``payment_day`` of 31 means the last day of every month; if the
    first installment lands in a 30-day month the DATE alone says "the 30th",
    and every later payment would be modelled a day early forever.  The pair is
    exactly the one ``ck_recurrence_rules_nominal_day`` admits -- present only
    where the installment month clamped the contractual day -- and
    :class:`~app.services.recurrence.RecurrenceSpec` refuses any other pairing
    at construction.

    A cadence with no day-of-month coordinate (an every-paycheck loan payment,
    plan ledger row **D27**'s unenforced precondition) states no nominal day:
    it bills by PAYCHECK, so ``resolve`` normalises the installment date onto
    the paycheck that hosts it and there is no contractual day to keep.

    Reads no clock: these are contract facts, not functions of when the sync
    happened to run (the A3 rule).

    Args:
        unit: The recurrence's cadence unit, which is the WHOLE of what decides
            whether the cadence has a day-of-month coordinate.  It took the
            placement beside it until plan step R7c-b; see the inline comment
            for the wrong-money defect that pairing caused, and
            :func:`~app.services.recurrence.has_day_of_month_coordinate` for why
            the unit alone is the right question.
        params: The loan's :class:`~app.models.loan_params.LoanParams`.

    Returns:
        The :class:`LoanCadenceStart`.
    """
    starts_on = installment_calendar.first_installment_date(
        params.origination_date, params.payment_day,
    )
    # MEMBERSHIP in the reader's own set, never a second list of conditions.
    # ``offerable_nominal_days`` IS the rule -- the cadence must have a
    # day-of-month coordinate, the date must be its month's last day, and the
    # value must exceed it and stay inside 29-31 -- and it is what
    # ``RecurrenceSpec``, ``ResolvedRecurrence`` and
    # ``ck_recurrence_rules_nominal_day`` all admit against.
    #
    # **Restating the conditions here was a wrong-money defect** (plan step
    # R7c-b).  This asked ``fires_on_day_of_month(unit, placement)``, which
    # answers whether a generated ROW is dated from that day,
    # while ``ResolvedRecurrence.day_of_month`` keys on the UNIT alone -- and
    # they differ for exactly ``Monthly First``.  Measured against the real
    # functions, a servicer's day-31 payment first billing in a 30-day month::
    #
    #   MONTH / CONTAINING_DATE
    #     orig 2026-03-10 pday 31 -> starts_on 2026-04-30 nominal 31, day 31
    #   MONTH / PERIOD_STARTING_ON_OR_AFTER
    #     orig 2026-03-10 pday 31 -> starts_on 2026-04-30 nominal None, day 30
    #
    # Reachable through ``POST /transfers``, where ``Monthly First`` is
    # authorable and ``settle_first_occurrence`` derives the pair for it: under
    # that placement the funding PAYCHECK moves a period early in every month
    # whose schedule puts a payday on the 30th, for the life of the loan.
    return LoanCadenceStart(
        starts_on=starts_on,
        nominal_day=(
            params.payment_day
            if params.payment_day in offerable_nominal_days(unit, starts_on)
            else None
        ),
    )


def loan_cadence_spec(
    spec: RecurrenceSpec, params: "LoanParams",
) -> RecurrenceSpec:
    """Return *spec* with the loan's contractual first occurrence stated.

    :func:`loan_cadence_start` applied to a spec the caller already holds --
    the shape the two IN-PLACE writers need, where the rest of the recurrence
    is the rule's own and only its start is the loan's.  A create route builds
    its spec from the value instead; see that function for the derivation and
    for why one producer answers both.

    Args:
        spec: The rule's current authored state.
        params: The loan's :class:`~app.models.loan_params.LoanParams`.

    Returns:
        *spec* with ``starts_on`` and ``nominal_day`` replaced.
    """
    start = loan_cadence_start(spec.unit, params)
    return replace(
        spec, starts_on=start.starts_on, nominal_day=start.nominal_day,
    )


def _sync_loan_cadence(rule: "RecurrenceRule", params: "LoanParams") -> bool:
    """Bring a loan recurrence's opening bound onto the loan's contract.

    Nothing else re-points a loan payment after a ``payment_day`` edit (the
    loan's ``origination_date`` is immutable after setup, so that edit is the
    one contract fact that can move the first installment).  Before plan step
    R7c-b this had to keep TWO columns in step -- the opening bound and the
    scheduling day -- and the two disagreeing was worse than either being
    stale: measured on a mortgage whose ``payment_day`` went 1 -> 20, the
    bound advanced to the 20th while the rule still matched the 1st, so the
    surviving period contained no matching day and the recurrence generated
    **nothing at all**.  One date carries both facts now (ruling **R-R16**),
    so that failure mode is unconstructible rather than guarded against.

    **Scenario-INDEPENDENT, and since plan step R7d-g the only bound this
    module writes** (ruling **R-R29**): the value is a function of the loan's
    params alone, so it resolves for a user with NO baseline scenario, where
    the payoff the closing bound derives from cannot.  The closing bound is
    never stored -- :func:`loan_payment_window` answers it on every read.

    **Idempotent in TWO stages, and the second one is what a day-less rule
    needs.**  The cheap comparison is on the authored spec, which settles it
    for every rule that bills on a day of the month -- both of the developer's
    live loan payments.  A rule that bills by PAYCHECK stores the payday
    ``resolve`` normalised the installment onto, which never equals the raw
    installment date, so the spec comparison alone would re-author and log on
    every call forever.  Comparing the RESOLVED values is what answers that,
    and it is reached only when the cheap check fails.

    **A start moved PAST an authored stop is REFUSED, not written** (plan
    step R7d-g).  The stored closing bound is the owner's word and nothing
    else since that step -- ``ck_recurrence_rules_valid_window`` holds
    ``end_date >= starts_on`` on every row -- and a definition can carry one
    while it is the loan's standing payment (ruling **R-R82**: a stop
    authored on a second transfer, or on a transfer into an account that was
    not yet a loan, is honoured when the definition becomes the standing
    payment with no submission to refuse it at).  A ``payment_day`` edit that
    moves the loan's first installment past that stop would write the one
    pair the constraint refuses.  The write door grades that pair off the
    value it STORES and refuses it (:class:`~app.services.recurrence
    .EmptyAuthoredWindowError`, the one comparison in the application); this
    function TRANSLATES that refusal into a sentence naming the transfer, so
    the door that reached it can refuse its edit whole and say which
    definition stands in its way.  The remedy is the owner's (archive that
    transfer, or set it up again), which is what the message names.  A COUNT
    bound cannot invert against a date and passes.

    Args:
        rule: The recurring payment's :class:`RecurrenceRule`.
        params: The loan's :class:`~app.models.loan_params.LoanParams`.

    Returns:
        ``True`` when the rule was re-authored -- its first occurrence MOVED
        -- and ``False`` when it was already on the contract.  The caller
        reads it to bring the rule's generated rows along (plan step
        R7d-g-2, ruling **R-R85**): a rule whose start moved names different
        occurrences, and rows left on the old ones would be a second home for
        the same derived fact.

    Raises:
        ValidationError: The re-derived first installment falls after the
            stop the rule's owner authored.  Nothing has been written when it
            raises; the caller owns the rollback.
    """
    current = recurrence_spec(rule)
    wanted = loan_cadence_spec(current, params)
    if wanted == current:
        return False
    calendar = calendar_for(rule.user_id)
    if resolve(wanted, calendar) == resolve(current, calendar):
        return False
    old_start = current.starts_on
    # RE-AUTHORED, not assigned: a rule is written whole through one door, so
    # the cycle phase and the closed set's storage encoding are re-derived from
    # the date this call is moving rather than left holding what a previous
    # contract implied.  The door grades the pair it would store and refuses
    # an inverted one before touching the row; the dates it names are the
    # STORED ones (a paycheck-space start normalised onto its payday).
    try:
        reauthor_rule(rule, wanted, calendar)
    except EmptyAuthoredWindowError as refused:
        raise ValidationError(
            LOAN_START_WOULD_PASS_AN_AUTHORED_STOP.format(
                # A loan payment is a transfer's rule: the transaction arm
                # of the owning arc pays into no account.
                name=rule.transfer_template.name,
                starts_on=refused.starts_on.strftime("%b %-d, %Y"),
                stop=refused.end_date.strftime("%b %-d, %Y"),
            ),
        ) from refused
    log_event(
        logger, logging.INFO,
        EVT_LOAN_RECURRENCE_START_DATE_UPDATED, BUSINESS,
        "Updated recurrence rule start date to first contractual installment",
        account_id=params.account_id,
        rule_id=rule.id,
        old_start_date=str(old_start),
        new_start_date=str(wanted.starts_on),
        old_nominal_day=current.nominal_day,
        new_nominal_day=wanted.nominal_day,
    )
    return True


def sync_loan_payment_start(account_id: int) -> "TransferTemplate | None":
    """Re-derive a loan's standing payment's opening bound from the loan's contract.

    **The tenth of the ten chokepoints, and the only one left** (plan step
    R7d-g).  Nine call sites wrote the loan's derived payoff into
    ``budget.recurrence_rules.end_date`` through this module's predecessor
    (``sync_recurring_payment_bounds``) and every one of them went with the
    column's last writer; the closing bound is DERIVED on every read by
    :func:`loan_payment_window` and stored nowhere.  What survives is the
    OPENING bound ruling **R-R29** keeps stored -- the cadence anchor a
    MONTH-unit rule has no day to fire on without -- for the ONE definition
    whose start is the loan's contract fact rather than its owner's word: the
    loan's STANDING payment (ruling **R-R81**; a second transfer's start is
    the owner's and is never re-synced).  It is called where that contract
    fact moves -- the loan-params edit, which can change ``payment_day`` --
    and, since plan step R7d-g-2 (ruling **R-R85**), at every door where
    WHICH definition is standing can move: loan setup, archive and
    hard-delete (the next-oldest is promoted), unarchive (the restored one is
    standing again, or newly), and the transfer update door (the standing
    payment's cadence unit).  Each calls it AFTER its write is flushed, so the
    lookup below answers for the state the door leaves; the route-side
    helper ``_standing_payment.sync_loan_payment_start_or_refuse`` is the one
    spelling of that call and of the refusal's handling.

    A no-op -- returning before any write -- when the account is not a
    configured loan, has no recurring payment, or its standing payment's
    start already matches the contract.  Flushes into the caller's
    transaction (does NOT commit).  Reads no clock and builds no read pass:
    the value is a function of the loan's params alone.

    Args:
        account_id: The loan account whose standing payment's ``starts_on`` to
            re-derive.

    Returns:
        The standing payment's :class:`~app.models.transfer_template
        .TransferTemplate` when its start MOVED, else ``None`` -- for a
        no-op of any kind, and for a start already on the contract.  The
        route-side helper brings the moved definition's rows along (ruling
        **R-R85**: a rule and its generated rows are one derived fact, and
        the maintain pass is what keeps them one).

    Raises:
        ValidationError: The re-derived first installment falls after a stop
            the standing payment's owner authored (see
            :func:`_sync_loan_cadence`).  Nothing is written; the caller
            refuses its edit whole and shows the message.
    """
    account = db.session.get(Account, account_id)
    if account is None:
        return None
    # The template lookup comes FIRST: with no recurring payment there is
    # nothing to re-derive.  Cheapest disqualifying check first.
    template = active_recurring_transfer_template(account_id, account.user_id)
    if template is None or not template.recurs:
        return None
    params = loan_loaders.load_loan_params(account_id)
    if params is None:
        # Not a configured loan (no LoanParams) -- the bound is not defined.
        return None
    if _sync_loan_cadence(template.recurrence_rule, params):
        return template
    return None
