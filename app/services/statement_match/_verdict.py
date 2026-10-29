"""What a standing rule comes to for one line, and what the screen says.

Plan step ``bank_import:X-gf-3a``, finding **N-359**.  :mod:`._placement`
answers *where does this merchant's money go* for one line; :mod:`._filing` is
the door that files it without a press; and this answers the question BETWEEN
them, which neither owned: *would this pass file this line under that rule, and
if not, why not*.

**It exists because that verdict had exactly one reader and no surface.**
``_rule_filings`` decided it inside the import request and reported it on
:class:`~._filing.RuleFiling`, whose only rendering is the import's FLASH.  The
review screen then re-rendered the same lines and could say nothing about it --
so a line the owner's own rule was supposed to have handled sat in the queue
looking like one nobody had answered for.  The screen could not restate the
rule either: a second spelling of *would this have filed* is a second place for
it to be wrong, on the one door in the app that moves money with no press.  So
the verdict is derived ONCE, here, and both read it (finding **N-359**).

**The SENTENCE the screen prints is derived here too, whole**, and that is not
convenience.  A first version of this step set two facts in Jinja and picked
between them with ``{% if %}``/``{% elif %}`` -- the shape
:attr:`~._bars.BarredLine.reason` and :attr:`~._leftovers.RecordableInflow
.withheld` exist to refuse, stated in as many words in the very template it was
written into: *a template restating a partition is a second place for it to be
wrong*.  Found by adversarial design review 2026-08-27.

**Two reasons withhold a filing, and they are not the same kind of fact.**

* **The pass did not finish LOOKING** (:func:`~._gaps.search_gap`, developer
  ruling 2026-08-26).  Membership of :attr:`~._reads.ReviewSet.creatable` is a
  set defined by SUBTRACTION -- no proposal claimed the line -- and that is two
  facts wearing one name: *the pass looked and there is nothing*, and *the pass
  threw the only candidate away*.  Under a human tick the person reading the
  screen is the check; ruling **R-GH**'s door has no person, so it withholds.
* **A proposal on this pass explains the rule's destination on its own**
  (ruling **R-FZ(d)**, inverted).  That ruling settled that where two TICKED
  items collide over one envelope the PROPOSAL wins; auto-apply files at import
  and the proposal is ticked afterwards, so the same rule is applied from the
  other side.

**What that second collision actually costs, measured rather than asserted.**
It does NOT double-count, in either order, and a first version of this header
said it did -- inheriting the wording ``X-ge`` gave the receipt.  A purchase
created from a bank line is born carrying the bank's posting day, so it is a
cash movement of its own on that day, and filing `X` into envelope `E` takes
`X` off what `E` still holds back (its budget less its purchases, floored at
zero) in the same act: the account's projection moves only by any part of `X`
beyond `E`'s remaining budget, and no dollar is counted twice.

**`E`'s own figures do NOT hold still, and this paragraph said they did**
until plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2c-2).  It read "filing `X`
raises both terms [``gross`` and
:func:`~app.services.cash_ledger.off_statement_sum`] by `X` and leaves `E`'s
cash leg unchanged to the cent", which holds only for an `E` that ALREADY held
a purchase -- and the `E` a proposal names whole holds none (the next
paragraph).  Its first purchase sends the settle down the purchases branch
(``transaction_service.settles_from_entries``), so its settle figure falls
from its budget to `X` and its cash leg to ``$0.00``.  Measured BY RUNNING the
rule's own filing door (``file_new_swipes``) 2026-10-05: `E` budgeting
`$125.00` and holding no purchase, `X` `$35.00` -- the settle figure
`$125.00` to `$35.00`, the cash leg `$125.00` to `$0.00`; `E` already holding
an un-dated `$85.00` -- both terms up `$35.00`, the cash leg `$85.00` both
times; the projected period-end balance unmoved in both.  So the books record
`X` INSIDE `E`'s budget, not beside it.  Where the bank also shows the line
the proposal explained, `L`, the projection stays above the bank by the part
of `L` that `E`'s remaining budget no longer covers until `L` is explained:
that is the unexplained line's effect, the cost the next paragraph names, and
explaining `L` -- filing it into `E`, which then settles from its purchases --
closes it.

**What it costs instead is the MATCH.**  `E` now holds a purchase, and a row
holding a purchase is worth ``$0.00`` to the offer
(``_settle_price.settle_price``, ruling **R-BAL81**), so
:func:`~._resolve.resolve_rows` refuses any later act naming `E` as a whole as
no longer available -- the proposal beside it becomes impossible to accept,
and the bank line that proposal explained can no longer be matched to `E`: it
stays unexplained until the purchase is undone, or until the line is itself
filed into `E` (the paragraph above).  That is the sentence both registers
carry now.

**The same trace REFUTED this module's first stated backstop.**  It claimed the
create-then-accept order is refused because :func:`~._valuation.repriced`
re-prices every named row and finding **N-336** rejects an item whose row has
moved.  N-336 is never asked here, and the reason this paragraph gave for it
was false: it said the price was invariant by the arithmetic above, which
holds for none of the `E` a proposal names whole (the price falls to ``0``,
the paragraph above).  What holds the order is that N-336 is never REACHED:
:func:`~._resolve.resolve_rows` refuses an Accept naming `E` before
:func:`~._valuation.repriced` runs for it (below).  It said too that the
order WAS refused by the second arm of the accept door's guard against an
envelope named beside its own purchase.  The invariant held; the reason given
for it was one unread call chain from false.  **That guard is deleted** (plan
step ``credit_card:CC-5-4a-5``, leaf 5c-2c-1, finding **CC-386**, ruling
**R-CC144**), because the order is refused a tier earlier, for the reason the
MATCH paragraph above gives.  The create and the later accept are always TWO
requests -- a batch runs every match before any creation
(:func:`~._batch.apply_reviewed`), and the import's filing runs through that
batch -- so the accept's offer set is a fresh candidate scan, and that scan
never offers `E`: ``_settle_price.settle_price`` prices it ``0`` and
``_valuation.transaction_candidate`` returns ``None`` for it.
:func:`~._resolve.resolve_rows` therefore refuses the Accept as naming a row
no longer available, before :func:`~._valuation.repriced` is reached for `E`
and before N-336 is asked.

**So this module adds a SENTENCE and not a refusal**, which is the consent
split ruling **R-GH** made: the screen says what the automatic door saw, and
the person decides.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query -- every fact
it needs arrives from the pass that derived it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ._already_held import ArrivalsAlreadyHeld
from ._creations import PurchaseCreation
from ._gaps import search_gap
from ._leftovers import CreatableLine
from ._offers import MatchProposal

#: What the screen and the receipt both say about a rule whose destination this
#: statement already explains on its own.  One spelling, because the door that
#: withholds and the screen that reports it describe one decision -- and the
#: wording is the CONSEQUENCE rather than *counted twice*, which adversarial
#: financial review 2026-08-27 measured to be false of both orders.
_ALREADY_EXPLAINED = (
    "this statement explains that budget line on its own, and a purchase "
    "filed inside it makes that match impossible to accept, so the line it "
    "explains would stay unexplained"
)

#: What the SCREEN adds to a withholding it is showing a live control beside --
#: the register-specific half, exactly as :func:`~._bars._refusal_for` adds
#: *nothing was changed* to a sentence the screen ends differently.  They
#: differ because the acts do: a line the pass never finished looking at is a
#: reason to go and look, and one whose destination a proposal explains is a
#: reason to accept that proposal first.
#:
#: **Neither names a POSITION**, and the first one did until plan step
#: ``bank_import:X-gf-3b`` (ruling **bank_import:R-HC**): it read *"Check the
#: match form BELOW"*, which went false the moment that form moved to a surface
#: of its own.  A service sentence naming where something sits on a page is
#: coupled to a layout the service cannot see -- EIGHT owner-visible sentences
#: carried that coupling and all eight broke in one step, THREE of them derived
#: here or in :mod:`._bars`.  *A first version of this note said five and two*,
#: which counts the literal string "the match form below" and not the coupling
#: (adversarial design review 2026-08-28).  What the sentence
#: states is the ACT; the row renders the link, which is the one fact a service
#: may not build (:attr:`~._bars.BarredLine.answer_door` sets the precedent).
#:
#: **It said *as new spending* until plan step ``bank_import:X-gj-2b-3``**, and
#: ruling **bank_import:R-II** is what made that false: a merchant credit a
#: rule files as a NEGATIVE purchase reaches this pipeline
#: (:func:`~._verdict.ruled`), and calling a refund *new spending* to the owner
#: is the mis-describing sentence over a working control that ruling **R-GJ**
#: measured `$7,412.94` going through.  The verb is direction-neutral now,
#: because the ACT is: matching a line against rows already held is the same
#: remedy whichever way its money went.
_LOOK_FIRST = (
    "Match it against rows you already hold before recording it."
)
#: What to do about a line whose period already holds money that arrived.
#: Beside :data:`_LOOK_FIRST` and :data:`_ACCEPT_FIRST` because the three are
#: one vocabulary: every withholding this pass reports ends with the ACT that
#: resolves it.
#:
#: **It named a SURFACE until this step's own review, which is the coupling
#: ruling bank_import:R-HC forbids** and which the note on :data:`_LOOK_FIRST`
#: directly above records eight sentences breaking on.  It read *"Check it on
#: the reconcile screen."* -- and :attr:`~._leftovers.CreatableLine.warning` is
#: rendered ON the reconcile screen (:func:`~._cards.creatable_card`), so the
#: owner was told to go where they already were.  It names the act now, and
#: *those rows* rather than *rows you already hold* because the sentence it
#: follows has just named them.
#:
#: **PUBLIC where its two siblings are private**, and the asymmetry is the
#: point: this is the only one of the three whose withholding is reported by a
#: SECOND door as well (:func:`~._filing._inflow_filings`, for the deposit the
#: same guard withholds), and the receipt and the screen must give the owner
#: the same act.  The other two are reached from this module alone.
CHECK_FIRST = "Match it against those rows before recording it as something new."

_ACCEPT_FIRST = "Accept that match first, or file this line somewhere else."


@dataclass(frozen=True)
class RuleVerdict:
    """What the owner's standing rule comes to for one unexplained line.

    **One value with two readers**, which is the whole of finding **N-359**'s
    remedy: :func:`~._filing._rule_filings` performs it or reports it withheld,
    and the review screen prints
    :attr:`~._leftovers.CreatableLine.warning`, composed by :func:`ruled` from
    this same decision.  A line no rule reaches has no verdict at all rather
    than an empty one -- the absence is the answer, and a value meaning *no
    rule* would be a second spelling of ``None`` a reader could forget to
    branch on.

    Attributes:
        creation: The act the rule names for this line
            (:meth:`~._placement.Placement.creation_for`), which is the same
            :class:`~._creations.PurchaseCreation` the screen's own destination
            select submits.  Never ``None``: a placement that names no
            destination reaches no line, so it produces no verdict.
        withheld: Why this pass would NOT perform it, or ``None`` when nothing
            stands in its way.  **This is the RECEIPT's register** -- the
            sentence :class:`~._filing.WithheldLine` carries after an import --
            and the screen's is :attr:`~._leftovers.CreatableLine.warning`,
            which wraps this one rather than restating it.
            **A withheld verdict is not a refusal**: the create door is still
            open for this line and its select still renders, because ruling
            **R-GH** withholds only the act nobody watches.
    """

    creation: PurchaseCreation
    withheld: "str | None" = None


def _proposed_destinations(
    proposals: "tuple[MatchProposal, ...]",
) -> "frozenset[int]":
    """Return the transaction ids this pass proposes as WHOLE-row matches.

    Ruling **R-FZ(d)** from the other side (plan step ``bank_import:X-ge``).
    That ruling settles a collision between two TICKED items -- an envelope a
    proposal names is also a destination a recorded line was aimed at -- in the
    proposal's favour, because a proposal explains money the records already
    hold against a line the bank showed, which can be re-aimed next pass.
    Auto-apply files BEFORE the proposal is ticked, so the same answer has to be
    reached by withholding rather than by ordering.

    **Only rows the proposal names WHOLE** -- a TRANSACTION, or a
    SETTLEMENT, which is a row's payment and so a claim on the row it pays
    (:attr:`~._subjects.CandidateRow.transaction_id`, plan step
    ``credit_card:CC-5-4a-1``).  A proposal naming a PURCHASE
    inside an envelope is not a claim on the envelope: the two acts name
    disjoint subjects, each match's members still sum to its own lines, and
    no act can name both, a row holding a purchase being worth ``$0.00`` to
    the offer (ruling **R-BAL81**).  Measured on the developer's own
    statement: 33 of the 80 lines a rule would file aim at an envelope
    holding a purchase a proposal names, and withholding those would withhold
    the whole Groceries case this step exists for.  **0** aim at an envelope
    a proposal names directly, which is the population this function bounds.

    Moved here from :mod:`._filing` at plan step ``bank_import:X-gf-3a``, with
    the decision that read it (finding **N-359**): the filing door and the
    review screen ask one question about one pass, and asking it inside the
    door left the screen unable to ask it at all.  It takes the PROPOSALS
    rather than the whole review set, because that is all it reads and because
    the set does not exist yet when the verdict is derived.

    Args:
        proposals: What this pass proposes.

    Returns:
        The transaction ids, empty when this pass proposes no whole-row match.
    """
    return frozenset(
        row.transaction_id
        for proposal in proposals
        for row in proposal.rows
        if row.transaction_id is not None
    )


def look_first(gap: str) -> str:
    """Return what the screen says about a line the pass did not finish.

    **The no-rule twin of** :data:`_LOOK_FIRST`, and it lost the same
    positional clause for the same reason (plan step ``bank_import:X-gf-3b``):
    it read *"check the match form BELOW"*, which the hand-build form's move to
    its own surface made false.

    **It said *as new spending* until plan step ``bank_import:X-gj-2b-3``**,
    for the reason :data:`_LOOK_FIRST` states: since ruling
    **bank_import:R-II** a line this composes for may be a refund.

    **PUBLIC since that step, and it is the SAME sentence** the retired
    queue composed for a parked line and an inflow (its model went at plan step
    ``bank_import:X-gi-3``; the CARD prints this now).  Its note there said in as many
    words that an inflow's gap *carries the same framing verb an outflow's
    does*, citing this module -- while spelling the sentence a second time,
    which is how the two came to differ by the clause this step has just had to
    delete from one of them.  One composer now, for all three mechanisms.

    Args:
        gap: The pass's own sentence (:func:`~._gaps.search_gap`).

    Returns:
        The sentence, for a line no rule reaches.
    """
    return (
        f"Before recording this, match it against rows you "
        f"already hold: {gap}."
    )


def ruled(
    creatable: "tuple[CreatableLine, ...]",
    proposals: "tuple[MatchProposal, ...]",
    declined_lines: "dict[int, str]",
    bounds,
    already_held: "dict[int, ArrivalsAlreadyHeld]",
) -> "tuple[CreatableLine, ...]":
    """Return this pass's creatable lines, each carrying what it is owed.

    **A post-pass over the built list**, which is the shape
    :func:`~._leftovers._marked_joining` already has one tier down and for the
    same reason: the facts it needs -- what this pass PROPOSED, and what it
    declined to conclude -- belong to the pass rather than to any one line, and
    the line values are built before either is known.

    Args:
        creatable: This pass's offerable unexplained outflows, each carrying
            the placement the owner's rule comes to for it.
        proposals: What this pass proposes, from which the whole-row
            destinations are taken (:func:`_proposed_destinations`).
        declined_lines: What each tier declined about a line, by line id
            (:attr:`~._propose.ProposedMatches.declined_lines`).
        bounds: What this pass did not look at
            (:class:`~._gaps.ReviewBounds`), read for the two limits that
            belong to the PASS rather than to any one line.
        already_held: ``{line_id: ArrivalsAlreadyHeld}`` for the INFLOW lines
            whose period already holds money ARRIVING that no bank line
            explains
            (:func:`~._reads.arrivals_already_held`), computed by the caller
            because it holds the rows.  **Empty for every outflow**, which is
            what makes the arm below a no-op on the outflow side rather than a
            second rule about it.

    Returns:
        The same lines, with :attr:`~._leftovers.CreatableLine.verdict` set on
        every line a stated rule names a destination for, and
        :attr:`~._leftovers.CreatableLine.warning` set on every line this pass
        has something to say about -- a WIDER set, because a line no rule
        reaches can still be one the pass never finished looking at.

        **A rule that names no container reaches nothing**, and the three
        states that produce one are not this pass withholding anything: the
        owner has said nothing about the merchant, or said *ask me every time*,
        or said *never a purchase* -- which is a BAR that keeps the line out of
        ``creatable`` entirely (ruling **R-GJ**).  None of the three is
        reported as a withholding, because a receipt saying *your rules
        withheld this* about a merchant with no rule is false.
    """
    proposed = _proposed_destinations(proposals)
    lines = []
    for item in creatable:
        placement = item.placement
        creation = (
            None if placement is None
            else placement.creation_for(item.line.line_id)
        )
        gap = search_gap(
            item.line, declined_lines,
            bounds.crowded_days, bounds.unpriceable_count,
        )
        if creation is None:
            # No rule reaches it, so there is no verdict -- but the pass may
            # still have failed to LOOK, which is the line's own fact and not
            # the rule's, and the screen owes it either way.
            lines.append(replace(
                item, warning=None if gap is None else look_first(gap),
            ))
            continue
        # **The gap is asked FIRST**, and the order is the one the receipt
        # reads in: a line the pass never finished looking at has not been
        # shown to collide with anything, so naming the collision first would
        # report a conclusion this pass did not reach.
        held = already_held.get(item.line.line_id)
        if gap is not None:
            withheld, advice = gap, _LOOK_FIRST
        elif creation.transaction_id in proposed:
            withheld, advice = _ALREADY_EXPLAINED, _ACCEPT_FIRST
        elif held is not None:
            # **The DOUBLE-COUNT guard, asked of a refund too** (plan step
            # ``bank_import:X-gj-2b``).  A merchant credit filed as a negative
            # purchase is money ARRIVING, so the period may already hold a row
            # for the same money -- exactly the hazard
            # :func:`~._reads.arrivals_already_held` was added for on the
            # income side.  That step routed these lines into THIS pipeline,
            # which asked ``search_gap`` and the proposed-destination test and
            # not this one; their fail sets are not nested (the gap reaches
            # ACROSS periods by ``DAY_WINDOW``, this tests the row's OWN span),
            # so neither substitutes for the other.
            withheld, advice = held.why_it_could_double_count, CHECK_FIRST
        else:
            withheld, advice = None, None
        lines.append(replace(
            item,
            verdict=RuleVerdict(creation=creation, withheld=withheld),
            warning=(
                None if withheld is None
                else (
                    f"Your rules will not record this one by themselves: "
                    f"{withheld}.  {advice}"
                )
            ),
        ))
    return tuple(lines)
