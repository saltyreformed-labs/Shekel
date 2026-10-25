> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Recurrence redesign, as built: the three shipped `R-F*` accounts (2026-08-15)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_recurrence_redesign.md`; this record exists so that document's
shipped entries can be pointers rather than accounts of themselves (`conventions.md` rule 5).
Archived to make room for R10-a's specification, under the same rule that says to shrink the record
of what is DONE and never the specification of what remains.

Each entry below is the full text that stood in section 4 before this archive. The live document
now carries one line for each, with its hash.

---

## R-F1 -- the lagging `ref` identity sequences are in step (F-1)

`44b25ad3`, migration `c7f3a9d1e864`, on `dev`. A census of every serial sequence in all five
application schemas found exactly the five. **Two corrections to the spec it replaced**: the
drafted `GREATEST(max(id), 1)` takes the greatest against a literal floor rather than the
sequence's own position, so it would LOWER a sequence sitting ahead of its data; and the repair
cannot live in `ref_seeds` -- `setval` needs UPDATE, which the app role measurably lacks.

## R-F10 -- delete the gap machinery

`fe365de1`. The same commit as `pay_calendar:C5a`, ticked at that arc's **C2-b2**: a period's end
is derived from the next payday, so a hole is not a state a reader can see. Closed **F-10**. The
LOSS survives the state -- an absorbed hole leaves an over-long period, which is that arc's **P16**
and its `C5b`. Deletion-only; the 430-shape baseline stayed byte-identical.

## R-F13 -- a baseline REGENERATION run can no longer report success (F-13)

`b97ec1c3`. TWO of its three holes no longer existed and were NOT rebuilt: `PlacementOutcome`, the
`OccurrencePlacement` invariant and the `SCHEDULE_GAP` / `BEYOND_THE_SCHEDULE` members died at
`pay_calendar:C2-b2` (`fe365de1`). The third survived: the 430-shape gate SKIPS while
`SHEKEL_UPDATE_RECURRENCE_BASELINE` is set, and a skip reads as a pass. Shown to fire -- switch on
1 failed / 7 passed / 1 skipped (was 8 passed, 1 skipped), switch off 9 passed.

## R-F2 -- the ref-seed parity scan ends a statement where the SQL does (F-2)

`672c18b1`. Not another keyword: a statement lives inside a Python STRING LITERAL, so the literal
is the outer bound and the keyword list stays as the inner one. Census: all 78 `INSERT INTO`
occurrences in 38 migrations sit inside a string constant, 2 constants carry more than one.
Controls SHOWN to fire against the old reader -- a literal below the seed read as seeded, and a
docstring-only INSERT counted as one.

## R-F3 -- a `ref` table's generated PK/UNIQUE names ARE the rule (F-3)

`e37b736c`. Ruled 2026-08-14 as recommended: the standard exempts the single-column
`PRIMARY KEY (id)` and `UNIQUE (name)` on a `ref` lookup table, stated in BOTH places the rule
lives. Measured against the live schema rather than the plan's estimate -- **24 of 24** carry
`<table>_pkey` and `<table>_name_key`, none a `uq_` name. Rejected: a rename migration across 24
tables, for names nothing references.

