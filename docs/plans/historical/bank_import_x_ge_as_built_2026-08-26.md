> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# A rule files a new swipe by itself, as built: X-ge (2026-08-26)

**Shipped at `6d3e3ca1`**, as two leaves in one commit: `X-ge-1` (the matcher
publishes what it DECLINED to decide) and `X-ge-2` (the auto-apply door that
rests on it).

**Condensed out of `implementation_plan_bank_import.md` under `conventions.md`
rule 5** in the same pass that shipped it: `X-ge`'s own entry took that document
inside its 20-line headroom, so the X-gb..X-ge span became five one-line
pointers and this file took the detail.

Rule 5's three conditions hold. **The finding it opens is NOT closed**: `N-359`
is a live row in `ledger.md` under `X-gf`. **No live sentence depends on this
one**: the constraints a later step must obey were moved onto `X-gf`'s own
entry, which is where they will be read.

## What shipped

**The door**: `POST /accounts/<id>/statements` records the file and then files
what standing rules answer for, in ONE unit of work.

| piece | what it is |
|---|---|
| `statement_match/_filing.py` | which of an import's NEW lines a rule files, what it withholds and why, and the receipt reader |
| `_batch.Consent` | who agreed to a whole pass -- `TICKED` or `STANDING_RULE`; `ReviewedBatch.__post_init__` refuses a rule batch carrying a match |
| `_reads.ReviewSet.search_gap_for` | why this pass cannot say a line has no counterpart, or `None` -- it READS what the tiers report and derives nothing |
| `_near.NearRefusal` / `_pairing.exactly_matched_but_outside_the_window` | each tier reporting the bound IT applies, in the module that owns the predicate |
| `_propose.ProposedMatches.declined_lines` | where those reports are joined; it replaces `undecided_near_lines` |
| `_placement.Placement.creation_for` | the sweep's target and the rule's act, from ONE derivation |
| `_accepted_view.AcceptedGroup.applied_by_rule` | what `R-GT`'s column exists to be SEEN as |
| `_container.py` | `_create.py`'s destination-and-close half, moved out at its 1,000-line bound |
| `statements.html` | the receipt card, its undo, and the banner that stopped saying *it changes no balance* |

## The measurement the design was taken on

**A simulation of the whole step over the developer's own 2026 statement**, on a
throwaway clone of dev reset to his PRE-import books: every import deleted
through `delete_import`, then the 91 purchases and 32 envelopes the 2026-08-21
review pass created removed through `entry_service` / `transaction_service`
(that pass predates `statement_match_creations`, so `delete_import` could not
reach them). What is left is his own 93 hand entries and 378 recorded lines.

| | lines | money |
|---|---|---|
| the matcher explains against his own rows (stays a proposal, keeps its tick) | 137 | -- |
| a standing rule REACHES | 80 | `$2,886.13` |
| -- of which it FILES | **70** | **`$2,324.17`** |
| -- of which `X-ge-1` WITHHOLDS | **10** | **`$561.96`** |
| -- into an envelope he had already closed | 33 | `$911.10` |
| -- creating a new envelope | 47 | `$1,975.03` |
| a rule exists and does not reach the line | 5 | `$446.78` |
| no rule at all | 1 | `$5.99` |
| parked card payments (**R-GJ**) | 9 | -- |
| before his pay calendar opens | 130 | -- |

**Zero of the 33 go into an envelope still OPEN**, which is what settled fork
(a): he closes his envelopes ahead of the bank, so a door refusing a closed one
would file nothing for Groceries, Gas or Kayla's.

**The double-count check came back clean, and that is the number that mattered
most.** Groceries in his 2026-08-13 period holds three hand purchases worth
`$499.82` (Sams `$130.11`, Walmart `$250.03`, Bjs `$119.68`) and the matcher
proposes exactly those three bank lines against them; the three a rule would add
(`$57.96` Walmart, `$10.89` Food Lion, `$11.21` Walmart) are swipes he never
recorded.

**Per-period, a routine import is small**: the last pay period the export covers
holds 10 auto-filed lines and 1 question. `Transactions-2026-08-22.csv`, his
real fortnightly export, recorded 2 new lines of which 1 would file.

**The derivation is cheap**: `ReviewScope.build` + `review_set` measured
0.21-0.27 s on that clone, against the 3.6 s the arc's older docstrings cite --
so the import path can afford one, and no caching argument was needed.

## X-ge-1, and why the first build of (c) was wrong

