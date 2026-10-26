"""
Shekel Budget App - Auth Helpers Tests

Tests for the reusable ownership verification helpers in
app/utils/auth_helpers.py.  Uses test_request_context + login_user
to set up a real Flask-Login request context for each test.

The access-denied logging tests near the bottom of this file cover
audit finding F-144 / commit C-14: the previously-silent ownership
failure paths now emit structured ``log_event`` records so cross-user
probing leaves a forensic trail.
"""
