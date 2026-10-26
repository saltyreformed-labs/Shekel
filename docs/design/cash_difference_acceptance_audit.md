# Accepting an account's unexplained difference: the audit before the design

**STATUS: OPEN. Nothing here is ruled.** This document exists because plan step `balance:X-f3c-4`'s
specification was measured UNBUILDABLE-AS-WRITTEN on 2026-09-01, and the developer asked for the
full option space to be explored before anything more is built. It records what was measured, what
was refuted, what survives, and the options a design pass must weigh.
**It is not a specification and it decides nothing.**

Its subject is ruling `balance:R-GY`'s evidence bound and, behind that, finding `N-314`. The step's
own specification is its card in the tracker, and each ruling is a ruling card.

---

## 1. The problem in one paragraph

An account's OUTSTANDING DIFFERENCE is its latest asserted balance less what its books produce for
that same day (`balance_at.cash_outstanding_difference`, shipped at `X-f3c-3`). `X-f3c-4` proposes
to let an owner ACCEPT that difference as an ordinary uncategorized transaction (`R-FN`), offered
only over a span an imported statement reconciles (`R-GY`).
**The gate and the act's purpose are mutually exclusive**, and section 3 proves it. The step cannot
be repaired by adjusting a term.

---

## 2. What was MEASURED, with its date and its database

Every figure below was re-derived rather than quoted. **Each decays**; re-derive before relying on
one.

### 2.1 Production, restored from the dump of 2026-09-01 02:06:55

Restored into a throwaway, stamp `a4c6f1d92b73`, migrated to `e2d7a94f61c3`.

| fact | value |
|---|---|
| accounts the question applies to | **1 of 9** (account 1, Checking). Eight are a MODELLED kind or a loan (`R-FO`); user 2 holds none |
| Checking books open | 2026-03-26 at `$689.16` |
| governing assertion | 2026-08-28 at `$4,116.42` |
| what the books produce | `$5,893.73` |
| **OUTSTANDING DIFFERENCE** | **`-$1,777.31`** |
| assertions on the account | 62, spanning 2026-03-27..2026-08-28 |
| settled rows on the account | 161 |
| statement imports / bank lines / matches | **0 / 0 / 0** |

**THE SIGN IS NEGATIVE and every prose statement of this quantity had omitted it.** The books
account for MORE than the owner says the account holds, so the act books an **EXPENSE**, not income.
A step booking the wrong SIDE of the ledger is worse than one booking the wrong amount, because the
amount is checkable against a balance and the side is not.

**Corroborated by a producer sharing no code with the balance seam.** SQL over the posted
double-entry ledger, touching `balance_at` nowhere: `account_trueup` nets `-$1,777.31` on Checking's
linked ledger account against `+$1,777.31` on `Checking -- Opening` (`anchor_equity`). That equity
leg is finding **N-171**.

### 2.2 The act, REHEARSED on that restore and rolled back

Minting the row through the app's own doors (born Projected, settled through
`transaction_service.apply_requested_status`):

- **the balance line moved `$0.00` on all seven sampled days** -- the opening day, the day after,
  mid-span, the day before the assertion, the assertion's own day, the day after, and year end;
- the outstanding difference went `-$1,777.31` -> `$0.00`;
- `Checking -- Opening` went `+$1,088.15` -> `-$689.16`, i.e. the accumulated true-up plug drained
  to nothing and only the opening remained;
- `Uncategorized Expense` went `$0.00` -> `$1,777.31`,
  **through the existing `posting_service._settled_target` path with no new machinery**;
- the pay period holding 2026-08-27..09-09 went from `+$1,320.50` net to `-$456.81` -- five months
  of accumulated difference landing in one biweekly budget column.

So the mechanism WORKS. What is wrong is what the gate lets it be pointed at.

### 2.3 Cost

