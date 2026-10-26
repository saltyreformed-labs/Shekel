> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# X-am as built: the settled band has two members (2026-08-27)

**What this is.** The record of plan step `balance:X-am`, shipped at `7b0ddae8`, and the home of
finding **N-177** now that its fix has shipped and its row has left `../../../plans/ledger.md`.
**The COMMIT is the record**; read the code it shipped, not this file.

## N-177, as it stood and as it turned out

| | |
|---|---|
| **the row, verbatim** | The `Settled` status has no writer, no reader that distinguishes it, and zero rows on both tables. Worst measured: `$0.00`; a THIRD member in the predicate every balance rule in this arc is written against |
| **what survived re-measurement** | The writer half and the zero-rows half. **The reader half was FALSE by the time the step ran** |
| **what closed it** | `7b0ddae8`, ruling **R-HA** |

**Its own predicate had rotted, and that is the first thing the step found.** Two readers that DID
tell the archive apart were added after the row was written: `balance_predicates.is_archived` (plan
step `X-ap`) and `not_archived_clause` (`bank_import:X-f6a-3b`), with four call sites between them
-- two refusal branches in `entry_service/_refusals.py` and two query clauses in
`statement_match/_candidates.py`. Both were guards over a state carrying zero rows, and both are
deleted with it. `feedback_a_ledger_rows_diagnosis_can_be_wrong`'s class, on this arc's own ledger.

## What the step measured before it decided

* **Nothing has ever carried it.** The load-bearing evidence is the AUDIT TRAIL, not the dumps:
  1,591 `system.audit_log` rows over `budget.transactions` and `budget.transfers` since 2026-05-07 --
  229 of them DELETEs and 208 status changes -- name it nowhere, which is what rules out a row that
  was archived and hard-deleted between two snapshots. Every snapshot reads zero besides, but the 18
  `pg_dump` archives are successive pre-deploy captures of ONE database over 2026-08-05 to
  2026-08-27, and dev is a CLONE of production -- one population under several names, not many
  observations. The 2026-05-01 dump is the only observation before the triggers existed.
* **It was a trapdoor, not a lock.** Driven through the real routes: archiving returned 200 and the
  revert returned 400 with no unarchive door anywhere; the popover then rendered every other option
  `disabled`; adding or removing a purchase was refused; the row dropped out of bank-import
  matching; and the DELETE control on the same card removed it and reversed its ledger postings.
* **`Paid` already did the only useful half.** `is_immutable` is true for Paid and Received, so
  `state_machine.finalised_edit_rejection` already refused amount / period / category / due-date
  edits on both. The archive's only distinct content was *and you may never revert*.

## The rulings and obligations this left behind

| what | where it now lives |
|---|---|
| **R-HA** -- the archive is deleted; a row's finishedness is PROVENANCE, and no state may be both reachable and absorbing | `../../../plans/rulings.md` |
| **CC3b owes a `deletion_refusal` arm** for a terminal `Credit`, or a stated reason the destroy-but-not-correct asymmetry is acceptable there | the live `X-am` entry in `../README.md` section 5, and `state_machine`'s module docstring |
| The archive half of `bank_import:R-GG` (d) is VACUOUS, not relaxed; its stored-figure half stands | `entry_service/_refusals.py:removal_refusal` |

## Two things the adversarial reviews changed, recorded because they were nearly shipped

1. **The invariant was first written as "no map has a terminal state"**, which contradicted
   `credit_card` locked ruling 5 (2026-07-19, *do not reopen*) on the day it was written -- that
   ruling makes `Credit` terminal at `CC3b`. The law that ships is the CONJUNCTION: absorbing AND
   reachable. A retired vocabulary item nothing can enter is legitimate, and CC3b builds one.
2. **A test was deleted on an "unreachable" argument that was measured FALSE.**
   `statement_match._release`'s `removal_refusal` arm was said to have no reachable caller state once
   the archive was gone; the production docstring named the second route on the same page -- a
   container RE-CLOSED AT A STORED FIGURE, reached by unticking *Track individual purchases* on a
   settled row and typing an Actual. Driven end to end, the panel refuses with *records a fixed
   figure*. `lessons.md`: an ONLY-way argument is one writer from wrong.
