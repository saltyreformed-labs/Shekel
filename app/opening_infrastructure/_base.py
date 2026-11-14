"""The BASE of the books boundary: the comparison, and which opening governs.

Arm-independent, because every arm's predicate asks both of these.  They are
installed whatever a caller declares and they survive an arm's withdrawal,
which is what lets a downgrade restore BEHAVIOUR without keeping a frozen copy
of the previous function bodies -- see
:func:`~app.opening_infrastructure.apply_opening_functions`.

This file also holds the ARM VOCABULARY a revision declares itself with.  The
package docstring (:mod:`app.opening_infrastructure`) carries the argument
these definitions only implement.
"""

from __future__ import annotations

#: The MOVEMENT arm: a settled row may not be dated on or before its account's
#: opening.  Installed by revision ``d3b6f1c8a274``, which censuses and repairs
#: the movements already in the way before turning it on.
MOVEMENT_ARM = "movement"

#: The MATCHED-LINE arm: a bank line some match names may not be dated on or
#: before its account's opening.  Installed by revision ``d1f6a83c9e47``, which
#: censuses the matched lines already in the way before turning it on.
MATCHED_LINE_ARM = "matched_line"

#: The LOAN-ORIGINATION arm: a CONFIGURED LOAN's books open on its
#: origination (ruling plan#3954), and the writes that decide whether an
#: account is one are graded too.  Installed by revision ``e5b2c8a41f73``,
#: which censuses the movements and matched lines already in the way before
#: turning it on.
#:
#: **It is an arm in the sense the vocabulary already has -- a part of the
#: boundary a revision declares -- and not a third row set.**  The movement and
#: matched-line arms each add rows the boundary grades; this one changes the
#: DAY they are graded against, in :func:`_create_opened_on_sql`, and attaches
#: the boundary to the three tables that decide it (``budget.loan_params``,
#: ``budget.accounts.account_type_id``, ``ref.account_types.has_amortization``;
#: :mod:`._loan_origination`).  So it adds no predicate, and an arm set naming
#: it ALONE asks nothing: :func:`~app.opening_infrastructure.apply_opening_functions`
#: refuses one.
LOAN_ORIGINATION_ARM = "loan_origination"

#: Every arm this module currently knows how to build, **for the two SCRIPTS
#: that materialise a database at HEAD** and for nothing else.
#:
#: **A MIGRATION names its arms as a literal tuple instead, and that closes the
#: ARM axis -- which is not the same as closing every axis.**  These builders
#: are imported LIVE from ``app/``, so a revision that let the module choose
#: would install whatever it had grown into by the time someone ran it -- and it
#: was measured doing exactly that: with the matched-line arm added and
#: ``d3b6f1c8a274`` still calling the builder unqualified, that revision
#: installed ``ck_matched_line_after_books_open`` and
#: ``ck_line_day_after_books_open`` five revisions before ``d1f6a83c9e47`` runs
#: the census that decides whether the rows already there can satisfy them.
#: (Observed on a clone stopped at ``c9f4b1e78d02``, which is one revision
#: before the census; the five is the span from ``d3b6f1c8a274``, where the arm
#: goes in.)  A constraint installed ahead of its census is the failure this arc
#: names as its own root cause (finding **N-400**), reached from the migration
#: side.
#:
#: **What this does NOT close: the BODY axis.**  A revision still reads
#: :data:`GOVERNING_ORDER_SQL` and every ``_CREATE_*`` body live, so a shipped
#: revision installs today's definitions rather than its own.  That is load
#: bearing and unfixed: ``d3b6f1c8a274`` interpolates
#: :data:`GOVERNING_ORDER_SQL`, so it now installs ``id DESC`` four revisions
#: before ``c9f4b1e78d02`` -- the revision whose whole purpose is to introduce
#: that ordering -- while ``c9f4b1e78d02``'s downgrade still restores the frozen
#: ``created_at DESC, id DESC`` its own docstring calls known-broken.  A
#: downgrade past it therefore installs an ordering the database never had.
#: Reported rather than fixed here (``CLAUDE.md`` rule 6): it predates this step
#: and the remedy is the same arm-explicit treatment applied to bodies, which is
#: a step of its own.
#:
#: The name is deliberately not something a revision would reach for: a
#: revision declares, a script materialises.
#:
#: *The BODY axis is closed for one function since plan task plan#3960:*
#: ``budget.account_books_opened_on`` is generated from the arm set
#: (:func:`_create_opened_on_sql`), so a revision naming no
#: :data:`LOAN_ORIGINATION_ARM` installs the governing-opening day it always
#: had, never the loan-aware one.  Every other body is still read live.
ALL_ARMS = (MOVEMENT_ARM, MATCHED_LINE_ARM, LOAN_ORIGINATION_ARM)

