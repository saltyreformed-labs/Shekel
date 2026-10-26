# Coding Standards

These standards apply to all code in the Shekel project. They are referenced from CLAUDE.md and are
loaded when working on code. Every rule here exists because its absence caused a real bug or a real
quality problem in this project.

**This file is the rationale tier and the one home for a rule's full statement.** CLAUDE.md states a
rule in one line and points; `.claude/rules/coding.md` and `.claude/rules/database.md` carry the
path-scoped must-knows; a fact lives in one tier and the other tiers point at it.

---

## Python

### Type Safety

- **Use `Decimal`, never `float`**, for all monetary amounts.
- **Construct Decimals from strings.** `Decimal("0.1")` is exact. `Decimal(0.1)` introduces float
  imprecision. This applies to hardcoded values, test assertions, seed data, defaults. (Backed by
  the `shekel-decimal-from-float` checker.)
- **Round money through `round_money`, never a bare `.quantize()`.** Keep intermediate arithmetic at
  full `Decimal` precision and round once at the display/persistence boundary via
  `app.utils.money.round_money` (`ROUND_HALF_UP`) -- or the explicitly-named `round_money_ceiling`
  for the sanctioned savings-goal over-funding case. A bare `value.quantize(Decimal("0.01"))`
  silently uses Python's default `ROUND_HALF_EVEN` (banker's rounding), which disagrees with the
  project's `ROUND_HALF_UP` convention by a cent at every half-cent boundary and the convention
  every hand-computed test assumes. (Backed by the `shekel-bare-money-quantize` checker; the
  financial_calculations audit's E-26 / HIGH-04.)
- **Type hints on all function signatures.** Annotate all parameters and return types. Use `Decimal`
  not `float` for monetary values. Use `X | None` for nullable parameters. Use specific collection
  types (`list[Transaction]`, `dict[str, Decimal]`), not bare `list`/`dict`. Do not use `Any` to
  satisfy the requirement without providing real type information.
- **Do not rely on truthiness for business logic.** `0` and `None` mean different things in a
  financial app. Write `if amount is None:` not `if not amount:`. A zero balance is not a missing
  balance.

### Code Structure

- **No magic numbers or strings.** Every numeric or string literal representing a business rule must
  be a named constant. Do not write `Decimal("0.062")` when you mean `SOCIAL_SECURITY_RATE`, or `12`
  when you mean `MONTHS_PER_YEAR`. Mathematical constants (`0`, `1`), HTTP status codes, and
  framework values are exempt.
- **A constant is for a value that does not vary. A per-owner fact is a DERIVATION, not a constant
  with a good name.** This rule cited `26` / `PAY_PERIODS_PER_YEAR` as its own example until plan
  step R7a-2a deleted that constant: how many paychecks a year an owner receives is a function of
  `budget.pay_schedule.cadence_days`, which is user-selectable 1..365, so naming the 26 made nine
  files agree on a number that was simply wrong for anyone not paid biweekly.
  `app.services.pay_calendar.PayCadence` derives it instead. Naming a varying value is worse than
  inlining it, because the name asserts it is settled.
- **Keep functions focused.** If a function exceeds 50 lines, evaluate decomposition. Functions over
  100 lines require justification. A 300-line function is a service module incorrectly written as a
  single function.
- **Prefer guard clauses over deep nesting.** Validate preconditions early and return or raise
  immediately. Aim for a maximum nesting depth of 3 levels in business logic.
- **No mutable default arguments.** Never use `[]`, `{}`, or `set()` as a default parameter. Use
  `None` and initialize inside the function body.
- **DRY and SOLID.** Do not duplicate logic -- extract shared behavior. Do not add parameters to
  patch around a function's broken internals. Verify equivalent logic does not already exist before
  writing new code.

### Documentation

- **Docstrings must be substantive.** A docstring restating the function name adds nothing. Explain:
  what it does in business terms, input types/constraints, return type/edge cases, side effects. For
  financial calculations, document the formula or business rule.
- **Comments must explain why, not what.** `# Subtract expenses` is noise.
  `# Exclude settled transactions -- already reflected in the anchor balance` explains a decision.
- **Docstrings on every module, class, and function -- public AND private.** Gated by
  `no-docstring-rgx=^__repr__$` in `.pylintrc`, which also puts every private docstring's `Args:`
  section under `differing-param-doc` / `missing-param-doc`. One exemption: `__repr__`, whose
  one-line body is its own documentation. Note what the gate does NOT catch -- a docstring with no
  `Args:` section at all passes (`accept-no-param-doc` defaults to yes), so omitting the section is
  not a way to satisfy the rule.

### Error Handling

- **Catch specific exceptions.** Never `except Exception:`. Identify the specific exceptions the
  `try` block can raise. List them in a tuple if multiple need the same handling.
- **Error messages must be actionable.** Bad: `raise ValueError("Invalid input")`. Good:
  `raise ValueError(f"Expected positive Decimal for amount, got {amount!r}")`.

### Style and Linting

- **Pylint compliance is mandatory.** Run `pylint app/ --fail-on=E,F` after every change. Do not
  decrease the current score.
- **Fix Pylint violations, do not suppress them.** The only acceptable `# pylint: disable=` is for
  genuine false positives. When truly necessary, it must: (a) be scoped to one line, (b) name the
  specific rule, and (c) carry a why-comment in the standard location and format below. NEVER use
  `# pylint: disable=all`. (Presence, location, and rule-naming are enforced by the
  `shekel-disable-rationale` checker.)
- **Disable why-comment: standard location and format.** Every disable's rationale is greppable via
  the `Pylint:` marker (`grep -rn "Pylint:" app/`). Where the rationale lives depends on what the
  directive is attached to:
  - **Definition-scoped** -- the directive sits on a `def`/`class` line (`too-many-*`,
    `too-many-instance-attributes`, a per-method `no-self-argument`): put the rationale in that
    symbol's **docstring** as a trailing note. It survives line-number drift, shows in `help()`, and
    reads alongside the contract. Format:
    `` Pylint: ``<rule>`` (<count>/<limit>) -- <why it is intentional/irreducible>. ``
  - **Statement-scoped** -- the directive sits on any other line (`broad-except`,
    `protected-access`, `import-outside-toplevel`): put the rationale in a comment
    **immediately above** the disabled line. Format: `` # Pylint: ``<rule>`` -- <why>. ``

  The rationale must name every rule the directive disables. `(<count>/<limit>)` is required for the
  count-based smells (`too-many-*`) and omitted where there is no count (e.g. `broad-except`). The
  `shekel-disable-rationale` checker enforces marker presence, location, and rule-naming; the
  `(<count>/<limit>)` shape is a documented convention, not machine-checked.
- **A module at the 1,000-line ceiling is SPLIT, not exempted.** `max-module-lines` is pylint's own
  default -- it is set in no config file here -- and the `C0302` refusal is the notice: the session
  whose commit breaks a module is the session that splits it. A module-level `too-many-lines`
  disable is NOT available, whatever rationale it carries. The RATING is blind to this, since one
  `C0302` across ~500 modules still rounds to 10.00/10, so the signal is pylint's exit code (bit 16)
  and never the score. Ruling `balance:R-IR`, 2026-09-01.
- **snake_case** for all variables, functions, modules, and database columns.
- **No unused imports.** Fix immediately.
- **Import organization.** Three sections separated by blank lines: standard library, third-party,
  local application. Alphabetical within each section.
- **Do not defer essential work as "future improvements."** If a user can hit an edge case with the
  feature as implemented, handle it now. Only genuinely out-of-scope features belong in a "future
  improvements" suggestion.

---

## SQL / Database

### Query Safety

- **All queries must be user-scoped.** Every query touching user data must filter by `user_id`.
  Missing ownership checks are IDOR vulnerabilities.
- **Always filter soft-deleted records.** Every query on a table with `is_deleted` must include
  `.filter(Model.is_deleted.is_(False))` unless deleted records are explicitly needed.
- **Use SQLAlchemy ORM.** No raw SQL strings in application code.
- **Prevent N+1 queries.** Use `joinedload()`, `subqueryload()`, or `selectinload()` when loading
  collections that access related objects. The grid route and balance calculator are the
  highest-traffic paths.

### Schema Design

- **NOT NULL by default.** Every new column should be NOT NULL unless there is a specific reason for
  nullability. Nullable columns must justify their nullability in a code comment.
- **Numeric(12,2) for all monetary columns.** Do not use Float, Integer, or bare Numeric.
- **CHECK constraints on every financial column.** Amounts, rates, counts, and durations must have
  database-level CHECK constraints. If Marshmallow says `min=0`, the column must have
  `CHECK(column >= 0)`.
- **Explicit ondelete on every foreign key.** Never rely on PostgreSQL's implicit default. Use
  `CASCADE` for user_id FKs, `RESTRICT` for ref table FKs, `CASCADE` or `SET NULL` for inter-domain
  FKs.
- **Name all constraints explicitly.** Pattern: `ck_<table>_<description>` for CHECK,
  `uq_<table>_<columns>` for unique, `ix_<table>_<columns>` for indexes.
  **EXEMPT: the single-column `PRIMARY KEY (id)` and `UNIQUE (name)` on a `ref` lookup table**
  (developer ruling 2026-08-14, ledger row `recurrence:F-3`). Those two take PostgreSQL's generated
  `<table>_pkey` / `<table>_name_key`, and that is the house pattern rather than an oversight:
  measured on the live schema, **24 of 24 `ref` tables** carry the generated names and none carries
  a `uq_` one. The names are never referenced -- no downgrade drops one by name, because the table
  goes with it -- so renaming 24 tables' constraints is a large migration that buys nothing, and a
  rule every table in a schema violates is a rule that teaches the next reader to ignore it. Every
  OTHER constraint, on a `ref` table or anywhere else, is named: a multi-column unique, a CHECK, an
  index and a foreign key all carry meaning a generated name loses.
- **Add indexes for query patterns.** Every column in a frequent WHERE, JOIN, or ORDER BY should
  have an index. Consider partial indexes for filtered queries.

### Validation

- **Marshmallow schema for every state-changing route.** Every POST/PUT/PATCH/DELETE that accepts
  input must validate through Marshmallow before any database operations. No manual
  `request.form.get()` with inline `try/except`.
- **Validate FK existence before commit.** Verify referenced rows exist and belong to the user.
  Unvalidated FKs produce IntegrityError (500) instead of clean validation errors (400).
- **A database refusal is a backstop; a code path that can reach one is a bug.** A CHECK, a trigger
  or a foreign key states what the data may never be, and holds it against every writer. It is not
  how the app tells a user no. It fires late: at the flush, or at the COMMIT for a deferred trigger,
  after the request has done its work and away from the act that caused it. It answers badly: an
  `IntegrityError` (a 500) naming a constraint, not a message saying what the user did. And when it
  fires, the door's own refusal was missing, so the defect is the door's, not the database's. So the
  code refuses the state first, with an actionable message, and the database's rule fires only on a
  defect. The rule is general: a card's, a row's and a transfer's refusals alike. Ruling
  `balance:R-BAL357`, 2026-10-21.
- **Range validation must match between schema and database.** No gaps where one is stricter than
  the other.

### Migrations

- **Always use Alembic.** Never modify schema by hand. Never use `db.create_all()` outside tests.
  Every change must have a migration with a descriptive message.
- **Destructive migrations require explicit approval.** Drops, renames, type changes, and constraint
  removals (including drop-and-recreate as a wider/narrower form) must be discussed with the
  developer first.
- **Destructive migrations must record a review.** Every migration whose `upgrade()` or
  `downgrade()` includes a drop, rename, type change, or constraint removal must carry a
  `Review: <name>, <date> (<scope>)` line in the module-level docstring at the time of authoring.
  The line documents who reviewed the change and when, and it survives long after a code-review
  thread or PR description has scrolled out of memory. Example:
  `Review: solo developer, 2026-05-11 (audit 2026-04-15, C-40 retroactive sweep)`.
- **Every migration must have a working downgrade.** Do not write `pass`. If downgrade is impossible
  (data-correction migrations, constraint replacements that allow new data patterns, lossy type
  narrowings), raise `NotImplementedError(<message>)` whose message includes (a) why the downgrade
  is unsafe, and (b) the literal SQL the operator must run to revert by hand. A bare `pass` is a
  FAIL because it lets `flask db downgrade` chain past the migration while leaving the schema
  half-reverted; the `NotImplementedError` halts the chain at the unsafe step.
- **Add NOT NULL columns to populated tables in three steps.** (1) `add_column` as nullable so the
  `ALTER TABLE` succeeds. (2) Run an `UPDATE` to backfill every existing row with a deterministic,
  documented derivation. (3) `alter_column` to NOT NULL once the count of NULL rows is verified to
  be zero (raise a `RuntimeError` with the diagnostic SELECT embedded in the message if any
  survived). A single `add_column(nullable=False)` against a populated table fails immediately with
  the cryptic `IntegrityError: null value in column ... violates not-null constraint`, breaks
  disaster-recovery replay from a pre-migration snapshot, and breaks staging rebuilds. The
  three-step form is the only reproducible path. `server_default` is an acceptable substitute only
  when a static default makes sense for every existing row; if the correct value depends on other
  columns (FK resolution, computed defaults), use the `UPDATE` form.
- **Review auto-generated migrations.** Verify intended changes, no phantom diffs, named
  constraints, and correct downgrade.

### Audit Triggers

The `system.audit_log` infrastructure (table, trigger function, per-table row-level triggers) is the
project's only tamper-resistant forensic record of financial state changes. It is materialised by
the rebuild migration (`migrations/versions/a5be2a99ea14_rebuild_audit_infrastructure.py`) and the
canonical table list lives in `app/audit_infrastructure.py:AUDITED_TABLES`.

- **Every new table in `auth`, `budget`, or `salary` MUST be added to `AUDITED_TABLES`.** Adding a
  table without auditing it leaves a gap in the forensic trail. Reference tables in the `ref` schema
  are the only schema-level exception (read-only seed data managed by `app/ref_seeds.py`).
- **Add the table to `AUDITED_TABLES`, then re-run `flask db upgrade`.** The rebuild migration's
  idempotent `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER` pair attaches the audit trigger on the next
  upgrade. The deploy's trigger-count check (`app.audit_infrastructure.require_audit_triggers`,
  inside entrypoint step 3's one transaction) refuses the deploy, committing nothing, if the count
  is short of `EXPECTED_TRIGGER_COUNT`.
- **Never write directly to `system.audit_log`.** All rows must come through
  `system.audit_trigger_func`, which captures `app.current_user_id`, `db_user`, and `executed_at`
  via session-local state. Direct INSERTs from application code would bypass the user-id capture and
  produce orphaned forensic rows.
- **The runtime app role (`shekel_app`) cannot drop, alter, or replace audit triggers.** An attacker
  who pivots into the Gunicorn process retains DML on financial tables but cannot remove the audit
  trail behind their actions; this is the load-bearing invariant the two-role policy provides.

### Reference Tables

- **IDs for logic, strings for display only.** Enums in `app/enums.py`. Cache in `app/ref_cache/`.
  NEVER compare against string `name` columns. Use boolean columns for grouping logic. Use FK
  references for category groupings, not bare strings.

---

## HTML / Jinja2 Templates

### Logic Boundaries

- **Templates are for display, not computation.** Do not perform financial calculations in Jinja.
  Compute in the route or service using Decimal, pass results to the template.
- **Use IDs, not strings, in template conditionals.** Write
  `{% if txn.status_id == PROJECTED_ID %}`, not `{% if status.name == "Projected" %}`.
- **No lazy-loaded queries in templates.** Confirm relationships were eager-loaded in the route
  before accessing them in a loop.

### Security

- **Never use `|safe` on user-provided data.** Only on HTML the application itself generated.
- **All forms must include CSRF protection.** `{{ csrf_token() }}` for non-HTMX forms. HTMX gets
  CSRF via `htmx:configRequest` in the base template.
- **State-changing actions must use POST.** Use `hx-post`, not `hx-get`, for mutations.

### HTMX Patterns

- **Partial templates (prefixed `_`) for HTMX responses.** Do not return full page HTML.
- **Include `hx-target` explicitly.** Do not rely on HTMX defaults.
- **Appropriate status codes.** 2xx for swap, 422 for validation errors, 204 for no-content.

### Structure

- **Extend `base.html` for all pages.** No standalone HTML files duplicating head/nav/footer.
- **Bootstrap utility classes before custom CSS.** Check Bootstrap docs first.

---

## JavaScript

- **No inline scripts.** CSP prohibits inline JS. All JS in external files via `<script src>`.
- **Pass data via `data-*` attributes.** Read with `element.dataset` in external JS. Do not generate
  JS in Jinja templates.
- **No JS frameworks.** No React, Vue, Alpine, jQuery. HTMX + vanilla JS only.
- **Monetary values in JS are display-only.** Financial arithmetic happens server-side in Python. JS
  never computes monetary values.
- **All JS in `app/static/js/`.** No JS elsewhere.

---

## CSS

- **Bootstrap 5 only.** No additional frameworks, preprocessors, or CSS-in-JS.
- **Utility classes before custom CSS.** Custom styles under `app/static/css/` as a last resort, in
  the file matching the concern: `theme-steel-ink.css` (design tokens only), `base.css` (app-wide
  chrome), `components.css` (cross-screen widgets), `grid.css` / `dashboard.css` / `analytics.css`
  (per-screen), `utilities.css` (single-class utilities; must stay the last `<link>`). Load order
  and cascade constraints: `docs/design/css_architecture_audit.md`.
- **Descriptive class names.** `.pay-period-header` not `.red-text`.
- **No `!important`.** Fix selector specificity instead.
- **Maintain responsive behavior.** Test at Bootstrap `md` and `sm` breakpoints.

---

## Shell Scripts

Scripts in `scripts/` run in the Docker container or on the Arch Linux host.

- **Validate inputs.** Check arguments, ranges, and environment variables. Fail with a clear
  message, not with silent defaults that could corrupt data.
- **Idempotent.** Running a seed script twice must produce the same result as once. Use upsert
  patterns or existence checks.
- **Never print secrets.** Print `Password: [set via environment variable]`, not the value.
- **Confirm destructive operations.** Prompt before deleting data. Support `--force` for automation.
- **Log destructive actions.** Write audit entries for credential resets, data deletions.
- **Match project Python standards.** Type hints, docstrings, specific exceptions, Pylint
  compliance. Scripts are production code.
