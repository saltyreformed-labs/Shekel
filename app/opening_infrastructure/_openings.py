"""The OPENINGS side: both predicates, and the dispatcher that asks them.

A restatement is graded from this direction -- given the books' new day, do
they still hold everything the account records?  Both arms' predicates live
here TOGETHER, which is deliberate: they are one rule over two row sets, and
the module docstring's argument that a reader must see them side by side is
the same one :mod:`app.services.cash_ledger._books` makes for its refusals.

The trigger on ``budget.account_openings`` is DISPATCH -- it names the accounts
an event could have changed the books of -- and the predicates it asks are the
ones the INSTALLING revision declared, which is why its body is generated
rather than fixed (:func:`_create_opening_func_sql`).
"""

from __future__ import annotations

from ._base import (
    MATCHED_LINE_ARM,
    MOVEMENT_ARM,
    _BOOKS_HOLD_FUNCTION,
    _OPENED_ON_FUNCTION,
    _OPENINGS_TABLE,
)
from ._matched_line import MATCHED_LINE_DAYS_SQL
from ._movement import SETTLED_MOVEMENTS_SQL

_OPENING_FUNCTION = "budget.assert_books_open_before_books_movements"

_OPENING_TRIGGER = "ck_books_open_before_movements"

#: The openings-side PREDICATES, stated once each and asked per affected
#: account.  The trigger above them is dispatch: which account this event
#: touched.  Keeping the two apart is what lets one event ask about TWO
#: accounts -- a raw ``UPDATE`` moving a row between them -- without spelling
#: either check twice.
#:
#: **They are TWO functions and not one widened row set**, because the two
#: states have different repairs and an owner has to be told which one they are
#: in: a movement is re-dated or removed, a matched line is UNMATCHED.  One
#: predicate over a union could only name the earlier of the two days and would
#: name the wrong remedy half the time.
_OPENING_PREDICATE_FUNCTION = "budget.assert_account_books_hold_its_movements"

_MATCHED_LINES_PREDICATE_FUNCTION = (
    "budget.assert_account_books_hold_its_matched_lines"
)

_CREATE_OPENING_PREDICATE_SQL = f"""
CREATE OR REPLACE FUNCTION {_OPENING_PREDICATE_FUNCTION}(p_account_id INTEGER)
RETURNS VOID AS $$
DECLARE
    v_opened_on DATE;
    v_earliest DATE;
BEGIN
    -- **The predicate is over the account's RESULTING STATE, not over the row
    -- that was written**, and that is what makes one function serve every
    -- event.  An INSERT, an UPDATE and a DELETE on
    -- ``budget.account_openings`` all change the same thing -- which row
    -- governs -- so each is checked by asking the same question afterwards:
    -- do this account's books, as they now stand, hold every movement it
    -- records?  The alternative (grade the written row, and skip it when some
    -- other row governs) needs the governing lookup spelled a SECOND time to
    -- find out, and it is blind to a DELETE, where the row that breaks the
    -- invariant is the one that SURVIVED.
    v_opened_on := {_OPENED_ON_FUNCTION}(p_account_id);

    -- No opening record at all.  Two ways to get here and neither is a
    -- violation: a CASCADE from ``budget.accounts`` has just disposed of the
    -- whole account, and (unreachably, but stated rather than assumed) an
    -- account that never got one.  The READ side already refuses the second
    -- loudly -- ``cash_ledger.account_opening_fact`` raises rather than
    -- fabricating a level -- so raising here would make this function enforce
    -- a SECOND invariant no other constraint states, and would surface it as
    -- a COMMIT abort on an unrelated write path.
    IF v_opened_on IS NULL THEN
        RETURN;
    END IF;

    -- The earliest day the account records money moving, over BOTH movement
    -- tables -- the same union the movement trigger's two attachments cover
    -- from the other side.
    --
    -- **SOFT-DELETED rows are counted, and this row set is deliberately WIDER
    -- than the fold's.**  ``balance_contributing_clause`` excludes
    -- ``is_deleted`` rows and the Credit / Cancelled statuses; this counts
    -- them, so a soft-deleted settled row still bounds how far back its
    -- account's books may be restated.  Narrowing to match the fold would
    -- open a hole on RESTORE: un-deleting is an ``UPDATE`` of ``is_deleted``
    -- alone, and the movement trigger fires ``UPDATE OF settled_on,
    -- account_id``, so a restored pre-books row would pass both tiers
    -- untouched.  The cost is over-refusal -- the books cannot move past a
    -- day whose only row the owner cannot see -- and that is the safe
    -- direction: it refuses a legal act loudly rather than admitting an
    -- illegal one silently.  Stated because two statements of one rule that
    -- differ silently is the failure this arc names as its own root cause.
    SELECT MIN(settled_on) INTO v_earliest FROM (
        {SETTLED_MOVEMENTS_SQL}
    ) AS movements WHERE account_id = p_account_id;

    IF v_earliest IS NOT NULL
       AND NOT {_BOOKS_HOLD_FUNCTION}(v_opened_on, v_earliest) THEN
        RAISE EXCEPTION
            'account % cannot open its books on %: a movement is already '
            'dated %, and an opening equity is the closing balance for its '
            'own day, so that money would be counted twice',
            p_account_id, v_opened_on, v_earliest;
    END IF;
END;
$$ LANGUAGE plpgsql
"""

