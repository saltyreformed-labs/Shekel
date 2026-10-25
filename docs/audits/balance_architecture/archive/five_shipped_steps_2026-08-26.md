> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Five shipped balance steps, archived out of the plan of record (2026-08-26)

**What this is.** The five `* [x]` entries that left `../README.md` section 5 on 2026-08-26 under
Section 9 rule 5, to buy the room `X-i3` and `X-i4` needed. The README stood at **980 of its
1,000-line cap with a 20-line headroom floor -- zero room** -- and rule 4 forbids raising a cap when
it binds. **The COMMIT is the record for every one of them**; read the code each shipped, not this
table.

**Why these five and no others.** Every one is SHIPPED. Two (`X-l`, `X-az`) are complete in
themselves; three (`X-au-a`, `X-au-b`, `X-au-j`) are shipped leaves of a container that is still
open, which is the same shape the 2026-08-16 archive already took -- `X-f3a-1` and `X-f3d` left
while `X-f3` remained open, and `X-f3c` is still #1 in the order today. A shipped leaf's record does
not become less complete because a sibling has not started.

## The obligations these entries carried, and where they now live

**Rule 5's second condition is that no live sentence may depend on an archived one.** Three of the
five carried a "**What a LATER leaf must obey**" clause, which is a live constraint rather than a
record of how something came to be -- so each was RESTATED in the live document beside the work it
binds, and is reproduced in full below. They are not summarised away:

| the obligation | it now sits |
|---|---|
| A derived row's answer must be INVARIANT under a change to its own amount column -- agreement alone cannot see the resolver (`X-au-b`) | on the `X-au` container, which owns the leaves that must obey it |
| The amount basis is a REQUIRED parameter on both `settle_amount` twins, and the BASELINE pin is stated once in `cash_ledger.baseline_amount_basis` (`X-au-j`) | on the `X-au` container, same reason |
| The settle-day pairing is a BICONDITIONAL rather than an implication, and a form-RESUBMITTED day is an ECHO that may not restate its basis (`X-az`) | beside the settle-day work in section 5 |

## The five entries, verbatim

* [x] **X-au-a** `81138fb8` a recurring definition's amount is an effective-dated series: the model,
  the ONE write door, the read-and-correct panel, and the backfill mining what the rows record.
  **44 templates, 47 versions, 625 minable rows reproduced with 0 mismatches** on a production
  clone. Opened **N-244**..**N-247**, and its two obligations on a later leaf are those rows.

* [x] **X-au-b** `81ad02d1` the TOTAL dispatch over the five amount rules, in
  `cash_ledger/_amount_source.py` -- a module INSIDE that package (developer, 2026-08-12).
  **997 of 997 rows on a production clone agree with what the app publishes, 0 refusals, `$0.00`
  drift.** **What a LATER leaf must obey**: a derived row's answer must be INVARIANT under a change
  to its own amount column -- agreement alone cannot see this resolver. Opened **N-252**, **N-253**.

* [x] **X-au-j** `cc7679a7` BOTH read passes hold ONE amount basis, closing **N-295**, **N-309**,
  **N-252** and **N-323**. `$0.00`; what goes is K profile lookups and K loan resolves per pass.
  **What a LATER leaf must obey**: the basis is a REQUIRED parameter on both `settle_amount` twins,
  because an optional one leaves the expensive shape as what a caller gets by saying nothing -- how
  this cost regrew after X-au-c2b closed it one tier down; and the BASELINE pin is stated once, in
  `cash_ledger.baseline_amount_basis`, so what-if scenarios move every surface in ONE edit.

* [x] **X-l -- the pay calendar answers any date.** `4f134bf4`. ONE step under THREE names: this
  row, `pay_calendar:C2` and `recurrence:R-F12`. The calendar is DERIVED from the owner's paydays
  and is total past the last stored one, so no consumer improvises; the specifications and the
  as-built are the pay-calendar document's section 4. Closed **N-128** (at `C2-c`). **It did NOT
  close N-82 or N-79's far half and this row claimed it would**, so both were re-pointed at the
  tick -- N-79 to **X-m**, N-82 to a stated developer decision.

* [x] **X-az** `488e8dd2` a settle day says HOW it is known, closing **N-332**, opening **N-334**.
  `settled_day_basis_id` on BOTH tables carrying `settled_on`, a `SettleDay(day, basis)` through
  every door, `ck_transactions_settle_day_needs_basis` renamed `..._needs_a_record` (it is the
  FIGURE's). `$0.00`. **A LATER leaf must obey**: the pairing is a BICONDITIONAL, not this
  section's implication -- a day and its basis share ONE lifetime; and a form-RESUBMITTED day is an
  ECHO that may not restate its basis, which cost `$4,173.07` over 59 of 66 rows before it did.

**`X-f1` was in the first cut of this archive and was PUT BACK.** Two live things name it -- the
`credit_card:CC3b` blocker cell that records why its dependency is already shipped, and a plan-gate
control that derives its annotated-blocker specimen from exactly that cell -- so archiving a
two-line entry cost more than it saved. That is the same lesson `C1` and `C2-f1` taught in the
pay-calendar archive on 2026-08-25.

## What still points here

`X-l`'s row stays in `docs/plans/steps.md` under all three of its names, because rule 11 makes an
identity class share ONE tick state and its two siblings live in other arcs. Its SPECIFICATION is
what left; the index entry is not this file's to remove.
