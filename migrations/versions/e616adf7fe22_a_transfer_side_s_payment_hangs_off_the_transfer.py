"""a transfer side's payment hangs off the transfer

Revision ID: e616adf7fe22
Revises: 1f431fec6547
Create Date: 2026-10-08
Review: developer, 2026-09-20 / 2026-09-22 / 2026-09-30 (ruling R-BAL88:
``transaction_entries.transaction_id`` made nullable under an exactly-one-parent
CHECK, and the two side keys; R-BAL107: the transfer owner keys; R-BAL168: the
side keys ON UPDATE CASCADE; R-CC89 / R-CC92 carried to a transfer: the
deleted-row triggers re-created with their transfer arm)

Plan step **balance:X-bi-6-4d-2** of ``docs/audits/balance_architecture/README.md``
(design ``X-bi-6-4d`` D1).  **A transfer side's payment record is filed under
the TRANSFER, not under the hidden twin row the transfer service keeps for that
side.**  Until now each side's record hung off its twin
(``transaction_entries.transaction_id`` = the twin's id), so every reader of a
transfer's money walked a row whose only job was to copy the transfer.  After
this revision every STORED record names its transfer by one of two SIDE LINKS,
and no twin holds one.  **No figure, day, account or statement link moves**:
every record keeps its id, amount, day, basis, account, matches and postings,
and only the column that says what it is filed under changes.

  1. **``budget.transfers``** gains the superkeys ``uq_transfers_id_from_account``
     / ``uq_transfers_id_to_account`` and the owner keys
     ``fk_transfers_owner_from_account`` / ``fk_transfers_owner_to_account``
     onto ``uq_accounts_id_user`` (ruling **R-BAL107**: a transfer naming another
     user's account is unstorable).
  2. **``budget.transaction_entries``** gains ``expense_transfer_id`` and
     ``income_transfer_id``, each keyed WITH ``account_id`` onto its side's
     superkey (ruling **R-BAL88**), so a side's record on any account but that
     side's endpoint is unstorable.  Both keys are ``ON UPDATE CASCADE`` (ruling
     **R-BAL168**: an endpoint move carries the side's record) and NO ACTION on
     delete (**R-CC54**).  ``transaction_id`` becomes NULLABLE under
     ``ck_transaction_entries_one_parent`` (exactly one of the three is set);
     ``ck_transaction_entries_side_link_is_a_record`` makes every side link a
     settlement record; ``uq_transaction_entries_one_expense_side_record`` /
     ``..._one_income_side_record`` allow one per side.  Every new constraint
     holds on the stored rows before the backfill (every link is NULL), so the
     DDL runs first and the backfill is graded row by row as it writes.
  3. **The backfill**: every movement under a twin takes that twin's transfer
     as its side link -- the INCOME twin is the to-side, the expense twin the
     from-side, which is how the fold and the ledger have always read a twin --
     and its ``transaction_id`` goes NULL, in ONE statement.
  4. **The deleted-row rule gains its transfer arm**
     (:mod:`app.deleted_row_infrastructure`, ``TRANSFER_ARM``): a payment may
     not arrive under a deleted transfer, read under the same row lock (ruling
     **R-CC96**), and a transfer may not be hidden while a side links one --
     and the row arm loses R-CC92's carve-out for a transfer's leg, which
     holds nothing now.
  5. **The band rule** (:mod:`app.side_band_infrastructure`, design D8): a
     side's record is dated exactly while its transfer is settled, and a
     settled transfer records both sides or, closed at zero, neither -- a
     deferred constraint trigger, installed LAST.

**The upgrade REFUSES, writing nothing, while any stored row could not take its
link**, each named with the query that finds it (fail-closed, the shape
``c4a4e7d1b9f2`` uses for ruling **R-CC82**): a movement under a twin that is
not a settlement record, not on its side's endpoint, or under a deleted twin or
a deleted transfer (the last two would be a hidden parent holding money, the
state the transfer arm forbids); two records on one side of one transfer; a
transfer naming an account its owner does not hold.  After the backfill, and
before either rule is installed, it refuses, naming each the same way, while a
transfer's records break the band rule (step 5), and while a twin does not
say what its transfer and its side's record say (:func:`refuse_drifted_twins`,
the downgrade's own census, asked once the side links exist and before the
app at this revision has written anything).  These two follow the DDL and the
backfill, so a refusal keeps nothing: the revision rolls back whole (save the
audit log's id sequence, which no rollback restores).  Below this revision
the app writes a twin's status, settle day, day basis and statement link with
its transfer and its record, so such a twin is a drift no door wrote; from
this revision the app
writes those facts on the transfer and the record alone, after which the
drift could not be told from the app's own, and revision
``82f7b7ed4332`` deletes the twin.  Every count is 0 where
the transfer service wrote the rows.

**Measured on a production dump** (at ``1f431fec6547``; the figures stay in
the lane's handoff record, ruling **R-BAL132**): every movement under a twin
is a dated settlement record on its side's endpoint, under a live twin of a
live Paid transfer, the two sides equal in number; no transfer names another
owner's account; the statement match members naming one of them key by the
movement's id and account, which do not change.  None refused.  The migration
prints its own counts so the operator compares them with the rehearsal's.

**The downgrade re-attaches each record to its side's LIVE twin** (a transfer
has at most one per side, ``uq_transactions_transfer_type_active``) and
refuses, writing nothing, while a record's side has none: a twin hidden around
the transfer service (Transfer Invariant 4 drift, which no door writes), or no
twin at all (a state only a LATER revision that deletes the twins can leave,
and that revision's downgrade rebuilds them first).  A deleted twin cannot take
the record back -- the row arm the downgrade restores refuses a movement
arriving under a deleted row -- and a record under a hidden TRANSFER cannot
exist at this revision, which the transfer arm refuses.  Then it drops the new
constraints and columns and restores ``transaction_id``'s NOT NULL, refusing if
any row would violate it.

**What the downgrade does NOT restore, and the refusal that makes that safe.**
From this revision the app no longer writes a twin's status, settle day, day
basis or statement link: the status seam's Transfer arm writes each side's
record alone, and a twin keeps only its mirrored period, scenario, category,
due date, override and deleted flags, ownership, account and name.  So after
any settle, revert, day or figure correction made at this revision, a twin's
status and day no longer say what its transfer and record say, and this
downgrade, which moves only the records' parent column, would leave a dated
record under a twin still saying Projected (or an un-dated one under a twin
saying Paid) -- what the code below this revision reads as a drifted pair.
**So the downgrade REFUSES, writing nothing, while any twin disagrees**
(:func:`refuse_drifted_twins`; the cp2b-1 review's L-4, fail-closed): this
revision is never downgraded alone on a database the app has written at it.  It
ships with revision ``82f7b7ed4332`` in ONE release, and
that revision's downgrade, which rebuilds the twins, rebuilds each twin's
status, settle day, basis and statement link from its transfer and its side's
record first, after which the census reads zero.  **The census reads what a
door below this revision wrote as agreeing**, so a downgrade straight after the
upgrade, with no app write between, passes: each twin's status is its
transfer's, and its day, basis and link are its side's record's (``NULL`` where
it holds none) -- save a ``$0.00`` close's twins, which that seam dated on the
day the close was stated with no record beside them, and which are read for
status alone (the cp2c review's M-1; the link too since ``X-bi-6-4d-2``'s cp5
review, L-3, as below).  On a production dump (holding deleted twins and
records, and no ``$0.00`` close) it reads zero at the upgrade, where it is
asked once the side links exist, and after it, and the downgrade through it is
digest-identical to the pristine restore.

**What a ``$0.00`` close's day becomes.**  Ruling **R-BAL230** gives a
``$0.00`` close no day at this revision ("a $0.00 close moved no money, so it
has no day"), and a side closed at ``$0.00`` holds no record, so its day lives
only on its twins: one closed below this revision keeps the day its twins
carry, and the downgrade leaves it there; one closed AT this revision has
none anywhere, so ``82f7b7ed4332``'s downgrade rebuilds its twins settled with
NO day -- the dateless settled pair the code below calls legacy (finding
N-181), declared here rather than restorable.  Revision ``82f7b7ed4332``,
which deletes the twins, owes the same declaration for the older closes' days.
No money moves in either: a ``$0.00`` close records none.  The stored
money is exact either way: no record, posting or match is written by the
downgrade but each record's parent link.

``tests/test_models/test_a_transfer_side_s_payment_hangs_off_the_transfer.py``
drives the shipped ``upgrade`` / ``downgrade`` and each refusal.
"""
from alembic import op
import sqlalchemy as sa

