> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Archive: superseded balance-architecture documents

**Read-only history. Nothing in this folder governs work.** The single live document is
`../README.md`, the CASH plan of record since 2026-07-26 -- and since 2026-08-04 "single" is
literally true again: `anchor_settle_partition.md` had become a second one, which Section 9 rule 1
prohibits, and it is now in this folder. Every file here is either a completed as-built record, a
superseded plan, or an audit whose surviving findings were absorbed into the plan of record's
findings ledger. Archived 2026-07-16, extended 2026-07-26 and 2026-08-04.

**The 2026-08-04 extraction changed what an as-built record IS, and the two earlier ones do not
follow the new rule.** `phase_x_as_built_2026-08-04.md` is CONDENSED -- one line per step, its
verified commit hash, and what it closed -- because this arc repeatedly carried a claim into an
as-built that the code later contradicted. The two records before it are verbatim moves of the plan
document's prose (211 KB and 252 KB), and the two standing warnings at the foot of this file apply
to them with full force. Section 9 rule 5 of the live document now requires the condensed form.

**Start here for the CASH half:** `phase_x_as_built_2026-08-04.md` for anything from X-c2c4
onward, and `cash_arc_as_built_2026-07-27.md` for X-a through X-g3b before it. **"Shipped" here
does not mean "deployed", and since 2026-08-10 the two NO LONGER coincide.** This sentence used to
end "though as of 2026-08-05 the two coincide for everything in these records", which stopped being
true the moment a dev-only step's ruling was archived: **`R-DB`'s remedy is `X-ad-a` (`2a4eb477`),
which is not an ancestor of `origin/main`**. Verify with `git merge-base --is-ancestor <hash>
origin/main` rather than reading this paragraph -- a stored claim about what production runs is a
derived value beside no reconciler, which is the root cause several of these records exist to
remove. Everything through Section 1 is in production (PR #65, merge `69a527cd`, and the per-step
PRs that record names), and **Section 1a's X-f1 cluster shipped too** -- PR #83, merge `8d812662`,
migration head `b5e3d9c1a7f2`. **Phase X is still IN FLIGHT**, so these are shipped-so-far records;
the remaining steps are live in `../README.md`. The earlier record also holds the plan of record's whole running-state narrative
from that span -- read it as history, and re-verify any figure in it against the code.

**Start here for the loan half:** `loan_arc_as_built_2026-07-26.md`. It is the as-built record of
Phases A-F -- the whole loan arc, shipped to production in PR #64 -- extracted verbatim from the
plan of record when that document was trimmed to the work that remains. It also holds the register
of every finding the arc CLOSED. The loan questions that are still OPEN are NOT there: they stayed
in the live findings ledger, because unfinished work belongs where work is planned.

