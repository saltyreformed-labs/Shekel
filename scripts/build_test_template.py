"""
Shekel Budget App -- Test Template Database Builder

Builds the per-pytest-worker template database (``shekel_test_template``
by default).  The script drops and rebuilds the database on every run
so two invocations produce a byte-identical end state; this is the
required property for the Phase 2 work in
``docs/audits/test_improvements/per-worker-database-plan.md``.

The template is the source for PostgreSQL ``CREATE DATABASE ...
TEMPLATE shekel_test_template`` clones made at the start of each
pytest session (and each pytest-xdist worker within a session).
Cloning a populated template is roughly two orders of magnitude
faster than re-running migrations + audit infrastructure + seed for
every session, which is what unlocks parallel pytest invocations
without the per-test ``TRUNCATE`` deadlock that the current shared
``shekel_test`` database suffers from.

Three steps, in order:

1. **Recreate** the template database.  Uses an admin DSN
   (``TEST_ADMIN_DATABASE_URL``, default ``postgresql:///postgres``)
   in autocommit mode because ``CREATE DATABASE`` cannot run inside
   a transaction.  ``DROP DATABASE ... WITH (FORCE)`` severs any
   lingering connections so a previously-orphaned clone or a leftover
   pytest worker connection does not block the rebuild.  PostgreSQL
   13+ required for ``WITH (FORCE)``.
2. **Populate** the template.  Creates the five user-facing schemas,
   runs the Alembic chain to ``head`` through the deploy's own runner,
   :func:`app.migration_runner.upgrade_to_head` (ruling R-BAL114, so a
   migration that cannot run inside the deploy's transaction fails the
   next template rebuild), refuses any table, column, constraint or index
   the chain builds differently from the models
   (:func:`scripts.schema_parity.refuse_schema_drift`, finding plan#1858;
   its module says what it does not compare),
   applies the audit infrastructure idempotently (so the LATEST
   in-code trigger definitions win over any migration-frozen state),
   and seeds reference data via :func:`app.ref_seeds.seed_reference_data`.
   Finally truncates ``system.audit_log`` so the audit rows the seed
   fires on ``ref.account_types`` (which is in
   :data:`app.audit_infrastructure.AUDITED_TABLES` after commit C-28)
   do not leak into the per-test clones, and the template ships a
   zeroed log -- per-test assertions on audit_log row count are then
   trivially true at clone time.  Last, it starts every non-ref table's
   ids in a range of their own, so no two kinds of id coincide in a
   clone (ruling ``balance:R-BAL356``).
3. **Verify** the populated template carries the expected state:
   ``ref.account_types`` row count equals the seed list size
   (:data:`_EXPECTED_ACCOUNT_TYPE_COUNT`),
   ``pg_trigger`` count of ``audit_%`` triggers equals
   :data:`app.audit_infrastructure.EXPECTED_TRIGGER_COUNT`
   (i.e. ``len(AUDITED_TABLES)``), and ``system.audit_log`` is
   empty.  Any mismatch raises :class:`RuntimeError` with a recovery
   hint; the script does not attempt to repair partial state.

Environment variables (read at module load before app import):

* ``TEST_ADMIN_DATABASE_URL`` (default ``postgresql:///postgres``):
  DSN with permission to ``CREATE DATABASE`` / ``DROP DATABASE``.
  Must point at a database OTHER THAN the template -- DROP DATABASE
  cannot run against the connection's own database.
* ``SECRET_KEY``: Used by ``create_app('testing')``.  Defaults to a
  non-production sentinel value if unset; the template database is
  never reachable through Gunicorn so the value is purely a
  placeholder needed for app construction.
* ``DATABASE_URL_APP``: Overridden to the empty string (= unset, per
  the config resolver's documented contract) -- the script needs DDL
  privileges (the owner role, ``DATABASE_URL``), not the
  least-privilege app role.  Matches ``scripts/init_database.py``.

Usage::

    python scripts/build_test_template.py

    # With an explicit admin DSN (TCP + password) ----
    TEST_ADMIN_DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5433/postgres \\
        python scripts/build_test_template.py
"""