`outstanding_difference()` on a warm pass (the cash detail page's situation): **9 SQL statements**
on a restored dev clone at head carrying 3 imports and 378 lines; **28** cold. On a database holding
no import: **2** warm, **21** cold -- both terms query once and then answer nothing.

---

## 3. Why the specification cannot be repaired by adjusting a term

Write the difference in terms of the three things that can actually be wrong. Let `bank(T)` be the
account's true balance and define:

- `e_o` -- the stored opening is wrong: `opening_equity = bank(opened_on) + e_o`
- `e_m` -- the app's movement records are wrong: `SUM(recorded) = SUM(bank lines) + e_m`
- `e_a` -- the owner's typed balance is wrong: `asserted = bank(asserted_on) + e_a`

Substituting into `difference = asserted - opening_equity - SUM(recorded)`, the bank terms cancel:

> ### `difference = e_a - e_o - e_m`

**Untracked spend -- N-171's subject, and the whole reason this step exists -- is `e_m` and nothing
else.** `e_o` is fixed by restating the books; `e_a` is fixed by declaring a corrected balance. Only
`e_m` is a transaction.

Two facts make the identity exact rather than approximate, and both were verified in the code:

1. `cash_ledger.dated_deltas` (`_walk.py:350`) and `balance_at._cash_fold`'s `recorded`
   (`_cash_fold.py:595-597`) are the SAME expression over the same walk --
   `[(fact.settled_on, fact.delta) for fact in walk.source_facts]` in both places.
2. No movement may be dated on or before `opened_on`. This is enforced at the DATABASE tier since
   `X-f3c-2b-1` (`ck_movement_after_books_open` on both movement tables, via `budget.books_hold`),
   so no posting counted in `books` can fall outside the span.

**Now the trap.** `SpanAgreement.reconciles` proves `e_m = 0` on every day it covers.
`OpeningCorroboration.agrees` proves `e_o = 0`. What remains is `e_a` -- a typo.

**Every conjunct available from bank evidence constrains `e_m` toward zero.** The gate's strength
and the act's purpose point in opposite directions: tighten it and you prove harder that there is no
spend to book; loosen it and you lose the evidence that licensed booking anything. There is no
setting of a bank-evidence gate in which "the evidence is strong" and "the difference is unrecorded
spend" are both true, **because the evidence IS the proof that they are not**.

**This is a defect in the specification, not in the implementation.** The implementation was
reviewed separately and found correct (section 6).

### 3.1 The same result reached from the other end

Both adversarial reviews of 2026-09-01 reached this independently, and the branch's own
satisfiability control demonstrates it without modification:
`test_a_reconciling_span_with_a_DIFFERENCE_is_offerable` builds an import stating `$425.00` for
2026-03-04, books that produce `$425.00`, every day's residue `$0.00`, a corroborated opening -- and
an owner declaring `$400.00`. `is_offerable` is `True`, and **the act would book a `$25.00` expense
on a day the imported, line-by-line-reconciled statement says the account closed at `$425.00`**.

---

## 4. The root cause, and what already owns it

**The app has two competing authorities for a balance LEVEL -- the owner's assertions and the bank's
closing balances -- and no rule saying which wins.** That is finding **N-314**, measured at ruling
`R-FL`: of 55 Checking assertions only **17** equal the bank's closing balance for their day, 9 are
that day's closing plus a subset of its own postings, and **29 are neither**.

Every symptom in this arc descends from it. The assertion RESET was a level masquerading as a
movement. The equity plug is that confusion's residual. And `X-f3c-4` as specified would convert a
level disagreement INTO a movement -- the same category error running the other way.

**It is already owned and no new step is needed for it:**

| what | where | state |
|---|---|---|
| the bank's figure becomes the app's level | `bank_import:X-gh`, ruled **R-GL** 2026-08-24 | open; **deliberately sequenced AFTER `balance:X-f3c`** so it is written against the post-cutover assertion rather than the one `X-f4` deletes |
| restate Checking's opening from the bank | **N-275** | operator |
| import the running-balance export to production | **N-368** | operator |
| do the bank's DAILY closings become assertions, or opening restatements? | **N-314** | **developer-decision, re-opened 2026-08-27** |

**N-275 states** (quoted, not measured here) that account 1's opening ASSERTION is `$2,746.58` for
2026-03-27 where the bank's own closing that day is `$3,182.63`. Note this is the ASSERTION, not the
stored opening EQUITY, which section 2.1 measured as `$689.16` at 2026-03-26. The two are different
facts and a first draft of the branch's own docstring conflated them.

**N-368 states** that the running-balance export on disk covers 2026-01-02..2026-07-17 and that SECU
stopped exporting the column between the 2026-07-19 and 2026-08-16 pulls, so the self-verifying span
is closed and finite.

### 4.1 A CYCLE the design pass must break

`X-f3c-4` needs to know whether a difference is `e_a`, `e_o` or `e_m`. Attributing that needs the
level rule, which is `X-gh`. But `X-gh` is sequenced after the cutover, and the cutover's flip
(`X-f3c-5`) is sequenced after `X-f3c-4`.
**The plan therefore contains a dependency cycle that no `blocked by` cell expresses**, and any
design must say how it breaks it.

---

## 5. THE OPTION SPACE -- to be explored, NOT decided here

Six options were identified. **None is ruled and the list is not asserted to be complete**; a design
pass should widen it before narrowing it.

### Option 1 -- INVERT R-GY: offer over the span nothing has read

Offer where bank evidence is ABSENT rather than present, since that is the only place `e_m` can be
non-zero. Concretely: `disagreeing == 0` and `unchecked == 0` (everything read, agrees)
**and `unimported > 0`** (some days nobody read), plus the opening test weakened from *corroborated*
to **not contradicted** (`bank is None` or `bank == stored`), which is the shape
`honoured_correction` already uses -- a stronger record beats a weaker one, and its ABSENCE leaves
the weaker standing.

Yields three states and three honest answers: no import at all -> offer (which preserves `R-FN`'s
own bound, see section 7); fully reconciled -> refuse, and name the true-up door because the
difference is `e_a`; partially imported -> offer the residual over the unread days.

**Breaks the cycle**, because it gates on COVERAGE (shipped as `SpanAgreement`) rather than on the
level ladder. **Cost:** over unread days `e_a` and `e_m` remain indistinguishable, so a mistyped
balance is bookable as spend there. **Unverified claim to check:** that after importing the
running-balance export, 2026-07-18..2026-08-28 is genuinely unread and the residual over it is
small. Nobody has measured this.

### Option 2 -- ATTRIBUTE per sub-span, and offer a remedy per cause

Decompose the span by evidence and route each part to its own shipped door: covered-and-disagreeing
days to the matcher, a contradicted opening to the restatement door, the unread remainder to the
acceptance act. Richer and more honest than option 1; more surface, and it needs a per-day
attribution the instrument does not currently publish.

### Option 3 -- Do `X-gh` FIRST and shrink the act

Settle the level rule (N-314) before the cutover, so the bank's closing governs wherever it exists
and the acceptance act shrinks to the genuinely-no-evidence case.
**Requires revisiting `R-GL`'s ordering**, whose stated reason -- the ruling must be written against
the post-cutover assertion -- is sound and would have to be answered rather than overridden.

### Option 4 -- Per-account cutover instead of a global flip

Keep the assertion RESET for accounts with no bank coverage and flip only those that have it. Nobody
has costed this; it re-opens `R-EB` and it is the option most likely to leave two balance semantics
alive at once, which is the defect this whole arc exists to delete.

### Option 5 -- Unify the two level authorities in ONE record

The from-scratch shape: `budget.account_anchor_history` (the owner's levels) and the bank's stated
closings become one evidence-ranked LEVEL table, with `StatementBalanceEvidenceEnum` and
`weaker_of` -- both shipped -- deciding which governs for a day. That is N-314 built structurally
rather than ruled case by case, and the acceptance act then books only what the strongest available
level cannot explain.
**Largest change; most likely to be the real answer; needs its own decomposition.**

### Option 6 -- Do not book a transaction at all

Reject `R-FN`'s premise that an unexplained difference is a transaction, and treat it purely as a
reported disagreement between levels. **Fails `R-FN`'s own bound** (section 7) unless something else
carries it, and it is recorded here so the design pass rejects it explicitly rather than by
omission.

---

## 6. What the two adversarial reviews found on 2026-09-01

Two neutral reviewers, one on the code and one on the design, run independently.
**Both reached section 3's conclusion separately.** Their other live findings, none of which is
resolved:

1. **After `X-f3c-5`, a typed balance is INERT** for any account without a reconciling import, and
   production has zero. An assertion stops moving a plain account's balance, so a typed balance
   counts only by becoming a row -- and the gate refuses. This inverts `R-FN`'s own bound (section
   7) for exactly the user it was written for.
   **Ruling-level; belongs to the flip as much as to this step.**
2. **The partial unique index contradicts `R-IP`, in the same commit.** R-IP freezes on
   *categorised OR carrying a retained `corrected` record*; the index excludes only *categorised*.
   Correcting the figure in the popover leaves the row frozen but still in the index, so R-IP's
   "else it mints one" is refused by the schema. `settled_basis_id` is a ref id and a partial index
   cannot join to `ref` to learn which id means `corrected`, so this is a genuine fork.
3. **`category_id` is in `_LOCKED_EDIT_FIELDS`** (`routes/transactions/_helpers.py:85-87`). `R-FN`
   rests on the row being *"CATEGORIZABLE later -- which is the mechanism that shrinks the bucket"*.
   On a settled row that is revert -> categorise -> re-settle. **The mechanism the ruling depends on
   is not one click and nobody has specified the three-step version.**
4. **A new governing assertion MINTS a second row.** The index keys per ASSERTION, not per account,
   and assertions are append-only -- so re-asserting a corrected balance for the same day creates a
   second uncategorized row. That is R-IP's own rejected option, reached through the documented
   correction path.
5. **Three ways an owner's own figure reaches the row that the freeze misses**: editing `settled_on`
   (not locked on a finalised row) moves the row off `asserted_on` and reopens the full difference;
   ticking *track purchases* flips the basis to `PURCHASES`, which the reprice arm RAISES on; and
   revert -> retype the estimate -> re-settle writes a `derived` record at the owner's number.
6. **The fold-in arm breaks on a matched row** -- `statement_match._accept._apply_day` re-dates a
   settled member to the bank's day, after which the reprice either destroys that bank-observed day
   or never converges.
7. **`_opening_corroboration` issues queries `BankAgreement.runs`' own docstring forbids its reader
   from issuing**, and re-derives `covered_runs`, which the report already carries as `runs` (this
   item first named `BankAgreement.imports`, the runs' spans, which `balance:X-f3c-2b-2c` deleted at
   `d6285573`).
8. **No concurrency story**: two tabs both accepting produce an `IntegrityError`, and there is no
   staleness guard equivalent to `_reject_moved_since_review` on a money-moving door.
9. **Undo, the grid, and spending analysis are unaddressed.** A soft-deleted row leaves the partial
   index, so re-accepting mints a second; the lump lands whole in one pay-period subtotal (section
   2.2 measured `+$1,320.50` -> `-$456.81`); and it buckets under
   `("Uncategorized", "Uncategorized")` in every category chart for its period.

**What SURVIVED both attacks**, so a design pass need not re-litigate it: the composite foreign key
makes a cross-account link unrepresentable; `ondelete="RESTRICT"` causes no disposal regression; the
day arithmetic in `_opening_corroboration` is exact with no off-by-one; there is no quantization
hazard in the Decimal comparison; no IDOR is introduced; the migration's downgrade is byte-lossless;
`BooksSpan.is_empty` holds exactly when `SpanAgreement.day_count` is zero; and the fold-in
ARITHMETIC is correct (repricing rather than minting is genuinely necessary there).

---

## 7. What is SETTLED and must not be re-opened casually

- **`R-FN`** -- an unexplained difference is a transaction the owner ACCEPTS, with no category,
  booked to the per-owner Uncategorized ledger account through the existing category-leg rule, and
  categorisable later. **Its bound is the load-bearing part**: *"typing a balance away from a
  computer must still move the projected end balance, and it does -- in one accepted act that leaves
  a row you can find, instead of a plug you cannot."* Any design that leaves a typed balance inert
  fails this.
- **`R-FO`** -- the question does not apply to a MODELLED account; the same subtraction there is its
  return, not untracked spend.
- **`R-IP`** (2026-09-01) -- the accepted row OWNS its figure; the derivation is an OFFER, not a
  read-time recomputation; a reprice is revert -> restate -> re-settle. **Survived both reviews.**
- **`R-IQ`** (2026-09-01) -- **its RATIONALE is refuted by section 3**: it removes `e_o` and then
  books `e_a`. Its *mechanism* (compare the stored opening against the bank's own closing for
  `opened_on`) is sound and reusable; its *direction* (require corroboration) is what option 1 would
  weaken to non-contradiction.
  **The developer holds this ruling and it is his to amend or withdraw.**

---

## 8. What is BUILT and green on `feat/x-f3c-4`

Uncommitted at the time of writing.
**The schema half is independent of the gate's design and survives every option in section 5.**

- `budget.transactions.accepted_residual_for_id`, a COMPOSITE foreign key over `account_id`, and a
  PARTIAL UNIQUE index -- all four behaviours verified against restored production data.
- Migration `31bb08f73e50`, both directions, **byte-identical round trip** (7,430 lines of
  `pg_dump --schema-only`, `diff` exit 0).
  **Its parent `e2d7a94f61c3` is now two revisions stale; re-parent and re-measure.**
- `OpeningCorroboration` and a three-conjunct `is_offerable` -- **the part section 3 refutes.**
- Six tests, each conjunct mutation-verified to fail one case and only one.
- `docs/plans/ledger.md`: N-171's two undated figures STRUCK.
- `docs/plans/rulings.md`: `R-IP` and `R-IQ` recorded (250 rows; parser confirmed to see both).

`pylint app/` 10.00/10; plan gate 293 passed; `tests/test_models` + `tests/test_arch` 1007 passed.

---

## 9. Questions a design pass must answer

1. Which of section 5's options -- or which combination, or what else -- and why?
2. What happens to a typed balance after the flip on an account with no bank coverage? (Finding 1.)
3. Does `R-GL`'s ordering of `X-gh` after the cutover survive section 4.1's cycle?
4. Is `R-IQ` amended, withdrawn, or kept?
5. What does the freeze actually key on, given finding 2 -- and can the schema express it?
6. Is `R-FN`'s "categorisable later" achievable at all while `category_id` is locked on a settled
   row? (Finding 3.)
