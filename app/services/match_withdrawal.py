"""What a bank line's match means once the app row it named has gone.

**A match ASSERTS an identity** -- *these bank lines and these app rows are one
movement* (:mod:`app.models.statement_match`).  Destroying one of those rows
makes the assertion false, so the assertion is withdrawn and the bank lines it
explained become unexplained again.  That is the whole of this module, and the
one act that takes a movement off the books asks it first
(:mod:`app.services.movement_removal`).

**An act is withdrawn only when it would be left naming NO APP ROW AT ALL**,
which is the narrowest condition that reaches the goal, and two adversarial
reviews measured what a wider one costs (2026-08-25).  Withdrawing on the loss
of ANY member destroyed a group act that was still two-thirds true: one line
against three rows, one row deleted, and the other two were silently
un-matched while keeping the settle days the act had given them.  A partial
loss is exactly what
:attr:`~app.services.statement_match.AcceptedGroup.agrees` is for -- it fails
the SUM, tints the act amber and offers the Undo -- so this writer fires on the
one case that flag cannot repair by itself: an act with nothing left to
re-review.  The two mechanisms split on one predicate rather than shadowing
each other -- :func:`_loses_every_row` -- which was also ``_still_holds``' own
first branch until plan step ``credit_card:CC-5-4a-4`` made an act naming no
app row unrepresentable and deleted that branch.

**Whether a SOFT delete withdraws is the CALLER's to say, and the two callers
now say the same thing.**  A TRANSACTION's soft delete -- one occurrence of a
recurring definition, kept as a tombstone -- takes the row's payments and
purchases off through the one act exactly as its hard delete does (ruling
**R-CC75**, developer 2026-09-23: *"Deleting the occurrence takes its payments
and purchases off the books through the one removal act, exactly as deleting
a one-off does"*), so an act the tombstone empties is withdrawn here and its
line is unexplained again (``transaction_service._delete._leaves_the_books``;
the tombstone counts as leaving, ruling **R-CC84**).  Until R-CC75 it withdrew
nothing, on the argument that a shipped button reverses a soft delete; what
that left was a hidden row holding money the balance does not count, under a
match that read explained.  A
TRANSFER's soft delete withdraws too since plan step ``balance:X-bi-6-4d-2``
(finding **BAL-532**): ``transfer_service.delete_transfer`` takes its sides'
records off through the same act on either arm, because the database refuses
to hide a transfer still holding one (:mod:`app.deleted_row_infrastructure`'s
transfer arm).  Until then it withdrew nothing, and a reverted leg's kept
payment stayed under a soft-deleted shadow.

**What it does NOT do is remove rows the withdrawn act CREATED**, and the
asymmetry is deliberate.  ``release_match`` is the owner's UNDO -- *withdraw
this act, and take back what it made* -- and it refuses where the owner has
edited a created row since.  This is a different act: the owner asked to delete
ONE row, not to withdraw a decision.  What survives is COUNTED
(:attr:`MatchWithdrawal.kept_rows`), and counted over the rows the owner still
has: a subject in the going set -- deleted by the same press, or a recurring
occurrence the press empties and hides (ruling **R-CC84**) -- is not one, and
reporting it as kept is the *"Nothing moved."* shape this arc has
shipped once already (finding **N-336**).  A first build counted every creation
of every withdrawn act, and both reviews measured it promising a `-$21.68`
residual would stay while the press destroyed it.

**A MOVEMENT that LEAVES ITS ACCOUNT leaves the acts naming it too** (plan
step ``credit_card:CC-5-4a-1``, ruling **R-CC46**, developer 2026-09-21).
Every accepted act names movements since ruling **R-CC43**, and a member is
held to its movement's account by ``fk_statement_match_members_entry_account``
-- a key the database will not let the movement's ``account_id`` walk out
from under (``NO ACTION`` on update).  A match asserts that THIS account's
bank line IS this movement; money that moved on another account was shown by
no such line, so the assertion is false the moment the movement is re-pointed
(a "Paid from" correction, ``status_seam._covering._re_point``), exactly as
it is false the moment the movement is destroyed.  So the member goes as a
DELETE would have taken it, and an act left naming no app row is withdrawn on
the same narrowest condition -- :func:`withdraw_for_moved_movement`, with
:func:`pending_for_movements` for the screen that offers the move.  The
refused alternative kept the owner's correction from happening while a match
stood, naming the register's Undo as the remedy.

**A movement that LEAVES THE BOOKS leaves through ONE act, and this is that
act's match step** (plan step ``credit_card:CC-5-4a-3``, ruling **R-CC54**,
developer 2026-09-22).  :func:`take_out_of_matches` is the match step of
:func:`app.services.movement_removal.remove_movements` -- take the going
movements out of every act naming them, withdraw an act that leaves naming no
app row -- after that act has reversed their ledger and before it deletes
them.
The doors that remove a movement call THAT act rather than this module: the
transaction delete verb, the purchase delete, both CC-payback teardowns, the
transfer delete, and the status seam's ``$0.00`` / ``purchases`` record
(finding **CC-358**: until this step the seam deleted the payment itself and
withdrew nothing, so the act kept its line alone and a re-match raised on
``uq_statement_match_members_line``).  The member of an act that KEEPS
another app row is taken out explicitly, as the move always did: the act is
the one path a movement leaves by, and the member key does not cascade.

**"Every door" is structural since plan step ``credit_card:CC-5-4a-4``**
(rulings **R-CC54**, **R-CC63**..**R-CC65**, closing finding **CC-363**).
Three BULK doors destroyed movements without the act -- the template and
account permanent deletes and the pay-period retire -- because they judged
what they may destroy by a row's STATUS rather than by what it held
(measured: a permitted template delete erased a `$25.00` purchase recorded
from a bank line, and the act kept its line alone).  Now a row holding a
movement is history those doors keep, and neither the row's key to its
movements nor the member's key to its movement cascades, so no statement can
empty an act behind this module's back.  The read-time predicate that
stopped counting such an act's line as explained
(``statement_match._candidates.act_still_names_a_row``) is DELETED with it:
an act naming no app row is unrepresentable rather than filtered.  What this
module adds is the CLEANUP and the DISCLOSURE: the act the press empties goes,
and a door that discloses names the lines it frees before the press -- the row
delete (ruling **R-CC75**) and the two popovers (**R-CC56**, **R-CC59**).

**And the act ASKS what the owner was shown, ONCE PER SAVE** (plan step
``credit_card:CC-5-4a-5``, rulings **R-CC81**, **R-CC127** and **R-CC135**).
Every door whose page names lines, or that stays silent by a ruling, opens ONE
:class:`~app.services.match_press.Press` around its unit of work over the
bank lines its page named (:class:`~app.services.match_press.Shown`) or what
lets it stay silent (:class:`~app.services.match_press.Silent`), and threads
it to every call of :func:`take_out_of_matches` the save makes: a call
freeing a line the page did not name is refused at once, and before the door
commits, what the whole save freed must equal what the page named -- a page
drawn before a match existed or after another tab freed one, or a door that
forgot its caption.  A door that passes no press -- its page names
nothing -- has each call open one of its own over :data:`NOTHING_SHOWN`,
which refuses every line it would free: the same answer, one call at a
time.
Ruling **R-CC135** (developer 2026-10-04, "One check per save"): *"Every
button that can undo a match checks once per save: undoing one the page did
not name stops it at once, and before saving, what it undid must equal what
the page named for what you ticked, or nothing saves and the page
redraws"*.  The owner's one-click Mark Paid is silent by ruling (**R-CC56**),
and a companion's is refused whenever it would free a line, because no page
may show a companion the owner's statement
(:class:`~app.services.match_press.OwnerOnly`, ruling
**R-CC130**) -- as is its purchase X and CC un-tick (ruling **R-CC132**).
The purchase X, the CC un-tick, Undo CC and Status leaving Credit name their
lines first since plan step ``credit_card:CC-5-4a-5b`` (ruling **R-CC80**,
finding **CC-367** closed), and the reconcile panel since ``CC-5-4a-5c-1``:
the tick it captions -- a row settling from its purchases over a kept
payment -- names its lines under the row, a typed ``$0.00`` box is refused
(ruling **R-CC125**), and neither list offers a tick that would move a
payment between accounts (ruling **R-CC126**; finding **CC-378** closes at
that leaf's tick).  An act naming the payments of SEVERAL rows is named
under each of them with the rows that must all close
(:func:`pending_alone_and_together`, leaf ``5c-2c-1``, ruling **R-CC135**,
finding **CC-384**), and its lines count as named only when all of those
rows are ticked.  Every other panel tick names nothing, so one that would
free a line is refused; one can still take a payment off uncaptioned -- a
``$0.00``-figure row ticked with its box cleared, saved when its payment is
unmatched (ledger row **BAL-596**).  Carry-forward's confirmation names what
each envelope's close frees since leaf ``5c-2c-2`` (ruling **R-CC76**,
finding **CC-364** closed), a match only several closes empty together once
for the batch (ruling **R-CC135**), and its Confirm posts every line named
for the batch's one press (``carry_forward_service.carry_forward_unpaid``).
The declarations and the press live in :mod:`app.services.match_press`,
split from here by subject at leaf 5c-2b.

**Why it is a leaf module and not part of** :mod:`app.services.statement_match`.
That package imports ``entry_service``, ``credit_workflow`` and
``transaction_service`` -- three of the doors whose act calls this -- so a rule
living there could not be reached from any of them.  It imports the two match
MODELS and, of ``app.services``, only :mod:`app.services.transfer_legs` -- the
leaf below every service, which names a movement's parent for the event
(plan step ``balance:X-bi-6-4c-4``) -- and :mod:`app.services.match_press`,
which imports no service at run time; that is what lets the act and the
seam's move above it call one rule instead of a spelling each.

Services-boundary discipline (``CLAUDE.md`` Architecture): ORM rows in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
caller owns the unit of work.
"""

