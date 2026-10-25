> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Recurrence redesign -- as built (through R3)

Archived under section 7 rule 5 of `docs/plans/implementation_plan_recurrence_redesign.md`: a
completed span leaves the live plan and becomes one line per step -- its id, its commit, and what it
closed. The live document keeps the specification of what REMAINS; this file keeps the record of what
is done, so the 900-line cap binds on unfinished work rather than on history.

Read the commits, not this file. Each hash is the step's own account of itself and the diff is the
truth; a planning document only needs to say which commit to read.

| step | commit | what it did |
|---|---|---|
| **R1** -- Oracle and characterization snapshot | `96a35fe6` | Froze `match_periods` and `compute_due_date` at 8,105 lines over 423 shapes (`tests/oracles/recurrence_baseline.py`), gated by `tests/test_services/test_recurrence_baseline.py` with two control tests that prove the freeze can fail |
| **R2a** -- the vocabulary | `5c13e643` | `ref.recurrence_units` / `ref.period_placements` / `ref.business_day_shifts`, their enums, `ref_cache` accessors and `ref_seeds` entries, dual-seeded by migration `e7a4d95c2b18`. No behaviour change; R1 baseline byte-identical |
| **R2b** -- the anchor subtypes and the count bound | `86b9eaa3`, amended by `1e5e3430` | Both anchor subtype tables, `max_occurrences` and its two bound CHECKs, by migration `c8f2b6a41d93`. **It also added four two-axis columns and backfilled all 50 rules; R2d withdrew both**, so the migration as it stands writes no data at all |
| **R2c-1** -- the write door | `2fca91bc` | `app/services/recurrence/`: a caller states what it AUTHORS, one pure function resolves it, one writer assigns the columns. Five construction sites and four in-place writers routed through it. Closed defect **D1** -- an amount-only edit no longer re-phases "every N paychecks" |
| **R2d** -- the derived half stops being stored | `1e5e3430` | The four two-axis columns removed and computed on demand instead; steps R2c-2 and R2c-3 deleted with them. See ruling **R-R10** |
| **R2e-1** -- clearing a recurrence clears it | `4d99c9d4` | The null option both edit forms already offered was a silent no-op that then REGENERATED from the rule it was asked to stop (**D14**), and through it a loan payment could be made one-time and silently lose the standing overpayment the balance seam threads (**D15**). The clear branch deletes the rule; a loan payment is refused at the door |
| **R2e-2** -- the picker offers what the app MODELS | `a465e9fa`, `fcea8b3c` | Every recurrence surface reads `RecurrencePatternEnum` and the submission check is the SCHEMA's, so the surviving `ref` row is unreachable. `pattern_choices_for` keeps an unmodelled STORED pattern selected -- without it an edit form silently defaults to the destructive clear (measured) |
| **R2e-3** -- `Once` is retired | `eef43eef`, `867c3854` | The enum member, the four suppression guards, the preview guard and the picker entry gone; migration `d4a71f6e30bb` detached 2 templates and deleted 4 rules. Closed **D13** and **D16**. A review found the rule-less shape had no edit story -- an edit never reached its Transfer, $500 vs $700 -- so it also propagates; `transfers/_instances.py` is that split |
| **R3** -- the forward occurrence engine | `4b5c577b` | `occurrences` / `place` / `occurrence_placements`, parallel and unread; the month walk moved to `_months.py` so the anchor IS its first element. 416 of 428 oracle shapes reproduce the blob; all 46 live prod rules agree. Closed **D9**; exposed **D18-D21** |

## What these steps bind on later ones

Carried forward because a later step would otherwise rediscover it the expensive way:

- **Every subtype table carries a surrogate `id`**, not the design's `recurrence_rule_id` primary
  key. `system.audit_trigger_func` assigns `v_row_id := NEW.id`, so an INSERT into an audited table
  without that column dies with `record "new" has no field "id"` -- measured on a probe table, not
  reasoned. `UNIQUE (recurrence_rule_id)` enforces the identical 0-or-1 cardinality.
- **`c8f2b6a41d93` was amended in place**, which was safe only because it was the chain head and had
  never left `dev`. Its docstring carries the literal SQL for the one case that needs a hand: a
  backup restored from between `86b9eaa3` and `1e5e3430` already reads that revision, so
  `flask db upgrade` is a no-op and the withdrawn columns survive with nothing able to remove them.
- **`end_date >= anchor_date` was deliberately NOT added**, and there is no `anchor_date` column for
  it to name. It lands with the column at R7c, with the Marshmallow validator that can refuse the
  pair at the door -- `end_date` is user-authored and live, and 14 live rules resolve to a future
  anchor, so the CHECK alone would turn "stop this recurring bill" into an unhandled
  `CheckViolation` out of `update_template`'s autoflush.
