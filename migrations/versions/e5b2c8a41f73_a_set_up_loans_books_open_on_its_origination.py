"""A set-up loan's books open on its origination

Revision ID: e5b2c8a41f73
Revises: c3a7e5d91f42
Create Date: 2026-11-13 22:30:00.000000

Card plan#2436, task plan#3960, ruling **plan#3954** (option A): a configured
loan's books open on the origination date on its terms.

**What is wrong.**  The books boundary reads one day per account, the governing
``budget.account_openings.opened_on``, and for a loan nothing maintains it:
``account_service.create_account`` writes the creation form's day, migration
``d3b6f1c8a274`` set the two production loans' rows one day before their
earliest recorded movement, and no loan door writes or restates it.  The loan's
balance starts from its origination instead
(``loan_loaders._terms.synthesize_origination_anchor``).  So the Van Loan,
originated 2023-02-14, has books that open 2026-04-22, and matching SECU's
2026-04-22 line to the April payment re-dates the loan's leg onto that day and
is refused.

**What this installs: the LOAN-ORIGINATION arm**
(:data:`app.opening_infrastructure.LOAN_ORIGINATION_ARM`).

* ``budget.account_books_opened_on`` answers a configured loan's
  ``loan_params.origination_date`` (an account whose type has
  ``has_amortization`` and that carries a ``budget.loan_params`` row, the two
  halves ``balance_at._resolution.configured_loan`` tests), and every other
  account's governing opening as before.  The movement, matched-line and
  openings predicates all ask it, so they judge a loan by its origination with
  no change of their own.
* Three deferred constraint triggers grade the writes that decide whether an
  account is a configured loan, so the state stays unstorable from any single
  transaction: ``ck_books_open_after_loan_terms`` on ``budget.loan_params``
  (insert, a change of ``origination_date`` or ``account_id``, delete; it runs
  the openings dispatcher), ``ck_books_open_after_account_retype`` on
  ``budget.accounts.account_type_id`` and ``ck_books_open_after_type_retype``
  on ``ref.account_types.has_amortization``.  Each is dispatch to the two
  openings-side predicates and re-spells nothing.

**The census runs BEFORE the triggers and REFUSES rather than repairs**, as
``d1f6a83c9e47``'s does: a constraint over rows that already break it does not
enforce an invariant, it aborts the next write touching them (finding
**N-400**).  It asks the books day this revision has just installed, over every
movement and every matched line, so it grades exactly what the triggers will.
Moving a loan's books day can only make a movement illegal where the loan
originated AFTER its opening record, so the rows it can find are payments or
matched lines on or before a configured loan's origination.  The ways out are
the owner's (re-date the payment, undo the match), never a migration's.
*Measured 2026-11-13* on a restore of production's 20:47 dump (plan#3956's
census, ``c2.sql``): 0 movements and 0 matched lines on or before either
loan's origination.

**The Python tier still reads the opening record after this revision**
(``cash_ledger.account_opening_fact``), so it refuses what this tier now
accepts for a loan whose origination precedes its opening record, which is
both production loans.  Task plan#3964 gives both tiers one producer; the two
land in one pull request.

Review: Claude Opus 5.5, 2026-11-13 -- it drops and recreates the EIGHT books
boundary constraint triggers (three of them new here:
``ck_books_open_after_loan_terms``, ``ck_books_open_after_account_retype``,
``ck_books_open_after_type_retype``), it REPLACES the body of
``budget.account_books_opened_on`` (the loan-aware day above), and it creates
two functions (``budget.assert_books_hold_after_account_retype``,
``budget.assert_books_hold_after_type_retype``).  Every other function body it
re-issues is the one already installed, because
``apply_opening_infrastructure`` is idempotent and re-pins every trigger to
the in-code definition for the arms it is given.  Declared under
``.claude/rules/database.md`` because drop-and-recreate needs the line whether
or not the recreate is identical.
"""

from alembic import op
import sqlalchemy as sa

from app.opening_infrastructure import (
    LOAN_ORIGINATION_ARM,
    MATCHED_LINE_ARM,
    MATCHED_LINE_DAYS_SQL,
    MOVEMENT_ARM,
    SETTLED_MOVEMENTS_SQL,
    apply_opening_functions,
    apply_opening_infrastructure,
)