from __future__ import annotations

import logging
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from functools import partial
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import transfer_legs
from app.services.match_press import NOTHING_SHOWN, Press, Silent
from app.services.transfer_legs import TransferLeg
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_MATCH_WITHDRAWN,
    log_event,
)

logger = logging.getLogger(__name__)



@dataclass(frozen=True)
class FreedLine:
    """One bank line a withdrawal puts back among the unexplained.

    Attributes:
        line_id: The line's primary key.
        posted_on: The day the bank posted it.
        amount: Its signed figure, as the bank states it -- negative for money
            leaving the account.
        description: What the bank called it.

    Carried as FACTS rather than as a count because the control that triggers
    the withdrawal is a destructive one, and *"1 bank line"* over a `$793.23`
    ACH payment is the *"Nothing moved."* sentence this arc has already shipped
    once (finding **N-336**).  A dialog that names the day and the figure lets
    the owner recognise the line without leaving the screen.
    """

    line_id: int
    posted_on: date
    amount: Decimal
    description: str


@dataclass(frozen=True)
class MatchWithdrawal:
    """What withdrawing a subject's matches would take back, or did.

    **ONE dataclass for the read and the write**, which is the shape
    :func:`~app.services.statement_match.planned_removals` already has one door
    over: the confirm dialog prints what the press will do, the press does
    exactly that, and two derivations would let the two disagree.

    Attributes:
        matches: How many accepted acts are withdrawn.
        lines: The bank lines that become unexplained again.
        kept_rows: How many rows those acts had CREATED that the owner still
            has after the press (a deleted occurrence's hidden tombstone is not
            one, ruling **R-CC84**) -- reported rather than silent, because a row the owner
            did not ask for and was not told about is exactly what a receipt is
            for.  **A creation whose subject is in the going set is NOT counted
            here**, and a first build counted it: both 2026-08-25 reviews
            measured the dialog promising a `-$21.68` residual would stay while
            the press destroyed it, and a minted envelope reported as two rows
            kept while both it and its purchase went.  ``0`` for every act on
            the developer's database, which carry no creation records at all
            (the relation postdates them).
    """

    matches: int
    lines: "tuple[FreedLine, ...]"
    kept_rows: int

    @property
    def frees_a_line(self) -> bool:
        """Return whether this withdrawal changes what the review screen shows.

        The one question a template asks, answered here rather than as a
        ``length`` test in a Jinja condition.
        """
        return bool(self.lines)

    @property
    def line_ids(self) -> "frozenset[int]":
        """Return the ids of the lines this frees -- what a caption naming them posts back.

        The one spelling the page's posted field and the press's comparison
        (:class:`Press`) share, so what a caption sends and what the save is
        graded against cannot be two readings of one withdrawal (plan step
        ``credit_card:CC-5-4a-5``, ruling **R-CC127**).
        """
        return frozenset(line.line_id for line in self.lines)


