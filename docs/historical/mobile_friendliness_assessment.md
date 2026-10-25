> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel Mobile-Friendliness Assessment

**Date:** 2026-04-06

## Step 1: Current State Audit

### 1. Viewport Meta Tag

**Yes, present and correct.** `base.html:6`:
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0">
```

### 2. Bootstrap 5 Responsive Class Usage

**Inconsistent.** Bootstrap 5.3.8 is loaded via CDN. The responsive grid system (`col-md-*`,
`col-lg-*`) is used well in forms and dashboards, but data tables -- the app's core UI -- are
hit-or-miss:

- **Good:** Charts dashboard (`col-12`, `col-lg-6`), savings dashboard (`col-md-6`, `col-lg-4`),
  all form pages (`col-md-8 col-lg-6` centered), categories (`col-md-8` + `col-md-4`), retirement
  dashboard (`col-md-6`)
- **Bad:** 8 tables across the app lack `table-responsive` wrappers entirely. No
  `d-none d-md-table-cell` visibility classes are used anywhere to hide non-critical columns on
  mobile.

### 3. Hardcoded Widths and Fixed Layouts

**Multiple problems found:**

| Location | Issue |
|---|---|
| `app.css:258-281` | Grid columns: `min-width: 150px` (wide), `110px` (medium), `80px` (compact) |
| `app.css:619-632` | Full-edit popover: `width: 280px` fixed |
| `app.css:490-491` | Quick-edit form: `min-width: 180px` |
| `app.css:502-507` | `.btn-xs`: padding `0.1rem 0.35rem` (~24px rendered height) |
| `grid_edit.js:35` | Popover height hardcoded at `300px` for positioning logic |
| `grid_edit.js:48` | Popover width check hardcoded at `280` |
| `retirement/dashboard.html` | Input width: `style="width: 7rem; flex-shrink: 0;"` |
| `investment/dashboard.html` | Amount input: `style="width: 100px;"` |
| `debt_strategy/dashboard.html` | Table column: `style="width: 80px;"` |

### 4. Budget Grid at Mobile Widths

**Broken.** The budget grid is the app's most critical page and its worst mobile offender.

The grid is a `<table>` with 1 sticky label column + N period columns (typically 6-52). At the
CSS level:
- Sticky column shrinks from 160-200px to 90px at `<576px` (`app.css:773-795`)
- Period columns have a floor of 80px even in compact mode
- Font shrinks to 0.65-0.7rem (9.1-9.8px) -- borderline illegible
- Text-overflow is ellipsis, so row labels (item names) get truncated

The grid does have a `table-responsive` wrapper (`grid.html:72`), so it scrolls horizontally.
But on a 320px screen with a 90px sticky column, you see exactly one period column at 80px plus
~150px of a second. The user experience is constant horizontal scrolling through an illegible
table.

There is **no mobile-alternative layout** -- no card view, no single-period view, no collapsible
categories.

### 5. Touch Target Sizing

**Fails WCAG 2.5.8 (44x44px minimum) in multiple places:**

| Element | Estimated Size | Location |
|---|---|---|
| `.btn-xs` action buttons | ~20-24px | `app.css:502-507` |
| Transaction cell tap area | ~24px height | `app.css:465-487` (padding `0.15rem 0.25rem`) |
| Quick-edit expand button (three dots) | ~20px | `app.css:604-610` |
| Period nav quick-select (3P, 6P, etc.) | ~24-32px | `app.css:575-580` |
| `.btn-sm` action buttons | ~32px | Various list templates |

Only modal buttons and full-width form submit buttons meet the 44px minimum.

### 6. Hover-Only Interactions

**Several found, no touch equivalents:**

- **Transaction cell hover:** `app.css:471-474` -- subtle blue background on `:hover` with no
  `:active` or `:focus` state. Mobile users get zero visual feedback before tapping.
- **Anchor balance hover:** `app.css:452-454` -- same issue.
- **Retirement info icons:** `_retirement_account_table.html` uses `title=""` attributes for
  explanation text. These tooltips only display on hover; inaccessible on touch devices.
- **Transaction status badges:** `_transaction_cell.html` uses `title="{{ t.status.name }}"` --
  hover-only.

No `touchstart`/`touchend` handlers exist in any JS file. No swipe gesture support for
horizontal grid scrolling.

### 7. Navigation Collapse

**Works correctly.** The navbar uses `navbar-expand-md` (`base.html:35`) with a proper hamburger
toggle button, `data-bs-toggle="collapse"`, and ARIA attributes. At `<768px`, the navbar
collapses into a hamburger menu. This is standard Bootstrap behavior and functions properly.

### 8. Modals, Dropdowns, and HTMX Targets at 320-428px

- **Add Transaction modal:** Standard Bootstrap `modal-dialog` -- takes ~90% viewport width on
  mobile, form fields stack vertically. **Usable.**
- **Full-edit popover:** Fixed at 280px wide. At 320px, this leaves 20px margins on each side. At
  280px viewport (older devices), it overflows. The JS positioning (`grid_edit.js:23-56`) clamps
  `leftPos` to `window.innerWidth - 290`, which could push it 10px off-left on tiny screens.
  **Marginal.**
- **Loan dashboard tabs:** 6 tabs in `nav nav-tabs` -- will wrap onto two lines or scroll on
  narrow screens. **Needs work.**
- **HTMX partials:** All use proper `_` prefix convention and return fragment HTML. The swap
  targets are within the page flow and render correctly at any width. **Fine.**

### 9. Per-Template Mobile Readiness Rating

88 template files total. Grouped by rating:

**Mobile-Ready (41 templates)** -- work correctly at mobile widths:

| Template | Notes |
|---|---|
| `base.html` | Viewport tag, responsive navbar, container-fluid |
| `auth/login.html` | Centered `col-md-4 col-lg-3`, stacks on mobile |
| `auth/register.html` | Same as login |
| `auth/mfa_setup.html` | Simple centered form |
| `auth/mfa_verify.html` | Simple centered form |
| `auth/mfa_disable.html` | Simple centered form |
| `auth/mfa_backup_codes.html` | Simple centered content |
| `charts/dashboard.html` | `col-12 col-lg-6`, charts stack on mobile |
| `charts/_amortization.html` | Chart.js with `responsive: true` |
| `charts/_balance_over_time.html` | Chart.js responsive |
| `charts/_budget_vs_actuals.html` | Chart.js responsive |
| `charts/_net_pay.html` | Chart.js responsive |
| `charts/_net_worth.html` | Chart.js responsive |
| `charts/_spending_category.html` | Chart.js responsive |
| `charts/_error.html` | Simple error message |
| `accounts/form.html` | Centered `col-md-8 col-lg-6` |
| `accounts/checking_detail.html` | `col-md-6` + `table-responsive` |
| `accounts/interest_detail.html` | Same responsive pattern |
| `accounts/_anchor_cell.html` | Inline content, no fixed widths |
| `categories/list.html` | `col-md-8` + `col-md-4`, stacks on mobile |
| `categories/_category_row.html` | Simple row partial |
| `salary/form.html` | `col-lg-8` centered, `col-md-6` field groups |
| `salary/breakdown.html` | `col-lg-6 col-md-8` centered |
| `salary/calibrate.html` | Simple form page |
| `salary/calibrate_confirm.html` | Simple confirmation page |
| `salary/projection.html` | Table with `table-responsive` |
| `transfers/form.html` | `col-md-8 col-lg-6` centered |
| `transfers/_transfer_cell.html` | Inline cell content |
| `savings/goal_form.html` | Standard Bootstrap form |
| `retirement/pension_form.html` | Standard Bootstrap form |
| `retirement/_gap_analysis.html` | Simple table + chart |
| `loan/setup.html` | Standard form |
| `pay_periods/generate.html` | Simple form |
| `grid/_anchor_edit.html` | Inline edit form |
| `grid/_transaction_cell.html` | Inline cell display |
| `grid/_transaction_empty_cell.html` | Simple placeholder |
| `grid/no_periods.html` | Informational page |
| `grid/no_setup.html` | Informational page |
| `_form_macros.html` | Reusable form components |
| `_confirm_modal.html` | Standard Bootstrap modal |
| `_keyboard_help.html` | Help dialog |
| `errors/400.html` -- `500.html` | Simple centered error pages |

**Needs-Work (20 templates)** -- functional but with specific issues:

| Template | Issues |
|---|---|
| `accounts/list.html` | Missing `table-responsive` wrapper |
| `salary/list.html` | Missing `table-responsive`, 7 columns, icon-only action buttons |
| `salary/tax_config.html` | Table layout may overflow |
| `salary/_deductions_section.html` | Table without responsive wrapper |
| `salary/_raises_section.html` | Table without responsive wrapper |
| `transfers/list.html` | Missing `table-responsive`, 7 columns |
| `transfers/_transfer_quick_edit.html` | Small touch targets in edit form |
| `transfers/_transfer_full_edit.html` | Fixed-width popover |
| `templates/list.html` | Missing `table-responsive`, 7 columns |
| `templates/form.html` | Complex recurrence UI may crowd on narrow screens |
| `savings/dashboard.html` | Button wrapping in flex containers, metric sizing |
| `settings/dashboard.html` | Sidebar `col-md-3`/`col-md-9` -- functional but cramped |
| `settings/_*.html` (7 partials) | Various form layouts that may need tweaking |
| `loan/dashboard.html` | 6 tabs wrap poorly, alert form inline layout |
| `loan/_schedule.html` | Has `table-responsive` but 7-10 columns, no column hiding |
| `loan/_rate_history.html` | Missing `table-responsive` |
| `retirement/dashboard.html` | Hardcoded `width: 7rem` on slider inputs |
| `retirement/_retirement_account_table.html` | Missing `table-responsive`, hover-only tooltips |
| `investment/dashboard.html` | Hardcoded `width: 100px`, complex slider layout |
| `debt_strategy/dashboard.html` | Hardcoded `width: 80px` in custom priority table |
| `obligations/summary.html` | 3 large tables (6-7 columns each) |

**Broken (4 templates)** -- fundamentally unusable at mobile widths:

| Template | Issues |
|---|---|
| `grid/grid.html` | 26+ column table with 80px minimums, illegible at 9.8px font, no mobile-alternative view |
| `grid/_transaction_quick_edit.html` | Edit form designed for desktop cell widths, 20px expand button |
| `grid/_transaction_quick_create.html` | Same as quick edit |
| `grid/_transaction_full_edit.html` | 280px fixed-width popover with `btn-xs` action buttons |
| `grid/_transaction_full_create.html` | Same as full edit |

---

## Step 2: Mobile-Friendliness Tiers

### Tier 1: Mobile-Responsive Web

Make the existing app usable when opened in a mobile browser. No new technology -- just CSS,
template, and minor JS fixes.

#### A. Quick Wins (1-2 days)

These are mechanical fixes that don't require rethinking any UI:

1. **Add `table-responsive` wrappers** to the ~8 tables missing them: `accounts/list.html`,
   `salary/list.html`, `transfers/list.html`, `templates/list.html`, `loan/_rate_history.html`,
   `retirement/_retirement_account_table.html`, `salary/_deductions_section.html`,
   `salary/_raises_section.html`. Each is a one-line `<div class="table-responsive">` wrapper.

2. **Remove hardcoded inline widths** in `retirement/dashboard.html` (`7rem`),
   `investment/dashboard.html` (`100px`), `debt_strategy/dashboard.html` (`80px`). Replace with
   Bootstrap responsive utilities or percentage-based widths.

3. **Add `:active` and `:focus-visible` states** alongside every `:hover` rule in `app.css`.
   This gives mobile users visual feedback when they tap. Approximately 5 hover rules need touch
   equivalents (lines 452, 472, plus any others).

4. **Replace hover-only `title` tooltips** on retirement info icons with Bootstrap popovers
   triggered by click/tap, or visually inline the explanatory text on small screens with
   `d-md-none` helper text.

5. **Add a 320px media query** to `app.css` with further reductions for the grid's sticky column
   and font sizes, plus a `min-height` on the grid wrapper to prevent it from being crushed.

#### B. Table Column Hiding (1-2 days)

Add responsive visibility classes to data-heavy tables so non-essential columns hide on small
screens:

- `salary/list.html`: Hide Filing Status and State columns (`d-none d-lg-table-cell`)
- `transfers/list.html`: Hide Frequency column
- `templates/list.html`: Hide Category and Type columns
- `obligations/summary.html`: Hide Account and Category columns
- `loan/_schedule.html`: Hide Escrow, Extra, and Rate columns
- `salary/list.html`: Collapse 4 icon-only action buttons into a Bootstrap dropdown on mobile

#### C. Touch Target Fixes (1 day)

- Increase `.btn-xs` padding to at least `0.4rem 0.6rem` (renders ~36-40px, reasonable
  compromise)
- Increase `.txn-cell` padding to `0.35rem 0.4rem` minimum
- Increase quick-edit expand button to 32px minimum
- Increase period nav quick-select buttons (3P, 6P, etc.) padding
- Add `min-height: 44px` to form controls inside the full-edit popover

#### D. Budget Grid Mobile Layout (3-5 days) -- the hard one

The grid is fundamentally a desktop layout. Options:

**Option D1: Single-Period Card View** (recommended)
- At `<768px`, replace the multi-column table with a single-period view
- Show one pay period at a time with left/right swipe or arrow buttons to navigate
- Each transaction renders as a card/list-item: name on left, amount on right, status badge,
  tap to edit
- Category group headers as collapsible accordion sections
- Balance summary pinned at bottom
- This is a different template (`grid/_mobile_grid.html`) served conditionally via a responsive
  `d-none d-md-block` / `d-md-none` toggle, or via server-side user-agent detection
- **Effort:** 3-5 days. Requires a new partial template, a route change or template conditional,
  and mobile-specific CSS. The data is already structured per-period in the route, so no service
  changes needed.

**Option D2: Improved Horizontal Scroll** (simpler, less effective)
- Keep the table, but add momentum scrolling CSS (`-webkit-overflow-scrolling: touch`)
- Add a visible horizontal scrollbar or scroll indicator
- Reduce grid to compact mode automatically at `<768px`
- Add column-freezing controls so users pick which periods to show
- **Effort:** 1-2 days. Less disruptive but the experience is still "scrolling a desktop table
  on a phone."

**Option D3: Responsive Column Reduction** (middle ground)
- At `<768px`, show only 1-2 period columns plus the label column
- Add period selector dropdown above the table
- No fundamental layout change
- **Effort:** 2-3 days. Requires template conditionals and a period-selector control.

I recommend **D1 (single-period card view)** as the target, with **D2** as a quick stopgap.

#### E. Full-Edit Popover (1 day)

- Change `width: 280px` to `width: min(280px, calc(100vw - 2rem))` so it shrinks on narrow
  screens
- Update JS positioning logic to use `Math.min(280, window.innerWidth - 32)` instead of
  hardcoded `280`
- Increase `btn-xs` buttons inside the popover to proper touch-sized targets
- Consider making the popover full-screen-width on mobile as a bottom sheet

#### F. Loan Dashboard Tabs (0.5 days)

- Add `nav-pills` with horizontal scroll (`flex-nowrap overflow-auto`) on mobile instead of
  wrapping tabs
- Or collapse to a `<select>` dropdown on `<576px` that switches tab content

**Total Tier 1 estimate: 8-12 days**, with the budget grid mobile layout being the bulk of it.

---

### Tier 2: Progressive Web App (PWA)

Everything from Tier 1, plus installability and (limited) offline support.

#### What's Needed

1. **Web App Manifest** (`manifest.json`): App name, icons (192px and 512px PNG), theme color,
   display mode (`standalone`), start URL. Linked from `base.html`. ~0.5 days including icon
   creation.

2. **Service Worker** (`sw.js`): Registered from `base.html` or a JS file. Handles caching
   strategy.

3. **HTTPS**: Already satisfied -- Cloudflare Tunnel provides TLS.

4. **Icons**: Need 192x192 and 512x512 PNG versions of the Shekel logo for the home screen icon.

#### What to Cache

- **Static assets** (CSS, JS, fonts, images): Cache-first strategy. These rarely change. ~15
  files.
- **App shell** (`base.html` skeleton, navbar, empty grid wrapper): Cache with network-first
  fallback so the app "opens" instantly.
- **Last-viewed grid data**: Cache the most recent grid HTML response so the user can see their
  budget offline. Stale data is better than a blank screen for a budget app.

#### What NOT to Cache

- POST/PATCH/DELETE operations (mutations must hit the server)
- Auth routes (session-dependent)
- Dynamic HTMX partials (stale data risk for in-flight edits)

#### Limitations

**HTMX is the core problem.** The entire app is server-rendered. Every interaction (cell edit,
period navigation, status change, modal load) makes an HTTP request. Offline, none of these
work.

Realistic offline capability for Shekel:

- **Read-only budget view**: Cache the last grid render. User can review their budget but not
  edit.
- **Read-only dashboards**: Cache last-viewed charts, savings, retirement, loan pages.
- **Offline queue for edits**: This is technically possible (intercept HTMX requests in the
  service worker, store in IndexedDB, replay when online) but adds enormous complexity for a
  solo developer. Conflict resolution when the server state has changed is a real problem.

#### Honest Assessment

For a single-user personal app, the PWA mostly buys you **"add to home screen" with an app icon
and a splash screen**. The offline read-only view is nice but limited. The effort/value ratio is
low compared to Tier 1.

**Effort: 2-3 days** on top of Tier 1 for basic PWA (manifest + service worker + static caching
+ last-page caching). An offline edit queue would add 5-10 days and significant ongoing
maintenance burden.

---

### Tier 3: Native iOS App

#### Option 3A: Hybrid Wrapper (Capacitor/Ionic)

Capacitor wraps a web view (WKWebView on iOS) around your existing web app. In theory, you
point it at your Flask server and ship it.

**The problem:** Shekel is 100% server-rendered via HTMX. Capacitor's WKWebView would just be
loading `https://your-tunnel-url/` in a frame. This is functionally identical to a PWA "add to
home screen" -- but with Apple's App Store review process, code signing, provisioning profiles,
and annual $99 developer fee.

