# The bank import, rebuilt around standing consent

**STATUS: APPROVED by the developer, 2026-08-24 -- all six rulings (P-1..P-6), as recommended --
and TRANSCRIBED into the gated registries the same day** (rulings **R-GH**..**R-GL**, steps
`bank_import:X-ga`..`X-gh`, findings **N-344** / **N-345**).
This is the design record of the 2026-08-24 adversarial review (published as the "Bank Import
Verdict" artifact) turned into a concrete path: what to rule, what to ship, in what order, from
today until the import is what it would be if designed from scratch. The gated registries are the
plan of record and now state this design; section 5 records the mapping. What remains of this
document's own obligations is the P-6 operator repair, which rides step `X-gb`'s session.

**The goal, in the developer's words (2026-08-24):** "In a perfect world the app would hold every
real transaction the bank saw and automatically assign it to the appropriate line item in the
budget. Since that may not be fully possible I would like to get as close as I can." Manual
assignment is acceptable for judgment envelopes (Kayla's Spending Money). He has stated he is
willing to change prior decisions, his own process, and the code to get there.

---

## 0. Why the current design cannot get there, in four measured facts

Every figure below was produced during the 2026-08-24 review, in SQL against the dev database or by
driving the real app in a browser.

1. **The consent model forbids the goal.** Ruling R-FP makes every act a proposal needing a tick,
   every time. Food Lion's fortieth swipe needs the same human act as its first. "Automatically
   assign" is impossible *by ruling*, not by limitation -- no amount of matcher improvement changes
   this.
2. **The grain mismatch is the dominant case, and its cheapest act is the dangerous one.** 246 of
   376 lines (65%) have no matchable app row at any tolerance. For those, the review's only offer is
   "record as a new purchase" -- which is exactly wrong whenever the money already exists in the
   books in another shape. The one YTD pass double-booked all 9 Capital One ACH payments
   ($7,412.94 in minted envelopes beside the owner's own 22 CC Payback rows worth $6,286.46 --
   roughly $6,200 of overlap across periods 5-11) and three months of Geico ($356/month).
3. **The owner's process fights the model.** He settles envelopes ahead of the bank, so nearly every
   routine import lands in "record into one that has already closed, raising what it recorded" --
   reopening figures he considered done. His 60 hand anchor true-ups are him copying the bank's
   balance in by hand (the 08-18 true-up of $2,501.31 IS the bank's 08-18 closing, measured).
4. **The steady-state mechanics are already good.** Import 0.61 s, idempotent (40 of 42 overlapping
   lines recognised), apply 0.9 s, honest receipts, clean verified inverses. The recording layer is
   not the problem. The workflow above it is.

**The conclusion the rest of this document builds on:** keep the recording layer nearly verbatim;
rebuild the review from *propose-then-consent* into *ingest-then-categorize*, where consent is given
once per rule rather than once per line; and move the budget's envelope semantics to meet the bank
rather than making the bank negotiate with hand-settled aggregates.

---

## 1. The end state (the from-scratch design)

**A Tuesday, once every phase has shipped.** Overnight, the SimpleFIN fetch recorded yesterday's
lines and the bank's balance. Three swipes matched standing rules and are already purchases in
Groceries and Gas, dated the day the money moved; the receipt names all three and each carries an
undo. The bank's balance agreed with the books to the cent, so the anchor asserted itself. The grid
shows real dates and real amounts with nothing for the owner to do. One line -- a merchant never
seen before -- sits in the exception queue, which is the only thing the review screen shows. The
owner files it in ten seconds, states a rule if it will recur, and is done. No export, no upload, no
ticks for the routine, no hand true-up. Kayla's merchants stay manual because the owner said so, in
a rule.

**The layers of that design, and where today's code lands in it:**

| layer | from-scratch design | today's code | disposition |
|---|---|---|---|
| Record | Append-only bank lines, idempotent, evidence-laddered balance, undoable imports | `statement_import` (3,825 lines) | **Keep, nearly verbatim** |
| Recognize | Exact / group / near-miss matchers as a library the rules engine calls | `statement_match` tiers (10,944 lines) | **Keep the matchers; retire the per-line consent shell** |
| Rules | Standing merchant rules; auto-apply for NEW swipes; receipt + undo window | Merchant policies (suggest-only, R-GA) | **Promote** |
| Review | An exception queue: new merchants, contests, group residuals, acts on hand-made rows | Five-section 14,527 px review page | **Rebuild as queue** |
| Budget | Envelopes fill from the bank; targets vs. accumulating actuals; coverage-driven close | Hand-settled aggregates | **Change semantics** |
| Balance | Bank anchor asserts the opening when residue is zero; true-up becomes an exception alert | 60 hand true-ups beside an unused bank anchor | **Unify, after X-f3c** |
| Fetch | SimpleFIN daily, read-only revocable token, per-sync balance corroborates the chain | Manual CSV download/upload | **Add last** (already ranked as X-f6b) |

---

## 2. The rulings to approve

Each fork below states its options, a worked example from the owner's own data, and a firm
recommendation. Approving this document means approving the recommended arm of each; a ruling id is
minted when it is recorded in the arc document. These amend, and are honest about amending, rulings
the developer made between 2026-08-13 and 2026-08-24.

### P-1. Consent splits by ACT CLASS, not by act (amends R-FP's review half, R-GA, R-FZ)

**The fork:** who consents to a bank line becoming a purchase?

| option | what it means | consequence |
|---|---|---|
| (a) Keep consent-per-line | Every line, every visit, needs a tick (today) | The stated goal is unreachable; ~2 min twice a week forever, growing with SimpleFIN to daily |
| (b) **Consent-per-rule for CREATION; ticks for MODIFICATION** | A standing rule auto-applies to NEW swipe lines, with a receipt and per-item undo; any act touching a row the owner made by hand (re-date, re-price, settle, group-match) still needs its tick | The goal is reachable; the owner's own records keep their tick protection |
| (c) Full auto | Rules may also modify existing rows | Rejected: a re-price of a hand-entered figure is a correction of the OWNER, and review is the point |

**Worked example (b):** the 08-17 Food Lion swipe, $10.89. Today: it renders in "lines that are a
purchase you never recorded," the owner moves a select (or sweeps a class) and presses Apply --
every import, forever. Under (b): the rule "Food Lion goes in Groceries" was stated once; at import
the line becomes a $10.89 Groceries purchase dated 08-17, the receipt reads "3 line(s) filed by your
rules -- undo any of them here," and the review queue shows nothing. The same import's $389.46 Duke
Energy line, which matches the owner's own recurring bill row, is still a PROPOSAL -- it modifies a
hand-made row's date, and that keeps its tick.

**Recommendation: (b), firmly.** It is the smallest amendment that makes the goal reachable, and it
keeps R-FP's protective half exactly where the protection pays: over the owner's own records. The
undo machinery this rides on already exists and already names money (shipped at X-f6f, ruling R-GG).

### P-2. A merchant answer is a standing RULE, scoped and revocable (amends R-GA; absorbs X-f6c)

**The fork:** what does "where your merchants go" mean?

- (a) A suggestion the owner re-confirms per line (today, R-GA).
- (b) **A standing rule: auto-file NEW swipes of this merchant into this destination.** Revocable on
  the same screen; every application receipted; three answer shapes -- an envelope (via the template
  identity X-f6c already plans, so the container persists across periods), "always ask" (Kayla), and
  "never a purchase" (see P-3).

**Worked example:** the owner's saved answers already cover 29 merchants. Under (b), the 08-22
import's two swipes need zero acts (Lowe's rule fires) plus one exception (Public Library, new
merchant -- ten seconds, optionally stating a rule for next time). Measured today the same import
took two sweep-ticks, a select, two fields, and an Apply on a five-section page.

**Recommendation: (b).** Note what this deliberately preserves from R-GA: stating a rule still moves
no money at statement time; the money moves at IMPORT time, under the rule, with a receipt.

### P-3. A recognized card payment can NEVER become a purchase (amends R-FX)

**The fork:** what happens to a bank line that is a credit-card payment?

- (a) Warn in a paragraph and offer the create arm anyway (today). Measured outcome: all nine
  Capital One payments double-booked; the saved policy ("Capital One Credit Card -> a new
  envelope") will suggest the tenth.
- (b) **Structural refusal: a line matching a card-payment signature (merchant/pattern, e.g. the
  `CAPITAL ONE MOBILE PMT` ACH text) has no create arm at all.** Its arms are: match to payback
  rows as a group with the difference named (the machinery R-FN/X-f6d-4 already built), or park as
  "a card payment awaiting its home." The real home is the card arc's question -- finding N-337
  (owner CC3b) already carries it -- and this ruling refuses the wrong answer until the right one
  ships.

**Worked example:** period 11 holds the $793.23 ACH payment and $692.97 of the owner's payback rows.
Under (b) the line can be group-matched to the paybacks with the $100.26 difference named and
accepted (it is real -- card-side timing), or parked. It can no longer become a tenth "Capital One
Credit Card" envelope. The saved policy converts to "never a purchase" in the same change.

**Recommendation: (b).** A warning paragraph did not survive one contact with a 215-item session; a
missing door needs no vigilance.

### P-4. Envelopes FILL from the bank (new ruling; touches the balance/grid core)

**The fork:** what is an envelope once the bank is the source of swipes?

| option | what it means | consequence |
|---|---|---|
| (a) Keep hand-settled aggregates | Owner batch-adds entries, settles early; import negotiates with closed figures | Every routine import reopens "final" figures; dates stay guesses; the friction measured in the review persists |
| (b) **Target + accumulating actuals** | The envelope stays open through its period; rule-assigned purchases flow in from imports, dated by the bank; it closes when the period's statement coverage is complete (the walked-statement machinery X-f3a-2 ships) or when the owner closes it by hand; hand entries remain for cash and judgment envelopes | The import stops fighting the process; spending per period becomes observed, not transcribed |
| (c) Drop envelopes for bare categories | Swipes categorize with no per-period target | Rejected: the target IS the budget; this app's whole organizing idea is the pay period |

**Worked example (b):** Groceries, period starting 08-13, target $500. Today the owner batch-adds
entries and settles; the bank later shows 08-17's Food Lion $10.89, Walmart $11.21, Sam's $130.11 --
each a closed-envelope negotiation. Under (b) those swipes file themselves on import; mid-period the
envelope reads "spent $152.21 of $500"; at coverage-complete it closes at the observed total. The
owner's job becomes setting the target and reading the meter, not transcribing the receipts.

**Recommendation: (b).** This is the single largest change and the one that asks the owner's habit
to move (he has said he is willing). It needs its own design loop before building -- carry-forward
(which writes `max(0, budget - spent)` forward), the settle state machine, and the grid's chip
language all touch it. This document fixes the DIRECTION; the loop fixes the details.

### P-5. The bank's balance may assert the anchor (new ruling; designed WITH X-f3c, ships after)

**The fork:** two balance records exist -- the owner's hand true-ups and the import's evidence-
laddered bank anchor. Today they never touch (R-FV keeps a bank line out of `reconciled_by_id`,
rightly).

- (a) Keep both forever; the owner keeps copying the bank's number in by hand (60 times so far).
- (b) **A confirmed import's anchor asserts the opening when the day's residue is zero; a nonzero
  residue surfaces as the exception it is.** The owner's true-up door remains for the exceptional
  correction; the habit retires.

**Worked example:** the owner's last true-up (08-18, $2,501.31) equals the bank's own 08-18 closing
exactly. Under (b) that true-up would not have existed: the 08-21 import's anchor covers it, and the
books-vs-bank page (X-f6e-2, shipped) already computes the residue that gates it.

**Recommendation: (b), sequenced AFTER balance:X-f3c** -- the cutover redefines what an assertion is
(opening equity plus postings), and this ruling must be written against the new definition, not the
one X-f4 deletes.

### P-6. The dev books are repaired NOW, through real doors (operator decision, no code)

**The fork:** the dev database carries the YTD pass's damage (April/May Geico duplicates, nine CC
envelopes, 46 minted $0 shells). Repair row by row now, or wait for the cutover to draw a line?

**Recommendation: repair now, with the assistant driving, through the app's own doors** -- the July
Geico repair is already done and measured (undo, entry delete, near-tier re-match; one step needed
the orphaned delete route, which Phase 0 wires up). Reasons: the books get true immediately; the
cutover's design should start from honest books rather than encode exceptions for known-wrong rows;
and it is dev-only (production holds zero import rows -- measured 2026-08-24). Estimated one
operator session.

---

## 3. The phases, from today

Phases are ordered by dependency, not preference. Phase 1 is not new work -- it is the plan's
existing top of the order, named here because everything after it leans on it.

### Phase 0 -- Rulings and paper cuts (now; no dependencies)

Record the approved rulings in the arc document, then ship the small protective fixes the review
measured. Each is narrow, testable, and worth having even if nothing else in this document ships:

1. **The card-payment refusal (P-3)**, including converting the saved Capital One policy. This stops
   the worst bleeding before any rebuild.
2. **Wire the transaction delete door.** `DELETE /transactions/<id>` exists with zero UI callers
   (measured by census of every template and script); the 46 minted shells are permanent and an
   emptied $0 Projected envelope's chip stops rendering entirely. Decide the surface (full-edit
   popover is the natural home) and its guards.
3. **Fix the dead corroboration advice.** Every receipt and the books-vs-bank header prescribe
   "export once with your bank's running-balance option ticked" -- SECU removed that option. Until
   SimpleFIN revives corroboration (Phase 5), the copy must stop prescribing the impossible, and the
   permanent amber "uncorroborated" should read as the normal state it now is, not a warning.
4. **Undo disclosure symmetry.** An undo on an act with no creation record says only "Are you
   sure?"; it must say what the fresh-act dialog says -- what survives, what is removed. Also chase
   the once-observed confirm-bypass race (an undo submitted before the modal bound).
5. **Stop "rows the bank never showed" listing rows the bank could never show** (envelope
   containers, internal allowances). The panel's framing is false for them and buries the six
   actionable payroll groups.
6. **Operator session: repair the dev books (P-6).**

Estimated size: 2-3 leaf sessions plus the operator session.

### Phase 1 -- The cutover gate (already ranked #1-#3: X-f3c, X-f3a-2, X-f4)

Unchanged, and this proposal leans into it: the cutover makes bank lines the historical spine and
redefines the assertion; the walked-statement step gives "coverage" a meaning P-4's auto-close
needs; X-f4 deletes what the new model orphans. The card arc (N-337's owner) unlocks behind it,
which is where the parked card payments of P-3 get their real home.

### Phase 2 -- The standing-rules engine (P-1 + P-2; after Phase 0, parallel-safe with Phase 1)

The consent rebuild. Rules storage and lifecycle (state, revoke, receipt), auto-apply at import for
new swipe lines using the existing matchers and the existing creation door (`create_purchase_from_
line` already has one write path and an inverse), the import receipt growing the "filed by your
rules" section, and the review page rebuilt as an exception queue (new merchants, contested lines,
group residuals, and every proposal that modifies a hand-made row). X-f6c's template identity ships
inside this phase as the container half of P-2.

Estimated size: 4-6 leaf sessions. The consent boundary (which acts auto-apply) is THE thing to get
right; everything else is plumbing that mostly exists.

### Phase 3 -- Envelope semantics (P-4; after Phase 2, and after its own design loop)

The design loop first (worked examples over the owner's real envelopes, the carry-forward
interaction, the grid chip language), then the build: open-through-period envelopes, rule-filled
entries, coverage-driven close using X-f3a-2's walked statements, and the process change it enables.

Estimated size: 1 design loop + 3-5 leaf sessions.

### Phase 4 -- Anchor unification (P-5; after X-f3c and Phase 3)

The bank anchor asserts the opening on zero residue; nonzero residue becomes an exception surface
(the books-vs-bank page already computes it per day). The hand true-up door stays, demoted from
habit to correction.

Estimated size: 1-2 leaf sessions.

### Phase 5 -- SimpleFIN (X-f6b, re-scoped; after Phase 2, ideally after Phase 4)

The already-planned adapter, now landing on rules instead of a queue, plus two additions this
proposal makes to its scope: the per-sync balance becomes the corroboration source the evidence
ladder lost when SECU dropped running balances, and the scheduler decision X-f6b already names gets
its recommendation -- host cron invoking a CLI door (matches the deployment's no-scheduler,
no-exposed-ports posture; R-FP's read-only revocable-token ground stands). Daily fetch turns the
Tuesday narrative of section 1 into the actual product.

Estimated size: 2-3 leaf sessions.

### Phase 6 -- Retire what the end state orphans

The per-line destination selects for rule-covered merchants, the permanent residue panels, the
now-redundant sweep classes -- whatever the exception queue has made dead, deleted rather than left.
Sized when reached.

---

## 4. What this proposal deliberately does NOT do

- **It does not touch production data or the prod pipeline.** Production holds zero import rows.
- **It does not let rules modify hand-made rows.** That half of R-FP is kept on purpose (P-1c).
- **It does not adopt Plaid** or revisit R-FP's SimpleFIN-over-Plaid security ground.
- **It does not delete the review surface.** It shrinks it to the exceptions that deserve it.
- **It does not decide the card arc's payback model.** P-3 parks the question with N-337/CC3b where
  the developer already put it.

## 5. Bookkeeping (transcribed 2026-08-24, plan gate 204/204)

- **Rulings**: P-1 -> **R-GH**, P-2 -> **R-GI**, P-3 -> **R-GJ**, P-4 -> **R-GK**, P-5 -> **R-GL**,
  all in `implementation_plan_bank_import.md`, which also records the amendments in place on
  R-FP, R-FZ, R-GA and R-FX, and moves the arc's manual-entry bound.
- **Steps**: `X-ga`/`X-gb`/`X-gc` (Phase 0), `X-gd`/`X-ge`/`X-gf` (Phase 2) and `X-gh` (Phase 4),
  ranked in the plan (the ranks were its alone); `X-f6b` re-scoped per Phase 5 behind the
  auto-apply door; `X-f6c` beside the rules phase. P-6 rides `X-gb`'s session. The Phase 6
  retirement leaf is MINTED when `X-gf` ships. **Corrected 2026-08-24 (developer approval)**:
  `X-gg`, the Phase 3 design loop, moved BEHIND the card arc's CC3 block with an explicit blocker
  on `credit_card:CC3c` -- envelope filling is two-source (debit swipes from SECU lines, card
  swipes from card-side charges), and running the loop over the payback shape the card arc deletes
  is the mistake that withdrew `balance:X-au-i`.
- **Findings triage**: the delete-door absence is **N-344** (owner `X-gb`); the undo-disclosure
  asymmetry and the once-observed confirm-bypass race are **N-345** (owner `X-gc`); the "never
  showed" framing rides `X-gc`'s specification; the same-name grid double-render rides `X-gg`'s,
  because its remedy is the row-identity decision that loop owns. The card-payment path note was
  added to **N-337**, whose owner stays `CC3b`.
- Headroom for the transcription came from conventions rule 5: the six SHIPPED bank_import rows
  (X-f6a, X-f6d, X-f6e and leaves, X-f6f) were condensed into
  `docs/plans/historical/bank_import_x_f6a_core_as_built_2026-08-19.md`.
- The review's full evidence stays in the "Bank Import Verdict" artifact
  (https://claude.ai/code/artifact/4f6f6a50-7146-4263-bf63-5af5d3ad5e2b) and the session's measured
  figures above; this document is the plan-facing summary.