def _acts_emptied_by(entry_ids: "set[int]") -> "list[StatementMatch]":
    """Return the acts these movements are the LAST app rows of -- the READ.

    The first half of :func:`_partition` over :func:`_acts_naming`, the same
    call :func:`take_out_of_matches` makes, so what a dialog promises and
    what the press withdraws are one derivation.

    Args:
        entry_ids: Entry ids about to leave -- a purchase's, or a row's
            payment record, which goes with its row.

    Returns:
        The acts to withdraw, each with ``members`` and ``creations`` loaded.
        Empty for an ordinary delete, which is nearly every delete.
    """
    emptied, _surviving = _partition(_acts_naming(entry_ids), entry_ids)
    return emptied


def _partition(
    acts: "list[StatementMatch]", entry_ids: "set[int]",
) -> "tuple[list[StatementMatch], list[StatementMatch]]":
    """Split *acts* into those these movements EMPTY and those that survive.

    An act is emptied when every app-side member it holds is in the going set
    (:func:`_loses_every_row`) -- so a group that keeps a row keeps its act,
    and the ``agrees`` flag is what re-reviews it.  ONE spelling of the split
    for the read (:func:`_acts_emptied_by`) and the write
    (:func:`take_out_of_matches`).

    Args:
        acts: The acts naming any of the movements, ``members`` loaded.
        entry_ids: The movements leaving their matches.

    Returns:
        ``(emptied, surviving)``, each in *acts*' order.
    """
    emptied = [act for act in acts if _loses_every_row(act, entry_ids)]
    surviving = [act for act in acts if not _loses_every_row(act, entry_ids)]
    return emptied, surviving


