"""What an unexplained bank line may BECOME, and where it would land.

Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1`` split this out of
:mod:`._reads`.  **The seam is the SUBJECT**, which is the argument
:mod:`._section` already makes one module over: that one answers *what does the
review screen SHOW about this pass*, and this answers *what may one line the
matcher could not explain be turned into*, which is a different question with a
different grain -- one answer per line, and every one of them a door.

**Three answers, and they are TOTAL over what this module is given** since
plan step ``bank_import:X-gm``.  *A first draft of this header claimed that at
plan step ``bank_import:X-gf-1`` and was measured FALSE by that step's own
adversarial review*, because a FOURTH class reached none of them; the paragraph
below the three carries what that class was and what deleting it took:

* a line becomes a PURCHASE against a budget line the owner picks
  (:class:`CreatableLine`, ruling **R-FX**).  **Every outflow, and the INFLOWS
  whose merchant carries a spending answer** -- such a credit is a REFUND, a
  negative purchase back into that container (ruling **R-HT(a)**, plan step
  ``bank_import:X-gj-2b-2``);
* a line whose merchant is barred becomes nothing here, and carries the reason
  (:class:`~._bars.BarredLine`, ruling **R-GJ**).  **In BOTH directions**, and
  this paragraph said *an inflow is never barred: both arms of the bar are
  claims about money leaving* until plan step ``bank_import:X-gj-2b-3``.  That
  is true of *never a purchase* and FALSE of *your bank files this merchant as
  a payment to an account you hold*, which is a claim about the MERCHANT -- so
  a credit from a card-payment merchant is barred exactly as its debits are.
  The retraction was written into :func:`_creatable_lines` one commit later and
  this paragraph, which a reader reaches FIRST, was left asserting the argument
  that had just been measured false.  **A barred line goes to ONE OF TWO
  lists** since plan step ``bank_import:X-gj-4c`` (ruling
  **bank_import:R-JH**): :attr:`Leftovers.parked` where a source files the
  merchant as paying an account the owner holds, and
  :attr:`Leftovers.answered_never` where the only bar is the owner's own
  answer -- because that answer shuts the ADD door and says nothing about what
  the line IS, so the line is still unexplained work;
* every OTHER inflow becomes an uncategorized INCOME row
  (:class:`RecordableInflow`, ruling **bank_import:R-GW**).

**THERE IS NO FOURTH CLASS SINCE PLAN STEP ``bank_import:X-gm``, and deleting
it is that step's own root cause.**  A line the bank dates MADE after it POSTED
used to reach none of the three: :func:`_creatable_lines` dropped it before the
split into ``ReviewBounds.impossible_day_count``, a number naming no line.  What
that cost was not a count -- it was the line.  :func:`~._card_sections
.to_explain_sections` builds the inbox from four lists and this was in none of
them, so such a line reached NO CARD on the Reconcile page at all, on no tab,
and survived only in :attr:`~._reads.ReviewSet.unmatched` for the workbench
plan step ``bank_import:X-gi-2`` deletes.  It is a :class:`CreatableLine`
carrying :attr:`CreatableLine.withheld` now -- the identical remedy an
adversarial review applied on 2026-08-29 to a line no saved pay period covers,
one refusal over (see :func:`_one_creatable`).  Finding **N-325**'s ruling is
untouched: the app still does not choose between the bank's two days, and
:func:`~._scope.impossible_days_refusal` is the one sentence saying so.  0 of
the developer's 378 recorded lines are this shape; the OFX adapter's own
measurement found 2 of 361, so a second source makes it live.

**The third was missing until 2026-08-27 and the gap was invisible because the
three lived apart.**  ``_creatable_lines`` took ``amount < 0`` and nothing took
the other side, so eight of the developer's own deposits -- `$58.87`, five
dividends and three card refunds -- had no act on the review screen at all,
while the two refusals that should have caught it pointed at each other.

**AND THE SIGN WAS THE WRONG DISCRIMINANT ALL ALONG**, which plan step
``bank_import:X-gj-2b-2`` corrects.  Those two sign tests decided what a line
BECAME before the owner's rule was consulted -- exact only while a purchase had
to be positive.  A merchant credit is money arriving that must become a
purchase, so :func:`~._rules.pipeline_for` is the partition now and the sign is
one of its inputs.  The three card refunds in the paragraph above are the
lines that were on the wrong side of it.  A
module whose whole subject is *what may this line become* is where a missing
direction is one function short rather than nowhere -- and the fourth class
above is the proof that it works: it was invisible while these lived apart and
is a bullet here.

**The split is a line cap made useful rather than worked around**, exactly as
:mod:`._creations` and :mod:`._section` record: adding the inflow arm took
:mod:`._reads` past this project's 1,000-line module bound, and the two honest
answers are to cut the record or to cut the module.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.  It READS and never
writes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING

from app.services.status_seam import day_is_in_the_future

from ._bars import CreationBars, MerchantAnswers, BarredLine
from ._creations import NewEnvelope, PurchaseDestination, envelope_answer_key
from ._offers import BankLine
from ._placement import (
    InflowPlacement,
    Placement,
    inflow_placement_for,
    placements_for,
)
from ._rules import LinePipeline, RuleView, pipeline_for
from ._scope import ReviewScope, impossible_days_refusal, no_period_refusal
from ._section import MerchantSection, merchant_section

if TYPE_CHECKING:  # pragma: no cover -- the edge back would be a cycle
    # :mod:`._verdict` imports THIS module, so the annotation is a forward
    # reference and the import is type-checking only.  The direction is
    # right: a line is built before the pass that rules on it exists.  The
    # two safeguard values are annotations for the same reason: they are
    # set by that pass, after the line exists.
    from ._already_held import ArrivalsAlreadyHeld, SpendingAlreadyHeld
    from ._verdict import RuleVerdict


@dataclass(frozen=True)
class CreatableLine:  # pylint: disable=too-many-instance-attributes
    """One bank line the app has no row for, in EITHER direction, and where it could go.

    Pylint: ``too-many-instance-attributes`` (8/7) -- **eight because the
    card reads eight disjoint facts about the line**, three of which the PASS
    sets after the line is built (:attr:`verdict`, :attr:`warning`,
    :attr:`already_held`), and the value is the seam those three cross.  The
    eighth is :attr:`already_held`, the double-count safeguard ruling
    **bank_import:R-BI19** gives an outflow beside the one an inflow had, and
    it rides HERE rather than being re-derived by the card because the card
    and ruling **R-GH**'s automatic door must read ONE derivation of it
    (finding **N-359**'s rule).  Folding it into :attr:`warning` would print
    a sentence where the card names rows; folding :attr:`verdict` into
    :attr:`warning` is what :class:`~._reads.ReviewSet`'s own disable already
    refuses.

    Plan step ``bank_import:X-f6a-3b``, ruling **R-FS**'s third shape.  These
    are the lines the matcher can never explain, because the app records a
    period's groceries as one envelope and the bank records every swipe:
    measured on the developer's own statement **91** unmatched outflows survive
    every proposal, of which 74 are card swipes worth `$3,383.49` -- the case
    R-FS names -- and 17 are ACH debits the app may already hold in another
    shape, which the screen SAYS rather than filtering on the bank's prose.

    Attributes:
        line: The bank's own record of the movement.
        pay_period_id: The period covering the day the bank says it was MADE,
            or ``None`` when no saved period does -- which is what a line
            older than the owner's first payday looks like.  The MADE day and
            not the posting day, because a purchase's budget clock is
            ``purchased_on`` and a swipe made on a period's last day and posted
            on the next period's first belongs to the budget it was made under.
        destinations: The budget lines it could become a purchase against, in
            that period.  EMPTY is a real answer and the screen must say so
            rather than rendering a chooser with nothing in it: on the
            developer's own data the 2026-03-26 period holds three envelopes and
            all three closed at a fixed figure, so 8 lines worth `$662.13` have
            no existing destination and a NEW envelope is the only arm open to
            them.
        placement: What the owner's stated MERCHANT RULE comes to for this
            line (:class:`~._placement.Placement`), or ``None`` when they have not
            said where this merchant goes -- which is a different answer from
            "they said never" and the screen says it differently.  Plan step
            ``bank_import:X-f6a-3d``.
            **It is a SUGGESTION and never a tick**: the destination select
            still opens on *leave this line alone*, and what turns a placement
            into an act is the owner pressing the sweep.  A remembered
            destination that arrived already selected would be a default
            pointing at money, which is what ruling **R-FZ** removed.
        verdict: What ruling **R-GH**'s automatic door would do about this line
            (:class:`~._verdict.RuleVerdict`), or ``None`` where no stated rule
            names a destination for its merchant.  Set by
            :func:`~._verdict.ruled` after the pass exists, exactly as
            :attr:`~._placement.Placement.joins_new` is set by
            :func:`_marked_joining` -- the facts it rests on belong to the PASS
            and are not known when this value is built.
        withheld: Why the create door would REFUSE this line, or ``None``
            when it would not.  **Symmetric with**
            :attr:`RecordableInflow.withheld` **and asked of the same
            predicate**: no saved pay period covers the day the purchase was
            made, so there is no budget for it to belong to.  It is a
            different fact from :attr:`warning`, which explains why the line
            is still here; this says the ADD control may not be rendered at
            all.
        warning: The whole sentence the screen prints beside this line, or
            ``None``.  **Composed in the service and printed unbranched**
            (finding **N-359**, plan step ``bank_import:X-gf-3a``): it is the
            partition *why is this line still here* -- a rule the pass
            withheld, or a search it did not finish -- and a template picking
            between those two with ``{% if %}``/``{% elif %}`` is the second
            place for it to be wrong that :attr:`~._bars.BarredLine.reason`
            and :attr:`RecordableInflow.withheld` both exist to refuse.
            **A WIDER set than** :attr:`verdict`: a line no rule reaches can
            still be one the pass never finished looking at.
        already_held: What the books may already hold this line as, or
            ``None``: for an outflow the rows named for its merchant or
            inside its rule's destination
            (:class:`~._already_held.SpendingAlreadyHeld`, ruling
            **bank_import:R-BI19**), for a refund the arriving rows of its
            period (:class:`~._already_held.ArrivalsAlreadyHeld`, ruling
            **R-II**).  Set by :func:`~._verdict.ruled` from the ONE map the
            pass derives, so the rows the card names and the sentence the
            automatic door withholds on come from one derivation.  **It is
            set whether or not a rule reaches the line**: the screen's
            positive signal -- *your records already hold this* -- is owed
            to a bill no rule names just as much (finding **N-381**).
    """

    line: BankLine
    pay_period_id: "int | None"
    destinations: "tuple[PurchaseDestination, ...]"
    placement: Placement | None = None
    verdict: "RuleVerdict | None" = None
    warning: "str | None" = None
    withheld: "str | None" = None
    already_held: "ArrivalsAlreadyHeld | SpendingAlreadyHeld | None" = None


@dataclass(frozen=True)
class RecordableInflow:
    """One unmatched bank line of money COMING IN, and where it would land.

    Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``.
    :class:`CreatableLine` without its DESTINATIONS, and that absence is still
    the difference: a purchase is filed against a container the owner picks
    between, and an income row is filed against nothing -- so there is no
    offer set here and a field holding an empty one would be a destination
    select one Jinja condition away from rendering beside a deposit.  That is
    the argument :class:`~._bars.BarredLine` already makes beside them.

    **It DOES carry a placement since plan step ``bank_import:X-gj-2a``, and
    this docstring said it could not.**  It read *no placement to suggest*,
    which was true only while a rule's answer was always a CONTAINER: ruling
    **R-HT(a)** gave the answer set a member that names an income CATEGORY,
    which is a classification rather than a container, so it suggests
    something without offering anything to pick between.
    :class:`~._placement.InflowPlacement` is a different type from
    :class:`~._placement.Placement` for exactly that reason, so the field that
    would have rendered a select still cannot.

    Attributes:
        line: The bank's own record of the movement.
        pay_period_id: The period covering the day the bank POSTED it, or
            ``None`` when no SAVED period does.  **The posting day and not the
            transaction day**, which is the residual's rule rather than the
            purchase's: this row has no budget clock of its own because it IS
            the movement (:meth:`~._scope.ReviewScope.period_holding`).
        placement: What the owner's standing rule comes to for this deposit
            (:func:`~._placement.inflow_placement_for`), or ``None`` where no
            rule reaches it -- which is the ordinary state and is the same
            three-facts-not-distinguished absence :attr:`CreatableLine
            .placement` carries.
        withheld: Why this line may NOT be recorded, or ``None`` when it may.
            **ONE server-derived sentence rather than two Jinja conditions**,
            which is :attr:`~._bars.BarredLine.reason`'s argument: a template
            restating a partition is a second place for it to be wrong, and
            here the two arms are *your calendar does not reach that day* and
            *your bank dates that money as moving in the future*.

            **The second arm is the one an adversarial review measured
            2026-08-27**, and it was a control whose submission could never
            succeed -- the shape this package had by then closed five times and
            this value's own docstring claimed to close a sixth.  Pay periods
            project about two years forward, so a future-dated line resolved a
            period, rendered a tick, and was refused by
            ``status_seam.reject_future_settle_day`` only AFTER the door had
            written and settled the row.  The screen and the door now ask ONE
            published predicate (:func:`~app.services.status_seam
            .day_is_in_the_future`) and the door asks it before it writes.
    """

    line: BankLine
    pay_period_id: "int | None"
    withheld: "str | None" = None
    placement: InflowPlacement | None = None


def _barred_line(
    line: BankLine, bars: CreationBars,
) -> "BarredLine | None":
    """Return the barred value for one line, or ``None`` where none bars it.

    **ONE ask of the bar per line, and its answer is what routes the line.**
    A second ask -- once to partition and once to word the sentence -- is the
    redundant producer call :func:`_creatable_lines` already refuses over its
    destinations.

    **BOTH facts, because both bars can hold** (plan step
    ``bank_import:X-gf-3a``): which one the line is barred BY decides what the
    screen says happened, and whether the OTHER also holds decides both which
    list the line goes to (ruling **bank_import:R-JH**) and whether the owner
    has a door -- an answer they gave can be given again, and a source's
    filing is lifted by nothing.  Both come off the bars this pass already
    derived; the value asks nothing for itself.

    **Asked of EVERY line, in both directions**, and a bound that exempted
    inflows is what plan step ``bank_import:X-gj-2b``'s own review measured as
    a hole.  The argument for it -- neither arm of ruling **R-GJ**'s bar can be
    true of money arriving -- holds for *never a purchase* and NOT for *your
    bank files this merchant as a payment to an account you hold*, which is a
    claim about the MERCHANT rather than about one line's direction.  See
    :func:`~._bars.reject_barred_line`, whose docstring carries the whole
    argument and the class it reached; this is the SCREEN's half of the same
    refusal, so the two are graded separately and must agree.

    Args:
        line: The offerable bank line.
        bars: Which of this account's merchants may not become purchases.

    Returns:
        The :class:`~._bars.BarredLine`, or ``None`` when this line's merchant
        bars nothing -- in which case the caller resolves a destination for it.
    """
    barred_by = bars.bar_for(line.merchant_id)
    if barred_by is None:
        return None
    return BarredLine(
        line=line,
        barred_by=barred_by,
        also_pays_an_account=bars.pays_an_account(line.merchant_id),
    )


@dataclass(frozen=True)
class _SplitLines:
    """What :func:`_creatable_lines` made of the lines routed to PURCHASE.

    **A value and not a three-tuple**, which the second and third positions are
    what forces: ``parked`` and ``answered_never`` are both
    ``tuple[BarredLine, ...]``, so a caller that transposed them would type-check,
    run, and put the owner's own answered lines under a chip that renders a
    money MAGNITUDE -- the exact harm ruling **bank_import:R-JH** names.  Two
    adjacent same-typed positions are a pairing hazard this package already
    refuses by name (:func:`~._accept._record`'s two account ids).

    **It held a fourth member until plan step ``bank_import:X-gm``** --
    ``impossible_day_count``, the lines this function declined for being dated
    MADE after they POSTED.  Those lines are :attr:`creatable` now, carrying
    :attr:`CreatableLine.withheld`, and the module header carries why.  The
    count is re-read off the field block rather than decremented, which is the
    discipline :class:`Leftovers` states for its own.

    Attributes:
        creatable: The lines a create control may be rendered for, each with
            its own :attr:`CreatableLine.withheld` where the door would refuse
            it.  **Membership is not the door's answer** and never was: that
            is :func:`_one_creatable`'s own note, and the field is what says so
            per line.
        parked: The barred lines a source files as paying an account the owner
            holds -- a HOLDING state (**R-HQ**), never inbox work.
        answered_never: The barred lines whose ONLY bar is the owner's own
            *never a purchase* answer (**R-JH**).  Inbox work with its ADD
            door shut, because that answer claims nothing about what the line
            is.
    """

    creatable: "tuple[CreatableLine, ...]"
    parked: "tuple[BarredLine, ...]"
    answered_never: "tuple[BarredLine, ...]"


def _creatable_lines(
    calendar, unmatched: "list[BankLine]",
    destinations: "list[PurchaseDestination]",
    answers: MerchantAnswers,
) -> "_SplitLines":
    """Split the unmatched lines it is GIVEN into what may be recorded and what may not.

    *It said OUTFLOWS until plan step ``bank_import:X-gj-2b-3``*, which its own
    ``Returns`` section 45 lines below already contradicted (*"All three counts
    reach the INFLOW direction"*): since ruling **bank_import:R-II** the caller
    hands it every line ``pipeline_for`` routes to PURCHASE, and that includes
    a container-answered credit.

    **A line the bank dates MADE after it POSTED IS one of them, and that is
    plan step ``bank_import:X-gm``'s correction** (finding **N-325**, developer
    ruling 2026-08-19 for the rule; developer 2026-09-05 for the placement).
    ``entry_service.create_entry`` refuses a purchase whose money left before
    it was spent, so its destination chooser is a control whose submission can
    never succeed -- and this function used to answer that by DROPPING the line
    into a count on :class:`ReviewBounds`.  Dropping it took the line off every
    tab of the Reconcile page, not just out of one list, which the module
    header states in full.  It is withheld per line now
    (:func:`_one_creatable`), which is the answer the identical case one
    refusal over already had.  The RULE is unchanged: clamping the purchase day
    to the earlier of the two was rejected, because it decides which day the
    app believes when the bank contradicts itself -- the substitution ruling
    **R-FW** refused one clock over.  The predicate is
    :attr:`~._offers.BankLine.states_impossible_days`, stated once because
    :func:`~._pairing.within_window` and
    :func:`~._scope.reject_impossible_days` ask it too.

    Args:
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, which places each
            line.  Taken rather than loaded, so one request holds ONE calendar
            (:func:`review_set`).
        unmatched: The bank lines inside the calendar no proposal explains.
        destinations: Every offerable budget line
            (:func:`~._destinations.destinations_for`, narrowed by
            :func:`~._destinations.current_destinations`), read ONCE for the
            whole pass and grouped here rather than
            re-queried per line -- a redundant producer call inside one request
            is this project's DRY violation rather than a cost.
        answers: What the owner has said and which merchants that bars
            (:class:`~._bars.MerchantAnswers`, ruling **R-GJ**).  **Asked
            BEFORE a destination is resolved**, because a barred line has no
            destination to resolve one against: the placement machinery answers
            *which budget line would this go in*, and for these the answer is
            that none may, which is a refusal rather than a suggestion.  It is
            the whole value rather than its two halves since plan step
            ``bank_import:X-gm``, because the holding-state test below is a
            method on it and reads both.

    Returns:
        The :class:`_SplitLines` -- one :class:`CreatableLine` per offerable
        LINE a create control may be rendered for, one
        :class:`~._bars.BarredLine` per line ruling **R-GJ** bars split across
        the two lists that ruling **bank_import:R-JH** gives them, each list in
        the order the lines were given, and how many were declined for dating
        their own purchase after their own posting.

        **WHICH of the two a barred line goes to is decided HERE and not by
        the screen**, and that placement is the substance of plan step
        ``bank_import:X-gj-4c``.  It was ``_reconcile._parked_tab``, a
        function that read one parked list and answered *Transfers or
        Skipped*; ruling **R-JH** made the second answer wrong, and the
        remedy is not to delete that arm but to move the decision, because a
        line the owner has merely answered *never a purchase* for is INBOX
        WORK.  Leaving it in ``parked`` and dropping the arm would total that
        list on TRANSFERS, which counts a line's MAGNITUDE onto the hero's
        *waiting for the account they paid* chip -- a rendered money figure
        over a line no source filed as a payment.

        **Both lists reach the INFLOW direction since plan step
        ``bank_import:X-gj-2b-2``**: a refund is a purchase, so a credit whose
        merchant carries a container answer is routed here and barred here
        exactly as its debits are.  A refund whose source ALSO dates it MADE
        after it POSTED is a creatable line with its ADD withheld, which is the
        same treatment its outflow twin gets, on the same sentence.
        The per-period destination tuple is SHARED by every line in that
        period, so a statement with 91 outflows over 11 periods builds 11
        tuples rather than 91.
    """
    # **No sign test**: :func:`_by_pipeline` already chose these, and since
    # plan step ``bank_import:X-gj-2b-2`` they include the INFLOWS whose
    # merchant carries a container answer -- a refund is a purchase.
    #
    # **And no impossible-day test either, since plan step
    # ``bank_import:X-gm``.**  This function used to open by splitting those
    # lines off and returning their COUNT, which is the one arm here that
    # dropped a line rather than routing it.  Deleting it is what makes the
    # routing below TOTAL over what it is given -- every line appends to
    # exactly one list -- and that totality is what lets the inbox be counted
    # rather than summed (:func:`~._reconcile.reconcile_page`).
    if not unmatched:
        return _SplitLines(creatable=(), parked=(), answered_never=())
    # ONE pass over the destinations, and ONE placement per line.  Both were
    # asked twice: the grouping rescanned every destination once per period,
    # and each line placed itself once for its id and again for its lookup --
    # 182 calls for 91 outflows.  A redundant producer call inside one request
    # is this project's DRY violation rather than a cost.
    by_period: "dict[int, list[PurchaseDestination]]" = {}
    for destination in destinations:
        by_period.setdefault(
            destination.period.period_id, [],
        ).append(destination)
    creatable: "list[CreatableLine]" = []
    parked: "list[BarredLine]" = []
    answered_never: "list[BarredLine]" = []
    for line in unmatched:
        barred = _barred_line(line, answers.bars)
        if barred is not None:
            # **THE SOURCE'S FILING DECIDES, AND NOT THE OWNER'S ANSWER**
            # (ruling **bank_import:R-JH**).  A line a source files as paying
            # an account the owner holds is a HOLDING state whatever else they
            # said about it, because the money did move between two accounts
            # they hold; a line carrying ONLY their own *never a purchase*
            # answer is unexplained work whose ADD door that answer shuts, and
            # it goes back to the inbox.  That is the same predicate
            # ``_reconcile._parked_tab`` used to ask of one list, moved to the
            # pass so the answer decides MEMBERSHIP rather than only a tab --
            # see this function's ``Returns`` for why the weaker remedy prints
            # a money figure over the wrong line.
            #
            # **The question is asked of ``answers`` and not of the value in
            # hand** (plan step ``bank_import:X-gm``).  Reading
            # ``barred.also_pays_an_account`` gave the same answer, and that is
            # the problem: it was a SECOND spelling of the test
            # :func:`~._undisposed.inbox_partition` asks to decide what the
            # grid's badge counts, so the two could drift and the badge would
            # promise work this tab is holding.  ``CLAUDE.md`` rule 14: one
            # producer, and every caller reaches it.
            if answers.is_a_holding_state(
                amount=line.amount, merchant_id=line.merchant_id,
            ):
                parked.append(barred)
            else:
                answered_never.append(barred)
            continue
        creatable.append(_one_creatable(
            line, _period_id_for(calendar, line.happened_on), by_period,
            answers.view,
        ))
    return _SplitLines(
        creatable=_marked_joining(creatable),
        parked=tuple(parked),
        answered_never=tuple(answered_never),
    )


def _recordable_inflows(
    calendar, unmatched: "list[BankLine]", view: RuleView,
) -> "tuple[RecordableInflow, ...]":
    """Return the unmatched lines of money COMING IN, each placed by its day.

    Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``.  It takes the
    lines :func:`~._rules.pipeline_for` routes to
    :attr:`~._rules.LinePipeline.INCOME`, which is what closed a whole
    DIRECTION of movement having no act.  **The partition was the SIGN until
    plan step ``bank_import:X-gj-2b-2``** -- this took ``amount > 0`` and
    :func:`_creatable_lines` took ``amount < 0`` -- and the correction is that
    an inflow a container answer claims is a REFUND the other half owns.

    **The two PIPELINES are total and the two LISTS are not**, and the
    difference is the module header's fourth bullet.  :func:`~._rules.
    pipeline_for` names one pipeline for every (direction, answer) the schema
    allows, so no line is unrouted; but :func:`_creatable_lines` then drops the
    lines whose bank dates the purchase after the posting (finding **N-325**),
    and those reach no list at all.  Stating the routing half alone is what
    this step's own adversarial
    review measured FALSE in a first draft of this docstring.

    **No bar is asked** (ruling **R-GJ**).  A bar says this merchant's money
    was SPENT somewhere the budget already holds, which is a claim about an
    outflow; a deposit is not spending, and neither of the two bars has an arm
    that could be true of one.

    **A RULE is asked, since plan step ``bank_import:X-gj-2a``** (ruling
    **R-HT(a)**), and the two are not in tension: a bar refuses an act, and
    this suggests one.  A merchant answered *never a purchase* still reaches
    :func:`~._placement.inflow_placement_for` and still resolves to nothing
    there, so the bar's silence about deposits costs no safety -- the answer
    simply names no income category, which is the same as having said nothing.

    Args:
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, which places each
            line.  Taken rather than loaded, so one request holds ONE calendar.
        unmatched: The bank lines inside the calendar no proposal explains.
        view: What the owner has said and what it can resolve against.  **The
            SAME view the outflow half is given**, read once by
            :func:`leftovers` -- so one request asks ``merchant_rules`` once
            and the two directions cannot resolve against answers read at two
            different instants.

    Returns:
        One :class:`RecordableInflow` per inflow, in the order the lines were
        given.
    """
    return tuple(
        _one_inflow(
            line, _period_id_for(calendar, line.posted_on),
            inflow_placement_for(line.merchant_id, view),
        )
        for line in unmatched
    )


def _one_inflow(
    line: BankLine,
    pay_period_id: "int | None",
    placement: InflowPlacement | None,
) -> RecordableInflow:
    """Return one inflow, and why the door would refuse it if it would.

    **Both arms are the DOOR's own, asked here so the control is not rendered
    for a line it will refuse** -- and the second is asked through the status
    seam's published predicate rather than by a second comparison, because a
    money rule spelled twice is this project's own root cause.

    Args:
        line: The unexplained inflow.
        pay_period_id: The period covering the day it POSTED, or ``None``.
        placement: What a standing rule says this deposit is, or ``None``.
            **Resolved by the caller and threaded rather than looked up here**,
            for the reason every other fact on this value is: one pass holds
            one :class:`~._rules.RuleView`, and a per-line read of it would be
            the redundant producer call this project treats as a defect.

    Returns:
        Its :class:`RecordableInflow`.
    """
    if day_is_in_the_future(line.posted_on):
        withheld = (
            f"Your bank dates this as arriving on {line.posted_on}, which has "
            f"not happened yet.  A row records the day its money moved, so "
            f"there is nothing to record until then."
        )
    elif pay_period_id is None:
        withheld = no_period_refusal(line.posted_on, "this income row")
    else:
        withheld = None
    return RecordableInflow(
        # **The POSTING day**, which is the day this row would settle on and
        # the day its period is resolved from -- one derivation, so the screen
        # cannot offer a control the door then refuses.
        line=line, pay_period_id=pay_period_id, withheld=withheld,
        placement=placement,
    )


def _marked_joining(
    creatable: "list[CreatableLine]",
) -> "tuple[CreatableLine, ...]":
    """Flag each line that would JOIN an envelope an earlier line creates.

    **A press mints ONE envelope per answer per pay period**
    (:class:`~._create.MintedEnvelopes`, finding **N-327**), so the second and
    later lines of one new-envelope answer file into the first one's envelope
    rather than making more beside it.  The screen has to say that BEFORE the
    press, and this is the only reader that sees more than one line at a time
    -- ``placements_for`` resolves one line against its own period and cannot
    know what another line in the same pass will do.

    Walked in the order the lines are given, which is the order the sweep
    applies them, so under the SWEEP -- which ticks a whole class -- the line
    left unflagged is the one that creates.

    **It reads what is OFFERED, not what the owner will tick**, and the
    distinction is real: every select opens on *leave this line alone*, so if
    they hand-pick a flagged line and leave the unflagged one alone, the line
    told "an earlier line here already creates it" is the line that creates.
    The outcome is still one definition per answer, one row of it per period,
    so no money turns on it; what the sentence can be wrong about is WHICH
    line does the creating.  Named by two adversarial reviews 2026-08-20.

    Args:
        creatable: The pass's offerable outflows, in order.

    Returns:
        The same lines, with :attr:`~._placement.Placement.joins_new` set on
        every one after the first for its answer, and on every PLACE line
        after the first for its definition and paycheck.
    """
    creating: "set[tuple]" = set()
    marked = []
    for line in creatable:
        placement = line.placement
        if placement is None:
            marked.append(line)
            continue
        if placement.creates or placement.places:
            # The ANSWER alone for a creation since leaf 7b-3 of
            # balance:X-bi-7b: a second line of it in ANY paycheck joins the
            # definition the first mints (a row of it placed in that
            # paycheck), not only one in the same period.  A PLACE line joins
            # the ROW an earlier line places in the same paycheck, keyed as
            # the registry keys it (``MintedEnvelopes.by_row``).
            key = (
                envelope_answer_key(placement.new_envelope)
                if placement.creates
                else (placement.placed.template_id, line.pay_period_id)
            )
            if key in creating:
                line = replace(
                    line, placement=replace(placement, joins_new=True),
                )
            creating.add(key)
        elif placement.records_in and placement.destination.is_placed:
            # A NEW-ENVELOPE answer's first firing CONVERGED on a placed
            # envelope of its name: the door then names that definition and
            # the registry learns it (``CreatedPurchase.answer_named``), so
            # a later line of the same answer joins it -- said here as the
            # door will do it.  Seeded under the DESTINATION's name and
            # category, which the door's flip compares the stored answer
            # against, so the two cannot describe different envelopes.
            creating.add(
                envelope_answer_key(NewEnvelope(
                    name=placement.destination.name,
                    category_id=placement.destination.category_id,
                )),
            )
        marked.append(line)
    return tuple(marked)


def _one_creatable(
    line: BankLine,
    period_id: "int | None",
    by_period: "dict[int, list[PurchaseDestination]]",
    view: RuleView,
) -> CreatableLine:
    """Return one offerable outflow with its destinations and its placement.

    Args:
        line: The bank line.
        period_id: The pay period covering the day it was MADE, or ``None``.
        by_period: The offerable destinations, grouped by period.
        view: What the owner has said and what it can resolve against.

    Returns:
        Its :class:`CreatableLine`.  A line the create door would refuse gets no
        placement AND a :attr:`~CreatableLine.withheld` refusal
        (:func:`_add_refusal`), because a rule resolves into a destination and
        the sweep presses whatever it resolved.  **Withholding the placement
        alone was not enough** (adversarial review 2026-08-29): the line stayed
        in ``creatable``, so a reader taking membership of that list as the
        door's answer offered a control ``ReviewScope.period_holding`` then
        refused by name -- the chooser-that-cannot-succeed shape, on any swipe
        made just before the first payday or posted past the last saved period.
        **The two are one condition now** rather than two spellings of one
        period test, which is what let plan step ``bank_import:X-gm`` add a
        second refusal to this value without adding a second guard.
    """
    offered = by_period.get(period_id, [])
    withheld = _add_refusal(line, period_id)
    return CreatableLine(
        line=line,
        pay_period_id=period_id,
        destinations=tuple(offered),
        # **No placement where the door would refuse**, which is the pairing
        # the ``Returns`` above argues for: a placement is what the inbox's
        # sweep counts and what a one-click sweep
        # presses, so a refused line carrying one is a bulk control that
        # cannot succeed.
        placement=(
            None if withheld is not None
            else placements_for(
                line.merchant_id, view, offered, period_id=period_id,
            )
        ),
        withheld=withheld,
    )


def _add_refusal(line: BankLine, period_id: "int | None") -> "str | None":
    """Return why the create door would refuse *line*, or ``None``.

    Plan step ``bank_import:X-gm``.  **The screen's half of two refusals the
    door raises, in the order the door raises them**, so an owner who reaches
    the door anyway is told the same thing the card told them:
    :func:`~._scope.reject_impossible_days` fires before a destination is
    resolved (beside :meth:`~._scope.ReviewScope.reject_line_before_books_open`
    and for its reason), and :meth:`~._scope.ReviewScope.period_holding` fires
    when one is.  Both can hold of one line -- a swipe dated before the first
    payday whose bank also contradicts itself -- and stating the LATER one
    would name a refusal the owner never reaches.

    **Neither sentence is composed here.**  Both are read from the module that
    the door raises them from, which is :func:`~._scope.no_period_refusal`'s
    own rule; this function is the ORDER and nothing else.

    Args:
        line: The bank line routed to the purchase pipeline.
        period_id: The pay period covering the day it was MADE, or ``None``.
            **The MADE day**, which is the day this purchase would be placed
            by and the day this was resolved from -- one derivation, so the
            screen cannot offer a control the door refuses.

    Returns:
        The refusal sentence, or ``None`` when the door would take the line.
    """
    if line.states_impossible_days:
        return impossible_days_refusal(line)
    if period_id is None:
        return no_period_refusal(line.happened_on, "this purchase")
    return None


def _period_id_for(calendar, day: date) -> "int | None":
    """Return the SAVED pay period covering *day*, or ``None``.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        day: The day the bank says the purchase was made.

    Returns:
        Its period id, or ``None`` when no saved period covers it -- a line
        older than the owner's first payday, or past the generated horizon.
    """
    period = calendar.period_containing(day)
    return None if period is None else period.period_id


@dataclass(frozen=True)
class Leftovers:
    """What this pass could not explain, placed against the owner's rule.

    Six facts one derivation produces that travel together, which is the
    argument :class:`~._subjects.Candidates` and
    :class:`~._propose.ProposedMatches` already make in this package: a caller
    holding the offerable lines without the count of the ones declined would
    render a list that reads as complete.  *It said FOUR over five fields until
    plan step ``bank_import:X-gj-4c``, and adversarial review caught the
    off-by-one being carried forward rather than corrected* -- a count in a
    docstring is a claim about the value below it, so it moves with the field
    and is re-counted rather than incremented.  It was SIX until plan step
    ``bank_import:X-gj-4b`` added :attr:`account_payments`, and that number was
    re-counted off the field block rather than incremented, which is the same
    discipline one step later.  It was SEVEN until plan step
    ``bank_import:X-gm`` deleted ``impossible_day_count``, whose lines are
    :attr:`creatable` now; re-counted off the block a third time.

    It leaves this module for :func:`~._reads.review_set`, which is what
    assembles it into a :class:`~._reads.ReviewSet`.

    Attributes:
        creatable: The offerable unexplained lines a create control may be
            rendered for, each with its placement.  **Outflows and the inflows
            a container answer claims as REFUNDS** (ruling
            **bank_import:R-II**), which is :func:`~._rules.pipeline_for`'s
            partition rather than the sign's.
        parked: The offerable unexplained lines a source files as paying an
            account the owner holds, each with the reason
            (:class:`~._bars.BarredLine`).  Both directions: that arm is a
            claim about the MERCHANT, so a credit from such a merchant is
            barred exactly as its debits are.  **It held EVERY barred line
            until plan step ``bank_import:X-gj-4c``**; ruling
            **bank_import:R-JH** moved the ones barred only by the owner's own
            answer to :attr:`answered_never`, because those are unexplained
            work and this list is a holding state.
        answered_never: The offerable unexplained lines whose only bar is the
            owner's own *never a purchase* answer (**R-JH**, plan step
            ``bank_import:X-gj-4c``).  **The same value as** :attr:`parked`
            **in a different list, and the list IS the difference**: what the
            two hold is one fact -- the create door is shut for this line --
            and what they are is two, because a source's OBSERVATION that the
            money went to another account the owner holds is a disposition and
            the owner's DECISION that a merchant is not spending is not one.
            A line here keeps MATCH and reads *Nothing suggested*.
        recordable_inflows: The unexplained INFLOWS, each with the period
            that would hold it (ruling **bank_import:R-GW**).
        merchants: The rule control's rows and option list.
        account_payments: The merchant row ids this account's sources file as a
            payment to an account the owner holds
            (:attr:`~._bars.CreationBars.account_payments`).
            **Published rather than kept private, because a consumer outside
            these lists needs it and cannot reach it any other way** (plan step
            ``bank_import:X-gj-4b``); the argument is stated once on
            :attr:`~._reads.ReviewSet.account_payments`, which is what this is
            assembled into.
            **Carried rather than re-read**, which is :func:`leftovers`' own
            rule for the answers themselves: the bars have already read it at
            this pass's instant, and a second read in
            :func:`~._reads.review_set` would be the redundant producer call
            inside one request that this package treats as a DRY violation.
    """

    # Pylint: ``duplicate-code`` -- Incidental field-block similarity with
    # :class:`~._reads.ReviewSet`, which publishes ALL SIX of this value's
    # names because it is what this value is assembled INTO -- five of them
    # carrying the same value.  *It said SIX of SEVEN until plan step
    # ``bank_import:X-gm`` deleted ``impossible_day_count``, the one name that
    # did not travel*; re-read off both field blocks rather than decremented.
    # **The FENCE below is narrower than either
    # number and deliberately so**: it covers five annotation lines and no
    # logic, of which four carry the same value, because
    # :attr:`account_payments` is separated from the run in BOTH classes
    # (by the blank line here and by ``bounds`` there) and so
    # extends no similarity run.  *An earlier correction re-counted the
    # published names and left the fence's own figures describing them,
    # which named no set that exists.*
    # ``ReviewSet.creatable`` is
    # :func:`~._verdict.ruled` over this one and is a different value under
    # the same name, and the two are separate types deliberately -- this is
    # one producer's output and that is the whole pass.  It read four lines
    # and stayed under the gate until plan step ``bank_import:X-gj-4c`` added
    # ``answered_never`` to both.  A shared base would couple a private
    # transport value to the package's published shape, which is the coupling
    # ``app/models/transfer.py`` refuses in the same words (coding-standards
    # rule 13).  One-sided disable: the ``ReviewSet`` block stays un-disabled.
    # pylint: disable=duplicate-code
    creatable: "tuple[CreatableLine, ...]"
    parked: "tuple[BarredLine, ...]"
    answered_never: "tuple[BarredLine, ...]"
    recordable_inflows: "tuple[RecordableInflow, ...]"
    merchants: MerchantSection
    # pylint: enable=duplicate-code

    account_payments: "frozenset[int]"


def _by_pipeline(
    unmatched: "list[BankLine]", view: RuleView,
) -> "dict[LinePipeline, list[BankLine]]":
    """Split the unexplained lines by which ACT each is a candidate for.

    Plan step ``bank_import:X-gj-2b-2``.  The whole of the routing, in one
    place, so the two halves below can each be about their own act rather than
    about which lines are theirs.

    **Every pipeline gets a key even when empty**, so a caller indexes rather
    than ``.get``-with-a-default: a missing key would be a silently empty half,
    which is exactly how a whole DIRECTION of movement had no act at all until
    ruling **bank_import:R-GW** (see the module header).

    Args:
        unmatched: The bank lines inside the calendar no proposal explains.
        view: What the owner has said, read ONCE by :func:`leftovers` and
            shared, so the two halves cannot resolve against answers read at
            two different instants.

    Returns:
        ``{LinePipeline: [BankLine]}`` covering every member, each list in the
        order the lines were given.
    """
    buckets: "dict[LinePipeline, list[BankLine]]" = {
        pipeline: [] for pipeline in LinePipeline
    }
    for line in unmatched:
        rule = view.rules.get(line.merchant_id)
        buckets[pipeline_for(
            amount=line.amount,
            answer=rule.answer if rule is not None else None,
        )].append(line)
    return buckets


def leftovers(
    scope: ReviewScope,
    unmatched: "list[BankLine]",
    destinations: "list[PurchaseDestination]",
    answers: MerchantAnswers,
) -> "Leftovers":
    """Return the unexplained outflows placed against the owner's rule.

    **What the owner has SAID is read at the PASS's instant, not carried on the
    scope**, for the same reason the claims are (plan step
    ``bank_import:X-f6a-3d``): a pass can restate a rule, and this screen is
    re-rendered after the door that does, so a reader taking it off the scope
    would show the answers the pass had just replaced.  Ruling **R-GJ**'s bars
    are read at the same instant and for the same reason -- one of them IS an
    answer, and the other is the absence of one -- and from the answers that
    view has already read, so one request asks ``merchant_rules`` once.

    Args:
        scope: The pass's derived offer set.
        unmatched: The bank lines inside the calendar no proposal explains.
        destinations: The budget lines still open to a new purchase.
        answers: What the owner has said and which merchants that bars
            (:class:`~._bars.MerchantAnswers`).  **A parameter since plan step
            ``bank_import:X-gm``**, which built it here until then: the
            membership walk that decides what the inbox IS
            (:func:`~._undisposed.inbox_partition`) asks the same answers, and
            two reads inside one request is the DRY violation the paragraph
            above is about -- one instant further apart than the one it names.
            :func:`~._reads.review_set` reads it once and threads it to both.

    Returns:
        The :class:`Leftovers`.
    """
    view, bars = answers.view, answers.bars
    # **ONE partition, on what each line is a candidate to BECOME** (plan step
    # ``bank_import:X-gj-2b-2``).  The two halves each took their own SIGN test
    # until this step -- ``amount < 0`` here and ``amount > 0`` there -- which
    # decided what a line became before the owner's rule was consulted.  A
    # merchant credit is money ARRIVING that must become a PURCHASE, so the
    # sign cannot decide it; :func:`~._rules.pipeline_for` is the discriminant
    # and the direction is one of its inputs.
    by_pipeline = _by_pipeline(unmatched, view)
    split = _creatable_lines(
        scope.calendar, by_pipeline[LinePipeline.PURCHASE], destinations,
        answers,
    )
    return Leftovers(
        creatable=split.creatable,
        parked=split.parked,
        answered_never=split.answered_never,
        # **The bars' own set, at the instant the bars were read.**  The
        # screen shuts SKIP on exactly the lines :func:`~._skipping.skip_line`
        # refuses (ruling **bank_import:R-JI**), and both read
        # :func:`~._vocabulary.account_payment_merchants` -- this one once per
        # pass, the door once per act, which is the read-for-yourself-across-a-
        # write-boundary rule the module header states for the rules.
        account_payments=bars.account_payments,
        recordable_inflows=_recordable_inflows(
            scope.calendar, by_pipeline[LinePipeline.INCOME], view,
        ),
        # **Both UNANSWERED halves**, because a merchant is barred for want of
        # an answer and this control is the only place one is given: counting
        # only the creatable half would refuse the act and hide the door that
        # permits it.
        #
        # **``answered_never`` is NOT a third term, and it is not needed as
        # one.**  A first draft of plan step ``bank_import:X-gj-4c`` added it
        # on the reasoning that those lines had been inside ``parked`` and so
        # were already counted -- which adversarial review measured FALSE: they
        # were passed in before and produced no row THEN either.  Membership of
        # that list requires :attr:`~._bars.CreationBar.NEVER_A_PURCHASE`,
        # which :meth:`~._bars.CreationBars.build` reads out of ``view.rules``,
        # and :func:`~._section.merchant_section` emits only merchants
        # ``not in view.rules`` -- so the term could not have produced a row on
        # any input a database can hold.  That is ruling **R-GX** holding
        # structurally: an ANSWERED merchant is on the merchants list, and
        # *never a purchase* is an answer.  Stated rather than kept, because a term no
        # input can reach is a fence, and one nothing grades is a fence that
        # reads as coverage.
        merchants=merchant_section(
            [item.line for item in split.creatable]
            + [item.line for item in split.parked],
            view, bars,
        ),
    )
