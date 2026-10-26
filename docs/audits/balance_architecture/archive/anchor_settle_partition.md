> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The anchor/settle partition: when is a settled row already inside an asserted balance?

Status: **Steps S1-a, S1-b and the N-133 residue are IN PRODUCTION (PR #67 `fd0ddfab`,
PR #68 -- prod at `c4a19e7b2d80`, deployed 2026-08-01).**

**S1-c IS IN PRODUCTION** (PR #75, merge `51e07e74`; prod at migration `d7c1f4a9e603`, confirmed
on a fresh clone 2026-08-01).  Ruling R-M was RE-RULED in the course of it and the shape changed:
`transaction_entries.entry_date` SPLITS into `purchased_on` + `settled_on`, and reconciliation is
derived from an OBSERVED posting day rather than from a guess.  See Section 12 for the rulings and
Section 13 for what was built, what the conversion cost, and what a neutral adversarial review
found in it.

**Step 3 is IN PRODUCTION** -- committed as `d3e3d82a` on
`fix/one-partition-implementation` (2026-08-01), merged 2026-08-02 at PR #76 (`6f4b4cbf`), which
is the first time CI graded it: CI runs only on pull requests and pushes to `main`, so no push to
that branch could ever have graded it.  **Deployed 2026-08-02** (image `sha256:4ec10f50018d`,
container healthy with 0 restarts); this line read "NOT YET DEPLOYED" until then, and the ship
carried **no migration**, so it was a pure image swap and a rollback would be a pure digest revert.
**It did NOT ship the pylint checker
the step specified.**  The developer ruled the fence must be structural rather than a
detector.  **This paragraph used to justify that by saying an AST census showed the checker would
have been BLIND at `account_posting_service/_sync.py`'s bare-local `earliest <= latest` -- and
Section 14.1 of this same document WITHDREW that claim after step 3's own adversarial review
refuted it** (adding those names to the vocabulary matches them; resolving a `Name` to its `Assign`
is one astroid hop).  The header outlived the withdrawal by a day and was corrected on 2026-08-02,
after plan step X-ae cited the withdrawn version a second time.  What survives is the RULING, which
was never a claim about lint, plus 14.5's addition that a type and a checker fence COMPLEMENTARY
holes.  What shipped is `cash_ledger.ReconciledThrough`, a type with no ordering against a civil
day, so a restatement of the rule is a `TypeError` rather than a lint finding.  **Section 14** carries the census, the fenced shapes, the converged sites and the
negative controls.  Measured on a production clone: **0 of 16,536 seam leaves move**, and the
anchor backfill re-derives the ledger the OLD walk wrote without writing anything.

**Three neutral adversarial reviews then ran against it and changed it in four ways** (Section
14.5).  They found a SEVENTH implementation of the rule that this step's own census was blind to --
the modelled contribution feed, deciding with a bare date whether a payroll contribution is already
inside the asserted balance -- now converged, and figure-neutral on the clone.  They found the new
fence test blind to half a symmetric operator: a `__le__`-ONLY mutant passed all six pinned
spellings, so the control is now eleven and fails naming the two that escaped.  They found Section
14.4 citing an instrument that cannot observe its own claim.  And they refuted **three** of this
document's claims about the fence, including its central one -- the type and a checker cover
COMPLEMENTARY holes and neither substitutes for the other, because the bare `observed_on` field a
new module could still compare is exactly what a checker sees and the type does not.  One review
finding did not survive measurement and is recorded as refuted rather than dropped.

Step 4 and step 2's transaction half (`transactions.settled_on`) remain OPEN.  **X-d** now owns the
last duplication step 3 could not remove: two representations of the same events, and with them
`_attribution.py`'s duplicate loaders.

`pylint app/ scripts/` 10.00/10. Full suite **7,728 passed / 0 failed**, under both
`America/New_York` and CI's `TZ=Pacific/Kiritimati` (7,724 at S1-c, 7,687 before it; step 3 adds
four, two of them its reviews' findings). Production-clone verification is Section 7 for the
residue and Section 13.5 for S1-c: **standards 1, 2, 3, 5 and 6 all pass**, standard 3 after the
deploy hooks as designed, and S1-c moves **0 of 15,682 seam leaves**.

**A SECOND adversarial review (Section 9) ran against the residue before it was committed and found
eight items, four of them High.** All are fixed; the largest was structural and two reviewers found
it independently. Three remain open and are listed there, including **N-134**.

**F11 is MEASURED and ANSWERED (Sections 10 and 11, 2026-08-01): R-DH (d) STANDS AS RULED.**
It moves the current period's projected end balance from **-$19.95 to +$514.13** on live data
(today's own balance does not move), and F11's objection to that does not survive audit: the
settle side already carries **five times** the same exposure by explicit ruling, and outside a
single outlier day the alternative's residual is twice as large. Two of this document's own
recommendations were withdrawn on measurement -- see 10.6 and Section 11. **S1-c is UNBLOCKED,
with three things that must ship with it.**

**The developer ruled F1 on 2026-07-31: "Revert + date the opening now."** The EXCEPT clause is
deleted -- an assertion is the closing balance for its civil day, opening and true-up alike -- and
the case the exception protected is answered by the opening's own recorded date instead of by a
placement rule guessing at one. See Section 4.

Written 2026-07-31 after a production defect made the grid's
projected end balance wrong by **-$4,001.42** on the developer's own checking account. Traced,
reproduced against a clone of production at the shipped commit (`69a527cd`, the cash-arc ship), and
the fix prototyped and measured before a line of it was written.

Owned by the balance arc (`README.md`). This is root 4 of that document's "what remains" list --
"nothing in the app records WHEN money moved" -- which the arc had recorded as **noise in the
reconciliation row** and which is in fact a **correctness defect in the projected end balance**. See
ruling **R-DH** and finding **N-130** in `README.md`.

---

## 1. The defect, as it presented

On 2026-07-31 the developer did an ordinary bookkeeping session: read the bank, entered the
checking anchor, then ticked off the bills that had cleared. The grid then reported a projected end
balance of **-$4,021.37** for the current period and a negative balance in every period after it,
against a hand-computed **-$19.95**.

The session, from `budget.account_anchor_history` and `budget.transactions.paid_at`:

| instant (UTC) | event | delta | walked balance |
|---|---|---|---|
| 11:57:10 | Kindle Unlimited settled | -12.80 | 2,864.38 |
| 11:58:11 | Kayla's Spending Money settled | -68.27 | 2,796.11 |
| 11:58:13 | Groceries settled | -265.69 | 2,530.42 |
| 11:58:21 | Gas settled | -93.26 | 2,437.16 |
| 11:58:44 | Data Manager (paycheck) settled | +3,031.97 | 5,469.13 |
| **11:58:53** | **anchor asserted $1,307.66** | **-4,161.47** | **1,307.66** |
| 11:58:59 | CC Payback: Rogue Equipment settled | -1,958.87 | -651.21 |
| 11:59:00 | CC Payback: Mint Mobile settled | -131.60 | -782.81 |
| 11:59:02 | Transfer to Mortgage settled | -1,910.95 | **-2,693.76** |

The three rows recorded in the nine seconds *after* the anchor total **-$4,001.42**. The bank
balance of $1,307.66 already included them; the engine subtracted them a second time.

```
walked balance:                     -2,693.76
remaining projected bills:          -1,327.61
                                    ----------
grid's projected end balance:       -4,021.37     (shipped)
correct:  1307.66 - 1327.61 =          -19.95
```

Reproduced exactly (`-4021.37`) by running `balance_at.grid_balance_view` at the shipped commit
against a clone of production.

**The income, expense and net rows were correct throughout.** They group on the BUDGET clock (the pay
period a row is attributed to), which no part of this defect touches. Only the balance row and the
"Timing & true-ups" remainder were wrong -- and the remainder was wrong *because* it is where the
engine's own double count lands. Of the -$4,588.69 that row displayed, **-$4,161.47 was the plug the
engine booked against itself.**

## 2. Root cause

Three facts, each cited:

1. **`paid_at` is the moment of the click, not the day the money moved.**
   `app/services/status_seam.py:105` stamps `db.func.now()`;
   `app/schemas/validation/transactions.py:62` is `dump_only`, so no API can supply another value.

2. **A cash anchor has no date at all.** `AccountAnchorHistory` (`app/models/account.py:149`)
   carries only `created_at`. There is no "this balance was true as of ___".
   **The loan side already has it**: `anchor_service.apply_loan_anchor_true_up` takes an explicit
   user-supplied `anchor_date`. The cash half is the one that never got it (finding X5).

3. **The fold partitions those two data-entry timestamps at second granularity.**
   `cash_ledger/_events.py:391-398` merges assertions and settles into one stream keyed on the
   instant; `cash_ledger/_walk.py:228-240` applies each assertion as a reset. A settle recorded one
   second after an anchor rides on top of it.

So the question the engine must answer -- *is this settled row already inside the asserted balance?*
-- is answered by comparing two clocks, neither of which measures when money moved. In the
developer's workflow (open the app, true up the anchor, tick off what cleared) the answer is decided
by the order the buttons were pressed.

### 2.1 The same question, answered four different ways

The architectural root is not the granularity. It is that **one question had four
implementations**. Three were found when this document was written; the fourth was found by the
2026-07-31 adversarial review (F4) and is the reason step 3 is not optional polish:

| site | rule as found | rule now |
|---|---|---|
| `cash_ledger/_events.py` (read fold) | `occurred_at` vs `asserted_at`, instant, settle wins ties | `settled_on <= observed_on`, civil day |
| `account_posting_service/_walk.py` (posted ledger) | `sources[i][0] <= fact.asserted_at`, instant | `sources[i][0] <= fact.observed_on`, civil day |
| `account_posting_service/_sync.py` (the self-heal skip) | `utc_day_start_instant(entry_date) <= max(created_at)`, mixed | `min(entry_date) <= max(observed_on)`, civil day |
| `entry_service.py:799` (envelope entries) | `entry_date <= date.today()`, date, **not compared against the anchor at all** | **unchanged -- S1-c, deferred** |

**Three of the four now answer the question the same way, in the same units, against the same two
fields.** The fourth is the survivor and it is the one that never compared against the anchor: it
compares against `today`, which is the SERVER's UTC day, so it contradicts R-DH (b) as well.
Its own docstring states the correct semantic -- *"the owner just looked at their real checking
balance and entered it as the new anchor, so every debit purchase that had already posted is now
reflected in that number"* -- and that rule was right; it was applied to entries and never to
transactions, and the two still run inside one `apply_anchor_true_up` call and disagree.

Step 3 is what makes a FIFTH impossible rather than merely absent. It is now a smaller job than the
review found it: the three converged sites already share `settled_civil_day` and `observed_on`, so
what step 3 adds is the fence, not the convergence.

### 2.2 A second live instance: `is_cleared`

`TransactionEntry.is_cleared` is a **stored flag written as a side effect of the anchor save**
(`entry_service.clear_entries_for_anchor_true_up:795`, a bulk UPDATE). Record the entry then anchor
and it is set; anchor then record the entry and it never is. Live in production on 2026-07-31:

```
 id | txn  | amount | entry_date | is_cleared |     created_at      |   covering anchor
 81 | 2281 | 192.24 | 2026-07-31 |     f      | 2026-07-31 14:41:37 | 2026-07-31 11:58:53
 82 | 2281 | 150.27 | 2026-07-31 |     f      | 2026-07-31 14:42:08 | 2026-07-31 11:58:53
 83 | 2281 |  20.00 | 2026-07-31 |     f      | 2026-07-31 14:42:35 | 2026-07-31 11:58:53
```

Three entries dated today, an anchor exists for today, none reconciled -- because the anchor was
saved at 11:58 and the shopping happened at 14:41. Harmless at that instant (the anchor does not
reflect them either, so the two errors cancel) and self-resolving at the next anchor, but by chance
rather than by design.

The tell that this was known: `entry_service.toggle_cleared:830` is a **manual per-entry override**
whose docstring says it exists "for cases where the auto-clear on anchor true-up is wrong for a
specific purchase". A manual override for a rule that is wrong is a band-aid on the rule.

## 3. Measurements (production clone, 2026-07-31)

Checking account: 139 settled rows, 54 assertions, since 2026-03-26.

**Exposure.** 65 of 139 settled rows (**47%**) were recorded within one hour of an anchor assertion,
carrying **$19,602.13** of gross cash movement -- every one classified by click order. 32 of the 48
anchor-days have rows recorded after that day's last anchor, **$22,357.52** gross.

**Candidate rules, scored by the correction each is forced to plug in at every assertion.** That plug
is the model's own error; a rule that matches reality books small ones.

| rule | gross plug | net plug | worst single | walk ends at |
|---|---|---|---|---|
| R0 instant partition (**the defect**) | $40,554.34 | -$6,998.90 | $4,161.47 | **-$2,693.76** |
| **R1 civil day, assertion closes its day** (un-amended) | **$14,286.82** | **-$940.06** | $2,612.92 | **$1,307.66** |
| R2 civil day, settles win same-day ties | $65,671.21 | -$4,406.95 | $3,624.63 | -$101.81 |
| R3 anchor-only, settled rows ignored (pre-arc) | $41,008.30 | -$1,438.92 | $2,612.14 | $1,307.66 |

R1 wins on every axis and is the only rule under which the walk lands on the balance the bank
actually shows. Its median per-day plug is **$184.55**; today's is **-$160.05** against the shipped
rule's **-$4,161.47**.

> **CORRECTION (adversarial review, 2026-07-31 -- F1, now RULED and applied).** **The R1 row above
> was the rule this document scored, and it was NOT the rule `9c2c3130` shipped.** The opening
> amendment made during the build changed the rule after these figures were taken and they were
> never re-run. Re-measured on the same account, same 139 rows, same 54 assertions -- the plug is
> summed over the 53 TRUE-UPS, excluding the opening's own correction, which is genesis rather than
> a correction of model error:
>
> | rule | gross plug | net plug | worst single | walk ends at |
> |---|---|---|---|---|
> | R1 opening ABSORBS its own day -- **what this table scores, and what now SHIPS** | $15,367.94 | **-$940.06** | $1,853.92 | $1,307.66 |
> | R1 opening OPENS its day (the amendment, reverted) | $17,282.84 | **-$2,997.48** | $1,986.16 | $1,307.66 |
>
> Reproduced independently on 2026-07-31 by running the shipped walk against a pristine clone under
> both variants: the R0, R2 and R3 rows come back to the cent. The un-amended gross is **$15,367.94**,
> not the **$14,286.82** the table above states -- the original figure was never reachable by either
> variant and is the one number in this section that did not reconcile. Section 6's standard 5 is
> restated against the reachable target.
>
> **A structural fact the first measurement missed:** counting the opening's own correction, the net
> plug is IDENTICAL (-$250.90) under both variants. The amendment neither created nor destroyed
> error; it moved **$2,057.42** out of the opening's correction and into the next true-up's. The
> question it decided was not "which rule books less error" but "does the opening state the balance
> the user typed, or a fabricated one" -- and it fabricated $4,804.00 for a day the bank showed
> $2,746.58.

**The historical smoking gun.** On 2026-04-01 the anchor was set to $804.06; a $1,910.95 mortgage
transfer was then marked paid, dropping the walk to -$1,106.89; on 2026-04-02 **the same $804.06** was
entered again and the engine booked a **+$1,910.95** "true-up" to undo its own double count.

**Prototype.** One change to the sort key, run against the production clone at the shipped commit:

| period | shipped | prototype |
|---|---|---|
| 2026-07-30..08-12 (current) | -$4,021.37 | **-$19.95** |
| 2026-08-13..08-26 | -$3,395.19 | $606.23 |
| 2026-08-27..09-09 | -$4,110.62 | -$109.20 |
| 2026-09-10..09-23 | -$3,948.48 | $52.94 |

Every past period end also lands exactly on the balance that was asserted then ($448.77, $1,018.00,
$610.64, $218.58, $1,502.06, $2,037.01, $319.09, $2,877.18). Under the shipped rule the first period
ends at -$397.76, a balance the account never held.

**The remainder becomes readable.** Under the prototype the current period's "Timing & true-ups" is
-$587.27 and decomposes exactly: **-$427.22** of the previous period's bills whose money moved in this
one, plus a **-$160.05** true-up. Under the shipped rule it is -$4,588.69 and decomposes into nothing
a user can act on.

### 3.1 Timezone

Storage does not change: every timestamp stays `timestamptz` in UTC. The question is which calendar
day the engine derives from a stored instant **when it must compare that instant against a plain
`DATE` column**. `pay_periods.start_date`, `pay_periods.end_date` and `transactions.due_date` are
plain `DATE`s that mean the user's civil days; deriving the event's day in UTC and comparing it to
them is comparing two different calendars.

Measured: **22 of 139** settled rows land on a different day; **5 land in a different pay period**
(including a $1,910.95 mortgage payment on both 2026-04-22 and 2026-07-01, and a $178.32 + $8.53 pair
on 2026-06-03); 2 assertions also move period. Two Eastern evenings (**2026-04-22**, **2026-06-03**)
had a single bookkeeping session split across two UTC days -- the exact shape that would defeat this
document's fix.

**The hazard this introduces, and why it dictates the implementation.** Four settled rows carry no
`paid_at` and fall back to `utc_day_start_instant(period.start_date)` -- midnight UTC of a civil
date. Converting *that* to Eastern moves it to the previous day, which is wrong: it was never an
instant. So the fix is **not** "convert instants to Eastern at each read site". It is: **the fact
carries its civil day as a civil day, resolved once at construction, and only genuine instants are
converted.**

That is the same field step 2 fills with a stored, user-supplied column, which is why building it now
is not throwaway work.

## 4. Rulings

Ruled by the developer 2026-07-31 in the session that opened this document. Recorded as **R-DH** in
`README.md`.

**R-DH (a) -- An assertion is the closing balance for its civil day. No exception.**
It absorbs every cash movement dated that day, whatever order the two were recorded in. Multiple
assertions in one day apply in recording order; the last is the day's closing balance.
*Rejected:* the shipped instant partition (measured above: 7x the net plug, and the -$4,021.37 that
opened this document); settles winning same-day ties (worse than either); and the OPENING exception
below.

> **The EXCEPT clause lived for one day and is DELETED** (finding F1 / N-133, developer ruling
> 2026-07-31: *"Revert + date the opening now"*). It said an OPENING sorts BEFORE its own day's
> sources and they ride on top, because an opening states what an account holds as recording BEGINS
> rather than what a day closed at. Three things decided it, all measured:
>
> 1. **The only real data contradicts it.** Checking's opening asserts **$2,746.58** on 2026-03-27
>    and **four settled rows carry that same civil day**, netting **+$2,057.42** (`Data Manager`
>    +2,473.38, `Health Insurance Allowance` +100.00, `Audible` -15.96, `Transfer to Fidelity
>    Savings` -500.00). Every one was clicked **33 seconds to 1.6 hours AFTER** the opening was
>    typed, so the opening was read off a bank that already showed them. Riding them on top makes
>    the walk read **$4,804.00** for a day the bank showed $2,746.58, and makes the next assertion
>    (2026-03-30, $2,653.89) book **-$1,986.16** where absorbing them books **+$71.26**.
> 2. **It cost the rule its one-statement property.** The exception had to be hand-mirrored as a
>    sort position in the read fold and as a date boundary in the posting walk, held in step by
>    convention -- and `dated_deltas`' tie-break never moved with it at all (F5).
> 3. **Where the artifact lands.** Both variants leave exactly ONE wrong region, and they differ in
>    which. Measured on the whole seam, **7 of 15,682 figures move**: with the exception, three days
>    of tracked history (2026-03-27..29) read $4,804.00 and period 0's visible "Timing & true-ups"
>    carries -$1,421.00; without it, the artifact is the pre-tracking back-projection ($689.16 on
>    2026-03-26 and earlier) -- and that figure is R-I doing its job correctly, because $689.16 IS
>    what the account held before that day's four movements. The current period is **-$19.95** and
>    every other period end is identical either way.
>
> **The case the exception protected is now answered by a recorded fact instead of a placement
> rule.** "Open an account at $0 and fund it the same day" was its motivating case, and it has never
> occurred: account 1 is the only account with any settled row on its opening's day. Step 2's
> opening half ships with this revert, so a user who opens an account and funds it later the same
> day dates the opening to the day BEFORE -- which is what actually happened -- rather than relying
> on the engine to guess. That is the ruling's own sequencing: the day partition is the best
> available GUESS while nothing records when money moved, and every place the guess can be replaced
> by a date, it is.

Records on EARLIER days precede every assertion and are what ruling R-I back-projects into the
fold's seed; nothing here touches that arm. The rule is implemented on BOTH walks -- the read fold's
ordering and the posting walk's absorption boundary -- because moving one alone makes the two
disagree (measured during the build: 66 failures against 58 when only the read fold moved).

> **What the revert actually moved, measured on the whole seam.** The two variants were run
> against a pristine `pg_dump` of production through `tests/manual/verify_balance_baseline.py` and
> diffed leaf by leaf: **7 of 15,682 figures move, all on Checking, and nothing else moves at all.**
>
> | figure | exception kept (was shipped) | exception deleted (**ships now**) |
> |---|---|---|
> | current period balance | -$19.95 | **-$19.95** |
> | every other period end | identical | identical |
> | period 0 "Timing & true-ups" | -$1,421.00 | **+$636.42** |
> | daily balance 2026-03-27..29 | $4,804.00 | **$2,746.58** |
> | pre-tracking back-projection (R-I) | $2,746.58 | **$689.16** |
> | R-K's identity | 0 breaks / 60 pairs | 0 breaks / 60 pairs |
>
> Neither variant is artifact-free, and saying so is the honest form of the ruling: each leaves
> exactly one region that is not a balance the account held. The deleted exception put its artifact
> INSIDE tracked history for three days and into a visible grid row; the rule that ships puts it in
> the pre-tracking past, where finding **N-37** already records that the fold's answer before an
> account's first assertion is unruled -- and where $689.16 is in fact correct, being what the
> account held before that day's four movements.
>
> **Cost of the revert, re-measured on the day it was applied** (the review's "35 test re-rulings /
> 41 failures" predated both PR #66 and `dfc36af8`): **30 failures on a 0-failure baseline, and not
> one of them a financial re-ruling.** `tests/test_services/conftest.py` freezes today, and the
> ordinary settle idiom is `paid_at = db.func.now()`, so `seed_user`'s origination landed on the
> very civil day its settles did -- every fixture meaning "an account existed, then money moved"
> silently said "money moved on the opening's own day", and passed only because of the exception.
> That is finding **N-132**'s shape one layer up. The fix is one line per account-creating fixture:
> state the opening's day. Four tests then genuinely re-rule, and they are the controls for the rule
> itself.

*The residual, stated, in BOTH directions:* a movement that genuinely lands **after** the balance was
observed on the same day is absorbed anyway. An OUTFLOW makes the projection read high; an **INFLOW
makes it read LOW by the full amount** -- the shape the deleted opening exception was reaching for,
and the one the $184.55 median does NOT bound, because that median was measured over outflows.
Bounded by the next assertion either way (the developer re-anchors every 2.3 days), self-correcting,
and against the $4,161.47 the instant rule produced on a single day. **It is a guess, and step 2 is what removes it** -- with real
dates on both sides the comparison is between two real-world dates and the same-day case is settled
by the closing-balance semantic rather than by an unknowable ordering.

**R-DH (b) -- The civil day is the user's, not UTC.** `America/New_York` via the existing
`DISPLAY_TIMEZONE`. Storage is unchanged. Shipped in the same commit as (a): both change which day an
event counts on, both require the same posted-ledger resync, and shipping them apart means verifying
the same four months of balances twice.

**R-DH (c) -- The envelope process is unchanged, and made order-independent.** Record an entry per
purchase against the envelope; true up the anchor; either order. The invariant this must satisfy,
worked on the developer's own figures (anchor $1,307.66, Groceries $500 with nothing recorded, other
projected bills $827.61):

```
before:  1307.66 - 500.00 - 827.61  =  -19.95
record a $150.27 purchase and anchor to $1,157.39:
after:   1157.39 - 349.73 - 827.61  =  -19.95
```

**Recording a purchase and truing up the anchor by the same amount must not move the projected end
balance.** It also must not move if only the entry is recorded and no anchor follows. Both become
tests.

> **NOT YET TRUE, and NOT YET TESTED (F2, F11).** Neither invariant exists in `tests/` -- `grep`
> finds these sentences only in this document. And "either order" is still false in the code:
> `entry_service.clear_entries_for_anchor_true_up:787` compares `entry_date <= date.today()`, which
> is (i) the SERVER's UTC day, not the user's civil day, so it contradicts R-DH (b) which *did*
> ship, and (ii) not compared against the anchor at all. Record-then-anchor clears the entry;
> anchor-then-record does not. That is the order-dependence this ruling promises to remove, live in
> production today. It is S1-c's work and S1-c is deferred -- recorded here so the deferral is a
> known gap rather than a silent one.

The two figures the envelope carries are deliberately different and both are already correct:
`build_entry_sums_dict`'s `remaining` (budget minus every entry, ignoring reconciliation) answers
"how much budget is left"; `_entry_aware_amount`'s reservation (budget minus *reconciled* debits)
answers "how much to still hold back from the projection".

**R-DH (d) -- `is_cleared` becomes DERIVED, and the manual toggle is deleted.** An entry is
reconciled iff its date is on or before the latest assertion's observed day -- the same rule as (a),
evaluated at read time. This deletes a stored boolean, a bulk UPDATE, its autoflush coupling inside
`apply_anchor_true_up`, `entry_service.toggle_cleared`, its route, and its UI control. When
reconciliation is wrong the user corrects the entry's **date**, which is the fact that was actually
wrong. *Rejected:* keeping the flag with a better auto-rule (leaves a denormalized copy of a derivable
fact, which is the `Account.current_anchor_*` disease this arc is already removing at step X-e).

> **Measure the entry-side residual BEFORE building this (F11).** Applied to the three entries in
> Section 2.2 -- dated 2026-07-31, against an anchor whose `observed_on` is 2026-07-31 -- this rule
> **reconciles all three**, drops the envelope reservation by **$362.51**, and moves the projected
> end balance from **-$19.95 to +$342.56**, against a bank balance read at 11:58 that does not
> contain the 14:41 shopping. That is R-DH (a)'s accepted residual applied to entries, which is
> consistent -- but it was never measured there. **$362.51 in a single day is roughly twice the
> $184.55 daily median measured for transactions**, because an envelope entry is by definition
> "what I spent today, after I read my balance this morning" -- the residual's worst shape, not its
> average one. Section 2.2 currently calls today's uncleared state "harmless by chance"; under this
> ruling it becomes harmful by design.
>
> R-DH (e) is what closes it, and only if the entry form's default stops being today. **Sequence the
> two together, or ship (d) after step 2.** Do not ship (d) alone.
>
> **MEASURED 2026-08-01 against a fresh production clone, and this note is SUPERSEDED by Section
> 10.6.** The residual is **$534.08** today, not $362.51 (the day's shopping continued after the
> first measurement). But the finding's conclusion -- that this ruling should not ship alone -- does
> not survive the audit: **the settle side already carries `$3,142.61` of the identical exposure
> under R-DH (a)**, and the sequencing this note demands (R-DH (e) and the un-hidden entry date) is
> kept, so the note's remedy survives while its verdict does not.

**R-DH (e) -- A date means the day the money hit the account**, not the day the purchase happened.
They differ by a day or two for a debit card. Defaults to today, user-correctable; the error when the
user does not bother is one day's spend and self-corrects at the next assertion.

**R-DH (f) -- "Timing & true-ups" splits into two named figures**, and the anchor form shows the
difference before it is saved ("your recorded balance is $1,157.39, you entered $1,157.39, difference
$0.00"). *Period timing* should read $0.00 whenever every bill's money moves inside the period it was
budgeted to, so a persistently non-zero value is a diagnostic: either a bill is budgeted to the wrong
period, or dates are being recorded late. *Book vs bank* is the untracked spend and should be small.
Today's are -$427.22 and -$160.05 respectively, summed into one unreadable -$4,588.69.

## 5. The build

### Step 1 -- the civil-day seam (fixes production)

- **S1-a DONE** (`9c2c3130`) `fix(cash-ledger): a settled fact carries the civil day it moved on`
  `CashSourceFact.settled_on` and `CashAnchorFact.observed_on` become real fields, resolved once at
  construction (display-tz civil day of a genuine instant; the NULL-`paid_at` civil-date fallback
  passes through unconverted). `merge_anchor_and_cash_events` partitions on `(civil day, sources
  before assertions)` -- **that function is DELETED at step 3 (Section 14), which moved the same
  partition into each walk's own absorb loop over `ReconciledThrough.covers`**. `dated_deltas` keys off the same fields rather than re-deriving.
- **S1-b DONE** (`9c2c3130`) `fix(posting): the posted ledger partitions on the same day as the fold`
  `account_posting_service/_walk.py:434` and the journal-entry dating move to the same rule, so the
  write side and the read side stay one statement. Shipped as a deploy hook
  (`posting_service.resync_all_cash_postings` + `init_database.resync_all_cash_postings_after_migration`)
  rather than a migration, so the go-forward sync is the only statement of the rule. **The hook is
  load-bearing, not hygiene** -- see Section 7, standard 3.
- **S1-c NEXT** `refactor(entries): reconciliation is derived from the entry's date`
  R-DH (d), which stands as ruled (Section 10.6). Deletes the bulk clear, the toggle
  service/route/UI, and (with the developer's approval and a `Review:` line) the `is_cleared`
  column. **Three things ship with it and are not optional: the un-hidden entry date, R-DH (f)'s
  one-line split, and the pinned test (10.5). It moves the live projected end balance by
  `+$534.08` and that must be stated when it ships.**

Tests: the 6 that pin the instant partition are re-ruled against R-DH, and the test that was missing
is added -- **the projected balance is invariant to the order of assertion and settle within a
session**, plus R-DH (c)'s two envelope invariants.

> **STATUS: the re-rulings happened at the step; the additions did NOT, and are now DONE** (F2).
> `9c2c3130` added **zero** net tests -- `def test_` counts identical in all 8 changed files, 7,675
> collected at both `b73e25bc` and that commit. The residue branch closes it:
> `tests/test_services/test_anchor_settle_partition.py` is new and holds all five owed properties at
> the grain the ruling states them --
>
> - R-DH (c)'s two envelope invariants, at PROJECTED-BALANCE grain;
> - order-independence at PROJECTED-BALANCE grain (the existing control is walk-grain only);
> - `resync_all_cash_postings`: idempotent, and it re-dates a stale entry and COUNTS it.
>
> **Every one is negative-controlled**, which is the lesson F2 exists to record: three separate
> mutants (assertions applying before their day's sources; every debit treated as cleared; the
> resync walking without re-posting) were introduced one at a time, and each was caught by exactly
> the test that should catch it.  The three tests written for the deleted opening exception were
> re-ruled and re-controlled the same way: reintroducing the exception on both walks fails five
> tests in `test_account_posting_service.py`.

### Step 2 -- the app records when money moved

`transactions.settled_on` and `account_anchor_history.observed_on` as stored, user-editable `DATE`
columns, backfilled from step 1's derivation so **no figure moves on the day it ships**. The entry
date is un-hidden on its creation form. The walk reads the columns instead of deriving them; nothing
else in the engine changes. This is the arc's existing plan step **X-f** plus finding **X5**,
promoted from "after X-d" to "now" by R-DH.

- **S2-a DONE** (migration `c4a19e7b2d80`) `feat(anchor): an assertion carries the civil day it was
  true`. The ANCHOR half, pulled forward by the F1 ruling because it is what answers the case the
  deleted opening exception was reaching for. `account_anchor_history.observed_on` is a stored
  `DATE`, backfilled from `(created_at AT TIME ZONE 'America/New_York')::date` -- the derivation it
  replaces, verbatim, so **0 of 15,682 seam figures moved**. `AccountSpec` / `create_account` take
  it and the account-create form offers a "Balance as of" field defaulting to today; a future date
  is refused. The anchor PERIOD is now resolved FROM that day rather than from `today`, so the
  row's two statements of "when" cannot disagree. `cash_anchor_facts` reads the column.
  **F6 shipped with it and had to**: the posting walk's monotonic source pointer assumed
  `observed_on` was non-decreasing in `created_at` order, which a user-supplied column breaks.
  **F12 shipped with it too**: the double-submit index re-keys onto `observed_on`.
- **S2-b OPEN** -- the TRANSACTION half (`transactions.settled_on`) and the true-up form's own date
  field. Until it lands, a settle's day is still derived from `paid_at` (the click) and a true-up's
  `observed_on` still defaults to today, which is exactly what the derivation gave: figure-neutral,
  and the residual below is still a guess on the settle side.

### Step 3 -- one predicate, and the fence is STRUCTURAL

**DONE, 2026-08-01. The checker this step specified was BUILT AS A TYPE INSTEAD, on the
developer's ruling (Section 14).** The original text follows, because the reason the checker was
rejected is a measurement, not a preference.

*As specified:* a single `is_inside_assertion` shared by the read fold, the posting walk and the
entry reconcile, backed by a custom pylint checker on the `shekel-refname-compare` pattern, so a
fourth answer to section 2.1's question cannot be written.

> **This step is now smaller than the review found it, and what remains is the FENCE.** F4's fourth
> answer is converged (the self-heal skip compares days), F5's `dated_deltas` tie-break is correct
> by construction, and the two hand-mirrored statements of the OPENING placement are gone with the
> exception itself. What is left for step 3:
>
> - the ENTRY reconcile (`entry_service.py:799`), the one site that still compares against `today`
>   rather than against an anchor -- that is S1-c's work, and step 3 is what stops it being
>   writable a fifth way;
> - the checker that makes a fifth answer un-writable, which is the part no convergence buys.
> - `ledger_report_service/_attribution.py` restates the day derivation twice (`:445`, `:548`)
>   behind a justification that R-DH (b) has now falsified (F7).

> **The two fences cover DIFFERENT holes and neither is a substitute for the other. Section 14
> first claimed otherwise and an adversarial review refuted it.** A name-vocabulary census of
> `app/` finds five ordering comparisons on the assertion-day vocabulary and is blind to
> `account_posting_service/_sync.py`'s `earliest <= latest`, whose operands are both bare locals --
> the site finding F4 was about. That is a real limit, but it is a limit of the VOCABULARY chosen,
> not of lint: adding the two local names catches it, at the cost of ~9 more exemptions, and one
> astroid hop of intra-function assignment tracking catches it without them.
>
> What the TYPE fences is the derived accessor. What it does NOT fence is the bare field --
> `CashAnchorFact.observed_on` is still a plain `date`, so the exact line this step deleted
> (`x <= fact.observed_on`) compiles today in any new module. **That is the shape that shipped
> implementation #2**, and only a checker sees it. See Section 14.
>
> `_attribution.py`'s duplicate loaders are NOT closed by this step and are re-owned by **X-d**
> (Section 14.6), which deletes their twin rather than extracting a third shared home.

### Step 4 -- the remainder is named

R-DH (f): the row splits, and the anchor form previews its own difference.

## 6. Verification standard

Every step is verified against the production clone, not only the suite:

1. The current period reads **-$19.95**, and every past period end equals the balance asserted then.
2. `balance[p] - balance[p-1] == net[p] + reconciliation[p] + contribution[p] + accrual[p]` holds on
   every (account, period) pair, as it does today.
3. The read fold and the posted ledger agree per account per date, as they do today.
4. Order-independence: for the 2026-07-31 session, permuting the anchor against the three settles
   produces one answer.
5. The gross plug over Checking's 53 true-ups drops from $40,554.34 to **$15,367.94** (net
   -$6,998.90 to **-$940.06**). **Restated after F1**: the $14,286.82 the first measurement quoted
   was never reachable by any variant; $15,367.94 is the reachable target and the shipped rule hits
   it exactly.
6. **Added by the review:** nothing outside Checking moves except what a ruling says moves. The
   whole-seam baseline (`tests/manual/verify_balance_baseline.py`) is captured before and after
   against a pristine production clone, and every moved cent is explained.

## 7. Verification results (production clone)

Two runs, both against read-only restores of a production `pg_dump` on `shekel-dev-db` (never
written back). **7.0 is the residue branch's run and supersedes 7.1's numbers where they differ**;
the earlier run is kept because it is what found F1.

### 7.0 Residue branch (`fix/n133-review-residue`, 2026-07-31)

Code with the F1 revert, the stored `observed_on`, F4, F5, F6, F7, F8, F9 and F12 applied.

| # | standard | result |
|---|---|---|
| 1 | current period -$19.95; past ends equal a balance asserted then | **PASS** -- -$19.95, and all 9 past ends land on an asserted balance |
| 2 | `balance[p] - balance[p-1] == net + reconciliation (+ modelled)` | **PASS** -- 0 breaks over 420 period pairs, all 8 non-loan accounts |
| 3 | read fold == posted ledger, per account per date | **PASS after the deploy hooks** -- 0 breaks over 84 dates; **37 of 56 breaks on Checking BEFORE them**, which is F8's measurement reproduced |
| 4 | order-independence for a bookkeeping session | **PASS**, and now at PROJECTED-BALANCE grain in `tests/test_services/test_anchor_settle_partition.py` |
| 5 | the plug drops | **PASS** -- gross $40,554.34 -> **$15,367.94**, net -$6,998.90 -> **-$940.06**, worst $4,161.47 -> **$1,853.92** over 53 true-ups |
| 6 | nothing moves except what a ruling says moves | **PASS** -- against the un-amended reference captured before any code changed, **0 of 15,682 leaves moved**: the revert lands exactly on the measured variant, and the stored column plus F4/F6/F7/F8/F9 move nothing at all |

Also verified in this run:

- **The deploy resync is idempotent and its count is now meaningful.** First pass on a pristine
  clone: `RE-POSTED 16 transaction(s) and 1 transfer(s)`. Second pass: `already at target (0
  changed)`. Under the old "counts walked" line both passes would have printed 999 (F8).
- **The trial balance closes**: `SUM(account_postings.amount) = 0.00` after every hook, and a second
  full pass of all three hooks writes nothing (639 postings / 316 entries before and after).
- **The migration runs both directions** on the dev database and on two production clones, with the
  backfill exact: 0 rows where `observed_on <> (created_at AT TIME ZONE 'America/New_York')::date`
  over 67 rows.
- **`pylint app/ scripts/` 10.00/10 with zero messages; full suite 7,687 passed / 0 failed** (7,677 at the merge base).

### 7.1 Adversarial review run (2026-07-31)

Run against a **fresh** `pg_dump` of production (read-only; restored into `shekel_audit` /
`shekel_audit_pre`, never written back). Code at `9c2c3130`.

| # | standard | result |
|---|---|---|
| 1 | current period -$19.95; past ends equal the balance asserted then | **PASS** -- -$19.95, and all NINE past ends land exactly on an asserted balance: $448.77 / $1,018.00 / $610.64 / $1,100.99 / $31.73 / $1,502.06 / $126.06 / $319.09 / $2,877.18 |
| 2 | `balance[p] - balance[p-1] == net + reconciliation (+ modelled)` | **PASS** -- 0 breaks over 60 period pairs |
| 3 | read fold == posted ledger, per account per date | **PASS after the deploy hooks** -- 0 breaks over 7 non-loan accounts; **36 of 56 breaks on Checking BEFORE them** (final $1,307.66 vs -$2,693.76). See F8 |
| 4 | order-independence for the 2026-07-31 session | **PASS at walk grain**; no test at projected-balance grain (F2) |
| 5 | gross plug drops | **PARTIAL** -- $40,554.34 -> $17,282.84 as shipped, $15,367.94 un-amended (F1) |
| 6 | nothing else moves | **PASS** -- 924 of 15,682 captured figures moved, all on Checking, plus **one** loan figure: Mortgage period 7's map, $177,554.69 -> $177,277.97 (-$276.72), which is the single documented payment whose visible day moves from 2026-07-02 UTC to 2026-07-01 Eastern |

Also verified, not previously stated as standards:

- **The deploy resync is idempotent.** A second full pass of all three hooks writes nothing
  (315 journal entries before, 315 after).
- **The trial balance closes throughout.** `SUM(account_postings.amount) = 0.00` before, between and
  after every hook.
- **The resync changes no rendered figure.** Baseline captured against the resynced clone is
  byte-identical to the baseline captured against the un-resynced one (0 of 15,682 leaves moved), so
  the hook is ledger hygiene for the ledger-authoritative surfaces, not a projection change.
- **`pylint app/` 10.00/10** reproduced.
- **The suite is 6 failed / 7,669 passed**, identical at `b73e25bc` (F3).

### 7.1 The two test-instrument findings the build produced (N-131, N-132)

Both are recorded in `README.md`'s ledger; the detail is here.

**N-131 -- the cross-page locks are a month-end TIME BOMB.**
`tests/test_integration/test_cross_page_balance_equality.py` fails 6 tests on the last days of a
month, at the unmodified shipping commit, independent of this work. Confirmed by the review: the
same 6 fail with the same 7,669 passing at the merge base `b73e25bc`, so it is neither caused nor
cured by S1-a / S1-b. It fires roughly 12 days a year and would block any hotfix PR's merge gate.
**CLOSED by branch `fix/cross-page-month-end-clock` (`92879e86`, off `origin/main`)**, which carries
no application change and therefore warrants its own PR ahead of this one. Until that merges, any
claim of "0 failed" on this branch is false (F3).

**N-132 -- fixtures separated their events by HOURS against a partition that now reads civil days.**
Two shapes, both fixed in `9c2c3130`:

- fixtures that placed a settle an *hour* before or after an assertion to mean "before" / "after"
  collapse onto one civil day and stop discriminating the case they name. Converted to day offsets,
  with the reason recorded at each site.
- four fixtures built **midnight-UTC** instants to MEAN a civil day
  (`_test_helpers.override_anchor`, `conftest._pin_opening_to`). Midnight UTC is the previous
  EVENING in Eastern, so under R-DH (b) they filed a true-up in the PREVIOUS pay period and emptied
  the anchor column's remainder of the very assertion the fixture had just made. Both now build the
  instant in `DISPLAY_TIMEZONE` and convert to UTC for storage.

**The review found the conversion incomplete** in exactly the place it mattered most: the two tests
written for the OPENING amendment still use a *day* offset where their docstrings claim an *hour*,
which makes them unable to fail. That is F2, and it is the mirror image of N-132 -- the same
fixture-grain mistake, pointed the other way.

## 8. Adversarial review findings (2026-07-31)

Twelve items. **Ten are CLOSED on `fix/n133-review-residue`** (F1 by ruling, F2 F4 F5 F6 F7 F8 F9
F10 F12 by the work); **F3 is obsolete** (its branch merged); **F11 remains OPEN** and gates S1-c.
Each finding below carries its own closure note.
Nothing here says the fix is wrong -- the current-period figure is -$19.95 under every variant
considered, and standards 1, 2, 3 and 6 all pass. Method: a fresh read-only `pg_dump` of production
restored into two throwaway databases on `shekel-dev-db`, the seam walked directly, the whole-seam
baseline captured at `b73e25bc` and at `9c2c3130` and diffed, and the suite plus `pylint` re-run at
both commits. Production itself was never written to.

### Blocking a ruling

- **F1 (High) -- CLOSED by developer ruling 2026-07-31: "Revert + date the opening now".** The
  EXCEPT clause is deleted from R-DH (a) and from both walks, and step 2's opening half ships with
  it so the case the exception reached for is answered by a recorded date. Verified: 0 of 15,682
  seam leaves differ from the un-amended variant measured before any code changed. Detail at
  R-DH (a). The original finding, for the record:
  Full detail inline at R-DH (a) and in the Section 3 correction. Net plug -$2,997.48 shipped vs
  -$940.06 un-amended; the -$940.06 that this document and `README.md`'s R-DH entry both advertise
  is the un-amended rule. Concretely: Checking's opening day carries +$2,057.42 of settles clicked
  33 seconds to 1.6 hours after the opening was typed, and stacking them on top makes the engine
  read $4,804.00 for a day the bank showed $2,746.58. Bounded to March history and period 0's
  remainder. **Decide: revert (measured-best, 35 test re-rulings) or keep and correct both
  documents.**

### Test integrity

- **F2 (High) -- CLOSED.** The blind tests were repaired first (note below), and the three owed
  tests now exist in `tests/test_services/test_anchor_settle_partition.py`, each negative-controlled
  by a distinct mutant. The original finding:
  `test_account_posting_service.py:228` `test_a_settle_on_the_openings_own_day_rides_on_top` says
  "an hour after ... the same civil day" but passes `origin + timedelta(days=1)`, a DIFFERENT day,
  which rides on top under both rules. Proven by reverting the amendment in both walks: that test
  and its sibling `test_a_settle_on_an_earlier_day_is_inside_the_opening` both still passed.
  The amendment's only real pin is `test_same_instant_settle_is_absorbed` (`:361`) -- a test named
  and documented entirely for the DELETED instant rule, so fixing its stale name could silently
  delete the coverage. Also missing: R-DH (c)'s two envelope invariants; any test for
  `resync_all_cash_postings`. See the Section 5 status note.

  > **THE BLIND TESTS ARE FIXED (2026-07-31).** The root cause of the day offset was not
  > carelessness: `_origin_instant` returns the row's `created_at`, which is the ambient WALL CLOCK,
  > so `origin +/- an hour` really does cross midnight on a suite run near 23:30 or 00:30 Eastern --
  > a smaller offset would have been flaky rather than blind. The fix is therefore a **pinned
  > opening** (`_pin_opening` over the shared `restamp_opening_assertion`, the idiom
  > `test_cash_walk.py` already uses) at 12:00 EDT, so plus-or-minus an hour is PROVABLY the same
  > civil day -- and each test now asserts that precondition rather than assuming it.
  >
  > - `test_a_settle_on_the_openings_own_day_rides_on_top` is parametrized **both directions**
  >   (`recorded_after` / `recorded_before`), which is the order-independence pair the single
  >   direction was missing, at the assertion most exposed to R-DH's residual.
  > - `test_same_instant_settle_is_absorbed` is renamed
  >   `test_a_trueup_absorbs_a_settle_the_opening_rode_on_top_of` and re-documented: one civil day,
  >   both assertion kinds, opposite answers. It is the discriminating control that BOTH halves of
  >   the rule can break.
  > - The instant-partition vocabulary that made the blind test read as correct is gone from the
  >   class docstring, the section header and `_settle_expense`.
  >
  > **Negative-controlled, which is the whole point:** with the amendment reverted on both walks all
  > three now FAIL (`-200.00` and `-75.00` against the expected `0.00`); before the repair the
  > opening test PASSED under the reverted rule. Full suite 7,677 passed / 0 failed, `pylint app/`
  > 10.00/10, `app/` byte-unchanged.
  >
  > **Still owed from this finding:** R-DH (c)'s two envelope invariants, a test for
  > `resync_all_cash_postings`, and the order-independence test at PROJECTED-BALANCE grain.
- **F3 (High) -- OBSOLETE.** `fix/cross-page-month-end-clock` merged at PR #66, and the baseline is
  now 7,677 passed / 0 failed. The original finding: Actual: 6 failed /
  7,669 passed (7,675 collected, not 7,676). The 6 are `test_cross_page_balance_equality`, the known
  month-end bomb; **identical 6 failures and identical 7,669 passed at the merge base `b73e25bc`**,
  so nothing regressed. Fixed on branch `fix/cross-page-month-end-clock`. The honest claim is
  "6 failed, all pre-existing and unrelated", never "0 failed" (CLAUDE.md rule 4).

### DRY and latent correctness

- **F4 (Medium) -- CLOSED.** `self_heal_anchor_corrections` compares `min(entry_date)` against
  `max(observed_on)`: civil days on both sides, the rule both walks already apply. The zone-sign
  dependency is gone rather than documented, and `utc_day_start_instant` -- whose only remaining
  purpose was to manufacture the instant this comparison needed -- was DELETED, having no callers
  left. The original finding:
  `account_posting_service/_sync.py:304-311` compares `min(utc_day_start_instant(entry.entry_date))`
  against `max(created_at)`. It is sound ONLY because `America/New_York` is west of UTC (midnight
  UTC of a display day always precedes that day's start in UTC), so it can only over-fire, which is
  an idempotent no-op walk. **For a display zone east of UTC it silently UNDER-fires** and leaves a
  stale anchor correction posted with no error. Nothing states or gates that. Root fix, one line and
  it pre-pays step 3: compare days -- `min(e.entry_date) <= to_display_date(latest)`, the rule both
  walks already use.
- **F5 (Medium) -- CLOSED by construction.** With the opening exception gone the walk has ONE
  placement for both assertion kinds, so `dated_deltas`' source-before-assertion tag matches it for
  every anchor and the Returns docstring's chronology claim is true again. The comment now records
  why it was ever false. The original finding: `cash_ledger/_walk.py:327-336` tags source 0 / assertion 1 with the
  comment "the same tie-break the walk applies", but `_events.py:111-113` puts `_OPENING_ORDER = 0`
  BEFORE `_SOURCE_ORDER = 1`. For an OPENING the two orders are opposite. Arithmetically inert today
  (its one consumer `_cash_fold._actual_steps` day-sums and `sample_cumulative` reads day
  boundaries), but the Returns docstring's "reading the list shows the same chronology the walk
  applied" is false in exactly the place a reader debugging an opening-day discrepancy would look,
  and any future sequential consumer replays the opening wrongly.
- **F6 (Medium) -- CLOSED, and it had to be: step 2's opening half shipped in the same pass, which
  is precisely what would have armed it.** `walk_account_ledger` now sorts its facts by
  `(observed_on, asserted_at)` -- the read fold's order, stated the same way -- before advancing the
  monotonic source pointer. The original finding: `walk_account_ledger` iterates
  `cash_anchor_facts` in `(created_at, id)` order with a MONOTONIC `source_index` pointer, while the
  read fold explicitly re-sorts by `(observed_on, asserted_at)`. The two agree today only because
  `observed_on = to_display_date(created_at)` is monotone in `created_at`. **The moment step 2 makes
  `observed_on` a user-supplied column**, a user correcting an anchor's date backwards inverts the
  order, the pointer skips sources, and the two walks disagree -- the exact drift Phase X exists to
  prevent. Nothing asserts the invariant. Cheapest fix now: sort `facts` by
  `(observed_on, asserted_at)` in the posting walk too, so both sides state the order once and the
  same way.

### Documentation that still teaches the deleted rule

- **F7 (Medium) -- CLOSED, and wider than the finding scoped it.** All eight named sites are
  corrected, plus six the review did not list (`dates.py`'s `to_display_civil_date`,
  `_asset_fold` x3, `_asset_contributions` x2) and the test-side citations. `utc_instant`'s
  argument FOR the instant partition is replaced by the measurement that refuted it;
  `_attribution.py`'s "deliberately different" paragraph now states the narrower boundary stance
  that actually survives. The original finding:
  The commit's stated reason for deleting `utc_civil_date` / `to_utc_civil_date` was "leaving a
  UTC-civil-date helper in the tree is how the old rule gets reintroduced". The survivor carries the
  old rule's ARGUMENT:
  - `app/utils/dates.py:137,154-155` -- `utc_instant` cites both deleted functions AND argues FOR
    the instant partition using the exact 2026-07-25 / $108.15 case R-DH rejected. **Fix this one
    first.**
  - `app/utils/dates.py:176,182` -- cites deleted `to_utc_civil_date` and
    `cash_ledger.attribution_instant`.
  - `app/services/_posting_write.py:5-6` -- module docstring names `_utc_civil_date`, deleted in
    this same commit.
  - `posting_service.py:302,308,320` and `:364,379` -- still "The UTC civil date of ..." and
    "Mirrors the Commit-3 backfill's `COALESCE((paid_at AT TIME ZONE 'UTC')::date, ...)`". Both now
    return display-tz via `_civil_settle_date`.
  - `account_posting_service/_walk.py:1` -- "the **moment-granular** correction producer",
    contradicted by its own line 13.
  - `balance_at/_cash_fold.py:856` -- "its attribution instant".
  - `calendar_service.py:790` -- "`paid_at`'s UTC civil day".
  - **`ledger_report_service/_attribution.py:38-43` is worse than stale.** It says "*Instant vs
    civil date.* The write-side walk partitions by UTC INSTANT ... The two are **deliberately
    different**." Both sides are now the same display-tz civil day. That paragraph is the stated
    justification for keeping an independent restatement of the attribution rule in that package
    (`to_display_civil_date` copied at `:445` and `:548`); the justification has evaporated and the
    commit renamed the cross-references while leaving it. It is now a plain DRY violation with a
    false defence attached. Fold into step 3.
  - Test-side: `test_account_posting_service.py:165,175,180` ("the walk attributes by instant",
    "the moment partition", "partitions sources by instant"); `test_cash_walk.py:228,252` (cite
    `sources[i][0] <= fact.asserted_at`).

### Operations

- **F8 (Medium) -- CLOSED.** `resync_all_cash_postings` returns sources CHANGED (both sync
  functions already returned their emitted entries, so no extra query), the deploy line prints
  `already at target (0 changed)` in steady state and names the re-date when it happens, and the
  one-way risk is stated in both the service docstring and the deploy log. Measured on a pristine
  clone: `RE-POSTED 16 transaction(s) and 1 transfer(s)`, then 0. The original finding: Measured at HEAD
  against a PRISTINE production clone: the fold and the posted ledger disagree on **36 of 56 dates**
  on Checking (final $1,307.66 vs -$2,693.76); after the three hooks, 0 of 56. So
  `resync_all_cash_postings_after_migration` is mandatory. `entrypoint.sh` runs
  `set -eEuo pipefail` and calls `python scripts/init_database.py` at `:259`, so a failure aborts
  the container and the auto-rollback fires -- that part is right. Two gaps:
  - the hook returns counts **walked**, not counts **changed**, so the deploy log cannot tell an
    operator whether the one-time re-date actually happened, which is the one fact worth logging for
    this hook;
  - the re-date is one-way and undocumented as such. If the healthcheck fails AFTER the resync
    commits, the rolled-back image reads a display-dated ledger with UTC rules. Bounded on this data
    (1 payment, 1 day) but it should be a stated risk, not a discovered one.
- **F9 (Low) -- CLOSED.** `joinedload(Transaction.pay_period)` beside the existing eager `entries`,
  and the same on the transfer pass. The original finding: `posting_service.py:816-826` eager-loads
  `Transaction.entries` but not `pay_period`, while `_transaction_entry_date` and `_settled_target`
  (`txn.pay_period.user_id`) both dereference it, and `Transaction.pay_period` is a plain lazy
  relationship (`app/models/transaction.py:205`). 122 extra SELECTs on this dataset, plus one anchor
  walk per emitting source via `_self_heal_account_anchor_corrections`. Same function whose
  docstring makes a point of the eager `entries` load.

### Document accuracy (fixed in this pass)

- **F10 (Low) -- three inaccuracies in this document, all corrected in this pass.**
  - the status block: "COMPLETE and green on `dev` (uncommitted)" -- it is committed as `9c2c3130`
    on `fix/anchor-settle-partition`, not on `dev`. "verified end to end (Section 7)" -- there was
    no Section 7; the document ended at Section 6. "7,676 passed / 0 failed" -- see F3. Section 7
    now exists and holds the results the citation promised.
  - Section 3.1's **"22 of 139"** did not reconcile. Re-measured: **4** settled rows with NULL
    `paid_at`, **18** whose UTC day differs from their Eastern day, **5** that move pay period. The
    period count matches exactly; the day count does not, and the two methods were not reconciled.
    Not load-bearing (nothing in the fix turns on the count), but settle it before quoting it again.
  - Section 3's list of past period ends quotes **$218.58** and **$2,037.01**, which are the FIRST
    anchor of a two-anchor day. Both those days close on a later assertion (**$31.73** on
    2026-06-03, **$126.06** on 2026-07-01), so the list contradicts R-DH (a)'s own "the last is the
    day's closing balance". The engine is right and the list was wrong; Section 7 carries the
    measured nine.

### Forward design

- **F11 (Medium) -- MEASURED AND CLOSED 2026-08-01: Sections 10 and 11. R-DH (d) stands as ruled.**
  It moves the current period from **-$19.95 to +$514.13** (838 of 15,682 seam leaves, all on
  Checking, from the current period forward; today's own balance unchanged), and that is real. What
  does not survive is the conclusion drawn from it: the settle side already carries `$3,142.61` of
  the identical "recorded hours after the assertion" exposure against the entry side's `$623.70`,
  accepted by explicit ruling; outside a single outlier day the alternative's residual is twice as
  large (`$177.43` against `$89.62`); and the evidence that the ruling could never win was an
  artifact of a hidden form field. **S1-c is unblocked, with the un-hidden entry date, R-DH (f)'s
  one-line split, and a pinned test shipping with it.** The original finding: R-DH (d) as ruled
  would make today's production figure worse. Measure the entry-side residual before building S1-c.
- **F12 (Low) -- CLOSED with step 2's opening half, as the finding predicted.** The index now keys
  `(account_id, pay_period_id, anchor_balance, observed_on)`. Approved as a destructive
  drop-and-recreate by the developer 2026-07-31; migration `c4a19e7b2d80` carries the `Review:`
  line and reverses cleanly. `AnchorPoint.as_of_date` -- a UTC-day field whose own docstring cited
  this index as its justification, and which no production code read -- was deleted in the same
  pass. The original finding:
  `app/models/account.py:190-195` keys on `((created_at AT TIME ZONE 'UTC')::date)`. Two assertions
  of the same balance in the same period on two different Eastern days that share a UTC day (23:00
  EDT one evening, 01:00 EDT the next) are now rejected as a same-day duplicate, although R-DH calls
  them two different days' closing balances. The guard's day and the semantic day no longer mean the
  same thing. Narrow, and it fails as a rejected save (`DUPLICATE_SAME_DAY`) rather than a wrong
  number, so it is a step-2 cleanup item: when `observed_on` becomes a stored column the index
  should key on it.

## 9. Second adversarial review (2026-08-01) -- the residue's own review

Three neutral reviewers ran against the residue branch before it was committed:
the project's `code-reviewer` (standards, financial correctness, IDOR,
migration), a test-integrity auditor (mutation-testing every new and re-ruled
test), and a design reviewer (attacking the ruling and the schema). **Two of the
three independently found the same root defect, and one proved it live.**

### R1 (High, FIXED) -- `is_opening` was derived in RECORDING order while every consumer reads BUSINESS-DATE order

`cash_anchor_facts` loaded `ORDER BY (created_at, id)` and set
`is_opening=(index == 0)`, while the partition, the posting walk, ruling R-I's
seed (`anchor_corrections[0]`) and the period view's assertion component (`[1:]`)
all read business-date order. The two agreed for free while `observed_on` was
DERIVED from `created_at`; **step 2's stored column broke that**, and the review
caught it firing in this branch's own new test: an origination observed
2026-03-19 with a microsecond-later `created_at` than a true-up pinned to exactly
noon inverted the two, and a `$1,307.66` TRUE-UP posted to the ledger tagged
`account_opening`.

Fixed at the loader: `cash_anchor_facts` now orders `(observed_on, created_at,
id)` and sets the flag on THAT list, `resolve_anchor` takes the same key
descending so "latest" means the row the walk replays last, and the two
downstream re-sorts (the posting walk's F6 sort, the read fold's merge sort) are
DELETED as restatements. One ordering, stated where the rows are read.

### R2 (High, FIXED) -- the period and the assertion's day came off two different clocks

`resolve_anchor_period_id`'s docstring claims the two "cannot disagree", and that
was true only on the create path. Both true-up writers picked the period with
`get_current_period(user_id)` (`date.today()`, the PROCESS day) while
`stage_anchor_true_up` dated the row `display_today()`. In any process not pinned
to the display zone -- CI, a script, the migration host -- a 21:00 ET true-up on a
period's last day files the row in the NEXT period while dating it in this one,
and the grid (which buckets by `observed_on`) then disagrees with the ledger
(which stamps `pay_period_id`) by the whole correction. Both route sites now pass
`as_of=display_today()`; registration's bootstrap period and its origination
assertion now come off one clock too.

**A related documentation defect, three sites:** comments asserting that
`date.today()` is "the SERVER's UTC day" are FALSE for the deployed container,
which pins `TZ: America/New_York` precisely to fix that class of bug (parity
finding M01). Using `display_today()` is still right -- it is zone-explicit and
does not depend on an env var -- but the stated reason was wrong and is corrected.

### R3 (High, FIXED) -- `observed_on` had no lower bound

The field validated only "not in the future". It opens the modelled-return
accrual window (`_asset_fold._AccrualWindow.days()` materialises EVERY calendar
day from it to the horizon) and the contribution model's first period, and
`fields.Date()` accepts `0001-01-01`. Measured by the reviewer: 740,560 days
enumerated, 234 ms in `sorted()` alone before any accrual math, on every
dashboard render. The correctness half is worse -- back-dating an investment
account fabricates a payroll contribution into every past period.

Bounded by `account_service.earliest_observable_day`: `min(earliest pay period
start, today)`. Taking the EARLIER of the two is load-bearing -- a user whose
schedule is entirely in the future must still be able to assert what they hold
now. The form's `min`/`max` read the same helper, so the browser refuses what the
service refuses.

### R4 (High, FIXED) -- the case the ruling relies on this field to answer was not reachable through the UI

R-DH (a)'s justification for deleting the opening exception is that the opening's
own DATE answers the "$0 account funded the same day" case. The reviewer walked
the journey: the field defaults to today and the help text said *"Leave it as
today unless you are entering an account you already had"* -- so a user opening a
brand-new account is told to leave the default, and the funding is then absorbed.
Traced: the account reads **$0.00 while holding $500.00**.

The rule is right and the copy was wrong. The help text now states the
closing-balance semantic and names the funding case explicitly. **The residual's
direction is also corrected here**: this document said an absorbed same-day
movement makes the projection read HIGH, which is the outflow case only; an
INFLOW makes it read LOW by the full amount, and the `$184.55` median was
measured on outflows.

### R5 (Medium, FIXED) -- two "as of" surfaces dated the anchor from the keystroke

`AnchorPoint` carried no business date after `as_of_date` was deleted, so the
account-detail caption and the investment hero both rendered
`to_display_date(created_at)`. On a back-dated opening they say "anchored Jul 31"
while the engine treats the balance as Jan 1's closing balance -- and on an
investment account the "growth since" caption then contradicts the figure beside
it, whose accrual window opens on `observed_on`. `AnchorPoint.observed_on` added;
both surfaces repointed.

### R6 (Medium, FIXED) -- a seventh statement of "which civil day", missed by F7's sweep

`dashboard_pulse_service._utc_day` truncated the anchor instant to a **UTC** day
for the staleness count while the caption beside it used `to_display_date`. One
instant, two derivations, compared against a display-tz "today": the staleness
count was off by one for four hours every evening. Now one derivation, in the
user's zone. Recorded but not done: staleness should measure from `observed_on`,
not from the recording instant at all -- that needs
`dashboard_service._get_last_anchor_date`'s contract to change, which has callers
beyond this module.

### R7 (Medium, FIXED) -- the migration's two refusals

The index re-key had no duplicate pre-flight, although the migration that CREATED
that index (`e8b14f3a7c22`) does exactly one. And `downgrade` both destroyed
user-supplied data silently and could fail on data the new rule legitimately
admits. Both fixed and both verified by injecting the offending row: `upgrade`
refuses with the colliding tuples named, `downgrade` refuses with the hand-dated
rows named and the operator's recovery path spelled out.

### R8 (High, FIXED) -- two existing tests silently stopped testing what they name

The test auditor proved that `create_account_of_type`'s new "open the day before
today" default, combined with `_origin_instant` reading `created_at`, put the two
exactly one day apart -- so `origin - timedelta(days=1)` landed on the opening's
OWN day and `test_a_settle_on_an_earlier_day_is_inside_the_opening` became a
duplicate of the own-day test. `test_transfer_attribution_uses_income_shadow_day`
had the same defect. **This is N-133 / F2's shape recreated by the fix for it,
from a different cause.**

Root fix: `_origin_day` returns the opening's `observed_on` -- the day the
partition actually reads -- and every offset is built from it through
`settle_instant_on`. Verified with a mutant that absorbs only the assertion's own
day: both tests now FAIL under it, where before only two tests that do not name
the boundary covered it.

### R9 -- everything the reviewers confirmed clean

Worth recording, because a review that only lists defects reads as if nothing was
checked: no `float` anywhere in the diff; `Decimal` discipline intact; ownership
and IDOR unchanged and correct on both changed routes; the `update_account`
refactor behaviour-preserving field by field; the backfill exactly equal to the
derivation it replaces; a single Alembic head; the table already in
`AUDITED_TABLES`; transfer invariants untouched; `resync_all_cash_postings`'
changed-count accurate and its two eager loads many-to-one; F4's rewrite sound
with the zone dependency genuinely removed; F5 closed by construction; and **all
five new tests plus the four re-ruled ones VERIFIED-CAN-FAIL** by mutation, each
caught by exactly the test that should catch it.

### Still open from this review

- **N-134 -- `update_account` moves the anchor balance with NO history row when
  no period contains today.** Behaviour preserved verbatim through the
  `stage_anchor_true_up` refactor and flagged in the code, but it breaks E-19's
  "a matching `AccountAnchorHistory` row from the moment it exists": the cash
  walk then replays a history that disagrees with `current_anchor_balance`, which
  is exactly the divergence `cash_ledger._facts` logs. Not changed under an
  unrelated ruling.
- **`observed_on` is write-once.** It is offered only on create; no route writes
  it afterwards, and `stage_anchor_true_up` hard-codes `display_today()`. A user
  who gets it wrong has no UI to fix it until the next true-up resets the balance
  anyway. S2-b (the true-up form's date field) is what closes this.
- **The loan/cash index asymmetry expires at S2-b** and is noted in the model.
- **`pay_period_id` on the history row.** The reviewers split on whether it is now
  redundant. Against: the schedule is mutable, `journal_entries.pay_period_id` is
  NOT NULL, and a day in a period gap still needs a home. For: it is a
  denormalized copy of a derivable fact. The honest statement is that the row
  carries three "when"s, the third is derived from the second on the create path
  only, and nothing detects a disagreement.

### Suggested order

**Done, in this order, on `fix/n133-review-residue`:** F1's ruling -> S2-a (the stored `observed_on`,
because `create_account` had to be able to date the opening BEFORE it posts the correction, or every
fixture re-stamp leaves a reverse-and-repost pair in the seeded ledger) -> the F1 revert and its
fixture work -> F4 + F6 -> F7 -> F8 + F9 -> F2's three owed tests.

**What remains:** **F11 is MEASURED and CLOSED (Sections 10 and 11) -- R-DH (d) stands as ruled and
S1-c is unblocked**, shipping with the un-hidden entry date, R-DH (f)'s one-line split and a pinned
test; then step 3's fence; then S2-b, the transaction half of step 2.

The review's original order, for the record:

1. **F1** -- the ruling, because everything below is cheaper once the rule is settled.
2. ~~**F2** -- fix the two blind tests, rename `test_same_instant_settle_is_absorbed` for the rule
   it actually pins.~~ **DONE 2026-07-31** (see the F2 note above; the fix was a PINNED opening, not
   a smaller offset). Still owed: the three missing tests -- R-DH (c)'s two envelope invariants, a
   test for `resync_all_cash_postings`, and order-independence at projected-balance grain.
3. **F4** and **F6** -- two small changes that remove a hidden zone dependency and a step-2 landmine,
   and pre-pay step 3.
4. **F7** -- clear the stale docstrings, `utc_instant` and `_attribution.py` first, since those two
   actively argue for the deleted rule.
5. **F8**, **F9** -- deploy log and the N+1.
6. **F11** before S1-c; **F12** with step 2.

## 10. F11 measured: the entry-side residual, and why it is a fork rather than a number

F11 asked for one measurement before S1-c is built. It was taken on 2026-08-01 and it produced a
result the finding did not anticipate: **the ruling as written cannot be satisfied.** Two cases
R-DH has to serve are identical in every fact the app records, and they need opposite answers.

**Method.** A fresh read-only `pg_dump` of production (2026-07-31 23:00 ET) restored into
`shekel_f11_base` on `shekel-dev-db` and upgraded to the residue head `c4a19e7b2d80`; two
throwaway copies with `is_cleared` overwritten by each candidate rule; the whole-seam baseline
(`tests/manual/verify_balance_baseline.py`, 15,682 figures) captured on each and diffed leaf by
leaf. Production was never written to.

### 10.1 What R-DH (d) as ruled costs, live

**12 of 82 entries change, every one of them stored FALSE -> derived TRUE.** Five sit on PROJECTED
parents, so five move money; the other seven sit on parents that had already settled and are inert
(the entry formula only prices projected rows). **838 of 15,682 seam leaves move, all on Checking:
837 by `+$534.08` and ONE by `-$534.08`** -- the current period's `expense` column, which falls by
the same amount the balance rises, because the reservation IS the projected expense. No figure
before the current period moves, which is the blast radius the reservation's Projected-only filter
predicts.

**And TODAY's own balance does not move.** `scalar_today`, `cash_scalar_today` and the daily points
for 2026-07-30 and 2026-07-31 are all unchanged at `$1,307.66`; the first moved daily point is
2026-08-01. What moves is the current period's END balance and everything after it. For an operator
deciding whether this is safe to ship, that distinction is the whole risk profile.

| figure | today (shipped) | R-DH (d) as ruled |
|---|---|---|
| current period projected end balance | **-$19.95** | **+$514.13** |
| every later period end, every later daily point | -- | `+$534.08` |
| anything before the current period, any other account | -- | unchanged |

The arithmetic, from the two live envelopes (`Groceries` budget `$500.00` with `$485.10` recorded,
`Gas` budget `$80.00` with `$48.98`), against the `$1,307.66` anchor observed 2026-07-31 and
`$747.61` of other projected bills:

```
uncleared (today):  max(500 - 0, 485.10) + max(80 - 0, 48.98) = 500.00 + 80.00 = 580.00
                    1307.66 - 580.00 - 747.61 =  -19.95
cleared  (ruled):   max(500 - 485.10, 0) + max(80 - 48.98, 0) =  14.90 + 31.02 =  45.92
                    1307.66 -  45.92 - 747.61 = +514.13
```

The bank balance was read at 07:58 ET. The `$534.08` was spent between 10:41 and 15:02. **The money
is neither in the anchor nor held back from the projection**, so it is counted as available and as
already spent at the same time. F11's `$362.51` estimate was taken at 14:42 the same day and the
shopping continued (two entries added, one amended).

### 10.2 Which rule is right, scored on the only evidence the app has

Every entry classified by its own recorded facts:

- **53 of 82 fall on a day that also carries an assertion for their account**: 32 recorded BEFORE
  that day's last assertion, 21 after.
- The 21 split cleanly by how long after -- and the gap is real, not a chosen threshold: the near
  group tops out at **19.9 minutes** and the far group starts at **160.2 minutes**, with nothing
  between. **14 within 20 minutes** is a bookkeeping session -- the purchase predates the reading,
  so the asserted balance DOES contain it, and here the DAY RULE is right and the recording order
  is wrong. **7 more than an hour later** is anchored-in-the-morning, shopped-in-the-afternoon --
  the assertion does not contain it, and here the day rule is wrong.
- **So on same-day entries the day rule is right 46 times in 53 and wrong 7.**
- Restricted to DEBITS, since a credit entry reduces the reservation whatever its flag says:
  **the day rule is wrong on `$623.70` over 7 entries, of which `$534.08` is TODAY alone --
  `$89.62` over the other four months.** The recording-order rule is wrong on **`$177.43` over 5**,
  spread across those months. **Outside today, the day rule's residual is half the recording-order
  rule's.** The directions differ: the day rule errs optimistic, the recording-order rule
  conservative.

> **RETRACTED (audit, 2026-08-01).** This section first argued that **0 of 82 entries have ever
> been backdated** -- 73 recorded on the day they carry, 9 carrying the day after -- and concluded
> that *"the case the derived rule wins has not occurred once in four months"*. **That inference is
> unsupportable and is withdrawn.** The create form's `entry_date` is
> `type="hidden"` (`app/templates/grid/_transaction_entries.html:190`) and the audit trail shows
> **0 of 74 UPDATEs on `transaction_entries` ever touched `entry_date`** (trail from 2026-05-07).
> Every stored entry date in production is the form's default, unedited. **The measurement is a
> property of the form, not of the user**: the app has never offered a way to backdate a purchase,
> so the absence of backdating is evidence of nothing. Un-hiding that field -- which R-DH (e) and
> step 2 both require -- is exactly what makes the case occur.
>
> The 9 day-after rows were also misattributed. They stopped on **2026-06-12**, when `15eba64f`
> pinned `TZ: America/New_York` on the production container (`deploy/docker-compose.prod.yml`
> names this failure mode in its own comment) -- a month before `display_today()` reached the form
> (**2026-07-25**, `5b3764a7`). Ten evening entries in June and two in July carry the correct
> Eastern day under the OLD code. `app/routes/entries.py:159` is a real guard; it is not what ended
> the phenomenon.

### 10.3 The impossibility, which is the actual finding

R-DH (c)'s worked example and today's production state are the same two rows:

| | R-DH (c)'s example (a test on this branch) | production, 2026-07-31 |
|---|---|---|
| assertion `observed_on` | today | today |
| entry `entry_date` | today | today |
| what is TRUE | the asserted balance INCLUDES the purchase | it does NOT |
| the only difference | the entry was recorded BEFORE the assertion | AFTER |

**No rule that compares only DATES can answer both correctly**, because the two rows carry the same
dates. The options, measured (the residual column is CUMULATIVE at-recording exposure over four
months, not the residual standing at any one instant -- at the measurement instant the rendered
residuals are `$534.08` for option 1 and `$0.00` for the rest):

| option | what it does | moves today | four-month exposure |
|---|---|---|---|
| **1. R-DH (d) as ruled** | derived; `entry_date <= observed_on` | **+$534.08** on the period END (today's own balance is unchanged) | `$623.70` over 7 debits, `$89.62` of it outside today; **optimistic** |
| **2. derived + recording-order tie-break** | `entry_date < observed_on`, or `==` and the entry was recorded no later than the assertion | **$0.00 -- 0 of 15,682 leaves move, verified twice** | `$177.43` over 5 debits; **conservative** |
| **3. strictly-before** | `entry_date < observed_on`, no tie-break | `$0.00` today | `$1,920.61` over 26 debits; contradicts R-DH (a) for no measured gain |
| **4. status quo** | stored flag, bulk UPDATE, manual toggle | `$0.00` | same as 2, plus the flag goes stale and 7 rows never clear at all |
| **5. ask at the true-up form** | the assertion records whether it covers what is already recorded | `$0.00` | none -- the user supplies the fact -- at one question per true-up, and it does not generalize to settles |

Option 2 was run end to end and reproduced independently by the audit: `is_cleared` recomputed from
the rule on all 82 rows, whole-seam baseline recaptured, **0 of 15,682 leaves moved**. It differs
from the stored flag on exactly the 7 inert rows. That zero is partly STRUCTURAL and saying so is
the honest form of it: every row it changes sits on a settled parent, and the reservation prices
only projected rows (`cash_ledger/_amounts.py:273-274`), so it could not have moved a figure.

> **Scope of the impossibility, corrected by the audit.** The claim demonstrated is over rules that
> compare the two DATES. It was first stated over every order-blind rule, which is wider than the
> measurement reached: the two cases are not identical in every recorded fact -- R-DH (c)'s example
> asserts `$1,157.39` against a book of `$1,307.66` (a `-$150.27` shortfall exactly equal to the
> recorded entries) while production asserts `$1,307.66` against a book of `$1,894.93` (a
> `-$587.27` shortfall). The shortfall is a recorded fact and no argument was given for why it
> cannot discriminate. It plainly does not discriminate CLEANLY here -- `$587.27` of shortfall
> against `$534.08` of entries would over-clear -- but "no date rule can" is what was proved, and
> that is what the section now claims.

### 10.4 The asymmetry option 2 rests on -- REFUTED by measurement

Option 2 needs the entry side to differ from the settle side, or it is simply a second answer to
one question. The argument offered was: *a settle is a transcript of the same bank reading that
produced the anchor, and an envelope entry is not a bank record at all until it posts* -- so
recording order is informative for entries and anti-informative for settles.

**That behavioural claim is not in the data.** Measured on the same clone, Checking's 135 settled
rows carrying a `paid_at`:

| | entries | settled transactions |
|---|---|---|
| rows on a day that also carries an assertion | 53 of 82 | **134 of 135** |
| recorded AFTER that day's last assertion | 21 | **71** |
| recorded MORE THAN AN HOUR after | 7 | **7** |
| gross carried by those | `$623.70` | **`$3,142.61`** |

The user ticks settles hours after anchoring exactly as they record purchases hours after
anchoring -- `Transfer to Mortgage` `$1,910.95` at 641 minutes, `Groceries` `$501.60` at 160,
`Transfer to Fidelity Savings` `$500.00` at 96. **The settle side carries FIVE TIMES the exposure
of the entry side, and R-DH (a) already accepts it by explicit ruling**, having measured that the
alternative (recording order) is far worse: gross plug `$40,554.34` against `$15,367.94`.

So option 2 would reject, on the smaller surface, a residual the arc has already ruled acceptable
on the larger one -- and would do it by reinstating the very signal R-DH (a) deleted. There is no
measured asymmetry to fence.

One thing the refutation does NOT touch: `paid_at` is used as a **money-movement time** (it dates
the settle in the walk) and is wrong for that job, which is what step 2 fixes. Using a recording
instant to date money and using it to decide what a balance contained are different mistakes, and
only the first is what `-$4,001.42` was.

### 10.5 Two test defects this measurement found

- **`test_an_entry_alone_does_not_move_the_projected_end_balance` does not test the production
  shape, and under S1-c it would keep passing while testing nothing.** Its docstring states the
  semantic R-DH (d) deletes -- *"The entry is UNCLEARED -- the anchor was read before the purchase
  and does not contain it"* -- and names `$130.32` as the wrong answer to guard against. Under
  R-DH (d) that IS the answer whenever the assertion's observed day is on or after the entry's
  date, which is the production case. The test survives only because `override_anchor` dates the
  fixture's assertion to the PERIOD START (`tests/_test_helpers.py:2885-2894`) while the entry is
  dated `display_today()`.

  > **CORRECTED by the audit.** This first claimed the test would fail every Monday, when the
  > current period's start and today coincide. It would not: `tests/test_services/conftest.py:23-27`
  > freezes today to **2026-03-20, a Friday**, and `freeze_today`'s module-wide patch reaches
  > `tests/conftest.py`'s own `date` symbol, so `_today_relative_start_date()` is frozen too and
  > the two sit a fixed FOUR DAYS apart on every run. **The real defect is worse than a Monday
  > flake**: the fixture's assertion is four days stale relative to the entry, the exact opposite
  > of production where the anchor is same-day, so under S1-c the test would keep passing while no
  > longer exercising the boundary it names. That is finding **R8**'s shape -- a test that silently
  > stops testing what it says -- and it must be pinned to a same-day assertion before S1-c moves,
  > whichever option is ruled.
- **R-DH (c)'s "either order" is tested in one direction only.** `test_an_entry_plus_a_matching_
  trueup_does_not_move_it_either` records the entry and THEN trues up. The reverse -- anchor, then
  record -- returns `-$170.22` against the invariant's `-$19.95` on today's code, and under options
  2, 3 and 4 it always will. Only option 1 (and option 5) make that direction true.

### 10.6 Recommendation: R-DH (d) STANDS AS RULED

**This section first recommended option 2. The adversarial audit refuted the three findings that
recommendation rested on, and the recommendation is withdrawn.** What F11 raised is real; it does
not survive scrutiny as a reason to amend the ruling.

Four measurements decide it:

1. **The asymmetry option 2 needs does not exist (10.4).** The settle side carries `$3,142.61` of
   the identical "recorded hours after the assertion" exposure against the entry side's `$623.70`,
   and R-DH (a) already accepts it deliberately, having measured that recording order is far worse.
   Option 2 rejects on the small surface what the arc accepts on the large one, using the signal
   R-DH (a) deleted.
2. **Outside today, option 1's residual is HALF option 2's** -- `$89.62` against `$177.43` over
   four months. Today is a single outlier: a large Friday shop after a 07:58 anchor. The
   "five times smaller" figure that supported option 2 was `$623.70 / $177.43 = 3.5x` and it was
   pointing the wrong way once today is separated from the four months.
3. **The evidence that option 1 could never win was an artifact of the form (10.2).** Backdating has
   never occurred because the create form has never permitted it. Un-hiding that field is required
   by R-DH (e) and by step 2 -- and the moment it ships, the case option 1 wins starts occurring.
4. **Option 1 satisfies R-DH (c) in both directions and keeps ONE predicate**, which is what step 3
   exists to enforce. Option 2 is a second answer to Section 2.1's question with no measured
   asymmetry to justify it -- a fifth row in that table, which is the shape this whole document
   was written against.

**So: ship R-DH (d) exactly as ruled**, with three things that are NOT optional and must ship with
it, because they are what bounds the residual it accepts:

- **Un-hide the entry date** (`app/templates/grid/_transaction_entries.html:190`). A user who
  shops after anchoring can then date the purchase to the day it will hit, which is the residual's
  actual fix and what R-DH (e) already rules. Today the field is hidden on create and has never
  been edited on any row in production.
- **Ship R-DH (f)'s split with it.** *Book vs bank* is `asserted[period]` and *Period timing* is
  `moved - net`, both already computed in `_cash_fold._assemble_figures` (`:958-960`) -- **one
  line**, on shipped code. The residual R-DH (d) accepts then has a named home on screen instead of
  disappearing into an unreadable remainder.
- **Pin the blind test first** (10.5): a same-day assertion, so the boundary S1-c moves is actually
  exercised.

**The `+$534.08` is real and must be stated plainly when it ships**: on the day it lands, the
current period's projected end balance rises from `-$19.95` to `+$514.13` and stays optimistic
until the next assertion. Today's own balance does not move. That is R-DH (a)'s accepted residual
applied consistently to entries -- which is the argument for it, and the reason it is not a defect
being introduced but an inconsistency being removed.

**One contradiction to settle in the same ruling.** R-DH (e) says a date means the day the money
HIT THE ACCOUNT; ruling R-M and `entry_service._reject_future_entry_date` say an entry records a
purchase that HAPPENED and refuse any date after `display_today()`. For a debit card those are one
to two days apart, so the two rulings cannot both hold. R-M's guard is load-bearing for plan step
X-c2's deleted as-of window, so relaxing it is not a one-line change and it should not be done
inside S1-c.

> **The from-scratch design this fork prompted was built, adversarially reviewed and REJECTED --
> Section 11.** It proposed recording the observation per movement rather than deriving it, and it
> re-opened `-$4,001.42` by a different route. What survives from it is in 11.3.

## 11. From scratch: the design that was investigated, adversarially reviewed, and REJECTED

The developer's answer to Section 10's fork on 2026-08-01 was not one of the five options:
*"How would you design this from scratch? ... Correctness is the priority."* This section is that
design, the review that broke it, and the two pieces of it that survive. **It is recorded as a
REJECTED design rather than deleted, because the reasons it fails are the reasons a future reader
would otherwise propose it again.**

### 11.1 The premise it corrected, which SURVIVES

R-DH (d) rejects a stored reconciliation flag as *"a denormalized copy of a derivable fact, which
is the `Account.current_anchor_*` disease this arc is already removing at step X-e"*. **That
premise is wrong, and the correction stands whatever ships.**

`current_anchor_balance` genuinely is derivable -- the history rows produce it
(`cash_ledger/_facts.py:188-197`). **Whether the bank had posted a purchase is not.** A bank posts
when it posts; that moment is not a function of the purchase date, the recording instant, or any
other column in this database. A stored flag holding a bank observation would not be a
denormalization -- it would be the only place that fact could live.

R-DH (d)'s CONCLUSION still stands, but for a different reason than the one it gives: not because
the fact is derivable, but because **the app never actually collects the observation**, and a
stored boolean that no one observes is a guess with a database column, which is strictly worse than
a guess computed at read time where it can be seen.

### 11.2 The design, and why it fails

Proposed: a nullable `cleared_by_assertion_id` FK on `budget.transaction_entries` and
`budget.transactions` pointing at `budget.account_anchor_history` (NULL = outstanding), the walks
partitioning on that recorded coverage instead of on civil days, and the anchor true-up form
becoming a real reconciliation whose pre-ticks are the civil-day rule demoted from an engine rule
to a form default.

**A neutral adversarial review found four disqualifying defects. Two were verified independently
against the code before the design was dropped.**

- **It breaks the property verification standard 1 is built on, while standard 2 stays green.**
  `dated_deltas` places every event on its OWN civil day and the fold prefix-sums by day
  (`cash_ledger/_walk.py:333-340`, `balance_at/_cash_fold.py:362-372`). Partition the WALK on
  coverage while the FOLD still places by date and an assertion's own day stops reading the
  asserted balance -- yet `reconciliation = moved - net + asserted`
  (`_cash_fold.py:958-960`) is algebraic and holds for any correction value, so the arc's
  strongest gate would not notice. `dated_deltas`' own docstring (`:302-311`) says the walk and the
  re-key must be on ONE granularity and that the split "is what cost production `$4,001.42`". The
  design re-opens it. The only escapes are to fabricate a date for an uncovered movement, or to
  keep day ordering in the fold -- in which case coverage changes the size of a correction and
  never a balance, and buys nothing.
- **It reproduces `-$4,001.42`.** Coverage is written at true-up; ticking a bill goes through
  `status_seam.py:100-105`, which stamps `paid_at` and nothing else. Replay the 2026-07-31 session:
  the user asserts `$1,307.66`, then ticks three more rows six to nine seconds later. Nothing
  covers them. They ride on top. The claim that the defect "has no mechanism to arise" was false,
  and a read-time total rule is not interchangeable with a write-time one-shot default.
- **It has no correct implementation for transfers.** A transfer's two shadows sit on two different
  accounts (`transfer_service.py:321,325`), and the posted ledger already dates a transfer's net on
  BOTH linked ledgers from the income shadow's `paid_at` (`account_posting_service/_walk.py:245-267`).
  "Coverage must point at an assertion for the same account" and "both shadows are covered
  together" cannot both hold.
- **The same-day duplicate index would silently discard a reconciliation.** Re-asserting the same
  balance for the same day raises `IntegrityError`, which `apply_anchor_true_up` catches, rolls the
  session back, and reports as idempotent success (`anchor_service.py:367-379`). Every coverage
  write in that session would be lost while the UI said it saved.

Nine further findings (predicate multiplied across three write sites rather than deleted; no
provenance distinguishing a defaulted value from an observation; no lifecycle rule for
`done -> projected` un-settles; `ON DELETE SET NULL` making an assertion deletion a silent
money-mover; scenario scoping; and a deletion list that does not survive checking) are in the
review and are not repeated here.

### 11.3 What survives

- **11.1's premise correction**, which belongs in R-DH (d)'s record.
- **`merge_anchor_and_cash_events`' docstring promises something step 2 cannot deliver**:
  *"step 2 ... removes the guess entirely by recording the two real dates the question actually
  turns on"* (`cash_ledger/_events.py:526-528`). Section 10.3 disproves it -- with both real dates
  recorded the same-day case is still two truths and one fact. That is F7's stale-rationale shape
  and should be corrected in whichever step ships next.
- **R-DH (f) needs no new machinery.** *Book vs bank* is `asserted[period]` and *Period timing* is
  `moved - net`, both already computed in `_cash_fold._assemble_figures` (`:958-960`). The design
  claimed to enable that split; it is one line on shipped code, and Section 10.6 now sequences it
  with S1-c because it is what gives R-DH (d)'s accepted residual a visible home.

## 12. S1-c as RULED: one column carried two facts, and it splits

Ruled by the developer 2026-08-01, in the session that built S1-c. Section 10.6 had recommended
shipping R-DH (d) exactly as written; tracing the code to build it surfaced a defect underneath the
fork that neither Section 10 nor Section 11 had named, and the developer re-ruled on it.

### 12.1 The defect: `entry_date` meant two different things

```
app/models/transaction_entry.py:26   entry_date -- "Date the purchase occurred"
ruling R-M (README.md)               "an entry ... is something that HAPPENED" -> refuse > today
ruling R-DH (e)                      "A date means the day the money HIT THE ACCOUNT, not the day
                                      the purchase happened.  They differ by a day or two for a
                                      debit card."
```

Two rulings, one column, opposite definitions. **Every reconciliation rule built on that column
inherits the ambiguity**, which is why Section 10.3's "no date rule can answer both cases" felt
like an impossibility result: it was measuring one field being asked two questions.

The rest of the app had already drawn this line. `CashSourceFact` carries a cash clock beside a
budget clock and says so in its own docstring -- *"It carries TWO clocks, and the second one is not
decoration"*; a loan payment carries a `due_date` beside its pay period (ruling in
`project_loan_due_date_is_a_posting_input`); and S2-b is about to add `transactions.settled_on` for
exactly this reason. **The transaction entry was the last cash-moving record in the app answering
both clocks with one column.**

### 12.2 A correction to Section 10.6, which is what made the split reachable

Section 10.6 defers the R-M fork on the ground that *"R-M's guard is load-bearing for plan step
X-c2's deleted as-of window"*. That was true when the guard was written and **is no longer the
reason the window stays deleted.** X-c2c1's own as-built, quoted verbatim in
`cash_ledger/_amounts.py`, replaced it with a sharper one:

> *"a purchase that happened belongs in the reservation whatever date the reader is asking from"*
> ... *"What a row is WORTH is a function of the row ... the reader's clock decides WHEN the row
> lands, never what it is worth."*

So the window stays deleted on the ROW's semantics, not on the guard. The guard's real job is
narrower and still valid -- stop a purchase you have not made from moving a rendered balance,
measured at R-M as `-$89.45` as a debit or `+$60.55` as a CC entry -- and that job is about the
PURCHASE date. It says nothing about the POSTING date.

### 12.3 R-M -- AMENDED. The column splits; the guard does not move.

**`transaction_entries.entry_date` becomes `purchased_on`, and a nullable `settled_on` joins it.**

- **`purchased_on`** -- NOT NULL, the day the purchase was MADE. R-M's guard is **unchanged** and
  now sits on the column it was always about: a value after the user's today is refused at both
  write doors (`entry_service._reject_future_purchase_date`). Budget consumption (`remaining`), the
  out-of-period warning and the entry list's ordering all read it, which is what they always meant.
- **`settled_on`** -- NULLABLE, the day the bank TOOK the money, recorded only when the user has
  SEEN it. `CHECK (settled_on IS NULL OR settled_on >= purchased_on)`: money cannot leave the
  account before it was spent. **No upper bound** -- any "at most N days ahead" ceiling would be an
  unjustifiable constant, and a wrong forward date is visible on the row and self-corrects at the
  next true-up.

*Rejected:* **widening R-M's bound on one column** (the column would then mean "purchase day" on
some rows and "posting day" on others with nothing in the schema recording which -- contradictory
data by construction, and `remaining` plus the out-of-period warning both read it as the purchase
day); and **keeping R-M exactly as it stood** (the forward case a debit-card float actually
produces would remain inexpressible, so the app could not be told the truth).

### 12.4 R-DH (d) -- RESTATED. Derived from an OBSERVATION, not from a guess.

An entry is reconciled iff `settled_on` is on or before the account's latest asserted `observed_on`
-- `cash_ledger.is_inside_assertion`, the same predicate in the same units the read fold and the
posting walk apply to a settled transaction, **evaluated at read time**.

**A NULL `settled_on` means "not observed to have posted", and it is NOT reconciled.** That is the
conservative arm: the envelope keeps holding its whole budget back until the user confirms the money
has left. **The engine never guesses a posting day on a user's behalf.**

This is what Section 11.1's surviving premise demanded and Section 10.6 could not deliver. 11.1
established that *whether the bank had posted a purchase is not derivable* -- it is not a function
of the purchase date, the recording instant, or any other column -- and concluded that a stored flag
holding an unobserved value is *"a guess with a database column, which is strictly worse than a
guess computed at read time where it can be seen"*. Both halves are now satisfied at once: the
FLAG is gone (derived), and the DATE it derives from is observed rather than guessed.

*Rejected:* falling back to the civil-day rule on `purchased_on` when `settled_on` is NULL. It
would have kept Section 10.6's `+$534.08` and put the engine back in the business of guessing, on
a field whose whole purpose is to record what was seen.

### 12.5 The reconcile step -- how an observation gets recorded

When you enter a balance, the app lists the purchases it still thinks are outstanding and you tick
the ones your statement shows. **A tick stamps `settled_on` with the assertion's `observed_on`** --
an UPPER BOUND on the true posting day, and the only bound the reconciliation predicate consumes,
so no answer changes by sharpening it. The exact day is editable on the entry afterwards.

*Where it lives:* the one-click inline anchor editor is **untouched** (that habit produces an
assertion every 2.3 days on the real data and taxing it would cost more than a prompt buys). A
successful true-up appends an out-of-band reconcile modal **only when there is something to
reconcile**; the same partial is a permanent section on the cash account detail page, so dismissing
the prompt loses nothing.

*It is its own request, deliberately.* Folding it into `apply_anchor_true_up` would put it inside
that function's F-103 duplicate handler, which catches an `IntegrityError`, rolls the session back
and reports idempotent success -- so a same-day re-assert would silently discard every
reconciliation just made while the UI said it saved. **That is Section 11.2's fourth disqualifying
defect, carried forward and designed around rather than rediscovered.**

*Rejected:* replacing the inline editor with a true-up panel (taxes the habit on all five opener
surfaces); and a detail-page-only list with no prompt (nothing catches the user at the moment they
are holding the statement).

### 12.6 R-DH (c) -- BOTH invariants now hold, in both orders

Section 10.5 recorded that R-DH (c)'s two promises could not both be kept, and Section 10.6
accepted breaking the second. **Under the observed-date design neither is broken:**

| | what happens | invariant |
|---|---|---|
| record a purchase, nothing else | `settled_on` NULL -> outstanding -> reservation holds the full budget | **holds** -- the projection does not move |
| record it, then true up and tick it | reconciled -> reservation falls by the purchase; the anchor fell by the same | **holds** -- the two cancel |
| true up first, then record it, then tick it | same -> reservation falls by the purchase | **holds** -- "either order" is finally true |

The third row is what today's shipped code gets wrong: the bulk clear ran before the entry existed,
so it never cleared and the projection read `-$170.22` against a true `-$19.95` until the NEXT
true-up. That defect is 14 of the developer's 53 same-day entries (Section 10.2) and it is the one
S1-c actually fixes.

### 12.7 R-DH (f) -- the remainder splits into TWO rows with independent visibility

`CashPeriodFigures.reconciliation` becomes **`period_timing`** (`moved - net`: money budgeted here
that moved elsewhere, or has not moved yet) and **`book_vs_bank`** (`asserted[period]`: what the
user's own balance readings booked). `GridColumn` and `GridRowFlags` carry both, and **each row is
asked R-O's visibility question separately** -- a window carrying only true-ups must not also render
a permanently-`$0.00` timing row, which reads as "measured and zero" for a fact that was never in
question.

No combined `reconciliation` accessor survives. Leaving one would invite a surface to render the
sum again, which is the figure the ruling exists to delete.

The identity becomes:

```
balance[p] - balance[p-1]
    == net[p] + period_timing[p] + book_vs_bank[p] + contribution[p] + accrual[p]
```

### 12.8 What was measured on the production clone

A fresh `pg_dump` of production (2026-08-01, read-only) restored into dev and upgraded to
`d7c1f4a9e603`.

| standard | result |
|---|---|
| the migration runs both directions | **PASS** -- upgrade, downgrade, upgrade; `downgrade` REFUSES when any row carries an observed `settled_on` and names the row plus the recovery path |
| no figure moves on the day it ships | **PASS** -- current period `-$19.95` before and after |
| the backfill invents no date | **PASS** -- `settled_on` starts NULL on all 82 rows; measured, **0 of the 70 `is_cleared = TRUE` rows sat on a Projected parent** (53 debit + 17 credit, all on already-settled parents), and the reservation prices only Projected rows, so the dropped flag could not have moved a figure |
| R-DH (f)'s split lands on the hand-computed halves | **PASS** -- `period_timing = -$427.22`, `book_vs_bank = -$160.05`, exactly the decomposition Section 3 predicted for the `-$587.27` single row |
| `pylint app/` | **PASS** -- 10.00/10, including the two new W9909 classifications and the W9910 boundary |

### 12.9 Structural work this step forced, and why it belongs

> **NAMES SUPERSEDED BY STEP 3 (Section 14), 2026-08-01.** Three of the names below no longer
> exist: `cash_ledger.is_inside_assertion` is now the method
> `cash_ledger.ReconciledThrough.covers`, `cash_ledger.latest_observed_day` is
> `cash_ledger.reconciled_through`, and `CashLedgerWalk.latest_observed_on` is
> `CashLedgerWalk.reconciled_through` -- all three now returning the boundary TYPE rather than a
> bare `date`. `merge_anchor_and_cash_events` is DELETED. **The RULINGS below are unchanged**; only
> the spellings moved. Sections 12 and 13 are kept as the as-built record of S1-c and are not
> rewritten, so grep the code, not this section, for a live symbol.

- **`balance_at._cash_fold` split.** Adding R-DH (f)'s second field pushed it past the 1,000-line
  ceiling. The period-view half moved whole to **`balance_at._cash_periods`**: assembling a running
  total and regrouping it into columns are two jobs sharing exactly one input
  (`AssembledCashFold`), and the dependency runs one way. Growing past a gate is a signal, and the
  seam it was measuring is real.
- **`cash_ledger.is_inside_assertion`** -- the ONE statement of the arc's central question, public
  so the read fold, the posting walk and the entry reservation cannot each grow their own. This is
  step 3's convergence, arriving early because S1-c is the site step 3 names.
- **`cash_ledger.latest_observed_day`** -- one accessor for "the account's latest asserted day",
  which **deleted a duplicate**: `account_posting_service._sync` had its own copy, and a second copy
  of this question is what carried the timezone-sign dependency finding N-133 / F4 closed.
- **`CashLedgerWalk.latest_observed_on`** -- the in-memory twin for callers already holding a walk,
  so the fold pays no query. Its equality with the SQL form is pinned by a test rather than assumed.
- **`ProjectedBasis`** -- the two per-account facts a still-Projected valuation needs (the live
  override map, and the day through which purchases are reconciled), bundled and **REQUIRED**. Two
  optional arguments would be two ways to hand the reduction half a basis, which would silently
  value every purchase as outstanding.
- **`#modal-mount`** -- the body-level modal target generalised from `#carry-forward-modal-mount`,
  because an out-of-band prompt needs a mount on every page the anchor editor opens from.

### 12.10 One defect found in passing, fixed here

`app/routes/transactions/_helpers.py` passed `today=date.today()` into the add-purchase form, whose
own contract requires `display_today()` and whose value `entry_service` judges on the display clock
(ruling R-M). On any process not pinned to `America/New_York` -- CI runs `TZ=Pacific/Kiritimati`
precisely to catch this -- the form defaults to a date its own server refuses. Latent in production
(the container pins the zone); the same two-clock shape as finding N-133 / R2.

### 12.11 What this does NOT close

- **The settle side still guesses.** `transactions.settled_on` (S2-b) gives a settle the same second
  clock a purchase now has, but Section 10.3's result stands for it: with both dates recorded, a
  movement made after the balance was read still carries the same civil day as one made before it.
  Only an observation ends that, and on the settle side there is no reconcile step yet.
- **A bank import (OFX/CSV/Plaid) is the terminal state** and the only thing that removes the guess
  without asking the user anything. It is outside this arc and is named here so the arc stops
  implying that step 2 achieves it -- `merge_anchor_and_cash_events`' docstring made exactly that
  claim and is corrected in this branch.
- **R-DH (e) vs R-M is settled for entries only.** A transaction still has one date doing both jobs
  until S2-b.

## 13. S1-c as BUILT: the conversion, what it cost, and what the review found

**COMPLETE and GREEN, 2026-08-01, commit `b305b7b5` on `feat/entry-posting-date`.** This section
was a work-list -- 157 failures across 20 files, with the ruling each conversion had to satisfy --
and it is kept as the as-built record of what that cost, because three of the entries below were
not renames and one of them was a defect the conversion itself introduced.

The standard the conversion was held to: **a conversion is CORRECT when it satisfies the ruling
named in its row; a conversion that merely makes a test green is not.**

| gate | result |
|---|---|
| the suite | **7,724 passed / 0 failed** |
| the suite under CI's clock (`TZ=Pacific/Kiritimati`) | **7,724 passed / 0 failed** |
| `pylint app/` (with every custom checker as `--fail-on`) | **10.00/10** |
| `pylint scripts/`, cross-tree `duplicate-code`, `tests/` Decimal gate | **clean** |
| whole-seam clone diff, 9 accounts / 427 grid cells / 5,978 daily points | **0 of 15,682 leaves moved** |
| the migration, both directions | **PASS** -- and the downgrade REFUSES once any row carries an observed `settled_on`, naming the row and the recovery SQL |

> **Rebuild the test template before running the suite.** Migration `d7c1f4a9e603` invalidates
> `shekel_test_template`; without the rebuild the suite shows ~1,007 failures that are entirely
> `column transaction_entries.purchased_on does not exist`. The invocation needs the admin URL
> derived from `.env`:
>
> ```bash
> export TEST_DATABASE_URL=$(grep -h '^TEST_DATABASE_URL=' .env | cut -d= -f2-)
> export TEST_ADMIN_DATABASE_URL="$(printf '%s' "$TEST_DATABASE_URL" \
>     | sed -E 's#(/)[^/?]+(\?|$)#\1postgres\2#')"
> python scripts/build_test_template.py
> ```

### 13.1 The conversion, by file

Each row's ruling is the one its conversion had to satisfy, not merely the symptom that made it
red.

| file | count | the ruling it satisfies |
|---|---|---|
| `test_services/test_entry_service.py` | 35 | 12.3, 12.4. `check_entry_date_in_period` is `check_purchase_date_in_period`; the `toggle_cleared` and `clear_entries_for_anchor_true_up` classes are gone with the functions, replaced by `outstanding_purchases` / `record_settled_days` coverage (13.3) |
| `test_services/test_cash_amounts.py` | 23 | 12.4. Every case takes a `reconciled_through`; the fixture triples carry a DATE where they carried a bool, so each test's bucket is readable at its call site. Three cases were not expressible under a flag and are new: NULL is outstanding, a posting day AFTER the statement is outstanding, and an account that never asserted reconciles nothing |
| `test_routes/test_grid.py` | 23 | 12.7. `col.reconciliation` -> `col.period_timing` + `col.book_vs_bank`, and the render tests tell the two rows apart by the LABEL a user reads, because the `reconciliation-row` class now marks both |
| `test_services/test_balance_at.py` | 22 | 12.7, plus `cash_period_view` / `CashPeriodFigures` moved to `balance_at._cash_periods` |
| `test_services/test_transaction_service.py` | 8 | mechanical: the entry fixtures' renamed kwargs |
| `test_services/test_cash_flows.py` | 8 | 12.4. `sum_projected` takes a REQUIRED `ProjectedBasis` |
| `test_services/test_anchor_service.py` | 7 | 12.4, 12.5. A true-up NO LONGER TOUCHES ENTRIES; `apply_anchor_true_up` lost its `user_id`. The two tests asserting the flip now assert the opposite |
| `test_routes/test_accounts.py` | 6 | 12.4, 12.5. The bulk-clear class covered a deleted function; re-ruled to the reconcile route, keeping the per-account scoping invariant |
| `test_routes/test_entries.py` | 5 | 12.4. The toggle route is deleted; replaced by the read-only derived indicator's render and the `settled_on` edit path (including that an empty value CLEARS it) |
| `test_integration/test_cross_page_balance_equality.py` | 5 | 12.7 |
| `test_services/test_posting_service.py` | 3 | mechanical: `TransactionEntry.purchased_on` vs the unchanged `JournalEntry.entry_date` |
| `test_cash_fold`, `test_cash_period_view`, `test_daily_balance_series`, `test_asset_fold`, `test_account_posting_service`, `test_retirement_dashboard_service`, `test_models/test_posting_cash_backfill`, `test_transaction_posting_lifecycle`, `test_frozen_db_clock` | ~12 | 12.4, and the finding in 13.2 |
| `test_services/test_anchor_settle_partition.py` | 2 | 12.6, re-ruled to the three-row table |
| `test_service_log_events`, `test_utils/test_log_events`, `test_cash_period_view` | 3 errors | the two deleted log events, and the moved `cash_period_view` |

**The rename had been over-applied, and the conversion corrected it in both directions.**
`JournalEntry.entry_date` is UNCHANGED by this step -- only `TransactionEntry.entry_date` became
`purchased_on` -- but 15 sites across `test_posting_service.py`, `test_posting_cash_backfill.py`
and `test_anchor_settle_partition.py` had been swept along with it, and three helper signatures
kept the old parameter name against a renamed body (`E0602: Undefined variable 'purchased_on'`,
which pylint reported at the pre-conversion commit). A grep in both directions now finds no
`TransactionEntry.entry_date` and no `JournalEntry.purchased_on` anywhere in `app/`, `tests/`,
`scripts/`, `migrations/`, `tools/` or the templates.

**One consumer outside the suite was missed by the same sweep.**
`tests/manual/verify_balance_baseline.py` -- the instrument this arc's verification standard
diffs every change against -- still read `col.reconciliation` and would have raised
`AttributeError` before writing a line. It now emits both halves plus their sum under the old key,
so a baseline captured BEFORE the split stays diffable; the shim is documented as
delete-on-next-recut. Ruling 12.7 forbids a combined accessor on the DATACLASS, so that a surface
cannot render the sum; composing it once inside a diff instrument that also emits both halves is
the one place that genuinely needs it.

### 13.2 The finding the conversion surfaced, and the helper that records it

**`is_cleared=True` let fixtures assert a state production cannot reach.** The flag was an
unconditional claim that a purchase was inside the anchor; the derived rule needs the account's
latest assertion to actually cover it. Several suites set it on accounts whose only assertion was
months EARLIER than the purchase -- a state the app cannot produce, because the way a purchase gets
inside a declared balance is that the user declared the balance after it posted. This is **finding
N-132 / R8's shape from a third direction**: a fixture asserting an unreachable state passes for
years and silently stops discriminating the case it names.

`tests/_test_helpers.mark_purchase_settled` is the successor to `add_entry(..., is_cleared=True)`.
It does what the flag did AND asserts the precondition the flag let fixtures skip.

**Its guard is TWO-SIDED, and the upper half was added because the one-sided version let the same
class of defect back in.** Checking only `settled_on <= observed_on` invites the obvious escape:
move the ASSERTION forward until it covers the purchase. But an assertion is itself bounded --
`account_service._reject_undatable_observation` refuses an `observed_on` after the user's today --
so an assertion dated into the app's future is exactly as unreachable as the anchor-months-earlier
row the guard was written to stop. The conversion took that escape on two `test_cash_fold`
fixtures (assertions dated 2026-04-01 and 2026-04-06 against a suite frozen at 2026-03-20), and
the review below caught it. The second bound named both fixtures the moment it was added; they were
reshaped to sit inside the frozen clock, the N-39 firing control now sliding the READER back nine
days instead of dating the purchase into the future -- the same experiment on a state production
can hold. Both still fail with `$800.00` against `$920.00` when the deleted `as_of` window is
re-introduced.

### 13.3 The five tests this step OWED, and where they landed

> **NAMES SUPERSEDED BY STEP 3 (Section 14)** -- see the note at 12.9. The tests below all still
> exist and still pin what they say; the accessors they name were renamed onto the boundary type,
> and item 5's pair is now `reconciled_through` (SQL) == `CashLedgerWalk.reconciled_through`.
> Step 3's own reviews then found that item 5's fixture could not fail as written, and armed it.

Section 10.6 named three things that had to ship with S1-c; these are their controls.

1. **The invariant table in 12.6, all three rows**, at PROJECTED-BALANCE grain --
   `test_anchor_settle_partition.py`. The third row (true up, then record, then tick) is the defect
   S1-c fixes and had never had a test; it asserts the OLD figure (`_PROJECTED_END - _PURCHASE`,
   `-$170.22`) at the intermediate step, so it cannot pass on a build that reconciles nothing.
2. **`mark_purchase_settled`'s own guard** -- `test_entry_service.py`, all three refusals plus a
   positive control, because two refusals alone are satisfied by a helper that refuses everything.
3. **The reconcile step's scoping**, clause by clause from `_outstanding_scope` --
   `test_entry_service.py::TestTheOutstandingSet`, each graded from BOTH doors (listed-or-not and
   stamped-or-not), plus the route's own contract in `test_routes/test_accounts.py`.
4. **The two-clock pin** for `_helpers.py`'s `display_today()` fix (12.10) --
   `test_grid.py::TestTheAddPurchaseFormReadsTheUsersClock`. It substitutes `display_today` for a
   sentinel rather than mutating `TZ`: the C library's zone is process-global and survives
   `monkeypatch`'s env restore, so a leak silently re-zones every later test in the same xdist
   worker. That was measured, not theorised -- the first draft of this test broke an unrelated MFA
   test three files away.
5. **`latest_observed_day` == `CashLedgerWalk.latest_observed_on`** -- `test_cash_walk.py`, on a
   multi-assertion account, on an account whose BUSINESS day defies its recording order (the shape
   that broke the third statement N-133 / F4 deleted), and on one that has asserted nothing.

### 13.4 What a neutral adversarial review then found in the conversion

Run against the finished conversion before the commit. Each finding was confirmed by mutation --
the reviewer deleted the rule and watched the suite stay green -- and each fix carries a negative
control that was then shown to fire.

- **The reconcile route's day resolution was unpinned.** Substituting `display_today()` for
  `cash_ledger.latest_observed_day(account.id)` in `reconcile_purchases` left the ENTIRE 7,721-test
  suite green. Every test in the class trued up *today*, so the two clocks were indistinguishable
  in all of them. The production cost is not cosmetic: `observed_on` has been user-supplied since
  step 2, so a back-dated assertion is ordinary, and under the wrong clock the tick writes
  `settled_on = today > observed_on` -- the reservation never drops, AND the row stops matching
  `settled_on IS NULL`, so the panel can never offer it again. A back-dated-assertion test now pins
  the stamped day.
- **`_outstanding_scope`'s owner clause had no firing control.** Deleting
  `PayPeriod.user_id == owner_id` left all 19 reconcile tests green, including the one whose
  docstring called itself "the IDOR case" -- because every cross-user fixture also crossed
  ACCOUNTS, and the account clause rejected the row first. The clause is now isolated by the only
  shape that can: a transaction on THIS user's account under ANOTHER user's pay period, which the
  schema permits (two independent FKs, no composite constraint) and nothing in the app creates. The
  older test is now labelled over-determined rather than left to be rediscovered.
- **The clean-grid end-to-end guard had narrowed.** It was tightened from `b"Timing"` to the full
  row labels, which silently dropped the mobile Plan recap -- whose chips read "Timing" and "Bank"
  and are gated on `plan_row_flags`, computed over a DIFFERENT window from the tfoot's. Both
  spellings are asserted again.
- Plus the `mark_purchase_settled` upper bound (13.2), a companion refusal that was being probed
  with an unrelated user rather than a companion, a dropped non-cash-account case, and five
  documentation and hygiene items.

**One item was reported and deliberately NOT actioned:** the reconcile POST parses `entry_ids` with
an inline `isdigit()` filter rather than a Marshmallow schema, which deviates from the
schema-before-DB-work convention on state-changing routes. No reachable crash (a 23-digit forged id
returns 200), so it is a standards question rather than a defect, and it is left for a ruling
rather than folded into this step.

> **BOTH halves of that paragraph were wrong, and TWO successive neutral reviews were needed to see
> how wrong** (2026-08-01, while pushing step 3). *"No reachable crash"* is false: of the 888
> characters with `isdigit() == True`, **128 make `int()` raise** (`'\N{SUPERSCRIPT TWO}'` among
> them), and `app/error_handlers.py` registers no `ValueError` arm, so the parse is an unhandled
> 500 on forged input. A census then found the same unsound guard at **four** doors, not one --
> `accounts/anchor.py:282`, `loan/params.py:469`, `settings.py:442`, and `mfa_service.py:251` on
> the LOGIN path, where a 6-character all-`isdigit()` code reaches `hmac.compare_digest` and raises
> `TypeError` on non-ASCII. So it was never a property of this step's route. And *"left for a
> ruling"* named no owner, so nothing would ever have carried a ruling to it: what ruling R-AO
> forbids (*an unowned row does not wait, it rots*), and the shape that produced four stale
> resolvers on 2026-07-27. It is now **N-136**, owned by plan step **X-ae**, whose fix is ruled:
> ONE shared helper for the three int parses, in its own PR. No money moves and nothing is written
> -- the raise precedes all DB WRITES at every site, and the service-side re-scoping that is the
> actual authorization guard is untouched.
>
> **The correction itself needed correcting, twice, and that is the entry worth keeping.** Its
> first draft said `str.isdecimal()` was the sound predicate; it is not
> (`('1' * 4301).isdecimal()` is `True` and `int()` raises on CPython's 4,300-digit limit), so the
> only sound form is to attempt the parse. Its second said the fix touched "two route files no
> balance step opens"; `loan/params.py` is in plan step X-y's stated scope. **A finding downgraded
> to a style item, then corrected three times before it was true, is the case FOR the adversarial
> pass** -- and each correction came from measurement, not from re-reading.

### 13.5 What the clone measured

A fresh prod-shape clone at `main`'s head, captured from a `git worktree` at `main`, upgraded to
`d7c1f4a9e603`, captured again from the branch.

- **15,682 shared leaf figures, ZERO moved. No key disappeared.**
- **854 keys are new, and 854 = 427 grid cells x 2** -- exactly R-DH (f)'s split, nothing else.
- **0 identity breaks**: `period_timing + book_vs_bank` equals `main`'s pre-split `reconciliation`
  on every one of the 427 cells.
- Checking's current period reads **`-$19.95`** with `period_timing -$427.22` /
  `book_vs_bank -$160.05` -- the decomposition Section 3 predicted for the `-$587.27` single row --
  against a cash anchor of `$1,307.66`, the ruling's own worked example.
- **`period_timing` nets to `$0.00` across all history** (period 9's `+$427.22` cancels period 10's
  `-$427.22`), which is the design claim that a row is counted once as budget and once as cash and
  never twice. `book_vs_bank` carries the rest.

## 14. Step 3 as BUILT: the fence is a type, because a checker could not see the site that mattered

**COMPLETE and GREEN, 2026-08-01, commit `d3e3d82a` on branch `fix/one-partition-implementation`,
merged to `main` at PR #76 (`6f4b4cbf`).** Step 3 specified a custom pylint checker.
The developer ruled the
checker out before it was written -- *"I want to make the fences structurally unnecessary"* -- and
tracing what a checker could actually see showed the ruling was also the correct engineering call,
for a reason the plan had not recorded.

### 14.1 Why the checker was rejected, measured

An AST census of `app/` for ordering comparisons (`<`, `<=`, `>`, `>=`) touching the assertion-day
vocabulary returns five sites:

| site | what it compares | verdict |
|---|---|---|
| `cash_ledger/_amounts.py` | `event_day <= observed_on` | the rule itself |
| `account_posting_service/_walk.py:479` | `sources[i][0] <= fact.observed_on` | a RESTATEMENT |
| `entry_service.py:872` | `TransactionEntry.purchased_on <= observed_on` | a different question, in SQL, on the BUDGET clock |
| `account_service.py:192` | `observed_on > today` | a validation bound |
| `account_service.py:208` | `observed_on < floor` | a validation bound |

**And it does not return `account_posting_service/_sync.py:290` -- `earliest <= latest` -- which is
the site finding N-133 / F4 was about.** Both operands are bare locals, so this vocabulary does not
match them.

**The first draft of this section drew a stronger conclusion than the table supports, and an
adversarial review corrected it on three counts.** (1) It said the checker would have caught "the
three sites with no history and missed the one with it" -- but the table's second row, the posting
walk's `<=`, IS a site with history and IS a live catch, so the ratio is three exemptions to ONE
catch, not to zero. (2) It said "a lint rule cannot see through a local binding": adding `earliest`
and `latest` to the vocabulary does match them, at roughly nine more exemptions, and resolving a
`Name` operand to its in-scope `Assign` is one astroid hop rather than value inference -- less
exotic than what `package_privacy.py` already does. The honest reason to prefer syntax here is this
project's own stated convention (`money.py`: *"matched syntactically by name rather than by
inference ... avoids inference flakiness"*), which is a preference, not an impossibility. (3) The
SQL site genuinely cannot call a Python predicate, so it would need a permanent exemption -- that
one stands.

**What survives is the developer's ruling, which was never a claim about lint**: the fence must be
structural rather than a detector. And what the review added is that the two are COMPLEMENTARY --
see 14.5.

### 14.2 What was built instead

**`cash_ledger.ReconciledThrough`** -- a frozen dataclass carrying one field, `observed_day:
date | None`, and one method, `covers(event_day) -> bool`. It defines no ordering against a civil
day, so `settled_on <= reconciled_through` raises `TypeError`. Asking the arc's central question
correctly and asking it wrongly stopped being the same keystroke.

**Verified over ELEVEN shapes, which is both operand orders of all four
comparisons plus `sorted`, `max` and `min`** -- the list `TestTheRuleCannotBeAskedAnyOtherWay`
carries, so this transcript and the committed control cannot drift:

```
day <= boundary  -> TypeError     boundary <= day  -> TypeError
day <  boundary  -> TypeError     boundary <  day  -> TypeError
boundary >= day  -> TypeError     day >= boundary  -> TypeError
boundary >  day  -> TypeError     day >  boundary  -> TypeError
sorted([boundary, day]) -> TypeError   max(day, boundary) -> TypeError
min(day, boundary)      -> TypeError

covers(earlier) True   covers(same day) True   covers(later) False
covers(None) False     ReconciledThrough(None).covers(day) False
```

The first draft listed six, all of which resolve to `__ge__` / `__gt__` because Python reaches the
boundary by REFLECTION -- so a one-sided mutant passed every one of them. Both orders are pinned
now (14.5).

The rule stays TOTAL in both the argument and the boundary, which is what keeps it a rule rather
than one with a precondition each caller must remember.

**The escape hatch is named.** `observed_day` is read where a raw date is genuinely needed -- the
reconcile panel's SQL offer bound, the stamp `record_settled_days` writes, and one rendered
caption. Reaching for it is a visible act at the call site, which a `<=` was not.

### 14.3 The sites, converged -- and the SEVENTH one a review found

| site | before | after |
|---|---|---|
| read replay (`cash_ledger/_walk.py`) | a stable SORT with `_SOURCE_ORDER < _ASSERTION_ORDER` | an absorb loop over `anchor.reconciled_through.covers(...)` |
| posted ledger (`account_posting_service/_walk.py`) | `sources[i][0] <= fact.observed_on` | the same loop, over the same rule |
| self-heal skip (`_sync.py`) | `earliest <= latest` | `boundary.covers(earliest)` |

**The read side's rule was expressed as a SORT and the write side's as a LOOP, and that was the
duplication no one had named** -- the two were the same algorithm in two spellings, held in step by
convention. This is finding F5's shape (`dated_deltas`' tie-break) one level up. Both are now the
same loop over the same call, which is what makes X-d a deletion rather than a rewrite.

`merge_anchor_and_cash_events` is DELETED: with the walk advancing its own pointer, publishing the
merged stream as a separate public fact bought a hop and a second place for the order to be stated.

**A FOURTH site existed and this step's own census could not see it.** An adversarial review found
`balance_at._asset_contributions._dated_events` deciding, with a bare
`period.start_date <= accrual_start`, whether a modelled payroll contribution is already inside the
asserted balance -- the same rule, the same units, the same inclusivity, reached through
`walk.anchor_corrections[-1].observed_on` as a loose date. Its own docstring states the question
verbatim and names the cost: *"an over-count that looks exactly like real growth and so cannot be
detected later."* It is now `reconciled_through.covers(period.start_date)`, and
`_asset_fold._latest_assertion_boundary` returns the boundary so the two consumers there are
visibly different questions: the contribution feed asks `covers`, and the ACCRUAL window takes the
raw day because tiling a calendar with no gap is ruling R-Z's own inclusive boundary and is
deliberately not routed through R-DH's rule.

**Two things this cost the step's own claims.** The `<`-mutant control below could not reach the
contribution feed, so "35 tests fail" understated what had NOT converged; and 14.1's census was
blind to this site for precisely the reason 14.1 gives for rejecting the checker -- `accrual_start`
is a bare local outside the vocabulary. **A census is only as wide as its vocabulary, and this one
was measured against the wrong assumption that the cash package bounded the question.** Measured on
the clone after converging it: **0 of 16,536 leaves move**, which is what a same-rule same-
inclusivity substitution should do and is why it was measured rather than asserted.

**The self-heal skip is a COST guard, and the point is that it is now a CALLER rather than a second
implementation.** Its own docstring records that `sync_account_anchor_postings` is idempotent and
reconciles to target, so running it is always correct; the predicate only avoids the cost of
discovering that. A cost guard that spells the money rule itself can come to disagree with it --
and this one already had, silently, for the whole time it carried F4's timezone-sign dependency.

### 14.4 What was measured

| gate | result |
|---|---|
| full suite | **7,728 passed / 0 failed** (7,724 before; this step adds four, TWO of them the reviews' findings -- corrected 2026-08-01 by a node-id diff against `main`, which was three in the first draft: the other two additions are this step's own fence controls, and 14.5's own record has the reviews' remaining fixes ARMING existing tests (the `__le__` control widened in place, the blind fixture re-armed) rather than adding any.  The step is ONE commit, so that last clause is 14.5's account plus the node-id diff, not an independent measurement of a pre-review tree) |
| the suite under CI's clock (`TZ=Pacific/Kiritimati`) | **7,728 passed / 0 failed** |
| `pylint app/ scripts/` with every custom checker as `--fail-on` | **10.00/10** |
| `tests/` Decimal gate, cross-tree `duplicate-code`, checker package, checker unit tests | **clean; 146 passed** |
| whole-seam clone diff, 9 accounts / 427 grid cells / 5,978 daily points | **0 of 16,536 leaves moved; no key added or removed** -- re-run after the reviews, including the modelled contribution feed's convergence |
| the posted ledger, reconciled by the NEW walk against the ledger the OLD walk wrote | **`backfill_all_account_anchor_postings` reconciled 7 accounts and wrote NOTHING: 317 journal entries / 641 postings before and after, trial balance `$0.00`** |
| CI on PR #76 | **PASS -- 7,756 passed / 0 failed** on the run that merged (`30759813495`, after X-af landed), and on an earlier attempt (7,728 plus the 28 `docker` tests a local run deselects).  The FIRST attempt was RED and the diagnosis is below |

**CI's first attempt failed, and it was NOT this step -- measured rather than assumed.** Run
`30730598715` came back `2 failed, 7754 passed`, both
`subprocess.TimeoutExpired: 'scripts/seed_user.py' timed out after 20 seconds`
(`test_seed_redaction.py::...::test_seed_user_does_not_log_email_on_first_run` and
`test_seed_user.py::...::test_subprocess_does_not_print_password_after_seed`). **This step IS on
that path** -- `auth_service.register_user:724` calls `account_service.create_account`, which posts
the origination anchor through the walk step 3 rewrote -- so "unrelated" had to be measured. Timed
against a `git worktree` at `main`, same database, same invocation:

| test | this branch | `main` |
|---|---|---|
| `test_seed_user_does_not_log_email_on_first_run` | 1.37s | 1.35s |
| `test_subprocess_does_not_print_password_after_seed` | 1.31s | 1.31s |

**No cost delta, against a 20-second budget.** Two further facts rule out a slow runner: the red
run's pytest was FASTER than the last green one on `main` (1080s against 1388s), and the re-run of
the identical commit passed all 7,756. What remains is contention, and it is citable: `pytest.ini`'s
own comment records that `-n 12` was tuned "on a 24-core host", and CI runs that same `-n 12` on an
`ubuntu-latest` runner, so a test that spawns a fresh interpreter plus `create_app()` and then
asserts a hard wall-clock ceiling is measuring the runner. **Recorded rather than dismissed** (rule
4): the finding is the instrument, not this step, and it will fire again on some future PR.

The clone is a fresh read-only `pg_dump` of production restored into a throwaway database on
`shekel-dev-db` at `d7c1f4a9e603` (the deployed head), never written back. The ledger check is the
sharper of the two: production's corrections were written by the OLD walk, so any disagreement
would have surfaced as a reconcile-to-target delta rather than as a rendered figure.

> **This row first quoted `resync_all_cash_postings` reporting `(0, 0)` changed, and an adversarial
> review showed that instrument cannot observe the claim**: that function re-posts transaction and
> transfer legs and never calls `walk_account_ledger`, so a `(0, 0)` from it is consistent with the
> anchor walk being entirely broken. The evidence that DOES bear on it is the anchor backfill --
> which routes through the changed walk for every non-loan account -- writing nothing, plus the
> entry/posting counts and the closed trial balance either side of it. Same conclusion, correct
> instrument. Recorded rather than silently swapped, because citing an instrument that cannot see
> the thing it is offered as evidence for is the shape Section 8 exists to catch.

**Negative controls, each planted and then reverted.**

- **`covers` changed from `<=` to `<`** (an assertion no longer closes its own day):
  **35 tests fail**, spanning `test_cash_walk`, `test_posting_service`,
  `test_posting_ledger_account_anchor_reconciliation`, `test_cash_fold`, `test_grid` and
  `test_cross_page_balance_equality`. One edit reaching both walks, the reservation, the grid and
  the cross-page identity is the convergence proving itself -- before this step the same edit would
  have broken one site's tests.
- **A hand-written `__ge__` on the boundary**: `TestTheRuleCannotBeAskedAnyOtherWay` fails and
  names the two spellings that started working.
- **A hand-written `__le__` ALONE: escaped the first draft of that test entirely.** The review
  planted it and all six pinned spellings still raised, because Python reaches `__le__` and
  `__ge__` by reflection and the control pinned one direction of a symmetric operator. The
  orderings are now eleven -- both operand orders of all four comparisons, plus `sorted`, `max` and
  `min` -- and the one-sided mutant fails with `['boundary <= day', 'day >= boundary']` named. **A
  control that pins half a rule is the blind-test shape a fifth time**, and it was in the very test
  written to stop it.
- **`@dataclass(order=True)`: DOES NOT reopen the hole, and the first draft of the test claimed it
  did.** The generated dunders compare the same class only and return `NotImplemented` against a
  `date`, so every spelling still raises. The test's stated threat model was corrected to the
  mutant that actually fires. Recorded because it is this arc's own lesson -- a control whose
  threat model is assumed rather than measured is the blind-test shape of findings N-132, F2 and
  R8, arriving a fourth time.

### 14.5 The three adversarial reviews, and the one finding a measurement refuted

Three neutral reviewers ran against the branch before it was committed: the project's
`code-reviewer` (correctness, IDOR, Decimal), a test-integrity auditor (mutation-testing every new
and changed control), and a design reviewer (attacking the fence itself). **Between them they found
one live defect, one blind control, one mis-cited instrument and four overstated claims -- all
fixed above and in 14.1 / 14.3 / 14.4.**

What they confirmed clean: the two-pointer merge is exactly equivalent to the deleted stable sort
(one reviewer's 20,000 randomized cases and this step's own exhaustive 1,225-case enumeration, both
0 mismatches); the dropped terminal `running` is unobservable (no consumer of either walk reads
it); `_sync.py` is behaviourally identical, including the `None` short-circuit; ownership and IDOR
unchanged on both changed routes; no `float` anywhere in the diff.

**One finding did NOT survive measurement, and it is recorded rather than quietly dropped.** The
design review rated the case-only pair `ReconciledThrough` (class) / `reconciled_through`
(function) a SILENT failure: passing a date to the function was said to run
`WHERE account_id = <date>`, return `None`, and reconcile nothing -- every envelope holding its
full budget, the projection reading low in the plausible direction. Run against the clone, it
raises: `ProgrammingError: operator does not exist: integer = date`. **It fails LOUD**, so the
naming pair is a readability question and not a correctness one, and no code changed for it.

**The test-integrity audit found THREE mutants that survived the whole suite as it then
stood (7,726 tests; it is 7,728 now, and two of the step's four additions are these findings'
controls)**, and each is now a test with its own control. They are recorded individually because
two of them are this step's own doing:

| mutant that survived | what it costs | closed by |
|---|---|---|
| `settled_cash_facts` sorted by `transaction_id` instead of `(settled_on, transaction_id)` | **This step made the loader's sort load-bearing for the first time** by deleting the merge's defensive re-sort, and nothing tested it. A monotonic pointer over a list not non-decreasing in its day HALTS at the first out-of-order row: a purchase entered late (higher id, earlier `paid_at`) is never absorbed and is subtracted a second time. **That is `-$4,001.42`'s shape reached by a different route.** | `TestTheSourceOrderIsLoadBearing`, which fails at the MONEY (`$1,000.00` against a hand-computed `$900.00`) rather than at the sort |
| the account scope deleted from `reconciled_through`'s `MAX(observed_on)` | It becomes the MAX over every account of every user. The reconcile panel uses that day as an SQL bound **and stamps it onto every ticked purchase as its posting day**, so a savings account trued up today would empty a checking envelope's reservation and write the wrong `settled_on`. Invisible because every fixture in the class held one asserted account | `test_the_sql_form_answers_for_ONE_account`, which fails showing `2026-09-09` for an account that asserted `2026-03-01` |
| `covers(earliest)` unboxed to `earliest <= boundary.observed_day` | Nothing: semantically identical. Recorded because it is Finding 3 above made concrete -- the escape hatch unboxes in one token at the site that carried N-133 / F4, and no gate reports it | nothing; it is the stated limit, not a defect |

**And a FOURTH: `test_they_agree_when_the_business_day_defies_the_recording_order` was BLIND.**
Its docstring claims an implementation ordering by `created_at` "answers 2026-02-15 here"; it did
not, because `append_balance_assertion` derived `observed_on` AND stamped `created_at` from the
same argument, so the two clocks agreed row for row and the business day never defied anything.
The helper now takes `recorded_at` separately, the back-fill row is recorded six weeks after the
day it is true for, and the recording-order mutant fails the test. **This is N-132 / F2 / R8's
shape a fourth time, guarding the exact regression the retired third statement had** -- and it was
a fixture DEFAULT that disarmed it, not a wrong offset.

**The fence's limits are now stated at the fence** (`ReconciledThrough`'s own docstring) rather
than only here: the type fences the derived accessor, a checker would fence the bare
`CashAnchorFact.observed_on` / `CashSourceFact.settled_on` fields, and **the two are complementary
rather than substitutes.** The developer's ruling was "structural over detector", which stands; the
claim that a checker *could not have worked* did not, and is withdrawn in 14.1.

### 14.6 What this step does NOT close

- **The two EVENT representations survive.** The read walk folds transaction rows; the posting walk
  folds the posted copy of the same events. One rule, two source sets. **X-d is what deletes the
  second**, and ruling R-H already ruled it -- *"the posting writer consumes the SAME walk, so the
  projection and the posted ledger cannot drift by construction rather than by a test keeping two
  implementations in step"*. The two absorb loops are now textually the same, which is deliberate:
  it makes X-d a deletion.
- **The two FACT FIELDS are still bare `date`s**, so `x <= fact.observed_on` compiles in a new
  module -- the shape a checker would catch and this type does not (14.5). **Ruled 2026-08-01:
  wrap them, SEQUENCED AT X-d**, whose entry in `README.md` now carries it as an explicit
  obligation. The measurement behind the sequencing: after this step every remaining read of the
  two is a legitimate raw-date use, the wrap needs two distinct types rather than one, and X-d
  deletes one of the two consumers.
- **`ledger_report_service/_attribution.py`'s two date loaders** still duplicate
  `account_posting_service/_walk.py`'s, behind a `# pylint: disable=duplicate-code` whose rationale
  named step 3 as its resolver. It is re-owned by **X-d**: extracting a third shared home now would
  be scaffolding for a caller X-d deletes.
- **X-d's ship gate is MEASURED and CLEAN, which is the one thing this step could usefully do for
  it.** Its entry requires a production sweep for walk-invisible legacy rows. Run on the same
  clone, in both directions:

  | sweep | result |
  |---|---|
  | entries with BOTH concrete FKs NULL and a non-correction source kind (the `_residue_source_days` bucket) | **0 entries** |
  | entries whose `transaction_id` resolves to a MISSING, soft-deleted, or non-contributing (`Projected` / `Credit` / `Cancelled`) row -- what the posted ledger carries and a source-row walk filters out | **0 entries** |

  Positive-controlled, because a zero from a hand-written query is worth nothing on its own: the
  same joins return **170** transaction-linked entries, **19** transfer-linked and **128** with
  both FKs NULL (every one of the 128 a correction, which is why the residue sweep excludes that
  kind). So **no F1-class human decision is waiting for X-d on today's data.** That is a fact about
  the data, not about the mechanism: the reverse-before-delete discipline has held, and X-d must
  still decide whether the residue arm's defence moves to the checked-projection assert or is
  ceded.
