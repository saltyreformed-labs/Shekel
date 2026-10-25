> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Eight shipped balance steps, archived out of the plan of record (2026-09-01)

**What this is.** The eight multi-line `* [x]` entries that left `../README.md` section 5 on
2026-09-01 under conventions rule 5, to buy the room `X-au-g-2`'s DECOMPOSITION needed: one step
became three and each leaf owes a specification (rule 12). The README stood at **978 of its
1,000-line cap against a 20-line headroom floor -- two lines of room** for a change that needed
twenty-six. That is the third archive cut for this reason in six days (2026-08-26, 2026-08-30, and
this one), which is finding **N-365**'s subject and `X-be`'s open fork. Rule 4 forbids raising a cap
when it binds; the developer chose this remedy on 2026-09-01 when asked. **The COMMIT is the record
for every one of them**; read the code each shipped, not this file.

**Why these eight and no others.** Twenty entries in section 5 were ticked. Twelve are already
ONE-LINE stubs, so there is nothing in them to move. The other eight are archived whole and left as
one line each.

**Every row here is carried WITHOUT re-verification** (rule 5's third condition): each is reproduced
from the entry it replaces, and no claim in it was re-measured on 2026-09-01. The commit each names
is the thing to read.

## The obligations these entries carried, and where they now live

**Rule 5's second condition is that no live sentence may depend on an archived one.** Seven of the
eight carried nothing live -- their opened and closed findings are rows in `docs/plans/ledger.md`
with live owners, and their rulings are rows in `docs/plans/rulings.md`. One did:

| the obligation | it now sits |
|---|---|
| **A LATER leaf must obey**: a figure and a status change are independent facts ONE seam call applies (`X-au-c3`) | restated in the live **Phase X -- the amount model** preamble, which governs every leaf of that phase rather than the one that happened to come next. It was in NO registry -- not `rulings.md`, not `ledger.md` -- so archiving without relocating it would have deleted a live constraint on four unshipped cutovers |

`X-au-c3`'s entry also said it was *"Held here rather than archived because two live blocker cells
name it"*. That reason does not survive inspection: a blocker cell names a step KEY, and the key
still has its one-line entry -- what the cell needs is `steps.md`, which is where those cells live.
The real reason to hold it was the obligation above, and that is why the obligation moved rather
than the entry staying.

## The eight entries, reproduced

* [x] **X-f3c-2b-2a** `59b485df` -- the DOOR that restates an account's opening: append-only,
  through the table's ONE writer, bounded by the movements recorded and by today but NOT by
  `earliest_recordable_day` (**R-ER**), two entrances (**R-IE**). Migration `c9f4b1e78d02` rides it:
  the governing row is the latest `id`, never `created_at`. **N-275**, **N-379**, **N-382** stay
  OPEN at **X-f3c-2b-2c**; opened **N-400**.
* [x] **X-f3c-2b-2b** `7ef63899` -- a matched bank LINE bounds its account's opening at BOTH tiers
  (**R-IG**, **R-IH**): four doors refuse a line the books cannot hold, three screens stop OFFERING
  one, and deferred triggers on `statement_match_members` AND `bank_statement_lines` make the state
  unstorable. The builders became ARM-EXPLICIT, deleting the migration's 90-line frozen body copy.
  Closed **N-383**; opened **N-407**.
* [x] **X-au-c3** `3d1379d1` -- a settle RECORDS what moved rather than refreshing an amount. **A
  LATER leaf must obey**: a figure and a status change are independent facts ONE seam call applies.
* [x] **X-au-g-1** `af61263d` -- `_resolve_loan_basis` loaded the loan's whole payment history to
  recover ONE field that is character for character `compute_monthly_payment_baseline`'s body, a
  producer taking NO payments and NO anchors. Finding **N-266** (a) is MISDIAGNOSED rather than
  falsified: its PATH is dead, its CONCLUSION stands, because `get_payment_history` prices through
  `owned_contribution`, which refuses a derived row -- one unrouted reader, not an irreducible
  cycle, which is why the cutover routes that reader before declaring either leg.
* [x] **X-bh-1** `b955d0c8` -- the engine reads the owner's CALENDAR: `PayrollBasis` carries it,
  `all_periods` is deleted, and the four judgements become two producers (`pay_calendar._rhythm`),
  so **D25**'s narrow context is unrepresentable rather than forbidden in prose. Both trees driven
  against the dev database agree on all 63 paychecks, `$170,974.29` of net and every analytics
  month. Opened **N-394**, **N-395**, **N-396**.
* [x] **X-bh-2** `49fdfb91` -- the rhythm runs BACKWARD too (**R-IA**), bounded by a stored
  `budget.pay_schedule.history_opens_on` registration and the pay-periods settings section ask for.
  Closed **N-390**: with his opening stated, the developer's 2026 year-to-date at 2026-05-21 goes
  from four recorded paydays to the NINE he was really paid. **`NULL` means NOT STATED (`R-IF`)**,
  so an unasked owner is counted from the record, unchanged. Also closed **N-396** by deleting
  `earliest_start_in_month`. Opened **N-398**, **N-399**.
* [x] **X-ad-a** `2a4eb477` -- registration ASKS for the payday, the cadence and the horizon and the
  bootstrap payday is DELETED; closed **N-123** (= pay-calendar `P3`), satisfies `C4`'s `P8`.
* [x] **X-be-3** `0aa2cc80` -- the sweep grades EVERY GET route and carries no list of the ones it
  does not. Closed **N-388**: `_UNREACHED_RULES` and the skip branch that fed it are DELETED, the
  world holds a row of every kind a GET rule takes an id for, and coverage is an equality against
  `url_map` no list can satisfy. **All 17 unrequested rules answer and NONE 5xx.** The fill map is
  keyed `(blueprint, converter)` with NO bare-name fallback. `X-bd`'s and `X-be-2`'s two standing
  constraints moved into `_SWEEP_CASES`, the one home an archive cannot govern away.
