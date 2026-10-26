"""What this pass did NOT look at, and why it cannot conclude about a line.

Plan step ``bank_import:X-gf-3a``.  Values with one subject -- the LIMITS
of one pass over one account -- split out of :mod:`._reads` so that the module
which answers *what does the review screen show* is not also the module that
answers *what did this pass fail to look at*.

**The two DAY bounds APPLY here too since plan step balance:X-f3c-2b-2b.**
``_reads`` held the two functions that shorten a pass's line list -- at the pay
calendar's first day and at the account's books opening -- while this module
held the values reporting what they removed, so the rule and its disclosure sat
in different modules and only :func:`bounded_lines` states their ORDER.  That
order is load-bearing: the two bounds overlap heavily on real data, and
applying each to the whole set would count 130 of the developer's own lines
under both.

**The TIMING was forced by a line cap, and saying so is the honest version.**
``_reads`` stood at 973 of pylint's 1,000 lines before that step and the books
split is 26 more, so the two could not both stay.  Colocating a bound's value
with the function that produces it is defensible on its own and is why the move
went this way rather than some other -- but it is not why it happened now, and
a docstring that gave only the good reason would be the decaying premise this
arc keeps finding.

**The split is what makes the rule verdict possible at all.**
:mod:`._verdict` has to ask why the pass would not conclude about a line, and
asking :class:`~._reads.ReviewSet` would be a cycle: :mod:`._filing` imports
:mod:`._reads`, so :mod:`._reads` cannot import the module that reads its own
verdict.  Housing the question here rather than in :mod:`._verdict` is the
other half of the same discipline: every live caller of :func:`search_gap`
except one is about a line no rule reaches -- the queue's INFLOW rows, the
hand-build list -- so a module named for what a RULE comes to would have been two
subjects wearing one name.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query -- every fact
it holds arrives from the pass that measured it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.services.cash_ledger import books_hold

from ._offers import BankLine
from ._pairing import DAY_WINDOW

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    # The two day bounds take the ORM rows :func:`~._undisposed.undisposed_lines`
    # loads, and this module performs no query on them: it reads ``id`` and
    # ``posted_on`` and returns the same objects.  A type-checking import
    # keeps that promise HONEST rather than true by construction: it stops this
    # module naming the mapper, which is not the same as stopping it emitting
    # SQL.  ``_split_at_books_open`` reads ``line.posted_on`` and ``line.id``
    # off live ORM instances, and on an EXPIRED instance either access would
    # emit a SELECT -- so the promise rests on the caller handing over loaded
    # rows, which :func:`~._undisposed.undisposed_lines` does.
    from app.models.statement_import import BankStatementLine
    from app.services.cash_ledger import CashOpeningFact


@dataclass(frozen=True)
class BooksBound:
    """The lines an account's OPENING EQUITY already accounts for.

    Plan step **balance:X-f3c-2b-2b**, finding **N-383**, ruling
    **balance:R-HG**.  An opening equity is the balance at the CLOSE of the day
    the books open, so every dollar the bank moved on or before that day is
    inside the one figure.  Such a line is not work and never becomes work:
    matching it settles a row for money the opening already holds, and
    recording it as new spending or new income does the same.
    :func:`._split_at_books_open` therefore takes it out of the pass
    entirely, exactly as :func:`._split_at_calendar_open` takes out a
    line older than the pay calendar, and this is what the screen says instead.

    **A value rather than four more fields on** :class:`ReviewBounds`, and the
    reason is the one that value's own docstring gives for existing: these four
    facts are one subject and must travel together, because a count without the
    day and the figure it is bounded by cannot be acted on.  It also keeps
    :class:`ReviewBounds` at pylint's attribute ceiling rather than buying a
    disable for a grouping the data already has.

    **``None`` on the bound, never a zero-count instance.**  A chip renders
    only when its count is non-zero (:class:`~._reconcile.HoldingChip`), and an
    instance meaning *nothing was held back* is one truth test away from a
    panel announcing that this pass withheld no lines -- the *nothing to see
    here* row the reconcile rebuild removed.

    Attributes:
        opened_on: The day this account's books open.
        opening_equity: What they open holding, which is the figure those
            lines are already inside.  Carried because the sentence is not
            actionable without it: *4 lines are already counted* invites the
            owner to check WHICH figure counted them.
        count: How many recorded lines this pass held back for that reason.
            Always at least one where this value exists.
        last_day: The latest day any of them posted.  Bounded above by
            :attr:`opened_on` by construction, and equal to it in the ordinary
            case -- the developer's own Checking opens on the pay calendar's
            own first day, so the four lines held back all post on 2026-03-26.
    """

    opened_on: date
    opening_equity: Decimal
    count: int
    last_day: date

    @property
    def said(self) -> str:
        """Return the sentence the screen prints for this bound.

        Composed here rather than in Jinja, which is this package's standing
        rule (:attr:`~._bars.BarredLine.reason`): a template restating a
        partition is a second place for it to be wrong, and three surfaces
        render this one -- the reconcile page's holding chip, the workbench's
        pick-list caveat and the review body's bounds panel.

        **It states the BOUND and stops there**, and the ACT is
        :attr:`restatement_act` beside it -- the exact pairing
        :attr:`~._bars.BarredLine.reason` has with
        :attr:`~._bars.BarredLine.answer_door`, and for its reason: a surface
        that can build a URL renders the act as a link, and one that cannot
        prints it, so the two have to be separable.  Neither names a POSITION
        on any page, which is the coupling ruling **bank_import:R-HC** found in
        eight owner-visible sentences at once.

        **The money carries a thousands separator and the days read the way
        the books-opening card writes them**, which is not decoration: this
        sentence and that card describe the SAME opening two clicks apart, and
        a ``$12345.67`` here against a ``$12,345.67`` there reads as two
        different figures.  Named by adversarial money review 2026-08-31.

        Returns:
            The sentence, with no *nothing was changed* clause: nothing was
            pressed, so there was nothing to change.
        """
        return (
            f"{self.count} line(s) up to "
            f"{self.last_day.strftime('%b %-d, %Y')} are already inside this "
            "account's opening balance of "
            f"${self.opening_equity:,.2f}.  Its books open "
            f"{self.opened_on.strftime('%b %-d, %Y')}, and an opening equity "
            "is the closing balance for its own day, so the money those "
            "lines moved is already counted."
        )

    @property
    def restatement_act(self) -> str:
        """Return the act that would release these lines.

        Split from :attr:`said` so a surface holding a URL can render it as a
        link and one that cannot can print it, without either restating the
        other's half (:attr:`~._bars.BarredLine.answer_door`'s shape).

        **It is UNCONDITIONAL, where its precedent is not**, and the
        difference is the point that value makes: ``answer_door`` returns
        ``None`` where changing the answer would change nothing, which is 9 of
        9 parked lines on real data.  Restating an opening always would --
        every line this bound holds is held by that one day, so the act is
        never the *chooser that cannot succeed* shape.

        Returns:
            The sentence naming the act, hedged on the owner's own records
            because the app cannot know whether the opening or the statement
            is the wrong one.
        """
        return (
            "Restate this account's opening if your records really do start "
            "before then"
        )


@dataclass(frozen=True)
class ReviewBounds:
    """What the review DID NOT look at, and why.

    **A screen that lists what it could explain and says nothing about what it
    could not reads as a clean sweep.**  These facts are one subject -- the
    limits of this pass -- and they travel together so a caller cannot render
    the proposals while forgetting the caveat.

    Attributes:
        calendar_opens: The first day the owner's pay calendar covers, or
            ``None`` for an owner with no periods at all.
        before_calendar_count: How many recorded lines fall before it, which
            nothing can ever match: there are no rows to match them to.  A
            COUNT and a last day rather than the rows themselves -- they are
            not work, they are the statement being older than the budget.
            Measured at 130 of the developer's own 378 recorded lines.
        before_calendar_last_day: The latest of those days, or ``None``.
        books: The lines this account's OPENING EQUITY already accounts for
            (:class:`BooksBound`), or ``None`` where it held none back.  The
            SECOND bound on the same list and a different fact from the first:
            the pay calendar is the owner's, and the books opening is this
            ACCOUNT's, so the two remedies are *extend your pay schedule* and
            *restate this account's opening*.  **Disjoint by construction** --
            :func:`bounded_lines` splits the calendar off first and this
            off what remains -- so the two counts never claim the same line
            and can be read as a sum.  Measured on a restored production clone
            2026-08-31: Checking's 378 lines split 130 before the calendar and
            4 more inside the calendar the books cannot hold, because its
            books open on the calendar's own first day.
        crowded_days: Days the GROUP search refused to look at, as it
            reports them (:attr:`~._propose.ProposedMatches.crowded_days`).
        unpriceable_count: How many of the account's subjects could not be
            priced, so they could not be offered
            (:attr:`~._subjects.Candidates.unpriceable`): a row or a row's
            kept payment the amount model had no rule for, and a transfer's
            still-planned side whose price the amount model could not answer
            (a damaged shadow pair was counted here too, ruling **R-BAL158**,
            until ruling **R-BAL235** priced a leg off its transfer alone).

    **The near tier's bound is NOT here, and that is plan step
    ``bank_import:X-f6d-3``'s one deliberate exception to the paragraph above.**
    It was ``undecided_near_count``, and a count in this panel names no line:
    the owner was told that somewhere among a hundred lines one had a near
    candidate the page would not choose, with no way to find it.  A bound is
    only a bound if it can be acted on, so it moved onto the LINE
    (:attr:`ReviewSet.declined_lines`), where the act it should prompt is
    already offered -- and the panel keeps the four limits that genuinely
    belong to the PASS rather than to any one line.

    **``impossible_day_count`` is GONE, and plan step ``bank_import:X-gm``
    deleting it is the point rather than a tidy-up.**  It counted the lines the
    bank dates MADE after they POSTED, which
    :func:`~._leftovers._creatable_lines` dropped out of every list to produce
    it -- so the number named no line, and the lines it named reached no card
    on the Reconcile page at all.  That is the paragraph above applied to the
    one member that had outlived it: a bound is only a bound if it can be acted
    on, so this one moved onto the LINE as
    :attr:`~._leftovers.CreatableLine.withheld`, where the act it withholds is
    already offered.  Finding **N-325**'s rule is untouched -- the app still
    refuses to choose between the bank's two days -- and
    :func:`~._scope.impossible_days_refusal` is the one sentence that says so.
    """

    calendar_opens: "date | None"
    before_calendar_count: int
    before_calendar_last_day: "date | None"
    crowded_days: "tuple[date, ...]"
    unpriceable_count: int
    books: "BooksBound | None" = None


def _split_at_calendar_open(
    recorded: "list[BankStatementLine]", opens: "date | None",
) -> "tuple[list[BankStatementLine], list[BankStatementLine]]":
    """Split the account's unmatched lines at the owner's first payday.

    Args:
        recorded: Every recorded line no act has answered (a match, or a
            recorded skip since plan step ``bank_import:X-gj-4a``).
        opens: The first day the pay calendar covers, or ``None`` for an owner
            with no periods at all -- in which case nothing is BEFORE, because
            "before the calendar" is not a fact about a calendar that does not
            exist, and the lines are reported as unexplained rather than as
            out of reach.

    Returns:
        ``(before, inside)``.  A line before the first payday can never be
        matched -- there are no rows before that day for it to match -- so it
        is COUNTED on :class:`ReviewBounds` rather than listed as work.
    """
    before = [
        line for line in recorded
        if opens is not None and line.posted_on < opens
    ]
    before_ids = {line.id for line in before}
    return before, [line for line in recorded if line.id not in before_ids]


def _split_at_books_open(
    inside: "list[BankStatementLine]", opening: CashOpeningFact,
) -> "tuple[BooksBound | None, list[BankStatementLine]]":
    """Split the calendar's lines at the day the account's books open.

    Plan step **balance:X-f3c-2b-2b**, finding **N-383**, ruling
    **balance:R-HG**.  :func:`_split_at_calendar_open`'s twin one fact over: an
    opening equity is the balance at the CLOSE of ``opened_on``, so a line
    posted on or before it is ALREADY INSIDE that figure and nothing this
    screen offers can be done with it.  Matching it settles a row for money the
    opening already holds; recording it as spending or income does the same.

    **It runs AFTER the calendar split and over what that leaves**, which is
    what makes the two bounds disjoint and their counts a sum.  On the
    developer's own Checking the books open on the pay calendar's own first day
    (2026-03-26), so every line the calendar excludes the books would exclude
    too -- reporting both over the whole set would have counted 130 lines
    twice and told him 134 were inside an opening balance that accounts for 4.

    **The bound is what the SCREEN says, and the split is what stops the
    OFFER.**  Removing the line here is what makes the fix structural rather
    than a control-by-control refusal: the proposer never sees it, so the app
    stops PROPOSING acts it would refuse; ``creatable`` and
    ``recordable_inflows`` never hold it, so no create or record control is
    rendered; ``file_new_swipes`` reads this same pass, so the door that moves
    money with no press cannot reach it either.  Measured before the split, on
    a restored production clone: of the four lines Checking's books cannot
    hold, two were offered controls with no withholding at all and the other
    two were PROPOSED matches inside the one-click sweep.

    **The predicate is** :func:`~app.services.cash_ledger.books_hold`, asked of
    the posting day -- not re-spelled here, and not asked of the transaction
    day.  A swipe MADE before the books opened and TAKEN after is money that
    left the account after the opening, so it is recordable; its purchase's
    ``purchased_on`` is a budget clock and never a movement.

    Args:
        inside: The unmatched lines the pay calendar covers, in the order
            :func:`~._undisposed.undisposed_lines` gives -- ascending by
            posted day.
        opening: The account's governing
            :class:`~app.services.cash_ledger.CashOpeningFact`, off the pass
            (:attr:`~._scope.ReviewScope.opening`), so the screen and the
            doors judge every line against ONE opening.

    Returns:
        ``(bound, remaining)``.  The bound is ``None`` where the books hold
        every line, which is the state that needs no disclosure at all.
    """
    held = [
        line for line in inside
        if not books_hold(opening.opened_on, line.posted_on)
    ]
    if not held:
        return None, inside
    held_ids = {line.id for line in held}
    bound = BooksBound(
        opened_on=opening.opened_on,
        opening_equity=opening.opening_equity,
        count=len(held),
        last_day=max(line.posted_on for line in held),
    )
    return bound, [line for line in inside if line.id not in held_ids]


@dataclass(frozen=True)
class BoundedLines:
    """One pass's working line list, and what the two DAY bounds held back.

    Plan step **balance:X-f3c-2b-2b**.  :func:`bounded_lines`' answer, as a
    value rather than a three-tuple for the reason
    :class:`~._offers.MatchDays` is one: the three are derived together and a
    caller unpacking them in the wrong order gets a plausible-looking pass over
    the lines it was supposed to exclude.

    Attributes:
        inside: The lines this pass may offer acts on, in the order they were
            given -- ascending by posted day.
        before_calendar: The lines older than the owner's first payday.  The
            LINES rather than a count, because the caller states both the count
            and their latest day and deriving one of those here would leave the
            other to be derived twice.
        books: What this account's opening equity already accounts for
            (:class:`BooksBound`), or ``None`` where it accounts for none.
            Already a summary rather than the lines, because unlike the bound
            above it carries facts about the ACCOUNT -- the opening day and
            figure -- that no list of lines holds.
    """

    inside: "list[BankStatementLine]"
    before_calendar: "list[BankStatementLine]"
    books: "BooksBound | None"


def bounded_lines(
    recorded: "list[BankStatementLine]",
    opens: "date | None",
    opening: "CashOpeningFact",
) -> BoundedLines:
    """Narrow a pass's recorded lines to the ones it may offer acts on.

    Plan step **balance:X-f3c-2b-2b**.  **The ORDER of the two bounds is what
    this function is for**, and it is not cosmetic: the calendar bound is
    applied first and the books bound to what it leaves, so the two counts are
    DISJOINT and can be read as a sum.

    **They overlap heavily on real data.**  Measured on a restored production
    clone 2026-08-31: Checking carries 378 recorded lines, 130 posted before
    the pay calendar's first day and 134 posted on or before the day its books
    open -- because its books open ON the calendar's own first day.  Applying
    each bound to the whole set would report 264 lines held back out of 378 and
    tell the owner that 134 of them are inside an opening balance that accounts
    for 4.  In sequence it is 130 and 4, which is the truth and adds up.

    **Sequence rather than subtraction**, so a reader cannot get it wrong: the
    alternative is two independent predicates and a comment saying the second
    count must exclude the first, which is the kind of instruction that
    survives exactly as long as nobody edits it.

    Args:
        recorded: Every recorded line no act has ANSWERED -- neither a match
            nor a recorded skip -- ascending by posted day
            (:func:`~._undisposed.undisposed_lines`).  *It said "no accepted
            match explains" until plan step ``bank_import:X-gj-4a``.*
        opens: The first day the owner's pay calendar covers, or ``None`` for
            an owner with no periods at all.
        opening: The account's governing
            :class:`~app.services.cash_ledger.CashOpeningFact`, off the pass
            (:attr:`~._scope.ReviewScope.opening`).

    Returns:
        The :class:`BoundedLines`.
    """
    before, inside = _split_at_calendar_open(recorded, opens)
    books, inside = _split_at_books_open(inside, opening)
    return BoundedLines(inside=inside, before_calendar=before, books=books)


def search_gap(
    line: BankLine,
    declined_lines: "dict[int, str]",
    crowded_days: "tuple[date, ...]",
    unpriceable_count: int,
) -> "str | None":
    """Return why this pass cannot say *line* has no counterpart, or ``None``.

    Plan step ``bank_import:X-ge``, developer ruling 2026-08-26, corrected at
    ``X-ge-1``, moved here from :class:`~._reads.ReviewSet` at ``X-gf-3a``.
    :meth:`~._reads.ReviewSet.search_gap_for` is the screen's spelling of it
    and delegates here.

    **It READS what the search reports and derives nothing**, which is the
    whole of the correction ``X-ge-1`` made.  A first version enumerated the
    bounds :class:`ReviewBounds` and the near tier PUBLISH, and called
    that enumeration complete; an adversarial review measured it false twice
    over, because the matcher applies more bounds than it published.
    Re-deriving them here would have been a third spelling of
    :data:`~._near.NEAR_MISS_BOUND` and :data:`~._pairing.DAY_WINDOW` outside
    the modules that own them -- finding **N-322** exactly, which
    :mod:`._pairing`'s own header predicts in as many words.  So each tier
    reports its own refusals now (:attr:`~._propose.ProposedMatches
    .declined_lines`) and this joins them to the two bounds that belong to the
    PASS rather than to any line.

    **What that makes true:** a tier added later must put its refusals in
    ``declined_lines`` or they are invisible, which is the same rule the search
    already keeps for its crowded days -- rather than this function having to
    be taught about it.

    **Why the answer is per LINE and not a count in a panel**: ruling
    **R-GD**'s third amendment withdrew the reviewed line's candidate LIST
    because no bound made one anything but noise -- 0 of 18 inspected correct
    -- and moved what survives onto the line itself, which is the ground plan
    step ``bank_import:X-f6d-3`` acted on and ``X-gf-3a`` extended to the rule
    verdict beside it.  A bound reported in a panel names no line and cannot be
    acted on.

    The three sources, in the order a reader should hear them:

    * what a TIER declined about this line, in that tier's own words: a near
      candidate it admitted and would not choose between (the
      `$356.61`-for-one-`$178.29` shape, finding **N-335**), one it refused for
      want of the merchant in the row's label, one it refused for the day
      window, and an EXACT candidate the window refused;
    * a CROWDED day the GROUP search skipped, measured within
      :data:`~._pairing.DAY_WINDOW` of the line because that is the window
      :func:`~._propose._groups` pairs a line to a bucket across;
    * a row the amount model could not PRICE at all.  It is account-wide and so
      is this refusal: an unpriced row is absent from the candidate set
      entirely, so there is no line it can be said not to match.

    **Measured on the developer's own 378 recorded lines (2026-08-26):** the
    last two are ZERO, and the first touches 12 of the 80 lines a standing rule
    would file -- `$391.77` -- one of which is his own `Apple Music` row
    sitting one day past the window from an `Apple` line the door would
    otherwise have recorded a second time.

    Args:
        line: The bank line, which must be one this pass considered.
            **THREE surfaces take it off three different lists**, and the claim
            that "every caller takes it off ``creatable``" was already false
            when it was written: the queue's OUTFLOW rows read it off
            :attr:`~._reads.ReviewSet.creatable`, the hand-build form off
            :attr:`~._reads.ReviewSet.unmatched` (which is the only one an
            inflow used to reach), and since ruling **bank_import:R-GW** the
            queue's INFLOW rows off :attr:`~._reads.ReviewSet.recordable_inflows`.
            What every caller does share is that the line was in THIS pass,
            which is what makes *declined_lines* answerable for it.
        declined_lines: What each tier declined about a line, by line id
            (:attr:`~._propose.ProposedMatches.declined_lines`).
        crowded_days: The days the GROUP search refused to look at, as it
            reports them (:attr:`~._propose.ProposedMatches.crowded_days`).
        unpriceable_count: How many of the account's subjects could not be
            priced (:attr:`~._subjects.Candidates.unpriceable`): a row or a
            row's kept payment the amount model had no rule for, and a
            transfer's still-planned side whose price the amount model could
            not answer (a damaged shadow pair was counted here too, ruling
            **R-BAL158**, until ruling **R-BAL235** priced a leg off its
            transfer alone).

    Returns:
        One sentence naming the gap, for the receipt that has to say what it
        withheld and for the screen that has to say why a line is still there;
        ``None`` when this pass searched exhaustively for a counterpart to
        *line* and found none.
    """
    declined = declined_lines.get(line.line_id)
    if declined is not None:
        return declined
    crowded = [
        day for day in crowded_days
        if abs((day - line.posted_on).days) <= DAY_WINDOW
    ]
    if crowded:
        return (
            f"{crowded[0]} held too many rows for the app to search them "
            f"for a group that adds up to this line"
        )
    if unpriceable_count:
        return (
            f"{unpriceable_count} row(s) on this account could not be priced, "
            f"so the app could not compare them against this line"
        )
    return None
