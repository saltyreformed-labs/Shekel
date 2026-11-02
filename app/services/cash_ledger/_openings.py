"""An account's books OPENING: the level a cash fold starts from, loaded once.

The governing :class:`~app.models.account_opening.AccountOpening` row of an
account, as a fact (:class:`CashOpeningFact`), and its one load
(:func:`_governing_openings`) over one account's ids or every account one
owner holds (ruling **plan#2623**), with the two doors that read it -- the
WRITE door's question (:func:`governing_account_opening`, ``None`` for no
row) and the READER's (:func:`account_opening_fact`, which refuses one).

They were :mod:`._events`' own until plan task plan#2648 gave the loads a
scope, which took that module to one line under the 1,000-line ceiling: the
opening is the one fact kind there that is no event in the stream but the
level the stream starts from, and so the seam the ceiling was measuring, as
WHOSE movements was for :mod:`._movement_scope`.

Reads only: no write of its own.  Each query autoflushes what the caller's
session holds pending, as every query in the session does.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.extensions import db
from app.models.account_opening import AccountOpening
from app.utils.dates import utc_instant


@dataclass(frozen=True)
class CashOpeningFact:
    """What an account held BEFORE its records begin, as a loaded fact.

    The governing :class:`~app.models.account_opening.AccountOpening` row for
    one account (plan step **X-f3c-2a**, ruling **R-GX**), read once per walk
    and carried on :class:`~._walk.CashLedgerWalk` beside the assertions and the
    movements.  It is the LEVEL the fold's running total starts from: every
    balance the app renders is this figure plus what the records say happened
    since.

    **It replaces a derivation, and the model docstring lists the four defects
    that derivation caused.**  Until this step the same quantity was recomputed
    on every read as "the earliest assertion minus the movements dated at or
    before it" (ruling **R-I**), which made it move when an assertion was
    back-dated, differ between scenarios, impossible to correct, and derived a
    second time by the posted ledger.

    Attributes:
        opening_id: The ``budget.account_openings`` row's own id -- the
            restatement this fact is, so a reader can tell two apart.
        account_id: The account whose books these are.
        opened_on: The civil day the books opened.  The ``account_opening``
            journal entry is dated on it, which is what stops a back-dated
            assertion re-dating that entry.
        opening_equity: The capital the books opened with, LEDGER-NATIVE and
            in the same sign convention as
            :attr:`CashAnchorFact.anchor_balance`.
        source_id: ``ref.account_opening_sources.id`` -- whether a human stated
            this figure or the X-f3c-2a migration derived it.  Carried because
            a derived figure is the old inference frozen and may be WRONG
            (finding **N-275** measures one wrong by ``$436.05``), so a surface
            must be able to tell a guess from an observation.  The walk itself
            never branches on it: an opening is an opening whatever wrote it.
        recorded_at: The RECORDING instant, aware-UTC.  It is what ORDERS the
            restatements (see :func:`account_opening_fact`) and it dates
            nothing -- :attr:`opened_on` is the business date, the same
            two-clock split :attr:`CashAnchorFact.asserted_at` documents one
            table over.  Carried since plan step **X-f3c-2b** so the history
            card can caption a RESTATED opening the way it captions a
            back-dated assertion: books opening in March, recorded in August,
            is a fact the owner should be able to see rather than infer.
    """

    opening_id: int
    account_id: int
    opened_on: date
    opening_equity: Decimal
    source_id: int
    recorded_at: datetime



def governing_account_opening(account_id: int) -> CashOpeningFact | None:
    """Return *account_id*'s governing opening record, or ``None`` for no row.

    **The WRITE door's question**, and the non-raising twin of
    :func:`account_opening_fact` exactly as
    :func:`~._facts.governing_anchor_on` is :func:`~._facts.resolve_anchor`'s
    (plan step **X-f3c-2b-2a**).  Before appending a restatement, the door has
    to know what already stands so it can decline a submission that changes
    nothing (ruling **R-EQ**'s rule, one table over) -- and
    ``account_service.create_account`` reaches the same writer with an account
    that by construction carries none.  "This account has no opening yet" is an
    honest answer to a writer where it is a broken invariant to a reader, which
    is why the two policies are two functions over ONE query rather than one
    function with a flag.

    **The order is ``id`` DESC alone, and the term it LOST is the interesting
    half.**  The positional read plan step X-f3c-2a deleted
    (``is_opening = index == 0``) ordered by ``observed_on``, a business date
    any owner may back-date, so an ordinary act silently re-elected the
    opening.  Its replacement led on ``created_at``, justified as "set by the
    database on INSERT and therefore monotone in recording order" -- **which is
    false, and plan step X-f3c-2b-2a's review measured what it cost.**
    :class:`app.models.mixins.CreatedAtMixin` defaults the column to
    ``db.func.now()``, and PostgreSQL's ``now()`` is ``transaction_timestamp()``
    -- the instant the transaction BEGAN.  Two restatements from two tabs can
    therefore commit in the opposite order to their instants, and the second
    one's row sorts BELOW the row it was meant to supersede: the owner is told
    "Books restated" and nothing moves.
    :func:`app.services.loan_anchor_service._governing_loan_anchor` already stated
    the ``now()`` fact for the loan twin; this door never carried it across.

    ``id`` is a sequence value allocated when the INSERT executes, and
    :func:`app.services.opening_service.stage_account_opening` runs under the
    owner's write lock across its compare-and-append wherever another
    transaction could reach the account (a signed-in request's transaction
    takes it before reading any of the owner's data, plan step
    ``balance:X-bn``; at registration the account is uncommitted and no
    other transaction can see it), so within one account
    the id order IS the order the owner made the statements.  The SQL tier
    orders identically from one stated constant
    (:data:`app.opening_infrastructure.GOVERNING_ORDER_SQL`), so the Python
    reader and the database constraint cannot disagree about which restatement
    is in force.

    Args:
        account_id: The account whose opening to load.

    Returns:
        The governing :class:`CashOpeningFact`, or ``None`` when the account
        carries no ``budget.account_openings`` row at all.
    """
    return _governing_openings((account_id,)).get(account_id)


def _governing_openings(
    account_ids: Collection[int],
) -> dict[int, CashOpeningFact]:
    """Return the governing opening of each of *account_ids* that carries one.

    The ONE load of the opening table, over one account's ids or every
    account one owner holds (ruling **plan#2623**: each loader of a fact kind
    takes one scope, as :mod:`._movement_scope` does for movements), so the
    per-account doors above and below and the owner's walks
    (:func:`~._walk.owner_cash_records`) read one statement of which row
    governs.  The rows arrive ``id`` DESC and the first per account is
    kept: the greatest id, :func:`governing_account_opening`'s order and its
    reason (an ``ORDER BY id DESC LIMIT 1`` over one account until plan task
    plan#2648).  One statement whatever the number of accounts.

    Args:
        account_ids: The accounts whose openings to load.

    Returns:
        ``{account id: its governing CashOpeningFact}``; an account carrying
        no row is absent, which each door answers by its own policy.
    """
    rows = (
        db.session.query(AccountOpening)
        .filter(AccountOpening.account_id.in_(account_ids))
        .order_by(AccountOpening.id.desc())
        .all()
    )
    governing: dict[int, AccountOpening] = {}
    for row in rows:
        governing.setdefault(row.account_id, row)
    return {
        row.account_id: CashOpeningFact(
            opening_id=row.id,
            account_id=row.account_id,
            opened_on=row.opened_on,
            opening_equity=Decimal(str(row.opening_equity)),
            source_id=row.source_id,
            # Normalised the same way ``cash_anchor_facts`` normalises an
            # assertion's instant: PostgreSQL hands back an aware value, and
            # ``utc_instant`` is where a naive one from a fixture is refused
            # rather than compared against an aware one further downstream.
            recorded_at=utc_instant(row.created_at),
        )
        for row in governing.values()
    }


def account_opening_fact(account_id: int) -> CashOpeningFact:
    """Return *account_id*'s GOVERNING opening-equity record.

    The level a cash fold starts from (plan step **X-f3c-2a**).  The table is
    append-only, so an account may carry several rows -- each a restatement of
    what its books opened with -- and the one with the greatest ``id`` governs.
    Which row that is comes from :func:`governing_account_opening`, whose
    docstring states why the order lost its ``created_at`` term at plan step
    **X-f3c-2b-2a**; this adds the READER's policy for an account that carries
    none, and nothing else.

    **It RAISES on an account with no row, and that is reachable only through a
    broken invariant.**  Every account gets one at creation
    (``account_service.create_account``) and migration ``a7c41f9d2b60``
    backfilled every account that predated the table -- including the two
    amortizing loans, because ``balance_at.balance_at`` falls through to this
    fold for an amortizing account carrying no ``LoanParams``.  Answering a
    missing row with ``Decimal("0.00")`` was the alternative and it is exactly
    the fabrication that step exists to delete: it would silently move every
    balance on the account to a level nothing recorded.  The same fail-loud
    placement :func:`~._facts.resolve_anchor` documents for the assertion half.

    Args:
        account_id: The account whose opening to load.

    Returns:
        The account's governing :class:`CashOpeningFact`.

    Raises:
        RuntimeError: When the account carries no ``AccountOpening`` row --
            a broken invariant, not an empty state.
    """
    return _required_openings((account_id,))[account_id]


def _required_openings(
    account_ids: Collection[int],
) -> dict[int, CashOpeningFact]:
    """Return each of *account_ids*' governing opening, refusing a missing one.

    :func:`account_opening_fact`'s READER policy over the one load
    (:func:`_governing_openings`), for one account or every account one owner
    holds (:func:`~._walk.owner_cash_records`), so the refusal and its message
    are stated once for both scopes.

    Args:
        account_ids: The accounts whose openings to load.

    Returns:
        ``{account id: its governing CashOpeningFact}``, one per account.

    Raises:
        RuntimeError: When an account carries no ``AccountOpening`` row --
            the first such account in *account_ids*' order is named.
    """
    openings = _governing_openings(account_ids)
    for account_id in account_ids:
        if account_id not in openings:
            raise RuntimeError(
                f"account_opening_fact: account id={account_id} has zero "
                "AccountOpening rows.  Every account carries one -- "
                "account_service.create_account writes it and migration "
                "a7c41f9d2b60 backfilled every account that predated the "
                "table -- so investigate any code path that constructed the "
                "Account row without routing through the canonical factory.  "
                "A balance cannot be folded without the level it starts from, "
                "and answering 0.00 would move every figure on this account "
                "silently."
            )
    return openings
