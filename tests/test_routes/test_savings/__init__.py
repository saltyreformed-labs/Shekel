"""
Shekel Budget App -- Savings Route Tests

Tests for the savings dashboard and goal CRUD endpoints:
  - Dashboard rendering (with/without accounts, goals)
  - Goal creation (happy path, validation, IDOR)
  - Goal editing (happy path, IDOR)
  - Goal deletion (soft-deactivate, IDOR)
  - Double-submit (unique constraint on user+account+name)
"""
