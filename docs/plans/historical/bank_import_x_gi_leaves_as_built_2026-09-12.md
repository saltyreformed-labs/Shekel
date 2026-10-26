> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The shipped `bank_import:X-gi` leaves and `X-gm`, as built

The as-built records of the nine shipped steps of the exception queue's retirement -- `X-gi-1`,
`X-gi-2`, `X-go`, `X-gq`, `X-gp`, `X-gi-2a`, `X-gi-3`, `X-gi-4` and `X-gm` -- exactly as
`docs/plans/implementation_plan_bank_import.md` carried them until 2026-09-12, when `X-gi-4`'s tick
put that document within 20 lines of its cap and `docs/plans/conventions.md` rule 5 archived the
completed span. `X-gi-5` was still open, so the container `X-gi` itself is not archived; each step
below keeps one condensed entry in the live document, carrying any obligation a live step depends
on. Cite this for how a decision came to be, never for what is true now.

  - [x] **X-gi-1** `8543c80f` -- the four remaining links point at Reconcile and the MATCH pane
        works unscripted behind `?open=<line_id>` (**R-KA**, **R-BI1**), offering EVERY unexplained
        row because that render has no search. Both obligations it left `X-gi-2` are DISCHARGED:
        that step pruned the macro and left the file, and re-owned **BI-478** to `X-gi-2a`.
  - [x] **X-gi-2** `b462af07` -- **R-HU**'s deletion. Closed **N-404**; PRUNED `match_by_hand` per
        `X-gi-1`'s obligation; moved the `not_shown_alone` render coverage onto the Reconcile pane;
        filed **BI-479** and **BI-480**, both owned by `X-gi-3`.
  - [x] **X-go** `20f96d90` -- `_MAX_MATCH_MEMBERS` deleted (**R-BI2**). Two bounds already held its
        stated reason: `resolve_rows` and `load_lines` refuse anything the pass did not offer, and
        `MAX_FORM_MEMORY_SIZE` (500,000, Flask's default, unset here) refuses the body first.
        **It could never bind in aggregate** -- 500 items x 100 members is 50,000 ticks against a
        body carrying 22,727 -- so the door's worst case is IDENTICAL before and after, and the cap
        had begun to contradict a pane offering every unexplained row as a tickbox (**R-HW**).
  - [x] **X-gq** `86f8620d` + `be59238e` -- the LANDING out of `_variance.py` (999 -> 690 lines)
        into `_landing.py`, on the seam that module's own first line names, for the headroom the
        consent gate's rewrite needs. **A LATER SPLIT MUST OBEY**: a byte-pure move cannot keep a
        REFERENCE true across a module boundary -- nine broke here, seven unqualified Sphinx roles
        and two direction words -- and pylint, the suite and byte-identity are all blind to it. The
        docstring was NOT divided; six of eleven paragraphs span both halves (**BI-484**).
  - [x] **X-gp** `40354328` -- ONE control whose options ARE the acts (**R-BI2**, superseding
        **R-IV**): `ReviewedDifference` carries the figure AND the member under one `consent-<line>`
        field, the door compares it whole, the preview reads no consent. Closes **BI-481**: both
        docstrings that named `X-gn` as the step re-arming R-IV's bound were rewritten with the
        bound. **A LATER STEP MUST OBEY**: a pre-X-gp page FAILS CLOSED, since `residual-` and
        `difference_on-` are not read, so a stale tab refuses rather than mints.
  - [x] **X-gi-2a** `8fa4d0bc` -- the `?open=` pane is priced from the SUBMITTED form (`OpenedAsk`;
        `read_match` is ONE reading for the fragment and Apply), so a refused press comes back with
        the rows ticked and the acts offered; closes **BI-478**. **R-BI3**: every still-offered
        control is echoed, the consent on whole-value equality. `asked_to_open` reads through
        `parse_row_id`. Moves no money. **A LATER STEP MUST OBEY**: the SCRIPTED page without
        `?open=` still loses a refused press's ticks (placeholder re-fetch), unfiled.
  - [x] **X-gi-3** `43ce313b` -- `_queue.py`, `_register.py`, THREE `ReviewSet` members (the census
        said four; `unmatched` is LIVE via `card_subject`), plus `answered_merchants` and
        `MerchantRegister`, which the enumeration missed and whose reach survives in
        `merchant_directory`. **A LATER STEP MUST OBEY**: BI-479's remedy text was wrong --
        re-pointing the two schema classes beat deleting them, or live coverage went with the dead
        readers. Closes **BI-479**, **BI-480**; filed **BI-482**..**BI-485**.
  - [x] **X-gi-4** `ba5ae344` -- **N-470**'s two receipt figures rendered and logged; **N-405**
        shipped earlier at `af6f8a3f`; **N-402** closed by **R-BI4**: every `@login_required`
        deleted for ONE default-deny gate (`app/login_gate.py`), the sweep enumerating `url_map`
        (213 protected, 10 public pairs), an unmatched URL bounced too. **A LATER LANE OBEYS**:
        write no decorator; a public route is named in `PUBLIC_ENDPOINTS`. Its reviews filed
        **BI-486**..**BI-490**, owned by the two leaves the developer minted, `X-gr` and `X-gs`.
- [x] **X-gm** `1b722d52` -- the badge and the inbox are ONE producer (**R-KB**, **R-KD**):
      `inbox_partition` is one Python walk that BOTH read, and it owns BOTH halves, moving the
      badge's LINK to Reconcile too. **27 against 18 -> 18 against 18.** Closed **N-476**; deleted
      `impossible_day_count` and two SQL restatements. **A LATER STEP OBEYS TWO**: the walk decides
      what the PROPOSER is given, so removing a line reprices another's row (**R-GD(a)**), and
      `to_explain` counts CARDS where the badge counts LINES -- `X-gn` keeps those equal.
