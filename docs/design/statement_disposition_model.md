# The statement disposition model

The from-scratch design of what a bank line ENDS ON, written after the developer ruled on 2026-09-04
that the model below is the one to build rather than the one shipped. It is a Loop A argument: the
audit of what exists, the design, what the design DELETES, the screen and the words the owner reads,
the limits the design does not reach, and the forks still owed a ruling. It mints no steps and
states no order -- the tracker owns both.

It is the second Loop A over this feature. The first (`docs/design/bank_import_audit.md`, locked at
round 4 on 2026-08-29) redesigned the SCREENS and deliberately left the layers underneath alone, on
a verdict that measured them good. That verdict still holds for the matchers, the import record and
the receipt. What this document is about is the one layer that audit did not open: where an ACT is
written down.

Last evaluated: 2026-09-04, against `origin/dev` at `029c36ed`.

## Method

- Read in full: `app/services/statement_match/` `_verbs.py`, `_panel.py`, `_cards.py`, `_batch.py`,
  `_skipping.py`, `_undisposed.py`, `_bars.py`, `_rules.py`, `_leftovers.py` (the `Leftovers` value
  and `leftovers`), `_reads.py` (the `ReviewSet` value and `review_set`), `_filing.py`
  (`RuleFiling`, `rule_filed_acts`), `_candidates.py` (`act_still_names_a_row`); the models
  `merchant_rule.py` and `statement_line_skip.py`; `app/routes/accounts/statement_reconcile.py` and
  `_statement_doors.py`; `app/schemas/validation/statement_reconcile.py` and the batch schema in
  `statements.py`; the reconcile templates and `_merchant_rule_macros.html`.
- Every measurement quoted below is CITED to the site that recorded it and carries that site's own
  date. Nothing here was re-measured on 2026-09-04, and no figure in this document is mine.

## The verdict, in plain language

The app has one true idea and five partial spellings of it. The idea is ruling **bank_import:R-HP**:
*every bank line ends on exactly one of MATCH, ADD, TRANSFER or SKIP, and the inbox is the lines
with none yet.* That is right, and everything below is about the fact that no row in the database
says it.

Because no row says it, "this line is answered" is a PREDICATE assembled from two tables and
filtered by a third condition, and every consequence of that has had to be paid for separately: an
exclusivity invariant no key can carry, a cleanup enumerated at five doors that its own docstring
says a sixth will forget, an authorship column on one store and not the other, a receipt that
structurally cannot itemise one of the four verbs, and a third card kind on the screen.

The remedy is one row per bank line saying which verb it ended on and who decided. It is not a
tidy-up: it converts three fences into one key, and it is what makes the fourth verb expressible at
all.

## 1. What is here now

### 1.1 Five spellings of one vocabulary

| spelling | site | members | what it says |
|---|---|---|---|
| `Verb` | `_verbs.py` | 4 | what the card offers per line |
| `RuleAnswer` | `_rules.py` | 5, derived from which column is non-null | what a merchant's lines end on, standing |
| `LinePipeline` | `_rules.py` | 2 | which door an unexplained line is a candidate for |
| `AddAct` | `_panel.py` | 2 | which control the ADD tab renders |
| the stores | 2 tables | -- | where the act is written down |

`LinePipeline` and `AddAct` are ONE partition written twice. `pipeline_for` routes a line into
`creatable` or `recordable_inflows`; the card builder for each then states `AddAct.PURCHASE` or
`AddAct.INCOME` (`_cards._creatable_card`, `_cards._inflow_card`). The second is derived from the
first by construction, which is `CLAUDE.md` rule 14's tell rather than its exception: two homes that
agree today because one is downstream of the other.

`RuleAnswer` is the one that mixes GRAMMARS. Three of its five members name a verb and its argument
(`TEMPLATE`, `NEW_ENVELOPE`, `INCOME_CATEGORY` are all ADD, with three containers). One names no
verb at all: `NEVER` is a REFUSAL, and ruling **bank_import:R-JH** is that discovery made the
expensive way -- `never a purchase` had been filed among the dispositions, the Skipped tab held
lines nobody had disposed of, and plan step `bank_import:X-gj-4c-1` had to move them back to the
inbox. The fifth, `ALWAYS_ASK`, is a decision to have no standing answer, correctly distinct from an
absent row.

