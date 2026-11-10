"""Which account at a SOURCE is which account here -- the whole of that fact.

Ruling **R-FP**: the source-account mapping "is a fact, not a guess".  This
module is its entire life, and gathering the three moments in one place is what
plan step ``bank_import:X-f6a-4`` added: it is LEARNED from a first import,
CHECKED against every import after, and FORGOTTEN with the last import that
could have taught it.  The first two lived in the write door and the third was
written in the undo door beside a byte-identical lookup, which pylint's
cross-file ``duplicate-code`` reported and was right to.

**The forgetting is what makes a wrongly-recorded pairing repairable**, which
is the second live shape of finding **N-302**.  The pairing refuses every later
file that names a different account -- correct, and until X-f6a-4 unrepairable:
an owner who chose the wrong Shekel account on a first import could never
import that account's statements again, because nothing in ``app/`` deleted an
``account_external_identities`` row.

**A pairing has one of two provenances since plan step ``bank_import:X-f6b-2``
(ruling **R-BI26**), and only one of them is forgotten here.**  A pairing
LEARNED from a file is the shape above.  A pairing DECLARED on the bank feed
panel -- the owner saying which Bridge account is which account here, before
any import exists -- carries the feed's id, and its life is the FEED's: the
cascade on ``fk_account_external_identities_feed_owner`` ends it with the
disconnect, and :func:`forget_identity_if_last` leaves it alone, because the
imports a feed records did not teach it.  At this commit the schema and this
module's rule exist and nothing declares a row yet: the claim door and the
mapping form are the next commit of this leaf, and they write through this
module's one writer, :func:`record_identity`, which takes the feed as a
parameter so the row's provenance is written by the door that knows it.

Services-boundary discipline: plain data in, no Flask import.  Nothing here
commits.
"""

from __future__ import annotations

import logging

from app.extensions import db
from app.models.statement_import import AccountExternalIdentity, StatementImport
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_IDENTITY_RECORDED,
    log_event,
)

from ._errors import StatementAccountMismatch

_logger = logging.getLogger(__name__)


def _recorded_for(
    account_id: int, source_id: int,
) -> "AccountExternalIdentity | None":
    """Return *account_id*'s recorded pairing at *source_id*, or ``None``.

    The ONE spelling of "what does this source call this account", read by the
    check that refuses a mismatched file and by the forgetting that ends the
    pairing's life.  Two spellings is how one of them comes to be scoped
    differently from the other.

    Args:
        account_id: The Shekel account.
        source_id: The adapter.

    Returns:
        The :class:`~app.models.statement_import.AccountExternalIdentity`, or
        ``None`` when none is recorded.
    """
    return (
        db.session.query(AccountExternalIdentity)
        .filter(
            AccountExternalIdentity.account_id == account_id,
            AccountExternalIdentity.source_id == source_id,
        )
        .one_or_none()
    )


def verify_identity(
    account_id: int, user_id: int, source_id: int, external_account_id: str,
) -> bool:
    """Check *account_id*'s identity at *source_id* against the file's.

    Ruling **R-FP**: the source-account mapping is a FACT, not a guess.  The
    user states which account a file is for; the FILE states which account it
    is for; and this is where the two are held together.

    **It reads and raises; it writes nothing.**  Recording is
    :func:`record_identity`, and the split is not tidiness: the write door's
    whole claim is that it refuses BEFORE it stages anything, and an ``add``
    here would be autoflushed by the very next query -- ahead of the last
    refusal the door can still raise.  A claim that a refusal "leaves the
    database exactly as it was without depending on the rollback" has to be
    true of the ordering, not just of the outcome.

    **It checks in BOTH directions**, and the second one is the arm that
    matters.  Comparing only against this account's own recorded identity would
    let a file already claimed by ANOTHER of the owner's accounts be imported
    here as a first import -- so one bank statement would be recorded twice,
    under two accounts, and the second account's balance would later be
    reconciled against the wrong bank.

    **Both lookups are scoped to the OWNER.**  A global search would make one
    user's masked account number ("******3820", a 10,000-value space) collide
    with another user's, permanently locking the loser out of importing their
    own statements -- and the refusal would disclose that some other account in
    the system held that number, which is the existence oracle the project's
    404-for-both rule exists to prevent.

    Args:
        account_id: The account the user chose.
        user_id: Its owner.
        source_id: The ``ref.statement_sources`` row the file was read by.
        external_account_id: What the file calls its account.

    Returns:
        True when the mapping still has to be RECORDED (a first import), False
        when it already exists and agrees.

    Raises:
        StatementAccountMismatch: When the file names a different account than
            this one has been imported from, or one another of the owner's
            accounts already claims.
    """
    recorded = _recorded_for(account_id, source_id)
    if recorded is not None:
        if recorded.external_account_id != external_account_id:
            raise StatementAccountMismatch(
                recorded.external_account_id, external_account_id,
            )
        return False

    claimed_elsewhere = (
        db.session.query(AccountExternalIdentity)
        .filter(
            AccountExternalIdentity.user_id == user_id,
            AccountExternalIdentity.source_id == source_id,
            AccountExternalIdentity.external_account_id
            == external_account_id,
        )
        .one_or_none()
    )
    if claimed_elsewhere is not None:
        raise StatementAccountMismatch(
            "another of your own accounts, which has already imported it",
            external_account_id,
            claimed_elsewhere=True,
        )
    return True


