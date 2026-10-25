# The credit card from scratch, on the app as it stands

**STATUS: DESIGN, RULED 2026-09-18 (`credit_card:R-CC14`..`R-CC19`, and the three amendments of the
same day, `R-CC20`..`R-CC22`, recorded in 3.5 and 3.10) after two neutral adversarial reviews, which
broke a good deal of the first draft; section 8 records what each refuted.** Nothing here has been
built. The leaves of section 5 were minted as steps by the coordinator's re-mint of the arc, which
retired the 2026-07-19 leaves `CC0a`..`CC5b`; its open leaves and its rulings are cards in the
tracker, titled by their ids, and this document records how each came to be and quotes the option
the developer picked. Its companions are `from_scratch_architecture.md` (the movement model and the
level relation this design rides) and `pay_calendar_from_scratch.md`; the arc's own document is
`docs/plans/implementation_plan_credit_card.md`, and when a leaf may start is the board's answer,
never restated here. The session record, with the cold-start note each session left the next, is
`~/projects/shekel-handoffs/HANDOFF-credit-card-from-scratch-2026-09-18.md` (outside the
repository); the per-leaf trace that preceded it is `HANDOFF-credit-card-trace-2026-09-18.md` beside
it.

**What the developer asked** (2026-09-18, refusing the CC0a fork's premise):

> "Since so much has changed in the two months since I first planned the credit card arc I want to
> make sure I am following the best from scratch design to create the most robust, maintainable, and
> future proof app with the best user experience. How would you design and build this from scratch?
> I care more about doing it right than the amount of effort involved."

And, mid-turn: *"Be sure you account for the direction the code is headed in steps.md."*

**The arc's OUTCOME statement, in the developer's words (2026-09-18), which every fork after Q1
grades against**:

> "Right now the single most important row on the grid is the Projected End Balance which show my
> estimated cash on hand after every bill has been paid and all income has been received for that
> pay period. This is how I determine the health of my finances and whether I can afford a purchase
> or if I need to cut spending or transfer money from savings. I'm currently using my credit card to
> carry expenses if I need to move an expense from one paycheck to another. ... The money still
> comes out of checking. The only thing that changes is when the money comes out of checking."

**The answer in one paragraph.** A credit card is an ordinary account whose balance is
`opening + SUM(movements)`, exactly like Checking will be after the flip -- it needs NO balance
engine of its own, so it is a FLAG on the account type and not a projection kind. What makes it a
card is three things layered on top: (1) its MOVEMENTS are purchases that satisfy plan items on the
paycheck grid -- a bill paid by card is the bill's covering movement recorded ON THE CARD, and a
card swipe in an envelope is a purchase entry ON THE CARD -- the developer's ruling of 2026-09-18
(R-CC15), which asks the movement model for one thing: a movement is read on ITS OWN account, by the
fold and by every reader; (2) it has a STATEMENT CYCLE (a close day, a due day, a minimum rule, an
APR) from which the app DERIVES the statement balance, the payment you owe, whether grace held and
the finance charge, while the card's own statement closings enter as LEVEL observations the way a
bank's do, so what the app thinks and what Capital One says are compared, never plugged; (3) the
payment is a recurring TRANSFER checking -> card priced by the card, which puts the card's cash back
on checking's grid on the day it really leaves. Everything else -- the grid, the pay-period clock,
envelopes, the fold, the level relation, the four-verb import, the SimpleFIN feed -- the card
inherits. The `/grid` cheat's frozen history converts with `$0.00` movement on checking's CASH fold
by reading each payback as the movement it was, and its three live pairs are converted by the
owner's own act at the new door once his card exists. Sections 3.3, 3.10 and 3.4 hold the forks the
developer ruled, in that order; section 5 the re-minted leaves; section 8 what a neutral review
refuted in the first draft and what changed.

Read against: the movement model AS IT WILL STAND (`from_scratch_architecture.md` 3.1-3.4 and 4.5
leaves 3-5; `X-bi-4`, `X-bi-6` in the balance README), the flip's end state (`R-EB`, `X-f3c-5`: an
assertion stops resetting a plain account), the level relation as built
(`archive/x_bj_1a_as_built_2026-09-16.md`), the amount model (`R-FI`, `R-IY`), the one-definition
shape (`R-BAL20`, `app/services/one_off.py`), the books boundary (`d3b6f1c8a274`), the bank-import
rulings `R-GJ`, `R-GK`, `R-HP`..`R-HV`, and every open step in `steps.md` (2.1). `R-CC14` and
`R-CC15` are on dev since PR #399. The population was measured READ-ONLY on production 2026-09-18
(section 7; a dated measurement, re-run before it is relied on). Every code claim cites its
file:line on dev `1431c44a`, verified there (`cd242edc` moved none of the cited files); step RANKS
are quoted nowhere here, because they are recut with every tick and the board is their only home.

## 1. What a credit card is, in the app's own words

Plain words first. Today the developer budgets every bill and envelope on the checking grid. When he
pays one with the card he marks it **Credit**: the row stops counting against checking and the app
plants a "CC Payback" expense in the NEXT pay period as the reminder to pay the card. That is the
cheat. It is financially wrong in the five ways the 2026-07-19 plan named (the debt exists nowhere;
spending is attributed to "CC Payback" instead of its category; the payback lands one period later
while the real cash leaves on the card's due date up to six weeks later; interest and grace are
unrepresentable; cash back is manual) -- and since then the app has grown the parts that fix all
five without a card-specific engine.

A credit card, from scratch, is:

- **an account** (`budget.accounts`, type Credit Card, category Liability) with an OPENING (its
  balance the day its books open, read off a statement -- a constitutive fact, `R-GX`) and a balance
  that is `opening + SUM(movements)` at every date, folded by the one cash fold every non-loan
  account already rides (`balance_at/_cash_fold.py`; `_asset_fold.py:5-17` says a modelled asset is
  that fold plus a rate, and a card is that fold plus NOTHING);
- **whose movements are purchases** recorded on it: each one satisfies a plan item on the paycheck
  grid -- a bill's covering movement, or one purchase of an envelope -- and each clears on the
  CARD's statement, line by line, the grain Capital One speaks in;
- **whose cash comes back through checking** as a recurring transfer on the due day, priced by the
  card: the statement balance at the last close, less rewards credited since, or the minimum, or a
  fixed figure the owner states;
- **whose statement is DERIVED** from the cycle (close day, due day) over its movements, and
  **whose actual statement closings are OBSERVED** -- typed or carried by the feed -- as level rows,
  so the card cockpit shows "the app says `$1,234.56`, Capital One says `$1,240.06`, `$5.50`
  unexplained" and names the four remedies the checking reconcile already has (a missing purchase; a
  wrong opening; a wrong observation; an accepted unexplained line);
- **whose interest is a real row** on the card when grace fails, priced by the app's ONE interest
  producer from the card's APR history and daily balances (`R-CC8`, `recurrence:R16-d`), and
  **whose rewards accrue as a derived figure** with redemptions as real credit rows.

## 2. What the tree already gives the card, for free

| the card needs | the tree has | where |
|---|---|---|
| a balance at every date, past from records, future from the plan, kind-blind | the cash fold: ASSERTION / ACTUAL / PLANNED tiers, the R-G clamp, totality | `balance_at/_cash_fold.py:1-110`; a Credit Card classifies PLAIN (`ref_seeds.py:59`) and the grid's gate admits it (`account_resolver.py:90-91` `is_cash_flow_account`, which is only "not amortizing": it admits the IRAs and the savings accounts too, which is why 3.3 needs a predicate of its own) |
| "the records are the fact; a typed balance is a check" | the flip's end state for PLAIN (`X-f3c-5`), and until it ships, the reset the card shares with checking | `balance_at/_assertions.py`, `README` X-f3c-5 |
| a purchase as a first-class money row with its own day, figure, provenance and clearing link | the movement table and its covering-movement writer | `models/transaction_entry.py`; `status_seam/_covering.py` (X-bi-3a); `cash_ledger/_cash_leg.py:307` `movement_cash_leg` |
| the fold reading movements only | `X-bi-4` | `README` X-bi-4 |
| every plan item has exactly one definition, so a one-off card charge is a definition plus a placed row | `X-bi-7b` shipped | `app/services/one_off.py` |
| a derived amount is never stored; a row names the relation that prices it | `R-FI`; the resolver and the relation rules | `cash_ledger/_amount_source.py`, `_amount_rule.py:307-336` (`_is_loan_payment` reads `template.settings`) |
| the card's statement closing as an observation beside what the app computes | the level relation: `account_anchor_history` widened, bank rows naming their import, evidence-ranked, append-only releases | `x_bj_1a_as_built`; `X-bj-1b` (shipped, #404) chooses within a run; `X-bj-2` rides the flip |
| "no movement before the books" | the books boundary: a deferred constraint trigger on `settled_on` of BOTH row tables, strict, checked at COMMIT | `opening_infrastructure/_movement.py:39-47`, `:128-132`; `_base.py:135` |
| a statement file becomes lines, lines become movements or matches, an inbox that reaches zero | the SECU adapter and the four-verb reconcile | `statement_import/_adapters.py`, `_secu_csv.py`; `statement_match/*` (R-HP..R-HV) |
| an effective-dated rate on an account | `RateHistory` (account-scoped, unique per effective date) | `models/loan_features.py:17` |
| a recurring transfer priced by a rule that reads the destination | the loan payment: `loan_payment_settings` off the transfer template, rule 4 | `cash_ledger/_loan_pricing.py`, `loan_recurrence_sync.py` |
| a satellite params row per account kind | `loan_params.py`, `interest_params.py` | `models/` |
| a card-payment bank line that must never become a purchase | `R-GJ` parks it "awaiting its home" | `statement_match` |

What the tree does NOT have, and the design adds: a movement read on its own account rather than its
plan item's (3.2), a plan item placed on the account expected to pay it (3.3), the statement cycle
(3.4), the card payment rule (3.5), the finance-charge and rewards derivations (3.6, 3.7), the
card's screens (3.9), the card on the bank feed (3.11).

### 2.1 Where the open steps are taking the code, and how the card rides each direction

Each row is a direction the card must ride rather than duplicate or contradict:

