> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `X-f3c-2c` as built: an assertion is append-only (2026-08-30)

**What this is.** The three `* [x]` entries that left `../README.md` section 5 on 2026-08-30 under
conventions rule 5 -- `X-f3c-2c` and its two leaves -- archived the day they shipped, because the
live document stood at **exactly 980 of its 1,000-line cap against a 20-line headroom floor, zero
room**, and `X-be`'s specification needed six lines for a measurement that arrived after the step
merged. That is the same state and the same remedy as `four_shipped_steps_2026-08-30.md` earlier the
same day, and as `five_shipped_steps_2026-08-26.md` before it; the developer chose this remedy on
2026-08-29 when asked. **The COMMIT is the record**: read `930f06fc`, not this file.

Merged in PR #161 at `0a7c2aef`. CI run 33334394940, attempt 1, **11,918 passed** (a local
`./scripts/test.sh` reads 11,890 -- the 28 are `tests/test_deploy`, which the wrapper deselects and
CI runs bare).

## The three entries, verbatim as they stood

* [x] **X-f3c-2c** `930f06fc` -- the DECOMPOSED parent of the append-only refusal (**R-HZ**):
  the fixtures stop editing an assertion and the refusal that makes editing one impossible ship
  TOGETHER, so no tree has one without the other. Closed **N-287**; opened **N-392**, **N-393**.
  * [x] **X-f3c-2c-1** `930f06fc` -- a fixture PLACES an assertion and never edits one: three
    re-stamping helpers become `reassert_balance_on`, `append_balance_assertion` states both
    clocks at INSERT, the factories take their day at `create_account`, and the seeded
    origination stays on the bootstrap day. ~40 cases state their own asserted day now.
  * [x] **X-f3c-2c-2** `930f06fc` -- `budget.refuse_append_only_change` on all three tables
    (**R-HY**): every UPDATE refused, a DELETE refused while the owning ACCOUNT stands, so
    `ON DELETE CASCADE` stays the disposal path. `Account.anchor_history` takes
    `passive_deletes="all"`, which N-287's own evidence missed; `append_only_guard_lifted`
    keeps the three controls beneath the trigger graded.

## What a LATER step must still obey

**Three controls sit BENEATH the trigger and are reached past, never deleted.**
`fk_transactions_reconciled_by`'s `ON DELETE RESTRICT`, `ck_books_open_before_movements`' UPDATE and
DELETE arms, and `account_posting_service`'s posted-only reversal branch are all shadowed by the new
refusal, which would answer first and leave each one graded by nothing.
`_test_helpers.append_only_guard_lifted` lifts the trigger for exactly one statement and restores it
in a `finally`. **Deleting one of those controls because a newer guard stands in front of it trades
a measured refusal for an argument** -- the reason the instrument exists rather than the shorter fix.

**A migration that must rewrite `account_anchor_history`, `account_openings` or `loan_anchor_events`
calls `remove_append_only_infrastructure(op.execute)` first and re-applies after.** Two lines,
visible in the diff, refused loudly if forgotten. This project puts one-time backfills in the
revision that changes the schema, so the case is the norm rather than the exception --
`e5b2c8a17d34` backfilled `account_anchor_history.recorded_on` exactly that way five days earlier.
**UNRESOLVED and left for a ruling:** that migration's own downgrade still prescribes a hand-run
`UPDATE` as the operator's escape hatch, which the trigger now refuses.

**`X-f3c-4` and `X-f3c-5` inherit an append-only table.** The cutover's accept act writes a
transaction, not an assertion, so neither is blocked -- but a remedy that reaches for editing a
stored assertion is no longer available to either, and the reconcile that reverses the 162
`account_trueup` entries reverses JOURNAL entries rather than the assertions behind them.

## Two of N-287's own claims were measured FALSE, which is why the step cost what it did

* *"No `app/` path updates or deletes a row today (AST-checked)."* The census looked for a
  `.delete()` NAMING the model, so it could not see `hard_delete_account` disposing of every
  assertion through `Account.anchor_history`'s `cascade="all, delete-orphan"`. Four cases fail
  without a fix, and `passive_deletes=True` is not it: for children already in the session
  SQLAlchemy still nulls their foreign key, an `UPDATE` the guard refuses just as loudly.
  `passive_deletes="all"` is. **A case that does not LOAD the collection first passes either way.**
* *"The guard is four lines."* **539 tests.** 501 after the relationship fix and an INSERT-only
  `append_balance_assertion`; 150 after three test factories take their day at `create_account`;
  40 after the remaining call sites; +22 once the trigger caught what an ORM listener cannot see.

Both corrections stayed in N-287's own worst-measured cell when it closed, because a finding whose
evidence was wrong is the more useful record.
