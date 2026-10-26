> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The credit-card plan's `CC-5-4a-5` entry and its two parents, condensed on 2026-10-05

`credit_card:CC-5-4a-5` shipped at `e291812c`, its seventh and last leaf (4a-5c-2c-2), which
carries the step's `Ships:` trailer; its two DECOMPOSED parents, `CC-5-4` and `CC-5`, held no
other open leaf, so they ticked with it. `docs/plans/conventions.md` rule 7 makes a SHIPPED
step's entry a POINTER that opens with its commit, and the plan gate holds this plan's ticked
entries to six lines, so the tick condensed all three entries in place. Their text is below,
verbatim as it stood on the tick's tree (`0f3c72ef4`), in 4-space-indented blocks (the indent
added so it renders as a record, not as live checkboxes), carried WITHOUT re-verification. With
them: the seventh leaf's record, which the entry held only as a plan, written by the tick, each
clause about the code graded on the tick's tree by the tick's own checks and its rulings and
applications as the leaf's message gives them; and the ledger row `credit_card:CC-364`, which
closed at that leaf, as it stood when it left `docs/plans/ledger.md`. No live sentence in the plan
cites this record's text; a live sentence may cite it for how something came to be, never as a
plan of record.

## The seventh leaf, as built

**4a-5c-2c-2** `e291812c` (the last leaf, carrying `Ships: credit_card:CC-5-4a-5`) --
carry-forward's Confirm names each bank line an envelope's close frees, from ONE read over every
envelope's covering movements (`_preview._what_each_close_frees`, through
`match_withdrawal.pending_alone_and_together`); a match only several closes free together is one
sentence (`_withdrawal_macros.shared_match`, **R-CC135**), the envelopes read in id order. The
Confirm posts every line the modal named (`shown_lines`, `CarryForwardPreview.named_line_ids`;
**R-CC127**). The batch is ONE `Press(shown, promised=True)`, threaded to every envelope's settle
and to `transfer_service.update_transfer`, in place of its `Silent("CC-364")` press; a refused
Confirm rolls back and redraws the modal's content, a designed 400 (**R-CC128**). The `Silent` sweep
leaves `match_press.MARK_PAID` the one silent press (**R-CC56**), and a call refused at once states
no count (`PageOutOfDate.over_an_unnamed_line`), the coordinator's application of
**balance:R-BAL207**. Applications under **balance:R-BAL207** (the lane's, as the leaf's message
lists them): a shared match is named under the FIRST envelope listed, its partners by name; an
envelope reopened with no purchase carries the same caption; a POST without the field named nothing;
promised, so an envelope another tab closed refuses the page that named its line; the redraw opens a
second read pass after the rollback; the press is threaded to the transfer move. Closed **CC-364**.

## The three entries as they stood (the plan's steps section)

`CC-5`:

    - [ ] **CC-5** `feat(cards): a purchase is a movement on the card` -- design 3.2 (`R-CC15`): the
          DECOMPOSED parent, split 2026-09-20 (the card lane's trace) into 5-1 (the key), 5-2 (the
          purchase door, its readers and the picker) and 5-3 (the settle-with-tender door), 5-1 and 5-2
          in ONE PR (`R-CC33`), and 2026-09-21 (at 5-3's entry) into 5-4 (the matcher's card-screen
          half, `R-CC40`) and 5-5 (the net-worth sign fix, `R-CC41`); ticks with its last leaf. The flag
          survives to CC-7.

`CC-5-4`:

    - [ ] **CC-5-4** `feat(cards): the card's line meets the bill it paid` -- design 3.2 and `R-CC40`'s
          HALF 2: the DECOMPOSED parent, split 2026-09-21 by the developer (`R-CC45`) into 4a-1 (the
          writer), 4a-2 (the re-key migration; the member table's bill column dropped) and 4b (the card
          panel's settlements arm, `R-CC44`), and on 2026-09-22 given 4a-3 (one act takes a movement off
          the books; the popovers' captions; `R-CC51`), 4a-4 (a row holding a movement is history;
          neither member subject key cascades; `R-CC55`) and, on 2026-09-23, 4a-5 (the reconcile panel
          and carry-forward say what they free first; `R-CC76`); ticks with its last leaf. Must land
          before any card import exists.

