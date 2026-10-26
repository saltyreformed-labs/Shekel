> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: UI Overhaul + Polish Pass Closeout

Combined remaining-work list for the Fable 5 UI/UX overhaul (`docs/design/overhaul_plan.md`) and the
UI/UX polish pass (`docs/plans/historical/implementation_plan_ui_ux_polish.md` plus its register at
`docs/design/ui_ux_polish_audit.md`). Written 2026-07-11 from both docs cross-checked against dev
git history; where a doc and the code disagreed, the code won. The polish register remains the SoT
for finding details and rulings; this plan governs only what is left and in what order.

## Status corrections (listed as open in the docs, verified DONE on dev)

The overhaul plan's status table stops at 2026-07-03. Complete since then:

- **Loan detail rebuild:** Loop B P1 (`39eafed0`) + P2 (`90b1116f`); all three deferred follow-ups
  resolved (`f361f1fb`); Extra-principal card placement (`f6467560`).
- **Escrow config redesign:** all 8 steps merged to dev via PR #60 (`b28a3cfa`).
- **Investment detail rebuild** (the last account-type page): band-grammar rebuild landed
  (`65730d0a`).
- **Recurring cluster** (templates + transfers + obligations -> one surface): Loop B P1
  (`60f0c96a`), P2 (`38152be7`), P3 conflict chooser (`9d14f005`), P4 shared form cards
  (`4f15ddd6`), P5 accepted 2026-07-05 (as-built in `recurring_audit.md`).
- **Navbar/IA rework:** decided and built 2026-07-06 (`navbar_audit.md` decision gate); silver-coin
  brand extended app-wide, gold logo deleted.
- **Polish sessions S1-S7 and S11-S16** complete (per-session records in the polish plan). Only S8,
  S9 (shrunk), and S10 remain.
- The dashboard row's "anchor UTC-day-vs-Eastern bucketing" fork: resolved by the timezone display
  policy (DB/backend UTC, America/New_York at presentation; shipped 2026-06-12).

## Remaining work

### A. Polish sessions still open (already specced in the polish plan)

1. **S8 - detail pages + anchor idiom** (Opus recommended, size S-M). P-DT2 loan allocation bar +
   "confirmed" badge off raw `bg-success/warning/info`; P-DT3 investment hero caption mono -> sans;
   P-DT5 casing normalization across the three siblings; P-DT6 investment half-width final row;
   P-DT7 ARM tag RC5 floor case; P-DT9 amortization link promoted to a real control; D14 port of the
   investment click-to-edit hero to loan and cash (cash has no on-page anchor recording, so the port
   may touch a route/form; ruled 2026-07-09, buildable).
2. **S9 remainder - accounts** (Fable preferred, size S). P-AC1 allocation-bar track + center tick
   (the judgment item); P-AC4 liability red on the loan-cell balances; verify the account edit form
   and archived list against the D5 archive convention (the cockpit has no on-card archive button
   left after D6-F).
   **P-AC1 RULED 2026-07-11 (Loop A round 1, run after everything else here closed):** scope widened
   to replace the bar AND the whole trend chart with ONE element - C1 diverging stream + milestone
   flags, Horizon default, two-mode range toggle (`2 years` / `Horizon`), picker + series toggle
   retired, net line in number ink. Full record at P-AC1 in the polish register +
   `accounts_audit.md` amendment. Remaining build = Loop B: **P1 (Opus)** long-horizon annual
   producer + per-category composition split of the 2-year series; **P2 (Fable)** the page element.
   This is the last build item before the ship arc (items 10-12).
   **Loop B P1 BUILT 2026-07-12 (Opus; data producers plus route serialization; on dev).**
   `_display.account_category_key` is now the one id-based classifier the grid grouping and the
   composition split share; `compute_net_worth_series` emits a reconciling `composition` band map;
   `_horizon.build_horizon` (via `compute_dashboard_data`) builds the annual composition, net
   trajectory, and milestones, reusing the /retirement engine (constant employer base), per-account
   growth params, and the loan schedules (via the new fenced `net_worth_kernel.loan_owed_at_dates`).
   The horizon wires into `compute_dashboard_data` and `savings._serialize_net_worth_chart` emits
   one Chart.js payload for both ranges (the prior 2-year keys unchanged, plus additive
   `composition` and a nested `horizon`). Verified on real dev data (the retirement band at 2049
   equals the ruled oracle); full suite green, pylint 10.00/10, adversarial review clean. Full
   as-built at P-AC1 in the register. **Remaining: P2 (Fable)** -- the page element (template, JS,
   CSS) consuming the payload.
