"""Where a match's difference LANDS, and what that member is then worth.

Plan step ``bank_import:X-gq``: a pure move out of :mod:`._variance`, which
stood at 999 of pylint's 1000-line ceiling with plan step
``bank_import:X-gp`` still to write the consent gate's new argument into it.
Nothing here changed in the move -- the three definitions are transplanted
byte for byte.

**THE ARGUMENT FOR ALL OF THIS IS STILL** :mod:`._variance`'s **module
docstring, and deliberately so.**  That prose is ONE argument -- what happens
to a difference: who may decide where it lands, and what gets written when
nobody does -- and it runs straight across the seam this split cuts on: six of
its eleven paragraphs describe the refusals and this landing at once.  Dividing
it would have meant rewriting it, and a rewritten paragraph is one a reviewer
cannot grade as unchanged.  So it was left whole, in the file that still holds
the refusals and :func:`~._variance.mint`.  **That leaves a design record
partly describing code it no longer sits beside**, which is a real cost and is
recorded as finding **bank_import:BI-484** rather than accepted silently.

What this module holds is the DECIDING half:

* :class:`DifferenceLanding` -- which member of a match absorbs the
  difference, and what the bank says that member is worth;
* :func:`corrected_figure` -- what that member's own column must then store,
  which differs on a purchase and on a transaction carrying entries;
* ``_named_member`` -- reading the owner's stated attribution off a
  submission, which is the only input this derivation is given.

The refusals that GATE these, and the :func:`~._variance.mint` that closes a
gap nobody attributed, stay in :mod:`._variance`.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import.  :func:`corrected_figure` READS --
a purchase's parent, for the sign rule -- and writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.services.cash_ledger import movement_figure_for
from app.utils.money import round_money

from ._subjects import CandidateRow, RowKind
from ._sides import MatchSides


@dataclass(frozen=True)
class DifferenceLanding:
    """Where a match's difference goes, decided ONCE for the whole act.

    **ONE rule, and it is the whole of ruling R-GD(a)'s determinacy:** the
    difference this door would write goes to the member the match ATTRIBUTES it
    to, and where it attributes it to none it becomes **R-FN**'s ordinary
    accepted row (:func:`~._variance.mint`).  A match attributes it

    * to its SOLE row where it names one -- nothing has to be apportioned, so
      the bank's figure is simply an assertion about that row;
    * to the member the OWNER NAMED where it names several (plan step
      ``bank_import:X-gj-3a``);
    * to NONE where it names several and the owner named no member, which is
      every match this screen submitted before that step.

    **The middle arm is the step's whole change, and it does not weaken the
    argument above it.**  :mod:`._variance`'s opening paragraph -- *three rows
    summing to one deposit, five cents short, is not five cents of error in one
    of them* -- is about what the DOOR can DERIVE, and it stands unamended:
    nothing here apportions and nothing here guesses.  What is new is that the
    owner may SAY which member the bank varied, and a stated attribution is a
    decision rather than a derivation.  The developer ruled the control on
    2026-09-01 and ruled its DEFAULT with it (**R-IU**): the panel offers NO
    pre-selected member.

    **The ruling that settles the default is R-HX, not R-FZ(b)**, and the
    difference is the one **R-HS** carves out in its own words: R-FZ(b) banned
    the ARBITRARY default and *never meant a suggestion the app can justify*,
    so citing it for a DERIVED pick would be citing the half that was carved
    away.  R-HX is on point -- *an unmatched inflow is not pre-filled until a
    rule can justify the destination*, and *being the ONLY act is not what
    R-HS means by justified*.  The app is not without a CANDIDATE here:
    ``salary_profiles.template_id`` names the member whose figure is computed
    (:func:`~app.services.income_service.salary_net_for` prices it, and every
    other member states its own price) so *which member rounds* is a stored
    fact.  What it lacked
    when the default was ruled is a JUSTIFICATION: `salary:N-391` read *NOT yet
    attributed*, ``bank_import:X-gj-3b`` was WITHDRAWN (**R-JJ**) and
    ``salary:R18`` dissolves the question: one deposit will name ONE row.  The
    attribution that came later (plan#2866, below) reopens nothing here, for
    that last reason.

    **It is a GENERALISATION of ``bank_cash_for`` rather than a second arm
    beside it, and the algebra is why that matters.**  That function answered
    :attr:`~._sides.MatchSides.bank` for a lone row and ``None`` otherwise.
    The figure here is *what the bank moved, less what the OTHER members come
    to* -- and for a lone row there are no others, so the subtrahend is zero
    and the answer IS the bank total, identically.  A version that had kept
    the old function and added an ``elif`` for the named member would have
    been two spellings of one money rule, which is this arc's own root cause 1.

    **It is stated as a subtraction from the BANK and not as an addition to
    the ROW, and the difference is a rounding one.**  ``row.cash_amount +
    sides.difference`` is the same value in exact arithmetic and is what a
    first version of this class computed -- but ``sides.app`` is ROUNDED
    (:class:`~._sides.MatchSides`), so on a row whose cash carried sub-cent
    places that spelling would return the bank's figure plus that row's own
    rounding error, where this one returns the bank's figure exactly.  Every
    figure the app can produce today descends from ``Numeric(12, 2)``, so the
    two agree on all of them; ``MatchSides``' own docstring is what says a
    derived price with more places is expressible, and a money rule that is
    right only until then is not right.

    **The two remedies stay EXCLUSIVE by construction rather than by care.**
    :attr:`bank_cash` is a figure exactly where a member absorbs the gap and
    ``None`` exactly where none does, so correcting a row and minting a member
    for the same difference is unrepresentable -- the property
    :func:`~._accept.record_match` used to get from ``bank_cash_for`` and which
    a first version of that step lost by gating the mint on consent alone.

    **The measured population is finding salary:N-391.**  On a production
    clone carrying the developer's own 376 recorded lines, seven payroll
    deposits are 2-3 app rows summing ``$0.04``-``$0.06`` under what the
    employer paid, ``+$0.35`` across the span.  Before this step the only thing
    this door could do with that gap was mint seven uncategorized rows.

    **WHICH member it belonged to was not established when this was written,
    and this class still derives no such thing for any match.**  An earlier
    draft of this paragraph said every cent of it belongs to the salary row;
    `N-391`'s own last sentence said the opposite -- *NOT yet attributed: a
    `$0.04` error in any of the 12 hand-entered deductions reproduces the same
    net, and the gross is n=1*.  What was
    measured on 2026-09-01 is narrower and is about the GROSS: the app derived
    a per-paycheck gross by dividing ``salary_profiles.annual_salary``
    (`$91,675.00` / 26 = `$3,525.96`) where the owner's stub states
    `$3,526.00`, and re-running all seven paychecks through
    :func:`~app.services.paycheck_calculator.calculate_paycheck` at the stub's
    figure collapses the span from ``+$0.35`` to ``+$0.07``.  That is a fact
    about the derivation and not a per-member attribution: the bank shows ONE
    deposit, so no per-member bank figure exists to compare an allowance
    against, and *the allowances match exactly* is ``bank less allowances =
    salary`` restated.  The gross half was ``salary:X-av``'s, which
    `N-391` names; since plan step ``salary:X-av-3a`` the salary is what one
    paycheck pays (its migration wrote the divided figure).

    **plan#2866 attributed it, on a same-day production copy, 2026-11-06.**
    Every saved stub's own figures add up to its deposit to the cent and its
    deductions agree with the app's line for line, so a mistyped deduction is
    not the cause.  On a stub's own payday its taxes are that stub's by
    construction (:func:`~app.services.paycheck_calculator._stubs
    .priced_taxes`), so that comparison grades no tax formula; what it does
    grade is that on the four stubs past the first raise the one line left to
    differ is BASE PAY: the walked pay list, four cents ABOVE what the employer
    prints, the sign having flipped at that raise because the employer's raise
    was not exactly the forecast's percentage.  A cent either way beyond that
    is read as the employer's -- two stubs with one gross and one set of lines
    print Medicare a cent apart -- and on the paydays with no stub it is
    inferred.  The first two paydays of the raise's month differ by dollars,
    not cents: the employer paid that raise by the hours worked after it, where
    the app prices every payday of the month at the whole raise (plan#3048);
    and where a forecast raise moves the state line, its rounding to the cent
    against the employer's whole dollars is plan#2682's.  The figures stay
    outside the repository.  While no later entry covers those paydays, a Fix
    of the first entry to the pre-raise pay WIDENS the gap, because the raise
    walks from it; the ruled way to state a raise received is a new entry
    (**R-SAL59**), plan step ``salary:X-av-3b``'s Record door.

    Attributes:
        on_row: The member the difference is written to, or ``None`` where it
            has none and :func:`~._variance.mint` is what closes the gap.  It is one of
            the very rows handed to :meth:`of`, so the caller cannot be given
            a row the act does not name.
        bank_cash: What the bank states :attr:`on_row` is worth, signed on
            :attr:`~._subjects.CandidateRow.cash_amount`'s own convention, or
            ``None`` beside a ``None`` row.  **The CASH figure and not the
            figure to store**: :func:`corrected_figure` is what inverts it into
            what the row's own column holds, which differs on a purchase and on
            a transaction carrying entries.
    """

    on_row: "CandidateRow | None"
    bank_cash: "Decimal | None"

    @classmethod
    def of(
        cls,
        sides: MatchSides,
        rows: "list[CandidateRow]",
        attributed: "tuple[RowKind, int] | None",
    ) -> "DifferenceLanding":
        """Return where this match's difference lands.

        **Total over every submission this door accepts**, including the ones
        with no difference at all: at zero what the bank left for the named
        row IS what that row already holds, so :func:`corrected_figure`
        answers ``None`` and an agreeing match writes nothing whichever arm it
        takes -- which is what the lone-row path did before this step and is
        why no arm here tests for zero.

        Args:
            sides: What the two halves come to, derived once for the whole act.
            rows: The match's app rows, already priced.  A match with none is
                refused before this runs
                (:func:`~._accept._reject_empty_side`).
            attributed: The ``(kind, row_id)`` of the member the owner named,
                or ``None``.  Held to be one of *rows* by
                :func:`~._resolve.resolve_rows`, so a body naming a row this
                match does not carry is refused by name rather than falling
                through to the mint -- which would let the SENDER choose the
                remedy.  :func:`_named_member` raises rather than answering
                ``None`` if that guard is ever bypassed.

        Returns:
            The :class:`DifferenceLanding`.
        """
        row = _named_member(rows, attributed)
        if row is None:
            return cls(on_row=None, bank_cash=None)
        others = sum(
            (
                other.cash_amount for other in rows
                if (other.kind, other.row_id) != (row.kind, row.row_id)
            ),
            Decimal("0.00"),
        )
        return cls(on_row=row, bank_cash=round_money(sides.bank - others))

    @staticmethod
    def offers_a_choice(rows: "list[CandidateRow]") -> bool:
        """Return whether this match has a member for the owner to NAME.

        **ONE statement of ruling R-GD's determinacy, read by both sides**
        (plan step ``bank_import:X-gj-3a``, second pass).  The door's
        :func:`_named_member` asks *is this already answered* to decide whether
        to consult the attribution, and the panel asks the same question to
        decide whether to render the control -- and a first version wrote it
        out twice, once here and once as ``len(rows) > 1`` in
        :func:`~._preview.preview_hand_build`.  Nothing in the tree fails when
        two spellings of one predicate diverge: widen the panel's and it offers
        a control the door ignores, widen the door's and it honours an
        attribution no panel could have rendered.

        Args:
            rows: The match's app rows, already priced.

        Returns:
            ``False`` where the match names one row -- there is nothing to
            apportion, so the answer is the row and no control is drawn --
            and ``True`` where it names several.
        """
        return len(rows) > 1

    @property
    def mints_a_row(self) -> bool:
        """Return whether a difference here becomes an ordinary accepted row.

        Returns:
            ``True`` where no member absorbs the difference, so **R-FN**'s row
            is what makes the group add up.  It says nothing about whether
            there IS a difference: the caller pairs it with
            :attr:`~._sides.MatchSides.difference`, exactly as
            :func:`~._accept.record_match` paired the old ``bank_cash is
            None``.
        """
        return self.on_row is None

    def figure_for(self, row: CandidateRow) -> "Decimal | None":
        """Return what *row* should book, or ``None`` where it does not move.

        **Asked of every member so the answer is one rule rather than a loop
        with a condition in it.**  A member that is not the attributed one is
        not re-priced, and the attributed one is re-priced only where the
        bank's figure differs from what it already holds -- both of which
        :func:`corrected_figure` already answers, given a ``None`` cash figure
        for the first.

        Args:
            row: One member of the match.

        Returns:
            The figure to submit to that row's settle verb, or ``None``.
        """
        if self.on_row is None:
            return None
        if (row.kind, row.row_id) != (self.on_row.kind, self.on_row.row_id):
            return None
        return corrected_figure(row, self.bank_cash)


def _named_member(
    rows: "list[CandidateRow]",
    attributed: "tuple[RowKind, int] | None",
) -> "CandidateRow | None":
    """Return the member a match attributes its difference to, or ``None``.

    **The lone-row arm is a DETERMINACY argument and not a default**, and the
    ruling that states it is **R-GD**'s fourth GROUP amendment, clause (ii) --
    *one ROW is determinate however many LINES explain it, so
    ``bank_cash_for``'s test is on the row rather than the lines*.  (Not
    R-GD(a), which is the different proposition that a match RECORDS the
    variance; the deleted ``bank_cash_for`` made the same conflation and it is
    corrected here rather than inherited.)  Where a match names one row the
    bank's figure is an assertion about that row and there is nothing to
    apportion, so the screen offers no choice and none is submitted.  The test
    was ``len(lines) != 1 or len(rows) != 1`` until plan step
    ``bank_import:X-f6d-4``, and the lines half has been gone since.

    Args:
        rows: The match's app rows, already priced.
        attributed: The ``(kind, row_id)`` the owner named, or ``None``.

    Returns:
        The member, or ``None`` where the difference has none.

    Raises:
        ValueError: When *attributed* names a row this match does not carry.
            **Unconstructible from the wire rather than defensive, and stated
            rather than left to fall out of a lookup's default**:
            :func:`~._resolve.resolve_rows` refuses a submission whose
            attribution is not one of its own rows, and refuses one whose rows
            do not all resolve -- so on every path a submission takes, an
            attributed subject IS a resolved row.  What this arm guards is the
            other caller of :func:`~._accept.record_match`, which builds its
            :class:`~._accept.MatchContent` in code.  Written as a raise
            because the alternative spelling, ``next(..., None)``, would fall
            through to the MINT: the remedy would have been chosen by naming a
            row that is not there, which is the shape ruling **R-IA** measured
            at `$2,572.36` one field over.
    """
    if not DifferenceLanding.offers_a_choice(rows):
        return rows[0]
    if attributed is None:
        return None
    for row in rows:
        if (row.kind, row.row_id) == attributed:
            return row
    raise ValueError(
        "A match's difference names a row the match does not carry, which "
        "resolve_rows refuses for every submission."
    )


def corrected_figure(
    row: CandidateRow, bank_cash: "Decimal | None",
) -> "Decimal | None":
    """Return the figure *row* should book to move its cash onto the bank's.

    **The bank constrains the CASH LEG, and a transaction's figure IS its
    cash leg unsigned** (ruling **R-BAL81**, plan step ``balance:X-bi-4a``):
    a settled row is worth what its covering movement moves and a projected
    one what settling it would book, signed by the transaction TYPE
    (:func:`~app.services.cash_ledger.cash_leg_of`), and no row this arm
    prices holds a purchase -- a row settling from its purchases is worth
    ``0`` to the offer and is not a candidate, and a stated figure beside
    purchases is unrepresentable (ruling **R-BAL78**).  So the inversion is
    ``|bank|``.  Through ``X-bi-4a``'s first cut it was ``|bank| +
    off_statement_sum``, the inverse of a row leg that subtracted card and
    posted purchases, and that term met a settled row's own covering
    movement -- summed as a posted purchase by a reader that walked the
    family -- and booked ``bank + figure`` onto it: a `$178.32` bill matched
    to the bank's `-178.29` re-recorded its movement at `356.61` and the
    post-apply check refused the act (finding **BAL-523**, measured on
    production's own release 2026-09-18 and confirmed by the developer on the
    reconcile screen the same evening).  The sum reads the row's purchases
    now, which is ``0`` for every row here by construction, and the term is
    deleted rather than kept at zero: a dead term under a live docstring is
    the shape ``cash_leg_of`` shed in the same step.

    **A PURCHASE stores its figure directly** -- its cash is its stored
    amount in its PARENT's direction
    (:func:`~app.services.cash_ledger.movement_cash_leg`, read by
    :func:`~._valuation.purchase_candidate`) -- so its correction is that
    rule INVERTED, :func:`~app.services.cash_ledger.movement_figure_for`:
    ``-bank_cash`` under an expense row, which every purchase this arm can
    reach today sits under.

    **It was ``abs(bank_cash)`` until plan step ``bank_import:X-gj-2b``, and
    the two agree only for an OUTFLOW.**  While every purchase was positive its
    cash was negative, so the magnitude and the negation were the same number
    and the simpler spelling was true.  Ruling **R-II** made a merchant refund a
    NEGATIVE purchase, whose cash is POSITIVE -- and there ``abs()`` returns
    ``+X`` where the stored figure must be ``-X``, flipping a refund into a
    charge of the same size.  The inverse reduces to the old expression for
    every outflow, so no already-correct case moves.  It was spelled
    ``-bank_cash`` here until plan step ``balance:X-bi-3b`` -- the expense
    arm alone, a second spelling of the direction rule (rule 14).

    Args:
        row: The member the bank's figure is about.
        bank_cash: What the bank states, signed, or ``None`` for a group.

    Returns:
        The figure to submit, or ``None`` when nothing should be submitted --
        a group or an unchanged figure.  A row whose figure is not its own to
        state (a CC payback; an envelope holding purchases is never a
        candidate, ruling **R-BAL81**) never reaches here:
        :func:`~._variance._reject_uncorrectable_row` has already refused it.
    """
    if bank_cash is None or bank_cash == row.cash_amount:
        return None
    if row.kind is RowKind.PURCHASE:
        entry = db.session.get(TransactionEntry, row.row_id)
        return round_money(movement_figure_for(entry.transaction, bank_cash))
    # **The row's arm keeps ``abs()`` and that is not an oversight.**  A
    # transaction -- and a SETTLEMENT, which is a row's record written through
    # the row's door (plan step ``credit_card:CC-5-4a-1``) -- states a GROSS,
    # non-negative figure (``estimated_amount >= 0``;
    # ``status_seam.Settlement`` refuses a negative record) whose direction
    # comes from the transaction TYPE rather than from the figure, so the
    # magnitude really is what it should book.  Only a PURCHASE stores a
    # signed amount.
    return round_money(abs(bank_cash))
