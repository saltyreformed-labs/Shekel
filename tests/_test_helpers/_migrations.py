"""Migrations under test: the revision files, the rewinds and restores of older schema shapes,
running a migration callable, loading a migration or ``scripts/init_database.py`` by path, and a
refusal by one of the database's own rules.
"""

import importlib.util

from contextlib import contextmanager

import pytest
from sqlalchemy import exc as sa_exc

from app.utils.db_errors import sqlstate_of
from tests._repo_root import REPO_ROOT


#: Plan step ``pay_calendar:C4-c``'s revision, whose ``downgrade()`` is the one
#: statement in this repository that re-creates ``budget.pay_periods.end_date``
#: and ``budget.pay_periods.period_index``.
_C4C_REVISION_FILE = "b7a41e2c9d63_a_pay_period_is_one_fact.py"


_C17A_REVISION_FILE = "6fc77e86d76f_a_pay_schedule_is_a_sequence_of_eras.py"


#: Plan step ``pay_calendar:C17-d-2``'s revision, whose ``downgrade()`` puts
#: ``budget.pay_eras.kind_id`` and ``ref.pay_cadence_kinds`` back -- the
#: objects ``C17-a``'s downgrade drops, so it runs FIRST in the rewind.
_C17D2_REVISION_FILE = "3ec5291ca4e2_a_pay_eras_kind_is_which_columns_it_carries.py"


#: Plan step ``salary:R18-a``'s revision, the RENAME of ``salary.paycheck_
#: deductions`` to ``salary.paycheck_lines`` (with ``ref.deduction_timings``
#: to ``ref.paycheck_line_kinds`` and the owning arm's column); its
#: ``downgrade()`` is the one statement that puts the old names back.
_R18A_REVISION_FILE = "0a4d2c3e89f8_a_paycheck_is_a_list_of_lines.py"


#: Plan step ``salary:R18-b``'s revision, which seeds the two EARNING kinds
#: whose names the pre-rename column cannot hold; its ``downgrade()`` runs
#: after S11-e's and before R18-a's in the rewind (Alembic's newest-first
#: order), and its ``upgrade()`` between them in the replay.
_R18B_REVISION_FILE = "6c15d2a97b78_a_paycheck_has_earning_lines.py"


#: Plan step ``salary:S11-e``'s revision, which seeded the all-payroll-taxes
#: pre-tax kind -- a name too long for the column R18-a's downgrade narrows,
#: so a rewind past R18-a takes it down first.
_S11E_REVISION_FILE = "47beea2b628a_pre_tax_splits_in_two.py"


def restore_pay_schedule_rhythm_columns(db_session):
    """Put the rhythm columns back on ``budget.pay_schedule``, for one older statement.

    :func:`restore_pay_period_derived_columns`' sibling, for the same reason.
    Plan step ``pay_calendar:C17-a`` moved ``cadence_days``, ``shift_id`` and
    ``nominal_anchor`` off the schedule row and onto ``budget.pay_eras``; four
    older migrations read or write the first of those on the row --
    ``af8254074bef``'s backfill, ``f1c8b3d5e920``'s, ``b7a41e2c9d63``'s
    downgrade and ``f2b7c40d918e``'s -- and a test that drives one of their
    callables against the test database meets ``UndefinedColumn`` where it
    used to find the schema it expected.

    **It adds the columns and fills them from each owner's LATEST era, and it
    does NOT run C17-a's ``downgrade()``, which is the difference
    :func:`relax_pay_schedule_shift_not_null` already argued for one column
    over.**  That downgrade also DROPS ``budget.pay_eras``, and every reader
    the head mapper has -- ``get_schedule``, ``calendar_for``, every fixture
    that derives a span -- selects that table; a test whose subject is an old
    statement reading the row's cadence would then break on every read it
    makes afterwards.  What the old statement needs is the COLUMN, so that is
    what comes back: three nullable columns, filled by the migration's own
    ``_RESTORE_RHYTHM_SQL`` (the shipped statement, not a copy), with the era
    table left standing and no key, CHECK or NOT NULL re-added.  A test that
    then deletes a schedule row must retire the owner's eras first, exactly
    as it must delete their paydays first.

    Idempotent (``ADD COLUMN IF NOT EXISTS``), because a round-trip case
    replays an older revision's ``downgrade`` twice.  Everything the sibling
    helper says about scope, locks and the absence of a restore applies
    unchanged, including ledger row ``balance:P79``.

    Args:
        db_session: The test ``db.session``, in the scope that currently holds
            the schedule table's locks.
    """
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import text

    db_session.commit()
    if _pay_schedule_carries_a_cadence(db_session):
        # Already restored -- by an earlier call, or by
        # :func:`rewind_pay_schedule_rhythm`, after which the era table this
        # would fill from is gone.
        return
    # Pylint: ``protected-access`` -- the migration's own restore statement,
    # so this helper cannot drift from what the shipped downgrade writes.
    restore_sql = load_migration_module(
        _C17A_REVISION_FILE,
    )._RESTORE_RHYTHM_SQL  # pylint: disable=protected-access
    db_session.execute(text(
        "ALTER TABLE budget.pay_schedule "
        "ADD COLUMN cadence_days INTEGER, "
        "ADD COLUMN shift_id INTEGER, "
        "ADD COLUMN nominal_anchor DATE"
    ))
    db_session.execute(text(restore_sql))
    db_session.commit()