import os
import sys
from urllib.parse import urlparse, urlunparse


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_ADMIN_URL: str = "postgresql:///postgres"
# Non-production placeholder.  The template database is never reachable
# through Gunicorn, so the value is purely scaffolding to satisfy the
# Flask config's ``_reject_sentinel`` and 32-character minimum.  Set
# only if the caller did not pre-populate SECRET_KEY (an operator who
# already exports a value should keep theirs).
_DEFAULT_SECRET_KEY: str = "build-test-template-key-32-characters-long-not-prod"
_REQUIRED_SCHEMAS: tuple[str, ...] = ("ref", "auth", "budget", "salary", "system")
# Sourced from ``app.ref_seeds.ACCT_TYPE_SEEDS`` -- the seed currently
# defines 19 account types (the 18 financial types plus the Property
# physical-asset type added by the home-equity mini-sprint).  The
# verification step asserts the count round-trips through the seed call
# so an accidental edit to the seed list (e.g. a deleted row) surfaces
# here, not in test failures.
_EXPECTED_ACCOUNT_TYPE_COUNT: int = 19
# How many ids each table's range holds; see
# :func:`_start_each_table_in_an_id_range_of_its_own`.
_ID_RANGE_WIDTH: int = 1_000_000
# The schemas whose tables are given ranges: every required schema but
# ``ref``, whose rows carry literal ids production shares (the function says
# why that excludes them).
_ID_RANGED_SCHEMAS: tuple[str, ...] = tuple(
    schema for schema in _REQUIRED_SCHEMAS if schema != "ref"
)


# ---------------------------------------------------------------------------
# Resolve configuration from environment and prepare the import path.
#
# These statements MUST run before ``from app import create_app`` below:
# ``app.config.TestConfig.SQLALCHEMY_DATABASE_URI`` reads
# ``TEST_DATABASE_URL`` at class-body evaluation time, which happens
# during the first ``app`` import.  Setting the variable after that
# import would leave the app pointed at whatever DSN was in the
# environment when the module was first loaded -- which is never the
# template DB this script just created.
# ---------------------------------------------------------------------------
ADMIN_URL: str = os.environ.get("TEST_ADMIN_DATABASE_URL", _DEFAULT_ADMIN_URL)
# THE TEMPLATE'S NAME IS A CONSTANT, NOT A KNOB (plan step
# ``balance:X-br-4``).  It was resolved from ``TEST_TEMPLATE_DATABASE`` --
# environment, then the repo ``.env`` -- so that two checkouts sharing ONE
# postmaster could each build a template at their own migration head without
# overwriting the other's.  ``scripts/test.sh`` now gives every run a private
# cluster, so there is one template per cluster and nothing to name it apart
# from.  ``tests/conftest.py`` and ``scripts/build_test_db_image.py``
# (``_TEMPLATE_DATABASE``, which its verifier reads) spell the same constant;
# all THREE must agree or the suite clones a template nobody built.
TEMPLATE_DB: str = "shekel_test_template"

# Build the template DSN by replacing the database name (the URL's
# path component) in the admin DSN.  Preserves scheme, host, port,
# user, password, query, and fragment so an admin DSN like
# ``postgresql://shekel_user:shekel_pass@localhost:5433/postgres`` becomes
# ``postgresql://shekel_user:shekel_pass@localhost:5433/shekel_test_template``.
_parsed_admin = urlparse(ADMIN_URL)
TEMPLATE_URL: str = urlunparse(_parsed_admin._replace(path=f"/{TEMPLATE_DB}"))

