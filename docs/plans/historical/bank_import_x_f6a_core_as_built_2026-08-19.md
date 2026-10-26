> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The statement importer's core, as built: the X-f6a-1 - X-f6a-3b span (2026-08-19)

**Four shipped steps, condensed out of `docs/plans/steps.md` and
`implementation_plan_bank_import.md` under `conventions.md` rule 5** when
`steps.md` reached 244 lines against the 240 its cap leaves after headroom,
which is where two arcs landing in one day and a fifth bank_import leaf met.

Rule 5's three conditions hold. **Every finding these steps opened and did not
close is still live in `ledger.md`** -- N-302, N-303, N-306, N-313, N-317,
N-318, N-319, N-320, N-321 -- each naming a live owner there. **No live
sentence depends on a sentence here**: the rulings these steps established
(**R-FP**, **R-FS**, **R-FT**, **R-FU**, **R-FV**, **R-FW**, **R-FX**) stay in
the arc document's own rulings table, which is where a later step reads them.
**Nothing below was re-verified in the move** -- these are the entries as they
stood, and the commits they name are the record.

| step | commit | what it shipped, and what it closed |
|---|---|---|
| **X-f6a-1** | `40a490c3` | A statement is RECORDED: four tables, one normalized line shape behind a source adapter, the SECU CSV reader, and the import page. Balance-neutral on a production clone (9 accounts, 434 grid cells, 6,076 daily points byte-identical with 306 real lines). Opened **N-313**, **N-302**, **N-303**. What a later leaf must obey: identity is positional and its ordinal is UNMEASURED (R-FU's amendment), and the restatement guard compares only the description |
| **X-f6a-2** | `267cb75e` | A bank line IS these rows: `budget.statement_matches` and its members hold the correspondence over an exclusive arc, the proposer offers it, and an accepted match writes the bank's posted day onto every member. Closed **N-173**; opened **N-305**, **N-306**, **N-314**. What a later step must obey: no clearing link is written from a match (**R-FV**), an unbalanced group is refused rather than apportioned, and a group's day is its LATEST |
| **X-f6a-3a** | `140f1f24` + `feb2ea91` | The bank owns BOTH of a purchase's days (**R-FW**): `transaction_on` went NULLABLE, the adapter reads the swipe day SECU states in a card line's description, and a match corrects a purchase's `purchased_on` where the bank refutes it. Forced by X-f6a-3b's premise being measured FALSE. Opened **N-312**. What a later leaf must obey: a purchase day moves only where the bank REFUTES it, and only a line within `DAY_WINDOW` of the day it was MADE may refute it |
| **X-f6a-3b** | `1f214712` | A bank line BECOMES a purchase (**R-FS**, **R-FX**), against an envelope the owner picks or a NEW one it creates in the period the purchase was MADE in. It also REPAIRED X-f6a-3a, whose `_COST_BEARING_FIELDS` refused `purchased_on` and so killed 13 of the 15 purchase-date corrections the screen offered. Opened **N-315**-**N-321**; **N-306 split off**. What a later leaf must obey: R-FX's two conditions on adding to a settled row, and that a line becomes a PURCHASE, never a row |

## The X-f6a-3c - X-f6a-4 span, condensed 2026-08-22

**Five more shipped leaves, condensed out of `implementation_plan_bank_import.md`
under `conventions.md` rule 5** when that document reached 197 lines against the
180 its cap leaves after headroom, which is where ruling **R-GD** and the five
steps it opens met an entry list of eight shipped leaves.

Rule 5's three conditions hold, and the third is the one that took work.
**Every finding these steps opened and did not close is live in `ledger.md`** --
N-323, N-326, N-328, N-330, N-331 -- each naming a live owner there.
**No live sentence depends on one here**: the rulings are in the arc document's
own table (**R-FY**, **R-FZ**, **R-GA**, **R-GB**, **R-GC**), and the four
*"what a LATER leaf must obey"* obligations these entries carried were MOVED
onto the live steps that inherit them (`X-f6d-1`, `X-f6d-3`, `X-f6e`) in the
same commit, rather than being archived with the entries that stated them.
**Nothing below was re-verified in the move.**

    - [x] **X-f6a-4** `9439a5ad` an import can be UNDONE (**R-GB**), a same-day group reconciles as
          a SET so the ordinal stops deciding (**R-FU** amended), and one press mints one envelope
          per answer per pay period (**R-GC**). Three commits: `ba7f91d8`, `cc9e8f27`, `9439a5ad`.
          Closed **N-302**, **N-317**, **N-324**, **N-327**; opened **N-328**, **N-329**.
          **What a LATER leaf must obey**: a match may not lose its bank lines, the source-account
          pairing outlives no import that taught it, and a per-request registry is written AFTER.

    - [x] **X-f6a-3c** `46bec314` the DECOMPOSED parent of the review screen's THROUGHPUT, split in
          two 2026-08-19 (developer) and ticked with its last leaf. What the proposer may OFFER and
          how many acts one request performs are different subjects: the first is a matching rule
          over money already in the app, the second is a batch write door, and grading both in one
          diff is the review surface that let a defect ship at X-f6a-3a.

    - [x] **X-f6a-3c-1** `c25edd96` every candidate row carries the WINDOW the app believes its
          money moved in (**R-FY**) -- a settle day, a purchase day, or a bill's whole pay period --
          so no row the matcher OFFERS is unbounded and the global undated pool is DELETED rather
          than reported. Closed **N-312**, **N-315**, **N-316**; opened **N-322**; RESTORED
          **N-317**. **What a LATER leaf must obey**: a new candidate KIND owes a window, and the
          pair test and the group bucket apply the SAME slack or they disagree silently.

    - [x] **X-f6a-3c-2** `46bec314` a whole reviewed pass is ONE request against ONE derivation of
          the account (**R-FZ**): 215 acts that cost 13.2 minutes of round trips now take 5.80 s in
          the door and 13.37 s end to end, each item in its own SAVEPOINT so a refusal costs only
          itself, and the screen answers with its own receipt. Closed **N-306** and **N-322**;
          opened **N-323**. **What a LATER leaf must obey**: the scope holds what a pass cannot
          change, and what it DOES change -- the claims, and a row's PRICE -- is re-read per act.

    - [x] **X-f6a-3d** `50fed660` a destination stated ONCE PER MERCHANT and SUGGESTED thereafter
          (**R-GA**), which turns 91 questions into 21 on a statement whose leftover lines are 21
          merchants. Closed **N-325**; opened **N-326** and **N-327**.
          **What a LATER leaf must obey**: a policy is read to SUGGEST and the destination select is
          still the tick, so nothing here may select one -- and of the 48 lines six answers place,
          42 file into an envelope that has already CLOSED, which is why the sweep is per class.

### Their `steps.md` index rows, condensed out 2026-08-22

Moved under `conventions.md` rule 5 when `steps.md` reached 243 lines
against the 240 its cap leaves after headroom. The arc document's own
entries for these five were condensed in the same commit.

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| bank_import | X-f6a-4 | -- | An import can be UNDONE -- it releases the matches naming its lines, forgets the source-account pairing with the last import from that source, and the database refuses to orphan a match -- while a same-day group reconciles as a SET so the ordinal this app mints stops deciding whether a line is held. Closed **N-302**, **N-317**, **N-324**, **N-327**; opened **N-328**, **N-330**, `balance:N-331`. | SHIPPED | `9439a5ad` | -- |
| bank_import | X-f6a-3c-2 | -- | A whole reviewed pass is ONE request against ONE derivation of the account: the 215 acts the review screen offers cost 13.2 minutes of round trips and now take 13.37 s end to end, each item in its own SAVEPOINT so a refusal costs only itself, and the screen answers with its own receipt. | SHIPPED | `46bec314` | -- |
| bank_import | X-f6a-3c | -- | The DECOMPOSED parent of the review screen's THROUGHPUT, ticked with X-f6a-3c-2, its last leaf. | SHIPPED | `46bec314` | -- |
| bank_import | X-f6a-3d | -- | A destination is stated ONCE PER MERCHANT and SUGGESTED thereafter, never selected: the merchant becomes a column the adapter records, a policy names a template or a new envelope or *never a purchase*, and one sweep ticks what it places -- which turns 91 questions into 21 on a statement whose leftover lines are 21 merchants. Closed **N-325**; opened **N-326** and **N-327**. | SHIPPED | `50fed660` | -- |
| bank_import | X-f6a-3c-1 | -- | Every candidate row carries the WINDOW the app believes its money moved in -- a settle day, a purchase day, or a bill's whole pay period -- so no row the matcher OFFERS is unbounded, and the global undated pool that switched group matching off is deleted rather than reported. Closed **N-312**, **N-315**, **N-316**; opened **N-322** and restored **N-317** with its diagnosis corrected. | SHIPPED | `c25edd96` | -- |

## The X-f6d span, condensed 2026-08-23

**Four shipped leaves, condensed out of `implementation_plan_bank_import.md`
and `steps.md` under `conventions.md` rule 5** when decomposing `X-f6e` put
both documents under their headroom on the same commit.

Rule 5's three conditions hold. **Every finding these steps opened and did
not close is still live in `ledger.md`** -- N-337, N-338, N-340 and
`balance:N-339` -- each naming a live owner there. **No live sentence
depends on a sentence here**: the rulings these steps established and
amended (**R-GD**, **R-GE**) stay in the arc document's own rulings table.
**Nothing below was re-verified in the move.**

    - [x] **X-f6d-2** `674dcc94` a match UPDATES the row to what the bank took (**R-GD(a)**,
          **R-GE**), superseding **R-FV**'s refusal: where the bank's figure names ONE row it is
          written to that row through the correction seam, so a one-to-one match has no residual
          left to book. **What a LATER leaf must obey**: the not-its-own-figure census is TWO
          predicates (`settles_from_entries` AND `repays_card_spend`) because the transaction door's
          own backstop refuses only the first. Carries **N-335** with `X-f6d-1`.

    - [x] **X-f6d-4** `9ce01870` a group's difference is the MEMBER it was missing (**R-FN**), so
          `Sigma(lines) = Sigma(members)` holds by construction -- and the door re-derives its
          members AFTER its own writes, because settling a purchase moves a SIBLING payback.
          **N-239** passed: 7 deposits, `+$0.35`, the income statement NAMES the bucket.
          **Later leaves**: the total is SERVER-rendered because JS may not compute money, and
          `created_version_id` says what an act CREATED, so `X-f6f` widens that WRITER not this.

    - [x] **X-f6d-1** `dcdb86fe` a proposal is SCORED and a near miss is offered, the near tier
          running LAST over what the exact ones leave (**R-GD(b)**, twice amended by this step's own
          measurements). **What a LATER leaf must obey**: a candidate is 1:1 and
          merchant-corroborated and must be the ONLY one, so `X-f6d-3`'s warn cannot see what this
          tier withheld and owes that decision; and **N-336** opens with it, because this is the
          first proposal that STATES a correction and nothing reconciles it with the write.

    - [x] **X-f6d-3** `5aa295bd` a tick carries the FIGURE and the REVISION its row was reviewed
          against, and the door refuses one that MOVED since -- finding **N-336**, reproduced at
          `$321.71` under a `$0.03` caption. Neither coordinate sees the other's writers. The warn
          and the candidate list it was specified around were WITHDRAWN on measurement, and
          **R-GD(b)**'s third amendment is why. Closes **N-335**; opens **N-337**-**N-339**. Later
          leaves: `rows` is ONE collection, and the guard refuses ANY movement, a batch item's too.

### Their `steps.md` index rows, condensed out 2026-08-23

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| bank_import | X-f6d-4 | -- | Make a group's residual an ordinary uncategorized row the owner accepts, rather than a difference the door refuses outright or an invisible plug to anchor equity. Closes nothing and opens nothing: the residue it makes visible is **N-239**, whose cause is `balance:X-aw`'s. | SHIPPED | `9ce01870` | -- |
| bank_import | X-f6d-1 | -- | Make a proposal a SCORE rather than an exact-amount gate, so a near miss is offered with the variance it would write instead of being silently withheld -- the defect that hid a $178.29 Geico line from the $178.32 row it belonged to. | SHIPPED | `dcdb86fe` | -- |
| bank_import | X-f6d-2 | -- | Let the accept door RECORD an unequal match instead of refusing it, taking the bank's figure as the settled figure and storing the difference as a reported variance. | SHIPPED | `674dcc94` | -- |
| bank_import | X-f6d-3 | -- | Make a tick carry the FIGURE and the REVISION its row was reviewed against, so the door refuses an item whose row moved since the screen described it, rather than writing a correction nobody saw. Closes **N-336** and **N-335**; opens **N-337** and **N-338**. | SHIPPED | `5aa295bd` | -- |

## Condensed 2026-08-24 under conventions rule 5 (steps.md and the arc document needed headroom)

One line per step: its id, its commit and what it closed. Full records are above and in git.

- **X-f6a** `9439a5ad` -- the importer's CORE (four leaves, 2026-08-16 - 2026-08-20); carried **N-173**.
- **X-f6d** `9ce01870` -- the matcher's CORRECTION half (**R-GD**, four leaves): near misses offered, a match may disagree, a stale tick refused, a group's difference recorded; carried **N-335** until `X-f6d-3` closed it.
- **X-f6e** `d74001b3` -- the statement's own self-proof (**R-GF**), two leaves.
- **X-f6e-1** `d56fce48` -- a stated balance is an OBSERVATION with a solved effective day and a weakest-link EVIDENCE level; `opening_balance`/`closing_balance` dropped; `ref_cache` became a package; opened **N-341**, **N-342**. Obligation kept live in ruling **R-GF**: only the file's own CHAIN may convict it, and every candidate day is bounded by the header's.
- **X-f6e-2** `d74001b3` -- the bank's balance per day beside the app's, reported and never gated; closed **N-342**; re-pointed **N-337**; opened **N-343**.
- **X-f6f** `33a16524` -- the create-a-purchase arm's INVERSE (**R-GG**); closed **N-333**, **N-340**; the undo is PROSPECTIVE for the 165 acts recorded before it.
- **X-gb** `ec346c46` -- the delete door the route already served (**N-344**, both limbs), ruling **R-GM** (a row leaving the books withdraws the matches it was the last app row of), the delete SEQUENCE unified into one verb the route and the undo share, and the P-6 repair through that door: 14 rows and 11 matches gone, `$7,769.58` reversed, nine card lines PARKED and two Geico lines re-matched. Opened **N-348**.
- **X-ga** `73a36cf4` -- a card payment is never a purchase (**R-GJ**): *never a purchase* became a structural BAR rather than a caption, and a line a source files as paying an ACCOUNT the owner holds has no create arm that any answer opens. Closed nothing; **N-337** stays `CC3b`'s. Opened **N-347**.
