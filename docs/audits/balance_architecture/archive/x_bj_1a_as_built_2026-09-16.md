> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-bj-1a` as built: one level relation (2026-09-16)

**What shipped** (`1d1f7ce8`; migration `d2e9f4a17c63` over `c4e8a2d7f1b3`, re-pointed at
`ac3b4bd0` after `X-bi-3c` landed first). `budget.account_anchor_history` is THE LEVEL RELATION:
every observation that an account held a balance at the close of a day, whoever observed it. The
bank's placed figures left `budget.statement_imports` (`balance_effective_on`,
`balance_evidence_id`, three CHECKs) and became rows naming their import; the owner's rows gained
`evidence_id = uncorroborated` by the column's default. A release is an appended row in the new
append-only `budget.anchor_releases`. Rulings **R-IS** / **R-JN** (the design) and **R-BAL47** to
**R-BAL57** (the ten forks the developer ruled the day it was built). `$0.00`: no rendered figure
moved. Closed **BAL-485**; re-pointed **N-343** to `X-bj-1b` and **N-434** to `bank_import:X-f6b`.

## The shape, and why each choice

| question | ruled | why |
|---|---|---|
| where the relation lives | `account_anchor_history` widened in place (R-BAL47) | same columns and normal form as a new table; a rename would re-point the two clearing-link keys on money rows and break 88 rows' audit continuity for a name |
| who observed it | `statement_import_id`, NULL = the owner (R-BAL48); no typed `source_id` | the key IS the fact: `fk_anchor_history_statement_import_claim` locks a bank row's amount to its file's `stated_balance` (a NULL claim matches nothing, so an import with no claim owns no level), and there is no third writer. A typed column would copy `statement_imports.source_id` and could not be CHECK-paired to a nullable key without freezing a ref id. Class-table inheritance was rejected because an import delete would leave the base row standing as an owner level and feed it to the reset |
| an owner's evidence | `uncorroborated`, the column default (R-BAL49) | the enum's own definition; a fourth member would be a source, and NULL would put a `None` arm in every rank site |
| a release | `budget.anchor_releases (anchor_id, released_by_import_id, lines_changed_from)` (R-BAL50) | an observation log cannot un-observe; a superseding row IN the relation would need a nullable amount and a permanent branch. `SET NULL (released_by_import_id)` is the column-subset form: a bare composite SET NULL nulls `account_id` and every import delete fails |
| an import deleted | its level CASCADES; the append-only DELETE arm permits the disposal when the import is gone at commit (R-BAL51) | R-GG(e): a row that exists only because a line did states nothing once the line is destroyed; RESTRICT would make a placed import undeletable; keeping the row leaves its key dangling |
| the bank walk's domain | standing BANK levels only, permanently (R-BAL52) | `anchor + sum(lines)` is exact only from the bank's posted end-of-day figure; a typed figure may hold pending items. A domain, not a precedence branch |
| coverage | the STATEMENT that owns lines, `covered_runs` unchanged (R-BAL53, revising R-JN(3)) | a date-range export whose header cannot be placed still records every line of its days; under the literal wording those days, and a released level's, would become un-imported |
| the within-file bound | one SQL rule, two attachments, immediate BEFORE triggers (R-BAL54) | the fact and the bounds sit on two tables; an insert-only trigger let `UPDATE statement_imports SET period_end` under a placement commit |
| the cut | 1a here, 1b = N-343; N-434's column to `X-f6b` (R-BAL55) | the column would be born dead behind a `COALESCE` until the parser half |

## What every reader became

| reader | before | after |
|---|---|---|
| `cash_ledger._facts._governing_row`, `reconciled_through`; `_events.cash_anchor_facts`; `_books.earliest_assertion_day` | every row | composes `balance_predicates.owner_declared_clause()` (`statement_import_id IS NULL`), the ONE spelling `X-f3c-5` deletes with the reset; `integrity_check.py` BA-01 spells it in raw SQL, BA-05 reads every level on purpose |
| `statement_import._balance.usable_anchor` | imports with a placed day, strongest evidence then recency | `standing_bank_levels`, same rank; still ONE anchor per account (N-343 is 1b's) |
| `_anchor.anchored_imports` / `resting_on` / `release_anchors_from` | UPDATE nulling two columns, `except_import_id` | `bank_levels` (level LEFT JOIN release) / `resting_on(standing, day, except)` / one appended release per undercut level naming the import whose lines changed -- ONE parameter, the cause and the exclusion being the same import at both doors |
| `_record.record_statement` | two columns on the import row | one level row after the lines, before the release |
| `_undo.delete_import` | released AFTER the delete with NULL | releases BEFORE the delete naming the doomed import (the cause reaches `system.audit_log`; the key's SET NULL then takes it off the row), then the delete cascades the import's own level; `expire_all()` after |
| `_reads.import_history` | read the two columns | reads the level and its release per import; `ImportedBalance.release` (`PlacementRelease`: placed day, cause file, day, instant) drives the statements badge |
| the append-only trigger | three tables, one body | four tables; two owner arms behind `TG_TABLE_NAME` guards; UPDATE admits exactly one transition -- old cause present, every other column byte-equal, the named import already gone |

## The measurement

`tests/manual/verify_level_relation.py` dumps every figure the step can move (the bank fold over
every recorded day, the agreement report whole, the outstanding-difference card, the cash
resolver, the clearing boundary, the history card, the statements read model). On a fresh restore
of the 2026-09-16 production dump (88 assertions, 1 import placed 2026-07-17 `file_chain`, 0
releases ever) replayed to `c4e8a2d7f1b3`: the before-dump with the pre-change code and the
after-dump at HEAD are byte-identical over 9 accounts bar the added `release` key. The mutation
control -- the owner predicate patched to `true` -- moved Checking's fold by `$578.71`, so the
instrument sees the axis. The migration round-trips down and up; its `evidence_id` backfill is a
fast default (no row UPDATE, no audit noise, verified against `attmissingval` and the audit count).

## What the two neutral reviews found and what was done

Design review: the composite `SET NULL` blocker (fixed as above); both level-to-import keys must
cascade; the trigger's field access needs nested `IF TG_TABLE_NAME` guards; the within-file bound
needs the import-side arm; three callers plus the image check must see any new trigger; the
delete door should name the doomed import first; N-434's column would be born dead. Code review:
the admitted transition let a hand-written `UPDATE ... SET released_by_import_id = NULL` through
while the cause stood (fixed: the arm also requires the named import to be gone, control added);
the evidence FK is now NAMED on the model so `create_all` and the migration agree; the cause's
file name is one query per page, not one per released row; the moved row's `recorded_on` and
`created_at` are graded in the migration test.

## What a LATER step must obey

* `owner_declared_clause()` is a fence with a named deleter: `X-f3c-5`. Until then no cash reader
  may read a bank level, and after it none may prefer one observer.
* The bank walk's bank-only domain is permanent (R-BAL52); `X-bj-1b` chooses the anchor PER RUN
  among standing bank levels and reads the others as checkpoints.
* A release is never followed by a re-placement of the same import; the next import re-establishes
  a level. `uq_anchor_history_statement_import` holds that (R-BAL56: identity, not content).
* The two shipped append-only migrations and this one name their tables LITERALLY; a fifth
  append-only table must do the same or a chain replay from the start breaks.
* A development database holding a placement the OLD release nulled upgrades it into "never
  placed"; production held none.