3. **S10 - breadcrumb removal + back buttons (D1)** (Opus recommended, size M). Inventory the
   sub-pages whose only way back is the breadcrumb; add a top-right "Back to <parent>" on the
   amortization-schedule pattern; then delete breadcrumbs everywhere (~24 templates). Run solo.
   **COMPLETE 2026-07-11 (session 3; run on Opus 4.8; full suite 7304 green, pylint 10.00/10, djlint
   and biome clean; representative pages re-shot both themes + mobile).** Exactly 24 breadcrumb
   templates found (7 navbar-level, 16 sub-pages, base.html slot). All breadcrumbs deleted (blocks,
   base.html slot, dead `.breadcrumb` CSS + comments); one shared `back_link` macro in the new
   `app/templates/_nav_macros.html` is the single source, byte-identical to the two shipped
   precedents (`loan/schedule`, `loan/setup`). 16 sub-pages carry the button (title-row for detail
   pages, in-column for form_card pages, h4-paired for the two raw-h4 forms). Two forks ruled
   pre-build: calibrate pages -> "Back to Profile"; the two non-standard existing controls
   normalized. Full as-built at D1 in the polish register.

### B. Flagged during polish sessions, not registered as findings

4. **Designed error-fragment convention for handled 4xx/5xx over HTMX** (Opus). htmx drops 4xx/5xx
   response bodies by default; S5 shimmed only the taxes checkpoint's 422 path
   (`tax_checkpoint.js`). Still dead UI: that card's handled-500 banner, and the grid's 422/400
   error paths. **RULED 2026-07-11: marker-header convention.** Every route returning a deliberately
   designed error fragment stamps a marker response header via a small helper; ONE global
   `htmx:beforeSwap` handler in `app.js` swaps any 4xx/5xx response carrying it. The two per-surface
   shims (`tax_checkpoint.js` swap half, `retirement_controls.js` swap half) are deleted; the
   handled-500 banner works because an unhandled crash page never carries the marker; the 409
   app-wide swap stays as-is. Fragment grammar per the checkpoint precedent: the same partial
   re-rendered in place with inline field errors + danger banner. Unhandled errors keep today's
   non-swap behavior. **BUILT 2026-07-11 (session 4; commits 02c4000d infra, f9cb3166 grid/entries,
   58aab29d transfers).** As-built: header = `Shekel-Designed-Fragment: 1`;
   `app/utils/error_fragments.py` owns the constant, `designed_error()`, and
   `flatten_schema_errors()`. Grid rejections re-render the request's own surface: the desktop cell
   mirrors the C-18 conflict icon/title grammar in danger ink; the mobile card gets an in-wrapper
   danger banner (a cancelled row's stale card gets a banner-only wrapper, since the card lists
   filter cancelled rows); the entry list gets a banner over current data. Transition messages now
   lead with status NAMES (ids stay in parens - the `str(id) in msg` test contract holds). Bug found
   and fixed in the sweep: the transfers `mark_done` / `cancel_transfer` routes never caught the
   state machine's ValidationError, so a stale-surface illegal transition crashed as an unhandled
   500. Deliberate residue: the ad-hoc CREATE paths (`create_ad_hoc`, `create_inline` 422) keep
   their JSON contract - they are not grid mutation surfaces and browser input constraints make them
   UI-unreachable; either can adopt the marker mechanically if a surface ever needs it.
