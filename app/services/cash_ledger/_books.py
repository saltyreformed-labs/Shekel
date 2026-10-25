"""The boundary between an account's OPENING and its RECORDS, both directions.

**An account's opening equity is the balance at the CLOSE of its ``opened_on``**
(ruling **balance:R-HG**), exactly the rule
:attr:`~._events.CashAnchorFact.observed_on` states for a balance assertion
(ruling R-DH (a)).  One boundary follows from that, and it can be crossed from
either side:

* a MOVEMENT may not be dated on or before the day its account's books open --
  :func:`reject_movement_before_books_open`, the sentence a settle-day box gets;
* a BANK LINE the books cannot hold may not be turned into one, or be named by
  a match that settles one -- :func:`reject_line_before_books_open`, the
  sentence the three import doors get (plan step **X-f3c-2b-2b**, finding
  **N-383**).  It is the movement rule reached from the EVIDENCE rather than
  from the row, and it is not implied by the movement rule: a match settles
  every member on the LATEST of its bank days, so a group holding one
  pre-opening line and one later line clears the movement check entirely.
  Measured on a production clone 2026-08-31 -- lines of 2026-03-26 and
  2026-08-17 matched to one `$80.00` envelope, accepted, and the `$15.96`
  already inside Checking's `$689.16` opening booked a second time;
* an OPENING may not be restated onto or past a day the account already records
  money moving -- :func:`reject_books_open_on_or_after_movements`, the sentence
  the restatement door gets (plan step **X-f3c-2b-2a**);
* an OPENING may not be restated onto or past a day the account has MATCHED a
  bank line on -- :func:`reject_books_open_on_or_after_matched_lines`, the
  same defect one door over.  A matched line can post well before the row
  explaining it settles, so the movement bound above calls the whole window
  between them legal;
* an OPENING may not be restated past a day the owner has ASSERTED a balance
  for -- :func:`reject_books_open_after_an_assertion`.  The one none of the
  others saw: an account with no settled movement is
  unbounded by the rule above, which is every investment, retirement and
  property account in production.  Reproduced on the developer's own Roth IRA,
  it fabricated ``$22,809.02`` of investment return and discarded the opening
  the owner had just stated.

**They are five questions about one COMPARISON, and the comparison is stated
once** -- :func:`~app.utils.books_boundary.books_hold`, which every refusal
here asks and nothing here re-spells.  It is defined in that pure leaf since
plan step ``pay_calendar:C18-a`` (ruling **R-PC85**), because the recurrence
walk asks it too and may not import this package; it is imported here and
re-exported through ``cash_ledger`` under its old name.  Each reads a
different table to answer it, so the QUERIES cannot be collapsed; the test
that decides them can, and leaving it open-coded five times
is how the ``<`` / ``<=`` distinction ruling **R-HG** turns on comes to differ
between two of them.  That is why they are read together: "two statements of
one rule that differ silently is the failure this arc names as its own root
cause" (:mod:`app.opening_infrastructure`).  The movement half lived in
:mod:`._events` beside the opening RECORD it reads while it was the only half;
with a second half it belongs beside its twin instead, and :mod:`._events` goes
back to being what its own docstring says it is -- the loaders that build the
event stream.

**The DATABASE is the structural half, and none of these is.**
:mod:`app.opening_infrastructure` installs deferrable constraint triggers over
``budget.transactions``, ``budget.transaction_entries``,
``budget.statement_match_members``, ``budget.bank_statement_lines`` AND
``budget.account_openings``, so the state is unstorable by any single
transaction from any client -- a bulk ``UPDATE``, a raw statement, a psql
session, a writer nobody enumerated.  These functions exist so an ordinary
date box gets a sentence instead of a database driver's exception at COMMIT: the
same pairing ``ck_transaction_entries_positive_amount`` has with
``entry_service``'s refusal of a purchase worth nothing (ruling
``bank_import:R-II``).

**Which is why the two ROW SETS below are imported rather than re-spelled.**
Each opening-side predicate has to refuse exactly what its trigger refuses, or a
submission passes here and aborts at COMMIT with a message no surface can
render -- and the trigger's row set is deliberately WIDER than the balance
fold's: it counts SOFT-DELETED rows and the Credit / Cancelled statuses, so a
row the owner cannot see still bounds how far back their books may be restated
(see :data:`app.opening_infrastructure.SETTLED_MOVEMENTS_SQL` for why narrowing
it opens a hole on RESTORE).  Interpolating that one SQL statement is what
makes "the service refuses what the database refuses" true by construction
rather than by two authors agreeing.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, plain
values out; no Flask symbol, no writes, no clock read.  The five REFUSALS
raise :class:`~app.exceptions.ValidationError` and do nothing else; the three
day readers and the one predicate beside them return a value and raise nothing.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import func, text

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.utils.balance_predicates import owner_declared_clause
# The ONE comparison every refusal below asks, and re-exported through
# ``cash_ledger`` under its old name; it lives in a pure leaf since plan step
# ``pay_calendar:C18-a`` because the recurrence walk asks it too.
from app.utils.books_boundary import books_hold
from app.opening_infrastructure import (
    MATCHED_LINE_DAYS_SQL, SETTLED_MOVEMENTS_SQL,
)

from ._events import account_opening_fact

if TYPE_CHECKING:  # pragma: no cover -- annotation only
    from ._events import CashOpeningFact


def reject_movement_before_books_open(account_id: int, day: date) -> None:
    """Refuse a cash movement dated on or before *account_id*'s opening day.

    The MOVEMENT side of the boundary (plan step X-f3c-2b, finding **N-378**).
    An account's opening equity is what it held at the CLOSE of
    :attr:`~._events.CashOpeningFact.opened_on`, so a movement dated on or
    before that day is ALREADY INSIDE the figure, and recording it counts the
    money twice.

    **What the double count costs, and why the balance healing is not a
    defence.**  The fold seeds at the opening equity and
    :func:`~._walk.dated_deltas` emits every source at its own day, so between
    the movement's day and the next assertion the running total carries it a
    second time.  The next assertion RESETS to what the owner declared, so the
    rendered balance heals -- but the correction that heals it is booked to the
    general ledger, and on a MODELLED account (ruling **R-FO**) its counter leg
    is ``unrealized_change``, not ``anchor_equity``.  A transfer therefore
    becomes market performance that never unwinds.  Measured on a fixture: a
    Roth declared ``$1,000.00`` with a ``$1,000.00`` pre-opening transfer
    reports ``$850.00`` of unrealized change against a real ``$150.00``.

    It is asked by the TWO writers of a settle day -- the ORM one
    (:func:`app.services.settle_day.record_settle_day`, which every door for
    both ``budget.transactions`` and ``budget.transaction_entries`` goes
    through) and the bulk one (``reconcile_service.record_settled_days``, a
    ``query.update()`` with no ORM instance to hand that function).  Two
    callers, one predicate.

    **The CALLER owns the ownership scoping, and the message is why that
    matters.**  The refusal names the account's opening equity so a date box
    can render it verbatim, and this function applies no ``user_id`` filter of
    its own.  Every caller today reaches it behind an ownership check -- the
    routes resolve the row by owner before any settle door is entered -- so
    the figure only ever reaches the owner.  A future caller that took an
    account id straight from a request would turn this message into a balance
    oracle.

    Args:
        account_id: The account the movement belongs to.  Assumed already
            scoped to the acting user by the caller.
        day: The civil day the movement's cash moved.

    Raises:
        ValidationError: When *day* is on or before the account's opening day.
            A 400 rather than a programming error: the day arrives from a date
            box, and the message names both the offending value and the bound
            it broke so a surface can render it verbatim.
        RuntimeError: When the account carries no opening record, propagated
            from :func:`~._events.account_opening_fact` -- a broken invariant,
            and deliberately not softened here into "then anything is allowed".
    """
    # **Under ``no_autoflush``, and that is a defect plan step X-f3c-2b's own
    # suite caught rather than a precaution.**  This is the only READ on a
    # write path, and SQLAlchemy autoflushes pending mutations before a query:
    # the caller has already assigned part of the row it is midway through
    # writing -- ``apply_status_change`` sets ``status_id`` before it reaches
    # :func:`app.services.settle_day.record_settle_day` -- so the flush lands a
    # half-written row against constraints that describe the finished one.
    # Measured (while the row still carried its figure columns, through plan
    # step ``balance:X-bi-4b-1``): a row already carrying a settle day and no
    # settlement record -- the legacy shape the since-deleted
    # ``ck_transactions_settle_day_needs_a_record`` existed to let an owner
    # repair -- failed with a raw ``CheckViolation`` raised "as a result of
    # Query-invoked autoflush".  The half-written row is the point, whatever
    # constraint it meets.  Suppressing the flush
    # cannot hide a pending opening from this read, and the reason is stated
    # precisely because the whole paragraph is about autoflush: the ONE writer
    # (``opening_service.stage_account_opening``) only ever stages, and BOTH
    # of its entrances emit the row before returning, each through the
    # autoflush of the first query after it (measured 2026-09-29, statement by
    # statement): a restatement's opening INSERT is statement 9 of
    # ``apply_opening_restatement``, sent by the posting reconcile's first
    # query at 10, and an origination's is statement 4 of ``create_account``,
    # sent by ``stage_anchor_true_up``'s first read,
    # ``cash_ledger.governing_anchor``, at 5.  *That read sends it since plan
    # step ``balance:X-bn`` deleted ``stage_anchor_true_up``'s advisory-lock
    # statement, which sent it before (measured 2026-08-31 at statement 6).*
    # Neither leaves an unflushed opening for a settle in the same transaction
    # to race, and the migration writes through ``op.execute`` outside the ORM
    # entirely.
    with db.session.no_autoflush:
        opening = account_opening_fact(account_id)
    if books_hold(opening.opened_on, day):
        return
    raise ValidationError(
        f"Money cannot have moved on {day.isoformat()}: this account's books "
        f"open on {opening.opened_on.isoformat()} holding "
        f"${opening.opening_equity:,.2f}, and that figure is the closing balance "
        "for its own day -- so anything that moved by then is already inside "
        "it.  Restate the account's opening to an earlier day if the records "
        "really do start before it."
    )


def reject_line_before_books_open(
    opening: "CashOpeningFact", posted_on: date, subject: str,
) -> None:
    """Refuse an act over a bank line the account's books cannot hold.

    The EVIDENCE side of the boundary (plan step **X-f3c-2b-2b**, finding
    **N-383**).  A bank line is the bank's record of money moving on the day
    it POSTED, so a line posted on or before ``opened_on`` is already inside
    the opening equity -- and an act that turns it into a purchase, into an
    income row, or into the evidence for settling one, records that money a
    second time.

    **It is NOT implied by** :func:`reject_movement_before_books_open`, and
    that gap is the reason this exists rather than a second guard on the same
    fact.  A match settles every member row on the LATEST of its bank days
    (:class:`~app.services.statement_match.MatchDays`), so a group holding one
    pre-opening line and one later line settles AFTER the books open and
    passes the movement check untouched.  Measured on a restored production
    clone 2026-08-31: Checking's books open 2026-03-26 holding ``$689.16``,
    and lines of 2026-03-26 (``-$15.96``) and 2026-08-17 (``-$64.04``) matched
    to one ``$80.00`` envelope were ACCEPTED, settling it on 2026-08-17 --
    ``$15.96`` inside the opening equity and inside a settled row at once.
    The single-line case refuses today only because ``max`` over one line is
    that line's own day, which is an accident of the derivation rather than a
    rule anything states.

    **The OPENING is passed in rather than loaded**, which is the opposite of
    its twin above and is the review pass's own rule: a pass over one account
    holds what it cannot change (:class:`~app.services.statement_match
    .ReviewScope`), and re-loading the governing opening here would be the
    redundant producer call that package treats as a DRY violation rather than
    a cost.  **What the pass's field buys is SHARING and not a saved read** --
    the same correction ``ReviewScope.opening`` records against its own first
    draft, which claimed 378 reads that no design would have made.  What it
    makes true is that the SCREEN and the DOOR are provably one answer: both
    read the opening the pass resolved.

    Args:
        opening: The account's governing
            :class:`~._events.CashOpeningFact`.  The caller proved ownership
            when it built the pass this came from; nothing is re-scoped here.
        posted_on: The day the bank posted the line.  **The posting day and
            never the transaction day**: a swipe MADE before the books opened
            and TAKEN after is money that left the account after the opening,
            so it is recordable, and its purchase's ``purchased_on`` is a
            budget clock rather than a movement.
        subject: What is being recorded, named as the owner would name it --
            "this purchase", "this deposit", "this match".  Taken rather than
            composed here, for the reason
            :meth:`~app.services.statement_match.ReviewScope.period_holding`
            takes one: a refusal an owner reads has to name the act they
            performed.

    Raises:
        ValidationError: When the line posted on or before the books opened.
            A 400 rather than a programming error: it is reachable by an
            ordinary owner working from a page rendered before the books were
            restated forward.
    """
    if books_hold(opening.opened_on, posted_on):
        return
    raise ValidationError(
        f"Your bank posted this line on {posted_on.isoformat()}, and this "
        f"account's books open on {opening.opened_on.isoformat()} holding "
        f"${opening.opening_equity:,.2f} -- an opening equity is the closing "
        "balance for its own day, so that money is already inside it.  "
        f"Recording {subject} would count it twice.  Restate the account's "
        "opening to an earlier day if your records really do start before "
        "it.  Nothing was changed."
    )


def earliest_recorded_movement_day(account_id: int) -> date | None:
    """Return the first day *account_id* records cash moving, or ``None``.

    ``MIN(settled_on)`` over BOTH movement tables, and it is the SAME row set
    the database constraint counts -- one SQL statement
    (:data:`app.opening_infrastructure.SETTLED_MOVEMENTS_SQL`), interpolated
    here and into ``budget.assert_account_books_hold_its_movements`` rather
    than written twice.  That is what makes
    :func:`reject_books_open_on_or_after_movements` refuse exactly what the
    trigger refuses, instead of two authors agreeing to keep two queries in
    step.

    **The row set is deliberately WIDER than the balance fold's**, so this
    answers a day the fold ignores.  ``balance_contributing_clause`` excludes
    ``is_deleted`` rows and the Credit / Cancelled statuses; this counts them,
    because un-deleting a row is an ``UPDATE`` of ``is_deleted`` alone and the
    movement trigger fires ``UPDATE OF settled_on, account_id`` -- so a
    restored pre-books row would pass every tier untouched.  The cost is
    over-refusal, which is the safe direction: it refuses a legal act loudly
    rather than admitting an illegal one silently.

    **Raw SQL rather than a two-model union, deliberately.**  The point of the
    function is to ask the constraint's own question, and the constraint's
    question exists as a SQL string; rebuilding it out of
    :class:`~app.models.transaction.Transaction` and
    :class:`~app.models.transaction_entry.TransactionEntry` would be a second
    spelling that no gate could grade against the first -- ``duplicate-code``
    does not see SQL inside a string literal.

    Args:
        account_id: The account whose movements to bound.

    Returns:
        The earliest ``settled_on`` recorded against the account on either
        movement table, or ``None`` when it records none.
    """
    return db.session.execute(
        text(
            f"SELECT MIN(settled_on) FROM ({SETTLED_MOVEMENTS_SQL}) "
            "AS movements WHERE account_id = :account_id"
        ),
        {"account_id": account_id},
    ).scalar()


def reject_books_open_on_or_after_movements(
    account_id: int, day: date,
) -> None:
    """Refuse opening *account_id*'s books on or after a recorded movement.

    The OPENING side of the boundary (plan step **X-f3c-2b-2a**), and the exact
    mirror of :func:`reject_movement_before_books_open`: the movement door is
    handed a day and asks where the books open, this door is handed a day and
    asks when the records start.  Both refuse the same state; which one an
    owner meets is decided by which act they are performing.

    **It is asked under ``no_autoflush`` for the reason its twin is**, and the
    reason is sharper here: the restatement door calls this BEFORE it stages
    the new ``budget.account_openings`` row, so an autoflush at this query
    could only ever emit some other pending mutation of the caller's -- never
    the opening being judged, which does not exist yet.  Letting a half-written
    unrelated row flush against the constraints that describe the finished one
    is the defect measured one door over, and suppressing it costs nothing:
    this function reads only the two movement tables, and no writer of those
    reaches a restatement.

    Args:
        account_id: The account whose books are being opened or restated.
            Assumed already scoped to the acting user by the caller, exactly as
            :func:`reject_movement_before_books_open` assumes it -- the message
            names the account's own earliest recorded day, which is a fact
            about the owner's records.
        day: The candidate ``opened_on``.

    Raises:
        ValidationError: When the account already records money moving on or
            before *day*.  A 400 rather than a programming error: the day
            arrives from a date box, and the message names both the offending
            value and the bound it broke so a surface can render it verbatim.
    """
    with db.session.no_autoflush:
        earliest = earliest_recorded_movement_day(account_id)
    # **The SAME predicate, asked from the other side.**  The movement door is
    # handed a day and asks where the books open; this door is handed the
    # books' day and asks whether they would hold the earliest movement the
    # account records.  Spelling it as ``day < earliest`` was the same test
    # written a second way, which is the drift :func:`books_hold` exists to
    # remove.
    if earliest is None or books_hold(day, earliest):
        return
    raise ValidationError(
        f"These books cannot open on {day.isoformat()}: this account already "
        f"records money moving on {earliest.isoformat()}, and an opening "
        "equity is the closing balance for its own day -- so that movement "
        "would be counted twice.  Open the books on a day before it, or "
        "re-date the movement first."
    )


def earliest_matched_line_day(account_id: int) -> "date | None":
    """Return the first day *account_id* has MATCHED a bank line on, or ``None``.

    ``MIN(posted_on)`` over the bank lines this account's matches name, and it
    is the SAME row set the database constraint counts -- one SQL statement
    (:data:`app.opening_infrastructure.MATCHED_LINE_DAYS_SQL`), interpolated
    here and into ``budget.assert_account_books_hold_its_matched_lines``
    rather than written twice, exactly as
    :func:`earliest_recorded_movement_day` is.

    **It is a SECOND bound and not a tightening of the movement one**, and the
    gap between them is a money defect rather than a margin.  A match settles
    every member on the LATEST of its bank days, so the earliest line of a
    multi-day group posts strictly BEFORE the row explaining it settles --
    which means ``MIN(settled_on)`` can be days or months after
    ``MIN(posted_on)``, and every day in between is one the movement bound
    calls legal.  Restating the books into that window puts the earlier line's
    money inside the opening equity and inside a settled row at once.

    **Raw SQL rather than a two-model join, deliberately**, for the reason its
    twin gives: the point is to ask the constraint's own question, and the
    constraint's question exists as a SQL string.  ``duplicate-code`` cannot
    grade a second spelling written in ORM.

    Args:
        account_id: The account whose matched lines to bound.

    Returns:
        The earliest ``posted_on`` over the bank lines this account's matches
        name, or ``None`` when it has matched none -- which is every account in
        production today, and is why this bound is latent rather than live.
    """
    return db.session.execute(
        text(
            f"SELECT MIN(posted_on) FROM ({MATCHED_LINE_DAYS_SQL}) "
            "AS matched WHERE account_id = :account_id"
        ),
        {"account_id": account_id},
    ).scalar()


def reject_books_open_on_or_after_matched_lines(
    account_id: int, day: date,
) -> None:
    """Refuse opening *account_id*'s books on or after a MATCHED bank line.

    The fourth refusal of the boundary (plan step **balance:X-f3c-2b-2b**), and
    the one the other three could not make.
    :func:`reject_books_open_on_or_after_movements` bounds a restatement by the
    account's earliest settled MOVEMENT; a matched bank line can post well
    before the row that explains it settles, because a match settles every
    member on the LATEST of its bank days.  The window between the two is
    accepted by every other bound, and restating into it counts the earlier
    line's money twice -- once inside the new opening equity and once in the
    settled row that explains it.

    **It is reachable rather than instantiated, and saying which matters.**
    Measured 2026-08-31 on a clone of the developer's dev database: account 1's
    earliest matched line is 2026-03-26 and its earliest movement is the same
    day, so the window is EMPTY on all nine accounts and production carries no
    match at all.  What makes it reachable is arithmetic rather than data --
    a group of lines posted 04-10 and 04-20 on an account with no other
    activity settles its member on 04-20 and leaves 04-10..04-19 open -- and
    the planning conventions' rule 8 was explicit that a finding costing ``$0.00`` on
    today's data is a defect waiting for the data to change.

    **The message names UNMATCHING and not re-dating**, which is why this is a
    separate function from its movement twin rather than a widened row set: a
    bank line's day is the BANK's and no door in this app may move it, so
    telling the owner to re-date it would be the *chooser that cannot succeed*
    shape one sentence over.

    Args:
        account_id: The account whose books are being restated.  Assumed
            already scoped to the acting user by the caller, exactly as its
            three siblings assume it.
        day: The candidate ``opened_on``.

    Raises:
        ValidationError: When the account has matched a bank line posted on or
            before *day*.  A 400: the day arrives from a date box, and the
            message names both the offending value and the bound it broke.
    """
    with db.session.no_autoflush:
        earliest = earliest_matched_line_day(account_id)
    if earliest is None or books_hold(day, earliest):
        return
    raise ValidationError(
        f"These books cannot open on {day.isoformat()}: you have matched a "
        f"bank line your bank posted on {earliest.isoformat()}, and an "
        "opening equity is the closing balance for its own day -- so that "
        "money would be counted twice, once in the opening and once in the "
        "row that explains the line.  Open the books on a day before it, or "
        "undo that match first."
    )


def earliest_assertion_day(account_id: int) -> "date | None":
    """Return the first day *account_id* was asserted to hold a balance.

    ``MIN(observed_on)`` over ``budget.account_anchor_history``, the bound
    :func:`reject_books_open_after_an_assertion` compares against.  Public for
    the same reason :func:`earliest_recorded_movement_day` is: the restatement
    form renders it as its date input's ceiling, so the browser refuses what
    the service would refuse rather than round-tripping a rejection.

    **Every assertion, not the governing one.**  The rule is about the EARLIEST
    balance the owner has stated, because that is the first one the fold resets
    at -- a later assertion cannot be the one an opening jumps over first.

    Args:
        account_id: The account whose assertions to bound against.

    Returns:
        The earliest ``observed_on`` on the account, or ``None`` when it
        carries no assertion at all -- a broken invariant elsewhere
        (``account_service.create_account`` writes one), and an honest empty
        answer here.
    """
    return db.session.query(
        func.min(AccountAnchorHistory.observed_on)
    ).filter(
        AccountAnchorHistory.account_id == account_id,
        # The first level the fold RESETS at is the owner's earliest; a bank
        # placement resets nothing until the flip, so it bounds nothing here
        # (plan step ``balance:X-bj-1``; the same interim narrowing every
        # cash reader composes, deleted with the reset).
        owner_declared_clause(),
    ).scalar()


def reject_books_open_after_an_assertion(account_id: int, day: date) -> None:
    """Refuse opening *account_id*'s books after a balance it has asserted.

    **The THIRD bound, and it closes a money defect the first two did not
    see** (code review, 2026-08-31).  The movement rule bounds an opening
    against recorded CASH; nothing bounded it against what the owner has
    SAID the account held.  An account with no settled movement -- which is
    every investment, retirement and property account in production -- could
    therefore have its books restated to any day up to today, past every
    assertion it carries.

    **What that costs, reproduced on the developer's own Roth IRA.**  Restating
    it to 2026-08-01 at ``$100.00``, over six assertions running from
    2026-03-31 to 2026-07-16, was ACCEPTED: ``unrealized_change`` moved from
    ``-$4,523.33`` to ``-$27,332.35``, so the app reported ``$22,809.02`` of
    investment return that never happened.  The stated opening was discarded in
    the same act -- the earliest assertion RESETS the fold, so the balance went
    on reading ``$27,432.35`` and the ``$100.00`` the owner typed was never
    read by anything.  Both halves are finding **N-378**'s shape, reached from
    the opening side rather than the movement side.

    **The comparison is ``>``, not ``>=``, and equality is the ORDINARY case.**
    ``account_service.create_account`` writes the origination opening and the
    origination assertion for the same day, so an opening EQUAL to the earliest
    assertion is what every account starts life with -- FOUR of the
    developer's nine sit exactly there (accounts 4, 5, 6 and 11), the other
    five having been moved below their first assertion by ``d3b6f1c8a274``'s
    legalising restatement.  Refusing equality would make the factory's own
    output unrestatable.

    **That figure said THREE until 2026-09-01, and the correction is not the
    point -- how it was wrong is** (finding **N-431**).  Three was never true
    of an unmutated database.  It counted a state THIS DOCSTRING'S OWN
    EXPERIMENT had created: "What that costs, reproduced on the developer's own
    Roth IRA" above restates account 4 to 2026-08-01 at ``$100.00``, and that
    restatement is exactly what took account 4 out of the count.  The mutating
    row was written at 10:27:58Z and this docstring committed at 10:42:53Z,
    fifteen minutes later, on the same clone.  **Dating it would not have
    caught it**, which is why the remedy is to state the BASIS: measured
    2026-09-01 on a clone of production restored fresh from a dump, at alembic
    head ``e2d7a94f61c3``, reading each account's GOVERNING opening
    (``_events.py``'s ``ORDER BY id DESC``) against ``MIN(observed_on)``.
    Re-derive it that way or not at all; a figure measured on a clone this
    module has been experimented against is measuring the experiment.

    Args:
        account_id: The account whose books are being restated.  Assumed
            already scoped to the acting user by the caller.
        day: The candidate ``opened_on``.

    Raises:
        ValidationError: When the account has asserted a balance for a day
            before *day*.  A 400: the day arrives from a date box, and the
            message names the offending value and the bound it broke.
    """
    with db.session.no_autoflush:
        first = earliest_assertion_day(account_id)
    # **The same predicate, and here it bounds the books from ABOVE.**  An
    # assertion is the level the fold RESETS to, so the books must open on or
    # before it: this refuses exactly where books opening on *first* would
    # hold a movement on *day*, which is the ``>`` the paragraph above calls
    # the ordinary case at equality.
    if first is None or not books_hold(first, day):
        return
    raise ValidationError(
        f"These books cannot open on {day.isoformat()}: you have already "
        f"recorded a balance for this account on {first.isoformat()}, and a "
        "balance you recorded is what the app folds forward from -- so an "
        "opening after it would be ignored, and the gap would be reported as "
        "a gain. Open the books on or before that day."
    )
