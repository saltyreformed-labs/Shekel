"""
Shekel Budget App -- Database Initialization

Detects fresh vs. existing databases and initializes accordingly:

- Fresh DB: creates all tables via SQLAlchemy metadata, materialises
  the ``system.audit_log`` infrastructure (table, trigger function,
  and per-table audit triggers), then stamps Alembic to mark every
  migration as applied.  The audit-infrastructure step is the
  difference vs. ``db.create_all()`` alone -- the audit triggers,
  function, and table are raw SQL outside SQLAlchemy's model registry,
  so a bare ``create_all`` would skip them and the deploy's audit-trigger
  check (:func:`app.audit_infrastructure.require_audit_triggers`) would
  refuse the deploy.  See audit finding F-028 and remediation Commit C-13.

- Existing DB: runs incremental Alembic migrations, then the three
  deploy hooks that reconcile the posted ledger.  An existing DB
  that pre-dates Commit C-13 picks up the rebuild migration on the
  next ``flask db upgrade`` and the GRANT block inside the migration
  applies once the ``shekel_app`` role has been provisioned by
  ``scripts/init_db.sql``.

Both paths then run the same sequence (ruling R-BAL122): the reference
rows are seeded, ``ref_cache`` is loaded from them, the hooks run (an
existing database only), and the audit triggers are counted -- the work
entrypoint steps 4 and 7 used to do after this script had committed
(:func:`_bring_to_release`).  The same sequence seeded every user's missing
tax rows (its step 5; entrypoint step 6 before plan step balance:X-cv) until
plan step salary:X-at-1 gave the tax law one home in the code
(:mod:`app.tax_law`), which no deploy copies.

**All of it is ONE transaction, committed once** (plan step
balance:X-cv, ruling R-BAL105).  The deploy opens that transaction on a
connection of its own and commits it once, itself: the
migrations run on that connection (:mod:`app.migration_runner`), and
the app's session JOINS the transaction for everything else (rulings
R-BAL113, R-BAL118), so a hook that refuses, a migration that fails, a
seed that errors or a missing audit trigger leaves every row where the
deploy found it, ``alembic_version`` included (sequence counters, which
PostgreSQL does not roll back, still advance).  That stamp is what
``deploy/shekel-deploy.sh`` reads
to decide a rollback: unmoved, the previous image can still resolve
it, and the script re-pins that image on its own instead of refusing
and naming a dump to restore.

Database role policy:

    This script is part of the deployment pipeline -- not the
    application's request-time path -- so it always runs as the
    owner role (``DATABASE_URL``), never as the least-privilege app
    role (``DATABASE_URL_APP``).  ``DATABASE_URL_APP`` is overridden
    to the empty string (= unset, per the config resolver's contract)
    at the top of the file before ``create_app()`` reads it; this
    scopes the override to this process only and does not affect the
    Gunicorn process that ``entrypoint.sh`` exec's afterwards.

Usage:
    python scripts/init_database.py
"""

import contextlib
import os
import sys

# Force the owner role for this script.  ``app/config.py`` prefers
# ``DATABASE_URL_APP`` over ``DATABASE_URL`` when both are set, which
# is correct for the runtime app (least privilege) but wrong for
# this script (needs DDL: CREATE TABLE, CREATE TRIGGER, ...).
#
# Empty string rather than ``os.environ.pop``: config.py runs
# ``load_dotenv()`` at import time (override=False), which re-inserts
# a ``DATABASE_URL_APP`` line from a repo-local or bind-mounted
# ``.env`` into an absent key -- silently defeating a pop-based
# override (and, inside the dev container, pointing this script at a
# localhost DB that does not exist there).  An existing-but-empty key
# survives load_dotenv, and the config resolver documents
# empty-as-unset: it falls through to DATABASE_URL (covered by
# ``test_empty_database_url_app_falls_through``).  The assignment is
# process-local -- the parent shell's env is untouched, so ``exec
# gunicorn`` after this script still sees the real DATABASE_URL_APP
# and runs as the app role.
os.environ["DATABASE_URL_APP"] = ""

# The repository root, on sys.path for the imports below (sys.path[0] is
# scripts/ when this runs as a script; a test process loads it by path).
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

