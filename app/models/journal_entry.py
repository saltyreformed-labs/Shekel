"""
Shekel Budget App -- Journal Entry + Posting Models (budget schema)

The append-only double-entry ledger for the posting architecture
(Build-Order Step 2).  A confirmed financial event is recorded as one
:class:`JournalEntry` header plus two or more :class:`Posting` legs whose
signed amounts sum to zero.  Step 2 pilots the mechanism on settled
transfers (the simplest balanced event: money leaves one account and
enters another), so the only legs written are a transfer's two.

**Coexistence, not replacement.**  These tables run *alongside*
``budget.transfers`` / ``budget.transactions``.  Every balance read in the
app still flows through the ``balance_at`` seam over ``budget.transactions``
(Build-Order Step 1); nothing reads postings yet.  The ledger is a parallel,
independently-checkable record of the confirmed-transfer subset, validated
by the reconciliation oracle (Commit 6) against the settled-transfer shadow
effect in ``budget.transactions``.

**The signed amount is debit-positive.**  Each :class:`Posting` carries one
signed ``Numeric(12,2)`` ``amount``: a debit is positive, a credit is
negative.  For a transfer the *from* leg is ``-amount`` (a credit: money
leaving) and the *to* leg is ``+amount`` (a debit: money entering), so the
entry sums to zero regardless of whether a leg's ledger account is an asset
or a liability.  The posting builder therefore never branches on account
class; the class only affects how a *reader* later interprets an account's
accumulated debit-positive balance (Asset/Expense are debit-normal;
Liability/Income/Equity are credit-normal -- see
:class:`app.models.ref.LedgerAccountClass`).  Debit-positive (not
"balance-effect") is required so the sum-to-zero self-check survives the
later steps that post income and expense legs.

**Append-only, corrected by reversing entries.**  Mirroring
:class:`app.models.loan_anchor_event.LoanAnchorEvent`, both tables are
structurally append-only: the ``before_update`` / ``before_delete``
SQLAlchemy listeners below raise :class:`JournalEntryImmutableError` /
:class:`PostingImmutableError` on any ORM-mediated UPDATE or DELETE.  A
correction is a NEW balanced (reversing) entry, never an edit.  The
database-level CASCADE that disposes of an entire tenancy
(``journal_entries.user_id`` / ``scenario_id`` / ``pay_period_id`` are
CASCADE, and ``account_postings.journal_entry_id`` / ``ledger_account_id``
are CASCADE) runs OUTSIDE the ORM session, so the listeners do not block it;
the audit-log trigger captures the disposed rows regardless.  The
``passive_deletes=True`` on the postings relationship tells SQLAlchemy to
rely on that database CASCADE rather than loading and individually deleting
the legs (which would trip the leg's own ``before_delete`` guard).

**The balanced-journal invariant is enforced in the database.**  Per-entry
``SUM(amount) = 0`` and ``COUNT(*) >= 2`` cannot be expressed as a row-level
CHECK, so they live in a *deferred* constraint trigger
(``ck_account_postings_balanced``) that validates at COMMIT, after all of an
entry's legs are inserted.  Its SQL is centralised in
:mod:`app.posting_infrastructure` (modeled on
:mod:`app.audit_infrastructure`) so the migration, ``scripts/init_database``
(the ``create_all`` path that bypasses migrations), and
``scripts/build_test_template`` stay in lock-step.  The trigger fires
``AFTER INSERT OR UPDATE`` but NOT on DELETE, so the CASCADE disposal path
does not abort mid-cascade on a transient ``COUNT < 2``.
"""

from sqlalchemy import event

from app.extensions import db
from app.models.mixins import CreatedAtMixin, UserScopedMixin