os.environ["TEST_DATABASE_URL"] = TEMPLATE_URL
os.environ.setdefault("SECRET_KEY", _DEFAULT_SECRET_KEY)
# Force the owner role -- the script needs DDL privileges (CREATE
# TABLE, CREATE TRIGGER, ...).  ``app/config.py`` prefers
# ``DATABASE_URL_APP`` over ``DATABASE_URL`` when both are set, which
# is correct for the runtime app (least privilege) but wrong here.
# Empty string rather than ``pop``: config.py's ``load_dotenv()``
# (override=False) re-inserts a ``.env`` ``DATABASE_URL_APP`` line
# into an absent key, defeating the override; an existing-but-empty
# key survives dotenv and the config resolver documents
# empty-as-unset (falls through to DATABASE_URL).  See the matching
# block in scripts/init_database.py.  The assignment is
# process-local; the parent shell's env is untouched.
os.environ["DATABASE_URL_APP"] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- the environment setup (TEST_DATABASE_URL,
# SECRET_KEY, the DATABASE_URL_APP override) and the sys.path bootstrap
# above must run before these imports: the app config reads the environment
# at import time, and ``app`` only resolves once the repo root is on sys.path.
# pylint: disable=wrong-import-position
import psycopg
from psycopg import sql

from app import create_app
from app.append_only_infrastructure import apply_append_only_infrastructure
from app.audit_infrastructure import (
    AUDIT_TRIGGER_COUNT_SQL,
    EXPECTED_TRIGGER_COUNT,
    apply_audit_infrastructure,
)
from app.level_infrastructure import apply_level_infrastructure
from app.migration_runner import upgrade_to_head
from app.sighting_infrastructure import apply_sighting_infrastructure
from app.pay_stub_infrastructure import apply_pay_stub_infrastructure
from app.deleted_row_infrastructure import (
    ALL_ARMS as DELETED_ROW_ARMS,
    apply_deleted_row_infrastructure,
)
from app.side_band_infrastructure import (
    ALL_ARMS as SIDE_BAND_ARMS,
    apply_side_band_infrastructure,
)
from app.extensions import db
from app.opening_infrastructure import ALL_ARMS, apply_opening_infrastructure
from app.posting_infrastructure import (
    apply_ledger_append_only_privileges,
    apply_posting_infrastructure,
)
from app.ref_seeds import seed_reference_data
from scripts.schema_parity import refuse_schema_drift


def _recreate_template_database() -> None:
    """Drop the template database (if present) and create a fresh empty one.

    Uses an admin connection in autocommit mode.  ``DROP DATABASE
    ... WITH (FORCE)`` (PostgreSQL 13+) severs any lingering
    connections so a previously-orphaned clone or a stuck pytest
    worker connection does not block the rebuild.  Identifier
    interpolation goes through :mod:`psycopg.sql` to defend against
    a future change that sources the template name from user input.
    """
    # psycopg 3's own idiom: the connection closes when the block ends, and
    # autocommit is set at connect because CREATE / DROP DATABASE cannot run
    # inside a transaction.
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(TEMPLATE_DB)
            )
        )
        cur.execute(
            sql.SQL("CREATE DATABASE {}").format(
                sql.Identifier(TEMPLATE_DB)
            )
        )


