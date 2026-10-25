> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Plan: Calendar Inline Totals and Day Detail Section

**Date:** April 9, 2026
**Scope:** Replace calendar marker dots and broken Bootstrap popovers with inline
income/expense totals per day and a click-to-show detail section below the grid.
**Prerequisite:** Section 8 complete. Calendar is live with marker dots and popovers.

---

## Problem

Two bugs in the current calendar month view:

1. **Popovers don't dismiss.** `data-bs-trigger="click focus"` on `<div>` elements causes
   Bootstrap's click-toggle state to block focus-based dismissal. Users must refresh the page.
2. **Popovers truncate items.** `_build_popover_html()` hard-caps at 5 entries with a static
   "+N more" label. No scroll, no expansion.

Rather than fix the popovers, replace them entirely with a better UX: inline totals in each
day cell for at-a-glance financial visibility, and a detail section below the calendar for
full transaction lists on click.

---

## Design

### Day cells: inline totals (replaces marker dots)

Each day cell currently shows colored dots (max 3) with a "+N more" overflow label. Replace
with small text showing the day's income total (green) and expense total (red). Only show a
line if its total is non-zero. Most days have only expenses, so typically one line.

```
 +---------+
 | 15      |
 | +$3,200 |  <-- green, only if income > 0
 | -$485   |  <-- red, only if expenses > 0
 +---------+
```

This gives immediate financial visibility without any interaction. Large upcoming expenses are
instantly obvious.

### Detail section: click-to-expand (replaces popovers)

A `<div id="calendar-day-detail">` below the calendar grid (above the monthly summary). When
the user clicks a day with transactions:

- The detail section shows all transactions for that day in a compact table.
- The clicked day cell gets a visual highlight (selected state).
- Clicking another day replaces the detail and moves the highlight.
- Clicking the same day again hides the detail (toggle off).
- Clicking an empty day (no transactions) does nothing.

The detail table columns: Name, Category, Amount, Status. These map directly to existing
`DayEntry` fields. No new data needed.

### What does NOT change

- **`calendar_service.py`** -- Untouched. `DayEntry` and `MonthSummary` already provide all
  needed data (per-entry amounts, `is_income`, `is_paid`, `category_group`, `category_item`).
- **`_calendar_year.html`** -- Year view already shows month-level totals, no markers.
- **`csv_export_service.py`** -- Reads from `MonthSummary.day_entries`, unaffected.
- **`analytics.html`** -- Parent page with tab pills, unchanged.

---

## Implementation

### File 1: `app/routes/analytics.py`

**In `_build_calendar_weeks()`:**

- Compute `income_total` and `expense_total` per day from the entries list. Add these two
  keys to each day dict. This follows the coding standard: "Templates are for display, not
  computation."
- Remove `popover_html` key from the day dict.

**Delete `_build_popover_html()`** -- No longer called. Also removes the `markupsafe.escape`
import if it becomes unused.

### File 2: `app/templates/analytics/_calendar_month.html`

**Day cells:** Replace the markers block:

```jinja
{# REMOVE: marker dots and popover data attributes #}
data-bs-toggle="popover" data-bs-trigger="click focus" data-bs-html="true"
data-bs-content="{{ day.popover_html }}" tabindex="0" role="button"
...
<div class="calendar-markers">
  {% for entry in day.entries[:3] %} ... {% endfor %}
</div>
```

Replace with inline totals:

```jinja
{# Day cells become clickable when they have entries #}
{% if day.entries %}data-day="{{ day.number }}" role="button" tabindex="0"{% endif %}
...
<div class="calendar-day-totals">
  {% if day.income_total %}
  <div class="calendar-day-income font-mono">${{ "{:,.0f}".format(day.income_total|float) }}</div>
  {% endif %}
  {% if day.expense_total %}
  <div class="calendar-day-expense font-mono">${{ "{:,.0f}".format(day.expense_total|float) }}</div>
  {% endif %}
</div>
```

Note: Using `|float` for Jinja formatting follows the existing pattern in this template
(lines 76, 80, 84, 90). Amounts are computed as `Decimal` in Python; the `|float` conversion
is for display formatting only, not for financial arithmetic.

**Detail section:** Add below the calendar grid, above the monthly summary:

```jinja
{# Day detail -- shown when a day cell is clicked #}
<div id="calendar-day-detail" class="mb-3"></div>

{# Pre-rendered detail content, hidden until selected #}
{% for week in weeks %}
{% for day in week %}
{% if day.entries %}
<template data-detail-day="{{ day.number }}">
  <div class="d-flex justify-content-between align-items-center mb-2">
    <h6 class="mb-0">{{ month_name }} {{ day.number }}, {{ year }}</h6>
    <button type="button" class="btn-close btn-close-sm" aria-label="Close"
            id="calendar-detail-close"></button>
  </div>
  <div class="table-responsive">
    <table class="table table-sm mb-0">
      <thead>
        <tr>
          <th>Name</th>
          <th>Category</th>
          <th class="text-end">Amount</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {% for entry in day.entries %}
        <tr>
          <td>{{ entry.name }}</td>
          <td class="text-muted">{{ entry.category_group|default("--", true) }}</td>
          <td class="text-end font-mono {{ 'text-success' if entry.is_income else 'text-danger' }}">
            ${{ "{:,.2f}".format(entry.amount|float) }}
          </td>
          <td><span class="badge {{ 'bg-success' if entry.is_paid else 'bg-secondary' }}">
            {{ "Paid" if entry.is_paid else "Projected" }}
          </span></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</template>
{% endif %}
{% endfor %}
{% endfor %}
```

Using `<template>` tags: these are not rendered in the DOM until JS clones their content
into `#calendar-day-detail`. This avoids hidden-div overhead and keeps the DOM clean. This is
vanilla HTML, not a framework.

### File 3: `app/static/js/calendar.js`

Replace popover initialization with day-click toggle logic:

```javascript
/* On HTMX settle, bind click handlers to day cells with data-day attributes. */
document.addEventListener('htmx:afterSettle', function(event) {
  var tabContent = document.getElementById('tab-content');
  if (!tabContent || !tabContent.contains(event.detail.target || event.detail.elt)) return;

  var detailContainer = tabContent.querySelector('#calendar-day-detail');
  if (!detailContainer) return;
  var activeDay = null;

  tabContent.querySelectorAll('.calendar-day[data-day]').forEach(function(cell) {
    cell.addEventListener('click', function() {
      var day = cell.getAttribute('data-day');
      var template = tabContent.querySelector('template[data-detail-day="' + day + '"]');
      if (!template) return;

      /* Toggle off if clicking the same day */
      if (activeDay === day) {
        detailContainer.innerHTML = '';
        cell.classList.remove('calendar-day--selected');
        activeDay = null;
        return;
      }

      /* Deselect previous */
      if (activeDay !== null) {
        var prev = tabContent.querySelector('.calendar-day--selected');
        if (prev) prev.classList.remove('calendar-day--selected');
      }

      /* Show new detail */
      detailContainer.innerHTML = '';
      detailContainer.appendChild(template.content.cloneNode(true));
      cell.classList.add('calendar-day--selected');
      activeDay = day;

      /* Close button inside the detail */
      var closeBtn = detailContainer.querySelector('#calendar-detail-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', function() {
          detailContainer.innerHTML = '';
          cell.classList.remove('calendar-day--selected');
          activeDay = null;
        });
      }
    });
  });
});
```

Remove all popover-related code (`disposePopovers`, `bootstrap.Popover` calls).

### File 4: `app/static/css/app.css`

**Remove:**
- `.calendar-markers`, `.calendar-marker`, `.calendar-income` (marker dot),
  `.calendar-expense` (marker dot), `.calendar-large`, `.calendar-infrequent`,
  `.calendar-more`
- Mobile media query rules for `.calendar-marker` and `.calendar-large`

**Add:**

```css
/* Day cell totals */
.calendar-day-totals {
  margin-top: 2px;
  line-height: 1.2;
}

.calendar-day-income {
  font-size: 0.65rem;
  color: var(--shekel-done);
}

.calendar-day-expense {
  font-size: 0.65rem;
  color: var(--shekel-danger);
}

/* Clickable day cells */
.calendar-day[data-day] {
  cursor: pointer;
}

.calendar-day[data-day]:hover {
  background-color: var(--shekel-surface-raised, var(--bs-tertiary-bg));
}

.calendar-day--selected {
  outline: 2px solid var(--shekel-accent);
  outline-offset: -2px;
  z-index: 1;
}

/* Day detail section */
#calendar-day-detail:empty {
  display: none;
}

/* Mobile: smaller totals text */
@media (max-width: 767.98px) {
  .calendar-day-income,
  .calendar-day-expense {
    font-size: 0.6rem;
  }
}
```