from app.deleted_row_infrastructure import (
    ROW_ARM,
    TRANSFER_ARM,
    apply_deleted_row_infrastructure,
)
from app.side_band_infrastructure import (
    BAND_ARM,
    apply_side_band_infrastructure,
    remove_side_band_infrastructure,
)


# revision identifiers, used by Alembic.
revision = "e616adf7fe22"
down_revision = "1f431fec6547"
branch_labels = None
depends_on = None


#: The INCOME transaction type's id, by name: a migration resolves a ref row by
#: name because its id differs between databases.  An income twin is the
#: to-side; every other twin is the from-side.
_INCOME = "(SELECT id FROM ref.transaction_types WHERE name = 'Income')"

#: Every movement under a twin that cannot take its side link as it stands,
#: with the reason: not a settlement record, not on its side's endpoint, or
#: under a deleted twin or a deleted transfer.
_UNLINKABLE_SQL = f"""
SELECT e.id, t.transfer_id,
       CASE
           WHEN NOT e.covers_settlement THEN 'not a settlement record'
           WHEN e.account_id <> CASE WHEN t.transaction_type_id = {_INCOME}
                                     THEN x.to_account_id
                                     ELSE x.from_account_id END
               THEN 'not on its side''s account'
           WHEN t.is_deleted THEN 'under a deleted twin'
           ELSE 'under a deleted transfer'
       END AS reason
  FROM budget.transaction_entries e
  JOIN budget.transactions t ON t.id = e.transaction_id
  JOIN budget.transfers x ON x.id = t.transfer_id
 WHERE NOT e.covers_settlement
    OR e.account_id <> CASE WHEN t.transaction_type_id = {_INCOME}
                            THEN x.to_account_id
                            ELSE x.from_account_id END
    OR t.is_deleted
    OR x.is_deleted
 ORDER BY e.id
"""

