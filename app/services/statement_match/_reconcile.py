"""What the RECONCILE screen shows: one card per bank line, on four verbs.

Plan step ``bank_import:X-gj-1a``; rulings **bank_import:R-HP**, **R-HQ**,
**R-HR**, **R-HS**, **R-HW** and **R-HX**.  The direction this builds to was
locked at Loop A round 4 on 2026-08-29 and is
``docs/design/bank_import_audit.md``.

**One page replaces three.**  The review queue, the register and the
hand-build workbench become five TABS over one list -- To explain, Explained,
Filed by rules, Transfers, Skipped -- with a hero that answers *am I done* and
holding chips for the lines that are not work.  This module is the whole of
what that page displays; the template displays it and computes nothing.

**Every tab is its own request, and that is a measurement rather than a
style.**  Ruling **R-GX** split the register off the review screen because
rendering both valued all 221 of the developer's accepted acts to draw a panel
he was not reading -- 442,109 bytes of a 578,523-byte page.  Folding them back
into one render would rebuild exactly that, so :func:`reconcile_page` builds
the cards of ONE tab and takes the other four's counts from
:func:`~._accepted_view.accepted_counts`, :func:`~._skipping.skipped_count`
and the pass itself.

**What each tab holds, and where its cards come from:**

===================  ==========================================================
tab                  source
===================  ==========================================================
To explain           the pass: :attr:`~._reads.ReviewSet.proposals`,
                     ``creatable``, ``recordable_inflows`` and
                     ``answered_never``, in three sections by what SUGGESTED
                     the verb (**R-HP**)
Explained            :func:`~._accepted_view.accepted_register`, acts a person
                     ticked
Filed by rules       :func:`~._accepted_view.accepted_register` narrowed to
                     the acts a standing rule performed (**R-GT**)
Transfers            the pass's ``parked`` lines, which a source files as
                     paying an account the owner holds -- a HOLDING state
                     (**R-HQ**), never inbox work
Skipped              :func:`~._skipping.skipped_acts` over
                     ``budget.statement_line_skips`` -- the owner's own
                     decision that a line explains nothing (**R-JG**,
                     **R-JH**), bounded and linked past like the settled tabs
                     (**R-GX**)
===================  ==========================================================

**THE SKIPPED TAB IS THE RESULT'S HOME, AND IT LANDS BEFORE THE CONTROL THAT
FILLS IT** (plan step ``bank_import:X-gj-4c-2``), which is the order rulings
**R-GY** and **R-HU** put those two in.  Its history is worth one paragraph
because two of its states were wrong in different ways.  It first listed the
pass's barred lines the owner had answered *never a purchase* for, on the
argument that the standing answer WAS the disposition; ruling
**bank_import:R-JH** refuted that -- *not a purchase* is not *explained by
nothing* -- and ``X-gj-4c-1`` returned those lines to the inbox, which left
the tab holding a hard-coded empty tuple.  It reads the store now, and **plan step
``bank_import:X-gj-4b`` gave that store its first writer**:
:func:`~._batch._apply_skips` calls :func:`~._skipping.skip_line`, so the
count is whatever the owner has skipped rather than 0 on every account.
*This said the count was still 0 until that step lit the verb, and the step
has shipped.*  The point it was making survives unchanged: what the tab shows
is a fact about the DATA rather than about this module, and whatever a door
records, this tab renders.

**A line with no available act never enters To explain** (**R-HQ**).  That is
what makes the inbox reach zero: measured 2026-08-29 on the developer's own
account, 9 of the 27 unexplained lines are card payments no screen in the app
can resolve, and a queue that cannot empty is not a queue.  **A line the owner
answered *never a purchase* for HAS one** -- it keeps MATCH, and since plan
step ``bank_import:X-gj-4b`` it HAS SKIP, which ruling **bank_import:R-JI**
shuts only for a merchant a source files as paying an account the owner holds
and so never for this class -- which is why **R-JH** puts it back in the inbox
without breaching this.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.  Every read it
performs takes the owner and account the route proved, exactly as
:func:`~._accepted_view.accepted_register` does.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from ._accepted_view import REGISTER_LIMIT, accepted_counts, accepted_register
from ._card_sections import (
    act_sections,
    skip_sections,
    to_explain_sections,
)
from ._cards import CardKind, CardSection, LineCard, parked_card
from ._last_import import last_import
from ._opened import (
    MatchAsk,
    MatchReach,
    opened_match,
    proposed_submission,
)
from ._reads import review_set
from ._skipping import skipped_acts, skipped_count

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from datetime import date

    from ._gaps import BooksBound

    from app.services.bank_agreement import BankAgreement

    from ._last_import import LastImport
    from ._opened import OpenedAsk, OpenedMatch
    from ._reads import ReviewSet
    from ._scope import ReviewScope
    from ._subjects import HeldElsewhere


class Tab(enum.Enum):
    """One of the five lists the Reconcile page holds.

    **The order of the members is the order the tab bar renders them**, which
    is the order the work goes in: what is still to do, then what was done,
    then the two holding states.
    """

    TO_EXPLAIN = "to_explain"
    EXPLAINED = "explained"
    FILED_BY_RULES = "filed_by_rules"
    TRANSFERS = "transfers"
    SKIPPED = "skipped"

    @property
    def label(self) -> str:
        """Return what the tab bar calls this tab.

        Returns:
            The label, server-derived so the tab bar and any sentence naming a
            tab cannot drift apart.
        """
        return _TAB_LABELS[self]


#: What each tab is called.  A table rather than a property full of branches,
#: for the reason :data:`~._verbs._WORDS` is one.
_TAB_LABELS: "dict[Tab, str]" = {
    Tab.TO_EXPLAIN: "To explain",
    Tab.EXPLAINED: "Explained",
    Tab.FILED_BY_RULES: "Filed by rules",
    Tab.TRANSFERS: "Transfers",
    Tab.SKIPPED: "Skipped",
}


#: The sweep classes in RISK ORDER, each with the phrase that names what one
#: click would do.  Ruling **R-FZ(c)**: *the riskiest class may not ride the
#: same click as the safest*, so there is one control per class and never a
#: "tick all".
#:
#: **It is TWO partitions concatenated, and both are already server-derived**
#: -- :attr:`~._offers.MatchProposal.review_class` over the proposals and
#: :attr:`~._placement.Placement.sweep_class` over the spending a rule places.
#: They are listed here in one sequence because the footer offers one row of
#: controls, and the order is each partition's own: ascending by how much
#: accepting changes, day effects before amount effects, and creating an
#: envelope last because it is the one act an undo cannot fully reverse.
#:
#: **The review screen counted these in JINJA** (its body's
#: ``selectattr | length``) until plan step ``bank_import:X-gi-2`` deleted it,
#: which is the shape
#: the retired queue's ``_sweeps_for`` existed to refuse: a caption may not
#: promise a number a template counted.  This page counts them in the service;
#: the second copy of the LABELS, ``_SWEEP_LABELS``, outlived its only reader
#: by that deletion and WENT at ``bank_import:X-gi-3`` with the module holding
#: it -- the two had already drifted by a word on arrival.
SWEEP_LABELS: "tuple[tuple[str, str], ...]" = (
    ("confirm", "that only confirm a day you already had"),
    ("correct", "that move a day onto the bank's"),
    ("settle", "that mark a row as having happened"),
    ("reprice", "that change an amount onto the bank's"),
    ("into_open", "into a budget line that is still open"),
    ("into_closed", "into one that has already closed, raising what it recorded"),
    ("creates", "into a NEW envelope this would create"),
)


@dataclass(frozen=True)
class Hero:
    """The four figures that answer *am I done*.

    Ruling: the number is the hero (the design language's first principle).
    Done is ``off_by`` at zero and ``to_explain`` at zero, and the page says
    exactly that under them.

    Attributes:
        day: The day all three figures are about -- the latest day BOTH
            records state a balance for, the books' opening day included
            (:attr:`~app.services.bank_agreement.BankAgreement.headline`,
            ruling **R-BAL286**) -- or ``None`` exactly when no drawn day
            carries both, e.g. no anchored import, or a bank balance placed
            only before the books open, after NOW or past the two-year draw.
        bank: What the bank's own record says the account held that day, or
            ``None`` with *day*.
        books: What the app's records say it held that day, or ``None`` with
            *day*.
        off_by: The books LESS the bank, or ``None`` with *day*.  **Signed in
            that direction**, which is
            :attr:`~app.services.bank_agreement.AgreementDay.gap`'s own
            convention: positive means the books stand higher.
        to_explain: How many lines the inbox holds.
        unpriced_after: How many drawn days after :attr:`day` the books state
            a balance for and the bank's own record cannot price -- a later
            import placing no balance, or the gap before one.  **Zero is what
            makes the hero current**, and the other four do not imply it, so
            :attr:`ReconcilePage.is_done` refuses done over it and the page
            says *priced through <day>*.  Drawn days end at the last statement
            line, so a record AFTER every statement is not counted: statements
            stopping three weeks before the records read zero and can be done
            (finding **BAL-651**).  Named by adversarial review 2026-08-29.

    **The three money figures are all-or-nothing**, and that is the point of
    carrying the day beside them: a hero that printed two of three would be
    inviting a subtraction the app could not perform.
    """

    day: "date | None"
    bank: "Decimal | None"
    books: "Decimal | None"
    off_by: "Decimal | None"
    to_explain: int
    unpriced_after: int


@dataclass(frozen=True)
class HoldingChip:
    """One count of lines that are NOT work, in the hero's quiet row.

    Ruling **bank_import:R-HQ**.  A card payment with no card account to pair
    with, and a line older than the pay calendar, are states rather than
    tasks; the chip says how many and how much, and the tab that owns them is
    one click away.

    Attributes:
        label: What these lines are, in the reader's words.
        count: How many.
        amount: What they come to as a MAGNITUDE, or ``None`` where a sum
            says nothing.  **Positive, and the label supplies the direction**:
            every parked line is an outflow and
            :attr:`~._offers.BankLine.amount` is signed positive INTO the
            account, so carrying the raw sum would print `-$7,412.94` under
            the words *waiting for the account they paid* --
            :attr:`~app.services.bank_agreement.BankAgreement.bank_ahead` was
            corrected for exactly that, one module over, after two reviews
            found `-$15,028.03` on screen under *ran ahead*.
        day: The day that bounds them, or ``None``.
        tab: The tab that lists them (:class:`Tab`), or ``None`` where none
            does -- which is a chip that states a fact rather than offering a
            way in.

    **A chip renders only when its count is non-zero**, which is why the
    builder omits it rather than emitting a zero: a row of zeroes is the
    *nothing to see here* panel this rebuild removed.
    """

    label: str
    count: int
    amount: "Decimal | None"
    day: "date | None"
    tab: "Tab | None"


@dataclass(frozen=True)
class Sweep:
    """One risk class of cards, and the one click that OKs them.

    Ruling **R-FZ(c)**, and the same value the retired queue's ``QueueSweep``
    was, over this screen's two partitions.

    Attributes:
        css_class: The class key, which is the value a card carries in
            :attr:`LineCard.sweep_class`.
        count: How many cards on THIS tab the click would set.
        label: The phrase naming what it does to them.
    """

    css_class: str
    count: int
    label: str


@dataclass(frozen=True)
class TabCount:
    """One tab's name and how many cards it holds.

    Attributes:
        tab: Which tab (:class:`Tab`).
        count: Its size.
    """

    tab: Tab
    count: int


@dataclass(frozen=True)
class ReconcilePage:  # pylint: disable=too-many-instance-attributes
    """Everything the Reconcile page renders, for ONE of its five tabs.

    Pylint: ``too-many-instance-attributes`` (12/7) -- **twelve because the
    page renders twelve distinct things**: which tab is open, WHICH KIND of
    card it holds, the hero, what the last import did, the holding chips, the
    tab bar, the cards, the sweeps, the footer's disclosure, what the
    account's opening already accounts for, the ONE card a scriptless
    request asked to open, and the rows planned here whose payment is
    recorded on another account.
    Every one of them is read by ``_statement_reconcile_body.html``, so the
    count is re-derivable rather than asserted; folding any pair would be the
    speculative nesting ``CLAUDE.md`` rule 13 forbids, and
    :class:`~._reads.ReviewSet` carries the same disable for the same reason.
    *It read (8/7) until plan step balance:X-f3c-2b-2b added the ninth, (9/7)
    until ``bank_import:X-gj-4c-2`` added the tenth, and (10/7) until
    ``bank_import:X-gi-1`` added the eleventh, and (11/7) until plan step
    ``credit_card:CC-5-4a-5`` (leaf 5c-2a) added the twelfth* -- a count in a
    rationale is a measurement, and the first of those went stale in the
    same commit that made it stale.

    **It carried an ``account_id`` until plan step ``bank_import:X-gj-1b``,
    and NOTHING read it** -- not a template, not the route, not a test.  Every
    URL on the page is built from the ``account`` object the route passes
    beside this one, and the account this page is ABOUT is already
    :attr:`~._scope.ReviewScope.account_id`, which
    :func:`reconcile_page` checks the agreement against.  A field written on
    every render and read on none is the shape this package keeps deleting;
    it went rather than being re-documented.  Found by adversarial design
    review 2026-08-30.

    Attributes:
        tab: Which tab's cards :attr:`sections` holds (:class:`Tab`).
        kind: Which KIND of card those sections hold
            (:class:`~._cards.CardKind`) -- the ONE fact a template needs in
            order to pick a partial, and it is set by the arm that BUILT the
            cards (:func:`_tab_sections`) rather than looked up from a table
            beside it.  **That placement is the whole of it** (developer
            ruling **R-JX**): a ``Tab.holds`` property over a table was one
            fact with two homes, agreeing only because a test said so, and a
            table saying ACT over a dispatch building skips rendered the wrong
            partial and 500'd on a field that is not there.  Here the two
            cannot part.  **Stated even for a tab with no cards**, which is why
            it is a page fact and not a section one: an empty inbox still
            renders the Apply form and an empty Skipped tab still must not.
        hero: The four figures that answer *am I done* (:class:`Hero`).
        last_import: What the newest import on this account did
            (:class:`~._last_import.LastImport`), or ``None`` for an account
            nobody has imported into.  **Provenance rather than a hero
            figure**: the locked direction prints it right of the four
            numbers, and it answers *is what I am looking at current* rather
            than *am I done*.
        chips: The holding counts (:class:`HoldingChip`), non-zero only.
        counts: Every tab and its size (:class:`TabCount`), in tab order, so
            the bar is drawn whichever tab is open.
        sections: The open tab's cards (:class:`CardSection`), non-empty only.
        sweeps: The one-click controls this tab offers (:class:`Sweep`), which
            is empty for every tab but To explain.
        unexamined: What this pass did NOT look at, one sentence each, for the
            footer's disclosure.  Empty when it examined everything.
        books_bound: The lines this account's opening equity already
            accounts for
            (:class:`~._gaps.BooksBound`), or ``None`` where it accounts
            for none.  **Named ``books_bound`` and not ``books``**, because
            :attr:`Hero.books` on the same page is the app's own BALANCE and
            one page carrying two unrelated ``books`` is a misreading waiting
            to happen.  **Carried whole rather than flattened into**
            :attr:`unexamined`, because unlike every sentence in that tuple
            this one ends in an ACT, and the template has to render that act
            as a link -- which is the one fact a service may not build
            (:attr:`~._bars.BarredLine.answer_door`).
        opened: The MATCH pane of the ONE card ``?open=<line_id>`` named
            (:class:`~._opened.OpenedMatch`), or ``None`` -- which is every
            render whose URL carries no ``open``, and every render that holds
            no card for the line it names.  **NOT "every render but a
            scriptless one"**, which is what this said until adversarial review
            2026-09-05: a scripted browser reaches ``?open=`` through a shared
            or bookmarked URL, and every Apply POST from an opened card carries
            it in the form's action.  Ruling
            **bank_import:R-KA**: the pane is a fragment htmx fetches, so with
            scripting off it never arrives at all, and this is what puts the
            rows in the document instead.  **Derived for ONE card and never
            for the tab**, which is the whole of the ruling's cost argument:
            :class:`~._panel.VerbPanel` carried a ``MatchTab`` holding every
            card's rows until plan step ``bank_import:X-gj-1b`` deleted it for
            deriving 248 cards' worth of a value nobody read.
        held_elsewhere: The rows this screen withholds because their payment
            is recorded on another account (ruling **R-CC137**), each saying
            its own sentence (:attr:`~._subjects.HeldElsewhere.said`).  Not
            lines of :attr:`unexamined`: these were looked at and withheld.
    """

    tab: Tab
    kind: CardKind
    hero: Hero
    last_import: "LastImport | None"
    chips: "tuple[HoldingChip, ...]"
    counts: "tuple[TabCount, ...]"
    sections: "tuple[CardSection, ...]"
    sweeps: "tuple[Sweep, ...]"
    unexamined: "tuple[str, ...]"
    books_bound: "BooksBound | None"
    opened: "OpenedMatch | None"
    held_elsewhere: "tuple[HeldElsewhere, ...]"

    @property
    def is_done(self) -> bool:
        """Return whether there is nothing left to do on this account.

        Returns:
            ``True`` when the books and the bank agree on the hero's day, that
            day is the last one both records reach, and no line is waiting to
            be explained.  **All three, and a page with no priceable day is
            never done**: an account whose bank balance nothing places has not
            been shown to agree, and one whose priced days stop short of its
            drawn ones has agreed about a week that is not this one.
            Reporting either unknown as agreement is the failure this whole
            arc exists to stop.
        """
        return (
            self.hero.off_by == 0
            and self.hero.unpriced_after == 0
            and self.hero.to_explain == 0
        )


def _sweeps(sections: "tuple[CardSection, ...]") -> "tuple[Sweep, ...]":
    """Return the one-click controls this tab's cards support.

    **Counted over the cards that will actually be rendered**, so a caption
    cannot promise a number the control does not deliver -- which is the rule
    the retired queue's ``_sweeps_for`` stated and the review screen broke by
    counting in Jinja.

    Args:
        sections: The tab's sections.

    Returns:
        One :class:`Sweep` per class with at least one card, in
        :data:`SWEEP_LABELS`' risk order.
    """
    counts: "dict[str, int]" = {}
    for section in sections:
        for card in section.cards:
            css_class = card.sweep_class
            if css_class is not None:
                counts[css_class] = counts.get(css_class, 0) + 1
    return tuple(
        Sweep(css_class=key, count=counts[key], label=label)
        for key, label in SWEEP_LABELS if key in counts
    )


def _hero(agreement: "BankAgreement | None", to_explain: int) -> Hero:
    """Return the four figures that answer *am I done*.

    Args:
        agreement: The account's :class:`~app.services.bank_agreement
            .BankAgreement`, or ``None`` for an account holding no recorded
            line.
        to_explain: How many lines the inbox holds.

    Returns:
        The :class:`Hero`.  Its three money figures are all present or all
        absent, because they are one comparison on one day: an account with no
        anchored import has a bank side nothing places, and printing two of
        three would invite a subtraction the app cannot perform.
    """
    day = None if agreement is None else agreement.headline
    if day is None:
        return Hero(
            day=None, bank=None, books=None, off_by=None,
            to_explain=to_explain, unpriced_after=0,
        )
    return Hero(
        day=day.day,
        bank=day.bank_balance,
        books=day.app_balance,
        off_by=day.gap,
        to_explain=to_explain,
        # Each later day has a books balance (the hero's is on or after the
        # opening) and no bank one (the hero is the LATEST carrying both).
        unpriced_after=sum(1 for other in agreement.days if other.day > day.day),
    )


def _chips(
    review: "ReviewSet",
    transfers: "tuple[LineCard, ...]",
) -> "tuple[HoldingChip, ...]":
    """Return the quiet counts under the hero.

    Ruling **bank_import:R-HQ**: what is not work is a count, never a queue
    row.  **A chip whose count is zero is omitted**, so the row says something
    or is not there.

    **There was a third chip, *already explained*, and plan step
    ``bank_import:X-gj-1c`` DELETED it.**  It carried
    :attr:`~._accepted_view.AcceptedCounts.total` and led to the register,
    which renders every accepted act; once the two settled TABS exist that
    total is the union of two tabs, so the chip would have promised a number
    neither of the tabs it could link to delivers -- the caption-over-a-count
    defect the retired queue's ``_sweeps_for`` existed to refuse.  The tab bar states
    both halves with their own counts, which is the same fact said once per
    place it is true rather than twice.

    **The books bound is NOT a chip either**, and it was one until an
    adversarial design review measured what that cost (2026-08-31): the chip
    carried the count, the day and a link, and the sentence under it repeated
    all three three lines later.  That is the clutter :func:`_unexamined` one
    function down says this rebuild removed.  It renders as
    :attr:`ReconcilePage.books_bound` instead -- once, with the act as a link,
    the way the review body and the workbench rendered the same value before
    plan step ``bank_import:X-gi-2`` deleted them.

    **The two paragraphs above removed DIFFERENT chips and both removals
    stand** (merge of ``balance:X-f3c-2b-2b`` into ``bank_import:X-gj-1c``,
    2026-08-31).  The merge is worth recording because the textual resolution
    that compiles is the WRONG one: this function's parameter list is the
    bank_import side's two, and the balance side calls it with a third,
    ``explained``, feeding the very chip R-HU deleted.  Taking that call site
    whole would have restored a chip pointing at a retired page, and taking the
    signature whole would have been a ``TypeError`` -- one of those fails
    loudly and the other does not.

    Args:
        review: The pass, which owns the pay-calendar and books bounds.
        transfers: The cards on the Transfers tab.

    Returns:
        The chips, in reading order.  **Every one of them names a tab this
        page serves or names no tab at all**, which is what lets the route
        turn :attr:`HoldingChip.tab` into a URL without an arm for a tab it
        cannot render.
    """
    chips = []
    if transfers:
        chips.append(HoldingChip(
            label="waiting for the account they paid",
            count=len(transfers),
            amount=sum(
                (abs(card.line.amount) for card in transfers),
                Decimal("0.00"),
            ),
            day=None,
            tab=Tab.TRANSFERS,
        ))
    bounds = review.bounds
    if bounds.before_calendar_count:
        chips.append(HoldingChip(
            label="before your pay calendar opens",
            count=bounds.before_calendar_count,
            amount=None,
            day=bounds.before_calendar_last_day,
            tab=None,
        ))
    return tuple(chips)


def _unexamined(review: "ReviewSet") -> "tuple[str, ...]":
    """Return what this pass did NOT look at, one sentence each.

    **Two of the four bounds, and the partition is decided HERE.**  The
    other two are stated elsewhere on the page and stating either twice is
    the clutter this rebuild removed:
    :attr:`~._gaps.ReviewBounds.before_calendar_count` is a
    :class:`HoldingChip` above the fold, and
    :attr:`~._gaps.ReviewBounds.books` is
    :attr:`ReconcilePage.books_bound`, which carries an ACT and so cannot
    be one of these plain sentences.  A template picking two of four
    would be the *second place for a partition to be wrong* this package
    refuses everywhere else.

    **It said THREE of FIVE until plan step ``bank_import:X-gm``**, which
    deleted ``ReviewBounds.impossible_day_count`` rather than reworded its
    sentence here: the lines it counted are inbox cards now, each carrying its
    own refusal, so a sentence in this panel would be announcing work the page
    is already showing.  :class:`~._gaps.ReviewBounds` carries the argument.

    Args:
        review: The pass.

    Returns:
        The sentences, or empty when this pass examined everything -- which is
        the state that needs no disclosure at all.
    """
    bounds = review.bounds
    said = []
    if bounds.crowded_days:
        said.append(
            f"The app did not search {len(bounds.crowded_days)} day(s) for "
            f"groups, because too many of your rows fall on them: "
            f"{', '.join(str(day) for day in bounds.crowded_days)}."
        )
    if bounds.unpriceable_count:
        said.append(
            f"{bounds.unpriceable_count} of your rows could not be priced, "
            f"so nothing here was offered against them."
        )
    return tuple(said)


def _opened_pane(
    scope: "ReviewScope",
    review: "ReviewSet",
    sections: "tuple[CardSection, ...]",
    opened: "OpenedAsk | None",
) -> "OpenedMatch | None":
    """Return the MATCH pane of the one card ``?open=`` named, or ``None``.

    Plan step ``bank_import:X-gi-1``, ruling **bank_import:R-KA**.  **Priced
    from what the request's form holds for the card** since plan step
    ``bank_import:X-gi-2a`` (finding **BI-478**): it took the line id alone
    and priced every render from the proposal, so a refused Apply came back
    with the owner's ticks gone.

    **It reaches EVERY unexplained row and not the line's own pay period**
    (developer, 2026-09-05).  The period is what the live fragment opens on,
    because the SEARCH widens it and 67 rows a keystroke is what finding
    **N-374** is about; this render has no search at all, so the period would
    be a bound the owner cannot widen -- which is the cap **N-374** refused --
    and :mod:`._panel` measures the class it would strand: all 9 of the 9 card
    payments on the developer's own Checking have payback rows their own
    period does NOT hold (2026-08-30), and a hand-built group against those
    paybacks is the ONLY act ruling **R-GJ** leaves such a line.

    **Nothing is derived unless THIS RENDER holds that card**, and the test is
    membership of the sections actually built rather than the tab's KIND
    (adversarial review 2026-09-05).  ``card_subject`` searches the whole pass,
    so asking it alone answered a live pane for
    ``?tab=to_explain&open=<a parked card payment>`` -- two database reads and
    a full ``preview_hand_build`` for a value the template then rendered
    nowhere, because the card is on Transfers.  A settled tab, a bounded-out
    act and a card on another tab are now one answer and one absence.

    Args:
        scope: The pass's scope, which prices the pane.
        review: The pass, which resolves the line to its card.
        sections: The cards this render actually holds
            (:class:`~._cards.CardSection`).
        opened: Which card was asked for and what the form holds for it
            (:class:`~._opened.OpenedAsk`), or ``None``.

    Returns:
        The :class:`~._opened.OpenedMatch`, or ``None`` where nothing was
        asked for, or where this render holds no card for the line named --
        which is a stale ``?open=`` rather than an error.
    """
    if opened is None:
        return None
    if not any(
        getattr(card, "line", None) is not None
        and card.line.line_id == opened.line_id
        for section in sections for card in section.cards
    ):
        return None
    subject = review.card_subject(opened.line_id)
    if subject is None:
        return None
    return opened_match(
        scope, review,
        MatchAsk(
            subject=subject,
            # **The form's own rows where the request carried a form, else
            # the proposal the card already offers** -- which is what the
            # unopened card's hidden fields carry: a first render priced at
            # `$0.00` over a proposal stating a correction would report a
            # different act from the one the card is offering.
            submitted=(
                proposed_submission(subject) if opened.submitted is None
                else opened.submitted
            ),
            query="",
            reach=MatchReach.EVERY_ROW,
        ),
    )


def reconcile_page(
    scope: "ReviewScope",
    agreement: "BankAgreement | None",
    tab: Tab,
    limit: "int | None" = REGISTER_LIMIT,
    opened: "OpenedAsk | None" = None,
) -> ReconcilePage:
    """Return everything the Reconcile page renders, for ONE of its tabs.

    Plan step ``bank_import:X-gj-1a``.  The whole of what the screen displays,
    so the template displays it and computes nothing -- which is the hard
    constraint the design language states and the review screen broke by
    counting its own sweeps in Jinja.

    **The pass is derived whichever tab is open**, and that is not waste: the
    hero's *to explain*, all three holding chips and three of the five tab
    counts are facts about it.  Measured on the developer's own account
    2026-08-29: ``review_set`` 0.136 s, ``accepted_counts`` one aggregate
    query, ``bank_agreement`` 0.108 s.

    **A bounded tab's CARDS are built only when it is open.**  Their
    counts come from :func:`~._accepted_view.accepted_counts`, which is three
    aggregates over one indexed read; building them would value EVERY act on
    the account -- :data:`~._accepted_view.REGISTER_LIMIT` bounds what is
    rendered and not what is priced -- re-deriving each one's removals and
    re-pricing its member rows, which is the cost ruling **R-GX** split the
    register off the review screen to stop paying.

    Args:
        scope: The pass to build from
            (:class:`~._scope.ReviewScope`).  **The route builds it**, which
            is the rule every read pass in this project is held to; a producer
            below the route takes one and never derives its own.
        agreement: The account's :class:`~app.services.bank_agreement
            .BankAgreement`, or ``None`` when it holds no recorded line.
            Passed in for the same reason: it needs a
            :class:`~app.services.balance_at.BalanceContext`, and only a route
            builds one of those either.
        tab: Which tab's cards to build (:class:`Tab`).
        limit: How many rows a BOUNDED tab may render, or ``None`` for the
            whole record -- which is what that tab's own *show the other N*
            link asks for (plan steps ``bank_import:X-gj-1c`` and
            ``X-gj-4c-2``).  Three tabs read it: the two settled ones and
            Skipped.  *It was "how many SETTLED acts" until the developer
            ruled the Skipped tab bounded at **R-JW**.*  It defaults to
            :data:`~._accepted_view.REGISTER_LIMIT`, and an act that NO LONGER
            HOLDS is never subject to it whatever this says: which acts the
            bound may reach is
            :func:`~._accepted_view.accepted_register`'s rule and is not
            restated here.  **The link exists because this page retires the
            register** (**R-HU**): the register offered exactly this, and on
            the developer's own account it reaches 171 of his 221 acts, so
            dropping it would put them out of reach rather than merely
            unlisted.
        opened: The bank line whose MATCH pane renders IN the document
            rather than behind a fetch, with what the request's form holds
            for it (:class:`~._opened.OpenedAsk`), or ``None``.  Ruling
            **bank_import:R-KA**, plan step ``bank_import:X-gi-1``: the pane is
            htmx's, so with scripting off it never arrives, and
            ``?open=<line_id>`` is the path that puts one card's rows on the
            page.  **ONE card, and only where this tab holds bank lines**:
            deriving it for a tab of settled acts would price a pane no card
            here could render, and deriving it for every card is the 1.2 MB
            plan step ``bank_import:X-gj-1b`` measured out.  **It was the line
            id alone until plan step ``bank_import:X-gi-2a``**, and the route
            builds the ask now because only the route knows whether its form
            carries the card.

            A line this pass renders no card for yields ``None`` rather than
            an error, because that is what a STALE ``?open=`` is: the owner
            applied the card and the door answered with the query string it
            was pressed under.

    Returns:
        The :class:`ReconcilePage`.

    Raises:
        ValueError: When *scope* and *agreement* name different accounts.
            **Two arguments a caller pairs by hand are two chances to pair one
            act's account with another's**, which is the rule
            :func:`~._accept._record` already states -- and here the
            consequence is one account's hero over another's lines, with no
            figure on the page wrong enough to look wrong.
    """
    if agreement is not None and agreement.account_id != scope.account_id:
        raise ValueError(
            f"The pass is for account {scope.account_id} and the agreement "
            f"for account {agreement.account_id}; one page cannot show one "
            f"account's lines beside another's balances."
        )
    review = review_set(scope)
    # **THE PAGE DERIVES NO CANDIDATE ROWS**, and it did until plan step
    # ``bank_import:X-gj-1b``.  It built one
    # :class:`~._panel.MatchCandidates` index and handed every card its own
    # line's pay period; the cards then rendered none of it, because the
    # panel's row list is lazy-loaded and the fragment asks for itself.  So
    # the index was derived over 248 cards and 11 periods for a value nobody
    # read.  ``accounts.statement_reconcile_match`` builds it now, once per
    # opened tab, which is where it is looked at.
    # **The Transfers tab's cards, as a plain tuple.**  This was a MAPPING of
    # tab to cards while two tabs were holding states, justified as "a holding
    # state added later adds a key here and no signature grows" -- and plan
    # step ``bank_import:X-gj-4c-2`` left it holding exactly one key, at which
    # point that sentence was flexibility for a caller that does not exist
    # (``CLAUDE.md`` rule 13).  Adversarial review named it; the dict is gone
    # rather than re-justified, and :func:`_tab_sections` takes the tuple.
    #
    # **WHICH TAB A BARRED LINE IS ON IS THE PASS'S ANSWER, NOT THIS
    # FUNCTION'S** (ruling **bank_import:R-JH**, plan step
    # ``bank_import:X-gj-4c-1``).  A private ``_parked_tab`` read one ``parked``
    # list and answered *Transfers or Skipped*; the second answer was wrong,
    # and moving the split into :func:`~._leftovers._creatable_lines` is what
    # makes it unrepresentable here rather than merely unreachable -- the lines
    # it used to send to Skipped are not in ``parked`` at all, so they cannot
    # be counted onto the Transfers chip, whose label carries a MAGNITUDE.
    #
    # **``Tab.SKIPPED`` IS NO LONGER IN THIS MAPPING**, and its absence is the
    # whole of plan step ``bank_import:X-gj-4c-2``: it held a hard-coded empty
    # tuple, because no reader existed over ``budget.statement_line_skips``.
    # The hazard that carried -- ``X-gj-4b`` lighting the verb while the tab
    # could show nothing, so a skipped line left EVERY surface with no way back
    # -- is now unrepresentable rather than held off by a blocker in
    # the plan: the tab reads the store
    # (:func:`~._skipping.skipped_acts`), so whatever a door records, the tab
    # renders.  Named by adversarial design review 2026-09-03 and closed here.
    transfers = tuple(parked_card(review, one) for one in review.parked)
    counts = accepted_counts(scope.owner_id, scope.account_id)
    # **A COUNT and not a list**, on every render whichever tab is open, which
    # is the rule the two settled tabs' counts already keep: this page builds
    # the cards of ONE tab.  **The two reads here share one SNAPSHOT** -- a
    # query request runs at ``REPEATABLE READ`` (:mod:`app.db_transaction`) --
    # so this figure and the open tab's own total cannot see different sets;
    # :func:`~._skipping.skipped_count` records both legs of that.
    skipped = skipped_count(scope.owner_id, scope.account_id)
    # **THE COUNT IS THE CARDS** (plan step ``bank_import:X-gm``).  It was a
    # SUM over four of the pass's lists, written here, and that shape has
    # failed both ways it can: plan step ``bank_import:X-gj-4c`` added a fifth
    # list and had to remember to add a term, and the lines the bank dates
    # impossibly never got a list at all, so they fell out of the sum AND off
    # every tab with nothing able to notice.  Counting what
    # :func:`~._card_sections.to_explain_sections` BUILT means a class that
    # reaches a card is counted and one that does not is visibly missing from
    # the tab rather than silently missing from a figure.
    #
    # **Built on every render whichever tab is open**, which the two settled
    # tabs' counts do NOT do -- and the asymmetry is the honest one: those
    # counts are database aggregates and this is arithmetic over a pass that is
    # already in hand.  :mod:`._card_sections` performs no query.
    #
    # **THIS COUNTS CARDS AND THE BADGE COUNTS LINES, and they agree because
    # every proposal names exactly ONE line.**
    # :func:`~._propose._one_to_one`, :func:`~._propose._groups` and
    # :func:`~._near.near_misses` all construct ``lines=(line,)``, and
    # :attr:`~._cards.LineCard.line` is singular -- ``proposal_card`` renders
    # ``proposal.lines[0]``.  So a proposal naming TWO lines would render one
    # card about the first and NO card for the second, and this figure would
    # be one short of :func:`~._undisposed.inbox_partition`'s.
    # **That is plan step ``bank_import:X-gn``'s to keep** -- it is the step
    # that lets a match name a second bank line -- and the failure it would
    # cause is the RIGHT one: a badge disagreeing with the page it links to,
    # rather than a line silently rendering nowhere.  **It is not caught for
    # free, and saying so is the point**: the equality is asserted by ONE case
    # (``test_awaiting_count.TestTheCountAgreesWithTheScreenItLinksTo
    # .test_the_badge_equals_the_page_it_links_to``) over ONE fixture, whose
    # proposal names one line -- so ``X-gn`` owes that case a multi-line
    # fixture or the alarm will not fire.  Counting distinct proposal LINES
    # here instead would restore the arithmetic and hide the missing card,
    # which is the trade plan step ``bank_import:X-gm`` exists to refuse; the
    # opposite reading of the same premise was argued at the retired
    # ``ReviewSet.explained_by_a_proposal``, which counted lines for a value
    # that was a REPORT rather than a caption over cards; it went with the
    # queue at ``bank_import:X-gi-3``, and the premise is recorded here
    # because THIS figure still turns on it.
    inbox = to_explain_sections(review)
    to_explain = sum(len(section.cards) for section in inbox)

    kind, sections = _tab_sections(scope, tab, transfers, inbox, limit)
    return ReconcilePage(
        tab=tab,
        # **The kind comes back FROM the builder**, so the page cannot claim a
        # kind its own cards are not (**R-JX**).
        kind=kind,
        hero=_hero(agreement, to_explain),
        # **One row read, and one COUNT where that row exists.**  The
        # provenance line the locked direction prints beside the four figures
        # is a fact about the last import rather than about the comparison, so
        # it is read here and carried whole.  ``EXPLAIN ANALYZE`` on a
        # restored production clone 2026-08-30: 0.034 ms and 0.071 ms of
        # execution, against ``review_set``'s own 0.136 s on the same
        # account -- so the provenance costs about a two-thousandth of what
        # the page already pays to exist.
        last_import=last_import(scope.owner_id, scope.account_id),
        # TWO arguments, not the balance side's three: its ``explained`` feeds
        # the *already explained* chip plan step ``bank_import:X-gj-1c``
        # deleted (**R-HU**), whose count is the union of two tabs.
        chips=_chips(review, transfers),
        books_bound=review.bounds.books,
        opened=_opened_pane(scope, review, sections, opened),
        counts=(
            TabCount(tab=Tab.TO_EXPLAIN, count=to_explain),
            TabCount(tab=Tab.EXPLAINED, count=counts.by_hand),
            TabCount(tab=Tab.FILED_BY_RULES, count=counts.by_rule),
            TabCount(tab=Tab.TRANSFERS, count=len(transfers)),
            TabCount(tab=Tab.SKIPPED, count=skipped),
        ),
        sections=sections,
        # **Only the inbox sweeps.**  A settled act is undone one at a time
        # (**R-GY** confirms where it destroys a row), and a holding card has
        # no act to sweep at all.
        sweeps=_sweeps(sections) if tab is Tab.TO_EXPLAIN else (),
        unexamined=_unexamined(review),
        held_elsewhere=scope.candidates.held_elsewhere,
    )


def _holding(cards: "tuple[LineCard, ...]") -> "tuple[CardSection, ...]":
    """Return a holding tab's one unnamed section, or nothing at all.

    Args:
        cards: The tab's cards.

    Returns:
        One :class:`~._cards.CardSection` withholding nothing, or ``()`` --
        because an empty section is ABSENT rather than rendered empty.
    """
    if not cards:
        return ()
    return (CardSection(section=None, cards=cards, withheld=0),)


def _tab_sections(
    scope: "ReviewScope",
    tab: Tab,
    transfers: "tuple[LineCard, ...]",
    inbox: "tuple[CardSection, ...]",
    limit: "int | None",
) -> "tuple[CardKind, tuple[CardSection, ...]]":
    """Return which KIND of card the open tab holds, and its cards.

    **A dispatch over the five, exhaustive by construction**: every member of
    :class:`Tab` has an arm, so a sixth tab added later fails loudly here
    rather than rendering an empty page.

    **It returns the KIND beside the sections, and that pairing is the point**
    (**R-JX**).  Which kind a tab holds used to be a
    ``Tab.holds`` property over a table -- one fact in two homes, in two
    modules, agreeing only because a test said so.  The arm that BUILDS a kind
    of card is the only thing that knows which kind it built, so it says so,
    and :attr:`ReconcilePage.kind` carries the answer to the template.
    ``CLAUDE.md`` rule 14: the remedy for two homes is to delete one.

    **It is LOCAL rather than impossible**, and the distinction is the honest
    one: an arm can still be written ``CardKind.ACT, skip_sections(...)``.
    What it cannot be any more is written in a different FILE from the cards
    it describes.  :class:`~._cards.CardKind` carries the full argument.

    Args:
        scope: The pass's scope, for the two reads the settled tabs need.
        tab: Which tab is open.  **The PASS itself is no longer a parameter**
            (plan step ``bank_import:X-gm``): the only arm that read it was the
            inbox's, and the inbox arrives already built because the hero
            counts its cards.
        transfers: The Transfers tab's cards, built by the caller because the
            hero's own chip is derived from them and a second build would be
            two derivations of one list.
        inbox: The To-explain tab's sections, built by the caller for the same
            reason one step stronger: the hero's ``to_explain`` is their card
            count, so building them again here would be two derivations of the
            list a rendered number is about (plan step ``bank_import:X-gm``).
        limit: How many rows a bounded arm may render, or ``None`` for all of
            them.  **THREE arms read it** -- the two settled ones and Skipped
            (**R-JW**) -- while the inbox is bounded by what
            the pass found and a holding tab renders every line in its state
            (see :func:`_holding`).  *This said it "reaches only the two
            settled arms" while the line below already passed it to the skip
            reader*, which adversarial review caught: a docstring contradicted
            by its own function three lines down.

    Returns:
        ``(kind, sections)`` -- the :class:`~._cards.CardKind` this tab's cards
        are, and the sections themselves, empty where the tab has no cards.
        **The kind is stated even where the sections are empty**, which is why
        it rides here rather than on a section: an empty tab still renders the
        Apply form or withholds it, and still picks an empty state.

    Raises:
        ValueError: When *tab* is not one this function knows, which cannot
            happen for a :class:`Tab` member and is raised rather than
            defaulted so that adding one is a failure and not a blank screen.
    """
    if tab is Tab.TO_EXPLAIN:
        return CardKind.LINE, inbox
    if tab is Tab.EXPLAINED:
        return CardKind.ACT, act_sections(accepted_register(
            scope.owner_id, scope.account_id, limit, applied_by_rule=False,
        ))
    if tab is Tab.FILED_BY_RULES:
        # **The register's reader and not** :func:`~._filing.rule_filed_acts`,
        # which is the IMPORT page's receipt: it bounds at
        # :data:`~._filing.RECEIPT_LIMIT` and orders by age alone, where a tab
        # beside Explained has to bound the same way and float an act that has
        # stopped holding to the top.  The two surfaces ask different
        # questions of one set, so each keeps its own reader.
        return CardKind.ACT, act_sections(accepted_register(
            scope.owner_id, scope.account_id, limit, applied_by_rule=True,
        ))
    if tab is Tab.SKIPPED:
        # **The recorded skips, read from their own store** (plan step
        # ``bank_import:X-gj-4c-2``, ruling **bank_import:R-JG**).  Built only
        # where the tab is OPEN, which is why the count above is a separate
        # aggregate: this is the same discipline the two settled arms keep.
        return CardKind.SKIP, skip_sections(
            skipped_acts(scope.owner_id, scope.account_id, limit),
        )
    if tab is Tab.TRANSFERS:
        # **A holding tab withholds nothing**: it renders every line in its
        # state, because a count of card payments the owner cannot act on is
        # useless if it is also truncated.
        return CardKind.LINE, _holding(transfers)
    raise ValueError(f"No Reconcile tab is built for {tab!r}.")