### 1.2 Two act stores, and what each one costs

MATCH and ADD land in `budget.statement_matches` with `budget.statement_match_members`, carrying
`applied_by_rule` (ruling **bank_import:R-GT**). SKIP lands in `budget.statement_line_skips`, which
carries `id`, `bank_statement_line_id`, `account_id`, `user_id` and `created_at` and no authorship
column. TRANSFER lands nowhere.

| what it costs | site | in its own words |
|---|---|---|
| Exclusivity is not a key | `_skipping.py` | *"no CHECK can carry it"* -- the pair spans two tables, so it is held by two app-tier reads (`answered_by_a_match`, `skipped_among`) plus a `FOR NO KEY UPDATE` lock in both doors, because at `READ COMMITTED` two tabs otherwise interleave into a line carrying both answers with nothing raising |
| Explained is not a row's existence | `_candidates.act_still_names_a_row` | deleting the app rows a match names leaves the act holding its line forever, so every read filters on an `EXISTS`, and `match_withdrawal` cleans up at five enumerated doors: *"a sixth door written next year will not know to call it"* |
| Authorship is per-store | `statement_line_skip.py` | the column ruling **R-GT** requires exists on one store and not the other |
| The rule receipt cannot itemise a skip | `_filing.rule_filed_acts` | it selects `StatementMatch.applied_by_rule.is_(True)`; a skip leaves no `StatementMatch`, so it gets no receipt item and no one-click undo -- which is exactly what rulings **R-GH** and **R-GG** promise |
| Undisposed is two anti-joins | `_undisposed.undisposed` | *"two acts are two facts"* |
| The screen grew a third card kind | `_cards.CardKind` | it *"replaced a BOOLEAN"*, `Tab.holds_settled_acts`, because a recorded skip is an act with no `StatementMatch` behind it and a bank line the reader can print |
| Two undo doors | `statement_reconcile.py` | *"Six routes"*, two of them undos over two tables with two schemas |

### 1.3 The measured ground under `NEVER`

`app/models/merchant_rule.py` records the case the answer exists for: Capital One Credit Card is 9
of the 91 unexplained outflows and `-$7,412.94` of the `-$11,336.36` in that list (measured
2026-08-19), and every one must never become a purchase because the app already holds that money as
CC Payback rows. `_bars.py` records the same shape one layer over: nine Capital One ACH payments
became purchases in eight `$0.00`-budget envelopes holding `$7,412.94`, past a warning paragraph.

That is the strongest case for the answer, and the model's own docstring says what it really is:
*"when the credit card arc gives Capital One its own account, the Checking-side line stops being
'not a purchase' and becomes a payment to MATCH."* `_bars.py` says it of its own bar too, calling
`PAYS_AN_ACCOUNT_YOU_HOLD` interim with a clean seam. So on the measured data `NEVER` is standing in
for a verb the app cannot yet offer.

It does not follow that the answer is worthless. An owner often knows a line is not spending without
knowing what it IS, and refusing a wrong door without naming the right one is a real, honest,
partial answer. What follows is that it is on a different AXIS from the dispositions, and putting
the two axes on one control is what `R-JH` had to correct in prose.

## 2. The design

### 2.1 One disposition per bank line

```text
budget.line_dispositions
  id
  bank_statement_line_id   NOT NULL, UNIQUE      <- R-HP, as a key
  account_id, user_id
  verb_id                  NOT NULL -> ref.statement_verbs (match|add|transfer|skip)
  applied_by_rule          NOT NULL              <- R-GT, for all four verbs
  group_id                 NULL except MATCH/ADD -> budget.statement_match_groups
  created_at
```

The ARGUMENT stays with the verb that has one: the member rows for MATCH, the created transaction
for ADD, the paired line for TRANSFER, and nothing at all for SKIP. `budget.statement_match_members`
is already three quarters of this table -- one row per (line, act) with the act's app-side
subject -- so the move is to GENERALISE the row the app already writes, not to invent a table beside
it.