def _start_each_table_in_an_id_range_of_its_own() -> None:
    """Start every non-ref table's ids in a range no other table's ids share.

    Ruling ``balance:R-BAL356``.  Every identity sequence starts at 1, and a
    case's first user, scenario, account and period are each row 1 of their
    table, so in a fresh clone ids of different KINDS coincide: the user and
    the Checking account are both id 1.  A code path that writes or compares
    one kind of id where another is meant then gives the right answer by
    accident, and no case can tell.  The fixture has the coincidence by
    construction; production has it only where two counters happen to agree,
    so there the wrong id names a wrong row.  Review 1 of plan step
    ``balance:X-bi-6-4d-2`` (its MEDIUM-4) measured six account-id writes that
    no case in the suite caught: four for exactly this reason, and two log
    fields that no case asserts, which the ranges do not change.

    So the sequence at place *k* (from 1, in schema then name order, over
    :data:`_ID_RANGED_SCHEMAS`) hands out ``k * _ID_RANGE_WIDTH + 1`` first.
    In a clone as this template ships it, two non-ref tables' ids can then
    meet only if one case draws a million ids from one table, and every clone
    starts fresh, so the counters never carry from one case to the next; and
    no non-ref row holds an id at or below :data:`_ID_RANGE_WIDTH`, so a
    case's "no such row" literal (``999_999``) stays absent.  Two things
    leave that state on purpose, inside one case only: a restart (below), and
    a migration case that drops and re-creates a table, whose new sequence
    counts from 1.  A table added next year in one of these schemas is ranged
    the day it exists, because the sequences come from the catalog, not a
    list; a table in a NEW schema is not until the schema joins
    :data:`_REQUIRED_SCHEMAS` (the guard test, which scans every non-ref
    schema, fails until it does).  Adding a table moves the ranges after it
    in name order, which is harmless: a case that depends on a literal id is
    wrong whatever the ranges are.

    **``ref`` is excluded on purpose.**  Its rows are seeded with literal ids
    (``tests/test_models/test_ref_identity_sequences.py`` says why that leaves
    their sequences behind), and production holds the same ids, so two ref ids
    that coincide here coincide there too.  That is a fact of production, not
    an accident of the fixture.  A restart could not move them anyway: the
    rows are numbered before this runs.

    **The ranges have no bounds** (no ``MINVALUE`` / ``MAXVALUE``), because a
    case may restart a sequence ON PURPOSE to build a coincidence it then
    grades (``_transfer_numbered_like`` in
    ``tests/test_services/test_reconcile_transfer_legs.py``,
    ``_the_next_row_takes_id`` in
    ``tests/test_utils/test_auth_helpers/test_access_denials.py``, and
    ``test_the_door_settles_the_payments_row_and_not_the_row_sharing_its_id``
    in ``tests/test_services/test_cc5_4a1_settlement_subject/``), and a bound
    would refuse it.

    It runs LAST in :func:`_populate_template`, after every step that could
    create a sequence, so no sequence the template carries is missed.
    ``setval`` with ``is_called`` true makes the next value one past the given
    value.  The largest start stays inside ``integer`` while there are fewer
    than 2,147 sequences.
    """
    sequences = db.session.execute(
        db.text(
            "SELECT schemaname, sequencename FROM pg_sequences "
            "WHERE schemaname = ANY(:schemas) "
            "ORDER BY schemaname, sequencename"
        ),
        {"schemas": list(_ID_RANGED_SCHEMAS)},
    ).all()
    for place, (schema, sequence) in enumerate(sequences, start=1):
        db.session.execute(
            db.text(
                "SELECT setval("
                "CAST(format('%I.%I', CAST(:schema AS text), "
                "CAST(:sequence AS text)) AS regclass), "
                ":last, true)"
            ),
            {
                "schema": schema,
                "sequence": sequence,
                "last": place * _ID_RANGE_WIDTH,
            },
        )
    db.session.commit()


