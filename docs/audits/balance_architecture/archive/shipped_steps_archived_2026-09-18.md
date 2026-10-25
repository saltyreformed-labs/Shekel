> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# 7 shipped balance steps archived under rule 5 on 2026-09-18

The balance README stood at 1,296 of its 1,330 lines with the `X-bj-1b` tick to land, four lines
from the gate's 30-line headroom arm. These 7 SHIPPED steps are cited as a wait or an alias by
NO other row of `docs/plans/steps.md` (measured on the tick's tree with the gate's own
`blocked_keys` / `alias_keys` plus a scan of `(shipped` notes), so under `docs/plans/conventions.md`
rule 5 their rows leave the index and their README entries leave section 5, one line per step
here: its id, its commit and what it did, as its index row said. Carried WITHOUT re-verification.
A live sentence may cite one of these ids for how something came to be, never as a plan of record.
Their containers (`X-bi-7c`, `X-bi-3`, `X-bi-7b`, `X-bj-1`) keep their rows: a live step cites each.

- **X-bi-7c-1** `555410b6` -- Built `one_off_row_of` on `one_off.place_one_off` (the twin of `generate_row_of`), put the three bare builders on it with `legacy_link_less_row_of` as the one transitional home and `payback_row_of` on the payback's producer, committed the AST census, and fixed the 16 failures the move surfaced (**R-BAL58**, **R-BAL59**). Opened **BAL-511**.
- **X-bi-7c-2** `1c1b2de8` -- Moved the 60 hand-built rows of `tests/test_services/*`, `tests/test_utils` and `tests/test_ref_cache.py` (25 files) onto `one_off_row_of` by the committed mover `tests/manual/move_hand_built_rows.py`; 7 failures classified (4 legacy-subject onto `legacy_link_less_row_of`) and five cases that graded the placed branch twice put on the legacy home, named for 7d.
- **X-bi-7c-3** `b9ee1495` -- Moved the 58 hand-built rows of `tests/test_routes/*` except the grid pair (26 files) onto `one_off_row_of` by the committed mover; 15 failures classified (10 raw-column readers re-aimed at `resolved_amount`, 2 locking cases on an owner-repriced engine row, 3 legacy-subject; **R-BAL60**); three category-delete cases that graded the definition alone re-aimed at the row. The marker 227 -> 169.
- **X-bi-7c-4** `a32a7c98` -- Moved 89 of the grid pair's 90 hand-built rows (`test_grid.py` 72 + 7 splat, `test_grid_regression.py` 11) onto `one_off_row_of`, 81 by the committed mover and 8 by hand; 2 failures of R-BAL60's raw-column class re-aimed; two double-graded R-BAL34 cases and two own-flag fixtures put on the app's own state; ONE bare row stays, grading the table's NOT NULL. The marker 169 -> 80.
- **X-bi-7c-5** `4f15f222` -- Moved 50 of the last 67 hand-built rows (integration, models, adversarial, performance, concurrent, audit_fixes) onto `one_off_row_of`; 17 failures classified (15 raw-column readers under R-BAL60, 3 legacy-subject); three benchmark workloads re-cut over ONE rule-less definition per batch; 16 bare CHECK builders named with the sentence each grades. The marker 80 -> 30.
- **X-bi-3c** `68401855` -- Covered both legs of a settled transfer through the ONE seam `transfer_service` already reaches per shadow, the transfer gate deleted (**R-BAL41**); the ledger's endpoint ruled C, two movements against a transfers-in-transit account (**R-BAL45**), a shadow's movement posting nowhere until `X-bi-6`; an endpoint move carries the movements (**R-BAL46**, migration `c4e8a2d7f1b3`). `$0.00`.
- **X-bj-1a** `1d1f7ce8` -- Build the ONE evidence-ranked level relation: the owner's true-ups and the bank's per-import closings in `budget.account_anchor_history`, a release an appended `budget.anchor_releases` row, the reset reading the owner's rows through one predicate until the flip, and the badge naming a release's cause (**R-BAL47**..**R-BAL57**). Closed **BAL-485**.
