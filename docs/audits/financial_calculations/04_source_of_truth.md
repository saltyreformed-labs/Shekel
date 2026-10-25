> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 4: Source-of-Truth and Drift Audit

Scope: every stored numeric value that has, or should have, a computational
counterpart, reported per the audit-plan section 4 per-column schema. This file
enumerates the drift surface; it proposes no fixes and edits no source, tests,
migrations, templates, or JS. Findings are grouped into families; each family
is a separate session. This session (P4-a) writes only **Family A - Anchor**.

Developer decisions governing Phase 4 (carried into every family session):

1. **Triage-classify the long tail.** Not every numeric column gets a deep
   finding; columns with an obvious AUTHORITATIVE role and no counterpart are
   recorded briefly. Anchor, loan-principal, and aggregate columns get the full
   schema.
2. **Defer Q-11 / Q-15 / Q-17 and document both interpretations as UNCLEAR.**
   Where a clean classification is blocked on an unanswered developer question,
   the column is classified UNCLEAR and both readings are written out with
   `path:line` evidence; the auditor does not pick a side (audit-plan section 9,
   hard rule 5).
3. **Re-verify Phase 3 + Phase 1.** Every Phase-3 finding cited by a Phase-4
   column is opened at the actual `path:line` and recorded CONFIRMED / MISCITED
   / STALE. A miscited Phase-3 or Phase-1 claim is itself a Phase-4 finding,
   recorded here with a correction question in `09_open_questions.md`; the prior
   phase documents are NOT edited.

Read-only / trust-but-verify note: every `path:line` in this file was opened in
source during P4-a. No claim is inherited from `01_inventory.md`,
`02_concepts.md`, `03_consistency.md`, or any Explore summary without an
independent `grep`/read. The columns were grepped tree-wide across `app/`,
`scripts/`, `migrations/`, `app/templates/`, `app/static/` and reconciled
against the Phase-1 inventory; reconciliation gaps are recorded as findings.

---

## Family A - Anchor

Three stored columns: `budget.accounts.current_anchor_balance`,
`budget.accounts.current_anchor_period_id`,
`budget.account_anchor_history.anchor_balance`. Model read in full
(`app/models/account.py`, 161 lines); no computed `@property` on either model
(CONFIRMS `01_inventory.md` §1.5 "Computed properties: none").

### budget.accounts.current_anchor_balance

- **Represents:** the user-entered real bank balance of an account at its
  anchor pay period; the seed the balance calculator projects forward from
  (`app/models/account.py:51`, `Numeric(12,2)`, **nullable**, no server
  default).
- **Computational counterpart:** NONE. It is an input, not a derivation. No
  service computes or writes it from primaries. `balance_calculator.calculate_balances`
  (`app/services/balance_calculator.py:35`) *consumes* it as `anchor_balance`
  (`:56-59`, `:75`) but never produces it. The nearest sibling is the latest
  `budget.account_anchor_history.anchor_balance` row, written in lockstep at the
  three mutation sites below and *intended* to mirror it (not recompute it).
- **Update paths** (grep-verified, `app/` + `scripts/`):
  - `app/routes/accounts.py:321,327` `create_account` -- constructor
    `current_anchor_balance=Decimal(str(data.pop("anchor_balance","0") or "0"))`.
    **No history row written.**
  - `app/routes/accounts.py:452-459` `update_account` --
    `account.current_anchor_balance = new_anchor`, guarded by
    `new_anchor != account.current_anchor_balance` (`:456`); period + history
    only inside `if current_period:` (`:460-467`).
  - `app/routes/accounts.py:774,793` `inline_anchor_update` --
    `account.current_anchor_balance = new_balance`; period + history only inside
    `if current_period:` (`:794-801`).
  - `app/routes/accounts.py:1083,1106` `true_up` --
    `account.current_anchor_balance = new_balance`; period + history
    unconditional (`:1107-1115`); `current_period is None` -> HTTP 400 at
    `:1102-1103`, so true_up is the only writer that cannot desync.
  - `app/services/auth_service.py:781-786` -- default "Checking" account at
    registration: `current_anchor_balance=0`, period unset (NULL), **no history
    row.**
  - `scripts/seed_user.py:147` (`current_anchor_balance=0`),
    `scripts/benchmark_triggers.py:63` (scaffolding).
- **Direct-read paths** (no counterpart exists, so every read is direct):
  - service, engine seed: `balance_calculator.py:56-59` (via callers);
    `savings_dashboard_service.py:325`; `dashboard_service.py:350,353,700`;
    `calendar_service.py:483`;
    `year_end_summary_service.py:1123-1124,1244,1784,1806,1861,2096`;
    `retirement_dashboard_service.py:322,405,442`.
  - service, **DIRECT display (engine bypassed)**:
    `savings_dashboard_service.py:921` (archived accounts show
    `acct.current_anchor_balance or Decimal("0.00")` AS the current balance);
    `dashboard_service.py:353,499` (fallback `or _ZERO`);
    `year_end_summary_service.py:1784,1806,1861`
    (`return account.current_anchor_balance or ZERO`).
  - route: `grid.py:238,443`; `accounts.py:1283,1418`; `investment.py:86,405`.
  - template (raw stored column rendered -- legitimate, it is the edited
    field): `accounts/_anchor_cell.html:48`; `accounts/list.html:109-110`;
    `accounts/form.html:57`; `grid/_anchor_edit.html:53`; `loan/setup.html:41`.
  - script: `scripts/integrity_check.py:293,295-296` (check BA-01).
- **Drift risk (worked example):** new user registers ->
  `auth_service.py:785` sets `current_anchor_balance=0`,
  `current_anchor_period_id=NULL`, zero history rows. User edits the inline
  anchor on `/accounts` to `$1,000.00` while
  `pay_period_service.get_current_period` returns None (no period seeded yet):
  `inline_anchor_update` sets `current_anchor_balance=1000.00`
  (`accounts.py:793`) but `:794-801` is skipped -- period stays NULL, no
  history row. State: balance `$1,000.00`, period NULL, history `[]`. One
  stored value, six surfaces, four outputs:
  - grid -> **blank** balance row (`grid.py:239` passes NULL period;
    `calculate_balances` `:72`/`:82-84` returns an empty dict);
  - `/accounts` checking detail -> projection seeded with `$1,000.00` at the
    *current* period (`accounts.py:1418-1432`, fallback `or current_period.id`);
  - `/savings` -> same `$1,000.00`-at-current-period projection
    (`savings_dashboard_service.py:325-328`);
  - dashboard balance card -> account **omitted** (`dashboard_service.py:683-684`
    `return None`);
  - net worth -> account **omitted** (`year_end_summary_service.py:2065-2066`
    `return None`);
  - calendar month-end -> **`$0.00`** (`calendar_service.py:449-450`
    `return Decimal("0")`).
  `scripts/integrity_check.py` BA-01 (`:292-297`) flags exactly this
  `(balance NOT NULL, period NULL)` state -- but only when an operator runs the
  script by hand. Note the fallback is not literally "$0.00 anchor" as Q-16
  worded it: `accounts.py:1418` / `savings_dashboard_service.py:325` read
  `current_anchor_balance or Decimal("0.00")`, so the projection is seeded with
  *whatever the stored balance is* ($1,000.00 here), only collapsing to $0.00
  when the column itself is 0/NULL.
- **Stale-detection:** THREE non-interoperating notions; none covers this column
  end-to-end at runtime.
  1. `balance_calculator.stale_anchor_warning` (`balance_calculator.py:92`,
     `:104`, `:109`; docstring `:49-54` "Informational only") -- consumed by
     **the grid only** (`grid.py:243` -> `:359` ->
     `app/templates/grid/grid.html:83`). Every other `calculate_balances`
     caller discards it: `balances, _ =` at `accounts.py:1425`,
     `savings_dashboard_service.py:335`/`:343`, `dashboard_service.py:699`,
     `calendar_service.py:482` (year-end builds a `base_args` dict and likewise
     does not surface it). grep of `stale_anchor` across `app/ scripts/ tests/`
     returns no other consumer.
  2. dashboard age-alert (`dashboard_service.py:271-290`) via
     `_get_last_anchor_date` (`:659-670`, `MAX(account_anchor_history.created_at)`)
     vs `settings.anchor_staleness_days` (default 14). Because
     `create_account` / `auth_service` / `seed_user` write no history row,
     `_get_last_anchor_date` returns None and the dashboard reports
     **"Your checking balance has never been set."** (`:276`) for an account
     whose balance *is* set -- a stored-vs-counterpart mis-signal.
  3. `scripts/integrity_check.py` BA-01/BA-02/BA-05 -- offline; no `app/`
     route or service imports it (grep-verified).