def _populate_template(app) -> None:
    """Materialise the schema, run migrations, re-apply the infrastructure, seed.

    Steps, in order:

    1. ``CREATE SCHEMA IF NOT EXISTS`` for each entry in
       :data:`_REQUIRED_SCHEMAS`.  Migrations expect the four
       user-facing schemas to exist; the rebuild migration creates
       the ``system`` schema conditionally but the others are assumed.
    2. :func:`app.migration_runner.upgrade_to_head`: the ONE runner
       ``scripts/init_database.py::migrate_existing_database`` calls too
       (plan step ``balance:X-cv``, ruling R-BAL114), on a connection
       this build hands over inside a transaction it opened -- the
       path the deploy takes, so a migration that cannot run inside the
       deploy's one transaction fails HERE, on every template rebuild,
       instead of at a release.  Then
       :func:`scripts.schema_parity.refuse_schema_drift` builds the
       models beside the migrated schemas and refuses the template when
       the two disagree on any table, column, constraint or index (finding
       plan#1858): ``scripts/init_database.py`` builds a fresh database
       from the models, so a difference is a schema a fresh install holds
       and production does not.  It runs BEFORE step 3 so it compares the
       chain with the models and nothing else; the infrastructure the
       later steps apply is triggers and functions, which it does not
       compare.  Because the
       connection is handed over, ``migrations/env.py`` leaves this
       process's logging to the app, as the deploy's (ruling R-BAL121):
       Alembic's per-revision lines print as the app's JSON log on
       STDOUT, not alembic.ini's plain text on stderr, and
       ``scripts/build_test_db_image.py`` reports stdout's tail with
       stderr when this build fails, so the revision that was running
       is still named.
    3. ``apply_audit_infrastructure``: idempotent re-application so
       the latest in-code trigger definitions win over any
       migration-frozen state.  Pulls in any trigger that was added
       to :data:`AUDITED_TABLES` after the rebuild migration was
       authored.
    4. ``apply_posting_infrastructure``: idempotent re-application of
       the balanced-journal constraint trigger (created by the
       Commit-3 migration in step 2) so the latest in-code trigger
       definition wins over any migration-frozen state -- the same
       robustness contract step 3 gives the audit trigger.  This is
       the test-suite caller for the posting infrastructure: the
       per-test ``db`` fixture clones this template, so the trigger
       enforced here is the one every test runs against.
    5. ``apply_opening_infrastructure``: idempotent re-application of
       the account-books boundary constraint (plan step X-f3c-2b,
       created by migration ``d3b6f1c8a274`` in step 2) -- the same
       latest-definition-wins contract steps 3-4 give the audit and
       balanced-journal triggers.  This is the test-suite caller: the
       per-test ``db`` fixture clones this template, so a fixture that
       dates a movement inside its account's opening equity aborts at
       COMMIT here exactly as production would.
    6. ``apply_append_only_infrastructure``: the append-only refusal on
       the account-history tables (plan step X-f3c-2c), re-applied under
       the same contract.
    7. ``apply_level_infrastructure``: a bank level lies inside its
       statement's file (plan step balance:X-bj-1), the same contract.
    8. ``apply_sighting_infrastructure``: a bank line goes with its last
       sighting (plan step bank_import:X-f6b-1), the same contract.
    9. ``apply_pay_stub_infrastructure``: a transcribed pay stub is never
       deleted and never moved (plan step salary:S11-a), the same
       contract.
    10. ``apply_deleted_row_infrastructure``: a deleted row takes no money
        (plan step credit_card:CC-5-4a-4), and
        ``apply_side_band_infrastructure``: a transfer side's record is dated
        exactly while its transfer is settled (plan step
        balance:X-bi-6-4d-2), the same contract.  Steps 4-10 are each a rule
        a FIXTURE can trip -- steps 4-7, 9 and 10 refuse a write, step 8
        deletes the line its last sighting leaves behind -- so the suite runs
        against the rules the app has.
    11. ``apply_ledger_append_only_privileges``: idempotent
        re-application of the ledger append-only posture (review
        M1/R4) -- a no-op unless the cluster-scoped ``shekel_app``
        role happens to exist at rebuild time.
    12. ``seed_reference_data``: populates ``ref.account_types`` (the
        :data:`_EXPECTED_ACCOUNT_TYPE_COUNT` built-in rows) and the other
        ref tables.  The INSERTs on ``ref.account_types`` fire the audit
        trigger attached in step 2/3 and write one row each into
        ``system.audit_log``.
    13. ``TRUNCATE system.audit_log``: clear those seed-time audit rows
        so the template ships with a zeroed log and every per-test clone
        of it starts from a clean slate (``tests/conftest.py``'s world
        builder truncates after its own seed for the same reason).
    14. :func:`_start_each_table_in_an_id_range_of_its_own`: every non-ref
        table's ids start in a range of their own, so no two kinds of id
        coincide in a clone (ruling ``balance:R-BAL356``).

    The audit, append-only, level, sighting, pay-stub and deleted-row
    trigger families are counted by one list,
    :func:`scripts.build_test_db_image.template_checks`: in the baked image,
    and on a first boot by
    ``tests/test_scripts/test_init_database_one_transaction.py``.  The
    posting (step 4) and opening (step 5) triggers are counted by neither:
    their modules export no constant naming their triggers (finding
    BAL-542).

    Args:
        app: Flask application built by ``create_app('testing')``.
            Must already be pointed at the template DSN via
            ``TEST_DATABASE_URL`` (set at module load above).
    """
    with app.app_context():
        for schema_name in _REQUIRED_SCHEMAS:
            db.session.execute(
                db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
            )
        db.session.commit()

        with db.engine.begin() as connection:
            upgrade_to_head(connection)

        # Its own connection, whose transaction closes unwritten: the
        # comparison builds the models inside a savepoint it rolls back.
        with db.engine.connect() as connection:
            refuse_schema_drift(connection, db.metadata)

        apply_audit_infrastructure(
            lambda statement: db.session.execute(db.text(statement))
        )
        db.session.commit()

        apply_posting_infrastructure(
            lambda statement: db.session.execute(db.text(statement))
        )
        db.session.commit()

        # The account-books boundary (plan step X-f3c-2b): idempotent
        # re-application, same contract as the two above.  Like the
        # balanced-journal trigger (and unlike the audit trigger, which
        # refuses nothing) it is a rule a FIXTURE can trip: a test that
        # settles a row on or before its account's opening day is building a
        # state production cannot hold, and this is what makes the suite say
        # so.
        # ``ALL_ARMS``: this builds a database at HEAD, which is the one
        # caller shape that wants whatever arms the module currently has.  A
        # MIGRATION names its arms literally instead -- see that constant.
        apply_opening_infrastructure(
            lambda statement: db.session.execute(db.text(statement)),
            arms=ALL_ARMS,
        )
        db.session.commit()

        # The append-only refusal on the account-history tables (plan step
        # X-f3c-2c; every one in APPEND_ONLY_TABLES): idempotent re-application, same
        # latest-definition-wins contract as the blocks above.  It matters
        # here for the reason the books boundary does -- this is a constraint
        # a FIXTURE can trip, and a suite that could not trip it would be
        # asserting against a database the app does not have.
        apply_append_only_infrastructure(
            lambda statement: db.session.execute(db.text(statement))
        )
        db.session.commit()

        # A bank level lies inside its statement's file (plan step
        # balance:X-bj-1): idempotent re-application, same contract as the
        # blocks above, and a constraint a FIXTURE can trip for the same
        # reason the two above are.
        apply_level_infrastructure(
            lambda statement: db.session.execute(db.text(statement))
        )
        db.session.commit()

        # A bank line goes with its last sighting (plan step
        # bank_import:X-f6b-1): idempotent re-application, same contract.
        apply_sighting_infrastructure(
            lambda statement: db.session.execute(db.text(statement))
        )
        db.session.commit()

        # A transcribed pay stub is never deleted and never moved (plan step
        # salary:S11-a, ruling R-SAL44): idempotent re-application, same
        # contract, and a refusal a FIXTURE can trip.
        apply_pay_stub_infrastructure(
            lambda statement: db.session.execute(db.text(statement))
        )
        db.session.commit()

        # A deleted row takes no money (plan step credit_card:CC-5-4a-4):
        # idempotent re-application of EVERY arm, the opening rule's HEAD
        # contract, and a rule a FIXTURE can trip -- one that stages a
        # movement under a hidden row or transfer.
        apply_deleted_row_infrastructure(
            lambda statement: db.session.execute(db.text(statement)),
            arms=DELETED_ROW_ARMS,
        )
        db.session.commit()

        # A transfer side's record is dated exactly while its transfer is
        # settled (plan step balance:X-bi-6-4d-2): the same HEAD contract.
        apply_side_band_infrastructure(
            lambda statement: db.session.execute(db.text(statement)),
            arms=SIDE_BAND_ARMS,
        )
        db.session.commit()

        # Ledger append-only posture (review M1/R4): idempotent
        # re-application for the same latest-definition-wins contract as
        # steps 3-4.  Almost always a no-op here -- the cluster-scoped
        # shekel_app role exists only while a role-privilege test is
        # running -- but keeps the template correct on a developer
        # cluster where the role is provisioned.
        apply_ledger_append_only_privileges(
            lambda statement: db.session.execute(db.text(statement))
        )
        db.session.commit()

        seed_reference_data(db.session)
        db.session.commit()

        # Clear the seed-time audit rows so the template ships with a
        # zeroed log: TRUNCATE after the reseed commits, then commit the
        # truncate separately.
        db.session.execute(db.text("TRUNCATE system.audit_log"))
        db.session.commit()

        # LAST, after every step that could create a sequence (its docstring
        # says why): ids of different kinds stop coinciding.
        _start_each_table_in_an_id_range_of_its_own()