Three properties, in the order they matter:

1. **`R-HP` becomes a UNIQUE key.** *Exactly one verb per line* stops being an invariant two doors
   maintain and becomes a constraint the database refuses to break. See section 5 for the one
   condition this depends on, which is real and is not free.
2. **Authorship is one column for four verbs.** Every act a rule performs is labelled as the app's
   wherever it renders, and the import receipt itemises all four kinds through one reader.
3. **Undisposed is one anti-join.** `undisposed()` becomes
   `NOT EXISTS (a disposition for this line)`.

### 2.2 The rule states a disposition, plus a refusal

`budget.merchant_rules` stops deriving five answers from which column is non-null and carries two
independent things:

- **a disposition (nullable)** -- the verb this merchant's lines end on, plus that verb's argument.
  ADD takes a template, a new envelope, or an income category, which is exactly today's three. SKIP
  takes no argument, which makes it the CHEAPEST member in the set: no foreign key, no cascade, and
  none of the archived-option round-tripping the template and category answers need
  (`_merchant_rule_macros.html` carries two such arms today). TRANSFER takes the other account, when
  the card arc makes that real.
- **a refusal** -- *this merchant's lines are never spending.* Today's `never_a_purchase`, moved
  onto its own axis.

`ALWAYS_ASK` survives unchanged as *I have decided to have no standing answer*, and the absence of a
row survives as *I have not said*. The model docstring's argument for keeping those two apart is
untouched by any of this.

What this buys beyond tidiness: the card arc's arrival becomes a DATA change (restate those
merchants as TRANSFER) rather than a schema change, and ruling `R-JH` stops being a rule a future
reader has to remember, because the two axes are two fields.

### 2.3 The consent

`ReviewedBatch` today is one value carrying a `consent` plus act lists, with `__post_init__`
policing which combinations are legal -- two homes and a maintained contract, and every new act
class adds a sentence to the contract.

Two values instead: a TICKED pass carrying all four act lists, and a RULE pass carrying only the act
classes ruling **R-GH** consents to. A rule pass cannot name an act it may not perform because the
field is not there, and `__post_init__` deletes whole.

Under 2.1 this also stops being a policy question. A disposition row records WHO, so a rule-filed
skip is representable, labelled, visible on the receipt and undoable in one click. The product does
want one: ruling **bank_import:R-JZ** (developer, 2026-09-04) ships a skip rule in exactly the shape
section 6.1 works through, which is why that example is a specification and not an illustration.

## 3. What the design deletes

Not "simplifies". Deletes.

| deleted | what it is today |
|---|---|
| `_skipping._line_on`'s `FOR NO KEY UPDATE` lock, and the interleaving argument beside it | the app-tier half of `R-HP` under concurrency |
| `_undisposed.answered_by_a_match` and `_undisposed.skipped_among` | the two directions of the same exclusivity |
| one of the two `NOT IN` terms in `_undisposed.undisposed` | *"two acts are two facts"* |
| `_candidates.act_still_names_a_row` and most of `app/services/match_withdrawal` | see section 5: conditional |
| the second undo door, its schema and its route | one act, one undo |
| `_cards.CardKind`'s third arm | one card, with a verb-keyed argument block |
| `AddAct` | `LinePipeline` is the same partition, and the disposition names it |
| `ReviewedBatch.__post_init__` | two types |
| ruling `R-JH` as a thing to remember | two fields |

## 4. The screen, and the words

The failure to fix is that the app teaches TWO VOCABULARIES. The card asks *what is this?* and
offers four verbs (`_statement_reconcile_macros.html`, the verb strip). The merchants page asks
*where does it go?* under a column headed **Goes in**, with a single `<select>` mixing places
(templates), an action (`-- a new envelope --`), a direction-scoped group (`income: Dividends`) and
two meta-answers (`-- ask me every time --`, `-- never a purchase --`). Someone who has learned the
card cannot read the rule page.

