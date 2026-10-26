> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The Reconcile page, as built: X-gj-1 and its three leaves (2026-08-29)

**Ticked at `a43e8e2f`** with its third leaf. Condensed out of
`implementation_plan_bank_import.md` under `conventions.md` rule 5 on 2026-09-02, when the SKIP
verb's decomposition (`X-gj-4`, rulings `bank_import:R-JG`..`R-JI`) needed the document's headroom:
it stood at 238 lines against an effective cap of 240, so the next entry of any size had to buy its
room rather than raise the cap.

Rule 5's three conditions hold. Every finding this span did not close is a live `ledger.md` row
(**N-404**, **N-405**). One sentence here IS depended on and was therefore kept in the live
document rather than archived -- *`X-gi` must take the review QUEUE with the register*, because
that page still links to it three times and deleting one route alone breaks the other's header
button. Nothing below was re-verified for this archive; it is the text as it stood.

  - [x] **X-gj-1** `a43e8e2f` -- the DECOMPOSED parent of the page, split three ways 2026-08-29 on
        the two boundaries this package draws: the SERVICES boundary, and **R-GX**'s -- a line to
        explain is `_reads`', an applied act is `_accepted_view`'s. It ticked with its third leaf.
        **It posted only to doors that exist** (`apply_reviewed`, `release_match`, `state_rules`)
        and added no money door. Each leaf shipped a WHOLE page: none rendered a tab or control a
        later one completes, which **R-HW** forbids.
    - [x] **X-gj-1a** `bc851df9` -- the service turning a pass into CARDS: one per line with its
          verb (**R-HP**), what suggested it, the sentence's PARTS, which verbs are OPEN and why a
          shut one is (**R-HW**), the settled act's card one tense over, the tab counts, the chips
          (**R-HQ**), and `bank_agreement`'s HEADLINE DAY -- the latest COMPARED day the bank's
          record can PRICE, not `span.last_day`.
    - [x] **X-gj-1b** `cfcfcac9` -- the page over that model and the three BANK-LINE tabs: the route
          pair, the hero with the last import's provenance, chips, legend, tab bar, the card and its
          opened four-tab panel on ONE footer (**R-HR**, **R-HW**), Find and Match, and the sweeps
          and Apply (**R-FZ(c)**, **R-HD**). The card's always-for-this-merchant checkbox is DELETED
          (`bank_import:R-IB`) and the accept door exempts no shape (`bank_import:R-IA`). Minted
          **X-gk** (`bank_import:R-IC`).
    - [x] **X-gj-1c** `a43e8e2f` -- the two settled tabs, each act's Undo, and the register RETIRED
          as a page (**R-HU**): its acts, its bound, its *show the other N* link and its Undo are
          the tabs'. The Explained CHIP was DELETED rather than re-pointed -- its count is the union
          of two tabs. Shut **N-389** and **N-403**; opened **N-404**, **N-405**.
          **`X-gi` must take the review QUEUE with the register**: that page still links to it three
          times, so deleting one route alone breaks the other's header button.


# The inflow answer, as built: X-gj-2's five leaves (2026-08-31 to 2026-09-01)

**Ticked at `a23315dc`** with `X-gj-2b-3`. Condensed out of the live plan on 2026-09-02 in the same
pass and for the same reason as the span above. The parent `X-gj-2` is TICKED in the same commit: every one of its
leaves had shipped and a decomposed parent ticks with its last, so it takes `a23315dc`. It had been
reading `ticks with #1` -- a rank naming a step in another span. An earlier draft of this line
cited rule 12 as the reason to leave it unticked; rule 12 grades index against specification and
says nothing about tick state.

Its one finding that remains live is `ledger.md` row **N-420**. **N-435** was closed by ruling
`balance:R-IR` and **N-411** was opened and spent inside `X-gj-2b-1` -- an earlier draft of this
line called N-435 live, which adversarial review caught. Nothing below was re-verified for this archive.

    - [x] **X-gj-2a -- a standing rule answers a DEPOSIT.** `751eba5d`. The fifth
          :class:`RuleAnswer` and `merchant_rules.income_category_id`, auto-applied at import.
          **A LATER step must obey:** a merchant CREDIT is reported UNRESOLVED and never filed here
          (**R-HX**) -- `X-gj-2b` is what files it.
    - [x] **X-gj-2b** `a23315dc` -- the DECOMPOSED parent of the refund filing, split three ways
          during the build; ticked with its third leaf. Ruled **R-IK**, **R-IL**, **R-IM**; opened
          **N-420** and **N-435**; **N-411** was opened and spent inside `X-gj-2b-1`.
      - [x] **X-gj-2b-1** `9920bed7` -- `ck_transaction_entries_positive_amount` becomes
            `amount <> 0` (**R-II**), so a merchant credit files as a contra-entry against its
            merchant's envelope: representable, editable and safe, with no rule filing one yet.
      - [x] **X-gj-2b-2** `1bfeff07` -- a standing rule FILES a refund (**R-HT(a)**), auto-applied
            under **R-GH**. A PARTITION CORRECTION and not a new arm: `_creatable_lines`'
            `amount < 0` and `_recordable_inflows`' `amount > 0` chose the pipeline before the
            owner's rule was read at all.
      - [x] **X-gj-2b-3** `a23315dc` -- the census, run twice (by NAME, then by SHAPE) because an
            ABSENCE is what it had to establish. Both hand-entry doors now take a MAGNITUDE and a
            Charge/Refund control; an envelope's budget is a NET target (**R-IK**); the spending
            share divides by what MOVED (**R-IL**); the grid cell goes through `money()` (**R-IM**).
