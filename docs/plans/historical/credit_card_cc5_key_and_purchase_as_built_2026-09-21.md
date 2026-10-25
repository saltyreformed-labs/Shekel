> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# CC-5-1 and CC-5-2 as built: a movement's account is its own, and the first door that writes one elsewhere (2026-09-21)

Archived at the tick of the two leaves (5-1 `4045a9b1` the key; 5-2 `3ca97070` + `af3b9f5b` the purchase door,
its readers and the grid half) that opened the CC-5 family, shipped in ONE pull request under **R-CC33**. The
commits are the record; the build logs, the mutation harnesses and the adversarial reviews are
`~/projects/shekel-handoffs/HANDOFF-credit-card-CC-5.md` and `credit_card-2026-09-21/` ((1/2)'s nine
mutations are listed in `3ca97070`'s body). 5-1 carries
migration `9900b309f0b0` (on `45f10b870c8b`); 5-2 carries none; neither moves money. Below: the CC-5 entry as it
stood in `implementation_plan_credit_card.md` before the split (verbatim), the ledger row 5-1 closed, and the
developer's picked option texts for the seven rulings the leaves built to, verbatim -- the `rulings.md` rows
quote them in full; this record holds the old entries beside them.

## The CC-5 entry as it stood before the split, verbatim

- [ ] **CC-5** `feat(cards): a purchase is a movement on the card` -- design 3.2 (`R-CC15`): the
      settle-with-tender door (the movement's day, default the act's, basis `entered`, refused on or
      before the card's opening) and the card purchase entry; the parent-account key re-cut to a
      plain `transaction_id` FK (**CC-353**, renamed from BAL-506, closes with it); every reader in
      3.2's census re-pointed onto the movement's account, the entry reduction gaining its
      account-keyed arm BESIDE the flag's (`_amounts.py`, `_cash_leg.py`: the balance and
      bank-import lanes' files, coordinated at build); the ownership gate against the ROW's owner;
      the movement chip. The flag survives to CC-7.

## The `steps.md` row as it stood, verbatim

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| credit_card | CC-5 | -- | Add the movement-on-card doors (settle-with-tender taking the movement's day, basis `entered`, refused on or before the card's opening; the card purchase entry), re-cut the parent-account key to a plain FK, re-point every reader in design 3.2's census onto the movement's account, and gate ownership on the ROW's owner (**R-CC15**). Closes **CC-353**. | #1 | -- | NOW / balance:X-bi-4 (shipped; both halves of the fork ruled on its entry: the key relaxed AND the fold's movement predicate) |

## Ledger row CLOSED at this tick (moved here under the same id)

**CC-353** CLOSED at `credit_card:CC-5-1` `4045a9b1`: `fk_transaction_entries_parent_account` and its
`ON UPDATE CASCADE` are dropped; a movement is held to its own account by `fk_transaction_entries_account_id`
and to its row's owner by `fk_transaction_entries_owner_transaction` / `fk_transaction_entries_owner_account`
(**R-BAL76**, **R-CC32**). The row as it stood:

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| credit_card | CC-353 (`balance:X-bi-3c`'s adversarial review 2026-09-16) | renamed from BAL-506 2026-09-18: filed under balance while `X-bi-6` owned it, re-homed with its owner `CC-5` (conventions.md rule 10) | **THE `ON UPDATE CASCADE` ON `fk_transaction_entries_parent_account` HAS ONE BENEFICIARY, THE TRANSFER SHADOW** (migration `c4e8a2d7f1b3`, **R-BAL46**): it admits a parent-account move carrying ordinary purchases too, which the `NO ACTION` key refused and no writer performs (the maintain pass retains such a row, N-292); once `X-bi-6` deletes the shadow rows no parent's account can move and the cascade fences nothing | `$0.00`; a key stating a rule about an act the schema can no longer express | **OPEN, born with an owner.** Was `X-bi-6` restoring `NO ACTION`; since the 2026-09-18 re-mint `CC-5` re-cuts the key to a plain `transaction_id` FK (design 3.2), so the update rule goes with the composite key and the restore has no object | CC-5 |

## The rulings' picked texts, verbatim (the developer's words; `rulings.md` holds the condensed rows)

R-CC32 and R-CC33 were asked at CC-5-1 (2026-09-20), R-CC34 to R-CC37 at CC-5-2 (1/2) (2026-09-20), R-CC39 at
CC-5-2 (2/2) (2026-09-21). The rule-5 confirmation at 5-1 (*"Yes, re-express to R-BAL76"*: two cases of
`test_clearing_link_schema.py::TestAPurchasesAccountIsItsParents` re-expressed) took no id.

**R-CC32** -- picked *"Follow the row's own shape (Recommended)"*: "The movement-to-row owner key CASCADEs exactly like the existing `transaction_id` key beside it (the model's stated principle: two keys over one column never delete differently, or a delete's outcome depends on which PostgreSQL evaluates). Both account keys RESTRICT like `fk_transactions_owner_account`: an account holding any movement -- a card with purchases from checking envelopes -- cannot be hard-deleted, and the account delete door archives it instead (leaf 5-2 teaches that door to count movements on the account, not only rows). ON UPDATE stays plain NO ACTION on all three; the old cascade's one beneficiary, the transfer shadow, is already moved by hand (ledger row CC-353)." Rejected: the account keys CASCADE too (deleting a card would silently destroy checking envelopes' swipes).

**R-CC33** -- picked *"Stack 5-1 and 5-2, one PR (Recommended)"*: "5-1 is committed and pushed now as the leaf boundary (a fresh session builds 5-2 on the same branch), and the branch opens ONE pull request only when 5-2 is on it -- so the key never reaches dev ahead of the readers and the writer, which is R-BAL76's letter. Cost: 5-1's migration sits unmerged until 5-2 lands, so if another lane merges a migration first, this one is re-parented (the d4a92f6b13c8 precedent); the coordinator holds that." Rejected: merge 5-1 alone with a window-accepted ruling; fold 5-1 into 5-2.

**R-CC34** -- picked *"Hidden when one choice"*: "the add-purchase Account dropdown renders only when the owner's set (the row's account plus their active cards, CC-4-2's set) holds more than one account; with one, the form carries no account control and the door defaults to the row's account -- CC-4-2's `accounts|length <= 1` rule applied to the purchase picker, so every production form (0 cards) is byte-identical." Rejected: always show; show for any other cash account.

**R-CC35** -- picked *"Keep the checkbox as is"*: "the CC checkbox stays beside the dropdown until CC-7 deletes it; ticking CC and picking the card is refused by the service with a banner naming which to use; no interim show/hide logic." Rejected: hide on the add form when a card exists; hide on both forms.

**R-CC36** -- picked *"Retire it and re-point on re-settle"*: "for transaction definitions the account-move refusal goes -- a row holding records moves with its definition and its movements stay on their own accounts; the three tests re-expressed; the seam's re-settle branch sets a kept payment record's account to the row's (the line CC-5-3 writes for "no tender named")." Rejected: retire except for a kept record; keep until CC-5-3.

**R-CC37** -- picked *"The picker's set, plus the row's own"*: "a purchase may name its row's own account, or any member of the owner's cash-flow set (the primary grid account plus active cards) -- the SAME predicate the picker renders from, so the door and the form agree by one rule and a swipe on a 401(k), IRA, house or savings account is unwritable, not merely unoffered; a row that LIVES on such an account keeps its purchases there." Rejected: any account whose balance folds movements (PLAIN/INTEREST); keep as built and fix the sentence.

**R-CC39** -- picked *"Union (Recommended)"*: "A purchase may name its row's own account (preselected) or any member of the owner's cash-flow set (primary checking plus active cards), in that order. One rule, no branch on where the row lives. The dropdown appears only when there is more than one option. This is R-CC37 as written and the door as built at (1/2)." Rejected: "Own + cards" (own account or an active card, never primary checking unless it is the row's own; a Savings envelope with no card shows no dropdown; the door narrows); "Own only outside the set" (a row on checking or a card offers the set; a row on any other account offers only its own and shows no dropdown; the door narrows).

## The two rulings asked at CC-5-3's entry (2026-09-21 08:2x, the card lane), verbatim

**R-CC40** -- picked *"Card's screen only, two halves (Recommended)"*: "The most robust and future-proof: ONE rule, money is read where it moved, for the fold, the ledger, the panel and the matcher alike; no cross-account link, no allocation table, one bank line to one movement. HALF 1 lands in 5-3 (~10 lines: the price producer in status_seam and the matcher's three readers): on an account's screen a settled row is worth what its payment moved ON THAT ACCOUNT, zero when it moved elsewhere, so the grocery bill leaves checking's screen and a stray $120 there stays unexplained until you record it. HALF 2 is its own leaf right after 5-3 (bank-import's files: candidates, moving, accept, the accepted register, undo; the card's reconcile panel gets the same admission): the payment movement is the CARD screen's candidate for GROCERIES -$120, matched as a movement member, dated through the bill's own door because the movement follows the row. Worked: accept dates the bill by the card's line; the card's actual equals the bank; nothing counted twice. No card import exists yet, so nothing is lost provided half 2 lands before the card import does." Refused: *"Half 1 now; half 2 rides the card import"* (half 2 stays with the design's card-on-the-feed leaf, minted when the card import is built); *"Nothing in 5-3; both with the card import"* (the matcher untouched: the bill offered on checking's screen at -$120, a stray-$120 pairing hides a real outflow, the card's line has no candidate; CC-5's steps.md sentence false at the tick); *"Both screens"* (matching on checking books money against a statement that never showed it, and the card-side row member is unwritable under `fk_statement_match_members_transaction_account`).

**R-CC41** -- picked *"Its own leaf under CC-5 (Recommended)"*: "A sign fix across five sites with a stated invariant between them (the trend's index 0 reconciles to the hero 'by construction', so one site alone breaks it): one subject, one review, $0.00 on production. Minted after 5-3 (and after the matcher's half 2 if that is minted too); the coordinator ranks it and mints its id." Refused: *"CC-5-3 takes it"*; *"CC-11, the card cockpit"*; *"Another step"*.

The developer's context, in his words at the restatement he asked for: "All the charges to my checking account SECU to my credit card Capital One are held until the credit card account is built and ready." "My expectation is that the app could track payments from checking to the credit card as a transfer to pay off the credit card. I would hope that the app could handle applying payments to the oldest purchases first which I believe is how my real credit card works. The wrinkle in this is I don't always pay off the card in amounts that exactly match the purchases. For example, I make $120 and $450 purchases with the credit card, then I pay $300 towards the credit card in one paycheck and the remaining $270 out of the next paycheck. I have only built an import for checking and don't have one for the credit card yet but I plan to do so." The lane answered from the ruled design (no per-purchase allocation, R-CC18 / R-CC22) and he picked the recommended option with that stated.

## CC-5-3 `9a74658f`, ticked 2026-09-21 (its specification as it stood, then R-CC42 verbatim)

- [ ] **CC-5-3** `feat(cards): the settle-with-tender door` -- design 3.2: `Settlement` gains the
      tender account (default the row's expectation, else its own; a statement-driven settle forces
      the statement's account); `_cover` / `_record_onto` write the covering movement onto the
      tender on the movement's day, basis `entered`, refused on or before the card's opening;
      `_mirror_assertion` and `record_clearing` stop copying the row's clearing link onto a movement
      on another account; the full-edit popover's settle section and `mark_done`'s form gain the
      "Paid from" picker, both reading `purchase_accounts` (`R-CC39`: one function, three doors);
      undo is the ordinary revert. HALF 1 of `R-CC40` (ruled at this leaf's entry):
      `status_seam.covered_cash_leg` takes the account and the matcher's three readers
      (`_candidates`, `_accepted_view`, `_release`) pass the statement's, so on an account's screen
      a settled row is worth what its payment moved ON THAT ACCOUNT, zero when it moved elsewhere.

| credit_card | CC-5-3 | -- | Add the settle-with-tender door: `Settlement` gains the tender account, the seam writes the covering movement onto it on the movement's day, basis `entered`, refused on or before the card's opening; the popover's settle section and `mark_done` gain a "Paid from" picker reading `purchase_accounts` (**R-CC39**); on a screen a settled row is worth what its payment moved there (**R-CC40**, half 1). | #3 | -- | NOW / credit_card:CC-5-2 (shipped; the door and the picker it reuses) |

**R-CC42** -- picked *"Retained across a revert (Recommended)"*: "From scratch: where the money moved is part of the record and survives a revert exactly as a stated figure does. A re-settle that names no account keeps the kept payment's account; naming one re-points it; every door reads the record: the popover's 'Paid from' shows the kept account selected, and the one-click checkmark and mobile Mark Paid pass nothing, so the seam keeps it. One default, spelled once in the seam. AMENDS R-CC36's re-point clause: a Projected row whose definition moved keeps its kept record where the money moved, and only a named tender moves it (that test is re-expressed to say so). Worked: the hotel's $120 stays on the card after revert, move and checkmark." Refused: *"R-CC36's letter: none named = the row's"* (the seam re-points a kept record onto the row's account whenever no tender is named -- the line 5-2 wrote; the one-click re-settle of the reverted hotel bill books $120 on Checking: card $120 low, Checking $120 high, the card's line unmatched -- unless the popover is opened and the card re-picked; the definition-move case keeps booking on the new plan account); *"Hybrid: seam per R-CC36, doors name the kept account"* (every door posts the kept record's account; the default spelled at the doors AND the seam, rule 14's two homes); *"Store whether the tender was picked"* (a column beside the movement's figure source; a migration and a second echo rule at the door). Worked example given: a $120 hotel bill on Checking, marked Paid from the card on 9/22 (the $120 movement on the card); revert to move it to next paycheck, move it, click the cell's checkmark (one click, no picker). Retained: still on the card. R-CC36's letter: silently onto Checking. The wrinkle stated: R-CC36 was ruled for a definition move (Checking -> Second Checking) over a reverted row holding a kept record; nothing stored says whether an account was PICKED or merely defaulted, so retention flips that case. CONSEQUENCE (rule 5, confirmed by his option text): `tests/test_services/test_covering_movement.py::test_a_re_settle_re_points_the_kept_movement_onto_the_rows_account` (R-CC36's control) is RE-EXPRESSED as `test_a_re_settle_keeps_the_kept_movement_where_the_money_moved`, his text quoted in its docstring: after the definition move the re-settle keeps the movement on Checking; a re-settle NAMING Second Checking moves it. R-CC36's row takes an "AMENDED 2026-09-21 BY R-CC42" head on its re-point clause; its retire-the-retention clause stands.

## CC-5-4a-1 `079524b0` (+ `e8ba69d6`, the rule-5 docstrings), ticked 2026-09-21: CC-5-4's specification as it stood, then the six rulings and the confirmation VERBATIM (the card lane's record, `rulings_cc5_4.md`)

- [ ] **CC-5-4** `feat(cards): the card's line meets the bill it paid` -- design 3.2 and `R-CC40`'s
      HALF 2: the payment movement is the CARD screen's candidate for the bill (`statement_match`
      candidates, moving, accept, the accepted register, undo -- bank-import's files,
      announce-first; the card's reconcile panel's purchases arm gets the same admission), matched
      as a MOVEMENT member and dated through the bill's own door because the movement follows the
      row; accept dates the bill by the card's line, the card's actual equals the bank, nothing is
      counted twice. Must land before any card import exists.

| credit_card | CC-5-4 | -- | Offer the payment MOVEMENT on the CARD's screen as the candidate for the bill it settled, matched as a movement member and dated through the bill's own door (`statement_match` candidates, moving, accept, the accepted register, undo; the card's reconcile panel's purchases arm), so a card-settled bill reconciles on the card's statement, never checking's (**R-CC40**, half 2); before any card import. | #3 | -- | NOW / credit_card:CC-5-3 (shipped; the movement it offers and the readers it re-keys) |

## Ruling 1 (Q1, "Subject shape") -- what the matcher's subject IS

Picked: **"B now, in this leaf"**

> The universal subject in CC-5-4: the kind, the migration re-keying every row member onto its payment,
> the census, the register/undo/claims/proposer rewrite. Its own PR and release (it rewrites accepted
> acts). Collides with bank-import's held branch across 18 files.

The option B it names, as stated in the question: "the MOVEMENT is the subject of EVERY settled match on
every screen; a row is a candidate only while Projected. It deletes A's fence, the settled arm of the
row-pricer and three readers of covered_cash_leg. It needs a migration re-keying every accepted match's
row members onto their payments (221 acts on production at the 08-27 count) plus a census for rows
holding none, and it rewrites the register, the undo, the claims and the proposer's whole-row set:
bank-import's files, 18 of which its held branch edits; it also survives X-bi-6-4's re-parenting (a
payment member outlives a shadow-row member)."

Refused: *"A now, B minted later (Recommended)"* (the SETTLEMENT kind admitted only where the movement is
and the row is NOT; B minted as a bank-import follow-up that deletes that fence); *"A only; B is not the
destination"* (the two-armed rule -- row on its own screen, payment elsewhere -- as the design);
*"Refuse: re-open the design"*. Rejected in the question's text: reuse PURCHASE with a covers_settlement
flag (a proxy beside a kind); a TRANSACTION member on the card (R-CC40 refused "Both screens"; the key
refuses it).

Facts stated in the question, verified at 739288bb: the matcher's two kinds conflate two facts (which
table the member names; whose record the figure / clock / door are); a covering movement has the ENTRY's
identity and the ROW's record; fifteen kind branches (`_offers`, `_landing`, `_dating`, `_pairing`,
`_propose`, `_verdict`, `_variance`, `_moving`, `_accept`, `_resolve`, the review template's token); a
third kind dropped in blind makes `_moving._apply_day`'s else-arm `db.session.get(Transaction, <entry
id>)`.

Worked (given for all options): GROCERIES $120 on Checking, paycheck 9/12-9/25, marked Paid from the
card on 9/22; the card's feed posts GROCERIES -$120 on 9/24. On the card's screen the payment is offered
at -$120 with window 9/12-9/25, so 9/24 pairs; accept moves the bill's day to 9/24 (observed) and the
payment follows; the card's fold reads -$120 on 9/24, Checking reads $0, nothing twice. REVERTED CASE,
also admitted: revert the bill to move it to the 9/26 paycheck; its $120 payment is KEPT on the card,
un-dated (R-CC42). The card's screen offers that kept payment at what re-settling would book ($120),
window = the new paycheck; accept re-settles the bill on the card at 9/24. Checking's screen ALSO offers
the reverted bill (Projected, on Checking); whichever is accepted first wins, the other re-prices to $0
and is refused.

### Ruling 2 (Q2, "Panel") -- the card's reconcile panel

Picked: **"P1: a fourth panel arm, own leaf (Recommended)"**

> CC-5-4b: settlements arm in reconcile_service, ticked through the bill's door on the asserted day with
> this account as tender; the payment takes the statement's link; record_clearing takes the statement's
> account. Built after 5-4a, a separate session.

The P1 it names, as stated: "a FOURTH arm of the panel, settlements: un-dated payments on this account
whose bill is on another; listed under the bill's name in its paycheck block; ticked through the bill's
door (settle on the asserted day, tender = this account); the PAYMENT takes the statement's link and the
bill does not (a bill's link is held to the bill's own account), which means the seam's clearing writer
learns which account's statement it is recording. Its own leaf (CC-5-4b): the panel is
balance/bank-import's package and the template and POST gain a field."

Refused: *"P1, but inside CC-5-4 as one leaf"*; *"P3: no panel admission; amend R-CC40"* (the card's
panel never lists a payment; Checking's panel keeps offering the reverted bill; R-CC40's "same admission"
clause struck); *"Refuse: re-open the design"*. Rejected in the text: P2, widen the purchases arm and
split its writer (two writers inside the arm whose whole character is one bulk UPDATE).

Facts stated: the panel lists only UN-DATED things (purchases `settled_on IS NULL`; rows Projected) on
THIS account; a card-paid bill is dated at the settle, so the ONE state the card's panel can meet is the
REVERTED card bill (kept, un-dated payment on the card); the purchases arm's writer is a bulk UPDATE of
the day pair + link (`_purchases.record_settled_days`), which on a payment would date the seam's mirror
while its bill stays Projected; `fk_transactions_reconciled_by` is composite over the row's account.

Worked: the reverted GROCERIES bill, $120 kept on the card; the card's balance asserted for 9/30. P1: the
panel lists "Groceries (Checking's plan, paid from this card) $120"; tick it and the bill settles on the
card by 9/30 (a bound), the payment carries the 9/30 statement's link, and the card's books-vs-bank
difference closes by $120. P3: the panel lists nothing; the card's difference stays $120 off until the
bill is marked Paid on the grid, and then the panel still never asks about it.

### Ruling 3 (Q3, "Leaf split") -- B's two halves, and the member table's bill column

Picked: **"Two leaves, two PRs (Recommended)"**

> CC-5-4a-1 = the writer, this session, no migration, rides the next dev-to-main. CC-5-4a-2 = the re-key
> migration + the bill column dropped, next session, its own PR and release, graded on production's
> shape first. CC-5-4b (the panel arm) after both.

The two halves as stated: HALF 1, the WRITER: a settled bill is offered as its payment on whichever
account the payment is on; a Projected bill is offered as itself; when a match settles a Projected bill,
the member recorded names the payment the settle just wrote, never the bill; every new match names
movements only; the register, the undo, the claims and the proposer read BOTH member shapes until half 2;
no migration. HALF 2, the STORED PAST: a migration re-keys every existing bill member (221 accepted
matches on production at the 08-27 count) onto that bill's payment, after a census on the newest
production dump; a member with no payment to re-key onto (a bill closed from its purchases, a $0.00
close, a Credit or Cancelled bill matched before today's refusals existed) REFUSES the migration rather
than being guessed at, and the developer rules what happens to it if the census finds one; then the bill
column on the member table goes, with its key and its unique index (the member is a bank line or a
movement, nothing else) and the six readers of a bill member (the register, the undo, the claims, the
withdrawal on delete, the bank agreement) lose that arm; its own PR and release.

Refused: *"One leaf, one PR"*; *"Two leaves, but keep the bill column"* (half 2 leaves
`statement_match_members.transaction_id` in place, nullable and unwritten -- a stored home with no
writer); *"Refuse: re-open the design"*.

Stated for the record (the coordinator's ask): a 4a-only release, before 4b: the card's statement screen
offers the $120 payment and matches it; the card's reconcile panel does not yet list the ONE state 4b
covers (a reverted card bill's kept payment); Checking's panel keeps listing that reverted bill as a
Projected row. Coherent, with a stated gap.

### Ruling 4 (Q4, "Finding") -- the card screen's create arm

Picked: **"File it, owner = the card import leaf"**

> A ledger row owned by the step that builds the card import (the bank-side leaf design 3.2 names);
> nothing built now.

The finding as stated: on the CARD's statement screen today, a card line cannot be recorded into a
Checking envelope: the destinations offered are only envelopes ON the card (`_destinations.py:182`
scopes by `Transaction.account_id`; none exist), and the door that records a purchase from a line names
no account (`_create._born_purchase` -> `entry_service.create_entry` with no `account_id`), so the
purchase would take the envelope's account (Checking) while the member key
(`fk_statement_match_members_entry_account`) holds it to the card, a refusal. A NEW envelope minted from
the card's screen is placed ON THE CARD (`_container.py:416`, `account_id=scope.account_id`), so its
budget would sit on the card rather than on Checking's paycheck grid. Design 3.2's bank-side row names
this; the card import (not yet built) is what would hit it.

Refused: *"File it, owner = CC-5-4b"*; *"File it, owner = a new CC-5 leaf"*; *"Decline: not a finding"*.

### Ruling 5 (asked during the build after 12:49 EDT, recorded 13:42 EDT, "Re-point vs match") -- a matched payment's account change

Picked: **"W: withdraw and disclose (Recommended)"**

> A payment's account change withdraws any match naming it, through match_withdrawal, disclosed at the
> door as a delete is. Applied to the tender re-point and the transfer endpoint move in this leaf; the
> row/definition doors reported, not fixed.

Refused: *"R: refuse while matched"* (the seam refuses a re-point of a matched payment with a sentence
naming the match and its Undo; the endpoint move refuses the same way; nothing withdrawn without his
hand); *"W here; the other doors too"* (as W, and the leaf also wires the withdrawal into the
definition-move and any other door that changes a matched subject's account, after a census);
*"Refuse: re-open the design"*. Listed as impossible in the text: *"K: keep half 1's register reading"*
(a payment member cannot survive the account change; the member key holds it, NO ACTION on update).

Facts stated in the question: under R-CC43 every accepted match names the PAYMENT; the member table
holds a payment member to its account by `fk_statement_match_members_entry_account` and the database
refuses an UPDATE of the payment's account while the member exists; under half 1 (CC-5-3) the member
named the BILL, which stayed on Checking, so the re-point went through and the register showed the match
"no longer holding" at $0.00 with its Undo -- structurally unreachable for a payment member. The same
key shape holds a bill member to the bill's account and a purchase member to its account, so a matched
Projected bill whose definition moves accounts (the R-CC36 case) and a matched transfer leg whose
endpoint moves already hit the database the same way before this leaf (the endpoint move's own docstring
names `fk_transaction_entries_reconciled_by` refusing a cleared leg, ledger row BAL-503).

Worked: GROCERIES $120 on Checking, marked Paid from Checking on 9/22; Checking's feed shows GROCERIES
-$120 on 9/24; matched (the match names the $120 payment on Checking, dated 9/24); later "Paid from: the
card" is picked on the popover. W: the correction goes through; the match is WITHDRAWN and disclosed
("this frees 1 bank line: 9/24 GROCERIES -$120"); Checking's 9/24 line is unexplained again and the
payment is on the card, offerable on the card's screen. R: refused with a sentence naming the match and
its Undo.

### Ruling 6 (asked and recorded 13:42-13:44 EDT, "Endpoint move") -- R-CC46's second door

Picked: **"BAL-503 clause; X-bi-6-4 owes it (Recommended)"**

> R-CC46's endpoint-move half is recorded as a clause on ledger row BAL-503 (the member key beside the
> link), owned by X-bi-6-4; this leaf wires the tender re-point only and reports the endpoint move.

Refused: *"This leaf edits transfer_service now"* (CC-5-4a-1 wires the withdrawal into
`_apply_endpoint_move` today, announced to balance first; a merge conflict with X-bi-6-3 likely);
*"Refuse: re-open the design"*.

Facts stated: `transfer_service._endpoints._apply_endpoint_move` already refuses at the database when a
payment carries a statement link (`fk_transaction_entries_reconciled_by`, its own docstring; ledger row
BAL-503, owned by balance:X-bi-6-4); under R-CC43 a MATCHED leg's payment refuses the same way
(`fk_statement_match_members_entry_account`); transfer_service is the balance lane's live file this hour.

Worked: the mortgage transfer settled, its Checking leg matched to Checking's 9/1 line; the source
re-pointed to Second Checking. Before this leaf: refused at the database if the leg was cleared through
the panel (BAL-503); under this leaf also refused when matched. Under the clause: the refusal stays
and X-bi-6-4 owes the designed treatment (withdraw and disclose) when it re-parents the record.

### Confirmation 7 (asked and recorded 14:5x EDT, "Rule 5") -- the re-expressed tests

Picked: **"Confirm A, B and C as rule-5 re-expressions under R-CC43 (Recommended)"**

> The member names the payment; a day moves through the seam that mirrors it; a settled row is
> ticked as its payment. Each docstring quotes this confirmation; the commit stands.

Refused: *"Confirm B and C; keep A as written"* (the four member-shape tests back to a ROW member,
failing against the ruled code -> stop and re-open R-CC43); *"Confirm A and C; keep B as written"*
(the two raw row-column writes back, failing and pinning a state no door produces); *"Refuse:
re-open"*.

The groups as stated: A (4 tests: test_accept "the match is recorded with one member per subject",
test_income "it records a match naming both", test_residual "it is recorded as a member" and "the
members sum to the lines after an attributed match": the app-side member names the bill's PAYMENT,
not the row); B (2 tests: test_accept "a hand-moved day stops it agreeing", test_release "an act that
no longer holds is shown however old": the day moves through the row's own door, which mirrors it
onto the payment the act names); C (the builder `a_submission` resolving a settled row to its
payment; four window cases in test_candidates asking the payment; the token round-trip staging a
settled row; two pane ids in test_statement_reconcile; three CC-5-3 cases). Stated in the question:
"a row whose REVISION moved is refused" keeps its raw write (the payment's token carries the row's
counter since the review's L2), nothing to confirm.

## CC-5-5a `aa29d977`, ticked 2026-09-22: CC-5-5's specification, its `steps.md` row and ledger row CC-354 as they stood, then Blocks 1-6 VERBATIM (the card lane's record, `rulings_cc5_5.md`)

- [ ] **CC-5-5** `fix(cards): a card in credit is the issuer owing` -- `R-CC41`: the five `abs()`
      sites (`balance_at/_liability.py` `_spliced_owed_series` and `liability_owed_at_dates`,
      `savings_dashboard_service/_net_worth.py`'s hero and trend series, `_debt_line.py`'s
      no-payoff-model debt total) as ONE subject, the trend's index 0 still reconciling to the hero
      by construction; `$0.00` on production (no card). Closes **CC-354**.

| credit_card | CC-5-5 | -- | Fix the five net-worth sign sites (`_liability.py`'s spliced owed series and `liability_owed_at_dates`, `_net_worth.py`'s hero and trend, `_debt_line.py`'s debt total) as ONE subject so a card in credit reads as the issuer owing and the trend's index 0 still reconciles to the hero (**R-CC41**); `$0.00` on production. Closes **CC-354**. | #5 | -- | NOW |

| credit_card | CC-354 (`credit_card:CC-3`'s trace 2026-09-18; the cut-b review's census the same night) | -- | **THE NET-WORTH SURFACES REPORT A CARD IN CREDIT AS OWED.** Five sites take `abs()` of a plain liability's fold: `balance_at/_liability.py:80` (`_spliced_owed_series`) and `:218` (`liability_owed_at_dates`), `savings_dashboard_service/_net_worth.py:291` (`compute_net_worth_today`, the hero) and `:370` (the trend series), `_debt_line.py:261` (the no-payoff-model debt total) -- and `_liability.py:137-140` states the band's index 0 reconciles to the hero "by construction", so a fix at one site alone breaks that. A card the issuer owes `$50.00` (a credit balance; the fold reads +50.00) reads `$50.00` OWED where `-$50.00` is true; CC-3's statement module keeps the ONE sign flip **R-CC29** rules (owed = minus the fold) and reads none of these | `$0.00` on production (no card exists); on a card in credit every surface reads net worth low by twice the credit | **OPEN, born with an owner** (developer 2026-09-18, ruled in the coordinator session) | CC-5-5 (developer, 2026-09-21: its own leaf under CC-5, **R-CC41**) |

### Block 1 -- R-CC47 (Q1, the design)

Question as asked (verbatim):

> CC-5-5 (the card-in-credit sign fix): the net-worth screens take abs() of every liability because
> the balance code reports in two signs. A loan with its terms set up reports what you OWE as
> positive (Mortgage +$176,719.77); every other account, cards included, reports what it HOLDS (a
> card owing $1,000 is -$1,000; a card holding a $50 credit is +$50), so abs() counts that $50
> credit as $50 owed. Measured on your data plus a hypothetical Visa owing $1,000, Amex with a $50
> credit, auto loan with no terms owing $5,000 and family loan owing $2,000: the hero says
> liabilities $199,515.28 and net worth $227,242.71; the truth is $199,415.28 and $227,342.71 (low
> by $100). Production is $0.00 either way (your two liabilities are set-up loans, never negative).
> Every option gives the true figures; which design?

PICKED: **"A: one sign, re-scoped now (Recommended)"**:

> Every balance is what the account holds, negative when owed; a set-up loan reports -$176,719.77
> like a card. 'Owed' is minus the balance, R-CC29's one flip moved into the balance seam. Net
> worth is the plain sum of all balances with no sign branch left; the five abs() and the subtotal
> defect go. Every loan screen and liability tile shows what is owed, so production's screens do
> not change (a hypothetical card tile would read $1,000 where it reads -$1,000 today). CC-5-5 is
> re-scoped to this: more than one leaf, the loan path re-signed, about 5 loan-balance readers
> moved onto 'owed', and every loan test figure that flips sign is a rule-5 re-expression you
> confirm. I propose the leaf cut before building.

REFUSED:

- "C now, A as its own later step": "CC-5-5 ships C now: the seam publishes one 'owed' figure whose
  rule knows which calculation reports which sign (a set-up loan as is, everything else minus). The
  five sites and the subtotal read it, and tiles are unchanged. A is minted as its own later step
  that re-signs the loan path and deletes C's rule. Faster to ship, but it builds scaffolding that is
  planned for deletion."
- "B: one sign in the net-worth view": "Cards and liabilities without loan terms flip once inside the
  seam's net-worth view, so every liability reads as owed there; loans are untouched. Same screens
  as A and a smaller change. But a card's seam balance (+$1,000) then disagrees in sign with its own
  grid (-$1,000), and every future sum across account types must remember to treat liabilities
  differently."
- "C only, no later step": "The seam's one 'owed' rule that knows which calculation reports which
  sign, with no plan to unify the two signs. Smallest change. The two signs stay for good, so the
  next screen that adds raw balances repeats the subtotal bug."

Specifics in the picked text that the reader census must CONFIRM or CORRECT before the cut is
approved (coordinator, 2026-09-22): "about 5 loan-balance readers" and "production's screens do
not change".

### Block 2 -- R-CC48 (Q2, the sixth site)

Question as asked (verbatim):

> CC-5-5 found a sixth site the ruling did not name: on /savings, the liability group subtotal
> (which the chart legend prints beside the liability band) adds liability balances raw, with loans
> positive and cards negative. With a hypothetical Visa owing $1,000, Amex with a $50 credit, auto
> loan with no terms owing $5,000 and family loan owing $2,000 beside your two loans, it reads
> $183,515.28 while the band beside it reads $199,515.28 (the truth is $199,415.28). It is $0.00 on
> production, where both liabilities are loans. Fix it in CC-5-5?

PICKED: **"Yes, same subject (Recommended)"**:

> It is the same two-sign defect on the same screen, and the legend labels the band the hero
> reconciles to. Designs A and B fix it with the same change; C needs one line reading 'owed'.

REFUSED: "No, file it separately": "CC-5-5 stays at the five named sites. The subtotal becomes its
own ledger row with an owner, and the coordinator grants the id."

The site: `app/services/savings_dashboard_service/_display.py` `_compute_group_subtotals` (sums
`ad.current_balance` per category group; its docstring claims "Liability groups sum the loan
resolver's positive owed balances", false for any non-loan liability).

### Block 3 -- R-CC49 (Q3, the revolving footer)

Question as asked (verbatim):

> The /savings footer 'Loans paid off <month>' adds 'excludes $X revolving' for liabilities with no
> payoff date (cards, and liabilities without loan terms). With a Visa owing $1,000, an Amex
> holding a $50 credit, an auto loan with no terms owing $5,000 and a family loan owing $2,000, it
> says $8,050 today, counting the Amex credit as debt. Once the sign is fixed, what should X be?

PICKED: **"$8,000: floored per account (Recommended)"**:

> Each account's owed amount, floored at zero, then summed. A credit on one card is not debt and
> does not pay down another card; the footer counts the debt a payoff date leaves out. This is the
> same zero floor R-CC29 puts on the statement minimum.

REFUSED: "$7,950: net of all of them": "The Amex credit offsets the debts, the way net worth counts
it. Whenever a card holds a credit, the footer then understates the debt that has no payoff date."

### The measurements the questions quoted (HEAD 4d97749e, clone shekel_cc55)

- Production: 9 accounts; 2 liabilities, both CONFIGURED loans (Mortgage id 3, Van Loan id 8).
  Current 176,719.77 / 14,745.51; dense map over 64 periods min 169,499.28 / 3,026.43; the forward
  fold at every horizon sample (25, to 2049-12-31) and on EVERY day for 40 years: min 0.00, zero
  negative days. So every option is $0.00 on production's net-worth figures.
- Production as is: hero liabilities 191,465.28, net 235,292.71; 2y band and Horizon index 0 equal
  the hero; legend subtotal 191,465.28; revolving footer 0.00.
- Plus the four hypothetical liabilities (rolled back): hero 199,515.28 / 227,242.71; 2y band and
  Horizon index 0 199,515.28; legend subtotal 183,515.28; footer 8,050.00; tiles Visa -1,000.00,
  Amex 50.00, auto loan -5,000.00, family loan -2,000.00, Mortgage 176,719.77, Van Loan 14,745.51.
- Baseline blob for the byte-identical grade: `verify_savings_producers.py` on HEAD 4d97749e
  against shekel_cc55 -> `credit_card-2026-09-22/cc55/before_head.json` (77,009 bytes, 2 users).

### Block 4 -- R-CC50, the LEAF CUT (asked ~17:5x 2026-09-22, after the reader census)

Question as asked (verbatim):

> CC-5-5 leaf cut (your ruling R-CC47: one sign, every balance is what the account holds, 'owed'
> is minus it). The census CORRECTS two specifics you ruled: the loan-balance readers moving onto
> 'owed' are 7, not 'about 5' (debt strategy; the loan pages' one balance property feeding the
> dashboard and payoff/refinance calculators; home equity; on /savings a liability tile's figure,
> its sparkline, and two debt-summary figures). 'Production's screens do not change' holds by
> census, and a committed before/after screen diff will grade it. Order: CC-5-5 goes BEFORE
> recurrence R16-c-2 (#5 vs #23; it moves no money and does not touch the loan stream R16-c-2
> rewrites). The sign flip must be ONE commit: flipping loans before /savings moves breaks the
> subtotal, tiles and debt figures, and moving /savings first needs the per-account-type rule you
> refused. Which cut?

PICKED: **"5a groundwork, then 5b flip (Recommended)"**:

> CC-5-5a: R-CC29's one flip moves from card_statement into the balance seam, and the statement
> reads it (byte-identical); the footer becomes each non-loan liability's owed amount floored at
> zero, summed (R-CC49; those accounts already report held, so no loan re-sign; $0.00 on
> production); a screen-diff instrument over /savings, the loan dashboard and its calculators, and
> /debt-strategy on the clone, baselined on HEAD. CC-5-5b, one commit: the loan path reports held,
> the hero, trend, band and subtotal read the sum of balances and 'owed', the 7 readers move onto
> 'owed', liability tiles show owed, tests are re-signed after you confirm the classes with counts,
> and 5a's diff must read byte-identical on production's screens.

REFUSED: "One leaf, no container": "All of CC-5-5a and CC-5-5b in one commit and one PR: the flip's
move into the seam, the footer floor, the screen-diff instrument, and the sign flip with every
reader. Fewer ticks, but the grading instrument is not committed and baselined ahead of the change
it grades, and the single session carries both."

### Block 5 -- ledger row CC-357 (owned by credit_card:CC-11), the card tile's editor (asked with Block 4)

Question as asked (verbatim):

> A side effect of your ruling that liability tiles show what is owed: on /savings a card or
> custom-liability tile would read $1,000 (owed) while its click-to-edit anchor editor still
> pre-fills and takes the held figure, -$1,000.00. Loan tiles are unaffected, since the editor
> refuses every loan type and they stay read-only. Production has no such account, so this is
> latent. How should CC-5-5 handle it?

PICKED: **"Report it for CC-11 (Recommended)"**:

> CC-5-5 leaves the anchor editor alone and files a ledger row (id from the coordinator) owned by
> CC-11, the card cockpit and grid affordances step, which designs that editor. The editor is a
> WRITE door; changing the sign it takes is a behaviour change that belongs with the step designing
> it.

REFUSED: "Fix it inside CC-5-5b": "The anchor editor on a liability shows and takes what is owed,
flipping once at the door through the same 'owed' function. This widens CC-5-5b into a write door
(the true-up), so a sign mistake there would record a wrong balance assertion."

Facts behind Block 5 (measured on HEAD 4d97749e): `routes/accounts/anchor.py` `anchor_form` refuses
every AMORTIZING account (`_anchor_kind_refusal`), so loan tiles, configured or not, are read-only;
it pre-fills `cash_ledger.resolve_anchor(account).balance`, the HELD figure, for a card or a custom
(non-amortizing) liability.

### Block 6 -- R-CC52, THE TYPED SIGN (asked ~18:3x 2026-09-22 after 5a's adversarial review, finding F1). AMENDS R-CC50 (the coordinator files R-CC50 with an "AMENDED 2026-09-22 BY R-CC52" head); CC-357's owner moves CC-11 -> CC-5-5b; names confirmed CC-5-5a / 5b (the doors) / 5c (the flip); next free card R-CC53 / CC-360

Evidence (measured on clone shekel_cc55 / HEAD 4d97749e before asking): `templates/accounts/form.html:60-68`
"Opening Balance" help text "The account's real-world balance." (no sign guidance);
`budget.account_openings`: Mortgage (id 3, configured) +174,281.51, Van Loan (id 8) -531.94;
`budget.account_anchor_history`: Mortgage +178,103.41 (2026-05-01), Van Loan 0.00;
`tests/test_services/test_balance_at.py` builds a "Terms Never Entered" Mortgage at +150,000.00 and
one test says the cash producer "owes exactly the anchor".

Question as asked (verbatim):

> CC-5-5's review found that the premise of your ruling R-CC47 ('every balance is what the account
> holds, negative when owed') is not enforced where balances are TYPED. The account create form asks
> 'Opening Balance: the account's real-world balance' with no sign guidance, and your own Mortgage
> was typed positive (+$174,281.51 opening, +$178,103.41 anchor) while the Van Loan's opening is
> -$531.94; the suite's 'terms never entered' Mortgage is +$150,000 and a test calls that owed. Cards
> are ruled negative-when-owed (R-CC14/R-CC29), but a loan without terms or a custom liability
> carries whatever sign the owner typed; abs() was hiding that. Worked case: an owner adds 'Car
> loan' (terms not entered) and types 5,000.00. Today it counts as $5,000 owed; once R-CC47's flip
> lands it counts as a $5,000 ASSET, net worth off by $10,000. Production has no such account. How
> should the sign be fixed?

PICKED: **"Doors ask 'owed' (Recommended)"**:

> From scratch: every door that takes a balance for a LIABILITY account (the create form's opening
> balance, the anchor editor, the books-opening door) asks for the amount OWED (positive = you owe;
> a card's credit entered as a negative amount owed) and stores the held sign through the one
> owed() flip, so the premise is true by construction. Typing 5,000.00 on the car loan stores
> -5,000.00 and reads $5,000 owed. It is a write-door change, so it becomes its OWN leaf between 5a
> and the flip (CC-5-5b the doors, CC-5-5c the flip), re-opening your cut (R-CC50) and folding
> CC-357 (the tile editor you routed to CC-11) into it. 5a commits as built.

REFUSED:

- "Refuse a positive entry": "Keep 'held' as the stored convention and make the doors fail closed:
  a positive balance typed on a liability that cannot hold a credit (a loan, a custom liability) is
  REFUSED with 'enter what you owe as a negative amount'; a card may still be positive (a credit).
  Typing 5,000.00 on the car loan is refused, and the owner re-types -5,000.00. Also a write-door
  change, so its own leaf before the flip, same re-cut. An overpaid loan without terms could then not
  be entered."
- "Document 'held', add guidance": "Keep 'held' as the convention and only add sign guidance to the
  doors' help text ('negative for something you owe', as the books-opening door already says); no
  refusal and no flip at entry. The smallest change, but an owner who types 5,000.00 anyway, as the
  Mortgage was typed, gets a $5,000 asset, silently. 5a commits as built and the cut stands."
- "Sign by kind, not held": "Withdraw R-CC47's premise for liabilities other than cards: a card is
  held (negative = owed); every other liability is read as a MAGNITUDE owed whatever sign was typed.
  The car loan reads $5,000 owed either way. It brings back a per-account-type sign rule (the shape
  you refused as 'C'), cannot represent an overpaid loan without terms, and 5a's footer must be
  rebuilt for non-card liabilities."

Consequences as ruled: the cut is now CC-5-5a (built) -> CC-5-5b (the liability doors ask owed;
CC-357 folded in) -> CC-5-5c (the one-commit flip, formerly "5b"). R-CC50's "5b" text now describes
CC-5-5c. OPEN for CC-5-5b's first question (not asked here): the EXISTING positive-typed liability
entries (production: only the configured Mortgage's own cash-ledger opening and anchor, which feed no
net-worth surface because the loan reads its schedule) -- re-sign in a migration, or leave.

## CC-5-5a's tick review (2026-09-22, FIX-THEN-SHIP): the developer's two answers VERBATIM, as the coordinator relayed them

The questions were asked by the coordinator after the fresh review of the tick and `aa29d977`; their text is the coordinator's record. The picked option's description is the ruling; the options' labels are as asked.

### M1 -- the positive-typed liability and 5a's floored footer (R-CC49)

The finding: 5a's floored footer TRUSTS the typed sign, so until CC-5-5b makes the doors enforce it, a non-loan liability typed POSITIVE (e.g. an 'Auto Loan' with no terms typed +$5,000) reads owed -5,000, floors to $0 and HIDES the caveat; at HEAD `abs()` would have counted it. The coordinator measured the 2026-09-22 17:06 pre-deploy dump: 9 accounts; the only 2 liabilities, Mortgage and Van Loan, both have LoanParams.

PICKED: **"Ship 5a now, disclose (Recommended)"**:

> Merge 5a now; the PR and the R-CC49 ruling row name the positive-typed case. The window is real only if you
> create a card, a loan without terms, or a custom liability before 5b ships; I'll warn you until it does. No
> production figure changes today.

REFUSED: "Hold 5a until 5b"; "Revert the footer to abs until 5b".

### L1 -- the recurrence:R16-c-2 wait over both new leaves (R-CC50, R-CC52)

PICKED: **"Keep: after 5b and 5c (Recommended)"**:

> R16-c-2 starts only after the flip ships, so its loan tests are written once, against the final sign. It's
> unstaffed, so nothing waits today; I'll raise it again if a session frees up before 5c lands.

REFUSED: "Only after 5c's design"; "Drop the wait".

## CC-5-4a-2 `550cc9ce` and CC-5-4a-3 `175b192d`, ticked 2026-09-22: 4a-2's specification and `steps.md` row as they stood, the ledger rows closed, then both leaves' rulings VERBATIM (the card lane's records `rulings_cc5_4a2.md` and `rulings_cc5_4a3.md`)

4a-2 carries migration `2eabfa596ee0`, cut on `9900b309f0b0` and RE-PARENTED onto `balance:X-bi-6-3`'s `c7d1e9a4b2f8` at `62bf0e35` when 6-3 merged to dev first (#448; the two touch disjoint tables). 4a-3 carries none. Under **R-CC51** and **R-CC55** the two ship in ONE release, and CC-5-4a-4 (**R-CC54** parts 2 and 3) follows in its own. The commits are the record; the build logs, probes and adversarial reviews are `~/projects/shekel-handoffs/HANDOFF-credit-card-CC-5.md` and `credit_card-2026-09-22/`.

### CC-5-4a-2's specification as it stood, verbatim

- [ ] **CC-5-4a-2** `feat(cards): a member is a bank line or a movement` -- `R-CC45`'s second half:
      a migration re-keys every accepted act's row member (103 row members on the 2026-09-21 dump;
      221 acts at the 08-27 count) onto that bill's payment after a census on the newest production
      dump and an ASSERT of one act per row; a member with no payment to re-key onto (a bill closed
      from its purchases, a `$0.00` close, a Credit or Cancelled bill matched before today's
      refusals) REFUSES the migration and the developer rules it; then
      `statement_match_members.transaction_id` goes with its key and unique index, and the six
      bill-member readers (`_candidates.matched_subjects`, `_candidates._is_claimed`,
      `_accepted_view._accepted_row`, `_acts.named_rows`, `match_withdrawal`,
      `bank_agreement._rows_on`) lose that arm; with the row-member shape gone a definition's
      account move touches no member's subject (closes **CC-356**), and
      `status_seam/_covering.py:36-40`'s stale module docstring (it names `_candidates._price`,
      gone) is this leaf's to correct (announce-first, balance's region). Its own PR and release,
      graded byte-identical on production's shape first; rehearsal base the clone `shekel_cc54` (the
      2026-09-21 10:33 dump at `9900b309f0b0`).

### The `steps.md` row as it stood, verbatim

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| credit_card | CC-5-4a-2 | -- | Re-key every accepted act's row member onto its payment by migration after a census on the newest dump (103 row members on 2026-09-21; one with no payment to re-key onto REFUSES the migration), assert one act per row first, then drop `statement_match_members.transaction_id` with its key and unique index and the six bill-member readers' arm (**R-CC45**); OWN PR, OWN RELEASE. Closes **CC-356**. | #2 | -- | NOW / credit_card:CC-5-4a-1 (shipped; the writer whose stored past it re-keys) |

### Ledger rows CLOSED at this tick

**CC-356** CLOSED at `credit_card:CC-5-4a-2` `550cc9ce`: with the row-member shape gone a definition's account move touches no member's subject. Pinned through its own door by `tests/test_models/test_cc5_4a2_member_rekey.py::TestADefinitionsAccountMoveNoLongerMeetsTheMemberKey::test_the_definition_door_moves_the_row_once_the_act_names_its_payment` (on the old-shape member the door raises on `fk_statement_match_members_transaction_account`; re-keyed, the same door commits and the act is unchanged). The row as it stood:

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| credit_card | CC-356 (`credit_card:CC-5-4a-1`'s tick review 2026-09-21, M3; the developer's R-CC46 text "the row/definition doors reported, not fixed") | -- | **A DEFINITION'S ACCOUNT MOVE RE-ATTRIBUTES A PROJECTED ROW THAT A PRE-4a-1 ROW MEMBER STILL NAMES, AND THE MEMBER KEY 500s.** Since **R-CC36** `recurrence_engine/_maintain.py` (`propagate_to_unruled_definition`, :404-447; :225-240) moves every Projected row's `account_id` with its definition; a ROW member recorded before CC-5-4a-1 (103 on the 2026-09-21 10:33 dump, the reverted acts shown as "no longer holding" among them) is held to the row's account by `fk_statement_match_members_transaction_account` (NO ACTION on update, `app/models/statement_match.py:281-286`), so the move raises `IntegrityError` where R-CC46 owes a withdrawal and a disclosure | `$0.00`; a 500 on a definition move over a matched Projected row (0 cards; 103 row members on production's shape) | **OPEN, born with an owner**: the re-key deletes the row-member shape whole, after which a definition move touches no member's subject (the payment stays where the money moved, **R-CC42**); sequenced there by the developer's R-CC46 text | CC-5-4a-2 |

**CC-359** CLOSED at `credit_card:CC-5-4a-3` `175b192d` as NOT A DEFECT; it was never a row. Granted at CC-5-4a-2's review (L6) for `_release._remove`'s claim that the shared delete verb's withdrawal is a no-op, unverified for a row an act CREATED that later gained a purchase another act matched. Measured at 4a-3: adding a purchase does NOT move the row's `version_id` (2 -> 2 on a minted envelope, 3 -> 3 on a reverted residual), so the revision is not what holds it; `_release._container_survives`' content arm keeps a container that still holds a purchase, so releasing the first act never reaches the second. Pinned for the CONTAINER case by `tests/test_services/test_statement_match/test_withdrawal.py::TestReleasingAnActDoesNotWithdrawTwice::test_a_purchase_ANOTHER_act_matched_keeps_the_container_and_that_act`, which failed when that content arm was deleted (4a-3's mutation log); the residual and income cases are held by the refusals `_release._remove` names (`entry_service._refusals._reject_settled_addition`; after a revert the undo's revision check and `_accept._reject_parent_and_its_own_purchase`; `create_entry`'s expense-only guard), with no pin of their own.

### CC-5-4a-2's rulings and confirmations, VERBATIM as picked (AskUserQuestion in the CC-5-4a-2 session shekel-0d, 2026-09-22)

For the CC-5-4a-2 tick (the coordinator's). Each block: the option label + description as picked (his option text
IS the ruling), the refused options, and what the question stated. Ids are the coordinator's: CC-358 (the finding),
CC-5-4a-3 (the fix leaf), R-CC51 (the disposition, NOT FILED until the sequencing question below resolves).

#### Confirmation 8 (asked 17:3x-17:5x EDT 2026-09-22, recorded 17:56, header "Rule 5") -- the re-expressed tests, under R-CC45 (no id)

Picked: **"Confirm all four groups (Recommended)"**

> All four are rule-5 re-expressions under R-CC45. Each changed docstring quotes this confirmation, and the leaf
> commits.

The groups as stated in the question: Group 1, DELETE: the 2 tests of the old row-member shape
(`test_cc5_4a1_settlement_subject.py::TestAnActRecordedBeforeThisStepStillClaimsItsRow`) and 2 parametrize rows for
the dropped relationship (`test_statement_match_schema.py::TestASubjectIsReachedThroughTheAccountToo`, the
`StatementMatchMember.transaction` rows); their behaviour stays graded on the payment shape by existing tests and the
new migration test. Group 2, DELETE ONE LINE in 6 tests: the assertion "no member names the row", which the schema now
guarantees; each keeps its "the member names the payment" assertions (test_accept, test_income, test_residual,
test_cc5_4a1's card-member case, test_cc5_3's two register cases). Group 3, RE-POINT 8 tests from a row member to the
row's payment member, same assertions (schema: two subjects refused; one movement in two matches, now asserting
`uq_statement_match_members_entry`; the partial index; the three `e4a7c0f13b92` repair cases; bank_agreement: the
matched case and the covered bill and paycheck case). Group 4: the schema test helper stops naming the dropped column.
"No money assertion changes in any group."

Refused: *"Confirm 2-4; re-express group 1"* (rewrite the two old-shape tests onto an act naming the payment);
*"Refuse: re-open R-CC45"*.

NOT asked, reported instead (a harness change, no assertion moved): `tests/test_models/test_settle_day_basis.py`
`TestTheBackfillArmsAreExactOverTheirOwnPredicates._unpair` now also re-adds `statement_match_members.transaction_id`
for the duration of each (rolled-back) case, so migration `c7d31f9a45e8`'s frozen `classify_settle_days` runs against
the schema of its own revision; the four cases stage no member.

#### Ruling (asked 17:3x-17:5x EDT 2026-09-22, recorded 17:56, header "$0 re-record") -- the finding CC-358's disposition (R-CC51 when filed)

Picked: **"Own leaf next; 4a-2 not held (Recommended)"**

> New card leaf right after CC-5-4a-2, before CC-5-4b. It designs the fix with you (withdraw and disclose at the
> popover, as R-CC46 does, plus a structural end for the leftover match). CC-5-4a-2 ships as ruled. The defect is live
> for every new match either way; the register's Undo repairs any case.

Refused: *"Own leaf; hold 4a-2's release"* ("Same new leaf, but CC-5-4a-2's release waits until the fix ships, so the
103 older matches never meet the defect. CC-356 stays open on them meanwhile."); *"Fold the fix into CC-5-4a-2"*
("CC-5-4a-2 grows: the fix's design questions come first, then the build, in one PR and one release.").

The question as stated: "A defect found while tracing this leaf. It's live since today's 17:06 release, which shipped
CC-5-4a-1 (acts name the payment). Example: a Hotel bill, $120 on Checking, marked Paid 9/22; Checking's 9/24 bank
line "HOTEL -$120" matched to it. Later you type $0.00 as the paid amount on its popover (or switch it to 'from its
purchases'). NOW: the $120 payment is deleted and the match silently keeps only the bank line. The register shows it
as no longer holding, with 0 rows (its Undo still works). The 9/24 line shows as unexplained again, and matching it
to any other row gives an error page, because the old match still holds the line. BEFORE today's release, and still
today for the 103 older matches: the match stays, reads amber ($0 vs -$120) with its Undo, and the line stays
explained. CC-5-4a-2's migration moves those 103 older matches onto the new behaviour. CC-5-4a-2 also closes CC-356,
another error page on the same 103."

#### The SEQUENCING conflict -- RESOLVED (the coordinator's final question, quoting all four answers, 2026-09-22)

**RESOLVED.** Josh picked **"Wait for the fix (Recommended)"**, verbatim: "CC-5-4a-2 commits and its PR merges to dev
now, but it deploys only together with or after CC-5-4a-3 (the CC-358 fix). The 103 older matches keep CC-356's
milder exposure (an error, nothing changed) until then and never meet CC-358's silent withdrawal. I cut other
releases around 4a-2's unreleased migration on dev. Answers (2) and (3) are withdrawn." Refused: "Release 4a-2 when
ready". **R-CC51** = the disposition as it now stands: CC-5-4a-3 its own leaf right after CC-5-4a-2, and CC-5-4a-2's
RELEASE waits for it (with or after). The history below is kept for the record.

The disposition above conflicted with the coordinator-session ruling on production ("Keep it, fix forward": "... The
fix goes out before or with CC-5-4a-2's release, never after."). Both sessions then asked Josh to reconcile, in
parallel, each tagging the OPPOSITE option "(Recommended)", and he took the recommended one both times:

- In THIS session (header "Sequencing"), picked **"Fix before or with 4a-2 (Recommended)"**: "The coordinator-session
  answer stands. CC-358's own leaf is built next, and CC-5-4a-2's release goes out after it or together with it.
  CC-5-4a-2 still commits and opens its PR now. This session's "not held" is withdrawn." Refused: *"4a-2 not held"*
  ("This session's answer stands. CC-5-4a-2 releases when ready, and the fix follows as the next leaf. "Never after"
  is withdrawn; you avoid a $0 re-record of a matched bill until the fix ships.")
- In the COORDINATOR's session, per the coordinator: picked "4a-2 not held (Recommended)".

The coordinator took the ONE final question (quoting all four answers; the lane's recommendation, which the
coordinator shares: fix before or with 4a-2 -- CC-358 silently withdraws a match AND 500s on re-match, where CC-356,
which 4a-2 closes, only refuses). Whatever it returns is the sequencing; CC-5-4a-3 as minted and CC-358's owner
= CC-5-4a-3 hold either way.

#### Measured for CC-358's row (2026-09-22, scratch probes on the CC-5-4a-2 tree, deleted after)

1. THE SEAM PATH (new since CC-5-4a-1): settle a bill, accept a match naming its payment, re-record it at $0.00 on the
   popover door (`apply_requested_status(..., submitted=typed(0.00))`): `status_seam._covering._withdraw` deleted the
   payment; the member cascaded; the act stood with its line member only; `matched_subjects` no longer claimed the
   line; the register listed the act `agrees=False` with 0 rows; accepting the line against another row raised
   `IntegrityError` on `uq_statement_match_members_line`.
2. A BULK DOOR (pre-existing since the purchase member): `test_withdrawal`'s template hard-delete shape (a matched
   PURCHASE's envelope removed by one bulk statement) leaves the same act; re-matching its line raised the same
   `IntegrityError` on `uq_statement_match_members_line`. So the re-accept 500 predates CC-5-4a-1 at the bulk doors
   `match_withdrawal`'s docstring names; CC-5-4a-1 added the seam's `$0.00` / purchases re-record as a new door into
   it, and CC-5-4a-2's re-key extends that door to the 103 older acts.

#### Confirmation 8a (asked after the review's L7, 2026-09-22, header "Rule 5 fix") -- a correction to Confirmation 8

The review found Confirmation 8's group-2 description exact for 4 of its 6 tests only. Asked: "... In the other 2
(test_cc5_3_settle_tender: "the matcher's price asks the screen's account" and "a member whose tender moved reads
zero") I deleted a different kind of line: 4 lines that valued a ROW member on the register, e.g. register(txn, day,
card) == -$120.00 and register(txn, day, checking) == $0.00. Each sits beside an identical line valuing the PAYMENT
member (register(movement, ...)) with the same expected figure, and those stay. ... (Group 3 was also tightened to
match its description: the schema tests now stage the row's PAYMENT, not a purchase.) Confirm deleting those 4
row-valuation lines under R-CC45?"

Picked: **"Confirm the 4 row-valuation lines (Recommended)"**

> Deleting the 4 register(txn, ...) lines is a rule-5 re-expression under R-CC45. The paired register(movement, ...)
> lines, with the same -$120.00 / $0.00 figures, carry the assertion. The docstrings quote this.

Refused: *"Refuse: keep an equivalent"* (add a test that the register still values a row's payment the same as the
old row arm did, asserted through covered_cash_leg on the row).

#### CC-358's clause (the coordinator, 2026-09-22): the day drill-down

`bank_agreement._lines_on` reads a line's claim off the member table WITHOUT `act_still_names_a_row()`, so an act left
holding only its line (CC-358's end state; the bulk doors' leftovers) shows that line "matched" on the day drill-down
while the review screen lists it unexplained. Pre-existing; rides CC-358 as a clause; CC-5-4a-3 fixes it with the rest.
CC-359 (granted): `_release._remove`'s "the withdrawal is a no-op" claim is unverified for a created row that later
gained a purchase another act matched; CC-5-4a-3 probes it first (does adding a purchase bump the row's version_id so
`planned_removals` refuses it?) and closes it as not-a-defect with the measurement if so.

### CC-5-4a-3's rulings, VERBATIM as picked (AskUserQuestion in the CC-5-4a-3 session shekel-0d, 2026-09-22)

For the CC-5-4a-3 tick (the coordinator's). Each block: the option label + description as picked (his option
text IS the ruling), the refused options, and what the question stated. **IDS GRANTED by the coordinator
(shekel-d1) 2026-09-22 ~19:5x: R-CC54 = Round 2 Q1 (the design), R-CC55 = Round 2 Q2 (the sequencing), R-CC56 =
Round 1 Q2 (the disclosure); step credit_card:CC-5-4a-4 minted (parts (2)+(3), the migration, the check
deleted; after CC-5-4a-3, blocked by it; migration-bearing, its own release after the 4a-2 + 4a-3 release);
ledger CC-363 (MONEY) = the side-effect destruction, owner CC-5-4a-4; CC-358's `_lines_on` clause moves to
CC-5-4a-4. Next free card R-CC57 / CC-364.**

#### Round 1 (asked 19:28 EDT, answered ~19:3x EDT 2026-09-22)

##### Q1 "Leftover" -- REFUSED, the premise rejected

The question offered four ways to make a match that outlives its payment impossible: B (a database rule
deletes the match when its last payment or purchase goes, R-BI10's shape), A (the database refuses the delete),
C (refuse at commit), D (keep today's shape and wire the doors). Josh's answer, verbatim: **"Are these options
the best from scratch design? They seem like fences trying to avoid the root cause."** No option picked; the
design was re-opened (see Round 2).

##### Q2 "Disclosure" -- PICKED (**R-CC56**): **"Both popover paths (Recommended)"**

> A caption under the Actual box ('Recording $0.00 withdraws 1 accepted match, so 1 bank line is unexplained
> again: 9/24 HOTEL -$120.00'). A second caption beside Paid/Received on a reverted row whose purchases would
> replace its payment. Both captions come from the one read the door acts on. The grid's one-click Mark Paid
> withdraws and logs it without a caption, as the Paid-from change does today.

Refused: *"Actual box only"* (only the $0.00 caption; the purchases path withdraws and logs without saying so
first); *"A confirm step"* (a second click, 'Save and withdraw the match', a control shape the Paid-from picker
does not use).

The question as stated: "R-CC51 already rules that the popover withdraws the match and says so first, as the
'Paid from' picker does ('Picking another account withdraws 1 accepted match, so 1 bank line is unexplained
again: 9/24 HOTEL -$120.00'). Two popover paths remove a matched payment: typing $0.00 in Actual on the Hotel
bill, and pressing Paid on a reverted bill that now has purchases (its purchases replace the payment). Grid
quick-actions have no popover in either case. Where does the popover say it?"

##### Q3 "Bulk doors" -- REFUSED, the premise rejected

The question (conditional on Q1's B) asked what the template / account / pay-period doors say when a match
goes with them. Josh's answer, verbatim: **"I want root cause solutions and the best from scratch design that
avoids this problem"**.

#### Round 2 (asked 19:4x EDT, answered ~19:4x EDT 2026-09-22) -- the re-opened design

Measured before asking (scratch probes on 550cc9ce, deleted after; outputs in the session scratchpad):
P5 -- a Projected one-off envelope 'Home Improvement' holding a $25.00 purchase recorded from Checking's 1/5
bank line (`create_purchase_from_line` into the existing envelope): `template_has_paid_history` = False;
`definition_delete.permanently_delete_definition` erased the purchase; Checking's `settled_cash_facts` sum
moved -25.00 -> 0 (the balance $25.00 higher than the bank); the act was left holding its line alone.
P6 -- a matched Paid $120 Hotel bill reverted to Projected, then its template permanently deleted: the kept
payment destroyed, the act left holding its line alone. Production census (17:06 dump `shekel_cc54a2_base`):
284 acts, 0 stranded, 0 lineless; 1 non-settled row holds a movement (an un-dated purchase under template 19
'Clothes', already refused permanent delete by its merchant rule) [FALSE, measured at CC-5-4a-4's entry: template 19 has no merchant rule and its permanent delete is permitted; see CC-363]; 0 future periods hold one; 0 matched
movements under a non-settled row.

##### Q1 "Root cause" -- PICKED (**R-CC54**): **"Root-cause design (Recommended)"**

> (1) ONE act takes a payment or purchase off the books: it reverses its ledger, takes it out of any match
> (withdrawing a match left with nothing and saying which bank line that freed), then deletes it. Every door
> calls it, including the popover's two paths. (2) The database stops cascading a row's delete to its payments
> and purchases. A row holding one is history, so the template and account permanent deletes archive instead,
> and truncate/regenerate lock its period. Home Improvement: archived, the $25 stays spent, the match stands.
> (3) A match's key to its payment or purchase stops cascading, like its key to the bank line. The screens'
> leftover-match check is deleted.

Refused: *"Same, bulk doors disclose"* ((1) and (3), but the template, account and pay-period doors take the
payments and purchases off through the one act and say so in their confirmation; the bank-confirmed purchase
destroyed on his say-so, Checking $25.00 higher than the bank until the line is recorded again); *"(1)+(2),
keep the check"* (the match's key keeps cascading; the screens' leftover-match check stays as the guard for a
door written later).

The question's stem: "Root causes found for CC-358. (1) Taking a payment or purchase off the books is written
separately in several doors, and the popover's $0.00 path forgot the match. (2) The template, account and
pay-period deletes destroy payments and purchases as a side effect, judging safety by 'never Paid' instead of
by what a row holds. Measured: permanently deleting the one-off 'Home Improvement', whose $25.00 purchase was
recorded from Checking's 1/5 bank line, erases the purchase, so Checking reads $25.00 higher than the bank, and
the match is stranded. A matched $120 Hotel bill, reverted then template-deleted, loses its kept payment the
same way. On production's 17:06 copy the rule changes nothing deletable today. [FALSE, measured at CC-5-4a-4's entry: template 19 'Clothes' (no merchant rule, no settled row) holds movement 343, $107.57, under a row its permanent delete may erase, and its archive drops that purchase from the fold; see CC-363] Which design?"

##### Q2 "Sequencing" -- PICKED (**R-CC55**): **"Two leaves (Recommended)"**

> CC-5-4a-3 builds part (1). CC-5-4a-2 then deploys with it, as R-CC51 requires, because the popover path is
> the one R-CC51 is about. A next leaf (id from the coordinator) builds parts (2)+(3) with their migration and
> deletes the check. Until that leaf ships, the bulk doors behave as today (the Home Improvement case stays
> live, with 0 instances on production). [FALSE, measured at CC-5-4a-4's entry: template 19 'Clothes' is one live instance; see CC-363]

Refused: *"One leaf, all three"* (CC-5-4a-3 builds (1), (2) and (3) with the migration, announced to the
template, account and pay-period owners; CC-5-4a-2's release waits for all of it).

#### Round 3 (asked ~20:2x EDT 2026-09-22, after the neutral review's M1) -- the transfer popover (**R-CC59**, granted by the coordinator; R-CC56 gains "extended to the transfer popover by R-CC59" at the tick)

Measured before asking (scratch probe on the 4a-3 tree, deleted after): a $500 Checking-to-Savings transfer
marked Paid, its Checking leg's payment matched to a -$500 line; `transfer_service.update_transfer(...,
figure=typed(0.00))` (the transfer popover's Actual box) -> acts 1 -> 0, the line unclaimed, both legs'
covering movements gone. The transfer popover rendered no caption. Census: only two templates post
`settled_amount` (grid/_transaction_full_edit.html, transfers/_transfer_full_edit.html). [INCOMPLETE as a census of the doors that take $0.00, read at the CC-5-4a-2 / 4a-3 tick: `accounts/_reconcile_panel.html:71` posts a per-row `settled_amount-<id>` (`min="0"`) that settles through `transaction_service.settle_transaction`, a door neither R-CC56 nor R-CC59 names]

##### Q1 "Transfer $0" -- PICKED: **"Same caption, this leaf (Recommended)"**

> The transfer popover's Actual box gets the bill popover's caption: 'Recording $0.00 withdraws 1 accepted
> match, so 1 bank line is unexplained again on your statement screen: 1/6 TRANSFER -$500.00'. It reads what
> taking BOTH legs' payments off frees, from the same read the door acts on. Built in CC-5-4a-3 (the transfer
> popover's route and template, cleared with the coordinator; balance's 6-3 does not touch them).

Refused: *"No caption; file it"* (this leaf states the transfer popover as a door that withdraws and logs
without a caption, like the grid's one-click Mark Paid; a ledger row files the transfer caption for a later
step).

#### Measured for BAL-530 (granted by the coordinator 2026-09-22 ~21:3x, owner balance:X-bi-6-4; filed at the 4a-3 tick)

The transfer full-edit popover (`GET /transfers/<id>/full-edit`, `app/routes/transfers/forms.py:get_full_edit`) calls
`transfer_legs.covering_movements_by_leg([xfer.id])` TWICE per render, on the CC-5-4a-3 tree:
(1) its figures -- `forms.py:108` `transfer_settlement_amounts(xfer, current_user.id)` -> `app/routes/_render_helpers.py:347`
`grid_transfer_leg(xfer, xfer.from_account_id)` -> `app/services/transfer_legs.py:631` `covering_movements_by_leg([transfer.id])`;
(2) R-CC59's caption -- `forms.py:131` `_payment_withdrawal(xfer)` -> `forms.py:160`
`transfer_legs.covering_movements_by_leg([xfer.id])`. One indexed query each (`_covering_movements_query` narrowed to
one transfer). Left in 4a-3 because resolving it once and threading it through both reads changes the signatures of
`transfer_settlement_amounts` (`_render_helpers.py`, where balance:X-bi-6-3 carries a docstring hunk) and
`grid_transfer_leg` (X-bi-6's leg producers, the join X-bi-6-4 moves onto the link); `_payment_withdrawal`'s docstring
states the second call honestly (round-3 review L5).

#### Consequences for the registries (the coordinator's to cut at the tick; ids not yet granted)

- A ruling for the disclosure (Round 1 Q2), one for the root-cause design (Round 2 Q1), one for the sequencing
  (Round 2 Q2) -- or fewer if the coordinator folds them.
- A NEW STEP: the leaf that builds parts (2)+(3) + migration + deletes the check (`act_still_names_a_row`),
  ranked after CC-5-4a-3.
- A NEW LEDGER ROW (money): the template / account / pay-period doors destroy payments and purchases as a side
  effect (P5's +$25.00; P6's kept payment), owned by that new step. CC-358's `_lines_on` clause moves to the new
  step (it dies with the check); CC-358 itself (the popover path) closes at CC-5-4a-3.
- CC-359 closes at CC-5-4a-3 as NOT A DEFECT (measured 19:21; the coordinator acked).

## CC-5-5b `3d9d0c1a` (two commits, `ef4f6782` first) and CC-5-5c `6daa3048`, ticked 2026-09-23: the CC-5-5 entry, 5b's and 5c's specifications and `steps.md` rows as they stood, the ledger rows closed and re-owned, then Blocks 7-18 VERBATIM (the card lane's record, `rulings_cc5_5.md`)

5b and 5c reach dev in ONE pull request and deploy in ONE release (the coordinator's rulings, 2026-09-22: a release carrying 5b's owed-speaking doors without 5c's owed tiles would show a card tile at -$1,000 whose editor asks 1,000.00 owed); neither carries a migration. CC-5-5d, the debt goals (**R-CC69**..**R-CC73**), was minted at this tick, its own PR and release after them. **Blocks 13 Q1 and 16 are rule-5 CONFIRMATIONS and take no ruling id** (CC-5-4a-2's Confirmations 8 / 8a are the precedent): the developer confirmed the four classes of 45 failing tests re-signed in sign only (26 loan balance reads, 10 checkers, 8 old-sign inputs, 1 footer wording) and the vacuity census's three fix kinds (5 tests re-signed, false wording, the loan's shown sign pinned), "the dollars in every assertion stay the same". Two counts in the record below were corrected after it was written: Block 17's stem said a grid save read the governing assertion 4 times, measured 5 for a today-dated save (17c), and **R-CC85** took the final counts to 2 today and 3 back-dated. The commits are the record; the build logs, probes and reviews are `~/projects/shekel-handoffs/HANDOFF-credit-card-CC-5-5.md` and `credit_card-2026-09-2{2,3}/`.

### The CC-5-5 entry and 5b's and 5c's specifications as they stood, verbatim

- [ ] **CC-5-5** `fix(cards): one sign for every balance` -- `R-CC47` (re-scoping `R-CC41`; the
      sixth site `R-CC48`): every balance is what the account HOLDS, negative when owed, and owed is
      minus it; the DECOMPOSED parent, split 2026-09-22 by the developer (`R-CC50` as amended by
      `R-CC52`) into 5a (the groundwork), 5b (the liability doors ask owed) and 5c (the one-commit
      flip); ticks with its last leaf. `$0.00` on production's net-worth figures (both liabilities
      are configured loans).
  - [x] **CC-5-5a** `aa29d977` -- `balance_at.owed(balance) = -balance`, R-CC29's one flip moved
        into the seam (`card_statement.owed` deleted); the /savings revolving footer is each
        non-loan liability's owed amount floored at zero, summed (`R-CC49`);
        `tests/manual/verify_liability_screens.py`, the screen-diff instrument (178 responses plus a
        `tree.json` app digest) that grades 5b and 5c; 178 screens byte-identical on production's
        shape.
- [ ] **CC-5-5b** `fix(accounts): a liability's balance is asked as owed` -- `R-CC52`: every door
      that takes a LIABILITY's balance asks for the amount OWED (positive = you owe; a card's credit
      a negative amount owed) and stores the held sign through `balance_at.owed`, one crossing per
      door (the form speaks owed, the service stores held; the schema tier may not branch on account
      class, as `schemas/validation/accounts.py` states): the account create form's opening balance
      (`templates/accounts/form.html`, its route, `AccountCreateSchema`,
      `account_service.create_account`), the anchor editor (`routes/accounts/anchor.py`
      `anchor_form` and its PATCH, `grid/_anchor_edit.html`; amortizing accounts stay refused) and
      the books-opening door (`routes/accounts/opening.py`, `opening_service`,
      `accounts/_books_opening.html`), each entered and shown as owed, the pre-fill and a 422
      redisplay included; the tile editor's held pre-fill (**CC-357**) closes with it. FIRST, a
      census of every STORED liability balance on the newest production dump
      (`account_openings.opening_equity`, `account_anchor_history.anchor_balance`, any other
      books-opening or assertion row), each read under `R-CC47`, and of what reads them: the
      Mortgage's `+174,281.51` opening and `+178,103.41` anchor were typed as owed, so wrong-signed
      (both a configured loan's, feeding no net-worth figure; its cash fold and the posted ledger's
      Balance Sheet are what to trace). A wrong-signed stored value is a MONEY question to the
      developer, with worked dollars, BEFORE 5c; any backfill is an Alembic migration, which makes
      this leaf migration-bearing and its own release. Before 5b grades with it, 5a's instrument
      gains `/grid?account_id=<id>` for each non-amortizing liability (the balance line the anchor
      door opens from). Graded by 5a's instrument: every ASSET screen byte-identical, a liability
      form's label change a declared change. Closes **CC-357**.
- [ ] **CC-5-5c** `fix(cards): a card in credit is the issuer owing` -- `R-CC47` and `R-CC48`, ONE
      commit: the seam's two configured-loan arms report the HELD sign (`positions()` and the loan
      domain's producers stay owed), each arm converting through the one flip and never an inline
      `-positions(...)` (CLAUDE.md rule 14) -- so FIRST `owed` MOVES out of `_liability.py`, which
      imports `_inputs` and `_kind_correct` (the two arms), to a module with no seam imports (e.g.
      `balance_at/_sign.py`; the public name `balance_at.owed` stays), and its docstring names the
      seam's other NON-held outputs (`liability_owed_at_dates`' magnitudes for every liability,
      `positions`, `secured_loan_series`), none of which may be passed to it; the hero and trend
      read the plain sum of balances with liabilities as `owed`, the band drops its `abs` and the
      liability subtotal (`_display._compute_group_subtotals`, `R-CC48`) reads `owed`; the seven
      loan-balance readers move onto `owed` (debt strategy; the loan pages' `current_balance`
      property feeding the dashboard, the payoff and refinance calculators and the true-up pre-fill;
      home equity; on /savings the liability tile's figure, its sparkline and two debt-summary
      figures) and liability tiles show owed. The flip also decides what a savings goal backed by a
      LIABILITY reads (**CC-360**: `_goal_form_context` offers any active account, a loan included,
      and `_goals._goal_account_balance` reads a configured loan's OWED figure as progress today and
      its held balance once the loan arm is re-signed): 5c either gains that reader, the goal
      reading `owed()`, or refuses a liability as a goal's account, a design question the lane puts
      to the developer with worked dollars before building; and the footer's "revolving" caption,
      whose sum also counts an amortizing account with no loan terms and a custom liability, is
      corrected (**CC-361**). Every loan test figure that flips sign is a rule-5 re-expression the
      developer confirms, its classes with counts, before the commit. Graded by 5a's
      `tests/manual/verify_liability_screens.py` per its docstring's procedure: production's screens
      byte-identical. Closes **CC-354**, **CC-360**, **CC-361**.

### The `steps.md` rows as they stood, verbatim

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| credit_card | CC-5-5b | -- | Make every door that takes a LIABILITY's balance (the create form, the anchor editor, the books-opening door) ask for the amount OWED and store the held sign through `balance_at.owed` (**R-CC52**), first censusing every STORED liability balance on the newest dump under **R-CC47** (a wrong-signed one is the developer's money question before 5c, any backfill a migration). Closes **CC-357**. | #4 | -- | NOW / credit_card:CC-5-5a (shipped; `balance_at.owed`, the one flip its doors store through) |
| credit_card | CC-5-5c | -- | Flip every balance to the HELD sign in ONE commit (**R-CC47**, **R-CC48**): the seam's configured-loan arms report held, the hero, trend, band and liability subtotal read the plain sum and `owed`, seven loan-balance readers and liability tiles move onto `owed`, loan tests re-signed on the developer's confirmation; byte-identical on production's screens. Closes **CC-354**, **CC-360**, **CC-361**. | #6 | -- | after #4 / credit_card:CC-5-5b (the doors that make the held sign true by construction, R-CC52) |
| credit_card | CC-5-5 | -- | The DECOMPOSED parent of ONE SIGN FOR EVERY BALANCE (**R-CC47**, re-scoping **R-CC41**; the sixth site **R-CC48**), split 2026-09-22 by the developer (**R-CC50** as amended by **R-CC52**) into the groundwork (5a), the liability doors that ask for the amount owed (5b) and the one-commit flip (5c); ticks with its last leaf. | container | -- | ticks with #6 |