**What you'd gain over a PWA:**
- Push notifications (via APNs instead of Web Push)
- Home screen icon without the "Add to Home Screen" friction
- Biometric auth (Face ID/Touch ID) via Capacitor plugin
- Access to native APIs (camera, haptics, etc.) -- but a budget app doesn't need these

**What you'd lose:**
- Instant updates (App Store review delays)
- Simplicity (Xcode build toolchain, CocoaPods/SPM, provisioning profiles)
- Time (initial setup: 2-3 days; ongoing maintenance: every iOS update)

**Verdict:** Not worth it for a single-user app. A PWA gives you 90% of the benefit at 10% of
the cost.

#### Option 3B: Native Swift/SwiftUI Rewrite

A full rewrite would mean:
- New SwiftUI frontend consuming a JSON API
- Building that JSON API in Flask (your current routes return HTML, not JSON)
- CoreData or SwiftData for local persistence
- Sync engine between local DB and PostgreSQL server
- Reimplementing every screen: grid, dashboards, charts, forms, settings, auth

**Realistic estimate for a solo developer:**
- JSON API layer: 3-5 weeks (50+ endpoints covering all current routes)
- SwiftUI app with all screens: 8-12 weeks
- Sync engine with conflict resolution: 4-6 weeks
- Testing and polish: 2-4 weeks
- **Total: 4-6 months of focused work**, assuming SwiftUI proficiency

