"""The LOAN-ORIGINATION arm: the writes that decide whether an account is a loan.

Installed by revision ``e5b2c8a41f73`` (ruling plan#3954), which censuses the
movements and matched lines already in the way before turning it on.  The
package docstring (:mod:`app.opening_infrastructure`) carries the argument.

**This arm adds no row set.**  With it, ``budget.account_books_opened_on``
answers a configured loan's origination rather than its opening record
(:func:`._base._create_opened_on_sql`), and the movement, matched-line and
openings predicates judge a loan by that day with no change of their own.  What
the arm adds is the INPUTS of that day, attached here, because a day that can
move without the boundary noticing is a day the boundary cannot bound:

* ``budget.loan_params`` -- a loan's terms appearing, its ``origination_date``
  or ``account_id`` changing, or the terms being deleted;
* ``budget.accounts.account_type_id`` -- an account re-typed across the loan
  boundary;
* ``ref.account_types.has_amortization`` -- a type itself crossing it, which
  moves every account of that type at once.

Each attachment is DISPATCH: it names the accounts whose books the event could
have moved, and asks each of them the openings-side predicates of the arms
installed beside it.  It never re-spells the rule; the predicates read
``budget.account_books_opened_on``, which is where the rule is.
"""

from __future__ import annotations

from ._openings import _OPENING_FUNCTION, _openings_predicates_for

_LOAN_PARAMS_TABLE = "budget.loan_params"

_ACCOUNTS_TABLE = "budget.accounts"

_ACCOUNT_TYPES_TABLE = "ref.account_types"

_ACCOUNT_KIND_FUNCTION = "budget.assert_books_hold_after_account_retype"

_TYPE_KIND_FUNCTION = "budget.assert_books_hold_after_type_retype"

_LOAN_TERMS_TRIGGER = "ck_books_open_after_loan_terms"

_ACCOUNT_KIND_TRIGGER = "ck_books_open_after_account_retype"

_TYPE_KIND_TRIGGER = "ck_books_open_after_type_retype"


def _performs(arms: "tuple[str, ...]", account: str, indent: str) -> str:
    """Return one ``PERFORM`` line per installed predicate, asking about *account*.

    Args:
        arms: The arms being installed; their openings-side predicates are
            asked in refusal order (:func:`._openings._openings_predicates_for`).
        account: The PL/pgSQL expression naming the account.
        indent: The leading whitespace of each line.

    Returns:
        The ``PERFORM`` statements, one per line.
    """
    return "\n".join(
        f"{indent}PERFORM {name}({account});"
        for name in _openings_predicates_for(arms)
    )


def _create_account_kind_func_sql(arms: "tuple[str, ...]") -> str:
    """Return the dispatcher for an account RE-TYPED, asking *arms*' predicates.

    One account, the row's own: a change of ``account_type_id`` can turn a
    configured loan into a cash account (its books then open on its opening
    record) or the reverse, and either can move the day past a movement or a
    matched line the account already records.

    **Generated from the arm set**, for the reason the openings dispatcher is
    (:func:`._openings._create_opening_func_sql`): a body that ``PERFORM``s a
    predicate its revision never created is accepted at ``CREATE`` time by
    PL/pgSQL and fails at COMMIT.

    Args:
        arms: The arms being installed -- see :data:`._base.ALL_ARMS`.

    Returns:
        The ``CREATE OR REPLACE FUNCTION`` statement.
    """
    return f"""
CREATE OR REPLACE FUNCTION {_ACCOUNT_KIND_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    -- DISPATCH ONLY: the account whose kind moved, asked whether its books,
    -- as they now open, still hold everything it records.  The trigger's
    -- WHEN clause has already said the type really changed.
{_performs(arms, "NEW.id", "    ")}
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def _create_type_kind_func_sql(arms: "tuple[str, ...]") -> str:
    """Return the dispatcher for a TYPE re-typed, asking *arms*' predicates.

    **Every account of the type**, not only the configured loans among them:
    choosing which ones moved would mean a second copy of the configured-loan
    rule, and the predicates already know it.  An account carrying no loan
    terms opens on its opening record whatever its type says, so asking it
    re-asks a question it already passes.  The table changes only when an
    owner edits one of their own types (``routes.accounts.types``, which
    already refuses the crossing while any account of the type holds a ledger
    line) or a revision does, so the loop is short and rare.

    Args:
        arms: The arms being installed -- see :data:`._base.ALL_ARMS`.

    Returns:
        The ``CREATE OR REPLACE FUNCTION`` statement.
    """
    return f"""