Note: `.calendar-day--selected` reuses the same outline pattern as `.calendar-day--today`.
When a selected day is also today, `--selected` takes visual precedence due to later source
order (same specificity). Both use `outline` so they don't conflict structurally.

**Keep:** `.calendar-income` and `.calendar-expense` class names are NOT reused -- the new
classes are `.calendar-day-income` and `.calendar-day-expense` to avoid confusion.

### File 5: `tests/test_routes/test_analytics.py`

**Tests to update:**

Tests that assert for removed HTML will need updating:

- `test_calendar_month_has_day_cells` -- Still passes (checks for `calendar-day` class,
  unchanged).
- `test_calendar_paycheck_highlighting` -- Still passes (checks for `calendar-paycheck`,
  unchanged).

No existing tests assert for `calendar-marker`, `popover`, or `data-bs-toggle`, so no
existing tests should break from the HTML structure change. The tests primarily check for
status codes, CSS class presence (`calendar-grid`, `calendar-day`, `calendar-paycheck`,
`calendar-day--today`), navigation params, and month name strings -- all of which remain.

**New tests to add:**

| Test | Asserts |
|------|---------|
| `test_calendar_day_totals_rendered` | Day with income+expense entries shows `calendar-day-income` and `calendar-day-expense` elements |
| `test_calendar_day_detail_template` | Day with entries has a `<template data-detail-day>` element with transaction name |
| `test_calendar_no_popover_attributes` | Response does not contain `data-bs-toggle="popover"` |
| `test_calendar_day_click_attributes` | Day with entries has `data-day` and `role="button"` attributes |

---

## Files changed (summary)

| File | Action |
|------|--------|
| `app/routes/analytics.py` | Modify `_build_calendar_weeks`, delete `_build_popover_html` |
| `app/templates/analytics/_calendar_month.html` | Replace markers with totals, add detail section |
| `app/static/js/calendar.js` | Replace popover init with day-click toggle |
| `app/static/css/app.css` | Remove marker CSS, add totals and detail CSS |
| `tests/test_routes/test_analytics.py` | Add new tests for inline totals and detail section |

No new files. No migrations. No service layer changes. No new endpoints.

---

## Tradeoffs considered

**Pre-rendered detail vs. HTMX endpoint:** The day detail could be loaded via a new HTMX
endpoint (`/analytics/calendar/day?day=N`). But the data is already queried and available in
`_render_month_view` -- adding an endpoint would re-query the same data. Pre-rendering
`<template>` elements is simpler, faster, and avoids a server round-trip. The HTML overhead is
negligible (a month has at most ~31 days, typically 20-40 transactions total).

**`<template>` tags vs. hidden divs:** `<template>` content is inert -- not rendered, not part
of the DOM tree, no performance cost. Hidden divs would work but add unnecessary DOM nodes.
`<template>` is the semantically correct choice per HTML spec.

**Whole-dollar totals in cells:** Using `{:,.0f}` (no cents) for inline totals keeps them
compact. The detail table shows full `{:,.2f}` amounts. This matches the year view, which
also uses `{:,.0f}` for month cards.

**No abbreviation (e.g., "$1.5K"):** Considered for mobile but rejected. Most household
expenses are under $9,999 which fits in the cell at 0.6rem. Abbreviation adds complexity
and reduces precision for minimal gain.

---

## Success criteria

1. Calendar day cells show green income total and/or red expense total inline (no dots).
2. Clicking a day with transactions shows a detail table below the calendar with ALL entries.
3. Clicking another day replaces the detail; clicking the same day hides it.
4. A close button in the detail section also dismisses it.
5. No Bootstrap popovers remain in the calendar.
6. Year view unchanged and functional.
7. CSV export unchanged and functional.
8. All existing tests pass (updated where HTML structure changed).
9. New tests verify inline totals, detail templates, and absence of popovers.
10. `pylint app/ --fail-on=E,F` passes with no new warnings.
11. Responsive at Bootstrap md and sm breakpoints.

---

## Commit

```
refactor(calendar): replace marker dots and popovers with inline totals and day detail section
```
