> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Archived sequencing: the credit-card arc, 2026-07-19

**Lifted out of `implementation_plan_credit_card.md` on 2026-08-11**, unchanged. The heading is
kept so an existing citation still resolves.

**It was DISCHARGED before it was archived, and that is why it is here rather than live.** Every
balance-arc step it names has shipped or been renamed: `C8` / `C9`, `D1`-`D3` and old `X1`-`X3`
resolve only in archived records, and old `X4` survives as `X-e`. Two of those bare ids now name
LIVE steps in other arcs -- `pay_calendar:C8` and `recurrence:D1` -- so reading the list below as
current sends a reader at work that is not the work it meant.

**What actually gates the credit-card arc is ruling R-EB**, which is newer than this ruling, and
the order is `docs/plans/steps.md`'s.

## Ratified sequencing (developer ruling, 2026-07-19)

After C8 is NOT the start signal; the ratified order is:

1. Balance arc C8 tail (C8d + C8e + C8f, in flight) -> C9 -> Phase D (D1/D2/D3). Rationale: the
   checker fence is FROZEN until D3 and this arc needs new fence classifications; D2's typed
   balances mean the revolving producer is born into the final structure, never retrofitted.
2. Interim prod ship dev -> main (including the outstanding C2 real-clone live-render check), so the
   loan arc ships and rolls back independently of card work.
3. **X1 alone** (settled counts from its settle instant; ruled R-B). It stops the live re-anchor
   treadmill AND lands the shared instant-partition core as production code -- CC1a below becomes
   consume-the-helper instead of extract.
4. **This arc, Phases 0-5** (dedicated worktree + feature branch off clean dev; land into dev per
   phase -- 0+1 together, then each phase).
5. Balance arc X2-X4 (the cash fold adopts the core this arc proved on the complete-data case), then
   E1, F1, closeout.
6. This arc's Phase 6 (UI cockpit) -- any time after Phase 5, via the shekel-design loop.