### Ledger rows CLOSED at this tick, and one re-owned

**CC-354** CLOSED at `credit_card:CC-5-5c` `6daa3048`: all six sites read what a liability owes through `liability_sign.owed` (the band's two halves with no `abs`, the hero's `total_liabilities` the sum of `AccountProjection.owed` beside a plain-sum net worth, the trend, the subtotal through `shown_balance`; `_debt_line` fixed at CC-5-5a). Pinned by `tests/test_services/test_one_liability_sign.py::TestACardHoldingACreditIsACreditOnEveryNetWorthSurface::test_every_liability_figure_counts_the_credit_against_the_debt`.

**CC-357** CLOSED at `credit_card:CC-5-5b` (`ef4f6782`): the anchor editor on a liability pre-fills and takes the amount owed through `liability_sign`. Pinned by `tests/test_routes/test_liability_doors.py::TestTheAnchorEditor::test_a_card_owing_1000_opens_on_1000`.

**CC-361** CLOSED at `credit_card:CC-5-5c` `6daa3048`: the footer reads "excludes $X with no payoff date" (**R-CC68**). Pinned by `tests/test_routes/test_one_liability_sign_screens.py::TestTheSavingsPageSpeaksOwedForADebt::test_the_footer_names_debt_with_no_payoff_date`.

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| credit_card | CC-354 (`credit_card:CC-3`'s trace 2026-09-18; the cut-b review's census the same night) | -- | **THE NET-WORTH SURFACES REPORT A CARD IN CREDIT AS OWED.** Five sites took `abs()` of a plain liability's fold: `balance_at/_liability.py:153` (`_spliced_owed_series`) and `:291` (`liability_owed_at_dates`), `savings_dashboard_service/_net_worth.py:291` (`compute_net_worth_today`, the hero) and `:370` (the trend series), and `_debt_line.py`'s no-payoff-model debt total (FIXED at `aa29d977`, CC-5-5a: each account's owed amount floored at zero, **R-CC49**); **R-CC48** adds a sixth, `_display.py:176`'s liability subtotal, summing the balances raw -- and `_liability.py:210-213` states the band's index 0 reconciles to the hero "by construction", so a fix at one site alone breaks that. A card the issuer owes `$50.00` (a credit balance; the fold reads +50.00) reads `$50.00` OWED where `-$50.00` is true; the ONE sign flip **R-CC29** rules (owed = minus the fold) is `balance_at.owed` since `aa29d977` (**R-CC47**) and none of the four `abs()` sites or the subtotal reads it | `$0.00` on production (no card exists); on a card in credit every surface reads net worth low by twice the credit | **OPEN, born with an owner** (developer 2026-09-18, ruled in the coordinator session) | CC-5-5c (developer, 2026-09-22: the one-commit flip holds these sites, **R-CC47** and **R-CC50** as amended by **R-CC52**) |
| credit_card | CC-357 (`credit_card:CC-5-5`'s leaf-cut question 2026-09-22, Block 5; filed by the developer's pick) | -- | **AFTER THE FLIP A CARD OR CUSTOM-LIABILITY TILE SHOWS WHAT IS OWED WHILE ITS CLICK-TO-EDIT ANCHOR EDITOR PRE-FILLS AND TAKES THE HELD FIGURE.** **R-CC47** has every liability tile on /savings show the amount owed (CC-5-5c), but `routes/accounts/anchor.py` `anchor_form` pre-fills `cash_ledger.resolve_anchor(account).balance`, the HELD figure, for a card or a custom (non-amortizing) liability and its PATCH takes a held figure back: a card owing $1,000 would read `$1,000` on its tile and `-1,000.00` in its editor, and the tile's own figure typed back would assert a $1,000 credit, $2,000 from the truth. Loan tiles are unaffected: `_anchor_kind_refusal` refuses every AMORTIZING account, so they stay read-only | `$0.00` today (production has no card and no custom liability; the tile shows the held figure until CC-5-5c) | **OPEN, born with an owner** (developer 2026-09-22: *"Report it for CC-11"*, refusing *"Fix it inside CC-5-5b"*, the name the flip then had, now CC-5-5c; **R-CC52** the same evening folded it into the liability doors' leaf, the new CC-5-5b) | CC-5-5b |
| credit_card | CC-361 (`credit_card:CC-5-5a`'s build 2026-09-22, the lane's handoff s.0.5; filed by the coordinator at the CC-5-5a tick) | -- | **THE /SAVINGS FOOTER CALLS ITS SUM "REVOLVING" WHILE THE SUM ALSO COUNTS LIABILITIES THAT DO NOT REVOLVE.** `templates/savings/_cockpit.html:299` renders "excludes $X revolving", and `_debt_line.debt_without_payoff_model` (**R-CC49**, `aa29d977`) sums every liability with no `loan` -- set exactly when a loan is configured -- so an amortizing account with no loan terms and a custom liability are counted beside the cards. The FIGURE is right and the LABEL is not | `$0.00` today (no such account: 0 non-loan liabilities on the 2026-09-22 17:06 pre-deploy dump (9 accounts; its only 2 liabilities, Mortgage and Van Loan, carry `LoanParams`)) | **OPEN, born with an owner** (coordinator 2026-09-22, rule 1, at CC-5-5a's tick; the lane's measured record reported it unfixed; CC-5-5c is the leaf that touches those screens' captions) | CC-5-5c |

**CC-362** CLOSED at `credit_card:CC-5-5c` `6daa3048`; it was never a row. Granted 2026-09-22 with **R-CC53** (owner CC-5-5c, its text in Block 7 below) and fixed before any tick filed it: an archived debt is an `ArchivedDebt` whose figure is what it owes today (**R-CC67**), no longer the typed assertion. Pinned by `tests/test_services/test_one_liability_sign.py::TestAnArchivedDebtShowsWhatItOwesToday`.

**CC-365** CLOSED at `credit_card:CC-5-5c` `6daa3048`; it was never a row. Granted 2026-09-23 (the /savings card tile flashing its held figure for one round trip after a save, because every save answered with the grid's cell) and fixed inside 5c by **R-CC74** / **R-CC77**. Pinned by `tests/test_routes/test_anchor_save_answers_its_surface.py::TestTheSaveAnswersWithTheOpenersDisplay::test_a_card_saved_from_savings_shows_what_it_owes`.

**CC-360** RE-OWNED CC-5-5c -> CC-5-5d and re-worded to the post-flip state (a goal on a debt now reads its held balance, `0%`); the row as it stood:

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| credit_card | CC-360 (`credit_card:CC-5-5a`'s build 2026-09-22, the lane's handoff s.0.5; filed by the coordinator at the CC-5-5a tick) | -- | **A SAVINGS GOAL BACKED BY A LIABILITY READS A DEBT AS SAVINGS -- LIVE TODAY FOR A CONFIGURED LOAN, WHOSE OWED FIGURE COUNTS AS PROGRESS, AND THE HELD, NEGATIVE BALANCE AFTER THE FLIP.** `routes/savings.py` `_goal_form_context` offers every active account (`account_service.list_active_accounts`) as a goal's account, a loan included, and `savings_dashboard_service/_goals._goal_account_balance` hands the backing account's `AccountProjection.current_balance` to the goal's progress (`percent_complete`): for a configured loan that is its OWED figure today (the Mortgage's `176,719.77` would count as saved), which **R-CC47**'s flip re-signs to HELD (`-176,719.77`); for a card or a custom liability it is already held. Nothing states what a goal on a debt means, so the flip decides whether a goal reads the amount owed through `owed()` or a liability is refused as a goal's account | `$0.00` on production (its one goal is on the Money Market); reachable today by any goal set on a configured loan | **OPEN, born with an owner** (coordinator 2026-09-22, rule 1, at CC-5-5a's tick; the lane's measured record reported it unfixed; a design question the lane puts to the developer with worked dollars) | CC-5-5c |

### Block 7 -- the pre-5b TYPED ROWS (CC-5-5b's first question; asked by lane credit-extra [a6edea] ~19:3x 2026-09-22 EDT after the stored-balance census). Ruling **R-CC53** (granted by the coordinator ~19:4x 2026-09-22); the archived-list ledger row **CC-362**, owner CC-5-5c (granted with it); statement import on a liability routed as **BI-507**, owner bank_import:X-hf (granted with it; facts at the end of this block). Next free: card R-CC54 / CC-363, bank_import BI-508

Census (clone `shekel_cc55b` = the 2026-09-22 17:06 pre-deploy dump `shekel_prod_predeploy_88d4e6463b17_20260922_170621.dump`, head 9900b309f0b0; what-if clone `shekel_cc55b_resign` = the same with account 3's two `account_openings` rows and one `account_anchor_history` row negated in place, triggers bypassed, throwaway):
- 9 accounts; 2 liabilities, both CONFIGURED loans; no card, no custom type, no unconfigured loan, no statement import on a liability.
- Mortgage (id 3): `account_openings` ids 3, 12 = +174,281.51 (`migration_derived`); `account_anchor_history` id 33 = +178,103.41 observed 2026-05-01 (evidence uncorroborated). OWED-typed: wrong sign under R-CC47.
- Van Loan (id 8): openings ids 7, 13 = -531.94 (`migration_derived`); anchor id 45 = 0.00 observed 2026-05-21 (sign-neutral; wrong in magnitude).
- NOT held-family stores (owed by design, the loan domain's; 5c converts at the seam's arm): `loan_anchor_events` 6 rows (CHECK >= 0), `loan_params.original_principal`. Posted ledger books both loans from the loan domain (`loan_opening` / `loan_trueup` only; no `account_opening` / `account_trueup` on 3 or 8): -176,719.77 / -14,745.51, already held.
- Measured (display day 2026-09-22): Mortgage kind-correct 176,719.77, cash fold +185,747.21 (re-signed what-if: -170,459.61, still 6,260.16 off: the fold accrues no interest); Van Loan 14,745.51, cash fold 2,127.76.
- Screens: `verify_liability_screens.py` (180 files) on both clones: ONLY `GET /accounts/3/anchor-display` and `/accounts/3/anchor-form` differ (`$178,103` -> `-$178,103`), both answered only on a direct request or the N-199 race (an amortizing tile is read-only). Plus 21 extra GETs (grid with account_id=3/8, calendar month/year for 3 and 8, dashboard?account_id=3, templates, settings, analytics): identical (the calendar and the dashboard refuse a loan account and render the default).
- Readers NOT on any listed screen, found by code census: (a) the ARCHIVED-accounts list on /savings (`_data.py:206` `last_anchor_balance=cash_ledger.resolve_anchor(acct).balance`, `dashboard.html:357`): with both loans archived (clone `shekel_cc55b_arch`) it reads Mortgage `$178,103.41`, Van Loan `$0.00` -- wrong in magnitude TODAY in either sign; (b) the loan SETUP page's "balance today" pre-fill (`routes/loan/_helpers.py:134`, unconfigured loans only; none on production) -- a pre-fill R-CC52 already puts in 5b.
- The account re-type door refuses an amortizing-boundary crossing once postings exist (`account_validation._validate_account_type_change`), and no door removes LoanParams short of deleting the account, so a configured loan never falls back to its typed rows.

Question as asked (verbatim):

> CC-5-5b (typed balances enter as 'owed'): your Mortgage's typed account records are in the old sign: +$174,281.51 opening and +$178,103.41 as of 2026-05-01. Under your ruling R-CC47 (every balance is what the account holds, negative when owed) both should be negative. Screens read the Mortgage from its loan schedule ($176,719.77 owed), so a what-if re-sign on a copy of production moved no net-worth figure, loan page or ledger entry. Only two places read these typed rows. (1) The loan's read-only balance cell, reachable only by typing its web address or through a rare race; it shows $178,103. (2) The archived-accounts list on /savings, which shows the last typed figure: an archived Mortgage would read $178,103.41 (it owes $176,719.77), and an archived Van Loan $0.00 (it owes $14,745.51). Re-signed, the rows would still be $6,260.16 off, because typed records never accrue interest. What should CC-5-5b do with them?

PICKED: **"Leave rows, stop reading (Recommended)"**:

> No migration, so 5b ships without its own release. The loan's read-only balance cell stops showing a figure and just points to the loan page (done in 5b). The archived list shows a loan's own balance as owed: an archived Mortgage reads $176,719.77 and a Van Loan $14,745.51, instead of $178,103.41 and $0.00. That becomes a new ledger row for CC-5-5c (id from the coordinator), since 5c moves every liability figure onto 'owed'. After that nothing reads a set-up loan's typed rows, so their sign cannot reach a screen.

REFUSED:

- "Re-sign in a migration": "A migration adds corrected rows (opening -$174,281.51, balance -$178,103.41 for 2026-05-01); the typed ones stay in the history. 5b then needs its own release, a third migration behind the two already queued. The migration's rule has to assume that a positive figure on a loan was typed as owed. The two readers stay: they keep showing a typed figure that is not what the loan owes ($178,103 against $176,719.77; the Van Loan's $0.00 against $14,745.51)."
- "Both: re-sign and stop reading": "The migration above plus both reader fixes from the first option. Every stored balance then matches R-CC47 by construction, at the cost of a migration and its own release for rows nothing reads any more."
- "Leave everything as is": "No migration and no reader changes. After 5b the loan's balance cell reads the typed row through the owed flip and shows -$178,103. The archived list keeps showing $178,103.41 and $0.00 for the two loans."

Rejected before asking (named in the chat text, not as options): a read-time rule negating a positive figure on a loan (the per-type sign rule refused as "C" under R-CC47); deleting a configured loan's typed rows (append-only, R-HY; every account needs an opening for the unconfigured state; the from-scratch loan ledger's territory).

#### CC-362 (ledger row, owner credit_card:CC-5-5c; granted ~19:4x 2026-09-22)

The ARCHIVED-accounts list on /savings shows each archived account's latest cash ASSERTION
(`app/services/savings_dashboard_service/_data.py:206` `last_anchor_balance=cash_ledger.resolve_anchor(acct).balance`,
rendered at `app/templates/savings/dashboard.html:357` `money(archived.last_anchor_balance)`), which for a
CONFIGURED loan is a typed row that is not the loan's balance: with both loans archived on clone
`shekel_cc55b_arch` (the 17:06 dump) it reads Mortgage `$178,103.41` (owes `$176,719.77`) and Van Loan
`$0.00` (owes `$14,745.51`) -- wrong TODAY in either sign. R-CC53: the list shows a loan's own balance as
OWED; CC-5-5c moves it with the other liability figures onto `owed`. Production has no archived liability
today (its one archived account is Fidelity Savings, an asset), so `$0.00` on production's screens today.

#### BI-507 (ledger row, owner bank_import:X-hf; granted ~19:4x 2026-09-22)

A statement import records the file's stated balance as an `account_anchor_history` level VERBATIM
(`app/services/statement_import/_anchor.py`, "its amount locked to the file's own claim by key"; the level
relation `app/models/account.py` `AccountAnchorHistory.statement_import_id`), with no liability sign handling
anywhere in `app/services/statement_import/` (grep for liabilit / revolving / negat / flip: none but a
comment) and no account-kind gate on the import routes (`app/routes/accounts/statements.py`,
`_statement_doors.py`, `bank_feed.py`: no `classify_account` / `has_amortization` / category test). So for
a LIABILITY the stored level carries whatever sign the issuer's file uses, where R-CC47 / R-CC52 require the
held sign (negative when owed) through `owed()`. `$0.00` today: no statement import on a liability on the
17:06 dump (`budget.statement_imports` joined to liability accounts: 0 rows), and the only parser on dev
is SECU's checking CSV (`_secu_csv.py`). The first leaf that imports a liability's statement ("the card on
the feed", X-hf) owns the sign at that door.

### Block 8 -- CC-5-5b's two door-shape questions (asked by lane credit-extra [a6edea] ~19:5x 2026-09-22 EDT in ONE AskUserQuestion, after the door trace). Ruling ids **R-CC57** (Question 1) and **R-CC58** (Question 2), granted by the coordinator ~20:0x 2026-09-22 (R-CC54..R-CC56 went to CC-5-4a-3); `app/static/js/account_form.js` joins 5b's claim. Next free card R-CC59 / CC-364

Trace facts behind them (HEAD aa29d977): the anchor editor (`grid/_anchor_edit.html`) opens from five surfaces
(`routes/accounts/anchor.py` module docstring); for a non-amortizing liability (a card, or a custom liability
type) four reach it -- the /savings cockpit tile (`savings/_cockpit_balance.html:72`), the grid header
(`grid/grid.html:22`, `grid/page.py:879`), the cash detail hero (`accounts/_cash_balance_hero.html:27`;
`account_resolver.serves_cash_detail` serves Credit Card) and the dashboard hero (`dashboard/_pulse_balance.html:27`;
`resolve_grid_account` may pick any non-loan kind). The grid renders raw HELD balances (no liability handling
in `templates/grid/` or `routes/grid/`), so a card's grid, cash page and dashboard are HELD surfaces (R-CC14);
the /savings tile becomes OWED at CC-5-5c (R-CC47). The create form (`accounts/form.html:52-68`) has a type
`<select>` and one "Opening Balance" box with help "The account's real-world balance."; no script reads the
form (`app/static/js/`: no `account_type_id` / `anchor_balance` reader).

#### Question 1 -- **R-CC57** (as asked, verbatim)

> CC-5-5b, the balance editor for a card: under your ruling R-CC52 the editor asks for the amount OWED. But a card's grid, its cash page, and the dashboard (when pointed at the card) show the HELD figure: a card owing $1,000 reads -$1,000 there, as your ruling R-CC14 set. What should the editor opened by clicking that -$1,000 show?

PICKED: **"Editor owed, screens keep sign (Recommended)"**:

> Clicking the grid's -$1,000 opens an editor labelled 'Amount owed', pre-filled 1,000.00. Typing 1,200 stores -1,200, and the grid then reads -$1,200 alongside its rows. The preview reads 'Your records $1,000.00 / You entered $1,200.00 / Difference $200.00: spend Shekel has not recorded', and the confirmation says '$1,200.00 owed'. Each screen keeps one sign and every balance editor speaks owed, which is R-CC52 as written. The reconcile question after saving stays the cash page's own: 'Your balance for Sep 22 is -$1,200.00'.

REFUSED:

- "Editor's figure owed too": "Also show the editor's own resting figure as owed on those screens: the grid header reads 'Visa owes $1,000', while the grid's balance rows below keep -$1,000 (R-CC14). One screen then shows two signs for the same card."
- "Editor speaks screen's sign": "The editor asks owed when opened from /savings, and asks for the held figure when opened from the grid, the cash page or the dashboard. The same typed 1,200 would mean owing $1,200 on one screen and a $1,200 CREDIT on another: a per-screen rule that invites a wrong-sign entry, which R-CC52 exists to stop."

Rejected before asking (in the chat text): the card's grid, cash page and dashboard speaking owed (re-opens R-CC47's refused "B").

#### Question 2 -- **R-CC58** (as asked, verbatim)

> CC-5-5b, the create form: it has a type dropdown and one 'Opening Balance' box whose help says only 'The account's real-world balance'. That is how your Mortgage came to be typed positive. After 5b the app stores the held sign by the TYPE you pick, whatever the label says: typing 5,000 on a car loan stores -5,000 and reads $5,000 owed. How should the box ask?

PICKED: **"Label switches with type (Recommended)"**:

> Picking a loan or card type relabels the box 'Amount owed', with help 'What you owe today. A card holding a credit: a negative number.' Picking any other type shows 'Opening balance'. A small new script does the switching; with scripts off, the box shows the second option's text. The question is asked in the words of the type you picked, right at the box.

(Preview as shown: Credit Card -> "Amount owed" / "What you owe today. A card holding a credit: a negative number."; Checking -> "Opening balance" / "What the account holds today.")

REFUSED: "Help text names both": "The box stays 'Opening Balance', and its help reads: 'What the account holds today. For a loan or credit card, enter the amount you OWE; a card holding a credit is a negative number.' No new script. It is always visible, but the owner has to read the help to learn which way a card's figure goes." -- NOTE: this text is ALSO the picked option's no-script fallback ("with scripts off, the box shows the second option's text").

Rejected before asking (in the chat text): a two-step create (type first, then the balance page) -- a flow change.

### Block 9 -- CC-5-5b's adversarial review, two developer answers (asked by lane credit-extra [a6edea] ~20:5x 2026-09-22 EDT in ONE AskUserQuestion). Ruling ids **R-CC60** (Question 1; AMENDS R-CC57) and **R-CC61** (Question 2), granted by the coordinator ~21:0x 2026-09-22 (R-CC59 went to CC-5-4a-3). Next free card R-CC62 / CC-364

Review: fresh code-reviewer, FIX-THEN-SHIP, no Critical/High; M1 and L2 below needed the developer; L1, L3-L8 fixed by the lane.

#### Question 1 (M1: R-CC57's confirmation clause) -- **R-CC60** -- as asked, verbatim

> CC-5-5b, the save confirmation on a card: your ruling R-CC57 picked text saying that after typing 1,200 on a card owing $1,000 'the confirmation says $1,200.00 owed'. That text was wrong. The confirmation toast only appears when a save changes nothing visible (finding N-204, e.g. re-recording the same balance or back-dating one); here the card's balance cell visibly moves from -$1,000 to -$1,200, so no toast appears. Whenever the toast does appear on a card, it says '$1,000.00 owed'. What should a card save confirm with?

PICKED: **"Keep the N-204 rule (Recommended)"**:

> As built. A save that visibly changes the cell confirms by the cell itself: typing 1,200 on a card owing $1,000 turns the grid's -$1,000 into -$1,200. The toast appears only when nothing visible moved, and on a card it names the figure owed ('$1,000.00 owed'). Every account confirms the same way, and the 'Amount owed' label is what connects the typed 1,200 to the grid's -$1,200.

REFUSED: "Every card save toasts owed": "A card save ALWAYS shows a toast in owed words: typing 1,200 shows '$1,200.00 owed' beside the cell's -$1,200, a bridge between the two signs. Assets keep the N-204 rule, so cards and assets confirm differently, and a card save with outstanding purchases shows both the toast and the reconcile question. This re-opens N-204's design for liabilities."

EFFECT: AMENDS R-CC57 -- its clause "and the confirmation says '$1,200.00 owed'" is withdrawn (the lane's option text invented it); R-CC57 takes an "AMENDED 2026-09-22 BY R-CC60" head at the tick.

#### Question 2 (L2: the sign race) -- **R-CC61** -- as asked, verbatim

> CC-5-5b, a sign race: the app decides whether a balance box means 'amount owed' (a card) or 'balance' (an asset) when you press Save, not when the box opened. Measured: a new $0 Checking account's editor is open; in another tab it is re-typed to Credit Card (allowed while it has no postings); you type 2,500 and save. It stores -$2,500, a card owing $2,500, where the box you typed into asked for an asset's balance. Before 5b it stored +$2,500, $5,000 apart. It needs a deliberate second-tab re-type of an account with no postings, and the cell shows the result at once. How should it be handled?

PICKED: **"Refuse the stale save (Recommended)"**:

> Fail closed. The balance editor and the books-opening card each carry the meaning they were opened with. A save whose account switched between asset and liability since then is refused, nothing is stored, and the box re-opens with the new meaning: 'This account's type changed while you were editing; the box now asks for the amount owed. Check the figure and save again.' The race case stores nothing instead of -$2,500. The create form is not guarded: its type is picked in the same form, and only an in-place category edit of a custom type in another tab could race it; that is documented instead.

REFUSED: "Document it, no guard": "Record it beside the existing N-199 race (a form opened on a cash account and saved after it became a loan) as an accepted residual. The race case stores -$2,500, and the owner sees the grid read -$2,500 and must correct it. No new code."

Measured by the reviewer (probe `scratchpad/probe/test_probe_retype_race.py`): $0.00 Checking, editor open (no "Amount owed"), re-typed to Credit Card (302), Save 2,500.00 -> stored held -2,500.00; base aa29d977 stores +2,500.00.

### Block 10 -- the box R-CC61's refusal re-opens (asked by lane credit-extra [a6edea] ~22:1x 2026-09-22 EDT after the re-review of ef4f6782, finding L-a). Ruling **R-CC62** (granted by the coordinator ~22:2x 2026-09-22; refines R-CC61). Next free card R-CC63 / CC-364

Re-review of `ef4f6782`: SHIP, no Critical/High/Medium; 8 Low. L-a (measured): after the stale refusal the re-rendered editor held `{'asks_owed': 'true', 'anchor_balance': '2500.00'}` in an autofocused box; submitting it unchanged returned 200 and stored -2,500.00.

Question as asked (verbatim):

> CC-5-5b, after your R-CC61 refusal: the balance editor refuses a save whose account switched between asset and liability since it opened, and re-opens under the new meaning. As built, the re-opened box still holds the figure you typed. Measured: a $0 Checking editor, the account re-typed to a card, 2,500.00 typed. The save is refused, the box re-opens as 'Amount owed 2500.00' (focused), and pressing Enter stores a card owing $2,500, the same figure the guard just refused. What should the re-opened box hold?

PICKED: **"Re-open as the editor opens (Recommended)"**:

> The box re-opens exactly as a fresh click would: pre-filled with the account's standing balance in the new words (here 0.00 owed), with the refusal message beside it. Enter alone re-asserts the standing figure, which changes nothing. To record $2,500 owed you type 2,500 again under the 'Amount owed' label. This is what the books-opening card already does: its refusal re-shows the stored figure.

REFUSED:

- "Echo what was typed": "As built. The box keeps 2500.00 under the new 'Amount owed' label with the red message. Your typing is not lost, but one Enter stores the figure typed under the old meaning (-$2,500 here), which the refusal existed to stop."
- "Re-open empty": "The box re-opens empty under the new words with the message; nothing can be saved until a figure is typed under the label that is now showing. Enter alone is refused as a missing balance."

EFFECT: refines R-CC61 (its "the box re-opens with the new meaning" is now: re-opens as `anchor_form` would, the standing balance crossed into the new words).

### Block 11 -- CC-5-5c's first questions (asked by lane credit-extra [a6edea] ~23:5x 2026-09-22 EDT in ONE AskUserQuestion, after the 5c trace on 1918dab7). Ids granted by the coordinator shekel-d1 ~00:1x 2026-09-23: **R-CC67** (Q2), **R-CC68** (Q3), **R-CC69** (Q1, REFUSED, and the direction given instead: DEBT GOALS). Next free card R-CC74 / CC-364

Trace facts behind them: `savings_dashboard_service/_goals._goal_account_balance` hands the backing
account's `AccountProjection.current_balance` to `percent_complete` / `calculate_trajectory`;
`routes/savings.py` `_goal_form_context` offers `account_service.list_active_accounts` (debts
included); the goal create route checks ownership + active only. The archived drawer
(`_data._load_archived_accounts`, `savings/dashboard.html` "Last Balance") reads
`cash_ledger.resolve_anchor(acct).balance`. The footer (`savings/_cockpit.html:298-300`) renders
`debt_summary.revolving_debt` as "excludes $X revolving". Census of the 17:06 dump (clone
`shekel_cc55b`): ONE goal (Emergency Fund, account 10 Money Market); no archived debt; no non-loan
liability.

#### Question 1 (CC-360, a goal on a debt) -- **R-CC69** -- REFUSED (the developer's own words; re-opened as Block 12)

> CC-5-5c flips a set-up loan's stored balance to negative when owed, like a card (your ruling R-CC47). Savings goals: the goal form offers every account, debts included, and a goal counts its account's balance as 'saved'. Example: a goal 'Van Loan payoff', target $20,000, on your Van Loan (owes $14,745.51). Today it reads $14,745.51 saved (73.73%). Your one real goal (Emergency Fund, on the Money Market) is unaffected by every option. What should a goal on a debt do?

Options offered: "Refuse a debt (Recommended)" (a goal backed only by a non-debt account; form lists
those; save on a card/loan refused; an account backing a goal cannot be re-typed to a debt); "Count
the amount owed" (today's reading, $14,745.51, 73.73%; paying $531.94 moves it back to $14,213.57,
71.07%); "Count the balance as held" (-$14,745.51, 0%, $34,745.51 still needed).

ANSWER (free text, verbatim): "I think a user could have a goal to get debt under a certain
milestone so for the Van Loan a goal of $10,000. The debt goals would just need to be less than the
current amount owed for it to make since and the percentages would need to be fixed for them."

#### Question 2 (CC-362's card half, the archived list) -- **R-CC67** -- PICKED "What it owes today (Recommended)"

> The /savings 'Archived Accounts' list shows one figure per account, 'Last Balance'. Your ruling R-CC53 already made an archived loan show what it owes from its schedule (Mortgage $176,719.77, not its typed $178,103.41). What should an archived credit card show? Example: a Visa last typed as owing $1,000.00 on Aug 1, then $200.00 of purchases recorded before it was archived, so it owes $1,200.00. Archived savings/checking accounts are unchanged in every option (what archiving means for them is owned by step X-f4). Production has no archived debt, so $0.00 on your screens today.

PICKED text: "Every archived debt (card, loan, other liability) shows what it owes today from the same
calculation as a live /savings tile: the Visa reads $1,200.00, the loans as R-CC53 ruled. For a debt
the figure is captioned 'Owed' instead of 'Last Balance'."

REFUSED: "Owed from its last typed figure" (the Visa reads $1,000.00, missing the $200.00; the list
mixes 'last typed' and 'today'); "Last typed figure, as stored" (-$1,000.00 beside positive loans:
two signs in one list).

#### Question 3 (CC-361, the footer label) -- **R-CC68** -- PICKED "'with no payoff date' (Recommended)"

> The /savings footer reads 'Loans paid off <month> · excludes $X revolving', where X is each debt with no payoff date, owed and floored at zero, then summed (your ruling R-CC49). But X also counts loans with no terms entered and custom liabilities, which do not revolve. Example: a Visa owing $1,000, an Amex holding a $50 credit, an auto loan with no terms owing $5,000, and a family loan (custom liability) owing $2,000 give X = $8,000. Production has none of these, so the footer is unchanged there. How should it read?

PICKED text: "'excludes $8,000 with no payoff date'. One figure, and the words say exactly what it
counts: the debt that the payoff date leaves out."

REFUSED: "Two figures by kind" ('excludes $1,000 revolving · $7,000 without loan terms'; a second
sum and a card-versus-other rule); "'card and other debt'" (names the kinds, not why they are out).

### Block 12 -- DEBT GOALS (CC-360 re-opened on the developer's refusal of Block 11 Q1; asked by lane credit-extra [a6edea] ~00:0x 2026-09-23 EDT in ONE AskUserQuestion). Ids granted by the coordinator ~00:1x 2026-09-23: **R-CC70** (Q1), **R-CC71** (Q2), **R-CC72** (Q3), **R-CC73** (Q4). SEQUENCING (the coordinator's calls): the debt goals are leaf **CC-5-5d**, built AFTER 5c, its OWN PR and OWN release after 5b+5c (its migration re-parented at merge); CC-360's owner moves to CC-5-5d at the 5b+5c tick; 5c leaves `_goals.py` and the goal routes untouched

Measured for the worked dollars (read-only probe on `shekel_cc55b`, HEAD 1918dab7, as_of
2026-09-22): Van Loan (account 8) owes 14,745.51 today; the seam's PAST reads 32,402.45 on
2026-04-06 (= `loan_params.original_principal`; its May true-up corrected the books later),
17,020.47 on 2026-06-01, 15,205.63 on 2026-08-01; FORWARD 12,883.20 on 2027-01-01, 10,023.08 on
2027-07-01, 9,538.48 on 2027-08-01, 0.00 from 2029-03-01. `savings_goals` CHECK
`ck_savings_goals_positive_target` is `target_amount > 0`.

Question stems carried the developer's own premise: "a goal on a debt sets a milestone to get UNDER
(Van Loan: owe less than $10,000; it owes $14,745.51 today). The target must be below what it owes
when saved, and a debt goal is a fixed amount only (no 'months of salary' mode)."

#### Q1 (the percentage) -- **R-CC70** -- PICKED "From owed when set (Recommended)"

> Progress runs from what it owed when the goal was set to the target: 0.00% today, 39.24% on Jan 1 2027 ($1,862.31 of the $4,745.51 paid down), 100% at $10,000. Works the same for a loan, a card or any other debt.

REFUSED: "From the original loan amount" (78.82% today, 87.13% Jan 1 2027; loans with terms only);
"Target divided by owed" (67.82% today, 77.62% Jan 1 2027; two-thirds done before any payment).

#### Q2 (the start figure) -- **R-CC71** -- PICKED "Re-read from the books" (the lane recommended "Saved with the goal")

> No migration: the start is re-read each time as what the books now say it owed that day, so the correction moves it to $15,000.00 and the percentage shifts with no payment made. A debt whose early history is missing reads its opening: the Van Loan on Apr 6 2026 reads $32,402.45, because its May true-up corrected the books later.

(Stem: "a goal set Aug 1 on a debt the books then showed owing $15,205.63; later you back-date a
$205.63 correction to July, so the books now say it owed $15,000.00 on Aug 1.")

REFUSED: "Saved with the goal (Recommended)": "The goal stores what the debt owed at the moment it
was saved as a debt goal ($15,205.63 here), and a later back-dated correction does not move it. A
new stored figure needs a database migration, so this work takes its own place in the release
order."

#### Q3 ($0 payoff target) -- **R-CC72** -- PICKED "Allow $0 for debts (Recommended)"

> A debt goal may target $0.00 ('Van Loan paid off by Dec 2028'); a savings goal still needs a target above $0. The database check changes to allow $0 and the app refuses $0 on a savings goal, in the same migration as the start figure (or a migration of its own).

REFUSED: "Milestones above $0 only" (the smallest debt target $0.01; payoff stays the loan page's).
EFFECT: a MIGRATION (the CHECK only; Q2 added no column).

#### Q4 (pace) -- **R-CC73** -- PICKED "Projected date only (Recommended)"

> The card says when the debt is projected to fall below the target, from the same forward figures the loan page uses (Van Loan: $10,023.08 on Jul 1 2027, $9,538.48 on Aug 1 2027, so July 2027), and whether that is on pace for the goal's date. No 'required per period' figure on a debt goal.

REFUSED: "Plus a simple per-period amount" (ignores interest; understates a loan); "Plus an
interest-aware amount" (the payoff calculator's math at a milestone; the largest build).

OPEN for the debt-goal leaf (not asked): "when set" is the goal's `created_at` day, so an EDIT that
moves a goal between a savings account and a debt would make its start the books' figure on a day
the goal was not a debt goal -- the leaf decides (and asks) whether that crossing is refused or
re-anchors.

### Block 13 -- CC-5-5c's rule-5 confirmation and CC-365's owner (asked by lane credit-extra [a6edea] (05:16-06:57, exact time unrecorded) 2026-09-23 EDT in ONE AskUserQuestion, after triaging the 45 failures of the full suite on the uncommitted 5c tree; triage record `credit_card-2026-09-23/cc55c/triage_5c.md`). CC-365 GRANTED by the coordinator shekel-d1 (05:16-06:57, exact time unrecorded) 2026-09-23. Q1 is a rule-5 CONFIRMATION (no ruling id, the shape of CC-5-4a-2's Confirmations 8 / 8a); Q2 is ruling **R-CC74** (granted by the coordinator (05:16-06:57, exact time unrecorded) 2026-09-23; CC-365 records the defect it fixes inside 5c). Next free card R-CC77 / CC-367 (R-CC75/76 went to CC-5-4a-4)

#### Q1 (rule 5: the four test-edit classes) -- CONFIRMED, ALL FOUR (multi-select; the lane recommended all four)

Stem: "None is a defect in the app: I checked every production reader of a loan's balance. ... In every
edit the hand-computed dollars stay the same; only the sign the test reads changes."

- **Loan balance read (26)**: "26 tests expect the balance seam to say a $200,000 mortgage is
  200,000.00. It now says -200,000.00 (the account holds -200,000 and owes 200,000). Edit: assert
  owed(balance) == 200,000.00, with the same dollars."
- **Test checkers (10)**: "10 checkers inside the tests read the seam as owed. The interest checker
  started from -100,000 and found $0 interest instead of $500, and the cross-page check compared the
  /savings tile (-200,000) with the trend band (+200,000). Edit: each checker converts once through
  owed(), or reads what the page shows. Its arithmetic is unchanged."
- **Old-sign inputs (8)**: "8 tests enter a debt in the old sign. A card 'owing $3,000' entered as
  +3,000 is a $3,000 CREDIT under R-CC47, so net worth came out $8,000 instead of $2,000. Edit: enter
  -3,000, which is what the 5b door stores for '$3,000 owed'. The $2,000 assertion is unchanged."
- **Footer wording (1)**: "1 test checks that the /savings footer says 'revolving'. Your R-CC68
  changed it to 'excludes $X with no payoff date'. Edit: check for the new words."

#### Q2 (CC-365's owner) -- **R-CC74** -- PICKED "In 5c, all screens (Recommended)"

> The save answers with the display of the screen that opened the editor, using the same screen-to-display map Cancel uses, so there is one home for 'what this screen shows'. The dashboard, investment and cash pages also stop answering with the grid's cell. I measure each screen before and after. No release ever has the flash.

Stem: "when you save a card's balance from /savings, the tile shows the held figure (-$1,200) for one
round trip, then the page redraws it as $1,200 owed. Cause: a successful save always answers with the
budget grid's balance cell, whichever screen opened the editor, while Cancel already answers with
that screen's own display."

REFUSED: "In 5c, /savings only" (leaves a second spelling of 'what this screen shows'); "A later
leaf" (5b+5c ship the flash). EFFECT: CC-365's owner is CC-5-5c; `anchor._true_up_success_response`
renders through the one surface -> display mapping `_anchor_revert_url` names (grid unchanged).

### Block 14 -- R-CC74's SHAPE: ruling **R-CC77** (refines R-CC74; granted by the coordinator (05:16-06:57, exact time unrecorded) 2026-09-23, the R-CC62-refines-R-CC61 precedent). Asked by lane credit-extra [a6edea] (05:16-06:57, exact time unrecorded) 2026-09-23 EDT after tracing the five display endpoints. Next free card R-CC78 / CC-367

Stem: "Today five small GET pages each redraw one screen's balance for Cancel: grid cell, dashboard hero,
/savings tile, investment hero, cash hero. Three of them live in other screens' route files (savings,
dashboard, investment). Where should the shared 'draw this screen's balance' functions live? I rejected
two other shapes: calling the Cancel page itself from inside the save (it could answer 404 after the
save already committed), and having the browser fetch the Cancel page after saving (still a blank flash,
and it races the page refresh)."

**R-CC77** -- PICKED "Beside each screen (Recommended)":

> Each screen's route file keeps its own 'draw my balance' function, and its Cancel page and the save both call it. One table in the anchor editor's file lists the five screens, each with its Cancel page and its draw function. That replaces today's two copies of the screen list (a set of names plus an if-chain). New: the anchor file imports from three other screens' route files (no import loop today). If a save lands on an account archived mid-edit, the /savings and investment draws find no account and the save answers with an empty cell, since that screen no longer shows it.

REFUSED: "One shared module" (all five draw functions and the table in one new routes module; the cash
hero's figure pulled out of accounts/detail.py's whole-page context builder).

### Block 15 -- R-CC77's FALSE PREMISE corrected: the import loop. Ruling **R-CC78** (granted by the coordinator (05:16-06:57, exact time unrecorded) 2026-09-23; R-CC77 takes "AMENDED 2026-09-23 BY R-CC78" at the tick). Asked by lane credit-extra [a6edea] (05:16-06:57, exact time unrecorded) 2026-09-23 EDT after the build subagent STOPPED before editing. Next free card R-CC79 / CC-367

The R-CC77 option text said "the anchor file imports from three other screens' route files (no import loop
today)". True for savings / dashboard / investment; FALSE for the fourth, the cash hero's draw in
`app/routes/accounts/detail.py`: anchor.py -> detail.py:69 (outstanding) -> outstanding.py:48 (difference)
-> difference.py:56 (`from app.routes.accounts.anchor import LOAN_ANCHOR_REFUSAL`) -> anchor.py. Measured on a
scratch copy of app/: `ImportError: cannot import name 'LOAN_ANCHOR_REFUSAL' from partially initialized module
'app.routes.accounts.anchor'`; the unmodified copy imports clean; the loop is confirmed by grep (the three lines above).

**R-CC78** -- PICKED "Move the shared sentence (Recommended)":

> Move the loan-refusal sentence out of the anchor file into the small shared module that already holds these balance doors' other refusal wording (_door_meaning.py). The anchor file and the difference preview both import it from there, which removes the loop at its cause. It touches 4 more files (one import line, plus comments naming where the sentence lives). No behavior changes; the wording is identical.

REFUSED: "Import inside the function" (a lint-exempted in-function import: routes around the loop);
"Move the cash draw out" (a new module for the cash page's balance calculation; the draw would no longer sit
beside its screen as R-CC77 says).

Also settled by the lane (implementation, not a ruling): every draw takes the loaded, owned `account` and
builds its OWN read pass exactly as its GET does today (so each GET still builds one; the save's draw builds one
AFTER its write); the investment producer, which builds its own (`investment_dashboard_service/_orchestrator.py:120`,
a module `test_one_read_pass_per_render.py` names and pay_calendar:C11 owns closing), is NOT changed; the grid's
draw needs none.

### Block 16 -- the VACUITY CENSUS confirmation (rule 5; asked by lane credit-extra [a6edea] (05:16-06:57, exact time unrecorded) 2026-09-23 EDT after a read-only census of tests/ -- 73 sign-sensitive assertions in liability-touching tests reviewed; record in `credit_card-2026-09-23/cc55c/`). A rule-5 CONFIRMATION, no ruling id (Block 13 Q1's shape)

Stem: "found PASSING tests that the held sign broke without failing. These are more instances beyond the 45 you
approved. Which fixes do you approve? I recommend all three. The dollars in every assertion stay the same."
CONFIRMED ALL THREE (multi-select):
- **Re-sign 5 tests** (test_balance_at.py): the +200,000 input that made "the forward point owes MORE than today"
  vacuous (enter -200,000; pin today at 200,000 owed); the card oracle's retired `abs()` -> `owed()`; two unread
  +200,000 inputs -> -200,000; the terms-less Mortgage seeded +150,000 "owes 150,000" -> seed -150,000, assert it
  owes 150,000.
- **Fix false wording** (comments only): the retired-abs docstrings in the touched files, the held -100 called
  "overpaid", and the "one control per abs site" prose in test_net_worth_kernel.py and test_appreciation_projection.py.
- **Pin the loan's shown sign**: NEW tests in 5c's new screen-test file -- the loan page's "Balance owed" and a
  configured loan's /savings tile show $X owed with no minus sign (today only the digits are checked).

### Block 17 -- THE DOOR REPORTS WHAT IT DECIDED: ruling **R-CC79** (granted by the coordinator (05:16-06:57, exact time unrecorded) 2026-09-23); ledger row **CC-368** (owner `operator`) for the OTHER doors. Asked by lane credit-extra [a6edea] (05:16-06:57, exact time unrecorded) 2026-09-23 EDT. Next free card R-CC80 / CC-369

**17a (REFUSED).** The build of R-CC74/77/78 left a grid save reading the governing assertion twice after the write
(the response's `resolve_anchor` for the note / 'as of', and the grid draw's). Offered: "Accept it, noted in code
(Recommended)" and "Thread the record through". Josh REFUSED BOTH: "I feel like neither of these options are great.
What is the root cause solution? How would you design this from scratch?" (the premise both shared -- that the
response must learn the door's decision by reading the ledger -- was the defect).

**17b (the root cause, traced).** Per grid save today: the route reads `governing_anchor_on(account.id, display_today())`
BEFORE the write and OUTSIDE the owner's lock (anchor.py true_up); the door's stager reads `governing_anchor_on(submitted
day)` UNDER the lock (the R-EQ no-op test); the response reads `resolve_anchor(account)` after; the grid draw reads it
again = 4 reads; the door reports only COMMITTED / UNCHANGED (`AnchorTrueUpOutcome`, anchor_service.py:202), so
everything around it re-derives what it decided. `apply_anchor_true_up` has ONE caller (anchor.py true_up); the enum
is also the loan door's (loan_anchor_service.py:202-206, routes/loan/params.py:401, :508); `stage_anchor_true_up` also
serves account_service.py:344.

**R-CC79** -- PICKED "Inside 5c (Recommended)" to the question (stem, verbatim core): "the door returns a report with the record that
governed today before the write and the one that governs today after it, both read inside its lock. The page draws from
the report: the grid's draw takes the governing record as its input, and Cancel reads it and passes it in. Only Cancel
can ever show a loan, because the save door refuses loans, so the loan check lives in one place. Reads per grid save
drop to 3, each a different fact (the record for the submitted day, today's before, today's after), and the other
screens are unchanged. It also closes a race that I found by reading the code, not by reproducing it: the confirmation
note compares against a figure read outside the lock, so two tabs saving at once could show the wrong note."
Option text: "Build it now, as part of the same 5c commit that introduced the extra read. 5b+5c then ship with no
redundant read and without the race. The door's return value changes from a two-value outcome to a report; it has one
caller, the anchor save. Whether the loan and books-opening doors re-read the same way gets filed as a ledger row for
their owner."
REFUSED: "Its own leaf after 5c" (5c ships the extra read until then); "Every balance door, later" (one later leaf
for the cash, loan and books-opening doors).

**CC-368** (row text for the tick, owner `operator`, UNMEASURED): "Do the loan true-up door
(`loan_anchor_service.apply_loan_anchor_true_up`; `routes/loan/params.py` :401 / :508, which share
`AnchorTrueUpOutcome`) and the books-opening door re-read the governing assertion around their doors the way the cash
anchor save did -- a before-read OUTSIDE the owner's lock and re-reads after the write -- because each door reports only
COMMITTED / UNCHANGED? UNMEASURED. The known instance, measured by trace at credit_card:CC-5-5c: the grid anchor save
read the governing assertion 4 times per request (route pre-read outside the lock, the stager's locked read, the
response's post-read, the grid draw's); ruling R-CC79 made the cash door return a report of today's governing assertion
before and after the write, both read inside its lock (3 reads, each a distinct fact) -- the model for these doors."

**17c (MEASURED after the build, correcting the option text).** The option text said "4 reads per grid save" and
"drop to 3". A spy on `cash_ledger._facts._governing_row` measured the OLD code: a BACK-DATED grid save read the
governing assertion 4 times (`true_up` today, `stage_anchor_true_up` day, `_true_up_success_response`, `_grid_cell`); a
TODAY-dated save (the editor's prefill) read it 5 times -- the 5th `prompt_fragment` -> `reconcile_statement` ->
`governing_statement`. The response also read `cash_ledger.reconciled_through` (MAX(observed_on), the same fact's day)
after the commit, OUTSIDE the lock -- part of the race the option said it closed. First build: back-dated 3, today 4.
Finishing both (prompt_fragment takes the report's governing assertion; the prompt/note decision reads the report's day)
is inside R-CC79's own words ("the page draws from the report", "3 reads", "closes a race"); coordinator-cleared
reconcile.py :362-425 and loan_anchor_service.py :7-9 (comment). Josh to be TOLD the count was 5, not 4.

### Block 18 -- the review's M1: the stager reads once and reports. Ruling **R-CC85** (granted by the coordinator; R-CC80..R-CC84 went to the 4a-4 lane). Asked by lane credit-extra [a6edea] after the neutral review of the 5c diff, 2026-09-23 after 06:57. Next free card R-CC86 / CC-371

The review (no HIGH) found that on a TODAY-dated save the door's before-read (`cash_ledger.governing_anchor`, latest)
and the stager's `governing_anchor_on(id, day)` return the same row -- R-CC79's "3 reads, each a different fact" held
only for back-dated saves. The review's suggested fix (the door passes its read to the stager) would drop the
account-creation door's refusal (account_service.py:344 relies on the stager's own read to detect "an assertion already
governs this just-created account"), so it was offered as a non-recommended option.
**R-CC85** -- PICKED "Stager reads once, reports (Recommended)":

> The stager reads the latest record once. If that record is dated on or before the submitted day, it IS the one governing that day, so there is no second read; only a back-dated save, where the latest record is later than the day, reads the day's record separately. The stager hands the latest record back to the door as 'before'. Reads: today save 2, back-dated save 3, each a distinct fact. The account-creation door's call changes by one line, and its 'a balance already governs this new account' refusal stays, because the stager still reads.

REFUSED: "Door passes its read in" (adds a read to the account-creation door); "Restate the claim" (the refused
17a shape). Final suite on the tree BEFORE this change: 15322 passed / 0 failed, exit 0 (17:06), after merging dev
28fdfcc9 (merge 836c4ebf).

## CC-5-5d `8f8b056d`, ticked 2026-09-23 with the CC-5-5 container: the CC-5-5 entry with 5d's specification and the `steps.md` rows as they stood, the ledger rows closed, then Blocks 19-24 VERBATIM (the card lane's record, `rulings_cc5_5.md`)

CC-5-5d reaches dev in its OWN pull request and deploys in its OWN release (the coordinator's call with **R-CC69**..**R-CC73**); its migration `764461215480` sits on `2eabfa596ee0`. Its last leaf shipped, CC-5-5 ticks with it (5a `aa29d977`, 5b `3d9d0c1a`, 5c `6daa3048`, 5d `8f8b056d`). Filed at this tick: **R-CC91** (Block 21), **R-CC93** and **R-CC94** (Block 23), **R-CC95** (Block 24, granted by the coordinator after the block was written, whose header still reads "grant pending"); **R-CC87**, **R-CC88** and **R-CC90** (Blocks 19-20) were filed at the 5b + 5c tick (`79dea057`), and R-CC89 (Block 20's header) and R-CC92 (the gap between R-CC91 and R-CC93) are CC-5-4a-4's. **Block 22 is a rule-5 CONFIRMATION and takes no ruling id** (CC-5-4a-2's Confirmations 8 / 8a and Blocks 13 Q1 / 16 are the precedent): the developer confirmed all four re-expressed tests -- the `GoalProgress` field set gains `start_owed`, the KeyError contract re-pointed from the deleted `_current_balance_from_map` to `_tile.tile_balance_on`, the create schema loads a `$0.00` target with the savings-only refusal tested at the goal door, and the edit schema drops `is_active`. The build's three reports became **CC-373** (a deleted goal's name), **CC-374** ("configured loan" asked two ways) and **CC-375** (the fixed-target rule's two homes), owner `operator`. The commits are the record; the build logs, probes and reviews are `~/projects/shekel-handoffs/HANDOFF-credit-card-CC-5-5.md` and `credit_card-2026-09-23/cc55d/`.

### The CC-5-5 entry and 5d's specification as they stood, verbatim

- [ ] **CC-5-5** `fix(cards): one sign for every balance` -- `R-CC47` (re-scoping `R-CC41`; the
      sixth site `R-CC48`): every balance is what the account HOLDS, negative when owed, and owed is
      minus it; the DECOMPOSED parent, split 2026-09-22 by the developer (`R-CC50` as amended by
      `R-CC52`) into 5a (the groundwork), 5b (the liability doors ask owed) and 5c (the one-commit
      flip), and given 2026-09-23 5d (the debt goals the flip's goal question became,
      `R-CC69`..`R-CC73`); ticks with its last leaf. `$0.00` on production's net-worth figures (both
      liabilities are configured loans).
  - [x] **CC-5-5a** `aa29d977` -- `balance_at.owed(balance) = -balance`, R-CC29's one flip moved
        into the seam (`card_statement.owed` deleted); the /savings revolving footer is each
        non-loan liability's owed amount floored at zero, summed (`R-CC49`);
        `tests/manual/verify_liability_screens.py`, the screen-diff instrument (178 responses plus a
        `tree.json` app digest) that grades 5b and 5c; 178 screens byte-identical on production's
        shape.
  - [x] **CC-5-5b** `3d9d0c1a` -- every liability balance door asks OWED and stores held through
        `liability_sign.held_balance` (`owed()` moved out of the seam; `R-CC52`): the create form
        (`R-CC58`), the anchor editor on every surface (`R-CC57` as amended by `R-CC60`), the
        books-opening card; a stale form refused and re-opened as a fresh click (`R-CC61`,
        `R-CC62`); no stored row re-signed, a loan's anchor cell a link (`R-CC53`). Two commits,
        `ef4f6782` first; closed **CC-357**; suite 15186/0 at `ef4f6782`.
  - [x] **CC-5-5c** `6daa3048` -- ONE commit (`R-CC50`): the configured-loan arms report HELD
        through `liability_sign.owed`; net worth the plain sum; band, trend, subtotal (`R-CC48`),
        tiles, debt summary, loan readers and archived drawer (`R-CC67`) read `owed`; the footer's
        words (`R-CC68`); an anchor save answers its opener's display (`R-CC74`, `R-CC77`, `R-CC78`)
        from the door's report (`R-CC79`, `R-CC85`). Rule-5 re-signs confirmed; closed **CC-354**,
        **CC-361**, **CC-362**, **CC-365**; suite 15323/0.
- [ ] **CC-5-5d** `feat(goals): a goal on a debt is a milestone to get under` -- `R-CC69`..`R-CC73`.
      Asked at 5c what a savings goal on a debt means, the developer refused all three readings
      offered (`R-CC69`) and ruled a milestone to get UNDER: a fixed amount only (no income-relative
      'months of salary' mode), below what the debt owes when the goal is saved (the Van Loan: under
      `$10,000` while it owes `$14,745.51`). Progress runs from what it owed when the goal was set
      down to the target (`R-CC70`: `0.00%` today, `39.24%` on 2027-01-01 with `$1,862.31` of
      `$4,745.51` paid down, `100%` at `$10,000`); that start is RE-READ from the books each time,
      what they now say it owed on the goal's set day (its `created_at`), never stored (`R-CC71`: a
      back-dated correction moves it; a debt whose early history is missing reads its opening). A
      debt goal may target `$0.00` while a savings goal still needs more:
      `ck_savings_goals_positive_target` (`target_amount > 0`) relaxed by an Alembic migration, the
      app refusing `$0.00` on a savings goal (`R-CC72`). The card shows when the debt is projected
      to fall below the target, from the forward figures the loan page reads, and whether that is on
      pace for the goal's date, with no required-per-period figure (`R-CC73`: July 2027) and no
      manual per-period field (`R-CC90`). The readers: `routes/savings.py` `_goal_form_context`
      (every active account, debts included),
      `savings_dashboard_service/_goals._goal_account_balance` (since 5c a debt's HELD balance, so
      `percent_complete` holds a goal on a loan at `0%`) and
      `savings_goal_service.calculate_trajectory`. A debt goal on a card or any other non-loan debt
      reads its tile's figure for its start, its current figure and the target check (`R-CC88`).
      **CC-371** (an archived debt's drawer figure is the day's balance while a live non-loan tile
      shows its current period's end) is NOT yet ruled: `R-CC67` says what it owes today, and the
      question is with the developer, asked by the CC-5-5d lane 2026-09-23. The goal move is
      answered by `R-CC87`: refused, and re-typing an account across savings and debt is refused
      while a goal is on it. Its OWN PR and OWN release after 5b and 5c's (the coordinator's call),
      the migration re-parented at merge. `$0.00` on production: its one goal is on the Money
      Market. Closes **CC-360**, **CC-371**.