CREATE OR REPLACE FUNCTION {_TYPE_KIND_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    v_account_id INTEGER;
BEGIN
    -- DISPATCH ONLY: every account of the type whose ``has_amortization``
    -- moved, each asked the installed predicates.
    FOR v_account_id IN
        SELECT id FROM {_ACCOUNTS_TABLE} WHERE account_type_id = NEW.id
    LOOP
{_performs(arms, "v_account_id", "        ")}
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


#: A loan's TERMS: the attachment the day's own column lives on.
#:
#: **It runs the openings dispatcher itself**
#: (``budget.assert_books_open_before_books_movements``), because the event has
#: exactly that shape: a row naming an account by ``account_id``, whose INSERT,
#: UPDATE or DELETE can move that account's books day, and whose raw
#: ``UPDATE`` of ``account_id`` moves a row between two accounts.  The
#: dispatcher asks the account each event could have changed, OLD and NEW, so
#: one function serves both tables rather than two spelling one dispatch.
#:
#: * INSERT: terms appear, and the books move to the origination, LATER than
#:   the opening record when the loan originated after it.  The loan setup
#:   door accepts payments recorded before setup (``routes/loan/params.py``),
#:   so this is the ordinary case the refusal is for.
#: * UPDATE OF ``origination_date``, ``account_id``: the day moves, or the
#:   terms move to another account.  No other column decides the day, and the
#:   table is written on every loan edit (``payment_day``, ``term_months``),
#:   so the column list is the difference between an event per edit and one
#:   per change that matters.
#: * DELETE: the terms are gone and the account opens on its opening record,
#:   which can sit on or after a payment the loan's origination admitted.
#:   A CASCADE from ``budget.accounts`` reaches here too, and is answered as
#:   the openings table's is: the account is gone, its books day is NULL, and
#:   the predicates return before counting anything.
#:
#: No WHEN clause, because a combined INSERT / UPDATE / DELETE trigger may not
#: name ``OLD`` and ``NEW`` both.  Every event it queues is one the rule must
#: grade anyway.
_CREATE_LOAN_TERMS_TRIGGER_SQL = (
    f"CREATE CONSTRAINT TRIGGER {_LOAN_TERMS_TRIGGER} "
    "AFTER INSERT OR UPDATE OF origination_date, account_id OR DELETE "
    f"ON {_LOAN_PARAMS_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED "
    f"FOR EACH ROW EXECUTE FUNCTION {_OPENING_FUNCTION}()"
)

#: An account's KIND, which the configured-loan rule reads half of.
#:
#: **``WHEN`` the type really changed**, for the reason
#: :func:`._movement._create_movement_trigger_sql` gives: a deferred event is
#: QUEUED at statement time, and a queued event reserves the table against DDL
#: for the rest of the transaction.  ``budget.accounts`` is updated on every
#: rename, archive and sort, and an ORM flush names only the columns that
#: changed, but a raw ``UPDATE`` naming the column with its own value queues
#: nothing either.  No INSERT arm: a new account has no loan terms yet (they
#: name it by foreign key) and no movement, so its books are graded by its
#: opening record's own trigger.
_CREATE_ACCOUNT_KIND_TRIGGER_SQL = (
    f"CREATE CONSTRAINT TRIGGER {_ACCOUNT_KIND_TRIGGER} "
    f"AFTER UPDATE OF account_type_id ON {_ACCOUNTS_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW WHEN "
    "(NEW.account_type_id IS DISTINCT FROM OLD.account_type_id) "
    f"EXECUTE FUNCTION {_ACCOUNT_KIND_FUNCTION}()"
)

#: A type's KIND, the other half: one edit re-kinds every account of the type
#: with no ``account_type_id`` change for the trigger above to see.
#:
#: ``WHEN`` the flag really changed, as above.  No INSERT arm (no account has
#: a new type yet) and no DELETE arm (``budget.accounts.account_type_id`` is
#: ``ON DELETE RESTRICT``, so a type an account still names cannot go).
_CREATE_TYPE_KIND_TRIGGER_SQL = (
    f"CREATE CONSTRAINT TRIGGER {_TYPE_KIND_TRIGGER} "
    f"AFTER UPDATE OF has_amortization ON {_ACCOUNT_TYPES_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW WHEN "
    "(NEW.has_amortization IS DISTINCT FROM OLD.has_amortization) "
    f"EXECUTE FUNCTION {_TYPE_KIND_FUNCTION}()"
)
