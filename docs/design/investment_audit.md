# Investment Dashboard Audit (Fable 5 overhaul)

Per-surface diagnosis of the investment / retirement account detail page, the last account type to
receive the Steel Ink + band-grammar treatment (grid, dashboard, cockpit, cash detail, property,
retirement readiness, salary, loan all shipped or on dev). Written 2026-07-06 from a code trace of
the route, service, templates, and JS; line references are to `dev` at `ba52771f`.

## Scope

One screen: `GET /accounts/<id>/investment` (`investment.dashboard`,
`app/routes/investment.py:56-87`), template `investment/dashboard.html`, HTMX fragment
`investment/_growth_chart.html` (route `investment.growth_chart`, lines 90-117), plus its two POST
handlers (`create_contribution_transfer` 140-265, `update_params` 268-317) and the JS triad
`growth_chart.js` / `chart_slider.js` / `investment_form.js`.

The cockpit's `detail_endpoint` macro (`_acct_macros.html:34`) routes BOTH the RETIREMENT category
(401(k), Roth 401(k), Traditional IRA, Roth IRA) and the INVESTMENT category (Brokerage, 529 Plan)
here. The retirement READINESS page (`/retirement`, rebuilt PR #56) is a different screen: it
answers "am I ready at 65" across accounts; this page answers per-account growth and contribution
mechanics. Its account tables link here (four links in `retirement/_retirement_account_rows.html` /
`_retirement_account_table.html`).

Data is in very good shape -- this is primarily a presentation rebuild. The headline balance reads
the `balance_at` seam (`_resolve_current_balance`, `investment_dashboard_service.py:174-206`) and
agrees to the cent with the cockpit net-worth tile; the projection seeds from the cash basis via
`investment_seed_map` (double-count guards DH-#9/#10); limits and suggestions carry the E-12
zero-cap semantics; percent conversion is schema-owned. The service was extracted to the
thin-delegator shape in Commit 28 and is regression-pinned by `tests/test_routes/test_investment/`.

## The screen's job (proposed)

"Is this account growing, am I feeding it right (within its limit), and what will it be worth?"
Three sub-questions: the balance now (and where it came from), the contribution machinery
(per-period employee + employer, YTD against the cap), and the trajectory (the growth curve, with
what-if).

## Inventory

1. Page chrome: breadcrumb + Back button + header.
2. Account Summary card: four equal tiles (Current Balance, Assumed Return, Per-Period Contribution,
   Employer Per Period).
3. YTD contribution-limit progress bar.
4. Contribution setup prompt (deduction path / transfer path, with the create-transfer form).
5. Employer Contribution details card.
6. Growth Projection card: horizon slider + what-if input + HTMX chart fragment (+ the fragment's
   comparison strip).
7. Parameters form (assumed return, limit, limit year, employer type + percents).

## Surface 1: Page chrome

- **Should show:** where you are, one way back, the account's identity in the cockpit's visual
  vocabulary.
- **Actually does:** breadcrumb "Accounts Dashboard / {name} Investment" AND a "Back" button on the
  header row, both to `savings.dashboard` (`dashboard.html:5-25`). Header icon is a hardcoded
  `bi-graph-up`; the shared glyph vocabulary (`acct_glyph`) gives `bi-briefcase` (retirement) /
  `bi-graph-up-arrow` (investment). Type name rendered as muted `<small>` text rather than the
  `acctd-type-tag` chip every rebuilt detail page uses. The `setup=1` post-create alert (27-32) is
  useful onboarding; it says "configure the settings below" and the Parameters form is the LAST card
  on the page.
- **Divergence:** duplicated navigation; icon and type-tag vocabulary drift -- the same finding the
  cash-detail audit fixed (its Surface 1).
- **Verdict: fix.** One navigation affordance, `acct_glyph` + `acctd-type-tag` chrome, keep the
  setup alert (and the rebuild puts parameters where the alert claims they are).

## Surface 2: Account Summary card

- **Should show:** the balance as the hero with honest provenance, contribution machinery as
  supporting figures.
- **Actually does:** four equal-rank `fs-4` tiles in one card (`dashboard.html:34-68`). Current
  Balance is styled `text-accent` (38-43); no caption states what the figure IS (a model-from-anchor
  value: the anchor compounded forward to the end of the current period). Assumed Return, Per-Period
  Contribution, and Employer Per Period sit at the same visual rank as the balance.
- **Divergence (confirmed):**
  1. **The number is not the hero** (principle 1). Four peers, none the answer.
  2. **No provenance caption** (principle 2). Every rebuilt sibling states its basis ("anchored Jun
     14, 2026" on cash detail; "Ledger-confirmed through Jun 2026" on loan). This balance is modeled
     from an anchor at an assumed return -- the page never says so, and never says when the anchor
     was.
  3. **Accent used as money color** -- breaks the Steel Ink rule (accent = controls/brand only);
     same finding as cash-detail Surface 2.
  4. Per-Period Contribution has no provenance either: it may come from a paycheck deduction, a
     recurring transfer, or nothing (it renders $0.00 with no explanation until you notice the
     prompt).
- **Verdict: fix.** Balance becomes the band hero with an "anchored <as_of_date>" caption (anchor
  event date from `AccountAnchorHistory`, the same source the cockpit uses); return / contribution /
  employer figures become chips with short provenance labels.

## Surface 3: YTD contribution-limit bar

- **Should show:** progress against the annual cap, with over-contribution as the alarm state.
- **Actually does:** `{ytd} / {limit}` in `font-mono` over a Bootstrap progress bar; fill class is
  `bg-danger` at >= 100, `bg-warning` at >= 90, else `bg-primary` (`dashboard.html:81`). Hidden
  entirely when no limit is configured (service returns `limit_info=None`, `_compute_limit_info`,
  service 401-441; `percent_complete` clamps to [0, 100]).
- **Divergence (confirmed):**
  1. **The color semantics are budget-framing on a goal-shaped number.** Worked example, 2026 401(k)
     limit $23,500: YTD $23,500 -> 100% -> RED bar. Maxing the 401(k) -- the outcome the user is
     deliberately steering toward all year -- renders as an emergency. YTD $21,600 (92%) -> YELLOW
     "careful" while the user is trying to land on 100 by December.
  2. **At-limit and over-limit are indistinguishable.** YTD $24,100 (over by $600 -- a real problem:
     excess contributions carry tax penalties) clamps to the same 100% red bar as a perfect max. The
     one state that deserves danger has no distinct signal (and no dollar amount).
  3. Raw Bootstrap `bg-warning` is not in the token vocabulary (state trio is done / credit /
     danger).
- **Verdict: fix (semantics at Gate A).** Recommended: goal framing -- neutral/accent fill while
  filling, `--shekel-done` at exactly-at-limit, `--shekel-danger` ONLY when `ytd > limit`, with the
  over-amount stated in text ("$600 over the 2026 limit"). Needs a small service addition (an
  over-limit flag/delta -- Opus scope) since the clamped pct cannot express it.

## Surface 4: Contribution setup prompt

- **Should show:** a one-time nudge with a working path when an account has params but no funding
  source.
- **Actually does:** three-state logic in `_compute_contribution_prompt` (service 665-736): hidden
  when a deduction or active recurring transfer exists; deduction path (401(k)-type) links to the
  active salary profile; transfer path renders an inline create form with a suggested per-period
  amount spread over the REMAINING periods of the year (DH-#59 boundary handling) and a source
  `<select>` defaulting to checking. CSRF present, POST, zero-cap refusal handled in the route
  (E-12).
- **Divergence:** none of substance. The `alert-info` idiom matches the loan page's transfer prompt
  exactly. Copy and styling ride the rebuild.
- **Verdict: keep.** Restyle only as part of the band page; logic untouched.

## Surface 5: Employer Contribution details card

- **Should show:** the match formula, quietly -- it is set-and-forget configuration prose.
- **Actually does:** a full card (`dashboard.html:163-187`) rendering one sentence (flat: "x% of
  gross salary each pay period"; match: "matches x% up to y% of gross"). Branches on `type_id`
  against injected enum globals -- correct.
- **Divergence:** prominence only. A whole card for one sentence, while the actual per-period dollar
  figure already sits in the summary tiles.
- **Verdict: fix (demote).** Fold the sentence into the employer chip's caption or the Parameters
  card; the formula belongs next to the fields that define it.

## Surface 6: Growth Projection card + fragment

- **Should show:** the trajectory -- the trend IS the answer here -- plus honest controls for
  horizon and what-if.
- **Actually does:** horizon slider (1-40 yr) synced to a number input, a what-if per-period
  contribution input, and `#growth-chart-container` with `hx-trigger="slider-changed"` re-fetching
  the fragment (`dashboard.html:189-242`). The fragment renders a two-line chart (projected balance
  filled + contributions-only dashed) or, in what-if mode, committed vs what-if with a three-figure
  comparison strip (`_growth_chart.html`). Chart goes through `ShekelChart.create` with a config
  factory (theme-toggle safe), money-formatted axes and tooltips. Empty state exists.
- **Divergence (confirmed):**
  1. **The initial chart and the slider disagree** (principle 2). `slider-changed` fires only from
     input events (`chart_slider.js:48-63`, `growth_chart.js:140-155`), so first paint renders the
     context from `_project_dashboard_balances` -- the REAL rolling-window periods, about 2 years --
     while the slider shows `default_horizon` (planned retirement year minus today when set, e.g.
     24; service 444-463). A user with a retirement date set reads a "24 yr" slider over a 2-year
     curve until they touch the slider.
  2. **Two different period sources for the same chart:** real pay periods on first paint, synthetic
     365-day periods (`generate_projection_periods`) on every HTMX refresh. The curve silently
     changes basis after the first slider touch.
  3. **No history, no Today marker.** The chart starts at today and is 100% dashed-equivalent (all
     projection, rendered solid). Every rebuilt sibling shows solid history + dashed projection
     split at a Today marker (`chart_theme.js` helpers: `splitSegment`, `todayMarkerPlugin`). Past
     modeled balances are available from the same `balance_at.balance_map` the headline uses.
  4. **No retirement-date landmark.** The default horizon already secretly targets the planned
     retirement year; the chart never marks it. A vertical marker (like Today) would make that
     default honest and give the 401(k) curve its destination.
  5. The comparison strip uses raw `text-success` / `text-danger` (not the token-mapped state
     treatment) and no icon/text pairing beyond the sign.
  6. Slider + what-if inputs are form rows inside the chart card; the rebuilt grammar for "adjust an
     assumption, see the outcome" is the lever card (retirement levers, loan pay-off-sooner /
     refinance).
- **Verdict: fix.** Band chart with history + Today marker (+ retirement marker when set); initial
  render at the slider's default horizon on ONE period basis; horizon + what-if become levers below
  the band in the loan/retirement lever grammar with token-mapped verdict figures.

## Surface 7: Parameters form

- **Should show:** set-and-forget configuration, editable but not competing with the money.
- **Actually does:** a full-width card at the page bottom (`dashboard.html:244-315`): assumed
  return, annual limit, limit year, employer type `<select>` (renders ref rows by id -- correct),
  three employer percent fields toggled by `investment_form.js` reading the selected option's
  `data-name` (a display-semantic toggle; ids drive the POST). Percent-to-fraction conversion is
  schema-owned (F-17). Full-page redirect + flash on save.
- **Divergence:** prominence/idiom only -- it predates the `form_card` / `acctd-params` vocabulary.
  Mechanics are sound.
- **Verdict: keep + restyle.** Parameters card below the band (cash-detail decision 7 precedent),
  employer formula sentence folded in (Surface 5), same fields and endpoint.

## Cross-cutting notes

- **Dead context key:** `compute_dashboard_data` returns `projection` (the full `ProjectedBalance`
  list) and the template never references it -- only the three chart series are consumed. The
  projection run itself is needed (it feeds `_build_chart_series`), so this is a dead KEY, not
  wasted compute. Drop it from the context during Loop B (Opus-scope one-liner) or leave documented.
- Money macro used throughout; no inline style/script; CSRF on all three forms; mutations are POST;
  ref rows render by id; `.name` usage is display-only. No template money math.
- `_compute_default_horizon` uses `date.today()` (server-local); worst case is a year-boundary
  off-by-one on the default slider value around midnight UTC -- cosmetic, note only.
- No `investment.css` exists; rebuild CSS lands in `accounts.css` (shared `acctd-*` / band classes)
  with a new per-screen file only if the levers need one.
- Tests: `tests/test_routes/test_investment/` is the load-bearing regression suite;
  `tests/test_services/test_investment_projection.py` covers the projection primitives.

## What the page does not show (candidate additions, Gate A)

1. **Measured growth story.** The page is entirely forward-looking; nothing answers "how has this
   account actually done?" A "growth vs contributed" figure is computable from existing producers:
   modeled balance delta across the year minus measured YTD contributions (deductions + transfer
   shadows). Worked example: balance $50,000 at the current period, anchored $40,000 on 2026-01-01,
   contributions since then $6,000 -> growth +$4,000. Caveat for honesty: between anchors the
   balance is MODELED at the assumed return, so the figure is only as measured as the anchor cadence
   (statement-day true-ups on the cockpit make it near-measured). Requires a small producer (Opus
   scope).
2. **Retirement cross-link.** The readiness page links each account here; nothing links back. A
   quiet "Retirement outlook" link for RETIREMENT-category accounts closes the loop (principle 4).
3. **Balance true-up on-page.** The loan page grew a "Record balance" form; investment balances are
   trued up on the cockpit's click-to-edit. Question of lived workflow -- statement day currently
   means a cockpit round-trip per account.

## Navigation facts

Cockpit card (sparkline, click-to-edit balance) -> here via `detail_endpoint`. Retirement readiness
tables -> here (4 links). Account creation redirects new investment/retirement accounts here with
`setup=1`. This page links out only to `savings.dashboard` (twice) and the salary profile (deduction
prompt).

## Proposed shape (for Gate A)

Rebuild in the account-detail band grammar, loan/retirement lever grammar:

- **Band:** balance hero (model-from-anchor, agrees with the cockpit to the cent) + honest caption
  ("anchored <as_of_date> - modeled at x% assumed return"); chips: assumed return, per-period
  contribution (with deduction/transfer provenance), employer per period (with the match formula as
  its caption), YTD-vs-limit (goal-framed), and -- if approved -- growth YTD (measured). Full-width
  chart: solid modeled history, dashed projection, Today marker, retirement-date marker when set,
  contributions-only baseline kept.
- **Levers (below the chart):** Horizon (slider, default = retirement year, initial render MATCHES
  it on one period basis) and What-if contribution (input + comparison verdict in token colors).
  Same HTMX fragment mechanics, restyled.
- **Sections:** contribution setup prompt (unchanged logic), Parameters card (form_card idiom,
  employer formula folded in), optional retirement cross-link.
- **Mechanical fixes ride along:** initial-chart/slider mismatch, single nav affordance, glyph +
  type tag, token colors on the comparison strip and limit bar, dead `projection` context key
  dropped.

## Gate A questions for the developer

1. **History on the chart?** Solid modeled history + dashed projection + Today marker (+
   retirement-date marker when set), matching every rebuilt sibling. Recommended: yes.
2. **Limit-bar semantics:** goal framing (green at max, danger ONLY over-limit, over-amount stated)
   vs the current budget framing (yellow at 90, red at 100)? Recommended: goal framing -- see the
   Surface 3 worked example.
3. **Levers:** convert the horizon slider + what-if input into the lever-card grammar below the band
   chart? Recommended: yes (retirement/loan precedent).
4. **Measured growth story:** add the growth-vs-contributed YTD figure (new producer, Opus scope,
   honesty caveat above)? Recommended: yes -- it is this page's version of the loan page's measured
   chips.
5. **Retirement cross-link** for retirement-category accounts? Recommended: yes, quiet.
6. **On-page balance true-up** (loan-style Record balance) or keep the cockpit as the only true-up
   surface?
7. **Your lived workflow:** what do you actually check when you open the 401(k) / brokerage page
   today, and what do you wish it answered that it does not?

## Rebuild decisions (Gate A, 2026-07-06)

Decided 2026-07-06 (developer ruling). Locked.

1. **Chart history: LOCKED.** Solid modeled history + dashed projection split at a Today marker,
   retirement-date landmark when a planned retirement date is set; the contributions-only baseline
   stays as the second dashed series.
2. **Limit bar: LOCKED goal framing.** Accent/neutral fill while filling, `--shekel-done` at
   exactly-at-limit, `--shekel-danger` ONLY when `ytd > limit` with the over-amount stated in text
   ("$600 over the 2026 limit"). Requires the over-limit flag/delta in the service (the clamped pct
   cannot express it) -- Opus scope.
3. **Levers: LOCKED.** Horizon and What-if contribution become lever cards below the band chart
   (loan/retirement grammar); comparison verdict figures token-mapped. Same HTMX fragment mechanics.
4. **Additions: ALL THREE LOCKED.**
   - Growth-vs-contributed YTD figure (measured; new producer, Opus scope; honest caption per the
     audit's anchor-cadence caveat).
   - Retirement cross-link on RETIREMENT-category accounts back to `/retirement`.
   - On-page balance true-up: a loan-style "Record balance" form appending a dated anchor
     (`AccountAnchorHistory`), so statement day does not require a cockpit round-trip per account.
     Route work is Opus scope.
5. **Mechanical ride-alongs** (from the audit, no gate contested): initial-chart/slider horizon
   mismatch fixed by rendering the first paint at the default horizon on ONE period basis; single
   nav affordance + `acct_glyph` + `acctd-type-tag` chrome; token colors on the comparison strip;
   employer formula sentence folds into the Parameters card (Surface 5 demotion); Parameters card
   restyled below the band; dead `projection` context key dropped.
6. **Lived workflow (recorded 2026-07-06):** the developer checks ONLY the growth projection, and it
   is buried (sixth surface down the page). They want to update the balance directly on the page.
   They rarely adjust parameters. The deduction-warning banner "takes up a lot of real estate for
   something I already know" -- the unfunded state can be deliberate and long-lived, so a permanent
   full-width alert for it is noise; same verdict for the employer card. Overall: "a lot of data
   available for the page but most of it is either buried on the page or not displayed at all."

   Design consequences (binding on Loop A):
   - **The chart is the band's centerpiece at the top of the page.** The growth projection IS the
     page; everything else supports it.
   - The on-page true-up gets prime placement (hero-adjacent, cockpit click-to-edit pattern or a
     compact dated Record-balance affordance), not a bottom-section form.
   - Parameters stay demoted to the last card.
   - The contribution setup prompt DEMOTES from a full-width alert to a quiet one-line hint with an
     inline action (both paths: deduction link and transfer form behind a disclosure); the transfer
     form's logic is unchanged.
   - The employer formula demotes to chip caption / Parameters card (decision 5 confirmed).
   - The available-but-hidden data (measured YTD contributions, growth vs contributed, limit
     progress, employer per period) surfaces as band chips so it is visible without being louder
     than the chart.

## Loop A record

- **Round 1 (2026-07-06):** two directions on a 401(k) mock sharing the Gate A band (hero +
  provenance chips + goal-framed limit chip + history/projection chart with TODAY and RETIRE
  markers): **A "levers below"** (full-width chart; Horizon + What-if lever cards beneath -- the
  loan grammar) vs **B "control rail"** (slider, what-if, and verdict in a rail beside the chart
  inside the band). Developer picked **A**: "I like the wider chart and the consistency with the
  loan page." The two exhibits (demoted funding hint; the three goal-framed limit-bar states) rode
  the same shots.
- **Round 2 (2026-07-06): LOCKED as presented.** A refined: RETIRE label cleared of its marker line;
  the projection verdict promoted to a full-width strip under BOTH levers (horizon and what-if both
  feed those figures); mobile stacking verified; Parameters sits as the lone constrained section
  card (cash-detail `acctd-params` precedent). Scratch mocks live in the session scratchpad only
  (anti-anchoring rule) and are deleted when Loop B completes.

## Locked anatomy (Loop A complete, 2026-07-06)

- **Header:** `acct_glyph` + account name + `acctd-type-tag`; a quiet "Retirement outlook ->"
  page-head link (RETIREMENT-category accounts only); the breadcrumb is the only other navigation
  (the Back button is dropped).
- **Band:** click-to-edit balance hero (pencil affordance; records a statement balance as a dated
  anchor as-of today, the cockpit editor pattern) with the honest caption "anchored on the event
  date, modeled at the assumed return". Chips: contribution per period (funding-provenance caption),
  employer per period (with the match formula), growth since anchor (measured; done tint when the
  figure is positive, danger when negative, sign always rendered, contributed total in the caption),
  and the annual limit (goal-framed mini bar plus a pace caption; hidden when no limit configured).
  Chart, full width: solid modeled history, dashed projection, a dashed contributions-only baseline,
  a TODAY marker, and a RETIRE marker at the planned retirement year when one is set.
- **Levers (below the chart):** Horizon (slider 1-40 yr; default = planned retirement year; "24 yr
  -> 2050, your planned retirement year") and What-if contribution (input; "currently $X - clear to
  return to the committed plan"). One SHARED verdict strip spans beneath both: Current plan /
  What-if / Difference at the horizon year; with no what-if entered it shows the current plan only.
- **Sections:** the Parameters card alone (constrained width): assumed return, annual limit + limit
  year, employer type + percents, the employer formula sentence, save. Nothing else.
- **Unfunded state:** a quiet one-line hint ("No funding linked to this account. Link a paycheck
  deduction | Set up a recurring transfer") replaces the full-width alert; the transfer create form
  is reachable from the hint; prompt logic unchanged.
- **Removed as separate surfaces:** Account Summary card, YTD limit card, Employer Contribution
  card, the contribution alert banner, the Back button.

## Loop B build plan

- **P1 -- data (Opus scope):**
  - C1 context honesty: expose the anchor event date (`AnchorPoint.as_of_date` /
    `AccountAnchorHistory`) for the hero caption; extend `_compute_limit_info` with the over-limit
    state (`is_over` + `over_amount` -- the clamped pct cannot express it); expose the
    funding-provenance discriminator for the contribution chip caption; drop the dead `projection`
    context key.
  - C2 one chart, one basis: the initial dashboard chart renders at `default_horizon` on the SAME
    synthetic-period basis as the HTMX fragment (kills the slider/first-paint disagreement and the
    silent basis switch), and both gain the history series (past modeled balances from
    `balance_at.balance_map` over the real past periods up to the current one) plus the marker
    indices (today boundary; retirement year when set). The regression assertions this changes are
    the Gate-A-approved behavior change (ride-along 5) -- each changed assertion documented with
    hand-computed values.
  - C3 growth-vs-contributed producer: growth_ytd = modeled balance at the current period minus the
    modeled balance at the last period ending before Jan 1 minus ALL measured money-in for that
    window (employee + employer + transfer receipts). If no prior-year period exists in the window,
    the chip hides (None) rather than showing a wrong figure. Intent pinned by the audit's worked
    example; exact stream composition verified from code, tests hand-computed.
  - C4 hero true-up wiring: reuse the existing anchor editor flow (cockpit pattern, dated as-of
    today) for this page; ownership 404s, CSRF, POST partials per standards.
- **P2 -- page (Fable scope):** rebuild `investment/dashboard.html` + `_growth_chart.html` in the
  locked anatomy; band CSS shared from `accounts.css` (new file only if the levers need one); chart
  via the `chart_theme.js` factory + shared marker helpers; the verdict strip updates with the
  fragment (OOB swap or wrapper target); demoted funding hint; both themes, both viewports via
  shoot.py.
- **P3 -- acceptance:** developer drive on real accounts; as-built recorded here.

## Loop B P1 + P2 as-built (2026-07-06)

Built on `dev`, UNCOMMITTED, full suite **7220 passed** (pylint 10.00/10 after P1; biome clean after
P2; no CSP violations in the changed templates).

**P1 (data, Opus subagent) -- C1, C2, C4 delivered; C3 STOPPED on a developer fork:**

- C1: `anchor_as_of` (the dated-anchor event date via `balance_resolver.resolve_anchor`,
  display-tz), `limit_info.is_over` / `over_amount` (E-12 zero-cap semantics preserved),
  `contribution_funding` discriminator (deduction / transfer / none; resolved even when the prompt
  is hidden, deduction wins), dead `projection` key dropped.
- C2: one shared `_assemble_chart_context` -- first paint and the HTMX fragment both render at the
  slider's horizon on the synthetic-period basis; history series
  (`history_labels`/`history_balances` via `balance_at.balance_map`) + markers
  (`today_boundary_index`, `retirement_marker_index` on the combined axis, `retirement_year`). The
  regression assertions this changed were hand-recomputed (documented in
  `test_investment/test_the_projection.py`; e.g. the DH-#9 double-count pin re-expressed against
  `chart_balances[0]`).
- C4: `investment` revert surface on the shared anchor editor; `investment.balance_hero` GET +
  `compute_balance_hero_cell` producer + `investment/_balance_hero.html` click-to-edit partial
  (PATCH `accounts.true_up`, dated anchor, `balanceChanged` on save; IDOR 404s; 409 keeps the
  investment revert). 6 new route tests.
- **C3 STOPPED (correct per rule 8):** "measured money-in, all sources" does not exist in code for
  deduction-funded accounts -- deductions and employer match are modeled (no transactions), only
  transfer shadows are measured, and the modeled balance map applies a FLAT contribution stream. The
  fork (what "contributed" means) is with the developer: **A reconcile-with-map (recommended)** =
  subtract the projection's applied employee+employer contributions (ties to the displayed balance
  to the cent); B measured-transfers-only (counts a 401(k)'s own paychecks as growth -- wrong for
  the primary case); C timeline mix (can disagree with the hero); or drop the chip. Growth chip is
  ABSENT from the page pending the ruling.
- P1 note: `investment_dashboard_service.py` sits at exactly 1000 lines (the C0302 ceiling); the C3
  producer should land in its own module or ride a package split (`savings_dashboard_service/`
  precedent).

**P2 (page, Fable, this session):** `dashboard.html` rebuilt to the locked anatomy (band = hero
region re-rendering on `balanceChanged` + modeled-at caption + contribution / employer / goal-framed
limit chips + chart container also refreshing on `balanceChanged`; demoted band-foot funding hint
with the transfer form behind a Bootstrap collapse; levers + shared verdict strip; lone Parameters
card with the employer formula sentence folded in; Retirement outlook head link for
`ACCT_CAT_RETIREMENT`). `_growth_chart.html` = canvas with merged-axis data attributes + out-of-band
verdict re-delivery (`oob_verdict` flag; initial include renders the strip in place). New
`_growth_verdict.html`. `growth_chart.js` rebuilt on the `chart_theme.js` helpers (splitSegment /
todayMarkerPlugin) + a local retirement-marker plugin (credit token) + the horizon "N yr -> year"
caption sync (calendar math only). `invd-*` CSS section appended to `accounts.css`. Limit-bar widths
via `data-progress-pct` (`progress_bar.js`), no inline styles. 13 copy-pinned assertions retargeted
to the new surfaces (including the previously-vacuous negative pins on the hidden-prompt tests); the
setup-banner copy stayed "settings below" to match `cash_detail.html` verbatim.

**Acceptance-drive fixes (2026-07-06, developer live report):**

1. Chips stacked vertically: the band's chip row carried only `nw-statrow` (a margin tweak); the
   flex layout lives on `pulse-statrow` (dashboard.css). Fixed to the cash-detail markup
   `class="pulse-statrow nw-statrow"`.
2. Partial chart after lever changes: chart re-init moved from `htmx:afterSwap` to
   `htmx:afterSettle` -- the settle phase restores the id-matched canvas's original attributes ~20ms
   after the swap, stripping the sizes Chart.js just wrote and collapsing the bitmap to a 300x150
   slice, nondeterministically. Identical to the retirement path chart's recorded acceptance defect;
   same fix (see `retirement_path_chart.js`).

**C3 ruling (developer, 2026-07-06): Option A reconcile-with-map LOCKED.** "Contributed" = the
modeled employee + employer contributions the balance map itself applied over the window, so growth

- contributed = balance delta to the cent and the chip cannot disagree with the hero. Chip hides
(None) when no pre-Jan-1 baseline period exists in the map. Honest caption carries the contributed
figure ("on $X contributed").

## Loop B C3 (growth chip) + projection_end as-built (2026-07-06)

Built on `dev`, UNCOMMITTED. Full suite **7226 passed**, pylint app/ **10.00/10**, checker suite 131
passed, biome clean, no CSP violations. Live-verified on the real Empower 401(k) (both themes).

**Growth-since-anchor chip (Option A, reconcile-with-map):** the window is `(anchor, current]`
(entirely post-anchor, so forward-projection only). `growth = Sum(row.growth)` and
`contributed = Sum(contribution + employer_contribution)` are read from the SAME forward projection
the modeled map's post-anchor values come from, so
`growth + contributed == balance_map[current] - anchor_balance` by telescoping. **Verified live:
Empower 401(k) balance $31,370.87, anchor $31,070.06 -> delta $300.81 = +$119.22 growth + $181.59
contributed, to the cent.** Chip hides (`None`) when anchored this period or later (no elapsed
window). The reconciliation identity is pinned by
`tests/test_services/test_balance_at/test_investment.py::TestInvestmentGrowthSinceAnchor` (+
seam-parity and None cases) and the render by two route tests.

**Architectural note -- the kernel split this required.** Reconciling to the cent requires the
decomposition to read the kernel's exact forward projection (the dashboard's own inputs use a
different reference period and would NOT reconcile). The kernel (`net_worth_kernel`) was at its
997/1000 line ceiling, so the investment growth sub-chain was EXTRACTED to a new module
`app/services/net_worth_investment.py`: it now owns `investment_base_balance_map`,
`get_anchor_period_index`, `_load_shadow_contributions`, `build_investment_balance_map`,
`build_appreciation_balance_map`, and the new `investment_growth_since_anchor`, sharing ONE assembly
(`_assemble_investment_projection_inputs`) and ONE forward call (`_forward_project_rows`) so the map
and the chip cannot drift (true DRY). The kernel dispatches into it via a call-time import (the
established loan-reader cycle-break pattern); the module imports nothing back, so there is no static
cycle. The seam gained `balance_at.investment_growth_since_anchor` (fenced, mirrors
`investment_seed_map`); the W9906 checker allowlists the new module and fences the two renamed
public builders; year-end + the seam import the moved primitives from the new home.
Behavior-preserving: all 119 balance-layer + year-end tests pass unchanged.

**projection_end:** the committed projection's end balance at the rendered horizon, added to the
chart context; the verdict strip's current-plan-only state (no what-if entered) shows "Projected
balance at horizon $X". Verified live: $82,145.47.

## P3 acceptance (2026-07-06): COMPLETE + ACCEPTED

Developer-accepted 2026-07-06 on the real Empower 401(k) (both themes). The whole rebuild -- band
grammar, the growth chip reconciling to the cent (+$119.22 on $181.59 contributed), the
projection_end verdict state ($82,145.47), the two acceptance-drive bug fixes, and the kernel
split -- is accepted. The investment / retirement detail page is the LAST account type in the Fable
5 overhaul; all account types now carry the Steel Ink + cockpit treatment. Committed to `dev`; ships
to prod with the next `dev` -> `main` PR.

Accepted implementation note (not a defect): the on-page balance true-up is the click-to-edit
balance hero (pencil affordance opening the shared cockpit anchor editor), NOT a separate "Record
balance" form card -- functionally the Gate A ruling (dated anchor, on-page, no cockpit round-trip),
accepted as built.

**Deferred (recorded, not blocking):** both `investment_dashboard_service` (1000/1000) and
`balance_at` (1000/1000) sit at the C0302 ceiling; the dashboard's chart cluster is the natural next
extraction (P1 flagged it) if either needs to grow again. Scratch mocks (`investment_explore.html`,
`investment_explore_r2.html`) kept in the session scratchpad for reference by developer request (not
committed).