| open step | the direction | what the card does about it |
|---|---|---|
| `X-f3c-5`, `X-bj-2` | an assertion is a CHECK, a level is an observation, `balance(T) = opening + postings` | the card's statement closing is a level; its payment prices from the fold (3.4 Q3); nothing in the card assumes the reset survives |
| `X-f3c-4` | an unexplained difference is a transaction the owner ACCEPTS, offered where a statement covers the span | the card cockpit's fourth remedy is that door, unchanged |
| `X-bk` | the ONE-TIME operator reconcile of imported history into the post-restructure shape | the card's first cycle under 3.10 is the same act on the card's statement |
| `X-bi-4` | the fold re-points to movements | the card asks it to read a movement on the MOVEMENT's account (3.2); today the fold reads it on the PARENT's (`cash_ledger/_events.py:750`) and the posted ledger on the movement's (`_posting_purchases.py:180`), and R-CC15's fork on X-bi-4's entry is where that is ruled |
| `X-bi-6` | the shadow rows go; the parent-account key's NO ACTION update rule is restored | 3.2 re-cuts that key to a plain parent FK, so the restore has no object and X-bi-6's entry loses that clause -- named, not assumed |
| `bank_import:X-f6b` | SimpleFIN: the statement arrives without being fetched, daily, landing on standing rules; its per-sync balance corroborates | SimpleFIN serves credit cards as it serves checking, so the card's lines and its daily balance arrive on that feed with no card adapter; the Capital One CSV adapter X-f6b's entry defers stays the MANUAL fallback (3.11) |
| `bank_import:X-gl` | one disposition row per bank line naming its VERB; "TRANSFER is the verb the disposition exists to admit, and `credit_card` is what makes TRANSFER real" | a card payment line on either statement is disposed TRANSFER against the payment transfer's leg (3.5); `R-GJ`'s parking retires |
| `bank_import:X-gg` | the envelope-semantics loop; it waits on the card's entry shape | 3.2 IS that shape (a card swipe is a purchase entry on the card); the loop designs over it once Q1 is ruled, and its scope grows by the matcher readers 3.2 lists |
| `recurrence:R16-d` | ONE total `accrued_interest(balance, rate, from, to, convention)` with a `ref` table of conventions | the card's finance charge consumes it per constant-balance segment under a `DAILY_ACTUAL_365` member that R16-d GAINS (coordinator, 2026-09-18: no second convention table); whether R16-d is pulled up the order was Q4 (ruled: it is not, `R-CC19`) |
| `recurrence:R5` | `due_on` beside `occurs_on`; `compute_due_date` deleted | the payment's due date and the finance charge's close are `due_on` of a monthly rule; the card states no date rule of its own |
| `recurrence:R7f` | what a PROGRAMMATICALLY created recurrence starts on is RULED | the card payment setup flow seats its transfer template at the NEXT due date, never the opening bound, under whatever R7f rules |
| `recurrence:R11` | a LEAD placement is expressible | the payment is placed by its due date (R-CC12); a lead needs nothing card-specific |
| `balance:X-ci` | a transfer has exactly one definition; the ad-hoc door closes | the payment is rows of a transfer DEFINITION from birth; an extra payment is a placed one-off of that shape |
| `balance:X-au-l`, `X-bp` | `amount_source_id` and `default_amount` are deleted; a rule is read through the LINK and a price through the SERIES | the CARD rule refines rule 3 through `card_payment_settings` (3.5); fixed mode is the template's series |
| `pay_calendar:C13-c` | every owner-scoped table holds its rows to ONE owner by a superkey and a composite key | `credit_card_params` and `card_payment_settings` are born with those keys |
| `pay_calendar:C20-b` | the month-day clamp has ONE producer, `utils/dates.clamped_day` | the close and due days clamp through it (3.4) |
| `balance:X-ab` | ONE asset-vs-liability rule in the posting path | the card's postings are a liability's and take that rule |
| `balance:X-bg` | "this occurrence did not happen" is told apart from "archive this row" on a transfer | a skipped card payment is X-bg's first case, not a card flag |
| `balance:R-BAL15` (and `X-cd`, which KEEPS the exclusive bound on a typed plan figure because "a `$0.00` PLAN is not a thing") | `$0.00` moved is a legal observation | a derived payment of `$0.00` (nothing owed) stores no figure, so no typed-figure bound bites; it is a legal row and writes no movement |
| `balance:X-i5` | every request is a QUERY until it declares a write | the card's doors declare theirs |

## 3. The design

### 3.1 The card account is a flag on its type, not a projection kind

`ref.account_types.has_revolving_credit` (boolean, seed-only like `has_appreciation`; the Credit
Card seed row carries it; `ck_account_types_revolving_is_plain`: a revolving type carries none of
`has_amortization` / `has_interest` / `has_appreciation` / `has_parameters`, so the "both flags"
type is unrepresentable rather than resolved by precedence). ONE predicate `is_revolving(account)`
beside `is_payroll_deduction_funded` in `app/services/account_projection.py:137` -- which today is
an enum TUPLE whose own comment asks to be replaced by a schema flag (`:124-134`), so `is_revolving`
is the shape that comment asks for rather than a copy of it -- gates every card feature. The CHECK's
`has_parameters` clause means what that column means (`models/ref.py:36-38`: a `*Params` row that
MUST be created alongside the account): the card's params row of 3.4 is optional by design, so the
type carries `has_parameters = FALSE` and the CHECK stands. `classify_account` stays five-valued and
a card stays PLAIN.