def rewind_pay_schedule_rhythm(db_session):
    """Run plan step ``pay_calendar:C17-a``'s own ``downgrade()``.

    The WHOLE rewind, for the few cases whose subject is an older revision's
    statement against the schema exactly as that revision's chain leaves it:
    the rhythm columns back on ``budget.pay_schedule`` with their ``NOT
    NULL``, ``ck_pay_schedule_cadence_range`` and ``fk_pay_schedule_shift_id``
    re-added, and ``budget.pay_eras`` DROPPED with ``ref.pay_cadence_kinds``.
    A case that needs the CHECK to fire, or that drops ``budget.pay_schedule``
    itself, needs this; a case that only reads the cadence off the row wants
    :func:`restore_pay_schedule_rhythm_columns`, which leaves every head
    reader working.  Call it FIRST: that helper skips once the columns exist,
    and the restore statement it would run reads the table this drops.

    **It runs ``C17-d-2``'s downgrade before ``C17-a``'s** (plan step
    ``pay_calendar:C17-d-2``), Alembic's newest-first order: the head has no
    ``kind_id`` and no ``ref.pay_cadence_kinds`` for ``C17-a``'s downgrade
    to drop, and the later revision's downgrade is what puts them back.  It
    refuses a day-of-month era, as that downgrade does, and a schedule row
    holding no era, as ``C17-a``'s does.

    Args:
        db_session: The test ``db.session``, in the scope that currently holds
            the schedule table's locks.
    """
    run_migration_callable(
        load_migration_module(_C17D2_REVISION_FILE).downgrade, db_session,
    )
    run_migration_callable(
        load_migration_module(_C17A_REVISION_FILE).downgrade, db_session,
    )


def _pay_schedule_carries_a_cadence(db_session):
    """Return whether ``budget.pay_schedule.cadence_days`` exists right now."""
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import text

    return bool(db_session.execute(text(
        "SELECT 1 FROM information_schema.columns "
        " WHERE table_schema = 'budget' AND table_name = 'pay_schedule' "
        "   AND column_name = 'cadence_days'"
    )).scalar())


def rewind_paycheck_lines_rename(db_session):
    """Run plan step ``salary:R18-a``'s own ``downgrade()``: the old table names back.

    **For a test whose subject is an EARLIER revision's shipped SQL**, which
    names ``salary.paycheck_deductions``, ``ref.deduction_timings`` or
    ``budget.recurrence_rules.paycheck_deduction_id`` -- ``salary:R15-b``'s
    cadence migration, the C-42 index repair, ``salary:R14-a``'s funding
    backfill.  Head renamed all three (ruling **R-SAL38**), so those callables
    meet ``UndefinedTable`` where they used to find the schema they expected:
    :func:`rewind_pay_schedule_rhythm`'s shape, Alembic's newest-first order.

    What it leaves is head's schema with the three names restored -- every
    constraint, index, sequence and trigger under its old name, the two ref
    rows named ``pre_tax`` / ``post_tax`` -- and NOT any particular revision.
    The ORM models on this tree map the NEW names, so an ORM read of a line
    between this and :func:`replay_paycheck_lines_rename` finds no table;
    seed through the ORM first, rewind, drive the old statement in SQL, and
    replay before reading a line back through the model.

    **It runs S11-e's downgrade, then R18-b's, before R18-a's**: head carries
    the all-payroll-taxes kind S11-e seeded and the two earning-kind rows
    R18-b seeded, and R18-a's downgrade REFUSES to narrow the name column
    while a name longer than ten characters stands, which is the refusal
    ``test_r18a_paycheck_lines_rename`` grades; S11-e's and R18-b's
    downgrades in turn refuse while any row CARRIES their kinds, so a case
    that seeded one must delete it before rewinding.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            tables' locks (see :func:`run_migration_callable`).
    """
    run_migration_callable(
        load_migration_module(_S11E_REVISION_FILE).downgrade, db_session,
    )
    run_migration_callable(
        load_migration_module(_R18B_REVISION_FILE).downgrade, db_session,
    )
    run_migration_callable(
        load_migration_module(_R18A_REVISION_FILE).downgrade, db_session,
    )