#: The day an account's books open, stated ONCE in SQL so every trigger below
#: reads it the same way.  Without :data:`LOAN_ORIGINATION_ARM` it is the
#: GOVERNING opening record's day, the way ``cash_ledger.account_opening_fact``
#: reads it in Python; the order is :data:`GOVERNING_ORDER_SQL`, whose own note
#: records why it lost its ``created_at`` term at plan step X-f3c-2b-2a: the
#: table is append-only and the latest restatement governs (ruling **R-HE**).
#: With that arm, a configured loan's day is its origination instead
#: (:func:`_create_opened_on_sql`), the way ``cash_ledger.books_start`` reads
#: an account's books start in Python since plan task plan#3964.
_OPENED_ON_FUNCTION = "budget.account_books_opened_on"

#: THE COMPARISON, in SQL, so this tier states it exactly once -- the same
#: move :data:`_OPENED_ON_FUNCTION` makes for "which opening governs", and for
#: its reason.  ``cash_ledger.books_hold`` is the Python half; between them the
#: rule ruling **R-HG** turns on (``>``, not ``>=``) is stated ONCE PER TIER
#: rather than in one Python function and five hand-typed PL/pgSQL predicates.
#:
#: **"In two places" would be the wrong count, and the exception is worth
#: naming rather than rounding off**: revision ``d3b6f1c8a274`` spells the same
#: rule three more times in live SQL (``m.earliest <= g.opened_on`` in
#: ``_ACCOUNTS_TO_RESTATE``, the same predicate again inside
#: ``_DECLARED_OPENINGS_IN_THE_WAY``, and the ``(m.earliest - 1)::date`` that
#: encodes the same reading as day arithmetic).  Those are the CENSUS and
#: REPAIR queries of the revision that installs this function, so they cannot
#: ask it -- it does not exist until they have run.  The claim this constant
#: can make is about the trigger tier, which is what it is now worded as.
#:
#: **It was five before plan step balance:X-f3c-2b-2b's own adversarial review
#: found them**, and three of those five were that step's: the module's Python
#: twin carried a docstring claiming the comparison was "stated once" while
#: the step added open-coded copies one file over.  ``IMMUTABLE`` and
#: ``LANGUAGE sql`` so the planner inlines it, which is what makes using it in
#: a per-row trigger free.
_BOOKS_HOLD_FUNCTION = "budget.books_hold"

_OPENINGS_TABLE = "budget.account_openings"

#: The RECORDING order that decides which opening record governs (ruling
#: **R-HE**): the table is append-only and the latest restatement wins.  Public
#: because the revision's three window functions, this module's own
#: ``budget.account_books_opened_on`` and
#: ``cash_ledger.governing_account_opening`` must all break it the same way; a
#: fourth spelling is how two tiers come to disagree about which restatement is
#: in force.
#:
#: **It is ``id`` ALONE, and it was ``created_at DESC, id DESC`` until plan step
#: X-f3c-2b-2a's adversarial review refuted the reason for the first term.**
#: That reason -- stated in this module and twice in ``cash_ledger`` -- was that
#: ``created_at`` is set on INSERT and so is monotone in recording order.  It is
#: not: :class:`app.models.mixins.CreatedAtMixin` defaults it to
#: ``db.func.now()``, which in PostgreSQL is ``transaction_timestamp()`` -- the
#: instant the transaction BEGAN.  ``loan_anchor_service._governing_loan_anchor``
#: already says so in as many words about the loan twin, and this door never
#: carried the implication across.  **The failure it produced is a SILENT NO-OP
#: on the level every balance rests on**: two restatements from two tabs, the
#: one whose transaction opened EARLIER commits SECOND under the owner's write
#: lock, and its row sorts below the row it was meant to supersede -- so the
#: owner is told "Books restated" and nothing moved.
#:
#: ``id`` is a sequence value allocated when the INSERT executes, and the write
#: door's compare-and-append runs under the owner's write lock, which a
#: signed-in request's transaction takes before reading any of the owner's
#: data (plan step ``balance:X-bn``); an origination at registration runs
#: under none, on an account no other transaction can see yet.  So within an
#: account the id order IS the order the owner made the statements.  The
#: migration writes every row in one transaction and at most one per account,
#: so it is unaffected either way.
GOVERNING_ORDER_SQL = "id DESC"

_CREATE_BOOKS_HOLD_SQL = f"""
CREATE OR REPLACE FUNCTION {_BOOKS_HOLD_FUNCTION}(
    p_opened_on DATE, p_day DATE
)
RETURNS BOOLEAN AS $$
    -- Whether books opening on p_opened_on may record money moving on p_day.
    -- The opening equity is the CLOSING balance for its own day (ruling
    -- R-HG), so a day ON it is already inside the figure: the test is a
    -- STRICT ``>``, and the ruling weighed and rejected the start-of-day
    -- reading.  ``cash_ledger.books_hold`` is the Python half of this one
    -- sentence; every predicate below asks THIS rather than re-spelling it.
    SELECT p_day > p_opened_on;
$$ LANGUAGE sql IMMUTABLE
"""

#: The GOVERNING opening record's day for ``p_account_id``, as one ``SELECT``:
#: no row when the account carries none.  Stated once and used by both bodies
#: :func:`_create_opened_on_sql` generates, so the arm set decides only whether
#: a loan's origination comes first, never how the governing row is found.
_GOVERNING_OPENED_ON_SQL = f"""SELECT opened_on
      FROM {_OPENINGS_TABLE}
     WHERE account_id = p_account_id
     ORDER BY {GOVERNING_ORDER_SQL}
     LIMIT 1"""