### The `steps.md` rows as they stood, verbatim

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| credit_card | CC-5-5d | -- | Make a goal on a DEBT a milestone to get UNDER (**R-CC69**): a fixed target below what it owes when saved, `$0.00` allowed by a CHECK migration (**R-CC72**), progress from what it owed when set, re-read from the books (**R-CC70**, **R-CC71**), a projected date only (**R-CC73**); own PR and release, an edit moving a goal across savings and debt refused (**R-CC87**). Closes **CC-360**, **CC-371**. | #4 | -- | NOW / credit_card:CC-5-5c (shipped; the coordinator's call with R-CC69..R-CC73: the debt goals follow the flip that makes a debt's balance held) |
| credit_card | CC-5-5 | -- | The DECOMPOSED parent of ONE SIGN FOR EVERY BALANCE (**R-CC47**, re-scoping **R-CC41**; the sixth site **R-CC48**), split 2026-09-22 by the developer (**R-CC50** as amended by **R-CC52**) into the groundwork (5a), the liability doors that ask for the amount owed (5b) and the one-commit flip (5c), and given 2026-09-23 the debt goals (5d, **R-CC69**..**R-CC73**); ticks with its last leaf. | container | -- | ticks with #4 |

### Ledger rows CLOSED at this tick

**CC-360** CLOSED at `credit_card:CC-5-5d` `8f8b056d`: a goal on a liability is a DEBT goal (**R-CC69**..**R-CC73**). `savings_dashboard_service/_goals._compute_goal_progress` routes it to `_build_debt_goal_datum` (`_goals.py:571` at `6d7f45ac`, the dev merge this tick sits on), whose percentage runs from its start down to its target, `percent_complete(start_owed - owed_now, start_owed - resolved_target)` (`:513`), the start recorded for a non-loan debt (**R-CC91**) or re-read for a loan (**R-CC71**); `_goal_account_balance` is gone (no match in `app/` or `tests/`), and `routes/savings.py` `_goal_form_accounts`, on an edit, offers only what **R-CC87**'s move rule admits. Pinned by `tests/test_services/test_debt_goals.py::TestALoanGoalReReadsItsStart::test_a_loan_goal_runs_from_what_it_owed_the_day_it_was_set` and `tests/test_services/test_debt_goals.py::TestACardGoalKeepsTheStartItRecorded::test_the_card_worked_example_reads_zero_then_57_14`.