class JournalEntry(UserScopedMixin, CreatedAtMixin, db.Model):
    """The header of one balanced double-entry event.

    Carries the owning ``user_id`` (tenancy), the ``scenario_id`` and
    ``pay_period_id`` the event is attributed to (the join keys the
    reconciliation oracle uses), the civil ``entry_date`` of the confirmed
    event, a ``source_kind_id`` naming the *kind* of source
    (``ref.posting_sources`` -- ``transfer`` in Step 2, ``transaction`` in
    Step 3), an optional concrete ``transfer_id`` / ``transaction_id`` /
    ``transaction_entry_id`` linking back to the source row, and a human
    ``description``.

    The source references are deliberately layered: ``source_kind_id``
    answers "what kind of event posted this?" (a non-removable ref
    invariant, RESTRICT) while each concrete nullable FK answers "which
    concrete source row?" (SET NULL, so the immutable posted fact survives a
    source delete).  Step 2 added ``transfer_id``; Step 3 added
    ``transaction_id`` beside it for ordinary (non-transfer) settled
    transactions; plan step X-f3b adds ``transaction_entry_id`` for a PURCHASE
    that has cleared the bank (ruling **R-FM**).  ``source_kind_id``
    disambiguates which is set: a ``transfer`` entry carries ``transfer_id``,
    a ``transaction`` entry ``transaction_id``, a ``purchase`` or
    ``transfer_movement`` entry ``transaction_entry_id``, and each carries
    NULL in the other two; a CORRECTION -- ``loan_opening`` / ``loan_trueup``
    / ``loan_payment`` / ``account_opening`` / ``account_trueup`` -- carries
    NULL in all three, because it is a derivation of a walk and keyed by its
    ``(source kind, pay period, entry date)`` rather than by a row (the loan
    payment split joined that family at plan step ``balance:X-bi-6-3``,
    ruling **R-BAL102**; it linked the loan-side shadow before).  Later
    Build-Order steps add one concrete nullable FK per new ROW-LINKED source
    kind beside these three.  (The one-set-FK-per-entry rule is maintained by the posting
    builder, not a storage CHECK -- a CHECK would have to grow with every
    future source kind and reference ref-table IDs it cannot see; the
    reconciliation oracle is the cross-source correctness gate.  Plan step
    X-ai-s replaces the convention with an exclusive arc.)

    Append-only: see the module docstring.  Disposal is the database-level
    CASCADE from a deleted user / scenario / pay period, which runs outside
    the ORM and so is not blocked by the immutability listeners below.
    """

    __tablename__ = "journal_entries"
    __table_args__ = (
        # Reconciliation and reporting scan by (user, scenario, period):
        # the oracle sums an account's postings within one scenario/period,
        # and reports group entries the same way.
        db.Index(
            "idx_journal_entries_user_scenario_period",
            "user_id", "scenario_id", "pay_period_id",
        ),
        # Lifecycle lookups ("what has this transfer posted?").  Partial so
        # the index covers only entries that carry a concrete transfer link;
        # entries from later source kinds with a NULL ``transfer_id`` fall
        # outside it.  The ``postgresql_where`` text matches the migration's
        # index DDL byte-for-byte so autogenerate produces no spurious diff.
        db.Index(
            "idx_journal_entries_transfer",
            "transfer_id",
            postgresql_where=db.text("transfer_id IS NOT NULL"),
        ),
        # The transaction analog (Step 3): lifecycle lookups ("what has this
        # transaction posted?") and the per-transaction reconcile-to-target
        # filter.  Partial (``WHERE transaction_id IS NOT NULL``) for the
        # same reason as the transfer index -- transfer-sourced entries carry
        # a NULL ``transaction_id`` and fall outside it.  Verbatim shape of
        # the transfer index above.
        db.Index(
            "idx_journal_entries_transaction",
            "transaction_id",
            postgresql_where=db.text("transaction_id IS NOT NULL"),
        ),
        # The PURCHASE analog (plan step X-f3b, ruling **R-FM**): the
        # per-purchase reconcile-to-target filter reads back what one
        # ``budget.transaction_entries`` row has posted.  Partial for the same
        # reason as its two siblings -- an entry of any other source kind
        # carries NULL here and falls outside the index.
        db.Index(
            "idx_journal_entries_transaction_entry",
            "transaction_entry_id",
            postgresql_where=db.text("transaction_entry_id IS NOT NULL"),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # ``scenario_id`` (denormalised tenancy -- CASCADE so deleting a scenario
    # disposes of its entries, whose legs cascade in turn) and the
    # ``pay_period_id`` that follows (period attribution -- the join key for
    # period-level reconciliation; CASCADE matches the sibling period-scoped
    # tables, e.g. ``transactions.pay_period_id``) both carry explicit
    # convention FK names: the SHEKEL_NAMING_CONVENTION is not installed on
    # the metadata, so new FKs must name themselves.  The two consecutive
    # CASCADE-FK blocks are structurally identical to the transfer table's --
    # both a transfer and a journal entry are budget events attributed to a
    # scenario and a period -- but the tables are deliberately separate
    # domains (a transfer is a planned movement whose two sides' payments
    # hang off it by side links on ``transaction_entries``; a journal entry
    # is an append-only ledger header), so a shared base would couple them
    # wrongly (coding-standards rule 13).
    # Pylint: ``duplicate-code`` -- incidental structural FK-block similarity
    # with ``app/models/transfer.py``; one-sided disable so the transfer
    # block stays un-disabled (mirroring transfer.py's own one-sided disable
    # against transaction.py).
    # pylint: disable=duplicate-code
    scenario_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.scenarios.id",
            name="fk_journal_entries_scenario_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # **The paycheck this entry posted into.  KEPT rather than derived**
    # (plan step ``pay_calendar:C7``, ruling **R-PC53**, which holds the
    # measurements and the argument).  Ledger row **P18** read the column as a
    # materialisation of "which paycheck does ``entry_date`` fall in" and asked
    # for its removal; that is refused, because the column has TWO halves and
    # the premise fits neither as stated.  A SOURCE-LINKED entry COPIES its
    # source row's own ``pay_period_id`` -- the owner's budgeting choice, not a
    # function of ``entry_date``.  An ANCHOR correction DERIVES its period from
    # the entry's date through ``PayCalendar.filing_period`` (ruling
    # **balance:R-EA**).
    #
    # **Read this column back; never recompute it at read time.**  The reconcile
    # targets a reversal at the period its postings landed in, which is what the
    # stored value records.  R-PC53 states why that matters and what actually
    # holds the value against rewriting -- in short, the ``shekel_app`` role has
    # no ``UPDATE`` on this table (migration ``e3c23fadb21d``) while the owner
    # role, and therefore any migration, does.
    pay_period_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.pay_periods.id",
            name="fk_journal_entries_pay_period_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # pylint: enable=duplicate-code
    # Civil date of the confirmed event, in the USER's timezone (ruling
    # R-DH (b)).  Not derivable from ``pay_period_id`` (a period spans 14
    # days), so it is stored, not computed.  **Nor does it DETERMINE
    # ``pay_period_id``** -- see that column's comment above and ruling
    # **R-PC53**: the relationship differs by source kind and is not a
    # derivation in general.
    #
    # A source entry takes the source
    # row's stored ``transactions.settled_on``; an anchor correction takes the
    # assertion's stored ``observed_on``.  BOTH are now stored facts read
    # through one accessor rather than days derived from an instant -- the
    # source side became one at plan step X-f1 (ruling R-EC), the anchor side
    # at step 2.  It was the UTC civil day until 2026-07-31 (the Commit-3
    # backfill's ``COALESCE((paid_at AT TIME ZONE 'UTC')::date, start_date)``),
    # which put a late-evening Eastern event on the next day's entry.
    entry_date = db.Column(db.Date, nullable=False)
    # The KIND of source event (ref.posting_sources).  RESTRICT: the seeded
    # rows are non-removable application invariants -- a successful delete
    # would strand every entry tagged with that source kind.
    source_kind_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.posting_sources.id",
            name="fk_journal_entries_source_kind_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    # The concrete source transfer, or NULL once that transfer is hard-
    # deleted.  SET NULL (not CASCADE): the posted fact is immutable history
    # and must survive a source-transfer delete; only the back-link is
    # cleared.  Nullable because a hard-deleted transfer leaves the entry
    # orphaned-but-intact, and because later source kinds carry NULL here.
    transfer_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.transfers.id",
            name="fk_journal_entries_transfer_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    # The concrete source transaction (Step 3), or NULL once that
    # transaction is hard-deleted (and NULL on every transfer-sourced
    # entry).  Verbatim shape of ``transfer_id`` above: SET NULL so the
    # immutable posted fact survives a source-transaction delete with only
    # the back-link cleared.  ``source_kind_id`` disambiguates which of the
    # two concrete FKs is set (see the class docstring).
    transaction_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.transactions.id",
            name="fk_journal_entries_transaction_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    # The concrete source PURCHASE (plan step X-f3b, ruling **R-FM**), or NULL
    # once that purchase is deleted (and NULL on every entry of another source
    # kind).  Verbatim shape of the two FKs above.
    #
    # **It is a source of its own rather than more legs on the parent's entry**
    # because the posted walk reads a source's day and its clearing link off the
    # SOURCE ROW: a purchase carries its own ``settled_on`` and its own
    # ``reconciled_by_id``, and a still-projected envelope has no settle day at
    # all -- so legs grouped under the parent's ``transaction_id`` could not be
    # dated or attributed.
    transaction_entry_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.transaction_entries.id",
            name="fk_journal_entries_transaction_entry_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    # Human-readable label, e.g. "Transfer: Checking to Savings".  Display
    # only; never used for logic.
    description = db.Column(db.String(200), nullable=False)
    # user_id (NOT NULL, CASCADE FK to auth.users.id) from UserScopedMixin.
    # created_at (TIMESTAMPTZ NOT NULL DEFAULT now()) from CreatedAtMixin.

    # The legs.  ``cascade="all, delete-orphan"`` + ``passive_deletes=True``
    # is the standard parent-with-DB-cascade-children pattern: the ORM does
    # not emit per-leg DELETEs (the ``account_postings.journal_entry_id`` FK
    # is ON DELETE CASCADE and disposes of them), and an ORM delete of the
    # entry is blocked by the immutability guard anyway.  Ordered by ``id``
    # so a reader sees the legs in insertion order.
    postings = db.relationship(
        "Posting",
        back_populates="journal_entry",
        order_by="Posting.id",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )

    def __repr__(self):
        return (
            f"<JournalEntry id={self.id} transfer_id={self.transfer_id} "
            f"transaction_id={self.transaction_id} "
            f"transaction_entry_id={self.transaction_entry_id} "
            f"date={self.entry_date}>"
        )


class Posting(CreatedAtMixin, db.Model):
    """One signed leg of a balanced journal entry.

    ``amount`` is the single signed ``Numeric(12,2)`` money column in the
    schema (debit-positive / credit-negative; ``CHECK (amount <> 0)`` -- a
    zero leg is meaningless).  ``posting_kind_id`` tags the leg's economic
    nature (``ref.posting_kinds`` -- ``transfer`` in Step 2), RESTRICT
    because the seeded kinds are non-removable invariants.

    No ``user_id``: a posting is scoped through its
    ``journal_entry.user_id`` (normalisation over a denormalised
    convenience column).  ``journal_entry_id`` and ``ledger_account_id`` are
    both CASCADE -- the documented disposal paths (a tenancy delete cascades
    through the entry; a ledger-account delete cascades the leg).  Per the
    cascade-imbalance impossibility argument in
    :class:`app.models.ledger_account.LedgerAccount`, an account whose linked
    ledger *has* postings is archived rather than hard-deleted (route Guard 5,
    load-bearing because a settled transfer's postings outlive the transfer
    that wrote them), so only a posting-free account ever reaches the CASCADE
    and the ``ledger_account_id`` CASCADE never orphans -- and in practice
    never fires on -- a leg.

    Append-only: see the module docstring.  The per-entry balanced invariant
    (sum to zero, at least two legs) is enforced by the deferred constraint
    trigger in :mod:`app.posting_infrastructure`, validated at COMMIT.
    """

    __tablename__ = "account_postings"
    __table_args__ = (
        # The balanced-trigger's per-entry SUM and ORM leg retrieval both
        # scan by ``journal_entry_id``.
        db.Index("idx_account_postings_entry", "journal_entry_id"),
        # Per-account reconciliation sums postings by ledger account.
        db.Index("idx_account_postings_ledger", "ledger_account_id"),
        # A zero leg carries no information and would let a "balanced" entry
        # hide a missing movement; the storage tier refuses it.
        db.CheckConstraint(
            "amount <> 0",
            name="ck_account_postings_amount_nonzero",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # The owning entry.  CASCADE: disposing of an entry (only ever via a
    # tenancy-level DB cascade) takes its legs with it.
    journal_entry_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.journal_entries.id",
            name="fk_account_postings_journal_entry_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # The chart-of-accounts entry this leg lands in.  CASCADE is the
    # documented disposal path (mirroring ``loan_anchor_events.account_id``);
    # the LedgerAccount impossibility argument keeps it from ever orphaning a
    # leg in practice.
    ledger_account_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.ledger_accounts.id",
            name="fk_account_postings_ledger_account_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # Signed: debit-positive / credit-negative.  The one signed money column
    # in the schema (every other monetary column is non-negative).
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    # The economic nature of the leg (ref.posting_kinds).  RESTRICT: the
    # seeded kinds are non-removable invariants.
    posting_kind_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.posting_kinds.id",
            name="fk_account_postings_posting_kind_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    # created_at (TIMESTAMPTZ NOT NULL DEFAULT now()) from CreatedAtMixin.

    # Relationships.  ``journal_entry`` pairs with ``JournalEntry.postings``.
    # ``ledger_account`` and ``posting_kind`` are ``lazy="select"`` (no eager
    # JOIN): readers resolve the kind via ``ref_cache.posting_kind_id`` and
    # the ledger account's class via ``ref_cache.ledger_class_is_debit_normal``
    # keyed by id, never by navigating these relationships, so eager-loading
    # them would add JOINs no path consumes.  Kept for ORM navigation and
    # debugging.
    journal_entry = db.relationship("JournalEntry", back_populates="postings")
    ledger_account = db.relationship("LedgerAccount", lazy="select")
    posting_kind = db.relationship("PostingKind", lazy="select")

    def __repr__(self):
        return (
            f"<Posting id={self.id} entry={self.journal_entry_id} "
            f"ledger_account={self.ledger_account_id} amount={self.amount}>"
        )


class JournalEntryImmutableError(RuntimeError):
    """Raised when ORM code attempts to UPDATE or DELETE a JournalEntry.

    The ledger is structurally append-only: a correction is expressed as a
    NEW balanced reversing entry, never as an edit or delete of an existing
    one.  Mirrors :class:`app.models.loan_anchor_event.LoanAnchorEventImmutableError`
    and the ``system.audit_log`` forensic-immutability stance.

    Database-level CASCADE deletes (from a deleted user / scenario / pay
    period) are NOT intercepted -- they run outside the SQLAlchemy ORM
    session and are the documented disposal path for an entire tenancy.
    Direct SQL UPDATE/DELETE statements are similarly unaffected; this guard
    catches programmer errors at the call site, and the audit-log trigger
    captures the row regardless.
    """


class PostingImmutableError(RuntimeError):
    """Raised when ORM code attempts to UPDATE or DELETE a Posting.

    Same append-only rationale as :class:`JournalEntryImmutableError`: a leg
    is never edited or individually removed; corrections are new reversing
    entries.  Database-level CASCADE (from a disposed entry or ledger
    account) runs outside the ORM and is not intercepted.
    """


@event.listens_for(JournalEntry, "before_update")
def _block_journal_entry_update(_mapper, _connection, target):
    """Refuse every ORM-mediated UPDATE on a JournalEntry.

    Fires before SQLAlchemy emits the UPDATE so the session rolls back
    cleanly with a named exception the test suite can assert against.  Any
    correction must be a new reversing entry, not an edit.
    """
    raise JournalEntryImmutableError(
        f"JournalEntry is append-only; UPDATE rejected for id={target.id!r}."
    )


@event.listens_for(JournalEntry, "before_delete")
def _block_journal_entry_delete(_mapper, _connection, target):
    """Refuse every ORM-mediated DELETE on a JournalEntry.

    Same rationale as :func:`_block_journal_entry_update`.  Database-level
    CASCADE from ``budget.scenarios`` / ``budget.pay_periods`` /
    ``auth.users`` flows through the FK action and does NOT load each entry
    into the ORM session, so this guard does not interfere with tenancy
    disposal.
    """
    raise JournalEntryImmutableError(
        f"JournalEntry is append-only; DELETE rejected for id={target.id!r}."
    )


@event.listens_for(Posting, "before_update")
def _block_posting_update(_mapper, _connection, target):
    """Refuse every ORM-mediated UPDATE on a Posting.

    A leg's signed amount is an immutable record of a confirmed movement;
    correcting it means a new reversing entry, never an in-place edit.
    """
    raise PostingImmutableError(
        f"Posting is append-only; UPDATE rejected for id={target.id!r}."
    )


@event.listens_for(Posting, "before_delete")
def _block_posting_delete(_mapper, _connection, target):
    """Refuse every ORM-mediated DELETE on a Posting.

    Same rationale as :func:`_block_posting_update`.  The database CASCADE
    that disposes of a leg (from a deleted entry or ledger account) runs
    outside the ORM session and is not intercepted -- it is the documented
    disposal path; ``passive_deletes=True`` on ``JournalEntry.postings``
    ensures the ORM relies on that cascade rather than loading the legs to
    delete them here.
    """
    raise PostingImmutableError(
        f"Posting is append-only; DELETE rejected for id={target.id!r}."
    )