#: Every transfer side holding more than one movement across its twins.
_DOUBLED_SIDES_SQL = f"""
SELECT t.transfer_id, (t.transaction_type_id = {_INCOME}) AS is_income,
       count(*) AS movements
  FROM budget.transaction_entries e
  JOIN budget.transactions t ON t.id = e.transaction_id
 WHERE t.transfer_id IS NOT NULL
 GROUP BY 1, 2
HAVING count(*) > 1
 ORDER BY 1, 2
"""

#: Every transfer naming an account its owner does not hold (ruling R-BAL107).
_FOREIGN_ACCOUNT_SQL = """
SELECT x.id
  FROM budget.transfers x
  JOIN budget.accounts f ON f.id = x.from_account_id
  JOIN budget.accounts t ON t.id = x.to_account_id
 WHERE f.user_id <> x.user_id OR t.user_id <> x.user_id
 ORDER BY x.id
"""

#: How many movements hang off a twin, per side -- the backfill's census and,
#: after it, the count that must read zero.
_UNDER_TWINS_SQL = f"""
SELECT count(*) FILTER (WHERE t.transaction_type_id <> {_INCOME}),
       count(*) FILTER (WHERE t.transaction_type_id = {_INCOME})
  FROM budget.transaction_entries e
  JOIN budget.transactions t ON t.id = e.transaction_id
 WHERE t.transfer_id IS NOT NULL
"""

#: How many movements a side link names, per side.
_SIDE_LINKED_SQL = """
SELECT count(expense_transfer_id), count(income_transfer_id)
  FROM budget.transaction_entries
"""

#: The backfill, both sides in one statement.
_BACKFILL_SQL = f"""
UPDATE budget.transaction_entries e
   SET expense_transfer_id = CASE WHEN t.transaction_type_id = {_INCOME}
                                  THEN NULL ELSE t.transfer_id END,
       income_transfer_id = CASE WHEN t.transaction_type_id = {_INCOME}
                                 THEN t.transfer_id ELSE NULL END,
       transaction_id = NULL
  FROM budget.transactions t
 WHERE t.id = e.transaction_id
   AND t.transfer_id IS NOT NULL
"""

#: Every side-linked movement whose side has no LIVE twin to go back under: a
#: deleted twin cannot take it (the row arm refuses a movement arriving under
#: a deleted row).
_TWINLESS_SQL = f"""
SELECT e.id, coalesce(e.expense_transfer_id, e.income_transfer_id)
  FROM budget.transaction_entries e
 WHERE coalesce(e.expense_transfer_id, e.income_transfer_id) IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM budget.transactions t
        WHERE t.transfer_id = coalesce(e.expense_transfer_id,
                                       e.income_transfer_id)
          AND (t.transaction_type_id = {_INCOME})
              = (e.income_transfer_id IS NOT NULL)
          AND NOT t.is_deleted
   )
 ORDER BY e.id
"""