**CC-371** CLOSED at `credit_card:CC-5-5d` `8f8b056d` under **R-CC95**: the archived list reads `owed(tile_balance_on(acct, ctx, ctx.as_of))` (`_data.py:235`), the one rule the live tile reads too (`_projections.py:179` for a loan, `:294` otherwise; `_tile.py:69`) (save a terms row left on a non-amortizing type, where the archived list's loan test and the live tile's part: **CC-374**), and `_current_balance_from_map` survives only in two docstrings (`_tile.py:109` and `tests/test_services/test_savings_dashboard_service.py:5957`). Pinned by `tests/test_services/test_debt_goals.py::TestTheTileRuleHasOneHome::test_an_archived_card_shows_what_its_live_tile_showed`.

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| credit_card | CC-360 (`credit_card:CC-5-5a`'s build 2026-09-22, the lane's handoff s.0.5; filed by the coordinator at the CC-5-5a tick) | -- | **A SAVINGS GOAL BACKED BY A DEBT READS THE DEBT'S HELD, NEGATIVE BALANCE AS SAVED, SO ITS PROGRESS SITS AT `0%` HOWEVER MUCH IS PAID DOWN.** `routes/savings.py` `_goal_form_context` offers every active account (`account_service.list_active_accounts`) as a goal's account, a loan or card included, and `savings_dashboard_service/_goals._goal_account_balance` hands the backing account's `AccountProjection.current_balance` to the goal's progress (`percent_complete`, clamped to `[0, 100]`). Since `credit_card:CC-5-5c` (`6daa3048`, **R-CC47**) that balance is HELD for every account, a configured loan included (the Mortgage `-176,719.77`), where until then a configured loan's OWED figure counted as saved. Asked at 5c, the developer refused every reading offered (**R-CC69**) and ruled DEBT GOALS instead: a milestone to get under, progress from what the debt owed when the goal was set, re-read from the books, a `$0.00` target allowed, a projected date only (**R-CC70**..**R-CC73**) | `$0.00` on production (its one goal is on the Money Market); reachable by any goal set on a debt | **OPEN, born with an owner** (coordinator 2026-09-22, rule 1, at CC-5-5a's tick; RE-OWNED 2026-09-23 at the 5b + 5c tick to the leaf the coordinator minted for **R-CC69**..**R-CC73**, 5c having left `_goals.py` and the goal routes untouched) | CC-5-5d |
| credit_card | CC-371 (the coordinator's trace of `credit_card:CC-5-5c` 2026-09-23, then measured) | -- | **AN ARCHIVED DEBT'S 'OWED' IS THE DAY'S BALANCE WHILE A LIVE NON-LOAN TILE SHOWS ITS CURRENT PERIOD'S END, SO R-CC67'S "SAME CALCULATION AS A LIVE /SAVINGS TILE" HOLDS FOR LOANS ONLY.** **R-CC67** has an archived debt show what it owes today "from the same calculation as a live /savings tile", and `savings_dashboard_service/_data.py:224` (`_load_archived_accounts`, `6daa3048`) reads `owed(balance_at.balance_at(acct, ctx, ctx.as_of))`, the DAY. A live LOAN tile reads the same day (`_projections.py:222`, `_compute_loan_account`), but with a current period a live NON-loan tile reads `balances[ctx.current_period.period_id]` (`_projections.py:173`, `_current_balance_from_map`), which `balance_at/_cash_fold.py:640` `period_balances` samples at each period's END date, the fold holding every still-Projected row at `max(its date, as_of + 1)` (`balance:R-G`, `_cash_fold.py:943`) -- so a card tile shows what it will owe at the period's end. The two diverge for an archived card or custom liability holding a still-Projected row the fold places on or before the current period's end (an overdue one lands tomorrow); archiving cancels no row (`routes/accounts/crud.py:523` `archive_account` sets `is_active` alone, after refusing an account an active recurring transfer names) | `$0.00` on production: no non-loan liability and no archived debt (the 2026-09-22 17:06 census). MEASURED on a synthetic fixture at `6daa3048` (a scratch probe, not a test in the tree): as_of 2026-02-15, period 02-13..02-26, a Credit Card anchored at -$1,000.00 with a projected $200.00 one-off due 02-23 holds -$1,200.00 at the period END, which its tile shows as $1,200.00, and -$1,000.00 by the day, so archived it shows 'Owed $1,000.00' | **OPEN, born with an owner** (granted at the 5b + 5c tick); NOT yet ruled (**R-CC67** says today; the question is with the developer, asked by the CC-5-5d lane 2026-09-23) | CC-5-5d |

### Block 19 -- CC-5-5d's first questions (asked by lane credit-extra [a6edea], fresh context, ~09:3x 2026-09-23 EDT (`date` read 09:36:52 just after the answer), in ONE AskUserQuestion after the 5d trace on 6daa3048). Ids GRANTED by the coordinator shekel-d1 (~09:4x 2026-09-23): **R-CC87** (Q1) and **R-CC88** (Q2). Next free card R-CC89 / CC-373. CC-371 (the archived list reads the DAY, a live non-loan tile the period END) and CC-372 (a savings goal on an ARCHIVED account reads $0.00 saved) were granted by the coordinator the same morning, ride the 5b+5c tick, and CC-371's owner is CC-5-5d

Trace facts behind them (6daa3048): a non-loan /savings tile reads `balances[current_period.period_id]`
(`_projections._current_balance_from_map`), which `_cash_fold.period_balances` samples at the period's END date; a
configured loan's tile reads `balance_at(acct, ctx, as_of)` (the day). The archived list (R-CC67) reads
`owed(balance_at(acct, ctx, ctx.as_of))` (the day). The goal update route checks ownership but not `is_active` on an
account change (create checks both). Account re-type across Asset/Liability is allowed while the account has no
posted history (`account_validation._validate_account_type_change`); a custom type's category edit likewise
(`_validate_account_type_boundary_edit`). Archive (`crud.archive_account`) leaves projected rows and goals active.

#### Q1 (an edit that moves a goal) -- **R-CC87** -- PICKED "Refuse the move (Recommended)"

> Debt goals (your rulings R-CC69..R-CC73) measure progress from what the debt owed on the day the goal was set. What should happen when an EDIT changes a goal's account, moving it between a savings account and a debt, or from one debt to another? (Re-typing an account between savings and debt while a goal is on it is the same question. That is only possible for an account with nothing posted yet.) Worked example on your Van Loan, figures measured Sep 22: it owes $14,745.51 today, read $32,402.45 on Apr 6 before its May true-up, and is projected to owe $12,883.20 on Jan 1 2027. A savings goal created Apr 6 with a $10,000 target is edited today to point at the Van Loan. Your one real goal (Emergency Fund on the Money Market) is unaffected by every option.

PICKED text: "A goal can move only from one savings account to another. Moving between savings and a debt, or to a
different debt, is refused with 'create a new goal instead'. Re-typing an account between savings and debt is also
refused while a goal is on it. A new 'Van Loan under $10,000' goal made today reads 0.00% now and 39.24% on Jan 1
2027. A goal's start is always the day it was created, so no new date is stored."

REFUSED: "Restart on the move" (the day it moved onto the debt becomes its start, saved as a new date on every goal
in the same database change as the $0 target; same 0.00% / 39.24%; re-typing still refused); "Allow, start when
created" (the start is the debt's figure on the CREATION day: the Van Loan's Apr 6 opening $32,402.45, reading
78.82% the moment it is pointed at the loan and 87.13% on Jan 1 2027).

