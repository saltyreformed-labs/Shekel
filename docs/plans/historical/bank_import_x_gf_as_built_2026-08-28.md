> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The review is an exception queue, as built: X-gf and its seven leaves (2026-08-27 to 2026-08-28)

**Ticked at `ff744d79`** with the last of its leaves. Condensed out of
`implementation_plan_bank_import.md` under `conventions.md` rule 5 on 2026-08-29, when the
Reconcile rebuild (`X-gj`, rulings `bank_import:R-HP`..`R-HV`) needed the document's headroom.
The queue this span built is what `X-gj` REPLACES and `X-gi` deletes, so the constraints it left
a later step were moved onto those two entries and none is depended on from here.

Rule 5's three conditions hold: every finding it did not close is a live `ledger.md` row; no live
sentence depends on one here; and nothing below was re-verified for this archive -- it is the
live text of 2026-08-29 condensed.

## What shipped, leaf by leaf

- **X-gf** `ff744d79` -- the review is an exception queue, ticked with the last of its seven
  leaves. Measurement forced the split: 554,122 bytes rendered, 79% of it two registers of
  decisions already made, against 15,842 bytes of actual work. Closed nothing itself; minted
  `X-gi`.
- **X-gf-1** `a4db019f` -- an inflow the books hold no row for becomes an uncategorized INCOME row
  (`bank_import:R-GW`), MOVING MONEY: two correct refusals had pointed at each other and left every
  unmatched deposit with no act at all -- 8 lines, `$58.87`, on the developer's own data. The 9
  parked Capital One lines still had only a group match: `$7,412.94` of card payments against
  `$5,819.99` of unmatched `CC Payback` rows, which `credit_card:CC3b` dissolves (**N-337**).
- **X-gf-2** `64cfca05` -- THE REGISTER IS NOT THE QUEUE (`bank_import:R-GX`, `R-GY`): 29 answered
  merchants and 221 accepted acts left the review screen for their own surface, taking the review
  body from 578,523 bytes to 149,103 and `review_set` from 146 SQL statements to 7. Closed
  **N-358** (three by-id reads of rendered user data became composite-FK relationships) and
  **N-349**; opened **N-371**, **N-372**.
- **X-gf-3** `ff744d79` -- the decomposed parent of the exception queue proper, split 2026-08-27
  into the rule verdict both readers share and the queue's own shape.
- **X-gf-3a** `44f1cc7b` -- one rule VERDICT and one screen SENTENCE, derived where the decision is
  (`_verdict.ruled`) and read by ruling **R-GH**'s door and by the review screen. Closed **N-359**
  and **N-371**. The withholding sentence is composed in `_verdict.ruled` and printed unbranched; a
  parked line names the register only where a different answer would open the create door, which
  was 0 of 9 on the developer's own data.
- **X-gf-3b** `ff744d79` -- the decomposed parent of the queue's second leaf, split 2026-08-28 on
  measurement: **R-HC**'s half was 59% of the page and closed **N-374**; **R-HB**'s was a
  re-shaping of what was left. R-HC went first, because merging the three cards while the form was
  on the page would have rendered the same 27 lines twice.
- **X-gf-3b-1** `d2248fe6` -- THE WORKBENCH IS NOT THE QUEUE (`bank_import:R-HC`): the hand-build
  form, its live-totals endpoint and a write door of its own moved to
  `/accounts/<id>/statements/match`, every exception linking to it with its own line ticked and
  priced. The review body fell from 150,853 bytes to 65,005. Closed **N-374**. `apply=hand` was
  DELETED, not moved; five owner-visible sentences that named a POSITION on the page, two of them
  in the service, went false in this one commit -- a service sentence may state an act, never where
  something sits.
- **X-gf-3b-2** `ff744d79` -- the queue is ONE list grouped by the DECISION (`bank_import:R-HB`),
  the mechanism partition load-bearing underneath: 17 / 10 / 0 by evidence on the developer's own
  data, conserving 27 against `unmatched`. Closed **N-380**; opened **N-381**; ruled **R-HD** (a
  sweep reaches only the group that offers it). The grouping reads BOTH of `_verdict.ruled`'s
  withholding arms, and a third arm must teach `_positive_for` or `TestNoSweptRowCarriesASentence`
  fails.

## What the next day measured

On 2026-08-29 the developer said he would not use the screens this span built: the queue held 27
lines of which 16 had no correct act anywhere in the app (nine card payments awaiting the card
arc, seven payroll deposits 4-6 cents short under **N-239**), and the every-bound-beside-its-line
rendering had put two sentences on the page sixteen times. The record of that assessment and the
locked replacement is `docs/design/bank_import_audit.md`; the rulings are
`bank_import:R-HP`..`R-HV`.