# Pylint: wrong-import-position -- the DATABASE_URL_APP override and the
# sys.path bootstrap above must run before these imports: the app config
# reads the environment at import time, and ``app`` only resolves once
# the repo root is on sys.path (sys.path[0] is scripts/ when invoked as
# ``python scripts/init_database.py``).
# pylint: disable=wrong-import-position
from sqlalchemy.orm import Session

from app import create_app, ref_cache
from app.audit_infrastructure import (
    EXPECTED_TRIGGER_COUNT,
    apply_audit_infrastructure,
    require_audit_triggers,
)
from app.deleted_row_infrastructure import (
    ALL_ARMS as DELETED_ROW_ARMS,
    apply_deleted_row_infrastructure,
)
from app.side_band_infrastructure import (
    ALL_ARMS as SIDE_BAND_ARMS,
    apply_side_band_infrastructure,
)
from app.extensions import db
from app.level_infrastructure import apply_level_infrastructure
from app.migration_runner import stamp_head, upgrade_to_head
from app.sighting_infrastructure import apply_sighting_infrastructure
from app.pay_stub_infrastructure import apply_pay_stub_infrastructure
from app.opening_infrastructure import ALL_ARMS, apply_opening_infrastructure
from app.append_only_infrastructure import (
    apply_append_only_infrastructure,
)
from app.posting_infrastructure import (
    apply_ledger_append_only_privileges,
    apply_posting_infrastructure,
)
from app.ref_seeds import seed_reference_data
from app.services import (
    account_posting_service,
    loan_posting_service,
    posting_service,
)
# pylint: enable=wrong-import-position


def is_fresh_database():
    """Return True when the application's auth schema is empty.

    "Fresh" is defined as the absence of ``auth.users``: every other
    schema in the project depends on it (FKs from budget/salary), so
    if it does not exist neither does anything else.  Returns False
    when the table is present, which signals "run incremental
    migrations" to the caller.
    """
    result = db.session.execute(db.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.tables "
        "  WHERE table_schema = 'auth' AND table_name = 'users'"
        ")"
    ))
    return not result.scalar()


