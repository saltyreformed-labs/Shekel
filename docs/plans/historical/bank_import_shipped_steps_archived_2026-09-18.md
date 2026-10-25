> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# 16 shipped bank_import steps archived under rule 5 on 2026-09-18

`implementation_plan_bank_import.md` stood at 260 of its 280 lines, exactly the gate's 20-line headroom
floor, with `X-f6b-1b`'s tick landing and `X-f6b-2`'s to come. These 16 SHIPPED steps -- the X-gb..X-gf-3b-2
span and X-gk -- are cited as a wait or an alias by NO other row of `docs/plans/steps.md` (measured on the
tick's tree with the gate's own `blocked_keys` / `alias_keys` plus a scan of `(shipped` notes) and own no
ledger row, so under `docs/plans/conventions.md` rule 5 their rows leave the index and their one-line entries
leave the document, one line per step here: its id, its commit and what it did, as its index row said. Their
as-built records were archived earlier to `bank_import_x_gb_x_gc_as_built_2026-08-25.md`,
`bank_import_x_gd_as_built_2026-08-26.md`, `bank_import_x_ge_as_built_2026-08-26.md` and
`bank_import_x_gf_as_built_2026-08-28.md` beside this file (X-gk has none; its row below is its record).
Carried WITHOUT re-verification. A live sentence may cite one of these ids for how something came to be,
never as a plan of record.

The one-line entries as the document carried them:

- [x] **X-gb** `ec346c46` -- the delete door (**R-GM**), P-6. Closed **N-344**; opened **N-348**.
- [x] **X-gc** `0452eef3` -- three surfaces stopped stating what is false (**R-GN**..**R-GP**).
- [x] **X-gd** `d1910c95` -- a merchant answer became a standing RULE: its identity and its store.
- [x] **X-gd-1** `395b14f7` -- a merchant is a ROW (**R-GR**).
- [x] **X-gd-2** `154cfcec` -- the rule STORE (**R-GS**, **R-GT**); **N-353** shut, **N-358** open.
- [x] **X-ge** `6d3e3ca1` -- the auto-apply door (**R-GH**, **R-GU**); MONEY, no press. **N-359**.
- [x] **X-ge-1** `6d3e3ca1` -- each tier publishes the refusals it used to swallow.
- [x] **X-gf** `ff744d79` -- the review is an exception queue; minted **X-gi**.
- [x] **X-gf-1** `a4db019f` -- an unmatched inflow becomes income (`bank_import:R-GW`).
- [x] **X-gf-2** `64cfca05` -- the register is not the queue (**R-GX**, **R-GY**). Shut **N-358**,
      **N-349**.
- [x] **X-gf-3** `ff744d79` -- decomposed parent of the queue proper; ticked with its two.
- [x] **X-gf-3a** `44f1cc7b` -- one rule VERDICT, one SENTENCE. Shut **N-359**, **N-371**.
- [x] **X-gf-3b** `ff744d79` -- decomposed parent of the queue's second leaf; ticked with two.
- [x] **X-gf-3b-1** `d2248fe6` -- the workbench is not the queue (**R-HC**). Closed **N-374**.
- [x] **X-gf-3b-2** `ff744d79` -- one list by the decision (**R-HB**, **R-HD**). **N-380** shut,
      **N-381** open.
- [x] **X-gk** `8569e5ec` -- the MERCHANTS surface (**R-IC**); opened **N-402** and **N-403**.

The index rows:

