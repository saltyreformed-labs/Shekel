# Bank Import Audit

Diagnosis of the five bank-import screens under `/accounts/<id>/statements/` ahead of a rebuild,
written after the developer said on 2026-08-29 that the feature as shipped is one he will not use.
It is the per-screen audit artifact the `shekel-design` skill requires before a Loop A: what each
surface should show, what the code actually produces, the divergence, and a keep / fix / remove /
merge verdict. It also records the three findings that are NOT presentation problems and would keep
the queue from ever emptying under any skin, the field research the from-scratch design is grounded
in, and the rulings the developer has to make before mockups are worth drawing.

Last evaluated: 2026-08-29, on the dev database (378 recorded bank lines, 221 accepted matches, 30
standing merchant rules) and on the five screens rendered live through the developer's own session.

## The verdict, in plain language

**Do not scrap the feature. Scrap the five screens.** The layers underneath the screens -- the
import record, the matchers, the standing-rule store, the auto-apply door, the receipt and the
undo -- are measured good and are what the balance cutover (the cutover steps ranked behind this
rebuild) consumes. The layers on top of them were rebuilt over five days as disclosure documents
rather than as a work queue, and the queue itself holds sixteen lines that no screen can resolve
because their remedy lives in other arcs. The developer's expectation -- import, most lines match
themselves, a few exceptions by hand, done -- is exactly the design every peer product converges on,
and it is reachable here: the engine for it shipped last week and has never run on a real import.

## Method and scope

- Read in full: all fourteen templates under `app/templates/accounts/*statement*`, both scripts
  (`statement_review.js`, `statement_rules.js`), the queue, verdict, filing, gap and bar modules of
  `app/services/statement_match/`, the review and workbench routes, the arc plan
  (`docs/plans/implementation_plan_bank_import.md`), the approved redesign record
  (`docs/audits/bank_import_redesign/README.md`), every `bank_import` ruling in `rulings.md` and
  every open `bank_import` finding in `ledger.md`.
- Rendered live: the five screens at desktop width in the dark theme through `tests/manual/shoot.py`
  with the developer's saved session; the review page is 4,178 CSS pixels tall (4.6 screens), the
  register 4,448.
- Measured on the dev database: the state of every recorded line, the composition of the queue by
  kind, the payroll deposits against the app's income rows, the envelope status per pay period, and
  the shape of every standing rule.
- Research: three parallel literature sweeps over YNAB and Actual Budget (the envelope peers), Xero
  and QuickBooks Online (the mature bank-feed reconciliation interfaces), and Monarch, Copilot,
  Lunch Money, Firefly III and Tiller (the review-inbox generation), plus the design writing on
  transaction inboxes. Every claim cites its page in the research section.
- No code was changed in producing this audit.

## What is in the queue today, and why none of it is workable

The review screen lists 27 unexplained bank lines. Every one is there for a reason the screen cannot
act on, and the reasons partition cleanly:

| what | lines | money | why it is stuck | what the screen offers |
| --- | --- | --- | --- | --- |
| Capital One card payments | 9 | -$7,412.94 | Ruling R-GJ correctly bars a card payment from becoming a purchase. The books hold the money as 22 `CC Payback` rows ($6,286.46) that never sum to a payment, because a payment covers a card statement and the paybacks cover purchases. The real home is the card LEDGER (`credit_card:CC1a`..`CC1c`) and a TRANSFER (`CC3b`). | Two amber paragraphs per line, repeated nine times, and a link to build a match by hand against rows that do not add up. |
| Payroll deposits | 7 | $18,132.63 | One deposit is 2-3 income rows (Data Manager, Health Insurance Allowance, sometimes Phone Allowance) whose sum is 4-6 cents UNDER the bank's figure. That is finding N-239: `paycheck_calculator` spreads the annual rounding residue over however many periods happen to exist today (owner `balance:X-aw`). A group match must sum exactly (R-GD forbids scoring a group). Since 2026-07-02 the developer types the actual net by hand and those four deposits matched. | Two amber paragraphs per line, a "Record as income" tick that would DOUBLE the paycheck, and the workbench link: five clicks each, minting a $0.04 uncategorized row every time. |
| Real purchases already ruled | 2 | -$41.71 | Lowe's and Public Library, imported 2026-08-24, two days BEFORE the auto-apply door shipped (`X-ge`, 2026-08-26). The next import files such lines itself. | A select opening on "leave this line alone" (ruling R-FZ(b)) with the rule printed beside it. |
| Dividends | 5 | $0.79 | No rule shape covers an inflow. | Five separate "Record $0.15 as income" ticks, every import, forever. |
| Refunds (Amazon, Walmart x2) | 3 | $58.08 | Same. A refund is a negative purchase in the envelope that took the charge; both merchants already carry a rule. | "Record as income", which is the wrong act. |
| Member deposit | 1 | $200.00 | An inflow the books do not know. | "Record as income". |

Sixteen of 27 lines have no correct action on any screen in the app today; nine are trivially
rule-able but no rule shape covers them; two are already ruled. The queue grows by one payroll
deposit, about one card payment and one dividend per pay period until the root causes ship. That
arithmetic, not the prose, is why the screen feels hopeless: **the inbox cannot reach zero**, and a
queue that cannot empty is not a queue.

Two more facts frame the rest:

- **0 of the 221 accepted matches were applied by a rule.** The auto-apply door shipped 2026-08-26
  and no import has run since, so the developer has never seen a swipe file itself. His experience
  of the feature is entirely the pre-rule flow.
