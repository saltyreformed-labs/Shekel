"""What the owner sends BACK, and the state they sent it back against.

:mod:`._offers` is what the app OFFERS; this is the other direction.  That
module's own docstring has always drawn the line -- *a* :class:`MatchProposal`
*is a candidate the app OFFERS; a* :class:`MatchSubmission` *is what the owner
sent back* -- and plan step ``bank_import:X-f6d-3`` is where the two stopped
fitting in one file.  The seam is the DIRECTION, not the line count: everything
here is read off a request body and must survive a hostile one, and nothing
there is.

**Why a submission carries a figure at all, when it deliberately never did.**
Finding **N-336**: the review screen states the correction accepting would
write -- *from ``-178.32`` to ``-178.29``* -- and until this step nothing
compared that with what the door was about to write.  ``resolve_rows``
re-derives each named row's price per act (finding **N-309**'s remedy, correct
in itself) and :func:`~._variance.corrected_figure` then wrote the bank's figure
whatever the re-derived price was.  **Reproduced**: the row was edited to
``500.00`` in another tab between render and Apply, and the door wrote a
**``$321.71``** correction under a ``$0.03`` caption.

**The exact tier never had this, and that is the shape of the regression
rather than an oversight.**  An equal match whose price moved became UNEQUAL
and was refused outright (**R-FV**), so staleness failed CLOSED by accident.
``X-f6d-2`` made an unequal one-to-one RECORDABLE (**R-GD(a)**) and
``X-f6d-1`` made it reachable from a tick, and the accident stopped protecting
anything.  :class:`ReviewedRow` restores that closure deliberately, for every
tier, and states WHY it refused.

**A precondition is not a payload.**  Nothing here is written.  Every figure
and every day the door commits it still re-derives from the rows the ids name,
inside the same transaction; what arrives on the wire is only *what the owner
was looking at*, which is the one question ruling **R-FP** asks -- *what
commits is what was reviewed* -- and the one thing no id can answer.

Services-boundary discipline (``CLAUDE.md`` Architecture): frozen dataclasses,
no Flask import, no query, no clock read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.utils.digit_strings import parse_row_id

from ._subjects import CandidateRow, RowKind


#: What separates a reviewed row's four fields on the wire.  A colon cannot
#: occur inside any of them: two are digit runs, one is a signed decimal, and
#: the fourth is a :class:`~._subjects.RowKind` VALUE rather than a label.
_SEPARATOR: str = ":"

#: How many fields a token carries.  Named so :meth:`ReviewedRow.from_token`
#: refuses a short body and an over-long one with the SAME test and its own
#: sentence.  **Not because the unpack below would otherwise escape** -- it
#: raises ``ValueError`` too, which this function documents and the schema
#: field catches -- but because a refusal that happens to fall out of a tuple
#: assignment is not a decision anyone can read: relax this to ``<`` and a
#: FIFTH field added to the token later is silently dropped rather than
#: refused.  A mutation sweep 2026-08-23 is what made that concrete.
_TOKEN_FIELDS: int = 4

#: The ONE spelling of a figure this module will read.  **Deliberately not
#: bare** ``Decimal(raw)``, which accepts ``"NaN"``, ``"Infinity"``, ``"1E+5"``
#: and ``"1_0"`` -- and a ``NaN`` here would make
#: :meth:`ReviewedRow.disagrees_with` answer ``None`` for every row, because
#: ``Decimal("NaN") != x`` is TRUE but ``==`` is FALSE and this compares with
#: ``!=``.  That is the money door silently opening, which is the class of hole
#: plan step ``bank_import:X-f6a-1`` already shipped once.
#:
#: **Anchored with ``\Z`` and not ``$``** (plan step ``bank_import:X-gp``,
#: adversarial review 2026-09-11): ``$`` matches before a TRAILING NEWLINE, so
#: ``"0.05\n"`` passed as a figure from 2026-08-23 until then -- value-equal to
#: ``"0.05"``, so it could not open the gate, but not the one spelling this
#: note claims.  A row token was never reachable that way: its figure sits
#: between two colons and its last field goes through ``parse_row_id``.
#:
#: Twelve integer digits and six decimal places bound it to what the producer
#: can emit: every candidate's figure descends from ``Numeric(12, 2)`` columns
#: (measured 2026-08-23 over all 804 candidates on a production clone -- 2
#: decimal places, 4 integer digits at the widest), and the six places are
#: headroom for a derived price rather than a licence.
#:
#: **Nothing TIES this to the producer, and the failure mode is the whole
#: pass rather than one item.**  A figure outside it renders a token this same
#: module refuses, the schema raises, and the pass reader fails the entire
#: submission at 400 -- so every apply on that account would die under a
#: message blaming the owner's page.  The control is a test rather than a type:
#: ``test_candidates.TestEveryOFFEREDRowCanCarryItsOwnTokenBack`` round-trips
#: the real offer set at both extremes of ``Numeric(12, 2)``.  Named by two
#: adversarial reviews 2026-08-23.
_FIGURE = re.compile(r"^-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?\Z")


def spell_figure(value: Decimal) -> str:
    """Return *value* as this screen's wire format spells a money figure.

    **The INVERSE of :func:`parse_figure`, and it lives beside it for that
    function's own reason.**  The same figure is submitted in two shapes --
    inside a row's reviewed token, and as the difference a match states it was
    reviewed against -- and reading them through two READERS was measured on
    2026-08-23 to have let three spellings past one gate that the other
    refused.  Writing them through two WRITERS is the same defect with the
    sides swapped, and a codec whose halves sit in two modules is the drift
    this package names as its own root cause; a first version of plan step
    ``bank_import:X-gj-1b`` put this in :mod:`._sides` and made
    :mod:`._submission` import it back.

    Two tokens spell a figure FOR THE WIRE and both come through here:
    :attr:`ReviewedRow.token`, and :attr:`ReviewedDifference.token`, which
    since plan step ``bank_import:X-gp`` is the one writer behind every
    consent value a surface emits -- the MATCH pane's options, the hidden
    field an agreeing match carries, and the difference a tier's unopened
    card states (:func:`app.jinja_filters.stated_difference`).

    **One other site spells a figure with ``:f`` and is deliberately NOT
    here**: :meth:`~._panel.MatchCandidates.matching` builds the text an
    owner's typed search is compared against.  That is a SEARCH predicate and
    not a submitted value -- nothing reads it back -- so routing it through
    this function would couple a display decision to a wire format and make a
    change to either answerable only by reading both.  The census is
    ``grep -rn ':f}"' app/``, which returns exactly those two lines.

    **``:f`` and never ``str()``.**  ``Decimal.__str__`` emits scientific
    notation for a small enough exponent (``Decimal("1E-7")`` is ``"1E-7"``)
    and :data:`_FIGURE` admits no ``E``, so the door would refuse a body this
    app had emitted.

    **Its precondition is TWO decimal places, and it is the caller's.**  ``:f``
    fixes the notation and not the precision: :data:`_FIGURE` admits one to six
    places, so a figure carrying seven would be spelled faithfully and then
    refused by :func:`parse_figure` -- measured 2026-08-30, where
    ``Decimal("1E-7")`` and ``Decimal("0E-8")`` are the only two of fifteen
    probe values that do not round-trip.  Every figure that reaches here is
    quantized to cents by :meth:`~._sides.MatchSides.of`'s ``round_money`` and
    descends from ``Numeric(12, 2)``, so the state is unreachable and carries
    no guard -- an arm for it would be error handling for an impossible
    scenario.  It is written down because "the notation is safe" and "the
    round trip is total" are different claims and this docstring used to imply
    the second.

    Args:
        value: The figure, as this package derived it, quantized to cents.

    Returns:
        Its plain decimal spelling, which :func:`parse_figure` reads back to an
        equal ``Decimal``.
    """
    return f"{value:f}"


def parse_figure(raw: str) -> Decimal:
    """Return *raw* as a money figure, or refuse the spelling.

    **The ONE strict money reader this screen's wire format has**, read by
    both token readers because the screen submits money in two shapes:
    inside a row's reviewed token, and as the figure half of the difference
    the owner accepted (plan steps ``bank_import:X-f6d-4`` and ``X-gp``).
    *It was a package export until X-gp, when the schema field that read it
    directly became a reader of the whole token instead.*  Reading them
    through two
    readers is what an adversarial review measured on 2026-08-23 -- the token
    refused ``"1_0"``, ``"+0.05"`` and ``" 0.05 "`` while the schema field
    beside it on the same form took all three, and quantized a sub-cent figure
    into agreement using ``ROUND_HALF_EVEN``, the mode ``app.utils.money``
    forbids reaching implicitly.

    **It REFUSES rather than repairs.**  A figure with more precision than the
    app can hold is not a figure the owner was shown, so rounding it into one
    would make the consent gate agree with a body it should have turned away.

    Args:
        raw: What arrived on the wire.

    Returns:
        Its :class:`~decimal.Decimal`.

    Raises:
        ValueError: When *raw* is not a spelling :data:`_FIGURE` admits.
    """
    if not _FIGURE.match(raw):
        raise ValueError("that is not a figure a row can hold")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:  # pragma: no cover - _FIGURE precedes
        raise ValueError("that is not a figure a row can hold") from exc

@dataclass(frozen=True)
class ReviewedRow:
    """One row a submission names, AS THE SCREEN SHOWED IT.

    Plan step ``bank_import:X-f6d-3``, finding **N-336**.  See this module's
    docstring for what it is for; what follows is why it carries TWO
    coordinates when either alone looks sufficient.

    **Neither sees the other's writers, and that is measured rather than
    argued** -- three probes on a production clone, 2026-08-23:

    * the row's own amount edited (``178.32`` to ``500.00``): ``version_id``
      moves 3 to 4 AND the figure moves.  Either coordinate sees it;
    * a card ENTRY added to the row: ``version_id`` stays at 3 while the figure
      moves ``-178.32`` to ``-153.32``.  Only the FIGURE sees it -- a
      transaction's cash is ``gross - off_statement_sum(entries)``
      (:func:`~app.services.cash_ledger.cash_leg_of`) and a CHILD insert emits
      no UPDATE against the parent, so no counter on the parent can move;
    * the row's ``purchased_on`` moved three days: ``version_id`` moves 1 to 2
      while the figure is unchanged.  Only the COUNTER sees it -- and that day
      decides whether a match RE-DATES the purchase
      (:func:`~._offers.corrected_purchase_day`), which is the one write
      releasing a match cannot undo.

    A version counter alone was the remedy finding N-336 was filed with.  It
    catches the case that was reproduced and misses the second bullet, which is
    the shape ``resolve_rows``' own docstring already documents as the reason
    it re-prices per act at all: *enumerating sibling writes is a guard the
    next unenumerated writer reopens*.

    Attributes:
        kind: Which table the row is in.
        row_id: Its primary key within that table.  The pair is the identity a
            submission names, and it REPLACED two bare id lists: a row and the
            state it was reviewed in are one fact, and carrying them apart
            would be a parallel array joined on the row id, whose halves a
            crafted body can desynchronise.
        cash_amount: Its signed cash effect on this account as the screen
            showed it.
        version_id: The revision the screen showed.
    """

    kind: RowKind
    row_id: int
    cash_amount: Decimal
    version_id: int

    @property
    def subject(self) -> "tuple[RowKind, int]":
        """Return the ``(kind, row_id)`` key this row is identified by.

        The same pair :func:`~._candidates.matched_subjects` and
        :func:`~._candidates.unmatched_rows` key on, so a submission, an offer
        set and a claim all say WHICH ROW the same way.
        """
        return (self.kind, self.row_id)

    @property
    def token(self) -> str:
        """Return this row as the single form value the screen renders.

        **The wire format is stated ONCE, here, in both directions** -- this
        property writes it and :meth:`from_token` reads it.  A template
        composing the four fields itself and a schema field taking them apart
        would be two spellings of one format, which is this arc's own root
        cause 1: the pair that has to agree is a template and a validator, and
        nothing in the tree would fail when they stopped.

        **The figure is formatted with** ``:f`` **rather than** ``str()``,
        which is not cosmetic: ``str(Decimal("1E+3"))`` is ``"1E+3"`` and
        :data:`_FIGURE` refuses it, so a producer that ever handed back a
        positive-exponent Decimal would make its own token unreadable and
        refuse a match nobody had touched.  ``:f`` has no exponent form.

        Returns:
            ``"<kind>:<row_id>:<cash_amount>:<version_id>"``.
        """
        return _SEPARATOR.join((
            self.kind.value,
            str(self.row_id),
            spell_figure(self.cash_amount),
            str(self.version_id),
        ))

    @classmethod
    def from_token(cls, raw: str) -> "ReviewedRow":
        """Return the row *raw* names, refusing anything it does not.

        **Total over every ``str``**: no submitted value reaches an ``int()``,
        a ``Decimal()`` or an unpack that could raise past this function, which
        is what a form door needs in an application whose error handlers
        register no ``ValueError`` arm.  ``apply=%C2%B2`` was a 500 on this
        very screen once (:func:`~app.schemas.validation._helpers.order_token_key`),
        and this is the same lesson one field over.

        Args:
            raw: One submitted ``match-<index>-rows`` value.

        Returns:
            The :class:`ReviewedRow` it names.

        Raises:
            ValueError: When *raw* is not a token this application emitted.
                The schema field is what turns it into a 400; nothing else
                calls this.
        """
        if not isinstance(raw, str):
            raise ValueError("a reviewed row must be submitted as text")
        parts = raw.split(_SEPARATOR)
        if len(parts) != _TOKEN_FIELDS:
            raise ValueError("a reviewed row names four fields")
        kind_value, row_id, figure, version_id = parts
        try:
            kind = RowKind(kind_value)
        except ValueError as exc:
            raise ValueError("that is not a kind of row") from exc
        cash_amount = parse_figure(figure)
        # **BOTH counters through the schema layer's own reader**, which is the
        # rule plan step X-ae imposed on every submitted digit string (finding
        # **N-136**): ``str.isdigit`` is true for 888 characters, 128 of which
        # make ``int()`` raise, and an ASCII digit run does not license the
        # conversion either -- CPython refuses one past 4,300 digits.  A
        # ``version_id`` is the same shape as a row id and starts at 1 for the
        # same reason (``ck_*_version_id_positive``), so it takes the same
        # reader rather than a second, laxer one beside it.
        parsed_id = parse_row_id(row_id)
        parsed_version = parse_row_id(version_id)
        if parsed_id is None or parsed_version is None:
            raise ValueError("that is not a row this page could have shown")
        return cls(
            kind=kind,
            row_id=parsed_id,
            cash_amount=cash_amount,
            version_id=parsed_version,
        )

    def disagrees_with(self, row: CandidateRow) -> "str | None":
        """Return why *row* is no longer what was reviewed, or ``None``.

        **The reconciliation finding N-336 says nothing performs**, stated once
        and here so the door reports it rather than composing the comparison at
        its one call site.  It answers a SENTENCE rather than a boolean because
        ruling **R-FZ(a)** requires each refused item to be quoted in the
        service's own words: an item that refuses has to say WHAT moved, or the
        owner learns only that something did and has no way to tell a stale tab
        from a bug.

        The figure is compared with ``!=`` on :class:`~decimal.Decimal`, which
        is a VALUE comparison -- ``Decimal("178.3") == Decimal("178.30")`` is
        ``True`` -- so a scale that differs between the render and the re-read
        cannot invent a disagreement.  :data:`_FIGURE` is what keeps a ``NaN``
        out of this test, where ``!=`` would be true against everything.

        Args:
            row: The same row as it stands NOW
                (:func:`~._valuation.repriced`).

        Returns:
            One sentence naming what moved, or ``None`` when the row still
            agrees with what was reviewed on both coordinates.
        """
        if self.cash_amount != row.cash_amount:
            return (
                f'"{row.label}" was reviewed at {self.cash_amount} and now '
                f"stands at {row.cash_amount}"
            )
        if self.version_id != row.version_id:
            return f'"{row.label}" has been edited since this page was shown'
        return None


def as_reviewed(row: CandidateRow) -> ReviewedRow:
    """Return *row* as the state a submission would carry it back in.

    **The one place an offer becomes a reviewed state**, so the screen that
    emits the token and the door that checks it cannot describe a row
    differently.  It is a function here rather than a property on
    :class:`~._subjects.CandidateRow` because the dependency runs one way: this
    module knows what an offer is, and :mod:`._offers` must not have to know
    what a submission is.

    Args:
        row: The candidate the screen is about to render.

    Returns:
        Its :class:`ReviewedRow`.
    """
    return ReviewedRow(
        kind=row.kind,
        row_id=row.row_id,
        cash_amount=row.cash_amount,
        version_id=row.version_id,
    )


#: What separates the figure from the member it lands on in a
#: :class:`ReviewedDifference` token.  It cannot occur inside either half: the
#: figure is :data:`_FIGURE`'s alphabet, and a :attr:`ReviewedRow.token` is
#: two digit runs, a figure and a :class:`~._subjects.RowKind` value joined by
#: :data:`_SEPARATOR`.  **A second character rather than a second use of the
#: colon**, so the two halves read as two halves: ``-50.00@transaction:12:...``
#: is a figure AT a row, where ``-50.00:transaction:12:...`` would read as a
#: five-field row token, and :meth:`ReviewedRow.from_token` refuses those by
#: count.
_LANDING_SEPARATOR: str = "@"


@dataclass(frozen=True)
class ReviewedDifference:
    """The difference a match was reviewed against, and WHERE it was reviewed
    as going -- one value, because they are one decision.

    Plan step ``bank_import:X-gp``, ruling **R-BI2**, superseding **R-IV**.

    **The consent used to witness a PROJECTION of the act, and this is what
    makes it witness the act.**  Until this step the MATCH pane drew TWO
    controls for one decision: ``difference_on-<line>``, naming the member the
    gap belongs to, and ``residual-<line>``, a box whose value was the FIGURE
    and whose label was composed from the select at draw time.  The door
    compared only the figure.  Worked, on the developer's own case: a bank
    line of `-$793.23` against rows of `-$93.23` and `-$650.00`, a difference
    of `-$50.00`.  Leave the select alone and a NEW uncategorised `-$50.00`
    row is minted; name the `-$650.00` row and nothing is minted and that row
    is re-priced to `-$700.00`.  Both bodies submitted ``residual=-50.00``, so
    one consent stood for two acts with different consequences, and only a
    DOM event -- the select sat inside the element whose change re-rendered
    the box unticked -- kept a browser from pairing them.  **R-IV** accepted
    that bound on the verified strength of the event; ``X-gi-2a`` renders the
    pane on a page where nothing swaps, so the ground gave way and the
    widening R-IV refused is the one this class is.

    **The option the owner ticks IS the act.**  The pane renders one control
    whose options are the acts, each labelled with the figures it writes, and
    each option's value is one of these -- so the sentence the owner read and
    the value the door reads are one rendering of one value, and a body
    cannot pair the consent shown for *record it as a row with no category*
    with a landing that re-prices a budget row instead.  There is no second
    field for it to pair with.

    **It carries the DIFFERENCE and not the figure the act writes**, and the
    two are interchangeable only because every other input is pinned.  The
    figure a member option writes is *the bank total less the OTHER members*
    (:class:`~._landing.DifferenceLanding`); the bank total is a line's
    identity, and every member's figure is in the row tokens the same body
    carries, each reconciled by :func:`~._resolve._reject_moved_since_review`.
    So a difference plus a row set determines what is written, and stating
    the difference keeps :attr:`~._sides.MatchSides.difference` as the one
    figure the gate compares -- for the pane's options, for the hidden field
    an agreeing match carries, and for the figure a tier's unopened card
    states (:func:`app.jinja_filters.stated_difference`), which names no row
    because a match naming ONE row has nothing to choose.

    **A consent cannot outlive the row set it was drawn for, and the premise
    that makes that total is named because it is the one a later writer could
    break.**  A figure with no row means MINT for a group and WRITE-TO-THE-ROW
    for a lone row (:func:`~._landing._named_member`), and nothing in the
    value says which; what keeps one body's consent from meaning the other act
    is that adding or removing ANY row moves the difference, so the gate
    refuses the stale figure.  That holds only because no candidate is worth
    nothing: :func:`~._valuation.transaction_candidate` answers ``None`` at
    zero, and ``ck_transaction_entries_positive_amount`` is ``amount <> 0``.
    A zero-cash candidate would let a row join or leave a set under a consent
    that still compares equal.

    **A ``None`` row means what an absent attribution always meant**: for a
    match naming one row, ruling **R-GD**'s determinacy answers the question
    and the row is that one; for a group, nothing says which member and
    **R-FN**'s ordinary uncategorised row is what closes the gap.  The pane's
    *record it as a row with no category* option is spelled with a ``None``
    row for exactly that reason -- it is the act a group performed before any
    member could be named, and it takes no second spelling.

    Attributes:
        figure: The difference the screen showed, signed on
            :attr:`~._sides.MatchSides.difference`'s convention.  A
            PRECONDITION and never a payload: what the door writes is its own
            derivation, and this is only ever compared against it.
        on_row: The member the owner said the difference belongs to, as the
            screen showed that row, or ``None``.  A POINTER into
            :attr:`MatchSubmission.rows` carrying the whole reviewed value,
            held to be one of them by :func:`~._resolve.resolve_rows`, so an
            option rendered against a stale row is refused by the same guard
            that refuses the row.
    """

    figure: Decimal
    on_row: "ReviewedRow | None" = None

    @property
    def token(self) -> str:
        """Return this decision as the single form value the pane renders.

        **The wire format is stated ONCE, here, in both directions**, which is
        :attr:`ReviewedRow.token`'s rule one grain up: this writes it and
        :meth:`from_token` reads it, and the row half is
        :attr:`ReviewedRow.token` itself rather than a second spelling of it.

        Returns:
            ``"<figure>"`` where no member is named, else
            ``"<figure>@<kind>:<row_id>:<cash_amount>:<version_id>"``.
        """
        figure = spell_figure(self.figure)
        if self.on_row is None:
            return figure
        return f"{figure}{_LANDING_SEPARATOR}{self.on_row.token}"

    @classmethod
    def from_token(cls, raw: str) -> "ReviewedDifference":
        """Return the decision *raw* names, refusing anything it does not.

        **Total over every ``str``**, for :meth:`ReviewedRow.from_token`'s
        reason: nothing submitted reaches a ``Decimal()`` or an ``int()``
        that could raise past this function.  Both halves go through the
        readers that already grade them -- :func:`parse_figure` for the
        figure, so ``"NaN"`` cannot jam the gate open, and
        :meth:`ReviewedRow.from_token` for the row, so a lax id or revision
        inside the landing is refused exactly as it is inside ``rows``.

        Args:
            raw: One submitted ``consent-<line>`` value.

        Returns:
            The :class:`ReviewedDifference` it names.

        Raises:
            ValueError: When *raw* is not a token this application emitted.
                The schema field is what turns it into a 400; nothing else
                calls this.
        """
        if not isinstance(raw, str):
            raise ValueError("a reviewed difference must be submitted as text")
        parts = raw.split(_LANDING_SEPARATOR)
        if len(parts) > 2:
            raise ValueError("a reviewed difference lands on at most one row")
        figure = parse_figure(parts[0])
        if len(parts) == 1:
            return cls(figure=figure, on_row=None)
        return cls(figure=figure, on_row=ReviewedRow.from_token(parts[1]))


@dataclass(frozen=True)
class MatchSubmission:
    """What the owner accepted: the lines, and the rows AS THEY WERE REVIEWED.

    **Nothing here is written and that has not changed.**  Every figure and
    every day the door commits it re-derives from the rows the ids name, inside
    the same transaction, so a stale screen still cannot commit a figure the
    database no longer holds -- the same reason
    :func:`~app.services.reconcile_service._rows.record_settled` re-derives its
    ids through the arm's own scope rather than trusting them.  What
    :class:`ReviewedRow` adds is a PRECONDITION rather than a payload.

    **The rows are ONE collection rather than two id lists** (plan step
    ``bank_import:X-f6d-3``).  ``transaction_ids`` and ``entry_ids`` were the
    same fact discriminated by table, and a row and the state it was reviewed
    in are one fact, so they travel as one value and the ids are DERIVED from
    it (:attr:`subjects`).

    **The real alternative was NOT "two parallel lists", and an adversarial
    review was right that arguing against that one is arguing against a straw**
    (2026-08-23).  This POST already carries per-row attributes another way:
    :func:`~app.schemas.validation.statement_reconcile.reconcile_payload`
    keys them by row id IN THE FIELD NAME (``destination-<line_id>``,
    ``envelope_name-<line_id>``) -- so
    ``match-<i>-row-<kind>-<id>-figure`` and ``-version`` was available, needs
    no format of its own, and cannot desynchronise because the key IS the
    identity.  **What decided it is that a match names N rows where a creation
    names one line.**  Under the keyed shape the row SET is implied by which
    keys happen to be present, so a body carrying a figure with no version, or
    a version with no figure, is expressible and has to be refused by a rule
    reconciling two prefixes; here one field per row is atomic and its absence
    is simply a row not named.  The cost is this module's own format, its
    parser and their controls, which is a real cost honestly paid rather than
    an obvious win.

    **It names no OWNER and no ACCOUNT**, for the reason
    :class:`~._creations.PurchaseCreation` states: whose account this is, is
    the :class:`~._scope.ReviewScope`'s, and a second statement of it could
    disagree with the scope the rows were priced from.

    Attributes:
        line_ids: The bank lines to explain.  They carry no reviewed state,
            and the reason is narrower than it looks: ``posted_on`` and
            ``amount`` are a line's IDENTITY in
            ``statement_import._plan._reconcile``, which pairs an incoming
            line against a recorded one by exactly those, so neither can move
            under an open review.  What a re-import DOES write is the NULL
            provenance columns it can fill.

            **One of those feeds a write, and finding N-338 owns it.**
            ``transaction_on`` filled from ``NULL`` moves
            :attr:`~._offers.MatchDays.happened_on`, which
            :func:`~._offers.corrected_purchase_day` writes onto a matched
            purchase's ``purchased_on`` -- the one write releasing a match
            cannot undo.  It is the LINE-side twin of **N-336** and is
            deliberately NOT fixed here: whether a re-import should invalidate
            an open review is a ruling, not an omission, and the row's own
            remedy column carries the options.  What is bounded is the blast
            radius -- ``posted_first`` cannot move, so WHETHER a purchase is
            re-dated is stable and only the day it moves TO can shift.
        rows: The app rows that explain them, each as the screen showed it.
        consent: What the owner agreed to about the DIFFERENCE -- the figure
            the screen showed, and the member it lands on -- as one
            :class:`ReviewedDifference`, or ``None`` where they agreed to
            nothing (plan steps ``bank_import:X-f6d-4`` and ``X-gp``; rulings
            **R-FN** and **R-BI2**).

            **ONE value and not two fields, and that is the whole of plan step
            X-gp.**  It was ``accepted_difference``, a ``Decimal``, beside
            ``attributed_to``, a reviewed row, until then -- how MUCH and WHERE
            as two facts the door read separately, so that two acts with
            different consequences submitted one consent.
            :class:`ReviewedDifference` carries the argument.

            **Named for the CONSENT rather than for the row**, because it
            gates both of the two things a difference can become: the bank's
            figure written to the member the match names, and
            :class:`~._accept.AcceptedMatch`'s ``residual`` -- the
            uncategorized row a GROUP's leftover becomes.  Calling it
            ``residual`` here made one word mean two things in one call
            chain, on the two sides of a money gate.

            **It is the one thing on this class that is not per-row, and that
            is exactly why it has to travel.**  Every other precondition here
            is reconciled by
            :func:`~._resolve._reject_moved_since_review`, which compares each
            ROW against the state the screen described -- so no per-row figure
            can drift between render and Apply.  The difference is a
            subtraction OVER those rows, and no per-row guard can see that
            arithmetic being wrong: a page that says ``-0.06`` over a door
            that writes ``-1,006.00`` is finding **N-336** one tier up.
            :func:`~._variance.reject_unrecordable` is what reconciles it.

            Like everything else here it is a PRECONDITION and never a payload:
            the figure the door writes is its own
            (:attr:`~._sides.MatchSides.difference`), derived inside the same
            transaction from the rows the ids name; and its row half is a
            POINTER into :attr:`rows` held to be one of them by
            :func:`~._resolve.resolve_rows`, compared as a whole reviewed
            value, so a stale option is refused by the guard that refuses the
            row.

            **A ``None`` row inside it is what every surface but the Reconcile
            card's MATCH pane sends, and it means what an absent attribution
            always meant**: a match naming ONE row is answered by ruling
            **R-GD(a)**'s determinacy, and a group naming no member takes
            **R-FN**'s ordinary accepted row.
    """

    line_ids: "frozenset[int]"
    rows: "frozenset[ReviewedRow]"
    consent: "ReviewedDifference | None" = None

    @property
    def attributed_subject(self) -> "tuple[RowKind, int] | None":
        """Return which row the difference is attributed to, or ``None``.

        The ``(kind, row_id)`` key the offer set, the claims and
        :attr:`subjects` all say WHICH ROW with, so the door compares one
        spelling of a row identity rather than two.

        Returns:
            :attr:`ReviewedRow.subject` of the consent's row, or ``None``
            where no consent was given or it names no member.
        """
        if self.consent is None or self.consent.on_row is None:
            return None
        return self.consent.on_row.subject

    @property
    def subjects(self) -> "dict[tuple[RowKind, int], ReviewedRow]":
        """Return the reviewed rows keyed by the subject each one names.

        The lookup :func:`~._resolve.resolve_rows` needs twice -- once to say
        WHICH rows were submitted and once to reconcile each against what it
        finds -- built once here so the two cannot range over different sets.

        **A body naming one subject twice COLLAPSES here**, keeping whichever
        entry the set iterated last, so this mapping alone cannot be trusted to
        represent the submission.  :func:`~._resolve.resolve_rows` refuses that
        body by name before it reconciles anything, comparing this against
        ``len(rows)``.  It is not a hypothetical: two entries with one subject
        and DIFFERENT figures would otherwise pick one of them arbitrarily and
        check the guard against a state the sender chose, on a money door.
        """
        return {row.subject: row for row in self.rows}