- **X-gk** `8569e5ec` -- Build the MERCHANTS surface the audit's fix column names: one row per merchant this account has seen, carrying its standing answer or *I have not said*, edited on click through the door the receipt's offer and the register already post to, so that the question three partial surfaces share today has one durable home.
- **X-gf** `ff744d79` -- The DECOMPOSED parent of the review-screen rebuild, split 2026-08-27 into the disposition hole its model left open, the queue-versus-register split and the exception queue proper, whose own third leaf split again. Ticked with the last of its seven leaves; minted **X-gi**.
- **X-gf-3b-2** `ff744d79` -- Rebuild what remains of the queue as ONE list of unexplained bank lines grouped by the DECISION each poses -- *is this money my books already hold, or is it new?* -- rather than the three cards that partition them by MECHANISM, which is the service's partition and stays load-bearing underneath the screen.
- **X-gf-1** `a4db019f` -- A bank line of money COMING IN that no app row explains became an uncategorized INCOME row (**bank_import:R-GW**), **MOVING MONEY**: two correct refusals had pointed at each other and left every unmatched deposit with no act at all -- 8 lines, `$58.87`, on the developer's own data.
- **X-gf-2** `64cfca05` -- THE REGISTER IS NOT THE QUEUE (**bank_import:R-GX**, **R-GY**): the 29 answered merchants and 221 accepted acts left the review screen for their own surface, taking the review body from 578,523 bytes to 149,103 and `review_set` from 146 SQL statements to 7. Closed **N-358** structurally and **N-349**; opened **bank_import:N-371**, **N-372**.
- **X-gf-3** `ff744d79` -- The DECOMPOSED parent of the exception queue proper, split 2026-08-27 on the developer's ruling into the rule verdict both the automatic door and the screen read, and the queue's own shape.
- **X-gd-2** `154cfcec` -- Ship the rule STORE (**R-GI**): a stated merchant answer gains a fourth answer, *ask me every time*, told apart from *never a purchase* by one boolean (**R-GS**); the withdrawal goes, so a rule is only ever restated; and a match act records whether a standing rule performed it (**R-GT**). Closed **N-353**; opened **N-358**.
- **X-gd-1** `395b14f7` -- Make a merchant a ROW rather than a string two tables each keep a 100-character copy of (**R-GR**), so the answer's key is a foreign key, the scope check becomes a sentence and *which merchants may be asked about* is one table read.
- **X-gc** `0452eef3` -- Three import surfaces stopped stating what is false: the corroboration advice no longer prescribes an export option SECU dropped, the never-showed panel stops claiming the bank failed to pay 18 CC Payback rows it only ever shows inside a card payment, and the destructive dialog binds in `<head>` so it cannot be raced. Closed **N-345**'s race half; re-pointed its other half at `operator`.
- **X-gb** `ec346c46` -- The transaction DELETE door got a surface and a disclosure, a `$0.00` row stopped rendering an unclickable chip, a row leaving the books withdraws the matches it was the last app row of (**R-GM**), and the dev books were repaired through those doors -- `$7,769.58` double-booked, 14 rows, 11 matches. Closed **N-344**; opened **N-348**.
- **X-gf-3b-1** `d2248fe6` -- THE WORKBENCH IS NOT THE QUEUE (**bank_import:R-HC**): the hand-build match form left the review screen for a surface of its own with its own write door, taking the review body from 150,853 bytes to 65,005 and its checkboxes from 112 to 17, and every exception now links to it with its own line already ticked. Closes **N-374**.
- **X-gf-3b** `ff744d79` -- The DECOMPOSED parent of the exception queue proper's own second leaf, split 2026-08-28 on measurement: the step's sentence carried two rulings, and **R-HC**'s half is 59% of the page while **R-HB**'s is a re-shaping of what is left. Merging the cards first would have rendered the same 27 lines twice on one page. It ticks with the last of its two leaves.
- **X-gf-3a** `44f1cc7b` -- Make what a standing rule comes to for a line ONE verdict the automatic door and the review screen both read, so a line a rule would have filed and the pass withheld says why on the screen rather than only in the import's transient flash, and a parked line names the register only where a different answer would open the create door. Closes **N-359**, **N-371**.
- **X-gd** `d1910c95` -- Promote a stated merchant answer from a suggestion to a standing RULE (**R-GI**), which the developer decomposed on 2026-08-25 into the merchant's own identity and the store that keys on it.
- **X-ge** `6d3e3ca1` -- Let a standing rule AUTO-APPLY at import for a NEW swipe line only (**R-GH**), **MOVES MONEY, OWN PR**: the line becomes a purchase in the rule's destination, dated by the bank and itemised on a receipt whose every item carries X-f6f's one-click undo, while every act that would modify a hand-made row keeps its tick.
- **X-ge-1** `6d3e3ca1` -- Make each matcher tier publish the refusals it used to swallow, so a pass reports three verdicts rather than two -- explained, unexplained, and DECLINED-with-a-reason -- and the auto-apply door withholds a line the search never positively finished looking at.
