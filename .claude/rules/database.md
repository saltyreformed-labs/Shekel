---
paths:
  - "migrations/**/*"
  - "app/models/**/*"
---

# Database, schema, and migration rules

Must-knows for models and migrations. Full standards: `docs/coding-standards.md`
(SQL / Database, Migrations, Audit Triggers). The `code-reviewer` agent restates
these rules deliberately, so a review carries them even where this file has not
loaded; a change to a rule here updates that mirror in the same commit.

## Query safety

- **Every query touching user data filters by `user_id`** -- a missing ownership
  check is an IDOR vulnerability. Security response: 404 for both "not found" and
  "not yours" (no existence oracle).
- **Filter soft-deleted rows:** `.filter(Model.is_deleted.is_(False))` unless
  deleted rows are explicitly needed.
- **ORM only**, no raw SQL strings. Prevent N+1 with `joinedload` /
  `selectinload` on the grid route and balance calculator especially.

## Schema design

- **NOT NULL by default;** nullable columns justify nullability in a comment.
- **`Numeric(12,2)` for all money** -- never Float, Integer, or bare Numeric.
- **CHECK constraints on every financial column** (amounts, rates, counts,
  durations). If Marshmallow says `min=0`, the column has `CHECK(col >= 0)`.
- **Explicit `ondelete` on every FK** (`CASCADE` for user_id, `RESTRICT` for ref
  tables, `CASCADE`/`SET NULL` inter-domain). **Name all constraints**:
  `ck_<table>_<desc>`, `uq_<table>_<cols>`, `ix_<table>_<cols>`. Index frequent
  WHERE/JOIN/ORDER BY columns.
  **EXEMPT: single-column `PRIMARY KEY (id)` / `UNIQUE (name)` on a `ref` lookup
  table**, which take PostgreSQL's generated names (developer ruling 2026-08-14,
  finding `recurrence:F-3`; 24 of 24 live `ref` tables already do). Those two
  names are never referenced by anything. Every other constraint is named.

## Migrations

- **Alembic only**; never `db.create_all()` outside tests.
- **Destructive migrations** (drops, renames, type changes, constraint removals,
  incl. drop-and-recreate) need developer approval AND a `Review: <name>, <date>`
  line in the module docstring.
- **Every migration has a working downgrade**, or `raise NotImplementedError`
  whose message gives (a) why it is unsafe and (b) the literal SQL to revert by
  hand. A bare `pass` is a FAIL.
- **NOT NULL on a populated table = three steps:** add nullable, `UPDATE` backfill
  with a documented derivation, then `alter_column` to NOT NULL after verifying
  zero NULLs (raise `RuntimeError` with the diagnostic SELECT if any survive).
  `server_default` only if a static default fits every existing row.
- Review auto-generated migrations: intended changes only, no phantom diffs, named
  constraints, correct downgrade.
- **A migration's commit carries its own change and nothing else.** An unrelated fix riding in
  a schema migration is unreviewable there: it is a standalone commit of its own.

## Audit triggers

Every new table in `auth`, `budget`, or `salary` MUST be added to
`app/audit_infrastructure.py:AUDITED_TABLES`, then `flask db upgrade` re-attaches
the trigger. Never write directly to `system.audit_log` -- all rows go through
`system.audit_trigger_func`.