#### Q2 (which figure a card goal measures) -- **R-CC88** -- PICKED "Same as its tile (Recommended)"

> On /savings, a credit card's tile (or any other non-loan debt's tile) shows what it will owe at the END of the current pay period, counting purchases and payments already planned later in the period. A loan's tile shows what it owes today. Which figure should a debt goal on a card use, for its start, its current figure and the 'target must be below what it owes' check? Hypothetical example (production has no card): a Visa owes $1,000.00 today, and a $200.00 purchase is planned for later this pay period, so its tile shows $1,200.00. A goal 'Visa under $500' is set today, and a $400.00 payment is planned for Oct 9, in the next pay period. Loans read the same under both options. (Taking the start from the day and the current figure from the tile was rejected: set today, it would start at $1,000.00 while owing $1,200.00, and a $1,100 target would be allowed above its own start.)

PICKED text: "The goal uses the card's tile figure. It starts at $1,200.00 and reads 0.00% today, and the target must
be under $1,200.00. From the first day of next period it reads $800.00 owed, which is 57.14%. The goal and the tile
above it always show the same figure, which is the rule savings goals already follow."

REFUSED: "What it owes that day" (starts at $1,000.00, target under $1,000.00 while the tile reads $1,200.00; 0% after
the purchase; 40.00% on Oct 9 after the payment).