def _verify_template_state() -> None:
    """Assert the template carries the expected row and trigger counts.

    Three assertions, each with a specific recovery hint:

    * ``ref.account_types`` row count equals
      :data:`_EXPECTED_ACCOUNT_TYPE_COUNT`.  Catches a seed list
      edit that removed or duplicated a row.
    * ``pg_trigger`` count of non-internal ``audit_*`` triggers
      (:data:`app.audit_infrastructure.AUDIT_TRIGGER_COUNT_SQL`, the
      query the deploy's check runs too) equals
      :data:`EXPECTED_TRIGGER_COUNT` exactly.  Catches a new table that was
      added to ``AUDITED_TABLES`` but whose trigger never attached
      (or, less likely, a stray trigger left over from a previous
      template that the DROP did not wipe).
    * ``system.audit_log`` row count equals 0.  Catches a missing
      TRUNCATE -- the template must ship with a clean log so every
      per-test clone starts from a known zero.

    Raises:
        RuntimeError: When any assertion fails.  The message names
            the offending count, the expected value, and a recovery
            hint pointing at the most likely root cause.
    """
    with psycopg.connect(TEMPLATE_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ref.account_types")
        account_type_count = cur.fetchone()[0]
        if account_type_count != _EXPECTED_ACCOUNT_TYPE_COUNT:
            raise RuntimeError(
                f"Template ref.account_types count is "
                f"{account_type_count}, expected "
                f"{_EXPECTED_ACCOUNT_TYPE_COUNT}.  Check that "
                "app.ref_seeds.ACCT_TYPE_SEEDS still has 19 entries "
                "and that seed_reference_data committed cleanly."
            )

        cur.execute(AUDIT_TRIGGER_COUNT_SQL)
        trigger_count = cur.fetchone()[0]
        if trigger_count != EXPECTED_TRIGGER_COUNT:
            raise RuntimeError(
                f"Template audit trigger count is {trigger_count}, "
                f"expected {EXPECTED_TRIGGER_COUNT} (from "
                "app.audit_infrastructure.AUDITED_TABLES).  A new "
                "AUDITED_TABLES entry without a matching table, "
                "or a stale trigger left behind, would cause this."
            )

        cur.execute("SELECT count(*) FROM system.audit_log")
        audit_log_count = cur.fetchone()[0]
        if audit_log_count != 0:
            raise RuntimeError(
                f"Template system.audit_log count is "
                f"{audit_log_count}, expected 0.  The post-seed "
                "TRUNCATE in _populate_template did not commit, or "
                "an unaudited write fired a trigger after the "
                "truncate."
            )


def main() -> int:
    """Drop, rebuild, populate, and verify the template database.

    Prints progress for each of the three phases plus a final
    summary line.  Exits 0 on success; failures propagate as
    exceptions (``psycopg.OperationalError`` on connect issues,
    Alembic errors on migration failure, :class:`RuntimeError` on
    verification failure).
    """
    print(f"Building test template database: {TEMPLATE_DB}")
    print(f"  Admin DSN:    {ADMIN_URL}")
    print(f"  Template URL: {TEMPLATE_URL}")

    _recreate_template_database()
    print("  Step 1/3: dropped and recreated empty database.")

    app = create_app("testing")
    _populate_template(app)
    print("  Step 2/3: migrated to head, applied audit, seeded reference data.")

    _verify_template_state()
    print(
        f"  Step 3/3: verified "
        f"({_EXPECTED_ACCOUNT_TYPE_COUNT} account types, "
        f"{EXPECTED_TRIGGER_COUNT} audit triggers, "
        f"0 audit_log rows)."
    )

    print(f"DONE: {TEMPLATE_DB} ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
