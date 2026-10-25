---
paths:
  - "app/templates/_recurrence_fields.html"
  - "app/static/js/recurrence_form.js"
  - "app/schemas/validation/_recurrence.py"
  - "app/routes/_recurrence_form_helpers.py"
  - "app/routes/_recurrence_preview.py"
  - "app/services/recurrence/_picker.py"
  - "app/static/js/app.js"
  - "app/templates/templates/form.html"
  - "app/templates/transfers/form.html"
  - "app/routes/transfers/templates.py"
  - "app/templates/salary/form.html"
  - "app/templates/salary/_lines_section.html"
  - "tests/manual/verify_recurrence_form.py"
---

# Recurrence form rules

Must-knows for any change to the recurrence form's controls: the control macros, the script that
links them, the schema and helpers that read what they post, the live preview, and the forms that
include them.

- **A change to the recurrence form's controls runs `tests/manual/verify_recurrence_form.py`.**
  pytest cannot see what that script grades, by construction: a control hidden by a class, an
  option hidden by a script, and a style the browser REFUSED to apply all look identical in
  rendered HTML. Two of `recurrence:R7b-2`'s six defects were that kind, found by the harness
  after the suite was green and two reviews had passed the code (its module docstring lists them).
- **When the dev app is pointed at a database COPY, point the harness there too**
  (`VERIFY_DEV_DATABASE`). The `ref` ids a `TEMPLATE` copy carries are identical either way, which
  is exactly what made the mismatch invisible: `recurrence:R7c-c`'s run found two of the harness's
  OWN checks reading a database the app under test never wrote to, so they would have passed with
  every refusal accepted.
