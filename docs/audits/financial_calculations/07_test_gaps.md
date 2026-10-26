> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 7 -- Test Coverage Gaps for Financial Assertions

Output of Phase 7 per `financial_calculation_audit_plan.md:677-704` and
`phase7_plan.md` sections 2-4. Three top-level parts: Part 7.A (per-concept
coverage census), Part 7.B (divergence-catching audit + symptom regression
targets + cross-page meta-gap), Part 7.C (proposed tests, report-only).

Every coverage claim below quotes an assertion line Read this session; every
absence claim pastes the grep that proves it. Pinned-vs-loose is decided by the
quoted assertion line, never the test name (contract item 3). A pinned value
that contradicts the governing F-NN / E-NN is flagged
`PINNED-AGAINST-DIVERGENT-BEHAVIOR` and the test file is never opened for
modification (contract item 4).

---

## Part 7.A -- per-concept coverage census (slice 1)

Slice-1 family (P7-a): the eight balance / anchor / cross-page concepts that
carry symptoms #1 and #5: `checking_balance`, `projected_end_balance`,
`account_balance`, `period_subtotal`, `chart_balance_series`, `net_worth`,
`savings_total`, `debt_total`.

### Cross-cutting absence evidence (applies to concepts 1-8)

The single defining risk of this slice is the cross-page divergence (E-04:
"same number on the grid, `/savings`, `/accounts`"). The Explore sweep ran the
cross-page-equality greps over `tests/`:

```
grep -rn 'auth_client.get.*grid.*auth_client.get.*accounts\|auth_client.get.*accounts.*auth_client.get.*grid' tests/test_routes/   -> 0 matches
grep -rn 'auth_client.get.*grid.*auth_client.get.*savings\|auth_client.get.*savings.*auth_client.get.*grid'   tests/test_routes/   -> 0 matches
grep -rn 'grid.*==.*savings\|checking.*==.*accounts'                                                          tests/               -> 0 matches
```

No test renders two of {grid index, `/savings` dashboard, `/accounts`
checking_detail} in one function and asserts the same balance. The nearest
candidate, `tests/test_routes/test_accounts.py::test_checking_detail_matches_grid_balance`
(`:2211`), is **not** a cross-page test: it computes its own `calculate_balances`
WITHOUT `selectinload(Transaction.entries)` (`:2259-2268`, no `.options(...)`),
so its own calc path mirrors `/accounts` (entries-absent), never calls the
`/grid` route, and uses only plain Paycheck/Bills transactions (no envelope
expense with cleared entries), so it cannot exercise the F-002/F-009
entries-load divergence. Its assertion is

```
2286   expected_str = "${:,.0f}".format(float(calc_balance))
2287   assert expected_str.encode() in resp.data
```

`float(...)` + `{:,.0f}` rounds to whole dollars (display-only, not a pinned
Decimal) and asserts a substring of one page only. It would pass against the
divergent code. The cross-page consistency invariant is therefore UNTESTED for
every concept in this slice; audit-plan:700-703 requires this be noted
explicitly even where individual concept tests exist. The full divergence-
catching analysis and the proposed cross-page fixture are Part 7.B / Part 7.C
(P7-e); recorded here per-concept as "Consistency-invariant test present? NO".

---

### Concept 1: `checking_balance`

- **Canonical producer.** `balance_calculator.calculate_balances`@`balance_calculator.py:35`
  (and `calculate_balances_with_interest`@`:112` for interest-bearing);
  `02_concepts.md:108-117`. Designated, not UNKNOWN.
