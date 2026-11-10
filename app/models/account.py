"""
Shekel Budget App -- Account Models (budget schema)

Tracks checking and savings accounts with anchor balance history
for the true-up workflow.
"""

from app.enums import StatementBalanceEvidenceEnum
from app.extensions import db
from app.models.append_only import (
    AppendOnlyViolation,
    install_append_only_guards,
)
from app.models.mixins import (
    AccountScopedMixin,
    CreatedAtMixin,
    IsActiveMixin,
    OptimisticLockMixin,
    SortOrderMixin,
    TimestampMixin,
    UserScopedMixin,
)
from app.utils.dates import display_today


class Account(
    UserScopedMixin, SortOrderMixin, IsActiveMixin, OptimisticLockMixin,
    TimestampMixin, db.Model,
):
    """A financial account (checking or savings) owned by a user.

    Optimistic locking: ``version_id`` is the SQLAlchemy
    ``version_id_col`` for the row.  Every ORM-emitted UPDATE or
    DELETE is automatically narrowed to ``WHERE id = ? AND
    version_id = ?`` and the stored value is incremented in the same
    statement.  When two concurrent requests both load the same row
    at version N, the first commit advances the row to N+1; the
    second commit's WHERE matches zero rows, SQLAlchemy raises
    :class:`sqlalchemy.orm.exc.StaleDataError`, and the calling
    route returns HTTP 409 Conflict.

    The column has ``server_default="1"`` so existing rows on the
    production database are populated automatically when the
    accompanying migration runs ALTER TABLE; new rows insert with
    version_id = 1 on either path (default or explicit).
    """

    __tablename__ = "accounts"
    __table_args__ = (
        db.UniqueConstraint("user_id", "name", name="uq_accounts_user_name"),
        # The SUPERKEY ``account_external_identities`` names to prove its own
        # ``user_id`` is this account's owner (plan step
        # ``bank_import:X-f6a-1``).  It constrains nothing -- ``id`` is already
        # the primary key, so this key can reject no row -- and exists only
        # because PostgreSQL requires a UNIQUE over exactly the referenced
        # columns before a composite foreign key may target them.  The same
        # construction, for the same reason, as ``uq_transactions_id_account``.
        db.UniqueConstraint("id", "user_id", name="uq_accounts_id_user"),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_accounts_version_id_positive",
        ),
        # Collateral self-link guard (home-equity mini-sprint): an
        # account may not secure itself.  Belt-and-suspenders with the
        # route validator's no-self-link check.
        db.CheckConstraint(
            "collateral_account_id IS NULL OR collateral_account_id != id",
            name="ck_accounts_collateral_not_self",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    account_type_id = db.Column(
        db.Integer, db.ForeignKey("ref.account_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    # An account carries NO anchor columns, and that is ruling R-EH (plan step
    # X-f1c3c).  ``current_anchor_balance`` / ``current_anchor_period_id`` were
    # a denormalized copy of the newest ``AccountAnchorHistory`` row --
    # ``cash_ledger/_facts`` said so in those words, and when they disagreed the
    # history row already won while the copy was logged and left wrong.  Twelve
    # surfaces read the copy instead of the fact.  What the account has been
    # asserted to hold is now asked of
    # :func:`app.services.cash_ledger.resolve_anchor`, the one resolver, and the
    # divergence this pair could express is not detected-and-logged, it is
    # inexpressible.  Measured before the drop: the copy agreed with the latest
    # assertion on 9 of 9 production accounts, so nothing moved.
    #
    # Their FK to ``pay_periods`` went with them, and it took real machinery:
    # ``ON DELETE NO ACTION DEFERRABLE INITIALLY IMMEDIATE`` existed so
    # ``reset_pay_periods`` could delete the old anchor period and re-point
    # every account inside one transaction via ``SET CONSTRAINTS ... DEFERRED``.
    # With no column to re-point there is nothing to defer.
    # Collateral link (home-equity mini-sprint): a secured liability
    # (mortgage / HELOC / auto loan) points at the Asset account it is
    # secured by, so a Property and its loans can be grouped and equity
    # rendered.  Nullable -- NULL means the loan is not secured by a
    # tracked asset.  ``ON DELETE SET NULL`` (not CASCADE) keeps the loan
    # -- real money -- alive when the asset row is deleted; the link is
    # presentation only and the net-worth math never reads it.  A
    # self-link is rejected by ``ck_accounts_collateral_not_self`` and the
    # route validator.
    collateral_account_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.accounts.id",
            name="fk_accounts_collateral_account",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    account_type = db.relationship("AccountType", lazy="joined")
    # **This collection carries NO ``order_by``, deliberately** (plan step
    # X-an-b, found by that step's census of every ``created_at`` ordering in
    # ``app/``).  It held ``created_at DESC`` -- the RECORDING instant, with no
    # ``observed_on`` term at all, which is the key
    # :mod:`app.services.cash_ledger` replaced at plan step 2 precisely because
    # a user-supplied business date and a recording instant can order
    # differently: that confusion cost production ``$4,001.42`` of
    # double-subtracted payments and once tagged a ``$1,307.66`` true-up as the
    # account's OPENING (``cash_ledger/_events.py``).  It carried no ``id``
    # either, so it was not total.  Zero readers in ``app/``, ``tests/`` or
    # ``scripts/``, so no figure moved -- it was a wrong rule sitting beside two
    # correct statements of the same question, which is what makes it worth
    # deleting rather than correcting.  **The cash anchor order is
    # :func:`app.services.cash_ledger.cash_anchor_facts`** (ascending) and
    # :func:`app.services.cash_ledger.resolve_anchor` (its exact reverse), both
    # ``(observed_on, created_at, id)``; ask them.  The loan twin went the same
    # way in the same step (``models/loan_anchor_event.py``).
    #
    # **It carries ``passive_deletes`` and NO ``delete-orphan``, and that is
    # what lets an account be deleted at all** (plan step X-f3c-2c).  An
    # assertion is append-only, so the ORM may not delete one -- and this
    # collection's ``cascade="all, delete-orphan"`` made
    # ``routes/accounts/crud.hard_delete_account`` do exactly that: it loaded
    # every assertion and emitted a DELETE per row on the way to deleting the
    # account, which
    # :func:`app.models.append_only.install_append_only_guards` refuses.
    # Measured: with the guard and this line unchanged, four cases fail,
    # ``test_clearing_link_schema.TestWhatDeletingOneCosts`` among them.
    #
    # ``passive_deletes=True`` leaves the disposal to
    # :class:`~app.models.mixins.AccountScopedMixin`'s ``ON DELETE CASCADE``,
    # which PostgreSQL executes without loading a row into the session -- the
    # same path ``AccountOpening`` and ``LoanAnchorEvent`` already take, and
    # they take it by having no relationship here at all.  ``delete-orphan``
    # goes with it because it cannot be honoured: an assertion cannot be
    # orphaned (``account_id`` is NOT NULL) and deleting one is the act the
    # guard exists to refuse.
    #
    # **``"all"`` and not ``True``, and the difference is measured.**  ``True``
    # only stops SQLAlchemy LOADING the collection; for children already in the
    # session it still nulls their foreign key on the parent's delete, which is
    # an UPDATE -- and the guard refuses that as loudly as it refuses a DELETE.
    # ``tests/test_models/test_append_only.py`` loads the collection first for
    # exactly this reason: an unloaded one passes either way, so a case that
    # did not load it would grade nothing.
    anchor_history = db.relationship(
        "AccountAnchorHistory",
        back_populates="account",
        passive_deletes="all",
    )
    # Self-referential collateral link.  ``remote_side`` marks the
    # referenced (asset) side; ``foreign_keys`` disambiguates from the
    # account's other FKs.  The ``secured_loans`` backref lists the
    # liabilities secured by this account (a Property can secure a
    # mortgage AND a HELOC).  ``passive_deletes`` lets the database-level
    # ``ON DELETE SET NULL`` null the link on asset deletion without the
    # ORM eagerly loading and rewriting each secured loan first.
    collateral_account = db.relationship(
        "Account",
        remote_side="Account.id",
        foreign_keys="Account.collateral_account_id",
        backref=db.backref("secured_loans", passive_deletes=True),
    )

    def __repr__(self):
        return f"<Account {self.name} ({self.id})>"


def _owner_declared_evidence_id() -> int:
    """Return the evidence a level the OWNER declared is held with.

    The column default for :attr:`AccountAnchorHistory.evidence_id`, and the
    whole of what an owner-typed figure can claim: ``uncorroborated`` --
    "nothing confirms it; the figure is taken at face value" -- which the enum
    defines as exactly what a first import is (plan step ``balance:X-bj-1``,
    developer ruling 2026-09-16).  The import door sets a bank row's evidence
    explicitly from what it solved; every other writer of this table is an
    owner declaring a balance and takes this.

    A callable rather than a literal because the id is a ``ref`` row's, read
    through the cache and never frozen into the schema.  Imported inside the
    call because ``ref_cache`` imports this module.
    """
    # Pylint: ``import-outside-toplevel`` (1/0) -- a real import cycle, not a
    # cost dodge; ``app/models/transaction.py`` takes the same shape.
    from app import ref_cache  # pylint: disable=import-outside-toplevel

    return ref_cache.statement_balance_evidence_id(
        StatementBalanceEvidenceEnum.UNCORROBORATED,
    )


class AccountAnchorHistory(AccountScopedMixin, CreatedAtMixin, db.Model):
    """THE LEVEL RELATION: every observation that an account held a balance on a day.

    **One relation for both observers, and that is ruling R-IS as built by
    plan step ``balance:X-bj-1`` (ruling R-JN; the forks ruled 2026-09-16).**
    A row says *account A held $B at the close of day D*, and who observed it
    is :attr:`statement_import_id`: a bank STATEMENT (the import that stated
    the figure and solved its day) or, when that column is NULL, the OWNER,
    who read their bank and typed it.  The two were held in unrelated places
    -- this table, and two columns on ``budget.statement_imports`` -- with no
    rule saying which governed, which was finding **N-314**; one relation with
    a declared evidence rank (:attr:`evidence_id`) closes that structurally,
    because there is no "which authority wins" branch left to write.

    **The source is the import key's nullability, and there is deliberately no
    typed ``source_id``.**  The key is not a proxy for the fact, it IS the
    fact: ``fk_anchor_history_statement_import_claim`` locks a row's
    :attr:`anchor_balance` to its import's own ``stated_balance``, so any row
    naming an import is a bank level by construction, and no third writer
    exists.  A typed column would copy ``statement_imports.source_id`` on bank
    rows and be a constant on the owner's -- a second home (rule 14) -- and
    PostgreSQL cannot pair it with a nullable key in a CHECK without freezing
    a ``ref`` id into the schema.  Precedent: ``pay_calendar:R-PC80``, absence
    is the discriminator when the stored shapes are disjoint.  A surface that
    wants a source label (the statements page's badge, the history card)
    derives it from the join.

    **A bank level goes with its file, and a delete cannot manufacture an
    owner row.**  Both import keys are ``ON DELETE CASCADE``: a placement is
    the file's conclusion, and a row that exists only because a line did
    states nothing once the line is destroyed (``bank_import:R-GG(e)``).  The
    append-only DELETE arm permits that cascade by the same end-state test it
    applies to an account's disposal (``app.append_only_infrastructure``); the
    audit log keeps the row.  This is why the relation is ONE table and not a
    base table with a statement subtype beside it: a subtype row cascading
    away would leave the base row standing as an owner-declared level and
    feed it to the cash reset, and SQL cannot say "the parent dies with this
    child".

    **A release is an APPENDED row in ``budget.anchor_releases``, never an
    edit here** (:class:`~app.models.anchor_release.AnchorRelease`).  A bank
    level is solved from the lines recorded at or before its day, so a later
    import recording such a line, or a delete removing one, withdraws it; the
    withdrawal names the level and its cause.  A level STANDS when no release
    names it.

    **What reads which rows, until the flip.**  The cash fold still RESETS at
    the owner's levels and at nothing else (``X-f3c-5`` deletes that reset),
    so every cash-side reader composes
    :func:`app.utils.balance_predicates.owner_declared_clause` -- one
    predicate, deleted with the reset.  The bank walk
    (``statement_import.fold_bank_balances``) anchors on STANDING BANK levels
    only, permanently: ``anchor + sum(lines)`` is exact only from the bank's
    own posted end-of-day figure, and a typed number may hold pending items.
    That restricts the domain of one derived figure to the rows it is about;
    it ranks nothing across sources, so R-IS's rule stands.

    **Two clocks, and only one of them dates anything.**  ``observed_on`` is
    the BUSINESS date -- the civil day the asserted balance was TRUE -- and it
    is what the balance engine partitions settled movements against (ruling
    R-DH).  ``created_at`` is the RECORDING instant, and its only remaining job
    is to order two assertions that share an ``observed_on`` so the last one
    recorded is that day's closing balance.  The loan side has carried the same
    split since Commit 16 (``LoanAnchorEvent.anchor_date`` beside its
    ``created_at``); the cash side is the half that never got it, and until
    2026-07-31 it derived the business date from the recording instant.  That
    derivation is what let an ordinary bookkeeping session subtract
    ``$4,001.42`` of already-cleared payments a second time (finding N-130).

    **It carries no pay period, and that is ruling R-EO** (plan step X-f1c3b).
    An assertion is a fact about a BANK -- "on day D, account A held $B" -- and
    it is true whatever the user's paychecks are scheduled to do.  A
    ``pay_period_id`` filed it under a BUDGETING artifact, on an
    ``ON DELETE CASCADE`` FK, so a pay-period operation could destroy the
    record of what the bank said: a schedule reset wiped all 78 of the
    developer's production assertions and wrote 9 fabricated replacements.  The
    column was also a CACHE of a derivation rather than a fact -- both posting
    reconciles derived a correction's period from ``observed_on`` and
    ``account_posting_service._anchors`` refused this column BY NAME -- and it
    was already WRONG on 2 of those 78 rows, whose stored period does not
    contain their own ``observed_on`` (finding N-168).  A reader wanting the
    period an assertion books in derives it from the day, which is ruling
    R-EA verbatim.

    **This table has NO uniqueness constraint, and that is ruling R-EQ** (plan
    step X-f1c4b).  It carried ``uq_anchor_history_account_period_balance_day``
    on ``(account_id, anchor_balance, observed_on)`` as the database-level
    backstop for ``true_up`` double-submits -- a network retry, a double-click,
    a back-and-resubmit.  **A key over the row's own values cannot do that job**:
    a transport retry and a deliberate re-assertion carry identical values by
    construction, so the index had to mis-classify one of them, and it
    mis-classified the correction.  Assert ``$500`` for a day, correct it to
    ``$600``, then re-assert ``$500`` for that day and the index rejected the
    third write while ``anchor_service`` reported it as saved and every surface
    kept rendering ``$600``.  Measured on the developer's own data, account 1
    carries 2-3 assertions on 3 of its 50 assertion days, so the shape is
    ordinary rather than exotic.

    The rule lives at the write door instead (:func:`stage_anchor_true_up`),
    where the question can be answered exactly: an assertion is refused only
    when it matches the assertion that currently GOVERNS.  What that costs and
    what it buys is stated in ``anchor_service``'s module docstring; what it
    costs THIS table is nothing, because a surplus row would be financially
    inert (a zero-delta anchor correction emits no legs, and two assertions of
    one balance replay to one balance).

    The key's own history, kept because it explains two other comments: it lost
    ``pay_period_id`` with the column at ruling R-EO, and its last term was
    ``((created_at AT TIME ZONE 'UTC')::date)`` until ``observed_on`` existed
    (finding N-133 / F12), which keyed a USER's day to a UTC one.  Each move was
    an attempt to make a content key mean what the door meant.

    ``idx_anchor_history_account`` survives and is not a uniqueness guard: it
    serves the per-account lookups (:func:`app.services.cash_ledger.resolve_anchor`
    and :func:`~app.services.cash_ledger.cash_anchor_facts`).

    **An assertion is (account, day, balance) and NOTHING else, and that is
    ruling R-ES** (plan step X-f1e2).  The row carried a free-text ``notes``
    column for the audit trail's "which door wrote this", and it answered
    nobody: no code in ``app/`` ever read it (an AST census of every ``.notes``
    attribute access finds a transfer and a salary tax checkpoint, and no
    anchor), 76 of production's 78 rows were NULL, and the 2 labelled rows were
    the only assertion their account carried.  Worse, it was a SECOND definition
    of a word the engine already owns --
    :func:`app.services.cash_ledger.cash_anchor_facts` marks the OPENING
    positionally (``is_opening = index == 0`` over
    ``(observed_on, created_at, id)``) and
    ``account_posting_service._anchors`` mapped that to the typed
    ``account_opening`` / ``account_trueup`` posting kinds, so a back-dated
    assertion could make the label and the flag name different rows.  What the
    column held is not lost: this table is in
    ``app.audit_infrastructure.AUDITED_TABLES``, so an INSERT's whole row --
    ``notes`` included -- is in ``system.audit_log`` wherever an audit row
    exists.  The migration states that record's two measured limits.

    The LOAN twin keeps its provenance and that is not a drift.
    :class:`~app.models.loan_anchor_event.LoanAnchorEvent` carries a typed
    ``source_id`` FK because it is READ -- ``loan_loaders`` tells a
    ``tracking_start`` from a ``user_trueup``, the write door scopes its
    duplicate compare per source, and the dashboard renders the label.  This
    table's two kinds of row are told apart by :attr:`statement_import_id`,
    which every reader that cares composes as a predicate (see above).
    """

    __tablename__ = "account_anchor_history"
    __table_args__ = (
        db.Index(
            "idx_anchor_history_account",
            "account_id",
            "created_at",
        ),
        # A bank level names ONE OF THIS ACCOUNT'S statements, structurally
        # (plan step ``balance:X-bj-1``): the composite key onto
        # ``uq_statement_imports_id_account`` is what makes a level solved
        # from another account's file unrepresentable.  ``MATCH SIMPLE``
        # (PostgreSQL's default) is what lets it sit beside a nullable
        # column: an owner-declared row satisfies it whatever ``account_id``
        # says.  CASCADE: the placement is the file's conclusion and goes
        # with it -- see the class docstring.
        db.ForeignKeyConstraint(
            ["statement_import_id", "account_id"],
            ["budget.statement_imports.id",
             "budget.statement_imports.account_id"],
            name="fk_anchor_history_statement_import_account",
            ondelete="CASCADE",
        ),
        # A bank level's FIGURE is its file's own claim, guaranteed rather
        # than maintained: the pair keys onto
        # ``uq_statement_imports_id_stated_balance``, so a level whose amount
        # differs from what its statement stated cannot be stored, and --
        # because a NULL ``stated_balance`` matches nothing -- an import that
        # states no balance cannot own a level at all.  That second property
        # is what ``ck_statement_imports_anchor_needs_a_claim`` used to say
        # in words on the other table.  The claim is the SOURCE; this row's
        # amount is a co-located key over it, the shape
        # ``bank_statement_lines.account_id`` takes against its import.
        db.ForeignKeyConstraint(
            ["statement_import_id", "anchor_balance"],
            ["budget.statement_imports.id",
             "budget.statement_imports.stated_balance"],
            name="fk_anchor_history_statement_import_claim",
            ondelete="CASCADE",
        ),
        # An import places its figure on AT MOST ONE day: the door writes one
        # level per import, and a withdrawn placement is never re-placed by
        # the same import (the NEXT import re-establishes a level from the
        # evidence then present).  The key makes that structural, and it is
        # what lets the statements page key a placement by import id.  NULLs
        # are distinct under a UNIQUE constraint, so the owner's rows are
        # untouched by it.
        db.UniqueConstraint(
            "statement_import_id", name="uq_anchor_history_statement_import",
        ),
        # **The SUPERKEY the clearing links target, and it does NOT revive
        # R-EQ** (plan step X-f3a-1, ruling **R-FL**).  A transaction and a
        # purchase each name the assertion whose statement showed them, through
        # a COMPOSITE foreign key over ``(account_id, id)`` so a link to another
        # account's statement is unrepresentable; PostgreSQL requires a UNIQUE
        # over exactly those columns before such a key may target them.
        #
        # It can reject NO row: ``id`` is the primary key, so every pair
        # containing it is already distinct.  That is the whole difference from
        # the CONTENT key R-EQ deleted -- that one keyed
        # ``(account_id, anchor_balance, observed_on)``, values a transport retry
        # and a deliberate re-assertion share by construction, so it had to
        # mis-classify one of them and it mis-classified the correction.
        db.UniqueConstraint(
            "account_id", "id", name="uq_anchor_history_account_id",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    anchor_balance = db.Column(db.Numeric(12, 2), nullable=False)
    # The civil day the asserted balance was TRUE, in the user's timezone --
    # the business date the whole anchor/settle partition turns on (ruling
    # R-DH).  NOT NULL because there is no honest assertion without one: a
    # balance that was true on no particular day cannot be compared against the
    # movements it does or does not already contain.  Backfilled from
    # ``(created_at AT TIME ZONE 'America/New_York')::date``, the derivation it
    # replaces, so no rendered figure moved on the day the column shipped.
    observed_on = db.Column(db.Date, nullable=False)
    # The civil day this assertion was ENTERED, in the user's timezone --
    # STORED and APPLICATION-supplied, which is finding **N-299** (developer
    # ruling 2026-08-25).
    #
    # It was derived from ``created_at`` until this column shipped, and the
    # backfill is that derivation verbatim
    # (``(created_at AT TIME ZONE 'America/New_York')::date``), so no rendered
    # caption moved on the day it landed.  What the derivation could not do is
    # be RIGHT: ``created_at`` is ``server_default=db.func.now()``, stamped by
    # PostgreSQL, while ``observed_on`` defaults to the APPLICATION's
    # ``display_today()``.  The balance-history card captions a row as
    # back-dated when the two differ, so the caption compared two clocks --
    # which made the weekly calendar sweep red on every matrix date from
    # 2026-08-10 (``time_machine`` fakes the Python clock and cannot fake
    # Postgres), and which in production mis-captions any true-up submitted
    # in the last second of a civil day as back-dated.
    #
    # **The split is a responsibility split, not a duplicate.**  ``created_at``
    # keeps the job this table's own docstring gives it -- ordering two
    # assertions that share an ``observed_on`` -- because a DAY has no
    # resolution to rank a bookkeeping session with.  This column is what a
    # reader SHOWS.  That is the distinction
    # :class:`~app.services.balance_at.CashAnchorRow` already stated between
    # ranking and showing; it stopped one step short of giving the shown value
    # its own clock.
    #
    # The default is a plain callable on the APPLICATION's clock, and every
    # caller that means something else states its own value.
    #
    # **A first version made this default CONTEXT-SENSITIVE**: it inspected the
    # INSERT's parameters and, where a caller had pinned ``created_at``,
    # derived the day from that instant instead.  Two independent adversarial
    # reviews killed it on 2026-08-25, on measurement rather than taste.
    # **1,807 tests passed with its pinned branch deleted**, so the branch was
    # ungraded.  ``Insert.from_select`` renders a Python-side default with no
    # row context, so a future backfill of this table would have stamped every
    # row with the deploy date and raised nothing.  And its only beneficiaries
    # were three test fixtures -- test-shaped logic living in a production
    # model, which is rule 13.
    #
    # The fixtures state the value themselves now
    # (``_test_helpers.add_anchor_history`` / ``override_anchor`` /
    # ``reassert_balance_on``), so the rule sits where the fixture author reads
    # it.  It used to add "a bulk ``UPDATE`` reaches no column default at all,
    # which is why ``tests/conftest.py``'s re-anchoring step writes both
    # columns"; plan step X-f3c-2c deleted that step and the bulk UPDATE with
    # it, because this table refuses one.
    recorded_on = db.Column(
        db.Date, nullable=False, default=display_today,
    )
    # How firmly this level is held: the WEAKEST LINK in the chain behind the
    # figure, over ``ref.statement_balance_evidence``
    # (:class:`~app.enums.StatementBalanceEvidenceEnum`).  NOT NULL: every
    # observation carries its evidence (ruling R-IS).  The import door sets a
    # bank row's from what it solved (``file_chain`` from the file's own
    # chain, ``corroborated`` from agreement with a recorded statement,
    # ``uncorroborated`` from nothing); an owner's row takes the default,
    # which is the bottom rung -- see :func:`_owner_declared_evidence_id`.
    # The rank is READ by the bank walk alone (``fold_bank_balances`` chooses
    # the strongest standing bank level per run) and shown by the badge; no
    # cash reader consults it.
    evidence_id = db.Column(
        db.Integer,
        # Named, so a database built by ``create_all`` (``init_database``'s
        # fresh path) and one built by the migration chain carry the same
        # constraint name and the migration's downgrade can drop it on either.
        db.ForeignKey(
            "ref.statement_balance_evidence.id",
            name="fk_account_anchor_history_evidence",
            ondelete="RESTRICT",
        ),
        nullable=False,
        default=_owner_declared_evidence_id,
    )
    # The statement that observed this level, or NULL for a level the owner
    # declared -- the source, stated once (see the class docstring for why it
    # is this key and not a typed column).  Its two composite keys are in
    # ``__table_args__``; this bare column carries no key of its own.
    statement_import_id = db.Column(db.Integer)

    # Relationships
    account = db.relationship("Account", back_populates="anchor_history")

    def __repr__(self):
        return f"<AnchorHistory account={self.account_id} balance={self.anchor_balance}>"


class AccountAnchorHistoryImmutableError(AppendOnlyViolation):
    """Raised when ORM code tries to UPDATE or DELETE a balance assertion.

    **The table was append-only by CONVENTION until plan step X-f3c-2c**, where
    its two siblings were append-only structurally -- finding **N-287**.  An
    assertion is the record of what a bank showed on a day, and that day is
    what every clearing link was recorded against (ruling **R-FL**), so a door
    that edited one would silently re-point cleared purchases at a statement
    that did not show them.

    **This is the NAMED half of the refusal, not the whole of it** (ruling
    **R-HY**).  The listener sees only ORM-mediated writes;
    ``budget.refuse_append_only_change`` -- the trigger
    :mod:`app.append_only_infrastructure` installs -- refuses every actor and
    every spelling, including the bulk ``query.update()`` the finding's own harm
    sentence names.  The ``ON DELETE CASCADE`` from ``budget.accounts`` is
    deliberately outside both: history goes with its account, and
    :attr:`Account.anchor_history` declares ``passive_deletes`` so the ORM
    leaves that disposal to the database.
    """


install_append_only_guards(
    AccountAnchorHistory, AccountAnchorHistoryImmutableError,
)
