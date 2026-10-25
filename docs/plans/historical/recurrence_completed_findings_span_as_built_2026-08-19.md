> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Four completed recurrence steps, archived 2026-08-19 and 2026-08-20

Four completed recurrence steps, condensed to one line each under
`conventions.md` rule 5, archived out of `docs/plans/steps.md` in TWO passes:
the first two on 2026-08-19 when `recurrence:R-F16` needed the room, the last
two on 2026-08-20 when `recurrence:R-F17` needed it -- which is what the
closing paragraph of the first pass predicted. The file keeps its original
date in its NAME, because a filename is cited and renaming one orphans the
citation; the passes carry their own dates here instead. Cite this for how any
of them came to be; never as a plan of record. The code as committed is the
source of truth.

All four are the shape the 2026-08-17 span used: each closed a finding on its
own commit and **blocked nothing** -- no `steps.md` row names any of them as a
blocker, which is what makes removing them safe under rule 13. Each was also
checked to have no citation in a LIVE planning document beyond its own two
records, which is rule 5's third condition; every remaining citation of all
four is in code comments and test docstrings, where rule 15 permits it and rule
2's append-only ids exist to keep it resolvable.

| step | commit | what it closed |
|---|---|---|
| `recurrence:R-D33` | `dd2a5a34` | **D33**. Both closing bounds answer "is this still a commitment" from whether the rule still OWES an occurrence, so a DATE bound stops counting at its bound date and two spellings of one schedule cannot leave the obligations total on different days. |
| `recurrence:R9` | `800671a7`, migration `b2e9a47c3f18`, ruling **R-R27** | The closed pattern set's LAST artefacts: `ref.recurrence_patterns` dropped with `RecurrencePatternEnum`, `ref_cache.recurrence_pattern_id` and the seed entry behind them, in ONE release rather than the two R-R11 had reserved; the suite's cadence vocabulary moved onto the two axes. Opened **D41** / **R12**. Its full account -- the rollback measurement, the production census and the clone rehearsal -- is `recurrence_r9_as_built_2026-08-17.md`. |
| `recurrence:R-F6` | `a679bb2e`, migration `e7a2c4f18d05` | **F-6**. A rule is OWNED BY its definition: the owning FK inverted onto `budget.recurrence_rules` as an exclusive arc under `ON DELETE CASCADE`, so a hard-deleted template can no longer strand its rule and the orphan is inexpressible rather than swept. 3 such rows on production, deleted with it. `recurrence_rules.user_id`, `_rule_is_exclusively_owned` and `integrity_check`'s **OR-02** all went with it. **Its one live obligation moved to `R5`'s specification when it was archived**: that step must re-examine `build_transient_rule`, whose last callers are tests needing a rule only because `compute_due_date` takes one. |
| `recurrence:R7a-2b` | `7c417b90` | The monthly equivalent became ONE expression over `(interval_n, unit)`, and the infrequent badge derives from the same pair; `amount_to_monthly` and the unmodelled-pattern `None` arm were deleted. Its fuller account was already archived to `recurrence_findings_as_built_2026-08-15.md`. |

## Why the obvious neighbours did NOT go with them

Stated so the next session does not re-derive it:

- **`R7c-c`** looks archivable (no blocker names it) and is not: its entry
  carries a LIVE obligation -- "`R5` deletes `compute_due_date`, and the WEEK
  unit `R8-b` frees is where that migration's SQL and its Python twin part" --
  and rule 5 forbids a live sentence depending on an archived one.
- **`R7c`** is `pay_calendar:C6`'s blocker (`after #17 / pay_calendar:C4 /
  recurrence:R7c (shipped)`), so archiving it would orphan a key rule 13
  grades.
- **`R7a-2a`** is `recurrence:R-F17`'s blocker, so archiving it would orphan a
  key rule 13 grades. `R7a-2b` went in the second pass and `R7a-1` did not:
  three live sentences cite `R7a-1` (this document's own section 4 prose and
  ledger rows `D29` and `F-15`), where `R7a-2b` had none outside its two
  records.
- **`R-F1`** stays for the reason its own row states: a `tools/plan_gate`
  control uses its id as the worked example of the PREFIX trap.

`steps.md` stood at 240 lines against a 260 cap after the first pass -- AT the
20-line headroom floor, not under it -- and the second pass is what that
sentence predicted. After both passes and `R-F17`'s own row changes it stands
at the count `steps.md`'s own header states, which rule 3 grades. The next step
to need room in this arc should expect to archive again; `R7a-1` is the
cheapest remaining candidate once its three live citations are re-pointed.
