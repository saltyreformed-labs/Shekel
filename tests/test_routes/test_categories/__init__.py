"""
Shekel Budget App -- Category Route Tests

Tests for category CRUD endpoints:
  - Listing categories grouped by group_name
  - Creating categories (regular + HTMX)
  - Duplicate detection
  - Deleting categories (unused, in-use by template/transaction, IDOR)
  - Archive helper history-detection functions (5A.5-1)
"""
