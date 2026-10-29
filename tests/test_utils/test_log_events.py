"""Tests for ``app.utils.log_events``.

Covers the helper, the category constants, and the post-C-14 event
registry.  The registry tests are the load-bearing safety net: they
prove that every ``EVT_*`` constant the code emits has a single
canonical home, that no two events share a name, that every recorded
category is one of the documented constants, and that the runtime
helper accepts the registered names without surprise.

These tests were extended for audit findings F-080 / F-085 / F-144
(commit C-14 of the 2026-04-15 security remediation plan).
"""
import logging
import re

import pytest

from app.utils import log_events
from app.utils.log_events import (
    ACCESS,
    AUDIT,
    AUTH,
    BUSINESS,
    ERROR,
    EVENT_REGISTRY,
    EVT_ACCESS_DENIED_CROSS_USER,
    EVT_ACCESS_DENIED_OWNER_ONLY,
    EVT_ACCOUNT_LOCKED,
    EVT_BACKUP_CODES_REGENERATED,
    EVT_CARRY_FORWARD,
    EVT_CREDIT_MARKED,
    EVT_CREDIT_UNMARKED,
    EVT_CROSS_USER_BLOCKED,
    EVT_ENTRIES_SETTLED_DAY_RECORDED,
    EVT_ENTRY_CREATED,
    EVT_ENTRY_DELETED,
    EVT_ENTRY_PAYBACK_CREATED,
    EVT_ENTRY_PAYBACK_DELETED,
    EVT_ENTRY_PAYBACK_UPDATED,
    EVT_ENTRY_UPDATED,
    EVT_HIBP_CHECK_FAILED,
    EVT_HIBP_CHECK_REJECTED,
    EVT_LOGIN_FAILED,
    EVT_LOGIN_SUCCESS,
    EVT_LOGOUT,
    EVT_MFA_DISABLED,
    EVT_MFA_ENABLED,
    EVT_MFA_LOGIN_SUCCESS,
    EVT_OTHER_SESSIONS_INVALIDATED,
    EVT_PASSWORD_CHANGED,
    EVT_PAY_PERIODS_GENERATED,
    EVT_RATE_LIMIT_EXCEEDED,
    EVT_REAUTH_FAILED,
    EVT_REAUTH_SUCCESS,
    EVT_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_RECURRENCE_GENERATED,
    EVT_RECURRENCE_REGENERATED,
    EVT_REQUEST_COMPLETE,
    EVT_RESOURCE_NOT_FOUND,
    EVT_SESSIONS_INVALIDATED,
    EVT_SLOW_REQUEST,
    EVT_TOTP_REPLAY_REJECTED,
    EVT_TRANSACTION_SETTLED_FROM_ENTRIES,
    EVT_TRANSFER_CREATED,
    EVT_TRANSFER_HARD_DELETED,
    EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_TRANSFER_RECURRENCE_GENERATED,
    EVT_TRANSFER_RECURRENCE_REGENERATED,
    EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT,
    EVT_TRANSFER_RESTORED,
    EVT_TRANSFER_SOFT_DELETED,
    EVT_TRANSFER_UPDATED,
    EVT_USER_REGISTERED,
    PERFORMANCE,
    log_event,
)


# Snake_case validator: lowercase letters, digits, and underscores only;
# must start with a letter; no double-underscore or trailing underscore.
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")

# Every category constant the registry is allowed to declare.  Mirrored
# from log_events._register so the test fails loudly if a new category
# is added there without a corresponding test update.
_KNOWN_CATEGORIES = frozenset({
    AUTH, BUSINESS, ACCESS, AUDIT, ERROR, PERFORMANCE,
})


class TestLogEvent:
    """Tests for the ``log_event`` helper."""

    def test_log_event_emits_at_correct_level(self):
        """``log_event`` calls ``logger.log`` with the specified level."""
        test_logger = logging.getLogger("test.log_events")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.WARNING, "test_evt", AUTH, "msg")
        assert cap.records[0].levelno == logging.WARNING

    def test_log_event_includes_event_field(self):
        """``log_event`` puts ``event`` in the structured extra dict."""
        test_logger = logging.getLogger("test.log_events.event")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.INFO, "my_event", AUTH, "msg")
        assert cap.records[0].event == "my_event"

    def test_log_event_includes_category_field(self):
        """``log_event`` puts ``category`` in the structured extra dict."""
        test_logger = logging.getLogger("test.log_events.category")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.INFO, "evt", BUSINESS, "msg")
        assert cap.records[0].category == "business"

    def test_log_event_includes_extra_kwargs(self):
        """``log_event`` propagates additional kwargs as record fields."""
        test_logger = logging.getLogger("test.log_events.extra")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.INFO, "evt", AUTH, "msg",
                      user_id=42, ip="1.2.3.4")
        assert cap.records[0].user_id == 42
        assert cap.records[0].ip == "1.2.3.4"

    def test_log_event_message_is_human_readable(self):
        """``log_event`` passes the message string to the logger."""
        test_logger = logging.getLogger("test.log_events.msg")
        with _LogCapture(test_logger) as cap:
            log_event(test_logger, logging.INFO, "evt", AUTH,
                      "User logged in")
        assert cap.records[0].getMessage() == "User logged in"


