> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# X-f3a-1 and X-f3d as built: clearing became a fact, and a true-up got a name

**Read-only history. Nothing here governs work.** The live document is
`../README.md`.

Extracted 2026-08-16 at plan step **bank_import:X-f6a-1**, when that step's own three index rows
took `docs/plans/steps.md` to 241 lines against the 20-line headroom its 260-line cap requires.
`conventions.md` rule 4 is explicit that a cap is a FORCING FUNCTION rather than a ceiling sized to
fit, and rule 5 that the only way back under one is to archive a COMPLETED span -- never to trim a
live step's specification. These two steps are that span: both shipped, both closed out, and neither
is named as a blocker by any open step.

**Every line here is one step, its commit, and what it closed.** The commit is the record; read it
rather than prose about it.

## The steps

| step | commit | what shipped |
|---|---|---|
| **X-f3a-1** | `d6d9692c` | Clearing became a RECORDED FACT: a transaction and a purchase each name the `account_anchor_history` row whose statement showed them, under composite keys over the account, and one rule (`cash_ledger.StatementCoverage`) replaced `ReconciledThrough.covers` for every cash consumer. Balance-neutral with nothing backfilled. Opened **N-285**-**N-289**. |
| **X-f3d** | `98ea657d` | A balance assertion's counter leg NAMES what the difference was: a total dispatch over `classify_account` sends an `INTEREST` true-up to per-account Interest Income and an `INVESTMENT` / `APPRECIATING` one to per-account Change in Value in a sixth reporting class, while every OPENING stays on `anchor_equity`. Closed **N-276**; opened **N-277**, **N-278**. |

## What still depends on them, and why that is legal

Three live sentences in `../README.md` name these two steps, and all three cite them for HOW
SOMETHING CAME TO BE rather than resting on anything written here (`conventions.md` rule 15):

* **X-f4** notes that its deletion set is narrower "because X-f3a-1 re-pointed the cash consumers"
  -- a statement about the code, checkable in the tree;
* **X-f3c** notes that "PLAIN's arm of X-f3d's dispatch flips" at the cutover (ruling R-FO);
* **X-ay** notes the `$1,157.16` that "X-f3d must call a change in value, not a gain".

Each names a fact the code carries. None needs this file to be true, which is the test rule 5
applies: a live sentence may not DEPEND on an archived one.

**The findings both steps opened are LIVE and are not here.** `N-277`, `N-278` and
`N-285`-`N-289` are rows in `../../../plans/ledger.md`, each with its own owner, exactly as rule 5
requires -- unfinished work stays in the ledger whichever arc it came from.
