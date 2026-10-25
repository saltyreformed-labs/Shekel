> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

The as-built account of recurrence plan step **R9** and of ruling **R-R27**, taken with it on
2026-08-17. Cite it for how those decisions came to be, never as a plan of record.

# R9: the closed pattern set's last artefacts die

## What the step said it was

`steps.md` ranked **R9** at #20: "drop the `ref.recurrence_patterns` table and
`pay_period_admin._repoint_recurrence_rules`, after re-checking the two premises ledger row **D6**
names." Every clause of that sentence had already expired:

* `pay_period_admin._repoint_recurrence_rules` was DELETED at plan step R7b-4 with the
  `start_period_id` capture it existed for. Confirmed by census: 0 occurrences in `app/`, `tests/`
  or `scripts/`.
* **D6** closed at R7c-c. What survived it is **D39**, a different defect on a different column,
  owned by R5.
* The arc document's own R9 entry had already said both, and had reduced the step to "one table and
  one enum". That is what was built.

## The release question, which is the whole of R-R27

The arc document held R9 for its OWN release under ruling **R-R11**, whose reasoning is recorded in
migration `d4a71f6e30bb`: `ref_cache.init` iterates the running image's enum and raises for a member
with no row, so an image that still names `RecurrencePatternEnum.ONCE` cannot boot against a
database whose `Once` row has been deleted -- and that image is exactly the one `shekel-deploy`
auto-rolls back to on an unhealthy deploy.

**The step first argued that dropping the whole table beside the whole enum is that hazard at full
size, and that was WRONG.** An adversarial review of R9 refuted it: `ref_cache._load_rows` CATCHES
the `ProgrammingError` a missing table raises, rolls the session back, logs, and returns `None`, and
`init` records the table "unavailable" and completes normally. Its own docstring says so, and
`tests/test_ref_cache.py::test_init_records_unavailable_table_and_keeps_others_usable` pins it. The
fatal `RuntimeError` needs the table to EXIST and a member's row to be missing from it -- which is
exactly and only R-R11's case. The previous image would raise on **zero** of the seven, not all
seven.

**So R-R11 was right about the row and simply does not reach the table.** Three independent reasons
the previous image is safe, in the order they actually fire:

1. **It never reaches `ref_cache` at all.** `entrypoint.sh` step 3 runs `scripts/init_database.py`,
   which builds the app with `init_ref_cache=False` and then calls `command.upgrade(cfg, "head")`.
   The previous image's Alembic tree cannot resolve `b2e9a47c3f18`, so that raises and
   `set -eEuo pipefail` aborts the entrypoint before step 4's seed and before any cache exists.
   This is finding **F-8**.
2. **`shekel-deploy.sh` refuses to put it there.** `repin_is_safe` re-pins the previous digest only
   when `image_resolves_revisions` says that image's Alembic tree can resolve whatever revision
   `public.alembic_version` holds AFTER the failure. The drop applied -> stamp `b2e9a47c3f18`,
   unresolvable, `refuse_to_repin`, and recovery is the pre-deploy dump the script takes
   unconditionally, which restores the table with the schema. The drop did not apply -> the stamp is
   unmoved and the ordinary rollback proceeds against a table still standing. Those are the only two
   states the SCRIPT can produce; a hand-edited `SHEKEL_IMAGE_DIGEST` plus `docker compose up -d` is
   a third, and reason 1 is what covers it.
3. **Booted anyway, it would DEGRADE rather than die**, by the paragraph above --
   `app/__init__.py` skips Jinja-globals registration when `init` returns a non-empty unavailable
   list.

**The lesson this paid for is that a hazard's MECHANISM has to be re-read, not inherited.** R-R11's
sentence was carried forward into three documents as though "the enum and the table must not go
together" were the rule; the rule was about a row in a table that exists, and nobody had re-opened
`_load_rows`. See `docs/plans/lessons.md`.