CONSEQUENCE (the lane's, stated with the question's recommendation): ONE rule, "what the tile shows for day D" (a
configured loan: the day; any other account: the end of the pay period containing D, or D when no period contains
it), read by the live tile, the archived list (CC-371 resolved in 5d) and every debt-goal figure (start, current,
the save check). Applied WITHOUT a question because earlier rulings decide them (stated to Josh before the question):
a debt goal's TARGET edit must be below what it owes at the edit (Block 11's own words, "less than the current amount
owed") and never moves the start; a goal on an ARCHIVED debt reads what it owes (R-CC67), never $0.00 / "Goal met!";
debt goals show no "Recommended per paycheck" (R-CC73) and so no false "Past target date"; create and edit are ONE
door (the edit route skips create's active-account check today).

### Block 20 -- the manual per-period field on a debt goal (asked by lane credit-extra [a6edea] ~09:4x 2026-09-23 EDT (`date` read 09:44:23 just after the answer), ONE AskUserQuestion, after Block 19). Id GRANTED by the coordinator: **R-CC90** (R-CC89 went to the 4a-4 lane's Round 7 at 09:4x; this lane had pre-guessed R-CC89 and was corrected)

#### Q1 -- **R-CC90** -- PICKED "Not on debt goals (Recommended)"

> The goal form has an optional 'Manual Contribution per Period' field. Its help text reads 'Override the auto-calculated recommendation', and the savings goal card shows it as 'Manual target $X/period' beside 'Recommended per paycheck'. Your ruling R-CC73 gives a DEBT goal no per-period figure: it shows only the projected date it falls under the target, and whether that is on pace. What should that field do on a debt goal? Example: 'Van Loan under $10,000', with $500.00 typed into the field.

PICKED text: "The form hides the field when a debt is picked, and a save that carries it on a debt goal is refused.
The debt goal card shows no per-period figure at all, consistent with R-CC73. What moves a debt goal is the loan or
card's own planned payments, which the projected date already reads. Savings goals keep the field unchanged."

REFUSED: "Keep it as a note" (the field stays; the card shows 'Manual target $500.00/period' as a reminder nothing
reads; help text changes for debts).

### Block 21 -- the adversarial review's H1: a card goal's start (asked by lane credit-extra [a6edea] ~11:0x 2026-09-23 EDT (`date` read 11:05:59 just after the answer), ONE AskUserQuestion, after the neutral review of the 5d diff). Id GRANTED by the coordinator (11:0x): **R-CC91**. REFINES R-CC71 (for non-loan debts) and R-CC88

Review finding H1 (traced by the lane in `_goals._build_debt_goal_datum`; measured by the reviewer): a non-loan
debt goal's start is `tile_balance_on(set day)` = the END of the set day's pay period, RE-READ with today's books
(R-CC71), so every row dated later in that period -- a payment or purchase recorded after the goal was set --
lands inside the start. Loans are unaffected (their tile reads the day).

> A card goal's start behaves wrongly for card and other non-loan debts; loans are fine. A card's /savings tile shows what it will owe at the END of the pay period. Your R-CC71 re-reads a goal's start from the books every time, as that end-of-period figure for the day the goal was set, so anything recorded later in that same pay period lands inside the start. Example (hypothetical; production has no card): a Visa owes $1,000 today, a $200 purchase is planned later this period, so the tile shows $1,200. Goal 'Visa under $500' is set today. On day 5 of the same period you pay $300; next period you pay another $100. How should a card goal's start work?

PICKED "Record it for cards (Recommended)": "A card or other non-loan debt goal SAVES its start when created:
$1,200. The day-5 payment reads 42.86% ($300 of $700) and the next payment 57.14%. Loans keep re-reading their
start from the books (R-CC71), because a loan's tile reads the day itself, so a later back-dated correction still
moves a loan goal's start. Needs a saved start amount (same database change as the $0 target). Changing an
account between a card-style and a loan-style type is then refused while a goal is on it, so a saved start can
never land on a loan."

REFUSED: "Record it for every debt" (loans stored too; the Van Loan $205.63 correction would read as progress);
"Keep re-reading, say so" (0.00% after the day-5 payment, 25.00% after the next; a $300 day-5 purchase raises the
start to $1,500); "Read the day, not the tile" (start $1,000, 60.00% after the payment, the tile reads $900).

### Block 22 -- CC-5-5d's rule-5 CONFIRMATION (asked by lane credit-extra [a6edea] ~12:3x 2026-09-23 EDT (`date` read 12:34:05 just after the answer), ONE AskUserQuestion, multi-select; no ruling id, the shape of Block 13 Q1 / Block 16). CONFIRMED ALL FOUR

(1) `tests/test_services/test_savings_dashboard_service.py::TestIncomeRelativeGoalDashboard::test_dashboard_goal_data_includes_new_keys`
-- the pinned field set gains `start_owed` (read by the debt goal card). (2) `::TestTheDenseMapIsTotalAndSaysSo::
test_the_projection_read_raises_on_a_missing_current_period` -- re-pointed from the deleted `_current_balance_from_map`
to `_tile.tile_balance_on`, the same KeyError-on-a-missing-column contract. (3) `tests/test_schemas/test_validation.py::
TestSavingsGoalCreateSchema::test_zero_target_rejected` -- the create schema loads a $0 target (R-CC72); the
savings-only refusal is tested at the goal door. (4) `::TestSavingsGoalUpdateSchema::test_valid_partial_update` --
the edit schema drops `is_active` (review M1); the test asserts it is ignored.

### Block 23 -- the DELTA review's HIGH-1 and MEDIUM-1 (asked by lane credit-extra [a6edea] ~13:0x 2026-09-23 EDT, ONE AskUserQuestion, two questions). Ids GRANTED by the coordinator: **R-CC93** (Q1) and **R-CC94** (Q2); both file at 5d's tick with R-CC91

HIGH-1 (measured by the reviewer: route create then route loan setup, 0% loan, frozen 2026-03-20): a loan-TYPE
account with no terms reads as a card to the goal door, so a goal on it RECORDED its start; entering the terms made
it a configured loan read by the day, and the recorded start then read 40.00% with no payment -- R-CC91's "a saved
start can never land on a loan" broke through the loan-setup door (`routes/loan/params.py`).

#### Q1 -- **R-CC93** -- PICKED "No goal until terms (Recommended)"
> A second review found a hole in your R-CC91 ('a saved start can never land on a loan'). A loan-type account (e.g. an Auto Loan) can exist before its loan terms are entered. Until then it is treated like a card, so a goal on it RECORDS its start. Entering the terms later makes it a real loan read by the day, and the recorded start then sits against it. Measured: an Auto Loan typed as owing $20,000 with no terms; goal 'Auto under $15,000' set, start recorded $20,000; you then enter the terms, stating it owes $18,000 today. The goal reads (20,000 - 18,000) / (20,000 - 15,000) = 40.00% with no payment made. Production has no such account or goal. What should happen?

PICKED text: "A goal cannot be set on a loan-type account until its loan terms are entered ('Enter this loan's terms
before setting a goal on it'). After setup the goal is an ordinary loan goal: its start is re-read from the books,
$18,000, 0.00%. Then 'loan type' and 'real loan' mean the same thing for every account that carries a goal, so the
terms screen needs no new check."
REFUSED: "Block entering the terms" (refuse setup while a goal is on it); "Drop the start at setup" (a goal set a week
before setup reads 70.00% with no payment); "Keep it as built" (40.00%).

#### Q2 -- **R-CC94** -- PICKED "On the edit page (Recommended)"
> The goal refusals tell you to 'delete the goal first' (re-typing an account under a goal) or 'create a new goal instead' (moving a goal to another debt). But no screen has a button to delete a goal: the delete action exists, nothing links to it, and that was already true before this work. Since the review's fix, deleting is also the only way a goal ever stops. Should a goal get a delete button?

PICKED text: "The goal's edit page gets a 'Delete goal' button with a confirm prompt, posting to the existing delete
action (it deactivates the goal; the record is kept for the audit log). One small form, no new route."
REFUSED: "On each goal card"; "Both places"; "Not in this step" (a ledger row instead).

### Block 24 -- what an ARCHIVED card shows (CC-371's own ruling; asked by lane credit-extra [a6edea] ~14:0x 2026-09-23 EDT (`date` read 14:08:33 just after the answer) at the coordinator's word, after its review of the 5b+5c fix found R-CC88's filed text and CC-371 treating the archived list as ruled when Block 19 Q2 asked only about a debt goal's figure). ONE AskUserQuestion. Id REQUESTED from the coordinator (grant pending)

> Your R-CC67 says every archived debt 'shows what it owes today from the same calculation as a live /savings tile'. For a card with a purchase planned later in the current pay period those are two different figures, and I picked one without asking you. Example: an archived Visa owes $1,000.00 today, and a $200.00 purchase is still planned later this period. Its live tile (before archiving) showed $1,200.00, because a card tile reads the END of the pay period. The just-committed debt-goal work makes the archived list show 'Owed $1,200.00'. Before it, the list showed 'Owed $1,000.00'. Loans read the same either way. Production has no archived debt. What should an archived card show?

PICKED "Its tile's figure (Recommended)": "'Owed $1,200.00': the same rule the live tile and a debt goal use (R-CC88),
so a card shows one figure whether live, archived or behind a goal. This is what is committed (8f8b056d); nothing
changes."
REFUSED: "Today's owed" ("'Owed $1,000.00': what it owes on the day, ignoring the purchase still planned in the
period, as R-CC67's 'owes today' reads. The archived list then differs from the card's live tile by the planned
rows. Needs a follow-up commit to switch the list back.").
CONSEQUENCE: 8f8b056d stands as committed (no follow-up); R-CC67 takes an "AMENDED BY" head naming this ruling (its
"owes today" read by the tile's rule for a non-loan debt); CC-371 closes citing it.