#: The arms this revision leaves installed.  Named as a LITERAL rather than as
#: ``ALL_ARMS``: a revision declares the boundary its own point in history
#: describes.  See ``opening_infrastructure.ALL_ARMS``.
_ARMS_AFTER = (MOVEMENT_ARM, MATCHED_LINE_ARM, LOAN_ORIGINATION_ARM)

#: The arms its downgrade leaves: ``d1f6a83c9e47``'s.
_ARMS_BEFORE = (MOVEMENT_ARM, MATCHED_LINE_ARM)


# revision identifiers, used by Alembic.
revision = "e5b2c8a41f73"
down_revision = "c3a7e5d91f42"
branch_labels = None
depends_on = None


#: Every movement and every matched line on or before its account's books day,
#: as the INSTALLED ``budget.account_books_opened_on`` answers it.
#:
#: **It reads the row sets the constraint counts**
#: (:data:`~app.opening_infrastructure.SETTLED_MOVEMENTS_SQL`,
#: :data:`~app.opening_infrastructure.MATCHED_LINE_DAYS_SQL`), interpolated
#: rather than re-spelled, and asks the comparison and the day the predicates
#: ask, so the census cannot answer a narrower question than the triggers
#: installed after it.  Each direction runs it after
#: :func:`apply_opening_functions` has installed the day that direction
#: leaves, inside the one transaction ``migrations/env.py`` opens.
_CENSUS_SQL = f"""
    SELECT census.kind,
           census.account_id,
           accounts.name,
           MIN(census.day) AS earliest,
           budget.account_books_opened_on(census.account_id) AS opened_on,
           COUNT(*) AS offending_rows
      FROM (
            SELECT 'movement' AS kind, movements.account_id,
                   movements.settled_on AS day
              FROM ({SETTLED_MOVEMENTS_SQL}) AS movements
            UNION ALL
            SELECT 'matched line' AS kind, matched.account_id,
                   matched.posted_on AS day
              FROM ({MATCHED_LINE_DAYS_SQL}) AS matched
           ) AS census
      JOIN budget.accounts AS accounts
        ON accounts.id = census.account_id
     WHERE NOT budget.books_hold(
               budget.account_books_opened_on(census.account_id),
               census.day)
     GROUP BY census.kind, census.account_id, accounts.name
     ORDER BY census.account_id, census.kind
"""

#: The movement rows on a configured loan, joined to its terms, for the two
#: diagnostic queries below.  Text for an operator, never executed here.
_LOAN_MOVEMENTS_TEXT = (
    "SELECT m.account_id, m.settled_on, p.origination_date FROM (SELECT "
    "account_id, settled_on FROM budget.transactions WHERE settled_on IS NOT "
    "NULL UNION ALL SELECT account_id, settled_on FROM "
    "budget.transaction_entries WHERE settled_on IS NOT NULL) m JOIN "
    "budget.loan_params p ON p.account_id = m.account_id JOIN budget.accounts "
    "a ON a.id = p.account_id JOIN ref.account_types t ON t.id = "
    "a.account_type_id WHERE t.has_amortization"
)


def _offending_accounts(connection) -> str | None:
    """Return one line per account the installed books day refuses, or None.

    Args:
        connection: The Alembic connection.

    Returns:
        The accounts, one per line, each with its books day, the kind of row,
        the earliest offending day and how many rows; ``None`` when the
        census finds nothing.
    """
    offenders = connection.execute(sa.text(_CENSUS_SQL)).fetchall()
    if not offenders:
        return None
    return "\n".join(
        f"  account {row.account_id} ({row.name}): books open {row.opened_on}, "
        f"earliest {row.kind} {row.earliest}, {row.offending_rows} row(s)"
        for row in offenders
    )


