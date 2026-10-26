> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The recurrence FINDINGS-STEPS span, as built (2026-08-17)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_recurrence_redesign.md`.

Archived at plan step **bank_import:X-f6a-2**, which needed the room:
`steps.md` had reached 241 of its 260-line cap and `conventions.md` rule 5
admits exactly one way back under one -- archive a COMPLETED span, condensed
to its id, its commit and what it closed. Never trim a live step.

**Why THIS span.** These are the recurrence arc's standalone findings-steps:
each closed one `F-` finding from the arc's own audit, each shipped on its own
commit, and none is a leaf of any live container or a blocker of any open step.

**Two of the family are deliberately NOT here.** `R-F10` is an identity class
with `pay_calendar:C5a`, and rule 11 makes an identity class share one tick
state and therefore one home. `R-F1` stays in `steps.md` because a
`tools/plan_gate` control -- `test_a_prefix_derivation_would_have_fired_falsely_on_this_corpus`
-- uses it as the worked example of the id-PREFIX trap that makes a decomposed
parent DECLARED rather than derived: `R-F1` is a string prefix of `R-F10`,
`R-F12` and `R-F13`, which are unrelated steps. Archiving it emptied that trap
out of the corpus entirely (measured: zero prefix traps remain in any arc), so
the control lost its premise. Leaving the row in place was the smaller move
than editing a gate that a rule-5 archival had merely walked into; the
control's own brittleness is recorded as **D42**.

| arc | id | commit | what it closed |
|---|---|---|---|
| recurrence | R-F2 | `672c18b1` | **F-2** -- the ref-seed parity scan bounds a statement where the SQL does, at the Python string literal carrying it, with four negative controls shown to fire against the reader it replaces |
| recurrence | R-F3 | `e37b736c` | **F-3** -- a `ref` lookup table's single-column PK and UNIQUE take PostgreSQL's generated names, stated in both places the constraint-naming rule lives; measured 24 of 24 live `ref` tables |
| recurrence | R-F8 | `2e63e4f9` | **F-8**, **F-14** -- the deploy's safety net stops lying: back up unconditionally, pre-flight the rollback, and refuse the one that cannot work |

**Nothing was carried without re-verification, because nothing was carried:**
all four are closed and all four of their findings left `ledger.md` when they
shipped. What survives them is the code at those four commits.
