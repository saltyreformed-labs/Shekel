"""
Shekel Budget App -- Shared Model Mixins

Centralizes column declarations that would otherwise repeat verbatim
across many model files.  SQLAlchemy mixins produce DDL identical to
inline column declarations; the only effect is to keep the canonical
definition in one place so a future change (e.g. timezone choice,
default precision) is a single edit instead of N edits.

Mixins are NOT registered in ``app/models/__init__.py`` -- they
represent shared declarations, not concrete tables.
"""

from datetime import date, datetime

from sqlalchemy.orm import declared_attr, validates

from app.extensions import db


class UserScopedMixin:
    """Owning-user foreign key for per-user data tables.

    Adds one column:

      ``user_id`` -- INTEGER NOT NULL, ``FK auth.users.id ON DELETE
                     CASCADE``.  Identifies the user who owns the row.

    **The CASCADE does not make a user deletable, and a first version of
    this line said it did** ("deleting a user cascades to every row they
    own").  A user holding transactions cannot be deleted at all:
    ``budget.transactions.user_id`` is ``ON DELETE RESTRICT`` (see
    :class:`app.models.transaction.Transaction` for that ruling), so the
    statement dies there.  Driven 2026-09-02 on a clone of the
    developer's DEV database -- 9 accounts, 63 pay periods, 1,057
    transactions -- at plan step ``pay_calendar:C13-a``'s revision:
    ``DELETE FROM auth.users WHERE id = 1`` raises ``violates RESTRICT
    setting of foreign key constraint "fk_transactions_user_id"``, under
    both referential-trigger orderings.

    *Two things a first version of this paragraph got wrong, and both are
    one mistake -- a measurement quoted without saying which side of a
    same-day schema change it was taken on.*  It quoted the
    ``transactions_account_id_fkey`` refusal, which is what the PRE-C13-a
    schema raises and not this one; and it called that database "a clone
    of production", where production holds 1,028 transactions and 1,057
    is the dev clone.

    Nothing in ``app/`` deletes a user, so this is the shape of a door
    nobody has built rather than a live defect -- but the CASCADE here
    describes what the column does when it is REACHED, and never a
    guarantee about the statement.

    Applied to the user-owned tables whose ``user_id`` is exactly this
    shape: a NOT NULL CASCADE FK with no ``unique`` qualifier and
    PostgreSQL's default constraint name.  Five ``user_id`` columns are
    deliberately EXCLUDED because their DDL differs:

      * ``ref.*`` per-user override rows (``AccountType``) -- the FK is
        ``ON DELETE RESTRICT`` and ``nullable=True`` (a NULL ``user_id``
        marks a seeded, system-owned row).
      * ``auth.user_settings`` / ``auth.mfa_configs`` -- 1:1 satellite
        tables whose ``user_id`` carries ``unique=True``.
      * ``budget.transactions`` -- the FK is ``ON DELETE RESTRICT``; see
        below.
      * ``salary.state_child_deductions`` -- its migration named the FK
        ``fk_state_child_deductions_user_id``, and production holds that
        name, so the mixin's default-named FK would diverge (plan#1858).

    ``Transaction`` HAS a ``user_id`` since plan step
    ``pay_calendar:C13-a`` (ruling **R-PC32**) -- until then it had none
    at all and was scoped by walking ``pay_period_id`` / ``account_id``
    -- but it is a FOURTH exclusion, on the same ground as the first:
    its ``ondelete`` is ``RESTRICT``, not ``CASCADE`` (developer,
    2026-09-02).  Reusing this mixin there was measured to convert
    ``DELETE FROM auth.users`` from refused into a statement that empties
    the database; the column comment on
    :class:`app.models.transaction.Transaction` carries the table of
    driven results and the ruling.

    DDL-ORDERING NOTE (differs from the end-positioned mixins below).
    SQLAlchemy renders mixin columns AFTER a class's own columns, so
    extracting this previously-second column moves ``user_id`` to the
    tail of the emitted ``CREATE TABLE``.  This is NOT byte-identical to
    the prior inline declaration -- unlike :class:`OptimisticLockMixin`
    et al., whose columns already sat at the table tail.  It is
    nonetheless safe here, and the correct verification standard for
    this mixin is **order-independent equivalence + an empty Alembic
    autogenerate diff**, not byte-identical DDL, because column ORDER is
    load-bearing nowhere in this project:

      * the test suite clones the Alembic-migrated ``shekel_test_template``
        (column order comes from the migration chain, not the model);
      * no code or test does positional row/column access on these
        tables (the ORM addresses every column by name);
      * the documented ``db.create_all()`` <-> migration alignment
        invariant is about CONSTRAINT NAMES and existence, never order.

    Alembic autogenerate compares columns by name, so the reorder
    produces no migration.  If the auth-FK policy ever changes (e.g. a
    different ``ondelete``, or an added FK index), it is now a single
    edit here instead of ~15.
    """

    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )


class AccountScopedMixin:
    """Owning-account foreign key for per-account satellite tables.

    Adds one column:

      ``account_id`` -- INTEGER NOT NULL, ``FK budget.accounts.id ON
                        DELETE CASCADE``.  Identifies the account a
                        history/feature/goal row belongs to; deleting the
                        account cascades to the row.

    Applied ONLY to tables whose ``account_id`` is exactly this shape: a
    NOT NULL CASCADE FK with no ``unique`` qualifier and the
    convention-generated FK name.  These ``account_id`` blocks are
    byte-identical across several per-account tables and form a
    duplicate-code clique that one-sided disables cannot resolve, so
    centralizing them here is the structural fix.

    EXCLUDES tables whose ``account_id`` differs:

      * ``loan_params`` / ``investment_params`` -- ``unique=True`` (1:1
        with the account): the unique-FK clique, centralized in the sibling
        :class:`AccountScopedUniqueMixin` rather than here.
      * ``transaction`` / ``transaction_template`` -- ``ON DELETE
        RESTRICT`` (a transaction must not silently vanish with its
        account).
      * ``interest_params`` -- carries an explicit ``fk_*`` constraint
        name, so a convention-named mixin FK would diverge.

    Same mid-table reorder + order-independence argument as
    :class:`UserScopedMixin` (column order is load-bearing nowhere here).
    """

    account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False,
    )


class AccountScopedUniqueMixin:
    """Owning-account foreign key for 1:1 per-account satellite tables.

    Adds one column:

      ``account_id`` -- INTEGER NOT NULL UNIQUE, ``FK budget.accounts.id
                        ON DELETE CASCADE``.  The ``unique`` qualifier makes
                        the satellite row one-to-one with the account (one
                        ``LoanParams`` / ``InvestmentParams`` per account);
                        deleting the account cascades to the row.

    The unique variant of :class:`AccountScopedMixin`.  Applied to
    ``loan_params`` and ``investment_params``, whose ``account_id`` blocks
    are byte-identical (NOT NULL, CASCADE, ``unique=True``, convention-
    generated names) -- the unique-FK clique :class:`AccountScopedMixin`
    documents deferring.  Same mid-table reorder + order-independence
    argument as :class:`UserScopedMixin` (column order is load-bearing
    nowhere; ``flask db migrate`` autogenerate sees no diff, and the
    per-table unique index name derives from the table at mapping time).
    """

    account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )


class SalaryProfileScopedMixin:
    """Owning salary-profile foreign key for per-profile child tables.

    Adds one column:

      ``salary_profile_id`` -- INTEGER NOT NULL, ``FK
                               salary.salary_profiles.id ON DELETE
                               CASCADE``.  Deleting the profile cascades
                               to the raise / deduction / calibration /
                               pay entry row.

    Applied to ``salary_raise``, ``paycheck_line`` and
    ``calibration_override``, whose ``salary_profile_id`` blocks are
    byte-identical and form a duplicate-code clique.  EXCLUDES
    ``pension_profile``, whose FK is ``ON DELETE SET NULL`` and
    ``nullable=True`` (a pension can outlive the linked salary profile),
    ``pay_stub``, whose FK is ``ON DELETE RESTRICT`` (a transcribed stub
    is a record, so a profile holding one cannot be deleted; plan step
    salary:S11-a), and ``salary_pay_entry``, whose migration named its FK
    ``fk_pay_entries_salary_profile_id`` where this one takes PostgreSQL's
    default name; production holds the migration's (plan#1858).

    Same mid-table reorder + order-independence argument as
    :class:`UserScopedMixin`.
    """

    salary_profile_id = db.Column(
        db.Integer,
        db.ForeignKey("salary.salary_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )


class SortOrderMixin:
    """User-controlled display-ordering column.

    Adds one column:

      ``sort_order`` -- INTEGER NOT NULL DEFAULT 0.  A user-assignable
                        rank used to order rows in dropdowns and lists;
                        lower sorts first, ties broken by name.

    Applied to every table whose ``sort_order`` is exactly this shape
    (accounts, categories, salary profiles, both template tables,
    paycheck deductions, tax brackets).  Like :class:`UserScopedMixin`
    this is a mid-table column, so extracting it reorders the column to
    the table tail; the same order-independence argument applies (see
    :class:`UserScopedMixin` -- order is load-bearing nowhere here, so
    the standard is order-independent equivalence + empty autogenerate
    diff, not byte-identical DDL).
    """

    sort_order = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )


class IsActiveMixin:
    """Soft-enable / archive flag.

    Adds one column:

      ``is_active`` -- BOOLEAN NOT NULL DEFAULT TRUE.  False archives the
                       row: it stops driving new work (an inactive
                       template generates nothing; an inactive account is
                       hidden) while its historical rows remain valid.

    Distinct from :class:`SoftDeleteOverridableMixin`'s ``is_deleted``:
    ``is_active`` is a forward-looking enable switch the user toggles,
    whereas ``is_deleted`` is a soft-delete tombstone.  Applied to every
    table whose ``is_active`` is exactly this shape.  Same mid-table
    reorder + order-independence argument as :class:`UserScopedMixin`.

    EXCLUDES :class:`~app.models.user.User`.  ``User`` inherits
    Flask-Login's ``UserMixin``, which defines ``is_active`` as a
    property returning ``True``.  ``User`` declares its ``is_active``
    Column inline so the class attribute overrides that property; routing
    it through this mixin instead would put ``UserMixin.is_active`` ahead
    of the mixin Column in the MRO and silently shadow the database
    column.  ``User.is_active`` therefore stays inline by design.
    """

    is_active = db.Column(
        db.Boolean, nullable=False, default=True,
        server_default=db.text("true"),
    )


class TimestampMixin:
    """Audit-trail timestamps for mutable rows.

    Adds two columns:

      ``created_at`` -- TIMESTAMPTZ NOT NULL DEFAULT NOW().  Set once
                        at INSERT time by the database default.
      ``updated_at`` -- TIMESTAMPTZ NOT NULL DEFAULT NOW(), refreshed
                        to NOW() on every UPDATE via SQLAlchemy's
                        ``onupdate`` hook.

    Use on tables where rows are edited after creation (most user
    data: accounts, transactions, settings, etc.).  For append-only
    history/event tables where ``updated_at`` would be misleading,
    use :class:`CreatedAtMixin` instead.
    """

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now(),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )


class CreatedAtMixin:
    """Single-timestamp variant for append-only history rows.

    Adds one column:

      ``created_at`` -- TIMESTAMPTZ NOT NULL DEFAULT NOW().

    Use on tables that record events at a moment in time and never
    update afterwards: anchor history, rate history, pay periods,
    salary raises, tax-year configurations, etc.  A separate
    ``updated_at`` would be misleading on these rows because they
    are not edited after the initial INSERT -- amendment is modeled
    as a new row, not an update of an existing row.
    """

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now(),
    )


class SoftDeleteOverridableMixin:
    """Override and soft-delete flags for canonical/shadow rows.

    Adds two columns -- both ``BOOLEAN NOT NULL DEFAULT FALSE``:

      ``is_override`` -- True when the row was manually edited and
                         must NOT be regenerated/overwritten by the
                         recurrence engine.
      ``is_deleted``  -- True when the row was soft-deleted by the
                         user; remains in the table so historical
                         queries and audit triggers see the full
                         lifecycle, but the cash valuation and
                         balance-relevant queries treat it as
                         absent.

    Used by :class:`Transaction` and :class:`Transfer`.  The columns
    are declared at class level (NOT via ``@declared_attr``) so the
    SQLAlchemy DDL is byte-identical to the pre-mixin inline
    declarations; ``flask db migrate --autogenerate`` against a
    migrated schema must produce an empty diff.

    Do NOT apply this mixin to (Transaction|Transfer)Template -- the
    template tables have ``is_active`` instead, with semantics that
    differ from soft-delete (an inactive template stops generating
    new rows but its historical rows remain valid).  Adding
    ``is_override`` / ``is_deleted`` to the template tables would be
    a schema change, not a refactor, and is out of scope for the
    duplicate-code cleanup the audit's Issue 1 names.
    """

    is_override = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    is_deleted = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )


class OptimisticLockMixin:
    """Optimistic-locking version counter for concurrently-edited rows.

    Adds one column plus the mapper configuration that activates
    SQLAlchemy's version-counter concurrency control:

      ``version_id`` -- INTEGER NOT NULL DEFAULT 1.  SQLAlchemy issues
                        ``UPDATE ... WHERE id = ? AND version_id = ?`` on
                        every flush of a dirty row, atomically increments
                        the counter in the same statement, and raises
                        ``StaleDataError`` when the rowcount is 0 -- i.e.
                        a concurrent commit already advanced the counter.

    Routes that mutate a row carrying this mixin MUST catch
    ``StaleDataError`` and surface a 409 Conflict (or a flash + redirect
    for full-page forms) so the loser retries against fresh state.  This
    is the commit C-18 / F-010 optimistic-locking contract.

    ``__mapper_args__`` is supplied via ``@declared_attr`` -- not a plain
    class attribute -- because declarative copies the mixin's
    ``version_id`` Column onto each subclass, and the ``version_id_col``
    mapper option must point at THAT subclass's own copy.  A plain
    ``{"version_id_col": version_id}`` dict would capture the mixin's
    original (unmapped) column and misconfigure every subclass.  The
    column itself is declared at class level (NOT via ``@declared_attr``)
    so the emitted DDL is byte-identical to the prior inline
    declarations; ``flask db migrate`` against a migrated schema must
    produce an empty diff.

    A model that needs its own ``__mapper_args__`` keys (e.g. polymorphic
    config) cannot use this mixin as-is -- it would have to merge the
    ``version_id_col`` entry into its own declared-attr.  None of the
    current optimistic-locked tables do.
    """

    version_id = db.Column(
        db.Integer, nullable=False, server_default="1",
    )

    @declared_attr
    def __mapper_args__(cls):  # pylint: disable=no-self-argument
        """Map the version counter so each subclass locks on its own copy.

        Pylint: ``no-self-argument`` -- declared_attr passes the mapped
        class, not an instance; the ``cls`` name is the
        SQLAlchemy-mandated convention here.
        """
        return {"version_id_col": cls.version_id}


def reject_settle_instant(value: date | None) -> date | None:
    """Return *value*, refusing a ``datetime`` where a civil DAY is required.

    **The ONE statement of finding N-179's rule**, consumed by the column's own
    ORM validator below and by :class:`app.services.settle_day.SettleDay`'s
    constructor -- which runs at the CALLER, so a wrong-typed day cannot even be
    packaged for a settle door, let alone reach a row.

    ``datetime`` subclasses ``date``, so a type annotation catches nothing and
    the value flows all the way to PostgreSQL, which coerces it into the
    ``DATE`` column on the SESSION clock -- UTC.  An instant at
    2026-03-04 04:30 UTC is 2026-03-03 23:30 Eastern, so the row stores
    2026-03-04: one day later than the user's civil day, silently, which is
    exactly the UTC-vs-display split ruling **R-DH (b)** exists to delete,
    reintroduced one layer down.  Measured during the X-f1 conversion: 16 test
    sites handed the seam an instant and 8 of them stayed GREEN while doing it,
    one writing a journal entry whose ``DATE`` column held
    ``2026-03-20T13:00:00+00:00``.

    Args:
        value: The candidate settle day, or ``None``.

    Returns:
        *value* unchanged, so the function composes into an assignment.

    Raises:
        TypeError: When *value* is a ``datetime``.  A programming error at the
            call site rather than user input -- no form can submit an instant
            into a date field -- so it is not a ``ValidationError``.
    """
    if isinstance(value, datetime):
        raise TypeError(
            f"settled_on must be a date, got datetime {value!r}.  A "
            "settle records the CIVIL DAY its money moved, and an instant "
            "handed here is truncated by PostgreSQL on the session clock "
            "(UTC), so an evening-Eastern settle would be filed on the "
            "following day.  Pass the user's civil day -- display_today(), or "
            "the day the bank showed."
        )
    return value