def _acts_naming(entry_ids: "set[int]") -> "list[StatementMatch]":
    """Return every act naming any of these movements, loaded WHOLE.

    Two statements.  The first finds every act naming any of the going
    movements; the second loads those acts with both relations, which is what
    both the emptied acts (withdrawn whole) and the surviving ones (a member
    taken out of the loaded collection) are read off.

    **A member names a MOVEMENT, so the going MOVEMENTS are the whole
    question** (plan step ``credit_card:CC-5-4a-2``, ruling **R-CC45**): a
    row leaving the table takes its purchases and its payment with it
    (:func:`_subject_ids`), and those are what an act names.  The going ROW
    ids still matter to :func:`_summarise`, whose creations may name a row.

    **Scoped by the SUBJECTS, and by nothing else** (plan step
    ``credit_card:CC-5-4a-1``).  A member is held to its movement's account
    by a composite key (``fk_statement_match_members_entry_account``) and to
    its act's by another, so an act naming one of these ids is on that
    movement's account and its owner's by construction;
    a second clause on ONE account -- this took the going rows' account
    through ``CC-5-3`` -- is then either redundant or wrong, and since the
    tender (``CC-5-3``) it is wrong: a bill's payment can sit on ANOTHER
    account than the bill (a checking bill paid from the card), and the act
    that names that payment (ruling **R-CC43**) is on the CARD's account.
    Deleting the bill takes the payment with it, and an act lookup scoped to
    checking would have left that act standing while its member cascaded
    away -- an act naming a line and no movement, and the freed card line
    undisclosed by the dialog.  (Since plan step ``credit_card:CC-5-4a-4``
    the member key refuses rather than cascades, so a wrong scope here would
    fail loud at the flush instead.)

    Args:
        entry_ids: Entry ids about to leave -- a purchase's, or a row's
            payment record, which goes with its row.

    Returns:
        Every act naming one of them, each with ``members`` and ``creations``
        loaded.  Empty for an ordinary delete, which is nearly every delete.
    """
    if not entry_ids:
        return []
    match_ids = {
        row[0]
        for row in db.session.query(StatementMatchMember.match_id)
        .filter(StatementMatchMember.transaction_entry_id.in_(entry_ids))
        .all()
    }
    if not match_ids:
        return []
    return (
        db.session.query(StatementMatch)
        .filter(StatementMatch.id.in_(match_ids))
        .options(
            selectinload(StatementMatch.members),
            selectinload(StatementMatch.creations),
        )
        .all()
    )


def _loses_every_row(act: StatementMatch, entry_ids: "set[int]") -> bool:
    """Return whether *act* would name no app row once these movements go.

    Args:
        act: The act, with ``members`` loaded.
        entry_ids: Movement ids about to leave the table.

    Returns:
        ``True`` when every app-side member is in the going set.  Every act
        reaching here names at least one of them (:func:`_acts_naming`), and
        since plan step ``credit_card:CC-5-4a-4`` no act can name no movement
        at all (ruling **R-CC54**), so the empty case this once answered
        ``True`` for has no subject.
    """
    return all(
        member.transaction_entry_id in entry_ids
        for member in act.members
        if member.bank_statement_line_id is None
    )


def _line_ids_of(acts: "list[StatementMatch]") -> "set[int]":
    """Return the ids of the bank lines *acts* name.

    Args:
        acts: The acts, with ``members`` loaded.

    Returns:
        Every line id a member of one of them names.
    """
    return {
        member.bank_statement_line_id
        for act in acts
        for member in act.members
        if member.bank_statement_line_id is not None
    }


def _freed_lines(acts: "list[StatementMatch]") -> "dict[int, FreedLine]":
    """Return the bank lines *acts* name, as facts, keyed by id -- ONE query.

    Read once for however many withdrawals *acts* make up between them
    (:func:`pending_alone_and_together`), and for the one a press makes.  No query
    at all when no act names a line.

    Args:
        acts: The acts, with ``members`` loaded.

    Returns:
        ``{line id: FreedLine}``.
    """
    line_ids = _line_ids_of(acts)
    if not line_ids:
        return {}
    return {
        line.id: FreedLine(
            line_id=line.id,
            posted_on=line.posted_on,
            amount=line.amount,
            description=line.description,
        )
        for line in db.session.query(BankStatementLine)
        .filter(BankStatementLine.id.in_(line_ids))
        .all()
    }


def _summarise(
    acts: "list[StatementMatch]",
    transaction_ids: "set[int]",
    entry_ids: "set[int]",
    lines: "dict[int, FreedLine]",
) -> MatchWithdrawal:
    """Return what withdrawing *acts* comes to, WITHOUT withdrawing them.

    Args:
        acts: The acts, with ``members`` and ``creations`` loaded.
        transaction_ids: Row ids the press takes from the owner -- deleted,
            or a recurring occurrence it empties and hides (ruling
            **R-CC84**) -- so a creation that names one is not reported as
            staying.
        entry_ids: Purchase ids about to leave the table, likewise.
        lines: :func:`_freed_lines` over *acts* or over a superset of them,
            so many withdrawals read their lines in one query.

    Returns:
        Their :class:`MatchWithdrawal`, its lines ordered by the day the
        bank posted them.
    """
    return MatchWithdrawal(
        matches=len(acts),
        lines=tuple(
            sorted(
                (lines[line_id] for line_id in _line_ids_of(acts)),
                key=lambda line: (line.posted_on, line.line_id),
            )
        ),
        kept_rows=sum(
            1
            for act in acts
            for creation in act.creations
            if creation.transaction_id not in transaction_ids
            and creation.transaction_entry_id not in entry_ids
        ),
    )


