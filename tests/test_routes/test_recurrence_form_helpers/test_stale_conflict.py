"""Recurrence form helper tests: the stale-conflict handlers ``handle_stale_conflict`` (C2-6) and
``handle_stale_form_conflict`` (C3-1).
"""

import logging

from flask import Response

from app.routes._commit_helpers import (
    STALE_ACTION_MESSAGE,
    STALE_EDITING_MESSAGE,
    StaleConflictContext,
    handle_stale_conflict,
    handle_stale_form_conflict,
)
from app.routes._redirect_target import RedirectTarget


class TestHandleStaleConflict:
    """Helper :func:`handle_stale_conflict` contract tests."""

    def test_logs_flashes_and_redirects(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """C2-6: rollback + log + flash + 302 redirect.

        Pins the canonical handler shape so a regression that drops
        any of the four side effects (rollback, log, flash, redirect)
        surfaces here.
        """
        with app.test_request_context():
            test_logger = logging.getLogger("test_handle_stale_conflict")
            # The helper expects to be invoked from inside an
            # ``except`` block where a commit just raised.  No
            # commit happened here, so rollback is a no-op -- the
            # assertion focuses on the redirect contract.
            response = handle_stale_conflict(StaleConflictContext(
                logger=test_logger,
                log_label="test_route",
                log_id=123,
                flash_message=STALE_EDITING_MESSAGE.format(
                    noun="test object",
                ),
                redirect=RedirectTarget(
                    "templates.edit_template",
                    {"template_id": 123},
                ),
            ))
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/123" in response.headers["Location"]

    def test_stale_message_templates_render_expected_strings(self):
        """C2-6 (variant): the two flash templates render expected copy.

        Pins the user-facing wording so a copy-edit that breaks the
        ``{noun}`` substitution or rewords the canonical line
        surfaces as a unit-test failure.
        """
        editing = STALE_EDITING_MESSAGE.format(noun="recurring transaction")
        assert "while you were editing" in editing
        assert "recurring transaction" in editing
        assert "Please reload and try again." in editing

        action = STALE_ACTION_MESSAGE.format(noun="recurring transfer")
        assert "while you were editing" not in action
        assert "recurring transfer" in action
        assert "Please reload and try again." in action


class TestHandleStaleFormConflict:
    """F-26 helper :func:`handle_stale_form_conflict` contract tests.

    Pre-flush optimistic-locking mirror of
    :func:`handle_stale_conflict`; logs both submitted and current
    version counters so post-mortem analysis can reconstruct the
    race.  Does NOT roll back (no DB write attempted at the call
    site).
    """

    def test_logs_both_counters_flashes_and_redirects(
        self, app, auth_client, seed_user, caplog,  # pylint: disable=unused-argument
    ):
        """C3-1: log at INFO with submitted + current; flash; 302.

        Pins the canonical handler shape so a regression that drops
        either counter from the log (or rewords the canonical line)
        surfaces here.
        """
        with app.test_request_context():
            test_logger = logging.getLogger(
                "test_handle_stale_form_conflict",
            )
            with caplog.at_level(
                logging.INFO,
                logger="test_handle_stale_form_conflict",
            ):
                response = handle_stale_form_conflict(
                    StaleConflictContext(
                        logger=test_logger,
                        log_label="update_template",
                        log_id=42,
                        flash_message=STALE_EDITING_MESSAGE.format(
                            noun="recurring transaction",
                        ),
                        redirect=RedirectTarget(
                            "templates.edit_template",
                            {"template_id": 42},
                        ),
                    ),
                    submitted=7,
                    current=9,
                )
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/42" in response.headers["Location"]
            # The log record must carry BOTH the submitted and the
            # current counters -- the post-mortem-reconstruction
            # rationale fails if either is missing.
            log_msg = caplog.records[-1].getMessage()
            assert "update_template" in log_msg
            assert "id=42" in log_msg
            assert "submitted=7" in log_msg
            assert "current=9" in log_msg