class SettleDatedMixin:
    """The day a row's money moved, and HOW that day is known.

    Adds three NULLABLE columns, and they are ONE fact in three parts -- the
    ASSERTION that this money moved, on this day, that is what kind of day it
    is, and that statement showed it:

      ``settled_on``           -- DATE.  The civil day the money moved.  NULL is
                                  the row's own invariant rather than a gap; see
                                  each model for which one (a transaction is
                                  dated exactly while it is in a settled status;
                                  a purchase is dated exactly when the bank has
                                  been seen to take it).
      ``settled_day_basis_id`` -- INTEGER, ``FK ref.settled_day_bases.id ON
                                  DELETE RESTRICT``.  WHICH KIND of day that is:
                                  ``observed`` / ``asserted`` / ``entered``
                                  (:class:`app.enums.SettledDayBasisEnum`).
                                  Paired to the day above by each table's own
                                  BICONDITIONAL check constraint, so a day with
                                  no basis and a basis with no day are both
                                  unstorable.
      ``reconciled_by_id``     -- INTEGER.  WHICH statement was seen to show this
                                  money -- an ``account_anchor_history`` row.
                                  Declared bare here because its foreign key is
                                  COMPOSITE over the table's own ``account_id``
                                  (ruling **R-FL**) and lives in each model's
                                  ``__table_args__`` beside the rest of that
                                  table's constraints.

    Used by :class:`~app.models.transaction.Transaction` and
    :class:`~app.models.transaction_entry.TransactionEntry`.

    **It exists because plan step X-az made the duplication a gate finding.**
    The two tables carried ``settled_on`` and ``reconciled_by_id`` separately for
    as long as both have existed; adding the basis column to each pushed the
    block past pylint's ``duplicate-code`` threshold, which is the gate saying
    what was already true -- these are the same three columns recording the same
    fact about two kinds of row.  Extracting them is the same cleanup
    :class:`SoftDeleteOverridableMixin` is.  (``TrackingVisibilityMixin`` was
    another until plan steps ``balance:X-bi-1`` and ``X-bi-1b`` sealed both of
    its columns on ``Transaction``: the two tables stopped treating them alike,
    so each declares its own, and a mixin with one consumer dissolved.)

    **The ``datetime`` refusal is now on BOTH tables, and that is a widening
    rather than a move** (finding **N-179**).  It was a ``@validates`` on
    ``Transaction`` alone, and ``TransactionEntry.settled_on`` had the identical
    exposure with no guard: ``datetime`` subclasses ``date``, so a type
    annotation catches nothing and PostgreSQL truncates the instant on the UTC
    session clock, filing an evening-Eastern purchase under the following day.
    A rule stated for one table and enforced on one table is a rule the second
    table does not have.

    ``settled_day_basis_id`` is the one column declared through
    ``@declared_attr``, because its foreign key carries a per-table NAME
    (``fk_transactions_settled_day_basis_id`` /
    ``fk_transaction_entries_settled_day_basis_id``) and a shared
    ``ForeignKey`` object would give both tables one constraint name.  The other
    two are class-level, so their DDL is byte-identical to the prior inline
    declarations and ``flask db migrate --autogenerate`` against a migrated
    schema produces an empty diff.
    """

    settled_on = db.Column(db.Date)

    @declared_attr
    def settled_day_basis_id(cls):  # pylint: disable=no-self-argument
        """Map the day's basis so each table names its own foreign key.

        Pylint: ``no-self-argument`` -- ``declared_attr`` passes the mapped
        CLASS, not an instance, and SQLAlchemy's own documented signature for
        the decorator names that parameter ``cls``.  The same disable
        :meth:`OptimisticLockMixin.__mapper_args__` carries for the same reason.

        Args:
            cls: The mapped class, supplied by ``declared_attr``.  Its
                ``__tablename__`` is what makes the constraint name per-table.

        Returns:
            The ``settled_day_basis_id`` :class:`sqlalchemy.Column` for *cls*.
        """
        return db.Column(
            db.Integer,
            db.ForeignKey(
                "ref.settled_day_bases.id",
                name=f"fk_{cls.__tablename__}_settled_day_basis_id",
                ondelete="RESTRICT",
            ),
        )

    reconciled_by_id = db.Column(db.Integer)

    @validates("settled_on")
    def _refuse_a_settle_instant(self, _key, value):
        """Refuse a ``datetime`` written to :attr:`settled_on`, on ANY path.

        The type guard lives on the COLUMN rather than only at the write door,
        and that placement is the point (finding **N-179**).  The seam refuses
        an instant it is handed, but nothing stopped a caller -- or a fixture --
        assigning the attribute directly, and PostgreSQL then truncates the
        instant on the UTC session clock in silence.  A validator fires for the
        constructor, for a plain ``txn.settled_on = ...``, and for every ORM
        write path, so the wrong type is a loud ``TypeError`` wherever it is
        written instead of a day that is wrong by one.

        It is not a fence with an allowlist and it hunts no call sites: the
        column simply does not accept the type.  The residual, stated because
        an unstated limit reads as stronger than it is: a bulk
        ``query.update({"settled_on": ...})`` bypasses the ORM attribute layer
        and is not seen here, the same boundary
        ``LoanAnchorEvent``'s append-only guard states for itself.

        Args:
            value: The candidate settle day.  (SQLAlchemy also passes the
            attribute name, always ``settled_on``, which this ignores.)

        Returns:
            *value* unchanged when it is a civil ``date`` or ``None``.

        Raises:
            TypeError: When *value* is a ``datetime`` (from
                :func:`reject_settle_instant`).
        """
        return reject_settle_instant(value)
