"""pre-tax splits in two: a pre-tax line says whether it skips Social Security and Medicare

Revision ID: 47beea2b628a
Revises: 858f569b2c17
Create Date: 2026-11-02

Plan step **salary:S11-e**, card plan#1156 (ruling **plan#2639**, "A new line
kind"; ledger row **SAL-566**)::

    ref.paycheck_line_kinds  +=  'pre_fica_deduction'

**One reference row and nothing else; no figure moves.**  A line's kind is its
position in the paycheck's waterfall (ruling R-SAL38), and a pre-tax deduction
sits at one of two: a Section 125 premium or a flexible spending account leaves
the Social Security and Medicare wages as well as the income-tax base ("Pre-tax,
all payroll taxes", this row), while a retirement deferral leaves the
income-tax base alone ("Pre-tax, income tax only", the existing
``pre_tax_deduction`` row, which keeps its name and its arithmetic).  The tax
formulas charge Social Security and Medicare on the gross less this row's
lines, in the same commit as this revision, so no tree holds a kind row nothing
prices and none holds an enum member without its row (the ref cache refuses
the latter at start-up) -- the ``6c15d2a97b78`` precedent.

**Every saved paycheck prices byte-identical at upgrade**, because no line,
stub line amount or one-off changes kind here.  Which of an owner's lines skip
those taxes is a fact of his employer's payroll that no column holds, so the
owner re-picks the kind of each such line, and of the same lines on each saved
pay stub, through the salary page and the stub door after the release (the
ruling's "You re-pick 4 lines and the 4 lines on each saved stub").

Inline-seeded ``ON CONFLICT (name) DO NOTHING`` so a freshly upgraded database
resolves the enum before the idempotent reseed in ``app/ref_seeds.py`` runs.

**The downgrade REFUSES, by name, any line, stub line amount or one-off of the
kind** before it deletes the row: the three foreign keys are ``ON DELETE
RESTRICT``, so the delete would fail anyway, but a refusal that names the rows
and says why is the R18-b shape -- re-kinding them to the income-tax-only kind
to make the downgrade pass would charge Social Security and Medicare on money
the employer exempts, moving every paycheck they price.  With none, the row is
deleted, which is the tree below this revision exactly.
"""
from alembic import op
from sqlalchemy import text


# Revision identifiers, used by Alembic.
revision = "47beea2b628a"
# The head of ``dev`` at authoring, measured (one head).  Another lane landing
# a revision first makes two heads, which git merges cleanly and ``flask db
# upgrade`` refuses: re-point this at the measured head as the last edit
# before the pull request.
down_revision = "858f569b2c17"
branch_labels = None
depends_on = None


#: The kind this revision adds, spelled once; the enum
#: (``app.enums.PaycheckLineKindEnum.PRE_FICA_DEDUCTION``) and the reseed list
#: carry the same string.
KIND = "pre_fica_deduction"

#: Every row that names a kind: what it is, read in words for the refusal.
_HOLDERS = (
    (
        "payroll line",
        "SELECT l.id, l.name FROM salary.paycheck_lines l"
        "  JOIN ref.paycheck_line_kinds k ON k.id = l.paycheck_line_kind_id"
        " WHERE k.name = :kind ORDER BY l.id",
    ),
    (
        "pay stub line amount",
        "SELECT a.id, s.payday::text || ' ' || l.name"
        "  FROM salary.pay_stub_line_amounts a"
        "  JOIN salary.pay_stubs s ON s.id = a.pay_stub_id"
        "  JOIN salary.paycheck_lines l ON l.id = a.paycheck_line_id"
        "  JOIN ref.paycheck_line_kinds k ON k.id = a.paycheck_line_kind_id"
        " WHERE k.name = :kind ORDER BY a.id",
    ),
    (
        "pay stub one-off",
        "SELECT o.id, s.payday::text || ' ' || o.name"
        "  FROM salary.pay_stub_one_offs o"
        "  JOIN salary.pay_stubs s ON s.id = o.pay_stub_id"
        "  JOIN ref.paycheck_line_kinds k ON k.id = o.paycheck_line_kind_id"
        " WHERE k.name = :kind ORDER BY o.id",
    ),
)


def _rows_of_the_kind(connection):
    """Return ``(what, id, label)`` for every row that names the kind."""
    return [
        (what, row_id, label)
        for what, query in _HOLDERS
        for row_id, label in connection.execute(text(query), {"kind": KIND}).all()
    ]


def upgrade():
    """Seed the all-payroll-taxes pre-tax kind, idempotently."""
    op.execute(
        f"INSERT INTO ref.paycheck_line_kinds (name) VALUES ('{KIND}') "
        "ON CONFLICT (name) DO NOTHING"
    )


def downgrade():
    """Delete the kind; refuses while any line, stub line amount or one-off carries it."""
    connection = op.get_bind()
    rows = _rows_of_the_kind(connection)
    if rows:
        listed = "; ".join(f"{what} {row_id} ('{label}')" for what, row_id, label in rows)
        raise RuntimeError(
            f"{len(rows)} row(s) carry the kind 'Pre-tax, all payroll taxes' "
            f"({KIND}) the tree below this revision cannot hold: {listed}.  "
            f"Re-kinding them to 'Pre-tax, income tax only' would charge Social "
            f"Security and Medicare on money the employer exempts and move every "
            f"paycheck they price.  Change each through the salary page and the "
            f"pay stub door first, or keep this revision."
        )
    op.execute(f"DELETE FROM ref.paycheck_line_kinds WHERE name = '{KIND}'")