def replay_paycheck_lines_rename(db_session):
    """Run plan step ``salary:R18-a``'s own ``upgrade()`` after a rewind, then R18-b's and S11-e's.

    :func:`rewind_paycheck_lines_rename`'s inverse: the head names back, so a
    test that drove an older revision's statement can read the result through
    the ORM models this tree maps.  Not needed for isolation -- the ``db``
    fixture re-clones the per-worker database for every test -- only for a
    case that reads lines back after the older statement ran.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            tables' locks.
    """
    run_migration_callable(
        load_migration_module(_R18A_REVISION_FILE).upgrade, db_session,
    )
    run_migration_callable(
        load_migration_module(_R18B_REVISION_FILE).upgrade, db_session,
    )
    run_migration_callable(
        load_migration_module(_S11E_REVISION_FILE).upgrade, db_session,
    )


#: Plan step ``recurrence:R5-a``'s revision, which dropped the rule's due day.
_R5A_REVISION_FILE = "1c569c51b449_a_rules_day_is_its_due_day.py"


def restore_rule_due_day_column(db_session):
    """Re-create ``budget.recurrence_rules.due_day_of_month``, which R5-a dropped.

    **For a test whose subject is an EARLIER revision's shipped SQL**, the
    reason :func:`restore_pay_period_derived_columns` exists, and built the
    same way: it runs plan step ``recurrence:R5-a``'s own ``downgrade()``
    (ruling R-R96) rather than issuing DDL of its own, so the column comes back
    with its CHECK exactly as the shipped statement rebuilds it -- nullable and
    empty.  Revision ``542c61e48ee8`` (plan step ``salary:R15-b``) reads the
    column in its own SQL, so a test driving that revision meets
    ``UndefinedColumn`` at head without it.  Alembic undoes the NEWEST revision
    first, so a caller rewinding further runs this before its older rewinds.

    It does not put the database at any particular revision.  The ORM model on
    this tree does not map the column, so an ORM insert leaves it ``NULL``.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            table's locks (see :func:`run_migration_callable`).
    """
    run_migration_callable(
        load_migration_module(_R5A_REVISION_FILE).downgrade, db_session,
    )


