"""
Shekel Budget App -- Service-tier log_event emission tests.

Audit finding F-080 / commit C-14: every state-changing service
function MUST emit a structured ``log_event`` on its successful path
so the Python tier of the audit story is queryable by event-name.

These tests treat each emission as observable behaviour: they call
the service through its public API and assert that the expected
event lands in the log stream with the expected category, level, and
the structured fields a forensic query would key off.

Why every service in one file: each test sets up the bare minimum
state for ONE call and asserts ONE event.  Combining them keeps the
fixture surface area small (a single ``_LogCapture`` helper, no
ad-hoc per-service conftest), and the failure mode is "this specific
service stopped logging" -- a far more useful signal than a generic
"the registry is incomplete".

``_LogCapture`` is re-exported here so a test outside this package imports
the package rather than its private ``_helpers`` module (the remedy plan task
plan#3949 names for a W9910 into a ``tests/`` helper package).
"""

from tests.test_services.test_service_log_events._helpers import _LogCapture

__all__ = ["_LogCapture"]
