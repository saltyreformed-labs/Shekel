"""
Shekel Budget App -- the stored ledger, as the tests that grade it read it.

What a test still reads of ``budget.journal_entries``, ``budget.account_postings``
and ``budget.ledger_accounts``, by plan step plan#2913 once every assertion of a
figure had moved onto ``tests/_ledger_reads.py``: most moved here from
``tests/_test_helpers.py``, and ``stored_account_net``, ``stored_per_loan_net``,
``stored_trial_balance`` and ``entries_violating_balance`` were written here from
what the package helpers held.  **The callers are the store's own machinery**: the
shape or count of a journal entry or a reversal, a re-sync, the deploy resync,
the balanced-write refusals, chart row creation, the parity tests, and a state
only the store can hold (a forged or cleared entry).  So nothing here is a
second home of a figure, and this module goes with the store.

Underscore-prefixed module name keeps pytest from collecting it.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.services.ledger_lines import ChartKey
from tests._ledger_reads import (
    family_movement_ids,
    transfer_family_movement_ids,
    transfer_side_movement_ids,
)


def ledger_accounts_for_account(db_session, account_id):
    """Return every ``LedgerAccount`` linked to *account_id*.

    Shared by the ledger-account model / service / backfill suites so the
    one-line lookup is not re-inlined per file (a duplicate-code finding).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id whose linked ledger accounts to
            fetch.

    Returns:
        list[:class:`~app.models.ledger_account.LedgerAccount`] -- every
        row carrying this ``account_id``.  Since Step 5's
        ``uq_ledger_accounts_account_kind`` re-key, TWO rows are normal
        once an account has anchor corrections: the ``linked`` row plus
        its ``anchor_equity`` twin (one per account-linked kind).  Callers
        asserting counts or shapes must say WHICH kinds they expect.
    """

    return (
        db_session.query(LedgerAccount)
        .filter_by(account_id=account_id)
        .all()
    )


def chart_row_or_plant(db_session, account, kind, class_name, name=None):
    """Return the id of *account*'s chart row of *kind*, planting it by SQL when absent.

    For the store's model and migration tests, which outlive the writers that
    minted these rows and plant what their cases read, as a migration test
    plants its revision's rows.  The shape every per-account row shares
    (``account_id`` set, no category, no loan, not an owner bucket) under its
    natural key ``uq_ledger_accounts_account_kind``, so a get-or-plant is
    safe: an account holds at most one row of a kind.  It finds the linked row
    ``account_service.create_account`` still pairs until plan task plan#3133
    deletes that call, and plants it after.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` the row belongs to.
        kind: The ``ref.ledger_account_kinds`` name, e.g. ``"linked"``.
        class_name: The ``ref.ledger_account_classes`` name, e.g. ``"Asset"``.
        name: The display name the row snapshots, or ``None`` (a linked row
            renders its account's name).

    Returns:
        The row's id.
    """
    existing = db_session.execute(db.text(
        "SELECT la.id FROM budget.ledger_accounts la "
        "JOIN ref.ledger_account_kinds k ON k.id = la.kind_id "
        "WHERE la.account_id = :a AND k.name = :kind"
    ), {"a": account.id, "kind": kind}).scalar()
    if existing is not None:
        return existing
    return db_session.execute(db.text(
        "INSERT INTO budget.ledger_accounts "
        "(user_id, class_id, kind_id, account_id, is_owner_bucket, name) "
        "SELECT :u, c.id, k.id, :a, FALSE, :name "
        "FROM ref.ledger_account_classes c, ref.ledger_account_kinds k "
        "WHERE c.name = :class AND k.name = :kind RETURNING id"
    ), {
        "u": account.user_id, "a": account.id, "class": class_name,
        "kind": kind, "name": name,
    }).scalar_one()


def ledger_account_of_kind(db_session, account_id, kind_enum):
    """Return an account's ledger row of a given kind (linked / anchor_equity), or None.

    The shared kind-scoped lookup behind :func:`linked_ledger_account` and the
    account-anchor suites' anchor-equity lookups.  Since Step 5's
    ``(account_id, kind_id)`` re-key an account can carry TWO rows sharing its
    ``account_id`` (the ``linked`` row plus its ``anchor_equity`` equity twin),
    so any "the account's ledger account" lookup must say WHICH kind -- a bare
    ``[0]`` on :func:`ledger_accounts_for_account` is insertion-order-dependent.
    Holding the ``(account_id, kind_id)`` query in one place keeps the several
    kind-scoped lookups the posting suites use from drifting (a duplicate-code
    finding otherwise).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id.
        kind_enum: The :class:`~app.enums.LedgerAccountKindEnum` member to
            resolve (e.g. ``LINKED`` or ``ANCHOR_EQUITY``).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` of that kind for
        the account, or ``None`` when the account has no such row yet.
    """
    return (
        db_session.query(LedgerAccount)
        .filter_by(
            account_id=account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind_enum),
        )
        .one_or_none()
    )


def transit_ledger_account(db_session, user_id):
    """Return the owner's Transfers-in-transit ledger row, or ``None``.

    A LOOKUP, never a create -- the writer mints the ``transit`` owner bucket
    on the owner's first settled transfer
    (``ledger_account_service.get_or_create_transit_ledger_account``, plan
    step ``balance:X-bi-6-3``, ruling **R-BAL99**), so a suite that finds
    none has caught the writer not booking against it.  Keyed exactly as
    ``uq_ledger_accounts_owner_bucket`` is: the owner, the ``transit`` kind
    and the flag; the class is a function of the kind for a bucket.

    Args:
        db_session: The test ``db.session``.
        user_id: The owner's id.

    Returns:
        The ``transit`` :class:`~app.models.ledger_account.LedgerAccount`, or
        ``None`` when no transfer of the owner's has posted yet.
    """
    return (
        db_session.query(LedgerAccount)
        .filter_by(
            user_id=user_id,
            kind_id=ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.TRANSIT,
            ),
            is_owner_bucket=True,
        )
        .one_or_none()
    )


def stored_chart_key(ledger_account):
    """Return the ``ChartKey`` a STORED chart row stands for, read off its columns.

    The bridge the worked-out ledger's proof maps by (plan#2513: plan#2523
    builds it, the parity test plan#2527 and the production run plan#2528 map
    each worked-out line onto its stored row with it).  It reads the row AS
    STORED -- its ``kind_id``, its ``class_id``, and the one link column its
    shape sets -- and deliberately re-derives nothing: a stored class the
    worked-out rule would not give (a stale snapshot) must surface as a key
    that differs, never be repaired by the mapping.  The record is whichever of
    ``account_id`` / ``category_id`` / ``loan_account_id`` is set; the shape
    CHECKs allow at most one, and an owner bucket sets none.

    A row no worked-out key can name maps to a key no constructor builds: an
    ``orphan``, or a ``category`` row whose category was deleted (its
    ``category_id`` SET NULL, its kind unchanged), whose ``record_id`` is
    ``None``.  The records book those lines on the owner's Uncategorized bucket
    instead, so a nonzero stored net on one is a difference the proof names.

    Args:
        ledger_account: The stored
            :class:`~app.models.ledger_account.LedgerAccount`.

    Returns:
        The :class:`~app.services.ledger_lines.ChartKey` its columns spell.
    """
    kinds = {
        ref_cache.ledger_account_kind_id(member): member
        for member in LedgerAccountKindEnum
    }
    classes = {
        ref_cache.ledger_account_class_id(member): member
        for member in LedgerAccountClassEnum
    }
    # At most one is set: ``ck_ledger_accounts_account_or_category_null`` and
    # ``ck_ledger_accounts_loan_shape`` hold the row to it.
    links = [
        link for link in (
            ledger_account.account_id,
            ledger_account.category_id,
            ledger_account.loan_account_id,
        )
        if link is not None
    ]
    return ChartKey(
        kinds[ledger_account.kind_id],
        classes[ledger_account.class_id],
        links[0] if links else None,
    )


def linked_ledger_account(db_session, account_id):
    """Return the account's LINKED ledger row, never its anchor-equity twin.

    Since Step 5's ``(account_id, kind_id)`` re-key an account with posted
    anchor corrections carries TWO ledger rows sharing its ``account_id``
    (the ``linked`` row plus the ``anchor_equity`` equity twin), so any
    helper that grabs "the account's ledger account" must say WHICH kind --
    a bare ``[0]`` on :func:`ledger_accounts_for_account` is
    insertion-order-dependent.  This is the shared linked-kind lookup the
    posting suites key their cash-leg assertions off (the ``LINKED`` case of
    :func:`ledger_account_of_kind`).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id.

    Returns:
        The LINKED :class:`~app.models.ledger_account.LedgerAccount`, or
        ``None`` when the account has no pairing yet.
    """
    return ledger_account_of_kind(
        db_session, account_id, LedgerAccountKindEnum.LINKED,
    )


def stored_ledger_net(db_session, ledger_account_id, scenario_id):
    """Return the net of every posting leg on a ledger account in a scenario.

    Sums ``budget.account_postings.amount`` over *ledger_account_id* for journal
    entries in *scenario_id*.

    Args:
        db_session: The test ``db.session``.
        ledger_account_id: The ledger account whose legs to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0")`` when none posted).
    """
    return (
        db_session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def stored_account_net(account_id, scenario_id):
    """Return the net of an account's LINKED row's legs in a scenario.

    Keyed off the real account (``ledger_accounts.account_id``) and filtered to
    the LINKED kind: an anchor correction lands ``+delta`` on the linked row and
    ``-delta`` on the ``anchor_equity`` twin, which shares the ``account_id``
    column, so a bare ``account_id`` sum would cancel the correction pairwise.

    Args:
        account_id: The real account's id.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0")`` when none posted).
    """
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.account_id == account_id,
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def stored_per_loan_net(loan_account_id, scenario_id):
    """Return the net of a loan's per-loan chart rows' legs in a scenario.

    Keyed on ``ledger_accounts.loan_account_id``: the loan's interest, escrow,
    refund and opening-equity rows, which carry ``account_id IS NULL``.

    Args:
        loan_account_id: The loan's account id.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0")`` when none posted).
    """
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.loan_account_id == loan_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def stored_trial_balance():
    """Return ``SUM(account_postings.amount)`` over the whole stored ledger."""
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .scalar()
    )


def entries_violating_balance():
    """Return ``(entry_id, leg_sum, leg_count)`` for every malformed stored entry.

    A well-formed double-entry has ``leg_sum == 0`` and ``leg_count >= 2``.  Any
    row returned here is a violation -- the per-entry invariant the deferred
    trigger also enforces, re-checked from the ORM side.

    Returns:
        The violating entries, empty for a well-formed journal.
    """
    rows = (
        db.session.query(
            Posting.journal_entry_id,
            db.func.sum(Posting.amount),
            db.func.count(Posting.id),
        )
        .group_by(Posting.journal_entry_id)
        .all()
    )
    return [
        (entry_id, leg_sum, leg_count)
        for entry_id, leg_sum, leg_count in rows
        if leg_sum != 0 or leg_count < 2
    ]


def find_loan_ledger_account(db_session, loan_account_id, kind):
    """Return the per-loan ledger account of *kind*, or None if not created.

    The per-loan interest / escrow / refund ledger account
    (``loan_account_id`` + the ``kind`` discriminator), lazily minted by the
    Step-4 chart resolver on first use.  Shared by the Step-4 split-service and
    wiring suites.

    Args:
        db_session: The test ``db.session``.
        loan_account_id: The loan whose per-loan ledger account to find.
        kind: The :class:`~app.enums.LedgerAccountKindEnum` member
            (``LOAN_INTEREST`` / ``LOAN_ESCROW`` / ``LOAN_REFUND``).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount`, or ``None`` when
        the resolver has not minted it yet.
    """
    return (
        db_session.query(LedgerAccount)
        .filter_by(
            loan_account_id=loan_account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind),
        )
        .one_or_none()
    )


def family_journal_filter(txn):
    """Return the SQL clause selecting the journal entries of *txn*'s FAMILY.

    **A settled bill's money is posted under its covering movement since plan
    step X-bi-3a** (ruling **R-BAL39**): the status seam mirrors the
    settlement as one ``transaction_entries`` row, the bill's own leg reads
    zero, and the posting writer files the money under
    ``journal_entries.transaction_entry_id`` (the purchase source) rather than
    under ``transaction_id``, a link plan step ``balance:X-bi-6-5``'s second
    leaf (card plan#1845) deleted with the row's own ``transaction`` leg.  A
    suite that read "the row's postings" by ``JournalEntry.transaction_id``
    alone read an empty ledger for a covered bill.  This is the ONE spelling
    of the family read, so the 38 cases the developer confirmed on 2026-09-15
    widen their subject the same way and no figure moves: the row's
    movements' entries.

    The movements are read through ``Transaction.entries`` -- EVERY movement
    of the row since plan step ``balance:X-bi-4a`` (ruling **R-BAL80**: the
    fold and the writer read a row's money as its movements alone, a dated
    purchase's leg as much as the covering movement's; through ``X-bi-3e``
    this read the covering movements only, while the row's own leg carried
    an envelope's un-dated purchases).  A revert KEEPS the mirror, un-dated
    (plan step **X-bi-3e-2**, ruling **R-BAL61**), so a reverted row's family
    includes its survivor's linked postings -- a net-zero pair once the
    door's reconcile has reversed them -- where through 3e-1 the mirror was
    deleted and the pair stood unlinked.  Every reader of this filter sums a
    net or scopes by period, so the pair changes no figure.

    Args:
        txn: The :class:`~app.models.transaction.Transaction`, or its id.

    Returns:
        A SQLAlchemy boolean clause over ``JournalEntry``.
    """
    return JournalEntry.transaction_entry_id.in_(family_movement_ids(txn))


def transfer_family_journal_filter(transfer_id):
    """Return the SQL clause selecting the journal entries of a TRANSFER's family.

    **A settled transfer's money is posted under its two covering movements
    since plan step ``balance:X-bi-6-3``** (rulings **R-BAL45** and
    **R-BAL101**): one entry per side, linked by
    ``journal_entries.transaction_entry_id`` to the movement covering that
    side (the side's record; it hung off the side's shadow until the
    re-parent below), each against the owner's Transfers-in-transit account.  A
    suite read "the transfer's postings" by ``JournalEntry.transfer_id`` until
    plan step ``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted that
    link with the LEGACY one-entry shape it named, which no go-forward settle
    wrote.  This is the ONE spelling of the transfer family read -- the
    records of BOTH sides of the transfer -- so the cases the developer
    confirmed when the shape moved widen their subject the same way and no
    figure per real account moves.

    **Independent SQL over the side links** (plan step
    ``balance:X-bi-6-4d-2``, ruling **R-BAL167** class 4): a record hangs off
    the TRANSFER by ``expense_transfer_id`` / ``income_transfer_id`` since
    that step, and this reads those columns itself rather than through
    ``transfer_legs``, the producer the suite grades -- a helper selecting
    money through the producer under test cannot catch that producer's
    fault.  Until then it read the movements under every shadow of the
    transfer; a record keeps its id across the re-parent, so a journal entry
    that linked it still does.

    Args:
        transfer_id: The ``budget.transfers`` id.

    Returns:
        A SQLAlchemy boolean clause over ``JournalEntry``.
    """
    return JournalEntry.transaction_entry_id.in_(transfer_family_movement_ids(transfer_id))


def transfer_side_journal_filter(transfer_id, account_id):
    """Return the SQL clause selecting ONE side's entries of a transfer's family.

    The entries linked to the record of the transfer's side on *account_id*
    -- the loan-side cash entry of a loan payment, or either side of a cash
    transfer -- since plan step ``balance:X-bi-6-3`` (a side is an entry of
    its own, rulings **R-BAL45** and **R-BAL101**).  The per-side twin of
    :func:`transfer_family_journal_filter`, and independent SQL over the side
    links for its reason: a side's record is on its endpoint (the side keys),
    so the side on *account_id* is the linked record on that account.

    Args:
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on.

    Returns:
        A SQLAlchemy boolean clause over ``JournalEntry``.
    """
    return JournalEntry.transaction_entry_id.in_(transfer_side_movement_ids(transfer_id, account_id))