def record_identity(
    account_id: int, user_id: int, source_id: int, external_account_id: str,
    *, feed_id: "int | None" = None,
) -> AccountExternalIdentity:
    """Record what *source_id* calls *account_id*.

    One row shape for two provenances (ruling **R-BI26**): a first import
    records a pairing it LEARNED from the file, with no feed -- the one
    caller at this commit, ``_record.py`` -- and the bank feed's mapping
    form (the next commit of this leaf) records one the owner DECLARED,
    naming the feed it was declared under.  The row's provenance is written
    here because this is the one writer, and a second
    ``AccountExternalIdentity(...)`` in that door's service would be a second
    spelling of the same insert.

    Args:
        account_id: The account the user chose.
        user_id: Its owner, held equal to the account's by
            ``fk_account_external_identities_owner`` -- and to the feed's by
            ``fk_account_external_identities_feed_owner`` when one is named.
        source_id: The adapter the file was read by, or the feed's source.
        external_account_id: What the source calls its account.
        feed_id: The owner's ``budget.bank_feeds`` row for a DECLARED
            pairing; ``None`` (the default) for one learned from a file.

    Returns:
        The staged row, so a caller that reports the act can name it.
    """
    identity = AccountExternalIdentity(
        account_id=account_id,
        user_id=user_id,
        source_id=source_id,
        external_account_id=external_account_id,
        feed_id=feed_id,
    )
    db.session.add(identity)
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_IDENTITY_RECORDED, BUSINESS,
        "Recorded which account a statement source calls this account.",
        account_id=account_id, source_id=source_id,
        declared=feed_id is not None,
    )
    return identity


def forget_identity_if_last(account_id: int, source_id: int) -> bool:
    """Drop the pairing when no import from *source_id* survives.

    **The pairing is a fact LEARNED from an import, so it does not outlive
    every import that taught it** (developer ruling 2026-08-20).  Undoing every
    import returns the account to its never-imported state, and the next import
    learns the pairing afresh -- which is what makes a wrongly-recorded pairing
    repairable rather than permanent (finding **N-302**).

    **Rejected: a separate "forget this pairing" control.**  That is the
    stricter option -- the wrong-file guard would then survive a
    delete-and-redo -- and it was rejected because it adds a second door and a
    state nothing else in the app produces: a pairing with no import behind it.
    The guard is only weakened after the owner has deleted every import from
    that source, which is the moment they have said "forget what I imported".

    **A DECLARED pairing is never forgotten here** (ruling **R-BI26**, which
    amends R-GB to the learned rows).  The owner stated it on the feed panel
    before any import existed, so the imports that later arrived under it did
    not teach it and deleting them says nothing about it; its life is the
    feed's, ended by the disconnect's cascade.  Without this arm, deleting
    the last feed import of an account would silently unmap it, and the
    nightly sync (a later leaf of X-f6b-2) would skip the account with no
    screen saying why.  The arm reads the row's own ``feed_id``, not the
    source: a predicate on the source would be a per-source constant stated
    in a door.

    Args:
        account_id: The account whose imports were being undone.
        source_id: The adapter whose pairing to reconsider.

    Returns:
        Whether a pairing was dropped.
    """
    survivor = (
        db.session.query(StatementImport.id)
        .filter(
            StatementImport.account_id == account_id,
            StatementImport.source_id == source_id,
        )
        .first()
    )
    if survivor is not None:
        return False
    identity = _recorded_for(account_id, source_id)
    if identity is None or identity.feed_id is not None:
        return False
    db.session.delete(identity)
    return True