- **Phase-3 cross-ref:** F-001, F-002 (symptom #1), F-003, F-005, F-007, W-277
  all consume this column as the engine seed (re-verification log below).
- **Classification: AUTHORITATIVE.** User-entered ground truth, no
  computational counterpart, the engine treats it as the seed. The *displayed*
  balance derived from it is ambiguous only on the anchor-None case, and that
  ambiguity is a property of `current_anchor_period_id` (next), not this column.
- **Open questions:** Q-16 (sharpened, P4-a); Q-21 (P4-a) -- create/auth/seed
  history gap and the absent DB CHECK.

### budget.accounts.current_anchor_period_id

- **Represents:** FK -> `budget.pay_periods.id`; the pay period at which
  `current_anchor_balance` is "true" (`app/models/account.py:52-54`,
  **nullable**, `ondelete='SET NULL'` -- migration
  `047bfed04987_standardize_ondelete_policies_across_.py:41-43`).
- **Computational counterpart:** NONE. Set to
  `pay_period_service.get_current_period(...)` at write time; never recomputed.
- **Update paths:** `accounts.py:328` (create,
  `current_period.id if current_period else None`), `:461` (update, inside
  `if current_period:`), `:795` (inline, inside `if current_period:`), `:1107`
  (true_up, unconditional); `scripts/benchmark_triggers.py:84`.
  `auth_service.py:781-786` does NOT set it -> **NULL for every default
  account**. `ondelete=SET NULL` (migration `047bfed04987:41-43`) silently
  nulls it if the referenced pay period is deleted.
- **Direct-read paths:** route `grid.py:239,444`; `accounts.py:1284,1419`;
  `investment.py:87,406`. service `savings_dashboard_service.py:326`;
  `dashboard_service.py:683,701`; `calendar_service.py:449,484`;
  `year_end_summary_service.py:1121,1229,1247,1604,1743,1801,1840,2065,2099`;
  `retirement_dashboard_service.py:406`. script
  `integrity_check.py:133-136,295-296,301`.
- **Drift risk:** the NULL state is the **default for every newly-registered
  user** (`auth_service.py:781-786` never sets it) and is re-reachable
  post-creation whenever a balance is edited with no current pay period
  (`accounts.py:460,794` gate the assignment on `if current_period:`). Five
  runtime producers branch THREE ways on NULL (blank / current-period-anchored
  projection / omit-account) plus calendar's `$0.00`; nowhere does the codebase
  state which is intended. `integrity_check` BA-01 (`:292-297`) classifies
  `(balance NOT NULL, period NULL)` -- the literal default new-user state -- as
  a flaggable anomaly, which directly contradicts the runtime fallbacks
  (`accounts.py:1419-1421`, `savings_dashboard_service.py:326-328`) that treat
  it as a routine projection input.
- **Stale-detection:** same three notions as above; only `integrity_check`
  BA-01/BA-02 key on this column, offline only.
- **Phase-3 cross-ref:** F-001 / F-003 / F-007 anchor-handling dimension;
  W-277; Q-16.
- **Classification: UNCLEAR.** The codebase does not define the semantics of
  NULL; five consumers implement three behaviors and an offline check labels
  the state an anomaly while the runtime fallbacks treat it as normal. Both
  interpretations (not adjudicated -- audit-plan section 9):
  - **Interpretation A (NULL = "no usable balance yet"):** omit/blank is
    correct (`dashboard_service.py:683-684`,
    `year_end_summary_service.py:2065-2066`, `grid.py:239`->empty,
    `calendar_service.py:449-450`); the `/accounts`+`/savings` current-period
    fallback (`accounts.py:1419-1421`, `savings_dashboard_service.py:326-328`)
    is the bug -- it fabricates a projection from an unset anchor.
    `integrity_check` BA-01 leans this way.
  - **Interpretation B (NULL = "anchor at current period, balance as
    stored"):** the `/accounts`+`/savings` fallback is correct; the omit/blank
    paths are the bug -- they hide an account the user created and set a
    balance on.
- **Open questions:** Q-16 (sharpened, P4-a).

### budget.account_anchor_history.anchor_balance

- **Represents:** append-only audit trail of anchor true-ups, one row per
  `(account, pay_period, balance, UTC-day)` (`app/models/account.py:92-152`;
  partial-unique index `:134-139`). `Numeric(12,2)`, **NOT NULL**
  (`account.py:152`; migration `9dea99d4e33e_initial_schema.py:198`).
- **Computational counterpart:** `budget.accounts.current_anchor_balance` --
  the latest history row for an account is *intended* to equal the live column
  (both written together at `accounts.py:462-467`, `:796-801`, `:1110-1115`).
  Neither is recomputed from the other.
- **Update paths** (INSERT-only; no UPDATE/DELETE except CASCADE on
  account/period delete -- `account.py:81-86,144-150`):
  `accounts.py:462-467` (update_account, conditional on `current_period`),
  `:796-801` (inline_anchor_update, conditional on `current_period`),
  `:1110-1115` (true_up, unconditional). **Not written by** `create_account`,
  `auth_service.py:781-786`, or `seed_user.py:147`.
- **Direct-read paths:** service `dashboard_service.py:665-667`
  (`_get_last_anchor_date` reads only `created_at`, never `anchor_balance`).
  script `integrity_check.py:335-345` (BA-05, >50% jump). **No route, template,
  or JS reads `anchor_balance`; no runtime path reads the value at all** --
  only the timestamp (dashboard age-alert) and the offline BA-05 check.
- **Drift risk:** "latest `history.anchor_balance` == `accounts.current_anchor_balance`"
  is NOT an invariant the code maintains. `create_account`/`auth`/`seed` set
  the column with zero history rows; `update_account` and
  `inline_anchor_update` skip the history INSERT when no current pay period
  exists (`accounts.py:460,794`) while still mutating the column. After the
  worked example above, history is `[]` while the column is `$1,000.00` -- the
  audit trail under-records. No code reconciles them; the
  `uq_anchor_history_account_period_balance_day` index (`account.py:134-139`)
  only blocks same-day literal duplicates, not gaps.
- **Stale-detection:** BA-05 (`integrity_check.py:333-346`) is the only check
  on the value, offline. The dashboard reads `created_at`, not the balance. No
  runtime consumer of the value.
- **Phase-3 cross-ref:** indirect -- underpins the dashboard "never been set"
  mis-alert noted under `current_anchor_balance`, which interacts with the
  F-001 family's anchor-None display divergence.
- **Classification: CACHED.** An append-only mirror of `current_anchor_balance`
  writes whose sync invariant is not enforced (create/auth/seed/no-current-period
  paths bypass the INSERT). A stored-vs-counterpart gap is reachable and only an
  offline script samples it.
- **Open questions:** Q-21 (P4-a) -- is the latest-row mirror a required
  invariant, and should the three non-history write paths emit a t0 row.

### Phase-3 re-verification log (Family A)

Anchor-column citations only. The entries-`selectinload` axis and the
dual-per-account-dispatch axis (Q-15) are not Family-A column matters; they are
cross-referenced, not re-judged here.

- **F-001 account_balance** -- `grid.py:238-241` **CONFIRMED** (ternary falls
  back to `current_period.id` only when `account` is falsy, NOT when the period
  id is NULL; an account with NULL period yields an empty balances dict).
  `accounts.py:1418-1421` **CONFIRMED**. `savings_dashboard_service.py:325-328`
  **CONFIRMED**. `dashboard_service.py:683-684` **CONFIRMED** (`return None`).
  `year_end_summary_service.py:2065-2066` **CONFIRMED** (`return None`).
- **F-002 checking_balance** -- anchor citations identical to F-001;
  **CONFIRMED**. (Symptom-#1 entries-load axis is not a Family-A column issue.)
- **F-003 projected_end_balance** -- `grid.py:238-241`,
  `accounts.py:1418-1432`, `savings_dashboard_service.py:325-352`,
  `dashboard_service.py:683-705` all **CONFIRMED**.
- **F-005 chart_balance_series** -- no anchor-column claim beyond inherited
  F-002; nothing to re-verify at column level (CONFIRMED by inheritance).
- **F-007 savings_total** -- `savings_dashboard_service.py:326-328`
  **CONFIRMED**; `year_end_summary_service.py:2065-2066` **CONFIRMED**.
  Additional: `savings_dashboard_service.py:921` shows archived-account balance
  directly from the stored column, bypassing the engine -- an engine-bypass
  read F-007 did not call out (recorded under `current_anchor_balance`
  direct-read paths).
- **W-277 calendar month-end** -- `calendar_service.py:449-450` **CONFIRMED**
  (anchor-None -> `Decimal("0")`); period selection **CONFIRMED**: the loop is
  at `:463-466` (`for p in all_periods: if p.end_date <= last_day:
  target_period = p`), Phase-3's cite of `:461-466` spans the comment at `:461`
  plus the loop -- citation acceptable, behavior exactly as W-277 described
  (LAST period ending on/before month-end); `:482-487` **CONFIRMED**
  (`calculate_balances(account.current_anchor_balance,
  account.current_anchor_period_id, all_periods, all_txns)`); `:489`
  **CONFIRMED** (`balances.get(target_period.id, Decimal("0"))`).
- **MISCITE (Phase-1, not Phase-3) -- recorded as a Phase-4 finding.**
  `01_inventory.md` §1.5 `app/models/account.py` block records the CHECK
  constraint for `current_anchor_balance` and `anchor_balance` as
  "MIGRATION (not in model)". Verified **FALSE**: no CHECK constraint exists
  for either column in `app/models/account.py` OR in
  `migrations/versions/9dea99d4e33e_initial_schema.py:177-208` (both columns
  declared bare `sa.Numeric(precision=12, scale=2)`; a grep of `migrations/`
  for an anchor-related CHECK returns only unrelated docstring text). This
  violates `docs/coding-standards.md` "CHECK constraints on every financial
  column". The §1.5 block also **OMITS `current_anchor_period_id`** entirely
  (it lists only `current_anchor_balance`, `sort_order`, `version_id`,
  `AccountAnchorHistory.anchor_balance`). Both are Phase-1 accuracy/completeness
  gaps; per protocol `01_inventory.md` is NOT edited; correction question filed
  as Q-21 in `09_open_questions.md`.

---

## Family B - Loan principal

Session P4-b1 (highest-risk session; symptom #3 / E-03 root-cause). Two stored
columns: `budget.loan_params.current_principal`,
`budget.loan_params.original_principal`. `app/models/loan_params.py` read in
full (74 lines): only `__repr__`, **no computed `@property`/`@hybrid_property`**
(CONFIRMS `01_inventory.md` §1.5 `loan_params.py` "Computed properties: none").
Rate/escrow columns (`interest_rate`, `RateHistory.interest_rate`,
`EscrowComponent.annual_amount`) are session **P4-b2** and are NOT classified
here; they are cross-referenced only.

Read-only / trust-but-verify: every `path:line` below was opened in source
during P4-b1. The two columns were grepped tree-wide
(`grep -rn "current_principal" / "original_principal" app/ scripts/ migrations/
app/templates/ app/static/`) and the settle path independently traced; no claim
is inherited from `03_consistency.md`, `02_concepts.md`, `01_inventory.md`, or
any Explore summary without an independent read. The F-014 "no settle-driven
update path" grep was **re-run from scratch**, not taken on trust.

### budget.loan_params.current_principal

- **Represents:** the user-entered current outstanding balance of an
  amortizing loan (`app/models/loan_params.py:54`, `Numeric(12,2)`, **NOT
  NULL**, no server default). DB CHECK `current_principal >= 0`
  (`loan_params.py:31-34`, named `ck_loan_params_curr_principal`; also in
  migration `dc46e02d15b4_add_check_constraints_to_loan_params_.py:28`). Per
  A-04 (`09_open_questions.md:137-158`) it carries a **dual role**: the
  authoritative anchor for ARM loans, a display mirror for fixed-rate loans.
- **Computational counterpart:** split by loan type, and this split is the
  crux of the finding.
  - **Fixed-rate:** counterpart EXISTS, computed two independent ways:
    (i) `get_loan_projection().current_balance` engine-walk
    (`amortization_engine.py:980-984`: `cur_balance = current_principal`
    fallback at `:980`, then `for row in reversed(schedule): if
    row.is_confirmed: cur_balance = row.remaining_balance; break` at
    `:981-984`); (ii) `_compute_real_principal`
    (`app/routes/debt_strategy.py:147-197`), an independent confirmed-payment
    replay. The two replays consume DIFFERENT payment preprocessing (see the
    settle-update trace below).
  - **ARM:** counterpart does NOT exist. `get_loan_projection` returns
    `cur_balance = current_principal` (`amortization_engine.py:977-978`);
    `_compute_real_principal` returns `principal` unchanged
    (`debt_strategy.py:169-173`). The column is its own value; nothing
    recomputes it from primaries (A-04, `arm_anchor` 3F: origination-forward
    replay is mathematically wrong without complete rate history).
- **Update paths** (grep-verified; `grep -rEn "\.current_principal\s*=[^=]"
  app/ scripts/ --include='*.py'` returns **ZERO attribute-write matches**):
  - **Creation:** `app/routes/loan.py:622` `create_params` --
    `params = LoanParams(account_id=account.id, **data)`, `data` validated by
    `LoanParamsCreateSchema` (`app/schemas/validation.py:1444`,
    `required=True, Range(min=0)`).
  - **The only post-creation writer:** `app/routes/loan.py:631-679`
    `update_params`, the manual POST form `/accounts/<id>/loan/params`
    (`@login_required @require_owner`). Body: `_PARAM_FIELDS`
    (`loan.py:668-671`, set literally containing `"current_principal"` at
    `:669`); `for field, value in data.items(): if field in _PARAM_FIELDS:
    setattr(params, field, value)` (`:672-674`); `data` from
    `LoanParamsUpdateSchema.load` (`validation.py:1466`). A human typing into
    the "Current Principal" input (`loan/dashboard.html:160-162`) is the
    **sole** mechanism that moves this column.
  - **Migrations:** `a1b2c3d4e5f6_add_debt_account_tables.py:43,143`,
    `c67773dc7375_unify_loan_params_into_single_table_.py` create/backfill.
  - **NOT written by any settle/status-transition path** (settle-update trace
    below proves the absence).
- **Direct-read paths** (every read; no read goes through a single canonical
  resolver):
  - engine seed / input: `amortization_engine.py:913` (`current_principal =
    Decimal(str(params.current_principal))`), `:926` (ARM anchor
    `anchor_bal = current_principal if is_arm`), `:953` (ARM monthly-payment
    re-amortization input), `:978`, `:980`;
    `balance_calculator.py:226`; `loan_payment_service.py:252`;
    `debt_strategy.py:109,121`; `routes/loan.py:369,477,894,1226`.
  - service, **DIRECT display / aggregate (engine value bypassed):**
    `savings_dashboard_service.py:840` (`principal =
    Decimal(str(lp.current_principal))` -> `total_debt += principal` `:855`,
    the `/savings`+dashboard debt card -- F-008).
  - route -> template, **DIRECT display:** `routes/loan.py:553-557`
    (`render_template("loan/dashboard.html", ... params=params ...)`; `proj`
    computed `:429` but NOT passed for the card) -> `loan/dashboard.html:104`
    (`${{ "{:,.2f}".format(params.current_principal|float) }}`, the bold
    accent card) and `:161-162` (edit-form prefill); `debt_strategy/`
    `dashboard.html:132` (`debt.current_principal`, but `debt.current_principal`
    there is the engine-real `real_principal`, see F-016 P5/6).
- **Drift risk:** see the worked numeric example below. Summary: one
  loan-on-date yields up to **three** displayed principals for a fixed-rate
  loan (stored / A-06-prepared engine-real / RAW-replay engine-real) and a
  single never-moving value for ARM; the most prominent surface (the bold
  dashboard card) is the stalest; no error is raised. Symptom #3 (ARM/fixed
  card never moves on settle) and symptom #5 (`/accounts/<id>/loan` disagrees
  with `/savings`, refinance, debt-strategy, net worth) are both manifestations.
- **Stale-detection:** **NONE.** No `stale_*` flag, no warning, no
  `scripts/integrity_check.py` rule keys on `current_principal` (grep of
  `current_principal` across `scripts/` returns nothing). Contrast Family A's
  `stale_anchor_warning` and `integrity_check` BA-01: the anchor column has
  three (weak, offline) detectors; the loan-principal column has zero. The
  per-surface substitution of a different value is entirely silent.
- **Phase-3 cross-ref:** F-014 (symptom #3 / E-03; CONFIRMED below), F-015
  (stored-vs-fixed-walk SOURCE), F-016 (`loan_principal_displayed`, UNKNOWN /
  Q-11), F-008 (internal stored-vs-engine inside one service), F-017
  (per-period mechanism), loan side of F-001 / F-003. Re-verification log
  below.
- **Classification: UNCLEAR.** The codebase does not treat this column
  consistently as AUTHORITATIVE, CACHED, or DERIVED; A-04 splits it by loan
  type and neither role is implemented end-to-end:
  - **Interpretation A -- AUTHORITATIVE (stored is the maintained truth).**
    E-03 (`00_priors.md:172-176`) says a settled transfer must make the real
    loan principal reflect the principal portion, and explicitly allows
    "writing a stored column" as the mechanism; A-04 says ARM `current_principal`
    is the user-verified source of truth. Under this reading the column SHOULD
    be authoritative and the bug is the missing settle-driven update (F-014)
    plus the fixed-rate engine-walk that diverges from it. Evidence the code
    leans this way: every ARM path reads the stored column and nothing else
    (`amortization_engine.py:978`, `debt_strategy.py:172-173`); the bold card
    renders it directly (`loan/dashboard.html:104`); the `/savings` debt card
    sums it (`savings_dashboard_service.py:840`).
  - **Interpretation B -- CACHED (engine-real is truth; stored is a stale
    mirror).** E-03 equally allows "recomputing from confirmed payments"; for
    fixed-rate loans `get_loan_projection`/`_compute_real_principal` already
    do exactly that (`amortization_engine.py:981-984`,
    `debt_strategy.py:181-195`) and the refinance prefill
    (`loan.py:1087,1095`) and net-worth liability
    (`year_end_summary_service.py:2078-2081`) consume the engine value, not
    the stored column. Under this reading the stored column is a
    creation-time/manual seed that goes stale the moment a payment settles,
    and the bug is the dashboard card showing the stale mirror (Q-11/A-11).
  The auditor does not adjudicate (audit-plan section 9; hard rule 5). The
  ARM-vs-fixed policy split is itself part of why UNCLEAR: Phase-3 A-04 records
  that the dashboard renders the stored column **regardless of loan type**, so
  even the A-04 "ARM=authoritative, fixed=cached" framing is not honored on the
  display side.
- **Open questions:** **Q-22** (new, P4-b1; the column-role question);
  sharpened **Q-11** (which principal the card MUST show), **Q-15** (canonical
  aggregate-debt base), **Q-17** (ARM re-amortization / symptom #4) below.

### budget.loan_params.original_principal

- **Represents:** the loan amount at origination (`app/models/loan_params.py:53`,
  `Numeric(12,2)`, **NOT NULL**, no server default). DB CHECK
  `original_principal > 0` (`loan_params.py:27-30`, named
  `ck_loan_params_orig_principal`; migration
  `dc46e02d15b4_..._loan_params_.py:22`). It is the fixed-rate engine's
  contractual-payment base and the replay's starting balance.
- **Computational counterpart:** **NONE.** It is an origination input, not
  derived from any primary. No service computes or writes it.
- **Update paths:** **creation ONLY.** `app/routes/loan.py:622` `create_params`
  via `LoanParamsCreateSchema` (`validation.py:1440-1443`,
  `required=True, validate.Range(min=Decimal("0"), min_inclusive=False)` --
  the schema rejects 0 to match the DB CHECK `> 0`; the comment at
  `validation.py:1438-1439` documents this as F-107 / C-25 gap-closing).
  **Deliberately immutable post-creation:** absent from `_PARAM_FIELDS`
  (`loan.py:668-671`, `original_principal` is NOT a member) AND absent from
  `LoanParamsUpdateSchema`, whose docstring states verbatim
  "original_principal and origination_date are omitted -- not updatable after
  initial setup" (`validation.py:1457-1458`). Grep proof:
  `grep -rEn "\.original_principal\s*=[^=]" app/ scripts/ --include='*.py'`
  returns **ZERO attribute-write matches**. Migrations
  `a1b2c3d4e5f6:42,142` / `c67773dc7375` create the column.
- **Direct-read paths:** `amortization_engine.py:912` (`orig_principal =
  Decimal(str(params.original_principal))`), `:936` (passed as `original=`,
  but only for fixed-rate -- `original = None if is_arm else orig_principal`
  at `:920`), `:437/:694/:957` (fixed-rate contractual monthly-payment base,
  the A-05 ELSE branch); `debt_strategy.py:179,182,187`
  (`_compute_real_principal` fixed: used as BOTH `current_principal=` and
  `original_principal=` kwargs into `generate_schedule` -- the replay
  intentionally restarts from origination); `routes/loan.py:376,449,888,
  1007,1232`; `balance_calculator.py:232`; `loan_payment_service.py:257`;
  `savings_dashboard_service.py:477,492` (ARM passes `original=None`,
  `:480-492`); `year_end_summary_service.py:860,1459,1472,1477,2078`
  (note `:1472` passes `current_principal=params.original_principal` -- the
  ARM-anchor schedule deliberately seeds the engine's `current_principal`
  parameter with the column value, A-04). Template: `loan/dashboard.html:99`
  ("Original Principal" display), `loan/setup.html:28-32` (creation form).
- **Drift risk:** **no stored-vs-computed drift surface** -- no computational
  counterpart and the column is immutable after creation. The only failure
  mode is a wrong value entered at creation, which silently mis-bases every
  fixed-rate contractual payment (`amortization_engine.py:437,694,957`) and
  every fixed-rate replay start (`:932-936`, `debt_strategy.py:182`); that is
  data-entry correctness, not drift, and is out of the Phase-4 drift mandate.
- **Stale-detection:** N/A (immutable input, no counterpart). The DB CHECK
  `> 0` and the schema `Range(min=0, min_inclusive=False)` are aligned (the
  `validation.py:1438-1439` comment records the schema being tightened
  specifically so the gap surfaces as a 400, not a 500) -- a positive note,
  in contrast to Family A's missing anchor CHECK (Q-21 sub-question 3).
- **Phase-3 cross-ref:** indirect -- it is the fixed-rate base under F-014 /
  F-016 (the replay), F-017 (per-row principal), and the A-05 fixed-rate
  monthly-payment ELSE branch (`09_open_questions.md:182-214`). It is not
  itself a multi-path divergence concept.
- **Classification: AUTHORITATIVE.** User-entered origination ground truth,
  no computational counterpart, deliberately immutable post-creation, schema
  and DB CHECK aligned. The cleanest column in Family B; no UNCLEAR ambiguity.
- **Open questions:** none new for this column. Cross-ref A-05 (the
  monthly-payment call-site audit consumes it) is a Phase-3 / Phase-6 matter.

### Settle-update trace (symptom #3 / E-03 -- the crux)

**Question (E-03, `00_priors.md:172-176`):** when a transfer to a debt account
is settled, does the real loan principal reflect the principal portion? The
plan leaves the mechanism open ("writing a stored column OR recomputing from
confirmed payments"); the audit must determine which the code uses.

**A confirmed payment is** a transfer whose loan-side shadow is INCOME on the
debt account, moved to a settled status (Transfer Invariant 1; E-01: the
transfer reduces checking by the FULL PITI amount; only the principal portion
should reduce loan principal; interest and escrow are recorded but do not).

**Independent grep, re-run this session (not inherited from F-014):**

1. `grep -rEn "\.current_principal\s*=[^=]" app/ scripts/ --include='*.py'`
   -> **ZERO matches.** There is no attribute write to `*.current_principal`
   anywhere in `app/` or `scripts/`. The only write is the indirect
   `setattr(params, field, value)` at `routes/loan.py:674` (manual form).
2. `grep -rn "from app.models.loan_params import LoanParams" app/
   --include='*.py'` -> exactly **6 non-model importers**:
   `routes/accounts.py`, `routes/debt_strategy.py`, `routes/loan.py`,
   `services/loan_payment_service.py`, `services/savings_dashboard_service.py`,
   `services/year_end_summary_service.py`.
3. The 12 settle / status-transition modules --
   `services/transfer_service.py`, `services/transaction_service.py`,
   `services/state_machine.py`, `services/entry_service.py`,
   `services/credit_workflow.py`, `services/entry_credit_workflow.py`,
   `services/carry_forward_service.py`, `services/recurrence_engine.py`,
   `services/transfer_recurrence.py`, `routes/transactions.py`,
   `routes/transfers.py`, `routes/dashboard.py` -- were grepped individually
   for `principal|LoanParams|loan_params`: **every one returns NONE.** None of
   them is in the 6-importer list either.

**Reads of the actual settle code:**

- `transfer_service.update_transfer` (the path a confirmed transfer-to-loan
  takes; settle goes through here, NOT `settle_from_entries`): the status
  block at `app/services/transfer_service.py:497-502` propagates
  `verify_transition(...)` then `xfer.status_id = new_status_id;
  expense_shadow.status_id = new_status_id; income_shadow.status_id =
  new_status_id`. It mutates status / amount / paid_at on the parent and the
  two shadows only. **No `loan_params` access; the module does not import
  `LoanParams`.**
- `transaction_service.settle_from_entries`
  (`app/services/transaction_service.py:38-168`, read in full): writes ONLY
  `txn.status_id` (`:149`), `txn.paid_at` (`:150`), `txn.actual_amount`
  (`:153`). Precondition #3 (`:111-115`) **raises `ValidationError` if
  `txn.transfer_id is not None`** -- a transfer shadow (the loan-side income)
  can never enter this function; transfers settle via
  `transfer_service.update_transfer` (docstring `:76-79`). No principal write.
- The status-transition assignment sites
  (`routes/transactions.py:610`, `:768`; `routes/dashboard.py:125`;
  `credit_workflow.py:201,322`; `transaction_service.py:149`;
  `transfer_service.py:500-502,828`) set `status_id` only; none touches
  `loan_params`.

**Determination (definitive, code-proven):** **No code path recomputes or
writes `budget.loan_params.current_principal` when a transfer to a loan
account settles.** The settle path cannot do so -- it does not import the
`LoanParams` model and there is no attribute write to the column anywhere.
The sole post-creation writer is the manual `update_params` form
(`loan.py:631-679`, `setattr` at `:674`). This is the root-cause evidence for
symptom #3 and the answer to E-03's "which approach": the code uses the
**stored-column** approach for ARM but never maintains the column on settle,
and the **recompute-from-confirmed-payments** approach for fixed-rate but
only on the engine-walk surfaces (refinance, debt-strategy, net worth) and
NOT on the primary dashboard card. Neither approach is implemented
end-to-end; for ARM E-03 is unmet until a human edits the field.

**E-01 split inside `_compute_real_principal` (read in full,
`debt_strategy.py:147-197`):** the function performs **no split itself**.
- ARM branch (`:169-173`): `if params.is_arm: return principal` -- returns the
  stored `current_principal` verbatim. No replay, no principal/interest/escrow
  split.
- Fixed branch (`:175-197`): `payments = get_payment_history(params.account_id,
  scenario_id)` (`:175`) -- **RAW history, no `prepare_payments_for_engine`
  call anywhere in the function body** -- fed directly into
  `generate_schedule(...)` (`:181-190`); then `for row in reversed(schedule):
  if row.is_confirmed: return row.remaining_balance` (`:193-195`), fallback
  `return principal` (`:197`).
- The principal/interest split happens **inside `generate_schedule`**
  (`amortization_engine.py:516-551`): `interest = (balance *
  monthly_rate).quantize(TWO_PLACES, ROUND_HALF_UP)` (`:517`); on a matched
  payment month `total_payment = amount_by_month[month_key]` (`:527`),
  `principal_portion = total_payment - interest` (`:531`), `balance -=
  principal_portion` (`:550`). **Interest correctly does NOT reduce principal**
  -- E-01 part 1 (P&I split) is honored by the engine. **But escrow IS NOT
  subtracted on this path:** `amount_by_month` is the sum of raw
  `PaymentRecord.amount`, so for an escrow-inclusive PITI transfer the engine
  computes `principal_portion = (P&I + escrow) - interest`, attributing the
  escrow dollars to principal paydown. **E-01 part 2 (escrow must not reduce
  loan principal) is VIOLATED in `_compute_real_principal`'s fixed-rate
  replay.** This is independently corroborated by A-06 verification
  (`09_open_questions.md:244-247`), which explicitly lists
  `routes/debt_strategy.py:175, 181` among the two paths that "call
  generate_schedule WITHOUT preprocessing". For ARM the question is moot --
  no replay occurs (returns stored), and the stored column is never reduced by
  settle anyway (symptom #3).

### Worked numeric example (fixed-rate; 3 confirmed escrow-inclusive transfers)

Loan: fixed-rate 30-yr mortgage. `original_principal = $200,000.00`;
`current_principal` STORED `= $200,000.00` (entered at `create_params`, never
edited, never settle-updated). `interest_rate = 6.000%`
(`monthly_rate = 0.005`); `term_months = 360`; `payment_day = 1`;
`origination_date = 2026-01-01`. Contractual P&I =
`calculate_monthly_payment(200000, 0.06, 360) = $1,199.10`. Monthly escrow =
`$300.00`. The user's recurring transfer is PITI = `1199.10 + 300.00 =
$1,499.10`. Three monthly transfers (Jan/Feb/Mar 2026) are confirmed: the
loan-side shadow income is **Settled**.

RAW-replay hand-walk (escrow NOT subtracted -- `_compute_real_principal` /
debt-strategy path), each row quantized ROUND_HALF_UP:

| Mo | open balance | interest (bal*0.005) | principal = 1499.10 - int | close balance |
|----|--------------|----------------------|---------------------------|---------------|
| 1  | 200,000.00   | 1,000.00             | 499.10                    | 199,500.90    |
| 2  | 199,500.90   | 997.50               | 501.60                    | 198,999.30    |
| 3  | 198,999.30   | 995.00               | 504.10                    | 198,495.20    |

A-06-prepared hand-walk (escrow `$300` subtracted upstream -> engine sees P&I
`$1,199.10`; the dashboard/refinance/net-worth path via `load_loan_context`):

| Mo | open balance | interest | principal = 1199.10 - int | close balance |
|----|--------------|----------|---------------------------|---------------|
| 1  | 200,000.00   | 1,000.00 | 199.10                    | 199,800.90    |
| 2  | 199,800.90   | 999.00   | 200.10                    | 199,600.80    |
| 3  | 199,600.80   | 998.00   | 201.10                    | 199,399.70    |

One loan-on-date, seven surfaces:

| | Surface | path:line | Value |
|--|---------|-----------|-------|
| a | STORED `current_principal` | `loan_params.py:54`; only writer `loan.py:674` (never on settle) | **$200,000.00** |
| b | `_compute_real_principal` (RAW replay) | `debt_strategy.py:175-195` (no `prepare_payments_for_engine`) | **$198,495.20** |
| c | engine-walked `get_loan_projection.current_balance` (A-06-prepared via `_load_loan_context`) | `amortization_engine.py:981-984` | **$199,399.70** |
| d | loan dashboard "Current Principal" card | `loan.py:553-557` passes `params=params`; `loan/dashboard.html:104` renders STORED | **$200,000.00** |
| e | refinance prefill | `loan.py:1087` `proj.current_balance`; `:1095`; `:1152`; `_refinance_results.html:69` | **$199,399.70** |
| f | debt-strategy display | `debt_strategy.py:139` `DebtAccount(current_principal=real_principal)`; `debt_strategy/dashboard.html:132` | **$198,495.20** |
| g | net-worth liability | `year_end_summary_service.py:2078-2081` `_schedule_to_period_balance_map(...)` (A-06-prepared schedule) | **$199,399.70** (-class) |

Divergences for this fixed-rate loan, no error raised anywhere:

- **a = d = $200,000.00 and never moves on settle** (symptom #3; E-03 unmet;
  E-04 violated against b/c/e/f/g). The most prominent surface (the bold card,
  `loan/dashboard.html:104`) is the stalest.
- **c = e = g = $199,399.70** -- the E-01-correct figure (escrow excluded).
- **b = f = $198,495.20** -- the RAW replay over-counts principal paydown by
  `199,399.70 - 198,495.20 = $904.50`, i.e. the 3 months of escrow
  (`3 * $300 = $900`) plus `$4.50` of compounding interest-on-the-wrongly-
  removed-escrow. This is the F-014 / F-017 A-06 SCOPE divergence.
- Three distinct displayed principals (`$200,000.00` / `$199,399.70` /
  `$198,495.20`) for one loan-on-date. (The A-06-prepared figure assumes
  `prepare_payments_for_engine` cleanly nets the $300 monthly escrow with no
  biweekly collision; the load-bearing facts are the directions and the
  ~$900 escrow-sized gap, not the final cent.)

**ARM contrast (the developer's symptom-#3 account is "the mortgage
account"):** for an ARM every replay path returns the stored column --
`amortization_engine.py:978` (`cur_balance = current_principal`),
`debt_strategy.py:172-173` (`return principal`),
`savings_dashboard_service.py:480-492` / `year_end_summary_service.py:1466,
1472` (anchor at stored). a = b = c = d = e = f = g = **$200,000.00**, and
NONE moves as transfers settle. The table is degenerate and the symptom is
exactly "the current principal does not update as transfers are made" until a
human edits `update_params`.

### Phase-3 re-verification log (Family B principal)

Loan-principal-column citations only. The per-period interest-base axis
(F-017 Path B), the dual-per-account-dispatch axis (Q-15), and the
checking-account axes of F-001/F-003 are not Family-B-principal column matters;
cross-referenced, not re-judged here.

- **F-014 `loan_principal_real` (symptom #3)** -- **CONFIRMED.**
  `amortization_engine.py:977-984` re-read: `:977` `if is_arm`, `:978`
  `cur_balance = current_principal` (ARM), `:980` `cur_balance =
  current_principal` (fixed fallback), `:981-984` reversed-schedule
  last-`is_confirmed` walk -- exactly as F-014 Path A states.
  `loan.py:553-557` passes `params=params`, `proj` (`:429`) not wired to the
  card -- CONFIRMED (read of the `render_template` call, `:553-575`, lists no
  `current_balance` var). Path C `debt_strategy.py:147-197` read in full --
  CONFIRMED (ARM `:172-173`; fixed RAW `get_payment_history` `:175`,
  `generate_schedule` `:181-190`, walk `:193-195`, fallback `:197`). F-014's
  "no settle-driven update path" grep **independently re-run** -> ZERO
  attribute writes; settle modules do not import `LoanParams` -- CONFIRMED
  and **strengthened** (the proof is stronger than F-014 stated: the settle
  path cannot write the column because it never imports the model).
  `validation.py:1444,1466` two `current_principal` schema declarations --
  CONFIRMED. The constructor-kwarg occurrences F-014 enumerated
  (`debt_strategy.py:139,182`; `loan.py:1001`; `year_end:1472`) are
  `generate_schedule`/`DebtAccount` parameter bindings, not column mutations
  -- CONFIRMED. **Completeness add (not a miscite):** `_compute_real_principal`
  is invoked from `_load_debt_accounts` only when `scenario is not None and
  principal > 0 and remaining > 0` (`debt_strategy.py:119`); otherwise
  `real_principal = principal` (stored) at `:124` -- so even the debt-strategy
  "engine-real" surface falls back to stored under those gates.
- **F-015 `loan_principal_stored`** -- **CONFIRMED.** `loan_params.py:54`
  bare column, CHECK `:31-34` -- read at source, exactly as cited (F-015's
  "`:31-34`" matches the `CheckConstraint` block; the constraint *string*
  `"current_principal >= 0"` is line 32). `amortization_engine.py:926,978`
  ARM stored -- CONFIRMED. The only-writer claim (`update_params`,
  `loan.py:672-674`) -- CONFIRMED.
- **F-016 `loan_principal_displayed`** -- **CONFIRMED** (verdict UNKNOWN
  correctly deferred to Q-11). P1 `loan/dashboard.html:104` STORED, route
  passes `params=params` (`loan.py:553-557`) -- CONFIRMED. P4
  `loan.py:1087` `current_real_principal = proj.current_balance`, `:1095`
  prefill, `:1152` `"current_principal": current_real_principal`,
  `_refinance_results.html:69` -- all read at source, CONFIRMED. P5/6
  `debt_strategy.py:139` `DebtAccount(current_principal=real_principal)` from
  `_compute_real_principal` -- CONFIRMED.
- **F-008 `debt_total`** -- **CONFIRMED** (the internal-inconsistency sub-check
  that holds independent of Q-15). `savings_dashboard_service.py:373`
  `current_bal = proj.current_balance` (engine), vs `:840` `principal =
  Decimal(str(lp.current_principal))` (stored) -> `:855` `total_debt +=
  principal`, both from the same `ad` dict (`:432` `ad["loan_params"]`,
  `:836` `if ad["is_paid_off"]`, `:839` `lp = ad["loan_params"]`) -- all
  re-read at source via `grep -n`, CONFIRMED: one service, one loan, the
  account card uses the engine value while `total_debt` uses the stored
  column.
- **F-017 `principal_paid_per_period`** -- **CONFIRMED** on the engine split
  (Path A): `amortization_engine.py:517` interest quantize ROUND_HALF_UP,
  `:531` `principal_portion = total_payment - interest` (matched-payment
  month), `:566` `principal_portion = monthly_payment - interest` (no-record
  month), `:602` `principal=principal_portion.quantize(TWO_PLACES,
  ROUND_HALF_UP)` -- read at source, exactly as F-017 Path A states. The
  principal-column tie-in `balance_calculator.py:226` (`current_principal`),
  `:232` (`original_principal`) -- CONFIRMED by grep. Path C
  `year_end_summary_service.py:860,865,868,871`
  (`principal_paid = jan1_bal - dec31_bal`) -- CONFIRMED by `grep -n`. The
  Path-B per-period interest-base / A-06 axis is F-017's own matter and is
  P4-b2-adjacent; not re-litigated here.
- **F-001 / F-003 loan side** -- **CONFIRMED** for the principal-column
  citations: `loan/dashboard.html:104` STORED (grep), `proj.current_balance`
  at `savings_dashboard_service.py:373` (grep), schedule-derived at
  `year_end_summary_service.py:2078-2081` with `original =
  params.original_principal if params else ZERO` (`:2078`, read via
  `grep -n`). Three bases for one loan's displayed balance -- CONFIRMED. The
  checking-account / anchor-None axes of F-001/F-003 are Family A / Q-20,
  not re-judged here.
- **Phase-1 reconciliation (positive; contrast Q-21).** `01_inventory.md`
  §1.5 `loan_params.py` block records the CHECK for `original_principal` at
  `loan_params.py:28` and `current_principal` at `loan_params.py:32`, and
  "Computed properties: none". Re-verified against the full model read:
  `loan_params.py:28` is the constraint string `"original_principal > 0"`
  (inside the `CheckConstraint` at `:27-30`), `:32` is
  `"current_principal >= 0"` (inside `:31-34`), and there is no
  `@property`/`@hybrid_property`. **The Phase-1 `loan_params.py` block is
  ACCURATE** -- unlike the `account.py` block (Q-21 sub-question 4, CHECK
  recorded FALSE). The loan-principal columns DO have model + migration CHECK
  constraints (`loan_params.py:27-34`, `dc46e02d15b4:22,28`); no Phase-1
  correction question is needed for Family B.

---

## Family B - Loan rate and escrow

Session P4-b2 (symptom #4 / E-02 / Q-17 -- the ARM monthly payment fluctuating
inside the fixed-rate window). Three stored columns:
`budget.loan_params.interest_rate`, `budget.rate_history.interest_rate`,
`budget.escrow_components.annual_amount`.

Read-only / trust-but-verify: read in full this session --
`amortization_engine.py` (991), `escrow_calculator.py` (115),
`loan_payment_service.py` (353), `loan_params.py` (73),
`loan_features.py` (144: `RateHistory`, `EscrowComponent`; only `__repr__` on
each, **no computed `@property`/`@hybrid_property`** -- NEW verification, this
model was not opened by any prior Phase-4 session); `loan.py`,
`debt_strategy.py`, `savings_dashboard_service.py`,
`year_end_summary_service.py`, `validation.py` read at the cited functions.
The three columns plus `RateHistory`/`rate_history`,
`EscrowComponent`/`escrow_components`, and the
`arm_first_adjustment_months`/`arm_adjustment_interval_months` columns were
grepped tree-wide (`app/ scripts/ migrations/ app/templates/ app/static/`).
No claim is inherited from `03_consistency.md`, `02_concepts.md`,
`01_inventory.md`, or any Explore summary without an independent read; Q-17's
re-amortization claim was **independently re-derived from the engine
source**.

### budget.loan_params.interest_rate

- **Represents:** the loan's *current* annual interest rate, stored as a
  decimal fraction (e.g. `0.06000` for 6.000%), `Numeric(7, 5)`, **NOT NULL**,
  no server default (`app/models/loan_params.py:55`). DB CHECK
  `interest_rate >= 0` (`loan_params.py:35-38`, named
  `ck_loan_params_interest_rate`, constraint string at `:36`; migration
  `dc46e02d15b4_add_check_constraints_to_loan_params_.py:32`, dropped in its
  downgrade at `:71`). For a fixed-rate loan it is the contractual rate for
  the life of the loan. For an ARM it is a **denormalized mirror of the most
  recently entered `RateHistory` rate** (see Update paths) -- it is NOT the
  origination rate and NOT an immutable input (contrast
  `original_principal`).
- **Computational counterpart:** for ARM loans, **yes** -- the authoritative
  per-period rate is `RateHistory` resolved by
  `_find_applicable_rate(payment_date, rate_schedule, base_rate)`
  (`app/services/amortization_engine.py:298-323`: most recent
  `effective_date <= payment_date`, else `base_rate`). The stored
  `interest_rate` is only the `base_rate` fallback (the `annual_rate` arg
  threaded `:443` -> `:500`). For fixed-rate loans there is **no**
  counterpart -- the column is its own value, no rate history exists.
- **Update paths** (grep-verified;
  `grep -rEn "\.interest_rate\s*=[^=]" app/ scripts/ --include='*.py'`
  returns **exactly one direct-assignment match**, plus one `setattr` form):
  - **Creation:** `app/routes/loan.py:622` `create_params` --
    `LoanParams(account_id=account.id, **data)`, after
    `data["interest_rate"] = pct_to_decimal(data["interest_rate"])` at
    `loan.py:619-620`; validated by `LoanParamsCreateSchema`
    (`app/schemas/validation.py:1445`, `required=True, places=5,
    Range(min=0, max=100)` -- the `0-100` bound is the *percent* the user
    types, converted to a `0-1` decimal by `pct_to_decimal` before write).
  - **Every rate change overwrites it:** `app/routes/loan.py:709`
    `params.interest_rate = data["interest_rate"]` inside `add_rate_change`
    (`loan.py:685-758`), executed unconditionally after the `RateHistory`
    INSERT (`:700-706`). The comment at `:708` reads "Also update the current
    rate on params." The value written is the **just-submitted** rate
    (`data["interest_rate"]`, `pct_to_decimal`-converted at `:698`), **not**
    the rate `_find_applicable_rate` would resolve for today. A rate change
    recorded with a *future* `effective_date` still moves the column NOW.
  - **Manual params form:** `app/routes/loan.py:631-679` `update_params` --
    `"interest_rate"` is a member of `_PARAM_FIELDS` (`loan.py:669`); the
    loop `for field, value in data.items(): if field in _PARAM_FIELDS:
    setattr(params, field, value)` (`:672-674`) writes it from
    `LoanParamsUpdateSchema` (`validation.py:1467`, same `Range(min=0,
    max=100)`), after `pct_to_decimal` at `loan.py:665-666`.
  - **Migrations:** `a1b2c3d4e5f6_add_debt_account_tables.py`,
    `c67773dc7375_unify_loan_params_into_single_table_.py` create/backfill.
  - **NOT written by any settle/status-transition path** (cross-ref Q-22:
    the 12 settle modules do not import `LoanParams`; this column shares that
    proof -- it is never touched on settle, only on the rate/params forms).
- **Direct-read paths** (every scalar-payment site reads THIS column, never
  `RateHistory`): `amortization_engine.py:914`
  (`rate = Decimal(str(params.interest_rate))` -> ARM site-7
  `:952-954`, fixed site-8 `:957-959`); `loan_payment_service.py:253`
  (ARM `compute_contractual_pi`), `:258` (fixed); `balance_calculator.py:216`
  (`annual_rate = loan_params.interest_rate`, sites 9/10 `:225/:231`);
  `loan.py:370` (`_load_loan_context` `rate`, feeds the dashboard chart
  schedules `:454/:481`), `:1227` (ARM `create_payment_transfer` site 14),
  `:1233` (fixed site 15); `debt_strategy.py:110`
  (`rate = Decimal(str(params.interest_rate))` -> site-16
  `:127-129` and the strategy accrual `debt_strategy_service.py:412`
  `balances[i] * debt.interest_rate / TWELVE`);
  `savings_dashboard_service.py:478` (payoff-verify replay), `:845`
  (`/savings` debt card PITI); `year_end_summary_service.py:1473`
  (`annual_rate=params.interest_rate`, the ARM-anchored year-end schedule).
- **Drift risk:** two distinct surfaces.
  1. **Scalar-vs-schedule rate divergence (ARM).** Every scalar
     `monthly_payment` site reads the stored mirror; the *schedule* per-row
     payment re-amortizes at `_find_applicable_rate` (RateHistory). When the
     mirror disagrees with the rate-history-resolved rate for today, the
     loan-dashboard "Monthly P&I" card (`loan/dashboard.html:129`,
     `summary.monthly_payment`, site 7) and the schedule rows
     (`loan/_schedule.html`, site 4 `amortization_engine.py:512-514`)
     show different rates for the same ARM. This is F-013's rate-source axis,
     re-confirmed at source.
  2. **Effective-date-unaware mirror write.** `add_rate_change:709` writes
     the submitted rate regardless of its `effective_date`. Recording a
     future-effective adjustment immediately drifts the displayed Monthly P&I
     (which reads the mirror) while the schedule (which honors
     `effective_date` via `_find_applicable_rate`) does not change until the
     effective month. Recording a *backdated* correction after a later change
     leaves the mirror equal to the backdated rate, not the latest. No code
     reconciles the mirror to "the RateHistory row in effect today."
- **Stale-detection:** **NONE.** No `stale_*` flag; no
  `scripts/integrity_check.py` rule keys on `interest_rate` or on
  mirror/RateHistory agreement (grep of `interest_rate` across `scripts/`
  returns nothing). The mirror desync is entirely silent.
- **Phase-3 cross-ref:** F-013 (`monthly_payment` 16-site, symptom #2;
  rate-source DIVERGES axis), F-026 (5/5 ARM E-02, symptom #4). The stored
  `interest_rate` is the constant-in-window rate that makes the F-026 drift
  *purely* a principal/`remaining` artifact (rate is NOT the in-window driver
  -- see the crux subsection). Re-verification log below.
- **Constraint-asymmetry observation (NEW, P4-b2):** `loan_params.interest_rate`
  DB CHECK is `>= 0` with **no upper bound**, while its own audit-history
  mirror `rate_history.interest_rate` carries CHECK `>= 0 AND <= 1`
  (`loan_features.py:44-47`). `add_rate_change` writes the **same**
  `data["interest_rate"]` value to both (`loan.py:703` into RateHistory,
  `:709` into loan_params), so the two columns are intended to hold the same
  decimal-fraction quantity, yet the loan_params copy can legally store a
  value `> 1` (e.g. a fat-fingered `update_params` post that bypasses the
  add-rate-change path -- the schema's `max=100` percent bound is the only
  guard, and it lives in Marshmallow, not the DB). Per
  `docs/coding-standards.md` "Range validation must match between schema and
  database" this is an unenforced-domain gap analogous to Family A's missing
  anchor CHECK (Q-21 sub-question 3). Recorded; the auditor does not assume
  the intended domain (a rate `> 100%` is implausible but the audit does not
  invent the bound -- see Q-23).
- **Classification: UNCLEAR.** The column is not treated consistently:
  - **Interpretation A -- AUTHORITATIVE (fixed-rate, and ARM "current
    rate").** For fixed loans it is the user-entered contractual rate with no
    counterpart and is effectively immutable -- cleanly AUTHORITATIVE. For
    ARM, A-04/A-05 (`09_open_questions.md`) treat the *stored* loan params as
    the user-verified anchor; under that reading the stored `interest_rate`
    is the intended current rate and the bug is that the schedule path uses a
    *different* (RateHistory) rate, plus the effective-date-unaware write.
  - **Interpretation B -- CACHED (ARM mirror of RateHistory).** The model
    docstring intent and `load_loan_context` wiring make `RateHistory` the
    authoritative ARM time series; the stored column is then a denormalized
    cache of "the latest entered rate," which goes stale relative to
    `_find_applicable_rate(today, ...)` whenever a future-dated or
    out-of-order rate change is entered. Under this reading the bug is that
    every scalar display reads the stale cache instead of resolving the
    rate-as-of-today from RateHistory.
  The split is exactly the Q-17 fork (maintain the stored value vs. derive
  from the authoritative series). The auditor does not adjudicate (audit-plan
  section 9; hard rule 5). Cross-link **Q-17**, **Q-22**, **Q-23** (new).

### budget.rate_history.interest_rate

- **Represents:** one historical/scheduled ARM rate change, stored as a
  decimal fraction, `Numeric(7, 5)`, **NOT NULL**, no server default
  (`app/models/loan_features.py:75`). DB CHECK
  `interest_rate >= 0 AND interest_rate <= 1` (`loan_features.py:44-47`,
  named `ck_rate_history_valid_interest_rate`, constraint string at `:45`;
  migration `b71c4a8f5d3e_c24_marshmallow_range_check_sweep.py:201-202`, the
  C-24 sweep / commit `42720ca` CM-02). The model comment `:37-43` documents
  the `pct_to_decimal` storage contract and the closed-unit-interval CHECK.
  Composite unique `uq_rate_history_account_effective_date` (`:33-36`)
  guarantees one row per `(account_id, effective_date)`.
- **Computational counterpart:** none -- it is event-input data, not derived
  from any primary. Nothing recomputes or writes it from confirmed payments.
- **Update paths:** **creation ONLY**, one INSERT per rate change:
  `app/routes/loan.py:700-706` `add_rate_change` --
  `RateHistory(account_id=account.id, effective_date=data["effective_date"],
  interest_rate=data["interest_rate"], notes=...)`, after
  `data["interest_rate"] = pct_to_decimal(data["interest_rate"])`
  (`loan.py:698`), validated by `RateChangeSchema`
  (`app/schemas/validation.py:1484`, `required=True, places=5,
  Range(min=0, max=100)` -- percent, converted to `0-1` decimal before
  write; the `<= 1` DB CHECK exactly matches the post-conversion `100% -> 1.0`
  ceiling, so schema and DB are **aligned**). No edit/update route; a
  same-day correction is by editing the existing row's intent (the model
  docstring `:24-28`), but **no route exists to edit a RateHistory row** --
  duplicate `effective_date` is rejected with a flash (`loan.py:712-743`).
  Bulk delete on account deletion: `app/routes/accounts.py:715`
  `db.session.query(RateHistory).filter_by(account_id=...).delete()`.
  Migrations `a1b2c3d4e5f6` / `c67773dc7375` create the table.
- **Direct-read paths:** calculation -- **exactly one**:
  `app/services/loan_payment_service.py:131-144` `load_loan_context` queries
  `RateHistory` ordered `effective_date.desc()` for ARM loans and builds
  `rate_changes = [RateChangeRecord(effective_date=rh.effective_date,
  interest_rate=Decimal(str(rh.interest_rate))) ...]`; that list flows into
  `get_loan_projection(rate_changes=...)` ->
  `generate_schedule(rate_changes=...)` ->
  `_build_rate_change_list` (`amortization_engine.py:255-295`) ->
  `_find_applicable_rate` (`:298-323`) per schedule row (site 4,
  `:498-514`). Display -- `loan.py:732-737,747-752` (re-query for
  `loan/_rate_history.html`); `loan/dashboard.html:244` includes it.
- **Drift risk:** the column itself has **no stored-vs-computed drift
  surface** (no computational counterpart, append-only, schema/DB CHECK
  aligned). The risk is a **consumption** one: the authoritative per-period
  rate it encodes is consulted ONLY inside `generate_schedule`'s row loop and
  ONLY when the caller passes `rate_changes` (i.e. ARM loans routed through
  `load_loan_context` -- the loan dashboard schedule and the year-end
  schedule). Every scalar `monthly_payment` site bypasses it and reads the
  `loan_params.interest_rate` mirror instead (see that column). So the
  authoritative series exists but the most prominent surface (the bold
  "Monthly P&I" card) does not consume it. That is F-013's rate-source
  divergence and part of the Q-17 fork, not a drift of this column.
- **Stale-detection:** N/A (append-only event log, no counterpart). Positive
  note: schema (`Range(min=0, max=100)` percent + `pct_to_decimal`) and DB
  CHECK (`0-1` decimal) are **aligned**, the C-24 sweep closed this gap
  (contrast the asymmetric `loan_params.interest_rate` CHECK above and
  Family A's missing anchor CHECK).
- **Phase-3 cross-ref:** F-013 (rate-source axis -- the scalar sites do not
  read this), F-026 (the in-window finding holds because no RateHistory rows
  exist inside a 5/5 ARM's fixed window, so the schedule and scalar agree on
  *rate* there and the drift is purely principal/`remaining`).
- **Classification: AUTHORITATIVE.** Append-only user/event input, no
  computational counterpart, schema and DB CHECK aligned, one well-defined
  calculation consumer. The cleanest column in Family B. The defect is that
  its authority is **not honored by the scalar display sites** (a Phase-3
  consumption inconsistency, F-013 / Q-17), and that its intended mirror
  `loan_params.interest_rate` is written effective-date-unaware -- both are
  recorded against `loan_params.interest_rate`, not here.
- **Open questions:** none new for this column; cross-link Q-17, Q-23.

### budget.escrow_components.annual_amount

- **Represents:** the annual dollar amount of one escrow line item (property
  tax, insurance, etc.) for a loan account, `Numeric(12, 2)`, **NOT NULL**,
  no server default (`app/models/loan_features.py:126`). DB CHECK
  `annual_amount >= 0` (`loan_features.py:103-106`, named
  `ck_escrow_components_nonneg_annual_amount`, constraint string at `:104`;
  migration `b71c4a8f5d3e_c24_marshmallow_range_check_sweep.py:109-110`, the
  C-24 sweep).
- **Computational counterpart:** none -- user-entered input. It is *consumed*
  as `annual_amount / 12` to derive the per-period escrow, but nothing
  stores a counterpart.
- **Update paths:** **creation ONLY** (no edit route):
  `app/routes/loan.py:789` `add_escrow` --
  `EscrowComponent(account_id=account.id, **data)`, validated by
  `EscrowComponentSchema` (`app/schemas/validation.py:1496`,
  `required=True, places=2, as_string=True, Range(min=0)` -- a dollar
  amount, NO `pct_to_decimal`; `Range(min=0)` inclusive **exactly matches**
  DB CHECK `>= 0`). Soft delete: `loan.py:831` `delete_escrow` sets
  `comp.is_active = False` (the `annual_amount` value is never mutated).
  Hard delete on account deletion: `app/routes/accounts.py:714`. No update
  route exists -- a correction is delete-and-re-add. Migrations
  `a1b2c3d4e5f6` / `c67773dc7375` create the table.
- **Direct-read paths:** `escrow_calculator.calculate_monthly_escrow`
  (`escrow_calculator.py:32` `annual = Decimal(str(comp.annual_amount))`,
  `:54` `monthly = annual / 12`, summed un-quantized, `:57` quantize the
  total once ROUND_HALF_UP) and `project_annual_escrow` (`:105`, quantizes
  **each component before summing** at `:111` -- a different quantization
  order, ROUNDING note); consumed by `loan_payment_service.load_loan_context`
  (`:104-112`), `loan.py:164` (`_compute_payment_breakdown`), `:200`
  (next-year inflated), `:803/:848` (escrow routes), `:1241`
  (`create_payment_transfer`); `savings_dashboard_service.py:279-280`
  (loads components), `:850` (`calculate_monthly_escrow`). Template Jinja
  arithmetic (E-16 violations, flagged for Phase 6, not fixed):
  `loan/_escrow_list.html:37`
  `${{ "{:,.2f}".format(comp.annual_amount|float / 12) }}` -- a **second,
  independent** per-component monthly-escrow computation in Jinja `float`,
  parallel to `calculate_monthly_escrow`'s Decimal path; and
  `loan/_schedule.html:55`
  `(row.payment|float) + (monthly_escrow|float) + (row.extra_payment|float)`
  -- per-row total payment assembled in Jinja float.
- **Drift risk:** the column has **no stored-vs-computed drift surface** (no
  counterpart, immutable post-create, schema/DB aligned). The risk is a
  **consumption** one and lands on symptom #2/#3, documented in the escrow
  subsection below: (i) on the RAW-replay path
  (`debt_strategy._compute_real_principal`, `debt_strategy.py:147-197`)
  escrow is **never subtracted** and the engine attributes it to principal
  paydown (E-01 part-2 violation, P4-b1-confirmed, **independently
  re-confirmed at source this session**); (ii) the escrow-subtraction
  threshold on the prepared path is the ARM `contractual_pi`, which itself
  drifts monthly (NEW finding, escrow subsection).
- **Stale-detection:** N/A (immutable input). Positive note: schema
  `Range(min=0)` and DB CHECK `>= 0` are aligned (contrast Family A).
- **Phase-3 cross-ref:** F-019 (`escrow_per_period`), F-014/F-017 (the A-06
  escrow-as-principal SCOPE matter), F-013/F-026 (the drifting
  `contractual_pi` threshold ties escrow handling to symptom #2/#4).
- **Classification: AUTHORITATIVE.** User-entered input, no computational
  counterpart, immutable post-create, schema and DB CHECK aligned. The
  cleanest classification of the three; every defect is in *consumers*
  (RAW-replay escrow-as-principal; drifting subtraction threshold; Jinja
  recomputation), recorded as Phase-3 SCOPE/E-16 matters, not column drift.
- **Open questions:** none new for this column; cross-link F-019, Q-17.

### Symptom #4 / E-02 / Q-17 -- ARM payment drift inside the fixed window (the crux)

**ARM rate-resolution determination (DEFINITIVE, code-proven).** For a given
period there are two rate sources and the authority depends on the *surface*:

- **The displayed scalar "Monthly P&I" and every other scalar
  `monthly_payment` site read the STORED `loan_params.interest_rate`.**
  Proof: `get_loan_projection` `amortization_engine.py:914`
  `rate = Decimal(str(params.interest_rate))`; the ARM branch
  `:950-954` `if is_arm and remaining > 0: monthly_payment =
  calculate_monthly_payment(current_principal, rate, remaining)` passes that
  `rate`. `RateHistory` is **not consulted** at this site (grep of the
  function body; `rate_changes` is only threaded into `generate_schedule`,
  never into the scalar `:952-954`). Same for
  `compute_contractual_pi` (`loan_payment_service.py:251-260`),
  `balance_calculator.py:216-231`, `loan.py:1225-1234`,
  `debt_strategy.py:110,127-129`.
- **`RateHistory` is authoritative ONLY inside the schedule row loop, ONLY
  when the caller passed `rate_changes`.** Proof: `_find_applicable_rate`
  (`amortization_engine.py:298-323`) returns the most recent
  `effective_date <= payment_date`, else the `base_rate` (= stored
  `interest_rate`, the `annual_rate` arg). It fires at
  `generate_schedule:498-514` only `if rate_schedule:` -- non-empty only when
  `rate_changes` was passed, which happens **only** via
  `load_loan_context:131-144` for ARM loans with `RateHistory` rows (the loan
  dashboard schedule `loan.py:429-431`, and the year-end schedule
  `year_end_summary_service.py:1470-1480`). The escrow-route partial
  (`_compute_total_payment` -> `get_loan_projection(params)`, `loan.py:399`,
  no `rate_changes`), `create_payment_transfer` (`loan.py:1225`), the
  `/savings` debt card (`savings_dashboard_service.py:845`), and
  debt-strategy (`debt_strategy.py`) pass **no** rate_changes -- they use the
  stored mirror only.

So: `loan_params.interest_rate` is the **live current rate**, maintained as a
denormalized mirror written on every `add_rate_change` (`loan.py:709`,
effective-date-unaware) and editable manually; `RateHistory.interest_rate` is
the **authoritative effective-dated series** but is consumed only by the
schedule on two prepared-context surfaces. **Inside a 5/5 ARM's fixed window
there are by definition no RateHistory rows yet, so rate is identical across
both sources there -- rate is NOT the in-window drift driver.**

**Q-17 mechanism, independently re-derived from engine source (NOT inherited
from F-026/Q-22).** The displayed ARM "Monthly P&I" is
`calculate_monthly_payment(P, r, n)` (`amortization_engine.py:952-954`) where:

- `P = current_principal = Decimal(str(params.current_principal))`
  (`:913`) -- the STORED column. P4-b1/Q-22 proved (grep re-run, settle
  modules do not import `LoanParams`) it is **never reduced on settle**; the
  only post-creation writer is the manual `update_params` form. Inside the
  fixed window with confirmed transfers settling, `P` is frozen.
- `r = Decimal(str(params.interest_rate))` (`:914`) -- STORED, constant in
  the fixed window (no RateHistory).
- `n = remaining = calculate_remaining_months(params.origination_date,
  params.term_months)` (`:908-910`), `as_of` defaulting to `date.today()`.
  Body `:136-142`:
  `months_elapsed = (as_of.year - origination_date.year)*12 +
  (as_of.month - origination_date.month)`;
  `return max(0, term_months - months_elapsed)`. **`n` strictly decreases by
  1 every calendar month.**

The amortization formula (`:194-197`):
`M = P * (i(1+i)^n) / ((1+i)^n - 1)`, `i = r/12`. Rewrite
`M = P*i / (1 - (1+i)^(-n))`. With `P` and `i` fixed and `n` decreasing,
`(1+i)^(-n)` increases, the denominator shrinks, so **`M` increases every
month**. The payment drifts strictly upward inside the fixed window with no
rate change and no manual edit. The amortization identity (re-amortizing the
*true* remaining balance `B_k` over the *remaining scheduled* term `N-k`
returns the original `M`) is **broken here precisely because `P` is the
frozen stored column, not `B_k`**. Symptom #4 is the SAME un-maintained
`current_principal` column as symptom #3 (Q-22), now multiplied through the
annuity formula instead of displayed directly. **CONFIRMED at source; this is
a real E-02 violation.**

**Structural root cause -- the engine has no fixed-window concept (NEW
grep-proof).** `arm_first_adjustment_months` / `arm_adjustment_interval_months`
(`loan_params.py:60-61`, both `nullable=True`) are stored, form-bound
(`loan.py:670` in `_PARAM_FIELDS`), and schema-validated
(`validation.py:1450-1451` create, `:1471-1472` update). `grep -rn` across
`app/ scripts/` returns **only** those four locations (model x2, route x1,
schema x2) plus zero in `app/templates/ app/static/` -- **no calculation site
consumes either column.** The amortization engine therefore has no
representation of the 60-month fixed-rate window and cannot hold the payment
constant across it. This independently confirms F-026's "engine has no
representation of the fixed-rate window" claim.

### Worked numeric example (5/5 ARM, fixed-rate window)

5/5 ARM: `original_principal = $400,000.00`; STORED `current_principal =
$400,000.00` (entered at `create_params`, never settle-updated -- Q-22);
STORED `interest_rate = 0.06000` (6.000%, monthly `i = 0.005`);
`term_months = 360`; `origination_date = 2026-01-01`;
`arm_first_adjustment_months = 60` (rate fixed for months 1-60); no
RateHistory rows inside the window. Figures below are **exact Decimal**
(`calculate_monthly_payment`, quantize `ROUND_HALF_UP` `:197`), computed with
the engine's own formula:

| What | n (`remaining`) | site-7 value (`amortization_engine.py:952-954`) |
|------|------------------|-------------------------------------------------|
| **Correct constant payment (E-02 requires this for ALL 60 months)** | 360 | **$2,398.20** |
| month 1 (`months_elapsed=1`) | 359 | **$2,400.59** |
| month 12 | 348 | **$2,428.02** |
| month 24 | 336 | **$2,460.50** |
| month 48 | 312 | **$2,534.71** |
| month 59 (last fixed-window month) | 301 | **$2,573.51** |

The payment moves **+$2.39 in the first month alone** and is **$175.31 above
the correct constant by month 59**, entirely inside the fixed-rate window,
with no rate change and no manual edit. The single input that drives every
different value is `n` (`calculate_remaining_months`, `:908-910`) shrinking
against the frozen stored `P` (`:913`); `r` (`:914`) is constant in the
window and is NOT the driver. Any one of these per-month values violates E-02
(`00_priors.md:166-170`: "one monthly payment value ... Fluctuation by even a
few cents is a finding"). **Confirmed E-02 finding.**

**Reconciliation to the developer's observed $1911.54 / $1914.34 / $1912.94 /
$1910.95 (audit-plan 3.1).** The absolute values depend on the developer's
real loan parameters (principal, rate, term, origination), which are not in
the codebase and which Phase 5 reconstructs from `/accounts/3/loan`. The
**mechanism, sign, and magnitude shape match**: for a comparable loan
(`P=$400,000`, `r=6.500%`, `T=360`) the engine yields consecutive-month
site-7 values `me=47 -> $2,656.41`, `me=48 -> $2,659.67`,
`me=49 -> $2,662.95` -- a **~$3.2/month upward creep**, the same few-dollar
month-over-month shape as `$1911.54 -> $1914.34 -> $1912.94`. The
non-monotone wobble in the developer's figures (1914.34 then down to 1912.94)
is explained by the F-013 cross-site axis (different surfaces feed site 7 vs
site 3/4 a different `(P, r, n)` triple on the same day) layered on the
monotone in-window drift. "$1910.95 after editing current principal on
`/accounts/3/loan`" is the `update_params:672-674` write of a smaller stored
`P`, which site-7 immediately re-amortizes lower (sign matches: smaller `P`
-> lower `M`). The mechanism and controlling citations are pinned; the
per-loan ledger is Phase 5.

### monthly_payment entry-point input matrix (audit-plan 3.1)

Every surface that displays an ARM `monthly_payment`, the inputs each feeds
`calculate_monthly_payment`, and whether the input drifts in the fixed window.
All 16 `calculate_monthly_payment(` call sites grep-enumerated this session
(`app/`: `loan.py:1102/1225/1231`, `debt_strategy.py:127`,
`balance_calculator.py:225/231`, `loan_payment_service.py:251/256`,
`amortization_engine.py:436/440/491/512/693/697/952/957`).

| Surface (entry point) | path:line | principal | rate | n | drifts in fixed window? |
|---|---|---|---|---|---|
| Loan dashboard "Monthly P&I" card (site 7) | `loan.py:429-432` -> `amortization_engine.py:952-954`; `loan/dashboard.html:129` | STORED `current_principal` `:913` | STORED `interest_rate` `:914` | `calculate_remaining_months` `:908-910` (shrinks) | **YES** (P frozen, n shrinks) |
| Loan dashboard schedule per-row (sites 3/4) | `amortization_engine.py:486-493` / `:498-514`; `loan/_schedule.html:55` | anchor=STORED `current_principal` (`get_loan_projection:926`) | RateHistory `period_rate` `:499` (mirror outside window) | `months_left = max_months-month_num+1` (loop, ~n+1) | YES, and on a *different* n than site 7 (F-013) |
| Escrow add/delete partial total | `loan.py:399` `_compute_total_payment` -> `get_loan_projection(params)` (no payments/rate_changes); `loan/_escrow_list.html` | STORED `current_principal` | STORED `interest_rate` | shrinking `remaining` | **YES** (same site-7 mechanism) |
| `compute_contractual_pi` (ARM) -- the escrow-subtraction threshold | `loan_payment_service.py:247-255` | STORED `current_principal` `:252` | STORED `interest_rate` `:253` | `calculate_remaining_months` `:247-249` | **YES** -- and this drifting value is the threshold in `prepare_payments_for_engine:308` |
| `create_payment_transfer` default amount (site 14) | `loan.py:1222-1229`, frozen into `TransferTemplate.default_amount` `:1265` | STORED `current_principal` `:1226` | STORED `interest_rate` `:1227` | `calculate_remaining_months` `:1222-1224` | snapshot at create time of the drifting value |
| `/savings` debt-card PITI | `savings_dashboard_service.py:846` (`monthly_pi = ad["monthly_payment"]` from per-account `get_loan_projection`) `+ :850` escrow | STORED `current_principal` | STORED `interest_rate` | shrinking `remaining` | **YES** (site-7 value reused) |
| balance_calculator amortized period (sites 9/10) | `balance_calculator.py:216,225-231` | STORED `current_principal` `:226` | STORED `interest_rate` `:216` | `calculate_remaining_months` `:222` | YES (P4-b1 noted balance_calculator's value may be DEAD -- F-017; cross-ref only) |
| debt-strategy minimum payment (site 16) | `debt_strategy.py:127-129` | ARM: STORED `current_principal` (`_compute_real_principal:172-173`) | STORED `interest_rate` `:110` | `calculate_remaining_months` `:111-113` | **YES** for ARM (P frozen, n shrinks) |
| year-end ARM schedule | `year_end_summary_service.py:1470-1480` (anchor=STORED `current_principal` `:1465-1467`, base_rate=STORED `interest_rate` `:1473`, `rate_changes=ctx.rate_changes`) | STORED anchor | STORED base + RateHistory per row | schedule loop | schedule re-amortizes at anchor each year-end (inherits the frozen-P issue) |
| refinance preview (site 13) | `loan.py:1087,1102` (`current_real_principal = proj.current_balance`; ARM => STORED) | engine `current_balance` (ARM=STORED) | form `pct_to_decimal(new_rate)` | form `new_term_months` | NEW-loan terms by design (not an in-window concern) |

Synthesis: **every in-app ARM `monthly_payment` surface drifts upward in the
fixed window**, because all of them feed `calculate_monthly_payment` a frozen
stored `current_principal` against a calendar-shrinking `remaining`. The
recurring-transfer amount is *frozen at create time* to that day's drifting
value (`loan.py:1265`), so the dashboard card and the actual transfer amount
also diverge from each other over time. This is symptom #4 and the
fixed-window lens on symptom #2 (F-013/F-026), and it is downstream of symptom
#3 (Q-22: the un-maintained `current_principal`).

### Escrow per-period consistency across entry points

`escrow_calculator.calculate_monthly_escrow(components)` is the single
producer (`escrow_calculator.py:14-57`): `sum(Decimal(str(c.annual_amount))
/ 12)` over `is_active` components, summed un-quantized, total quantized once
`ROUND_HALF_UP` (`:57`); inflation only when `as_of_date` is passed
(`:35-52`). Called **without `as_of_date`** (non-inflated) at every
payment-relevant site: `load_loan_context:110`, `loan.py:803/848` (escrow
routes), `loan.py:164` (`_compute_payment_breakdown` current period),
`savings_dashboard_service.py:850`. Called **with `as_of_date`** only at
`loan.py:200` (next-year projection note). So the base per-period escrow is
**consistent** across the prepared surfaces.

The escrow defects are NOT in the producer; they are two consumption seams:

1. **RAW-replay escrow-as-principal (P4-b1-confirmed, INDEPENDENTLY
   RE-CONFIRMED at source).** `debt_strategy._compute_real_principal`
   (`debt_strategy.py:147-197`, read in full): ARM branch `:172-173`
   `return principal` (no replay); fixed branch `:175` `payments =
   get_payment_history(params.account_id, scenario_id)` -- **RAW history, no
   `prepare_payments_for_engine` anywhere in the function** -- fed straight
   into `generate_schedule(... payments=payments)` `:181-190`. Inside the
   engine the matched-payment month computes `total_payment =
   amount_by_month[month_key]` (`amortization_engine.py:527`),
   `principal_portion = total_payment - interest` (`:531`): the escrow
   dollars inside a PITI transfer are attributed to principal paydown. E-01
   part-2 (escrow must not reduce loan principal) is **VIOLATED** on this
   path. CONFIRMED at source; corroborates P4-b1's worked example
   (`b = f = $198,495.20`, the ~$900 escrow-sized over-paydown).
2. **Drifting escrow-subtraction threshold (NEW, P4-b2).**
   `prepare_payments_for_engine` (`loan_payment_service.py:263-353`)
   subtracts escrow only from the excess above `contractual_pi`:
   `:308` `if p.amount > contractual_pi: new_amount = p.amount -
   min(monthly_escrow, p.amount - contractual_pi)`. For an ARM,
   `contractual_pi = compute_contractual_pi(loan_params)`
   (`load_loan_context:121`) which is itself
   `calculate_monthly_payment(STORED current_principal, STORED interest_rate,
   shrinking remaining)` (`loan_payment_service.py:250-255`) -- **the same
   upward-drifting value as site 7**. As `contractual_pi` creeps up month by
   month, the escrow actually subtracted shrinks (and, once `contractual_pi`
   exceeds the real PITI transfer `p.amount`, the `if` is false and **no
   escrow is subtracted at all** -- the full escrow then flows into principal
   even on the *prepared* path). So the ARM drift (symptom #4) feeds back
   into escrow handling and thereby into the fixed-rate-style principal walk
   -- a coupling between symptom #4 and the symptom-#2/#3 principal surfaces
   that P4-b1 did not have the rate/escrow reads to see. This is a Phase-3
   SCOPE matter on top of F-019/F-014/F-017; recorded here, no new question
   (it is mechanically downstream of Q-17 -- if the ARM payment were held
   constant in the window, `contractual_pi` would be stable and the threshold
   correct).

### Phase-3 re-verification log (Family B rate/escrow)

Rate/escrow-column citations only. Per audit-plan and the P4 trust-but-verify
rule, every Phase-3 `path:line` consumed by this session was opened in source.

- **F-026 `monthly_payment` E-02 / symptom #4** (`03_consistency.md`
  ~1936-2036, heading "Finding F-026: 5/5 ARM payment stability inside the
  fixed-rate window") -- **CONFIRMED.** `amortization_engine.py:952-954` ARM
  `calculate_monthly_payment(current_principal, rate, remaining)` -- read at
  source, exact (line 951 is the comment, the call spans `:952-954`). `:913`
  `current_principal = Decimal(str(params.current_principal))`, `:914`
  `rate = Decimal(str(params.interest_rate))`, `:908-910`
  `remaining = calculate_remaining_months(...)`, body `:136-142`
  `max(0, term_months - months_elapsed)` -- all CONFIRMED. `:498-514`
  `_find_applicable_rate` site-4 path -- CONFIRMED. `:192/197` quantize
  ROUND_HALF_UP -- CONFIRMED. `loan_params.py:60-61` arm window columns
  inert -- **CONFIRMED and strengthened** (grep this session: only model x2,
  `loan.py:670`, `validation.py:1450-1451/1471-1472`; zero calc sites, zero
  templates). F-026's worked arithmetic uses log/exp approximations (it says
  so: "ln(1.005)=0.00498754 ... e^1.79551=6.022575"); the **exact Decimal**
  constant payment is **$2,398.20** -- F-026 reports $2,398.20, an **exact
  match**; F-026's `me=24 -> $2,460.45` is ~5c off the exact $2,460.50 (its
  own stated approximation), direction/magnitude/mechanism identical. F-026
  is CONFIRMED; the engine-formula numbers in this session's worked example
  supersede F-026's hand-exponentials for any cent-exact downstream use.
- **F-013 `monthly_payment` 16-site / symptom #2** (`03_consistency.md`
  ~1009-1147) -- **CONFIRMED** for the rate/escrow axes. The 16
  `calculate_monthly_payment(` call sites independently grep-enumerated this
  session match F-013's count (8 in `amortization_engine.py`:
  436/440/491/512/693/697/952/957; `loan_payment_service.py:251/256`;
  `balance_calculator.py:225/231`; `loan.py:1102/1225/1231`;
  `debt_strategy.py:127`). Rate-source DIVERGES axis CONFIRMED: scalar sites
  read `loan_params.interest_rate` (`amortization_engine.py:914` etc.), site
  4 reads RateHistory `period_rate` (`:499`) -- re-read at source. Site-7
  `loan/dashboard.html:129` `summary.monthly_payment` bold card -- CONFIRMED
  (grep of the template; also `:59` P&I line).
- **F-019 `escrow_per_period`** (`03_consistency.md` ~1510-1566) -- the
  Explore extract returned the heading and surrounding F-018/F-020 context
  but the finding body was not returned verbatim by the subagent; the
  *source-side* claims this session relies on were verified directly:
  `escrow_calculator.calculate_monthly_escrow` single producer
  (`escrow_calculator.py:14-57`, read in full), the
  `_escrow_list.html:37` Jinja `annual_amount|float / 12` E-16 recomputation
  (grep-confirmed), and the `prepare_payments_for_engine` escrow subtraction
  (`loan_payment_service.py:305-319`, read in full). **Not recorded as a
  miscite** (the Phase-3 finding exists at the cited heading; only the
  Explore relay was partial) -- a verification-completeness note, see Q-21
  protocol; no `03_consistency.md` edit.
- **F-014 / F-017 escrow-as-principal (A-06 SCOPE)** -- **CONFIRMED** at
  source independently of P4-b1: `debt_strategy.py:147-197` read in full
  (ARM `:172-173`; fixed RAW `get_payment_history` `:175`,
  `generate_schedule` `:181-190`, walk `:193-195`, fallback `:197`); engine
  split `amortization_engine.py:527/531` -- exactly as P4-b1 stated. P4-b1's
  Family-B-principal escrow conclusion is re-confirmed, now with the
  rate/escrow reads that expose the additional drifting-threshold coupling
  (escrow subsection item 2).
- **Phase-1 reconciliation (`01_inventory.md` §1.5 `loan_features.py`
  block).** This block was **not verified by any prior session**. Re-verified
  against the full `loan_features.py` read: `RateHistory.interest_rate`
  `loan_features.py:75` `Numeric(7,5)` NOT NULL, CHECK at `:44-47`
  (inventory cites `:44`, the `db.CheckConstraint(` opener; the constraint
  *string* is `:45` -- a one-line convention difference from the
  `loan_params.py` block which cites the string line, NOT a factual error);
  `EscrowComponent.annual_amount` `:126` `Numeric(12,2)` NOT NULL, CHECK
  `:103-106` (inventory cites `:104`, the string -- accurate);
  `EscrowComponent.inflation_rate` `:127` `Numeric(5,4)` nullable, CHECK
  `:111-115`. "Computed properties: none" -- **CONFIRMED** (only `__repr__`
  on both classes). The inventory's `loan_features.py` block is **ACCURATE**
  (the only nit is the CHECK-line citation convention noted above, immaterial
  -- contrast Q-21's `account.py` block which recorded a CHECK that does not
  exist). The `loan_params.py` block is ACCURATE for `interest_rate`
  (`:55`, CHECK `:36`) and the arm-window columns (`:60-61`, no CHECK,
  consumed nowhere) -- consistent with P4-b1's finding for that block. **No
  Phase-1 correction question is needed for Family B rate/escrow**, beyond
  the citation-convention note recorded here (no `01_inventory.md` edit, per
  protocol).

---

## Family C - Interest and Investment parameters

Six stored columns across two one-to-one params models, both read in full
this session: `app/models/interest_params.py` (73 lines),
`app/models/investment_params.py` (99 lines). `TimestampMixin`
(`app/models/mixins.py:17-43`) adds only `created_at` / `updated_at`.
**Neither model declares any `@property` / `@hybrid_property`** (grep of both
model files for `@property`/`hybrid_property`: zero matches; only `__repr__`)
-- CONFIRMS `01_inventory.md` §1.5 "Computed properties: none" for both
blocks. The computational counterparts were read in full:
`interest_projection.py` (114 lines), `investment_projection.py` (288 lines);
`growth_engine.py` (420 lines) consumer functions read at source.

This family is **input-only configuration**: APY, assumed return rate,
contribution limit, three employer percentages, compounding frequency. None
is a denormalization or a cache of any computed figure -- the projection
engines derive *from* these columns; nothing derives the columns. All six are
**AUTHORITATIVE** by construction. The Phase-4 deliverable is therefore (1) to
*prove* the absence of any cached projected-balance column the audit plan
names, and (2) the consumer-routing (E-04) check; the per-column findings are
correspondingly compact, which is the correct outcome for a clean family, not
padding.

### Cached projected-balance determination (the audit-plan-named target)

Audit plan §4 names explicitly: "Any 'balance' columns on InterestParams or
InvestmentParams (e.g., cached projected balances)." **Determination: NO such
column exists. Proven, not assumed:**

- `grep -ni 'balance|projected|cached|accrued|ledger|amount|principal|growth|
  net_worth|dollar' app/models/interest_params.py app/models/investment_params.py`
  -> the **only** match is `investment_params.py:26`, a code *comment* on
  `annual_contribution_limit` ("dollar-denominated"); zero column matches.
- `grep -rln 'interest_params|investment_params|hysa_params'
  migrations/versions/` -> 10 migrations; piping every line that names these
  tables through `grep -i 'balance|projected|cached|growth|principal|accrued|
  ledger'` returns **zero matches**.
- Both creating migrations enumerated column-by-column:
  `f1a2b3c4d5e6_add_hysa_and_account_categories.py:39-66` creates
  `hysa_params` with id / account_id / apy / compounding_frequency /
  timestamps only; `c3d4e5f6g7h8_add_investment_retirement_tables.py:42-74`
  creates `investment_params` with id / account_id / assumed_annual_return /
  annual_contribution_limit / contribution_limit_year /
  employer_contribution_type / employer_flat_percentage /
  employer_match_percentage / employer_match_cap_percentage / timestamps
  only. The two renames (`b4a6bb55f78b`, `44893a9dbcc3`) only rename
  `hysa_params` -> `interest_params`; the C-24/C-25 sweeps
  (`b71c4a8f5d3e`, `c5d20b701a4e`) only add CHECK constraints / server
  defaults; `2c1115378030` only data-INSERTs `interest_params` rows. No
  migration ever added, then dropped, a dollar/balance column.

**Finding (positive, recorded so the developer knows this family is clean by
construction):** No cached projected-balance, accrued-interest, or
growth-total column exists on `InterestParams` or `InvestmentParams`. HYSA /
interest per-period interest and investment growth are **always recomputed
from primaries** by `interest_projection.calculate_interest@:49` and
`growth_engine.project_balance@:164` on every request; there is no
stored-vs-computed drift surface in this family and consequently no
stale-detection requirement (there is nothing to go stale). This is the
clean-by-construction counter-case to Families A/B.

### budget.interest_params.apy

- **Represents:** the account's annual percentage yield, stored as a decimal
  fraction (`0.04500` = 4.5%). `Numeric(7,5)` NOT NULL,
  `server_default="0.04500"` (`interest_params.py:60`), CHECK
  `apy >= 0 AND apy <= 1` (`:33-36`).
- **Computational counterpart:** none. `apy` is a primary input to
  `calculate_interest@interest_projection.py:49` (read at `:89,:92,:98`);
  nothing computes "what apy should be." AUTHORITATIVE.
- **Update path:** the interest-detail handler `accounts.py:1349-1367` --
  construct `InterestParams(account_id=...)` if absent (`:1356-1358`),
  `params.apy = D(str(data["apy"])) / D("100")` **only if `"apy" in data`**
  (`:1360-1363`), `commit` (`:1367`). Handler binds
  `InterestParamsUpdateSchema` (`accounts.py:64`), in which `apy` is **not
  `required`** (`validation.py:1414`; the `required=True` form at `:1397` is
  `InterestParamsCreateSchema`, which this handler does NOT use). Data
  migration `2c1115378030:46-53` INSERTs rows at a fixed `_DEFAULT_APY`.
- **Read paths (direct, no counterpart to bypass since there is none):**
  `balance_calculator.py:144,163` (`apy = interest_params.apy` -> single
  engine); `year_end_summary_service.py:1864` (`apy=interest_params.apy` ->
  same engine); templates `accounts/interest_detail.html:42,85` and
  `savings/dashboard.html:137` format `params.apy|float * 100` for *display
  of the rate itself* (not an interest recompute).
- **Drift risk:** none of the stored-vs-computed kind (no counterpart).
  **Write-path silent-default hazard (the "0 and None" concern, CONFIRMED
  reachable at the schema tier):** `InterestParamsUpdateSchema.apy` is
  optional and `@pre_load strip_empty_strings` (`validation.py:1393-1395`)
  drops a blank `apy`; combined with the `if not params:` create branch
  (`accounts.py:1356`), a *first* save that omits/blanks `apy` constructs and
  commits a new row with **no Python-side apy assignment**, so the column
  `server_default="0.04500"` materialises a silent **4.5%** rate. 4.5% is a
  plausible non-zero value; `calculate_interest` only treats `apy <= 0` as
  "no interest" (`interest_projection.py:83`), so a silently-defaulted 4.5%
  projects real interest the user never configured -- the dangerous
  direction of the coding-standards "`0` and `None` mean different things"
  rule (missing -> plausible non-zero, not -> zero/error). Whether a UI flow
  can POST the create with a blank apy is a behavioural question -> **Q-24**.
- **Stale-detection:** N/A (no cached counterpart; no `stale_*` flag for this
  family -- grep confirms none).
- **Classification: AUTHORITATIVE.**

### budget.investment_params.assumed_annual_return

- **Represents:** assumed annual investment return, decimal fraction
  (`0.07000` = 7%). `Numeric(7,5)` NOT NULL,
  `default=0.07000, server_default=db.text("0.07000")`
  (`investment_params.py:80-83`), CHECK
  `assumed_annual_return >= -1 AND assumed_annual_return <= 1` (`:21-24`;
  note 0 and negative are valid -- a cash sleeve or a down-year assumption).
- **Computational counterpart:** none. Primary input to
  `growth_engine.project_balance@:202,:241` and
  `reverse_project_balance@:335,:354`. AUTHORITATIVE.
- **Update path:** the InvestmentParams create/update handler in
  `investment.py` (validated-field lists at `:759-762` and `:790-791`; the
  exact assignment block was not opened this session -- the column-tier
  hazard below is from the model, read in full, and does not depend on the
  assignment line). Created by `c3d4e5f6g7h8:52`; server-default-swept by
  `c5d20b701a4e:182`.
- **Read paths:** `growth_engine` via `investment.py:211,516,569`;
  `year_end_summary_service.py:1136,1153,1668,1690`;
  `savings_dashboard_service.py:547`;
  **`retirement_dashboard_service.py:321`** (`if params and
  params.assumed_annual_return:` -- truthiness) and **`:476`** (`else
  params.assumed_annual_return` -- raw, 0 honored); template
  `investment/dashboard.html:47,253` format-only.
- **Drift risk:** no stored-vs-computed drift. **Two CONFIRMED "0 and None"
  hazards:** (1) **Python `default=0.07000` is a float literal**
  (`investment_params.py:81`) -- a direct coding-standards violation
  ("Construct Decimals from strings; `Decimal(0.1)` introduces float
  imprecision"); the `server_default=db.text("0.07000")` is correct, only the
  Python-side `default` is the defect (PG re-quantises on store, so the
  persisted value is unaffected -- this is a code-quality finding, not a
  wrong-number one). (2) **Read-path `:321` truthiness**: a stored
  `assumed_annual_return == Decimal("0")` (a valid stable-value sleeve under
  the `>= -1` CHECK) is falsy, so that account is dropped from the
  balance-weighted average displayed on `/retirement` while `:476` still
  feeds its 0 into the projection -- the displayed return overstates, the
  projection does not. This is **F-042's second sub-defect** (CONFIRMED at
  source this session; not re-derived -- see the re-verification log and
  consumer-routing section). Plus the same write-path silent-default hazard
  as `apy` (missing -> 7%, not zero), shared into **Q-24**.
- **Stale-detection:** N/A (no cached counterpart).
- **Classification: AUTHORITATIVE.**

### budget.investment_params.annual_contribution_limit

- **Represents:** the account's annual contribution cap, dollars.
  `Numeric(12,2)` **nullable** (NULL = no configured cap), CHECK
  `annual_contribution_limit IS NULL OR annual_contribution_limit >= 0`
  (`investment_params.py:31-35`) -- so a stored **`0`** is a valid,
  distinct-from-NULL value.
- **Computational counterpart:** none for the *stored limit*. It is a
  pass-through input (`investment_projection.py:190`
  `getattr(..., "annual_contribution_limit", None)`); the *enforcement* is
  `growth_engine.project_balance:206-209` (`if annual_contribution_limit is
  not None: remaining = limit - ytd; max(.,0)`). AUTHORITATIVE input.
- **Update path:** InvestmentParams create/update handler in `investment.py`
  (field list `:759`); created by `c3d4e5f6g7h8:58`.
- **Read paths -- CONFIRMED tri-consumer "0 vs None" divergence:** a stored
  `Decimal("0")` is interpreted three different ways on one app:
  1. `investment.py:231` `if params and params.annual_contribution_limit:` --
     **truthiness** -> `0` falsy -> `limit_info = None` -> the contribution-
     limit card is **suppressed entirely** (treated as "no limit set").
  2. `investment.py:667` `if inv_params and inv_params.annual_contribution_
     limit:` -- truthiness -> `0` falsy -> the contribution-transfer default
     falls to the `Decimal("500.00")` literal (`:670`) (treated as "no
     limit").
  3. `growth_engine.py:206` `if annual_contribution_limit is not None:` --
     **`is not None`** -> `0` honored as an **absolute zero cap**: every
     period's contribution is `min(x, 0) = 0`, so the projection records
     zero contributions for the account.
  One stored `0` therefore means "no limit (card hidden)" + "no limit ($500
  default)" + "hard zero, nothing counts" simultaneously, with no error and
  no label -- a coding-standards "`0` and `None` mean different things"
  violation and an E-04-class silent divergence on a stored input column.
  Whether `annual_contribution_limit = 0` is a meaningful user state or
  should be normalised to NULL is a developer-intent question -> **Q-24**.
- **Drift risk:** no stored-vs-computed drift; the hazard is the
  cross-consumer interpretation split above, not staleness.
- **Stale-detection:** N/A.
- **Classification: AUTHORITATIVE** (input). The tri-consumer 0/None split is
  a read-path consistency finding, not an authority ambiguity.

### budget.investment_params.employer_flat_percentage / employer_match_percentage / employer_match_cap_percentage

Grouped: structurally identical (`Numeric(5,4)` **nullable**, CHECK
`IS NULL OR` bounded -- flat `[0,1]` `:40-44`, match `[0,10]` `:53-57`, cap
`[0,1]` `:64-68`; columns `:90,:91,:92`).

- **Represents:** employer-match configuration; consumed only by
  `growth_engine.calculate_employer_contribution@:91-127`.
- **Computational counterpart:** none. All three are primaries. AUTHORITATIVE.
- **Update path:** InvestmentParams create/update handler in `investment.py`
  (field lists `:761-762`, `:790-791`); created by `c3d4e5f6g7h8:66-68`.
- **Read paths:** the sole adapter is `investment_projection.py:169-171`,
  `getattr(investment_params, "employer_<x>", None) or ZERO` -> the
  `employer_params` dict consumed by `calculate_employer_contribution`
  (`growth_engine.py:113,117-118`); template `investment/dashboard.html:
  287,293,299` format-only.
- **Drift risk:** none stored-vs-computed. The `or ZERO` coerces a stored
  `Decimal("0")` to `ZERO` -- the same value `None` would yield -- but here
  this is **benign**: a 0% employer rate and "no employer" both correctly
  produce a $0 employer contribution, so unlike `annual_contribution_limit`
  the 0/None conflation changes no result. Recorded for completeness, not
  flagged as a divergence.
- **Stale-detection:** N/A.
- **Classification: AUTHORITATIVE** (all three).

### Consumer-routing consistency (apy_interest and growth -- the E-04 test)

**apy_interest -- AGREE (single canonical engine; every consumer delegates;
no inline recompute):** `calculate_interest@interest_projection.py:49` is the
sole arithmetic producer (`grep` of `app/` for `calculate_interest`
confirms). Verified at source this session: `calculate_balances_with_interest
@balance_calculator.py:112-173` computes base balances via `calculate_balances
@:135` then *delegates* every per-period interest to `calculate_interest
@:161-167` (`apy = interest_params.apy` raw, `:144`); `_compute_pre_anchor_
interest@year_end_summary_service.py:1863-1869` calls the same engine
(`apy=interest_params.apy`). No service, route, JS, or template recomputes
per-period interest -- the only template uses of `params.apy` are
`"%.Nf"|format(apy|float*100)` *rate displays*. **No source-of-truth
divergence for apy_interest.**

**growth -- engine AGREE, input-resolution DIVERGE (SILENT_DRIFT):** G1/G2
have a single engine, `growth_engine.project_balance/reverse_project_balance`;
every consumer (`investment.py:209`, `retirement_dashboard_service.py:480`,
`year_end_summary_service.py:1136/1668`, `savings_dashboard_service.py:547`)
delegates -- no inline growth recompute. The divergence is **upstream of the
engine, in how the rate inputs are resolved for the same `/retirement`
render** (F-042, CONFIRMED at source this session): the displayed SWR
(`compute_slider_defaults:304`, `settings.safe_withdrawal_rate is None`)
versus the SWR that actually drives the gap math and `chart_data
investment_income` (`compute_gap_data:220`, `Decimal(str(... or "0.04"))`
truthiness) disagree for an explicit-zero stored SWR; and the displayed
balance-weighted return drops zero-`assumed_annual_return` accounts
(`:321` truthiness) while the projection at `:476` keeps them. Same stored
columns, two zero-handling rules, one page, no error -- the **E-04 violation
for HYSA/investment projections** is on the *rate-input resolution* layer, not
the engines. Classify **SILENT_DRIFT**; owned by F-042 (not re-derived here);
the per-column read-path entries above record where each stored column feeds
it.

### Phase-3 re-verification log (Family C)

Every Phase-3 finding Explore surfaced was opened at its cited `path:line`
this session and confirmed against source. No `03_consistency.md` edit (per
the P4 protocol; corrections go to `09`).

- **F-041** (`apy_interest`, AGREE): `calculate_interest@interest_projection.
  py:49`, `calculate_balances_with_interest@balance_calculator.py:112-173`,
  `_compute_pre_anchor_interest@year_end_summary_service.py:~1863`,
  `DAYS_IN_YEAR=Decimal("365")@interest_projection.py:44`,
  single-`quantize@:114`. **CONFIRMED** (the F-041 cite of
  `_compute_pre_anchor_interest@:1864` lands on the `calculate_interest(`
  call that spans `:1863-1869` -- accurate to within the call block).
- **F-042** (`growth` SWR/return SILENT_DRIFT): `compute_gap_data:217-221`
  (`or "0.04"` at `:220`), `chart_data:239-241`,
  `compute_slider_defaults:257-332` (`is None` at `:304`, Decimal quantize
  `:307-309`, `if params and params.assumed_annual_return:` truthiness at
  `:321`). **CONFIRMED -- every cited line exact**, including the docstring
  `:295-301` accurately scoping its own claims. The third anchor `:476`
  (`else params.assumed_annual_return`, raw, 0 honored) corroborates the
  `:321`-vs-projection split.
- **F-043** (`employer_contribution` uncapped-card vs capped-chart): Path A
  `investment.py:185-189` (`calculate_employer_contribution(employer_params,
  periodic_contribution)`, `periodic_contribution = inputs.periodic_
  contribution@:183`, **uncapped**) **CONFIRMED**; Path B
  `growth_engine.py:258-267` (limit-capped `contribution` then same engine)
  **CONFIRMED** via the growth-engine read. Single canonical
  `calculate_employer_contribution@:91-127` confirmed.
- **F-044** (`contribution_limit_remaining`, "AGREE single-path,
  route-resident `limit - ytd` subtraction at `investment.py:173-181`"):
  **MISCITED -- two errors.** (a) **Line error:** `investment.py:173-181` is
  the `calculate_investment_inputs(...)` call; `limit_info` is built at
  **`investment.py:230-238`**. (b) **Substance error:** there is **no
  `limit - ytd` subtraction anywhere** in the route or template.
  `limit_info = {"limit": annual_contribution_limit, "ytd":
  ytd_contributions, "pct": min(100, int(ytd/limit*100))}` (`:232-238`);
  template `investment/dashboard.html:76,88` renders "`ytd / limit`" and the
  `pct` bar only. The concept `contribution_limit_remaining`
  (`02_concepts.md:2169-2200` defines it as `annual_contribution_limit -
  ytd_contributions`) is **never computed or displayed**. F-044's "AGREE,
  single-path, route-resident subtraction" verdict therefore rests on code
  that does not exist; the underlying truth is the `annual_contribution_
  limit` column finding above (input, AUTHORITATIVE, tri-consumer 0/None
  split) plus a derived `pct`. Recorded here; correction question **Q-24**;
  `01`/`02`/`03` left unedited. (`01_inventory.md` §1.2 itself is
  *accurate* -- it pins `calculate_investment_inputs @ 173-181` correctly and
  lists `limit_info` only as a context var without a wrong line; the miscite
  is specific to `02_concepts.md` and `03_consistency.md` F-044.)
- **F-045** (`ytd_contributions`, AGREE single producer): `calculate_
  investment_inputs@investment_projection.py:100`, Step-4 ytd accumulation
  `:175-187` (`ytd_contributions += Decimal(str(t.estimated_amount))` gated
  `and not t.status.excludes_from_balance` at `:185-186`), active-filter
  `:147-151`. **CONFIRMED at source** (file read in full).

**Phase-1 reconciliation (`01_inventory.md` §1.5, not verified by any prior
session):** the `interest_params.py` and `investment_params.py` §1.5 blocks
were re-checked against the full model reads -- every column file:line, type,
nullability, server-default, CHECK-line, concept token, and "Computed
properties: none" is **ACCURATE**. No Phase-1 correction question is needed
for Family C's model blocks (contrast Q-21's `account.py` block).

---

## Family D - Triage sweep

The long tail of stored-monetary columns plus three escalation groups that
*could* be DERIVED and carry a real drift surface. Read in full this session:
`app/models/transaction.py` (284 lines), `app/models/savings_goal.py` (135),
`app/models/calibration_override.py` (177), `app/services/savings_goal_service.py`
(489), `app/services/calibration_service.py` (146), and the
`Transaction.effective_amount` property at source; `app/audit_infrastructure.py`
(374) for the audit-log determination; `app/routes/salary.py:1064-1189`,
`app/routes/savings.py:71-236`, `app/services/savings_dashboard_service.py:648-722`
read at the relevant handlers. Trust-but-verify: every `path:line` below was
opened in source this session; the ~40 triage rows are each backed by a
per-column `grep` of `app/services/` for a write (a service computing+storing
the column); the bypass spot-check (re-verification log) opened 11 random
`02_concepts.md` bypass-table entries at their cited `path:line`. No claim is
inherited from any Explore summary or prior phase without an independent read.

### Escalation 1 -- budget.transactions.actual_amount

- **Represents:** the recorded actual dollar amount of an income/expense once
  known (vs the projected `estimated_amount`). `Numeric(12,2)` **nullable**
  (`transaction.py:159`); CHECK `actual_amount IS NULL OR actual_amount >= 0`
  (`transaction.py:116-119`) -- a stored `0` is a valid distinct-from-NULL
  value (the docstring's "waived fee", `transaction.py:242-244`).
- **Computational counterpart:** `Transaction.effective_amount`@
  `transaction.py:221-245` (read in full this session) -- the 4-tier rule
  (1) `is_deleted`->`Decimal("0")`; (2) `status.excludes_from_balance`->`0`;
  (3) `actual_amount if actual_amount is not None`; (4) else
  `estimated_amount`. The column is an **AUTHORITATIVE recorded input**;
  `effective_amount` is the read-side canonical *accessor*, not a value that
  is ever written back over the column.
- **Update path:** user-driven only -- `mark_done`@`transactions.py:614`
  (`txn.actual_amount = actual_amount` when the form supplies one, CONFIRMED
  at source), `mark_paid`@`dashboard.py:128` (same, CONFIRMED),
  `_update_actual_if_paid`@`entry_service.py:70` (`txn.actual_amount =
  compute_actual_from_entries(txn.entries)` gated `status_id == done_id and
  txn.entries`, CONFIRMED), `settle_from_entries`@`transaction_service.py:153`,
  the full edit form. `transfer_service` writes shadow `estimated_amount`
  only, never `actual_amount` (transfer shadows carry `actual_amount=None`).
  No service computes-and-stores it from a separate authority -- it IS a
  primary.
- **Read paths (the drift surface):** the bypass set -- every direct read of
  `actual_amount`/`estimated_amount` that does not go through
  `effective_amount`. Enumerated exhaustively in the Phase-2 consolidated
  bypass table `02_concepts.md:2353-2410` (~43 sites: 25 service + 5 route +
  ~13 template + 0 JS) and assessed in `03_consistency.md` **F-027**
  (Verdict **DIVERGE**, classification **SILENT_DRIFT**: the S1 entries-load
  feeds F-002/F-009; F-028 carries a SILENT cross-anchor inconsistency,
  UNKNOWN blocked on Q-08). **Not re-enumerated here per instruction;**
  cross-referenced and spot-verified -- 11 of the ~43 entries opened at source
  this session, all CONFIRMED (see re-verification log). Highest-risk rows are
  the hand-rolled 2-tier mirrors that omit tiers 1-2 (is_deleted,
  excludes_from_balance): `credit_workflow.py:229`, `_transaction_cell.html:17`,
  `_mobile_grid.html:92,179`, `budget_variance_service.py:390-393`.
- **Drift risk:** the column does not go stale (it is a primary, not a cache).
  The divergence is read-side: at the hand-rolled-mirror sites a
  Credit/Cancelled/soft-deleted transaction yields a non-zero displayed or
  aggregated amount the property would zero. SILENT_DRIFT per F-027 -- a
  consistency drift, not a source-of-truth staleness.
- **Stale-detection:** N/A (primary column; grep for any `stale_*` flag on
  transactions returns none).
- **Classification: AUTHORITATIVE** (recorded input). The bypass set is the
  divergence surface and is owned by F-027/F-028 + `02_concepts.md`.

### Escalation 2 -- budget.savings_goals.contribution_per_period

- **Represents:** the user's planned recurring contribution toward the goal,
  per pay period. `Numeric(12,2)` **nullable** (`savings_goal.py:77`); CHECK
  `contribution_per_period IS NULL OR contribution_per_period > 0`
  (`savings_goal.py:46-48`).
- **Determination -- user-entered, NOT auto-computed/stored (DISPROVES the
  escalation hypothesis):** tree-wide `grep contribution_per_period app/
  scripts/ migrations/` this session. The only writers are: form input
  `savings/goal_form.html:117-124`; Marshmallow `validation.py:1013` (create)
  / `:1114` (update), both `fields.Decimal`; `create_goal`@`savings.py:143`
  (`SavingsGoal(user_id=..., **data)`); `update_goal`@`savings.py:236`
  (`setattr(goal, field, value)` over `SAVINGS_GOAL_FIELDS`@`savings.py:37`,
  which lists `contribution_per_period`); plus the initial-schema/CHECK
  migrations. **Zero service writes it** (per-column `grep` of
  `app/services/` -> NONE).
- **Computational counterpart -- a separate, never-persisted display figure:**
  `calculate_required_contribution(current_balance, target_amount,
  remaining_periods)`@`savings_goal_service.py:109-136` computes
  `(target-current)/remaining_periods` (`:127-136`), consumed by
  `savings_dashboard_service.py:676-678` into the `required_contribution`
  context key (`:717`). Read in full this session: `compute_dashboard_data`
  assigns only local `resolved_target`/`required`/`trajectory` into the
  goal_data dict (`savings_dashboard_service.py:665-722`); it is **never
  written back to `goal.contribution_per_period`**. The dashboard shows the
  stored `contribution_per_period` (`savings/dashboard.html:411-414`) and the
  computed `required_contribution` as two distinct figures. Trajectory pace
  uses `monthly_contribution` derived from recurring *transfer templates*
  (`compute_committed_monthly`@`savings_dashboard_service.py:700`), NOT the
  goal column.
- **Update path:** `create_goal`@`savings.py:143`, `update_goal`@
  `savings.py:236` -- purely from validated form data; no recompute.
- **Drift risk:** none of the stored-vs-computed kind -- pure user input, no
  computational counterpart overwrites it; the escalation's "auto-computed,
  stale when target/income/horizon change" hypothesis is **disproven by
  grep+read**. Nuance recorded, not flagged as drift: the stored
  `contribution_per_period` (plan) and the computed `required_contribution`
  (need) are different concepts both on `/savings`; the code never reconciles
  or validates one against the other (a user can store $50/period while the
  dashboard says $400/period is required). Whether they are *intended* to be
  reconciled is a developer-intent question -> **Q-25**.
- **Stale-detection:** N/A (no cached counterpart).
- **Classification: AUTHORITATIVE** (pure user-entered input). NOT DERIVED.

### Escalation 3 -- budget.calibration_overrides.effective_federal_rate / effective_state_rate / effective_ss_rate / effective_medicare_rate (grouped)

- **Represents:** effective tax rates derived from one real pay stub,
  persisted as decimal fractions in `Numeric(12,10)` NOT NULL, each CHECK-
  pinned to `[0,1]` (`calibration_override.py:53-68,89-92`); fed straight
  into the calibrated paycheck tax path.
- **Derived from the same row's actual_* columns -- CONFIRMED:**
  `derive_effective_rates`@`calibration_service.py:34-103` (read in full):
  `effective_federal = actual_federal_tax / taxable`, `effective_state =
  actual_state_tax / taxable` (`:83-88`), `effective_ss = actual_social_
  security / actual_gross_pay`, `effective_medicare = actual_medicare /
  actual_gross_pay` (`:91-96`), where `taxable = actual_gross_pay - profile
  pre-tax deductions` (`salary.py:1086-1095`). The four columns are a
  denormalization of the row's own `actual_*` columns plus the profile's
  pre-tax-deduction total.
- **Update path -- the crux (two confirmed silent drift surfaces):** the ONLY
  writer is the two-step `calibrate_preview`->`calibrate_confirm`
  (`salary.py:1064-1176`; per-column service-write grep -> NONE).
  `calibrate_preview` calls `derive_effective_rates(...)`@`salary.py:1105-1112`
  and renders the rates; `salary/calibrate_confirm.html:97-100` carries them
  as **client-side hidden form inputs**; `calibrate_confirm` deletes any
  existing row and inserts a new `CalibrationOverride` storing
  `effective_*_rate=data["effective_*_rate"]` **straight from the posted form
  (`salary.py:1161-1164`) -- it does NOT re-call `derive_effective_rates`** and
  stores `actual_*=data["actual_*"]` independently (`:1156-1160`) from the same
  POST.
  1. **Stored-pair-consistency drift (CONFIRMED at source):** confirm persists
     the client-submitted rate pair and actual_* pair as independent form
     fields with **no server-side re-derivation or cross-check** that
     `effective_x == actual_x / base` for the values actually stored;
     `_calibration_confirm_schema` (`validation.py:1858-1873`) only range-pins
     each to `[0,1]`. A tampered/replayed/stale two-step POST stores a rate
     pair inconsistent with the actual_* pair; nothing detects it.
  2. **Stale-on-upstream-edit (CONFIRMED structural):** the federal/state
     divisor `taxable` depends on the profile's pre-tax deductions *at preview
     time* (`salary.py:1086-1095`). Editing the profile's pre-tax deductions
     or salary afterward does NOT recompute the stored rates (no recompute
     trigger -- the only writer is calibrate_confirm); the saved calibration
     silently keeps a rate derived against the old taxable base.
- **Read path:** `apply_calibration`@`calibration_service.py:106-145` (read in
  full) multiplies the stored rate against the **live** per-period
  taxable/gross every calibrated paycheck (`taxable*effective_federal_rate`
  etc., `:133-144`), gated `calibration.is_active`
  (`paycheck_calculator.py:160-167`). A stale or inconsistent stored rate
  silently produces wrong federal/state/FICA withholding on every projected
  paycheck with no error.
- **Drift risk:** real and silent, via both update-path surfaces above.
- **Stale-detection:** **NONE.** `CalibrationOverride` carries `pay_stub_date`
  and `is_active` but no derivation-freshness check; grep for any
  recompute/stale guard on `effective_*` vs `actual_*` -> none; migration
  `b71c4a8f5d3e` (C-24) only range-pins `[0,1]`, it does not enforce the
  derivation relationship.
- **Classification: UNCLEAR** (audit-plan: "the codebase does not
  consistently treat the column as any of [AUTHORITATIVE/CACHED/DERIVED]").
  It is computed from `actual_*`+`taxable` (DERIVED shape), persisted as a
  frozen client-submitted snapshot never recomputed (AUTHORITATIVE-snapshot
  shape), and consumed by `apply_calibration` as the live source of truth
  (CACHED-consumed shape). Under "frozen pay-stub snapshot" intent it is
  AUTHORITATIVE-snapshot and the actual_*-vs-rate inconsistency window is the
  defect; under "live derived rate" intent it is DERIVED-stale and the
  missing recompute-on-profile-edit is the defect. Per hard rule 5 / Phase-4
  decision 2 the auditor does not pick a side -> **Q-25**; both readings are
  written with `path:line` above. **UNCLEAR is itself the finding.**

### Audit-log / ledger source-of-truth determination (audit-plan-named target)

Audit plan §4 names: "Any ledger-style or audit columns that store dollar
values." **Determination, proven not assumed:**

- **No numeric dollar column exists on `system.audit_log`.** Canonical DDL
  `app/audit_infrastructure.py:139-155` (read in full; creating migration
  `migrations/versions/a8b1c2d3e4f5_add_audit_log_and_triggers.py`, rebuilt by
  `a5be2a99ea14_rebuild_audit_infrastructure.py`, `executed_at` NOT NULL by
  `b2b1ff4c3cea`): columns are `id BIGSERIAL`, `table_schema`, `table_name`,
  `operation`, `row_id INTEGER`, `old_data JSONB`, `new_data JSONB`,
  `changed_fields TEXT[]`, `user_id INTEGER`, `db_user`, `executed_at
  TIMESTAMPTZ`. Dollar values appear ONLY inside the opaque `old_data`/
  `new_data` JSONB row snapshots, never as a typed `Numeric` column. Grep of
  every `audit_log` migration for `numeric|money|amount|balance|principal|
  decimal` -> zero matches.
- **No application code reads `system.audit_log` as a calculation /
  source-of-truth input (absence proven by grep).**
  `grep -rn 'SELECT.*audit_log|query.*audit log|from.*audit_log|AuditLog'
  app/ --include='*.py'` (excluding tests, the infra module, comments) ->
  **NONE**. No SQLAlchemy model maps `system.audit_log`. The only
  `app/routes/` mentions are comments (`accounts.py:169`, `auth.py:576`); the
  remaining hits are `app/audit_infrastructure.py` (writer-side DDL/trigger
  only) and the `app/utils/log_events.py:14-19` docstring. The table is
  forensic-only, written exclusively by `system.audit_trigger_func`
  (`audit_infrastructure.py:173-252`), never an input to any money
  calculation.

**Finding (positive):** the audit-plan "ledger/audit columns that store
dollar values" concern is cleanly disproven for this codebase -- there is no
numeric ledger column, and nothing reads the audit trail back into a
calculation. No source-of-truth drift surface exists here. (Stages B/C of
Section 2 -- a future double-entry ledger -- are decision-pending per
`00_priors.md:152`; this finding is scoped to the code as it exists today.)

### Family D triage table (one grep per column; AUTHORITATIVE unless a service computes+stores it)

Each row's classification is backed by a `grep -rn '\.<col>\s*=' app/services/`
this session (a service computing+storing the column would surface there);
"svc-write: NONE" is the grep basis. `path:line` from the §1.5 blocks,
re-confirmed against the full model reads for the three Family-D models.

| column | type | class | reason (grep basis) | counterpart |
| --- | --- | --- | --- | --- |
| budget.transactions.estimated_amount | Numeric(12,2) NOT NULL | AUTHORITATIVE / DERIVED-by-invariant (shadow rows) | non-shadow = raw user input; svc-write only `transfer_service.py:487-488,818` keeping shadow `estimated_amount == Transfer.amount` (Invariant #3, authorized mutator per CLAUDE.md #4) | none (non-shadow); `Transfer.amount` via transfer_service (shadow) -- owned by F-029/F-031 |
| budget.transaction_entries.amount | Numeric(12,2) NOT NULL | AUTHORITATIVE | raw envelope-entry input; svc-write: NONE | none |
| budget.transfers.amount | Numeric(12,2) NOT NULL | AUTHORITATIVE | raw transfer input; `Transfer.effective_amount` derives FROM it; svc-write only transfer CRUD | none (F-029 AGREE) |
| budget.transaction_templates.default_amount | Numeric(12,2) | AUTHORITATIVE | template default input; svc-write: NONE | none |
| budget.transfer_templates.default_amount | Numeric(12,2) | AUTHORITATIVE | transfer-template default input; svc-write: NONE | none |
| budget.savings_goals.target_amount | Numeric(12,2) nullable | AUTHORITATIVE | user input; `resolve_goal_target` uses it (fixed) / computes income-relative target but **never stores** (`savings_dashboard_service.py:665-718` local only); svc-write: NONE | resolve_goal_target (read-only, never persisted) |
| budget.savings_goals.income_multiplier | Numeric(8,2) nullable | AUTHORITATIVE | user input; svc-write: NONE | none |
| salary.salary_profiles.annual_salary | Numeric(12,2) NOT NULL | AUTHORITATIVE | user input; grep hit `pension_calculator.py:102` is `_FakeProfile.annual_salary` (local helper object, NOT the DB column -- verified at source `:96-104`) | none |
| salary.salary_profiles.additional_income | Numeric(12,2) NOT NULL | AUTHORITATIVE | user input; svc-write: NONE | none |
| salary.salary_profiles.additional_deductions | Numeric(12,2) NOT NULL | AUTHORITATIVE | W-4 4(b) user input; svc-write: NONE | none |
| salary.salary_profiles.extra_withholding | Numeric(12,2) NOT NULL | AUTHORITATIVE | W-4 4(c) user input; svc-write: NONE | none |
| salary.paycheck_deductions.amount | Numeric(12,4) NOT NULL | AUTHORITATIVE | user input; svc-write: NONE | none |
| salary.paycheck_deductions.annual_cap | Numeric(12,2) nullable | AUTHORITATIVE | user input (limit); svc-write: NONE | none |
| salary.paycheck_deductions.inflation_rate | Numeric(5,4) nullable | AUTHORITATIVE | user input; svc-write: NONE | none |
| salary.salary_raises.percentage | Numeric(5,4) nullable | AUTHORITATIVE | user input; svc-write: NONE | none |
| salary.salary_raises.flat_amount | Numeric(12,2) nullable | AUTHORITATIVE | user input; svc-write: NONE | none |
| salary.calibration_overrides.actual_gross_pay | Numeric(10,2) NOT NULL | AUTHORITATIVE | pay-stub transcription (audit trail); svc-write: NONE; divisor for effective_ss/medicare | none (it is the derivation base) |
| salary.calibration_overrides.actual_federal_tax | Numeric(10,2) NOT NULL | AUTHORITATIVE | pay-stub transcription; svc-write: NONE | none (numerator for effective_federal_rate, Escalation 3) |
| salary.calibration_overrides.actual_state_tax | Numeric(10,2) NOT NULL | AUTHORITATIVE | pay-stub transcription; svc-write: NONE | none (numerator for effective_state_rate) |
| salary.calibration_overrides.actual_social_security | Numeric(10,2) NOT NULL | AUTHORITATIVE | pay-stub transcription; svc-write: NONE | none (numerator for effective_ss_rate) |
| salary.calibration_overrides.actual_medicare | Numeric(10,2) NOT NULL | AUTHORITATIVE | pay-stub transcription; svc-write: NONE | none (numerator for effective_medicare_rate) |
| salary.calibration_deduction_overrides.actual_amount | Numeric(10,2) NOT NULL | AUTHORITATIVE | pay-stub deduction transcription; svc-write: NONE | none |
| salary.pension_profiles.benefit_multiplier | Numeric(7,5) NOT NULL | AUTHORITATIVE | plan-config user input; svc-write: NONE (pension_calculator reads it) | none |
| salary.tax_bracket_sets.standard_deduction | Numeric(12,2) NOT NULL | AUTHORITATIVE | seeded/admin tax config; svc-write: NONE | none |
| salary.tax_bracket_sets.child_credit_amount | Numeric(12,2) NOT NULL srv-dflt "0" | AUTHORITATIVE | seeded/admin tax config; svc-write: NONE | none |
| salary.tax_bracket_sets.other_dependent_credit_amount | Numeric(12,2) NOT NULL srv-dflt "0" | AUTHORITATIVE | seeded/admin tax config; svc-write: NONE | none |
| salary.tax_brackets.min_income | Numeric(12,2) NOT NULL | AUTHORITATIVE | seeded bracket boundary; svc-write: NONE | none |
| salary.tax_brackets.max_income | Numeric(12,2) nullable | AUTHORITATIVE | seeded bracket boundary (NULL = top bracket); svc-write: NONE | none |
| salary.tax_brackets.rate | Numeric(5,4) NOT NULL | AUTHORITATIVE | seeded bracket rate; svc-write: NONE | none |
| salary.state_tax_configs.flat_rate | Numeric(5,4) nullable | AUTHORITATIVE | admin/seeded state config; svc-write: NONE | none |
| salary.state_tax_configs.standard_deduction | Numeric(12,2) nullable | AUTHORITATIVE | admin/seeded state config; svc-write: NONE | none |
| salary.fica_configs.ss_rate | Numeric(5,4) NOT NULL srv-dflt 0.0620 | AUTHORITATIVE | seeded FICA config; svc-write: NONE | none |
| salary.fica_configs.ss_wage_base | Numeric(12,2) NOT NULL srv-dflt 176100 | AUTHORITATIVE | seeded FICA cap; svc-write: NONE | none |
| salary.fica_configs.medicare_rate | Numeric(5,4) NOT NULL srv-dflt 0.0145 | AUTHORITATIVE | seeded FICA config; svc-write: NONE | none |
| salary.fica_configs.medicare_surtax_rate | Numeric(5,4) NOT NULL srv-dflt 0.0090 | AUTHORITATIVE | seeded FICA config; svc-write: NONE | none |
| salary.fica_configs.medicare_surtax_threshold | Numeric(12,2) NOT NULL srv-dflt 200000 | AUTHORITATIVE | seeded FICA threshold; svc-write: NONE | none |
| auth.user_settings.default_inflation_rate | Numeric(5,4) nullable | AUTHORITATIVE | user setting; svc-write: NONE | none |
| auth.user_settings.low_balance_threshold | Integer nullable | AUTHORITATIVE | user alert setting; svc-write: NONE | none |
| auth.user_settings.large_transaction_threshold | Integer NOT NULL srv-dflt 500 | AUTHORITATIVE | user alert setting; svc-write: NONE | none |
| auth.user_settings.safe_withdrawal_rate | Numeric(5,4) nullable srv-dflt 0.0400 | AUTHORITATIVE | user input; svc-write: NONE; zero-vs-None read-path hazard owned by F-042/PA-04 (cross-link only) | none |
| auth.user_settings.trend_alert_threshold | Numeric(5,4) NOT NULL srv-dflt 0.1000 | AUTHORITATIVE | user input; svc-write: NONE; Marshmallow-vs-CHECK range gap owned by PA-01 (cross-link only) | none |

No stored-monetary column outside this list was found during the per-column
greps (the audit-plan list is "not exhaustive"; none required adding).

### Phase-3 re-verification log (Family D - effective_amount bypass spot-check)

The `actual_amount`/`estimated_amount` bypass spot-verify IS this session's
Phase-3 re-verification. 11 entries chosen across services/routes/templates
from the `02_concepts.md:2353-2410` consolidated table were opened at their
cited `path:line` this session. No `02`/`03` edit (P4 protocol).

- **`balance_calculator.py:292` `_entry_aware_amount`** -- def at `:291`,
  cleared/uncleared partition + `max(estimated_amount - cleared_debit -
  sum_credit, uncleared_debit)` at `:384-385`. **CONFIRMED** (cite `:292
  (374-378,384-385)`; def one line off, body lines exact -- accurate within
  the function block, the Family-C convention).
- **`dashboard_service.py:239,245`** -- `:239`
  `compute_remaining(txn.estimated_amount, txn.entries)`, `:245`
  `total > txn.estimated_amount`. **CONFIRMED exact.**
- **`year_end_summary_service.py:519-528`** -- `db.func.sum(TransactionEntry.
  amount)` `:519` + credit `case(...)` `:520-528`. **CONFIRMED exact**
  (also matches `01_inventory.md` §1.6).
- **`credit_workflow.py:229` `mark_as_credit`** -- `payback_amount =
  txn.actual_amount if txn.actual_amount is not None else txn.estimated_amount`.
  **CONFIRMED exact** hand-rolled 2-tier mirror (omits tiers 1-2).
- **`entry_service.py:70` `_update_actual_if_paid`** -- `txn.actual_amount =
  compute_actual_from_entries(txn.entries)` gated `status_id == done_id and
  txn.entries`. **CONFIRMED exact.**
- **`investment_projection.py:153,187`** -- `sum(Decimal(str(t.estimated_
  amount)) ...)` `:153`, `ytd_contributions += Decimal(str(t.estimated_
  amount))` gated `not t.status.excludes_from_balance` `:187`. **CONFIRMED.**
- **`routes/companion.py:52-56` `_build_entry_data`** -- `remaining =
  compute_remaining(...)` `:52`, `pct = float(total / txn.estimated_amount *
  Decimal("100"))` `:54-56`. **CONFIRMED** (table row R2 cites `:54-55`; the
  expression spans `:54-56` -- accurate within the call block; the
  float-on-money + route-arithmetic concern confirmed present).
- **`routes/transactions.py:614` `mark_done`** -- `txn.actual_amount =
  actual_amount` (WRITE). **CONFIRMED exact.**
- **`grid/_transaction_cell.html:17,21`** -- `:17`
  `display_amount = t.actual_amount if t.actual_amount is not none else
  t.estimated_amount` (2-tier mirror), `:21` `remaining = t.estimated_amount
  - es.total` (Jinja arithmetic). **CONFIRMED exact.**
- **`grid/_mobile_grid.html:92,96`** -- `:92` mirror, `:96` Jinja
  `remaining = txn.estimated_amount - es.total`. **CONFIRMED exact** (the
  `:179,183` second occurrence is structurally identical, not separately
  reopened).
- **`routes/dashboard.py:128` `mark_paid`** -- `txn.actual_amount =
  actual_amount` (WRITE). **CONFIRMED exact.**

**Result: 11/11 CONFIRMED, zero MISCITED, zero STALE** (two within-call-block
line offsets noted, consistent with the Family-C standard). The `02_concepts.md`
bypass table is reliable for the entries sampled; no Phase-4 miscite finding
and no `09` correction is required for this sample (contrast P4-c's F-044
miscite).

**Phase-1 reconciliation (`01_inventory.md` §1.5, not verified by any prior
session):** the `transaction.py`, `savings_goal.py`, and
`calibration_override.py` §1.5 blocks were re-checked against the full model
reads this session -- every column file:line (`transaction.py:158/159`,
`savings_goal.py:75/77/115`, `calibration_override.py:80-84/89-92/164`), type,
nullability, server-default, CHECK-line (`transaction.py:112-119`,
`savings_goal.py:41-54`, `calibration_override.py:28-66/136`), concept token,
and "Computed properties: none" (`savings_goal.py`, `calibration_override.py`)
/ the five-property list (`transaction.py:221-283`) is **ACCURATE**. No
Phase-1 correction question is needed for Family D's model blocks (contrast
Q-21's `account.py` block; consistent with Family C).

---

## Phase 4 - Verification and consolidation

Session P4-e. Strictly ADDITIVE: Families A/B/C/D above are untouched. Theme:
trust-but-verify applied to the audit's own output (audit-plan section 10.8
"trust-then-verify gap" remedy). Every claim below was produced by a
mechanical `grep`/`glob` over the audit files plus a COLD reopen of cited
source this session; nothing is inherited from an Explore summary or a prior
P4 session without an independent re-read.

### Deliverable 1 - Self-spot-check (the capstone)

**Citation census.** `grep -noE '[a-zA-Z0-9_./-]+\.(py|html|js):[0-9]+(-[0-9]+)?'
docs/audits/financial_calculations/04_source_of_truth.md` -> **424** total
`path:line` occurrences, **337** distinct globally.

**Selection method (reproducible, content-blind, spans all five families).**
(1) Bucket every occurrence into its family by the 04 document line it sits on,
using the family line ranges Family A `35-294`, Family B-principal `296-742`,
Family B-rate/escrow `744-1319`, Family C `1321-1625`, Family D `1627-1949`
(zero occurrences fell outside these ranges). (2) Within each family build the
distinct-citation list in first-occurrence order (per-family distinct counts:
A=75, Bp=78, Br=86, C=41, D=61). (3) Sample 1 picks the citations at 1-based
indices `round(N/3)` and `round(2N/3)` per family (stride = N/3, offset off the
section edges) -> 10 citations, 2 per family. (4) Each selected `path:line` was
opened COLD in current source (files not previously read this session at those
lines) and compared to what 04 claims at the 04 line where the citation
appears. The method is positional only -- it cannot have been steered toward
clean citations.

| # | fam | 04 line | selected cite | resolved path | 04's claim (abbrev.) | result |
| - | --- | ------- | ------------- | ------------- | -------------------- | ------ |
| 1 | A | 90 | `grid/_anchor_edit.html:53` | `app/templates/grid/_anchor_edit.html:53` | template renders raw stored `current_anchor_balance` (edited field) | **CONFIRMED** -- `:53` `${{ "{:,.0f}".format(account.current_anchor_balance if account.current_anchor_balance else 0) }}` |
| 2 | A | 164 | `accounts.py:1284` | `app/routes/accounts.py:1284` | direct-read of `current_anchor_period_id` | **CONFIRMED** -- `:1284` `anchor_period_id = account.current_anchor_period_id or (` |
| 3 | Bp | 414 | `amortization_engine.py:981-984` | `app/services/amortization_engine.py:981-984` | fixed-rate engine walks confirmed schedule rows for real principal | **CONFIRMED** -- `:979 else:` then `:981-984` `for row in reversed(schedule): if row.is_confirmed: cur_balance = row.remaining_balance; break` |
| 4 | Bp | 541 | `credit_workflow.py:201` | `app/services/credit_workflow.py:201` | status-transition site sets `status_id` only; no `loan_params` touch | **CONFIRMED** -- `:201` `txn.status_id = credit_id` |
| 5 | Br | 914 | `loan.py:698` | `app/routes/loan.py:698` | `data["interest_rate"] = pct_to_decimal(data["interest_rate"])` in `add_rate_change` | **CONFIRMED** -- exact line match |
| 6 | Br | 1056 | `loan.py:1225` | `app/routes/loan.py:1225` | `create_payment_transfer` computes monthly P&I from stored mirror, no `rate_changes` | **CONFIRMED** -- `:1225-1227` `calculate_monthly_payment(Decimal(str(params.current_principal)), Decimal(str(params.interest_rate)), remaining)`, no RateHistory resolution |
| 7 | C | 1408 | `validation.py:1393-1395` | `app/schemas/validation.py:1393-1395` | `@pre_load strip_empty_strings` drops a blank `apy` | **CONFIRMED** -- `:1393-1395` `@pre_load` / `def strip_empty_strings(self, data, **kwargs):` / `return {k: v for k, v in data.items() if v != ""}` (the lines sit in `InterestParamsCreateSchema` `:1390`; 04's prose says `InterestParamsUpdateSchema` -- the create schema is the correct one for 04's "first save" `if not params:` argument, so the line cite is exact and the substance is strengthened, not a miscite of the sampled token) |
| 8 | C | 1485 | `investment.py:667` | `app/routes/investment.py:667` | `if inv_params and inv_params.annual_contribution_limit:` truthiness; 0 falsy -> `$500.00` literal | **CONFIRMED** -- `:667` exact; observed adjacent: the `Decimal("500.00")` literal 04 cites at `:670` is actually `:672` (2-line within-block offset on a *non-sampled* secondary cite; the sampled `:667` is exact) |
| 9 | D | 1701 | `savings.py:143` | `app/routes/savings.py:143` | `create_goal` `SavingsGoal(user_id=..., **data)` | **CONFIRMED** -- `:143` `goal = SavingsGoal(user_id=current_user.id, **data)` |
| 10 | D | 1822 | `app/utils/log_events.py:14-19` | `app/utils/log_events.py:14-19` | a docstring mention of `system.audit_log`, not a calc-input read | **CONFIRMED** -- `:14-19` are inside the module docstring (opens `:1`), prose describing DB-tier triggers; no code read of audit_log |

**Sample 1 result: 10/10 CONFIRMED.** Two prose-precision observations
recorded honestly (items 7, 8): both are about adjacent/secondary attribution,
not the sampled `path:line` tokens -- every one of the 10 sampled tokens
resolves exactly to the code 04 claims.

**Second sample (mandated -- "a too-clean result on a 2,000-line document
warrants a second look").** The method was re-examined: it is purely
positional, deterministic, and the resolution was genuinely cold (source files
opened at the cited lines without prior reading this session). Sample 2 draws
a different deterministic offset -- the median citation `ceil(N/2)` per family,
1 per family, no index overlap with sample 1:

| # | fam | 04 line | selected cite | resolved path | 04's claim (abbrev.) | result |
| - | --- | ------- | ------------- | ------------- | -------------------- | ------ |
| 11 | A | 124 | `grid.py:243` | `app/routes/grid.py:243` | grid is the only consumer of `stale_anchor_warning` | **CONFIRMED** -- `:243` `balances, stale_anchor_warning = balance_calculator.calculate_balances(` |
| 12 | Bp | 461 | `balance_calculator.py:232` | `app/services/balance_calculator.py:232` | direct-read of `original_principal` (fixed-rate ELSE branch) | **CONFIRMED** -- `:230 else:` then `:232` `loan_params.original_principal,  # Already Decimal from Numeric(12,2).` |
| 13 | Br | 985 | `app/routes/accounts.py:714` | `app/routes/accounts.py:714` | escrow components hard-deleted on account deletion | **CONFIRMED** -- `:714` `db.session.query(EscrowComponent).filter_by(account_id=account_id).delete()` |
| 14 | C | 1443 | `retirement_dashboard_service.py:321` | `app/services/retirement_dashboard_service.py:321` | `if params and params.assumed_annual_return:` truthiness | **CONFIRMED** -- exact line match |
| 15 | D | 1752 | `salary.py:1064-1176` | `app/routes/salary.py:1064-1176` | range = the `calibrate_preview`->`calibrate_confirm` two-step (only writer of `effective_*_rate`) | **CONFIRMED** -- range opens exactly at `:1064` `@salary_bp.route(".../calibrate", methods=["POST"])` / `:1067 def calibrate_preview` |

**Sample 2 result: 5/5 CONFIRMED exact.** **Combined spot-check: 15/15
selected `path:line` tokens resolve to what 04 claims**, with two recorded
prose-precision nits on non-sampled secondaries. 04's source citations are
reliable at the sampled rate. (This does not certify the 322 unsampled
distinct citations; it bounds the miscite rate as low on a content-blind
positional sample re-resolved cold.)

### Deliverable 2 - Phase-1 completeness reconciliation

Denominator: the **105** stored-numeric §1.5 columns extracted from
`01_inventory.md` (lines 270-794), reconciled against **verified reality**,
not the inventory as written. Two Phase-4-discovered Phase-1 corrections are
folded in: **Q-21 sub-q4** (the §1.5 `account.py` block records a CHECK for
`current_anchor_balance`/`anchor_balance` as "MIGRATION (not in model)" --
verified FALSE, no CHECK anywhere -- and **omits `current_anchor_period_id`
entirely**, so verified reality has **106** numeric columns, the +1 being
`current_anchor_period_id`, which Phase-4 Family A did cover); **Q-24** (the
F-044 `contribution_limit_remaining` miscite is in `02_concepts.md:2169-2200`
/ `03` F-044, **not** §1.5 -- 04:1939-1949 and Q-24 both record that
`01_inventory.md` §1.2/§1.5 are accurate here -- so Q-24 does **not** change
the §1.5 denominator).

Disposition buckets (every one of the 105 §1.5 rows + the Q-21 row is
accounted for):

| bucket | count | Phase-4 disposition |
| ------ | ----- | ------------------- |
| Monetary, Family A/B/C explicit per-column finding | 13 | covered (`current_anchor_balance`, `current_anchor_period_id`(Q-21 +1), `account_anchor_history.anchor_balance`; `loan_params.current_principal`/`original_principal`/`interest_rate`; `rate_history.interest_rate`; `escrow_components.annual_amount`; `interest_params.apy`; `investment_params.assumed_annual_return`/`annual_contribution_limit`/`employer_flat_percentage`/`employer_match_percentage`/`employer_match_cap_percentage`) -- count is 16 distinct; 13 stated to avoid recount, see consolidated table D3 for the authoritative per-column list |
| Monetary, Family B escrow secondary | 1 | `escrow_components.inflation_rate` -- covered (04:1307-1308, Family B rate/escrow) but **no standalone classification line** (minor nit, see below) |
| Monetary, Family D Escalation 1/2/3 | 7 | `transactions.actual_amount`; `savings_goals.contribution_per_period`; `calibration_overrides.effective_federal_rate`/`effective_state_rate`/`effective_ss_rate`/`effective_medicare_rate` (4); + `transactions.estimated_amount` is in the triage block | covered |
| Monetary, Family D triage table (04:1841-1883) | ~38 | covered, per-column grep-backed, AUTHORITATIVE |
| Monetary, **GAP** | **1** | **`auth.user_settings.estimated_retirement_tax_rate`** -- see finding F-046-SoT below |
| Non-monetary structural Integers (out of Phase-4 scope) | ~46 | `*.version_id`, `*.sort_order`, `PayPeriod.period_index`, `LoanParams.payment_day`/`term_months`/`arm_*_months`, `*.tax_year`(×4), `SalaryProfile.qualifying_children`/`other_dependents`/`pay_periods_per_year`, `PaycheckDeduction.deductions_per_year`/`inflation_effective_month`, `InvestmentParams.contribution_limit_year`, `PensionProfile.consecutive_high_years`, `RecurrenceRule.{day_of_month,due_day_of_month,interval_n,month_of_year,offset_periods}`, `SalaryRaise.effective_month`/`effective_year`, `User.failed_login_count`, `MfaConfig.last_totp_timestep`, `UserSettings.grid_default_periods`/`anchor_staleness_days` -- structural/config Integers with no money/rate computational counterpart; out of scope per audit-plan section 4 ("every stored numeric value that has, or should have, a computational counterpart") |

**Finding F-046-SoT (Phase-4 coverage GAP) -- `auth.user_settings.estimated_retirement_tax_rate`.**
`01_inventory.md:731` records it `Numeric(5,4)` nullable, `user.py:216` CHECK,
concept token `federal_tax (retirement projection input)`;
`02_concepts.md:2875` lists `UserSettings.estimated_retirement_tax_rate@user.py:242`
as a `federal_tax` producer input consumed by the retirement gap analysis;
`01_inventory.md:739` notes it is "one of the rate fields inspected by PA-02"
and is an input to financial calculations. It is a stored money-affecting rate
(it multiplies projected retirement income to a withholding figure). `grep -n
estimated_retirement_tax_rate 04_source_of_truth.md` -> **zero matches**: it
appears in **no** Family section, and the Family D triage table omits it (the
table lists 5 of the 6 `UserSettings` rate/threshold columns -- 04:1879-1883 --
but not this one). Consequently the triage table's closing completeness claim
**04:1885-1886** ("No stored-monetary column outside this list was found
during the per-column greps") is **inaccurate**: this column was missed.
Disposition: **Phase-4 did not classify it.** By the triage table's own rule
("AUTHORITATIVE unless a service computes+stores it") it is *probably*
AUTHORITATIVE (a user setting; no plausible service writes a rate back to it),
but the per-column `grep -rn '\.estimated_retirement_tax_rate\s*=' app/services/`
that backs every other triage row was not run/recorded for it, so the audit
does not assert the verdict. Recorded as **GAP**, classification **UNCLEAR
pending the confirmatory write-grep**; question **Q-26** raised in
`09_open_questions.md`; the 04:1885-1886 completeness claim is flagged
inaccurate (no edit to the Family D section, per the additive-only protocol).

**Minor nit (not a GAP).** `escrow_components.inflation_rate` is covered inside
Family B rate/escrow (04:1307-1308, within the `escrow_components` treatment
and the Phase-1 reconciliation that confirms the inventory's `loan_features.py`
block accurate) but is not given its own AUTHORITATIVE/CACHED/... line the way
its sibling `annual_amount` is. Provenance is unambiguous (user-entered
EscrowComponent input rate, no service computes+stores it -- same basis as the
explicitly-AUTHORITATIVE `annual_amount` and the explicitly-AUTHORITATIVE
`paycheck_deductions.inflation_rate` at 04:1856), so the implied class is
**AUTHORITATIVE**; recommend an explicit line in a later pass. Recorded here so
the reconciliation is honest; no separate question (provenance is not
ambiguous, unlike the GAP).

**Acceptance:** with F-046-SoT recorded and Q-26 raised, every stored-monetary
§1.5 column (plus the Q-21-omitted `current_anchor_period_id`) is either
covered by a Phase-4 family/escalation/triage row or explicitly GAP-listed; no
stored-monetary column is silently missed.

### Deliverable 3 - Consolidated classification table

The single artifact to scan first. Grep-able. `blk` = where the full
finding/basis lives. The ~38 AUTHORITATIVE Family-D triage columns are the
grep-able per-column table at **04:1841-1883** (not re-transcribed here -- that
would duplicate 40 rows the audit-plan section 12 anti-bloat rule forbids);
the count and the closing-claim correction (F-046-SoT) make that block
auditable.

| schema.table.column | type | class | one-line basis | blocking-Q |
| ------------------- | ---- | ----- | -------------- | ---------- |
| budget.accounts.current_anchor_balance | Numeric(12,2) | AUTHORITATIVE | user-entered seed, no computational counterpart; audit-mirror unenforced | -- |
| budget.accounts.current_anchor_period_id | Integer FK | **UNCLEAR** | NULL semantics undefined; 5 consumers, 4 behaviors on one row | **Q-20** |
| budget.account_anchor_history.anchor_balance | Numeric(12,2) | CACHED | append-only mirror of `current_anchor_balance`; sync invariant not enforced | -- (Q-21 sub-q1) |
| budget.loan_params.current_principal | Numeric(12,2) | **UNCLEAR** | dual role: ARM-authoritative-anchor vs fixed-cached; never updated on settle; displayed regardless of loan type | **Q-22** |
| budget.loan_params.original_principal | Numeric(12,2) | AUTHORITATIVE | origination input, immutable post-create, schema/DB CHECK aligned | -- |
| budget.loan_params.interest_rate | Numeric(7,5) | **UNCLEAR** | dual role: fixed-authoritative vs ARM denormalized RateHistory mirror; effective-date-unaware write | **Q-23** |
| budget.rate_history.interest_rate | Numeric(7,5) | AUTHORITATIVE | append-only event input; authority not honored by scalar display sites | -- |
| budget.escrow_components.annual_amount | Numeric(12,2) | AUTHORITATIVE | user input, immutable post-create, schema/DB CHECK aligned; consumer defects only | -- |
| budget.escrow_components.inflation_rate | Numeric(5,4) | AUTHORITATIVE | user-input EscrowComponent inflation rate; CHECK `inflation_rate IS NULL OR (0..1)` (`loan_features.py:111-115`); no second producer, no drift surface (LOW-04 D2-nit promoted to a standalone classification line) | -- |
| budget.interest_params.apy | Numeric(7,5) | AUTHORITATIVE | primary input, no counterpart; write-path silent-default 4.5% hazard | -- (Q-24 #2) |
| budget.investment_params.assumed_annual_return | Numeric(7,5) | AUTHORITATIVE | primary input; float Python default violation; 0-vs-None read hazard | -- (Q-24 #2, F-042) |
| budget.investment_params.annual_contribution_limit | Numeric(12,2) | AUTHORITATIVE | input; tri-consumer 0/None divergence | -- (Q-24 #3) |
| budget.investment_params.employer_flat_percentage | Numeric(5,4) | AUTHORITATIVE | primary; 0/None conflation benign | -- |
| budget.investment_params.employer_match_percentage | Numeric(5,4) | AUTHORITATIVE | primary; 0/None conflation benign | -- |
| budget.investment_params.employer_match_cap_percentage | Numeric(5,4) | AUTHORITATIVE | primary; 0/None conflation benign | -- |
| budget.transactions.actual_amount | Numeric(12,2) | AUTHORITATIVE | recorded input; ~43-site `effective_amount` bypass is the divergence surface | -- (F-027/F-028) |
| budget.transactions.estimated_amount | Numeric(12,2) | AUTHORITATIVE / DERIVED-by-invariant (shadows) | non-shadow raw input; shadow rows kept == `Transfer.amount` by transfer_service | -- (F-029/F-031) |
| budget.savings_goals.contribution_per_period | Numeric(12,2) | AUTHORITATIVE | pure user input; escalation hypothesis disproven by grep | -- (Q-25 #2) |
| salary.calibration_overrides.effective_federal_rate | Numeric(12,10) | **UNCLEAR** | frozen client-snapshot vs live-derived; never re-derived at confirm / on profile edit | **Q-25** |
| salary.calibration_overrides.effective_state_rate | Numeric(12,10) | **UNCLEAR** | same as effective_federal_rate | **Q-25** |
| salary.calibration_overrides.effective_ss_rate | Numeric(12,10) | **UNCLEAR** | same as effective_federal_rate | **Q-25** |
| salary.calibration_overrides.effective_medicare_rate | Numeric(12,10) | **UNCLEAR** | same as effective_federal_rate | **Q-25** |
| auth.user_settings.estimated_retirement_tax_rate | Numeric(5,4) | **GAP -> UNCLEAR** | Phase-4 coverage GAP (F-046-SoT); absent from 04; probably AUTHORITATIVE pending write-grep | **Q-26** |
| Family D triage block (~38 cols) | mixed Numeric | AUTHORITATIVE | per-column grep-backed; see grep-able table **04:1841-1883** | -- |

UNCLEAR/GAP count: **7 columns** block on **4 questions** -- Q-20, Q-22, Q-23,
Q-25 (the four `effective_*_rate` share Q-25) -- plus Q-26 for the GAP.

### Deliverable 4 - Drift-risk register (Phase-5 input; Phase 5 NOT performed here)

Each row links a developer symptom to the Phase-4 column finding(s), the
Phase-3 F-IDs, and the blocking question(s) that *together* explain it. Links
verified against the family sections and the Explore-extracted 03 mapping this
session, not asserted from the prompt.

| symptom | Phase-4 column finding(s) | Phase-3 F-IDs (verbatim verdict) | blocking Q | the explanation thread |
| ------- | ------------------------- | -------------------------------- | ---------- | ---------------------- |
| **#1** checking $160 grid vs $114.29 /savings | Family A `current_anchor_balance` AUTHORITATIVE + `current_anchor_period_id` UNCLEAR | F-002 checking_balance DIVERGE/SILENT_DRIFT (entries-load); F-003 projected_end_balance DIVERGE (SILENT_DRIFT checking + SCOPE_DRIFT anchor-None); F-001 account_balance DIVERGE | Q-16, **Q-20** | the entries-load expense divergence + the NULL-anchor-period SCOPE axis: Q-20 governs the SCOPE_DRIFT axis in F-001/F-003 (Q-20:860-862, verified) |
| **#2** mortgage payment 1911/1914/1912 -> 1910.95 | Family B `interest_rate` UNCLEAR + `current_principal` UNCLEAR | F-013 monthly_payment 16-site DIVERGE (rate-source axis); F-026 monthly_payment DIVERGE/SILENT_DRIFT+PLAN_DRIFT | **Q-22, Q-23**, Q-17 | rate authority split by surface (stored mirror vs RateHistory) compounded by the stored-principal that never updates; the verdict is not blocked, only the fix shape (Q-23:1117-1125, verified) |
| **#3** current_principal not updating on settle | Family B `current_principal` UNCLEAR (zero-writer settle trace 04:489-547) | F-014 loan_principal_real DIVERGE/SOURCE_DRIFT; F-015 loan_principal_stored DIVERGE/SOURCE_DRIFT; F-016 loan_principal_displayed UNKNOWN | **Q-22** | proven: no code path writes/recomputes `current_principal` on settle (settle modules do not import LoanParams); E-03 satisfied by neither route end-to-end |
| **#4** ARM payment drift in fixed window | Family B `current_principal` UNCLEAR + `interest_rate` UNCLEAR | F-026 monthly_payment DIVERGE/SILENT_DRIFT+PLAN_DRIFT vs E-02/W-048 | Q-17, **Q-22, Q-23** | rate-independent inside the window (no RateHistory rows in first 60 mo); the creep is the frozen `current_principal` re-amortized over a calendar-shrinking `remaining` -- the SAME un-maintained column as #3 (Q-22 sub-q3 / Q-23:1068-1082, verified) |
| **#5** /accounts matches nothing | Family A anchor + Family B `current_principal`/`interest_rate` UNCLEAR | F-001 account_balance DIVERGE/SOURCE_DRIFT; F-003 DIVERGE; F-008 debt_total UNKNOWN/SOURCE_DRIFT; F-015/F-016 (loan side) | Q-16, **Q-20**, Q-22 | unlabeled per-page base divergence (stored vs engine vs schedule) across the checking anchor and the loan principal -- E-04 violated |

**This register IS the Phase-5 input.** Phase 5 (symptom-driven hypothesis
trees + best-evidence root cause) is NOT performed in this session.

### Deliverable 5 - Open-questions consolidation

**Every UNCLEAR column has a both-interpretations 09 entry** (verified by
reading Q-20..Q-25 verbatim this session):

- `accounts.current_anchor_period_id` -> **Q-20** (Interpretation A "NULL = no
  usable balance yet" vs B "NULL = anchor at current period, balance as
  stored"; 09:851-858).
- `loan_params.current_principal` -> **Q-22** (Interpretation A AUTHORITATIVE
  / settle-update missing vs B CACHED / stale-mirror displayed; 09:951-962).
- `loan_params.interest_rate` -> **Q-23** (AUTHORITATIVE user-maintained vs
  CACHED RateHistory-mirror; 09:1087-1097).
- `calibration_overrides.effective_{federal,state,ss,medicare}_rate` -> **Q-25**
  (AUTHORITATIVE-snapshot vs DERIVED-stale; 09:1262-1266).
- `user_settings.estimated_retirement_tax_rate` -> **Q-26** (new this session;
  the GAP).

**Headline consolidation (confirmed from the family sections + Q-22/Q-23
text, not asserted).** Q-17, Q-22, Q-23, and Q-25 are **facets of ONE
structural decision**, not four independent choices. The shared fork is: *is a
stored column that mirrors/anchors a computation AUTHORITATIVE (so the bug is
the missing maintenance-on-event), or CACHED/DERIVED (so the bug is the display
reading the stale mirror instead of recomputing from the authority)?* The
audit files already assert the coupling for three of them: Q-22 ("the answers
must agree -- symptoms #2/#3/#4 are one un-maintained-stored-column family",
09:1118-1121; "symptom #4 is the SAME un-maintained column as symptom #3",
09:1001), Q-23 ("This is the same fork as Q-17 ... and Q-22 ...; the answers
should be consistent across all three", 09:1094-1097). Q-25's
`effective_*_rate` is the **same pattern in the salary/calibration domain**: a
stored column that should equal a derivation (`actual_x / base`) but is
written once from a client snapshot and never re-derived on the triggering
event (profile/deduction edit) -- structurally identical to `current_principal`
written once and never re-derived on settle. The developer should answer these
as one policy ("when does a stored mirror get maintained, and which side wins
on display"), not piecemeal.

**09 linkage was incomplete and is now cross-linked (questions NOT
duplicated).** Q-22/Q-23 already cross-link each other and Q-17. Q-25
(09:1309-1315) cross-links F-035/F-037/F-046/Q-13/Q-21 but **does not**
cross-link Q-17/Q-22/Q-23 -- so the headline coupling is not discoverable from
Q-25. A cross-link addendum is appended to Q-25 in `09_open_questions.md` (and
Q-26 added) this session; no question text is duplicated or rewritten.

### Deliverable 6 - Phase-4 acceptance gate

| # | criterion | evidence / verdict |
| - | --------- | ------------------ |
| a | 04 exists, non-empty; every mandatory column (Families A/B/C) has full schema + a classification | **PASS** -- 04 is 1949 lines pre-append; Family A (3 cols), B-principal (2), B-rate/escrow (3+inflation_rate), C (6) each carry schema + class (consolidated table D3). Section headers verified via grep this session. |
| b | every §1.5 stored-monetary column covered or GAP-listed | **PASS with 1 GAP** -- deliverable 2: 105 §1.5 cols + Q-21 `current_anchor_period_id`; all monetary covered except **`estimated_retirement_tax_rate`** (F-046-SoT, Q-26); structural Integers explicitly out-of-scope; the `escrow_components.inflation_rate` nit recorded. |
| c | every Phase-3 SOURCE_DRIFT finding (F-001/003/008/014/015/016/017) has a re-verification verdict | **PASS** -- roll-up below; 2 independently re-verified at source this session, not transcribed. |
| d | every UNCLEAR column has a both-interpretations 09 entry | **PASS** -- Q-20 (current_anchor_period_id), Q-22 (current_principal), Q-23 (interest_rate), Q-25 (effective_*_rate ×4); Q-26 for the GAP. Verified by reading Q-20..Q-25 verbatim. |
| e | deliverable-1 spot-check logged with its result | **PASS** -- 15/15 CONFIRMED (10 + mandated 5), method + tables above; 2 prose nits recorded plainly. |
| f | worked numeric examples exist for `current_principal` and `current_anchor_balance` | **PASS** -- `current_anchor_balance`: 04:92-119 ("new user registers -> ... six surfaces, four outputs", read this session). `current_principal`: 04:588-654 "### Worked numeric example (fixed-rate; 3 confirmed escrow-inclusive transfers)" yielding $200,000.00 / $199,399.70 / $198,495.20 (section header verified this session). |
| g | `git status` shows only docs/audits/financial_calculations/ files changed | **PASS** -- pasted below. |

**6(c) SOURCE_DRIFT re-verification roll-up** (per-family logs consolidated;
verdicts quoted from the family Phase-3 re-verification logs, classifications
from the Explore-extracted 03 mapping):

| F-ID | concept | 03 verdict/classification (verbatim) | Phase-4 re-verification |
| ---- | ------- | ------------------------------------ | ----------------------- |
| F-001 | account_balance | DIVERGE; SILENT_DRIFT + SCOPE_DRIFT + SOURCE_DRIFT (loan base stored/engine/schedule) | Family A log: `grid.py:238-241`, `accounts.py:1418-1421`, `savings_dashboard_service.py:325-328`, `dashboard_service.py:683-684`, `year_end_summary_service.py:2065-2066` all CONFIRMED |
| F-003 | projected_end_balance | DIVERGE; SILENT_DRIFT + SOURCE_DRIFT + SCOPE_DRIFT | Family A log: `grid.py:238-241`, `accounts.py:1418-1432`, `savings_dashboard_service.py:325-352`, `dashboard_service.py:683-705` CONFIRMED |
| F-008 | debt_total | UNKNOWN for canonical base; SOURCE_DRIFT + DEFINITION_DRIFT | Family B-principal log: `savings_dashboard_service.py:373` engine vs `:840` stored CONFIRMED. **6(c) independent reopen this session:** `:373` `current_bal = proj.current_balance` (comment "for fixed-rate it is derived from the schedule") vs `:840` `principal = Decimal(str(lp.current_principal))` -- same service, same loan, two principals. **SOURCE_DRIFT verdict re-verified at source.** |
| F-014 | loan_principal_real | DIVERGE; SOURCE_DRIFT + SCOPE_DRIFT + SILENT_DRIFT | Family B-principal log: `amortization_engine.py:977-984`, `loan.py:553-557`, `debt_strategy.py:147-197` CONFIRMED; settle grep strengthened (settle modules do not import LoanParams) |
| F-015 | loan_principal_stored | DIVERGE; SOURCE_DRIFT (stored mirror vs engine-walked, fixed-rate) | Family B-principal log: `loan_params.py:54`, CHECK `:31-34`, only-writer `update_params` `loan.py:672-674` CONFIRMED |
| F-016 | loan_principal_displayed | UNKNOWN (primary path undesignated); SOURCE_DRIFT + SCOPE_DRIFT conditional | Family B-principal log: `loan/dashboard.html:104` STORED, `loan.py:1087` engine-real prefill, `debt_strategy.py:139` third value CONFIRMED. **6(c) independent reopen this session:** `loan.py:553-557` `return render_template("loan/dashboard.html", ..., params=params, ...)` -- the route passes the stored-`current_principal`-bearing `params` to the card; engine `proj` not wired to it. **SOURCE_DRIFT (stored displayed regardless of loan type) re-verified at source.** |
| F-017 | principal_paid_per_period | DIVERGE; SCOPE_DRIFT + SILENT_DRIFT | Family B logs: `amortization_engine.py:517/531/566/602`, `balance_calculator.py:226/232`, `year_end_summary_service.py:860/865/868/871` CONFIRMED |

All seven SOURCE_DRIFT findings carry a Phase-4 re-verification verdict; the
two independently reopened this session (F-008, F-016) confirm the roll-up is
verified, not transcribed.

**6(g) `git status` (run this session):**

```text
 M docs/audits/financial_calculations/03_consistency.md
 M docs/audits/financial_calculations/09_open_questions.md
?? docs/audits/financial_calculations/04_source_of_truth.md
```

Only files under `docs/audits/financial_calculations/` are changed -- no
source, test, migration, template, or JS file touched (audit-plan section 11
criterion 7). **Note on the `03_consistency.md` `M`:** it carried a
pre-existing modification (`git diff --stat`: 2110 lines, +1713/-905) from
**before** the Phase-4 sessions began -- Phase 3 was committed at `da3d108`
("P3-reconcile gate -- Phase 3 complete") and this uncommitted delta sits on
top of that. **Phase 4 (this session, P4-e) did not author it**; the
trust-but-verify protocol left it untouched (the session is strictly additive
to 04 and 09). Surfaced here so the developer can decide whether that external
`M` is expected; the audit does not silently absorb or revert it (hard rule:
look at what contradicts the description rather than proceed). `09` `M` is the
accumulated Q-20..Q-25 from prior Phase-4 sessions (P4-a..P4-d) plus this
session's Q-26 + Q-25 cross-link addendum; `04` `??` is the Phase-4 deliverable
file this session appends to.

## Phase 4 complete

Phase 4 (source-of-truth and drift audit) is complete. Acceptance gate: PASS
with one recorded coverage GAP (F-046-SoT / Q-26) and one minor classification
nit (`escrow_components.inflation_rate`), neither blocking a developer
decision. 7 columns are UNCLEAR/GAP, blocking on Q-20, Q-22, Q-23, Q-25
(consolidated as ONE structural "stored-mirror maintenance policy" decision),
and Q-26.

**What Phase 5 inherits:**

1. **The drift-risk register (deliverable 4)** -- the symptom -> column-finding
   -> F-ID -> blocking-Q threads for symptoms #1-#5, already verified against
   the family sections. Phase 5 builds hypothesis trees on top of it; it does
   not re-derive the mapping.
2. **The blocking questions** -- Q-20 (current_anchor_period_id NULL
   semantics, symptoms #1/#5), Q-22 (current_principal role, symptoms
   #2/#3/#4/#5), Q-23 (interest_rate role, symptoms #2/#4), Q-25
   (effective_*_rate role, calibrated-paycheck projections), Q-26 (the
   estimated_retirement_tax_rate GAP). Per the headline consolidation,
   Q-17/Q-22/Q-23/Q-25 should be answered as one stored-mirror-maintenance
   policy; symptoms #2/#3/#4 collapse onto the single un-maintained
   `current_principal` column.
3. **The consolidated classification table (deliverable 3)** -- the
   single-scan artifact pairing every column with its class and blocking-Q.

Phase 5 is NOT begun in this session.

## Phase 4 - P4-f gap closure

Session P4-f. Strictly additive: nothing above this heading was edited,
trimmed, or rewritten (Family A/B/C/D and the P4-e section are untouched).
Scope is exactly two deliverables: close the F-046-SoT GAP with a full
per-column finding, and rebuild the completeness denominator from model
source so the completeness claim no longer rests on the twice-failed
§1.5 inventory (Q-21 `current_anchor_period_id`, P4-e F-046). Every factual
claim below was produced by a `grep`/`glob`/full-file read run this session;
no conclusion was inherited from §1.5, the planning inventory, or P4-d's
triage list.

### Deliverable 1 -- Finding F-046-SoT (closed): `auth.user_settings.estimated_retirement_tax_rate`

- **Column:** `auth.user_settings.estimated_retirement_tax_rate`
  `Numeric(5,4)` nullable. Definition `app/models/user.py:242`
  (`db.Column(db.Numeric(5, 4), nullable=True)`, no server default).
  DB CHECK `ck_user_settings_valid_estimated_tax_rate`
  `app/models/user.py:216-221`
  (`estimated_retirement_tax_rate IS NULL OR (>= 0 AND <= 1)`).
  Model-vs-migration: the add migration
  `migrations/versions/c3d4e5f6g7h8_add_investment_retirement_tables.py:171-179`
  declares `sa.Numeric(5, 4), nullable=True` (no server default); the
  CHECK was added by the C-24 sweep
  `migrations/versions/b71c4a8f5d3e_c24_marshmallow_range_check_sweep.py:224-237`.
  Model type and CHECK match both migrations exactly -- **no schema drift**.

- **Represents:** the user's estimated effective income-tax rate during
  retirement, stored as a fractional decimal in `[0, 1]` (a planning
  assumption multiplied against projected traditional/pension retirement
  income to produce an after-tax view). The model comment
  `app/models/user.py:215-216` states "Estimated effective tax rate during
  retirement (NULL = unset, fall back to current bracket-based estimate)".
  Entered on `app/templates/settings/_retirement.html:27-29` as a percent;
  the route divides by 100 before persistence.

- **Computational counterpart:** **NONE exists in code.** The model comment
  (`user.py:215-216`) claims a "fall back to current bracket-based estimate"
  when NULL. Full read of the only consuming chain disproves the claim:
  `retirement_dashboard_service.compute_gap_data`
  (`app/services/retirement_dashboard_service.py:79-254`, the
  `estimated_retirement_tax_rate` read is `:222-226`) computes
  `tax_rate = Decimal(str(settings.estimated_retirement_tax_rate)) if
  settings and settings.estimated_retirement_tax_rate else None` and passes
  it as `estimated_tax_rate` to
  `retirement_gap_calculator.calculate_gap`
  (`app/services/retirement_gap_calculator.py:37-136`). In `calculate_gap`,
  `estimated_tax_rate=None` (`:43`) means the guarded blocks at `:76-80`
  (after-tax pension) and `:108-122` (after-tax projected savings) are
  **skipped entirely**: `after_tax_monthly_pension=None` (`:75`),
  `after_tax_projected=None` (`:108`), and `effective_pension` falls back
  to the **gross** `monthly_pension_income` (`:85`). There is no
  bracket-based estimate anywhere in the function. NULL therefore yields
  an **untaxed/gross** retirement projection, not a computed estimate. The
  model comment documents a fallback the code does not implement -- a
  DEFINITION discrepancy (documentation vs behavior), surfaced for the
  developer in Q-26, not adjudicated here (hard rule 5).

- **Update paths:** exactly one, user-driven.
  `app/routes/retirement.py:338-410` `update_settings()`
  (`@retirement_bp.route("/retirement/settings", POST)`):
  raw form captured `:344`; percent->decimal `form_data[field] =
  str(Decimal(form_data[field]) / Decimal("100"))` for
  `("safe_withdrawal_rate", "estimated_retirement_tax_rate")` `:348-359`
  (narrow `InvalidOperation` catch `:352-359`, C-46/F-145); validate via
  `_settings_schema` (= `RetirementSettingsSchema()`,
  `app/routes/retirement.py:43`, import `:23-26`) `:361`; on error
  re-render 422 `:362-386`; `load` `:388`; `setattr(settings, field_name,
  value)` for `field_name in _SETTINGS_FIELDS` (set includes
  `"estimated_retirement_tax_rate"` `:401`) `:403-405`;
  `db.session.commit()` `:407`. **Confirmatory write-grep**
  `grep -rn 'estimated_retirement_tax_rate\s*=' app/services/` ->
  **zero matches (exit 1)**: no service computes-and-stores this column.
  This is the same grep that backs every Family D triage row; it was the
  one never run for this column in P4-e (the F-046-SoT GAP cause). It is
  run and recorded now.

- **Direct-read paths:**
  1. `app/services/retirement_dashboard_service.py:222-226`
     (`compute_gap_data`) -- the **only computational consumer**. Reads
     `settings.estimated_retirement_tax_rate` under a **truthiness** guard
     `if settings and settings.estimated_retirement_tax_rate`, then passes
     `Decimal(str(...))` or `None` to `calculate_gap(..., estimated_tax_rate=)`.
  2. `app/templates/settings/_retirement.html:29` -- **display only**:
     `("%.1f"|format(settings.estimated_retirement_tax_rate|float * 100) ...)`
     (decimal->percent for the form field; Jinja arithmetic + `|float`
     cast, a templates-layer/Decimal-contract concern owned by Phase 6 /
     the F-042 family, cross-link only).
  No other read exists (full-repo
  `grep -rn estimated_retirement_tax_rate app/ scripts/ migrations/`:
  `user.py` def/CHECK, `retirement.py:348,401`, `validation.py:1752`,
  `retirement_dashboard_service.py:223-224`, `_retirement.html:27-31`,
  the two migrations -- complete surface).

- **Drift risk:** This is **not** a stored-vs-computed staleness surface
  (the Phase-4 concern): no service writer, no cache, nothing derives INTO
  the column. Two real defects observed while reading, neither a Phase-4
  staleness, both routed to Q-26 / cross-refs (hard rule 4 -- recorded, not
  ignored, not fixed):
  - **(a) Documentation-vs-code fallback discrepancy.** `user.py:215-216`
    promises a bracket-based estimate when NULL; `calculate_gap` applies
    **no tax** when NULL (gross figures). A user who leaves the field unset
    sees an over-optimistic (untaxed) retirement projection with no error,
    while the model comment asserts the opposite behavior.
  - **(b) 0-vs-None truthiness conflation**
    (`retirement_dashboard_service.py:224`). The CHECK admits `>= 0`, so an
    explicit `0.0000` ("I expect 0% retirement tax" -- realistic for
    Roth-only withdrawals) is a valid stored value distinct from NULL
    ("unset", per the model comment). The guard
    `if settings and settings.estimated_retirement_tax_rate` treats
    `Decimal("0.0000")` as falsy and coerces it to `None`. The gap *math*
    coincidentally agrees (0% tax and "no tax" yield the same
    `monthly_income_gap`), but the `RetirementGapAnalysis` after-tax fields
    (`after_tax_monthly_pension`, `after_tax_projected_savings`,
    `after_tax_surplus_or_shortfall`,
    `retirement_gap_calculator.py:26,33-34,124-136`) stay `None` instead of
    the explicit-0% numeric values, suppressing the after-tax view for a
    user who deliberately entered 0. Violates coding-standards.md "Do not
    rely on truthiness for business logic ... `0` and `None` mean different
    things". Same class as `Transaction.effective_amount`'s deliberately
    correct `is not None` (`transaction.py:242-245`).

- **Stale-detection:** N/A -- no stored/computed pair, no staleness flag,
  none needed.

- **Phase-3 cross-ref:** F-042 (other `UserSettings` rate read-path
  0-vs-None hazards: `safe_withdrawal_rate`, `trend_alert_threshold`;
  04:2111 / triage 04:1882-1883), PA-04 (`safe_withdrawal_rate`
  float-cast, `00_priors.md:667`), F-027/F-028 (the `effective_amount`
  truthiness-bypass family). The defect (b) here is the *same shape* as
  F-042's but on a different column; recorded, not re-litigated.

- **PA-02 status (does the prior-audit concern still hold here?):** **No,
  not for this column in current source.** PA-02 (`00_priors.md:665`,
  findings.md F-014) is the Marshmallow `Range(min=0, max=100)`
  (percentage) vs DB CHECK `0..1` (decimal) mismatch on "inflation_rate,
  FICA tax rates, state tax rates". Current source for THIS column:
  `RetirementSettingsSchema.estimated_retirement_tax_rate`
  (`app/schemas/validation.py:1752-1755`) validates
  `Range(min=0, max=1)` -- the **decimal** range, not the percent range;
  the route normalizes percent->decimal via `/100` **before** validation
  (`retirement.py:348-351`); the DB CHECK is `0..1`
  (`user.py:217-219`, migration `b71c4a8f5d3e:224-237`). All three layers
  are consistent. The percent/decimal hazard PA-02 describes is **absent
  here**, remediated by C-24/F-077 (model comment `user.py:214` "F-077 /
  C-24"; commit CM-02 `42720ca`, `00_priors.md:714`). Note also: PA-02 as
  recorded at `00_priors.md:665` does **not** enumerate this column;
  `01_inventory.md:739`'s "one of the rate fields inspected by PA-02" is an
  over-broad linkage and is moot regardless, since the hazard is not
  present in current source. The still-live PA-01/PA-02-class surfaces are
  the *other* schema -- `UserSettingsSchema.default_inflation_rate`
  (`validation.py:1779`, `Range(0,100)`) and `trend_alert_threshold`
  (`validation.py:1788`, `Range(1,100)`) -- which are out of P4-f scope.

- **Classification: AUTHORITATIVE.** As a stored column it is a pure
  user-entered planning input: a single user-driven write path
  (`retirement.py:update_settings`), zero service writers (write-grep exit
  1), nothing derives into it, no cache, no staleness surface. The two
  interpretations Q-26 posed are settled mechanically by the code, not by
  auditor preference: Interpretation A (AUTHORITATIVE) holds; Interpretation
  B (CACHED/DERIVED) is disproven -- no path recomputes or stores it.
  **GAP F-046-SoT is CLOSED with verdict AUTHORITATIVE.** Defects (a) and
  (b) are Phase-3/5/6 read-path/documentation findings, not a Phase-4
  source-of-truth staleness; they do not change the stored column's class.

- **Open questions:** the source-of-truth-role sub-question of Q-26 is
  **RESOLVED (AUTHORITATIVE)**. One new sharpened sub-question is added to
  Q-26 (the developer adjudicates, hard rule 5): does an *unset* (NULL)
  `estimated_retirement_tax_rate` mean "apply no tax / show gross
  retirement figures" (what `retirement_gap_calculator.calculate_gap:76,108`
  actually does) or "fall back to a current bracket-based estimate" (what
  the model comment `app/models/user.py:215-216` documents)? The two
  diverge silently in every retirement projection of a user who has not
  set the field. Plus the 0-vs-None conflation at
  `retirement_dashboard_service.py:224` (defect (b)) is flagged for
  Phase-3/Phase-6 alongside the F-042 family.

### Deliverable 2 -- Completeness denominator rebuilt from model source

`glob app/models/*.py` -> 25 files; all 23 concrete-model files read in
full this session (`__init__.py` is an import registry, `mixins.py` carries
only timestamp/soft-delete columns -- no money/rate). The census below was
built **only** from that model source plus a migration backstop. It was
**not** seeded from §1.5, the planning inventory, or P4-d's triage list
(the artifacts that failed twice). Comparison to §1.5 is done only *after*,
to explain discrepancies.

Inclusion rule (audit-plan section 4): a stored numeric column representing
money, a balance, a rate, a limit, a count with a money/rate computational
counterpart, or a source-of-truth pointer. Excluded: pure relational FKs,
booleans, timestamps, dates, strings, PKs, and structural/config Integers
with no money/rate counterpart (`version_id` optimistic-lock counters,
`sort_order`, `period_index`, term/day/month/year/dependent counters) --
enumerated in the out-of-scope appendix so the denominator is auditable,
matching the audit-plan's "has, or should have, a computational
counterpart" scope.

Coverage column maps each census row to where 04 already classifies it:
**A** = Family A / consolidated 04:2101-2103; **B** = Family B /
consolidated 04:2104-2109; **C** = Family C / consolidated 04:2110-2115;
**D-tri:NNNN** = Family D triage table row at 04:NNNN; **D-esc:NNNN** =
consolidated/Escalation row at 04:NNNN (not in the triage table);
**F-046** = closed by Deliverable 1 above; **NONE** = uncovered.

#### Canonical census -- in-scope stored numeric columns (model-derived)

| schema.table.column | type | nullable | CHECK | 04 coverage |
| --- | --- | --- | --- | --- |
| budget.accounts.current_anchor_balance | Numeric(12,2) | yes | none | A (04:2101) |
| budget.accounts.current_anchor_period_id | Integer FK->pay_periods | yes (SET NULL) | none | A (04:2102, UNCLEAR Q-20) |
| budget.account_anchor_history.anchor_balance | Numeric(12,2) | no | none | A (04:2103, CACHED) |
| budget.loan_params.original_principal | Numeric(12,2) | no | `> 0` (user.py loan_params:27-30) | B (04:2105) |
| budget.loan_params.current_principal | Numeric(12,2) | no | `>= 0` (loan_params.py:31-34) | B (04:2104, UNCLEAR Q-22) |
| budget.loan_params.interest_rate | Numeric(7,5) | no | `>= 0` (loan_params.py:35-38) | B (04:2106, UNCLEAR Q-23) |
| budget.rate_history.interest_rate | Numeric(7,5) | no | `>=0 AND <=1` (loan_features.py:44-47) | B (04:2107) |
| budget.escrow_components.annual_amount | Numeric(12,2) | no | `>= 0` (loan_features.py:103-106) | B (04:2108) |
| budget.escrow_components.inflation_rate | Numeric(5,4) | yes | NULL OR `0..1` (loan_features.py:111-115) | B (04:2109, D2 nit) |
| budget.interest_params.apy | Numeric(7,5) | no | `>=0 AND <=1` (interest_params.py:33-36) | C (04:2110) |
| budget.investment_params.assumed_annual_return | Numeric(7,5) | no | `-1..1` (investment_params.py:21-24) | C (04:2111) |
| budget.investment_params.annual_contribution_limit | Numeric(12,2) | yes | NULL OR `>= 0` (investment_params.py:31-35) | C (04:2112) |
| budget.investment_params.employer_flat_percentage | Numeric(5,4) | yes | NULL OR `0..1` (investment_params.py:40-45) | C (04:2113) |
| budget.investment_params.employer_match_percentage | Numeric(5,4) | yes | NULL OR `0..10` (investment_params.py:53-58) | C (04:2114) |
| budget.investment_params.employer_match_cap_percentage | Numeric(5,4) | yes | NULL OR `0..1` (investment_params.py:64-69) | C (04:2115) |
| budget.transactions.estimated_amount | Numeric(12,2) | no | `>= 0` (transaction.py:112-115) | D-tri:1843 / 04:2117 |
| budget.transactions.actual_amount | Numeric(12,2) | yes | NULL OR `>= 0` (transaction.py:116-119) | D-esc:2116 (F-027/F-028) |
| budget.transaction_entries.amount | Numeric(12,2) | no | `> 0` (transaction_entry.py:51-54) | D-tri:1844 |
| budget.transfers.amount | Numeric(12,2) | no | `> 0` (transfer.py:37) | D-tri:1845 |
| budget.transaction_templates.default_amount | Numeric(12,2) | no | `>= 0` (transaction_template.py:30) | D-tri:1846 |
| budget.transfer_templates.default_amount | Numeric(12,2) | no | `> 0` (transfer_template.py:31-34) | D-tri:1847 |
| budget.savings_goals.target_amount | Numeric(12,2) | yes | `> 0` (savings_goal.py:41-44) | D-tri:1848 |
| budget.savings_goals.contribution_per_period | Numeric(12,2) | yes | NULL OR `> 0` (savings_goal.py:45-48) | D-esc:2118 (Q-25 #2) |
| budget.savings_goals.income_multiplier | Numeric(8,2) | yes | NULL OR `> 0` (savings_goal.py:49-52) | D-tri:1849 |
| salary.salary_profiles.annual_salary | Numeric(12,2) | no | `> 0` (salary_profile.py:28) | D-tri:1850 |
| salary.salary_profiles.additional_income | Numeric(12,2) | no | `>= 0` (salary_profile.py:32) | D-tri:1851 |
| salary.salary_profiles.additional_deductions | Numeric(12,2) | no | `>= 0` (salary_profile.py:33) | D-tri:1852 |
| salary.salary_profiles.extra_withholding | Numeric(12,2) | no | `>= 0` (salary_profile.py:34) | D-tri:1853 |
| salary.salary_raises.percentage | Numeric(5,4) | yes | NULL OR `> 0` (salary_raise.py:66) | D-tri:1857 |
| salary.salary_raises.flat_amount | Numeric(12,2) | yes | NULL OR `> 0` (salary_raise.py:67) | D-tri:1858 |
| salary.paycheck_deductions.amount | Numeric(12,4) | no | `> 0` (paycheck_deduction.py:40) | D-tri:1854 |
| salary.paycheck_deductions.annual_cap | Numeric(12,2) | yes | NULL OR `> 0` (paycheck_deduction.py:42-45) | D-tri:1855 |
| salary.paycheck_deductions.inflation_rate | Numeric(5,4) | yes | NULL OR `0..1` (paycheck_deduction.py:51-55) | D-tri:1856 |
| salary.pension_profiles.benefit_multiplier | Numeric(7,5) | no | `> 0` (pension_profile.py:32-35) | D-tri:1865 |
| salary.calibration_overrides.actual_gross_pay | Numeric(10,2) | no | `> 0` (calibration_override.py:27-30) | D-tri:1859 |
| salary.calibration_overrides.actual_federal_tax | Numeric(10,2) | no | `>= 0` (calibration_override.py:31-34) | D-tri:1860 |
| salary.calibration_overrides.actual_state_tax | Numeric(10,2) | no | `>= 0` (calibration_override.py:35-38) | D-tri:1861 |
| salary.calibration_overrides.actual_social_security | Numeric(10,2) | no | `>= 0` (calibration_override.py:39-42) | D-tri:1862 |
| salary.calibration_overrides.actual_medicare | Numeric(10,2) | no | `>= 0` (calibration_override.py:43-46) | D-tri:1863 |
| salary.calibration_overrides.effective_federal_rate | Numeric(12,10) | no | `0..1` (calibration_override.py:53-56) | D-esc:2119 (UNCLEAR Q-25) |
| salary.calibration_overrides.effective_state_rate | Numeric(12,10) | no | `0..1` (calibration_override.py:57-60) | D-esc:2120 (Q-25) |
| salary.calibration_overrides.effective_ss_rate | Numeric(12,10) | no | `0..1` (calibration_override.py:61-64) | D-esc:2121 (Q-25) |
| salary.calibration_overrides.effective_medicare_rate | Numeric(12,10) | no | `0..1` (calibration_override.py:65-68) | D-esc:2122 (Q-25) |
| salary.calibration_deduction_overrides.actual_amount | Numeric(10,2) | no | `>= 0` (calibration_override.py:135-138) | D-tri:1864 |
| salary.tax_bracket_sets.standard_deduction | Numeric(12,2) | no | `>= 0` (tax_config.py:21) | D-tri:1866 |
| salary.tax_bracket_sets.child_credit_amount | Numeric(12,2) | no | `>= 0` (tax_config.py:22) | D-tri:1867 |
| salary.tax_bracket_sets.other_dependent_credit_amount | Numeric(12,2) | no | `>= 0` (tax_config.py:23) | D-tri:1868 |
| salary.tax_brackets.min_income | Numeric(12,2) | no | `>= 0` (tax_config.py:86) | D-tri:1869 |
| salary.tax_brackets.max_income | Numeric(12,2) | yes | NULL OR `>= min_income` (tax_config.py:87-90) | D-tri:1870 |
| salary.tax_brackets.rate | Numeric(5,4) | no | `0..1` (tax_config.py:91) | D-tri:1871 |
| salary.state_tax_configs.flat_rate | Numeric(5,4) | yes | NULL OR `0..1` (tax_config.py:136-139) | D-tri:1872 |
| salary.state_tax_configs.standard_deduction | Numeric(12,2) | yes | NULL OR `>= 0` (tax_config.py:143-146) | D-tri:1873 |
| salary.fica_configs.ss_rate | Numeric(5,4) | no | `0..1` (tax_config.py:194) | D-tri:1874 |
| salary.fica_configs.ss_wage_base | Numeric(12,2) | no | `> 0` (tax_config.py:195) | D-tri:1875 |
| salary.fica_configs.medicare_rate | Numeric(5,4) | no | `0..1` (tax_config.py:196) | D-tri:1876 |
| salary.fica_configs.medicare_surtax_rate | Numeric(5,4) | no | `0..1` (tax_config.py:197-200) | D-tri:1877 |
| salary.fica_configs.medicare_surtax_threshold | Numeric(12,2) | no | `> 0` (tax_config.py:201) | D-tri:1878 |
| auth.user_settings.default_inflation_rate | Numeric(5,4) | yes | `0..1` (user.py:187-188) | D-tri:1879 |
| auth.user_settings.low_balance_threshold | Integer (money) | yes | `>= 0` (user.py:192) | D-tri:1880 |
| auth.user_settings.large_transaction_threshold | Integer (money) | no | `>= 0` (user.py:193-196) | D-tri:1881 |
| auth.user_settings.safe_withdrawal_rate | Numeric(5,4) | yes | NULL OR `0..1` (user.py:207-211) | D-tri:1882 |
| auth.user_settings.trend_alert_threshold | Numeric(5,4) | no | `0..1` (user.py:197-200) | D-tri:1883 |
| auth.user_settings.estimated_retirement_tax_rate | Numeric(5,4) | yes | NULL OR `0..1` (user.py:216-221) | **F-046 (CLOSED by Deliverable 1: AUTHORITATIVE)** |

**62 in-scope columns. Coverage column has zero NONE rows** -- the only
previously-uncovered column (`estimated_retirement_tax_rate`) is closed by
Deliverable 1.

#### Out-of-scope structural Integers (model-derived, audit-plan section 4)

Independently enumerated so the denominator's exclusions are auditable
(matches P4-e's "~46 non-monetary structural Integers" bucket, 04:2047, by
the audit-plan's "has/should have a computational counterpart" rule, not by
trusting it): `auth.user_settings.grid_default_periods`,
`auth.user_settings.anchor_staleness_days`, `auth.users.failed_login_count`,
`auth.mfa_configs.last_totp_timestep`, `budget.loan_params.term_months`,
`budget.loan_params.payment_day`,
`budget.loan_params.arm_first_adjustment_months`,
`budget.loan_params.arm_adjustment_interval_months`,
`budget.investment_params.contribution_limit_year`,
`budget.recurrence_rules.{interval_n,offset_periods,day_of_month,due_day_of_month,month_of_year}`,
`budget.pay_periods.period_index`,
`salary.salary_profiles.{pay_periods_per_year,qualifying_children,other_dependents}`,
`salary.salary_raises.{effective_month,effective_year}`,
`salary.paycheck_deductions.{deductions_per_year,inflation_effective_month}`,
`salary.pension_profiles.consecutive_high_years`,
`salary.tax_bracket_sets.tax_year`, `salary.state_tax_configs.tax_year`,
`salary.fica_configs.tax_year`, `ref.account_types.max_term_months`, plus
`version_id` (9 tables) and `sort_order` (5 tables). None has a money/rate
computational counterpart; all correctly out of Phase-4 scope.

#### Census-vs-04 set-difference

`census in-scope columns` MINUS `columns covered by a 04 family/section` =
**{ `auth.user_settings.estimated_retirement_tax_rate` }** -- the single
known GAP, **closed by Deliverable 1 above (verdict AUTHORITATIVE)**.

**Zero NEW gaps.** (Well under the "stop and escalate if > ~3 new gaps"
threshold -- no developer re-sweep decision required.) Coverage verified
mechanically, not asserted: for each non-triage-table column,
`grep -c <col> docs/audits/financial_calculations/04_source_of_truth.md`
returns substantive hits in a Family/consolidated section
(`current_anchor_balance` 30, `current_anchor_period_id` 17,
`original_principal` 20, `interest_rate` 65, `apy` 28,
`assumed_annual_return` 12, `employer_match_cap_percentage` 4,
`actual_amount` 19, `estimated_amount` 16, `contribution_per_period` 9,
`effective_federal_rate` 8, `effective_medicare_rate` 4); the triage-table
columns were confirmed by direct read of 04:1843-1883.
`estimated_retirement_tax_rate` returns 9 hits, **all** in F-046-SoT /
consolidated-GAP / Q-26 context (no Family classification line) -- which is
exactly why it was the GAP and why Deliverable 1 was needed.

Migration backstop (catch a DB column with no model, or a model column
whose migration type differs):
- `comm -23` of {tables in any migration `op.create_table`/`op.add_column`}
  vs {model `__tablename__`} = **empty** -- every migrated table has a
  model; no DB-only table.
- `grep -B2 'sa.Numeric('` over `migrations/versions/*.py` surfaced no
  column name absent from the model census (`effective_date`/`name` are
  Date/String false positives of the window).
- Targeted model-vs-migration check on the F-046 column: model
  `user.py:242` `Numeric(5,4) nullable` == add migration
  `c3d4e5f6g7h8:171-179`; model CHECK `user.py:216-221` == C-24 migration
  `b71c4a8f5d3e:224-237`. No drift.
The model layer is the project's canonical schema source (the
`db.create_all()` test path and the Alembic chain are kept convergent;
see e.g. `interest_params.py:43-51`, `scenario.py:18-26`); the backstop
confirms no DB-side column escapes the model-derived denominator.

#### Census spot-check (trust-but-verify; 3 columns re-read at source)

1. `salary.fica_configs.ss_wage_base` -- reopened `app/models/tax_config.py`:
   `:221-224` `db.Numeric(12, 2), nullable=False, server_default=db.text("176100")`;
   CHECK `:195` `ss_wage_base > 0`. Census row says
   `Numeric(12,2) | no | > 0`. **Match.**
2. `budget.savings_goals.income_multiplier` -- reopened
   `app/models/savings_goal.py`: `:115-118`
   `db.Numeric(8, 2), nullable=True`; CHECK `:49-52`
   `income_multiplier IS NULL OR income_multiplier > 0`. Census row says
   `Numeric(8,2) | yes | NULL OR > 0`. **Match.**
3. `budget.loan_params.current_principal` -- reopened
   `app/models/loan_params.py`: `:54`
   `db.Numeric(12, 2), nullable=False`; CHECK `:31-34`
   `current_principal >= 0`. Census row says
   `Numeric(12,2) | no | >= 0`. **Match.**
All three verified at source; the census is verified, not asserted.

#### §1.5 discrepancy explanation (compared only after the census was built)

The independent census matches the P4-e *union* of covered columns;
the integer partition differs (P4-e: "~38 triage" + "7 escalation";
P4-f: 41 triage rows read at 04:1843-1883 + 6 escalation-only rows) only
because the triage/escalation boundary was drawn differently and P4-e
hedged ("13 stated to avoid recount", 04:2042). The load-bearing result is
identical: the set-difference is exactly the one F-046 column. §1.5's
known omissions (Q-21 `current_anchor_period_id`; the
`estimated_retirement_tax_rate` row §1.5 *did* list at 01:731 but no Family
classified) did not produce any additional uncovered column once the
denominator is rebuilt from `app/models/*.py`.

### Supersession note for 04:1885-1886 (additive; the prior section is unedited)

The Family-D triage-table closing claim at **04:1885-1886** ("No
stored-monetary column outside this list was found during the per-column
greps...") is **superseded as the completeness basis** by this P4-f
model-derived census. That sentence was already known inaccurate (it
omitted `estimated_retirement_tax_rate` -- the F-046-SoT GAP / Q-26); per
the additive-only Phase-4 protocol the sentence is **not edited**. A reader
of 04 who reaches 04:1885-1886 should treat the authoritative completeness
statement as: *the triage table 04:1841-1883 + the consolidated Families
A/B/C and Escalation rows 04:2101-2122 + this P4-f finding for
`auth.user_settings.estimated_retirement_tax_rate` together cover 100% of
the independently model-derived in-scope stored-numeric columns (62
columns); zero NONE remain.* This supersession pointer is the protocol-
compliant correction (no edit to the Family D section, consistent with the
Q-21 sub-q4 / Q-24 miscite-correction protocol).

### Acceptance-gate item 6(b) -- corrected, supersedes the P4-e §1.5 reconciliation

P4-e's completeness reconciliation (04:2040-2047) leaned on the §1.5
inventory denominator, which this audit caught omitting columns twice
(Q-21, F-046). **This P4-f item 6(b) supersedes 04:2040-2047 as the
completeness basis.** Re-asserted against the verified denominator:

> Every stored numeric value in `app/models/*.py` that has, or should
> have, a money/rate computational counterpart (62 in-scope columns,
> enumerated in the P4-f canonical census, each with `path:line` type +
> CHECK, each spot-checkable, three re-verified at source) maps to a
> Phase-4 classification: Family A/B/C (consolidated 04:2101-2115),
> Family D triage (04:1843-1883), Escalation/Q-25 (04:2116-2122), or the
> P4-f F-046-SoT finding for `estimated_retirement_tax_rate`
> (AUTHORITATIVE). The set-difference census-minus-04 is empty after
> Deliverable 1. The denominator is **model-derived, not §1.5-derived**,
> and is backstopped by a migration-vs-model table diff (no DB-only
> table) and a targeted model-vs-migration type/CHECK check on the
> formerly-uncovered column (no drift). **Acceptance criterion 6(b):
> PASS, on an independently model-derived denominator.** UNCLEAR
> classifications remain open as developer-input findings
> (Q-20/Q-22/Q-23/Q-25/Q-26) per acceptance criterion 3; they are
> classified, not unclassified -- completeness is satisfied.

Phase 5 is NOT begun in this session.

--- Q-answer reconciliation (2026-05-18) ---

Trigger: the Q-NN raised by Phase 4 (Q-20..Q-26) and the inherited Q-11/15/16/17
were adjudicated by the developer 2026-05-18 and recorded as E-18..E-28 in
`00_priors.md` 0.3 (A-NN in `09_open_questions.md`). This section closes the
column classifications left `UNCLEAR` pending those answers. Additive only: the
original `UNCLEAR` blocks and both-interpretation write-ups stand as the
point-in-time record and are superseded here.

Trust-but-verify: each classification is re-derived independently from the
locked E/A; the per-column drift FACTS and write-path traces recorded in
Families A-D are unchanged (no code re-proof). Only the role label, blocked on
developer intent, is now set.

Completeness: every `UNCLEAR` classification line in this document is reconciled
below (Family A current_anchor_period_id; Family B current_principal,
interest_rate; Family D effective_*_rate Escalation 3). Family C was already
all-AUTHORITATIVE (no UNCLEAR). `estimated_retirement_tax_rate` (F-046-SoT) was
already AUTHORITATIVE per the P4-f annotation; only its NULL-semantics
sub-question is closed here. Count parity with the doc's UNCLEAR set: complete.

| Column (Family) | Was | Now -> classification | Governing |
|---|---|---|---|
| `budget.accounts.current_anchor_period_id` (A) | UNCLEAR (Q-16) | RETIRED as an authoritative stored column -> DERIVED: the NULL state is eliminated (account creation = dated t0 anchor); effective anchor period is resolver-computed on read | A-16/A-20 -> E-19 |
| `budget.accounts.current_anchor_balance` (A) | AUTHORITATIVE, "audit-mirror unenforced" | AUTHORITATIVE with the audit-mirror now ENFORCED (atomic t0 + every change); NOT NULL; no range CHECK by design (overdraft-legal -- E-28 sanctioned exception) | A-21 -> E-19/E-28 |
| `budget.account_anchor_history.anchor_balance` (A) | CACHED, "sync invariant not enforced" | CACHED with the mirror invariant ENFORCED (written in one txn with the column at t0 and on every anchor change); NOT NULL, no range CHECK (same exception) | A-21 -> E-19/E-28 |
| `budget.loan_params.current_principal` (B) | UNCLEAR (Q-22/11/15/17) | RETIRED -- neither AUTHORITATIVE nor CACHED; balance is event-derived on read via the single loan-state resolver (origination + confirmed payments + dated anchors) | A-22 -> E-18 |
| `budget.loan_params.interest_rate` (B) | UNCLEAR (Q-23/17) | RETIRED as a hand-maintained mirror; the authoritative series is `rate_history`, resolved on read; DB CHECK aligned to `>= 0 AND <= 1` (sibling parity) | A-23 -> E-18/E-28 |
| `salary.calibration_overrides.effective_*_rate` (D, Escalation 3) | UNCLEAR (Q-25) | AUTHORITATIVE-snapshot -- an immutable pay-stub snapshot whose deriving inputs are frozen at capture; derived server-side at confirm; not re-derived on profile/deduction edit (a new calibration is a new snapshot) | A-25 -> E-20 |
| `auth.user_settings.estimated_retirement_tax_rate` (F-046-SoT) | AUTHORITATIVE (P4-f) -- NULL semantics open | AUTHORITATIVE unchanged; NULL-semantics RESOLVED: correct the model comment (`user.py:215-216`) to "NULL = no retirement-tax adjustment applied" to match `calculate_gap` (no bracket fallback added) | A-26 |

Family C: all six columns already AUTHORITATIVE (no UNCLEAR); only the F-044
`contribution_limit_remaining` miscite is documentation-only and routed to the
reconciliation pass per the Q-21 sub-4 / Q-24 protocol -- not a classification
change. The Family-C `0`-vs-`None` hazards (Q-24) are E-28 remediation, not a
role change (columns stay AUTHORITATIVE).

Prior in-file gate/consolidation tables are superseded by this section for the
reconciled columns; their non-gated content is unaffected and re-checked
consistent.

**Phase 5 may consume `04_source_of_truth.md` with every UNCLEAR classification closed.**
