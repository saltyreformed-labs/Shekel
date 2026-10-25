> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-bl-2a` as built: the payment feed has ONE date producer (archived 2026-09-11)

**Condensed out of the balance README under `docs/plans/conventions.md` rule 5 on 2026-09-11**
(developer's call under rule 4). Carried VERBATIM and WITHOUT re-verification; the obligation it
states on a LATER step stays in the README, on the live line that points here.

* [x] **X-bl-2a** `ee4fc2d7` -- the payment feed has ONE date producer and ONE
  settled-history derivation (**R-BAL7**), and the amount model's eager load is the CALLER's
  statement (**R-BAL8**); `loan_loaders` became a package at 1,054 lines. Byte-identical on both
  live loans and 4,000 collision trials; the DISPLAYED loan balance **25 statements -> 21**.
  **A LATER step must obey:** the load is stated by whoever traverses it, graded by a
  statement-COUNTING pair -- a presence check passed a mutant that deleted the options. Carries **N-432**.