- **Pinned-value tests** (each Read this session; producer = `calculate_balances`):
  - `tests/test_services/test_balance_calculator.py::test_credit_excluded_from_balance`
    -- `159  assert balances[periods[0].id] == Decimal("1000.00")` (Credit-status
    excluded; exact Decimal -> PINNED).
  - `tests/test_services/test_balance_calculator.py::test_settled_transactions_excluded_post_anchor`
    -- `414  assert balances[1] == Decimal("1000.00")` and
    `416  assert balances[2] == Decimal("900.00")` (Done/Received excluded
    post-anchor; PINNED).
  - `tests/test_services/test_balance_calculator.py::test_cancelled_shadow_excluded`
    -- `436  assert balances[1] == Decimal("900.00")` (PINNED).
  - `tests/test_services/test_balance_calculator.py::test_empty_transactions`
    -- `449  assert balances[1] == Decimal("2500.00")` (empty period -> anchor
    passthrough; PINNED).
  - `tests/test_services/test_balance_calculator_entries.py::test_grocery_bug_scenario_after_true_up`
    -- `1658 assert balances[seed_periods[1].id] == Decimal("4962.34")`
    (entry-aware path WITH `selectinload(entries)` @`:1645`; the symptom-#1
    mechanism's entries-loaded branch; PINNED).
  - `tests/test_services/test_balance_calculator_entries.py::test_entry_aware_entries_not_loaded`
    -- `725  assert "entries" not in all_txns[0].__dict__` then
    `736  assert balances[seed_periods[1].id] == Decimal("4500.00")` then
    `739  assert "entries" not in all_txns[0].__dict__` (the
    `'entries' not in txn.__dict__` short-circuit -> `effective_amount`
    fallback; the symptom-#1 entries-ABSENT branch; PINNED).
  - 40+ further exact-Decimal assertions in `test_balance_calculator.py`
    (negative/overdraft `result[0] == Decimal("-500.00")`,
    `test_negative_anchor_balance_overdraft`), `test_balance_calculator_entries.py`
    (27 entry-formula scenarios), `test_audit_fixes.py`, `test_workflows.py` --
    all single-path engine units, all pinned.
- **Relationship tests.** None asserting `balance[p]-balance[p-1] ==
  subtotal.net` (D6-03 / F-002 Pair C). Grep for a footer-vs-balance-row
  reconciliation returned only loose substring footer checks (see
  `period_subtotal`). NONE.
- **Pinned / loose classification.** The engine unit tests are PINNED (exact
  string-constructed `Decimal`, hand-computed in the docstring arithmetic, e.g.
  `test_grocery_bug_scenario_after_true_up:1621-1622`). Both divergence branches
  (entries-loaded `4962.34`; entries-absent `4500.00`) are independently pinned
  and each is the engine's *correct* output for its given input.
- **E-NN-consistency check.** F-002 verdict **DIVERGE**, classification
  SILENT_DRIFT (`03_consistency.md:273-275`): the divergence is a cross-page
  *input* difference (`selectinload(entries)` present at `grid.py:229`, absent
  at `savings_dashboard_service.py:92-100`), not an engine miscompute. No E-NN
  designates a single correct cross-page value (E-04 requires equality but does
  not pick which value; symptom #1's `$160` vs `$114.29` reconstruction is
  Phase-5 unresolved). The pinned tests are therefore NOT
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR` -- they pin the engine correctly per
  branch; the gap is that no test pins the two branches *equal across pages*.
- **Consistency-invariant test present?** **NO.** See cross-cutting evidence
  above (0 grep matches; the `test_checking_detail_matches_grid_balance` near-
  miss does not exercise the divergence and would pass against divergent code).
- **Edge cases untested.** Single-path edges ARE pinned: zero/None anchor
  (`test_anchor_balance_none_defaults_to_zero:310`), negative/overdraft
  (`test_negative_anchor_balance_overdraft`), Credit (`:159`), settled (`:416`),
  empty period (`:449`), no-matching-anchor (`test_no_matching_anchor_period:462
  assert len(balances) == 0`). UNTESTED: (a) the cross-page entries-load
  divergence at the page level (F-002/F-009); (b) the anchor-None *cross-
  producer* four-behavior divergence (Q-16 / D6-02) -- a test asserting which of
  blank-row / `$0`-anchored / account-omitted is correct does not exist.
- **Coverage verdict.** **COVERED** for the single-path engine math (>=1 pinned
  test, value F-002-consistent per branch, all single-path edges pinned). The
  defining cross-page invariant (E-04) and the Q-16 anchor-None edge are
  UNTESTED and escalated to Part 7.B; per audit-plan:700-703 this is recorded
  as the headline gap even though concept-unit tests exist. (COVERED here means
  "engine unit covered", NOT "symptom #1 has a regression test" -- it does not.)
- **Independent note.** P7-a. The suite pins *both* sides of the F-002/F-009
  divergence in isolation (`4962.34` entries-loaded; `4500.00` entries-absent)
  yet has zero tests asserting they are equal for the same `(user, period,
  scenario, account)` across grid / `/savings` / `/accounts` -- the
  divergence's defining property is exactly what is not tested.

---

### Concept 2: `projected_end_balance`

- **Canonical producer.** Per account type (`02_concepts.md:176-184`):
  checking/HYSA -> `calculate_balances`/`_with_interest`@`balance_calculator.py:35,112`;
  loan -> `amortization_engine.get_loan_projection`@`:864` (A-04 dual policy);
  investment -> `growth_engine.project_balance`@`:164` (P7-d, out of slice).
  No single cross-account producer.
- **Pinned-value tests.**
  - Checking flavor: same engine as `checking_balance`; all its pinned unit
    tests above apply (the engine return is the per-period end balance).
  - Route-level: `tests/test_routes/test_grid.py::test_full_payday_sequence`
    -- `2902 assert b"$4,850" in resp.data` and
    `2903 assert b"$4,550" in resp.data`. These are LOOSE: substring `in
    resp.data`, comma-grouped display strings, not exact-`Decimal` assertions
    on a return value. (`test_grid_regression.py` has a parallel
    `test_full_payday_sequence` with the same loose substring shape.)
  - Loan flavor: `tests/test_services/test_balance_calculator_debt.py` pins the
    amortization-walked end balance exactly, e.g.
    `test_debt_balance_with_payments` -- `assert balances[2] ==
    Decimal("99900.45")`; `test_debt_26_period_amortization_accuracy` --
    `assert balances[3] == Decimal("199819.19")` (oracle-replicated). PINNED,
    but **fixed-rate only -- zero ARM (`is_arm=True`) tests** (Explore-confirmed
    across all 17 debt tests).
- **Relationship tests.** None reconciling the loan card's stored
  `current_principal` against `proj.current_balance` against the schedule
  (F-003 loan 3-way). NONE.
- **Pinned / loose classification.** Checking engine units PINNED; the
  route-surface assertions LOOSE (substring); loan amortization units PINNED
  (fixed-rate).
- **E-NN-consistency check.** F-003 verdict **DIVERGE** (`03_consistency.md:332-334`):
  checking entries-load axis unconditional (inherits F-002, SILENT_DRIFT); loan-
  base axis SOURCE_DRIFT but its canonicalization **UNKNOWN, blocked on Q-11 /
  Q-15**. The stored-vs-engine-vs-schedule loan base has no designated-correct
  value, so the fixed-rate amortization pins are not against a settled E-NN; the
  loan card's stored-`current_principal` display path has no test at all.
- **Consistency-invariant test present?** **NO** (cross-cutting evidence).
- **Edge cases untested.** ARM in-window loan projection (no `is_arm=True`
  test); the loan-card-vs-`/savings`-vs-net-worth 3-way (F-003 E); checking
  cross-page entries-load (inherits F-002); anchor-None cross-producer (Q-16).
- **Coverage verdict.** **BLOCKED-ON-OPEN-QUESTION** (Q-11 / Q-15). The checking
  flavor is engine-COVERED (inherits concept 1) but a material sub-path -- the
  loan account's `projected_end_balance` base -- has no designated canonical
  producer (F-003 loan axis UNKNOWN), so the concept as a whole cannot be pinned
  to one authoritative value. The fixed-rate amortization tests are pinned but
  to an undesignated-canonical base; ARM is wholly untested.
- **Independent note.** P7-a. The route-level `$4,850/$4,550` checks are the
  only "page shows projected balance" assertions and they are loose substrings;
  combined with the missing ARM coverage, the loan facet of symptom #5 has no
  pinned regression anchor.

---

### Concept 3: `account_balance`

- **Canonical producer.** Per account type the same engines as
  `projected_end_balance` (`02_concepts.md:243-254`). The **cross-account
  dispatcher is NOT canonical**: two independent implementations --
  `_compute_account_projections`@`savings_dashboard_service.py:294` and
  `_get_account_balance_map`@`year_end_summary_service.py:2036` -- with no code
  designating either. `02_concepts.md:254`: "See Q-15."
- **Pinned-value tests.** Per-type engine units as in concepts 1-2 (pinned).
  No test exercises the *dispatcher* equivalence (S6-03: savings-dashboard vs
  year-end loan path). Route surface: `test_accounts.py::test_checking_detail_*`
  pin display strings via `float()`+`{:,.0f}` (LOOSE, display-only) per the
  cross-cutting near-miss analysis.
- **Relationship tests.** None asserting the two dispatchers return the same
  per-account balance for the same `(account, period)`. NONE.
- **Pinned / loose classification.** Engine units PINNED; dispatcher-level and
  route-surface LOOSE/absent.
- **E-NN-consistency check.** F-001 verdict **DIVERGE** (checking-account and
  anchor-None axes hold unconditionally) **plus the dispatcher-canonical axis
  UNKNOWN, blocked on Q-15** (`03_consistency.md:202-206`). No designated
  canonical dispatcher -> no authoritative value to pin to.
- **Consistency-invariant test present?** **NO** (cross-cutting evidence; plus
  no dual-dispatch equivalence test).
- **Edge cases untested.** Dual-dispatcher equivalence (S6-03); the four-way
  anchor-None behavior (F-001 / Q-16 / D6-02 -- blank row vs `$0` vs account-
  omitted, no test asserts which); grid account-scope `if account` edge
  (`grid.py:224-225`); loan base stored-vs-engine-vs-schedule (F-001 row F).
- **Coverage verdict.** **BLOCKED-ON-OPEN-QUESTION** (Q-15). The per-type engine
  math is pinned, but the concept's cross-account producer is undesignated by
  code (two dispatchers), so no test can be pinned to *the* canonical
  `account_balance` until Q-15 resolves which dispatcher is authoritative.
- **Independent note.** P7-a. The dual dispatch (`_compute_account_projections`
  vs `_get_account_balance_map`) is reimplemented with no equivalence test;
  this is the structural twin of the `debt_total` dual-base gap (concept 8).

---

### Concept 4: `period_subtotal`

- **Canonical producer.** `PRIMARY PATH: UNKNOWN` (`02_concepts.md:323-328`).
  Three competing definitions: D1 grid inline (`grid.py:263-279`, raw
  `effective_amount`), D2 balance-calc internal (`_sum_remaining`/`_sum_all`,
  `_entry_aware_amount`), D3 per-domain analytics (settled-only, abs). No
  service-level `period_subtotal` function exists (A-10). Governing question
  Q-10.
- **Pinned-value tests.**
  - `tests/test_routes/test_grid.py::test_footer_totals_reflect_viewed_account_only`
    -- `1451 assert "$2,000" in html  # Total Income (checking).` and
    `1452 assert "$800" in html    # Total Expenses (checking).` -- LOOSE
    (substring on rendered HTML, not exact Decimal; Definition-1 path only).
  - `test_grid.py::test_footer_totals_with_received_income` -- same loose
    substring shape (`"$2,000"`, `"$1,500"` reported by Explore).
  - D2 internals (`_sum_remaining`/`_sum_all`) are pinned indirectly via every
    `calculate_balances` test, but never surfaced as a standalone subtotal nor
    compared to D1.
- **Relationship tests.** None asserting D1 (`grid.py:263-279`) ==
  D2-derived balance-row delta on the same grid (F-002 Pair C / F-004 D1-D2 /
  D6-03). NONE -- this is the same-page divergence with no test.
- **Pinned / loose classification.** All `period_subtotal` route assertions are
  LOOSE substrings; no pinned exact-Decimal subtotal test exists for the user-
  facing (D1) producer.
- **E-NN-consistency check.** F-004 verdict **UNKNOWN -- blocked on Q-10**
  (`03_consistency.md:398-403`). D1-D2 SILENT and D1-D3 DEFINITION recorded as
  facts regardless of Q-10; the verdict label itself is gated.
- **Consistency-invariant test present?** **NO** (no D1==D2 same-page test).
- **Edge cases untested.** Projected envelope expense with cleared entries
  (D1's raw `effective_amount` vs D2's `_entry_aware_amount` -- the exact
  divergence, untested at any layer); the obligations monthly-equivalent path
  (Q-12, out of slice, cross-link only).
- **Coverage verdict.** **PRODUCER-UNKNOWN-CANNOT-PIN** (PRIMARY PATH UNKNOWN
  per `02_concepts.md:323`; governing Q-10). You cannot pin a test to an
  undesignated producer; the only existing assertions are loose substrings on
  Definition 1.
- **Independent note.** P7-a. D6-03 (`balance[p]-balance[p-1] == subtotal.net`
  on the same grid) is the developer's most diagnostic missing test for
  symptom #1's same-page facet and has zero coverage.

---

### Concept 5: `chart_balance_series`

- **Canonical producer.** Per chart domain (`02_concepts.md:367-373`); the
  balance-family flavor audited in slice 1 is HYSA/checking via
  `calculate_balances_with_interest`@`balance_calculator.py:112`. Loan
  (`amortization_engine`) and investment (`growth_engine`) chart series are
  P7-b / P7-d, not audited here.
- **Pinned-value tests** (all in
  `tests/test_services/test_balance_calculator_hysa.py`, Read this session):
  - `test_hysa_balance_includes_interest` --
    `120 assert balances[1] == Decimal("10017.27")`,
    `124 assert interest[1] == Decimal("17.27")`,
    `131 assert balances[2] == Decimal("10034.57")`,
    `142 assert balances[3] == Decimal("10051.90")` (hand-computed in docstring
    `:117-141`; PINNED).
  - `test_interest_by_period_dict` --
    `331 assert set(interest.keys()) == set(balances.keys())` (series shape;
    structural, loose) plus pinned per-period values `339/344/349/355/360/365`.
  - `test_hysa_26_period_compounding_no_drift`,
    `test_hysa_monthly_compounding_exact`,
    `test_hysa_quarterly_compounding_exact`,
    `test_hysa_high_apy_no_overflow`,
    `test_hysa_compounding_with_periodic_deposits` -- each pins the full ordered
    series against an independent server-side Decimal oracle (PINNED).
- **Relationship tests.** None asserting the *rendered* chart series (JS data-
  attr / JSON) equals the server-computed series (E-17), nor the F-005 Pair A-B
  (chart series via `grid.balance_row` entries-loaded vs `accounts.checking_detail`
  entries-absent). NONE. Chart routes are dead/redirect: `tests/test_routes/test_charts.py`
  asserts only 301-to-`/analytics` and 404 (Explore-confirmed; matches
  `03_consistency.md:441-446` -- `chart_data_service.py` removed in `e3b3a5e`).
- **Pinned / loose classification.** HYSA producer series values PINNED
  (exhaustive, oracle-backed); the render-equivalence and cross-surface checks
  absent.
- **E-NN-consistency check.** F-005 verdict **DIVERGE (HYSA/checking flavor,
  inherits F-002)**, SILENT_DRIFT (`03_consistency.md:450-451`). As with concept
  1 the divergence is the cross-surface entries-load input, not the engine; the
  oracle-pinned series values are correct per branch.
- **Consistency-invariant test present?** **NO** (no chart-vs-scalar-card
  equality; no rendered-vs-server series equality).
- **Edge cases untested.** Negative/overdraft, Credit, settled with interest
  (Explore: no HYSA test exercises these); anchor-None with interest; the F-005
  chart-vs-`/accounts`-card cross-surface delta; E-17 rendered-series equality.
- **Coverage verdict.** **COVERED** for the HYSA producer series values (>=1
  pinned test, oracle-backed, F-005-consistent per branch). The E-17 render
  equivalence and the F-005 cross-surface invariant are UNTESTED (escalated to
  Part 7.B); loan/investment chart flavors deferred to P7-b/d.
- **Independent note.** P7-a. The HYSA series is the best-pinned producer in
  this slice (independent oracle, 26-period no-drift), yet still has no test
  proving the charted array the browser receives equals that server series.

---

### Concept 6: `net_worth`

- **Canonical producer.** `_compute_net_worth`@`year_end_summary_service.py:689`
  -- sole token producer, unambiguous at the token level; its *inputs*
  (`_get_account_balance_map`, the second per-account dispatch) are the Q-15
  concern (`02_concepts.md:408-413`).
- **Pinned-value tests.** **NONE for the net-worth figure.** Read this session:
  - `tests/test_services/test_year_end_summary_service.py::test_net_worth_jan_dec_delta`
    -- `998 assert nw["delta"] == nw["dec31"] - nw["jan1"]`. LOOSE: a tautology
    of the producer's own arithmetic (`delta = dec31 - jan1`@`:746`); passes
    even if all three values are jointly wrong. Not a hand-computed scalar.
  - `test_net_worth_debt_negative` --
    `1016 has_negative = any(v["balance"] < ZERO for v in nw["monthly_values"])`
    then `1019 assert has_negative`. LOOSE (boolean any-negative).
  - `test_net_worth_12_points` -- `assert len(nw["monthly_values"]) == 12` /
    `assert months == list(range(1, 13))`. Shape-only, LOOSE.
  - `test_net_worth_debt_uses_amortization` -- `assert month_1 < ZERO`,
    `assert month_1 > static_nw`. LOOSE comparisons.
  - `test_net_worth_consistent_with_savings_progress` --
    `assert result["net_worth"]["dec31"] >= sp_dec31 - Decimal("10000.00")`.
    LOOSE: `>=` with a $10,000 slack -- explicitly does NOT enforce the W-159
    Dec-31 equality; would not catch a multi-thousand-dollar divergence.
- **Relationship tests.** Only the delta tautology (`:998`) and the $10k-slack
  net-worth-vs-savings-progress check; neither pins a value nor enforces W-159
  equality.
- **Pinned / loose classification.** LOOSE-ONLY. No exact-Decimal jan1 / dec31 /
  delta assertion exists anywhere (Explore-confirmed across 29 year-end tests).
- **E-NN-consistency check.** F-006 verdict **UNKNOWN -- blocked on Q-15**
  (`03_consistency.md:513-516`): which dispatcher canonical, and whether
  net_worth_amort W-152/W-159 is "code must catch up" or "plan superseded".
  SOURCE/PLAN/SCOPE divergences recorded regardless.
- **Consistency-invariant test present?** **NO.** The one cross-aggregator
  assertion (`test_net_worth_consistent_with_savings_progress`) is loose with a
  $10k tolerance and does not enforce W-159.
- **Edge cases untested.** W-159 Dec-31 investment equality (savings-progress ==
  net-worth investment) -- the only test tolerates $10k drift; loan liability
  base = schedule vs `proj.current_balance` (F-006); anchor-None dropping the
  account from net worth but not from `/savings` (Q-16).
- **Coverage verdict.** **BLOCKED-ON-OPEN-QUESTION** (Q-15). Compounded by
  LOOSE-ONLY: even setting Q-15 aside, there is no pinned net-worth value test
  and the W-159 invariant test is too slack to catch the F-006 divergence.
- **Independent note.** P7-a. The `delta == dec31 - jan1` test is a structural
  tautology, not a regression anchor; flagged so Phase 8 does not mistake the
  green bar for a pinned net-worth figure.

---

### Concept 7: `savings_total`

- **Canonical producer.** Per account type same as `account_balance`; the
  cross-account aggregate has **no single canonical owner** -- three independent
  aggregators: `_compute_account_projections`@`savings_dashboard_service.py:294`,
  `compute_gap_data`@`retirement_dashboard_service.py:79`,
  `_compute_savings_progress`@`year_end_summary_service.py:887`
  (`02_concepts.md:461-467`; "See Q-15").
- **Pinned-value tests.** Pinned values exist only for *contributions*, not the
  savings-balance aggregate, e.g.
  `tests/test_services/test_year_end_summary_service.py::test_savings_progress_basic`
  -- `assert entry["total_contributions"] == Decimal("200.00")`;
  `test_savings_contributions_from_shadows` -- `expected = Decimal("100.00") +
  Decimal("150.00") + Decimal("250.00")` / `assert entry["total_contributions"]
  == expected`. The balance facets are LOOSE: `assert entry["dec31_balance"] >
  entry["jan1_balance"]`, `assert entry["investment_growth"] > ZERO`,
  `assert entry["jan1_balance"] < Decimal("10000.00")` (Explore-confirmed across
  the savings-progress suite). No pinned `savings_total` aggregate.
- **Relationship tests.** W-159 cross-link is the same loose
  `>= sp_dec31 - Decimal("10000.00")` test cited in concept 6. NONE tight.
- **Pinned / loose classification.** Aggregate balance LOOSE-ONLY;
  contributions pinned but contributions are a different facet (P7-d
  `ytd_contributions` territory).
- **E-NN-consistency check.** F-007 verdict **UNKNOWN -- blocked on Q-15**
  (`03_consistency.md:571-574`): canonical aggregator owner. Multi-aggregator
  divergence and the W-159 gap recorded regardless.
- **Consistency-invariant test present?** **NO** (no A==B==C aggregator-
  equality test; the W-159 test is loose with $10k slack).
- **Edge cases untested.** A/B/C three-aggregator equality where account
  universes overlap; W-159 Dec-31 equality; anchor-None across aggregators
  (Q-16).
- **Coverage verdict.** **BLOCKED-ON-OPEN-QUESTION** (Q-15). Also LOOSE-ONLY for
  the aggregate value: no pinned `savings_total` test, and the only cross-
  aggregator assertion would not catch the F-007 divergence.
- **Independent note.** P7-a. Three aggregators, zero equality tests, and the
  one consistency assertion tolerates $10,000 of drift -- structurally the same
  untested-multi-producer gap as concepts 3 and 8.

---

### Concept 8: `debt_total`

- **Canonical producer.** `PRIMARY PATH: UNKNOWN` (`02_concepts.md:511-517`).
  `_compute_debt_summary`@`savings_dashboard_service.py:802` sums **stored**
  `LoanParams.current_principal` (Definition A); `_compute_net_worth` /
  `_compute_debt_progress` derive from the **amortization schedule**
  (Definition B/C). A-04: these differ for fixed-rate loans with confirmed
  payments. Governing question Q-15.
- **Pinned-value tests.**
  - Definition A (stored base),
    `tests/test_services/test_savings_dashboard_service.py`:
    `test_debt_summary_single_loan` -- `947 assert ds["total_debt"] ==
    Decimal("1000.00")`, `949 assert ds["weighted_avg_rate"] ==
    Decimal("0.05000")`; `test_debt_summary_multiple_loans_weighted_rate` --
    `assert ds["total_debt"] == Decimal("225000.00")`,
    `assert ds["weighted_avg_rate"] == Decimal("0.06322")`;
    `test_debt_summary_excludes_paid_off` -- `== Decimal("2000.00")`;
    `test_debt_summary_all_paid_off` -- `== Decimal("0.00")`. PINNED, all to the
    **stored-`current_principal`** base.
  - Definition B/C (schedule base),
    `tests/test_services/test_year_end_summary_service.py::test_debt_progress_uses_amortization`
    -- `1280 assert entry["jan1_balance"] == Decimal("237547.74")`,
    `1281 assert entry["dec31_balance"] == Decimal("234701.02")`,
    `1282 assert entry["principal_paid"] == Decimal("2846.72")`. PINNED, to the
    **amortization-schedule** base.
- **Relationship tests.** `test_debt_progress_uses_amortization` --
  `1284 assert entry["principal_paid"] == (entry["jan1_balance"] -
  entry["dec31_balance"])` (internal invariant, loose tautology). None
  asserting Definition A == Definition B for the same loan (F-008 A-B), and
  none asserting the internal A-inconsistency (debt-card stored vs same-page
  account-card engine `current_balance`, `03_consistency.md:621-627`).
- **Pinned / loose classification.** Both bases PINNED -- in *different* tests,
  to *contradictory* definitions, with no test asserting they agree.
- **E-NN-consistency check.** F-008 verdict **UNKNOWN for the canonical
  aggregate-debt base -- blocked on Q-15** (`03_consistency.md:639-643`); the
  internal A-inconsistency is a recorded DIVERGE independent of Q-15. Because
  Q-15 has not designated a canonical base, the stored-base pins are not yet
  provably `PINNED-AGAINST-DIVERGENT-BEHAVIOR` -- but they are **flagged**: if
  Q-15 resolves the canonical `debt_total` to the schedule/engine base (A-04
  direction), `test_debt_summary_single_loan:947`,
  `test_debt_summary_multiple_loans_weighted_rate`,
  `test_debt_summary_excludes_paid_off`, `test_debt_summary_all_paid_off`
  immediately become `PINNED-AGAINST-DIVERGENT-BEHAVIOR` (they hard-pin the
  un-maintained stored column symptom #3 says does not move). Flagged only;
  test files not opened (contract item 4).
- **Consistency-invariant test present?** **NO** (no A==B test; no internal
  stored-vs-engine same-page test).
- **Edge cases untested.** Fixed-rate loan with confirmed payments where stored
  `current_principal` != schedule balance (the exact A-04 divergence; the suite
  pins both bases but never their disagreement); the internal one-service two-
  principals inconsistency (`savings_dashboard_service.py:840` vs `:373`).
- **Coverage verdict.** **BLOCKED-ON-OPEN-QUESTION** (Q-15), with the dual-base
  pinning flagged: the suite simultaneously hard-pins two contradictory debt
  bases in separate green tests and has zero tests asserting they reconcile --
  a green bar that launders the F-008 / symptom-#3 divergence.
- **Independent note.** P7-a. This is the most acute anti-coverage finding in
  slice 1: not merely an absent test but two passing pinned tests anchoring
  *opposite* definitions of the same user-facing number, pending Q-15.

---

### Slice-1 verdict roll-up

| Concept | Verdict | Governing F-NN / Q |
| --- | --- | --- |
| `checking_balance` | COVERED (engine single-path); cross-page invariant UNTESTED | F-002 DIVERGE; Q-16 |
| `projected_end_balance` | BLOCKED-ON-OPEN-QUESTION | F-003 DIVERGE; Q-11/Q-15 |
| `account_balance` | BLOCKED-ON-OPEN-QUESTION | F-001 DIVERGE; Q-15 |
| `period_subtotal` | PRODUCER-UNKNOWN-CANNOT-PIN | F-004 UNKNOWN; Q-10 |
| `chart_balance_series` | COVERED (HYSA producer); E-17 + cross-surface UNTESTED | F-005 DIVERGE; F-002 |
| `net_worth` | BLOCKED-ON-OPEN-QUESTION (also LOOSE-ONLY) | F-006 UNKNOWN; Q-15 |
| `savings_total` | BLOCKED-ON-OPEN-QUESTION (also LOOSE-ONLY) | F-007 UNKNOWN; Q-15 |
| `debt_total` | BLOCKED-ON-OPEN-QUESTION (dual-base pinning flagged) | F-008 UNKNOWN; Q-15 |

Carried to Part 7.B (P7-e): the cross-page balance-equality meta-gap (symptoms
#1, #5; audit-plan:700-703), the D6-02 anchor-None single-behavior gap, the
D6-03 `balance[p]-balance[p-1] == subtotal.net` gap, the S6-03 dual-dispatcher
equivalence gap, and the `debt_total` dual-base anti-coverage flag (potential
`PINNED-AGAINST-DIVERGENT-BEHAVIOR` pending Q-15). Part 7.B and Part 7.C are
out of P7-a scope; the eight slice-1 records above are the P7-a deliverable.

---

## Part 7.A -- per-concept coverage census (slice 2)

Slice-2 family (P7-b): the 13 loan / debt concepts that carry symptoms
#2/#3/#4: `monthly_payment`, `loan_principal_real`, `loan_principal_stored`,
`loan_principal_displayed`, `principal_paid_per_period`,
`interest_paid_per_period`, `escrow_per_period`, `total_interest`,
`interest_saved`, `months_saved`, `payoff_date`, `loan_remaining_months`,
`dti_ratio`. Governing findings F-013..F-026 (`03_consistency.md:1009-2017`);
symptom -> root-cause map C2 / CRITICAL pre-list C3 (`:6049-6075`); D6-01 /
S6-03 equivalence-test implications (`06_dry_solid.md:2193-2206`).

### Cross-cutting absence evidence (applies to concepts 1-13)

The defining risks of this slice are the **cross-site `monthly_payment`
divergence** (F-013, 16 call sites feeding incompatible `(P,r,n)` triples;
D6-01: "one loan resolver, asserted-equal `(balance, monthly_payment,
schedule)` across all 16 call surfaces"), the **ARM in-window stability
violation** (F-026 / E-02, symptom #4), the **symptom-#3 stored-principal
non-update** (F-014), and the **dual-dispatcher / dual-base reconciliation**
(S6-03, F-008 cross-ref). Explore swept `tests/` for catching tests of each:

```
grep -rn "is_arm" tests/test_services/test_balance_calculator_debt.py        -> 0 matches (all debt-balance tests fixed-rate)
grep -rn "is_arm=True" tests/                                                -> 34 hits / 4 files (test_loan.py 18, test_amortization_engine.py 10, test_loan_payment_service.py 2, test_debt_strategy.py 1) -- none assert payment STABLE across consecutive months
grep -rn "_compute_real_principal" tests/                                    -> 0 matches (no direct test of the debt-strategy real-principal replay)
grep -rn "savings_dashboard.*year_end\|dispatcher.*equiv\|invariant" tests/  -> 0 cross-service equivalence tests
(settle-then-assert-principal-DECREASE pattern) tests/test_routes/test_loan.py -> 0 (only test_params_update asserts the column after a manual POST; no settle-driven decrease test)
```

No test (a) asserts an ARM Monthly P&I is identical across two consecutive
calendar dates or across the dashboard / schedule / debt-strategy surfaces
(F-013/F-026/D6-01); (b) settles a transfer into a loan and asserts the
displayed/stored principal decreased (symptom #3 / F-014); (c) asserts the
savings-dashboard debt figure equals the year-end loan-path figure for the same
loan (S6-03 / F-008). The boundary test
`tests/test_services/test_amortization_engine.py` (`:1781-1783`,
`test_arm_*rate*`) asserts the schedule payment **changes** at each rate
boundary --

```
1781   assert schedule[11].payment != schedule[12].payment
1782   assert schedule[23].payment != schedule[24].payment
1783   assert schedule[35].payment != schedule[36].payment
```

-- the inverse (constant *inside* the fixed window) is never asserted. These
register-level gaps are recorded per-concept below as "Consistency-invariant
test present? NO" and escalated to Part 7.B/7.C (P7-e).

---

### Concept 1: `monthly_payment`

- **Canonical producer.** One formula `calculate_monthly_payment`@`amortization_engine.py:178`;
  the user-facing per-loan-on-date primary path is
  `get_loan_projection().summary.monthly_payment` site 7/8 (`02_concepts.md:675-684`).
  16 call sites (`02_concepts.md:627-645`).
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_amortization_engine.py`, `TestCalculateMonthlyPayment`):
  - `test_known_30yr` -- `33  assert payment == Decimal("1264.14")`
    (`Decimal("200000"),Decimal("0.065"),360`; PINNED).
  - `test_zero_rate` -- `40  assert payment == Decimal("1000.00")`
    (zero-rate -> `principal/n`; edge PINNED).
  - `test_zero_principal` -- `47  assert payment == Decimal("0.00")`;
    `test_negative_principal` -- `54  assert payment == Decimal("0.00")`;
    `test_zero_remaining_months` -- `61  assert payment == Decimal("0.00")`
    (all three guard edges PINNED).
  - `test_short_term_auto_loan` -- `68  assert payment == Decimal("471.78")`;
    `test_one_month` -- `76  assert payment == Decimal("10050.00")` (PINNED).
  - `test_standard_summary_known_values` -- `787 assert summary.monthly_payment
    == Decimal("1580.17")` (250k/6.5%/360 via `calculate_summary`; PINNED).
  - ARM `TestARMContractualPaymentBug`: `test_arm_summary_uses_current_balance_not_original`
    -- `2643 assert summary.monthly_payment == self.CORRECT_PAYMENT` (docstring
    `M($90,000,7%,300)=$636.10`) and `2648 assert summary.monthly_payment !=
    self.WRONG_PAYMENT` (`$665.30`); `test_arm_projection_without_rate_changes`
    -- `2842 assert proj.summary.monthly_payment == expected_payment` where
    `expected_payment = calculate_monthly_payment(self.CURRENT_PRINCIPAL,
    self.CURRENT_RATE, proj.remaining_months)` (PINNED to the re-amortized
    current-balance value; `is_arm=True`).
- **Relationship tests.** `test_contractual_payment_uses_original_principal` --
  `600 assert proj.summary.monthly_payment == expected` where `expected =
  calculate_monthly_payment(Decimal("35000.00"),Decimal("0.03250"),72)`
  (asserts the projection routes through the contractual triple; PINNED-derived).
  Route surface: `tests/test_routes/test_loan.py::test_schedule_amortization_formula_accurate`
  -- `2145 assert "$1,580.17" in html` and
  `test_refinance_comparison_metrics_hand_calculated` --
  `3514 assert "$1,264.14" in html` (LOOSE: substring on rendered HTML, not an
  exact-Decimal return assertion).
- **Pinned / loose classification.** Engine-formula units PINNED (exact
  string-`Decimal`, hand-anchored docstrings); ARM single-call PINNED; the
  route-surface assertions LOOSE (substring).
- **E-NN-consistency check.** F-013 verdict **DIVERGE** for the cross-site
  principal/rate/`n`/discriminator axes (`03_consistency.md:1134-1141`); F-026
  verdict **DIVERGE** -- the E-02 in-window-stability violation
  (`03_consistency.md:2008-2013`), worked hand value: correct constant
  `$2,398.20`, site-7 returns `$2,460.45` (month 24) then `$2,463.27` (month 25)
  for one 5/5 ARM with no rate change (`:1974-1994`). The pinned ARM tests
  assert a **single call** of `calculate_monthly_payment(current_principal,
  rate, remaining)` equals the A-05-correct re-amortized method value (e.g.
  `$636.10`) -- consistent with A-05 (the resolved ARM method), so these are
  **not** `PINNED-AGAINST-DIVERGENT-BEHAVIOR`: they pin the method correctly at
  one instant. The divergence F-013/F-026 describes is the value differing
  *across sites* and *across consecutive months*; no pinned test exercises
  either axis (cross-cutting grep above).
- **Consistency-invariant test present?** **NO** -- no cross-site
  `(balance,monthly_payment,schedule)` equality (D6-01); no consecutive-month
  ARM stability assertion (E-02/F-026); the only multi-month payment assertion
  asserts the payment *changes* at rate boundaries (`:1781-1783`).
- **Edge cases untested.** ARM inside the 60-month fixed window across
  consecutive `months_elapsed` (F-026, the symptom-#4 mechanism); the 16-site
  `(P,r,n)`-triple equivalence (F-013, esp. site-7 vs site-3 `n`, site-8 vs
  site-16 fixed partially-paid, the `using_contractual`-vs-`is_arm`
  discriminator seam); `update_params`-then-recompute drop (`03_consistency.md:1124-1127`).
- **Coverage verdict.** **COVERED** for the single-call formula and its guard
  edges (>=1 pinned exact-Decimal test, A-05-/W-203-consistent, zero-rate /
  zero-/negative-principal / zero-months pinned). The defining F-013 cross-site
  and F-026 ARM in-window invariants are **UNTESTED** and escalated to Part 7.B
  (per `financial_calculation_audit_plan.md:696-704`, recorded as the headline
  gap even though formula-unit tests exist). COVERED here means "formula unit
  covered", NOT "symptom #2/#4 has a regression test" -- it does not.
- **Independent note.** P7-b. The suite pins the ARM re-amortized-from-current
  method at a single instant but has zero tests that the displayed Monthly P&I
  is stable month-over-month inside the fixed window or equal across the 16
  sites -- the exact properties symptoms #2/#4 are about.

---

### Concept 2: `loan_principal_real`

- **Canonical producer.** `get_loan_projection().current_balance`@`amortization_engine.py:864`
  (A-04 dual: ARM=stored, fixed=confirmed-payment-walked); parallel
  reimplementation `_compute_real_principal`@`debt_strategy.py:147`
  (`02_concepts.md:780-784`).
- **Pinned-value tests.** **NONE for the real-principal scalar.** Explore +
  this-session grep: `grep -rn "_compute_real_principal" tests/` -> 0 matches;
  no test asserts `proj.current_balance == Decimal(...)` reflecting confirmed
  payments for a partially-paid loan. The nearest,
  `tests/test_services/test_amortization_engine.py::test_zero_remaining_months`,
  asserts `572 assert proj.schedule[-1].remaining_balance == Decimal("0.00")`
  (fully-elapsed terminal balance, not the as-of-today real principal with
  confirmed payments). `test_balance_calculator_debt.py` pins a per-period
  *trajectory* (`balances[2] == Decimal("99900.45")`), not the scalar
  `loan_principal_real` (concept `projected_end_balance` territory).
- **Relationship tests.** None reconciling A (`get_loan_projection`) vs C
  (`_compute_real_principal`) for the same fixed loan (F-014 A-C). NONE.
- **Pinned / loose classification.** No producer-level test exists to classify;
  the symptom-#3 update path has no test at all.
- **E-NN-consistency check.** F-014 verdict **DIVERGE**
  (`03_consistency.md:1237-1242`): (1) the stored column has no settle-driven
  writer (grep-proven, symptom #3), (2) the card renders STORED regardless of
  type, (3) A-fixed vs C-fixed are two replays that disagree (A-06). Canonical
  aggregate axis **UNKNOWN, blocked on Q-15**. No pinned test exists to be
  consistent or inconsistent with it -- the gap is total.
- **Consistency-invariant test present?** **NO** (cross-cutting grep:
  zero settle-then-assert-decrease; zero `_compute_real_principal` test).
- **Edge cases untested.** Symptom-#3 core: confirmed transfer into an ARM loan
  does not move principal until manual edit (no test); fixed-rate
  confirmed-payment walk reflected by `proj.current_balance` but not the card
  (no test); A-fixed (A-06-prepared) vs C-fixed (raw replay) divergence for an
  escrow-inclusive biweekly mortgage.
- **Coverage verdict.** **NO-PINNED-TEST.** The symptom-#3 regression target
  (`05_symptoms.md:1714-1721`, "strict principal decrease per settled
  transfer") has zero coverage; the canonical real-principal scalar has no
  pinned-value test on any path. (The aggregate-canonical axis is additionally
  Q-15-blocked, but the scalar gap is unconditional and is the operative
  verdict.)
- **Independent note.** P7-b. This is the highest-stakes absence in slice 2: the
  developer's reported symptom #3 has no falsifying test, so a fix cannot be
  regression-locked.

---

### Concept 3: `loan_principal_stored`

- **Canonical producer.** model `LoanParams.current_principal`@`loan_params.py:54`
  (A-04: AUTHORITATIVE for ARM, CACHED-for-display for fixed;
  `02_concepts.md:835-839`). Sole writer `update_params`@`loan.py:634`.
- **Pinned-value tests.**
  `tests/test_routes/test_loan.py::test_params_update` --
  `344 assert params.current_principal == Decimal("22000.00")` (after POSTing
  `current_principal="22000.00"`; PINNED -- verifies the form-bind/percentage
  conversion + `Numeric(12,2)` persist path, the only writer).
  `test_setup_prefills_current_principal` --
  `321 assert b'value="15000.00"' in resp.data` (LOOSE: form-prefill substring).
- **Relationship tests.** None asserting stored == engine-walked, or stored
  decreasing on settle (F-015 B-vs-C). NONE.
- **Pinned / loose classification.** The manual-write column read is PINNED
  (exact Decimal); the prefill is LOOSE substring.
- **E-NN-consistency check.** F-015 verdict **DIVERGE**
  (`03_consistency.md:1290-1293`): B (stored, `loan/dashboard.html:104`) vs C
  (`amortization_engine.py:980-984` engine-walked) SOURCE_DRIFT for a
  partially-paid fixed loan. The pinned test pins the column == the value
  *manually written* -- A-04-consistent (the column stores exactly what
  `update_params` set); it does **not** contradict F-015 (it never exercises
  the fixed-with-confirmed-payments stale-mirror case). Not
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **NO** (no stored-vs-engine-walked
  test; no settle-non-update test).
- **Edge cases untested.** Fixed loan with confirmed payments where stored !=
  engine-walked (F-015 SOURCE); the stale-mirror staleness after N settled
  transfers (symptom #3, cross-ref F-014).
- **Coverage verdict.** **COVERED** for the stored-column manual-write
  semantics (>=1 pinned exact-Decimal test, A-04-consistent). The F-015
  stored-vs-engine divergence and the symptom-#3 no-settle-writer behavior are
  **UNTESTED**, escalated to Part 7.B.
- **Independent note.** P7-b. Coverage exists only for "the form writes the
  column"; the load-bearing risk (the column is a stale mirror that the
  prominent card renders) has no test.

---

### Concept 4: `loan_principal_displayed`

- **Canonical producer.** `PRIMARY PATH: UNKNOWN` (`02_concepts.md:882`;
  P2-b-resolved Appendix-A orphan; governing question Q-11). Page 1
  (`loan/dashboard.html:104`) renders STORED; pages 4-6 render engine-real.
- **Pinned-value tests.**
  `tests/test_routes/test_loan.py::test_refinance_principal_auto_calculated`
  -- `3223 assert "$250,000.00" in html` (LOOSE substring);
  `test_setup_prefills_current_principal` --
  `321 assert b'value="15000.00"' in resp.data` (LOOSE substring). No pinned
  exact-Decimal on any displayed-principal card; no P1-vs-P4-vs-P5/6
  reconciliation test.
- **Relationship tests.** None asserting the three display surfaces agree for
  the same loan-on-date (F-016 P1-P4, P4-P5/6). NONE.
- **Pinned / loose classification.** All assertions LOOSE substrings; no pinned
  displayed-principal test.
- **E-NN-consistency check.** F-016 verdict **UNKNOWN -- PRIMARY PATH UNKNOWN,
  blocked on Q-11** (`03_consistency.md:1347-1352`); the P1-vs-P4 and P4-vs-P5/6
  divergences are recorded as facts (E-04) regardless of Q-11.
- **Consistency-invariant test present?** **NO.**
- **Edge cases untested.** P1 (STORED card) vs P4 (refinance prefill,
  engine-real) for a partially-paid fixed loan; P4 (A-06-prepared replay) vs
  P5/6 (`_compute_real_principal` raw replay) for an escrow-inclusive biweekly
  mortgage; the 7th aggregate surface (debt card stored base vs page-4/5/6
  engine base).
- **Coverage verdict.** **PRODUCER-UNKNOWN-CANNOT-PIN** (PRIMARY PATH UNKNOWN
  per `02_concepts.md:882`; governing Q-11). A test cannot be pinned to an
  undesignated correct producer; only loose substrings exist.
- **Independent note.** P7-b. Structural twin of slice-1 `period_subtotal` /
  `debt_total`: a user-facing number with three competing render bases and zero
  reconciliation tests.

---

### Concept 5: `principal_paid_per_period`

- **Canonical producer.** `generate_schedule` per-row `principal`@`amortization_engine.py:602`;
  consumers `calculate_balances_with_amortization.principal_by_period`@`balance_calculator.py:283`
  and `_compute_debt_progress`@`year_end_summary_service.py:871`
  (`02_concepts.md:924-929`).
- **Pinned-value tests.**
  - Engine per-row (`tests/test_services/test_amortization_engine.py`):
    `768 assert schedule[0].principal == Decimal("226.00")` (250k/6.5%/360);
    additional pinned rows reported by Explore (`810.66`, `1014.71`,
    `-50.00` negative-amort edge, `1000.00` zero-rate equal-split, ARM
    `111.10`) -- PINNED.
  - Balance-path B (`tests/test_services/test_balance_calculator_debt.py`,
    fixed-rate only): `test_debt_balance_with_payments` --
    `111 assert principal_by_period[1] == Decimal("0.00")`,
    `122 assert principal_by_period[2] == Decimal("99.55")` (hand-computed in
    the `100000*(0.06/12)=500.00 ... 99.55` comment `:116-118`);
    `test_debt_26_period_amortization_accuracy` --
    `352 assert pbp[3] == Decimal("180.81")` (hand-arith comment `:347-351`).
    PINNED, oracle/hand-anchored, **no `is_arm` test** (grep: 0 matches).
  - Year aggregate C (`tests/test_services/test_year_end_summary_service.py::test_debt_progress_uses_amortization`)
    -- `1282 assert entry["principal_paid"] == Decimal("2846.72")` (PINNED).
- **Relationship tests.** `test_debt_progress_uses_amortization` --
  `1284 assert entry["principal_paid"] == (entry["jan1_balance"] -
  entry["dec31_balance"])` (internal tautology of C's own arithmetic, LOOSE).
  None asserting A (engine per-row) == B (`principal_by_period`) == C (year
  delta) for the same loan/period (F-017 A-B / B-C). NONE.
- **Pinned / loose classification.** Engine and B-path units PINNED; the
  jan1-dec31 invariant LOOSE (tautology).
- **E-NN-consistency check.** F-017 verdict **DIVERGE**
  (`03_consistency.md:1436-1438`): B omits A-06 escrow subtraction -> for an
  escrow-inclusive payment B over-states principal by the escrow (worked:
  `$775.00` B vs `$275.00` A/C, a `$500.00` gap, `:1429-1435`); A-vs-C AGREE by
  construction. The B-path pinned tests use **non-escrow** loans
  (`test_balance_calculator_debt.py` has zero `escrow` references, grep-proven),
  so they pin B where B and A coincide -- not against E-NN. The F-017
  divergence (escrow-inclusive B) is simply unexercised.
- **Consistency-invariant test present?** **NO** (no A==B==C cross-producer
  equality; the only invariant is C's internal jan1-dec31 tautology).
- **Edge cases untested.** Escrow-inclusive biweekly payment through B
  (`balance_calculator.py:270` raw `effective_amount`, the exact F-017
  divergence); ARM debt-balance path (zero `is_arm` debt tests); B's
  ORM-load-context dependence (F-017 SILENT axis).
- **Coverage verdict.** **COVERED** for the engine per-row split and the
  non-escrow balance-path B (>=1 pinned hand-anchored test each,
  F-017-consistent where AGREE). The F-017 escrow-subtraction divergence
  (B vs A/C) and the A==B==C equivalence are **UNTESTED**, escalated to
  Part 7.B.
- **Independent note.** P7-b. The `$500` escrow misattribution F-017 worked is
  precisely the untested edge -- every pinned B test deliberately avoids escrow.

---

### Concept 6: `interest_paid_per_period`

- **Canonical producer.** `generate_schedule` per-row `interest`@`amortization_engine.py:517`;
  year-end calendar aggregate `_compute_mortgage_interest`@`year_end_summary_service.py:380`
  over A-06-preprocessed payments (`02_concepts.md:975-980`).
- **Pinned-value tests** (`tests/test_services/test_amortization_engine.py`):
  `767 assert schedule[0].interest == Decimal("1354.17")` (250k/6.5%/360);
  Explore-reported pinned rows `50.00`, `45.95`, zero-rate `0.00`, plus ARM
  rate-change rows `schedule[12].interest == expected_interest`,
  `schedule[0].interest == expected_interest` (PINNED, expected computed from
  the new-rate balance). No `_compute_mortgage_interest` calendar-year sum is
  pinned (the year-end suite pins `principal_paid`, not interest -- concept 5).
- **Relationship tests.** None reconciling raw per-row interest vs the
  A-06-preprocessed `_compute_mortgage_interest` (F-018 A-B), nor the two raw
  callers vs the prepared path. NONE.
- **Pinned / loose classification.** Engine per-row interest PINNED (incl.
  ARM rate-change rows); the year-end aggregate absent.
- **E-NN-consistency check.** F-018 verdict **DIVERGE**
  (`03_consistency.md:1499-1503`): two raw `generate_schedule` callers
  (`savings_dashboard_service.py:471,488`; `debt_strategy.py:175,181`) bypass
  A-06 -> interest on an escrow-inflated paydown trajectory (worked: month-2
  `$1,495.50` raw vs `$1,498.00` A-06-correct, compounding, `:1489-1498`);
  A-B dashboard/year-end AGREE by construction. The pinned per-row tests use
  non-escrow loans, so they pin the formula where raw==prepared -- not against
  E-NN; the F-018 divergence is unexercised.
- **Consistency-invariant test present?** **NO.**
- **Edge cases untested.** The two raw callers' escrow-inflated interest series
  (F-018 DEFINITION); the calendar-year `_compute_mortgage_interest` figure
  (Schedule-A accuracy, A-06); ARM in-window inheriting the F-013 input risk.
- **Coverage verdict.** **COVERED** for the canonical per-row interest formula
  (>=1 pinned exact-Decimal test, incl. ARM rate-change rows,
  F-018-consistent where AGREE). The F-018 raw-vs-A-06-prepared divergence and
  the year-end calendar aggregate are **UNTESTED**, escalated to Part 7.B.
- **Independent note.** P7-b. The Schedule-A-driving `_compute_mortgage_interest`
  has no pinned-value test at all.

---

### Concept 7: `escrow_per_period`

- **Canonical producer.** `calculate_monthly_escrow`@`escrow_calculator.py:14`
  (sum-of-`annual/12`); `calculate_total_payment`@`:60` (P&I+escrow);
  `project_annual_escrow`@`:79` (inflated; `02_concepts.md:1023-1026`).
- **Pinned-value tests** (`tests/test_services/test_escrow_calculator.py`,
  Read this session):
  `115 assert calculate_monthly_escrow([comp1]) == Decimal("100.00")`,
  `116 ... == Decimal("200.00")`, `117 ... == Decimal("50.00")`,
  `118 assert combined == Decimal("350.00")`;
  `test_pi_plus_escrow` -- `132 assert result == Decimal("1864.14")`
  (`calculate_total_payment(Decimal("1264.14"), [4800,2400])`);
  `test_no_escrow` -- `137 assert result == Decimal("1000.00")`;
  `test_no_inflation` -- `152 assert amount == Decimal("7200.00")`; plus
  Explore-reported inflated `result[2][1] == Decimal("1152.48")`. All PINNED
  exact Decimal at the canonical producer; zero-component and inflation edges
  pinned.
- **Relationship tests.** `118 assert combined == individual_sum`
  (sum-of-components invariant; meaningful, PINNED-derived). None asserting the
  dashboard-displayed escrow == the escrow `prepare_payments_for_engine`
  subtracts (F-019 A-B) -- but F-019 establishes these call the IDENTICAL
  function with identical inputs, so the invariant holds by construction.
- **Pinned / loose classification.** Producer units PINNED. Route surface
  (`test_loan.py:543 assert "$400.00/mo" in html`,
  `:1758 assert "800.00" in html`) LOOSE substrings.
- **E-NN-consistency check.** F-019 verdict **AGREE** for the numeric
  escrow-per-period (display A == engine-subtracted B; shared function,
  `03_consistency.md:1558-1561`); the `loan/_escrow_list.html:37`
  Jinja-arithmetic `|float / 12` is a recorded **E-16 standards finding**, not
  numeric drift. The pinned values are F-019-AGREE-consistent.
- **Consistency-invariant test present?** Partially -- the sum-of-components
  invariant (`:118`) is pinned; the A-vs-B (display vs engine-subtracted)
  equality is untested but holds by construction (same function). No test of
  the E-16 template-vs-service per-component rounding (`_escrow_list.html:37`).
- **Edge cases untested.** The E-16 `loan/_escrow_list.html:37` per-component
  `float`-division vs the service `quantize(sum(annual/12))` (cent-rounding
  decomposition; F-019 A-C); inflation interaction with `prepare_payments_for_engine`.
- **Coverage verdict.** **COVERED** (>=1 pinned exact-Decimal test at the
  canonical producer, F-019-AGREE-consistent, zero/inflation edges pinned). The
  E-16 template-arithmetic finding has no test but is a standards/Phase-6 item,
  not a numeric-coverage gap; recorded for Part 7.B cross-link only.
- **Independent note.** P7-b. Best-pinned producer in slice 2 alongside
  `loan_remaining_months`; the only residual is the E-16 template-computation
  surface, which is a coding-standards finding rather than a value gap.

---

### Concept 8: `total_interest`

- **Canonical producer.** Definition 1 (life-of-loan)
  `_derive_summary_metrics`@`amortization_engine.py:622` via
  `get_loan_projection`/`calculate_summary`; Definition 2 (calendar-year, A-06)
  `_compute_mortgage_interest`@`year_end_summary_service.py:380`
  (`02_concepts.md:1073-1078`).
- **Pinned-value tests** (`tests/test_services/test_amortization_engine.py`):
  `778 assert total_interest == Decimal("318861.58")` (sum of the 360-row
  schedule, 250k/6.5%/360) and `788 assert summary.total_interest ==
  Decimal("318861.58")` (Definition 1 via `calculate_summary`; PINNED).
  No Definition-2 `_compute_mortgage_interest` pinned test (year-end suite pins
  `principal_paid`, not interest).
- **Relationship tests.** `test_summary_with_extra_matches_accelerated_schedule`
  -- `684 assert summary.total_interest_with_extra == accel_interest` where
  `accel_interest = sum(r.interest for r in accel_schedule)` (cross-validated
  against an independently generated schedule; meaningful, PINNED-derived).
  None reconciling Definition 1 vs `calculate_strategy` per-debt total (F-020
  A-C). NONE.
- **Pinned / loose classification.** Definition-1 units PINNED; Definition-2
  and the A-vs-C strategy reconciliation absent.
- **E-NN-consistency check.** F-020 verdict **DIVERGE**
  (`03_consistency.md:1623-1627`): A-vs-C (life-of-loan engine from
  `original_principal` vs strategy total from site-16 `real_principal` at
  today) and B-vs-C hold from code; A-vs-B is DEFINITION-by-design and HOLDS
  while the dashboard "(life of loan)" label stays distinct from the year-end
  figure. The pinned tests are all Definition-1 single-loan -- consistent with
  the A path; not against E-NN.
- **Consistency-invariant test present?** **NO** (no Def1-vs-Def2 label-
  distinctness test; no single-loan A-vs-C reconciliation).
- **Edge cases untested.** Definition-2 calendar-year sum over A-06-prepared
  payments; A-vs-C (a one-debt strategy run must equal the payoff calculator's
  total_interest, `03_consistency.md:1610-1613`); inherited F-013 ARM-input
  risk on the schedule the sum reads.
- **Coverage verdict.** **COVERED** for Definition-1 life-of-loan
  (>=1 pinned exact-Decimal test, F-020-consistent for the A path,
  cross-validated against an independent schedule). Definition-2 and the
  A-vs-C strategy reconciliation are **UNTESTED**, escalated to Part 7.B.
- **Independent note.** P7-b. The two-definitions risk (F-020) is real but the
  pinned coverage only anchors Definition 1; a page conflating the labels would
  not be caught.

---

### Concept 9: `interest_saved`

- **Canonical producer.** Single-loan acceleration
  `calculate_summary`@`amortization_engine.py:740-749` (ROUND_HALF_UP);
  multi-debt `calculate_strategy`@`debt_strategy_service.py:521`
  (`02_concepts.md:1108-1112`).
- **Pinned-value tests** (`tests/test_services/test_amortization_engine.py`):
  `test_summary_with_extra` -- `300 assert summary.interest_saved ==
  Decimal("90074.66")` (200k/6.5%/360, +$200/mo; hand-anchored docstring
  `255085.82 - 165011.16 = 90074.66`); `test_summary_no_extra` --
  `270 assert summary.interest_saved == Decimal("0.00")`;
  `365 assert summary.interest_saved == Decimal("0.00")` (explicit zero).
  PINNED, path A.
- **Relationship tests.** `test_summary_with_extra_matches_accelerated_schedule`
  -- `688 assert summary.interest_saved == summary.total_interest -
  accel_interest` (cross-validated; PINNED-derived). Route B path:
  `tests/test_routes/test_loan.py::test_refinance_comparison_metrics_hand_calculated`
  -- `3524 assert "$68,572.58" in html` (LOOSE substring; an exact value but
  not on a half-cent boundary, so it cannot catch the F-021 banker's-vs-HALF_UP
  delta at `loan.py:968`). `tests/test_routes/test_debt_strategy.py::test_comparison_table_structure`
  -- `508 assert "Interest Saved" in html` (LOOSE: label presence only).
- **Pinned / loose classification.** Path-A `calculate_summary` units PINNED
  (ROUND_HALF_UP); the B (refinance route) and C (debt-strategy) surfaces LOOSE
  substrings.
- **E-NN-consistency check.** F-021 verdict **DIVERGE**
  (`03_consistency.md:1686-1689`): ROUNDING (path B `loan.py:968`
  `.quantize(Decimal("0.01"))` defaults to banker's -- an A-01 24-list site --
  vs path A ROUND_HALF_UP), DEFINITION (three "interest saved" definitions),
  SCOPE (A-06 for C). The pinned tests exercise **path A** (ROUND_HALF_UP,
  A-01-clean) -- E-NN-consistent for that path. The F-021 finding is at
  **path B** (`loan.py:968`), which has only a LOOSE substring test that would
  not fail on the banker's-vs-HALF_UP cent. Not
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR` (no pinned test on the divergent path).
- **Consistency-invariant test present?** **NO** (no single-debt A-vs-C
  equivalence; no half-cent boundary test for `loan.py:968`).
- **Edge cases untested.** The `loan.py:968` banker's-rounding half-cent
  boundary (F-021 ROUNDING / A-01); a one-debt `calculate_strategy` run ==
  the payoff calculator's `interest_saved` (F-021 DEFINITION A-vs-C); the
  refinance "interest savings" exact-Decimal value.
- **Coverage verdict.** **COVERED** for path A `calculate_summary`
  (>=1 pinned exact-Decimal test, ROUND_HALF_UP, F-021-consistent for that
  path, cross-validated). The F-021 ROUNDING site (`loan.py:968`) and the
  A-vs-C definition reconciliation are **UNTESTED** (only LOOSE substrings),
  escalated to Part 7.B.
- **Independent note.** P7-b. The A-01-confirmed banker's site `loan.py:968`
  ships behind a loose substring assertion -- a green bar that cannot catch the
  documented rounding defect.

---

### Concept 10: `months_saved`

- **Canonical producer.** Single-loan `calculate_summary`@`amortization_engine.py:739`
  (`len(standard)-len(accelerated)`); refinance `break_even_months` (W-242,
  distinct formula); `calculate_strategy` (`02_concepts.md:1141-1144`).
- **Pinned-value tests** (`tests/test_services/test_amortization_engine.py`):
  `test_summary_with_extra` -- `297 assert summary.months_saved == 110`
  (hand-anchored `360 - 250 = 110`); `test_summary_no_extra` --
  `269 assert summary.months_saved == 0`; `360 assert summary.months_saved == 0`.
  PINNED int, path A.
- **Relationship tests.** `test_summary_with_extra_matches_accelerated_schedule`
  -- `686 assert summary.months_saved == self.MONTHS - len(accel_schedule)`
  (cross-validated against an independent schedule; PINNED-derived). Route:
  `tests/test_routes/test_debt_strategy.py::test_comparison_table_structure`
  -- `509 assert "Months Saved" in html` (LOOSE label-only); no
  `break_even_months` exact assertion (the refinance hand-calc test pins
  monthly/interest dollars, not the months slot).
- **Pinned / loose classification.** Path-A units PINNED; the refinance
  break-even and debt-strategy surfaces LOOSE.
- **E-NN-consistency check.** F-022 verdict **DIVERGE**
  (`03_consistency.md:1747-1750`): four distinct integer-month quantities share
  the token (standard-vs-accelerated A; committed-vs-original B; refinance
  break-even C; strategy payoff-delta D); render-slot reuse risks misleading
  the user (W-242). The pinned tests cover path A only -- E-NN-consistent for
  A; the definitional fork is unexercised.
- **Consistency-invariant test present?** **NO** (no test that the four
  "months" figures are labelled distinctly / not mis-comparable; no A-vs-B-vs-D
  reconciliation).
- **Edge cases untested.** Refinance `break_even_months` value + its distinct
  labelling (F-022 / W-242, the user-misleading risk); committed-vs-original B
  (`loan.py:957-959`); strategy payoff-month delta D.
- **Coverage verdict.** **COVERED** for path-A `calculate_summary.months_saved`
  (>=1 pinned exact-int test, F-022-consistent for A, cross-validated). The
  F-022 definitional fork (break-even / committed / strategy variants and their
  label-distinctness) is **UNTESTED**, escalated to Part 7.B.
- **Independent note.** P7-b. The W-242 "user could compare 27-month break-even
  to 54-month acceleration" risk (`03_consistency.md:1740-1746`) has no test.

---

### Concept 11: `payoff_date`

- **Canonical producer.** `_derive_summary_metrics`@`amortization_engine.py:645`
  (`schedule[-1].payment_date`) via `get_loan_projection`/`calculate_summary`
  (`02_concepts.md:1179-1183`).
- **Pinned-value tests** (`tests/test_services/test_amortization_engine.py`):
  `test_standard_summary_known_values` -- `789 assert summary.payoff_date ==
  date(2054, 1, 1)` (250k/6.5%/360; PINNED exact date).
- **Relationship tests.** `663 assert summary.payoff_date ==
  schedule[-1].payment_date` and `test_summary_with_extra_matches_accelerated_schedule`
  -- `685 assert summary.payoff_date_with_extra ==
  accel_schedule[-1].payment_date` (cross-validated against an independently
  generated schedule -- meaningful, PINNED-derived, not a bare tautology since
  the schedule is regenerated independently). None reconciling A vs
  `calculate_payoff_by_date` B (no-anchor ARM) or vs `calculate_strategy` C
  (F-023). NONE.
- **Pinned / loose classification.** Summary payoff_date PINNED (exact `date`)
  + cross-validated against an independent schedule.
- **E-NN-consistency check.** F-023 verdict **DIVERGE**
  (`03_consistency.md:1809-1812`): A-vs-B (`calculate_payoff_by_date` has no
  `payments`/`anchor` params -> cannot reproduce an ARM's anchored schedule,
  SILENT) and A-vs-C (strategy walk from site-16 `real_principal`) hold; A-vs-D
  (W-239 transfer-template `end_date`) AGREE by construction. The pinned test
  is fixed-rate path A -- E-NN-consistent for A; the B/C divergences are
  unexercised.
- **Consistency-invariant test present?** **NO** (no A-vs-B ARM-anchor test;
  no W-239 transfer-`end_date` == displayed-payoff test).
- **Edge cases untested.** ARM `calculate_payoff_by_date` ignoring the
  user-verified anchor (F-023 A-vs-B SILENT, the symptom-#4-adjacent ARM seam);
  the W-239 auto-set transfer-template `end_date` == `summary.payoff_date`
  (`02_concepts.md:1188-1189`); strategy-vs-engine payoff for the same loan.
- **Coverage verdict.** **COVERED** for the fixed-rate summary `payoff_date`
  (>=1 pinned exact-`date` test, F-023-consistent for path A, cross-validated
  against an independent schedule). The F-023 A-vs-B ARM-no-anchor seam, the
  A-vs-C strategy reconciliation, and the W-239 sync are **UNTESTED**,
  escalated to Part 7.B.
- **Independent note.** P7-b. The W-239 cross-family seam (transfer-template
  `end_date` must equal the displayed payoff) has zero coverage -- a wrong
  payoff silently mis-bounds shadow-transfer generation.

---

### Concept 12: `loan_remaining_months`

- **Canonical producer.** `calculate_remaining_months`@`amortization_engine.py:128`
  -- sole canonical producer, single-path (`02_concepts.md:1212-1214`;
  F-024 AGREE).
- **Pinned-value tests** (`tests/test_services/test_amortization_engine.py`,
  Read this session):
  `484 assert result == 300` (`date(2020,1,1),360,as_of=date(2025,1,1)`;
  hand-computed docstring `(2025-2020)*12=60 -> 360-60=300`);
  `497 assert result == 0` (past-term edge, docstring `max(0,12-60)=0`);
  `510 assert result == 360` (same-month-as-origination edge, docstring
  `360-0=360`). PINNED exact int with hand-arithmetic docstrings.
- **Relationship tests.** `test_remaining_months_none_as_of` --
  `519 assert isinstance(result, int)` then `521 assert result >= 0` (LOOSE:
  the default `as_of=today` path is only type/sign-checked, not value-pinned).
- **Pinned / loose classification.** Explicit-`as_of` cases PINNED (exact int,
  edges past-term/same-month pinned); the default-`today` path LOOSE
  (`isinstance` + `>= 0`).
- **E-NN-consistency check.** F-024 verdict **AGREE** (single canonical
  producer; every ARM `monthly_payment` site calls it with `as_of=today`;
  internally consistent, `03_consistency.md:1853-1856`). The pinned values
  match the calendar formula F-024 verifies. Consistent.
- **Consistency-invariant test present?** N/A (single-path; F-024 verifies
  every ARM site uses this one producer -- that internal-consistency is a code
  fact, not a test, and is correctly out of test scope here).
- **Edge cases untested.** The default `as_of=today` path is value-loose only
  (acceptable -- a today-dependent value cannot be pinned without freezing the
  clock; the explicit-`as_of` pins cover the formula). The `loan.py:1126`
  `len(current_schedule)` alternative "remaining" is a labelled-distinct
  consumer quantity (F-024), not a gap in this concept.
- **Coverage verdict.** **COVERED** (>=1 pinned exact-int test,
  F-024-AGREE-consistent, past-term and same-month edges pinned with
  hand-arithmetic).
- **Independent note.** P7-b. This is a Phase-7 finding **against an
  assumption**: F-024's closing line (`03_consistency.md:1858-1859`) carries
  the PA-28 claim that `calculate_remaining_months` has "zero pinned-value
  coverage." The live suite contradicts that -- `:484/:497/:510` pin
  `300/0/360` exactly. Recorded per `phase7_plan.md:81-86` (a register entry
  assumed untested that in fact has pinned, E-NN-consistent tests).

---

### Concept 13: `dti_ratio`

- **Canonical producer.** `savings_dashboard_service` (debt-side numerator
  `_compute_debt_summary`@`:802`, division @`:173-176`); the
  `dashboard/_debt_summary.html` widget delegates via
  `dashboard_service._get_debt_summary`@`:533` (`02_concepts.md:1259-1262`).
- **Pinned-value tests** (`tests/test_services/test_savings_dashboard_service.py`,
  `TestDTI`, Read this session):
  - `test_dti_no_salary` -- `1265 assert ds["dti_ratio"] is None` (the
    defined no-salary edge; asserts the specific edge value `None`).
  - `test_dti_with_salary` -- `1297 assert ds["dti_ratio"] is not None`,
    `1298 assert isinstance(ds["dti_ratio"], Decimal)` (LOOSE: existence/type
    only), with `1300 assert ds["gross_monthly_income"] == Decimal("6500.00")`
    (pins the **denominator**, not the ratio; the docstring hand-computes
    `~0.7%` but **no assertion pins `0.7`**).
  - `test_dti_zero_debt` -- `1338 assert ds["dti_ratio"] == Decimal("0.0")`
    (PINNED but the trivial zero-debt edge).
- **Relationship tests.** None asserting the `savings/dashboard.html` DTI ==
  the `dashboard/_debt_summary.html` widget DTI (F-025 A-B delegate
  equivalence). Grep (Explore): 0 cross-service/delegate equivalence tests.
- **Pinned / loose classification.** The only non-trivial DTI path
  (`test_dti_with_salary`) asserts `is not None` + `isinstance` -- **LOOSE**;
  the only `==` on the ratio is the trivial `Decimal("0.0")` zero-debt edge.
  No pinned non-trivial DTI value (the hand-computable `~0.7%` is asserted only
  via `isinstance`).
- **E-NN-consistency check.** F-025 verdict **AGREE** for the A-vs-B DTI value
  (B delegates to A; Read-confirmed, `03_consistency.md:1923-1925`); the
  co-displayed `total_debt` base inconsistency is a cross-ref to **F-008**
  (DIVERGE, UNKNOWN on Q-15); the Q-12 mortgage double-count is **UNKNOWN,
  blocked on Q-12**. The loose tests do not contradict F-025 (they simply do
  not pin the value).
- **Consistency-invariant test present?** **NO** (no A-vs-B delegate-equality
  numeric test; no co-displayed `total_debt`-vs-DTI-numerator base
  reconciliation).
- **Edge cases untested.** The hand-computable non-zero DTI value (the `~0.7%`
  the docstring derives but does not pin); A-vs-B delegate numeric equality
  (F-025); the F-008 co-displayed `total_debt` stored-vs-engine base
  inconsistency on the same card; the Q-12 obligations/DTI mortgage
  double-count.
- **Coverage verdict.** **LOOSE-ONLY.** No pinned non-trivial DTI value exists
  -- the only `==` assertion is the trivial zero-debt edge; the meaningful
  with-salary path asserts only `is not None`/`isinstance`. F-025 is AGREE so
  this is not `PINNED-AGAINST-DIVERGENT-BEHAVIOR`; the F-008 base inconsistency
  and the Q-12 double-count are additionally untested and escalated to
  Part 7.B.
- **Independent note.** P7-b. `test_dti_with_salary` carries a hand-computed
  `~0.7%` in its docstring but pins only `isinstance(..., Decimal)` -- a
  textbook `testing-standards.md` "loose where the value is hand-computable"
  miss; the arithmetic is sitting in the docstring unused.

---

### Slice-2 verdict roll-up

| Concept | Verdict | Governing F-NN / Q |
| --- | --- | --- |
| `monthly_payment` | COVERED (formula+edges single-call); F-013/F-026 cross-site+ARM-stability UNTESTED | F-013 DIVERGE; F-026 DIVERGE; E-02 |
| `loan_principal_real` | NO-PINNED-TEST (symptom-#3 target uncovered) | F-014 DIVERGE; Q-15 |
| `loan_principal_stored` | COVERED (manual-write column); F-015 + symptom-#3 UNTESTED | F-015 DIVERGE |
| `loan_principal_displayed` | PRODUCER-UNKNOWN-CANNOT-PIN | F-016 UNKNOWN; Q-11 |
| `principal_paid_per_period` | COVERED (engine + non-escrow B); F-017 escrow + A==B==C UNTESTED | F-017 DIVERGE |
| `interest_paid_per_period` | COVERED (engine per-row); F-018 raw-vs-A06 + year aggregate UNTESTED | F-018 DIVERGE; A-06 |
| `escrow_per_period` | COVERED (canonical producer pinned); E-16 template-arith cross-link | F-019 AGREE; E-16 |
| `total_interest` | COVERED (Def1 life-of-loan); Def2 + A-vs-C UNTESTED | F-020 DIVERGE; A-06 |
| `interest_saved` | COVERED (path A); F-021 banker's `loan.py:968` + A-vs-C UNTESTED | F-021 DIVERGE; A-01 |
| `months_saved` | COVERED (path A); F-022 definitional fork UNTESTED | F-022 DIVERGE; W-242 |
| `payoff_date` | COVERED (fixed-rate summary); F-023 A-vs-B/C + W-239 UNTESTED | F-023 DIVERGE; W-239 |
| `loan_remaining_months` | COVERED (pinned 300/0/360; contradicts PA-28 assumption) | F-024 AGREE |
| `dti_ratio` | LOOSE-ONLY (no pinned non-trivial value) | F-025 AGREE; F-008/Q-15; Q-12 |

Carried to Part 7.B (P7-e): the F-013 16-site `monthly_payment` equivalence
(D6-01) and the F-026 / E-02 ARM in-window stability (symptoms #2/#4); the
F-014 symptom-#3 strict-principal-decrease-per-settled-transfer regression
target (`05_symptoms.md:1714-1721`); F-015/F-016 stored-vs-engine-vs-displayed
reconciliation; F-017/F-018 A-06 escrow-subtraction divergences; F-020/F-021/
F-022/F-023 cross-definition + ROUNDING (`loan.py:968`) + strategy
reconciliations; the S6-03 savings-dashboard-vs-year-end dispatcher
equivalence and the F-008 co-displayed `total_debt` base (cross-linked from
`dti_ratio`). Part 7.B/7.C are out of P7-b scope; the 13 records above are the
P7-b deliverable.

## Part 7.A -- per-concept coverage census (slice 3)

Slice-3 family (P7-c): the 8 income / tax / paycheck concepts: `paycheck_gross`,
`paycheck_net`, `taxable_income`, `federal_tax`, `state_tax`, `fica`,
`pre_tax_deduction`, `post_tax_deduction`. Governing findings F-032..F-040
(`03_consistency.md:2700-3229`); the standards idioms a pinned tax test must
itself obey are E-10..E-17 (`00_priors.md:354-385`); the relationship invariant
is E-20-adjacent `net = gross - (federal+state+fica) - pre_tax - post_tax`
(catalog `02_concepts.md:1466-1480`, code form
`paycheck_calculator.py:223-231`). Two findings in this slice carry a live
`DIVERGE`: **F-032** (`paycheck_gross` -- the off-engine
`savings_dashboard_service` DTI income-denominator recompute drops
`_apply_raises` and uses banker's rounding; the canonical
`calculate_paycheck` gross is NOT divergent) and **F-037** (`fica` -- the
calibrated `apply_calibration` path silently bypasses the SS wage-base cap).
F-033/F-036/F-039 are AGREE; F-034/F-035/F-038 are AGREE for the primary path
with a Q-13-blocked calibrate_preview sub-pair; F-040 is DEAD_CODE.

### Cross-cutting absence / presence evidence (applies to concepts 1-8)

Explore swept `tests/` (one invocation per producer family:
`calculate_paycheck`/`_apply_raises`/`_get_cumulative_wages`,
`calculate_federal_withholding`/`calculate_state_tax`/`calculate_fica`/legacy
`calculate_federal_tax`, the calibration path, and a repo-wide
relationship/absence sweep). The main session Read every asserting line quoted
below at current source this session. Key cross-cutting results:

```
grep -rn "salary_gross_biweekly" tests/   -> 2 hits, BOTH fixture kwargs in
   tests/test_services/test_investment_projection.py (no assertion on the
   off-engine DTI gross base)
grep -rn "gross_monthly" tests/           -> tests/test_services/test_savings_dashboard_service.py
   :1300 (assertion) + :1360-1375 (standalone arithmetic, not the producer)
grep -rn "ss_wage_base|cumulative_wages|cumulative" tests/  -> all hits are
   BRACKET-path (calculate_fica direct / project_salary); 0 calibration-path
   cap tests (test_calibration_service.py, TestCalibrationIntegration: none
   thread cumulative wages over the cap with calibration active)
grep -rn "calculate_federal_tax" tests/   -> tests/test_services/test_tax_calculator.py
   only (TestLegacyWrapper :513-526; TestAnnualConsistency :554-599)
(net == gross - fed - state - ss - med - pre - post reconstruction) ->
   test_paycheck_calculator.py::test_net_pay_formula :504-516;
   ::test_26_period_annual_net_pay_sum :1337-1341 (omits pre/post, zero in
   fixture); test_year_end_summary_service.py::test_net_pay_consistency
   :539-548 (full section-7 form incl. pre/post, year-end aggregation layer)
(full-year 26-period sum, PA-20/PA-24) ->
   test_paycheck_calculator.py::test_26_period_annual_net_pay_sum :1285-1335;
   test_tax_calculator.py::test_26_period_annual_withholding_matches_annual_tax
   :581-596; test_year_end_summary_service.py::test_tax_breakdown :440-450
```

Two register assumptions are **contradicted by the live suite** and recorded as
Phase-7 findings-against-assumption (Phase-7 section-1 mandate): PA-20 ("no
full-year gross/net-pay exact-sum test") -- a pinned 26-period sum test EXISTS
(`test_26_period_annual_net_pay_sum`); PA-24 ("no test computes 26-period total
vs annual liability") -- a pinned 26-period-vs-annual reconciliation EXISTS
(`test_26_period_annual_withholding_matches_annual_tax`, asserting the exact
`$0.04` residue). The relationship-invariant tests are recorded explicitly per
the P7-c prompt; see each concept and the roll-up.

---

### Concept 1: `paycheck_gross`

- **Canonical producer.** `calculate_paycheck`@`paycheck_calculator.py:92`
  (post-raise biweekly gross @133-135), with `_apply_raises`@`:274` as the
  raise-sequencing sub-engine (`02_concepts.md:1412-1418`). **PRIMARY PATH
  known.** The off-engine `savings_dashboard_service.py:263-266`
  `salary_gross_biweekly` is explicitly NON-canonical (F-032 B path).
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_paycheck_calculator.py`):
  - `test_basic_paycheck_no_deductions` -- `455  assert result.gross_biweekly
    == Decimal("2307.69")` (60000/26; PINNED, Decimal-from-string E-11-clean).
  - `test_gross_biweekly_calculation` -- `528  assert result.gross_biweekly ==
    expected_gross` where `expected_gross = (Decimal("75000") /
    26).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` (PINNED-derived, pins the
    HALF_UP mode -- the exact axis F-032 B violates).
  - `test_zero_salary` -- `assert result.gross_biweekly == Decimal("0.00")`
    (zero-salary edge PINNED).
  - `test_26_period_annual_net_pay_sum` -- `1289  assert total_gross ==
    exp_gross * 26` with `exp_gross = Decimal("2307.69")` (full-year sum
    PINNED).
  - `test_calibration_does_not_bypass_gross_computation` -- `2850  assert
    cal_result.gross_biweekly == bracket_result.gross_biweekly` (calibration
    must not alter gross; equality-PINNED).
- **Relationship tests.** Gross is the lead term of the
  `net = gross - ...` invariant (see `paycheck_net`); no gross-specific
  inter-concept invariant beyond that.
- **Pinned / loose classification.** All canonical-gross assertions PINNED
  (exact string-`Decimal` or HALF_UP-derived).
- **E-NN-consistency check.** F-032 verdict **DIVERGE**
  (`03_consistency.md:2785-2790`) -- but the divergence is the **B path**
  (`savings_dashboard_service.py:263-266`: raw `annual_salary`, no
  `_apply_raises`, banker's-default quantize); the **A path** the pinned tests
  exercise (`calculate_paycheck` @133-135) is the E-NN-correct canonical
  (F-032: "A-vs-C AGREES by construction"; A is not divergent). The pinned
  values are A-path and HALF_UP-mode-correct -> **NOT**
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **NO** -- no test asserts the
  off-engine DTI gross base (`salary_gross_biweekly`) equals the canonical
  `calculate_paycheck` gross for the same profile WITH an applicable
  `SalaryRaise`. `test_dti_with_salary`
  (`test_savings_dashboard_service.py:1300`) pins `ds["gross_monthly_income"]
  == Decimal("6500.00")` but on a NO-RAISE profile (`annual_salary =
  Decimal("78000.00")`, no `SalaryRaise` added, Read this session
  `:1281-1300`), so off-engine == canonical there and F-032 is not exercised;
  the pinned $6,500.00 is correct under BOTH paths for a no-raise profile, so
  this is not pinned-against-divergent either -- it simply does not catch
  F-032.
- **Edge cases untested.** The F-032 raise-omission + banker's-rounding
  divergence on the DTI denominator (`savings_dashboard_service.py:263-266`
  with an applicable raise -- the worked example
  `03_consistency.md:2766-2784`, $8,666.67 vs the correct $8,926.67);
  `annual_salary=0`/negative and `pay_periods_per_year=0` `or 26` guard
  (PA-22, `paycheck_calculator.py:132,265,489`).
- **Coverage verdict.** **COVERED** for the canonical producer (>=1 pinned
  exact-`Decimal` test, HALF_UP-mode pinned, zero-salary edge pinned,
  E-NN/F-032-A-consistent). The F-032 **off-engine DTI-base divergence**
  (raise omission + banker's rounding) is **UNTESTED** and escalated to
  Part 7.B (`financial_calculation_audit_plan.md:696-704`). COVERED here means
  "canonical engine covered," NOT "the DTI income denominator has a regression
  test" -- it does not.
- **Independent note.** P7-c. The suite pins the canonical HALF_UP gross
  hard but has zero tests on the off-engine `salary_gross_biweekly` recompute
  that actually feeds the displayed DTI ratio; `test_dti_with_salary`'s
  no-raise fixture structurally cannot fail on F-032.

---

### Concept 2: `paycheck_net`

- **Canonical producer.** `calculate_paycheck`@`paycheck_calculator.py:92`
  (net @223-231); sole genuine producer, every consumer is a verified
  pass-through (`02_concepts.md:1499-1500`, F-033). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_paycheck_calculator.py`):
  - `test_basic_paycheck_no_deductions` -- `482  assert result.net_pay ==
    Decimal("1854.22")` (PINNED).
  - `test_net_pay_negative_from_excessive_post_tax` -- `assert result.net_pay
    == Decimal("-1044.03")`; `test_massive_deductions_exceed_gross` -- `assert
    result.net_pay == Decimal("-653.00")` (negative-net edge PINNED -- the
    overdraft-of-take-home edge).
  - `test_calibrated_paycheck_uses_override_rates` -- `2532  assert
    result.net_pay == expected_net` where `expected_net = Decimal("2307.69") -
    Decimal("230.77") - Decimal("115.38") - Decimal("143.08") -
    Decimal("33.46")` (calibrated-path net PINNED-derived).
- **Relationship tests** (explicitly, per the P7-c prompt -- the
  `net = gross - taxes - deductions` invariant):
  - `test_net_pay_formula` -- `499  assert r.net_pay == Decimal("1854.22")`
    AND `513  assert r.net_pay == expected_net` where `expected_net =
    (r.gross_biweekly - r.total_pre_tax - r.federal_tax - r.state_tax -
    r.social_security - r.medicare - r.total_post_tax).quantize(TWO_PLACES,
    rounding=ROUND_HALF_UP)` -- the **exact code-form section-7 invariant**
    with `fica` expanded to `ss + medicare` and the single terminal HALF_UP
    quantize (F-033 / `02_concepts.md:1479-1480`). PINNED.
  - `test_net_pay_end_to_end_with_pretax` -- `2392  assert r.net_pay ==
    expected_net` (same recomposition) AND `2395  assert r.net_pay ==
    Decimal("1683.22")` (formula + exact anchor; PINNED).
  - `test_26_period_annual_net_pay_sum` -- `1332  assert total_net == exp_net
    * 26` then `1338  assert total_net == (total_gross - total_federal -
    total_state - total_ss - total_medicare)` (full-year sum + cross-check;
    PINNED -- but the pre/post-tax terms are absent because the fixture has
    zero deductions, so it does not exercise the pre/post legs of the
    invariant).
  - `tests/test_services/test_year_end_summary_service.py::test_net_pay_consistency`
    -- `545  assert inc["net_pay_total"] == expected_net` where
    `expected_net = inc["gross_wages"] - total_taxes - inc["total_pretax"] -
    inc["total_posttax"]` (the **full** section-7 form including pre- and
    post-tax, at the year-end aggregation layer; PINNED-derived, equality
    invariant).
- **Pinned / loose classification.** All PINNED (exact string-`Decimal` or
  full-recomposition equality); the relationship invariant is pinned three
  independent ways (per-period, full-year, year-end aggregate).
- **E-NN-consistency check.** F-033 verdict **AGREE**
  (`03_consistency.md:2840-2842`); the invariant tests pin exactly the
  E-NN-consistent code form. Not divergent.
- **Consistency-invariant test present?** **YES** -- uniquely in this slice.
  The `net = gross - (fed+state+fica) - pre - post` invariant is asserted by
  `test_net_pay_formula:513`, `test_net_pay_end_to_end_with_pretax:2392`, and
  (full form incl. deductions) `test_net_pay_consistency:545`.
- **Edge cases untested.** Negative/zero salary feeds (`test_zero_salary`
  covers the zero case, Read this session `assert result.net_pay ==
  Decimal("0.00")`); `pay_periods_per_year=0` `or 26` net path (PA-22) not
  pinned for net specifically.
- **Coverage verdict.** **COVERED.** >=1 pinned exact-`Decimal` test, the
  relationship invariant pinned three ways, negative-net edge pinned,
  E-NN-consistent (F-033 AGREE). Records a **finding-against-assumption**:
  PA-20's "no full-year net-pay sum test" is contradicted -- the pinned
  `test_26_period_annual_net_pay_sum` exists (escalated to Part 7.B only as a
  register-correction, not a gap).
- **Independent note.** P7-c. This is the slice's only concept with a
  pinned consistency invariant; the gap is narrow (the 26-period cross-check
  drops the pre/post-tax legs because its fixture has zero deductions -- the
  year-end test covers the full form).

---

### Concept 3: `taxable_income`

- **Canonical producer.** For the DISPLAYED token:
  `calculate_paycheck`@`paycheck_calculator.py:155-157` (`gross_biweekly -
  total_pre_tax`, floor 0; `02_concepts.md:1568-1574`). The federal/state
  engine-internal taxables (D2/D3) are a different layer, not this token.
  **PRIMARY PATH known** for the display token.
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_paycheck_calculator.py`):
  - `test_taxable_income_floors_at_zero` -- `544  assert result.taxable_income
    == ZERO` (the floor-0 edge -- pre_tax > gross; PINNED to the specific
    edge value, satisfies the `testing-standards.md` edge rule).
  - `test_flat_pretax_deduction_reduces_federal_and_state` -- `assert
    with_ded.taxable_income == Decimal("2107.69")`;
    `test_percentage_pretax_deduction_reduces_taxes` -- `assert
    result.taxable_income == Decimal("2169.23")`;
    `test_multiple_pretax_deductions_stack` -- `assert result.taxable_income
    == Decimal("2007.69")` (PINNED -- pins `gross - pre_tax`).
  - `test_calibration_with_mixed_deductions` -- `assert result.taxable_income
    == Decimal("2107.69")`;
    `test_calibration_does_not_bypass_gross_computation` -- `2859  assert
    cal_result.taxable_income == bracket_result.taxable_income` (calibration
    must not alter the displayed taxable; equality-PINNED).
- **Relationship tests.** `taxable_income == gross_biweekly - total_pre_tax`
  (floor 0) is asserted indirectly by the pinned values above against their
  hand-computed gross/pre-tax; no single test names the identity as such.
- **Pinned / loose classification.** All PINNED (exact string-`Decimal`;
  floor-0 edge pinned to `ZERO`).
- **E-NN-consistency check.** F-034 verdict **AGREE** for the display token
  D1 (`03_consistency.md:2900-2906`); the pinned values are D1 (`gross -
  pre_tax`, floor 0), E-NN-consistent. **UNKNOWN** only for the
  D1-vs-`salary.calibrate_preview`-inline sub-pair (Q-13,
  `09_open_questions.md:514-559`); no pinned test pins the divergent inline
  value, so no `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **NO** -- no test asserts the
  displayed `taxable_income` equals the `salary.calibrate_preview` inline
  `actual_gross_pay - bk.total_pre_tax` for the same profile (the Q-13 seam,
  `salary.py:1095`); no test pins the legacy D4 vs D1 difference (-> F-040).
- **Edge cases untested.** The Q-13 calibrate_preview pct-base divergence
  (profile-gross-based `total_pre_tax` applied against `actual_gross_pay`);
  the federal-internal D2 Pub 15-T Steps 1-3 base exact value (PA-23, an
  engine-internal taxable, not this token but cross-linked).
- **Coverage verdict.** **COVERED** for the displayed token (>=1 pinned
  exact-`Decimal` test, floor-0 edge pinned, F-034-AGREE/E-NN-consistent). The
  Q-13 calibrate_preview sub-pair is **BLOCKED-ON-OPEN-QUESTION** (Q-13: no
  pinnable correct value until the developer decides the base) and is escalated
  to Part 7.B as an untested-and-unpinnable seam, not as a `COVERED` claim for
  that sub-path.
- **Independent note.** P7-c. The display-token primary path is well pinned;
  the gap is entirely the Q-13 calibrate_preview seam, which is correctly
  unpinnable (BLOCKED-ON-OPEN-QUESTION) rather than a missed pin.

---

### Concept 4: `federal_tax`

- **Canonical producer.** Bracket engine
  `calculate_federal_withholding`@`tax_calculator.py:35-170`, selected by
  `calculate_paycheck`@`paycheck_calculator.py:184-195`; calibrated override
  `apply_calibration`@`calibration_service.py:133-135` is a gated intentional
  alternative (`02_concepts.md:1635-1639`, F-035). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session):
  - `tests/test_services/test_paycheck_calculator.py::test_basic_paycheck_no_deductions`
    -- `461  assert result.federal_tax == Decimal("173.08")` (per-period
    withholding; PINNED, hand-derived from Pub 15-T in-test comment
    `459-460`).
  - `tests/test_services/test_tax_calculator.py`: `test_basic_biweekly_withholding`
    -- `141  assert result == Decimal("219.23")`;
    `test_income_spans_all_brackets` -- `259  assert result ==
    Decimal("6948.08")`; `test_very_high_income_top_bracket_only` -- `290
    assert result == Decimal("12978.85")`; `test_income_exactly_at_first_bracket_top`
    -- `317  assert result == Decimal("38.46")`;
    `test_income_one_dollar_into_next_bracket` -- `337  assert result ==
    Decimal("38.47")` (bracket-boundary edges PINNED); dependent-credit
    deltas `test_child_credits_reduce_tax` -- `assert diff ==
    Decimal("153.84")`, `test_other_dependent_credits` -- `assert diff ==
    Decimal("38.46")` (PINNED).
  - Calibrated: `test_basic_calibration_application` --
    `203  assert result["federal"] == Decimal("153.08")`;
    `test_calibrated_paycheck_uses_override_rates` -- `2520  assert
    result.federal_tax == Decimal("230.77")` (PINNED).
- **Relationship tests.** `test_26_period_annual_withholding_matches_annual_tax`
  (`test_tax_calculator.py`) -- `581  assert per_period ==
  Decimal("371.54")`, `585  assert annual_tax == Decimal("9660.00")`, `590
  assert annual_via_withholding == Decimal("9660.04")`, `596  assert
  annual_via_withholding - annual_tax == Decimal("0.04")` (the 26-period sum
  vs annual-liability reconciliation, residue PINNED exactly -- PA-24).
- **Pinned / loose classification.** The IRS Pub 15-T value tests are PINNED
  (exact string-`Decimal`). A minority are directional/LOOSE:
  `test_additional_income_increases_tax` -- `199  assert with_additional >
  base`; `test_additional_deductions_reduce_tax` -- `214  assert
  with_deductions < base`; `test_pre_tax_deductions_reduce_tax` -- `229
  assert with_pretax < base` (monotonicity only, LOOSE).
- **E-NN-consistency check.** F-035 verdict **AGREE** for the
  bracket-vs-calibrated pair (gated, mutually exclusive, by-design;
  `03_consistency.md:2957-2960`); pinned values are bracket-engine or
  calibrated-rate-correct, E-NN-consistent. **UNKNOWN** only for the
  A-vs-calibrate_preview Q-13 sub-pair. No pinned test pins a divergent value
  -> no `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **Partially.** The 26-period vs
  annual-liability reconciliation IS pinned
  (`test_26_period_annual_withholding_matches_annual_tax`). NO test asserts
  the calibrate_preview Q-13 effective-rate base equals the canonical taxable
  (the Q-13 seam) or pins legacy `calculate_federal_tax` against the canonical
  engine (-> F-040).
- **Edge cases untested.** The Q-13 calibrate_preview effective-rate base
  divergence (BLOCKED-ON-OPEN-QUESTION Q-13); `calculate_federal_withholding`
  negative-input guards (`gross_pay<0` `:91-92`, `pay_periods<=0` `:93-94`,
  negative dependents `:97-100`) -- TestInputValidation exists per the sweep
  but does not pin calculation output for these (PA-22).
- **Coverage verdict.** **COVERED** for the canonical bracket engine and the
  gated calibrated path (>=1 pinned exact-`Decimal` IRS Pub 15-T test,
  bracket-boundary edges pinned, F-035-AGREE/E-NN-consistent; the
  26-period-vs-annual reconciliation pinned). Records a
  **finding-against-assumption**: PA-23 ("seven tax tests use range/directional
  vs exact Decimal against Pub 15-T") is partially contradicted -- numerous
  exact-`Decimal` Pub 15-T pins exist; the directional tests
  (`:199,:214,:229`) are an additive monotonicity layer, not the only
  coverage. The Q-13 sub-pair is BLOCKED-ON-OPEN-QUESTION (escalated to
  Part 7.B).
- **Independent note.** P7-c. Legacy `calculate_federal_tax` is pinned by
  `test_tax_calculator.py::TestLegacyWrapper` (`518  assert result ==
  Decimal("5700.00")`, `:522`/`:526 == Decimal("0")`) against a definition
  that omits pre-tax and returns annual -- F-040 verdicts the function
  DEAD_CODE (not a live DIVERGE), so these are not
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR`; but if F-040 remediation deletes the
  function these pins break -- flagged for Part 7.B / Phase 8 (CLAUDE.md rule
  5 tension on dead-code deletion), cross-linked under F-040.

---

### Concept 5: `state_tax`

- **Canonical producer.** `calculate_state_tax`@`tax_calculator.py:240-268`
  (annual), de-annualized once by
  `calculate_paycheck`@`paycheck_calculator.py:202-204`; calibrated override
  `apply_calibration`@`calibration_service.py:136-138` gated
  (`02_concepts.md:1693-1695`, F-036). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session):
  - `test_paycheck_calculator.py::test_basic_paycheck_no_deductions` -- `467
    assert result.state_tax == Decimal("103.85")` (per-period, hand-derived
    `465-466`; PINNED).
  - `test_tax_calculator.py::test_zero_taxable_income` -- `719  assert state
    == Decimal("0.00")` (FLAT-rate config, zero income; edge PINNED).
  - Calibrated: `test_basic_calibration_application` -- `204  assert
    result["state"] == Decimal("94.85")`; `test_federal_and_state_use_taxable_not_gross`
    -- `249  assert result["state"] == Decimal("150.00")`;
    `test_rounding_to_two_decimal_places` -- `273  assert result["state"] ==
    Decimal("40.49")` (HALF_UP-mode pinned); `test_calibrated_paycheck_uses_override_rates`
    -- `2521  assert result.state_tax == Decimal("115.38")` (PINNED).
- **Relationship tests.** State is a term in the `paycheck_net` invariant
  (covered there) and the year-end `test_tax_breakdown` per-period-sum
  reconciliation (`test_year_end_summary_service.py:440-450`, `assert
  inc["state_tax"] == expected_state` where `expected_state = sum(bd.state_tax
  for bd in breakdowns)`; PINNED-derived).
- **Pinned / loose classification.** All PINNED (exact string-`Decimal`,
  HALF_UP-mode pinned at `:273`).
- **E-NN-consistency check.** F-036 verdict **AGREE**
  (`03_consistency.md:3005-3009`); single canonical engine, the annual ->
  per-period double-quantize is the documented PA-07 residue class, not a
  cross-producer divergence. Pinned values E-NN-consistent. Not divergent.
- **Consistency-invariant test present?** Partially -- year-end-vs-per-period
  state sum IS pinned (`test_tax_breakdown:440-450`). No
  bracket-vs-calibrated state equality test (by design, gated -- not a
  required invariant per F-036).
- **Edge cases untested.** The `tax_type_id == NONE` ref-id -> `Decimal("0")`
  path (`tax_calculator.py:257`, E-15 ID-based check) is **NOT tested** -- the
  only state test (`test_zero_taxable_income:719`) uses a FLAT-rate config
  with zero income, never the NONE-state branch (Explore B critical gap; grep
  confirmed no NONE/PROGRESSIVE state-config test). This is a specific,
  inventory-flagged edge with no test exercising the specific edge behavior
  (`testing-standards.md` edge rule).
- **Coverage verdict.** **COVERED** for the canonical engine and calibrated
  path (>=1 pinned exact-`Decimal` test, HALF_UP-mode pinned, year-end
  reconciliation pinned, F-036-AGREE/E-NN-consistent). The
  **`tax_type_id == NONE` -> 0 edge is UNTESTED** and escalated to Part 7.B as
  an edge-coverage gap (the displayed state line silently wrong for a
  no-income-tax state would ship undetected).
- **Independent note.** P7-c. The NONE-state branch
  (`tax_calculator.py:257`) is the single most common real-world
  configuration (9 US states have no wage income tax) and has zero test
  exercising it -- a high-blast-radius edge gap despite the primary path being
  well pinned.

---

### Concept 6: `fica`

- **Canonical producer.** `calculate_fica`@`tax_calculator.py:274-321`
  (SS cap-correct when fed cumulative wages), driven by
  `calculate_paycheck`@`paycheck_calculator.py:206-214` (the only path
  supplying cumulative wages); calibrated `apply_calibration` flavor drops the
  SS cap (`02_concepts.md:1761-1765`, F-037 DIVERGE). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_paycheck_calculator.py`):
  - `test_basic_paycheck_no_deductions` -- `472  assert
    result.social_security == Decimal("143.08")`, `477  assert result.medicare
    == Decimal("33.46")` (per-period, hand-derived `471`/`476`; PINNED).
  - SS wage-base cap, `TestFICADirectBoundary` (all three `calculate_fica`
    branches PINNED): `test_ss_at_cap_zero` -- `1109  assert result["ss"] ==
    Decimal("0.00")` (cumulative == cap); `test_ss_above_cap_zero` -- `1123
    assert result["ss"] == Decimal("0.00")` (cumulative > cap);
    `test_ss_partial_one_dollar_under` -- `1140  assert result["ss"] ==
    Decimal("0.06")` (partial-crossing); `test_ss_full_well_under_cap` --
    `1156  assert result["ss"] == Decimal("62.00")`; `test_ss_partial_straddle`
    -- `1173  assert result["ss"] == Decimal("37.20")`.
  - Full-year cap crossover: `test_fica_ss_wage_cap_boundary` -- 26-period
    `$200k` salary, per the sweep `assert results[i].social_security ==
    full_ss` (periods 1-21), `assert results[21].social_security ==
    partial_ss`, `assert results[i].social_security == Decimal("0.00")`
    (periods 23-26), and `assert total_ss == Decimal("10453.13")` (the
    `184500 * 0.062` annual cap total; PINNED).
  - Medicare surtax 26-period: `test_medicare_surtax_high_income` -- base
    `== Decimal("167.31")`, transition `== Decimal("236.54")`, full surtax
    `== Decimal("271.16")` (PINNED).
- **Relationship tests.** FICA (`ss + medicare`) is a term in the
  `paycheck_net` invariant (covered there) and the year-end `test_tax_breakdown`
  per-period-sum reconciliation (`assert inc["social_security_tax"] ==
  expected_ss` / `inc["medicare_tax"] == expected_medicare`; PINNED-derived).
- **Pinned / loose classification.** All bracket-path FICA assertions PINNED
  (exact string-`Decimal`; all three SS-cap branches + the full-year crossover
  + the Medicare surtax transition pinned).
- **E-NN-consistency check.** F-037 verdict **DIVERGE**
  (`03_consistency.md:3092-3097`): the calibrated path
  (`calibration_service.py:139-144`) has no `ss_wage_base`/`cumulative_wages`
  and never caps SS; worked example `03_consistency.md:3072-3091` -- a
  `$312,000` calibrated earner accrues `$19,344.00` SS vs the correct
  `$11,439.00`. The pinned tests all exercise the **bracket path**
  (`calculate_fica` / `project_salary`), where the cap IS enforced -- those
  pin the **E-NN-correct** value (cap = correct per the IRS hard invariant
  `02_concepts.md:1715-1719`). The calibrated `apply_calibration` tests
  (`test_calibration_service.py`: `test_basic_calibration_application:205
  assert result["ss"] == Decimal("143.08")`,
  `test_federal_and_state_use_taxable_not_gross:251 assert result["ss"] ==
  Decimal("248.00")`) pin the uncapped formula at single periods **well below
  the wage base**, where capped and uncapped agree -- they do NOT pin a
  divergent over-cap value, so they are **NOT**
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR` (they would not fail when F-037 is
  fixed). The F-037 divergence (calibrated SS past the cap for a high earner)
  is simply **never exercised**.
- **Consistency-invariant test present?** **NO** -- no test threads
  cumulative wages over `ss_wage_base` with calibration ACTIVE and asserts the
  calibrated SS caps (Explore C: grep for calibration-path cap tests EMPTY;
  every `ss_wage_base`/`cumulative_wages` hit is bracket-path). No
  calibrated-vs-bracket FICA equality test for an over-cap high earner.
- **Edge cases untested.** The F-037 calibration-path SS-cap bypass (the
  worked $312k example, periods after YTD wages exceed `ss_wage_base` with
  calibration active -- the headline gap); the Medicare-surtax interaction on
  the calibrated path (also uncapped/unthreaded).
- **Coverage verdict.** **COVERED** for the canonical bracket engine
  (>=1 pinned exact-`Decimal` test; all three SS-cap branches, the full-year
  crossover, and the Medicare surtax transition pinned; E-NN-consistent -- the
  cap is the correct IRS behavior). The **F-037 calibration-path SS-cap
  bypass** is **UNTESTED** and escalated to Part 7.B as the headline
  income/tax slice-3 gap (a calibrated high earner ships an overstated FICA
  line and understated net pay undetected;
  `financial_calculation_audit_plan.md:696-704`). COVERED means "bracket
  engine + cap edges covered," NOT "the F-037 DIVERGE has a regression test"
  -- it does not.
- **Independent note.** P7-c. The bracket-path SS cap is among the
  best-pinned calculations in the slice (5 direct-branch tests + a 26-period
  crossover + Medicare surtax), which makes the total absence of any
  calibration-path cap test the sharpest cross-cutting gap: the proven F-037
  DIVERGE has zero catching test on either axis.

---

### Concept 7: `pre_tax_deduction`

- **Canonical producer.** `_calculate_deductions`@`paycheck_calculator.py:403`
  invoked with the PRE_TAX timing id by `calculate_paycheck`@`:149` (single
  parameterized producer; `02_concepts.md:1814-1815`, F-038). **PRIMARY PATH
  known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_paycheck_calculator.py`):
  - `test_total_pre_tax_sums_deductions` -- `384  assert
    breakdown.total_pre_tax == Decimal("250.00")` (sum of two lines; PINNED).
  - `test_empty_deductions_return_zero` -- `416  assert breakdown.total_pre_tax
    == Decimal("0")` (empty edge PINNED).
  - `test_multiple_pretax_deductions_stack` -- `assert result.total_pre_tax ==
    Decimal("300.00")` (PINNED).
  - `test_percentage_deduction` -- `assert result.pre_tax_deductions[0].amount
    == expected` where `expected = (gross * Decimal("0.06")).quantize(
    TWO_PLACES, rounding=ROUND_HALF_UP)` (the pct branch + HALF_UP mode
    PINNED-derived).
- **Relationship tests.** The ordering invariant (pre-tax subtracted BEFORE
  taxable/tax) is asserted behaviorally by
  `test_percentage_pretax_deduction_reduces_taxes` (taxable and federal/state
  drop when a pre-tax deduction is added) and
  `test_third_paycheck_skipped_deduction_increases_taxes` (deduction absent ->
  taxes rise); `pre_tax_deduction` is also a term in the `paycheck_net`
  invariant.
- **Pinned / loose classification.** All PINNED (exact string-`Decimal` or
  HALF_UP-derived); the ordering invariant is asserted via pinned downstream
  tax values (behavioral, pinned).
- **E-NN-consistency check.** F-038 verdict **AGREE** for the canonical
  producer and the ordering invariant (`03_consistency.md:3143-3147`); pinned
  values E-NN-consistent. **UNKNOWN** only for the Q-13 pct-base sub-pair
  (profile-gross `_calculate_deductions:440` vs `actual_gross_pay`
  `salary.py:1095`). No pinned test pins a divergent value -> no
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **NO** for the Q-13 pct-base seam
  (no test asserts the profile-gross-based pct deduction equals the
  actual-gross-based one through calibrate_preview); the
  pre-tax-before-tax ordering IS asserted behaviorally (above).
- **Edge cases untested.** The Q-13 pct-base divergence
  (BLOCKED-ON-OPEN-QUESTION Q-13); pct-of-zero-gross
  (`_calculate_deductions:422-458`, PA-22) -- `test_inactive_deduction_skipped`
  (`assert len(result.pre_tax_deductions) == 0`) and
  `test_24_per_year_skipped_on_third_paycheck` (`assert len(result) == 0`)
  cover inactive/frequency-skip but not percentage-of-zero-gross.
- **Coverage verdict.** **COVERED** for the canonical producer and ordering
  invariant (>=1 pinned exact-`Decimal` test, empty edge pinned, HALF_UP-mode
  pinned, F-038-AGREE/E-NN-consistent). The Q-13 pct-base sub-pair is
  **BLOCKED-ON-OPEN-QUESTION** (Q-13) and escalated to Part 7.B; pct-of-zero-
  gross is an untested PA-22 edge (escalated).
- **Independent note.** P7-c. `pre_tax_deduction` and `post_tax_deduction`
  share `_calculate_deductions` (one parameterized core, distinguished by
  `timing_id`) -- DRY-correct per F-038/F-039; the producer-level coverage
  here also covers `post_tax_deduction`'s producer.

---

### Concept 8: `post_tax_deduction`

- **Canonical producer.** `_calculate_deductions`@`paycheck_calculator.py:403`
  invoked with the POST_TAX timing id by `calculate_paycheck`@`:217` (same
  parameterized core as `pre_tax_deduction`; `02_concepts.md:1854-1855`,
  F-039). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_paycheck_calculator.py`):
  - `test_total_post_tax_sums_deductions` -- `396  assert
    breakdown.total_post_tax == Decimal("125.00")` (PINNED).
  - `test_empty_deductions_return_zero` -- `417  assert
    breakdown.total_post_tax == Decimal("0")` (empty edge PINNED).
  - `test_mixed_pre_and_post_tax_deductions` -- `assert result.total_post_tax
    == Decimal("150.00")` (PINNED).
- **Relationship tests.** The ordering invariant (post-tax applied AFTER tax,
  never reduces `taxable_income`) is asserted by
  `test_post_tax_deduction_does_not_affect_any_tax`: federal `==
  Decimal("173.08")`, state `== Decimal("103.85")`, social_security `==
  Decimal("143.08")`, medicare `== Decimal("33.46")` (all unchanged by the
  post-tax deduction) and `assert post_ded.net_pay == no_ded.net_pay -
  Decimal("200.00")` (net reduced by exactly the post-tax amount; PINNED --
  this is the post-tax ordering invariant pinned directly). Also a term in the
  `paycheck_net` invariant.
- **Pinned / loose classification.** All PINNED (exact string-`Decimal`; the
  ordering invariant pinned via four unchanged-tax exact values + an exact
  net-delta).
- **E-NN-consistency check.** F-039 verdict **AGREE**
  (`03_consistency.md:3178-3181`); single canonical producer, ordering
  invariant holds, pinned values E-NN-consistent. Not divergent.
- **Consistency-invariant test present?** **YES** for the post-tax ordering
  invariant -- `test_post_tax_deduction_does_not_affect_any_tax` pins that
  post-tax leaves all four tax fields unchanged and reduces net by exactly the
  deduction (the definitional invariant `02_concepts.md:1839-1844`).
- **Edge cases untested.** pct-of-zero-gross post-tax
  (`_calculate_deductions:422-458`, PA-22); inactive/frequency-skip is covered
  by the shared-core tests under `pre_tax_deduction`.
- **Coverage verdict.** **COVERED.** >=1 pinned exact-`Decimal` test, empty
  edge pinned, the post-tax-after-tax ordering invariant pinned directly
  (four unchanged-tax exact values + exact net-delta), F-039-AGREE/
  E-NN-consistent.
- **Independent note.** P7-c. This concept has the cleanest coverage in the
  slice: the definitional ordering invariant is pinned directly rather than
  inferred; the only gap is the shared PA-22 pct-of-zero-gross edge.

---

### Slice-3 verdict roll-up

| Concept | Verdict | Governing F-NN / Q |
| --- | --- | --- |
| `paycheck_gross` | COVERED (canonical engine + HALF_UP + zero edge); F-032 off-engine DTI-base divergence UNTESTED | F-032 DIVERGE (B path); A/C AGREE; A-01 |
| `paycheck_net` | COVERED (relationship invariant pinned 3 ways); PA-20 assumption contradicted | F-033 AGREE; PA-20/PA-24 |
| `taxable_income` | COVERED (display token D1, floor-0 edge pinned); Q-13 calibrate_preview sub-pair BLOCKED-ON-OPEN-QUESTION | F-034 AGREE/UNKNOWN; Q-13 |
| `federal_tax` | COVERED (Pub 15-T + bracket-boundary + 26-vs-annual pinned); Q-13 sub-pair blocked; PA-23 assumption partially contradicted | F-035 AGREE/UNKNOWN; Q-13; F-040 |
| `state_tax` | COVERED (canonical + calibrated pinned); `tax_type_id == NONE -> 0` edge UNTESTED | F-036 AGREE; E-15 |
| `fica` | COVERED (bracket engine + all SS-cap branches + surtax pinned); F-037 calibration-path SS-cap bypass UNTESTED (headline gap) | F-037 DIVERGE; PA-21 |
| `pre_tax_deduction` | COVERED (producer + ordering pinned); Q-13 pct-base BLOCKED-ON-OPEN-QUESTION; pct-of-zero-gross edge UNTESTED | F-038 AGREE/UNKNOWN; Q-13 |
| `post_tax_deduction` | COVERED (producer + ordering invariant pinned directly) | F-039 AGREE |

Relationship-invariant tests (recorded explicitly per the P7-c prompt): the
`net = gross - (federal+state+fica) - pre_tax - post_tax` invariant IS pinned
-- per-period and full-year form (`test_paycheck_calculator.py::test_net_pay_formula:513`,
`::test_net_pay_end_to_end_with_pretax:2392`,
`::test_26_period_annual_net_pay_sum:1338` [drops pre/post legs, zero in
fixture]) and the full form including pre/post-tax at the year-end aggregation
layer (`test_year_end_summary_service.py::test_net_pay_consistency:545`). The
post-tax ordering invariant is pinned directly
(`test_post_tax_deduction_does_not_affect_any_tax`). This is the only slice in
which the headline relationship invariant has a pinned consistency test.

Carried to Part 7.B (P7-e): the **F-032** off-engine DTI income-denominator
divergence (no test exercises `salary_gross_biweekly` with an applicable
raise / banker's rounding); the **F-037** calibration-path SS-wage-base-cap
bypass (the headline slice-3 gap -- zero catching test on either axis); the
Q-13 calibrate_preview sub-pairs for `taxable_income` / `federal_tax` /
`pre_tax_deduction` (BLOCKED-ON-OPEN-QUESTION, unpinnable until Q-13 is
decided); the `state_tax` `tax_type_id == NONE -> 0` edge gap; the F-040 dead
legacy `calculate_federal_tax` pins (CLAUDE.md rule 5 deletion tension); the
PA-20 / PA-23 / PA-24 register assumptions contradicted by the live suite
(findings-against-assumption). Part 7.B/7.C are out of P7-c scope; the 8
records above are the P7-c deliverable.

## Part 7.A -- per-concept coverage census (slice 4)

Slice-4 family (P7-d): the 18 growth / retirement / transfer / goal /
year-summary concepts: `apy_interest`, `growth`, `employer_contribution`,
`contribution_limit_remaining`, `ytd_contributions`, `transfer_amount`,
`transfer_amount_computed`, `effective_amount`, `goal_progress`,
`emergency_fund_coverage_months`, `cash_runway_days`,
`pension_benefit_annual`, `pension_benefit_monthly`,
`year_summary_jan1_balance`, `year_summary_dec31_balance`,
`year_summary_principal_paid`, `year_summary_growth`,
`year_summary_employer_total`. No concept was deferred from slices 1-3 (the
P7-a/P7-b/P7-c roll-ups each give a verdict for all 8/13/8 of their concepts);
slice 4's 18 records complete the census. Governing findings F-027..F-031
(`03_consistency.md:2175-2520`) and F-041..F-056
(`03_consistency.md:3390-4210`); C3 CRITICAL pre-list
(`03_consistency.md:6062-6075`). Live `DIVERGE` in this slice: **F-042**
(`growth` -- the `compute_gap_data:220` `or "0.04"` SWR truthiness +
`compute_slider_defaults:321` zero-return-account exclusion; G1/G2 engine NOT
divergent), **F-043** (`employer_contribution` -- uncapped dashboard card
`investment.py:188` vs limit-capped chart/year-end `growth_engine.py:259-265`),
**F-027** (`effective_amount` -- label DIVERGE carrying the S1 entries-load
F-002/F-009 axis + the F-028 Q-08 cross-anchor inconsistency), **F-055**
(`year_summary_employer_total` -- inherits F-043 + Q-15). Q-15-gated UNKNOWN:
F-051/F-052/F-054-YG1. Q-08-gated UNKNOWN: F-028/F-046-GP2/F-056-`entry_remaining`.
AGREE: F-029, F-030, F-041, F-044, F-045, F-047, F-048, F-049, F-050,
F-053, F-054-YG2/YG3.

### Cross-cutting absence / presence evidence (applies to concepts 1-18)

Explore swept `tests/` (one invocation per producer family:
`calculate_interest`/`calculate_balances_with_interest`;
`project_balance`/`reverse_project_balance`/`calculate_employer_contribution`;
`compute_slider_defaults`/`compute_gap_data`/`safe_withdrawal_rate`;
`Transaction.effective_amount`/`Transfer.effective_amount`;
transfer-amount + shadow-invariant + `_compute_transfers_summary`;
`calculate_investment_inputs`/`ytd_contributions`/`contribution_limit`;
`create_payment_transfer`/`create_contribution_transfer`;
`savings_goal_service`/`calculate_savings_metrics`/`_compute_cash_runway`;
`calculate_benefit`; the `year_end_summary_service` year-summary producers).
The main session Read every load-bearing asserting line quoted below at
current source this session. Key cross-cutting results:

```
grep -rn "calculate_interest" tests/       -> 23 hits; pinned exact-Decimal
   throughout test_interest_projection.py + test_balance_calculator_hysa.py
grep -rn "compute_slider_defaults|safe_withdrawal_rate|compute_gap_data"
   tests/ -> hits exist; test_retirement_dashboard_service.py:188-189 pins
   ONLY slider["current_swr"]==Decimal("0.00") (the is-None-correct DISPLAY
   side); NO assertion on data["chart_data"]["investment_income"] or the
   gap-math swr (the compute_gap_data:220 `or "0.04"` divergent side)
grep -rn "_compute_cash_runway|cash_runway|runway" tests/ -> 2 assertions
   only: test_dashboard_service.py:422 (`is None`), :431 (`== 0`); ZERO
   pinned positive runway-day value (the int(bal/daily_avg) main formula)
grep -rn "create_payment_transfer|create_contribution_transfer" tests/ ->
   test_loan.py:1278 `default_amount > 0` (LOOSE); test_investment.py:715
   `default_amount == Decimal("269.23")` but "269.23" is POSTED in the
   request body (:699) -- pins user-supplied passthrough, NOT the
   route-resident limit/26 / $500-fallback / P&I+escrow derivation
grep -rn "contribution_limit_remaining|limit_info" tests/ -> only
   test_growth_engine.py:875-877 (the growth-engine ProjectionResult field,
   a DIFFERENT producer than the route-resident investment.py:173-181
   `limit - ytd` subtraction); ZERO route-render assertion
(year-end employer/growth dollar) -> test_year_end_summary_service.py
   employer_contributions/investment_growth asserted only `== ZERO` /
   `> ZERO` (LOOSE); no pinned non-zero year-end employer-total or
   investment-growth dollar
(PA-30 pension directional-only) -> CONTRADICTED: pinned exact pension
   dollars exist (test_pension_calculator.py:63 $38,387.50, :146 $606.80)
```

One register assumption is **contradicted by the live suite** and recorded as
a Phase-7 finding-against-assumption (section-1 mandate): **PA-30**
("two pension-calculator tests directional-only, annual_benefit precision
unverified") -- pinned exact-Decimal pension assertions EXIST
(`test_pension_calculator.py:63` `== Decimal("38387.50")`, `:146`
`== Decimal("606.80")`, `:64` `== Decimal("3198.96")`).

---

### Concept 1: `apy_interest`

- **Canonical producer.** `calculate_interest`@`interest_projection.py:49`
  -- THE single arithmetic engine; `calculate_balances_with_interest`
  @`balance_calculator.py:112` and `_compute_interest_for_year`
  @`year_end_summary_service.py:1207` delegate (`02_concepts.md:2016-2020`).
  **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_interest_projection.py`): `test_basic_14_day_period`
  `28  assert result == Decimal("17.27")`; `test_single_day_period`
  `41  assert result == Decimal("1.23")`; `test_30_day_period`
  `53  assert result == Decimal("37.05")`; `test_leap_year_february`
  `72  assert result == Decimal("16.04")`; monthly `106 == Decimal("16.94")`;
  quarterly `155 == Decimal("17.50")`; `test_unknown_frequency_returns_zero`
  `283  assert result == Decimal("0.00")` (PA-18 invalid-frequency branch).
  `tests/test_services/test_balance_calculator_hysa.py`:
  `test_hysa_basic_daily_compounding` `120  assert balances[1] ==
  Decimal("10017.27")` / `124  assert interest[1] == Decimal("17.27")`;
  `test_hysa_invalid_compounding_frequency` `670  assert interest[pid] ==
  Decimal("0.00")`. All exact string-`Decimal` -> PINNED.
- **Relationship tests.** `test_hysa_interest_compounds_across_periods`
  (`:175,184,193`) pins the period-to-period compounding chain
  (`17.27 -> 17.30 -> 17.33`) -- an inter-period invariant, PINNED.
- **Pinned / loose classification.** Engine + per-period wrapper assertions
  PINNED (exact string-`Decimal`).
- **E-NN-consistency check.** F-041 verdict **AGREE**
  (`03_consistency.md:3441`): single engine, all consumers delegate, the 365
  daily simplification uniform. The pinned values exercise the canonical
  engine -> E-NN-consistent; **not** `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **NO** -- the PA-19 invariant
  (`_compute_interest_for_year` annual aggregate == sum of per-period
  `calculate_interest` over the year) has no test; F-041 calls this an
  AGREE-by-construction equality, but no test pins the year-end-vs-per-period-
  sum reconciliation, and no single-page test asserts the
  `accounts/interest_detail.html` per-period column == the
  `savings/dashboard.html` interest figure for the same `(account, period)`.
- **Edge cases untested.** PA-19 26-period full-year compounding-accuracy
  reconciliation; the leap-year actual/365 overstatement is documented-
  accepted (F-041, not a gap). Invalid-frequency (PA-18) IS pinned (`:283`,
  `:670`).
- **Coverage verdict.** **COVERED** for the single canonical engine (>=1
  pinned exact-`Decimal` test, edges incl. invalid-frequency pinned, value
  F-041-AGREE-consistent). The PA-19 year-end-vs-per-period-sum consistency
  invariant is **UNTESTED** (carried to Part 7.B).
- **Independent note.** P7-d. The engine is the best-pinned producer in
  slice 4; the only gap is the absent aggregate-vs-per-period-sum
  reconciliation that F-041 declares true by construction but no test guards.

---

### Concept 2: `growth`

- **Canonical producer.** Token-overloaded (E2 split): **G1**
  `growth_engine.project_balance@:164`; **G2**
  `growth_engine.reverse_project_balance@:297`; **G3**
  `spending_trend_service._safe_pct_change@:470` (rate, not money); **G4**
  year-end aggregates (owned by F-053/F-054). The live DIVERGE sub-cluster:
  `compute_slider_defaults@retirement_dashboard_service.py:257` (display) vs
  `compute_gap_data@:217-221` (gap math). **PRIMARY PATH known per
  sub-concept** (`02_concepts.md:2081-2089`).
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_growth_engine.py`): G1 `test_basic_growth_no_contributions`
  `138  assert result[0].growth == Decimal("24.13")` / `141 ... end_balance
  == Decimal("10024.13")`; `test_with_periodic_contributions`
  `178/185/192` (`24.13/25.39/26.66`); `test_negative_return_rate`
  `318  assert result[0].growth == Decimal("-37.46")`. G2
  `test_zero_return_subtracts_contributions` `1038  assert
  reversed_proj[0].start_balance == Decimal("600.00")`. G3
  `tests/test_services/test_spending_trend_service.py` `test_regression_perfect_line`
  `136  assert slope == Decimal("10")` (regression slope PINNED) but
  `test_trend_increasing` `347  assert item.pct_change > Decimal("0")`
  (the percentage itself LOOSE). SWR slider:
  `tests/test_services/test_retirement_dashboard_service.py`
  `test_zero_swr_round_trips_as_decimal_zero` `189  assert
  slider["current_swr"] == Decimal("0.00")` (Read in full this session,
  `:163-189`).
- **Relationship tests.** None across the G1/G2/G3/G4 token (E2 mandates they
  are NOT numerically comparable; no test asserts a cross-sub-concept
  relationship, correctly).
- **Pinned / loose classification.** G1/G2 PINNED (exact string-`Decimal`);
  G3 regression slope/intercept PINNED but the spending-trend `pct_change`
  LOOSE (`>`, `<`, `>= Decimal("10")`, bool); SWR slider value PINNED.
- **E-NN-consistency check.** F-042 verdict **DIVERGE**
  (`03_consistency.md:3574`). The divergence is (a) `compute_gap_data:220`
  `or "0.04"` truthiness silently replacing an explicit-zero SWR with 4% in
  the gap math, and (b) `compute_slider_defaults:321` truthiness excluding a
  zero-return account from the weighted-return denominator. G1/G2 AGREE
  (single engine) -> the G1/G2 pinned values are E-NN-correct.
  `test_zero_swr_round_trips_as_decimal_zero:189` pins ONLY
  `slider["current_swr"] == Decimal("0.00")` -- the `compute_slider_defaults`
  `is None`-correct DISPLAY value, which is the E-NN-CORRECT side, not the
  divergent `compute_gap_data` 4% gap-math value. It is therefore **not**
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR`, but it also does **not** catch F-042:
  it never asserts `data["chart_data"]["investment_income"]` or the gap swr,
  so the slider-displays-0%-while-gap-uses-4% phantom-income divergence
  (C3 `03_consistency.md:6070`, $4,000/mo) ships undetected.
- **Consistency-invariant test present?** **NO** -- no test asserts
  `compute_slider_defaults` displayed SWR/return == the SWR/return actually
  driving `compute_gap_data`'s projected income for an explicit-zero SWR or a
  zero-return account.
- **Edge cases untested.** Explicit-zero stored `safe_withdrawal_rate` through
  the GAP path (only the slider path is pinned); a zero-`assumed_annual_return`
  account in the weighted-return average (`compute_slider_defaults:321`); G3
  `_safe_pct_change` exact percentage (only directional pinned).
- **Coverage verdict.** **COVERED** for the G1/G2 `growth_engine` money
  engine (>=1 pinned exact-`Decimal`, F-042-AGREE-consistent); **LOOSE-ONLY**
  for G3 spending-trend `pct_change`; the **F-042 SWR cross-anchor +
  zero-return-exclusion DIVERGE is UNTESTED** (the one pinned zero-SWR test
  asserts only the correct display side). Escalated to Part 7.B.
- **Independent note.** P7-d. `test_zero_swr_round_trips_as_decimal_zero` is
  the textbook "green bar that does not guard the divergence" -- it pins the
  symptom-free half of F-042 and structurally cannot fail on the phantom
  income.

---

### Concept 3: `employer_contribution`

- **Canonical producer.**
  `calculate_employer_contribution`@`growth_engine.py:91` -- the sole
  producer; `project_balance:265`, `investment.py:187` delegate
  (`02_concepts.md:2148-2151`). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_growth_engine.py`): `test_flat_percentage`
  `77  assert result == Decimal("125.00")`; `test_match_full`
  `89  assert result == Decimal("150.00")`; `test_match_partial`
  `101  assert result == Decimal("100.00")`; `test_none_type_returns_zero`
  `68 ... == ZERO`; `test_match_zero_employee` `112 ... == ZERO`;
  `test_none_params_returns_zero` `115 ... == ZERO`. In-loop via
  `project_balance`: `test_employer_flat_percentage`
  `240  assert result[0].employer_contribution == Decimal("125.00")`
  (Read `:228-240` this session); `test_employer_match`
  `257 ... == Decimal("150.00")` (Read `:242-257`). All PINNED.
- **Relationship tests.** None (employer math is a pure function; no
  inter-concept invariant beyond F-055's annual==per-period-sum, owned there).
- **Pinned / loose classification.** All branch assertions PINNED (exact
  string-`Decimal` / `ZERO` constant).
- **E-NN-consistency check.** F-043 verdict **DIVERGE**
  (`03_consistency.md:3670`): the dashboard card
  (`investment.py:185-189`, UNCAPPED `periodic_contribution`) vs the growth
  chart / year-end total (`growth_engine.py:259-265`, limit-CAPPED
  `contribution`). The pinned tests feed `calculate_employer_contribution`
  directly or via `project_balance` with a `periodic_contribution` well below
  any binding `annual_contribution_limit` (no `annual_contribution_limit`
  argument set in `test_employer_flat_percentage`/`test_employer_match`), so
  capped == uncapped in every fixture -> the engine values are
  E-NN-correct and **not** `PINNED-AGAINST-DIVERGENT-BEHAVIOR`; they simply
  never exercise F-043.
- **Consistency-invariant test present?** **NO** -- no test asserts the
  `investment.py:187` dashboard-card `employer_contribution_per_period` equals
  the `project_balance:265` chart employer line for a `match` employer on an
  account at/near its annual contribution limit (the F-043 worked example,
  `03_consistency.md:3651-3669`, card $240 vs chart $100).
- **Edge cases untested.** `match` employer in a limit-binding period (the
  F-043 / F-055 divergence); the `investment.py:188` route card path has no
  pinned test at all (only the engine).
- **Coverage verdict.** **COVERED** for the single canonical engine (>=1
  pinned exact-`Decimal` per branch, F-043-engine-consistent). The **F-043
  uncapped-card vs capped-chart DIVERGE is UNTESTED** -- escalated to
  Part 7.B (it is the same divergence F-055 inherits).
- **Independent note.** P7-d. The engine is well-pinned but the divergent
  surface (the route card) has zero pinned tests, so the $140/period
  overstatement near the limit is fully undetected.

---

### Concept 4: `contribution_limit_remaining`

- **Canonical producer.** Route-resident `investment.py:173-181`
  (`annual_contribution_limit - ytd_contributions`); **no service producer**
  by design (F-044 AGREE, structurally like `transfer_amount_computed`,
  `02_concepts.md:2181-2197`). **PRIMARY PATH known (route-resident).**
- **Pinned-value tests** (Read this session). The only pinned hits are
  `tests/test_services/test_growth_engine.py`
  `test_contribution_limit_remaining_reflects_actuals`
  `875  assert result[0].contribution_limit_remaining == Decimal("900")`
  / `876 ... == Decimal("700")` / `877 ... == Decimal("400")`. **This is a
  DIFFERENT producer**: `growth_engine.project_balance`'s in-loop
  `ProjectionResult.contribution_limit_remaining` field, NOT the route-
  resident `limit - ytd` subtraction at `investment.py:173-181` that this
  concept names. No test asserts the rendered `limit_info` remaining dollar
  on `investment/dashboard.html:76`.
- **Relationship tests.** None.
- **Pinned / loose classification.** The growth-engine field is PINNED but
  off-concept; the concept's actual route-resident producer has NO test.
- **E-NN-consistency check.** F-044 verdict **AGREE** (single-path,
  no recompute drift). No divergent value to pin against; the gap is absence,
  not contradiction.
- **Consistency-invariant test present?** **NO** -- nothing asserts the
  route-rendered remaining == `calculate_investment_inputs`'
  `annual_contribution_limit - ytd_contributions` for the dashboard.
- **Edge cases untested.** `annual_contribution_limit IS NULL` ("no limit")
  vs `> 0` (the E-28 0-vs-NULL idiom, `00_priors.md:320`); the
  `limit - ytd` subtraction going negative (over-contribution).
- **Coverage verdict.** **NO-PINNED-TEST** for the route-resident producer
  this concept names (the growth-engine `contribution_limit_remaining` field
  is a separate producer; pinning it does not cover the
  `investment.py:173-181` subtraction or its E-28 NULL/0 edges).
- **Independent note.** P7-d. A grep-name collision: a reader scanning for
  `contribution_limit_remaining` finds a pinned test and could wrongly
  conclude the concept is covered; the asserted field is the engine's
  internal cap-tracking, not the displayed "remaining" figure.

---

### Concept 5: `ytd_contributions`

- **Canonical producer.** `calculate_investment_inputs`
  @`investment_projection.py:100` Step-4 sum (`02_concepts.md:2214-2223`).
  **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_investment_projection.py`):
  `test_no_deductions_no_transfers` `83  assert result.ytd_contributions ==
  Decimal("0")`; `test_ytd_contributions_from_transfers`
  `200  assert result.ytd_contributions == Decimal("1500")`;
  `test_pre_filtered_contributions_only` `395 ... == Decimal("200")`;
  `test_empty_periods_none_current_period` `307 ... == Decimal("0")`;
  `test_none_current_period_with_contributions` `423 ... == Decimal("0")`.
  All exact string-`Decimal` -> PINNED.
- **Relationship tests.** None directly (it is the subtrahend of
  `contribution_limit_remaining`, untested there -- concept 4).
- **Pinned / loose classification.** PINNED (exact string-`Decimal`).
- **E-NN-consistency check.** F-045 verdict **AGREE** (sole producer, the
  `estimated_amount` bypass is the contract-safe F-027 S18 row gated by
  `status.excludes_from_balance`). The pinned values exercise the canonical
  producer -> E-NN-consistent; not `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **NO** consistency-across-pages
  test, but a single producer with a self-contained sum -- F-045's AGREE does
  not require one.
- **Edge cases untested.** The `status.excludes_from_balance` shadow filter
  honored (`:186`) -- `test_pre_filtered_contributions_only:395` exercises
  the pre-filter contract, so the contract-safe bypass IS covered.
- **Coverage verdict.** **COVERED** (>=1 pinned exact-`Decimal`, zero/None
  edges pinned, the pre-filter contract pinned, value F-045-AGREE-consistent).
- **Independent note.** P7-d. Well-pinned single-path producer; no gap.

---

### Concept 6: `transfer_amount`

- **Canonical producer.** Stored `Transfer.amount`@`transfer.py:142`,
  mutated EXCLUSIVELY by `transfer_service.create_transfer/update_transfer/
  restore_transfer` (Invariant 4); canonical read
  `Transfer.effective_amount`@`transfer.py:174-182`
  (`02_concepts.md:2257-2260`). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session). `Transfer.effective_amount`
  2-tier: `tests/test_audit_fixes.py` `test_transfer_cancelled_returns_decimal`
  `172-173  assert isinstance(...) ; assert xfer.effective_amount ==
  Decimal("0")`; `test_transfer_active_returns_decimal` `193-194 ... ==
  Decimal("200.00")`. Year-end per-destination total
  `tests/test_services/test_year_end_summary_service.py`
  `test_transfers_grouped_by_destination` `932  assert
  by_name["Savings"]["total_amount"] == Decimal("400.00")` / `933 ...
  ["Mortgage"]["total_amount"] == Decimal("1500.00")`. All PINNED.
- **Relationship tests (Invariant 3, shadow == parent).**
  `tests/test_integration/test_loan_payment_pipeline.py`
  `test_full_payment_pipeline`: `125  assert len(pair) == 2`,
  `137  assert len(amounts) == 1` (both shadows share one amount),
  `143  assert len(statuses) == 1`, `150  assert len(period_ids) == 1`
  -- the shadow-equals-parent invariant pinned as a set-collapse. PINNED.
- **Pinned / loose classification.** `effective_amount` + year-end total
  PINNED; the Invariant-3 set-collapse PINNED (asserts the two shadows are
  identical, the F-029 substrate).
- **E-NN-consistency check.** F-029 verdict **AGREE**
  (`03_consistency.md:2425`): A/B/C/D all trace to one stored `Transfer.amount`;
  no double-count. Pinned values exercise the canonical column/read ->
  E-NN-consistent; not `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **YES** -- the Invariant-3
  shadow-amount-collapse (`test_full_payment_pipeline:137`) is exactly the
  cross-producer consistency test F-029 rests on (shadow `effective_amount`
  pair == parent). This is the rare slice-4 concept with a pinned
  consistency invariant.
- **Edge cases untested.** PA-25 zero-amount / self-transfer / max-amount
  transfer-recurrence boundaries (`02_concepts.md:2270-2272`) -- not pinned;
  the year-end-vs-shadow double-count is structurally impossible (F-029) so
  no test needed there.
- **Coverage verdict.** **COVERED** (>=1 pinned exact-`Decimal`, the
  Invariant-3 consistency invariant pinned, value F-029-AGREE-consistent).
- **Independent note.** P7-d. The only slice-4 concept whose cross-producer
  consistency invariant is actually pinned (via the loan-payment-pipeline
  shadow-collapse assertions).

---

### Concept 7: `transfer_amount_computed`

- **Canonical producer.** Route-resident BY DESIGN, no service producer:
  `loan.create_payment_transfer`@`loan.py:1213-1241` (P&I + escrow) and
  `investment.create_contribution_transfer`@`investment.py:668-670`
  (`annual_contribution_limit/26`, `$500` fallback)
  (`02_concepts.md:2535-2545`). **PRIMARY PATH known (route-resident).**
- **Pinned-value tests** (Read this session,
  `tests/test_routes/test_investment.py:690-716`):
  `test_create_transfer_success` `715  assert tpl.default_amount ==
  Decimal("269.23")` -- but `"269.23"` is the value POSTED in the request
  body (`:699  "amount": "269.23"`), so this pins that a USER-SUPPLIED
  amount round-trips to the `TransferTemplate`, NOT the route-resident
  `limit/26` / `$500` fallback DERIVATION. `tests/test_routes/test_loan.py`
  `test_create_transfer_success` `1278  assert tpl.default_amount > 0`
  (LOOSE). `test_create_transfer_amount_override` (loan `1401`, investment
  `849`) likewise pin POSTED override values, not the computed pre-fill.
- **Relationship tests.** None (F-030 says the pre-fill should equal the
  loan dashboard `monthly_payment + escrow_per_period` / the investment
  `limit/26`; no test asserts that equality).
- **Pinned / loose classification.** The `$269.23` assertion is PINNED but
  pins user-supplied passthrough; the computed-derivation path is LOOSE
  (`> 0`) or absent.
- **E-NN-consistency check.** F-030 verdict **AGREE** (single-path,
  route-resident; reads the same producers as `monthly_payment`/
  `escrow_per_period`/`annual_contribution_limit`; inherits F-013 ARM
  `monthly_payment` input risk cross-link only). No divergent value to pin
  against; the gap is absence.
- **Consistency-invariant test present?** **NO** -- nothing asserts the
  pre-filled amount == the dashboard's displayed P&I+escrow (loan) or
  `calculate_investment_inputs`' `annual_contribution_limit/26` (investment).
- **Edge cases untested.** The `$500` fallback when no
  `annual_contribution_limit` is set; the loan P&I+escrow derivation value
  (`loan.py:1225-1229`, the F-013 site-14 ARM P&I substrate); user-omits-
  override (the actual pre-fill branch).
- **Coverage verdict.** **NO-PINNED-TEST** for the route-resident computed
  derivation this concept names (the `$269.23` test pins a posted value, not
  the `limit/26` / `$500` / P&I+escrow computation; the loan path is `> 0`
  LOOSE).
- **Independent note.** P7-d. Same grep-collision hazard as concept 4: a
  pinned `default_amount == Decimal("269.23")` looks like coverage but the
  number came from the test's own POST body.

---

### Concept 8: `effective_amount`

- **Canonical producer.** `Transaction.effective_amount`
  @`transaction.py:221-245` (4-tier: is_deleted->0;
  excludes_from_balance->0; actual if not None; else estimated) -- the
  explicitly designated single source of truth;
  `Transfer.effective_amount`@`transfer.py:174-182` (2-tier)
  (`02_concepts.md:2340-2344`). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session). 4-tier on `Transaction`:
  `tests/test_ref_cache.py`
  `test_effective_amount_uses_actual_for_settled_status`
  `168  assert txn.effective_amount == Decimal("487.00")` (tier-3 actual;
  Read `:160-168`); `test_effective_amount_uses_estimated_for_projected`
  `194  assert txn.effective_amount == Decimal("500.00")` (tier-4 estimated;
  Read `:170-194`); `test_effective_amount_returns_zero_for_excluded_status`
  `139  ... == Decimal("0")` (tier-2). `tests/test_audit_fixes.py:131,152`
  Credit/Cancelled `== Decimal("0")`. `tests/test_adversarial/test_hostile_qa.py`
  many status-transition pins (`:189,204,224,256,297,318,365,414,437`).
  Transfer 2-tier: `test_audit_fixes.py:172-173,193-194`. All PINNED.
- **Relationship tests.** None at the property level; the entries-aware
  S1 path (`balance_calculator._entry_aware_amount`) feeds the F-002/F-009
  axis (slice-1 concern, cross-ref, not re-derived).
- **Pinned / loose classification.** All four tiers PINNED (exact
  string-`Decimal`), incl. tier-1/2 zero and the actual-vs-estimated split.
- **E-NN-consistency check.** F-027 verdict **DIVERGE** label
  (`03_consistency.md:2260-2266`) -- but the DIVERGE is carried by S1
  (entries-load -> F-002/F-009, not re-verdicted there) and F-028 (the Q-08
  entry-progress cross-anchor). The PROPERTY itself is the canonical correct
  producer (F-027: "no NEW silent balance-bypass exists"; every hand-rolled
  mirror is tier-1/2-guarded). The pinned tier tests exercise the property
  -> E-NN-correct, **not** `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **NO** -- no test asserts a
  bypass site (e.g. the `_transaction_cell.html:17` 2-tier mirror, the
  `budget_variance_service:390-393` hand-rolled mirror) equals
  `Transaction.effective_amount` across all is_deleted/excludes_from_balance
  combinations; and no test asserts the F-028 cross-anchor consistency
  (bill `amount`=`effective_amount` vs `entry_remaining`=`estimated_amount`).
- **Edge cases untested.** The F-028/Q-08 estimated-vs-actual entry-progress
  base for a settled entry-tracked txn (BLOCKED on Q-08); the four hand-rolled
  2-tier mirrors omitting tiers 1-2 (F-027 S10/S14/T1/T4).
- **Coverage verdict.** **COVERED** for the canonical 4-tier property (>=1
  pinned exact-`Decimal` per tier, value F-027-property-correct). The
  bypass-equivalence sweep and the **F-028 cross-anchor inconsistency are
  UNTESTED**; the Q-08 estimated/actual sub-axis is
  **BLOCKED-ON-OPEN-QUESTION (Q-08)**. COVERED here = "the property is
  pinned," NOT "every bypass mirror is proven equivalent."
- **Independent note.** P7-d. The property is the best-pinned model accessor
  in the suite; the audit risk is entirely on the ~43 bypass sites and the
  Q-08 entry-progress base, none of which has an equivalence test.

---

### Concept 9: `goal_progress`

- **Canonical producer.** Token-overloaded (E2): **GP1** savings-goal
  completion -- `savings_goal_service` (`resolve_goal_target@:21`,
  `calculate_required_contribution@:109`, `calculate_trajectory@:331`,
  `_compute_required_monthly@:431` ROUND_CEILING); **GP2** entry-tracked
  spend pct -- `dashboard_service._entry_progress_fields@:203` /
  `companion.py:53-56` (`02_concepts.md:2452-2459`). **PRIMARY PATH known
  per sub-concept**; GP2 base is Q-08-governed.
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_savings_goal_service.py`): GP1
  `test_resolve_fixed_goal` `316  assert result == Decimal("5000.00")`;
  `test_resolve_income_relative_paychecks` `349 ... == Decimal("6000.00")`;
  `test_gap_exists_returns_per_period_amount`
  `40  assert result == Decimal("200.00")`;
  `test_decimal_precision_round_half_up` `77 ... == Decimal("33.33")`;
  `test_trajectory_on_track` `518  assert result["required_monthly"] ==
  Decimal("500.00")`; `test_required_monthly_rounds_up`
  `693 ... == Decimal("1666.67")` (the ROUND_CEILING site);
  `test_trajectory_ahead` `576 ... == Decimal("83.34")`. GP2
  `tests/test_routes/test_dashboard_entries.py`
  `test_bill_dict_entry_fields_tracked` `322  assert bill["entry_remaining"]
  == Decimal("170.00")`; `test_bill_dict_over_budget_flag_and_negative_remaining`
  `394 ... == Decimal("-50.00")`; `tests/test_services/test_entry_service.py`
  `test_compute_remaining_under_budget` `967  assert remaining ==
  Decimal("170.00")`. All PINNED.
- **Relationship tests.** None across GP1/GP2 (E2: not numerically
  comparable -- correctly no cross-sub-concept test).
- **Pinned / loose classification.** GP1 + GP2 entry-remaining PINNED
  (exact string-`Decimal`); the `companion.py:54` `float` pct is an E-10
  display concern (no pinned pct found, the assertions are on
  `entry_remaining`/`entry_over_budget`).
- **E-NN-consistency check.** F-046: **GP1 AGREE** (single canonical
  `savings_goal_service` producers; ROUND_CEILING documented-intentional and
  pinned at `:693`); **GP2 UNKNOWN** blocked **Q-08**
  (`03_consistency.md:3780-3783`). The GP2 `entry_remaining` pins
  (`170.00`, `-50.00`, `0.00`) lock the CURRENT estimated-base behavior
  (interpretation (1) "what you allocated"); since Q-08 is unresolved this is
  not yet `PINNED-AGAINST-DIVERGENT-BEHAVIOR`, but the pins WILL conflict
  with interpretation (2) if the developer chooses it -- flagged.
- **Consistency-invariant test present?** **NO** -- no test asserts
  `_entry_progress_fields` (`dashboard_service.py:203`) == the
  `companion.py:53-56` inline pct for the same txn (the F-046 GP2 duplicate-
  impl pair); they happen to agree by construction but nothing pins it.
- **Edge cases untested.** GP1: none material (ROUND_CEILING pinned). GP2:
  the settled entry-tracked txn estimated-vs-actual base (Q-08 / F-028
  worked example, `03_consistency.md:3774-3777`).
- **Coverage verdict.** **GP1 COVERED** (>=1 pinned exact-`Decimal`,
  ROUND_CEILING pinned, F-046-AGREE-consistent). **GP2
  BLOCKED-ON-OPEN-QUESTION (Q-08)** -- pinned, but the pins lock the
  estimated base before Q-08 is decided (a pre-emptive lock-in risk,
  carried to Part 7.B).
- **Independent note.** P7-d. GP1 is the most thoroughly pinned producer
  family in slice 4 (trajectory ETA, pace, required-monthly all exact);
  GP2's pins are a latent Q-08 hazard, not current coverage failure.

---

### Concept 10: `emergency_fund_coverage_months`

- **Canonical producer.** `calculate_savings_metrics`
  @`savings_goal_service.py:139-175` -- sole producer
  (`02_concepts.md:2479-2487`). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_savings_goal_service.py`):
  `test_returns_months_paychecks_years` `92  assert
  result["months_covered"] == Decimal("6.0")` / `93 ...
  ["paychecks_covered"] == Decimal("13.0")` / `94 ... ["years_covered"]
  == Decimal("0.5")`; `test_paychecks_formula` `103 ... == Decimal("17.3")`
  / `104 ... ["months_covered"] == Decimal("8.0")`; `test_years_formula`
  `113/114` (`36.0`/`3.0`); `test_expenses_zero_returns_all_zeros`
  `122-124` (`Decimal("0")` x3); `test_balance_zero_returns_all_zeros`
  `142-144`. All PINNED.
- **Relationship tests.** `test_paychecks_formula` /
  `test_years_formula` pin `paychecks_covered = months * 26/12` and
  `years_covered = months / 12` against the same `months` -- the F-047
  internal-consistency invariant, PINNED.
- **Pinned / loose classification.** All three derived figures PINNED
  (exact string-`Decimal`, incl. the zero-expense and zero-balance edges).
- **E-NN-consistency check.** F-047 verdict **AGREE** (sole producer; the
  three figures internally consistent against one `months`). Pinned values
  exercise the canonical producer -> E-NN-consistent; not
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **YES** -- the months/paychecks/
  years internal-consistency invariant is pinned
  (`test_paychecks_formula:103-104`, `test_years_formula:113-114`).
- **Edge cases untested.** Zero/None expenses AND zero balance both pinned;
  the `26/12` duplication vs `savings_dashboard_service.py:170-172,765` is a
  Q-12 DRY cross-link, not a value gap.
- **Coverage verdict.** **COVERED** (>=1 pinned exact-`Decimal`, internal
  consistency invariant pinned, zero edges pinned, F-047-AGREE-consistent).
- **Independent note.** P7-d. Fully pinned single-path producer with its
  internal invariant guarded; no gap.

---

### Concept 11: `cash_runway_days`

- **Canonical producer.** `_compute_cash_runway`
  @`dashboard_service.py:375-417` -- sole producer
  (`02_concepts.md:2509-2515`). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_dashboard_service.py:415-431`):
  `test_cash_runway_zero_spending` `422  assert
  result["cash_runway_days"] is None` (no recent spend -> None);
  `test_cash_runway_negative_balance` `431  assert
  result["cash_runway_days"] == 0` (balance <= 0 -> 0). The `is None`
  is LOOSE; the `== 0` is a pinned scalar but it is the negative-balance
  GUARD branch, not the formula.
- **Relationship tests.** F-048's single-path verify (the
  `current_balance` input == the dashboard `checking_balance` for the same
  period) -- NO test asserts this equality.
- **Pinned / loose classification.** Only the two edge guards covered
  (`is None`; `== 0`); the main `int((current_balance/daily_avg).quantize(1,
  HALF_UP))` formula (`:415-417`) has **no pinned positive runway value**.
- **E-NN-consistency check.** F-048 verdict **AGREE** (sole producer; uses
  `txn.effective_amount` correctly; balance base byte-identical to the
  dashboard card). No divergent value; the gap is absence of the formula
  test.
- **Consistency-invariant test present?** **NO** -- nothing asserts the
  runway's `current_balance` base == the dashboard `checking_balance` card
  for the same `(account, current_period)` (F-048's mandated single-path
  verify).
- **Edge cases untested.** The main trailing-30-day burn-rate division (a
  positive day count); the `current_balance`-vs-`checking_balance` agreement.
  The None (no-spend) and 0 (negative-balance) guards ARE covered.
- **Coverage verdict.** **NO-PINNED-TEST** for the main
  `int(balance/daily_avg)` formula (only the None / 0 edge guards are
  asserted; the day-count value and the balance-base agreement are unpinned).
- **Independent note.** P7-d. Both existing tests are guard-branch tests;
  the actual runway arithmetic ships with zero value coverage.

---

### Concept 12: `pension_benefit_annual`

- **Canonical producer.** `calculate_benefit`@`pension_calculator.py:31-75`
  -- sole producer (`annual_benefit@:61-63`) (`02_concepts.md:2564-2569`).
  **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_pension_calculator.py`): `test_basic_benefit`
  `60  assert result.years_of_service == Decimal("25.00")` / `61 ...
  high_salary_average == Decimal("83000.00")` / `63 ... annual_benefit ==
  Decimal("38387.50")`; `test_high_salary_average_correct_window`
  `83 ... == Decimal("85000.00")`; `test_fewer_years_than_window`
  `98 ... == Decimal("82500.00")`; `test_very_short_service`
  `146  assert result.annual_benefit == Decimal("606.80")`. All PINNED.
  `test_empty_salary_projections` `109 ... == ZERO` (no-history edge).
- **Relationship tests.** Component invariant pinned: `test_basic_benefit`
  pins `years_of_service`, `high_salary_average` AND `annual_benefit`
  together (`60/61/63`), so `annual = multiplier * years * high_avg` is
  effectively pinned end-to-end.
- **Pinned / loose classification.** PINNED (exact string-`Decimal`); the
  no-history `== ZERO` is the documented zero edge.
- **E-NN-consistency check.** F-049 verdict **AGREE** (sole producer; the
  salary projection delegates to the same `_apply_raises` engine as
  `paycheck_gross`, F-032 cross-ref). Pinned values exercise the canonical
  producer -> E-NN-consistent; not `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **NO** cross-page test, but a
  single producer with consumer pass-throughs (F-049) -- not required.
- **Edge cases untested.** No-salary-history pinned (`:109`); the
  `_apply_raises` propagation correctness is owned by F-032 (slice-3 concern,
  cross-ref).
- **Coverage verdict.** **COVERED** (>=1 pinned exact-`Decimal`, component
  invariant pinned, no-history edge pinned, F-049-AGREE-consistent).
  **PA-30 register assumption contradicted** -- pinned exact pension
  dollars exist (`:63`, `:146`), not directional-only.
- **Independent note.** P7-d. PA-30's "directional-only" claim is stale;
  the producer is exact-pinned including the short-service edge.

---

### Concept 13: `pension_benefit_monthly`

- **Canonical producer.** Same `calculate_benefit`@`pension_calculator.py:31`
  (`monthly_benefit@:65-67` = `annual / 12`) (`02_concepts.md:2586-2594`).
  **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_pension_calculator.py`): `test_basic_benefit`
  `64  assert result.monthly_benefit == Decimal("3198.96")`;
  `test_very_short_service` `149 ... == Decimal("50.57")`. PINNED.
- **Relationship tests.** `test_monthly_is_annual_divided_by_12`
  (Read `:112-124` this session): `122  expected_annual = Decimal("0.02") *
  Decimal("20.00") * Decimal("100000")`; `123  expected_monthly =
  (expected_annual / 12).quantize(Decimal("0.01"))`; `124  assert
  result.monthly_benefit == expected_monthly` -- the `monthly == annual/12`
  invariant pinned with the hand-computed expectation. PINNED relationship.
- **Pinned / loose classification.** Value + relationship PINNED (exact
  string-`Decimal`).
- **E-NN-consistency check.** F-050 verdict **AGREE** (computed in the same
  call as `pension_benefit_annual`; `monthly == annual/12` by construction).
  The relationship test pins exactly that -> E-NN-consistent; not
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
- **Consistency-invariant test present?** **YES** --
  `test_monthly_is_annual_divided_by_12:124` pins the F-050 governing
  relationship (`monthly == (annual/12).quantize`).
- **Edge cases untested.** None material (no-history zero shared with
  concept 12, pinned at `:110`).
- **Coverage verdict.** **COVERED** (>=1 pinned exact-`Decimal`, the
  `monthly == annual/12` relationship invariant pinned, F-050-AGREE-
  consistent).
- **Independent note.** P7-d. One of only three slice-4 concepts whose
  governing relationship invariant is actually pinned (with
  `emergency_fund_coverage_months` and `transfer_amount`).

---

### Concept 14: `year_summary_jan1_balance`

- **Canonical producer.** `_compute_net_worth`
  @`year_end_summary_service.py:689` (the `jan1` endpoint) -- the same
  producer and per-account dispatch as P2-a `net_worth`; the cross-account
  dispatcher (`_get_account_balance_map` vs `_compute_account_projections`)
  is **Q-15** (`02_concepts.md:2616-2620`). **PRIMARY known; dispatcher
  Q-15-gated.**
- **Pinned-value tests** (Read this session). Explore returned NO pinned
  `jan1` dollar assertion. The only related assertion is
  `tests/test_services/test_year_end_summary_service.py`
  `test_net_worth_jan_dec_delta` (Read `:993-998`): `998  assert nw["delta"]
  == nw["dec31"] - nw["jan1"]` -- a tautological internal-consistency check
  on one producer's three outputs, NOT a pinned `jan1` value and NOT a
  cross-dispatcher consistency test.
- **Relationship tests.** `:998` is the `delta == dec31 - jan1`
  relationship -- PINNED but self-referential (does not constrain `jan1`).
- **Pinned / loose classification.** No pinned `jan1` value; the delta
  relationship is structurally trivial (a == b - c where all three come
  from the same dict).
- **E-NN-consistency check.** F-051 verdict **UNKNOWN** -- blocked **Q-15**
  (`03_consistency.md:3895-3902`; same dispatcher question as F-006
  `net_worth`, cross-referenced not re-derived). No pinned value to classify
  against; the producer's canonicality is itself the open question.
- **Consistency-invariant test present?** **NO** -- nothing asserts the
  net-worth Jan-1 dispatch (`_get_account_balance_map@:750`) == the
  savings-progress Jan-1 (`_compute_account_projections@savings_dashboard_
  service.py:294`) for the same account (W-152/W-159, Q-15).
- **Edge cases untested.** Anchor-None; the dual-dispatcher divergence
  (F-006, inherited); investment Jan-1 cross-path equality.
- **Coverage verdict.** **BLOCKED-ON-OPEN-QUESTION (Q-15)**, compounded by
  **NO-PINNED-TEST** for the `jan1` dollar value (the delta relationship is
  the only assertion and it does not pin `jan1`).
- **Independent note.** P7-d. `test_net_worth_jan_dec_delta` is a
  green-bar-without-coverage: it can never fail on a wrong `jan1` because it
  only checks `jan1` against the same producer's own `dec31`/`delta`.

---

### Concept 15: `year_summary_dec31_balance`

- **Canonical producer.** Same `_compute_net_worth`
  @`year_end_summary_service.py:689` (the `dec31` endpoint); cross-account
  dispatcher = **Q-15** (`02_concepts.md:2639-2644`). **PRIMARY known;
  dispatcher Q-15-gated.**
- **Pinned-value tests** (Read this session). NO pinned `dec31` dollar
  assertion (Explore sweep). Same `test_net_worth_jan_dec_delta:998`
  (`assert nw["delta"] == nw["dec31"] - nw["jan1"]`) as concept 14 -- does
  not pin `dec31`.
- **Relationship tests.** `:998` only (self-referential, as concept 14).
- **Pinned / loose classification.** No pinned `dec31` value.
- **E-NN-consistency check.** F-052 verdict **UNKNOWN** -- blocked **Q-15**
  (`03_consistency.md:3922-3924`); the W-159 Dec-31 investment-equality gap
  (`_get_account_balance_map` vs `_project_investment_for_year`) is the
  F-006/F-007 divergence inherited here. No pinned value to classify.
- **Consistency-invariant test present?** **NO** -- nothing asserts the
  net-worth Dec-31 investment balance == the savings-progress Dec-31
  investment balance for the same account (W-159, Q-15).
- **Edge cases untested.** Anchor-None; W-159 investment Dec-31 equality;
  dual-dispatcher (F-006/F-007).
- **Coverage verdict.** **BLOCKED-ON-OPEN-QUESTION (Q-15)**, compounded by
  **NO-PINNED-TEST** for the `dec31` dollar value.
- **Independent note.** P7-d. Same self-referential-delta limitation as
  concept 14; the W-159 cross-path equality F-007 flags has zero test.

---

### Concept 16: `year_summary_principal_paid`

- **Canonical producer.** `_compute_debt_progress`
  @`year_end_summary_service.py:824-882` (`principal_paid = jan1_bal -
  dec31_bal@:871`, over the A-06 `_generate_debt_schedules` source)
  (`02_concepts.md:2664-2674`). **PRIMARY PATH known.**
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_year_end_summary_service.py`):
  `test_debt_progress_with_amortization` `1280  assert entry["jan1_balance"]
  == Decimal("237547.74")` / `1281 ... ["dec31_balance"] ==
  Decimal("234701.02")` / `1282 ... ["principal_paid"] ==
  Decimal("2846.72")`. PINNED.
- **Relationship tests.** `test_debt_progress_with_amortization`
  `1284-1286  assert entry["principal_paid"] == (entry["jan1_balance"] -
  entry["dec31_balance"])` and `test_debt_progress_with_payments`
  `1324-1326` (same identity) -- the F-053 `principal_paid = jan1 - dec31`
  invariant pinned, and here NON-trivially because `jan1_balance` /
  `dec31_balance` are independently pinned to exact dollars at `:1280-1281`.
- **Pinned / loose classification.** PINNED (exact string-`Decimal` for all
  three figures plus the identity).
- **E-NN-consistency check.** F-053 verdict **AGREE-by-construction**
  (`03_consistency.md:3968`): the year-end `jan1-dec31` delta == the
  per-period schedule principal sum, both reading the same
  `load_loan_context` A-06 schedule. Pinned values exercise the canonical
  A-06 schedule -> E-NN-consistent; not `PINNED-AGAINST-DIVERGENT-BEHAVIOR`.
  Inherits (cross-ref, not re-verdicted) the F-013/A-05 `monthly_payment`
  input SILENT substrate the schedule rests on.
- **Consistency-invariant test present?** **PARTIAL** -- the
  `principal_paid == jan1 - dec31` identity is pinned (`:1284-1286`), but no
  test asserts the year-end delta == the loan-dashboard
  `principal_paid_per_period` SUM over the same year (F-053's A-vs-B pair,
  AGREE-by-construction but unguarded).
- **Edge cases untested.** The inherited F-013 ARM `monthly_payment` input
  drift (the schedule is only as stable as its `monthly_payment` substrate;
  cross-ref slice-2 `monthly_payment`).
- **Coverage verdict.** **COVERED** (>=1 pinned exact-`Decimal` for jan1 /
  dec31 / principal_paid, the `jan1-dec31` identity pinned non-trivially,
  value F-053-AGREE-consistent). The year-end-vs-per-period-sum
  reconciliation is AGREE-by-construction but UNTESTED (carried to Part 7.B).
- **Independent note.** P7-d. The only year-summary concept with exact-
  pinned endpoint dollars; the F-013 inherited substrate is the residual
  risk, not the principal-paid arithmetic itself.

---

### Concept 17: `year_summary_growth`

- **Canonical producer.** Token-overloaded (E2): **YG1**
  `_project_investment_for_year` (via `_compute_savings_progress@:887`);
  **YG2** `_compute_interest_for_year@:1207`; **YG3**
  `_compute_mortgage_interest@:380` (`02_concepts.md:2714-2720`). YG1
  dispatcher Q-15-gated; YG2/YG3 AGREE-by-construction.
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_year_end_summary_service.py`). NO pinned
  YG1 investment-growth or YG2 interest dollar at the year-end layer:
  `test_savings_progress_basic` `1442  assert entry["total_contributions"]
  == Decimal("200.00")` (contributions, not growth) but
  `1445  assert entry["investment_growth"] == ZERO` (LOOSE zero);
  `test_savings_investment_with_growth` `1512  assert
  entry["investment_growth"] > ZERO` (LOOSE); `test_savings_hysa_with_interest`
  `1620 ... > ZERO` (LOOSE); pre-anchor `1732/1734 ... > Decimal("10000.00")
  / > ZERO` (LOOSE). YG2 engine-layer values ARE pinned under `apy_interest`
  (F-041, concept 1); YG3 engine-layer under `interest_paid_per_period`
  (slice-2, F-018).
