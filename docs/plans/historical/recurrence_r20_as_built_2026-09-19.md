> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# recurrence:R20 as built (2026-09-19)

**The setup door records the stated balance as the assertion it is.** `R20`'s entry in
`implementation_plan_recurrence_redesign.md` as it stood the moment it shipped (`b4da8068`, on
`b141e779`'s pure move of the loan anchor doors into `app/services/loan_anchor_service.py`;
migration `22b23085394d`) -- moved here under conventions.md rule 7, because a ticked entry holds six
lines and this one carried the argument `R-R72`'s row was trimmed of on 2026-09-12. The `[ ]` below
is the entry as it stood before the tick.

## As built (what the entry below does not say)

* **Two commits.** `b141e779` moves `_governing_loan_anchor`, `_append_loan_anchor_and_sync`,
  `apply_loan_anchor_true_up` and `record_loan_tracking_start` verbatim out of `anchor_service.py`
  (at exactly 1,000 lines) into `loan_anchor_service.py`; `b4da8068` is the leaf.
* **The migration's reading of "NO stored assertion of any source".** The spec's sentence is below;
  the migration excludes the two ASSERTION sources by name (`user_trueup`, `tracking_start`) and
  does NOT count a legacy `origination` row as one, because every reader synthesizes the origination
  and ignores that row (`loan_loaders.load_loan_anchor_facts`), so a loan carrying only one is
  exactly the loan the backfill exists for. Both readings match 0 of 2 on production (measured on
  the clone: both live loans carry a 2026 `user_trueup`); up / down / up clean; the downgrade
  restores `current_principal` NULL and `ck_loan_params_curr_principal`.
  `test_r20_backfill_records_the_unasserted_stated_balance_once` pins the reading with a case per
  direction. The migration PRINTS its count; production's log is expected to say `0`.
* **"Typed the fact twice" was measured false, and the design consequence is unchanged.** The
  Van Loan's 2026-05-22 `user_trueup` (event 4) and the Mortgage's (event 3) carry
  `created_at 2026-05-22 02:41:22.187019+00`, the SAME instant as the two legacy `origination` rows
  (1, 2): `d3d25212504b`'s own backfill wrote them (a `user_trueup` at `(today, current_principal)`
  where the stored figure differed from the replay), dated the migration day. The column's figure
  was RECORDED as an assertion by a migration, not typed by the owner; either way the door had no
  home for it, which is what R20 gives it. (Re-read on the clone by the coordinator at the tick.)
* **What moves.** A loan set up after this ships gets a `tracking_start` at the "as of" day
  (default the display-day today) carrying the "Balance today" whenever it originated before that
  day, and replays from THAT assertion (R-R71 charges only the months after it). No existing loan's
  figure moves: the leaf's commit message reports `tests/manual/verify_loan_plan_sum.py` byte-identical,
  base vs branch (3,617 lines).
* **Four censuses moved, not the one the leaf reported**: `get_baseline_scenario(` 10 -> 9
  (steps.md X-y), `date.today()` in tests 198 -> 205 (ledger.md N-138: the leaf's tests read the
  process clock where its door reads the display day), `Numeric(12, 2)` 48 -> 47 (the column) and
  `due_date` files 62 -> 61; measured with `_census.census_violations()` on `b4da8068` against a clean
  dev `8819185a`.
* **The review's Lows** (the leaf's commit message reports no Critical / High and two Mediums fixed in
  `b4da8068`) are questions for the developer, held in the coordinator's batch: the prefill's two dates (balance asserted for the
  account's `observed_on`, date defaulting to today); a stated balance ON the origination day that
  contradicts `original_principal` is dropped in silence (lane recommends a refusal); the dashboard's
  sibling forms prefill `today_iso` from `date.today()` where the setup form reads `display_today()`
  (the clock-split class, pre-existing) -- and the setup door itself straddles both clocks in one
  request: `LoanParamsCreateSchema` refuses a future `anchor_date` against `date.today()` while the
  form's default and `max` and the origination exemption read `display_today()`, so between 20:00 and
  midnight America/New_York the schema accepts a crafted date one day past the form's `max` (N-138 /
  C10's class; the tick's review); `seed_dast_users.py:332` still constructs
  `LoanParams(current_principal=...)` (a dated audit tool, already broken since `interest_rate` went).

## The entry, verbatim

- [ ] **R20** -- The setup door records the stated balance as the assertion it is.

**Root cause (REC-519):** the setup form requires "Current Principal", `create_params` stores it in
`LoanParams.current_principal`, and nothing reads it; a loan configured mid-life has only its
synthesized origination assertion, and under R-R71 its unrecorded months read as unpaid.
**Design (R-R72 part 3):** the field becomes "Balance today" with an "as of" date defaulting to the
setup date and bounded `[origination_date, today]`; `create_params` appends a `tracking_start`
`LoanAnchorEvent` (`anchor_service.record_loan_tracking_start`) in the same transaction as the
params whenever `origination_date < as_of` -- a loan originating today or later asserts nothing, its
origination IS the assertion; a migration drops `current_principal` and
`ck_loan_params_curr_principal` after appending a `tracking_start` at `loan_params.created_at`
carrying `current_principal` for every loan with `origination_date < created_at` and NO stored
assertion of any source (measure the count on the clone first; both live loans carry one, so
production backfills nothing, and the downgrade restores the column NULL); the tracking-start
route's "STRICTLY BEFORE the earliest recorded payment" refusal is deleted -- an assertion after
payments is what a true-up already is, and the two sources differ in label alone
(`anchor_service._append_loan_anchor_and_sync`); `tests/_test_helpers.create_loan_account` writes
what the door writes, and the fixtures R16-b-2 corrected to assert their balance at the read date
(`test_loan._TRACKED_FROM`, the `liability_owed_at_dates` and dashboard mortgages, the matured
balloon) take that shape. **What R-R72's row no longer carries (rule 4, moved here 2026-09-12 when
the row was trimmed under the 2,000-character cap):** the seventeen fixtures that failed under R-R71
were every one a loan whose only assertion was years old; the worked example over the app's own
producers (`worked_door.py` in the handoff directory): `$250,000` at 6.5% over 360 months from
2023-06-01 -- a paying borrower owes `$240,215.10`; under R-R71 with today's door it reads
`$302,586.63` after its first plan payment (40 months standing, `$54,166.80`) and never clears; with
the tracking-start it accrues `$1,301.17` and pays off 2053-07-01. REFUSED together at R-R72, as
dancing around the root cause: bounding the calendar at the later of the assertion and the
schedule's opening (a pay calendar is evidence of nothing about a loan); the assertion bound with
the door left as it is; and a door that REQUIRES a second balance entry. Clone evidence (REC-519):
the Van Loan was set up 2026-03-27 with `current_principal` `$17,020.47`, carries no
`tracking_start`, and its first assertion after the 2023 origination is a `user_trueup` of
2026-05-22 for exactly `$17,020.47` -- the owner typed the fact twice. **Verification:** the setup
route's tests (a past origination writes ONE `tracking_start` at the stated date; a future one
writes none; the stray-field case), the migration up and down on a clone, and
`tests/manual/verify_loan_plan_sum.py`'s baseline byte-identical.