#: The downgrade's re-attach: each side-linked movement under its side's live
#: twin, of which ``uq_transactions_transfer_type_active`` allows one.
_REATTACH_SQL = f"""
UPDATE budget.transaction_entries e
   SET transaction_id = (
           SELECT t.id FROM budget.transactions t
            WHERE t.transfer_id = coalesce(e.expense_transfer_id,
                                           e.income_transfer_id)
              AND (t.transaction_type_id = {_INCOME})
                  = (e.income_transfer_id IS NOT NULL)
              AND NOT t.is_deleted
       ),
       expense_transfer_id = NULL,
       income_transfer_id = NULL
 WHERE coalesce(e.expense_transfer_id, e.income_transfer_id) IS NOT NULL
"""

#: Every twin whose status, settle day, day basis or statement link is not what
#: its transfer and its side's record say -- the four facts this revision's
#: app stops writing on a twin.  A LIVE twin is read against its side's record
#: (the one the downgrade re-attaches under it; ``NULL`` when the side holds
#: none) and a deleted twin against none, since a record goes back under its
#: side's live twin only.  **A twin of a SETTLED transfer whose side holds no
#: record at all is read for its status alone**: that is a ``$0.00`` close,
#: and the seam below this revision dated both twins of one on the day it was
#: stated while writing no record, so its day, basis and statement link are
#: the close's own, which nothing at this revision holds and which a
#: downgrade leaves on the twin where the code below reads them (a ``$0.00``
#: close made AT this revision leaves its twins Projected, which the status
#: reads).  Keyed on the SIDE holding no record, live or not, so a deleted
#: twin whose side does hold one is still read against none (L-3 of
#: ``X-bi-6-4d-2``'s cp5 review, built at ``X-bi-6-4d-2``).  ``(transfer id,
#: twin id, the facts that differ)``.
_DRIFTED_TWINS_SQL = f"""
SELECT transfer_id, twin_id, facts FROM (
SELECT t.transfer_id, t.id AS twin_id,
       array_remove(ARRAY[
           CASE WHEN t.status_id <> x.status_id THEN 'status' END,
           CASE WHEN NOT zero_close.is_one
                 AND t.settled_on IS DISTINCT FROM e.settled_on
                THEN 'settle day' END,
           CASE WHEN NOT zero_close.is_one
                 AND t.settled_day_basis_id
                     IS DISTINCT FROM e.settled_day_basis_id
                THEN 'day basis' END,
           CASE WHEN NOT zero_close.is_one
                 AND t.reconciled_by_id IS DISTINCT FROM e.reconciled_by_id
                THEN 'statement link' END
       ], NULL) AS facts
  FROM budget.transactions t
  JOIN budget.transfers x ON x.id = t.transfer_id
  JOIN ref.statuses s ON s.id = x.status_id
  LEFT JOIN budget.transaction_entries e
         ON NOT t.is_deleted
        AND CASE WHEN t.transaction_type_id = {_INCOME}
                 THEN e.income_transfer_id
                 ELSE e.expense_transfer_id END = t.transfer_id
  CROSS JOIN LATERAL (
        SELECT s.is_settled AND NOT EXISTS (
                 SELECT 1 FROM budget.transaction_entries r
                  WHERE CASE WHEN t.transaction_type_id = {_INCOME}
                             THEN r.income_transfer_id
                             ELSE r.expense_transfer_id END = t.transfer_id
               ) AS is_one
       ) zero_close
) twins
 WHERE cardinality(facts) > 0
 ORDER BY transfer_id, twin_id
"""

#: How many drifted twins a refusal names; the diagnostic query finds the rest.
_NAMED_AT_MOST = 20

