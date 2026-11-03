"""
Shekel Budget App -- Template Route Tests

Tests for transaction template CRUD and recurrence preview:
  - Template listing (happy path, auth)
  - Template creation (with/without recurrence, validation, IDOR)
  - Template update (happy path, validation, IDOR, recurrence conflict)
  - Template archive (archive + soft-delete projected txns)
  - Template unarchive (restore + regenerate)
  - Recurrence preview HTMX endpoint
"""
