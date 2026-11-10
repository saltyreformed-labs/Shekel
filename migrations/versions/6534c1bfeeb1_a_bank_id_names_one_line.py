"""A bank source's own id names one line, held by a database key.

Plan step plan#2232 (``bank_import``), rulings **plan#2233** (an id stays with
its line for as long as the line exists), **plan#3209** (the key lands before
SimpleFIN's feed) and **plan#3220** (the sighting's id column and its index are
dropped).  MOVES NO MONEY: no line, amount, day or level is written; the id a
source gave a line moves from each import's sighting to one row per id.

Review: Josh, 2026-11-10 -- APPROVED (ruling **plan#3220**), having been told
production holds no value in the dropped column (0 of 1,388 sightings, read
2026-11-10 12:23 UTC) and what the downgrade does with an id whose source no
longer shows its line.

What changes:

1. NEW ``budget.line_source_ids (id, account_id, source_id, external_id,
   line_id)``: unique on ``(account_id, source_id, external_id)``, so a source
   names one line per id; its line held to the same account by
   ``fk_line_source_ids_line_account`` (CASCADE: the id goes with its line);
   its source keyed to ``ref.statement_sources`` (RESTRICT).  Audit-logged
   like its siblings.  Backfilled with one row per ``(account, source, id)``
   from the sightings that carried one.
2. ``budget.statement_line_sightings`` DROPS ``external_id`` and its partial
   index ``idx_statement_line_sightings_account_external_id``, so the id has
   one home.

**The upgrade REFUSES, before any DDL, while one source's id names two lines
of one account**: the state the record door has refused since
``bank_import:X-f6b-1`` (``_plan._refuse_moved_ids``) and the one the new key
cannot hold.  The door has no hole that reaches it by moving an id, but one
shape slipped past it -- a file stating two ids that one line held -- and a
row written outside the door could too.  The refusal lists each such id with
its lines and the imports that state it, and gives the remedy, rather than
choosing a line.

The DOWNGRADE writes each id back onto every sighting its own source wrote of
its line -- below this revision a source's sighting carries the id it stated.
That is exact for a source that states an id on every line it sends; one that
stated an id on only some of its sightings of a line would have it written
onto those that never stated it, and no such source exists (the one shipped
adapter, SECU's CSV, states none).  Two states it cannot write back exactly:

* **An id whose source no longer shows its line** -- the state ruling
  **plan#2233** keeps and the old schema cannot hold.  It is DROPPED, which is
  exactly the old rule: below this revision an id was held only while a
  sighting from its own source carried it.
* **One line holding two ids from one source** -- below this revision each
  came from a different import's sighting, and which import stated which is
  not recorded here.  The downgrade REFUSES while a sighting of that source
  still shows such a line, listing each with its ids and giving the remedy
  (delete every import of that source that shows it; an id is kept while its
  line is, so one import's deletion frees nothing), rather than writing an id
  onto a sighting that may never have stated it.

Revision ID: 6534c1bfeeb1
Revises: 3748145b83e3
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "6534c1bfeeb1"
down_revision = "3748145b83e3"
branch_labels = None
depends_on = None

_TABLE = "line_source_ids"
_SIGHTINGS = "statement_line_sightings"
_SIGHTING_ID_INDEX = "idx_statement_line_sightings_account_external_id"

# UP: the state the key cannot hold -- one source's id on two lines of one
# account, over the sightings that carried an id.  One row per such id, with
# its lines and the imports whose sightings state it: the refusal's count and
# what it prints, so the two cannot disagree.
IDS_ON_TWO_LINES_SQL = """
SELECT s.account_id, i.source_id, s.external_id,
       array_agg(DISTINCT s.line_id ORDER BY s.line_id) AS line_ids,
       array_agg(DISTINCT s.import_id ORDER BY s.import_id) AS import_ids
FROM budget.statement_line_sightings AS s
JOIN budget.statement_imports AS i
  ON i.id = s.import_id AND i.account_id = s.account_id
WHERE s.external_id IS NOT NULL
GROUP BY s.account_id, i.source_id, s.external_id
HAVING count(DISTINCT s.line_id) > 1
ORDER BY s.account_id, i.source_id, s.external_id
"""

# UP: one row per (account, source, id), naming the one line it is on.
BACKFILL_IDS_SQL = """
INSERT INTO budget.line_source_ids (account_id, source_id, external_id, line_id)
SELECT DISTINCT s.account_id, i.source_id, s.external_id, s.line_id
FROM budget.statement_line_sightings AS s
JOIN budget.statement_imports AS i
  ON i.id = s.import_id AND i.account_id = s.account_id
WHERE s.external_id IS NOT NULL
ORDER BY s.account_id, i.source_id, s.external_id
"""

# DOWN: the state the old schema cannot write back -- one line holding two
# ids from one source, among the ids a sighting of that source still shows.
# One row per such line and source, with its ids: the refusal's count and what
# it prints.
LINES_WITH_TWO_IDS_SQL = """
SELECT k.account_id, k.source_id, k.line_id,
       array_agg(k.external_id ORDER BY k.external_id) AS external_ids