_CREATE_MATCHED_LINES_PREDICATE_SQL = f"""
CREATE OR REPLACE FUNCTION {_MATCHED_LINES_PREDICATE_FUNCTION}(
    p_account_id INTEGER
)
RETURNS VOID AS $$
DECLARE
    v_opened_on DATE;
    v_earliest DATE;
BEGIN
    -- The openings-side twin of the movement predicate beside it, over the
    -- second row set, and it is asked about the account's RESULTING STATE for
    -- the same reason: an INSERT, an UPDATE and a DELETE all change which
    -- opening governs, so each is graded by asking the same question after.
    v_opened_on := {_OPENED_ON_FUNCTION}(p_account_id);

    IF v_opened_on IS NULL THEN
        RETURN;
    END IF;

    -- The earliest day the account has MATCHED a bank line on.
    --
    -- **This is the state the movement predicate cannot see**, and it is not
    -- a redundant tightening.  A match settles every member on the LATEST of
    -- its bank days, so the earliest line of a multi-day group posts strictly
    -- before the row explaining it settles; restating the books into that gap
    -- passes ``{_OPENING_PREDICATE_FUNCTION}`` -- the movement really is
    -- later -- while the line's money lands inside the opening equity AND
    -- inside a settled row.  Plan step balance:X-f3c-2b-2b measured the
    -- import side of the same defect on a production clone and closed both
    -- ends together.
    SELECT MIN(posted_on) INTO v_earliest FROM (
        {MATCHED_LINE_DAYS_SQL}
    ) AS matched WHERE account_id = p_account_id;

    IF v_earliest IS NOT NULL
       AND NOT {_BOOKS_HOLD_FUNCTION}(v_opened_on, v_earliest) THEN
        RAISE EXCEPTION
            'account % cannot open its books on %: a bank line you have '
            'matched is dated %, and an opening equity is the closing '
            'balance for its own day, so that money would be counted twice',
            p_account_id, v_opened_on, v_earliest;
    END IF;
END;
$$ LANGUAGE plpgsql
"""

def _openings_predicates_for(arms: "tuple[str, ...]") -> "tuple[str, ...]":
    """Return the openings-side predicates for *arms*, in REFUSAL order.

    **Movements first, then matched lines**, because that is the order an owner
    can act in: a movement is the account's own record and is re-dated or
    removed, while a matched line is an assertion ABOUT a record and is undone
    one level up.  Only the first failure is reported, so which is asked first
    decides which repair the owner is sent to.

    Args:
        arms: The arms being installed -- see :data:`ALL_ARMS`.

    Returns:
        The schema-qualified predicate names the openings dispatcher must
        ``PERFORM``, empty when *arms* is.
    """
    predicates = []
    if MOVEMENT_ARM in arms:
        predicates.append(_OPENING_PREDICATE_FUNCTION)
    if MATCHED_LINE_ARM in arms:
        predicates.append(_MATCHED_LINES_PREDICATE_FUNCTION)
    return tuple(predicates)