`CC-5-4a-5`:

    - [ ] **CC-5-4a-5** `fix(cards): the panel and carry-forward say what they free first` -- `R-CC76`:
          the two doors that reach the status seam's `$0.00` / purchases withdrawal and undo a statement
          match with no caption (**CC-364**) name the bank lines they would free BEFORE the press, from
          the same read they act on, as the popovers do (`match_withdrawal.pending_for_movements`;
          `R-CC56`, `R-CC59`). The reconcile panel (`accounts/_reconcile_panel.html`) says it on a row
          whose kept payment an accepted match names: its per-row `settled_amount-<id>` box at `$0.00`,
          a reverted envelope's tick (the seam's purchases arm) and a transfer row's box at `$0.00`,
          which takes both legs' payments off as the transfer popover's Actual box does. Carry-forward's
          confirmation (`grid/_carry_forward_preview_modal.html`) says it for each envelope
          `settle_from_entries` would settle. Of the seam's doors only the grid's one-click Mark Paid
          stays silent (`R-CC56`); the doors OUTSIDE the seam that withdraw a match with no caption (the
          purchase delete, Undo CC, the popover's Status leaving Credit, the last credit purchase's
          delete or un-credit) were **CC-367**; the account and recurring-transfer permanent deletes
          archive on a held leg since **R-CC65** and free nothing. **R-CC80** puts CC-367's doors in
          this step: each says it first, and the one removal act asks what the owner was shown
          (**R-CC81**). Its `$0.00` captions were its own question at its start, under **R-BAL155**
          (ruled below). A row's own account's list, ticking a reopened bill whose kept payment is on
          another account (a card, or Checking under `R-CC117`), re-pointed that payment
          (`status_seam._covering._re_point`) and withdrew any match naming it through
          `match_withdrawal.withdraw_for_moved_movement` with no caption (**CC-378**): its builder asked
          the developer whether that list captions such a row (`paid from <account>`) or omits it (ruled
          below). After 4a-4 (`R-CC76`). Closes **CC-364**, **CC-367**, **CC-378**, **CC-384**,
          **CC-385**, **CC-386**. **Ruled at its start** (2026-09-30): the panel refuses a `$0.00`
          payment (**R-CC125**), a reopened bill paid from another account is offered only on that
          account's list (**R-CC126**, so the panel never re-points and **CC-378** closes unreachable),
          and the removal act checks the page (**R-CC127**).
          **Built in seven leaves, `Ships:` on the last one** (the coordinator, 2026-10-04 and
          2026-10-05). **4a-5a** `ff86849b` (done, no `Ships:`) -- the removal act takes the lines a
          page showed or a named silence (**R-CC127**); every popover press posts the lines its captions
          named (`shown_lines`), and a stale press redraws the whole card (**R-CC128**); Estimated's
          caption on a reverted matched row lets one Save record `$0.00` (**R-CC129**); a companion's
          Mark Paid that would free a line is refused (**R-CC130**); the delete dialog posts the
          purchases it named (`shown_purchases`; **R-CC131**, fulfilling **R-CC96**'s clause);
          `ShownIds` joins the row-id census; a hand-built hard DELETE of a pair whose leg is matched,
          which withdrew the match silently, is refused (a designed 400; **R-CC127**). Applications, no
          ruling of their own: `designed_error(retarget=)` for the redraw (**salary:R-SAL33**) and the
          owner's one-click Mark Paid silent (**R-CC56**); a transfer `$0.00` Paid caption withdrawn (a
          transfer prices `$0.00` only where a derived payment rounds to it, and there the popover's
          Paid is refused as out of date). **4a-5b** `b3ff5bec` (done, no `Ships:`) -- **CC-367**'s
          doors name what they free first and post it back (**R-CC80**, **R-CC127**): the purchase X's
          confirmation asks in R-CC80's words and posts its lines as the DELETE's query string, a list
          out of date refused and drawn again as it is now; on the envelope's last card purchase the X
          names and takes the CC payback's line too, purchase and payback in ONE removal act
          (`delete_entry`: two acts on one account refused the ordinary X as out of date); the edit
          form's CC un-tick on that purchase names the payback's line in a caption and posts it; Undo CC
          and Status leaving Credit share one caption beside Undo CC
          (`credit_workflow.pending_for_credit_revert`), posted by Undo CC and the Save; one read serves
          the purchase lists (`match_withdrawal.pending_for_each`, `entry_service._removals`), who is
          looking taken from the session, never the posted `can_edit`. A companion is shown no line
          (**R-CC132**): a purchase whose X would free its line has no X, the ruled note in its place, a
          press that would free one anyway is refused with the ruled sentence, and one over the CC
          payback's line (the last card purchase's X or un-tick, an add or an edit bringing the card
          total to `$0.00`) with one sentence, an application: "Groceries's card payback is matched to a
          line on the bank statement, so only the account owner can make this change." The grid's
          statement-count test folds an `IN (...)` id list (**R-CC133**, rule 5). Closed **CC-367**.
          **4a-5c**, split in two: **4a-5c-1** `bc0b02b3` (done, no `Ships:`) -- the reconcile panel. A
          ticked `$0.00` box is refused before its arm settles anything, in the ruled sentence naming
          the row or the leg (**R-CC125**, `_rows._refuse_a_zero_payment`, both arms). A row's own list
          leaves out a row holding no purchase whose covering movement is on another account, of any
          date (**R-CC126**, `_transactions._own_clauses`; any date is an application of the picked
          description), so neither list re-points a payment (`status_seam._covering._re_point` changes
          nothing), and its tick names no tender (**R-CC15**'s tender at the panel deleted as changing
          nothing a tick books, an application; the booked account pinned per list). The one tick left
          that takes a payment out of its matches, a row settling from its purchases over the payment a
          revert kept, says so under its row ("Closing it from its purchases withdraws ...", the shared
          `frees_lines` clause, an application of **R-CC56** and **R-CC80**) from ONE read
          (`match_withdrawal.pending_for_each`), and posts those lines per row (`shown_lines-<row id>`)
          to its own settle, since the act compares per account; a stale press redraws the panel with
          the refusal's facts and "Here it is again -- tick what your statement shows." (**R-CC127**).
          The panel's two `Silent` sites are deleted. Closed **CC-378**. Left open: a `$0.00`-figure row
          ticked with its box cleared, uncaptioned (**balance:BAL-596**); two rows matched to one line
          ticked together, refused (**CC-384**); a captioned tick whose last purchase another tab
          deleted, saved uncompared (**balance:BAL-597**). **4a-5c-2**, split in three (the coordinator,
          2026-10-04): **4a-5c-2a** `f6de7772` (done, no `Ships:`) -- a statement screen never moves a
          payment between accounts (**R-CC137**, amending **R-CC43**): the reconcile panel and the
          statement matcher ask ONE predicate, `status_seam.payment_recorded_elsewhere_clause`, so a
          Projected row holding no purchase whose payment is recorded on another account is not offered
          on its own account's screen, and the matcher names no tender (`_moving._apply_day`), so 'Paid
          from' is the one door that moves a bill's payment. The screen names each such row in one
          sentence (**R-CC140**, replacing R-CC137's; `_subjects.HeldElsewhere.said`) at what pressing
          Paid records now (**R-CC139**, `_valuation.held_elsewhere_of`); applications under
          **balance:R-BAL207**: a `$0.00` row is not named, an unpriceable one is counted among the rows
          that could not be priced, a figure the owner typed is the figure named, only Projected rows
          are named, and "is not listed here" speaks of the match list. The panel's `$0.00` refusal
          reads as a deposit on money coming in (**R-CC136**, `_rows.ZERO_DEPOSIT_REFUSAL`). Three
          **R-CC43** tests rewritten under rule 5 (**R-CC138**), and a fourth deleted, the lane's
          reading of R-CC138's "Make the tests match the code" (only the declined ask named the
          deletion). Filing a purchase into a lump-paid envelope stays allowed (**R-CC141**), except
          where the owner-wide claims scan refuses it (**CC-385**). **4a-5c-2b** `2c7bb775` (done, no
          `Ships:`) -- **R-CC135**'s one check per save: a door whose page names lines, or that is
          silent by a ruling, opens ONE `match_press.Press` around its save, and a door whose page names
          nothing has each call open one of its own over `NOTHING_SHOWN`; each call refuses at once a
          line the page did not name, and the close compares what the whole save freed with what the
          page named (a `Silent` press compares nothing) and logs the withdrawal events. A save that
          reached no match step is graded only where its page promised what it named, the reconcile
          panel (`promised`; closed **balance:BAL-597**). The statement matcher keeps the default press:
          its accept frees nothing, and its undo frees no line another act holds, since an act that only
          matched creates nothing to remove and a purchase an act created becomes its envelope's last
          card purchase only by a CC tick, an edit the undo refuses. Its claims are the account's own
          acts and a destination is re-asked of its row as it stands, so filing into, and matching a
          purchase under, a reverted lump-paid envelope go ahead (**R-CC141**, **R-CC143**; closed
          **CC-385**). Applications under **balance:R-BAL207**: the `promised` rule, a door's own
          refusal abandoning its press, and carry-forward as one `Silent("CC-364")` press. Left open:
          **CC-384**, **balance:BAL-599** (events logged before the commit, `balance:X-dc`) and
          **balance:BAL-600** (BAL-597 at the single-row doors, `balance:X-da`). **4a-5c-2c**, split in
          two (the coordinator, 2026-10-05): **4a-5c-2c-1** `0088187f` (done, no `Ships:`) -- the
          statement matcher's double-count guard, `_accept._reject_parent_and_its_own_purchase`, and its
          proposer half, `_propose._holds_a_parent_and_its_child`, are deleted, every path that could
          reach them being refused earlier as no longer available to match, and `record_match` takes no
          claims (**R-CC144**, amending **bank_import:R-FY**; closed **CC-386**). **R-CC135**'s warning
          on the reconcile panel: a match naming several rows' payments is named under each row it
          needs, with the rows that must all close, from one read
          (`match_withdrawal.pending_alone_and_together`), and posts its lines with those rows
          (`shared_lines`); the save's one press is graded against the lines named for the rows ticked,
          a shared match's only when all its rows are (`NamedLines.for_ticks`), so ticking both rows of
          a shared match saves and frees its line (closed **CC-384**). Applications under
          **balance:R-BAL207**: the rows a shared line needs are the page's claim, posted back and never
          re-derived at the save, and a partner row's paycheck is printed where another row on the list
          shares its name. Left open: **balance:BAL-601** (a shared match's withdrawal event names only
          the last row, `balance:X-dc`). **4a-5c-2c-2** carries the `Ships:`: carry-forward's Confirm
          with its caption, post-back and redraw, its `Silent("CC-364")` press a promised `Shown` one,
          closing **CC-364**; the `Silent` sweep and the docstring rewrite.

## The ledger row `credit_card:CC-364` as it stood

    | credit_card | CC-364 (`credit_card:CC-5-4a-4`'s trace 2026-09-23, the coordinator's question; disposition **R-CC76**) | -- | The reconcile panel's `$0.00` box (`accounts/_reconcile_panel.html:76`) or reopened-envelope tick and carry-forward (`carry_forward_service/_execute.py:575`) withdraw a kept payment and any match naming it through the seam's `$0.00` / purchases record (`status_seam/_covering.py:581`, `:821`) with no caption, and every row of the "Paid from this account" list (`credit_card:CC-5-4b`) carries one. | a match withdrawn with no caption, its bank line unexplained again with nothing said before the press (the withdrawal is logged); `$0.00` misstated | **OPEN, born with an owner** (developer 2026-09-23, **R-CC76**). Re-worded, re-pointed and widened 2026-09-25 at the tick of `credit_card:CC-5-4b`, whose commit message carries its evidence | CC-5-4a-5 (closed in its leaf 4a-5c-2c-2, by carry-forward's Confirm) |