FROM budget.line_source_ids AS k
WHERE EXISTS (
    SELECT 1
    FROM budget.statement_line_sightings AS s
    JOIN budget.statement_imports AS i
      ON i.id = s.import_id AND i.account_id = s.account_id
    WHERE s.line_id = k.line_id AND i.source_id = k.source_id
)
GROUP BY k.account_id, k.source_id, k.line_id
HAVING count(*) > 1
ORDER BY k.account_id, k.source_id, k.line_id
"""

# DOWN: each id onto every sighting its own source wrote of its line.  An id
# no such sighting remains for matches no row and is dropped with the table.
WRITE_IDS_BACK_SQL = """
UPDATE budget.statement_line_sightings AS s
SET external_id = k.external_id
FROM budget.line_source_ids AS k, budget.statement_imports AS i
WHERE i.id = s.import_id
  AND i.account_id = s.account_id
  AND k.line_id = s.line_id
  AND k.account_id = s.account_id
  AND k.source_id = i.source_id
"""


def _attach_audit_trigger() -> None:
    """Attach ``system.audit_trigger_func`` to the table this revision creates.

    The idempotent pair :mod:`app.audit_infrastructure` writes, spelled here
    because the table did not exist when the audit revision ran and the
    entrypoint's trigger-count assertion expects it at boot -- the shape
    ``af07125d00f1`` uses for the sighting table.
    """
    op.execute(f"DROP TRIGGER IF EXISTS audit_{_TABLE} ON budget.{_TABLE}")
    op.execute(
        f"CREATE TRIGGER audit_{_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{_TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def upgrade():
    """Create the id key, move every stated id into it, drop the sighting's column.

    Raises:
        RuntimeError: One source's id already names two lines of one account
            (the module docstring), before anything is created.
    """
    colliding = op.get_bind().execute(sa.text(IDS_ON_TWO_LINES_SQL)).all()
    if colliding:
        raise RuntimeError(
            f"{len(colliding)} bank line id(s) are each held on two lines of "
            "one account by one source, and the key this revision adds holds "
            "a source to one line per id.  List them, each with its lines and "
            "the imports that state it, with: "
            f"{IDS_ON_TWO_LINES_SQL.strip()} -- then, for each, delete on the "
            "statements page every import of that source whose sighting "
            "states the id on the wrong line, and upgrade again.  Nothing was "
            "changed."
        )

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "source_id", "external_id",
            name="uq_line_source_ids_account_source_external",
        ),
        sa.ForeignKeyConstraint(
            ["line_id", "account_id"],
            ["budget.bank_statement_lines.id",
             "budget.bank_statement_lines.account_id"],
            name="fk_line_source_ids_line_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["ref.statement_sources.id"],
            name="fk_line_source_ids_source",
            ondelete="RESTRICT",
        ),
        schema="budget",
    )
    op.create_index(
        "idx_line_source_ids_account_line", _TABLE,
        ["account_id", "line_id"], schema="budget",
    )
    _attach_audit_trigger()
    op.execute(BACKFILL_IDS_SQL)

    op.drop_index(_SIGHTING_ID_INDEX, table_name=_SIGHTINGS, schema="budget")
    op.drop_column(_SIGHTINGS, "external_id", schema="budget")


def downgrade():
    """Write each id back onto its source's sightings of its line; drop the key.

    Raises:
        RuntimeError: One line holds two ids from one source that a sighting
            of that source still shows (the module docstring), before
            anything is dropped.
    """
    doubled = op.get_bind().execute(sa.text(LINES_WITH_TWO_IDS_SQL)).all()
    if doubled:
        raise RuntimeError(
            f"{len(doubled)} bank line(s) each hold two ids from one source, "
            "and the schema this revision downgrades to keeps a source's id "
            "on each import's sighting without recording which import stated "
            "which.  List them, each with its ids, with: "
            f"{LINES_WITH_TWO_IDS_SQL.strip()} -- an id is kept while its "
            "line is, so deleting one import does not free it: delete every "
            "import of that source that shows such a line on its statements "
            "page, then downgrade again.  Nothing was changed."
        )

    op.add_column(
        _SIGHTINGS,
        sa.Column("external_id", sa.String(length=64), nullable=True),
        schema="budget",
    )
    op.execute(WRITE_IDS_BACK_SQL)
    op.create_index(
        _SIGHTING_ID_INDEX, _SIGHTINGS, ["account_id", "external_id"],
        schema="budget",
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    # The key goes; its audit trigger and its index go with the table.
    op.drop_index(
        "idx_line_source_ids_account_line", table_name=_TABLE, schema="budget",
    )
    op.drop_table(_TABLE, schema="budget")
