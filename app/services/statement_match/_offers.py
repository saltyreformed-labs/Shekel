"""What a match IS, as values -- the shapes every other module here passes.

Ruling **R-FS** gives a match three shapes and this module gives them one
type.  A :class:`MatchProposal` is a candidate the app OFFERS -- and that is
now the whole of what lives here.

**What the owner sends BACK lives in** :mod:`._submission` **since plan step
``bank_import:X-f6d-3``**, and the seam is the DIRECTION rather than the line
count.  The distinction was always this module's own -- *a proposal carries
what the screen needs to explain itself, and a submission carries only ids* --
and finding **N-336** is where the two stopped fitting in one file: a
submission now carries the STATE each row was reviewed in, which is a value
read off a hostile request body, and nothing on this side ever is.

**What a CREATION is lives in** :mod:`._creations` **since plan step
``bank_import:X-f6d-1``**, and the seam is the subject rather than the line
count: this module is about a correspondence between what the bank recorded
and what the app already holds, and those five names are about a budget row
that does not exist yet.  Five of the six modules that import them import
this one too, and that is the honest statement: the seam is the
SUBJECT, not the import graph -- a review screen is about both.

**What the app HOLDS lives in** :mod:`._subjects` **since plan step
``credit_card:CC-5-4a-1``** (this module crossed the 1,000-line bound,
ruling **balance:R-IR**), on the same argument: a :class:`CandidateRow` is
one of the app's three matchable subjects as the app holds it, and knows no
bank line; what stays here is the bank's side (:class:`BankLine`) and the
correspondence itself (:class:`MatchDays`, :class:`MatchProposal`).

Services-boundary discipline (``CLAUDE.md`` Architecture): frozen dataclasses,
no Flask import, no query, no clock read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._sides import MatchSides
from ._subjects import CandidateRow, RowKind


def merchant_label(merchant: "str | None", description: str) -> str:
    """Return what to CALL a line's merchant, always non-empty.

    The merchant's own name where the source named one, else the whole
    description.  **Two consumers, and both need a string rather than an
    answer**: the new-envelope name box on the review screen, and the
    description :func:`~._create.create_purchase_from_line` gives the purchase
    it writes.  ``transactions.name`` and ``transaction_entries.description``
    are both NOT NULL, and neither door goes through a schema that would supply
    a default, so the fallback is what makes those writes total.

    **It is a LABEL and never a key**, which is the whole distinction plan step
    ``bank_import:X-f6a-3d`` drew: a merchant rule is keyed by the merchant
    ROW, and a source that names none has no row -- so a rule fires on nothing
    there.  This falls back to the description instead, because a name box
    cannot show ``None`` -- and if the two were one function, that fallback
    would become a key and a whole truncated OFX statement would share it.  The
    predecessor (``merchant_of(description)``) WAS one function, parsing the
    description at render time; what replaced it is the adapter recording the
    fact and this reader choosing how to display it.

    **It takes the two VALUES it uses** (plan step ``bank_import:X-gd-1``).  It
    took a line and duck-typed over :class:`BankLine` and
    :class:`~app.models.statement_import.BankStatementLine`, which each
    exposed a ``merchant`` string; the ORM row exposes the
    :class:`~app.models.merchant.Merchant`'s id and name as two read-only
    projections, so one spelling no longer reaches both -- and a function
    taking a name and a description says what it needs without either caller
    having to be a particular shape.

    Args:
        merchant: What the merchant is called, or ``None`` where the source
            named none.
        description: What the bank called the line, verbatim.

    Returns:
        The label.
    """
    return merchant or description


@dataclass(frozen=True)
class BankLine:  # pylint: disable=too-many-instance-attributes
    """One recorded statement line, as a proposal needs to show it.

    Pylint: ``too-many-instance-attributes`` (8/7) -- eight because the
    subject is one ROW and eight of its columns are read, which is
    :class:`CandidateRow`'s argument above.

    Attributes:
        line_id: The ``budget.bank_statement_lines`` row.
        posted_on: The day the bank posted it -- the fact this whole arc
            exists to obtain.
        amount: Signed, positive INTO the account.
        description: What the bank called it, verbatim.
        transaction_on: The day the bank STATED the transaction happened, or
            ``None`` where the source states none.  It is what a match writes
            onto a matched purchase's ``purchased_on`` (ruling **R-FW**), so it
            is carried here rather than re-read at the write door: the
            proposer has to know it too, because whether that write can
            succeed is what decides whether the pairing may be OFFERED.
        merchant_id: The :class:`~app.models.merchant.Merchant` this line was
            with, or ``None`` where the source names none.  **This is the KEY**
            (plan step ``bank_import:X-gd-1``): a stated destination is about
            this row, so the two sides of that join are one id rather than two
            copies of one string compared by equality.
        merchant: What that merchant is CALLED, carried beside its id for the
            reason every label on this value is carried: the screen prints it
            and the refusals name it, and re-reading a name a query has already
            fetched is the N+1 this package pays for at 90 lines a statement.
            ``None`` exactly when :attr:`merchant_id` is -- both come from the
            same row, and a source that names no merchant has neither.
            **Carried from the recorded fact rather than parsed from
            :attr:`description`** (plan step ``bank_import:X-f6a-3d``): a
            reader that derived it would have to be total, which on a source
            with no merchant field means every line keying one rule.
        source_category: What the BANK filed this line under, verbatim, or
            ``None`` where the source states none.  **Provenance the screen
            prints and NOTHING may branch on**: the one decision this package
            makes on a bank's category is :mod:`._vocabulary`'s, asked in SQL
            against that adapter's own vocabulary (**bank_import:R-GJ**), and
            a second reader comparing this text would be the ref-name
            comparison forbidden everywhere else.
    """

    line_id: int
    posted_on: date
    amount: Decimal
    description: str
    transaction_on: "date | None" = None
    merchant_id: "int | None" = None
    merchant: "str | None" = None
    source_category: "str | None" = None

    @property
    def merchant_label(self) -> str:
        """Return what to call this line's merchant (:func:`merchant_label`)."""
        return merchant_label(self.merchant, self.description)

    @property
    def states_impossible_days(self) -> bool:
        """Return whether the bank dates this line MADE after it POSTED.

        Two of a source's own facts contradicting each other, which the schema
        deliberately admits: ``bank_statement_lines`` imposes no
        ``transaction_on <= posted_on`` CHECK because 2 of 361 lines in the
        developer's own OFX carry an ``DTUSER`` one day after their
        ``DTPOSTED``, and a constraint a real statement violates would make the
        truth unimportable.

        **So the guard is a reader's, and it is stated HERE because three
        readers ask it** (finding **N-325**): :func:`~._pairing.within_window`,
        to decide whether a purchase recorded after the line posted may still
        be paired -- it may, because the bank's own stated day is later too;
        :func:`~._leftovers._add_refusal`, to WITHHOLD the ADD control, because
        ``entry_service.create_entry`` refuses a purchase whose money left
        before it was spent; and :func:`~._scope.reject_impossible_days` at the
        DOOR, so that withholding is a refusal rather than a paragraph.  *It
        said TWO until plan step ``bank_import:X-gm`` gave the rule its door*,
        re-read off the callers rather than incremented.  Two spellings of one
        predicate on a money screen is this arc's own root cause 1 -- and this
        sentence was FALSE when first written: the proposer went on spelling it
        inline, so the claim described an intention rather than the tree.  Both
        adversarial reviews of 2026-08-19 caught it.

        0 of the developer's 361 recorded lines are this shape; the OFX
        adapter's own measurement found 2 of 361, so a second source makes it
        live.
        """
        return (
            self.transaction_on is not None
            and self.transaction_on > self.posted_on
        )

    @property
    def happened_on(self) -> date:
        """Return the day the bank says this movement was MADE.

        The stated transaction day where the bank states one, else the day it
        posted -- which is the tightest bound the statement supports, since
        money cannot clear before it moves.  **It is a fallback and not a
        claim of equality**, which is exactly the distinction
        ``bank_statement_lines.transaction_on`` became NULLABLE to express:
        callers that must write a day get an answer here, and callers that
        need to know whether the bank OBSERVED it read the column itself.
        """
        return self.transaction_on or self.posted_on