def _subject_ids(rows) -> "tuple[set[int], set[int]]":
    """Return the ids of *rows* and of every movement they hold.

    **Its purchases leave the books, and so does its PAYMENT**: a delete
    takes every entry under the row, on either arm (ruling **R-CC75**) --
    through the act that takes a movement off the books
    (:mod:`app.services.movement_removal`, plan step
    ``credit_card:CC-5-4a-3``) before the row itself goes -- and a match
    naming one loses that member -- a purchase's member, or the covering
    movement's that every act names for a settled row (ruling **R-CC43**:
    every act since plan step ``credit_card:CC-5-4a-1``, and every older one
    since migration ``2eabfa596ee0`` re-keyed it, ruling **R-CC45**);
    ``row.entries`` holds both.  The ROW ids are returned too, for
    :func:`_summarise`: an act's creations may name a row.

    Args:
        rows: The transactions whose movements the press takes off, each with
            ``entries`` accessible -- the row AND its live CC-payback chain,
            because those go down in the same commit and a dialog that named
            only the first would understate the press.  A recurring row is
            among them though it stays in the table as an emptied tombstone
            (ruling **R-CC75**): the owner deleted it, so it counts as
            leaving (ruling **R-CC84**).

    Returns:
        ``(transaction_ids, entry_ids)``.
    """
    return (
        {row.id for row in rows},
        {entry.id for row in rows for entry in row.entries},
    )


#: What the event says when rows left the books with their movements -- the
#: row, purchase, payback and transfer deletes (the act's ``because``).
LEFT_THE_BOOKS = (
    "Rows left the books and took the last app row of the matches naming "
    "them; those bank lines are unexplained again."
)

#: What the re-point door's event says happened (ruling **R-CC46**).  Its own
#: sentence because the rows STAY on the books, and production is observed
#: through these lines.
MOVED_ACCOUNTS = (
    "A payment moved to another account and took the last app row of the "
    "matches naming it; those bank lines are unexplained again."
)

#: What the status seam's event says when a settle's own record withdraws its
#: payment -- a ``$0.00`` figure, or the row's purchases becoming its record
#: (plan step ``credit_card:CC-5-4a-3``, finding **CC-358**).  Its own
#: sentence because the ROW stays and only its payment went.
RE_RECORDED = (
    "A payment was re-recorded as nothing, or as its row's purchases, and "
    "took the last app row of the matches naming it; those bank lines are "
    "unexplained again."
)


def _withdraw(
    acts, planned: MatchWithdrawal, owner_id: int, *, press: Press,
    because: str, **fields,
) -> None:
    """Delete the acts, and owe the save the event recording what that freed.

    The members go with each act through the ORM cascade and the composite
    foreign key alike, which is what puts the lines back among the unexplained:
    no member names them any more.  The event is logged at the save's close
    (:class:`Press`, ruling **R-CC135**), so a save its door then refuses
    logs no withdrawal its rollback undid.

    Args:
        acts: The acts to withdraw.
        planned: What :func:`_summarise` said they come to, so the event
            records the same figures the dialog printed.
        owner_id: The user the caller proved owns the account.
        press: The save this call is part of.
        because: The event's sentence -- the DOOR's, since a delete, a
            re-record and a re-point withdraw for different reasons and the
            log is read.
        **fields: Subject coordinates for the event (``transaction_ids``,
            ``transfer_ids``, ``transaction_entry_ids`` and
            ``freed_line_ids``), and ``silent_by``: the ruling or finding a
            no-caption door named (:class:`Silent`), else ``None``.
    """
    for act in acts:
        db.session.delete(act)
    db.session.flush()
    press.owe_event(partial(
        log_event, logger, logging.INFO, EVT_STATEMENT_MATCH_WITHDRAWN,
        BUSINESS, because,
        user_id=owner_id,
        match_count=planned.matches,
        freed_line_count=len(planned.lines),
        kept_row_count=planned.kept_rows,
        **fields,
    ))


def pending_for_rows(rows) -> MatchWithdrawal:
    """Return what deleting *rows* would withdraw, WITHOUT withdrawing it.

    The read half, for the confirm dialog on a delete control: what
    :func:`app.services.movement_removal.remove_movements` withdraws when
    the delete verb hands it every movement of *rows* with *rows* leaving.
    A recurring row's tombstone is among them and counts as leaving (ruling
    **R-CC84**: the owner deleted it, so no creation naming it is reported
    as kept).  Runs on a popover render: one member query always, and the act
    and line queries only where an act actually names one of these subjects.

    Args:
        rows: The transactions a screen is offering to delete -- the row the
            owner pressed AND everything that goes down with it.

    Returns:
        Its :class:`MatchWithdrawal`.  All zeroes when no act would be emptied,
        which is every row on a book nobody has matched.
    """
    transaction_ids, entry_ids = _subject_ids(rows)
    emptied = _acts_emptied_by(entry_ids)
    return _summarise(
        emptied, transaction_ids, entry_ids, _freed_lines(emptied),
    )