#: A CONFIGURED LOAN's origination day for ``p_account_id``, as one scalar
#: subquery: NULL unless the account's kind amortizes AND it carries loan terms.
#:
#: **Both halves are the two ``balance_at._resolution.configured_loan``
#: tests, and neither implies the other** (that function's docstring argues it):
#: a ``budget.loan_params`` row on an account whose type does not amortize is a
#: data defect the balance refuses to amortize, and an amortizing account
#: whose terms were never entered is cash to the balance.  The boundary follows
#: the balance's dispatch, so it reads both, and nothing else:
#: ``loan_params.account_id`` is unique, so this is one row or none, and
#: ``origination_date`` is NOT NULL, so NULL here means "not a configured loan"
#: and nothing more.
_CONFIGURED_LOAN_ORIGINATION_SQL = """(
            SELECT params.origination_date
              FROM budget.loan_params AS params
              JOIN budget.accounts AS accounts
                ON accounts.id = params.account_id
              JOIN ref.account_types AS types
                ON types.id = accounts.account_type_id
             WHERE params.account_id = p_account_id
               AND types.has_amortization)"""


def _create_opened_on_sql(arms: "tuple[str, ...]") -> str:
    """Return ``budget.account_books_opened_on`` for the arm set *arms*.

    **The day an account's books open, which every predicate of every arm
    asks.**  Without :data:`LOAN_ORIGINATION_ARM` it is the governing opening
    record's day.  With it, a configured loan's books open on its origination
    and every other account's on its governing opening (ruling plan#3954): a
    loan's balance starts from its origination
    (``loan_loaders._terms.synthesize_origination_anchor``), so the boundary
    starts there too, and a configured loan's ``budget.account_openings`` row
    decides nothing.

    **Generated from the arm set, so the BODY follows the revision** (the axis
    :data:`ALL_ARMS` records as open for every other body): a revision before
    the loan arm installs the governing-opening day it always had.  **Without
    the loan arm the body is byte-for-byte the one every revision installed
    before plan task plan#3960**, so withdrawing the arm restores the
    database's bytes and not only its behaviour (measured: ``md5(prosrc)``
    on a production copy, before the upgrade and after its downgrade).

    ``COALESCE`` is exact here and not a fallback: the loan subquery is NULL
    precisely when the account is not a configured loan
    (:data:`_CONFIGURED_LOAN_ORIGINATION_SQL`).  NULL overall still means an
    account with no opening record and no loan terms, which every predicate
    reads as "bounds nothing" (the movement function argues why).

    ``LANGUAGE sql STABLE``, so the planner may inline it, and so
    ``check_function_bodies`` resolves every table it names at ``CREATE`` time:
    applied before ``budget.loan_params``, ``budget.accounts`` or
    ``ref.account_types`` exists, the loan-aware body fails loudly there.

    Args:
        arms: The arms being installed -- see :data:`ALL_ARMS`.

    Returns:
        The ``CREATE OR REPLACE FUNCTION`` statement.
    """
    if LOAN_ORIGINATION_ARM in arms:
        body = f"""\
    -- A CONFIGURED LOAN's books open on its origination (ruling
    -- plan#3954), every other account's on its GOVERNING opening record's
    -- day; NULL when the account has neither.  ``budget.account_openings``
    -- is append-only and the latest RECORDING instant governs (ruling R-HE);
    -- ``id`` breaks a same-instant tie, exactly as the Python loader does.
    SELECT COALESCE(
        {_CONFIGURED_LOAN_ORIGINATION_SQL},
        ({_GOVERNING_OPENED_ON_SQL}));
"""
    else:
        body = f"""\
    -- The GOVERNING opening record's day, or NULL when the account carries
    -- none.  ``budget.account_openings`` is append-only and the latest
    -- RECORDING instant governs (ruling R-HE); ``id`` breaks a same-instant
    -- tie, exactly as the Python loader does, so the two cannot disagree
    -- about which restatement is in force.
    {_GOVERNING_OPENED_ON_SQL};
"""
    return f"""
CREATE OR REPLACE FUNCTION {_OPENED_ON_FUNCTION}(p_account_id INTEGER)
RETURNS DATE AS $$
{body}$$ LANGUAGE sql STABLE
"""

def _drop_trigger_sql(trigger: str, table: str) -> str:
    """Return the guarded ``DROP TRIGGER`` for *trigger* on *table*.

    PostgreSQL has no ``CREATE CONSTRAINT TRIGGER IF NOT EXISTS``, so every
    apply pairs a guarded drop with a fresh create to stay idempotent.  The
    same pattern :mod:`app.posting_infrastructure` uses, spelled as a helper
    here because this module attaches three triggers rather than one.

    Args:
        trigger: The trigger's name.
        table: The schema-qualified table it is attached to.

    Returns:
        The ``DROP TRIGGER IF EXISTS`` statement.
    """
    return f"DROP TRIGGER IF EXISTS {trigger} ON {table}"
