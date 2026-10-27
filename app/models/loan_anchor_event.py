"""
Shekel Budget App -- Loan Anchor Event Model (budget schema)

Append-only log of dated balance assertions for a loan account.  The
loan resolver (Commit 13 / E-18) replays confirmed payments forward
from the latest event in this log to derive the loan's current
principal, monthly payment, and full amortisation schedule on read.

Two row provenances are recorded via the ``source_id`` FK into
``ref.loan_anchor_sources``:

* ``origination`` -- LEGACY ONLY (the read switch's final commit retired
  this write): rows materialised from the immutable :class:`LoanParams`
  fields (``origination_date``, ``original_principal``) by the Commit 12
  backfill migration and, until the retirement, by ``create_params``.
  Every consumer now SYNTHESIZES the origination anchor from the params
  (``loan_loaders.load_loan_anchor_facts``) and ignores these rows --
  they are value-identical append-only history, kept, never read.
* ``user_trueup`` -- appended by the loan dashboard's balance-edit
  flow (Commit 16, decision D-C) whenever the operator asserts a new
  dated balance: the operator's SOURCE DOCUMENT, from which the genesis
  ledger's self-healing TRUEUP correction is derived and re-derived.
  Mirrors the checking-account ``AccountAnchorHistory``
  UX so the mental model is consistent across account types.

This table is **structurally append-only**.  Application code never
updates or deletes a row, mirroring the
:class:`AccountAnchorHistory` design and matching the project's
forensic-immutability stance for financial state changes.  The
in-process guard fires on any ORM-mediated UPDATE or DELETE
(:func:`sqlalchemy.event` listeners below); database-level CASCADE
deletes from ``budget.accounts`` still cascade through the FK action,
since those run outside the ORM and are the documented disposal path
for an entire account's history.

Duplicate-submit prevention follows the :class:`AccountAnchorHistory`
precedent, and since ruling **R-EQ** (plan step X-f1c4b) that precedent is a
WRITE-DOOR rule rather than a unique index: an event is appended only when it
differs from the event that already governs its source.  See
:func:`app.services.loan_anchor_service._append_loan_anchor_and_sync` for the rule
and the ``__table_args__`` comment below for what the deleted index could not
express.
"""

from app.extensions import db
from app.models.append_only import (
    AppendOnlyViolation,
    install_append_only_guards,
)
from app.models.mixins import AccountScopedMixin, CreatedAtMixin


