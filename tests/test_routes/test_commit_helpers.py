"""
Direct unit tests for ``app.routes._commit_helpers``.

Covers the three helpers the Phase-4 ``_commit_helpers`` extraction
added without direct tests -- :func:`handle_db_error`,
:func:`handle_unique_violation`, and
:func:`regenerate_commit_or_report` (the plan.md Phase-4 follow-up).
Until now they were covered only transitively through the ~124 salary
route tests; these tests pin each helper's own contract -- the
rollback / log / flash / redirect side effects and the
most-specific-first failure routing -- so a regression surfaces here
as a unit failure rather than as drift in a route suite.

The stale-conflict half of the module
(:func:`handle_stale_conflict` and the two stale wrappers) already has
direct coverage in
``test_recurrence_form_helpers/test_stale_conflict.py`` (C2-6) and is
exercised again here only through ``regenerate_commit_or_report``'s
stale arm.

Tests use real blueprint endpoints for the redirect target (the
session-scoped ``app`` fixture is frozen once a request has been
handled; see ``test_recurrence_form_helpers/__init__.py``), and forge
``IntegrityError`` objects with the ``orig.diag.constraint_name``
shape psycopg produces (the ``test_c19_credit_payback_unique.py``
idiom) so :func:`app.utils.db_errors.is_unique_violation` discriminates
exactly as it does against a live PostgreSQL error packet.
"""
import logging

from flask import Response, get_flashed_messages
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.routes._commit_helpers import (
    DbErrorContext,
    StaleConflictContext,
    UniqueViolationContext,
    handle_db_error,
    handle_unique_violation,
    regenerate_commit_or_report,
)
from app.routes._redirect_target import RedirectTarget

EXPECTED_CONSTRAINT = "uq_test_expected_constraint"


def _spy_rollback(monkeypatch):
    """Wrap ``db.session.rollback`` with a call counter.

    The helpers' rollback side effect is invisible under a bare
    ``test_request_context`` (nothing is staged, so a rollback is a
    no-op), so the tests count calls through the real method instead:
    the wrapper records, then delegates, keeping the session's actual
    behavior intact.

    **Patched on the proxy's CLASS, never on the ``db.session`` instance**
    (finding **BAL-569**).  That instance is built once by ``SQLAlchemy()``
    and lives for the whole worker process, and ``monkeypatch.setattr`` on
    an INSTANCE whose attribute lives on its class restores it by writing
    the bound method it read back ONTO the instance (``_pytest/monkeypatch.py``:
    ``setattr`` saves ``getattr(target, name)``, ``undo`` puts it back with
    ``setattr``; only a CLASS target saves its own ``__dict__`` entry).  The
    instance spelling this replaced left
    ``rollback`` in ``vars(db.session)`` for the rest of the process, where it
    shadowed every later class-level patch of it:
    ``TestTheCashDoorReportsWhatGovernsEitherSide`` counted 0 rollbacks
    whenever one of these three tests had run before it on the same worker.
    A class attribute is restored exactly (pytest records ``__dict__[name]``).

    Returns:
        The list the wrapper appends to -- one element per rollback.
    """
    calls = []
    real_rollback = type(db.session).rollback

    def _wrapper(session):
        calls.append(True)
        real_rollback(session)

    monkeypatch.setattr(type(db.session), "rollback", _wrapper)
    return calls


def _forged_integrity_error(constraint_name: str) -> IntegrityError:
    """Build an IntegrityError reporting ``constraint_name`` via diag.

    Mirrors the psycopg shape ``is_unique_violation`` inspects:
    ``exc.orig.diag.constraint_name`` (the structured PostgreSQL
    error-packet field), so the helpers under test discriminate the
    forged error exactly as they would a live unique violation.
    """
    class _FakeDiag:  # pylint: disable=too-few-public-methods
        constraint_name = None

    class _FakeOrig(Exception):
        diag = _FakeDiag()

    _FakeDiag.constraint_name = constraint_name
    return IntegrityError("stmt", {}, _FakeOrig("forged-violation"))


def _stale_ctx() -> StaleConflictContext:
    """StaleConflictContext aimed at a real templates endpoint."""
    return StaleConflictContext(
        logger=logging.getLogger("test_commit_helpers.stale"),
        log_label="test_mutation",
        log_id=11,
        flash_message="Stale conflict on the test object.",
        redirect=RedirectTarget("templates.edit_template", {"template_id": 11}),
    )


def _error_ctx() -> DbErrorContext:
    """DbErrorContext aimed at a real templates endpoint."""
    return DbErrorContext(
        logger=logging.getLogger("test_commit_helpers.db_error"),
        log_message="user_id=%d failed test mutation on row %d",
        log_args=(7, 42),
        flash_message="Something went wrong saving the test object.",
        redirect=RedirectTarget("templates.new_template"),
    )


