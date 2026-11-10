"""
Shekel Budget App -- the stored ledger's chart rows, as the store's own tests read them.

What a test still reads of ``budget.ledger_accounts``: the model and migration
tests under ``tests/test_models/`` that grade the store's schema, which outlive
its writers (plan task plan#3133) until plan step plan#2915 drops the tables.
The store's figure readers and journal filters that stood here went once no
test called them (plan task plan#3084), so nothing here is a second home of a
figure, and this module goes with the store.

Underscore-prefixed module name keeps pytest from collecting it.
"""

from app import ref_cache
from app.extensions import db
from app.models.ledger_account import LedgerAccount


def ledger_accounts_for_account(db_session, account_id):
    """Return every ``LedgerAccount`` linked to *account_id*.

    Shared by the ledger-account backfill suite's cases so the one-line
    lookup is not re-inlined per case (a duplicate-code finding).

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
    safe: an account holds at most one row of a kind.  It found the linked row
    ``account_service.create_account`` paired until plan task plan#3133
    deleted that call; it plants that row now.

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

    The kind-scoped lookup the true-up counter migration's cases read an
    account's anchor-equity row by.  Since Step 5's ``(account_id, kind_id)``
    re-key an account can carry TWO rows sharing its ``account_id`` (the
    ``linked`` row plus its ``anchor_equity`` equity twin), so any "the
    account's ledger account" lookup must say WHICH kind -- a bare ``[0]`` on
    :func:`ledger_accounts_for_account` is insertion-order-dependent.

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