def restore_pay_period_derived_columns(db_session):
    """Re-create the two ``budget.pay_periods`` columns C4-c dropped.

    **For a test whose subject is an EARLIER revision's shipped SQL**, which is
    the only reason this exists.  Plan step ``pay_calendar:C4-c`` dropped
    ``end_date`` and ``period_index``; several migrations older than it read
    those columns, and a test that drives one of their callables against the
    test database therefore meets ``UndefinedColumn`` where it used to find the
    schema it expected.

    It runs that revision's own ``downgrade()`` rather than issuing DDL of its
    own, so the columns come back with the constraints, the NOT NULLs and --
    for every row already in the table -- the VALUES the shipped statement
    rebuilds.  A hand-written ``ADD COLUMN`` here would be a second statement of
    the schema that could drift from the migration without failing anything.

    **What it does NOT do, stated because the difference is the whole risk.**
    It does not put the database at any particular revision: everything else
    stays at head, so this is head's schema plus two restored columns.  That is
    enough for a test whose subject is a STATEMENT reading those two columns,
    and it is not enough for one whose subject is the whole schema at its own
    revision -- for that, every revision after it has to be undone in order,
    which is what Alembic does and what no helper here pretends to.  *The
    general shape -- migration tests in this suite drive their callables at
    HEAD rather than at the revision's own parent -- is a latent category error
    that C4-c is simply the first step to make fire; it is recorded as ledger
    row ``balance:P79`` rather than fixed inside this step.*

    No restore is needed afterwards: the ``db`` fixture drops and re-clones the
    per-worker database for every test, so a schema this leaves off head cannot
    reach the next one.

    **Call it on the session whose transaction is the one holding locks**, and
    in practice that means BEFORE entering a nested ``app.app_context()`` rather
    than inside one.  ``ADD COLUMN`` takes ACCESS EXCLUSIVE; Flask-SQLAlchemy
    scopes its session to the app context and a test already runs inside one,
    so a NESTED context gets a second session on a second connection while the
    outer one sits idle-in-transaction holding ACCESS SHARE on
    ``budget.pay_periods`` from whatever its fixtures last read.  The two
    conflict and the DDL dies on the cluster's 10-second ``lock_timeout`` --
    measured rather than predicted, and a confusing failure to meet cold.  The
    commit below ends the handed-in session's own transaction, which is every
    conflict this function can reach; a session in another scope is not
    addressable from here.

    Args:
        db_session: The test ``db.session``, in the scope that currently holds
            this table's locks.
    """
    # C4-c's downgrade rebuilds the stored end from
    # ``budget.pay_schedule.cadence_days``, which plan step
    # ``pay_calendar:C17-a`` moved onto the era relation -- so the schedule's
    # rhythm columns come back FIRST, or the statement this replays cannot.
    restore_pay_schedule_rhythm_columns(db_session)
    run_migration_callable(
        load_migration_module(_C4C_REVISION_FILE).downgrade, db_session,
    )


def relax_pay_schedule_shift_not_null(db_session):
    """Let ``budget.pay_schedule.shift_id`` be omitted, for one test.

    :func:`restore_pay_period_derived_columns`' MIRROR, and the mirror is the
    point.  That helper exists because a later revision REMOVED columns an
    older statement reads; this one exists because plan step
    ``pay_calendar:C14-b`` ADDED a ``NOT NULL`` column an older statement does
    not write.  Both break the same assumption -- that head's schema is a
    superset an old statement can still run against -- and it holds for READS
    and not for an insert into a table that has since gained a required
    column.

    Concretely: ``af8254074bef``'s backfill is
    ``INSERT INTO budget.pay_schedule (user_id, cadence_days) SELECT ...``, and
    at head PostgreSQL refuses that row with a ``NotNullViolation`` naming a
    column the statement predates by 95 revisions.

    **It drops the NOT NULL rather than running C14-b's ``downgrade()``, and
    the difference was measured rather than reasoned.**  The downgrade removes
    the COLUMN, and every reader in the test then breaks instead: the mapper at
    head still selects ``shift_id``, so the assertions that read the backfilled
    row back through the ORM die on ``UndefinedColumn``.  What the old
    statement needs is not the column's absence but permission to omit it, and
    that is the smaller change -- it leaves the column, the foreign key and
    every ORM reader intact.  Hand-written DDL here therefore duplicates no
    migration statement: it relaxes one constraint for the length of a test
    rather than restating a schema change.

    Everything :func:`restore_pay_period_derived_columns` says about scope
    applies unchanged -- the database is left at head minus one constraint
    rather than at any particular revision, no restore is needed because the
    ``db`` fixture re-clones per test, and the call belongs on the session
    holding the table's locks.  This is the same latent category error ledger
    row ``balance:P79`` records, met from the other direction.

    Args:
        db_session: The test ``db.session``, in the scope that currently holds
            this table's locks.
    """
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import text

    db_session.commit()
    db_session.execute(text(
        "ALTER TABLE budget.pay_schedule ALTER COLUMN shift_id DROP NOT NULL"
    ))
    db_session.commit()