def pending_for_movements(entries) -> MatchWithdrawal:
    """Return what taking *entries* out of their matches would withdraw, without doing it.

    **ONE read for every door that takes a single movement out** (plan step
    ``credit_card:CC-5-4a-3``): a purchase the owner deletes, a payment a
    settle's record withdraws (``$0.00``, or the row's purchases), and a
    payment re-pointed onto another account (ruling **R-CC46**) all leave
    the same acts, because an act names the MOVEMENT and none of the three
    keeps it named.  It was two functions until then --
    ``pending_for_purchase`` and ``pending_for_moved_movement``, the second
    returning the first -- which is one read with two names.  The screens
    that offer those acts read it: the bill popover's three captions (the
    "Paid from" pick, the Actual box's ``$0.00``, and Paid on a row whose
    purchases would replace its payment, ruling **R-CC56**), and the
    transfer popover's ``$0.00``, which takes BOTH legs' payments off at once
    -- the reason it takes a set.

    Like the delete dialog it names the ACTS the removal empties and the
    lines those free; a GROUP act that keeps another row is not named here,
    and the act still takes this member out of it and turns its ``agrees``
    flag amber on the register, stated so it reads as a choice and not a fact.
    The purchase list reads the same derivation for many purchases at once
    (:func:`pending_for_each`, plan step ``credit_card:CC-5-4a-5``, ruling
    **R-CC80**).

    Args:
        entries: The movements a screen is offering to remove or re-point.

    Returns:
        Their :class:`MatchWithdrawal`.
    """
    return pending_for_each({None: entries})[None]


@dataclass(frozen=True)
class SharedWithdrawal:
    """ONE accepted act that only SEVERAL removals made together withdraw.

    Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2c-1), ruling **R-CC135**
    (developer 2026-10-04, "One check per save"): *"Warnings name each match
    under every row it names, saying which rows must all close"*.  An act
    naming the payments of two rows is left naming no app row only when both
    rows' removals are made, so neither removal frees its lines alone
    (:attr:`RemovalWithdrawals.alone`); the reconcile panel read each row
    alone until this leaf, named the line under neither, and refused the two
    ticked together (finding **CC-384**).

    Attributes:
        withdrawal: What withdrawing this one act frees (``matches`` is 1).
        keys: Every removal whose movements the act names, withdrawn when
            all are made in one save (or fewer, where keys' movements overlap).
    """

    withdrawal: MatchWithdrawal
    keys: "frozenset[Hashable]"


@dataclass(frozen=True)
class RemovalWithdrawals:
    """What one removal withdraws by itself, and what it withdraws only with others.

    Attributes:
        alone: The acts this removal empties by itself, as
            :func:`take_out_of_matches` would answer it alone -- what
            :func:`pending_for_each` returns for it.
        shared: The acts it empties only together with other removals given
            beside it, one :class:`SharedWithdrawal` per act, in the order the
            acts load.
    """

    alone: MatchWithdrawal
    shared: "tuple[SharedWithdrawal, ...]"


def _classified(groups: "dict[Hashable, set[int]]") -> "tuple[dict, dict]":
    """Class every act naming *groups*' movements ONCE: per key, ``(alone, shared)``.

    The one walk :func:`pending_alone_and_together` and
    :func:`pending_for_each` share; each then reads the lines of only the acts
    it reports.  *groups* is ``{key: movement ids}``.
    """
    alone: "dict[Hashable, list[StatementMatch]]" = {key: [] for key in groups}
    shared: "dict[Hashable, list[tuple[StatementMatch, frozenset[Hashable]]]]" = {
        key: [] for key in groups
    }
    for act in _acts_naming(set().union(*groups.values())):
        keys = frozenset(
            key for key, ids in groups.items()
            if any(member.transaction_entry_id in ids for member in act.members)
        )
        singles = [key for key in keys if _loses_every_row(act, groups[key])]
        for key in singles:
            alone[key].append(act)
        if not singles and len(keys) > 1 and _loses_every_row(
            act, set().union(*(groups[key] for key in keys)),
        ):
            for key in keys:
                shared[key].append((act, keys))
    return alone, shared


def pending_alone_and_together(
    removals: "dict[Hashable, Iterable]",
    rows_leaving: "dict[Hashable, Iterable] | None" = None,
) -> "dict[Hashable, RemovalWithdrawals]":
    """Return what each removal withdraws alone, and with the others given, in ONE read.

    The read a screen offering MANY removals at once asks: one member query,
    one act load and one line query however many it offers, and no query at
    all when none of their movements is named by an act.  Each act naming any
    of the movements is classed ONCE (plan step ``credit_card:CC-5-4a-5``,
    leaf 5c-2c-1, ruling **R-CC135**):

    * emptied by ONE removal's movements (:func:`_loses_every_row`) -- that
      removal's :attr:`~RemovalWithdrawals.alone`, exactly as
      :func:`take_out_of_matches` withdraws it when that removal is made;
    * emptied only by the removals naming it, made together, and by no one
      of them -- a :class:`SharedWithdrawal` under each of those removals;
    * naming a movement none of *removals* takes -- neither: no save of these
      removals empties it, and making some of them only takes their members
      out of it, as :func:`take_out_of_matches` does.

    Args:
        removals: ``{key: movements}`` -- each value one removal's movements
            (a reconcile panel tick's, a purchase X's), under whatever key the
            caller reads its answer back by.
        rows_leaving: ``{key: rows}`` -- the rows a removal deletes with its
            movements (a CC payback the last card purchase's X takes down),
            as :func:`take_out_of_matches` is handed them, so a creation
            naming one is not counted as kept.  A key absent here deletes no
            row; a shared act counts the rows of every removal it needs.

    Returns:
        ``{key: RemovalWithdrawals}``, one per key given.  All zeroes and no
        shared act for a removal no act names, which is nearly every one.
    """
    groups = {
        key: {entry.id for entry in entries}
        for key, entries in removals.items()
    }
    leaving = {
        key: {row.id for row in (rows_leaving or {}).get(key, ())}
        for key in groups
    }
    alone, shared = _classified(groups)
    lines = _freed_lines(
        [act for each in alone.values() for act in each]
        + [act for each in shared.values() for act, _keys in each]
    )
    return {
        key: RemovalWithdrawals(
            alone=_summarise(alone[key], leaving[key], groups[key], lines),
            shared=tuple(
                SharedWithdrawal(
                    withdrawal=_summarise(
                        [act],
                        set().union(*(leaving[each] for each in keys)),
                        set().union(*(groups[each] for each in keys)),
                        lines,
                    ),
                    keys=keys,
                )
                for act, keys in shared[key]
            ),
        )
        for key in groups
    }