#: The two side keys, as ``(name, link column, referenced endpoint column)``.
#: Every transfer whose side records break the band rule
#: (:mod:`app.side_band_infrastructure`): settled with an un-dated record or a
#: record on one side only, or not settled with a dated record.  Read AFTER
#: the backfill, over the side links; ``(transfer id, settled?, dated,
#: un-dated)``.
_OFF_BAND_SQL = """
SELECT x.id, s.is_settled,
       count(e.id) FILTER (WHERE e.settled_on IS NOT NULL) AS dated,
       count(e.id) FILTER (WHERE e.settled_on IS NULL) AS undated
FROM budget.transfers x
JOIN ref.statuses s ON s.id = x.status_id
LEFT JOIN budget.transaction_entries e
       ON e.expense_transfer_id = x.id OR e.income_transfer_id = x.id
GROUP BY x.id, s.is_settled
HAVING (s.is_settled
        AND (count(e.id) FILTER (WHERE e.settled_on IS NULL) > 0
             OR count(e.id) FILTER (WHERE e.settled_on IS NOT NULL)
                NOT IN (0, 2)))
    OR (NOT s.is_settled
        AND count(e.id) FILTER (WHERE e.settled_on IS NOT NULL) > 0)
ORDER BY x.id
"""

_SIDE_KEYS = (
    ("fk_transaction_entries_expense_side", "expense_transfer_id",
     "from_account_id"),
    ("fk_transaction_entries_income_side", "income_transfer_id",
     "to_account_id"),
)

#: The one-record-per-side indexes, as ``(name, link column)``.
_SIDE_INDEXES = (
    ("uq_transaction_entries_one_expense_side_record", "expense_transfer_id"),
    ("uq_transaction_entries_one_income_side_record", "income_transfer_id"),
)

#: The transfer table's superkeys and owner keys, as ``(endpoint column,
#: superkey name, owner key name)``.
_ENDPOINTS = (
    ("from_account_id", "uq_transfers_id_from_account",
     "fk_transfers_owner_from_account"),
    ("to_account_id", "uq_transfers_id_to_account",
     "fk_transfers_owner_to_account"),
)


def refuse_unlinkable_rows(bind) -> None:
    """Refuse the upgrade while any stored row could not take its side link.

    **Module-level so a test can DRIVE each refusal** (the chain's own pattern,
    ``c4a4e7d1b9f2.refuse_hidden_rows_holding_movements``).

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each offending movement, side or transfer and the
            query that finds it.  Nothing has been written.
    """
    unlinkable = [tuple(row) for row in bind.execute(sa.text(_UNLINKABLE_SQL))]
    doubled = [tuple(row) for row in bind.execute(sa.text(_DOUBLED_SIDES_SQL))]
    foreign = [row[0] for row in bind.execute(sa.text(_FOREIGN_ACCOUNT_SQL))]
    problems = []
    if unlinkable:
        problems.append(
            f"{len(unlinkable)} movement(s) under a twin cannot take a side "
            f"link (entry id, transfer id, reason: {unlinkable}; diagnose "
            f"with: {_UNLINKABLE_SQL.strip()})"
        )
    if doubled:
        problems.append(
            f"{len(doubled)} transfer side(s) hold more than one movement "
            f"(transfer id, is the to-side, count: {doubled}; diagnose with: "
            f"{_DOUBLED_SIDES_SQL.strip()})"
        )
    if foreign:
        problems.append(
            f"{len(foreign)} transfer(s) name an account their owner does not "
            f"hold (ids {foreign}; diagnose with: {_FOREIGN_ACCOUNT_SQL.strip()})"
        )
    if problems:
        raise RuntimeError(
            "X-bi-6-4d-2 refuses: " + "; ".join(problems) + ".  Nothing was "
            "written; each is the developer's to rule (rulings R-BAL88, "
            "R-BAL107, R-CC82)."
        )


def refuse_twinless_sides(bind) -> None:
    """Refuse the downgrade while any side-linked record has no live twin to go back under.

    Module-level so a test can DRIVE the refusal, as
    :func:`refuse_unlinkable_rows`.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each such movement and its transfer.  Nothing has
            been written.
    """
    twinless = [tuple(row) for row in bind.execute(sa.text(_TWINLESS_SQL))]
    if twinless:
        raise RuntimeError(
            f"X-bi-6-4d-2's downgrade refuses: {len(twinless)} side-linked "
            f"payment(s) have no live twin row on their side to go back under "
            f"(entry id, transfer id: {twinless}; diagnose with: "
            f"{_TWINLESS_SQL.strip()}).  A twin hidden around the transfer "
            "service is the developer's to rule; twins a later revision "
            "deleted come back with that revision's downgrade.  Nothing was "
            "written."
        )