def run_migration_callable(callable_, db_session):
    """Run one migration ``upgrade``/``downgrade`` against the test connection.

    **The one statement of this bootstrap.**  Eighteen files under
    ``tests/test_models/`` hand-copy a ``MigrationContext`` / ``Operations`` /
    ``patch.object(op, "get_bind")`` block; consolidating all of them is ledger
    row **P79**'s territory rather than a column drop's, so this is the copy
    the files that plan step ``pay_calendar:C4-c`` touches share, and it exists
    because that step had otherwise written a second one three lines from
    :func:`restore_pay_period_derived_columns`'s (adversarial review,
    2026-09-01).  The two had already drifted: one committed before configuring
    the context and the other relied on every caller having done so.

    It commits FIRST, and that is the lock rather than tidiness: DDL takes
    ACCESS EXCLUSIVE, and a session left idle-in-transaction holding ACCESS
    SHARE on the table blocks it until the cluster's ``lock_timeout``.
    Committing ends the handed-in session's own transaction, which is every
    conflict this function can reach; a session in another app-context scope is
    not addressable from here, so a caller inside a NESTED ``app.app_context()``
    must have committed the outer one.

    Args:
        callable_: The migration module's ``upgrade`` or ``downgrade``.
        db_session: The test ``db.session``, in the scope that holds the
            table's locks.
    """
    # Pylint: ``import-outside-toplevel`` -- alembic's operation plumbing is
    # needed by this one helper, and importing it at module scope would put it
    # in the import path of every test that touches this file.
    from alembic import op  # pylint: disable=import-outside-toplevel
    from alembic.operations import (  # pylint: disable=import-outside-toplevel
        Operations,
    )
    from alembic.runtime.migration import (  # pylint: disable=import-outside-toplevel
        MigrationContext,
    )
    from unittest.mock import patch  # pylint: disable=import-outside-toplevel

    db_session.commit()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection=connection)
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=connection):
            callable_()
    db_session.commit()


#: PostgreSQL's SQLSTATE for ``raise_exception``: what a PL/pgSQL ``RAISE
#: EXCEPTION`` with no ``ERRCODE`` reports, which is how every trigger and
#: rule function this schema installs refuses a write.
RAISE_EXCEPTION_SQLSTATE = "P0001"


@contextmanager
def refused_by_database_rule(match):
    """Expect the block to be refused by one of the DATABASE's own rules.

    The suite's one statement of "a trigger refused this" (ruling
    balance:R-BAL210, "Check the error code"): the refusal must carry
    PostgreSQL's SQLSTATE :data:`RAISE_EXCEPTION_SQLSTATE` and a message
    matching *match*.  It asserts the code rather than the DB-API exception
    CLASS because the class is the driver's choice and it moved with the
    driver: a ``RAISE`` is an ``InternalError`` under psycopg2 and a
    ``ProgrammingError`` under psycopg 3 (plan step balance:X-dj).  The code
    is the server's own answer, read through
    :func:`app.utils.db_errors.sqlstate_of`.

    Args:
        match: A regular expression the refusal's message must match
            (``pytest.raises``' ``match``).  Required: the code says a rule
            refused, the message says WHICH.

    Yields:
        The ``pytest.ExceptionInfo``, populated once the block has raised.
    """
    with pytest.raises(sa_exc.DBAPIError, match=match) as excinfo:
        yield excinfo
    assert sqlstate_of(excinfo.value) == RAISE_EXCEPTION_SQLSTATE, (
        f"refused with SQLSTATE {sqlstate_of(excinfo.value)!r}, not a "
        f"database rule's {RAISE_EXCEPTION_SQLSTATE}: {excinfo.value}"
    )


