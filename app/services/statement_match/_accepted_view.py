"""What an ACCEPTED match looks like now, and whether it still holds.

Plan step ``bank_import:X-f6a-2`` built this reader; plan step
``bank_import:X-f6a-3d`` moved it out of :mod:`._reads`, which had grown past
the 1,000-line module ceiling.  The boundary is a real one rather than a place
to cut: :mod:`._reads` answers *what is there to do*, and this answers *what
was already done and does it still say what it said*.

**They are read by two different SCREENS since plan step
``bank_import:X-gf-2``** (ruling **bank_import:R-GX**), which is the same
boundary made visible: the review screen was the exception queue, and every
act already accepted is settled -- on the register then, and on the Reconcile
page's two settled tabs since plan step ``bank_import:X-gj-1c`` retired it --
where it is found and undone.  So this
module no longer runs inside the review pass at all -- it was valuing 221 acts
on the developer's own account on every render of a page whose panel for them
he was not reading -- and it needs no :class:`~._scope.ReviewScope`, no
calendar and no candidate derivation of its own.

**Agreement is DERIVED, never stored** (:mod:`app.models.statement_match`).
Nothing records the day a match asserted -- that is ``max(posted_on)`` over its
bank lines, and each member row carries it in its own ``settled_on``.  So an
owner who later moves a day by hand puts the group out of agreement with the
bank, and :attr:`AcceptedGroup.agrees` SHOWS it rather than a release nobody
can see: the repair door finding **N-302** says a refusal owes.

Services-boundary discipline: reads only, plain data in, frozen dataclasses
out, no Flask import.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, status_seam, transfer_legs
from app.utils.log_events import (
    ERROR,
    EVT_STATEMENT_MATCH_LINELESS,
    log_event,
)

from ._acts import NAMES_A_BANK_LINE, acts_of, named_rows
from ._release import PlannedRemovals, planned_removals
from ._sides import MatchSides

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcceptedRow:
    """One member of an accepted match, as the screen lists it.

    Attributes:
        label: What to call the app row.
        settled_on: The day it currently records, which is the bank's own day
            for a match still in agreement.
        cash_amount: Its signed cash effect NOW -- what its covering movement
            or its own movement moves (ruling **R-BAL81**), ``0.00`` for a
            member soft-deleted or reverted since.  Carried because
            :func:`_still_holds` re-asks the balance the accept door checked,
            and such a member contributes zero -- which no test over days
            could see.  It was ``Decimal | None`` while a reverted member's
            valuation could refuse (through ``X-bi-4a``'s first cut).
        agrees: Whether that day is still the one the match asserted.
    """

    label: str
    settled_on: "date | None"
    cash_amount: Decimal
    agrees: bool


@dataclass(frozen=True)
class AcceptedGroup:  # pylint: disable=too-many-instance-attributes
    """One accepted match, as the screen lists it.

    Pylint: ``too-many-instance-attributes`` (9/7) -- **nine because the two
    surfaces that render an act read nine disjoint facts about it**, not
    because the value wants splitting.  The ninth is
    :attr:`created_every_row`, which the Reconcile screen's card reads to say
    ADD or MATCH and which nothing else can answer: the act's members and its
    creations are both loaded here and nowhere downstream.  The eighth is
    :attr:`applied_by_rule`, which arrived with plan step ``bank_import:X-ge``
    and is the whole reason ruling **R-GT** stores a column at all: WHICH rule
    fired is derivable from the matched line, and THAT one fired is not, so a
    reader that could not see it could not tell an act the owner pressed from
    one the app performed for them.  Dropping a field to meet a limit is what
    :class:`~._accept.AcceptedMatch`'s own disable records the cost of: the
    receipt said *"Nothing moved."* over a rewritten figure.  The obvious
    grouping -- :attr:`agrees` beside :attr:`removes` beside this -- is three
    facts a template reads separately and one condition each, so a nested value
    would be the speculative shape ``CLAUDE.md`` rule 13 forbids.
    :class:`~._creations.PurchaseDestination`, :class:`~._subjects.CandidateRow`,
    :class:`~._creations.CreatedPurchase` and
    :class:`~._outcome.BatchOutcome` carry the same disable for the same reason.

    Attributes:
        match_id: The act, so the screen can offer to release it.
        posts_on: The day the match asserted -- the latest of its bank days,
            derived here rather than stored (see
            :mod:`app.models.statement_match`).
        amount: The signed total its bank lines state.
        descriptions: What the bank called each line.
        rows: Its app rows.
        removes: What an Undo would take back
            (:class:`~._release.PlannedRemovals`) -- the rows this act CREATED
            and the money the account would stop recording.  **From the release
            door's OWN derivation** (:func:`~._release.planned_removals`),
            which is the shape :func:`~._preview.preview_hand_build` already
            has for the accept door: two spellings would let the screen promise
            one thing and the button do another, and this button destroys
            records.  It is empty for every match between rows that already
            existed, which is most of them.
        applied_by_rule: Whether a STANDING RULE performed this act rather than
            a person ticking it (ruling **R-GT**, plan step
            ``bank_import:X-ge``).  **The column exists so that this can be
            SHOWN**: it is the one fact about an act that is not derivable from
            what the act names, which is R-GT's own argument for storing it and
            against a foreign key to the rule row -- and a fact written and
            never seen is the shape this arc keeps finding.  It is what lets
            the import receipt list exactly the acts nobody pressed, and what
            lets the register say which of the accepted matches the owner
            agreed to line by line.  **No default, which is the discipline every
            other link in this chain keeps**: ``ReviewedBatch.consent``,
            ``create_purchase_from_line``'s keyword-only flag and the column
            itself all refuse one, because the two values are *the owner agreed
            to this* and *the app did it on their behalf* -- and a default
            claims the first by omission.  It defaulted to ``False`` until an
            adversarial security review named it, 2026-08-26: the DISPLAY half
            of a consent fact may no more assume consent than the writing half.
        created_every_row: Whether EVERY row this act names is one it made,
            which is the difference between ADD and MATCH in the Reconcile
            screen's vocabulary (ruling **bank_import:R-HP**, plan step
            ``bank_import:X-gj-1a``).  A MATCH says *this line IS a row the
            books already hold*, so an act naming even one row it did not
            create is one.
            **It is NOT "did this act create anything"**, and that reading is
            wrong on the case this whole arc exists for: an unbalanced group
            mints ruling **R-FN**'s residual row and records it as a creation
            (:func:`~._accept.accept_match`), so a payroll MATCH -- seven
            deposits and `$18,132.63` on the developer's own account -- creates
            something while matching rows that already existed, and would have
            been captioned *Added*.  Reproduced by adversarial review
            2026-08-29 against ``test_residual``'s own fixture.
            **Nor is it "did it create a PURCHASE"**: ruling **R-GW**'s income
            door creates an uncategorized TRANSACTION and is an ADD, which a
            kind test would call a match.  What separates them is whether
            anything PRE-EXISTED, and that is what this asks.
        agrees: Whether the match still HOLDS -- which is three questions, not
            one, and a first draft asked only the first.  Every row still
            carries ``posts_on``; the act still names at least one row; and the
            rows still SUM to what the bank stated.  The second and third are
            what a CASCADE breaks: deleting a purchase or destroying a pay
            period removes that member silently, and a day-only test then
            reports a group that explains less than it claims -- or, when every
            row goes, nothing at all -- as still agreeing with the bank.  False
            is not a corruption; it means the match wants re-reviewing, and the
            screen offers it for exactly that.
    """

    match_id: int
    posts_on: date
    amount: Decimal
    descriptions: "tuple[str, ...]"
    rows: "tuple[AcceptedRow, ...]"
    created_every_row: bool
    agrees: bool
    removes: PlannedRemovals
    applied_by_rule: bool


def accepted_groups(
    owner_id: int, account_id: int, match_ids: "set[int] | None" = None,
    *, applied_by_rule: "bool | None" = None,
) -> "list[AcceptedGroup]":
    """Return this account's accepted matches, newest first.

    Args:
        owner_id: The user whose matches to list.
        account_id: The account.
        applied_by_rule: Which half of the accepted set to describe --
            ``True`` for the acts a standing rule performed (**R-GT**),
            ``False`` for the acts a person ticked, ``None`` for both.
            Threaded into :func:`~._acts.acts_of`'s own query, so a caller
            asking for one half never loads or prices the other.
        match_ids: The acts to describe, or ``None`` for all of this
            account's.  **It is :func:`~._acts.acts_of`'s own parameter,
            surfaced rather than reimplemented** (plan step
            ``bank_import:X-ge``): the import receipt names a SUBSET -- the
            acts a standing rule performed -- and a caller filtering this
            function's output instead would load and value every act on the
            account to render twenty, which is the unbounded fold an
            adversarial review measured at 475 queries one reader over.  An
            empty set means no act, and returns nothing rather than
            everything, which is that function's rule and the only reading
            that does not turn a filter into its own opposite.

    Returns:
        One :class:`AcceptedGroup` per act.  A group whose rows no longer carry
        the day it asserted is flagged rather than hidden: it is the shape a
        later hand edit produces, and the screen is where it can be re-reviewed.
    """
    # ONE loader, shared with the import page's delete preview
    # (:func:`~._acts.acts_of`): an act is only readable with BOTH its
    # relations AND the row each of them names -- what it names decides
    # whether it still holds, and what it created decides what the Undo
    # control would take back.  It narrows by the owner as well as the
    # account, which is the pair the write door itself uses, and every subject
    # below arrives through a join carrying that account
    # (:data:`~._acts._WHOLE_ACT`, finding **bank_import:N-358**).
    #
    # **That replaced three by-id reads and a warm** (plan step
    # ``bank_import:X-gf-2``).  This fold collected the member ids and selected
    # the lines, transactions and purchases back by primary key alone, and
    # separately warmed the creations' subjects into the identity map -- a warm
    # whose result had to be held, because that map's references are weak.
    # Nothing leaked through the by-id reads, every id having come from a
    # scoped act, but that is safety by DERIVATION over an open set of future
    # callers; and a discipline about holding a variable is one a reader can
    # drop without anything saying so.  Both are now properties of how the act
    # is loaded.
    matches = acts_of(
        owner_id, account_id, match_ids,
        applied_by_rule=applied_by_rule,
    )
    if not matches:
        return []

    groups = []
    for match in matches:
        # **Every act reaching this fold names a bank line, and that is now the
        # LOADER's clause rather than a guard here** (plan step
        # ``bank_import:X-gj-1c``, finding **N-389**).  A Python skip sitting
        # here was the same invariant spelled a second time, in a language
        # :func:`accepted_counts` could not share -- so the caption came from
        # the table and the cards from this loop, and one lineless act made the
        # Explained tab promise a card it could not draw.
        # :data:`~._acts.NAMES_A_BANK_LINE` is that invariant stated once;
        # ``acts_of`` narrows on it and the counts read narrows and ALARMS on
        # it, so there is nothing left for this loop to defend against.
        #
        # The three guarantees that make the state unreachable at all are still
        # ``record_match``'s refusal at the one writer,
        # ``fk_statement_match_members_line_account`` no longer cascading, and
        # migration ``e4a7c0f13b92``'s deletion of the acts that already held
        # none (plan step ``bank_import:X-f6a-4``).
        #
        # **The clause and this loop read two different things, and the gap is
        # the SESSION** -- named by adversarial review 2026-08-31.  The WHERE
        # runs in the database; ``match.members`` is a loaded collection, and
        # SQLAlchemy does not overwrite one already populated without
        # ``populate_existing()``.  So an act whose members were emptied
        # IN-SESSION while the rows still stand would satisfy the clause and
        # reach ``max()`` empty.  Nothing in ``app/`` produces that: the one
        # writer that empties a membership is ``release_match``, which deletes
        # the ACT in the same flush, so there is no state in which this fold
        # can be handed one.  It is recorded rather than guarded because a
        # guard here would be the second spelling finding **N-389** is about.
        match_lines = [
            member.line for member in match.members
            if member.bank_statement_line_id is not None
        ]
        posts_on = max(line.posted_on for line in match_lines)
        rows = [
            _accepted_row(member.entry, posts_on, match.account_id)
            for member in match.members
            if member.transaction_entry_id is not None
        ]
        # **Asked where both relations are loaded**, which is only here: what
        # an act NAMES and what it MADE are two tables
        # (:class:`~app.models.statement_match.StatementMatchCreation`), and
        # every reader downstream has the labels rather than the ids.  What
        # it names is read through :func:`~._acts.named_rows` (plan steps
        # ``credit_card:CC-5-4a-1`` / ``CC-5-4a-2``): a member naming a row's
        # covering movement names THAT ROW and not a purchase, so a residual the act
        # minted -- created as a row, named as its payment -- still meets
        # its creation here, and a payment member is never mistaken for a
        # purchase the act would have had to create.  A transfer side's
        # payment names its LEG (a ``(transfer id, account id)`` pair, plan
        # step ``balance:X-bi-6-4d-2``), which no creation's key can equal.
        created_keys = {
            (creation.transaction_id, creation.transaction_entry_id)
            for creation in match.creations
        }
        named_items, named_purchases = named_rows(match)
        member_keys = (
            {(item, None) for item in named_items}
            | {(None, entry_id) for entry_id in named_purchases}
        )
        groups.append(AcceptedGroup(
            match_id=match.id,
            posts_on=posts_on,
            amount=sum(
                (Decimal(str(line.amount)) for line in match_lines),
                Decimal("0.00"),
            ),
            descriptions=tuple(line.description for line in match_lines),
            rows=tuple(rows),
            # **An act naming NO row created nothing it names**, so the empty
            # case is a MATCH rather than vacuously an ADD -- which is what a
            # bare subset test would answer for a group every one of whose
            # rows a cascade has removed.
            created_every_row=bool(member_keys) and member_keys <= created_keys,
            agrees=_still_holds(rows, match_lines, posts_on),
            removes=planned_removals(match),
            applied_by_rule=match.applied_by_rule,
        ))
    return groups


#: How many SETTLED acts the register renders before it stops.
#:
#: **A bound on what is RENDERED, never on what is read** (ruling
#: **bank_import:R-GX**, developer 2026-08-27).  Whether an act still holds is
#: a VALUATION over its member rows (:func:`_still_holds`) and not a query, so
#: the fold has to reach every act to know which ones no longer do -- and an
#: act that no longer holds is the one thing on that page worth finding.  So
#: every such act is rendered whatever its age, and the bound falls only on the
#: remainder, which is a log.
#:
#: Measured on the developer's own data 2026-08-27: 221 acts, 216,637 bytes,
#: 0 of them out of agreement -- so this cuts the register's own card to about
#: a quarter of that, and cuts nothing an owner is looking for.
REGISTER_LIMIT = 50


@dataclass(frozen=True)
class AcceptedCounts:
    """How many acts this account has ACCEPTED, and how many a rule performed.

    Plan step ``bank_import:X-gj-1a``.  The Reconcile screen shows a count on
    every tab whichever tab is open, so it needs these two numbers on every
    render -- and :func:`accepted_register` is the wrong way to get them:
    it VALUES up to :data:`REGISTER_LIMIT` acts to produce them, re-pricing
    every member row through :func:`~._release.planned_removals` and
    :func:`_still_holds`.  Measured on the developer's own account
    2026-08-29: 221 accepted acts, of which 0 by rule.

    **Two counts and not one**, because they partition the same set: the
    Filed-by-rules tab holds exactly the acts nobody pressed (**R-GT**), and
    Explained holds the rest.  A screen deriving one from the other by
    subtraction in Jinja would be computing in a template.

    Attributes:
        total: Every accepted act on this account that a tab can render --
            which is every act it holds, the state these exclude being one the
            app's writers cannot produce (:data:`~._acts.NAMES_A_BANK_LINE`).
        by_rule: Those a standing rule performed (**R-GT**).
    """

    total: int
    by_rule: int

    @property
    def by_hand(self) -> int:
        """Return how many acts a person ticked.

        Returns:
            The remainder, which is the Explained tab's own count.
        """
        return self.total - self.by_rule


def accepted_counts(owner_id: int, account_id: int) -> AcceptedCounts:
    """Return how many acts this account has accepted, and how many by rule.

    **ONE read of three aggregates**, rather than three queries or one list
    valued for its length.

    **It filters on the OWNER as well as the account**, which is the narrowing
    :func:`~._acts.acts_of` already applies for the reason recorded there:
    the account implies the owner, and a reader feeding a screen narrows by
    the same columns the write door does.

    **It counts what a TAB CAN DRAW, through the loader's own clause**
    (:data:`~._acts.NAMES_A_BANK_LINE`, plan step ``bank_import:X-gj-1c``,
    finding **N-389**).  It counted the table until then, while
    :func:`accepted_groups` skipped an act naming no bank line in Python -- so
    a caption derived here and cards derived there disagreed by one for every
    such act.  Measured on a planted act 2026-08-31: caption ``1``, rendered
    ``0``, withheld ``0``.  One clause now narrows both.

    **The excluded acts are counted in the SAME aggregate and logged at
    ERROR, NAMING each one**, which is where the alarm the Python skip used to
    raise went.  It belongs here rather than in the fold for two reasons: this
    is the read the Reconcile page performs on EVERY render whichever tab is
    open, so the operator is told whenever anyone looks; and it is the only
    reader that sees the whole set, the fold now being unable to receive one.
    Skipping SILENTLY was the original defect (two adversarial reviews,
    2026-08-20) -- such an act goes on claiming its transactions in
    ``matched_subjects``, so those rows can never be matched again and no
    release control exists to free them -- and RAISING would take the screen
    down for the account with no in-app repair, which is finding **N-302**'s
    shape.

    **The IDS are what the alarm is FOR, and a first version dropped them.**
    The Python skip it replaced logged ``match_id``, and its own comment called
    that "naming the row to delete"; carrying only a count told an operator how
    many rows they could not find.  Adversarial review measured the loss
    2026-08-31.  The second read runs ONLY when the count is non-zero -- which
    is never, on any account the app's own writers built -- so the ordinary
    render still pays one query.

    Args:
        owner_id: The user the route proved owns the account.
        account_id: The account to count for.

    Returns:
        The :class:`AcceptedCounts`, over the acts a tab can render.
    """
    total, by_rule, lineless = db.session.query(
        db.func.count(StatementMatch.id).filter(NAMES_A_BANK_LINE),
        db.func.count(StatementMatch.id).filter(
            NAMES_A_BANK_LINE,
            StatementMatch.applied_by_rule.is_(True),
        ),
        db.func.count(StatementMatch.id).filter(~NAMES_A_BANK_LINE),
    ).filter(
        StatementMatch.account_id == account_id,
        StatementMatch.user_id == owner_id,
    ).one()
    if lineless:
        log_event(
            _logger, logging.ERROR, EVT_STATEMENT_MATCH_LINELESS, ERROR,
            "Accepted match(es) on this account name no bank line.",
            account_id=account_id, lineless_count=lineless,
            match_ids=sorted(
                row[0] for row in db.session.query(StatementMatch.id).filter(
                    StatementMatch.account_id == account_id,
                    StatementMatch.user_id == owner_id,
                    ~NAMES_A_BANK_LINE,
                ).all()
            ),
        )
    return AcceptedCounts(total=total, by_rule=by_rule)


@dataclass(frozen=True)
class AcceptedRegister:
    """The accepted acts a register renders, and how many it withheld.

    Plan step ``bank_import:X-gf-2``, ruling **bank_import:R-GX**.  **The
    count travels with the rows** because a truncated list that does not say
    it is truncated is a page claiming to be the whole record -- and this one
    is the only surface where an act can be found and undone.

    Attributes:
        shown: The acts to render: every one that NO LONGER HOLDS, newest
            first, then the newest :data:`REGISTER_LIMIT` of the rest.  The
            two orders are one sort, so an act that stops agreeing rises to
            the top of the page rather than staying where its date put it.
        withheld_count: How many acts the bound left out -- ``0`` when the
            whole record is on screen, which is what tells the surface whether
            to offer the link that shows everything.
    """

    shown: "tuple[AcceptedGroup, ...]"
    withheld_count: int


def accepted_register(
    owner_id: int, account_id: int, limit: "int | None" = REGISTER_LIMIT,
    *, applied_by_rule: "bool | None" = None,
) -> AcceptedRegister:
    """Return this account's accepted acts as a register renders them.

    Args:
        owner_id: The user whose acts to list.
        account_id: The account.
        limit: How many SETTLED acts to render, or ``None`` for all of them --
            which is what the *show everything* link asks for.  An act that no
            longer holds is never subject to it.
        applied_by_rule: Which half of the accepted set to return -- ``True``
            for the acts a standing rule performed, ``False`` for the acts a
            person ticked, ``None`` for both, which is the register page's own
            question and this parameter's default.

            **It narrows in SQL, before the bound and before anything is
            priced** (plan step ``bank_import:X-gj-1a``).  Both halves of that
            matter: a caller filtering this function's RESULT would take the
            newest :data:`REGISTER_LIMIT` of BOTH and then drop the other half
            from them, AND would have loaded and valued every act on the
            account to do it.  The Reconcile screen shows the two
            halves as two tabs (**R-GT**), and a caller slicing this function's
            result would take the newest :data:`REGISTER_LIMIT` of BOTH and
            then drop the other half from them: with 60 hand acts and 10 rule
            acts the Explained tab would render about 43 rows under a tab
            captioned *60*, which is a caption promising a number the tab does
            not deliver -- the defect the retired queue's ``_sweeps_for``
            existed to refuse, one surface over.

    Returns:
        The :class:`AcceptedRegister`.  Its :attr:`~AcceptedRegister
        .withheld_count` is over the narrowed set, so a tab that states it
        states its own truncation and not the whole account's.
    """
    groups = accepted_groups(
        owner_id, account_id, applied_by_rule=applied_by_rule,
    )
    # **Stable, so the second key is the order** :func:`~._acts.acts_of`
    # **already returned** (newest first) rather than a second sort restating
    # it -- and so an act that stops agreeing moves to the top without
    # disturbing anything else's order.
    ordered = sorted(groups, key=lambda group: group.agrees)
    if limit is None:
        return AcceptedRegister(shown=tuple(ordered), withheld_count=0)
    asking = sum(1 for group in ordered if not group.agrees)
    keep = asking + limit
    return AcceptedRegister(
        shown=tuple(ordered[:keep]),
        withheld_count=max(len(ordered) - keep, 0),
    )


def _accepted_row(
    entry: TransactionEntry, posts_on: date, account_id: int,
) -> AcceptedRow:
    """Return one member of an accepted match, valued as it stands NOW.

    **The valuation is the cash ledger's, and it is what makes a soft-deleted
    member visible.**  ``covered_cash_leg`` answers ``0.00`` for a row that
    contributes nothing -- soft-deleted, Credit or Cancelled -- and for one
    whose covering movement is un-dated (REVERTED since the match was
    accepted, plan step ``X-bi-3e-2``), so a member that has quietly left
    the balance shows up in :func:`_still_holds`'s sum even though its
    recorded day is untouched.  A purchase takes the same gate through its
    PARENT, which is ruling **R-FM**: a non-contributing row's purchases post
    nothing either.  Nothing here can fail to price: a movement's figure is
    stored (ruling **R-BAL81**).  Through ``X-bi-4a``'s first cut the
    transaction arm read ``settled_family_leg``, which REFUSED a reverted
    member, and this caught the refusal as ``None`` so the group was offered
    for re-review rather than the page raising (finding **N-302**'s shape);
    the arithmetic says the same thing now.

    **A member that names a row's PAYMENT is valued as that row's payment ON
    THIS ACCOUNT** (plan steps ``credit_card:CC-5-4a-1`` / ``CC-5-4a-2``,
    rulings **R-CC43**, **R-CC45**): every act names the covering movement
    rather than the row, and the member key holds the MOVEMENT to the act's
    account.  ``covered_cash_leg`` is that movement while it is dated and
    ``0.00`` once a revert un-dates it (a "Paid from" correction cannot leave
    it here: the member goes with the re-point, ruling **R-CC46**); and it is
    labelled by the row it pays.  The acts recorded before ``CC-5-4a-1``
    named the ROW and were valued by the same ``covered_cash_leg`` on the
    row, so migration ``2eabfa596ee0`` re-keyed them onto the payment
    without moving a label or a figure.  The DAY (and so the agreement) is
    the payment's, which reads as the row's wherever the seam's mirror holds
    -- 103 of 103 on production's shape; the migration counts and prints any
    member whose payment is dated on another day.

    **A transfer's payment is valued and named through its LEG** (leaf
    ``balance:X-bi-6-4c-1``): the member's parent is read by
    ``transfer_legs.movement_parent`` -- the plan row, or the transfer leg
    whose record the movement is -- and ``covered_cash_leg`` asks either
    the same question (a leg answers ``covering_movements`` with its
    record), so one expression values a bill's payment and a transfer's
    with no shape branch here.  Until that leaf a transfer's was read off
    the SHADOW row the movement hangs off (its stored ``name``; its
    contributing gate): the label is now the leg's, composed from the
    endpoints' CURRENT names, and the gate the TRANSFER's (ruling **R-JM**:
    the parent decides), which the shadow's equals on every door-written
    state.

    Args:
        entry: The :class:`~app.models.transaction_entry.TransactionEntry`
            the member names -- a person's purchase or a row's payment.
        posts_on: The day the match asserted.
        account_id: The match's account -- the statement the member was
            matched on, and the account a row's payment is valued ON.

    Returns:
        Its :class:`AcceptedRow`.
    """
    if entry.covers_settlement:
        parent = transfer_legs.movement_parent(entry)
        return AcceptedRow(
            label=parent.name, settled_on=entry.settled_on,
            # What the parent's covering movement moves ON THIS ACCOUNT
            # (rulings **R-BAL81**, **R-CC40**) -- the same valuation the
            # offer and the post-apply check use.
            cash_amount=status_seam.covered_cash_leg(parent, account_id),
            agrees=entry.settled_on == posts_on,
        )
    # **A CARD purchase moves no cash through THIS account at all** -- it
    # leaves later through its own CC Payback sibling, which is why
    # ``credit_entry_sum`` removes it from its parent's leg and the posted
    # walk filters it out.  ``update_entry`` supports that flip, and the
    # purchase keeps its ``settled_on`` through it, so a valuation reading the
    # magnitude alone reported a match as still explaining money that had
    # stopped being on this statement.  Found by adversarial financial review
    # 2026-08-17.
    return AcceptedRow(
        label=entry.description, settled_on=entry.settled_on,
        # The ONE valuation of a movement (plan step X-bi-3b): its figure in
        # its parent's direction, ``0.00`` for a card purchase or one under a
        # non-contributing parent -- the three clauses this spelled inline.
        cash_amount=cash_ledger.movement_cash_leg(entry.transaction, entry),
        agrees=entry.settled_on == posts_on,
    )


def _still_holds(
    rows: "list[AcceptedRow]",
    lines: "list[BankStatementLine]",
    posts_on: date,
) -> bool:
    """Return whether an accepted match still says what it said when accepted.

    Two questions, because a group can lose a member, or a member stop
    contributing, without touching a single day:

    * every row still carries the day the match asserted;
    * the rows still SUM to what the bank stated -- the invariant
      :func:`~._accept.accept_match` checks before it writes, asked again of
      what survives.

    **A third came first until plan step ``credit_card:CC-5-4a-4``: whether
    the act still named ANY app row** (``all([])`` is True, so a match that
    had lost every row -- its movements destroyed by a cascade -- reported
    agreement while explaining nothing).  The leftover-match check is deleted
    with that step (ruling **R-CC54**): the movement's key is NO ACTION and
    the one act that takes a movement off the books withdraws an act it
    empties, so an act with no app row is unrepresentable rather than caught
    here.

    Args:
        rows: The act's app-row members as the screen holds them.
        lines: Its bank lines.
        posts_on: The day it asserted.

    Returns:
        Whether the match still holds.  A soft-deleted member is caught by the
        SUM rather than by the day: it keeps its ``settled_on`` and contributes
        nothing to any balance, so only the total can see it has gone.
    """
    if any(row.settled_on != posts_on for row in rows):
        return False
    # **Through the door's OWN derivation** (plan step ``bank_import:X-f6d-4``).
    # This reader summed and rounded the two sides itself until then, with a
    # comment saying it must match "``_accept._reject_unbalanced``" -- a
    # function that has not existed for two steps.  Two spellings of one
    # invariant is a match the door accepted that this reader calls broken
    # forever, and a citation that resolves to nothing is how the second
    # spelling survived.  ``MatchSides.of`` is STRUCTURALLY typed over anything
    # exposing ``amount`` / ``cash_amount``, which is exactly why these two
    # shapes can reach it.
    return not MatchSides.of(lines, rows).difference
