> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The pay calendar, as built: the C2-a - C2-e span (2026-08-18)

**Seven shipped steps, condensed out of `implementation_plan_pay_calendar.md`
section 4 under `conventions.md` rule 5** when that document reached 494 lines
against its 500-line cap while `C2-f3`'s decomposition was being written.  Rule
5's three conditions hold: every finding these steps opened and did not close is
still live in `ledger.md`, no live sentence depends on a sentence here, and
nothing below was re-verified in the move -- these are the entries as they stood.

**What survives in the live document is one line per step**, its id and its
commit.  The commit is the record; read the code it shipped, not this.

## The entries as they stood

- [x] **C2-a -- the one calendar VALUE, and nothing calls it.** `3cb3082f`. Opened **P21**-**P25**.
      Proof: `_calendar.py`'s docstring.

- [x] **C2-b -- the recurrence cutover.** `fe365de1`. The DECOMPOSED parent, ticked with C2-b2, its
      last leaf.

- [x] **C2-b1 -- the last two questions, the cadence rule, and one door.** `90f2fbb7`. Opened
      **P28**. Proof: `_loader.py`'s docstring.

- [x] **C2-b2 -- the cutover.** `fe365de1`. Closed **P2** (= recurrence **F-10**) and **P25**;
      opened **P34**, **P35**. Proof: `recurrence/_occurrence.py`'s docstring, which states the
      THREE shapes where the derivation and the stored columns disagree, and the byte-identical
      430-shape baseline. **P26**, **P27** and **P28** re-pointed to **C4**: each owed a STATEMENT
      here and now owes only the column.

- [x] **C2-c -- the cash-view cutover.** `b8a72f6c`. `_PeriodSpans` is DELETED and the balance
      seam's THIRTEEN per-period entries stopped taking a period list: the domain is
      `BalanceContext.reported_periods()`. Closed **P14**, **P24**, **P32** and `balance:N-128`;
      opened **P36**-**P39**. Proof: `_window.py`'s docstring, and the corrupted-column pin in
      `test_cash_period_view.py` with its firing control.

- [x] **C2-d -- the filing cutover.** `3e6cd4ec`. Closed **N-169**. Proof: `filing_period`'s
      docstring and `tests/manual/verify_filing_cutover.py` (1,654 days, 0 disagreements).

- [x] **C2-e -- the projection axis.** `8143c6fe`. All six call sites run on
      `PayCalendar.projection_axis`; `generate_projection_periods` and `SyntheticPeriod` are
      DELETED. Closed **P17**, **P20**, **P21**, **P22**, **P23**; opened **P40**-**P44**.
      **P7 is RE-POINTED to C2-f, not ticked** (developer 2026-08-14): its projection half shipped
      here, the tier its `+$5,427.07` was measured on did not. Proof: `projection_axis`'s docstring
      and `tests/manual/verify_projection_axis.py` against a production clone.

## The C2-f1 and C2-f2 entries, condensed 2026-08-19

Moved here for the same reason and under the same rule when `C2-f3`'s
decomposition took the live document back over its cap.  **Two shapes a later
step must not undo, and they are the reason this section exists rather than a
deletion**: `period_starting_after` / `_before` filter to MATERIALISED periods,
which is what makes the credit-payback FK write safe with no guard; and a
surface that SELECTS its periods by the derived span must PLACE its rows by
that same one -- splitting those cost `$1,234.56` on a planted disagreement.
`C2-f2`'s own four are in `pay_calendar_c2f2_as_built_2026-08-18.md`.

- [x] **C2-f1 -- the three the calendar already answered.** `792e3b21`. Opened **P45**-**P50**.
      **TWO shapes a later step must not undo.** `period_starting_after` / `_before` filter to
      MATERIALISED periods, which is what makes the credit-payback FK write safe with no guard --
      `filing_period`'s correction, taken again. And a surface that SELECTS its periods by the
      derived span must PLACE its rows by that same one: splitting those cost `$1,234.56` on a
      planted disagreement. Proof: `tests/manual/verify_period_window_cutover.py`'s docstring.

- [x] **C2-f2 -- the readers at a surface that already holds a read pass.** `531c1402`. The
      DECOMPOSED parent and all five leaves, condensed into
      `historical/pay_calendar_c2f2_as_built_2026-08-18.md` under `conventions.md` rule 5, with the
      FOUR shapes a later step must not undo. Closed **P36**-**P37**, **P43**, **P48**, **P55**,
      **P57**-**P59**, **P61**, **P65**-**P66** and three of **P56**'s eight modules (five survive);
      opened **P52**-**P54**, **P60**, **P62**-**P63**.

## Row P6's census, as it stood before C2-f3a emptied it

Seven implementations of "which pay period contains this date", moved here from
the live `C2` entry on 2026-08-19.  Three had gone when this was written:
`recurrence/_calendar.py:287` at `C2-b2`, `balance_at/_cash_periods.py:320` (a
bisect over the STORED spans, 3 in-module sites) at `C2-c`, and
`loan_ledger/_visible.py:150` at `C2-d` (`3e6cd4ec`, with
`resolve_anchor_pay_period` and `owner_pay_periods`).
`savings_dashboard_service._period_id_at` went at `C2-e`,
`get_overlapping_periods` at `C2-f1`, and
`investment_dashboard_service/_chart._build_chart_markers` -- a linear
containment scan over a `PeriodWindow` -- MOVED onto that type's own
`containing` at `C2-f2c` (row **P48**).  The last, and the only SQL one,
was `pay_period_service.get_current_period`, deleted at `C2-f3a`.

`entry_service.py:816` was EXCLUDED throughout: it asks MEMBERSHIP, the
primitive the searches are built on, not a search.