**Verdict:** Wildly disproportionate for a personal-use app. You'd be maintaining two complete
frontends indefinitely.

#### Option 3C: React Native or Flutter Rewrite

Same JSON API requirement as 3B, plus:
- **React Native:** JS/TS frontend, Expo for build tooling. Faster iteration than Swift but
  still requires the full API layer. You'd lose HTMX simplicity and gain a JS build pipeline.
  ~3-4 months.
- **Flutter:** Dart frontend. Same API requirement. Good cross-platform (iOS + Android) but
  you'd be learning a new language and framework. ~3-4 months.

**Verdict for both:** Same fundamental problem as 3B -- you need a JSON API that doesn't exist,
and you'd be maintaining two frontends. The only scenario where this makes sense is if you
wanted to abandon the Flask/HTMX frontend entirely and go API-first.

---

### Tier 4: Other Options

#### 4A: Mobile-Optimized Route Set in Flask

Instead of making every template responsive, create a parallel set of mobile-specific routes
and templates:

- `/m/` prefix: `/m/grid`, `/m/accounts`, `/m/savings`, etc.
- Mobile templates designed from scratch for 320-428px
- Same Flask services and models -- just different routes returning different HTML
- Auto-redirect based on user-agent or a toggle in settings
- Desktop templates remain untouched

