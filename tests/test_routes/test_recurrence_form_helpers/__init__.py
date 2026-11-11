"""
Unit tests for ``app.routes._recurrence_form_helpers`` (F-24, F-26).

Pins helper-internal contracts so future edits to any of the four
helpers surface as unit-test failures rather than as integration
drift in the templates / transfers CRUD route suites.  Test IDs
C2-1 through C2-6 map to the F-24 commit's E section; C3-1 and
C3-2 map to the F-26 commit's E section.  Both commit specs live
in ``remediation_follow_up_F24_F25_F26_plan.md`` Section 7.

The tests use real templates / transfers blueprint endpoints
(``templates.new_template``, ``templates.edit_template``,
``transfers.list_transfer_templates``) for the helper's redirect
target rather than fabricating a test-only endpoint, because the
session-scoped ``app`` fixture is frozen by the time these tests
run (Flask refuses ``app.add_url_rule`` once a request has been
handled).
"""