def constraint_name_from(exc):
    """Return the named constraint reported on an :class:`IntegrityError`.

    Reads ``exc.orig.diag.constraint_name`` -- the structured field psycopg
    surfaces from the PostgreSQL error packet -- so a test asserting WHICH
    CHECK fired does not depend on the brittle prose of the error message.

    Shared because it was written twice: the C-24 range/CHECK sweep and the
    ``salary:S3-b`` terminal-year suite carried byte-identical copies, and
    ``pylint app/`` never sees ``tests/``, so ``duplicate-code`` could not
    find it.

    Args:
        exc: The :class:`sqlalchemy.exc.IntegrityError` a refused write
            raised.

    Returns:
        The constraint name, or ``None`` when the driver reported none --
        which is itself a useful answer, since a NOT NULL violation and a
        named CHECK violation are different refusals.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    diag = getattr(orig, "diag", None)
    if diag is None:
        return None
    return getattr(diag, "constraint_name", None)


def load_migration_module(filename):
    """Load an Alembic migration module by filename via importlib.

    ``migrations/versions`` has no ``__init__``, so a migration cannot be
    imported as an ordinary package member.  This loads one by absolute path so
    a test can invoke a migration's module-level helpers directly (e.g. the
    posting-ledger backfill's ``_backfill_settled_transfers``).  Shared by the
    posting-ledger backfill suite and the Commit-6 reconciliation oracle so the
    importlib boilerplate lives in one place (a duplicate-code finding).

    Args:
        filename: The migration module's filename (e.g.
            ``"db239773c2fd_create_journal_entries_account_postings_.py"``),
            resolved under ``<repo>/migrations/versions``.

    Returns:
        The loaded migration module object.
    """
    versions_dir = REPO_ROOT / "migrations" / "versions"
    path = versions_dir / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def step_down_through_the_journal_link_drop(db_session):
    """Run revision ``1712c506ee99``'s downgrade: the journal entries' two old links come back.

    **The ONE statement of what a migration case below plan step
    ``balance:X-bi-6-5``'s second leaf does first** (card plan#1845).  The
    suite's database is built at head, where ``budget.journal_entries`` has no
    ``transfer_id`` or ``transaction_id`` and ``ref.posting_sources`` no
    ``transfer`` or ``transaction`` row, so a lower revision whose upgrade or
    downgrade names either -- the posting backfills, the shape-C split's
    unlink -- fails on it.  This puts back both columns (EMPTY: the revision
    deleted every entry that carried one), their keys, their two partial
    indexes and both reference rows, and the case then walks its own revisions
    exactly as it did before the drop.  The deleted entries are not re-created
    (ruling plan#1867).

    **It does not come back up**, deliberately: each case's database is its
    own and is dropped after it, and the upgrade would delete the legacy
    entries a revision below had written for the case to read.

    Args:
        db_session: The test ``db.session``.
    """
    run_migration_callable(
        load_migration_module(
            "1712c506ee99_a_ledger_entry_links_only_its_movement.py",
        ).downgrade,
        db_session,
    )


def step_down_through_the_transfer_column_drop(db_session):
    """Run revisions ``1712c506ee99``'s and ``c956c1d83108``'s downgrades, in that order.

    **The ONE statement of what a migration case below plan step
    ``balance:X-bi-6-5``'s column drop does first** (card plan#1844).  The
    suite's database is built at head, where ``budget.transactions`` has no
    ``transfer_id``, so a lower revision whose upgrade or downgrade names the
    column -- ``82f7b7ed4332``'s twins, ``c4a4e7d1b9f2``'s row-only deleted-row
    rule, ``596408fab6f1``'s pricing CHECK -- fails on it.  Every such case is
    below ``1712c506ee99`` too, so this steps down through it first
    (:func:`step_down_through_the_journal_link_drop` says what that restores),
    then through ``c956c1d83108``: that puts back the column (EMPTY: no row
    names a transfer), its key, its two indexes, its two CHECKs and the
    ``parent_transfer`` source, and the case then walks its own revisions
    exactly as it did before the drop.  The twin copy that revision dropped is
    not re-created (ruling plan#1851), and ``82f7b7ed4332``'s downgrade reads a
    missing copy as an empty one.

    **It does not come back up**, deliberately: each case's database is its
    own and is dropped after it, and the upgrade would drop a twin copy a
    revision below had kept for the case to read.

    Args:
        db_session: The test ``db.session``.
    """
    step_down_through_the_journal_link_drop(db_session)
    run_migration_callable(
        load_migration_module(
            "c956c1d83108_a_transaction_names_no_transfer.py",
        ).downgrade,
        db_session,
    )


def load_init_database_module():
    """Load ``scripts/init_database.py`` by path (it is not a package member).

    ``scripts`` has no ``__init__``, so the deploy host is loaded by absolute
    path -- the same importlib idiom :func:`load_migration_module` uses -- so a
    test can run its one-transaction deploy (``initialise_database``) or one of
    its pieces directly.  Loading it changes nothing in the environment: the
    owner-role override it once made at import went with the deploy's own
    configuration (:class:`app.config.DeployConfig`, which never reads
    ``DATABASE_URL_APP``).  Shared by the loan and account backfill suites so
    the importlib boilerplate lives in one place (a duplicate-code finding
    otherwise).

    Returns:
        The loaded ``init_database`` module object, exposing
        ``initialise_database`` (step 3 of the deploy, ``scripts/deploy.sh``, ONE
        transaction since plan step ``balance:X-cv``) and the pieces it runs.
    """
    script_path = REPO_ROOT / "scripts" / "init_database.py"
    spec = importlib.util.spec_from_file_location(
        script_path.stem, str(script_path),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
