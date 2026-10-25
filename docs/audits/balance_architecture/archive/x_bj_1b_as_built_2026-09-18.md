> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-bj-1b` as built: the bank walk anchors within the run (2026-09-18)

**What shipped** (`82b534f6`; no migration, no schema change). `fold_bank_balances` chooses its
anchor PER RUN among the standing bank levels the run reaches, reads every other standing bank
level in the run as a CHECKPOINT `walked - observed`, and is TOTAL: `BankBalances.runs` carries
every recorded run with its anchor or none and its checkpoints, and `BankAgreement.runs` replaces
both `anchor` and `imports` (`imports` is derived). `statement_agreement.html` renders one block
per run naming each figure's file. Rulings **R-BAL63** to **R-BAL66** (the four forks the developer
ruled the day it was built). Closed **N-343**. Filed **BAL-512** (owner `X-cm`).

## The shape, and why each choice

| question | ruled | why |
|---|---|---|
| where the anchor is chosen | per RUN, the account-wide rank (strongest evidence, then latest day, then id) applied to the levels one run reaches (R-BAL63) | a run is as far as any walk can honestly go, so a choice across runs prices nothing beyond the chosen run; N-343 reproduced on production's shape with a planted August run: the July `file_chain` level left all 20 August days at `--`, August's own placed day included |
| which run a level belongs to | the run whose last day `reaches_end_of((first, last), level.day, last)`, at most one | runs are disjoint and never adjacent, so the day BEFORE a run's first line (admitted by `budget.level_lies_within_file`) lies within reach of that run's levels and no other's |
| the other levels in a run | checkpoints `walked - observed`, `walked` read off the SAME prefix-sum walk as every priced day (`_walked`, one spelling) | with every line between the two days recorded, two bank figures either agree to the cent or one is placed on a day it is not the balance for -- most often the one the door had to assume (R-GN) |
| a run with lines and no standing bank level | anchors on nothing; its days are unpriced; the block says so (R-BAL64) | never across a gap (lines nobody imported: `$3,050.00` for 09-01 over eleven unimported days on the planted copy) and never from an owner true-up (R-BAL52, permanent: arithmetic on two bases) |
| the value shape | the fold is total; `runs` is the one home of the spans; `imports` derived; `BankAnchor.unconfirmed` decided in Python on the enum member and read per run; the route's `_anchor_is_assumed` deleted (R-BAL65) | the run spans in two lists on one value is rule 14's invariant tell; `covered_runs` was queried twice per page; three `None` branches went |
| the rendering | one block per run in date order, files named, checkpoints with "agrees" or "off by $Z (the walk says $W)"; the `unpriced_days` badge keeps its count with copy naming both causes (R-BAL63) | a re-import legitimately places a second figure on the same day (`uq_anchor_history_statement_import` is per import, R-BAL56), so day plus badge cannot tell two apart. Rejected: a per-run table; a "bank stated" column in the day table for four rows in 247; one global paragraph plus a checkpoints line (names one figure over days another anchor prices) |
| the import door | `recorded_opening_before` caps against `BankBalances.anchor_for(day_before)`, the anchor that priced the day; a disagreeing checkpoint is reported and moves nothing (R-BAL66) | R-GF: an instrument, never a gate; by rank the checkpoint is the weaker figure, so letting it weaken the anchor inverts the ladder. A file continuing an assumed run now opens with a SOLVED day held `uncorroborated` where it got no opening and a guessed day |
| the constant-offset signature | left account-wide this leaf; filed as **BAL-512** for `X-cm` | with two anchored runs, one out by `K` and one by `0.00`, the gap set holds two members and the page says nothing; the developer chose the option whose text kept it account-wide for the leaf |

## What every reader became

| reader | before | after |
|---|---|---|
| `_balance.usable_anchor` | ONE `BankAnchor` per account, or `None` | deleted; `_strongest_then_latest(levels)` is the rank, applied per run by `_recorded_runs` |
| `_balance.reaches_end_of(runs, ...)` | any run carries the crossing | ONE run: `reaches_end_of((first, last), anchor_day, end_day)`; `RecordedRun.prices(day)` is the one membership test the fold and `anchor_for` share |
| `_balance.fold_bank_balances` | `BankBalances(anchor, balances)` or `None` | `BankBalances(runs, balances)`, total; `RecordedRun(first_day, last_day, anchor, checkpoints)`; `Checkpoint(day, observed, walked, evidence, file_name)`; `BankAnchor` gains `file_name` and `unconfirmed` |
| `_balance.bank_balance_on` | `None if folded is None else ...` | `.balances.get(day)` |
| `_anchor.recorded_opening_before` | capped against the account's one anchor | capped against `folded.anchor_for(day_before)` |
| `_reads._file_names_of` | private to the statements page | `_balance.file_names_of`, one lookup both the page and the fold read |
| `bank_agreement.BankAgreement` | `anchor`, `imports` (a second `covered_runs` query) | `runs`; `imports` and `anchored` derived; `unpriced_days` reads `anchored` |
| `routes.accounts.bank_agreement` | `_anchor_is_assumed(agreement)` -> `anchor_assumed` | deleted; the template reads `run.anchor.unconfirmed` |
| `statement_agreement.html` | one paragraph: no anchor / walked from `$X` on `D` | one block per run; checkpoints; the `unpriced_days` badge after the runs |
| `tests/manual/verify_level_relation.py` | dumped `fold.anchor`, `agreement.anchor`, probed `agreement.anchors` | dumps `fold.runs`, `agreement.runs` |

## The measurement

Two dumps, taken BEFORE any code moved and again at HEAD. The untouched production restore
`shekel_xbj1` (2026-09-16 06:19, head `d2e9f4a17c63`: one run 2026-01-02..07-17, one `file_chain`
level `$2,229.73` at 07-17, 306 lines): **0 changed leaves**; the only moves are the shape keys
(`fold.anchor` and `agreement.anchor` became `runs[0]` carrying the same three figures plus the
file name and the span; the harness's `anchors: null` probe went). The throwaway copy
`shekel_xbj1_planted` (the restore plus four planted shapes: a Q1 re-import stating `$2,715.01` at
03-31, a May re-import assuming `$1,548.86` at 05-15, a disconnected August run with lines and an
assumed `$3,000.00` at 08-20, a September run with lines and no figure): **27 changed leaves, every
one predicted** -- 07-31..08-20 priced from August's own anchor (`$2,350.00` at 07-31 the day before
its first line, `$3,350.00` 08-01..08-09, `$3,100.00` 08-10..08-19, `$3,000.00` at 08-20), the hero
day moved from 07-17 to 08-20, `unpriced_days` 50 -> 29 (13 of the July gap, 11 of the August
gap, September's 5); the runs carry the Q1 checkpoint agreeing (`$2,715.01` = `$2,715.01`) and the
May one off by `-$10.00` (walk `$1,538.86`); September anchors on nothing; the opening before the
first recorded line is unchanged (`$1,845.33`, corroborated).

## What the neutral review found and what was done

One neutral review over the design and the staged diff (a fresh `code-reviewer` subagent, 58 tool
uses), asked to grade the fix's twelve claims about itself hardest; it confirmed all twelve by
reading the code, one with a documented exception (below). Should-fix: `statement_match._offers
.not_shown_alone`'s docstring still pointed at the deleted route helper (re-pointed at
`BankAnchor.unconfirmed`); the checkpoint verdict's copy deduced ONE cause ("one of the two figures
is placed on a day it is not the balance for") from a disagreement that has three -- a line
recorded twice under two export formats' keys puts the walk off by that line while both placements
are right -- the same overclaim `constant_offset` was corrected for on 2026-08-24 (hedged: "either
... or the lines recorded between them are not what the bank posted"). Nits, all fixed: no test held
two checkpoints on different days, so a descending sort passed (a third level LATER than the anchor,
seeded first so id order and day order differ, now grades the order and the other subtraction: walk
`$1,015.00` against a stated `$1,020.00`, off by `-$5.00`); two page assertions accepted a figure in
any money cell of a row, where the "apart" cell can carry the bank cell's figure (`_bank_balance_cell`
reads the column by position, the third `<td>` from the end in both row shapes); one checkpoint's
walked figure was searched in a window the other checkpoint's stated figure could satisfy (each
verdict is now read between its own file name and the next block); `standing_gap is not None`
became the figure. Two docstring imprecisions fixed (`AgreementDay.bank_balance` on the day before an
anchored run's first line; `unpriced_days`' rationale in the mixed case). Left as stated: the
hand-built hero agreement in `test_reconcile` carries priced days on a run with no anchor, a state
production cannot produce, which nothing in that class reads.

**Claim 1's exception.** With one standing level the old and new walks price the identical day
set, EXCEPT for a level no run reaches: the old code priced that level's own day (an empty crossing
is reached whatever the runs), the new code prices nothing and lists it nowhere while the
statements page shows it standing. Reachable only by a 0-line re-import placing its level on the
day before its file's first line and the owning import then being deleted (no release predicate
withdraws a level that rests on no deleted line). Not on production's data (0 changed leaves);
stated in `_recorded_runs`'s docstring and reported to the coordinator as a candidate ledger row.

## What a LATER step must obey

* The bank walk's bank-only domain is permanent (R-BAL52) and a run with no standing bank level
  anchors on nothing (R-BAL64): no reader may fill a run from a neighbouring run or from an owner
  level.
* A checkpoint is an instrument (R-BAL66): no door may refuse, cap or re-solve on one. `X-bj-2`'s
  `discrepancy = observed - computed` is the cross-source reading; a checkpoint is the bank's
  record against itself and stays on the agreement page.
* `RecordedRun.prices` is the ONE membership test; a reader that needs "which run answered" reads
  `BankBalances.anchor_for`, never a second walk.
* A standing bank level no run reaches (its lines deleted from under a level placed on the day
  before its file's first line, which no release predicate withdraws) anchors nothing and is listed
  nowhere; the next import re-establishes a level. Stated in `_recorded_runs`'s docstring.
* `BankAgreement.constant_offset` is account-wide until `X-cm` (BAL-512).
