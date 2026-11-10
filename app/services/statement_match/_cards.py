"""One CARD per bank line, and the sections the inbox groups them into.

Plan steps ``bank_import:X-gj-1a`` and ``X-gj-1b``; rulings
**bank_import:R-HP**, **R-HQ**, **R-HR**, **R-HS**, **R-HW** and **R-HX**.
:mod:`._reconcile` assembles the PAGE; this is the card it is made of, and the
two are separate modules because they are separate subjects -- and because one
file holding both passed the 1,000-line ceiling that already split
:mod:`._accepted_view` out of :mod:`._reads`.

**The same card renders on all five tabs**, which is the whole design: the
inbox, the holding tab, the skipped tab and the two settled tabs are one list
seen five ways, not five screens.  THREE kinds exist because the three
subjects carry disjoint facts -- a :class:`LineCard` has a bank line and a
:class:`~._panel.VerbPanel`, an :class:`ActCard` has an Undo and what it would
destroy, a :class:`SkipCard` has a bank line and an Undo that destroys nothing
-- and one value holding empty versions of another's fields is a control one
Jinja condition away from rendering, which is what
:class:`~._bars.BarredLine` exists to refuse.

*It said TWO until plan step ``bank_import:X-gj-4c-2``*, which built the
Skipped tab: a recorded skip is neither a bank line the books have not settled
nor a :class:`~app.models.statement_match.StatementMatch`, so it is neither of
the first two kinds and could be made to look like one only by leaving fields
empty.  Which kind a tab holds is the TAB's own fact (:class:`CardKind`), and
that value replaced a BOOLEAN for exactly this reason -- a two-valued answer
has no third arm to give.

**What the card SHOWS and what the panel DISCLOSES are two values** (ruling
**R-HR**, plan step ``X-gj-1b``).  :class:`LineCard` carried twelve attributes
under a ``too-many-instance-attributes`` disable while it held both, and the
page needed two more -- which act ADD would perform, and the rows MATCH
offers.  :mod:`._panel` holds the disclosure now, so the disable is DELETED
rather than raised: the boundary ruling R-HR draws is stated in the type
system, and a template cannot print a panel fact beside the sentence because
it is not there to print.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._panel import AddAct, AddTab, VerbPanel
from ._rules import is_inflow
from ._sentence import NAMED_ROW_LIMIT, Span, choose
from ._sentence import for_income_placement
from ._sentence import for_parked_transfer, for_placement, for_proposal
from ._verbs import (
    ADD_SHUT_BY_A_PROPOSAL,
    SKIP_SHUT_PAYS_AN_ACCOUNT,
    Verb,
    VerbOffer,
    offers_for,
)

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from ._accepted_view import AcceptedGroup
    from ._bars import BarredLine
    from ._leftovers import CreatableLine, RecordableInflow
    from ._offers import BankLine, MatchProposal
    from ._already_held import ArrivalsAlreadyHeld, SpendingAlreadyHeld
    from ._reads import ReviewSet
    from ._skipping import SkippedAct


class Section(enum.Enum):
    """What suggested a To-explain card's verb, which is how the tab groups.

    Ruling **bank_import:R-HP** replaced ruling **R-HB**'s visible EVIDENCE
    grouping with this one and returned the evidence partition to the service.
    The reader's question on every line is the same -- *what is this?* -- and
    what they need to know first is how much the app is claiming: a standing
    rule the owner stated, a tier's guess, or nothing at all.

    **The order of the members is the order the sections render**, most
    confident first, because that is the order the work is quickest in.
    """

    BY_RULE = "by_rule"
    PROPOSED = "proposed"
    NOTHING = "nothing"

    @property
    def heading(self) -> str:
        """Return the thin rule this section is printed under.

        Returns:
            The heading.  It is a NAME and a count and nothing else: ruling
            **R-HR** moved the paragraph that used to sit under each heading
            into the opened card, because the same two sentences were printed
            sixteen times on one screen.
        """
        return _SECTION_HEADINGS[self]


#: Each section's heading.  Three words or so each, deliberately: **R-HR**
#: retired the explanatory paragraph these used to carry.
_SECTION_HEADINGS: "dict[Section, str]" = {
    Section.BY_RULE: "Suggested by your rules",
    Section.PROPOSED: "Proposed",
    Section.NOTHING: "Nothing suggested",
}


class CardKind(enum.Enum):
    """Which of the three card values a tab's sections hold.

    Plan step ``bank_import:X-gj-4c-2``.  **The ONE fact a template needs in
    order to pick a partial**, and it is the service's rather than a Jinja
    condition over a tab's name: the three kinds carry disjoint fields and
    disjoint controls, so a template that guessed would print a field that is
    not there.

    **It replaced a BOOLEAN**, ``Tab.holds_settled_acts``, and the
    replacement is what the Skipped tab forced rather than a tidy-up.  That
    predicate partitioned five tabs into *acts* and *bank lines*; a recorded
    skip is an ACT with no :class:`~app.models.statement_match.StatementMatch`
    behind it and a bank line the reader can print, so it answers the
    boolean's question with neither value.  A third arm cannot be added to a
    two-valued answer, and widening the boolean's meaning is how a tab comes
    to render the wrong partial with nothing raising.

    **It is carried on the PAGE and not on the TAB** (**R-JX**), and that
    placement is the whole of what makes it one fact.
    A ``Tab.holds`` property read from a table beside a dispatch that
    independently built the cards was ONE fact with TWO homes: set the table
    to :attr:`ACT` for the Skipped tab and the page still built
    :class:`SkipCard` values, the template still picked the act partial, and
    the page 500'd on a field that is not there -- with nothing but a test
    holding the two in step.  :attr:`~._reconcile.ReconcilePage.kind` is set
    BY the dispatch that builds the sections, so the two-home disagreement is
    gone and its reconciling test with it -- LOCAL rather than impossible,
    which the paragraph below states exactly and **R-JX** insists on.
    ``CLAUDE.md`` rule 14: where a rule says two places must always agree,
    they are one value with two homes, and the remedy is to delete a home.

    **What that buys is LOCALITY, not impossibility, and the difference is
    worth stating exactly.**  Each arm of :func:`~._reconcile._tab_sections`
    still writes a :class:`CardKind` literal beside the builder it calls, so
    ``CardKind.ACT, skip_sections(...)`` still compiles.  What changed is that
    the two halves are now one line apart in one function instead of a table
    in one module and a dispatch in another -- a mistake a reader SEES rather
    than one only a test can find.  *An earlier draft of this paragraph said
    the disagreement was "unrepresentable", which is the word the design
    doctrine reserves for a defect that cannot be written; adversarial review
    named the overclaim, and the test below this module's own change says the
    opposite four lines from where the docstring said it.*

    * ``LINE`` -- :class:`LineCard`: a bank line the books have not settled.
      It renders INSIDE the Apply form, because its OK is what that form
      submits.
    * ``ACT`` -- :class:`ActCard`: an accepted match, with the Undo that
      destroys what it created.
    * ``SKIP`` -- :class:`SkipCard`: a recorded skip, with the Undo that
      destroys the decision and nothing else.

    The last two render OUTSIDE the Apply form: an Undo is a ``form``, a form
    cannot nest in a form, and neither tab has anything to Apply.
    """

    LINE = "line"
    ACT = "act"
    SKIP = "skip"

    @property
    def is_line(self) -> bool:
        """Return whether this tab's cards are bank lines to explain.

        **A predicate rather than a value for a template to compare**, which
        is the rule :attr:`~._verbs.VerbOffer.is_match` states: a screen
        comparing an enum's own string is one rename away from silently
        rendering nothing.

        Returns:
            ``True`` for :attr:`LINE`.
        """
        return self is CardKind.LINE

    @property
    def is_act(self) -> bool:
        """Return whether this tab's cards are accepted matches.

        See :attr:`is_line`.

        Returns:
            ``True`` for :attr:`ACT`.
        """
        return self is CardKind.ACT

    @property
    def is_skip(self) -> bool:
        """Return whether this tab's cards are recorded skips.

        See :attr:`is_line`.  **The three DO partition** :class:`CardKind`, so
        a body rendering one arm each covers every page -- and the route test
        driven from :class:`~._reconcile.Tab` is what holds that true, because
        a fourth kind with no arm would render a blank tab rather than raise.

        Returns:
            ``True`` for :attr:`SKIP`.
        """
        return self is CardKind.SKIP


@dataclass(frozen=True)
class LineCard:
    """One bank line the books have not settled, as one card.

    Attributes:
        line: The bank's own record of the movement, which is the top of every
            card: the cleaned merchant, the posted day, the raw description.
        section: Which of the three To-explain sections this sits in
            (:class:`Section`), or ``None`` on a holding tab, which has one
            unnamed section.
        suggested: The verb the sentence proposes (:class:`~._verbs.Verb`), or
            ``None`` where nothing justified one -- in which case the card
            reads *Choose what this is* and opens its panel instead of
            offering a one-click OK (**R-HS**, **R-HX**).
        sentence: The ONE sentence the card carries, as spans
            (:mod:`._sentence`).  **The whole of what the card says**: ruling
            **R-HR** put every reason one click away.
        already_held: What the books may already hold this line as, or
            ``None``: for money ARRIVING, every arriving row this line's own
            pay period already holds that no bank line explains
            (:class:`~._already_held.ArrivalsAlreadyHeld`); for money LEAVING,
            the unexplained rows named for its merchant or inside its rule's
            destination (:class:`~._already_held.SpendingAlreadyHeld`, ruling
            **bank_import:R-BI19**, plan step ``bank_import:X-f6b-2``).  **The
            one money-at-risk signal this build has**, and the only thing on
            a card that may be drawn in amber: recording a line the books
            already hold in another shape is how money gets counted twice.
            The two values carry their own words for the alert, so the
            template prints one macro and chooses nothing.  *The field was
            arriving-only until X-f6b-2*, which is finding **N-381**: the
            outflow side had no positive signal at all, and under the daily
            feed its automatic door needed one first.  **ARRIVING and not
            income** on that side -- a stored refund is one of them since
            ruling **bank_import:R-II** -- and the field said ``income`` until
            plan step ``bank_import:X-gj-2b-3``.
        risk_class: Which of :data:`~._reconcile.SWEEP_LABELS` this card's act
            falls under, or ``None`` where it falls under none.  It is what a
            sweep would reach IF the card were clean; :attr:`sweep_class` is
            the guarded answer and is what a control may read.
        panel: What the opened card discloses (:class:`~._panel.VerbPanel`) --
            all four verbs, every sentence this pass owes the reader, and what
            each verb's tab offers.
    """

    line: "BankLine"
    section: "Section | None"
    suggested: "Verb | None"
    sentence: "tuple[Span, ...]"
    already_held: "ArrivalsAlreadyHeld | SpendingAlreadyHeld | None"
    risk_class: "str | None"
    panel: VerbPanel

    @property
    def offers_ok(self) -> bool:
        """Return whether this card offers the one-click OK.

        **It asks the DOOR and not the sentence**, which is the difference
        between a working button and the shape ruling **R-HW** forbids: a
        Transfers card's sentence opens on TRANSFER, a verb with no door, so
        a test on :attr:`suggested` alone would render OK on every parked card
        payment -- 9 of the developer's 27 unexplained lines, measured
        2026-08-29.  *That measurement also covered the lines a standing
        *never a purchase* answer barred, which the same sentence named
        separately until plan step ``bank_import:X-gj-4c``*: those carry no
        suggestion at all now (:func:`answered_never_card`), so this predicate
        is not what withholds their OK and claiming it were would be a guard
        taking credit for a state it cannot reach.

        Returns:
            ``True`` exactly when a verb was justified AND that verb has a
            door.  A card with neither shows *Choose*, which opens the panel,
            so the two controls are exclusive and a template asks one
            question.
        """
        if self.suggested is None:
            return False
        return self.panel.offer_for(self.suggested).is_open

    @property
    def takes_ok(self) -> bool:
        """Return whether this card can be consented to at all.

        Plan step ``bank_import:X-gj-1b``.  **The question the CHECKBOX asks,
        and it is not the one :attr:`offers_ok` asks.**  ``offers_ok`` says
        whether the SUMMARY shows a one-click OK button -- a verb was
        justified and its door exists.  This says whether the card has any act
        to consent to at all, which is what decides whether the ``ok``
        checkbox is in the document.

        **They were one question until an adversarial review measured what
        that cost.**  The panel renders its primary button as a
        ``<label for="ok-N">`` inside every open verb pane, and the checkbox
        it points at was rendered only where ``offers_ok`` -- so a card with no
        suggestion rendered a button pointing at an element that was not in the
        document.  Measured 2026-08-30 on a restored production clone: **31 of
        248 cards**, and on those the primary act was dead in a browser -- an
        inflow could never be recorded, an unruled swipe could never become a
        purchase, a parked payment could never be group-matched -- with the
        whole suite green, because every acting test hand-appended an ``ok``
        value a browser could not have produced.

        Returns:
            Whether any verb's door would accept this line
            (:attr:`~._panel.VerbPanel.open_verbs`).  **It is the set of
            verbs this card may be consented TO**, which is what decides
            whether the ``ok`` checkbox is in the document.  TRANSFER has no
            door in this build so it is never open; SKIP is open on most cards
            since plan step ``bank_import:X-gj-4b``, and on a card with no
            other act it is what puts the checkbox there at all.  *This read
            "TRANSFER and SKIP have no door so they are never open", and
            called this "exactly the set whose panes render a submitting
            control" -- that step falsified both*: an open SKIP pane renders
            no control of its own, because the verb takes no argument, and
            the footer's verb-named button is what submits it.  An open MATCH
            always offers the row list, and an open ADD always carries an
            :class:`~._panel.AddTab` -- a card whose ADD has no act states its
            refusal through ``add_waits`` instead, which shuts the verb.
            Measured on the same 248 cards: this predicate and *a pane renders
            a control* agree on every one, with no disagreement.

            ``False`` is reachable and is the holding state ruling **R-HQ**
            names: a parked card payment on an account whose every row is
            already explained has no act, so it takes no OK and its panel is a
            disclosure.
        """
        return bool(self.panel.open_verbs)

    @property
    def opens_on(self) -> Verb:
        """Return which of the four tabs the opened panel opens on.

        **Total, and stated here rather than in Jinja**, because it decides
        which control a reader sees first on a screen that writes money.  Two
        arms, each with a reason:

        * the verb the sentence proposes, where one was justified.  The panel
          opens on the destination a standing rule names, which is ruling
          **R-HS**'s pre-fill read at the grain of the tab;
        * otherwise the verb this line's own mechanism can act on -- ADD where
          it has an act (a purchase for an outflow, an income row for a
          deposit) -- and failing that the first verb with a door at all.
          **The mechanism rather than the enum's order**, because ruling
          **R-HX** leaves 16 of the developer's own 18 inbox cards with no
          suggestion, and opening every one of them on MATCH would put the
          only act they have behind a tab.

        Returns:
            The :class:`~._verbs.Verb` whose tab opens.  A card with NO open
            verb -- a parked payment on an account with nothing left to pair
            -- opens on :attr:`suggested` or, failing that, the first verb,
            whose tab is then its explanation.  That is the holding state
            ruling **R-HQ** names, and it is a disclosure rather than a
            control (**R-HW**).
        """
        if self.suggested is not None:
            return self.suggested
        open_verbs = self.panel.open_verbs
        if self.panel.add is not None and Verb.ADD in open_verbs:
            return Verb.ADD
        return open_verbs[0] if open_verbs else Verb.MATCH

    @property
    def sweep_class(self) -> "str | None":
        """Return which sweep may reach this card, or ``None`` where none may.

        **ONE statement of the guard, on the value.**  Ruling **R-FZ(c)**
        sweeps per risk class, and the developer's ruling of 2026-08-28 added
        the second half: a bulk click may not reach a line whose own card says
        the books may already hold it, or that the pass could not finish
        checking.  The retired queue's ``_sweeps_for`` kept that by giving
        sweeps to one evidence group only -- a coupling that broke the moment
        the grouping changed -- so it is stated here as the predicate it
        always was.  *That producer went with its model at plan step
        ``bank_import:X-gi-3``; the coupling it is named for is the reason
        this predicate did not go with it.*

        Returns:
            :attr:`risk_class` when this card carries a working verb, no
            withheld sentence and no money at risk; ``None`` otherwise.
        """
        if not self.offers_ok:
            return None
        if self.panel.notes or self.already_held is not None:
            return None
        return self.risk_class


@dataclass(frozen=True)
class ActCard:
    """One act already applied, as the same card one tense over.

    Attributes:
        act: The :class:`~._accepted_view.AcceptedGroup` -- what the bank
            showed, what it named, whether it still holds, and what an Undo
            would destroy.
        sentence: The past-tense sentence (:func:`~._sentence.for_accepted`).

    **Two fields and not a flattened copy**, because everything else the
    settled card renders is already on the act and a second spelling of it is
    how the Undo control comes to promise something the door will not do --
    which is the argument :attr:`~._accepted_view.AcceptedGroup.removes`
    already makes for reading the release door's own derivation.
    """

    act: "AcceptedGroup"
    sentence: "tuple[Span, ...]"

    @property
    def rows_not_named(self) -> "tuple[str, ...]":
        """Return the member rows the SENTENCE could not fit.

        Plan step ``bank_import:X-gj-1c``.  **The settled tabs retire the
        register (R-HU), so they may not drop what it printed.**  That page
        listed every member row's label; this card carries a sentence, and
        :func:`~._sentence.for_accepted` names at most
        :data:`~._sentence.NAMED_ROW_LIMIT` of them before it says *and N
        more*.  The count was never silent, but the NAMES were -- so an owner
        deciding whether to undo could not see which rows the act claims.

        Measured on a restored production clone 2026-08-31, 220 acts on the
        developer's own account: **217 name one row, 2 name two, and 1 names
        four** -- so this is non-empty for exactly ONE of them, and the card
        stays one line for the other 219.  An earlier draft of this module's
        card claimed *0 of 221 acts have either to say*; that figure is
        :attr:`~._release.PlannedRemovals.rows`' (removals), and applying it
        to member rows was measured false by adversarial review.

        Returns:
            The labels beyond what the sentence names, in the act's own order;
            ``()`` when it named them all.  **It reads the same constant the
            sentence does**, so the two cannot disagree about where the cut
            falls -- which is the whole reason it is here and not a length
            comparison in Jinja.
        """
        labels = tuple(row.label for row in self.act.rows)
        if len(labels) <= 1:
            return ()
        return labels[NAMED_ROW_LIMIT:]


@dataclass(frozen=True)
class SkipCard:
    """One recorded skip, as the same card with an Undo that destroys nothing.

    Plan step ``bank_import:X-gj-4c-2``, rulings **bank_import:R-JG** and
    **R-JH**.  The locked direction gives the Skipped tab in three words --
    ``docs/design/bank_import_audit.md``: *the same card with Undo* -- and this
    is that card.

    Attributes:
        skip: The recorded act (:class:`~._skipping.SkippedAct`) -- the
            ``skip_id`` the Undo submits, and the bank's own record of the
            line it disposes of.
        sentence: The past-tense sentence (:func:`~._sentence.for_skip`).

    **Two fields and not a flattened copy**, which is :class:`ActCard`'s own
    argument one act over: everything the card prints is already on the act,
    and a second spelling is how an Undo comes to name a line the door will
    not find.

    **It is NOT an** :class:`ActCard`, and the distinction is a field rather
    than a preference: that value carries an
    :class:`~._accepted_view.AcceptedGroup`, whose ``rows``, ``removes`` and
    ``agrees`` a skip has none of -- there is no app row to hold, nothing for
    an Undo to remove (:func:`~._skipping.unskip_line`), and nothing that could
    stop agreeing.  Reusing it would mean three empty fields and a template one
    condition away from printing *Undo removes 0 row(s)* over a decision.

    **It is NOT a** :class:`LineCard` **either**, though both print a bank
    line: that value carries a :class:`~._panel.VerbPanel` and an OK, and a
    skipped line is out of the pass entirely -- no verb is offered for it and
    no OK can reach it, so the panel would be four shut verbs nobody asked
    for.
    """

    skip: "SkippedAct"
    sentence: "tuple[Span, ...]"


@dataclass(frozen=True)
class CardSection:
    """One thin rule, and the cards under it.

    Attributes:
        section: Which of the three To-explain sections
            (:class:`Section`), or ``None`` for a tab that has one unnamed
            section.
        cards: The cards, all of one kind: :class:`LineCard` on To explain
            and Transfers, :class:`ActCard` on Explained and Filed by rules,
            :class:`SkipCard` on Skipped.  Which kind is the TAB's fact
            (:class:`CardKind`), so the three are rendered by three partials
            and none ever meets another's type.  *It named Skipped among the
            bank-line tabs until plan step ``bank_import:X-gj-4c-2``, which
            was true of the lines a standing answer barred and is not true of
            a recorded skip.*
        withheld: How many cards a BOUND left out -- ``0`` where none did.
            **No default**, which is the discipline
            :attr:`~._accepted_view.AcceptedGroup.applied_by_rule` keeps for
            its own reason: ``0`` is the value that reads as safe, and it
            claims *the whole record is here*.  **A truncated list that does not say it
            is truncated is a page claiming to be the whole record**, which is
            :class:`~._accepted_view.AcceptedRegister`'s own argument for
            travelling with the same count; dropping it on the way to the
            screen would have reinstated exactly what that value exists to
            prevent.

    **An empty section is ABSENT rather than rendered empty**, which is the
    rule the retired queue's ``StatementQueue`` already kept: a heading over
    no rows reads as work the owner has somewhere to do.
    """

    section: "Section | None"
    cards: "tuple[LineCard, ...] | tuple[ActCard, ...] | tuple[SkipCard, ...]"
    withheld: int

    @property
    def count(self) -> int:
        """Return how many cards this section holds.

        Returns:
            The count, which the heading prints beside its name.
        """
        return len(self.cards)


def _offers(
    review: "ReviewSet", line: "BankLine", add_waits: "str | None",
    proposal: "MatchProposal | None",
) -> "tuple[VerbOffer, ...]":
    """Return all four verb offers for one line.

    **The one place the pass-level MATCH and SKIP facts are applied.**  A
    proposal names its own rows, and those rows are exactly the ones
    ``unmatched_rows`` withholds
    (:func:`~._reads._rows_the_bank_never_showed`) -- so a pass whose every row
    a proposal claims would report an empty pool and shut MATCH on the very
    lines it had just matched.

    **SKIP is shut HERE and for every builder**, which is the only placement
    that is total (plan step ``bank_import:X-gj-4b``, ruling
    **bank_import:R-JI**): the screen shuts the verb on exactly the set
    :func:`~._skipping.skip_line` refuses.  Why the narrower per-list spelling
    cannot do it, and why a line naming no merchant is never shut, are stated
    once on :attr:`~._reads.ReviewSet.account_payments`, which is where the
    fact is published.

    Args:
        review: The pass, which owns the pool of unexplained app rows and the
            merchants a source files as account payments.
        line: The bank line this card is about, for its merchant.
        add_waits: Why ADD is shut for this line, or ``None``.
        proposal: The match a tier offers for this line, or ``None``.

    Returns:
        The four :class:`~._verbs.VerbOffer` values.
    """
    return offers_for(
        add_waits=add_waits,
        has_rows_to_match=proposal is not None or bool(review.unmatched_rows),
        skip_waits=(
            SKIP_SHUT_PAYS_AN_ACCOUNT
            if line.merchant_id in review.account_payments else None
        ),
    )


def proposal_card(
    review: "ReviewSet", proposal: "MatchProposal",
) -> LineCard:
    """Return the card for a match a TIER proposes.

    Args:
        review: The pass.
        proposal: The proposal, which names exactly one line -- every tier
            constructs ``lines=(line,)``.

    Returns:
        The :class:`LineCard`.
    """
    return LineCard(
        line=proposal.lines[0],
        section=Section.PROPOSED,
        suggested=Verb.MATCH,
        sentence=for_proposal(proposal),
        already_held=None,
        risk_class=proposal.review_class,
        panel=VerbPanel(
            offers=_offers(
                review, proposal.lines[0], ADD_SHUT_BY_A_PROPOSAL,
                proposal,
            ),
            # **A proposal is a CONCLUSION**, so this pass has no unfinished
            # search to report about its line: the gap sentence answers *why
            # can the app not say nothing explains this*, which a proposal
            # answers.
            notes=(),
            answer_door=None,
            # **No ADD act at all.**  The pass derives destinations from
            # ``unmatched``, which a proposal's line is not in, so no envelope
            # was worked out for it -- which is exactly what
            # :data:`~._verbs.ADD_SHUT_BY_A_PROPOSAL` says on the tab.
            add=None,
            proposal=proposal,
        ),
    )


def creatable_card(
    review: "ReviewSet", creatable: "CreatableLine",
) -> LineCard:
    """Return the card for an outflow the create door would accept.

    **A rule that does not REACH this line suggests nothing**, so an
    UNRESOLVED placement lands in *Nothing suggested* with its reason in the
    panel rather than in *Suggested by your rules* under a sentence naming a
    destination the rule never gave.  Substituting one is how a suggestion
    becomes a guess (:mod:`._placement`).

    Args:
        review: The pass.
        creatable: The :class:`~._leftovers.CreatableLine`.

    Returns:
        The :class:`LineCard`.
    """
    placement = creatable.placement
    names_a_home = placement is not None and placement.names_a_home
    return LineCard(
        line=creatable.line,
        section=Section.BY_RULE if names_a_home else Section.NOTHING,
        suggested=Verb.ADD if names_a_home else None,
        sentence=for_placement(placement) if names_a_home else choose(),
        # **The line's OWN fact, set by the pass** (:func:`~._verdict.ruled`)
        # rather than derived again here: the rows this names and the
        # sentence the automatic door withheld on are one value.
        already_held=creatable.already_held,
        risk_class=placement.sweep_class if names_a_home else None,
        panel=VerbPanel(
            offers=_offers(review, creatable.line, creatable.withheld, None),
            # **The gap is not asked for**: :func:`~._verdict.ruled` has
            # already folded it into ``warning``, which is the wider sentence
            # -- a rule this pass withheld, OR a search it did not finish --
            # so asking again would print the same words twice.
            notes=() if creatable.warning is None else (creatable.warning,),
            answer_door=None,
            add=AddTab(
                act=AddAct.PURCHASE,
                destinations=creatable.destinations,
                placement=placement,
                # Money ARRIVING that a rule files as a purchase is a REFUND
                # (ruling **R-II**).  Stated here, where the line is in hand,
                # so no template restates the partition -- and asked through
                # :func:`~._rules.is_inflow`, which is the ONE statement of the
                # bank's sign convention this package has.
                records_a_refund=is_inflow(creatable.line.amount),
            ),
            proposal=None,
        ),
    )


def inflow_card(
    review: "ReviewSet", inflow: "RecordableInflow",
) -> LineCard:
    """Return the card for money coming IN that no row explains.

    **Ruling R-HX, and the condition it set is now MET for one class of
    deposit** (plan step ``bank_import:X-gj-2a``).  That ruling said every
    unmatched inflow reads *Choose what this is* "until ``X-gj-2`` ships
    **R-HT(a)**'s inflow rule and the destination becomes one the app can
    defend" -- because recording a deposit as uncategorized income was the ONLY
    inflow act, and being the only act is not what **R-HS** means by justified.

    So the split is by whether a STANDING RULE answers this signature, and by
    nothing else:

    * a rule that names an income category is a destination the owner stated,
      so the card states it and offers OK (:func:`~._sentence
      .for_income_placement`);
    * everything else still reads :func:`choose` and opens the panel -- a
      merchant credit included, which is a REFUND whose act
      ``bank_import:X-gj-2b`` builds, and calling it income remains the wrong
      act this arc's audit measured.

    **The reason a rule withheld is a NOTE and never a suppressed sentence.**
    An unresolved placement carries one -- an archived category, or a spending
    answer whose refund arm does not exist yet -- and it joins the panel's
    notes beside the search gap, so the owner reads why their own rule did
    nothing rather than seeing a card that looks unanswered.

    Args:
        review: The pass, which owns the search gap, the
            books-already-hold-income signal and the category names a stated
            rule resolves against.
        inflow: The :class:`~._leftovers.RecordableInflow`.

    Returns:
        The :class:`LineCard`.
    """
    gap = review.search_gap_for(inflow.line)
    placement = inflow.placement
    files_here = placement is not None and placement.records
    withheld_by_rule = (
        placement.unresolved_reason if placement is not None else None
    )
    return LineCard(
        line=inflow.line,
        section=Section.BY_RULE if files_here else Section.NOTHING,
        suggested=Verb.ADD if files_here else None,
        sentence=(
            for_income_placement(placement)
            if files_here else choose()
        ),
        already_held=review.arrivals_already_held_in(inflow.line),
        risk_class=None,
        panel=VerbPanel(
            offers=_offers(review, inflow.line, inflow.withheld, None),
            notes=tuple(
                said for said in (inflow.withheld, withheld_by_rule, gap)
                if said is not None
            ),
            answer_door=None,
            # **An income row is filed against NO container**, so the tab
            # offers no destinations and carries no placement: ruling
            # **R-GW** states that emptiness as the whole difference between
            # the two ADD doors, and :class:`~._panel.AddAct` is what says
            # which one this is instead of a template reading the sign.
            # ``records_a_refund=False`` is STATED rather than defaulted
            # (plan step ``bank_import:X-gj-2b-3``): an income row is filed
            # against no container, so it is not a refund -- and a default
            # would have meant that silently for every future builder too.
            add=AddTab(
                act=AddAct.INCOME, destinations=(), placement=None,
                records_a_refund=False,
            ),
            proposal=None,
        ),
    )


def _barred_panel(review: "ReviewSet", barred: "BarredLine") -> VerbPanel:
    """Return the opened panel a barred line's card carries, on either list.

    **ONE panel for both** (:func:`parked_card` and
    :func:`answered_never_card`), because what a bar does to the four verbs
    does not depend on which list the line is in: ADD is shut by the bar,
    TRANSFER and SKIP have no door in this build, and MATCH is open exactly
    when the pass offers a row.  What the two cards differ in is the SENTENCE
    and the SECTION, which is a statement about where the line stands rather
    than about what may be pressed.

    Args:
        review: The pass, which owns the search gap and the row pool.
        barred: The :class:`~._bars.BarredLine`.

    Returns:
        The :class:`~._panel.VerbPanel`.

    **The answer door is asked of the value and not decided here**, so both
    builders get the same answer to *would changing this answer open anything*:
    :attr:`~._bars.BarredLine.answer_door` withholds it for a line a source
    files as an account payment, which no answer lifts, and names it for one
    barred by the owner's own answer alone.  Asking it here rather than
    branching on the list keeps the two lists from being a second spelling of
    that predicate.
    """
    gap = review.search_gap_for(barred.line)
    return VerbPanel(
        offers=_offers(review, barred.line, barred.reason, None),
        notes=tuple(
            said for said in (barred.reason, gap) if said is not None
        ),
        answer_door=barred.answer_door,
        # **NO ADD act, and that is the BAR rather than a shortage of
        # destinations** (ruling **R-GJ**): the bar is asked BEFORE a
        # destination is resolved, so there is nothing for the tab to offer
        # beyond the refusal its own offer carries.
        add=None,
        proposal=None,
    )


def parked_card(
    review: "ReviewSet", parked: "BarredLine",
) -> LineCard:
    """Return the holding card for a line the bank files as an account payment.

    Ruling **bank_import:R-HQ**: money the source says went to another account
    the owner holds is a STATE rather than a task, so it sits on the Transfers
    tab in one unnamed section with no act to press.

    **It built the *never a purchase* card too, until plan step
    ``bank_import:X-gj-4c``** -- it branched on
    :attr:`~._bars.BarredLine.also_pays_an_account` for its verb and its
    sentence.  Ruling **bank_import:R-JH** ended that: such a line is not
    disposed of, it is unexplained work with one door shut, so it is a
    different card on a different tab (:func:`answered_never_card`) and the
    branch is gone rather than being made to answer harder.  Which of the two
    a line is is the PASS's partition now
    (:attr:`~._reads.ReviewSet.parked`), so this function has no arm that
    could answer wrongly.

    Args:
        review: The pass.
        parked: The :class:`~._bars.BarredLine`, which must be one a source
            files as paying an account the owner holds.

    Returns:
        The :class:`LineCard`.  Its ADD is shut by the bar, its TRANSFER and
        SKIP by having no door at all, so :attr:`LineCard.offers_ok` is False
        and no tab renders it a button.
    """
    return LineCard(
        line=parked.line,
        section=None,
        suggested=Verb.TRANSFER,
        sentence=for_parked_transfer(parked),
        already_held=None,
        risk_class=None,
        panel=_barred_panel(review, parked),
    )


def answered_never_card(
    review: "ReviewSet", barred: "BarredLine",
) -> LineCard:
    """Return the INBOX card for a line the owner answered *never a purchase*.

    Ruling **bank_import:R-JH**, plan step ``bank_import:X-gj-4c``.  **That
    answer bars the create door and stops there.**  *Not a purchase* is not
    *explained by nothing* -- a paycheck is neither and a transfer to savings
    is neither -- so the line is not disposed of, it is an unexplained line
    whose ADD verb is shut.  It reads :func:`~._sentence.choose` under
    *Nothing suggested*, keeps MATCH, and carries the reason and the door that
    changes the answer in its panel.

    **It was a past-tense card on a SKIPPED tab until this step**, composed by
    a ``_sentence.for_parked_never`` that opened on ``Skipped`` -- which stated
    a disposition the owner never made.  That sentence is DELETED rather than
    reworded: the card it belonged to no longer exists, and the developer's
    ruling is what makes ``CLAUDE.md`` rule 5's exception apply here.

    **A card built by this function has an available act WHEREVER THE PASS
    OFFERS A ROW, which is what keeps ruling R-HQ from being breached by
    putting it in the inbox** -- and the bound in that sentence is real rather
    than defensive.  MATCH is shut for EVERY line when the pass offers no
    unexplained row at all (:data:`~._verbs.MATCH_SHUT_NO_ROWS`).  **Plan
    step ``bank_import:X-gj-4b`` CLOSED the hole this paragraph described**:
    SKIP was shut on every line until that step, so a pass offering no row
    left this card in the inbox with NO open verb and
    :attr:`~._reconcile.ReconcilePage.is_done` unable to reach ``True``.  SKIP
    is open for these lines now -- ruling **bank_import:R-JI** shuts it only
    for a merchant a source files as paying an account the owner holds, which
    is never true of a line barred by the owner's own answer alone -- so the
    card always has an act.  *This was written in the FUTURE tense about the
    step that has now shipped.*  The same state is still reachable elsewhere
    and is not this class's: a :class:`~._leftovers.CreatableLine` whose pay
    period no calendar covers is in the inbox on the same terms
    (:func:`~._leftovers._one_creatable` gives it no destinations, no
    placement and a ``withheld`` refusal).  *An earlier draft of this paragraph opened
    "always has an available act" and then stated the bound that contradicts
    it; adversarial review 2026-09-03 named the contradiction.*

    Args:
        review: The pass.
        barred: The :class:`~._bars.BarredLine`, which must be one barred
            ONLY by the owner's own answer -- the membership rule of
            :attr:`~._reads.ReviewSet.answered_never`.

    Returns:
        The :class:`LineCard`.  :attr:`LineCard.offers_ok` is False because
        nothing here justifies a verb, and :attr:`LineCard.takes_ok` is True
        wherever MATCH is open, which is what puts the consent checkbox in the
        document for the act this card does have.
    """
    return LineCard(
        line=barred.line,
        section=Section.NOTHING,
        # **NO suggestion, which is what "nothing suggested" means** (ruling
        # **R-HS**): the pass has not worked out what this line is, and the
        # owner's answer said only what it is not.  A card suggesting SKIP
        # here would be the app deciding a disposition on the strength of an
        # answer that claims nothing about the line.
        suggested=None,
        sentence=choose(),
        already_held=None,
        risk_class=None,
        panel=_barred_panel(review, barred),
    )
