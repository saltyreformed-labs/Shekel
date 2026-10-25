> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Eleven closed findings that `app/`, `tools/` and `tests/` still cite (2026-09-01)

**Read-only history. Nothing here governs anything.** It exists so that a reader who meets
**N-241** in a docstring can find out what N-241 WAS. Cite this for how a decision came to be,
never for what is true now.

## What was wrong

`ledger.md` promises, on its own line 5, that "closed rows move to their arc's as-built record under
the same id." **For these eleven that did not happen.** Each row was removed from the ledger when its
fix shipped, and no archived document ever recorded what it had said. Their ids survived only in the
CODE, where 75 comments and docstrings name them as the reason a line is written the way it is.

That is a live cost rather than an archival tidiness point: a reader who greps `N-269` finds a
`steps.md` row saying `X-au-c2b` closed it, and no statement anywhere of what it was, so the
docstring citing it cannot be checked, updated or safely deleted.

**Nothing in the gate can see this.** `tools/plan_gate` grades step keys, owners, ruling ids, counts,
order and archive banners; there is no finding-id referential check anywhere in it. The gap was found
2026-09-01 when `balance:X-f3c-2b-2c` shipped a runbook citing a ledger row that did not exist and
the gate passed 291 of 291 over it.

## How these eleven were chosen, and what the census cannot do

The set is: every `N-###` cited from a `.py` file that has neither a live `ledger.md` row nor a
naming in any document under an `archive/` or `historical/` directory. Measured 2026-09-01 over the
tree at `b29625c1`: 258 ids are cited from Python, 93 have a live row, 154 have an archived record,
and these 11 have neither.

**The census OVER-connects and cannot establish an absence on its own.** Its pattern also matches
arithmetic -- `reject step N-1`, `N-1 paychecks` -- so every one of the eleven was read in context
and confirmed to be a finding reference before it was included. A wider sweep of all documents found
22 unresolvable ids; the other eleven are not orphans of this kind and were deliberately left out:
`N-279` and `N-280` are retired names recorded inside the rows that replaced them, `N-369` was
deleted rather than answered, and the rest are named only in `rulings.md` prose about the plan gate.

**The developer scoped this deliberately** (2026-09-01): the ten-or-so ids that a code reader
actually trips over, not a catalogue of all 417. The full index was priced and deferred -- 92 ids are
named in more than one archived document, so "which document is the record" is not mechanical and
needs a judgement per id.

**A second weakness in the same predicate, stated because re-running the census will show it.**
"Has an archived record" is satisfied by ANY naming in an archived file, including a passing mention.
So the three ids named in the paragraph above -- `N-279`, `N-280`, `N-369` -- count as resolved from
the moment this document names them, without anyone having written down what they were. That is an
artefact of the measurement, not a repair. A predicate that meant what it says would ask whether the
id has a DESCRIPTION, and no mechanical test can ask that.

## The eleven

**Every entry is carried WITHOUT re-verification** (`conventions.md` rule 5's third condition). Each
is the finding's own words as they stood in `ledger.md` immediately before the commit that removed
it, recovered from git, condensed to its subject. **None has been re-measured against today's code**,
and several are explicitly about a state that no longer exists.

| arc | id | closed by | what the finding was |
|---|---|---|---|
| balance | **N-145** | `01efd1b4` (X-au-c2a, 2026-08-13) | `transfer_service.py` sat at 999 of pylint's 1000-line ceiling, blocking `X-d`. A size gate, not a money defect |
| balance | **N-220** | `b6b65f1e` (2026-08-14) | Seven archived rulings state an id and no rule: an unescaped pipe inside a backticked identifier truncated the cell when the rows were condensed into `phase_x_as_built_2026-08-04.md` |
| balance | **N-234** | `b6b65f1e` (2026-08-14) | `tools/plan_gate` separated `steps.md`'s tables by COLUMN COUNT, so any three-column table in that file was silently read as a cross-arc fork. A table added for another purpose joined the registry it happened to match, with no error |
| balance | **N-241** | `81b5b659` (X-au-c3, 2026-08-18) | A machine-derived figure was written into `actual_amount`, the column ruling **R-FH** reserves for a human's, on a derive-mode loan-payment settle. Three subsystems read that column's NULL-ness as "a human entered this" and every one saw a manufactured correction |
| balance | **N-242** | `81b5b659` (X-au-c3, 2026-08-18) | `posting_service._settle_effective` read a settled figure with no status predicate and no `.limit(1)`; settled-ness was enforced solely by its one call site. A second active shadow raised `MultipleResultsFound`, and a NULL amount reported a shadow that exists as missing |
| balance | **N-254** | `b6b65f1e` (2026-08-14) | `ledger.md` gave ONE id to TWO findings, four times over (`N-244`..`N-247`), because two sessions appended on successive days. The gate graded owners, sizes, the graph and the order, and never that the KEY is unique |
| balance | **N-257** | `81b5b659` (X-au-c3, 2026-08-18) | A reverted transfer kept its `actual_amount`, so a Projected row was priced from a settle that had been undone, and a later settle's freeze was masked by the stale actual |
| balance | **N-269** | `47f3baef` (X-au-c2b, 2026-08-16) | The transfer settle door re-queried the transfer `frozen_amount` it had just loaded, taking the reconcile panel's transfer SELECTs from K to 2K |
| balance | **N-270** | `47f3baef` (X-au-c2b, 2026-08-16) | `spending_report_service.py` sat at exactly 1000 lines with zero headroom, so the next edit of any kind would trip C0302 and have to shed a line or split the module under time pressure |
| bank_import | **N-389** | `9038a649` (X-gj-1c, 2026-08-31) | The Reconcile tab bar could count an act its tab cannot render: `accepted_counts` counted every match row while `accepted_groups` skipped one naming no bank line, so the Explained caption promised a number the tab did not deliver |
| bank_import | **N-403** | `9038a649` (X-gj-1c, 2026-08-31) | The NEW-ENVELOPE answer was unreachable with scripting off on all three merchant-rule surfaces: the fields stayed `d-none`, so the answer arrived with a name and no category and was refused every time |

## What this does NOT do

- **It adds no gate.** Whether a finding-id referential check should exist, and whether it would be a
  gate arm or a report, is undecided. A gate arm that refused a commit for citing an unresolvable id
  would fire on retired aliases and on deliberately deleted rows, and would refuse to record work
  somebody has done -- the ground on which the developer made rule 4's corpus arm a REPORT on
  2026-09-01.
- **It does not close the class.** The next finding closed without a record becomes the twelfth
  orphan, silently, and nothing will notice until somebody greps.
- **It re-measures nothing.** Every entry above is a 2026-08 statement about a 2026-08 tree.
