> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The decision sweep of 2026-09-03: rows that left `ledger.md` and a step that was withdrawn

Forty-four `ledger.md` rows named no live step on 2026-09-03 -- 32 owned by `developer-decision`,
12 by `operator` -- and the developer dispositioned every one in a single session. Most gained an
owner and stayed; these left, each by a RULING recorded in `rulings.md` or by a fix that SHIPPED.

| arc | id | left because |
|---|---|---|
| balance | N-214 | closed, **R-JO**: the shared rate-limit bucket it described does not exist (Flask-Limiter limits are per endpoint, measured 2026-08-14); the query-string exposure it recorded is mitigated by nginx logging `$uri` only |
| balance | N-314 | answered by **R-IS** and **R-JN**: the bank's daily closings become OBSERVATIONS in the level relation `X-bj-1` builds, neither assertions that reset nor opening-equity restatements |
| balance | N-243 | DISSOLVED per kind onto the leaves that delete each: CC paybacks to `credit_card:N-351` (CC3b, CC3c), calibration rates to `salary:N-530` (S1), the salary template's default amount into `balance:N-446` (X-bp), transfer template amounts to `balance:N-450` (X-au-f), the loan principal to `recurrence:D61` (R16-c), the envelope actual to `balance:N-447` (X-bi-3) |
| balance | N-393 | FIXED: `append_balance_assertion` and its sibling `add_anchor_history` lost the `period` their bodies never read, 43 and 6 call sites -- `a73392b2` (PR #216) |
| pay_calendar | P82 | FIXED: the `_file_under_rules` docstring says the `PayCalendarError` handler exists and why the door degrades the filing ahead of it -- `a73392b2` (PR #216) |
| recurrence | F-5 | ruled a FEATURE, **R-R54**: a 27-paycheck year becomes a derived fact at `pay_calendar:C14`, and naming one on a surface is a roadmap item, moved to `docs/project_roadmap_v5.md` |
| bank_import | N-472 | closed at `ba5e0474` (PR #217), where the container arm it named was built and the row's text became false; by conventions rule 1 a finding whose subject is the gate is document upkeep |

**Merged, not closed** (the absorbed id stays in the surviving row's `also` column): `balance:N-82`
into `pay_calendar:P7` (the same half-model defect, closed by C9); `recurrence:F-4` into
`pay_calendar:N-398` (the same payday-shift defect, closed by C14; the row re-keyed with its owner).

**Withdrawn step**: `bank_import:X-gh` (`feat(balance): the bank's balance asserts the anchor`,
ruling **R-GL** 2026-08-24). Superseded by **R-IS** (2026-09-01) and **R-JN** (2026-09-03): under
the level relation the bank's closing is an observation, so *a confirmed import's anchor may ASSERT
the opening* describes a mechanism the design no longer has. Its rows went to `X-gi` (**N-470**) and
`X-bj-1` (**N-434**'s column half; the parser half to `X-f6b`). R-GL stays in `rulings.md` with its
superseded-by note.