One vocabulary, four words, every surface:

```text
Amazon                                          26 lines, last 2026-08-19

  What is this, from now on?
  ( ) Add it to my budget     ->  [ Groceries          v ]   or  [ + a new envelope ]
  ( ) It is income            ->  [ Interest income    v ]
  ( ) It is a transfer        ->  [ Capital One Card   v ]
  ( ) Skip it, it explains nothing I budget for
  ( ) Ask me every time

  [ ] Never spending, but keep asking me what it is
```

Four radio answers that are the card's four tabs, in the card's order and the card's words; one
checkbox on the OTHER axis, visibly not one of the four; the argument revealed beside the answer
that takes one. The tab bar already reads
`To explain - Explained - Filed by rules - Transfers - Skipped`, so the whole feature would name one
thing one way.

Three further rules for the surface, each with a reason:

- **State a rule where the decision is made.** Ruling **R-IB** already moved the offer onto the
  RECEIPT, once per merchant, about what actually landed. That stays. The merchants page becomes
  where standing answers are REVIEWED, not where they are first stated.
- **A standing answer says what it will do next**:
  *"Skip. 3 lines waiting, the next import will file them"* rather than a bare phrase, so a rule is
  legible before it fires rather than after.
- **The app's decisions are never dressed as the owner's.** One authorship column splits every
  settled tab the way `Explained` / `Filed by rules` already splits, and the Undo wording stops
  saying *you decided* over something the owner did not decide. Today the Skipped tab's Undo dialog
  says exactly that, and it would be false for every rule-filed skip.

## 5. What this design does NOT fix

Stated because a design that claims a fence is deleted, and leaves it standing, is worse than one
that keeps it.

**The UNIQUE key in 2.1 replaces the exclusivity fence only if a disposition that has stopped
claiming is DELETED rather than filtered.** Today a match whose app rows were cascade-deleted
survives as a zombie and `act_still_names_a_row` filters it out of every read. Under the new shape
the same cascade must remove the disposition, or a stale MATCH row will block a later SKIP on a line
that is genuinely unanswered. A per-line foreign key does not do it, because a group names several
rows and losing one of them diminishes the act rather than ending it; what is needed is that a group
holding NO app-row member ceases to exist, which is an `AFTER DELETE` trigger on the members.

That is a fence in the database rather than an absence of one. It is still strictly better than what
is there -- it is TOTAL where `match_withdrawal`'s five enumerated doors are not, and its own
docstring names the three doors it cannot cover -- but it is a trigger, and this document does not
pretend otherwise.

Without that piece the design still buys authorship, one receipt, one undo, one card kind and one
vocabulary. It does not buy the key.

## 6. The forks still owed a ruling

1. **Does the trigger in section 5 ship with the container, or is the zombie filter kept?** The
   first costs a trigger and a migration; the second keeps `act_still_names_a_row` and gives up the
   key, which is most of the reason to do the work.
   **A third option since `bank_import:R-BI18` (2026-09-18): neither** -- `X-hc` makes the state
   unrepresentable (the members' row-side keys NO ACTION, every moving door refusing while the act
   stands), so there is nothing for a trigger or a filter to catch; `X-gl-2` waits on `X-hc`.
2. **Does `never a purchase` migrate to a refusal flag, or is it re-stated per merchant?** On the
   measured data (2026-08-19) the developer holds 30 standing rules and the answer is load-bearing
   on the Capital One family; a migration is mechanical, but if those merchants become TRANSFER
   under the card arc the refusal may have no members left.

*Fork 1 was **does a SKIP rule ship**, and it is RULED*: it does, with rule-authored skips shown
SEPARATELY -- disposition rows carrying `applied_by_rule = true`, rendered under
*Skipped by your rule* rather than *Skipped*, itemised on the import receipt with the one-click undo
**R-GG** built, and EXCLUDED from `filed_total`, because a skip names no created subject. That is
section 6.1's second panel, which stops being a hypothetical and becomes what `X-gl-3` and `X-gl-5`
build (developer, 2026-09-04, ruling **bank_import:R-JZ**). The case against was recorded and lost
on its merits: a skip rule is the only standing answer whose effect is INVISIBLE, an absence from
the inbox while the books-versus-bank difference the hero reports does not move -- and the remedy
taken is to make that absence VISIBLE on a surface of its own rather than to refuse the rule, so the
ruling is honoured only where that surface ships with it.*