@dataclass(frozen=True)
class MatchDays:
    """The two days a match writes, derived once from its lines.

    Ruling **R-FV** gave a match one day to write and **R-FW** gives it a
    second, because a purchase carries two clocks and the bank states both.
    They are ONE value because they are derived from one set of lines and must
    not be re-derived per member: a match taking ``max(posted_on)`` for one row
    and recomputing it for the next would be two answers to one question.

    **It lives here, beside the values, because two modules ask it** -- the
    accept door, which writes the days, and :class:`MatchProposal`, which has
    to tell the reviewer what accepting would do.  A second spelling of "which
    day does this match write" is this arc's own root cause 1 on a money rule.

    Attributes:
        posts_on: The day every member row records the money as having moved --
            the LATEST of the match's bank days, for the reason
            :attr:`MatchProposal.posts_on` states.
        happened_on: The EARLIEST day the bank says any of these lines was
            MADE, which is what a matched purchase's ``purchased_on`` may be
            corrected to.  **Earliest against latest, and the asymmetry is the
            point**: a row is not wholly moved until its last line posts, and a
            purchase the bank split across several lines was made no later than
            the first of them.
        posted_first: The EARLIEST day any of these lines posted, which is what
            REFUTES a recorded purchase day (:func:`corrected_purchase_day`).
            A third day rather than a reuse of :attr:`posts_on`, for the same
            reason ``happened_on`` is one: money cannot leave before it is
            spent, so a purchase explained by lines posted 06-01 and 06-10 was
            made on or before 06-01 -- and testing a purchase recorded 06-05
            against 06-10 leaves an impossibility uncorrected, which
            ``update_entry``'s own check (against the LATEST day) would not
            catch either.  Found by adversarial design review 2026-08-18.
    """

    posts_on: date
    happened_on: date
    posted_first: date

    @classmethod
    def of(cls, lines) -> "MatchDays":
        """Return the days *lines* state.

        Args:
            lines: The match's bank lines, at least one.  **Structurally
                typed**: each must expose ``posted_on`` (a ``date``) and
                ``transaction_on`` (``date | None``), which both
                :class:`BankLine` and
                :class:`~app.models.statement_import.BankStatementLine` do.
                The idiom is
                :func:`app.services.cash_ledger.unspent_budget`'s
                -- one rule, stated once, over whichever of the two shapes the
                caller already holds, rather than a conversion whose only job
                is to satisfy an annotation.

        Returns:
            Its :class:`MatchDays`.
        """
        return cls(
            posts_on=max(line.posted_on for line in lines),
            happened_on=min(
                line.transaction_on or line.posted_on for line in lines
            ),
            posted_first=min(line.posted_on for line in lines),
        )