def init_fresh_database(connection):
    """Create the schema, the integrity infrastructure, and stamp Alembic.

    The steps below run in order -- with the append-only, level-within-file,
    last-sighting, pay-stub and deleted-row blocks between 4 and 5, each
    documented where it runs -- every one on the deploy's ONE connection
    and none of them committing (plan step balance:X-cv):
    :func:`initialise_database` commits them together.  A failure part-way
    therefore leaves the database as empty as it found it, instead of a
    half-built schema that the next boot reads as "existing"
    (:func:`is_fresh_database` asks only for ``auth.users``) and tries to
    migrate from no stamp.

    1. ``db.metadata.create_all`` on that connection -- materialise every
       SQLAlchemy-modeled table.  This covers the ``ref``, ``auth``,
       ``budget``, and ``salary`` schemas.  Not ``db.create_all()``:
       Flask-SQLAlchemy runs that on a connection of its own and commits it.
    2. ``apply_audit_infrastructure`` -- materialise the
       ``system.audit_log`` table, the trigger function, the indexes,
       the per-table triggers (one per entry in
       :data:`app.audit_infrastructure.AUDITED_TABLES`), and the
       conditional ``shekel_app`` GRANT block.  ``db.create_all`` does
       not know about any of these -- they are raw SQL outside the
       SQLAlchemy model registry -- so this second step is what
       distinguishes fresh-DB initialisation post-C-13 from the
       previous bypass-of-audit-triggers behaviour that audit
       finding F-028 documents.
    3. ``apply_posting_infrastructure`` -- materialise the
       ``budget.assert_journal_entry_balanced`` function and the deferred
       ``ck_account_postings_balanced`` constraint trigger that enforces
       the per-journal-entry sum-to-zero / at-least-two-legs invariant.
       Like the audit trigger, these are raw SQL outside the model
       registry, so ``db.create_all`` (which made the
       ``budget.account_postings`` table) does not create them.
    4. ``apply_opening_infrastructure`` -- materialise
       ``budget.account_books_opened_on`` and the five deferred
       constraint triggers of every arm in ``ALL_ARMS`` that make a
       settled movement (plan step X-f3c-2b) or a matched bank line dated
       on or before its account's books open unstorable, graded from the
       row's side and from the opening's.  Raw SQL outside the model
       registry, exactly like the two above, so ``db.create_all`` does not
       produce it.  There is nothing to legalise on this path: the
       database is empty.
    5. ``apply_ledger_append_only_privileges`` -- revoke UPDATE/DELETE
       on the two ledger tables from ``shekel_app`` (review M1/R4).
       Required on this path specifically: ``init_db_role.sql`` ran
       BEFORE the tables existed (its table-guarded REVOKE skipped),
       and the stamp in step 6 marks the revoke migration
       (``e3c23fadb21d``) as applied without running it.
    6. ``alembic stamp head`` -- mark every migration as applied so
       subsequent ``flask db upgrade`` calls only apply
       newly-authored migrations.  On the same connection
       (:func:`app.migration_runner.stamp_head`), so the stamp commits
       with the schema.

    Args:
        connection: The deploy's connection, inside its one open transaction.
    """
    print("Fresh database detected. Creating all tables...")
    db.metadata.create_all(bind=connection)
    print("Tables created.")

    print("Materialising audit infrastructure (system.audit_log + triggers)...")
    apply_audit_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Audit infrastructure ready.")

    print("Materialising posting infrastructure (balanced-journal trigger)...")
    apply_posting_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Posting infrastructure ready.")

    print("Materialising books-boundary constraint (opening equity)...")
    # ``ALL_ARMS``: the fresh-database path materialises HEAD and never runs
    # the migration chain, so it wants whatever arms the module currently has.
    # A MIGRATION names its arms literally instead -- see that constant.
    apply_opening_infrastructure(
        lambda sql: db.session.execute(db.text(sql)),
        arms=ALL_ARMS,
    )
    print("Books-boundary constraint ready.")

    # The append-only refusal on the account-history tables (plan step
    # X-f3c-2c, ruling R-HY; every one in APPEND_ONLY_TABLES).  Same fresh-DB
    # reason as every block around it: the tables were created by
    # ``create_all`` above and the stamp below marks f4a7c2d9e51b applied
    # without running it, so this call is what installs the trigger on a
    # fresh database.
    print("Applying append-only refusal (account history tables)...")
    apply_append_only_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Append-only refusal ready.")

    # A bank level lies inside its statement's file (plan step balance:X-bj-1,
    # ruling R-GF): the cross-table bound that was a CHECK on the import row
    # while the placed day lived there.  Same fresh-DB reason as the block
    # above, same three-caller contract.
    print("Applying level-within-file bound (statement levels)...")
    apply_level_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Level-within-file bound ready.")

    # A bank line goes with its last sighting (plan step bank_import:X-f6b-1,
    # ruling R-BI10): the rule that makes a line no source stands behind
    # unrepresentable.  Same fresh-DB reason, same three-caller contract.
    print("Applying last-sighting rule (statement lines)...")
    apply_sighting_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Last-sighting rule ready.")

    # A transcribed pay stub is never deleted and never moved (plan step
    # salary:S11-a, ruling R-SAL44).  Same fresh-DB reason, same three-caller
    # contract: ``create_all`` made the four stub tables and the stamp below
    # marks 5641f7729b68 applied without running it.
    print("Applying pay-stub refusal (transcribed pay stubs)...")
    apply_pay_stub_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Pay-stub refusal ready.")

    # A deleted row takes no money (plan step credit_card:CC-5-4a-4, ruling
    # R-CC89): a payment or purchase arriving under a deleted row is refused.
    # Same fresh-DB reason, and every arm for the opening rule's reason: the
    # stamp below marks every revision that installs one applied without
    # running it, so this materialises HEAD.
    print("Applying deleted-row rule (payments and purchases)...")
    apply_deleted_row_infrastructure(
        lambda sql: db.session.execute(db.text(sql)), arms=DELETED_ROW_ARMS,
    )
    print("Deleted-row rule ready.")

    # A transfer side's record is dated exactly while its transfer is settled
    # (plan step balance:X-bi-6-4d-2): every arm, for the deleted-row rule's
    # reason above.
    print("Applying side-record band rule...")
    apply_side_band_infrastructure(
        lambda sql: db.session.execute(db.text(sql)), arms=SIDE_BAND_ARMS,
    )
    print("Side-record band rule ready.")

    # Ledger append-only posture (review M1/R4).  On the fresh-DB path the
    # tables were just created AFTER init_db_role.sql ran (its table-guarded
    # REVOKE skipped), and the Alembic stamp below marks the revoke migration
    # (e3c23fadb21d) as applied without running it -- so this call is what
    # closes UPDATE/DELETE for shekel_app on a fresh database.  A no-op when
    # the role does not exist; idempotent when it does.
    print("Applying ledger append-only privileges (shekel_app)...")
    apply_ledger_append_only_privileges(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Ledger append-only privileges ready.")

    # Stamp Alembic so it knows all migrations are "applied".
    stamp_head(connection)
    print("Alembic stamped to head.")


def migrate_existing_database(connection):
    """Run incremental Alembic migrations against a populated database.

    On the deploy's ONE connection, through the one runner the test-template
    build uses too (:func:`app.migration_runner.upgrade_to_head`, ruling
    **R-BAL114**), committing nothing: the chain, its stamp included, commits
    with the rest of the deploy's one transaction or rolls back with it.

    Args:
        connection: The deploy's connection, inside its one open transaction.
    """
    print("Existing database detected. Running migrations...")
    upgrade_to_head(connection)
    print("Migrations applied (they commit with the rest of this deploy).")


def resync_all_cash_postings_after_migration():
    """Re-date or re-book every settled cash source's postings after the chain is at head.

    Ruling **R-DH (b)** (2026-07-31,
    ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``).  A journal
    entry's ``entry_date`` is derived by
    ``balance_predicates.settled_day``, which moved from the UTC civil day
    to the USER's on that date, together with both folds that read it.  Every
    entry written before then carries the old day, so the STORED ledger and the
    readers disagree for any settle recorded between midnight UTC and the user's
    midnight -- on production, one ``$1,910.95`` mortgage payment stamped
    2026-07-02 00:38:53 UTC that belongs to the evening of 2026-07-01.

    Like its two siblings below this cannot run inside an Alembic migration: it
    needs ``ref_cache`` and the service layer, and this migration host builds the
    app with ``init_ref_cache=False`` (the pre-migration bootstrap window; see
    the ``3104f87`` deploy fix).  And like them it must NOT be a raw-SQL
    restatement of the dating rule -- one statement of "which civil day did this
    settle on" is the property the whole balance arc exists to hold, so it drives
    the go-forward sync instead
    (:func:`app.services.posting_service.resync_all_cash_postings`).

    **It runs FIRST of the three, and the order is the dependency direction.**
    The anchor walk computes each correction's ``ledger_before`` from the source
    postings on the account's linked ledger, so the sources are brought to target
    before the corrections that sit on top of them are reconciled.  (The anchor
    walk reads posting AMOUNTS grouped by source rather than their dates, so this
    ordering is defence rather than a live coupling -- stated so a later reader
    does not reorder it on the assumption that it is arbitrary.)

    Runs only on the existing-database path (a fresh database has no settled
    sources).  Idempotent and self-healing via reconcile-to-target, so it is safe
    on every deploy: a source already at target posts nothing.  Commits nothing
    itself: its re-posts commit with the migrations and the other two hooks in
    the deploy's ONE transaction (plan step balance:X-cv), where the deferred
    balanced-journal trigger validates every entry, so an unbalanced re-post
    aborts the deploy loud.  Until plan step ``X-bi-6-5`` the resync itself
    also refuses while any transfer still holds a nonzero legacy one-entry
    posting -- one whose family it had to skip (ruling **R-BAL104**).  Since
    X-cv that refusal rolls the migrations back with it, so the stamp stays
    where the previous image can resolve it and ``deploy/shekel-deploy.sh``
    re-pins that image on its own -- the manual dump restore ruling
    **R-BAL105** accepted until then is gone.  The release rehearsal on a
    same-day dump still meets it first.

    Returns:
        str: The line the deploy log prints once the one commit has landed --
        the count of sources CHANGED, not walked (finding N-133 / F8).  A
        steady-state deploy prints zeroes; a non-zero line is the operator's
        only evidence that a one-time re-date or re-book actually happened,
        and the one worth reading in the deploy log.
    """
    print("Resyncing settled cash postings (transactions + transfers)...")
    transactions, transfers = posting_service.resync_all_cash_postings()
    if transactions or transfers:
        return (
            f"Cash posting resync complete: RE-POSTED {transactions} "
            f"transaction(s) and {transfers} transfer(s); their journal "
            "entries were re-dated or re-booked.  To roll back past this "
            "deploy, follow deploy/shekel-deploy.sh's rollback instructions; "
            "it logs the pre-deploy dump it took."
        )
    return "Cash posting resync complete: already at target (0 changed)."


def backfill_loan_payment_postings_after_migration():
    """Post the historical loan genesis ledger after the chain is at head.

    Build-Order Step 4 + the read switch.  Posts every loan's opening, true-up,
    and confirmed-payment corrections.  This backfill cannot run inside an
    Alembic migration: it needs the ``ref_cache`` / service layer, and this
    migration host builds the app with ``init_ref_cache=False`` (the
    pre-migration bootstrap window; see the ``3104f87`` deploy fix), so
    ``ref_cache`` is off while migrations run.  Unlike the Step-2 / Step-3 cash
    backfills, the loan split is a running-balance walk over rate periods and
    effective-dated escrow -- not a one-line SQL formula -- so it cannot be
    reproduced in raw SQL without duplicating the money-critical split engine.
    So it runs HERE, once the chain has reached head and every ref row (the
    posting kinds / sources, the ledger-account kinds) and schema object the
    service needs exists -- on the ``ref_cache`` :func:`_bring_to_release`
    loaded after the reference seed, inside the same transaction, so it sees the
    ref rows the migrations and the seed wrote -- delegating to the idempotent
    :func:`app.services.loan_posting_service.backfill_all_loan_postings`.

    Runs only on the existing-database path (the fresh-database branch stamps
    Alembic without running migrations and has no loan payments to post).
    Idempotent and self-healing (reconcile-to-target), so it is safe on every
    deploy -- a payment already carrying a go-forward correction is at target
    and nothing is re-posted.  Commits nothing itself: the corrections commit in the deploy's
    ONE transaction (plan step balance:X-cv), where the deferred
    balanced-journal trigger validates every entry, so an unbalanced correction
    aborts the deploy loud.

    Returns:
        str: The line the deploy log prints once the one commit has landed.
    """
    print("Backfilling historical loan genesis ledger (opening/true-up/splits)...")
    posted = loan_posting_service.backfill_all_loan_postings()
    return f"Loan genesis-ledger backfill complete ({len(posted)} loan(s) reconciled)."


def backfill_all_account_anchor_postings_after_migration():
    """Post every non-loan account's anchor genesis ledger after the chain is at head.

    Build-Order Step 5, C7.  Posts every NON-loan account's opening and
    true-up anchor corrections (the equity counter-leg of each
    ``AccountAnchorHistory`` assertion), so after this the trial balance closes
    app-wide: every non-loan linked ledger sums to an ABSOLUTE balance.  Like
    the loan genesis backfill this cannot run inside an Alembic migration -- it
    needs the ``ref_cache`` / service layer, and this migration host builds the
    app with ``init_ref_cache=False`` (the pre-migration bootstrap window; see
    the ``3104f87`` deploy fix) so ``ref_cache`` is off while migrations run.
    Unlike the Step-2 / Step-3 cash backfills, an anchor correction is a
    moment-granular walk over the account's assertions against its linked
    ledger, not a one-line SQL formula, so it cannot be reproduced in raw SQL
    without duplicating that walk.  So it runs HERE, once the chain has reached
    head and every ref row (the ``account_opening`` / ``account_trueup``
    sources, and the ``anchor_equity`` / ``interest_income`` /
    ``unrealized_change`` ledger-account kinds with the ``Unrealized`` class
    ruling R-FO's dispatch books into) exists: it re-uses the ``ref_cache``
    :func:`_bring_to_release` loaded for all three hooks, then delegates to
    the idempotent
    :func:`app.services.account_posting_service.backfill_all_account_anchor_postings`.

    Runs only on the existing-database path (the fresh-database branch stamps
    Alembic without running migrations and has no accounts to post).
    Idempotent and self-healing (reconcile-to-target), so it is safe on every
    deploy -- an account already carrying its go-forward
    corrections is at target and nothing is re-posted.  Commits nothing itself:
    the corrections commit in the deploy's ONE transaction (plan step
    balance:X-cv), where the deferred balanced-journal trigger validates every
    entry, so an unbalanced correction aborts the deploy loud.

    Returns:
        str: The line the deploy log prints once the one commit has landed.
    """
    print("Backfilling historical account anchor ledger (opening/true-up)...")
    posted = account_posting_service.backfill_all_account_anchor_postings()
    return (
        f"Account anchor-ledger backfill complete "
        f"({len(posted)} account(s) reconciled)."
    )


def _reconcile_the_ledger():
    """Run the three deploy hooks, uncommitted, in the order the cash resync explains.

    They share the ONE ``ref_cache`` :func:`_bring_to_release` loaded.  Each
    hook used to roll back and re-load it on a fresh transaction of its own,
    which is what one transaction removes.

    Returns:
        list[str]: Each hook's line for the deploy log, in run order.
    """
    completed = [resync_all_cash_postings_after_migration()]
    completed.append(backfill_loan_payment_postings_after_migration())
    completed.append(backfill_all_account_anchor_postings_after_migration())
    return completed


def _bring_to_release(connection):
    """Bring the database to this release, uncommitted: schema, seeds, ledger, check.

    Ruling **R-BAL122** ("Seed first, one order"): a new database and an
    existing one run the same sequence, and only its first step differs.

    1. The schema to head: the first-boot build (:func:`init_fresh_database`)
       or the migrations (:func:`migrate_existing_database`).
    2. The reference rows (:func:`app.ref_seeds.seed_reference_data`): every
       row this release names that the database lacks is inserted, and the
       built-in account types' flags are brought to this release's values.
    3. ``ref_cache``, loaded ONCE, from those rows.
    4. The three deploy hooks (:func:`_reconcile_the_ledger`), on an existing
       database only: a new one has no ledger to reconcile.
    5. The audit-trigger check
       (:func:`app.audit_infrastructure.require_audit_triggers`).

    **Why the seed precedes the cache.**  ``ref_cache.init`` refuses a ref
    table that EXISTS without a row for one of its enum members.  A first
    boot's ref tables are empty until the seed fills them, so the cache read
    that entrypoint step 4 made before seeding (``scripts/seed_ref_tables.py``
    built the app with the eager init) stopped every new production install
    there, and every restart after it in step 3 (finding **BAL-536**).  On an
    existing database, reading the cache straight after the migrations also
    served as an alarm for a migration that forgot its own inline seed; with
    the seed first there is nothing for that alarm to protect -- the seed
    supplies the row before anything reads it -- and a migration that reads a
    row it forgot to seed STRICTLY (one row required, or a NOT NULL insert)
    still fails inside the chain, where the test-template build meets it
    first.  A lenient read (an ``INSERT ... SELECT`` or an ``UPDATE ... SET x =
    (SELECT ...)`` that finds nothing) would quietly do nothing there and in a
    deploy alike; no gate sees that case.

    Steps 2 and 5 were entrypoint steps 4 and 7 (the tax seed, entrypoint
    step 6 and then this sequence's step 5, was deleted at plan step
    salary:X-at-1), each run after step 3 had
    committed, so a failure in one left the release's stamp behind a dead
    container: the case ``deploy/shekel-deploy.sh`` cannot re-pin.  Here a
    failure in any step rolls back with the rest.

    Every line these steps print is printed before the commit, as the
    first-boot build's are: only :func:`initialise_database`'s closing line
    says the work landed.

    Args:
        connection: The deploy's connection, inside its one open transaction.

    Returns:
        list[str]: The hooks' lines for the deploy log (none on a first boot),
        printed by :func:`initialise_database` only once they have committed.
    """
    fresh = is_fresh_database()
    if fresh:
        init_fresh_database(connection)
    else:
        migrate_existing_database(connection)
    print("Seeding reference data...")
    seed_reference_data(db.session, verbose=True)
    ref_cache.init(db.session)
    completed = [] if fresh else _reconcile_the_ledger()
    found = require_audit_triggers(connection)
    print(
        f"Audit trigger health OK: {found} triggers "
        f"(expected >= {EXPECTED_TRIGGER_COUNT})."
    )
    return completed


@contextlib.contextmanager
def _session_joined_to(connection):
    """Make ``db.session`` a session JOINED to the deploy's transaction, for the block.

    Rulings **R-BAL113** and **R-BAL118**.  The hooks, ``ref_cache`` and
    :func:`is_fresh_database` all use the global ``db.session``.  For the deploy
    that name must mean a session whose every statement runs on *connection*,
    inside the transaction :func:`initialise_database` opened on it, and which
    cannot end that transaction.

    **Why not ``db.session.configure(bind=connection)``.**  Flask-SQLAlchemy
    3.1's ``Session.get_bind`` returns an engine and ignores a session-level
    bind (``flask_sqlalchemy/session.py``), so a configured bind would leave
    every query on a pooled connection of its own, outside the deploy's
    transaction.  So this puts a plain SQLAlchemy ``Session`` in the scoped
    session's slot for the current app context (``scoped_session.registry``)
    and takes it out again afterwards; the hooks' code is unchanged.  What the
    plain class lacks is Flask-SQLAlchemy's bind-key routing (this app has one
    database) and its ``Query`` subclass's ``*_or_404`` / ``paginate`` (request
    helpers no service calls).  The app's one session listener
    (:mod:`app.db_transaction`) is registered on SQLAlchemy's base ``Session``
    class, so it fires here as it does everywhere else.

    **Joined in ``rollback_only`` mode, with ``autobegin`` off.**  Joining the
    connection's open transaction means a session ``commit()`` is not passed to
    it, so the deploy's own ``commit()`` is the only one.  A session
    ``rollback()`` IS passed, so it ends the deploy's transaction and the
    deploy's commit then raises ``This transaction is inactive``.  With
    ``autobegin`` off the session holds exactly one transaction, the one begun
    here: after a stray ``commit()`` or ``rollback()`` its next statement raises
    ``Autobegin is disabled on this Session``.  With autobegin on, that
    statement would instead begin a transaction of the session's own on the
    connection, and a later ``commit()`` after a rollback WOULD commit it:
    the hooks' writes landing without the migrations the rollback undid
    (measured 2026-09-23 on a throwaway database, before ruling R-BAL118).

    The block BORROWS the app context's session slot.  Whatever session the
    context held is put back untouched when the block exits, so a caller's
    uncommitted work is neither committed nor discarded here.  In the deploy
    process the slot is empty, so it is simply cleared.

    Args:
        connection: The deploy's connection, inside its one open transaction.

    Yields:
        None: ``db.session`` is the joined session until the block exits.
    """
    registry = db.session.registry
    held = registry() if registry.has() else None
    session = Session(
        bind=connection, join_transaction_mode="rollback_only", autobegin=False,
    )
    session.begin()
    registry.set(session)
    try:
        yield
    finally:
        session.close()
        if held is None:
            registry.clear()
        else:
            registry.set(held)


def initialise_database():
    """Run entrypoint step 3 as ONE transaction and commit it once (plan step balance:X-cv).

    Ruling **R-BAL105**'s future step.  Before it the migrations committed in
    ``migrations/env.py``'s own transaction BEFORE the three deploy hooks ran,
    so a hook that refused (ruling **R-BAL104**'s legacy-net refusal, the loan
    sync's checked-projection assert, an unbalanced entry at a hook's commit,
    the anchor walk's refusals)
    left a stamp the previous image could not resolve, and
    ``deploy/shekel-deploy.sh`` refused to re-pin it: a manual dump restore.
    Now the deploy opens a connection and a transaction of its OWN, and the
    fresh-database build or the migrations, the reference seed,
    ``ref_cache``, the three hooks and the audit-trigger check
    (:func:`_bring_to_release`) all run inside it: Alembic on the connection,
    everything else through a session joined to it
    (:func:`_session_joined_to`).  The deploy's
    ``commit()`` below is the ONLY one.  Any exception before it propagates,
    the process exits non-zero, and closing the connection rolls the whole
    transaction back, ``alembic_version`` included.

    **Nothing the session or the migration runner does can commit, early or
    partially** (rulings **R-BAL113**, **R-BAL118**).  A session ``commit()``
    does not reach the deploy's transaction.  A stray session ``commit()`` or
    ``rollback()`` followed by any further statement on the session fails the
    deploy with nothing saved, and a ``rollback()`` after the last session
    statement makes this commit raise ``This transaction is inactive`` on an
    existing database (the audit-trigger count that follows runs on the
    connection, in a transaction of its own that closing the connection
    discards).  On a first boot the same rollback has also undone the schema,
    so that count finds no trigger and refuses first, with a message that
    blames a missing trigger rather than the rollback; nothing is saved
    either way.  Only a ``commit()`` after the last session statement is
    harmless, and it saves nothing this commit would not.  A rollback
    followed by Alembic rather than the session (the first-boot build's
    stamp comes after its nine session-routed infrastructure steps) meets
    :func:`app.migration_runner._config`, which puts the connection back
    inside a transaction nobody commits.  ``ref_cache.init`` rolls back when a
    ref table is missing (``_load_rows``), but since ruling R-BAL122 the
    reference seed reads every table the cache reads before it does (measured
    2026-09-23, again at salary:S11-a's merge, and 2026-09-24 at salary:X-at-1,
    which added ``ref.filing_statuses`` to the cache: its 29 tables are the
    seed's 29), so a missing table fails the seed first; the cache's
    rollback is unreachable from the deploy while that holds.  None of the
    three hooks' services, and not the seed, commits or rolls back (census
    re-run 2026-09-23: the two
    ``rollback()`` calls a hook's module holds --
    ``loan_posting_service._sync.sync_all_scenarios_or_duplicate`` and
    ``ref_cache._state._load_rows`` -- are the rate-history door's and the
    missing-table path; their SAVEPOINTs are ``begin_nested`` blocks, which
    never end the deploy's transaction; the seed's only commit is its
    script's own wrapper, which the deploy does not call).

    **What neither can see is a second CONNECTION, a CONNECTION-level commit,
    or a raw ``COMMIT``.**  A hook that opened a connection of its own
    (``db.engine.connect()``) would run outside the deploy's transaction
    altogether; ``db.session.connection().commit()`` or a migration's
    ``op.get_bind().commit()`` would commit the deploy's transaction in place;
    and a ``COMMIT`` sent as SQL would end it at the server without SQLAlchemy
    knowing.  The census (2026-09-23, re-run at salary:S11-a's merge, over
    ``app/`` and ``migrations/versions/``, which run inside the transaction,
    and over ``scripts/seed_tax_brackets.py`` until plan step salary:X-at-1
    deleted it) finds none of the three: no
    ``db.engine``, ``create_engine`` or ``engine.connect``, no ``.commit()`` on
    a connection or bind, and no ``COMMIT`` / ``ROLLBACK`` statement text.

    The session is FLUSHED before the commit, never committed: its pending ORM
    state reaches the connection, and the deploy commits the connection.
    """
    with db.engine.connect() as connection:
        deploy = connection.begin()
        with _session_joined_to(connection):
            completed = _bring_to_release(connection)
            db.session.flush()
        deploy.commit()
    print("Database initialised: ONE transaction, committed.")
    for line in completed:
        print(line)


if __name__ == "__main__":
    # init_ref_cache=False: this migration host builds the app only for an
    # Alembic context and runs BEFORE the migrations seed new ref rows, so the
    # strict ref_cache row-check must not fire on the pre-migration database
    # (it raises on a missing row in an existing ref table -- the exact
    # bootstrap window a row-adding migration like Step 3's creates).
    flask_app = create_app(init_ref_cache=False)
    with flask_app.app_context():
        initialise_database()
