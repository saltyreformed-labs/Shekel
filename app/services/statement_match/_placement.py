"""What a stated rule comes to for ONE unexplained bank line.

Plan step ``bank_import:X-f6a-3d``.  :mod:`._rules` holds the ANSWER -- where
the owner has said a merchant's spending goes -- and this holds what that
answer means for one line of one statement, which is a different question with
a different shape: an answer is period-independent by construction, and a
placement is the one budget line in the one pay period that line falls in.

**Every way an answer can fail to reach a line is REPORTED, never substituted
for.**  A template that generated no offerable row in this period, two rows
where one was expected, a category since archived: each is a
:attr:`PlacementKind.UNRESOLVED` carrying the sentence that says which.
Substituting -- falling back to a new envelope when the named one is missing --
is how a suggestion becomes a guess, and it would file money somewhere the
owner never named.

**Nothing here writes anything**, which is the property the whole step rests
on: a placement is rendered BESIDE a line's destination select, never into it,
and the select still opens on *leave this line alone* (ruling **R-FZ**).

Services-boundary discipline: plain data in, frozen dataclasses out, no Flask
import, no clock read, no query -- every fact it needs arrives on a
:class:`~._rules.RuleView`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from ._creations import (
    NEW_ENVELOPE,
    IncomeCreation,
    NewEnvelope,
    PurchaseCreation,
    PurchaseDestination,
    place_token,
)
from ._rules import StandingRule, RuleAnswer, RuleView


class PlacementKind(enum.Enum):
    """What a rule comes to for ONE creatable line.

    Four, because a rule that cannot be applied HERE is a different thing to
    say than one that names a row:

    * ``RECORD_IN`` -- an existing budget line in this line's own pay period;
    * ``PLACE`` -- a row of a RULE-LESS definition the line's recording would
      PLACE in its own pay period, where that paycheck holds none (plan step
      ``balance:X-bi-7b`` leaf 7b-3, ruling **R-BAL24**): a TEMPLATE answer
      naming a definition that generates nothing -- a grid one-off's, or the
      one a NEW-ENVELOPE answer minted the first time it fired -- reaches
      every later paycheck this way, one placed row per paycheck through
      ``one_off.place_row_of``.  Before this kind existed such an answer
      resolved UNRESOLVED everywhere but the one paycheck holding a row
      (``from_scratch_architecture.md`` 10.6-D's regression);
    * ``CREATE_NEW`` -- an envelope this line's recording would create, and
      only where this period holds NO placed envelope of that name and
      category (finding **N-327**, developer ruling 2026-08-20): a rule-less
      definition minted through ``one_off.place_one_off`` with its first
      row, after which the merchant's answer NAMES that definition and every
      later line takes ``PLACE`` (finding **N-328**).  Within one press the
      lines of one answer converge on the definition the first of them
      minted (:class:`~._container.MintedEnvelopes`: a ``Lowe's -> a new
      "Home Improvement"`` answer applied to the developer's own statement
      once made **4 envelopes across 3 pay periods** in one press).  **It is
      still a SUGGESTION and never a substitution**: the placement is
      PRINTED beside the line's own destination select, which still opens on
      *leave this line alone*, so the owner may pick another;
    * ``UNRESOLVED`` -- a rule exists and does not reach this line, with the
      reason it does not.  **Reported rather than substituted for**: the
      obvious substitution, falling back to a new envelope when the named
      template has no row in this period, would file money somewhere the owner
      never named.

    **There was a fourth, ``NOT_A_PURCHASE``, and ruling R-GJ deleted it** (plan
    step ``bank_import:X-ga``).  *Never a purchase* stopped being something a
    rule PLACES and became something that BARS: it is a
    :class:`~._bars.CreationBar` now, resolved before a destination is looked
    for, so the line never reaches this module at all.  Keeping a kind here for
    it would be a second statement of a refusal -- and what that second
    statement bought while it existed was a sentence saying "nothing here
    records it" printed directly beneath a select that did.
    """

    RECORD_IN = "record_in"
    PLACE = "place"
    CREATE_NEW = "create_new"
    UNRESOLVED = "unresolved"

    # The screen asks :class:`Placement`'s own questions rather than comparing
    # these strings: a Jinja condition restating a partition is a second place
    # for it to be wrong, and a typo in one of three literals falls through to
    # the arm that prints an unresolved reason -- ``None`` for the other two.
    # Named by adversarial financial review 2026-08-19.


@dataclass(frozen=True)
class PlacedDefinition:
    """The rule-less definition a PLACE placement would place a row of.

    Attributes:
        template_id: The definition (``transaction_templates.id``).
        name: What to call it on the card.
    """

    template_id: int
    name: str


@dataclass(frozen=True)
class Placement:
    """What the owner's rule comes to for one creatable line.

    Attributes:
        merchant: The line's merchant, which is the rule's key.
        kind: Which of the four (:class:`PlacementKind`).
        destination: The budget line to file into, for
            :attr:`PlacementKind.RECORD_IN`.  A
            :class:`~._creations.PurchaseDestination` drawn from the pass's own
            offer set rather than an id looked up here, so the screen shows the
            label and period span it would show anyway and the write door
            cannot be handed a row the screen may not offer.
        new_envelope: The envelope to create, for
            :attr:`PlacementKind.CREATE_NEW`.
        placed: The rule-less definition to place a row of, for
            :attr:`PlacementKind.PLACE`, as ``(template_id, name)`` -- the
            rule's own id, which the view has already proved is offerable on
            this account, and what to call it on the card.  One value rather
            than two fields because the two are one fact, and because the
            class sits at pylint's attribute bound.
        joins_new: Whether an EARLIER line in this same pass already creates
            that envelope -- or, for a ``PLACE``, already places that
            definition's row in this paycheck -- so this one would join it
            rather than make a second (finding **N-327**).  It stays a
            ``CREATE_NEW`` / ``PLACE`` because the select value is unchanged
            -- one press mints one definition per answer and places one row
            of it per period (:class:`~._container.MintedEnvelopes`) -- and
            what this flag buys is that the SCREEN says so before the press
            rather than after it.  Set by :func:`~._leftovers._marked_joining`,
            which is the only reader that sees more than one line at a time.
        unresolved_reason: One sentence saying why the rule does not reach
            this line, for :attr:`PlacementKind.UNRESOLVED`.
    """

    merchant: str
    kind: PlacementKind
    destination: "PurchaseDestination | None" = None
    new_envelope: "NewEnvelope | None" = None
    placed: "PlacedDefinition | None" = None
    joins_new: bool = False
    unresolved_reason: "str | None" = None

    @property
    def records_in(self) -> bool:
        """Return whether this places the line in a budget line that exists."""
        return self.kind is PlacementKind.RECORD_IN

    @property
    def creates(self) -> bool:
        """Return whether this places the line in an envelope it would make."""
        return self.kind is PlacementKind.CREATE_NEW

    @property
    def places(self) -> bool:
        """Return whether this places the line in a row of a definition it would place."""
        return self.kind is PlacementKind.PLACE

    @property
    def names_a_home(self) -> bool:
        """Return whether this names somewhere to file the line at all.

        The three kinds that do, asked in ONE place: the card, the sweep and
        the sentence each used to spell ``records_in or creates`` for
        themselves, and a fourth kind is exactly the edit that spelling would
        miss.
        """
        return self.records_in or self.creates or self.places

    @property
    def definition_id(self) -> "int | None":
        """Return the DEFINITION whose rows this placement files into, or ``None``.

        Plan step ``bank_import:X-f6b-2``, ruling **bank_import:R-BI19**: the
        container a merchant's swipes go into has an identity ACROSS pay
        periods (plan step ``bank_import:X-f6c``), and the double-count
        safeguard asks that identity -- *does any row of this definition
        already hold an unexplained purchase near this line* -- rather than the
        one period's row, because a hand-logged purchase's day is a guess that
        crosses period boundaries.  Read off the placement's own arms so the
        safeguard and the act name one definition: the row a ``RECORD_IN``
        placement files into is one of its definition's, and a ``PLACE``
        placement names the definition outright.  ``None`` for
        ``CREATE_NEW`` and for ``UNRESOLVED``, because neither NAMES a
        definition: a ``CREATE_NEW`` answer's convergence on a same-named
        placed envelope in the line's own period yields ``RECORD_IN``
        (finding **N-327**), and a same-named definition in ANOTHER period
        is one the rule never named, so shape (2) has nothing to look in.
        """
        if self.kind is PlacementKind.RECORD_IN:
            return self.destination.template_id
        if self.kind is PlacementKind.PLACE:
            return self.placed.template_id
        return None

    @property
    def home_name(self) -> "str | None":
        """Return what the card calls the container this files into, or ``None``.

        The row's own name for ``RECORD_IN``, the definition's for ``PLACE``,
        the envelope's for ``CREATE_NEW``.  **The ONE spelling of the home's
        name**: :func:`~._sentence.for_placement` prints it on each arm and
        :class:`~._already_held.SpendingAlreadyHeld` names it in the
        safeguard's clause, so the card and the sentence the automatic door
        withholds on cannot name two different homes (a first draft read the
        three arms here AND there, and agreement is not the test).  ``None``
        for ``UNRESOLVED``.
        """
        if self.kind is PlacementKind.RECORD_IN:
            return self.destination.name
        if self.kind is PlacementKind.PLACE:
            return self.placed.name
        if self.kind is PlacementKind.CREATE_NEW:
            return self.new_envelope.name
        return None

    @property
    def sweep_class(self) -> "str | None":
        """Return which RISK class ticking this line would fall in.

        ``"into_open"`` files into a budget line that has not closed, which a
        reservation absorbs; ``"into_closed"`` raises what a CLOSED row records
        as its cost; ``"creates"`` mints a budget line the account did not
        have.  ``None`` for an UNRESOLVED placement, which names no row to act
        on.

        **A PARTITION, and it exists because ruling R-FZ(c) already demanded
        one.** That ruling swept proposals per CLASS rather than by one "tick
        all", on the ground that *the riskiest class may not ride the same
        click as the safest*, and :attr:`~._offers.MatchProposal.review_class`
        is what makes it server-derived.  A first draft of this sweep ticked
        every placement together -- and measured on a production clone, **29 of
        account 1's 220 offerable destinations have already closed**, including
        8 of 60 Gas rows and 9 of 61 Groceries rows, so a template answer
        naming either would have raised a closed row's recorded cost on the
        same press that filled an open one.  Found by two adversarial reviews
        2026-08-19.

        Derived HERE rather than as a Jinja condition for the reason
        ``review_class`` is: a template restating the partition is a second
        place for it to be wrong.
        """
        if self.kind in (PlacementKind.CREATE_NEW, PlacementKind.PLACE):
            # A placed row is a budget line the account did not have in that
            # paycheck, exactly as a minted envelope is.
            return "creates"
        if self.kind is not PlacementKind.RECORD_IN:
            return None
        return "into_closed" if self.destination.is_settled else "into_open"

    @property
    def select_value(self) -> "str | None":
        """Return what this line's destination select would be set to.

        **The ONE place the sweep's target value is decided**, so the control
        that ticks a line and the door that writes it cannot disagree about
        which option a rule means.  ``None`` for an UNRESOLVED placement --
        there is nothing to tick for a rule that does not reach here, and
        rendering a value for it would be a tick the owner never stated.
        """
        if self.kind is PlacementKind.RECORD_IN:
            return str(self.destination.transaction_id)
        if self.kind is PlacementKind.CREATE_NEW:
            return NEW_ENVELOPE
        if self.kind is PlacementKind.PLACE:
            return place_token(self.placed.template_id)
        return None

    def creation_for(self, line_id: int) -> "PurchaseCreation | None":
        """Return this placement as the act the create door performs.

        **The TICK and the RULE reach one write through one derivation** (plan
        step ``bank_import:X-ge``, ruling **R-GH**).  :attr:`select_value`
        answers what the owner's own control would be set to and this answers
        what an import files without one, and the two are the same decision
        stated twice unless one of them is derived from the other: a rule that
        auto-applied into a destination the sweep would not have ticked would
        be a second answer to *where does this merchant's money go*, on the
        door that moves it.

        **The two arms are exactly** :class:`~._creations.PurchaseCreation`'s,
        and stating them here rather than in the caller is what keeps the
        ambiguous shape unbuildable: ``_create._reject_ambiguous_destination``
        refuses a submission naming both arms or neither, and a caller
        assembling the value by hand is a caller that can assemble one of
        those.

        Args:
            line_id: The bank line this placement is for.  Taken rather than
                carried on the placement, because a placement is what a rule
                comes to for *a* line and the same value is built per line by
                :func:`placements_for`; a stored id would be a second copy of
                the key its own caller already holds.

        Returns:
            The :class:`~._creations.PurchaseCreation`, or ``None`` for an
            :attr:`PlacementKind.UNRESOLVED` placement -- a rule that does not
            reach this line names no destination, so there is no act to
            perform.  ``None`` exactly where :attr:`select_value` is ``None``,
            which is the property the pair is graded as.
        """
        if self.kind is PlacementKind.RECORD_IN:
            return PurchaseCreation(
                line_id=line_id,
                transaction_id=self.destination.transaction_id,
            )
        if self.kind is PlacementKind.CREATE_NEW:
            return PurchaseCreation(
                line_id=line_id, new_envelope=self.new_envelope,
            )
        if self.kind is PlacementKind.PLACE:
            return PurchaseCreation(
                line_id=line_id, template_id=self.placed.template_id,
            )
        return None


def _template_placement(
    rule: StandingRule,
    offered: "list[PurchaseDestination]",
    view: RuleView,
    period_id: "int | None",
) -> Placement:
    """Resolve the TEMPLATE answer against one line's own period.

    **A template does not always produce exactly one row in a period, and
    assuming it did would file money in a row the owner did not pick.**
    Measured on a 2026-08-18 production clone: template 22
    (``Kayla's Spending Money``) generated TWO rows in pay period 3, ids 2388
    and 2389.  So the cases are all real and all reported:

    * exactly one offerable row -- the placement;
    * none offerable, the definition is RULE-LESS and the paycheck holds NO
      row of it at all -- a row of it is PLACED here
      (:attr:`PlacementKind.PLACE`, ruling **R-BAL24**): such a definition
      generates nothing, so where a paycheck holds none of its rows the
      answer is to place one, which is what a bank-born envelope's identity
      across paychecks means.  **No row at all, not no offerable row**
      (:attr:`~._rules.RuleView.placed_periods`; found by 7b-3's adversarial
      reviews): a cancelled, credited, match-claimed or fixed-figure-closed
      row is still the paycheck's one row of the definition, and placing a
      second beside it met the occurrence index as a bare ``IntegrityError``
      that failed the whole press.  A definition with a rule takes the next
      arm instead: its rows are the ENGINE's, and placing one beside them
      would be a second writer of what its cadence says;
    * none offerable otherwise -- the template made no row here, or the one
      it made cannot take a purchase (closed at a stored figure, already
      matched, cancelled).  Measured: template 5 (``Gas``) is offerable in 9
      of the 11 periods the developer's creatable lines fall in, and
      template 38 (``Groceries``) in 10;
    * more than one -- which of them the owner meant is a guess, and this
      module does not make guesses.

    Args:
        rule: The stated answer, whose ``template_id`` is not ``None``.  It
            carries the merchant's NAME too (plan step ``bank_import:X-gd-1``),
            so the sentence and the placement name the same merchant by
            construction rather than because two arguments agreed.
        offered: The destinations open to THIS line -- already narrowed to its
            own pay period and to what no match has claimed.
        view: What the owner has said and what it can resolve against:
            ``template_names`` for the sentence, ``placeable_templates`` and
            ``placed_periods`` for whether a row may be placed here.
        period_id: The line's own paycheck, or ``None`` where it has none
            (a day before the books opened), which places nothing.

    Returns:
        The :class:`Placement`.
    """
    merchant = rule.merchant
    matches = [
        destination for destination in offered
        if destination.template_id == rule.template_id
    ]
    named = view.template_names.get(rule.template_id)
    if len(matches) == 1:
        return Placement(
            merchant=merchant, kind=PlacementKind.RECORD_IN,
            destination=matches[0],
        )
    if (
        not matches
        and period_id is not None
        and rule.template_id in view.placeable_templates
        and period_id not in view.placed_periods.get(rule.template_id, ())
    ):
        return Placement(
            merchant=merchant, kind=PlacementKind.PLACE,
            placed=PlacedDefinition(template_id=rule.template_id, name=named),
        )
    if not matches:
        return Placement(
            merchant=merchant, kind=PlacementKind.UNRESOLVED,
            unresolved_reason=(
                f"You file {merchant} in {named or 'a recurring envelope'}, "
                f"and this pay period has none that can take a purchase -- it "
                f"may not have been generated here, or it may have closed at a "
                f"fixed figure."
            ),
        )
    return Placement(
        merchant=merchant, kind=PlacementKind.UNRESOLVED,
        unresolved_reason=(
            f"You file {merchant} in {named or 'a recurring envelope'}, and "
            f"this pay period holds {len(matches)} of them -- pick the one you "
            f"mean."
        ),
    )


def _new_envelope_placement(
    rule: StandingRule,
    offered: "list[PurchaseDestination]",
    view: RuleView,
) -> Placement:
    """Resolve the NEW-ENVELOPE answer against one line's own period.

    **A NEW-ENVELOPE answer fires ONCE across statements**: its first line
    either converges on a same-named placed envelope already in the line's
    period or mints a rule-less definition through
    ``one_off.place_one_off``, and either way the answer becomes TEMPLATE
    naming that definition (finding **N-328**, ruling **R-BAL24**, leaf 7b-3
    of ``balance:X-bi-7b``; :func:`~._naming.name_the_filed_definition`),
    so from the NEXT request every line of that merchant resolves through
    :func:`_template_placement` and PLACES a row of the definition where its
    paycheck holds none.  Within the press that fired it the read model
    still says NEW-ENVELOPE, and the later lines of the same answer are
    converged on that definition by :class:`~._container.MintedEnvelopes`
    instead.  What this arm decides is the first firing.

    **An answer naming an envelope by name is answered by a PLACED envelope
    of that name and category where one is already here** (finding
    **N-327**, developer ruling 2026-08-20): an envelope the owner made at
    the grid under that name carries a rule-less definition, and minting a
    second beside it would be the fragmentation N-327 measured -- a ``Lowe's``
    answer once made 4 envelopes across 3 pay periods in one press.  It is
    still only a suggestion: the placement prints beside the line's own
    select, which opens on *leave this line alone*.  **Two of them is a
    guess, so it is reported instead**, the rule `_template_placement`
    applies to a template that generated two rows in one period.

    **A RECURRING definition's row is NOT converged on**: naming a template
    is a DIFFERENT answer with its own resolution beside this one, and an
    owner who means the recurring envelope has that answer available and did
    not pick it.  The convergence key is "no cadence made it"
    (``PurchaseDestination.names_no_cadence``), which is ``is_placed`` --
    the same predicate the FLIP is keyed on
    (:func:`~._naming.name_the_filed_definition`).  Until the family's
    cutover (plan step ``balance:X-bi-7d-2``) the key also admitted a LEGACY
    link-less row (33 of the developer's 256 offerable destinations on
    2026-08-30), which had no definition for the answer to name, so the
    name compare lasted while the flip could not happen; the cutover minted
    each of those a definition and the two predicates coincide.

    Args:
        rule: The stated answer, whose ``answer`` is ``NEW_ENVELOPE``.  It
            carries the merchant's NAME too, for the reason
            :func:`_template_placement` states.
        offered: The destinations open to THIS line -- already narrowed to its
            own pay period and to what no match has claimed.
        view: What the owner has said and what it can resolve against.

    Returns:
        The :class:`Placement`.
    """
    merchant = rule.merchant
    if rule.category_id not in view.active_categories:
        return Placement(
            merchant=merchant, kind=PlacementKind.UNRESOLVED,
            unresolved_reason=(
                f"You give {merchant} a new envelope called "
                f"{rule.envelope_name}, and the category you filed it "
                f"under has been archived -- so nothing can be created for "
                f"it until you answer for {merchant} again."
            ),
        )
    # **The whole answer, never just its name.**  A rule's answer is a name
    # AND a category, and the within-press registry keys on both -- so matching
    # the name alone here made the two halves of one rule disagree, and would
    # have filed spending into a same-named envelope under a category the owner
    # did not pick.  The label is not compared either: it appends the
    # pay-period span for a reader.
    named = [
        destination for destination in offered
        if destination.name == rule.envelope_name
        and destination.category_id == rule.category_id
        and destination.names_no_cadence
    ]
    if len(named) == 1:
        return Placement(
            merchant=merchant, kind=PlacementKind.RECORD_IN,
            destination=named[0],
        )
    if len(named) > 1:
        return Placement(
            merchant=merchant, kind=PlacementKind.UNRESOLVED,
            unresolved_reason=(
                f"You give {merchant} an envelope called "
                f"{rule.envelope_name}, and this pay period already holds "
                f"{len(named)} of them -- pick the one you mean."
            ),
        )
    return Placement(
        merchant=merchant, kind=PlacementKind.CREATE_NEW,
        new_envelope=NewEnvelope(
            name=rule.envelope_name, category_id=rule.category_id,
        ),
    )


def placements_for(
    merchant_id: "int | None",
    view: RuleView,
    offered: "list[PurchaseDestination]",
    *,
    period_id: "int | None" = None,
) -> "Placement | None":
    """Return what the owner's rule comes to for ONE creatable line.

    Args:
        merchant_id: The line's merchant row, or ``None`` where the source
            names none -- which keys no rule at all, so the answer is
            ``None``.  **That is the whole reason the merchant is a nullable
            fact** (plan step X-f6a-3d): a reader that fell back to the
            description would key one rule for every truncated line a second
            adapter records and fire it on all of them.
        view: What the owner has said and what it can resolve against
            (:class:`RuleView`).
        offered: The destinations open to this line, in its own pay period.
        period_id: The line's own paycheck (``budget.pay_periods.id``), which
            the PLACE arm needs to ask whether the definition already holds a
            row there; ``None`` for a line before the books opened, which no
            arm places into.  Keyword-only and defaulted for the callers that
            resolve against *offered* alone.

    Returns:
        The :class:`Placement`, or ``None`` when nothing is placed -- which is
        three facts this function deliberately does not distinguish, because
        none of them puts anything beside the line's own control: the owner has
        not stated a rule for this merchant, they have stated *ask me every
        time*, or they have stated *never a purchase*.  **The last is a BAR,
        not a placement** (ruling **R-GJ**): it is answered by
        :meth:`~._bars.CreationBars.bar_for`, which
        :func:`~._leftovers._creatable_lines` asks first, so a line carrying that
        answer never reaches here at all.

    **The dispatch names the answers that PLACE and falls through to nothing**,
    which is the direction that matters: the two container answers are asked
    for by name and everything else returns ``None``.  It used to be written
    the other way round -- name the answers that place nothing, fall through to
    :func:`_template_placement` -- and that shape put a fifth answer one edit
    away from being resolved as a template with a ``NULL`` template id.  Ruling
    **R-GS** added the fourth answer, which is exactly that edit.
    """
    if merchant_id is None:
        return None
    rule = view.rules.get(merchant_id)
    if rule is None:
        return None
    if rule.answer is RuleAnswer.TEMPLATE:
        return _template_placement(rule, offered, view, period_id)
    if rule.answer is RuleAnswer.NEW_ENVELOPE:
        return _new_envelope_placement(rule, offered, view)
    return None


@dataclass(frozen=True)
class InflowPlacement:
    """What the owner's rule comes to for one unexplained DEPOSIT.

    Ruling **bank_import:R-HT(a)**, plan step ``bank_import:X-gj-2a``.
    :class:`Placement`'s twin for the other direction, and deliberately a
    SEPARATE value rather than a fourth :class:`PlacementKind`: the two resolve
    against different things and produce different acts.  An outflow placement
    picks from the pass's own offer set of budget lines and yields a
    :class:`~._creations.PurchaseCreation`; this names a CATEGORY -- which is
    not a container, reserves nothing and is not drawn from any offer set --
    and yields a :class:`~._creations.IncomeCreation`.  A value carrying both
    shapes would be one Jinja condition away from rendering a destination
    select beside a deposit, which is exactly what ruling **bank_import:R-GW**
    states the emptiness of :class:`~._leftovers.RecordableInflow` to prevent.

    **There is no CREATE_NEW arm and there cannot be one.**  An income row is
    filed against nothing, so there is no container for an answer to mint --
    which is why this has two states where :class:`Placement` has three.

    Attributes:
        merchant: The line's merchant, which is the rule's key.
        category_id: The income category the answer names, or ``None`` when
            this pass will not act on it.
        category: What that category is CALLED, carried beside its id for the
            reason :attr:`~._rules.StandingRule.merchant` is: the card's
            sentence prints it, and the read that resolved the answer already
            had it (:attr:`~._rules.RuleView.active_categories`).  Reading it
            back separately would be a redundant producer call, and worse, the
            label a card prints could then come from a different instant than
            the answer it describes.
        unresolved_reason: One sentence saying why the rule does not reach this
            line, or ``None`` when it does.  **Exactly one of the two is set**,
            which is :attr:`~._bars.BarredLine.answer_door`'s own idiom: a
            value that could carry a destination AND a reason not to use it is
            two answers to one question.
    """

    merchant: str
    category_id: "int | None" = None
    category: "str | None" = None
    unresolved_reason: "str | None" = None

    @property
    def records(self) -> bool:
        """Return whether this places the deposit under a category."""
        return self.category_id is not None

    def creation_for(self, line_id: int) -> "IncomeCreation | None":
        """Return this placement as the act the income door performs.

        The inflow twin of :meth:`Placement.creation_for`, and it exists for
        the same reason: what a rule files WITHOUT a press and what the owner's
        own OK submits have to be one derivation, or the automatic door and the
        card are two answers to *what is this deposit* on the door that moves
        money.

        Args:
            line_id: The bank line this placement is for.

        Returns:
            The :class:`~._creations.IncomeCreation`, or ``None`` for an
            unresolved placement -- a rule that does not reach this line names
            no category, so there is no act to perform.

            **The act carries the LINE and not the category**, and this method
            is what DECIDES there is an act rather than what says what it
            files under: :func:`~._income.record_income_from_line` re-derives
            the classification from the same stored rule, so the automatic
            door and the owner's own OK cannot answer *what is this deposit*
            differently (adversarial code review 2026-08-31, which measured
            them doing exactly that).
        """
        if self.category_id is None:
            return None
        return IncomeCreation(line_id=line_id)


def inflow_placement_for(
    merchant_id: "int | None", view: RuleView,
) -> "InflowPlacement | None":
    """Return what the owner's rule comes to for ONE unexplained deposit.

    Ruling **bank_import:R-HT(a)**, plan step ``bank_import:X-gj-2a``.  The
    inflow twin of :func:`placements_for`, and it takes NO offer set because
    there is nothing to pick from: a deposit is filed against a category rather
    than against a budget line, so the answer is resolvable from the rule and
    the owner's live categories alone.

    **The dispatch names the answers that RESOLVE and falls through to
    nothing**, which is the direction :func:`placements_for`'s own docstring
    argues for: written the other way round, a sixth answer would be one edit
    away from being read as an income category with a ``NULL`` id.

    Args:
        merchant_id: The line's merchant row, or ``None`` where the source
            names none -- which keys no rule, so the answer is ``None``.
        view: What the owner has said and what it can resolve against.

    Returns:
        The :class:`InflowPlacement`, or ``None`` when nothing is placed --
        which is three facts deliberately not distinguished, exactly as
        :func:`placements_for` does not distinguish its own three: the owner
        has said nothing about this merchant, or said *ask me every time*, or
        said *never a purchase*.  None of the three is this pass withholding
        anything, so none is reported as one.

    **A merchant with a SPENDING answer NEVER REACHES HERE**, and that arm's
    removal is plan step ``bank_import:X-gj-2b-2``.  It used to return an
    UNRESOLVED placement whose sentence said the refund act was not built.  It
    now IS built: such a line is a REFUND -- a negative purchase back into the
    container that answer names -- so :func:`~._rules.pipeline_for` routes it
    to :func:`placements_for` and the purchase door, and this function is
    reached only by the lines the income door owns.

    That is what makes this function's return type honest again: it names a
    CATEGORY and yields an ``IncomeCreation``, and there is no longer a state
    in which it describes an act it cannot produce.  The refusal that keeps it
    true is :func:`~._income.record_income_from_line`'s, which turns a
    container-answer line away at the door for the same reason the screen does
    not offer one.
    """
    if merchant_id is None:
        return None
    rule = view.rules.get(merchant_id)
    if rule is None:
        return None
    if rule.answer is RuleAnswer.INCOME_CATEGORY:
        return _income_placement(rule, view)
    return None


def _income_placement(rule: StandingRule, view: RuleView) -> InflowPlacement:
    """Resolve the INCOME-CATEGORY answer for one deposit.

    **One way to fail and it is REPORTED**, which is the rule this whole module
    keeps: the category the answer names may have been archived since, and
    filing new money into a category the owner has retired would resurrect it
    silently.  Substituting -- falling back to no category at all, which is
    what the un-ruled inflow door writes -- is refused for the reason
    :func:`_template_placement` refuses its own substitution: it files money
    somewhere the owner did not name, and here it would do so under a receipt
    saying their rule ran.

    Args:
        rule: The stated answer, whose ``answer`` is
            :attr:`~._rules.RuleAnswer.INCOME_CATEGORY`.
        view: What the owner has said and what it can resolve against.

    Returns:
        The :class:`InflowPlacement`.
    """
    if rule.income_category_id not in view.active_categories:
        return InflowPlacement(
            merchant=rule.merchant,
            unresolved_reason=(
                f"You file deposits from {rule.merchant} under "
                f"{view.category_label_for(rule.income_category_id)}, and you "
                f"have archived it -- so nothing can be recorded under it "
                f"until you answer for {rule.merchant} again."
            ),
        )
    return InflowPlacement(
        merchant=rule.merchant,
        category_id=rule.income_category_id,
        category=view.category_label_for(rule.income_category_id),
    )
