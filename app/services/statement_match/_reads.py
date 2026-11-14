"""What the Reconcile page's INBOX shows: unmatched lines, proposals, and what agrees.

*It said "the review screen" until plan step ``bank_import:X-gi-3``.  That
screen went at ``bank_import:X-gi-2`` and this step deleted its model; what
this module produces is the pass, and :func:`~._reconcile.reconcile_page` is
its principal reader.*

Read-only, and separate from :mod:`._accept` for the reason every package here
splits that way: the write door and the reader answer different questions, and
a reader living inside the door is a reader nobody can call without one.

**It reports three things a bound would otherwise hide**, because a screen that
lists what it could explain and says nothing about what it could not reads as
a clean sweep:

* lines that predate the owner's pay calendar, which nothing can ever match --
  130 of the developer's own 378 lines, and listing them beside genuine
  failures would bury the ones worth acting on;
* days too crowded to search for groups, as the SEARCH reports them
  (:attr:`~._propose.ProposedMatches.crowded_days`);
* matches whose rows no longer carry the day the bank stated, which is what a
  later hand edit produces and what makes a match re-reviewable rather than
  quietly stale -- reported by :mod:`._accepted_view`, which the two SETTLED
  TABS render, since plan step ``bank_import:X-gf-2`` took accepted acts off
  this pass entirely (ruling **bank_import:R-GX**).  *That said "on the
  REGISTER" until ``bank_import:X-gi-3`` deleted it.*

Services-boundary discipline: reads only, plain data in, frozen dataclasses
out, no Flask import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.statement_import import BankStatementLine

from ._candidates import matched_subjects, unmatched_rows
from ._destinations import current_destinations
from ._offers import (
    BankLine,
    CandidateRow,
    MatchProposal,
)
from ._already_held import (
    ArrivalsAlreadyHeld,
    arrivals_already_held,
)
from ._bars import BarredLine, MerchantAnswers
from ._gaps import ReviewBounds, search_gap
from ._leftovers import CreatableLine, RecordableInflow, leftovers
from ._propose import propose
from ._scope import ReviewScope
from ._section import MerchantSection
from ._undisposed import inbox_partition
from ._verdict import ruled


@dataclass(frozen=True)
class CardSubject:
    """One bank line the Reconcile page renders a card for, and what claims it.

    Plan step ``bank_import:X-gj-1b``.  **Two fields and not two lookups**,
    because a caller that asked for the line and then asked separately whether
    a proposal claims it could get half an answer: the line resolved and the
    proposal missed, which is the state that renders a proposed card's rows as
    ordinary candidates and lets an untick look like a hand-built group.

    Attributes:
        line: The :class:`~._offers.BankLine` the card is about.
        proposal: The :class:`~._offers.MatchProposal` whose acceptance this
            card offers, or ``None`` for a line no tier paired -- an outflow
            the create door would take, a deposit, or a parked payment.
    """

    line: "BankLine"
    proposal: "MatchProposal | None"


@dataclass(frozen=True)
class ReviewSet:  # pylint: disable=too-many-instance-attributes
    """Everything the review screen needs, in one value.

    Pylint: too-many-instance-attributes (11/7) -- **eleven because the
    screen renders eleven distinct things**, not because the value wants
    splitting.  Nine are cards the owner reads and acts in;
    :attr:`declined_lines` annotates two of them; and
    :attr:`account_payments` is neither -- it is a pass-level MERCHANT set,
    carried here because the question it answers is asked of every CARD and
    cannot be answered from any of the lists (its own entry below argues the
    placement).  *This said NINE over eight cards, and was already wrong by
    one at plan step ``bank_import:X-gj-4c``, which added ``answered_never``
    without re-counting; ``X-gj-4b`` added the eleventh.*  Re-counted off the
    field block rather than incremented, which is the discipline
    :class:`~._leftovers.Leftovers` states for its own count and this file did
    not keep.  Named by adversarial review 2026-09-04.  It was TEN until
    plan step ``bank_import:X-gf-2`` took the accepted matches off this screen
    (ruling **bank_import:R-GX**): they are not a decision anyone is making,
    and folding them cost this pass a valuation of all 221 acts on the
    developer's own account to render a panel he was not reading.

    **``bank_import:X-gf-3a`` did NOT make it ten.**  A first version added a
    ``rule_verdicts`` map keyed by line id; adversarial design review
    2026-08-27 pointed out that a per-LINE fact belongs on the per-line value,
    where :attr:`~._leftovers.CreatableLine.placement` and
    :attr:`~._leftovers.RecordableInflow.withheld` already are -- which also
    deleted the map, its accessor, and a ``field(default_factory=dict)`` whose
    default meant *not asked yet* in a value whose own docstring forbade that
    reading.

    The obvious way to satisfy the limit is to fold ``declined_lines``
    back into :attr:`bounds`, where it lived until plan step
    ``bank_import:X-f6d-3`` -- and that is exactly what the step measured to be
    wrong, because a bound reported in a panel names no line and cannot be
    acted on.  The other obvious way is to fold :attr:`parked` back into
    :attr:`creatable` and let a Jinja condition withhold the control, and that
    is what ruling **R-GJ** exists because of: a sentence saying *nothing here
    records it* sat over a working select for as long as those two were one
    list.  ``AcceptedMatch`` carries the same disable for the same reason, and
    its docstring says what dropping a field to meet a limit costs: the receipt
    said *"Nothing moved."* over a rewritten figure.

    Attributes:
        proposals: What the app believes goes with what, best first.
        unmatched: Bank lines inside the pay calendar that no proposal
            explains, ascending by day.
        unmatched_rows: The app's OWN rows that no proposal explains, over the
            span the recorded statements cover -- ruling **R-FP**'s other side,
            and the more valuable half for a budget: a row the bank never
            showed *and would have shown separately* is a payment the records
            claim happened and the bank did not make.  **That qualifier is
            load-bearing and was missing until plan step ``bank_import:X-gc``**;
            :attr:`~._subjects.CandidateRow.not_shown_alone` is where the screen
            withdraws the claim for a row whose money the bank accounts for
            through some other row, and the membership of this list is
            deliberately unchanged by it -- it is also the hand-build form's
            row-picker, and ruling **R-GJ** leaves the group match as a parked
            card payment's only arm.  They are
            :class:`~._subjects.CandidateRow` values rather than a type of their
            own; a second record carrying the same five fields was reported by
            pylint's cross-file ``duplicate-code`` and was exactly rule 13's
            speculative shape.
        creatable: The unmatched lines that may become a PURCHASE, each with
            the budget lines it could become one against
            (:class:`CreatableLine`).  A SUBSET
            of ``unmatched`` rather than a partition of it, and deliberately:
            the same line is offered to the hand-build form as something to
            GROUP and to the create door as something to RECORD, because those
            are different acts on the same fact and the owner is the one who
            knows which it is.  **INFLOWS ARE PRESENT, and which ones is
            :func:`~._rules.pipeline_for`'s answer rather than the sign's**
            (ruling **bank_import:R-II**, plan step
            ``bank_import:X-gj-2b-2``).  This held only outflows while
            ``ck_transaction_entries_positive_amount`` said ``amount > 0`` and
            the create door refused every inflow by sign; that CHECK is
            ``amount <> 0`` now, so a merchant credit IS a purchase the table
            can hold -- a NEGATIVE one, against the container that merchant's
            rule names -- and the inflows here are exactly the ones a container
            answer claims.  Every other inflow is in
            :attr:`recordable_inflows`.  **A line that ruling R-GJ bars is not
            here** -- it is in :attr:`parked` or :attr:`answered_never` -- so
            this list is exactly the lines a create control may be rendered
            for, and the screen cannot render one for a line the door would
            refuse.  *That was one list until plan step
            ``bank_import:X-gj-4c``*, and both halves are still out of this
            one: which of the two a barred line is in decides where it
            RENDERS, never whether the create door would take it.
        parked: The unmatched lines a source files as paying an account the
            owner holds, IN BOTH DIRECTIONS (it said OUTFLOW until plan step
            ``bank_import:X-gj-2b-3``; see :attr:`~._leftovers.Leftovers.parked`,
            which this is assigned from verbatim), with
            the reason each may not become a purchase
            (:class:`~._bars.BarredLine`, ruling **R-GJ**).  They are still in
            ``unmatched``, so the group-match arm the ruling leaves open is
            reached exactly as it was.

            **It held BOTH of ruling R-GJ's arms until plan step
            ``bank_import:X-gj-4c``** -- a merchant the owner answered *never a
            purchase* and a merchant a source files as an account payment --
            and ruling **bank_import:R-JH** separated them, because only the
            second is a disposition.  A line carrying BOTH bars is here, since
            the money did move between two of the owner's accounts whatever
            else they said about it.
        answered_never: The unmatched lines barred ONLY by the owner's own
            *never a purchase* answer (:class:`~._bars.BarredLine`, ruling
            **bank_import:R-JH**, plan step ``bank_import:X-gj-4c``).  **Inbox
            work, and that is the whole of the ruling**: the answer shuts the
            ADD door and claims nothing about what the line IS, since a
            paycheck is not a purchase either -- so these are rendered under
            *Nothing suggested* keeping MATCH, rather than filed as though the
            owner had disposed of them.  A SUBSET of ``unmatched`` like the
            two lists above it.
        recordable_inflows: The unmatched INFLOW lines, each with the period
            that would hold it (:class:`RecordableInflow`, ruling **bank_import:R-GW**).
            The mirror of ``creatable`` on the direction that had no door at
            all until plan step ``bank_import:X-gf-1``: no inflow could be a
            purchase then, and a match needs an app
            row on the other side -- which left `$58.87` of the developer's own
            deposits, in eight lines, with no act the screen could offer.
            Like ``creatable`` this is a SUBSET of ``unmatched`` and not a
            partition: the same deposit is offered here as something to RECORD
            and in the hand-build form as something to MATCH, because those are
            different acts on one fact.  **An inflow CAN be barred out of it, and
            that is a behaviour change plan step ``bank_import:X-gj-2b-3``
            states rather than leaves to be discovered.**  This read *nothing
            is barred out of it: neither arm has anything that could be true of
            money arriving* -- the argument ``bf500943`` measured FALSE for
            ``PAYS_AN_ACCOUNT_YOU_HOLD``, which is a claim about the MERCHANT.
            A credit from a card-payment merchant that ALSO carries a stored
            spending answer routes to PURCHASE, meets
            :func:`~._bars.reject_barred_line`, and is PARKED -- so it reaches
            neither this list nor a create control, and the hand-build match is
            its only act.  **That is the intended outcome** (recording it as
            income is the double count against the card transfer ruling
            **R-GJ** exists to stop), and the sentence *the owner has an act
            for it either way* was false of exactly that line.  What leaves
            this list WITHOUT being barred is a deposit a container answer
            claims: it is a refund, it is in ``creatable``, and that is a
            routing decision.
        merchants: The queue's rule control
            (:class:`~._section.MerchantSection`) -- the merchants this pass
            has an unexplained outflow for and the owner has NEVER answered
            about, which is a decision they owe.  **It counts EVERY list a
            line can land in**, because a merchant a source files as an account
            payment is barred for want of an answer and this is the control
            that gives one.  *It said* ``parked`` *beside* ``creatable`` *until
            plan step ``bank_import:X-gj-4c``*, when ``answered_never`` became
            a third: those lines were inside ``parked`` and therefore already
            counted, so naming two lists after the split would have narrowed
            this control silently.  An ANSWERED merchant is on the merchants
            list instead (rulings **bank_import:R-GX** and **R-IC**).
        bounds: What this pass did NOT look at (:class:`ReviewBounds`).
        account_payments: The merchant row ids this account's sources file as a
            payment to an account the owner holds, carried up from
            :attr:`~._leftovers.Leftovers.account_payments` verbatim (plan step
            ``bank_import:X-gj-4b``).  **It is the only fact here that is about
            every card rather than about one of the lists**, and that is why it
            is a set on the pass instead of a field on a line value: ruling
            **bank_import:R-JI** shuts SKIP for these merchants, and the lines
            it has to shut it for include the ones a tier PROPOSED a match for,
            which are in :attr:`proposals` and in none of the barred lists.
            :func:`~._cards._offers` is the one reader, and this is the one
            statement of the argument it points at.
            **The obvious narrower spelling is wrong**:
            :attr:`~._bars.BarredLine.also_pays_an_account` answers this for
            the two BARRED lists only, and a line a tier has proposed a match
            for never meets :meth:`~._bars.CreationBars.bar_for` at all.
            **ONE of the two populations it was widened for is gone, and the
            set stays because the OTHER one is untouched** (plan step
            ``bank_import:X-gm``).  The argument for reaching past the barred
            lists was that a line a tier PROPOSED a match for is in
            :attr:`proposals` and in neither of them.  An OUTFLOW from such a
            merchant can no longer be proposed --
            :func:`~._undisposed.inbox_partition` takes the holding states off
            the proposer (ruling **R-HQ**) -- but an INFLOW from one still can:
            a deposit no container answer claims routes to INCOME
            (:func:`~._rules.pipeline_for`), so it is not a holding state, it
            is in the inbox, and it reaches this pass as a
            :class:`~._leftovers.RecordableInflow` rather than a
            :class:`~._bars.BarredLine`.  :attr:`~._bars.BarredLine
            .also_pays_an_account` cannot answer for such a line AT ALL, which
            is why the narrower spelling is still wrong and this set is still
            the one :func:`~._cards._offers` asks.  It is also the DOOR's own
            set (:func:`~._vocabulary.account_payment_merchants`, read once per
            pass), so a card builder added later shuts the verb by asking the
            question :func:`~._skipping.skip_line` asks rather than by
            remembering a list.  Shutting the verb anywhere but on this set
            renders a control the door refuses, which is ruling **R-GJ**'s
            `$7,412.94` shape one verb over.
            *An earlier draft of this paragraph cited :mod:`._bars`' seven Van
            Loan lines as the populated case and that was the WRONG SET* --
            that module records four of them as already MATCHED and three as
            falling before the pay calendar opens, and a matched line is out of
            the pass altogether.  Named by adversarial review 2026-09-04.
            **A line naming NO merchant is never shut**, and that is
            :meth:`~._bars.CreationBars.bar_for`'s own totality rather than a
            guard restated by a reader:
            :func:`~._vocabulary.account_payment_merchants` filters
            ``merchant_id.isnot(None)``, so ``None`` is absent from this set
            and falls through to the same answer a branch for it would give.
            The DOOR asks the same question the same way.
            **No default**, for :attr:`~._bars.BarredLine.also_pays_an_account`'s
            own reason: an empty set means *nothing is barred*, which is the
            value that reads as safe and would open a verb the door refuses.
        declined_lines: WHAT THIS PASS CONSIDERED and would not conclude
            about, by line id, in the words of the tier that declined
            (:attr:`~._propose.ProposedMatches.declined_lines`).  It carried
            only the near tier's CONTEST until plan step
            ``bank_import:X-ge-1``; it now carries every rejection a tier makes
            after admitting the figure, because a bound a tier applies and does
            not report is one nothing can see.

            **It rides on the SET rather than in the bounds panel** (plan step
            ``bank_import:X-f6d-3``).  It was a count under *What this page did
            not look at*, which named no line -- so the owner was told that
            somewhere among a hundred lines one had a near candidate, with no
            way to find it.  The act it should prompt belongs to ONE line and
            is offered in two cards: build this one by hand, rather than record
            it a second time from the create arm, which is exactly the
            duplicate **N-335** measures.  The screen asks membership per line;
            the count is ``len`` and nothing needs it.
    """

    proposals: "tuple[MatchProposal, ...]"
    unmatched: "tuple[BankLine, ...]"
    unmatched_rows: "tuple[CandidateRow, ...]"
    creatable: "tuple[CreatableLine, ...]"
    parked: "tuple[BarredLine, ...]"
    answered_never: "tuple[BarredLine, ...]"
    recordable_inflows: "tuple[RecordableInflow, ...]"
    merchants: MerchantSection
    bounds: ReviewBounds
    account_payments: "frozenset[int]"
    declined_lines: "dict[int, str]" = field(default_factory=dict)

    def card_subject(self, line_id: int) -> "CardSubject | None":
        """Return the line this pass renders a card for, and what claims it.

        Plan step ``bank_import:X-gj-1b``.  **The Reconcile page's own
        membership question, asked of the pass that drew the cards.**  A route
        serving one card's fragment has to answer *is this line one I rendered
        a card for* -- and answering it with :attr:`unmatched` alone is wrong
        by construction, because :func:`_unexplained` takes every proposal's
        line OUT of that list before it exists.  Measured 2026-08-30 on a
        restored production clone: **137 of the 137** cards the developer's
        account proposes resolve to nothing in :attr:`unmatched`, so every one
        of their MATCH panes answered 404 and its spinner never resolved.

        **It is a membership test and never a second ownership check**, which
        is the distinction the retired workbench's own preselection drew: the
        pass is already scoped to one owner's one
        account, so a line it does not hold has no card here whatever the
        reason.  What the measurement above shows is the hazard in that --
        a 404 from the URL map, a 404 from ownership and a 404 from asking the
        WRONG SET are indistinguishable to everything except a reader
        ([[feedback_a_moved_door_disarms_its_ownership_control]] one surface
        over) -- so the set lives here beside the two lists it is the union
        of, rather than being spelled again by each caller.

        Args:
            line_id: The bank line the card is about, already an ``int``.

        Returns:
            Its :class:`CardSubject`, or ``None`` where this pass renders no
            card for that line -- someone else's line, one another match has
            already claimed, or one outside the pay calendar.
        """
        for line in self.unmatched:
            if line.line_id == line_id:
                return CardSubject(line=line, proposal=None)
        for proposal in self.proposals:
            for line in proposal.lines:
                if line.line_id == line_id:
                    return CardSubject(line=line, proposal=proposal)
        return None

    def arrivals_already_held_in(
        self, line: BankLine,
    ) -> ArrivalsAlreadyHeld | None:
        """Return what the books already hold for *line*'s period, or ``None``.

        The per-line safeguard on an unexplained INFLOW
        (:class:`ArrivalsAlreadyHeld`).  **A method over
        :attr:`unmatched_rows` rather than a field on
        :class:`~._leftovers.RecordableInflow`**, because those rows are
        derived AFTER the leftovers are split -- and a value built from a
        second read of them could disagree with the list the hand-build form
        renders, which is where the owner is being sent.

        **The period is tested by the row's own SPAN** (``expected_on`` ..
        ``expected_through``), which for a transaction IS its period; the
        candidate carries the period whole since plan step
        ``bank_import:X-gz``, and :func:`~._already_held.arrivals_already_held`
        says why the test stays on the span.

        Args:
            line: A recordable inflow's bank line.

        Returns:
            The :class:`ArrivalsAlreadyHeld`, or ``None`` when this period's
            books hold nothing arriving that no line explains -- which is the
            state that
            makes recording safe, and the screen says nothing rather than
            saying it is fine.
        """
        return arrivals_already_held(self.unmatched_rows, line)

    def search_gap_for(self, line: BankLine) -> "str | None":
        """Return why this pass cannot say *line* has no counterpart, or ``None``.

        The screen's spelling of :func:`~._verdict.search_gap`, which holds the
        derivation and the whole argument for it.  It moved out of this class
        at plan step ``bank_import:X-gf-3a`` so the rule verdict could ask the
        same question of the same pass without importing this value -- and one
        spelling is the point of the move rather than a side effect of it: the
        sentence the screen prints beside a line and the sentence ruling
        **R-GH**'s automatic door withholds on are the same sentence.

        Args:
            line: The bank line, which must be one this pass considered.
                **THREE surfaces take it off three different lists**, and the
                claim that "every caller takes it off :attr:`creatable`" was
                already false when it was written: the queue's OUTFLOW rows
                read it off :attr:`creatable`, the hand-build form off
                :attr:`unmatched` (which is the only one an inflow used to
                reach), and since ruling **bank_import:R-GW** its INFLOW
                rows off :attr:`recordable_inflows`.  What every caller does share is
                that the line was in THIS pass, which is what makes
                :attr:`declined_lines` answerable for it.

        Returns:
            One sentence naming the gap, or ``None`` when this pass searched
            exhaustively for a counterpart to *line* and found none.
        """
        return search_gap(
            line,
            self.declined_lines,
            self.bounds.crowded_days,
            self.bounds.unpriceable_count,
        )


def _covered_span(account_id: int) -> tuple[date, date] | None:
    """Return the first and last day this account has a recorded line for.

    Every RECORDED line, matched or not: the span a statement covers is a fact
    about what the bank sent, and it must not move as the owner works through
    the matches.

    Args:
        account_id: The account.

    Returns:
        ``(first, last)``, or ``None`` when nothing is recorded.
    """
    bounds = db.session.query(
        db.func.min(BankStatementLine.posted_on),
        db.func.max(BankStatementLine.posted_on),
    ).filter(BankStatementLine.account_id == account_id).one()
    return None if bounds[0] is None else (bounds[0], bounds[1])


def _could_have_been_shown(
    row: CandidateRow, covered: tuple[date, date] | None,
) -> bool:
    """Return whether the statement could have shown *row*'s movement.

    **It asks the row's own WINDOW** -- the days the app believes that money
    moved between (:attr:`~._subjects.CandidateRow.expected_window`) -- and the
    two overlap or they do not.  It used to test one day, ``settled_on or
    expected_on``, which is that accessor's own rule written a second time and
    one end short: a bill budgeted across the statement's opening day was
    dropped from the list because its period STARTS earlier, while every rule
    beside it had learned that a bill occupies a fortnight.  Found by
    adversarial design review 2026-08-19.

    Args:
        row: The candidate.
        covered: The recorded span, or ``None`` when nothing is recorded.

    Returns:
        Whether the two spans overlap.  A row the app can date no way at all is
        IN: there is no basis for excluding it, and saying so is better than
        dropping it silently -- the opposite of the proposer's answer for the
        same row, and deliberately, because this list is a REPORT and that one
        is a money door.
    """
    if covered is None:
        return False
    window = row.expected_window
    if window is None:
        return True
    return window[0] <= covered[1] and window[1] >= covered[0]


def as_bank_line(row: BankStatementLine) -> BankLine:
    """Return *row* as the value the proposer and the screen share.

    **PUBLIC since plan step ``bank_import:X-gj-4c-2``, and the promotion is
    ``CLAUDE.md`` rule 14 rather than a widening for its own sake.**  A second
    reader needs it -- :func:`~._skipping.skipped_acts`, which lists the
    recorded skips the Skipped tab renders -- and every one of its eight fields
    would otherwise be spelled a second time, including the ``Decimal(str(...))``
    that keeps a float off a money value.  It stays HERE rather than moving
    beside :class:`~._offers.BankLine`, which is the placement rule 14 asks for
    first: :mod:`._offers` stands at 996 lines against pylint's 1,000-line
    ceiling, so taking it would owe that module a split -- and ruling
    **balance:R-IR** puts a split on the session that BREAKS the module, which
    this one does not.  Recorded so the next reader knows the home is a
    constraint rather than a judgment.

    Args:
        row: A recorded line.

    Returns:
        Its :class:`~._offers.BankLine`.
    """
    return BankLine(
        line_id=row.id,
        posted_on=row.posted_on,
        amount=Decimal(str(row.amount)),
        description=row.description,
        transaction_on=row.transaction_on,
        merchant_id=row.merchant_id,
        merchant=row.merchant_name,
        source_category=row.source_category,
    )


def _rows_the_bank_never_showed(
    offerable: "list[CandidateRow]",
    proposals: "tuple[MatchProposal, ...]",
    account_id: int,
) -> "tuple[CandidateRow, ...]":
    """Return the app's own rows the statement could have shown and did not.

    Ruling **R-FP**'s other side, and the more valuable half for a budget: a row
    the bank never showed, and would have shown as a line of its own, is a
    payment the records claim happened and the bank did not make.

    **This answers "did any line explain it", never "should the bank have shown
    it separately"**, and conflating the two is what plan step
    ``bank_import:X-gc`` corrected on the screen rather than here.  A CC
    payback's money leaves inside one payment to the card and an envelope's
    inside its own purchases, so neither is ever a line of its own -- and both
    stay in this list, because it is the hand-build form's row-picker and those
    paybacks are what a parked Capital One line is grouped against.  The screen
    withdraws the inference per row through
    :attr:`~._subjects.CandidateRow.not_shown_alone`; withdrawing MEMBERSHIP
    would have closed ruling **R-GJ**'s only remaining arm.

    **The span is every RECORDED line's, not the unmatched ones'.**  Taking it
    from the leftovers made the window SHRINK as matches were accepted --
    matching the earliest or latest line silently dropped app rows from the
    list, and matching every line left no span at all -- while the card went on
    claiming these "fall inside the span your statement covers".

    **A row is measured on the WINDOW the app expects it in**, which is its
    settle day where it has one and its projection's span where it does not.
    Using "undated is always in" put every forward projection on the account
    into the list: 712 rows on the developer's own, most of them dated months
    ahead.  A projection the bank could not yet have shown is not a payment the
    bank failed to make.  Both found by adversarial review 2026-08-17.

    Args:
        offerable: The candidate rows no accepted match has claimed.
        proposals: What this pass proposes, whose rows are already explained.
        account_id: The account, for the recorded span.

    Returns:
        The rows, in the candidate order.
    """
    spoken_for = {
        (row.kind, row.row_id)
        for proposal in proposals for row in proposal.rows
    }
    covered = _covered_span(account_id)
    return tuple(
        row for row in offerable
        if (row.kind, row.row_id) not in spoken_for
        and _could_have_been_shown(row, covered)
    )


def _unexplained(
    bank_lines: "list[BankLine]", proposals: "tuple[MatchProposal, ...]",
) -> "list[BankLine]":
    """Return the lines no proposal in this pass accounts for.

    Args:
        bank_lines: Every line inside the calendar that no act has answered --
            neither an accepted match nor a recorded SKIP (plan step
            ``bank_import:X-gj-4a``).  *It said "no accepted match already
            explains" until that step, which is one of two answers rather
            than the only one.*
        proposals: What this pass proposes.

    Returns:
        The leftovers, in the order given.
    """
    explained = {
        line.line_id for proposal in proposals for line in proposal.lines
    }
    return [line for line in bank_lines if line.line_id not in explained]


def _already_held_by_line(
    creatable, never_shown,
) -> "dict[int, ArrivalsAlreadyHeld]":
    """Return the double-count answer for every creatable INFLOW.

    Plan step ``bank_import:X-gj-2b``.  **Asked of EVERY creatable line, with
    no direction guard, because the predicate is already total.**  A first
    version skipped outflows; adversarial review measured that skip an
    EQUIVALENT MUTANT and it is -- an outflow's amount is negative, every row
    :func:`~._already_held.arrivals_already_held` selects has positive cash,
    so ``line.amount < min(...)`` holds and the answer is ``None`` for every
    outflow the schema allows.  No mutation could reach the branch.  This
    package has deleted exactly that shape three times by name
    (:meth:`~._bars.CreationBars.bar_for`'s ``merchant is None`` arm,
    the retired queue's ``_sweeps_for`` ``or row.notes``, and
    :mod:`._income`'s zero arm),
    and the cost of not having it is one pass over ``never_shown`` per outflow
    -- 91 lines against 49 rows on the developer's own statement, arithmetic
    with no query in it.

    It reads the SAME rows :attr:`ReviewSet.unmatched_rows` publishes and the
    same predicate the income pipeline asks
    (:func:`~._already_held.arrivals_already_held`), so a refund and a
    deposit in one period cannot come to different answers about what the books
    already hold.

    Args:
        creatable: This pass's creatable lines.
        never_shown: The candidate rows no bank line explains, derived once by
            the caller.

    Returns:
        ``{line_id: ArrivalsAlreadyHeld}``, holding only the lines that have
        an answer -- absent means nothing to check, which is what
        :func:`~._verdict.ruled` reads a missing key as.
    """
    held_by_line = {}
    for item in creatable:
        held = arrivals_already_held(never_shown, item.line)
        if held is not None:
            held_by_line[item.line.line_id] = held
    return held_by_line


def review_set(scope: ReviewScope) -> ReviewSet:
    """Return everything the review screen shows for one account.

    ONE assembly, so the proposals, the leftovers and the bounds are all
    derived from the same read of the same account inside one request -- a
    screen whose "unmatched" list came from a second pass could disagree with
    its own proposals.

    **The SCOPE is a parameter since plan step ``bank_import:X-f6a-3c-2``**, so
    the screen and the doors it posts to derive an account once between them
    rather than once each.  This reader built its own until then, and the batch
    door's response re-renders this same set: two derivations of 827 priced rows
    in one request, at 3.593 s apiece.

    Args:
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).

    Returns:
        Its :class:`ReviewSet`.
    """
    account_id = scope.account_id
    opens = scope.calendar.opening_bound()
    # **The two DAY bounds, applied in sequence and stated once**
    # (:func:`~._gaps.bounded_lines`, plan step **balance:X-f3c-2b-2b**).  They
    # are different facts with different remedies -- the owner's pay schedule
    # and this ACCOUNT's opening -- and they overlap on real data, so the order
    # they are applied in decides whether their counts add up.
    # **What the owner has SAID, read ONCE for the whole pass** (plan step
    # ``bank_import:X-gm``).  It is read HERE rather than on the scope for the
    # reason :func:`~._leftovers.leftovers` states -- a pass can restate a
    # rule, and this screen is re-rendered after the door that does -- and it
    # is read here rather than THERE because the membership walk below needs
    # the same answers at the same instant.  Two reads of ``merchant_rules`` in
    # one request is this project's DRY violation, and the walk and the split
    # answering from two instants could park a line under an answer this pass
    # had just replaced.
    answers = MerchantAnswers.build(scope.owner_id, account_id)
    # **ONE statement of what the inbox IS**, which the grid's badge counts and
    # this pass is built from (:func:`~._undisposed.inbox_partition`).  It
    # applies both day bounds and takes the holding states out, so the
    # proposer below is given exactly the lines that are TASKS -- ruling
    # **bank_import:R-HQ** made structural rather than restated per surface,
    # since nothing should propose a match for a line nobody can act on.
    lines = inbox_partition(
        account_id, opens, scope.books_start, answers.view.rules, answers.bars,
    )

    # **What is already CLAIMED is read HERE, not carried in the scope.**  The
    # doors re-read it per act, and this screen is rendered after them inside
    # the same request, so a reader taking it off the scope would list rows the
    # batch had just matched.
    matched = matched_subjects(account_id)
    candidates = scope.candidates
    offerable = unmatched_rows(candidates, matched)
    bank_lines = [as_bank_line(line) for line in lines.inbox]
    proposed = propose(bank_lines, offerable)
    proposals = proposed.proposals
    # **The parked lines rejoin here and nowhere earlier.**  They are still
    # ``unmatched`` -- no proposal explains them, and the hand-built group
    # match ruling **R-GJ** leaves open is reached off this list, as is the
    # MATCH pane's own membership test (:meth:`ReviewSet.card_subject`) -- but
    # they were never OFFERED to the proposer, which is the half that changed.
    # Sorted back into the pass's documented order rather than appended, so a
    # surface rendering this list still reads oldest first.
    unmatched = sorted(
        _unexplained(bank_lines, proposals)
        + [as_bank_line(line) for line in lines.parked],
        key=lambda line: (line.posted_on, line.line_id),
    )
    parts = leftovers(
        scope, unmatched,
        current_destinations(scope.destinations),
        answers,
    )
    bounds = ReviewBounds(
        calendar_opens=opens,
        before_calendar_count=len(lines.before_calendar),
        before_calendar_last_day=(
            max(line.posted_on for line in lines.before_calendar)
            if lines.before_calendar else None
        ),
        # **The days the SEARCH skipped, published by the search** (finding
        # **N-322**).  This reader re-derived them over every candidate until
        # plan step X-f6a-3c-2, while ``propose`` searches only the rows no
        # one-to-one proposal claimed -- a superset, so the screen could name a
        # day too crowded to search that had been searched.
        crowded_days=proposed.crowded_days,
        unpriceable_count=len(candidates.unpriceable),
        books=lines.books,
    )
    # **ONE derivation, read twice below** -- by the set's own field and by the
    # double-count map beside it.  Asking the producer once per line inside the
    # comprehension is the redundant-producer-call shape this package treats as
    # a DRY violation rather than a cost.
    never_shown = _rows_the_bank_never_showed(offerable, proposals, account_id)
    return ReviewSet(
        proposals=proposals,
        unmatched=tuple(unmatched),
        # **The bars' own set, carried rather than re-read** (plan step
        # ``bank_import:X-gj-4b``): ``leftovers`` has already asked
        # ``account_payment_merchants`` at this pass's instant, and asking
        # again here would be the redundant producer call inside one request
        # this package refuses.
        account_payments=parts.account_payments,
        unmatched_rows=never_shown,
        # **What the owner's own rules came to for this pass** (finding
        # **N-359**), attached to the LINES rather than derived inside ruling
        # **R-GH**'s door, so that the door and this screen read ONE verdict --
        # and one SENTENCE, composed where the decision is.
        creatable=ruled(
            parts.creatable, proposals, proposed.declined_lines, bounds,
            _already_held_by_line(parts.creatable, never_shown),
        ),
        parked=parts.parked,
        answered_never=parts.answered_never,
        recordable_inflows=parts.recordable_inflows,
        merchants=parts.merchants,
        bounds=bounds,
        # **The near tier's own bound, published by the pass that applied it**,
        # for the reason the crowded days beside it are: a reader re-deriving
        # it would be scoring a different population.  It sits on the SET
        # rather than in ``bounds`` because the screen renders it against the
        # LINE it concerns rather than in the panel of things this page did not
        # look at (plan step ``bank_import:X-f6d-3``).
        declined_lines=proposed.declined_lines,
    )
