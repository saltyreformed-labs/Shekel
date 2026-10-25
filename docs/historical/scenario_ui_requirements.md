> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Scenario UI Requirements (Phase 7 Preparation)

This document was created during UI/UX Remediation Phase 6 to capture the UI requirements that the Scenarios feature (application development Phase 7) will need to satisfy. It references audit findings from `docs/ui_ux_audit.md` Section 6 ("Future-Proofing for Phase 7") and the reserved UI slots added during this phase.

---

## 1. Global Scenario Indicator (Audit 6.2)

- The app needs a persistent, globally visible indicator showing which scenario the user is currently viewing.
- **Recommended location:** the reserved `#scenario-selector-slot` in `app/templates/base.html`, positioned between the brand and the main nav links inside the collapsed navbar area.
- The indicator should display the scenario name and provide a dropdown to switch between scenarios.
- The baseline scenario should be visually distinct (e.g., no badge or a subtle "Baseline" label) from user-created scenarios (which should show the scenario name prominently, possibly with a colored badge).
- When the user switches scenarios, all data on the current page should reload to reflect the selected scenario. This can use a full page reload with a `?scenario_id=` query parameter, or HTMX-driven partial reloads.
- Every route file that queries scenario-scoped data will need to read the active scenario: `grid.py`, `templates.py`, `transfers.py`, `salary.py`, `savings.py`, `retirement.py`, `charts.py`.

## 2. Grid Scenario Controls (Audit 6.3)

- The budget grid needs scenario controls in the reserved `#scenario-controls-slot` in `app/templates/grid/grid.html`.
- This row should contain: a scenario selector dropdown (if not already handled by the global indicator), a "Compare" mode toggle button, and (when compare mode is active) a second dropdown to select the comparison scenario.
- The scenario controls row must be visually separate from the period navigation row to avoid crowding.
- In compare mode, the grid could use one of three approaches (to be decided during Phase 7 implementation):
  1. Dual grids side by side
  2. A single merged grid with interleaved comparison columns
  3. A diff overlay on a single grid
- Note that the current grid CSS uses `grid-wrapper` with `max-height: calc(100vh - 160px)` and sticky columns/headers, which would need reworking for a dual-grid layout.

## 3. Chart Scenario Overlays (Audit 6.6)

- The charts dashboard already uses HTMX-loaded fragments with interactive controls per card.
- Each chart card should gain a scenario selector (multi-select or toggle) allowing overlay of data from multiple scenarios on the same chart.
- The chart rendering JavaScript files (`chart_theme.js`, `chart_balance.js`, etc.) will need to support multi-dataset rendering with distinct colors/line styles per scenario.
- No structural preparation is needed; the existing template pattern is compatible.

## 4. Retirement Gap Analysis (Audit 6.7)

- The retirement dashboard's sensitivity sliders already support parameter overrides.
- Scenario what-ifs (e.g., "what if I increase my 401(k) contribution?") follow this same pattern: change input parameters and recalculate.
- No structural preparation is needed.

## 5. Comparison View (Audit 6.4)

- A dedicated comparison page (`/scenarios/compare`) will show differences between two scenarios.
- Key metrics to highlight:
  - End-of-period checking balance differences
  - Total income/expense differences
  - Net worth trajectory differences across account types
- Balance differences should be color-coded: green for "better than baseline" and red for "worse than baseline."
- Layout decision (deferred to Phase 7): side-by-side grids vs. single merged grid vs. summary-only comparison page.

## 6. Resolved by Earlier Phases

- **Audit 6.1 (navbar capacity):** Resolved by Phase 4. The navbar now has 8 items, leaving room for "Scenarios" as a 9th top-level link.
- **Audit 6.5 (nomenclature compounding):** Resolved by Phase 2. "Recurring Transactions" is clear and will not be confusing when prefixed with a scenario name.

## 7. Reserved UI Slots Summary

The following hidden slots were added during UI/UX Remediation Phase 6:

| Element ID | File | Purpose |
|---|---|---|
| `#scenario-selector-slot` | `app/templates/base.html` | Global scenario selector dropdown in the navbar |
| `#scenario-controls-slot` | `app/templates/grid/grid.html` | Grid-specific scenario controls (selector, compare toggle, comparison target) |
