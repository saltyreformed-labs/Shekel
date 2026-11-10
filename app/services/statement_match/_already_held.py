"""What the books ALREADY HOLD that one bank line may be, in either direction.

Plan step ``bank_import:X-gj-2b``.  Split out of :mod:`._reads`, whose subject
is *what the review screen shows about this pass*; this one answers a narrower
question that TWO pipelines now ask -- *does this period already hold money
arriving that no bank line explains, which recording this line would count
twice*.

**The question is about ARRIVING money and not about INCOME**, and every name
in this module said the narrower word until plan step
``bank_import:X-gj-2b-3``.  The set is filtered on ``cash_amount > 0`` over
``ReviewSet.unmatched_rows``, which holds PURCHASE rows too -- a stored refund
is a positive-cash row there since ruling **bank_import:R-II** -- so the
answer always included rows that are not income.

**The split is what the 1,000-line ceiling on ``_reads`` was measuring.**  It
had one caller while only the INCOME door asked it.  Ruling **R-II** routes a
container-answered merchant credit into the PURCHASE pipeline
(:func:`~._verdict.ruled`), which must ask the same question of the same rows
-- and a predicate reached from two pipelines through a method on one of their
read models is the seam that ceiling exists to surface.

**Two facts since plan step ``bank_import:X-f6b-2`` (ruling
**bank_import:R-BI19**), one per direction, and the second exists because
the first's shape does not carry over.**  A deposit's counterpart is a bare
row in a PERIOD -- a salary row whose span covers the day -- so
:class:`ArrivalsAlreadyHeld` asks the period.  An outflow's counterpart is
the same money *in another shape*: a purchase logged by hand at another
amount or day, or a bill priced at another figure, so the amount is exactly
what is wrong and no figure test can find it.  What CAN find it is the two
things a hand-logged row shares with the swipe -- the merchant's name, and
the container the merchant's swipes go in -- and :class:`SpendingAlreadyHeld`
asks those.  Measured on the developer's own restore 2026-09-18 (X-f6b-2's
handoff s.3d-1): 14 of 51 creatable outflows, 11 of them lines the
search-gap guard does not reach, 1 needless warning among 20 accepted exact
matches.

Services-boundary discipline: plain data in, a frozen dataclass out, no Flask
import, no query.  Every fact it needs arrives as an argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from ._near import names_the_merchant
from ._pairing import within_window
from ._subjects import RowKind

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ._offers import BankLine
    from ._subjects import CandidateRow


@dataclass(frozen=True)
class ArrivalsAlreadyHeld:
    """Every unexplained ARRIVAL the books already hold for one line's period.

    Ruling **bank_import:R-GW**, added after this step's own adversarial review measured
    what the card was really protected by.  **Recording a deposit the books
    already hold is the only way this door can double-count money**, and the
    per-line safeguard the card was written around -- the pass's own near-miss
    sentence (:meth:`ReviewSet.search_gap_for`) -- fires only where some TIER
    admitted a candidate and declined it.  Measured on the developer's own
    data 2026-08-27: it fires on **4 of 16** recordable inflows, and the three
    it misses hardest are `$2,612.98`, `$2,612.97` and `$2,612.97` payroll
    deposits -- **`$7,838.92`** -- each sitting in a pay period whose books
    hold a `$2,473.38` salary row nothing explains.  Those rendered a bare
    one-click tick, with only a card-level paragraph between the owner and a
    duplicate; *a warning paragraph is not a door* is what ruling **R-GJ**
    measured `$7,412.94` going through.

    **It is a FACT and not a candidate**, which is the distinction ruling
    **R-GD**'s third amendment turns on: that amendment withdrew the reviewed
    line's candidate LIST because no bound made one anything but noise -- 0 of
    18 inspected correct.  This names no candidate and scores nothing.  It
    answers *does your budget already hold money arriving in this pay period that no
    bank line explains*, which is a question about the PERIOD, and it is the
    question whose answer decides whether recording this is a duplicate.

    **The one narrowing is a PROOF, not a threshold**, and it is the same
    argument ruling **bank_import:R-GW** rests on: a deposit SMALLER than the smallest
    unexplained arriving row in its period cannot be any subset of them, because
    every one of them is positive and already exceeds it.  So the five
    dividends of `$0.12`-`$0.22` and the three card refunds of `$11.73`-
    `$28.29` -- the eight lines this whole step exists for -- say nothing,
    while every payroll deposit does.  Measured on the developer's own data
    2026-08-27: **8 of 16** recordable inflows warn, against 4 of 16 for
    ``search_gap_for`` alone, and the three payroll deposits worth `$7,838.92`
    that had NO per-line signal now have one.

    **The obvious alternative tightening is refused**: warn only where the
    rows could SUM to this line is measured false on the shape it exists for --
    the 2026-03-26 payroll deposit is `$2,573.42` and its period's two rows
    come to `$2,573.38`, **four cents short**, which is finding **N-239**
    exactly.  A bound that misses the case it was built for is the tolerance
    this arc refuses; a bound that only drops what provably cannot match is
    not one.

    **THE SET IS WIDER THAN THE NAME SINCE RULING bank_import:R-II, AND THE
    NAME IS A DEBT plan step ``bank_import:X-gj-2b-3`` OWNS.**  The filter is
    ``cash_amount > 0`` over :attr:`~._reads.ReviewSet.unmatched_rows`, which
    holds PURCHASE rows beside transaction rows -- and
    :func:`~._valuation.purchase_candidate` sets ``cash_amount`` to the
    stored figure in the parent's direction
    (:func:`~app.services.cash_ledger.movement_cash_leg`), so a stored REFUND
    under an expense row is a positive-cash row here.  That is
    the right SET: the question is *could this money already be in the books*,
    and a refund the books already hold is money that already arrived.  It is
    the wrong WORD, and the NAME is what plan step ``bank_import:X-gj-2b-3``
    changed: this class was ``IncomeAlreadyRecorded``, the method was
    ``ReviewSet.income_already_recorded_in``, and the field the queue, the
    cards and two templates carry was ``income_already_held`` -- 74 references
    across 13 files, renamed together so no half of it can go on saying
    *income* about a set that holds refunds.  The two template SENTENCES said
    it out loud (*already holds N income row(s)*) and were changed in the same
    pass; the service-composed one had already been corrected at
    ``bank_import:X-gj-2b`` (see :meth:`why_it_could_double_count`), which is
    what left the name and the words disagreeing.

    Attributes:
        rows: The unexplained ARRIVING rows whose pay period covers the day
            the bank credited this line, in the order the offer set holds them.
            **Not only income rows**: a stored REFUND is a positive-cash row
            in ``unmatched_rows`` (ruling **bank_import:R-II**), and it is
            money the books already hold arriving exactly as a salary row is.
        total: What they come to, POSITIVE -- every member's ``cash_amount``
            is, by the filter that selects them -- so the screen states the
            figure without arithmetic in a template.
    """

    rows: "tuple[CandidateRow, ...]"
    total: Decimal

    #: The words the card's alert sets around the count and the total
    #: (``books_already_hold``): WHO holds them, and WHICH rows.  Class-level
    #: constants on the VALUE rather than words in the template, because the
    #: twin below has different words and a template choosing between two
    #: values' sentences with ``{% if %}`` is the second place for the
    #: partition to be wrong (:attr:`~._leftovers.CreatableLine.warning`
    #: states the rule).
    who_holds: ClassVar[str] = "This pay period already holds"
    which: ClassVar[str] = (
        "your records say arrived and no bank line explains"
    )
    glyph_title: ClassVar[str] = (
        "This pay period already holds money arriving that no bank line "
        "explains."
    )

    @property
    def why_it_could_double_count(self) -> str:
        """Return the clause both withholding sentences are built from.

        **ONE composition, because two spellings of one rule are two things
        that can come to disagree** (plan step ``bank_import:X-gj-2b``, after
        that step's own adversarial review).  The receipt the INCOME door
        writes (:func:`~._filing._inflow_filings`) and the verdict the PURCHASE
        pipeline writes (:func:`~._verdict.ruled`) are about the same fact
        about the same period, and :mod:`._filing` states the rule for exactly
        this in as many words: *the sentence this receipt reports and the
        sentence the review screen prints beside the same line are the same
        value rather than two spellings of one rule*.  The second spelling
        arrived with the refund half, and it had already drifted -- it printed
        the figure as a bare ``Decimal``.

        **It does not say *income*, and that is not a wording preference.**
        The rows it totals are every ARRIVING row the books hold and no line
        explains, which since ruling **bank_import:R-II** includes a stored
        refund -- a negative purchase, whose cash is positive.  **The class
        was still called ``IncomeAlreadyRecorded`` when this sentence was
        written**, because renaming it reached 74 references across 13 files;
        the SENTENCE is what the owner reads, so it was made true first and
        plan step ``bank_import:X-gj-2b-3`` renamed the rest to match.

        **The FIGURE carries a currency symbol and separators**, which is what
        every other money sentence this package composes does
        (:func:`~._receipt_sentences.created_summary`, :mod:`._gaps`).  Both sides read
        ``2473.38`` where the card beside them read ``$2,473.38``, because a
        template's ``money()`` filter never reaches a string a service already
        composed.

        Returns:
            The clause, with no leading capital and no trailing stop, so each
            caller sets it in its own sentence.
        """
        return (
            f"the pay period it falls in already holds "
            f"${self.total:,.2f} your records say arrived and no bank line "
            f"explains, so recording it automatically could count the same "
            f"money twice"
        )



def arrivals_already_held(
    unmatched_rows, line: BankLine,
) -> "ArrivalsAlreadyHeld | None":
    """Return what the books already hold for *line*'s period, or ``None``.

    **The ONE statement of the double-count safeguard, and it has TWO callers
    since plan step ``bank_import:X-gj-2b``.**  It was a method body reached
    only by :meth:`ReviewSet.arrivals_already_held_in`, which serves the
    lines the INCOME pipeline owns.  That step routes a container-answered
    merchant credit into the PURCHASE pipeline instead, and those lines are
    ruled by :func:`~._verdict.ruled` -- so a hazard this package added a
    control for was live for a class the very next change routed past it.
    Extracted rather than re-asked, so the two pipelines cannot come to
    disagree about what the books already hold.

    **The period is tested by the row's own SPAN** (``expected_on`` ..
    ``expected_through``), which for a transaction IS its period and for a
    refund purchase is the day it was made.  *The row carries its ``period``
    whole since plan step ``bank_import:X-gz``*, and this test is
    deliberately left on the span: asking ``period.covers`` here would widen
    a refund's test from its day to its whole paycheck, which changes what the
    safeguard counts and is nobody's ruling.

    Args:
        unmatched_rows: The candidate rows no bank line explains.
        line: The inflow being considered.

    Returns:
        The :class:`ArrivalsAlreadyHeld`, or ``None`` when this period's
        books hold nothing that could be the same money.
    """
    day = line.posted_on
    rows = tuple(
        row for row in unmatched_rows
        if row.cash_amount > 0
        and row.expected_on <= day <= row.expected_through
    )
    # **A deposit smaller than the SMALLEST of them cannot be any subset of
    # them**, every one being positive -- so there is nothing for the owner
    # to check and a sentence here would be the warning-on-every-row shape
    # this package measures money going through.  A PROOF rather than a
    # bound: it drops only what cannot match, at any tolerance.
    if not rows or line.amount < min(row.cash_amount for row in rows):
        return None
    return ArrivalsAlreadyHeld(
        rows=rows,
        total=sum((row.cash_amount for row in rows), Decimal("0.00")),
    )


@dataclass(frozen=True)
class SpendingAlreadyHeld:
    """Every unexplained LEAVING row the books may already hold this line as.

    Ruling **bank_import:R-BI19**, plan step ``bank_import:X-f6b-2``, finding
    **N-381**.  The twin of :class:`ArrivalsAlreadyHeld` for money going OUT,
    and the daily feed's first need: ruling **R-GH**'s automatic door files a
    NEW swipe under a standing rule with no press, and the only guards on
    that door were *the pass did not finish looking* (:func:`~._gaps.search_gap`)
    and *the destination is proposed whole*.  A row whose figure sits more
    than :data:`~._near.NEAR_MISS_BOUND` from the line is never admitted by
    the near tier and never reported -- so a purchase the owner logged by
    hand at another amount, or a bill priced at another figure, was invisible
    to both, and the rule filed the swipe a second time.  Measured on the
    developer's 2026-09-15 import: 21 of 150 undisposed lines are purchases
    logged at another amount or day, 10 are bills whose app amount differs.

    **Two shapes, one fact, and NO narrowing by amount.**  The amount is what
    is wrong in *another shape* -- `$50.00` logged for a `$54.12` swipe -- so
    the inflow value's proof (a deposit smaller than the smallest row cannot
    be any subset of them) is invalid here and is not copied.  The bounds are
    the day window the pass already pairs across (:func:`~._pairing.within_window`,
    :data:`~._pairing.DAY_WINDOW`) and the two things a hand-logged row shares
    with the swipe:

    * :attr:`named_for_merchant` -- rows whose label names the line's
      merchant (:func:`~._near.names_the_merchant`, the near tier's own word
      test), at any figure: a ``Food Lion`` purchase logged as `$50.00`, a
      bill called ``GEICO auto``;
    * :attr:`inside_destination` -- purchases under the DEFINITION the
      owner's rule files this merchant into (plan step ``bank_import:X-f6c``
      gave that container an identity across pay periods), whatever they are
      called: a ``weekly shop`` entry in Groceries.  Empty where no rule
      reaches the line, because there is then no destination to look in.

    **Rejected shapes, each measured or argued in the ruling**: mirroring the
    inflow's period-span test (an envelope IS a leaving row covering every
    day of its period, so every swipe would warn); either shape alone (the
    first misses ``weekly shop``, the second misses bills and rule-less
    lines); widening the near tier's published refusals to ``TOO_FAR`` (it
    cannot reach the container, and it would make *did not finish looking*
    say something false).

    **A row whose figure is NOT ITS OWN is never one of these, in either
    shape.**  An envelope is priced at its purchases and a CC payback at the
    card spend it repays (:attr:`~._offers.CandidateRow.states_own_figure`,
    ``False`` for exactly those two), so neither holds money the books do not
    already hold as the rows it is priced from -- and both stay in
    ``unmatched_rows`` for the hand-build form's sake, with negative cash.
    Counting the container beside its own purchase says `$100.00` for one
    `$50.00` row; and because a new envelope is NAMED FOR THE MERCHANT by
    default (both new-envelope forms offer the merchant as the name), a
    merchant-named envelope would be *named for* every later swipe into it
    from the moment the automatic door filed the first -- the door
    manufacturing the population it withholds on.  That is the ruling's own
    rejected shape (*an envelope IS a leaving row covering every day of its
    period, so every swipe would warn*) reached by another road, and the near
    tier refuses the same rows first of all
    (:attr:`~._near.NearRefusal.FIGURE_NOT_ITS_OWN`) for the same reason.
    Found by this leaf's adversarial review 2026-09-18.

    **Its cost is stated**: while an envelope holds hand-logged unexplained
    purchases within the window, every swipe into it is withheld from the
    automatic door and needs the owner's tick.  Under the feed the owner
    hand-logs nothing, so that population empties itself within one window.

    Attributes:
        named_for_merchant: The unexplained rows naming the line's merchant
            inside the window, in the offer set's order.
        inside_destination: The unexplained PURCHASES under the rule's
            destination definition inside the window, in the offer set's
            order.  A row in both is in both.
        merchant: What the bank calls the merchant, for the sentence.
        destination: What the card calls the rule's destination, for the
            sentence, or ``None`` where no rule names one.
    """

    named_for_merchant: "tuple[CandidateRow, ...]"
    inside_destination: "tuple[CandidateRow, ...]"
    merchant: str
    destination: "str | None"

    #: The card's words (see :class:`ArrivalsAlreadyHeld`): the books rather
    #: than the period, because the window reaches across periods.
    who_holds: ClassVar[str] = "Your records already hold"
    which: ClassVar[str] = "leaving that no bank line explains"
    glyph_title: ClassVar[str] = (
        "Your records already hold spending near this line that no bank "
        "line explains, named for this merchant or inside its envelope."
    )

    @property
    def rows(self) -> "tuple[CandidateRow, ...]":
        """Return every row either shape found, once each.

        Returns:
            The union: the rows named for the merchant first, then the rows
            only the container found, each run in the offer set's order.  A
            row found both ways is listed once, where the name found it.
        """
        seen = set()
        rows = []
        for row in self.named_for_merchant + self.inside_destination:
            key = (row.kind, row.row_id)
            if key not in seen:
                seen.add(key)
                rows.append(row)
        return tuple(rows)

    @property
    def total(self) -> Decimal:
        """Return what the rows come to, SIGNED -- negative, money leaving.

        Signed because the card's alert prints it through the ``money``
        macro beside the rows it lists, and those print signed (`-$50.00`);
        the clause :attr:`why_it_could_double_count` composes says the
        direction as a WORD (*$50.00 leaving*) and prints the magnitude, as
        the arriving twin's does.  One value, two renderings, each the
        convention of the sentence it sits in.

        Returns:
            The sum of the rows' cash effect.
        """
        return sum((row.cash_amount for row in self.rows), Decimal("0.00"))

    @property
    def why_it_could_double_count(self) -> str:
        """Return the clause the withholding sentence is built from.

        The twin of :meth:`ArrivalsAlreadyHeld.why_it_could_double_count`,
        composed once for the receipt and the screen for the reason stated
        there.  It names WHICH of the two shapes found the rows, because that
        is what tells the owner where to look: beside the merchant's other
        rows, or inside the envelope the rule would file into.

        Returns:
            The clause, with no leading capital and no trailing stop.
        """
        found = []
        if self.named_for_merchant:
            found.append(f"named for {self.merchant}")
        if self.inside_destination:
            found.append(f"inside {self.destination}")
        return (
            f"your records already hold ${-self.total:,.2f} leaving that no "
            f"bank line explains, {' or '.join(found)}, so recording it "
            f"automatically could count the same money twice"
        )


def spending_already_held(
    unmatched_rows, line: "BankLine", destination_id: "int | None",
    destination_name: "str | None", definition_of_parent: "dict[int, int]",
) -> "SpendingAlreadyHeld | None":
    """Return what the books may already hold *line* as, or ``None``.

    **The ONE statement of the outflow-side safeguard**, read by the card and
    by :func:`~._verdict.ruled` -- the same value, so the sentence the
    automatic door withholds on and the rows the screen names cannot come
    from two derivations (finding **N-359**'s rule, one direction over).

    Args:
        unmatched_rows: The candidate rows no bank line explains
            (:attr:`~._reads.ReviewSet.unmatched_rows`).
        line: The outflow being considered.
        destination_id: The DEFINITION the owner's rule files this line's
            merchant into (:attr:`~._placement.Placement.definition_id`), or
            ``None`` where no rule names one.
        destination_name: What the card calls it, or ``None``.
        definition_of_parent: The definition each candidate PURCHASE's parent
            row belongs to, by parent id -- read once per pass by the caller
            that holds the session.

    Returns:
        The :class:`SpendingAlreadyHeld`, or ``None`` when neither shape finds
        a row -- which is the state that makes filing safe, and the screen
        says nothing rather than saying it is fine.
    """
    # A row priced from OTHER rows (an envelope, a payback) holds nothing
    # the books do not already hold as those rows; see the class docstring.
    leaving = [
        row for row in unmatched_rows
        if row.cash_amount < 0
        and row.states_own_figure
        and within_window(row, line)
    ]
    named = tuple(row for row in leaving if names_the_merchant(line, row))
    inside = tuple(
        row for row in leaving
        if destination_id is not None
        and row.kind is RowKind.PURCHASE
        and definition_of_parent.get(row.parent_id) == destination_id
    )
    if not named and not inside:
        return None
    return SpendingAlreadyHeld(
        named_for_merchant=named,
        inside_destination=inside,
        merchant=line.merchant_label,
        destination=destination_name,
    )
