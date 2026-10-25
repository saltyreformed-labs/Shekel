> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the design of record for the credit card is
> `docs/design/credit_card_from_scratch.md` (ruled 2026-09-18, `credit_card:R-CC14`..`R-CC22`), and the
> code as committed is the source of truth for what the app does.

# The 2026-07-19 credit-card plan: its architecture, its seventeen leaves and its consumer inventory

Archived 2026-09-18 at the RE-MINT (`docs/design/credit_card_from_scratch.md` section 5 holds the
old-leaf -> new-leaf map). Every leaf below was OPEN when it left `steps.md`; none shipped. The
checkboxes are the record's, not a plan's.

## Architecture (decided during planning)

**Two bullets below were SUPERSEDED on 2026-09-18** (`R-CC14`, `R-CC15`, `rulings.md`): the card
rides the shipped cash fold and grows no producer of its own, and a charge is the plan row's
covering movement on the card rather than a retargeted row. They stay as the 2026-07-19 record,
marked.

- **Every card event is a real row on the card account** (SUPERSEDED for purchases by `R-CC15`: a
  charge is a movement on the card, the row never moves): purchases (retargeted or direct), payments
  (transfer shadows), interest charges and reward redemptions (system-generated PROJECTED rows the
  user confirms, projected->done philosophy). The card's past balance is a pure transaction sum;
  Step-2/Step-3 posting writers (`posting_service.py`, sign-based `_signed_cash_leg`, already
  liability-aware) need NO ledger-schema change. No loan-style split writer. The `PostingSourceEnum`
  planned `credit_payback` source dies (docstring).
- **New kind `REVOLVING`**: boolean `has_revolving_credit` on `ref.account_types`; classifier
  precedence AMORTIZING -> REVOLVING -> INTEREST -> APPRECIATING -> INVESTMENT -> PLAIN
  (`app/services/account_projection.py`). Seeded Credit Card row gains the flag via migration;
  `has_parameters` stays False.