- **A rule is written whole, through `app/services/recurrence` and nowhere else.** `RecurrenceSpec`
  is pinned by test to be exactly the table's non-DB-assigned columns, so a column added without it
  becomes unauthorable loudly rather than in silence.
- **The three `ref` vocabulary tables are NOT audited** and seed without literal ids -- the `ref`
  schema is excluded from `AUDITED_TABLES` with one multi-tenant exception, and a literal-id seed is
  what leaves an identity sequence behind its data (finding F-1).
- **R7c inherits R2's whole blast radius** (ruling R-R5, now spent). The moment the two-axis columns
  are NOT NULL every INSERT must supply them: 5 production writers plus **83 direct
  `RecurrenceRule(...)` constructions across 39 test files**. R2 was split three ways to avoid one
  enormous commit; R2d dissolved the problem instead, so none of those 83 was touched -- a partial
  construction is still a complete rule. R7c is where that bill finally comes due.
- **R1's baseline freezes some WRONG answers on purpose.** The `long_cadence.*` shapes and four
  `bounds.*` blocks record defects D3 and D5 as they behave today, so R4 -- not R3 -- re-freezes
  exactly those lines with `SHEKEL_UPDATE_RECURRENCE_BASELINE=1`.

## Closed defects, with the measurement that found them

Kept here rather than in the live plan: a defect whose step has shipped is history, and the live
document's line cap is for work that has not happened yet (section 7 rules 4 and 5).

**D1 -- an amount-only edit re-phased "every N paychecks"** (closed by `2fca91bc`). No
`offset_periods` input exists in any template under `app/templates/`, but
`update_recurrence_rule_from_form` wrote `rule.offset_periods = data.pop("offset_periods", 0)`, so
the schema default landed on every edit. Probe, route-level with a real form payload:

```text
created with offset=1 (start period_index=7, interval=2)
after an amount-only edit:
E   AssertionError: assert 0 == 1
```

Every future occurrence shifted by one pay period. Latent in data only because no live rule uses the
pattern. Closed by deriving the phase from the rule's own start period on EVERY write, not only on
create.

## Rulings archived with their steps (2026-08-07)

Moved out of the live plan under rule 5 when the 900-line cap bound: both were taken FOR a step that
has now shipped, and both are restated in the code they produced. Read
`app/services/recurrence/_resolution.py`'s module docstring first -- derivations 2 and 3 there are
R-R8, and the "Nothing persists what this returns" opening is R-R10.

**R-R8 -- a period-unit anchor is the BOUND, not a period boundary** (ruled 2026-08-05, built into
`2fca91bc`). R2b had anchored a pay-period-space rule on "the START of the first period ending on or
after the bound", which is not always derivable: `loan_recurrence_sync._sync_loan_cadence` stamps
`start_date` onto ANY rule, so a loan originating past the materialised horizon left no qualifying
period at all. The anchor holds the effective start ITSELF -- an occurrence is a DATE and
`placement` is what carries it onto a period, so a period start in the anchor puts the result of the
placement axis into the anchor axis. Under `CONTAINING_DATE` both readings select the same period
whenever the schedule covers the bound, which is why all 11 live period-unit rules resolve
identically either way. `Every N Periods` is the exception and keeps a phased boundary
(`_phased_period_anchor`); `Monthly First` answers in one step rather than scanning a horizon.

**R-R10 -- a derivation is not stored beside its own inputs** (ruled 2026-08-07, built into
`1e5e3430`). Superseded R-R9's read-only-column write door: measured on SQLAlchemy 2.0.49 that
mechanism blocks 2 of 6 write paths, and in Python no mechanism blocks all six. So the state is
deleted rather than guarded -- `unit_id` / `anchor_date` / `placement_id` / `shift_id` are computed
by `resolve()` and stored nowhere, which is also why R2c-2 and R2c-3 were deleted rather than
deferred. The argument is stronger than "a derivation beside its inputs": `anchor_date` depends on a
FOREIGN, independently mutable table, and it had already gone stale -- a schedule reset stranded 3
of the 50 live rules.

### What R-R10 binds on R7c

Carried forward rather than archived with the rest, because R7c has not run: **one backfill still
needs the derivation IN a migration.** The copy is not eliminated, it is made single-use and
destroyed with its inputs in the same transaction. Measured against the 50 live rules, 49 anchors
need only `GREATEST(schedule opening, start_date, start_period.start)` -- Postgres `GREATEST` skips
NULLs, so that is the `_effective_start` maximum exactly -- and one (`Monthly First`) needs a scan.