def _unique_ctx() -> UniqueViolationContext:
    """UniqueViolationContext naming the expected test constraint."""
    return UniqueViolationContext(
        logger=logging.getLogger("test_commit_helpers.unique"),
        constraint=EXPECTED_CONSTRAINT,
        log_message="user_id=%d name collision on row %d",
        log_args=(7, 42),
        flash_message="That name already exists.",
        redirect=RedirectTarget("templates.edit_template", {"template_id": 42}),
    )


class TestHandleDbError:
    """:func:`handle_db_error` contract tests."""

    def test_logs_traceback_flashes_danger_and_redirects(
        self, app, caplog, monkeypatch,
    ):
        """Rollback + ERROR log with args + traceback + danger flash + 302.

        Called from inside an ``except`` block (as every production
        caller does) so ``logger.exception`` has an active exception to
        attach -- the captured record must carry the formatted
        ``log_message % log_args`` AND the traceback (``exc_info``);
        the flash must land in the ``danger`` category; the session
        must be rolled back exactly once (counted via the spy -- a
        bare rollback is a no-op here, so the call is the observable).
        """
        with app.test_request_context():
            rollbacks = _spy_rollback(monkeypatch)
            ctx = _error_ctx()
            with caplog.at_level(
                logging.ERROR, logger="test_commit_helpers.db_error",
            ):
                try:
                    raise SQLAlchemyError("forged DB failure")
                except SQLAlchemyError:
                    response = handle_db_error(ctx)
            assert rollbacks == [True]
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/new" in response.headers["Location"]
            record = caplog.records[-1]
            # log_message rendered with log_args, traceback attached.
            assert record.getMessage() == (
                "user_id=7 failed test mutation on row 42"
            )
            assert record.exc_info is not None
            assert get_flashed_messages(with_categories=True) == [
                ("danger", "Something went wrong saving the test object."),
            ]


class TestHandleUniqueViolation:
    """:func:`handle_unique_violation` contract tests."""

    def test_expected_constraint_reports_collision(
        self, app, caplog, monkeypatch,
    ):
        """Matched constraint -> rollback + INFO log + warning flash + 302."""
        with app.test_request_context():
            rollbacks = _spy_rollback(monkeypatch)
            exc = _forged_integrity_error(EXPECTED_CONSTRAINT)
            with caplog.at_level(
                logging.INFO, logger="test_commit_helpers.unique",
            ):
                response = handle_unique_violation(exc, _unique_ctx())
            assert rollbacks == [True]
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/42" in response.headers["Location"]
            record = caplog.records[-1]
            assert record.levelno == logging.INFO
            assert record.getMessage() == (
                "user_id=7 name collision on row 42"
            )
            assert get_flashed_messages(with_categories=True) == [
                ("warning", "That name already exists."),
            ]

    def test_other_constraint_defers_with_none(
        self, app, caplog, monkeypatch,
    ):
        """Unmatched constraint -> ``None``, session untouched, no flash.

        The ``None`` return is load-bearing: the caller (a route's
        ``except IntegrityError`` arm or
        :func:`regenerate_commit_or_report`) falls through to the
        generic :func:`handle_db_error` fallback, which owns the
        rollback and the traceback log for the unexpected error.  The
        deferral must therefore NOT roll back itself -- the documented
        "session is left untouched for that fallback" contract.
        """
        with app.test_request_context():
            rollbacks = _spy_rollback(monkeypatch)
            exc = _forged_integrity_error("uq_some_other_constraint")
            with caplog.at_level(
                logging.INFO, logger="test_commit_helpers.unique",
            ):
                result = handle_unique_violation(exc, _unique_ctx())
            assert result is None
            assert rollbacks == []
            assert not caplog.records
            assert get_flashed_messages(with_categories=True) == []


