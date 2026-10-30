"""C-41 / F-069: create uq_scenarios_one_baseline partial unique index in prod

Closes audit finding F-069 (commit C-41 of the 2026-04-15 security
remediation plan) and the production side of H-2 in
``shekel-audits/security-2026-04-15/model-migration-drift.md``.

The partial unique index ``uq_scenarios_one_baseline`` on
``budget.scenarios (user_id) WHERE is_baseline = TRUE`` was declared
by the upgrade body of
``c5d6e7f8a901_add_positive_amount_check_constraints.py`` (migration
#7 in the chain) but the production database never carried it.  The
production schema was bootstrapped via
``scripts/init_database.py`` -> ``db.create_all()`` -> Alembic
``stamp head`` (per the rebuild-audit-infrastructure migration's
docstring), which materialises the schema from model declarations
rather than running the migration chain.  At bootstrap time the
``Scenario`` model in ``app/models/scenario.py`` did NOT declare the
partial unique index inline, so ``db.create_all()`` produced a
scenarios table without it.  The index was added to the model only
in commit ``709786a`` (2026-05-10, H-2 model-side fix), well after
production had already been bootstrapped, so the production schema
remained drifted.

H-2 of the drift catalogue closed the test-side gap (the model now
declares the index, so the test-template path via ``flask db upgrade``
materialises it on every per-pytest-worker DB clone), but production
still lacks the storage-tier backstop.  Without the index the
``budget.balance_calculator`` would silently pick one of two baseline
scenarios per user when computing projections, producing different
answers depending on which ``Scenario`` row the ORM returned first --
a load-bearing correctness bug because every balance projection
starts from "the user's baseline scenario."

The route layer already enforces idempotency in two places:
``app/routes/grid.py::create_baseline`` checks for an existing
baseline before inserting a new one, and
``app/services/auth_service.py::register_user`` creates the user's
canonical baseline exactly once at sign-up.  In normal operation no
duplicate baselines can be created.  The partial unique index is the
storage-tier backstop against any future caller -- a script, a
hypothetical RPC handler, a concurrent registration race -- that
bypasses those route-layer checks.  Defense in depth: the route
checks catch "almost all" duplicates; the index catches "every"
duplicate.

The findings catalogue (F-027 -- duplicate CHECK constraints on
``budget.transactions``) was originally bundled with F-069 into this
commit per the plan.  The CHECK-duplication half was closed earlier
by migration
``724d21236759_drop_redundant_transaction_check_.py`` (commit
``9a5cca1``, 2026-05-10), so this migration only handles the F-069
side.  See
``shekel-audits/security-2026-04-15/model-migration-drift.md`` H-1 for
the duplicate-CHECK history.

Pre-flight duplicate-baseline detection refuses the upgrade if any
user already carries two or more baseline scenarios.  Auto-resolving
duplicates would mean choosing one to keep -- a financial decision
the migration cannot make on the operator's behalf (the wrong choice
would silently change the user's balance projections).  The operator
must reconcile duplicates by hand after confirming which scenario
holds the user's intended baseline data.

The DDL step is guarded by :func:`_index_exists` so the migration is
idempotent: a no-op against the test-template path (which already
carries the index from ``c5d6e7f8a901``) and a real cleanup on a
fresh-from-migrations production database.

A post-creation shape check verifies the index is unique and has the
expected ``WHERE is_baseline = ...`` partial predicate.  An index
carrying the right name but the wrong predicate (e.g.,
hand-recreated without the WHERE clause) would silently downgrade
the constraint to "at most one Scenario row per user" -- breaking
the multi-scenario product entirely -- so the shape check refuses to
silently accept that state.

The upgrade body delegates to module-level helpers
(``_preflight_duplicate_baselines``, ``_create_index_if_missing``,
``_assert_index_shape``) so the regression tests in
``tests/test_models/test_c41_baseline_unique_migration.py`` can
exercise each step against the live test database without spinning
up an Alembic ``MigrationContext`` -- the helpers accept a SQLAlchemy
``Connection`` rather than calling ``op.get_bind()`` directly.

Revision ID: a80c3447c153
Revises: 2109f7a490e7
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "a80c3447c153"
down_revision = "2109f7a490e7"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_scenarios_one_baseline"
TABLE_NAME = "scenarios"
SCHEMA_NAME = "budget"
INDEX_COLUMN = "user_id"
INDEX_WHERE_CLAUSE = "is_baseline = true"

DUPLICATE_DETECTION_SQL = (
    "SELECT user_id, "
    "       array_agg(id ORDER BY id)          AS scenario_ids, "
    "       array_agg(name ORDER BY id)        AS names, "
    "       array_agg(created_at ORDER BY id)  AS created_at_values "
    "FROM   budget.scenarios "
    "WHERE  is_baseline = TRUE "
    "GROUP  BY user_id "
    "HAVING count(*) > 1 "
    "ORDER  BY user_id"
)


def _index_exists(bind, name: str, schema: str) -> bool:
    """Return True iff an index named ``name`` exists in ``schema``.

    Queries ``pg_indexes`` (PostgreSQL's catalog view) directly rather
    than ``information_schema.statistics`` because the partial WHERE
    clause is only visible through ``pg_indexes.indexdef``; using the
    same view for the existence check and the shape check keeps the
    two reads consistent against the same catalog snapshot.

    Args:
        bind: A SQLAlchemy ``Connection`` (or any object exposing
            ``execute(text(...))``) bound to the database under
            inspection.  The migration's :func:`upgrade` supplies
            ``op.get_bind()``; tests pass ``db.session.connection()``.
        name: The index name to look up (case-sensitive).
        schema: The schema the index belongs to.

    Returns:
        True if PostgreSQL has an index with this exact name in this
        exact schema, False otherwise.
    """
    return bool(bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_indexes "
            "  WHERE schemaname = :schema AND indexname = :name"
            ")"
        ),
        {"schema": schema, "name": name},
    ).scalar())


def _index_definition(bind, name: str, schema: str) -> str | None:
    """Return the ``pg_get_indexdef``-style CREATE INDEX string, or None.

    Used by the post-creation shape check to confirm the partial
    WHERE clause is the expected ``is_baseline = ...``.  Returns
    ``None`` if the index is absent.

    Args:
        bind: A SQLAlchemy ``Connection`` exposing ``execute``.
        name: The index name to look up.
        schema: The schema the index belongs to.

    Returns:
        The raw ``indexdef`` string PostgreSQL stores in
        ``pg_indexes``, e.g. ``"CREATE UNIQUE INDEX uq_... ON
        budget.scenarios USING btree (user_id) WHERE (is_baseline =
        true)"``, or ``None`` if no such index exists.
    """
    return bind.execute(
        sa.text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = :schema AND indexname = :name"
        ),
        {"schema": schema, "name": name},
    ).scalar()


def _format_duplicate_rows(rows) -> str:
    """Render duplicate-baseline diagnostic rows into a copy-paste string.

    Each row carries (user_id, scenario_ids, names, created_at) for
    one violator user.  Emit one block per user so the operator can
    scan the list, pick the scenario row that holds the canonical
    baseline data, and clear the ``is_baseline`` flag on the others
    via a hand-written UPDATE.

    Args:
        rows: Sequence of result rows from
            :data:`DUPLICATE_DETECTION_SQL`.

    Returns:
        Newline-separated block of diagnostic text suitable for
        embedding in a ``RuntimeError`` message.
    """
    blocks = []
    for row in rows:
        scenarios_lines = []
        for sid, sname, sat in zip(
            row.scenario_ids, row.names, row.created_at_values
        ):
            scenarios_lines.append(
                f"    scenario id={sid!r} name={sname!r} created_at={sat!r}"
            )
        blocks.append(
            f"  user_id={row.user_id!r} carries "
            f"{len(row.scenario_ids)} baseline scenarios:\n"
            + "\n".join(scenarios_lines)
        )
    return "\n".join(blocks)


def _preflight_duplicate_baselines(bind):
    """Return the duplicate-baseline detection result rows.

    Wraps :data:`DUPLICATE_DETECTION_SQL` so callers (the migration's
    :func:`upgrade` and the regression test suite) share a single
    code path for "which users carry more than one baseline".

    Args:
        bind: A SQLAlchemy ``Connection`` exposing ``execute``.

    Returns:
        Sequence of rows from :data:`DUPLICATE_DETECTION_SQL`.  Each
        row exposes ``user_id``, ``scenario_ids``, ``names``, and
        ``created_at_values`` attributes (PostgreSQL ``array_agg``
        results).  Empty when no user violates the
        one-baseline-per-user invariant.
    """
    return bind.execute(sa.text(DUPLICATE_DETECTION_SQL)).fetchall()


def _build_violator_message(rows) -> str:
    """Compose the full RuntimeError message body for a duplicate-baseline run.

    Centralised so the test suite asserts the same text the operator
    would see when the migration refuses.  The message includes the
    actionable remediation SQL alongside the violator dump so the
    operator does not need to consult external docs to resolve the
    drift.

    Args:
        rows: Non-empty sequence of duplicate-detection rows.

    Returns:
        Multi-line string suitable for the ``RuntimeError(...)``
        argument in :func:`upgrade`.
    """
    diagnostic = _format_duplicate_rows(rows)
    return (
        f"Refusing to create {INDEX_NAME}: "
        f"{len(rows)} user(s) carry more than one baseline "
        f"scenario.  The partial unique index would fail with an "
        f"IntegrityError at creation time, leaving the schema "
        f"half-migrated.  Reconcile each violator by hand -- pick "
        f"the scenario row that holds the canonical baseline data "
        f"and clear ``is_baseline`` on the others via:\n"
        f"  UPDATE budget.scenarios "
        f"SET is_baseline = FALSE WHERE id = <scenario_id>;\n"
        f"Per docs/coding-standards.md the migration never "
        f"auto-rewrites data.  Offenders:\n{diagnostic}"
    )


def _create_index_if_missing(bind) -> bool:
    """Create the partial unique index iff PostgreSQL does not already have it.

    Uses :func:`op.create_index` so Alembic's bookkeeping captures
    the DDL alongside any other operations the surrounding
    migration runs in the same transaction.

    Args:
        bind: A SQLAlchemy ``Connection`` used only for the
            existence check; the create call goes through
            :func:`op.create_index`.

    Returns:
        True iff the index was created on this call.  False when the
        index already existed and the call was a no-op.  The return
        value drives the regression-test idempotency assertions.
    """
    if _index_exists(bind, INDEX_NAME, SCHEMA_NAME):
        return False
    op.create_index(
        INDEX_NAME,
        TABLE_NAME,
        [INDEX_COLUMN],
        unique=True,
        schema=SCHEMA_NAME,
        postgresql_where=sa.text(INDEX_WHERE_CLAUSE),
    )
    return True


def _assert_index_shape(bind, name: str, schema: str) -> None:
    """Verify the freshly-created index has the expected partial-unique shape.

    PostgreSQL's ``pg_get_indexdef`` renders the index definition into
    a canonical form -- e.g.,
    ``CREATE UNIQUE INDEX uq_scenarios_one_baseline ON
    budget.scenarios USING btree (user_id) WHERE (is_baseline =
    true)``.  The check confirms four properties:

      * The index is UNIQUE (``CREATE UNIQUE INDEX``).
      * It scopes to the ``user_id`` column.
      * It carries a partial WHERE clause referencing
        ``is_baseline``.
      * It belongs to ``budget.scenarios``.

    Raises ``RuntimeError`` with the actual indexdef embedded so the
    operator can compare against the expected shape.  This guards
    against a future hand-recreation that drops the WHERE clause or
    the UNIQUE keyword, either of which would silently break the
    one-baseline-per-user invariant.

    Args:
        bind: A SQLAlchemy ``Connection`` exposing ``execute``.
        name: The index name to inspect.
        schema: The schema the index belongs to.
    """
    definition = _index_definition(bind, name, schema)
    if definition is None:
        raise RuntimeError(
            f"Post-creation shape check failed: index "
            f"{schema}.{name} disappeared between creation and "
            f"verification.  Investigate the database state before "
            f"retrying the migration."
        )

    expected_substrings = (
        "CREATE UNIQUE INDEX",
        f"{schema}.{TABLE_NAME}",
        f"({INDEX_COLUMN})",
        "WHERE",
        "is_baseline",
    )
    missing = [s for s in expected_substrings if s not in definition]
    if missing:
        raise RuntimeError(
            f"Post-creation shape check failed: index "
            f"{schema}.{name} exists but its definition does not "
            f"match the expected partial-unique shape.\n"
            f"  Missing substrings: {missing!r}\n"
            f"  Actual indexdef:    {definition!r}\n"
            f"  Expected substrings (all must be present): "
            f"{list(expected_substrings)!r}\n"
            f"Drop the index manually and rerun the migration:\n"
            f"  DROP INDEX {schema}.{name};"
        )


def upgrade():
    """Pre-flight detect duplicate baselines, then create the partial unique index.

    Three-step pattern:

      1. Pre-flight scan ``budget.scenarios`` for any user carrying
         more than one ``is_baseline = TRUE`` row.  Raise
         :class:`RuntimeError` with a copy-paste diagnostic block if
         any violators are found.  The migration refuses to
         auto-resolve because picking the "winning" baseline is a
         financial decision the operator must make per
         ``docs/coding-standards.md``.
      2. Idempotency-guarded :func:`op.create_index`.  Skipped when
         the index already exists (the test-template path, plus any
         future re-run of this migration on the same DB).
      3. Post-creation shape check via
         :func:`_assert_index_shape`.  Verifies the index is UNIQUE
         on ``user_id`` with the partial ``WHERE is_baseline = ...``
         predicate -- the same shape the ``Scenario`` model declares.
    """
    bind = op.get_bind()

    rows = _preflight_duplicate_baselines(bind)
    if rows:
        raise RuntimeError(_build_violator_message(rows))

    _create_index_if_missing(bind)
    _assert_index_shape(bind, INDEX_NAME, SCHEMA_NAME)


def downgrade():
    """Refuse automatic downgrade.

    The migration's upgrade is a one-way reconciliation: it closes a
    production drift where ``uq_scenarios_one_baseline`` was missing.
    Dropping the index here would re-open that drift on a database
    that had been correctly aligned with the model, allowing a future
    code path to insert a second baseline per user and silently
    corrupting balance projections.

    The operator-facing manual recovery path is to drop the index by
    hand only after confirming the call site that motivates the
    drop:

      DROP INDEX budget.uq_scenarios_one_baseline;

    Per docs/coding-standards.md, irreversible-by-design migrations
    raise NotImplementedError with the manual recovery SQL embedded
    in the message so a ``flask db downgrade`` chain halts here
    rather than continuing past a half-reverted step.

    Review: solo developer, 2026-05-11 (audit 2026-04-15, C-41 / F-069).
    """
    raise NotImplementedError(
        "Refusing to drop budget.uq_scenarios_one_baseline.  This "
        "migration closes a production drift where the partial "
        "unique index was missing from the live schema; dropping it "
        "would re-open the drift and allow a defective caller to "
        "insert a second baseline scenario per user, silently "
        "corrupting balance projections.  If the index truly must be "
        "removed (e.g., to roll back a deployment that introduced a "
        "regression in baseline-creation logic), drop it manually "
        "after confirming the call site:\n"
        "  DROP INDEX budget.uq_scenarios_one_baseline;\n"
        "Then stamp the prior revision before continuing the "
        "downgrade chain:\n"
        "  flask db stamp 2109f7a490e7"
    )
