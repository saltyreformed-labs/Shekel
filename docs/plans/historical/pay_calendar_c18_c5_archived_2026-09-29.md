> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The pay-calendar plan's `C18` and `C5` spans, condensed under rule 5 on 2026-09-29

The pay-calendar plan stood at 499 lines, one under the most its 520-line cap allows under the
gate's 20-line headroom arm, when the joint tick of `pay_calendar:C18-b` (`f23fef7b`) and
`pay_calendar:C21` (`f73246a3`) had to file `C21`'s shipped pointer and the specification of
`C22`, the step ruling `pay_calendar:R-PC113` sequenced after it. Under `docs/plans/conventions.md`
rule 5 the room comes from COMPLETED spans, never from trimming a live specification. `C18-b` is
`C18`'s last leaf, so the whole `C18` span -- the container, `C18-a` and `C18-b` -- completed with
this tick; `C5`'s span, already condensed once to buy `C4-b`'s room, is a completed span whose
three entries still ran to eight lines and carry no clause a later step must obey. Both are condensed in place to rule 5's form -- each step's id,
its commit and what it closed -- and their text is below, verbatim as it stood on the tick's
merged tree, carried WITHOUT re-verification. The `C18-b` entry below is the LIVE specification it
had before it shipped; what shipped is `f23fef7b` and its commit message, and every ruling these
steps took is a row of `docs/plans/rulings.md`. The live sentences that still name these steps --
among them `recurrence:R22`'s row deleting ruling `R-PC97`'s revert stopgap and
`balance:X-f3c-2b-2c`'s wait on `C18` -- state what they need themselves, so none depends on the
text below. A live sentence may
cite this record for how something came to be, never as a plan of record.

The `C18` span as it stood (section 4):

    - [ ] **C18 -- a payday may be recorded BEFORE the schedule's earliest, and a period below the books
          generates nothing** (ruling **R-PC62**): the DECOMPOSED parent of two leaves, cut 2026-09-22
          by the coordinator when **R-PC85**-**R-PC88** were ruled; it ticks with `C18-b`.
    - [x] **C18-a** `22f3d85b` -- the books bound: a recurring item's occurrences start above the books
          of every account it moves money in (**R-PC85**, **R-PC86**, **R-PC89**, **R-PC94**); the
          restatement, both template edit doors and the revert refuse to leave an unpaid recurring row
          inside the books it sits on, and the unarchive keeps one deleted (**R-PC88**,
          **R-PC90**-**R-PC93**, **R-PC95**-**R-PC102**). The revert refusal (**R-PC97**) is a stopgap
          `recurrence:R22` deletes. Closed **PC-500**.
    - [ ] **C18-b -- "Add earlier paychecks"** (ruling **R-PC87**; closes **PC-499**). An action beside
          Extend asks only how many paychecks to add before the first and records the paydays the
          EARLIEST era projects just below it: it states no date or rhythm, and deletes, moves and
          re-files nothing, so it reaches no paycheck regenerate's lock protects. The new periods are
          populated (**R-R38**); since `C18-a` a period before the books fills with nothing.
          `pay_period_batch.reject_backward_payday` stays for the forms that state a start, its
          docstring and refusal message corrected.

The `C5` span as it stood (section 4):

    - [x] **C5 -- the gap machinery goes, and a paycheck may owe one template twice.** `4e8b40b3`. The
          decomposed parent, ticked with `C5b`. This span is COMPLETE and condensed under rule 5, to buy
          the room `C4-b`'s decomposition needed; the commits are the record and `steps.md` carries each
          row's own sentence.
    - [x] **C5a -- delete what is now unconstructible.** `fe365de1`. Ticked at `C2-b2`; ticks
          `recurrence:R-F10`.
    - [x] **C5b -- a paycheck may owe one template more than once.** `4e8b40b3`. One commit under two
          arc names with `recurrence:R17`; closed **P16**.
