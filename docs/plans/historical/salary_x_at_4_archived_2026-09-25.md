> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The shipped salary step `X-at-4` archived under rule 5 on 2026-09-25

The salary plan stood at 300 lines, the most its 320-line cap allows under the gate's 20-line
headroom arm, when the tick for `salary:X-av-3a` (`8e832d8e`) split `X-av-3` into its two leaves:
the container, the shipped `X-av-3a` with its money declaration, and `X-av-3b`'s specification
(**R-SAL95**, **R-SAL97**, per **R-SAL96**). Under `docs/plans/conventions.md` rule 5 the room
comes from archiving a COMPLETED step, never from trimming a live specification. `X-at-4` was the
one SHIPPED salary step left in the plan that no other row of `docs/plans/steps.md` cites as a
wait or an alias and no outcome names (measured on the tick's tree with the gate's own
`blocked_keys` / `alias_keys` and the outcomes table), and it owns no ledger row. Its parent
`X-at` stays open, as `X-av` did when `X-av-1` left
(`historical/salary_shipped_steps_archived_2026-09-25.md`). The live sentences still naming it
(`X-at-8`'s "when `X-at-4`'s refusal begins", in its plan entry and its index row) cite where the
refusal came from and state its date themselves, so none depends on the text below. Its entry and
its index row are below, verbatim, carried WITHOUT re-verification. A live sentence may cite
`X-at-4` for how something came to be, never as a plan of record.

The plan entry as it stood (section 4):

      - [x] **X-at-4** `5d5f5bc1` -- the alarms (**R-SAL74**, **R-SAL86**-**R-SAL88**), `$0.00`; where
            the newest year lacks a state, a missing year's one sentence names each part's year
            (**R-SAL91**); the publish job outputs the refusal's start instant and `build-and-push`'s
            first step refuses once it has passed, so a re-run reusing a November check still refuses.

The index row:

- **X-at-4** `5d5f5bc1` -- Made a forgotten tax year loud on ONE pure check (**R-SAL74**) that next year's law lists every state an earlier year lists, the law refusing a skipped year (**R-SAL86**): from Nov 1 a banner with no close on every owner page (**R-SAL87**, **R-SAL91**) and a failing weekly workflow; from Dec 1 its own `tax-law` CI job in every scope and a refused release image (**R-SAL88**); `$0.00`.
