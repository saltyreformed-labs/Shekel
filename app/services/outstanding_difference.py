"""What an account's books cannot explain, and what has CHECKED those days.

Plan step **balance:X-f3c-3** (shipped at ``a7dbfdca``).
Two facts that only mean anything together, resolved in ONE call:

* the OUTSTANDING DIFFERENCE -- the owner's latest declared balance less what
  the account's books produce for that same day, which is
  :func:`app.services.balance_at.cash_outstanding_difference`;
* whether an imported bank statement RECONCILES the days that difference
  accumulated over, which is a reading of
  :func:`app.services.bank_agreement.bank_agreement`.

**Money-neutral.**  Nothing here writes, and nothing here offers an act: the
figure is the INSTRUMENT plan step X-f3c-4 needs, and the act that ACCEPTS it
as an ordinary uncategorized transaction is that step (ruling **R-FN**).

**Why the two travel together, and why that is this module rather than the
route.**  A difference the books cannot explain means one thing over a span the
bank has confirmed line by line and quite another over a span nobody has
imported -- ruling **R-GY** turns exactly that into X-f3c-4's offer gate.  The
span the verdict must be about is the DIFFERENCE's own
(:class:`~app.services.balance_at.BooksSpan`, carried on the difference), so a
caller that resolved the two halves separately would hold a figure and a span
as independent arguments and could pair them wrongly -- the shape finding
**N-354** closed one layer down.  One door, one account, nothing left to pair.

**Its own module rather than four more values in** :mod:`.bank_agreement`,
and the reason is measured rather than tidy: that module stood at 797 lines of
pylint's 1000-line ceiling before this step and the first build of this leaf
put it at **993**, seven lines of headroom for whoever touches it next.
Findings **N-152**, **N-156** and **N-201** record the same ceiling on three
other service modules and rule the same answer -- a split on the seam, never
another round of shaving prose off a measured claim.  The seam is real: that
module owns the per-DAY comparison, and this owns a reading of it beside a
figure from a different package.

**It also TAKES THAT VERDICT APART** (plan step **balance:X-bk-1**, ruling
**R-BAL232**): :func:`disagreement_list` cuts the same span into back-to-back
stretches, grades each with the one grader (:func:`span_agreement`) the whole
span's verdict comes from, and names what differs on every day that
disagrees.  It is the list the one-time reconcile of the imported bank
history (plan step X-bk-2) is written from, and the measurement that step
re-runs to show it is done.  No route reads it -- the ruling's own words are
"no screen changes" -- and its one caller is
``tests/manual/measure_bank_disagreements.py``, run against a production
COPY.

Services-boundary discipline: no Flask import, no clock read -- the reader's
NOW arrives on the :class:`~app.services.balance_at.BalanceContext`.  Reads
only; no writes, no commit.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from itertools import groupby

from app.models.account import Account
from app.services import balance_at, bank_agreement

#: One civil day, in the list's calendar arithmetic only: a stretch or piece
#: ends the day before the next begins, a window's END edge is the day after
#: its last day, and the imported days after the books begin the day after
#: the latest assertion.  Not the books' own opening offset (ruling R-HG),
#: which arrives already applied on the span and on ``opened_on``.
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class SpanAgreement:
    """Whether the bank's own record accounts for one span of days.

    A reading of :class:`~app.services.bank_agreement.BankAgreement` narrowed
    to the days one figure accumulated over.

    **It REPORTS and it refuses nothing**, which is ruling **R-GF** applied to
    a narrower question than the whole comparison page: what GATES the
    acceptance act is X-f3c-4's decision (ruling **R-GY**), taken where the
    money moves, and this value is what that decision reads rather than the
    decision itself.

    **The test is per DAY, never on the net.**  That is the same measurement
    :attr:`~app.services.bank_agreement.AgreementDay.agrees` rests on: on the
    developer's Checking account 11 of 35 real disagreements read as EXACT
    agreement in the balance difference, because a same-day assertion cancels
    the error to the cent.  So what this value counts is DAYS, and it carries
    no summed figure at all.

    Attributes:
        first_day: The span's first day, echoed back so a reader holding this
            value alone knows what it is about.
        last_day: The span's last day.
        day_count: How many days the span holds -- ``0`` for an inverted span,
            which is what :attr:`~app.services.balance_at.BooksSpan.is_empty`
            describes.
        compared: How many of those days the comparison could actually take:
            drawn days BOTH records cover
            (:attr:`~app.services.bank_agreement.AgreementDay.compared`, plan
            step ``balance:X-f3c-2b-2c``, ruling **R-BAL291**).  Fewer than
            :attr:`day_count` for a day outside the drawn range
            (``bank_agreement`` draws from the account's first recorded line
            to its last, never past the reader's NOW and at most two years
            back), for a day on or before the day the books opened, whose bank
            lines the opening already holds (finding **N-314**) -- which no day
            of the books span is, since that span starts the day after -- and
            for a day inside no import's window, which nothing the bank said
            reaches (finding **BAL-650**).  So every compared day is an
            :attr:`imported` one.
        disagreeing: How many of the COMPARED days the two records differ on.

            *A signed net RESIDUE over those days was published beside this
            until adversarial review 2026-09-01 and is gone, for two reasons
            that point the same way: nothing in* ``app/`` *read it, and a
            SIGNED sum is the shape*
            :attr:`~app.services.bank_agreement.BankAgreement.asserted_total`
            *warns understates its own subject -- two disagreements of
            opposite sign read as* ``$0.00``.  *A reader wanting the size
            opens the day-by-day comparison, which states it per day.*
        imported: How many days of the span lie inside the window some import
            DECLARES, merged across overlapping and adjacent imports
            (:func:`app.services.statement_import.covered_runs`, carried on the
            report as :attr:`~app.services.bank_agreement.BankAgreement.runs`
            and clipped to the span by
            :func:`~app.services.bank_agreement.covered_pieces`).
            A day outside every such run is a day the app holds no bank record
            for: the bank may have posted lines there and nothing would show
            it.  **It names a REASON, and since plan step
            balance:X-f3c-2b-2c it guards nothing on its own**: such a day's
            bank movement is absent rather than ``0.00``, so it is never
            compared and :attr:`unchecked` refuses it too.  *Until that step it
            was compared against a ``0.00`` the bank never stated (finding*
            **BAL-650** *), "agreed" vacuously, and this count was the one term
            that kept the verdict right.*

            **A window is what the import SAYS it read** (ruling **R-BAL71**,
            plan step ``bank_import:X-f6b-1``, which closed finding **N-434**)
            -- the count reads the declared columns, not the lines.  What a
            window SPANS is the adapter's: the SECU CSV, the only adapter
            today, declares its own first..last line day, so a quiet day at
            either edge of a statement still reads as un-imported, which
            refuses more than it must and never less.  A feed sync will
            declare the window it requested, quiet days included -- and the
            comparison, which draws only from the account's first recorded
            line to its last, would then hold imported days it never compares
            (finding **bank_import:BI-513**).
    """

    first_day: date
    last_day: date
    day_count: int
    compared: int
    disagreeing: int
    imported: int

    @property
    def unchecked(self) -> int:
        """Return how many days of the span nothing compared.

        Returns:
            ``day_count - compared``, which is ``0`` exactly when every day of
            the span was compared.
        """
        return self.day_count - self.compared

    @property
    def unimported(self) -> int:
        """Return how many days of the span no import covers.

        Returns:
            ``day_count - imported``, which is ``0`` exactly when the whole
            span lies inside one run of imported days.
        """
        return self.day_count - self.imported

    @property
    def imported_unchecked(self) -> int:
        """Return how many days of the span an import covers and nothing compared.

        The other part of :attr:`unchecked`: every compared day is an imported
        one, so ``unchecked = unimported + imported_unchecked`` and the two
        never share a day.  What the card states as its own sentence BESIDE
        :attr:`unimported`, so neither recounts the other's days.

        Returns:
            ``imported - compared``: imported days the comparison did not take
            -- days it did not draw (before the account's first recorded line,
            after its last or after the reader's NOW, or beyond the two-year
            bound), and days on or before the books opened, whose movement the
            opening holds (none of which is in the books span).
        """
        return self.imported - self.compared

    @property
    def shortfall(self) -> "StretchKind | None":
        """Return the FIRST reconcile condition the span fails, or ``None``.

        The conditions, in order: the bank's own imports cover every day
        (:attr:`unimported` is zero), the comparison took every day
        (:attr:`unchecked` is zero), and none of the compared days disagreed.
        **This is their one statement**: :attr:`reconciles` is "the span holds
        a day and falls short of none", and a stretch of
        :func:`disagreement_list` is of the kind its own shortfall names --
        so the conditions and their order cannot come to differ between the
        verdict and the list.

        Returns:
            :attr:`StretchKind.NO_STATEMENT`, :attr:`StretchKind.NOT_COMPARED`
            or :attr:`StretchKind.DISAGREES`, the first that applies; or
            ``None`` when none does -- which an EMPTY span also answers, and
            which is why :attr:`reconciles` asks for a day as well.
        """
        if self.unimported:
            return StretchKind.NO_STATEMENT
        if self.unchecked:
            return StretchKind.NOT_COMPARED
        if self.disagreeing:
            return StretchKind.DISAGREES
        return None

    @property
    def reconciles(self) -> bool:
        """Return whether the bank's record accounts for EVERY day of the span.

        Four conditions, each a distinct way the claim could be empty: the
        span holds a day at all; the bank's own imports cover every one of them
        (:attr:`unimported` is zero); the comparison took every one
        (:attr:`unchecked` is zero); and none of the compared days disagreed.
        The last three are :attr:`shortfall`'s, stated there once.  The
        second is implied by the third (below) and is kept for what it
        SAYS: :attr:`shortfall` names a day no import covers as
        :attr:`StretchKind.NO_STATEMENT`, the act that day calls for.

        **A vacuous truth is not reconciliation.**  An empty span satisfies "no
        day disagrees" for free, and so does a span of days nobody imported --
        which is exactly the shape that would let an owner accept a difference
        over months nothing has ever read.

        **:attr:`unchecked` CONTAINS :attr:`unimported`, so its zero implies
        the other's.**  Every compared day is an imported one (a day inside no import's
        window has no bank movement to compare, plan step
        ``balance:X-f3c-2b-2c``, finding **BAL-650**), so :attr:`unchecked` is
        :attr:`unimported` plus :attr:`imported_unchecked` and the two parts
        share no day.  An imported day goes uncompared where the report does
        not draw it -- before the ACCOUNT's first recorded line across every
        import (finding **bank_import:BI-513**, for a window declared wider
        than its lines), after its last line or the reader's NOW, or beyond
        the two-year bound -- or on or before the day the books opened
        (finding **N-314**), which no day of the books span is.  So
        :attr:`unimported` names the REASON a day was not compared and no
        longer guards the verdict alone: ``unchecked == 0`` already forces it
        to zero.  The card states the two parts as two sentences for that
        reason, neither recounting the other.  *Until that step the counts
        OVERLAPPED: a drawn day between two imports was compared against a
        bank movement of ``0.00`` nobody imported, and only the imported-days
        term kept such a span from reading as reconciled.*
        A first draft tested the imports' two END DAYS instead, and that term
        was DEAD: ``bank_agreement`` draws only days between the first and last
        recorded line, so ``unchecked == 0`` already implied it and it could
        never fire.  A defensive term that cannot fire is the *born dead* shape
        ``lessons.md`` names, and it reads as protection nobody has.

        Returns:
            True when the bank's own record accounts for every day the
            difference accumulated over.
        """
        return self.day_count > 0 and self.shortfall is None


@dataclass(frozen=True)
class OutstandingDifference:
    """One account's unexplained difference, beside what has checked its span.

    The output of :func:`outstanding_difference`, and the whole of what plan
    step X-f3c-4 needs before it may offer to book the figure.

    Attributes:
        difference: The
            :class:`~app.services.balance_at.CashOutstandingDifference` -- both
            sides of the subtraction and the span it accumulated over.
        reconciliation: The :class:`SpanAgreement` for that span, or ``None``
            when the account holds no recorded bank line AT ALL.  An absence
            rather than an empty comparison, which is the same distinction
            :func:`~app.services.bank_agreement.bank_agreement` answers ``None``
            for: "nobody has imported a statement" and "the statements say
            nothing disagrees" are different answers and a surface must not
            print the second for the first.
    """

    difference: "balance_at.CashOutstandingDifference"
    reconciliation: "SpanAgreement | None"


class StretchKind(Enum):
    """What every day of one stretch of a books span is.

    **A day's kind is its own** :attr:`SpanAgreement.shortfall` -- the first
    reconcile condition it fails, in that property's order: imported, then
    compared, then agreeing -- or :attr:`AGREES` when it fails none.  So every
    day has exactly one kind, and a stretch of one kind fails for one reason.
    The order is the conditions', not a ranking of which defect matters most:
    a day no statement covers is listed as that even when the app recorded
    money on it, because there is no bank record for the app's rows to
    disagree WITH, and importing one is the act that day calls for.  The
    values are the list's own plain words.
    """

    #: Inside an imported window, compared, and the two records agree.
    AGREES = "agrees"
    #: Compared, and the app's rows and the bank's lines moved different
    #: money -- the days the reconcile's acts are written for.
    DISAGREES = "disagrees"
    #: Inside no import's declared window, so nothing the bank said reaches
    #: it; importing a statement that covers it is what changes it.
    NO_STATEMENT = "no statement yet"
    #: Inside an imported window, yet the comparison could not take it: the
    #: day lies outside the range ``bank_agreement`` draws -- from the first
    #: recorded LINE to the last (finding **bank_import:BI-513**), never past
    #: the reader's NOW, and at most two years back.  *Until plan step
    #: balance:X-f3c-2b-2c a books day before the account's first cash fact
    #: was this kind too (finding **balance:BAL-616**); the comparison now
    #: takes every day after the books open that an import covers, so a drawn
    #: day of the books span never is -- an imported one is compared, and an
    #: un-imported one is* :attr:`NO_STATEMENT`.
    NOT_COMPARED = "not compared"


@dataclass(frozen=True)
class DisagreeingDay:
    """One day the comparison counts as disagreeing, and what is on it.

    **Always a day BOTH records cover** (plan step ``balance:X-f3c-2b-2c``,
    ruling **R-BAL291**): a disagreement needs two movements to differ.
    *Until that step a day no statement covers could be one, because the
    comparison read the bank's movement there as ``0.00`` (finding*
    **BAL-650** *); such a day is now never compared, so it is never listed
    here, and the app's rows on it are reached through the day-by-day
    comparison page instead.*

    Attributes:
        comparison: The day's :class:`~app.services.bank_agreement.AgreementDay`
            -- both movements and their
            :attr:`~app.services.bank_agreement.AgreementDay.residue`, read off
            the comparison rather than re-summed from :attr:`detail`.
        detail: The day's :class:`~app.services.bank_agreement.DayDetail` --
            every bank line on it and every app row that moved money on it (a
            ``$0.00`` leg is left out, as ``day_detail`` leaves it out, and it
            adds nothing to the day's total), each with whether a statement
            match already claims it.  Claimed items too, not only the
            unclaimed ones, for the reason ``DayDetail`` gives: a row matched
            to a line on ANOTHER day moves both days' residues while being
            unclaimed on neither.
    """

    comparison: "bank_agreement.AgreementDay"
    detail: "bank_agreement.DayDetail"


@dataclass(frozen=True)
class Stretch:
    """A run of consecutive days of one :class:`StretchKind`.

    Attributes:
        kind: What every day of it is.
        verdict: Its :class:`SpanAgreement`, from :func:`span_agreement` --
            the grader the whole span's verdict comes from, so a stretch and
            the span it is part of are counted by one rule.
        days: One :class:`DisagreeingDay` per day :attr:`verdict` counts as
            disagreeing, ascending -- so there are exactly
            ``verdict.disagreeing`` of them, and across the stretches exactly
            as many as the whole span's verdict counts.  Every day of a
            :attr:`StretchKind.DISAGREES` stretch, and empty for the other
            kinds: a disagreeing day is a compared one, and a compared day
            lies inside an import's window.  *Until plan step
            balance:X-f3c-2b-2c a* :attr:`StretchKind.NO_STATEMENT` *stretch
            listed a day between two imports on which the app recorded money
            (finding* **BAL-650** *).*
    """

    kind: StretchKind
    verdict: SpanAgreement
    days: "tuple[DisagreeingDay, ...]"


@dataclass(frozen=True)
class DisagreementList:
    """Where one account's bank record and its books part, as a list.

    The output of :func:`disagreement_list` -- plan step **balance:X-bk-1**,
    ruling **R-BAL232**.

    Attributes:
        books: The whole books span's :class:`SpanAgreement` -- the same value
            :attr:`OutstandingDifference.reconciliation` carries, and the one
            whose :attr:`~SpanAgreement.reconciles` the reconcile is done
            against (plan step X-bk-2: "done when the whole books span
            reconciles").  **That is STRICTER than the gloss beside it in the
            ruling's option text** ("every day covered by a statement, none
            disagreeing"): it also asks that the comparison TOOK every day and
            that the span holds one, so a covered day the comparison cannot
            take keeps it false.  The safe direction for a step that moves
            money.  Since plan step balance:X-f3c-2b-2c (findings
            **balance:BAL-616** and **BAL-650**, rulings **R-BAL285** and
            **R-BAL291**) a drawn day of the span is compared exactly when an
            import's window covers it, so once finding **bank_import:BI-513**
            makes the draw reach every covered day the two coincide for a span
            that holds a day and lies within ``bank_agreement``'s two-year
            drawing bound; an empty span, and covered days beyond the bound,
            still keep :attr:`~SpanAgreement.reconciles` false.
        stretches: The books span cut into back-to-back :class:`Stretch`
            values, ascending -- the first begins on the span's first day,
            each begins the day after the one before it ends, and the last ends
            on the span's last day.  Empty for an EMPTY span.  Their verdicts
            add up to :attr:`books` count by count, which is what makes this a
            decomposition of that verdict rather than a second opinion beside
            it.
        before_books: The days from the first imported day through the day
            the books OPEN, graded and set apart, or ``None`` when no import
            reaches back that far.  The opening absorbed them (ruling
            **R-HG**: the books open at the CLOSE of their opening day), so no
            movement is recorded on any of them and none is counted with the
            span.  The counts are the comparison's own, and NONE of these
            days is compared: the books hold a movement only after they open
            (:func:`~app.utils.books_boundary.books_hold`), so these days'
            bank lines are counted as imported days and never as
            disagreements.
            *Until plan step balance:X-f3c-2b-2c a bank line ON the opening
            day was counted here as disagreeing wherever an assertion fell on
            that day (finding **balance:BAL-616**).*  A day between two
            imports in this range counts as un-imported.
        after_books: The days after the account's latest balance assertion
            through the last imported day, graded and set apart, or ``None``
            when no import reaches past it.  Outside the span the completion
            test reads, and reported so a disagreement there is not silent --
            as COUNTS only, like :attr:`before_books`: the days are named by
            the next balance the owner records, which brings them into the
            span, or by the day-by-day comparison page.
    """

    books: SpanAgreement
    stretches: "tuple[Stretch, ...]"
    before_books: "SpanAgreement | None"
    after_books: "SpanAgreement | None"


def outstanding_difference(
    account: Account, ctx: balance_at.BalanceContext,
) -> "OutstandingDifference | None":
    """Return what *account*'s books cannot explain, and what has checked it.

    Args:
        account: The account to measure.  Must belong to ``ctx.user_id``, which
            the read pass REFUSES rather than trusts.  Must be attached to
            ``db.session``.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The :class:`OutstandingDifference`, or ``None`` where the question does
        not apply at all -- an account whose balance carries a MODELLED tier
        (ruling **R-FO**: an IRA has no record of a price movement to discard,
        so the same subtraction there is its RETURN, finding **N-213**), and an
        account carrying no assertion for its books to disagree with.  Both are
        :func:`~app.services.balance_at.cash_outstanding_difference`'s own
        answer; this function adds no scope rule of its own.

    Raises:
        BaselineMissingError: When *ctx* carries no baseline scenario.
        ForeignAccountError: When *account* belongs to another owner.
        PayCalendarError: When the owner's paydays cannot define a calendar,
            and :exc:`RuntimeError` when a planned row names a pay period that
            calendar does not hold.  **Neither is this function's own**: both
            come out of :func:`~._cash_fold.assembled_fold`, which is the door
            it takes the walk through, and they are listed because a caller
            reading only this signature would not know it assembles a whole
            fold to read three fields off it.

    **The comparison is only loaded where there is a figure to place it
    beside**, which is not an optimisation of a rare path: eight of the
    developer's nine accounts are a kind this question does not apply to, and
    :func:`~app.services.bank_agreement.bank_agreement` draws up to 731 days.

    **What it costs, measured on the dev database 2026-09-01 rather than
    estimated.**  On a pass whose cash fold is already assembled -- which is
    the cash detail page's situation, since the band builder folds the account
    before this card is built -- the whole instrument is **6 SQL statements**,
    every one of them the bank comparison's; the FIGURE itself is **0**,
    because it reads the fold the pass already holds.  On a COLD pass it is 25,
    which is the fold's own assembly and not this function's.
    *An earlier draft of this paragraph said five, which was the comparison's
    cost BEFORE this step added ``covered_runs`` to it -- a number measured
    once and then quoted through a change that moved it.*
    """
    halves = _both_halves(account, ctx)
    if halves is None:
        return None
    difference, agreement = halves
    return OutstandingDifference(
        difference=difference,
        reconciliation=(
            None if agreement is None
            else span_agreement(agreement, difference.span)
        ),
    )


def disagreement_list(
    account: Account, ctx: balance_at.BalanceContext,
) -> "DisagreementList | None":
    """Return where *account*'s bank record and its books part, as a list.

    Plan step **balance:X-bk-1**, ruling **R-BAL232**: the books span
    :func:`outstanding_difference` grades as ONE verdict, cut into back-to-back
    :class:`Stretch` values of one :class:`StretchKind` each, every one graded
    by :func:`span_agreement`, with each disagreeing day's bank lines and app
    rows -- plus the imported days either side of the span, set apart.

    **One span, one comparison, resolved once** (:func:`_both_halves`), so
    the list and the card's verdict cannot be about different days: the span
    is the outstanding difference's own, and that pairing is the shape finding
    **N-354** closed.

    Args:
        account: The account to list.  Must belong to ``ctx.user_id``, which
            the read pass REFUSES rather than trusts.  Must be attached to
            ``db.session``.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The :class:`DisagreementList`, or ``None`` in each case
        :func:`outstanding_difference` has no verdict for: a MODELLED kind or a
        loan (ruling **R-FO**), an account that has asserted nothing, and an
        account holding no recorded bank line at all.  An absence rather than
        an empty list, for the reason :class:`OutstandingDifference` gives:
        "nobody imported a statement" is not "nothing disagrees".

    Raises:
        BaselineMissingError: When *ctx* carries no baseline scenario.
        ForeignAccountError: When *account* belongs to another owner.
        PayCalendarError: When the owner's paydays cannot define a calendar,
            and :exc:`RuntimeError` when a planned row names a pay period that
            calendar does not hold -- both from the fold, as in
            :func:`outstanding_difference`.

    **What it costs, and why that is acceptable here and would not be on a
    page.**  Each disagreeing day's detail walks the account's cash ledger
    again (:func:`~app.services.bank_agreement.day_detail`), and each day the
    comparison drew is graded on its own before the stretches are merged --
    at most 731 of them, each a pass over the same at most 731 days.  The one
    caller is a measurement run by hand against a copy.
    """
    halves = _both_halves(account, ctx)
    if halves is None or halves[1] is None:
        return None
    difference, agreement = halves
    span = difference.span
    stretches = []
    for kind, members in groupby(
        _pieces(agreement, span), key=lambda piece: piece[0],
    ):
        group = [piece for _, piece in members]
        stretches.append(_stretch(
            account, ctx, agreement, kind,
            balance_at.BooksSpan(
                first_day=group[0].first_day, last_day=group[-1].last_day,
            ),
        ))
    first_imported = agreement.runs[0].first_day if agreement.runs else None
    last_imported = agreement.runs[-1].last_day if agreement.runs else None
    return DisagreementList(
        books=span_agreement(agreement, span),
        stretches=tuple(stretches),
        before_books=_set_apart(
            agreement, first_imported, difference.opened_on,
        ),
        after_books=_set_apart(
            agreement, span.last_day + _ONE_DAY, last_imported,
        ),
    )


def _both_halves(
    account: Account, ctx: balance_at.BalanceContext,
) -> "tuple[balance_at.CashOutstandingDifference, bank_agreement.BankAgreement | None] | None":
    """Return *account*'s outstanding difference and its bank comparison.

    The ONE place the two are resolved together, read by both public doors, so
    the span a verdict or a list is about is always the difference's own.

    Args:
        account: The account.  See :func:`outstanding_difference`.
        ctx: The read pass's context.

    Returns:
        ``(difference, agreement)``, where *agreement* is ``None`` for an
        account holding no recorded bank line; or ``None`` when the account
        has no outstanding difference to grade at all.  The comparison is only
        loaded where there is a difference to place it beside (see
        :func:`outstanding_difference`).
    """
    difference = balance_at.cash_outstanding_difference(account, ctx)
    if difference is None:
        return None
    return difference, bank_agreement.bank_agreement(account, ctx)


def _pieces(
    agreement: "bank_agreement.BankAgreement",
    span: "balance_at.BooksSpan",
) -> "list[tuple[StretchKind, balance_at.BooksSpan]]":
    """Cut *span* into ascending, back-to-back pieces, each wholly one kind.

    **Every day the comparison DREW is a piece of its own**, because its kind
    depends on its own residue.  **The days it did not draw are cut only where
    an import's window begins or ends**, since nothing else about them can
    change from one day to the next: none was compared, so each is
    :attr:`StretchKind.NO_STATEMENT` or :attr:`StretchKind.NOT_COMPARED` by
    window membership alone.  So the books span is never materialised as a
    day list -- a span whose first day is a mistyped opening in 1900 costs a
    handful of pieces, not 46,000 (the hazard ``_days_between`` names).

    **A piece's kind is read off its own grade** (:func:`_kind_of` over
    :func:`span_agreement`), never off a second test of window membership, so
    the pieces and the verdicts they are merged into rest on one rule.

    Args:
        agreement: The account's comparison.
        span: The books span.  An inverted (empty) span has no piece.

    Returns:
        ``[(kind, piece), ...]`` ascending, each piece a
        :class:`~app.services.balance_at.BooksSpan` over its own days.
    """
    drawn = [day.day for day in _drawn(agreement, span)]
    if drawn:
        ranges = (
            _cut_at_windows(agreement, span.first_day, drawn[0] - _ONE_DAY)
            + [(day, day) for day in drawn]
            + _cut_at_windows(agreement, drawn[-1] + _ONE_DAY, span.last_day)
        )
    else:
        ranges = _cut_at_windows(agreement, span.first_day, span.last_day)
    pieces = []
    for first_day, last_day in ranges:
        piece = balance_at.BooksSpan(first_day=first_day, last_day=last_day)
        pieces.append((_kind_of(span_agreement(agreement, piece)), piece))
    return pieces


def _cut_at_windows(
    agreement: "bank_agreement.BankAgreement",
    first_day: date,
    last_day: date,
) -> "list[tuple[date, date]]":
    """Cut one range of days where an imported window begins or ends.

    Args:
        agreement: The comparison, whose
            :attr:`~app.services.bank_agreement.BankAgreement.runs` are the
            merged windows.
        first_day: The range's first day.
        last_day: Its last day.  An inverted range has no part.

    Returns:
        ``[(first, last), ...]`` ascending and back to back, covering the
        range, with each part wholly inside one window or wholly outside all
        of them: the inside parts are
        :func:`~app.services.bank_agreement.covered_pieces`, the one
        statement of window membership, and the outside parts the days
        between them.
    """
    parts = []
    outside_from = first_day
    for inside in bank_agreement.covered_pieces(
        agreement.runs, first_day, last_day,
    ):
        if outside_from < inside[0]:
            parts.append((outside_from, inside[0] - _ONE_DAY))
        parts.append(inside)
        outside_from = inside[1] + _ONE_DAY
    if outside_from <= last_day:
        parts.append((outside_from, last_day))
    return parts


def _kind_of(verdict: SpanAgreement) -> StretchKind:
    """Return the first of the reconcile conditions *verdict* fails, as a kind.

    Exact for a piece :func:`_pieces` cut, every day of which is one kind; not
    a summary of a mixed span, which is why the whole span's verdict is
    published as counts and never as a kind.

    Args:
        verdict: The piece's grade.

    Returns:
        The :class:`StretchKind`: the piece's own
        :attr:`SpanAgreement.shortfall`, or :attr:`StretchKind.AGREES` when it
        falls short of nothing -- so the conditions and their order are read
        from the one place :attr:`SpanAgreement.reconciles` reads them.
    """
    shortfall = verdict.shortfall
    return StretchKind.AGREES if shortfall is None else shortfall


def _stretch(
    account: Account,
    ctx: balance_at.BalanceContext,
    agreement: "bank_agreement.BankAgreement",
    kind: StretchKind,
    span: "balance_at.BooksSpan",
) -> Stretch:
    """Grade one merged stretch, and name what differs on each day of it.

    **The days named are the ones the grade COUNTS as disagreeing**, picked
    by the grader's own rule (:func:`_disagreeing_among` over
    :func:`_compared`), so a stretch lists exactly as many days as its
    verdict counts -- whatever its kind.

    Args:
        account: The account, for the day detail.
        ctx: The read pass's context, for the day detail.
        agreement: The account's comparison.
        kind: What every day of the stretch is.
        span: The stretch's days.

    Returns:
        The :class:`Stretch`.
    """
    return Stretch(
        kind=kind,
        verdict=span_agreement(agreement, span),
        days=tuple(
            DisagreeingDay(
                comparison=day,
                detail=bank_agreement.day_detail(account, ctx, day.day),
            )
            for day in _disagreeing_among(_compared(agreement, span))
        ),
    )


def _set_apart(
    agreement: "bank_agreement.BankAgreement",
    first_day: "date | None",
    last_day: "date | None",
) -> "SpanAgreement | None":
    """Grade the imported days on one side of the books span, or ``None``.

    Args:
        agreement: The account's comparison.
        first_day: The first such day, or ``None`` when no import exists.
        last_day: The last such day, or ``None`` when no import exists.

    Returns:
        The :class:`SpanAgreement` over those days, or ``None`` when there is
        no such day -- no import reaches past that side of the span.
    """
    if first_day is None or last_day is None or last_day < first_day:
        return None
    return span_agreement(
        agreement,
        balance_at.BooksSpan(first_day=first_day, last_day=last_day),
    )


def span_agreement(
    agreement: "bank_agreement.BankAgreement",
    span: "balance_at.BooksSpan",
) -> SpanAgreement:
    """Fold *agreement*'s days down to the verdict for one *span*.

    **The ONE grader**: the outstanding difference's verdict and every
    stretch of :func:`disagreement_list` are counted here, so a stretch and
    the span it is part of cannot be counted by two rules.  Public since plan
    step balance:X-bk-1, which needed it for spans other than the
    difference's own.

    **Days are counted, never re-derived.**  Membership comes from
    :attr:`~app.services.bank_agreement.AgreementDay.compared` and
    disagreement from
    :attr:`~app.services.bank_agreement.AgreementDay.agrees`, both of which are
    the report's own published rules; nothing here re-tests a residue, re-reads
    a line or issues a query.  The two counts NOT taken over the report's
    days, :attr:`~SpanAgreement.day_count` and
    :attr:`~SpanAgreement.imported`, are arithmetic over the span's ends,
    because a day the report never drew is still a day of the span and can
    still lie inside an import's window.  So the verdict and the comparison page's own
    per-day table are two readings of ONE comparison rather than two
    implementations of "does the bank agree".

    Args:
        agreement: The account's comparison
            (:func:`~app.services.bank_agreement.bank_agreement`).
        span: The days to grade -- the days the outstanding difference
            accumulated over
            (:attr:`~app.services.balance_at.CashOutstandingDifference.span`),
            or a stretch of them, or the imported days either side of them.
            Only its two ends are read.  An INVERTED span is legal and answers
            with a zero :attr:`~SpanAgreement.day_count`, which
            :attr:`~SpanAgreement.reconciles` reads as *nothing was checked*.

    Returns:
        The :class:`SpanAgreement`.
    """
    compared = _compared(agreement, span)
    return SpanAgreement(
        first_day=span.first_day,
        last_day=span.last_day,
        # From the span's OWN ends rather than from the report's days: a day
        # the report never drew is a day nothing checked, and counting only
        # what was drawn would report a truncated comparison as a whole one.
        day_count=_days_between(span.first_day, span.last_day),
        compared=len(compared),
        disagreeing=len(_disagreeing_among(compared)),
        imported=sum(
            _days_between(first_day, last_day)
            for first_day, last_day in bank_agreement.covered_pieces(
                agreement.runs, span.first_day, span.last_day,
            )
        ),
    )


def _drawn(
    agreement: "bank_agreement.BankAgreement",
    span: "balance_at.BooksSpan",
) -> "list[bank_agreement.AgreementDay]":
    """Return the days the comparison DREW inside *span*, ascending.

    Args:
        agreement: The account's comparison.
        span: The days asked about; only its two ends are read.

    Returns:
        The :class:`~app.services.bank_agreement.AgreementDay` values whose
        day lies in *span* -- empty for an inverted span, or one the
        comparison drew no day of.
    """
    return [
        day for day in agreement.days
        if span.first_day <= day.day <= span.last_day
    ]


def _compared(
    agreement: "bank_agreement.BankAgreement",
    span: "balance_at.BooksSpan",
) -> "list[bank_agreement.AgreementDay]":
    """Return the days of *span* the comparison drew AND both records cover.

    Args:
        agreement: The account's comparison.
        span: The days asked about.

    Returns:
        The drawn days whose
        :attr:`~app.services.bank_agreement.AgreementDay.compared` holds.
    """
    return [day for day in _drawn(agreement, span) if day.compared]


def _disagreeing_among(
    compared: "list[bank_agreement.AgreementDay]",
) -> "list[bank_agreement.AgreementDay]":
    """Return the compared days whose two records differ.

    Args:
        compared: Days :func:`_compared` returned.

    Returns:
        Those whose :attr:`~app.services.bank_agreement.AgreementDay.agrees`
        is False, in their order.
    """
    return [day for day in compared if not day.agrees]


def _days_between(first_day: date, last_day: date) -> int:
    """Return how many civil days an inclusive range holds.

    **Arithmetic rather than a materialised list, and the reason is a real
    hazard rather than tidiness.**  The span this counts starts at an
    account's ``opened_on + 1``, and ``opened_on`` is USER-SUPPLIED through the
    books-restatement form with no lower bound -- ``opening_service`` refuses a
    future day, a day at or after a movement, a matched line or an assertion,
    and nothing refuses 1900.  Building the day list would allocate ~46,000
    ``date`` objects on every cash-detail render and every ``balanceChanged``
    refresh of the card, which is the same class of defect
    ``bank_agreement._MAX_COMPARED_DAYS`` was added for after a two-line
    statement rendered 26 MB.  Found by adversarial review 2026-09-01.

    Args:
        first_day: The range's first day, inclusive.
        last_day: The range's last day, inclusive.

    Returns:
        The day count, or ``0`` for an INVERTED range -- which is what an empty
        :class:`~app.services.balance_at.BooksSpan` is, and what
        :func:`span_agreement`'s ``day_count`` relies on for one.  Its
        ``imported`` count never passes an inverted range:
        :func:`~app.services.bank_agreement.covered_pieces` drops a run that
        does not overlap the span.
    """
    if last_day < first_day:
        return 0
    return (last_day - first_day).days + 1