class TestEventCategories:
    """Tests for event category constants."""

    def test_auth_category_is_string(self):
        """AUTH constant is a string with the expected value."""
        assert isinstance(AUTH, str)
        assert AUTH == "auth"

    def test_business_category_is_string(self):
        """BUSINESS constant is a string with the expected value."""
        assert isinstance(BUSINESS, str)
        assert BUSINESS == "business"

    def test_access_category_is_string(self):
        """ACCESS constant exists for IDOR / ownership events (F-144 / C-14)."""
        assert isinstance(ACCESS, str)
        assert ACCESS == "access"

    def test_audit_category_is_string(self):
        """AUDIT constant exists for explicit audit-trail markers."""
        assert isinstance(AUDIT, str)
        assert AUDIT == "audit"

    def test_error_category_is_string(self):
        """ERROR constant is a string with the expected value."""
        assert isinstance(ERROR, str)
        assert ERROR == "error"

    def test_performance_category_is_string(self):
        """PERFORMANCE constant is a string with the expected value."""
        assert isinstance(PERFORMANCE, str)
        assert PERFORMANCE == "performance"


class TestEventRegistry:
    """Tests for the post-C-14 :data:`EVENT_REGISTRY`.

    The registry is the catalogue every Python-tier event the app
    emits via ``log_event``.  These tests assert structural invariants
    (no duplicates, valid categories, snake_case names) and that the
    expected breadth of events from C-14 are present.
    """

    def test_registry_is_non_empty(self):
        """The registry must declare at least the events C-14 added."""
        # Concrete lower bound rather than ``>0`` so a future
        # accidental clear of the registry fails loudly.  The
        # constant 30 is well below the actual count -- it is a
        # smoke test, not a count assertion.
        assert len(EVENT_REGISTRY) > 30, (
            f"Registry shrank unexpectedly to {len(EVENT_REGISTRY)} "
            "events; either an event was deleted without updating the "
            "lower bound here, or the module failed to import."
        )

    def test_every_event_has_known_category(self):
        """Every registered event's category is one of the documented constants.

        Catches typos like ``"buisness"`` before they reach a deployed
        environment where they would silently bypass dashboard filters.
        """
        unknown = {
            name: meta["category"]
            for name, meta in EVENT_REGISTRY.items()
            if meta["category"] not in _KNOWN_CATEGORIES
        }
        assert not unknown, (
            f"Event registry contains unknown categories: {unknown!r}.  "
            f"Known categories: {sorted(_KNOWN_CATEGORIES)}."
        )

    def test_every_event_has_non_empty_description(self):
        """Every registered event has a substantive description.

        The registry is also operator-facing documentation; a blank
        description would defeat the introspection use case.
        """
        empty = [
            name for name, meta in EVENT_REGISTRY.items()
            if not meta["description"].strip()
        ]
        assert not empty, (
            f"Events with empty descriptions: {empty!r}.  Every event "
            "must document WHEN it fires."
        )

    def test_every_event_name_is_snake_case(self):
        """Event names follow the snake_case convention from D-4."""
        bad = [name for name in EVENT_REGISTRY if not _SNAKE_CASE.match(name)]
        assert not bad, (
            f"Events with non-snake_case names: {bad!r}.  See D-4 in the "
            "remediation plan: ``<verb>_<noun>`` or ``<noun>_<event>`` "
            "in snake_case, no dots."
        )

    def test_no_duplicate_event_names(self):
        """``_register`` refuses duplicates.

        The check happens at module import time; this test exercises
        the helper to confirm the gate is active (a future regression
        that swallows the ValueError would otherwise let two services
        emit the same event-name and conflate them in dashboards).
        """
        with pytest.raises(ValueError, match="already registered"):
            log_events._register(  # pylint: disable=protected-access
                EVT_LOGIN_SUCCESS, AUTH, "duplicate -- should raise",
            )

    def test_register_rejects_unknown_category(self):
        """``_register`` refuses unknown categories.

        Important so a typo in a category constant cannot quietly get
        a new event added under an unrecognised category.
        """
        with pytest.raises(ValueError, match="unknown category"):
            log_events._register(  # pylint: disable=protected-access
                "synthetic_for_test", "buisness",
                "typo'd category -- should raise",
            )


