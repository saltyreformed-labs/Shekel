> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The credit-card plan's `CC-5-5` span, condensed under rule 5 on 2026-10-05

The credit-card plan stood at 379 lines, one under the most its 400-line cap allows under
the gate's 20-line headroom arm, when the tick of `credit_card:CC-5-4a-5`'s sixth leaf,
4a-5c-2c-1 (`0088187f`), had to record that leaf as built in `CC-5-4a-5`'s entry. Under
`docs/plans/conventions.md` rules 4 and 5 the room comes from a COMPLETED span, never from
trimming a live specification. `CC-5-5` is a decomposed parent ticked with its last leaf
(`8f8b056d`), and it and its four leaves, `CC-5-5a` to `CC-5-5d`, are SHIPPED rows of
`docs/plans/steps.md`, so the span is complete. It is condensed in place to rule 5's form -- each
step's id, its commit and what it closed -- and its text is below, verbatim as it stood on the
tick's tree (`64cb8a8af`), in a 4-space-indented block (the indent added so it renders as a
record, not as live checkboxes), carried WITHOUT re-verification. Every finding the text names is
closed (none is a row of `docs/plans/ledger.md`), every ruling these steps took is a row of
`docs/plans/rulings.md`, and each step's own sentence is its `docs/plans/steps.md` row. No live
sentence in the plan cites the text below; a live sentence may cite this record for how
something came to be, never as a plan of record.

The `CC-5-5` span as it stood (the plan's steps section):

    - [x] **CC-5-5** `8f8b056d` -- `R-CC47` (re-scoping `R-CC41`; the sixth site `R-CC48`): every
          balance is what the account HOLDS, negative when owed, and owed is minus it; the DECOMPOSED
          parent, split 2026-09-22 (`R-CC50` as amended by `R-CC52`) into 5a, 5b and 5c, and given
          2026-09-23 5d (`R-CC69`..`R-CC73`); ticked with 5d.
      - [x] **CC-5-5a** `aa29d977` -- `balance_at.owed(balance) = -balance`, R-CC29's one flip moved
            into the seam (`card_statement.owed` deleted); the /savings revolving footer is each
            non-loan liability's owed amount floored at zero, summed (`R-CC49`);
            `tests/manual/verify_liability_screens.py`, the screen-diff instrument (178 responses plus a
            `tree.json` app digest) that grades 5b and 5c; 178 screens byte-identical on production's
            shape.
      - [x] **CC-5-5b** `3d9d0c1a` -- every liability balance door asks OWED and stores held through
            `liability_sign.held_balance` (`owed()` moved out of the seam; `R-CC52`): the create form
            (`R-CC58`), the anchor editor on every surface (`R-CC57` as amended by `R-CC60`), the
            books-opening card; a stale form refused and re-opened as a fresh click (`R-CC61`,
            `R-CC62`); no stored row re-signed, a loan's anchor cell a link (`R-CC53`). Two commits,
            `ef4f6782` first; closed **CC-357**; suite 15186/0 at `ef4f6782`.
      - [x] **CC-5-5c** `6daa3048` -- ONE commit (`R-CC50`): the configured-loan arms report HELD
            through `liability_sign.owed`; net worth the plain sum; band, trend, subtotal (`R-CC48`),
            tiles, debt summary, loan readers and archived drawer (`R-CC67`) read `owed`; the footer's
            words (`R-CC68`); an anchor save answers its opener's display (`R-CC74`, `R-CC77`, `R-CC78`)
            from the door's report (`R-CC79`, `R-CC85`). Rule-5 re-signs confirmed; closed **CC-354**,
            **CC-361**, **CC-362**, **CC-365**; suite 15323/0.
      - [x] **CC-5-5d** `8f8b056d` -- a goal on a DEBT is a milestone to get under (`R-CC69`..`R-CC73`):
            ONE goal door, `savings_goal_door.judge_goal_save` (`R-CC87`, `R-CC90`, `R-CC93`); every
            debt figure by the tile's one rule, `_tile.tile_balance_on` (`R-CC88`, `R-CC95`, `R-CC97`),
            a card-style goal's start recorded as `start_owed` (`R-CC91`); a Delete goal button
            (`R-CC94`); migration `764461215480`. Rule-5 re-expressions confirmed; closed **CC-360**,
            **CC-371**; suite 15368/0.