- **Every envelope in the last eight pay periods is closed** (`Paid`: 5, 3, 2, 3, 5, 3, 4 per
  period; 0 open). Ruling R-GU(a) lets a rule file into a closed envelope, raising what it recorded.
  For an envelope the developer ALSO fills by hand, that is a double count of every swipe he already
  transcribed. Auto-apply is therefore only honest for envelopes he stops hand-filling -- the
  process change ruling R-GK names, whose design loop (`X-gg`) sits behind the card arc.

## The screens today

Five pages plus the account's reconcile panel, reached from one another through sentences:

| route | title on screen | job | rendered height |
| --- | --- | --- | --- |
| `/statements` | Bank statements | upload a CSV; import history; recent lines; the rule receipt | 1,701 px |
| `/statements/review` | Review statement matches | the queue: proposals, then unexplained lines grouped by evidence | 4,178 px |
| `/statements/register` | What you have already decided | 30 merchant rules as a live form; 221 accepted matches with Undo | 4,448 px |
| `/statements/match` | Build a match by hand | two pick lists (27 lines, 67 rows), a running total, one button | 1,162 px |
| `/statements/agreement` | Books vs bank | five headline figures; a day-by-day table | 1,122 px |

## Summary table

| # | Surface | Location | Verdict |
| - | ------- | -------- | ------- |
| 1 | The page set itself | five routes, cross-linked in prose | merge: ONE Reconcile screen with tabs, plus a Rules settings page; Statements becomes secondary; Books vs bank stays as the report |
| 2 | Alert banner on every page | `_statement_review_body.html:41`, `_statement_workbench_body.html`, `statements.html`, `statement_agreement.html` | remove: the consequence of an act belongs in its confirm and its receipt, not above the fold of every visit |
| 3 | Proposed matches card | `_statement_review_body.html`, "Proposed matches" | fix: keep the per-class sweep (R-FZ(c)); render as rows of the one list with a solid suggestion chip, not a separate card |
| 4 | The evidence groups | `_queue.py` `_SAID`, rendered as two cards | fix: the grouping the reader needs is by VERB (match / add / transfer / skip), not by what the evidence says; the evidence becomes the chip's style |
| 5 | Per-row amber sentences | `_queue.py` `_notes_for`, `arrivals_already_held` | fix: a sentence per row is the wrong grain; state it once per signature and put the sentence one click away |
| 6 | Parked card payments in the queue | `_bars.py` ParkedLine, rendered as 9 rows | remove from the queue: a line with no available act is a HOLDING STATE (a count in the header, a Transfers tab), never inbox work |
| 7 | The destination select defaulting to "leave this line alone" | `_statement_review_body.html`, `data-destination` | fix: a JUSTIFIED suggestion (a rule, an exact or near match) is pre-filled; an untouched row is not submitted; undo is the safety. Amends R-FZ(b), which banned an ARBITRARY default and was right to |
| 8 | "What Apply will create" and "What this page did not look at" panels | `_statement_review_body.html` | move: the first is the confirm dialog's text; the second is a footer line with a disclosure |
| 9 | "What your records hold and this statement did not show" | `_statement_review_body.html`, amber alert | fix: it is the OTHER side of the reconciliation and belongs in the header's balance check, as a count with a tab, not an amber alert |
| 10 | Build a match by hand (workbench) | `statement_workbench.html`, `_statement_workbench_body.html` | merge: an inline expansion on the row (Xero's Find & Match), not a page; the two unbounded pick lists become a search |
| 11 | Merchant rules as a live 30-row form | `_statement_register_body.html`, `_merchant_rule_macros.html` | fix: a Rules settings list, one row per rule, edit on click; a rule is minted INLINE from the row that prompted it |
| 12 | Accepted matches register | `_statement_register_body.html` | merge: the "Explained" tab of the one list, with Undo on the row |
| 13 | Import form and history | `statements.html` | keep, demote: reachable from Reconcile's header; the "Most recent lines" table is the Explained tab |
| 14 | Books vs bank | `statement_agreement.html` | keep: its headline (bank vs books on the latest day) becomes Reconcile's hero number; the day table stays here |
| 15 | Rule shapes | `merchant_rules` table: template / new envelope / never / ask | extend: a GROUP rule (payroll = this period's income rows, residue onto the net row) and an INFLOW rule (dividend -> interest income; refund -> a negative purchase in the merchant's envelope) |

## Structural findings

### S1. The clutter is a design principle, not a bug

The arc adopted, and each adversarial review enforced, the rule that a screen must state every bound
it applies, every refusal in the service's own sentence, and every consequence beside the control
that causes it. The rule came from measured money defects: a warning paragraph above a working
control double-booked $7,412.94 of card payments (ruling R-GJ), and a receipt nobody could see made
a working door look inert. Each sentence on the screen closes a real hole.

The result is a screen that is half rationale. Measured on the templates:

| template | visible words, all branches | Jinja comment bytes / total |
| --- | --- | --- |
| `_statement_review_body.html` | ~1,247 | 26,334 / 54,312 (48%) |
| `statements.html` | ~474 | 5,499 / 19,624 |
| `statement_agreement.html` | ~452 | 4,020 / 13,796 |
| `_statement_register_body.html` | ~389 | 4,536 / 13,371 |
| `_statement_workbench_body.html` | ~354 | 9,172 / 17,976 |

On the review page today the two sentences "You have said Capital One Credit Card is never a
purchase..." and "This pay period already holds 2 row(s) ... your records say arrived..." are
printed, between them, sixteen times. Trimming words does not fix this; the grain does. The remedy
is a different rendering principle, stated as a screen rule:
**the row shows the decision; the disclosure is one click away.** The RECEIPT keeps every sentence
(that half of R-FZ stands); the queue does not.

### S2. Amber is being used for status, and the design language forbids that

`--shekel-warning` is reserved for a caution that is not a money state (a low-but-positive balance,
a stale anchor). "You said this merchant is never a purchase" is a status, and the design language's
rule that colour is never the only signal is inverted here: colour is the only signal that anything
on the page differs from anything else. Under the rebuild, amber appears on a row only where money
is at risk -- a suggested ADD whose envelope already holds a same-day, same-figure entry -- and a
rule withheld, a tier declined, or a parked payment is a chip in the neutral tier with the reason on
hover.

### S3. The vocabulary on screen is the code's partition

A reader today has to hold: proposal, creatable line, parked line, recordable inflow, evidence
group, sweep class, workbench group, register, filed-by-rule. Ruling R-HB already says the mechanism
partition is the service's and not the reader's; it then replaced three mechanism cards with two
evidence cards, which is a second partition the reader did not ask for. The reader's question is the
same on every line: *what is this?* Four answers cover every line the bank can send, and every peer
product uses the same four (see research): it MATCHES a row I have, it ADDS spending or income I did
not have, it is a TRANSFER between my own accounts, or I SKIP it.

### S4. Three root causes are not presentation and are ranked far from the feature

| root cause | finding / ruling | owner and rank | what it does to the inbox until fixed |
| --- | --- | --- | --- |
| Payroll net is 4-6 cents short | N-239 | `balance:X-aw`, pulled forward by this audit, starts NOW | one permanent resident per pay period, unless the developer hand-types the net |
| A card payment has no home | N-337, R-GJ | `credit_card:CC-1` then `CC-6` and `CC-7` (re-minted 2026-09-18; was `CC1a`..`CC1c` then `CC3b`), after the cutover | one permanent resident per card payment |
| Envelopes close before the bank sees the swipes | R-GK | `bank_import:X-gg`, after `credit_card:CC-5` (re-minted 2026-09-18; was `CC3c`) | every rule-filed swipe reopens a figure the developer considered final, and double counts it if he also hand-entered it |

Nothing in a skin changes those three counts. The design below gives the first two a holding shape
so they stop rendering as work, and gives the third an interim that asks the developer for one
process change he has already said he is willing to make.

### S5. The per-line consent shell survives on the two lines that matter most

The redesign approved 2026-08-24 moved creation to standing rules. The two real purchases in the
queue today still render the pre-redesign control: a select opening on "leave this line alone" with
the rule's answer printed beside it as advice. That is R-FZ(b), ruled on 2026-08-19 when the select
defaulted to the FIRST envelope in the period (78 of 91 lines onto a closed one) and the category
select to whichever sorted first. The ruling banned an arbitrary default and was right to. A
pre-filled JUSTIFIED suggestion -- the destination a rule names, the row an exact tier found -- is a
different thing: every peer product pre-fills exactly that and lets the reader correct it, with undo
as the safety. R-FZ(b) should be amended to say which of the two it bans.

## What the field does (research, 2026-08-29)

Three sweeps, cited per claim. The convergence is strong enough to treat as settled practice.

### The envelope peers: YNAB and Actual Budget

- **YNAB**: drag a file anywhere; a mapping pop-up; automatic dedupe on exact date and amount
  (`import_id` = `YNAB:[milliunits]:[date]:[occurrence]`); automatic MATCH to a hand-entered row
  when "the transaction dates are within 10 days of each other and the amounts are the same", and
  "matching counts as approving"; unmatched rows carry a circled-i and a banner count; approve per
  row (key `A`) or select-all; a card payment is a transfer whose payee is remembered after the
  first correction, and since 2025 both sides link automatically when they import within days of
  each other. Reconcile is a separate act: enter the bank balance, YNAB writes a "Reconciliation
  Balance Adjustment". Sources: support.ynab.com file-based-import, approving-and-matching,
  reconciling-accounts, how-to-handle-imported-credit-card-payments; api.ynab.com open_api_spec.
- **Actual Budget**: a mapping dialog with a live parse preview; "Merge with existing transactions"
  and per-row checkboxes at preview; rules run at import (conditions on imported payee / amount /
  date / notes, actions on category / payee / notes / split; auto-ranked "is" above "contains"; a
  rename or categorisation PROMPTS to create the rule); the matcher is exact amount within +/-7
  days, imported side wins and updates the date. There is NO approve step (its most-upvoted gap,
  issue #4230); reconcile is a lock icon, a statement balance, green circles and "Create
  reconciliation transaction". Sources: actualbudget.org/docs transactions/importing,
  budgeting/rules, accounts/reconciliation; loot-core `sync.ts`.
- Both treat MATCHED as terminal and auto-approved: only unmatched rows ask for attention. A split
  paycheck is the community pattern in both: a scheduled split that totals to the NET, which the
  imported deposit then matches on amount.

### The accounting interfaces: Xero and QuickBooks Online

The closest analogue to Shekel's problem -- a bank line against rows the books already hold -- is an
accounting bank feed, not a consumer categoriser.

- **Xero**: two columns of paired boxes, the bank's statement line on the left and "a suggested
  match in Xero" on the right, "highlighted in light green" with one green **OK** button when Xero
  is confident. Four tabs per line: **Match / Create / Transfer / Discuss** (Discuss is the honest
  "park it" state: the line stays with a note). **Find & Match** is a searchable, tickable list of
  the books' unreconciled rows for one bank line against several, with **Adjustments** ("Bank fee",
  "Minor adjustment") to close a residual. A transfer is created from ONE side and appears as the
  suggested match on the other account. Bank rules are created from the line with conditions
  pre-filled and SUGGEST rather than post; **Cash coding** is a spreadsheet view where rule-coded
  lines arrive pre-filled and "Save & Reconcile All" posts them. Statement Balance and Balance in
  Xero sit at the top and "should get closer" as you work; the reconciliation summary is an equation
  (balance in Xero, less outstanding payments, plus unreconciled lines, equals the statement). Its
  2025 auto-reconciliation labels each line **Rule / Match / Memory / Prediction** with the
  reasoning on hover. Top complaint, 245 votes: no bulk OK. Sources: central.xero.com (Bank
  reconciliation in Xero; Reconcile a bank statement line using Find & Match; Create a bank rule;
  Reconcile using cash coding; About auto bank reconciliation powered by JAX).
- **QuickBooks Online**: one table with three tabs, **For review / Categorized / Excluded**; per row
  **Match** ("you already entered a record") vs **Add** ("creates a brand-new record"), a Transfer
  transaction type, and a **Pair** badge on predicted transfers and card payments. Suggested matches
  are same amount within 90 days before to 20 after; **Find other match** ticks several records
  against one line and **Resolve Difference** adds the balancing entry. **Exclude** is an explicit
  action whose lines move to the Excluded tab and are not downloaded again; **Undo** on the
  Categorized tab returns a line to review and removes what it created. Rules carry an **Auto-add**
  toggle per rule; confidence is a badge ("green means a high-confidence match and blue means a
  medium-confidence match") with a "Top suggestion" rationale. Sources: quickbooks.intuit.com
  learn-support (Categorize and match; Match online bank transactions; Exclude a bank transaction;
  Set up bank rules; AI suggestions).
- **Wave** merges a bank line with a manual entry (two rows, one Merge; auto-merge only when exactly
  one candidate exists, "to avoid an error"), with Unmerge. **GnuCash**'s import matcher is one grid
  with three checkboxes per row (add / update-and-clear / clear), a match-quality bar, row colour
  for state, and user-set thresholds including a "commercial ATM fee" tolerance so an amount off by
  a fee still matches. **beancount-import** ranks candidates by "the most matched postings" and
  defaults unknowns to `Expenses:FIXME` for later.

The patterns the design below borrows from this generation: one verb per row with the confident case
pre-selected; Find & Match as the many-to-one tool with a residual line; transfers created once and
matched on the other side; exclude as a tab; rules suggesting by default with auto-add as an opt-in;
and the balance check as an equation at the top of the screen.

### The review-inbox generation: Monarch, Copilot, Lunch Money, Firefly III, Tiller

- A per-transaction `reviewed` bit, false on import and true on manual entry, settable by rules,
  with a header control that marks the whole view reviewed so the list reaches EMPTY (Monarch,
  Copilot, Lunch Money; Monarch's "Cmd/Ctrl+Enter" and Copilot's macOS batch edit).
- Only flag what needs eyes: Monarch's two preferences ("all new" vs "only uncategorised"), Origin's
  four toggles (over $X, first-time merchant), Prosper's "only genuine exceptions".
- A rule is minted INLINE from the correction just made, with a preview and "apply to N existing":
  Monarch's rule widget "appears automatically when you make any changes", Copilot's non-blocking
  name-rule menu, Lunch Money's "create a rule based on the changes you just made", Simplifi's
  "Update & Create Rule", Tiller's "Rule from Selection".
- Suggestion pre-filled, confidence shown by a badge and by ordering, never by a warning: Copilot's
  Intelligence badge and "top two guesses at the front of the list".
- Auto-apply then undo, not confirm-per-line: QBO auto-add rules post with an AUTO badge and an Undo
  back to the review list; NN/G on confirmation dialogs ("confirmations habituate; go to great
  lengths to provide undo") and on bulk actions.
- Exclude / hide is a first-class state distinct from delete and from transfer (Monarch Hide,
  Copilot Exclude, Lunch Money exclude-from-budget, Rocket "ignore").
- Transfers are paired, not categorised; a card payment is a transfer (all of them).
- Keyboard-driven review (Monarch, Copilot macOS, Lunch Money, Xero cash coding).
- The design writing worth reading: Prosper "Zero Inbox Accounting" ("the same uncertainty should
  not surface twice"); the "Inbox Zero to Timeline" bookkeeping case study (an inbox organised by
  source "was organising around our systems, not their work"); Smashing Magazine's 2026 agentic
  patterns (confidence signal, autonomy dial, action audit and undo).

Sources: help.monarch.com (Reviewing Transactions, Creating Transaction Rules, Hiding),
help.copilot.money (dashboard, intelligence, name rules, transaction types), support.lunchmoney.app
(transaction status, rules, import via CSV), docs.firefly-iii.org (rules, reconcile, duplicate
detection), help.tiller.com (AutoCat), nngroup.com (confirmation-dialog, bulk-actions),
prosperfinance.app/blog/zero-inbox-accounting.

### What every one of them has that Shekel's screens do not

1. One inbox that shrinks to zero, because a line with no action never enters it.
2. Four verbs, not a partition by mechanism.
3. The suggestion pre-filled; the reader corrects, does not compose.
4. Rules minted from the row, applied automatically, undone from a receipt.
5. Transfers paired, never spending.
6. Exclude as a state.
7. The balance check as the headline of the screen, not a separate report.
8. Keyboard.

## The from-scratch design

### The mental model: four verbs, one inbox

Every bank line ends with exactly one verb. The inbox is the lines with none yet.

| verb | it means | what it writes | who decides |
| --- | --- | --- | --- |
| MATCH | this line IS a row, or rows, my budget already holds | the row takes the bank's day and figure (R-FW, R-GD); a group names its difference and where it lands | the exact and group tiers propose; the owner ticks, or a GROUP RULE applies (payroll) |
| ADD | new spending or income the budget did not have | a purchase in an envelope, or an income row, dated by the bank | a merchant RULE applies at import (R-GH); else the owner picks once and may mint the rule inline |
| TRANSFER | money moved between two of my own accounts | pairs with the other account's line or shadow; never spending | the signature decides (R-GJ); auto-pairs once the card account exists; until then a HOLDING state |
| SKIP | deliberately explained by nothing | the line stays recorded, is marked, leaves the inbox | the owner, once; a rule can skip a signature |

The service partition underneath (proposal / creatable / parked / inflow; the three doors) does not
change. R-HB already ruled that it stays load-bearing and stops being visible; this finishes the
thought by naming what IS visible.

### The screen: Reconcile

One page per statement-covered account, replacing review, register and workbench.

**Header, the hero.** `Bank $2,459.60 as of 2026-08-21 · Books $2,501.31 · Off by $41.71`, and
beside it `18 to explain`. *(Both figures were wrong when this was written and are corrected here
from the producers themselves, measured 2026-08-29: the books stand $41.71 ABOVE the bank, not
below, since `AgreementDay.gap` is books less bank; and the inbox is 18 rather than 27 because
**R-HQ** moves the nine parked card payments to Transfers.)* Those two figures answer "am I done":
off by
$0.00 and 0 to explain is done. Holding counts sit under them as quiet chips: `9 card payments · $7,412.94
· waiting for the card account`, `130 lines older than your pay calendar`. The import button and the
last import's receipt are here too, so a routine session is: import, read the receipt, work the
inbox, see the difference reach zero.

**Tabs, not pages.**
`To explain (27) · Explained (221) · Filed by rules (0) · Transfers (9) · Skipped (0)`. That is
QBO's For Review / Categorised / Excluded. The register page becomes the Explained tab with Undo on
the row; the statements page's "most recent lines" is the same list with a filter.

**The list: spreadsheet grammar.** One dense row per line: day · merchant (cleaned; the bank's raw
text on hover) · amount, tabular · a verb chip carrying its pre-filled suggestion · a tick. Chip
styles carry confidence: SOLID means a rule or an exact tier would have applied it and it is here
only because it modifies a hand-made row (R-GH keeps that tick); OUTLINE means a proposal (near,
group); DASHED means no suggestion, choose. No amber unless money is at risk. Ticking the header's
checkbox for a verb class is R-FZ(c)'s per-class sweep, rendered as a control instead of a
paragraph. `j`/`k` move, `m`/`a`/`t`/`s` set the verb, Enter accepts, `z` undoes.

**A row opens.** Click the row and it expands in place: the reasons (why a rule withheld, what a
tier declined, what accepting writes and to which row) and, for MATCH, Find & Match -- the candidate
rows for this line with a running total and the difference, and where the difference goes (onto the
largest row, or its own uncategorised row per R-FN). The workbench folds into this; its two
unbounded lists become a search box over the account's rows.

**The receipt keeps every sentence.** What a pass did, quoted in the service's own words, per item,
with Undo per item (X-f6f, R-GG). Confirm only where a row is destroyed (R-GY).

**Rules.** A settings page: one line per rule (`Food Lion -> Groceries`,
`TOWN OF CLAYTON PAYROLL -> this period's payroll rows, residue on Data Manager`,
`DIVIDEND EARNED -> interest income`, `CAPITAL ONE MOBILE PMT -> transfer to Capital One`), edit on
click, "applies to N waiting" beside each. A rule is minted from the row: when the owner sets a verb
on a line whose merchant has no rule, the row offers "always do this for <merchant>" as a checkbox.

### Two rule shapes the store does not have

1. **A GROUP rule**: signature -> a row SET, with the residue's destination. The payroll case:
   `TOWN OF CLAYTON PAYROLL` -> the income rows of the deposit's pay period; the difference goes on
   the net row. Worked on the developer's data for 2026-04-23: the bank paid
   $2,573.43; the rows Data Manager $2,473.38 and Health Insurance Allowance
   $100.00 sum to $2,573.38; the rule matches both and re-prices Data Manager to $2,473.43, the
   bank's figure being the record (R-GD), so the line is explained. Once N-239 is fixed the residue
   is zero and the same rule still applies.
2. **An INFLOW rule**: signature -> income category, or -> a negative purchase in the merchant's
   envelope. `DIVIDEND EARNED` -> Interest income, automatic. `POINT OF SALE CREDIT ... AMAZON` -> a
   -$28.29 purchase in the Amazon envelope for that period, automatic, because the Amazon rule
   already exists and a refund is its inverse.

### Holding states, not queue rows

A card payment before the card account exists, a line older than the pay calendar, a line whose
merchant has a rule that is withheld pending a proposal: each is a COUNT in the header and a row on
the tab that owns it, never a row on To explain. The R-GJ bar and its sentence survive unchanged;
what changes is that a line with no act is not presented as work.

### Pages after the rebuild

| today | after |
| --- | --- |
| `/statements` (form, history, lines, rule receipt) | Statements: import + history, secondary; linked from Reconcile's header |
| `/statements/review` | **Reconcile** |
| `/statements/register` | Rules (settings); accepted matches = the Explained tab |
| `/statements/match` | the row's inline Find & Match |
| `/statements/agreement` | Books vs bank, unchanged; its headline is Reconcile's hero |
| the account's reconcile panel | `X-f6g`'s question, not this loop's |

### The interim for envelopes, and the one process change

R-GK (envelopes fill from the bank, closing on statement coverage) is the right end state and its
loop is correctly parked behind the card arc, because envelope filling is two-source and the
card-side shape is what `CC3c` rewrites. The interim is R-GU(a) as ruled: a rule files a debit swipe
into the period's envelope even when it has closed, and the envelope's figure rises by exactly what
the bank showed. That is honest bookkeeping IF the developer stops hand-entering debit swipes for
merchants that carry a rule (30 of them today, covering 65% of his recorded swipes) and keeps
hand-entering card purchases until the card arc. That is the process ruling to make before the first
real import under rules.

## Option space and recommendation

| option | what it is | verdict |
| --- | --- | --- |
| A. Scrap the feature | delete the arc | rejected: the record layer is what `balance:X-f3c` consumes; 378 lines and 221 matches of measured-good infrastructure; the dates-are-guesses problem stays unsolved forever; every peer product has this feature and the problem here is presentation plus three parked root causes |
| B. Polish the copy | shorter sentences, fewer alerts, same pages | rejected: it treats the symptom; the volume is a consequence of the every-fact-beside-its-line principle and the row-as-unit grain, and it leaves sixteen no-action lines in the queue |
| C. **Keep the engine, rebuild the surface on four verbs** | one Reconcile screen, a Rules page, two new rule shapes, holding states, R-FZ(b) amended, the three root causes ranked | **recommended** |
| D. Go straight to SimpleFIN and full auto | daily fetch, rules only, no screen | rejected as a FIRST move: `X-f6b` still needs the scheduler; rules cover 30 merchants; the exception path needs a screen; without a group rule payroll never matches. It is the END state after C (the redesign README's phase 5) |
| E. Change the budgeting model to bare categories | drop the per-period envelope | rejected, as the redesign README already did (P-4(c)): the pay period is the app's organising idea; the half of this that is right -- envelopes fill from the bank -- is already ruled (R-GK) |

The principle that decides: fix the layer that is wrong and keep the layers that are measured right.
C is also the most-correct design rather than the least risky one; its risk is a template rewrite,
and that is a sequencing question -- mockups first (Loop A), then one screen built beside the old
ones with the old routes alive until the new screen's tests cover every door, then the retirement
census `X-gi` already owes.

## Rulings needed before Loop A

**RULED 2026-08-29: yes to all seven** (developer, in-session). They are recorded as the ruling
cards `bank_import:R-HP` to `R-HV`, one per item below in the same order; the build steps they mint
are `bank_import:X-gj` and its leaves. The list is kept here as the argument each ruling points back
to.

1. **The screen's vocabulary is the four verbs** (match / add / transfer / skip); the evidence and
   mechanism partitions stay in the service. Amends R-HB's visible grouping only.
2. **A line with no available act is a holding state**, shown as a count and on its own tab, never
   as an inbox row. New ruling; it changes where R-GJ's parked lines render, not what they may do.
3. **The row shows the decision; the disclosure is one click away.** The receipt keeps every
   sentence (R-FZ's rendered-outcome half stands); the queue row carries a chip and a hover.
4. **A justified suggestion is pre-filled** -- the destination a rule names, the row an exact or
   near tier found -- and an untouched row is not submitted. Amends R-FZ(b) to ban the arbitrary
   default it was written against, not suggestions.
5. **A rule may target a row set and may cover an inflow.** Extends R-GI with the two shapes above.
6. **Register and workbench retire as pages**: the Explained tab and the row's inline Find & Match
   replace them. R-GX and R-HC's argument (they are not the queue) stands; the answer becomes a tab
   and an expansion rather than a page.
7. **Sequencing.** `balance:X-aw` (N-239, starts NOW) is pulled forward to sit beside the rebuild,
   because the payroll group rule is the interim and the calculator is the root cause. `X-gg` stays
   behind `CC3c`. The rebuild is minted as a `bank_import` step with the Loop A as its first leaf;
   `X-gi`'s census becomes its last.

## Loop A record (2026-08-29, four rounds, locked at round 4)

Run per `docs/design/visual_loop.md` on the developer's real lines (the 27 above, the nine card
payments on a Transfers tab), shot in both themes and both viewports, then published as one
interactive page with a direction toggle so the rounds could be compared in a browser. The mockups
stay out of the repo; this is the record of what was chosen and what was refused, which is what Loop
B needs.

| round | what was shown | the developer's verdict |
| --- | --- | --- |
| 1 | Three directions on one shared skeleton (hero, holding chips, tabs, footer band): a dense REGISTER in the grid's grammar; TWO COLUMNS in Xero's grammar (bank line left, "your books" right with a verb control and a green OK); CARDS grouped by verb | Cards too busy. Register too dense. Two columns has the right look, but the two boxes make the purchase and the action "feel like totally different things"; it must be ONE column |
| 2 | ONE CARD PER LINE: tick, merchant and details, amount, an arrow, then MATCH / ADD / TRANSFER / SKIP as a segmented control with the suggestion under it, and OK | The arrow "doesn't actually point to anything"; the verbs-plus-envelope block is cluttered and it is not clear what clicking each verb DOES; a first-time user would be lost; no more text, "maybe just an explanation will do". The To explain tab looks good; the other tabs still have the register look and the page must be cohesive with the rest of Shekel |
| 3 | THE SENTENCE IS THE VERB ("-$35.72 → **Add** to Lowe's, 2026-08-13 period"); the four verbs moved into the opened row as TABS whose content explains each; a one-line dismissable legend; the purchase details cut to two lines; every tab restyled as card sections with headers and subtitles, rows inside with hairline dividers, a confidence pill on the right | The sentence and the panel are right. But the rows condensed back into a register; round 2's visual separation was better; the OK button was missed ("a quick click away if the default is correct", instead of opening every row that is already right); the card-header subtitles are clutter; the pill does not earn its place, "the OK button would win hands down" |
| 4 | Round 3's sentence and panel on round 2's cards: one bordered card per line with a gap, amount → sentence → OK, no checkbox, no pill (a muted phrase at the end of the sentence only where the reason is not the section's), thin section rules with a name and a count only, the other tabs in the same card-per-line shape | **LOCKED.** |

Two things the loop settled that the audit had left open: the raw bank text stays on the row (it is
the second line of the card, mono and muted, and "the different fonts help"); and the
suggested-by-rules / proposed / nothing-suggested SECTIONS stay, as thin rules, because they carry
the reason the pill used to.

## Chosen direction: Reconcile, as locked (what Loop B builds)

Every figure below is passed in from the service or route; the template displays it. Stack and
constraints are the hard constraints at the end of this document. Where a detail is not stated here,
round 4 of the mockup is the reference and this section wins where they differ.

### The page

- **Route**: one page per statement-covered cash account, `Reconcile`, replacing the review,
  register and workbench pages. The old routes stay alive beside it until `X-gi` deletes them, so
  every door the new screen posts to is one that already exists and is tested.
- **Page header**: the account's glyph and name, the `RECONCILE` type tag, and four quiet actions on
  the right: Import a statement (opens the statements page), Rules (with the count of standing
  rules), Books vs bank, Back to the account. No alert banner on this page, or on any of the
  statement pages.
- **Hero** (the number is the hero): four figures in JetBrains Mono at the same size, each with an
  uppercase caption above: `BANK, <day>` (the walked bank balance on the latest day the agreement
  page can price), `BOOKS, <day>` (the books' balance on that day), `OFF BY` (their difference,
  `--shekel-warning` when nonzero, `--shekel-done` at zero), `TO EXPLAIN` (the count on that tab,
  accent). Right-aligned beside them: "Last import <day> · N lines recorded · N filed by rules ·
  receipt" and the one caption "Done is $0.00 off and 0 to explain." Nothing else.
- **Holding chips**, one line, pill-shaped, each a count with a noun and where relevant a link: card
  payments waiting for the card account (count, sum, link to Transfers); lines before the pay
  calendar opens (count, the day); explained (count, link). A chip renders only when its count is
  nonzero. These replace the "what this page did not look at" and "what your records hold and this
  statement did not show" panels; the latter's two figures move to the Books vs bank page.
- **Tabs**: To explain · Explained · Filed by rules · Transfers · Skipped, each with a mono count
  chip; a `?` beside the active tab shows the legend.
- **Legend**: one line, dismissable, remembered per browser: "Match a row you already hold · Add new
  spending or income · Transfer between your own accounts · Skip explained by nothing". It is the
  only explanatory prose on the page.

### The To explain tab

- **Sections**, as thin uppercase rules with a name and a count and nothing else:
  *Suggested by your rules* (a standing rule names the destination; the line is here only because it
  modifies a hand-made row or the rule was withheld, per R-GH), *Proposed* (a tier or a rule variant
  found a likely answer), *Nothing suggested*. An empty section is absent. Within a section, newest
  first.
- **The card**, one per line, bordered (`--shekel-border-subtle`), 6 px radius, 8 px gap; hover
  strengthens the border; OK'd tints the card 5% accent and sets the border accent. One grid row:
  1. **Facts**: merchant (the cleaned name) with the bank's posted day in mono beside it, and
     `made <day>` where the bank supplied one; under it, in mono muted small, the bank's raw
     description and its category, ellipsised.
  2. **Amount**: money ink, tabular, danger red when negative, right-aligned so amounts line up
     across cards.
  3. **The sentence**: a muted arrow, then the verb as the first word in bold ("**Add** to
     **Lowe's**, 2026-08-13 period"; "**Match** **2 rows**: Data Manager + Health Insurance
     Allowance · off by **$0.05**"; "**Add** as a refund to **Groceries**, 2026-07-02 period";
     "**Add** as **Interest income**"; "**Transfer** to **Capital One Credit Card**"; "**Choose**
     what this is" in accent for a line with no suggestion), the destination in primary ink, a muted
     trailing phrase only where the reason is not the section's ("· by the rule you just stated", "·
     you, just now"), and a caret showing the card opens.
  4. **OK**: a small outline button; pressed it becomes the green `✓ OK` and the footer's count
     moves; pressed again it un-OKs. A line with no suggestion shows **Choose** instead, which opens
     the card. There is no checkbox.
- **The opened card** (click anywhere on the card that is not a control): a raised panel inside the
  card with four tabs, `MATCH · ADD · TRANSFER · SKIP`, opening on the sentence's verb. Each tab's
  content is its explanation:
  - **MATCH**: "Which of your rows is this? Ticked rows take the bank's day <day>." A tickable list
    of the candidate rows with their figures (the group tier's pick pre-ticked), a search box over
    the account's rows in the period, then `ticked · bank · difference` and where the difference
    goes (onto the largest row, re-pricing it, or its own uncategorized row per R-FN), an "always:
    <signature> = this period's payroll rows, difference onto <row>" checkbox, and one line saying
    why the pass did not file it.
  - **ADD**: "Add records this as spending or income your budget did not have, dated <day>." A
    destination select (envelopes and categories in the line's pay period, plus a new envelope),
    pre-filled where a rule or a variant names one, and "always, for <merchant>".
  - **TRANSFER**: "Transfer means this money moved to or from another account of yours. It is never
    spending; it pairs with that account's own line." An account select.
  - **SKIP**: "Skip leaves this line on record and deliberately explained by nothing. It leaves To
    explain; Undo brings it back."
  - The panel's footer: "Nothing is written until you press <Verb>, or OK the card and Apply",
    Close, and one primary button NAMED BY THE VERB. Pressing it OKs the card with that verb and
    closes the panel.
- **The footer band** (sticky, the grid's summary band): the per-class sweeps as checkboxes ("OK all
  N by your rules", "OK all N payroll groups", "OK all N by the rule you just stated", per R-FZ(c)
  and R-HD, only where the class has members), the keyboard hints in mono (`j`/`k` move, Enter OK,
  `o` open, `z` undo), and the primary **Apply N OK'd**. Apply posts the OK'd cards through the
  existing reviewed-pass door and re-renders the tab with the receipt at its top.
- **The receipt** is the existing pass receipt, unchanged in content: what landed, every refusal in
  the service's words, per-item undo.
- **Amber** appears on a card only where money is at risk (a suggested Add whose envelope already
  holds a same-day, same-figure entry once N-381's spending-side test exists); a withheld rule, a
  declined tier and a parked payment are never amber.

### The other tabs, same card

- **Explained**: the same card with the sentence in the past tense ("**Added** to Apple, 2026-08-13
  period"; "**Matched** Duke Energy, 2026-08-13 period · marked paid, dated 08-17"; "**Matched** 2
  rows: ..."), newest first, and **Undo** where OK was (R-GY's confirm where the undo destroys a
  row). It replaces the register's accepted-matches list; a match that has stopped holding sorts to
  the top with its sentence saying so.
- **Filed by rules**: the same card for acts a rule performed at import, with Undo; the empty state
  names when rules will file.
- **Transfers**: the same card, "**Transfer** to Capital One Credit Card · waiting for the card
  account", no button. This is R-GJ's parked state rendered as a holding tab; when the card account
  exists the lines pair and leave the tab.
- **Skipped**: the same card with Undo.
- **Rules** is its own page (the existing rule control, one line per rule, edit on click); it is not
  a tab.

### Mobile and themes

Under 900 px the card's grid drops to two columns: facts and amount on the first row, the sentence
on its own row without the arrow, OK right-aligned on the last row; the panel loses its left margin.
The hero drops to two columns. Both themes through the tokens; the mockup was shot in both.

## Loop B plan

The build was `bank_import:X-gj`, now shipped (its as-built records are
`docs/plans/historical/bank_import_x_gj_as_built_2026-09-04.md` and
`docs/plans/historical/bank_import_x_gj_1_as_built_2026-09-02.md`); the plan stated its leaves,
their ORDER and what blocked what. What belongs here is only what each leaf IS, so that the locked
direction and the plan could not describe different work:

- **The page** (`X-gj-1`) is itself decomposed three ways, on the two boundaries the package already
  draws: `X-gj-1a` is the view model in the service; `X-gj-1b` is the page and the three tabs whose
  cards are BANK LINES, card and opened panel together; `X-gj-1c` is the two tabs whose cards are
  ACTS already applied. Each ships a WHOLE page and none renders a control a later one completes.
  All of it posts to doors that already exist, and none of it moves money of its own.
- **The inflow rule** (`X-gj-2`, moves money, own PR): an income destination and the refund
  inversion as rule answers; auto-applies at import under R-GH because it CREATES.
- **The group rule** (`X-gj-3`, moves money, own PR): a signature that pre-builds the group match
  with its residue's destination and the checkbox that states the rule; applied only on the owner's
  OK, because it MODIFIES rows.
- **Skip** (`X-gj-4`): a recorded disposition for a skipped line, which is what lights the SKIP verb
  the panel renders shut; the storage shape is a fork stated in the step, decided at the gate.
  *(It no longer owes the Transfers tab: that tab needs no store and the page draws it.)*
- **Retirement** (`X-gi`): the census, then the deletion of review, register, workbench, the
  evidence-group rendering and the per-row sentence composers.

`balance:X-aw` (N-239) is ranked beside the rebuild; until it ships the group rule is what makes the
4-6 cent residue land on the net row.

Acceptance for Loop B, beyond the Definition of Done: the developer's 27 lines render on dev in the
sections above with the nine card payments on Transfers and the 130 pre-calendar lines as a chip;
every figure in the hero is the service's; a route test posts exactly what the card and the footer
emit; the ownership 404 is paired with a case asserting the URL still routes; both themes and both
viewports shot through the visual loop before the screen is called done.

## Hard constraints (unchanged, from the design language)

Bootstrap 5 + tokens + HTMX + vanilla JS; no inline style or script (CSP); templates display and
never compute; reference tables by id; CSRF on every form and HTMX mutations POST to `_` partials;
both themes through `data-bs-theme` and tokens; amber only for a non-money caution; the number is
the hero; every call to action goes somewhere useful.