**Precedent, measured the same day.** R7c-a, R7c-b and R7c-c ALL reached production in one release,
PR #102 (`41e09dad`) -- `git merge-base --is-ancestor 900e761a c92b49d7` is false, and so is the
same test for `ee35bca7`. R7c-c dropped `budget.recurrence_rules.pattern_id`, a column the previous
image's ORM mapped and would have SELECTed on every rule load. The project already ships this class
of forward-only change in one release, relying on the same refusal.

Developer ruling, 2026-08-17: **one release**, recorded as **R-R27**.

## The production census, taken before anything was built

Both `shekel-prod-db` and the dev clone were stamped `d9f5c1a48b73` at the time.

| question | production | dev clone |
|---|---|---|
| rows in `ref.recurrence_patterns` | 8 | 8 |
| inbound foreign keys (`pg_constraint`, `contype='f'`) | 0 | 0 |
| views, matviews, rules depending on it (`pg_depend` / `pg_rewrite`) | 0 | 0 |
| functions whose definition names it | 0 | 0 |
| triggers, comments | 0 | 0 |
| columns anywhere in the database named `%pattern%` | 0 | 0 |

The production column read `--` on four of those rows when this file was first written, because
only the dev clone had been asked; the adversarial review re-measured every one against production
directly and they are all zero. A provenance line that overstates which database was asked is the
same defect as a stale number.

The eight names, at ids 1-8: Every Period, Every N Periods, Monthly, Monthly First, Quarterly,
Semi-Annual, Annual, Once.

## The DROP carries no guard, and the refusal was DRIVEN

`op.drop_table` emits a plain `DROP TABLE`, never `CASCADE`, and PostgreSQL refuses one while a
foreign key depends on it. A Python pre-check counting `pg_constraint` rows would be a second
implementation of a refusal the database already makes structural.

Driven on the rehearsal clone with a planted dependency:

```text
CREATE TABLE budget.planted_pattern_ref (
    id serial PRIMARY KEY,
    pattern_id integer NOT NULL REFERENCES ref.recurrence_patterns(id));

psycopg2.errors.DependentObjectsStillExist: cannot drop table
  ref.recurrence_patterns because other objects depend on it
DETAIL:  constraint planted_pattern_ref_pattern_id_fkey on table
  budget.planted_pattern_ref depends on table ref.recurrence_patterns
```

The transaction rolled back whole: the stamp stayed at `d9f5c1a48b73` and the table was intact.

## The clone rehearsal, both directions

A restore of the production database (8 pattern rows, 46 recurrence rules, 1,012 transactions):

1. **upgrade** -> stamp `b2e9a47c3f18`, table absent, 46 rules and 1,012 transactions untouched;
2. **downgrade** -> table back with the SAME shape production had: `id integer NOT NULL DEFAULT
   nextval('ref.recurrence_patterns_id_seq')`, `name character varying(20) NOT NULL`,
   `recurrence_patterns_pkey`, `recurrence_patterns_name_key`, and the eight names at ids 1-8;
3. **downgrade one further**, to `b6d41f0a9c27`, so `d9f5c1a48b73`'s own restore ran against the
   reseeded table -- **0 of 46 rules left with a NULL `pattern_id`**, distributed Annual 20 /
   Monthly 14 / Every Period 7 / Quarterly 2 / Semi-Annual 2 / Monthly First 1;
4. **re-upgrade to head** -> table absent, 46 rules, 1,012 transactions, and the interval histogram
   back at 42 x 1, 2 x 3, 2 x 6, which is R7c-c's own re-point unmoved by the round trip.

**Step 3 is why the downgrade is not optional.** `d9f5c1a48b73`'s downgrade re-seats every rule with
`SELECT id FROM ref.recurrence_patterns WHERE name = :pattern_name`, so the chain below this
revision READS the table it drops. `tests/test_models/test_drop_recurrence_patterns_migration.py`
asserts the reseed against that migration's own `_PATTERN_BY_READING` rather than against a copy.