class LoanAnchorEvent(AccountScopedMixin, CreatedAtMixin, db.Model):
    """Append-only dated balance assertion for a loan account.

    Read by the loan resolver (Commit 13) which selects the most
    recent event per account, treats ``(anchor_date, anchor_balance)``
    as the snap-to point, and replays confirmed payments -- the settled
    to-side legs of transfers into the loan -- forward from that anchor
    to produce a current balance, monthly payment, and full amortisation
    schedule.

    The ``source_id`` FK is RESTRICT-on-delete because the seed rows
    in ``ref.loan_anchor_sources`` are non-removable application
    invariants: a successful DELETE would orphan every event tagged
    with that source.

    Storage tier guarantees:

    * ``anchor_balance >= 0`` -- a negative loan balance is
      meaningless; the engine treats overpayment as zero principal,
      and a positive-only domain matches every monetary CHECK in
      the rest of the schema.
    * ``account_id`` CASCADE-on-delete -- deleting a loan account
      removes its anchor history with it.  No orphan-event rows.
    * No uniqueness guard over the row's VALUES, deliberately (ruling R-EQ)
      -- a duplicate submit is refused at the write door, which can compare
      against what governs; an index over the row's values cannot.  See the
      ``__table_args__`` comment below, which also says why the one UNIQUE
      the table does carry, over ``(account_id, id)``, rejects no row.
    * A statement STANDS until a
      :class:`~app.models.loan_anchor_withdrawal.LoanAnchorWithdrawal` names
      it (plan step ``recurrence:R23``); read a loan's statements through
      :func:`app.services.loan_loaders.load_standing_loan_assertions`, never
      this table directly.
    """

    __tablename__ = "loan_anchor_events"
    __table_args__ = (
        db.CheckConstraint(
            "anchor_balance >= 0",
            name="ck_loan_anchor_events_balance_nonneg",
        ),
        # The seek index for the table's ONE reader,
        # ``loan_loaders.load_standing_loan_assertions``, which filters on
        # ``account_id`` and orders in Python (the loan's chronology key), so
        # only the leading term serves it.  It served an ``ORDER BY`` for two
        # readers once: the resolver's "latest anchor per account" lookup until
        # plan step X-an-b, and the write door's governing-event query
        # (``loan_anchor_service._governing_loan_anchor``, which bounded
        # ``anchor_date`` and sorted in SQL) until plan step ``recurrence:R23``
        # routed the door through the same producer.  It shared the table with
        # a unique expression index over ``(account_id, anchor_date,
        # anchor_balance, utc_day(created_at))`` until ruling R-EQ deleted that
        # one (plan step X-f1c4b).
        db.Index(
            "idx_loan_anchor_events_account",
            "account_id", "anchor_date",
        ),
        # **There is no uniqueness guard over this table's VALUES, and that is
        # ruling R-EQ** (plan step X-f1c4b).  It carried
        # ``uq_loan_anchor_events_acct_date_bal_day`` over ``(account_id,
        # anchor_date, anchor_balance, ((created_at AT TIME ZONE 'UTC')::date))``
        # to absorb a double-click on the dashboard's "Record balance" button,
        # the loan twin of a guard ``AccountAnchorHistory`` also carried.
        #
        # A content key cannot tell a transport retry from a deliberate
        # re-assertion -- their values are identical by construction -- so it
        # refused the correction: re-assert a balance for a date after
        # correcting it earlier on the same recording day, and the write was
        # rejected while the route flashed "already recorded".  The rule now
        # lives at the write door
        # (``loan_anchor_service._append_loan_anchor_and_sync``), which compares the
        # submission against the event that GOVERNS for its own source and so
        # answers exactly.
        #
        # An earlier version of this comment scheduled the OPPOSITE fix: it
        # said the checking twin's asymmetry would expire when the checking
        # form gained its own date field, and directed a re-key back onto
        # ``(observed_on, utc_day(created_at))``.  That would have narrowed the
        # false refusal to one recording day rather than removing it; the trace
        # for that step measured the residue and replaced the mechanism instead.
        #
        # **The SUPERKEY a withdrawal targets, and it does NOT revive R-EQ**
        # (plan step ``recurrence:R23``, ruling **R-R98**).  A
        # :class:`~app.models.loan_anchor_withdrawal.LoanAnchorWithdrawal`
        # names the statement it withdraws through a COMPOSITE foreign key over
        # ``(account_id, id)``, so withdrawing another account's statement is
        # unrepresentable, and PostgreSQL requires a UNIQUE over exactly those
        # columns before such a key may target them.  It can reject NO row:
        # ``id`` is the primary key, so every pair containing it is already
        # distinct -- the reason ``uq_anchor_history_account_id`` gives for the
        # same key on the cash twin.
        db.UniqueConstraint(
            "account_id", "id", name="uq_loan_anchor_events_account_id",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    anchor_date = db.Column(db.Date, nullable=False)
    anchor_balance = db.Column(db.Numeric(12, 2), nullable=False)
    source_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.loan_anchor_sources.id",
            name="fk_loan_anchor_events_source_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    # **This collection carries NO ``order_by``, deliberately** (plan step
    # X-an-b).  It held ``anchor_date DESC, created_at DESC`` until then, which
    # was a third statement of "which anchor governs" -- incomplete (no ``id``
    # term, so it did not order two rows written in one transaction) and with
    # ZERO readers in ``app/``, ``tests/`` or ``scripts/``.  Growing it the
    # missing term would have kept a correct answer to the wrong question: this
    # collection can never BE the loan's anchor set under any ordering, because
    # it EXCLUDES the synthesized origination (which has no stored row) and
    # INCLUDES the legacy ``origination``-source rows that
    # ``loan_loaders.load_loan_anchor_facts`` deliberately ignores.  An
    # ordered-looking collection therefore invites a reader to treat it as the
    # anchor chronology, which it is not.  **The loan's ONE anchor order is
    # ``load_loan_anchor_facts``**, ascending by
    # ``(anchor_date, created_at, id)``; read anchors through it.
    #
    # The relationship itself stays: its cascade configuration is load-bearing
    # (see the module docstring on the account-deletion disposal path).
    account = db.relationship(
        "Account",
        backref=db.backref(
            "loan_anchor_events",
            cascade="all, delete-orphan",
            passive_deletes=True,
            lazy="select",
        ),
    )
    source = db.relationship("LoanAnchorSource", lazy="joined")

    def __repr__(self):
        return (
            f"<LoanAnchorEvent account_id={self.account_id} "
            f"date={self.anchor_date} balance={self.anchor_balance}>"
        )


class LoanAnchorEventImmutableError(AppendOnlyViolation):
    """Raised when ORM code attempts to UPDATE or DELETE a LoanAnchorEvent.

    The table is structurally append-only (decision D-A): a
    correction is expressed as a NEW row, never as an edit of an
    existing one.  Mirrors the audit philosophy applied to
    ``system.audit_log`` and matches the forensic invariant that
    backfilled origination rows must be reconstructible at any point
    in the future from the same immutable LoanParams source.

    Database-level CASCADE deletes from ``budget.accounts`` are NOT
    intercepted -- they happen outside the SQLAlchemy ORM session
    and are the documented disposal path for an entire account's
    history.

    **A direct SQL UPDATE or DELETE is no longer unaffected, and this
    docstring said it was until plan step X-f3c-2c** (ruling **R-HY**).
    ``budget.refuse_append_only_change`` -- the trigger
    :mod:`app.append_only_infrastructure` installs on this table and on its two
    cash siblings -- refuses every actor and every spelling, so what this
    listener adds is the NAME and the call site rather than the rule.
    """


install_append_only_guards(
    LoanAnchorEvent, LoanAnchorEventImmutableError,
)