**Pros:** No compromises on either desktop or mobile UX. Each layout is purpose-built.
**Cons:** Double the template maintenance. Every feature must be implemented in two template
sets.
**Effort:** 2-3 weeks for core pages (grid, accounts, dashboards), ongoing doubling of template
work.
**Verdict:** Overkill for a single-user app. Tier 1 responsive fixes are sufficient.

#### 4B: Lightweight JSON API + Standalone Mobile Frontend

Add a `/api/v1/` route set returning JSON alongside existing HTML routes. Build a minimal
mobile frontend (could be a single-page app with vanilla JS, or even a simple HTML/CSS/JS app
using fetch):

- API endpoints mirror existing service layer
- Mobile app is a static site (hostable on same server or CDN)
- No App Store, no build tools beyond a text editor

**Pros:** Clean separation. Mobile app can work partially offline with local storage.
**Cons:** Building and maintaining a JSON API + a separate frontend is significant work. Auth
needs to work for API tokens.
**Effort:** 3-5 weeks.
**Verdict:** Only makes sense if you eventually want third-party integrations or automation. For
personal mobile access, Tier 1 is enough.

#### 4C: Tauri Mobile

Tauri 2.0 supports iOS and Android. It uses a system web view (like Capacitor) but with a Rust
backend instead of Node. For Shekel, which already has a Python backend, Tauri adds no value
over Capacitor -- it's just a different web view wrapper with the same server-dependency problem.

