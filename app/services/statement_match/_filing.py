"""What a standing rule files by itself, for the lines ONE import just recorded.

Ruling **R-GH**, plan step ``bank_import:X-ge``.  **It MOVES MONEY, and it is
the only door in the app that moves money without a press.**

**Why it exists, in the developer's own words (2026-08-24):** "In a perfect
world the app would hold every real transaction the bank saw and automatically
assign it to the appropriate line item in the budget."  Ruling **R-FP** made
that unreachable *by ruling* rather than by limitation -- every act was a
proposal needing a tick, so Food Lion's fortieth swipe needed the same human
act as its first.  **R-GH splits consent by ACT CLASS**: creating a row from a
NEW bank swipe under a rule the owner stated is consented to once, when they
state the rule; modifying a row they made by hand -- re-date, re-price, settle,
group-match -- keeps its tick, which is :mod:`._accept`'s door and not this
one.

**What it acts on is the SCREEN's own creatable set, not a second derivation.**
:func:`~._reads.review_set` decides which lines a create control may be
rendered for, and every clause of that decision is a refusal this door would
otherwise have to restate: the pay calendar, finding **N-325**'s impossible
days, ruling **R-GJ**'s bars, and *no proposal explains this line*.  A rule
files a subset of that set and can never widen it, which is the property
:mod:`._rules` states for a placement one tier down.

**Three narrowings turn "the screen would offer this" into "a rule may file
it", and each is a measured hazard rather than caution.**

* **NEW swipe lines only** (**R-GI**).  A line is fresh to the import that
  FIRST sighted it (plan step ``bank_import:X-f6b-1``: derived from the
  sightings, ``_fresh_line_ids``), so no second column is needed.  It is also
  exactly right about the case that looks like an exception: a line's
  merchant is the earliest surviving sighting that names one (ruling
  **R-BI16**), so a line the first adapter could not name becomes rule-keyed
  at a LATER import while its first sighting stays the earlier one -- and it
  is not a new swipe, so it is not filed.  A re-import of an overlapping
  span records no fresh line and
  therefore files nothing, which is what makes this door idempotent for free.
* **The pass must have finished LOOKING** (:meth:`~._reads.ReviewSet
  .search_gap_for`, developer ruling 2026-08-26).  Membership of ``creatable``
  is a set defined by subtraction, and under a human tick the person reading
  the screen is the check.  There is no person here.
* **A destination this pass PROPOSES as a whole-row match is not open to a
  rule.**  Ruling **R-FZ(d)** ruled that where two TICKED items collide over
  one envelope the PROPOSAL wins, because it explains money the records already
  hold; auto-apply inverts the order -- it files at import and the proposal is
  ticked afterwards -- so the same rule has to be applied from the other side.
  Measured on the developer's own 378 recorded lines: **0** of the 80 lines a
  rule would file aim at an envelope a proposal names directly, and 33 aim at
  one holding a purchase a proposal names, which is the safe shape (two acts,
  neither naming both a parent and its own child).

**What it does NOT narrow, and both were ruled by the developer on measurement
(2026-08-26).**  A rule files into an envelope that has already CLOSED -- 33 of
those 80 lines, `$911.10`, against **0** into an envelope still open, because
the owner settles ahead of the bank -- which ruling **R-FX** already admits on
exactly the terms :func:`~._destinations.destinations_for` enforces: the row's
recorded figure IS its purchases, so a new one raises that cost by exactly the
figure the bank showed and carries the bank's own posting day.  And a *new
envelope* answer files too, minting the container, because the alternative
leaves the developer's single largest rule (Amazon, 26 lines, `$1,323.06`)
needing a manual act on every import until ``bank_import:X-f6c`` gives that
answer a template identity.

**It DERIVES and does not write.**  What it produces is the same
:class:`~._creations.PurchaseCreation` the review screen's own destination
select submits, and :func:`~._batch.apply_reviewed` is what performs them --
one door, one failure policy, one savepoint per item, one receipt shape.  The
consent is the batch's (:class:`~._batch.Consent`), so nothing here decides
what reaches ``applied_by_rule``.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.extensions import db
from app.models.merchant_rule import MerchantRule
from app.models.statement_import import StatementLineSighting
from app.models.statement_match import StatementMatch

from ._accepted_view import AcceptedGroup, accepted_groups
from ._batch import Consent, ReviewedBatch, apply_reviewed
from ._creations import IncomeCreation, PurchaseCreation
from ._offers import BankLine
from ._outcome import BatchOutcome
from ._reads import ReviewSet, review_set
from ._scope import ReviewScope
from ._verdict import CHECK_FIRST


@dataclass(frozen=True)
class WithheldLine:
    """One line a rule reaches and this pass declined to file, with the reason.

    **A bound that says nothing about what it dropped reads as a clean sweep**
    -- the sentence :class:`~._propose.ProposedMatches` and
    :class:`~._reads.ReviewBounds` are both built around, applied to the one
    surface where nobody is watching.  A receipt reading *"3 line(s) filed by
    your rules"* over a fourth the rules would have filed and did not tells the
    owner their rules ran when they partly did not.

    **It carries only lines a rule REACHES.**  A line whose merchant has no
    rule, or an *ask me every time* one, is not withheld -- it is unanswered,
    which is the exception queue's ordinary content and not a fact about this
    pass.

    Attributes:
        line: The bank's own record of the movement.
        reason: One sentence saying why the rule did not fire, written for the
            owner.  The pass's own words
            (:meth:`~._reads.ReviewSet.search_gap_for`) where the reason is a
            search bound, so the receipt and the screen cannot describe one
            limit two ways.
    """

    line: BankLine
    reason: str


@dataclass(frozen=True)
class RuleFiling:
    """What one import's standing rules came to.

    Attributes:
        outcome: What :func:`~._batch.apply_reviewed` did
            (:class:`~._outcome.BatchOutcome`) -- the applied items, the refused
            ones and every count the review screen's own receipt carries.  A
            pass with nothing to file still produces one, because *your rules
            filed nothing* and *your rules were not consulted* are different
            sentences and only the second is an absence.
        withheld: The lines a rule reaches that this pass would not file, with
            the reason each (:class:`WithheldLine`).
        unavailable: Why the rules could not be consulted AT ALL, or ``None``
            when they were.  **The import still landed**, which is the whole
            point of it being a value: recording what the bank said does not
            depend on the budget being derivable, and a request that could not
            derive one has still recorded a statement correctly
            (:func:`~app.routes.accounts.statements.import_statement`).
    """

    outcome: BatchOutcome
    withheld: "tuple[WithheldLine, ...]"
    unavailable: "str | None" = None

    @classmethod
    def could_not_run(cls, reason: str) -> "RuleFiling":
        """Return the filing for an import whose rules could not be consulted.

        Args:
            reason: One sentence, written for the owner, saying what stopped
                them -- and it is the CALLER's sentence rather than an
                exception's, because the two states that reach here raise
                messages written for a developer.

        Returns:
            The :class:`RuleFiling`.
        """
        return cls(
            outcome=BatchOutcome.nothing(), withheld=(), unavailable=reason,
        )

    @property
    def filed_count(self) -> int:
        """Return how many lines a rule filed, in EITHER direction.

        **ALL THREE counts, and the third arrived the same way the second
        did.**  It read ``recorded_count`` alone until plan step
        ``bank_import:X-gj-2a``, which is purchases only
        (:attr:`~._outcome.BatchOutcome.deposited_count` is deliberately not
        folded into it), so a pass that filed nothing but deposits under ruling
        **R-HT(a)** would have reported filing NOTHING -- with the acts landed,
        the money moved and the receipt silent about all of it.  That is the
        under-report a bound-with-no-denominator makes, on the door nobody
        watches.

        Plan step ``bank_import:X-gj-2b-3`` split ``recorded_count`` again, by
        DIRECTION, and this sum had to learn the new one for the identical
        reason: a pass that filed nothing but merchant credits would have
        reported ZERO.  **A count assembled by NAMING its members is one member
        away from wrong every time a member is added** -- which is now twice on
        this one property -- and what caught it both times was a case asserting
        the whole outcome rather than the count alone.
        """
        return (
            self.outcome.recorded_count
            + self.outcome.refunded_count
            + self.outcome.deposited_count
        )

    @property
    def filed_total(self) -> Decimal:
        """Return what the bank moved on the lines a rule filed, signed.

        Derived from the acts that LANDED rather than tallied beside them,
        which is the rule :attr:`~._outcome.BatchOutcome.moved_nothing` states
        one type over: two fields that must agree are two fields that can come
        to disagree.  In the BANK's own direction, so the receipt states the
        figure without inventing a second convention for it.

        **It is a NET and no longer always negative** (ruling **R-HT(a)**, plan
        step ``bank_import:X-gj-2a``).  This said *negative, because every line
        this door can file is money leaving --* :func:`~._create._load_line`
        *refuses an inflow*, which was true of a door that filed only
        purchases; it now also files deposits a standing INCOME rule answers
        for, and those are positive.  Plan step ``bank_import:X-gj-2b-2`` then
        took the second half of it as well: ``_load_line`` refuses an inflow
        only where NO container answer claims it, so the purchase arm files
        refunds and they are positive on the bank's convention too.  A
        one-import pass can therefore come to any sign, including exactly zero
        on a pass whose filed swipes and filed deposits happen to cancel: a
        receipt reading the figure must say what it is rather than assume it is
        spending.
        """
        return sum(
            (item.amount for item in self.outcome.applied),
            Decimal("0.00"),
        )

    @property
    def says_nothing(self) -> bool:
        """Return whether this pass has nothing at all to report.

        The receipt's own question.  A pass that filed nothing, refused
        nothing and withheld nothing is one an ordinary re-import produces --
        it recorded no fresh line -- and a receipt sentence for it would be
        noise on every idempotent import the owner performs.

        **A pass that could not RUN always has something to report**, which is
        the one thing this must not collapse: *your rules found nothing to do*
        and *your rules were not consulted* look identical from every count,
        and only the second is a state the owner has to repair.
        """
        if self.unavailable is not None:
            return False
        return not (
            self.outcome.applied or self.outcome.refused or self.withheld
        )


#: How many rule-filed acts the receipt lists.
#:
#: **The receipt is a DERIVATION over stored facts and not a flash**, which is
#: what makes it survive the reload that a flash cannot: ruling **R-GH** wants
#: every filed line itemised with the one-click undo **R-GG** built, and an
#: undo control is a form rather than a sentence -- flash messages ride in the
#: signed session cookie, where the review screen has already measured nine
#: batch sentences overflowing the 4 KB a browser stores.  So the list is a
#: bound one, newest first, exactly as the imports table beside it is.
#:
#: 20 for the reason ``import_history``'s limit is 20: it is a page section,
#: and an account whose rules file weekly for years would otherwise render
#: thousands of rows.  The page SAYS the bound, which is the half
#: ``import_history`` had to be corrected for (finding **N-330**).
RECEIPT_LIMIT: int = 20


def _fresh_line_ids(account_id: int, import_id: int) -> "frozenset[int]":
    """Return the ids of the lines *import_id* was the FIRST to sight.

    **The freshness fact is derived from the sightings** (plan step
    ``bank_import:X-f6b-1``): a line is fresh to an import when that
    import's sighting of it is the line's earliest
    (:meth:`~app.models.statement_import.StatementLineSighting
    .first_import_of_each_line`), which is the one spelling of *first* the
    receipt's *recorded* count reads too.  *It read the line's own
    ``import_id`` until that step*, when a line belonged to one import; a
    line another source had already shown is not this import's to file,
    because it was offered when it first appeared.

    Args:
        account_id: The account being imported into, which is the ONE statement
            of whose lines may be reached -- the same account clause
            :func:`~._resolve.load_lines` applies at the write door, asked here
            so a rule cannot even be offered another account's line.
        import_id: The import that just ran.

    Returns:
        The line ids, or empty for an import that sighted nothing first.
    """
    first = StatementLineSighting.first_import_of_each_line(account_id)
    rows = (
        db.session.query(first.c.line_id)
        .filter(first.c.import_id == import_id)
        .all()
    )
    return frozenset(row[0] for row in rows)


def rule_filed_acts(
    owner_id: int, account_id: int, limit: int = RECEIPT_LIMIT,
) -> "list[AcceptedGroup]":
    """Return the acts a STANDING RULE performed on this account, newest first.

    **The receipt ruling R-GH asks for, derived rather than carried** (plan
    step ``bank_import:X-ge``).  What a rule filed is stored -- the purchase,
    the match that names it, and ``applied_by_rule`` saying a rule performed it
    (**R-GT**) -- so the receipt is a READ, which is why it is still there
    after a reload, still there tomorrow, and identical whether the owner
    arrived from the import they just performed or from the menu.

    **It is the REGISTER's OWN value type**
    (:class:`~._accepted_view.AcceptedGroup`), narrowed.  That list already
    answers everything a receipt item needs -- what the bank showed, what an
    Undo would remove and what that is worth, and whether the act still holds
    -- from :func:`~._release.planned_removals`, which is the door's own
    derivation rather than a second estimate of it.  A receipt with its own
    value would be a second description of one act, and the figure on the undo
    control is exactly where the two must not differ.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account whose acts to read.
        limit: How many to return, newest first.  Bounded because this feeds a
            page section; the page states the bound.

    Returns:
        One :class:`~._accepted_view.AcceptedGroup` per act a rule performed,
        newest first.  Empty for an account whose rules have never fired, which
        is every account until this step ships -- all 221 acts on the
        developer's own dev database are ticks, measured at ``X-gd-2``.
    """
    ids = (
        db.session.query(StatementMatch.id)
        .filter(
            StatementMatch.account_id == account_id,
            StatementMatch.user_id == owner_id,
            StatementMatch.applied_by_rule.is_(True),
        )
        .order_by(StatementMatch.created_at.desc(), StatementMatch.id.desc())
        .limit(limit)
        .all()
    )
    if not ids:
        return []
    return accepted_groups(owner_id, account_id, {row[0] for row in ids})


def _has_a_filing_rule(owner_id: int, account_id: int) -> bool:
    """Return whether this account carries any rule this door could ACT on.

    **A pre-check, and it is exact rather than a heuristic**: only
    :attr:`~._rules.RuleAnswer.TEMPLATE` and
    :attr:`~._rules.RuleAnswer.NEW_ENVELOPE` produce a
    :meth:`~._placement.Placement.creation_for`, and only
    :attr:`~._rules.RuleAnswer.INCOME_CATEGORY` produces a
    :meth:`~._placement.InflowPlacement.creation_for` -- so an account with
    none of the three files nothing and withholds nothing, whatever its
    statement holds.  Its answer therefore says the same thing the whole
    derivation would, for one indexed read.

    **The income arm had to be added here as well as downstream, and forgetting
    it would have been a SILENT no-op** (plan step ``bank_import:X-gj-2a``).
    This short-circuit returns before the pass is derived at all, so an account
    whose only rules answer DEPOSITS would have skipped the whole derivation
    and filed nothing -- with no refusal, no withholding and no receipt line,
    because a door that returned early has nothing to report.  A test asserting
    the income arm files would have passed on an account that also held a
    spending rule and failed on one that did not, which is the shape that hides
    until a real owner has exactly the second.

    **It is a COUNT and not a second read of the rules themselves**, which is
    the distinction that keeps it from being the redundant producer call this
    project treats as a defect: :func:`~._rules.rules_for` answers *what has
    the owner said*, and this answers *is there any work* -- a different
    question, asked once, whose answer nothing downstream reuses.

    Args:
        owner_id: The user the caller proved owns the account.
        account_id: The account being imported into.

    Returns:
        Whether at least one rule on it names a template, a new envelope or an
        income category.
    """
    return db.session.query(
        db.session.query(MerchantRule)
        .filter(
            MerchantRule.user_id == owner_id,
            MerchantRule.account_id == account_id,
            db.or_(
                MerchantRule.template_id.isnot(None),
                MerchantRule.envelope_name.isnot(None),
                MerchantRule.income_category_id.isnot(None),
            ),
        )
        .exists()
    ).scalar()


def _rule_filings(
    review: ReviewSet, fresh: "frozenset[int]",
) -> "tuple[list[PurchaseCreation], list[WithheldLine]]":
    """Split the lines a rule reaches into what it files and what it withholds.

    Args:
        review: What this pass offers -- its creatable lines carry the
            placement a stated rule comes to for each
            (:class:`~._placement.Placement`).
        fresh: The ids of the lines this import was the first to record.

    Returns:
        ``(creations, withheld)`` in the order the pass lists its lines, which
        is ascending by posted day: the order the receipt reads down, and the
        order :class:`~._create.MintedEnvelopes` converges a new-envelope
        answer in, so the envelope a press mints belongs to the earliest line
        that reaches it.

    **It no longer DECIDES which of the two it is** (plan step
    ``bank_import:X-gf-3a``, finding **N-359**).  The verdict is
    :attr:`~._reads.ReviewSet.rule_verdicts`, derived once by the pass, so the
    sentence this receipt reports and the sentence the review screen prints
    beside the same line are the same value rather than two spellings of one
    rule.  What stays here is the one narrowing that belongs to this DOOR and
    to no screen: ruling **R-GI**'s *new swipe lines only*.
    """
    creations: "list[PurchaseCreation]" = []
    withheld: "list[WithheldLine]" = []
    for item in review.creatable:
        if item.line.line_id not in fresh:
            continue
        # A rule that names no container REACHES nothing: the owner has said
        # nothing, or said *ask me every time*, or said *never a purchase* --
        # which is a BAR that keeps the line out of ``creatable`` entirely
        # (ruling **R-GJ**).  None of the three is this pass withholding
        # anything, so none of them is reported as one, and none of them has a
        # verdict.
        verdict = item.verdict
        if verdict is None:
            continue
        if verdict.withheld is not None:
            withheld.append(
                WithheldLine(line=item.line, reason=verdict.withheld),
            )
            continue
        creations.append(verdict.creation)
    return creations, withheld


def _inflow_filings(
    review: ReviewSet, fresh: "frozenset[int]",
) -> "tuple[list[IncomeCreation], list[WithheldLine]]":
    """Split the DEPOSITS a rule answers into what it files and what it holds.

    Ruling **R-HT(a)**, plan step ``bank_import:X-gj-2a``.
    :func:`_rule_filings`' twin for the other direction, restated here against
    the facts an inflow has rather than shared, because the outflow half reads
    a :class:`~._placement.Placement` and this reads a
    :class:`~._placement.InflowPlacement` and they are different types for a
    reason (one names a container, the other a classification).

    **TWO withholdings, and a first version of this function had only the
    second** (adversarial code review 2026-08-31, which measured a deposit
    filed with no press that the pass had declined to conclude about).

    * **The pass did not finish LOOKING**, which is ruling **R-GH**'s own
      narrowing and the one :func:`~._verdict.ruled` asks FIRST on the outflow
      side.  It applies unchanged here: a line nothing finished searching for
      has not been shown to collide with anything.
    * **The books may ALREADY HOLD this deposit**, which is the hazard the
      outflow side does not have.
      :meth:`~._reads.ReviewSet.arrivals_already_held_in` answers it -- does
      this deposit's own pay period hold unexplained income that could contain
      it -- and under a human tick the card renders that warning and the person
      decides.  **There is no person here**, so a rule withholds wherever the
      card would have warned.

    **Neither can stand in for the other, and assuming one could is what made
    the omission a MONEY defect.**  The double-count guard tests
    ``row.expected_on <= day <= row.expected_through``, so it sees only rows in
    the deposit's own pay period; the near tier reaches ACROSS periods by
    ``DAY_WINDOW``.  A candidate row one period over is invisible to the first
    and visible to the second.  Their fail sets are not nested, so a door
    holding one of them holds neither's whole property.

    Measured on the developer's own account 2026-08-31, over the 16 recordable
    inflows: it is quiet for all five dividends (`$0.12`-`$0.22`) and all three
    merchant credits (`$11.73`-`$28.29`), and fires for the seven payroll
    deposits and for the `$200.00` member deposit -- whose period holds a
    `$2,473.38` and a `$100.00` row, so `$200.00` is not provably outside them.
    So the withholding costs this step nothing it was built for and holds back
    exactly the line whose books cannot be shown to be missing it.

    Args:
        review: What this pass offers, whose recordable inflows carry the
            placement a stated rule comes to for each.
        fresh: The ids of the lines this import was the first to record.

    Returns:
        ``(incomes, withheld)`` in the order the pass lists its lines.
    """
    incomes: "list[IncomeCreation]" = []
    withheld: "list[WithheldLine]" = []
    for inflow in review.recordable_inflows:
        if inflow.line.line_id not in fresh:
            continue
        placement = inflow.placement
        # A rule that reaches nothing here is the owner having said nothing,
        # or *ask me every time*, or a SPENDING answer whose refund arm is
        # ``bank_import:X-gj-2b``'s -- and only the last of those is this pass
        # withholding anything, which is why only it carries a reason.
        if placement is None:
            continue
        if placement.unresolved_reason is not None:
            withheld.append(WithheldLine(
                line=inflow.line, reason=placement.unresolved_reason,
            ))
            continue
        # **The DOOR's own refusals, asked before the rule's**: a day the
        # calendar does not reach and a day that has not happened yet are
        # reasons no answer can lift, and the pass has already composed the
        # sentence for each.
        if inflow.withheld is not None:
            withheld.append(WithheldLine(
                line=inflow.line, reason=inflow.withheld,
            ))
            continue
        # **THE PASS DID NOT FINISH LOOKING**, which is ruling **R-GH**'s own
        # narrowing and the one this function was missing (adversarial code
        # review 2026-08-31).  ``_verdict.ruled`` asks it FIRST on the outflow
        # side -- *a line the pass never finished looking at has not been shown
        # to collide with anything* -- and the same is true of a deposit.
        #
        # **The double-count guard below cannot stand in for it**, which is
        # what made the omission a money defect rather than a tidiness one:
        # :meth:`~._reads.ReviewSet.arrivals_already_held_in` tests
        # ``row.expected_on <= day <= row.expected_through``, so it sees only
        # rows in the deposit's OWN pay period, while the near tier reaches
        # across periods by ``DAY_WINDOW``.  A candidate row one period over is
        # therefore invisible to the guard and visible to the gap -- so a
        # deposit the pass explicitly declined to conclude about was being
        # filed with no press.
        gap = review.search_gap_for(inflow.line)
        if gap is not None:
            withheld.append(WithheldLine(
                line=inflow.line,
                reason=(
                    f"Your rule says this is income, and this pass did not "
                    f"finish looking for a row you already hold that could be "
                    f"the same money: {gap}.  It is left for you to check."
                ),
            ))
            continue
        held = review.arrivals_already_held_in(inflow.line)
        if held is not None:
            withheld.append(WithheldLine(
                line=inflow.line,
                # **The CLAUSE is the value's own** (plan step
                # ``bank_import:X-gj-2b``): the refund half composes the same
                # withholding, and a second spelling of it here drifted at
                # once -- this printed a bare ``Decimal`` where the card beside
                # it printed ``$2,473.38``.  The ADVICE is
                # ``_verdict.CHECK_FIRST``, so the receipt and the screen
                # send the owner to the same place.
                reason=(
                    f"Your rule says this is income, and "
                    f"{held.why_it_could_double_count}.  {CHECK_FIRST}"
                ),
            ))
            continue
        creation = placement.creation_for(inflow.line.line_id)
        if creation is not None:
            incomes.append(creation)
    return incomes, withheld


def file_new_swipes(scope: ReviewScope, import_id: int) -> RuleFiling:
    """File the lines *import_id* recorded that a standing rule answers for.

    Ruling **R-GH**, plan step ``bank_import:X-ge``.  **It MOVES MONEY**: every
    line it files becomes a purchase the app did not have, in the destination
    the owner's own rule names, dated the day the bank posted it.

    Does NOT commit -- the route owns the unit of work, exactly as
    :func:`~._batch.apply_reviewed` beneath it does.  So an import whose filing
    dies outside a designed refusal records no line either, which is what makes
    the receipt's account of what happened true rather than reassuring.

    **The SCOPE is the route's, for the reason** :func:`~._batch.apply_reviewed`
    **states**: only a route builds a read pass.  It is derived AFTER the
    import has staged its lines, because a pass derived before them cannot see
    the swipes this door exists to file.

    **Its two setup failures are deliberately NOT caught here.**
    ``ReviewScope.build`` raises ``PayCalendarError`` when the owner has
    paydays and no resolvable cadence, and ``BaselineMissingError`` when no
    scenario can price a row; both fail the whole request loud, which rolls the
    import back with it.  That is the right answer rather than a gap: in either
    state every money surface in the app is already unreachable, ruling
    **R-BW** has an application-level handler that renders the repair page for
    the second, and recording a statement is IDEMPOTENT -- so the owner fixes
    the calendar and imports the same file again at no cost.  Catching them
    would trade a loud, repairable failure for a silently half-run import.

    Args:
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`),
            built by the route after the import staged its lines.
        import_id: The import that JUST RAN, and the caller's contract is
            that it did.  Ruling **R-GI** reaches NEW swipe lines only, and an
            id is a number any caller can supply: handing this an OLDER
            import's id would file lines that have been sitting in the
            exception queue since, which is the backfill that ruling forbids.
            **Taking the ``ImportOutcome`` instead would make that structural
            and CANNOT be done** -- ``statement_import`` imports this package
            (``_reads`` and ``_undo`` take ``removals_by_match``), so the edge
            back is a cycle, which is the same wall
            :mod:`._vocabulary` documents for the source adapter's own
            knowledge.  Named by adversarial security review 2026-08-26; the
            one caller is the import route, which passes the outcome it has
            just received.
            Its account is *scope*'s: an import belongs to one account
            (``statement_imports.account_id``, and every sighting of a line
            is keyed to its import's account and its line's) and so does a
            pass,
            and taking the account from the SCOPE is what stops one door's
            import being filed against another door's account -- a foreign
            ``import_id`` yields no line at all rather than a foreign one.

    Returns:
        The :class:`RuleFiling`.
    """
    # **TWO exact short-circuits, before the pass is derived at all.**  A
    # re-import of an overlapping span records no fresh line, and an account
    # with no container answer has nothing a rule could place -- and in both
    # states the whole derivation returns exactly this.  It is worth naming
    # rather than leaving to the general path because that derivation is not
    # free on the WRITE door: measured on the developer's own account
    # 2026-08-26, ``ReviewScope.build`` plus ``review_set`` is 0.59-0.75 s and
    # 202 queries.  **The second half of that reason has been retired**: it
    # read "and the accepted-matches half of it grows with every act the
    # account accumulates", which plan step ``bank_import:X-gf-2`` made false
    # by taking the accepted acts out of ``review_set`` altogether.  What
    # survives is the candidate derivation, which grows with the account's ROWS
    # and is the bulk of that figure.  These two cover every import that has
    # nothing to do, which is every re-import the owner performs.
    fresh = _fresh_line_ids(scope.account_id, import_id)
    if not fresh or not _has_a_filing_rule(scope.owner_id, scope.account_id):
        return RuleFiling(outcome=BatchOutcome.nothing(), withheld=())
    review = review_set(scope)
    creations, withheld = _rule_filings(review, fresh)
    incomes, inflow_withheld = _inflow_filings(review, fresh)
    outcome = apply_reviewed(
        ReviewedBatch(
            matches=(),
            creations=tuple(creations),
            # **A rule FILES a deposit since plan step
            # ``bank_import:X-gj-2a``** (ruling **R-HT(a)**), where this was an
            # empty tuple whose comment read *a merchant answer says where that
            # merchant's SPENDING goes, so there is no answer it could hold
            # that means record this money coming in*.  That was true of a
            # four-member answer set; R-HT(a) added the member that means
            # exactly that.  ``__post_init__`` no longer refuses it, and the
            # MATCH tuple above stays empty because R-HT(b)'s group rule
            # modifies rows the owner made and so keeps its tick.
            incomes=tuple(incomes),
            # **A rule may not SKIP, and the refusal is the STORE's rather
            # than R-GH's** (plan step ``bank_import:X-gj-4b``).  R-GH would
            # permit one on its own terms -- a skip creates a decision and
            # modifies no row the owner made by hand -- but
            # ``budget.statement_line_skips`` has no ``applied_by_rule``
            # column, so nothing could record that a rule did it and three
            # surfaces would then state something false.  ``__post_init__``
            # refuses the batch that contradicts this, and the whole argument
            # is there.  It is also moot today: ruling **bank_import:R-JH**
            # holds that *never a purchase* states no disposition, so no
            # ``RuleAnswer`` member means skip and there is nothing for this
            # tuple to hold.
            skips=(),
            # **A rule is the consent** (**R-GH**), which is what
            # ``ReviewedBatch.__post_init__`` holds the empty match tuple above
            # to: the acts a rule may not perform are exactly the acts that
            # reach ``accept_match``.
            consent=Consent.STANDING_RULE,
        ),
        scope,
    )
    # **ONE withheld list over both directions, in the order the pass lists
    # its lines**: the receipt reads down the page and an owner does not think
    # in directions, so a deposit a rule held back and a swipe a rule held back
    # are one section rather than two.
    return RuleFiling(
        outcome=outcome, withheld=tuple(withheld + inflow_withheld),
    )
