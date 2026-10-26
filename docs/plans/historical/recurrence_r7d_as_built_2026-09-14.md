> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# recurrence:R7d as built (2026-09-14)

**A loan payment's CLOSING bound stops being a stored column.** The `R7d` span of
`implementation_plan_recurrence_redesign.md` as it stood when `R7d-g-3` (`b5ac8710`) shipped the
`R7d-g` and `R7d` containers -- moved here under conventions.md rule 5 as a COMPLETED span, that
document standing at 864 of 900 lines. Every LEAF entry below was already ticked; the two container
entries (`R7d`, `R7d-g`) and `R7d-g-3`'s tick here, and `R7d-g-3`'s R-R83 paragraph is superseded by
**R-R88** (the resolver's loan-level extra is DELETED, not summed) -- its record is its commits
(`a66496ec`, `a1c5082f`, `b5ac8710`) and its PR; the earlier leaves' full records are their commit
messages and PRs.

## The span, verbatim

- [x] **R7d-a** `89cb0c1d` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [ ] **R7d -- a loan payment's CLOSING bound stops being a stored column.** DECOMPOSED 2026-08-25
      into seven leaves, one per surface that READS the column, after a census found this
      specification had enumerated only the ten WRITERS.

`loan_recurrence_sync` becomes a RESOLVER -- `loan_payment_window(template, ctx)` answering
`ClosesOn`, `Indefinite` or `EMPTY` -- and every reader asks it, instead of ten call sites writing
it into `budget.recurrence_rules` and hoping no reader gets there first.
**It takes the DEFINITION and not the loan** (ruling **R-R35**, which supersedes the
`(account, ctx)` this section specified). **R-R29 narrowed this to the CLOSING bound**; `starts_on`
stays stored. **Read TRAP 1: it cost this step a design.**

**The root cause is a CATEGORY ERROR in the table, and R7c-b is where it became visible.** Those two
columns hold two KINDS of fact: what a USER AUTHORS, where a stop before the start is a mistake to
report, and what the app DERIVES for a loan payment, where an EMPTY window is legitimate and
sometimes correct. `ck_recurrence_rules_valid_window` was drafted into R7c-b and HELD BACK on that
measurement (developer, 2026-08-15): originate 2026-08-01 with `payment_day` 1, then true the
balance to zero on 2026-08-15 -- the window is empty, forward generation emits nothing, and that is
right for a loan owing nothing, where a CHECK makes it an unhandled `CheckViolation` out of the
true-up. Both local repairs are worse: clamping to `max(as_of, starts_on)` admits ONE occurrence, so
a paid-off loan keeps a projected payment whose cash still debits while the fold books it to Refund;
and archiving the template inside a sync is destructive on a path that runs on every settle.

**The cached bound is STALE on live data, and it costs a budgeted payment.** Re-measured 2026-08-25
on a clone: rule 48 stores `end_date` `2029-01-22` where the Van's derived payoff is `2029-02-22`,
and extending the calendar by 26 periods generated rows only through `2029-01-22` -- the `$531.94`
installment due `2029-02-22` is never created.

**TRAP 1 -- DO NOT "fix" the ESTIMATED tier's future-only rule. It is finding B-9's FIX.**
`_estimated_from_contract` skips any contractual installment whose `payment_date < as_of`, so an
overdue slot with no record pays nothing. That reads like an oversight and is the opposite: the
retired forward walk amortized an installment per month whether or not one was recorded, and step
C6b deleted it for exactly that -- `-$15,755.38` per period. The LIVE statement is the comment on
the line itself. This step's first design read it as the root cause and proposed filling every
uncovered slot, which is B-9 re-introduced under a new name.

**TRAP 2 is CLOSED and ARCHIVED**, with the scope of R7d-a's invariance beside it, to
`historical/recurrence_r7d_trap2_as_built_2026-08-25.md`: a bound resolved before the rows exist is
NOT always later than the true one, measured twelve payments wrong through `regenerate_pay_periods`,
and `89cb0c1d` closed it. Row **N-352** carries what is left.

**R-R30 (2026-08-19) decides the FORM READ path and nothing else**: the create path already raises
and `period_population` already returns 0, so what an owner with no baseline changes is what a
locked "Ends" control renders. Its rule is in the Rulings index above; R7d-f applies it.

**The READER census forced the decomposition** (2026-08-25, by setting `end_date` NULL on both live
rules, then corrected by an adversarial review that found it wrong in BOTH directions). SEVEN
surfaces read a loan payment's closing bound where this specification named one, and each leaf below
names its own; the roll-call is archived to
`historical/recurrence_r7d_trap2_as_built_2026-08-25.md`. `_recurrence_preview` is NOT one -- it
composes from `request.args` and its control is `disabled` when locked, so it renders "never ends"
already. Ruling **R-R34**.

**What the resolver deletes.** Nine of the ten `sync_recurring_payment_bounds` call sites go
whole -- in `params.py`, `escrow_rates.py`, `payment_transfer.py` and `_loan_posting.py`,
regenerated by `grep -rn 'sync_recurring_payment_bounds(' app/`, whose eleventh hit is the
DEFINITION, because THREE of the nine line numbers this sentence carried had drifted by
2026-09-11 -- and with them the double sync at create, the "idempotent WITHIN a day" caveat, and the
stale bound D35 measures. **The census is of the CLOSING bound only**: all ten also call
`_sync_loan_cadence`, whose write repairs three shapes a `payment_day` edit misses --
`create_params` calls no sync, a PAY-SCHEDULE change moves a `PERIOD`-unit rule's resolved bound
(**D39**'s shape), and a cadence-unit edit moves `nominal_day`. After R7d the opening bound has
THREE writers: `params.py`'s, and `bind_rule_to_loan`'s two, found by
`grep -rn 'bind_rule_to_loan' app/`. Decide what repairs those three before deleting the path.

**`owns_validity_window` SPLITS; it is not deleted.** It is one predicate because
`_recurrence_form_refusals` states ONE writer owns both bounds -- a premise R-R29 makes false. It
drives the render lock and the refusal, so after R7d the "Ends" control either unlocks (a change to
a money-adjacent form) or stays locked for a value nothing stores. R7d-f decides which.

- [x] **R7d-b** `0462dc38` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R7d-c** `b8509c1e` -- the DECOMPOSED parent of "generation takes the resolver", split into
      TWO leaves 2026-08-27 (**R-R38**): the pass had to REACH generation first (R7d-c-1), and who
      opens it was a question about the three write doors, each of which did a write and then a
      read-dependent write in ONE call. Both leaves shipped; the container ships with the last.

- [x] **R7d-c-1** `61d81c7f` -- as built:
      `historical/thirteen_shipped_recurrence_steps_2026-09-02.md`.

- [x] **R7d-c-2** `b8509c1e` -- generation resolves a loan payment's stop through the composed door
      (`read_definition` over the pass the schedule carries), so a stale `end_date` binds nothing
      either way (**R-R56**) and R16-b-2's summed payoff reaches the ROWS. `$0.00` live on the
      clone; the planted `$50` definition 35/65 -> 32/32 rows at `2028-11-22` (measured 2026-09-11
      on R16-b-2's tree; not re-run on the era calendar). Held behind R16-b-2 (**R-R65**), re-cut at
      `a01839aa`, its first review found the id-keyed memo (**R-R73**). Closed **D46** via R16-b-2.

- [x] **R7d-h** `83dd4b8a` -- a loan gets ONE closing date, past AND future (`loan_closing_date`,
      **R-R51**: the later of two crossings); `recurrence_end_date` deleted. Its two obeys clauses
      were discharged at R7d-f and R7d-g-1. As built: the commit message and PR #214.

- [x] **R7d-d** `4a839587` -- the DISPLAY readers took the resolver through a COMPOSED DOOR
      (`recurring_definition`, one read pass); `4f40d6de` is **R-R56**, an arm R7d-g-1 deleted with
      the column. Its obeys clauses are discharged. As built: `f6ba59f8`..`713c4fce` (PR #240).

- [x] **R7d-e** `89302ba4` -- the monthly totals took the resolver: every `DerivedStop` answers
      `has_closed` under **R-R57**, `Closing.has_closed` ORs its two stops over one memoised
      reading, and the aggregator takes the read pass. Opened **N-513**, **N-514** (closed at
      R7d-f-2). As built: PR #253.

- [x] **R7d-f** `48e78700` -- the FORM's "Ends" control, its refusals and its preview: the
      DECOMPOSED parent, split into three leaves 2026-09-05 (**R-R61**), grown to five 2026-09-12.
      `R7d-f-1` measured its text inexact (`owns_validity_window` splits no SET; the preview never
      read the column, so **R-R34**'s sixth reader was inexact too);
      `update_recurrence_rule_from_form` READS THE BOUND AND WRITES IT BACK on every unrelated edit
      (`reauthor_rule`). Every leaf shipped; the container ships with the last.

- [x] **R7d-f-1** `6af50d53` -- the locked *Ends* row reads the RESOLVER and the locks read the
      pass, through one renamed identity and per-ROW lock flags. Closed **N-511**, opened
      **REC-515** (closed at R7d-f-2). **A LATER LEAF MUST OBEY**: the identity serves the form's
      locks and the door's **R-R56** arm ONLY, never the resolver (**R-R35** stands). Its browser
      pass RAN with R7d-f-3's (154 checks).

- [x] **R7d-f-2** `1d1f466f` -- the horizon rides ON `RuleReading` and `has_ended` takes no calendar
      (**N-514**); the occurrence WALK is the pass's memo keyed by the COMPOSED value
      (`placements_of`, R-R73's shape; a /savings render walks a goal transfer once, **N-513**); the
      preview resolves an `UnsavedDefinition` through `resolved_submission`, destination
      owner-checked (**REC-515**; R-R34's preview census now wholly corrected). Prerequisite:
      `_context.py` split (**R-R75**, closes balance:BAL-483). Browser pass RAN (158 checks).

- [x] **R7d-f-3** `e3661f6f` -- a stated stop is REFUSED at create where the destination loan holds
      no active payment (**R-R60**), "stated" meaning a real stop and never the key's presence
      (**R-R74**); the server emits which loans derive the stop and the script locks the "Ends" row
      as an affordance. Closed **N-512**; the update door's twin, **REC-521**, closed at R7d-f-4.
      **R7d-g MUST OBEY**: `recurring_definition` limit (1) names three producers of an owner's
      bound that outlive the two doors' refusals. Browser pass RAN (154 checks), R7d-f-1's too.

- [x] **R7d-f-4** `e0c67c0b` -- the UPDATE door settles the destination the edit LEAVES ahead of the
      recurrence step (`settle_destination_for_update`): the first occurrence derived, a real stop
      refused where the loan holds no active payment, "Never" or nothing writing the unbounded rule
      even over a stored stop (**R-R77**); a loan's payment (standing or settings-carrying, the
      twin's union) cannot change destination (**R-R76**). Closed **REC-521**; opened **REC-522**
      (R7d-g: an archived transfer edited and unarchived, a THIRD producer).

- [x] **R7d-f-5** `48e78700` -- the edit form emits `LoanDestinationLocks` computed for THIS edit
      (`_loan_destination.loan_destination_locks_for_edit`, the door's own order: pinned -> empty
      sets and the R-R76 sentence on a DISABLED destination control; repeats -> stored destination
      out; no rule -> every loan in; the payment-less subset for "Ends"; **R-R79**); the R-R76 union
      one predicate; the loan-destination half moved to `_loan_destination.py` first (**R-R78**,
      `cd22baa6`). Browser pass RAN (191 checks).

- [ ] **R7d-g -- the closing bound stops being WRITTEN.** The DECOMPOSED parent, split into THREE
      leaves 2026-09-13 once its four forks were ruled (**R-R80** D56's scope, **R-R81** D50's start
      by kind, **R-R82** the stop at a submission-less entry, **R-R83** D49); ships with its last.

- [x] **R7d-g-1** `a776b9df` -- nine of ten `sync_recurring_payment_bounds` sites went and the tenth
      became `sync_loan_payment_start` (opening-only; loan SETUP calls it too, the door where a
      transfer becomes the standing payment); the write door refuses an inverted STORED pair
      (`EmptyAuthoredWindowError`, the one comparison behind `ck_recurrence_rules_valid_window`);
      `authored_closing` went; migration `bf50951a3599` NULLed the **R-R80** set (2 rows on
      production, `$0.00`) and landed the CHECK. Closed **D56**; ten tests flipped by ruling.

- [x] **R7d-g-2** `547cc43d` -- the stored opening bound's maintenance contract is COMPLETE: every
      door where a definition becomes the standing payment (unarchive, archive, hard-delete, the
      update door, setup, params) calls ONE entry helper (**R-R85**); a SECOND transfer's start is
      its owner's, refused at or before origination (**R-R81**; the `$250.00` of past sweeps gone);
      the archived edit form locks as the standing payment's (**R-R86**); the lifecycle doors moved
      (**R-R84**). Closed **D35** (R-R29's residue is by design), **D50**, **REC-522**.

- [ ] **R7d-g-3 -- the loan dashboard's payment card is PER DEFINITION.**

**R-R83**: the card lists every recurring transfer into the loan with its own extra-principal and
track controls, so `update_payment_settings`, `track_payment` and the prefill stop taking
`.order_by(id).first()` -- the tie-break that hands the Track door a `$50` sweep created before the
payment (`$531.94 + $531.94` a month). It reaches the seam: `resolved_loan` prices the contract with
the STANDING payment's extra alone, so per-definition extras are summed there. Closes **D49**.