**Verdict:** No advantage over Capacitor for this architecture. Skip.

---

## Step 3: Recommendation

**Target Tier 1 (Mobile-Responsive Web).** Here's why:

1. **You're a solo developer with a single-user app.** Every hour spent on native wrappers, sync
   engines, or parallel frontends is an hour not spent on the actual budgeting features. The ROI
   of Tiers 2-4 is near zero for your use case.

2. **The existing stack already does 70% of the work.** Bootstrap 5, proper viewport meta tag,
   responsive navbar -- the foundation is solid. The problems are specific and enumerable:
   missing `table-responsive` wrappers, hardcoded widths, undersized touch targets, and the grid
   needing a mobile layout.

3. **The budget grid is the only hard problem.** Everything else is 1-2 day fixes. The grid
   needs a single-period card view at mobile widths (Option D1 from above), which is 3-5 days
   of focused work. That one change transforms the app from "broken on mobile" to "usable on
   mobile."

4. **PWA adds minimal value.** Your app requires server connectivity for every interaction.
   Offline read-only caching is nice but not worth the service worker complexity. If you want
   the "home screen icon" experience, you can add just the manifest file (0.5 days) without the
   full service worker -- modern browsers will offer "Add to Home Screen" with just a manifest.

5. **Maintainability matters most.** One set of templates, one CSS file, one deployment.
   Responsive CSS is the most maintainable path forward because it's the same codebase, not a
   parallel one.

**Suggested order of work:**

1. Quick wins: `table-responsive` wrappers, hardcoded width removal, touch states (~2 days)
2. Touch target sizing across the app (~1 day)
3. Table column hiding for data-heavy pages (~1-2 days)
4. Budget grid mobile layout -- single-period card view (~3-5 days)
5. Full-edit popover responsive fix (~1 day)
6. (Optional) Web app manifest for home screen icon (~0.5 days)

**Total: ~8-12 days** to go from "broken on phones" to "genuinely usable on phones" without
adding any new technology to the stack.