5. **Analytics year-view month cards** (size S, mechanical). Raw `border/text-success/danger` + cyan
   `bg-info` badges; flagged in the S5 as-built as out-of-register residue; same-genus token sweep.
   **COMPLETE 2026-07-11 (run on Opus 4.8; full suite 7304 green, djlint + biome clean; year view
   re-shot both themes + computed colors probed against the tokens).** `_calendar_year.html`: the
   income/expense/net figures (per-card and annual totals) adopt the calendar money-state classes
   `calendar-day-income` / `calendar-day-expense` (matching the month sibling so the Month/Year
   toggle stays consistent); the net-sign card border moves onto new
   `.card.calendar-month-card--surplus` / `--deficit` token modifiers in `analytics.css`
   (`--shekel-done` / `--shekel-danger`, the compound `.card` selector specificity-matched to the
   `[data-bs-theme] .card` border rule so they win on source order); the "3rd check" badge adopts
   the shared accent `.flag-chip` per D11 + S7's event-marker ruling (a 3rd-paycheck month is a
   structural marker, not a caution). Template + CSS only, figures unchanged. As-built recorded at
   the Calendar section of the polish register.

### C. Overhaul-plan small follow-ups (verified still open today)

6. **A5 - grid quick-create name field** (Opus: schema/route/template). Verified 2026-07-11:
   `_transaction_quick_create.html` still posts `estimated_amount` plus hidden ids only, so an
   ad-hoc row cannot be named at the Tier-1 entry point.
   **BUILT 2026-07-11 (session 4; commits 53211095 + 10d28499).** Optional name input above the
   amount (placeholder = the category default; autofocus stays on the amount so the everyday
   amount-Enter loop is unchanged); a typed name wins, blank falls back to `category.display_name`.
   Live-loop catch worth remembering: HTML implicit submission only fires on single-field forms, so
   the second input silently killed Enter-to-save - a hidden default submit button restores it
   (10d28499).
7. **Grid D1/D2 (noted-not-prioritized in `grid_audit.md`) - RULED 2026-07-11** (recorded at D1/D2
   in `grid_audit.md`). D1 (period-nav simplification): DROPPED - the three mechanisms serve three
   distinct jobs and daily use since the rebuild surfaced zero findings. D2 (invalid status
   transitions): SCHEDULED into session 4, both halves - the entries.py 400/422 paths return
   designed fragments under the item-4 convention, AND the action card disables transitions the
   current status does not allow (allowed-transition map exposed to the template).
   **D2 BUILT 2026-07-11 (session 4; commit 858029e2 + the item-4 fragments).**
   `state_machine.allowed_transitions()` feeds both action cards' status dropdowns (illegal options
   disabled with a reason; Credit also disabled on purchase-tracking rows; the transfer dropdown
   permanently grays Credit/Received). The Paid / Mark Paid buttons tightened from not-is_settled to
   projected-only, removing the documented dead affordance on Credit and Cancelled cards (desktop
   card + mobile action bar; the cell paybtn already complied).