Why not a kind (the 2026-07-19 Architecture's `AMORTIZING -> REVOLVING -> ...`): the kind existed so
the seam could dispatch the card to its own fold, and the developer dissolved that fold on
2026-09-18 (R-CC14). With the seam kind-blind a sixth member buys nothing an engine reads and costs:
two kind-keyed tables that fail loud (`routes/grid/_shared.py:248` KeyError on every card grid
render; `ledger_account_service/_counters.py:141` ValueError at the deploy resync on a card true-up
with a non-zero correction), six sites spelling "records are the fact" as `is PLAIN`
(`_cash_flow.py:418`, `:736`, `_outstanding.py:311`, `_net_worth.py:394`, the two tables' PLAIN
rows) that would each need a second member, and the flip learning a twin at every PLAIN arm.
**Rejected also**: a `CardKind` enum (one boolean has no second value); INTEREST-kinding the card
(that accrual is a modelled return on an asset with an APY row, and `R-FO` would book a card true-up
as interest income). This SUPERSEDES the step `CC0a`'s "REVOLVING projection kind" and the
Architecture's precedence sentence.

The one seam surface that changes: `balance_at/_liability.py:95-101` holds every non-loan liability
FLAT forward "because it has no forward model". False since X-g2b: the fold's PLANNED tier is a
forward model for every account. The arm becomes kind-blind -- gated only by `configured_loan`, a
non-loan liability reads `balance_at` at each future sample date -- so the net-worth horizon carries
the card's projected balance, and a plain liability with projected rows stops holding flat too. This
is CC1c as the developer shrank it (R-CC14).

### 3.2 A purchase is a movement ON THE CARD, read on the card; the plan item never moves (R-CC15)

Today `fk_transaction_entries_parent_account` (`transaction_entry.py:232-237`) holds every
movement's `account_id` equal to its plan row's, AND the fold reads a movement on its PARENT's
account: `cash_ledger/_events.py:750` `_posted_purchase_facts` filters
`Transaction.account_id == account_id`, never `TransactionEntry.account_id`, and
`_cash_periods.py:396` `_budget_legs` and `:453` `_cash_sums` inherit that attribution from the
walk. The posted LEDGER already attributes by the movement (`_posting_purchases.py:180`
`_ledger_account_for(entry.account_id)`). The two agree today only because the key makes them equal.
The app carries the evidence that the premise is a card's to break:
`ck_transaction_entries_card_purchase_clears_nowhere` (`:278-287`: "A CARD purchase never touches
checking -- it leaves through its own CC Payback sibling -- so this link, which is scoped to the
ENVELOPE's account, could only ever claim that the checking statement showed it. False by
construction, and unwritable rather than merely unoffered. The credit-card arc revisits it (CC1b)").

**The design**: a movement names its own account -- the account its money moved through -- and is
READ on that account by every reader; its plan item's `account_id` is where it is EXPECTED to be
paid from (3.3). The composite key is re-cut to a plain `transaction_id` FK (so `X-bi-6`'s "restore
NO ACTION" clause has no object: named in 2.1); `fk_transaction_entries_reconciled_by` (`:244-248`)
then scopes a clearing link by the MOVEMENT's account, which is what a card statement clearing a
card purchase needs; the CHECK above and `TransactionEntry.is_credit` are DELETED with
`credit_payback_id`, and `movement_cash_leg`'s `if entry.is_credit: return 0.00` arm
(`_cash_leg.py:345`) becomes structurally unnecessary.

**What it asks of `X-bi-4`, stated as the fork the coordinator recorded on its entry**: the fold's
movement predicate becomes `TransactionEntry.account_id`, not the parent's. Under today's equality
either predicate proves X-bi-4's identity, so X-bi-4's spec does not say which; the card needs the
movement's. Until X-bi-4 re-points, a cross-account movement would be read by the fold on the
parent's account and by the ledger on its own -- two answers -- so the card's doors (CC-5) wait for
X-bi-4 and the key is re-cut WITH it.

**The readers that assume equality today** (the neutral review's census, 2026-09-18), each a named
item of CC-5 or of the bank-side leaf, none a branch on a card:

| reader | today | under the design |
|---|---|---|
| `cash_ledger/_events.py:750` `_posted_purchase_facts` | parent's account | the movement's (X-bi-4) |
| `posting_service.py:611-612`, `:787` self-heal scope `(txn.account_id,)` | re-derives the parent's account only | the set of accounts the row's movements touch |
| `status_seam/_covering.py:294-302` `_mirror_assertion`, `:414-415` `record_clearing` | copy the ROW's `reconciled_by_id` onto its covering movement | a movement on another account carries its OWN clearing link (the checking statement never showed it); the row's link stays the row's |
| `statement_match/_candidates.py:302-309`, `:709` `_transaction_candidates` + `_covering.py:233-240` `covered_cash_leg` | a settled row is offered on the parent's statement at `settled_family_leg`, and covering movements are never candidates (`:791-799` `~covering_clause()`, a clause already keyed by the MOVEMENT's account) | a row is offered on the account of its MOVEMENT'S money; a covering movement on another account is the candidate on that account's statement |
| `reconcile_service/_purchases.py:150-155` | outstanding purchases scoped by the parent's account, `is_credit IS FALSE` | scoped by the movement's account; the flag is gone |
| `statement_match/_create.py` `_born_purchase` -> `entry_service/_doors.py:369` | a minted purchase takes the parent's account; `statement_match_members`' composite key (`statement_match.py:287-291`) holds the member to the statement's account | a minted purchase takes the STATEMENT's account (a card line files into a checking envelope as a movement on the card); the member key holds |
| `statement_match/_container.py:418` | a NEW envelope is placed on the statement's account | placed on the owner's budget account (checking), its first purchase on the statement's |
| `transfer_service/_endpoints.py:288-295` `_apply_endpoint_move` | assigns movements by hand and relies on the cascade | assigns by hand alone; the comment goes |
| `entry_service` and the settle door | `transaction_entries.user_id` is the AUTHOR (`from_scratch_architecture.md:486-487`), so no composite key can hold the tender account to the owner | the door gates the tender account against the ROW's owner (`txn.user_id`), never `current_user`: a companion reaches the row through `auth_helpers.get_accessible_transaction` (`:377-417`) and does not own the card, so a gate on the caller would refuse every companion swipe and break `R-CC11`; the card picker is otherwise an IDOR |
| `cash_ledger/_amounts.py:509-518` `_entry_checking_impact`, `_cash_leg.py:63` `credit_entry_sum`, `:122-126` `posted_purchase_sum`, `:131` `settled_cash_leg`, `:250-267` `off_statement_sum` | `is_credit` is the PROXY for "this purchase's money is not on this account": a card entry is bucketed `sum_credit` and left out of the reservation | the proxy is the entry's own account: `entry.account_id != the fold's account`, an arm CC-5 adds BESIDE the flag's; the flag's arm stays until CC-7 has converted the 20 legacy lines that carry it (they sit on checking with the flag as their only mark), and CC-7 deletes the flag, its arm and the CHECK together. Deleting the flag first would move the 12 Paid envelopes' cash legs on checking by their card sums (up to `$2,372.75`) on a leaf that is not a money mover, and would destroy the fact the envelope clause needs. Without the re-key an UNPOSTED card swipe in a checking envelope floors checking's reservation and counts against its Projected End Balance -- the double count 3.3 exists to prevent. These files belong to the balance and bank-import lanes (read, never edit; coordinated at build) |
| `posting_reads.py:316`, `:420`; `_posting_purchases.py:80`, `:114` `purchase_posts`; `utils/entry_partition.py:46` | the posted-ledger oracle and the poster branch on `is_credit` | branch on the entry's account |
| `recurrence_engine/_amounts.py:100-106` + `_recurrence_common.classify_maintain_work` | the RETAINED-conflict arm exists because the composite key CASCADES an account move onto the entries | re-cutting the key deletes the arm's reason; the arm goes with it |

Two doors, one writer:

- **Charge a bill to the card** = settle the bill with its covering movement's `account_id` set to
  the card. `status_seam.apply_status_change` already writes the covering movement
  (`_covering.py:354-368` builds it with `account_id=row.account_id`); the `Settlement` record gains
  the tender account, defaulting to the row's expectation (3.3), else its own; a statement-driven
  settle forces tender = the statement's account. The door takes the movement's DAY (default the
  act's day; basis `entered`, the owner's word), and the books boundary refuses a day on or before
  the card's opening. A bill's ONE movement is on the card; the bill's leg on checking is zero by
  construction (R-FM's identity); undo is the ordinary revert, which deletes the movement.
- **A card swipe in an envelope** = `entry_service.create_entry` with the card as the purchase's
  account. The envelope's spend is the sum of its purchases whatever account each moved through, so
  the budget reads right and each account's balance reads right.

No `charged_from_account_id`, no retarget, no ghost row derived from provenance (R-CC9's ghost row
IS the plan row, still on the paycheck grid, with its money elsewhere), no `card_charge_for_id`, no
aggregate card-side row kept in step by a sync matrix (CC3c's
`ONE settled card-side EXPENSE = credit-entry sum`, a stored derivation N-351 already names), no
`is_card_tender` rename (R-CC7 is re-opened: the flag is deleted, not renamed). The census of the
name is `grep -rn is_credit app/ --include='*.py'`: 16 files outside the two workflow modules
section 4 deletes, of which the STATUS predicate `balance_predicates.is_credit(txn)` (`:459`, read
at `mutations.py:247`) and the docstring mentions are CC-7's, and every FLAG reader -- the table
above plus `entry_service/_refusals.py:460` and `schemas/validation/entries.py:69`, `:129` -- is an
item of CC-5, CC-7 or the bank-side leaf.

**Worked example, row 2773 on production** (`$1,958.87` Family, period 2026-07-16, today Credit with
a Paid payback): from scratch it is the same plan row on the paycheck grid, settled, with ONE
movement of `$1,958.87` on the card dated the day he swiped; checking's fold sees nothing on that
day; the card's fold sees `-$1,958.87`; the card's August statement line for it clears that
movement; the cash leaves checking on the card's due date inside the derived payment (3.5). Envelope
2276 (two card purchases, `$181.58`, payback `$123.18`): two purchase entries on the card, `$181.58`
of Family spending attributed where it belongs, and no "CC Payback" row anywhere.

### 3.3 Where a plan item is EXPECTED to be paid from -- FORK Q1 (the grid's identity)

Recording is 3.2. PLANNING needs one more fact: a bill that is always paid by card (Netflix, the
phone) should not be counted against checking's projected balance in its own period and then again
inside the card payment; and the developer wants to see on the grid that a row is a card row before
it is paid. The grid today is ALREADY keyed by paycheck: its rows are
`(category_id, template_id | name)` (`grid_view_service.py:46-59`, `:169-172`), its columns are
periods, and the ONE account fact is a filter, `Transaction.account_id == account.id` at
`routes/grid/page.py:311`; the keyboard model and the popovers carry `txnId` / `periodId` and no
account; `grid_edit.js` names an account only on the empty-cell CREATE path. Three shapes:

| | B. a plan item lives on the account expected to pay it; the grid shows the paycheck's rows across the owner's cash-flow accounts (recommended) | A. plan items stay on checking; a `paid_from_account_id` on the DEFINITION says the tender | C. per-account grids, no expectation |
|---|---|---|---|
| the fact | none new: `transactions.account_id` means ONE thing, "the account this item's money is expected to move through", which is what the covering movement already defaults to | a nullable column on `transaction_templates` (NULL = the item's own account), inherited by every row; the settle default reads it | none |
| the grid | the filter at `page.py:311` widens from one account to the SET the ruling names, "checking and its cards": the owner's default grid account (`user_settings.default_grid_account_id`, else the first active checking account -- `resolve_grid_account`'s own steps 2-3, `account_resolver.py:223-236`) plus the owner's active revolving accounts, a NEW predicate, because `is_cash_flow_account` (`:90-91`) is only "not amortizing" and admits the IRAs and the savings accounts; the balance line stays ONE account's, any member of the set (default the primary, an override within it as today; an override naming an account outside the set keeps that account's single-account grid as today, with no chip and a zero term); a CELL on another account carries an account chip (a definition moved between tenders keeps its settled cells on the old account, so one grid row can hold cells on two accounts: `grid_view_service.py:52-59` keys rows by `(category_id, template_id | name)` with no account); the create popover takes an account (default: the balance line's) | unchanged filter; a card chip read off the definition | card bills live on the card's grid; checking's grid shows only the payment |
| the fold | UNTOUCHED and kind-blind: each account's fold loads its own rows (`_facts.py:471-474`, `:560`) and, after 3.2, its own movements | `planned_cash_rows` gains a two-arm predicate joined to the definition (budgeted here with no expectation, OR expected here) on BOTH accounts' folds -- a tender branch INSIDE the fold, the thing 3.1 and 3.2 delete elsewhere | untouched |
| the grid identity (R-K: `balance[p]-balance[p-1] == net[p] + timing + book_vs_bank + contribution + accrual`) | `net[p]` is the PAYCHECK's budget (both accounts' `_budget_legs` composed in the grid reader) and the balance is one account's, so the identity gains ONE named term, `elsewhere[p]`: rows of this paycheck whose money moves on another account -- which is exactly the figure the cheat shows the owner today as the Credit rows' total | the same term is needed, or the cells and the subtotal part (the review measured it: a card bill's cell renders on checking's grid while `_budget_legs` drops it from the subtotal) | no term; the budget is split across grids |
| what it costs | the widened filter; the composed subtotal and its term; an account on create; a chip. Moving a bill between tenders is a plan edit of `account_id` (a projected row has no movement; a settled row's account is not moved -- its next occurrence is) | one column, one join, the same term, a settle default, the same chip -- and the branch in the fold | the budget in two places, the thing the cheat exists to avoid |
| fences it deletes | the cheat's `Credit` status as a planning device; no expectation column to keep in step with the account | the same status | none |

**The same set, read everywhere a paycheck's plan items are read.** The grid is one of at least four
owner-facing readers scoped to ONE account by `Transaction.account_id == account_id`:
`dashboard_service/_bills.py:78` (the upcoming bills), `spending_analysis.py:194`, `:258` (the
spending report) and `calendar_service.py:412` (the calendar). Under the ruling the phone bill lives
ON the card, so a reader left at one account drops it from the dashboard, the report and the
calendar; every one of them reads the set through the one predicate (CC-4).

**Worked example**: the developer's phone bill, `$45` on the 5th, always on the card; a grocery
envelope `$500` with a `$120` card swipe. Under B: the phone row is a card row and shows on the
paycheck grid with a card chip on its cells; its `$45` sits on the card's projected balance; the
grocery envelope is a checking row whose `$120` purchase is a movement on the card; the paycheck's
expense subtotal reads `$545` of budget; checking's balance line moves by `$380` and the identity's
`elsewhere` term reads `$165`; the card payment brings `$165` back onto checking's grid as one
payable when he pays (3.5). Under A: the same figures, with the `$45` routed by a definition column
and the fold deciding per row which account a plan row belongs to. Under C: the phone row is on the
card's grid and the owner reads two grids to see one paycheck. Recommendation: **B** -- it is the
from-scratch reading of the column the app already has, it keeps the fold kind-blind and
account-keyed, and its cost is one widened filter and one honest term on the grid's reconciliation
row. (The first draft recommended A and graded B as "the largest UI change the app has had"; the
review measured the grid's keying and refuted that cost.)

**RULED 2026-09-18 (developer): B, `credit_card:R-CC16`.** The option text as picked:

> "No new column. A plan item's account_id means one thing: the account its money is expected to
> move through. The phone bill is a row ON the card; the grocery envelope is a row on checking whose
> `$120` swipe is a movement on the card. The grid's one filter widens from 'this account' to 'the
> owner's cash-flow accounts' (checking and its cards; loans and investments stay out), the
> Projected End Balance stays ONE account's (checking's, chosen as today), a row on another account
> carries an account chip, and the create popover takes an account (default: the balance line's).
> The fold is untouched and stays kind-blind. The grid's reconciliation row gains one named term,
> 'on other accounts'."

Before picking, the developer asked how it works in practice and gave the context that amended 3.5
(the payment mode, payments since close, the partial payment); his words are recorded there. Note
the word EXPECTED: the row is the expectation, the movement is the fact (3.2), and a summary that
drops it collapses the two.

### 3.4 The statement cycle, and where the statement balance comes from -- FORK Q3

`budget.credit_card_params` (1:1 account, CASCADE, the `loan_params` pattern, born with C13-c's
owner key): `statement_close_day` and `payment_due_day` (SmallInt, CHECK 1-31, month-end clamp
through `utils/dates.py:398` `clamped_day`), `min_payment_percent` Numeric(5,4) (the form takes a
percent, the column stores the fraction), `min_payment_floor` Numeric(12,2), `cashback_rate`
Numeric(5,4) default 0, `auto_redeem_threshold` Numeric(12,2) NULL (NULL = manual only),
`credit_limit` Numeric(12,2) NULL (utilization only). No auto-create: a card with no params row is a
dormant plain liability and every card feature gates on the row. APR rides `budget.rate_history`
unchanged: account-scoped, effective-dated, CHECKed to [0,1]; the loan loaders resolve through
`LoanParams` and never see a card row; the card gets its own thin loader and a card-gated write
route.

A STATEMENT is DERIVED (R-CC2): `card_statement.py`, pure -- `cycle_window(close_day, month)`
(closed-open, so a row posted AT the close belongs to the next cycle), `statement_sequence`,
`due_date_for`, `statement_balance` = the fold at the close instant, `grace_kept` (the prior
statement paid in full by its due date), `minimum_payment` =
`max(floor, round_money(pct x balance))` clamped to the balance, all Decimal, `round_money` at the
boundary only. The finance charge is 3.6's.

**The card's actual closing balance is an OBSERVATION**, typed on the cockpit or carried by the
feed, and it lands in the level relation exactly as a bank's closing does (`account_anchor_history`,
an import's row naming its import, the owner's `uncorroborated`). Until the flip it resets the card
as it resets checking; after `X-bj-2` it moves nothing and yields
`discrepancy = observed - computed`, shown on the card cockpit with the four remedies. The card is
just another account to X-bj.

**FORK Q3 -- what prices the payment: the app's derived statement balance, or the observed one?**

| | (i) the FOLD at the close (recommended) | (ii) the observed level on the close day when one exists, else the fold |
|---|---|---|
| the rule | `statement_balance` is one walk: the card's fold at the close instant. An observation is compared, never substituted | two sources for one figure, chosen by presence of a level row |
| when the app is behind the card (a purchase not yet recorded) | the payment under-states; the cockpit shows the discrepancy and the fix is to record the purchase (the feed does it), after which the payment is right | the payment is right at once; the discrepancy still shows; the missing purchase stays missing |
| rule 14 | one producer | the "which authority wins" branch `X-bj` dissolved for checking, re-erected for the card |
| worked, close 09-20: app says `$1,234.56`, statement says `$1,240.06` | payment projected `$1,234.56` until the `$5.50` line lands; then `$1,240.06` | payment `$1,240.06`; the `$5.50` is a discrepancy the fold never absorbs |

Recommendation: **(i)** -- it is the design the level relation was ruled for (R-IS: "a level never
moves a balance"), and the feed makes "record the missing purchase" one click. (ii) is a plug with a
prettier name.

### 3.5 The payment is a recurring transfer priced by the card

**What the developer does today, in his words (2026-09-18, before ruling Q1)**:

> "Right now the single most important row on the grid is the Projected End Balance which show my
> estimated cash on hand after every bill has been paid and all income has been received for that
> pay period. This is how I determine the health of my finances and whether I can afford a purchase
> or if I need to cut spending or transfer money from savings. I'm currently using my credit card to
> carry expenses if I need to move an expense from one paycheck to another. For example, with my
> biweekly paycheck, sometime my bills cluster in one pay period so I'll pay for groceries on the
> credit card then pay it off out of the next pay check. The money still comes out of checking. The
> only thing that changes is when the money comes out of checking. This is why my cheat has been
> effective so far. ... Most expenses come out of checking by default. There are a few that are
> charged to my credit card by default though. One feature that I can't do easily now is make a
> partial payment to the credit card. For example, I have `$500` worth or credit card charges but I
> only want to pay `$300` off now. Currently, the app doesn't handle this well and I have to mark
> individual expenses paid even if the totals don't match exactly."

Three things follow -- PROPOSED here, and RULED only when Q3's option text names them (coordinator,
2026-09-18); together they would AMEND R-CC3's "ONE auto-maintained projected transfer per statement
on the due date":

1. **The payment has a MODE, and one of them is his practice.** `budget.card_payment_settings` (1:1
   `transfer_template_id`, the `LoanPaymentSettings` shape, born with C13-c's owner key;
   `payment_mode_id` FK to seeded `ref.card_payment_modes`): `every_paycheck` (a payment at each
   payday for what the card holds at that payday -- the cheat's behaviour, as ONE row per paycheck
   instead of one payback per expense), `statement_balance` (the fold at the last close, on the due
   date -- R-CC3's shape), `minimum_payment` (3.4's minimum, on the due date), `fixed` (the
   template's own series, rule 3). The setup flow mirrors the loan payment flow and creates ONE
   RECURRING transfer definition checking -> card per card, held by a unique key over the card on
   `card_payment_settings` (a placed one-off into the card is a second, rule-less definition,
   `R-BAL20`, and an extra payment), whose cadence is the mode's: the owner's pay cadence for
   `every_paycheck`, monthly on `payment_due_day` otherwise; seated at the NEXT payday or due date
   under `R7f`'s rule rather than the opening bound; its rows' due date is `R5`'s `due_on`.
2. **Every derived payment SUBTRACTS the payments and credits made since the figure it prices from**,
   floor 0: statement mode = the fold at the last close less payments and reward redemptions
   credited since the close; every-paycheck mode = the card's balance at the payday less payments
   since the last payday. So a partial payment shrinks the next derived one by construction and the
   remainder rolls; if a due date passes with a remainder, 3.6's interest accrues on it and the
   cockpit shows the projected charge (post-`R16-d`, per `R-CC19`).
3. **A partial payment is a placed ONE-OFF transfer into the card** of the owner's own figure (OWN,
   rule 1) in whichever paycheck he picks -- `X-ci`'s one-definition shape for a transfer, nothing
   card-specific -- and it shows on the paycheck grid as a payable in that period. No per-expense
   marking: the card's balance is one number and payments are amounts. (AMENDED the same day by
   `R-CC22`, below: the partial is the payday row's figure typed over; a placed one-off is an EXTRA
   payment.)

The rows are priced by a **CARD rule** in `cash_ledger/_amount_rule.py` / `_amount_source.py`: a
refinement of rule 3 read through the LINK (`template.card_payment_settings`, as `_is_loan_payment`
reads `settings` at `_amount_rule.py:307-336`; `AmountRule.LOAN_PAYMENT` is the resolver's own
refinement and no `AmountSourceEnum` member, `enums.py:583`) -- never a new enum member, which
`X-au-l` deletes. The row stores no figure (R-FI). The pricing needs a balance-at-T from inside
`cash_ledger`, which imports nothing of `balance_at` (grep: 0 hits, while `balance_at` imports
`cash_ledger` ten times); the loan precedent prices from terms and needs no fold, so this is NEW
ground: the read pass injects the deriver (the `_memoize_once` shape, `_positions.py:486-488`), and
CC-6's design loop states it. The balance it reads is the fold at the END OF THE DAY BEFORE the
row's day (the amendment below), which `sample_cumulative`'s on-or-before read answers and which
excludes the row's own leg -- the fold's PLANNED tier carries every live projected transfer leg the
account is on (`_facts.py:471-474`), so a row priced AT its own day would count itself.

**RULED 2026-09-18 (developer): (i), `credit_card:R-CC18`, which AMENDS R-CC3.** The option text as
picked:

> "ONE payment DEFINITION per card, a recurring transfer checking -> card, with a MODE on
> card_payment_settings: every_paycheck (a payment at each payday for what the card holds at that
> payday -- what the cheat does today, as one row per paycheck), statement_balance (the FOLD at the
> last close, placed on the due date), minimum_payment, or fixed (the template's own series). Every
> derived payment subtracts the payments and credits made since the figure it prices from, floor 0,
> so a partial payment shrinks the next one and the remainder rolls. A partial payment is a placed
> one-off transfer into the card of your own figure in the paycheck you pick. The card's observed
> statement closing is a LEVEL compared beside the fold on the cockpit, never substituted into the
> payment. Amends R-CC3."

The three items above are therefore no longer proposed; 3.4's Q3 (the fold, never the observed
level, prices the payment) is answered by the same text.

**AMENDED 2026-09-18 (developer, the same day, `credit_card:R-CC22`), after the second neutral
review measured the text above against its own worked example.** Read literally in `every_paycheck`
mode the subtraction clause DOUBLE-COUNTS: the card's balance at a payday already nets every payment
settled before it (`cash_ledger/_walk.py:350` keys each fact by its `settled_on`), so subtracting
them again makes the Oct 8 row `$0` for `$200` owed; a one-off placed beside a `$500` payday row is
`$800` of outflow in P2; a row priced AT its own day counts its own leg; and a payday row paid late
(after the next row priced) is paid twice. The option text as picked:

> "A payday row prices at the card's balance at the end of the day before its day (so it never
> counts itself), less every OTHER transfer or credit into the card dated on or before its day that
> is not yet inside that balance (placed but unpaid, or paid later), floor zero; so a one-off placed
> in the same paycheck shrinks the row and the paycheck's total stays the card's balance. Paying
> LESS than the balance is typing my own figure over the row (`$300` over `$500`); the next payday's
> row reads the balance then (`$200`). The same rule prices statement_balance and minimum_payment:
> the close's figure less every transfer or credit into the card dated after the close and not yet
> inside it. A purchase made on the payday itself rolls into the next row. Amends R-CC18's
> every_paycheck, subtraction and partial-payment sentences."

The typed figure is the override a generated transfer row already carries and keeps through
regeneration (`transfer_service/_amount.py:48-66`; the maintain pass selects "projected, not
overridden", `transfer_recurrence.py:295-299`; the loan payment does this today), and a placed
one-off is dated its paycheck's first day, the payday row's own day
(`transfer_recurrence.py:262-266`), which is why same-day netting is the rule. Rejected: no
same-paycheck netting (the `$800` path stays open and a late-paid row is paid twice); the text as
first ruled (the `$0` row).

**Projected End Balance is unchanged in meaning**: checking's cash after every checking outflow of
the period, including whatever card payment sits in it. Money put on the card reaches that row when
a payment brings it back, which is the timing the card is being used to choose; the grid carries the
`elsewhere` term per period (3.3) and the card's owed balance as a chip, so affordability stays
readable at a glance.

On checking's grid the payment is the transfer's checking leg, a payable in the due date's period
(R-CC10, structural under `transfer_legs.TransferLeg` since X-bi-6a); the timing shift R-CC12
accepts is the correct one. Underpayment (payment < minimum) warns -- a non-blocking notice with a
one-click "pay statement balance" that flips the mode, which is a rewrite of the transfer's
recurrence rule (the PERIOD unit to MONTH on `payment_due_day`, `recurrence_rule.py:343-402`) and a
regeneration of its projected rows through the maintain pass, stated so because it moves rows.
`R-GJ`'s parked card-payment bank lines get their home through `bank_import:X-gl`'s TRANSFER verb: a
Capital One payment on the CHECKING statement is disposed TRANSFER against the transfer's checking
leg; the same payment on the CARD statement against its card leg.

### 3.6 Interest is a real row of a card-owned definition, priced by the app's ONE interest producer

When grace fails the card charges interest; the statement will show it as a line. From scratch it is
a row of a DEFINITION the card owns (`credit_card_params.finance_charge_template_id`, the
`SalaryProfile.template_id` precedent: a definition whose rows are priced by a relation, rule 2
being a subset of rule 3), monthly on the statement close, priced by the card rule as
`recurrence:R16-d`'s ONE `accrued_interest(balance, rate, from, to, convention)` under a
`DAILY_ACTUAL_365` member that R16-d GAINS, consumed PER CONSTANT-BALANCE SEGMENT of the cycle (the
card's balance path is piecewise constant between movements, and so is its rate between
`rate_history` rows): `sum over segments of balance x APR/365 x days`, which is APR/365 x average
daily balance x days exactly (R-CC8), with purchases joining the path on grace loss and the cycle's
window closed-open so the charge posted at the close prices the cycle before it. The card ships NO
convention table of its own (coordinator, 2026-09-18: a second home for the convention is the
rule-14 defect the arc exists to delete). `$0.00` when grace held, which is an honest projected row
and writes no movement when it settles at zero (R-BAL40's rule). **RULED 2026-09-18 (developer, Q4,
`credit_card:R-CC19`): the card's interest WAITS ITS TURN IN O6.** The option text as picked:

> "R16-d stays where the outcome order puts it (after R16-c, in O6). The card's interest leaf is
> ranked after R16-d; every other card leaf proceeds. Cost: cycles where grace is lost carry no
> projected finance-charge row until O6 reaches R16-d -- the cockpit still shows the lost grace and
> the remainder, and the real charge arrives on the feed. No loan step is pulled forward for the
> card's sake."

Until then the cockpit's grace state and the remainder are the card's whole interest surface, and a
real charge arriving on the feed settles as an unplanned card row; 3.5's sentence that a remainder
carried past a due date "accrues 3.6's interest" and `R-CC3`'s "plus a projected finance charge"
both read as POST-`R16-d`, and the re-mint records `R-CC3` as amended by `R-CC18` with that clause
deferred by `R-CC19`. Confirming the real charge is the ordinary settle: the feed's line matches the
row, or the owner ticks it. No `system_origin_id` column (R-IY: the row's definition already says
what it is), no sync that creates and deletes a conditional row, no fourth pricing FK (the step
`CC4d` is re-opened: the row carries `template_id` and satisfies `ck_transactions_one_pricing_link`
at `<= 1` today and at `= 1` after `X-bi-7d`).

### 3.7 Rewards accrue as a derived figure; redemptions are rows

`accrued = round_money(cashback_rate x SUM(all card purchases)) - SUM(all redemptions)`, one walk
over the whole history rounded once (a "since the last redemption" window reads `-$5.00` after a
`$5.00` partial redemption of `$20.00` accrued, where the answer is `$15.00`), over the card's
movements (purchases only: expense-parented movements, never the payment leg), a non-producer
(rewards earned, not a balance). A redemption is a real credit row on the card: manual (a one-off,
OWN figure, `0 < amount <= accrued`) or auto (`accrued >= auto_redeem_threshold`: one projected row
of a card-owned rule-less definition, priced by the card rule, at most one live projected row at a
time, held by a partial unique index the way the payback's `uq_transactions_credit_payback_unique`
holds it today; confirmed by the ordinary settle). A confirmed redemption reduces the fold and so
the next derived payment, by construction.

### 3.8 What the card refuses

No transfer OUT of the card (no cash advances, no balance transfers):
`_reject_transfer_out_of_ revolving` beside `_reject_transfer_out_of_loan` at BOTH of its call
sites -- `transfer_service/_create.py:332` and `_endpoints.py:192` (the endpoint-move door the CC3d
spec missed). `active_accounts_query` gains an orthogonal `revolving` filter; the salary deposit
picker passes `revolving=False`. Direct income on the card (a refund, a redemption) stays allowed.

### 3.9 The card's screens

Under the design language and the cockpit grammar (hero + chips + a chart that IS the answer +
supporting cards; tables only for the grid and statements):

- **The card cockpit** replaces `cash_detail` for a revolving account: hero = the balance the app
  derives beside the card's last observed closing and their difference (the reconcile card checking
  has); chips = statement close / due date / minimum due / grace kept or lost / utilization against
  the limit; the chart = the balance path through the cycle with the close and due marked (a shape,
  so a chart); cards = the purchases of this cycle (movements, the one list that is a table because
  it IS the statement), the payment (mode, next amount, pay-in-full), APR history, rewards (accrued,
  redeem). Every figure through the seam.
- **The paycheck grid** (3.3 B) keeps its rows; a card chip marks a row on the card or a row whose
  movement is on the card; the payment leg is a payable on the due date's period; the `c` key and
  the palette's Credit command become "charge to card" (the picker under the multi-card policy: zero
  cards -> hidden; one -> it; several -> lowest `sort_order`, then id), an ownership-gated door
  (3.2).
- **The savings cockpit tile** for a card: balance, utilization, next payment.

Every screen through the `shekel-design` loop with its own dev-clone live-verify; mockups out of the
repo.

### 3.10 The cutover of the cheat -- FORK Q2 (the one that moves money)

**Measured on production 2026-09-18** (section 7): no Credit Card account exists; 19 rows carry the
`Credit` status, of which 16 have a PAID payback (`$4,629.16` -- the card was paid back, the source
is frozen at Credit) and 3 have a PROJECTED payback (`$970.00`); 20 card-tender entries
(`$2,372.75`) in 12 envelopes, ALL Paid on the purchases basis, every one with a PAID payback
(`$2,315.63`; envelopes 2275 and 2276 differ from their purchases by +`$1.28` and `-$58.40`, the
other ten are equal); 30 journal legs stand on the paid paybacks.

Three shapes for the history:

| | (a) the payback WAS the movement (recommended for every frozen pair) | (b) freeze: `Credit` and `is_credit` retire as terminal vocabulary on the old rows (R-CC5 as ruled) | (c) open the card's books back at 2026-05-03 and reconcile its whole history through its statements |
|---|---|---|---|
| a frozen transaction-level pair (2773: Credit `$1,958.87` + Paid payback `$1,958.87`) | the source becomes a settled row whose covering movement is the payback's cash on CHECKING on the payback's own settle day; the payback row is deleted. Checking's CASH fold: byte-identical (same figure, same day). The BUDGET clock moves: the `$1,958.87` leaves the payback's period (P+1, category "CC Payback") and lands in the source's (P, Family) -- a correction of history, stated and graded per period. Lost: that it went via the card (the card's pre-books history, which no account holds) | untouched; the `Credit` status survives as `credit: {credit}` terminal with the R-HA obligation | the source becomes a card movement dated by rule; the payback becomes the checking leg of a payment transfer; the real ACH payments on the SECU export (9 lines, `-$7,412.94` through 07-17) match those legs as a group with named residuals |
| a frozen entry-level pair (2276: purchases `$181.58` + payback `$123.18`) | as first drafted: the two card entries deleted and one line of the payback's figure written; AS RULED (the envelope clause below): the lines are KEPT and settled on the payback's day, and where the payback disagrees with the lines a correction line of the difference is written. Checking's cash fold: byte-identical either way. The envelope's recorded spend moves from `$505.91` to `$447.51` (2276) and by +`$1.28` (2275); ten envelopes are unchanged | untouched; `is_credit` survives on 20 rows with no writer, and `movement_cash_leg`'s zero arm survives for them | the two entries become card movements; as left |
| the 3 live pairs (`$970.00`) | see the recommendation | the paybacks deleted, the sources freeze | the sources become card movements after the books open |
| the posted ledger | the paybacks' posted legs (30 on the pre-3d census; X-bi-3d's resync re-posts a Paid row's cash from its movement, so the count and the linking column at CC-7 are the resync's) are REVERSED before the delete (the reverse-before-delete discipline; `journal_entries.transaction_id` is `SET NULL` on delete, `journal_entry.py:247-252`, and an orphaned leg is counted as RESIDUE by the walk, `account_posting_service/_walk.py:529-532`); the deploy resync (`scripts/init_database.py:238`) then re-derives the surviving rows' postings under the source's category | unchanged | rebuilt by the operator act |
| fences left standing | none: `Credit`, `is_credit`, `credit_payback_for_id`, `credit_workflow.py`, `entry_credit_workflow.py`, `uq_transactions_credit_payback_unique` all go | the terminal status, the flag, the zero arm, the "destroy-but-not-correct" asymmetry R-CC5 names | none |
| what the owner does | reads ONE statement balance for the opening, then converts three rows at the new door | nothing | reads the May statement balance; imports four months of card statements; answers ~40 lines and 9 payments in the inbox (X-bk's shape, for the card) |

**Recommendation: (a) for every FROZEN pair, by a total rule in the cutover migration (R-BAL40's
class: every column of the movement is a function of the payback's stored facts), graded on
checking's CASH fold by equality AND on the budget clock by a stated per-period diff. The three LIVE
pairs are the OWNER's act at the new door, not the migration's**, because no card ACCOUNT exists
when the migration runs (R-BAL40 amends R-HJ to permit a cutover writing movements by a total rule;
what it cannot do is write one on an account that is not there) and R-CC6's "deploy never bricks"
forbids a migration that refuses: the migration retires the mark/unmark doors and the `Credit`
transition (the status becomes unreachable), converts the frozen pairs, and leaves the three live
rows standing until the owner (1) creates his card through the account door with its books opened on
the day BEFORE the oldest of the three rows' dated days -- 2026-08-27 is row 2859's period start (it
has no due date), 2731 is due 09-07, 2881 is due 09-10, so the books open on or before 2026-08-26 at
the balance the August statement states; the boundary trigger reads `settled_on` strictly after
`opened_on` (`_base.py:135`), so the refusal is against the DATED day, never the period -- and (2)
presses "charge to card" on each: the ONE door of 3.2, which settles the row with its movement on
the card dated by that rule (basis `entered`) and deletes its payback. The derived payment (3.5)
then carries the `$970` to checking on the real due date, and the first cycle on the feed reconciles
every purchase he did NOT type. Three rows under a retired status for the days between the deploy
and his act is the honest state; `Credit`'s enum value is deleted by a later leaf once no row
carries it. (Under `R-CC21` no row carries it after the migration, so CC-7 deletes the value in the
same leaf: the "later leaf" was for live rows the prerequisite leaves none of.)

This RE-OPENS **R-CC5** (freeze history; Credit as terminal vocabulary -- option (b), rejected:
three fences for twenty rows forever) and **R-CC6** (the migration creates a `$0` card inline --
rejected: an app-computed opening on a card whose real balance is known is a plug, and the owner's
act through the door is R-HJ's shape). (c) for the whole history stays available to the developer
later, since the opening can be restated backward through the door X-f3c-2b-2a shipped.

**RULED 2026-09-18 (developer): (a), `credit_card:R-CC17`.** The option text as picked:

> "Frozen pairs (16 + 12 envelopes) convert by a total rule in the cutover migration: the source row
> settles with one covering movement on checking on the payback's settle day, the payback row is
> deleted after its ledger legs are reversed; checking's cash fold is graded byte-identical and the
> budget-clock diff is stated per period. The mark/unmark doors and the Credit transition are
> retired in the same migration. The 3 live rows wait for your act: create the card with books
> opened on or before 2026-08-26 at the August statement's balance, then 'charge to card' each; the
> Credit enum value is deleted by a later leaf once no row carries it. Re-opens R-CC5 and R-CC6."

**AMENDED 2026-09-18 (developer, the same day), twice, after the second neutral review.** What it
refuted in the text as ruled: (1) the sentence "the source row settles with one covering movement on
checking" cannot run for the 12 ENVELOPES, which are Paid on the `purchases` basis and carry no
covering movement by the seam's own rule (`_covering.py:456-463`; `_record.py:71-80` refuses a
purchases settlement with a figure), and the first draft's table deleted 20 itemised lines
(`description` and `purchased_on`, `transaction_entry.py:321-324`) that the ruling never mentioned;
(2) "books opened on or before 2026-08-26 at the August statement's balance" pairs a day with
another day's figure, which `R-HG` forbids (the opening IS the closing balance for its own day,
`_base.py:127-137` strict), and leaves STRADDLERS -- frozen pairs charged inside the opening figure
whose payback settled after the opening day -- whose payback the conversion books on checking and
the card's fold never sees; (3) "basis `entered`" was invented (the total rule copies the payback's
own `settled_day_basis_id`); (4) "retired in the same migration" reads as the same LEAF (a door is a
route). Measured for it (section 7, part 4): an opening on any day Aug 20-26 has two straddlers,
`$148.15`; Jul 31-Aug 4 none; Sep 11 or later none, but the three live rows inside the opening; and
0 of 28 paid paybacks carry a clearing link or a `statement_match_members` row, so nothing of that
kind is lost to a delete's CASCADE today (`statement_match.py:281-285`). That census ran at alembic
`d2e9f4a17c63`, BEFORE release #405 put X-bi-3d live the same afternoon (`ad573b07bede`): since then
every Paid payback carries a covering movement (which a delete of the payback also CASCADEs,
`fk_transaction_entries_parent_account`), so the total rule copies the day, the figure, both bases
and the clearing link from the payback's MOVEMENT before the delete, re-points any match member to
the source, and CC-7 re-runs the census on the post-3d shape rather than quoting this one.

The ENVELOPE clause, `credit_card:R-CC20`, the option text as picked:

> "Keep every card purchase line as the envelope's own purchase, settled on its payback's paid day
> with the payback's own day-basis; delete the payback row. Where the payback's figure differs from
> the lines' sum, the cutover adds one correction line in that envelope on the same day for the
> difference, described 'card cutover: paid $X more than itemised' when positive (2275, +`$1.28`, a
> purchase) or 'card cutover: $X itemised but never paid back' when negative (2276, `-$58.40`, which
> the app shows as a refund against the envelope), so checking's balance is byte-identical and each
> envelope's spend equals its cash. I can edit or delete a correction line later. Amends R-CC17 with
> its envelope clause."

A negative line is a REFUND under `R-II` (`transaction_entry.py:155-185`), which is what its
description says it is not, so the description carries the fact. Rejected: booking the lines at
`$181.58` on 06-23 (the Paid payback's `$123.18` is what left checking, `R-FM`); a migration that
REFUSES on the two disagreeing envelopes (it blocks the deploy); a post-deploy hand fix (its only
path runs through the doors this leaf retires); collapsing each envelope's lines into one line of
the payback's figure (offered; loses what was bought and when).

The OPERATOR step, `credit_card:R-CC21`, the option text as picked:

> "The cutover is a coordinated release with one prerequisite act of mine: I pay the card in full,
> mark every payback paid, and make no new card charges until the card exists in the app. The books
> then open on the day of that payment at the card's balance at the end of it, verbatim (zero plus
> any same-day charges). By construction no pair straddles and no charge is pre-books, so the
> migration's one rule converts every pair and the door records only what comes after. A pair still
> live at the release is reported, its payback deleted and its bill settled at `$0.00` on checking
> with its cash inside the card's opening (the bill leaves that period's budget, stated per period).
> Amends R-CC17's operator step."

The two classes the first text had to represent -- a charge inside the opening with its payment
outside it, and a live charge inside the opening -- are made EMPTY by the prerequisite rather than
represented, which is the doctrine's own shape (a fence made structurally unnecessary); the `$0.00`
settle is the one the seam already writes with no movement (`_covering.py:434-436`). The first
derived payment after any opening pays the card's WHOLE balance then (3.5), cash the cheat never
scheduled on the grid; under this rule it is near zero. Rejected: opening at the statement close
before the live charges and re-filing each straddler's payback as a transfer into the card (no door
writes "a settled bill with no movement" -- X-bi-3d made every settled row carry one -- so the act
would have to be designed first); opening on or after the last frozen payback with the live bills
settled at `$0.00` (the bills leave the budget clock for no reason the prerequisite does not
remove); the ruled text corrected to one day with the opening restated at the first reconcile (a
figure the app computed, `R-GX`'s plug). **The X-bi-7d window (`credit_card:CC-352`)**: `X-bi-7d`
makes both SET NULL link keys RESTRICT, `fk_transactions_credit_payback_for`
(`transaction.py:464-468`) among them, so until CC-7 deletes the payback rows a bulk delete of a
`Credit` parent that today SET-NULL-orphans its payback is refused by the database; ruled 2026-09-18
as a ledger row owned by the cutover leaf, no interim code (minted that day, on dev with #406, owner
`CC3b` until the re-mint re-homes it).

### 3.11 The bank: the card is one more account on the feed

`bank_import:X-f6b` makes the statement arrive without being fetched: a SimpleFIN daily feed landing
on standing rules, its per-sync balance the corroboration source. That SimpleFIN serves a credit
card as it serves checking is a property of an external service that nothing on the tree asserts
(`X-f6b`'s own entry does not), so it is `X-f6b`'s to confirm; if it holds, the card's lines and its
daily balance arrive on that feed with NO card adapter; the Capital One CSV adapter X-f6b's own
entry defers ("worth minting once the card ledger exists") stays the manual fallback. Either way the
lines land in the ONE inbox on the card account: purchase lines match card movements (3.2) or mint
them (`mint_uncategorized`, the one door, into the envelope a merchant rule names -- `R-GK`'s "an
envelope fills from the bank"; the minted purchase takes the STATEMENT's account and the envelope
stays on the budget account, the two `statement_match` readers 3.2 names); payment lines are
disposed TRANSFER against the transfer's card leg (`X-gl`); interest lines match the finance-charge
row; reward credits match a redemption; the feed's closing balance is a level. These are bank_import
steps, not card steps, and they are what make the card's history real.

## 4. What this deletes

`credit_workflow.py` (666 lines) and `entry_credit_workflow.py` (414) whole; the `Credit` status as
a reachable state and, after 3.10, as a stored one; `TransactionEntry.is_credit`,
`credit_payback_id`, `Transaction.credit_payback_for_id`, `uq_transactions_credit_payback_unique`,
`idx_transactions_credit_payback`, the third term of `ck_transactions_one_pricing_link`;
`ck_transaction_entries_card_purchase_clears_nowhere`; the composite
`fk_transaction_entries_parent_account` (a plain parent FK remains); the `is_credit -> 0.00` arm of
`movement_cash_leg` and `credit_entry_sum`; the `transfer_id`-branches in the mark/unmark routes and
`credit_workflow.py:367` (3 of X-bi-6's census of 20: `mutations.py:868`, `:901`, and the module);
the "CC Payback" category as a system device; `EVT_CREDIT_*`; every template and JS branch on the
Credit badge; the 2026-07-19 plan's `charged_from_account_id`, `card_charge_for_id`,
`is_card_tender`, `system_origin_id`, `ref.transaction_origins`, the ghost-row derivation, the
card-side aggregate row and its 2x2 sync matrix, `_revolving.py` and the REVOLVING kind -- none of
them built.

**Rulings and steps this re-opens, named**: R-CC1 and R-CC9 (re-opened by R-CC15 on 2026-09-18);
R-CC5 and R-CC6 (3.10); R-CC7 (the rename becomes a delete, 3.2); the step `CC0a`'s REVOLVING kind
(3.1), `CC1a`/`CC1b` (dissolved by R-CC14), `CC2b`'s wait (it becomes CC-3i behind
`recurrence:R16-d`, 3.6), `CC3a`-`CC3c`'s shapes (3.2), `CC4c`/`CC4d`'s sync-maintained row and
typed FK (3.6), `CC5a`'s `system_origin_id` (3.7); `X-bi-6`'s NO ACTION restore clause (3.2). R-CC2,
R-CC4, R-CC8, R-CC10, R-CC11, R-CC12 and R-CC13 (the per-leaf trace standard) stand; R-CC3 stands as
AMENDED by R-CC18 (3.5) with its finance-charge clause deferred by R-CC19 (3.6).

## 5. What it costs, and the order (the re-minted leaves)

Every step additive-first, each commit green and revertable; money movers own their PR. Waits on the
balance chain are REAL ones only (the trace's standard, `HANDOFF-credit-card-trace-2026-09-18.md`).
Ranks are the board's and are quoted nowhere here; the `waits on` column is the dependency graph the
cards' blocked-by links hold.

| leaf | what | waits on |
|---|---|---|
| CC-1 | `has_revolving_credit` + the type CHECK + `is_revolving`; seed + migration; the liability band reads the fold forward (3.1) | none |
| CC-2 | `credit_card_params` + migration + the setup flow (3.4's table, C13-c's key; schema with percent -> fraction) | CC-1 |
| CC-3 | `card_statement.py` pure (cycle, sequence, due date, minimum, grace) + the card APR loader and write route on `rate_history` (3.4) | CC-2 |
| CC-3i | the finance charge as R16-d's `accrued_interest` under the `DAILY_ACTUAL_365` member of R16-d's ONE convention table, per constant-balance segment (3.6) | `recurrence:R16-d` (which gains the member at the re-mint tick; it keeps its O6 rank, R-CC19), CC-3 |
| CC-4 | the paycheck's plan items across "checking and its cards" (R-CC16): ONE predicate -- the owner's default grid account plus its active revolving accounts -- read by the grid, the dashboard's upcoming bills, the spending report and the calendar; the composed subtotal and its `elsewhere` term on the reconciliation row; the balance line any member of the set; the account on create; the chip per CELL (3.3) | CC-1 (`is_revolving` defines the set); nothing on the balance chain |
| CC-5 | the movement-on-card doors: settle-with-tender (the door takes the movement's day, default the act's day, basis `entered`, refused on or before the card's opening) and the card purchase entry; the parent-account key re-cut to a plain FK; every reader in 3.2's census re-pointed onto the movement's account, the entry reduction gaining its account-keyed arm BESIDE the flag's (`_amounts.py`, `_cash_leg.py`: the balance and bank-import lanes' files, coordinated at build); the ownership gate against the ROW's owner; the movement chip (3.2). The flag itself survives to CC-7 | `X-bi-4`, with BOTH halves of the fork ruled on its entry: the key relaxed AND the fold's movement predicate `TransactionEntry.account_id` |
| CC-6 | the payment (R-CC18 as amended by R-CC22): `card_payment_settings` with the four modes and a unique key over the card; the transfer setup flow, seated under R7f once ruled (else this leaf's loop rules the seat); the CARD amount rule pricing at the end of the day before the row's day less what is not yet inside that balance, floor zero, with its injected deriver; a mode switch as a recurrence rewrite plus regeneration; the typed-over row as the partial payment; the placed one-off as an extra payment; the payable on checking's grid; the underpayment notice (3.5). Owns **N-311** | CC-3, CC-5 |
| CC-7 | the cutover of the cheat (3.10; R-CC17 as amended by R-CC20 and R-CC21): every frozen transaction-level pair by the total rule copying day, figure, both bases and the clearing link from the payback's covering movement, with the journal reversal and the match-member re-point; every envelope by the envelope clause with its correction lines; a still-live pair reported, its payback deleted, its bill settled at `$0.00`; `is_credit`, its reducer arm and `ck_transaction_entries_card_purchase_clears_nowhere` deleted once no line carries the flag; the mark/unmark doors, the `Credit` transition and the `Credit` status value retired; routes, templates and JS; `credit_workflow.py` and `entry_credit_workflow.py` deleted whole; graded on checking's cash fold by equality (`tests/manual/verify_balance_baseline.py`), on the budget clock by a stated per-period diff (`verify_grid_cutover.py`) and on the posted ledger for the reversed legs. **MOVES MONEY on the budget clock, `$0.00` on checking's cash fold.** Closes **CC-352**, **N-350**, **N-351** (credit_card), **N-337** (re-homed; its remedy re-cited from R-CC1 to R-CC15/R-CC18 and `X-gl`'s TRANSFER verb) | CC-5, CC-6 |
| CC-7o | the RELEASE runbook (R-CC21; `salary:R18-d`'s shape under R-HJ): the prerequisite act (the card paid in full, every payback marked paid, no new charges), the release, the card created through the account door with its books opened on the payment day at that day's balance verbatim, the payment definition set up, the first cycle on the feed reconciled; no per-row door act | CC-7, `X-f6b` |
| CC-8 | the finance-charge definition and its rule (3.6); `template_id` is the pricing link R-IY names, so no fourth FK. Closes **N-264** | CC-3i, CC-6 |
| CC-9 | rewards accrual and redemptions (3.7); no `system_origin_id` | CC-6 (ruled the tail by the 2026-09-15 order) |
| CC-10 | the refusals (3.8) at both transfer doors; the `revolving` filter | CC-1 |
| CC-11 | the card cockpit and the grid affordances through the design loop (3.9) | CC-6 |
| BI | the card on the feed (3.11), bank_import's steps: `X-f6b` carries it and CONFIRMS that SimpleFIN serves a card's lines and balance (an external property nothing on the tree asserts); card lines disposed under `X-gl`'s verbs; the two `statement_match` readers of 3.2; the Capital One CSV adapter only as a manual fallback | CC-5, `X-f6b`, `X-gl` |

When each leaf may start is the board's answer.

**What the re-mint retires and re-homes** (the 2026-07-19 leaves as `steps.md` held them at
`cd242edc`):

| old leaf | becomes |
|---|---|
| `CC0a` (the flag + the REVOLVING kind + a classifier branch) | CC-1, without the kind (3.1) |
| `CC1c` (the liability band) | CC-1 (3.1: the one seam surface) |
| `CC0b` (params, inert), `CC0c` (the setup flow + a REVOLVING redirect) | CC-2, without the redirect |
| `CC2a` (the pure cycle), `CC2c` (APR on `rate_history`) | CC-3 |
| `CC2b` (the finance charge over APR segments) | CC-3i, behind `recurrence:R16-d` (R-CC19) |
| `CC3a`, `CC3b`, `CC3c` | CC-5 (the doors; the flag deleted, not renamed: R-CC7 re-opened), CC-7 (the cutover), CC-7o (the runbook) |
| `CC3d` | CC-10 |
| `CC4a`, `CC4b` | CC-6 |
| `CC4c` | CC-6 (the notice) and CC-8 (the finance-charge definition; no sync-maintained row) |
| `CC4d` (a typed FK for the charge; closed **N-264**) | DISSOLVED: `template_id` on a card-owned definition is the link R-IY names; **N-264** moves to CC-8 |
| `CC5a` (with `system_origin_id`), `CC5b` | CC-9 |
| (none) | CC-4, CC-11, BI are new |

Ledger rows re-homed: **N-350** (`CC3c` -> CC-7), **N-337** (`CC3b` -> CC-7), **N-311** (`CC4b` ->
CC-6), **N-264** (`CC4c` -> CC-8), **N-351** credit_card (`CC3b`/`CC3c` -> CC-7), **CC-352** (`CC3b`
-> CC-7). Other-arc waits re-pointed: the O2 outcome row lists the new leaves; `bank_import:X-gg`
waits on CC-5 (the entry shape) instead of `CC3c`; `balance:X-au-l`'s wait on `CC4d` DROPS, because
the finance charge carries `template_id` from birth and `amount_source_id` needs no card link to
go -- RULED DROPPED by the developer 2026-09-18 (AskUserQuestion in the coordinator session).
Registry edits the re-mint owed beyond the card rows: `recurrence:R16-d`'s sentence gains the
`DAILY_ACTUAL_365` member (rule 14: one convention table); `X-bi-4`'s README entry spells BOTH
halves of the fork (the key relaxed AND the fold's movement predicate `TransactionEntry.account_id`,
which `_events.py:750` reads as the parent's today) and its `steps.md` sentence if that changes;
`X-bi-6`'s entry loses its "restore NO ACTION" clause (3.2); `R-CC3` is recorded as amended by
`R-CC18` with its finance-charge clause deferred by `R-CC19`; `R-CC17` as amended by `R-CC20` and
`R-CC21`; `R-CC18` as amended by `R-CC22`.

## 6. The forks, in the order they were asked

1. **Q1** -- RULED B, `R-CC16` (3.3): the row's account IS the expectation.
2. **Q2** -- RULED (a), `R-CC17` (3.10): the payback was the movement; amended the same day by
   `R-CC20` (the envelope clause) and `R-CC21` (the operator step is a release with a prerequisite
   act, not a per-row door act).
3. **Q3** -- RULED (i), `R-CC18` (3.5): one recurring payment definition per card with a mode,
   priced from the fold; amends R-CC3; amended the same day by `R-CC22` (the row prices at the end
   of the day before its day less what is not yet inside that balance; a partial is the row's figure
   typed over).
4. **Q4** -- RULED, `R-CC19` (3.6): the card's interest waits its turn in O6. As asked on
   2026-09-18, in the order of that day: whether `recurrence:R16-d` -- then O6's third leaf, after
   `R16-c` -- is pulled UP the order so the card's finance charge can land, or the card's interest
   waits its turn. A RANK decision at the re-mint; the cost of pulling it up is R16-c first (the
   loan's past and future as one event stream, MOVES POSTED MONEY, own PR), the cost of waiting is a
   card whose grace-lost cycles carry no projected charge until O6 reaches it.
5. **Q5, Q6, Q7** -- the three amendments above, asked after the second neutral review measured the
   ruled texts against the code and the population (section 8).

Design elements stated rather than asked, each revisitable on objection: the flag not the kind
(3.1); the key re-cut and the movement-account predicate as what R-CC15 asks of X-bi-4 (3.2); the
set "checking and its cards" as one predicate read by every plan-item reader (3.3); the finance
charge and redemption as rows of card-owned definitions rather than sync-maintained rows with a
`system_origin_id` (3.6, 3.7); `card_payment_settings` as a settings row off the transfer template
rather than a mode on the params, with a unique key over the card (a card can hold a placed one-off
beside its recurring payment, and the mode is the recurring template's).

## 7. What was measured (production, read-only, 2026-09-18 07:20-09:40 EDT; alembic `9c1e4b7a2d3f`)

Accounts: 9, none of type Credit Card (Checking, HYSA inactive, Mortgage, Roth IRA, Traditional IRA,
401(k), Van Loan, Money Market, Home). `Credit`-status rows: 19 live, 0 soft-deleted, `$5,419.16`
stated on 17 (two are derived rows with no stored figure), due dates 2026-04-24.. 2026-09-10, 13
undated, 14 link-less; in 10 periods 2026-05-07..2026-09-10. Paybacks: 16 Paid with a Credit source
(`$4,629.16`), 12 Paid with a non-Credit source, i.e. entry-level (`$2,315.63`), 3 Projected with a
Credit source (`$970.00`: source 2859 undated in period 2026-08-27 with payback 2860 `$690.00` in
2026-09-10; source 2731 due 2026-09-07 with payback 2883 `$200.00` in 2026-09-24; source 2881 due
2026-09-10 with payback 2882 `$80.00` in 2026-09-24); paybacks settled 2026-05-14..2026-09-11; 30
journal legs stand on the paid paybacks. Entries: 92 non-card (`$6,928.21`), 20 card-tender
(`$2,372.75`, 2026-05-04..2026-09-07) in 12 envelopes, ALL Paid on the purchases basis
(`settled_amount` NULL, settled 2026-05-13..2026-09-07), all with a Paid payback; envelope 2275
purchases `$49.52` vs payback `$50.80`, 2276 `$181.58` vs `$123.18`, the other ten equal. Statement
imports: 1 (2026-01-02..2026-07-17). The census SQL is in the session's scratchpad
(`card_population.sql`, `card_population2.sql`, `card_population3.sql`), every statement a SELECT.

**Part 4, the same day after the second review (alembic `d2e9f4a17c63` by then: a release had
landed), `card_population4.sql`, read-only:** of the 28 Paid paybacks, 0 carry `reconciled_by_id`
and 0 have a `statement_match_members` row, so nothing a delete would CASCADE away exists today.
None of the 20 card-tender entries carries a `settled_on`. Envelope 2276's payback (`$123.18`)
equals exactly ONE of its two lines (`$123.18` on 05-31; the `$58.40` line of 05-30 was never paid
back through a payback row). Frozen pairs against a candidate opening day C (charge dated on or
before C -- an undated source dated at its period start -- with the payback settled after C): for
any C from 2026-07-31 through 08-04, ZERO straddle and every live row is after C; for any C from
08-20 through 08-26, TWO straddle, `$148.15` (2837 `$52.22` and 2839 `$95.93`, both undated in the
period starting 08-13, paid back 09-11); for C on or after 09-11, zero straddle but all three live
rows (`$970.00`: 2859 dated 08-27 by period, 2731 due 09-07, 2881 due 09-10) are on or before C. The
four frozen charges dated after 08-20 total `$510.18` (2856 `$108.11`, 2861 `$300.00`, entries 104
`$62.62` and 105 `$39.45`) and the six 09-11 paybacks `$658.33` (those four plus the two
straddlers).

## 8. What the neutral adversarial reviews refuted, and what changed

Reviewed 2026-09-18 by a fresh subagent, read-only, against dev `b1d083b2`. REFUTED: (1) "the fold
partitions movements by `account_id` and needs no card branch" -- the fold reads a movement on its
PARENT's account (`_events.py:750`) and X-bi-4's spec does not say otherwise; 3.2 now states the
movement-account predicate as what R-CC15 asks of X-bi-4, and CC-5 waits on it. (2) "a charged bill
clears on the CARD's statement line by line" -- today its covering movement is never a candidate and
the ROW is offered on checking at full value; 3.2 carries the readers census and the bank-side leaf
owns the matcher. (3) Option A's "the grid: unchanged" -- the cells (`page.py:311`) and the
subtotals (`_budget_legs`) would part; both A and B need the `elsewhere` term. (4) "graded by
checking's fold equality" -- true on the cash clock only; the budget clock and the posted ledger
move, and 3.10 now states and grades both. OVERSTATED: option B's cost ("the largest UI change the
app has had") -- the grid is already keyed by paycheck, the account is a one-line filter, the
keyboard model and popovers carry no account; the recommendation moved from A to B on that
measurement. MISSED and added: seven readers that assume a movement's account is its parent's; the
ownership gate at the tender door; the deleted paybacks' journal legs; the boundary trigger reading
`settled_on` strictly, so the opening is dated against the oldest DATED day; R16-d's single-balance
signature consumed per segment; R-CC5, R-CC6, R-CC7 and CC4d named as re-opened; X-bi-6's NO ACTION
restore made moot rather than "a clause"; R-CC14/R-CC15 cited as PR #399's.

**The second review (2026-09-18, after the four rulings, by a fresh read-only subagent against dev
`1431c44a`, then a second one over the three amendment drafts), graded the four option texts that
had become rulings hardest.** REFUTED in them: (1) R-CC16's supporting claim that
`is_cash_flow_account` gates out loans and investments -- it is only "not amortizing"
(`account_resolver.py:90-91`) and admits the IRAs and the savings accounts, so 3.3 names a predicate
of its own; (2) "the fold is untouched" under R-CC16 once section 4 deletes `is_credit` -- the entry
reduction (`_amounts.py:509-518`) and four `_cash_leg.py` sites use the flag as the proxy for "money
not on this account", and without a re-key an unposted card swipe floors checking's reservation, the
double count 3.3 exists to prevent; (3) R-CC17's sentence for the 12 envelopes (a purchases-basis
row carries no covering movement) and its operator step (a day paired with another day's figure,
`R-HG`; straddlers; an invented `entered` basis); (4) R-CC18's subtraction clause in
`every_paycheck` mode (a double count; an `$800` paycheck; a self-including fold; a late-paid row
paid twice). OVERSTATED: the X-bi-4 README entry, which records the KEY half of 3.2's fork and not
the fold-predicate half; R-HJ cited for a reason it does not give (the live rows cannot convert in
the migration because no card account exists then, `R-BAL40` permitting the total rule); X-cd's
direction (it KEEPS the typed-figure bound); `is_payroll_deduction_funded` as "type metadata" (an
enum tuple whose comment asks for a schema flag; `:137`, not `:152`); "2 of X-bi-6's 20" (3). MISSED
and added: the three other one-account plan-item readers (the dashboard's bills, the spending
report, the calendar); the chip is per cell, not per row; the balance-line override outside the set;
the companion's swipe under an ownership gate on the caller; seven more `is_credit` readers and the
RETAINED-conflict arm of the maintain pass; the `statement_match_members` CASCADE on a deleted
payback (0 today); the `has_parameters` CHECK reading; CC-4's wait on CC-1; CC-7's graders; the
ledger owners and cross-arc waits the re-mint re-points; "C7 shape" and "C-19 lock shape" as
undefined identifiers; every rank in 2.1 stale (now quoted nowhere); the SimpleFIN card claim as an
external property the tree does not assert; the first derived payment after the opening paying the
card's whole balance. What the drafts of the three amendments lost to the second grader before the
developer saw them: a REFUSING migration (blocks the deploy); "the budget clock unchanged" (the
twelve payback rows leave the next period in every option); an opening "less the straddlers" (the
plug `R-HG` forbids, over a set undated rows cannot define); a deleted plan row as the only way to
hold a live charge inside the opening; a partial rule with an `$800` path and a late-paid double
pay; and worked figures that did not reproduce from the census (four post-Aug-20 charges, not six).
The population was re-measured for it (section 7, part 4).

**A third review, over this document as folded (the same day, before it was handed to the
coordinator), refuted and the fold corrected**: the `is_credit` deletion claimed by three leaves,
which at CC-5 would have moved the 12 Paid envelopes' cash legs on checking by up to `$2,372.75` on
a leaf not marked a money mover and destroyed the fact the envelope clause reads (the flag and its
arm now go at CC-7); the rewards formula, which read `-$5.00` after a partial redemption (one walk
over the whole history now); the CC-352 key (`fk_transactions_credit_payback_for`, not
`template_id`); the part-4 census quoted as post-3d when it ran before release #405 (CC-7 re-runs
it); the door's day rule stated in a leaf and not in 3.2; the `Credit` status value with no owner;
`off_statement_sum` and `_walk.py` mis-cited; two stale ranks; a `starts` column restated against
rule 16; R-CC13 missing from section 4; and the flag census conflating the STATUS predicate with the
FLAG. What it confirmed: all seven ruling texts verbatim against their sources, every figure of
section 7 reproducing from the census, every worked example's arithmetic, and every citation added
by the fold resolving on `cd242edc`.
