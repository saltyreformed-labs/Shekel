"""
Shekel Budget App -- Analytics Route Tests

Tests for the analytics page shell and HTMX tab endpoints:
  - Authentication required for all endpoints
  - Main page renders with nav-pills and tab-content div
  - Tab endpoints return placeholders/content with HX-Request header
  - A direct (non-HTMX) tab GET renders the shell with that tab active (D13)
  - Nav bar shows Analytics link with correct active state
  - Statements pill groups the income statement + balance sheet toggle
  - Retired Variance / Trends / Year-End URLs redirect to the page
"""