- **Relationship tests.** None at the year-end layer (E2: YG1/YG2/YG3 not
  summable; no cross-sub-concept test, correctly).
- **Pinned / loose classification.** YG1 year-end `investment_growth`
  **LOOSE-ONLY** (`> ZERO` / `== ZERO`); YG2/YG3 pinned only at their
  delegated engines (concept 1 / slice-2), not at the year-end aggregate.
- **E-NN-consistency check.** F-054: **YG1 UNKNOWN** blocked **Q-15**
  (`03_consistency.md:4020`; dispatcher, cross-ref F-006 not re-derived);
  **YG2 AGREE-by-construction** (sums the single `apy_interest` engine,
  F-041); **YG3 AGREE-by-construction** (same A-06 schedule, F-018). The
  LOOSE `> ZERO` assertions cannot be classified pinned-against; they simply
  do not pin the YG1 value.
- **Consistency-invariant test present?** **NO** -- no test asserts YG1
  annual == the `growth_engine.project_balance` per-period growth SUM over
  the year (W-151/W-159), nor YG2 annual == per-period `calculate_interest`
  sum at the year-end layer.
- **Edge cases untested.** YG1 dispatcher (Q-15 / F-006); YG1 exact
  year-end growth dollar (only `> ZERO`); the W-159 Dec-31 equality.