**The developer's ruling (c) was right and my first implementation of it was
not.**  It read the bounds a pass PUBLISHES -- `ReviewBounds` plus the near
tier's contest -- and called that enumeration complete.  An adversarial review
measured it false twice:

* the near tier throws away any candidate whose row label does not NAME the
  merchant, and reported nothing.  A `$178.32` row labelled `Groceries` against
  a `-$178.29` Food Lion line filed a SECOND purchase: **`$356.61` for one
  `$178.29` movement**, which is finding **N-335**'s own figures arriving
  through the door with nobody watching;
* any pair more than `DAY_WINDOW` apart is refused, and nothing reported that
  either.  On the developer's own books an `Apple` line of `-$21.34` sits **15
  days** -- one past the window -- from his own `Apple Music` row.

**Four remedies were put to him and all four were the same band-aid**: each
added a fourth predicate OUTSIDE the matcher that re-derived what the matcher
had already decided, which is finding **N-322** exactly and what
`_pairing.py`'s own header predicts in as many words.  He refused all four and
asked for the from-scratch answer.

**That answer is that a pass reports three verdicts, not two**: explained,
unexplained, and DECLINED-with-a-reason.  Each tier now publishes its own
refusals where it makes them, `search_gap_for` reads them, and its completeness
claim is true by construction -- a tier added later must report or it is not a
bound.  `NEAR_MISS_BOUND` and `DAY_WINDOW` stay spelled once each.

**One refusal is deliberately NOT published and it is a measured exclusion.**
`NearRefusal.FIGURE_NOT_ITS_OWN` -- a row whose figure is whatever its contents
come to -- is the ordinary shape here, since every envelope is one.  Publishing
it withheld the Groceries case ruling R-GU exists to file, caught by this step's
own controls.  See `_near._FIGURE_ADMITTED`.

## The three decisions, and who took them

All three went to the developer with worked figures and all three came back on
the recommendation (**R-GU**): a rule FILES into an already-closed envelope; a
*new envelope* answer files too, minting its container; and a rule files only
where the pass POSITIVELY finished looking, with the receipt saying what it
withheld.

**The 29-envelopes-over-11-periods figure that fork (b) turns on can never
occur.** It is what a whole-year BACKFILL would produce, and a rule reaches NEW
swipe lines only -- a fortnightly import touches one period, so `MintedEnvelopes`
mints one envelope per answer in it.

## Three things the specification did not name

1. **`_create.py` passed its 1,000-line ceiling** and split by SUBJECT rather
   than size, which is the cut `_rules` / `_stating` was made on: `_create.py`
   answers *what is this bank line, as a purchase*, and `_container.py` answers
   *which budget line contains it, and what closing that means*.
   `CreatedPurchase` moved to `_creations.py`, whose whole subject it is, which
   is what let the two modules cut apart without an import cycle.
2. **The statements page's banner was FALSE the moment a rule could fire.** It
   read *This records what your bank said. It changes no balance.* The
   route-test asserting that sentence went on passing against the new copy,
   because the replacement still contains the phrase about the RECORDING half --
   so the case was rewritten to assert both halves rather than left alone.
3. **The matcher proposes against the ENVELOPE, not its purchases, when those
   purchases are unposted.** Found by probing a control that failed for a reason
   the test's own premise had got wrong. It is why the R-FZ(d) collision arm is
   reachable rather than hypothetical: an envelope holding unposted purchases is
   priced at the reservation they make, and a line equal to that figure matches
   the row WHOLE. On the developer's books every hand purchase carries a settle
   day, which is why the same measurement reads 0.

## What the controls are held to

**Eight planted defects, every one CAUGHT** (2026-08-26; each mutation applied
and the edit verified present before the suite ran, because a harness whose
edit silently missed prints the same green as survival):

| planted defect | caught by |
|---|---|
| a rule files EVERY unexplained line, not only new swipes | 1 failure |
| the pass files a line it could not finish searching | 1 |
| a rule files into a row the statement explains whole | 1 |
| a rule's act is recorded as a tick | 3 |
| every act is recorded as a rule's | 1 |
| a rule mints one envelope PER LINE | 1 |
| only the line's OWN day counts as crowded | 2 |
| a rule-consented batch may carry a match | 1 |

**Two of `search_gap_for`'s three arms are unreachable from ordinary data** -- a
crowded day needs more than 32 candidate rows in one bucket, an unpriceable row
needs an amount model that cannot value it -- so they are graded against the
value the pass publishes rather than against data that would make the defect
expensive first.