def refuse_drifted_twins(bind, *, at_the_upgrade: bool = False) -> None:
    """Refuse while a twin does not say what its transfer and its side's record say.

    The module docstring's contract, made a refusal, and ONE census for both
    directions (:data:`_DRIFTED_TWINS_SQL`).  The DOWNGRADE asks it before
    anything is written: the code below this revision reads a side's status,
    day, basis and statement link off its twin, and this revision's app
    writes them on the transfer and the side's record alone, so a twin it has
    outlived is a drifted pair below.  The UPGRADE asks it once the backfill
    has written the side links the census reads through, inside the
    revision's transaction, so a refusal rolls the whole revision back: below
    this revision the app writes those facts on the twin too, so a twin that
    disagrees is a drift no door wrote, which the app from here on could no
    longer be told from its own.  Module-level so a test can DRIVE it, as
    :func:`refuse_unlinkable_rows`.

    Args:
        bind: A SQLAlchemy connection.
        at_the_upgrade: Whether the upgrade asks, which words the refusal as
            the upgrade's; the downgrade's otherwise.

    Raises:
        RuntimeError: Naming how many twins disagree, the first
            :data:`_NAMED_AT_MOST` with the facts that differ, and the query
            that finds them all.  Asked by the downgrade, nothing has been
            written; asked by the upgrade, after its DDL and backfill, the
            revision rolls back whole.
    """
    drifted = [tuple(row) for row in bind.execute(sa.text(_DRIFTED_TWINS_SQL))]
    if not drifted:
        return
    named = (
        f"(transfer id, twin id, what differs: {drifted[:_NAMED_AT_MOST]}; "
        f"diagnose with: {_DRIFTED_TWINS_SQL.strip()})"
    )
    if at_the_upgrade:
        raise RuntimeError(
            f"X-bi-6-4d-2 refuses: {len(drifted)} twin row(s) do not say what "
            f"their transfer and its side's payment record say {named}.  The "
            "app below this revision writes those facts on a twin with its "
            "transfer and its record, so each is a drift no door wrote; from "
            "this revision it writes them on the transfer and the record "
            "alone, and revision 82f7b7ed4332 deletes the twin.  Nothing is "
            "kept: the revision rolls back whole; each is the developer's to "
            "rule."
        )
    raise RuntimeError(
        f"X-bi-6-4d-2's downgrade refuses: {len(drifted)} twin row(s) no "
        "longer say what their transfer and its side's payment record say "
        f"{named}.  The app writes those facts on the transfer and the record "
        "alone from this revision, so the code below it would read each as a "
        "drifted pair: downgrade through revision 82f7b7ed4332, whose "
        "downgrade rebuilds the twins first.  Nothing was written."
    )


def refuse_off_band_transfers(bind) -> None:
    """Refuse the upgrade while a stored transfer's side records break the band rule.

    Asked after the backfill, inside the revision's transaction, so a refusal
    keeps nothing: the revision rolls back whole.  Module-level so a test can
    DRIVE it, as :func:`refuse_unlinkable_rows`.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each offending transfer and the query that finds
            it.
    """
    off_band = [tuple(row) for row in bind.execute(sa.text(_OFF_BAND_SQL))]
    if off_band:
        raise RuntimeError(
            f"X-bi-6-4d-2 refuses: {len(off_band)} transfer(s) hold payment "
            "records their status does not admit -- a settled transfer with an "
            "un-dated record or a record on one side only, or an unsettled "
            "one with a dated record (transfer id, settled, dated, un-dated: "
            f"{off_band}; diagnose with: {_OFF_BAND_SQL.strip()}).  Nothing "
            "is kept: the revision rolls back whole; each is the developer's "
            "to rule."
        )


def _counts(bind, sql: str) -> tuple[int, int]:
    """Return a ``(from-side, to-side)`` count pair from *sql*."""
    expense, income = bind.execute(sa.text(sql)).one()
    return int(expense), int(income)


