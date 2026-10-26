> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Bug Investigation: Transfer Template Rename 500 + Blank Error Page

**Date:** 2025-04-11
**Reported behavior:** Renaming a recurring transfer template from "Checking -> Fidelity Money
Market Savings Contribution" to "Emergency Fund" produces HTTP 500. The 500 page is a blank white
page with only "Internal Server Error" text. Creating a new template does not exhibit this bug.
Reproducible in production with a specific template but not in development.

---

## Issue A: Transfer Template Rename Produces 500

### Root Cause

The `update_transfer_template` route (`app/routes/transfers.py:275-372`) has a **misplaced
IntegrityError catch**. The try/except wraps only the final `db.session.commit()` (line 365-370),
but the actual constraint violation fires earlier during `regenerate_for_template`.

**The sequence:**

1. **Line 340:** `setattr(template, field, value)` changes the template name in memory. The
   SQLAlchemy ORM marks the template as dirty.

2. **Line 350-353:** `regenerate_for_template()` is called. This function deletes old transfers
   and then calls `db.session.flush()` at `transfer_recurrence.py:181`.

3. **That flush persists ALL dirty ORM objects** -- including the template name change from step 1.
   If the new name conflicts with `uq_transfer_templates_user_name` (the unique constraint on
   `user_id + name`), `IntegrityError` fires HERE.

4. **Line 354:** The except clause only catches `RecurrenceConflict`. The `IntegrityError` is
   uncaught and bubbles up as a 500.

5. **Lines 365-370:** The try/except IntegrityError on `db.session.commit()` never executes
   because the exception already escaped at step 3.

**Why the create route doesn't have this bug:** The create route (`app/routes/transfers.py:168-180`)
explicitly flushes the template FIRST with its own IntegrityError handler:

```python
template = TransferTemplate(...)
db.session.add(template)
try:
    db.session.flush()          # catches name conflict HERE
except IntegrityError:
    db.session.rollback()
    flash("A transfer with that name already exists.", "warning")
    return redirect(...)
# ... THEN calls generate_for_template (no name-dirtied template to flush)
```

**Why production-only:** The user likely has an existing template named "Emergency Fund" in
production but not in development. The unique constraint `uq_transfer_templates_user_name`
(`transfer_template.py:27`) fires only when a duplicate `(user_id, name)` exists.

### Secondary Risk

Even if the template name change is validated first, the regeneration path has additional unhandled
exception paths:

- `transfer_service.create_transfer()` flushes at `transfer_service.py:367`. If the partial unique
  index `idx_transfers_template_period_scenario` fires (e.g. due to a data inconsistency), that
  `IntegrityError` is also uncaught.
- `create_transfer` can raise `NotFoundError` or `ShekelValidationError` (documented at line
  314-317 of `transfer_service.py`). The create route catches these (line 219) but the update route
  does not.

### Verification Steps

```sql
-- Check if the user has a duplicate template name in production:
SELECT id, name FROM budget.transfer_templates
WHERE user_id = <uid> AND name = 'Emergency Fund';
```

### Proposed Fix

Flush the template change explicitly before calling regeneration, with IntegrityError handling.
Also wrap `regenerate_for_template` in a broader except that catches `IntegrityError`:

```python
# After setattr loop (line 340), before regeneration (line 342):
for field, value in data.items():
    if field in _TEMPLATE_UPDATE_FIELDS:
        setattr(template, field, value)

# NEW: Flush template changes to catch name uniqueness early.
try:
    db.session.flush()
except IntegrityError:
    db.session.rollback()
    flash("A recurring transfer with that name already exists.", "warning")
    return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))

# Regenerate future transfers.
scenario = ...
if scenario and template.recurrence_rule:
    periods = pay_period_service.get_all_periods(current_user.id)
    try:
        transfer_recurrence.regenerate_for_template(
            template, periods, scenario.id, effective_from=effective_from,
        )
    except RecurrenceConflict as conflict:
        ...
    # REMOVE the separate try/except IntegrityError around db.session.commit()
    # since the template name is already validated above.

try:
    db.session.commit()
except IntegrityError:
    db.session.rollback()
    flash("A recurring transfer with that name already exists.", "warning")
    return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))
```

The key change: flush the template separately (like the create route does) so the name uniqueness
check happens before regeneration starts. Keep the commit-level catch as a safety net for transfer
constraint violations.

---

## Issue B: Blank 500 Error Page

### Current State

**Error handlers exist and are correctly registered.** `app/__init__.py:323-370` defines handlers
for 400, 403, 404, 429, and 500. All render templates from `app/templates/errors/` that extend
`base.html` with proper content blocks. The 500 template (`errors/500.html`) shows a "Something
Went Wrong" page with a link back to the dashboard.

**Error templates exist:**
- `app/templates/errors/400.html`
- `app/templates/errors/403.html`
- `app/templates/errors/404.html`
- `app/templates/errors/429.html`
- `app/templates/errors/500.html`

All are properly structured, extending `base.html`.

### Root Cause

The 500 handler itself crashes when rendering, causing Flask to fall back to its built-in plain
text response.

**The sequence:**

1. Unhandled `IntegrityError` in the update route puts SQLAlchemy's session into a **failed
   transaction state** ("needs rollback").

2. Flask invokes the 500 error handler (`app/__init__.py:363-370`):
   ```python
   @app.errorhandler(500)
   def internal_server_error(e):
       return render_template("errors/500.html"), 500
   ```

3. `render_template("errors/500.html")` triggers Jinja rendering, which runs context processors.

4. The `inject_onboarding` context processor (`app/__init__.py:217-258`) executes **5 database
   queries** (Account, Category, PayPeriod, SalaryProfile, TransactionTemplate) on the poisoned
   session.

5. Those queries raise `sqlalchemy.exc.PendingRollbackError` (or `InvalidRequestError`) because
   the session has an uncommitted failed transaction.

6. The error handler itself raises an exception. Flask cannot render the custom error page and
   falls back to its built-in plain text "Internal Server Error" response -- the blank white page.

### Proposed Fix

Add `db.session.rollback()` in the 500 error handler before rendering:

```python
@app.errorhandler(500)
def internal_server_error(e):
    """Handle 500 Internal Server Error."""
    db.session.rollback()
    return render_template("errors/500.html"), 500
```

This clears the failed transaction state so the context processor queries succeed and the custom
error page renders correctly.

The rollback is always safe in a 500 handler -- the request is already failed, and any pending
changes should not be committed.

---

## Files That Would Need to Change

| File | Issue | Change |
|------|-------|--------|
| `app/routes/transfers.py` | A | Add explicit flush + IntegrityError catch after setattr, before regeneration (around line 340) |
| `app/__init__.py` | B | Add `db.session.rollback()` to the 500 error handler (line 364) |

Both fixes are small (under 10 lines each) and surgically targeted.
