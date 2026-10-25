> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase X as built: X-c2c4 through X-f1e3 (the X-f1 cluster, complete)

**Read-only history. Nothing here governs work.** The live document is `../README.md`. This record
was extracted 2026-08-04, when that document stood at 6,688 lines and Section 9 rule 4 was rewritten
into a 1,000-line cap with a gate behind it. **Extended 2026-08-05** with the X-f1 cluster
(X-f1a .. X-f1e3, complete), when the live document reached 1,195 of its 1,200-line cap -- the second time
rule 4 bound, and the same answer both times: condense what SHIPPED, never trim what remains.

**Every line here is one step, its commit, and what it closed. The commit message is the step's own
account of itself and every hash below was verified against `git log` at extraction; the narrative,
the measurements and the review residue that used to sit in the plan document are NOT reproduced.**
That is deliberate. This arc repeatedly carried a claim into an as-built record that the code later
contradicted -- an invented provenance line, a count that drifted, a citation to a deleted producer
-- and prose nobody re-verifies is worse than a hash anyone can check. **When you need the detail,
read the commit.**

Two sibling records hold the earlier spans: `loan_arc_as_built_2026-07-26.md` (Phases A-F, PR #64)
and `cash_arc_as_built_2026-07-27.md` (X-a .. X-g3b, PR #65). Open questions from any span live in
`../README.md` Section 6, never here.

## 1. The steps

All of X-c2c4 .. X-z shipped to production in **PR #65** (merge `69a527cd`). X-ae, X-af and X-aj1
shipped in their own PRs, noted per row. X-f1b is committed on `feat/xf1-settle-day` and is not yet
merged.

| step | commit(s) | what the commit says it did | findings closed | note |
|---|---|---|---|---|
| **X-c2c4** | `17c57cde` | refactor(balance): the modelled and cash producers delete | -- | The last cash producer. Shipped INSIDE X-g4b by ruling R-AR; no commit of its own |
| **X-g1** | `17ead4c5` | feat(balance): a modelled asset is an event stream | -- | Additive and unwired: the modelled replay beside the incumbent |
| **X-g2** | `560b3339` | refactor(balance): a modelled asset is one event stream | N-71, N-74, N-75 | The cutover. One event replay for all five kinds |
| **X-g3** | `920366a9` | feat(balance): the grid renders the modelled balance | -- | The grid renders the modelled balance; R-K's identity holds for all five kinds |
| **X-g4a** | `2ee817b4` | test(balance): the drift oracle walks 52 periods of the fold | -- | The drift oracle walks 52 periods. ADDITIVE: `git diff app/` empty |
| **X-g4b** | `17c57cde` | refactor(balance): the modelled and cash producers delete | N-43, N-46, N-78, N-95 | 1,347 production and 4,937 test lines removed; baseline byte-identical on both databases |
| **X-o** | `68c22fa0` | fix(savings): the debt-line question uses the debt-line predicate | B-16 |  |
| **X-q1** | `3b7823e1` | fix(savings): one debt-free date, one derivation | N-98 | One `LoanPayoffOutlook`, both surfaces read it |
| **X-q3** | `bad97e6a` | feat(savings): the payoff caption says what it measures | N-99 |  |
| **X-q2** | `be6cfae6` | refactor(savings): the horizon publishes what the page reads | N-100, N-102 | Rendered payload BYTE-IDENTICAL on both databases |
| **X-r** | `1204a99e` | refactor(savings): the projection dict carries the seam's figures | N-101 | No figure moved |
| **X-h** | `6337606e` + `7d61c67f` + `8e739298` + `86c38e28` + `6b1373ab` + `cd002872` | test(balance): the debt-track guard asserts through the builder | B-17, N-45, N-65, N-94 | No production change. The last two commits are the plan gate; `cd002872` moved it from `tests/` to `tools/` so editing the ledger is what runs it |
| **X-s** | `bbdfc2c0` | refactor(savings): the payload and the debt summary publish what is read | N-104, N-105, N-106 | Three leaves, one commit: the production files split cleanly and their tests did not |
| **X-t** | `db1e45a4` + `b3ff3343` + `709cda23` + `21893ec5` + `d4e0d4e7` | refactor(savings): the per-account projection is a value object | N-107, N-108, N-110, N-111 | No figure moves, on a harness built for this step because the seam one is blind above the seam |
| **X-u** | `70c5cf39` + `e2cdc589` | docs(balance): rule X-u's four forks, open N-115, gate the ledger's own count | N-109 | Opened N-115 |
| **X-v** | `7d4e4986` + `dbf154c7` | docs(balance): X-v's rulings -- one named exception answers a state the app cannot produce | N-112, N-113 | Opened N-116, N-117. 18 display-tier guards deleted |
| **X-w** | `03272174` + `38f8d879` | docs(balance): X-w's rulings -- the second per-account container, and the six others | N-114 | Seven commits; the two listed are the rulings and the review residue. Opened N-118 |
| **X-aa** | `5c2ba585` + `c10d5d12` | docs(balance): R-CO -- the two records X-w reported and gave to nobody | N-119 | Opened by the developer's own question |
| **X-z** | `b6b1446e` + `5e77d0db` | docs(balance): R-CP..R-CS -- X-z's four forks, all as recommended | N-118, N-120 | Ten commits; the two listed are the rulings and the review residue. Classifier calls per render ~488 -> 8. Opened N-121, N-122 |
| **X-ae** | `cbca7eed` | fix(app): a submitted digit string is parsed, not predicated | N-136, N-140, N-141, N-143 | PR #79, merge `a778703f`. Shipped WIDER than scoped: two reviews proved its central claim false. Opened N-139, N-142 |
| **X-af** | `209e8b6c` | test(periods): the fixtures build their window on the USER's clock | N-137 (merge-gate half) | PR #77, merge `dbee3812`. Test-only. Opened N-138 |
| **X-aj1** | `1688f508` + `63514efc` + `1e75d0ce` | refactor(transfers): restore validates before it mutates | N-146 | PR #80, merge `dde107f6`. `transfer_service.py` 999 -> 987. Opened N-149..N-152 |
| **X-f1b** | `51679384` | feat(transactions): a settle carries the day the money moved | N-179, N-182, N-183 | Figure-neutral by construction. Opened N-180, N-181, N-184, N-185 |

### 1a. The X-f1 cluster (added 2026-08-05)

**The cluster is COMPLETE and IN PRODUCTION as of 2026-08-05** -- PR #82 into `dev`, PR #83 into
`main`, merge `8d812662`, image `2ab54855937b`, migration head `b5e3d9c1a7f2`. All fourteen leaves
shipped, and the parent **X-f1** is ticked in `../README.md`, where two invariants it established
stay live because later steps read against them. Hashes re-verified against `git log` at extraction;
`feat/xf1-settle-day` was deleted once it merged. **X-f1 did NOT close N-173** -- its backfill was
the deleted derivation verbatim, so no stored date improved and that row now belongs to X-f6.

**The deploy was rehearsed on a restored copy of production before it ran, and was FORWARD-ONLY**:
four of the five migrations drop columns, `a3f7c8e21b64.downgrade()` refuses by design, and
`shekel-deploy.sh` reverts only the image digest. Measured on the rehearsal and again on production
afterwards: migrations clean `d7c1f4a9e603` -> `b5e3d9c1a7f2`, the settled-iff-dated invariant exact
at 158/158/0, the backfill accounted 999/999 (150 from `paid_at`, 8 from the period-start fallback,
841 NULL, 0 unexpected), and the seam harness byte-identical across the change with a positive
control moving 1,674 lines.

| step | commit(s) | what the commit says it did | findings closed | note |
|---|---|---|---|---|
| **X-f1a** | `1ca63972` | docs(balance): X-f1's three forks are ruled, and the reviews refuted three claims | -- | Rulings R-EC / R-ED / R-EE, before any code cited them |
| **X-f1b0** | `70ba87f6` (folded into `51679384`) + `0b04f255` | fix: a re-settle does not re-date the money | N-178 | **Never shipped as its own commit**, so the revertability the plan claimed for it was false. Cited at three sites in the code |
| **X-f1c1** | `0b04f255` | feat(transactions): the settle day is editable on a settled row | N-184, N-186, N-189 | Ruling R-ED, plus R-EG's shared `settle_day_for_status`, R-EJ's future-day refusal, R-EL's lower bound. Preceded by the module split its 1000-line ceiling forced. **Hash recovered at extraction -- the plan never recorded it** |
| **X-f1c2** | `7e9f261a` | feat(transfers): the settle day is editable on a settled transfer | N-181 | Ruling R-EF. `Transfer.settled_on` as a read-only property over the INCOME shadow; no setter, so the seam stays the single writer structurally. **Hash recovered at extraction** |
| **X-f1c3a** | `c16bdb3b` | refactor(accounts): the asserted balance has one resolver | N-73, N-83 (cache half), N-103 (premise), cash D4 | Rulings R-EM / R-EP. `resolve_anchor` loses `scenario_id`, gains a batch twin; five `IS NULL` guards on a `NOT NULL` column deleted |
| **X-f1c3b** | `379ed1af` | refactor(accounts): an assertion is a day and a balance | N-168, N-169 (cash half), N-170 | Ruling R-EO. Drops `account_anchor_history.pay_period_id`; 0 of 78 production rows rejected by the tighter key. `reset_pay_periods` stops destroying 69 real observations |
| **X-f1c3c** | `4b0af18b` (migration `c81f0a5b3e27`) | refactor(accounts): the anchor balance has one home, and the reconcile has a lock | N-134, N-4, N-5, N-190 | Ruling R-EH drops both anchor cache columns; R-EN removes the C-17 lock. Carries N-190's fix -- one per-USER advisory lock inside the reconcile, all four entry points. Opened **N-191**, **N-192**, **N-193**. **The commit's subject differs from the title the plan predicted** (`drop the anchor cache columns`) |
| **X-f1c4a** | `380d44dd` | docs(balance): the duplicate rule is designed, not re-keyed a fourth time | -- | Ruling R-EQ, with R-EE / R-EI restated onto the leaves that build them |
| **X-f1c4b** | `22e472e6` (migration `a3f6c1d84b90`) | refactor(anchors): an assertion is refused only when it changes nothing | N-194 | Ruling R-EQ on BOTH anchor tables in one commit. Drops both content-keyed unique indexes. The horizon correction -- compare against the assertion governing the SUBMITTED DAY, not the account's latest -- came from three adversarial reviews; all four lock mutants had survived the suite before its tests existed. Opened **N-195**, **N-196** |
| **X-f1c4c** | `5fc22bba` | feat(accounts): a true-up carries the statement day it was read from | N-187 is NOT among these -- see X-an | Rulings R-EE / R-EI / R-ER. Fixed two live defects the plan had not named: an unrenderable refusal, and a reconcile prompt decoupled from the coverage boundary releasing `$120.00` of projected checking. 16 mutants planted and killed. Opened **N-197**, **N-198**, **N-199** |
| **X-f1d** | -- | the archive move (N-175) | N-175 | No commit of its own: done at the 2026-08-04 trim, when `anchor_settle_partition.md` moved here |
| **X-f1e1** | `677bb397` | refactor(accounts): a balance is asserted at ONE door | N-195 | The account EDIT page stops asserting balances, in four layers (template, schema, route branch, kind gate) so none is a fence. Measured refutation of the two reviews that recommended ALIGNING the door: with its gate deleted, a RENAME moves the coverage boundary two months. Baselines byte-identical, positive-controlled; four controls shown to fail on revert |
| **X-f1e2** | `bf15ed0c` (migration `b5e3d9c1a7f2`) | refactor(accounts): an assertion is (account, day, balance), written at one door | N-198 | Ruling R-ES. Drops `account_anchor_history.notes`; the origination assertion routes through `stage_anchor_true_up`, so the table has ONE writer. Both reviews found the same defect the ruling did not anticipate: unifying the writers made a clock-dependent day rule run TWICE, so a midnight roll between the two applications refused the day the first produced. Fixed with `ObservationDay`, a type only the resolver mints -- its field is `civil_day` because `date.day` silently satisfied `.day` with a day-of-month integer. Opened **N-200**, **N-201**, **N-202** |
| **X-f1e3** | `993532db` | fix(accounts): the back-dated acknowledgement reaches every surface | N-199 | Ruling **R-ET**. The acknowledgement moves off the per-surface `#anchor-as-of` caption onto `#anchor-ack-mount`, a `base.html` toast mount no refresh region owns -- reaching 5 surfaces where it reached 1. The as-of snippet narrows to the GRID alone (the only caption nothing else redraws), the route's HTML f-strings become partials, and the prompt/acknowledgement exclusion becomes one `if/else`. **Both kind refusals stop being raw 4xx AND the affordance behind them is deleted**: the shared partial renders a loan's balance read-only, the rule the cockpit already followed and the other four surfaces did not. Two adversarial reviews; the code review's mutation experiment proved the first test set blind to the two attributes the feature hangs on. Live-verified in Chromium on 3 surfaces. Opened **N-203**..**N-206** |

**The three DECOMPOSITION PARENTS have no commit of their own and ticked with their last leaf**,
and they are listed because the live document still cites them by id: **X-f1c** (the edit doors --
c1, c2, c3, c4; the re-scope was itself the finding, since as one leaf it was measured wrong three
ways), **X-f1c3** (the anchor's one home -- c3a, c3b, c3c; the census was 65 references over 24
`app/` modules and 5 templates, and substituting moved no figure on 9 of 9 production accounts),
and **X-f1c4** (the statement day and the duplicate rule -- c4a, c4b, c4c, one SESSION each).
**X-f1c's ordering was load-bearing: c3 ran BEFORE c4**, so back-dating never met the anchor cache.

### 1b. The X-an cluster (added 2026-08-09)

**COMPLETE at two leaves, merged to `dev` and NOT yet PR'd to `main`**, so CI has graded neither.
The parent **X-an** stayed ticked in `../README.md` because live sentences cite it by id; the two
leaves left the index with their specifications, the X-f1 precedent one cluster over.
**AMENDED 2026-08-13**: the parent left the index too, and its own line is in
`x_f2_reconciliation_as_built_2026-08-12.md`'s extension -- an id citation resolves through the
archive (conventions rule 10), which is what the condition above was really protecting.

| step | commit | what it did | findings |
|---|---|---|---|
| **X-an-a** | `3d3f0ef5` | The CUT: the loan resolver's replay/projection boundary moves off the pay-period start onto `settled_on`, through ONE `app.utils.dates.has_settled_by` both halves call, and `PaymentRecord.is_confirmed` stops being stored beside the day. Measured per-DAY on the prod clone (`tests/manual/verify_loan_daily_figures.py`, `d0adf188`): no balance moved and a **$1,910.95** installment came back into a schedule it had been missing from for 12 days. **The exhaustive seam harness was BLIND to it by construction** -- it samples the default `as_of` plus six fixed dates while the change moves an answer only in 1-12 day windows -- and came back byte-identical, proving nothing | closed **N-187**; opened **N-207** |
| **X-an-b** | `549015c0` | The anchor TIE-BREAK: a loan's anchors were loaded by one function and reduced by TWO consumers, each breaking a tie its own way over a list with no `ORDER BY` (the walk reset on the LAST of a tie, `select_latest_anchor`'s `max()` took the FIRST). It moves POSTED money -- `_anchor_correction_targets` sums same-day legs, so the sum telescopes to the walk's anchor while the resolver seeds from the other. Production `$0.00`: four rows share the backfill instant, none share an `anchor_date`. **The fix is ONE key, not an `id` term added to three orderings**: `app.utils.dates.anchor_chronology_key`. Two ORM `order_by=` arguments DELETED rather than corrected; the fifth site was found by CENSUS, not by the step's list. `43fdf325` carries the separate `_opening_anchor_fact` correction the same trace found | closed **N-196**; opened **N-208**, **N-209**, **N-210**, **N-211** |

**The live lesson, kept because it is cited**: X-an-b's first draft added the missing `id` term to
each ordering and justified the two surviving spellings by citing a cash test that pins
`resolve_anchor` against `cash_anchor_facts`. No such test exists, and the invented citation was the
tell that the shape was wrong.

**One obligation left this span and did NOT come here**: the recurrence redesign's `R6` cannot ship
with X-an (it reads `due_on`, which `R5` creates behind X-f4). That `developer-decision` is carried
by `implementation_plan_recurrence_redesign.md` section 0 and by `steps.md`'s `blocked by` cell on
`recurrence:R6`, both of which state it independently of this record.

### 1c. The X-f2 cluster's first two leaves (added 2026-08-10)

**X-f2 itself is OPEN** -- `X-f2-c` is unbuilt and decomposed into three leaves of its own -- so the
parent's specification stays in `../README.md`. What comes here is the two leaves that are DONE.
Rule 5's three conditions hold: `N-205` is unfinished and stays in `ledger.md` under `X-j`; the one
live sentence either leaf carried (the seam PRESERVES an existing settle day, which `X-f2-c2`
overrides with the statement date) moved UP into `X-f2-c2`'s own specification rather than being
archived under it; and nothing below was carried without re-verification.

| step | commit(s) | what it did | findings |
|---|---|---|---|
| **X-f2-a** | `397ce36e`, preceded by the module split it forced (`69b91a53`) | The DIFFERENCE, before it is saved (**R-EU**): the anchor editor previews what the account's RECORDS produce for the day the form names, what was typed, and the gap, through `balance_at.records_balance_at`. Read-only, so no figure moved; the arithmetic and the sign's MEANING are decided in the route. Scoped to accounts with no modelled tier, which is what **N-213** records | opened **N-212**, **N-213**, **N-214** |
| **X-f2-b** | `a41b5ebf` + `7f30057a` + `a004361d` | The DURABLE record (**R-EV**): a Balance history card on the cash detail page -- as-of, recorded, ledger, CORRECTION -- on the new seam entry `balance_at.cash_anchor_history`, whose every column is a field `walk_cash_ledger(...).anchor_corrections` already published, so no producer was added. The acknowledgement re-keys onto "did the figure the user is looking at change" and its mount stops destroying a still-visible predecessor. `a004361d` is two neutral adversarial reviews' residue, and its top finding is the live lesson below | closed **N-204**, **N-206**; **N-205** NARROWED to the modelled kinds and re-pointed to **X-j** |

**Three lessons this cluster paid for, kept because they are cited.** (i) One WORD covered two rules:
the card's `recorded - balance_before` and the true-up preview's `recorded - records_balance_at(day)`
were both labelled "Difference" on one page and diverge on any day carrying more than one assertion
-- 3 of the real Checking account's ~50 -- so the column is `Correction` now and the figures never
moved. (ii) **The pytest suite structurally cannot see a JS visibility defect**: `beforeend:` put the
acknowledgement outside the element its auto-show handler is scoped to, so it reached the DOM and
stayed INVISIBLE with 8,556 tests green; `tests/manual/verify_anchor_ack.py` is the probe that saw
it. (iii) Bootstrap's instance map is a `Map`, not a WeakMap, so any component on an element an htmx
swap destroys is retained: 5 expand-then-refresh cycles of the card retained **4,592 DOM nodes**,
measured by CDP `Performance.getMetrics` with a forced GC, and `app.js` now disposes on
`htmx:beforeSwap`.

**Two defects this cluster REPORTED and did not fix**, each wanting its own step and each recorded
where it will be read: `loan_posting_service.loan_balance_anchor_history` and
`balance_at.cash_anchor_history` are two statements of ONE rule, and the fence registry rules that
same column two opposite ways; and `is_opening` is POSITIONAL, so an assertion back-dated before an
account's origination takes the "Opening" badge and the real origination loses it -- which is
**N-200**, owned by `X-f3`.

## 2. Findings closed outside a Phase X step

| finding | closed by | note |
|---|---|---|
| **N-130** | S1-a + S1-b, PR #67 (merge `fd0ddfab`), in production 2026-07-31 | The anchor/settle partition was decided by CLICK ORDER and cost `$4,001.42` on production: the grid rendered `-$4,021.37` against a hand-computed `-$19.95`. Ruling R-DH. Verified on a fresh clone: current period `-$19.95`, all nine past period ends land on an asserted balance |
| **N-131** | `92879e86`, its own PR | The cross-page locks were a month-end TIME BOMB, red on `main` at an unmodified commit and firing ~12 days a year |
| **N-132** | closed at the step, by day-offset conversion with the reason recorded at each site | Fixtures separated by calendar arithmetic stopped testing the case they name |
| **N-133** | `fix/n133-review-residue`, shipped in PR #68 | **The one row in this arc that a MEASUREMENT contradicted a ruling on.** R-DH (a)'s opening amendment was made mid-build on a hypothetical and never re-scored; it was 3.2x worse on the net plug than the rule the ruling's own table scores. Ruled: revert the amendment and date the opening. This is where Section 8's "score the rule you shipped" lesson was paid for |
| **N-137** (merge-gate half) | X-af, PR #77 | The app half is still open and is **N-138**'s, in the live ledger |
| **N-146** | X-aj1 commit `63514efc` | A notes-only save on a PAID transfer moved its money forward to today, through the ordinary UI path. Reproduced at `HEAD` before it was written down |

## 3. Rulings

**One line each: the RULE.** These governed work that has shipped, so a live sentence in
`../README.md` no longer depends on any of them (rule 5). Rulings that still govern remaining work
stayed in that document's Section 4. Where a headline reads thin, the ruling's full deliberation is
in the plan document's git history at the commit that recorded it.

| ruling | date | what was ruled |
|---|---|---|
| **R-B** | -- | The cash projection counts a settled transaction iff `COALESCE(paid_at, period start)` is after the latest anchor's `created_at` -- SHARED with the posting walk's existing rule, never copied |
| **R-F** | -- | Phase X ships FOLD-FIRST, not partition-patch-first |
| **R-H** | -- | ONE walk, designed for both consumers from the start |
| **R-J** | 2026-07-25 | A loan is refused at the SOURCE; the cash producers stay TOTAL and kind-blind |
| **R-M** | 2026-07-25, amended 2026-08-01 | The column SPLITS rather than the guard bending: `purchased_on` is the day of purchase, `settled_on` the day the bank was seen to take it |
| **R-P** | 2026-07-26 | Every surface that renders the subtotal figures renders ruling R-O's row, on R-O's own non-zero rule |
| **R-R** | 2026-07-26 | A contribution is partitioned by SOURCE, so the two feeds are disjoint BY CONSTRUCTION and there is no de-dup rule to get wrong |
| **R-V** | 2026-07-26 | Plan step X-c2c3 is CANCELLED; X-g replaces the modeled bases outright, and no compensator ships in the meantime |
| **R-W** | 2026-07-26 | The grid renders the MODELED balance, with a "Growth" row that is the accrual producer's own answer -- ruling R-K's identity then holds for all FIVE kinds |
| **R-AA** | 2026-07-26 | X-g2 ships in TWO commits: X-g2a the SHAPE, byte-identical, then X-g2b THE cutover |
| **R-AB** | 2026-07-26 | The forward-projection SEED becomes a DATE read at `window_start - 1 day`, and both `current_period_transfer_contribution` subtractions DELETE |
| **R-AC** | 2026-07-26 | The growth chip becomes a sum over the replay's ACCRUAL and CONTRIBUTION tiers, and hides ONLY with no investment params or no current period |
| **R-AD** | 2026-07-26 | The kernel's per-kind ladder COLLAPSES at X-g2b; the producers it replaces stay dead until X-g4 |
| **R-AE** | 2026-07-27 | The forward-projection SEED is the modelled balance at the day before the window, with NOTHING filtered out -- `asset_seed_at` DELETES and `investment_seed_map` leaves with no successor at all |
| **R-AH** | 2026-07-27 | The grid renders the two modelled tiers as TWO conditional rows, on ruling R-O's own non-zero rule and on both form factors (R-P) |
| **R-AI** | 2026-07-27 | The accrual row's label is PER KIND, resolved in the ROUTE from `classify_account`; the contribution row is unconditionally "Contributions" |
| **R-AJ** | 2026-07-27 | `grid_balance_view` assembles the account's REAL `ContributionInputs`, its kind GATE is deleted, and `GridColumn.interest` becomes a non-optional `accrual` beside a new `contribution` |
| **R-AL** | 2026-07-27 | The accrual row's CSS marker renames kind-neutral: `interest-row` -> `modelled-accrual-row`, and the new row is `modelled-contribution-row` |
| **R-AM** | 2026-07-27 | The accrual figure's sign treatment is THREE-WAY -- `> 0` green with an explicit `+`, `< 0` the danger token, `== 0` neither -- and NOT `/investment`'s `>= 0` |
| **R-AN** | 2026-07-27 | The two MODELLED rows render CENTS; the four rows around them stay whole-dollar |
| **R-AR** | 2026-07-27 | X-g4 and X-c2c4 ship as TWO commits: X-g4a the PORT, then X-g4b the WHOLE deletion |
| **R-AS** | 2026-07-27 | `_interest.py` DELETES WHOLE and its one surviving predicate folds into its one caller, which removes a redundant second `classify_account`; ruling R-L's window history and the day-count lesson are restated in `_asset_fold`'s docstring rather than deleted with the file |
| **R-AT** | 2026-07-27 | The ported 52-period drift oracle exercises ALL THREE of the fold's tiers, not the one the original had |
| **R-AU** | 2026-07-27 | `test_asset_fold_parallel.py` KEEPS the two classes graded against something other than the dying incumbent (the grain class's `growth_engine.project_balance` comparison and the properties-of-the-replay class) and LOSES the three that are not |
| **R-AV** | 2026-07-27 | X-o ships the predicate alone; the second debt-free producer gets its OWN step, X-q, and both are built now |
| **R-AW** | 2026-07-27 | The per-account projection dict carries the seam's `LoanFigures`, and the six flat copies go -- in its OWN commit, AFTER the defect is fixed |
| **R-AX** | 2026-07-27 | "Debt-free" stays LOAN-ONLY, and the surfaces say so |
| **R-AY** | 2026-07-27 | A payoff date that is already PAST is reported, and the chart falls back |
| **R-BA** | 2026-07-27 | `compute_net_worth_horizon` DELETES, and its tests read the horizon where the route reads it |
| **R-BB** | 2026-07-27 | NO "Paid Off" badge on the archived drawer, permanently, and the drawer's WRONG NUMBER is recorded as N-103 with X-e as its owner |
| **R-BC** | 2026-07-28 | The milestone's `kind` is deleted at BOTH ends, so X-q2's mutation guard extends one level down and the payload contract stays STRUCTURAL |
| **R-BD** | 2026-07-28 | The debt summary becomes a frozen value object that CARRIES the outlook, the DTI trio collapses to ONE nullable field, and the dashboard track COMPOSES the summary instead of copying it |
| **R-BE** | 2026-07-28 | A borrower whose loans are ALL retired earns the caption "All loans paid off" on the /savings Liabilities footer, and nowhere else |
| **R-BF** | 2026-07-28 | `_project_one_account` states each of its two conditions ONCE, and the missing-baseline guard is HOISTED to cover both arms |
| **R-BG** | 2026-07-28 | `compute_net_worth_series` stops publishing `assets` and `liabilities` |
| **R-BH** | 2026-07-28 | `DtiMetrics` stores ONE fact: the ratio |
| **R-BI** | 2026-07-28 | The debt track's DOUBLE projection is recorded as finding N-109 with its own step, not folded into X-s3 |
| **R-BJ** | 2026-07-28 | The two residues X-s does not reach get ONE new step, and the `ad` dict rides with them |
| **R-BK** | 2026-07-28 | The projection becomes a frozen `AccountProjection` with a NESTED `LoanDetail \| None` |
| **R-BL** | 2026-07-28 | One predicate on the CONTEXT, plus a hoist inside the region -- not the plan's "hoist all four to the build entry" |
| **R-BM** | 2026-07-28 | Derive what can be derived, gate the rest, and delete two homes doing it |
| **R-BN** | 2026-07-28 | A milestone's LABEL is its identity, and a duplicate is a display outcome rather than a defect |
| **R-BO** | 2026-07-28 | `compute_property_equity` owns its no-baseline state, and the "two doors" claim is corrected in every docstring that made it |
| **R-BP** | 2026-07-28 | The Horizon's band literals are DERIVED, and the gate asserts the partition |
| **R-BQ** | 2026-07-28 | The gate is comment-stripped, and it carries controls on ITSELF |
| **R-BR** | 2026-07-28 | The fabricated no-baseline HERO is recorded, not changed inside a fix commit |
| **R-BS** | 2026-07-28 | `principal_paid_fraction` becomes a `DebtSummary` FIELD, set at that value object's one construction site, and the second debt producer is deleted |
| **R-BT** | 2026-07-28 | `_project_debt_accounts` is INLINED into `compute_debt_summary` and deleted |
| **R-BV** | 2026-07-28 | `/savings` does not render the new field, and X-u adds no pixel |
| **R-BW** | 2026-07-28 | The no-baseline state gets ONE answer, and it is a NAMED exception with ONE handler -- not nineteen invented values |
| **R-BX** | 2026-07-28 | `BalanceContext.scenario_id` becomes the RAISING accessor (`-> int`) so the nullable cannot escape the context: five sites dereference it to SCOPE A QUERY, and each would otherwise raise `AttributeError` on `None` -- a 500 of the wrong type, which the handler cannot answer |
| **R-BY** | 2026-07-28 | TWO guards keep their own explicit handling, and the reason is written at each |
| **R-BZ** | 2026-07-28 | X-t2's `/savings` no-baseline degradation is REVERSED, and this row is where that is findable |
| **R-CA** | 2026-07-28 | N-113 is closed by DELETION, not by inventing a display vocabulary for a state no page can now reach |
| **R-CB** | 2026-07-28 | The census instrument becomes a permanent GATE, because that is the only part of this step that cannot go stale |
| **R-CD** | 2026-07-29 | 204 answers a POLL, never a BUTTON |
| **R-CE** | 2026-07-29 | The exception carries the user it was RESOLVED for, and the event logs both ids |
| **R-CF** | 2026-07-29 | The sweep's coverage claim becomes a PINNED LIST, and it grades the fragment answer too |
| **R-CG** | 2026-07-30 | ONE per-account record per render, and the dense period map rides on it |
| **R-CI** | 2026-07-30 | Every RECORD container crossing a module boundary on this path is a value object; the four that are not records stay dicts, with the reason written |
| **R-CJ** | 2026-07-30 | A map that is TOTAL over its input is INDEXED, not defaulted |
| **R-CK** | 2026-07-30 | A total map is INDEXED at every reader, and indexing is not a policy -- it is the absence of one |
| **R-CL** | 2026-07-30 | `TrendPoint.period_index` is DELETED: an AST census found ZERO production readers, so it is finding N-100's defect inside the step that deletes `goal_mode_id` for the same reason |
| **R-CM** | 2026-07-30 | The one-record design STANDS and the INSTRUMENT is what changes |
| **R-CN** | 2026-07-30 | A type that crosses this package's boundary is re-exported, and a region's type lives with its own fields |
| **R-CO** | 2026-07-30 | Both records are typed at their producer, and the unreachable nullable X-w4 wrote goes with them |
| **R-CP** | 2026-07-30 | ONE classifier answers the CATEGORY, and both existing questions BUILD ON it |
| **R-CQ** | 2026-07-30 | The classifier's module is RENAMED to `app/services/account_category.py` and stays PUBLIC at the service layer: the classification rule is account metadata, not a cockpit display decision |
| **R-CR** | 2026-07-30 | ONE category map per render, and the Jinja spelling of the liability key is GATED |
| **R-CS** | 2026-07-30 | The three coverage units are each quantized ONCE, from the RAW ratio -- and the two inputs that cannot be `None` stop being nullable |
| **R-CT** | 2026-07-30 | The category rides on `AccountProjection`, and the per-account map X-z2 threaded is DELETED |
| **R-CV** | 2026-07-30 | Every citation, count and control the two reviews found, plus an O(1) category reverse lookup in `ref_cache` |
| **R-CW** | 2026-07-30 | The raw-ratio rule STANDS, and the shape it can render is recorded rather than discovered later |
| **R-DF** | 2026-07-31 | The hole's reconciliation defect is finding N-128 with plan step X-l as its owner, and R-CX's false sentence is corrected in the same commit |
| **R-DG** | 2026-07-31 | The whole residue is fixed and RE-REVIEWED before anything is committed; nothing ships piecemeal |
| **R-DI** | 2026-08-02 | The residue arm is DELETED, and a posting the source rows cannot explain becomes a LOUD refusal instead of a silent absorption |
| **R-DJ** | 2026-08-02 | Two distinct civil-day TYPES -- `CashAnchorFact.observed_on` becomes an `ObservedOn` and `CashSourceFact.settled_on` a `MovedOn`, each ordered against its OWN kind only -- and `ReconciledThrough.covers` narrows to the event one, which makes the line plan step 3 deleted a `TypeError` |
| **R-DL** | 2026-08-02 | The anchor reconcile resolves its two ledger accounts ONCE per account rather than once per correction, and the fix rides INSIDE X-d |
| **R-DX** | 2026-08-03 | The IDENTITY invariants go to the DATABASE tier where they CAN go |
| **R-DZ** | 2026-08-03 | R2 for anchor corrections is a KEY-SHAPE change with NO migration in it, and it ships FIRST, alone, as plan step X-ai-r |
| **R-DB** | 2026-07-31, DEEPER than recommended | Registration STOPS creating a bootstrap pay period |
| **R-EC** | 2026-08-03 | `transactions.paid_at` is REPLACED by `transactions.settled_on`, not joined by it: the column stores the CIVIL DAY and the click instant is deleted |
| **R-ED** | 2026-08-03 | The settle day is EDITABLE on a finalised row, and it is POSTING-RELEVANT |
| **R-EF** | 2026-08-03 | The settle-day door ships on BOTH forms, because the transaction door alone corrects NOTHING |
| **R-EG** | 2026-08-03 | A settle day submitted alongside a REVERT is dropped at the door, not refused |
| **R-EI** | 2026-08-03 | The true-up editor's statement date goes on a SECOND LINE |
| **R-EJ** | 2026-08-03 | A settle day in the FUTURE is refused, at the seam |
| **R-EL** | 2026-08-04 | A settle day BELOW the schedule is refused, at the same seam and by the same bound an anchor observation already uses |
| **R-EP** | 2026-08-04 | "As of when was this balance asserted" gets ONE source: the assertion's own `observed_on` |
| **R-AQ** | 2026-07-27 | There is no DEFERRED category. Every finding is owned by a step, and a wake condition is not an owner. SUPERSEDED as a rule by `conventions.md` rule 1, which grades it |
| **R-CC** | 2026-07-29 | A financial STATEMENT never reports zeros for a ledger it cannot read |
| **R-DC** | 2026-07-31 | A mid-life schedule change FILLS the hole it would leave |
| **R-DD** | 2026-07-31 | Both write-path fixes are their OWN step (X-ad), sequenced immediately after X-x. REVERSED on the ordering by **R-DE**, and the later ruling governs |
| **R-DS** | 2026-08-02 | A status repair takes the PAIR's instant, and never invents one |

## 4. What this record deliberately does not carry

* **The running-state narrative.** ~1,000 lines of "X-n is DONE" entries with their measurements,
  branch topology and deploy notes. Superseded by the commits and by `git log`.
* **Per-step measurement tables and review residue.** Each step's adversarial reviews, their
  findings, the corrections they forced and the figures behind them are in that step's commits and
  in the commit that recorded its rulings.
* **Anything already contradicted by the code.** Where the plan document's prose and the tree
  disagreed at extraction, nothing was carried forward on the prose's authority.