def corrected_purchase_day(
    row: CandidateRow, days: MatchDays,
) -> "date | None":
    """Return the day this purchase should be re-dated to, or ``None``.

    **Ruling R-FW: the bank owns both of a purchase's days, but it corrects
    only the day it CONTRADICTS.**  A purchase carries the day it was made
    (``purchased_on``, the budget clock) beside the day the bank took the money
    (``settled_on``, the cash clock).  Accepting a match asserts that this bank
    line IS this purchase -- which asserts the purchase was made on or before
    the day the line posted.  Where the app's recorded day is AFTER that, the
    owner's own assertion has refuted it and it moves; where it is not, the
    bank contradicts nothing and nothing moves.

    **The alternative was measured and it is worse.**  Taking the bank's day
    unconditionally would move 27 of the 44 purchases in today's proposals on
    the developer's own statement, and 18 of those would move LATER: their
    recorded day is already earlier than the bank's, because the bank states no
    transaction day on 179 of 361 lines and :attr:`BankLine.happened_on` then
    falls back to the CLEARING day.  Writing that would record a card purchase
    as having been made on the day it cleared -- replacing 27 dates the owner
    got right in order to fix 3 they got wrong.  Correcting only what is
    contradicted moves exactly those 3, and every one is an impossibility
    rather than a disagreement.

    **Only a PURCHASE has this second day.**  A transaction's ``settled_on`` is
    its only clock, and its pay period -- not a date column -- is what says when
    it was budgeted; a SETTLEMENT is that row's record and has the same one
    clock (its stored ``purchased_on`` IS its settle day, ruling **R-BAL39**,
    and the row's door moves both together).

    Args:
        row: The member being moved.
        days: The days the match's lines state.

    Returns:
        The day to write into ``purchased_on``, or ``None`` when the bank
        contradicts nothing and the column must be left alone.
    """
    # ``expected_on is None`` is UNREACHABLE for a purchase and is stated
    # anyway, which is the discipline ``_candidates._transaction_candidates``
    # applies to its own shadow-parent clause: ``transaction_entries
    # .purchased_on`` is NOT NULL and ``_purchase_candidates`` always fills the
    # field, so the test can refuse no row today -- and without it a hand-built
    # candidate would reach a ``None`` comparison and raise ``TypeError`` from
    # inside a money path rather than declining.  Named by adversarial
    # test-quality review 2026-08-18, which measured that deleting it changes
    # no test.
    if row.kind is not RowKind.PURCHASE or row.expected_on is None:
        return None
    if row.expected_on <= days.posted_first:
        return None
    # Refuted.  The bank's own stated day where it has one, else the day it
    # posted -- the tightest day the owner's own assertion supports.  It is NOT
    # clamped to ``posts_on``: a source whose stated transaction day is LATER
    # than its posting day exists (2 of 361 OFX lines), and clamping would
    # invent a day to keep a write door quiet.  ``update_entry`` refuses that
    # pair by name, and the proposer already declines to offer it.
    return days.happened_on