def pending_for_each(
    removals: "dict[Hashable, Iterable]",
    rows_leaving: "dict[Hashable, Iterable] | None" = None,
) -> "dict[Hashable, MatchWithdrawal]":
    """Return what each of several removals would withdraw ALONE, in ONE read.

    :func:`pending_for_movements` for a screen offering MANY removals, each
    its own press -- the purchase list's X on each of an envelope's
    purchases, drawn for every envelope on the grid (plan step
    ``credit_card:CC-5-4a-5``, ruling **R-CC80**).  Each removal is answered
    exactly as :func:`take_out_of_matches` would answer it alone: the
    :attr:`~RemovalWithdrawals.alone` half of
    :func:`pending_alone_and_together` over the same walk
    (:func:`_classified`), reading the lines of those acts only.

    Args:
        removals: ``{key: movements}`` -- each value one press's movements,
            under whatever key the caller reads its answer back by.
        rows_leaving: ``{key: rows}`` -- the rows a removal's press deletes
            with its movements, as :func:`pending_alone_and_together` takes
            them.

    Returns:
        ``{key: MatchWithdrawal}``, one per key given.  All zeroes for a
        removal no act names, which is nearly every one.
    """
    leaving = rows_leaving or {}
    groups = {key: {entry.id for entry in each} for key, each in removals.items()}
    alone, _shared = _classified(groups)
    lines = _freed_lines([act for each in alone.values() for act in each])
    return {
        key: _summarise(
            alone[key], {row.id for row in leaving.get(key, ())},
            groups[key], lines,
        )
        for key in groups
    }


def _parent_ids(entries, leaving_ids: "set[int]") -> "dict[str, list[int]]":
    """Return the withdrawal event's parent coordinates for *entries*.

    What each movement was recorded FOR, asked of the one resolution of a
    movement's parent (:func:`app.services.transfer_legs.movement_parent`,
    plan step ``balance:X-bi-6-4c-4``, ruling **R-BAL160**) and split in one
    pass: a plan row under ``transaction_ids``, with the rows the press
    deletes beside it, and a transfer's payment under ``transfer_ids``.  It
    read the shadow row's id off a transfer's payment until that step -- an
    id ``X-bi-6-4d`` leaves unset, and one ``sorted`` could not have ordered
    beside the others.  The rows a transfer delete handed over as leaving
    were its shadows until plan step ``balance:X-bi-6-4d-2`` deleted them;
    it hands over none.

    Args:
        entries: The movements leaving their matches.
        leaving_ids: The ids of the rows the press deletes, soft or hard.

    Returns:
        ``{"transaction_ids": [...], "transfer_ids": [...]}``, each sorted.
    """
    row_ids, transfer_ids = set(leaving_ids), set()
    for entry in entries:
        parent = transfer_legs.movement_parent(entry)
        if isinstance(parent, TransferLeg):
            transfer_ids.add(parent.transfer.id)
        else:
            row_ids.add(parent.id)
    return {
        "transaction_ids": sorted(row_ids),
        "transfer_ids": sorted(transfer_ids),
    }