def _create_opening_func_sql(arms: "tuple[str, ...]") -> str:
    """Return the openings dispatcher, asking exactly *arms*' predicates.

    **The body is GENERATED rather than fixed, and that is what makes an
    arm-explicit install possible at all.**  The dispatcher names its
    predicates, so a revision installing only the movement arm needs a
    dispatcher that asks only the movements predicate -- the alternative is a
    body that ``PERFORM``s a function that revision never created, which
    PL/pgSQL accepts at ``CREATE`` time and fails on at COMMIT.

    It is also what lets ``d1f6a83c9e47``'s downgrade be one call instead of a
    hand-frozen copy of the previous bodies: re-applying with the movement arm
    alone regenerates this dispatcher without the matched-line term, so there
    is nothing to keep in step by hand.

    **Two tables' triggers run it**: ``budget.account_openings``'s, and,
    with the loan-origination arm, ``budget.loan_params``'s
    (:data:`._loan_origination._CREATE_LOAN_TERMS_TRIGGER_SQL`).  Both are a
    row naming an account by ``account_id`` whose INSERT, UPDATE or DELETE can
    move that account's books day, so the dispatch is the same and is stated
    once.  Its SQL comment speaks of "this event" for that reason, and its
    body did not change when the second table joined.

    Args:
        arms: The arms being installed -- see :data:`ALL_ARMS`.

    Returns:
        The ``CREATE OR REPLACE FUNCTION`` statement for the dispatcher.
    """
    predicates = _openings_predicates_for(arms)
    old_arm = "\n".join(
        f"        PERFORM {name}(OLD.account_id);" for name in predicates
    )
    new_arm = "\n".join(
        f"        PERFORM {name}(NEW.account_id);" for name in predicates
    )
    return f"""
CREATE OR REPLACE FUNCTION {_OPENING_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    -- DISPATCH ONLY: name the accounts this event could have changed the
    -- books of, and ask each installed predicate about each.  There are two
    -- accounts only for a raw ``UPDATE`` that moves a row BETWEEN them, which
    -- no door does and which would otherwise leave the abandoned account's
    -- books ungraded -- the same reason the arms below are written out rather
    -- than collapsed into ``NEW``.
    --
    -- The predicates asked here are the ones the INSTALLING revision declared;
    -- their order is ``_openings_predicates_for``'s, whose docstring says why
    -- movements are asked first.
    IF TG_OP <> 'INSERT' THEN
{old_arm}
    END IF;
    IF TG_OP <> 'DELETE' AND (
        TG_OP = 'INSERT' OR NEW.account_id IS DISTINCT FROM OLD.account_id
    ) THEN
{new_arm}
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

#: **INSERT, UPDATE and DELETE**, where the movement side needs only the first
#: two.  The table is append-only and a restatement IS an insert, so INSERT is
#: the only door; the other two arms exist because the invariant is a property
#: of the SURVIVING rows rather than of the written one.  A raw ``UPDATE``
#: moving the governing row's ``opened_on`` forward, and a raw ``DELETE`` of
#: the governing row -- which promotes an older restatement whose day the live
#: movements may contradict -- both break it without any door being opened.
#: Neither is reachable through the ORM (``AccountOpening._block_update`` /
#: ``_block_delete`` refuse both), which is exactly why the DATABASE is where
#: they are stated: this tier's whole job is the writer nobody enumerated.  No
#: column list, because a ``DELETE`` has no columns to name and the table is
#: written to only when an account is created or restated.
#:
#: **The DELETE arm cannot make an account undeletable, and the reason is an
#: FK asymmetry rather than the predicate's own care.**  Disposing of an
#: account CASCADEs its openings away (``AccountScopedMixin.account_id`` is
#: ``ON DELETE CASCADE``) while ``budget.transactions.account_id`` is ON
#: DELETE **RESTRICT** -- so an account that still records a movement cannot
#: be deleted at all, and one that can be has no movement for the surviving
#: books to fail against.  At COMMIT the predicate finds no governing opening
#: and returns before it counts anything.
_CREATE_OPENING_TRIGGER_SQL = (
    f"CREATE CONSTRAINT TRIGGER {_OPENING_TRIGGER} "
    f"AFTER INSERT OR UPDATE OR DELETE ON {_OPENINGS_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED "
    f"FOR EACH ROW EXECUTE FUNCTION {_OPENING_FUNCTION}()"
)