- **Coverage verdict.** **LOOSE-ONLY** for the YG1 year-end
  `investment_growth` value, compounded by **BLOCKED-ON-OPEN-QUESTION
  (Q-15)** for the dispatcher; YG2/YG3 are AGREE-by-construction and pinned
  only at their delegated engines (concept 1 / slice-2 F-018), not at this
  aggregate.
- **Independent note.** P7-d. Every year-end growth assertion is `> ZERO`;
  the Q-15 dispatcher means even a pinned value could not be classified
  COVERED until the canonical dispatcher is decided.

---

### Concept 18: `year_summary_employer_total`

- **Canonical producer.** `_compute_savings_progress`
  @`year_end_summary_service.py:887` (sums limit-CAPPED per-period
  `growth_engine.calculate_employer_contribution` via
  `_project_investment_for_year`) (`02_concepts.md:2747-2755`). Dispatcher
  Q-15-gated; the cap axis inherits F-043.
- **Pinned-value tests** (Read this session,
  `tests/test_services/test_year_end_summary_service.py`):
  `test_savings_progress_basic` `1444  assert entry["employer_contributions"]
  == ZERO` (LOOSE zero); `test_savings_employer_match` `1565 ... > ZERO`
  (LOOSE); `test_savings_employer_flat_pct` `1595 ... > ZERO` (LOOSE);
  pre-anchor `1784 ... > ZERO` (LOOSE). **No pinned non-zero year-end
  employer-total dollar.** (The per-period engine IS pinned under
  `employer_contribution`, concept 3, but not the year-end sum.)