**R-R4 -- a `Once` rule is retired at R2e, before anything must model it**
(ruled 2026-08-05, AMENDED 2026-08-07, built into `eef43eef`).

The original ruling deferred the deletion to R9 and gave `Once` inert two-axis values in the
meantime, because NOT NULL was coming and the transfer form has no null option. R2d removed the NOT
NULL pressure, and two neutral reviews then showed the deferral costs more than it saves: `Once`
resolves to EXACTLY the `Every Period` value, so a consumer holding only a `ResolvedRecurrence`
would generate a `Once` rule forever, and `(1, period, containing_date)` naming two patterns is what
would force R7c's downgrade to refuse rather than round-trip.

**Amended ruling: retire it at R2e, before R3.** The new engine then never models "does not recur",
and the resolved value is unambiguous before anything consumes it. Until the retirement shipped,
`pattern_id = Once` REMAINED what suppressed generation -- the four guards ran before the engine was
consulted, so the ambiguity was contained to a value nothing read yet.

**R-R11 -- `Once` cannot be retired in one leaf, and the `ref` row outlives the enum**
(ruled 2026-08-07, built into `4d99c9d4` + `a465e9fa` + `fcea8b3c` + `eef43eef`).

Building R2e surfaced two conditions that had to hold before the retirement was even safe, each a
measured defect rather than a refactor, so the step became three leaves -- R2e-1, R2e-2, R2e-3, all
listed with their commits in the table above.

**The deploy half is the ruling that changed the design.** Deleting the `ref.recurrence_patterns`
row was proposed first, because the picker is built from that table and a surviving row would still
offer `Once`. Measured: `ref_cache.init` raises `RuntimeError` for an enum member with no row
(`ref_cache.py:290`), the entrypoint runs migrations before the seed, and `shekel-deploy` rolls back
to the PREVIOUS image on an unhealthy deploy -- so the row's deletion would leave both images unable
to boot, on exactly the deploy where the safety net is needed. The developer refused that trade, and
the refusal produced the better design: **drive the picker off the enum instead** (R2e-2). The app's
model of the vocabulary is `RecurrencePatternEnum` -- `_pattern_member` raises for anything else --
so a table-driven picker was always able to author an unresolvable rule. With the picker enum-driven
the row is unreachable rather than merely unused, and expand/contract costs nothing: R9 already
drops the table.

**What R2e-1 had to fix first.** Two defects the retirement would otherwise have shipped onto a
second surface: **D14** (the null option was a silent no-op that then regenerated) and, through it,
**D15** -- a loan payment could be made "one-time", which nulls the column
`recurring_transfer_query` finds it by and silently drops the standing overpayment the balance seam
threads (measured 250.00 -> 0.00 with the `loan_payment_settings` row still asserting 250.00).

## What R2e and R3 bind on later steps

- **`Once`'s `ref.recurrence_patterns` row SURVIVES to R9** (ruling R-R11). The NEW image does not
  need it -- `ref_cache.init` iterates the RUNNING image's enum and a surplus row is invisible --
  but the PREVIOUS one does, and cannot self-heal: `scripts/seed_ref_tables.py` calls plain
  `create_app()`, so `ref_cache.init` raises BEFORE the upsert. Deleting the row would leave the
  auto-rollback image unable to boot, on exactly the deploy where the net is needed. Ruling R-R4 is
  why the retirement ran at R2e rather than R9: `Once` resolved to EXACTLY the `Every Period` value,
  so a consumer holding only a `ResolvedRecurrence` could not tell them apart and R7c's downgrade
  could not round-trip. Pinned by `TestDeliberateRefSeedSurplus`.
- **A rule-less TRANSFER template still materialises ONE Transfer** (unlike a rule-less TRANSACTION
  template, which generates nothing), so it needs both halves: `materialize_initial_transfers` on
  create and `propagate_to_non_repeating_transfers` on edit. An ACCOUNT change is refused, scoped to
  "a live Transfer exists". `app/routes/transfers/_instances.py` is the split.
- **R3's engine reads `offset_periods` for the PERIOD unit's phase**, and R7c drops that column --
  see the live plan's ledger row D21 for why an anchor-derived phase is not equivalent.
- **The R1 oracle's long-cadence builder must cover every pattern whose matcher reads a period's
  ENDPOINT months.** It covered `MONTHLY` and `QUARTERLY` only, so `MONTHLY_FIRST`, `SEMI_ANNUAL`
  and `ANNUAL` were unmeasured at any cadence but the developer's. R3 added them; the rule is
  written into `_add_long_cadence_shapes`' docstring so the next pattern inherits it.
