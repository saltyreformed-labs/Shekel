> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Recurrence R7c-c, as built: the closed pattern set dies (2026-08-16)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_recurrence_redesign.md`; this record exists so
that document's step entry can be a POINTER rather than an account
(`conventions.md` rule 5).

Migration `d9f5c1a48b73`, `down_revision` `b6d41f0a9c27`. R7c-c is the CONTRACT
half of the recurrence redesign's expand / migrate / contract: R7c-a
(`370a30cc`) added the two-axis columns and dual-wrote them while nothing read
them, R7c-b (`900e761a`) moved every reader and the form across, and this leaf
dropped what they replaced. The destructive DDL is therefore last, and no leaf
wrote a translation shim.

## What it dropped

`pattern_id`, `day_of_month`, `month_of_year`, `start_date`, `start_period_id`
and `offset_periods`, plus the unwritten `budget.recurrence_month_anchors`
(**R-R13**) -- so `EXPECTED_TRIGGER_COUNT` moves 43 -> 42, the number the
container entrypoint asserts at start. `interval_n` is not dropped but
RE-POINTED in the same migration: the write door had stored `1` for every
pattern whose interval was in its NAME, so the four live Quarterly and
Semi-Annual rules read as MONTHLY at face value (Quarterly to 3, Semi-Annual to
6).

`encode_cadence`, `decode_pattern`, `PATTERN_DERIVATIONS`, its computed
inverse, `stored_interval`, `pattern_member` and `modelled_pattern` leave
together; `cadence_of` moves to the read door and takes a RULE, which is what
its two outside callers already held.

## The four rulings

**D37 -- `day_of_month` goes, and its reader reads the derivation** (developer,
2026-08-16, **R-R20**). `recurrence_engine.compute_due_date` dates every
generated row from that column and R5 is what deletes that function, four ranks
later behind the balance cutover; the row named two ways out and the developer
took a third. The column was a derived ENCODING --
`resolved.day_of_month if fires_on_day_of_month(unit, placement) else None` --
whose every input survives, so `recurrence.scheduling_day_of_month` answers it
and the migration GRADES the equality in SQL before the `ALTER TABLE`. Measured
0 of 46 disagreements on a production clone. The gate is
`fires_on_day_of_month` (the `(unit, placement)` pair) and NOT
`has_day_of_month_coordinate` (the unit): the two disagree on exactly one
cadence, a MONTH-unit rule funded from a month's FIRST paycheck, where the
column was always NULL and NULL is what dates the row from its paycheck.
Answering the day there would be ledger row **D26**'s fix arriving in the wrong
step, measured at 11 rows.

**D32 -- the "Funded from" row is always OFFERED** (developer, 2026-08-16,
**R-R21**). The measured defect ceases to exist rather than being warned about:
a placement belonged to the `(unit, interval)` PAIR only because
`MONTHLY_FIRST` had no quarterly twin, and the MONTH unit now offers both
placements at every interval. Two cadences still admit one -- PERIOD, where the
placement is provably inert, and YEAR, whose first-paycheck anchor is R8's --
and for those the row renders with help text saying so, because hiding it is
what let a funding rule change unseen.

**D38 -- closed on R-R15, nothing built** (developer, 2026-08-16, **R-R22**).
Both affected rules fire on the same cycle either way -- `3 = 6 (mod 3)` and
`3 = 9 (mod 6)` -- so no generated row and no projected balance moves; the typed
month was a second spelling of the residue class the first occurrence carries,
not a second fact.

**D6 -- closed, with a NEW row for the residual** (developer, 2026-08-16).
`start_date` had no reader and no writer after R7c-b, and D6's own wording
("folding `start_date` into `anchor_date` is lossy") is answered by ruling
**R-R16**: there is no fold, the first occurrence IS the opening bound. What
survives is a different fact and carries its own id, **D39**, owned by R5 -- an
every-paycheck loan payment's stored first occurrence is the PAYDAY of the
paycheck hosting the first contractual installment, not the installment date,
so the bound can precede the loan's origination by up to a pay period. `$0.00`
today: both live loan payments are Monthly.

Pay-calendar row **P11** closes with `offset_periods`. While the ordinal was
stored, inserting a payday before an existing one re-phased every
`Every N Periods` rule; the phase is derived from the first occurrence on every
read now, so there is nothing left to re-phase.

## Two more decisions inside the step

**`(12k, MONTH)` is CANONICALISED to `(k, YEAR)`** (**R-R17**), inside `resolve`
rather than in the write door alone -- so the form's live preview, the Recurring
cell and the stored row cannot word one rhythm three ways. Guarded on the two
spellings resolving the SAME way, because `(12, MONTH, first paycheck)` is a
real cadence whose YEAR spelling `anchor_family` refuses.

**Two hazards the freed interval created, both closed at their root.** A
calendar walk stepping past year 9999 raised a bare `ValueError` -- outside the
package's hierarchy, so the preview answered a stack trace to any signed-in
user; `_months.walk_months` now stops at the last month the application's
calendar reaches, and such a cadence fires ONCE. And deleting `encode_cadence`
deleted the write door's only completeness refusal, so the preview began listing
dates the schema refuses (measured: five, for the WEEK unit);
`require_authorable_cadence` restates it over the set the FORM offers, which is
what keeps picker, schema and door one answer.

## What the adversarial reviews found, and what was fixed for them

Two reviews ran against the built step and a third against the fixes. Beyond
the stale-prose findings, which are repaired in the shipping commit, four
defects were found and fixed at the root:

* **A cleared interval box silently re-cadenced a bill.** The form's months
  `<select>` became one free number box in this step, and a cleared number input
  submits `""`, which `_normalize_empty_inputs` DROPS. Both write doors and
  `validate_authorable_cadence` then defaulted to `1`, so editing a quarterly
  bill with the box emptied stored "every 1 month" -- 12 occurrences a year
  where 4 were owed, across the whole projection, with nothing on screen saying
  so. The shape was already reachable for the PERIOD unit's free box; freeing
  the interval widened it to every unit. **Fixed at the submission, not with a
  fourth presence read**: unlike `starts_on` and the closing bound, the
  interval box has no locked or app-derived state, so absence beside a named
  unit is always a malformed submission -- two states, not the three ledger row
  **D36** describes. Refusing it deleted three defaulted guesses rather than
  adding a guard.
* **A shipped migration stopped replaying.** `48e2c7ee593d`'s backfill imported
  `recurrence_engine.compute_due_date` and called it through `SimpleNamespace`
  stand-ins; this step pointed that function at columns which do not exist at
  that revision, so every replay over a NON-EMPTY database raised
  `AttributeError` and aborted `flask db upgrade` mid-chain. Invisible to CI and
  to `build_test_template.py`, which replay against an EMPTY database where the
  loop never runs. The arithmetic is FROZEN into that revision now and the
  import deleted -- the rule `d9f5c1a48b73` states for its own pattern table --
  and graded against the original by differential sweep over 153,600 input
  combinations, 0 disagreements.
* **`interval_n` had no upper bound**, so a crafted POST of `2147483648` reached
  the flush as an unhandled `NumericValueOutOfRange`. Latent for three of the
  four units until this step: while the closed set was the storage,
  `is_authorable` refused any MONTH or YEAR interval above 6. Bounded at the
  column's own domain, the precedent `max_occurrences` already carried.
* **Three schema refusals were dead copy.** `interval_n` (this step's),
  `starts_on` and `nominal_day` (R7c-b's) named controls the user could fix and
  reached `_form_errors.ACTIONABLE_FLASH_FIELDS` as unlisted keys, so each
  redirected to "correct the highlighted errors" on a page that highlights
  nothing. The gate written to prevent exactly this asked only the converse --
  that no entry names a field nothing declares -- so it caught a RENAME and was
  structurally blind to an ADDITION. The reverse arm is what shipped with the
  fix.

## Verification

**Measured on a 2026-08-16 production clone, both directions.** Generation is
BYTE-IDENTICAL across the cutover: 46 rules, 880 placed occurrences with their
pay period and due date, driven against the `origin/dev` tree in a second
worktree so the before side runs the code that shipped. The downgrade restores
`pattern_id`, `interval_n`, `day_of_month`, `start_period_id` and
`offset_periods` exactly (0 of 46 differ), returns `month_of_year` in the same
residue class on the 2 rules D38 names, restores `start_date` empty on the 4
that held one, and generates byte-identically to the pre-upgrade database. It
REFUSES rather than guessing for a cadence the closed set cannot name -- which
this leaf is what makes authorable.

The frozen 434-shape baseline did not move: `recurrence_baseline.py`'s shapes
state the two-axis cadence and every LABEL is unchanged, which is the same
substitution R7c-b made for the anchor.

Both of the migration's refusals are DRIVEN by
`tests/test_models/test_closed_pattern_set_dies_migration.py`, which also grades
the SQL scheduling-day derivation against the Python one over the cases the two
could disagree on. `tests/test_models/test_recurrence_start_bound_fold.py` is
deleted and `test_recurrence_two_axis_backfill.py` lost its backfill half: both
graded SQL that reads dropped columns, so neither can execute against a database
at head. Each loss is stated in the file that replaced it.

## The R7c container's account, archived with it

R7c ticked with this leaf, so its entry became a pointer and the account
below moved here under `conventions.md` rule 5.  Several of its paragraphs
are SUPERSEDED by the leaf they were written to constrain -- the month
interval is a free box now, and a placement no longer belongs to the
`(unit, interval)` pair -- which is what makes them history rather than a
rule a reader still has to keep.  The one standing mandate it carried, that
any step changing the form's controls runs `tests/manual/
verify_recurrence_form.py`, was hoisted into section 4's preamble instead.

(`ee35bca7`), its last leaf.

Split with the developer 2026-08-14 (R-R18) as an expand / migrate / contract: `R7c-a` adds the
two-axis columns and dual-writes them while nothing reads them, `R7c-b` moves every reader and the
form onto them, `R7c-c` drops the closed set. The destructive DDL is therefore LAST, and no leaf
writes a translation shim -- the alternative split, which kept the schema still until the end,
needed three (a route composing `starts_on`, a write door decomposing it back, and a read door
recomposing it behind a `calendar` argument the last leaf would remove again).

**What R7b left for the CUTOVER** sits here rather than in its own entry, because a shipped step's
entry is a pointer rather than an account (`conventions.md` rule 5) and every ruling below binds all
three leaves. R7b's four leaves have all shipped, each authoring through the closed-set columns, so
the schema does not move until this step.

**Two rulings taken 2026-08-12 on the shape of the cadence controls, which R7c must obey.** They are
LINKED rather than independent: the unit repopulates the interval control and the placement select,
which renders only where a `(unit, interval)` pair offers more than one. And the month interval is a
SELECT of 1 / 3 / 6 rather than a number box, because those are the only month cadences the closed
set stores; R7c widens it to a free box when the authored columns land. Three free controls would
offer combinations `encode_cadence` refuses, which is the refusal this design makes unreachable.

**A placement belongs to the `(unit, interval)` PAIR, not to the unit**, and a design keyed on the
unit alone re-opens exactly what that ruling closes. `MONTHLY_FIRST` is
`(1, MONTH, PERIOD_STARTING_ON_OR_AFTER)` and the closed set has no quarterly or semi-annual twin,
so `(3, MONTH, first-paycheck)` is unstorable -- measured, not argued. The offer set therefore
carries whole triples and every consumer filters them. Row **D32** is the cost still owed on it: a
pair that drops the first-paycheck option rewrites the funding choice with nothing on screen saying
which paycheck now pays.

**Where the anchor-family router lives, ruled 2026-08-13.** `anchor_family` and the `FAMILY_*`
constants sit in `_frequency`, not `_resolution`: WHICH derivation a `(unit, placement)` cadence
uses needs no schedule, which is that module's charter, while WHERE the anchor lands needs the whole
calendar. `fires_on_day_of_month` is its projection, and it is what the form asks rather than
keeping a second list of which cadences have a day-of-month coordinate.

**THE PHASE IS A DERIVATION OF THE OPENING BOUND** (developer ruling 2026-08-14, option C of three),
and R7c inherits it. `_derive_offset_periods` answers
`span_containing(effective).period_index % interval_n` -- the ordinal of the paycheck the bound
falls in -- so nothing authors a phase and `offset_periods` is written but never read back. The
alternative was to keep the stored column, which would have opened a new every-N-paychecks rule up
to N-1 paychecks LATE for its whole life. Three consequences R7c must not undo: the `FAMILY_PERIOD`
anchor IS the bound (`_phased_period_anchor` existed only to reconcile two independent statements of
one cadence); `ck_recurrence_rules_valid_offset` is unviolatable rather than mirrored, retiring one
of **D23**'s two remaining mirrors; and the divergence `_occurrence._period_walk` recorded for R7c
-- the advance falling back to the raw bound and generating nothing -- is unreachable, because the
paycheck the bound falls in is in phase by construction. `span_containing` rather than
`period_containing` is what keeps it TOTAL past the horizon, which R7c's NOT NULL columns need.

**The migration's downgrade restores NOTHING, and that is the ruling rather than a shortcut.** The
old code read `start_period_id` for two things: the opening maximum, which `start_date` now HOLDS,
and the phase, which fell back to the `offset_periods` COLUMN -- and the write door always wrote
that column with the value the FK would have derived. Re-deriving the FK would NOT be neutral: a
rule that always had a `start_date` and never had a start period (every loan payment) would newly
acquire one and re-phase. Exercised in both directions on a production clone.

## The pattern mapping this migration derived from (archived 2026-08-25)

Moved out of the live arc document under `conventions.md` rule 5 when `R7d`'s decomposition needed the room. It is the derivation `d9f5c1a48b73` ran: how each of the eight closed pattern names became a `(interval, unit, placement, starts_on)`. The columns it maps FROM no longer exist -- `R7c-c` dropped them and `R9` dropped `ref.recurrence_patterns` with its enum -- so nothing live can depend on it, which is what made it archivable.

### Pattern mapping (migration derivation)

| today | (interval, unit) | placement | starts_on |
|---|---|---|---|
| Every Period | (1, PERIOD) | CONTAINING | start_period.start_date, else first generated row's period start |
| Every N Periods | (N, PERIOD) | CONTAINING | the period whose `period_index % N == offset_periods` |
| Monthly | (1, MONTH) | CONTAINING | first date with `day = day_of_month` on/after the rule's effective start |
| Monthly First | (1, MONTH) | **STARTING_ON_OR_AFTER** | the 1st of the first month whose OWN first paycheck clears the effective start (R-R6) |
| Quarterly | (3, MONTH) | CONTAINING | (month_of_year, day_of_month) |
| Semi-Annual | (6, MONTH) | CONTAINING | (month_of_year, day_of_month) |
| Annual | (1, YEAR) | CONTAINING | (month_of_year, day_of_month) |
| Once | rule DELETED, FK nulled | -- | -- |
| *impossible today* | **(2, MONTH)** every other month | | |
| *impossible today* | **(2, YEAR)** every two years | | |
| *impossible today* | **(1, WEEK)** / **(2, WEEK)** weekly / biweekly by date | | |

Equivalence is provable for the calendar family: old quarterly fires in months
`{moy, moy+3, moy+6, moy+9}` forever, new fires at `anchor + 3k` months, and
`anchor.month === moy (mod 3)` by construction -- identical sets. Same for semi-annual (mod 6) and
annual (mod 12). Month-end **clamping is preserved** (`recurrence_engine.py:546`), so the live
`Walmart+ Membership` rule (day 31, March) stays 31 March.

