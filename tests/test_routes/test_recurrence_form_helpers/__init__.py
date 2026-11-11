"""
Unit tests for the recurrence form's route helpers (F-24, F-26), first
written against ``app.routes._recurrence_form_helpers``.

Pins helper-internal contracts so a future edit to one surfaces as a
unit-test failure rather than as integration drift in the templates /
transfers CRUD route suites.  Several contracts have moved since F-24, and
each part of this package grades its own where it lives now:

* the update dispatcher
  (:func:`~app.routes._recurrence_form_helpers.resolve_recurrence_rule_for_update`),
  the refusals it asks and the write door it hands a spec to:
  ``test_window``, ``test_phase`` and ``test_cadence_edit``;
* the create builder, which is
  :meth:`~app.services.recurrence.RecurrenceSubmission.authored` since plan
  step R7e: ``test_create_builder``;
* the stale-conflict handlers, in :mod:`app.routes._commit_helpers` since
  plan step R7c-b: ``test_stale_conflict``.

Test IDs C2-1 through C2-6 map to the F-24 commit's E section; C3-1 and
C3-2 map to the F-26 commit's E section.  Both commit specs live in
``remediation_follow_up_F24_F25_F26_plan.md`` Section 7.

The tests use real templates / transfers blueprint endpoints
(``templates.new_template``, ``templates.edit_template``,
``transfers.list_transfer_templates``) for the helper's redirect
target rather than fabricating a test-only endpoint, because the
session-scoped ``app`` fixture is frozen by the time these tests
run (Flask refuses ``app.add_url_rule`` once a request has been
handled).
"""