- **Relationship tests.** None pinned (F-055's annual==per-period-sum is
  AGREE-by-construction but unguarded).
- **Pinned / loose classification.** **LOOSE-ONLY** (`> ZERO` / `== ZERO`).
- **E-NN-consistency check.** F-055 verdict **DIVERGE** for the
  card-vs-(chart/year-end-total) axis -- inherits **F-043**
  (`03_consistency.md:4082-4085`): the year-end total sums the CAPPED
  per-period match while the dashboard card (`investment.py:188`) shows the
  UNCAPPED match; for a `match` employer near the limit they disagree
  (card $240 vs year-end $100, `03_consistency.md:4078-4081`). The
  dispatcher axis is additionally **UNKNOWN blocked Q-15**. The LOOSE
  `> ZERO` assertions neither pin the value nor catch F-043/F-055.
- **Consistency-invariant test present?** **NO** -- nothing asserts the
  year-end employer total == the dashboard card == the growth-chart
  employer line for a limit-binding `match` employer (the F-043/F-055
  three-surface divergence).
- **Edge cases untested.** The F-043 limit-binding `match` divergence (the
  C3-adjacent silent overstatement); the Q-15 dispatcher; any pinned
  non-zero year-end employer dollar.
- **Coverage verdict.** **LOOSE-ONLY** (no pinned year-end employer-total
  value), compounded by the **F-055/F-043 cap DIVERGE UNTESTED** and the
  **Q-15 dispatcher (BLOCKED-ON-OPEN-QUESTION)**. Escalated to Part 7.B as
  the year-end consumer of the F-043 divergence.
- **Independent note.** P7-d. Worst-covered slice-4 concept that carries a
  live DIVERGE: a `> ZERO` bar over a figure that silently disagrees with
  the dashboard card by the full uncapped-vs-capped delta.

---

### Slice-4 verdict roll-up