def take_out_of_matches(
    entries, owner_id: int, *, because: str, press: "Press | None",
    rows_leaving=(),
) -> MatchWithdrawal:
    """Take *entries* out of every act naming them; withdraw the acts that empties.

    The MATCH STEP of the one act that takes a movement off the books
    (:func:`app.services.movement_removal.remove_movements`, plan step
    ``credit_card:CC-5-4a-3``, ruling **R-CC54**), and the whole of the
    seam's re-point (:func:`withdraw_for_moved_movement`).  Two things,
    over ONE loaded set (:func:`_acts_naming`):

    * **an act left naming no app row is WITHDRAWN** -- deleted with its
      members and its creation records, its freed lines logged -- on the
      narrowest condition the module docstring states, read by the same
      predicate the dialogs read (:func:`_loses_every_row`);
    * **an act that KEEPS another app row loses this member**, taken out of
      its loaded ``members`` collection so the session and the table agree
      (``delete-orphan`` issues the DELETE).  The re-point spelled this as a
      bulk ``DELETE`` with ``synchronize_session=False`` until this step,
      which left the surviving act's already-loaded collection holding a
      member the table no longer had.

    FLUSHED before returning WHEN IT CHANGED SOMETHING (the withdrawal's own
    flush, or a member taken out), for the re-point's reason (the unit of
    work orders a DELETE after an UPDATE, so a member left to the same flush
    as a movement's account assignment would be checked against it and refuse
    it) and for the act's: the members are gone before the movement is.  An
    unmatched movement -- nearly every one -- flushes nothing here, so the
    status seam run inside a caller's ``no_autoflush`` block (the
    carry-forward batch) writes no earlier than it did before this step.

    **It asks what the owner was SHOWN, as one call of a save** (plan step
    ``credit_card:CC-5-4a-5``, rulings **R-CC81**, **R-CC127**, **R-CC135**):
    *press* is REQUIRED, a line this call frees that the page did not name is
    refused before anything is written, and what it frees is recorded for the
    save's close (:class:`Press`).  ``None`` is a settle verb's default --
    its door said nothing -- and is a press of this one call naming nothing
    (:data:`NOTHING_SHOWN`), opened and closed here.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        entries: The movements leaving their matches -- the whole set one
            press removes, so a group act naming two of them is withdrawn
            once and a creation naming one is not reported as kept.
        owner_id: The owner under whose books the acts are filed.
        because: The event's sentence (:data:`LEFT_THE_BOOKS`,
            :data:`RE_RECORDED`, :data:`MOVED_ACCOUNTS`).
        press: The door's open :class:`Press`, or ``None`` for a door that
            named nothing.
        rows_leaving: The rows going in the same press, soft or hard (a
            recurring occurrence's tombstone counts as gone, ruling
            **R-CC84**), when the caller is a row delete -- so a creation that
            names one is not reported as kept (:func:`_summarise`).  Empty
            when only movements go.

    Returns:
        What was withdrawn, as the dialog's read would have printed it.

    Raises:
        PageOutOfDate: When this frees a line the press's page did not name.
        ValidationError: An :class:`OwnerOnly` press's own refusal, when it
            would free a line (ruling **R-CC130**).
    """
    if press is None:
        with Press(NOTHING_SHOWN) as one_call:
            return _take_out(entries, owner_id, because, one_call, rows_leaving)
    return _take_out(entries, owner_id, because, press, rows_leaving)


def _take_out(
    entries, owner_id: int, because: str, press: Press, rows_leaving,
) -> MatchWithdrawal:
    """Take *entries* out of their matches as one call of *press*'s save.

    The body of :func:`take_out_of_matches`, which states the contract.

    Returns:
        What was withdrawn.
    """
    entry_ids = {entry.id for entry in entries}
    leaving_ids = {row.id for row in rows_leaving}
    emptied, surviving = _partition(_acts_naming(entry_ids), entry_ids)
    planned = _summarise(
        emptied, leaving_ids, entry_ids, _freed_lines(emptied),
    )
    press.take(planned)
    if emptied:
        _withdraw(
            emptied, planned, owner_id, press=press, because=because,
            # The PARENTS the movements were under, whether or not they leave
            # too -- a re-record's row stays, and the event is the only record
            # of which row a no-caption door (the grid's Mark Paid) touched.
            **_parent_ids(entries, leaving_ids),
            transaction_entry_ids=sorted(entry_ids),
            freed_line_ids=[line.line_id for line in planned.lines],
            # What let a no-caption door withdraw (ruling **R-CC81**).
            silent_by=(
                press.shown.because if isinstance(press.shown, Silent)
                else None
            ),
        )
    taken = [
        (act, member) for act in surviving for member in act.members
        if member.transaction_entry_id in entry_ids
    ]
    for act, member in taken:
        act.members.remove(member)
    if taken:
        db.session.flush()
    return planned


def withdraw_for_moved_movement(
    entry, owner_id: int, *, press: "Press | None",
) -> MatchWithdrawal:
    """Take *entry* out of every act naming it; withdraw the acts that empties.

    The door a movement's ACCOUNT change calls BEFORE the account is
    assigned (``status_seam._covering._re_point``; module docstring, ruling
    **R-CC46**).  A moved movement stays on the books, so it does not go
    through the act that takes one off them -- only through that act's match
    step (:func:`take_out_of_matches`): the key would refuse the move while
    the member stands, so the member is taken out and FLUSHED first.  A
    group act that keeps another row keeps standing, its ``agrees`` flag the
    re-review, exactly as after a purchase's delete.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        entry: The covering movement about to be re-pointed.
        owner_id: The row's owner, under whose books the act is filed.
        press: The door's open :class:`Press`, or ``None`` for a door that
            named nothing (:func:`take_out_of_matches`).

    Returns:
        What was withdrawn.

    Raises:
        PageOutOfDate: When this frees a line the press's page did not name.
        ValidationError: An :class:`OwnerOnly` press's own refusal, when it
            would free a line (ruling **R-CC130**).
    """
    return take_out_of_matches(
        [entry], owner_id, because=MOVED_ACCOUNTS, press=press,
    )