class TestRegenerateCommitOrReport:
    """:func:`regenerate_commit_or_report` failure-routing tests.

    The orchestrator's contract is the most-specific-first routing of
    the three failure modes (stale race, expected unique collision,
    generic DB error) around the regenerate+commit body.  Each test
    injects the failure through ``regenerate`` -- it runs inside the
    same guard as the commit, which is exactly the reason the
    orchestrator exists (the salary regeneration step itself flushes).
    """

    def test_clean_path_runs_regenerate_once_and_returns_none(
        self, app,
    ):
        """Success -> regenerate called exactly once, ``None`` returned."""
        with app.test_request_context():
            calls = []
            result = regenerate_commit_or_report(
                lambda: calls.append("ran"),
                stale_ctx=_stale_ctx(),
                error_ctx=_error_ctx(),
                on_integrity=_unique_ctx(),
            )
            assert result is None
            assert calls == ["ran"]
            assert get_flashed_messages(with_categories=True) == []

    def test_stale_race_from_regenerate_reports_conflict(
        self, app, caplog,
    ):
        """StaleDataError -> the stale arm: warning flash + stale redirect.

        Pins that the regeneration step runs INSIDE the stale guard --
        the pre-extraction handlers caught a stale race raised by the
        regeneration flush, not only by the commit.
        """
        def _raise_stale():
            raise StaleDataError("forged stale race")

        with app.test_request_context():
            with caplog.at_level(
                logging.INFO, logger="test_commit_helpers.stale",
            ):
                response = regenerate_commit_or_report(
                    _raise_stale,
                    stale_ctx=_stale_ctx(),
                    error_ctx=_error_ctx(),
                )
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/11" in response.headers["Location"]
            assert caplog.records[-1].getMessage() == (
                "Stale-data conflict on test_mutation id=11"
            )
            assert get_flashed_messages(with_categories=True) == [
                ("warning", "Stale conflict on the test object."),
            ]

    def test_expected_integrity_error_takes_collision_arm(
        self, app, caplog,
    ):
        """IntegrityError on ``on_integrity.constraint`` -> collision arm."""
        def _raise_expected():
            raise _forged_integrity_error(EXPECTED_CONSTRAINT)

        with app.test_request_context():
            with caplog.at_level(
                logging.INFO, logger="test_commit_helpers.unique",
            ):
                response = regenerate_commit_or_report(
                    _raise_expected,
                    stale_ctx=_stale_ctx(),
                    error_ctx=_error_ctx(),
                    on_integrity=_unique_ctx(),
                )
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/42" in response.headers["Location"]
            assert caplog.records[-1].getMessage() == (
                "user_id=7 name collision on row 42"
            )
            assert get_flashed_messages(with_categories=True) == [
                ("warning", "That name already exists."),
            ]

    def test_unexpected_integrity_error_falls_through_to_db_error(
        self, app, caplog,
    ):
        """IntegrityError on a DIFFERENT constraint -> generic fallback.

        ``on_integrity`` is supplied but does not match, so the
        orchestrator must fall through to :func:`handle_db_error`
        (danger flash + error redirect + ERROR traceback log), exactly
        as the hand-written routes did for an unrecognised
        IntegrityError.
        """
        def _raise_other():
            raise _forged_integrity_error("uq_some_other_constraint")

        with app.test_request_context():
            with caplog.at_level(
                logging.ERROR, logger="test_commit_helpers.db_error",
            ):
                response = regenerate_commit_or_report(
                    _raise_other,
                    stale_ctx=_stale_ctx(),
                    error_ctx=_error_ctx(),
                    on_integrity=_unique_ctx(),
                )
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/new" in response.headers["Location"]
            record = caplog.records[-1]
            assert record.getMessage() == (
                "user_id=7 failed test mutation on row 42"
            )
            assert record.exc_info is not None
            assert get_flashed_messages(with_categories=True) == [
                ("danger", "Something went wrong saving the test object."),
            ]

    def test_integrity_error_without_on_integrity_takes_db_error(
        self, app, caplog,
    ):
        """IntegrityError with ``on_integrity=None`` -> generic fallback.

        The delete / profile-edit routes pass no collision context;
        their IntegrityErrors must take the generic fallback exactly as
        a bare ``except SQLAlchemyError`` would.
        """
        def _raise_expected():
            raise _forged_integrity_error(EXPECTED_CONSTRAINT)

        with app.test_request_context():
            with caplog.at_level(
                logging.ERROR, logger="test_commit_helpers.db_error",
            ):
                response = regenerate_commit_or_report(
                    _raise_expected,
                    stale_ctx=_stale_ctx(),
                    error_ctx=_error_ctx(),
                )
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/new" in response.headers["Location"]
            assert get_flashed_messages(with_categories=True) == [
                ("danger", "Something went wrong saving the test object."),
            ]

    def test_generic_sqlalchemy_error_takes_db_error(
        self, app, caplog,
    ):
        """Plain SQLAlchemyError -> generic fallback (the outermost arm)."""
        def _raise_generic():
            raise SQLAlchemyError("forged operational failure")

        with app.test_request_context():
            with caplog.at_level(
                logging.ERROR, logger="test_commit_helpers.db_error",
            ):
                response = regenerate_commit_or_report(
                    _raise_generic,
                    stale_ctx=_stale_ctx(),
                    error_ctx=_error_ctx(),
                    on_integrity=_unique_ctx(),
                )
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/new" in response.headers["Location"]
            record = caplog.records[-1]
            assert record.getMessage() == (
                "user_id=7 failed test mutation on row 42"
            )
            assert record.exc_info is not None
            assert get_flashed_messages(with_categories=True) == [
                ("danger", "Something went wrong saving the test object."),
            ]
