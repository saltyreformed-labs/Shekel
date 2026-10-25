> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Three shipped bank_import entries condensed under rule 5 on 2026-09-22

`implementation_plan_bank_import.md` stood at 260 of its 280 lines -- the gate's 20-line headroom floor -- when
**R-BI38** widened `bank_import:X-gy`'s specification (`balance:X-bi-6-3`'s tick). Under
`docs/plans/conventions.md` rule 5 the contiguous shipped span `X-gv`, `X-gx`, `X-gz` was condensed to one line
per step; none carries an "a later step obeys" clause, and each keeps its `steps.md` row and its checkbox. The
entries as they stood, verbatim, carried WITHOUT re-verification:

- [x] **X-gv** `88f38feb` -- `locked_for_write` composes `populate_existing()` beside the mode, so a
      door reads the row the lock holds rather than the one `review_set` hydrated before it; vacuous
      on `lock_lines` (id column only), acting at `load_lines` and `_line_on`. Closed **BI-493**,
      REPRODUCED first at both doors: a skip landed on a line whose merchant now paid an account the
      owner holds (**R-JI**), and a purchase took its posting day over a stated transaction day.
      Graded by `test_locked_read_refresh.py` (4 cases, one firing control).
- [x] **X-gx** `c2e22790` -- `FiledMerchant` on `CreatedPurchase` and `AppliedItem`;
      `rules_worth_offering` takes the applied items and drops `review`; the press builds
      `RuleDoorAccepts` off `RuleView.build` and no longer runs `review_set`. Closed **BI-495**,
      REPRODUCED first (the door applied, the receipt offered nothing). Graded by
      `test_offered_rules.py` (BI-495 class + the real door behind every case) and `test_batch.py`
      (only the create arm names a merchant).
- [x] **X-gz** `08a66901` -- the match pane shows the dates a human verifies by (**R-BI9** and its
      two sub-rulings; closed **BI-498**; outcome O0 delivered 2026-09-16): each candidate row
      carries its BUDGETED placement and, for an envelope entry, its `purchased_on`, every date
      labelled by kind in the paycheck register's MM/DD, the gap to the bank's date printed, the
      settle day never shown (`.rec-row-day` now names the per-fact span, `accounts.css:1205-1211`);
      `CandidateRow` carries `period` and `purchased_on`; `_dating.py` presents; `_caveat.py` split.

And `X-gy`'s specification as it stood before **R-BI38** widened it (a LIVE step, re-cut, not archived):

- [ ] **X-gy** `chore(ci): the suite's CI clock is measured, then fixed` -- **BI-496**. CI runs a
      database-bound test 5-13x slower than the host and only ~2x of it is accounted for; a matched
      A/B on the runner names the rest, the fix lands with its measurement, and `pytest.ini`'s cap
      is re-sized from CI's own `--durations` table. Minted 2026-09-13 by the developer from the
      coordinator's triage of PR #337's timeout.