def _reject_upgrade_over_violations(connection) -> None:
    """Refuse the upgrade when a row sits on or before a loan's origination.

    Args:
        connection: The Alembic connection, after the loan-aware books day
            is installed.

    Raises:
        RuntimeError: When any movement or matched line is dated on or before
            its account's books day under the loan-origination arm, naming
            every account, because the ways out (re-date the payment, undo
            the match) are the owner's.

    Note:
        **The diagnostic query spells the loan rule out rather than calling
        ``budget.account_books_opened_on``.**  Raising rolls back this
        revision's ``CREATE OR REPLACE`` of that function with everything
        else in the chain's one transaction, so a query calling it would be
        answered by the PREVIOUS body, which reads the opening record: for a
        loan, exactly the day this refusal is not about.
    """
    offending = _offending_accounts(connection)
    if offending is None:
        return
    raise RuntimeError(
        "e5b2c8a41f73 cannot open a configured loan's books on its "
        "origination: these accounts already record a movement or a matched "
        "bank line on or before the day their books would open, and the "
        "constraint would abort the next write touching them.\n"
        f"{offending}\n"
        "Repair each through the app's own doors before upgrading -- re-date "
        "the payment to a day after the loan's origination, or undo the "
        "match -- then re-run.  The loan payments in the way: "
        f"{_LOAN_MOVEMENTS_TEXT} AND m.settled_on <= p.origination_date"
    )


def _reject_downgrade_over_violations(connection) -> None:
    """Refuse the downgrade when a loan holds a row its opening record refuses.

    **The state this revision admits and the one before it cannot hold**: a
    configured loan whose origination precedes its opening record may record
    a payment between the two (line 172's match re-dates the Van Loan's leg
    onto its 2026-04-22 opening record), and the governing-opening day
    restored here refuses it.  Withdrawing the arm over such a row would
    leave a constraint the next write touching it aborts on (finding
    **N-400**), so the downgrade refuses instead.  A cash account's day does
    not change in either direction, so only a loan can be named.

    Args:
        connection: The Alembic connection, after the governing-opening books
            day is restored.

    Raises:
        RuntimeError: When any movement or matched line is dated on or before
            its account's governing opening record, naming every account.

    Note:
        The diagnostic query spells the opening-record rule out, for the
        upgrade's reason: the rollback leaves the loan-aware body standing.
    """
    offending = _offending_accounts(connection)
    if offending is None:
        return
    raise RuntimeError(
        "e5b2c8a41f73 cannot be downgraded: these accounts record a movement "
        "or a matched bank line on or before their governing opening record, "
        "which a configured loan's origination admitted and the restored "
        "books day refuses.\n"
        f"{offending}\n"
        "Undo each match or re-date each payment past the opening record "
        "before downgrading.  The loan payments in the way: "
        f"{_LOAN_MOVEMENTS_TEXT} AND m.settled_on <= (SELECT o.opened_on FROM "
        "budget.account_openings o WHERE o.account_id = m.account_id ORDER BY "
        "o.id DESC LIMIT 1)"
    )


def upgrade():
    """Install the FUNCTIONS, census the existing rows, then the triggers.

    **That order is forced**, for ``d1f6a83c9e47``'s reason: the census must
    run before the constraint, since a constraint trigger validates writes
    rather than existing rows, and it asks the loan-aware books day, which the
    function half is what installs.  Functions, census, triggers.
    """
    apply_opening_functions(op.execute, arms=_ARMS_AFTER)
    _reject_upgrade_over_violations(op.get_bind())
    apply_opening_infrastructure(op.execute, arms=_ARMS_AFTER)


def downgrade():
    """Withdraw the LOAN-ORIGINATION arm, censused, leaving the other two.

    The upgrade's order run backwards over the same census: restore the
    governing-opening books day, refuse over any row it cannot hold
    (:func:`_reject_downgrade_over_violations`), then one call that drops this
    revision's three triggers and two functions.  **One call, because the
    builder states the boundary TOTALLY**
    (:func:`~app.opening_infrastructure.apply_opening_infrastructure`): naming
    ``d1f6a83c9e47``'s two arms removes what they do not include and
    regenerates the books day without the loan term.
    """
    apply_opening_functions(op.execute, arms=_ARMS_BEFORE)
    _reject_downgrade_over_violations(op.get_bind())
    apply_opening_infrastructure(op.execute, arms=_ARMS_BEFORE)
