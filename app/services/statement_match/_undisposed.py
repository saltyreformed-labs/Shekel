"""Which of an account's recorded lines no act has ANSWERED yet.

Plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-HP**: every bank line
ends on exactly one of MATCH, ADD, TRANSFER or SKIP, and the Reconcile inbox is
the lines with none yet.  This module is the one statement of *none yet*.

**It is a module because the question has TWO acts behind it.**  An accepted
match answers a line (MATCH and ADD both leave a
:class:`~app.models.statement_match.StatementMatch`), and a recorded skip
answers one with nothing
(:class:`~app.models.statement_line_skip.StatementLineSkip`).  :func:`undisposed`
is the clause that joins them, and :func:`undisposed_lines` is its one reader.

**Since plan step ``bank_import:X-gm`` this module also states what the INBOX
is** (:func:`inbox_partition`), which is *no act has answered it, both day
bounds admit it, and no holding state claims it*.  The grid's badge counts that
walk and the review pass is built from it, so a figure and the page it links to
cannot answer different questions.  Until that step the badge ran a SQL count of
its own that restated two of the pass's Python predicates and had never heard of
a third: it read **27** where the page's inbox read **18**.

**It was three functions inside** :mod:`._reads` **until this step**, and the
split is ruling **balance:R-IR**'s rule rather than a preference: that module
stood at 989 lines against pylint's 1,000-line ceiling, and the fourth
predicate plus the prose it owes does not fit in eleven lines.  R-IR puts the
split on the session that BREAKS the module, and the boundary it takes is the
one stated above: this is the one statement of *none yet*, and
:mod:`._reads` assembles what the review screen makes of it.  *A first draft
justified the line differently -- ":mod:`._reads` is the review screen and
``awaiting_review_count``'s consumer is the grid" -- which its own contents
refute, since two of the three functions that moved serve the review screen.
Named by adversarial design review 2026-09-02.*  Nothing was reworded on the
way across except where this step made a sentence false, and each of those is
marked.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, plain
data out, no Flask import, no clock read.  It READS and never writes; the SKIP
act's own doors are :mod:`._skipping`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_line_skip import StatementLineSkip
from app.models.statement_match import StatementMatchMember
from app.services.cash_ledger import account_opening_fact

from ._bars import CreationBars, is_a_holding_state
from ._gaps import bounded_lines
from ._rules import rules_for

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from datetime import date

    from app.services.cash_ledger import CashOpeningFact

    from ._gaps import BooksBound
    from ._rules import StandingRule


def _spoken_for(account_id: int):
    """Return the query naming *account_id*'s lines a match already claims.

    One definition of "spoken for", because three questions rest on it: the
    review screen's own list and the grid's count, through :func:`undisposed`,
    and the SKIP door's refusal, through :func:`answered_by_a_match` -- which
    may not record a second answer for a line a match has already answered.  A
    count that answered a different question from the list it links to would be
    a figure disagreeing with its caption.

    **Module-private**: every one of those three reaches it through a public
    function in this file, so widening the name would be a surface nobody
    imports.  Named by adversarial design review 2026-09-02.

    Args:
        account_id: The account.

    Returns:
        The query of claimed ``bank_statement_line_id`` values.
    """
    # **Every line membership is a claim** (plan step
    # ``credit_card:CC-5-4a-4``, ruling **R-CC54**): an act naming a line and
    # no movement is unrepresentable now rather than filtered here, so this
    # and ``_candidates.matched_subjects`` read the members as they stand and
    # cannot disagree about what "explained" means.
    return (
        db.session.query(StatementMatchMember.bank_statement_line_id)
        .filter(
            StatementMatchMember.account_id == account_id,
            StatementMatchMember.bank_statement_line_id.isnot(None),
        )
    )


def answered_by_a_match(line_id: int, account_id: int) -> bool:
    """Return whether an accepted match already answers one line.

    Plan step ``bank_import:X-gj-4a``.  **The list predicate asked of ONE
    line**, so the SKIP door and the screen that offered the card cannot come
    to disagree about what "already answered" means -- which would be visible
    to the owner as a control that refuses what the page offered.  Spelled here
    rather than in :mod:`._skipping` so that module never names
    :class:`~app.models.statement_match.StatementMatchMember` and there is one
    place the membership clause lives.

    Args:
        line_id: The bank line.
        account_id: The account the route proved the caller owns.

    Returns:
        Whether a live match claims it.
    """
    return bool(
        db.session.query(
            _spoken_for(account_id)
            .filter(StatementMatchMember.bank_statement_line_id == line_id)
            .exists()
        ).scalar()
    )


def skipped(account_id: int):
    """Return the query naming *account_id*'s lines a recorded skip answers.

    Plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-JG**.  **The
    owner deciding a line is explained by nothing is an ANSWER**, so a skipped
    line leaves the inbox exactly as a matched one does -- which is the whole
    of what the store is for: a skip nothing reads is a line that comes back on
    the next visit.

    **No "still names a row" clause here, and none in** :func:`_spoken_for`
    **either since plan step ``credit_card:CC-5-4a-4``.**  A match could
    outlive its subject until then -- destroying the last movement it named
    left it claiming a line and asserting nothing -- so that reader asked
    whether the claim survived; the movement's key refuses that now (ruling
    **R-CC54**).  A skip names NO app row, so there was never anything for it
    to outlive: it stands until the owner undoes it, or until its own line
    goes (``fk_statement_line_skips_line_account``, ``ON DELETE CASCADE``).

    Args:
        account_id: The account.

    Returns:
        The query of skipped ``bank_statement_line_id`` values.
    """
    return (
        db.session.query(StatementLineSkip.bank_statement_line_id)
        .filter(StatementLineSkip.account_id == account_id)
    )


def skipped_among(
    line_ids: "frozenset[int]", account_id: int,
) -> "frozenset[int]":
    """Return which of *line_ids* a recorded skip already answers.

    Plan step ``bank_import:X-gj-4a``.  **The OTHER direction of ruling
    R-HP's "exactly one of the four"**, and it is asked at the one place the
    match doors resolve a submitted line
    (:func:`~._resolve.load_lines`) rather than in each of them:
    :func:`~._skipping.skip_line` refuses a line a live match answers, and
    without this the same line could be matched AFTER it was skipped and end
    up carrying both answers -- a card on the Explained tab and a card on the
    Skipped tab, for one line, with nothing raising.  *That last clause was
    written at ``X-gj-4a`` and was FALSE until plan step
    ``bank_import:X-gj-4c-2`` gave the Skipped tab the store to read*; it is
    measured now by
    ``test_reconcile.test_the_cards_and_skips.TestADoublyAnsweredLineIsTHECOSTTwoRefusalsBUY``,
    which files the match through its real door and then inserts the skip row
    directly -- one door bypassed, not two, and the resulting state one that
    neither would admit.  The refusal itself never depended on it
    -- ruling **R-HP** is the reason -- but a consequence nobody could observe
    is not a reason a reader can check.

    **Read per ACT and never cached on the pass**, which is
    :class:`~._candidates.MatchedSubjects`' own rule for the same reason: a
    batch's fourth item has to see what its third item wrote, and a value read
    once at the top of the pass could not.

    Args:
        line_ids: The submitted ids.
        account_id: The account the route proved the caller owns.

    Returns:
        The subset a skip answers, empty when none does.
    """
    if not line_ids:
        return frozenset()
    # **NARROWED FROM** :func:`skipped` **rather than re-spelled.**  A clause
    # added to that reader later would reach :func:`undisposed` and silently
    # miss this refusal, which is the drift a module named for one statement
    # of a predicate exists to prevent -- and :func:`answered_by_a_match` one
    # function up already composes on its own base.  Named by adversarial
    # design review 2026-09-02.
    rows = (
        skipped(account_id)
        .filter(StatementLineSkip.bank_statement_line_id.in_(line_ids))
        .all()
    )
    return frozenset(row[0] for row in rows)


def undisposed(account_id: int):
    """Return the clause admitting only lines no act has answered.

    **The one statement of ruling R-HP's "with none yet"**, so the pass and the
    grid's badge cannot come to mean different things by it.  Two ``NOT IN``
    terms rather than one over a union, because the two acts are two facts: a
    reader looking for why a line is absent from the inbox is told which of them
    answered it.  *A first draft also claimed each term "reaches its own table's
    own index", which is unmeasured and half false --
    ``budget.statement_match_members`` carries no index on ``account_id``.
    Named by adversarial design review 2026-09-02.*

    Args:
        account_id: The account.

    Returns:
        A SQLAlchemy clause over :class:`~app.models.statement_import
        .BankStatementLine`, ready to hand to ``filter``.
    """
    return db.and_(
        BankStatementLine.id.notin_(_spoken_for(account_id)),
        BankStatementLine.id.notin_(skipped(account_id)),
    )


@dataclass(frozen=True)
class InboxPartition:
    """One account's recorded lines, split into what the review is ASKING.

    Plan step ``bank_import:X-gm``.  **The one producer of *what is the bank
    inbox*, so the grid's badge and the Reconcile page cannot come to different
    answers about it.**  They did: measured 2026-09-05 at migration head on the
    developer's own Checking, ``awaiting_review_count`` read **27** and the page's
    ``TO_EXPLAIN`` tab read **18**, the difference being the 9 parked card
    payments worth `$7,412.94` that the page holds on its Transfers tab.  They
    agreed only because the badge still opened the retiring review queue, which
    rendered all 27; plan step ``bank_import:X-gi-1`` repoints it at the
    Reconcile page, and that is the press that would have made the two numbers
    visible side by side.

    **The badge takes** ``len(inbox)`` **and the pass takes the whole
    partition**, so the number the grid renders is the membership the page then
    draws cards from rather than a second count of its own.  What the page adds
    is which CARD each line gets, and that is a decoration of this partition and
    never a second decision about who belongs in it.

    Attributes:
        inbox: The lines the review is still asking about, ascending by posted
            day then id.  **The proposer is given exactly these**, which is
            ruling **bank_import:R-HQ** applied where it is structural rather
            than restated per surface: nothing should propose a match for a
            line that is not a task.
        parked: The lines WAITING ON THE APP rather than on the owner
            (:meth:`~._bars.MerchantAnswers.is_a_holding_state`) -- money a
            source says moved between two accounts the owner holds, whose verb
            is TRANSFER and whose door this build does not have.  Still
            rendered, on the Transfers tab, and still reachable by a hand-built
            group match (ruling **R-GJ** leaves that arm open); simply not
            counted as outstanding work.
        before_calendar: The lines older than the owner's first payday, which
            nothing can ever match.
        books: What this account's opening equity already accounts for
            (:class:`~._gaps.BooksBound`), or ``None`` where it accounts for
            none.

    **Every one of the four is a LIST or a value and none is a bare count**,
    which is plan step ``bank_import:X-gm``'s own lesson written into the
    shape: the member this partition replaced that WAS a bare count
    (``ReviewBounds.impossible_day_count``) named no line, and the lines it
    named reached no card on any tab.
    """

    inbox: "list[BankStatementLine]"
    parked: "list[BankStatementLine]"
    before_calendar: "list[BankStatementLine]"
    books: "BooksBound | None"


def inbox_partition(
    account_id: int,
    opens: "date | None",
    opening: "CashOpeningFact",
    rules: "dict[int, StandingRule]",
    bars: CreationBars,
) -> InboxPartition:
    """Split one account's undisposed lines into the inbox and what is held.

    Plan step ``bank_import:X-gm``.  **ONE WALK** (``CLAUDE.md`` rule 14): the
    grid's badge counts what this returns and the review pass is built from
    what this returns, so the two cannot disagree about membership.

    **It is PYTHON and not SQL, and that is the ruling rather than an
    accident** (developer 2026-09-05).  A SQL predicate would have been
    cheaper.  Measured on the developer's own Checking at migration head
    2026-09-05, 378 recorded lines of which 157 are undisposed, medians of 15
    runs with the session expired between: **1.21 ms** for the SQL count this
    replaced, **1.64 ms** for a prototype SQL mirror of the same answer, and
    **3.05 ms** for this walk as shipped.  It would also have had to restate
    :func:`~._rules.pipeline_for` in SQL, which means restating
    :meth:`~._rules.RuleAnswer.of` -- a discriminator ``merchant_rules``
    deliberately does not store, because "a stored discriminator is a second
    statement of what the columns already say".  Three SQL restatements of
    Python predicates to fix a disagreement CAUSED by two SQL restatements of
    Python predicates is rule 14's own failure mode, and **1.84 ms** on the
    grid render is what not doing it costs.

    *The 1.64 ms figure is a PROTOTYPE's and the other two are the tree's*,
    which is why it is the one qualified: nothing in ``app/`` computes it, so
    it cannot be re-measured by running this code.

    **So this DELETES two fences rather than adding three.**  The count it
    replaced spelled the calendar bound as ``posted_on >= opens`` and the books
    bound as ``posted_on > opened_on`` in SQL, beside
    :func:`~._gaps.bounded_lines`' Python; both spellings are gone, and so is
    the arm that graded them against each other.

    **The cost is O(the account's UNDISPOSED lines), and the figure above is
    one account's.**  This hydrates every undisposed line and then drops the
    ones both day bounds exclude -- 157 rows to reach 27 on the developer's
    Checking, of which 130 predate his pay calendar -- where the ``COUNT(*)``
    it replaced touched none.  An owner who imports years of history and never
    reconciles pays for the whole backlog on every ``/grid``, so the 3.05 ms is
    a measurement of one account at one time and not a bound.  Narrowing the
    load to the day bounds is the obvious remedy and is NOT taken here: those
    bounds are :func:`~._gaps.bounded_lines`' to state, and pushing them into
    SQL is the restatement this step exists to delete.

    **IT ALSO CHANGES WHAT THE PROPOSER IS GIVEN, which is more than a
    membership decision.**  :func:`~._propose.propose` is a cascade over a
    SHARED row pool -- :func:`~._propose._one_to_one` is a least-cost pairing
    over all of them, then :func:`~._propose._groups` searches what it did not
    claim, then :func:`~._near.near_misses` what neither did.  Taking the
    holding states out therefore frees the rows they would have been paired
    with, so a DIFFERENT line can now be proposed a match, and a row can fall
    through to the near tier, whose accepted proposal re-prices it to the
    bank's figure (ruling **R-GD(a)**).  Second-order, the same deletion
    shrinks :func:`~._verdict._proposed_destinations`, so
    :func:`~._filing.file_new_swipes` -- the door that files with no press --
    withholds fewer rule filings than before.  Ruling **R-HQ** is what makes
    the change right; neither consequence is a side effect worth leaving for a
    reader to discover, and
    ``test_awaiting_count.TestTakingTheHoldingStatesOffTheProposerCostsNoACT``
    is where the first is graded.

    Args:
        account_id: The account.
        opens: The first day the owner's pay calendar covers, or ``None`` for
            an owner with no periods at all -- in which case nothing is before
            the calendar (:func:`~._gaps.bounded_lines`).
        opening: The account's governing
            :class:`~app.services.cash_ledger.CashOpeningFact`.
        rules: What the owner has answered about this account's merchants
            (:func:`~._rules.rules_for`).
        bars: Which of those merchants may not become purchases
            (:class:`~._bars.CreationBars`).  **Both are taken rather than read
            here**, so one request reads ``merchant_rules`` ONCE: the review
            pass has to read it at the pass's own instant, after any rule the
            same request just stated, and a second read here could park a line
            under an answer that pass had replaced.
            :func:`awaiting_review_count` reads its own, because on the grid
            render nothing else holds them.

    Returns:
        The :class:`InboxPartition`.
    """
    bounded = bounded_lines(undisposed_lines(account_id), opens, opening)
    inbox: "list[BankStatementLine]" = []
    parked: "list[BankStatementLine]" = []
    for line in bounded.inside:
        held = is_a_holding_state(
            amount=line.amount, merchant_id=line.merchant_id,
            rules=rules, bars=bars,
        )
        (parked if held else inbox).append(line)
    return InboxPartition(
        inbox=inbox,
        parked=parked,
        before_calendar=bounded.before_calendar,
        books=bounded.books,
    )


def awaiting_review_count(
    owner_id: int, account_id: int, opens: "date | None",
) -> int:
    """Return how many bank lines the review is still asking the owner about.

    The grid's bank statement control renders this figure, and since plan step
    ``bank_import:X-gm`` it is ``len`` over
    :func:`inbox_partition` -- the SAME membership the Reconcile page draws its
    inbox cards from, rather than a second, differently-shaped count of the
    same thing.

    **It was a COUNT in the database, and four predicates spelled in SQL beside
    the Python the pass applies.**  All four AGREED with the pass; what parted
    the two figures is a FIFTH predicate the pass had and the badge had never
    heard of -- the holding states.  The measurement says so on its own: 27
    against the page's 18 on the developer's own Checking, and 27 - 18 = 9 is
    exactly the parked count, so no day bound contributed a line.  *The two day
    bounds were deleted anyway*, because two spellings that agree today are
    still two spellings (``CLAUDE.md`` rule 14) -- but the honest claim is that
    they COULD have drifted and had not.  The remedy is not a fifth SQL predicate, and the
    argument for walking instead is on :func:`inbox_partition`; what it costs
    is **1.84 ms** on the grid render, measured there with its date.

    **It still does NOT run the proposer, or the pass, or the scope.**  A line
    a proposal explains is work until the owner ACCEPTS it, so the inbox is
    every line no act has answered and no holding state claims -- which is
    exactly what this walks, and none of it needs the 3.5 s offer set the
    review screen builds.

    Args:
        owner_id: The user the caller proved owns the account.  **New at plan
            step ``bank_import:X-gm``**: the holding-state test reads what the
            owner has ANSWERED about a merchant, and ``merchant_rules`` is
            keyed by user as well as account.
        account_id: The account.
        opens: The first day the owner's pay calendar covers
            (:meth:`~app.services.pay_calendar.PayCalendar.opening_bound`), or
            ``None`` for an owner with no periods at all.  **Taken as a
            parameter rather than derived here**: the grid route already holds
            the pass's memoized calendar, and a producer below the route reads
            the pass's derivation instead of building a second one -- two reads
            of one fact in one request is this project's DRY violation, and the
            read this replaced was a second ``budget.pay_periods`` scan on the
            app's hottest render path.

            **The BOOKS opening is NOT a parameter beside it, and the asymmetry
            is that same reason applied honestly rather than copied.**  The
            grid route holds a memoized calendar and holds no opening, so
            taking one would make every caller derive a fact only this function
            needs -- and the point of the parameter was to consume a derivation
            the caller already had, never to push work outward.

    Returns:
        The count, ``0`` when the account has nothing recorded.

    Raises:
        RuntimeError: From
            :func:`~app.services.cash_ledger.account_opening_fact`, when the
            account carries no opening row at all -- a broken invariant, and
            the same fail-loud policy :meth:`~._scope.ReviewScope.build`
            inherits.  Answering "then every line is work" would put a badge on
            the grid for lines the review screen cannot show.
    """
    # **The two facts the walk asks for and no more.**  A whole
    # :class:`~._bars.MerchantAnswers` would carry a
    # :class:`~._rules.RuleView`, whose template names and category list this
    # render does not draw -- measured 2026-09-05 on the developer's own
    # Checking at 2.54 ms against these two at 0.71 ms, on the app's hottest
    # page.  The PREDICATE is still one body
    # (:func:`~._bars.is_a_holding_state`); only its inputs are read the cheap
    # way here and off the pass's own pair there.
    rules = rules_for(owner_id, account_id)
    return len(inbox_partition(
        account_id,
        opens,
        account_opening_fact(account_id),
        rules,
        CreationBars.build(owner_id, account_id, rules),
    ).inbox)


def undisposed_lines(account_id: int) -> "list[BankStatementLine]":
    """Return the account's recorded lines that no act has answered.

    *It was ``unmatched_lines``, and said "that no match explains", until
    plan step ``bank_import:X-gj-4a``.*  A match is now one of two answers
    rather than the only one (:func:`undisposed`), so the old name claimed
    a narrower set than the body returns -- in the module named for the
    wider one.  Renamed rather than re-documented, because a name that has
    to be corrected in prose is a name the next reader trusts anyway.
    Named by adversarial design review 2026-09-02.

    Args:
        account_id: The account.

    Returns:
        The lines, ascending by posted day then id.
    """
    return (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            undisposed(account_id),
        )
        .order_by(BankStatementLine.posted_on, BankStatementLine.id)
        .all()
    )
