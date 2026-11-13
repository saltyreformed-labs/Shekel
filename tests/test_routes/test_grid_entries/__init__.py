"""
Shekel Budget App -- Grid Entry Progress Indicator Tests (Commit 7)

Tests the entry progress display ("X / Y" format) and enhanced tooltip
for tracked transactions in the grid cell and mobile grid views.

Covers:
  - build_entry_sums_dict computation correctness (unit tests).
  - Cell endpoint rendering with progress format (integration tests).
  - Tooltip enhancement with entry breakdown.
  - Non-tracked transaction regression (display unchanged).
  - Grid page flow: entry_sums passes through to template context.
"""