*Fork 4 was **does this become a new ARC or a `bank_import` container**, and it is RULED*: a
decomposed container inside `bank_import`, `X-gl`, leaves `X-gl-1..n` (developer, 2026-09-04,
recorded in ruling **bank_import:R-JY** rather than as an id of its own).

### 6.1 Worked example: a SKIP rule on three lines

An import records three fresh lines from one merchant: `+$52.31` (2026-08-14), `+$18.75`
(2026-08-17) and `+$140.00` (2026-08-19) -- a posting the bank later reversed, so they explain
nothing the owner budgets for.

**Today, if the batch value admitted a rule-consented skip.** Three rows land in
`statement_line_skips`, indistinguishable from presses. `RuleFiling.filed_total` sums
`AppliedItem.amount` over what landed and reports `+$211.06` under
*what the bank moved on the lines a rule filed* -- a rule filed nothing and created no row.
`rule_filed_acts` returns none of the three, so there is no receipt item and no one-click undo. The
inbox drops three cards. The hero's *off by* figure does not move, because a skip closes no
difference and `bank_agreement` goes on reporting all `+$211.06` as a disagreement. Net: three fewer
cards, no press of the owner's, and no surface that says so.

**Under this design.** The three disposition rows carry `applied_by_rule = true`. They render on
*Skipped by your rule*, not on *Skipped*. The import receipt itemises all three with the one-click
undo ruling **R-GG** built. `filed_total` excludes them because a skip names no created subject. The
inbox drops three cards, the *off by* figure still does not move, and the receipt says both things.

**Under a ticked press** (which is what plan step `bank_import:X-gj-4b` builds on today's store).
The owner opens each card, leaves it on the SKIP tab, presses OK and presses Apply. The receipt
reads *3 bank line(s) marked as explained by nothing. This closes no difference between your books
and your bank*, itemised as
*Skipped the $52.31 your bank paid in on 2026-08-14, as explained by nothing*.

## 7. Loop B

The decomposition and its order are the tracker's alone; what belongs here is what each piece IS, so
that this document and the cards cannot describe different work.

- **The store.** `budget.line_dispositions`, its migration and its backfill from the two existing
  act tables, with the readers repointed. It is the whole of section 2.1 and it moves no money: a
  disposition records what already happened.
- **The zombie cascade** (fork 2). The trigger of section 5 and the deletion of
  `act_still_names_a_row` and most of `match_withdrawal`.
- **The rule.** Section 2.2: the disposition column, the refusal flag, the migration off
  `never_a_purchase`, and `RuleAnswer`'s replacement by the verb set.
- **The consent.** Section 2.3: two batch types, and `__post_init__` deleted.
- **The screen.** Section 4: the merchant control rebuilt on the four verbs, the settled tabs'
  authorship split extended to SKIP, and `AddAct` deleted.
- **The skip rule** (ruling **R-JZ**): the SKIP disposition a merchant rule may state, its
  *Skipped by your rule* surface, and its exclusion from `filed_total`.

**The container is `bank_import:X-gl`** and its leaves are the pieces above; the board ranks them
and this document does not.

**Sequencing, ruled 2026-09-04 (**bank_import:R-JY**)**: ship `bank_import:X-gj-4b` first on the
store as it stands, then the release ruling **bank_import:R-JK** requires, then `X-gl` ranked beside
the card arc -- because TRANSFER is the verb the disposition exists to admit and the card arc is
what makes TRANSFER real. Nothing in `X-gj-4b` above the store changes under this design: the batch
schema's list, the payload reader, the apply arm and the SKIP pane all carry over, and only
`skip_line` and `unskip_line` are rewritten. Lighting the verb adds ROWS to a table the backfill
already has to read; it does not add a table.