- **Satellite `budget.credit_card_params`** (1:1 account_id CASCADE, loan_params pattern):
  `statement_close_day`/`payment_due_day` (SmallInt, CHECK 1-31, month-end clamp semantics shared
  with the recurrence engine's rule), `min_payment_percent` Numeric(5,4) + `min_payment_floor`
  Numeric(12,2), `cashback_rate` Numeric(5,4) default 0, `auto_redeem_threshold` Numeric(12,2) NULL
  (NULL = manual-only), `credit_limit` Numeric(12,2) NULL (display/utilization only). All CHECKs
  named; row added to `AUDITED_TABLES`. NO auto-create branch (no honest sentinel for the day
  columns) -- a params-less card is a dormant plain liability; every card feature gates on the row.
- **APR rides `budget.rate_history`** (verified: account-scoped, `monthly_pi` nullable, rate CHECKed
  to [0,1], unique (account_id, effective_date); every reader is loan-gated via LoanParams
  resolution, so card rows are invisible to loan code). Card gets its own thin loader + write route;
  `loan_features.py` docstrings updated to say "loan or card APR".
- **The card must NOT ride `calculate_balances`** (Projected-only premise + cash D1 would drop
  settled post-anchor purchases -- a card owner does not re-anchor daily). SUPERSEDED by `R-CC14`:
  the card rides the shipped cash fold (`cash_ledger/_walk.py` + `balance_at/_assertions.py` +
  `_fold.sample_cumulative`) as PLAIN does today; the 2026-07-19 text gave REVOLVING its own TOTAL
  fold producer sharing `account_posting_service/_walk.py`'s core, a producer `X-f4` deletes.
- **Context memos use the C8d injection shape**: `BalanceContext` memos inject their deriver (the
  `loan_payoff(account, derive)` precedent) instead of lazy-importing the seam -- the lazy-import
  shape closed real import cycles. Applies to CC4b's derived payment (CC1b is dissolved).
- **Sign convention**: the card balance stays a negative cash-style number; `_liability.py`
  magnitude and net-worth abs rules unchanged.
- **Scope exclusions**: no cash advances (transfers OUT of the card rejected, sibling of the loan
  guard), no balance transfers, no category reward rates, no promo APRs.
- **System-row marker**: nullable `transactions.system_origin_id` FK to new seeded
  `ref.transaction_origins` (`finance_charge`, `reward_redemption`) -- IDs-for-logic; category
  matching rejected (user-editable names).
- **Multi-card policy**: zero cards -> action hidden/ValidationError; one -> it; multiple ->
  explicit picker; keyboard `c`/palette default = lowest `sort_order` then `id`.

## The steps (each commit independently green + revertable; additive-first)

### Phase 0 -- Foundation

- [ ] **CC0a** `feat(ref): account types carry a revolving-credit kind` -- migration adds
      `has_revolving_credit` (server_default false) + updates the Credit Card row (name-match legal
      in migration); model + all 19 `ACCT_TYPE_SEEDS` tuples + `_seed_account_types` upsert;
      `AccountProjectionKind.REVOLVING` + classifier branch; behavior-preserving shims (scalar
      `balance_at` REVOLVING joins PLAIN's date-precise cash branch); audit every `classify_account`
      call site for no-op (32 on 2026-09-18, not the ~12 written here; two kind-keyed tables fail
      loud on a new member: `routes/grid/_shared.py:248`,
      `ledger_account_service/_counters.py:141`). Its design loop opens on whether REVOLVING is a
      projection KIND at all now the seam is kind-blind (the trace's question, 2026-09-18). Oracle:
      byte-identical pre/post parity for a Credit Card account's scalar/map/grid; classifier
      precedence tests incl. the both-flags pathological type; migration up+down + template rebuild.
- [ ] **CC0b** `feat(cards): budget.credit_card_params satellite` -- model + migration as specced in
      Architecture; constraint negative controls; inert by design.
- [ ] **CC0c** `feat(cards): card params setup flow` -- create/update routes + Marshmallow schema
  (percent->fraction `@pre_load`, E-28); `_setup_redirect_url` REVOLVING branch -> cash_detail
  until Phase 6; params-absent dormancy pinned by test.

### Phase 1 -- The revolving balance producer (DISSOLVED to one leaf, R-CC14, 2026-09-18)

- **CC1a and CC1b are DISSOLVED** (**R-CC14**): the card keeps riding the shipped cash fold
  (`cash_ledger/_walk.py` + `balance_at/_assertions.py` + `_fold.sample_cumulative`, through
  `balance_at/_asset_fold.py`), which is the core `CC1a` was to consume and the three-tier stream
  `CC1b`'s oracle matrix restated; built as written `CC1b` would have been a second walk against the
  reset semantics the flip deletes. No code; the card inherits the flip by construction.
- [ ] **CC1c** `feat(balance): the liability band reads the fold's forward values` -- ONE surface:
      `balance_at/_liability.py`'s flat hold for a non-loan liability (its docstring: "the ONE place
      that changes"). The other three surfaces the 2026-07-19 entry named are already the fold:
      `_account_balance_map` has one branch (the configured loan), the scalar and
      `grid_balance_view` are kind-blind, and `stale_anchor_warning` no longer exists. Moved numbers
      individually explained (the balance README Section 7.1 standard); dev-clone live-render; fence
      classification tests updated.

### Phase 2 -- Statement math (pure, no Flask/db -- the rate_period_engine discipline)

- [ ] **CC2a** `feat(cards): the statement cycle is a pure derivation` --
  `app/services/card_statement.py`: `cycle_window`/`statement_sequence` (month-end clamp
  shared/pinned vs the recurrence engine), `due_date_for`, `statement_balance` (fold through
  close), `grace_kept`, `minimum_payment` (= max(floor, round_money(pct x bal)) clamped to
  balance). Oracles: Feb-31st clamp, leap year, due-before/after-close, paid-in-full vs
  one-cent-short (control fires), floor crossover, zero.
- [ ] **CC2b** `feat(cards): the finance charge folds the daily balance` --
      `finance_charge(events, cycle, rate_records)`: day-by-day balance, APR segments
      effective-dated, DPR = APR/365, average daily balance, purchases join the ADB on grace loss
      (ruling **R-CC8**); grace kept => `0.00`. Oracles: hand-computed ADB with a mid-cycle
      purchase; APR change mid-cycle; boundary days of the closed-open cycle window.
- [ ] **CC2c** `feat(cards): card APR history rides rate_history` -- card-gated write route + schema
      + `load_card_rate_records`; docstring updates; double-submit uniqueness test; pin that the
      loan loaders' account set (LoanParams-driven) never contains the card.

### Phase 3 -- The charge, the migration, the deletion (the 2026-07-19 re-account shape, see below)

**R-CC15 (2026-09-18) re-opens R-CC1 and R-CC9; the three entries below are the 2026-07-19 shape and
stand as HISTORY where they move the row or write provenance:** a charge is the plan row's covering
MOVEMENT on the card, the row never moves, and a card-tender purchase is a movement on the card that
clears on the card's statement line by line. The shape needs `fk_transaction_entries_parent_account`
relaxed, a fork the balance chain's design loop hears first (recorded on `X-bi-4`'s entry in the
balance README); each leaf's own design loop restates its entry. The trace's finding for `CC3b`:
`paid_at` is a column `X-f1b` deleted -- a settle is `settled_on` + `settled_day_basis_id`,
`settled_amount` + `settled_basis_id` and the covering movement, written as ONE act the way
`status_seam.apply_status_change` writes them, and WHICH day a migrated pair settles on is the
ruling `CC3b` owes (13 of the 14 link-less Credit rows were undated on the 2026-09-12 restore); its
in-migration postings are redundant with the deploy resync.

- [ ] **CC3a** `feat(cards): charge-to-card (additive)` -- migration:
      `transactions.charged_from_account_id` (NULL FK SET NULL, partial index) = undo provenance.
      `card_charge_service.py`: `charge_to_card(txn_id, user_id, card_account_id)` -- row lock
      (generalize `lock_source_transaction_for_payback` into a shared home now; rationale carries
      verbatim), guards (expense-only, not shadow, not tracks_purchases, Projected-only, card
      ownership + REVOLVING check), then set provenance, retarget `account_id`, settle via
      `status_seam.apply_status_change(txn, DONE)`, reconcile postings LAST; `uncharge` reverts
      (Paid->Projected legal), moves back, clears provenance, reconciles. Routes beside the
      still-live mark-credit. Oracles: lifecycle, hand-computed posting oracle (card ledger -amount,
      category +amount, checking untouched, trial balance), undo round-trip to zero, C-19-shape
      concurrency, guard controls fire. Hazard checks: `template_id` partial unique unaffected;
      regeneration skips settled.
- [ ] **CC3b**
      `feat(cards)!: mark-credit is charge-to-card -- transaction-level cutover + live-pair
      migration` -- <!-- MD013 kept: the backticked commit SUBJECT is 96 characters on its own, so
      at this list's 6-space continuation indent no wrap reaches 100; the real fix is a shorter
      subject, which is CC3b's to decide. --> <!-- rumdl-disable-line MD013 -->
      delete mark/unmark routes + PATCH revert path; state machine: Projected loses `credit`,
      `credit: {credit}` terminal; minimal template/JS cutover (`data-can-charge`, `c` key, palette,
      badges' predicates); Alembic migration with in-migration backfill: per LIVE pair -- resolve
      target card (single; multiple -> lowest sort_order; zero -> CREATE the card inline per ruling
      **R-CC6**), set provenance, retarget, status Paid + documented `paid_at` derivation, emit
      balanced
      settled postings in-migration (loan-backfill precedent), delete payback; frozen settled pairs
      untouched; downgrade restores via provenance. **With `CC3c` it owns `credit_card:N-351`**
      (re-filed from
      `balance:N-243` 2026-09-03): a payback's amount is a stored derivation at both levels and
      carries neither pricing link, and both die with the payback shape. Rework
      `test_credit_workflow.py` +
      `test_c19_credit_payback_unique.py` (port lock/concurrency shapes). Reports move (settled
      spending now sees real categories at charge time) -- explained in-commit.
- [ ] **CC3c** `feat(cards)!: envelope split tender + renames` -- rewrite `entry_credit_workflow.py`
      -> `entry_card_charge.py`: maintains ONE settled card-side EXPENSE = credit-entry sum (same
      period/scenario/category, provenance set), linked via the RENAMED `card_charge_for_id` +
      partial unique index + `TransactionEntry.card_charge_id`; `is_credit -> is_card_tender`
      (ruling **R-CC7**; `Review:` lines; index/constraint renames in the same migration). The 2x2
      sync matrix carries over under the shared lock; sum==0 reverse-and-delete. `_signed_cash_leg`
      / `_credit_entry_sum` semantics unchanged (docstrings updated). Migrate live entry paybacks;
      delete `credit_workflow.py` whole. Oracles: hand-computed $500/$120 split tender (checking leg
      $380, card row $120 at the entries' settle instant), toggle shrink/grow, last-entry deletion
      reversal, migration up+down. HARD REQUIREMENT: the companion flow survives intact (see the
      requirements section) -- companion-access test end to end.
- [ ] **CC3d** `feat(cards): the card refuses what it cannot model` -- reject transfers OUT of the
  card (`_reject_transfer_out_of_revolving` at the `create_transfer` chokepoint);
  `active_accounts_query` gains an orthogonal `revolving` filter (default None); salary
  auto-picker passes `revolving=False`; direct entry on the card stays allowed. Residue sweep
  (docstrings, EVT_* events, enum docstring, comments). Every guard's control fires.

### Phase 4 -- The derived statement payment

- [ ] **CC4a** `feat(cards): card_payment_settings` -- 1:1 `transfer_template_id`
  (LoanPaymentSettings shape; generalization argued and rejected -- zero shared fields),
  `payment_mode_id` FK to new seeded `ref.card_payment_modes` (statement_balance /
  minimum_payment / fixed), dual-seeded; AUDITED_TABLES. Creation flow mirrors the loan payment
  transfer flow: monthly rule `day_of_month = payment_due_day`, one active template per card.
  Straddling close/due periods: due-date-wins placement pinned.
- [ ] **CC4b** `feat(cards): the payment you owe is the payment the card derives` -- a CARD rule
      behind the amount resolver (ruling **R-FI**; the balance step that built it is archived, so
      read `app/services/cash_ledger/_amount_source.py` rather than a plan), NOT another entry in
      `live_amount_overrides`, which that arc has already DELETED: projected payment amount =
      statement balance at last close minus `reward_redemption` rows posted since close, floor 0;
      min mode substitutes CC2a's minimum; fixed mode is a template amount the card owns. The card
      row stores no amount, so nothing can hold a figure the derivation contradicts. Oracles:
      derived amount renders identically on grid/card/checking; redemption after close reduces,
      before close does not (both controls); floor-0. **It owns `N-311`** (re-filed from `balance`
      2026-09-03): the CC payback rows do not reconcile to what was actually paid to the card -- one
      ACH per real payment against one payback per purchase, `$280.21` unexplained -- and the
      payment as a CARD rule is the remedy.
- [ ] **CC4c** `feat(cards): underpayment warns and projects its finance charge` -- C7-style warning
      (payment < minimum due) + one-click "pay statement balance" (flips mode);
      `card_recurrence_sync` maintains ONE projected finance-charge expense
      (`system_origin = finance_charge`) in the next cycle when grace fails; idempotent; recompute
      on write-path triggers only (transfer settle/revert, params/APR change); never touches settled
      rows. Oracles: hand-computed charge appears/disappears; settled charge untouched (control);
      confirming the charge posts + folds at its instant.
- [ ] **CC4d** `feat(cards): a finance charge names the relation that prices it` -- give CC4c's
      projected charge its OWN pricing link, a typed FK that `ck_transactions_one_pricing_link`
      counts, so it declares its relation the way every other derived row does.
      **It is this arc's half of `balance:X-au-l`**, which deletes `amount_source_id` on the ground
      that the relation is derivable from the link a row carries (**balance:R-IY**, `CLAUDE.md` rule
      14). **N-264** was written as the reason that column must SURVIVE --
      *a derived row that no pricing link can reach* -- and the developer ruled its other reading:
      the arc is missing a leg, not short a discriminator. Closes **N-264**, and `balance:X-au-l` is
      gated on this.

### Phase 5 -- Rewards

- [ ] **CC5a** `feat(cards): rewards accrue as a derived figure` -- the
      `transactions.system_origin_id` and `ref.transaction_origins` migration (this or CC4c,
      whichever lands first carries it); pure accrual:
      `round_money(rate x sum(settled purchases)) - sum(redemptions)`, purchases excluding both
      shadows and system rows; rounding on-the-sum, documented. W9909: classify non-producer
      (rewards earned, not balance-at-T). Hand-computed mixed-stream oracle.
- [ ] **CC5b** `feat(cards): redemptions -- manual + auto-redeem threshold` -- manual route (settled
      income row, `system_origin = reward_redemption`, `0 < amt <= accrued`); auto: the sync
      generates ONE projected redemption when accrued >= threshold (NULL = off), one-live-row
      invariant under the C-19 concurrency shape; confirmed via ordinary mark-done. Controls:
      threshold minus one cent does not fire; over-accrued rejected; confirmed redemption moves the
      fold + the next derived payment.

### Phase 6 -- UI (outline; a later shekel-design loop, per-screen audits + dev-clone verify)

Card cockpit page (statement hero, close/due chips, min due, grace state, utilization vs limit, APR
history, rewards chip + redeem, payment setup/track) replacing cash_detail for REVOLVING; grid
affordances (charge picker popover, card badge replacing CC/payback badges, what the source grid
shows once `CC3a`'s loop answers re-opened R-CC9); savings cockpit tile states; palette polish.
Every screen through the `shekel-design` loop with its own dev-clone live-verify.

## Consumer inventory (cutover checklist; grep-verified 2026-07-19)

Python: `credit_workflow.py` (delete), `entry_credit_workflow.py` (rewrite),
`routes/transactions/mutations.py` (PATCH revert, delete teardown, mark/unmark),
`routes/transactions/_helpers.py`, carry_forward (route + service `_context.py`),
`state_machine.py`, `status_seam/` (doc), `entry_service.py`, `pay_period_admin.py` (comments;
behavior stays correct), `spending_analysis.py`, `posting_service.py` + `posting_reads.py` (docs),
`db_errors.py`, `log_events.py` (EVT_CREDIT_* -> EVT_CARD_*), `balance_predicates.py` (stays;
historical-only note), `models/transaction.py` (index + FK renamed), `models/transaction_entry.py`
(renamed), `jinja_globals.py`, `enums.py` (doc), `schemas/validation/entries.py`,
`routes/companion.py` + the entries blueprint (companion access path). Templates:
`grid/_transaction_cell.html`, `grid/_grid_row_macros.html`, `grid/_mobile_card_actions.html`,
`grid/_transaction_full_edit.html`, `grid/_transaction_quick_edit.html`,
`grid/_transaction_entries.html`, `_keyboard_help.html`, `analytics/_balance_sheet.html`,
`accounts/cash_detail.html`, `savings/_cockpit.html`. JS: `app.js` (markTxnCredit + `c` key);
`command_palette.js` (Credit command, badge glyph). Tests:
`tests/test_services/test_credit_workflow.py`, `test_entry_credit_workflow.py` (1,789),
`test_c19_credit_payback_unique.py` (1,144) -- reworked per CC3a-c; plus grid-template,
state-machine, carry-forward, posting-lifecycle suites asserting Credit shapes.