def upgrade():
    """File every transfer side's payment under its transfer, by a side link."""
    bind = op.get_bind()
    refuse_unlinkable_rows(bind)
    expected = _counts(bind, _UNDER_TWINS_SQL)
    for column, superkey, owner_key in _ENDPOINTS:
        op.create_unique_constraint(
            superkey, "transfers", ["id", column], schema="budget",
        )
        op.create_foreign_key(
            owner_key, "transfers", "accounts", [column, "user_id"],
            ["id", "user_id"], source_schema="budget",
            referent_schema="budget", ondelete="RESTRICT",
        )
    for _name, link, _endpoint in _SIDE_KEYS:
        op.add_column(
            "transaction_entries", sa.Column(link, sa.Integer(), nullable=True),
            schema="budget",
        )
    op.alter_column(
        "transaction_entries", "transaction_id", existing_type=sa.Integer(),
        nullable=True, schema="budget",
    )
    op.create_check_constraint(
        "ck_transaction_entries_one_parent", "transaction_entries",
        "num_nonnulls(transaction_id, expense_transfer_id, "
        "income_transfer_id) = 1",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transaction_entries_side_link_is_a_record", "transaction_entries",
        "(expense_transfer_id IS NULL AND income_transfer_id IS NULL) "
        "OR covers_settlement",
        schema="budget",
    )
    for name, link, endpoint in _SIDE_KEYS:
        op.create_foreign_key(
            name, "transaction_entries", "transfers", [link, "account_id"],
            ["id", endpoint], source_schema="budget",
            referent_schema="budget", onupdate="CASCADE",
        )
    for name, link in _SIDE_INDEXES:
        op.create_index(
            name, "transaction_entries", [link], unique=True, schema="budget",
            postgresql_where=sa.text(f"{link} IS NOT NULL"),
        )
    op.execute(_BACKFILL_SQL)
    linked = _counts(bind, _SIDE_LINKED_SQL)
    left = _counts(bind, _UNDER_TWINS_SQL)
    if linked != expected or left != (0, 0):
        raise RuntimeError(
            f"X-bi-6-4d-2's backfill linked {linked} (from-side, to-side) "
            f"where {expected} hung off a twin, and {left} still do; the "
            "transaction rolls back."
        )
    refuse_drifted_twins(bind, at_the_upgrade=True)
    refuse_off_band_transfers(bind)
    apply_deleted_row_infrastructure(op.execute, arms=(ROW_ARM, TRANSFER_ARM))
    # LAST: a deferred trigger queues an event per matching row, and a queued
    # event blocks an ALTER TABLE later in the same transaction.
    apply_side_band_infrastructure(op.execute, arms=(BAND_ARM,))
    print(
        f"X-bi-6-4d-2: {linked[0]} from-side and {linked[1]} to-side payment "
        "record(s) now hang off their transfer by a side link; 0 under a twin; "
        "0 refused.  A transfer's accounts are its owner's, and a payment "
        "arriving under a deleted transfer, or a transfer hidden while it holds "
        "one, is refused; so is a record dated off its transfer's status."
    )


def downgrade():
    """Put every side-linked payment back under its side's twin."""
    bind = op.get_bind()
    refuse_twinless_sides(bind)
    refuse_drifted_twins(bind)
    # FIRST: the re-attach below would queue the band rule's deferred events,
    # and a queued event blocks the column drops after it.
    remove_side_band_infrastructure(op.execute)
    apply_deleted_row_infrastructure(op.execute, arms=(ROW_ARM,))
    op.execute(_REATTACH_SQL)
    orphaned = bind.execute(sa.text(
        "SELECT count(*) FROM budget.transaction_entries "
        "WHERE transaction_id IS NULL"
    )).scalar_one()
    if orphaned:
        raise RuntimeError(
            f"X-bi-6-4d-2's downgrade left {orphaned} movement(s) with no "
            "parent row (SELECT id FROM budget.transaction_entries WHERE "
            "transaction_id IS NULL); the transaction rolls back."
        )
    for name, _link in _SIDE_INDEXES:
        op.drop_index(name, table_name="transaction_entries", schema="budget")
    for name, _link, _endpoint in _SIDE_KEYS:
        op.drop_constraint(
            name, "transaction_entries", schema="budget", type_="foreignkey",
        )
    op.drop_constraint(
        "ck_transaction_entries_side_link_is_a_record", "transaction_entries",
        schema="budget", type_="check",
    )
    op.drop_constraint(
        "ck_transaction_entries_one_parent", "transaction_entries",
        schema="budget", type_="check",
    )
    for _name, link, _endpoint in _SIDE_KEYS:
        op.drop_column("transaction_entries", link, schema="budget")
    op.alter_column(
        "transaction_entries", "transaction_id", existing_type=sa.Integer(),
        nullable=False, schema="budget",
    )
    for _column, superkey, owner_key in _ENDPOINTS:
        op.drop_constraint(
            owner_key, "transfers", schema="budget", type_="foreignkey",
        )
        op.drop_constraint(
            superkey, "transfers", schema="budget", type_="unique",
        )