@dataclass(frozen=True)
class MatchProposal:
    """A candidate correspondence the app OFFERS, never applies.

    Ruling **R-FP**: *a match is a PROPOSAL, never a silent apply*.  Nothing
    here is written anywhere; :func:`~._accept.accept_match` takes a
    :class:`~._submission.MatchSubmission` built from the owner's own
    choice.

    Attributes:
        lines: The bank lines this proposal explains.  One for R-FS's first
            two shapes; several where N lines sum to one row.
        rows: The app rows it names.  One, or several where the app splits one
            movement.
        day_gap: The distance in days between the member rows' recorded
            ``settled_on`` and the day this proposal would move them to, or
            ``None`` when no member carries a day at all.  It is the field a
            reviewer scans: 0 confirms what the app already held, 8 corrects
            it, and ``None`` says the app never recorded the money as having
            moved -- three different acts, and a first draft collapsed the
            third into the first by reading "no distance" as "no difference".
    """

    lines: "tuple[BankLine, ...]"
    rows: "tuple[CandidateRow, ...]"
    day_gap: "int | None"

    @property
    def _sides(self) -> "MatchSides":
        """Return what this proposal's two halves come to.

        **The package's ONE derivation of it** (plan step
        ``bank_import:X-f6d-4``).  This class summed and subtracted for
        itself until then, which made four spellings of one subtraction --
        and a proposal whose screen figure disagreed with the door's by a
        cent would be an Accept the owner did not review.
        """
        return MatchSides.of(self.lines, self.rows)

    @property
    def bank_amount(self) -> Decimal:
        """Return the signed total the bank states for this proposal."""
        return self._sides.bank

    @property
    def app_amount(self) -> Decimal:
        """Return the signed total the app currently holds for it."""
        return self._sides.app

    @property
    def difference(self) -> Decimal:
        """Return what the bank states MINUS what the app holds.

        ``0.00`` for a proposal that balances.  Non-zero is what a NEAR MISS
        is, and ruling **R-GD(a)** made it the PRODUCT rather than a refusal:
        the bank's figure becomes the record and this is the correction
        accepting would write.  The screen states it before anything is
        pressed -- *bank `$178.29`, your row `$178.32`* -- which is plan step
        ``bank_import:X-f6d-1``'s own sentence.

        **Every proposal this app makes is EXACT or one-to-one**, so this is
        non-zero only on the near tier: the group tier sums to the line by
        construction, and nothing scores a group (**R-GD**).  A group whose
        sides differ is one the OWNER built by hand, and since plan step
        ``X-f6d-4`` its difference is recordable -- as **R-FN**'s ordinary
        accepted row rather than as a figure anything here invents for a
        member.  This docstring said such a group was "still refused" until
        that step made it false, on the property that computes the figure.
        """
        return self._sides.difference

    @property
    def reprices(self) -> bool:
        """Return whether accepting would change an AMOUNT and not only a day.

        The template's own question, answered here rather than as a
        ``difference != 0`` test in a Jinja condition -- the rule
        :attr:`review_class` states for the partition it heads, applied to the
        term that partition now turns on.
        """
        return self.difference != 0

    @property
    def confirms(self) -> bool:
        """Return whether this proposal changes no member's recorded day.

        The template's own question, answered here rather than as a truth test
        on :attr:`day_gap` -- where ``None`` reads falsy and an unsettled row
        would be captioned as confirming a day it never had.
        """
        return self.day_gap == 0

    @property
    def review_class(self) -> str:
        """Return which of four things accepting this proposal would DO.

        ``"reprice"`` when the two sides state different figures,
        ``"confirm"`` when it changes no recorded day, ``"correct"`` when it
        moves one the app had wrong, ``"settle"`` when no member carries a day
        at all and the match is what marks the money as having moved.

        **A PARTITION, and that is what the review screen's sweep controls
        rest on** (plan step ``bank_import:X-f6a-3c-2``, developer ruling
        2026-08-19).  R-FP's *reviewed before it commits* survives 124
        proposals only if the sweep is per class rather than one "tick all":
        the classes are different acts with different consequences, so the
        riskiest is never swept by the same click as the safest.  Measured on
        the developer's own statement, the three day classes came to
        27 / 46 / 51 of 124 and they sum -- which is the property a caption
        counting them has to be able to rely on.

        **``"reprice"`` is the fourth member and it takes PRECEDENCE, on
        ruling R-FZ(c)'s own criterion** (plan step ``bank_import:X-f6d-1``,
        developer decision 2026-08-22).  A near miss moves an AMOUNT as well
        as a day, which is the only act on this card that changes what money
        was spent; classing it by its day effect alone would put it on the
        same "tick all" checkbox as 104 day-only corrections, and *the
        riskiest class may not ride the same click as the safest* is the
        sentence that rules out exactly that.  The day effect is still printed
        per row, so nothing is hidden by the reclassification -- what changes
        is which sweep the proposal answers to.

        It is derived HERE rather than as a Jinja condition for the reason
        :attr:`confirms` is: ``day_gap`` is three-valued, and a template
        reading ``None`` as falsy would sweep the settle class in with the
        confirm class -- the exact collapse that caption was made three-valued
        to stop.
        """
        if self.reprices:
            return "reprice"
        if self.day_gap is None:
            return "settle"
        return "confirm" if self.day_gap == 0 else "correct"

    @property
    def days(self) -> MatchDays:
        """Return the two days accepting this proposal would write.

        The SAME derivation the accept door runs, so the screen cannot promise
        one thing and the write do another.
        """
        return MatchDays.of(self.lines)

    @property
    def redated_purchases(self) -> "tuple[CandidateRow, ...]":
        """Return the member purchases whose PURCHASE day accepting would move.

        The screen's own question, answered by
        :func:`corrected_purchase_day` rather than by a second date test in a
        Jinja condition -- where the rule would be stated twice and the two
        would diverge the first time either changed.

        Empty for every proposal that only moves a posting day, which is most
        of them: measured on the developer's own statement, 3 of the 44
        purchases in today's proposals are re-dated and 41 are not.
        """
        days = self.days
        return tuple(
            row for row in self.rows
            if corrected_purchase_day(row, days) is not None
        )

    @property
    def made_on(self) -> date:
        """Return the day a re-dated purchase would be moved to.

        Meaningful only when :attr:`redated_purchases` is non-empty; it is the
        earliest day the bank states for this proposal's lines.
        """
        return self.days.happened_on

    @property
    def redate_gap(self) -> "int | None":
        """Return the FURTHEST a purchase day would move, in days.

        **The screen named the day a purchase moves TO and never the day it
        moves FROM**, on the one write a release cannot undo -- so a reviewer
        was shown "corrects 1 purchase date(s) to 2026-05-30" with nothing
        saying the app currently holds 2026-07-27.  The posting-day caption
        beside it has always stated its distance; this is that caption's twin.
        Found by adversarial financial review 2026-08-18.

        ``None`` when nothing would be re-dated.
        """
        moved = self.redated_purchases
        if not moved:
            return None
        return max(
            (row.expected_on - self.days.happened_on).days for row in moved
        )

    @property
    def posts_on(self) -> date:
        """Return the day every member row would take.

        **The LATEST of the proposal's bank days**, and the choice matters
        where several lines sum to one row.  A row is not wholly moved until
        its last line posts, so the earliest day would let a balance asserted
        between the two absorb money that had not all left the account --
        which is the class of double-count ``dated_deltas``' day partition
        exists to make unspellable.  With one line, which is every proposal
        this app offers automatically, the two rules agree.

        **It DELEGATES rather than restating the rule.**  The template renders
        this property and the accept door writes :attr:`MatchDays.posts_on`, so
        a second spelling here would let the screen print one day while the
        door wrote another -- the duplication :class:`MatchDays` exists to
        prevent.  Found by two adversarial reviews 2026-08-18.
        """
        return self.days.posts_on
