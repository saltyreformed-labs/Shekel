> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Four shipped balance steps, archived out of the plan of record (2026-08-30)

**What this is.** The four `* [x]` entries that left `../README.md` section 5 on 2026-08-30 under
conventions rule 5, to buy the room `X-aw` needed for its own pointer, for `X-av`'s re-scope and for
the new step `X-bh`. The README stood at **exactly 980 of its 1,000-line cap against a 20-line
headroom floor -- zero room** -- the same state the 2026-08-26 archive was cut for, four days later
and one step's worth of additions along. Rule 4 forbids raising a cap when it binds, and the
developer chose this remedy on 2026-08-29 when asked. **The COMMIT is the record for every one of
them**; read the code each shipped, not this file.

**Why these four and no others.** Six entries in section 5 were ticked. `X-f1` and `X-l` are already
ONE-LINE stubs pointing at earlier archives, so there is nothing left in them to move. The other
four are archived whole.

## The obligations these entries carried, and where they now live

**Rule 5's second condition is that no live sentence may depend on an archived one.** Three of the
four carried a live constraint on work that has not shipped. None is summarised away:

| the obligation | it now sits |
|---|---|
| A later step may not reintroduce ONE test issuing every request -- that is O(the route table) under an O(1) budget -- and the routes must stay enumerable with no database (`X-bd`) | restated on the live `X-be-3` entry, the next step to touch that sweep |
| The per-test clone STAYS: the clone is what isolates one test from another, and a named start state may not weaken it (`X-be-2`) | restated on the live `X-be-3` entry, same reason |
| `credit_card:CC3b` owes a `deletion_refusal` arm for a terminal `Credit`, or a stated reason the destroy-but-not-correct asymmetry is acceptable there (`X-am`) | **already in `docs/plans/rulings.md` as `balance:R-HA`**, which states it in those words. The README copy was a SECOND copy of a registry's content, which conventions rule 16 forbids, so archiving the entry removes a duplication rather than creating a gap |

`X-aw` carried no such clause. What a later step must obey from it is in ruling **balance:R-HW** and
in ledger rows **N-390** and **N-391**, all three of which are live.

## The four entries, reproduced

* [x] **X-aw** `078077db` -- a paycheck's gross is a RATE, and **N-239** died by construction rather
  than being guarded. The residue distribution, `_residue_cents`, the reconciliation group and the
  partial-context fallback are DELETED; the gross is `round_money(annual / periods_per_year)` from
  ONE producer, `payroll_basis.gross_per_paycheck`, which `investment_projection`,
  `retirement_projection` and `retirement_dashboard_service` all share. **The step's own sentence
  was superseded before it was built**: it said to count a year's paydays from the cadence and KEEP
  the distribution, and ruling **R-HW** rejected that. **What it moved, measured**: 5 of the 63
  saved rows, 2027-01-14 .. 03-11, `$3,722.54` -> `$3,722.53`; the other 58 are unchanged. Its
  value is the six 2028 rows that would have moved when that year filled and now cannot. **Two of
  N-239's claims were measured FALSE.** Its bank half is a different defect -- the residue rule's
  whole reachable range is `{$3,525.96, $3,525.97}` against a stub gross of `$3,526.00` -- and is
  now **N-391**, owned by `X-av`, which is also why `bank_import:R-HT`'s "until X-aw ... the residue
  is zero" does not hold. And the identity MED-05 / PA-07 bought was already false in a 27-payday
  calendar year: 2026 is one on this owner's phase, and the old rule pays it `$95,200.96` at a flat
  `$91,675.00`. What the change costs is half a cent per paycheck of annual drift (`$0.13`
  biweekly, `$1.83` at the daily cadence). It also left **N-390**, the four judgements that still
  read the caller's period list.
* [x] **X-bd** `39935763` -- every route in the sweep is its OWN pytest item. Closed **N-364**,
  whose diagnosis it measured FALSE: the account-less arm had not grown at all since `910065a9`,
  and all 42 new URLs sat in the KIND arms, which one account route grows SEVEN at a time. The
  root cause was neither axis -- one test issuing every request is O(the route table) under an
  O(1) budget, so an arm only divides the constant. **A later step may not reintroduce one**, and
  the routes must stay enumerable without a database. Opened **N-387**, owned by `X-be-2`.
* [x] **X-be-2** `167aab8d` -- a test SAYS what world it starts in: a named seeded start state,
  built once per xdist worker and frozen into a snapshot every declaring test still takes its OWN
  private clone of. Closed **N-387**, whose read-only premise it measured FALSE (`GET /mfa/setup`
  writes, through `write_transaction()`), which is why the shared database that row pointed at was
  refused. **The per-test clone STAYS: the clone is the isolation.** Sweep 82.26 s -> 18.73 s
  serially, no suite-level change. Opened **N-388**, owned by `X-be-3`.
* [x] **X-am** `7b0ddae8` -- the `Settled` ARCHIVE is DELETED (**R-HA**): it was ABSORBING and
  REACHABLE, so a row entered it from one dropdown and no door led out, while the DELETE control on
  the same card still removed it. Closed **N-177**; as-built in `x_am_as_built_2026-08-27.md`.
  **What it pins is a CONJUNCTION** -- no state both reachable and absorbing -- not *no terminal
  states*, which would contradict `credit_card` locked ruling 5. **CC3b must obey it**: a terminal
  `Credit` re-arms the destroy-but-not-correct asymmetry `deletion_refusal` still permits.