| Concept | Verdict | Governing F-NN / Q |
| --- | --- | --- |
| `apy_interest` | COVERED (single engine pinned, invalid-freq edge pinned); PA-19 aggregate-vs-per-period-sum invariant UNTESTED | F-041 AGREE |
| `growth` | COVERED (G1/G2 engine pinned); LOOSE-ONLY G3 pct; F-042 SWR/zero-return DIVERGE UNTESTED (zero-SWR test pins only the correct slider side) | F-042 DIVERGE; G3 def-split |
| `employer_contribution` | COVERED (engine pinned all branches); F-043 uncapped-card vs capped-chart DIVERGE UNTESTED | F-043 DIVERGE |
| `contribution_limit_remaining` | NO-PINNED-TEST (route-resident producer untested; the pinned growth-engine field is a different producer) | F-044 AGREE; E-28 |
| `ytd_contributions` | COVERED (pinned, zero/None + pre-filter contract pinned) | F-045 AGREE |
| `transfer_amount` | COVERED (pinned + Invariant-3 shadow-collapse consistency invariant pinned) | F-029 AGREE |
| `transfer_amount_computed` | NO-PINNED-TEST (the $269.23 pin is a posted value, not the limit/26 / $500 / P&I+escrow derivation) | F-030 AGREE |
| `effective_amount` | COVERED (4-tier property pinned); bypass-equivalence + F-028 cross-anchor UNTESTED; Q-08 sub-axis BLOCKED-ON-OPEN-QUESTION | F-027 DIVERGE-label; F-028 Q-08 |
| `goal_progress` | GP1 COVERED (incl. ROUND_CEILING pinned); GP2 BLOCKED-ON-OPEN-QUESTION (Q-08) -- pins lock the estimated base pre-Q-08 | F-046 GP1 AGREE / GP2 UNKNOWN; Q-08 |
| `emergency_fund_coverage_months` | COVERED (pinned + months/paychecks/years internal invariant pinned) | F-047 AGREE |
| `cash_runway_days` | NO-PINNED-TEST for the main int(bal/daily_avg) formula (only None/0 edge guards) | F-048 AGREE |
| `pension_benefit_annual` | COVERED (pinned + component invariant); PA-30 assumption contradicted | F-049 AGREE; PA-30 |
| `pension_benefit_monthly` | COVERED (pinned + monthly==annual/12 relationship invariant pinned) | F-050 AGREE |
| `year_summary_jan1_balance` | BLOCKED-ON-OPEN-QUESTION (Q-15) + NO-PINNED-TEST (only self-referential delta) | F-051 UNKNOWN; Q-15 |
| `year_summary_dec31_balance` | BLOCKED-ON-OPEN-QUESTION (Q-15) + NO-PINNED-TEST (only self-referential delta) | F-052 UNKNOWN; Q-15 |
| `year_summary_principal_paid` | COVERED (jan1/dec31/principal_paid pinned, identity pinned non-trivially); inherits F-013 substrate | F-053 AGREE-by-construction |
| `year_summary_growth` | LOOSE-ONLY (YG1 year-end growth `> ZERO`) + BLOCKED-ON-OPEN-QUESTION (Q-15); YG2/YG3 AGREE-by-construction (pinned only at delegated engines) | F-054 YG1 UNKNOWN / YG2/YG3 AGREE; Q-15 |
| `year_summary_employer_total` | LOOSE-ONLY + F-055/F-043 cap DIVERGE UNTESTED + Q-15 BLOCKED-ON-OPEN-QUESTION | F-055 DIVERGE; Q-15 |

Relationship / consistency invariants pinned in slice 4 (rare -- recorded
explicitly): `transfer_amount` Invariant-3 shadow-collapse
(`test_full_payment_pipeline:137,143,150`); `emergency_fund_coverage_months`
months/paychecks/years (`test_paychecks_formula:103-104`,
`test_years_formula:113-114`); `pension_benefit_monthly` `monthly ==
annual/12` (`test_monthly_is_annual_divided_by_12:124`);
`year_summary_principal_paid` `principal_paid == jan1 - dec31`
(`test_debt_progress_with_amortization:1284-1286`, non-trivial because the
endpoints are independently pinned). Every OTHER slice-4 cross-producer
consistency invariant (F-041 PA-19 aggregate-vs-sum; F-042 slider-vs-gap
SWR; F-043/F-055 card-vs-chart-vs-year-end employer; F-053 year-end-vs-per-
period principal sum; F-054 YG1 net-worth-vs-savings-progress; the
`effective_amount` bypass-equivalence sweep) is **UNTESTED**.

Carried to Part 7.B (P7-e): the **F-042** SWR cross-anchor + zero-return-
exclusion (the one zero-SWR test pins only the correct slider display);
the **F-043 / F-055** uncapped-card vs capped-chart/year-end employer
divergence (zero catching test on any of the three surfaces); the
**F-027 / F-028** entry-progress base + cross-anchor inconsistency (Q-08-
gated); the **F-051 / F-052 / F-054-YG1 / F-055** Q-15-gated year-summary
dispatcher; the `cash_runway_days` main-formula and
`contribution_limit_remaining` / `transfer_amount_computed` route-resident-
producer absences; the F-041 PA-19 and F-053 year-end-vs-per-period-sum
AGREE-by-construction invariants that no test guards; the **PA-30** register
assumption contradicted by the live suite (finding-against-assumption).
Part 7.B/7.C are out of P7-d scope; the 18 records above are the P7-d
deliverable.

### Census completeness -- all 47 controlled-vocabulary concepts have a Part 7.A verdict

P7-a slice 1 (8): `checking_balance`, `projected_end_balance`,
`account_balance`, `period_subtotal`, `chart_balance_series`, `net_worth`,
`savings_total`, `debt_total`. P7-b slice 2 (13): `monthly_payment`,
`loan_principal_real`, `loan_principal_stored`, `loan_principal_displayed`,
`principal_paid_per_period`, `interest_paid_per_period`, `escrow_per_period`,
`total_interest`, `interest_saved`, `months_saved`, `payoff_date`,
`loan_remaining_months`, `dti_ratio`. P7-c slice 3 (8): `paycheck_gross`,
`paycheck_net`, `taxable_income`, `federal_tax`, `state_tax`, `fica`,
`pre_tax_deduction`, `post_tax_deduction`. P7-d slice 4 (18): the 18
concepts recorded above. **Total: 8 + 13 + 8 + 18 = 47.** No concept was
deferred from slices 1-3 (each prior slice roll-up assigns a verdict to all
of its concepts). **Every one of the 47 controlled-vocabulary concepts now
has exactly one Part 7.A coverage verdict.**

<!-- Part 7.B and Part 7.C: appended by session P7-e (per phase7_plan.md section 4). -->

## Part 7.B -- divergence-catching audit + symptom regression targets + cross-page meta-gap + Phase 6 equivalence implications