| file | what it was | why archived |
|---|---|---|
| `anchor_settle_partition.md` | The anchor/settle partition arc, and for a while a SECOND live planning document for this arc (finding N-175, which rule 1 prohibits): ruling R-DH's six parts, steps 1-4 and S1-c as built, the F1-F12 adversarial-review register, F11's measurement, and the from-scratch redesign that was measured and REJECTED because it re-opens `-$4,001.42` by another route | Superseded 2026-08-04. Everything in it had shipped (PRs #67, #75, #76), been absorbed (S2-b into X-f1), or was already a row in the live ledger -- **except three obligations with no home there, which were carried into `../README.md` FIRST**: the uniqueness-index re-key at X-f1c4, and X-d's already-measured ship gate plus the `_attribution.py` loaders it inherits. Read it for how the day partition was decided; do not read it as instructions |
| `phase_x_as_built_2026-08-04.md` | Phase X from **X-c2c4** to **X-f1e3**, CONDENSED: one row per step (id, verified commit hash, the commit's own subject, findings closed), the six findings closed outside a step, and the 79 rulings whose work has shipped | Extracted 2026-08-04, when the live document stood at **6,688 lines**. Section 9 rule 4 became a hard 1,000-line cap with a gate behind it, and rule 5 became "archived whole and CONDENSED, not moved verbatim". Read it to find WHICH COMMIT shipped a behaviour; read the commit for why. **Extended 2026-08-05** with Section 1a, the X-f1 cluster's fourteen leaves (complete, and in production at merge `8d812662`), when the live document hit 1,195 of its 1,200-line cap -- the second time rule 4 bound |
| `x_f2_reconciliation_as_built_2026-08-12.md` | The X-f2 span (the true-up is a reconciliation): its five leaves, the settle doors and the amount cache beside them (X-aq, X-as, X-ap), the panel's sizing measurement, and the two rules a later step must obey | Condensed 2026-08-12 under rule 5. **Extended 2026-08-13** with **X-an**'s own parent line when the X-f3 decomposition took the live document to its cap and rule 5 archived the rest of this span; X-an's two leaves and X-f1's fourteen are in `phase_x_as_built_2026-08-04.md` sections 1a / 1b, and every other archived step of the span already had a row here |
| `x_f3_clearing_as_built_2026-08-16.md` | `X-f3a-1` and `X-f3d` as built: clearing became a recorded FACT, and a true-up got a name | Extracted 2026-08-16 at `bank_import:X-f6a-1` under rule 5, when that step's three index rows took `steps.md` to 241 lines against the 20-line headroom its 260-line cap required. **This row was itself missing until 2026-08-26** -- the file was archived and never indexed, which is finding **N-357**'s reverse arm: nothing grades an archived file for being named by its own index, because an unindexed file is cited by nobody |
| `x_i_binding_as_built_2026-08-26.md` | The one-read-pass BOUNDARY and BINDING, as built: `X-i3-a` (`765daebd`) and `X-i4` (`79a1730c`) | Archived 2026-08-26 under rule 5, the day each shipped, because the live document stood at its cap again and `X-i4` opened four findings and two steps that needed the room. Both are LEAVES of `balance:X-i`, whose other five are live and specified in `../README.md`; each keeps a one-line pointer there per rule 7, and every obligation either leaves a live step is RESTATED at that step -- `X-i3-a`'s "a command's reads may not be snapshotted" at `X-i3-b` and `X-i5`, `X-i4`'s "a pass is a memo whose lifetime is one read" at `X-i5`. What is here is the measurement, not a rule |
| `x_f3c_2c_as_built_2026-08-30.md` | `X-f3c-2c` and its two leaves as built: an assertion is append-only at the DATABASE tier, on all three account-history tables | Archived 2026-08-30, the day it shipped (`930f06fc`, PR #161 at `0a7c2aef`), under rule 5 -- the live document stood at **980 of its 1,000-line cap with a 20-line headroom floor, zero room**, and `X-be`'s specification needed six lines for a measurement that arrived after the step merged. Carries what a LATER step must obey: the three controls beneath the trigger are reached past and never deleted, and a migration rewriting any of the three tables drops the guard and re-applies |
| `x_bi_6a_as_built_2026-09-15.md` | `X-bi-6a` as built: every projected-shadow reader re-pointed onto the parent transfer's derived legs (R-BAL13, R-BAL38), the five readers and what each became, the `$0.00` measurement, the status-drift finding the review refuted and the ruling on it | Written 2026-09-15, the day it shipped, so the live README's entry could be its one-line pointer: the README stood at 1,306 of its 1,310 ceiling |
| `x_bj_1a_as_built_2026-09-16.md` | `X-bj-1a` as built: `budget.account_anchor_history` became the ONE level relation (the bank's placements moved in, a release became an appended row in `budget.anchor_releases`), the ten rulings R-BAL47 to R-BAL57 and why each, every reader and what it became, the `$0.00` measurement and its mutation control, what the two neutral reviews found, and what a later step must obey | Written 2026-09-16, the day it shipped (`1d1f7ce8`), so the live README's entry could be its one-line pointer |
| `x_cv_as_built_2026-09-23.md` | `X-cv` as built: a deploy is all-or-nothing -- its four leaves and five commits, what a failed deploy costs now, the two findings closed that were never rows (BAL-535, BAL-536), and the README entry and `steps.md` row as they stood | Written 2026-09-23 at the tick, so the live README's entry could be its pointer |
| `eight_shipped_steps_2026-09-01.md` | Eight shipped steps, verbatim: `X-f3c-2b-2a`, `X-f3c-2b-2b`, `X-au-c3`, `X-au-g-1`, `X-bh-1`, `X-bh-2`, `X-ad-a` and `X-be-3` | Archived 2026-09-01 under rule 5 to buy the room `X-au-g-2`'s decomposition needed -- one step became three, each owing a specification under rule 12, against a live document at **978 of its 1,000-line cap with a 20-line headroom floor**. The THIRD such cut in six days, which is finding **N-365**'s subject and `X-be`'s open fork. `X-au-c3` carried a live obligation on four unshipped cutovers that was in NO registry; it was relocated to the amount-model phase preamble rather than archived with the entry (rule 5 condition 2). Every row is carried WITHOUT re-verification and says so |
| `two_shipped_steps_2026-08-30.md` | `X-f3c-2b-1` (the books-boundary invariant, entry reproduced verbatim) and `X-f3c-2d` (the append-only refusal's three arms) | Archived 2026-08-30 under rule 5 -- `../README.md` stood at exactly **980 of its 1,000-line cap against a 20-line headroom floor** and `X-f3c-2d` owed a specification, so `X-f3c-2b-1` was condensed to fund it, net zero. `X-f3c-2b-1` was the only shipped multi-line entry with NO archive record: `X-be-3` and `X-au-c3` are both named by live documents that would have lost what they point at. Carries what a LATER step must obey: the three arms are not interchangeable, the lift must disable before the delete, and the audit log's `to_jsonb(OLD)` conservation is what replaces an archive table |
| `five_shipped_steps_2026-08-26.md` | Five shipped steps, verbatim: `X-au-a`, `X-au-b`, `X-au-j`, `X-l` and `X-az` | Archived 2026-08-26 under rule 5 to buy the room `X-i3` and `X-i4` needed, the live document standing at **980 of its 1,000-line cap with a 20-line headroom floor -- zero room**. Three of the five carried a "What a LATER leaf must obey" clause, which is a live constraint rather than a record, so each was RESTATED in `../README.md` beside the work it binds. **`X-f1` was in the first cut and was PUT BACK**: `credit_card:CC3b`'s blocker cell names it and a plan-gate control derives its specimen from that cell, so archiving two lines cost more than it saved |
| `cash_arc_as_built_2026-07-27.md` | The CASH half as far as it has shipped: the plan of record's entire preamble (its running state narrative), Phase X steps X-a .. X-c2c3 and X-g1 .. X-g3b as built, and the 10-row closed-findings register | Extracted 2026-07-27 on the developer's instruction, at 2,982 lines, so the plan of record carries the work that REMAINS rather than the log of work that is done. **Phase X is NOT complete**, though every step recorded here IS in production -- read this for how a shipped cash behaviour was decided and which commit shipped it; the remaining steps are live in `../README.md` |
| `loan_arc_as_built_2026-07-26.md` | The LOAN half of the plan of record: its running state narrative, the loan problem and root causes, the loan fold's target shape, rulings D1-D5 / R-A / R-C / R-D / R-E, Phases A-E and F2/F3 as built, and the 75-row closed-findings register | The loan arc is COMPLETE and in production (PR #64, merge `88c79857`, 2026-07-25). Extracted 2026-07-26 so the live document could shrink from 2,713 lines to the cash work that remains. Read it for how a shipped loan behaviour was decided and which commit shipped it; do NOT read it as instructions |
| `recurring_loan_balance_root_cause.md` | The original 2026-06-26 diagnosis | Superseded by the deeper root cause (partial balance function) |
| `implementation_plan_posting_ledger_transfers.md` | Posting ledger Step 2 (transfers) | SHIPPED to prod 2026-06-28 (PR #48) |
| `implementation_plan_posting_ledger_cash_envelopes.md` | Posting ledger Step 3 (cash) | SHIPPED to prod 2026-06-29 |
| `implementation_plan_posting_ledger_loan_payments.md` + `implementation_plan_temporal_escrow.md` | Posting ledger Step 4 (loan splits) | SHIPPED to prod 2026-07-01 (PR #51) |
| `implementation_plan_loan_read_switch.md` | Ledger-authoritative loan reads | SHIPPED to prod 2026-07-02 (PR #52) |
| `implementation_plan_actuals_reporting.md` | Build-order Step 5 (actuals reporting) | SHIPPED (PR #58 merged) |
| `adversarial_review_balance_architecture_2026-07-02.md` | Full-arc review + oracle-teeth review | Their R-items shipped; process lessons carried into the plan of record |
| `followup_fence_loan_owed_at_dates.md`, `followup_debt_schedule_attribute_fence.md`, `followup_horizon_loan_predicate_split.md`, `followup_redundant_loan_resolution.md` | W9906 fence follow-ups + the 11x-resolution finding | All CLOSED on dev 2026-07-12/13 |
| `implementation_plan_fail_loud_ledger_authority.md` | Fail-loud arc; AS-BUILT record of C1/C1b/C2/C2b/C3 | C1-C3 shipped on dev; its "next up" superseded; kept as the authoritative as-built record of those five commits |
| `fifteen_shipped_steps_2026-09-12.md` | Fifteen shipped steps, verbatim, WITH their `steps.md` rows: `X-au-c`, `X-au-c3`, `X-au-f-1`, `X-bl-1`, `X-aw`, `X-bh-1`, `X-bh-2`, `X-bu`, `X-cf-1`, `X-cf-2`, `X-cf-3`, `X-bd`, `X-be-2`, `X-be-3` and `X-am` | Archived 2026-09-12 under rule 5 to buy the room `X-bi-1`'s tick needed, the live document standing at **1308 of its 1,330-line cap with a 20-line headroom floor -- two lines of room** for a tick netting eight. The developer ruled every fully shipped span leave; these are the fifteen of twenty-six that legally could -- the entries were already one-line stubs, so under rule 12 the ROWS leave the index with them, and a row a live `blocked by` cell names (seven), an entry carrying a *LATER step must obey* clause (three), `X-bz`'s live rollback sentinel, `X-f1` (put back 2026-08-26), `X-l` (rule 11) and `X-au-f-2` (a plan-gate control's specimen) stay. Every row is carried WITHOUT re-verification and says so |

**Twenty-five archived documents were DELETED on 2026-08-11**, twelve of them from this folder:
every one was referenced by nothing -- no live document, no code comment, no config. Completed
work is in the code and superseded work is a trap, so the working tree stopped carrying either.
**Git history holds them all**: `git log --diff-filter=D --name-only -- docs/` finds the commit,
and `git show <commit>^:<path>` reads any one of them back. The rows below are what remains.

Two standing warnings for anyone reading these for history:

* **Dollar figures and line numbers in archived documents were true on their write date only.**
  Several were already stale within days (the Checking figures moved with every re-anchor).
* **Three of these documents contain at least one load-bearing claim later proven false or
  uncited** (X0's premise, the cash "anchor has no date" root cause, M5's severity, one plan
  citation). Those corrections are reflected in the plan of record; the review that made them was among the
  documents deleted 2026-08-11 and is recoverable from git history. Do not act on an archived claim without re-verifying
  it against the code.