## The test suite moved off the closed set too, which is most of the diff

The seven display names were the suite's cadence vocabulary. `tests._test_helpers.make_pattern_rule`
took a name or an enum member and resolved it through
`tests.oracles.recurrence_baseline.CADENCE_BY_LEGACY_NAME`; six more files swept that dict's keys.
Deleting the enum without deleting the vocabulary would have left the suite speaking a set the
application no longer has.

Developer ruling, 2026-08-17, taken with R-R27: the call sites state **the two axes**, as one of the
oracle's `ShapeCadence` constants. `CADENCE_BY_LEGACY_NAME` is deleted; `BASELINE_CADENCES` is the
sweep space and `ShapeCadence.label` derives its parametrize ids. `make_pattern_rule` and
`transient_pattern_rule` became `make_cadence_rule` and `transient_cadence_rule`, taking a cadence
and refusing anything else at the door.

**The substitution was scripted and it had FALSE POSITIVES, which is the lesson.** 272 string
literals and 104 enum references were rewritten by AST, skipping docstrings and one hand-listed
site. Five were not cadences at all: four `_add_transaction(..., "Annual", ...)` calls in
`test_calendar_service.py` where the literal was the TRANSACTION's name, and one
`assert "Monthly" in html` reading a rendered label. All five were caught by the suite because a
`ShapeCadence` cannot be adapted to a SQL parameter or found in a string -- a loud failure the
substitution earned by moving to a type the wrong context cannot accept. A rename that had stayed
inside the string type would have produced five silently wrong tests.

## The arc's browser mandate, and why this step did not discharge it

**Developer ruling, 2026-08-18: SKIP, with the reason recorded.** R9 changes no route, template,
form field, schema or rendered string -- `git diff` touches no file under `app/routes/`,
`app/templates/` or `app/schemas/` except three docstrings. Its whole risk surface is app BOOT,
through `ref_cache.init` and `seed_reference_data`, and both are exercised: the full suite builds
the app 9,626 times against a migrated database, and `scripts/build_test_template.py` runs
`seed_reference_data` against the post-migration schema (its own output: "migrated to head, applied
audit, seeded reference data ... 19 account types, 42 audit triggers").

Two things made running it cost more than it could return, and both are worth recording because
they will recur:

* `tests/manual/.dev_session_state.json` expired on 2026-08-16, and `IDLE_TIMEOUT_MINUTES=720`
  means the re-login is genuinely the developer's;
* the shared dev containers belong to the `shekel-r8` worktree
  (`com.docker.compose.project.working_dir`), and this worktree's dev app would migrate the SHARED
  dev database to `b2e9a47c3f18` -- a revision the two concurrent sessions' checkouts cannot
  resolve, so their dev apps would die at entrypoint step 3 until they merged R9. A browser pass
  here needs its own compose project, port and cloned database, or it breaks two other sessions.

## Coverage that ended with its subject

Three test classes were deleted because the thing they asserted stopped being expressible:

* `TestDeliberateRefSeedSurplus` (`test_posting_ref_seed_parity.py`) -- that `app/ref_seeds.py`
  still carried `Once` and that no enum member named it. Both halves lose their subject; the enum
  is registered in that file's parity tuples no more, because it does not exist.
* `TestTheSurvivingRefRow` (`test_retire_once_pattern_migration.py`) -- the same pair against the
  live database. Replaced in that file by the STRICTLY STRONGER claim: the table does not exist.
* `TestTheRetiredOncePattern` (`test_recurrence_resolution.py`) -- that no foreign key pointed at
  the table and that the retired row survived unnamed. The first half is subsumed by the drop; the
  second is gone with the row.

## Ledger

No row opened, none closed. **D6** had already closed at R7c-c and no live row named R9 as its
owner, so rule 2 had nothing to re-point.