class TestExpectedEventsRegistered:
    """Smoke-check that every event used by C-14 callers is registered.

    Not exhaustive -- the registry-structural tests above catch the
    no-duplicates / valid-category / snake_case shape -- but explicit
    assertions for a curated set of important events ensure that a
    future refactor cannot silently drop one of them.
    """

    @pytest.mark.parametrize("evt_name,expected_category", [
        # Auth (existing pre-C-14)
        (EVT_LOGIN_SUCCESS, AUTH),
        (EVT_LOGIN_FAILED, AUTH),
        (EVT_LOGOUT, AUTH),
        (EVT_PASSWORD_CHANGED, AUTH),
        (EVT_SESSIONS_INVALIDATED, AUTH),
        (EVT_OTHER_SESSIONS_INVALIDATED, AUTH),
        (EVT_REAUTH_FAILED, AUTH),
        (EVT_REAUTH_SUCCESS, AUTH),
        (EVT_MFA_LOGIN_SUCCESS, AUTH),
        (EVT_MFA_ENABLED, AUTH),
        (EVT_MFA_DISABLED, AUTH),
        (EVT_BACKUP_CODES_REGENERATED, AUTH),
        (EVT_TOTP_REPLAY_REJECTED, AUTH),
        (EVT_HIBP_CHECK_FAILED, AUTH),
        (EVT_HIBP_CHECK_REJECTED, AUTH),
        (EVT_ACCOUNT_LOCKED, AUTH),
        # Auth (C-14 / F-085 new)
        (EVT_USER_REGISTERED, AUTH),
        # Access (C-14 / F-144 new)
        (EVT_ACCESS_DENIED_OWNER_ONLY, ACCESS),
        (EVT_ACCESS_DENIED_CROSS_USER, ACCESS),
        (EVT_RESOURCE_NOT_FOUND, ACCESS),
        # Access (C-15 / F-146 new -- rate-limit alerting)
        (EVT_RATE_LIMIT_EXCEEDED, ACCESS),
        # Business (existing pre-C-14)
        (EVT_RECURRENCE_GENERATED, BUSINESS),
        (EVT_CROSS_USER_BLOCKED, BUSINESS),
        (EVT_CARRY_FORWARD, BUSINESS),
        # Business (C-14 / F-080 new -- service-tier mutations)
        (EVT_TRANSFER_CREATED, BUSINESS),
        (EVT_TRANSFER_UPDATED, BUSINESS),
        (EVT_TRANSFER_SOFT_DELETED, BUSINESS),
        (EVT_TRANSFER_HARD_DELETED, BUSINESS),
        (EVT_TRANSFER_RESTORED, BUSINESS),
        (EVT_CREDIT_MARKED, BUSINESS),
        (EVT_CREDIT_UNMARKED, BUSINESS),
        (EVT_ENTRY_PAYBACK_CREATED, BUSINESS),
        (EVT_ENTRY_PAYBACK_UPDATED, BUSINESS),
        (EVT_ENTRY_PAYBACK_DELETED, BUSINESS),
        (EVT_ENTRY_CREATED, BUSINESS),
        (EVT_ENTRY_UPDATED, BUSINESS),
        (EVT_ENTRY_DELETED, BUSINESS),
        # Business (plan step S1-c -- the reconcile step's writer).  It
        # SUCCEEDS the two events the retired ``is_cleared`` flag needed: the
        # bulk clear at true-up and the manual toggle both deleted with the
        # flag, and one event now records an OBSERVATION the user supplied.
        (EVT_ENTRIES_SETTLED_DAY_RECORDED, BUSINESS),
        (EVT_PAY_PERIODS_GENERATED, BUSINESS),
        (EVT_RECURRENCE_REGENERATED, BUSINESS),
        (EVT_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS),
        (EVT_TRANSFER_RECURRENCE_GENERATED, BUSINESS),
        (EVT_TRANSFER_RECURRENCE_REGENERATED, BUSINESS),
        (EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS),
        (EVT_TRANSACTION_SETTLED_FROM_ENTRIES, BUSINESS),
        # Business (C-20 / F-007 + F-164 new -- shadow + archived-account guards)
        (EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT, BUSINESS),
        # Performance
        (EVT_REQUEST_COMPLETE, PERFORMANCE),
        (EVT_SLOW_REQUEST, PERFORMANCE),
    ])
    def test_event_constant_is_registered_under_expected_category(
        self, evt_name, expected_category,
    ):
        """The constant resolves to a registered event under the right category."""
        assert evt_name in EVENT_REGISTRY, (
            f"Event {evt_name!r} (resolved from constant) is not "
            "registered.  Add a ``_register`` call in log_events.py."
        )
        actual = EVENT_REGISTRY[evt_name]["category"]
        assert actual == expected_category, (
            f"Event {evt_name!r} is registered under {actual!r} but "
            f"the test expected {expected_category!r}."
        )


# ── Helper ────────────────────────────────────────────────────────────────


class _LogCapture:
    """Context manager that captures log records on a logger."""

    def __init__(self, logger):
        self._logger = logger
        self.records = []
        self._handler = logging.Handler()
        self._handler.emit = lambda record: self.records.append(record)

    def __enter__(self):
        self._logger.addHandler(self._handler)
        self._logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