Per schema 3.2. One record per `DIVERGE` finding (20: F-001/002/003/005/009/
013/014/015/017/018/020/021/022/023/026/032/037/042/043/055), per symptom
regression target (#1-#5, each with its negative control), the explicit
cross-page meta-gap, and each Phase 6 D6-/S6-/B6- equivalence implication
(`06_dry_solid.md:2193-2206`). Catching-test searches were run this session via
Explore over the live `tests/` tree; raw grep results pasted. Pinned-vs-loose
and E-NN-consistency judged here from the asserting lines (contract items 3-4,
8). No test file opened for modification. "Would any existing test have caught
it?" downgrades a YES to NO when the candidate test passes against the divergent
code (contract item 3 clause).

### Cross-cutting catching-test sweep (this session, Explore)

```
grep -rn "def test.*matches.*grid|def test.*cross_page|def test.*consistent.*balance|def test.*same.*balance" tests/
  -> test_accounts.py:547 test_same_day_different_balance_creates_two_rows
  -> test_accounts.py:2211 test_checking_detail_matches_grid_balance        (the only near-miss)
grep -rn "_compute_account_projections|_get_account_balance_map|_compute_savings_progress" tests/   -> 0 matches
grep -rn "is_arm" tests/test_services/test_balance_calculator_debt.py                                 -> 0 matches
grep -rn "_compute_real_principal" tests/                                                            -> 0 matches
grep -rni "escrow" tests/test_services/test_balance_calculator_debt.py tests/test_services/test_balance_calculator.py -> 0 matches
grep -rni "import flask|ast.parse" tests/  (services flask-absent / Invariant-5 AST guard)           -> 0 matches
grep -rni "dispatcher|savings.*year_end|year_end.*savings" tests/                                    -> 1 match (route comment only)
```

The single near-miss `test_checking_detail_matches_grid_balance`
(`test_accounts.py:2211`) Read in Part 7.A cross-cutting evidence
(`07_test_gaps.md:38-53`): it computes its own `calculate_balances` WITHOUT
`selectinload(Transaction.entries)`, never calls `/grid`, uses no envelope
expense with cleared entries, and asserts
`assert "${:,.0f}".format(float(calc_balance)).encode() in resp.data` (whole-
dollar `float`, single-page substring). It passes against the divergent code.
Every "would it have caught it" YES below is downgraded to NO on this basis
where this test is the only candidate.

---

### F-001 `account_balance` -- DIVERGE

- **Divergence (re-read `03_consistency.md:202-211`).** Same `(user, period,
  scenario, account)` yields entry-aware grid/dashboard ($962.34) vs
  effective-amount `/savings`//accounts/net-worth ($500.00) for a Projected
  envelope expense with cleared entries; plus four anchor-None behaviors; plus a
  3-way loan base (stored vs engine vs schedule); plus dual undesignated
  per-account dispatch (Q-15).
- **Catching test search (this session).**
  `grep -rn "_compute_account_projections|_get_account_balance_map" tests/` ->
  `0 matches`; cross-page sweep above -> only the downgraded near-miss.
- **Would any existing test have caught it?** **NO.** No test renders two of
  {grid, `/savings`, `/accounts`} for one account/period and asserts equality;
  zero tests exercise either dispatcher; the near-miss is entries-absent and
  single-page (downgraded per cross-cutting note). Part 7.A
  (`07_test_gaps.md:206-219`) verdicted `account_balance`
  BLOCKED-ON-OPEN-QUESTION -- feeds here directly.
- **Proposed test pointer.** PT-01 (checking + anchor-None axes, unconditional);
  PT-05 (dual-dispatcher equivalence, holds independent of Q-15);
  loan-base axis -> PT-02 (Q-11/Q-15 gated, no pinnable value -- recorded, not
  authored).
- **Blast radius.** `/accounts` shows a balance that matches nowhere else
  (symptom #5, C3 `03_consistency.md:6066`); a checking account with one
  Projected envelope expense ships two different dollar balances on two pages
  with no error.

---

### F-002 `checking_balance` -- DIVERGE

- **Divergence (`03_consistency.md:255-275`).** `selectinload(entries)` present
  `grid.py:229` / absent `savings_dashboard_service.py:92-100` ->
  `_entry_aware_amount` returns the entry formula vs `effective_amount`; the
  same-page grid subtotal (raw `effective_amount`) also disagrees with the grid
  balance row by `cleared_debit + sum_credit`.
- **Catching test search.** `grep -rni "selectinload" tests/` -> 35 hits, all in
  `test_balance_calculator_entries.py` exercising the engine in isolation
  (Part 7.A `07_test_gaps.md:79-88`: `test_grocery_bug_scenario_after_true_up`
  pins the entries-loaded `4962.34`; `test_entry_aware_entries_not_loaded` pins
  the entries-absent `4500.00`). No cross-page sweep match.
- **Would any existing test have caught it?** **NO.** Both divergence branches
  are pinned *in isolation* (each correct for its input) but no test asserts
  they are equal across grid / `/savings` for the same `(user, period,
  scenario, account)`; the same-page subtotal-vs-balance delta has no test.
  Part 7.A verdict COVERED (engine) / cross-page UNTESTED.
- **Proposed test pointer.** PT-01 (cross-page checking equality); PT-04
  (same-page balance-delta == subtotal, Q-10 gated on canonical subtotal def).
- **Blast radius.** Symptom #1: grid `$160` vs `/savings` `$114.29` for the
  same current period (C3 N/A -- symptom CRITICAL, `03_consistency.md:6073`);
  the `$45.71` gap ships silently.

---

### F-003 `projected_end_balance` -- DIVERGE

- **Divergence (`03_consistency.md:323-340`).** Checking entries-load axis
  (inherits F-002, unconditional); loan end-of-period balance stored
  (`loan/dashboard.html:104`) vs engine `proj.current_balance` vs schedule
  (`year_end_summary_service.py:2079-2081`) -- three bases; anchor-None.
- **Catching test search.** `grep -rni "is_arm=True" tests/` -> 16 hits in
  `test_amortization_engine.py`/`test_loan.py`/`test_debt_strategy.py` (engine
  units); `grep -rn "is_arm" tests/test_services/test_balance_calculator_debt.py`
  -> `0 matches` (every debt-balance test fixed-rate). No loan-card-vs-`/savings`-
  vs-net-worth reconciliation.
- **Would any existing test have caught it?** **NO.** Fixed-rate amortization
  end balances are pinned (`test_balance_calculator_debt.py`, Part 7.A
  `07_test_gaps.md:153-159`) but to an undesignated-canonical base; zero ARM
  debt-balance tests; no 3-way loan-base reconciliation. Part 7.A verdict
  BLOCKED-ON-OPEN-QUESTION (Q-11/Q-15).
- **Proposed test pointer.** PT-01 (checking facet); PT-02 (loan 3-way, Q-11/
  Q-15 gated -- recorded, not authored).
- **Blast radius.** A partially-paid fixed loan's `/savings` projected balance
  and net-worth liability move with confirmed payments while the
  `/accounts/<id>/loan` card stays static (symptom #5 loan facet, C3
  `03_consistency.md:6066`).

---

### F-005 `chart_balance_series` -- DIVERGE

- **Divergence (`03_consistency.md:430-451`).** The charted series and a non-
  chart card for the same account/period disagree by the F-002 entries-load
  delta when one path selectinloaded entries and the other did not
  (`grid.balance_row:438` loaded vs `accounts.checking_detail:1407-1416` not).
- **Catching test search.** Part 7.A Explore (`07_test_gaps.md:295-297`):
  `test_charts.py` asserts only 301-to-`/analytics` + 404 (chart_data_service
  removed `e3b3a5e`); HYSA series oracle-pinned in `test_balance_calculator_hysa.py`
  but no rendered-vs-server (E-17) or chart-vs-card test.
- **Would any existing test have caught it?** **NO.** HYSA series values pinned
  in isolation (correct per branch); zero chart-vs-scalar-card cross-surface
  equality. Part 7.A verdict COVERED (HYSA producer) / cross-surface UNTESTED.
- **Proposed test pointer.** PT-01 (cross-surface equality includes the chart
  series flavor -- the chart line for the period == the scalar card == grid).
- **Blast radius.** A chart line that does not match the scalar balance card
  for the same account/period (inherits symptom #1 mechanism).

---

### F-009 `projected_end_balance` grid vs `checking_balance` /savings -- symptom #1 -- DIVERGE

- **Divergence (`03_consistency.md:690-712`).** Single pinned dimension:
  `selectinload(entries)` at `grid.py:229` vs absent
  `savings_dashboard_service.py:92-100`; every other dimension AGREES. Gap
  `$160 - $114.29 = $45.71` = sum of cleared/credit entry value the entries-
  unloaded path double-subtracts.
- **Catching test search.** Cross-page sweep above -> 0 grid-vs-savings equality
  tests; near-miss downgraded.
- **Would any existing test have caught it?** **NO.** Part 7.A
  (`07_test_gaps.md:31-57`) pasted the three empty cross-page greps; the only
  candidate passes against divergent code.
- **Proposed test pointer.** PT-01.
- **Blast radius.** The developer's reported symptom #1 ($160 vs $114.29) has
  zero falsifying test; a fix cannot be regression-locked (C3 symptom CRITICAL).

---

### F-013 `monthly_payment` 16-site -- symptom #2 -- DIVERGE

- **Divergence (`03_consistency.md:1100-1141`).** 16 sites feed
  `calculate_monthly_payment` incompatible `(P,r,n)` triples; site-7 `n=T-e`
  vs site-3 per-row `n=T-month_num+1` (off by ~1) -> few-dollar gap; the
  `using_contractual`-vs-`is_arm` discriminator seam; `update_params`-then-
  recompute drop.
- **Catching test search.**
  `grep -rni "monthly_payment.*==.*monthly_payment|16.*site|loan_resolver" tests/`
  -> only `test_amortization_engine.py:747-748`
  `assert projection.summary.monthly_payment == standalone_summary.monthly_payment`
  (projection vs standalone `calculate_summary`, identical engine inputs -- 2 of
  16 surfaces, not the cross-site triple divergence).
- **Would any existing test have caught it?** **NO.** `:747` only proves the
  engine is internally consistent for one input set; it never assembles site-7's
  calendar-`n` vs site-3's per-row `n` for the same loan-on-date. Part 7.A
  verdict COVERED (formula+edges) / F-013 cross-site UNTESTED.
- **Proposed test pointer.** PT-07 (16-site / D6-01 equivalence).
- **Blast radius.** $1911.54/$1914.34/$1912.94 -> $1910.95 displayed-payment
  fluctuation (symptom #2, C3 symptom CRITICAL).

---

### F-014 `loan_principal_real` -- symptom #3 -- DIVERGE

- **Divergence (`03_consistency.md:1205-1242`).** No settle-driven writer for
  STORED `current_principal` (grep-proven); the card renders STORED regardless
  of loan type; A-fixed (A-06-prepared engine walk) vs C-fixed
  (`_compute_real_principal` raw replay) disagree.
- **Catching test search.** `grep -rn "_compute_real_principal" tests/` ->
  `0 matches`; `grep -rni "settle.*principal|principal.*decrease|principal.*after.*settle" tests/`
  -> 6 hits, the relevant one
  `test_balance_calculator_debt.py::test_transfers_reduce_balance_by_principal_only`
  pins `principal_by_period[2] == Decimal("99.55")` / `balances[2] ==
  Decimal("99900.45")` -- the *balance-calculator amortization trajectory*, NOT
  the displayed/stored `current_principal` after a settled transfer.
- **Would any existing test have caught it?** **NO.** No test settles a transfer
  into a loan and asserts the displayed/stored principal decreased; zero
  `_compute_real_principal` coverage. Part 7.A verdict NO-PINNED-TEST (the
  highest-stakes slice-2 absence).
- **Proposed test pointer.** PT-08 (fixed-rate engine-real decrease, pinnable);
  ARM no-writer facet recorded as Q-15/Q-17-gated within PT-08.
- **Blast radius.** Symptom #3: current principal does not move as transfers
  settle (C3 symptom CRITICAL); ARM card frozen until manual edit.

---

### F-015 `loan_principal_stored` -- DIVERGE

- **Divergence (`03_consistency.md:1270-1300`).** B (stored,
  `loan/dashboard.html:104`) vs C (`amortization_engine.py:980-984` engine-
  walked) for a partially-paid fixed loan: the card shows the static stored
  value, the refinance prefill (`loan.py:1095`) the engine-real value.
- **Catching test search.** `grep -rni "current_principal" tests/test_routes/test_loan.py`
  -> 50+ hits; the pinned one Part 7.A Read (`07_test_gaps.md:668-671`)
  `test_params_update:344 assert params.current_principal == Decimal("22000.00")`
  -- pins the column == the value *manually written*. No stored-vs-engine-walked
  test.
- **Would any existing test have caught it?** **NO.** The pin verifies only the
  manual-write path (A-04-consistent, never exercises the fixed-with-confirmed-
  payments stale-mirror case). Part 7.A verdict COVERED (manual write) / F-015
  UNTESTED. Not PINNED-AGAINST-DIVERGENT-BEHAVIOR.
- **Proposed test pointer.** PT-08 (the engine-walked vs stored assertion is the
  same fixture family; the stored card must render the engine-real value).
- **Blast radius.** A partially-paid fixed loan's prominent "Current Principal"
  card is a stale mirror; same loan-on-date shows two principals.

---

### F-017 `principal_paid_per_period` -- DIVERGE

- **Divergence (`03_consistency.md:1410-1447`).** Balance-path B
  (`balance_calculator.py:270` raw `effective_amount`) omits A-06 escrow
  subtraction -> for an escrow-inclusive payment B over-states principal by the
  escrow ($775.00 B vs $275.00 A/C, a $500.00 gap); A-vs-C AGREE by
  construction.
- **Catching test search.**
  `grep -rni "escrow" tests/test_services/test_balance_calculator_debt.py tests/test_services/test_balance_calculator.py`
  -> `0 matches`; `grep -rni "principal_by_period" tests/` -> 52 hits, all
  non-escrow (Part 7.A `07_test_gaps.md:748-754`).
- **Would any existing test have caught it?** **NO.** Every pinned B-path test
  deliberately uses non-escrow loans (B and A coincide there). Part 7.A verdict
  COVERED (non-escrow B) / F-017 escrow divergence UNTESTED.
- **Proposed test pointer.** PT-10.
- **Blast radius.** For an escrow-inclusive biweekly mortgage every page reading
  `principal_by_period` over-reports principal paid by the escrow per period.

---

### F-018 `interest_paid_per_period` -- DIVERGE

- **Divergence (`03_consistency.md:1475-1509`).** Two raw `generate_schedule`
  callers (`savings_dashboard_service.py:471,488`; `debt_strategy.py:175,181`)
  bypass A-06 -> interest on an escrow-inflated paydown trajectory; month-2
  `$1,495.50` raw vs A-06-correct `$1,498.00`, compounding; A-B dashboard/year-
  end AGREE by construction.
- **Catching test search.**
  `grep -rni "get_payment_history|generate_schedule|prepare_payments_for_engine|_check_loan_paid_off" tests/`
  -> 200+ hits, all non-escrow per-row interest pins (Part 7.A
  `07_test_gaps.md:793-799`); no raw-vs-A-06-prepared reconciliation.
- **Would any existing test have caught it?** **NO.** Pinned per-row interest
  uses non-escrow loans (raw==prepared there). Part 7.A verdict COVERED (per-row
  formula) / F-018 raw-vs-prepared UNTESTED.
- **Proposed test pointer.** PT-11.
- **Blast radius.** Paid-off boolean (`_check_loan_paid_off`) and the debt-
  strategy starting principal are computed on an escrow-inflated interest
  series; over a year the Schedule-A-style figure drifts if ever summed.

---

### F-020 `total_interest` -- DIVERGE

- **Divergence (`03_consistency.md:1600-1632`).** A (life-of-loan engine from
  `original_principal`) vs C (strategy total from site-16 `real_principal` at
  today) hold; a one-debt strategy run must equal the payoff calculator and need
  not; A-vs-B is DEFINITION-by-design (label-distinct).
- **Catching test search.**
  `grep -rni "_compute_mortgage_interest|total_interest.*==|life of loan" tests/`
  -> Definition-1 pins only (`test_amortization_engine.py:778,788
  == Decimal("318861.58")`); `test_debt_strategy_service.py:309..779
  result.total_interest == Decimal(...)` are standalone strategy pins, never
  reconciled to a single-loan `calculate_summary`.
- **Would any existing test have caught it?** **NO.** No single-loan A-vs-C
  reconciliation; no Def1-vs-Def2 label-distinctness. Part 7.A verdict COVERED
  (Def1) / A-vs-C UNTESTED.
- **Proposed test pointer.** PT-12.
- **Blast radius.** A user comparing the loan dashboard "Total Interest (life
  of loan)" to the debt-strategy results sees an unlabeled difference.

---

### F-021 `interest_saved` -- DIVERGE

- **Divergence (`03_consistency.md:1660-1696`).** Path B `loan.py:968`
  `.quantize(Decimal("0.01"))` defaults to banker's vs path A
  `amortization_engine.py:749` ROUND_HALF_UP -> $0.01 at the half-cent boundary
  (A-01 24-site class); plus a dollars-scale DEFINITION gap A-vs-C.
- **Catching test search.**
  `grep -rni "interest_saved|interest savings" tests/test_routes/test_loan.py`
  -> only prose/label hits + `test_refinance_comparison_metrics_hand_calculated:3524
  assert "$68,572.58" in html` (LOOSE substring, value NOT on a half-cent
  boundary -- cannot fail on banker's-vs-HALF_UP). Path-A
  `test_summary_with_extra:300 == Decimal("90074.66")` is the non-divergent
  path.
- **Would any existing test have caught it?** **NO.** Path A pinned (ROUND_HALF_UP,
  A-01-clean); the divergent path B has only a non-boundary LOOSE substring.
  Part 7.A verdict COVERED (path A) / F-021 `loan.py:968` UNTESTED. Not
  PINNED-AGAINST-DIVERGENT-BEHAVIOR.
- **Proposed test pointer.** PT-13.
- **Blast radius.** The refinance "interest saved" figure rounds the wrong way
  at every half-cent boundary; an A-01-confirmed banker's site ships behind a
  loose substring.

---

### F-022 `months_saved` -- DIVERGE

- **Divergence (`03_consistency.md:1722-1756`).** Four distinct integer-month
  quantities share the token (standard-vs-accelerated A; committed-vs-original
  B; refinance break-even C; strategy payoff-delta D); render-slot reuse risks
  the user comparing `27`-month break-even to `54`-month acceleration (W-242).
- **Catching test search.** `grep -rni "break_even|months_saved" tests/` ->
  path-A `test_amortization_engine.py:297 summary.months_saved == 110`;
  `test_loan.py:3395 test_refinance_break_even_calculation_exact` asserts
  `"Break-even" in html` and `"31 months" in html` (LOOSE substring, single
  surface -- does not assert the two "months" figures are labelled distinctly /
  not mis-comparable).
- **Would any existing test have caught it?** **NO.** Path-A pinned; the break-
  even surface has a loose single-page substring; no A-vs-C label-distinctness.
  Part 7.A verdict COVERED (path A) / F-022 fork UNTESTED.
- **Proposed test pointer.** PT-14.
- **Blast radius.** A user reading "31 months" (break-even) and a months-saved
  figure for the same loan can wrongly compare two different quantities.

---

### F-023 `payoff_date` -- DIVERGE

- **Divergence (`03_consistency.md:1785-1818`).** A-vs-B:
  `calculate_payoff_by_date` has no `payments`/`anchor` param
  (`amortization_engine.py:753-762`) -> for an ARM it ignores the user-verified
  anchor and projects origination-forward -> a different last-row date than the
  displayed `summary.payoff_date`; A-vs-C strategy walk differs.
- **Catching test search.**
  `grep -rni "calculate_payoff_by_date|payoff_date.*==|payoff.*date" tests/` ->
  `test_amortization_engine.py:649-663 summary.payoff_date == schedule[-1].payment_date`
  (fixed-rate, self-consistent) + `:685 payoff_date_with_extra ==
  accel_schedule[-1].payment_date`. No A-vs-B (ARM no-anchor) reconciliation.
- **Would any existing test have caught it?** **NO.** Fixed-rate path A pinned
  + cross-validated against an independent schedule; B's no-anchor ARM seam
  never exercised. Part 7.A verdict COVERED (fixed-rate) / F-023 A-vs-B UNTESTED.
- **Proposed test pointer.** PT-15 (A==B equivalence; absolute date not hand-
  pinnable without the full ARM schedule -- the invariant is the equality).
- **Blast radius.** The target-date what-if tool's baseline payoff disagrees
  with the displayed projected payoff for the same ARM; a wrong payoff also
  mis-bounds shadow-transfer generation (W-239).

---

### F-026 ARM payment stability inside the fixed window -- symptom #4 -- DIVERGE

- **Divergence (`03_consistency.md:1985-2038`).** Site 7 re-amortizes a FROZEN
  STORED `current_principal` over a strictly-decreasing `remaining` -> for a
  5/5 ARM inside its fixed window the displayed Monthly P&I creeps upward month
  over month ($2,460.45 -> $2,463.27, both != the correct constant $2,398.20)
  with no rate change and no edit -- a direct E-02 violation.
- **Catching test search.** `grep -rn "is_arm" tests/test_services/test_balance_calculator_debt.py`
  -> `0 matches`;
  `grep -rni "schedule\[.*\].payment ==|stable|constant.*payment" tests/test_services/test_amortization_engine.py`
  -> the only consecutive-row payment assertions are the rate-boundary tests
  `:1781 assert schedule[11].payment != schedule[12].payment` (the *inverse* --
  payment must CHANGE at a boundary); `test_arm_projection_is_arm_true_no_rate_changes:2842
  assert proj.summary.monthly_payment == calculate_monthly_payment(CURRENT_PRINCIPAL,
  CURRENT_RATE, proj.remaining_months)` -- pins a SINGLE call at one instant,
  not stability across consecutive `months_elapsed`.
- **Would any existing test have caught it?** **NO.** `:2842` asserts the re-
  amortized value at one instant equals the re-amortized formula -- it would
  still pass while the value creeps month over month (it pins the buggy method's
  output, not the E-02 invariant). No test asserts payment(e) == payment(e+1)
  inside the fixed window. Part 7.A verdict COVERED (single call) / F-026 in-
  window stability UNTESTED.
- **Proposed test pointer.** PT-06.
- **Blast radius.** Symptom #4: every ARM Monthly P&I surface
  (`loan/dashboard.html:129`, recurring-transfer prefill, debt-summary PITI)
  creeps upward each request; the recurring-transfer auto-amount drifts with it
  (C3 symptom CRITICAL).

---

### F-032 `paycheck_gross` off-engine DTI denominator -- DIVERGE

- **Divergence (`03_consistency.md:2760-2805`).** The off-engine
  `savings_dashboard_service.py:263-266` `salary_gross_biweekly` drops
  `_apply_raises` and uses banker's-default quantize -> DTI denominator
  $8,666.67 vs the correct post-raise HALF_UP $8,926.67 (displayed DTI 27.7%
  vs correct 26.9%); A-vs-C AGREE by construction.
- **Catching test search.**
  `grep -rni "salary_gross_biweekly|gross_monthly_income|SalaryRaise" tests/test_services/test_savings_dashboard_service.py`
  -> `test_dti_with_salary:1300 assert ds["gross_monthly_income"] ==
  Decimal("6500.00")` on a fixture with `annual_salary = Decimal("78000.00")`
  and NO `SalaryRaise` added (Read this session, fixture lines 1268-1300).
- **Would any existing test have caught it?** **NO.** The fixture has no
  applicable raise, so off-engine == canonical and $6,500.00 is correct under
  both paths -- it structurally cannot fail on F-032 (not PINNED-AGAINST-
  DIVERGENT-BEHAVIOR; simply non-catching). Part 7.A verdict COVERED (canonical
  engine) / F-032 off-engine UNTESTED.
- **Proposed test pointer.** PT-16.
- **Blast radius.** Every displayed DTI ratio for an employee with a scheduled
  raise rests on an income base that drops the raise and rounds the wrong way
  (C3 N/A; debt-affordability decision off a wrong denominator).

---

### F-037 `fica` calibration-path SS-cap bypass -- DIVERGE

- **Divergence (`03_consistency.md:3060-3114`).** `apply_calibration`
  (`calibration_service.py:139-141`) has no `ss_wage_base`/`cumulative_wages`
  and never zeroes SS after the wage base; a $312,000 calibrated earner accrues
  $19,344.00 SS vs the correct $11,439.00 (= $184,500 x 0.062), +$7,905/yr.
- **Catching test search.**
  `grep -rni "apply_calibration|ss_wage_base|cumulative_wages|effective_ss_rate" tests/test_services/test_calibration_service.py`
  -> `test_basic_calibration_application:205 assert result["ss"] ==
  Decimal("143.08")`, `test_federal_and_state_use_taxable_not_gross:251 assert
  result["ss"] == Decimal("248.00")` -- both at single periods WELL BELOW the
  wage base (capped==uncapped there); zero `ss_wage_base`/`cumulative_wages`
  hits in `test_calibration_service.py`.
- **Would any existing test have caught it?** **NO.** Calibrated SS pinned only
  below the cap; the bracket-path cap tests (`test_paycheck_calculator.py`
  `TestFICADirectBoundary`, Part 7.A `07_test_gaps.md:1566-1579`) exercise
  `calculate_fica`, not `apply_calibration`. No test threads cumulative wages
  over `ss_wage_base` with calibration ACTIVE. Part 7.A verdict COVERED
  (bracket engine) / F-037 calibration bypass UNTESTED -- the headline slice-3
  gap. Not PINNED-AGAINST-DIVERGENT-BEHAVIOR.
- **Proposed test pointer.** PT-17.
- **Blast radius.** A calibrated high earner (the documented calibration use
  case) ships an overstated FICA line and understated net pay by $744.00 per
  over-cap period (C3 CRITICAL `03_consistency.md:6064`).

---

### F-042 `growth` SWR cross-anchor + zero-return exclusion -- DIVERGE

- **Divergence (`03_consistency.md:3540-3600`).** `compute_slider_defaults:304`
  `is None` (displays 0.00% for an explicit-zero SWR) vs `compute_gap_data:220`
  `or "0.04"` (gap math uses 4%) -> $4,000/mo phantom income; plus
  `compute_slider_defaults:321` truthiness drops a zero-`assumed_annual_return`
  account from the weighted-return denominator (7.00% shown vs true 3.50%).
- **Catching test search.**
  `grep -rni "compute_gap_data|compute_slider_defaults|safe_withdrawal_rate|investment_income|assumed_annual_return" tests/test_services/test_retirement_dashboard_service.py`
  -> `test_zero_swr_round_trips_as_decimal_zero:189 assert slider["current_swr"]
  == Decimal("0.00")` -- pins ONLY the `compute_slider_defaults` display side
  (the E-NN-CORRECT half); never asserts `data["chart_data"]["investment_income"]`
  or the gap SWR.
- **Would any existing test have caught it?** **NO.** It pins the symptom-free
  half and structurally cannot fail on the phantom income; no zero-return-
  account weighting test. Part 7.A verdict COVERED (G1/G2 engine) / F-042
  DIVERGE UNTESTED. Not PINNED-AGAINST-DIVERGENT-BEHAVIOR.
- **Proposed test pointer.** PT-18.
- **Blast radius.** Slider shows 0.00% while the gap silently assumes 4% ->
  $4,000/mo phantom retirement income; a zero-return half-million silently
  dropped from the weighted return (C3 CRITICAL `03_consistency.md:6065`).

---

### F-043 `employer_contribution` uncapped-card vs capped-chart -- DIVERGE

- **Divergence (`03_consistency.md:3650-3691`).** `investment.py:188` passes
  the UNCAPPED `periodic_contribution`; `growth_engine.py:259-265` passes the
  limit-CAPPED `contribution` -- both into the single canonical function. Near
  the annual contribution limit the card shows $240 while the chart/year-end
  show $100 for the same period.
- **Catching test search.**
  `grep -rni "annual_contribution_limit|employer_contribution|match.*cap|employer_contributions" tests/test_services/test_growth_engine.py tests/test_services/test_year_end_summary_service.py`
  -> `test_match_full:89 assert result == Decimal("150.00")` etc. -- every
  fixture sets no binding `annual_contribution_limit` (capped==uncapped); no
  card-vs-chart-vs-year-end comparison.
- **Would any existing test have caught it?** **NO.** Engine pinned where capped
  == uncapped; the divergent route-card path has no pinned test. Part 7.A
  verdict COVERED (engine) / F-043 DIVERGE UNTESTED. Not PINNED-AGAINST-
  DIVERGENT-BEHAVIOR.
- **Proposed test pointer.** PT-19.
- **Blast radius.** For a match employer near the limit the dashboard card
  overstates the employer match by up to $140/period vs the chart and the
  year-end total (C3 N/A; feeds F-055).

---

### F-055 `year_summary_employer_total` -- DIVERGE (inherits F-043)

- **Divergence (`03_consistency.md:4055-4097`).** The year-end total sums the
  limit-CAPPED per-period match (`growth_engine.py:259-265`) while the dashboard
  card (`investment.py:188`) shows the UNCAPPED match -> they disagree for a
  match employer near the limit; the per-account dispatcher axis is additionally
  Q-15-gated.
- **Catching test search.** Same grep as F-043 + `test_year_end_summary_service.py`
  -> `test_savings_employer_match:1565 ... > ZERO`,
  `test_savings_employer_flat_pct:1595 ... > ZERO` (LOOSE; no pinned non-zero
  year-end employer dollar; no three-surface equality).
- **Would any existing test have caught it?** **NO.** Only `> ZERO`/`== ZERO`
  loose bars over the year-end total. Part 7.A verdict LOOSE-ONLY + F-055/F-043
  cap DIVERGE UNTESTED + Q-15.
- **Proposed test pointer.** PT-19 (the three-surface equality includes the
  year-end consumer); dispatcher axis Q-15-gated, recorded not authored.
- **Blast radius.** The year-end employer total silently disagrees with the
  dashboard card by the full uncapped-vs-capped delta for the same account/year.

---

### Symptom regression targets (`05_symptoms.md:1714-1721`)

Each symptom's post-remediation equivalence assertion is the E-04 invariant for
that symptom; each carries a falsification / negative control (the "if it does
not, re-investigate" clause) to be encoded so the catching test cannot be
trivially always-failing.

| Symptom | Equivalence target (E-04 per symptom) | Existing catching test? | Negative control (encode alongside) | Proposed |
| --- | --- | --- | --- | --- |
| **#1** $160 grid vs $114.29 /savings, same current period (F-009) | grid balance == `/savings` balance == `/accounts` balance for the same `(user,period,scenario,account)` | **NO** (cross-page sweep 0 matches; near-miss downgraded) | an account with NO cleared/credit entries on its Projected expenses -> grid == /savings already; the cross-page test must still PASS (proves it is not always-red) | PT-01 |
| **#2** payment fluctuates $1911 -> $1910.95 (F-013) | `(balance, monthly_payment, schedule)` equal across all 16 call surfaces for one loan-on-date | **NO** (only the engine-internal `:747` projection==standalone) | a freshly-originated loan (`months_elapsed=0`, no edits) -> all 16 sites trivially agree; equivalence must hold | PT-07 |
| **#3** current principal does not move as transfers settle (F-014) | strict principal decrease per settled payment-transfer (displayed == engine-real, both < pre-settle) | **NO** (`_compute_real_principal` 0 matches; the `99900.45` pin is the balance trajectory, not the displayed scalar) | a loan with ZERO settled transfers -> displayed principal == stored, unchanged (the "before" assertion) | PT-08 |
| **#4** 5/5 ARM payment creeps inside the fixed window (F-026) | ARM Monthly P&I identical across two consecutive `months_elapsed` inside the fixed window, no rate change | **NO** (existing ARM consecutive-row test asserts the payment CHANGES at a rate boundary, `:1781`) | at the rate-change boundary the ARM payment SHOULD change -- the in-window-stability assertion must NOT fire there (`schedule[11].payment != schedule[12].payment` stays the guard) | PT-06 |
| **#5** /accounts balances match nowhere else (F-001 + F-008) | one account's displayed balance equal on grid / dashboard / `/savings` / `/accounts` / net-worth for the same period | **NO** (no cross-page test; dual-dispatcher 0 matches) | a single plain checking account, no envelope expense, no loan -> all surfaces already equal; the cross-surface test must PASS | PT-01 + PT-05 |

---

### Cross-page balance-equality meta-gap (explicit; audit-plan:700-703)

- **The gap.** Audit-plan 7 (`financial_calculation_audit_plan.md:698-703`):
  "the test the developer most needs but probably does not have is a cross-page
  consistency test ... assert that every page-facing service produces the same
  balance for the same period. Note this gap explicitly even if individual
  concept tests exist."
- **Catching test search (this session, the three audit-plan-mandated greps,
  re-run via Explore).**
  ```
  grep -rn 'auth_client.get.*grid.*auth_client.get.*accounts|...accounts.*...grid' tests/test_routes/ -> 0 matches
  grep -rn 'auth_client.get.*grid.*auth_client.get.*savings|...savings.*...grid'   tests/test_routes/ -> 0 matches
  grep -rn 'grid.*==.*savings|checking.*==.*accounts'                              tests/             -> 0 matches
  grep -rn 'def test.*matches.*grid|def test.*cross_page|def test.*same.*balance'  tests/             -> only test_accounts.py:2211 (downgraded) + test_accounts.py:547 (two-rows, unrelated)
  ```
- **Would any existing test have caught it?** **NO.** Zero tests render two of
  {grid index, `/savings`, `/accounts`} in one function and assert the same
  balance. The single near-miss `test_checking_detail_matches_grid_balance`
  computes its own entries-ABSENT `calculate_balances`, never calls `/grid`,
  uses no envelope expense with cleared entries, and asserts a whole-dollar
  `float`-formatted single-page substring -- it passes against the divergent
  code (Part 7.A `07_test_gaps.md:38-53`, re-confirmed this session).
- **Proposed test pointer.** PT-01 (the cross-page fixture; subsumes symptoms
  #1 and #5 checking facets, F-001/F-002/F-005/F-009, and D6-04 calendar).
- **Blast radius.** The defining E-04 invariant of the whole balance family is
  untested; symptoms #1 and #5 -- the developer's two most concrete reported
  wrong-dollar bugs -- have no falsifying regression anchor.

---

### Phase 6 D6-/S6-/B6- equivalence-test implications (`06_dry_solid.md:2193-2206`)

| Implication | Equivalence it implies | Catching-test search (this session) | Caught? | Proposed |
| --- | --- | --- | --- | --- |
| **D6-01** one loan resolver, equal `(balance, monthly_payment, schedule)` across 16 surfaces | F-013 cross-site | `:747-748 projection.summary.monthly_payment == standalone_summary.monthly_payment` (2 surfaces, same inputs) | **NO** (engine-internal only) | PT-07 |
| **D6-02** one anchor-None behavior across the 6 producers | F-001 anchor-None | `test_balance_calc_none_anchor_balance:851 result[...] == Decimal("0.00")` (one producer's local behavior); `test_grid_account_with_no_anchor_balance:1527 assert "New Savings Balance" in html` (one route, substring) | **NO** (no cross-producer single-behavior assertion) | Q-16-gated -- recorded, not authored (which of blank-row/$0/omitted is correct is undecided; PT-02 note) |
| **D6-03** `balance[p]-balance[p-1] == subtotal.net` on one grid | F-002 Pair C / F-004 | `test_subtotal_values_correct:1819-1821 "$2,100"/"$1,600" in html` (LOOSE substring, raw-effective D1 only); no balance-delta-vs-subtotal | **NO** | PT-04 (Q-10-gated on canonical subtotal definition; equality shape pinnable, value gated) |
| **D6-04** calendar month-end == canonical balance-as-of-date | F-003 / W-277 | `test_month_end_balance:524-561 result.projected_end_balance == Decimal("4000.00")` -- calendar service in isolation, plain Paycheck/Bills only, no envelope+cleared entries, never compared to `/grid` | **NO** (does not exercise the `selectinload`-omission drift) | PT-01 (calendar flavor: month-end == grid for an account with Projected envelope + cleared entries) |
| **D6-05** `compute_committed_monthly` == `/obligations` (end_date<today filter) | D6-05 | `test_compute_committed_monthly_empty_lists:1473 == Decimal("0.00")` (empty-list edge only); no committed-vs-/obligations equality | **NO** | PT-20a |
| **D6-06** the two `_sum_*` bodies property-equal | F-010 | `grep -rni "_sum_remaining|_sum_all" tests/` -> 3 hits, comments only | **NO** | PT-20b |
| **D6-07** `round_money` golden-cents over the 24 bare sites | F-027/A-01 | `grep -rni "round_money|golden.*cent|quantize.*ROUND_HALF_UP" tests/` -> 5 hits, no golden-cents parametrization | **NO** | PT-20c |
| **D6-08** the 6 mirrors == `Transaction.effective_amount` | F-027 | `test_effective_amount_projected_returns_estimated:49-53 txn.effective_amount == Decimal("150.00")` (the model property in isolation; not the 6 mirror sites) | **NO** (mirrors never asserted == the property) | PT-20d |
| **D6-09** one status predicate | F-011 | `test_status_boolean_attributes:85-107` asserts `is_settled/is_immutable/excludes_from_balance` for all 6 statuses against an explicit expected matrix | **PARTIAL/YES for the predicate VALUES, NO for single-source consolidation**: it pins the `Status` model booleans (would catch a model-level drift) but does not assert the duplicated call-site predicates resolve through one helper | PT-20e (the consolidation guard; the value matrix already pinned) |
| **D6-10** SWR fraction/percent round-trip | F-042 | `grep -rni "round_trips|/ 100|\* 100" test_retirement_dashboard_service.py` -> `test_zero_swr_round_trips_as_decimal_zero` (zero only, display side) | **NO** for the general fraction<->percent round-trip | PT-18 (subsumes the zero case + a non-zero round-trip) |
| **S6-03** dispatcher-equivalence (savings-dashboard vs year-end loan path) | F-006/F-008 | `grep -rni "dispatcher|savings.*year_end" tests/` -> 1 route-comment match; `_compute_account_projections|_get_account_balance_map` -> 0 | **NO** | PT-05 |
| **B6-01** enforced `flask`-absent AST/import test on services | Invariant (services Flask-isolated) | `grep -rni "import flask|ast.parse" tests/` -> 0 matches | **NO** | PT-20f |
| **B6-02** enforced `Transfer`-absent test on the balance calculator (Invariant 5) | Transfer Invariant 5 | `test_balance_calculator_reflects_transfer_shadows:2277+` asserts the balance REFLECTS shadows; `test_shadow_periods_match_transfer_period_invariant_5:882+` asserts shadow periods track the parent -- neither asserts `balance_calculator` never imports/queries `budget.transfers` | **NO** for the mechanical B6-02 (the behavioral shadow tests exist; the AST/import guard does not) | PT-20g |

---

## Part 7.C -- proposed tests (report-only, never written)

Per schema 3.3 and hard rule 2 (`financial_calculation_audit_plan.md:696-704`):
prose/pseudocode only, hand-computed expected value with every intermediate
`Decimal` step shown, pinning the E-NN-correct value (explicitly NOT the current
divergent output). Q-gated axes that have no settled correct value are recorded
as deferred, NOT authored (PT-02; the D6-02 anchor-None note).

### PT-01 -- cross-page balance equality (the meta-gap)

- **Catches.** Symptoms #1 + #5 (checking facet), F-001 (checking + cross-page
  axes), F-002, F-005 (chart flavor), F-009, D6-04 (calendar flavor); the
  audit-plan:700-703 cross-page fixture.
- **Fixture sketch.** One user, one baseline scenario, one checking account,
  `current_anchor_balance = Decimal("1000.00")`, anchor set to the current
  period. In the current period add one Projected envelope expense
  `estimated_amount = Decimal("500.00")`, `actual_amount = NULL`, with three
  cleared debit entries summing `Decimal("462.34")`, no credit/uncleared
  entries (the `_entry_aware_amount` docstring grocery case). No other
  transactions. Render the current-period balance via every page-facing path:
  `/grid` index, `/savings` dashboard card, `/accounts/<id>` checking_detail,
  the net-worth per-account map, and (D6-04) `calendar_service.get_month_detail`
  for the same month.
- **Exact assertion.** All five surfaces return the SAME current-period balance,
  and it equals the entry-aware value:
  `grid == savings == accounts == net_worth == calendar == Decimal("962.34")`.
- **Hand-computed expected value.** Anchor (real checking balance, already
  reflects the cleared debits) `= Decimal("1000.00")`. Entry-aware expense
  contribution `= max(estimated - cleared_debit - sum_credit, uncleared_debit)
  = max(Decimal("500.00") - Decimal("462.34") - Decimal("0.00"),
  Decimal("0.00")) = Decimal("37.66")`. Current-period balance
  `= Decimal("1000.00") - Decimal("37.66") = Decimal("962.34")`. The divergent
  entries-absent paths compute `Decimal("1000.00") - effective_amount
  (= estimated Decimal("500.00")) = Decimal("500.00")` -- this is the value the
  test must REJECT (it double-subtracts the `Decimal("462.34")` already in the
  anchor). E-NN-correct pinned value: `Decimal("962.34")`, explicitly NOT
  `Decimal("500.00")`. Negative control (same fixture, zero cleared/credit
  entries): all surfaces == `Decimal("1000.00") - Decimal("500.00") =
  Decimal("500.00")` and still equal -- proves the assertion is not always-red.
- **Why it is not code.** Proposal only; authoring deferred to the post-audit
  remediation session (hard rule 2, audit-plan 10.6).

### PT-02 -- loan-balance 3-way (F-001 row F / F-003 loan axis) -- DEFERRED, not authored

- **Catches.** F-001/F-003 loan-base SOURCE divergence (stored
  `loan/dashboard.html:104` vs engine `proj.current_balance` vs schedule
  `year_end_summary_service.py:2079-2081`).
- **Why no pinned value.** Which base is canonical is **UNKNOWN, blocked on
  Q-11 / Q-15** (`03_consistency.md:332-340`). A proposed test cannot pin an
  E-NN-correct value to an undesignated producer (contract item 3.1: "you
  cannot pin a test to an undesignated producer"). Recorded as a gap; the test
  is authorable only after Q-11/Q-15 designate the canonical loan base.

### PT-03 -- anchor-None single behavior (D6-02 / Q-16) -- DEFERRED, not authored

- **Catches.** F-001 anchor-None four-behavior SCOPE divergence.
- **Why no pinned value.** Which of {blank row / $0-anchored projection /
  account omitted} is correct is **Q-16** (raised in F-001, unresolved). No
  E-NN-correct value exists to pin. Recorded; authorable only after Q-16.

### PT-04 -- same-page balance-delta == period subtotal (D6-03 / F-002 Pair C / F-004)

- **Catches.** F-002 Pair C, F-004 D1-D2, D6-03.
- **Fixture sketch.** One checking account, anchor period p0 balance
  `B0 = Decimal("1000.00")`. Period p1: one Projected income
  `Decimal("2000.00")`, one Projected envelope expense `estimated_amount =
  Decimal("500.00")` with cleared debit entries summing `Decimal("462.34")`.
  Render `/grid`; read the p1 balance-row value, the p0 balance-row value, and
  the p1 displayed subtotal (footer net).
- **Exact assertion.** `balance[p1] - balance[p0] == subtotal_net(p1)` AND both
  `== Decimal("1962.34")`.
- **Hand-computed expected value.** Entry-aware expense contribution
  `= max(Decimal("500.00") - Decimal("462.34"), Decimal("0.00")) =
  Decimal("37.66")`. Net change p1 `= income - entry_aware_expense =
  Decimal("2000.00") - Decimal("37.66") = Decimal("1962.34")`. `balance[p1] =
  B0 + Decimal("1962.34") = Decimal("2962.34")`; `balance[p1] - balance[p0] =
  Decimal("2962.34") - Decimal("1000.00") = Decimal("1962.34")`. The current
  grid subtotal (D1, raw `effective_amount`, `grid.py:274`) computes
  `Decimal("2000.00") - Decimal("500.00") = Decimal("1500.00")` -- the value the
  test must REJECT (gap `= Decimal("462.34")`, the cleared debit already in the
  balance row). **Q-10 note:** which subtotal definition is canonical (raw D1
  vs entry-aware D2) is blocked on Q-10 (`03_consistency.md:398-403`); the
  *equality invariant* `balance-delta == subtotal` is the unconditional target,
  the pinned scalar `Decimal("1962.34")` assumes the entry-aware canonicalization
  -- flag both numbers (`1962.34` invariant-correct vs `1500.00` current D1) and
  hold the scalar pin until Q-10 resolves the canonical subtotal.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-05 -- dual per-account dispatcher equivalence (S6-03 / F-001)

- **Catches.** F-001 dual-dispatch, S6-03, symptom #5.
- **Fixture sketch.** One user with a checking account (anchor + Projected
  txns), one HYSA, one fixed-rate loan with confirmed payments. Call both
  per-account dispatchers for the same `(period, scenario)`:
  `_compute_account_projections` (`savings_dashboard_service.py:294`) and
  `_get_account_balance_map` (`year_end_summary_service.py:2036`).
- **Exact assertion.** For every account, the two dispatchers return the SAME
  balance for the SAME period:
  `proj_map[acct.id][period] == balance_map[acct.id][period]` for all accounts.
- **Hand-computed expected value.** The equivalence holds independent of which
  dispatcher Q-15 designates canonical (the finding's dual-dispatch axis is a
  consistency requirement, not a value choice). Concrete anchor: checking
  account anchor `Decimal("1000.00")`, one Projected expense `effective_amount
  = Decimal("200.00")` -> both dispatchers must return
  `Decimal("1000.00") - Decimal("200.00") = Decimal("800.00")` for the current
  period; the test asserts equality, and pins `Decimal("800.00")` for the
  envelope-free case where entries-load does not bite (isolating the dispatcher
  axis from F-001's entries-load axis, which PT-01 owns). Negative control: a
  user with one account only -> trivially equal.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-06 -- ARM payment stability inside the fixed window (F-026 / symptom #4 / E-02)

- **Catches.** F-026, symptom #4, E-02.
- **Fixture sketch.** One 5/5 ARM loan, `original_principal = current_principal
  = Decimal("400000.00")`, `interest_rate = Decimal("0.06")`, `term_months =
  360`, `is_arm = True`, no RateHistory rows (inside the fixed window), no
  manual edits. Compute `get_loan_projection().summary.monthly_payment` at two
  consecutive `months_elapsed` values, e=24 and e=25 (e.g. by evaluating the
  site-7 path `as_of` two consecutive payment dates).
- **Exact assertion.** `payment(e=24) == payment(e=25) == Decimal("2398.20")`
  (constant inside the fixed window, no rate change).
- **Hand-computed expected value.** Correct constant amortization payment for
  the contractual triple `P = Decimal("400000.00")`, monthly rate
  `r = Decimal("0.06")/12 = Decimal("0.005")`, `n = 360`:
  `M = P * r / (1 - (1+r)^-n)`. `(1.005)^360`: `360 * ln(1.005) = 360 *
  0.00498754 = 1.79551`; `e^1.79551 = 6.02257`; `(1.005)^-360 = 1/6.02257 =
  0.166041`. `1 - 0.166041 = 0.833959`. `P*r = Decimal("400000.00") *
  Decimal("0.005") = Decimal("2000.00")`. `M = 2000.00 / 0.833959 =
  Decimal("2398.20")` (quantize HALF_UP). The divergent site-7 path re-
  amortizes the frozen `Decimal("400000.00")` over `n = 360 - e`: at e=24
  `n=336` -> `Decimal("2460.45")`; at e=25 `n=335` -> `Decimal("2463.27")`
  (`03_consistency.md:1985-1994`). The test must REJECT the creeping
  `2460.45 -> 2463.27` and pin the constant `Decimal("2398.20")`. Negative
  control: with a RateHistory row at month 60, `payment(e=60) !=
  payment(e=61)` MUST still hold (the existing `:1781` rate-boundary guard) --
  the stability assertion is scoped to inside the fixed window only.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-07 -- 16-site monthly_payment equivalence (F-013 / D6-01 / symptom #2)

- **Catches.** F-013, D6-01, symptom #2.
- **Fixture sketch.** One fixed-rate loan, `original_principal =
  current_principal = Decimal("250000.00")`, `interest_rate =
  Decimal("0.065")`, `term_months = 360`, no rate changes, viewed at a fixed
  `as_of` date. Collect the displayed monthly payment from every user-facing
  surface: loan-dashboard "Monthly P&I" (site 7), the amortization schedule
  first post-today row (site 3), the debt-strategy minimum payment (site 16),
  the recurring-transfer prefill (site 14), the debt-summary PITI
  (`savings_dashboard_service.py:846`), the refinance baseline.
- **Exact assertion.** Every surface returns the SAME payment:
  all `== Decimal("1580.17")`.
- **Hand-computed expected value.** `P = Decimal("250000.00")`,
  `r = Decimal("0.065")/12 = Decimal("0.00541667")`, `n = 360`. `(1+r)^360`:
  `360 * ln(1.00541667) = 360 * 0.00540204 = 1.944735`; `e^1.944735 = 6.99179`;
  `(1+r)^-360 = 0.143025`. `1 - 0.143025 = 0.856975`. `P*r =
  Decimal("250000.00") * Decimal("0.00541667") = Decimal("1354.17")`.
  `M = 1354.17 / 0.856975 = Decimal("1580.17")` (matches the suite's
  `test_standard_summary_known_values:787 == Decimal("1580.17")`). The
  divergence the test catches: site-3's per-row `n` (off-by-one vs site-7's
  calendar `n`) yields a few-dollar different value for a partially-elapsed
  loan; for a freshly-originated loan all sites agree (negative control --
  `months_elapsed=0` -> all 16 == `Decimal("1580.17")`, the test must PASS).
- **Why it is not code.** Proposal only (hard rule 2).

### PT-08 -- settled transfer decreases displayed principal (F-014/F-015 / symptom #3)

- **Catches.** F-014, F-015, symptom #3.
- **Fixture sketch.** One FIXED-rate loan account, `original_principal =
  current_principal = Decimal("312000.00")`, `interest_rate = Decimal("0.06")`,
  `term_months = 360`. Create a transfer whose loan-side shadow income equals
  the monthly payment, in the current period; assert the displayed principal
  BEFORE settling (negative control), then settle the shadow (status ->
  Settled) and assert the displayed principal (the `loan/dashboard.html:104`
  card value AND `get_loan_projection().current_balance`) strictly decreased
  to the engine-real value.
- **Exact assertion.** Before settle: `displayed_principal ==
  Decimal("312000.00")`. After one settled payment-transfer:
  `displayed_principal == proj.current_balance == Decimal("311689.41")` and
  `< Decimal("312000.00")`.
- **Hand-computed expected value.** Monthly payment `P = Decimal("312000.00")`,
  `r = Decimal("0.005")`, `n=360`: `P*r = Decimal("1560.00")`; from PT-06's
  `(1.005)^-360 = 0.166041`, `1 - 0.166041 = 0.833959`; `M = 1560.00 /
  0.833959 = Decimal("1870.59")`. Payment 1: interest `= 312000.00 * 0.005 =
  Decimal("1560.00")`; principal `= 1870.59 - 1560.00 = Decimal("310.59")`;
  new balance `= 312000.00 - 310.59 = Decimal("311689.41")`. The current code
  renders the STORED `Decimal("312000.00")` unchanged (no settle writer,
  `03_consistency.md:1205-1242`) -- the value the test must REJECT.
  E-NN-correct (E-03, fixed-rate engine walk): `Decimal("311689.41")`. **ARM
  facet note:** for `is_arm=True` the stored column is A-04-authoritative and
  has no settle-driven update path; whether the fix maintains the stored column
  on settle or re-amortizes from `proj.current_balance` is **Q-15/Q-17-gated**
  (`03_consistency.md:2022-2038`) -- the ARM variant of this test is recorded
  as deferred (no pinnable post-fix value until Q-17), the fixed-rate scalar
  above is unconditionally pinnable.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-10 -- escrow not misattributed to principal (F-017)

- **Catches.** F-017.
- **Fixture sketch.** One biweekly mortgage loan account, balance/anchor
  `Decimal("300000.00")`, monthly transfer `Decimal("2400.00")` = P&I
  `Decimal("1900.00")` + escrow `Decimal("500.00")`, monthly rate
  `Decimal("0.0054166")` (the F-017 worked rate). Run
  `calculate_balances_with_amortization` (path B) and assert the per-period
  principal equals the A-06-prepared value, not the raw split.
- **Exact assertion.** `principal_by_period[p] == Decimal("275.00")` (NOT
  `Decimal("775.00")`).
- **Hand-computed expected value.** Interest `= 300000.00 * 0.0054166 =
  Decimal("1625.00")`. A-06-correct: escrow is subtracted before the P&I split,
  so payment applied to the loan `= Decimal("1900.00")`; principal `= 1900.00 -
  1625.00 = Decimal("275.00")`. The divergent path B uses the raw transfer
  amount: principal `= 2400.00 - 1625.00 = Decimal("775.00")` -- escrow
  `Decimal("500.00")` wrongly counted toward principal (gap = the escrow).
  E-NN-correct (A-06, both layers apply): `Decimal("275.00")`.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-11 -- raw vs A-06-prepared interest series (F-018)

- **Catches.** F-018.
- **Fixture sketch.** One biweekly mortgage, balance `Decimal("300000.00")`,
  monthly transfer `Decimal("2400.00")` (P&I `Decimal("1900.00")` + escrow
  `Decimal("500.00")`), monthly rate `Decimal("0.005")`. Drive
  `_check_loan_paid_off` / `_compute_real_principal` (the raw `generate_schedule`
  callers) and assert the month-2 interest equals the A-06-prepared value.
- **Exact assertion.** Month-2 schedule interest from the raw caller
  `== Decimal("1498.00")` (NOT `Decimal("1495.50")`).
- **Hand-computed expected value.** A-06-prepared (payment = P&I
  `Decimal("1900.00")`): month-1 interest `= 300000.00 * 0.005 =
  Decimal("1500.00")`; principal `= 1900.00 - 1500.00 = Decimal("400.00")`;
  month-1 end balance `= 300000.00 - 400.00 = Decimal("299600.00")`; month-2
  interest `= 299600.00 * 0.005 = Decimal("1498.00")`. Raw (payment =
  `Decimal("2400.00")`): month-1 principal `= 2400.00 - 1500.00 =
  Decimal("900.00")`; end balance `= 300000.00 - 900.00 = Decimal("299100.00")`;
  month-2 interest `= 299100.00 * 0.005 = Decimal("1495.50")` -- under-reports
  by `Decimal("2.50")` and compounds. E-NN-correct (A-06): `Decimal("1498.00")`.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-12 -- single-debt strategy total_interest == calculate_summary (F-020)

- **Catches.** F-020 A-vs-C.
- **Fixture sketch.** One fixed loan, `original_principal =
  Decimal("200000.00")`, `interest_rate = Decimal("0.065")`, `term_months =
  360`, extra `Decimal("200.00")`/month, no confirmed payments (so
  `real_principal == original_principal`, isolating the A-vs-C definition from
  the A-06 axis). Run `calculate_summary` (path A) and a single-debt
  `calculate_strategy` (path C) with the same extra.
- **Exact assertion.** `strategy.total_interest == summary.total_interest_with_extra
  == Decimal("165011.16")`.
- **Hand-computed expected value.** The suite already pins the A-side
  decomposition (`test_summary_with_extra:300 summary.interest_saved ==
  Decimal("90074.66")` with the docstring `Decimal("255085.82") -
  Decimal("165011.16") = Decimal("90074.66")`). So the accelerated life-of-loan
  interest is `Decimal("165011.16")`. A one-debt `calculate_strategy` run with
  the same `original_principal`, rate, term, and extra must reproduce the same
  accelerated total: E-NN-correct `Decimal("165011.16")`. The divergence the
  test catches: C starts from site-16 `real_principal` and derives its minimum
  payment differently, so on a partially-paid loan C != A by dollars; on a
  zero-confirmed-payment loan they MUST coincide (negative control built in).
- **Why it is not code.** Proposal only (hard rule 2).

### PT-13 -- loan.py:968 banker's-vs-HALF_UP half-cent (F-021)

- **Catches.** F-021 ROUNDING (A-01 site `loan.py:968`).
- **Fixture sketch.** One refinance scenario contrived so the raw
  `original_interest - committed_interest` lands exactly on a half-cent:
  `original_interest = Decimal("X")`, `committed_interest = Decimal("Y")` with
  `X - Y = Decimal("1234.565")`. Render the refinance "interest saved" figure
  (path B, `loan.py:968`).
- **Exact assertion.** Displayed interest-saved `== Decimal("1234.57")` (NOT
  `Decimal("1234.56")`).
- **Hand-computed expected value.** `Decimal("1234.565").quantize(Decimal("0.01"))`
  with the module's banker's default (ROUND_HALF_EVEN) -> the digit before the
  5 is `6` (even), so it rounds DOWN to `Decimal("1234.56")` -- the current
  divergent output. `Decimal("1234.565").quantize(Decimal("0.01"),
  rounding=ROUND_HALF_UP)` -> `Decimal("1234.57")` -- the value path A
  (`amortization_engine.py:749`) produces and the A-01 verdict
  (`09_open_questions.md:37-62`) makes canonical. E-NN-correct (A-01,
  ROUND_HALF_UP project-wide): `Decimal("1234.57")`.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-14 -- months_saved vs break_even label-distinctness (F-022 / W-242)

- **Catches.** F-022, W-242.
- **Fixture sketch.** One loan with both an acceleration scenario (extra
  `Decimal("200.00")`/month) and a refinance scenario (`closing_costs =
  Decimal("4000.00")`, `monthly_savings = Decimal("150.00")`). Render the page
  that shows both the payoff acceleration "months saved" and the refinance
  "break-even months".
- **Exact assertion.** The rendered DOM places the two figures under distinctly
  labelled sections (`Break-even` heading vs `Months Saved` heading) such that
  they are not presented as the same quantity; the break-even value
  `== ceil(4000.00 / 150.00) = 27` and the acceleration months-saved value
  `== 54` appear in separate, distinctly-labelled blocks.
- **Hand-computed expected value.** `break_even_months = ceil(Decimal("4000.00")
  / Decimal("150.00")) = ceil(Decimal("26.6667")) = 27`. Acceleration:
  `months_saved = len(standard_schedule) - len(accelerated_schedule)`; for the
  F-022 worked example this is `54` (`03_consistency.md:1740-1746`). The test's
  E-NN-correct property: `27` and `54` are different quantities under different
  headings -- it must FAIL if both render under one "Months" slot without a
  distinguishing label (the W-242 user-misleading risk). This is a label-
  distinctness assertion, not a single-scalar pin (F-022 is DEFINITION_DRIFT;
  the gap is render-slot reuse, so the test pins the structural distinctness).
- **Why it is not code.** Proposal only (hard rule 2).

### PT-15 -- payoff_date A == B for an ARM (F-023)

- **Catches.** F-023 A-vs-B.
- **Fixture sketch.** One ARM loan, anchored `current_balance =
  Decimal("280000.00")`, stored `interest_rate = Decimal("0.065")`, remaining
  300 months, confirmed payments setting the anchor. Compute the displayed
  `summary.payoff_date` (path A, anchored committed schedule) and the target-
  date tool's baseline payoff (path B, `calculate_payoff_by_date`).
- **Exact assertion.** `payoff_date_A == payoff_date_B` (the same calendar
  month) for the same ARM loan-on-date.
- **Hand-computed expected value.** The absolute payoff date depends on the
  full anchored ARM schedule (origination, payment_day, the exact confirmed-
  payment set) and is not hand-pinnable to a single date without reproducing
  the entire schedule; the E-NN-correct INVARIANT is the EQUALITY: path B must
  gain an `anchor`/`payments` parameter so it reproduces path A's anchored ARM
  trajectory. The test pins `A == B`; it must FAIL while
  `calculate_payoff_by_date` has no anchor param and projects origination-
  forward (`03_consistency.md:1785-1812`). Negative control: a fixed-rate loan
  with no anchor offset -> A == B already (the existing fixed-rate behavior),
  the equality must hold.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-16 -- off-engine DTI gross with an applicable raise (F-032)

- **Catches.** F-032.
- **Fixture sketch.** One `SalaryProfile`, `annual_salary =
  Decimal("104000.00")`, `pay_periods_per_year = 26`; one recurring
  `SalaryRaise`, `percentage = Decimal("0.03")`, effective and reached for the
  viewed period; one debt totalling `Decimal("2400.00")`/month. Render the
  `/savings` dashboard DTI card (the off-engine
  `savings_dashboard_service.py:263-266` path) and compute the canonical
  `calculate_paycheck` gross for the same profile/period.
- **Exact assertion.** `ds["gross_monthly_income"] == Decimal("8926.67")` (the
  post-raise HALF_UP canonical), NOT `Decimal("8666.67")`; and `ds["dti_ratio"]`
  computed against `Decimal("8926.67")`.
- **Hand-computed expected value.** Canonical A: `_apply_raises` -> `104000.00 *
  1.03 = Decimal("107120.00")` (quantize HALF_UP); `gross_biweekly =
  (Decimal("107120.00") / 26).quantize(Decimal("0.01"), ROUND_HALF_UP) =
  Decimal("4120.00")`; `gross_monthly = (Decimal("4120.00") * 26 /
  12).quantize(Decimal("0.01"), ROUND_HALF_UP) = Decimal("8926.67")`
  (`107120.00 / 12 = 8926.6667`). Off-engine B: `salary_gross_biweekly =
  (Decimal("104000") / 26).quantize(Decimal("0.01")) = Decimal("4000.00")`
  (RAW, no raise, banker's default); `gross_monthly = (Decimal("4000.00") * 26
  / 12) = Decimal("8666.67")`. E-NN-correct (canonical, post-raise, HALF_UP):
  `Decimal("8926.67")`; the test must REJECT `Decimal("8666.67")`.
  Corresponding DTI: `2400.00 / 8926.67 * 100 = Decimal("26.9")` correct vs
  `2400.00 / 8666.67 * 100 = Decimal("27.7")` divergent. Negative control: a
  profile with NO `SalaryRaise` -> off-engine == canonical (the existing
  `test_dti_with_salary` $6,500.00 case), the equality must hold.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-17 -- calibration-path SS wage-base cap (F-037)

- **Catches.** F-037.
- **Fixture sketch.** One employee, `annual_salary = Decimal("312000.00")`,
  `pay_periods_per_year = 26` (per-period gross `Decimal("12000.00")`);
  `FicaConfig.ss_wage_base = Decimal("184500.00")`, `ss_rate =
  Decimal("0.062")`; calibration ACTIVE with `effective_ss_rate =
  Decimal("0.062")`. Compute the full 26-period SS via the calibrated path
  (`apply_calibration`), sum the year.
- **Exact assertion.** `total_calibrated_ss == Decimal("11439.00")` (NOT
  `Decimal("19344.00")`); per-period: periods 1-15 `Decimal("744.00")`, period
  16 `Decimal("279.00")`, periods 17-26 `Decimal("0.00")`.
- **Hand-computed expected value.** Per-period gross `= Decimal("312000.00") /
  26 = Decimal("12000.00")`. Cumulative after period 15 `= 15 * 12000.00 =
  Decimal("180000.00") < Decimal("184500.00")`; after period 16 `= 16 *
  12000.00 = Decimal("192000.00") > Decimal("184500.00")`. Periods 1-15: SS
  `= (12000.00 * 0.062).quantize(Decimal("0.01"), ROUND_HALF_UP) =
  Decimal("744.00")` each -> `15 * 744.00 = Decimal("11160.00")`. Period 16
  (partial crossing): `ss_taxable = 184500.00 - 180000.00 =
  Decimal("4500.00")`; SS `= (4500.00 * 0.062).quantize(...) =
  Decimal("279.00")`. Periods 17-26: cumulative >= cap -> SS `=
  Decimal("0.00")`. Year total `= 11160.00 + 279.00 + 0.00 =
  Decimal("11439.00")` `= Decimal("184500.00") * Decimal("0.062")`. The
  calibrated path (no cap) charges `12000.00 * 0.062 = Decimal("744.00")` all
  26 periods -> `26 * 744.00 = Decimal("19344.00")`, `Decimal("7905.00")` over
  -- the value the test must REJECT. E-NN-correct (IRS SS wage-base cap, hard
  invariant `02_concepts.md:1715-1719`): `Decimal("11439.00")`. Negative
  control: a calibrated earner WELL BELOW the cap (e.g. `annual_salary =
  Decimal("52000.00")`) -> calibrated SS == uncapped == bracket SS every
  period (capped==uncapped there), the test must PASS.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-18 -- SWR slider == gap math + zero-return weighting (F-042 / D6-10)

- **Catches.** F-042 (both live sub-defects), D6-10 (SWR fraction/percent
  round-trip).
- **Fixture sketch.** Part 1 (SWR cross-anchor): one user, `UserSettings.
  safe_withdrawal_rate = Decimal("0.0000")` (explicit zero), retirement
  accounts projecting `gap_result.projected_total_savings =
  Decimal("1200000.00")`. Compute both `compute_slider_defaults` (slider) and
  `compute_gap_data` (gap math + `chart_data["investment_income"]`). Part 2
  (zero-return weighting): two retirement accounts, each `current_balance =
  Decimal("100000.00")`, account X `assumed_annual_return = Decimal("0.0000")`,
  account Y `assumed_annual_return = Decimal("0.0700")`; compute the slider's
  `current_return`.
- **Exact assertion.** Part 1: `slider["current_swr"] == Decimal("0.00")` AND
  `chart_data["investment_income"] == "0.00"` (the gap math must use the SAME
  0% the slider displays), NOT `"4000.00"`. Part 2: `slider["current_return"]
  == Decimal("3.50")`, NOT `Decimal("7.00")`.
- **Hand-computed expected value.** Part 1: slider
  `= (Decimal("0.0000") * 100).quantize(Decimal("0.01")) = Decimal("0.00")`
  (the `is None`-correct display). Gap math E-NN-correct: it must apply the
  same explicit-zero SWR, so `investment_income = (Decimal("1200000.00") *
  Decimal("0.00") / 12).quantize(Decimal("0.01")) = Decimal("0.00")` -> "0.00".
  The divergent `compute_gap_data:220` `or "0.04"` makes `Decimal("0.0000")`
  falsy -> swr `Decimal("0.04")` -> `investment_income = (1200000.00 * 0.04 /
  12) = Decimal("4000.00")` -- the $4,000/mo phantom income the test must
  REJECT. Part 2: true balance-weighted return `= (100000.00 * 0.0000 +
  100000.00 * 0.0700) / (100000.00 + 100000.00) = 7000.00 / 200000.00 =
  Decimal("0.0350") = Decimal("3.50")%`. The divergent `:321` truthiness skips
  X (`Decimal("0")` falsy): `weighted_return = 100000.00 * 0.07 = 7000.00`,
  `total_balance = 100000.00` -> `current_return = (7000.00 / 100000.00 *
  100).quantize(Decimal("0.01")) = Decimal("7.00")` -- the value the test must
  REJECT. E-NN-correct (coding-standards `0` vs `None` rule;
  `is not None` / explicit-zero semantics): `"0.00"` and `Decimal("3.50")`.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-19 -- employer match: card == chart == year-end at the limit (F-043 / F-055)

- **Catches.** F-043, F-055.
- **Fixture sketch.** One `match`-type employer, `match_percentage =
  Decimal("1.00")`, `match_cap_percentage = Decimal("0.06")`, `gross_biweekly =
  Decimal("4000.00")`; employee `periodic_contribution = Decimal("1000.00")`;
  `annual_contribution_limit` set so that at the LAST limit-binding period
  `remaining_limit = Decimal("100.00")`. Render the dashboard card
  (`investment.py:188`, uncapped), the growth-chart employer line
  (`growth_engine.py:259-265`, capped), and the year-end
  `year_summary_employer_total` (sums the capped value) for that period/year.
- **Exact assertion.** `card_employer == chart_employer ==
  year_end_employer_for_period` (the three surfaces equal for the same
  account/period).
- **Hand-computed expected value.** `matchable_salary = (Decimal("4000.00") *
  Decimal("0.06")).quantize(Decimal("0.01"), ROUND_HALF_UP) = Decimal("240.00")`.
  Card (uncapped): contribution fed `= Decimal("1000.00")`; `matched =
  min(1000.00, 240.00) = Decimal("240.00")`; employer `= 240.00 * 1.00 =
  Decimal("240.00")`. Chart/year-end (capped): contribution fed `=
  min(1000.00, remaining_limit Decimal("100.00")) = Decimal("100.00")`;
  `matched = min(100.00, 240.00) = Decimal("100.00")`; employer `= 100.00 *
  1.00 = Decimal("100.00")`. The two surfaces produce `Decimal("240.00")` vs
  `Decimal("100.00")` for the same period -- the divergence. **Value note:**
  whether the canonical employer figure at the limit is the capped
  `Decimal("100.00")` or the uncapped `Decimal("240.00")` is a remediation
  CHOICE, not a settled E-NN (`03_consistency.md:3680-3688`: "whether the card
  SHOULD show capped or uncapped is a remediation choice"). The unconditional
  E-NN-correct INVARIANT is the THREE-WAY EQUALITY; the test pins the equality
  and flags both candidate scalars (`100.00` chart/year-end vs `240.00` card)
  -- the pinned scalar is deferred to the remediation decision, the equality is
  not. Negative control: an account whose `periodic_contribution` is well below
  any binding limit -> capped == uncapped == `Decimal("240.00")` on all three
  surfaces, the equality must PASS.
- **Why it is not code.** Proposal only (hard rule 2).

### PT-20 -- Phase 6 structural / mechanical equivalence guards (D6-05..D6-09, B6-01/B6-02)

Grouped; each is a property/structural guard with no single hand-computed
scalar (they assert equivalence of two code paths or absence of an import).
Report-only.

- **PT-20a (D6-05).** Fixture: a user with one obligation whose template
  `end_date < today` and one with `end_date >= today`. Assert
  `savings_goal_service.compute_committed_monthly(...) == sum(rows shown on
  /obligations)` -- both must apply the identical `end_date < today` filter.
  Expected: the two equal (same monthly figure, e.g. only the
  `end_date >= today` obligation's monthly amount contributes); the test fails
  if `/obligations` includes an ended obligation the committed-monthly sum
  excludes (or vice versa). No scalar pin -- the invariant is equality of the
  two filtered sums.
- **PT-20b (D6-06).** Property test: for a transaction set with ZERO settled
  transactions, `_sum_remaining(period) == _sum_all(period)` (the two `_sum_*`
  bodies must agree when there is nothing settled to exclude); for a set WITH
  one settled `Decimal("100.00")` transaction, `_sum_all - _sum_remaining ==
  Decimal("100.00")`. Pinned delta: `Decimal("100.00")` (the settled amount).
- **PT-20c (D6-07).** Golden-cents parametrization: for each of the 24
  `round_money`/bare-`quantize` sites, feed a half-cent input
  (`Decimal("1.005")`, `Decimal("2.675")`, `Decimal("-1.005")`) and assert the
  result `== Decimal("1.01")`, `Decimal("2.68")`, `Decimal("-1.01")`
  respectively (ROUND_HALF_UP, the A-01 canonical mode). Pins the project-wide
  rounding mode at every site; fails on any banker's-default residue.
- **PT-20d (D6-08).** For one transaction in each status (Projected, Done,
  Credit, Cancelled, Settled, Received), assert each of the 6 mirror sites
  returns exactly `txn.effective_amount` (the model property). Pinned per
  status, e.g. Projected `estimated_amount = Decimal("150.00")` -> every mirror
  `== Decimal("150.00")`; Cancelled -> every mirror `== Decimal("0.00")`. Fails
  if any mirror diverges from the property.
- **PT-20e (D6-09).** The status-predicate VALUE matrix is already pinned
  (`test_status_boolean_attributes:85-107`, the full 6x3 expected table). The
  residual proposal is the CONSOLIDATION guard: an AST/import test asserting
  every `excludes_from_balance`/`is_settled` decision in `app/services` and
  `app/routes` resolves through the single `Status` predicate (no inline
  `status.name == "..."` or duplicated boolean). No scalar; structural.
- **PT-20f (B6-01).** AST test over `app/services/*.py`: parse each module,
  assert no `import flask`, no `from flask import ...`, no reference to
  `request`/`session`/`g`. No scalar; the assertion is the empty set of Flask
  references (the services-isolated invariant; B6-01 explicitly recommends
  converting the prose contract to a mechanical one).
- **PT-20g (B6-02).** AST test over `app/services/balance_calculator.py`:
  assert no import of, or query against, the `Transfer` model / `budget.transfers`
  (Transfer Invariant 5: the balance calculator queries ONLY
  `budget.transactions`). The behavioral shadow tests
  (`test_balance_calculator_reflects_transfer_shadows`,
  `test_shadow_periods_match_transfer_period_invariant_5`) cover the runtime
  effect; this is the mechanical guard they do not provide. No scalar;
  structural.
- **Why it is not code.** All proposals only; authoring deferred to the
  post-audit remediation session (hard rule 2, audit-plan 10.6).

---

### Part 7.B / 7.C completeness

All 20 `DIVERGE` findings (F-001/002/003/005/009/013/014/015/017/018/020/021/
022/023/026/032/037/042/043/055) have a Part 7.B record with a this-session
catching-test grep result and a "would it have caught it" verdict (every one:
**NO**, none caught by an existing test; the lone near-miss downgraded per
contract item 3). The 5 symptom regression targets (#1-#5) each have a record
with the E-04 equivalence target, the grep result, and an encoded negative
control. The explicit cross-page meta-gap has its own record with the three
audit-plan-mandated greps re-run this session (all 0 matches). All Phase 6
D6-01..D6-10 / S6-03 / B6-01 / B6-02 implications have a record. Part 7.C
proposes PT-01..PT-20 report-only with hand-computed expected values pinning
the E-NN-correct (non-divergent) value; PT-02, PT-03, and the Q-15/Q-17 ARM
facet of PT-08, the Q-10 scalar of PT-04, and the remediation-choice scalar of
PT-19 are recorded as **deferred/not authored** because no settled E-NN value
exists until the governing open question resolves (the equality invariant,
where one exists independent of the question, is still pinned). No test file
was opened, modified, run, or created; source, tests, and migrations untouched.


---

## Part 7.F -- verification and consolidation gate (P7-f, trust-but-verify capstone)

No new coverage analysis. Every claim below was re-resolved to live test
source this session (grep/Read), not recalled and not trusted from a prior
session's citation. `pytest` was not invoked; no file under `tests/` was
created, modified, or deleted.

### 7.F.1 Spot-check (>= 15 cited claims, mixed verdict types)

18 claims drawn across Part 7.A (all four slices), Part 7.B (divergence
records + meta-gap), and the assumption-contradiction findings, mixing
`COVERED`, `NO-PINNED-TEST`, `LOOSE-ONLY`, the conditional anti-coverage
flags, and the "would it have caught it" NO determinations. Each re-resolved
to the live test file:line this session.

| # | Claim (07_test_gaps.md cite) | Verdict class | Re-resolved against (this session) | Result |
| --- | --- | --- | --- | --- |
| 1 | C1 `checking_balance` `test_credit_excluded_from_balance:159 == Decimal("1000.00")` | COVERED | `tests/test_services/test_balance_calculator.py:159` `assert balances[periods[0].id] == Decimal("1000.00")` (def @126) | PASS |
| 2 | C1 `test_grocery_bug_scenario_after_true_up:1658 == Decimal("4962.34")` | COVERED (entries-loaded branch) | `tests/test_services/test_balance_calculator_entries.py:1658` `assert balances[seed_periods[1].id] == Decimal("4962.34")`; hand-arith comment @1657 (def @1614) | PASS |
| 3 | Slice-1 near-miss `test_checking_detail_matches_grid_balance:2211` is entries-absent single-page substring | NO (downgraded near-miss) | `tests/test_routes/test_accounts.py:2211` (def), `:2259-2268` query has no `.options(selectinload)`, `:2286-2287` `"${:,.0f}".format(float(calc_balance))` substring | PASS |
| 4 | C1/slice2 `monthly_payment` `test_known_30yr:33 == Decimal("1264.14")` | COVERED | `tests/test_services/test_amortization_engine.py:33` `assert payment == Decimal("1264.14")` for `Decimal("200000"),Decimal("0.065"),360` | PASS |
| 5 | Slice-2 `loan_remaining_months` pinned `300/0/360` contradicts PA-28 | COVERED + finding-against-assumption | `tests/test_services/test_amortization_engine.py:484` `assert result == 300`, `:497 == 0`, `:510 == 360` (explicit-`as_of` cases) | PASS |
| 6 | Slice-2 `loan_principal_real` NO-PINNED-TEST: `_compute_real_principal` absent | NO-PINNED-TEST (absence) | `grep -rn "_compute_real_principal" tests/` -> exit 1, 0 matches | PASS |
| 7 | Slice-2 `dti_ratio` LOOSE-ONLY: with-salary path is `is not None`/`isinstance`; only `==` is trivial zero | LOOSE-ONLY | `tests/test_services/test_savings_dashboard_service.py:1297` `is not None`, `:1298 isinstance(...,Decimal)`, `:1300 gross_monthly_income == Decimal("6500.00")`, `:1338 dti_ratio == Decimal("0.0")` | PASS |
| 8 | Slice-3 `fica` COVERED: `:472 == Decimal("143.08")`; SS-cap branches pinned | COVERED | `tests/test_services/test_paycheck_calculator.py:472` `assert result.social_security == Decimal("143.08")`; `TestFICADirectBoundary` @1086, `test_ss_at_cap_zero` @1100 `result["ss"] == Decimal("0.00")`, partial `== Decimal("0.06")` | PASS |
| 9 | Slice-3 `paycheck_net` relationship invariant pinned (`test_net_pay_formula:513`) | COVERED (consistency invariant) | `tests/test_services/test_paycheck_calculator.py:499 == Decimal("1854.22")`; `:513 assert r.net_pay == expected_net` = full `gross - pre - fed - state - ss - med - post` `.quantize(...,ROUND_HALF_UP)` (def @487) | PASS |
| 10 | Slice-3 `state_tax` `:467 == Decimal("103.85")` | COVERED | `tests/test_services/test_paycheck_calculator.py:467` `assert result.state_tax == Decimal("103.85")`, hand-arith comment @465-466 | PASS |
| 11 | Slice-4 `contribution_limit_remaining` NO-PINNED-TEST: the `:875-877` pin is the growth-engine field, a different producer | NO-PINNED-TEST (grep-name collision) | `tests/test_services/test_growth_engine.py:875-877` `assert result[i].contribution_limit_remaining == Decimal("900"/"700"/"400")` -- the `ProjectionResult` field, not `investment.py:173-181` (def @854) | PASS |
| 12 | Slice-4 `transfer_amount_computed` NO-PINNED-TEST: `$269.23` is a POSTed value | NO-PINNED-TEST | `tests/test_routes/test_investment.py:699` `"amount": "269.23"` posted; `:715 assert tpl.default_amount == Decimal("269.23")` (def @677) -- user passthrough, not the derivation | PASS |
| 13 | Slice-4 `cash_runway_days` NO-PINNED-TEST: only None/0 guards | NO-PINNED-TEST | `tests/test_services/test_dashboard_service.py:422` `cash_runway_days is None` (def @415), `:431 == 0` (def @424) -- guard branches only | PASS |
| 14 | Slice-4 pension COVERED, contradicts PA-30 | COVERED + finding-against-assumption | `tests/test_services/test_pension_calculator.py:63 annual_benefit == Decimal("38387.50")`, `:64 monthly == Decimal("3198.96")`, `:146 == Decimal("606.80")` | PASS |
| 15 | 7.B F-013 "would it have caught it? NO": `:747` is engine-internal projection==standalone | NO (2-of-16 surfaces) | `tests/test_services/test_amortization_engine.py:747` `assert projection.summary.monthly_payment == standalone_summary.monthly_payment` -- identical engine inputs, not cross-site triples | PASS |
| 16 | 7.B F-026 "NO": `:1781` asserts payment CHANGES at boundary; `:2842` pins a single instant | NO | `tests/test_services/test_amortization_engine.py:1781-1783` `assert schedule[11].payment != schedule[12].payment` (+ 23/24, 35/36); `:2842 assert proj.summary.monthly_payment == expected_payment` (one instant) | PASS |
| 17 | 7.B cross-page meta-gap: the three audit-plan-mandated greps return 0 matches | NO (absence, meta-gap) | re-ran this session: grid-vs-accounts -> exit 1; grid-vs-savings -> exit 1; `grid.*==.*savings\|checking.*==.*accounts` -> exit 1 (all 0 matches) | PASS |
| 18 | Slice-4 `effective_amount` COVERED: 4-tier property pinned | COVERED | `tests/test_ref_cache.py:168` `assert txn.effective_amount == Decimal("487.00")` (tier-3, def @141); `:194 == Decimal("500.00")` (tier-4, def @170) | PASS |

**Spot-check pass count: 18 / 18 (100%).** Threshold 100% met. No
citation drifted; no session reopened.

### 7.F.2 Concept-completeness reconciliation

All 47 controlled-vocabulary concepts have exactly one Part 7.A record with a
coverage verdict (`grep -cE "^### Concept [0-9]+:" 07_test_gaps.md` -> 47;
verdict roll-up tables enumerate 8 + 13 + 8 + 18 = 47, the slice-4
`year_summary_jan1/dec31` rows confirmed present at lines 2659-2660). The two
`PRIMARY PATH: UNKNOWN` concepts are recorded `PRODUCER-UNKNOWN-CANNOT-PIN`,
not skipped: `period_subtotal` (`02_concepts.md:323`, Q-10) and
`loan_principal_displayed` (`02_concepts.md:882`, Q-11). No concept silently
dropped. **VERDICT: PASS.**

### 7.F.3 Divergence-completeness reconciliation

Re-grepped `03_consistency.md` for `Verdict:` this session: 20 findings carry
a `DIVERGE` verdict word (F-001, F-002, F-003, F-005, F-009, F-013, F-014,
F-015, F-017, F-018, F-020, F-021, F-022, F-023, F-026, F-032, F-037, F-042,
F-043, F-055). (The `grep -ic DIVERGE` raw count of 21 includes F-025 line
1923, whose verdict word is `AGREE`; the literal substring "divergence"
appears in its body -- it is not a DIVERGE finding. Excluded correctly.) This
exactly matches the Part 7.B canonical 20-set.

- All 20 `DIVERGE` findings have a Part 7.B `### F-NNN ... -- DIVERGE` record
  (headers verified at lines 2750-3210), each with a this-session
  catching-test grep result and a "would it have caught it" verdict (every
  one: **NO**; the single near-miss downgraded per contract item 3).
- The 5 symptom regression targets (#1-#5) each have a Part 7.B record with
  the per-symptom E-04 equivalence target, the grep result, and an encoded
  negative control (the `Symptom regression targets` table, line 3221).
- The explicit cross-page balance-equality meta-gap has its own record with
  the three audit-plan-mandated greps re-run this session, all 0 matches
  (line 3231; re-confirmed in spot-check #17).
- The Phase 6 equivalence implications D6-01..D6-10, S6-03, B6-01, B6-02 each
  have a row in the Part 7.B Phase-6 table (line 3261) with a this-session
  catching-test search and Caught? verdict; D6-09 is the only PARTIAL/YES (the
  status-predicate VALUE matrix is pinned by `test_status_boolean_attributes`;
  the single-source consolidation guard is not -- recorded, PT-20e).

**VERDICT: PASS.**

### 7.F.4 Anti-coverage roll-up (PINNED-AGAINST-DIVERGENT-BEHAVIOR)

Flag-only. No test file was opened for modification, annotation, or
proposal-in-place (contract item 4). CLAUDE.md rule 5 forbids "fixing" any
test that breaks when the producer is correctly fixed; this table is the
single most consequential Phase 7 output for the developer and Phase 8.

**No concept in Part 7.A carries an unconditional
`PINNED-AGAINST-DIVERGENT-BEHAVIOR` verdict.** Every verdict roll-up row was
checked this session (`grep -nE "^\| \`[token]\` \|" 07_test_gaps.md`, 47
rows): zero assign that verdict
(`grep -E "^\| ... \| .*PINNED-AGAINST" 07_test_gaps.md` -> exit 1, none).
Every one of the 25 in-prose `PINNED-AGAINST-DIVERGENT-BEHAVIOR` mentions in
the census is either the schema definition (lines 12, 110) or an explicit
**not**-against-divergent determination: the pinned tests for the proven
`DIVERGE`s (F-002, F-013/F-026, F-021, F-032, F-037, F-042, F-043, F-055,
F-017, F-018, F-015) exercise the *correct* engine/branch at an input where
divergent and correct coincide, or pin only the symptom-free display side --
they pin the E-NN-correct value and would NOT fail when the producer is
fixed. The divergences are *unexercised*, not *pinned-against*. That is why
the operative gaps are `NO-PINNED-TEST` / `COVERED-but-cross-page-UNTESTED`
rather than anti-coverage.

Three **conditional / latent** flags remain (flagged only; no test file
opened) -- they are NOT yet anti-coverage but become so on a specific open-
question resolution, and Phase 8 must treat them as CRITICAL inputs:

| Flag | Tests (verbatim cites, Read in Part 7.A) | Becomes PINNED-AGAINST-DIVERGENT-BEHAVIOR if | Authority |
| --- | --- | --- | --- |
| `debt_total` dual-base | `test_savings_dashboard_service.py::test_debt_summary_single_loan:947 == Decimal("1000.00")`, `test_debt_summary_multiple_loans_weighted_rate`, `test_debt_summary_excludes_paid_off`, `test_debt_summary_all_paid_off` (all hard-pin the STORED `current_principal` base) vs `test_year_end_summary_service.py::test_debt_progress_uses_amortization:1280-1282` (hard-pins the AMORTIZATION-SCHEDULE base) | Q-15 designates the canonical `debt_total` as the schedule/engine base (A-04 direction) -- then the four stored-base pins lock the un-maintained column symptom #3 says does not move | 07_test_gaps.md:440-464; F-008 UNKNOWN; Q-15 |
| `goal_progress` GP2 | `test_dashboard_entries.py::test_bill_dict_entry_fields_tracked:322 entry_remaining == Decimal("170.00")`, `:394 == Decimal("-50.00")`; `test_entry_service.py::test_compute_remaining_under_budget:967 == Decimal("170.00")` (lock the CURRENT estimated-base interpretation) | Q-08 resolves the entry-progress base to interpretation (2) (actual-spend) -- then these pins lock the superseded estimated base | 07_test_gaps.md:2265-2284; F-046 GP2 UNKNOWN; Q-08 |
| `federal_tax` legacy F-040 | `test_tax_calculator.py::TestLegacyWrapper:518 calculate_federal_tax(...) == Decimal("5700.00")`, `:522`/`:526 == Decimal("0")` | F-040 remediation deletes the DEAD legacy `calculate_federal_tax` -- then these pins block the deletion (CLAUDE.md rule 5 tension on dead-code removal) | 07_test_gaps.md:1488-1495; F-040 DEAD_CODE |

These three are flag-only and Q-/remediation-gated; recorded so Phase 8 does
not record a green bar as coverage and so the developer is warned before a
"correct" fix turns a passing test red. No unconditional anti-coverage
finding exists in this audit.

### 7.F.5 Acceptance gate G1-G9 (phase7_plan.md section 5)

| Gate | Criterion | Evidence | Verdict |
| --- | --- | --- | --- |
| **G1** | `07_test_gaps.md` exists, non-empty, three parts, every record carries every section-3 element | File 3812+ lines pre-gate; Part 7.A (47 concept records, each with Canonical producer / Pinned-value tests / Relationship tests / Pinned-loose / E-NN-consistency / Consistency-invariant / Edge-cases / Coverage verdict / Independent note), Part 7.B (20 DIVERGE + 5 symptom + meta-gap + D6/S6/B6), Part 7.C (PT-01..PT-20) | PASS |
| **G2** | Every coverage claim cites a `tests/...::test` Read this session with the asserting line quoted; every absence claim pastes the grep; no prior-test-audit-doc source | Per-concept records quote asserting lines; cross-cutting absence greps pasted per slice; spot-check 7.F.1 re-resolved 18 sites to live source 18/18; no `test_audit_report.md`/`test_remediation_plan.md`/`test_audit_phase0_phase1.md` cited (grep of the doc for those names -> 0) | PASS |
| **G3** | Every pinned/loose classification carries the quoted assertion line; none rests on a test name | Each Pinned-value-tests bullet quotes the `assert ... == Decimal(...)` line; loose calls quote `is not None`/`isinstance`/`> ZERO`/substring; spot-checks 7 and 13 confirm loose calls quote the actual weak assertion | PASS |
| **G4** | Every `DIVERGE` concept whose test pins the divergent value -> `PINNED-AGAINST-DIVERGENT-BEHAVIOR` with both numbers + F-NN; anti-coverage table exists and is flag-only | No test pins a divergent value (7.F.4: divergences unexercised, pins are E-NN-correct); the conditional/latent table (7.F.4) shows both numbers + the F-NN/Q gate per row; flag-only, no test file opened | PASS |
| **G5** | Spot-check >= 15 sites (mixed verdict types), 100% resolve; table + count shown | 7.F.1: 18 sites, COVERED + NO-PINNED-TEST + LOOSE-ONLY + downgraded-NO + finding-against-assumption mix, 18/18 PASS | PASS |
| **G6** | All 47 concepts have a 7.A verdict; every DIVERGE + 5 symptom + cross-page meta-gap + Phase 6 implications have a 7.B entry; nothing silently dropped | 7.F.2 (47/47) + 7.F.3 (20/20 DIVERGE, 5/5 symptom, meta-gap, D6-01..D6-10/S6-03/B6-01/B6-02) | PASS |
| **G7** | Every Part 7.C proposal report-only with hand-computed expected value + arithmetic, pinning the E-NN-correct (non-divergent) value; no test file created/modified/deleted; `pytest` never run | PT-01..PT-19 each carry the intermediate-`Decimal` arithmetic and an explicit "must REJECT <divergent value>" / pin the E-NN-correct value; PT-02/PT-03 and the Q-gated scalars of PT-04/PT-08/PT-19 recorded deferred-not-authored; `git status` (7.F.7) shows no `tests/` change; pytest not invoked this session | PASS |
| **G8** | No new auditor-invented expectation in `09_open_questions.md`; A-26 tail carried unchanged; open-question-blocked concepts recorded `BLOCKED-ON-OPEN-QUESTION` | `09_open_questions.md` not modified (git status 7.F.7); A-26 carried unchanged in 7.F.6; 33 `BLOCKED-ON-OPEN-QUESTION` recordings for Q-08/Q-10/Q-11/Q-13/Q-15/Q-16-gated concepts, none guessed | PASS |
| **G9** | `git status` shows only `docs/audits/financial_calculations/`; no source/test/migration/template/JS file touched | 7.F.7: `git status --porcelain` -> single line `?? docs/audits/financial_calculations/07_test_gaps.md` | PASS |

### 7.F.6 Handoff to Phase 8 / Phase 9

**Phase 8 (findings / severity).** Every non-`COVERED` Part 7.A verdict and
every Part 7.B "would it have caught it: NO" feeds a Phase 8 finding; severity
is assigned there, not here. CRITICAL inputs:

- The cross-page balance-equality meta-gap (symptoms #1 and #5 have zero
  falsifying regression anchor; PT-01) -- the developer's two most concrete
  reported wrong-dollar bugs cannot be regression-locked.
- `loan_principal_real` `NO-PINNED-TEST` (symptom #3, F-014): the strict
  principal-decrease-per-settled-transfer target has no coverage at all
  (PT-08, fixed-rate scalar pinnable; ARM facet Q-15/Q-17-gated).
- F-013/F-026 (`monthly_payment`, symptoms #2/#4): cross-site and ARM
  in-window-stability invariants UNTESTED; the only ARM consecutive-row test
  asserts the *inverse* (PT-06, PT-07).
- F-037 (`fica` calibration-path SS-cap bypass): proven DIVERGE with zero
  catching test on either axis; a calibrated high earner ships overstated
  FICA / understated net pay (PT-17).
- The 7.F.4 conditional/latent anti-coverage flags (`debt_total` dual-base
  Q-15; `goal_progress` GP2 Q-08; `federal_tax` F-040 dead-code) -- Phase 8
  must not record these green bars as coverage.

**Phase 9 (open questions).** No new auditor-invented "obvious" expectation
was added. Concepts blocked on Q-08/Q-10/Q-11/Q-13/Q-15/Q-16 are recorded
`BLOCKED-ON-OPEN-QUESTION` / `PRODUCER-UNKNOWN-CANNOT-PIN`, not guessed; their
pinnable-value status is gated on the developer's adjudication, not on Phase
7.

**A-26 tail -- carried forward unchanged.** The
`auth.user_settings.estimated_retirement_tax_rate` NULL-semantics question
(`05_symptoms.md:1731-1740`, `06_dry_solid.md:2218-2225`,
`phase7_plan.md:71-78`) is a developer-adjudicated open question, not a test
gap with a known-correct assertion. It is NOT one of the 47
controlled-vocabulary concepts, so its absence from the Part 7.A census is
correct, not an omission. Its Phase-7 test state is
`BLOCKED-ON-OPEN-QUESTION` ("no pinned assertion possible until the
NULL-contract is decided"); no value-pinned test is proposed for it. It is
carried to Phase 9 **unchanged** for developer adjudication, exactly as
inherited from Phase 5 / Phase 6. No expectation was invented for it.

### 7.F.7 git status (only docs/audits/financial_calculations/ changed)

```
$ git status --porcelain
?? docs/audits/financial_calculations/07_test_gaps.md
```

`07_test_gaps.md` is the sole changed path; it lives under
`docs/audits/financial_calculations/`. No file under `tests/` was created,
modified, or deleted this session (the only test interaction was read-only
`grep`/`sed -n`/Read for verification). No source, migration, template, or
JS file was touched. `pytest` was never invoked in this session (verification
was source-read only; running the suite is out of Phase 7 scope per contract
item 6).

---

## Phase 7 complete

G1-G9 roll-up: **G1 PASS, G2 PASS, G3 PASS, G4 PASS, G5 PASS (18/18,
100%), G6 PASS (47/47 concepts; 20/20 DIVERGE; 5/5 symptom; meta-gap;
D6/S6/B6), G7 PASS, G8 PASS (A-26 carried unchanged), G9 PASS.** All nine
acceptance criteria hold with shown evidence. No unconditional
`PINNED-AGAINST-DIVERGENT-BEHAVIOR` finding exists; three conditional/latent
flags (`debt_total` Q-15, `goal_progress` GP2 Q-08, `federal_tax` F-040
dead-code) are recorded flag-only for Phase 8. The A-26
`estimated_retirement_tax_rate` tail is carried to Phase 9 unchanged. Source,
tests, and migrations untouched; `pytest` never invoked. **Phase 7 is
complete.**