8. **Manifest icon versioning residue** (size XS). `css_architecture_audit.md`: manifest icon paths
   are unversioned strings (the icons themselves were re-baked silver 2026-07-06). **COMPLETE
   2026-07-11 (run on Opus 4.8; pylint 10.00/10, biome + djlint clean; live-verified on the dev
   app).** The manifest now serves through the new `static_pass.web_manifest` route
   (`/manifest.json`), mirroring the `/sw.js` precedent: it reads `app/static/manifest.json` and
   rewrites each icon `src` through `url_for('static', ...)`, so the icons carry the same
   `?v=<content hash>` every other asset gets (verified the manifest's icon hash equals the
   apple-touch-icon's for the same file). `base.html` points at the route; the now-defunct
   `/static/manifest.json` was removed from the SW cache list (`_CACHED_STATIC_FILES`) and
   `STATIC_PREFIXES` (the SW invariant is untouched -- caching only narrowed). Route is `no-store`
   via the app-wide non-static hook. 4 new tests (`TestWebManifest`). Residue note in
   `css_architecture_audit.md` section 5 marked RESOLVED.

### D. Housekeeping

9. **Test-suite idle-timeout pin.** `.env` carries `IDLE_TIMEOUT_MINUTES=10080` (the 7-day dev value
   from `1bdf0485`) and neither `scripts/test.sh` nor the fixtures pin the test value, so
   `test_stale_activity_rejected` fails suite-wide unless run with `IDLE_TIMEOUT_MINUTES=720`. Root
   fix: pin the session-lifetime config in the test fixture so the suite is independent of the
   ambient `.env`. **BUILT 2026-07-11 (session 4; commit 9c370901).**
   `TestConfig.IDLE_TIMEOUT_MINUTES = 720` (the RATELIMIT_STORAGE_URI hermetic-override precedent);
   verified by running the step-up suite with `IDLE_TIMEOUT_MINUTES=10080` exported - 94 passed.
   Suite runs no longer need the env prefix.

### E. Ship

10. **Developer acceptance drive** on dev over everything unshipped: dashboard S2, accounts D6-F
    cells, spending D7 cockpit, analytics S5, statements D8, retirement/salary S7, settings S11,
    plus whatever A-D adds.
11. **dev -> main PR** (57 commits pending as of 2026-07-11) -> prod deploy -> resync dev.
12. **Docs refresh after the merge:** run `/update-docs`; bring `overhaul_plan.md`'s status table
    current (the corrections above); mark the polish plan and register closed out.

## Proposed sessions

| # | Session     | Contents                          | Model           | Size     |
|---|-------------|-----------------------------------|-----------------|----------|
| 1 | S8          | item 1                            | Opus            | S-M      |
| 2 | S9+         | items 2, 5, 8 (all done 2026-07-11; P-AC1 ruled, Loop B P1+P2 remain) | Fable preferred | S        |
| 3 | S10         | item 3                            | Opus            | M (solo) |
| 4 | Errors + A5 | items 4, 6, 9 + grid D2 (item 7)  | Opus            | M        |
| 5 | Ship        | items 10-12                       | developer-led   | -        |

**Session 3 (S10) COMPLETE 2026-07-11** (run on Opus 4.8; full suite 7304 green, pylint 10.00/10,
djlint + biome clean; representative pages re-shot both themes + mobile on real dev data). 24
breadcrumb templates deleted; one shared `back_link` macro (new `app/templates/_nav_macros.html`)
byte-identical to the shipped `loan/schedule` + `loan/setup` precedents; 16 sub-pages carry a "Back
to <parent>" button. Full as-built at item 3 above and at D1 in the polish register.

**Session 4 COMPLETE 2026-07-11** (run on Fable 5, the S3 precedent; commits 02c4000d -> 10d28499, 7
commits; full suite 7277 green, pylint 10.00/10; live-verified on a seeded throwaway server via
scripted Playwright, both themes: A5 named-cell create, the disabled-dropdown pre-hint, a forced
illegal transition swapping the designed 400 into the cell, and the retirement rail 422 swapping
inline through the new global listener after its shim was deleted).

Sessions 1, 2, and 4 are order-free among themselves; session 3 runs solo (it brushes ~24 templates,
so nothing else mid-flight); session 5 is last.

## Developer rulings (2026-07-11; the plan's two open decisions, closed)

- **Error-fragment convention (item 4): marker header.** Full spec inline at item 4. Session 4 is
  unblocked.
- **Grid D1: DROPPED; grid D2: SCHEDULED into session 4 with the pre-hint half** (card disables
  disallowed transitions). Recorded at D1/D2 in `grid_audit.md`.

## Adjacent open items (outside these two docs; triage at the acceptance drive)

From the grid/entries review session (recorded in session memory, not in a doc): mobile-cell
breakpoint staleness, add-form refocus, and the `detail.target` detached-node trap. The same
review's 422 error partials are item 4 above. Verify each still reproduces, then register or drop.

## Parked (recorded, no action)

- Theme selector Scope B (palettes beyond Steel Ink, selector UI + persistence, meta theme-color).
- Svelte 5 grid island: re-gated only if daily use shows the need.
- The two dashboard presentational deviations (persistent negative-point chart labels; the tracks
  target-date tick): REVISIT LATER by ruling, pending daily-use feedback.
- `investment_dashboard_service` and `balance_at` at the C0302 module-size ceiling: split when next
  touched.

## Session protocol

Unchanged from the polish plan: load the `shekel-design` skill; before/after shots in both themes (+
mobile where the surface renders there); targeted tests per change with the full suite once as the
session gate (item 9 landed 2026-07-11, so no `IDLE_TIMEOUT_MINUTES` prefix is needed anymore);
logical commits per finding; update the register; model discipline (`services/`/`routes/`/test-
assertion edits happen in an Opus context).
